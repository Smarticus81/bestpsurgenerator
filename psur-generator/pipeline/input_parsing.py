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
from imdrf_coder import auto_code_complaints, strip_imdrf_code, _is_valid_imdrf_code

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

    if sales_path:
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

    if complaints_path:
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

    # Auto-code IMDRF if complaints were parsed from any source
    if parsed_data["complaints"].get("total_complaints", 0) > 0:
        # Use _is_valid_imdrf_code() to recognize both alphanumeric codes
        # AND known IMDRF terms so user-provided term-only codes are NOT
        # flagged as "uncoded".
        summaries = parsed_data["complaints"].get("complaint_summaries", [])
        uncoded = sum(
            1 for s in summaries
            if not s.get("imdrf_code")
            or str(s["imdrf_code"]).strip() in ("", "Unknown", "N/A", "nan")
            or not _is_valid_imdrf_code(str(s["imdrf_code"]))
        )
        if uncoded > 0:
            console.print(f"    -> [yellow]{uncoded} complaints need IMDRF coding...[/yellow]")
            auto_code_complaints(summaries, {"device_name": device_name})
            _rebuild_imdrf_counts(parsed_data["complaints"], summaries)
            console.print(f"    -> [green]IMDRF auto-coding complete[/green]")
        else:
            # Even when no auto-coding is needed, rebuild counts to ensure
            # term-only codes are normalised and cross-tabs are populated.
            _rebuild_imdrf_counts(parsed_data["complaints"], summaries)
            console.print(f"    -> [green]All complaints already have valid IMDRF codes[/green]")

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
            previous_stats = prev.get("_statistics", None)
            prev_text = json.dumps(prev, default=str)
            expanded_context["previous_psur"] = prev_text
            parsed_data["previous_psur"] = prev_text
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
    }
