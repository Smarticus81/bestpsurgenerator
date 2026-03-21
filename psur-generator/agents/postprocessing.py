"""Deterministic post-processing applied after every section generation.

Extracted from orchestrator.py.  All functions are pure transforms on
section_content dicts — they never call the LLM.
"""
import re
from datetime import datetime
from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# Benefit-risk linkage
# ---------------------------------------------------------------------------

# Section-specific benefit-risk linkage sentences.
# MDCG 2022-21 requires every section to connect its findings back to the
# overall benefit-risk determination.  Generic boilerplate weakens the PSUR;
# each sentence should reflect what the section actually contributes.
_BENEFIT_RISK_LINK_SENTENCES = {
    "B_scope_and_device_description": (
        "The device description, intended purpose, and known contraindications "
        "form the baseline against which the benefit-risk profile is evaluated."
    ),
    "C_volume_of_sales_and_population_exposure": (
        "The volume of sales and estimated population exposure provide the "
        "denominators essential for interpreting complaint rates within the "
        "overall benefit-risk assessment."
    ),
    "D_information_on_serious_incidents": (
        "The serious incident data reviewed in this section are a primary input "
        "to the overall benefit-risk determination and have been considered "
        "in the conclusions drawn in Section M."
    ),
    "E_customer_feedback": (
        "Customer feedback, including non-complaint observations, has been "
        "considered as part of the holistic benefit-risk evaluation for this "
        "reporting period."
    ),
    "F_product_complaint_types_counts_and_rates": (
        "The complaint types, counts, and rates presented above are key metrics "
        "informing the benefit-risk assessment; any exceedances or emerging "
        "patterns have been evaluated for their impact on the overall "
        "benefit-risk profile."
    ),
    "G_information_from_trend_reporting": (
        "The trend analysis and any control limit assessments inform the "
        "ongoing benefit-risk evaluation by identifying changes in complaint "
        "patterns over time."
    ),
    "H_information_from_fsca": (
        "Any field safety corrective actions, or the confirmed absence thereof, "
        "are factored into the overall benefit-risk determination."
    ),
    "I_corrective_and_preventive_actions": (
        "Corrective and preventive actions undertaken during this period directly "
        "support the maintenance of an acceptable benefit-risk profile."
    ),
    "J_scientific_literature_review": (
        "The scientific literature findings have been assessed for their "
        "relevance to the device's safety and performance and are reflected "
        "in the benefit-risk evaluation."
    ),
    "K_review_of_external_databases_and_registries": (
        "External database and registry data for this device and similar "
        "devices have been considered in the overall benefit-risk assessment."
    ),
    "L_pmcf": (
        "Post-market clinical follow-up data, or the rationale for its absence, "
        "are integral to confirming the ongoing acceptability of the "
        "benefit-risk profile."
    ),
}

# Fallback for any section not in the map above
_BENEFIT_RISK_LINK_FALLBACK = (
    "These findings are considered in the overall benefit-risk profile and "
    "do not change the current benefit-risk determination."
)


def _append_benefit_risk_link(text: str, section_key: str = "") -> str:
    if not isinstance(text, str):
        return text
    if "benefit-risk" in text.lower() or "benefit risk" in text.lower():
        return text
    text = text.strip()
    if not text:
        return text
    sentence = _BENEFIT_RISK_LINK_SENTENCES.get(section_key, _BENEFIT_RISK_LINK_FALLBACK)
    if text.endswith((".", "!", "?")):
        return f"{text} {sentence}"
    return f"{text}. {sentence}"


