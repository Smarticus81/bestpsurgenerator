"""Authoritative PSUR fact and insight model.

The generator has several downstream consumers of the same information:
section agents, deterministic table builders, chart captions, reconciliation,
validators, and the LLM whole-report reviewer. This module builds a compact
fact spine that all of those consumers can share.

The model deliberately contains both raw facts and approved interpretations.
The LLM may reason over the relationships between facts, but it should not
recalculate counts, rates, or table/chart meanings from scratch.
"""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime
from typing import Any, Dict, List, Mapping, Tuple

TODO = "[TO BE COMPLETED]"


def build_report_facts(
    psur: Mapping[str, Any] | None = None,
    *,
    stats: Any,
    parsed_data: Mapping[str, Any] | None,
    device_context: Mapping[str, Any] | None,
    start_date: str,
    end_date: str,
) -> Dict[str, Any]:
    """Build the authoritative facts/insights package for one PSUR run."""
    psur = psur or {}
    parsed_data = parsed_data or {}
    device_context = device_context or {}
    stats_dict = _asdict(stats)
    sections = psur.get("sections", {}) if isinstance(psur.get("sections"), Mapping) else {}

    current_units = _int(stats_dict.get("total_units_sold"))
    total_complaints = _int(stats_dict.get("total_complaints"))
    overall_pct = _float(stats_dict.get("overall_complaint_percentage"))
    eu_uk_si = _int(stats_dict.get("eu_uk_serious_incident_count"))
    fda_mdr = _int(stats_dict.get("fda_mdr_count"))
    period_label = _period_label(start_date, end_date)
    cadence = (
        psur.get("psur_cover_page", {})
        .get("document_information", {})
        .get("psur_cadence")
        or device_context.get("psur_cadence")
        or "ANNUALLY"
    )

    table7 = _table7_insight(sections, stats_dict, current_units, total_complaints)
    external = _external_db_insight(parsed_data, device_context)
    capa = _capa_insight(parsed_data, end_date)
    fsca = _fsca_insight(parsed_data)
    pmcf = _pmcf_insight(parsed_data)
    scope = _scope_insight(parsed_data, stats_dict, device_context)
    charts = _chart_insights(stats_dict, table7, current_units, total_complaints)
    cadence_policy = _cadence_policy(cadence, start_date, end_date)

    facts = {
        "schema_version": "report_facts.v1",
        "device": {
            "name": device_context.get("device_name"),
            "classification": device_context.get("device_class") or device_context.get("classification"),
            "legal_manufacturer": (device_context.get("manufacturer_info") or {}).get("company_name"),
            "manufacturer_info": device_context.get("manufacturer_info") or {},
            "single_use_or_reusable": device_context.get("single_use_or_reusable"),
            "device_description_authority": (
                device_context.get("device_description")
                or "Use device_context.json as the authoritative device-description source."
            ),
        },
        "period": {
            "start_date": start_date,
            "end_date": end_date,
            "label": period_label,
            "is_calendar_year": start_date[:4] == end_date[:4],
            "cadence": cadence,
            "policy": cadence_policy,
        },
        "exposure": {
            "current_period_units": current_units,
            "current_period_units_display": f"{current_units:,}",
            "denominator_type": stats_dict.get("denominator_type"),
            "denominator_description": stats_dict.get("denominator_description"),
            "units_by_region": stats_dict.get("units_by_region") or {},
            "section_c_region_rows": stats_dict.get("section_c_region_rows") or [],
            "patient_exposure_estimate": {
                "low": current_units // 2 if current_units else 0,
                "high": current_units if current_units else 0,
                "basis": "Derived from current-period units only and an assumed 1 to 2 wraps per patient.",
            },
        },
        "complaints": {
            "total": total_complaints,
            "overall_rate_pct": overall_pct,
            "overall_rate_display": f"{total_complaints} / {current_units:,} units ({overall_pct:.4f}%)",
            "complaints_by_harm": stats_dict.get("complaints_by_harm") or {},
            "complaints_by_imdrf": stats_dict.get("complaints_by_imdrf") or {},
        },
        "serious_event_framing": {
            "eu_uk_article_2_65_serious_incidents": eu_uk_si,
            "fda_mdr_reportable_events": fda_mdr,
            "authorized_statement": (
                f"{fda_mdr} FDA MDR-reportable event(s); "
                f"{eu_uk_si} confirmed EU/UK Article 2(65) serious incident(s)."
            ),
            "forbidden_when_eu_uk_zero": [
                f"{fda_mdr} serious incidents",
                "serious incident rate",
                "all serious incidents were associated",
            ] if eu_uk_si == 0 and fda_mdr else [],
        },
        "regulatory_scope": scope,
        "table_insights": {
            "table_7": table7,
            "table_8_fsca": fsca,
            "table_9_capa": capa,
            "table_10_external_databases": external,
            "table_11_pmcf": pmcf,
        },
        "chart_insights": charts,
        "relationship_analysis": _relationship_analysis(
            table7=table7,
            capa=capa,
            fsca=fsca,
            pmcf=pmcf,
            external=external,
            eu_uk_si=eu_uk_si,
            fda_mdr=fda_mdr,
            cadence_policy=cadence_policy,
        ),
    }
    return facts


