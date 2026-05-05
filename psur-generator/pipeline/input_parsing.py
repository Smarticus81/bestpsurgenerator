"""Parse all input files into parsed_data dict.

Extracted from main.py to keep the generate() command thin.
Handles: sales, complaints (with IMDRF auto-coding), CAPA, CER,
expanded inputs (IFU, RMF, PMCF, FSCA, external DB), PMS Plan,
RACT, previous PSUR, and extra files.
"""
import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from rich.console import Console

from parsers.sales import parse_sales
from parsers.complaints import parse_complaints
from parsers.capa import parse_capa
from parsers.cer_extractor import extract_cer_data
from parsers.universal import parse_file, parse_any_to_text
from parsers.previous_psur import parse_previous_psur
from parsers.ract import parse_ract
from parsers.sqlite_db import (
    parse_complaints_from_db,
    parse_sales_from_db,
    resolve_td_id_for_device,
    load_product_classification_from_db,
)
from imdrf_coder import (
    auto_code_complaints,
    strip_imdrf_code,
    _is_valid_imdrf_code,
    apply_skill_classification,
)

logger = logging.getLogger(__name__)
console = Console()


def _rebuild_imdrf_counts(complaints_data: Dict, summaries: list):
    """Rebuild IMDRF and harm counts + cross-tabs after auto-coding.

    All keys use TERM-ONLY format (no alphanumeric codes).
    """
    by_imdrf: Dict[str, int] = {}
    by_harm: Dict[str, int] = {}
    harm_by_imdrf: Dict[str, Dict[str, int]] = {}

    for s in summaries:
        code = s.get("imdrf_code", "Unknown")
        if code and code not in ("", "Unknown", "nan"):
            code = strip_imdrf_code(code)
            by_imdrf[code] = by_imdrf.get(code, 0) + 1
        else:
            code = "Unknown"

        harm = s.get("harm", s.get("harm_code", "Unknown"))
        if harm and harm not in ("", "Unknown", "nan"):
            harm = strip_imdrf_code(harm)
            by_harm[harm] = by_harm.get(harm, 0) + 1
        else:
            harm = "No Harm"
            by_harm[harm] = by_harm.get(harm, 0) + 1

        if harm not in harm_by_imdrf:
            harm_by_imdrf[harm] = {}
        harm_by_imdrf[harm][code] = harm_by_imdrf[harm].get(code, 0) + 1

    if by_imdrf:
        complaints_data["by_imdrf_code"] = by_imdrf
    if by_harm:
        complaints_data["by_harm_category"] = by_harm
    if harm_by_imdrf:
        complaints_data["harm_by_imdrf"] = harm_by_imdrf


def _print_mapping_summary(parsed: Dict):
    """Print a brief summary of column mapping results."""
    mappings = parsed.get("column_mappings", {})
    if not mappings:
        return

    mapped = mappings.get("mappings", {})
    unmapped = mappings.get("unmapped_columns", [])

    ai_count = sum(1 for m in mapped.values() if m.get("mapping_source") == "ai")
    exact_count = sum(1 for m in mapped.values() if m.get("mapping_source") == "exact")
    user_count = sum(1 for m in mapped.values() if m.get("mapping_source") == "user")

    parts = []
    if exact_count:
        parts.append(f"{exact_count} exact")
    if ai_count:
        parts.append(f"{ai_count} AI-mapped")
    if user_count:
        parts.append(f"{user_count} user-confirmed")
    if unmapped:
        parts.append(f"{len(unmapped)} extra cols")

    if parts:
        console.print(f"    -> Columns: {', '.join(parts)}")


