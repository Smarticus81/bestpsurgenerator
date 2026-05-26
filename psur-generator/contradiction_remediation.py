"""LLM-assisted remediation loop for contradiction/accuracy findings."""
from __future__ import annotations

import json
import os
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Mapping, Optional

from agents.base import SectionAgent
from agents.postprocessing import (
    coerce_schema_numeric_strings,
    fill_default_empty_tables,
    fix_empty_and_placeholder_tables,
    fix_fabricated_external_db,
    fix_fabricated_literature,
    fix_fabricated_udi_di,
    fix_first_person_singular,
    fix_section_a_capa_status,
    fix_table7_grand_total,
    normalize_enum_values,
    repair_section_tables,
    shorten_classification_rule,
    strip_template_debris,
    strip_unknown_section_a_keys,
)
from contradiction_accuracy_auditor import BLOCKING_SEVERITIES, ContradictionFinding, run_contradiction_accuracy_audit
from deterministic_tables import apply_psur_table_skills
from holistic_coherence_auditor import run_holistic_coherence_review
from reconciliation import reconcile_psur_content


SECTION_BY_LETTER = {
    "A": "A_executive_summary",
    "B": "B_scope_and_device_description",
    "C": "C_volume_of_sales_and_population_exposure",
    "D": "D_information_on_serious_incidents",
    "E": "E_customer_feedback",
    "F": "F_product_complaint_types_counts_and_rates",
    "G": "G_information_from_trend_reporting",
    "H": "H_information_from_fsca",
    "I": "I_corrective_and_preventive_actions",
    "J": "J_scientific_literature_review",
    "K": "K_review_of_external_databases_and_registries",
    "L": "L_pmcf",
    "M": "M_findings_and_conclusions",
}


def remediate_contradictions_with_llm(
    psur: Dict[str, Any],
    *,
    statistics: Any,
    parsed_data: Mapping[str, Any],
    device_context: Mapping[str, Any],
    start_date: str,
    end_date: str,
    global_context: str = "",
    max_iterations: Optional[int] = None,
    console: Any = None,
) -> Dict[str, Any]:
    """Repair contradiction/accuracy findings, then re-audit.

    The deterministic auditor remains the judge. LLM involvement is used to
    understand and rewrite affected narratives/section JSON, after which the
    deterministic table and reconciliation layers reassert source facts.
    """
    if max_iterations is None:
        max_iterations = int(os.getenv("PSUR_CONTRADICTION_REMEDIATION_ITERATIONS", "2"))
    use_llm = os.getenv("PSUR_CONTRADICTION_REMEDIATION_USE_LLM", "1").lower() in {"1", "true", "yes"}

    _run_deterministic_repairs(
        psur,
        statistics=statistics,
        parsed_data=parsed_data,
        device_context=device_context,
        start_date=start_date,
        end_date=end_date,
    )

    report = run_full_coherence_audit(psur, parsed_data=parsed_data, device_context=device_context)
    psur["_contradiction_accuracy_audit"] = report.to_dict()
    if not report.blocking_findings or max_iterations <= 0:
        return psur

    if console:
        console.print(
            f"  [yellow]Contradictions/accuracy audit: {report.blocking_findings} "
            f"blocking finding(s); starting remediation[/yellow]"
        )

    for iteration in range(1, max_iterations + 1):
        blocking = [f for f in report.findings if f.severity in BLOCKING_SEVERITIES]
        if not blocking:
            break

        if use_llm:
            grouped = _group_findings_by_section(blocking)
            for section_key, findings in grouped.items():
                current = psur.setdefault("sections", {}).get(section_key)
                if not isinstance(current, dict):
                    continue
                if console:
                    letter = section_key.split("_", 1)[0]
                    console.print(
                        f"    [dim]Contradiction remediation pass {iteration}: "
                        f"Section {letter} ({len(findings)} finding(s))[/dim]"
                    )
                try:
                    agent = SectionAgent(
                        section_key,
                        global_context=global_context,
                        uk_market_detected=bool(_get_stat(statistics, "uk_market_detected", False)),
                        class_i_no_nb=_is_class_i_no_nb(device_context),
                    )
                    prompt = _build_remediation_prompt(findings, parsed_data, psur, section_key)
                    fixed = agent.remediate(
                        current,
                        prompt,
                        statistics=_stats_dict(statistics),
                        device_context=dict(device_context),
                        parsed_data=dict(parsed_data),
                    )
                    fixed = _postprocess_section(section_key, fixed, parsed_data, device_context)
                    psur["sections"][section_key] = fixed
                except Exception as exc:
                    if console:
                        console.print(
                            f"    [yellow]Section {section_key} contradiction remediation skipped: {exc}[/yellow]"
                        )

        _run_deterministic_repairs(
            psur,
            statistics=statistics,
            parsed_data=parsed_data,
            device_context=device_context,
            start_date=start_date,
            end_date=end_date,
        )
        report = run_full_coherence_audit(psur, parsed_data=parsed_data, device_context=device_context)
        psur["_contradiction_accuracy_audit"] = report.to_dict()
        if console:
            if report.blocking_findings:
                console.print(
                    f"    [yellow]Contradiction remediation pass {iteration}: "
                    f"{report.blocking_findings} blocking finding(s) remain[/yellow]"
                )
            else:
                console.print(f"    [green]Contradiction remediation pass {iteration}: clean[/green]")
        if not report.blocking_findings:
            break

    return psur


