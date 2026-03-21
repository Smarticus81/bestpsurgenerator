"""AI-powered column mapping module.

Uses Claude to intelligently map unfamiliar column names to expected schema fields
by analyzing both headers and sample content from the data.
"""
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from llm_client import get_llm_client
from config import MODEL

logger = logging.getLogger(__name__)


# ── Field schemas per data type ─────────────────────────────────────────

FIELD_SCHEMAS: Dict[str, Dict[str, Dict[str, Any]]] = {
    "sales": {
        "date": {
            "description": "Date of sale, shipment, or invoice (full datetime values like 2024-01-15)",
            "data_type": "datetime",
            "required": False,
            "examples": ["2024-01-15", "01/15/2024", "15-Jan-2024"],
        },
        "year": {
            "description": "Calendar year or fiscal year (numeric year only, NOT a full date)",
            "data_type": "numeric",
            "required": False,
            "examples": ["2022", "2023", "2024"],
        },
        "month": {
            "description": "Month of sale — either a month name or month number (NOT a full date)",
            "data_type": "text",
            "required": False,
            "examples": ["January", "April", "1", "12"],
        },
        "quantity": {
            "description": "Numeric count of units sold, shipped, distributed, or invoiced",
            "data_type": "numeric",
            "required": False,
            "examples": ["100", "1", "5000"],
        },
        "region": {
            "description": "Geographic region, country, or market territory",
            "data_type": "text",
            "required": False,
            "examples": ["US", "Germany", "EMEA", "North America"],
        },
        "product": {
            "description": "Product name, item/SKU/catalog number, or material code",
            "data_type": "text",
            "required": False,
            "examples": ["SKU-12345", "Product A", "Cat. No. 100-200"],
        },
    },
    "complaints": {
        "date": {
            "description": "Complaint date, event date, date received, or notification date",
            "data_type": "datetime",
            "required": False,
            "examples": ["2024-01-15", "01/15/2024"],
        },
        "complaint_number": {
            "description": "Unique complaint identifier, case number, or reference number",
            "data_type": "text",
            "required": False,
            "examples": ["COMP-2024-001", "CSI-12345", "129-00001"],
        },
        "description": {
            "description": "Complaint narrative, event description, or product issue text",
            "data_type": "text",
            "required": False,
            "examples": ["Device failed during procedure", "Patient reported discomfort"],
        },
        "imdrf_code": {
            "description": "IMDRF device problem code (Annex A), event type, or problem classification code",
            "data_type": "text",
            "required": False,
            "examples": ["A0301", "A0701", "Device Breakage"],
        },
        "harm": {
            "description": "Patient harm outcome, injury category, or health impact (IMDRF Annex F)",
            "data_type": "text",
            "required": False,
            "examples": ["No Harm", "Minor Injury", "F0101"],
        },
        "serious": {
            "description": "Whether the incident is serious/reportable (yes/no/boolean flag)",
            "data_type": "boolean_text",
            "required": False,
            "examples": ["Yes", "No", "TRUE", "Reportable"],
        },
        "region": {
            "description": "Geographic region or country where the complaint originated",
            "data_type": "text",
            "required": False,
            "examples": ["US", "Germany", "EMEA"],
        },
    },
    "capa": {
        "capa_number": {
            "description": "CAPA identifier, reference number, or ID",
            "data_type": "text",
            "required": False,
            "examples": ["CAPA-2024-001", "CR-12345"],
        },
        "title": {
            "description": "CAPA title, description, or subject line",
            "data_type": "text",
            "required": False,
            "examples": ["Root cause investigation for labeling issue"],
        },
        "status": {
            "description": "Current CAPA status or state (open, closed, in progress)",
            "data_type": "text",
            "required": False,
            "examples": ["Open", "Closed", "In Progress", "Completed"],
        },
        "open_date": {
            "description": "CAPA initiation/opening date",
            "data_type": "datetime",
            "required": False,
            "examples": ["2024-01-15"],
        },
        "close_date": {
            "description": "CAPA completion/closing date",
            "data_type": "datetime",
            "required": False,
            "examples": ["2024-06-30"],
        },
        "root_cause": {
            "description": "Root cause category or description",
            "data_type": "text",
            "required": False,
            "examples": ["Process", "Design", "Supplier"],
        },
        "type": {
            "description": "CAPA type — corrective vs preventive action",
            "data_type": "text",
            "required": False,
            "examples": ["Corrective", "Preventive", "Both"],
        },
    },
}


