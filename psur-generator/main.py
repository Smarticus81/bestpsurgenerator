"""PSUR Generator CLI.

Thin entry point — orchestrates the pipeline by delegating to sub-modules:
  pipeline/   — file discovery, device context extraction, input parsing, perf reporting
  agents/     — LLM section generation (with dynamic global context)
  parsers/    — input file parsing (called from pipeline/input_parsing)
  statistics  — deterministic statistics and chart generation
  validator   — schema + content validation
  renderer    — DOCX template rendering
"""
import json
import os
import re
import sys
import faulthandler
from datetime import datetime
faulthandler.enable()

# Force UTF-8 for stdout/stderr so Unicode characters (arrows, em-dashes, etc.)
# emitted by rich don't crash on Windows cp1252 consoles.
for _stream in ("stdout", "stderr"):
    _s = getattr(sys, _stream, None)
    if _s is not None and hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import typer
from pathlib import Path
from dataclasses import asdict
from typing import Any, Dict, List, Optional

import time as _time
import logging
from rich.console import Console

from config import INPUT_DIR, OUTPUT_DIR
from statistics import compute_psur_statistics
from agents.orchestrator import generate_psur
from validation import PSURValidator, ValidationEngine
from rendering import PSURTemplateRenderer
from deterministic_tables import apply_psur_table_skills
from charts import generate_all_charts
from contradiction_remediation import remediate_contradictions_with_llm, run_full_coherence_audit

# Pipeline modules
from pipeline.discovery import auto_discover_inputs, print_discovered_files
from pipeline.format_contract import audit_discovered_formats
from pipeline.input_resolver import resolve_inputs, print_input_resolution
from pipeline.device_context import (
    extract_device_context_llm,
    gather_file_snippets,
    load_device_context_file,
    resolve_device_metadata,
    build_device_context,
)
from pipeline.input_parsing import parse_all_inputs
from pipeline.performance import print_performance_summary

logger = logging.getLogger(__name__)


def _format_engine_messages(messages: List[str], *, limit: int = 10) -> List[str]:
    shown = list(messages[:limit])
    if len(messages) > limit:
        shown.append(f"... and {len(messages) - limit} more")
    return shown


def _blocking_coherence_findings(report: Any) -> List[Any]:
    return [
        f
        for f in getattr(report, "findings", []) or []
        if getattr(f, "severity", "") in {"CRITICAL", "MAJOR"}
        and str(getattr(f, "finding_id", "")).startswith("LLM-COHERENCE")
    ]


def _all_blockers_are_near_pass_coherence(report: Any) -> bool:
    blockers = [
        f
        for f in getattr(report, "findings", []) or []
        if getattr(f, "severity", "") in {"CRITICAL", "MAJOR"}
    ]
    coherence = _blocking_coherence_findings(report)
    return bool(blockers) and len(blockers) <= 2 and len(blockers) == len(coherence)


def _annotate_unresolved_coherence_issues(psur: Dict[str, Any], report: Any) -> None:
    findings = _blocking_coherence_findings(report)
    if not findings:
        return
    psur["_generated_with_unresolved_coherence_findings"] = [
        {
            "finding_id": getattr(f, "finding_id", ""),
            "severity": getattr(f, "severity", ""),
            "section": getattr(f, "section", ""),
            "title": getattr(f, "title", ""),
            "evidence": getattr(f, "evidence", ""),
            "expected": getattr(f, "expected", ""),
            "recommendation": getattr(f, "recommendation", ""),
        }
        for f in findings
    ]
    issue_text = "; ".join(
        f"{getattr(f, 'finding_id', '')} ({getattr(f, 'section', '')}): {getattr(f, 'title', '')}"
        for f in findings
    )
    sec_m = psur.setdefault("sections", {}).setdefault("M_findings_and_conclusions", {})
    existing = str(sec_m.get("limitations_of_data_and_conclusion") or "").strip()
    note = (
        "Unresolved coherence review note: the report was generated with the following "
        f"LLM coherence finding(s) requiring reviewer attention before release: {issue_text}."
    )
    if note not in existing:
        sec_m["limitations_of_data_and_conclusion"] = f"{existing} {note}".strip()


def _prompt_near_pass_action(console: Console, report: Any) -> str:
    console.print(
        "\n[yellow]Near-pass coherence gate: 2 or fewer LLM coherence finding(s) remain.[/yellow]"
    )
    for f in _blocking_coherence_findings(report):
        console.print(f"  - {f.finding_id} [{f.severity}] {f.section}: {f.title}")
    console.print("Choose how to proceed:")
    console.print("  [bold]1[/bold] Generate PSUR with these issues highlighted")
    console.print("  [bold]2[/bold] Stop generation")
    console.print("  [bold]3[/bold] Continue remediation loop")
    while True:
        choice = typer.prompt("Enter 1, 2, or 3", default="2").strip()
        if choice in {"1", "2", "3"}:
            return choice
        console.print("[yellow]Please enter 1, 2, or 3.[/yellow]")


def _build_rich_context_from_config(wb_config: Dict[str, Any]) -> Dict[str, Any]:
    """Build a 'rich' device context dict from Config sheet data.

    This is used when no device_context.json exists, so the LLM sections
    still get manufacturer/device metadata from the workbook.
    """
    nb_name = wb_config.get("notified_body_name", "")
    nb_number = wb_config.get("notified_body_number", "")
    basic_udi_di = wb_config.get("basic_udi_di", "")
    gmdn_code = wb_config.get("gmdn_code", "")
    emdn_code = wb_config.get("emdn_code", "")
    device_name = wb_config.get("device_name", "")
    trade_name = wb_config.get("device_trade_name", device_name)

    return {
        "device_trade_names": [trade_name] if trade_name else [],
        "device_description": "",
        "intended_use": "",
        "indications": [],
        "contraindications": [],
        "target_patient_population": "",
        "intended_user_profile": "",
        "sterility_status": "",
        "single_use_or_reusable": "",
        "market_history": "",
        "device_lifetime": "",
        "known_identifiers": {
            "basic_udi_di": basic_udi_di,
            "model_numbers": [],
            "catalog_numbers": [],
            "emdn_code": emdn_code,
            "gmdn_codes": [str(gmdn_code)] if gmdn_code else [],
            "classification_rule_mdr_annex_viii": "",
            "first_ce_marking_date": "",
            "first_declaration_of_conformity_date": "",
            "risk_management_file_number": "",
        },
        "notified_body": {"name": nb_name, "number": str(nb_number)},
        "cer_document": {"number": "", "date": ""},
        "pms_plan_document": {},
        "pmcf_plan_document": {},
        "ifu_document": {},
        "other_associated_documents": [],
        # Extra manufacturer fields from Config
        "manufacturer_name": wb_config.get("manufacturer_name", ""),
        "manufacturer_address": wb_config.get("manufacturer_address", ""),
        "manufacturer_srn": wb_config.get("manufacturer_srn", ""),
        "authorized_rep": wb_config.get("authorized_rep", ""),
        "ar_srn": wb_config.get("ar_srn", ""),
        "data_collection_period": wb_config.get("data_collection_period", ""),
    }

