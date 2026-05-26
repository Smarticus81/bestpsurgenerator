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
from statistics_tables import (
    calculate_region_percentages,
    determine_12month_periods_from_dates,
    build_table8_rows,
)


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

    # Pre-computed Table 8 rows (harm × device-problem × {EEA+TR+XI, UK, Worldwide})
    # Built per psur-complaint-tables skill spec; carries occurrence code (O1-O5)
    # from MEDDEV 2.7/1 Rev.4 keyed off the worldwide rate.
    table8_rows: List[Dict[str, Any]] = field(default_factory=list)

    # EU/UK vs FDA MDR distinction
    eu_uk_serious_incident_count: int = 0  # Art. 2(65) qualifying events only
    fda_mdr_count: int = 0  # FDA MDRs (may not be EU serious incidents)

    # Per-region complaint rates for Table 2
    rates_by_region: List[Dict[str, Any]] = field(default_factory=list)

    # Countries with >5% of global sales (need own row in Table 1)
    countries_above_5pct: List[str] = field(default_factory=list)

    # Data availability flags
    has_previous_period_data: bool = False
    complaint_number_format: str = ""  # e.g. "CMP-2024-0001"

    # Headers for the 3 "Preceding 12-Month" columns in Section C Table 1
    section_c_period_labels: List[str] = field(default_factory=list)

    # ── Single-use vs reusable bifurcation (mixed portfolios) ──
    # Populated when product_classification is supplied. For pure-class
    # devices these mirror the overall totals in the relevant bucket.
    reusable_units: int = 0
    single_use_units: int = 0
    unknown_class_units: int = 0
    reusable_complaints: int = 0
    single_use_complaints: int = 0
    unknown_class_complaints: int = 0
    reusable_rate: float = 0.0
    single_use_rate: float = 0.0
    reusable_rate_pct: float = 0.0
    single_use_rate_pct: float = 0.0
    reusable_rate_display: str = ""
    single_use_rate_display: str = ""
    portfolio_is_mixed: bool = False  # True when both classes have units > 0

    # ── UK breakout (always reported separately from EEA) ──
    uk_rate: float = 0.0
    uk_rate_pct: float = 0.0
    uk_rate_display: str = ""
    uk_serious_incidents: int = 0

    # ── Quarterly cumulative trend (preferred over monthly p-chart) ──
    # Monthly trend (trend_analysis above) is retained for backward compat
    # but the quarterly view is what MDCG 2022-21 §III.5 expects for
    # multi-year service-life devices.
    quarterly_trend: Optional[Dict[str, Any]] = None

    # ── psur-trend-charts skill inputs ──
    # harm_by_month: {YYYY-MM: {harm_label: count}} — for stacked harm chart.
    # per_period_aggregates: list of {label, complaints, units, rate, occurrence_*}
    # for per-period bar variants.
    harm_by_month: Dict[str, Dict[str, int]] = field(default_factory=dict)
    per_period_aggregates: List[Dict[str, Any]] = field(default_factory=list)

    # ── Data-quality audit signals ──
    negative_unit_rows_excluded: int = 0
    negative_units_total: int = 0

    # ── Previous PSUR breakdown (for YoY narrative) ──
    previous_period_summary: Optional[Dict[str, Any]] = None


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
    ract_data: Optional[Dict[str, Any]] = None,
    product_classification: Optional[Dict[str, Dict[str, str]]] = None,
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

    # ── Distinguish EU/UK serious incidents from FDA MDRs ──
    # EU MDR Art. 2(65) criteria: death, serious deterioration in health
    # (hospitalization, life-threatening, permanent impairment, intervention needed),
    # or serious public health threat.
    # FDA MDRs include "serious injury" that may not meet EU criteria (e.g. laceration
    # resolving with local wound care).
    eu_uk_si_count = 0
    fda_mdr_count = 0
    for inc in serious:
        # Check for EU/UK qualification markers
        is_eu_si = inc.get("eu_serious_incident", False)
        is_eu_si = is_eu_si or inc.get("meets_art_2_65", False)
        # If harm severity indicates death, hospitalization, etc.
        harm = str(inc.get("harm", "") or inc.get("imdrf_harm", "") or "").lower()
        if is_eu_si or any(k in harm for k in ("death", "hospitali", "life-threaten",
                                                "permanent", "public health")):
            eu_uk_si_count += 1
        else:
            fda_mdr_count += 1
    # If no explicit distinction available, assume ALL are FDA MDRs (conservative)
    # unless the complaint parser explicitly tagged them
    if eu_uk_si_count == 0 and serious_count > 0:
        # Check if any have explicit FDA MDR markers
        has_fda_markers = any(
            inc.get("mdr_issued") or inc.get("fda_mdr") for inc in serious
        )
        if has_fda_markers:
            fda_mdr_count = serious_count
            eu_uk_si_count = 0

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
                harm_label = strip_imdrf_code(harm_cat)
                harm_low = harm_label.strip().lower()
                if (
                    harm_low in {"no harm", "no health consequence", "no health consequence or impact"}
                    or harm_low.startswith("no harm")
                    or "near miss" in harm_low
                ):
                    harm_label = "No Health Consequence or Impact"
                harms_for_code[harm_label] = harms_for_code.get(harm_label, 0) + code_count
        if not harms_for_code:
            harms_for_code = {"No Health Consequence or Impact": rate_result.complaint_count}
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

    # ── Reconcile Table 7 with total_complaints ───────────────────
    # Some complaints may lack IMDRF coding (auto-coding can fail or be
    # skipped); they exist in total_complaints but not in by_imdrf_code.
    # Add an explicit "Uncoded" bucket so the displayed Grand Total matches
    # total_complaints exactly. Without this, auditors see a mismatch.
    coded_total = sum(r["complaint_count"] for r in table7_rows)
    uncoded = max(0, total_complaints - coded_total)
    if uncoded > 0:
        u_rate = round(calculate_rate(uncoded, total_units), 8)
        u_pct = round(calculate_rate(uncoded, total_units) * 100, 4)
        uncoded_row = {
            "harm": "No Health Consequence or Impact",
            "medical_device_problem": "Uncoded / Other",
            "complaint_count": uncoded,
            "complaint_rate": u_rate,
            "complaint_percentage": u_pct,
            "ract_max_expected_rate": None,
            "rate_vs_ract": "NO_RACT_DATA",
            "ract_ratio": None,
        }
        oc = classify_occurrence_code(u_rate)
        uncoded_row.update(oc)
        table7_rows.append(uncoded_row)

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

    def _canonical_harm_label(value: Any) -> str:
        label = strip_imdrf_code(value) if value else "No Harm"
        if not label:
            label = "No Harm"
        low = str(label).strip().lower()
        if (
            low in {"no harm", "no health consequence", "no health consequence or impact"}
            or low.startswith("no harm")
            or "near miss" in low
        ):
            return "No Health Consequence or Impact"
        return str(label).strip()

    canonical_complaints_by_harm: Dict[str, int] = {}
    for harm, count in complaints_data.get("by_harm_category", {}).items():
        key = _canonical_harm_label(harm)
        canonical_complaints_by_harm[key] = canonical_complaints_by_harm.get(key, 0) + int(count or 0)

    # Rates by harm category
    rates_by_harm = []
    for harm, count in canonical_complaints_by_harm.items():
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
    # Northern Ireland (XI) follows EU rules via the Windsor Framework but
    # for PSUR reporting we count it under UK so the "UK MDR Part 4A"
    # surface area is complete. (NB: kept out of EEA totals.)
    UK_GB_NAMES = [
        "United Kingdom", "UK", "Great Britain", "GB",
        "England", "Scotland", "Wales",
        "Northern Ireland", "XI",
    ]

    # Region label normalizer — collapses common spelling variants so the
    # UK breakout, EEA aggregation and rates_by_region inner-join all see
    # the same canonical key.
    def _canon_region(s: Optional[str]) -> str:
        if not s:
            return "Unknown"
        v = str(s).strip()
        for canon in UK_GB_NAMES:
            if v.lower() == canon.lower():
                return "United Kingdom"
        if v.lower() in ("usa", "u.s.", "u.s.a.", "united states of america"):
            return "United States"
        return v.title()

    # Use country-level data if available (preferred), else fall back to region-level
    units_by_country = sales_data.get("by_country", {})
    units_by_region = sales_data.get("by_region", {})

    # Macro-region label → canonical bucket so sales feeds that only carry
    # coarse labels (Europe, NorthAmerica, APAC, …) still aggregate correctly
    # instead of dumping everything into "Rest of World".
    _MACRO_REGION_MAP = {
        "europe": "EEA",
        "eu": "EEA",
        "eea": "EEA",
        "emea": "EEA",
        "european union": "EEA",
        "north america": "USA",
        "northamerica": "USA",
        "na": "USA",
        "namer": "USA",
        "united kingdom": "UK",
        "uk": "UK",
        "great britain": "UK",
        "gb": "UK",
    }

    def _macro_bucket(label: str) -> Optional[str]:
        return _MACRO_REGION_MAP.get(str(label or "").strip().lower())

    eea_units = 0
    eea_countries_found = []
    uk_units = 0
    uk_complaints_total = 0
    # Macro-region carve-out so the bookkeeping below can fold these into the
    # correct fixed-region rows even when the input only carries coarse labels.
    macro_usa_units = 0
    macro_eea_units = 0
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
            else:
                bucket = _macro_bucket(region_name)
                if bucket == "EEA":
                    macro_eea_units += int(units or 0)
                elif bucket == "USA":
                    macro_usa_units += int(units or 0)
                elif bucket == "UK":
                    uk_units += int(units or 0)

    eea_units += macro_eea_units
    if macro_eea_units > 0 and "EEA (macro)" not in eea_countries_found:
        eea_countries_found.append("EEA (macro)")

    # Also count UK complaints from complaint region data
    complaints_by_region_raw = complaints_data.get("by_region", {})
    uk_serious_count = 0
    for region_name, count in complaints_by_region_raw.items():
        if region_name in UK_GB_NAMES:
            uk_complaints_total += count
    for inc in serious:
        if inc.get("region") in UK_GB_NAMES:
            uk_serious_count += 1

    uk_market_detected = uk_units > 0

    # Pre-compute Section C region rows so the LLM doesn't guess
    # EEA+TR+XI includes Turkey and Northern Ireland (XI = NI protocol)
    xi_units = units_by_country.get("Northern Ireland", 0)
    eea_tr_xi_units = eea_units + xi_units  # Turkey already in EEA_COUNTRIES list

    # Per-country buckets matching the FormQAR-054 template's fixed region slots.
    # Country names in the DB may use ISO codes or variants; we accept several
    # common spellings so totals reconcile to Worldwide.
    _COUNTRY_ALIASES = {
        "Australia": ["Australia", "AU", "AUS"],
        "Brazil": ["Brazil", "BR", "BRA", "Brasil"],
        "Canada": ["Canada", "CA", "CAN"],
        "China": ["China", "CN", "CHN", "People's Republic of China", "PRC"],
        "Japan": ["Japan", "JP", "JPN"],
        "United States": [
            "United States", "USA", "US", "U.S.", "U.S.A.",
            "United States of America",
        ],
    }

    def _sum_country_aliases(aliases: list) -> int:
        return sum(int(units_by_country.get(a, 0) or 0) for a in aliases)

    named_country_buckets: Dict[str, int] = {}
    accounted_country_units = 0
    for label, aliases in _COUNTRY_ALIASES.items():
        u = _sum_country_aliases(aliases)
        named_country_buckets[label] = u
        accounted_country_units += u

    # Fold macro "NorthAmerica" totals into the United States bucket so the
    # fixed-region template row reflects actual distribution rather than
    # punting it to Rest of World.
    if macro_usa_units > 0:
        named_country_buckets["United States"] = (
            named_country_buckets.get("United States", 0) + macro_usa_units
        )
        accounted_country_units += macro_usa_units

    # Rest of World = everything not in EEA+TR+XI, UK, named country buckets,
    # or unattributed (unknown country). Unknown units are surfaced as their
    # own audit-visible row instead of being silently folded into RoW.
    units_unknown_country = int(sales_data.get("units_unknown_country", 0) or 0)
    rest_of_world_units = max(
        0,
        total_units
        - eea_tr_xi_units
        - uk_units
        - accounted_country_units
        - units_unknown_country,
    )

    section_c_region_rows = [
        {"region": "EEA+TR+XI", "units": eea_tr_xi_units},
        {"region": "Australia", "units": named_country_buckets["Australia"]},
        {"region": "Brazil", "units": named_country_buckets["Brazil"]},
        {"region": "Canada", "units": named_country_buckets["Canada"]},
        {"region": "China", "units": named_country_buckets["China"]},
        {"region": "Japan", "units": named_country_buckets["Japan"]},
    ]
    if uk_market_detected or uk_units > 0:
        section_c_region_rows.append({"region": "UK", "units": uk_units})
    section_c_region_rows.append(
        {"region": "United States", "units": named_country_buckets["United States"]}
    )
    section_c_region_rows.append(
        {"region": "Rest of World", "units": rest_of_world_units}
    )
    if units_unknown_country > 0:
        section_c_region_rows.append(
            {"region": "Unknown / Unattributed", "units": units_unknown_country}
        )
    section_c_region_rows.append(
        {"region": "Worldwide", "units": total_units}
    )

    # ── Attach historical 12-month totals to each region row ──
    # Renderer fills the 3 "Preceding 12-Month" columns from these.
    historical_periods = sales_data.get("historical_periods", []) or []

    def _bucketize_period(period_by_country: Dict[str, int],
                          period_total: int,
                          period_unknown: int,
                          period_by_region: Optional[Dict[str, int]] = None) -> Dict[str, int]:
        """Return units per template region label for one historical window."""
        eea_xi = sum(int(period_by_country.get(c, 0) or 0) for c in EEA_COUNTRIES)
        eea_xi += int(period_by_country.get("Northern Ireland", 0) or 0)
        uk = sum(int(period_by_country.get(c, 0) or 0) for c in UK_GB_NAMES)
        named = {
            label: sum(int(period_by_country.get(a, 0) or 0) for a in aliases)
            for label, aliases in _COUNTRY_ALIASES.items()
        }
        for label, units in (period_by_region or {}).items():
            bucket = _macro_bucket(label)
            u = int(units or 0)
            if bucket == "EEA":
                eea_xi += u
            elif bucket == "USA":
                named["United States"] = named.get("United States", 0) + u
            elif bucket == "UK":
                uk += u
        accounted = eea_xi + uk + sum(named.values()) + int(period_unknown or 0)
        row = {
            "EEA+TR+XI": eea_xi,
            "Australia": named.get("Australia", 0),
            "Brazil": named.get("Brazil", 0),
            "Canada": named.get("Canada", 0),
            "China": named.get("China", 0),
            "Japan": named.get("Japan", 0),
            "UK": uk,
            "United States": named.get("United States", 0),
            "Rest of World": max(0, int(period_total or 0) - accounted),
            "Unknown / Unattributed": int(period_unknown or 0),
            "Worldwide": int(period_total or 0),
        }
        return row

    period_buckets = []  # list of dicts keyed by region label, ordered P-1, P-2, P-3
    period_labels = []
    for period in historical_periods[:3]:
        period_buckets.append(_bucketize_period(
            period.get("by_country", {}) or {},
            period.get("total_units", 0) or 0,
            period.get("units_unknown_country", 0) or 0,
            period.get("by_region", {}) or {},
        ))
        period_labels.append(period.get("label", ""))

    # Pad to exactly 3 slots so the renderer always sees the same shape
    while len(period_buckets) < 3:
        period_buckets.append({})
        period_labels.append("")

    for row in section_c_region_rows:
        region_label = row.get("region", "")
        row["units_p1"] = period_buckets[0].get(region_label, 0) if period_buckets[0] else None
        row["units_p2"] = period_buckets[1].get(region_label, 0) if period_buckets[1] else None
        row["units_p3"] = period_buckets[2].get(region_label, 0) if period_buckets[2] else None

    # Surface period labels for use in Table 1 column headers
    # Default to historical-period labels; override with skill labels when available.
    section_c_period_labels = list(period_labels)
    start_period = surveillance_period.get("start", "")
    end_period = surveillance_period.get("end", "")
    determined_periods: List[Any] = []
    try:
        determined_periods = determine_12month_periods_from_dates(start_period, end_period)
        if determined_periods and len(determined_periods) >= 3:
            # Use skill-generated period labels if available
            section_c_period_labels = [p[2] for p in determined_periods[-3:]]  # Last 3 periods
    except Exception:
        pass  # Fall back to existing labels if skill fails

    # Add percentage column to section_c_region_rows (for current period)
    worldwide_total = total_units
    if worldwide_total > 0:
        for row in section_c_region_rows:
            row_units = row.get("units", 0)
            row["pct_current"] = round((row_units / worldwide_total) * 100, 1)

    # ── Per-region complaint rates (for Table 2 / Section D) ──
    # Inner-join sales × complaints on a canonical region key so that
    # "United Kingdom" / "UK" / "GB" don't appear as separate rows and
    # so rates_by_region is non-empty whenever both sides have data.
    complaints_by_region = complaints_data.get("by_region", {})
    canon_units: Dict[str, int] = {}
    canon_complaints: Dict[str, int] = {}
    for r, u in units_by_region.items():
        k = _canon_region(r)
        canon_units[k] = canon_units.get(k, 0) + int(u or 0)
    for r, c in complaints_by_region.items():
        k = _canon_region(r)
        canon_complaints[k] = canon_complaints.get(k, 0) + int(c or 0)
    region_rate_list = []
    all_regions = set(canon_units.keys()) | set(canon_complaints.keys())
    for region in sorted(all_regions):
        region_units = canon_units.get(region, 0)
        region_complaints = canon_complaints.get(region, 0)
        rate = calculate_rate(region_complaints, region_units)
        row = {
            "region": region,
            "units_distributed": region_units,
            "complaints": region_complaints,
            "complaint_rate": round(rate, 8),
            "complaint_percentage": round(rate * 100, 2),
            "rate_display": format_rate_display(region_complaints, region_units, denom_type),
        }
        # MEDDEV 2.7/1 Rev.4 occurrence classification per skill spec.
        row.update(classify_occurrence_code(rate))
        region_rate_list.append(row)
    region_rate_list.sort(key=lambda x: -x["complaints"])

    # ── Table 8: harm × device-problem × region (EEA+TR+XI / UK / Worldwide) ──
    # Deterministic build from raw complaint summaries; mirrors psur-complaint-tables skill.
    table8_rows = build_table8_rows(
        complaints_data.get("complaint_summaries", []) or [],
        total_units,
        classify_occurrence_code,
        strip_imdrf_code,
    )

    # ── harm_by_month (psur-trend-charts skill input) ──
    # {YYYY-MM: {harm_label: count}} — needed for stacked harm-trend chart.
    # Built deterministically from complaint_summaries so the chart layer
    # never has to recompute or fabricate.
    harm_by_month: Dict[str, Dict[str, int]] = {m: {} for m in all_months}
    for s in complaints_data.get("complaint_summaries", []) or []:
        d = (s.get("date") or "")[:7]
        if not d:
            continue
        harm_raw = s.get("harm") or "No Harm"
        harm_label = strip_imdrf_code(harm_raw) if harm_raw else "No Harm"
        if not harm_label:
            harm_label = "No Harm"
        harm_low = str(harm_label).strip().lower()
        if (
            harm_low in {"no harm", "no health consequence", "no health consequence or impact"}
            or harm_low.startswith("no harm")
            or "near miss" in harm_low
        ):
            harm_label = "No Health Consequence or Impact"
        harm_by_month.setdefault(d, {})
        harm_by_month[d][harm_label] = harm_by_month[d].get(harm_label, 0) + 1

    # ── Per-period count + rate aggregates (psur-trend-charts skill) ──
    # Aggregates the last three 12-month windows so the chart layer can
    # render per-period bar charts without recomputing.
    per_period_aggregates: List[Dict[str, Any]] = []
    for p_start, p_end, p_label in (determined_periods or [])[-3:]:
        p_complaints = 0
        p_units = 0
        for m in all_months:
            if p_start[:7] <= m <= p_end[:7]:
                p_complaints += int(complaints_by_month_filled.get(m, 0) or 0)
                p_units += int(units_by_month_filled.get(m, 0) or 0)
        p_rate = calculate_rate(p_complaints, p_units)
        agg = {
            "label": p_label,
            "start": p_start,
            "end": p_end,
            "complaints": p_complaints,
            "units": p_units,
            "rate": round(p_rate, 8),
            "rate_pct": round(p_rate * 100, 4),
        }
        agg.update(classify_occurrence_code(p_rate))
        per_period_aggregates.append(agg)

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

    # ── Reusable vs single-use bifurcation ──
    # Sales side: prefer the by_product_class buckets emitted by
    # parsers/sqlite_db.py (already populated when the SQLite path is
    # used). Otherwise fall back to per-product classification.
    sales_class = sales_data.get("by_product_class") or {}
    by_product_units = sales_data.get("by_product", {}) or {}
    cls_units = {"reusable": 0, "single_use": 0, "unknown": 0}
    if sales_class:
        for k, v in sales_class.items():
            cls_units[k] = cls_units.get(k, 0) + int(v or 0)
    elif product_classification:
        for prod, units in by_product_units.items():
            cls = product_classification.get(prod, {}).get("class", "unknown")
            cls_units[cls] = cls_units.get(cls, 0) + int(units or 0)

    # Complaints side
    complaints_class = complaints_data.get("by_product_class") or {}
    cls_complaints = {"reusable": 0, "single_use": 0, "unknown": 0}
    if complaints_class:
        for k, v in complaints_class.items():
            cls_complaints[k] = cls_complaints.get(k, 0) + int(v or 0)
    elif product_classification:
        for s in complaints_data.get("complaint_summaries", []) or []:
            prod = s.get("product_number") or ""
            cls = product_classification.get(prod, {}).get("class", "unknown")
            cls_complaints[cls] = cls_complaints.get(cls, 0) + 1

    reusable_units_v = cls_units.get("reusable", 0)
    single_use_units_v = cls_units.get("single_use", 0)
    unknown_units_v = cls_units.get("unknown", 0)
    reusable_complaints_v = cls_complaints.get("reusable", 0)
    single_use_complaints_v = cls_complaints.get("single_use", 0)
    unknown_complaints_v = cls_complaints.get("unknown", 0)
    reusable_rate_v = calculate_rate(reusable_complaints_v, reusable_units_v)
    single_use_rate_v = calculate_rate(single_use_complaints_v, single_use_units_v)
    portfolio_mixed = (reusable_units_v > 0 and single_use_units_v > 0)

    # ── UK rate ──
    uk_rate_v = calculate_rate(uk_complaints_total, uk_units)

    # ── Quarterly cumulative trend (preferred for multi-year service-life devices) ──
    # Aggregate complaints and sales into year-quarters, then compute the
    # cumulative-denominator rate at each quarter (£ cumulative complaints /
    # cumulative units). This is what MDCG 2022-21 §III.5 expects: a stable
    # period-anchored rate, not a single-month cohort rate that swings on
    # tiny denominators.
    def _q_key(month_key: str) -> str:
        try:
            y, m = month_key.split("-")
            q = (int(m) - 1) // 3 + 1
            return f"{y}-Q{q}"
        except Exception:
            return ""
    q_complaints: Dict[str, int] = {}
    q_units: Dict[str, int] = {}
    for m, c in complaints_by_month_filled.items():
        qk = _q_key(m)
        if qk:
            q_complaints[qk] = q_complaints.get(qk, 0) + int(c or 0)
    for m, u in units_by_month_filled.items():
        qk = _q_key(m)
        if qk:
            q_units[qk] = q_units.get(qk, 0) + int(u or 0)
    q_labels = sorted(set(q_complaints.keys()) | set(q_units.keys()))
    q_rates = []
    q_cumulative_rates = []
    cum_c = 0
    cum_u = 0
    for ql in q_labels:
        qc = q_complaints.get(ql, 0)
        qu = q_units.get(ql, 0)
        cum_c += qc
        cum_u += qu
        q_rates.append(round(calculate_rate(qc, qu), 8))
        q_cumulative_rates.append(round(calculate_rate(cum_c, cum_u), 8))
    quarterly_trend_v = {
        "quarter_labels": q_labels,
        "complaints_per_quarter": [q_complaints.get(q, 0) for q in q_labels],
        "units_per_quarter": [q_units.get(q, 0) for q in q_labels],
        "quarterly_rates": q_rates,
        "quarterly_rates_pct": [round(r * 100, 4) for r in q_rates],
        "cumulative_rates": q_cumulative_rates,
        "cumulative_rates_pct": [round(r * 100, 4) for r in q_cumulative_rates],
        "final_cumulative_rate": q_cumulative_rates[-1] if q_cumulative_rates else 0.0,
        "final_cumulative_rate_pct": (
            round(q_cumulative_rates[-1] * 100, 4) if q_cumulative_rates else 0.0
        ),
        "method": "cumulative_denominator_quarterly_per_MDCG_2022_21_III_5",
    }

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
        complaints_by_harm=canonical_complaints_by_harm,
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
        section_c_period_labels=section_c_period_labels,
        serious_incident_count=serious_count,
        eu_uk_serious_incident_count=eu_uk_si_count,
        fda_mdr_count=fda_mdr_count,
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
        table8_rows=table8_rows,
        rates_by_region=region_rate_list,
        countries_above_5pct=countries_5pct,
        has_previous_period_data=has_previous,
        complaint_number_format=complaint_number_format,
        # Bifurcation
        reusable_units=reusable_units_v,
        single_use_units=single_use_units_v,
        unknown_class_units=unknown_units_v,
        reusable_complaints=reusable_complaints_v,
        single_use_complaints=single_use_complaints_v,
        unknown_class_complaints=unknown_complaints_v,
        reusable_rate=round(reusable_rate_v, 8),
        single_use_rate=round(single_use_rate_v, 8),
        reusable_rate_pct=round(reusable_rate_v * 100, 4),
        single_use_rate_pct=round(single_use_rate_v * 100, 4),
        reusable_rate_display=format_rate_display(
            reusable_complaints_v, reusable_units_v, "procedures"
        ),
        single_use_rate_display=format_rate_display(
            single_use_complaints_v, single_use_units_v, "units"
        ),
        portfolio_is_mixed=portfolio_mixed,
        # UK breakout
        uk_rate=round(uk_rate_v, 8),
        uk_rate_pct=round(uk_rate_v * 100, 4),
        uk_rate_display=format_rate_display(uk_complaints_total, uk_units, denom_type),
        uk_serious_incidents=uk_serious_count,
        # Quarterly trend
        quarterly_trend=quarterly_trend_v,
        # Data-quality audit
        negative_unit_rows_excluded=int(sales_data.get("negative_unit_rows_excluded", 0) or 0),
        negative_units_total=int(sales_data.get("negative_units_total", 0) or 0),
        # Previous period (when input_parsing supplied a structured summary)
        previous_period_summary=previous_stats if isinstance(previous_stats, dict) else None,
        # psur-trend-charts skill inputs
        harm_by_month=harm_by_month,
        per_period_aggregates=per_period_aggregates,
    )
