"""Base agent that loads template schema and guidance for each section."""
import json
import time
from typing import Any, Dict, List, Optional
from llm_client import get_llm_client
from config import MODEL_REASONING, TEMPLATE_SCHEMA_PATH, SECTION_GUIDANCE_PATH, MDCG_KB_PATH

# Global token + timing accumulator (reset per generate_psur run)
_token_usage = {
    "input_tokens": 0,
    "output_tokens": 0,
    "api_calls": 0,
    "total_latency_s": 0.0,
}


def reset_token_usage():
    """Reset global token usage counters."""
    _token_usage["input_tokens"] = 0
    _token_usage["output_tokens"] = 0
    _token_usage["api_calls"] = 0
    _token_usage["total_latency_s"] = 0.0


def get_token_usage() -> dict:
    """Return a copy of the current token usage."""
    return dict(_token_usage)


class SectionAgent:
    """
    Ephemeral agent for generating one PSUR section.

    Uses:
    - template.json for structural constraints (what fields exist, types, validation)
    - psur_agent_guidance.json for content instructions (how to write each field)
    """

    # Generous token budget for comprehensive regulatory narratives.
    # PSURs must be thorough; each section needs ample room.
    MAX_TOKENS = 16384

    # No minimum word counts — depth is driven by the quality and
    # analytical substance the section demands, not by volume targets.

    def __init__(self, section_key: str, global_context: str = "", uk_market_detected: bool = False,
                 class_i_no_nb: bool = False):
        """
        Args:
            section_key: Key in the template, e.g., "A_executive_summary"
            global_context: Persistent context block (device, period, stats,
                rules) built once by the orchestrator and shared across
                all 13 section agents.
            uk_market_detected: Whether UK sales exist, triggering UK MDR
                regulatory requirements in section instructions.
            class_i_no_nb: Whether the device is Class I non-sterile/non-measuring
                (self-certified, no Notified Body involvement).
        """
        self.section_key = section_key
        self.global_context = global_context
        self.uk_market_detected = uk_market_detected
        self.class_i_no_nb = class_i_no_nb
        self.client = get_llm_client()

        # Load constraint files
        with open(TEMPLATE_SCHEMA_PATH) as f:
            self.template = json.load(f)

        with open(SECTION_GUIDANCE_PATH) as f:
            self.guidance = json.load(f)

        # Load MDCG 2022-21 regulatory knowledge base
        with open(MDCG_KB_PATH) as f:
            self._mdcg_kb = json.load(f)

        # Extract this section's schema and guidance
        self.section_schema = self._get_section_schema()
        self.section_guidance = self._get_section_guidance()

        # Extract MDCG 2022-21 section-specific regulatory context
        self.mdcg_core_principles = self._mdcg_kb.get("core_principles", {})
        self.mdcg_section_knowledge = (
            self._mdcg_kb.get("section_knowledge", {}).get(self.section_key, {})
        )
        self.mdcg_assessment_rules = self._mdcg_kb.get("annex_iii_data_assessment_rules", {})
        self.mdcg_annex_iv = self._mdcg_kb.get("annex_iv_psur_requirements", {})
        self.mdcg_terminology = self._mdcg_kb.get("terminology", {})

    def _get_section_schema(self) -> Dict[str, Any]:
        """Extract schema for this section from template.json, with all $ref resolved."""
        sections_schema = (
            self.template
            .get("schema", {})
            .get("properties", {})
            .get("sections", {})
        )

        # Handle $ref to $defs/sections
        if "$ref" in sections_schema:
            ref_path = sections_schema["$ref"]
            parts = ref_path.lstrip("#/").split("/")
            resolved = self.template.get("schema", {})
            for part in parts:
                resolved = resolved.get(part, {})
            sections_schema = resolved

        section = sections_schema.get("properties", {}).get(self.section_key, {})

        # Resolve all remaining $ref within this section's schema
        defs = self.template.get("schema", {}).get("$defs", {})
        return self._resolve_refs(section, defs)

    @staticmethod
    def _resolve_refs(schema: Any, defs: Dict[str, Any]) -> Any:
        """Recursively resolve all $ref in a schema against $defs."""
        if isinstance(schema, dict):
            if "$ref" in schema:
                ref_path = schema["$ref"]  # e.g., "#/$defs/TriState"
                parts = ref_path.lstrip("#/").split("/")
                # Navigate from $defs root (parts[0] is "$defs", parts[1] is the key)
                if len(parts) >= 2 and parts[0] == "$defs":
                    resolved = defs.get(parts[1], {})
                    # Merge any sibling keys (like ui hints) with the resolved def
                    siblings = {k: v for k, v in schema.items() if k != "$ref"}
                    if siblings:
                        merged = dict(resolved)
                        merged.update(siblings)
                        return SectionAgent._resolve_refs(merged, defs)
                    return SectionAgent._resolve_refs(resolved, defs)
                return schema
            return {k: SectionAgent._resolve_refs(v, defs) for k, v in schema.items()}
        elif isinstance(schema, list):
            return [SectionAgent._resolve_refs(item, defs) for item in schema]
        return schema

    def _get_section_guidance(self) -> Dict[str, Any]:
        """Extract guidance for this section from psur_agent_guidance.json."""
        return self.guidance.get("sections", {}).get(self.section_key, {})

    def generate(
        self,
        statistics: Dict[str, Any],
        device_context: Dict[str, Any],
        parsed_data: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Generate this section's content with auto-retry on validation failure.

        After parsing the LLM response, validates against the section schema.
        On failure, retries up to 2 times with error feedback.

        Args:
            statistics: Pre-calculated statistics (rates, UCL, trends)
            device_context: Device info (name, class, intended use, etc.)
            parsed_data: Raw parsed data (complaints list, CER text, etc.)

        Returns:
            Populated section matching template schema
        """
        from validation import PSURValidator
        validator = PSURValidator()

        system_prompt = self._build_system_prompt()
        user_prompt = self._build_user_prompt(statistics, device_context, parsed_data)
        max_tokens = self.MAX_TOKENS

        messages = [{"role": "user", "content": user_prompt}]
        best_result = None
        best_errors = None

        for attempt in range(3):  # initial + 2 retries
            t0 = time.time()
            # Retry API call up to 3 times on transient errors (timeout, server error)
            # Note: quota/rate-limit fallback to OpenAI is handled by llm_client
            api_response = None
            for api_retry in range(3):
                try:
                    api_response = self.client.messages.create(
                        model=MODEL_REASONING,
                        max_tokens=max_tokens,
                        temperature=0.1,
                        system=system_prompt,
                        messages=messages
                    )
                    break
                except Exception as api_err:
                    import sys
                    print(f"  [{self.section_key}] API error (retry {api_retry+1}/3): {api_err}", file=sys.stderr)
                    if api_retry < 2:
                        time.sleep(5 * (api_retry + 1))  # 5s, 10s backoff
                    else:
                        raise  # Re-raise after exhausting retries
            response = api_response
            elapsed = time.time() - t0

            # Accumulate token usage
            usage = getattr(response, "usage", None)
            if usage:
                _token_usage["input_tokens"] += getattr(usage, "input_tokens", 0)
                _token_usage["output_tokens"] += getattr(usage, "output_tokens", 0)
            _token_usage["api_calls"] += 1
            _token_usage["total_latency_s"] += elapsed

            content = response.content[0].text.strip()
            parsed = self._parse_json_response(content)

            # Validate against section schema
            schema_errors = validator.validate_section(self.section_key, parsed)
            if not schema_errors:
                # Schema is valid — check comprehensiveness
                depth_issues = self._check_narrative_depth(parsed)
                if depth_issues:
                    # Try one depth-enhancement pass
                    parsed = self._depth_enhancement_pass(parsed, depth_issues, system_prompt)
                parsed = self._table_completion_pass(parsed, system_prompt)
                return parsed

            # Track best attempt (fewest errors)
            if best_errors is None or len(schema_errors) < len(best_errors):
                best_result = parsed
                best_errors = schema_errors

            # If this was the last attempt, return best result with quality passes
            if attempt == 2:
                import sys
                print(
                    f"  [{self.section_key}] {len(best_errors)} schema errors after {attempt + 1} attempts",
                    file=sys.stderr
                )
                # Still try depth enhancement even on imperfect schema output
                depth_issues = self._check_narrative_depth(best_result)
                if depth_issues:
                    best_result = self._depth_enhancement_pass(best_result, depth_issues, system_prompt)
                best_result = self._table_completion_pass(best_result, system_prompt)
                return best_result

            # Build retry prompt with validation errors
            error_list = "\n".join(f"- {e}" for e in schema_errors[:20])

            # Also check for empty table cells and add to feedback
            table_empties = self._find_empty_table_cells(parsed)
            empty_cell_feedback = ""
            if table_empties:
                empty_samples = "\n".join(f"  - {e}" for e in table_empties[:15])
                empty_cell_feedback = f"""

Additionally, {len(table_empties)} table cell(s) are empty (None or blank):
{empty_samples}

Fill ALL empty cells: use "N/A" for text, 0 for counts, 0.00 for rates."""

            retry_content = f"""Your previous output for Section {self.section_key} had schema validation errors:

{error_list}
{empty_cell_feedback}

Please fix these issues and output the corrected JSON. Remember:
- Every required field must be present
- Enum fields must use ONLY the allowed values from the schema
- Array fields must be arrays, string fields must be strings
- Every table cell must be populated — no empty strings or null values
- Output ONLY valid JSON, no explanation."""

            messages = [
                {"role": "user", "content": user_prompt},
                {"role": "assistant", "content": content},
                {"role": "user", "content": retry_content},
            ]

        return best_result

    # ------------------------------------------------------------------
    # Audit-driven remediation pass
    # ------------------------------------------------------------------

    def remediate(
        self,
        section_content: Dict[str, Any],
        remediation_prompt: str,
        statistics: Optional[Dict[str, Any]] = None,
        device_context: Optional[Dict[str, Any]] = None,
        parsed_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Re-generate a section to fix compliance gaps found by the auditor.

        This method is called by the orchestrator's audit-remediation loop when
        ``run_json_audit`` identifies GAP or PARTIAL findings. It sends the
        original section JSON along with the auditor's specific findings and
        recommendations back to the LLM, instructing it to fix the gaps while
        preserving all existing valid content.

        Args:
            section_content: The current section JSON dict.
            remediation_prompt: Pre-built remediation instructions from
                ``SectionAuditResult.build_remediation_prompt()``.
            statistics: Pre-computed PSUR statistics (optional, for context).
            device_context: Device info (optional, for context).
            parsed_data: Raw parsed data (optional, for context).

        Returns:
            The remediated section JSON dict.
        """
        import sys

        if not remediation_prompt:
            return section_content

        system_prompt = self._build_system_prompt()

        # Build the combined remediation user prompt
        parts: List[str] = []

        parts.append(
            f"# SECTION {self.section_key.upper()} — COMPLIANCE REMEDIATION\n\n"
            f"The auditor found compliance gaps in your previous output. "
            f"You MUST fix ALL identified gaps while preserving all valid content.\n"
        )

        # Inject the audit findings and their remediation instructions
        parts.append(remediation_prompt)

        # Provide the current section JSON as context
        section_json = json.dumps(section_content, indent=2)
        max_chars = 40000
        if len(section_json) > max_chars:
            section_json = section_json[:max_chars] + "\n... [truncated]"
        parts.append(
            f"\n## CURRENT SECTION JSON (to be fixed)\n\n```json\n{section_json}\n```\n"
        )

        # Optionally inject statistics summaries for grounding
        if statistics:
            stats_json = json.dumps(statistics, indent=2, default=str)
            if len(stats_json) > 8000:
                stats_json = stats_json[:8000] + "\n... [truncated]"
            parts.append(f"\n## STATISTICS CONTEXT\n\n{stats_json}\n")

        if device_context:
            dc_json = json.dumps(device_context, indent=2, default=str)
            if len(dc_json) > 3000:
                dc_json = dc_json[:3000] + "\n... [truncated]"
            parts.append(f"\n## DEVICE CONTEXT\n\n{dc_json}\n")

        parts.append(
            "\n## OUTPUT INSTRUCTIONS\n\n"
            "1. Return the COMPLETE section JSON with all gaps fixed.\n"
            "2. Do NOT remove any existing valid content.\n"
            "3. For GAP findings: add the missing content entirely.\n"
            "4. For PARTIAL findings: expand and deepen the content.\n"
            "5. Maintain the exact same JSON schema structure.\n"
            "6. All numeric values must remain unchanged unless specifically flagged.\n"
            "7. Output ONLY valid JSON — no explanation text.\n"
        )

        user_prompt = "\n".join(parts)

        t0 = time.time()
        try:
            response = self.client.messages.create(
                model=MODEL_REASONING,
                max_tokens=self.MAX_TOKENS,
                temperature=0.15,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            elapsed = time.time() - t0

            usage = getattr(response, "usage", None)
            if usage:
                _token_usage["input_tokens"] += getattr(usage, "input_tokens", 0)
                _token_usage["output_tokens"] += getattr(usage, "output_tokens", 0)
            _token_usage["api_calls"] += 1
            _token_usage["total_latency_s"] += elapsed

            content = response.content[0].text.strip()
            remediated = self._parse_json_response(content)

            # Validate basic structure preserved
            from validation import PSURValidator
            validator = PSURValidator()
            schema_errors = validator.validate_section(self.section_key, remediated)
            if schema_errors:
                print(
                    f"  [{self.section_key}] Remediation produced {len(schema_errors)} "
                    f"schema errors — keeping original",
                    file=sys.stderr,
                )
                return section_content

            # Verify word count didn't regress
            old_words = self._count_narrative_words(section_content)
            new_words = self._count_narrative_words(remediated)
            if new_words < old_words * 0.8:
                print(
                    f"  [{self.section_key}] Remediation lost content "
                    f"({old_words} → {new_words} words) — keeping original",
                    file=sys.stderr,
                )
                return section_content

            print(
                f"  [{self.section_key}] Remediation: {old_words} → {new_words} words, "
                f"schema OK  [{elapsed:.1f}s]",
                file=sys.stderr,
            )
            return remediated

        except Exception as e:
            print(
                f"  [{self.section_key}] Remediation failed: {e}",
                file=sys.stderr,
            )
            return section_content

    # ------------------------------------------------------------------
    # Table completion pass — focused repair for empty cells
    # ------------------------------------------------------------------

    def _find_empty_table_cells(self, section: Dict[str, Any]) -> list:
        """Find all empty cells in tables within a section. Returns list of descriptions."""
        empties = []

        def _walk(obj, path):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if k.startswith("_"):
                        continue
                    child = f"{path}.{k}" if path else k
                    if isinstance(v, list) and v and isinstance(v[0], dict):
                        # Table found
                        for ri, row in enumerate(v):
                            if not isinstance(row, dict):
                                continue
                            for ck, cv in row.items():
                                if ck.startswith("_"):
                                    continue
                                if cv is None or (isinstance(cv, str) and cv.strip() == ""):
                                    empties.append(f"{child}[{ri}].{ck}")
                    else:
                        _walk(v, child)
            elif isinstance(obj, list):
                for i, item in enumerate(obj):
                    _walk(item, f"{path}[{i}]")

        _walk(section, "")
        return empties

    def _table_completion_pass(self, section: Dict[str, Any],
                                system_prompt: str) -> Dict[str, Any]:
        """Run a focused LLM pass to fill empty table cells.

        Sends the section JSON back with explicit instructions to fill
        every empty cell. Uses a smaller, targeted prompt.
        """
        empties = self._find_empty_table_cells(section)
        if not empties:
            return section

        import sys
        print(f"  [{self.section_key}] Table completion pass: {len(empties)} empty cell(s)", file=sys.stderr)

        empty_list = "\n".join(f"  - {e}" for e in empties[:40])
        repair_prompt = f"""The following Section {self.section_key} JSON has empty table cells that must be filled.

## EMPTY CELLS FOUND

{empty_list}

## CURRENT SECTION JSON

{json.dumps(section, indent=2)}

## INSTRUCTIONS

Return the COMPLETE section JSON with ALL empty cells filled:
- Text/string cells with no data → "N/A"
- Count/integer cells with no data → 0
- Rate/percentage cells with no data → 0.00
- Every table must have at least one data row
- Harm header rows in Table 7 may have null count/rate (they are grouping headers)
- Do NOT change any existing non-empty values
- Do NOT remove any existing fields or rows
- Output ONLY valid JSON, no explanation."""

        t0 = time.time()
        try:
            response = self.client.messages.create(
                model=MODEL_REASONING,
                max_tokens=self.MAX_TOKENS,
                temperature=0.0,
                system=system_prompt,
                messages=[{"role": "user", "content": repair_prompt}]
            )
            elapsed = time.time() - t0
            usage = getattr(response, "usage", None)
            if usage:
                _token_usage["input_tokens"] += getattr(usage, "input_tokens", 0)
                _token_usage["output_tokens"] += getattr(usage, "output_tokens", 0)
            _token_usage["api_calls"] += 1
            _token_usage["total_latency_s"] += elapsed

            content = response.content[0].text.strip()
            repaired = self._parse_json_response(content)

            # Verify repair actually reduced empty cells
            remaining = self._find_empty_table_cells(repaired)
            if len(remaining) < len(empties):
                print(f"  [{self.section_key}] Table repair: {len(empties)} → {len(remaining)} empty cells", file=sys.stderr)
                return repaired
            else:
                print(f"  [{self.section_key}] Table repair did not improve — keeping original", file=sys.stderr)
                return section
        except Exception as e:
            print(f"  [{self.section_key}] Table completion pass failed: {e}", file=sys.stderr)
            return section

    # ------------------------------------------------------------------
    # Narrative depth check — ensures comprehensive output
    # ------------------------------------------------------------------

    def _count_narrative_words(self, section: Dict[str, Any]) -> int:
        """Count total words across all narrative string fields in the section."""
        total = 0

        def _walk(obj):
            nonlocal total
            if isinstance(obj, str) and len(obj) > 50:
                total += len(obj.split())
            elif isinstance(obj, dict):
                for k, v in obj.items():
                    if k.startswith("_"):
                        continue
                    # Skip table arrays — they're data, not narrative
                    if isinstance(v, list) and v and isinstance(v[0], dict):
                        continue
                    _walk(v)
            elif isinstance(obj, list):
                for item in obj:
                    if isinstance(item, str):
                        _walk(item)

        _walk(section)
        return total

    def _find_thin_narratives(self, section: Dict[str, Any]) -> list:
        """Find narrative fields that are too thin (single short sentences)."""
        thin = []
        _NARRATIVE_KEYS = {
            "narrative_summary", "narrative_analysis", "summary",
            "summary_or_na_statement", "description", "device_description",
            "intended_purpose_use", "method_description_and_justification",
            "commentary_context_for_exceedances", "breaches_commentary_and_actions",
            "statement_if_not_applicable", "registries_reviewed_summary",
            "summary_of_new_data_performance_or_safety",
            "benefit_risk_profile_conclusion", "intended_benefits_achieved",
            "limitations_of_data_and_conclusion", "new_or_emerging_risks_or_new_benefits",
            "overall_performance_conclusion", "literature_search_methodology",
            "comparison_with_similar_devices", "estimated_size",
            "estimated_size_of_patient_population_exposed",
            "graph_reference", "upper_control_limit_definition",
            "new_incident_types_identified_this_cycle",
            "action_details_and_follow_up",
        }

        def _walk(obj, path):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if k.startswith("_"):
                        continue
                    child = f"{path}.{k}" if path else k
                    if k in _NARRATIVE_KEYS and isinstance(v, str):
                        wc = len(v.split())
                        if wc < 40 and len(v) > 10:
                            thin.append((child, wc, v[:100]))
                    elif isinstance(v, dict):
                        _walk(v, child)

        _walk(section, "")
        return thin

    def _check_narrative_depth(self, section: Dict[str, Any]) -> list:
        """Check for stub narratives that lack any substantive content.
        Returns list of issue descriptions, or empty list if OK.
        
        Does NOT enforce minimum word counts — length is driven by
        the analytical substance the section demands, not volume targets."""
        issues = []

        thin = self._find_thin_narratives(section)
        for path, wc, preview in thin[:5]:
            issues.append(
                f"Field '{path}' has only {wc} words: \"{preview}...\" — "
                f"Add the specific facts, analysis, or conclusions this "
                f"field requires. Write only what is needed, no padding."
            )

        return issues

    def _depth_enhancement_pass(self, section: Dict[str, Any],
                                 issues: list,
                                 system_prompt: str) -> Dict[str, Any]:
        """Run a focused LLM pass to expand thin narratives into comprehensive content."""
        import sys
        print(f"  [{self.section_key}] Depth enhancement pass: {len(issues)} issue(s)", file=sys.stderr)

        issue_list = "\n".join(f"  - {i}" for i in issues)
        enhance_prompt = f"""The following Section {self.section_key} JSON passes schema validation but \
some narrative fields are stubs that lack substantive content.

## DEPTH ISSUES FOUND

{issue_list}

## CURRENT SECTION JSON

{json.dumps(section, indent=2)}

## INSTRUCTIONS

Return the COMPLETE section JSON with stub narrative fields improved:
- Add the specific facts, interpretations, or conclusions that are MISSING.
- Write like a safety assessor summarising evidence. Every sentence must state \
a finding or conclusion — never explain the PSUR, the surveillance system, or \
how the report was prepared.
- Proportionality: null/routine findings get 1–3 sentences. Only significant \
safety signals warrant full paragraphs.
- DO NOT add meta-narration ('This section provides…', 'This reporting period \
establishes the baseline…').
- Use 'CooperSurgical' — never 'we', 'our', 'us', or 'I'.
- DO NOT restate conclusions already present in different words.
- DO NOT change numeric values, table data, enum values, or identifiers.
- A concise narrative that covers all relevant data is BETTER than a longer one \
that pads. Quality over quantity.
- Output ONLY valid JSON, no explanation."""

        t0 = time.time()
        try:
            response = self.client.messages.create(
                model=MODEL_REASONING,
                max_tokens=self.MAX_TOKENS,
                temperature=0.2,
                system=system_prompt,
                messages=[{"role": "user", "content": enhance_prompt}]
            )
            elapsed = time.time() - t0
            usage = getattr(response, "usage", None)
            if usage:
                _token_usage["input_tokens"] += getattr(usage, "input_tokens", 0)
                _token_usage["output_tokens"] += getattr(usage, "output_tokens", 0)
            _token_usage["api_calls"] += 1
            _token_usage["total_latency_s"] += elapsed

            content = response.content[0].text.strip()
            enhanced = self._parse_json_response(content)

            # Verify enhancement actually improved depth
            new_words = self._count_narrative_words(enhanced)
            old_words = self._count_narrative_words(section)
            if new_words > old_words:
                print(
                    f"  [{self.section_key}] Depth enhancement: {old_words} → {new_words} words",
                    file=sys.stderr,
                )
                return enhanced
            else:
                print(
                    f"  [{self.section_key}] Depth enhancement did not improve — keeping original",
                    file=sys.stderr,
                )
                return section
        except Exception as e:
            print(f"  [{self.section_key}] Depth enhancement pass failed: {e}", file=sys.stderr)
            return section

    @staticmethod
    def _parse_json_response(content: str) -> Dict[str, Any]:
        """Parse JSON from LLM response, handling markdown wrapping and trailing text."""
        # Clean markdown wrapping if present
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])

        # Try parsing as-is first
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass

        # Try extracting the outermost JSON object (handles trailing text)
        brace_depth = 0
        start = content.find("{")
        if start == -1:
            raise json.JSONDecodeError("No JSON object found in response", content, 0)
        for i in range(start, len(content)):
            if content[i] == "{":
                brace_depth += 1
            elif content[i] == "}":
                brace_depth -= 1
                if brace_depth == 0:
                    return json.loads(content[start:i+1])

        raise json.JSONDecodeError("No valid JSON object found", content, 0)

    def _build_mdcg_regulatory_context(self) -> str:
        """Build MDCG 2022-21 regulatory context block for this section.

        Injects section-specific regulatory purpose, required elements, and
        assessment criteria from the MDCG 2022-21 knowledge base so the LLM
        understands WHY each element is required and WHAT a Notified Body
        assessor expects.
        """
        if not self.mdcg_section_knowledge:
            return ""

        parts = []
        parts.append("## INTERNAL REGULATORY CONTEXT (do NOT cite or reference in output)")
        parts.append("")
        parts.append("The following regulatory guidance shapes what this section must cover and how data")
        parts.append("should be presented. Apply this guidance silently — NEVER mention the source document,")
        parts.append("its title, annex numbers, or article references in your generated text.")
        parts.append("")

        # Core PSUR principles (brief)
        parts.append("### PSUR Core Principles")
        parts.append(f"- **Purpose**: {self.mdcg_core_principles.get('psur_purpose', '')}")
        dual = self.mdcg_core_principles.get("dual_objectives", {})
        if dual:
            parts.append(f"- **Benefit-Risk Objective**: {dual.get('benefit_risk', '')}")
            parts.append(f"- **CAPA Reporting Objective**: {dual.get('capa_reporting', '')}")
        parts.append(f"- **Stand-alone Requirement**: {self.mdcg_core_principles.get('stand_alone_requirement', '')}")
        parts.append(f"- **Absent Data**: {self.mdcg_core_principles.get('absent_data_justification', '')}")
        parts.append("")

        # Section-specific regulatory purpose
        reg_purpose = self.mdcg_section_knowledge.get("regulatory_purpose", "")
        if reg_purpose:
            parts.append(f"### Regulatory Purpose of This Section")
            parts.append(reg_purpose)
            parts.append("")

        # Required elements per MDCG
        required = (
            self.mdcg_section_knowledge.get("required_elements")
            or self.mdcg_section_knowledge.get("required_elements_mdr_devices")
            or []
        )
        if required:
            parts.append("### Required Elements for This Section")
            for elem in required:
                parts.append(f"- {elem}")
            parts.append("")

        # Additional required elements for legacy devices (if present)
        legacy_required = self.mdcg_section_knowledge.get("required_elements_legacy_devices", [])
        if legacy_required:
            parts.append("### Additional Required Elements for Legacy Devices")
            for elem in legacy_required:
                parts.append(f"- {elem}")
            parts.append("")

        # Assessment context (what the Notified Body looks for)
        assessment = self.mdcg_section_knowledge.get("assessment_context", "")
        if assessment:
            parts.append("### Notified Body Assessment Focus")
            parts.append(assessment)
            parts.append("")

        # Benefit-risk statement requirement (for Section A)
        br_statement = self.mdcg_section_knowledge.get("benefit_risk_statement_requirement", "")
        if br_statement:
            parts.append("### Benefit-Risk Statement Requirement")
            parts.append(br_statement)
            parts.append("")

        # Grouping requirements (for Section B)
        grouping = self.mdcg_section_knowledge.get("grouping_requirements")
        if grouping and isinstance(grouping, dict):
            parts.append("### Device Grouping Requirements")
            for key, val in grouping.items():
                parts.append(f"- **{key.replace('_', ' ').title()}**: {val}")
            parts.append("")

        # Annex III data presentation rules (for data-heavy sections)
        annex_iii_pres = self.mdcg_section_knowledge.get("annex_iii_data_presentation")
        if annex_iii_pres and isinstance(annex_iii_pres, dict):
            parts.append("### Data Presentation Rules for This Section")
            for key, val in annex_iii_pres.items():
                if isinstance(val, list):
                    parts.append(f"- **{key.replace('_', ' ').title()}**:")
                    for item in val:
                        parts.append(f"  - {item}")
                else:
                    parts.append(f"- **{key.replace('_', ' ').title()}**: {val}")
            parts.append("")

        # Annex III assessment rules (for data-heavy sections)
        annex_iii_assess = self.mdcg_section_knowledge.get("annex_iii_assessment_rules", [])
        if annex_iii_assess:
            parts.append("### Assessment Rules for This Section")
            for rule in annex_iii_assess:
                parts.append(f"- {rule}")
            parts.append("")

        # Assessment rule (singular, for some sections)
        annex_iii_single = self.mdcg_section_knowledge.get("annex_iii_assessment_rule", "")
        if annex_iii_single:
            parts.append("### Assessment Rule")
            parts.append(annex_iii_single)
            parts.append("")

        # Section-specific MDCG fields (volume_of_sales_requirements, etc.)
        for special_key in [
            "volume_of_sales_requirements", "population_exposure_requirements",
            "table_requirements", "characterization_requirements",
            "literature_approach", "database_requirements",
            "pmcf_requirements", "conclusion_requirements",
            "data_collection_requirements", "trend_reporting_requirements",
            "imdrf_coding_requirements", "harm_assessment_requirements"
        ]:
            special_val = self.mdcg_section_knowledge.get(special_key)
            if special_val:
                header = special_key.replace("_", " ").title()
                parts.append(f"### {header}")
                if isinstance(special_val, list):
                    for item in special_val:
                        parts.append(f"- {item}")
                elif isinstance(special_val, dict):
                    for k, v in special_val.items():
                        if isinstance(v, list):
                            parts.append(f"- **{k.replace('_', ' ').title()}**:")
                            for sub in v:
                                parts.append(f"  - {sub}")
                        else:
                            parts.append(f"- **{k.replace('_', ' ').title()}**: {v}")
                else:
                    parts.append(str(special_val))
                parts.append("")

        # Global Annex III assessment rules (condensed, for all sections)
        if self.mdcg_assessment_rules:
            presentation_rules = self.mdcg_assessment_rules.get("data_presentation_rules", [])
            assessment_rules = self.mdcg_assessment_rules.get("data_assessment_rules", [])
            if presentation_rules or assessment_rules:
                parts.append("### Global Data Presentation & Assessment Framework")
                if presentation_rules:
                    parts.append("**Data Presentation Principles:**")
                    for rule in presentation_rules[:5]:  # Top 5 most relevant
                        parts.append(f"- {rule}")
                if assessment_rules:
                    parts.append("**Data Assessment Principles:**")
                    for rule in assessment_rules[:5]:  # Top 5 most relevant
                        parts.append(f"- {rule}")
                parts.append("")

        return "\n".join(parts)

    def _build_system_prompt(self) -> str:
        """Build system prompt: global context + agent-scoped instructions.

        Structure (broad → specific):
          1. Global persistent context (identity, device, stats, rules)
          2. Section task header
          3. Section schema (JSON structure)
          4. Section-specific guidance
          5. MDCG regulatory context
          6. Critical constraints (deduplicated — writing rules in global ctx)
          7. Fabrication prohibition (condensed)
          8. Conditional instructions (gated by section letter)
          9. Section addendum (A–M specific tips)
        """
        from agents.prompts.section_instructions import (
            build_section_constraints,
            build_fabrication_block,
            build_conditional_instructions,
            get_section_addendum,
        )

        prompt = f"""{self.global_context}

{'=' * 60}
SECTION TASK: {self.section_key}
{'=' * 60}

## SECTION SCHEMA

You MUST output valid JSON that EXACTLY matches this structure:

```json
{json.dumps(self.section_schema, indent=2)}
```

## SECTION-SPECIFIC GUIDANCE (from psur_agent_guidance.json)

{json.dumps(self.section_guidance, indent=2)}

{self._build_mdcg_regulatory_context()}"""

        prompt += "\n\n" + build_section_constraints()
        prompt += "\n\n" + build_fabrication_block()
        prompt += "\n\n" + build_conditional_instructions(
            self.section_key, self.uk_market_detected, self.class_i_no_nb
        )

        addendum = get_section_addendum(self.section_key)
        if addendum:
            prompt += "\n" + addendum

        return prompt

    def _build_user_prompt(
        self,
        statistics: Dict[str, Any],
        device_context: Dict[str, Any],
        parsed_data: Optional[Dict[str, Any]]
    ) -> str:
        """Build user prompt with data."""

        prompt = f"""Generate Section {self.section_key} for this PSUR.

## DEVICE CONTEXT

{json.dumps(device_context, indent=2)}

## PRE-CALCULATED STATISTICS (use these exact numbers)

{json.dumps(statistics, indent=2)}
"""

        if parsed_data:
            # Shared Working Context: prior section findings (dependency-aware)
            shared_ctx = parsed_data.get("_shared_context")
            if shared_ctx:
                prompt += f"\n{shared_ctx}\n"

            # Surface data warnings prominently BEFORE the data
            data_warning = parsed_data.get("_DATA_WARNING")
            if data_warning:
                prompt += f"""
## ⚠️ DATA AVAILABILITY WARNING ⚠️

{data_warning}

"""

            # Surface extra column context
            extra_cols = parsed_data.get("_extra_columns")
            if extra_cols:
                extra_str = json.dumps(extra_cols, indent=2, default=str)
                if len(extra_str) > 5000:
                    extra_str = extra_str[:5000] + "\n... [truncated]"
                prompt += f"""
## ADDITIONAL UNMAPPED DATA COLUMNS

The following columns were present in the source data but not mapped to standard fields.
They may contain relevant information — reference them if pertinent to this section.

{extra_str}

"""

            # Filter out internal keys from serialized data
            clean_data = {k: v for k, v in parsed_data.items() if not k.startswith("_")}
            data_str = json.dumps(clean_data, indent=2, default=str)

            # Intelligent truncation: never cut mid-JSON
            MAX_DATA_CHARS = 15000
            if len(data_str) > MAX_DATA_CHARS:
                # Try to reduce by trimming large complaint/sales arrays to exemplars
                trimmed = dict(clean_data)
                for array_key in ("complaints", "summaries", "records", "rows"):
                    for top_key in list(trimmed.keys()):
                        if isinstance(trimmed[top_key], dict):
                            inner = trimmed[top_key]
                            if array_key in inner and isinstance(inner[array_key], list) and len(inner[array_key]) > 20:
                                inner[array_key] = inner[array_key][:20]
                                inner[f"_{array_key}_note"] = f"Showing first 20 of {len(clean_data[top_key].get(array_key, []))} records. Full statistics are in PRE-CALCULATED STATISTICS above."
                        elif isinstance(trimmed[top_key], list) and len(trimmed[top_key]) > 20:
                            original_len = len(clean_data[top_key])
                            trimmed[top_key] = trimmed[top_key][:20]
                            trimmed[f"_{top_key}_note"] = f"Showing first 20 of {original_len} records."
                data_str = json.dumps(trimmed, indent=2, default=str)
                # If still too long, truncate at last complete JSON record
                if len(data_str) > MAX_DATA_CHARS:
                    # Find last complete object/array boundary before the limit
                    cutoff = data_str[:MAX_DATA_CHARS].rfind("},")
                    if cutoff > 0:
                        data_str = data_str[:cutoff + 1] + "\n  ... [remaining records omitted — see PRE-CALCULATED STATISTICS]\n}"
                    else:
                        data_str = data_str[:MAX_DATA_CHARS] + "\n... [truncated]"

            prompt += f"""
## ADDITIONAL DATA

{data_str}
"""

        prompt += """
Output the completed section as valid JSON matching the schema exactly."""

        return prompt
