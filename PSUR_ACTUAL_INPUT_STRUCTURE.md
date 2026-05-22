# PSUR Generator — Actual Input Structure

**What the user drops in. What the system must parse.**

---

## Input Bundle

Every PSUR generation starts with a set of files the user provides. These come from two sources: CooperSurgical's internal systems (CSI Complaints Database, D365 ERP) and existing regulatory documents (CER, previous PSUR, RACT). The file naming convention follows a device index prefix: `{NNN}_{type}.{ext}`.

```
input/
├── {NNN}_complaints.csv          # CSI Complaints Database export
├── {NNN}_sales.xlsx              # D365 ERP sales export
├── {NNN}_cer.docx                # Current Clinical Evaluation Report
├── {NNN}_ract.xlsx               # Risk Acceptability Criteria Table
├── {NNN}_previous_psur.docx      # Most recent approved PSUR (if exists)
└── {NNN}_tech_doc.docx           # Technical documentation (optional, ad hoc)
```

The `{NNN}` prefix is a zero-padded device index (e.g., `008`, `018`, `134`, `147`) that groups all files for one PSUR together. The system uses this prefix to associate files to a single generation run.

Along with the files, the user provides two parameters verbally or via CLI:

- **Surveillance period:** e.g., "06/01/2023 through 05/31/2025"
- **Device name:** e.g., "Endosee® Hysteroscope"

Everything else is extracted from the files.

---

## 1. Complaints — `{NNN}_complaints.csv`

**Source:** CSI Complaints Database export  
**Format:** CSV (occasionally `.xlsx` with a sheet named "CSI Complaints")

### Actual Column Headers (as exported from CSI)

| Column Header | Type | Always Present | What It Contains |
|---------------|------|----------------|------------------|
| `Complaint Number` | String | Yes | Unique complaint ID (e.g., "2024-00345") |
| `Date Entered` | Date | Yes | Date complaint was received into CSI |
| `Date Closed` | Date | No | Date investigation was completed |
| `Product Number` | String | Yes | Catalog/item number of the device |
| `Lot Number` | String | No | Manufacturing lot or batch number |
| `Description` | String | Yes | Free-text complaint narrative |
| `Complaint Type` | String | Yes | CSI complaint classification category |
| `Symptom Code` | String | Yes | CSI symptom classification (maps to IMDRF device problem) |
| `Fault Code` | String | No | Root cause / fault classification |
| `Failure Code` | String | No | Failure mode classification |
| `MDR Number` | String | No | FDA MDR number if event was reportable |
| `Complaint Confirmed` | String | No | "Yes" / "No" — whether investigation confirmed the complaint |
| `Investigation Findings` | String | No | Summary of investigation outcome |
| `Corrective Actions` | String | No | Actions taken in response |
| `Country` | String | Yes | Country where the event occurred |
| `CAPA Number` | String | No | Linked CAPA reference if one was initiated |

### What the System Must Do With This

The raw CSI export does not contain IMDRF-coded fields. The system must:

1. **Map `Symptom Code` → IMDRF Annex A device problem terms.** This mapping is device-family-specific and maintained in a configuration layer (or performed via LLM classification with validation).

2. **Map `Complaint Type` → IMDRF harm categories** (Annex E/F health impact terms, or "No Health Consequence or Impact"). Same approach — config mapping or LLM classification.

3. **Determine reportability.** The presence of a non-empty `MDR Number` indicates the event was reported to FDA. For EU/UK reportability, the system must infer from complaint type, harm category, and any flags in the narrative.

4. **Count correctly.** `total_complaints` = count of unique `Complaint Number` values. If the system expands one complaint into multiple IMDRF rows (because one complaint carries multiple symptom codes), it must track `total_imdrf_occurrences` separately.

5. **Filter by surveillance period.** Use `Date Entered` against the user-provided date range.

---

## 2. Sales — `{NNN}_sales.xlsx`

**Source:** D365 ERP export  
**Format:** Excel (`.xlsx`), sometimes with metadata rows above the actual data header

### Actual Column Headers (as exported from D365)

The D365 export is not always clean. The actual data header row may not be row 1 — there are sometimes title rows, blank rows, or filter summary rows above the data. The system must detect the header row by scanning for recognizable column names.

| Column Header (common variants) | Type | Always Present | What It Contains |
|----------------------------------|------|----------------|------------------|
| `ItemNumber` or `Item Number` or `Product Number` | String | Yes | Catalog/SKU number |
| `Ship Date` or `Invoice Date` or `Date` | Date | Yes | Transaction date |
| `Quantity` or `Qty` or `Quantity Shipped` or `Units` | Integer | Yes | Units in transaction |
| `Ship To Country` or `Country` or `Region` | String | Yes | Destination country |
| `Customer Name` or `Customer` | String | No | Customer identifier |
| `Description` or `Product Name` or `Product Description` | String | No | Human-readable product name |