def enforce_benefit_risk_link(section_key: str, section_content: Any) -> Any:
    """Ensure every section contains a benefit-risk linkage sentence.

    MDCG 2022-21 requires a benefit-risk thread through every PSUR section.
    This function finds the last substantive narrative field in the section
    and appends the linkage sentence if not already present.
    """
    if not isinstance(section_content, dict):
        return section_content

    _SECTION_NARRATIVE_TARGETS = {
        "A_executive_summary": [
            ("benefit_risk_assessment_conclusion", "summary"),
        ],
        "B_scope_and_device_description": [
            ("device_description_and_information", "description"),
            ("device_description_and_information", "intended_purpose_use"),
        ],
        "C_volume_of_sales_and_population_exposure": [
            ("sales_data_analysis", "narrative_analysis"),
            ("size_and_characteristics_of_population_using_device", "estimated_size"),
        ],
        "D_information_on_serious_incidents": [
            (None, "narrative_summary"),
            (None, "new_incident_types_identified_this_cycle"),
        ],
        "E_customer_feedback": [
            (None, "summary"),
        ],
        "F_product_complaint_types_counts_and_rates": [
            ("annual_number_of_complaints_and_complaint_rate_by_harm_and_medical_device_problem", "commentary_context_for_exceedances"),
            ("complaint_rate_calculation", "method_description_and_justification"),
        ],
        "G_information_from_trend_reporting": [
            ("overall_monthly_complaint_rate_trending", "breaches_commentary_and_actions"),
            ("trend_reporting_summary", "statement_if_not_applicable"),
        ],
        "H_information_from_fsca": [
            (None, "summary_or_na_statement"),
        ],
        "I_corrective_and_preventive_actions": [
            (None, "summary_or_na_statement"),
        ],
        "J_scientific_literature_review": [
            (None, "summary_of_new_data_performance_or_safety"),
            (None, "comparison_with_similar_devices"),
        ],
        "K_review_of_external_databases_and_registries": [
            (None, "registries_reviewed_summary"),
        ],
        "L_pmcf": [
            (None, "summary_or_na_statement"),
        ],
    }

    targets = _SECTION_NARRATIVE_TARGETS.get(section_key, [])
    applied = False

    for parent_key, field_key in targets:
        if applied:
            break
        if parent_key:
            parent = section_content.get(parent_key)
            if isinstance(parent, dict):
                narrative = parent.get(field_key, "")
                if isinstance(narrative, str) and narrative.strip():
                    parent[field_key] = _append_benefit_risk_link(narrative, section_key)
                    applied = True
        else:
            narrative = section_content.get(field_key, "")
            if isinstance(narrative, str) and narrative.strip():
                section_content[field_key] = _append_benefit_risk_link(narrative, section_key)
                applied = True

    if not applied:
        for fallback_key in ("narrative_summary", "summary", "summary_or_na_statement"):
            narrative = section_content.get(fallback_key, "")
            if isinstance(narrative, str) and narrative.strip():
                section_content[fallback_key] = _append_benefit_risk_link(narrative, section_key)
                break

    return section_content


# ---------------------------------------------------------------------------
# Table repair
# ---------------------------------------------------------------------------

_COUNT_KEYS = frozenset({
    "count", "n", "n_current_period", "complaint_count", "number_of_serious_incidents",
    "current_12_month_complaint_count", "preceding_period_complaint_count",
    "number_of_fsca", "number_of_capa", "total",
})

_RATE_KEYS = frozenset({
    "rate", "rate_percent", "complaint_rate", "complaint_percentage",
    "current_12_month_complaint_rate", "preceding_period_complaint_rate",
    "max_expected_rate_of_occurrence_from_ract", "max_expected", "percent",
    "ract_max_expected_rate", "ract_ratio",
})


def _infer_cell_default(key: str, sibling_vals: list) -> Any:
    """Infer a sensible default for an empty cell based on key name and siblings."""
    kl = key.lower()
    if kl in _COUNT_KEYS or kl.endswith("_count"):
        return 0
    if kl in _RATE_KEYS or kl.endswith("_rate") or kl.endswith("_percent") or kl.endswith("_percentage"):
        return 0.00
    numeric_vals = [v for v in sibling_vals if isinstance(v, (int, float))]
    if numeric_vals and len(numeric_vals) == len(sibling_vals):
        return 0
    return "N/A"