app = typer.Typer(
    name="psur-generator",
    help="Generate EU MDR-compliant PSURs for medical devices."
)
console = Console()


def _validate_date_range_or_exit(start_date: str, end_date: str) -> None:
    """Abort early when a CLI date range is invalid."""
    try:
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
    except ValueError as ex:
        console.print(
            f"[red]Invalid date format: {ex}. Use YYYY-MM-DD, e.g. "
            "2025-01-01.[/red]"
        )
        raise typer.Exit(1)

    if start_dt > end_dt:
        console.print(
            f"[red]Invalid reporting period: start date {start_date} is after "
            f"end date {end_date}.[/red]\n"
            f"[yellow]Use: --start {end_date} --end {start_date} if those "
            "dates were reversed.[/yellow]"
        )
        raise typer.Exit(1)


@app.command()
def generate(
    device_name: str = typer.Argument("", help="Device name (auto-detected from CER if omitted)"),
    start_date: str = typer.Option(..., "--start", "-s", help="Start date (YYYY-MM-DD)"),
    end_date: str = typer.Option(..., "--end", "-e", help="End date (YYYY-MM-DD)"),
    # Input directory (auto-discovers all files inside)
    input_dir: Path = typer.Option(None, "--input", "-i", help="Input directory (default: data/input/)"),
    # Optional explicit overrides (take priority over auto-discovery)
    sales: Path = typer.Option(None, "--sales", help="Override: Sales data", hidden=True),
    complaints: Path = typer.Option(None, "--complaints", help="Override: Complaints data", hidden=True),
    capa: Path = typer.Option(None, "--capa", help="Override: CAPA data", hidden=True),
    cer: Path = typer.Option(None, "--cer", help="Override: CER", hidden=True),
    ifu: Path = typer.Option(None, "--ifu", help="Override: IFU", hidden=True),
    rmf: Path = typer.Option(None, "--rmf", help="Override: RMF", hidden=True),
    ract: Path = typer.Option(None, "--ract", help="Override: RACT", hidden=True),
    pms_plan: Path = typer.Option(None, "--pms-plan", help="Override: PMS Plan", hidden=True),
    pmcf: Path = typer.Option(None, "--pmcf", help="Override: PMCF", hidden=True),
    fsca: Path = typer.Option(None, "--fsca", help="Override: FSCA", hidden=True),
    external_db: Path = typer.Option(None, "--external-db", help="Override: External DB", hidden=True),
    previous_psur: Path = typer.Option(None, "--previous", help="Override: Previous PSUR", hidden=True),
    extra_files: Optional[List[Path]] = typer.Option(None, "--extra", help="Override: Extra files", hidden=True),
    output_dir: Path = typer.Option(None, "--output", "-o", help="Output directory"),
    resume: bool = typer.Option(False, "--resume", help="Resume from last checkpoint"),
    # Ollama local model support
    ollama_model: Optional[str] = typer.Option(None, "--ollama-model", help="Use an approved local reasoning model for ALL LLM calls: deepseek-r1 or qwq"),
    ollama_url: Optional[str] = typer.Option(None, "--ollama-url", help="Ollama API base URL (default: http://localhost:11434)"),
):
    """Generate a PSUR from files in data/input/.

    Drop your files into data/input/ and run:
        python main.py generate --start 2025-01-01 --end 2025-12-31

    Use --ollama-model to run entirely on an approved local reasoning model:
        python main.py generate --start 2025-01-01 --end 2025-12-31 --ollama-model deepseek-r1

    Device name, classification, and reusability are auto-detected from ALL source
    files (sales, complaints, CAPA, CER, PMS Plan, previous PSUR, RACT, RMF, IFU, etc.).
    You can optionally pass a device name as the first argument to override auto-detection.
    Column mappings are auto-mapped by default.
    """

    _validate_date_range_or_exit(start_date, end_date)

    # ── 0. Activate Ollama override if requested ────────────────────
    if ollama_model:
        from llm_client import set_ollama_override
        set_ollama_override(ollama_model, url=ollama_url)
        console.print(f"[bold green]  Ollama mode:[/bold green] {ollama_model} @ {ollama_url or 'http://localhost:11434'}\n")

    _run_t0 = _time.time()
    surveillance_period = {"start_date": start_date, "end_date": end_date}
    out_dir = Path(output_dir or OUTPUT_DIR)
    in_dir = Path(input_dir or INPUT_DIR)

    console.print(f"\n[bold blue]{'='*56}[/bold blue]")
    console.print(f"[bold blue]  PSUR Generator - FormQAR-054 Rev C[/bold blue]")
    console.print(f"[bold blue]{'='*56}[/bold blue]")
    try:
        from knowledge import get_registry, KNOWLEDGE_VERSION
        _reg = get_registry()
        console.print(
            f"[dim]Knowledge layer v{KNOWLEDGE_VERSION} — "
            f"{len(_reg.all())} rules across {len(_reg.frameworks())} frameworks[/dim]"
        )
    except Exception as _exc:
        console.print(f"[yellow]Knowledge layer unavailable: {_exc}[/yellow]")
    console.print(f"\nPeriod: {start_date} to {end_date}\n")

    # ── 1. Auto-discover input files ────────────────────────────────
    # Phase A: Standardized resolver (handles {NNN}_ prefix, (N) suffix, clean names)
    tier1_resolved = resolve_inputs(in_dir)
    print_input_resolution(tier1_resolved)

    # Phase B: Legacy AI-based discovery (fallback for unusual filenames)
    discovered = auto_discover_inputs(in_dir)
    print_discovered_files(discovered)
    audit_discovered_formats(discovered)

    def _resolve(explicit_path, category):
        """Resolve file path: CLI arg > Tier 1 resolver > legacy discovery."""
        if explicit_path and Path(explicit_path).exists():
            return Path(explicit_path)
        # Tier 1: standardized resolver
        tier1 = tier1_resolved.get(category)
        if tier1 and tier1.exists():
            return tier1
        # Tier 2: legacy discovery
        files = discovered.get(category, [])
        return files[0] if files else None

    input_paths: Dict[str, Optional[Path]] = {
        "sales":        _resolve(sales, "sales"),
        "complaints":   _resolve(complaints, "complaints"),
        "capa":         _resolve(capa, "capa"),
        "cer":          _resolve(cer, "cer"),
        "ifu":          _resolve(ifu, "ifu"),
        "rmf":          _resolve(rmf, "rmf"),
        "ract":         _resolve(ract, "ract"),
        "pms_plan":     _resolve(pms_plan, "pms_plan"),
        "pmcf":         _resolve(pmcf, "pmcf"),
        "literature":   _resolve(None, "literature"),
        "fsca":         _resolve(fsca, "fsca"),
        "external_db":  _resolve(external_db, "external_db"),
        "previous_psur": _resolve(previous_psur, "previous_psur"),
        "device_context": _resolve(None, "device_context"),
        "analysis_workbook": _resolve(None, "analysis_workbook"),
    }
    extra_paths = list(extra_files or []) + discovered.get("extra", [])

    # ── 2. Device context detection ─────────────────────────────────
    # Fast path: if a device_context.json is provided, use it directly
    # Slow path: gather snippets from all files and call LLM

    context_file_rich: Optional[Dict[str, Any]] = None
    context_file_path = input_paths.get("device_context")

    if context_file_path and context_file_path.exists():
        console.print(f"[bold green]Loading device context from {context_file_path.name}...[/bold green]")
        ctx_loaded = load_device_context_file(context_file_path)
        meta = ctx_loaded["meta"]
        context_file_rich = ctx_loaded["rich"]

        _device_name = meta["device_name"]
        if device_name:
            _device_name = device_name
            meta["device_name"] = device_name
        console.print(f"  [green]Device: {_device_name}[/green]")
        console.print(f"  [dim]Classification: {meta['device_class']}[/dim]")
        console.print(f"  [dim]Reusable: {meta['is_reusable']}[/dim]")
        console.print(f"  [dim]Source: device_context.json (no LLM call needed)[/dim]")
        console.print()
    else:
        console.print("[bold]Auto-detecting device context from all input files...[/bold]")
        snippets = gather_file_snippets(input_paths, extra_paths)
        llm_detected = extract_device_context_llm(snippets)
        meta = resolve_device_metadata(llm_detected, device_name, start_date, end_date)

        _device_name = meta["device_name"]
        if not device_name and meta["auto_detected_name"]:
            console.print(f"  [green]Auto-detected device: {_device_name}[/green]")
        elif device_name:
            console.print(f"  [dim]Using CLI device name: {_device_name}[/dim]")

        console.print(f"  [dim]Classification: {meta['device_class']}[/dim]")
        console.print(f"  [dim]Reusable: {meta['is_reusable']}[/dim]")
        console.print(f"  [dim]Denominator: {'procedures (reusable)' if meta['is_reusable'] else 'units distributed (disposable)'}[/dim]")
        if meta["certificate_number"]:
            console.print(f"  [dim]Certificate: {meta['certificate_number']}[/dim]")
        console.print()

    if not _device_name:
        console.print("  [red]Could not auto-detect device name from any input file.[/red]")
        console.print("  [red]Either provide it as an argument or ensure input files contain the product name.[/red]")
        raise typer.Exit(1)

    # ── 3. Checkpoint / resume ──────────────────────────────────────

    safe_name = re.sub(r'[^\w\-]', '_', _device_name).strip('_')
    checkpoint_path = out_dir / f".checkpoint_{safe_name}_{end_date[:4]}.json"

    checkpoint_data = None
    if resume and checkpoint_path.exists():
        with open(checkpoint_path) as f:
            checkpoint_data = json.load(f)
        completed = list(checkpoint_data.get("completed_sections", {}).keys())
        console.print(f"  [green]Resuming from checkpoint — {len(completed)} sections already done[/green]")
        console.print(f"  [dim]Completed: {', '.join(s.split('_')[0] for s in completed)}[/dim]\n")

    # ── 4. Parse all inputs ─────────────────────────────────────────

    # Always parse CER when present — it provides regulatory context (NB, certificates,
    # clinical evidence, literature, PMCF) that device_context.json does not cover.
    _skip_cer = False

    # Resolve in-scope part numbers from input documents so downstream
    # filtering is scoped to THIS PSUR. Sources (in priority):
    # device_context.model_or_catalog_numbers,
    # previous_psur.appendix_a1_models.* (if previous PSUR is JSON).
    _scope_pns: List[str] = []
    _seen_pns: set = set()

    def _add_pns(values):
        if not values:
            return
        if isinstance(values, str):
            values = [values]
        for v in values:
            s = str(v).strip()
            if s and s.upper() not in _seen_pns:
                _seen_pns.add(s.upper())
                _scope_pns.append(s)

    if context_file_rich:
        _add_pns(context_file_rich.get("model_or_catalog_numbers"))
        _add_pns(context_file_rich.get("model_numbers"))
        _add_pns(context_file_rich.get("catalog_numbers"))

    _prev_path = input_paths.get("previous_psur")
    if _prev_path and _prev_path.exists() and _prev_path.suffix.lower() == ".json":
        try:
            with open(_prev_path) as _pf:
                _prev_doc = json.load(_pf)
            _appendix = _prev_doc.get("appendix_a1_models") or {}
            if isinstance(_appendix, dict):
                for _v in _appendix.values():
                    _add_pns(_v)
            elif isinstance(_appendix, list):
                _add_pns(_appendix)
        except Exception as e:
            console.print(f"  [yellow]Could not read previous_psur scope appendix: {e}[/yellow]")

    if _scope_pns:
        console.print(
            f"  [green]In-scope part numbers resolved from inputs: "
            f"{len(_scope_pns)}[/green]"
        )

    parse_result = parse_all_inputs(
        sales_path=input_paths["sales"],
        complaints_path=input_paths["complaints"],
        capa_path=input_paths["capa"],
        cer_path=input_paths["cer"],
        ifu_path=input_paths["ifu"],
        rmf_path=input_paths["rmf"],
        ract_path=input_paths["ract"],
        pms_plan_path=input_paths["pms_plan"],
        pmcf_path=input_paths["pmcf"],
        literature_path=input_paths.get("literature"),
        fsca_path=input_paths["fsca"],
        ext_db_path=input_paths["external_db"],
        prev_psur_path=input_paths["previous_psur"],
        extra_paths=extra_paths,
        start_date=start_date,
        end_date=end_date,
        device_name=_device_name,
        confirm_cb=None,  # auto-map always on
        skip_cer=_skip_cer,
        unified_workbook_path=input_paths.get("analysis_workbook"),
    )
    parsed_data = parse_result["parsed_data"]
    expanded_context = parse_result["expanded_context"]
    previous_stats = parse_result["previous_stats"]
    product_classification = parse_result.get("product_classification", {}) or {}

    # ── 4a. Override cadence from PMS plan if explicitly stated ─────
    # The PMS Plan is the authoritative source for PSUR cadence; it
    # takes precedence over the class-based default in device_context.
    pms_plan_data = parsed_data.get("pms_plan")
    if isinstance(pms_plan_data, dict):
        pms_cadence = pms_plan_data.get("psur_cadence", "").strip().upper()
        if pms_cadence in ("ANNUALLY", "EVERY_TWO_YEARS", "EVERY_THREE_YEARS"):
            if pms_cadence != meta.get("psur_cadence"):
                console.print(
                    f"  [green]PSUR cadence overridden by PMS Plan: "
                    f"{meta.get('psur_cadence')} -> {pms_cadence}[/green]"
                )
                meta["psur_cadence"] = pms_cadence

    # ── 4b. Parse analysis workbook (if provided) ──────────────────
    # A pre-computed workbook supplements raw parsers with curated
    # sales tables, complaint trending, harms cross-tab, and Section D tables.

    wb_path = input_paths.get("analysis_workbook")
    if wb_path and wb_path.exists():
        console.print(f"\n[bold]Parsing analysis workbook: {wb_path.name}...[/bold]")
        from parsers.analysis_workbook import parse_analysis_workbook
        wb_data = parse_analysis_workbook(wb_path)

        if wb_data:
            # -- Merge monthly sales into sales_data ---------------------
            if wb_data.get("monthly_sales"):
                if "sales" not in parsed_data or not parsed_data["sales"]:
                    parsed_data["sales"] = {"total_units": 0, "by_month": {}, "by_region": {}, "by_country": {}, "by_product": {}}
                parsed_data["sales"]["by_month"] = wb_data["monthly_sales"]
                console.print(f"  [green]Monthly sales: {len(wb_data['monthly_sales'])} months from workbook[/green]")

            # -- Merge regional sales ------------------------------------
            if wb_data.get("sales_by_region"):
                if "sales" not in parsed_data or not parsed_data["sales"]:
                    parsed_data["sales"] = {"total_units": 0, "by_month": {}, "by_region": {}, "by_country": {}, "by_product": {}}
                # Build by_region dict from the region rows (sum year columns)
                region_totals = {}
                for row in wb_data["sales_by_region"]:
                    region_name = row.get("region", "")
                    if not region_name or "worldwide" in region_name.lower() or "total" in region_name.lower():
                        continue
                    # Sum all numeric columns (year columns) for total
                    total = sum(v for k, v in row.items() if k not in ("region", "pct_of_total") and isinstance(v, (int, float)))
                    if total > 0:
                        region_totals[region_name] = int(total)
                if region_totals:
                    parsed_data["sales"]["by_region"] = region_totals
                    console.print(f"  [green]Regional sales: {len(region_totals)} regions from workbook[/green]")

            # -- Merge total units ---------------------------------------
            if wb_data.get("total_units"):
                if "sales" not in parsed_data or not parsed_data["sales"]:
                    parsed_data["sales"] = {"total_units": 0, "by_month": {}, "by_region": {}, "by_country": {}, "by_product": {}}
                parsed_data["sales"]["total_units"] = wb_data["total_units"]
                console.print(f"  [green]Total units: {wb_data['total_units']:,} from workbook[/green]")

            # -- Merge monthly complaints into complaints_data -----------
            if wb_data.get("complaint_trending"):
                if "complaints" not in parsed_data or not parsed_data["complaints"]:
                    parsed_data["complaints"] = {"total_complaints": 0, "by_month": {}, "by_imdrf_code": {}, "by_harm_category": {}, "by_region": {}, "harm_by_imdrf": {}, "serious_incidents": []}
                monthly_complaints = {}
                total_from_wb = 0
                for entry in wb_data["complaint_trending"]:
                    m = entry["month"]
                    c = entry["complaints"]
                    monthly_complaints[m] = c
                    total_from_wb += c
                parsed_data["complaints"]["by_month"] = monthly_complaints
                console.print(f"  [green]Complaint trending: {len(monthly_complaints)} months from workbook[/green]")

            # -- Store pre-computed analysis tables for LLM sections ------
            analysis_tables = {}
            if wb_data.get("harms_table"):
                analysis_tables["harms_table"] = wb_data["harms_table"]
                console.print(f"  [green]Harms table (Table 7): {len(wb_data['harms_table'])} rows[/green]")
            if wb_data.get("section_d_table2"):
                analysis_tables["section_d_table2"] = wb_data["section_d_table2"]
                console.print(f"  [green]Section D Table 2: {len(wb_data['section_d_table2'])} rows[/green]")
            if wb_data.get("section_d_table3"):
                analysis_tables["section_d_table3"] = wb_data["section_d_table3"]
                console.print(f"  [green]Section D Table 3: {len(wb_data['section_d_table3'])} rows[/green]")
            if wb_data.get("section_d_table4"):
                analysis_tables["section_d_table4"] = wb_data["section_d_table4"]
                console.print(f"  [green]Section D Table 4: {len(wb_data['section_d_table4'])} rows[/green]")
            if wb_data.get("trend_summary"):
                analysis_tables["trend_summary"] = wb_data["trend_summary"]
                console.print(f"  [green]Trend summary: mean={wb_data['trend_summary']['mean_rate']:.6f}, UCL={wb_data['trend_summary']['ucl']:.6f}[/green]")
            if wb_data.get("sales_by_region"):
                analysis_tables["sales_by_region"] = wb_data["sales_by_region"]

            if analysis_tables:
                parsed_data["analysis_workbook"] = analysis_tables

            # ── External DB search from workbook ───────────────────────
            if wb_data.get("external_db") and not parsed_data.get("external_db"):
                parsed_data["external_db"] = wb_data["external_db"]
                console.print(f"  [green]External DB search: {len(wb_data['external_db'])} databases from workbook[/green]")

            # ── Config sheet → override device metadata ────────────────
            wb_config = wb_data.get("config")
            if wb_config:
                _overrides = []
                if wb_config.get("device_name") and not device_name:
                    meta["device_name"] = wb_config["device_name"]
                    _device_name = wb_config["device_name"]
                    _overrides.append(f"device={_device_name}")
                if wb_config.get("device_class"):
                    meta["device_class"] = wb_config["device_class"]
                    _overrides.append(f"class={wb_config['device_class']}")
                if wb_config.get("psur_cadence"):
                    cad = wb_config["psur_cadence"].lower()
                    if "biennial" in cad or "two" in cad:
                        meta["psur_cadence"] = "EVERY_TWO_YEARS"
                    elif "annual" in cad:
                        meta["psur_cadence"] = "ANNUALLY"
                    _overrides.append(f"cadence={meta.get('psur_cadence')}")
                if wb_config.get("certificate_number"):
                    meta["certificate_number"] = wb_config["certificate_number"]
                # Build rich context from config if not already loaded from file
                if context_file_rich is None:
                    context_file_rich = _build_rich_context_from_config(wb_config)
                    _overrides.append("rich_context=built")
                if _overrides:
                    console.print(f"  [green]Config sheet: {', '.join(_overrides)}[/green]")

            # ── FSCA table from workbook ───────────────────────────────
            if wb_data.get("fsca_table") is not None:
                analysis_tables["fsca_table"] = wb_data["fsca_table"]
                if wb_data.get("fsca_summary"):
                    analysis_tables["fsca_summary"] = wb_data["fsca_summary"]
                console.print(f"  [green]FSCA (Table 8): {len(wb_data['fsca_table'])} rows, summary={wb_data.get('fsca_summary', {})}[/green]")

            # ── CAPA Section I table from workbook ─────────────────────
            if wb_data.get("capa_section_i") is not None:
                analysis_tables["capa_section_i"] = wb_data["capa_section_i"]
                if wb_data.get("capa_summary"):
                    analysis_tables["capa_summary"] = wb_data["capa_summary"]
                console.print(f"  [green]CAPA (Table 9): {len(wb_data['capa_section_i'])} rows, summary={wb_data.get('capa_summary', {})}[/green]")
        else:
            console.print("  [yellow]No data extracted from workbook[/yellow]")

    # ── 5. Compute statistics ───────────────────────────────────────

    console.print("\n[bold]Computing statistics...[/bold]")
    stats = compute_psur_statistics(
        sales_data=parsed_data.get("sales", {}),
        complaints_data=parsed_data.get("complaints", {}),
        surveillance_period=surveillance_period,
        previous_stats=previous_stats,
        is_reusable=meta["is_reusable"],
        ract_data=parsed_data.get("ract") if isinstance(parsed_data.get("ract"), dict) else None,
        product_classification=product_classification,
    )

    console.print(f"  Total {stats.denominator_type}: {stats.total_units_sold:,}")
    console.print(f"  Total complaints: {stats.total_complaints}")
    console.print(f"  Overall rate: {stats.overall_rate_display}")
    console.print(f"  EU/UK serious incidents: {stats.eu_uk_serious_incident_count}")
    console.print(f"  FDA MDR-reportable events: {stats.fda_mdr_count}")
    console.print(f"  Trend status: {stats.trend_analysis.status}")
    if stats.yoy_rate_change is not None:
        console.print(f"  YoY rate change: {stats.yoy_rate_change}%")

    # ── 5b. Build deterministic FormQAR-054 tables DOCX ─────────────
    # Ports the psur-tables / sales-aggregate / imdrf-classify skills into
    # a deterministic Python pipeline step. Produces a tables-only DOCX as
    # a first-class artifact alongside the LLM-generated narrative.
    tables_path = None
    try:
        from build_tables_standalone import build_tables_docx
        from datetime import date as _date
        out_dir.mkdir(parents=True, exist_ok=True)
        safe_name = "".join(c if c.isalnum() else "_" for c in _device_name).strip("_") or "device"
        tables_path = out_dir / f"PSUR_Tables_{safe_name}_{end_date[:4]}.docx"
        cadence = meta.get("psur_cadence") or "annual"
        cadence_label = f"{cadence} cadence, EU Class {meta.get('device_class', 'IIb')}"
        build_tables_docx(
            reporting_start=_date.fromisoformat(start_date),
            reporting_end=_date.fromisoformat(end_date),
            input_dir=in_dir,
            output_path=tables_path,
            device_label=_device_name,
            cadence_label=cadence_label,
        )
        console.print(f"  [green]FormQAR-054 tables DOCX: {tables_path}[/green]")
    except Exception as _e:
        console.print(f"  [yellow]FormQAR-054 tables build skipped: {_e}[/yellow]")
        tables_path = None

    # ── 6. Generate charts (skip if user supplied) ────────────────

    console.print("\n[bold]Preparing charts...[/bold]")
    stats_dict = asdict(stats)
    chart_dir = out_dir / "charts"

    # Check for user-supplied chart PNGs from discovery
    user_sales_chart = discovered.get("chart_sales", [None])[0] if discovered.get("chart_sales") else None
    user_trend_chart = discovered.get("chart_trend", [None])[0] if discovered.get("chart_trend") else None

    chart_paths: Dict[str, Path] = {}
    if user_sales_chart and user_sales_chart.exists():
        chart_paths["sales_trend"] = user_sales_chart
        console.print(f"  [green]sales_trend: using user-supplied {user_sales_chart.name}[/green]")
    if user_trend_chart and user_trend_chart.exists():
        chart_paths["trend_ucl"] = user_trend_chart
        console.print(f"  [green]trend_ucl: using user-supplied {user_trend_chart.name}[/green]")

    # Auto-generate any charts NOT supplied by the user
    if len(chart_paths) < 2:
        _ract_for_charts = parsed_data.get("ract") if isinstance(parsed_data.get("ract"), dict) else None
        auto_charts = generate_all_charts(
            stats_dict, chart_dir, _device_name, ract_data=_ract_for_charts
        )
        for name, path in auto_charts.items():
            if name not in chart_paths:
                chart_paths[name] = path
                console.print(f"  {name}: auto-generated {path.name}")
    else:
        console.print("  [dim]All charts user-supplied — skipping auto-generation[/dim]")

    # ── 7. Build device context ─────────────────────────────────────

    device_context, _device_name = build_device_context(
        device_name=_device_name,
        device_class=meta["device_class"],
        is_reusable=meta["is_reusable"],
        certificate_number=meta["certificate_number"],
        certificate_date=meta["certificate_date"],
        psur_cadence=meta["psur_cadence"],
        infocard_number=meta["infocard_number"],
        denominator_type=stats.denominator_type,
        denominator_description=stats.denominator_description,
        parsed_data=parsed_data,
        expanded_context=expanded_context,
        input_paths=input_paths,
        context_file_rich=context_file_rich,
    )

    # ── 8. Generate PSUR ────────────────────────────────────────────

    from agents.base import reset_token_usage, get_token_usage
    reset_token_usage()
    _gen_t0 = _time.time()

    psur = generate_psur(
        device_context=device_context,
        statistics=stats,
        parsed_data=parsed_data,
        checkpoint_path=checkpoint_path,
        resume_data=checkpoint_data,
    )

    _gen_elapsed = _time.time() - _gen_t0
    _usage = get_token_usage()
    psur["_statistics"] = stats_dict
    apply_psur_table_skills(
        psur,
        stats=stats,
        parsed_data=parsed_data,
        start_date=start_date,
        end_date=end_date,
        device_context=device_context,
    )
    psur = remediate_contradictions_with_llm(
        psur,
        statistics=stats,
        parsed_data=parsed_data,
        device_context=device_context,
        start_date=start_date,
        end_date=end_date,
        console=console,
    )
    apply_psur_table_skills(
        psur,
        stats=stats,
        parsed_data=parsed_data,
        start_date=start_date,
        end_date=end_date,
        device_context=device_context,
    )
    while True:
        contradiction_report = run_full_coherence_audit(
            psur,
            parsed_data=parsed_data,
            device_context=device_context,
        )
        psur["_contradiction_accuracy_audit"] = contradiction_report.to_dict()
        if not contradiction_report.blocking_findings:
            console.print("  [green]Contradictions/accuracy audit: clean[/green]")
            break

        console.print(
            f"  [yellow]Contradictions/accuracy audit: "
            f"{contradiction_report.blocking_findings} blocking finding(s)[/yellow]"
        )
        unresolved = "\n".join(
            f"- {f.finding_id} [{f.severity}] {f.section}: {f.title}"
            for f in contradiction_report.findings
            if f.severity in {"CRITICAL", "MAJOR"}
        )

        if _all_blockers_are_near_pass_coherence(contradiction_report):
            choice = _prompt_near_pass_action(console, contradiction_report)
            if choice == "1":
                _annotate_unresolved_coherence_issues(psur, contradiction_report)
                psur["_contradiction_accuracy_audit"] = contradiction_report.to_dict()
                console.print(
                    "  [yellow]Proceeding with generation; unresolved coherence "
                    "finding(s) have been highlighted in Section M and the audit JSON.[/yellow]"
                )
                break
            if choice == "3":
                console.print("  [yellow]Continuing contradiction remediation loop...[/yellow]")
                psur = remediate_contradictions_with_llm(
                    psur,
                    statistics=stats,
                    parsed_data=parsed_data,
                    device_context=device_context,
                    start_date=start_date,
                    end_date=end_date,
                    max_iterations=2,
                    console=console,
                )
                apply_psur_table_skills(
                    psur,
                    stats=stats,
                    parsed_data=parsed_data,
                    start_date=start_date,
                    end_date=end_date,
                    device_context=device_context,
                )
                continue

        console.print(
            "[red]Contradictions/accuracy remediation did not clear all blocking findings:[/red]\n"
            f"{unresolved}"
        )
        raise typer.Exit(code=1)

    # ── 9. Validate ─────────────────────────────────────────────────

    console.print("\n[bold]Validating...[/bold]")
    validation_engine = ValidationEngine()
    validation_report = validation_engine.run(
        psur,
        parsed_data=parsed_data,
        device_context=device_context,
        statistics=stats_dict,
    )
    psur["_validation_engine"] = validation_report
    pv = validation_report["PSUR_VALIDATION"]
    engine_ready = bool(pv["READY"])
    if engine_ready:
        console.print(
            f"  [green]Validation engine READY[/green] "
            f"(score {pv['SCORE']}/100, passed {pv['PASSED']}/{pv['CHECKS_RUN']})"
        )
    else:
        console.print(
            f"  [red]Validation engine NOT READY[/red] "
            f"(score {pv['SCORE']}/100, failed {pv['FAILED']}/{pv['CHECKS_RUN']})"
        )
        for sev_key, colour in (("CRITICAL_ERRORS", "red"), ("MAJOR_ERRORS", "yellow"), ("MINOR_ERRORS", "cyan")):
            messages = pv.get(sev_key) or []
            if not messages:
                continue
            console.print(f"  [{colour}]{sev_key.replace('_', ' ').title()} ({len(messages)})[/{colour}]")
            for msg in _format_engine_messages(messages):
                console.print(f"    - {msg}")

    # Legacy validator is retained only for advisory traceability and DOCX checks.
    validator = PSURValidator()

    # ── 10. Save outputs ────────────────────────────────────────────

    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / f"PSUR_{safe_name}_{end_date[:4]}.json"
    with open(json_path, "w") as f:
        json.dump(psur, f, indent=2, default=str)
    console.print(f"\n  JSON: {json_path}")

    engine_path = out_dir / f"PSUR_{safe_name}_{end_date[:4]}_validation_engine.json"
    with open(engine_path, "w") as f:
        json.dump(validation_report, f, indent=2, default=str)
    console.print(f"  Validation Engine: {engine_path}")

    contradiction_path = out_dir / f"PSUR_{safe_name}_{end_date[:4]}_contradiction_accuracy_audit.json"
    with open(contradiction_path, "w") as f:
        json.dump(psur.get("_contradiction_accuracy_audit", {}), f, indent=2, default=str)
    console.print(f"  Contradictions/Accuracy Audit: {contradiction_path}")

    docx_path = out_dir / f"PSUR_{safe_name}_{end_date[:4]}.docx"
    renderer = PSURTemplateRenderer()
    renderer.render(psur, docx_path, chart_paths=chart_paths)
    console.print(f"  DOCX (template-based): {docx_path}")

    is_valid_docx, docx_errors = validator.validate_docx(docx_path)
    if is_valid_docx:
        console.print("  [green]DOCX structural checks passed[/green]")
    else:
        console.print(f"  [yellow]DOCX structural issues: {len(docx_errors)}[/yellow]")
        for err in _format_engine_messages(docx_errors):
            console.print(f"    - {err}")

    stats_path = out_dir / f"PSUR_{safe_name}_{end_date[:4]}_statistics.json"
    with open(stats_path, "w") as f:
        json.dump(stats_dict, f, indent=2, default=str)
    console.print(f"  Stats: {stats_path}")

    # Traceability matrix — every narrative sentence → data source
    _trace_errors, trace_matrix = validator._check_traceability(
        psur, parsed_data=parsed_data, device_context=device_context,
    )
    if trace_matrix:
        trace_path = out_dir / f"PSUR_{safe_name}_{end_date[:4]}_traceability.json"
        with open(trace_path, "w") as f:
            json.dump(trace_matrix, f, indent=2, default=str)
        leak_count = trace_matrix.get("summary", {}).get("total_leakage_findings", 0)
        if leak_count:
            console.print(f"  [yellow]Traceability: {trace_path} ({leak_count} leakage findings)[/yellow]")
        else:
            console.print(f"  [green]Traceability: {trace_path} (clean)[/green]")

    if checkpoint_path.exists():
        checkpoint_path.unlink()
        console.print(f"  [dim]Checkpoint cleaned up[/dim]")

    # ── 11. Performance summary ─────────────────────────────────────

    _total_elapsed = _time.time() - _run_t0
    print_performance_summary(
        total_elapsed=_total_elapsed,
        gen_elapsed=_gen_elapsed,
        token_usage=_usage,
    )

    console.print(f"\n[bold green]PSUR generation complete![/bold green]\n")
    if not engine_ready or not is_valid_docx:
        raise typer.Exit(code=1)

