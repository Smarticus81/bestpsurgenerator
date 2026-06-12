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
from typing import Any, Dict, List, Optional

import logging
from rich.console import Console

from config import INPUT_DIR, OUTPUT_DIR
from validation import PSURValidator
from rendering import PSURTemplateRenderer

# Pipeline modules
from pipeline.discovery import auto_discover_inputs

logger = logging.getLogger(__name__)

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

    # ── Delegate to the shared pipeline runner (same code path as the
    # FastAPI service). The CLI uses the default NoopEmitter so its
    # behaviour is unchanged.
    from pipeline.run import run_generation, PipelineError

    try:
        run_generation(
            start_date=start_date,
            end_date=end_date,
            device_name=device_name,
            input_dir=Path(input_dir) if input_dir else None,
            output_dir=Path(output_dir) if output_dir else None,
            resume=resume,
            sales=sales,
            complaints=complaints,
            capa=capa,
            cer=cer,
            ifu=ifu,
            rmf=rmf,
            ract=ract,
            pms_plan=pms_plan,
            pmcf=pmcf,
            fsca=fsca,
            external_db=external_db,
            previous_psur=previous_psur,
            extra_files=extra_files,
        )
    except PipelineError as ex:
        console.print(f"  [red]{ex}[/red]")
        raise typer.Exit(1)


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
    is_first_psur: bool = typer.Option(False, "--first-psur", help="Skip the previous_psur mandatory check"),
    resume: bool = typer.Option(False, "--resume", help="Resume from last checkpoint"),
    ollama_model: Optional[str] = typer.Option(None, "--ollama-model", help="Use a local Ollama model for ALL LLM calls"),
    ollama_url: Optional[str] = typer.Option(None, "--ollama-url", help="Ollama API base URL"),
):
    """Run the Smarticus PSUR Harness v3 (urn:regground:smarticus:psur-harness:v3).

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


if __name__ == "__main__":
    app()
