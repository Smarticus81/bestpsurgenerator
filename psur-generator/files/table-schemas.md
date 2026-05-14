# Table Schemas — FormQAR-054

This file defines the exact schema for each PSUR table. Each section includes: columns, widths, row order, population logic, data sources, and failure modes.

---

## Table 1: Annual Number of Devices Sold by Region

**Use when:** PSUR cadence = annually (Class IIb, III, implantable). DELETE Table 2 (biennial) from the document.

### Columns (5 total, sum = 9,360 DXA)

| # | Header | Width | Alignment | Content |
|---|--------|-------|-----------|---------|
| 1 | Region | 1,800 | Left | Region name |
| 2 | Preceding 12-Month Period *(date range)* | 1,900 | Right | Integer units sold |
| 3 | Current Data Collection Period *(date range)* | 1,900 | Right | Integer units sold |
| 4 | 12-Month Total | 1,900 | Right | = Column 3 for annual |
| 5 | 12-Month Percent of Global Sales | 1,860 | Right | (region / worldwide) × 100, 1 decimal |

### Rows (fixed order, all mandatory)

```
EEA+TR+XI    ← European Economic Area + Turkey + Northern Ireland
Australia
Brazil
Canada
China
Japan
UK           ← England, Scotland, Wales ONLY (not Northern Ireland)
United States
[dynamic]    ← any country with >5% of global sales, add row
Rest of World
Worldwide    ← BOLD, gray fill, = sum of all rows above
```

### Column 2 — Preceding Period (MUST be populated)

The preceding period is the 12 months immediately before the current reporting period. If reporting period = May 2025 to Apr 2026, then preceding = May 2024 to Apr 2025.

```python
# Compute preceding period boundaries
from dateutil.relativedelta import relativedelta
preceding_start = reporting_start - relativedelta(months=12)
preceding_end = reporting_start - relativedelta(days=1)

# Filter sales CSV
preceding_df = sales_df[
    (sales_df['YearMonth'] >= preceding_start_ym) &
    (sales_df['YearMonth'] <= preceding_end_ym)
]
```

If the sales CSV does not contain preceding period data, write `Data not available` — never output dashes.

### Country-to-Region Mapping

```python
EEA_TR = {
    'Austria','Belgium','Bulgaria','Croatia','Cyprus','Czech Republic',
    'Czechia','Denmark','Estonia','Finland','France','Germany','Greece',
    'Hungary','Ireland','Italy','Latvia','Lithuania','Luxembourg','Malta',
    'Netherlands','Poland','Portugal','Romania','Slovakia','Slovenia',
    'Spain','Sweden','Iceland','Liechtenstein','Norway','Turkey',
    'Switzerland'  # grouped here per CooperSurgical convention
}

def map_region(country):
    c = country.strip()
    if c in ('United States of America','United States','USA','US'):
        return 'United States'
    elif c in ('United Kingdom','UK','Great Britain'):
        return 'UK'
    elif c == 'Australia': return 'Australia'
    elif c == 'Brazil': return 'Brazil'
    elif c == 'Canada': return 'Canada'
    elif c == 'China': return 'China'
    elif c == 'Japan': return 'Japan'
    elif c in EEA_TR: return 'EEA+TR+XI'
    else: return 'Rest of World'
```

### Failure Modes

- ✗ Preceding column shows all dashes — compute from CSV
- ✗ Both Table 1 AND Table 2 rendered — delete unused variant
- ✗ `[Any other countries...]` placeholder row left in — remove or replace
- ✗ Percentages don't sum to 100.0% — recompute
- ✗ Worldwide ≠ sum of regions — recompute
- ✗ Template instruction text in header — strip `[Use this table if...]`

---

## Tables 2–4: Serious Incidents (EU/UK ONLY)

### CRITICAL — What Goes in These Tables

These tables report **EU MDR Article 2(65) / UK MDR serious incidents ONLY**. This is NOT the same as US FDA MDRs.

An event is a serious incident if it caused:
- Death
- Serious deterioration in state of health (hospitalization, life-threatening, permanent impairment, required medical/surgical intervention to prevent the above)
- Serious public health threat

A skin stapler laceration that resolves with local wound care = FDA MDR (serious injury) but ≠ EU MDR serious incident.

```
POPULATION LOGIC:
1. Read complaints where MDR Issued = 'Yes'
2. For EACH: evaluate against Art. 2(65) criteria above
3. Only events meeting those criteria appear in Tables 2-4
4. All FDA MDRs (including non-qualifying ones) are discussed 
   in Section D narrative and Section F
```

