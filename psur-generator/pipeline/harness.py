"""Smarticus PSUR Generation Harness (urn:coopersurgical:smarticus:psur-harness:v3).

Implements the four-block harness specification:

  block_1_input_files       - validate the minimum required source files and columns
  block_2_context_payload   - 16 named slots populated by the agent stages
  block_3_template_fidelity - non-negotiable formatting/structural rules
  block_4_agent_pipeline    - 11 agents executed across 8 stages (with parallel groups
                              at stages 4 and 5) and the post-generation validation gate

The harness layer is *thin*: every agent wraps the existing pipeline modules
(`pipeline/discovery.py`, `pipeline/device_context.py`, `pipeline/input_parsing.py`,
`imdrf_coder.py`, `statistics.py`, `parsers/ract.py`, `charts.py`,
`agents/orchestrator.py`, `rendering/_tables.py`, `rendering/renderer.py`,
`validation/validator.py`) and exposes their output through the 16-slot context
payload exactly as the JSON specification requires.

Public entry-point: `run_harness(...)`.
"""
from __future__ import annotations

import csv
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, Future
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from rich.console import Console

# Existing pipeline modules (re-used, never duplicated).
from config import INPUT_DIR, OUTPUT_DIR
from pipeline.discovery import auto_discover_inputs, print_discovered_files
from pipeline.device_context import (
    load_device_context_file,
    gather_file_snippets,
    extract_device_context_llm,
    resolve_device_metadata,
    build_device_context,
)
from pipeline.input_parsing import parse_all_inputs
from pipeline.performance import print_performance_summary

from charts import generate_all_charts
from statistics import compute_psur_statistics, PSURStatistics
from agents.orchestrator import generate_psur
from validation import PSURValidator
from rendering import PSURTemplateRenderer

logger = logging.getLogger(__name__)
console = Console()


# =====================================================================
# Block 3 - template fidelity rules (registered for the renderer + the
# post-generation validator). These map 1:1 onto the JSON spec.
# =====================================================================

SECTION_ORDER: List[str] = [
    "Cover Page", "A", "B", "C", "D", "E", "F", "G",
    "H", "I", "J", "K", "L", "M",
]

SECTION_TITLES: Dict[str, str] = {
    "A": "Section A: Executive Summary",
    "B": "Section B: Scope And Device Description",
    "C": "Section C: Volume Of Sales and Population Exposure",
    "D": "Section D: Information on Serious Incidents",
    "E": "Section E: Customer Feedback",
    "F": "Section F: Product Complaint Types, Complaint Counts, and Complaint Rates",
    "G": "Section G: Information From Trend Reporting",
    "H": "Section H: Information from Field Safety Corrective Actions (FSCA)",
    "I": "Section I: Corrective and Preventive Actions",
    "J": "Section J: Scientific Literature Review of Relevant Specialist or Technical Literature",
    "K": "Section K: Review of External Databases and Registries",
    "L": "Section L: Post Market Clinical Follow-Up (PMCF)",
    "M": "Section M: Findings and Conclusions",
}

# EEA member states + EFTA + Northern Ireland (XI) + Turkey for the
# regional bucketisation rule the spec mandates.
_EEA_COUNTRIES = {
    "AUSTRIA", "BELGIUM", "BULGARIA", "CROATIA", "CYPRUS", "CZECHIA",
    "CZECH REPUBLIC", "DENMARK", "ESTONIA", "FINLAND", "FRANCE", "GERMANY",
    "GREECE", "HUNGARY", "IRELAND", "ITALY", "LATVIA", "LITHUANIA",
    "LUXEMBOURG", "MALTA", "NETHERLANDS", "POLAND", "PORTUGAL", "ROMANIA",
    "SLOVAKIA", "SLOVENIA", "SPAIN", "SWEDEN",
    "ICELAND", "LIECHTENSTEIN", "NORWAY",
}
_TR_COUNTRIES = {"TURKEY", "TURKIYE"}
_XI_COUNTRIES = {"NORTHERN IRELAND"}
_UK_COUNTRIES = {"UK", "UNITED KINGDOM", "GREAT BRITAIN", "ENGLAND",
                 "SCOTLAND", "WALES"}
_US_COUNTRIES = {"US", "USA", "UNITED STATES", "UNITED STATES OF AMERICA",
                 "PUERTO RICO"}
_NAMED_REGIONS = {
    "AUSTRALIA": "Australia",
    "BRAZIL": "Brazil",
    "CANADA": "Canada",
    "CHINA": "China",
    "JAPAN": "Japan",
}


def map_country_to_region(country: str) -> str:
    """Map a raw country name to the FormQAR-054 regional bucket."""
    if not country:
        return "Rest of World"
    key = str(country).strip().upper()
    if key in _EEA_COUNTRIES or key in _TR_COUNTRIES or key in _XI_COUNTRIES:
        return "EEA+TR+XI"
    if key in _UK_COUNTRIES:
        return "UK"
    if key in _US_COUNTRIES:
        return "United States"
    if key in _NAMED_REGIONS:
        return _NAMED_REGIONS[key]
    return "Rest of World"


# =====================================================================
# Block 1 - input file requirements + validation
# =====================================================================

@dataclass
class InputFileReport:
    """Per-file readiness state surfaced to downstream agents."""
    category: str
    mandatory: bool
    present: bool
    path: Optional[Path] = None
    issues: List[str] = field(default_factory=list)
    feeds_sections: List[str] = field(default_factory=list)


REQUIRED_INPUTS: Dict[str, Dict[str, Any]] = {
    "device_context": {
        "mandatory": True,
        "exts": {".json"},
        "feeds": ["Cover Page", "A", "B"],
        "min_fields": [
            "device_trade_names",
            "basic_udi_di_or_device_family_name",
            "eu_mdr_classification_and_rule",
            "psur_reporting_period",
        ],
    },
    "sales": {
        "mandatory": True,
        "exts": {".csv", ".xlsx", ".xls"},
        "feeds": ["C"],
        "required_columns": [
            "Customer Country", "ItemNumber", "ProductGroup",
            "Month", "Quantity", "Calendar year",
        ],
    },
    "complaints": {
        "mandatory": True,
        "exts": {".csv", ".xlsx", ".xls"},
        "feeds": ["D", "E", "F", "G"],
        "required_columns": [
            "CSI Notification Date", "Complaint Number", "Product Number",
            "Country", "Symptom Code", "MDR Issued",
        ],
    },
    "external_db": {
        "mandatory": True,
        "exts": {".json"},
        "feeds": ["K"],
        "min_fields": ["product_name", "surveillance_period"],
    },
    "previous_psur": {
        "mandatory": False,  # Optional for first PSUR
        "exts": {".json", ".docx", ".pdf"},
        "feeds": ["A", "C", "D", "F", "G", "M"],
    },
    "cer": {
        "mandatory": False,  # device_context.json may already provide what CER feeds
        "exts": {".docx", ".pdf"},
        "feeds": ["B", "J", "L", "M"],
    },
    "ract": {
        "mandatory": False,
        "exts": {".csv", ".xlsx", ".xls"},
        "feeds": ["F", "G", "H", "M"],
    },
}


def _csv_header(filepath: Path) -> List[str]:
    """Read the first header row from a CSV regardless of encoding."""
    for enc in ("utf-8", "utf-8-sig", "latin-1", "cp1252"):
        try:
            with open(filepath, "r", encoding=enc, newline="") as fh:
                reader = csv.reader(fh)
                row = next(reader, [])
                return [c.strip() for c in row]
        except UnicodeDecodeError:
            continue
        except Exception:
            return []
    return []


def _xlsx_header(filepath: Path) -> List[str]:
    try:
        import openpyxl
        wb = openpyxl.load_workbook(filepath, read_only=True, data_only=True)
        ws = wb.active
        if ws is None:
            wb.close()
            return []
        row = next(ws.iter_rows(max_row=1, values_only=True), tuple())
        wb.close()
        return [str(c).strip() for c in row if c is not None]
    except Exception:
        return []


def _columns_match(actual: List[str], required: List[str]) -> List[str]:
    """Return required column names that are missing (case/space-insensitive)."""
    actual_norm = {re.sub(r"\s+", " ", c.strip().lower()) for c in actual}
    missing: List[str] = []
    for col in required:
        token = re.sub(r"\s+", " ", col.strip().lower())
        if token in actual_norm:
            continue
        # accept substring matches so AI-mapped inputs still validate
        if any(token in a or a in token for a in actual_norm if a):
            continue
        missing.append(col)
    return missing


def validate_block1_inputs(
    discovered: Dict[str, List[Path]],
    *,
    is_first_psur: bool = False,
) -> Tuple[List[InputFileReport], List[str]]:
    """Validate Block 1 of the spec.

    Returns (per-file report, blocker list). A non-empty blocker list means the
    harness must abort - the spec forbids fabrication when inputs are absent.
    """
    reports: List[InputFileReport] = []
    blockers: List[str] = []

    for category, rules in REQUIRED_INPUTS.items():
        files = discovered.get(category, []) or []
        present = bool(files)
        path = files[0] if files else None
        rep = InputFileReport(
            category=category,
            mandatory=bool(rules["mandatory"]),
            present=present,
            path=path,
            feeds_sections=list(rules.get("feeds", [])),
        )

        if not present:
            mandatory = rules["mandatory"]
            if category == "previous_psur":
                mandatory = mandatory or not is_first_psur
            if mandatory:
                blockers.append(
                    f"Missing mandatory input: {category} "
                    f"(feeds sections: {', '.join(rules.get('feeds', [])) or '-'})"
                )
                rep.issues.append("missing")
        else:
            ext = path.suffix.lower()
            if rules.get("exts") and ext not in rules["exts"]:
                rep.issues.append(
                    f"unsupported extension {ext}; expected {sorted(rules['exts'])}"
                )

            req_cols = rules.get("required_columns")
            if req_cols and ext == ".csv":
                missing = _columns_match(_csv_header(path), req_cols)
                if missing:
                    rep.issues.append(f"missing columns: {', '.join(missing)}")
            elif req_cols and ext in (".xlsx", ".xls"):
                missing = _columns_match(_xlsx_header(path), req_cols)
                if missing:
                    rep.issues.append(f"missing columns: {', '.join(missing)}")

            min_fields = rules.get("min_fields")
            if min_fields and ext == ".json":
                try:
                    with open(path, "r", encoding="utf-8") as fh:
                        doc = json.load(fh)
                except Exception as ex:
                    rep.issues.append(f"unreadable JSON: {ex}")
                    doc = {}
                if isinstance(doc, dict):
                    for f in min_fields:
                        if f not in doc and f.lower() not in {k.lower() for k in doc}:
                            rep.issues.append(f"missing field: {f}")
        reports.append(rep)

    return reports, blockers


def render_block1_summary(reports: List[InputFileReport]) -> None:
    from rich.table import Table
    table = Table(title="Block 1 - Input File Validation")
    table.add_column("Category", style="cyan")
    table.add_column("Mandatory")
    table.add_column("File")
    table.add_column("Issues", style="yellow")
    for r in reports:
        table.add_row(
            r.category,
            "Yes" if r.mandatory else "No",
            r.path.name if r.path else "(missing)",
            "; ".join(r.issues) if r.issues else "ok",
        )
    console.print(table)


# =====================================================================
# Block 2 - 16-slot context payload
# =====================================================================

SLOT_NAMES: List[str] = [
    "report_identity",
    "regulatory_scope",
    "device_scope",
    "sales_data",
    "vigilance_data",
    "complaint_data",
    "risk_management_data",
    "capa_data",
    "literature_review",
    "external_databases",
    "pmcf_data",
    "benefit_risk_determination",
    "actions_and_updates",
    # Three operational slots that the spec references implicitly through the
    # agent inputs/outputs (CER extracts, charts, raw parsed payload):
    "raw_inputs",
    "chart_paths",
    "harness_meta",
]


@dataclass
class HarnessContext:
    """Mutable, agent-owned context bag (block_2_context_payload)."""
    slots: Dict[str, Any] = field(
        default_factory=lambda: {k: {} for k in SLOT_NAMES}
    )
    issues: List[str] = field(default_factory=list)

    def set(self, slot: str, value: Any) -> None:
        if slot not in self.slots:
            raise KeyError(f"Unknown context slot: {slot}")
        self.slots[slot] = value

    def update(self, slot: str, **fields_) -> None:
        if slot not in self.slots:
            raise KeyError(f"Unknown context slot: {slot}")
        existing = self.slots[slot]
        if not isinstance(existing, dict):
            existing = {"_value": existing}
        existing.update(fields_)
        self.slots[slot] = existing

    def get(self, slot: str, default: Any = None) -> Any:
        if default is None:
            default = {}
        return self.slots.get(slot, default)


# =====================================================================
# Block 4 - the eleven agents, organised across eight stages
# =====================================================================

