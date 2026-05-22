"""Tiered Input Resolver — bridges real CooperSurgical workflow and demo data.

Scans data/input/ and resolves files using a two-tier priority system:

  Tier 1 (Core):     Raw CooperSurgical files ({NNN}_complaints.csv, etc.)
                     System extracts everything from these.

  Tier 2 (Override): Enrichment files (device_context.json, capa.csv, etc.)
                     If present, supplement or override Tier 1 extractions.

Standardized file naming convention:
  - complaints.csv       (or {NNN}_complaints.csv)
  - sales.csv / .xlsx    (or {NNN}_sales.xlsx)
  - capa.csv             (optional enrichment)
  - fsca.csv             (optional enrichment)
  - cer.docx             (or {NNN}_cer.docx)
  - ract.xlsx / .json    (or {NNN}_ract.xlsx)
  - previous_psur.docx / .json   (or {NNN}_previous_psur.docx)
  - device_context.json  (optional override — extracted from previous PSUR if absent)
  - pms_plan.json / .docx (optional enrichment)
  - external_events.csv  (optional enrichment)
  - coding_dictionary.json (optional enrichment)

NOTE: clinical_performance.json and clinical_safety.json are REMOVED as input
types. PMCF data is extracted from the CER document (Section L).
"""

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from rich.console import Console
from rich.table import Table

logger = logging.getLogger(__name__)
console = Console()


# ── Canonical input types ────────────────────────────────────────────
# Maps type_key -> (description, required?, valid extensions)

INPUT_TYPES: Dict[str, Tuple[str, bool, List[str]]] = {
    "complaints":       ("Complaint records",                    True,  [".csv", ".xlsx", ".xls"]),
    "sales":            ("Sales / distribution data",            True,  [".csv", ".xlsx", ".xls"]),
    "cer":              ("Clinical Evaluation Report",           True,  [".docx", ".doc", ".pdf"]),
    "ract":             ("Risk Assessment & Control Table",      True,  [".xlsx", ".xls", ".json"]),
    "previous_psur":    ("Previous PSUR",                        False, [".docx", ".doc", ".json"]),
    "device_context":   ("Device context metadata (override)",   False, [".json"]),
    "capa":             ("CAPA records (enrichment)",             False, [".csv", ".xlsx", ".xls"]),
    "fsca":             ("FSCA data (enrichment)",               False, [".csv", ".xlsx", ".xls"]),
    "pms_plan":         ("PMS Plan (enrichment)",                False, [".json", ".docx", ".doc", ".pdf"]),
    "ifu":              ("Instructions for Use (enrichment)",    False, [".docx", ".doc", ".pdf"]),
    "rmf":              ("Risk Management File (enrichment)",    False, [".docx", ".doc", ".pdf"]),
    "pmcf":             ("PMCF Report/Plan (enrichment)",        False, [".docx", ".doc", ".pdf", ".json"]),
    "external_db":      ("External database results",            False, [".csv", ".xlsx", ".xls"]),
    "coding_dictionary": ("IMDRF coding dictionary",             False, [".json", ".csv"]),
}

# Keywords for filename matching (same as discovery.py but standardized)
_KEYWORDS: Dict[str, List[str]] = {
    "complaints":       ["complaint", "adverse", "vigilance", "mdr_report"],
    "sales":            ["sales", "distribution", "units_sold", "shipment"],
    "cer":              ["cer", "clinical_evaluation", "clinical evaluation"],
    "ract":             ["ract", "risk_assessment", "risk assessment", "risk_control"],
    "previous_psur":    ["previous_psur", "prior_psur", "previous psur"],
    "device_context":   ["device_context"],
    "capa":             ["capa", "corrective", "preventive"],
    "fsca":             ["fsca", "field_safety", "field safety"],
    "pms_plan":         ["pms", "post_market_surveillance", "pms_plan", "plan"],
    "ifu":              ["ifu", "instructions_for_use", "instructions for use"],
    "rmf":              ["rmf", "risk_management", "risk management"],
    "pmcf":             ["pmcf", "post_market_clinical", "post-market clinical"],
    "external_db":      ["maude", "external_db", "external_database", "registry",
                         "eudamed", "external_events", "external events"],
    "coding_dictionary": ["coding_dictionary", "imdrf_codes", "imdrf_dictionary",
                          "annex_a", "annex_f", "harm_mdp", "code_dictionary"],
}

