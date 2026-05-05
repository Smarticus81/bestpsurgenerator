"""Cross-section consistency checks mixin for PSURValidator."""
import json
import re
from typing import Any, Dict, List

from validation._helpers import iter_string_fields


class ConsistencyChecksMixin:
    """Period duration, date consistency, denominator, benefit-risk, rate precision,
    cross-section harm/sterile/regional consistency."""

    # ── Existing checks ──────────────────────────────────────────────

    def _check_period_duration(self, psur: Dict[str, Any]) -> List[str]:
        """Check that surveillance period duration is consistent."""
        errors = []
        cover = psur.get("psur_cover_page", {})
        dcp = cover.get("document_information", {}).get("data_collection_period", {})
        start = dcp.get("start_date", "")
        end = dcp.get("end_date", "")

        if not start or not end:
            return errors

        try:
            from datetime import datetime
            s = datetime.strptime(start, "%Y-%m-%d")
            e = datetime.strptime(end, "%Y-%m-%d")
            months = (e.year - s.year) * 12 + (e.month - s.month)

            sections = psur.get("sections", {})
            self._find_month_claims(sections, months, errors)
        except (ValueError, TypeError):
            pass

        return errors

    def _find_month_claims(self, data: Any, actual_months: int, errors: List[str]):
        """Find 'XX-month' duration claims that ASSERT the surveillance period.

        Only flags phrases where the duration explicitly modifies the
        reporting / surveillance / data collection / review / PSUR / evaluation
        period.  Narrative durations like "23-month sustained performance" are
        not constrained to match the surveillance window.
        """
        if isinstance(data, str) and len(data) > 20:
            pattern = re.compile(
                r"(\d+)\s*-?\s*month(?:s)?\s+"
                r"(?:reporting|surveillance|data\s+collection|review|PSUR|evaluation|assessment)\s+period",
                re.IGNORECASE,
            )
            for m in pattern.finditer(data):
                claimed = int(m.group(1))
                if abs(claimed - actual_months) > 1 and 6 < claimed < 120:
                    errors.append(
                        f"PERIOD: Narrative claims '{claimed}-month' period "
                        f"but surveillance period is {actual_months} months"
                    )
                    return
        elif isinstance(data, dict):
            for v in data.values():
                self._find_month_claims(v, actual_months, errors)
        elif isinstance(data, list):
            for item in data:
                self._find_month_claims(item, actual_months, errors)

    def _check_rate_precision(self, psur: Dict[str, Any]) -> List[str]:
        """Check rates are 2dp, percentages 1dp, counts are whole numbers."""
        errors = []
        sections = psur.get("sections", {})

        sec_f = sections.get("F_product_complaint_types_counts_and_rates", {})
        table7 = sec_f.get("table_7_complaint_rate_and_count", {})
        annual = table7.get("annual_format", {})
        rows = annual.get("rows", [])

        for i, row in enumerate(rows):
            for rate_key in ("current_12_month_complaint_rate", "complaint_rate"):
                rate = row.get(rate_key)
                if rate is not None and isinstance(rate, float):
                    formatted = f"{rate:.8f}"
                    sig_digits = formatted.rstrip("0").split(".")[-1]
                    if len(sig_digits) > 4:
                        mdp = row.get("medical_device_problem", f"row {i}")
                        errors.append(
                            f"PRECISION: Table 7 rate for '{mdp}' has excessive precision "
                            f"({rate}). Use 2 decimal places."
                        )
                        break

            for count_key in ("current_12_month_complaint_count", "complaint_count"):
                count = row.get(count_key)
                if count is not None and isinstance(count, float) and count != int(count):
                    errors.append(
                        f"PRECISION: Table 7 complaint count should be a whole number, got {count}"
                    )
                    break

        return errors

    def _check_date_consistency(self, psur: Dict[str, Any]) -> List[str]:
        """Check that data collection period dates are consistent across sections."""
        errors = []

        cover = psur.get("psur_cover_page", {})
        doc_info = cover.get("document_information", {})
        dcp = doc_info.get("data_collection_period", {})
        cover_start = dcp.get("start_date", "")
        cover_end = dcp.get("end_date", "")

        if not cover_start or not cover_end:
            return errors

        sections = psur.get("sections", {})

        sec_a = sections.get("A_executive_summary", {})
        a_dcp = sec_a.get("data_collection_period", {})
        if a_dcp:
            a_start = a_dcp.get("start_date", "")
            a_end = a_dcp.get("end_date", "")
            if a_start and a_start != cover_start:
                errors.append(
                    f"DATE_CONSISTENCY: Section A start date ({a_start}) "
                    f"differs from Cover Page ({cover_start})"
                )
            if a_end and a_end != cover_end:
                errors.append(
                    f"DATE_CONSISTENCY: Section A end date ({a_end}) "
                    f"differs from Cover Page ({cover_end})"
                )

        sec_b = sections.get("B_scope_and_device_description", {})
        b_dcp = sec_b.get("data_collection_period", sec_b.get("reporting_period", {}))
        if isinstance(b_dcp, dict):
            b_start = b_dcp.get("start_date", "")
            b_end = b_dcp.get("end_date", "")
            if b_start and b_start != cover_start:
                errors.append(
                    f"DATE_CONSISTENCY: Section B start date ({b_start}) "
                    f"differs from Cover Page ({cover_start})"
                )
            if b_end and b_end != cover_end:
                errors.append(
                    f"DATE_CONSISTENCY: Section B end date ({b_end}) "
                    f"differs from Cover Page ({cover_end})"
                )

        return errors

    def _check_denominator_consistency(self, psur: Dict[str, Any]) -> List[str]:
        """Check that the worldwide sales figure is used consistently as denominator."""
        errors = []
        sections = psur.get("sections", {})
        stats = psur.get("_statistics", {})

        total_units = stats.get("total_units_sold", 0)
        if total_units == 0:
            return errors

        sec_f = sections.get("F_product_complaint_types_counts_and_rates", {})
        table7 = sec_f.get("table_7_complaint_rate_and_count", {})
        annual = table7.get("annual_format", {})
        grand_total = annual.get("grand_total", {})

        gt_rate = grand_total.get("complaint_rate")
        gt_count = grand_total.get("complaint_count")
        if gt_rate is not None and gt_count is not None and gt_count > 0 and total_units > 0:
            expected_rate_pct = round((gt_count / total_units) * 100, 2)
            expected_rate_raw = round(gt_count / total_units, 6)
            pct_match = abs(gt_rate - expected_rate_pct) <= 0.01
            raw_match = abs(gt_rate - expected_rate_raw) <= 0.000001
            if not pct_match and not raw_match:
                errors.append(
                    f"DENOMINATOR: Section F Table 7 grand total rate ({gt_rate}) does not match "
                    f"expected rate from {gt_count}/{total_units:,} units "
                    f"(expected {expected_rate_pct}% or {expected_rate_raw}). "
                    f"Rate must be computed from actual complaint count and units sold."
                )

        return errors

    def _check_benefit_risk_thread(self, psur: Dict[str, Any]) -> List[str]:
        """Check that benefit-risk linkage appears in key sections."""
        errors = []
        sections = psur.get("sections", {})

        br_required_sections = [
            "A_executive_summary",
            "B_scope_and_device_description",
            "C_volume_of_sales_and_population_exposure",
            "D_information_on_serious_incidents",
            "E_customer_feedback",
            "F_product_complaint_types_counts_and_rates",
            "G_information_from_trend_reporting",
            "H_information_from_fsca",
            "I_corrective_and_preventive_actions",
            "J_scientific_literature_review",
            "K_review_of_external_databases_and_registries",
            "L_pmcf",
            "M_findings_and_conclusions",
        ]

        br_phrases = [
            "benefit-risk", "benefit risk", "risk-benefit", "risk benefit",
            "risk profile", "benefit profile", "safety profile",
        ]

        for section_key in br_required_sections:
            section_data = sections.get(section_key, {})

            if section_key == "A_executive_summary":
                brac = section_data.get("benefit_risk_assessment_conclusion", {})
                if isinstance(brac, dict) and brac.get("conclusion"):
                    continue

            if section_key == "M_findings_and_conclusions":
                brpc = section_data.get("benefit_risk_profile_conclusion")
                if isinstance(brpc, str) and len(brpc.strip()) > 20:
                    continue

            narrative_text = " ".join(
                s.lower() for _, s in iter_string_fields(section_data) if len(s.strip()) > 20
            )
            if not any(phrase in narrative_text for phrase in br_phrases):
                short = section_key.split("_", 1)[0]
                errors.append(
                    f"BENEFIT_RISK: Section {short} does not reference the benefit-risk profile. "
                    f"Every section must connect findings to the overall benefit-risk determination."
                )

        sec_a = sections.get("A_executive_summary", {})
        sec_m = sections.get("M_findings_and_conclusions", {})
        a_conclusion = sec_a.get("benefit_risk_conclusion", "")
        m_conclusion = sec_m.get("benefit_risk_profile_conclusion", "")
        if a_conclusion and m_conclusion:
            a_text = json.dumps(a_conclusion).lower()
            m_text = json.dumps(m_conclusion).lower()
            a_adverse = "adversely_impacted" in a_text and "not_adversely" not in a_text
            m_adverse = any(w in m_text for w in ["adversely impacted", "has been adversely"])
            m_unchanged = any(w in m_text for w in ["not adversely", "unchanged", "remains acceptable"])
            if a_adverse and m_unchanged:
                errors.append(
                    "BENEFIT_RISK: Section A says 'ADVERSELY_IMPACTED' but "
                    "Section M conclusion says benefit-risk unchanged. These must be consistent."
                )
            elif not a_adverse and m_adverse:
                errors.append(
                    "BENEFIT_RISK: Section A says 'NOT_ADVERSELY_IMPACTED' but "
                    "Section M narrative suggests adverse impact. These must be consistent."
                )

        return errors

    # ── NEW cross-section consistency checks ─────────────────────────

    def _check_serious_incident_consistency(self, psur: Dict[str, Any]) -> List[str]:
        """Check D (serious incidents) vs F (complaint harm classifications) consistency.

        If D says zero serious incidents, F's Table 7 should not show 'Serious Injury'
        harm rows, and vice versa.
        """
        errors = []
        sections = psur.get("sections", {})

        sec_d = sections.get("D_information_on_serious_incidents", {})
        sec_f = sections.get("F_product_complaint_types_counts_and_rates", {})

        # Get D's serious incident narrative
        d_narrative = json.dumps(sec_d).lower()
        d_says_zero = (
            "zero serious incidents" in d_narrative
            or "no serious incidents" in d_narrative
            or "0 serious incidents" in d_narrative
        )

        # Check F's Table 7 for serious injury harm rows
        table7 = sec_f.get("table_7_complaint_rate_and_count", {})
        annual = table7.get("annual_format", {})
        rows = annual.get("rows", [])

        f_has_serious_injury = any(
            "serious" in str(row.get("harm", "")).lower()
            and "injury" in str(row.get("harm", "")).lower()
            for row in rows
            if str(row.get("harm", "")).lower() != "grand total"
        )

        # Check F narrative for serious injury references
        f_narrative = json.dumps(sec_f).lower()
        f_mentions_serious_injury = (
            "serious injury" in f_narrative or "serious harm" in f_narrative
        )

        if d_says_zero and (f_has_serious_injury or f_mentions_serious_injury):
            errors.append(
                "CROSS_SECTION: Section D says 'zero serious incidents' but Section F "
                "contains 'Serious Injury' harm classifications. These must be consistent. "
                "Either D should report the incidents, or F should not classify them as serious."
            )

        return errors

    def _check_sterile_consistency(self, psur: Dict[str, Any],
                                    device_context: Dict[str, Any] = None) -> List[str]:
        """Check that sterile/non-sterile terminology is consistent with device_context."""
        errors = []
        if not device_context:
            return errors

        sterile_raw = (device_context.get("sterility_status") or "").lower()
        is_non_sterile = sterile_raw in ("non-sterile", "nonsterile", "no", "false")

        if not is_non_sterile:
            return errors  # Only check for non-sterile contradictions

        sections = psur.get("sections", {})
        # Check Sections B, F, and M narratives for sterile contradictions
        check_sections = [
            ("B", "B_scope_and_device_description"),
            ("F", "F_product_complaint_types_counts_and_rates"),
            ("M", "M_findings_and_conclusions"),
        ]

        sterile_patterns = re.compile(
            r"\bsterile\s+(?:single[- ]use|disposable|medical)\s+device",
            re.IGNORECASE,
        )

        for label, key in check_sections:
            sec_data = sections.get(key, {})
            text = json.dumps(sec_data)
            if sterile_patterns.search(text):
                errors.append(
                    f"STERILE_CONSISTENCY: Section {label} describes device as 'sterile' "
                    f"but device_context says '{sterile_raw}'. Terms must match."
                )

        return errors

    def _check_regional_total_consistency(self, psur: Dict[str, Any]) -> List[str]:
        """Check that Section C and Section M use same regional sales totals."""
        errors = []
        sections = psur.get("sections", {})

        sec_c = sections.get("C_volume_of_sales_and_population_exposure", {})
        sec_m = sections.get("M_findings_and_conclusions", {})

        if not sec_c or not sec_m:
            return errors

        # Extract C's region numbers from the section (look for Table 1 data)
        c_table = sec_c.get("table_1_volume_of_sales_and_population_exposure", {})
        c_rows = c_table.get("rows", [])

        c_worldwide = None
        for row in c_rows:
            region = str(row.get("region", "")).lower()
            if "worldwide" in region or "global" in region or "total" in region:
                c_worldwide = row.get("units_current_period")
                break

        if c_worldwide is None:
            return errors

        # Check M's narrative for inconsistent worldwide unit claims
        m_text = json.dumps(sec_m)
        # Look for unit counts in M that don't match C's total
        unit_mentions = re.findall(r"(\d[\d,]*)\s*(?:units|devices)\s*(?:distributed|sold|placed)", m_text, re.IGNORECASE)
        for mention in unit_mentions:
            num = int(mention.replace(",", ""))
            if num > 100 and abs(num - c_worldwide) > 1 and num != c_worldwide:
                errors.append(
                    f"REGIONAL_CONSISTENCY: Section M mentions {num:,} units "
                    f"but Section C Table 1 worldwide total is {c_worldwide:,}. "
                    f"Totals must match."
                )
                break

        return errors

    def _check_actions_capa_consistency(self, psur: Dict[str, Any]) -> List[str]:
        """Check G (described actions) vs H (no FSCA) and I (no CAPA) for logic gaps.

        If G describes corrective actions (lot quarantine, process improvements),
        H and I should address why those were not FSCAs/CAPAs.
        """
        errors = []
        sections = psur.get("sections", {})

        sec_g = sections.get("G_information_from_trend_reporting", {})
        sec_h = sections.get("H_information_from_fsca", {})
        sec_i = sections.get("I_corrective_and_preventive_actions", {})

        if not sec_g:
            return errors

        g_text = json.dumps(sec_g).lower()

        # Detect if G describes corrective actions
        action_indicators = [
            "lot quarantine", "quarantined", "process improvement",
            "production hold", "root cause investigation", "corrective action",
            "implemented changes", "process adjustment", "containment action",
        ]
        g_describes_actions = any(indicator in g_text for indicator in action_indicators)

        if not g_describes_actions:
            return errors

        # Check H for FSCA acknowledgment
        h_text = json.dumps(sec_h).lower()
        h_says_no_fsca = (
            "no field safety corrective action" in h_text
            or "no fsca" in h_text
            or "were not initiated" in h_text
        )
        h_addresses_g = any(phrase in h_text for phrase in [
            "section g", "trend", "did not meet", "threshold", "did not constitute",
            "did not require", "not warrant",
        ])

        if h_says_no_fsca and not h_addresses_g:
            errors.append(
                "CROSS_SECTION: Section G describes corrective actions but Section H "
                "says 'no FSCAs' without addressing why G's actions did not constitute "
                "an FSCA. Section H must provide explicit rationale."
            )

        # Check I for CAPA acknowledgment
        i_text = json.dumps(sec_i).lower()
        i_says_no_capa = (
            "no corrective and preventive action" in i_text
            or "no capa" in i_text
            or "were not initiated" in i_text
        )
        i_addresses_g = any(phrase in i_text for phrase in [
            "section g", "trend", "did not meet", "threshold", "routine quality",
            "did not warrant", "did not require", "not escalated",
        ])

        if i_says_no_capa and not i_addresses_g:
            errors.append(
                "CROSS_SECTION: Section G describes corrective actions but Section I "
                "says 'no CAPAs' without addressing why G's actions did not warrant "
                "formal CAPA initiation. Section I must provide risk-based rationale."
            )

        return errors

    def _check_class_nb_consistency(self, psur: Dict[str, Any],
                                     device_context: Dict[str, Any] = None) -> List[str]:
        """Check that Class I non-sterile devices don't reference NB oversight in narratives."""
        errors = []
        if not device_context:
            return errors

        eu_class_raw = (device_context.get("device_class_eu") or "").upper()
        is_class_i = "CLASS I" in eu_class_raw and "CLASS II" not in eu_class_raw
        sterile_raw = (device_context.get("sterility_status") or "").lower()
        is_sterile = sterile_raw in ("sterile", "yes", "true")

        if not (is_class_i and not is_sterile):
            return errors  # Only applies to Class I non-sterile

        sections = psur.get("sections", {})
        nb_reference_re = re.compile(
            r"(?:notified\s+body|NB)\s+(?:review|audit|opinion|assessment|finding|action|"
            r"will\s+review|submission\s+to|submitted\s+to)",
            re.IGNORECASE,
        )

        for section_key, section_data in sections.items():
            if not isinstance(section_data, dict):
                continue
            text = json.dumps(section_data)
            matches = nb_reference_re.findall(text)
            if matches:
                letter = section_key.split("_")[0]
                errors.append(
                    f"CLASS_NB_CONSISTENCY: Section {letter} references Notified Body "
                    f"oversight/review but device is Class I non-sterile (no NB involvement). "
                    f"Found: '{matches[0]}'"
                )

        return errors

    def _check_single_use_consistency(self, psur: Dict[str, Any],
                                       device_context: Dict[str, Any] = None) -> List[str]:
        """Check that single-use/reusable terminology matches device_context."""
        errors = []
        if not device_context:
            return errors

        single_use_raw = (device_context.get("single_use_or_reusable") or "").lower()
        is_single_use = single_use_raw not in ("reusable", "multi-use", "multi use")

        if not is_single_use:
            return errors  # Only check for single-use devices described as reusable

        sections = psur.get("sections", {})
        check_sections = [
            ("B", "B_scope_and_device_description"),
            ("F", "F_product_complaint_types_counts_and_rates"),
            ("M", "M_findings_and_conclusions"),
        ]

        contradiction_re = re.compile(
            r"\b(?:non[- ]single[- ]use|reusable|multi[- ]use)\s+devices?\b",
            re.IGNORECASE,
        )

        for label, key in check_sections:
            sec_data = sections.get(key, {})
            text = json.dumps(sec_data)
            if contradiction_re.search(text):
                errors.append(
                    f"SINGLE_USE_CONSISTENCY: Section {label} describes device as "
                    f"'non-single-use' or 'reusable' but device_context says "
                    f"'{single_use_raw or 'single-use'}'. Terminology must match."
                )

        return errors

    def _check_complaint_total_consistency(self, psur: Dict[str, Any]) -> List[str]:
        """Check that complaint category counts sum to total across E, F, and M."""
        errors = []
        sections = psur.get("sections", {})
        stats = psur.get("_statistics", {})

        total_complaints = stats.get("total_complaints")
        if total_complaints is None:
            return errors

        # Check F's Table 7 grand total
        sec_f = sections.get("F_product_complaint_types_counts_and_rates", {})
        table7 = sec_f.get("table_7_complaint_rate_and_count", {})
        annual = table7.get("annual_format", {})
        rows = annual.get("rows", [])

        if rows:
            # Sum non-grand-total rows
            row_sum = sum(
                int(row.get("current_12_month_complaint_count", 0) or 0)
                for row in rows
                if str(row.get("harm", "")).lower() != "grand total"
                and str(row.get("medical_device_problem", "")).lower() != ""
            )
            # Find the grand total row
            grand_total_row = next(
                (r for r in rows if str(r.get("harm", "")).lower() == "grand total"),
                None
            )
            if grand_total_row:
                gt_count = int(grand_total_row.get("current_12_month_complaint_count", 0) or 0)
                if gt_count != total_complaints:
                    errors.append(
                        f"COMPLAINT_TOTAL: Section F Table 7 grand total shows "
                        f"{gt_count} complaints but statistics says {total_complaints}. "
                        f"Totals must match."
                    )

            # Check that category rows sum to grand total or total_complaints
            if row_sum > 0 and grand_total_row:
                gt_count = int(grand_total_row.get("current_12_month_complaint_count", 0) or 0)
                if row_sum != gt_count and row_sum != total_complaints:
                    errors.append(
                        f"COMPLAINT_TOTAL: Section F Table 7 category rows sum to "
                        f"{row_sum} but grand total is {gt_count} and statistics says "
                        f"{total_complaints}. Category counts must sum to the total."
                    )

        return errors

    def _check_sales_narrative_vs_table(self, psur: Dict[str, Any]) -> List[str]:
        """Check that Section C narrative country/region mentions match Table 1 data."""
        errors = []
        sections = psur.get("sections", {})
        sec_c = sections.get("C_volume_of_sales_and_population_exposure", {})
        if not sec_c:
            return errors

        # Get Table 1 regions
        table1 = sec_c.get("table_1_volume_of_sales_and_population_exposure", {})
        table_regions = set()
        for fmt_key in ("annual_format", "every_two_years_format"):
            fmt = table1.get(fmt_key)
            if isinstance(fmt, dict):
                for row in fmt.get("rows", []):
                    region = str(row.get("region", "")).strip()
                    if region and region.lower() not in ("worldwide", "total", "grand total"):
                        table_regions.add(region.lower())

        if not table_regions:
            return errors

        # Get narrative text
        narrative = sec_c.get("narrative_analysis", "")
        if not isinstance(narrative, str) or len(narrative) < 50:
            return errors

        # Look for country names with specific unit counts in narrative
        # Pattern: "CountryName (N,NNN; X.X%)" or "CountryName … N,NNN units"
        country_mentions = re.findall(
            r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s*\(\s*(\d[\d,]*)\s*;\s*\d+\.?\d*\s*%\s*\)",
            narrative
        )

        for country, count_str in country_mentions:
            country_lower = country.lower()
            # Check if mentioned country is in the table
            found_in_table = any(
                country_lower in region for region in table_regions
            )
            if not found_in_table and int(count_str.replace(",", "")) > 50:
                errors.append(
                    f"SALES_CONSISTENCY: Section C narrative mentions '{country}' "
                    f"({count_str} units) but this country is not in Table 1. "
                    f"Narrative countries must match table data."
                )

        return errors

    def _check_serious_incident_d_vs_f_harm(self, psur: Dict[str, Any]) -> List[str]:
        """Stricter check: D's exact serious incident count vs F's harm row counts."""
        errors = []
        sections = psur.get("sections", {})
        stats = psur.get("_statistics", {})

        si_count = stats.get("serious_incident_count", 0)

        sec_f = sections.get("F_product_complaint_types_counts_and_rates", {})
        table7 = sec_f.get("table_7_complaint_rate_and_count", {})
        annual = table7.get("annual_format", {})
        rows = annual.get("rows", [])

        # Count F rows with patient-injury harm. SKILL_PSUR_GENERATION uses
        # specific leaf-node harm terms (e.g. "Skin/Subcutaneous Injury
        # (Laceration)", "Tissue Reaction (Staple Migration/Extrusion)") in
        # addition to the legacy "Serious Injury" / "serious injury" labels.
        injury_keywords = (
            "serious injury",
            "skin/subcutaneous injury",
            "tissue reaction",
            "laceration",
            "death",
            "serious deterioration",
        )
        f_serious_count = 0
        for row in rows:
            harm = str(row.get("harm", "")).lower()
            if harm == "grand total":
                continue
            if any(kw in harm for kw in injury_keywords):
                f_serious_count += int(row.get("current_12_month_complaint_count", 0) or 0)

        if si_count == 0 and f_serious_count > 0:
            errors.append(
                f"SERIOUS_INCIDENT_HARM: Statistics reports 0 serious incidents but "
                f"Section F Table 7 has {f_serious_count} complaints classified as "
                f"'Serious Injury'. The harm classification must be consistent with D."
            )
        elif si_count > 0 and f_serious_count == 0:
            errors.append(
                f"SERIOUS_INCIDENT_HARM: Statistics reports {si_count} serious "
                f"incidents but Section F Table 7 shows no 'Serious Injury' harm rows. "
                f"Serious incidents must appear in F's harm taxonomy."
            )

        return errors
