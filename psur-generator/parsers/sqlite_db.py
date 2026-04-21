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
# Complaints
# ──────────────────────────────────────────────────────────────────

def parse_complaints_from_db(
    db_path: str | Path,
    start_date: str,
    end_date: str,
    td_id: Optional[str] = None,
    product_numbers: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Return complaints dict matching parse_complaints() shape."""
    con = _connect(db_path)
    try:
        # Use COALESCE so we filter on csi_notification_date when present,
        # falling back to date_entered.
        where: List[str] = [
            "COALESCE(csi_notification_date, date_entered) BETWEEN ? AND ?"
        ]
        params: List[Any] = [start_date, end_date]

        if td_id:
            where.append("td_id = ?")
            params.append(td_id)
        if product_numbers:
            placeholders = ",".join("?" * len(product_numbers))
            where.append(f"product_number IN ({placeholders})")
            params.extend(product_numbers)

        sql = f"""
            SELECT
                COALESCE(csi_notification_date, date_entered) AS event_date,
                complaint_number,
                description,
                region,
                customer_country,
                product_number,
                mdr_issued,
                symptom_code,
                fault_code,
                failure_code,
                td_id
            FROM complaints
            WHERE {' AND '.join(where)}
        """
        rows = [dict(r) for r in con.execute(sql, params).fetchall()]

        # Pre-filter row count for transparency
        count_sql = "SELECT COUNT(*) FROM complaints"
        total_pre_filter = con.execute(count_sql).fetchone()[0]
    finally:
        con.close()

    total_complaints = len(rows)

    by_month: Dict[str, int] = {}
    by_region: Dict[str, int] = {}
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

        summary = {
            "date": date_str,
            "complaint_number": str(r.get("complaint_number") or ""),
            "description": str(r.get("description") or "")[:500],
            "imdrf_code": "",   # auto-coded downstream
            "harm": "",          # auto-coded downstream
            "region": region,
            "serious": serious,
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
            })

    # IMDRF / harm cross-tabs are populated AFTER auto-coding by
    # input_parsing._rebuild_imdrf_counts(); leave empty here.
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
) -> Dict[str, Any]:
    """Return sales dict matching parse_sales() shape."""
    # Sales has no day-level date — only calendar_year + month_num.
    # Build YYYY-MM-01 inclusive range and compare against synthesized date.
    s_year, s_month = int(start_date[:4]), int(start_date[5:7])
    e_year, e_month = int(end_date[:4]), int(end_date[5:7])

    con = _connect(db_path)
    try:
        where = [
            "(calendar_year * 100 + COALESCE(month_num, 1)) "
            "BETWEEN ? AND ?"
        ]
        params: List[Any] = [s_year * 100 + s_month, e_year * 100 + e_month]

        if td_id:
            where.append("td_id = ?")
            params.append(td_id)
        if product_numbers:
            placeholders = ",".join("?" * len(product_numbers))
            where.append(f"item_number IN ({placeholders})")
            params.extend(product_numbers)

        sql = f"""
            SELECT
                calendar_year,
                month_num,
                customer_region,
                customer_country,
                item_number,
                item_description,
                quantity,
                units_shipped,
                pack_size,
                td_id
            FROM sales
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
    units_unknown_country = 0
    rows_unknown_country = 0

    for r in rows:
        # Prefer units_shipped (true unit count); fall back to quantity * pack_size,
        # and finally raw quantity.
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

        total_units += units_int

        year = r.get("calendar_year")
        month = r.get("month_num") or 1
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

    src_label = f"sqlite:{Path(db_path).name}"
    if td_id:
        src_label += f"?td_id={td_id}"

    return {
        "total_units": total_units,
        "by_month": by_month,
        "by_region": by_region,
        "by_country": by_country,
        "by_product": by_product,
        "units_unknown_country": units_unknown_country,
        "rows_unknown_country": rows_unknown_country,
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
    """Return distinct td_id values that have BOTH sales and complaints rows."""
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

    # Score each td_id by the number of products whose description shares
    # tokens with the device name.
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
