"""Inject pre-computed immutable values into section data before LLM generation.

Extracted from orchestrator.py.  Numbers that can be computed deterministically
from source files must never be left to the LLM to calculate — they are injected
here as ``_prefilled`` so the LLM receives them as facts.
"""
from typing import Any, Dict, Optional


def inject_prefilled_values(
    section_key: str,
    section_data: Optional[Dict],
    stats_dict: Dict[str, Any],
    device_context: Dict[str, Any],
) -> Optional[Dict]:
    """Inject pre-computed immutable values into section data.

    Numbers that can be computed deterministically from source files must
    never be left to the LLM to calculate.  They are injected here as
    ``_prefilled`` so the LLM receives them as facts.
    """
    if section_data is None:
        section_data = {}

    prefilled: Dict[str, Any] = {}
    letter = section_key.split("_")[0]

    # ── Section A: Executive Summary — high-level conclusion signals ──
    if letter == "A":
        for key in ("total_complaints", "total_units_sold", "serious_incident_count"):
            if stats_dict.get(key) is not None:
                prefilled[key] = stats_dict[key]
        wu = stats_dict.get("total_units_sold", 0)
        tc = stats_dict.get("total_complaints")
        si = stats_dict.get("serious_incident_count")
        if wu and tc is not None:
            prefilled["overall_complaint_rate_pct"] = round((tc / wu) * 100, 2) if wu > 0 else 0.0
        if wu and si is not None:
            prefilled["serious_incident_rate_pct"] = round((si / wu) * 100, 3) if wu > 0 else 0.0
        trend = stats_dict.get("trend_analysis", {})
        if trend:
            prefilled["trend_status"] = trend.get("status")
            prefilled["ucl_breaches"] = len(
                [v for v in trend.get("western_electric_violations", []) if "Rule 1" in v]
            )
        prefilled["denominator_term"] = stats_dict.get(
            "denominator_description", "units distributed"
        )

    elif letter == "C":
        if stats_dict.get("eea_units") is not None:
            prefilled["exact_eea_units_current_period"] = stats_dict["eea_units"]
        if stats_dict.get("total_units_sold") is not None:
            prefilled["exact_worldwide_units_current_period"] = stats_dict["total_units_sold"]
        if stats_dict.get("units_by_region"):
            prefilled["exact_sales_by_region"] = stats_dict["units_by_region"]
        # UK-specific sales total (Reg 44ZM(3) — devices placed on UK market)
        if stats_dict.get("uk_market_detected"):
            prefilled["exact_uk_units_current_period"] = stats_dict.get("uk_units", 0)
        if stats_dict.get("section_c_region_rows"):
            prefilled["exact_section_c_region_rows"] = stats_dict["section_c_region_rows"]
            total = stats_dict.get("total_units_sold", 0)
            prefilled["table1_ready_rows"] = [
                {
                    "region": r["region"],
                    "units_current_period": r["units"],
                    "percent_of_global": round((r["units"] / total) * 100, 1) if total > 0 else 0.0,
                }
                for r in stats_dict["section_c_region_rows"]
            ]
        if stats_dict.get("countries_above_5pct"):
            prefilled["country_count"] = len(stats_dict["countries_above_5pct"])
        ubr = stats_dict.get("units_by_region", {})
        if ubr:
            prefilled["total_country_count"] = len([k for k, v in ubr.items() if v > 0])
        prefilled["denominator_term"] = stats_dict.get(
            "denominator_description", "units distributed"
        )

    elif letter == "B":
        ki = device_context.get("known_identifiers", {})
        for field in (
            "eu_technical_documentation_number", "risk_management_file_number",
            "certificate_number", "basic_udi_di",
            "classification_rule_mdr_annex_viii", "emdn_code",
            "us_pre_market_submission_number", "fda_clearance",
        ):
            val = ki.get(field)
            if val:
                prefilled[field] = val
        # Device classification & sterility — ensures B states these correctly
        eu_class = device_context.get("device_class_eu", "")
        if eu_class:
            prefilled["device_class_eu"] = eu_class
        sterility = device_context.get("sterility_status", "")
        if sterility:
            prefilled["sterility_status"] = sterility

    elif letter == "D":
        si_count = stats_dict.get("serious_incident_count", 0)
        prefilled["exact_serious_incident_count"] = si_count
        wu = stats_dict.get("total_units_sold", 0)
        if wu > 0 and si_count is not None:
            prefilled["exact_serious_incident_rate"] = round((si_count / wu) * 100, 3)
        serious_by_region = stats_dict.get("serious_by_region_imdrf", {})
        if serious_by_region:
            table2_rows = []
            for key, detail in serious_by_region.items():
                parts = key.split("|")
                if len(parts) >= 2:
                    region, imdrf = parts[0], parts[1]
                    count = detail.get("count", 0) if isinstance(detail, dict) else detail
                    complaint_nums = (
                        detail.get("complaint_numbers", []) if isinstance(detail, dict) else []
                    )
                    table2_rows.append({
                        "region": region,
                        "imdrf_problem_term": imdrf,
                        "n_current_period": count,
                        "rate_percent": round((count / wu) * 100, 2) if wu > 0 else 0.00,
                        "complaint_number": ", ".join(complaint_nums) if complaint_nums else "N/A",
                    })
            prefilled["table2_ready_rows"] = table2_rows
        if stats_dict.get("serious_incidents_detail"):
            prefilled["serious_incidents_detail"] = stats_dict["serious_incidents_detail"]

    elif letter == "G":
        trend = stats_dict.get("trend_analysis", {})
        if trend:
            prefilled["mean_monthly_rate_pct"] = trend.get("mean_pct")
            prefilled["std_dev_pct"] = trend.get("std_dev_pct")
            prefilled["ucl_pct"] = trend.get("ucl_3sigma_pct")
            prefilled["lcl_pct"] = trend.get("lcl_3sigma_pct")
            prefilled["current_rate_pct"] = trend.get("current_rate_pct")
            prefilled["monthly_rates_pct"] = trend.get("monthly_rates_pct", [])
            prefilled["monthly_labels"] = trend.get("monthly_labels", [])
            prefilled["western_electric_violations"] = trend.get(
                "western_electric_violations", []
            )

    elif letter == "F":
        if stats_dict.get("table7_rows"):
            prefilled["table7_rows"] = stats_dict["table7_rows"]
            t7_rows = stats_dict["table7_rows"]
            prefilled["table7_annual_format_rows"] = [
                {
                    "harm": r.get("harm", "N/A"),
                    "medical_device_problem": r.get("medical_device_problem", "N/A"),
                    "current_12_month_complaint_count": r.get("complaint_count", 0),
                    "current_12_month_complaint_rate": r.get("complaint_percentage", 0.00),
                    "max_expected_rate_of_occurrence_from_ract": (
                        r.get("ract_max_expected_rate")
                        if r.get("ract_max_expected_rate") is not None
                        else "N/A"
                    ),
                }
                for r in t7_rows
            ]
            tc_sum = sum(r.get("complaint_count", 0) for r in t7_rows)
            wu = stats_dict.get("total_units_sold", 0)
            prefilled["table7_grand_total_row"] = {
                "harm": "Grand Total",
                "medical_device_problem": "",
                "current_12_month_complaint_count": tc_sum,
                "current_12_month_complaint_rate": (
                    round((tc_sum / wu) * 100, 2) if wu and wu > 0 else 0.0
                ),
                "max_expected_rate_of_occurrence_from_ract": "N/A",
            }
        tc = stats_dict.get("total_complaints")
        wu = stats_dict.get("total_units_sold")
        if tc is not None:
            prefilled["exact_total_complaints"] = tc
        if wu and tc is not None:
            prefilled["exact_grand_total_rate"] = round((tc / wu) * 100, 2) if wu > 0 else 0.0

    elif letter == "E":
        if stats_dict.get("total_complaints") is not None:
            prefilled["total_complaints"] = stats_dict["total_complaints"]
        if stats_dict.get("serious_incident_count") is not None:
            prefilled["serious_incident_count"] = stats_dict["serious_incident_count"]

    elif letter == "H":
        if stats_dict.get("total_complaints") is not None:
            prefilled["total_complaints"] = stats_dict["total_complaints"]
        if stats_dict.get("serious_incident_count") is not None:
            prefilled["serious_incident_count"] = stats_dict["serious_incident_count"]

    elif letter == "I":
        if stats_dict.get("total_complaints") is not None:
            prefilled["total_complaints"] = stats_dict["total_complaints"]
        if stats_dict.get("serious_incident_count") is not None:
            prefilled["serious_incident_count"] = stats_dict["serious_incident_count"]

        # Pre-format Table 9 rows from parsed CAPA data so the LLM
        # does not need to restructure raw records (and risk hallucinating
        # "no CAPAs" when records are present).
        capa_data = section_data.get("capa") if section_data else None
        if isinstance(capa_data, dict):
            capa_records = capa_data.get("capa_records", [])
            if capa_records:
                _STATUS_MAP = {
                    "open": "Open", "closed": "Closed",
                    "in progress": "In Progress", "in_progress": "In Progress",
                    "completed": "Closed", "implemented": "Closed",
                    "verified": "Closed",
                }
                table9_rows = []
                for rec in capa_records:
                    raw_status = str(rec.get("status", "Open")).strip()
                    norm_status = _STATUS_MAP.get(raw_status.lower(), raw_status)
                    if norm_status not in ("Open", "Closed", "In Progress"):
                        norm_status = "Open"
                    table9_rows.append({
                        "capa_number": rec.get("capa_number", "N/A"),
                        "initiation_date": rec.get("open_date", "N/A"),
                        "scope": rec.get("type", "Corrective"),
                        "status": norm_status,
                        "description": rec.get("title", "N/A"),
                        "root_cause": rec.get("root_cause", "N/A"),
                        "effectiveness": (
                            "Verified effective" if norm_status == "Closed"
                            else "Pending verification"
                        ),
                        "target_completion_date": rec.get("close_date") or None,
                    })
                prefilled["table9_ready_rows"] = table9_rows
                prefilled["total_capas_in_period"] = len(table9_rows)

    elif letter in ("J", "K", "L"):
        if stats_dict.get("total_complaints") is not None:
            prefilled["total_complaints"] = stats_dict["total_complaints"]
        if stats_dict.get("serious_incident_count") is not None:
            prefilled["serious_incident_count"] = stats_dict["serious_incident_count"]
        wu = stats_dict.get("total_units_sold", 0)
        if wu:
            prefilled["total_units_sold"] = wu

    elif letter == "M":
        for key in (
            "total_complaints", "total_units_sold", "eea_units", "serious_incident_count",
        ):
            if stats_dict.get(key) is not None:
                prefilled[key] = stats_dict[key]

        # ── Manufacturer identity — prevents M from fabricating ──
        mfr_name = (
            device_context.get("manufacturer_name")
            or device_context.get("manufacturer_info", {}).get("company_name", "")
        )
        if mfr_name:
            prefilled["manufacturer_name"] = mfr_name
        mfr_srn = device_context.get("manufacturer_info", {}).get("manufacturer_srn", "")
        if mfr_srn:
            prefilled["manufacturer_srn"] = mfr_srn

        # UK market data for Section M synthesis
        if stats_dict.get("uk_market_detected"):
            prefilled["uk_units"] = stats_dict.get("uk_units", 0)
            prefilled["uk_complaints"] = stats_dict.get("uk_complaints", 0)
            prefilled["uk_market_detected"] = True
        wu = stats_dict.get("total_units_sold", 0)
        tc = stats_dict.get("total_complaints")
        si = stats_dict.get("serious_incident_count")
        if wu and tc is not None:
            prefilled["exact_overall_complaint_rate"] = (
                round((tc / wu) * 100, 2) if wu > 0 else 0.0
            )
        if wu and si is not None:
            prefilled["exact_serious_incident_rate"] = (
                round((si / wu) * 100, 3) if wu > 0 else 0.0
            )
        prefilled["denominator_term"] = stats_dict.get(
            "denominator_description", "units distributed"
        )

        # ── Regional breakdown (same as Section C) — prevents M from fabricating ──
        if stats_dict.get("section_c_region_rows"):
            total = stats_dict.get("total_units_sold", 0)
            prefilled["exact_region_breakdown"] = [
                {
                    "region": r["region"],
                    "units": r["units"],
                    "percent_of_global": round((r["units"] / total) * 100, 1) if total > 0 else 0.0,
                }
                for r in stats_dict["section_c_region_rows"]
            ]

        # ── Complaint category summary (same as Section F) — prevents M fabrication ──
        if stats_dict.get("table7_rows"):
            prefilled["exact_complaint_categories"] = [
                {
                    "harm": r.get("harm", "N/A"),
                    "medical_device_problem": r.get("medical_device_problem", "N/A"),
                    "count": r.get("complaint_count", 0),
                    "rate_pct": r.get("complaint_percentage", 0.0),
                }
                for r in stats_dict["table7_rows"]
            ]

        # ── Device classification awareness for M's regulatory conclusions ──
        eu_class = device_context.get("device_class_eu", "")
        if eu_class:
            prefilled["device_class_eu"] = eu_class
        sterility = device_context.get("sterility_status", "")
        if sterility:
            prefilled["sterility_status"] = sterility

    if prefilled:
        section_data["_prefilled"] = prefilled

    return section_data if section_data else None
