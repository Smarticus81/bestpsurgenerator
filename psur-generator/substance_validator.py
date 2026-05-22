"""Substance-Level PSUR Validator — validates data integrity, cross-references,
logical consistency, and regulatory substance beyond keyword compliance.

Produces a Substance Score (0-100) independent of the structural compliance score.
"""

from __future__ import annotations
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from rich.console import Console
from rich.table import Table

console = Console()


class Severity(str, Enum):
    CRITICAL = "CRITICAL"
    MAJOR = "MAJOR"
    MODERATE = "MODERATE"
    MINOR = "MINOR"


class FindingCategory(str, Enum):
    DATA_SANITY = "DATA_SANITY"
    CROSS_REFERENCE = "CROSS_REFERENCE"
    LOGICAL_CONSISTENCY = "LOGICAL_CONSISTENCY"
    REGULATORY_SUBSTANCE = "REGULATORY_SUBSTANCE"


@dataclass
class SubstanceFinding:
    finding_id: str
    severity: Severity
    category: FindingCategory
    title: str
    description: str
    section: str = ""
    evidence: str = ""
    recommendation: str = ""


@dataclass
class SubstanceReport:
    substance_score: float = 0.0
    findings: List[SubstanceFinding] = field(default_factory=list)
    total_checks: int = 0
    passed_checks: int = 0

    def compute_score(self) -> None:
        if self.total_checks == 0:
            self.substance_score = 0.0
            return
        penalty = 0.0
        weights = {Severity.CRITICAL: 15.0, Severity.MAJOR: 8.0,
                   Severity.MODERATE: 4.0, Severity.MINOR: 1.5}
        for f in self.findings:
            penalty += weights.get(f.severity, 1.0)
        raw = max(0.0, 100.0 - penalty)
        self.substance_score = round(raw, 1)


# ═══════════════════════════════════════════════════════════════════════════
# Layer 1: Data Sanity Engine (Deterministic)
# ═══════════════════════════════════════════════════════════════════════════

def _check_data_sanity(psur: Dict, stats: Dict) -> List[SubstanceFinding]:
    findings = []
    total_units = stats.get("total_units_sold", 0)
    total_complaints = stats.get("total_complaints", 0)
    period = stats.get("surveillance_period", {})
    start = period.get("start_date", "")
    end = period.get("end_date", "")

    # DS-01: Zero denominator
    if total_units == 0:
        findings.append(SubstanceFinding(
            finding_id="DS-01", severity=Severity.CRITICAL,
            category=FindingCategory.DATA_SANITY,
            title="Zero-denominator reporting period",
            description=f"Total units sold = 0 for period {start} to {end}. "
                        "Any complaint rates, trend analyses, or population exposure "
                        "estimates are mathematically meaningless.",
            section="C",
            recommendation="Verify input sales data contains rows matching the "
                          "reporting period. Consider adjusting --start/--end dates."
        ))

    # DS-02: Negative units
    region_rows = stats.get("section_c_region_rows", [])
    for row in region_rows:
        if row.get("units", 0) < 0:
            findings.append(SubstanceFinding(
                finding_id="DS-02", severity=Severity.CRITICAL,
                category=FindingCategory.DATA_SANITY,
                title=f"Negative units in region: {row.get('region', '?')}",
                description=f"Units = {row['units']} for {row.get('region')}.",
                section="C",
                recommendation="Check sales CSV for data quality issues."
            ))

    # DS-03: Complaint rate > 100%
    rate = stats.get("overall_complaint_percentage", 0.0)
    if rate > 100.0:
        findings.append(SubstanceFinding(
            finding_id="DS-03", severity=Severity.CRITICAL,
            category=FindingCategory.DATA_SANITY,
            title="Impossible complaint rate",
            description=f"Overall complaint rate is {rate}%, exceeding 100%.",
            section="F",
            recommendation="Verify complaint and sales data alignment."
        ))

    # DS-04: Complaints exist but zero sales
    if total_complaints > 0 and total_units == 0:
        findings.append(SubstanceFinding(
            finding_id="DS-04", severity=Severity.MAJOR,
            category=FindingCategory.DATA_SANITY,
            title="Orphan complaints with no sales denominator",
            description=f"{total_complaints} complaints recorded but 0 units sold. "
                        "Rate calculations are undefined.",
            section="F",
            recommendation="Ensure sales data covers the same period as complaints."
        ))

    # DS-05: All regions zero but prior period has data
    current_total = sum((r.get("units") or 0) for r in region_rows
                       if r.get("region") != "Worldwide")
    prior_total = sum((r.get("units_p1") or 0) for r in region_rows
                     if r.get("region") != "Worldwide")
    if current_total == 0 and prior_total > 0:
        findings.append(SubstanceFinding(
            finding_id="DS-05", severity=Severity.MAJOR,
            category=FindingCategory.DATA_SANITY,
            title="Complete sales cessation vs. prior period",
            description=f"Current period: 0 units. Prior period: {prior_total} units. "
                        "This may indicate a date range mismatch rather than actual "
                        "market withdrawal.",
            section="C",
            recommendation="Verify the --start/--end dates match sales data rows."
        ))

    # DS-06: Region bucketing — all sales in "Rest of World"
    row_map = {r["region"]: r.get("units", 0) for r in region_rows}
    named_regions = ["EEA+TR+XI", "United States", "Australia", "Canada",
                     "China", "Japan", "Brazil"]
    named_total = sum(row_map.get(r, 0) for r in named_regions)
    row_total = row_map.get("Rest of World", 0)
    if row_total > 0 and named_total == 0 and total_units > 0:
        findings.append(SubstanceFinding(
            finding_id="DS-06", severity=Severity.MODERATE,
            category=FindingCategory.DATA_SANITY,
            title="All sales bucketed in 'Rest of World'",
            description=f"{row_total} units in 'Rest of World', 0 in named regions. "
                        "Region mapping may be failing.",
            section="C",
            recommendation="Check region column values in sales CSV match expected "
                          "region names (e.g., 'Europe' → 'EEA+TR+XI')."
        ))

    return findings


