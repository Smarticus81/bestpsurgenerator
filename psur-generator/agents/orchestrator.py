"""Orchestrate all section agents to generate complete PSUR."""
import json
import os
import re
from datetime import datetime
from typing import Any, Dict, Optional
from dataclasses import asdict
from pathlib import Path
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from config import SECTION_GUIDANCE_PATH
from agents.base import SectionAgent
from agents.postprocessing import (
    enforce_benefit_risk_link,
    repair_section_tables,
    fix_table7_grand_total,
    normalize_enum_values,
    fix_fabricated_udi_di,
    fix_empty_and_placeholder_tables,
    compute_period_months,
    normalize_period_mentions,
    strip_regulation_citations,
    strip_marketing_language,
    fix_sterile_contradictions,
    fix_single_use_contradictions,
    fix_manufacturer_consistency,
    strip_nb_references_class_i,
    strip_wrong_cadence_tables,
    strip_unknown_section_a_keys,
    fix_section_a_capa_status,
    shorten_classification_rule,
    zero_fabricated_preceding_periods,
    fix_fabricated_external_db,
    fix_fabricated_literature,
    reconcile_table7_row_sum,
    fill_default_empty_tables,
    fix_first_person_singular,
    fix_cross_section_serious_consistency,
    scrub_leaked_identifiers,
    _build_allowed_identifier_set,
    coerce_schema_numeric_strings,
    strip_template_debris,
    format_rates_as_percentages,
    enforce_first_psur_consistency,
    align_fsca_capa_narrative,
    align_threshold_claims,
)
from agents.prefill import inject_prefilled_values
from agents.stats_filter import filter_statistics_for_section
from agents.prompts.global_context import build_global_context, extract_statistics_summary
from agents.prompts.shared_context import build_shared_context
from deterministic_tables import apply_psur_table_skills
from reconciliation import reconcile_psur_content
from contradiction_remediation import remediate_contradictions_with_llm, run_full_coherence_audit
from report_facts import build_report_facts
from statistics import PSURStatistics


def _is_iso_date(s: str) -> bool:
    """Check if string is already ISO 8601 (YYYY-MM-DD)."""
    return bool(re.match(r'^\d{4}-\d{2}-\d{2}$', s))


