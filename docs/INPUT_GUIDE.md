# PSUR Generator — Input Guide

What to provide, where to put it, and what the system does with each file.

---

## Quick Start

```
psur-generator/data/input/          ← drop your files here
psur-generator/.env                 ← API keys
```

```bash
python main.py generate --start 2025-01-01 --end 2025-12-31
```

The system **auto-discovers** and **auto-classifies** every file in `data/input/` — no flags needed. File naming hints help (see below), but the AI classifier handles ambiguous names too.

---

## Environment Setup

Create `psur-generator/.env`:

```env
ANTHROPIC_API_KEY=sk-ant-...        # Required — primary LLM
OPENAI_API_KEY=sk-...               # Optional — fallback + reasoning model
OPENAI_FALLBACK_MODEL=gpt-4.1      # Optional — default: gpt-4.1
OPENAI_REASONING_MODEL=gpt-5.2     # Optional — default: gpt-5.2
```

---

## Input Files

### Required

| File | What It Is | Sections It Feeds | Formats |
|------|-----------|-------------------|---------|
| **Sales data** | Units sold/distributed per period | C (population exposure), F (rates), G (trends) | CSV, XLSX |
| **Complaints data** | Individual complaint records | D (serious incidents), E (feedback), F (rates/types), G (trends) | CSV, XLSX |

Without these two files, the generator cannot compute complaint rates or population exposure — the quantitative backbone of the report.

### Strongly Recommended

| File | What It Is | Sections It Feeds | Formats |
|------|-----------|-------------------|---------|
| **CER** | Clinical Evaluation Report | B (scope/device description), J (literature), L (PMCF), M (conclusions) | PDF, DOCX |
| **Previous PSUR** | Prior period's PSUR | A (executive summary), G (trend comparison), M (prior commitments) | DOCX, PDF, JSON |
| **PMS Plan** | Post-Market Surveillance Plan | A (scope), B (device context), I (CAPA strategy), M (planned actions) | DOCX, PDF |

The CER is the richest source for device identity — name, classification, UDI, intended use, notified body, and certificate details are all extracted from it automatically.

### Optional (Enriching)

| File | What It Is | Sections It Feeds | Formats |
|------|-----------|-------------------|---------|
| **CAPA records** | Corrective/preventive actions | I (CAPA section) | CSV, XLSX |
| **RACT** | Risk Assessment & Control Table | F (expected rates), G (trend vs. thresholds) | XLSX, CSV |
| **FSCA data** | Field Safety Corrective Actions | H (FSCA section) | CSV, XLSX |
| **PMCF report/plan** | Post-Market Clinical Follow-up | L (PMCF section) | PDF, DOCX |
| **External DB results** | MAUDE, EUDAMED, registry searches | K (external databases) | CSV, XLSX, PDF |
| **IFU** | Instructions for Use | B (device description, indications) | PDF, DOCX |
| **RMF** | Risk Management File | B (risk context), F (residual risk) | PDF, DOCX |

If an optional file is absent, the corresponding PSUR section will explicitly state the data was unavailable and describe typical methodology — it won't fabricate content.

---

## File Naming

Auto-discovery uses keyword matching first, then AI classification as fallback. Naming your files with these keywords makes discovery instant:

| Category | Recognized Keywords |
|----------|-------------------|
| Sales | `sales`, `distribution`, `units_sold`, `shipment` |
| Complaints | `complaint`, `adverse`, `vigilance`, `mdr_report` |
| CAPA | `capa`, `corrective`, `preventive` |
| CER | `cer`, `clinical_evaluation`, `clinical evaluation` |
| IFU | `ifu`, `instructions_for_use`, `instructions for use` |
| RMF | `rmf`, `risk_management`, `risk management` |
| RACT | `ract`, `risk_assessment`, `risk assessment`, `risk_control` |
| PMS Plan | `pms`, `post_market_surveillance`, `pms_plan`, `plan` |
| PMCF | `pmcf`, `post_market_clinical`, `post-market clinical` |
| FSCA | `fsca`, `field_safety`, `field safety` |
| External DB | `maude`, `external_db`, `external_database`, `registry`, `eudamed` |
| Previous PSUR | `previous_psur`, `prior_psur`, `previous psur` |

