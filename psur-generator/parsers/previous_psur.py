"""Structured parser for previous PSUR documents (.docx / .pdf).

Extracts:
- Previous period dates & cadence
- Prior actions / commitments (open & closed)
- Complaint summary counts from prior period
- Trend data from prior period
- Findings and conclusions
- Notified body review status
- Any RACT data referenced
"""
import re
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from parsers.universal import parse_file

logger = logging.getLogger(__name__)


# ── Section heading patterns ──────────────────────────────────────────
# These match FormQAR-054 headings as they appear in typical Word docs
_HEADING_PATTERNS = {
    "executive_summary": re.compile(
        r"(?:section\s*a|executive\s*summary)", re.I
    ),
    "scope": re.compile(
        r"(?:section\s*b|scope|device\s*description)", re.I
    ),
    "sales": re.compile(
        r"(?:section\s*c|volume\s*of\s*sales|population\s*exposure)", re.I
    ),
    "serious_incidents": re.compile(
        r"(?:section\s*d|serious\s*incident)", re.I
    ),
    "complaints_feedback": re.compile(
        r"(?:section\s*e|customer\s*feedback)", re.I
    ),
    "complaint_rates": re.compile(
        r"(?:section\s*f|complaint\s*types.*counts.*rates|complaint\s*rate)", re.I
    ),
    "trend": re.compile(
        r"(?:section\s*g|trend\s*report)", re.I
    ),
    "fsca": re.compile(
        r"(?:section\s*h|field\s*safety)", re.I
    ),
    "capa": re.compile(
        r"(?:section\s*i|corrective.*preventive|capa)", re.I
    ),
    "literature": re.compile(
        r"(?:section\s*j|literature\s*review)", re.I
    ),
    "external_db": re.compile(
        r"(?:section\s*k|external\s*database)", re.I
    ),
    "pmcf": re.compile(
        r"(?:section\s*l|pmcf|post.market\s*clinical)", re.I
    ),
    "conclusions": re.compile(
        r"(?:section\s*m|findings.*conclusions|conclusion)", re.I
    ),
}


def parse_previous_psur(filepath: Path) -> Dict[str, Any]:
    """Parse a previous PSUR document and extract structured data.

    Args:
        filepath: Path to .docx or .pdf previous PSUR

    Returns:
        Dict with structured data from the previous PSUR:
        {
            "source_file": str,
            "period": {"start_date": str, "end_date": str},
            "cadence": str,
            "device_name": str,
            "manufacturer": str,
            "sections": {section_key: text, ...},
            "prior_actions": [...],
            "complaint_summary": {...},
            "trend_data": {...},
            "serious_incidents_count": int,
            "notified_body_review": str,
            "full_text": str,
            "tables": [...],
        }
    """
    result = parse_file(filepath, purpose="previous PSUR")
    full_text = result.get("full_text", "")
    tables = result.get("tables", [])
    paragraphs = result.get("paragraphs", [])

    parsed = {
        "source_file": filepath.name,
        "period": _extract_period(full_text),
        "cadence": _extract_cadence(full_text),
        "device_name": _extract_device_name(full_text),
        "manufacturer": _extract_manufacturer(full_text),
        "sections": _split_into_sections(full_text, paragraphs),
        "prior_actions": _extract_prior_actions(full_text, tables),
        "complaint_summary": _extract_complaint_summary(full_text, tables),
        "trend_data": _extract_trend_data(full_text, tables),
        "serious_incidents_count": _extract_serious_count(full_text),
        "notified_body_review": _extract_nb_review(full_text),
        "sales_data": _extract_sales_data(full_text, tables),
        "full_text": full_text,
        "tables": tables,
    }

    logger.info(
        f"Previous PSUR parsed: period={parsed['period']}, "
        f"device={parsed['device_name']}, "
        f"manufacturer={parsed['manufacturer']}, "
        f"{len(parsed['prior_actions'])} prior actions, "
        f"{parsed['serious_incidents_count']} serious incidents"
    )

    return parsed


