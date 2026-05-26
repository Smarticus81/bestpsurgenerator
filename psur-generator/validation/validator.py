"""
PSURValidator — thin facade composing all validation mixins.

Each mixin lives in its own submodule under ``validation/`` and contributes
a group of ``_check_*`` / ``validate_*`` methods via Python MRO.
"""
import json
from typing import Any, Dict, List, Tuple

from config import TEMPLATE_SCHEMA_PATH, SECTION_GUIDANCE_PATH
from validation._schema_checks import SchemaValidationMixin
from validation._fabrication_checks import FabricationChecksMixin
from validation._formatting_checks import FormattingChecksMixin
from validation._content_checks import ContentChecksMixin
from validation._consistency_checks import ConsistencyChecksMixin
from validation._docx_checks import DocxChecksMixin
from validation._traceability import TraceabilityChecksMixin
from validation._remediation_checks import RemediationChecksMixin
from contradiction_accuracy_auditor import run_contradiction_accuracy_audit


class PSURValidator(
    SchemaValidationMixin,
    FabricationChecksMixin,
    FormattingChecksMixin,
    ContentChecksMixin,
    ConsistencyChecksMixin,
    DocxChecksMixin,
    TraceabilityChecksMixin,
    RemediationChecksMixin,
):
    """Validates PSUR against template schema and writing rules."""

    # --------------- class-level constants ---------------
    # Patterns that should NOT appear in narratives
    FORBIDDEN_PATTERNS = [
        r"Article \d+\(\d+\)",                    # EU MDR article+paragraph
        r"\u00a7\s*\d+",                          # Section sign citations
        r"(?:MDR|MDD|IVDR)\s+Section \d+\.\d+",   # Regulation section citations
        r"MDCG \d{4}",                             # MDCG guidance citations
        r"\bEU\s+MDR\b",                           # "EU MDR" shorthand
        r"Regulation\s*\(EU\)\s*2017/745",         # Full MDR regulation citation
        r"Annex\s+[IVX]+\s+of\s+MDCG",            # MDCG annex references
    ]

    BULLET_PATTERNS = [
        "\u2022 ", "\u25cf ", "\u25cb ", "\u25a0 ", "\u25a1 ",
        "\n- ", "\n* ", "\n\u2022 ",
    ]

    # --------------- lifecycle ---------------
    def __init__(self):
        with open(TEMPLATE_SCHEMA_PATH) as f:
            self.template = json.load(f)
        with open(SECTION_GUIDANCE_PATH) as f:
            self.guidance = json.load(f)

        self.schema = self.template.get("schema", {})
        self._defs = self.schema.get("$defs", {})

        # Build resolved section schemas for JSON Schema validation
        self._resolved_section_schemas = self._build_resolved_section_schemas()

        # Populated by the most recent validate() call
        self.last_traceability_matrix: Dict[str, Any] = {}

    # --------------- main entry point ---------------
    def validate(self, psur: Dict[str, Any], parsed_data: Dict[str, Any] = None,
                 device_context: Dict[str, Any] = None) -> Tuple[bool, List[str]]:
        """Validate complete PSUR.  Returns ``(is_valid, errors)``.

        Parameters
        ----------
        psur : dict
            The full PSUR JSON structure.
        parsed_data : dict, optional
            The parsed input data dict used for generation.  When provided,
            enables deep fabrication detection for Sections J, K, L, G.
        device_context : dict, optional
            Device context.  When provided, enables UK RP fabrication checks.
        """
        errors: List[str] = []
        if parsed_data is None:
            parsed_data = {}
        if device_context is None:
            device_context = {}

        # 1. Required top-level keys
        for key in ["form", "psur_cover_page", "sections"]:
            if key not in psur:
                errors.append(f"Missing required top-level key: {key}")

        # 2. All 13 sections present
        required_sections = [
            "A_executive_summary", "B_scope_and_device_description",
            "C_volume_of_sales_and_population_exposure", "D_information_on_serious_incidents",
            "E_customer_feedback", "F_product_complaint_types_counts_and_rates",
            "G_information_from_trend_reporting", "H_information_from_fsca",
            "I_corrective_and_preventive_actions", "J_scientific_literature_review",
            "K_review_of_external_databases_and_registries", "L_pmcf",
            "M_findings_and_conclusions",
        ]
        for section in required_sections:
            if section not in psur.get("sections", {}):
                errors.append(f"Missing required section: {section}")

        # Filter out internal metadata (keys starting with _)
        psur_content = {k: v for k, v in psur.items() if not k.startswith("_")}

        # ---- mixin-provided checks (3-25) ----
        # 3. No bullet points
        errors.extend(self._check_no_bullets(psur_content))
        # 4. No regulation citations
        errors.extend(self._check_no_citations(psur_content))
        # 5. Enum values
        errors.extend(self._check_enums(psur_content))
        # 6. SRN format
        errors.extend(self._check_srn_formats(psur))
        # 7. JSON Schema per section
        for section_key, section_data in psur.get("sections", {}).items():
            if isinstance(section_data, dict) and "error" not in section_data:
                errors.extend(self._validate_section_schema(section_key, section_data))
        # 8. Content integrity (cross-ref with _statistics)
        stats = psur.get("_statistics", {})
        if stats:
            errors.extend(self._check_content_integrity(psur, stats))
        # 9. Fabrication detection
        errors.extend(self._check_fabrication(psur))
        # 9a. External database fabrication (Section K)
        errors.extend(self._check_external_db_fabrication(psur, parsed_data))
        # 9b. Literature fabrication (Section J)
        errors.extend(self._check_literature_fabrication(psur, parsed_data))
        # 9c. PMCF fabrication (Section L)
        errors.extend(self._check_pmcf_fabrication(psur, parsed_data))
        # 9d. Trend report fabrication (Section G)
        errors.extend(self._check_trend_report_fabrication(psur, parsed_data))
        # 9e. UK Responsible Person fabrication
        errors.extend(self._check_uk_rp_fabrication(psur, device_context))
        # 9f. Narrative identifier leakage (e.g. stale CAPA-XXX from prior period)
        errors.extend(self._check_narrative_identifier_leakage(psur, parsed_data))
        # 10. Cover page
        errors.extend(self._check_cover_page(psur))
        # 11. IMDRF codes
        errors.extend(self._check_imdrf_codes(psur))
        # 12. Table 7 row sums
        errors.extend(self._check_table7_sums(psur))
        # 13. Period duration
        errors.extend(self._check_period_duration(psur))
        # 14. Narrative presence
        errors.extend(self._check_narrative_presence(psur))
        # 15. Rate precision
        errors.extend(self._check_rate_precision(psur))
        # 16. IMDRF code+term pairing
        errors.extend(self._check_imdrf_code_term_pairing(psur))
        # 17. Absence-of-evidence discipline
        errors.extend(self._check_absence_of_evidence(psur))
        # 18. Tone
        errors.extend(self._check_tone(psur))
        # 19. Cross-section date consistency
        errors.extend(self._check_date_consistency(psur))
        # 20. Sales/denominator consistency
        errors.extend(self._check_denominator_consistency(psur))
        # 21. Benefit-risk thread
        errors.extend(self._check_benefit_risk_thread(psur))
        # 22. Key fidelity
        errors.extend(self._check_key_fidelity(psur))
        # 23. Example copying
        errors.extend(self._check_example_copying(psur))
        # 24. Markdown/numbered-list formatting
        errors.extend(self._check_no_markdown_or_numbered_lists(psur))
        # 25. Empty table cells
        errors.extend(self._check_empty_table_cells(psur))
        # 26. Narrative depth (minimum word counts per section)
        errors.extend(self._check_narrative_depth(psur))
        # 27. Narrative substance (thin single-sentence narratives)
        errors.extend(self._check_narrative_substance(psur))
        # 28. Cross-section: D vs F serious incident/harm consistency
        errors.extend(self._check_serious_incident_consistency(psur))
        # 29. Cross-section: sterile/non-sterile consistency
        errors.extend(self._check_sterile_consistency(psur, device_context))
        # 30. Cross-section: C vs M regional total consistency
        errors.extend(self._check_regional_total_consistency(psur))
        # 31. Cross-section: G described actions vs H/I no-CAPA/FSCA logic gap
        errors.extend(self._check_actions_capa_consistency(psur))
        # 32. Cross-section: Class I NB reference consistency
        errors.extend(self._check_class_nb_consistency(psur, device_context))
        # 33. Manufacturer identity consistency
        errors.extend(self._check_manufacturer_consistency(psur, device_context))
        # 34. Single-use / reusable consistency
        errors.extend(self._check_single_use_consistency(psur, device_context))
        # 35. Complaint category totals consistency
        errors.extend(self._check_complaint_total_consistency(psur))
        # 36. Sales narrative vs table country consistency
        errors.extend(self._check_sales_narrative_vs_table(psur))
        # 37. D vs F serious incident harm count consistency
        errors.extend(self._check_serious_incident_d_vs_f_harm(psur))
        # 38. End-to-end deterministic reconciliation checks
        errors.extend(self._check_final_reconciliation_contract(psur, device_context))
        # 38a. Contradictions and accuracy auditor (source facts vs generated prose/tables)
        contradiction_report = run_contradiction_accuracy_audit(
            psur,
            parsed_data=parsed_data,
            device_context=device_context,
        )
        psur["_contradiction_accuracy_audit"] = contradiction_report.to_dict()
        for finding in contradiction_report.findings:
            if finding.severity in {"CRITICAL", "MAJOR"}:
                errors.append(
                    f"CONTRADICTION_ACCURACY [{finding.severity}] {finding.finding_id} "
                    f"{finding.section}: {finding.title} -- {finding.evidence} "
                    f"Expected: {finding.expected}"
                )
        # 39. Sentence-level traceability / leakage prevention
        trace_errors, trace_matrix = self._check_traceability(
            psur, parsed_data=parsed_data, device_context=device_context,
        )
        errors.extend(trace_errors)
        self.last_traceability_matrix = trace_matrix

        # 39-48. Remediation checks (audit findings F-001..F-013)
        errors.extend(self._check_incident_count_cross_section_consistency(psur))
        errors.extend(self._check_capa_flag_matches_list(psur))
        errors.extend(self._check_pmcf_summary_or_justification_present(psur))
        errors.extend(self._check_pms_plan_execution_traceability(psur))
        errors.extend(self._check_fsca_effectiveness_metric_present(psur))
        errors.extend(self._check_regional_normalized_rates_present(psur))
        errors.extend(self._check_device_lifetime_defined(psur))
        errors.extend(self._check_regulatory_metadata_complete(psur))
        errors.extend(self._check_rmf_traceability_mapping(psur))
        errors.extend(self._check_uk_classification_complete(psur))

        return (len(errors) == 0, errors)
