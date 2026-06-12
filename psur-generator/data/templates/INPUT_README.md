# PSUR Input Templates Guide

This folder contains optimized input templates for the PSUR Generator pipeline. Each template uses **exact column names and data formats** that the parsers recognize natively, ensuring maximum confidence mapping without AI fallback overhead.

---

## Quick Start

This `templates/` folder holds **blank scaffolds only**. Real input files belong in the parent `data/input/` directory (the auto-discovery scanner only looks there, not in subfolders).

1. Copy the template files you need from `data/input/templates/` into `data/input/`
2. Rename them using the naming conventions below (for auto-discovery)
3. Replace sample data with your actual device data
4. Run: `python main.py generate --start 2025-01-01 --end 2025-12-31`

---

## File Naming for Auto-Discovery

The pipeline auto-discovers input files by keyword matching on filenames. Use these naming patterns for instant classification:

| Input Type | Filename Keywords | Example |
|---|---|---|
| Sales | `sales`, `distribution`, `units_sold` | `sales_data_2025.csv` |
| Complaints | `complaints`, `complaint_log` | `complaints_2025.csv` |
| CAPA | `capa`, `corrective_action` | `capa_log_2025.csv` |
| Device Context | `device_context` | `device_context.json` |
| RACT | `ract`, `risk_acceptance`, `risk_table` | `ract_2025.csv` |
| Previous PSUR | `previous_psur`, `prior_psur` | `previous_psur_data.json` |
| PMS Plan | `pms_plan`, `pms_procedure` | `pms_plan_data.json` |
| CER | `cer`, `clinical_evaluation` | `cer_report.pdf` |
| IFU | `ifu`, `instructions_for_use` | `ifu_document.pdf` |
| Risk Management | `rmf`, `risk_management` | `rmf_document.pdf` |
| PMCF | `pmcf`, `post_market_clinical` | `pmcf_plan.pdf` |
| FSCA | `fsca`, `field_safety` | `fsca_records.csv` |
| External DB | `external_db`, `maude`, `bfarm`, `external_event` | `external_db_search.pdf` |
| Literature | `literature`, `lit_search` | `literature.csv` |
| Clinical Safety/Performance | `clinical_safety`, `clinical_performance` | `clinical_safety.json` |
| Analysis Workbook | `analysis_workbook`, `analysis` | `analysis_workbook.xlsx` |

---

## Template Details

### 1. `sales_data_template.csv`

**Purpose:** Monthly unit sales by region, country, and product for complaint rate denominators and Table 1.

| Column | Required | Format | Description |
|---|---|---|---|
| `date` | Yes | `YYYY-MM-DD` | Sale date (day-level). Parser groups by month automatically |
| `year` | Optional | `YYYY` | Year (alternative to date column) |
| `month` | Optional | `1-12` or `Jan`/`January` | Month (used with year if no date column) |
| `quantity` | Yes | Integer | Units sold in this row |
| `region` | Yes | Text | Geographic region (e.g., `EMEA`, `North America`, `Asia Pacific`) |
| `country` | Yes | ISO or name | Country code or name. Used for EEA calculation |
| `product` | Optional | Text | Product line or catalog number for per-product breakdown |

**Tips:**
- Include **all regions** for accurate EEA vs. non-EEA splitting
- One row per transaction/shipment gives best granularity
- The `country` field drives EEA country counting — use consistent names

---

### 2. `complaints_data_template.csv`

**Purpose:** Individual complaint records for trending, IMDRF cross-tabs, and sections D-G.

| Column | Required | Format | Description |
|---|---|---|---|
| `date` | Yes | `YYYY-MM-DD` | Complaint received date |
| `complaint_number` | Yes | Text | Unique complaint identifier (e.g., `COMP-2025-0001`) |
| `description` | Yes | Text | Narrative description of the complaint event |
| `imdrf_code` | Recommended | IMDRF Annex A text | Device problem term (e.g., `Device breakage or deterioration`). If blank, auto-coded by AI |
| `harm` | Recommended | IMDRF Annex F text | Health impact (e.g., `No Harm`, `Minor Injury`, `Serious Injury`, `Death`) |
| `serious` | Yes | `Yes` / `No` | Whether this is a serious/reportable incident. Accepts: YES, TRUE, 1, Y, SERIOUS, REPORTABLE |
| `region` | Yes | Text | Region where complaint originated |