@app.command()
def validate(
    psur_json: Path = typer.Argument(..., help="Path to PSUR JSON file"),
    docx: Path = typer.Option(None, "--docx", help="Optional DOCX path to validate rendered structure/tables"),
    input_dir: Path = typer.Option(Path("data/input"), "--input", "-i", help="Input directory used to source parsed_data context (suppresses false-positive fabrication flags when external_db / literature / etc. are present)."),
):
    """Validate an existing PSUR JSON file."""
    console.print(f"\nValidating: {psur_json}\n")
    with open(psur_json) as f:
        psur = json.load(f)

    # Best-effort load of parsed_data so context-aware checks (external_db,
    # literature, RACT) don't report false positives. Falls back to {} if
    # the input directory is missing.
    parsed_data: Dict[str, Any] = {}
    device_context: Dict[str, Any] = {}
    try:
        if input_dir and input_dir.exists():
            discovered = auto_discover_inputs(input_dir)

            def _first(cat: str) -> Optional[Path]:
                files = discovered.get(cat, [])
                return files[0] if files else None

            for key in ("external_db", "literature", "ract", "capa",
                        "complaints", "fsca", "previous_psur"):
                p = _first(key)
                if p and p.exists():
                    try:
                        if p.suffix.lower() == ".json":
                            parsed_data[key] = json.loads(p.read_text(encoding="utf-8"))
                        else:
                            # Non-JSON sources still mark "data was provided"
                            parsed_data[key] = {"_source_path": str(p)}
                    except Exception:
                        parsed_data[key] = {"_source_path": str(p)}

            dc_path = _first("device_context")
            if dc_path and dc_path.exists() and dc_path.suffix.lower() == ".json":
                try:
                    device_context = json.loads(dc_path.read_text(encoding="utf-8"))
                except Exception:
                    pass
    except Exception as ex:
        console.print(f"[yellow]Could not load parsed_data context for validation: {ex}[/yellow]")

    engine = ValidationEngine()
    validation_report = engine.run(
        psur,
        parsed_data=parsed_data,
        device_context=device_context,
        statistics=psur.get("_statistics") or {},
    )
    pv = validation_report["PSUR_VALIDATION"]
    console.print(
        f"[bold {'green' if pv['READY'] else 'red'}]READY = {pv['READY']}  "
        f"SCORE = {pv['SCORE']}/100[/bold {'green' if pv['READY'] else 'red'}]"
    )
    console.print(f"  checks_run={pv['CHECKS_RUN']}  passed={pv['PASSED']}  failed={pv['FAILED']}")
    for sev_key, colour in (("CRITICAL_ERRORS", "red"), ("MAJOR_ERRORS", "yellow"), ("MINOR_ERRORS", "cyan")):
        messages = pv.get(sev_key) or []
        if not messages:
            continue
        console.print(f"\n[{colour}]{sev_key.replace('_', ' ').title()} ({len(messages)}):[/{colour}]")
        for msg in messages:
            console.print(f"  - {msg}")

    validator = PSURValidator()

    # Optional DOCX validation (post-render fidelity checks)
    docx_path = docx
    if docx_path is None:
        inferred = psur_json.with_suffix(".docx")
        if inferred.exists():
            docx_path = inferred

    if docx_path:
        console.print(f"Checking rendered DOCX: {docx_path}")
        is_valid_docx, docx_errors = validator.validate_docx(docx_path)
        if docx_errors:
            for e in docx_errors:
                console.print(f"  - {e}")
    else:
        is_valid_docx = True

    is_valid = bool(pv["READY"]) and is_valid_docx
    if is_valid:
        console.print("[green]All release-gate validation checks passed[/green]")
    else:
        if not is_valid_docx:
            console.print("[red]DOCX structural validation failed[/red]")
    return 0 if is_valid else 1


