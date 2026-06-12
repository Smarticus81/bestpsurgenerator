"""Programmatic PSUR generation pipeline.

This module is the single code path for full PSUR generation. The CLI
(``python main.py generate``) and the FastAPI service both call
``run_generation``; the CLI passes a ``NoopEmitter`` so its behaviour is
unchanged, while the server passes a ``QueueEmitter`` to stream progress
and decision events.

The deterministic-first statistics design is untouched: every number is
pre-computed in ``statistics.py`` and handed to the LLM agents as fact.
"""
import json
import re
import time as _time
import logging
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from rich.console import Console

from config import INPUT_DIR, OUTPUT_DIR
from events import ProgressEmitter, NoopEmitter
from statistics import compute_psur_statistics
from agents.orchestrator import generate_psur
from validation import PSURValidator
from rendering import PSURTemplateRenderer
from charts import generate_all_charts

from pipeline.discovery import auto_discover_inputs, print_discovered_files
from pipeline.format_contract import audit_discovered_formats
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
console = Console()


class PipelineError(RuntimeError):
    """Raised when the generation pipeline cannot proceed."""


def _build_rich_context_from_config(wb_config: Dict[str, Any]) -> Dict[str, Any]:
    """Build a 'rich' device context dict from workbook Config sheet data."""
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
        "manufacturer_name": wb_config.get("manufacturer_name", ""),
        "manufacturer_address": wb_config.get("manufacturer_address", ""),
        "manufacturer_srn": wb_config.get("manufacturer_srn", ""),
        "authorized_rep": wb_config.get("authorized_rep", ""),
        "ar_srn": wb_config.get("ar_srn", ""),
        "data_collection_period": wb_config.get("data_collection_period", ""),
    }


def _emit_cadence_decision(emitter: ProgressEmitter, meta: Dict[str, Any]) -> str:
    """Emit the PSUR-vs-PMSR cadence decision. Returns the report type."""
    device_class = (meta.get("device_class") or "CLASS_IIB").upper()
    cadence = meta.get("psur_cadence", "ANNUALLY")
    if device_class == "CLASS_I":
        report_type = "PMSR"
        reason = (
            "Device is EU/UK Class I: UK MDR 2024 requires a Post-Market "
            "Surveillance Report (PMSR) on a 3-year cycle instead of a PSUR "
            "(Reg 44ZL); EU MDR Article 85/86 applies the equivalent split."
        )
        basis = ["UK MDR 2024 Reg 44ZL", "EU MDR Art. 86"]
    else:
        report_type = "PSUR"
        if device_class in ("CLASS_IIB", "CLASS_III"):
            reason = (
                f"Device is {device_class.replace('_', ' ').title()}: a PSUR is "
                "required at least annually (UK MDR 2024 Reg 44ZM(6); "
                "EU MDR Art. 86)."
            )
        else:
            reason = (
                f"Device is {device_class.replace('_', ' ').title()}: a PSUR is "
                "required at least every two years (UK MDR 2024 Reg 44ZM(7)-(8); "
                "EU MDR Art. 86)."
            )
        basis = ["UK MDR 2024 Reg 44ZM", "EU MDR Art. 86"]
    emitter.decision(
        "psur_vs_pmsr_cadence",
        inputs_summary={"device_class": device_class, "psur_cadence": cadence},
        output={"report_type": report_type, "cadence": cadence},
        reason=reason,
        regulatory_basis=basis,
    )
    return report_type


def run_generation(
    *,
    start_date: str,
    end_date: str,
    device_name: str = "",
    input_dir: Optional[Path] = None,
    output_dir: Optional[Path] = None,
    resume: bool = False,
    # Optional explicit per-input overrides (take priority over discovery)
    sales: Optional[Path] = None,
    complaints: Optional[Path] = None,
    capa: Optional[Path] = None,
    cer: Optional[Path] = None,
    ifu: Optional[Path] = None,
    rmf: Optional[Path] = None,
    ract: Optional[Path] = None,
    pms_plan: Optional[Path] = None,
    pmcf: Optional[Path] = None,
    fsca: Optional[Path] = None,
    external_db: Optional[Path] = None,
    previous_psur: Optional[Path] = None,
    literature: Optional[Path] = None,
    extra_files: Optional[List[Path]] = None,
    emitter: Optional[ProgressEmitter] = None,
) -> Dict[str, Any]:
    """Run the full PSUR generation pipeline. Returns artifact paths + status.

    Raises PipelineError when the run cannot proceed (e.g. no device name).
    """
    emitter = emitter or NoopEmitter()
    try:
        return _run_generation_inner(
            start_date=start_date, end_date=end_date, device_name=device_name,
            input_dir=input_dir, output_dir=output_dir, resume=resume,
            sales=sales, complaints=complaints, capa=capa, cer=cer, ifu=ifu,
            rmf=rmf, ract=ract, pms_plan=pms_plan, pmcf=pmcf, fsca=fsca,
            external_db=external_db, previous_psur=previous_psur,
            literature=literature, extra_files=extra_files, emitter=emitter,
        )
    except Exception as ex:
        emitter.error(str(ex))
        raise