### Structural Quirks the Parser Must Handle

- **Header row detection.** Scan rows 0–10 looking for a row containing at least two of: `ItemNumber`, `Quantity`, `Country`, `Date`. That row is the header.
- **Multi-sheet workbooks.** Some exports place sales data on a sheet named "Sales", "Data", "Sheet1", or use the product name. Use `pd.ExcelFile().sheet_names` and examine each sheet for sales-like column structures.
- **Mixed product numbers.** The export may contain sales for multiple catalog numbers. The system must filter to only the catalog numbers relevant to the device under evaluation (cross-referenced against product numbers in the complaints file or provided in user parameters).
- **Units-per-sales-unit.** Some catalog numbers represent multi-packs (e.g., a box of 10). If the PSUR requires individual device counts, a multiplier must be applied. This is device-specific and must be configured per product family.

---

## 3. Clinical Evaluation Report — `{NNN}_cer.docx`

**Source:** CooperSurgical QMS (SharePoint)  
**Format:** Word document (`.docx`)

### What the System Extracts

The CER is a structured narrative document. The system parses it via `python-docx` or `pandoc` text extraction and identifies the following by section heading patterns:

| Information Needed | Typical CER Section | Used In PSUR Section |
|--------------------|---------------------|----------------------|
| Device description | "Device Description", "Description of the Device" | B |
| Intended purpose / intended use | "Intended Purpose", "Intended Use" | B |
| Indications for use | "Indications" | B |
| Contraindications | "Contraindications" | B |
| Clinical benefits claimed | "Clinical Benefits", "Benefit-Risk" | M |
| Known residual risks | "Residual Risk", "Risk Summary" | M |
| State of the art | "State of the Art", "Current Knowledge" | J, M |
| Equivalent devices (if applicable) | "Equivalence Assessment" | J, K |
| PMCF conclusions (if included) | "PMCF", "Post-Market Clinical Follow-Up" | L |

### Parsing Strategy

The system does not need to parse the entire CER. It needs targeted extraction of the sections above. Strategy:

1. Extract full text via `pandoc` or `python-docx`.
2. Identify section boundaries by heading styles or heading-like text patterns.
3. Extract relevant sections as text blocks.
4. Feed extracted blocks to the appropriate section agents as context.

If the CER cannot be parsed (corrupted, scanned PDF, etc.), the system flags this and proceeds with `[CER DATA NOT AVAILABLE — manual entry required]` placeholders in Sections B and M.

---

## 4. Risk Acceptability Criteria Table — `{NNN}_ract.xlsx`

**Source:** Risk Management File (SharePoint)  
**Format:** Excel (`.xlsx`), typically with a sheet named "RACT"

### Actual Structure (as observed across devices)

The RACT is an Excel workbook. The primary sheet is usually named "RACT" and has a header row (sometimes at row 2, not row 1). The column structure varies slightly by device family but follows this general pattern:

| Column Header (common variants) | Type | Always Present | What It Contains |
|----------------------------------|------|----------------|------------------|
| `Primary ID` or `ID` | String | Yes | Hazard sequence identifier |
| `Secondary ID` | String | No | Sub-identifier |
| `Topic` or `Category` | String | No | Hazard topic grouping |
| `Hazard` | String | Yes | Identified hazard |
| `Hazardous Situation` | String | Yes | Exposure scenario |
| `Party Exposed` | String | No | Patient, user, etc. |
| `Harm` | String | Yes | Harm description — **this is the key field for PSUR mapping** |
| `Root Cause` or `Cause` | String | No | Cause of hazardous situation |
| `Initial Severity` | String/Int | Yes | Pre-mitigation severity score |
| `Initial Occurrence` or `Initial Probability` | String/Int | Yes | Pre-mitigation occurrence score |
| `Initial Risk Level` | String | Yes | Pre-mitigation risk (e.g., "High", "Medium", "Low") |
| `Standard RCMs` or `Risk Control Measures` | String | Yes | Standard risk control measures |
| `Additional RCMs` | String | No | Additional risk controls |
| `Evidence of Implementation` | String | No | Implementation evidence reference |
| `Evidence of Effectiveness` | String | No | Effectiveness evidence reference |
| `Final Severity` or `Residual Severity` | String/Int | Yes | Post-mitigation severity |
| `Final Occurrence` or `Residual Occurrence` | String/Int | Yes | Post-mitigation occurrence |
| `Final Risk Level` or `Residual Risk Level` | String | Yes | Post-mitigation risk |
| `Risk Acceptability` | String | Yes | "Acceptable" / "ALARP" / etc. |