def repair_section_tables(section_content: Any) -> Any:
    """Walk a section dict, find all table arrays, fill empty cells and ensure non-empty.

    Rules:
    1. Every cell in a table row that is None or "" gets a type-appropriate default.
    2. If a table array is empty ([]), leave as-is (handled by data warnings).
    3. Harm header rows (count=null, rate=null) are exempt — intentional grouping rows.
    """
    if not isinstance(section_content, dict):
        return section_content

    def _is_harm_header_row(row: dict) -> bool:
        harm = row.get("harm", "")
        mdp = row.get("medical_device_problem", "")
        return bool(harm) and not mdp

    def _repair_table(rows: list, table_path: str) -> list:
        if not rows:
            return rows
        all_keys = set()
        for r in rows:
            if isinstance(r, dict):
                all_keys.update(k for k in r.keys() if not k.startswith("_"))
        for row in rows:
            if not isinstance(row, dict):
                continue
            if _is_harm_header_row(row):
                if not row.get("harm"):
                    row["harm"] = "N/A"
                continue
            for key in all_keys:
                if key.startswith("_"):
                    continue
                val = row.get(key)
                if val is None or (isinstance(val, str) and val.strip() == ""):
                    siblings = [
                        r.get(key) for r in rows
                        if isinstance(r, dict) and r.get(key) is not None
                        and not (isinstance(r.get(key), str) and r.get(key).strip() == "")
                    ]
                    row[key] = _infer_cell_default(key, siblings)
        return rows

    def _ensure_non_empty_tables(obj: Any, path: str) -> Any:
        if isinstance(obj, dict):
            for k, v in list(obj.items()):
                if k.startswith("_"):
                    continue
                child_path = f"{path}.{k}" if path else k
                if isinstance(v, list):
                    if v and isinstance(v[0], dict):
                        obj[k] = _repair_table(v, child_path)
                    elif not v:
                        pass
                    else:
                        obj[k] = _ensure_non_empty_tables(v, child_path)
                else:
                    obj[k] = _ensure_non_empty_tables(v, child_path)
        elif isinstance(obj, list):
            return [_ensure_non_empty_tables(item, f"{path}[{i}]") for i, item in enumerate(obj)]
        return obj

    return _ensure_non_empty_tables(section_content, "")


# ---------------------------------------------------------------------------
# Table 7 grand-total deduplication
# ---------------------------------------------------------------------------


def fix_table7_grand_total(section_content: Any) -> Any:
    """Remove duplicate grand total row from Table 7 rows."""
    if not isinstance(section_content, dict):
        return section_content

    t7 = section_content.get("table_7_complaint_rate_and_count")
    if not isinstance(t7, dict):
        return section_content

    annual = t7.get("annual_format")
    if not isinstance(annual, dict):
        return section_content

    rows = annual.get("rows")
    if not isinstance(rows, list):
        return section_content

    filtered = [
        r for r in rows
        if not (
            isinstance(r, dict)
            and isinstance(r.get("harm", ""), str)
            and "grand total" in r.get("harm", "").lower()
        )
    ]

    if len(filtered) < len(rows):
        annual["rows"] = filtered

    return section_content


# ---------------------------------------------------------------------------
# Enum normalisation
# ---------------------------------------------------------------------------


def normalize_enum_values(section_key: str, section_content: Any) -> Any:
    """Normalize common LLM enum value mistakes to schema-valid values."""
    if not isinstance(section_content, dict):
        return section_content

    if section_key == "I_corrective_and_preventive_actions":
        table9 = section_content.get("table_9_capa_initiated_current_reporting_period", [])
        _STATUS_MAP = {
            "open": "Open", "closed": "Closed", "in progress": "In Progress",
            "in_progress": "In Progress", "completed": "Closed",
            "not started": "Open", "not_started": "Open",
            "n/a": "Closed", "not applicable": "Closed", "not_applicable": "Closed",
            "implemented": "Closed", "verified": "Closed",
        }
        for row in table9:
            if isinstance(row, dict):
                status = row.get("status", "")
                if isinstance(status, str) and status not in ("Open", "Closed", "In Progress"):
                    row["status"] = _STATUS_MAP.get(status.lower().strip(), "Open")

    if section_key == "H_information_from_fsca":
        table8 = section_content.get("table_8_fsca_initiated_current_period_and_open_fscas", [])
        _FSCA_STATUS_MAP = {
            "open": "Open", "closed": "Closed", "in progress": "In Progress",
            "in_progress": "In Progress", "completed": "Closed",
        }
        for row in table8:
            if isinstance(row, dict):
                status = row.get("status", "")
                if isinstance(status, str) and status not in ("Open", "Closed", "In Progress"):
                    row["status"] = _FSCA_STATUS_MAP.get(status.lower().strip(), "Open")

    return section_content


