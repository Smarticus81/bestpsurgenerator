"""Input structure specifications for the demo service.

Content is editable; STRUCTURE IS LOCKED. Each input is either:

  table — a CSV-backed input with a fixed column set. Submitted rows must
          carry exactly the spec's columns (no additions/removals/renames),
          and each value must satisfy the column type.
  json  — a JSON-backed input whose key structure must match the bundled
          template exactly at every nesting level.

The bundled mock pack in data/input/ is the single source of truth for
both the default content and the locked JSON structures.
"""
import csv
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from config import INPUT_DIR

# ── Column descriptor types ──────────────────────────────────────────
# "string"  — any string (empty allowed unless required)
# "date"    — ISO date string YYYY-MM-DD
# "integer" — int (or string of digits)
# "number"  — int or float

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _col(name: str, type_: str = "string", required: bool = True) -> Dict[str, Any]:
    return {"name": name, "type": type_, "required": required}


# ── Table input specs (column order matches the bundled mock CSVs) ───
TABLE_SPECS: Dict[str, Dict[str, Any]] = {
    "sales": {
        "mock_file": "sales (2).csv",
        "run_filename": "sales.csv",
        "columns": [
            _col("date", "date"),
            _col("device_model"),
            _col("device_name"),
            _col("region"),
            _col("units_sold", "integer"),
        ],
    },
    "complaints": {
        "mock_file": "complaints (1).csv",
        "run_filename": "complaints.csv",
        "columns": [
            _col("complaint_id"),
            _col("event_date", "date"),
            _col("awareness_date", "date"),
            _col("device_model"),
            _col("device_name"),
            _col("udi"),
            _col("description"),
            _col("narrative"),
            _col("nonconformity", required=False),
            _col("investigation_findings", required=False),
            _col("failure_mode", required=False),
            _col("device_problem", required=False),
            _col("event_type"),
            _col("serious", "integer"),
            _col("outcome", required=False),
            _col("symptom_code", required=False),
            _col("fault_code", required=False),
            _col("root_cause", required=False),
            _col("region"),
        ],
    },
    "capa": {
        "mock_file": "capa (1).csv",
        "run_filename": "capa.csv",
        "columns": [
            _col("capa_id"),
            _col("complaint_id", required=False),
            _col("device_model"),
            _col("device_name"),
            _col("trigger"),
            _col("root_cause", required=False),
            _col("actions_taken"),
            _col("effectiveness", required=False),
        ],
    },
    "fsca": {
        "mock_file": "fsca (1).csv",
        "run_filename": "fsca.csv",
        "columns": [
            _col("action_id"),
            _col("reason"),
            _col("device_model"),
            _col("device_name"),
            _col("date_initiated", "date"),
            _col("regions_affected"),
            _col("status"),
            _col("effectiveness", required=False),
        ],
    },
    "external_events": {
        "mock_file": "external_events.csv",
        "run_filename": "external_events.csv",
        "columns": [
            _col("event_id"),
            _col("date", "date"),
            _col("device_model"),
            _col("device_name"),
            _col("external_source"),
            _col("description"),
            _col("narrative", required=False),
            _col("event_type", required=False),
            _col("serious", "integer"),
            _col("outcome", required=False),
        ],
    },
    "literature": {
        "mock_file": "literature.csv",
        "run_filename": "literature.csv",
        "columns": [
            _col("article_id"),
            _col("title"),
            _col("authors"),
            _col("journal"),
            _col("publication_date", "date"),
            _col("database"),
            _col("search_terms", required=False),
            _col("relevance"),
            _col("findings_summary"),
            _col("safety_signal", required=False),
        ],
    },
}

# ── JSON input specs ─────────────────────────────────────────────────
JSON_SPECS: Dict[str, Dict[str, Any]] = {
    "device_context": {
        "mock_file": "device_context.json",
        "run_filename": "device_context.json",
    },
    "ract": {
        "mock_file": "risk_ract (1).json",
        "run_filename": "risk_ract.json",
    },
    "pms_plan": {
        "mock_file": "pms_plan (1).json",
        "run_filename": "pms_plan.json",
    },
    "previous_psur": {
        "mock_file": "previous_psur (1).json",
        "run_filename": "previous_psur.json",
    },
    "clinical_safety": {
        "mock_file": "clinical_safety (1).json",
        "run_filename": "clinical_safety.json",
    },
    "clinical_performance": {
        "mock_file": "clinical_performance (1).json",
        "run_filename": "clinical_performance.json",
    },
}