@app.command()
def render(
    psur_json: Path = typer.Argument(..., help="Path to PSUR JSON file"),
    output: Path = typer.Option(None, "--output", "-o", help="Output DOCX path"),
):
    """Render a PSUR JSON to DOCX."""
    console.print(f"\nRendering: {psur_json}\n")
    with open(psur_json) as f:
        psur = json.load(f)
    if output is None:
        output = psur_json.with_suffix(".docx")
    renderer = PSURTemplateRenderer()
    renderer.render(psur, output)
    console.print(f"[green]DOCX saved (template-based): {output}[/green]")


@app.command()
def harness(
    device_name: str = typer.Argument("", help="Device name (auto-detected from device_context.json if omitted)"),
    start_date: str = typer.Option(..., "--start", "-s", help="Reporting period start (YYYY-MM-DD)"),
    end_date: str = typer.Option(..., "--end", "-e", help="Reporting period end (YYYY-MM-DD)"),
    input_dir: Path = typer.Option(None, "--input", "-i", help="Input directory (default: data/input/)"),
    output_dir: Path = typer.Option(None, "--output", "-o", help="Output directory (default: data/output/)"),
    is_first_psur: bool = typer.Option(False, "--first-psur", help="Skip the previous_psur mandatory check"),
    resume: bool = typer.Option(False, "--resume", help="Resume from last checkpoint"),
    ollama_model: Optional[str] = typer.Option(None, "--ollama-model", help="Use a local Ollama model for ALL LLM calls"),
    ollama_url: Optional[str] = typer.Option(None, "--ollama-url", help="Ollama API base URL"),
):
    """Run the Smarticus PSUR Harness v3 (urn:coopersurgical:smarticus:psur-harness:v3).

    Eight-stage pipeline with 11 agents:
      1. regulatory_classifier
      2. data_ingestion
      3. imdrf_classifier
      4. statistical_engine + risk_assessor   (parallel)
      5. chart_generator + narrative_writer + table_generator   (parallel)
      6. benefit_risk_synthesizer
      7. docx_renderer
      8. validation

        python main.py harness --start 2025-05-01 --end 2026-04-30
    """
    _validate_date_range_or_exit(start_date, end_date)

    if ollama_model:
        from llm_client import set_ollama_override
        set_ollama_override(ollama_model, url=ollama_url)
        console.print(
            f"[bold green]Ollama mode:[/bold green] {ollama_model} @ "
            f"{ollama_url or 'http://localhost:11434'}\n"
        )

    from pipeline.harness import run_harness
    in_dir = Path(input_dir or INPUT_DIR)
    out_dir = Path(output_dir or OUTPUT_DIR)
    try:
        result = run_harness(
            start_date=start_date,
            end_date=end_date,
            input_dir=in_dir,
            output_dir=out_dir,
            device_name=device_name,
            is_first_psur=is_first_psur,
            resume=resume,
        )
    except RuntimeError as ex:
        console.print(f"\n[red]Harness aborted: {ex}[/red]")
        raise typer.Exit(1)

    err_count = sum(1 for it in result.issues if it.severity == "ERROR")
    if err_count:
        console.print(f"\n[red]Harness completed with {err_count} ERROR-level issues[/red]")
        raise typer.Exit(2)


