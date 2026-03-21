"""
Shared Working Context — prior section findings passed between agents.

Each section receives summaries of relevant prior sections (not all).
This creates continuity across the 13-section pipeline without bleeding
data between sections that should remain independent.

Dependency routing prevents data-scope violations:
  - G (Trends) sees F (Rates) to reference complaint breakdown context
  - I (CAPA) sees D (Incidents) and H (FSCA) for cross-referencing
  - M (Conclusions) sees ALL prior sections A–L to synthesize findings
"""

from typing import Any, Dict, List


# ── Dependency map ──────────────────────────────────────────────────
# Which prior sections each section should receive summaries of.
# Sections NOT listed here (or with empty lists) operate independently.

SECTION_DEPENDENCIES: Dict[str, List[str]] = {
    "A_executive_summary": [],
    "B_scope_and_device_description": [],
    "C_volume_of_sales_and_population_exposure": [],
    "D_information_on_serious_incidents": [],
    "E_customer_feedback": [],
    "F_product_complaint_types_counts_and_rates": [
        "D_information_on_serious_incidents",     # Harm classification consistency
    ],
    "G_information_from_trend_reporting": [
        "F_product_complaint_types_counts_and_rates",
    ],
    "H_information_from_fsca": [
        "D_information_on_serious_incidents",     # Incident context for FSCA assessment
        "G_information_from_trend_reporting",      # Corrective actions context
    ],
    "I_corrective_and_preventive_actions": [
        "D_information_on_serious_incidents",
        "G_information_from_trend_reporting",      # Described actions need CAPA assessment
        "H_information_from_fsca",
    ],
    "J_scientific_literature_review": [],
    "K_review_of_external_databases_and_registries": [],
    "L_pmcf": [],
    "M_findings_and_conclusions": [
        "A_executive_summary",
        "B_scope_and_device_description",
        "C_volume_of_sales_and_population_exposure",
        "D_information_on_serious_incidents",
        "E_customer_feedback",
        "F_product_complaint_types_counts_and_rates",
        "G_information_from_trend_reporting",
        "H_information_from_fsca",
        "I_corrective_and_preventive_actions",
        "J_scientific_literature_review",
        "K_review_of_external_databases_and_registries",
        "L_pmcf",
    ],
}


# ── Summary extraction fields per section ───────────────────────────
# Maps section key → list of dot-separated field paths to extract.

_SUMMARY_FIELDS: Dict[str, List[str]] = {
    "A_executive_summary": [
        "benefit_risk_assessment_conclusion",
        "previous_psur_actions_status.actions_and_status_from_previous_report",
    ],
    "B_scope_and_device_description": [
        "device_information.product_name",
        "device_classification",
        "device_description_and_information.description",
        "device_description_and_information.intended_purpose_use",
    ],
    "C_volume_of_sales_and_population_exposure": [
        "sales_data_analysis.narrative_analysis",
        "size_and_characteristics_of_population_using_device.estimated_size_of_patient_population_exposed",
    ],
    "D_information_on_serious_incidents": [
        "narrative_summary",
        "new_incident_types_identified_this_cycle",
    ],
    "E_customer_feedback": [
        "summary",
    ],
    "F_product_complaint_types_counts_and_rates": [
        "complaint_rate_calculation.method_description_and_justification",
        "annual_number_of_complaints_and_complaint_rate_by_harm_and_medical_device_problem.commentary_context_for_exceedances",
    ],
    "G_information_from_trend_reporting": [
        "overall_monthly_complaint_rate_trending.breaches_commentary_and_actions",
        "overall_monthly_complaint_rate_trending.upper_control_limit_definition",
        "trend_reporting_summary.statement_if_not_applicable",
        "trend_reporting_summary.corrective_actions_summary",
    ],
    "H_information_from_fsca": [
        "summary_or_na_statement",
    ],
    "I_corrective_and_preventive_actions": [
        "summary_or_na_statement",
    ],
    "J_scientific_literature_review": [
        "summary_of_new_data_performance_or_safety",
        "literature_search_methodology",
        "comparison_with_similar_devices",
    ],
    "K_review_of_external_databases_and_registries": [
        "registries_reviewed_summary",
    ],
    "L_pmcf": [
        "summary_or_na_statement",
    ],
}