class ColumnMapping:
    """Result of AI column mapping for a single field."""

    def __init__(
        self,
        target_field: str,
        source_column: str,
        confidence: float,
        mapping_source: str = "ai",
        reasoning: str = "",
    ):
        self.target_field = target_field
        self.source_column = source_column
        self.confidence = confidence
        self.mapping_source = mapping_source  # "ai", "exact", "user"
        self.reasoning = reasoning

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_field": self.target_field,
            "source_column": self.source_column,
            "confidence": self.confidence,
            "mapping_source": self.mapping_source,
            "reasoning": self.reasoning,
        }


class ColumnMappingResult:
    """Full mapping result for a dataset."""

    def __init__(self):
        self.mappings: Dict[str, ColumnMapping] = {}  # target_field -> ColumnMapping
        self.unmapped_columns: List[str] = []
        self.low_confidence: List[ColumnMapping] = []

    def get_source_column(self, target_field: str) -> Optional[str]:
        """Get the source column name for a target field."""
        mapping = self.mappings.get(target_field)
        return mapping.source_column if mapping else None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mappings": {k: v.to_dict() for k, v in self.mappings.items()},
            "unmapped_columns": self.unmapped_columns,
            "low_confidence": [m.to_dict() for m in self.low_confidence],
        }


def _try_exact_match(
    df: pd.DataFrame, schema: Dict[str, Dict[str, Any]]
) -> Tuple[Dict[str, str], List[str]]:
    """
    Try exact and substring matching first (fast, no API call).
    Returns (matched: {target -> source_col}, unmatched_targets: [target_field, ...])
    """
    # Build alias lists from the schema descriptions/examples
    matched = {}
    columns = list(df.columns)

    for target_field, field_info in schema.items():
        # Direct exact match
        if target_field in columns:
            matched[target_field] = target_field
            continue

        # Check if any column contains the target or vice versa
        found = False
        for col in columns:
            if target_field in col or col in target_field:
                matched[target_field] = col
                found = True
                break

        # Also check common known aliases
        if not found:
            aliases = _get_known_aliases(target_field)
            for alias in aliases:
                if alias in columns:
                    matched[target_field] = alias
                    found = True
                    break
                for col in columns:
                    if alias in col or col in alias:
                        matched[target_field] = col
                        found = True
                        break
                if found:
                    break

    unmatched = [t for t in schema if t not in matched]
    return matched, unmatched