**Tips:**
- Pre-coding `imdrf_code` and `harm` saves API calls and improves accuracy
- Use consistent IMDRF Annex A terms for device problems
- Use consistent IMDRF Annex F terms for health impacts
- `serious` = YES triggers inclusion in Section D (Serious Incidents)
- `description` should be detailed enough for the LLM agents to assess root cause patterns

---

### 3. `capa_data_template.csv`

**Purpose:** CAPA records for Section I analysis and prior-action tracking.

| Column | Required | Format | Description |
|---|---|---|---|
| `capa_number` | Yes | Text | Unique CAPA identifier (e.g., `CAPA-2025-001`) |
| `title` | Yes | Text | Descriptive title of the CAPA |
| `status` | Yes | Text | `Open`, `Closed`, `In Progress` |
| `open_date` | Yes | `YYYY-MM-DD` | Date CAPA was initiated |
| `close_date` | If closed | `YYYY-MM-DD` | Date CAPA was closed (blank if still open) |
| `root_cause` | Recommended | Text | Root cause determination narrative |
| `type` | Recommended | Text | `Corrective` or `Preventive` |

**Tips:**
- The parser filters CAPAs by period overlap: `open_date ≤ period_end AND (close_date ≥ period_start OR close_date is blank)`
- Include CAPAs from previous periods that remained open — they'll be captured automatically
- Detailed `root_cause` narratives improve Section I agent output quality

---

### 4. `device_context_template.json`

**Purpose:** Complete device metadata that drives Section B (Scope/Device Description) and contextualizes all other sections.

**This is the single most important input file.** It determines device classification, regulatory strategy, document references, and the depth of contextual information available to every section agent.

| Field | Required | Type | Notes |
|---|---|---|---|
| `device_trade_names` | Yes | Array of strings | First entry used as primary device name |
| `device_description` | Yes | String | Detailed technical description |
| `intended_purpose` | Yes | String | Exact intended purpose from DoC/TD |
| `indications_for_use` | Yes | Array of strings | Each specific clinical indication |
| `contraindications` | Recommended | Array of strings | Use `["None stated in the CER"]` if none |
| `target_patient_population` | Yes | String | Patient demographics and conditions |
| `intended_user_profile` | Yes | String | Required training/qualifications |
| `basic_udi_di_or_device_family_name` | Yes | String | UDI-DI or GMN |
| `model_or_catalog_numbers` | Yes | Array of strings | All model/catalog numbers |
| `eu_mdr_classification_and_rule` | Yes | String | e.g., `EU Class IIb under EU MDR 2017/745, Rule 12` |
| `uk_mdr_classification_and_rule` | Conditional | String | UK MDR class if device is on the GB market; `Not applicable` otherwise |
| `uk_responsible_person` | Conditional | String | UK Responsible Person name/address if on GB market; `Not applicable` otherwise |
| `ukca_marking_status` | Conditional | String | UKCA marking status or transitional provision status |
| `emdn_code` | Recommended | String | European Medical Device Nomenclature code |
| `gmdn_code` | Recommended | String | Global Medical Device Nomenclature code |
| `date_of_first_ce_marking_or_doc` | Recommended | String | First CE marking or DoC date |
| `notified_body_name_and_id` | Yes | String | NB name + 4-digit number (e.g., `BSI (0086)`) |
| `cer_document_number_and_version` | Recommended | String | CER document reference |
| `cer_date_or_last_update` | Recommended | String | Latest CER date |
| `device_lifetime` | Recommended | Object | `{shelf_life, expected_service_life}` |
| `sterility_status` | Yes | String | `Sterile` or `Non-sterile` |
| `single_use_or_reusable` | Yes | String | `Single-use` or `Reusable` |
| `market_history` | Recommended | String | Market presence narrative with cumulative sales |
| `pms_plan_document` | Recommended | Object | `{number, title}` |
| `pmcf_plan_document` | Recommended | Object | `{number, title}` |
| `risk_management_file_document_number` | Recommended | String | RMF document reference(s) |
| `ifu_document` | Recommended | Object | `{number, version}` |
| `other_associated_documents` | Recommended | Array of strings | All other relevant QMS documents |

**Tips:**
- `eu_mdr_classification_and_rule` drives PSUR cadence (IIb/III = annual, IIa/I = biennial)
- `uk_mdr_classification_and_rule` triggers UK MDR regulatory requirements when UK sales are detected; if your device is placed on the GB market, fill this field to enable UK-specific analysis per The Medical Devices (Post-market Surveillance Requirements) (Amendment) (Great Britain) Regulations 2024
- `notified_body_name_and_id` must include a 4-digit number in parentheses for proper parsing
- The richer the `market_history` narrative, the better the Executive Summary and Conclusions