@app.command()
def audit(
    psur_docx: Path = typer.Argument(..., help="Path to PSUR .docx file to audit"),
    uk_mdr: bool = typer.Option(True, "--uk-mdr/--no-uk-mdr", help="UK MDR requirements (enabled by default)"),
    no_llm: bool = typer.Option(False, "--no-llm", help="Keyword-only mode (skip LLM calls)"),
    output: Path = typer.Option(None, "--output", "-o", help="Save JSON audit report to this path"),
    ollama_model: Optional[str] = typer.Option(None, "--ollama-model", help="Use local Ollama model"),
    ollama_url: Optional[str] = typer.Option(None, "--ollama-url", help="Ollama API base URL"),
):
    """Audit a PSUR .docx against MDCG 2022-21 and UK MDR requirements.

    Both EU MDR (MDCG 2022-21) and UK MDR requirements are always applied by
    default. Use --no-uk-mdr to disable UK MDR checks. Uses a two-pass
    architecture: fast keyword pre-screen followed by LLM deep-analysis for
    any PARTIAL or GAP findings. Produces a scored compliance report.

        python main.py audit path/to/PSUR.docx
        python main.py audit path/to/PSUR.docx --no-uk-mdr --no-llm
    """
    from psur_auditor import run_audit, print_audit_report

    if ollama_model:
        from llm_client import set_ollama_override
        set_ollama_override(ollama_model, url=ollama_url)

    if not psur_docx.exists():
        console.print(f"[red]File not found: {psur_docx}[/red]")
        raise typer.Exit(1)

    report = run_audit(
        psur_docx,
        include_uk=uk_mdr,
        use_llm=not no_llm,
        verbose=True,
    )

    print_audit_report(report)

    if output:
        from dataclasses import asdict as _asdict
        with open(output, "w") as f:
            json.dump(_asdict(report), f, indent=2, default=str)
        console.print(f"\n[green]Report saved: {output}[/green]")

    if report.compliance_score < 80:
        raise typer.Exit(1)


