"""IMDRF auto-coding module.

Uses Claude to assign IMDRF Medical Device Problem (Annex A)
and Health Impact / Harm (Annex E/F) codes when complaints lack them.

IMPORTANT — Output convention:
  • Public-facing fields (imdrf_code, harm_code, harm) contain TERMS ONLY
    (e.g. "Device breakage or deterioration"), never alphanumeric codes.
  • Internal fields (_imdrf_code_raw, _harm_code_raw) store the raw
    alphanumeric code for RACT matching, validation lineage, etc.
"""
import csv
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from llm_client import get_llm_client
from config import MODEL, HARM_MDP_PATH


# ---------------------------------------------------------------------------
# Load IMDRF codes from harm_mdp_codes.csv (single source of truth)
# ---------------------------------------------------------------------------

def _load_imdrf_from_csv(csv_path: Path) -> tuple:
    """Load IMDRF Annex A (MDP) and Harm codes from the CSV.

    Returns:
        (annex_a_dict, harm_dict)  — both are {code: term}
    """
    annex_a: Dict[str, str] = {}
    harm: Dict[str, str] = {}
    try:
        with open(csv_path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                row_type = (row.get("type") or "").strip()
                code = (row.get("code") or "").strip()
                term = (row.get("term") or "").strip()
                if not code or not term:
                    continue
                if row_type == "MDP":
                    annex_a[code] = term
                elif row_type == "HARM":
                    harm[code] = term
    except FileNotFoundError:
        pass  # Fall back to empty dicts — callers handle gracefully
    return annex_a, harm


IMDRF_ANNEX_A, IMDRF_ANNEX_F = _load_imdrf_from_csv(HARM_MDP_PATH)


# ---------------------------------------------------------------------------
# F2 (SKILL_PSUR_GENERATION): Deterministic Symptom Code -> IMDRF mapping.
# Eliminates the "Unknown / Not yet determined" failure mode by classifying
# every complaint to a leaf-node Harm + MDP without requiring an LLM call
# for the well-known patterns. Falls through to LLM only for true edge cases.
# ---------------------------------------------------------------------------

# Canonical leaf-node Harm and MDP terms used by the skill (term-only, no
# alphanumeric codes per the Block 3 formatting rules).
HARM_NO_HEALTH_CONSEQUENCE = "No Health Consequence or Impact"
HARM_LACERATION = "Skin/Subcutaneous Injury (Laceration)"
HARM_TISSUE_REACTION = "Tissue Reaction (Staple Migration/Extrusion)"

MDP_FAILURE_TO_FIRE = "Device Did Not Operate as Intended (Failure to Fire)"
MDP_FAILURE_DELIVER_STAPLE = "Failure to Deliver Staple Properly / Misfire"
MDP_COMPONENT_BROKEN = "Component Broken or Damaged"
MDP_FOREIGN_MATERIAL = "Foreign Material in/on Device"
MDP_MATERIAL_INTEGRITY = "Material Integrity / Adverse Tissue Response"
MDP_PERFORMANCE = "Performance Discrepancy"
MDP_MECHANISM_STIFFNESS = "Mechanism Stiffness / Joint Resistance"
MDP_PACKAGING_DAMAGE = "Packaging/Shipping Damage"
MDP_INCORRECT_QUANTITY = "Incorrect Quantity in Package"
MDP_DEFECTIVE_COMPONENT = "Defective Component"
MDP_WRONG_COMPONENT = "Wrong Component / Labeling Mismatch"
MDP_OTHER_PERFORMANCE = "Other Device Performance Problem"

# CSI Symptom Code -> (Harm term, MDP term). Keys are normalised to lowercase
# alphanumeric so the lookup tolerates whitespace, hyphens, underscores, and
# typical spelling variants.
SYMPTOM_CODE_MAP: Dict[str, tuple[str, str]] = {
    "laceration":              (HARM_LACERATION,             MDP_FAILURE_DELIVER_STAPLE),
    "doesnotperformproperly":  (HARM_NO_HEALTH_CONSEQUENCE,  MDP_FAILURE_TO_FIRE),
    "brokenordamagedcomponent":(HARM_NO_HEALTH_CONSEQUENCE,  MDP_COMPONENT_BROKEN),
    "foreignmaterial":         (HARM_NO_HEALTH_CONSEQUENCE,  MDP_FOREIGN_MATERIAL),
    "performance":             (HARM_NO_HEALTH_CONSEQUENCE,  MDP_PERFORMANCE),
    "rigidjoints":             (HARM_NO_HEALTH_CONSEQUENCE,  MDP_MECHANISM_STIFFNESS),
    "shippingdamage":          (HARM_NO_HEALTH_CONSEQUENCE,  MDP_PACKAGING_DAMAGE),
    "incorrectquantity":       (HARM_NO_HEALTH_CONSEQUENCE,  MDP_INCORRECT_QUANTITY),
    "defective":               (HARM_NO_HEALTH_CONSEQUENCE,  MDP_DEFECTIVE_COMPONENT),
    "wrongcomponent":          (HARM_NO_HEALTH_CONSEQUENCE,  MDP_WRONG_COMPONENT),
    "productsticking":         (HARM_NO_HEALTH_CONSEQUENCE,  MDP_MECHANISM_STIFFNESS),
}

# Narrative keyword fallback for `other` symptom codes (Step 3 of F2).
NARRATIVE_KEYWORD_RULES: List[tuple[List[str], str, str]] = [
    (["lacerat", "cut ", " nick", "bleed", "skin tear"],   HARM_LACERATION,            MDP_FAILURE_DELIVER_STAPLE),
    (["extrud", "migrat", "reject", "surface", "protrud"], HARM_TISSUE_REACTION,       MDP_MATERIAL_INTEGRITY),
    (["fire", "deploy", "staple", "jam", "stuck", "misfire", "won't close",
      "wont close", "did not close", "failure to close"],
                                                            HARM_NO_HEALTH_CONSEQUENCE, MDP_FAILURE_TO_FIRE),
    (["broken", "cracked", "snapped", "fracture"],          HARM_NO_HEALTH_CONSEQUENCE, MDP_COMPONENT_BROKEN),
    (["foreign", "particle", "debris", "contamin"],         HARM_NO_HEALTH_CONSEQUENCE, MDP_FOREIGN_MATERIAL),
    (["packag", "shipping", "dent", "crushed"],             HARM_NO_HEALTH_CONSEQUENCE, MDP_PACKAGING_DAMAGE),
    (["wrong", "mislabel", "labeling"],                     HARM_NO_HEALTH_CONSEQUENCE, MDP_WRONG_COMPONENT),
    (["expir", "expired"],                                  HARM_NO_HEALTH_CONSEQUENCE, MDP_OTHER_PERFORMANCE),
    (["stiff", "tight", "resistance"],                      HARM_NO_HEALTH_CONSEQUENCE, MDP_MECHANISM_STIFFNESS),
]

# Tokens the SKILL spec forbids appearing as Harm or MDP categories.
FORBIDDEN_HARM_TERMS = {
    "unknown", "unknown / not yet determined", "not yet determined",
    "f0601", "f0601 - unknown / not yet determined",
}
FORBIDDEN_MDP_TERMS = {
    "device issues, consequence or impact to patient or user unknown",
    "device issues, consequence or impact unknown",
    "a0302", "a0302 - device issues, consequence or impact unknown",
}


def _norm_token(value: Any) -> str:
    """Normalise a symptom/code string to lowercase alphanumeric."""
    if value is None:
        return ""
    return re.sub(r"[^a-z0-9]+", "", str(value).strip().lower())


def classify_complaint_skill(complaint: Dict[str, Any]) -> Optional[tuple[str, str, str]]:
    """Apply the SKILL_PSUR_GENERATION F2 deterministic classifier.

    Returns (harm_term, mdp_term, source) when a confident classification
    can be made without an LLM call, otherwise None. `source` is one of
    "symptom_code" or "narrative".
    """
    sym = _norm_token(complaint.get("symptom_code"))
    if sym and sym in SYMPTOM_CODE_MAP:
        harm, mdp = SYMPTOM_CODE_MAP[sym]
        return harm, mdp, "symptom_code"

    fault = _norm_token(complaint.get("fault_code"))
    if fault and fault in SYMPTOM_CODE_MAP:
        harm, mdp = SYMPTOM_CODE_MAP[fault]
        return harm, mdp, "symptom_code"

    # Step 3: narrative keyword matching. Search description + nonconformity
    # + investigation_findings, in priority order so injuries win over
    # generic device-issue keywords.
    narrative = " ".join(str(complaint.get(k, "")).lower() for k in (
        "nonconformity", "description", "narrative",
        "investigation_findings", "failure_mode",
    ))
    if narrative.strip():
        for keywords, harm, mdp in NARRATIVE_KEYWORD_RULES:
            if any(kw in narrative for kw in keywords):
                return harm, mdp, "narrative"

    return None


def force_safe_default(harm: str, mdp: str) -> tuple[str, str]:
    """Replace forbidden 'Unknown'-style terms with the SKILL safe defaults."""
    if not harm or harm.strip().lower() in FORBIDDEN_HARM_TERMS:
        harm = HARM_NO_HEALTH_CONSEQUENCE
    if not mdp or mdp.strip().lower() in FORBIDDEN_MDP_TERMS:
        mdp = MDP_OTHER_PERFORMANCE
    return harm, mdp


def apply_skill_classification(
    complaints: List[Dict[str, Any]],
) -> Dict[str, int]:
    """Apply the deterministic F2 mapping to a complaint list IN PLACE.

    Returns counters for telemetry: {"symptom_code": n, "narrative": n,
    "fallback_default": n, "left_for_llm": n}.
    """
    counters = {"symptom_code": 0, "narrative": 0,
                "fallback_default": 0, "left_for_llm": 0}
    for complaint in complaints:
        result = classify_complaint_skill(complaint)
        if result is not None:
            harm, mdp, source = result
            harm, mdp = force_safe_default(harm, mdp)
            complaint["harm"] = harm
            complaint["harm_code"] = harm
            complaint["imdrf_code"] = mdp
            complaint["imdrf_code_auto"] = True
            complaint["harm_code_auto"] = True
            complaint["_skill_classification_source"] = source
            counters[source] += 1
        else:
            # Coerce any forbidden terms even when no rule applied.
            harm = complaint.get("harm") or complaint.get("harm_code") or ""
            mdp = complaint.get("imdrf_code") or ""
            new_harm, new_mdp = force_safe_default(harm, mdp)
            if new_harm != harm or new_mdp != mdp:
                complaint["harm"] = new_harm
                complaint["harm_code"] = new_harm
                complaint["imdrf_code"] = new_mdp
                counters["fallback_default"] += 1
            else:
                counters["left_for_llm"] += 1
    return counters


# ---------------------------------------------------------------------------
# Utility: strip alphanumeric IMDRF codes from display strings
# ---------------------------------------------------------------------------

_CODE_PREFIX_RE = re.compile(
    r"^[A-F]\d{2,6}\s*[-–—]\s*",   # "A0701 - Device breakage" → "Device breakage"
)
_CODE_SUFFIX_RE = re.compile(
    r"\s*\([A-F]\d{2,6}\)\s*$",     # "Device Breakage (A0502)" → "Device Breakage"
)
_BARE_CODE_RE = re.compile(
    r"^[A-F]\d{2,6}$"               # Bare code with no term attached
)


def strip_imdrf_code(value: str) -> str:
    """Remove IMDRF alphanumeric codes from a display string, keeping term only.

    Examples:
        "A0701 - Device breakage or deterioration" → "Device breakage or deterioration"
        "Device Breakage (A0502)"                  → "Device Breakage"
        "F0101 - No Harm"                          → "No Harm"
        "No Harm"                                  → "No Harm"
        "A0701"                                    → looks up term from ANNEX_A/F dict
    """
    if not value or not isinstance(value, str):
        return value or ""
    v = value.strip()

    # Strip "CODE - term" prefix pattern
    v = _CODE_PREFIX_RE.sub("", v)
    # Strip "term (CODE)" suffix pattern
    v = _CODE_SUFFIX_RE.sub("", v)

    # If the result is now a bare code, try to resolve it
    if _BARE_CODE_RE.match(v):
        term = IMDRF_ANNEX_A.get(v) or IMDRF_ANNEX_F.get(v)
        if term:
            return term
        return v  # Return as-is if we can't resolve

    return v.strip()


def auto_code_complaints(
    complaints: List[Dict[str, Any]],
    device_context: Dict[str, Any] = None
) -> List[Dict[str, Any]]:
    """
    Auto-assign IMDRF codes to complaints that lack them.

    Args:
        complaints: List of complaint dicts with at least 'description' field
        device_context: Optional device info for coding context

    Returns:
        Same list with 'imdrf_code' and 'harm_code' populated where missing
    """
    needs_coding = []
    for i, c in enumerate(complaints):
        existing_code = c.get("imdrf_code", "")
        # Auto-code if missing OR if existing code isn't a valid IMDRF format (Axxxx)
        needs_problem = (
            not existing_code
            or existing_code in ("", "Unknown", "N/A", "nan")
            or not _is_valid_imdrf_code(existing_code)
        )
        existing_harm = c.get("harm_code", c.get("harm", ""))
        needs_harm = (
            not existing_harm
            or str(existing_harm) in ("", "Unknown", "N/A", "nan")
            or not _is_valid_imdrf_code(str(existing_harm))
        )
        if needs_problem or needs_harm:
            needs_coding.append((i, c, needs_problem, needs_harm))

    if not needs_coding:
        return complaints

    # Batch coding — send up to 20 at a time
    client = get_llm_client()
    batch_size = 20

    for batch_start in range(0, len(needs_coding), batch_size):
        batch = needs_coding[batch_start:batch_start + batch_size]
        _code_batch(client, complaints, batch, device_context)

    # Post-coding quality check: warn if >80% mapped to generic A0302
    coded = [c for c in complaints if c.get("imdrf_code", "")]
    if coded:
        a0302_count = sum(1 for c in coded if str(c.get("imdrf_code", "")).startswith("A0302"))
        pct = a0302_count / len(coded)
        if pct > 0.8:
            import sys
            print(
                f"  [IMDRF quality warning] {a0302_count}/{len(coded)} ({pct:.0%}) complaints "
                f"coded as A0302 (generic catch-all). Review complaint descriptions for specificity.",
                file=sys.stderr
            )

    return complaints


def _code_batch(
    client,
    complaints: List[Dict],
    batch: List[tuple],
    device_context: Optional[Dict]
):
    """Code a batch of complaints via LLM (Anthropic or OpenAI fallback)."""
    device_desc = ""
    if device_context:
        device_desc = f"""
Device: {device_context.get('device_name', 'medical device')}
Intended Use: {device_context.get('intended_use', '')}
Classification: {device_context.get('eu_mdr_classification', '')}
"""

    items = []
    for idx, (orig_idx, complaint, needs_problem, needs_harm) in enumerate(batch):
        desc = complaint.get("description", complaint.get("narrative", "No description"))
        failure_mode = complaint.get("failure_mode", "")
        device_problem = complaint.get("device_problem", "")
        event_type = complaint.get("event_type", "")
        extra_context = ""
        if failure_mode:
            extra_context += f" | Failure mode: {failure_mode}"
        if device_problem:
            extra_context += f" | Device problem: {device_problem}"
        if event_type:
            extra_context += f" | Event type: {event_type}"
        items.append(f"COMPLAINT {idx+1}: {desc[:800]}{extra_context}")

    system_prompt = f"""You are an IMDRF coding specialist for medical device complaints.
{device_desc}

## IMDRF Annex A — Medical Device Problem Codes
{json.dumps(IMDRF_ANNEX_A, indent=2)}

## IMDRF Annex F — Health Impact Codes
{json.dumps(IMDRF_ANNEX_F, indent=2)}

For each complaint, assign:
1. The most appropriate Annex A code (Medical Device Problem)
2. The most appropriate Annex F code (Health Impact / Harm)

Rules:
- Use the MOST SPECIFIC sub-code that fits (e.g. A070101 "Fracture / break of device" rather than A0701 "Device breakage or deterioration")
- ALWAYS include both the code AND the descriptive term in your output (e.g. "A070101" + "Fracture / break of device")

### CRITICAL: AVOID GENERIC CATCH-ALL CODES

- **A0302 ("Device issues, consequence or impact unknown")** should ONLY be used when the complaint provides absolutely NO information about what the device problem was — not even a vague hint. If there is ANY description of what went wrong, use a more specific code.
- **F0601 ("Unknown / Not yet determined")** should ONLY be used when harm is explicitly stated as unknown or under investigation. If no patient contact occurred or no adverse outcome is mentioned, use F0101 (No Harm) instead.

### COMMON DEVICE PROBLEM MAPPING HINTS
- Display/screen issues → A130105 (Interface / display error)
- Power/battery issues → A080104 (Battery failure) or A080105 (Power supply issue)
- Broken/fractured → A070101 (Fracture / break of device)
- Part detached → A070102 (Detachment of device component)
- Leak/flow problem → A100102 (Leak / flow issue)
- Won't turn on/operate → A020101 (Failure to operate as intended)
- Intermittent function → A020102 (Intermittent operation)
- Packaging issue → A0601 or sub-codes
- Labeling error → A1501 or sub-codes
- Software crash/freeze → A130101 (Software crash / freeze)
- Calibration drift → A110101 (Drift / loss of calibration)
- Material degradation → A040102 (Material degradation / discoloration)
- Cosmetic only, no impact → A030101 (Cosmetic / aesthetic issue only)

- If no harm occurred, use F0101 (No Harm) or F0102 (Near Miss)
- NEVER invent codes not in the lists above — if no exact sub-code fits, use the parent level code
- Output valid JSON only, no explanation"""

    user_prompt = f"""Code these complaints:

{chr(10).join(items)}

Output a JSON array with one object per complaint, each having:
- "complaint_number": the complaint number (1-indexed)
- "annex_a_code": the code like "A0301"
- "annex_a_term": the descriptive term
- "annex_f_code": the code like "F0101"
- "annex_f_term": the descriptive term

JSON only:"""

    response = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        temperature=0.0,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}]
    )

    content = response.content[0].text.strip()
    if content.startswith("```"):
        lines = content.split("\n")
        content = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])

    try:
        codings = json.loads(content)
    except json.JSONDecodeError:
        return  # Skip if LLM output is malformed

    for coding in codings:
        complaint_num = coding.get("complaint_number", 0)
        if 1 <= complaint_num <= len(batch):
            orig_idx, complaint, needs_problem, needs_harm = batch[complaint_num - 1]

            if needs_problem:
                code = coding.get("annex_a_code", "")
                term = coding.get("annex_a_term", "")
                if code:
                    # Public field: term only (no alphanumeric code)
                    display_term = term if term else IMDRF_ANNEX_A.get(code, code)
                    complaints[orig_idx]["imdrf_code"] = display_term
                    # Internal field: raw alphanumeric code for RACT matching
                    complaints[orig_idx]["_imdrf_code_raw"] = code
                    complaints[orig_idx]["imdrf_code_auto"] = True

            if needs_harm:
                code = coding.get("annex_f_code", "")
                term = coding.get("annex_f_term", "")
                if code:
                    # Public field: term only
                    display_term = term if term else IMDRF_ANNEX_F.get(code, code)
                    complaints[orig_idx]["harm_code"] = display_term
                    complaints[orig_idx]["harm"] = display_term
                    # Internal field: raw alphanumeric code
                    complaints[orig_idx]["_harm_code_raw"] = code
                    complaints[orig_idx]["harm_code_auto"] = True


def _is_valid_imdrf_code(code_str: str) -> bool:
    """Check if a string is a valid IMDRF code or known IMDRF term.

    Accepts:
        - Alphanumeric patterns: A0301, F0101, A070101
        - "CODE - Term" patterns: A0301 - Device deficiency
        - Known term-only values: Device breakage or deterioration, No Harm
    """
    if not code_str:
        return False
    s = str(code_str).strip()
    # Accept alphanumeric code patterns
    if re.match(r'^[A-F]\d{2,6}', s):
        return True
    # Accept known IMDRF terms (term-only format)
    term_lower = strip_imdrf_code(s).lower()
    all_terms = {v.lower() for v in IMDRF_ANNEX_A.values()} | {v.lower() for v in IMDRF_ANNEX_F.values()}
    return term_lower in all_terms