---

### 5. `ract_template.csv`

**Purpose:** Risk Acceptance Criteria Table data for Section G (Trend Report) benefit-risk contextualisation and Table 7 max expected rate comparison.

| Column | Required | Format | Description |
|---|---|---|---|
| `hazard_id` | Yes | Text | Unique hazard identifier (e.g., `H-001`) |
| `hazard_description` | Yes | Text | Full description of the hazard scenario |
| `hazard_category` | Recommended | Text | Category (e.g., `Mechanical Failure`, `Sterility`) |
| `harm` | Yes | Text | Resulting harm (IMDRF Annex F term preferred) |
| `severity` | Yes | Text | `Minor`, `Moderate`, `Serious`, `Critical` |
| `probability_before` | Yes | Text | Pre-mitigation probability (e.g., `Occasional`, `Remote`, `Improbable`) |
| `risk_level_before` | Yes | Text | Pre-mitigation risk level (`High`, `Medium`, `Low`) |
| `risk_control` | Yes | Text | Description of risk control measure(s) |
| `probability_after` | Yes | Text | Post-mitigation probability |
| `risk_level_after` | Yes | Text | Post-mitigation risk level |
| `imdrf_code` | Recommended | Text | IMDRF Annex A code (e.g., `A0301`) |
| `medical_device_problem` | Recommended | Text | IMDRF Annex A term |
| `expected_rate` | Yes | Numeric | Expected complaint rate (e.g., `0.001`) |
| `max_expected_rate` | Yes | Numeric | Maximum acceptable rate threshold |

**Rate formats accepted:** Decimals (`0.001`), percentages (`0.1%`), scientific notation (`1e-3`), fractions (`1/10000`), inequalities (`< 0.001`)

**Tips:**
- `max_expected_rate` values are compared against actual complaint rates in Table 7
- Rates use raw denominators (complaints/units), NOT per-1000
- Include all hazards from your Risk Management File, not just complaint-related ones

---

### 6. `previous_psur_data_template.json`

**Purpose:** Prior period PSUR data enabling year-over-year trend comparison, prior-action tracking, and Section A/M continuity.

| Field | Required | Type | Notes |
|---|---|---|---|
| `period` | Yes | Object | `{start_date, end_date}` in ISO format |
| `cadence` | Yes | String | `ANNUALLY` or `EVERY_TWO_YEARS` |
| `device_name` | Yes | String | Must match current device name |
| `manufacturer` | Yes | String | Legal manufacturer name |
| `prior_actions` | Yes | Array | `[{description, status}]` — status: `COMPLETED`, `IN_PROGRESS`, `OPEN` |
| `complaint_summary` | Yes | Object | `{total_complaints, serious_incidents, complaint_rate, by_category}` |
| `trend_data` | Recommended | Object | `{status, ucl, mean_rate, monthly_rates}` |
| `serious_incidents_count` | Yes | Integer | Count from prior period |
| `sales_data` | Recommended | Object | `{total_units, by_region}` |
| `notified_body_review` | Recommended | String | `YES`, `NO`, or `N_A` |
| `sections` | Recommended | Object | Key section narratives (executive_summary, conclusions) |

**Tips:**
- `prior_actions` with status tracking enables Section M continuity narrative
- `complaint_summary.by_category` should use the same IMDRF terms as your complaints data
- Providing `monthly_rates` in `trend_data` enables extended trend chart generation

---

### 7. `pms_plan_data_template.json`

**Purpose:** PMS Plan reference data for Section L (PMCF) and overall surveillance context.

| Field | Required | Type | Notes |
|---|---|---|---|
| `device_name` | Yes | String | Device name |
| `device_classification` | Yes | String | e.g., `Class IIb` |
| `pms_plan_version` | Recommended | String | Document revision |
| `pms_plan_date` | Recommended | String | ISO date |
| `proactive_activities` | Yes | Array of strings | All proactive surveillance activities |
| `reactive_activities` | Yes | Array of strings | All reactive surveillance activities |
| `psur_cadence` | Yes | String | `ANNUALLY` or `EVERY_TWO_YEARS` |
| `trend_reporting_thresholds` | Recommended | Object | Metric → threshold value pairs |
| `complaint_handling_summary` | Recommended | String | Complaint process narrative |
| `pmcf_plan_reference` | Recommended | String | PMCF document reference |
| `pmcf_activities` | Recommended | Array of strings | Planned PMCF activities |
| `associated_documents` | Recommended | Array of objects | `[{document_number, title}]` |