# ────────────────────────────────────────────────────────────────────
# Knowledge-base inspection commands
# ────────────────────────────────────────────────────────────────────

kb_app = typer.Typer(help="Inspect and query the regulatory knowledge base.")
app.add_typer(kb_app, name="kb")


@kb_app.command("stats")
def kb_stats():
    """Show registry statistics: rule count, frameworks, loaded files."""
    from knowledge import get_registry
    reg = get_registry()
    stats = reg.stats()
    console.print(f"[bold]Knowledge base version:[/bold] {stats['version']}")
    console.print(f"[bold]Total rules:[/bold] {stats['total_rules']}")
    console.print("[bold]Frameworks:[/bold]")
    for fw, n in sorted(stats["frameworks"].items()):
        console.print(f"  {fw}: {n}")
    console.print("[bold]Files loaded:[/bold]")
    for f in stats["files"]:
        console.print(f"  {f}")

    from knowledge import get_skill_registry
    sk_reg = get_skill_registry()
    console.print(f"\n[bold]Skills:[/bold] {len(sk_reg.all())}")
    for sk in sk_reg.all():
        console.print(f"  {sk.name} v{sk.version} — {sk.description[:80]}")


@kb_app.command("list")
def kb_list(
    framework: Optional[str] = typer.Option(None, "--framework", "-f"),
    section: Optional[str] = typer.Option(None, "--section", "-s",
                                          help="Section letter (A-M) or full key"),
):
    """List rules, optionally filtered by framework or section."""
    from knowledge import get_registry
    reg = get_registry()
    rules = reg.all()
    if framework:
        rules = [r for r in rules if r.framework.upper() == framework.upper()]
    if section:
        rules = reg.by_section(section)
        if framework:
            rules = [r for r in rules if r.framework.upper() == framework.upper()]
    for r in rules:
        sections = ",".join(r.applies_to.sections) or "*"
        console.print(
            f"  [{r.criticality:8}] {r.id:50} ({r.framework}, sec={sections})"
        )
    console.print(f"\n[dim]{len(rules)} rule(s) shown[/dim]")