# ---------------------------------------------------------------------------
# UDI-DI fabrication fix
# ---------------------------------------------------------------------------


def fix_fabricated_udi_di(
    section_key: str, section_content: Any, device_context: Dict[str, Any]
) -> Any:
    """Replace fabricated UDI-DIs with actual value from device_context or 'N/A'."""
    if section_key != "B_scope_and_device_description" or not isinstance(section_content, dict):
        return section_content

    known = device_context.get("known_identifiers", {})
    actual_udi = known.get("basic_udi_di", "")

    dev_info = section_content.get("device_information_breakdown", {})
    mdr_devices = dev_info.get("mdr_devices", {})
    rows = mdr_devices.get("basic_udi_di_rows", [])

    for row in rows:
        udi = row.get("basic_udi_di", "")
        if udi and len(udi) < 10:
            row["basic_udi_di"] = actual_udi if actual_udi else "N/A"

    return section_content


# ---------------------------------------------------------------------------
# Empty / placeholder table fix
# ---------------------------------------------------------------------------


def fix_empty_and_placeholder_tables(section_key: str, section_content: Any) -> Any:
    """Fix empty tables and placeholder rows with non-schema keys."""
    if not isinstance(section_content, dict):
        return section_content

    _NA_ROWS = {
        "table_6_feedback_by_type_and_source": [{
            "feedback_type": "N/A", "source": "N/A", "count": 0,
            "summary": "No structured customer feedback was collected separately from the formal complaint process during this reporting period."
        }],
        "table_8_fsca_initiated_current_period_and_open_fscas": [{
            "type_of_action": "N/A", "manufacturer_reference_number": "N/A",
            "issuing_date": "N/A", "scope": "N/A", "status": "Closed",
            "rationale_and_description": "No FSCAs were initiated during this reporting period.",
            "impacted_regions": "N/A", "date_reported_to_mhra": "N/A"
        }],
        "table_10_adverse_events_and_recalls": [{
            "database_registry": "N/A", "total_matches": 0,
            "relevant_findings": "No formal external database review was conducted during this reporting period.",
            "benchmark_vs_similar_devices": "N/A",
            "regulatory_actions_affecting_similar_devices": "N/A",
            "rmf_update_reference": "N/A"
        }],
        "table_11_pmcf_activities": [{
            "specific_pmcf_activity": "N/A",
            "key_findings": "No PMCF data was available for this reporting period.",
            "impact_on_safety_performance": "N/A",
            "rmf_cer_update": "N/A",
            "pmcf_evaluation_report_reference": "N/A"
        }],
    }

    def _fix(obj):
        if isinstance(obj, dict):
            for k, v in list(obj.items()):
                if k.startswith("_"):
                    continue
                if isinstance(v, list):
                    if v and all(isinstance(r, dict) and set(r.keys()) == {"note"} for r in v):
                        obj[k] = _NA_ROWS.get(k, [])
                    elif not v and k in _NA_ROWS:
                        obj[k] = _NA_ROWS[k]
                    else:
                        _fix(v)
                elif isinstance(v, dict):
                    _fix(v)
        elif isinstance(obj, list):
            for item in obj:
                if isinstance(item, (dict, list)):
                    _fix(item)

    _fix(section_content)
    return section_content


# ---------------------------------------------------------------------------
# Period normalisation
# ---------------------------------------------------------------------------


def compute_period_months(
    device_context: Dict[str, Any], stats_dict: Dict[str, Any]
) -> Optional[int]:
    """Compute reporting period duration in months."""
    period = stats_dict.get("surveillance_period")
    if isinstance(period, dict):
        start = period.get("start_date")
        end = period.get("end_date")
        if isinstance(start, str) and isinstance(end, str):
            try:
                s = datetime.strptime(start, "%Y-%m-%d")
                e = datetime.strptime(end, "%Y-%m-%d")
                return max(1, (e.year - s.year) * 12 + (e.month - s.month) + 1)
            except Exception:
                pass

    start = device_context.get("period_start")
    end = device_context.get("period_end")
    if isinstance(start, str) and isinstance(end, str):
        try:
            s = datetime.strptime(start, "%Y-%m-%d")
            e = datetime.strptime(end, "%Y-%m-%d")
            return max(1, (e.year - s.year) * 12 + (e.month - s.month) + 1)
        except Exception:
            return None
    return None