# ═══════════════════════════════════════════════════════════════════════════
# Layer 2: Cross-Reference Auditor (Deterministic)
# ═══════════════════════════════════════════════════════════════════════════

def _extract_numbers_from_text(text: str) -> List[int]:
    """Extract all integers from narrative text."""
    return [int(n.replace(",", "")) for n in re.findall(r'\b[\d,]+\b', text)
            if n.replace(",", "").isdigit() and int(n.replace(",", "")) > 0]


def _check_cross_references(psur: Dict, stats: Dict) -> List[SubstanceFinding]:
    findings = []
    sections = psur.get("sections", {})
    total_units = stats.get("total_units_sold", 0)
    total_complaints = stats.get("total_complaints", 0)
    serious_count = stats.get("serious_incident_count", 0)

    # CR-01: Section C narrative vs statistics — unit count
    sec_c = sections.get("C_volume_of_sales_and_population_exposure", {})
    c_narrative = json.dumps(sec_c.get("sales_data_analysis", {}))
    c_numbers = _extract_numbers_from_text(c_narrative)
    if total_units > 0 and total_units not in c_numbers:
        findings.append(SubstanceFinding(
            finding_id="CR-01", severity=Severity.MAJOR,
            category=FindingCategory.CROSS_REFERENCE,
            title="Section C total units mismatch",
            description=f"Statistics block says {total_units} total units, "
                        f"but Section C narrative does not mention this number.",
            section="C",
            recommendation="Ensure narrative references the exact computed total."
        ))

    # CR-02: Section D serious incident count
    sec_d = sections.get("D_information_on_serious_incidents", {})
    d_text = json.dumps(sec_d)
    if serious_count > 0 and str(serious_count) not in d_text:
        findings.append(SubstanceFinding(
            finding_id="CR-02", severity=Severity.MAJOR,
            category=FindingCategory.CROSS_REFERENCE,
            title="Section D serious incident count mismatch",
            description=f"Statistics: {serious_count} serious incidents, "
                        f"but Section D does not reference this count.",
            section="D"
        ))

    # CR-03: Section F complaint rate vs statistics
    sec_f = sections.get("F_product_complaint_types_counts_and_rates", {})
    f_text = json.dumps(sec_f)
    stat_rate = stats.get("overall_complaint_percentage", 0.0)
    if total_units > 0 and total_complaints > 0:
        rate_str = f"{stat_rate:.1f}" if stat_rate < 10 else f"{stat_rate:.0f}"
        if rate_str not in f_text and f"{stat_rate}" not in f_text:
            findings.append(SubstanceFinding(
                finding_id="CR-03", severity=Severity.MAJOR,
                category=FindingCategory.CROSS_REFERENCE,
                title="Section F complaint rate mismatch",
                description=f"Computed rate: {stat_rate}%, not found in Section F.",
                section="F"
            ))

    # CR-04: Section M should reference PMCF if Section L has data
    sec_l = sections.get("L_pmcf", {})
    sec_m = sections.get("M_findings_and_conclusions", {})
    m_text = json.dumps(sec_m).lower()
    l_activities = sec_l.get("table_11_pmcf_activities", [])
    if l_activities and len(l_activities) > 0:
        has_pmcf_ref = "pmcf" in m_text or "post-market clinical" in m_text
        if not has_pmcf_ref:
            findings.append(SubstanceFinding(
                finding_id="CR-04", severity=Severity.MODERATE,
                category=FindingCategory.CROSS_REFERENCE,
                title="Section M omits PMCF findings",
                description="Section L documents PMCF activities but Section M "
                           "conclusion does not reference them.",
                section="M",
                recommendation="Section M must integrate PMCF findings into the "
                              "benefit-risk conclusion per MDCG 2022-21."
            ))

    return findings