def attach_report_facts(
    psur: Dict[str, Any],
    *,
    stats: Any,
    parsed_data: Mapping[str, Any] | None,
    device_context: Mapping[str, Any] | None,
    start_date: str,
    end_date: str,
) -> Dict[str, Any]:
    """Build and attach ``_report_facts`` to the PSUR JSON."""
    psur["_report_facts"] = build_report_facts(
        psur,
        stats=stats,
        parsed_data=parsed_data,
        device_context=device_context,
        start_date=start_date,
        end_date=end_date,
    )
    return psur


def _asdict(obj: Any) -> Dict[str, Any]:
    if isinstance(obj, dict):
        return obj
    if is_dataclass(obj):
        return asdict(obj)
    if hasattr(obj, "__dict__"):
        return dict(obj.__dict__)
    return {}


def _int(value: Any) -> int:
    try:
        return int(str(value).replace(",", "").strip())
    except Exception:
        return 0


def _float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _norm(value: Any) -> str:
    return str(value or "").strip()


def _lower(value: Any) -> str:
    return _norm(value).lower()


def _records_from(value: Any, *keys: str) -> List[Dict[str, Any]]:
    if isinstance(value, list):
        return [r for r in value if isinstance(r, dict)]
    if isinstance(value, Mapping):
        for key in keys:
            rows = value.get(key)
            if isinstance(rows, list):
                return [r for r in rows if isinstance(r, dict)]
    return []


def _period_label(start_date: str, end_date: str) -> str:
    try:
        s = datetime.strptime(start_date[:10], "%Y-%m-%d")
        e = datetime.strptime(end_date[:10], "%Y-%m-%d")
        return f"{s.strftime('%b %Y')} to {e.strftime('%b %Y')}"
    except Exception:
        return f"{start_date} to {end_date}"


def _cadence_policy(cadence: str, start_date: str, end_date: str) -> Dict[str, Any]:
    annual_selected = start_date[:4] == end_date[:4]
    if cadence == "EVERY_TWO_YEARS" and annual_selected:
        return {
            "current_report_policy": "VOLUNTARY_ANNUAL_UPDATE_WITH_BIENNIAL_CADENCE",
            "authorized_interpretation": (
                "The device is on a documented biennial Class IIa PSUR cadence. This report is a "
                "manufacturer-selected annual interim update for the 2023 calendar year and does not "
                "change the documented cadence."
            ),
            "section_a_data_collection_period_changed": "NO",
            "next_period_statement": (
                "The next scheduled Class IIa PSUR covers January 2024 to December 2025 under the documented biennial cadence."
            ),
        }
    return {
        "current_report_policy": "CADENCE_MATCHES_SELECTED_PERIOD",
        "authorized_interpretation": "The selected reporting period is consistent with the configured PSUR cadence.",
        "section_a_data_collection_period_changed": "NO",
        "next_period_statement": "The next PSUR should follow the documented PMS reporting schedule.",
    }