def _extract_period(text: str) -> Dict[str, str]:
    """Extract surveillance period dates from the previous PSUR."""
    # Try common date range patterns
    patterns = [
        # "January 1, 2023 to December 31, 2023"
        r"(?:data\s*collection|surveillance|reporting)\s*period[:\s]*"
        r"(\w+\s+\d{1,2},?\s+\d{4})\s*(?:to|through|–|-|—)\s*(\w+\s+\d{1,2},?\s+\d{4})",
        # "2023-01-01 to 2023-12-31"
        r"(?:data\s*collection|surveillance|reporting)\s*period[:\s]*"
        r"(\d{4}-\d{2}-\d{2})\s*(?:to|through|–|-|—)\s*(\d{4}-\d{2}-\d{2})",
        # "01/01/2023 - 12/31/2023"
        r"(?:data\s*collection|surveillance|reporting)\s*period[:\s]*"
        r"(\d{1,2}/\d{1,2}/\d{4})\s*(?:to|through|–|-|—)\s*(\d{1,2}/\d{1,2}/\d{4})",
        # Fallback: any two dates near "period"
        r"period[:\s]*.*?(\d{4}-\d{2}-\d{2})\s*(?:to|through|–|-|—)\s*(\d{4}-\d{2}-\d{2})",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.I)
        if m:
            return {"start_date": m.group(1).strip(), "end_date": m.group(2).strip()}

    return {"start_date": "", "end_date": ""}


def _extract_cadence(text: str) -> str:
    """Extract PSUR cadence."""
    if re.search(r"annual(?:ly)?|every\s*(?:12|twelve)\s*months?|yearly", text, re.I):
        return "ANNUALLY"
    if re.search(r"every\s*(?:2|two)\s*years?|biennial|every\s*24\s*months?", text, re.I):
        return "EVERY_TWO_YEARS"
    return ""


def _extract_device_name(text: str) -> str:
    """Extract device name from the previous PSUR."""
    patterns = [
        r"device\s*(?:name|trade\s*name)[:\s]*[\"']?([^\n\"']{3,80})[\"']?",
        r"(?:PSUR|periodic\s*safety\s*update\s*report)\s*(?:for|:)\s*[\"']?([^\n\"']{3,80})[\"']?",
        r"subject\s*(?:device|product)[:\s]*[\"']?([^\n\"']{3,80})[\"']?",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.I)
        if m:
            name = m.group(1).strip().rstrip(".")
            # Filter out obviously wrong captures
            if len(name) > 3 and not name.lower().startswith(("section", "the ", "this ")):
                return name
    return ""


def _extract_manufacturer(text: str) -> str:
    """Extract manufacturer name from the previous PSUR."""
    patterns = [
        r"manufacturer[:\s]*[\"']?([^\n\"']{3,100})[\"']?",
        r"(?:prepared\s*by|authored\s*by|legal\s*manufacturer)[:\s]*[\"']?([^\n\"']{3,100})[\"']?",
        r"company\s*(?:name)?[:\s]*[\"']?([^\n\"']{3,100})[\"']?",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.I)
        if m:
            name = m.group(1).strip().rstrip(".")
            if len(name) > 3 and not name.lower().startswith(("section", "the ", "this ", "information")):
                return name
    return ""


def _split_into_sections(text: str, paragraphs: List[Dict]) -> Dict[str, str]:
    """Split the PSUR text into sections based on heading patterns."""
    sections = {}

    # Try paragraph-based splitting first (more reliable with styles)
    if paragraphs:
        current_section = None
        current_text = []
        for para in paragraphs:
            p_text = para.get("text", "")
            style = para.get("style", "").lower()

            # Check if this paragraph is a section heading
            is_heading = "heading" in style or style.startswith("h")
            matched_section = None
            for section_key, pattern in _HEADING_PATTERNS.items():
                if pattern.search(p_text):
                    matched_section = section_key
                    break

            if matched_section and (is_heading or len(p_text) < 100):
                # Save previous section
                if current_section:
                    sections[current_section] = "\n".join(current_text).strip()
                current_section = matched_section
                current_text = []
            elif current_section:
                current_text.append(p_text)

        if current_section:
            sections[current_section] = "\n".join(current_text).strip()

    # Fallback: line-based splitting
    if not sections:
        lines = text.split("\n")
        current_section = None
        current_text = []
        for line in lines:
            matched_section = None
            for section_key, pattern in _HEADING_PATTERNS.items():
                if pattern.search(line) and len(line.strip()) < 120:
                    matched_section = section_key
                    break
            if matched_section:
                if current_section:
                    sections[current_section] = "\n".join(current_text).strip()
                current_section = matched_section
                current_text = []
            elif current_section:
                current_text.append(line)
        if current_section:
            sections[current_section] = "\n".join(current_text).strip()

    return sections


