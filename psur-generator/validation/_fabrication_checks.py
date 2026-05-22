"""Fabrication detection mixin for PSURValidator."""
import re
from typing import Any, Dict, List


class FabricationChecksMixin:
    """Detect fabricated identifiers, document numbers, dates, and example copying."""

    def _check_fabrication(self, psur: Dict[str, Any]) -> List[str]:
        """Detect likely fabricated identifiers, document numbers, and dates."""
        errors = []
        sections = psur.get("sections", {})
        sec_b = sections.get("B_scope_and_device_description", {})

        fabrication_patterns = [
            r"[A-Z]+-TD\d{2,4}",
            r"BEP-TD\d{2,4}",
            r"PMCF-TD\d{2,4}",
            r"DOC-\d{4,}",
            r"REF-\d{4,}",
        ]

        tech_info = sec_b.get("technical_information", {})
        assoc_docs = tech_info.get("associated_documents", [])
        for doc in assoc_docs:
            doc_num = doc.get("document_number", "")
            if doc_num:
                for pattern in fabrication_patterns:
                    if re.match(pattern, doc_num):
                        errors.append(
                            f"FABRICATION: Section B associated document number '{doc_num}' "
                            f"appears fabricated (matches pattern {pattern}). "
                            f"Document numbers must come from input data or be empty."
                        )
                        break

        rmf_num = tech_info.get("risk_management_file_number", "")
        if rmf_num:
            for pattern in fabrication_patterns:
                if re.match(pattern, rmf_num):
                    errors.append(
                        f"FABRICATION: risk_management_file_number '{rmf_num}' appears fabricated."
                    )
                    break

        dev_info_bd = sec_b.get("device_information_breakdown", {})
        mdr_devices = dev_info_bd.get("mdr_devices", {})
        udi_rows = mdr_devices.get("basic_udi_di_rows", [])
        for row_data in udi_rows:
            udi = row_data.get("basic_udi_di", "")
            if udi and not self._is_plausible_udi_di(udi):
                errors.append(
                    f"FABRICATION: basic_udi_di '{udi}' may be fabricated. "
                    f"UDI-DIs must come from input data."
                )

        dev_class = sec_b.get("device_classification", {})
        td_num = dev_class.get("eu_technical_documentation_number", "")
        _td_placeholder_patterns = [
            r"^TBD$", r"^N/?A$", r"^UNKNOWN$", r"^PLACEHOLDER",
            r"^EXAMPLE", r"^INSERT\b", r"^\[.*\]$", r"^X{2,}$", r"^_+$",
        ]
        if td_num:
            td_upper = td_num.strip().upper()
            for pat in _td_placeholder_patterns:
                if re.match(pat, td_upper, re.IGNORECASE):
                    errors.append(
                        f"FABRICATION: eu_technical_documentation_number '{td_num}' "
                        f"appears to be a placeholder, not a real document number."
                    )
                    break
        if td_num and len(td_num) > 30:
            errors.append(
                f"VERBOSE_PLACEHOLDER: eu_technical_documentation_number is too long "
                f"({len(td_num)} chars). Use 'N/A' when data is not available."
            )

        tech_info = sec_b.get("technical_information", {})
        rmf_num = tech_info.get("risk_management_file_number", "")
        if rmf_num and len(rmf_num) > 30:
            errors.append(
                f"VERBOSE_PLACEHOLDER: risk_management_file_number is too long "
                f"({len(rmf_num)} chars). Use 'N/A' when data is not available."
            )

        class_rule = dev_class.get("classification_rule_mdr_annex_viii", "")
        if class_rule and len(class_rule) > 30:
            errors.append(
                f"VERBOSE_PLACEHOLDER: classification_rule_mdr_annex_viii is too long "
                f"({len(class_rule)} chars). Use 'N/A' when data is not available."
            )

        grouping = sec_b.get("device_grouping_information", {})
        leading = grouping.get("leading_device", "")
        if leading and re.match(r"^TD\d{2,4}$", leading):
            errors.append(
                f"FABRICATION: leading_device '{leading}' appears derived from "
                f"filename number, not from actual device data."
            )

        self._check_narrative_date_fabrication(sec_b, errors, "B")

        return errors

    # ──────────────────────────────────────────────────────────────
    # Narrative identifier leakage detector
    # ──────────────────────────────────────────────────────────────
    # The LLM sometimes lifts specific identifiers (CAPA-782, specific
    # MDR numbers, complaint IDs, date-stamped events) from the PREVIOUS
    # PSUR context into the current-period narrative, producing claims
    # about events that did not occur in this period. We build an
    # "allowed identifiers" set from CURRENT-period parsed input data and
    # flag any narrative reference to an identifier pattern NOT in that
    # set.
    def _check_narrative_identifier_leakage(
        self,
        psur: Dict[str, Any],
        parsed_data: Dict[str, Any],
    ) -> List[str]:
        errors: List[str] = []
        if not parsed_data:
            return errors

        allowed: set = set()

        # Seed allowed set from current-period CAPA records
        capa = parsed_data.get("capa") or {}
        if isinstance(capa, dict):
            for rec in capa.get("capa_records", []) or []:
                for key in ("capa_number", "capa_id", "number", "id"):
                    v = rec.get(key) if isinstance(rec, dict) else None
                    if v:
                        allowed.add(str(v).strip().upper())

        # Seed from current-period complaint / MDR numbers
        complaints = parsed_data.get("complaints") or {}
        if isinstance(complaints, dict):
            for s in complaints.get("complaint_summaries", []) or []:
                for key in ("complaint_number", "mdr_number", "capa_number"):
                    v = s.get(key) if isinstance(s, dict) else None
                    if v:
                        allowed.add(str(v).strip().upper())

        # Seed from FSCA table
        for fsca in (parsed_data.get("fsca") or []):
            if isinstance(fsca, dict):
                for key in ("fsca_id", "reference_number", "id"):
                    v = fsca.get(key)
                    if v:
                        allowed.add(str(v).strip().upper())

        if not allowed:
            # No source-of-truth identifiers to compare against — skip.
            return errors

        # Patterns that look like identifiers the LLM might copy forward
        id_patterns = [
            re.compile(r"\bCAPA[-\s]?\d{2,6}\b", re.IGNORECASE),
            re.compile(r"\bMDR[-\s]?\d{3,10}\b", re.IGNORECASE),
            re.compile(r"\bFSCA[-\s]?[A-Z0-9\-]{3,20}\b", re.IGNORECASE),
            re.compile(r"\bCMP[-\s]?\d{3,10}\b", re.IGNORECASE),
        ]

        def _walk(node: Any, path: str = ""):
            if isinstance(node, dict):
                for k, v in node.items():
                    _walk(v, f"{path}.{k}" if path else k)
            elif isinstance(node, list):
                for i, v in enumerate(node):
                    _walk(v, f"{path}[{i}]")
            elif isinstance(node, str) and len(node) > 10:
                for pat in id_patterns:
                    for m in pat.findall(node):
                        norm = str(m).replace(" ", "").replace("-", "").upper()
                        matched = any(
                            norm == str(a).replace(" ", "").replace("-", "").upper()
                            or str(m).strip().upper() == str(a).strip().upper()
                            for a in allowed
                        )
                        if not matched:
                            errors.append(
                                f"NARRATIVE_LEAKAGE at {path}: identifier '{m}' "
                                f"referenced in current-period narrative but not "
                                f"present in any current-period input record. "
                                f"Likely carried forward from previous PSUR — "
                                f"remove or replace with a generic reference."
                            )

        _walk(psur.get("sections", {}))
        # De-duplicate while preserving order
        seen = set()
        unique = []
        for e in errors:
            if e not in seen:
                seen.add(e)
                unique.append(e)
        return unique

    @staticmethod
    def _is_plausible_udi_di(udi: str) -> bool:
        """Check if a UDI-DI looks plausible (basic format check)."""
        if not udi:
            return True
        if udi == "":
            return True
        if len(udi) < 10:
            return False
        return True

    def _check_narrative_date_fabrication(self, data: Any, errors: List[str], section: str):
        """Check for specific date claims in narratives that may be fabricated."""
        if isinstance(data, str) and len(data) > 50:
            date_claims = re.findall(
                r"(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}",
                data
            )
            if len(date_claims) > 3:
                errors.append(
                    f"FABRICATION WARNING: Section {section} narrative contains {len(date_claims)} "
                    f"specific date references ({', '.join(date_claims[:5])}...). "
                    f"Verify these dates come from input data."
                )
        elif isinstance(data, dict):
            for k, v in data.items():
                self._check_narrative_date_fabrication(v, errors, section)
        elif isinstance(data, list):
            for item in data:
                self._check_narrative_date_fabrication(item, errors, section)

    def _check_absence_of_evidence(self, psur: Dict[str, Any]) -> List[str]:
        """Check explicit N/A statements are present when data is absent."""
        import json as _json
        errors = []
        sections = psur.get("sections", {})
        stats = psur.get("_statistics", {})

        def _table_is_effectively_empty(value: Any) -> bool:
            if value is None:
                return True
            if isinstance(value, list):
                if len(value) == 0:
                    return True
                for row in value:
                    if isinstance(row, dict):
                        non_empty = [v for v in row.values() if v not in (None, "", [], {})]
                        if non_empty:
                            return False
                    elif row not in (None, ""):
                        return False
                return True
            if isinstance(value, dict):
                rows = value.get("rows")
                if isinstance(rows, list):
                    return _table_is_effectively_empty(rows)
                return len(value) == 0
            return False

        if stats.get("serious_incident_count", 0) == 0:
            sec_d = sections.get("D_information_on_serious_incidents", {})
            d_text = _json.dumps(sec_d)
            if not any(phrase in d_text.lower() for phrase in [
                "no serious incident", "zero serious incident",
                "no reportable incident", "none reported",
                "no serious adverse", "no incidents were reported"
            ]):
                errors.append(
                    "ABSENCE: Section D has 0 serious incidents but narrative does not "
                    "explicitly state this. Say 'No serious incidents were reported during "
                    "the reporting period.'"
                )

        sec_i = sections.get("I_corrective_and_preventive_actions", {})
        capa_table = sec_i.get("table_9_capa_initiated_current_reporting_period", None)
        if _table_is_effectively_empty(capa_table):
            i_text = _json.dumps(sec_i)
            if not any(phrase in i_text.lower() for phrase in [
                "no capa", "no corrective", "n/a", "not applicable",
                "none initiated", "none required", "no new capa"
            ]):
                errors.append(
                    "ABSENCE: Section I has no CAPAs but does not explicitly state this."
                )

        sec_h = sections.get("H_information_from_fsca", {})
        fsca_table = sec_h.get("table_8_fsca_initiated_current_period_and_open_fscas", None)
        if _table_is_effectively_empty(fsca_table):
            h_text = _json.dumps(sec_h)
            if not any(phrase in h_text.lower() for phrase in [
                "no fsca", "no field safety", "n/a", "not applicable",
                "none initiated", "none required", "no corrective action"
            ]):
                errors.append(
                    "ABSENCE: Section H has no FSCAs but does not explicitly state this."
                )

        sec_g = sections.get("G_information_from_trend_reporting", {})
        trend_table = sec_g.get("trend_reports_table", sec_g.get("trend_reports", None))
        if _table_is_effectively_empty(trend_table):
            g_text = _json.dumps(sec_g)
            if not any(phrase in g_text.lower() for phrase in [
                "no trend report", "no formal trend", "n/a", "not applicable",
                "none filed", "none submitted", "no reports were submitted"
            ]):
                errors.append(
                    "ABSENCE: Section G has no trend reports but does not explicitly state this."
                )

        return errors

    def _check_external_db_fabrication(self, psur: Dict[str, Any], parsed_data: Dict[str, Any]) -> List[str]:
        """Detect fabricated external database results in Section K.

        If no external_db data was provided in parsed_data, Section K must NOT
        contain specific report counts, percentages, or named findings.
        """
        import json as _json
        errors = []
        sections = psur.get("sections", {})
        sec_k = sections.get("K_review_of_external_databases_and_registries", {})
        if not sec_k:
            return errors

        # Check if external_db data was actually provided
        has_external_db = bool(parsed_data.get("external_db"))

        if has_external_db:
            return errors  # Data was provided; cannot flag as fabricated

        k_text = _json.dumps(sec_k).lower()

        # Detect specific report counts (e.g. "127 reports", "89 events")
        count_matches = re.findall(r"\b(\d+)\s+(?:reports?|events?|entries|results?|incidents?|alerts?|notices?|recalls?)\b", k_text)
        for count_str in count_matches:
            count = int(count_str)
            if count > 0:
                errors.append(
                    f"FABRICATION: Section K claims {count} external database reports/events "
                    f"but no external_db data was provided. All counts must be zero or absent."
                )

        # Detect specific percentage claims from external databases
        pct_matches = re.findall(r"(\d+\.?\d*)\s*%", k_text)
        for pct in pct_matches:
            errors.append(
                f"FABRICATION: Section K contains percentage '{pct}%' but no external_db "
                f"data was provided. Do NOT fabricate statistics from external sources."
            )

        # Detect named database findings
        db_finding_patterns = [
            (r"\bmaude\b.*?\b(\d+)", "FDA MAUDE"),
            (r"\beu\s+vigilance\b.*?\b(\d+)", "EU Vigilance"),
            (r"\bmhra\b.*?\b(\d+)\s+(?:report|alert|notice)", "MHRA"),
            (r"\bbfarm\b.*?\b(\d+)", "BfArM"),
            (r"\btga\b.*?\b(\d+)", "TGA"),
            (r"\bhealth\s+canada\b.*?\b(\d+)", "Health Canada"),
        ]
        for pattern, db_name in db_finding_patterns:
            if re.search(pattern, k_text, re.IGNORECASE):
                errors.append(
                    f"FABRICATION: Section K contains specific {db_name} findings but "
                    f"no external_db data was provided. This is fabricated content."
                )

        # Detect fabricated industry/market benchmarks
        benchmark_patterns = [
            r"industry\s+average",
            r"market\s+average",
            r"compares?\s+favorably",
            r"compares?\s+favourably",
            r"benchmark\s+rate",
        ]
        for pattern in benchmark_patterns:
            if re.search(pattern, k_text, re.IGNORECASE):
                errors.append(
                    f"FABRICATION: Section K contains benchmark/comparison language "
                    f"('{pattern}') but no external_db data was provided."
                )

        # Check Table 10 is empty when no data provided
        table_10 = sec_k.get("table_10_adverse_events_and_recalls_external_databases", [])
        if isinstance(table_10, list) and len(table_10) > 0:
            errors.append(
                f"FABRICATION: Section K Table 10 has {len(table_10)} rows but no "
                f"external_db data was provided. Table 10 must be empty []."
            )

        return errors

    def _check_literature_fabrication(self, psur: Dict[str, Any], parsed_data: Dict[str, Any]) -> List[str]:
        """Detect fabricated literature search results in Section J."""
        import json as _json
        errors = []
        sections = psur.get("sections", {})
        sec_j = sections.get("J_scientific_literature_review", {})
        if not sec_j:
            return errors

        has_literature = bool(parsed_data.get("literature"))

        if has_literature:
            return errors

        j_text = _json.dumps(sec_j).lower()

        # Check article count is null/0
        article_count = sec_j.get("number_of_relevant_articles_identified")
        if article_count is not None and article_count != 0:
            errors.append(
                f"FABRICATION: Section J claims {article_count} relevant articles but "
                f"no literature data was provided."
            )

        # Detect fabricated study references
        study_patterns = [
            r"\b(?:et\s+al\.?)\b",  # Author citations
            r"\b(?:pubmed|doi|pmid)\s*[:=]?\s*\d+",  # Database IDs
            r"\b(?:n\s*=\s*\d{2,})\b",  # Sample sizes
            r"\b(?:p\s*[<>=]\s*0\.\d+)\b",  # p-values
            r"\b(?:journal\s+of|annals\s+of|lancet|bmj)\b",  # Journal names
        ]
        for pattern in study_patterns:
            if re.search(pattern, j_text, re.IGNORECASE):
                errors.append(
                    f"FABRICATION: Section J contains study reference pattern '{pattern}' "
                    f"but no literature data was provided."
                )

        return errors

    def _check_pmcf_fabrication(self, psur: Dict[str, Any], parsed_data: Dict[str, Any]) -> List[str]:
        """Detect fabricated PMCF results in Section L."""
        import json as _json
        errors = []
        sections = psur.get("sections", {})
        sec_l = sections.get("L_pmcf", {})
        if not sec_l:
            return errors

        has_pmcf = bool(parsed_data.get("pmcf"))

        if has_pmcf:
            return errors

        l_text = _json.dumps(sec_l).lower()

        # Detect fabricated PMCF findings
        pmcf_fabrication_patterns = [
            (r"\b(\d+)\s+patients?\b", "patient counts"),
            (r"\b(\d+)\s+sites?\b", "site counts"),
            (r"\b(\d+)\s+(?:enrolled|subjects?|participants?)\b", "enrollment numbers"),
            (r"\bcomplication\s+rate\s+(?:of\s+)?(\d)", "complication rates"),
            (r"\bresponse\s+rate\s+(?:of\s+)?(\d)", "response rates"),
            (r"\bregistry\s+(?:data|results|enrollment)\b", "registry data"),
        ]
        for pattern, desc in pmcf_fabrication_patterns:
            if re.search(pattern, l_text, re.IGNORECASE):
                errors.append(
                    f"FABRICATION: Section L contains {desc} but no PMCF data was provided."
                )

        # Check Table 11 for fabricated entries with specific results
        table_11 = sec_l.get("table_11_pmcf_activities_and_results", [])
        if isinstance(table_11, list):
            for i, row in enumerate(table_11):
                if isinstance(row, dict):
                    findings = row.get("findings", "") or row.get("results", "") or ""
                    if isinstance(findings, str) and len(findings) > 100:
                        # Long findings text without PMCF data is likely fabricated
                        errors.append(
                            f"FABRICATION: Section L Table 11 row {i} has detailed findings "
                            f"({len(findings)} chars) but no PMCF data was provided."
                        )

        return errors

    def _check_trend_report_fabrication(self, psur: Dict[str, Any], parsed_data: Dict[str, Any]) -> List[str]:
        """Detect fabricated trend report details in Section G."""
        import json as _json
        errors = []
        sections = psur.get("sections", {})
        sec_g = sections.get("G_information_from_trend_reporting", {})
        if not sec_g:
            return errors

        g_text = _json.dumps(sec_g)

        # Detect fabricated trend report reference numbers (TR-YYYY-NNN pattern)
        tr_refs = re.findall(r"\bTR[-_]?\d{4}[-_]?\d{2,4}\b", g_text, re.IGNORECASE)
        if tr_refs:
            errors.append(
                f"FABRICATION: Section G contains trend report reference numbers "
                f"({', '.join(tr_refs)}). These must come from input data — "
                f"do not invent TR reference numbers."
            )

        # Detect fabricated CAPA references in trend context
        capa_in_g = re.findall(r"\bCAPA[-_]?\d{4}[-_]?\d{2,4}\b", g_text, re.IGNORECASE)
        # Cross-check with actual CAPA data
        has_capa = bool(parsed_data.get("capa"))
        capa_list = parsed_data.get("capa", {}).get("records", []) if isinstance(parsed_data.get("capa"), dict) else []
        known_capas = set()
        for c in capa_list:
            if isinstance(c, dict):
                cnum = c.get("capa_number", "") or c.get("number", "")
                if cnum:
                    known_capas.add(cnum.upper())

        for capa_ref in capa_in_g:
            if capa_ref.upper() not in known_capas:
                errors.append(
                    f"FABRICATION: Section G references CAPA '{capa_ref}' which does "
                    f"not appear in the input CAPA data."
                )

        # Detect fabricated regulatory submission dates for trend reports
        g_lower = g_text.lower()
        if re.search(r"(?:submitted|reported|notified)\s+(?:to\s+)?(?:mhra|competent\s+authority|notified\s+body)", g_lower):
            if not has_capa and "no trend report" not in g_lower:
                errors.append(
                    "FABRICATION WARNING: Section G claims regulatory submissions for "
                    "trend reports. Verify these dates/submissions come from input data."
                )

        return errors

    def _check_uk_rp_fabrication(self, psur: Dict[str, Any], device_context: Dict[str, Any]) -> List[str]:
        """Detect fabricated UK Responsible Person details."""
        errors = []
        sections = psur.get("sections", {})
        sec_b = sections.get("B_scope_and_device_description", {})
        if not sec_b:
            return errors

        import json as _json
        b_text = _json.dumps(sec_b).lower()

        # Check if UK RP was provided in device context
        uk_rp = device_context.get("uk_responsible_person", {})
        has_uk_rp = bool(
            (isinstance(uk_rp, dict) and uk_rp.get("name"))
            or (isinstance(uk_rp, str) and uk_rp.strip())
        )

        if has_uk_rp:
            return errors  # Data was provided

        # Known fabricated UK RP companies the LLM likes to hallucinate.
        # Scope entity markers to UK RP / UK Approved Body contexts so a valid
        # EU MDR Notified Body reference (e.g. BSI Group The Netherlands B.V.,
        # NB 2797) is not misclassified as a UK RP fabrication.
        broad_markers = ["northbank house", "sir thomas longley", "rochester", "kent me2"]
        scoped_markers = ["emergo", "ul limited", "bsi group", "sgs"]
        for marker in broad_markers:
            if marker in b_text:
                errors.append(
                    f"FABRICATION: Section B contains UK Responsible Person detail "
                    f"'{marker}' but no UK RP data was provided in device_context. "
                    f"Do NOT invent UK RP details."
                )
        for marker in scoped_markers:
            if re.search(
                rf"(uk responsible person|uk rp|approved body|ukca)[^.{{}}]{{0,120}}{re.escape(marker)}|"
                rf"{re.escape(marker)}[^.{{}}]{{0,120}}(uk responsible person|uk rp|approved body|ukca)",
                b_text,
            ):
                errors.append(
                    f"FABRICATION: Section B contains UK Responsible Person detail "
                    f"'{marker}' but no UK RP data was provided in device_context. "
                    f"Do NOT invent UK RP details."
                )

        # Detect any full UK address patterns when no UK RP provided
        uk_postcode = re.search(r"\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b", _json.dumps(sec_b))
        if uk_postcode:
            errors.append(
                f"FABRICATION: Section B contains UK postcode '{uk_postcode.group()}' "
                f"but no UK RP data was provided."
            )

        return errors

    def _check_example_copying(self, psur: Dict[str, Any]) -> List[str]:
        """Detect copied guidance/example boilerplate in generated content."""
        errors: List[str] = []
        sections = psur.get("sections", {})

        forbidden_markers = [
            "note: example only",
            "the following is an example only",
            "should be used as a guideline, not verbatim",
        ]

        def _walk(value: Any, path: str):
            if isinstance(value, str):
                low = value.lower()
                for marker in forbidden_markers:
                    if marker in low:
                        errors.append(
                            f"EXAMPLE_COPY: Guidance/example boilerplate found at {path}"
                        )
                        break
            elif isinstance(value, dict):
                for k, v in value.items():
                    _walk(v, f"{path}.{k}" if path else k)
            elif isinstance(value, list):
                for i, item in enumerate(value):
                    _walk(item, f"{path}[{i}]")

        _walk(sections, "sections")
        return errors

    def _check_manufacturer_consistency(self, psur: Dict[str, Any],
                                         device_context: Dict[str, Any] = None) -> List[str]:
        """Check that manufacturer identity is consistent across all sections.

        The legal manufacturer name on the cover page must match any manufacturer
        references in Section M and all other narrative sections.
        """
        import json as _json
        errors = []
        if not device_context:
            return errors

        # Resolve the canonical manufacturer name
        canonical = (
            device_context.get("manufacturer_name")
            or device_context.get("manufacturer_info", {}).get("company_name", "")
            or ""
        ).strip()
        if not canonical:
            return errors

        # Known fabricated alternatives the LLM likes to hallucinate
        _KNOWN_FAKES = [
            "neotech products", "neotech medical", "neotech",
            "ackrad laboratories", "ackrad",
        ]

        sections = psur.get("sections", {})
        for section_key, section_data in sections.items():
            if not isinstance(section_data, dict):
                continue
            text = _json.dumps(section_data).lower()
            for fake in _KNOWN_FAKES:
                if fake in text and fake not in canonical.lower():
                    letter = section_key.split("_")[0]
                    errors.append(
                        f"MANUFACTURER_CONSISTENCY: Section {letter} references "
                        f"'{fake}' but the legal manufacturer is '{canonical}'. "
                        f"All sections must use the same manufacturer identity."
                    )
                    break  # One error per section is enough

        # Also check cover vs M for SRN consistency
        cover_srn = (
            psur.get("psur_cover_page", {})
            .get("manufacturer_information", {})
            .get("manufacturer_srn", "")
        )
        m_text = _json.dumps(sections.get("M_findings_and_conclusions", {}))
        srn_re = re.compile(r"US-MF-\d{9}", re.IGNORECASE)
        m_srns = srn_re.findall(m_text)
        for srn in m_srns:
            if cover_srn and srn != cover_srn:
                errors.append(
                    f"MANUFACTURER_CONSISTENCY: Section M references SRN '{srn}' "
                    f"but cover page uses '{cover_srn}'. SRNs must match."
                )

        return errors