def _parse_date_to_iso(s: str) -> str:
    """Parse a human-readable date string to ISO 8601."""
    for fmt in (
        "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y",
        "%m/%d/%Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%b-%Y", "%d-%B-%Y",
    ):
        try:
            return datetime.strptime(s.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return s  # Return as-is if unparseable

console = Console()


def _get_cover_defaults_from_guidance() -> Dict[str, Any]:
    """Load cover-page defaults from guidance JSON (no hardcoded manufacturer/product)."""
    defaults = {
        "manufacturer_srn": "",
        "company_name": "",
        "address_lines": [],
        "ar_name": "",
        "ar_address": [],
        "ar_srn": "",
        "nb_name": "",
        "nb_number": "",
    }
    try:
        with open(SECTION_GUIDANCE_PATH, encoding="utf-8") as f:
            guidance = json.load(f)

        mfg = guidance.get("psur_cover_page", {}).get("fields", {}).get("manufacturer_information", {})
        reg = guidance.get("psur_cover_page", {}).get("fields", {}).get("regulatory_information", {})

        defaults["company_name"] = mfg.get("company_name", {}).get("default_value", "")
        defaults["address_lines"] = mfg.get("address_lines", {}).get("default_value", [])
        defaults["manufacturer_srn"] = mfg.get("manufacturer_srn", {}).get("default_value", "")

        ar = mfg.get("authorized_representative", {})
        defaults["ar_name"] = ar.get("name", {}).get("default_value", "")
        defaults["ar_address"] = ar.get("address_lines", {}).get("default_value", [])
        defaults["ar_srn"] = ar.get("authorized_representative_srn", {}).get("default_value", "")

        nb = reg.get("notified_body", {})
        defaults["nb_name"] = nb.get("name", {}).get("default_value", "")
        defaults["nb_number"] = nb.get("number", {}).get("default_value", "")
    except Exception:
        pass

    return defaults

# Section order matching FormQAR-054
SECTION_ORDER = [
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
    "M_findings_and_conclusions",
]

def _get_lake_insights(section_key: str, lake) -> Dict[str, Any]:
    """Return cross-source insights from the data lake relevant to a section.

    Each section gets only the cross-joined data it needs — preventing
    data leakage while enabling the cross-source visibility that was
    previously impossible.
    """
    letter = section_key.split("_")[0]
    insights: Dict[str, Any] = {}

    try:
        if letter == "C":
            # Sales by region with complaint overlay
            insights["complaints_per_region"] = lake.query(
                "SELECT region, COUNT(*) as n FROM complaints "
                "WHERE region != '' GROUP BY region ORDER BY n DESC"
            )

        elif letter == "D":
            # Serious incidents by region with sales denominator
            insights["serious_by_region_with_sales"] = lake.serious_incidents_by_region()

        elif letter == "F":
            # Monthly complaint rates with sales denominator
            insights["monthly_rates_with_denominator"] = lake.complaints_with_sales_denominator()

        elif letter == "G":
            # Same monthly rates for trend analysis
            insights["monthly_rates_with_denominator"] = lake.complaints_with_sales_denominator()

        elif letter == "I":
            # CAPAs near complaint spikes
            insights["capas_near_spikes"] = lake.capas_near_complaint_spikes()

        elif letter == "M":
            # Full cross-source summary for conclusions
            insights["data_estate_summary"] = lake.summary()
            insights["serious_by_region_with_sales"] = lake.serious_incidents_by_region()
            insights["monthly_rates_with_denominator"] = lake.complaints_with_sales_denominator()
    except Exception as e:
        insights["_error"] = str(e)

    return insights


def generate_psur(
    device_context: Dict[str, Any],
    statistics: PSURStatistics,
    parsed_data: Dict[str, Any],
    previous_psur: Optional[Dict] = None,
    checkpoint_path: Optional[Path] = None,
    resume_data: Optional[Dict] = None,
) -> Dict[str, Any]:
    """
    Generate complete PSUR by running all section agents.

    Supports checkpoint/resume: after each section completes, progress is
    saved to checkpoint_path.  On resume, completed sections are loaded
    from resume_data and skipped.

    Args:
        device_context: Device information (name, class, intended use, etc.)
        statistics: Pre-calculated statistics
        parsed_data: Dict with keys: sales, complaints, capa, cer
        previous_psur: Previous PSUR JSON for comparison (optional)
        checkpoint_path: Path to save checkpoint JSON after each section
        resume_data: Loaded checkpoint data for resume (dict with 'completed_sections')

    Returns:
        Complete PSUR as JSON matching template.json schema
    """

    stats_dict = asdict(statistics)
    period_months = compute_period_months(device_context, stats_dict)

    # ── Derive Class I awareness ─────────────────────────────────────
    eu_class_raw = (device_context.get("device_class_eu") or "").upper()
    is_class_i = "CLASS I" in eu_class_raw and "CLASS II" not in eu_class_raw
    sterile_raw = (device_context.get("sterility_status") or "").lower()
    is_sterile = sterile_raw in ("sterile", "yes", "true")
    class_i_no_nb = is_class_i and not is_sterile

    # ── Derive single-use / reusable awareness ───────────────────────
    single_use_raw = (device_context.get("single_use_or_reusable") or "").lower()
    is_reusable = single_use_raw in ("reusable", "multi-use", "multi use")

    # ── Resolve manufacturer identity for consistency enforcement ─────
    manufacturer_name = (
        device_context.get("manufacturer_name")
        or device_context.get("manufacturer_info", {}).get("company_name", "")
        or ""
    )

    # ── Resolve PSUR cadence for table variant cleanup ────────────────
    psur_cadence = device_context.get("psur_cadence", "ANNUALLY")

    # ── Data-availability flags for fabrication scrubbing ─────────────
    has_previous_period_data = bool(stats_dict.get("has_previous_period_data"))
    has_external_db = bool(parsed_data.get("external_db"))
    has_literature = bool(parsed_data.get("literature"))
    has_previous_psur = bool(previous_psur) or bool(parsed_data.get("previous_psur"))
    _fsca_count = len((parsed_data.get("fsca") or {}).get("records", [])) \
        if isinstance(parsed_data.get("fsca"), dict) else 0
    if not _fsca_count and isinstance(parsed_data.get("fsca"), list):
        _fsca_count = len(parsed_data["fsca"])
    _capa_count = len((parsed_data.get("capa") or {}).get("records", [])) \
        if isinstance(parsed_data.get("capa"), dict) else 0
    if not _capa_count and isinstance(parsed_data.get("capa"), list):
        _capa_count = len(parsed_data["capa"])
    # Threshold-exceedance signal for trimming "all within threshold" claims.
    _any_threshold_exceeded = False
    try:
        ract_rows = (stats_dict.get("ract_comparison") or {}).get("rows", []) or []
        _any_threshold_exceeded = any(
            (isinstance(r, dict) and (r.get("exceeds") or r.get("exceeded")
             or (r.get("actual_rate") is not None
                 and r.get("max_expected_rate") is not None
                 and float(r["actual_rate"]) > float(r["max_expected_rate"]))))
            for r in ract_rows
        )
    except Exception:
        _any_threshold_exceeded = False
    # Build the allowed-identifier set once per run so each section's
    # post-pass can scrub stale CAPA/MDR/FSCA references carried forward
    # from previous PSUR context without rebuilding it per section.
    _allowed_ids = _build_allowed_identifier_set(parsed_data)

    # ── Build persistent global context ONCE for all 13 sections ──
    period = stats_dict.get("surveillance_period", {})
    global_context = build_global_context(
        device_context=device_context,
        reporting_period_start=period.get("start_date", device_context.get("period_start", "")),
        reporting_period_end=period.get("end_date", device_context.get("period_end", "")),
        statistics_summary=extract_statistics_summary(stats_dict),
    )

    # ── Build manufacturer info from device_context (populated by extractors) ──
    # Use guidance/template defaults when extraction didn't find real values
    TEMPLATE_DEFAULTS = _get_cover_defaults_from_guidance()

    mfr_info = device_context.get("manufacturer_info", {})
    ar_info = device_context.get("authorized_representative_info", {})
    nb_info = device_context.get("notified_body", {})

    # SKILL F10: harness-reconciled NB number/name override (BSI 2797 not 0086).
    skill_nb_number = device_context.get("notified_body_number")
    skill_nb_name = device_context.get("notified_body_name")
    if skill_nb_number:
        nb_info = dict(nb_info)
        nb_info["number"] = skill_nb_number
        if skill_nb_name:
            nb_info["name"] = skill_nb_name

    # Normalise certificate date to ISO 8601 if human-readable
    cert_date = device_context.get("certificate_date", "")
    if cert_date and not _is_iso_date(cert_date):
        cert_date = _parse_date_to_iso(cert_date)
    # SKILL F6: harness-reconciled certificate date wins.
    skill_cert_date = device_context.get("eu_mdr_certificate_date")
    if skill_cert_date:
        cert_date = (
            skill_cert_date if _is_iso_date(skill_cert_date)
            else _parse_date_to_iso(skill_cert_date)
        )

    # SKILL F6: harness-reconciled certificate number wins over device_context.
    skill_cert_number = (
        device_context.get("eu_mdr_certificate_number")
        or device_context.get("certificate_number", "")
    )

    psur = {
        "form": {
            "form_id": "FormQAR-054",
            "form_title": "Periodic Safety Update Report (PSUR)",
            "revision": "C",
            "document_control": {
                "product_or_product_family": device_context.get("device_name", ""),
                "infocard_number": device_context.get("infocard_number", "")
            }
        },
        "psur_cover_page": {
            "manufacturer_information": {
                "company_name": mfr_info.get("company_name") or TEMPLATE_DEFAULTS["company_name"],
                "address_lines": mfr_info.get("address_lines") or TEMPLATE_DEFAULTS["address_lines"],
                "manufacturer_srn": mfr_info.get("manufacturer_srn") or TEMPLATE_DEFAULTS["manufacturer_srn"],
                "authorized_representative": {
                    "is_applicable": True,  # Always true — EU MDR requires AR for non-EU manufacturers
                    "name": ar_info.get("name") or TEMPLATE_DEFAULTS["ar_name"],
                    "address_lines": ar_info.get("address_lines") or TEMPLATE_DEFAULTS["ar_address"],
                    "authorized_representative_srn": ar_info.get("srn") or TEMPLATE_DEFAULTS["ar_srn"]
                }
            },
            "regulatory_information": {
                "certificate_number": skill_cert_number,
                "date_of_issue": cert_date,
                "notified_body": {
                    "name": nb_info.get("name") or TEMPLATE_DEFAULTS["nb_name"],
                    "number": nb_info.get("number") or TEMPLATE_DEFAULTS["nb_number"]
                },
                "psur_available_within_3_working_days": True
            },
            "document_information": {
                "data_collection_period": statistics.surveillance_period,
                "psur_cadence": device_context.get("psur_cadence", "ANNUALLY")
            }
        },
        "sections": {}
    }
    # Validators and auditors must see the same deterministic facts that will
    # be rendered. Without this, substance validation sees an empty stats dict
    # and raises false zero-denominator findings.
    psur["_statistics"] = stats_dict
    psur["_report_facts"] = build_report_facts(
        psur,
        stats=stats_dict,
        parsed_data=parsed_data,
        device_context=device_context,
        start_date=period.get("start_date", device_context.get("period_start", "")),
        end_date=period.get("end_date", device_context.get("period_end", "")),
    )

    # Generate each section
    console.print("\n[bold]Generating PSUR sections...[/bold]\n")

    # ── Initialize Data Lake for cross-source queries ─────────────────
    data_lake = None
    try:
        from data_lake import DataLake
        data_lake = DataLake(parsed_data, device_context, stats_dict)
        lake_summary = data_lake.summary()
        console.print(
            f"  [dim]Data Lake: {lake_summary['tables']['sales']['row_count']} sales, "
            f"{lake_summary['tables']['complaints']['row_count']} complaints, "
            f"{lake_summary['tables']['capas']['row_count']} CAPAs, "
            f"{lake_summary['tables']['fscas']['row_count']} FSCAs[/dim]"
        )
    except Exception as e:
        console.print(f"  [yellow]Data Lake initialization skipped: {e}[/yellow]")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console
    ) as progress:

        # Pre-load completed sections from checkpoint (resume support)
        completed_sections = {}
        if resume_data and isinstance(resume_data.get("completed_sections"), dict):
            completed_sections = resume_data["completed_sections"]

        for section_key in SECTION_ORDER:
            task = progress.add_task(f"Section {section_key.split('_')[0]}", total=1)

            # Skip sections already completed in a prior run
            if section_key in completed_sections:
                psur["sections"][section_key] = completed_sections[section_key]
                progress.update(task, completed=1, description=f"Section {section_key.split('_')[0]} (resumed)")
                continue

            try:
                agent = SectionAgent(
                    section_key,
                    global_context=global_context,
                    uk_market_detected=statistics.uk_market_detected,
                    class_i_no_nb=class_i_no_nb,
                )

                # Determine which parsed data this section needs
                section_data = _get_section_data(section_key, parsed_data)

                # Inject pre-computed immutable values
                section_data = inject_prefilled_values(
                    section_key, section_data, stats_dict, device_context
                )

                # Inject shared working context (prior section findings, dependency-aware)
                shared_ctx = build_shared_context(section_key, psur.get("sections", {}))
                if shared_ctx:
                    if section_data is None:
                        section_data = {}
                    section_data["_shared_context"] = shared_ctx

                # Inject cross-source data lake insights (per section)
                if data_lake:
                    if section_data is None:
                        section_data = {}
                    section_data["_data_lake"] = _get_lake_insights(
                        section_key, data_lake
                    )
                if section_data is None:
                    section_data = {}
                section_data["_report_facts"] = psur.get("_report_facts", {})

                # Filter statistics to only what this section needs
                section_stats = filter_statistics_for_section(section_key, stats_dict)

                # Generate
                section_content = agent.generate(
                    statistics=section_stats,
                    device_context=device_context,
                    parsed_data=section_data
                )
                section_content = enforce_benefit_risk_link(section_key, section_content)
                section_content = normalize_period_mentions(section_content, period_months)
                section_content = strip_regulation_citations(section_content)
                section_content = strip_marketing_language(section_content)
                section_content = strip_template_debris(section_content)
                section_content = format_rates_as_percentages(section_content)
                section_content = fix_sterile_contradictions(section_content, is_sterile)
                section_content = fix_single_use_contradictions(section_content, not is_reusable)
                section_content = fix_manufacturer_consistency(section_content, manufacturer_name)
                section_content = strip_nb_references_class_i(section_content, class_i_no_nb)
                section_content = repair_section_tables(section_content)
                section_content = fix_table7_grand_total(section_content)
                section_content = normalize_enum_values(section_key, section_content)
                section_content = fix_empty_and_placeholder_tables(section_key, section_content)
                section_content = fill_default_empty_tables(section_key, section_content)
                section_content = fix_fabricated_udi_di(section_key, section_content, device_context)
                section_content = strip_wrong_cadence_tables(section_key, section_content, psur_cadence)
                section_content = strip_unknown_section_a_keys(section_key, section_content)
                section_content = fix_section_a_capa_status(section_key, section_content)
                section_content = shorten_classification_rule(section_key, section_content)
                section_content = zero_fabricated_preceding_periods(
                    section_key, section_content, has_previous_period_data)
                section_content = fix_fabricated_external_db(
                    section_key, section_content, has_external_db)
                section_content = fix_fabricated_literature(
                    section_key, section_content, has_literature)
                section_content = reconcile_table7_row_sum(section_content)
                section_content = fix_first_person_singular(section_content)
                section_content = scrub_leaked_identifiers(section_content, _allowed_ids)
                section_content = coerce_schema_numeric_strings(section_content)

                psur["sections"][section_key] = section_content
                progress.update(task, completed=1, description=f"Section {section_key.split('_')[0]} done")

            except Exception as e:
                console.print(f"[red]Error in {section_key}: {e}[/red]")
                psur["sections"][section_key] = {"error": str(e)}
                progress.update(task, completed=1, description=f"Section {section_key.split('_')[0]} FAILED")

            # Save checkpoint after each section
            if checkpoint_path:
                try:
                    _save_checkpoint(checkpoint_path, psur)
                except Exception:
                    pass  # Checkpoint save failure is non-fatal

    period = stats_dict.get("surveillance_period", {}) or {}
    apply_psur_table_skills(
        psur,
        stats=statistics,
        parsed_data=parsed_data,
        start_date=period.get("start_date", ""),
        end_date=period.get("end_date", ""),
    )

    # ══════════════════════════════════════════════════════════════════════
    # Audit-Remediation Loop — iteratively fix compliance gaps
    # ══════════════════════════════════════════════════════════════════════
    MAX_AUDIT_ITERATIONS = int(os.getenv("PSUR_AUDIT_ITERATIONS", "0"))
    PASS_THRESHOLD = float(os.getenv("PSUR_AUDIT_PASS_THRESHOLD", "85"))
    audit_use_llm = os.getenv("PSUR_AUDIT_USE_LLM", "0").lower() in {"1", "true", "yes"}

    try:
        from psur_auditor import run_json_audit
    except ImportError as _ie:
        import sys
        print(f"  [orchestrator] psur_auditor not available, skipping audit loop: {_ie}", file=sys.stderr)
        return psur

    if MAX_AUDIT_ITERATIONS <= 0:
        console.print("\n[dim]Compliance audit-remediation loop skipped (PSUR_AUDIT_ITERATIONS=0).[/dim]\n")
    else:
        console.print("\n[bold]Running compliance audit-remediation loop...[/bold]\n")

    for audit_iter in range(1, MAX_AUDIT_ITERATIONS + 1):
        console.print(f"  [dim]Audit iteration {audit_iter}/{MAX_AUDIT_ITERATIONS}...[/dim]")

        section_results, audit_report = run_json_audit(
            psur,
            uk_market_detected=statistics.uk_market_detected,
            use_llm=audit_use_llm,
            verbose=True,
        )

        # Check if we've reached the pass threshold
        if audit_report.compliance_score >= PASS_THRESHOLD and audit_report.gap == 0:
            console.print(
                f"  [green]Audit passed: {audit_report.compliance_score}% compliance, "
                f"0 gaps.[/green]"
            )
            break

        # Identify sections that need remediation
        sections_needing_fix = [
            sr for sr in section_results
            if sr.remediation_prompt and sr.section_key in psur["sections"]
        ]

        if not sections_needing_fix:
            console.print(
                f"  [yellow]No actionable remediations found (score: "
                f"{audit_report.compliance_score}%).[/yellow]"
            )
            break

        console.print(
            f"  Remediating {len(sections_needing_fix)} section(s): "
            f"{', '.join(sr.section_key.split('_')[0] for sr in sections_needing_fix)}"
        )

        for sr in sections_needing_fix:
            section_key = sr.section_key
            letter = section_key.split("_")[0]

            try:
                agent = SectionAgent(
                    section_key,
                    global_context=global_context,
                    uk_market_detected=statistics.uk_market_detected,
                    class_i_no_nb=class_i_no_nb,
                )

                section_stats = filter_statistics_for_section(section_key, stats_dict)
                section_data = _get_section_data(section_key, parsed_data)

                remediated = agent.remediate(
                    section_content=psur["sections"][section_key],
                    remediation_prompt=sr.remediation_prompt,
                    statistics=section_stats,
                    device_context=device_context,
                    parsed_data=section_data,
                )

                # Re-apply all deterministic postprocessing
                remediated = enforce_benefit_risk_link(section_key, remediated)
                remediated = normalize_period_mentions(remediated, period_months)
                remediated = strip_regulation_citations(remediated)
                remediated = strip_marketing_language(remediated)
                remediated = strip_template_debris(remediated)
                remediated = format_rates_as_percentages(remediated)
                remediated = fix_sterile_contradictions(remediated, is_sterile)
                remediated = fix_single_use_contradictions(remediated, not is_reusable)
                remediated = fix_manufacturer_consistency(remediated, manufacturer_name)
                remediated = strip_nb_references_class_i(remediated, class_i_no_nb)
                remediated = repair_section_tables(remediated)
                remediated = fix_table7_grand_total(remediated)
                remediated = normalize_enum_values(section_key, remediated)
                remediated = fix_empty_and_placeholder_tables(section_key, remediated)
                remediated = fill_default_empty_tables(section_key, remediated)
                remediated = fix_fabricated_udi_di(section_key, remediated, device_context)
                remediated = strip_wrong_cadence_tables(section_key, remediated, psur_cadence)
                remediated = strip_unknown_section_a_keys(section_key, remediated)
                remediated = fix_section_a_capa_status(section_key, remediated)
                remediated = shorten_classification_rule(section_key, remediated)
                remediated = zero_fabricated_preceding_periods(
                    section_key, remediated, has_previous_period_data)
                remediated = fix_fabricated_external_db(
                    section_key, remediated, has_external_db)
                remediated = fix_fabricated_literature(
                    section_key, remediated, has_literature)
                remediated = reconcile_table7_row_sum(remediated)
                remediated = fix_first_person_singular(remediated)
                remediated = scrub_leaked_identifiers(remediated, _allowed_ids)
                remediated = coerce_schema_numeric_strings(remediated)

                psur["sections"][section_key] = remediated
                console.print(f"    Section {letter}: remediated")

            except Exception as e:
                console.print(f"    [red]Section {letter} remediation failed: {e}[/red]")

            # Save checkpoint after each remediation
            if checkpoint_path:
                try:
                    _save_checkpoint(checkpoint_path, psur)
                except Exception:
                    pass

        # Log iteration result
        if audit_iter == MAX_AUDIT_ITERATIONS:
            console.print(
                f"  [yellow]Max audit iterations reached "
                f"(score: {audit_report.compliance_score}%).[/yellow]"
            )

    # ── Final cross-section consistency pass ─────────────────────────
    apply_psur_table_skills(
        psur,
        stats=statistics,
        parsed_data=parsed_data,
        start_date=period.get("start_date", ""),
        end_date=period.get("end_date", ""),
    )
    psur = fix_cross_section_serious_consistency(psur)
    psur = enforce_first_psur_consistency(psur, has_previous_psur)
    psur = align_fsca_capa_narrative(psur, _fsca_count, _capa_count)
    psur = align_threshold_claims(psur, _any_threshold_exceeded)
    psur = reconcile_psur_content(
        psur,
        stats=psur.get("_statistics", {}),
        parsed_data=parsed_data,
        device_context=device_context,
        start_date=period.get("start_date", ""),
        end_date=period.get("end_date", ""),
    )
    # Re-run template-debris and duplicate-phrase scrubbing AFTER the
    # cross-section edits so any newly produced fragments are also cleaned.
    psur["sections"] = strip_template_debris(psur.get("sections", {}))

    try:
        psur = remediate_contradictions_with_llm(
            psur,
            statistics=statistics,
            parsed_data=parsed_data,
            device_context=device_context,
            start_date=period.get("start_date", ""),
            end_date=period.get("end_date", ""),
            global_context=global_context,
            console=console,
        )
        contradiction_report = run_full_coherence_audit(
            psur,
            parsed_data=parsed_data,
            device_context=device_context,
        )
        psur["_contradiction_accuracy_audit"] = contradiction_report.to_dict()
        if contradiction_report.blocking_findings:
            console.print(
                f"  [yellow]Contradictions/accuracy audit: "
                f"{contradiction_report.blocking_findings} unresolved blocking finding(s)[/yellow]"
            )
        else:
            console.print("  [green]Contradictions/accuracy audit: clean[/green]")
    except Exception as e:
        console.print(f"  [yellow]Contradictions/accuracy audit skipped: {e}[/yellow]")

    # ── Substance Validation ──────────────────────────────────────────
    try:
        from substance_validator import run_substance_validation
        substance_stats = psur.get("_statistics", {})
        substance_use_llm = os.getenv("PSUR_SUBSTANCE_USE_LLM", "0").lower() in {"1", "true", "yes"}
        substance_report = run_substance_validation(
            psur, substance_stats, use_llm=substance_use_llm, verbose=True
        )
        psur["_substance_validation"] = {
            "substance_score": substance_report.substance_score,
            "total_checks": substance_report.total_checks,
            "findings_count": len(substance_report.findings),
            "findings": [
                {"id": f.finding_id, "severity": f.severity.value,
                 "category": f.category.value, "title": f.title,
                 "description": f.description, "section": f.section}
                for f in substance_report.findings
            ],
        }
    except Exception as e:
        console.print(f"  [yellow]Substance validation skipped: {e}[/yellow]")

    # ── Store Data Lake summary in output ─────────────────────────────
    if data_lake:
        try:
            psur["_data_lake_summary"] = data_lake.summary()
        except Exception:
            pass
        finally:
            data_lake.close()

    # ── Knowledge-layer provenance (rules applied, KB version) ────────
    try:
        if os.getenv("PSUR_INCLUDE_KNOWLEDGE_META", "0").lower() not in {"1", "true", "yes"}:
            raise RuntimeError("disabled by PSUR_INCLUDE_KNOWLEDGE_META=0")
        from knowledge import build_meta_block
        from knowledge.provenance import collect_rule_ids_from_sections
        from knowledge import get_skill_registry

        applied_ids = collect_rule_ids_from_sections(psur.get("sections", {}))
        skills_invoked = []
        try:
            skills_invoked = list(get_skill_registry()._skills.keys())  # noqa: SLF001
            skills_invoked = [{"name": n, "version": get_skill_registry().get(n).version}
                              for n in skills_invoked if get_skill_registry().get(n)]
        except Exception:
            skills_invoked = []
        psur["_knowledge_meta"] = build_meta_block(
            rules_applied=applied_ids,
            skills_invoked=skills_invoked,
        )

        # Per-rule validator findings (rule_id-keyed traceability)
        try:
            from validation import PSURValidator, validate_with_rule_provenance
            _v = PSURValidator()
            rule_findings = validate_with_rule_provenance(
                _v, psur,
                parsed_data=psur.get("_parsed_data") or {},
                device_context=psur.get("_device_context") or {},
            )
            psur["_knowledge_meta"]["rule_findings"] = rule_findings
            psur["_knowledge_meta"]["rule_findings_count"] = len(rule_findings)
        except Exception as exc:
            console.print(f"  [yellow]Rule-keyed validation skipped: {exc}[/yellow]")

        # Declarative validation engine (Modules A-G + Semantic)
        try:
            from validation import ValidationEngine
            engine = ValidationEngine()
            engine_report = engine.run(
                psur,
                parsed_data=psur.get("_parsed_data") or {},
                device_context=psur.get("_device_context") or {},
                statistics=psur.get("_statistics") or {},
            )
            psur["_validation_engine"] = engine_report
            pv = engine_report["PSUR_VALIDATION"]
            colour = "green" if pv["READY"] else "red"
            console.print(
                f"  [{colour}]Validation engine: READY={pv['READY']}, "
                f"score={pv['SCORE']}, "
                f"critical={len(pv['CRITICAL_ERRORS'])}, "
                f"major={len(pv['MAJOR_ERRORS'])}, "
                f"minor={len(pv['MINOR_ERRORS'])}[/{colour}]"
            )
        except Exception as exc:
            console.print(f"  [yellow]Validation engine skipped: {exc}[/yellow]")
    except Exception as exc:
        if "PSUR_INCLUDE_KNOWLEDGE_META=0" in str(exc):
            console.print("  [dim]Knowledge-meta block skipped (PSUR_INCLUDE_KNOWLEDGE_META=0).[/dim]")
        else:
            console.print(f"  [yellow]Knowledge-meta block skipped: {exc}[/yellow]")

    for section in psur.get("sections", {}).values():
        if isinstance(section, dict):
            section.pop("_meta", None)

    return psur


