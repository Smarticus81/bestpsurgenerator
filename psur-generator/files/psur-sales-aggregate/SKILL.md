---
name: psur-sales-aggregate
description: Aggregate ERP sales data by FormQAR-054 region and reporting period for PSUR Table 1. Maps countries to regulatory regions (EEA+TR+XI, UK, etc.), computes current and preceding period totals, and calculates the complaint rate denominator. Use before building Table 1 or computing complaint rates.
when_to_use: Trigger when processing sales CSV for PSUR, aggregating sales by region, computing complaint denominators, or preparing Table 1 data.
allowed-tools: Bash(python3 *) Read
---

# Sales Data Aggregation for PSUR

You process the raw ERP sales CSV into the regional aggregation required by FormQAR-054 Table 1 and compute the denominator used for all complaint rate calculations in Table 7.

## Input

A CSV file with at minimum these columns:
- `Customer Country` — country name (inconsistent casing/naming expected)
- `Month` — month name (January, February, etc.)
- `Quantity` — integer units
- `Calendar year` — 4-digit year
- Optional: `ItemNumber`, `ProductGroup` (for filtering to specific devices)

The CSV may use Latin-1 encoding. Try UTF-8 first, fall back to `latin-1`.

## Processing Steps

Run this Python script, adapting file paths as needed:

```python
import pandas as pd
from dateutil.relativedelta import relativedelta
from datetime import date

# --- CONFIGURATION (set these per device) ---
SALES_CSV = 'sales_data.csv'
REPORTING_START = date(2025, 5, 1)   # from device_context.json
REPORTING_END = date(2026, 4, 30)
PRODUCT_FILTER = None  # e.g., 'Insorb Stapler' or None for all

# --- LOAD ---
for enc in ['utf-8', 'latin-1']:
    try:
        df = pd.read_csv(SALES_CSV, encoding=enc)
        break
    except UnicodeDecodeError:
        continue

if PRODUCT_FILTER:
    df = df[df['ProductGroup'].str.contains(PRODUCT_FILTER, case=False, na=False)]

# --- DATE PROCESSING ---
month_map = {'January':1,'February':2,'March':3,'April':4,'May':5,'June':6,
             'July':7,'August':8,'September':9,'October':10,'November':11,'December':12}
df['MonthNum'] = df['Month'].map(month_map)
df['YearMonth'] = df['Calendar year'] * 100 + df['MonthNum']

# --- PERIOD BOUNDARIES ---
curr_start_ym = REPORTING_START.year * 100 + REPORTING_START.month
curr_end_ym = REPORTING_END.year * 100 + REPORTING_END.month
prec_start = REPORTING_START - relativedelta(months=12)
prec_start_ym = prec_start.year * 100 + prec_start.month
prec_end_ym = curr_start_ym - 1

# --- REGION MAPPING ---
EEA_TR = {
    'Austria','Belgium','Bulgaria','Croatia','Cyprus','Czech Republic',
    'Czechia','Denmark','Estonia','Finland','France','Germany','Greece',
    'Hungary','Ireland','Italy','Latvia','Lithuania','Luxembourg','Malta',
    'Netherlands','Poland','Portugal','Romania','Slovakia','Slovenia',
    'Spain','Sweden','Iceland','Liechtenstein','Norway','Turkey','Switzerland'
}

def map_region(c):
    c = str(c).strip()
    if c in ('United States of America','United States','USA','US'): return 'United States'
    if c in ('United Kingdom','UK','Great Britain'): return 'UK'
    if c == 'Australia': return 'Australia'
    if c == 'Brazil': return 'Brazil'
    if c == 'Canada': return 'Canada'
    if c == 'China': return 'China'
    if c == 'Japan': return 'Japan'
    if c in EEA_TR: return 'EEA+TR+XI'
    return 'Rest of World'

df['Region'] = df['Customer Country'].apply(map_region)

# --- AGGREGATE ---
curr = df[(df['YearMonth'] >= curr_start_ym) & (df['YearMonth'] <= curr_end_ym)]
prec = df[(df['YearMonth'] >= prec_start_ym) & (df['YearMonth'] <= prec_end_ym)]

curr_by_region = curr.groupby('Region')['Quantity'].sum()
prec_by_region = prec.groupby('Region')['Quantity'].sum()
ww_curr = curr_by_region.sum()
ww_prec = prec_by_region.sum()

# --- MONTHLY BREAKDOWN (for UCL trending) ---
monthly = curr.groupby('YearMonth')['Quantity'].sum()

# --- OUTPUT ---
REGIONS = ['EEA+TR+XI','Australia','Brazil','Canada','China','Japan',
           'UK','United States','Rest of World']

print(f"\n{'='*60}")
print(f"SALES AGGREGATION RESULTS")
print(f"Current period: {REPORTING_START} to {REPORTING_END}")
print(f"Preceding period: {prec_start} to {REPORTING_START - relativedelta(days=1)}")
print(f"{'='*60}")
print(f"\n{'Region':<20} {'Preceding':>12} {'Current':>12} {'Pct':>8}")
print(f"{'-'*52}")
for r in REGIONS:
    c = int(curr_by_region.get(r, 0))
    p = int(prec_by_region.get(r, 0))
    pct = f"{c/ww_curr*100:.1f}%" if ww_curr > 0 else "0.0%"
    print(f"{r:<20} {p:>12,} {c:>12,} {pct:>8}")
print(f"{'-'*52}")
print(f"{'Worldwide':<20} {int(ww_prec):>12,} {int(ww_curr):>12,} {'100.0%':>8}")
print(f"\nDenominator for complaint rates: {int(ww_curr):,}")
print(f"Period change: {(ww_curr-ww_prec)/ww_prec*100:+.1f}%" if ww_prec > 0 else "N/A")
print(f"\nMonthly sales (for UCL trending):")
for ym, qty in sorted(monthly.items()):
    print(f"  {ym}: {int(qty):,}")
```

## Output

The script prints a formatted table ready to populate Table 1, plus the worldwide total used as the denominator for all Table 7 complaint rate calculations, plus monthly sales for UCL computation in Section G.
