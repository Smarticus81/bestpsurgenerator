"""Sales data parser — supports CSV, Excel, and AI column mapping.

Flexibly handles varying column names, extra columns, and different file formats
by using the AI column mapper for intelligent field detection.
"""
import logging
import pandas as pd
from pathlib import Path
from typing import Any, Dict, Optional

from parsers.column_mapper import (
    infer_column_mapping,
    get_extra_column_context,
    ColumnMappingResult,
)

logger = logging.getLogger(__name__)


def parse_sales(
    filepath: Path,
    start_date: str,
    end_date: str,
    user_confirm_callback=None,
    sheet_name=None,
) -> Dict[str, Any]:
    """
    Parse sales data from CSV or Excel with AI column mapping.

    Handles:
    - Variable column names (AI-inferred)
    - Extra columns (preserved for LLM context)
    - CSV and Excel formats
    - Missing fields (graceful degradation — no longer raises ValueError)

    Args:
        filepath: Path to sales file (.csv, .xlsx, .xls)
        start_date: Period start (YYYY-MM-DD)
        end_date: Period end (YYYY-MM-DD)
        user_confirm_callback: Optional callback for confirming low-confidence mappings

    Returns:
        Standardized sales data dict
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
    mapping_result = infer_column_mapping(df, purpose="sales")

    # Handle low-confidence mappings
    if mapping_result.low_confidence and user_confirm_callback:
        mapping_result = user_confirm_callback(mapping_result, df)

    # Extract mapped column names
    date_col = mapping_result.get_source_column("date")
    year_col = mapping_result.get_source_column("year")
    month_col = mapping_result.get_source_column("month")
    qty_col = mapping_result.get_source_column("quantity")
    region_col = mapping_result.get_source_column("region")
    product_col = mapping_result.get_source_column("product")

    # Log mapping results
    _log_mappings(mapping_result, filepath.name)

    # Graceful handling when key columns are missing
    if not qty_col:
        logger.warning(f"No quantity column mapped in {filepath.name} — unit counts will be zero")

    # ── Build / resolve a proper date column ────────────────────────
    total_pre_filter = len(df)
    _date_col = _resolve_date_column(df, date_col, year_col, month_col)

    # Date filtering
    if _date_col:
        start = pd.Timestamp(start_date)
        end = pd.Timestamp(end_date)
        df = df.dropna(subset=[_date_col])
        df = df[(df[_date_col] >= start) & (df[_date_col] <= end)]
        date_col = _date_col  # use the resolved column for grouping
    else:
        logger.warning(f"No usable date in {filepath.name} — using all rows without date filtering")
        date_col = None

    # Quantity calculations
    total_units = 0
    by_month = {}
    by_region = {}
    by_product = {}

    if qty_col:
        df[qty_col] = pd.to_numeric(df[qty_col], errors="coerce").fillna(0).astype(int)
        total_units = int(df[qty_col].sum())

        if date_col:
            df["_month"] = df[date_col].dt.strftime("%Y-%m")
            by_month = df.groupby("_month")[qty_col].sum().to_dict()
            by_month = {k: int(v) for k, v in by_month.items()}

        if region_col:
            by_region = df.groupby(region_col)[qty_col].sum().to_dict()
            by_region = {str(k): int(v) for k, v in by_region.items()}

        if product_col:
            by_product = df.groupby(product_col)[qty_col].sum().to_dict()
            by_product = {str(k): int(v) for k, v in by_product.items()}

    # ── Country-level aggregation (for EEA calculation) ─────────────
    by_country = {}
    country_col = mapping_result.get_source_column("country")
    if not country_col:
        # Try common country column names directly
        for candidate in ["shipping_country", "customer_country", "country"]:
            if candidate in df.columns:
                country_col = candidate
                break
    if country_col and qty_col:
        by_country = df.groupby(country_col)[qty_col].sum().to_dict()
        by_country = {str(k): int(v) for k, v in by_country.items() if str(k) != "Unknown"}

    # Build extra column context for LLM
    extra_context = {}
    if mapping_result.unmapped_columns:
        extra_context = get_extra_column_context(df, mapping_result.unmapped_columns)

    return {
        "total_units": total_units,
        "by_month": by_month,
        "by_region": by_region,
        "by_country": by_country,
        "by_product": by_product,
        "period": {"start": start_date, "end": end_date},
        "source_file": filepath.name,
        "rows_processed": len(df),
        "rows_pre_filter": total_pre_filter,
        "column_mappings": mapping_result.to_dict(),
        "extra_columns": extra_context,
    }


# ── Date resolution helper ──────────────────────────────────────────

_MONTH_MAP = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4,
    "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _resolve_date_column(
    df: pd.DataFrame,
    date_col: Optional[str],
    year_col: Optional[str],
    month_col: Optional[str],
) -> Optional[str]:
    """
    Resolve or synthesize a proper datetime column.

    Priority (most reliable first):
    1. Year + month columns → synthesize date (1st of month)
    2. date_col with real full dates in a sane range → use it
    3. date_col (month names) + year_col → combine
    4. Year-only → Jan 1st of that year
    5. Return None
    """
    synth_col = "_synth_date"

    # 1) Best option: synthesize from explicit year + month columns
    if year_col and month_col:
        def _to_date(row):
            try:
                y = int(float(row[year_col]))
                m_raw = str(row[month_col]).strip().lower()
                m = _MONTH_MAP.get(m_raw)
                if m is None:
                    m = int(float(m_raw))
                return pd.Timestamp(year=y, month=m, day=1)
            except Exception:
                return pd.NaT
        df[synth_col] = df.apply(_to_date, axis=1)
        valid = df[synth_col].notna().sum()
        if valid > 0:
            logger.info(f"Synthesized date from '{year_col}' + '{month_col}' ({valid:,} valid rows)")
            return synth_col

    # 2) Try the mapped date column — must contain real parseable dates in a sane range
    if date_col:
        test = pd.to_datetime(df[date_col], errors="coerce")
        valid_mask = test.notna()
        valid_pct = valid_mask.mean()
        if valid_pct > 0.5:
            # Sanity check: are the dates in a reasonable range (1990–2050)?
            valid_dates = test[valid_mask]
            reasonable = ((valid_dates.dt.year >= 1990) & (valid_dates.dt.year <= 2050)).mean()
            if reasonable > 0.5:
                df[date_col] = test
                return date_col
            else:
                logger.info(f"Column '{date_col}' parsed as dates but {1-reasonable:.0%} outside 1990-2050 — skipping")
        else:
            logger.info(f"Column '{date_col}' only {valid_pct:.0%} parseable as dates")

    # 3) date_col has month names + year_col has years → combine
    if date_col and year_col:
        def _combo(row):
            try:
                y = int(float(row[year_col]))
                m_raw = str(row[date_col]).strip().lower()
                m = _MONTH_MAP.get(m_raw)
                if m is None:
                    m = int(float(m_raw))
                return pd.Timestamp(year=y, month=m, day=1)
            except Exception:
                return pd.NaT
        df[synth_col] = df.apply(_combo, axis=1)
        valid = df[synth_col].notna().sum()
        if valid > 0:
            logger.info(f"Synthesized date from '{date_col}' (month) + '{year_col}' ({valid:,} valid rows)")
            return synth_col

    # 4) Year-only fallback
    if year_col:
        def _year_to_date(row):
            try:
                y = int(float(row[year_col]))
                return pd.Timestamp(year=y, month=1, day=1)
            except Exception:
                return pd.NaT
        df[synth_col] = df.apply(_year_to_date, axis=1)
        valid = df[synth_col].notna().sum()
        if valid > 0:
            logger.info(f"Synthesized date from '{year_col}' only ({valid:,} valid rows)")
            return synth_col

    return None


def _log_mappings(result: ColumnMappingResult, filename: str):
    """Log column mapping results."""
    for target, mapping in result.mappings.items():
        src = f"[{mapping.mapping_source}]"
        conf = f"{mapping.confidence:.0%}"
        logger.info(f"  {filename}: {mapping.source_column} -> {target} {src} ({conf})")
    if result.unmapped_columns:
        logger.info(f"  {filename}: {len(result.unmapped_columns)} extra columns: {result.unmapped_columns[:10]}")