# ---------------------------------------------------------------------------
# Regulation citation stripping
# ---------------------------------------------------------------------------

# Patterns that reference specific regulation articles / guidance documents.
# These should never appear in narrative prose (internal doc refs like
# ISO 14971 or IEC 62366 are allowed).
_REGULATION_CITATION_PATTERNS = [
    # Order matters: longer/more-specific patterns MUST come first so that
    # e.g. "MDR Annex XIV Part A" is eaten whole before the generic "MDR" rule.
    # "Regulation (EU) 2017/745" or "Regulation 2017/745"
    re.compile(r"Regulation\s*\(?EU\)?\s*2017\s*/\s*745", re.IGNORECASE),
    # "MDR Annex XIV/VII/VIII" etc. (BEFORE the generic MDR pattern)
    re.compile(r"\b(?:the\s+)?(?:EU\s+)?MDR\s+Annex\s+[IVXLCDM]+(?:\s+Part\s+[A-Z])?", re.IGNORECASE),
    # "MDR Article XX" / "Article XX of the MDR" / "MDR Art. XX(Y)"
    re.compile(
        r"(?:(?:the\s+)?(?:EU\s+)?MDR\s+)?Art(?:icle)?\s*\.?\s*\d+(?:\s*\(\d+\))*(?:\s+of\s+(?:the\s+)?MDR)?",
        re.IGNORECASE,
    ),
    # "MDCG 2022-21" or "MDCG 2022/21" or other MDCG guidance refs
    re.compile(r"MDCG\s+\d{4}[-/]\d+(?:\s+Rev\.?\s*\d+)?", re.IGNORECASE),
    # "MEDDEV 2.7/1 Rev 4" or similar
    re.compile(r"MEDDEV\s+\d+\.\d+/\d+(?:\s+Rev\.?\s*\d+)?", re.IGNORECASE),
    # "per MDR" / "under EU MDR requirements" (AFTER specific MDR patterns)
    re.compile(r"\bper\s+(?:the\s+)?(?:EU\s+)?MDR\b", re.IGNORECASE),
    # "EU MDR", "the MDR", "MDR 2017/745" (LAST — generic catch-all)
    re.compile(r"\b(?:the\s+)?EU\s+MDR\b(?:\s+2017/745)?", re.IGNORECASE),
]


def strip_regulation_citations(value: Any) -> Any:
    """Recursively strip regulation article citations from narrative text.

    Removes references like 'EU MDR', 'MDR Article 86(2)', 'MDCG 2022-21',
    'Regulation (EU) 2017/745', and 'MEDDEV 2.7/1 Rev 4' from all string
    values.  Internal document references (ISO 14971, IEC 62366, EN ISO
    standards) are intentionally preserved.
    """
    if isinstance(value, str):
        result = value
        for pattern in _REGULATION_CITATION_PATTERNS:
            result = pattern.sub("", result)
        # Clean up artefacts: double spaces, orphaned commas/semicolons,
        # "as required by " with nothing after, "in accordance with  ," etc.
        result = re.sub(r"\s{2,}", " ", result)
        result = re.sub(r"\s+([,;.])", r"\1", result)
        result = re.sub(
            r"(?:as\s+required\s+by|in\s+accordance\s+with|pursuant\s+to|per|under)\s*[,;.]",
            "",
            result,
            flags=re.IGNORECASE,
        )
        result = re.sub(r"\s{2,}", " ", result)
        return result.strip()

    if isinstance(value, list):
        return [strip_regulation_citations(v) for v in value]

    if isinstance(value, dict):
        return {k: strip_regulation_citations(v) for k, v in value.items()}

    return value


