"""Structured parser for Risk Assessment and Control Table (RACT) files.

Extracts structured RACT data from .xlsx/.csv files:
- Hazard categories
- Medical device problems (IMDRF codes)
- Expected rates of occurrence
- Risk levels (before/after mitigation)
- Risk controls
"""
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)


def parse_ract(filepath: Path) -> Dict[str, Any]:
    """Parse a RACT file into structured data.

    Args:
        filepath: Path to RACT .xlsx or .csv

    Returns:
        Dict with structured RACT data:
        {
            "source_file": str,
            "hazards": [...],
            "max_expected_rates": {imdrf_code: rate, ...},
            "risk_summary": {...},
        }
    """
    ext = filepath.suffix.lower()
    if ext == ".csv":
        df = _read_csv(filepath)
    elif ext in (".xlsx", ".xls"):
        df = _read_excel(filepath)
    else:
        logger.warning(f"Unsupported RACT format: {ext}")
        return {"source_file": filepath.name, "hazards": [], "max_expected_rates": {}, "risk_summary": {}}

    # Normalize column names
    df.columns = [_normalize_col(c) for c in df.columns]
    logger.info(f"RACT columns: {list(df.columns)}")

    # Auto-detect column roles
    col_map = _detect_columns(df)
    logger.info(f"RACT column mapping: {col_map}")

    hazards = _extract_hazards(df, col_map)
    max_rates = _extract_max_expected_rates(df, col_map)
    risk_summary = _compute_risk_summary(hazards)

    result = {
        "source_file": filepath.name,
        "hazards": hazards,
        "max_expected_rates": max_rates,
        "risk_summary": risk_summary,
        "column_mapping": col_map,
        "total_hazards": len(hazards),
    }

    logger.info(
        f"RACT parsed: {len(hazards)} hazards, "
        f"{len(max_rates)} max expected rates, "
        f"risk summary: {risk_summary}"
    )

    return result


