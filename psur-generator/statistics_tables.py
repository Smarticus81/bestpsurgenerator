"""Enhance PSUR Section C sales distribution table generation.

Provides helpers to improve period determination, percentage calculation,
and high-volume country identification.
"""
from typing import Dict, List, Any, Tuple
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta


def determine_12month_periods_from_dates(
    start_date: str,
    end_date: str
) -> List[Tuple[str, str, str]]:
    """
    Determine 12-month periods building backwards from end_date.

    Returns list of (start_iso, end_iso, label) tuples, ordered from oldest to newest.

    Example:
        Jul 2022 - Jun 2025 (36 months) →
        [
            ("2022-07-01", "2023-06-30", "Jul-2022 to Jun-2023"),
            ("2023-07-01", "2024-06-30", "Jul-2023 to Jun-2024"),
            ("2024-07-01", "2025-06-30", "Jul-2024 to Jun-2025"),
        ]
    """
    try:
        start = datetime.strptime(start_date[:10], "%Y-%m-%d")
        end = datetime.strptime(end_date[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return []

    if start > end:
        start, end = end, start

    periods = []
    current_end = end

    while current_end > start:
        period_start = current_end - relativedelta(months=12)

        # Don't go before the data start
        if period_start < start:
            period_start = start

        # Format period label
        label = f"{period_start.strftime('%b-%Y')} to {current_end.strftime('%b-%Y')}"

        periods.append((
            period_start.strftime("%Y-%m-%d"),
            current_end.strftime("%Y-%m-%d"),
            label,
        ))

        current_end = period_start - timedelta(days=1)

    return list(reversed(periods))  # Oldest first


def calculate_region_percentages(
    region_units: Dict[str, int],
    worldwide_total: int
) -> Dict[str, float]:
    """Calculate percentage of global sales for each region."""
    if worldwide_total == 0:
        return {r: 0.0 for r in region_units}

    return {
        region: round((units / worldwide_total) * 100, 1)
        for region, units in region_units.items()
    }


def identify_high_volume_regions_in_period(
    period_data: Dict[str, int],
    threshold_pct: float = 5.0
) -> List[str]:
    """Find regions with >5% of sales in current period (excluding standard named regions)."""
    standard_regions = {
        "EEA+TR+XI", "Australia", "Brazil", "Canada", "China", "Japan",
        "United Kingdom", "UK", "United States", "Rest of World", "Unknown / Unattributed",
        "Worldwide"
    }

    total = sum(period_data.values())
    if total == 0:
        return []

    high_volume = []
    for region, units in period_data.items():
        if region not in standard_regions:
            pct = (units / total) * 100
            if pct >= threshold_pct:
                high_volume.append(region)

    return sorted(high_volume)


def format_period_label_with_percentage(
    region_units: Dict[str, int],
    period_label: str = ""
) -> str:
    """Format period label with total units and percentage info."""
    total = sum(region_units.values())
    if period_label:
        return f"{period_label} ({total:,} units)"
    return f"{total:,} units"


# ── psur-complaint-tables skill: Table 7 / Table 8 helpers ──

# EEA + Turkey member-state list. XI (Northern Ireland) is added separately
# because it follows EU rules via the Windsor Framework but is reported under
# UK in some PSUR contexts. Per the skill spec, NI rolls up under EEA+TR+XI.
_EEA_TR = {
    "Austria", "Belgium", "Bulgaria", "Croatia", "Cyprus", "Czech Republic",
    "Denmark", "Estonia", "Finland", "France", "Germany", "Greece", "Hungary",
    "Iceland", "Ireland", "Italy", "Latvia", "Liechtenstein", "Lithuania",
    "Luxembourg", "Malta", "Netherlands", "Norway", "Poland", "Portugal",
    "Romania", "Slovakia", "Slovenia", "Spain", "Sweden", "Turkey",
}
_UK_GB = {"United Kingdom", "UK", "Great Britain", "GB", "England", "Scotland", "Wales"}
_NI = {"Northern Ireland", "XI"}


def classify_country_to_psur_region(country: str) -> str:
    """Map a country / region label to the standard PSUR region buckets.

    Returns one of: "EEA+TR+XI", "United Kingdom", "Australia", "Brazil",
    "Canada", "China", "Japan", "United States", or "Rest of World".
    """
    if not country:
        return "Rest of World"
    c = str(country).strip().title()
    if c in _EEA_TR or c in _NI:
        return "EEA+TR+XI"
    if c in _UK_GB:
        return "United Kingdom"
    if c in {"Australia"}:
        return "Australia"
    if c in {"Brazil"}:
        return "Brazil"
    if c in {"Canada"}:
        return "Canada"
    if c in {"China", "Hong Kong", "Macau", "Macao"}:
        return "China"
    if c in {"Japan"}:
        return "Japan"
    if c in {"United States", "Usa", "Us", "U.S.", "U.S.A.", "Puerto Rico"}:
        return "United States"
    return "Rest of World"


def build_complaint_region_breakdown(
    complaint_summaries: List[Dict[str, Any]]
) -> Dict[str, Dict[str, Dict[str, int]]]:
    """Cross-tabulate complaints by harm × medical-device-problem × region.

    Returns nested dict: {harm: {device_problem: {region_bucket: count}}}
    Region buckets: "EEA+TR+XI", "United Kingdom", "Worldwide".
    Worldwide always equals total across all regions.
    """
    out: Dict[str, Dict[str, Dict[str, int]]] = {}
    for s in complaint_summaries or []:
        harm = (s.get("harm") or "No Harm").strip() or "No Harm"
        mdp = (s.get("imdrf_code") or "Uncoded / Other").strip() or "Uncoded / Other"
        country = s.get("region") or s.get("country") or ""
        region_bucket = classify_country_to_psur_region(country)

        out.setdefault(harm, {}).setdefault(mdp, {"EEA+TR+XI": 0, "United Kingdom": 0, "Worldwide": 0})
        cell = out[harm][mdp]
        cell["Worldwide"] += 1
        if region_bucket == "EEA+TR+XI":
            cell["EEA+TR+XI"] += 1
        elif region_bucket == "United Kingdom":
            cell["United Kingdom"] += 1
    return out


def build_table8_rows(
    complaint_summaries: List[Dict[str, Any]],
    total_units: int,
    classify_occurrence_fn: Any,
    strip_imdrf_code_fn: Any = None,
) -> List[Dict[str, Any]]:
    """Build Table 8 rows: complaint counts by harm × device problem × region.

    Each row carries EEA+TR+XI, UK and Worldwide counts plus the worldwide
    occurrence-code classification (per MEDDEV 2.7/1 Rev.4).
    """
    breakdown = build_complaint_region_breakdown(complaint_summaries)
    rows: List[Dict[str, Any]] = []
    for harm, mdp_map in breakdown.items():
        harm_clean = strip_imdrf_code_fn(harm) if strip_imdrf_code_fn else harm
        for mdp, region_counts in mdp_map.items():
            mdp_clean = strip_imdrf_code_fn(mdp) if strip_imdrf_code_fn else mdp
            ww = region_counts.get("Worldwide", 0)
            rate = (ww / total_units) if total_units > 0 else 0.0
            row = {
                "harm": harm_clean,
                "medical_device_problem": mdp_clean,
                "eea_tr_xi_count": region_counts.get("EEA+TR+XI", 0),
                "uk_count": region_counts.get("United Kingdom", 0),
                "worldwide_count": ww,
                "worldwide_rate": round(rate, 8),
                "worldwide_percentage": round(rate * 100, 4),
            }
            row.update(classify_occurrence_fn(rate))
            rows.append(row)
    rows.sort(key=lambda r: -r["worldwide_count"])
    return rows