# ═══════════════════════════════════════════════════════════════════════════
# Layer 3: Logical Consistency Checker
# ═══════════════════════════════════════════════════════════════════════════

def _check_logical_consistency(psur: Dict, stats: Dict) -> List[SubstanceFinding]:
    findings = []
    sections = psur.get("sections", {})
    total_units = stats.get("total_units_sold", 0)
    total_complaints = stats.get("total_complaints", 0)
    serious_count = stats.get("serious_incident_count", 0)
    trend = stats.get("trend_analysis", {})

    # LC-01: Zero sales but meaningful complaint rate discussion
    if total_units == 0:
        sec_f = sections.get("F_product_complaint_types_counts_and_rates", {})
        f_text = json.dumps(sec_f).lower()
        rate_keywords = ["rate of", "rate was", "rate increased", "rate decreased",
                         "trending", "rate exceeds"]
        problematic = [kw for kw in rate_keywords if kw in f_text]
        # Allow "0.0%" discussions — only flag if discussing non-zero rates
        if problematic and "0.0%" not in f_text.replace(" ", ""):
            findings.append(SubstanceFinding(
                finding_id="LC-01", severity=Severity.CRITICAL,
                category=FindingCategory.LOGICAL_CONSISTENCY,
                title="Rate analysis with zero denominator",
                description="Section F discusses complaint rates but the reporting "
                           f"period has 0 units sold. Found: {problematic}",
                section="F",
                recommendation="When denominator is zero, state rates are 'not "
                              "calculable' rather than presenting trend language."
            ))

    # LC-02: Zero incidents but active FSCAs
    if serious_count == 0:
        sec_h = sections.get("H_information_from_fsca", {})
        h_table = sec_h.get("table_8_fsca_initiated_current_period_and_open_fscas", [])
        active_fscas = [f for f in h_table
                       if f.get("status", "").lower() not in
                       ("closed", "n/a", "not applicable", "")]
        if active_fscas:
            findings.append(SubstanceFinding(
                finding_id="LC-02", severity=Severity.MAJOR,
                category=FindingCategory.LOGICAL_CONSISTENCY,
                title="Active FSCAs with zero serious incidents",
                description=f"Section D reports 0 serious incidents but Section H "
                           f"shows {len(active_fscas)} active FSCAs.",
                section="H"
            ))

    # LC-03: UCL breach but benefit-risk "unchanged"
    ucl = trend.get("ucl_3sigma", 0)
    current_rate = trend.get("current_rate", 0)
    sec_m = sections.get("M_findings_and_conclusions", {})
    m_text = json.dumps(sec_m).lower()
    if ucl > 0 and current_rate > ucl:
        if "unchanged" in m_text or "not adversely" in m_text:
            findings.append(SubstanceFinding(
                finding_id="LC-03", severity=Severity.CRITICAL,
                category=FindingCategory.LOGICAL_CONSISTENCY,
                title="UCL breach contradicts 'unchanged' conclusion",
                description=f"Trend UCL={ucl}, current rate={current_rate} (breach), "
                           "but Section M concludes benefit-risk is unchanged.",
                section="M",
                recommendation="A UCL breach requires explicit justification in the "
                              "benefit-risk conclusion."
            ))

    # LC-04: Section A benefit-risk vs Section M benefit-risk
    sec_a = sections.get("A_executive_summary", {})
    a_conclusion = sec_a.get("benefit_risk_assessment_conclusion", {})
    a_status = a_conclusion.get("conclusion", "")
    m_conclusion = sec_m.get("benefit_risk_profile_conclusion", "")
    if "adversely" in a_status.lower() and "not adversely" in m_text:
        findings.append(SubstanceFinding(
            finding_id="LC-04", severity=Severity.CRITICAL,
            category=FindingCategory.LOGICAL_CONSISTENCY,
            title="Section A and M benefit-risk conclusions contradict",
            description="Section A flags adverse impact but Section M says unchanged.",
            section="M"
        ))

    # LC-05: Zero sales but population exposure claims
    if total_units == 0:
        sec_c = sections.get("C_volume_of_sales_and_population_exposure", {})
        pop = sec_c.get("size_and_characteristics_of_population_using_device", {})
        pop_text = json.dumps(pop).lower()
        # Check for specific patient count claims that aren't "zero"
        patient_nums = re.findall(r'(\d[\d,]+)\s*patient', pop_text)
        nonzero_patients = [n for n in patient_nums
                           if int(n.replace(",", "")) > 0]
        if nonzero_patients:
            findings.append(SubstanceFinding(
                finding_id="LC-05", severity=Severity.MAJOR,
                category=FindingCategory.LOGICAL_CONSISTENCY,
                title="Non-zero patient exposure with zero sales",
                description=f"Section C claims {nonzero_patients[0]} patients "
                           "exposed but 0 units were sold in the period.",
                section="C"
            ))

    return findings


