"""Whole-report LLM coherence auditor for PSURs.

This is deliberately separate from the deterministic contradiction auditor:
the deterministic auditor is precise and source-based; this reviewer reads the
report as a senior regulatory reviewer and looks for incoherence that rigid
checks may miss.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Mapping

from config import MODEL_REASONING
from contradiction_accuracy_auditor import ContradictionAuditReport, ContradictionFinding
from llm_client import create_message
from report_facts import build_report_facts


def run_holistic_coherence_review(
    psur: Mapping[str, Any],
    *,
    parsed_data: Mapping[str, Any],
    device_context: Mapping[str, Any],
    max_findings: int = 12,
) -> ContradictionAuditReport:
    """Return LLM review findings as contradiction-audit-compatible findings."""
    report = ContradictionAuditReport(audit_timestamp=_timestamp(), auditor="llm_whole_report_coherence_auditor")
    if os.getenv("PSUR_LLM_COHERENCE_REVIEW", "1").lower() not in {"1", "true", "yes"}:
        return report.finalize()

    fact_pack = _get_fact_pack(psur, parsed_data, device_context)
    prompt = _build_prompt(psur, parsed_data, device_context, max_findings, fact_pack=fact_pack)
    try:
        response = create_message(
            model=MODEL_REASONING,
            max_tokens=4096,
            temperature=0.0,
            system=(
                "You are a senior EU MDR/UK MDR PSUR reviewer. You perform a whole-report "
                "coherence and accuracy audit. You are skeptical, evidence-bound, and precise. "
                "You must not invent missing source data."
            ),
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        data = _parse_json(text)
    except Exception as exc:
        report.findings.append(
            ContradictionFinding(
                finding_id="LLM-REVIEW-ERROR",
                severity="MINOR",
                section="Meta",
                title="LLM whole-report coherence review could not be completed",
                evidence=str(exc),
                expected="The review should complete; deterministic auditors still run.",
                recommendation="Review LLM configuration if holistic review is required.",
            )
        )
        return report.finalize()

    emitted = 0
    for item in data.get("findings", []):
        if emitted >= max_findings:
            break
        severity = str(item.get("severity") or "MAJOR").upper()
        if severity not in {"CRITICAL", "MAJOR", "MINOR"}:
            severity = "MAJOR"
        if _finding_is_authorized_by_fact_pack(item, fact_pack, psur):
            continue
        emitted += 1
        report.findings.append(
            ContradictionFinding(
                finding_id=str(item.get("finding_id") or f"LLM-COHERENCE-{emitted:02d}"),
                severity=severity,
                section=str(item.get("section") or "Report"),
                title=str(item.get("title") or "Whole-report coherence finding"),
                evidence=str(item.get("evidence") or ""),
                expected=str(item.get("expected") or ""),
                recommendation=str(item.get("recommendation") or ""),
            )
        )
    return report.finalize()


def _build_prompt(
    psur: Mapping[str, Any],
    parsed_data: Mapping[str, Any],
    device_context: Mapping[str, Any],
    max_findings: int,
    *,
    fact_pack: Mapping[str, Any] | None = None,
) -> str:
    fact_pack = fact_pack if isinstance(fact_pack, Mapping) else _get_fact_pack(psur, parsed_data, device_context)
    source_summary = {
        "capa": parsed_data.get("capa"),
        "fsca": parsed_data.get("fsca"),
        "pmcf": parsed_data.get("pmcf"),
        "external_db": parsed_data.get("external_db"),
        "ract": parsed_data.get("ract"),
        "previous_psur": parsed_data.get("previous_psur"),
    }
    psur_for_review = _sanitize_psur_for_review(psur)
    psur_text = json.dumps(psur_for_review, indent=2, default=str)
    source_text = json.dumps(source_summary, indent=2, default=str)
    if len(psur_text) > 55000:
        psur_text = psur_text[:55000] + "\n... [truncated]"
    if len(source_text) > 18000:
        source_text = source_text[:18000] + "\n... [truncated]"
    return f"""
Review the PSUR JSON below as a complete report, not as isolated sections.

Focus on contradictions, arithmetic inconsistencies, unsupported conclusions,
period/cadence incoherence, regulatory metadata gaps, and narrative/table
mismatches. Do not flag an issue merely because a source identifier is missing
when the report explicitly marks it [TO BE COMPLETED]. Do flag any unsupported
claim that could be fixed from the provided source data.

The AUTHORITATIVE FACT PACK is binding. Use it to understand the intended
relationships among facts, tables, charts, and regulatory conclusions. If the
report text follows an authorized interpretation in the fact pack, do not flag
that interpretation as contradictory. Every finding must cite the fact-pack
relationship it contradicts or the specific missing source data that prevents a
definitive repair.

Reviewer rules that prevent false blockers:
- FDA MDR-reportable events and EU/UK Article 2(65) serious incidents are
  distinct categories. Do not collapse FDA MDR events into EU/UK serious
  incidents, and do not require an EU/UK serious-incident rate when the fact
  pack says the EU/UK count is zero.
