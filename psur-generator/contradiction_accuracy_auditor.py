"""Deterministic contradiction and accuracy auditor for generated PSUR JSON.

This auditor is intentionally not LLM-based. It compares generated narrative,
tables, and action checkboxes against source-derived statistics and parsed
records so obvious contradictions fail before a report is accepted.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional


BLOCKING_SEVERITIES = {"CRITICAL", "MAJOR"}


@dataclass
class ContradictionFinding:
    finding_id: str
    severity: str
    section: str
    title: str
    evidence: str
    expected: str
    recommendation: str


@dataclass
class ContradictionAuditReport:
    audit_timestamp: str
    auditor: str = "contradictions_and_accuracy_auditor"
    findings: List[ContradictionFinding] = field(default_factory=list)
    critical: int = 0
    major: int = 0
    minor: int = 0
    blocking_findings: int = 0
    passed: bool = True
    llm_review: Dict[str, Any] = field(default_factory=dict)

    def finalize(self) -> "ContradictionAuditReport":
        self.critical = sum(1 for f in self.findings if f.severity == "CRITICAL")
        self.major = sum(1 for f in self.findings if f.severity == "MAJOR")
        self.minor = sum(1 for f in self.findings if f.severity == "MINOR")
        self.blocking_findings = self.critical + self.major
        self.passed = self.blocking_findings == 0
        return self

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def run_contradiction_accuracy_audit(
    psur: Mapping[str, Any],
    *,
    parsed_data: Optional[Mapping[str, Any]] = None,
    device_context: Optional[Mapping[str, Any]] = None,
) -> ContradictionAuditReport:
    """Run deterministic cross-section and source-accuracy checks."""
    parsed_data = parsed_data or {}
    device_context = device_context or {}
    report = ContradictionAuditReport(audit_timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"))

    _check_serious_incident_framing(psur, report)
    _check_section_m_actions(psur, parsed_data, report)
    _check_previous_action_status(psur, report)
    _check_regulatory_scope(psur, parsed_data, device_context, report)
    _check_historical_sales_bucketing(psur, report)
    _check_sales_narrative_period_and_denominator(psur, report)
    _check_table7_total_reconciliation(psur, report)
    _check_external_database_consistency(psur, parsed_data, device_context, report)
    _check_chart_harm_language(psur, report)
    _check_manufacturer_identity(psur, device_context, report)
    _check_pmcf_shortfall_response(psur, parsed_data, report)
    _check_capa_dates(psur, report)
    _check_fsca_uk_reporting(psur, report)
    _check_ract_thresholds(psur, parsed_data, report)
    _check_reusable_single_use_language(psur, device_context, report)
    _check_customer_feedback_pmcf_consistency(psur, parsed_data, report)

    return report.finalize()


def contradiction_audit_errors(
    psur: Mapping[str, Any],
    *,
    parsed_data: Optional[Mapping[str, Any]] = None,
    device_context: Optional[Mapping[str, Any]] = None,
) -> List[str]:
    """Return validator-ready errors for blocking contradiction findings."""
    report = run_contradiction_accuracy_audit(
        psur,
        parsed_data=parsed_data,
        device_context=device_context,
    )
    return [
        (
            f"CONTRADICTION_ACCURACY [{f.severity}] {f.finding_id} "
            f"{f.section}: {f.title} -- {f.evidence} Expected: {f.expected}"
        )
        for f in report.findings
        if f.severity in BLOCKING_SEVERITIES
    ]


def _add(
    report: ContradictionAuditReport,
    finding_id: str,
    severity: str,
    section: str,
    title: str,
    evidence: str,
    expected: str,
    recommendation: str,
) -> None:
    report.findings.append(
        ContradictionFinding(
            finding_id=finding_id,
            severity=severity,
            section=section,
            title=title,
            evidence=evidence,
            expected=expected,
            recommendation=recommendation,
        )
    )


def _sections(psur: Mapping[str, Any]) -> Mapping[str, Any]:
    return psur.get("sections", {}) if isinstance(psur.get("sections"), Mapping) else {}


def _stats(psur: Mapping[str, Any]) -> Mapping[str, Any]:
    return psur.get("_statistics", {}) if isinstance(psur.get("_statistics"), Mapping) else {}


def _text(value: Any) -> str:
    return json.dumps(value, default=str, ensure_ascii=True).lower()


def _records_from(value: Any, *keys: str) -> List[Dict[str, Any]]:
    if isinstance(value, list):
        return [r for r in value if isinstance(r, dict)]
    if isinstance(value, Mapping):
        for key in keys:
            rows = value.get(key)
            if isinstance(rows, list):
                return [r for r in rows if isinstance(r, dict)]
    return []


def _is_blankish(value: Any) -> bool:
    s = str(value or "").strip()
    return s == "" or s.upper() in {"N/A", "NA", "NONE", "NOT_APPLICABLE", "NOT APPLICABLE"}


def _truthy_checkbox(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, str):
        return value.strip().lower() in {"yes", "true", "checked", "selected", "completed"}
    return False


def _as_int(value: Any) -> int:
    try:
        return int(str(value).replace(",", "").strip())
    except Exception:
        return 0


def _norm(value: Any) -> str:
    return str(value or "").strip()


def _source_count(parsed_data: Mapping[str, Any], key: str, *row_keys: str) -> int:
    return len(_records_from(parsed_data.get(key), *row_keys))


def _check_serious_incident_framing(psur: Mapping[str, Any], report: ContradictionAuditReport) -> None:
    stats = _stats(psur)
    sections = _sections(psur)
    eu_uk_si = _as_int(stats.get("eu_uk_serious_incident_count"))
    fda_mdr = _as_int(stats.get("fda_mdr_count"))
    if fda_mdr <= 0:
        return

    for section_key, label in (
        ("A_executive_summary", "A"),
        ("D_information_on_serious_incidents", "D"),
        ("M_findings_and_conclusions", "M"),
    ):
        section_text = _text(sections.get(section_key, {}))
        if eu_uk_si == 0:
            forbidden = [
                f"{fda_mdr} serious incidents",
                f"{fda_mdr} serious incident",
                "serious incident rate",
            ]
            if any(p in section_text for p in forbidden):
                _add(
                    report,
                    "CAA-001",
                    "CRITICAL",
                    label,
                    "FDA MDR events are framed as confirmed EU/UK serious incidents",
                    f"Section text contains one of {forbidden}.",
                    f"{fda_mdr} FDA MDR-reportable event(s); 0 EU/UK Article 2(65) serious incidents.",
                    "Rewrite the section to preserve the FDA MDR vs EU/UK serious-incident distinction.",
                )
            eu_uk_zero_stated = (
                "0 confirmed eu/uk" in section_text
                or "0 event(s) were confirmed as eu mdr" in section_text
                or "0 event(s) were confirmed as eu/uk" in section_text
                or "0 events were confirmed as eu/uk" in section_text
                or "0 eu/uk" in section_text
            )
            if label in {"D", "M"} and ("fda mdr-reportable" not in section_text or not eu_uk_zero_stated):
                _add(
                    report,
                    "CAA-002",
                    "MAJOR",
                    label,
                    "Serious-incident framing is incomplete",
                    "Section does not explicitly state both FDA MDR-reportable events and 0 confirmed EU/UK serious incidents.",
                    "Both facts must be stated together when FDA MDRs exist but EU/UK serious incidents are zero.",
                    "Use deterministic serious-incident framing from statistics.",
                )


def _check_section_m_actions(
    psur: Mapping[str, Any],
    parsed_data: Mapping[str, Any],
    report: ContradictionAuditReport,
) -> None:
    sections = _sections(psur)
    actions = (
        sections.get("M_findings_and_conclusions", {})
        .get("actions_taken_or_planned", {})
        if isinstance(sections.get("M_findings_and_conclusions"), Mapping)
        else {}
    )
    capa_rows = _records_from(
        sections.get("I_corrective_and_preventive_actions", {}),
        "table_9_capa_initiated_current_reporting_period",
    )
    fsca_rows = _records_from(
        sections.get("H_information_from_fsca", {}),
        "table_8_fsca_initiated_current_period_and_open_fscas",
    )
    capa_count = _source_count(parsed_data, "capa", "records", "capa_records") or len(
        [r for r in capa_rows if _norm(r.get("capa_number")).upper() != "N/A"]
    )
    fsca_count = _source_count(parsed_data, "fsca", "records", "fsca_records") or len(
        [r for r in fsca_rows if _norm(r.get("manufacturer_reference_number")).upper() != "N/A"]
    )
    if capa_count and not _truthy_checkbox(actions.get("capa_initiated")):
        _add(
            report,
            "CAA-003",
            "CRITICAL",
            "M",
            "CAPA action checkbox contradicts CAPA records",
            f"{capa_count} CAPA record(s) exist, but Section M does not mark CAPA initiated as Yes.",
            "CAPA Initiated must be Yes when current-period CAPA rows exist.",
            "Set Section M CAPA checkbox and follow-up text from source CAPA rows.",
        )
    if fsca_count and not _truthy_checkbox(actions.get("fsca_initiated")):
        _add(
            report,
            "CAA-004",
            "CRITICAL",
            "M",
            "FSCA action checkbox contradicts FSCA records",
            f"{fsca_count} FSCA record(s) exist, but Section M does not mark FSCA initiated as Yes.",
            "FSCA Initiated must be Yes when current-period FSCA rows exist.",
            "Set Section M FSCA checkbox and follow-up text from source FSCA rows.",
        )
    if (
        _truthy_checkbox(actions.get("risk_management_file_update"))
        or _truthy_checkbox(actions.get("clinical_evaluation_report_update"))
    ) and not _truthy_checkbox(actions.get("benefit_risk_assessment_update")):
        _add(
            report,
            "CAA-005",
            "MAJOR",
            "M",
            "Benefit-risk update checkbox conflicts with RMF/CER updates",
            "RMF or CER update is selected but benefit-risk assessment update is No/blank.",
            "Benefit-risk assessment update should be Yes when RMF/CER updates materially affect the synthesis.",
            "Set benefit-risk assessment update to Yes or add a documented rationale.",
        )


def _check_previous_action_status(psur: Mapping[str, Any], report: ContradictionAuditReport) -> None:
    sec_a = _sections(psur).get("A_executive_summary", {})
    text = _text(sec_a)
    status_block = sec_a.get("previous_psur_actions_status", {}) if isinstance(sec_a, Mapping) else {}
    status_text = _text(status_block)
    if ("completed" in text or "verified effective" in text) and "in_progress" in status_text:
        _add(
            report,
            "CAA-006",
            "MAJOR",
            "A",
            "Previous PSUR action status contradicts narrative",
            "Narrative says previous actions were completed/effective while checkbox/status says In Progress.",
            "Previous action status and narrative must agree.",
            "Drive Section A previous-action status from source action statuses.",
        )


def _check_regulatory_scope(
    psur: Mapping[str, Any],
    parsed_data: Mapping[str, Any],
    device_context: Mapping[str, Any],
    report: ContradictionAuditReport,
) -> None:
    sections = _sections(psur)
    sec_b = sections.get("B_scope_and_device_description", {})
    b_class = sec_b.get("device_classification", {}) if isinstance(sec_b, Mapping) else {}
    b_text = _text(sec_b)
    cover = psur.get("psur_cover_page", {}) if isinstance(psur.get("psur_cover_page"), Mapping) else {}
    cover_text = _text(cover)
    class_text = _text(b_class)

    device_class = (
        _norm(b_class.get("eu_mdr_classification"))
        or _norm(device_context.get("device_class"))
        or _norm(cover.get("regulatory_information", {}).get("device_classification"))
    ).upper()
    if device_class in {"CLASS_IIA", "CLASS_IIB", "CLASS_III"} and (
        "self-certified" in b_text or "no notified body certificate required" in b_text
    ):
        _add(
            report,
            "CAA-007",
            "CRITICAL",
            "B",
            "Non-Class-I device described as self-certified",
            f"Device classification is {device_class}, but Section B states self-certification/no NB certificate.",
            "Class IIa/IIb/III devices require Notified Body involvement.",
            "Populate certificate and NB details from device context or mark missing identifiers as [TO BE COMPLETED].",
        )

    uk_evidence = _has_uk_evidence(psur, parsed_data)
    uk = b_class.get("uk_classification", {}) if isinstance(b_class.get("uk_classification"), Mapping) else {}
    if uk_evidence and uk.get("is_applicable") is not True:
        _add(
            report,
            "CAA-008",
            "MAJOR",
            "B",
            "UK evidence exists but UK scope is not applicable",
            "UK sales/FSCA/MHRA evidence appears in source or report, but Section B does not mark UK applicable.",
            "UK fields must be populated or marked [TO BE COMPLETED] when UK evidence exists.",
            "Set UK scope and UK conformity/market placement fields from source facts.",
        )

    us_evidence = _has_us_evidence(psur, parsed_data)
    if us_evidence:
        if _is_blankish(b_class.get("us_fda_classification")) or _is_blankish(
            b_class.get("us_pre_market_submission_number")
        ):
            _add(
                report,
                "CAA-009",
                "MAJOR",
                "B",
                "US/FDA evidence exists but FDA metadata is blank",
                "US sales/FDA MDR/MAUDE evidence appears, but Section B FDA class or premarket submission is blank/N/A.",
                "FDA metadata must be populated or marked [TO BE COMPLETED].",
                "Populate US FDA classification and submission number from device context or use [TO BE COMPLETED].",
            )

    if "coopersurgical" in cover_text and _norm((device_context.get("manufacturer_info") or {}).get("company_name")):
        _add(
            report,
            "CAA-010",
            "CRITICAL",
            "Cover/B",
            "Manufacturer identity conflicts with device context",
            "Generated cover still contains CooperSurgical while device context defines another legal manufacturer.",
            "Manufacturer name/address/SRN must match device_context.json.",
            "Use device_context manufacturer_info as the sole manufacturer source.",
        )


def _check_historical_sales_bucketing(psur: Mapping[str, Any], report: ContradictionAuditReport) -> None:
    sec_c = _sections(psur).get("C_volume_of_sales_and_population_exposure", {})
    rows = (
        sec_c.get("table_1_sales_by_region", {})
        .get("annual_format", {})
        .get("rows", [])
        if isinstance(sec_c, Mapping)
        else []
    )
    by_region = {str(r.get("region")): r for r in rows if isinstance(r, Mapping)}
    worldwide_prev = by_region.get("Worldwide", {}).get("preceding_12_month_periods") or []
    rest_prev = by_region.get("Rest of World", {}).get("preceding_12_month_periods") or []
    named_prev_nonzero = False
    for region, row in by_region.items():
        if region in {"Worldwide", "Rest of World", "Unknown / Unattributed"}:
            continue
        if any(_as_int(v) for v in row.get("preceding_12_month_periods") or []):
            named_prev_nonzero = True
            break
    if any(_as_int(v) for v in worldwide_prev) and any(_as_int(v) for v in rest_prev) and not named_prev_nonzero:
        _add(
            report,
            "CAA-011",
            "MAJOR",
            "C",
            "Historical sales are mis-bucketed into Rest of World",
            "Worldwide historical sales are nonzero, named regions are zero, and Rest of World carries the history.",
            "Historical macro-regions must map to the same regulatory regions as current-period sales.",
            "Map Europe to EEA+TR+XI and NorthAmerica to United States before building Table 1.",
        )


def _check_external_database_consistency(
    psur: Mapping[str, Any],
    parsed_data: Mapping[str, Any],
    device_context: Mapping[str, Any],
    report: ContradictionAuditReport,
) -> None:
    sec_k = _sections(psur).get("K_review_of_external_databases_and_registries", {})
    if not isinstance(sec_k, Mapping):
        return
    k_text = _text(sec_k)
    rows = _table10_rows(sec_k)
    row_total = sum(_as_int(r.get("total_matches")) for r in rows)
    source_total = _source_count(parsed_data, "external_db", "records", "events", "results")
    if source_total and row_total == 0:
        _add(
            report,
            "CAA-012",
            "MAJOR",
            "K",
            "External database source rows are missing from Table 10",
            f"Source has {source_total} external event row(s), but Table 10 totals are zero.",
            "Section K narrative and Table 10 must use the same external database rows.",
            "Parse external database CSV rows and rebuild Table 10 deterministically.",
        )
    mentioned = _first_event_count_mention(k_text)
    if mentioned is not None and row_total and mentioned != source_total and mentioned != row_total:
        _add(
            report,
            "CAA-013",
            "MAJOR",
            "K",
            "External database narrative count conflicts with table/source",
            f"Section K mentions {mentioned} event(s), source has {source_total}, and Table 10 totals {row_total}.",
            "Narrative count must match source/table count or explicitly distinguish total vs subject-device counts.",
            "Rewrite Section K summary from parsed external database counts.",
        )
    source_subject = _subject_external_count(parsed_data, device_context)
    numeric_subject = sum(_as_int(r.get("subject_device_matches") or r.get("relevant_subject_device_matches")) for r in rows)
    eudamed_limited = any("eudamed" in _text(r.get("database_registry")) and "limited" in _text(r) for r in rows)
    if source_subject and numeric_subject and source_subject != numeric_subject and not eudamed_limited:
        _add(
            report,
            "CAA-022",
            "MAJOR",
            "K",
            "External database subject-device count is not reconciled",
            f"Narrative/source subject-device count is {source_subject}, but Table 10 numeric subject count is {numeric_subject}.",
            "Narrative must explain any EUDAMED/limited-access rows or table count must be corrected.",
            "Reconcile Section K narrative and Table 10 subject-device counts.",
        )


def _check_chart_harm_language(psur: Mapping[str, Any], report: ContradictionAuditReport) -> None:
    stats = _stats(psur)
    harm_labels = {str(k).lower() for k in (stats.get("complaints_by_harm") or {}).keys()}
    full_text = _text(psur)
    if "laceration complaints" in full_text and not any("laceration" in h for h in harm_labels):
        _add(
            report,
            "CAA-014",
            "MAJOR",
            "G",
            "Chart interpretation references absent harm category",
            "Report mentions laceration complaints but generated harm statistics do not contain laceration.",
            "Chart notes must describe actual generated harm categories.",
            "Build chart context from complaints_by_harm/rates_by_harm instead of hardcoded language.",
        )


def _check_manufacturer_identity(
    psur: Mapping[str, Any],
    device_context: Mapping[str, Any],
    report: ContradictionAuditReport,
) -> None:
    expected = _norm((device_context.get("manufacturer_info") or {}).get("company_name"))
    if not expected:
        return
    cover = psur.get("psur_cover_page", {}) if isinstance(psur.get("psur_cover_page"), Mapping) else {}
    actual = _norm(cover.get("manufacturer_information", {}).get("company_name"))
    if actual and actual != expected:
        _add(
            report,
            "CAA-015",
            "CRITICAL",
            "Cover",
            "Legal manufacturer does not match device context",
            f"Cover says '{actual}', device context says '{expected}'.",
            "The legal manufacturer must match device_context.json.",
            "Replace cover manufacturer fields from device_context manufacturer_info.",
        )


def _check_pmcf_shortfall_response(
    psur: Mapping[str, Any],
    parsed_data: Mapping[str, Any],
    report: ContradictionAuditReport,
) -> None:
    enrolled, planned = _pmcf_enrollment(parsed_data)
    if not planned or enrolled >= planned:
        return
    sec_m = _sections(psur).get("M_findings_and_conclusions", {})
    text = _text(sec_m)
    if "pmcf" not in text or not any(term in text for term in ("follow-up", "recovery", "monitoring", "plan revision")):
        _add(
            report,
            "CAA-016",
            "MAJOR",
            "M/L",
            "PMCF enrollment shortfall is not actioned",
            f"PMCF enrollment is {enrolled} of {planned}, but Section M lacks follow-up/recovery action.",
            "PMCF under-enrollment must be acknowledged and actioned.",
            "Add PMCF recovery monitoring, plan update, or documented rationale in Section M.",
        )


def _check_capa_dates(psur: Mapping[str, Any], report: ContradictionAuditReport) -> None:
    sec_i = _sections(psur).get("I_corrective_and_preventive_actions", {})
    rows = _records_from(sec_i, "table_9_capa_initiated_current_reporting_period")
    for row in rows:
        capa_id = _norm(row.get("capa_number") or row.get("id"))
        if capa_id.upper() == "N/A":
            continue
        status = _norm(row.get("status")).lower()
        if status in {"in progress", "open"} and (
            _is_blankish(row.get("initiation_date")) or _is_blankish(row.get("target_completion_date"))
        ):
            _add(
                report,
                "CAA-017",
                "MAJOR",
                "I",
                "Open CAPA is missing required dates",
                f"{capa_id} is {status} but initiation/target completion date is blank or N/A.",
                "Open CAPAs require initiation and target completion dates.",
                "Populate CAPA dates from the CAPA source file.",
            )


def _check_fsca_uk_reporting(psur: Mapping[str, Any], report: ContradictionAuditReport) -> None:
    sec_h = _sections(psur).get("H_information_from_fsca", {})
    rows = _records_from(sec_h, "table_8_fsca_initiated_current_period_and_open_fscas")
    h_text = _text(sec_h)
    actual_in_progress = sum(1 for r in rows if _norm(r.get("status")).lower() in {"in progress", "open"})
    stated = re.search(r"\b(\d+)\s+remain(?:s)?\s+in\s+progress\b", h_text)
    if stated and int(stated.group(1)) != actual_in_progress:
        _add(
            report,
            "CAA-019",
            "MAJOR",
            "H",
            "FSCA narrative status count conflicts with Table 8",
            f"Section H says {stated.group(1)} remain in progress, but Table 8 has {actual_in_progress}.",
            "FSCA status counts must be consistent between narrative and table.",
            "Drive Section H narrative from Table 8 status counts.",
        )
    for row in rows:
        ref = _norm(row.get("manufacturer_reference_number"))
        if ref.upper() == "N/A":
            continue
        impacted = _text(row.get("impacted_regions"))
        if ("uk" in impacted or "gb" in impacted) and _is_blankish(row.get("date_reported_to_mhra")):
            _add(
                report,
                "CAA-018",
                "MAJOR",
                "H",
                "UK-impacted FSCA lacks MHRA reporting date",
                f"{ref} impacts UK/GB but Date reported to MHRA is blank/N/A.",
                "UK-impacted FSCAs require MHRA reporting documentation or a clear not-applicable rationale.",
                "Populate MHRA dates from FSCA source data.",
            )


def _check_ract_thresholds(
    psur: Mapping[str, Any],
    parsed_data: Mapping[str, Any],
    report: ContradictionAuditReport,
) -> None:
    ract = parsed_data.get("ract") or {}
    has_thresholds = bool(
        (isinstance(ract, Mapping) and (ract.get("max_expected_rates") or ract.get("hazards")))
        or _records_from(ract, "records", "hazards")
    )
    if not has_thresholds:
        return
    sec_f = _sections(psur).get("F_product_complaint_types_counts_and_rates", {})
    table7 = sec_f.get("table_7_complaint_rate_and_count", {}) if isinstance(sec_f, Mapping) else {}
    rows = (table7.get("annual_format") or {}).get("rows") or table7.get("rows") or []
    mdp_rows = [r for r in rows if isinstance(r, Mapping) and _norm(r.get("medical_device_problem"))]
    missing = [
        r for r in mdp_rows
        if "ract not provided" in _text(
            r.get("max_expected_rate_of_occurrence_from_ract") or r.get("max_expected_rate")
        )
        or _is_blankish(r.get("max_expected_rate_of_occurrence_from_ract") or r.get("max_expected_rate"))
    ]
    if mdp_rows and len(missing) == len(mdp_rows):
        _add(
            report,
            "CAA-020",
            "MAJOR",
            "F",
            "RACT thresholds are absent despite provided RACT data",
            "All Table 7 MDP rows show N/A/RACT not provided while RACT source thresholds exist.",
            "Table 7 must show source RACT max-expected-rate values where available.",
            "Map RACT thresholds to Table 7 MDP rows before narrative generation.",
        )


def _check_reusable_single_use_language(
    psur: Mapping[str, Any],
    device_context: Mapping[str, Any],
    report: ContradictionAuditReport,
) -> None:
    architecture = _norm(device_context.get("single_use_or_reusable"))
    if not architecture or "reusable" not in architecture.lower() or "single" not in architecture.lower():
        return
    sec_b = _sections(psur).get("B_scope_and_device_description", {})
    b_text = _text(sec_b)
    if "single-use medical device" in b_text and "reusable control unit" not in b_text:
        _add(
            report,
            "CAA-021",
            "MAJOR",
            "B",
            "Device architecture reduced to blanket single-use wording",
            f"Device context says '{architecture}', but Section B uses blanket single-use language.",
            "Section B must describe the reusable control unit plus single-patient-use component architecture.",
            "Rewrite device description from device_context single_use_or_reusable and device_description fields.",
        )


def _check_sales_narrative_period_and_denominator(psur: Mapping[str, Any], report: ContradictionAuditReport) -> None:
    stats = _stats(psur)
    total_units = _as_int(stats.get("total_units_sold"))
    if not total_units:
        return
    sec_c = _sections(psur).get("C_volume_of_sales_and_population_exposure", {})
    c_text = _text(sec_c)
    forbidden_numbers = ["34,787", "34787", "37,925", "37925", "17,000", "17000", "35,000", "35000"]
    if any(n in c_text for n in forbidden_numbers):
        _add(
            report,
            "CAA-023",
            "CRITICAL",
            "C",
            "Section C narrative uses non-current-period sales/population numbers",
            f"Section C contains one of {forbidden_numbers} while current-period total is {total_units:,}.",
            "Section C narrative must use only the current reporting-period denominator for current exposure.",
            "Rewrite Section C narrative from Table 1 and statistics.",
        )
    if "january 2022 to december 2023" in c_text or "january 2020 to december 2021" in c_text:
        _add(
            report,
            "CAA-024",
            "CRITICAL",
            "C",
            "Section C narrative uses a period outside the report window",
            "Section C references a 24-month or pre-certification period in current exposure narrative.",
            "Current exposure narrative must align to the PSUR start/end dates.",
            "Remove out-of-period sales windows from current-period analysis.",
        )


def _check_table7_total_reconciliation(psur: Mapping[str, Any], report: ContradictionAuditReport) -> None:
    stats = _stats(psur)
    expected = _as_int(stats.get("total_complaints"))
    sec_f = _sections(psur).get("F_product_complaint_types_counts_and_rates", {})
    table = sec_f.get("table_7_complaint_rate_and_count", {}) if isinstance(sec_f, Mapping) else {}
    rows = (table.get("annual_format") or {}).get("rows") or table.get("rows") or []
    child_sum = 0
    grand = None
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        harm = _norm(row.get("harm")).lower()
        mdp = _norm(row.get("medical_device_problem"))
        count = _as_int(row.get("current_12_month_complaint_count"))
        if "grand total" in harm:
            grand = count
        elif mdp:
            child_sum += count
    if expected and child_sum and child_sum != expected:
        _add(
            report,
            "CAA-025",
            "CRITICAL",
            "F",
            "Table 7 complaint categories do not reconcile to total complaints",
            f"Table 7 child rows sum to {child_sum}, but total complaints are {expected}.",
            "All complaint categories must sum to the total complaint count.",
            "Rebuild Table 7 from deterministic complaint statistics.",
        )
    if expected and grand is not None and grand != expected:
        _add(
            report,
            "CAA-026",
            "CRITICAL",
            "F",
            "Table 7 grand total conflicts with total complaints",
            f"Table 7 grand total is {grand}, but total complaints are {expected}.",
            "Grand total must equal the complaint source total.",
            "Rebuild Table 7 grand total from deterministic complaint statistics.",
        )
    f_text = _text(sec_f)
    if "13 complaints" in f_text and "no harm" in f_text and not any(
        isinstance(r, Mapping) and _norm(r.get("harm")).lower() == "no harm" and _as_int(r.get("current_12_month_complaint_count")) == 13
        for r in rows
    ):
        _add(
            report,
            "CAA-027",
            "MAJOR",
            "F",
            "Section F narrative cites a category/count not present in Table 7",
            "Narrative references No Harm with 13 complaints but Table 7 does not show that category/count.",
            "Narrative category counts must match Table 7 exactly.",
            "Rewrite Section F narrative from deterministic Table 7 rows.",
        )


def _check_customer_feedback_pmcf_consistency(
    psur: Mapping[str, Any],
    parsed_data: Mapping[str, Any],
    report: ContradictionAuditReport,
) -> None:
    pmcf_text = _text(parsed_data.get("pmcf"))
    if not any(term in pmcf_text for term in ("user feedback", "neonatal nurses", "alarm audibility", "wrap-size")):
        return
    sec_e = _sections(psur).get("E_customer_feedback", {})
    e_text = _text(sec_e)
    if "no dedicated feedback" in e_text and "section l" not in e_text and "pmcf" not in e_text:
        _add(
            report,
            "CAA-028",
            "MAJOR",
            "E/L",
            "Customer feedback narrative omits PMCF user feedback",
            "Section E says no feedback beyond complaints, but PMCF source data includes neonatal-nurse user feedback.",
            "Section E must cross-reference PMCF user feedback or distinguish standalone feedback from PMCF feedback.",
            "Add a Section L cross-reference to Section E.",
        )


def _has_uk_evidence(psur: Mapping[str, Any], parsed_data: Mapping[str, Any]) -> bool:
    stats = _stats(psur)
    if _as_int(stats.get("uk_units")) > 0 or stats.get("uk_market_detected"):
        return True
    for row in _records_from(parsed_data.get("fsca"), "records", "fsca_records"):
        if _norm(row.get("date_reported_to_mhra") or row.get("mhra_report_date")):
            return True
        if "uk" in _text(row.get("regions_affected") or row.get("impacted_regions")):
            return True
    report_text = _text(psur)
    return "mhra" in report_text


def _has_us_evidence(psur: Mapping[str, Any], parsed_data: Mapping[str, Any]) -> bool:
    stats = _stats(psur)
    units = stats.get("units_by_region") or {}
    if _as_int(units.get("NorthAmerica")) or _as_int(units.get("United States")):
        return True
    if _as_int(stats.get("fda_mdr_count")):
        return True
    for row in _records_from(parsed_data.get("external_db"), "records", "events", "results"):
        if "maude" in _text(row.get("external_source") or row.get("database_registry")):
            return True
    return False


def _table10_rows(sec_k: Mapping[str, Any]) -> List[Dict[str, Any]]:
    table = sec_k.get("table_10_external_database_review") or sec_k.get("table_10_adverse_events_and_recalls") or []
    if isinstance(table, Mapping):
        rows = table.get("rows") or (table.get("annual_format") or {}).get("rows") or []
    else:
        rows = table
    return [r for r in rows if isinstance(r, dict)]


def _subject_external_count(parsed_data: Mapping[str, Any], device_context: Mapping[str, Any]) -> int:
    rows = _records_from(parsed_data.get("external_db"), "records", "events", "results")
    names = {str(device_context.get("device_name") or "").strip().lower()}
    models = {str(v).strip().lower() for v in (device_context.get("known_identifiers", {}).get("model_numbers", []) or [])}
    count = 0
    for row in rows:
        model = str(row.get("device_model") or "").strip().lower()
        name = str(row.get("device_name") or "").strip().lower()
        if (model and model in models) or (name and name in names):
            count += 1
    return count


def _first_event_count_mention(text: str) -> Optional[int]:
    match = re.search(r"\b(\d+)\s+(?:total\s+)?events?\b", text)
    if not match:
        return None
    return int(match.group(1))


def _pmcf_enrollment(parsed_data: Mapping[str, Any]) -> tuple[int, int]:
    source = parsed_data.get("pmcf")
    text = json.dumps(source, default=str) if isinstance(source, Mapping) else str(source or "")
    match = re.search(r"\b(\d+)\s+of\s+(\d+)\b", text)
    if not match:
        return 0, 0
    return int(match.group(1)), int(match.group(2))