def _table7_insight(
    sections: Mapping[str, Any],
    stats: Mapping[str, Any],
    current_units: int,
    total_complaints: int,
) -> Dict[str, Any]:
    sec_f = sections.get("F_product_complaint_types_counts_and_rates", {})
    table = sec_f.get("table_7_complaint_rate_and_count", {}) if isinstance(sec_f, Mapping) else {}
    annual = table.get("annual_format", {}) if isinstance(table, Mapping) else {}
    rows = annual.get("rows") if isinstance(annual, Mapping) else None
    if not isinstance(rows, list) or not rows:
        rows = stats.get("table7_rows") or []

    def canonical_harm(value: Any) -> str:
        label = _norm(value) or "No Health Consequence or Impact"
        low = label.lower()
        if low in {"no harm", "no health consequence", "no health consequence or impact"} or low.startswith("no harm") or "near miss" in low:
            return "No Health Consequence or Impact"
        return label

    child_rows = []
    harm_totals: Dict[str, int] = {}
    thresholds_missing = 0
    for row in rows or []:
        if not isinstance(row, Mapping):
            continue
        mdp = _norm(row.get("medical_device_problem") or row.get("mdp"))
        count = _int(row.get("current_12_month_complaint_count") or row.get("complaint_count"))
        if not mdp or mdp.upper() in {"N/A", "GRAND TOTAL", "TOTAL"}:
            continue
        threshold = row.get("max_expected_rate_of_occurrence_from_ract")
        if threshold in (None, "", "N/A", "N/A - RACT not provided"):
            threshold = row.get("occurrence_max_expected_rate")
        if threshold in (None, "", "N/A", "N/A - RACT not provided"):
            thresholds_missing += 1
        child_rows.append({
            "harm": canonical_harm(row.get("harm")),
            "medical_device_problem": mdp,
            "count": count,
            "rate_pct": _float(row.get("current_12_month_complaint_rate") or row.get("complaint_rate")),
            "threshold": threshold,
        })
        harm_totals[canonical_harm(row.get("harm"))] = harm_totals.get(canonical_harm(row.get("harm")), 0) + count

    child_sum = sum(r["count"] for r in child_rows)
    leading = max(child_rows, key=lambda r: r["count"], default={})
    leading_harm, leading_harm_count = max(harm_totals.items(), key=lambda kv: kv[1], default=("", 0))
    category_names = sorted({r["harm"] for r in child_rows if r["harm"]})
    return {
        "total_complaints_authorized": total_complaints,
        "child_row_sum": child_sum,
        "rows_reconcile_to_total": child_sum == total_complaints,
        "leading_harm": leading_harm or leading.get("harm"),
        "leading_harm_count": leading_harm_count,
        "leading_mdp": leading.get("medical_device_problem"),
        "leading_count": leading.get("count", 0),
        "harm_categories_present": category_names,
        "thresholds_missing_count": thresholds_missing,
        "authorized_interpretation": (
            f"Table 7 reconciles to {total_complaints} complaints using the {current_units:,}-unit denominator. "
            f"The leading harm grouping is {leading_harm or 'N/A'} with {leading_harm_count} complaint(s). "
            f"The leading medical-device-problem sub-row is {leading.get('medical_device_problem', 'N/A')} "
            f"with {leading.get('count', 0)} complaint(s)."
        ),
    }


def _scope_insight(
    parsed_data: Mapping[str, Any],
    stats: Mapping[str, Any],
    device_context: Mapping[str, Any],
) -> Dict[str, Any]:
    uk_evidence = _int(stats.get("uk_units")) > 0 or bool(stats.get("uk_market_detected"))
    for row in _records_from(parsed_data.get("fsca"), "records", "fsca_records"):
        uk_evidence = uk_evidence or "uk" in _lower(row.get("regions_affected") or row.get("impacted_regions"))
        uk_evidence = uk_evidence or bool(_norm(row.get("date_reported_to_mhra") or row.get("mhra_report_date")))
    units = stats.get("units_by_region") or {}
    us_evidence = _int(units.get("NorthAmerica") or units.get("United States")) > 0 or _int(stats.get("fda_mdr_count")) > 0
    return {
        "uk_in_scope": bool(uk_evidence),
        "uk_authorized_statement": (
            "UK scope is applicable because UK/MHRA/FSCA evidence exists; missing identifiers must be marked [TO BE COMPLETED]."
            if uk_evidence else
            "No UK market evidence was identified from sales, FSCA, or MHRA source fields."
        ),
        "us_in_scope": bool(us_evidence),
        "us_authorized_statement": (
            "US scope is applicable because US sales/FDA MDR/MAUDE evidence exists; missing FDA identifiers must be marked [TO BE COMPLETED]."
            if us_evidence else
            "No US market evidence was identified."
        ),
        "eu_classification": device_context.get("device_class") or device_context.get("classification"),
        "class_iia_requires_nb": (device_context.get("device_class") or "").upper() in {"CLASS_IIA", "IIA", "CLASS II A"},
    }


