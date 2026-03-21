"""CSI complaints parser — supports CSV, Excel, and AI column mapping.

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


def parse_complaints(
    filepath: Path,
    start_date: str,
    end_date: str,
    user_confirm_callback=None,
    sheet_name=None,
) -> Dict[str, Any]:
    """
    Parse complaints from CSV or Excel with AI column mapping.

    Handles:
    - Variable column names (AI-inferred)
    - Extra columns (preserved for LLM context)
    - CSV and Excel formats
    - Missing fields (graceful degradation)

    Args:
        filepath: Path to complaints file (.csv, .xlsx, .xls)
        start_date: Period start (YYYY-MM-DD)
        end_date: Period end (YYYY-MM-DD)
        user_confirm_callback: Optional callback for confirming low-confidence mappings

    Returns:
        Standardized complaints data dict
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
    mapping_result = infer_column_mapping(df, purpose="complaints")

    # Handle low-confidence mappings
    if mapping_result.low_confidence and user_confirm_callback:
        mapping_result = user_confirm_callback(mapping_result, df)

    # Extract mapped column names
    date_col = mapping_result.get_source_column("date")
    imdrf_col = mapping_result.get_source_column("imdrf_code")
    harm_col = mapping_result.get_source_column("harm")
    serious_col = mapping_result.get_source_column("serious")
    region_col = mapping_result.get_source_column("region")
    desc_col = mapping_result.get_source_column("description")
    number_col = mapping_result.get_source_column("complaint_number")

    # Log mapping results
    _log_mappings(mapping_result, filepath.name)

    # Date filtering (if date column found)
    total_pre_filter = len(df)
    if date_col:
        # Try standard parsing first
        parsed_dates = pd.to_datetime(df[date_col], errors="coerce")
        # Check if dates are clearly wrong (year < 1900 means mis-parsed, e.g., "April-22" → year 1)
        _bad_dates = parsed_dates.isna() | (parsed_dates.dt.year < 1900)
        # If >50% bad, try "Month-YY" format (e.g., "April-22" → 2022-04-01)
        if _bad_dates.sum() > len(df) * 0.5:
            try:
                parsed_dates = pd.to_datetime(
                    df[date_col].astype(str).str.strip(),
                    format="%B-%y", errors="coerce"
                )
            except Exception:
                pass
        _bad_dates = parsed_dates.isna() | (parsed_dates.dt.year < 1900)
        # If still >50% bad, try "Mon-YY" format (e.g., "Apr-22")
        if _bad_dates.sum() > len(df) * 0.5:
            try:
                parsed_dates = pd.to_datetime(
                    df[date_col].astype(str).str.strip(),
                    format="%b-%y", errors="coerce"
                )
            except Exception:
                pass
        df[date_col] = parsed_dates
        df = df.dropna(subset=[date_col])

        start = pd.Timestamp(start_date)
        end = pd.Timestamp(end_date)
        df = df[(df[date_col] >= start) & (df[date_col] <= end)]
    else:
        logger.warning(f"No date column mapped in {filepath.name} — using all rows")

    # Count unique complaints by complaint ID if available, else by row count
    if number_col:
        total_complaints = df[number_col].nunique()
    else:
        total_complaints = len(df)

    # By month
    by_month = {}
    if date_col:
        df["_month"] = df[date_col].dt.strftime("%Y-%m")
        by_month = df.groupby("_month").size().to_dict()
        by_month = {k: int(v) for k, v in by_month.items()}

    # By IMDRF code
    by_imdrf_code = {}
    if imdrf_col:
        by_imdrf_code = df[imdrf_col].fillna("Unknown").value_counts().to_dict()
        by_imdrf_code = {str(k): int(v) for k, v in by_imdrf_code.items()}

    # By harm category
    by_harm_category = {}
    if harm_col:
        by_harm_category = df[harm_col].fillna("No Harm").value_counts().to_dict()
        by_harm_category = {str(k): int(v) for k, v in by_harm_category.items()}

    # By region — normalize: strip whitespace, title-case
    by_region = {}
    if region_col:
        normalized = df[region_col].fillna("Unknown").astype(str).str.strip().str.title()
        by_region = normalized.value_counts().to_dict()
        by_region = {str(k): int(v) for k, v in by_region.items()}

    # Serious incidents
    serious_incidents = []
    if serious_col:
        serious_mask = df[serious_col].astype(str).str.upper().isin([
            "YES", "TRUE", "1", "Y", "SERIOUS", "REPORTABLE"
        ])
        serious_df = df[serious_mask]

        for _, row in serious_df.iterrows():
            incident = {
                "date": row[date_col].strftime("%Y-%m-%d") if date_col and pd.notna(row.get(date_col)) else "",
                "imdrf_code": str(row.get(imdrf_col, "Unknown")) if imdrf_col else "Unknown",
                "harm": str(row.get(harm_col, "Unknown")) if harm_col else "Unknown",
                "description": str(row.get(desc_col, "")) if desc_col else "",
            }
            if region_col:
                incident["region"] = str(row.get(region_col, "Unknown"))
            if number_col:
                incident["complaint_number"] = str(row.get(number_col, ""))
            serious_incidents.append(incident)

    # Complaint summaries
    complaint_summaries = []
    for _, row in df.iterrows():
        summary = {}
        if date_col and pd.notna(row.get(date_col)):
            summary["date"] = row[date_col].strftime("%Y-%m-%d")
        if number_col:
            summary["complaint_number"] = str(row.get(number_col, ""))
        if desc_col:
            summary["description"] = str(row.get(desc_col, ""))[:500]
        if imdrf_col:
            summary["imdrf_code"] = str(row.get(imdrf_col, ""))
        if harm_col:
            summary["harm"] = str(row.get(harm_col, ""))
        if region_col:
            summary["region"] = str(row.get(region_col, ""))
        if serious_col:
            summary["serious"] = str(row.get(serious_col, "")).upper() in [
                "YES", "TRUE", "1", "Y", "SERIOUS", "REPORTABLE"
            ]
        complaint_summaries.append(summary)

    # Cross-tabulation: harm × IMDRF code
    harm_by_imdrf = {}
    if harm_col and imdrf_col:
        for _, row in df.iterrows():
            harm_raw = row.get(harm_col, "Unknown")
            # Handle NaN / empty values — default to "No Harm"
            if pd.isna(harm_raw) or str(harm_raw).strip().lower() in ("", "nan", "none"):
                harm = "No Harm"
            else:
                harm = str(harm_raw).strip()
            imdrf = str(row.get(imdrf_col, "Unknown"))
            if harm not in harm_by_imdrf:
                harm_by_imdrf[harm] = {}
            harm_by_imdrf[harm][imdrf] = harm_by_imdrf[harm].get(imdrf, 0) + 1

    # Serious incidents by region × IMDRF
    serious_by_region_imdrf = {}
    if serious_col and region_col and imdrf_col:
        for inc in serious_incidents:
            region = inc.get("region", "Unknown")
            imdrf = inc.get("imdrf_code", "Unknown")
            key = f"{region}|{imdrf}"
            if key not in serious_by_region_imdrf:
                serious_by_region_imdrf[key] = {"count": 0, "complaint_numbers": []}
            serious_by_region_imdrf[key]["count"] += 1
            if "complaint_number" in inc:
                serious_by_region_imdrf[key]["complaint_numbers"].append(inc["complaint_number"])

    # Complaint number format
    complaint_number_format = ""
    if number_col and len(df) > 0:
        first_num = str(df.iloc[0].get(number_col, ""))
        complaint_number_format = first_num

    # Build extra column context for LLM
    extra_context = {}
    if mapping_result.unmapped_columns:
        extra_context = get_extra_column_context(df, mapping_result.unmapped_columns)

    return {
        "total_complaints": total_complaints,
        "by_month": by_month,
        "by_imdrf_code": by_imdrf_code,
        "by_harm_category": by_harm_category,
        "by_region": by_region,
        "serious_incidents": serious_incidents,
        "serious_incident_count": len(serious_incidents),
        "complaint_summaries": complaint_summaries,
        "harm_by_imdrf": harm_by_imdrf,
        "serious_by_region_imdrf": serious_by_region_imdrf,
        "complaint_number_format": complaint_number_format,
        "period": {"start": start_date, "end": end_date},
        "source_file": filepath.name,
        "rows_processed": total_complaints,
        "rows_pre_filter": total_pre_filter,
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
