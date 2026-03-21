"""Per-section statistics filtering.

Extracted from orchestrator.py.  Returns only the statistics slice that a
given PSUR section should see, preventing the LLM from restating numbers
that belong to other sections.
"""
from typing import Any, Dict


def filter_statistics_for_section(
    section_key: str, stats_dict: Dict[str, Any]
) -> Dict[str, Any]:
    """Return only the statistics relevant to *section_key*.

    Prevents the LLM from seeing (and then restating) statistics that belong
    to other sections.  E.g., Section J (Literature) should never see monthly
    complaint rates or UCL values — those belong to Section G.
    """
    letter = section_key.split("_")[0]

    # Minimal context every section needs
    base: Dict[str, Any] = {
        "surveillance_period": stats_dict.get("surveillance_period"),
        "total_units_sold": stats_dict.get("total_units_sold"),
        "denominator_type": stats_dict.get("denominator_type"),
        "denominator_description": stats_dict.get("denominator_description"),
        "has_previous_period_data": stats_dict.get("has_previous_period_data"),
    }

    if letter == "A":
        base["total_complaints"] = stats_dict.get("total_complaints")
        base["serious_incident_count"] = stats_dict.get("serious_incident_count")
        base["overall_complaint_percentage"] = stats_dict.get("overall_complaint_percentage")
        base["serious_incident_rate"] = stats_dict.get("serious_incident_rate")
        trend = stats_dict.get("trend_analysis", {})
        base["trend_analysis"] = {
            "status": trend.get("status"),
            "western_electric_violations": trend.get("western_electric_violations", []),
        }
        return base

    elif letter == "B":
        base["eea_units"] = stats_dict.get("eea_units")
        base["eea_countries"] = stats_dict.get("eea_countries")
        base["uk_units"] = stats_dict.get("uk_units")
        base["uk_market_detected"] = stats_dict.get("uk_market_detected", False)
        return base

    elif letter == "C":
        base["units_by_region"] = stats_dict.get("units_by_region")
        base["units_by_month"] = stats_dict.get("units_by_month")
        base["units_by_product"] = stats_dict.get("units_by_product")
        base["eea_units"] = stats_dict.get("eea_units")
        base["eea_countries"] = stats_dict.get("eea_countries")
        base["uk_units"] = stats_dict.get("uk_units")
        base["uk_market_detected"] = stats_dict.get("uk_market_detected", False)
        base["section_c_region_rows"] = stats_dict.get("section_c_region_rows")
        base["countries_above_5pct"] = stats_dict.get("countries_above_5pct")
        base["yoy_volume_change"] = stats_dict.get("yoy_volume_change")
        return base

    elif letter == "D":
        base["total_complaints"] = stats_dict.get("total_complaints")
        base["serious_incident_count"] = stats_dict.get("serious_incident_count")
        base["serious_incidents_detail"] = stats_dict.get("serious_incidents_detail")
        base["serious_incidents_by_imdrf"] = stats_dict.get("serious_incidents_by_imdrf")
        base["serious_by_region_imdrf"] = stats_dict.get("serious_by_region_imdrf")
        base["serious_incident_rate"] = stats_dict.get("serious_incident_rate")
        base["serious_incident_rate_display"] = stats_dict.get("serious_incident_rate_display")
        base["eea_units"] = stats_dict.get("eea_units")
        base["uk_units"] = stats_dict.get("uk_units")
        base["uk_complaints"] = stats_dict.get("uk_complaints")
        base["uk_market_detected"] = stats_dict.get("uk_market_detected", False)
        return base

    elif letter == "E":
        base["total_complaints"] = stats_dict.get("total_complaints")
        base["serious_incident_count"] = stats_dict.get("serious_incident_count")
        return base

    elif letter == "F":
        base["total_complaints"] = stats_dict.get("total_complaints")
        base["serious_incident_count"] = stats_dict.get("serious_incident_count")
        base["complaints_by_imdrf"] = stats_dict.get("complaints_by_imdrf")
        base["complaints_by_harm"] = stats_dict.get("complaints_by_harm")
        base["complaints_by_region"] = stats_dict.get("complaints_by_region")
        base["harm_by_imdrf"] = stats_dict.get("harm_by_imdrf")
        base["overall_complaint_rate"] = stats_dict.get("overall_complaint_rate")
        base["overall_complaint_percentage"] = stats_dict.get("overall_complaint_percentage")
        base["overall_rate_display"] = stats_dict.get("overall_rate_display")
        base["rates_by_imdrf"] = stats_dict.get("rates_by_imdrf")
        base["rates_by_harm"] = stats_dict.get("rates_by_harm")
        base["table7_rows"] = stats_dict.get("table7_rows")
        base["rates_by_region"] = stats_dict.get("rates_by_region")
        base["complaint_number_format"] = stats_dict.get("complaint_number_format")
        return base

    elif letter == "G":
        base["total_complaints"] = stats_dict.get("total_complaints")
        base["trend_analysis"] = stats_dict.get("trend_analysis")
        base["complaints_by_month"] = stats_dict.get("complaints_by_month")
        base["units_by_month"] = stats_dict.get("units_by_month")
        base["overall_complaint_rate"] = stats_dict.get("overall_complaint_rate")
        base["overall_complaint_percentage"] = stats_dict.get("overall_complaint_percentage")
        return base

    elif letter == "H":
        base["total_complaints"] = stats_dict.get("total_complaints")
        base["serious_incident_count"] = stats_dict.get("serious_incident_count")
        return base

    elif letter == "I":
        base["total_complaints"] = stats_dict.get("total_complaints")
        base["serious_incident_count"] = stats_dict.get("serious_incident_count")
        # Pass CAPA-related summary stats so Section I can contextualise
        base["capa_total"] = stats_dict.get("capa_total")
        base["capa_open"] = stats_dict.get("capa_open")
        base["capa_closed"] = stats_dict.get("capa_closed")
        return base

    elif letter in ("J", "K", "L"):
        base["total_complaints"] = stats_dict.get("total_complaints")
        base["serious_incident_count"] = stats_dict.get("serious_incident_count")
        return base

    elif letter == "M":
        m_stats = dict(stats_dict)
        m_stats.pop("complaints_by_month", None)
        m_stats.pop("units_by_month", None)
        trend = m_stats.get("trend_analysis")
        if isinstance(trend, dict):
            m_trend = dict(trend)
            m_trend.pop("monthly_rates", None)
            m_trend.pop("monthly_labels", None)
            m_trend.pop("monthly_rates_pct", None)
            m_stats["trend_analysis"] = m_trend
        return m_stats

    # Fallback
    return stats_dict