def _save_checkpoint(checkpoint_path: Path, psur: Dict[str, Any]):
    """Save current generation progress to a checkpoint file."""
    checkpoint = {
        "timestamp": datetime.now().isoformat(),
        "completed_sections": {
            k: v for k, v in psur.get("sections", {}).items()
            if not (isinstance(v, dict) and "error" in v)
        },
        "partial_psur": psur,
    }
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    with open(checkpoint_path, "w") as f:
        json.dump(checkpoint, f, indent=2, default=str)


def _get_section_data(section_key: str, parsed_data: Dict[str, Any]) -> Optional[Dict]:
    """Get relevant parsed data for a section, with data availability warnings."""

    section_data_map = {
        "A_executive_summary": ["complaints", "capa", "previous_psur", "fsca", "analysis_workbook"],
        "B_scope_and_device_description": ["cer", "ifu", "rmf", "ract", "pms_plan", "previous_psur"],
        "C_volume_of_sales_and_population_exposure": ["sales", "previous_psur", "analysis_workbook"],
        "D_information_on_serious_incidents": ["complaints", "ract", "capa", "analysis_workbook"],
        "E_customer_feedback": ["complaints"],
        "F_product_complaint_types_counts_and_rates": ["complaints", "sales", "ract", "analysis_workbook"],
        "G_information_from_trend_reporting": ["complaints", "sales", "previous_psur", "ract", "analysis_workbook"],
        "H_information_from_fsca": ["fsca", "complaints"],
        "I_corrective_and_preventive_actions": ["capa", "ract", "complaints"],
        "J_scientific_literature_review": ["cer"],
        "K_review_of_external_databases_and_registries": ["external_db", "cer"],
        "L_pmcf": ["cer", "pmcf", "pms_plan"],
        "M_findings_and_conclusions": ["complaints", "sales", "capa", "cer", "previous_psur", "ract", "fsca", "pmcf", "analysis_workbook"],
    }

    # Section-specific data availability warnings
    missing_data_warnings = {
        "D_information_on_serious_incidents": {
            "missing": ["investigation_findings"],
            "warning": "IMPORTANT: No investigation findings data (IMDRF Annex C cause codes) was provided. Table 3 (investigation findings by region) MUST use an empty array []. The narrative must NOT fabricate investigation cause codes. State that investigation findings are documented in the complaint investigation records."
        },
        "E_customer_feedback": {
            "missing": ["customer_feedback"],
            "warning": "IMPORTANT: No dedicated customer feedback data was provided for this reporting period. The summary MUST state that no structured customer feedback was collected separately from the formal complaint process. Table 6 MUST be an empty array []. Do NOT fabricate feedback items, survey results, or training session data."
        },
        "H_information_from_fsca": {
            "missing": ["fsca"],
            "warning": "IMPORTANT: No FSCA data was provided. If no FSCAs were initiated, state this clearly. Table 8 MUST be an empty array []. Do NOT fabricate FSCA details."
        },
        "J_scientific_literature_review": {
            "missing": ["literature_search_results"],
            "warning": "CRITICAL — USER-INPUT SECTION: No literature search results data was provided. This section requires user-provided data. You MUST: (1) Set number_of_relevant_articles_identified to null. (2) State clearly that no formal literature search results were provided for this PSUR period. (3) Reference the CER for the most recent literature review. You MUST NOT: fabricate article counts, author names, journal titles, study findings, specific search terms used, or meta-analysis results. The methodology description must be LIMITED to a general framework — do NOT claim specific searches were conducted."
        },
        "K_review_of_external_databases_and_registries": {
            "missing": ["external_db"],
            "warning": "CRITICAL — USER-INPUT SECTION: No external database search results were provided. This section requires user-provided data. You MUST: (1) State that no external database review results were provided for this PSUR period. (2) List only the databases that are part of CooperSurgical's review protocol (FDA MAUDE, EU Vigilance, MHRA, BfArM, TGA DAEN, Health Canada). (3) Set table_10 to an EMPTY array []. You MUST NOT: fabricate ANY report counts, event numbers, percentages, regulatory actions, recalls, field corrections, 'industry average' rates, or comparative benchmark data from ANY external source. ZERO quantitative findings."
        },
        "L_pmcf": {
            "missing": ["pmcf"],
            "warning": "CRITICAL — USER-INPUT SECTION: No PMCF report or data was provided. This section requires user-provided data. You MUST: (1) State that no PMCF evaluation report results were provided. (2) Reference the PMCF Plan if available. (3) Table 11 may list only GENERAL activity categories (complaint monitoring, literature review) with status 'Ongoing' — NO detailed findings. You MUST NOT: fabricate registry enrollment numbers, patient counts, site counts, complication rates, response rates, PMCF study findings, or claim any PMCF activities 'confirmed' device performance."
        },
    }

    needed = section_data_map.get(section_key, [])

    result = {k: v for k, v in parsed_data.items() if k in needed and v is not None}

    # Include extra column data from tabular sources for LLM context
    for source_key in needed:
        source_data = parsed_data.get(source_key)
        if isinstance(source_data, dict) and source_data.get("extra_columns"):
            if "_extra_columns" not in result:
                result["_extra_columns"] = {}
            result["_extra_columns"][source_key] = source_data["extra_columns"]

    # Check for missing data and add warnings
    if section_key in missing_data_warnings:
        warning_info = missing_data_warnings[section_key]
        # Check if the section's primary data sources are actually available
        has_specific_data = any(
            k in parsed_data and parsed_data[k] is not None
            for k in warning_info["missing"]
        )
        if not has_specific_data:
            result["_DATA_WARNING"] = warning_info["warning"]

    if not result:
        return None

    return result