# ── Human-readable section labels ───────────────────────────────────

_SECTION_LABELS: Dict[str, str] = {
    "A_executive_summary": "Section A (Executive Summary)",
    "B_scope_and_device_description": "Section B (Scope & Device Description)",
    "C_volume_of_sales_and_population_exposure": "Section C (Sales & Population Exposure)",
    "D_information_on_serious_incidents": "Section D (Serious Incidents)",
    "E_customer_feedback": "Section E (Customer Feedback)",
    "F_product_complaint_types_counts_and_rates": "Section F (Complaint Types, Counts & Rates)",
    "G_information_from_trend_reporting": "Section G (Trend Reporting)",
    "H_information_from_fsca": "Section H (FSCA)",
    "I_corrective_and_preventive_actions": "Section I (CAPA)",
    "J_scientific_literature_review": "Section J (Literature Review)",
    "K_review_of_external_databases_and_registries": "Section K (External Databases)",
    "L_pmcf": "Section L (PMCF)",
    "M_findings_and_conclusions": "Section M (Findings & Conclusions)",
}


# ── Internal helpers ────────────────────────────────────────────────

def _extract_field(section_data: Dict[str, Any], field_path: str) -> str:
    """Walk a dot-separated path and return a truncated text value."""
    obj = section_data
    for part in field_path.split("."):
        if isinstance(obj, dict):
            obj = obj.get(part, "")
        else:
            return ""

    if isinstance(obj, str) and obj.strip():
        text = obj.strip()
        # For Section M context, allow longer summaries (up to 1000 chars)
        return text[:1000] + "..." if len(text) > 1000 else text

    if isinstance(obj, dict):
        parts = []
        for k, v in obj.items():
            if isinstance(v, str) and v.strip():
                text = v.strip()
                text = text[:500] + "..." if len(text) > 500 else text
                parts.append(f"{k}: {text}")
        return " ".join(parts)

    return ""


def _summarize_section(section_key: str, section_data: Dict[str, Any]) -> str:
    """Extract a concise summary from a completed section's output."""
    if not isinstance(section_data, dict) or "error" in section_data:
        return "Section not available."

    fields = _SUMMARY_FIELDS.get(section_key, [])
    parts = []
    for field_path in fields:
        text = _extract_field(section_data, field_path)
        if text:
            parts.append(text)

    return " ".join(parts) if parts else "No substantive content."


# ── Public API ──────────────────────────────────────────────────────

def build_shared_context(
    section_key: str,
    completed_sections: Dict[str, Dict[str, Any]],
) -> str:
    """Build shared working context for a section based on its dependencies.

    Called by the orchestrator before each section generation.  Returns a
    formatted block to include in the user prompt, or empty string if the
    section has no dependencies or no relevant priors are completed yet.

    Parameters
    ----------
    section_key : str
        The section about to be generated (e.g. "G_information_from_trend_reporting").
    completed_sections : dict
        All sections completed so far, keyed by section name.
    """
    deps = SECTION_DEPENDENCIES.get(section_key, [])
    if not deps:
        return ""

    summaries: List[str] = []
    for dep_key in deps:
        dep_data = completed_sections.get(dep_key)
        if dep_data is None:
            continue
        label = _SECTION_LABELS.get(dep_key, dep_key)
        summary = _summarize_section(dep_key, dep_data)
        summaries.append(f"  {label}: {summary}")

    if not summaries:
        return ""

    header = (
        "## PRIOR SECTION FINDINGS\n\n"
        "Reference these for continuity — do not restate their data.\n"
    )
    return header + "\n\n".join(summaries)