Examples of good filenames:
```
081_sales.csv
081_complaints.csv
CER_Fischer_Cone_Biopsy_Excisor.pdf
previous_psur_2024.docx
CAPA_register_2025.xlsx
RACT_081.xlsx
PMS_Plan_081.docx
```

If the filename doesn't match any keyword, the system peeks at the file content and uses AI classification. If that's also inconclusive, you'll be prompted to classify it manually.

---

## Column Expectations

Column names do **not** need to match a fixed schema. The AI column mapper reads your headers + sample rows and infers the mapping. That said, here's what it looks for:

### Sales Columns

| Target Field | What the Mapper Looks For | Required |
|-------------|--------------------------|----------|
| `date` | Sale/shipment/invoice date | No |
| `year` | Calendar or fiscal year | No |
| `month` | Month name or number | No |
| `quantity` | Units sold/shipped/distributed | No |
| `region` | Country, market, territory | No |
| `product` | Product name, SKU, catalog number | No |

The mapper needs *either* a `date` column *or* a `year`+`month` pair to filter by reporting period.

### Complaints Columns

| Target Field | What the Mapper Looks For | Required |
|-------------|--------------------------|----------|
| `date` | Complaint/event/received date | No |
| `complaint_number` | Unique ID, case number | No |
| `description` | Narrative, event description | No |
| `imdrf_code` | IMDRF Annex A code or problem text | No |
| `harm` | Patient harm / health impact (Annex F) | No |
| `serious` | Serious/reportable flag (yes/no/bool) | No |
| `region` | Country or region of origin | No |

If `imdrf_code` is missing or incomplete, the IMDRF auto-coder will assign codes using AI.

### CAPA Columns

| Target Field | What the Mapper Looks For | Required |
|-------------|--------------------------|----------|
| `capa_number` | CAPA ID or reference number | No |
| `title` | CAPA title or description | No |
| `status` | Open / Closed / In Progress | No |
| `open_date` | Initiation date | No |
| `close_date` | Completion date | No |
| `root_cause` | Root cause category | No |
| `type` | Corrective vs. Preventive | No |

All columns are optional — the mapper degrades gracefully when fields are missing.

---

## CLI Options

```
python main.py generate [DEVICE_NAME] --start DATE --end DATE [OPTIONS]
```

| Argument / Option | Description | Default |
|-------------------|-------------|---------|
| `DEVICE_NAME` | Device name (positional, optional) | Auto-detected from CER/inputs |
| `--start`, `-s` | Reporting period start (YYYY-MM-DD) | **Required** |
| `--end`, `-e` | Reporting period end (YYYY-MM-DD) | **Required** |
| `--input`, `-i` | Input directory path | `data/input/` |
| `--output`, `-o` | Output directory path | `data/output/` |
| `--resume` | Resume from last checkpoint | `false` |

Override flags also exist (`--sales`, `--complaints`, `--capa`, `--cer`, etc.) to point at specific file paths, bypassing auto-discovery.

---

## What Gets Auto-Detected

You don't need to manually specify any of the following — the system extracts them from your input files:

| Metadata | Primary Source | Fallback Sources |
|----------|---------------|-----------------|
| Device name | CER title/headers | Sales product column, complaints, previous PSUR |
| EU MDR class | CER regulatory section | PMS Plan, previous PSUR |
| Single-use vs. reusable | CER device description | IFU |
| Certificate number | CER certificates section | PMS Plan |
| Notified body | CER regulatory section | Previous PSUR |
| UDI-DI | CER identifiers | PMS Plan |
| PSUR cadence | MDR class (auto-derived) | PMS Plan |
| IMDRF codes | Complaints data | Auto-coded by AI if missing |
| Complaint rates, UCL, trends | Sales + Complaints | Deterministic computation |

---

## Output

After generation, `data/output/` contains:

| File | Description |
|------|-------------|
| `PSUR_{device}_{year}.json` | Full structured PSUR (all 13 sections) |
| `PSUR_{device}_{year}_statistics.json` | Pre-computed statistics snapshot |
| `PSUR_{device}_{year}.docx` | Formatted FormQAR-054 Rev C document |
| `charts/*.png` | Sales trend and complaint rate charts |