def _extract_prior_actions(text: str, tables: List) -> List[Dict[str, str]]:
    """Extract prior actions/commitments from findings/conclusions section.

    Looks for:
    - Action items from Section M
    - Open/closed/in-progress commitments
    - CAPA references
    """
    actions = []

    # Patterns for action items
    action_patterns = [
        r"(?:action\s*(?:item|#?\d+)|commitment|recommendation)[:\s]*(.+?)(?:\n|$)",
        r"(?:open|closed|in.progress)\s*(?:action|item)[:\s]*(.+?)(?:\n|$)",
        r"(?:CAPA|corrective\s*action)\s*#?\d*[:\s]*(.+?)(?:\n|$)",
    ]

    for pattern in action_patterns:
        for m in re.finditer(pattern, text, re.I | re.M):
            action_text = m.group(1).strip()
            if len(action_text) > 10:
                # Determine status
                context = text[max(0, m.start() - 50):m.end() + 50].lower()
                if "closed" in context or "completed" in context or "resolved" in context:
                    status = "COMPLETED"
                elif "in.progress" in context or "ongoing" in context:
                    status = "IN_PROGRESS"
                else:
                    status = "OPEN"

                actions.append({
                    "description": action_text,
                    "status": status,
                })

    # Extract from tables — look for tables with action/status columns
    for table in tables:
        if not table or len(table) < 2:
            continue
        header = [str(cell).lower() for cell in table[0]]
        action_col = _find_col(header, ["action", "description", "item", "commitment"])
        status_col = _find_col(header, ["status", "state", "progress"])

        if action_col is not None:
            for row in table[1:]:
                if action_col < len(row):
                    desc = str(row[action_col]).strip()
                    status = str(row[status_col]).strip().upper() if status_col is not None and status_col < len(row) else ""
                    if desc and len(desc) > 5:
                        norm_status = "COMPLETED" if any(s in status.lower() for s in ("closed", "complete", "done")) else \
                                      "IN_PROGRESS" if any(s in status.lower() for s in ("progress", "ongoing", "open")) else \
                                      "OPEN"
                        actions.append({
                            "description": desc,
                            "status": norm_status,
                        })

    return actions


def _extract_complaint_summary(text: str, tables: List) -> Dict[str, Any]:
    """Extract complaint counts from the previous PSUR."""
    summary = {
        "total_complaints": None,
        "serious_incidents": None,
        "complaint_rate": None,
        "by_category": {},
    }

    # Extract total complaint count
    m = re.search(
        r"(?:total|overall)\s*(?:of\s*)?(\d+)\s*(?:complaint|product\s*complaint)",
        text, re.I
    )
    if m:
        summary["total_complaints"] = int(m.group(1))

    # Try reverse pattern: "complaints: 47" or "complaint count: 47"
    if summary["total_complaints"] is None:
        m = re.search(r"complaint\s*(?:count|total)[:\s]*(\d+)", text, re.I)
        if m:
            summary["total_complaints"] = int(m.group(1))

    # Serious incident count
    m = re.search(
        r"(\d+)\s*(?:serious\s*incident|reportable\s*event|vigilance\s*report)",
        text, re.I
    )
    if m:
        summary["serious_incidents"] = int(m.group(1))

    # Overall complaint rate
    m = re.search(
        r"(?:overall|total)\s*(?:complaint)?\s*rate[:\s]*(\d+\.?\d*)\s*(?:%|per)", text, re.I
    )
    if m:
        summary["complaint_rate"] = float(m.group(1))

    # Extract from complaint rate tables
    for table in tables:
        if not table or len(table) < 2:
            continue
        header = [str(cell).lower() for cell in table[0]]
        if any("complaint" in h or "imdrf" in h or "problem" in h for h in header):
            cat_col = _find_col(header, ["problem", "category", "imdrf", "type", "description"])
            count_col = _find_col(header, ["count", "number", "quantity", "total"])
            if cat_col is not None and count_col is not None:
                for row in table[1:]:
                    if cat_col < len(row) and count_col < len(row):
                        cat = str(row[cat_col]).strip()
                        try:
                            count = int(float(str(row[count_col]).strip()))
                            if cat and count > 0:
                                summary["by_category"][cat] = count
                        except (ValueError, TypeError):
                            pass

    return summary