def run_full_coherence_audit(
    psur: Mapping[str, Any],
    *,
    parsed_data: Mapping[str, Any],
    device_context: Mapping[str, Any],
) -> Any:
    deterministic = run_contradiction_accuracy_audit(
        psur,
        parsed_data=parsed_data,
        device_context=device_context,
    )
    holistic = run_holistic_coherence_review(
        psur,
        parsed_data=parsed_data,
        device_context=device_context,
    )
    deterministic.findings.extend(holistic.findings)
    deterministic.auditor = "deterministic_and_llm_coherence_auditor"
    deterministic.llm_review = holistic.to_dict()
    deterministic.finalize()
    return deterministic


def _run_deterministic_repairs(
    psur: Dict[str, Any],
    *,
    statistics: Any,
    parsed_data: Mapping[str, Any],
    device_context: Mapping[str, Any],
    start_date: str,
    end_date: str,
) -> None:
    apply_psur_table_skills(
        psur,
        stats=statistics,
        parsed_data=parsed_data,
        start_date=start_date,
        end_date=end_date,
    )
    psur["_statistics"] = _stats_dict(statistics)
    reconcile_psur_content(
        psur,
        stats=psur.get("_statistics", {}),
        parsed_data=parsed_data,
        device_context=device_context,
        start_date=start_date,
        end_date=end_date,
    )
    psur["sections"] = strip_template_debris(psur.get("sections", {}))


def _group_findings_by_section(findings: Iterable[ContradictionFinding]) -> Dict[str, List[ContradictionFinding]]:
    grouped: Dict[str, List[ContradictionFinding]] = defaultdict(list)
    for finding in findings:
        section_label = str(finding.section)
        if "cover" in section_label.lower():
            # Cover/manufacturer metadata is corrected deterministically by
            # reconcile_psur_content(); do not waste LLM cycles on fake
            # "Section C" just because "Cover" begins with C.
            if "/B" not in section_label and "&B" not in section_label:
                continue
            letters = ["B"]
        else:
            letters = [part.strip() for part in section_label.replace("&", "/").split("/")]
        mapped = False
        for letter in letters:
            key = SECTION_BY_LETTER.get(letter[:1].upper())
            if key:
                grouped[key].append(finding)
                mapped = True
        if not mapped:
            grouped["M_findings_and_conclusions"].append(finding)
    return dict(grouped)