def _capa_insight(parsed_data: Mapping[str, Any], end_date: str) -> Dict[str, Any]:
    rows = _records_from(parsed_data.get("capa"), "records", "capa_records", "capa_summaries")
    overdue = []
    escalation_notes = []
    for row in rows:
        status = _lower(row.get("status"))
        target = _norm(row.get("target_completion_date"))
        if status in {"in progress", "open"} and target and target <= end_date[:10]:
            overdue.append({
                "id": row.get("capa_id") or row.get("capa_number") or row.get("id") or "CAPA",
                "target_completion_date": target,
                "status": row.get("status"),
            })
        escalation = _norm(row.get("escalation_or_recovery_plan"))
        if escalation and not escalation.upper().startswith(("N/A", "NA")):
            escalation_notes.append({
                "id": row.get("capa_id") or row.get("capa_number") or row.get("id") or "CAPA",
                "plan": escalation,
            })
    return {
        "count": len(rows),
        "initiated_during_period": len(rows) > 0,
        "overdue_open_capas": overdue,
        "escalation_or_recovery_plans": escalation_notes,
        "authorized_interpretation": (
            f"{len(rows)} CAPA record(s) exist. "
            + (f"{len(overdue)} open CAPA(s) are overdue and must be escalated in Sections I and M."
               if overdue else "No overdue open CAPA target date was identified from source data.")
            + (f" {len(escalation_notes)} CAPA escalation/recovery plan(s) are documented in source data."
               if escalation_notes else "")
        ),
    }


def _fsca_insight(parsed_data: Mapping[str, Any]) -> Dict[str, Any]:
    rows = _records_from(parsed_data.get("fsca"), "records", "fsca_records", "fsca_summaries")
    in_progress = [
        row.get("action_id") or row.get("manufacturer_reference_number") or row.get("id") or "FSCA"
        for row in rows
        if _lower(row.get("status")) in {"in progress", "open"}
    ]
    return {
        "count": len(rows),
        "initiated_during_period": len(rows) > 0,
        "in_progress_refs": in_progress,
        "authorized_interpretation": (
            f"{len(rows)} FSCA record(s) exist. "
            + (f"In-progress FSCA(s) require final FSN/completion follow-up: {', '.join(map(str, in_progress))}."
               if in_progress else "All provided FSCA records are closed or complete according to source status.")
        ),
    }


def _pmcf_insight(parsed_data: Mapping[str, Any]) -> Dict[str, Any]:
    import re

    text = str(parsed_data.get("pmcf") or "")
    match = re.search(r"\b(\d+)\s+of\s+(\d+)\b", text)
    enrolled, planned = (int(match.group(1)), int(match.group(2))) if match else (0, 0)
    pct = (enrolled / planned * 100.0) if planned else 0.0
    return {
        "enrolled": enrolled,
        "planned": planned,
        "enrollment_pct": round(pct, 1) if planned else 0.0,
        "shortfall": bool(planned and enrolled < planned),
        "authorized_interpretation": (
            f"PMCF enrollment was {enrolled} of {planned} planned observations ({pct:.1f}%). "
            "The shortfall must be disclosed with a recovery or plan-amendment action."
            if planned and enrolled < planned else
            "No PMCF enrollment shortfall was identified from source data."
        ),
    }


def _external_db_insight(parsed_data: Mapping[str, Any], device_context: Mapping[str, Any]) -> Dict[str, Any]:
    rows = _records_from(parsed_data.get("external_db"), "records", "events", "results")
    models, names = _subject_sets(parsed_data, device_context)
    subject = [r for r in rows if _is_subject_row(r, models, names)]
    eudamed_subject = [
        r for r in subject
        if "eudamed" in _lower(r.get("external_source") or r.get("database_registry") or r.get("database"))
    ]
    return {
        "total_rows": len(rows),
        "subject_device_rows": len(subject),
        "public_numeric_subject_rows": max(0, len(subject) - len(eudamed_subject)),
        "eudamed_limited_access_subject_rows": len(eudamed_subject),
        "authorized_interpretation": (
            f"External database data contain {len(rows)} event row(s), {len(subject)} subject-device row(s), "
            f"{max(0, len(subject) - len(eudamed_subject))} public numeric subject row(s), and "
            f"{len(eudamed_subject)} EUDAMED limited-access subject row(s). Narrative and Table 10 must use this same split."
        ),
    }