---

### 8. `literature_template.csv`

**Purpose:** Literature search results for Section J (Scientific Literature Review). Without this file, Section J states that no formal literature search results were provided and references the CER.

| Column | Required | Format | Description |
|---|---|---|---|
| `article_id` | Yes | Text | Unique article identifier (e.g., `LIT001`) |
| `title` | Yes | Text | Full article title |
| `authors` | Yes | Text | Semicolon-separated author list |
| `journal` | Yes | Text | Journal name |
| `publication_date` | Yes | `YYYY-MM-DD` | Publication date |
| `database` | Yes | Text | Database searched (e.g., `PubMed`, `Embase`, `Cochrane Library`) |
| `search_terms` | Yes | Text | Semicolon-separated search terms that retrieved the article |
| `relevance` | Yes | `Yes` / `No` | Whether the article is relevant to the device under review |
| `findings_summary` | Yes | Text | One-paragraph summary of findings relevant to the device |
| `safety_signal` | Yes | `Yes` / `No` | Whether the article raises a potential safety signal |

**Tips:**
- The parser counts `relevance = Yes` rows for `number_of_relevant_articles_identified`
- Articles flagged `safety_signal = Yes` are surfaced to the Section J agent for discussion
- Use the same `database` names as your literature search protocol

---

### 9. `analysis_workbook_template.xlsx`

**Purpose:** Pre-computed analysis tables that bypass raw data calculation, providing production-ready Table 1, Table 7, complaint trending, and Section D incident tables.

**Sheet structure:**

| Sheet Name | Content | Key Columns |
|---|---|---|
| `sales_tables` | Table 1 (annual by region) + monthly sales | Region, year columns, % of Total; Month, Units Sold |
| `complaint_trending` | Monthly complaint rates with control limits | Month, Complaints, Cumulative Sales, Complaint Rate, Mean Rate, UCL, Breach |
| `harms_table` | Table 7 harm × MDP cross-tab | Harm/MDP, Current Period rate(count), Cumulative rate(count), Max Expected Code, Max Expected Rate |
| `section_d` | Tables 2/3/4 (serious incidents) | Region, IMDRF Code–Term, Count, Rate %, Complaint Numbers |

**Tips:**
- Rate(count) format in harms_table: `0.0008 (10)` — parser splits rate and count automatically
- Indented rows (leading spaces) in harms_table are treated as MDP sub-rows under the preceding harm
- Section D tables are separated by `Table 2`, `Table 3`, `Table 4` markers
- When this workbook is provided, its pre-computed values take precedence over raw data calculations

---

## Data Flow Through the Pipeline

```
                    ┌─────────────────┐
                    │  Auto-Discovery  │  ← Filename keyword matching
                    └────────┬────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
        ┌──────────┐  ┌───────────┐  ┌──────────┐
        │ CSV/Excel │  │   JSON    │  │ PDF/DOCX │
        │  Parsers  │  │  Loaders  │  │   LLM    │
        │(AI column │  │(direct    │  │Extraction│
        │ mapping)  │  │ parse)    │  │          │
        └─────┬─────┘  └─────┬─────┘  └─────┬────┘
              │              │              │
              └──────────────┼──────────────┘
                             ▼
                    ┌─────────────────┐
                    │   IMDRF Auto-   │  ← Codes uncoded complaints
                    │     Coding      │
                    └────────┬────────┘
                             ▼
                    ┌─────────────────┐
                    │   Statistics    │  ← Deterministic computation
                    │   Engine       │     (rates, UCL, cross-tabs)
                    └────────┬────────┘
                             ▼
                    ┌─────────────────┐
                    │  13 Section     │  ← LLM agents (A through M)
                    │  Agents        │     receive pre-computed stats
                    └────────┬────────┘
                             ▼
                    ┌─────────────────┐
                    │  Validation +   │  ← 331-point checklist
                    │  DOCX Render    │
                    └─────────────────┘
```

---

## Optimization Tips

1. **Pre-code IMDRF codes** in complaints to avoid AI auto-coding overhead
2. **Use exact column names** from templates — bypasses AI column mapping entirely
3. **Provide the analysis workbook** for production PSURs — pre-computed tables ensure deterministic output
4. **Fill all device_context fields** — every blank field reduces agent narrative quality
5. **Include previous PSUR data** — enables year-over-year comparison and trend continuity
6. **Use consistent region names** across all files (sales, complaints, CAPA)
7. **Use ISO dates** (`YYYY-MM-DD`) everywhere for reliable date parsing
