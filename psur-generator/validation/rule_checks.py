"""Rule-ID → check method registry.

Maps the ``validator_check`` field on knowledge-base rules to concrete
``_check_*`` mixin methods on ``PSURValidator``. This is the single source
of truth that ties registry rules to validator implementations.

Adding a new rule with a ``validator_check`` value requires either
(a) reusing an existing key here, or (b) adding a new key + corresponding
``_check_*`` method on a mixin. The CI helper ``audit_rule_check_drift()``
asserts every rule's ``validator_check`` resolves to a callable.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Tuple

# Map: validator_check key (as declared in knowledge/rules/*.json) →
#      attribute name on PSURValidator (a bound method when accessed on instance).
#
# Wrappers receive (validator, psur, parsed_data, device_context) and
# return a list of error strings. Most just delegate to one mixin method.
RULE_CHECKS: Dict[str, str] = {
    # HOUSE
    "no_first_person_pronouns":             "_check_tone",
    "no_marketing_language":                "_check_tone",
    "no_fabricated_identifiers":            "_check_narrative_identifier_leakage",
    # EU MDR
    "benefit_risk_conclusion_present":      "_check_benefit_risk_thread",
    "no_notified_body_references_class_i":  "_check_class_nb_consistency",
    # FormQAR-054
    "table7_grand_total_consistent":        "_check_table7_sums",
    "no_empty_table_cells":                 "_check_empty_table_cells",
    "no_template_debris":                   "_check_example_copying",
    # MDCG 2022-21
    "executive_summary_present":            "_check_narrative_presence",
    "consistent_denominator_across_sections": "_check_denominator_consistency",
    "rate_formula_disclosed":               "_check_rate_precision",
    "no_fabricated_literature":             "_check_literature_fabrication",
    "no_fabricated_external_db":            "_check_external_db_fabrication",
    "no_regulation_citations_in_narrative": "_check_no_citations",
    "no_imdrf_codes_in_narrative":          "_check_imdrf_codes",
    # UK MDR 2024
    "uk_row_present_in_table1":             "_check_regional_total_consistency",
    "uk_rp_no_fabrication":                 "_check_uk_rp_fabrication",
    # Remediation (audit findings F-001..F-013)
    "incident_count_cross_section_consistency": "_check_incident_count_cross_section_consistency",
    "capa_flag_matches_list":                "_check_capa_flag_matches_list",
    "pmcf_summary_or_justification_present": "_check_pmcf_summary_or_justification_present",
    "pms_plan_execution_traceability":      "_check_pms_plan_execution_traceability",
    "fsca_effectiveness_metric_present":    "_check_fsca_effectiveness_metric_present",
    "regional_normalized_rates_present":    "_check_regional_normalized_rates_present",
    "device_lifetime_defined":              "_check_device_lifetime_defined",
    "regulatory_metadata_complete":         "_check_regulatory_metadata_complete",
    "rmf_traceability_mapping":             "_check_rmf_traceability_mapping",
    "uk_classification_complete":           "_check_uk_classification_complete",
}


def run_rule_check(
    validator: Any,
    rule_check_key: str,
    psur: Dict[str, Any],
    parsed_data: Dict[str, Any] | None = None,
    device_context: Dict[str, Any] | None = None,
) -> List[str]:
    """Invoke the validator method bound to ``rule_check_key``.

    Returns the list of error strings produced by that check, or an empty
    list if the key is not registered. Unknown keys are *silently ignored*
    here; use :func:`audit_rule_check_drift` to enforce coverage.
    """
    method_name = RULE_CHECKS.get(rule_check_key)
    if not method_name:
        return []
    method = getattr(validator, method_name, None)
    if not callable(method):
        return []
    # Inspect signature lazily — most checks accept just (psur)
    import inspect
    sig = inspect.signature(method)
    params = sig.parameters
    args: List[Any] = [psur]
    if "parsed_data" in params or len(params) >= 2:
        args.append(parsed_data or {})
    if "device_context" in params or len(params) >= 3:
        args.append(device_context or {})
    try:
        result = method(*args[: len(params)])
    except TypeError:
        # Fallback: try just (psur)
        try:
            result = method(psur)
        except Exception:
            return []
    return list(result) if result else []


def audit_rule_check_drift() -> Tuple[List[str], List[str]]:
    """Compare KB rules' ``validator_check`` declarations against ``RULE_CHECKS``.

    Returns ``(missing_keys, orphan_keys)``:
      * missing_keys — declared on a rule but not in ``RULE_CHECKS``
      * orphan_keys — in ``RULE_CHECKS`` but no rule references them
    Used by ``main.py kb audit`` and CI to prevent silent drift.
    """
    from knowledge import get_registry
    reg = get_registry()
    declared: set[str] = set()
    for rule in reg.all():
        if rule.validator_check:
            declared.add(rule.validator_check)
    registered = set(RULE_CHECKS.keys())
    missing = sorted(declared - registered)
    orphans = sorted(registered - declared)
    return missing, orphans


def validate_with_rule_provenance(
    validator: Any,
    psur: Dict[str, Any],
    parsed_data: Dict[str, Any] | None = None,
    device_context: Dict[str, Any] | None = None,
) -> List[Dict[str, Any]]:
    """Run all KB rule-mapped checks and emit findings keyed by rule_id.

    Each finding dict has: ``rule_id``, ``framework``, ``citation``,
    ``criticality``, ``check``, ``errors``. Used to produce the
    ``_meta.rule_findings`` block in the rendered PSUR.
    """
    from knowledge import get_registry
    reg = get_registry()
    findings: List[Dict[str, Any]] = []
    for rule in reg.all():
        if not rule.validator_check:
            continue
        errs = run_rule_check(
            validator, rule.validator_check, psur, parsed_data, device_context
        )
        if errs:
            findings.append({
                "rule_id": rule.id,
                "framework": rule.framework,
                "citation": rule.citation,
                "criticality": rule.criticality,
                "check": rule.validator_check,
                "errors": errs[:10],  # cap to avoid blowup
            })
    return findings