### Table 2: Serious Incidents by IMDRF Annex A (MDP) by Region

| # | Header | Width | Content |
|---|--------|-------|---------|
| 1 | Region | 1,500 | EEA+TR+XI / UK / Worldwide |
| 2 | IMDRF Problem Code & Term | 3,200 | Specific IMDRF Annex A MDP term |
| 3 | N (current period) | 1,200 | Count (center) |
| 4 | Rate (%) | 1,200 | N / regional_sales × 100 (center) |
| 5 | Complaint number | 2,260 | Comma-separated references |

**If zero serious incidents (common):**
```
EEA+TR+XI | N/A — No serious incident | 0 | 0.0000% | N/A
UK        | N/A — No serious incident | 0 | 0.0000% | N/A
Worldwide | N/A — No serious incident | 0 | 0.0000% | N/A
```

### Table 3: Serious Incidents by IMDRF Annex C (Cause)
Same 5-column schema as Table 2. Column 2 header changes to "IMDRF Cause Code & Term." Same zero-event pattern.

### Table 4: IMDRF Annex F (Health Impact) × Annex D (Investigation Conclusion)

| # | Header | Width | Content |
|---|--------|-------|---------|
| 1 | IMDRF Health Impact (Annex F) code and term, by region | 2,400 | Region + Annex F term |
| 2 | Number of serious incidents | 1,400 | Count |
| 3-6 | Investigation conclusion code+term 1-4 % | 1,140 each | Annex D conclusion |

### Failure Modes

- ✗ FDA MDRs placed in these tables — WRONG, only Art. 2(65) events
- ✗ "Worldwide: 10 lacerations" — these are FDA MDRs, not EU/UK SIs
- ✗ Cells left blank — write "0" and "N/A"
- ✗ Rate as "0" without % — use "0.0000%"

---

## Table 6: Feedback by Type and Source

| # | Header | Width | Content |
|---|--------|-------|---------|
| 1 | Feedback Type | 2,000 | Complaint / Non-complaint / PMCF-derived |
| 2 | Source | 2,400 | Who provided feedback |
| 3 | Count | 1,200 | Integer (center) |
| 4 | Summary | 3,760 | Brief narrative |

### Standard Rows

```
Row 1: Complaint | End-users | [total_complaints] | "All complaints in Section F."
Row 2: Non-complaint | Distributors/importers | 0 | "No safety-related feedback outside complaints."
Row 3: Non-complaint | Sales/Customer Service | 0 | "No qualitative themes impacting risk profile."
Row 4: PMCF-derived | [PMCF plan ref] | 0 | "No new signals from PMCF activity (see Section L)."
```

---

## Table 7: Complaint Rate by Harm and Medical Device Problem (Annual)

**THE MOST IMPORTANT TABLE IN THE PSUR.**

**Use when:** PSUR cadence = annually. DELETE the biennial variant.

### Columns (3 total, sum = 9,360 DXA)

| # | Header | Width | Alignment | Content |
|---|--------|-------|-----------|---------|
| 1 | Harm / Medical Device Problem | 4,400 | Left | Hierarchical (see below) |
| 2 | Current 12-Month Data Collection Period *(date range)* | 2,680 | Center | Rate (Count) |
| 3 | Max Expected Rate of Occurrence (from the RACT) | 2,280 | Center | RACT threshold |

### Hierarchical Structure

Column 1 uses a two-level hierarchy. Harm categories are bold header rows with gray fill. Medical Device Problems are indented rows under their parent Harm.

```
[Harm A — BOLD, fill #F2F2F2]
    [MDP 1 under Harm A — indent 360 DXA, normal weight]
    [MDP 2 under Harm A — indent 360 DXA, normal weight]
[Harm B — BOLD, fill #F2F2F2]
    [MDP 1 under Harm B — indent 360 DXA]
[No Health Consequence or Impact — BOLD, fill #F2F2F2]
    [MDP 1 — indent 360 DXA]
    [MDP 2 — indent 360 DXA]
[Grand Total — BOLD, fill #D9D9D9]
```

### Column 2 — Rate (Count) Format

Show rate first, then count in parentheses: `0.0155% (10)`

Rate = (complaint_count / total_units_sold) × 100, to 4 decimal places.

### Column 3 — Max Expected Rate from RACT

