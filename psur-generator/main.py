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
import sqlite3
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

from config import INPUT_DIR, OUTPUT_DIR, PSUR_DB_PATH
from statistics import compute_psur_statistics
from agents.orchestrator import generate_psur
from validation import PSURValidator
from rendering import PSURTemplateRenderer
from charts import generate_all_charts

# Pipeline modules
from pipeline.discovery import auto_discover_inputs, print_discovered_files
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
    # SQLite source for complaints + sales (overrides CSV/Excel files)
    db: Optional[Path] = typer.Option(None, "--db", help="SQLite DB with complaints + sales tables (default: config.PSUR_DB_PATH)"),
    no_db: bool = typer.Option(False, "--no-db", help="Disable SQLite source even if PSUR_DB_PATH is set"),
    td_id: Optional[str] = typer.Option(None, "--td-id", help="Filter SQLite rows by td_id (auto-resolved from device name if omitted)"),
    # Ollama local model support
    ollama_model: Optional[str] = typer.Option(None, "--ollama-model", help="Use a local Ollama model for ALL LLM calls (e.g. qwen3:32b, deepseek-r1:70b)"),
    ollama_url: Optional[str] = typer.Option(None, "--ollama-url", help="Ollama API base URL (default: http://localhost:11434)"),
):
    """Generate a PSUR from files in data/input/.

    Drop your files into data/input/ and run:
        python main.py generate --start 2025-01-01 --end 2025-12-31

    Use --ollama-model to run entirely on a local model:
        python main.py generate --start 2025-01-01 --end 2025-12-31 --ollama-model qwen3:32b

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
    console.print(f"\nPeriod: {start_date} to {end_date}\n")

    # ── 1. Auto-discover input files ────────────────────────────────

    discovered = auto_discover_inputs(in_dir)
    print_discovered_files(discovered)

    def _resolve(explicit_path, category):
        if explicit_path and Path(explicit_path).exists():
            return Path(explicit_path)
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

    # Determine whether CER parsing can be skipped
    # (device_context.json provides the fields CER would extract)
    _skip_cer = bool(
        context_file_rich
        and context_file_rich.get("device_description")
    )

    # Resolve SQLite source: explicit --db > config.PSUR_DB_PATH > none.
    # The DB is validated with PRAGMA integrity_check; a malformed file
    # transparently falls back to CSV/Excel inputs in data/input/.
    _db_resolved: Optional[Path] = None
    if not no_db:
        _candidate = db or (Path(PSUR_DB_PATH) if PSUR_DB_PATH else None)
        if _candidate and Path(_candidate).exists():
            try:
                _con = sqlite3.connect(f"file:{Path(_candidate)}?mode=ro", uri=True)
                _con.execute("PRAGMA quick_check").fetchone()
                _row = _con.execute("PRAGMA integrity_check(1)").fetchone()
                _con.close()
                if not _row or str(_row[0]).strip().lower() != "ok":
                    raise sqlite3.DatabaseError(f"integrity_check returned {_row}")
                _db_resolved = Path(_candidate)
                console.print(f"[cyan]SQLite source enabled:[/cyan] {_db_resolved}")
            except (sqlite3.DatabaseError, sqlite3.OperationalError) as _db_err:
                console.print(
                    f"  [yellow]SQLite source unusable ({_db_err}) — "
                    f"falling back to file inputs in {in_dir}[/yellow]"
                )
                _db_resolved = None
        elif _candidate:
            console.print(f"  [yellow]SQLite path set but not found: {_candidate} — falling back to file inputs[/yellow]")

    # Build in-scope part-number list from input documents so DB queries
    # are scoped to THIS PSUR instead of returning every row under td_id.
    # Sources (in priority): device_context.model_or_catalog_numbers,
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
        db_path=_db_resolved,
        db_td_id=td_id,
        scope_product_numbers=_scope_pns or None,
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
    console.print(f"  Serious incidents: {stats.serious_incident_count} ({stats.serious_incident_rate_display})")
    console.print(f"  Trend status: {stats.trend_analysis.status}")
    if stats.yoy_rate_change is not None:
        console.print(f"  YoY rate change: {stats.yoy_rate_change}%")

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

    # ── 9. Validate ─────────────────────────────────────────────────

    console.print("\n[bold]Validating...[/bold]")
    validator = PSURValidator()
    is_valid, errors = validator.validate(psur, parsed_data=parsed_data, device_context=device_context)

    if is_valid:
        console.print("  [green]All validation checks passed[/green]")
    else:
        console.print(f"  [yellow]{len(errors)} issues found:[/yellow]")
        for e in errors[:10]:
            console.print(f"    - {e}")
        if len(errors) > 10:
            console.print(f"    ... and {len(errors) - 10} more")

    # ── 10. Save outputs ────────────────────────────────────────────

    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / f"PSUR_{safe_name}_{end_date[:4]}.json"
    with open(json_path, "w") as f:
        json.dump(psur, f, indent=2, default=str)
    console.print(f"\n  JSON: {json_path}")

    docx_path = out_dir / f"PSUR_{safe_name}_{end_date[:4]}.docx"
    renderer = PSURTemplateRenderer()
    renderer.render(psur, docx_path, chart_paths=chart_paths)
    console.print(f"  DOCX (template-based): {docx_path}")

    stats_path = out_dir / f"PSUR_{safe_name}_{end_date[:4]}_statistics.json"
    with open(stats_path, "w") as f:
        json.dump(stats_dict, f, indent=2, default=str)
    console.print(f"  Stats: {stats_path}")

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

    validator = PSURValidator()
    is_valid_json, errors = validator.validate(psur, parsed_data=parsed_data, device_context=device_context)

    # Optional DOCX validation (post-render fidelity checks)
    docx_path = docx
    if docx_path is None:
        inferred = psur_json.with_suffix(".docx")
        if inferred.exists():
            docx_path = inferred

    if docx_path:
        console.print(f"Checking rendered DOCX: {docx_path}")
        is_valid_docx, docx_errors = validator.validate_docx(docx_path)
        errors.extend(docx_errors)
    else:
        is_valid_docx = True

    is_valid = is_valid_json and is_valid_docx and len(errors) == 0
    if is_valid:
        console.print("[green]All validation checks passed[/green]")
    else:
        console.print(f"[red]{len(errors)} validation errors:[/red]")
        for e in errors:
            console.print(f"  - {e}")
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
    db: Optional[Path] = typer.Option(None, "--db", help="SQLite DB with complaints + sales tables"),
    no_db: bool = typer.Option(False, "--no-db", help="Disable SQLite source even if PSUR_DB_PATH is set"),
    td_id: Optional[str] = typer.Option(None, "--td-id", help="Filter SQLite rows by td_id"),
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
            db_path=db,
            db_td_id=td_id,
            no_db=no_db,
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


if __name__ == "__main__":
    app()