def _get_known_aliases(target_field: str) -> List[str]:
    """Get common aliases for known field names. Supplements AI mapping."""
    aliases = {
        "date": [
            "complaint_date", "event_date", "received_date", "date_received",
            "report_date", "occurrence_date", "ship_date", "invoice_date",
            "shipment_date", "order_date", "csi_notification_date",
            "notification_date", "date_of_event", "date_entered",
        ],
        "year": [
            "calendar_year", "fiscal_year", "yr", "sale_year",
        ],
        "month": [
            "month_name", "sale_month", "period_month",
        ],
        "quantity": [
            "units", "qty", "unit_quantity", "shipped_qty", "ship_qty",
            "invoice_qty", "order_qty", "amount", "adjusted_quantity",
            "total_quantity", "units_sold", "units_shipped", "count",
        ],
        "region": [
            "country", "market", "territory", "ship_to_country",
            "reporting_country", "location",
        ],
        "product": [
            "item", "sku", "catalog_number", "item_number", "material",
            "product_name", "device", "model",
        ],
        "complaint_number": [
            "complaint_id", "case_number", "reference_number",
            "event_number", "report_number", "case_id", "complaint_no",
        ],
        "description": [
            "narrative", "event_description", "complaint_description",
            "event_narrative", "complaint_narrative", "details", "summary",
            "problem_description", "investigation_findings",
        ],
        "imdrf_code": [
            "problem_code", "event_type", "imdrf", "device_problem_code",
            "medical_device_problem", "symptom_code", "fault_code",
            "failure_code",
        ],
        "harm": [
            "injury", "patient_outcome", "health_impact", "harm_category",
            "patient_harm", "harm_code",
        ],
        "serious": [
            "reportable", "severity", "serious_incident", "is_serious",
            "reportability", "mdr_reportable",
        ],
        "capa_number": [
            "capa_id", "reference", "capa_ref", "number", "id",
        ],
        "title": [
            "subject", "capa_title", "capa_description",
        ],
        "status": [
            "state", "capa_status", "current_status",
        ],
        "open_date": [
            "initiation_date", "date_opened", "created_date",
            "start_date", "date_initiated",
        ],
        "close_date": [
            "completion_date", "date_closed", "closed_date",
            "date_completed",
        ],
        "root_cause": [
            "root_cause_category", "cause", "cause_category",
        ],
        "type": [
            "capa_type", "action_type",
        ],
    }
    return aliases.get(target_field, [])


def infer_column_mapping(
    df: pd.DataFrame,
    purpose: str,
    custom_schema: Optional[Dict[str, Dict[str, Any]]] = None,
) -> ColumnMappingResult:
    """
    Infer column mappings using exact matching first, then AI for remaining.

    Args:
        df: DataFrame with normalized column names
        purpose: Data purpose key (e.g., "sales", "complaints", "capa")
        custom_schema: Optional override for field schema

    Returns:
        ColumnMappingResult with all mappings and unmapped columns
    """
    schema = custom_schema or FIELD_SCHEMAS.get(purpose, {})
    if not schema:
        # No schema for this purpose — return empty mapping
        result = ColumnMappingResult()
        result.unmapped_columns = list(df.columns)
        return result

    result = ColumnMappingResult()
    columns = list(df.columns)

    # Phase 1: Try exact/substring matching (fast, no API cost)
    exact_matches, unmatched_targets = _try_exact_match(df, schema)

    for target_field, source_col in exact_matches.items():
        result.mappings[target_field] = ColumnMapping(
            target_field=target_field,
            source_column=source_col,
            confidence=1.0,
            mapping_source="exact",
            reasoning="Direct or substring match",
        )

    # Track which source columns are already mapped
    mapped_source_cols = set(exact_matches.values())

    # Phase 2: AI mapping for unmatched targets
    if unmatched_targets:
        remaining_columns = [c for c in columns if c not in mapped_source_cols]
        if remaining_columns:
            ai_mappings = _ai_infer_mapping(
                df, remaining_columns, unmatched_targets, schema, purpose
            )
            for mapping in ai_mappings:
                result.mappings[mapping.target_field] = mapping
                mapped_source_cols.add(mapping.source_column)
                if mapping.confidence < 0.7:
                    result.low_confidence.append(mapping)

    # Track unmapped source columns (extra data)
    result.unmapped_columns = [c for c in columns if c not in mapped_source_cols]

    return result