```
IF RACT data available:
  Show occurrence level: "≤0.1% (O2)"
  O1 = ≤0.01%   O2 = ≤0.1%   O3 = ≤1%   O4 = ≤10%   O5 = >10%

IF RACT not available:
  Show "N/A — RACT not provided" (not blank, not "—")

FOR Harm parent rows:
  Show the applicable RACT level or "N/A"

FOR "No Health Consequence" parent row:
  Column 3 = "N/A"

FOR Grand Total:
  Column 3 = "—"
```

### IMDRF Classification Rules for Table 7

**BEFORE building Table 7, invoke `/psur-imdrf-classify` to classify every complaint.** Table 7 cannot be built until classification is complete.

```
RULE 1: NEVER output "Unknown / Not yet determined" as a Harm.
RULE 2: NEVER output parent-level IMDRF codes as MDPs.
        "Device issues, consequence or impact to patient or user unknown" 
        is a PARENT NODE — not a valid classification endpoint.
RULE 3: Harm = patient outcome. MDP = what the device did wrong.
RULE 4: Every complaint appears exactly once in Table 7.
RULE 5: Harm subtotals must equal sum of child MDPs.
```

### docx-js Rendering

```javascript
// Harm header row
function harmRow(text, count, rate, ract) {
  return new TableRow({children: [
    new TableCell({
      borders: CB, width: {size: 4400, type: WidthType.DXA},
      shading: {fill: 'F2F2F2', type: ShadingType.CLEAR},
      margins: CM,
      children: [new Paragraph({children: [
        new TextRun({text, bold: true, font: 'Arial', size: 20})
      ]})]
    }),
    mc(`${rate} (${count})`, {w: 2680, al: AlignmentType.CENTER, b: true, f: 'F2F2F2'}),
    mc(ract, {w: 2280, al: AlignmentType.CENTER, b: true, f: 'F2F2F2'})
  ]});
}

// MDP row (indented under Harm)
function mdpRow(text, count, rate, ract) {
  return new TableRow({children: [
    new TableCell({
      borders: CB, width: {size: 4400, type: WidthType.DXA},
      margins: CM,
      children: [new Paragraph({
        indent: {left: 360},
        children: [new TextRun({text, font: 'Arial', size: 20})]
      })]
    }),
    mc(`${rate} (${count})`, {w: 2680, al: AlignmentType.CENTER}),
    mc(ract, {w: 2280, al: AlignmentType.CENTER})
  ]});
}

// Grand Total
function grandTotal(count, rate) {
  return new TableRow({children: [
    mc('Grand Total', {w: 4400, f: 'D9D9D9', b: true}),
    mc(`${rate} (${count})`, {w: 2680, f: 'D9D9D9', b: true, al: AlignmentType.CENTER}),
    mc('—', {w: 2280, f: 'D9D9D9', b: true, al: AlignmentType.CENTER})
  ]});
}
```

### Failure Modes

- ✗ "Unknown / Not yet determined" as Harm — classify every complaint
- ✗ Parent IMDRF codes as MDP — use leaf-node terms only
- ✗ Rate as decimal 0.000897 — show as percentage 0.0897%
- ✗ Max Expected Rate blank — show RACT value or "N/A — RACT not provided"
- ✗ Both annual AND biennial Table 7 — delete unused variant
- ✗ Harm rows not bold/gray — apply formatting
- ✗ MDP rows not indented — use `indent: {left: 360}`
- ✗ Grand Total ≠ sum of MDPs — verify arithmetic
- ✗ "Breach of device sterility" when complaint was "foreign material" — read actual complaint narrative

---

## Table 8: FSCA Initiated in Current Reporting Period

### Columns (8 total, sum = 9,360 DXA)

| # | Header | Width |
|---|--------|-------|
| 1 | Type of action | 1,000 |
| 2 | Manufacturer Reference number | 1,100 |
| 3 | Issuing Date / Date of Final FSN | 1,100 |
| 4 | Scope of the FSCA / Device models | 1,200 |
| 5 | Status of the FSCA | 900 |
| 6 | Rationale and description | 1,600 |
| 7 | Impacted regions | 1,000 |
| 8 | Date reported to MHRA | 1,460 |

### No-FSCA Handling

If no FSCA during period, replace the entire table with a single merged-cell row:
```
"N/A — There were no FSCAs initiated, ongoing, or closed during 
the data collection period for [Device Name]."
```
Do NOT render an empty 8-column table with blank cells.

---

## Table 9: CAPA Initiated in Current Reporting Period

### Columns (8 total, sum = 9,360 DXA)

