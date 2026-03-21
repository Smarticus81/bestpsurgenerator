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
    """Read Excel, trying to find the right sheet."""
    try:
        # Try reading all sheets and pick the largest one
        xls = pd.ExcelFile(filepath)
        best_df = None
        best_rows = 0
        for sheet in xls.sheet_names:
            df = pd.read_excel(filepath, sheet_name=sheet)
            if len(df) > best_rows:
                best_rows = len(df)
                best_df = df
        return best_df if best_df is not None else pd.DataFrame()
    except Exception:
        return pd.read_excel(filepath)


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
        "hazard_id": ["hazard_id", "id", "ref", "reference", "number", "no", "hazard_no", "item", "seq"],
        "hazard_description": ["hazard_description", "hazard", "description", "hazardous_situation",
                               "hazard_situation", "failure_mode", "potential_hazard"],
        "hazard_category": ["hazard_category", "category", "type", "hazard_type", "hazard_group",
                            "risk_category", "cause_category"],
        "harm": ["harm", "injury", "consequence", "clinical_consequence", "patient_harm",
                 "clinical_harm", "patient_consequence", "health_consequence", "clinical_impact"],
        "severity": ["severity", "sev", "s", "severity_score", "severity_level", "severity_rating"],
        "probability_before": ["probability_before", "prob_before", "p1", "initial_probability",
                               "probability_of_occurrence", "pre_mitigation_probability",
                               "likelihood_before", "occurrence_before", "initial_prob"],
        "risk_level_before": ["risk_before", "initial_risk", "risk_level_before", "risk_class_before",
                              "inherent_risk", "pre_mitigation_risk", "unmitigated_risk",
                              "risk_priority_number_before", "rpn_before"],
        "risk_control": ["risk_control", "control", "mitigation", "risk_control_measure",
                         "control_measure", "risk_mitigation", "corrective_action",
                         "preventive_measure", "safeguard", "design_control"],
        "probability_after": ["probability_after", "prob_after", "p2", "residual_probability",
                              "residual_prob", "post_mitigation_probability",
                              "likelihood_after", "occurrence_after"],
        "risk_level_after": ["risk_after", "residual_risk", "risk_level_after", "risk_class_after",
                             "post_mitigation_risk", "mitigated_risk", "final_risk",
                             "risk_priority_number_after", "rpn_after"],
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
        for col in columns:
            if any(kw == col or kw in col for kw in keywords):
                col_map[role] = col
                break

    # Special heuristic: if no explicit max_expected_rate, look for any column with "rate" or "expected"
    if not col_map["max_expected_rate"] and not col_map["expected_rate"]:
        for col in columns:
            if ("rate" in col or "expected" in col) and "date" not in col:
                col_map["max_expected_rate"] = col
                break

    return col_map


def _extract_hazards(df: pd.DataFrame, col_map: Dict[str, Optional[str]]) -> List[Dict[str, Any]]:
    """Extract structured hazard records."""
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

        # Only include rows that have at least a description or harm
        if hazard.get("hazard_description") or hazard.get("harm") or hazard.get("medical_device_problem"):
            hazards.append(hazard)

    return hazards


def _extract_max_expected_rates(df: pd.DataFrame, col_map: Dict[str, Optional[str]]) -> Dict[str, float]:
    """Extract max expected rates keyed by IMDRF code or medical device problem.

    Returns: {problem_description: rate, ...}
    """
    rates = {}

    rate_col = col_map.get("max_expected_rate") or col_map.get("expected_rate")
    imdrf_col = col_map.get("imdrf_code")
    problem_col = col_map.get("medical_device_problem") or col_map.get("hazard_description")

    if not rate_col or rate_col not in df.columns:
        return rates

    key_col = imdrf_col if imdrf_col and imdrf_col in df.columns else problem_col

    if not key_col or key_col not in df.columns:
        return rates

    for _, row in df.iterrows():
        key = row.get(key_col, None)
        rate_val = row.get(rate_col, None)

        if pd.isna(key) or pd.isna(rate_val):
            continue

        key_str = str(key).strip()
        if not key_str:
            continue

        # Parse rate value — handle percentages, scientific notation, fractions
        rate = _parse_rate_value(rate_val)
        if rate is not None and rate > 0:
            # Use the highest rate if the same key appears multiple times
            if key_str not in rates or rate > rates[key_str]:
                rates[key_str] = rate

    return rates


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
    }

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
            # Check if any residual risk is unacceptable
            if rl_str in ("HIGH", "UNACCEPTABLE", "INTOLERABLE", "4", "5"):
                summary["all_risks_acceptable"] = False

        # Count hazard categories
        cat = h.get("hazard_category")
        if cat:
            cat_str = str(cat).strip()
            summary["hazard_categories"][cat_str] = summary["hazard_categories"].get(cat_str, 0) + 1

    return summary
