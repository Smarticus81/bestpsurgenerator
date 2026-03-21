"""Formatting and tone checks mixin for PSURValidator."""
import json
import re
from typing import Any, Dict, List

from validation._helpers import deep_get, iter_string_fields


class FormattingChecksMixin:
    """Bullet, citation, markdown, tone, and narrative presence checks."""

    def _check_no_bullets(self, data: Any, path: str = "") -> List[str]:
        """Check no bullet points in string values."""
        errors = []
        if isinstance(data, str):
            for pattern in self.BULLET_PATTERNS:
                if pattern in data:
                    errors.append(f"Bullet points found at {path}")
                    break
        elif isinstance(data, dict):
            for k, v in data.items():
                errors.extend(self._check_no_bullets(v, f"{path}.{k}"))
        elif isinstance(data, list):
            for i, item in enumerate(data):
                errors.extend(self._check_no_bullets(item, f"{path}[{i}]"))
        return errors

    def _check_no_citations(self, data: Any, path: str = "") -> List[str]:
        """Check no regulation citations in narratives."""
        errors = []
        if isinstance(data, str) and len(data) > 50:
            for pattern in self.FORBIDDEN_PATTERNS:
                if re.search(pattern, data):
                    errors.append(f"Regulation citation found at {path}: pattern '{pattern}'")
                    break
        elif isinstance(data, dict):
            for k, v in data.items():
                errors.extend(self._check_no_citations(v, f"{path}.{k}"))
        elif isinstance(data, list):
            for i, item in enumerate(data):
                errors.extend(self._check_no_citations(item, f"{path}[{i}]"))
        return errors

    def _check_no_markdown_or_numbered_lists(self, psur: Dict[str, Any]) -> List[str]:
        """Reject markdown formatting and numbered list formatting in text fields."""
        errors: List[str] = []
        sections = psur.get("sections", {})

        numbered_list_re = re.compile(r"(?:^|\n)\s*\d+[\.)]\s+")
        markdown_heading_re = re.compile(r"(?:^|\n)\s*#{1,6}\s+")

        def _walk(value: Any, path: str):
            if isinstance(value, str):
                if "```" in value or "`" in value:
                    errors.append(f"FORMATTING: Markdown/code formatting found at {path}")
                    return
                if markdown_heading_re.search(value):
                    errors.append(f"FORMATTING: Markdown heading found at {path}")
                    return
                if numbered_list_re.search(value):
                    errors.append(f"FORMATTING: Numbered list formatting found at {path}")
                    return
            elif isinstance(value, dict):
                for k, v in value.items():
                    _walk(v, f"{path}.{k}" if path else k)
            elif isinstance(value, list):
                for i, item in enumerate(value):
                    _walk(item, f"{path}[{i}]")

        _walk(sections, "sections")
        return errors

    # ---- Narrative presence ----

    _SECTION_NARRATIVE_FIELDS = {
        "A_executive_summary": [
            "actions_and_status_from_previous_report",
            "notified_body_actions_taken",
            "justification_for_change",
            "impact_on_comparability",
            "high_level_summary_if_adversely_impacted",
        ],
        "B_scope_and_device_description": [
            "device_description", "intended_purpose_use", "indications",
            "contraindications", "target_populations", "market_status",
            "confirmation_of_ongoing_psur_obligation", "justification_for_grouping",
            "leading_device_rationale",
        ],
        "C_volume_of_sales_and_population_exposure": [
            "market_history", "narrative_analysis",
            "estimated_size_of_patient_population_exposed",
            "characteristics_of_patient_population_exposed",
        ],
        "D_information_on_serious_incidents": ["narrative_summary", "new_incident_types_identified_this_cycle"],
        "E_customer_feedback": ["summary"],
        "F_product_complaint_types_counts_and_rates": [
            "method_description_and_justification", "commentary_context_for_exceedances"
        ],
        "G_information_from_trend_reporting": [
            "upper_control_limit_definition", "breaches_commentary_and_actions", "statement_if_not_applicable"
        ],
        "H_information_from_fsca": ["summary_or_na_statement"],
        "I_corrective_and_preventive_actions": ["summary_or_na_statement"],
        "J_scientific_literature_review": ["literature_search_methodology", "summary_of_new_data_performance_or_safety"],
        "K_review_of_external_databases_and_registries": ["registries_reviewed_summary"],
        "L_pmcf": ["summary_or_na_statement"],
        "M_findings_and_conclusions": [
            "benefit_risk_profile_conclusion", "intended_benefits_achieved",
            "limitations_of_data_and_conclusion", "new_or_emerging_risks_or_new_benefits",
            "overall_performance_conclusion",
        ],
    }

    def _check_narrative_presence(self, psur: Dict[str, Any]) -> List[str]:
        """Check that every section has non-trivial narrative content (Q46+)."""
        errors = []
        sections = psur.get("sections", {})

        for section_key, narrative_fields in self._SECTION_NARRATIVE_FIELDS.items():
            section_data = sections.get(section_key, {})
            if not isinstance(section_data, dict):
                continue

            found_any = False
            for field in narrative_fields:
                val = deep_get(section_data, field)
                if isinstance(val, str) and len(val.strip()) > 30:
                    found_any = True
                    break

            if not found_any:
                short = section_key.split("_", 1)[0]
                errors.append(
                    f"NARRATIVE: Section {short} has no substantive narrative content "
                    f"(expected at least one of: {', '.join(narrative_fields)})"
                )

        return errors

    # ---- Narrative depth / substance checks ----

    # Stub-detection only — catches near-empty sections, not quality/depth.
    # Quality is driven by prompt guidance, not word count floors.
    _SECTION_MIN_WORDS = {
        "A_executive_summary": 40,
        "B_scope_and_device_description": 40,
        "C_volume_of_sales_and_population_exposure": 40,
        "D_information_on_serious_incidents": 40,
        "E_customer_feedback": 40,
        "F_product_complaint_types_counts_and_rates": 40,
        "G_information_from_trend_reporting": 40,
        "H_information_from_fsca": 30,
        "I_corrective_and_preventive_actions": 30,
        "J_scientific_literature_review": 30,
        "K_review_of_external_databases_and_registries": 30,
        "L_pmcf": 30,
        "M_findings_and_conclusions": 40,
    }

    # Fields that are expected to carry analytical prose (not just labels)
    _NARRATIVE_FIELD_NAMES = frozenset({
        "narrative_summary", "narrative_analysis", "summary",
        "summary_or_na_statement", "device_description", "intended_purpose_use",
        "benefit_risk_profile_conclusion", "overall_performance_conclusion",
        "method_description_and_justification", "commentary_context_for_exceedances",
        "breaches_commentary_and_actions", "statement_if_not_applicable",
        "literature_search_methodology", "summary_of_new_data_performance_or_safety",
        "registries_reviewed_summary", "market_history", "market_status",
        "estimated_size_of_patient_population_exposed",
        "characteristics_of_patient_population_exposed",
        "actions_and_status_from_previous_report", "high_level_summary_if_adversely_impacted",
        "new_incident_types_identified_this_cycle", "intended_benefits_achieved",
        "limitations_of_data_and_conclusion", "new_or_emerging_risks_or_new_benefits",
        "comparison_with_similar_devices", "indications", "contraindications",
        "target_populations", "confirmation_of_ongoing_psur_obligation",
        "upper_control_limit_definition", "action_details_and_follow_up",
    })

    def _count_section_narrative_words(self, section_data: Any) -> int:
        """Count total words in all string fields >50 chars, skipping table arrays."""
        total = 0
        if isinstance(section_data, dict):
            for k, v in section_data.items():
                if k.startswith("_"):
                    continue
                if isinstance(v, list) and v and isinstance(v[0], dict):
                    continue  # Skip table arrays
                total += self._count_section_narrative_words(v)
        elif isinstance(section_data, str) and len(section_data) > 50:
            total += len(section_data.split())
        elif isinstance(section_data, list):
            for item in section_data:
                total += self._count_section_narrative_words(item)
        return total

    def _check_narrative_depth(self, psur: Dict[str, Any]) -> List[str]:
        """Check that each section has minimum narrative depth (word count).

        A comprehensive PSUR requires substantive analytical prose in every
        section — not single-sentence placeholders.  This check flags sections
        that are egregiously thin.
        """
        errors = []
        sections = psur.get("sections", {})

        for section_key, min_words in self._SECTION_MIN_WORDS.items():
            section_data = sections.get(section_key, {})
            if not isinstance(section_data, dict):
                continue

            word_count = self._count_section_narrative_words(section_data)
            if word_count < min_words:
                short = section_key.split("_", 1)[0]
                errors.append(
                    f"DEPTH: Section {short} contains only {word_count} words of "
                    f"narrative content (minimum: {min_words}). A comprehensive PSUR "
                    f"requires substantive analytical prose, not thin placeholders."
                )

        return errors

    def _check_narrative_substance(self, psur: Dict[str, Any]) -> List[str]:
        """Check key narrative fields for analytical substance.

        Flags narratives that are < 40 words — these are almost certainly
        single-sentence placeholders rather than proper regulatory analysis.
        """
        errors = []
        sections = psur.get("sections", {})

        _THIN_THRESHOLD = 40  # words

        for section_key, section_data in sections.items():
            if not isinstance(section_data, dict):
                continue
            short = section_key.split("_", 1)[0]

            thin_fields = []
            self._find_thin_fields(section_data, "", thin_fields, _THIN_THRESHOLD)

            if len(thin_fields) >= 3:
                field_names = ", ".join(f[0] for f in thin_fields[:5])
                errors.append(
                    f"SUBSTANCE: Section {short} has {len(thin_fields)} narrative "
                    f"fields with fewer than {_THIN_THRESHOLD} words each "
                    f"({field_names}). Expand these into substantive analytical prose."
                )

        return errors

    def _find_thin_fields(self, obj: Any, path: str,
                          thin_list: list, threshold: int):
        """Recursively find narrative fields with fewer than `threshold` words."""
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k.startswith("_"):
                    continue
                child_path = f"{path}.{k}" if path else k
                if isinstance(v, str) and k in self._NARRATIVE_FIELD_NAMES:
                    word_count = len(v.split())
                    if 5 < word_count < threshold:
                        thin_list.append((k, word_count))
                elif isinstance(v, dict):
                    self._find_thin_fields(v, child_path, thin_list, threshold)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                if isinstance(item, dict):
                    self._find_thin_fields(item, f"{path}[{i}]", thin_list, threshold)

    # ---- Tone checks ----

    REASSURANCE_PATTERNS = [
        r"\bonly\s+a\s+minor\b",
        r"\bnothing\s+to\s+(?:be\s+)?concern",
        r"\bextremely\s+safe\b",
        r"\bvery\s+safe\b",
        r"\binherently\s+safe\b",
        r"\bno\s+(?:real|significant)\s+(?:risk|concern|danger)\b",
    ]

    MARKETING_PATTERNS = [
        r"\bindustry[\s-]leading\b",
        r"\bbest[\s-]in[\s-]class\b",
        r"\bsuperior\s+performance\b",
        r"\bgold\s+standard\b",
        r"\bworld[\s-]class\b",
        r"\bcutting[\s-]edge\b",
        r"\bstate[\s-]of[\s-]the[\s-]art\s+device\b",
        r"\bmarket[\s-]leading\b",
    ]

    FIRST_PERSON_SINGULAR_PATTERNS = [
        # Ban singular first-person ("I did", "my analysis") but ALLOW
        # plural first-person ("we", "our", "us") — the manufacturer's voice.
        r"\bmy\b",
    ]

    _PRONOUN_I_RE = re.compile(r"\bI\b")
    _SECTION_I_CONTEXT_RE = re.compile(
        r"(?:Section|Part|Annex|Class|Type|Phase|Step|Appendix|Category|Schedule|Table|Grade|Level|Stage|Group)s?\s+(?:[A-Z],?\s*)*I\b"
    )

    def _check_tone(self, psur: Dict[str, Any]) -> List[str]:
        """Check document tone: no reassurance, marketing, or singular first-person."""
        errors = []
        sections = psur.get("sections", {})
        all_text = json.dumps(sections)

        for pattern in self.REASSURANCE_PATTERNS:
            if re.search(pattern, all_text, re.IGNORECASE):
                errors.append(
                    f"TONE: Reassurance phrasing detected (pattern: {pattern}). "
                    f"Remove minimizing language."
                )
                break

        for pattern in self.MARKETING_PATTERNS:
            if re.search(pattern, all_text, re.IGNORECASE):
                errors.append(
                    f"TONE: Marketing language detected (pattern: {pattern}). "
                    f"Remove promotional claims."
                )
                break

        # Check for singular first-person ("I", "my") — use "we"/"our" instead
        for _path, text in iter_string_fields(sections):
            if len(text) < 20:
                continue
            for pattern in self.FIRST_PERSON_SINGULAR_PATTERNS:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    errors.append(
                        f"TONE: Singular first-person pronoun '{match.group()}' found. "
                        f"Use first-person plural ('we', 'our') throughout."
                    )
                    return errors
            if self._PRONOUN_I_RE.search(text):
                cleaned = self._SECTION_I_CONTEXT_RE.sub("", text)
                match = self._PRONOUN_I_RE.search(cleaned)
                if match:
                    after_match = cleaned[match.end():match.end() + 5]
                    if re.match(r"[IVX]", after_match.strip()):
                        continue
                    errors.append(
                        f"TONE: Singular first-person pronoun 'I' found in narrative. "
                        f"Use first-person plural ('we', 'our') throughout."
                    )
                    return errors

        return errors
