"""LLM-driven PMS Plan parser.

Extracts structured data from Post-Market Surveillance (PMS) Plan documents
using text extraction and Claude-based intelligent parsing.

Extracted data:
- Device identification (name, classification, UDI)
- PMS activities and their frequencies
- Proactive and reactive surveillance methods
- PMCF plan references
- Complaint handling procedures
- Trend reporting thresholds
- PSUR cadence
"""
import json
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List

from llm_client import create_message
from config import MODEL
from parsers.universal import parse_any_to_text

logger = logging.getLogger(__name__)


@dataclass
class PMSPlanData:
    """Structured data extracted from a PMS Plan document."""
    device_name: str = ""
    device_classification: str = ""  # e.g., "Class IIb"
    pms_plan_version: str = ""
    pms_plan_date: str = ""

    # Surveillance activities
    proactive_activities: List[str] = field(default_factory=list)
    reactive_activities: List[str] = field(default_factory=list)

    # Reporting
    psur_cadence: str = ""  # "ANNUALLY" or "EVERY_TWO_YEARS"
    trend_reporting_thresholds: Dict[str, Any] = field(default_factory=dict)
    complaint_handling_summary: str = ""

    # PMCF
    pmcf_plan_reference: str = ""
    pmcf_activities: List[str] = field(default_factory=list)

    # References
    associated_documents: List[Dict[str, str]] = field(default_factory=list)

    # Full text for agent context
    full_text: str = ""
    source_file: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d.pop("full_text", None)  # Don't include massive text in dict
        return d


def parse_pms_plan(filepath: Path) -> Dict[str, Any]:
    """Parse a PMS Plan document and extract structured data.

    Uses text extraction followed by LLM-based structured extraction.

    Args:
        filepath: Path to the PMS Plan file (PDF, DOCX, or text)

    Returns:
        Dict with structured PMS Plan data + full_text for agent context
    """
    filepath = Path(filepath)

    # Extract full text
    full_text = parse_any_to_text(filepath, purpose="post-market surveillance plan")
    if not full_text or len(full_text.strip()) < 50:
        logger.warning(f"PMS Plan text extraction yielded minimal content from {filepath}")
        return {"full_text": full_text, "source_file": filepath.name}

    # Use LLM to extract structured data
    structured = _llm_extract_pms_plan(full_text, filepath.name)

    # Merge structured data with full text
    result = structured
    result["full_text"] = full_text
    result["source_file"] = filepath.name
    return result


def _llm_extract_pms_plan(text: str, filename: str) -> Dict[str, Any]:
    """Use Claude to extract structured data from PMS Plan text."""
    # Truncate to fit context window
    excerpt = text[:12000]

    prompt = f"""You are a medical-device regulatory analyst. Extract structured data from this PMS Plan document.

Return ONLY a JSON object with these keys:
{{
  "device_name": "<exact commercial product name>",
  "device_classification": "<e.g. Class IIb>",
  "pms_plan_version": "<version number or revision>",
  "pms_plan_date": "<date in ISO 8601 if found>",
  "proactive_activities": ["<list of proactive PMS activities>"],
  "reactive_activities": ["<list of reactive PMS activities>"],
  "psur_cadence": "<ANNUALLY or EVERY_TWO_YEARS>",
  "trend_reporting_thresholds": {{"<metric>": "<threshold value>"}},
  "complaint_handling_summary": "<brief summary of complaint handling process>",
  "pmcf_plan_reference": "<PMCF plan document reference if mentioned>",
  "pmcf_activities": ["<list of planned PMCF activities>"],
  "associated_documents": [{{"document_number": "<num>", "title": "<title>"}}]
}}

RULES:
- Extract EXACT values from the text — do not fabricate.
- If a field cannot be determined, use empty string or empty list.
- For psur_cadence: Class III and IIb are typically ANNUALLY; IIa can be EVERY_TWO_YEARS.

DOCUMENT TEXT:
{excerpt}

Respond with ONLY the JSON object, no markdown fences."""

    try:
        response = create_message(
            model=MODEL,
            max_tokens=1500,
            temperature=0.0,
            messages=[{"role": "user", "content": prompt}]
        )
        import re
        raw = response.content[0].text.strip()
        raw = re.sub(r'^```(?:json)?\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)
        return json.loads(raw)
    except Exception as e:
        logger.warning(f"LLM PMS Plan extraction failed: {e}")
        return {}