INPUT_NAMES: Tuple[str, ...] = tuple(list(TABLE_SPECS) + list(JSON_SPECS))

# Default reporting period — matches the bundled mock data (2023).
DEFAULT_PERIOD = {"start": "2023-01-01", "end": "2023-12-31"}


# ── Default content loading (from the bundled mock pack) ─────────────

def _coerce_cell(value: str, col_type: str) -> Any:
    if col_type == "integer":
        try:
            return int(value)
        except (TypeError, ValueError):
            return value
    if col_type == "number":
        try:
            return float(value)
        except (TypeError, ValueError):
            return value
    return value


def load_default_rows(name: str, input_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Load the bundled mock CSV for a table input as typed row dicts."""
    spec = TABLE_SPECS[name]
    path = Path(input_dir or INPUT_DIR) / spec["mock_file"]
    types = {c["name"]: c["type"] for c in spec["columns"]}
    rows: List[Dict[str, Any]] = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for raw in csv.DictReader(fh):
            rows.append({
                k: _coerce_cell((v or "").strip(), types.get(k, "string"))
                for k, v in raw.items() if k is not None
            })
    return rows


def load_default_json(name: str, input_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Load the bundled mock JSON for a json input."""
    spec = JSON_SPECS[name]
    path = Path(input_dir or INPUT_DIR) / spec["mock_file"]
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def build_defaults() -> Dict[str, Any]:
    """Build the GET /defaults response body."""
    inputs: Dict[str, Any] = {}
    for name, spec in TABLE_SPECS.items():
        inputs[name] = {
            "kind": "table",
            "columns": spec["columns"],
            "rows": load_default_rows(name),
        }
    for name in JSON_SPECS:
        inputs[name] = {
            "kind": "json",
            "value": load_default_json(name),
        }
    return {"period": dict(DEFAULT_PERIOD), "inputs": inputs}


# ── Structural validation ────────────────────────────────────────────

class StructureViolation(Exception):
    """Raised with a list of precise per-field error dicts."""

    def __init__(self, errors: List[Dict[str, str]]):
        super().__init__(f"{len(errors)} structural violation(s)")
        self.errors = errors


def _check_cell(loc: str, value: Any, col: Dict[str, Any],
                errors: List[Dict[str, str]]) -> None:
    name, col_type, required = col["name"], col["type"], col["required"]
    is_empty = value is None or (isinstance(value, str) and value.strip() == "")
    if is_empty:
        if required:
            errors.append({
                "loc": loc,
                "msg": f"column '{name}' is required and must not be empty",
            })
        return
    if col_type == "integer":
        if isinstance(value, bool) or not (
            isinstance(value, int)
            or (isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()))
        ):
            errors.append({
                "loc": loc,
                "msg": f"column '{name}' must be an integer (got {value!r})",
            })
    elif col_type == "number":
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
        if not ok and isinstance(value, str):
            try:
                float(value)
                ok = True
            except ValueError:
                ok = False
        if not ok:
            errors.append({
                "loc": loc,
                "msg": f"column '{name}' must be a number (got {value!r})",
            })
    elif col_type == "date":
        if not (isinstance(value, str) and _DATE_RE.fullmatch(value.strip())):
            errors.append({
                "loc": loc,
                "msg": f"column '{name}' must be an ISO date YYYY-MM-DD (got {value!r})",
            })
    else:  # string
        if not isinstance(value, str):
            errors.append({
                "loc": loc,
                "msg": f"column '{name}' must be a string (got {type(value).__name__})",
            })