# Files that are REMOVED / deprecated — warn if found
_DEPRECATED_STEMS = {
    "clinical_performance",
    "clinical_safety",
}

# Regex to strip the {NNN}_ prefix and (1)/(2) suffixes
_PREFIX_RE = re.compile(r"^\d{1,4}_")  # e.g., "008_"
_SUFFIX_RE = re.compile(r"\s*\(\d+\)")  # e.g., " (1)"


def _normalize_stem(filename: str) -> str:
    """Normalize a filename stem for matching.

    Strips:
      - {NNN}_ prefix (e.g., '008_complaints' -> 'complaints')
      - (N) suffix (e.g., 'complaints (1)' -> 'complaints')
      - Replaces hyphens with underscores
      - Lowercases
    """
    stem = Path(filename).stem
    stem = _PREFIX_RE.sub("", stem)
    stem = _SUFFIX_RE.sub("", stem)
    stem = stem.strip().lower().replace("-", "_").replace(" ", "_")
    return stem


def resolve_inputs(input_dir: Path) -> Dict[str, Optional[Path]]:
    """Scan input_dir and resolve each canonical input type to a file path.

    Returns:
        Dict mapping type_key -> Path (or None if not found).
        Also includes 'extra' -> list of unmatched files.
    """
    if not input_dir.exists():
        logger.warning(f"Input directory does not exist: {input_dir}")
        return {k: None for k in INPUT_TYPES}

    supported_exts = {
        ".csv", ".xlsx", ".xls", ".docx", ".doc", ".pdf",
        ".json", ".txt", ".md", ".markdown",
        ".png", ".jpg", ".jpeg", ".tiff", ".bmp",
    }

    resolved: Dict[str, Optional[Path]] = {k: None for k in INPUT_TYPES}
    unmatched: List[Path] = []
    deprecated_found: List[str] = []

    for f in sorted(input_dir.iterdir()):
        if f.is_dir() or f.name.startswith(".") or f.name.startswith("~"):
            continue
        if f.suffix.lower() not in supported_exts:
            continue

        norm_stem = _normalize_stem(f.name)

        # Check for deprecated inputs
        if norm_stem in _DEPRECATED_STEMS:
            deprecated_found.append(f.name)
            continue

        # Try exact stem match first (highest priority)
        matched = False
        for type_key, keywords in _KEYWORDS.items():
            # device_context: exact match only
            if type_key == "device_context":
                if norm_stem == "device_context":
                    resolved[type_key] = f
                    matched = True
                    break
                continue

            # Exact stem match
            if norm_stem == type_key:
                resolved[type_key] = f
                matched = True
                break

        if matched:
            continue

        # Try keyword substring match
        for type_key, keywords in _KEYWORDS.items():
            if type_key == "device_context":
                continue
            if any(kw in norm_stem for kw in keywords):
                # Only assign if not already resolved (first match wins)
                if resolved[type_key] is None:
                    resolved[type_key] = f
                    matched = True
                    break

        if not matched:
            unmatched.append(f)

    # Log deprecation warnings
    for fname in deprecated_found:
        console.print(
            f"  [yellow]Deprecated input ignored: {fname}[/yellow]\n"
            f"    PMCF data is now extracted from the CER document."
        )

    return resolved


def print_input_resolution(resolved: Dict[str, Optional[Path]]):
    """Print a formatted table of resolved inputs."""
    table = Table(title="Input Resolution")
    table.add_column("Type", style="cyan", min_width=18)
    table.add_column("Status", style="bold", min_width=10)
    table.add_column("File", style="green")

    for type_key, (desc, required, _exts) in INPUT_TYPES.items():
        path = resolved.get(type_key)
        if path:
            status = "FOUND"
            style = "green"
            filename = path.name
        elif required:
            status = "MISSING"
            style = "red"
            filename = f"(required: {desc})"
        else:
            status = "Optional"
            style = "dim"
            filename = ""

        table.add_row(
            type_key,
            f"[{style}]{status}[/{style}]",
            filename,
        )

    console.print(table)
    console.print()