# ---------------------------------------------------------------------------
# Marketing language scrubbing
# ---------------------------------------------------------------------------

_MARKETING_PATTERNS = [
    # superlative / comparative marketing phrases
    re.compile(r"\bsuperior\s+performance\b", re.IGNORECASE),
    re.compile(r"\bbest[- ]in[- ]class\b", re.IGNORECASE),
    re.compile(r"\bmarket[- ]leading\b", re.IGNORECASE),
    re.compile(r"\bworld[- ]class\b", re.IGNORECASE),
    re.compile(r"\bcutting[- ]edge\b", re.IGNORECASE),
    re.compile(r"\bgold\s+standard\b", re.IGNORECASE),
    re.compile(r"\bindustry[- ]leading\b", re.IGNORECASE),
    re.compile(r"\boutstanding\s+(?:safety|performance|quality)\b", re.IGNORECASE),
    # comparative claims without evidence
    re.compile(r"\bcompares\s+favorably\b", re.IGNORECASE),
    re.compile(r"\bcompares\s+favourably\b", re.IGNORECASE),
    re.compile(r"\bsignificantly\s+(?:better|lower|superior)\b", re.IGNORECASE),
    # invented benchmarks
    re.compile(r"\bindustry\s+average\b", re.IGNORECASE),
    re.compile(r"\bmarket\s+average\b", re.IGNORECASE),
]

_MARKETING_SENTENCE_RE = re.compile(r"[^.]*?(?:" + "|".join(
    p.pattern for p in _MARKETING_PATTERNS
) + r")[^.]*\.", re.IGNORECASE)


def strip_marketing_language(value: Any) -> Any:
    """Recursively strip marketing/promotional language from narrative text.

    PSURs are regulatory documents — promotional claims like 'superior
    performance', 'best-in-class', 'compares favorably', or invented
    'industry average' benchmarks are inappropriate and undermine
    regulatory credibility.

    Strategy: remove entire sentences containing marketing phrases rather
    than leaving grammatically broken fragments.
    """
    if isinstance(value, str):
        result = value
        # Remove whole sentences containing marketing language
        result = _MARKETING_SENTENCE_RE.sub("", result)
        # Clean up artefacts: double spaces, leading/trailing whitespace
        result = re.sub(r"\s{2,}", " ", result)
        return result.strip()

    if isinstance(value, list):
        return [strip_marketing_language(v) for v in value]

    if isinstance(value, dict):
        return {k: strip_marketing_language(v) for k, v in value.items()}

    return value


def normalize_period_mentions(value: Any, period_months: Optional[int]) -> Any:
    """Fix LLM-hallucinated period durations in string fields.

    Only corrects mentions that clearly refer to the reporting/surveillance
    period — NOT standalone month durations like "3-month shelf life" or
    "6-month follow-up".
    """
    if period_months is None:
        return value

    if isinstance(value, str):
        # Only replace "X-month period" or "X-month reporting period" or
        # "X-month surveillance period" or "X-month data collection period"
        _period_context_re = re.compile(
            r"\b(\d+)\s*-?\s*month(?:s)?\s+"
            r"((?:reporting|surveillance|data\s+collection|review|PSUR)\s+period)",
            re.IGNORECASE,
        )

        def _replace_match(m):
            claimed = int(m.group(1))
            context = m.group(2)
            if claimed != period_months and 6 <= claimed <= 120:
                return f"{period_months}-month {context}"
            return m.group(0)

        value = _period_context_re.sub(_replace_match, value)
        return value

    if isinstance(value, list):
        return [normalize_period_mentions(v, period_months) for v in value]

    if isinstance(value, dict):
        for k, v in value.items():
            value[k] = normalize_period_mentions(v, period_months)
        return value

    return value


# ── Sterile/non-sterile contradiction fix ────────────────────────────

_STERILE_CONTRADICTION_PATTERNS = [
    # Phrases that wrongly call a non-sterile device sterile
    (re.compile(r"\bsterile\s+single[- ]use\s+devices?\b", re.IGNORECASE), "single-use devices"),
    (re.compile(r"\bsterile\s+single[- ]use\s+medical\s+devices?\b", re.IGNORECASE), "single-use medical devices"),
    (re.compile(r"\bsterile\s+disposable\s+devices?\b", re.IGNORECASE), "disposable devices"),
    (re.compile(r"\bsterilisation\s+validation\b", re.IGNORECASE), "manufacturing validation"),
    (re.compile(r"\bsterilization\s+validation\b", re.IGNORECASE), "manufacturing validation"),
]


