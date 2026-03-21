"""Pre-calculate all statistics before LLM generation.

These calculations are deterministic and auditable.
The LLM receives these as FACTS to incorporate, not to calculate.

IMPORTANT: All rates use RAW denominators (complaints / units), NOT per-1000.
For disposables: denominator = units distributed.
For reusables: denominator = estimated procedures (episodes of use).
"""
import numpy as np
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from datetime import datetime
import json

from imdrf_coder import strip_imdrf_code
from config import RACT_CODES_PATH


# ---------------------------------------------------------------------------
# Load standard RACT occurrence codes (O1-O5)
# ---------------------------------------------------------------------------

def _load_ract_occurrence_codes() -> List[Dict[str, Any]]:
    """Load standard RACT occurrence codes from JSON."""
    try:
        with open(RACT_CODES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("occurrence_codes", [])
    except (FileNotFoundError, json.JSONDecodeError):
        return []


RACT_OCCURRENCE_CODES = _load_ract_occurrence_codes()


def classify_occurrence_code(rate: float) -> Dict[str, Any]:
    """Classify a complaint rate into an occurrence code (O1-O5).

    Args:
        rate: Raw complaint rate (e.g. 0.003 = 0.3%)

    Returns:
        Dict with code, label, description, max_expected_rate or empty dict.
    """
    for oc in RACT_OCCURRENCE_CODES:
        if rate <= oc["rate_range_max"]:
            return {
                "occurrence_code": oc["code"],
                "occurrence_label": oc["label"],
                "occurrence_description": oc["description"],
                "occurrence_max_expected_rate": oc["max_expected_rate"],
            }
    # Rate exceeds O5 — still classify as O5 (Frequent)
    if RACT_OCCURRENCE_CODES:
        last = RACT_OCCURRENCE_CODES[-1]
        return {
            "occurrence_code": last["code"],
            "occurrence_label": last["label"],
            "occurrence_description": last["description"],
            "occurrence_max_expected_rate": last["max_expected_rate"],
        }
    return {}


@dataclass
class ComplaintRateResult:
    """Single complaint rate calculation."""
    category: str
    complaint_count: int
    denominator: int
    denominator_type: str  # "units_distributed" or "procedures"
    rate: float  # raw rate (complaints / denominator)
    percentage: float  # rate * 100


@dataclass
class TrendAnalysis:
    """UCL and trend status using raw rates."""
    mean: float
    std_dev: float
    ucl_3sigma: float
    lcl_3sigma: float
    current_rate: float
    status: str  # STABLE, INCREASING, DECREASING, ALERT
    western_electric_violations: List[str]
    data_points: int
    monthly_rates: List[float] = field(default_factory=list)
    monthly_labels: List[str] = field(default_factory=list)
    # Percentage fields (raw * 100) for human-readable display
    mean_pct: float = 0.0
    std_dev_pct: float = 0.0
    ucl_3sigma_pct: float = 0.0
    lcl_3sigma_pct: float = 0.0
    current_rate_pct: float = 0.0
    monthly_rates_pct: List[float] = field(default_factory=list)


@dataclass
class PSURStatistics:
    """Complete statistics package for PSUR generation."""
    surveillance_period: Dict[str, str]

    # Denominator info
    denominator_type: str  # "units_distributed" or "procedures"
    denominator_description: str

    # Sales
    total_units_sold: int
    units_by_region: Dict[str, int]
    units_by_month: Dict[str, int]
    units_by_product: Dict[str, int]

    # Complaints
    total_complaints: int
    complaints_by_imdrf: Dict[str, int]
    complaints_by_harm: Dict[str, int]
    complaints_by_region: Dict[str, int]
    complaints_by_month: Dict[str, int]

    # Cross-tabulations (pre-computed to prevent LLM fabrication)
    harm_by_imdrf: Dict[str, Dict[str, int]]  # harm → imdrf → count
    serious_by_region_imdrf: Dict[str, Any]  # "region|imdrf" → {count, complaint_numbers}
    serious_incidents_detail: List[Dict[str, Any]]  # Full detail of each serious incident

    # Regional aggregates
    eea_units: int  # Sum of EU/EEA member state units
    eea_countries: List[str]  # Which countries are in EEA aggregate
    uk_units: int  # Sum of Great Britain (England, Scotland, Wales) units
    uk_complaints: int  # Complaints originating from UK
    uk_market_detected: bool  # Whether UK sales exist (triggers UK MDR context)
    section_c_region_rows: List[Dict[str, Any]]  # Pre-computed rows for Section C Table 1

    # Serious incidents
    serious_incident_count: int
    serious_incidents_by_imdrf: Dict[str, int]

    # Rates (RAW — not per 1000)
    overall_complaint_rate: float  # complaints / denominator
    overall_complaint_percentage: float  # rate * 100
    overall_rate_display: str  # e.g. "47 / 15,340 units (0.003064)"
    serious_incident_rate: float
    serious_incident_rate_display: str
    rates_by_imdrf: List[ComplaintRateResult]
    rates_by_harm: List[ComplaintRateResult]

    # Trend
    trend_analysis: TrendAnalysis

    # Comparison (if previous data available)
    yoy_rate_change: Optional[float]
    yoy_volume_change: Optional[float]

    # Pre-computed Table 7 rows (harm × imdrf × count × rate)
    table7_rows: List[Dict[str, Any]] = field(default_factory=list)

    # Per-region complaint rates for Table 2
    rates_by_region: List[Dict[str, Any]] = field(default_factory=list)

    # Countries with >5% of global sales (need own row in Table 1)
    countries_above_5pct: List[str] = field(default_factory=list)

    # Data availability flags
    has_previous_period_data: bool = False
    complaint_number_format: str = ""  # e.g. "CMP-2024-0001"


def calculate_rate(complaints: int, denominator: int) -> float:
    """Raw complaint rate (complaints / denominator)."""
    if denominator == 0:
        return 0.0
    return complaints / denominator


def _generate_month_range(start_date: str, end_date: str) -> List[str]:
    """Generate all YYYY-MM strings between start and end dates (inclusive)."""
    try:
        start = datetime.strptime(start_date[:7], "%Y-%m")
        end = datetime.strptime(end_date[:7], "%Y-%m")
    except (ValueError, TypeError):
        return []
    months = []
    current = start
    while current <= end:
        months.append(current.strftime("%Y-%m"))
        # Move to next month
        if current.month == 12:
            current = current.replace(year=current.year + 1, month=1)
        else:
            current = current.replace(month=current.month + 1)
    return months


def format_rate_display(complaints: int, denominator: int, denom_type: str) -> str:
    """Format rate as human-readable string with raw numbers."""
    if denominator == 0:
        return f"{complaints} / 0 {denom_type} (N/A)"
    rate = complaints / denominator
    return f"{complaints} / {denominator:,} {denom_type} ({rate:.6f})"


def calculate_ucl(monthly_rates: List[float], monthly_labels: List[str] = None) -> TrendAnalysis:
    """Calculate UCL and check Western Electric rules using raw rates."""
    if monthly_labels is None:
        monthly_labels = [f"M{i+1}" for i in range(len(monthly_rates))]

    if len(monthly_rates) < 3:
        cur = monthly_rates[-1] if monthly_rates else 0
        return TrendAnalysis(
            mean=0, std_dev=0, ucl_3sigma=0, lcl_3sigma=0,
            current_rate=cur,
            status="INSUFFICIENT_DATA", western_electric_violations=[],
            data_points=len(monthly_rates),
            monthly_rates=monthly_rates,
            monthly_labels=monthly_labels,
            mean_pct=0.0, std_dev_pct=0.0,
            ucl_3sigma_pct=0.0, lcl_3sigma_pct=0.0,
            current_rate_pct=round(cur * 100, 2),
            monthly_rates_pct=[round(r * 100, 2) for r in monthly_rates],
        )

    mean = float(np.mean(monthly_rates))
    std = float(np.std(monthly_rates, ddof=1))
    ucl = mean + (3 * std)
    lcl = max(0, mean - (3 * std))
    current = monthly_rates[-1]

    violations = []
    if std > 0:
        # Rule 1: 1 point > 3 sigma
        for i, r in enumerate(monthly_rates):
            if abs(r - mean) > 3 * std:
                violations.append(f"Rule 1: {monthly_labels[i]} exceeds 3-sigma")

        # Rule 2: 2 of 3 consecutive > 2 sigma same side
        for i in range(len(monthly_rates) - 2):
            window = monthly_rates[i:i+3]
            above = sum(1 for r in window if r > mean + 2*std)
            below = sum(1 for r in window if r < mean - 2*std)
            if above >= 2:
                violations.append(f"Rule 2: {monthly_labels[i]}-{monthly_labels[i+2]}, 2/3 above 2-sigma")
            if below >= 2:
                violations.append(f"Rule 2: {monthly_labels[i]}-{monthly_labels[i+2]}, 2/3 below 2-sigma")

        # Rule 3: 4 of 5 consecutive > 1 sigma same side
        for i in range(len(monthly_rates) - 4):
            window = monthly_rates[i:i+5]
            above = sum(1 for r in window if r > mean + std)
            below = sum(1 for r in window if r < mean - std)
            if above >= 4:
                violations.append(f"Rule 3: {monthly_labels[i]}-{monthly_labels[i+4]}, 4/5 above 1-sigma")
            if below >= 4:
                violations.append(f"Rule 3: {monthly_labels[i]}-{monthly_labels[i+4]}, 4/5 below 1-sigma")

        # Rule 4: 8 consecutive same side — report only the longest qualifying run
        # to avoid overlapping triggers that confuse LLM interpretation
        above_run_start = None
        below_run_start = None
        longest_above = None  # (start, end)
        longest_below = None
        for i in range(len(monthly_rates)):
            if monthly_rates[i] > mean:
                if above_run_start is None:
                    above_run_start = i
                if i - above_run_start + 1 >= 8:
                    if longest_above is None or (i - above_run_start) > (longest_above[1] - longest_above[0]):
                        longest_above = (above_run_start, i)
            else:
                above_run_start = None

            if monthly_rates[i] < mean:
                if below_run_start is None:
                    below_run_start = i
                if i - below_run_start + 1 >= 8:
                    if longest_below is None or (i - below_run_start) > (longest_below[1] - longest_below[0]):
                        longest_below = (below_run_start, i)
            else:
                below_run_start = None

        if longest_above:
            s, e = longest_above
            run_len = e - s + 1
            violations.append(
                f"Rule 4: Run of {run_len} consecutive months above mean "
                f"({monthly_labels[s]} to {monthly_labels[e]})"
            )
        if longest_below:
            s, e = longest_below
            run_len = e - s + 1
            violations.append(
                f"Rule 4: Run of {run_len} consecutive months below mean "
                f"({monthly_labels[s]} to {monthly_labels[e]})"
            )

    # Determine status
    if violations:
        status = "ALERT"
    elif current > ucl:
        status = "ABOVE_UCL"
    elif len(monthly_rates) >= 3:
        recent = monthly_rates[-3:]
        if all(recent[i] > recent[i-1] for i in range(1, len(recent))):
            status = "INCREASING"
        elif all(recent[i] < recent[i-1] for i in range(1, len(recent))):
            status = "DECREASING"
        else:
            status = "STABLE"
    else:
        status = "STABLE"

    return TrendAnalysis(
        mean=round(mean, 8),
        std_dev=round(std, 8),
        ucl_3sigma=round(ucl, 8),
        lcl_3sigma=round(lcl, 8),
        current_rate=round(current, 8),
        status=status,
        western_electric_violations=violations,
        data_points=len(monthly_rates),
        monthly_rates=[round(r, 8) for r in monthly_rates],
        monthly_labels=monthly_labels,
        mean_pct=round(mean * 100, 2),
        std_dev_pct=round(std * 100, 2),
        ucl_3sigma_pct=round(ucl * 100, 2),
        lcl_3sigma_pct=round(lcl * 100, 2),
        current_rate_pct=round(current * 100, 2),
        monthly_rates_pct=[round(r * 100, 2) for r in monthly_rates],
    )


def compute_psur_statistics(
    sales_data: Dict[str, Any],
    complaints_data: Dict[str, Any],
    surveillance_period: Dict[str, str],
    previous_stats: Optional[Dict] = None,
    is_reusable: bool = False,
    ract_data: Optional[Dict[str, Any]] = None
) -> PSURStatistics:
    """Compute all statistics for PSUR generation.

    Args:
        sales_data: Parsed sales data
        complaints_data: Parsed complaints data
        surveillance_period: Start/end dates
        previous_stats: Previous period stats for YoY
        is_reusable: If True, denominator = procedures; else = units distributed
        ract_data: Parsed RACT data (for max expected rates linkage to Table 7)
    """
    total_units = sales_data.get("total_units", 0)
    total_complaints = complaints_data.get("total_complaints", 0)

    denom_type = "procedures" if is_reusable else "units"
    denom_desc = "estimated episodes of use" if is_reusable else "units distributed within the reporting period"

    # Overall rate (RAW)
    overall_rate = calculate_rate(total_complaints, total_units)
    overall_pct = round(overall_rate * 100, 4)
    overall_display = format_rate_display(total_complaints, total_units, denom_type)

    # Serious incidents
    serious = complaints_data.get("serious_incidents", [])
    serious_count = len(serious)
    serious_rate = calculate_rate(serious_count, total_units)
    serious_display = format_rate_display(serious_count, total_units, denom_type)

    serious_by_imdrf = {}
    for inc in serious:
        code = inc.get("imdrf_code", "Unknown")
        serious_by_imdrf[code] = serious_by_imdrf.get(code, 0) + 1

    # Rates by IMDRF code
    rates_by_imdrf = []
    for code, count in complaints_data.get("by_imdrf_code", {}).items():
        rate = calculate_rate(count, total_units)
        rates_by_imdrf.append(ComplaintRateResult(
            category=code,
            complaint_count=count,
            denominator=total_units,
            denominator_type=denom_type,
            rate=round(rate, 8),
            percentage=round(rate * 100, 4)
        ))
    rates_by_imdrf.sort(key=lambda x: -x.complaint_count)

    # Pre-aggregate for Table 7: all categories must appear (no truncation)
    # Also create a harm x imdrf cross-tab with proper terms for Section F
    harm_by_imdrf = complaints_data.get("harm_by_imdrf", {})
    table7_rows = []
    for rate_result in rates_by_imdrf:
        # Find the harm categories for this IMDRF code
        harms_for_code = {}
        for harm_cat, imdrf_counts in harm_by_imdrf.items():
            code_count = imdrf_counts.get(rate_result.category, 0)
            if code_count > 0:
                harms_for_code[strip_imdrf_code(harm_cat)] = code_count
        if not harms_for_code:
            harms_for_code = {"No Harm": rate_result.complaint_count}
        for harm_cat, count in harms_for_code.items():
            row_rate = round(calculate_rate(count, total_units), 8)
            row_pct = round(calculate_rate(count, total_units) * 100, 4)
            row = {
                "harm": harm_cat,
                "medical_device_problem": strip_imdrf_code(rate_result.category),
                "complaint_count": count,
                "complaint_rate": row_rate,
                "complaint_percentage": row_pct,
            }
            # Auto-classify into occurrence code (O1-O5)
            oc = classify_occurrence_code(row_rate)
            row.update(oc)
            table7_rows.append(row)
    table7_rows.sort(key=lambda x: -x["complaint_count"])

    # ── Link RACT max expected rates to Table 7 rows ──
    ract_max_rates = {}
    if ract_data and isinstance(ract_data, dict):
        ract_max_rates = ract_data.get("max_expected_rates", {})
    for row in table7_rows:
        mdp = row.get("medical_device_problem", "")
        # Try exact match first, then substring match
        matched_rate = ract_max_rates.get(mdp)
        if matched_rate is None:
            for ract_key, ract_rate in ract_max_rates.items():
                if ract_key and mdp and (ract_key.lower() in mdp.lower() or mdp.lower() in ract_key.lower()):
                    matched_rate = ract_rate
                    break
        row["ract_max_expected_rate"] = matched_rate
        if matched_rate is not None and matched_rate > 0:
            actual_rate = row.get("complaint_rate", 0)
            row["rate_vs_ract"] = "WITHIN" if actual_rate <= matched_rate else "EXCEEDS"
            row["ract_ratio"] = round(actual_rate / matched_rate, 2) if matched_rate > 0 else None
        else:
            row["rate_vs_ract"] = "NO_RACT_DATA"
            row["ract_ratio"] = None

    # Rates by harm category
    rates_by_harm = []
    for harm, count in complaints_data.get("by_harm_category", {}).items():
        rate = calculate_rate(count, total_units)
        rates_by_harm.append(ComplaintRateResult(
            category=harm,
            complaint_count=count,
            denominator=total_units,
            denominator_type=denom_type,
            rate=round(rate, 8),
            percentage=round(rate * 100, 4)
        ))
    rates_by_harm.sort(key=lambda x: -x.complaint_count)

    # Monthly rates for trend analysis — zero-fill missing months
    monthly_complaints = complaints_data.get("by_month", {})
    monthly_sales = sales_data.get("by_month", {})

    # Generate all months in the surveillance period
    all_months = _generate_month_range(
        surveillance_period.get("start_date", ""),
        surveillance_period.get("end_date", "")
    )
    # Fall back to union of data months if period parsing fails
    if not all_months:
        all_months = sorted(set(monthly_complaints.keys()) | set(monthly_sales.keys()))

    monthly_rates = []
    monthly_labels = []
    # Also build zero-filled month dicts
    units_by_month_filled = {}
    complaints_by_month_filled = {}
    for month in all_months:
        c = monthly_complaints.get(month, 0)
        s = monthly_sales.get(month, 0)
        units_by_month_filled[month] = s
        complaints_by_month_filled[month] = c
        if s > 0:
            monthly_rates.append(calculate_rate(c, s))
            monthly_labels.append(month)
        else:
            # Zero sales month — rate is 0 if no complaints either
            monthly_rates.append(0.0)
            monthly_labels.append(month)

    trend = calculate_ucl(monthly_rates, monthly_labels)

    # Cross-tabulations from parsed complaint data
    serious_by_region_imdrf = complaints_data.get("serious_by_region_imdrf", {})
    complaint_number_format = complaints_data.get("complaint_number_format", "")

    # EEA regional aggregate
    EEA_COUNTRIES = [
        "Austria", "Belgium", "Bulgaria", "Croatia", "Cyprus", "Czech Republic",
        "Denmark", "Estonia", "Finland", "France", "Germany", "Greece", "Hungary",
        "Iceland", "Ireland", "Italy", "Latvia", "Liechtenstein", "Lithuania",
        "Luxembourg", "Malta", "Netherlands", "Norway", "Poland", "Portugal",
        "Romania", "Slovakia", "Slovenia", "Spain", "Sweden", "Turkey"
    ]

    # UK / Great Britain — post-Brexit, UK is no longer in EEA.
    # Great Britain = England + Scotland + Wales (UKCA regime).
    # Northern Ireland follows EU rules via the Windsor Framework (XI protocol).
    UK_GB_NAMES = [
        "United Kingdom", "UK", "Great Britain", "GB",
        "England", "Scotland", "Wales",
    ]

    # Use country-level data if available (preferred), else fall back to region-level
    units_by_country = sales_data.get("by_country", {})
    units_by_region = sales_data.get("by_region", {})
    eea_units = 0
    eea_countries_found = []
    uk_units = 0
    uk_complaints_total = 0
    if units_by_country:
        for country, units in units_by_country.items():
            if country in EEA_COUNTRIES:
                eea_units += units
                eea_countries_found.append(country)
            elif country in UK_GB_NAMES:
                uk_units += units
    else:
        # Fallback: check region-level
        for region_name, units in units_by_region.items():
            if region_name in EEA_COUNTRIES:
                eea_units += units
                eea_countries_found.append(region_name)
            elif region_name in UK_GB_NAMES:
                uk_units += units

    # Also count UK complaints from complaint region data
    complaints_by_region_raw = complaints_data.get("by_region", {})
    for region_name, count in complaints_by_region_raw.items():
        if region_name in UK_GB_NAMES:
            uk_complaints_total += count

    uk_market_detected = uk_units > 0

    # Pre-compute Section C region rows so the LLM doesn't guess
    # EEA+TR+XI includes Turkey and Northern Ireland (XI = NI protocol)
    xi_units = units_by_country.get("Northern Ireland", 0)
    eea_tr_xi_units = eea_units + xi_units  # Turkey already in EEA_COUNTRIES list
    rest_of_world_units = total_units - eea_tr_xi_units - uk_units
    section_c_region_rows = [
        {"region": "EEA+TR+XI", "units": eea_tr_xi_units},
    ]
    # Insert UK as its own row when UK sales exist (UK MDR requires separate reporting)
    if uk_market_detected:
        section_c_region_rows.append({"region": "UK", "units": uk_units})
    section_c_region_rows.extend([
        {"region": "Rest of World", "units": rest_of_world_units},
        {"region": "Worldwide", "units": total_units},
    ])

    # ── Per-region complaint rates (for Table 2 / Section D) ──
    complaints_by_region = complaints_data.get("by_region", {})
    region_rate_list = []
    # Merge keys from both sales and complaints to cover all regions
    all_regions = set(units_by_region.keys()) | set(complaints_by_region.keys())
    for region in sorted(all_regions):
        region_units = units_by_region.get(region, 0)
        region_complaints = complaints_by_region.get(region, 0)
        rate = calculate_rate(region_complaints, region_units)
        region_rate_list.append({
            "region": region,
            "units_distributed": region_units,
            "complaints": region_complaints,
            "complaint_rate": round(rate, 8),
            "complaint_percentage": round(rate * 100, 2),
            "rate_display": format_rate_display(region_complaints, region_units, denom_type),
        })
    region_rate_list.sort(key=lambda x: -x["complaints"])

    # ── Countries with >5% of global sales (need own row in Table 1) ──
    countries_5pct = []
    if total_units > 0 and units_by_country:
        threshold = total_units * 0.05
        for country, units in sorted(units_by_country.items(), key=lambda x: -x[1]):
            if units >= threshold:
                countries_5pct.append(country)

    # YoY comparison
    yoy_rate = None
    yoy_volume = None
    has_previous = False
    if previous_stats:
        has_previous = True
        prev_rate = previous_stats.get("overall_complaint_rate", 0)
        prev_units = previous_stats.get("total_units_sold", 0)
        if prev_rate > 0:
            yoy_rate = round(((overall_rate - prev_rate) / prev_rate) * 100, 1)
        if prev_units > 0:
            yoy_volume = round(((total_units - prev_units) / prev_units) * 100, 1)

    return PSURStatistics(
        surveillance_period=surveillance_period,
        denominator_type=denom_type,
        denominator_description=denom_desc,
        total_units_sold=total_units,
        units_by_region=units_by_region,
        units_by_month=units_by_month_filled,
        units_by_product=sales_data.get("by_product", {}),
        total_complaints=total_complaints,
        complaints_by_imdrf=complaints_data.get("by_imdrf_code", {}),
        complaints_by_harm=complaints_data.get("by_harm_category", {}),
        complaints_by_region=complaints_data.get("by_region", {}),
        complaints_by_month=complaints_by_month_filled,
        harm_by_imdrf=harm_by_imdrf,
        serious_by_region_imdrf=serious_by_region_imdrf,
        serious_incidents_detail=serious,
        eea_units=eea_units,
        eea_countries=eea_countries_found,
        uk_units=uk_units,
        uk_complaints=uk_complaints_total,
        uk_market_detected=uk_market_detected,
        section_c_region_rows=section_c_region_rows,
        serious_incident_count=serious_count,
        serious_incidents_by_imdrf=serious_by_imdrf,
        overall_complaint_rate=round(overall_rate, 8),
        overall_complaint_percentage=overall_pct,
        overall_rate_display=overall_display,
        serious_incident_rate=round(serious_rate, 8),
        serious_incident_rate_display=serious_display,
        rates_by_imdrf=rates_by_imdrf,
        rates_by_harm=rates_by_harm,
        trend_analysis=trend,
        yoy_rate_change=yoy_rate,
        yoy_volume_change=yoy_volume,
        table7_rows=table7_rows,
        rates_by_region=region_rate_list,
        countries_above_5pct=countries_5pct,
        has_previous_period_data=has_previous,
        complaint_number_format=complaint_number_format
    )