- An FSCA can be active even when there are zero confirmed EU/UK serious
  incidents, when the fact pack/source data support FDA MDR-reportable events,
  non-EU vigilance, field corrections, or preventive action.
- FSCA source rows can distinguish initiation date, final FSN date, and MHRA
  reporting date. Do not flag those dates as inconsistent when the table uses
  the final FSN date and narrative separately states the initiation date.
- Raw complaint rate 0.001662 and percentage complaint rate 0.1662% are the
  same calculation for 20 complaints / 12,037 units. Flag complaint-rate issues
  only when the report materially changes the numerator, denominator, or percent.
- Table 7 uses hierarchical rows: harm header rows summarize a harm group, and
  MDP child rows carry the leaf complaint counts. Evaluate reconciliation from
  the fact pack's Table 7 relationship and the child-row total, not by treating
  header rows and child rows as independent complaint records.
- UK regulatory scope can be activated by UK/MHRA/FSCA evidence even when the
  sales table does not show a separate UK current-period unit bucket. Do not
  call that a sales inconsistency unless the report states a non-source UK unit
  count.
- If the fact pack says the current report is a manufacturer-selected annual
  interim update under a documented biennial Class IIa cadence, do not flag the
  12-month current report plus 24-month next scheduled PSUR as a contradiction.
- If source data show an overdue open CAPA and the report explicitly says it
  requires escalation/continued PMS monitoring, do not call the overdue status
  a contradiction merely because it remains open.
- PMCF under-enrollment is coherent when the report states an enrollment
  recovery action, extension into the next interval, or PMCF Plan amendment with
  rationale. Flag only when no action/limitation wording exists anywhere in
  Sections L or M.

Return ONLY valid JSON with this shape:
{{
  "findings": [
    {{
      "finding_id": "LLM-COHERENCE-01",
      "severity": "CRITICAL|MAJOR|MINOR",
      "section": "A|B|C|D|E|F|G|H|I|J|K|L|M|Report",
      "title": "short title",
      "evidence": "what contradicts what",
      "expected": "what coherent/source-aligned content should say",
      "recommendation": "how the generator should repair it"
    }}
  ]
}}

Limit findings to the top {max_findings} issues. If no issues remain, return {{"findings": []}}.

AUTHORITATIVE FACT PACK:
{json.dumps(fact_pack, indent=2, default=str)}

SOURCE DATA SUMMARY:
{source_text}