# ═══════════════════════════════════════════════════════════════════════════
# Layer 4: Regulatory Substance Judge (LLM-as-Judge)
# ═══════════════════════════════════════════════════════════════════════════

_SUBSTANCE_RUBRIC = """\
You are a senior EU MDR regulatory affairs expert evaluating a PSUR for \
SUBSTANCE — not just structural compliance, but whether the content is \
genuinely meaningful, evidence-based, and logically sound.

Evaluate the PSUR against these substance criteria:

1. EVIDENCE-BASED CONCLUSIONS: Are benefit-risk conclusions actually \
   supported by the data presented, or are they boilerplate?
2. DATA LIMITATIONS ACKNOWLEDGED: Does the report honestly acknowledge \
   gaps or limitations (e.g., zero sales, missing data sources)?
3. RACT INTEGRITY: Are RACT thresholds derived from documented risk \
   management files, or do they appear fabricated?
4. CROSS-SECTION INTEGRATION: Does Section M genuinely synthesize \
   findings from all prior sections, or just repeat generic statements?
5. LITERATURE INTEGRATION: Does the literature review actually inform \
   the benefit-risk conclusion with specific findings?

Return a JSON object:
{
  "findings": [
    {
      "id": "RS-XX",
      "severity": "CRITICAL|MAJOR|MODERATE|MINOR",
      "title": "...",
      "description": "...",
      "section": "A-M",
      "recommendation": "..."
    }
  ],
  "overall_assessment": "1-3 sentence summary"
}
Return ONLY the JSON object."""


