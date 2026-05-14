---
name: psur-validate
description: Validate all PSUR tables against FormQAR-054 requirements. Run after all tables are built to catch template debris, arithmetic errors, classification failures, missing data, and formatting issues before the document is finalized. Reports pass/fail for each check.
when_to_use: Trigger after building PSUR tables, before finalizing the PSUR document, when checking PSUR quality, or when the user asks to validate the PSUR.
disable-model-invocation: true
allowed-tools: Bash(python3 *) Read Grep
---

# PSUR Table Validation Checklist

Run every check below against the generated PSUR document. Report each as PASS or FAIL with details. If ANY check fails, the document is NOT ready for submission.

## Template Debris Checks

```
□ T1: No square-bracketed template instructions remain
      Search for: /\[.*?\]/ (excluding checkbox ☐/☑/☒ characters)
      FAIL examples: "[Use this table if...]", "[Add or delete rows as needed]",
                     "[Note: Multiply number of sales units...]"

□ T2: No "(Remove if not applicable)" text remains
      Search for: "(Remove if not applicable)"

□ T3: No "See Technical Documentation" / "See IFU" placeholders
      Search for: "See Technical Documentation", "See IFU", "See TD"

□ T4: Only ONE sales table variant present
      Count occurrences of "Annual Number of Devices" table headers.
      PASS = exactly 1. FAIL = 0 or 2+.

□ T5: Only ONE complaint rate Table 7 variant present
      Count occurrences of "Complaint Rate (and Complaint Count)" table headers.
      PASS = exactly 1. FAIL = 0 or 2+.

□ T6: No "[Any other countries which have more than 5%...]" placeholder row
      Search for: "Any other countries", "Add rows as needed"
```

## Table 1 (Sales) Checks

```
□ S1: Preceding period column is populated (not all dashes or blanks)
      Read the Table 1 preceding period column. At least one region must 
      have a numeric value. If all show "—" or blank: FAIL.

□ S2: Worldwide row = sum of all region rows (current period)
      Extract all region values and worldwide value. Verify arithmetic.

□ S3: Percentages sum to 100.0%
      Extract all percentage values. Sum must be between 99.9% and 100.1%.

□ S4: Date range format correct
      Column headers must use mmm-yyyy format (e.g., "May-2025 to Apr-2026").
      FAIL if showing ISO dates (2025-05-01) or raw date strings.
```

## Tables 2-4 (Serious Incidents) Checks

```
□ D1: Tables contain EU/UK serious incidents ONLY
      If any complaint listed in Tables 2-4 does not meet EU MDR Art. 2(65)
      criteria: FAIL. FDA MDRs that are not EU serious incidents must NOT 
      appear in these tables.

□ D2: All cells populated (not blank)
      Every cell must contain a value: "0", "0.0000%", "N/A", or actual data.

□ D3: Rate column shows percentage format
      Must show "0.0000%" not "0" for zero-event rates.
```

## Table 7 (Complaint Rates) Checks — MOST CRITICAL

```
□ C1: No "Unknown / Not yet determined" Harm category
      Search Table 7 column 1 for "Unknown", "Not yet determined", 
      "not yet classified". Any match: FAIL.

□ C2: No parent-level IMDRF codes as MDPs
      Search for: "Device issues, consequence or impact to patient or user",
      "Device issues, outcome or consequence", "Unknown device problem".
      Any match: FAIL.

□ C3: Harm rows are BOLD with gray fill (#F2F2F2)
      Verify formatting of Harm header rows.

□ C4: MDP rows are indented under correct Harm parent
      Verify left indent (360 DXA) on MDP rows.

□ C5: Max Expected Rate column has values
      Every MDP row must show either a RACT threshold ("≤0.1% (O2)")
      or "N/A — RACT not provided". Never blank, never "—" alone.

□ C6: Grand Total count = sum of all individual MDP counts
      Extract all MDP counts and Grand Total. Verify arithmetic.

□ C7: Each Harm subtotal = sum of its child MDP counts
      For each Harm header row, verify count equals sum of indented MDPs below it.

□ C8: All rates formatted as percentages (X.XXXX%)
      Search for decimal-format rates (0.000XXX without %). Any match: FAIL.

□ C9: Rate calculation is correct
      For each MDP: verify rate = (count / total_sales) × 100 to 4 decimal places.

□ C10: "No Health Consequence or Impact" category present
       Table 7 must contain this Harm category for all non-injury complaints.
```

## Table 8 (FSCA) Check

```
□ F1: If no FSCA, table replaced with N/A statement
      Must show "N/A — There were no FSCAs..." not an empty 8-column table.
```

## Table 9 (CAPA) Check

```
□ P1: CAPA status reflects evidence
      If CAPA from previous period, status should be "Open" or "In Progress"
      unless explicit closure documentation was provided. FAIL if auto-marked 
      as "Completed" without evidence.
```

## Table 10 (External Databases) Check

```
□ E1: All 6 mandatory database rows present
      Must include: FDA MAUDE, FDA Recall, MHRA Yellow Card, TGA DAEN, 
      Health Canada, EUDAMED.

□ E2: EUDAMED shows "Limited public access" (not "0")
```

## Table 11 (PMCF) Check

```
□ M1: If no PMCF, replaced with N/A statement
      Not an empty table with blank cells.
```

## Cross-Table Consistency Checks

```
□ X1: Total complaints in Table 7 Grand Total matches complaint count 
      stated in Section A Executive Summary and Section F narrative.

□ X2: Total sales in Table 1 Worldwide matches denominator used in 
      Table 7 rate calculations.

□ X3: Benefit-risk conclusion in Section A matches Section M(a).

□ X4: Number of serious incidents in Tables 2-4 matches Section D narrative.
```

## Formatting Checks

```
□ R1: All table column widths sum to 9,360 DXA
□ R2: All tables use WidthType.DXA (not WidthType.PERCENTAGE)
□ R3: No bullet points in document body
□ R4: No regulation/standard citations in document body
□ R5: Notified Body number is correct (2797 for EU MDR BSI, not 0086)
□ R6: UDI-DI matches previous_psur_data.json value
```

## Validation Report Format

```
PSUR TABLE VALIDATION REPORT
=============================
Device: [name]
Reporting Period: [dates]
Validated: [timestamp]

RESULTS:
  Template Debris: [X/6 passed]
    T1: PASS/FAIL — [details]
    T2: PASS/FAIL — [details]
    ...
  
  Table 1 (Sales): [X/4 passed]
    S1: PASS/FAIL — [details]
    ...

  Tables 2-4 (Serious Incidents): [X/3 passed]
    D1: PASS/FAIL — [details]
    ...

  Table 7 (Complaint Rates): [X/10 passed]
    C1: PASS/FAIL — [details]
    ...

  Other Tables: [X/4 passed]
    ...

  Cross-Table: [X/4 passed]
    ...

  Formatting: [X/6 passed]
    ...

OVERALL: [PASS — all checks passed] or [FAIL — N checks failed]
```

If OVERALL = FAIL, list every failing check with its details and the specific remediation needed.