def parse_all_inputs(
    *,
    sales_path: Optional[Path],
    complaints_path: Optional[Path],
    capa_path: Optional[Path],
    cer_path: Optional[Path],
    ifu_path: Optional[Path],
    rmf_path: Optional[Path],
    ract_path: Optional[Path],
    pms_plan_path: Optional[Path],
    pmcf_path: Optional[Path],
    fsca_path: Optional[Path],
    ext_db_path: Optional[Path],
    prev_psur_path: Optional[Path],
    extra_paths: List[Path],
    start_date: str,
    end_date: str,
    device_name: str,
    confirm_cb: Optional[Callable] = None,
    skip_cer: bool = False,
    unified_workbook_path: Optional[Path] = None,
    db_path: Optional[Path] = None,
    db_td_id: Optional[str] = None,
    scope_product_numbers: Optional[List[str]] = None,
    classification_hint: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Parse all input files and return (parsed_data, expanded_context, previous_stats).

    Returns a dict with keys:
      - parsed_data: Dict of all parsed input data
      - expanded_context: Dict of text extractions for expanded inputs
      - previous_stats: Optional dict of previous period statistics
    """
    console.print("[bold]Parsing input files...[/bold]")
    parsed_data: Dict[str, Any] = {}
    expanded_context: Dict[str, str] = {}
    previous_stats: Optional[Dict[str, Any]] = None
    product_classification: Dict[str, Dict[str, str]] = {}

    # ── Pre-load classification from previous PSUR JSON if available ──
    # appendix_a1_models keys map directly to reusable / single-use buckets.
    classification_map: Dict[str, str] = dict(classification_hint or {})
    if prev_psur_path and prev_psur_path.exists() and prev_psur_path.suffix.lower() == ".json":
        try:
            with open(prev_psur_path) as _pf:
                _prev = json.load(_pf)
            _appx = _prev.get("appendix_a1_models") or {}
            if isinstance(_appx, dict):
                for _model in _appx.get("non_sterile_retractors", []) or []:
                    classification_map[str(_model).strip().upper()] = "reusable"
                for _bucket in ("sterile_retractors", "sterile_retractor_kits"):
                    for _model in _appx.get(_bucket, []) or []:
                        classification_map[str(_model).strip().upper()] = "single_use"
            if classification_map:
                _r = sum(1 for v in classification_map.values() if v == "reusable")
                _s = sum(1 for v in classification_map.values() if v == "single_use")
                console.print(
                    f"  [cyan]Product classification from previous PSUR:[/cyan] "
                    f"{_r} reusable / {_s} single-use models"
                )
        except Exception as e:
            console.print(f"  [yellow]Classification preload skipped: {e}[/yellow]")

    # ── Detect raw data sheets in unified workbook (if provided) ────
    _wb_sheets: Dict[str, str] = {}  # category -> actual sheet name
    if unified_workbook_path and unified_workbook_path.exists() and unified_workbook_path.suffix.lower() in ('.xlsx', '.xls'):
        try:
            import openpyxl
            _wb = openpyxl.load_workbook(str(unified_workbook_path), read_only=True)
            _smap = {s.lower().replace(" ", "_"): s for s in _wb.sheetnames}
            _wb.close()
            for _cat, _cands in [
                ("sales", ["sales", "sales_data", "sales_raw"]),
                ("complaints", ["complaints", "complaints_data", "complaint_data"]),
                ("capa", ["capa", "capa_data", "capa_raw"]),
            ]:
                for _c in _cands:
                    if _c in _smap:
                        _wb_sheets[_cat] = _smap[_c]
                        break
            if _wb_sheets:
                console.print(f"  [dim]Unified workbook sheets detected: {', '.join(_wb_sheets.keys())}[/dim]")
        except Exception:
            pass  # Fall through — individual files will be used

    # ── Core inputs ─────────────────────────────────────────────────

    # SQLite DB takes priority over CSV/Excel files for sales + complaints.
    _db_path = Path(db_path) if db_path else None
    _use_db = bool(_db_path and _db_path.exists())
    _resolved_td_id: Optional[str] = db_td_id

    if _use_db:
        if not _resolved_td_id and device_name:
            try:
                _resolved_td_id = resolve_td_id_for_device(_db_path, device_name)
            except Exception as e:
                console.print(f"  [yellow]td_id auto-resolution failed: {e}[/yellow]")
        td_label = f" td_id={_resolved_td_id}" if _resolved_td_id else " (all td_id)"
        console.print(f"  [cyan]SQLite source:[/cyan] {_db_path.name}{td_label}")

        # Normalize scope: deduplicate, strip blanks, preserve order.
        _scope_pns: Optional[List[str]] = None
        if scope_product_numbers:
            seen = set()
            _scope_pns = []
            for pn in scope_product_numbers:
                key = str(pn).strip()
                if key and key.upper() not in seen:
                    seen.add(key.upper())
                    _scope_pns.append(key)
            if _scope_pns:
                _preview = ", ".join(_scope_pns[:8]) + (" …" if len(_scope_pns) > 8 else "")
                console.print(
                    f"  [cyan]Scope filter:[/cyan] {len(_scope_pns)} part numbers "
                    f"from input documents ({_preview})"
                )
            else:
                _scope_pns = None
        if not _scope_pns:
            console.print(
                "  [yellow]No part-number scope provided — DB queries will return ALL "
                "rows for the device family (td_id only)[/yellow]"
            )

    if _use_db:
        # Resolve per-item classification once; passed to both sales and complaints.
        try:
            product_classification = load_product_classification_from_db(
                _db_path,
                td_id=_resolved_td_id,
                product_numbers=_scope_pns,
                classification_map=classification_map or None,
            ) or {}
            if product_classification:
                _r = sum(1 for v in product_classification.values() if v.get("class") == "reusable")
                _s = sum(1 for v in product_classification.values() if v.get("class") == "single_use")
                _u = sum(1 for v in product_classification.values() if v.get("class") == "unknown")
                console.print(
                    f"  [cyan]DB classification resolved:[/cyan] "
                    f"{_r} reusable / {_s} single-use / {_u} unknown"
                )
        except Exception as e:
            console.print(f"  [yellow]Classification resolution failed: {e}[/yellow]")
            product_classification = {}

        try:
            parsed_data["sales"] = parse_sales_from_db(
                _db_path, start_date, end_date,
                td_id=_resolved_td_id,
                product_numbers=_scope_pns,
                product_classification=product_classification,
            )
            _neg = parsed_data["sales"].get("negative_unit_rows_excluded", 0)
            console.print(f"  Sales (DB): {parsed_data['sales']['total_units']:,} units "
                          f"({parsed_data['sales']['rows_processed']} rows)")
            if _neg:
                console.print(
                    f"    [yellow]Excluded {_neg} negative-unit rows totalling "
                    f"{parsed_data['sales'].get('negative_units_total', 0)} units[/yellow]"
                )
        except Exception as e:
            console.print(f"  [red]Sales DB read failed: {e}[/red]")
            parsed_data["sales"] = {"total_units": 0, "by_month": {}, "by_region": {}, "by_product": {}}

        # ── Pull up to 3 prior 12-month windows for Section C trend columns ──
        # The FormQAR-054 sales table has 3 "Preceding 12-Month" columns. The
        # DB lets us fill them deterministically without asking the LLM.
        try:
            from datetime import datetime, timedelta
            _fmt = "%Y-%m-%d"
            _cur_start = datetime.strptime(start_date, _fmt)
            _cur_end = datetime.strptime(end_date, _fmt)
            historical_periods = []
            for offset in (1, 2, 3):
                # Each prior window is the same length, shifted back by offset windows
                window = _cur_end - _cur_start
                p_end = _cur_start - timedelta(days=1) - (window * (offset - 1))
                p_start = p_end - window
                p_start_s = p_start.strftime(_fmt)
                p_end_s = p_end.strftime(_fmt)
                try:
                    h = parse_sales_from_db(
                        _db_path, p_start_s, p_end_s,
                        td_id=_resolved_td_id,
                        product_numbers=_scope_pns,
                    )
                    historical_periods.append({
                        "label": f"P-{offset} ({p_start_s} → {p_end_s})",
                        "start": p_start_s,
                        "end": p_end_s,
                        "total_units": h.get("total_units", 0),
                        "by_country": h.get("by_country", {}),
                        "by_region": h.get("by_region", {}),
                        "units_unknown_country": h.get("units_unknown_country", 0),
                    })
                except Exception:
                    historical_periods.append({
                        "label": f"P-{offset}", "start": p_start_s, "end": p_end_s,
                        "total_units": 0, "by_country": {}, "by_region": {},
                        "units_unknown_country": 0,
                    })
            parsed_data["sales"]["historical_periods"] = historical_periods
            _ws = ", ".join(f"{p['total_units']:,}" for p in historical_periods)
            console.print(f"  [dim]Historical 12-mo windows: {_ws}[/dim]")
        except Exception as e:
            console.print(f"  [yellow]Historical sales pull skipped: {e}[/yellow]")
    elif sales_path:
        console.print(f"  Sales: {sales_path.name}")
        parsed_data["sales"] = parse_sales(sales_path, start_date, end_date, user_confirm_callback=confirm_cb)
        console.print(f"    -> {parsed_data['sales']['total_units']:,} units")
        _print_mapping_summary(parsed_data["sales"])
    elif _wb_sheets.get("sales") and unified_workbook_path:
        _sn = _wb_sheets["sales"]
        console.print(f"  Sales: {unified_workbook_path.name} \u2192 sheet '{_sn}'")
        parsed_data["sales"] = parse_sales(unified_workbook_path, start_date, end_date,
                                            user_confirm_callback=confirm_cb, sheet_name=_sn)
        console.print(f"    -> {parsed_data['sales']['total_units']:,} units")
        _print_mapping_summary(parsed_data["sales"])
    else:
        console.print("  [yellow]No sales file — using empty data[/yellow]")
        parsed_data["sales"] = {"total_units": 0, "by_month": {}, "by_region": {}, "by_product": {}}

    if _use_db:
        try:
            parsed_data["complaints"] = parse_complaints_from_db(
                _db_path, start_date, end_date,
                td_id=_resolved_td_id,
                product_numbers=_scope_pns,
                product_classification=product_classification,
            )
            console.print(f"  Complaints (DB): {parsed_data['complaints']['total_complaints']} complaints "
                          f"({parsed_data['complaints']['serious_incident_count']} serious)")
        except Exception as e:
            console.print(f"  [red]Complaints DB read failed: {e}[/red]")
            parsed_data["complaints"] = {
                "total_complaints": 0, "by_month": {}, "by_imdrf_code": {},
                "by_harm_category": {}, "by_region": {}, "serious_incidents": [],
                "harm_by_imdrf": {}, "serious_by_region_imdrf": {},
                "complaint_number_format": "", "complaint_summaries": []
            }
    elif complaints_path:
        console.print(f"  Complaints: {complaints_path.name}")
        parsed_data["complaints"] = parse_complaints(complaints_path, start_date, end_date, user_confirm_callback=confirm_cb)
        console.print(f"    -> {parsed_data['complaints']['total_complaints']} complaints")
        _print_mapping_summary(parsed_data["complaints"])
    elif _wb_sheets.get("complaints") and unified_workbook_path:
        _sn = _wb_sheets["complaints"]
        console.print(f"  Complaints: {unified_workbook_path.name} → sheet '{_sn}'")
        parsed_data["complaints"] = parse_complaints(unified_workbook_path, start_date, end_date,
                                                      user_confirm_callback=confirm_cb, sheet_name=_sn)
        console.print(f"    -> {parsed_data['complaints']['total_complaints']} complaints")
        _print_mapping_summary(parsed_data["complaints"])
    else:
        console.print("  [yellow]No complaints file — using empty data[/yellow]")
        parsed_data["complaints"] = {
            "total_complaints": 0, "by_month": {}, "by_imdrf_code": {},
            "by_harm_category": {}, "by_region": {}, "serious_incidents": [],
            "harm_by_imdrf": {}, "serious_by_region_imdrf": {},
            "complaint_number_format": "", "complaint_summaries": []
        }

    # IMDRF classification — SKILL_PSUR_GENERATION F2 pipeline.
    # Step 1/2: deterministic Symptom Code mapping (no LLM call).
    # Step 3:   narrative keyword fallback (no LLM call).
    # Step 4/5: LLM auto-coder for the residual that survived steps 1-3.
    # The forbidden 'Unknown / Not yet determined' tokens are scrubbed at
    # every step.
    if parsed_data["complaints"].get("total_complaints", 0) > 0:
        summaries = parsed_data["complaints"].get("complaint_summaries", [])
        skill_counters = apply_skill_classification(summaries)
        deterministic = skill_counters["symptom_code"] + skill_counters["narrative"]
        if deterministic:
            console.print(
                f"    -> [green]F2 deterministic IMDRF: {deterministic}/"
                f"{len(summaries)} complaints classified "
                f"({skill_counters['symptom_code']} by symptom code, "
                f"{skill_counters['narrative']} by narrative)[/green]"
            )

        # Exclude SKILL-classified complaints from LLM fallback - their
        # terms are intentionally INSORB-specific and not in harm_mdp_codes.csv.
        residual = [
            s for s in summaries
            if not s.get("_skill_classification_source")
            and (
                not s.get("imdrf_code")
                or str(s["imdrf_code"]).strip() in ("", "Unknown", "N/A", "nan")
                or not _is_valid_imdrf_code(str(s["imdrf_code"]))
            )
        ]
        if residual:
            console.print(
                f"    -> [yellow]{len(residual)} complaints still need IMDRF coding "
                f"(LLM fallback)...[/yellow]"
            )
            auto_code_complaints(residual, {"device_name": device_name})

        # Final scrub: F2 forbids 'Unknown' parent-node terms even after LLM.
        from imdrf_coder import force_safe_default
        for s in summaries:
            new_harm, new_mdp = force_safe_default(
                s.get("harm") or s.get("harm_code") or "",
                s.get("imdrf_code") or "",
            )
            s["harm"] = new_harm
            s["harm_code"] = new_harm
            s["imdrf_code"] = new_mdp

        _rebuild_imdrf_counts(parsed_data["complaints"], summaries)
        console.print("    -> [green]IMDRF classification complete[/green]")

    if capa_path:
        console.print(f"  CAPA: {capa_path.name}")
        parsed_data["capa"] = parse_capa(capa_path, start_date, end_date, user_confirm_callback=confirm_cb)
        console.print(f"    -> {parsed_data['capa']['total_capas']} CAPAs")
        _print_mapping_summary(parsed_data["capa"])
    elif _wb_sheets.get("capa") and unified_workbook_path:
        _sn = _wb_sheets["capa"]
        console.print(f"  CAPA: {unified_workbook_path.name} \u2192 sheet '{_sn}'")
        parsed_data["capa"] = parse_capa(unified_workbook_path, start_date, end_date,
                                          user_confirm_callback=confirm_cb, sheet_name=_sn)
        console.print(f"    -> {parsed_data['capa']['total_capas']} CAPAs")
        _print_mapping_summary(parsed_data["capa"])

    if cer_path:
        if skip_cer:
            console.print(f"  CER: {cer_path.name} [dim](skipped — device_context.json provides device metadata)[/dim]")
        else:
            console.print(f"  CER: {cer_path.name}")
            ext = cer_path.suffix.lower()
            if ext in (".pdf", ".docx", ".doc"):
                cer_data = extract_cer_data(cer_path)
                parsed_data["cer"] = cer_data.to_flat_dict()
                console.print(f"    -> {parsed_data['cer'].get('total_pages', 'N/A')} pages (comprehensive extraction)")
            else:
                result = parse_file(cer_path, purpose="clinical evaluation report")
                parsed_data["cer"] = {"full_text": result.get("full_text", ""), "source_file": cer_path.name}
                console.print(f"    -> Parsed as {ext} text")

    # ── Expanded inputs ─────────────────────────────────────────────

    for label, filepath, purpose in [
        ("IFU", ifu_path, "instructions for use"),
        ("RMF", rmf_path, "risk management file"),
        ("PMCF", pmcf_path, "post-market clinical follow-up"),
        ("FSCA", fsca_path, "field safety corrective action"),
        ("External DB", ext_db_path, "external database search results"),
    ]:
        if filepath and filepath.exists():
            console.print(f"  {label}: {filepath.name}")
            text = parse_any_to_text(filepath, purpose=purpose)
            expanded_context[label.lower().replace(" ", "_")] = text
            parsed_data[label.lower().replace(" ", "_")] = text

    # ── CER fallbacks for IFU / PMCF / Literature ───────────────────
    # The CER extractor already pulls these subsections; use them when
    # dedicated files are not supplied. This avoids forcing users to
    # provide separate IFU/PMCF/literature documents if the CER already
    # contains that content.
    cer_info = parsed_data.get("cer") or {}

    if "ifu" not in expanded_context:
        ifu_info = cer_info.get("ifu_info") or {}
        ifu_parts = []
        if ifu_info.get("ifu_summary"):
            ifu_parts.append(f"Summary:\n{ifu_info['ifu_summary']}")
        if ifu_info.get("use_instructions"):
            ifu_parts.append("Use Instructions:\n- " + "\n- ".join(ifu_info["use_instructions"]))
        for k in ("cleaning_reprocessing", "storage_handling", "training_requirements"):
            if ifu_info.get(k):
                ifu_parts.append(f"{k.replace('_', ' ').title()}:\n{ifu_info[k]}")
        if ifu_parts:
            expanded_context["ifu"] = "\n\n".join(ifu_parts)
            parsed_data["ifu"] = expanded_context["ifu"]
            parsed_data["ifu_source"] = "cer"
            console.print("  [dim]IFU: derived from CER (no dedicated IFU file)[/dim]")

    if "pmcf" not in expanded_context:
        se = cer_info.get("safety_efficacy_detail") or {}
        pmcf_parts = []
        if se.get("pmcf_requirements"):
            pmcf_parts.append(f"PMCF Requirements:\n{se['pmcf_requirements']}")
        if se.get("pmcf_planned_activities"):
            pmcf_parts.append("Planned Activities:\n- " + "\n- ".join(se["pmcf_planned_activities"]))
        # Fallback to the flat-dict key populated by to_flat_dict()
        if not pmcf_parts and cer_info.get("pmcf_information"):
            pmcf_parts.append(f"PMCF (from CER):\n{cer_info['pmcf_information']}")
        if pmcf_parts:
            expanded_context["pmcf"] = "\n\n".join(pmcf_parts)
            parsed_data["pmcf"] = expanded_context["pmcf"]
            parsed_data["pmcf_source"] = "cer"
            console.print("  [dim]PMCF: derived from CER (no dedicated PMCF file)[/dim]")

    # Literature review — never supplied as a separate file today, but
    # downstream agents look for "literature_review" context. Populate
    # it from the CER whenever possible.
    if "literature_review" not in expanded_context:
        lit = (cer_info.get("safety_efficacy_detail") or {}).get("literature_review_summary") \
              or cer_info.get("literature_review")
        if lit:
            expanded_context["literature_review"] = lit
            parsed_data["literature_review"] = lit
            parsed_data["literature_review_source"] = "cer"
            console.print("  [dim]Literature review: derived from CER[/dim]")

    # PMS Plan — structured LLM-driven extraction
    if pms_plan_path and pms_plan_path.exists():
        console.print(f"  PMS Plan: {pms_plan_path.name}")
        try:
            from parsers.pms_plan import parse_pms_plan
            pms_data = parse_pms_plan(pms_plan_path)
            parsed_data["pms_plan"] = pms_data
            expanded_context["pms_plan"] = json.dumps(
                {k: v for k, v in pms_data.items() if k != "full_text"},
                indent=2, default=str
            )
            pms_name = pms_data.get("device_name", "")
            if pms_name:
                console.print(f"    -> Device: {pms_name}")
            n_activities = len(pms_data.get("proactive_activities", [])) + len(pms_data.get("reactive_activities", []))
            console.print(f"    -> {n_activities} PMS activities extracted")
        except Exception as e:
            console.print(f"  [yellow]PMS Plan structured parse failed ({e}), falling back to text[/yellow]")
            text = parse_any_to_text(pms_plan_path, purpose="post-market surveillance plan")
            expanded_context["pms_plan"] = text
            parsed_data["pms_plan"] = text

    # RACT — parse structurally to get max expected rates
    if ract_path and ract_path.exists():
        console.print(f"  RACT: {ract_path.name}")
        try:
            ract_data = parse_ract(ract_path)
            parsed_data["ract"] = ract_data
            expanded_context["ract"] = json.dumps(ract_data, indent=2, default=str)
            n_rates = len(ract_data.get("max_expected_rates", {}))
            console.print(f"    -> {ract_data.get('total_hazards', 0)} hazards, {n_rates} max expected rates")
        except Exception as e:
            console.print(f"  [yellow]RACT structured parse failed ({e}), falling back to text[/yellow]")
            text = parse_any_to_text(ract_path, purpose="risk assessment and control table")
            expanded_context["ract"] = text
            parsed_data["ract"] = text

    # Previous PSUR — parse structurally
    if prev_psur_path and prev_psur_path.exists():
        console.print(f"  Previous PSUR: {prev_psur_path.name}")
        ext = prev_psur_path.suffix.lower()
        if ext == ".json":
            with open(prev_psur_path) as f:
                prev = json.load(f)
            prev_text = json.dumps(prev, default=str)
            expanded_context["previous_psur"] = prev_text
            parsed_data["previous_psur"] = prev_text
            # Build a STRUCTURED previous_stats from section_d so YoY math
            # can happen without an LLM call. Keys mirror PSURStatistics so
            # statistics.compute_psur_statistics can subtract/divide directly.
            try:
                # previous_psur.json uses 'section_d_complaints_overview' with
                # child keys 'complaints', 'sales', 'overall_complaint_rate_percent'
                _sec_d = (
                    prev.get("section_d_complaints_overview")
                    or prev.get("section_d")
                    or {}
                )
                _reuse = _sec_d.get("reusable_devices") or {}
                _su = _sec_d.get("single_use_devices") or {}

                def _n(d, *keys, default=0):
                    for k in keys:
                        if k in d and d[k] not in (None, ""):
                            try:
                                return float(d[k])
                            except (TypeError, ValueError):
                                continue
                    return default

                _reuse_units = int(_n(_reuse, "sales", "total_sales", "units"))
                _reuse_c = int(_n(_reuse, "complaints", "total_complaints"))
                _reuse_rate_pct = _n(
                    _reuse, "overall_complaint_rate_percent",
                    "complaint_rate_pct", "complaint_rate_percent",
                )
                _su_units = int(_n(_su, "sales", "total_sales", "units"))
                _su_c = int(_n(_su, "complaints", "total_complaints"))
                _su_rate_pct = _n(
                    _su, "overall_complaint_rate_percent",
                    "complaint_rate_pct", "complaint_rate_percent",
                )
                _prev_units = _reuse_units + _su_units
                _prev_complaints = (
                    int(_sec_d.get("total_complaints") or 0)
                    or (_reuse_c + _su_c)
                )
                previous_stats = {
                    "total_units_sold": _prev_units,
                    "total_complaints": _prev_complaints,
                    "overall_complaint_rate": (
                        _prev_complaints / _prev_units if _prev_units > 0 else 0.0
                    ),
                    "reusable_units": _reuse_units,
                    "reusable_complaints": _reuse_c,
                    "reusable_rate": _reuse_rate_pct / 100.0,
                    "single_use_units": _su_units,
                    "single_use_complaints": _su_c,
                    "single_use_rate": _su_rate_pct / 100.0,
                    "period": prev.get("period", {}),
                    "source": "previous_psur.json::section_d_complaints_overview",
                }
                console.print(
                    f"    [green]Previous PSUR stats: {_prev_complaints} complaints / "
                    f"{_prev_units:,} units (reusable {_reuse_c}/{_reuse_units:,}, "
                    f"single-use {_su_c}/{_su_units:,})[/green]"
                )
            except Exception as _e:
                console.print(f"    [yellow]Could not extract previous_stats from JSON: {_e}[/yellow]")
        else:
            try:
                prev_parsed = parse_previous_psur(prev_psur_path)
                parsed_data["previous_psur"] = prev_parsed
                expanded_context["previous_psur"] = json.dumps(prev_parsed, indent=2, default=str)

                prev_sales = prev_parsed.get("sales_data", {})
                prev_complaints = prev_parsed.get("complaint_summary", {})
                if prev_sales.get("total_units") and prev_complaints.get("total_complaints") is not None:
                    total = prev_sales["total_units"]
                    comp = prev_complaints["total_complaints"]
                    previous_stats = {
                        "total_units_sold": total,
                        "total_complaints": comp,
                        "overall_complaint_rate": comp / total if total > 0 else 0,
                    }
                console.print(
                    f"    -> Period: {prev_parsed['period'].get('start_date', '?')} to "
                    f"{prev_parsed['period'].get('end_date', '?')}, "
                    f"{len(prev_parsed.get('prior_actions', []))} prior actions"
                )
            except Exception as e:
                console.print(f"  [yellow]Structured parse failed ({e}), falling back to text[/yellow]")
                text = parse_any_to_text(prev_psur_path, purpose="previous PSUR")
                expanded_context["previous_psur"] = text
                parsed_data["previous_psur"] = text

    # Extra files
    if extra_paths:
        for ef in extra_paths:
            if Path(ef).exists():
                console.print(f"  Extra: {Path(ef).name}")
                text = parse_any_to_text(Path(ef))
                expanded_context[f"extra_{Path(ef).stem}"] = text

    return {
        "parsed_data": parsed_data,
        "expanded_context": expanded_context,
        "previous_stats": previous_stats,
        "product_classification": product_classification,
    }