def fix_sterile_contradictions(value: Any, is_sterile: bool) -> Any:
    """Fix sterile/non-sterile terminology contradictions.

    When the device is non-sterile, replace phrases that wrongly
    describe it as sterile. When the device is sterile, this is a no-op.
    """
    if is_sterile:
        return value  # No fix needed for actually sterile devices

    if isinstance(value, str):
        result = value
        for pattern, replacement in _STERILE_CONTRADICTION_PATTERNS:
            result = pattern.sub(replacement, result)
        return result

    if isinstance(value, list):
        return [fix_sterile_contradictions(v, is_sterile) for v in value]

    if isinstance(value, dict):
        return {k: fix_sterile_contradictions(v, is_sterile) for k, v in value.items()}

    return value


# ── Single-use / non-single-use contradiction fix ────────────────────

_SINGLE_USE_CONTRADICTION_PATTERNS = [
    # Phrases that wrongly call a single-use device non-single-use or reusable
    (re.compile(r"\bnon[- ]single[- ]use\s+devices?\b", re.IGNORECASE), "single-use devices"),
    (re.compile(r"\bthese\s+are\s+non[- ]single[- ]use\s+devices?\b", re.IGNORECASE), "these are single-use devices"),
    (re.compile(r"\breusable\s+medical\s+devices?\b", re.IGNORECASE), "single-use medical devices"),
    (re.compile(r"\breusable\s+devices?\b", re.IGNORECASE), "single-use devices"),
    (re.compile(r"\bmulti[- ]use\s+devices?\b", re.IGNORECASE), "single-use devices"),
    (re.compile(r"\bnon[- ]disposable\s+devices?\b", re.IGNORECASE), "disposable devices"),
]

_REUSABLE_CONTRADICTION_PATTERNS = [
    # Phrases that wrongly call a reusable device single-use
    (re.compile(r"\bsingle[- ]use\s+devices?\b", re.IGNORECASE), "reusable devices"),
    (re.compile(r"\bdisposable\s+devices?\b", re.IGNORECASE), "reusable devices"),
]


def fix_single_use_contradictions(value: Any, is_single_use: bool) -> Any:
    """Fix single-use/reusable terminology contradictions.

    When the device is single-use, replace phrases that wrongly describe it
    as reusable or non-single-use. When the device is reusable, replace
    phrases that wrongly describe it as single-use.
    """
    patterns = _SINGLE_USE_CONTRADICTION_PATTERNS if is_single_use else _REUSABLE_CONTRADICTION_PATTERNS

    if isinstance(value, str):
        result = value
        for pattern, replacement in patterns:
            result = pattern.sub(replacement, result)
        return result

    if isinstance(value, list):
        return [fix_single_use_contradictions(v, is_single_use) for v in value]

    if isinstance(value, dict):
        return {k: fix_single_use_contradictions(v, is_single_use) for k, v in value.items()}

    return value


# ── Manufacturer identity consistency fix ────────────────────────────

def fix_manufacturer_consistency(value: Any, manufacturer_name: str) -> Any:
    """Replace fabricated manufacturer names with the correct one.

    The LLM sometimes invents alternative manufacturer names (e.g.,
    "Neotech Products LLC" instead of "CooperSurgical, Inc."). This
    function scans all string fields and replaces known fabricated names.
    """
    if not manufacturer_name or not manufacturer_name.strip():
        return value

    # Build dynamic pattern: detect "manufacturer" context followed by a
    # company name that doesn't match the real one.  Also fix specific
    # common fabrication patterns.
    _KNOWN_FABRICATED_MANUFACTURERS = [
        "Neotech Products LLC",
        "Neotech Products, LLC",
        "Neotech Products Inc",
        "Neotech Products, Inc",
        "Neotech Medical",
        "Neotech",  # standalone as manufacturer name
    ]

    if isinstance(value, str):
        result = value
        for fake in _KNOWN_FABRICATED_MANUFACTURERS:
            if fake.lower() in result.lower():
                # Case-insensitive replacement preserving surrounding text
                result = re.sub(re.escape(fake), manufacturer_name, result, flags=re.IGNORECASE)
        return result

    if isinstance(value, list):
        return [fix_manufacturer_consistency(v, manufacturer_name) for v in value]

    if isinstance(value, dict):
        return {k: fix_manufacturer_consistency(v, manufacturer_name) for k, v in value.items()}

    return value


