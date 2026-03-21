# PSUR Generator — Global Context Reference

Every section agent receives the same **global context** block prepended to its system prompt. This block is assembled once per pipeline run by `build_global_context()` in `agents/prompts/global_context.py` and contains eight labeled sections.

Token budget: ~600–800 tokens (varies with device details).

---

## 1. Identity & Role

| Item | Value |
|------|-------|
| Role | Regulatory affairs author drafting a PSUR per EU MDR |
| Form | CooperSurgical FormQAR-054 Rev C |
| Authority | Manufacturer's PSUR responsible person |
| Audience | Notified Bodies and Competent Authorities |

Static — same for every run.

---

## 2. Device Under Evaluation

| Field | Source | Example |
|-------|--------|---------|
| Name | `device_context["device_name"]` | Fischer Cone Biopsy Excisor |
| EU MDR Class | `device_context["device_class_eu"]` | IIa |
| US FDA Class | `device_context["device_class_us"]` | II |
| Intended Use | `device_context["intended_use"]` | Cervical cone biopsy excision… |
| Manufacturer | `device_context["manufacturer_info"]["company_name"]` | CooperSurgical, Inc. |
| Notified Body | `device_context["notified_body"]["name"]` | BSI |
| UDI-DI | `device_context["known_identifiers"]["basic_udi_di"]` | 10801741011… |

Dynamic — extracted from input files by `extract_device_context_llm()` at pipeline start.

---

## 3. Reporting Period

| Field | Source | Example |
|-------|--------|---------|
| Start date | CLI `--start` | 2024-07-01 |
| End date | CLI `--end` | 2025-08-31 |

---

## 4. Quantitative Ground Truth

Pre-computed in `statistics.py` — agents cite these verbatim, never recalculate.

| Statistic | Source Key | Example |
|-----------|-----------|---------|
| Total units sold | `total_units_sold` | 36,260 |
| Total complaints | `total_complaints` | 14 |
| Complaint rate | `overall_complaint_rate` | 0.000386 (raw fraction) |
| Complaint percentage | `overall_complaint_percentage` | 0.04% |
| Upper Control Limit (UCL) | `trend_analysis.ucl_3sigma_pct` | 14.76 |
| Serious injuries | `serious_incident_count` | 2 |
| Deaths | (hard-coded 0 unless overridden) | 0 |
| Field safety actions | (orchestrator-level; default 0) | 0 |
| Trend direction | `trend_analysis.status` | stable |
| Top IMDRF codes | `complaints_by_imdrf` (top 10 by count) | Difficult to use (4), Material deformation (3)… |

Agents are forbidden from rounding, estimating, or recalculating any of these values.

---

## 5. Writing Rules

### DO
| Rule |
|------|
| Formal regulatory English in narrative paragraphs |
| Past tense for observed data, present tense for ongoing conclusions |
| Third person only — attribute to "the manufacturer" or the company name |
| Cite every quantitative claim using exact values from ground truth |
| Reference other sections by letter (e.g., "as detailed in Section C") |
| Use the device name consistently as given in Block 2 |

### DO NOT
| Rule |
|------|
| Bullet points, numbered lists, or markdown formatting in narratives |
| Cite regulation articles (e.g., "MDR Article 86(2)", "MDCG 2022-21") |
| Invent, estimate, or round any statistic |
| Speculative language ("likely", "possibly", "it appears") |
| AI disclaimers, caveats, or meta-commentary |
| Reassurance, minimization, or superlatives |
| Marketing language ("industry-leading", "best-in-class") |

---

## 6. Terminology Dictionary

Enforces consistent language across all 13 sections.

| Term | Required Usage |
|------|---------------|
| Device name | Exact name from Block 2 — never abbreviated or paraphrased |
| Complaint rate | Always "complaint rate" (never "failure rate" or "event rate") |
| UCL | "Upper Control Limit" on first use per section, then "UCL" |
| IMDRF | Always "IMDRF" (never "GHTF"); descriptive terms only — no alphanumeric codes |
| Reporting period | Always "the reporting period" |
| CAPA | "corrective and preventive action" on first use, then "CAPA" |
| PMS | "post-market surveillance" on first use, then "PMS" |
| Manufacturer | Company name or "the manufacturer" — never "we" or "our" |

---

## 7. Missing Data Protocol

| Scenario | Required Behavior |
|----------|-------------------|
| Required field, no data provided | Populate with "No [data type] data was available for the reporting period." |
| Gap filling | Never fabricate data or use placeholder values that look real |
| Optional field, no data | Use `null` or empty string per schema type |
| Identifier field, no data | Use "N/A" |

---

## 8. Output Format

| Rule | Detail |
|------|--------|
| Response format | Valid JSON matching the section schema — nothing before or after |
| TriState fields | Exactly `"yes"`, `"no"`, or `"n/a"` |
| YesNoNA fields | Exactly `"Yes"`, `"No"`, or `"N/A"` |
| Rates | 2 decimal places |
| Percentages | 1 decimal place |
| Unit counts | Whole numbers, never rounded or estimated |

---

## Assembly

These eight blocks are joined with double newlines and prepended to every agent's system prompt before the section-specific schema, guidance, MDCG context, critical constraints, and section addendums.

```
┌─────────────────────────────────────┐
│ GLOBAL CONTEXT (this document)      │  ~800 tokens
├─────────────────────────────────────┤
│ ==== SECTION TASK: {key} ====       │
├─────────────────────────────────────┤
│ SECTION SCHEMA                      │  ~2K–8K tokens (varies)
├─────────────────────────────────────┤
│ SECTION-SPECIFIC GUIDANCE           │  ~1K–4K tokens
├─────────────────────────────────────┤
│ MDCG REGULATORY CONTEXT             │  ~500 tokens
├─────────────────────────────────────┤
│ CRITICAL CONSTRAINTS (16 rules)     │  ~500 tokens
├─────────────────────────────────────┤
│ SECTION ADDENDUM (A–M specific)     │  ~200–600 tokens
└─────────────────────────────────────┘
```

Built by `SectionAgent._build_system_prompt()` in `agents/base.py`.
