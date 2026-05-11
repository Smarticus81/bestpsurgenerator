"""SQLite-backed parsers for complaints and sales.

Reads from a fixed-schema SQLite database (PSUR_DB_PATH) and returns dicts
in the EXACT same shape as parsers.complaints.parse_complaints() and
parsers.sales.parse_sales(), so downstream pipeline code is unchanged.

Schema (psur_data.sqlite):
  complaints(id, td_id, complaint_number, csi_notification_date, date_entered,
             date_closed, value_stream, value_stream_type, product_sales_category,
             product_sales_subcategory, complaint_type, product_number, lot_number,
             description, being_returned, customer_number, customer_name,
             customer_country, region, nonconformity, investigation_findings,
             corrective_actions, symptom_code, fault_code, failure_code,
             mdr_issued, mdr_issued_date, mdr_number, ncmr_issued, ncmr_number,
             capa_number, complaint_confirmed, year)

  sales(id, source_file, customer_country, customer_region, customer_city,
        customer_address, month, month_num, calendar_year, item_number,
        item_description, quantity, pack_size, units_shipped, td_id,
        country_was_filled, region_was_filled)

  products(item_number PK, item_description, product_group, product_main_group,
           product_category, product_family, product_segment, pack_size, td_id)
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_SERIOUS_TRUE = {"YES", "TRUE", "1", "Y", "SERIOUS", "REPORTABLE"}


def _connect(db_path: str | Path) -> sqlite3.Connection:
    db_path = Path(db_path)
    if not db_path.exists():
        raise FileNotFoundError(f"PSUR SQLite DB not found: {db_path}")
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    return con


def _is_serious(mdr_issued: Optional[str]) -> bool:
    if not mdr_issued:
        return False
    return str(mdr_issued).strip().upper() in _SERIOUS_TRUE


def _norm_region(value: Optional[str]) -> str:
    if not value:
        return "Unknown"
    return str(value).strip().title() or "Unknown"


# ──────────────────────────────────────────────────────────────────
# Product classification — single-use vs reusable
# ──────────────────────────────────────────────────────────────────
#
# Priority of signals (highest first):
#   1. Explicit match from an external classification map passed in
#      (e.g., previous_psur.json.appendix_a1_models).
#   2. item_description / product_family / product_segment keyword match.
#   3. Fallback: "unknown".
#
# Keyword signals are cheap but effective for surgical retractors:
#   - "reusable" / "non-sterile" / "sterilizable" → reusable
#   - "sterile" / "single-use" / "disposable" / "kit" / "tray" → single_use

_REUSABLE_KW = (
    "reusable", "re-usable", "non-sterile", "nonsterile", "non sterile",
    "sterilizable", "autoclavable", "multi-use", "multi use", "multiuse",
)
_SINGLE_USE_KW = (
    "single-use", "single use", "single-patient", "disposable", "sterile ",
    "sterile,", "sterile-", "pre-sterile", "pre sterile", " kit", " tray",
    "eo sterile", "gamma sterile",
)


def _classify_single_use(
    item_number: str,
    item_description: str,
    product_category: str,
    product_family: str,
    product_segment: str,
    classification_map: Optional[Dict[str, str]] = None,
) -> str:
    """Return 'reusable', 'single_use', or 'unknown' for one product row.

    classification_map: optional {item_number: 'reusable'|'single_use'} for
    hard overrides sourced from previous PSUR or device_context.json.
    """
    if classification_map:
        key = str(item_number).strip()
        hit = classification_map.get(key) or classification_map.get(key.upper())
        if hit in ("reusable", "single_use"):
            return hit

    haystack = " ".join(
        str(x or "").lower()
        for x in (item_description, product_category, product_family, product_segment)
    )
    if not haystack.strip():
        return "unknown"

    # Reusable keywords win over "sterile" when both present.
    if any(kw in haystack for kw in _REUSABLE_KW):
        return "reusable"
    if any(kw in haystack for kw in _SINGLE_USE_KW):
        return "single_use"
    return "unknown"


def load_product_classification_from_db(
    db_path: str | Path,
    td_id: Optional[str] = None,
    product_numbers: Optional[List[str]] = None,
    classification_map: Optional[Dict[str, str]] = None,
) -> Dict[str, Dict[str, str]]:
    """Return {item_number: {class, description, category, family, segment}}.

    Deterministic product-level classification table reused across sales and
    complaints so both sides report compatible reusable vs single_use splits.
    """
    con = _connect(db_path)
    try:
        where: List[str] = ["1=1"]
        params: List[Any] = []
        if td_id:
            where.append("td_id = ?")
            params.append(td_id)
        if product_numbers:
            placeholders = ",".join("?" * len(product_numbers))
            where.append(f"item_number IN ({placeholders})")
            params.extend(product_numbers)

        sql = f"""
            SELECT item_number, item_description, product_category,
                   product_family, product_segment, product_main_group
            FROM products
            WHERE {' AND '.join(where)}
        """
        rows = [dict(r) for r in con.execute(sql, params).fetchall()]
    finally:
        con.close()

    out: Dict[str, Dict[str, str]] = {}
    for r in rows:
        item = str(r.get("item_number") or "").strip()
        if not item:
            continue
        cls = _classify_single_use(
            item,
            r.get("item_description") or "",
            r.get("product_category") or "",
            r.get("product_family") or "",
            r.get("product_segment") or "",
            classification_map=classification_map,
        )
        out[item] = {
            "class": cls,
            "description": r.get("item_description") or "",
            "category": r.get("product_category") or "",
            "family": r.get("product_family") or "",
            "segment": r.get("product_segment") or "",
            "main_group": r.get("product_main_group") or "",
        }
    return out


# ──────────────────────────────────────────────────────────────────
# Complaints
# ──────────────────────────────────────────────────────────────────

def parse_complaints_from_db(
    db_path: str | Path,
    start_date: str,
    end_date: str,
    td_id: Optional[str] = None,
    product_numbers: Optional[List[str]] = None,
    product_classification: Optional[Dict[str, Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """Return complaints dict matching parse_complaints() shape.

    product_classification: optional pre-loaded map from
    load_product_classification_from_db() — tags each complaint with
    reusable vs single_use class.
    """
    con = _connect(db_path)
    try:
        where: List[str] = [
            "COALESCE(c.csi_notification_date, c.date_entered) BETWEEN ? AND ?"
        ]
        params: List[Any] = [start_date, end_date]

        if td_id:
            where.append("c.td_id = ?")
            params.append(td_id)
        if product_numbers:
            placeholders = ",".join("?" * len(product_numbers))
            where.append(f"c.product_number IN ({placeholders})")
            params.extend(product_numbers)

        # JOIN with products so we get product_category/family/segment.
        sql = f"""
            SELECT
                COALESCE(c.csi_notification_date, c.date_entered) AS event_date,
                c.complaint_number,
                c.description,
                c.region,
                c.customer_country,
                c.product_number,
                c.mdr_issued,
                c.symptom_code,
                c.fault_code,
                c.failure_code,
                c.nonconformity,
                c.investigation_findings,
                c.complaint_type,
                c.product_sales_category,
                c.product_sales_subcategory,
                c.capa_number,
                c.mdr_number,
                c.td_id,
                p.item_description   AS product_description,
                p.product_category   AS product_category,
                p.product_family     AS product_family,
                p.product_segment    AS product_segment,
                p.product_main_group AS product_main_group
            FROM complaints c
            LEFT JOIN products p ON p.item_number = c.product_number
            WHERE {' AND '.join(where)}
        """
        rows = [dict(r) for r in con.execute(sql, params).fetchall()]
        total_pre_filter = con.execute("SELECT COUNT(*) FROM complaints").fetchone()[0]
    finally:
        con.close()

    total_complaints = len(rows)

    by_month: Dict[str, int] = {}
    by_region: Dict[str, int] = {}
    by_product_class: Dict[str, int] = {"reusable": 0, "single_use": 0, "unknown": 0}
    serious_incidents: List[Dict[str, Any]] = []
    complaint_summaries: List[Dict[str, Any]] = []

    for r in rows:
        date_str = (r.get("event_date") or "")[:10]
        month_key = date_str[:7] if len(date_str) >= 7 else ""
        if month_key:
            by_month[month_key] = by_month.get(month_key, 0) + 1

        region = _norm_region(r.get("region") or r.get("customer_country"))
        by_region[region] = by_region.get(region, 0) + 1

        serious = _is_serious(r.get("mdr_issued"))

        item = str(r.get("product_number") or "").strip()
        if product_classification and item in product_classification:
            cls = product_classification[item].get("class", "unknown")
        else:
            cls = _classify_single_use(
                item,
                r.get("product_description") or "",
                r.get("product_category") or "",
                r.get("product_family") or "",
                r.get("product_segment") or "",
            )
        by_product_class[cls] = by_product_class.get(cls, 0) + 1

        # Enrich description for IMDRF coder: raw description is often
        # terse ("broken", "leaked"). Adding symptom/fault/failure/
        # nonconformity/investigation_findings dramatically improves
        # auto-coding specificity.
        desc_parts: List[str] = []
        base_desc = str(r.get("description") or "").strip()
        if base_desc:
            desc_parts.append(base_desc)
        for key, label in [
            ("symptom_code", "Symptom"),
            ("fault_code", "Fault"),
            ("failure_code", "Failure"),
            ("nonconformity", "Nonconformity"),
            ("investigation_findings", "Investigation"),
            ("complaint_type", "Type"),
        ]:
            val = str(r.get(key) or "").strip()
            if val and val.lower() not in ("nan", "none", "null", "unknown", "n/a"):
                desc_parts.append(f"{label}: {val}")
        enriched_description = " | ".join(desc_parts)[:1500]

        summary = {
            "date": date_str,
            "complaint_number": str(r.get("complaint_number") or ""),
            "description": enriched_description,
            "imdrf_code": "",
            "harm": "",
            "region": region,
            "serious": serious,
            "product_number": item,
            "product_class": cls,
            "product_category": r.get("product_category") or "",
            "product_family": r.get("product_family") or "",
            "product_segment": r.get("product_segment") or "",
            "capa_number": str(r.get("capa_number") or "").strip() or None,
            "mdr_number": str(r.get("mdr_number") or "").strip() or None,
        }
        complaint_summaries.append(summary)

        if serious:
            serious_incidents.append({
                "date": date_str,
                "imdrf_code": "Unknown",
                "harm": "Unknown",
                "description": summary["description"],
                "region": region,
                "complaint_number": summary["complaint_number"],
                "product_number": item,
                "product_class": cls,
            })

    by_imdrf_code: Dict[str, int] = {}
    by_harm_category: Dict[str, int] = {}
    harm_by_imdrf: Dict[str, Dict[str, int]] = {}
    serious_by_region_imdrf: Dict[str, Dict[str, Any]] = {}

    complaint_number_format = (
        complaint_summaries[0]["complaint_number"] if complaint_summaries else ""
    )

    src_label = f"sqlite:{Path(db_path).name}"
    if td_id:
        src_label += f"?td_id={td_id}"

    return {
        "total_complaints": total_complaints,
        "by_month": by_month,
        "by_imdrf_code": by_imdrf_code,
        "by_harm_category": by_harm_category,
        "by_region": by_region,
        "by_product_class": by_product_class,
        "serious_incidents": serious_incidents,
        "serious_incident_count": len(serious_incidents),
        "complaint_summaries": complaint_summaries,
        "harm_by_imdrf": harm_by_imdrf,
        "serious_by_region_imdrf": serious_by_region_imdrf,
        "complaint_number_format": complaint_number_format,
        "period": {"start": start_date, "end": end_date},
        "source_file": src_label,
        "rows_processed": total_complaints,
        "rows_pre_filter": total_pre_filter,
        "column_mappings": {},
        "extra_columns": {},
    }


# ──────────────────────────────────────────────────────────────────
# Sales
# ──────────────────────────────────────────────────────────────────

def parse_sales_from_db(
    db_path: str | Path,
    start_date: str,
    end_date: str,
    td_id: Optional[str] = None,
    product_numbers: Optional[List[str]] = None,
    product_classification: Optional[Dict[str, Dict[str, str]]] = None,
    exclude_negative_units: bool = True,
) -> Dict[str, Any]:
    """Return sales dict matching parse_sales() shape.

    product_classification: optional map for reusable vs single_use split.
    exclude_negative_units: when True, rows resolving to negative unit counts
    are dropped (they usually represent credits/returns). Counts reported in
    `negative_unit_rows_excluded` for audit.
    """
    s_year, s_month = int(start_date[:4]), int(start_date[5:7])
    e_year, e_month = int(end_date[:4]), int(end_date[5:7])

    con = _connect(db_path)
    try:
        where = [
            "(s.calendar_year * 100 + COALESCE(s.month_num, 1)) BETWEEN ? AND ?"
        ]
        params: List[Any] = [s_year * 100 + s_month, e_year * 100 + e_month]

        if td_id:
            where.append("s.td_id = ?")
            params.append(td_id)
        if product_numbers:
            placeholders = ",".join("?" * len(product_numbers))
            where.append(f"s.item_number IN ({placeholders})")
            params.extend(product_numbers)

        sql = f"""
            SELECT
                s.calendar_year,
                s.month_num,
                s.customer_region,
                s.customer_country,
                s.item_number,
                s.item_description,
                s.quantity,
                s.units_shipped,
                s.pack_size,
                s.td_id,
                p.product_category   AS product_category,
                p.product_family     AS product_family,
                p.product_segment    AS product_segment,
                p.product_main_group AS product_main_group,
                p.item_description   AS product_description
            FROM sales s
            LEFT JOIN products p ON p.item_number = s.item_number
            WHERE {' AND '.join(where)}
        """
        rows = [dict(r) for r in con.execute(sql, params).fetchall()]
        total_pre_filter = con.execute("SELECT COUNT(*) FROM sales").fetchone()[0]
    finally:
        con.close()

    total_units = 0
    by_month: Dict[str, int] = {}
    by_region: Dict[str, int] = {}
    by_country: Dict[str, int] = {}
    by_product: Dict[str, int] = {}
    by_product_class: Dict[str, int] = {"reusable": 0, "single_use": 0, "unknown": 0}
    by_product_class_by_month: Dict[str, Dict[str, int]] = {
        "reusable": {}, "single_use": {}, "unknown": {},
    }
    by_product_class_by_region: Dict[str, Dict[str, int]] = {
        "reusable": {}, "single_use": {}, "unknown": {},
    }
    units_unknown_country = 0
    rows_unknown_country = 0
    negative_unit_rows_excluded = 0
    negative_units_total = 0

    for r in rows:
        units = r.get("units_shipped")
        if units is None:
            qty = r.get("quantity") or 0
            pack = r.get("pack_size") or 1
            try:
                units = float(qty) * float(pack or 1)
            except (TypeError, ValueError):
                units = 0
        try:
            units_int = int(round(float(units or 0)))
        except (TypeError, ValueError):
            units_int = 0

        # Negative units usually indicate credits/returns. Including them
        # creates negative monthly denominators which break rate math.
        if units_int < 0:
            negative_unit_rows_excluded += 1
            negative_units_total += units_int
            if exclude_negative_units:
                continue

        total_units += units_int

        year = r.get("calendar_year")
        month = r.get("month_num") or 1
        month_key = ""
        if year:
            try:
                month_key = f"{int(year):04d}-{int(month):02d}"
                by_month[month_key] = by_month.get(month_key, 0) + units_int
            except (TypeError, ValueError):
                pass

        region = _norm_region(r.get("customer_region"))
        by_region[region] = by_region.get(region, 0) + units_int

        country = (r.get("customer_country") or "").strip()
        if country and country.lower() != "unknown":
            by_country[country] = by_country.get(country, 0) + units_int
        else:
            units_unknown_country += units_int
            rows_unknown_country += 1

        product = (r.get("item_number") or "").strip()
        if product:
            by_product[product] = by_product.get(product, 0) + units_int

        if product_classification and product in product_classification:
            cls = product_classification[product].get("class", "unknown")
        else:
            cls = _classify_single_use(
                product,
                r.get("item_description") or r.get("product_description") or "",
                r.get("product_category") or "",
                r.get("product_family") or "",
                r.get("product_segment") or "",
            )
        by_product_class[cls] = by_product_class.get(cls, 0) + units_int
        if month_key:
            by_product_class_by_month[cls][month_key] = (
                by_product_class_by_month[cls].get(month_key, 0) + units_int
            )
        by_product_class_by_region[cls][region] = (
            by_product_class_by_region[cls].get(region, 0) + units_int
        )

    if negative_unit_rows_excluded > 0:
        logger.warning(
            "Sales DB: excluded %d rows with negative units (total=%d). "
            "These usually represent credits/returns.",
            negative_unit_rows_excluded, negative_units_total,
        )

    src_label = f"sqlite:{Path(db_path).name}"
    if td_id:
        src_label += f"?td_id={td_id}"

    return {
        "total_units": total_units,
        "by_month": by_month,
        "by_region": by_region,
        "by_country": by_country,
        "by_product": by_product,
        "by_product_class": by_product_class,
        "by_product_class_by_month": by_product_class_by_month,
        "by_product_class_by_region": by_product_class_by_region,
        "units_unknown_country": units_unknown_country,
        "rows_unknown_country": rows_unknown_country,
        "negative_unit_rows_excluded": negative_unit_rows_excluded,
        "negative_units_total": negative_units_total,
        "period": {"start": start_date, "end": end_date},
        "source_file": src_label,
        "rows_processed": len(rows),
        "rows_pre_filter": total_pre_filter,
        "column_mappings": {},
        "extra_columns": {},
    }


# ──────────────────────────────────────────────────────────────────
# Helpers exposed to the pipeline
# ──────────────────────────────────────────────────────────────────

def list_td_ids(db_path: str | Path) -> List[str]:
    """Return distinct td_id values with BOTH sales and complaints rows."""
    con = _connect(db_path)
    try:
        rows = con.execute("""
            SELECT DISTINCT s.td_id
            FROM sales s
            WHERE s.td_id IS NOT NULL AND s.td_id != ''
            INTERSECT
            SELECT DISTINCT c.td_id
            FROM complaints c
            WHERE c.td_id IS NOT NULL AND c.td_id != ''
        """).fetchall()
        return [r[0] for r in rows]
    finally:
        con.close()


def resolve_td_id_for_device(
    db_path: str | Path,
    device_name: str,
) -> Optional[str]:
    """Best-effort lookup: find a td_id whose products' descriptions match device_name."""
    if not device_name:
        return None
    name_lower = device_name.lower()
    con = _connect(db_path)
    try:
        rows = con.execute("""
            SELECT td_id, item_description
            FROM products
            WHERE td_id IS NOT NULL AND td_id != ''
        """).fetchall()
    finally:
        con.close()

    tokens = {t for t in name_lower.replace("-", " ").split() if len(t) > 3}
    if not tokens:
        return None

    scores: Dict[str, int] = {}
    for td_id, desc in rows:
        if not desc:
            continue
        d_lower = desc.lower()
        score = sum(1 for t in tokens if t in d_lower)
        if score > 0:
            scores[td_id] = scores.get(td_id, 0) + score

    if not scores:
        return None
    best = max(scores.items(), key=lambda kv: kv[1])
    logger.info(f"Resolved td_id={best[0]} for device '{device_name}' (score={best[1]})")
    return best[0]
