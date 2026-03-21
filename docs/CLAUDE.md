# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

PSUR (Periodic Safety Update Report) Generator for CooperSurgical medical devices, implementing FormQAR-054 Rev C. Generates regulatory PSUR documents from sales, complaints, CAPA, and CER input data through a pipeline of parsing, statistics computation, LLM section generation, validation, and DOCX rendering.

## Commands

```bash
# Install dependencies (WSL2, no sudo)
pip install -r psur-generator/requirements.txt --break-system-packages

# Generate a PSUR (main workflow)
python psur-generator/main.py generate --start 2025-01-01 --end 2025-12-31

# Generate with explicit input directory
python psur-generator/main.py generate --start 2025-01-01 --end 2025-12-31 --input-dir psur-generator/data/input

# Resume from checkpoint (after interruption)
python psur-generator/main.py generate --start 2025-01-01 --end 2025-12-31 --resume

# Validate existing PSUR JSON
python psur-generator/main.py validate <path-to-psur.json>

# Re-render PSUR JSON to DOCX
python psur-generator/main.py render <path-to-psur.json>
```

No test suite exists. Validation is done via `main.py validate`.

## Architecture

### Pipeline Flow

```
Input Files → Auto-Discovery → Parsing (AI Column Mapping) → IMDRF Auto-Coding
→ Statistics (deterministic) → Charts (matplotlib) → 13 LLM Section Agents (A–M)
→ Validation → DOCX Template Rendering → Output (JSON + DOCX + PNGs)
```

### Key Modules (`psur-generator/`)

| Module | Role |
|---|---|
| `main.py` | Typer CLI. File auto-discovery (keyword→AI→user prompt), device context extraction, IMDRF coding, orchestration glue, performance reporting |
| `config.py` | Paths, API key (`.env`), model selection (`claude-sonnet-4-20250514`) |
| `agents/orchestrator.py` | Iterates 13 sections (SECTION_ORDER), routes input data to each section via section_data_map, enforces benefit-risk linkage, handles checkpoint/resume |
| `agents/base.py` | `SectionAgent` class. Builds system prompt from schema+guidance, calls Claude API, parses JSON response (brace-depth extraction), auto-retries up to 3 attempts on validation failure. Per-section max_tokens budgets |
| `statistics.py` | `compute_psur_statistics()` — all rates, UCL, cross-tabs computed deterministically BEFORE LLM calls. LLM receives these as facts, never calculates them |
| `validator.py` | Schema validation (jsonschema), content integrity, fabrication detection, bullet/citation bans, IMDRF code validation, Table 7 reconciliation. 331-point checklist |
| `renderer_template.py` | Clones `FormQAR-054_template.docx`, replaces `{{PLACEHOLDER}}` markers, clones table rows for data, embeds chart PNGs |
| `charts.py` | Matplotlib: sales trend line, complaint rate + UCL/LCL band chart |
| `imdrf_coder.py` | Auto-codes uncoded complaints via Claude (Annex A device problems + Annex F health impacts) |

### Parsers (`psur-generator/parsers/`)

| Parser | Input | Method |
|---|---|---|
| `sales.py` | CSV/Excel | AI column mapping via `column_mapper.py` |
| `complaints.py` | CSV/Excel | AI column mapping, builds harm×imdrf cross-tabs |
| `capa.py` | CSV/Excel | AI column mapping |
| `cer_extractor.py` | PDF/DOCX | pdfplumber + Claude Vision OCR fallback |
| `universal.py` | Any format | Multi-format dispatcher (CSV, Excel, DOCX, PDF, JSON, text, images) |
| `column_mapper.py` | DataFrame | Claude-powered column name inference with confidence scores |
| `previous_psur.py` | DOCX/PDF/JSON | LLM-driven structured extraction |
| `ract.py` | Excel | Max expected rates extraction |
| `pms_plan.py` | DOCX/PDF | LLM-driven structured extraction |

### Constraint Files (`psur-generator/constraints/`)

- `template.json` (60KB) — FormQAR-054 JSON Schema with `$defs` for shared types (TriState, YesNoNA, MDRClass, USFDAClass). `$ref` references resolved at runtime by `base.py._resolve_refs()`
- `psur_agent_guidance.json` (130KB) — Per-section LLM guidance + `meta.global_rules` for writing style. Section keys use full names (e.g., `A_executive_summary`)
- `FormQAR-054_template.docx` — DOCX template with `{{PLACEHOLDER}}` markers and placeholder table rows for cloning

### 13 PSUR Sections

`A_executive_summary` through `M_findings_and_conclusions` — defined in `orchestrator.SECTION_ORDER`. Each section has its own schema in template.json and guidance in psur_agent_guidance.json.

## Critical Design Rules

1. **Statistics are never LLM-computed.** All rates, counts, UCL, cross-tabs are deterministic in `statistics.py`. LLM receives pre-computed values as facts.
2. **Rates use raw denominators** (complaints/units, e.g., 0.003064), NOT per-1000.
3. **No regulation citations in narratives.** Validator bans MDR Article X(Y), MDCG XXXX patterns. Internal doc refs (ISO 14971, Annex II) are allowed.
4. **No bullet points in narratives.** Validator enforces narrative-only prose style.
5. **Template fidelity.** Renderer never creates DOCX structure — only clones from the template DOCX.
6. **$ref resolution** happens at agent init time via `_resolve_refs()` which recursively inlines all `$defs`.
7. **JSON extraction** uses brace-depth counting to handle LLM trailing text after the JSON object.
8. **Benefit-risk linkage** is enforced on sections C and G by the orchestrator post-generation.
9. **Validator skips `_` prefixed keys** (like `_statistics`) to avoid false positives on internal metadata.

## Environment

- WSL2 Linux, no sudo access for apt
- Python packages: `pip install --break-system-packages`
- API key: `ANTHROPIC_API_KEY` in `psur-generator/.env`
- Input files go in `psur-generator/data/input/`
- Output lands in `psur-generator/data/output/`

## File Reference

- `SLOT_MAP.md` — Complete field-by-field reference for all ~175 template slots with types, constraints, and required status
- `psur_validator_questions.md` — 331-question validation checklist across 18 categories