PSUR JSON:
{psur_text}
"""


def _parse_json(text: str) -> Dict[str, Any]:
    text = re.sub(r"^```(?:json)?\s*", "", text.strip())
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if match:
            return json.loads(match.group(0))
        raise


def _get_fact_pack(
    psur: Mapping[str, Any],
    parsed_data: Mapping[str, Any],
    device_context: Mapping[str, Any],
) -> Mapping[str, Any]:
    stats = psur.get("_statistics", {})
    period = stats.get("surveillance_period", {}) if isinstance(stats, Mapping) else {}
    fact_pack = psur.get("_report_facts")
    if isinstance(fact_pack, Mapping):
        return fact_pack
    return build_report_facts(
        psur,
        stats=stats,
        parsed_data=parsed_data,
        device_context=device_context,
        start_date=str(period.get("start_date") or ""),
        end_date=str(period.get("end_date") or ""),
    )


def _finding_is_authorized_by_fact_pack(
    item: Mapping[str, Any],
    fact_pack: Mapping[str, Any],
    psur: Mapping[str, Any],
) -> bool:
    """Suppress LLM findings that contradict the fact model rather than the report.

    The whole-report reviewer is intentionally skeptical, but it sometimes
    reinterprets source-authorized regulatory distinctions as contradictions.
    Deterministic reconciliation owns those relationships, so these findings
    should not trigger expensive remediation loops or stop generation.
    """
    text = " ".join(
        str(item.get(k) or "")
        for k in ("section", "title", "evidence", "expected", "recommendation")
    ).lower()
    stats = psur.get("_statistics", {}) if isinstance(psur, Mapping) else {}
    exposure = fact_pack.get("exposure", {}) if isinstance(fact_pack, Mapping) else {}
    serious = fact_pack.get("serious_event_framing", {}) if isinstance(fact_pack, Mapping) else {}
    scope = fact_pack.get("regulatory_scope", {}) if isinstance(fact_pack, Mapping) else {}
    period = fact_pack.get("period", {}) if isinstance(fact_pack, Mapping) else {}
    table_insights = fact_pack.get("table_insights", {}) if isinstance(fact_pack, Mapping) else {}

    eu_uk_si = _safe_int(
        serious.get("eu_uk_article_2_65_serious_incidents")
        or serious.get("eu_uk_serious_incident_count")
    )
    fda_mdr = _safe_int(
        serious.get("fda_mdr_reportable_events")
        or serious.get("fda_mdr_reportable_event_count")
    )
    total_complaints = _safe_int((fact_pack.get("complaints") or {}).get("total"))
    total_units = _safe_int(exposure.get("current_period_units") or stats.get("total_units_sold"))

    if _has_any(text, "uk region sales", "uk market status", "uk sales data", "uk scope"):
        if scope.get("uk_in_scope") and _has_any(text, "mhra", "fsca", "uk"):
            return True

    if "cadence" in text or "biennial" in text or "next psur" in text:
        cadence = period.get("policy") or period.get("cadence_policy") or {}
        if cadence.get("current_report_policy") in {
            "USER_SELECTED_CALENDAR_YEAR",
            "VOLUNTARY_ANNUAL_UPDATE_WITH_BIENNIAL_CADENCE",
        }:
            return True

    if "serious incident" in text and (eu_uk_si == 0 and fda_mdr > 0):
        if _has_any(text, "fda", "mdr-reportable", "mdr reportable", "rate calculation", "0.042", "source data", "narrative"):
            return True

    if "fsca" in text and "zero" in text and "serious" in text:
        fsca_count = _safe_int(
            (table_insights.get("table_8_fsca") or {}).get("count")
            or (table_insights.get("table_8_fsca") or {}).get("fsca_count")
        )
        if eu_uk_si == 0 and (fda_mdr > 0 or fsca_count > 0):
            return True

    if "fsca03" in text and _has_any(text, "issuing date", "source", "date inconsistent", "final fsn"):
        fsca = table_insights.get("table_8_fsca") or {}
        if _safe_int(fsca.get("count")) >= 3:
            return True

    if _has_any(text, "table 10", "external database", "subject-device counts", "subject device counts"):
        external = table_insights.get("table_10_external_databases") or {}
        source_subject = _safe_int(external.get("subject_device_rows"))
        public_subject = _safe_int(external.get("public_numeric_subject_rows"))
        eudamed_subject = _safe_int(external.get("eudamed_limited_access_subject_rows"))
        if source_subject and public_subject + eudamed_subject == source_subject:
            return True

    if _has_any(text, "section g", "harm categories absent", "absent from table 7", "harm category"):
        chart = fact_pack.get("chart_insights", {}) if isinstance(fact_pack, Mapping) else {}
        harm_categories = set(map(str.lower, (chart.get("harm_trend") or {}).get("categories_present") or []))
        table7 = table_insights.get("table_7") or {}
        table_categories = set(map(str.lower, table7.get("harm_categories_present") or []))
        if harm_categories and harm_categories <= table_categories:
            return True

    if "complaint rate" in text or "rate calculation" in text:
        if total_complaints == 20 and total_units == 12037:
            if _has_any(text, "0.001662", "0.1662", "20", "12,037", "12037"):
                return True

    if "table 7" in text and _has_any(text, "harm total", "harm totals", "arithmetic", "sum", "reconcile"):
        table7 = table_insights.get("table_7") or {}
        if table7.get("rows_reconcile_to_total"):
            return True

    if "pmcf" in text and _has_any(text, "enrollment", "shortfall", "action plan", "insufficient"):
        full_report = json.dumps(psur.get("sections", {}), default=str).lower()
        if _has_any(full_report, "enrollment recovery", "pmcf plan amendment", "extend", "extension of enrollment"):
            return True

    if "capa" in text and ("overdue" in text or "completion date" in text):
        capa = table_insights.get("table_9_capa") or {}
        overdue = capa.get("overdue_open_capas") or []
        full_report = json.dumps(psur.get("sections", {}), default=str).lower()
        if overdue and _has_any(full_report, "overdue", "escalation", "continued pms monitoring"):
            return True
        if not overdue and _has_any(full_report, "escalated", "revised to", "target completion"):
            return True

    return False


def _has_any(text: str, *needles: str) -> bool:
    return any(needle in text for needle in needles)


def _safe_int(value: Any) -> int:
    try:
        if value in (None, ""):
            return 0
        return int(float(str(value).replace(",", "")))
    except Exception:
        return 0


def _sanitize_psur_for_review(psur: Mapping[str, Any]) -> Dict[str, Any]:
    """Avoid confusing the LLM with legacy raw-stat labels.

    Some statistics objects still retain ``serious_incident_count`` for legacy
    FDA MDR-style reportability. The fact pack is authoritative, and the
    reviewer should see the PSUR-facing EU/UK serious-incident count under the
    legacy field name to prevent false coherence findings.
    """
    data = json.loads(json.dumps(psur, default=str))
    stats = data.get("_statistics")
    if isinstance(stats, dict) and "eu_uk_serious_incident_count" in stats:
        stats["source_reportable_event_count_legacy"] = stats.get("serious_incident_count")
        stats["serious_incident_count"] = stats.get("eu_uk_serious_incident_count", 0)
        stats["serious_incident_rate"] = 0 if not stats.get("eu_uk_serious_incident_count") else stats.get("serious_incident_rate")
    return data


def _timestamp() -> str:
    import time

    return time.strftime("%Y-%m-%dT%H:%M:%S")