def validate_table_rows(name: str, rows: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Validate submitted rows against the locked column set + types."""
    spec = TABLE_SPECS[name]
    expected = [c["name"] for c in spec["columns"]]
    expected_set = set(expected)
    by_name = {c["name"]: c for c in spec["columns"]}
    errors: List[Dict[str, str]] = []

    if not rows:
        errors.append({
            "loc": f"inputs.{name}.rows",
            "msg": "at least one row is required",
        })
        return errors

    for i, row in enumerate(rows):
        loc_base = f"inputs.{name}.rows[{i}]"
        if not isinstance(row, dict):
            errors.append({"loc": loc_base, "msg": "each row must be an object"})
            continue
        keys = set(row.keys())
        for missing in sorted(expected_set - keys):
            errors.append({
                "loc": f"{loc_base}.{missing}",
                "msg": (
                    f"missing column '{missing}' — the column set is locked to: "
                    f"{', '.join(expected)}"
                ),
            })
        for extra in sorted(keys - expected_set):
            errors.append({
                "loc": f"{loc_base}.{extra}",
                "msg": (
                    f"unexpected column '{extra}' — columns cannot be added, "
                    f"removed, or renamed (allowed: {', '.join(expected)})"
                ),
            })
        for key in keys & expected_set:
            _check_cell(f"{loc_base}.{key}", row[key], by_name[key], errors)

    return errors


def _json_type_label(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "null"


def _validate_json_structure(loc: str, value: Any, template: Any,
                             errors: List[Dict[str, str]]) -> None:
    """Recursively enforce that ``value`` has the same key structure as
    ``template``. Scalar values are freely editable but must keep their
    primitive type; dict keys may be neither added nor removed; list items
    must match the structure of the template's first item."""
    if isinstance(template, dict):
        if not isinstance(value, dict):
            errors.append({
                "loc": loc,
                "msg": f"must be an object (got {_json_type_label(value)})",
            })
            return
        t_keys, v_keys = set(template.keys()), set(value.keys())
        for missing in sorted(t_keys - v_keys):
            errors.append({
                "loc": f"{loc}.{missing}",
                "msg": f"missing key '{missing}' — the field structure is locked",
            })
        for extra in sorted(v_keys - t_keys):
            errors.append({
                "loc": f"{loc}.{extra}",
                "msg": (
                    f"unexpected key '{extra}' — fields cannot be added, "
                    f"removed, or renamed"
                ),
            })
        for key in t_keys & v_keys:
            _validate_json_structure(f"{loc}.{key}", value[key], template[key], errors)
    elif isinstance(template, list):
        if not isinstance(value, list):
            errors.append({
                "loc": loc,
                "msg": f"must be an array (got {_json_type_label(value)})",
            })
            return
        if template:
            item_template = template[0]
            for i, item in enumerate(value):
                _validate_json_structure(f"{loc}[{i}]", item, item_template, errors)
    elif isinstance(template, bool):
        if not isinstance(value, bool):
            errors.append({
                "loc": loc,
                "msg": f"must be a boolean (got {_json_type_label(value)})",
            })
    elif isinstance(template, (int, float)):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append({
                "loc": loc,
                "msg": f"must be a number (got {_json_type_label(value)})",
            })
    elif isinstance(template, str):
        if not isinstance(value, str):
            errors.append({
                "loc": loc,
                "msg": f"must be a string (got {_json_type_label(value)})",
            })
    # template None → any scalar accepted


def validate_json_value(name: str, value: Dict[str, Any]) -> List[Dict[str, str]]:
    """Validate a submitted JSON input against the locked template structure."""
    template = load_default_json(name)
    errors: List[Dict[str, str]] = []
    _validate_json_structure(f"inputs.{name}.value", value, template, errors)
    return errors


# ── Run workspace writing ────────────────────────────────────────────

def write_run_inputs(workspace_input_dir: Path,
                     inputs: Dict[str, Any]) -> None:
    """Write validated inputs to a per-run workspace input directory.

    ``inputs`` maps input name → rows (table) or value (json). Any input
    not supplied falls back to the bundled mock default, so the pipeline
    always receives the complete pack.
    """
    workspace_input_dir.mkdir(parents=True, exist_ok=True)

    for name, spec in TABLE_SPECS.items():
        rows = inputs.get(name)
        if rows is None:
            rows = load_default_rows(name)
        columns = [c["name"] for c in spec["columns"]]
        with open(workspace_input_dir / spec["run_filename"], "w",
                  newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=columns)
            writer.writeheader()
            for row in rows:
                writer.writerow({k: ("" if row.get(k) is None else row.get(k))
                                 for k in columns})

    for name, spec in JSON_SPECS.items():
        value = inputs.get(name)
        if value is None:
            value = load_default_json(name)
        with open(workspace_input_dir / spec["run_filename"], "w",
                  encoding="utf-8") as fh:
            json.dump(value, fh, indent=2)
