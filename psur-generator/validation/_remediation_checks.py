"""Remediation checks mixin — closes gaps surfaced by the May 2026 audit.

Each method implements one validator_check key declared on a rule in
``knowledge/rules/remediation_findings.json``. Methods are intentionally
defensive: they walk the PSUR JSON heuristically because section schemas
allow flexible substructure.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple


_NO_CAPA_PHRASES = [
    r"no\s+capa\s+(?:was\s+)?initiated",
    r"no\s+corrective\s+(?:and\s+preventive\s+)?actions?\s+(?:were|was)\s+(?:required|initiated|opened)",
    r"no\s+capas?\s+(?:were|was)\s+opened",
]
_PMCF_DEFERRAL_PHRASES = [
    r"pmcf\s+data\s+(?:are|is)?\s*maintained\s+separately",
    r"see\s+(?:the\s+)?pmcf\s+(?:plan|report|file)\b",
    r"refer\s+to\s+(?:the\s+)?pmcf\s+(?:plan|report|file)\b",
]
_PMCF_JUSTIFICATION_PHRASES = [
    r"pmcf\s+(?:is\s+)?not\s+required",
    r"annex\s+xiv\s+part\s+b\b.{0,40}(?:1\.2|criteri)",
    r"justification\s+for\s+(?:the\s+)?absence\s+of\s+pmcf",
]


def _s(v: Any) -> str:
    """Safely coerce a JSON value to a stripped string. Returns '' for None / dict / list."""
    if v is None:
        return ""
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, (int, float, bool)):
        return str(v).strip()
    return ""


def _flatten_strings(obj: Any) -> List[str]:
    out: List[str] = []
    if isinstance(obj, str):
        if obj.strip():
            out.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            out.extend(_flatten_strings(v))
    elif isinstance(obj, list):
        for v in obj:
            out.extend(_flatten_strings(v))
    return out


def _all_text(obj: Any) -> str:
    return "\n".join(_flatten_strings(obj)).lower()


def _section(psur: Dict[str, Any], letter: str) -> Dict[str, Any]:
    sections = psur.get("sections") or {}
    for k, v in sections.items():
        if isinstance(v, dict) and k.startswith(f"{letter}_"):
            return v
    return {}


def _find_tables(section: Dict[str, Any], name_substr: str) -> List[Any]:
    """Return all values under section whose key contains ``name_substr``."""
    matches: List[Any] = []

    def walk(node: Any):
        if isinstance(node, dict):
            for k, v in node.items():
                if name_substr in k.lower():
                    matches.append(v)
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(section)
    return matches


def _table_rows(table_obj: Any) -> List[Dict[str, Any]]:
    if isinstance(table_obj, list):
        return [r for r in table_obj if isinstance(r, dict)]
    if isinstance(table_obj, dict):
        for key in ("rows", "items", "entries", "data"):
            if isinstance(table_obj.get(key), list):
                return [r for r in table_obj[key] if isinstance(r, dict)]
    return []


def _int_from(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        m = re.search(r"-?\d+", value)
        if m:
            try:
                return int(m.group(0))
            except ValueError:
                return 0
    return 0


def _narrative_integer_claims(text: str, keyword: str) -> List[int]:
    """Find integers in narrative immediately preceding ``keyword``."""
    out: List[int] = []
    pattern = re.compile(r"\b(\d{1,5})\s+" + re.escape(keyword), re.IGNORECASE)
    for m in pattern.finditer(text):
        try:
            out.append(int(m.group(1)))
        except ValueError:
            pass
    return out


class RemediationChecksMixin:
    """Audit-driven checks (F-001 through F-013)."""

    # ── F-001 ────────────────────────────────────────────────────────
    def _check_incident_count_cross_section_consistency(
        self, psur: Dict[str, Any], *_: Any
    ) -> List[str]:
        errors: List[str] = []
        sec_d = _section(psur, "D")
        if not sec_d:
            return errors
        narrative = _all_text(sec_d)
        claims = _narrative_integer_claims(narrative, "serious incident")
        claims += _narrative_integer_claims(narrative, "serious incidents")
        narrative_total = max(claims) if claims else None

        # Sum every numeric cell in any 'serious_incident' table
        tables = _find_tables(sec_d, "serious_incident")
        table_sum = 0
        any_table = False
        for tbl in tables:
            for row in _table_rows(tbl):
                any_table = True
                # Sum count-like fields
                for k, v in row.items():
                    if any(t in k.lower() for t in (
                        "count", "total", "incidents", "number", "n_", "qty",
                    )) and not isinstance(v, (dict, list)):
                        table_sum += _int_from(v)

        if narrative_total is not None and narrative_total > 0 and any_table and table_sum == 0:
            errors.append(
                f"F-001 [CRITICAL]: Section D narrative claims {narrative_total} "
                f"serious incident(s) but all Section D table cells sum to 0."
            )
        elif narrative_total is not None and any_table and table_sum > 0:
            if abs(narrative_total - table_sum) > 0:
                errors.append(
                    f"F-001 [CRITICAL]: Section D narrative claims {narrative_total} "
                    f"serious incident(s) but tables total {table_sum}."
                )
        return errors

    # ── F-002 ────────────────────────────────────────────────────────
    def _check_capa_flag_matches_list(
        self, psur: Dict[str, Any], *_: Any
    ) -> List[str]:
        errors: List[str] = []
        sec_i = _section(psur, "I")
        if not sec_i:
            return errors
        tables = _find_tables(sec_i, "capa")
        capa_rows: List[Dict[str, Any]] = []
        for t in tables:
            capa_rows.extend(_table_rows(t))
        narrative = _all_text(sec_i)
        narrative_says_no = any(
            re.search(p, narrative) for p in _NO_CAPA_PHRASES
        )
        if capa_rows and narrative_says_no:
            errors.append(
                f"F-002 [CRITICAL]: Section I narrative states 'no CAPA' "
                f"but CAPA table contains {len(capa_rows)} row(s)."
            )
        return errors

    # ── F-003 ────────────────────────────────────────────────────────
    def _check_pmcf_summary_or_justification_present(
        self, psur: Dict[str, Any], *_: Any
    ) -> List[str]:
        errors: List[str] = []
        sec_l = _section(psur, "L")
        if not sec_l:
            return errors
        narrative = _all_text(sec_l)
        # PMCF activities table present and populated?
        tables = _find_tables(sec_l, "pmcf")
        has_rows = any(_table_rows(t) for t in tables)
        # Summary string present?
        summary = _s(sec_l.get("summary_or_na_statement"))
        has_substantive_summary = bool(summary) and len(summary.split()) >= 25
        has_justification = any(
            re.search(p, narrative) for p in _PMCF_JUSTIFICATION_PHRASES
        )
        deferred = any(re.search(p, narrative) for p in _PMCF_DEFERRAL_PHRASES)

        if deferred and not has_rows and not has_justification:
            errors.append(
                "F-003 [CRITICAL]: Section L defers PMCF content to an external "
                "document ('maintained separately' / 'see PMCF plan') without "
                "summarising findings or providing an Annex XIV Part B "
                "justification for absence."
            )
        elif not has_rows and not has_substantive_summary and not has_justification:
            errors.append(
                "F-003 [CRITICAL]: Section L lacks both a substantive PMCF "
                "summary (>=25 words) and an explicit absence-justification."
            )
        return errors

    # ── F-004 ────────────────────────────────────────────────────────
    def _check_pms_plan_execution_traceability(
        self, psur: Dict[str, Any], *_: Any
    ) -> List[str]:
        errors: List[str] = []
        full_text = _all_text(psur.get("sections") or {})
        # Detect PMS Plan identifier reference (PMS-NNNN, PMS-NNNNR, etc.)
        plan_ref = re.search(r"\bpms[\s-]?\d{3,}[a-z]?\b", full_text)
        if not plan_ref:
            return errors
        # Look for planned-vs-performed / deviation language
        has_execution = bool(re.search(
            r"planned\s+(?:vs\.?|versus)\s+performed|"
            r"executed\s+frequency|"
            r"deviation\s+(?:analysis|from\s+plan)|"
            r"pms\s+plan\s+execution",
            full_text,
        ))
        if not has_execution:
            errors.append(
                "F-004 [MAJOR]: PMS Plan is referenced "
                f"('{plan_ref.group(0)}') but no planned-vs-performed "
                "execution summary or deviation analysis is provided."
            )
        return errors

    # ── F-005 ────────────────────────────────────────────────────────
    def _check_fsca_effectiveness_metric_present(
        self, psur: Dict[str, Any], *_: Any
    ) -> List[str]:
        errors: List[str] = []
        sec_h = _section(psur, "H")
        if not sec_h:
            return errors
        tables = _find_tables(sec_h, "fsca")
        narrative = _all_text(sec_h)
        for tbl in tables:
            for row in _table_rows(tbl):
                row_text = " ".join(str(v) for v in row.values()).lower()
                # Look for an effectiveness keyword
                if not re.search(
                    r"effectiveness|kpi|complaint\s+reduction|baseline|closure\s+criteria",
                    row_text + " " + narrative,
                ):
                    fsca_id = row.get("fsca_id") or row.get("id") or row.get("reference") or "?"
                    errors.append(
                        f"F-005 [MAJOR]: FSCA '{fsca_id}' lacks an "
                        "effectiveness KPI / measurement method / closure criterion."
                    )
                    break  # one error per table is enough
        return errors

    # ── F-006 ────────────────────────────────────────────────────────
    def _check_regional_normalized_rates_present(
        self, psur: Dict[str, Any], *_: Any
    ) -> List[str]:
        errors: List[str] = []
        # Look for sales by region (Section C / Table 1)
        sec_c = _section(psur, "C")
        sales_tables = _find_tables(sec_c, "sales_by_region") + _find_tables(sec_c, "table_1")
        region_units: Dict[str, int] = {}
        for tbl in sales_tables:
            for row in _table_rows(tbl):
                region = _s(row.get("region")) or _s(row.get("market"))
                if not region:
                    continue
                for k, v in row.items():
                    if "units" in k.lower() and not isinstance(v, (dict, list)):
                        region_units[region] = region_units.get(region, 0) + _int_from(v)
        active_regions = {r for r, u in region_units.items() if u > 0}
        if not active_regions:
            return errors
        # Section F: complaint table must have a per-region rate / count column
        for letter, label in (("F", "complaint"), ("D", "incident")):
            sec = _section(psur, letter)
            tbls = _find_tables(sec, label) or _find_tables(sec, "region")
            seen_regions: set = set()
            has_rate_column = False
            for tbl in tbls:
                for row in _table_rows(tbl):
                    region = _s(row.get("region")) or _s(row.get("market"))
                    if region:
                        seen_regions.add(region)
                    if any("rate" in k.lower() for k in row.keys()):
                        has_rate_column = True
            missing = active_regions - seen_regions
            if missing and tbls:
                errors.append(
                    f"F-006 [MAJOR]: Section {letter} {label} tables omit "
                    f"region(s) {sorted(missing)} that have non-zero sales."
                )
            if tbls and not has_rate_column:
                errors.append(
                    f"F-006 [MAJOR]: Section {letter} {label} tables do not "
                    "expose a normalised per-region rate column."
                )
        return errors

    # ── F-007 ────────────────────────────────────────────────────────
    def _check_device_lifetime_defined(
        self, psur: Dict[str, Any], *_: Any
    ) -> List[str]:
        errors: List[str] = []
        sec_b = _section(psur, "B")
        ctx = psur.get("_device_context") or {}
        candidates = []
        for src in (sec_b, ctx):
            for k, v in (src or {}).items():
                if any(t in k.lower() for t in (
                    "lifetime", "shelf_life", "usage_lifecycle",
                    "pms_horizon", "pms_period", "device_life",
                )):
                    candidates.append((k, v))
        if not candidates:
            errors.append(
                "F-007 [MAJOR]: Device lifetime / shelf life / PMS horizon "
                "not declared in Section B or device context."
            )
            return errors
        blanks = [k for k, v in candidates if not _s(v) or _s(v).lower() in ("n/a", "none", "null")]
        if len(blanks) == len(candidates):
            errors.append(
                f"F-007 [MAJOR]: Device lifetime fields are present but "
                f"blank: {blanks}."
            )
        return errors

    # ── F-008 ────────────────────────────────────────────────────────
    def _check_regulatory_metadata_complete(
        self, psur: Dict[str, Any], *_: Any
    ) -> List[str]:
        errors: List[str] = []
        cover = psur.get("psur_cover_page") or {}
        reg = cover.get("regulatory_information") or {}
        cert = _s(reg.get("nb_certificate_number")) or _s(reg.get("certificate_number"))
        nb_name = _s(reg.get("issuing_notified_body")) or _s(reg.get("notified_body"))
        # Class I exemption
        dc = psur.get("_device_context") or {}
        device_class = _s(dc.get("device_class_eu")).lower()
        is_class_i = device_class.startswith("class i") and "ii" not in device_class
        if not is_class_i and (not cert or not nb_name):
            errors.append(
                f"F-008 [MAJOR]: NB certificate number and/or issuing NB "
                f"missing on cover page (cert='{cert}', nb='{nb_name}')."
            )
        # MHRA references for GB market
        uk_present = bool(reg.get("uk_responsible_person")) or bool(psur.get("_uk_market_detected"))
        if uk_present:
            mhra = reg.get("mhra_reporting_reference") or reg.get("mhra_reference")
            if not _s(mhra):
                errors.append(
                    "F-008 [MAJOR]: UK market detected but MHRA reporting "
                    "reference is missing from cover page."
                )
        return errors

    # ── F-009 ────────────────────────────────────────────────────────
    def _check_rmf_traceability_mapping(
        self, psur: Dict[str, Any], *_: Any
    ) -> List[str]:
        errors: List[str] = []
        full_text = _all_text(psur.get("sections") or {})
        rmf_ref = re.search(r"\b(?:rmf|risk\s+management\s+file)\b", full_text)
        if not rmf_ref:
            return errors
        has_mapping = bool(re.search(
            r"hazard\s+id|risk\s+control\b|hazard\s*\u2192|complaint\s*\u2192\s*hazard|"
            r"hazard\s*->\s*control",
            full_text,
        ))
        if not has_mapping:
            errors.append(
                "F-009 [MAJOR]: RMF is referenced but no complaint -> hazard "
                "-> risk-control -> RMF-reference mapping is provided."
            )
        return errors

    # ── F-013 ────────────────────────────────────────────────────────
    def _check_uk_classification_complete(
        self, psur: Dict[str, Any], *_: Any
    ) -> List[str]:
        errors: List[str] = []
        uk_detected = bool(psur.get("_uk_market_detected"))
        dc = psur.get("_device_context") or {}
        if not uk_detected and not dc.get("uk_market_detected"):
            return errors
        required = {
            "uk_mdr_classification_and_rule": dc.get("uk_mdr_classification_and_rule"),
            "uk_responsible_person": (
                dc.get("uk_responsible_person")
                or (psur.get("psur_cover_page", {}).get("regulatory_information", {}).get("uk_responsible_person"))
            ),
        }
        missing = [k for k, v in required.items() if not _s(v)]
        if missing:
            errors.append(
                f"F-013 [MAJOR]: UK market detected but UK classification "
                f"fields are blank: {missing}."
            )
        return errors