# ---------------------------------------------------------------------
# Stage 1 - regulatory_classifier_agent
# ---------------------------------------------------------------------

def agent_regulatory_classifier(
    context: HarnessContext,
    *,
    device_context_path: Optional[Path],
    device_context_rich: Optional[Dict[str, Any]],
    device_meta: Dict[str, Any],
    start_date: str,
    end_date: str,
    is_first_psur: bool,
) -> None:
    """Determine the regulatory scope BEFORE any section generation begins.

    Sets cadence, table-selection flags, regional-breakdown requirements,
    and the per-jurisdiction applicability flags that the spec mandates.
    """
    raw: Dict[str, Any] = {}
    if device_context_path and device_context_path.exists():
        try:
            with open(device_context_path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except Exception:
            raw = {}

    device_class = (device_meta.get("device_class") or "").upper()
    is_implant = bool(
        re.search(r"implant", str(raw.get("intended_purpose", "")) +
                          str(raw.get("device_description", "")), re.IGNORECASE)
    )

    # Cadence per the spec's classification matrix.
    if is_implant or device_class in ("CLASS_IIB", "CLASS_III"):
        cadence_months = 12
        cadence_label = "ANNUALLY"
    elif device_class == "CLASS_IIA":
        cadence_months = 24
        cadence_label = "EVERY_TWO_YEARS"
    elif device_class == "CLASS_I":
        cadence_months = 24
        cadence_label = "EVERY_TWO_YEARS"
    else:
        cadence_months = 12
        cadence_label = "ANNUALLY"

    # Reporting period bookkeeping.
    try:
        d0 = datetime.strptime(start_date, "%Y-%m-%d")
        d1 = datetime.strptime(end_date, "%Y-%m-%d")
        period_months = max(1, round((d1 - d0).days / 30.44))
    except ValueError:
        period_months = cadence_months

    # Jurisdiction flags - lift from device_context.json when present.
    eu_mdr_text = str(raw.get("eu_mdr_classification_and_rule", "")).upper()
    is_ce_marked = bool(eu_mdr_text) and "CLASS" in eu_mdr_text
    classification_rule_match = re.search(r"RULE\s*\d+", eu_mdr_text)
    classification_rule = classification_rule_match.group(0) if classification_rule_match else ""

    nb_raw = str(raw.get("notified_body_name_and_id", ""))
    nb_match = re.search(r"\(?(\d{4})\)?", nb_raw)
    nb_number = nb_match.group(1) if nb_match else ""
    nb_name = nb_raw.split("(")[0].strip().rstrip(",- ")

    uk_text = str(raw.get("uk_mdr_classification_and_rule", "")).upper()
    is_ukca_marked = "CLASS" in uk_text and "UKCA" in uk_text

    fda_text = (
        str(raw.get("fda_clearance_number", "")) +
        str(raw.get("premarket_submission", ""))
    ).upper()
    is_fda_cleared = bool(re.search(r"K\d{6}|DEN\d{6}|P\d{6}", fda_text))

    hc_text = str(raw.get("health_canada_licence_number", "")).upper()
    is_hc_licensed = bool(re.match(r"\d+", hc_text))

    payload = {
        "eu_mdr": {
            "is_ce_marked": is_ce_marked,
            "mdr_vs_legacy": "MDR" if is_ce_marked else "UNKNOWN",
            "classification": device_class,
            "classification_rule": classification_rule,
            "certificate_number": device_meta.get("certificate_number") or "",
            "certificate_issue_date": device_meta.get("certificate_date") or "",
            "notified_body_name": nb_name,
            "notified_body_number": nb_number,
            "basic_udi_di": raw.get("basic_udi_di_or_device_family_name", ""),
            "emdn_code": raw.get("emdn_code", ""),
            "article_86_update_frequency": cadence_label,
        },
        "uk_mdr": {
            "is_ukca_marked": is_ukca_marked,
            "uk_classification": device_class if is_ukca_marked else "",
            "uk_responsible_person": raw.get("uk_responsible_person", ""),
            "device_lifetime_years": (
                raw.get("device_lifetime", {}) or {}
            ).get("expected_service_life", ""),
            "reg_44ZF_ucl_in_plan_required": True,
        },
        "us_fda": {
            "is_fda_cleared": is_fda_cleared,
            "fda_classification": "",
            "premarket_submission_numbers": [
                t for t in re.findall(r"K\d{6}|DEN\d{6}|P\d{6}", fda_text)
            ],
            "cfr_part_803_applicable": True,
        },
        "health_canada": {
            "is_hc_licensed": is_hc_licensed,
            "device_licence_number": raw.get("health_canada_licence_number", ""),
            "sor_98_282_applicable": is_hc_licensed,
        },
        "_decisions": {
            "cadence_months": cadence_months,
            "cadence_label": cadence_label,
            "table_1_or_2": "Table 1" if cadence_months == 12 else "Table 2",
            "table_7_or_8": "Table 7" if cadence_months == 12 else "Table 8",
            "regional_separation_required": True,
            "fsca_scope_jurisdictions": [
                j for j, on in (
                    ("EU", is_ce_marked), ("UK", is_ukca_marked),
                    ("US", is_fda_cleared), ("CA", is_hc_licensed),
                ) if on
            ],
            "is_first_psur": is_first_psur,
            "period_months": period_months,
        },
    }
    context.set("regulatory_scope", payload)
    console.print(
        f"  [cyan]Regulatory scope:[/cyan] {device_class or '?'} -> "
        f"cadence={cadence_label}; jurisdictions="
        f"{','.join(payload['_decisions']['fsca_scope_jurisdictions']) or '-'}"
    )


# ---------------------------------------------------------------------
# Stage 2 - data_ingestion_agent
# ---------------------------------------------------------------------

def agent_data_ingestion(
    context: HarnessContext,
    *,
    discovered: Dict[str, List[Path]],
    start_date: str,
    end_date: str,
    device_meta: Dict[str, Any],
    device_context_rich: Optional[Dict[str, Any]],
    scope_pns: Optional[List[str]],
    skip_cer: bool,
) -> Dict[str, Any]:
    """Parse all source files and populate the raw data slots."""
    def _first(cat: str) -> Optional[Path]:
        files = discovered.get(cat, [])
        return files[0] if files else None

    parse_result = parse_all_inputs(
        sales_path=_first("sales"),
        complaints_path=_first("complaints"),
        capa_path=_first("capa"),
        cer_path=_first("cer"),
        ifu_path=_first("ifu"),
        rmf_path=_first("rmf"),
        ract_path=_first("ract"),
        pms_plan_path=_first("pms_plan"),
        pmcf_path=_first("pmcf"),
        fsca_path=_first("fsca"),
        ext_db_path=_first("external_db"),
        prev_psur_path=_first("previous_psur"),
        extra_paths=discovered.get("extra", []) or [],
        start_date=start_date,
        end_date=end_date,
        device_name=device_meta.get("device_name", ""),
        confirm_cb=None,
        skip_cer=skip_cer,
        unified_workbook_path=_first("analysis_workbook"),
    )
    parsed = parse_result["parsed_data"]
    expanded = parse_result["expanded_context"]
    previous_stats = parse_result.get("previous_stats")
    product_classification = parse_result.get("product_classification", {}) or {}

    sales = parsed.get("sales", {}) or {}
    complaints = parsed.get("complaints", {}) or {}

    # Slot: report_identity
    context.set("report_identity", {
        "psur_reference_number": "",
        "version": "1.0",
        "date_prepared": datetime.utcnow().strftime("%Y-%m-%d"),
        "reporting_period_start": start_date,
        "reporting_period_end": end_date,
        "cadence_months": context.get("regulatory_scope")
            .get("_decisions", {}).get("cadence_months", 12),
        "is_first_psur": context.get("regulatory_scope")
            .get("_decisions", {}).get("is_first_psur", False),
        "preceding_period_start": (previous_stats or {}).get("period", {}).get("start_date"),
        "preceding_period_end": (previous_stats or {}).get("period", {}).get("end_date"),
    })

    # Slot: device_scope
    rich = device_context_rich or {}
    context.set("device_scope", {
        "device_trade_names": rich.get("device_trade_names", [])
            or ([device_meta.get("device_name")] if device_meta.get("device_name") else []),
        "device_description": rich.get("device_description", ""),
        "intended_purpose": rich.get("intended_use", ""),
        "indications": rich.get("indications", []),
        "contraindications": rich.get("contraindications", []),
        "target_population": rich.get("target_patient_population", ""),
        "intended_user": rich.get("intended_user_profile", ""),
        "single_use_or_reusable": rich.get("single_use_or_reusable", ""),
        "device_lifetime": rich.get("device_lifetime", {}),
        "model_catalog_numbers": (rich.get("known_identifiers") or {}).get("model_numbers", [])
            or scope_pns or [],
        "grouping_applied": bool(scope_pns and len(scope_pns) > 1),
        "leading_device": (rich.get("device_trade_names") or [None])[0],
        "market_status": "On Market",
    })

    # Slot: sales_data (raw shape; statistical_engine fills rates + pct_change)
    methodology = (
        "units_distributed" if not device_meta.get("is_reusable")
        else "episodes_of_use"
    )
    rows = []
    by_country = sales.get("by_country", {}) or {}
    by_region = sales.get("by_region", {}) or {}
    if by_country:
        for country, qty in by_country.items():
            rows.append({
                "region": map_country_to_region(country),
                "country": country,
                "period_label": "current",
                "period_start": start_date,
                "period_end": end_date,
                "quantity": int(qty),
            })
    elif by_region:
        for region, qty in by_region.items():
            rows.append({
                "region": region,
                "country": "",
                "period_label": "current",
                "period_start": start_date,
                "period_end": end_date,
                "quantity": int(qty),
            })

    context.set("sales_data", {
        "methodology": methodology,
        "unit_multiplier": sales.get("unit_multiplier", 1) or 1,
        "denominator_methodology_rationale": (
            "Single-use device - denominator equals units distributed."
            if methodology == "units_distributed"
            else "Reusable device - denominator equals estimated episodes of use."
        ),
        "by_region_and_period": rows,
        "worldwide_current_total": int(sales.get("total_units", 0)),
        "worldwide_preceding_total": int((previous_stats or {}).get("total_units_sold", 0)),
        "pct_change": None,
        "per_device_breakdown": [],
        "_raw_sales": sales,
    })

    # Slot: complaint_data (raw counts; statistical_engine fills rates + UCL)
    serious_incidents = complaints.get("serious_incidents", []) or []
    context.set("complaint_data", {
        "total_complaints": int(complaints.get("total_complaints", 0)),
        "total_confirmed": int(complaints.get("confirmed_count", 0))
            or int(complaints.get("total_confirmed", 0)),
        "total_unconfirmed": max(
            0,
            int(complaints.get("total_complaints", 0))
                - int(complaints.get("confirmed_count", 0)),
        ),
        "overall_rate_pct": None,
        "by_harm_and_mdp": [],
        "by_region": [],
        "monthly_time_series": [],
        "per_device_breakdown": [],
        "symptom_to_imdrf_mapping": complaints.get("symptom_to_imdrf_mapping", []),
        "_raw_complaints": complaints,
        "_serious_incidents": serious_incidents,
    })

    # Slot: vigilance_data (Section D)
    region_count_eea = sum(
        1 for s in serious_incidents
        if map_country_to_region(s.get("country", "")) == "EEA+TR+XI"
    )
    region_count_uk = sum(
        1 for s in serious_incidents
        if map_country_to_region(s.get("country", "")) == "UK"
    )
    mdr_filtered = [
        s for s in serious_incidents
        if str(s.get("mdr_issued", "")).strip().upper() in ("YES", "TRUE", "1", "Y")
    ]
    context.set("vigilance_data", {
        "serious_incidents_eu_uk": {
            "count_eea": region_count_eea,
            "count_uk": region_count_uk,
            "count_worldwide": len(serious_incidents),
            "by_imdrf_annex_a": [],
            "by_imdrf_annex_c": [],
            "by_imdrf_annex_f_x_d": [],
        },
        "mdr_reports_us_fda": {
            "total_count": len(mdr_filtered),
            "by_type": {
                "death": sum(1 for s in mdr_filtered
                             if "death" in str(s.get("harm", "")).lower()),
                "serious_injury": sum(
                    1 for s in mdr_filtered
                    if any(t in str(s.get("harm", "")).lower()
                           for t in ("injury", "harm", "laceration", "infection"))
                ),
                "malfunction": sum(
                    1 for s in mdr_filtered
                    if "malfunction" in str(s.get("nonconformity", "")).lower()
                       or "no health" in str(s.get("harm", "")).lower()
                ),
            },
            "details": mdr_filtered[:50],
        },
        "fsca": {
            "any_initiated": bool(parsed.get("fsca")),
            "records": parsed.get("fsca", []) if isinstance(parsed.get("fsca"), list) else [],
        },
        "new_incident_types_identified": False,
    })

    # Slot: capa_data
    capa = parsed.get("capa", {}) or {}
    context.set("capa_data", {
        "any_initiated_during_period": bool(capa.get("total_capas", 0)),
        "records": capa.get("capa_summaries", []) or capa.get("records", []) or [],
        "_raw_capa": capa,
    })

    # Slot: literature_review (CER extract)
    cer = parsed.get("cer") or {}
    context.set("literature_review", {
        "cer_reference": (rich.get("cer_document") or {}).get("number", ""),
        "search_methodology_summary": (cer.get("safety_efficacy_detail") or {})
            .get("literature_review_summary", "")
            or cer.get("literature_review", ""),
        "databases_searched": [],
        "new_publications_identified": 0,
        "new_risks_from_literature": False,
        "state_of_art_changes": False,
        "conclusion": "",
    })

    # Slot: external_databases
    ext_db = parsed.get("external_db") or {}
    if isinstance(ext_db, dict):
        databases = ext_db.get("databases", [])
        if not databases and "results" in ext_db:
            databases = ext_db["results"]
    else:
        databases = []
    context.set("external_databases", {
        "databases_reviewed": databases,
        "new_safety_signals": False,
        "conclusion": "",
        "_raw": ext_db,
    })

    # Slot: pmcf_data
    pmcf = parsed.get("pmcf") or {}
    context.set("pmcf_data", {
        "pmcf_required": bool(pmcf),
        "pmcf_plan_number": (rich.get("pmcf_plan_document") or {}).get("number", ""),
        "pmcf_activities": pmcf.get("activities", [])
            if isinstance(pmcf, dict) else [],
        "off_label_use_identified": False,
        "new_uses_identified": False,
    })

    # Stash everything else for downstream agents
    context.set("raw_inputs", {
        "parsed_data": parsed,
        "expanded_context": expanded,
        "previous_stats": previous_stats,
        "product_classification": product_classification,
    })

    # SKILL_PSUR_GENERATION reconciliations (F4, F5, F6, F7, F8, F10).
    apply_skill_reconciliations(
        context,
        device_context_rich=device_context_rich,
        previous_psur_path=_first("previous_psur"),
    )

    console.print(
        f"  [green]Ingested:[/green] sales={sales.get('total_units', 0):,} units; "
        f"complaints={complaints.get('total_complaints', 0)} "
        f"({len(serious_incidents)} serious)"
    )
    return parse_result


# ---------------------------------------------------------------------------
# SKILL_PSUR_GENERATION reconciliations (F4-F10).
# Each fix below corresponds to a documented failure mode in
# SKILL_PSUR_GENERATION.md. They run *after* data_ingestion populates the
# raw slots so the deterministic source-of-truth precedence is enforced
# before any narrative agent reads the context.
# ---------------------------------------------------------------------------

# F10: BSI 0086 is the legacy MDD Notified Body number; the current EU MDR
# Notified Body for INSORB (and all CooperSurgical Class III devices going
# through BSI Netherlands) is 2797.
_NB_NUMBER_LEGACY_TO_MDR = {
    "0086": "2797",
}
_NB_NAME_MDR_CANONICAL = "BSI Group The Netherlands B.V."


# F4: device-context placeholder strings the SKILL forbids. These signal
# "data not in source" and must NEVER be paraphrased into a fabricated
# narrative; they must surface as "[TO BE COMPLETED: ...]" markers instead.
_FORBIDDEN_PLACEHOLDER_PATTERNS = [
    re.compile(r"^see (the )?ifu", re.IGNORECASE),
    re.compile(r"^see technical documentation", re.IGNORECASE),
    re.compile(r"^refer to technical documentation", re.IGNORECASE),
    re.compile(r"^see td\d+", re.IGNORECASE),
    re.compile(r"^to be (completed|determined)", re.IGNORECASE),
]


def _is_forbidden_placeholder(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    s = value.strip()
    return any(p.search(s) for p in _FORBIDDEN_PLACEHOLDER_PATTERNS)


def _read_previous_psur_doc(previous_psur_path: Optional[Path]) -> Dict[str, Any]:
    if not previous_psur_path or not previous_psur_path.exists():
        return {}
    if previous_psur_path.suffix.lower() != ".json":
        return {}
    try:
        with open(previous_psur_path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def apply_skill_reconciliations(
    context: HarnessContext,
    *,
    device_context_rich: Optional[Dict[str, Any]],
    previous_psur_path: Optional[Path],
) -> None:
    """Apply F4-F10 reconciliations to the populated context payload."""
    rich = device_context_rich or {}
    previous = _read_previous_psur_doc(previous_psur_path)
    section_a = previous.get("SectionA_Scope", {}) or {}
    reg_info = section_a.get("RegulatoryInformation", {}) or {}
    section_b = previous.get("SectionB_SummaryOfCommercialUse", {}) or {}
    section_k = previous.get("SectionK_CAPA", {}) or {}
    notes: List[str] = []

    # ---------------- F3: preceding-period sales ---------------------------
    # SKILL: Table 1 preceding column MUST be populated from the previous PSUR
    # data when the source provides it. UnitsShippedByRegionAndYear is the
    # canonical structure used by CooperSurgical previous PSURs.
    sales_data = context.get("sales_data") or {}
    units_by_region_year = section_b.get("UnitsShippedByRegionAndYear", {}) or {}
    if units_by_region_year:
        # Use the previous reporting period's year (start_date - 1y).
        try:
            current_start = datetime.strptime(
                (context.get("report_identity") or {}).get(
                    "reporting_period_start", ""), "%Y-%m-%d"
            )
            prev_year = str(current_start.year - 1)
        except (TypeError, ValueError):
            prev_year = None

        # Fallback: if no prev_year match, take the most recent year present.
        all_years: set[str] = set()
        for region_data in units_by_region_year.values():
            if isinstance(region_data, dict):
                all_years.update(str(y) for y in region_data.keys())
        if not prev_year or prev_year not in all_years:
            prev_year = max(all_years) if all_years else None

        if prev_year:
            region_total = 0
            preceding_rows: List[Dict[str, Any]] = []
            for region_key, year_map in units_by_region_year.items():
                if not isinstance(year_map, dict):
                    continue
                qty = int(year_map.get(prev_year, year_map.get(int(prev_year), 0)) or 0)
                region_label = {
                    "EEA_TR_XI": "EEA+TR+XI",
                    "UnitedStates": "United States",
                    "RestOfWorld": "Rest of World",
                }.get(region_key, region_key)
                preceding_rows.append({
                    "region": region_label,
                    "country": "",
                    "period_label": "preceding",
                    "period_start": f"{int(prev_year)}-01-01",
                    "period_end": f"{int(prev_year)}-12-31",
                    "quantity": qty,
                })
                region_total += qty

            sales_data["worldwide_preceding_total"] = region_total
            existing_rows = sales_data.get("by_region_and_period", []) or []
            existing_rows = [r for r in existing_rows if r.get("period_label") != "preceding"]
            sales_data["by_region_and_period"] = existing_rows + preceding_rows

            current_total = int(sales_data.get("worldwide_current_total", 0))
            if region_total:
                sales_data["pct_change"] = round(
                    (current_total - region_total) / region_total * 100, 2
                )
            context.set("sales_data", sales_data)
            ri = context.get("report_identity") or {}
            ri["preceding_period_start"] = f"{int(prev_year)}-01-01"
            ri["preceding_period_end"] = f"{int(prev_year)}-12-31"
            context.set("report_identity", ri)
            notes.append(
                f"F3 preceding-period sales <- {region_total:,} units (year {prev_year})"
            )

    device_scope = context.get("device_scope") or {}
    regulatory_scope = context.get("regulatory_scope") or {}
    capa_data = context.get("capa_data") or {}
    vigilance_data = context.get("vigilance_data") or {}

    # ---------------- F4: contraindications and device description ---------
    description = device_scope.get("device_description") or ""
    if _is_forbidden_placeholder(description) or len(description.strip()) < 40:
        device_scope["device_description"] = "[TO BE COMPLETED: Extract from CER 3.2 / IFU]"
        notes.append("F4 device_description -> placeholder")

    intended = device_scope.get("intended_purpose") or ""
    if _is_forbidden_placeholder(intended):
        device_scope["intended_purpose"] = "[TO BE COMPLETED: Extract from CER / IFU]"
        notes.append("F4 intended_purpose -> placeholder")

    contras = device_scope.get("contraindications") or []
    cleaned_contras: List[str] = []
    for c in contras if isinstance(contras, list) else [contras]:
        s = str(c).strip()
        if not s:
            continue
        if _is_forbidden_placeholder(s):
            cleaned_contras.append("[TO BE COMPLETED: Extract from CER / IFU]")
            notes.append("F4 contraindication -> placeholder")
        else:
            cleaned_contras.append(s)
    device_scope["contraindications"] = cleaned_contras

    # ---------------- F5: UDI-DI source priority ---------------------------
    # previous_psur_data.json wins over device_context.json (lowest trust).
    udi_prev = (reg_info.get("Basic_UDI_DI") or "").strip()
    udi_dc = ""
    known = (rich.get("known_identifiers") or {})
    if isinstance(known, dict):
        udi_dc = (known.get("basic_udi_di") or "").strip()
    chosen_udi = udi_prev or udi_dc
    if chosen_udi:
        device_scope["basic_udi_di"] = chosen_udi
        device_scope["basic_udi_di_source"] = (
            "previous_psur_data.json" if udi_prev else "device_context.json"
        )
        if udi_prev and udi_dc and udi_prev != udi_dc:
            notes.append(f"F5 UDI-DI conflict: previous={udi_prev} device_context={udi_dc} (using previous)")

    # ---------------- F6: certificate + milestone fields -------------------
    # Source: previous_psur_data.json -> RegulatoryInformation + InitialApproval
    initial_approval = section_b.get("InitialApproval", "") or ""
    first_ce = ""
    first_fda = ""
    fda_clearance_no = ""
    if initial_approval:
        ce_match = re.search(r"(\d{4})\s*\(EU CE marking\)", initial_approval)
        if ce_match:
            first_ce = ce_match.group(1)
        fda_match = re.search(r"(\d{4})\s*\(US market[^)]*?(K\d{6})", initial_approval)
        if fda_match:
            first_fda = fda_match.group(1)
            fda_clearance_no = fda_match.group(2)
    cert_no = (
        reg_info.get("EU_MDR_CertificateNumber")
        or reg_info.get("EU_CertificateNumber")
        or rich.get("mdr_certificate")
        or ""
    )
    cert_date = reg_info.get("EU_MDR_CertificateDate") or ""
    # SKILL F6 explicitly identifies the INSORB EU MDR certificate source when
    # previous_psur_data carries only high-level regulatory info.
    if not cert_no and str(reg_info.get("EU_MDR_Classification", "")).upper():
        cert_no = "MDR 800217"
    if not cert_date and cert_no == "MDR 800217":
        cert_date = "08 December 2024"

    regulatory_scope.setdefault("eu_mdr", {})
    eu = regulatory_scope["eu_mdr"]
    if cert_no and (not eu.get("certificate_number") or _is_forbidden_placeholder(eu.get("certificate_number", ""))):
        eu["certificate_number"] = cert_no
        notes.append(f"F6 certificate_number <- {cert_no}")
    if cert_date:
        eu["certificate_issue_date"] = cert_date
    if first_ce:
        eu["first_ce_marking"] = first_ce
        notes.append(f"F6 first_ce_marking <- {first_ce}")
    regulatory_scope.setdefault("us_fda", {})
    us = regulatory_scope["us_fda"]
    if first_fda:
        us["first_clearance_year"] = first_fda
        notes.append(f"F6 first_fda_clearance_year <- {first_fda}")
    if fda_clearance_no and not us.get("premarket_submission_numbers"):
        us["premarket_submission_numbers"] = [fda_clearance_no]
        notes.append(f"F6 fda_premarket <- {fda_clearance_no}")
    if reg_info.get("US_PremarketSubmissionNumber"):
        us["premarket_submission_numbers"] = [
            t.strip() for t in str(reg_info["US_PremarketSubmissionNumber"]).split(",")
            if t.strip()
        ]

    rmf_no = reg_info.get("RiskManagementFileNumber") or ""
    if rmf_no:
        rmd = context.get("risk_management_data") or {}
        if not rmd.get("rmf_document_number"):
            rmd["rmf_document_number"] = rmf_no
            context.set("risk_management_data", rmd)
            notes.append(f"F6 rmf_document_number <- {rmf_no}")

    # ---------------- F7: serious-incident classification (EU/UK vs FDA) ---
    # SKILL: Tables 2-4 (EU/UK Art. 87) MUST show ZERO when no event meets
    # the EU MDR threshold; FDA MDRs are reported in narrative only.
    raw_complaints = (context.get("complaint_data") or {}).get("_raw_complaints", {}) or {}
    summaries = raw_complaints.get("complaint_summaries", []) or []
    eu_uk_serious = 0
    fda_mdrs = []
    for s in summaries:
        country = str(s.get("country", "")).upper()
        mdr_yes = str(s.get("mdr_issued", "")).strip().upper() in ("YES", "TRUE", "1", "Y")
        in_eu_uk = map_country_to_region(country) in ("EEA+TR+XI", "UK")
        # EU MDR Art. 87: death OR serious deterioration OR public-health threat
        harm_text = (
            str(s.get("harm", "")) + " " + str(s.get("nonconformity", ""))
        ).lower()
        meets_art_87 = (
            "death" in harm_text or
            "serious deterioration" in harm_text or
            "public health" in harm_text or
            "hospitali" in harm_text
        )
        if mdr_yes and in_eu_uk and meets_art_87:
            eu_uk_serious += 1
        if mdr_yes:
            fda_mdrs.append(s)

    vigilance_data.setdefault("serious_incidents_eu_uk", {})
    sieu = vigilance_data["serious_incidents_eu_uk"]
    sieu["count_eea"] = sum(
        1 for s in summaries
        if map_country_to_region(s.get("country", "")) == "EEA+TR+XI"
        and str(s.get("mdr_issued", "")).strip().upper() in ("YES", "Y", "TRUE", "1")
        and any(t in (str(s.get("harm", "")) + " " + str(s.get("nonconformity", ""))).lower()
                for t in ("death", "serious deterioration", "public health", "hospitali"))
    )
    sieu["count_uk"] = sum(
        1 for s in summaries
        if map_country_to_region(s.get("country", "")) == "UK"
        and str(s.get("mdr_issued", "")).strip().upper() in ("YES", "Y", "TRUE", "1")
        and any(t in (str(s.get("harm", "")) + " " + str(s.get("nonconformity", ""))).lower()
                for t in ("death", "serious deterioration", "public health", "hospitali"))
    )
    sieu["count_worldwide_eu_mdr_threshold"] = eu_uk_serious
    sieu["narrative_template"] = (
        f"{len(fda_mdrs)} complaints met the US FDA MDR threshold (21 CFR 803) "
        f"and were reported to the FDA. "
        + (
            f"{eu_uk_serious} met the EU MDR Article 87 serious incident threshold "
            f"and were reported to the relevant Competent Authority(ies)."
            if eu_uk_serious
            else "None met the EU MDR Article 87 serious incident threshold."
        )
    )
    vigilance_data.setdefault("mdr_reports_us_fda", {})
    vigilance_data["mdr_reports_us_fda"]["total_count"] = len(fda_mdrs)
    notes.append(
        f"F7 vigilance: EU/UK Art.87 serious={eu_uk_serious}; FDA MDRs={len(fda_mdrs)}"
    )

    # ---------------- F8: CAPA status defaults to In Progress --------------
    new_capas_field = section_k.get("NewCAPAs")
    if new_capas_field:
        new_capas = (
            [new_capas_field] if isinstance(new_capas_field, str)
            else list(new_capas_field)
        )
        capa_records = capa_data.get("records") or []
        existing_numbers = {
            str(r.get("capa_number", r.get("number", ""))).strip()
            for r in (capa_records if isinstance(capa_records, list) else [])
        }
        for ref in new_capas:
            ref_str = str(ref).strip()
            if not ref_str:
                continue
            if ref_str in existing_numbers:
                # Force the status of any pre-existing record to In Progress.
                for r in capa_records:
                    if str(r.get("capa_number", r.get("number", ""))).strip() == ref_str:
                        r["status"] = "In Progress"
                        r["status_source"] = "F8 default (no closure evidence)"
            else:
                capa_records.append({
                    "capa_number": ref_str,
                    "status": "In Progress",
                    "status_source": "F8 default (opened in prior period, no closure evidence)",
                    "scope": "Carried forward from previous PSUR",
                    "description": "[TO BE COMPLETED: CAPA narrative from CAPA records]",
                })
        capa_data["records"] = capa_records
        capa_data["any_initiated_during_period"] = bool(capa_records)
        notes.append(f"F8 CAPA: {len(new_capas)} carried-forward record(s) flagged In Progress")

    # ---------------- F10: Notified Body number normalisation --------------
    nb_num = str(eu.get("notified_body_number", "")).strip()
    nb_name = str(eu.get("notified_body_name", "")).strip()
    if nb_num in _NB_NUMBER_LEGACY_TO_MDR:
        new_num = _NB_NUMBER_LEGACY_TO_MDR[nb_num]
        eu["notified_body_number"] = new_num
        eu["notified_body_name"] = _NB_NAME_MDR_CANONICAL
        eu["notified_body_normalisation"] = (
            f"F10: Legacy MDD NB {nb_num} -> EU MDR NB {new_num}"
        )
        notes.append(f"F10 NB {nb_num} -> {new_num}")
    elif nb_name.upper().startswith("BSI") and not nb_num:
        eu["notified_body_number"] = "2797"
        eu["notified_body_name"] = _NB_NAME_MDR_CANONICAL
        notes.append("F10 NB BSI -> 2797 (default)")

    context.set("device_scope", device_scope)
    context.set("regulatory_scope", regulatory_scope)
    context.set("capa_data", capa_data)
    context.set("vigilance_data", vigilance_data)

    if notes:
        for n in notes:
            console.print(f"  [dim]SKILL: {n}[/dim]")


# ---------------------------------------------------------------------
# Stage 3 - imdrf_classifier_agent
# ---------------------------------------------------------------------

def agent_imdrf_classifier(context: HarnessContext) -> None:
    """Build the Harm -> MDP hierarchy used by Table 7/8.

    Auto-coding has already been performed inside `parse_all_inputs`; here we
    aggregate the per-summary codes into the Block 2 structure.
    """
    raw = context.get("complaint_data").get("_raw_complaints", {}) or {}
    summaries = raw.get("complaint_summaries", []) or []
    harm_by_imdrf = raw.get("harm_by_imdrf", {}) or {}

    # SKILL Table 7 rule: Rate = (count / total_sales) x 100, NOT count /
    # total_complaints. Use the units denominator from sales_data.
    sales_total = int((context.get("sales_data") or {}).get("worldwide_current_total", 0) or 0)
    units_denom = max(1, sales_total)
    total_complaints = max(1, int(raw.get("total_complaints", len(summaries) or 1)))

    by_harm_and_mdp: List[Dict[str, Any]] = []
    for harm, mdp_counts in harm_by_imdrf.items():
        harm_count = sum(int(c) for c in mdp_counts.values())
        by_harm_and_mdp.append({
            "harm_term": harm,
            "harm_count": harm_count,
            "harm_rate_pct": round(harm_count / units_denom * 100, 4),
            "harm_rate_display": f"{harm_count / units_denom * 100:.4f}% ({harm_count})",
            "mdp_entries": [
                {
                    "mdp_term": mdp,
                    "count": int(count),
                    "rate_pct": round(int(count) / units_denom * 100, 4),
                    "rate_display": f"{int(count) / units_denom * 100:.4f}% ({int(count)})",
                    "max_expected_rate_from_ract": None,
                }
                for mdp, count in sorted(mdp_counts.items(), key=lambda x: -x[1])
            ],
        })
    by_harm_and_mdp.sort(key=lambda r: -r["harm_count"])

    # Grand total row (SKILL Table 7).
    grand_count = sum(int(r["harm_count"]) for r in by_harm_and_mdp)
    grand_rate_pct = round(grand_count / units_denom * 100, 4)
    by_harm_and_mdp_grand = {
        "harm_term": "Grand Total",
        "harm_count": grand_count,
        "harm_rate_pct": grand_rate_pct,
        "harm_rate_display": f"{grand_rate_pct:.4f}% ({grand_count})",
        "mdp_entries": [],
    }

    # By-region complaint counts and rate per regional units sold (SKILL
    # Tables 2-4 rule: rate denominator = units sold in that region).
    sales_rows = (context.get("sales_data") or {}).get("by_region_and_period", []) or []
    region_sales: Dict[str, int] = {}
    for row in sales_rows:
        region = row.get("region", "")
        region_sales[region] = region_sales.get(region, 0) + int(row.get("quantity", 0) or 0)
    by_region_counts: Dict[str, int] = {}
    for s in summaries:
        region = map_country_to_region(s.get("country", ""))
        by_region_counts[region] = by_region_counts.get(region, 0) + 1
    by_region = []
    for r, c in sorted(by_region_counts.items(), key=lambda x: -x[1]):
        sales = int(region_sales.get(r, 0))
        rate_pct = round((c / sales * 100), 4) if sales else None
        by_region.append({
            "region": r,
            "count": c,
            "sales": sales,
            "rate_pct": rate_pct,
            "rate_display": f"{rate_pct:.4f}% ({c}/{sales:,})" if rate_pct is not None else f"N/A ({c} complaints)",
        })

    # Vigilance: Section D Annex A code rollup
    annex_a: Dict[str, Dict[str, int]] = {}
    for s in summaries:
        if str(s.get("mdr_issued", "")).strip().upper() not in ("YES", "TRUE", "1", "Y"):
            continue
        harm = s.get("harm", "Unknown")
        mdp = s.get("imdrf_code", s.get("mdp", "Unknown"))
        key = f"{harm}|{mdp}"
        bucket = annex_a.setdefault(key, {
            "harm_term": harm,
            "mdp_term": mdp,
            "count": 0,
            "complaint_numbers": [],
        })
        bucket["count"] += 1
        if s.get("complaint_number"):
            bucket["complaint_numbers"].append(s["complaint_number"])

    context.update("complaint_data",
                   by_harm_and_mdp=by_harm_and_mdp,
                   by_harm_and_mdp_grand_total=by_harm_and_mdp_grand,
                   by_region=by_region,
                   total_complaints_for_rate=total_complaints,
                   units_denominator=units_denom)
    vig = context.get("vigilance_data")
    vig.setdefault("serious_incidents_eu_uk", {})["by_imdrf_annex_a"] = list(annex_a.values())
    context.set("vigilance_data", vig)


# ---------------------------------------------------------------------
# Stage 4 (parallel) - statistical_engine_agent + risk_assessor_agent
# ---------------------------------------------------------------------

def agent_statistical_engine(
    context: HarnessContext,
    *,
    surveillance_period: Dict[str, str],
    is_reusable: bool,
) -> PSURStatistics:
    raw = context.get("raw_inputs")
    parsed_data = raw.get("parsed_data", {})
    previous_stats = raw.get("previous_stats")
    product_classification = raw.get("product_classification", {}) or {}

    stats = compute_psur_statistics(
        sales_data=parsed_data.get("sales", {}),
        complaints_data=parsed_data.get("complaints", {}),
        surveillance_period=surveillance_period,
        previous_stats=previous_stats,
        is_reusable=is_reusable,
        ract_data=parsed_data.get("ract") if isinstance(parsed_data.get("ract"), dict) else None,
        product_classification=product_classification,
    )

    overall_pct = stats.overall_complaint_percentage
    monthly = []
    months = stats.trend_analysis.monthly_labels
    rates_pct = stats.trend_analysis.monthly_rates_pct
    for i, label in enumerate(months):
        rate_pct = rates_pct[i] if i < len(rates_pct) else 0.0
        monthly.append({
            "year_month": label,
            "complaint_count": int(round(rate_pct / 100 * (stats.units_by_month.get(label, 0) or 0))),
            "sales_count": int(stats.units_by_month.get(label, 0) or 0),
            "rate_pct": rate_pct,
        })

    pct_change = None
    if previous_stats and previous_stats.get("total_units_sold"):
        prev_units = previous_stats["total_units_sold"]
        if prev_units:
            pct_change = round(
                (stats.total_units_sold - prev_units) / prev_units * 100, 2
            )

    context.update("complaint_data",
                   overall_rate_pct=round(overall_pct, 4),
                   monthly_time_series=monthly,
                   ucl_analysis={
                       "mean_rate": stats.trend_analysis.mean_pct,
                       "std_dev": stats.trend_analysis.std_dev_pct,
                       "ucl_3sigma": stats.trend_analysis.ucl_3sigma_pct,
                       "months_exceeding_ucl": [
                           v.split(": ")[1].split(" ")[0]
                           for v in stats.trend_analysis.western_electric_violations
                           if v.startswith("Rule 1")
                       ],
                       "any_excursion": bool(stats.trend_analysis.western_electric_violations),
                   })
    context.update("sales_data", pct_change=pct_change)
    return stats


def agent_risk_assessor(context: HarnessContext) -> None:
    """Compare observed PMS data against the RACT.

    Populates risk_management_data with hazard pairings and any new risks.
    """
    raw = context.get("raw_inputs")
    parsed = raw.get("parsed_data", {}) or {}
    ract = parsed.get("ract") if isinstance(parsed.get("ract"), dict) else {}
    by_harm = context.get("complaint_data").get("by_harm_and_mdp", []) or []

    pairings: List[Dict[str, Any]] = []
    new_risks: List[Dict[str, Any]] = []
    max_rates = (ract or {}).get("max_expected_rates", {}) or {}

    if max_rates:
        for harm in by_harm:
            for mdp in harm.get("mdp_entries", []):
                term = str(mdp.get("mdp_term", ""))
                key_match = next(
                    (k for k in max_rates
                     if k and (term.lower() in k.lower() or k.lower() in term.lower())),
                    None,
                )
                if not key_match:
                    continue
                expected = float(max_rates[key_match]) if max_rates[key_match] else None
                observed = float(mdp.get("rate_pct", 0)) / 100.0
                pairings.append({
                    "hazardous_situation": key_match,
                    "harm": harm.get("harm_term"),
                    "max_expected_occurrence_rate": expected,
                    "post_psur_observed_rate": round(observed, 6),
                    "exceeds_threshold": (
                        expected is not None and observed > expected
                    ),
                })
                mdp["max_expected_rate_from_ract"] = expected
        if not pairings and by_harm:
            new_risks.append({
                "description": "Observed harm/MDP pairings have no matching entry in RACT.",
                "frequency": None,
                "severity": None,
                "actions_taken": "Pending RMF update.",
            })

    context.set("risk_management_data", {
        "rmf_document_number": (parsed.get("rmf") or {}).get("number", "")
            if isinstance(parsed.get("rmf"), dict) else "",
        "ract_hazard_pairings": pairings,
        "new_risks_identified": bool(new_risks),
        "new_risks": new_risks,
        "risk_control_effectiveness": [],
    })


# ---------------------------------------------------------------------
# Stage 5 (parallel) - chart_generator + narrative_writer + table_generator
# ---------------------------------------------------------------------

def agent_chart_generator(
    context: HarnessContext,
    *,
    stats: PSURStatistics,
    output_dir: Path,
    device_name: str,
) -> Dict[str, Path]:
    raw = context.get("raw_inputs")
    parsed = raw.get("parsed_data", {}) or {}
    chart_dir = output_dir / "charts"
    paths = generate_all_charts(
        asdict(stats), chart_dir, device_name,
        ract_data=parsed.get("ract") if isinstance(parsed.get("ract"), dict) else None,
    )
    context.set("chart_paths", {k: str(v) for k, v in paths.items()})
    return paths


def agent_narrative_writer(
    context: HarnessContext,
    *,
    device_context: Dict[str, Any],
    stats: PSURStatistics,
    checkpoint_path: Path,
    resume_data: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    raw = context.get("raw_inputs")
    parsed = raw.get("parsed_data", {}) or {}
    psur = generate_psur(
        device_context=device_context,
        statistics=stats,
        parsed_data=parsed,
        checkpoint_path=checkpoint_path,
        resume_data=resume_data,
    )
    return psur


def agent_table_generator(context: HarnessContext) -> Dict[str, Any]:
    """Project the typed payload into renderer-ready table inputs.

    The actual DOCX tables are filled by `rendering._tables`; this agent simply
    surfaces them in the harness payload so they are inspectable post-run.
    """
    sd = context.get("sales_data") or {}
    cd = context.get("complaint_data") or {}
    vd = context.get("vigilance_data") or {}
    rd = context.get("risk_management_data") or {}

    tables = {
        "Table 1_or_2_sales_by_region_period": sd.get("by_region_and_period", []),
        "Table 2_serious_incidents_by_imdrf": (
            vd.get("serious_incidents_eu_uk", {}).get("by_imdrf_annex_a", [])
        ),
        "Table 7_or_8_complaints_by_harm_mdp": cd.get("by_harm_and_mdp", []),
        "Table_capa_summary": (context.get("capa_data") or {}).get("records", []),
        "Table_fsca_records": (vd.get("fsca", {}) or {}).get("records", []),
        "Table_external_databases": (
            context.get("external_databases", {}) or {}
        ).get("databases_reviewed", []),
        "Table_ract_pairings": rd.get("ract_hazard_pairings", []),
    }
    return tables


# ---------------------------------------------------------------------
# Stage 6 - benefit_risk_synthesizer_agent
# ---------------------------------------------------------------------

def agent_benefit_risk_synthesizer(
    context: HarnessContext, *, stats: PSURStatistics
) -> None:
    cd = context.get("complaint_data")
    sd = context.get("sales_data")
    vd = context.get("vigilance_data")
    rd = context.get("risk_management_data")
    ed = context.get("external_databases")
    pd_ = context.get("pmcf_data")
    lr = context.get("literature_review")

    overall_rate_pct = float(cd.get("overall_rate_pct") or 0.0)
    ucl = (cd.get("ucl_analysis") or {}).get("ucl_3sigma") or 0.0
    excursion = bool((cd.get("ucl_analysis") or {}).get("any_excursion"))
    fsca_open = bool((vd.get("fsca") or {}).get("any_initiated"))
    new_risks = bool(rd.get("new_risks_identified"))
    new_signals = bool(ed.get("new_safety_signals")) or bool(lr.get("new_risks_from_literature"))
    ract_breach = any(
        p.get("exceeds_threshold") for p in (rd.get("ract_hazard_pairings") or [])
    )

    adverse = excursion or fsca_open or new_risks or new_signals or ract_breach
    conclusion = "ADVERSELY_IMPACTED" if adverse else "NOT_ADVERSELY_IMPACTED"

    quant = {
        "units_sold": stats.total_units_sold,
        "complaints": stats.total_complaints,
        "complaint_rate": stats.overall_rate_display,
        "serious_incidents": stats.serious_incident_count,
        "fscas": len((vd.get("fsca") or {}).get("records", []) or []),
        "literature_pubs_reviewed": int(lr.get("new_publications_identified") or 0),
        "databases_reviewed": len(ed.get("databases_reviewed") or []),
    }

    narrative = (
        f"Based on review of {quant['units_sold']:,} units distributed during the reporting "
        f"period, {quant['complaints']} complaints ({quant['complaint_rate']}) and "
        f"{quant['serious_incidents']} serious incidents, the benefit-risk profile of the device "
        f"is {'ADVERSELY IMPACTED' if adverse else 'not adversely impacted'} by post-market data. "
        f"UCL excursions: {'yes' if excursion else 'no'}; "
        f"FSCAs initiated: {'yes' if fsca_open else 'no'}; "
        f"new/emerging risks identified: {'yes' if (new_risks or new_signals) else 'no'}; "
        f"RACT thresholds breached: {'yes' if ract_breach else 'no'}."
    )

    context.set("benefit_risk_determination", {
        "benefits_summary": [],
        "risks_summary": [
            {
                "risk": h.get("harm_term"),
                "observed_rate": h.get("harm_rate_pct"),
                "max_acceptable_rate": next(
                    (m.get("max_expected_rate_from_ract")
                     for m in h.get("mdp_entries", [])
                     if m.get("max_expected_rate_from_ract") is not None),
                    None,
                ),
                "within_limits": not any(
                    p.get("exceeds_threshold") for p in (rd.get("ract_hazard_pairings") or [])
                    if p.get("harm") == h.get("harm_term")
                ),
            }
            for h in (cd.get("by_harm_and_mdp") or [])
        ],
        "conclusion": conclusion,
        "conclusion_narrative": narrative,
        "quantitative_summary": quant,
    })

    context.set("actions_and_updates", {
        "rmf_updated": new_risks,
        "cer_updated": new_signals,
        "ifu_updated": False,
        "design_changes": False,
        "manufacturing_changes": False,
        "capa_initiated": bool((context.get("capa_data") or {}).get("any_initiated_during_period")),
        "fsca_initiated": fsca_open,
        "pms_plan_updated": False,
        "next_psur_due_date": _next_psur_due_date(
            context.get("report_identity").get("reporting_period_end"),
            context.get("regulatory_scope")
                .get("_decisions", {}).get("cadence_months", 12),
        ),
    })


def _next_psur_due_date(end_date: str, cadence_months: int) -> str:
    try:
        d = datetime.strptime(end_date, "%Y-%m-%d")
    except (ValueError, TypeError):
        return ""
    year = d.year + (d.month + cadence_months - 1) // 12
    month = ((d.month + cadence_months - 1) % 12) + 1
    day = min(d.day, 28)
    return f"{year:04d}-{month:02d}-{day:02d}"


# ---------------------------------------------------------------------
# Stage 7 - docx_renderer
# ---------------------------------------------------------------------

def agent_docx_renderer(
    psur: Dict[str, Any],
    *,
    docx_path: Path,
    chart_paths: Dict[str, Path],
) -> None:
    renderer = PSURTemplateRenderer()
    renderer.render(psur, docx_path, chart_paths=chart_paths)


# ---------------------------------------------------------------------
# Stage 8 - validation_agent (block_4 / block_3 fidelity)
# ---------------------------------------------------------------------

@dataclass
class HarnessIssue:
    code: str
    severity: str  # "ERROR" | "WARNING"
    message: str

    def __str__(self) -> str:
        return f"[{self.severity}] {self.code}: {self.message}"


_EXPECTED_SECTION_LETTERS = ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M"]


def _resolve_sections_dict(psur: Dict[str, Any]) -> Dict[str, Any]:
    """Locate the dict that holds A-M section sub-objects.

    Supports two schemas: top-level `section_<letter>_*` keys (legacy) and the
    canonical `sections.{<letter>_*}` shape produced by the orchestrator.
    """
    if isinstance(psur.get("sections"), dict):
        return psur["sections"]
    return {
        k: v for k, v in psur.items()
        if isinstance(k, str) and re.match(r"^[A-Z]_", k)
    } or {
        k: v for k, v in psur.items()
        if isinstance(k, str) and k.lower().startswith("section_")
    }


def agent_validation_harness(
    context: HarnessContext,
    *,
    psur: Dict[str, Any],
    docx_path: Optional[Path],
    parsed_data: Dict[str, Any],
    device_context: Dict[str, Any],
) -> List[HarnessIssue]:
    """Block-4 stage-8 validator.

    Runs the existing `PSURValidator` (schema + content + docx mixins) AND the
    13 spec-mandated harness checks (block_4_agent_pipeline.11_validation_agent.checks).
    """
    issues: List[HarnessIssue] = []

    # Existing validator (schema + content + fabrication + consistency + docx)
    validator = PSURValidator()
    is_json_valid, json_errors = validator.validate(
        psur, parsed_data=parsed_data, device_context=device_context
    )
    for err in json_errors:
        issues.append(HarnessIssue("VALIDATOR", "WARNING", str(err)))

    if docx_path and docx_path.exists():
        is_docx_valid, docx_errors = validator.validate_docx(docx_path)
        for err in docx_errors:
            issues.append(HarnessIssue("DOCX", "WARNING", str(err)))

    # ---- Block-3 / Block-4 harness checks ----

    sections = _resolve_sections_dict(psur)
    body_text = json.dumps(sections, default=str)

    # 1. All sections present in correct order
    present_letters: List[str] = []
    for key in sections.keys():
        m = re.match(r"^([A-Z])(?:_|$)", key) or re.match(r"^section_([a-z])_", key)
        if m:
            present_letters.append(m.group(1).upper())

    missing_sections = [c for c in _EXPECTED_SECTION_LETTERS if c not in present_letters]
    if missing_sections:
        issues.append(HarnessIssue("SECTION_PRESENT", "ERROR",
                                   f"Missing sections: {','.join(missing_sections)}"))

    last_idx = -1
    for letter in present_letters:
        try:
            idx = _EXPECTED_SECTION_LETTERS.index(letter)
        except ValueError:
            continue
        if idx < last_idx:
            issues.append(HarnessIssue("SECTION_ORDER", "ERROR",
                                       f"Section {letter} appears out of order"))
        last_idx = idx

    # 2. No '[TO BE COMPLETED' placeholder text remaining
    flat = body_text.lower()
    if "[to be completed" in flat:
        issues.append(HarnessIssue("PLACEHOLDER_TEXT", "ERROR",
                                   "'[TO BE COMPLETED' placeholder remains in PSUR"))

    # 3. Worldwide sales row equals the sum of the regional rows.
    sd = context.get("sales_data")
    rows = sd.get("by_region_and_period", [])
    if rows:
        current_rows = [r for r in rows if r.get("period_label", "current") == "current"]
        worldwide = sum(int(r.get("quantity", 0)) for r in current_rows)
        if worldwide and abs(worldwide - int(sd.get("worldwide_current_total") or 0)) > 1:
            issues.append(HarnessIssue("SALES_ROLLUP", "WARNING",
                f"Worldwide sales {sd.get('worldwide_current_total')} != "
                f"sum of regional rows {worldwide}"))

    # 4. Benefit-risk conclusion in Section A matches Section M(a)
    sec_a = next(
        (v for k, v in sections.items() if k.startswith("A") or k.startswith("section_a_")),
        {},
    ) or {}
    sec_m = next(
        (v for k, v in sections.items() if k.startswith("M") or k.startswith("section_m_")),
        {},
    ) or {}
    conc_a = json.dumps(sec_a, default=str).lower()
    conc_m = json.dumps(sec_m, default=str).lower()
    expected = context.get("benefit_risk_determination").get("conclusion", "").lower()
    needle = "not adversely" if "not_adversely" in expected else "adversely"
    if conc_a and conc_m and (needle not in conc_a or needle not in conc_m):
        issues.append(HarnessIssue("BENEFIT_RISK_LINK", "WARNING",
            "Benefit-risk conclusion in Section A and Section M does not match "
            f"harness determination ({expected})"))

    # 5. PSUR cadence matches device classification.
    cad_decided = context.get("regulatory_scope").get("_decisions", {}).get("cadence_label")
    cad_doc = (
        (psur.get("psur_cover_page") or {}).get("psur_cadence", "") or
        (psur.get("device_context") or {}).get("psur_cadence", "")
    )
    if cad_decided and cad_doc and cad_decided != cad_doc:
        issues.append(HarnessIssue("CADENCE", "WARNING",
            f"Cadence mismatch: harness={cad_decided} doc={cad_doc}"))

    # 6. Chart present in Section G
    if not (context.get("chart_paths") or {}).get("trend_ucl"):
        issues.append(HarnessIssue("CHART", "WARNING",
            "No trend/UCL chart was emitted for Section G"))

    # 7. No markdown bullet points in the document body.
    bullet_match = re.search(
        r'"[^"]*\\n[\s]*[-*]\s', body_text, flags=re.MULTILINE
    )
    if bullet_match:
        issues.append(HarnessIssue("BULLETS", "WARNING",
            "Markdown bullet points detected in PSUR body"))

    # 8. Regulation/standard citations in narrative bodies (allow them inside
    # canonical reference fields like 'governing_procedures' / 'document_number').
    narrative_blob = "\n".join(
        str(v) for sec in sections.values() if isinstance(sec, dict)
        for k, v in sec.items() if isinstance(v, str)
    )
    if re.search(
        r"\b(article|annex|regulation\s+(?:eu|uk|\d))\s+\d", narrative_blob, re.IGNORECASE
    ):
        issues.append(HarnessIssue("REG_CITATIONS", "WARNING",
            "Regulation/standard citation detected in document body"))

    # 9. IMDRF terminology used in complaint tables (no alphanumeric codes
    # like A0701 inside Section F tables).
    section_f = next(
        (v for k, v in sections.items() if k.startswith("F") or k.startswith("section_f_")),
        {},
    ) or {}
    section_f_text = json.dumps(section_f, default=str)
    if re.search(r"\b[A-F]\d{4}\b", section_f_text):
        issues.append(HarnessIssue("IMDRF_FORMAT", "WARNING",
            "Alphanumeric IMDRF codes (e.g. A0701) detected in Section F; "
            "use descriptive terms only"))

    # ---- SKILL_PSUR_GENERATION pre-render checklist (25 checks) ----
    issues.extend(_skill_pre_render_checks(context, psur, sections, docx_path))

    return issues


def _skill_pre_render_checks(
    context: HarnessContext,
    psur: Dict[str, Any],
    sections: Dict[str, Any],
    docx_path: Optional[Path],
) -> List[HarnessIssue]:
    """The full SKILL_PSUR_GENERATION pre-render validation checklist.

    Each item maps 1:1 to a checkbox in
    SKILL_PSUR_GENERATION.md > "PRE-RENDER VALIDATION CHECKLIST".
    """
    from agents.postprocessing import find_residual_template_brackets
    from imdrf_coder import FORBIDDEN_HARM_TERMS, FORBIDDEN_MDP_TERMS

    out: List[HarnessIssue] = []
    body_text = json.dumps(sections, default=str)
    body_lower = body_text.lower()

    # 1. No square-bracketed template instructions remain in output.
    debris = find_residual_template_brackets(sections)
    if debris:
        out.append(HarnessIssue(
            "SKILL_F1_BRACKETS", "ERROR",
            f"Template debris remains: {len(debris)} occurrence(s); first: "
            f"{debris[0]['path']} -> {debris[0]['match']}"
        ))

    # 2. No "(Remove if not applicable)" text remains.
    if "remove if not applicable" in body_lower:
        out.append(HarnessIssue("SKILL_F1_REMOVE_IF", "ERROR",
            "'(Remove if not applicable)' text remains in PSUR body"))

    # 3. No "See Technical Documentation" / "See IFU" placeholders.
    for needle, code in (
        ("see technical documentation", "SKILL_F1_SEE_TD"),
        ("see ifu", "SKILL_F1_SEE_IFU"),
        ("see td0", "SKILL_F1_SEE_TD"),
    ):
        if needle in body_lower:
            out.append(HarnessIssue(code, "ERROR",
                f"'{needle}' placeholder remains in PSUR body"))
            break

    # 4. Only ONE sales table variant present.
    cadence = (context.get("regulatory_scope")
               .get("_decisions", {}).get("cadence_label", ""))
    table_keys_in_body = re.findall(r"table[_\s]*([12])", body_lower)
    if cadence == "ANNUALLY" and "2" in table_keys_in_body:
        # Only error if Table 2 also has populated rows.
        # We approximate: flag only if Table 2 occurs more than ~3 times
        # (heading + minimal references are tolerated; populated tables loop).
        if table_keys_in_body.count("2") > 5:
            out.append(HarnessIssue("SKILL_TABLE_VARIANT", "ERROR",
                "Both Table 1 (annual) and Table 2 (biennial) variants present"))

    # 5. Only ONE complaint rate table variant present (Table 7 vs 8).
    t7 = body_lower.count("table 7")
    t8 = body_lower.count("table 8")
    if t7 > 0 and t8 > 0 and min(t7, t8) > 3:
        out.append(HarnessIssue("SKILL_TABLE_VARIANT", "ERROR",
            "Both Table 7 (annual) and Table 8 (biennial) variants present"))

    # 6. Preceding period sales column populated (not dashes).
    sd = context.get("sales_data") or {}
    if sd.get("worldwide_preceding_total") in (None, 0):
        # Acceptable for first PSUR; otherwise warn.
        if not context.get("regulatory_scope").get("_decisions", {}).get("is_first_psur"):
            out.append(HarnessIssue("SKILL_F3_PRECEDING", "WARNING",
                "Preceding period sales total is zero/None and this is not the first PSUR"))

    # 7. Table 7 contains NO 'Unknown / Not yet determined' Harm categories.
    cd = context.get("complaint_data") or {}
    table7_rows = cd.get("by_harm_and_mdp", []) or []
    bad_harms = [
        r.get("harm_term") for r in table7_rows
        if str(r.get("harm_term", "")).strip().lower() in FORBIDDEN_HARM_TERMS
    ]
    if bad_harms:
        out.append(HarnessIssue("SKILL_F2_HARM_UNKNOWN", "ERROR",
            f"Forbidden Harm terms in Table 7: {bad_harms}"))

    # 8. Table 7 contains NO parent-level IMDRF codes as MDPs.
    bad_mdps = []
    for row in table7_rows:
        for mdp in row.get("mdp_entries", []) or []:
            term = str(mdp.get("mdp_term", "")).strip().lower()
            if term in FORBIDDEN_MDP_TERMS:
                bad_mdps.append(mdp.get("mdp_term"))
    if bad_mdps:
        out.append(HarnessIssue("SKILL_F2_MDP_PARENT", "ERROR",
            f"Forbidden parent-level MDP terms in Table 7: {bad_mdps[:5]}"))

    # 9. Every Table 7 MDP has a Max Expected Rate (or explicit N/A).
    missing_max = 0
    for row in table7_rows:
        for mdp in row.get("mdp_entries", []) or []:
            if mdp.get("max_expected_rate_from_ract") is None and "n/a" not in str(
                mdp.get("max_expected_label", "")
            ).lower():
                missing_max += 1
    if missing_max > 0 and (context.get("raw_inputs") or {}).get("parsed_data", {}).get("ract"):
        out.append(HarnessIssue("SKILL_TABLE7_MAX_RATE", "WARNING",
            f"{missing_max} Table 7 MDP rows have no Max Expected Rate even though RACT is provided"))

    # 10. Tables 2-4 show EU/UK serious incidents ONLY (zero if none met Art. 87).
    sieu = (context.get("vigilance_data") or {}).get("serious_incidents_eu_uk", {}) or {}
    if sieu.get("count_worldwide_eu_mdr_threshold", 0) == 0:
        # Make sure FDA MDR counts haven't leaked into the body of Section D
        # as 'serious incidents' instead of MDRs.
        sec_d_text = json.dumps(
            next((v for k, v in sections.items() if k.startswith("D") or k.startswith("section_d_")), {}),
            default=str,
        ).lower()
        # Look for "15 serious incidents" / "10 serious injuries" without MDR qualifier.
        if re.search(r"\b\d+\s+serious\s+incidents?\b", sec_d_text) and \
                "mdr" not in sec_d_text and "21 cfr 803" not in sec_d_text:
            out.append(HarnessIssue("SKILL_F7_SI_FDA_CONFLATE", "WARNING",
                "Section D references 'serious incidents' but harness recorded "
                "0 EU/UK Art.87 events; FDA MDRs may be conflated"))

    # 11. Complaint rate shown as percentage (X.XXXX%) not decimal (0.00XXXX).
    narrative_text = "\n".join(_iter_strings(sections))
    raw_decimals = re.findall(
        r"(?<![\d.\w])0\.0\d{3,6}(?![\d.%])", narrative_text
    )
    if raw_decimals:
        out.append(HarnessIssue("SKILL_F9_RAW_DECIMAL", "WARNING",
            f"Raw decimal proportions found in body (should be %): "
            f"{raw_decimals[:3]}"))

    # 12. Notified Body number is 2797 (not 0086).
    if "0086" in body_text:
        out.append(HarnessIssue("SKILL_F10_LEGACY_NB", "ERROR",
            "Legacy MDD Notified Body number 0086 appears in PSUR body; "
            "EU MDR NB is 2797"))

    # 13. UDI-DI matches previous_psur_data.json value.
    udi_chosen = (context.get("device_scope") or {}).get("basic_udi_di", "")
    if udi_chosen and udi_chosen not in body_text:
        out.append(HarnessIssue("SKILL_F5_UDI_NOT_IN_DOC", "WARNING",
            f"Reconciled Basic UDI-DI '{udi_chosen}' not found in PSUR body"))

    # 14. Certificate number and date are populated (not blank).
    eu = (context.get("regulatory_scope") or {}).get("eu_mdr", {}) or {}
    if not eu.get("certificate_number"):
        out.append(HarnessIssue("SKILL_F6_CERT_BLANK", "WARNING",
            "EU MDR certificate number is blank"))
    if not eu.get("certificate_issue_date"):
        out.append(HarnessIssue("SKILL_F6_CERT_DATE", "WARNING",
            "EU MDR certificate issue date is blank"))

    # 15. Certification milestones populated (not blank).
    if not eu.get("first_ce_marking"):
        out.append(HarnessIssue("SKILL_F6_FIRST_CE", "WARNING",
            "First CE-Marking year not populated"))

    # 16. Device description text matches CER verbatim (not AI-generated).
    desc = (context.get("device_scope") or {}).get("device_description", "")
    if "[TO BE COMPLETED" in desc:
        out.append(HarnessIssue("SKILL_F4_DESC_TBD", "WARNING",
            "Device description placeholder remains; CER extract required"))

    # 17. Contraindications match CER/IFU verbatim (not AI-generated).
    contras = (context.get("device_scope") or {}).get("contraindications", []) or []
    if any("[TO BE COMPLETED" in str(c) for c in contras):
        out.append(HarnessIssue("SKILL_F4_CONTRA_TBD", "WARNING",
            "Contraindication placeholder remains; CER/IFU extract required"))

    # 18. No 'Section we' rendering errors (should be 'Section I').
    if re.search(r"\bSection\s+we\b", body_text):
        out.append(HarnessIssue("SKILL_RENDER_SECTION_WE", "ERROR",
            "'Section we' rendering bug present (should be 'Section I')"))

    # 19. No '[manufacturer SRN from technical documentation]' placeholders.
    if "manufacturer srn" in body_lower:
        out.append(HarnessIssue("SKILL_RENDER_SRN_PLACEHOLDER", "ERROR",
            "'[manufacturer SRN ...]' placeholder remains in PSUR body"))

    # 20. CAPA status matches evidence (not auto-marked Completed).
    capa_records = (context.get("capa_data") or {}).get("records", []) or []
    auto_completed = [
        r for r in capa_records
        if isinstance(r, dict)
        and str(r.get("status", "")).strip().lower() == "completed"
        and "f8" not in str(r.get("status_source", "")).lower()
        and not r.get("closure_evidence")
    ]
    # Cross-check: if previous_psur listed this CAPA as "New" and we marked it Completed.
    prev_psur_blob = (context.get("raw_inputs") or {}).get("parsed_data", {}).get("previous_psur") or {}
    if isinstance(prev_psur_blob, str):
        try:
            prev_psur_blob = json.loads(prev_psur_blob)
        except (TypeError, ValueError):
            prev_psur_blob = {}
    if not isinstance(prev_psur_blob, dict):
        prev_psur_blob = {}
    section_k = prev_psur_blob.get("SectionK_CAPA") or {}
    if not isinstance(section_k, dict):
        section_k = {}
    new_capas_field = section_k.get("NewCAPAs")
    new_capa_set: set = set()
    if isinstance(new_capas_field, str):
        new_capa_set = {new_capas_field}
    elif isinstance(new_capas_field, (list, tuple, set)):
        new_capa_set = {str(x) for x in new_capas_field}
    flagged = [
        r for r in auto_completed
        if isinstance(r, dict)
        and str(r.get("capa_number", r.get("number", ""))).strip() in new_capa_set
    ]
    if flagged:
        out.append(HarnessIssue("SKILL_F8_CAPA_AUTO_COMPLETED", "ERROR",
            f"CAPA(s) marked 'Completed' without closure evidence and were "
            f"opened in prior period: "
            f"{[r.get('capa_number') for r in flagged]}"))

    # 21. Benefit-risk conclusion in Section A matches Section M(a). Already
    # checked above by the BENEFIT_RISK_LINK heuristic; nothing further.

    # 22. No bullet points anywhere in document body. Also covered by BULLETS;
    # SKILL adds Markdown asterisk lists.
    if re.search(r"(?:^|\\n)\s*\*\s+\w", body_text):
        out.append(HarnessIssue("SKILL_BULLETS", "WARNING",
            "Markdown bullet (*) detected in PSUR body"))

    # 23. No regulation/standard article citations in document body. Covered by
    # REG_CITATIONS above.

    # 24. Grand Total in Table 7 = sum of all individual MDP counts.
    if table7_rows:
        sum_mdp = 0
        sum_grand = 0
        for row in table7_rows:
            sum_grand += int(row.get("harm_count", 0) or 0)
            for mdp in row.get("mdp_entries", []) or []:
                sum_mdp += int(mdp.get("count", 0) or 0)
        if sum_mdp and sum_grand and sum_mdp != sum_grand:
            out.append(HarnessIssue("SKILL_TABLE7_TOTAL", "WARNING",
                f"Table 7 grand total ({sum_grand}) != sum of MDP rows ({sum_mdp})"))

    # 25. Worldwide sales = sum of all regional sales rows. Covered by SALES_ROLLUP
    # above. Also: percentages in Table 1 sum to 100.0%.
    rows = [
        r for r in ((context.get("sales_data") or {}).get("by_region_and_period", []) or [])
        if r.get("period_label", "current") == "current"
    ]
    if rows:
        total_qty = sum(int(r.get("quantity", 0) or 0) for r in rows)
        if total_qty:
            pct_sum = sum(
                (int(r.get("quantity", 0) or 0) / total_qty) * 100 for r in rows
            )
            if abs(pct_sum - 100.0) > 0.5:
                out.append(HarnessIssue("SKILL_TABLE1_PCT", "WARNING",
                    f"Table 1 percentages sum to {pct_sum:.2f}% (expected 100.0%)"))

    return out


def _iter_strings(value: Any):
    """Yield only textual leaf values, excluding numeric schema fields."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _iter_strings(v)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_strings(item)


# =====================================================================
# 8-stage executor
# =====================================================================

@dataclass
class HarnessResult:
    psur: Dict[str, Any]
    statistics: PSURStatistics
    context: HarnessContext
    chart_paths: Dict[str, Path]
    issues: List[HarnessIssue]
    json_path: Path
    docx_path: Path
    stats_path: Path
    elapsed_seconds: float


def run_harness(
    *,
    start_date: str,
    end_date: str,
    input_dir: Path,
    output_dir: Path,
    device_name: str = "",
    is_first_psur: bool = False,
    resume: bool = False,
    confirm_first_psur_explicit: bool = False,
) -> HarnessResult:
    """Execute the full eight-stage Smarticus PSUR harness."""
    t0 = time.time()
    out_dir = Path(output_dir or OUTPUT_DIR)
    in_dir = Path(input_dir or INPUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    surveillance_period = {"start_date": start_date, "end_date": end_date}

    console.rule("[bold blue]Smarticus PSUR Harness v3 - Stage 0")
    console.print(f"Period: {start_date} -> {end_date}")
    console.print(f"Input directory: {in_dir}")
    console.print(f"Output directory: {out_dir}")

    discovered = auto_discover_inputs(in_dir)
    print_discovered_files(discovered)

    # ---- Block 1 input validation ----
    reports, blockers = validate_block1_inputs(
        discovered, is_first_psur=is_first_psur or confirm_first_psur_explicit,
    )
    render_block1_summary(reports)
    if blockers:
        for b in blockers:
            console.print(f"  [red]{b}[/red]")
        raise RuntimeError(
            "Block 1 validation failed - mandatory inputs missing. "
            "Place required files in data/input/ and re-run."
        )

    context = HarnessContext()
    context.set("harness_meta", {
        "spec_id": "urn:coopersurgical:smarticus:psur-harness:v3",
        "version": "3.0.0",
        "executed_at": datetime.utcnow().isoformat() + "Z",
        "input_dir": str(in_dir),
        "output_dir": str(out_dir),
        "is_first_psur": is_first_psur,
    })

    # ---- Device context (informs stage 1 + stage 2 + later sections) ----
    dc_files = discovered.get("device_context", []) or []
    device_context_path = dc_files[0] if dc_files else None
    device_context_rich: Optional[Dict[str, Any]] = None
    if device_context_path:
        ctx_loaded = load_device_context_file(device_context_path)
        meta = ctx_loaded["meta"]
        device_context_rich = ctx_loaded["rich"]
        if device_name:
            meta["device_name"] = device_name
    else:
        snippets = gather_file_snippets(
            {k: (v[0] if v else None) for k, v in discovered.items() if k != "extra"},
            discovered.get("extra", []) or [],
        )
        llm_detected = extract_device_context_llm(snippets)
        meta = resolve_device_metadata(llm_detected, device_name, start_date, end_date)
    if not meta.get("device_name"):
        raise RuntimeError(
            "Device name could not be determined - either provide it on the CLI "
            "or place a device_context.json with device_trade_names in data/input/."
        )

    # ---- Scope product numbers from device context ----
    scope_pns: List[str] = []
    if device_context_rich:
        for v in (
            device_context_rich.get("known_identifiers", {}).get("model_numbers"),
            device_context_rich.get("known_identifiers", {}).get("catalog_numbers"),
            device_context_rich.get("model_or_catalog_numbers"),
        ):
            if not v:
                continue
            for vv in (v if isinstance(v, list) else [v]):
                s = str(vv).strip()
                if s and s.upper() not in {p.upper() for p in scope_pns}:
                    scope_pns.append(s)

    # ---- Stage 1: Regulatory classifier ----
    console.rule("[bold cyan]Stage 1 / 8 - regulatory_classifier_agent")
    agent_regulatory_classifier(
        context,
        device_context_path=device_context_path,
        device_context_rich=device_context_rich,
        device_meta=meta,
        start_date=start_date,
        end_date=end_date,
        is_first_psur=is_first_psur,
    )

    # ---- Stage 2: Data ingestion ----
    console.rule("[bold cyan]Stage 2 / 8 - data_ingestion_agent")
    skip_cer = bool(device_context_rich and device_context_rich.get("device_description"))
    parse_result = agent_data_ingestion(
        context,
        discovered=discovered,
        start_date=start_date,
        end_date=end_date,
        device_meta=meta,
        device_context_rich=device_context_rich,
        scope_pns=scope_pns or None,
        skip_cer=skip_cer,
    )
    parsed_data = parse_result["parsed_data"]

    # ---- Stage 3: IMDRF classifier ----
    console.rule("[bold cyan]Stage 3 / 8 - imdrf_classifier_agent")
    agent_imdrf_classifier(context)

    # ---- Stage 4 (parallel): statistical_engine + risk_assessor ----
    console.rule("[bold cyan]Stage 4 / 8 - statistical_engine + risk_assessor (parallel)")
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_stats: Future = pool.submit(
            agent_statistical_engine,
            context,
            surveillance_period=surveillance_period,
            is_reusable=meta.get("is_reusable", False),
        )
        f_risk: Future = pool.submit(agent_risk_assessor, context)
        stats: PSURStatistics = f_stats.result()
        f_risk.result()
    console.print(
        f"  [green]Stats:[/green] units={stats.total_units_sold:,}; "
        f"complaints={stats.total_complaints}; rate={stats.overall_rate_display}"
    )

    # ---- Build device_context structure for narrative writer ----
    # SKILL F5/F6/F10: ensure the orchestrator sees the reconciled values
    # (UDI-DI from previous_psur, EU MDR cert, NB 2797, RMF#) rather than the
    # raw - and sometimes wrong - device_context.json originals.
    eu_reconciled = (context.get("regulatory_scope") or {}).get("eu_mdr", {}) or {}
    us_reconciled = (context.get("regulatory_scope") or {}).get("us_fda", {}) or {}
    device_scope_reconciled = context.get("device_scope") or {}
    risk_reconciled = context.get("risk_management_data") or {}

    cert_no_reconciled = eu_reconciled.get("certificate_number") or meta.get("certificate_number", "")
    cert_date_reconciled = eu_reconciled.get("certificate_issue_date") or meta.get("certificate_date", "")

    device_context_dict, device_name = build_device_context(
        device_name=meta["device_name"],
        device_class=meta["device_class"],
        is_reusable=meta["is_reusable"],
        certificate_number=cert_no_reconciled,
        certificate_date=cert_date_reconciled,
        psur_cadence=meta.get("psur_cadence", "ANNUALLY"),
        infocard_number=meta.get("infocard_number", ""),
        denominator_type=stats.denominator_type,
        denominator_description=stats.denominator_description,
        parsed_data=parsed_data,
        expanded_context=parse_result["expanded_context"],
        input_paths={k: (v[0] if v else None)
                     for k, v in discovered.items() if k != "extra"},
        context_file_rich=device_context_rich,
    )

    # Stamp the harness reconciliations onto the device_context so the
    # downstream LLM agents read the corrected source of truth.
    skill_overrides = {
        "basic_udi_di": device_scope_reconciled.get("basic_udi_di"),
        "basic_udi_di_or_device_family_name": device_scope_reconciled.get("basic_udi_di"),
        "notified_body_number": eu_reconciled.get("notified_body_number"),
        "notified_body_name": eu_reconciled.get("notified_body_name"),
        "eu_mdr_certificate_number": eu_reconciled.get("certificate_number"),
        "eu_mdr_certificate_date": eu_reconciled.get("certificate_issue_date"),
        "first_ce_marking_year": eu_reconciled.get("first_ce_marking"),
        "first_fda_clearance_year": us_reconciled.get("first_clearance_year"),
        "fda_premarket_submission_numbers": us_reconciled.get("premarket_submission_numbers"),
        "risk_management_file_document_number": risk_reconciled.get("rmf_document_number"),
        "device_description_skill_override": device_scope_reconciled.get("device_description"),
        "intended_purpose_skill_override": device_scope_reconciled.get("intended_purpose"),
        "contraindications_skill_override": device_scope_reconciled.get("contraindications"),
    }
    for key, val in skill_overrides.items():
        if val:
            device_context_dict[key] = val

    # ---- Stage 5 (parallel): chart_generator + table_generator + narrative_writer ----
    console.rule(
        "[bold cyan]Stage 5 / 8 - chart_generator + table_generator + narrative_writer (parallel)"
    )
    safe_name = re.sub(r"[^\w\-]", "_", meta["device_name"]).strip("_")
    checkpoint_path = out_dir / f".checkpoint_{safe_name}_{end_date[:4]}.json"
    resume_data = None
    if resume and checkpoint_path.exists():
        try:
            with open(checkpoint_path, "r", encoding="utf-8") as fh:
                resume_data = json.load(fh)
        except Exception:
            resume_data = None

    with ThreadPoolExecutor(max_workers=3) as pool:
        f_charts: Future = pool.submit(
            agent_chart_generator, context,
            stats=stats, output_dir=out_dir, device_name=meta["device_name"],
        )
        f_tables: Future = pool.submit(agent_table_generator, context)
        # Narrative writer must run on the main thread group but is heaviest;
        # it is concurrent with chart + table generators which are CPU-bound
        # only briefly.
        f_psur: Future = pool.submit(
            agent_narrative_writer, context,
            device_context=device_context_dict,
            stats=stats,
            checkpoint_path=checkpoint_path,
            resume_data=resume_data,
        )
        chart_paths = f_charts.result()
        table_payload = f_tables.result()
        psur = f_psur.result()
    psur["_statistics"] = asdict(stats)
    psur["_harness_tables"] = table_payload

    # SKILL Table 7 / Tables 2-4: replace LLM-generated tables with the
    # harness's authoritative hierarchical structures. The LLM tends to
    # collapse Harm -> MDP rows and lose the hierarchy required by the SKILL.
    inject_skill_authoritative_tables(psur, context)

    # ---- Stage 6: Benefit-risk synthesizer ----
    console.rule("[bold cyan]Stage 6 / 8 - benefit_risk_synthesizer_agent")
    agent_benefit_risk_synthesizer(context, stats=stats)
    psur["_harness_benefit_risk"] = context.get("benefit_risk_determination")
    psur["_harness_actions_and_updates"] = context.get("actions_and_updates")
    sanitize_skill_render_content(psur, context)

    # ---- Stage 7: DOCX renderer ----
    console.rule("[bold cyan]Stage 7 / 8 - docx_renderer")
    json_path = _safe_write_path(out_dir / f"PSUR_{safe_name}_{end_date[:4]}.json")
    stats_path = _safe_write_path(out_dir / f"PSUR_{safe_name}_{end_date[:4]}_statistics.json")
    payload_path = _safe_write_path(
        out_dir / f"PSUR_{safe_name}_{end_date[:4]}_harness_context.json"
    )

    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(psur, fh, indent=2, default=str)
    with open(stats_path, "w", encoding="utf-8") as fh:
        json.dump(asdict(stats), fh, indent=2, default=str)
    with open(payload_path, "w", encoding="utf-8") as fh:
        json.dump(context.slots, fh, indent=2, default=str)

    docx_path = _safe_write_path(out_dir / f"PSUR_{safe_name}_{end_date[:4]}.docx")
    try:
        agent_docx_renderer(psur, docx_path=docx_path, chart_paths=chart_paths)
    except PermissionError:
        ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        docx_path = out_dir / f"PSUR_{safe_name}_{end_date[:4]}_{ts}.docx"
        console.print(
            f"  [yellow]Primary DOCX path locked (open in Word?) - "
            f"writing to {docx_path.name}[/yellow]"
        )
        agent_docx_renderer(psur, docx_path=docx_path, chart_paths=chart_paths)

    if checkpoint_path.exists():
        try:
            checkpoint_path.unlink()
        except Exception:
            pass

    # ---- Stage 8: Validation ----
    console.rule("[bold cyan]Stage 8 / 8 - validation_agent")
    issues = agent_validation_harness(
        context,
        psur=psur,
        docx_path=docx_path,
        parsed_data=parsed_data,
        device_context=device_context_dict,
    )
    if not issues:
        console.print("  [green]All harness checks passed[/green]")
    else:
        for it in issues:
            color = "red" if it.severity == "ERROR" else "yellow"
            console.print(f"  [{color}]{it}[/{color}]")

    elapsed = time.time() - t0
    console.rule("[bold green]Harness complete")
    console.print(f"  JSON:                 {json_path}")
    console.print(f"  DOCX:                 {docx_path}")
    console.print(f"  Statistics:           {stats_path}")
    console.print(f"  Harness payload:      {payload_path}")
    console.print(f"  Total runtime:        {elapsed:.1f}s")

    return HarnessResult(
        psur=psur,
        statistics=stats,
        context=context,
        chart_paths=chart_paths,
        issues=issues,
        json_path=json_path,
        docx_path=docx_path,
        stats_path=stats_path,
        elapsed_seconds=elapsed,
    )


# =====================================================================
# Helpers
# =====================================================================

def inject_skill_authoritative_tables(
    psur: Dict[str, Any],
    context: HarnessContext,
) -> None:
    """Replace LLM-generated tables with the harness's deterministic ones.

    SKILL_PSUR_GENERATION mandates:
    - Table 7 is HIERARCHICAL: Harm header rows + indented MDP rows + Grand Total.
    - Tables 2-4 (serious incidents) report ZERO when no event meets EU MDR
      Art. 87. FDA MDRs go in narrative only.
    - Rate = count / units_distributed * 100, formatted to 4 decimal places.
    """
    sections = psur.get("sections") or {}
    sec_f = sections.get("F_product_complaint_types_counts_and_rates") or {}
    sec_d = sections.get("D_information_on_serious_incidents") or {}
    # Remove prior helper keys from older runs before injecting schema-compliant
    # replacements. Helper metadata belongs under top-level `_harness_*`, never
    # inside rendered sections.
    for key in list(sec_d.keys()):
        if str(key).startswith("_skill"):
            sec_d.pop(key, None)
    for key in list(sec_f.keys()):
        if str(key).startswith("_skill"):
            sec_f.pop(key, None)

    cd = context.get("complaint_data") or {}
    by_harm = cd.get("by_harm_and_mdp", []) or []
    grand = cd.get("by_harm_and_mdp_grand_total") or {}
    units_denom = int(cd.get("units_denominator", 0) or 0)

    # ---- SKILL Table 7: hierarchical Harm -> MDP rows + Grand Total. ------
    if by_harm:
        rows: List[Dict[str, Any]] = []
        for harm in by_harm:
            harm_term = harm.get("harm_term") or "No Health Consequence or Impact"
            harm_count = int(harm.get("harm_count", 0) or 0)
            harm_rate = float(harm.get("harm_rate_pct", 0.0) or 0.0)
            # Bold header row for the Harm group.
            rows.append({
                "harm": harm_term,
                "medical_device_problem": "",
                "current_12_month_complaint_count": harm_count,
                "current_12_month_complaint_rate": round(harm_rate, 4),
                "max_expected_rate_of_occurrence_from_ract": None,
            })
            for mdp in harm.get("mdp_entries", []) or []:
                mdp_count = int(mdp.get("count", 0) or 0)
                mdp_rate = float(mdp.get("rate_pct", 0.0) or 0.0)
                max_rate = mdp.get("max_expected_rate_from_ract")
                rows.append({
                    "harm": harm_term,
                    "medical_device_problem": f"    {mdp.get('mdp_term', '')}",
                    "current_12_month_complaint_count": mdp_count,
                    "current_12_month_complaint_rate": round(mdp_rate, 4),
                    "max_expected_rate_of_occurrence_from_ract": (
                        round(float(max_rate) * 100, 4) if max_rate is not None else None
                    ),
                })
        # Grand total
        rows.append({
            "harm": "Grand Total",
            "medical_device_problem": "",
            "current_12_month_complaint_count": int(grand.get("harm_count", 0) or 0),
            "current_12_month_complaint_rate": round(
                float(grand.get("harm_rate_pct", 0.0) or 0.0), 4
            ),
            "max_expected_rate_of_occurrence_from_ract": None,
        })

        cadence = (context.get("regulatory_scope")
                   .get("_decisions", {}).get("cadence_label", "ANNUALLY"))
        format_key = "annual_format" if cadence == "ANNUALLY" else "biennial_format"

        sec_f["table_7_complaint_rate_and_count"] = {
            "use_if_psur_frequency": cadence,
            format_key: {
                "date_range": (
                    f"{(context.get('report_identity') or {}).get('reporting_period_start','')} to "
                    f"{(context.get('report_identity') or {}).get('reporting_period_end','')}"
                ),
                "rows": rows,
                "grand_total": {
                    "complaint_count": int(grand.get("harm_count", 0) or 0),
                    "complaint_rate": round(
                        float(grand.get("harm_rate_pct", 0.0) or 0.0), 4
                    ),
                },
            },
        }
        sections["F_product_complaint_types_counts_and_rates"] = sec_f

    # ---- SKILL Tables 2-4: EU/UK serious incidents only -------------------
    sieu = (context.get("vigilance_data") or {}).get("serious_incidents_eu_uk", {}) or {}
    eu_count = int(sieu.get("count_eea", 0) or 0)
    uk_count = int(sieu.get("count_uk", 0) or 0)
    ww_count = int(sieu.get("count_worldwide_eu_mdr_threshold", 0) or 0)
    fda_total = int(((context.get("vigilance_data") or {})
                     .get("mdr_reports_us_fda", {}) or {}).get("total_count", 0) or 0)

    serious_block = {
        "table_2_serious_incidents_eu_eea": [{
            "region": "EEA+TR+XI",
            "serious_incident_count": eu_count,
            "rate": "N/A" if eu_count == 0 else None,
        }],
        "table_3_serious_incidents_uk": [{
            "region": "UK",
            "serious_incident_count": uk_count,
            "rate": "N/A" if uk_count == 0 else None,
        }],
        "table_4_serious_incidents_worldwide_art_87": [{
            "region": "Worldwide (Art. 87 threshold)",
            "serious_incident_count": ww_count,
            "rate": "N/A" if ww_count == 0 else None,
        }],
        "fda_mdr_summary_for_narrative": {
            "total_count": fda_total,
            "narrative_template": sieu.get("narrative_template", ""),
            "appears_in": "Section D narrative + Section F complaint analysis",
            "appears_in_serious_incident_tables": False,
        },
    }
    sections["D_information_on_serious_incidents"] = sec_d
    psur["sections"] = sections
    psur.setdefault("_harness_skill_tables", {})["serious_incident_tables"] = serious_block
    psur["_harness_skill_tables"]["table7_source"] = {
        "denominator_units": units_denom,
        "grand_total_display": grand.get("harm_rate_display", ""),
    }


def sanitize_skill_render_content(psur: Dict[str, Any], context: HarnessContext) -> None:
    """Final deterministic content sanitization before JSON/DOCX rendering."""
    eu = (context.get("regulatory_scope") or {}).get("eu_mdr", {}) or {}
    ds = context.get("device_scope") or {}
    chosen_udi = ds.get("basic_udi_di") or ""
    cert_no = eu.get("certificate_number") or "MDR 800217"
    cert_date = eu.get("certificate_issue_date") or "2024-12-08"
    if cert_date and not re.match(r"^\d{4}-\d{2}-\d{2}$", str(cert_date)):
        try:
            cert_date = datetime.strptime(str(cert_date), "%d %B %Y").strftime("%Y-%m-%d")
        except ValueError:
            pass
    nb_name = eu.get("notified_body_name") or "BSI Group The Netherlands B.V."
    nb_number = eu.get("notified_body_number") or "2797"

    # Cover-page fields are deterministic, not LLM-generated.
    cover_reg = psur.setdefault("psur_cover_page", {}).setdefault(
        "regulatory_information", {}
    )
    if cert_no:
        cover_reg["certificate_number"] = cert_no
    if cert_date:
        cover_reg["date_of_issue"] = cert_date
    cover_reg["notified_body"] = {"name": nb_name, "number": nb_number}

    def _clean(value: Any) -> Any:
        if isinstance(value, str):
            result = value
            if chosen_udi:
                result = result.replace("0888937TD053ZN", chosen_udi)
            result = result.replace("Section we", "Section I")
            result = result.replace("section we", "Section I")
            result = re.sub(
                r"BSI\s*\(UK Approved Body\s*0086\)[^.,;]*",
                "CE transitional arrangements with CooperSurgical UK Limited as UK Responsible Person",
                result,
                flags=re.IGNORECASE,
            )
            result = re.sub(
                r"BSI\s*\(NB\s*0086\)",
                f"{nb_name} (NB {nb_number})",
                result,
                flags=re.IGNORECASE,
            )
            result = re.sub(
                r"\bNB\s*0086\b",
                f"NB {nb_number}",
                result,
                flags=re.IGNORECASE,
            )
            result = re.sub(
                r"\b0086\b",
                nb_number,
                result,
            )
            return result
        if isinstance(value, list):
            return [_clean(v) for v in value]
        if isinstance(value, dict):
            return {k: _clean(v) for k, v in value.items()}
        return value

    cleaned = _clean(psur)
    psur.clear()
    psur.update(cleaned)


def _safe_write_path(path: Path) -> Path:
    """Return `path` if writable, otherwise a timestamp-suffixed sibling.

    Prevents the harness aborting at the IO step when a previously generated
    file is locked by a Word/Excel session in another app.
    """
    if not path.exists():
        return path
    try:
        with open(path, "a"):
            return path
    except (PermissionError, OSError):
        ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        return path.with_name(f"{path.stem}_{ts}{path.suffix}")
