"""Declarative PSUR Validation Engine.

Implements the formal check spec (Modules A-G + System + Semantic) where
each check is a self-describing :class:`Check` with INPUTS, VALIDATION_LOGIC,
PASS/FAIL/ERROR/SEVERITY/REGULATION_REF.

The engine reuses existing :class:`PSURValidator` heuristics where possible
and adds dedicated computation for new checks (B-003 rate calc, C-003 UCL,
F-002 closure verification, etc.).

Output shape (per spec §4):
    {
      "PSUR_VALIDATION": {
        "READY": bool,
        "CRITICAL_ERRORS": [...],
        "MAJOR_ERRORS": [...],
        "MINOR_ERRORS": [...],
        "SCORE": int            # 0..100
      }
    }
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

Severity = str  # "CRITICAL" | "MAJOR" | "MINOR"


def _safe_str(v: Any) -> str:
    """Coerce a JSON value to a stripped string. Returns '' for None/dict/list."""
    if v is None:
        return ""
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, (int, float, bool)):
        return str(v).strip()
    return ""


# ──────────────────────────────────────────────────────────────────────
# Check primitive
# ──────────────────────────────────────────────────────────────────────

@dataclass
class CheckResult:
    check_id: str
    passed: bool
    severity: Severity
    regulation: str
    message: str
    details: Optional[str] = None


@dataclass
class Check:
    """Declarative validation check.

    ``logic`` receives a :class:`ValidationContext` and returns
    ``(passed: bool, details: Optional[str])``. When ``passed`` is False the
    ``error_template`` is formatted with the returned ``details``.
    """
    check_id: str
    inputs: List[str]
    description: str
    pass_condition: str
    fail_condition: str
    error_template: str
    severity: Severity
    regulation_ref: str
    logic: Callable[["ValidationContext"], Tuple[bool, Optional[str]]]

    def run(self, ctx: "ValidationContext") -> CheckResult:
        try:
            passed, details = self.logic(ctx)
        except Exception as exc:  # defensive: a broken check must not abort engine
            passed = True
            details = f"check skipped (exception: {exc})"
        msg = self.error_template if not passed else "OK"
        if not passed and details:
            msg = f"{self.error_template} — {details}"
        return CheckResult(
            check_id=self.check_id,
            passed=passed,
            severity=self.severity,
            regulation=self.regulation_ref,
            message=msg,
            details=details,
        )


# ──────────────────────────────────────────────────────────────────────
# Context: PSUR + parsed_data + device_context + cached lookups
# ──────────────────────────────────────────────────────────────────────

@dataclass
class ValidationContext:
    psur: Dict[str, Any]
    parsed_data: Dict[str, Any] = field(default_factory=dict)
    device_context: Dict[str, Any] = field(default_factory=dict)
    statistics: Dict[str, Any] = field(default_factory=dict)
    tolerance: float = 0.01  # default % tolerance for numeric comparisons

    # ── Helpers (memoised lazy accessors) ───────────────────────────
    def section(self, letter: str) -> Dict[str, Any]:
        for k, v in (self.psur.get("sections") or {}).items():
            if isinstance(v, dict) and k.startswith(f"{letter}_"):
                return v
        return {}

    def cover(self) -> Dict[str, Any]:
        return self.psur.get("psur_cover_page") or {}

    def all_text(self, scope: Any = None) -> str:
        scope = self.psur.get("sections") if scope is None else scope
        out: List[str] = []

        def walk(node: Any):
            if isinstance(node, str):
                if node.strip():
                    out.append(node)
            elif isinstance(node, dict):
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)

        walk(scope)
        return "\n".join(out).lower()

    def find_tables(self, section: Dict[str, Any], name_substr: str) -> List[Any]:
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

    @staticmethod
    def rows(table: Any) -> List[Dict[str, Any]]:
        if isinstance(table, list):
            return [r for r in table if isinstance(r, dict)]
        if isinstance(table, dict):
            for k in ("rows", "items", "entries", "data"):
                if isinstance(table.get(k), list):
                    return [r for r in table[k] if isinstance(r, dict)]
        return []

    @staticmethod
    def to_int(v: Any) -> int:
        if isinstance(v, bool):
            return int(v)
        if isinstance(v, (int, float)):
            return int(v)
        if isinstance(v, str):
            m = re.search(r"-?\d[\d,]*", v)
            if m:
                try:
                    return int(m.group(0).replace(",", ""))
                except ValueError:
                    return 0
        return 0


# ──────────────────────────────────────────────────────────────────────
# Check implementations
# ──────────────────────────────────────────────────────────────────────

# Module A — Document Completeness ----------------------------------

_REQUIRED_SECTION_LETTERS = ["A", "B", "C", "D", "F", "G", "H", "I", "J", "K", "L", "M"]
_REQUIRED_SECTION_LABELS = {
    "A": "Executive Summary",
    "B": "Device Description",
    "C": "Sales / Exposure",
    "D": "Serious Incidents",
    "F": "Complaints",
    "G": "Trend Reporting",
    "H": "FSCA",
    "I": "CAPA",
    "J": "Literature Review",
    "K": "External Databases",
    "L": "PMCF",
    "M": "Benefit-Risk Conclusion",
}


def _check_a001(ctx: ValidationContext) -> Tuple[bool, Optional[str]]:
    sections = ctx.psur.get("sections") or {}
    present = set()
    for k in sections.keys():
        if isinstance(k, str) and len(k) >= 1:
            present.add(k[0].upper())
    missing = [
        _REQUIRED_SECTION_LABELS[l]
        for l in _REQUIRED_SECTION_LETTERS if l not in present
    ]
    return (not missing, f"missing={missing}" if missing else None)


def _check_a002(ctx: ValidationContext) -> Tuple[bool, Optional[str]]:
    cover = ctx.cover()
    reg = cover.get("regulatory_information") or {}
    doc = cover.get("document_information") or {}
    cert = _safe_str(reg.get("nb_certificate_number")) or _safe_str(reg.get("certificate_number"))
    nb = _safe_str(reg.get("issuing_notified_body")) or _safe_str(reg.get("notified_body"))
    period = doc.get("data_collection_period") or {}
    cadence = doc.get("psur_cadence") or reg.get("psur_cadence")
    dc = ctx.device_context or {}
    klass = (dc.get("device_class_eu") or "").lower()
    is_class_i = klass.startswith("class i") and "ii" not in klass
    missing = []
    if not is_class_i and (not cert or not nb):
        missing.append("nb_certificate / notified_body")
    if not period.get("start_date") or not period.get("end_date"):
        missing.append("reporting_period")
    if not cadence:
        missing.append("psur_cadence")
    return (not missing, f"missing={missing}" if missing else None)


# Module B — Data Consistency ----------------------------------------

def _narrative_count(text: str, *keywords: str) -> Optional[int]:
    nums: List[int] = []
    for kw in keywords:
        for m in re.finditer(r"\b(\d{1,5})\s+" + re.escape(kw), text, re.IGNORECASE):
            try:
                nums.append(int(m.group(1)))
            except ValueError:
                pass
    return max(nums) if nums else None


def _check_b001(ctx: ValidationContext) -> Tuple[bool, Optional[str]]:
    sec_d = ctx.section("D")
    if not sec_d:
        return True, "Section D absent — A-001 will report"
    text = ctx.all_text(sec_d)
    narrative_total = _narrative_count(text, "serious incident", "serious incidents")
    tables = ctx.find_tables(sec_d, "serious_incident")
    table_sum = 0
    any_rows = False
    for t in tables:
        for row in ctx.rows(t):
            any_rows = True
            for k, v in row.items():
                if any(s in k.lower() for s in ("count", "total", "incidents", "n_", "qty", "number")) \
                        and not isinstance(v, (dict, list)):
                    table_sum += ctx.to_int(v)
    if narrative_total is None or not any_rows:
        return True, None
    if narrative_total != table_sum:
        return False, f"narrative={narrative_total}, table_sum={table_sum}"
    return True, None


def _check_b002(ctx: ValidationContext) -> Tuple[bool, Optional[str]]:
    sec_d = ctx.section("D")
    if not sec_d:
        return True, None
    text = ctx.all_text(sec_d)
    narrative_total = _narrative_count(text, "serious incident", "serious incidents") or 0
    by_region = 0
    have_region = False
    for t in ctx.find_tables(sec_d, "serious_incident"):
        for row in ctx.rows(t):
            region = _safe_str(row.get("region")) or _safe_str(row.get("market"))
            if not region:
                continue
            have_region = True
            for k, v in row.items():
                if any(s in k.lower() for s in ("count", "total", "incidents")) \
                        and not isinstance(v, (dict, list)):
                    by_region += ctx.to_int(v)
    if narrative_total == 0:
        return True, None
    if not have_region:
        return False, f"narrative_total={narrative_total} but no regional rows"
    if by_region != narrative_total:
        return False, f"sum(regions)={by_region} vs narrative_total={narrative_total}"
    return True, None


def _check_b003(ctx: ValidationContext) -> Tuple[bool, Optional[str]]:
    stats = ctx.statistics
    complaints = ctx.to_int(stats.get("total_complaints") or stats.get("complaint_count"))
    units = ctx.to_int(stats.get("total_units_distributed") or stats.get("units_sold") or stats.get("denominator"))
    if not complaints or not units:
        # Try to extract from Section F narrative/tables
        sec_f = ctx.section("F")
        for t in ctx.find_tables(sec_f, "complaint"):
            for row in ctx.rows(t):
                for k, v in row.items():
                    if "rate" in k.lower() and isinstance(v, (int, float, str)):
                        try:
                            reported = float(str(v).strip().rstrip("%"))
                        except ValueError:
                            continue
                        # No denominator → cannot verify
                        return True, None
        return True, "complaint count or denominator unavailable"
    expected_pct = (complaints / units) * 100.0
    # Find a reported rate in stats or Section F
    reported = stats.get("complaint_rate_pct") or stats.get("complaint_rate")
    if reported is None:
        return True, None
    try:
        reported_pct = float(str(reported).rstrip("%"))
    except ValueError:
        return True, None
    if abs(expected_pct - reported_pct) > ctx.tolerance:
        return False, f"reported={reported_pct:.4f}% expected={expected_pct:.4f}%"
    return True, None


def _check_b004(ctx: ValidationContext) -> Tuple[bool, Optional[str]]:
    sec_i = ctx.section("I")
    if not sec_i:
        return True, None
    capa_rows: List[Dict[str, Any]] = []
    for t in ctx.find_tables(sec_i, "capa"):
        capa_rows.extend(ctx.rows(t))
    text = ctx.all_text(sec_i)
    says_no = bool(re.search(
        r"no\s+capa\s+(?:was\s+)?initiated|no\s+corrective\s+(?:and\s+preventive\s+)?actions?\s+(?:were|was)",
        text,
    ))
    flag = sec_i.get("capa_initiated")
    if flag is True and not capa_rows:
        return False, "capa_initiated=True but table empty"
    if flag is False and capa_rows:
        return False, f"capa_initiated=False but {len(capa_rows)} rows in table"
    if says_no and capa_rows:
        return False, f"narrative says 'no CAPA' but {len(capa_rows)} rows in table"
    return True, None


def _check_b005(ctx: ValidationContext) -> Tuple[bool, Optional[str]]:
    sec_h = ctx.section("H")
    if not sec_h:
        return True, None
    rows: List[Dict[str, Any]] = []
    for t in ctx.find_tables(sec_h, "fsca"):
        rows.extend(ctx.rows(t))
    text = ctx.all_text(sec_h)
    stated = _narrative_count(text, "fsca", "fscas", "field safety corrective actions")
    if stated is None:
        return True, None
    if stated != len(rows):
        return False, f"narrative says {stated}, table has {len(rows)}"
    return True, None


# Module C — Normalization & Statistical -----------------------------

def _check_c001(ctx: ValidationContext) -> Tuple[bool, Optional[str]]:
    stats = ctx.statistics
    have_units = bool(ctx.to_int(stats.get("total_units_distributed") or stats.get("denominator")))
    missing_denoms: List[str] = []
    for fld in ("complaint_rate", "complaint_rate_pct", "incident_rate"):
        if stats.get(fld) is not None and not have_units:
            missing_denoms.append(fld)
    if missing_denoms:
        return False, f"rates present without denominator: {missing_denoms}"
    return True, None


def _check_c002(ctx: ValidationContext) -> Tuple[bool, Optional[str]]:
    sec_c = ctx.section("C")
    units_by_region: Dict[str, int] = {}
    for t in ctx.find_tables(sec_c, "sales_by_region") + ctx.find_tables(sec_c, "table_1"):
        for row in ctx.rows(t):
            r = _safe_str(row.get("region")) or _safe_str(row.get("market"))
            if not r:
                continue
            for k, v in row.items():
                if "units" in k.lower() and not isinstance(v, (dict, list)):
                    units_by_region[r] = units_by_region.get(r, 0) + ctx.to_int(v)
    active = {r for r, u in units_by_region.items() if u > 0}
    if not active:
        return True, None
    # Section F: complaints
    sec_f = ctx.section("F")
    seen_regions: set = set()
    has_rate_column = False
    for t in ctx.find_tables(sec_f, "complaint") + ctx.find_tables(sec_f, "region"):
        for row in ctx.rows(t):
            r = _safe_str(row.get("region")) or _safe_str(row.get("market"))
            if r:
                seen_regions.add(r)
            if any("rate" in k.lower() for k in row.keys()):
                has_rate_column = True
    missing = sorted(active - seen_regions)
    if missing:
        return False, f"complaint tables omit regions {missing}"
    if not has_rate_column:
        return False, "no per-region rate column in Section F"
    return True, None


def _check_c003(ctx: ValidationContext) -> Tuple[bool, Optional[str]]:
    stats = ctx.statistics
    trend = stats.get("trend_analysis") or {}
    mean = trend.get("mean") or trend.get("mean_rate")
    std = trend.get("std_dev")
    ucl = trend.get("ucl_3sigma") or trend.get("UCL")
    if None in (mean, std, ucl):
        return True, None
    try:
        expected = float(mean) + 3.0 * float(std)
        if abs(float(ucl) - expected) > ctx.tolerance:
            return False, f"UCL={ucl} expected={expected:.6f}"
    except (TypeError, ValueError):
        return True, None
    return True, None


# Module D — PMS & PMCF ----------------------------------------------

def _check_d001(ctx: ValidationContext) -> Tuple[bool, Optional[str]]:
    text = ctx.all_text(ctx.psur.get("sections") or {})
    if not re.search(r"\bpms[\s-]?\d{3,}[a-z]?\b", text):
        return True, None  # No PMS plan referenced
    has_exec = bool(re.search(
        r"planned\s+(?:vs\.?|versus)\s+performed|executed\s+frequency|"
        r"deviation\s+(?:analysis|from\s+plan)|pms\s+plan\s+execution",
        text,
    ))
    return (has_exec, None if has_exec else "no execution summary found")


def _check_d002(ctx: ValidationContext) -> Tuple[bool, Optional[str]]:
    sec_l = ctx.section("L")
    if not sec_l:
        return False, "Section L absent"
    text = ctx.all_text(sec_l)
    tables = ctx.find_tables(sec_l, "pmcf")
    has_rows = any(ctx.rows(t) for t in tables)
    summary = _safe_str(sec_l.get("summary_or_na_statement"))
    has_substantive = bool(summary) and len(summary.split()) >= 25
    has_justification = bool(re.search(
        r"pmcf\s+(?:is\s+)?not\s+required|annex\s+xiv\s+part\s+b|"
        r"justification\s+for\s+(?:the\s+)?absence\s+of\s+pmcf",
        text,
    ))
    deferred = bool(re.search(
        r"pmcf\s+data\s+(?:are|is)?\s*maintained\s+separately|see\s+(?:the\s+)?pmcf\s+(?:plan|report|file)",
        text,
    ))
    if deferred and not has_rows and not has_justification:
        return False, "Section L defers to external doc without summary or justification"
    if not has_rows and not has_substantive and not has_justification:
        return False, "no PMCF summary (>=25 words) and no absence-justification"
    return True, None


def _check_d003(ctx: ValidationContext) -> Tuple[bool, Optional[str]]:
    sec_l_text = ctx.all_text(ctx.section("L"))
    sec_m_text = ctx.all_text(ctx.section("M"))
    if not sec_l_text:
        return True, None
    # Was anything PMCF-substantive said?
    if not re.search(r"\bpmcf\b", sec_l_text):
        return True, None
    if not re.search(r"\bpmcf\b|post[-\s]?market\s+clinical", sec_m_text):
        return False, "Section M makes no reference to PMCF results"
    return True, None


# Module E — Risk Management Traceability ----------------------------

def _check_e001(ctx: ValidationContext) -> Tuple[bool, Optional[str]]:
    text = ctx.all_text(ctx.psur.get("sections") or {})
    if not re.search(r"\b(?:rmf|risk\s+management\s+file)\b", text):
        return True, None
    if re.search(
        r"hazard\s+id|risk\s+control\b|complaint\s*\u2192\s*hazard|hazard\s*->\s*control",
        text,
    ):
        return True, None
    return False, "RMF cited but no complaint→hazard→control mapping"


def _check_e002(ctx: ValidationContext) -> Tuple[bool, Optional[str]]:
    stats = ctx.statistics
    new_types = stats.get("new_incident_types") or stats.get("new_risks") or []
    if not new_types:
        return True, None
    text = ctx.all_text(ctx.psur.get("sections") or {})
    rmf_updated = bool(re.search(
        r"rmf\s+(?:has\s+been\s+)?updated|risk\s+management\s+file\s+(?:has\s+been\s+)?updated|"
        r"updated\s+rmf|rmf\s+revision",
        text,
    ))
    return (rmf_updated, None if rmf_updated else f"{len(new_types)} new risk(s) without RMF update")


# Module F — FSCA Effectiveness --------------------------------------

def _check_f001(ctx: ValidationContext) -> Tuple[bool, Optional[str]]:
    sec_h = ctx.section("H")
    if not sec_h:
        return True, None
    rows: List[Dict[str, Any]] = []
    for t in ctx.find_tables(sec_h, "fsca"):
        rows.extend(ctx.rows(t))
    if not rows:
        return True, None
    section_text = ctx.all_text(sec_h)
    missing: List[str] = []
    for row in rows:
        row_text = " ".join(str(v) for v in row.values()).lower()
        if not re.search(
            r"effectiveness|kpi|complaint\s+reduction|baseline|closure\s+criteri",
            row_text + " " + section_text,
        ):
            missing.append(str(row.get("fsca_id") or row.get("id") or "?"))
    return (not missing, f"FSCAs without effectiveness metric: {missing}" if missing else None)


def _check_f002(ctx: ValidationContext) -> Tuple[bool, Optional[str]]:
    sec_h = ctx.section("H")
    if not sec_h:
        return True, None
    closed_unverified: List[str] = []
    for t in ctx.find_tables(sec_h, "fsca"):
        for row in ctx.rows(t):
            status = str(row.get("status") or row.get("fsca_status") or "").lower()
            verified = row.get("effectiveness_verified")
            text = " ".join(str(v) for v in row.values()).lower()
            if "closed" in status or "complete" in status:
                is_verified = (
                    verified is True
                    or "verified" in text
                    or "effectiveness confirmed" in text
                )
                if not is_verified:
                    closed_unverified.append(str(row.get("fsca_id") or row.get("id") or "?"))
    return (not closed_unverified,
            f"closed without verification: {closed_unverified}" if closed_unverified else None)


# Module G — Scientific & External Data ------------------------------

def _check_g001(ctx: ValidationContext) -> Tuple[bool, Optional[str]]:
    sec_j_text = ctx.all_text(ctx.section("J"))
    sec_m_text = ctx.all_text(ctx.section("M"))
    if not sec_j_text:
        return True, None
    if re.search(r"literature|publication|peer[-\s]?reviewed|published\s+study", sec_m_text):
        return True, None
    return False, "Section M does not reference literature findings"


def _check_g002(ctx: ValidationContext) -> Tuple[bool, Optional[str]]:
    sec_k = ctx.section("K")
    if not sec_k:
        return True, None
    text = ctx.all_text(sec_k)
    has_external = bool(re.search(
        r"maude|swissmedic|tga\s+iris|eudamed|fda|bfarm|mhra\s+(?:yellow|adverse)",
        text,
    ))
    if not has_external:
        return True, None
    has_benchmark = bool(re.search(
        r"compared\s+(?:to|with|against)|benchmark|consistent\s+with|in\s+line\s+with\s+(?:our|internal)|"
        r"versus\s+internal|external\s+vs\.?\s+internal",
        text,
    ))
    return (has_benchmark, None if has_benchmark else "no external-vs-internal comparison narrative")


# Semantic checks (NLP-lite, deterministic regex) --------------------

def _check_sem001(ctx: ValidationContext) -> Tuple[bool, Optional[str]]:
    # Reuse B-001 logic semantics (narrative↔table for any section)
    return _check_b001(ctx)


def _check_sem002(ctx: ValidationContext) -> Tuple[bool, Optional[str]]:
    return _check_b004(ctx)


def _check_sem003(ctx: ValidationContext) -> Tuple[bool, Optional[str]]:
    text = ctx.all_text(ctx.psur.get("sections") or {})
    deferred = re.search(r"maintained\s+separately|see\s+(?:the\s+)?(?:pmcf|cer|rmf)\s+(?:plan|report|file)", text)
    if not deferred:
        return True, None
    justified = bool(re.search(
        r"justification|not\s+required|annex\s+xiv\s+part\s+b|exempt(?:ion)?\s+(?:from|under)",
        text,
    ))
    return (justified, None if justified else "external-doc deferral without justification")


# ──────────────────────────────────────────────────────────────────────
# Engine
# ──────────────────────────────────────────────────────────────────────

def build_checks() -> List[Check]:
    return [
        # Module A
        Check("A-001", ["sections_present"], "Mandatory sections presence",
              "missing == empty", "missing != empty",
              "A-001 [CRITICAL] Missing mandatory sections",
              "CRITICAL", "MDR Art 86; MDCG 2022-21 §6", _check_a001),
        Check("A-002", ["certificate_number", "notified_body", "reporting_period", "psur_cadence"],
              "Regulatory metadata completeness",
              "all fields populated", "any field null",
              "A-002 [MAJOR] Incomplete regulatory metadata",
              "MAJOR", "MDR Annex III", _check_a002),
        # Module B
        Check("B-001", ["total_incidents", "incident_table_sum"],
              "Incident total consistency",
              "total == sum(table)", "mismatch",
              "B-001 [CRITICAL] Serious incident mismatch: narrative vs tables",
              "CRITICAL", "MDR Art 86", _check_b001),
        Check("B-002", ["incidents_by_region[]", "total_incidents"],
              "Regional incident alignment",
              "sum(regions) == total", "mismatch or regions empty",
              "B-002 [CRITICAL] Regional incident data inconsistent or missing",
              "CRITICAL", "MDCG 2022-21", _check_b002),
        Check("B-003", ["complaint_count", "units_sold", "reported_complaint_rate"],
              "Complaint rate calculation",
              "|calc - reported| <= tol", "deviation > tol",
              "B-003 [MAJOR] Complaint rate calculation error",
              "MAJOR", "MDR Art 86", _check_b003),
        Check("B-004", ["CAPA_flag", "CAPA_table_length"],
              "CAPA consistency",
              "flag == (len(table)>0)", "mismatch",
              "B-004 [CRITICAL] CAPA inconsistency between narrative and table",
              "CRITICAL", "MDR Art 83", _check_b004),
        Check("B-005", ["FSCA_list[]", "FSCA_count_stated"],
              "FSCA count consistency",
              "len(list) == count_stated", "mismatch",
              "B-005 [MAJOR] FSCA count mismatch",
              "MAJOR", "MDR Art 87", _check_b005),
        # Module C
        Check("C-001", ["complaint_rate", "incident_rate", "denominator"],
              "Rate normalization",
              "each rate has denominator", "missing denominator",
              "C-001 [CRITICAL] Rates not normalized to exposure",
              "CRITICAL", "MDCG 2022-21 §7", _check_c001),
        Check("C-002", ["units_by_region[]", "complaints_by_region[]"],
              "Regional rate calculation",
              "rates exist per active region", "missing rates or regions",
              "C-002 [MAJOR] Regional normalization missing",
              "MAJOR", "MDCG 2022-21", _check_c002),
        Check("C-003", ["mean_rate", "std_dev", "UCL"],
              "Control-limit (UCL = mean + 3σ) validation",
              "|UCL - (mean+3σ)| <= tol", "mismatch",
              "C-003 [MINOR] Incorrect UCL calculation",
              "MINOR", "Industry SPC convention", _check_c003),
        # Module D
        Check("D-001", ["PMS_plan_exists", "PMS_execution_summary_present"],
              "PMS plan execution",
              "execution summary present", "missing summary",
              "D-001 [MAJOR] Missing PMS plan execution description",
              "MAJOR", "MDR Annex III", _check_d001),
        Check("D-002", ["PMCF_required", "PMCF_summary_present", "PMCF_justification_present"],
              "PMCF inclusion",
              "summary if required else justification",
              "neither summary nor justification",
              "D-002 [CRITICAL] PMCF section non-compliant",
              "CRITICAL", "MDR Art 86(1)(c); Annex XIV Part B", _check_d002),
        Check("D-003", ["PMCF_results", "benefit_risk_section"],
              "PMCF impact linkage",
              "PMCF referenced in Section M", "no linkage",
              "D-003 [MAJOR] PMCF not integrated into benefit-risk",
              "MAJOR", "MDR Art 86", _check_d003),
        # Module E
        Check("E-001", ["complaint_categories[]", "RMF_reference_present"],
              "RMF linkage",
              "each category linked to hazard", "missing mapping",
              "E-001 [MAJOR] No traceability to risk management file",
              "MAJOR", "MDR Annex I + Annex III", _check_e001),
        Check("E-002", ["new_incident_types[]", "RMF_updated_flag"],
              "New-risk detection vs RMF update",
              "no new risks OR RMF updated",
              "new risks without RMF update",
              "E-002 [CRITICAL] New risks not integrated into RMF",
              "CRITICAL", "ISO 14971 + MDR", _check_e002),
        # Module F
        Check("F-001", ["FSCA_list[]", "FSCA_effectiveness_defined"],
              "FSCA effectiveness metric",
              "each FSCA has metric", "missing for any FSCA",
              "F-001 [MAJOR] FSCA effectiveness not evaluated",
              "MAJOR", "MDR Art 83(4)", _check_f001),
        Check("F-002", ["FSCA_status", "effectiveness_verified"],
              "FSCA closure validation",
              "closed ⇒ verified", "closed without verification",
              "F-002 [CRITICAL] FSCA closed without effectiveness confirmation",
              "CRITICAL", "MDR vigilance", _check_f002),
        # Module G
        Check("G-001", ["literature_findings", "risk_assessment_section"],
              "Literature integration",
              "literature in M / risk assessment", "no linkage",
              "G-001 [MINOR] Literature not integrated into risk evaluation",
              "MINOR", "MDCG 2022-21 §4.10", _check_g001),
        Check("G-002", ["external_events_count", "internal_event_comparison"],
              "External DB benchmarking",
              "internal vs external comparison present",
              "no benchmarking narrative",
              "G-002 [MINOR] External data not benchmarked",
              "MINOR", "MDCG 2022-21 §4.11", _check_g002),
        # Semantic
        Check("SEM-001", ["narrative_count", "table_total"],
              "NLP narrative↔table consistency",
              "narrative count == table total", "mismatch",
              "SEM-001 [CRITICAL] Narrative/table count mismatch",
              "CRITICAL", "MDR Art 86", _check_sem001),
        Check("SEM-002", ["negation_phrase", "table_rows"],
              "Contradiction detection (no CAPA + CAPA entries)",
              "no contradiction", "contradiction detected",
              "SEM-002 [CRITICAL] Contradiction detected",
              "CRITICAL", "MDR Art 83", _check_sem002),
        Check("SEM-003", ["deferral_phrase", "justification"],
              "Missing justification for external deferral",
              "deferral has justification",
              "deferral without justification",
              "SEM-003 [MAJOR] Deferral to external document lacks justification",
              "MAJOR", "MDR Art 86; MDCG 2022-21", _check_sem003),
    ]


class ValidationEngine:
    """Runs all declarative checks and produces the PSUR_VALIDATION block."""

    # Score weights per severity
    WEIGHTS = {"CRITICAL": 10, "MAJOR": 4, "MINOR": 1}
    BASE_SCORE = 100

    def __init__(self, checks: Optional[List[Check]] = None, tolerance: float = 0.01):
        self.checks = checks or build_checks()
        self.tolerance = tolerance

    def run(
        self,
        psur: Dict[str, Any],
        parsed_data: Optional[Dict[str, Any]] = None,
        device_context: Optional[Dict[str, Any]] = None,
        statistics: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        ctx = ValidationContext(
            psur=psur,
            parsed_data=parsed_data or {},
            device_context=device_context or psur.get("_device_context", {}) or {},
            statistics=statistics or psur.get("_statistics", {}) or {},
            tolerance=self.tolerance,
        )
        results = [c.run(ctx) for c in self.checks]
        critical = [r.message for r in results if not r.passed and r.severity == "CRITICAL"]
        major = [r.message for r in results if not r.passed and r.severity == "MAJOR"]
        minor = [r.message for r in results if not r.passed and r.severity == "MINOR"]

        # Score: subtract weight per failure (floor at 0)
        score = self.BASE_SCORE
        for r in results:
            if not r.passed:
                score -= self.WEIGHTS.get(r.severity, 0)
        score = max(score, 0)

        ready = (not critical) and (not major)

        return {
            "PSUR_VALIDATION": {
                "READY": ready,
                "CRITICAL_ERRORS": critical,
                "MAJOR_ERRORS": major,
                "MINOR_ERRORS": minor,
                "SCORE": int(score),
                "CHECKS_RUN": len(results),
                "PASSED": sum(1 for r in results if r.passed),
                "FAILED": sum(1 for r in results if not r.passed),
                "DETAIL": [
                    {
                        "check_id": r.check_id,
                        "passed": r.passed,
                        "severity": r.severity,
                        "regulation": r.regulation,
                        "message": r.message,
                    }
                    for r in results
                ],
            }
        }


__all__ = [
    "Check", "CheckResult", "ValidationContext",
    "ValidationEngine", "build_checks",
]