def _run_generation_inner(
    *,
    start_date: str,
    end_date: str,
    device_name: str,
    input_dir: Optional[Path],
    output_dir: Optional[Path],
    resume: bool,
    sales: Optional[Path],
    complaints: Optional[Path],
    capa: Optional[Path],
    cer: Optional[Path],
    ifu: Optional[Path],
    rmf: Optional[Path],
    ract: Optional[Path],
    pms_plan: Optional[Path],
    pmcf: Optional[Path],
    fsca: Optional[Path],
    external_db: Optional[Path],
    previous_psur: Optional[Path],
    literature: Optional[Path],
    extra_files: Optional[List[Path]],
    emitter: ProgressEmitter,
) -> Dict[str, Any]:
    _run_t0 = _time.time()
    surveillance_period = {"start_date": start_date, "end_date": end_date}
    out_dir = Path(output_dir or OUTPUT_DIR)
    in_dir = Path(input_dir or INPUT_DIR)

    console.print(f"\n[bold blue]{'='*56}[/bold blue]")
    console.print(f"[bold blue]  PSUR Generator - RG-PSUR-001[/bold blue]")
    console.print(f"[bold blue]{'='*56}[/bold blue]")
    console.print(f"\nPeriod: {start_date} to {end_date}\n")

    # ── 1. Auto-discover input files ────────────────────────────────
    emitter.phase_started("discovery", detail=str(in_dir))
    discovered = auto_discover_inputs(in_dir)
    print_discovered_files(discovered)
    audit_discovered_formats(discovered)
    emitter.phase_completed(
        "discovery",
        detail=f"{sum(len(v) for v in discovered.values())} file(s) classified",
    )

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
        "literature":   _resolve(literature, "literature"),
        "device_context": _resolve(None, "device_context"),
        "analysis_workbook": _resolve(None, "analysis_workbook"),
    }
    extra_paths = list(extra_files or []) + discovered.get("extra", [])

    # ── 2. Device context detection ─────────────────────────────────
    emitter.phase_started("device_context")
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
        raise PipelineError(
            "Could not auto-detect device name from any input file. "
            "Either provide it explicitly or ensure input files contain the product name."
        )

    emitter.phase_completed(
        "device_context",
        detail=f"{_device_name} ({meta['device_class']})",
    )

    # ── Decision: denominator selection ─────────────────────────────
    emitter.decision(
        "denominator_selection",
        inputs_summary={
            "single_use_or_reusable": "reusable" if meta["is_reusable"] else "single-use",
            "is_reusable": meta["is_reusable"],
        },
        output="procedures" if meta["is_reusable"] else "units_distributed",
        reason=(
            "Device is reusable, so the complaint-rate denominator is the "
            "estimated number of procedures (episodes of use) per MDCG 2022-21."
            if meta["is_reusable"] else
            "Device is single-use/disposable, so the complaint-rate denominator "
            "is units distributed within the reporting period per MDCG 2022-21."
        ),
        regulatory_basis=["MDCG 2022-21"],
        section="C_volume_of_sales_and_population_exposure",
    )

    # ── Decision: PSUR vs PMSR cadence ───────────────────────────────
    report_type = _emit_cadence_decision(emitter, meta)

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
    emitter.phase_started("parsing")
    _skip_cer = bool(
        context_file_rich
        and context_file_rich.get("device_description")
    )

    # Resolve in-scope part numbers from input documents
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
        literature_path=input_paths["literature"],
        extra_paths=extra_paths,
        start_date=start_date,
        end_date=end_date,
        device_name=_device_name,
        confirm_cb=None,  # auto-map always on
        skip_cer=_skip_cer,
        unified_workbook_path=input_paths.get("analysis_workbook"),
        emitter=emitter,
    )
    parsed_data = parse_result["parsed_data"]
    expanded_context = parse_result["expanded_context"]
    previous_stats = parse_result["previous_stats"]
    product_classification = parse_result.get("product_classification", {}) or {}
    emitter.phase_completed(
        "parsing",
        detail=(
            f"{parsed_data.get('sales', {}).get('total_units', 0):,} units, "
            f"{parsed_data.get('complaints', {}).get('total_complaints', 0)} complaints"
        ),
    )

    # ── 4a. Override cadence from PMS plan if explicitly stated ─────
    pms_plan_data = parsed_data.get("pms_plan")
    if isinstance(pms_plan_data, dict):
        pms_cadence = pms_plan_data.get("psur_cadence", "").strip().upper()
        if pms_cadence in ("ANNUALLY", "EVERY_TWO_YEARS", "EVERY_THREE_YEARS"):
            if pms_cadence != meta.get("psur_cadence"):
                console.print(
                    f"  [green]PSUR cadence overridden by PMS Plan: "
                    f"{meta.get('psur_cadence')} -> {pms_cadence}[/green]"
                )
                emitter.decision(
                    "psur_cadence_pms_plan_override",
                    inputs_summary={
                        "class_based_cadence": meta.get("psur_cadence"),
                        "pms_plan_cadence": pms_cadence,
                    },
                    output=pms_cadence,
                    reason=(
                        "The PMS Plan explicitly states the PSUR cadence; the "
                        "plan is the authoritative source and overrides the "
                        "class-based default (UK MDR 2024 Reg 44ZF requires the "
                        "PMS plan to define surveillance processes)."
                    ),
                    regulatory_basis=["UK MDR 2024 Reg 44ZF"],
                )
                meta["psur_cadence"] = pms_cadence

    # ── 4b. Parse analysis workbook (if provided) ──────────────────
    wb_path = input_paths.get("analysis_workbook")
    if wb_path and wb_path.exists():
        console.print(f"\n[bold]Parsing analysis workbook: {wb_path.name}...[/bold]")
        from parsers.analysis_workbook import parse_analysis_workbook
        wb_data = parse_analysis_workbook(wb_path)

        if wb_data:
            if wb_data.get("monthly_sales"):
                if "sales" not in parsed_data or not parsed_data["sales"]:
                    parsed_data["sales"] = {"total_units": 0, "by_month": {}, "by_region": {}, "by_country": {}, "by_product": {}}
                parsed_data["sales"]["by_month"] = wb_data["monthly_sales"]
                console.print(f"  [green]Monthly sales: {len(wb_data['monthly_sales'])} months from workbook[/green]")

            if wb_data.get("sales_by_region"):
                if "sales" not in parsed_data or not parsed_data["sales"]:
                    parsed_data["sales"] = {"total_units": 0, "by_month": {}, "by_region": {}, "by_country": {}, "by_product": {}}
                region_totals = {}
                for row in wb_data["sales_by_region"]:
                    region_name = row.get("region", "")
                    if not region_name or "worldwide" in region_name.lower() or "total" in region_name.lower():
                        continue
                    total = sum(v for k, v in row.items() if k not in ("region", "pct_of_total") and isinstance(v, (int, float)))
                    if total > 0:
                        region_totals[region_name] = int(total)
                if region_totals:
                    parsed_data["sales"]["by_region"] = region_totals
                    console.print(f"  [green]Regional sales: {len(region_totals)} regions from workbook[/green]")

            if wb_data.get("total_units"):
                if "sales" not in parsed_data or not parsed_data["sales"]:
                    parsed_data["sales"] = {"total_units": 0, "by_month": {}, "by_region": {}, "by_country": {}, "by_product": {}}
                parsed_data["sales"]["total_units"] = wb_data["total_units"]
                console.print(f"  [green]Total units: {wb_data['total_units']:,} from workbook[/green]")

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

            if wb_data.get("external_db") and not parsed_data.get("external_db"):
                parsed_data["external_db"] = wb_data["external_db"]
                console.print(f"  [green]External DB search: {len(wb_data['external_db'])} databases from workbook[/green]")

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
                if context_file_rich is None:
                    context_file_rich = _build_rich_context_from_config(wb_config)
                    _overrides.append("rich_context=built")
                if _overrides:
                    console.print(f"  [green]Config sheet: {', '.join(_overrides)}[/green]")

            if wb_data.get("fsca_table") is not None:
                analysis_tables["fsca_table"] = wb_data["fsca_table"]
                if wb_data.get("fsca_summary"):
                    analysis_tables["fsca_summary"] = wb_data["fsca_summary"]
                console.print(f"  [green]FSCA (Table 8): {len(wb_data['fsca_table'])} rows, summary={wb_data.get('fsca_summary', {})}[/green]")

            if wb_data.get("capa_section_i") is not None:
                analysis_tables["capa_section_i"] = wb_data["capa_section_i"]
                if wb_data.get("capa_summary"):
                    analysis_tables["capa_summary"] = wb_data["capa_summary"]
                console.print(f"  [green]CAPA (Table 9): {len(wb_data['capa_section_i'])} rows, summary={wb_data.get('capa_summary', {})}[/green]")
        else:
            console.print("  [yellow]No data extracted from workbook[/yellow]")

    # ── 5. Compute statistics ───────────────────────────────────────
    emitter.phase_started("statistics")
    console.print("\n[bold]Computing statistics...[/bold]")
    stats = compute_psur_statistics(
        sales_data=parsed_data.get("sales", {}),
        complaints_data=parsed_data.get("complaints", {}),
        surveillance_period=surveillance_period,
        previous_stats=previous_stats,
        is_reusable=meta["is_reusable"],
        ract_data=parsed_data.get("ract") if isinstance(parsed_data.get("ract"), dict) else None,
        product_classification=product_classification,
        emitter=emitter,
    )

    console.print(f"  Total {stats.denominator_type}: {stats.total_units_sold:,}")
    console.print(f"  Total complaints: {stats.total_complaints}")
    console.print(f"  Overall rate: {stats.overall_rate_display}")
    console.print(f"  Serious incidents: {stats.serious_incident_count} ({stats.serious_incident_rate_display})")
    console.print(f"  Trend status: {stats.trend_analysis.status}")
    if stats.yoy_rate_change is not None:
        console.print(f"  YoY rate change: {stats.yoy_rate_change}%")
    emitter.phase_completed(
        "statistics",
        detail=(
            f"rate {stats.overall_rate_display}; trend {stats.trend_analysis.status}"
        ),
    )

    # ── Decision: UK MDR activation on UK sales detection ───────────
    if stats.uk_market_detected:
        emitter.decision(
            "uk_mdr_activation",
            inputs_summary={
                "uk_units": stats.uk_units,
                "uk_complaints": stats.uk_complaints,
            },
            output="UK_MDR_REQUIREMENTS_ACTIVE",
            reason=(
                f"UK/GB sales detected ({stats.uk_units:,} units): the device is "
                "placed on the GB market, so UK MDR 2024 Part 4A post-market "
                "surveillance requirements apply and UK-specific reporting is "
                "included throughout the PSUR."
            ),
            regulatory_basis=["UK MDR 2024 Reg 44ZE"],
        )

    # ── 5b. Build deterministic PSUR tables DOCX ─────────────────────
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
        console.print(f"  [green]PSUR tables DOCX: {tables_path}[/green]")
    except Exception as _e:
        console.print(f"  [yellow]PSUR tables build skipped: {_e}[/yellow]")
        tables_path = None

    # ── 6. Generate charts (skip if user supplied) ──────────────────
    emitter.phase_started("charts")
    console.print("\n[bold]Preparing charts...[/bold]")
    stats_dict = asdict(stats)
    chart_dir = out_dir / "charts"

    user_sales_chart = discovered.get("chart_sales", [None])[0] if discovered.get("chart_sales") else None
    user_trend_chart = discovered.get("chart_trend", [None])[0] if discovered.get("chart_trend") else None

    chart_paths: Dict[str, Path] = {}
    if user_sales_chart and user_sales_chart.exists():
        chart_paths["sales_trend"] = user_sales_chart
        console.print(f"  [green]sales_trend: using user-supplied {user_sales_chart.name}[/green]")
    if user_trend_chart and user_trend_chart.exists():
        chart_paths["trend_ucl"] = user_trend_chart
        console.print(f"  [green]trend_ucl: using user-supplied {user_trend_chart.name}[/green]")

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
    emitter.phase_completed("charts", detail=f"{len(chart_paths)} chart(s)")

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
        emitter=emitter,
    )

    _gen_elapsed = _time.time() - _gen_t0
    _usage = get_token_usage()
    psur["_statistics"] = stats_dict

    # ── 9. Validate ─────────────────────────────────────────────────
    emitter.phase_started("validation")
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

    emitter.decision(
        "final_validation_outcome",
        inputs_summary={
            "checklist": "331-point PSUR validation checklist",
            "sections_validated": len(psur.get("sections", {})),
        },
        output={"passed": bool(is_valid), "error_count": len(errors)},
        reason=(
            "All 331 validation checks passed: the PSUR conforms to the "
            "template schema, contains no fabricated statistics, and meets "
            "the MDCG 2022-21 content requirements for Article 86 PSURs."
            if is_valid else
            f"{len(errors)} validation issue(s) remain after generation and "
            "remediation; the draft requires human review before release."
        ),
        regulatory_basis=["EU MDR Art. 86", "MDCG 2022-21"],
    )
    emitter.phase_completed(
        "validation",
        detail="passed" if is_valid else f"{len(errors)} issue(s)",
    )

    # ── 10. Save outputs ────────────────────────────────────────────
    emitter.phase_started("rendering")
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / f"PSUR_{safe_name}_{end_date[:4]}.json"
    with open(json_path, "w") as f:
        json.dump(psur, f, indent=2, default=str)
    console.print(f"\n  JSON: {json_path}")

    docx_path = out_dir / f"PSUR_{safe_name}_{end_date[:4]}.docx"
    renderer = PSURTemplateRenderer()
    renderer.render(psur, docx_path, chart_paths=chart_paths,
                    tables_docx_path=tables_path)
    console.print(f"  DOCX (template-based): {docx_path}")
    emitter.phase_completed("rendering", detail=docx_path.name)

    emitter.phase_started("artifacts")
    stats_path = out_dir / f"PSUR_{safe_name}_{end_date[:4]}_statistics.json"
    with open(stats_path, "w") as f:
        json.dump(stats_dict, f, indent=2, default=str)
    console.print(f"  Stats: {stats_path}")

    # Traceability matrix — every narrative sentence → data source
    trace_path: Optional[Path] = None
    trace_matrix = getattr(validator, "last_traceability_matrix", None)
    if trace_matrix:
        trace_path = out_dir / f"PSUR_{safe_name}_{end_date[:4]}_traceability.json"
        with open(trace_path, "w") as f:
            json.dump(trace_matrix, f, indent=2, default=str)
        leak_count = trace_matrix.get("summary", {}).get("total_leakage_findings", 0)
        if leak_count:
            console.print(f"  [yellow]Traceability: {trace_path} ({leak_count} leakage findings)[/yellow]")
        else:
            console.print(f"  [green]Traceability: {trace_path} (clean)[/green]")

    # Validation report artifact
    validation_path = out_dir / f"PSUR_{safe_name}_{end_date[:4]}_validation.json"
    with open(validation_path, "w") as f:
        json.dump({"passed": bool(is_valid), "error_count": len(errors),
                   "errors": errors}, f, indent=2, default=str)
    console.print(f"  Validation report: {validation_path}")

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

    artifact_paths: Dict[str, Path] = {
        f"PSUR_{safe_name}_{end_date[:4]}.docx": docx_path,
        f"PSUR_{safe_name}_{end_date[:4]}.json": json_path,
        f"PSUR_{safe_name}_{end_date[:4]}_statistics.json": stats_path,
        f"PSUR_{safe_name}_{end_date[:4]}_validation.json": validation_path,
    }
    if trace_path is not None:
        artifact_paths[trace_path.name] = trace_path
    if tables_path is not None and tables_path.exists():
        artifact_paths[tables_path.name] = tables_path

    _CONTENT_TYPES = {
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".json": "application/json",
    }
    artifacts_meta = [
        {
            "name": name,
            "content_type": _CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream"),
            "size_bytes": path.stat().st_size,
        }
        for name, path in artifact_paths.items()
        if path.exists()
    ]
    emitter.phase_completed("artifacts", detail=f"{len(artifacts_meta)} artifact(s)")
    emitter.complete(
        artifacts=artifacts_meta,
        validation={"passed": bool(is_valid), "error_count": len(errors)},
    )

    return {
        "device_name": _device_name,
        "report_type": report_type,
        "json_path": json_path,
        "docx_path": docx_path,
        "stats_path": stats_path,
        "validation_path": validation_path,
        "trace_path": trace_path,
        "tables_path": tables_path,
        "chart_paths": chart_paths,
        "artifacts": artifact_paths,
        "artifacts_meta": artifacts_meta,
        "is_valid": bool(is_valid),
        "errors": errors,
        "elapsed_seconds": _total_elapsed,
    }
