---
name: psur-tables
description: Build and populate all FormQAR-054 PSUR tables from source data. Use when generating a PSUR, filling in PSUR tables, building Table 7 complaint rates, building Table 1 sales data, or constructing any table in a Periodic Safety Update Report. Handles Table 1 (sales), Tables 2-4 (serious incidents), Table 6 (feedback), Table 7 (complaint rates by harm/MDP), Table 8 (FSCA), Table 9 (CAPA), Table 10 (external databases), and Table 11 (PMCF).
when_to_use: Trigger when user mentions PSUR tables, FormQAR-054, complaint rate table, sales table, serious incident table, FSCA table, CAPA table, external database table, or PMCF table. Also trigger when user asks to populate, fill in, or generate any table in a PSUR document.
allowed-tools: Bash(python3 *) Read Write Edit Grep
---

# PSUR Table Construction — FormQAR-054

You are building tables for a CooperSurgical Periodic Safety Update Report. Every table must match the exact schema defined in FormQAR-054. Read the supporting files before generating any table:

- For column schemas, cell population logic, and rendering code: see [table-schemas.md](table-schemas.md)
- For IMDRF complaint classification (required before Table 7): invoke `/psur-imdrf-classify`
- For sales data aggregation (required before Table 1): invoke `/psur-sales-aggregate`
- For post-generation validation: invoke `/psur-validate`

## Global Rules — Apply to Every Table

**SELECT ONE VARIANT ONLY.** FormQAR-054 provides annual AND biennial variants for Table 1 and Table 7. Determine the device cadence first (Class III / Class IIb = annual 12-month; Class IIa = biennial 24-month), render ONLY the matching variant, and DELETE the other from the document entirely.

**STRIP ALL TEMPLATE DEBRIS.** Before rendering any table, remove:
- All `[bracketed instructions]` — regex: `/\[.*?\]/g` (excluding checkbox ☐/☑ characters)
- All `(Remove if not applicable)` annotations
- All `[Add or delete rows as needed]` footers
- All `[Note: Multiply number of sales units...]` footnotes
- All `[Any other countries which have more than 5% of global sales. Add rows as needed.]` placeholder rows (replace with actual country name if one qualifies, otherwise delete the row)

**FILL EVERY CELL.** No table cell may be empty in the final document:
- Data exists → populate with value
- Data is zero → write `0` (integers) or `0.0000%` (rates)
- Not applicable → write `N/A`
- Data unavailable → write `[TO BE COMPLETED]`
- Never leave a cell blank or use a bare dash `—` without context

**NUMERIC FORMATTING:**
- Integers ≥ 1,000: comma-separated (64,664)
- Complaint rates in tables: percentage, 4 decimal places (0.0897%)
- Sales percentages: 1 decimal place (77.0%)
- Date ranges in column headers: `mmm-yyyy` format (May-2025 to Apr-2026)

**DOCX RENDERING:**
- Font: Arial, 10pt (20 half-points) in table cells
- Header row: bold, background fill `#D9D9D9`
- Borders: single, 0.5pt, `#808080` all sides
- Cell padding: 80 DXA top/bottom, 120 DXA left/right
- Column widths: always use `WidthType.DXA`, never `WidthType.PERCENTAGE`
- Full table width: 9,360 DXA (US Letter with 0.75" margins)
- Worldwide / Grand Total rows: bold, fill `#D9D9D9`

**ARITHMETIC VERIFICATION — run after populating every table:**
- Table 1: Worldwide row = sum of all region rows. Percentages sum to 100.0%.
- Table 7: Grand Total count = sum of all individual MDP counts. Each Harm subtotal = sum of its child MDP counts. Rate = count / total_sales × 100.

## Table-by-Table Instructions

Read [table-schemas.md](table-schemas.md) for the detailed column schema, population logic, data sources, and docx-js rendering pattern for each table. The schemas file is the authoritative reference — do not deviate from it.

## Execution Order

Tables must be populated in this order because later tables depend on earlier computations:

```
1. Determine cadence (annual vs biennial) → selects Table 1 vs Table 2, Table 7 variant
2. Run /psur-sales-aggregate → populates Table 1 data + denominator for rates
3. Run /psur-imdrf-classify → classifies complaints into Harm/MDP hierarchy
4. Populate Table 1 (sales by region)
5. Populate Tables 2-4 (serious incidents — EU/UK ONLY)
6. Populate Table 6 (customer feedback)
7. Populate Table 7 (complaint rates by Harm and MDP)
8. Populate Table 8 (FSCA)
9. Populate Table 9 (CAPA)
10. Populate Table 10 (external databases)
11. Populate Table 11 (PMCF)
12. Run /psur-validate → verify all tables pass checklist
```