### Critical Processing Note

The RACT as stored in the RMF does **not** contain a "max expected complaint rate" column in percentage format. The maximum expected rate of occurrence used in PSUR Table 7 is **derived** from the RACT's occurrence scores, not read directly as a percentage. The system must either:

- **Option A:** Apply the company's occurrence-to-rate conversion table (probability score → expected complaint rate percentage). This mapping is defined in the Risk Management procedure (BSR-ENG-007) and is typically a logarithmic scale.

- **Option B:** Accept a separate rate lookup that has already been pre-calculated and provided as an additional column or separate reference. Some device families have a standalone rate table appended to the RACT.

The system must **never** fabricate or estimate RACT rate values. If the conversion cannot be performed, the Table 7 "Max Expected Rate" column must contain `[RACT RATE REQUIRED]` placeholders.

---

## 5. Previous PSUR — `{NNN}_previous_psur.docx`

**Source:** CooperSurgical QMS (SharePoint)  
**Format:** Word document (`.docx`), following FormQAR-054 structure

### What the System Extracts

| Information Needed | Where in Previous PSUR | Used In Current PSUR Section |
|--------------------|------------------------|------------------------------|
| Previous surveillance period dates | Cover page or Section B | B (continuity validation) |
| Actions from previous PSUR | Section A (Executive Summary) | A |
| Status of previous actions | Section A | A |
| NB review status of previous PSUR | Section A | A |
| Previous complaint rates | Section F (Tables 6/7) | F (trend context, preceding period columns) |
| Previous benefit-risk conclusion | Section M | M (continuity) |
| Device description (carry-forward) | Section B | B (stable content reuse) |
| Intended purpose (carry-forward) | Section B | B (stable content reuse) |
| Classification details | Section B | B (carry-forward if unchanged) |
| Certificate number, UDI-DIs | Section B | Cover page, B |
| Associated document numbers | Section B | B |
| Previous CAPA status | Section I | I (carry-forward of open CAPAs) |
| Previous FSCA status | Section H | H (carry-forward of open FSCAs) |

### Parsing Strategy

The previous PSUR is the richest source of carry-forward content. The system should:

1. Extract full text via `pandoc` or `python-docx`.
2. Identify FormQAR-054 section boundaries (Section A, Section B, etc.) by heading patterns.
3. Extract each section as a text block.
4. Parse structured fields (dates, document numbers, classification) from Section B.
5. Parse tabular data from Section F for preceding-period complaint rate columns.

The previous PSUR provides essentially all of the `device_config.json` fields from the earlier specification — the system should reconstruct device identity, classification, certification milestones, UDI-DIs, associated documents, and manufacturer information from this document rather than requiring a separate config file.

---

## 6. Technical Documentation — `{NNN}_tech_doc.docx` (Optional)

**Source:** Ad hoc — may be IFU, product specifications, design history, or other supporting docs  
**Format:** Word document (`.docx`) or PDF

This is not a standard input. When provided, it supplements the CER with additional device-specific information that may be needed for Section B device description or Section M conclusions. The system should extract full text and make it available as supplementary context to relevant section agents.

---

## Summary: What the System Must Accept

| File | Required? | Format | Source System |
|------|-----------|--------|---------------|
| `{NNN}_complaints.csv` | **Yes** | CSV or XLSX | CSI Complaints Database |
| `{NNN}_sales.xlsx` | **Yes** | XLSX | D365 ERP |
| `{NNN}_cer.docx` | **Yes** | DOCX | SharePoint / QMS |
| `{NNN}_ract.xlsx` | **Yes** | XLSX | Risk Management File |
| `{NNN}_previous_psur.docx` | Yes (unless first PSUR) | DOCX | SharePoint / QMS |
| `{NNN}_tech_doc.docx` | No | DOCX or PDF | Ad hoc |

**User-provided parameters (not files):**

| Parameter | Example | Required |
|-----------|---------|----------|
| Surveillance period | "06/01/2023 through 05/31/2025" | **Yes** |
| Device name | "Endosee® Hysteroscope" | **Yes** |

Everything else — device classification, UDI-DIs, certification dates, manufacturer info, associated document numbers, intended purpose, contraindications, target populations, NB details — is extracted from the previous PSUR and CER. No separate configuration file is required from the user.