@kb_app.command("show")
def kb_show(rule_id: str = typer.Argument(...)):
    """Print the full text of a rule by ID."""
    from knowledge import get_registry
    reg = get_registry()
    r = reg.by_id(rule_id)
    if not r:
        console.print(f"[red]No rule with id: {rule_id}[/red]")
        raise typer.Exit(1)
    console.print(f"[bold]{r.id}[/bold]")
    console.print(f"  Framework:   {r.framework}")
    console.print(f"  Citation:    {r.citation}")
    console.print(f"  Version:     {r.version}")
    console.print(f"  Criticality: {r.criticality}")
    console.print(f"  Applies to:  sections={r.applies_to.sections} "
                  f"classes={r.applies_to.device_classes} "
                  f"markets={r.applies_to.markets}")
    if r.triggers.when:
        console.print(f"  Trigger:     {r.triggers.when}")
    if r.triggers.findings:
        console.print(f"  Findings:    {r.triggers.findings}")
    console.print(f"  Validator:   {r.validator_check or '(none)'}")
    console.print(f"  Source hash: {r.source_hash}")
    console.print(f"\n[bold]Obligation[/bold]\n  {r.obligation}")
    console.print(f"\n[bold]Agent instruction[/bold]\n  {r.agent_instruction}")


@kb_app.command("retrieve")
def kb_retrieve(
    section: str = typer.Option(..., "--section", "-s"),
    device_class: Optional[str] = typer.Option(None, "--class"),
    uk: bool = typer.Option(False, "--uk", help="UK market detected"),
    findings: List[str] = typer.Option([], "--finding", "-F",
                                       help="Symbolic finding flag, repeatable"),
    max_rules: int = typer.Option(15, "--max"),
):
    """Test the precision retrieval API.

    Example: python main.py kb retrieve -s F -c IIa --uk -F has_serious_incidents
    """
    from knowledge import get_registry, Query, retrieve as kb_retrieve_fn
    reg = get_registry()
    fmap = {f: True for f in findings}
    if uk:
        fmap["uk_market_detected"] = True
    q = Query(
        section=section,
        device_class_eu=device_class,
        markets={"UK", "EU"} if uk else {"EU"},
        findings=fmap,
        max_rules=max_rules,
    )
    scored = kb_retrieve_fn(q, reg)
    for sr in scored:
        console.print(
            f"  [{sr.score:5.2f}] {sr.rule.id:48} ({sr.rule.framework}) "
            f"[dim]{','.join(sr.reasons)}[/dim]"
        )
    console.print(f"\n[dim]{len(scored)} rule(s) retrieved[/dim]")