# ── Notified Body reference stripping for Class I devices ────────────

_NB_NARRATIVE_PATTERNS = [
    # Phrases implying NB involvement that should be removed for Class I
    re.compile(
        r"(?:The\s+)?[Nn]otified\s+[Bb]ody\s+(?:BSI|TÜV|SGS|Dekra|GMED|IMQ|UL|Intertek)"
        r"[^.]*?\.",
        re.IGNORECASE
    ),
    re.compile(
        r"[^.]*?(?:submitted\s+to|reviewed\s+by|approved\s+by)\s+(?:the\s+)?[Nn]otified\s+[Bb]ody[^.]*?\.",
        re.IGNORECASE
    ),
    re.compile(
        r"[^.]*?NB\s+(?:review|audit|opinion|observation|finding|closure)[^.]*?\.",
        re.IGNORECASE
    ),
    re.compile(
        r"[^.]*?[Cc]ertificate\s+number\s+[A-Z0-9-]+[^.]*?\.",
        re.IGNORECASE
    ),
]


def strip_nb_references_class_i(value: Any, class_i_no_nb: bool) -> Any:
    """Strip Notified Body narrative references for Class I non-sterile devices.

    This is a safety net that removes NB review/audit/opinion sentences from
    narratives when the device is Class I self-certified.  Cover page NB fields
    are left intact (they are administrative template fields).
    """
    if not class_i_no_nb:
        return value

    if isinstance(value, str):
        result = value
        for pattern in _NB_NARRATIVE_PATTERNS:
            result = pattern.sub("", result)
        # Clean up double spaces left by removals
        result = re.sub(r"  +", " ", result).strip()
        return result

    if isinstance(value, list):
        return [strip_nb_references_class_i(v, class_i_no_nb) for v in value]

    if isinstance(value, dict):
        return {k: strip_nb_references_class_i(v, class_i_no_nb) for k, v in value.items()}

    return value


# ── Cadence table cleanup ────────────────────────────────────────────

def strip_wrong_cadence_tables(section_key: str, section_content: Dict[str, Any],
                                psur_cadence: str) -> Dict[str, Any]:
    """Remove table variants that don't match the PSUR cadence.

    The template schema includes both 'annual' and 'every_two_years' table
    variants. Only the cadence-appropriate variant should be populated; the
    other should be empty/null.
    """
    if not isinstance(section_content, dict):
        return section_content

    is_biennial = psur_cadence.upper() in ("EVERY_TWO_YEARS", "BIENNIAL", "EVERY TWO YEARS")

    # Section C: Table 1 variants
    if section_key == "C_volume_of_sales_and_population_exposure":
        t1 = section_content.get("table_1_volume_of_sales_and_population_exposure", {})
        if isinstance(t1, dict):
            if is_biennial:
                # Keep every_two_years, clear annual
                if "annual_format" in t1 and "every_two_years_format" in t1:
                    t1["annual_format"] = None
            else:
                # Keep annual, clear every_two_years
                if "every_two_years_format" in t1 and "annual_format" in t1:
                    t1["every_two_years_format"] = None

    # Section F: Table 7 variants
    if section_key == "F_product_complaint_types_counts_and_rates":
        t7 = section_content.get("table_7_complaint_types_counts_and_rates", {})
        if isinstance(t7, dict):
            if is_biennial:
                if "annual_format" in t7 and "every_two_years_format" in t7:
                    t7["annual_format"] = None
            else:
                if "every_two_years_format" in t7 and "annual_format" in t7:
                    t7["every_two_years_format"] = None

    return section_content