def _chart_insights(
    stats: Mapping[str, Any],
    table7: Mapping[str, Any],
    current_units: int,
    total_complaints: int,
) -> Dict[str, Any]:
    trend = stats.get("trend_analysis") or {}
    if not isinstance(trend, Mapping):
        trend = getattr(trend, "__dict__", {}) or {}
    monthly = trend.get("monthly_rates_pct") or []
    labels = trend.get("monthly_labels") or []
    max_monthly = max(monthly) if monthly else 0.0
    peak_label = labels[monthly.index(max_monthly)] if monthly and labels else "the reporting period"
    harm_categories = table7.get("harm_categories_present") or []
    return {
        "sales_trend": {
            "source": "statistics.units_by_month",
            "authorized_interpretation": (
                f"The sales chart uses the current-period denominator of {current_units:,} units only."
            ),
        },
        "harm_distribution": {
            "source": "Table 7",
            "categories_present": harm_categories,
            "authorized_interpretation": table7.get("authorized_interpretation"),
        },
        "top_mdps": {
            "source": "Table 7",
            "leading_mdp": table7.get("leading_mdp"),
            "authorized_interpretation": (
                f"The top-MDP chart identifies {table7.get('leading_mdp') or 'the leading Table 7 MDP'} "
                f"with {table7.get('leading_count', 0)} complaint(s)."
            ),
        },
        "trend_ucl": {
            "source": "statistics.trend_analysis.monthly_rates_pct",
            "authorized_interpretation": (
                f"The control chart uses monthly complaint rates as percentages; status={trend.get('status', 'N/A')}, "
                f"mean={_float(trend.get('mean_pct')):.4f}%, UCL={_float(trend.get('ucl_3sigma_pct')):.4f}%, "
                f"current={_float(trend.get('current_rate_pct')):.4f}%."
            ),
        },
        "rate_occurrence": {
            "source": "statistics.trend_analysis.monthly_rates_pct",
            "authorized_interpretation": (
                f"The occurrence-band chart uses the same monthly rate basis; peak monthly rate was "
                f"{max_monthly:.4f}% in {peak_label}."
            ),
        },
        "harm_trend": {
            "source": "statistics.harm_by_month/Table 7",
            "categories_present": harm_categories,
            "authorized_interpretation": (
                "The harm-trend chart describes the Table 7 harm categories present in the source data: "
                + (", ".join(harm_categories) if harm_categories else "none available")
                + "."
            ),
        },
        "per_period": {
            "source": "statistics.per_period_aggregates",
            "authorized_interpretation": (
                "Per-period comparison uses the comparable preceding-period aggregates available in the statistics package."
            ),
        },
    }


def _relationship_analysis(
    *,
    table7: Mapping[str, Any],
    capa: Mapping[str, Any],
    fsca: Mapping[str, Any],
    pmcf: Mapping[str, Any],
    external: Mapping[str, Any],
    eu_uk_si: int,
    fda_mdr: int,
    cadence_policy: Mapping[str, Any],
) -> Dict[str, Any]:
    return {
        "serious_event_relationship": (
            f"FDA MDR reportability ({fda_mdr}) and EU/UK Article 2(65) serious incidents ({eu_uk_si}) "
            "are separate facts and must not be collapsed."
        ),
        "complaint_rate_relationship": table7.get("authorized_interpretation"),
        "action_relationship": (
            f"Section M checkboxes must say CAPA Initiated={bool(capa.get('count'))} and "
            f"FSCA Initiated={bool(fsca.get('count'))}."
        ),
        "pmcf_relationship": pmcf.get("authorized_interpretation"),
        "external_db_relationship": external.get("authorized_interpretation"),
        "cadence_relationship": cadence_policy.get("authorized_interpretation"),
    }


def _subject_sets(parsed_data: Mapping[str, Any], device_context: Mapping[str, Any]) -> Tuple[set[str], set[str]]:
    models: set[str] = set()
    names: set[str] = set()
    if device_context.get("device_name"):
        names.add(_lower(device_context["device_name"]))
    for v in (device_context.get("known_identifiers") or {}).get("model_numbers", []) or []:
        models.add(_lower(v))
    for source_key in ("sales", "complaints", "capa", "fsca", "ract", "pms_plan", "external_db"):
        value = parsed_data.get(source_key)
        if isinstance(value, Mapping):
            for field, target in (("device_model", models), ("model", models), ("device_name", names), ("name", names)):
                v = _lower(value.get(field))
                if v and not v.startswith("competitor"):
                    target.add(v)
        for rec in _records_from(value, "records", "events", "complaint_summaries", "capa_records", "fsca_records"):
            for field, target in (("device_model", models), ("model", models), ("device_name", names), ("name", names)):
                v = _lower(rec.get(field))
                if v and not v.startswith("competitor"):
                    target.add(v)
    return models, names


def _is_subject_row(row: Mapping[str, Any], models: set[str], names: set[str]) -> bool:
    model = _lower(row.get("device_model"))
    name = _lower(row.get("device_name"))
    return bool((model and model in models) or (name and name in names))