def _run_llm_substance_judge(
    psur: Dict, stats: Dict
) -> List[SubstanceFinding]:
    """Layer 4: LLM-as-judge for regulatory substance."""
    findings = []
    try:
        from config import MODEL
        from llm_client import create_message

        # Build a condensed version of the PSUR for the LLM
        sections = psur.get("sections", {})
        condensed = {
            "total_units_sold": stats.get("total_units_sold"),
            "total_complaints": stats.get("total_complaints"),
            "serious_incident_count": stats.get("serious_incident_count"),
            "overall_complaint_rate": stats.get("overall_complaint_percentage"),
            "trend_status": stats.get("trend_analysis", {}).get("status"),
            "ucl": stats.get("trend_analysis", {}).get("ucl_3sigma_pct"),
        }

        # Extract key narrative sections (truncated)
        for key in ["A_executive_summary", "C_volume_of_sales_and_population_exposure",
                     "F_product_complaint_types_counts_and_rates",
                     "M_findings_and_conclusions"]:
            sec_text = json.dumps(sections.get(key, {}))
            condensed[key] = sec_text[:3000] if len(sec_text) > 3000 else sec_text

        prompt = (
            f"## PSUR Data Summary\n```json\n{json.dumps(condensed, indent=2)}\n```\n\n"
            "Evaluate this PSUR for regulatory SUBSTANCE per the rubric."
        )

        resp = create_message(
            model=MODEL, max_tokens=2000, temperature=0.1,
            system=_SUBSTANCE_RUBRIC,
            messages=[{"role": "user", "content": prompt}],
        )

        text = resp.content[0].text.strip()
        # Parse JSON response
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            data = json.loads(text[start:end])
            for item in data.get("findings", []):
                findings.append(SubstanceFinding(
                    finding_id=item.get("id", "RS-??"),
                    severity=Severity(item.get("severity", "MODERATE")),
                    category=FindingCategory.REGULATORY_SUBSTANCE,
                    title=item.get("title", ""),
                    description=item.get("description", ""),
                    section=item.get("section", ""),
                    recommendation=item.get("recommendation", ""),
                ))
    except Exception as e:
        console.print(f"  [yellow]LLM substance judge skipped: {e}[/yellow]")

    return findings


# ═══════════════════════════════════════════════════════════════════════════
# Main entry point
# ═══════════════════════════════════════════════════════════════════════════

def run_substance_validation(
    psur: Dict[str, Any],
    stats: Dict[str, Any],
    *,
    use_llm: bool = True,
    verbose: bool = True,
) -> SubstanceReport:
    """Run the full 4-layer substance validation.

    Args:
        psur: The PSUR JSON dict (with sections A-M).
        stats: The computed statistics dict.
        use_llm: Whether to run Layer 4 (LLM-as-judge).
        verbose: Print findings to console.

    Returns:
        SubstanceReport with findings and substance score.
    """
    report = SubstanceReport()

    if verbose:
        console.print("\n[bold]Running substance validation...[/bold]\n")

    # Layer 1
    l1 = _check_data_sanity(psur, stats)
    report.findings.extend(l1)
    report.total_checks += 6
    if verbose and l1:
        console.print(f"  Layer 1 (Data Sanity): {len(l1)} finding(s)")

    # Layer 2
    l2 = _check_cross_references(psur, stats)
    report.findings.extend(l2)
    report.total_checks += 4
    if verbose and l2:
        console.print(f"  Layer 2 (Cross-Reference): {len(l2)} finding(s)")

    # Layer 3
    l3 = _check_logical_consistency(psur, stats)
    report.findings.extend(l3)
    report.total_checks += 5
    if verbose and l3:
        console.print(f"  Layer 3 (Logical Consistency): {len(l3)} finding(s)")

    # Layer 4
    if use_llm:
        l4 = _run_llm_substance_judge(psur, stats)
        report.findings.extend(l4)
        report.total_checks += 5
    if verbose and use_llm:
        console.print(f"  Layer 4 (Regulatory Substance): "
                      f"{len(l4) if use_llm else 0} finding(s)")

    report.passed_checks = report.total_checks - len(report.findings)
    report.compute_score()

    if verbose:
        _print_substance_report(report)

    return report


def _print_substance_report(report: SubstanceReport) -> None:
    """Pretty-print the substance validation report."""
    color = ("green" if report.substance_score >= 70
             else "yellow" if report.substance_score >= 50 else "red")

    console.print(f"\n  [{color} bold]Substance Score: "
                  f"{report.substance_score}%[/{color} bold]")

    if not report.findings:
        console.print("  [green]No substance issues detected.[/green]\n")
        return

    table = Table(title="Substance Findings", show_lines=True)
    table.add_column("ID", style="bold", width=8)
    table.add_column("Severity", width=10)
    table.add_column("Category", width=18)
    table.add_column("Title", width=35)
    table.add_column("Section", width=4)

    sev_colors = {Severity.CRITICAL: "red", Severity.MAJOR: "yellow",
                  Severity.MODERATE: "cyan", Severity.MINOR: "dim"}

    for f in sorted(report.findings, key=lambda x: list(Severity).index(x.severity)):
        c = sev_colors.get(f.severity, "white")
        table.add_row(f.finding_id, f"[{c}]{f.severity.value}[/{c}]",
                      f.category.value, f.title, f.section)

    console.print(table)
    console.print()