def _build_remediation_prompt(
    findings: List[ContradictionFinding],
    parsed_data: Mapping[str, Any],
    psur: Mapping[str, Any],
    section_key: str,
) -> str:
    stats = psur.get("_statistics", {})
    finding_lines = "\n".join(
        (
            f"- {f.finding_id} [{f.severity}] {f.section}: {f.title}\n"
            f"  Evidence: {f.evidence}\n"
            f"  Expected: {f.expected}\n"
            f"  Required fix: {f.recommendation}"
        )
        for f in findings
    )
    source_excerpt = _source_excerpt_for_section(section_key, parsed_data)
    fact_pack = {
        "total_units_sold": stats.get("total_units_sold"),
        "total_complaints": stats.get("total_complaints"),
        "eu_uk_serious_incident_count": stats.get("eu_uk_serious_incident_count"),
        "fda_mdr_count": stats.get("fda_mdr_count"),
        "uk_units": stats.get("uk_units"),
        "units_by_region": stats.get("units_by_region"),
        "overall_complaint_percentage": stats.get("overall_complaint_percentage"),
        "has_previous_period_data": stats.get("has_previous_period_data"),
    }
    report_facts = psur.get("_report_facts", {})
    return f"""
## CONTRADICTION AND ACCURACY REMEDIATION

You are repairing a generated PSUR section after an independent contradictions
and accuracy auditor found internal incoherence. Read the findings as a whole,
not as isolated wording edits. Repair the section so it is coherent with the
entire report and source facts.

Rules:
- Use deterministic facts verbatim. Do not recalculate rates or invent records.
- If the issue is caused by missing source data, keep the field as
  "[TO BE COMPLETED]" or explicitly state that the source data were not provided.
- Do not hide contradictions by deleting required content.
- Preserve table structures and schema keys.
- Keep FDA MDR-reportable events distinct from EU/UK serious incidents.
- Ensure action checkboxes agree with CAPA/FSCA source records.

## AUDITOR FINDINGS
{finding_lines}

## AUTHORITATIVE FACT PACK
{json.dumps(fact_pack, indent=2, default=str)}

## AUTHORITATIVE REPORT FACTS AND INTERPRETATIONS
{json.dumps(report_facts, indent=2, default=str)[:18000]}

## RELEVANT SOURCE EXCERPT
{source_excerpt}
"""


def _source_excerpt_for_section(section_key: str, parsed_data: Mapping[str, Any]) -> str:
    keys_by_section = {
        "A_executive_summary": ["previous_psur", "capa", "fsca", "complaints"],
        "B_scope_and_device_description": ["sales", "fsca", "external_db"],
        "C_volume_of_sales_and_population_exposure": ["sales", "previous_psur"],
        "D_information_on_serious_incidents": ["complaints"],
        "F_product_complaint_types_counts_and_rates": ["complaints", "ract"],
        "G_information_from_trend_reporting": ["complaints", "sales"],
        "H_information_from_fsca": ["fsca"],
        "I_corrective_and_preventive_actions": ["capa"],
        "K_review_of_external_databases_and_registries": ["external_db"],
        "L_pmcf": ["pmcf"],
        "M_findings_and_conclusions": ["complaints", "sales", "capa", "fsca", "pmcf", "ract", "external_db"],
    }
    selected = {k: parsed_data.get(k) for k in keys_by_section.get(section_key, []) if k in parsed_data}
    text = json.dumps(selected, indent=2, default=str)
    if len(text) > 12000:
        text = text[:12000] + "\n... [truncated]"
    return text


def _postprocess_section(
    section_key: str,
    section: Dict[str, Any],
    parsed_data: Mapping[str, Any],
    device_context: Mapping[str, Any],
) -> Dict[str, Any]:
    section = repair_section_tables(section)
    section = fix_table7_grand_total(section)
    section = normalize_enum_values(section_key, section)
    section = fix_empty_and_placeholder_tables(section_key, section)
    section = fill_default_empty_tables(section_key, section)
    section = fix_fabricated_udi_di(section_key, section, device_context)
    section = strip_unknown_section_a_keys(section_key, section)
    section = fix_section_a_capa_status(section_key, section)
    section = shorten_classification_rule(section_key, section)
    section = fix_fabricated_external_db(section_key, section, bool(parsed_data.get("external_db")))
    section = fix_fabricated_literature(section_key, section, bool(parsed_data.get("literature_review")))
    section = fix_first_person_singular(section)
    section = coerce_schema_numeric_strings(section)
    return section


def _stats_dict(statistics: Any) -> Dict[str, Any]:
    if isinstance(statistics, dict):
        return statistics
    try:
        from dataclasses import asdict

        return asdict(statistics)
    except Exception:
        return {}


def _get_stat(statistics: Any, key: str, default: Any = None) -> Any:
    if isinstance(statistics, Mapping):
        return statistics.get(key, default)
    return getattr(statistics, key, default)


def _is_class_i_no_nb(device_context: Mapping[str, Any]) -> bool:
    device_class = str(device_context.get("device_class", "")).upper()
    sterile = str(device_context.get("sterility_status", "")).lower()
    return device_class == "CLASS_I" and "sterile" not in sterile
