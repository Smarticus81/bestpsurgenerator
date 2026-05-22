# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Project Overview

Automated generator for **Periodic Safety Update Reports (PSURs)** for medical devices. Produces EU MDR Article 86 / MDCG 2022-21 compliant PSURs with UK MDR 2024 (Part 4A) support. Outputs both structured JSON and formatted DOCX (from FormQAR-054 template).

LLM-powered multi-agent pipeline: 13 section agents generate regulatory prose from parsed input data, with deterministic pre-computed statistics to prevent fabrication.

## Commands

All commands run from `psur-generator/`:

```bash
cd psur-generator
pip install -r requirements.txt

# Generate a full PSUR (requires ANTHROPIC_API_KEY in .env)
python main.py generate --start 2025-01-01 --end 2025-12-31

# Generate with options
python main.py generate --start 2025-01-01 --end 2025-12-31 -i data/input/ -o data/output/
python main.py generate --start 2025-01-01 --end 2025-12-31 --ollama-model qwen3:32b
python main.py generate --start 2025-01-01 --end 2025-12-31 --resume  # resume from checkpoint

# Validate generated PSUR JSON (331-point checklist)
python main.py validate path/to/PSUR.json [--docx path/to/PSUR.docx]

# Re-render PSUR JSON to DOCX
python main.py render path/to/PSUR.json [-o output.docx]

# Audit PSUR against MDCG 2022-21 + UK MDR 2024
python main.py audit path/to/PSUR.docx [--no-uk-mdr] [--no-llm] [-o report.json]
```

No test suite exists. Validation is the built-in 331-point checklist via `python main.py validate`.

## Architecture

### Pipeline Phases (main.py orchestrates)

```
Input files (CSV/Excel/PDF/DOCX/JSON) in data/input/
  → pipeline/discovery.py    — auto-discover & classify by filename keywords
  → pipeline/input_parsing.py — delegate to parsers/ (universal, sales, complaints, capa, ract, etc.)
  → pipeline/device_context.py — extract device metadata (JSON fast-path or LLM fallback)
  → imdrf_coder.py           — auto-code uncoded complaints with IMDRF Annex A/F terms
  → statistics.py             — deterministic pre-computation of ALL metrics (rates, UCL, cross-tabs)
  → agents/orchestrator.py    — run 13 section agents (A-M) with filtered context per section
  → agents/postprocessing.py  — fix LLM output (repair tables, strip citations, fix contradictions)
  → validation/validator.py   — 331-point checklist (6 mixin classes in validation/)
  → rendering/renderer.py     — clone DOCX template, fill placeholders/tables/charts
  → Output: PSUR_<device>_<year>.json + .docx in data/output/
```

### Key Design Decisions

- **Deterministic-first statistics**: `statistics.py` pre-calculates all metrics and passes them as facts to LLM agents. Agents are instructed to use these numbers verbatim, never calculate their own. The validator detects fabricated statistics.
- **Agent isolation**: Each of the 13 section agents receives only stats relevant to that section via `agents/stats_filter.py`. Section D sees serious incidents; Section J sees literature.
- **Template-clone-and-fill**: Rendering clones `constraints/FormQAR-054_template.docx` and fills it via python-docx, preserving exact layout fidelity.
- **LLM routing**: Anthropic Codex (primary) → OpenAI GPT (rate-limit fallback) → Ollama (local override). Managed in `llm_client.py`.
- **Post-processing over perfection**: Rather than requiring perfect LLM output, `postprocessing.py` applies targeted fixes (repair malformed tables, normalize IMDRF codes, strip NB references for Class I, etc.).

### Regulatory Constraint Files (constraints/)

| File | Purpose |
|------|---------|
| `template_schema.json` | JSON Schema — authoritative contract between agents and renderer |
| `section_guidance.json` | Field-by-field instructions for each section agent |
| `mdcg_2022_21_knowledge_base.json` | MDCG 2022-21 + UK MDR regulatory requirements |
| `ract_occurrence_codes.json` | Risk classification O1-O5 with rate thresholds |
| `harm_mdp_codes.csv` | IMDRF Annex A (Device Problem) and Annex F (Harm) code tables |
| `FormQAR-054_template.docx` | DOCX template cloned during rendering |

### Validation System (validation/)

Six mixin classes composed in `validator.py`:
- `_schema_checks.py` — JSON Schema conformance
- `_fabrication_checks.py` — detects LLM-generated fake data (rates, UDI-DIs, regions)
- `_formatting_checks.py` — document formatting rules
- `_content_checks.py` — regulatory content requirements
- `_consistency_checks.py` — cross-section consistency
- `_docx_checks.py` — rendered DOCX structure validation

## Domain Concepts

### Regulatory Frameworks
- **EU MDR (2017/745)** Article 86 + **MDCG 2022-21** guidance — primary framework
- **UK MDR 2024** (SI 2024/1368) Part 4A (Regs 44ZC-44ZR) — post-market surveillance requirements for GB market
- UK MDR requirements activate automatically when UK sales data is detected (`uk_market_detected`)

### PSUR Cadence by Device Classification
| Class | Cadence | Regulation |
|-------|---------|------------|
| I | Biennial (PMSR, not PSUR) | 44ZL |
| IIa | Biennial | 44ZM(7-8) |
| IIb | Annual | 44ZM(6) |
| III | Annual | 44ZM(6) |
| Implantable | Always annual | 44ZM(6) |

### Denominator Logic
- **Single-use/disposable**: denominator = units distributed
- **Reusable**: denominator = estimated procedures (episodes of use)
- Same denominator must be used consistently across all sections

### Complaint Rate Calculation
- Raw rate = complaints / denominator (NOT per-1000)
- UCL trending uses Western Electric rules (Rules 1-4) on monthly rates
- RACT comparison: actual rates vs. max expected rates per hazard → O1-O5 occurrence codes

### IMDRF Coding
- Annex A: Device Problem codes (e.g., "A0701 - Device breakage")
- Annex F: Health Impact/Harm codes (e.g., "F0101 - No Harm", "F0401 - Death")
- Auto-coded by LLM when missing from input data (`imdrf_coder.py`)

### UK MDR 2024 Part 4A Key Requirements
- **44ZE**: Manufacturer must maintain PMS system proportionate to risk
- **44ZF**: PMS plan must specify device lifetime, incident processes, threshold values
- **44ZH-44ZI**: Serious incident reporting (2 days for public health threats, 10 days for death, 15 days otherwise)
- **44ZJ-44ZK**: FSCA reporting (UK and outside GB)
- **44ZL**: PMSR for Class I / IVD Class A-B (3-year cycle)
- **44ZM**: PSUR for all other classes (annual/biennial per classification)
- **44ZN**: Trend reporting for significant increases in incident frequency/severity
- **44ZQ**: Retention: 15 years for implantables, 10 years for others (or PMS period if longer)

## Environment Setup

Requires `.env` in `psur-generator/` with:
```
ANTHROPIC_API_KEY=sk-...        # Required (primary LLM)
OPENAI_API_KEY=sk-...           # Optional (fallback)
OLLAMA_MODEL=qwen3:32b          # Optional (local override)
```

## Input File Conventions

Place files in `psur-generator/data/input/`. Auto-discovery uses filename keywords:
- Sales: `*sales*`, `*distribution*`, `*units_sold*`
- Complaints: `*complaint*`, `*adverse*`, `*vigilance*`
- CAPA: `*capa*`, `*corrective*`, `*preventive*`
- Device context: `device_context.json`
- RACT: `*ract*`, `*risk*`
- Templates with column specs are in `data/templates/`

See `data/templates/INPUT_README.md` for full column specifications.