| # | Header | Width |
|---|--------|-------|
| 1 | CAPA Number / Manufacturer Reference | 1,100 |
| 2 | Initiation Date | 900 |
| 3 | Scope of the CAPA | 1,200 |
| 4 | Status of the CAPA | 900 |
| 5 | CAPA description | 1,660 |
| 6 | Root cause | 1,500 |
| 7 | Effectiveness of the CAPA | 1,100 |
| 8 | Target date for completion | 1,000 |

### CAPA Status Logic

```
Sources (check in order):
  1. complaints_csv → CAPA Number column (non-empty values)
  2. previous_psur_data.json → SectionK_CAPA.NewCAPAs
  3. capa_records file (if provided)

Status determination:
  - CAPA opened in PREVIOUS period + no closure documentation → "Open — effectiveness verification underway"
  - CAPA opened in CURRENT period → "Open" (default until evidence of closure)
  - NEVER auto-mark as "Completed" without explicit closure documentation
```

### No-CAPA Handling
Same pattern as Table 8 — single merged-cell N/A row, not an empty table.

---

## Table 10: Adverse Events and Recalls (External Databases)

### Columns (6 total, sum = 9,360 DXA)

| # | Header | Width |
|---|--------|-------|
| 1 | Database/Registry | 1,700 |
| 2 | Total matches | 1,100 |
| 3 | Relevant findings | 2,600 |
| 4 | Benchmark vs similar devices | 1,900 |
| 5 | Regulatory actions affecting similar devices | 1,200 |
| 6 | RMF update reference | 860 |

### Mandatory Rows (6, all required)

```
Row 1: U.S. FDA MAUDE (product code [XXX])
Row 2: U.S. FDA Recall Database
Row 3: UK MHRA Yellow Card
Row 4: Australia TGA DAEN
Row 5: Health Canada Medical Device Incident Reports
Row 6: EUDAMED
```

### EUDAMED Special Handling
Total matches = `Limited public access` (not "0")
Findings = `Vigilance module partially available; no signal identified.`

### Data Source
Read from `external_db_search_results.json`. For each database, extract matches and findings. If no matches: `No adverse events identified for [Device Name] during the reporting period.`

---

## Table 11: PMCF Activities

### Columns (5 total, sum = 9,360 DXA)

| # | Header | Width |
|---|--------|-------|
| 1 | Specific PMCF Activities | 2,000 |
| 2 | Key Findings | 2,200 |
| 3 | Impact on safety/performance | 1,800 |
| 4 | RMF/CER update? | 1,500 |
| 5 | PMCF Evaluation Report reference | 1,860 |

### PMCF Status Handling

```
IF PMCF ongoing:
  Activity = description of study/data collection
  Key Findings = interim results or "Data collection continuing; no signal."
  Impact = "No adverse impact on safety/performance profile."
  RMF/CER = "No update required during reporting period."
  Report ref = "[PMCF number] (interim)"

IF PMCF not required:
  Replace table with: "N/A — Not required per PMS Plan [number]."

IF PMCF results unavailable:
  Use "Data collection continuing per protocol" language.
  NEVER fabricate PMCF findings.
```

---

## UDI-DI Table (Section B, unnumbered)

### Columns (4 total, sum = 9,360 DXA)

| # | Header | Width |
|---|--------|-------|
| 1 | Basic UDI-DI | 2,200 |
| 2 | Device Trade Name | 2,400 |
| 3 | EMDN Code | 1,760 |
| 4 | Changes from Previous PSUR | 3,000 |

### UDI-DI Verification

```
Source priority (highest trust first):
  1. previous_psur_data.json → RegulatoryInformation.Basic_UDI_DI
  2. CER document
  3. device_context.json (auto-generated — may have errors)

If sources conflict: use previous_psur value, flag conflict.
```

---

## Associated Documents Table (Section B, unnumbered)

### Columns (3 total, sum = 9,360 DXA)

| # | Header | Width |
|---|--------|-------|
| 1 | Document Type | 2,400 |
| 2 | Document Number | 2,400 |
| 3 | Document Title | 4,560 |

### Mandatory Rows

```
PMS Plan
Clinical Evaluation Report
PMCF Plan (or "N/A" if not applicable)
Risk Management File
Technical Documentation
Instructions for Use
Previous PSUR (if not first PSUR)
```

Never leave Document Number or Title as `[Number]` or `[Title]`. Use actual values or `[TO BE COMPLETED]`.
