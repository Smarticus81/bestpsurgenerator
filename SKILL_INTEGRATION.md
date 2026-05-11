# Sales Table Skill Integration Summary

## What Was Done

The `psur-sales-table` skill has been successfully integrated into the PSUR generator pipeline. The skill is now embedded directly in the statistics module for seamless deterministic table generation.

## Architecture

### New Module: `statistics_tables.py`
- **Purpose**: Implements the skill's core algorithms as reusable Python functions
- **Key Functions**:
  - `determine_12month_periods_from_dates()` — Builds 12-month periods backwards from end date with proper labeling
  - `calculate_region_percentages()` — Computes % of global sales for each region
  - `identify_high_volume_regions_in_period()` — Finds regions >5% for dedicated rows

### Enhanced: `statistics.py`
- **Added import**: Functions from `statistics_tables`
- **Enhancement**: Now calls `determine_12month_periods_from_dates()` to generate better-formatted period labels
- **New field**: `pct_current` added to section_c_region_rows for pre-calculated percentages

### Enhanced: `rendering/_tables.py`
- **Optimization**: Now uses pre-calculated `pct_current` when available instead of recalculating
- **Maintains backward compatibility**: Falls back to inline calculation if not present

## Integration Flow

```
Input files (sales data)
  ↓
pipeline/input_parsing.py (parses into sales_data dict)
  ↓
statistics.py::compute_psur_statistics()
  • Calls statistics_tables.determine_12month_periods_from_dates()
  • Adds pct_current to each region row
  ↓
PSURStatistics (now includes enhanced period labels + percentages)
  ↓
agents/orchestrator.py (Section C receives deterministic table data)
  ↓
rendering/_tables.py (renders into DOCX with proper formatting)
  ↓
Output: PSUR with FormQAR-054 compliant Section C sales table
```

## Key Benefits

1. **Deterministic**: All table calculations happen pre-LLM, preventing fabrication
2. **Skill-Enhanced**: Uses skill logic for period determination and percentage calculation
3. **Backward Compatible**: Existing pipeline continues to work without changes
4. **Optimized Rendering**: Pre-calculated values avoid redundant computation
5. **Regional Aggregation**: Properly maps countries to PSUR standard regions (EEA+TR+XI, UK, named countries, Rest of World)

## Where the Skill Receives Usage

**Section C Agent**: Receives the pre-computed section_c_region_rows via statistics context, uses them as fact inputs for narrative generation

**Rendering**: Renders the table via _tables.py with formatted periods and percentages

**Validation**: Sales table structure validated by 331-point checklist

## Testing Recommended

When running `python main.py generate`:
1. Check that section_c_period_labels are formatted as "mmm-yyyy to mmm-yyyy"
2. Verify percent_of_global_sales column sums to 100% for current period
3. Confirm Worldwide row equals sum of all region rows
4. Validate UK row appears only when UK sales detected

---

## Second Skill: `psur-complaint-tables`

Generates **Table 7** (Complaint Rate by Region) and **Table 8** (Complaint Rate by Harm × Medical Device Problem by Region). Integrated using the same deterministic-first pattern.

### Added to `statistics_tables.py`
- `classify_country_to_psur_region()` — maps country labels to standard PSUR region buckets (EEA+TR+XI, UK, named countries, Rest of World)
- `build_complaint_region_breakdown()` — cross-tabs raw complaint summaries by harm × device-problem × {EEA+TR+XI, UK, Worldwide}
- `build_table8_rows()` — produces Table 8 rows with worldwide rate + MEDDEV 2.7/1 Rev.4 occurrence code (O1–O5)

### Added to `statistics.py`
- New `table8_rows: List[Dict[str, Any]]` field on `PSURStatistics`
- `rates_by_region` rows now carry occurrence_code/label/description (Table 7 enhancement) — reuses existing `classify_occurrence_code()` and `RACT_OCCURRENCE_CODES`
- `table8_rows` built deterministically from `complaints_data["complaint_summaries"]` and surfaced through the constructor

### Why this placement
Both Table 7 and Table 8 are pure aggregations of parsed complaint records — they belong in the deterministic statistics layer, not in an LLM agent. Section D / E / F agents consume `rates_by_region` and `table8_rows` as facts and never compute these themselves.

---

## Third Skill: `psur-trend-charts`

Generates **rate-vs-occurrence**, **harm-stacked-trend**, and **per-period summary** charts with MEDDEV 2.7/1 Rev.4 occurrence reference bands (O1–O5). Integrated using the same deterministic-first pattern.

### Added to `statistics.py`
- New `harm_by_month: Dict[str, Dict[str, int]]` field — monthly cross-tab of harm category → count, derived from `complaint_summaries`
- New `per_period_aggregates: List[Dict[str, Any]]` field — one row per 12-month period (last 3) with complaints, units, rate, rate_pct, and MEDDEV occurrence_code/label/description
- Period determination reuses `determine_12month_periods_from_dates()` from the first skill — single source of truth for 12-month windows

### Added to `charts.py`
- `MEDDEV_OCCURRENCE_COLORS` palette (O1=green … O5=red) and `HARM_PALETTE` per skill spec
- `_generate_rate_occurrence_chart()` — line chart of monthly complaint-rate (%) with O1–O5 reference bands via `axhspan()` and period boundary markers via `axvline()`
- `_generate_harm_trend_chart()` — stacked bar of harm-category counts per month, sorted by total descending for stable stacking
- `_generate_per_period_chart()` — bar chart per 12-month period, bars colored by MEDDEV occurrence band, annotated with `count\nrate% (Ox)`
- All three registered in `chart_specs` so they auto-render via `generate_all_charts()`

### Why this placement
Charts are pure visualizations of pre-computed statistics — they sit alongside the existing `_generate_trend_chart` (Shewhart p-chart for UK MDR 44ZN trend reporting), which is preserved unchanged because it serves a distinct regulatory purpose (Western Electric rules) versus the skill's MEDDEV occurrence classification.