def _ai_infer_mapping(
    df: pd.DataFrame,
    source_columns: List[str],
    target_fields: List[str],
    schema: Dict[str, Dict[str, Any]],
    purpose: str,
) -> List[ColumnMapping]:
    """Use LLM to infer column mappings by analyzing headers AND content samples."""
    client = get_llm_client()

    # Build sample data for each column (3–5 non-null rows)
    column_samples = {}
    for col in source_columns:
        non_null = df[col].dropna()
        samples = non_null.head(5).astype(str).tolist()
        column_samples[col] = samples

    # Build target field descriptions
    target_descriptions = {}
    for field in target_fields:
        info = schema.get(field, {})
        target_descriptions[field] = {
            "description": info.get("description", ""),
            "data_type": info.get("data_type", "text"),
            "examples": info.get("examples", []),
        }

    system_prompt = f"""You are a data column mapping specialist for medical device regulatory documents.
Your task is to map source data columns to target schema fields for a '{purpose}' dataset.

Analyze BOTH the column names AND the sample data content to determine the correct mapping.
A column name might be misleading — always verify by checking the actual data values.

Rules:
1. A source column can map to AT MOST one target field
2. A target field can map to AT MOST one source column
3. Assign a confidence score (0.0 to 1.0) for each mapping
4. If no source column matches a target, omit that target from the output
5. Confidence guidelines:
   - 1.0: Column name is an obvious match AND data content confirms it
   - 0.8-0.9: Column name is a reasonable match AND data looks correct
   - 0.6-0.7: Column name is ambiguous but data content suggests a match
   - 0.3-0.5: Uncertain — name is unrelated but data might fit
   - Below 0.3: Very unlikely match, do not include

Output valid JSON only, no explanation."""

    user_prompt = f"""Map these source columns to target fields.

## Source Columns (with sample data)

{json.dumps(column_samples, indent=2, default=str)}

## Target Fields

{json.dumps(target_descriptions, indent=2)}

Output a JSON array of objects, each with:
- "target_field": the target field name
- "source_column": the source column name
- "confidence": float 0.0-1.0
- "reasoning": brief explanation (1 sentence)

JSON only:"""

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=2048,
            temperature=0.0,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )

        content = response.content[0].text.strip()

        # Strip markdown code fence if present
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(
                lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
            )

        mappings_raw = json.loads(content)
        results = []
        for m in mappings_raw:
            target = m.get("target_field", "")
            source = m.get("source_column", "")
            conf = float(m.get("confidence", 0.0))
            reasoning = m.get("reasoning", "")

            if target in target_fields and source in [c for c in df.columns]:
                results.append(
                    ColumnMapping(
                        target_field=target,
                        source_column=source,
                        confidence=conf,
                        mapping_source="ai",
                        reasoning=reasoning,
                    )
                )

        return results

    except Exception as e:
        logger.warning(f"AI column mapping failed: {e}")
        return []


def get_extra_column_context(
    df: pd.DataFrame, unmapped_columns: List[str], max_rows: int = 10
) -> Dict[str, Any]:
    """
    Build a context dict of unmapped columns for passing to LLM narrative generation.
    Includes column names, sample values, and basic stats.
    """
    extra = {}
    for col in unmapped_columns:
        non_null = df[col].dropna()
        col_info: Dict[str, Any] = {
            "non_null_count": len(non_null),
            "total_count": len(df),
            "sample_values": non_null.head(max_rows).astype(str).tolist(),
        }
        # Add basic stats for numeric columns
        if pd.api.types.is_numeric_dtype(df[col]):
            col_info["mean"] = float(non_null.mean()) if len(non_null) > 0 else None
            col_info["min"] = float(non_null.min()) if len(non_null) > 0 else None
            col_info["max"] = float(non_null.max()) if len(non_null) > 0 else None
        # Unique value counts for categoricals
        elif len(non_null) > 0:
            vc = non_null.value_counts()
            if len(vc) <= 20:
                col_info["value_counts"] = {str(k): int(v) for k, v in vc.items()}
            else:
                col_info["unique_count"] = int(vc.shape[0])
                col_info["top_values"] = {
                    str(k): int(v) for k, v in vc.head(10).items()
                }

        extra[col] = col_info
    return extra