def _read_csv(filepath: Path) -> pd.DataFrame:
    """Read CSV with encoding detection."""
    for enc in ["utf-8", "utf-8-sig", "latin-1", "cp1252"]:
        try:
            return pd.read_csv(filepath, encoding=enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return pd.read_csv(filepath, encoding="utf-8", errors="replace")


def _read_excel(filepath: Path) -> pd.DataFrame:
    """Read Excel and recover the real header row.

    Many RACT workbooks carry stacked / merged-cell headers
    (category banner row, then column-number row, then the real header row).
    A naive ``pd.read_excel`` grabs the banner row and produces useless column
    names like ``"Risk Identifiers"`` / ``"Unnamed: 1"``. We sniff the first
    few rows of every sheet to find the row that best matches known RACT
    column names, then re-read with that row promoted to the header.
    """
    HEADER_KEYWORDS = (
        "hazard", "harm", "severity", "occurrence", "probability",
        "risk level", "risk control", "mitigation", "imdrf",
        "device problem", "expected rate", "category", "primary id",
        "secondary id", "topic", "potential root cause", "party exposed",
        "acceptability",
    )

    def _score_row(row) -> int:
        """Count how many header keywords appear across the cells."""
        cells = [str(v).strip().lower() for v in row.tolist()
                 if v is not None and str(v).strip() and str(v).lower() != "nan"]
        if not cells:
            return 0
        score = 0
        for cell in cells:
            for kw in HEADER_KEYWORDS:
                if kw in cell:
                    score += 1
                    break
        return score

    try:
        xls = pd.ExcelFile(filepath)
    except Exception:
        return pd.read_excel(filepath)

    best_df: Optional[pd.DataFrame] = None
    best_rows = 0

    for sheet in xls.sheet_names:
        try:
            preview = pd.read_excel(filepath, sheet_name=sheet, header=None, nrows=10)
        except Exception:
            continue
        if preview.empty:
            continue

        scan_limit = min(8, len(preview))
        scores = [(_score_row(preview.iloc[i]), i) for i in range(scan_limit)]
        scores.sort(key=lambda t: (-t[0], t[1]))
        best_score, header_row = scores[0]

        try:
            if best_score >= 3:
                df = pd.read_excel(filepath, sheet_name=sheet, header=header_row)
                # Drop rows where every value is NaN (common just below
                # multi-row headers).
                df = df.dropna(how="all").reset_index(drop=True)
            else:
                df = pd.read_excel(filepath, sheet_name=sheet)
        except Exception:
            continue

        if len(df) > best_rows:
            best_rows = len(df)
            best_df = df

    return best_df if best_df is not None else pd.read_excel(filepath)


def _normalize_col(name: str) -> str:
    """Normalize column name."""
    return str(name).strip().lower().replace(" ", "_").replace("-", "_").replace(".", "_")


def _detect_columns(df: pd.DataFrame) -> Dict[str, Optional[str]]:
    """Auto-detect column roles from column names."""
    col_map = {
        "hazard_id": None,
        "hazard_description": None,
        "hazard_category": None,
        "harm": None,
        "severity": None,
        "probability_before": None,
        "risk_level_before": None,
        "risk_control": None,
        "probability_after": None,
        "risk_level_after": None,
        "imdrf_code": None,
        "medical_device_problem": None,
        "expected_rate": None,
        "max_expected_rate": None,
    }

    columns = list(df.columns)

    # Map column names to roles
    role_keywords = {
        "hazard_id": ["hazard_id", "primary_id", "id", "ref", "reference", "number", "no", "hazard_no", "item", "seq"],
        "hazard_description": ["hazard_description", "hazard", "description", "hazardous_situation",
                               "hazard_situation", "failure_mode", "potential_hazard"],
        "hazard_category": ["hazard_category", "category", "type", "hazard_type", "hazard_group",
                            "risk_category", "cause_category", "topic"],
        "harm": ["harm", "injury", "consequence", "clinical_consequence", "patient_harm",
                 "clinical_harm", "patient_consequence", "health_consequence", "clinical_impact"],
        "severity": ["initial_severity", "final_severity", "severity", "sev", "s", "severity_score",
                     "severity_level", "severity_rating"],
        "probability_before": ["initial_occurrence", "probability_before", "prob_before", "p1", "initial_probability",
                               "probability_of_occurrence", "pre_mitigation_probability",
                               "likelihood_before", "occurrence_before", "initial_prob"],
        "risk_level_before": ["initial_risk_level", "risk_before", "initial_risk", "risk_level_before", "risk_class_before",
                              "inherent_risk", "pre_mitigation_risk", "unmitigated_risk",
                              "risk_priority_number_before", "rpn_before"],
        "risk_control": ["standard_rcms", "additional_rcms", "risk_control", "control", "mitigation", "risk_control_measure",
                         "control_measure", "risk_mitigation", "corrective_action",
                         "preventive_measure", "safeguard", "design_control"],
        "probability_after": ["final_occurrence", "probability_after", "prob_after", "p2", "residual_probability",
                              "residual_prob", "post_mitigation_probability",
                              "likelihood_after", "occurrence_after"],
        "risk_level_after": ["final_risk_level", "risk_after", "residual_risk", "risk_level_after", "risk_class_after",
                             "post_mitigation_risk", "mitigated_risk", "final_risk",
                             "risk_priority_number_after", "rpn_after",
                             "acceptability_of_individual_residual_risk", "acceptability"],
        "imdrf_code": ["imdrf", "imdrf_code", "annex_code", "problem_code", "annex_a",
                        "annex_a_code", "device_problem_code", "imdrf_annex_a"],
        "medical_device_problem": ["medical_device_problem", "device_problem", "mdp",
                                   "problem_description", "device_problem_description",
                                   "imdrf_problem", "device_issue", "failure_description"],
        "expected_rate": ["expected_rate", "expected_occurrence", "expected_frequency",
                          "rate_of_occurrence", "occurrence_rate", "baseline_rate",
                          "historical_rate", "benchmark_rate"],
        "max_expected_rate": ["max_expected_rate", "maximum_expected_rate", "max_rate", "ract_rate",
                              "max_expected_rate_of_occurrence", "max_acceptable_rate",
                              "acceptable_rate", "threshold_rate", "acceptance_criterion",
                              "acceptable_occurrence_rate", "maximum_acceptable"],
    }

    for role, keywords in role_keywords.items():
        # Prefer exact matches, then substring matches, so that
        # "initial_severity" wins over "final_severity" for the severity slot.
        exact = next((c for c in columns if any(c == kw for kw in keywords)), None)
        if exact:
            col_map[role] = exact
            continue
        substr = next((c for c in columns
                       if any(kw in c for kw in keywords)), None)
        if substr:
            col_map[role] = substr

    # Special heuristic: if no explicit max_expected_rate, look for any column with "rate" or "expected"
    if not col_map["max_expected_rate"] and not col_map["expected_rate"]:
        for col in columns:
            if ("rate" in col or "expected" in col) and "date" not in col:
                col_map["max_expected_rate"] = col
                break

    return col_map


def _extract_hazards(df: pd.DataFrame, col_map: Dict[str, Optional[str]]) -> List[Dict[str, Any]]:
    """Extract structured hazard records.

    Performs forward-fill on the hazard category / topic column because RACT
    workbooks routinely use vertically-merged cells for the category that
    appear as NaN on every row except the first.  Also separates the long
    "acceptability" sentence from the actual risk-level field when the
    LLM-facing column was conflated.
    """
    df = df.copy()

    # Forward-fill the category column when it appears to be a merged-cell group.
    cat_col = col_map.get("hazard_category")
    if cat_col and cat_col in df.columns:
        df[cat_col] = df[cat_col].ffill()

    # Detect the long-text "acceptability" column so we can keep it separate
    # from the risk-level slot.
    acceptability_col = next(
        (c for c in df.columns if "acceptability" in c), None
    )
    rl_after_col = col_map.get("risk_level_after")
    if (
        rl_after_col
        and acceptability_col
        and rl_after_col == acceptability_col
    ):
        # The "Final Risk Level" column was empty in the source, so we mapped
        # the acceptability sentence into risk_level_after.  Demote that and
        # leave risk_level_after blank.
        col_map["risk_level_after"] = None

    hazards = []
    for _, row in df.iterrows():
        hazard = {}
        for role, col in col_map.items():
            if col and col in df.columns:
                val = row.get(col, None)
                if pd.notna(val):
                    hazard[role] = str(val).strip() if isinstance(val, str) else val
                else:
                    hazard[role] = None
            else:
                hazard[role] = None

        if acceptability_col and acceptability_col in df.columns:
            av = row.get(acceptability_col, None)
            hazard["acceptability_statement"] = (
                str(av).strip() if pd.notna(av) else None
            )

        # Only include rows that carry a description, harm, or device problem.
        if hazard.get("hazard_description") or hazard.get("harm") or hazard.get("medical_device_problem"):
            hazards.append(hazard)

    return hazards


def _extract_max_expected_rates(df: pd.DataFrame, col_map: Dict[str, Optional[str]]) -> Dict[str, float]:
    """Extract max expected rates keyed by IMDRF code or medical device problem.

    Returns: {problem_description: rate, ...}

    Two extraction modes (in priority order):
      1. Numeric rate column (`max_expected_rate` / `expected_rate`).
      2. Qualitative occurrence code (O1-O5) mapped to the upper threshold
         from `constraints/ract_occurrence_codes.json`.  This makes the RACT
         comparison block in `statistics.py` produce WITHIN/EXCEEDS verdicts
         even when the source workbook only carries the qualitative score.
    """
    rates: Dict[str, float] = {}

    rate_col = col_map.get("max_expected_rate") or col_map.get("expected_rate")
    imdrf_col = col_map.get("imdrf_code")
    problem_col = col_map.get("medical_device_problem") or col_map.get("hazard_description")

    key_col = imdrf_col if imdrf_col and imdrf_col in df.columns else problem_col

    if not key_col or key_col not in df.columns:
        return rates

    # Mode 1 — explicit numeric rate column.
    if rate_col and rate_col in df.columns:
        for _, row in df.iterrows():
            key = row.get(key_col, None)
            rate_val = row.get(rate_col, None)
            if pd.isna(key) or pd.isna(rate_val):
                continue
            key_str = str(key).strip()
            if not key_str:
                continue
            rate = _parse_rate_value(rate_val)
            if rate is not None and rate > 0:
                if key_str not in rates or rate > rates[key_str]:
                    rates[key_str] = rate

    # Mode 2 — fallback: qualitative O1-O5 occurrence code from
    # probability_after / probability_before, translated via the standard
    # threshold map.  Only used when Mode 1 produced nothing for the key.
    occurrence_to_max_rate = _load_occurrence_threshold_map()
    if occurrence_to_max_rate:
        prob_col = (
            col_map.get("probability_after")
            or col_map.get("probability_before")
        )
        if prob_col and prob_col in df.columns:
            for _, row in df.iterrows():
                key = row.get(key_col, None)
                prob_val = row.get(prob_col, None)
                if pd.isna(key) or pd.isna(prob_val):
                    continue
                key_str = str(key).strip()
                if not key_str or key_str in rates:
                    continue
                code = _normalize_occurrence_code(prob_val)
                if code and code in occurrence_to_max_rate:
                    rates[key_str] = occurrence_to_max_rate[code]

    return rates


def _load_occurrence_threshold_map() -> Dict[str, float]:
    """Return {O1: 0.0001, O2: 0.001, ...} from ract_occurrence_codes.json."""
    import json
    cfg_path = Path(__file__).resolve().parent.parent / "constraints" / "ract_occurrence_codes.json"
    if not cfg_path.exists():
        return {}
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out: Dict[str, float] = {}
    for entry in data.get("occurrence_codes", []):
        code = str(entry.get("code", "")).strip().upper()
        rate = entry.get("max_expected_rate")
        if code and isinstance(rate, (int, float)):
            out[code] = float(rate)
    return out


_OCCURRENCE_LABEL_TO_CODE = {
    "improbable": "O1",
    "remote": "O2",
    "occasional": "O3",
    "probable": "O4",
    "frequent": "O5",
}


def _normalize_occurrence_code(val: Any) -> Optional[str]:
    """Coerce a free-form occurrence cell to an O1-O5 code, or None."""
    if val is None:
        return None
    s = str(val).strip().lower()
    if not s:
        return None
    # Direct match: 'O1'..'O5' (allowing surrounding parentheses or spaces)
    m = re.search(r"\bo\s*([1-5])\b", s)
    if m:
        return f"O{m.group(1)}"
    # Numeric 1..5 → O1..O5
    if s in {"1", "2", "3", "4", "5"}:
        return f"O{s}"
    # Word labels
    for label, code in _OCCURRENCE_LABEL_TO_CODE.items():
        if label in s:
            return code
    return None


def _parse_rate_value(val: Any) -> Optional[float]:
    """Parse a rate value from various formats."""
    if isinstance(val, (int, float)):
        return float(val)

    val_str = str(val).strip()
    if not val_str:
        return None

    # Remove percentage signs
    val_str = val_str.rstrip("%").strip()

    # Handle scientific notation: "1.5e-4", "1.5E-04"
    try:
        return float(val_str)
    except ValueError:
        pass

    # Handle fractions: "1/10000", "1 in 10000"
    m = re.match(r"(\d+(?:\.\d+)?)\s*(?:/|in|out\s*of)\s*(\d+(?:\.\d+)?)", val_str)
    if m:
        num = float(m.group(1))
        den = float(m.group(2))
        if den > 0:
            return num / den

    # Handle "< 0.001" or "> 0.5"
    m = re.match(r"[<>≤≥]\s*(\d+\.?\d*(?:e[+-]?\d+)?)", val_str)
    if m:
        return float(m.group(1))

    return None


def _compute_risk_summary(hazards: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute summary statistics from the RACT."""
    summary = {
        "total_hazards": len(hazards),
        "risk_levels_before": {},
        "risk_levels_after": {},
        "hazard_categories": {},
        "all_risks_acceptable": True,
        # severity (1-5) → occurrence (1-5) → count, for risk-matrix charts
        "initial_matrix": {},
        "final_matrix": {},
        # 1-d distributions for quick bar charts
        "severity_distribution": {},
        "initial_occurrence_distribution": {},
        "final_occurrence_distribution": {},
    }

    def _to_int(v):
        try:
            return int(round(float(v)))
        except (TypeError, ValueError):
            return None

    for h in hazards:
        # Count risk levels before
        rl_before = h.get("risk_level_before")
        if rl_before:
            rl_str = str(rl_before).upper().strip()
            summary["risk_levels_before"][rl_str] = summary["risk_levels_before"].get(rl_str, 0) + 1

        # Count risk levels after
        rl_after = h.get("risk_level_after")
        if rl_after:
            rl_str = str(rl_after).upper().strip()
            summary["risk_levels_after"][rl_str] = summary["risk_levels_after"].get(rl_str, 0) + 1
            if rl_str in ("HIGH", "UNACCEPTABLE", "INTOLERABLE", "4", "5"):
                summary["all_risks_acceptable"] = False

        # Count hazard categories
        cat = h.get("hazard_category")
        if cat:
            cat_str = str(cat).strip()
            summary["hazard_categories"][cat_str] = summary["hazard_categories"].get(cat_str, 0) + 1

        # Severity / occurrence numerical scores → 1-d distributions and 2-d
        # severity-vs-occurrence matrices used by the risk-matrix chart.
        sev = _to_int(h.get("severity"))
        occ_b = _to_int(h.get("probability_before"))
        occ_a = _to_int(h.get("probability_after"))

        if sev is not None:
            summary["severity_distribution"][str(sev)] = (
                summary["severity_distribution"].get(str(sev), 0) + 1
            )
        if occ_b is not None:
            summary["initial_occurrence_distribution"][str(occ_b)] = (
                summary["initial_occurrence_distribution"].get(str(occ_b), 0) + 1
            )
        if occ_a is not None:
            summary["final_occurrence_distribution"][str(occ_a)] = (
                summary["final_occurrence_distribution"].get(str(occ_a), 0) + 1
            )

        if sev is not None and occ_b is not None:
            key = f"{sev}|{occ_b}"
            summary["initial_matrix"][key] = summary["initial_matrix"].get(key, 0) + 1
        if sev is not None and occ_a is not None:
            key = f"{sev}|{occ_a}"
            summary["final_matrix"][key] = summary["final_matrix"].get(key, 0) + 1

    return summary