@kb_app.command("audit")
def kb_audit():
    """Audit drift between KB rules' validator_check field and the check registry.

    Exit code 1 if any KB rule references a check key that has no
    implementation, ensuring CI catches drift.
    """
    from validation import audit_rule_check_drift
    missing, orphans = audit_rule_check_drift()
    ok = True
    if missing:
        ok = False
        console.print("[red]Missing check implementations (declared by rules but not in RULE_CHECKS):[/red]")
        for k in missing:
            console.print(f"  - {k}")
    if orphans:
        console.print("[yellow]Orphan check keys (in RULE_CHECKS but no rule references them):[/yellow]")
        for k in orphans:
            console.print(f"  - {k}")
    if ok and not orphans:
        console.print("[green]No drift: every KB rule check resolves to an implementation.[/green]")
    if not ok:
        raise typer.Exit(1)


@app.command("validate-engine")
def validate_engine(
    psur_json: Path = typer.Argument(..., help="Path to PSUR JSON"),
    output: Optional[Path] = typer.Option(None, "--output", "-o",
                                          help="Save full validation report as JSON"),
    fail_on: str = typer.Option("MAJOR", "--fail-on",
                                help="Exit non-zero when this severity (or worse) fails: CRITICAL|MAJOR|MINOR"),
):
    """Run the declarative validation engine (Modules A-G + Semantic).

    Emits the PSUR_VALIDATION JSON block defined by the engine spec.
    """
    from validation import ValidationEngine
    if not psur_json.exists():
        console.print(f"[red]File not found: {psur_json}[/red]")
        raise typer.Exit(1)
    with open(psur_json, "r", encoding="utf-8") as f:
        psur = json.load(f)
    engine = ValidationEngine()
    report = engine.run(
        psur,
        parsed_data=psur.get("_parsed_data") or {},
        device_context=psur.get("_device_context") or {},
        statistics=psur.get("_statistics") or {},
    )
    pv = report["PSUR_VALIDATION"]
    colour = "green" if pv["READY"] else "red"
    console.print(f"[bold {colour}]READY = {pv['READY']}  SCORE = {pv['SCORE']}/100[/bold {colour}]")
    console.print(f"  checks_run={pv['CHECKS_RUN']}  passed={pv['PASSED']}  failed={pv['FAILED']}")
    for sev, key in (("CRITICAL", "CRITICAL_ERRORS"), ("MAJOR", "MAJOR_ERRORS"), ("MINOR", "MINOR_ERRORS")):
        items = pv[key]
        if items:
            colour = {"CRITICAL": "red", "MAJOR": "yellow", "MINOR": "cyan"}[sev]
            console.print(f"\n[{colour}]{sev} ({len(items)}):[/{colour}]")
            for m in items:
                console.print(f"  - {m}")
    if output:
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        console.print(f"\n[dim]Full report written to {output}[/dim]")

    fail_levels = {"CRITICAL": {"CRITICAL"},
                   "MAJOR": {"CRITICAL", "MAJOR"},
                   "MINOR": {"CRITICAL", "MAJOR", "MINOR"}}
    threshold = fail_levels.get(fail_on.upper(), {"CRITICAL", "MAJOR"})
    failed = any(
        r["severity"] in threshold and not r["passed"]
        for r in pv["DETAIL"]
    )
    if failed:
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
