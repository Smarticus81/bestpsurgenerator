"""CAPA parser — supports CSV, Excel, and AI column mapping.

Flexibly handles varying column names, extra columns, and different file formats
by using the AI column mapper for intelligent field detection.
"""
import logging
import pandas as pd
from pathlib import Path
from typing import Any, Dict

from parsers.column_mapper import (
    infer_column_mapping,
    get_extra_column_context,
    ColumnMappingResult,
)

logger = logging.getLogger(__name__)


def parse_capa(
    filepath: Path,
    start_date: str,
    end_date: str,
    user_confirm_callback=None,
    sheet_name=None,
) -> Dict[str, Any]:
    """
    Parse CAPA data from CSV or Excel with AI column mapping.

    Handles:
    - Variable column names (AI-inferred)
    - Extra columns (preserved for LLM context)
    - CSV and Excel formats
    - All fields optional (graceful degradation)

    Args:
        filepath: Path to CAPA file (.csv, .xlsx, .xls)
        start_date: Period start (YYYY-MM-DD)
        end_date: Period end (YYYY-MM-DD)
        user_confirm_callback: Optional callback for confirming low-confidence mappings

    Returns:
        Standardized CAPA data dict
    """
    filepath = Path(filepath)
    ext = filepath.suffix.lower()

    # Read file with encoding detection
    if ext == ".csv":
        from parsers.universal import _read_csv_with_encoding
        df = _read_csv_with_encoding(filepath)
    else:
        df = pd.read_excel(filepath, sheet_name=sheet_name or 0)

    # Normalize column names
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

    # Infer column mappings
    mapping_result = infer_column_mapping(df, purpose="capa")

    # Handle low-confidence mappings
    if mapping_result.low_confidence and user_confirm_callback:
        mapping_result = user_confirm_callback(mapping_result, df)

    # Extract mapped column names
    id_col = mapping_result.get_source_column("capa_number")
    title_col = mapping_result.get_source_column("title")
    status_col = mapping_result.get_source_column("status")
    open_date_col = mapping_result.get_source_column("open_date")
    close_date_col = mapping_result.get_source_column("close_date")
    root_cause_col = mapping_result.get_source_column("root_cause")
    type_col = mapping_result.get_source_column("type")

    # Log mapping results
    _log_mappings(mapping_result, filepath.name)

    # Parse dates if available
    if open_date_col:
        df[open_date_col] = pd.to_datetime(df[open_date_col], errors="coerce")
    if close_date_col:
        df[close_date_col] = pd.to_datetime(df[close_date_col], errors="coerce")

    # Filter to CAPAs relevant to the surveillance period
    start = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date)

    if open_date_col and close_date_col:
        mask = (df[open_date_col] <= end) & (
            df[close_date_col].isna() | (df[close_date_col] >= start)
        )
        df = df[mask]
    elif open_date_col:
        df = df[df[open_date_col] <= end]
    else:
        logger.warning(f"No date columns mapped in {filepath.name} — using all rows")

    # Build CAPA records
    capa_records = []
    for _, row in df.iterrows():
        record = {}

        if id_col:
            record["capa_number"] = str(row.get(id_col, ""))
        if title_col:
            record["title"] = str(row.get(title_col, ""))
        if status_col:
            record["status"] = str(row.get(status_col, ""))
        if open_date_col and pd.notna(row.get(open_date_col)):
            record["open_date"] = row[open_date_col].strftime("%Y-%m-%d")
        if close_date_col and pd.notna(row.get(close_date_col)):
            record["close_date"] = row[close_date_col].strftime("%Y-%m-%d")
        if root_cause_col:
            record["root_cause"] = str(row.get(root_cause_col, ""))
        if type_col:
            record["type"] = str(row.get(type_col, ""))

        # Preserve common CAPA CSV fields even when the generic mapper does
        # not classify them. Section I/Table 9 is deterministic, so retaining
        # these columns avoids relying on the LLM to recover CAPA details.
        alias_map = {
            "capa_id": "capa_number",
            "capa_number": "capa_number",
            "initiation_date": "initiation_date",
            "date_initiated": "initiation_date",
            "actions_taken": "description",
            "trigger": "description",
            "effectiveness": "effectiveness",
            "device_name": "scope",
            "device_model": "scope",
            "target_completion_date": "target_completion_date",
        }
        for src, dest in alias_map.items():
            if src in df.columns and not record.get(dest):
                val = row.get(src)
                if pd.notna(val):
                    record[dest] = str(val)

        capa_records.append(record)

    # Summary statistics
    status_counts = {}
    if status_col:
        status_counts = df[status_col].fillna("Unknown").value_counts().to_dict()
        status_counts = {str(k): int(v) for k, v in status_counts.items()}

    root_cause_counts = {}
    if root_cause_col:
        root_cause_counts = df[root_cause_col].fillna("Unknown").value_counts().to_dict()
        root_cause_counts = {str(k): int(v) for k, v in root_cause_counts.items()}

    # Build extra column context for LLM
    extra_context = {}
    if mapping_result.unmapped_columns:
        extra_context = get_extra_column_context(df, mapping_result.unmapped_columns)

    return {
        "total_capas": len(capa_records),
        "capa_records": capa_records,
        "status_counts": status_counts,
        "root_cause_counts": root_cause_counts,
        "period": {"start": start_date, "end": end_date},
        "source_file": filepath.name,
        "column_mappings": mapping_result.to_dict(),
        "extra_columns": extra_context,
    }


def _log_mappings(result: ColumnMappingResult, filename: str):
    """Log column mapping results."""
    for target, mapping in result.mappings.items():
        src = f"[{mapping.mapping_source}]"
        conf = f"{mapping.confidence:.0%}"
        logger.info(f"  {filename}: {mapping.source_column} -> {target} {src} ({conf})")
    if result.unmapped_columns:
        logger.info(f"  {filename}: {len(result.unmapped_columns)} extra columns: {result.unmapped_columns[:10]}")
