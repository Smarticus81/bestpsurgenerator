"""Per-category input format contract for the PSUR ingestion pipeline.

Canonical formats:
  CSV  (tabular)  — complaints, sales/distribution, external vigilance, CAPA, FSCA
  JSON (structured) — clinical safety, clinical performance, RACT/risk,
                      PMS Plan, previous PSUR, coding dictionaries (e.g. IMDRF)

Excel (.xlsx) is treated as a transport format only and is normalized to
CSV in-memory before parsing. DOCX/PDF are NOT canonical hard inputs; the
pipeline still accepts them but logs a warning and routes them through the
LLM-based extractor (cer_extractor / pms_plan / previous_psur) to produce
structured data.

Two entry points:

* :func:`audit_discovered_formats` — print a compliance summary of the
  files returned by :func:`pipeline.discovery.auto_discover_inputs`.
* :func:`normalize_to_canonical` — for a given (category, path), return
  the path the parser should actually read. Converts xlsx -> csv on the fly.
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from rich.console import Console
from rich.table import Table

logger = logging.getLogger(__name__)
console = Console()


# ── Canonical format per category ──────────────────────────────────────
# "csv"  -> tabular records expected
# "json" -> structured object expected
# "image" / "passthrough" -> no format enforcement
INPUT_FORMAT_CONTRACT: Dict[str, str] = {
    # CSV-typed (tabular)
    "sales":         "csv",
    "complaints":    "csv",
    "capa":          "csv",
    "fsca":          "csv",
    "external_db":   "csv",   # external vigilance / registry data
    # JSON-typed (structured)
    "ract":          "json",  # risk data, hazards, thresholds
    "pms_plan":      "json",
    "previous_psur": "json",
    "device_context": "json",
    "coding_dictionary": "json",  # taxonomy only (e.g. IMDRF Annex A/F) — never device identity
    "cer":           "json",  # clinical safety + performance (CER-derived)
    "ifu":           "json",
    "rmf":           "json",
    "pmcf":          "json",
    # Workbook / image categories — no enforcement
    "analysis_workbook": "passthrough",
    "chart_sales":   "image",
    "chart_trend":   "image",
}

CSV_EXTS  = {".csv"}
JSON_EXTS = {".json"}
XLSX_EXTS = {".xlsx", ".xls"}
DOC_EXTS  = {".docx", ".doc", ".pdf"}
TEXT_EXTS = {".txt", ".md", ".markdown"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tiff", ".bmp"}


def _classify_actual_format(path: Path) -> str:
    """Return one of: csv, json, xlsx, doc, text, image, other."""
    ext = path.suffix.lower()
    if ext in CSV_EXTS:   return "csv"
    if ext in JSON_EXTS:  return "json"
    if ext in XLSX_EXTS:  return "xlsx"
    if ext in DOC_EXTS:   return "doc"
    if ext in TEXT_EXTS:  return "text"
    if ext in IMAGE_EXTS: return "image"
    return "other"


def assess_compliance(category: str, path: Path) -> Tuple[str, str]:
    """Return (status, message) for a single (category, file) pair.

    status ∈ {"canonical", "transport", "non_canonical", "unknown_category",
              "not_applicable"}.
    """
    expected = INPUT_FORMAT_CONTRACT.get(category)
    actual = _classify_actual_format(path)

    if expected is None:
        return "unknown_category", f"No format contract defined for '{category}'."
    if expected in ("image", "passthrough"):
        return "not_applicable", ""

    if expected == "csv":
        if actual == "csv":
            return "canonical", ""
        if actual == "xlsx":
            return "transport", "xlsx is a transport format — will be normalized to CSV before parsing."
        if actual == "doc":
            return "non_canonical", "DOCX/PDF is NOT a canonical hard input for tabular data — LLM extraction will run."
        if actual == "json":
            return "non_canonical", f"Expected CSV for {category}, got JSON. Will attempt to read but data may be malformed."
        return "non_canonical", f"Expected CSV for {category}, got {actual}."

    if expected == "json":
        if actual == "json":
            return "canonical", ""
        if actual == "doc":
            return "non_canonical", "DOCX/PDF is NOT a canonical hard input for structured data — LLM extraction will run."
        if actual in ("csv", "xlsx"):
            return "non_canonical", f"Expected JSON for {category}, got {actual}. Tabular data will be wrapped, but please supply a structured JSON file."
        return "non_canonical", f"Expected JSON for {category}, got {actual}."

    return "not_applicable", ""


def audit_discovered_formats(discovered: Dict[str, List[Path]]) -> Dict[str, List[Tuple[Path, str, str]]]:
    """Print a compliance table for all discovered files.

    Returns a dict keyed by category → list of (path, status, message)
    for downstream callers that want to act on the assessment.
    """
    report: Dict[str, List[Tuple[Path, str, str]]] = {}
    rows: List[Tuple[str, str, str, str]] = []  # category, file, status, msg

    for category, files in discovered.items():
        if not files:
            continue
        for fp in files:
            status, msg = assess_compliance(category, fp)
            report.setdefault(category, []).append((fp, status, msg))
            if status not in ("canonical", "not_applicable", "unknown_category"):
                rows.append((category, fp.name, status, msg))

    if not rows:
        return report

    table = Table(title="Input Format Compliance")
    table.add_column("Category", style="cyan")
    table.add_column("File", style="white")
    table.add_column("Status", style="yellow")
    table.add_column("Action", style="dim")
    for cat, name, status, msg in rows:
        style = "yellow" if status == "transport" else "red"
        table.add_row(cat, name, f"[{style}]{status}[/{style}]", msg)
    console.print(table)
    return report


def normalize_xlsx_to_csv(xlsx_path: Path, sheet_name: Optional[str] = None) -> Path:
    """Convert an .xlsx file to a temporary .csv and return the new path.

    The CSV is written to the system temp dir with a deterministic name so
    repeat conversions in the same run hit the same file. If pandas/openpyxl
    cannot read the workbook, the original path is returned unchanged.
    """
    try:
        import pandas as pd
    except Exception as e:
        logger.warning("pandas unavailable for xlsx→csv conversion: %s", e)
        return xlsx_path

    try:
        if sheet_name is not None:
            df = pd.read_excel(xlsx_path, sheet_name=sheet_name)
        else:
            df = pd.read_excel(xlsx_path)
    except Exception as e:
        logger.warning("xlsx→csv conversion failed for %s: %s", xlsx_path.name, e)
        return xlsx_path

    suffix = f"__{sheet_name}" if sheet_name else ""
    out_path = Path(tempfile.gettempdir()) / f"psur_norm_{xlsx_path.stem}{suffix}.csv"
    try:
        df.to_csv(out_path, index=False)
    except Exception as e:
        logger.warning("Could not write normalized CSV %s: %s", out_path, e)
        return xlsx_path
    logger.info("Normalized %s → %s (xlsx is transport format only)", xlsx_path.name, out_path.name)
    return out_path


def normalize_to_canonical(category: str, path: Optional[Path]) -> Optional[Path]:
    """Return the path the parser should read for `category`.

    For CSV-typed categories receiving xlsx, returns a temp .csv. For
    everything else (canonical, JSON-typed, doc that needs LLM extraction)
    the original path is returned unchanged so the existing parser handles it.
    """
    if path is None:
        return None
    expected = INPUT_FORMAT_CONTRACT.get(category)
    if expected != "csv":
        return path
    if _classify_actual_format(path) == "xlsx":
        return normalize_xlsx_to_csv(path)
    return path