def _extract_trend_data(text: str, tables: List) -> Dict[str, Any]:
    """Extract trend analysis data from the previous PSUR."""
    trend = {
        "status": "",
        "ucl": None,
        "mean_rate": None,
        "monthly_rates": [],
    }

    # Extract trend status
    if re.search(r"trend.*(?:stable|within\s*control)", text, re.I):
        trend["status"] = "STABLE"
    elif re.search(r"trend.*(?:increas|upward|above\s*UCL)", text, re.I):
        trend["status"] = "INCREASING"
    elif re.search(r"trend.*(?:decreas|downward)", text, re.I):
        trend["status"] = "DECREASING"

    # Extract UCL value
    m = re.search(r"UCL[:\s]*(\d+\.?\d*(?:e[+-]?\d+)?)", text, re.I)
    if m:
        trend["ucl"] = float(m.group(1))

    # Extract mean rate
    m = re.search(r"(?:mean|average)\s*(?:complaint)?\s*rate[:\s]*(\d+\.?\d*(?:e[+-]?\d+)?)", text, re.I)
    if m:
        trend["mean_rate"] = float(m.group(1))

    return trend


def _extract_serious_count(text: str) -> int:
    """Extract count of serious incidents from previous PSUR."""
    # "0 serious incidents" / "no serious incidents"
    m = re.search(r"(\d+)\s*serious\s*incident", text, re.I)
    if m:
        return int(m.group(1))
    if re.search(r"no\s*serious\s*incident", text, re.I):
        return 0
    return -1  # Unknown


def _extract_nb_review(text: str) -> str:
    """Extract notified body review status."""
    if re.search(r"notified\s*body.*(?:review|assess).*(?:completed|approved|accepted)", text, re.I):
        return "YES"
    if re.search(r"(?:not\s*(?:yet\s*)?reviewed|pending\s*(?:NB|notified\s*body))", text, re.I):
        return "NO"
    if re.search(r"first\s*PSUR|no\s*prior\s*PSUR", text, re.I):
        return "N_A"
    return ""


def _extract_sales_data(text: str, tables: List) -> Dict[str, Any]:
    """Extract sales/distribution data from previous PSUR for preceding periods."""
    sales = {
        "total_units": None,
        "by_region": {},
    }

    # Extract total units
    patterns = [
        r"(?:total|worldwide|global)\s*(?:sales|units\s*(?:sold|distributed))[:\s]*(\d[\d,]*)",
        r"(\d[\d,]*)\s*(?:total\s*)?units\s*(?:sold|distributed)",
        r"volume.*?(\d[\d,]*)\s*(?:units|devices)",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.I)
        if m:
            sales["total_units"] = int(m.group(1).replace(",", ""))
            break

    # Extract from sales tables
    for table in tables:
        if not table or len(table) < 2:
            continue
        header = [str(cell).lower() for cell in table[0]]
        if any("region" in h or "country" in h for h in header) and \
           any("unit" in h or "sales" in h or "quantity" in h for h in header):
            region_col = _find_col(header, ["region", "country", "market", "territory"])
            units_col = _find_col(header, ["units", "sales", "quantity", "volume", "distributed"])
            if region_col is not None and units_col is not None:
                for row in table[1:]:
                    if region_col < len(row) and units_col < len(row):
                        region = str(row[region_col]).strip()
                        try:
                            units = int(float(str(row[units_col]).strip().replace(",", "")))
                            if region and units > 0:
                                sales["by_region"][region] = units
                        except (ValueError, TypeError):
                            pass

    return sales


def _find_col(header: List[str], keywords: List[str]) -> Optional[int]:
    """Find column index by keyword matching."""
    for i, h in enumerate(header):
        for kw in keywords:
            if kw in h:
                return i
    return None
