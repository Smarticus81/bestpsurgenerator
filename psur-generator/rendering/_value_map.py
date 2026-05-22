"""ValueMapMixin — builds the placeholder→value dictionary for DOCX rendering."""
from typing import Any, Dict

from imdrf_coder import strip_imdrf_code
from rendering._helpers import deep_get, cb, stringify, CHECK_YES, CHECK_NO


class ValueMapMixin:
    """Provides ``_build_value_map`` and ``_fill_table7_placeholders``."""

    # ------------------------------------------------------------------
    # _build_value_map
    # ------------------------------------------------------------------
    def _build_value_map(self, psur: Dict[str, Any]) -> Dict[str, str]:
        v: Dict[str, str] = {}
        form = psur.get("form", {})
        cover = psur.get("psur_cover_page", {})
        sections = psur.get("sections", {})
        stats = psur.get("_statistics", {})

        # ── COVER PAGE ───────────────────────────────────────────────
        doc_ctrl = form.get("document_control", {})

        # Manufacturer Information
        mfr = cover.get("manufacturer_information", {})
        v["MFR_COMPANY_NAME"] = stringify(mfr.get("company_name", ""))
        addr_lines = mfr.get("address_lines", [])
        v["MFR_ADDRESS_LINE_1"] = addr_lines[0] if len(addr_lines) > 0 else ""
        v["MFR_ADDRESS_LINE_2"] = addr_lines[1] if len(addr_lines) > 1 else ""
        v["MFR_ADDRESS_COUNTRY"] = addr_lines[2] if len(addr_lines) > 2 else ""
        v["MFR_SRN"] = stringify(mfr.get("manufacturer_srn", ""))

        ar = mfr.get("authorized_representative", {})
        v["AR_NAME"] = stringify(ar.get("name", ""))
        ar_lines = ar.get("address_lines", [])
        v["AR_ADDRESS_LINE_1"] = ar_lines[0] if len(ar_lines) > 0 else ""
        v["AR_ADDRESS_LINE_2"] = ar_lines[1] if len(ar_lines) > 1 else ""
        v["AR_ADDRESS_COUNTRY"] = ar_lines[2] if len(ar_lines) > 2 else ""
        v["AR_SRN"] = stringify(ar.get("authorized_representative_srn", ""))

        # Regulatory Information
        reg = cover.get("regulatory_information", {})
        v["REG_CERT_NUMBER"] = stringify(reg.get("certificate_number", ""))
        v["REG_DATE_OF_ISSUE"] = stringify(reg.get("date_of_issue", ""))
        nb = reg.get("notified_body", {})
        v["REG_NB_NAME"] = stringify(nb.get("name", ""))
        v["REG_NB_NUMBER"] = stringify(nb.get("number", ""))

        psur_avail = reg.get("psur_available_within_3_working_days", True)
        v["CB_PSUR_AVAIL_YES"] = cb(psur_avail)
        v["CB_PSUR_AVAIL_NO"] = cb(not psur_avail)

        # Document Information
        doc_info = cover.get("document_information", {})
        period = doc_info.get("data_collection_period", {})
        v["DOC_PERIOD_START"] = stringify(period.get("start_date", ""))
        v["DOC_PERIOD_END"] = stringify(period.get("end_date", ""))
        v["DOC_PSUR_CADENCE"] = stringify(doc_info.get("psur_cadence", ""))

        # ── SECTION A: Executive Summary ─────────────────────────────
        sec_a = sections.get("A_executive_summary", {})

        # Previous PSUR Actions Status
        prev = sec_a.get("previous_psur_actions_status", {})
        v["A_PREV_ACTIONS_NARRATIVE"] = stringify(prev.get("actions_and_status_from_previous_report", ""))

        status_obj = prev.get("status_of_previous_actions", {})
        status_val = status_obj.get("status", "") if isinstance(status_obj, dict) else stringify(status_obj)
        v["CB_A_STATUS_COMPLETED"] = cb(status_val == "COMPLETED")
        v["CB_A_STATUS_IN_PROGRESS"] = cb(status_val == "IN_PROGRESS")
        v["CB_A_STATUS_NOT_STARTED"] = cb(status_val == "NOT_STARTED")
        v["CB_A_STATUS_NOT_APPLICABLE"] = cb(status_val == "NOT_APPLICABLE")
        v["A_PREV_STATUS_DETAILS"] = stringify(
            status_obj.get("details_if_needed", "") if isinstance(status_obj, dict) else "")

        # Notified Body Review Status
        nb_rev = sec_a.get("notified_body_review_status", {})
        nb_reviewed = stringify(nb_rev.get("previous_psur_reviewed_by_notified_body", ""))
        v["CB_A_NB_YES"] = cb(nb_reviewed == "YES")
        v["CB_A_NB_NO"] = cb(nb_reviewed == "NO")
        v["CB_A_NB_NA"] = cb(nb_reviewed in ("N/A", "N_A", "NA"))
        v["A_NB_ACTIONS"] = stringify(nb_rev.get("notified_body_actions_taken", ""))
        v["A_NB_ACTIONS_STATUS"] = stringify(nb_rev.get("status_of_nb_actions", ""))

        # Data Collection Period Changes
        dcp_a = sec_a.get("data_collection_period_changes", {})
        changed = stringify(dcp_a.get("data_collection_period_changed", ""))
        v["CB_A_PERIOD_YES"] = cb(changed == "YES")
        v["CB_A_PERIOD_NO"] = cb(changed == "NO")
        v["A_PERIOD_JUSTIFICATION"] = stringify(dcp_a.get("justification_for_change", ""))
        v["A_PERIOD_COMPARABILITY"] = stringify(dcp_a.get("impact_on_comparability", ""))

        # Benefit-Risk Assessment Conclusion
        brc_a = sec_a.get("benefit_risk_assessment_conclusion", {})
        conc_a = stringify(brc_a.get("conclusion", "")) if isinstance(brc_a, dict) else ""
        v["CB_A_BRC_UNCHANGED"] = cb(conc_a == "NOT_ADVERSELY_IMPACTED_UNCHANGED")
        v["CB_A_BRC_IMPACTED"] = cb(conc_a == "ADVERSELY_IMPACTED")
        v["A_BRC_SUMMARY"] = stringify(brc_a.get("high_level_summary_if_adversely_impacted", "")) if isinstance(brc_a, dict) else ""

        # ── Synthesized Executive Summary Narrative ──────────────────
        device_name = stringify(doc_ctrl.get("product_or_product_family", ""))
        period_start = v.get("DOC_PERIOD_START", "")
        period_end = v.get("DOC_PERIOD_END", "")
        total_units = stats.get("total_units_sold", stats.get("total_units", "N/A"))
        total_complaints = stats.get("total_complaints", "N/A")
        complaint_rate = stats.get("overall_complaint_rate", "N/A")
        serious_count = stats.get("serious_incident_count", stats.get("serious_incidents", 0))
        trend_status = stats.get("trend_status", "stable")

        # Format the complaint rate as a percentage (P4 fix)
        # Use overall_complaint_percentage (rate * 100) for human-readable display
        complaint_pct = stats.get("overall_complaint_percentage", None)
        if complaint_pct is not None and isinstance(complaint_pct, (int, float)):
            cr_str = f"{complaint_pct:.4f}%"
        elif isinstance(complaint_rate, (int, float)):
            cr_str = f"{complaint_rate * 100:.4f}%"
        else:
            cr_str = stringify(complaint_rate)

        # Format units with comma separator
        if isinstance(total_units, (int, float)):
            units_str = f"{int(total_units):,}"
        else:
            units_str = stringify(total_units)

        # Format complaints with comma separator
        if isinstance(total_complaints, (int, float)):
            complaints_str = f"{int(total_complaints):,}"
        else:
            complaints_str = stringify(total_complaints)

        # Previous actions summary
        prev_status = status_val if status_val else "NOT_APPLICABLE"
        prev_status_readable = prev_status.replace("_", " ").title()

        # Build the executive summary narrative
        exec_parts = []
        exec_parts.append(
            f"This Periodic Safety Update Report covers the data collection period "
            f"from {period_start} to {period_end} for {device_name}."
        )
        exec_parts.append(
            f"During this reporting period, {units_str} units were distributed and "
            f"{complaints_str} customer complaints were received, representing an "
            f"overall complaint rate of {cr_str}."
        )
        # Distinguish EU/UK serious incidents (Art. 2(65)) from FDA MDRs
        eu_si_count = stats.get("eu_uk_serious_incident_count", None)
        fda_mdr_count = stats.get("fda_mdr_count", None)

        if eu_si_count is not None and isinstance(eu_si_count, (int, float)):
            if int(eu_si_count) > 0:
                exec_parts.append(
                    f"{int(eu_si_count)} EU/UK serious incident(s) meeting "
                    f"EU MDR Article 2(65) criteria were reported during this period."
                )
            else:
                exec_parts.append(
                    "No EU/UK serious incidents (EU MDR Article 2(65)) were "
                    "identified during this period."
                )
            if fda_mdr_count is not None and isinstance(fda_mdr_count, (int, float)) and int(fda_mdr_count) > 0:
                exec_parts.append(
                    f"{int(fda_mdr_count)} U.S. FDA MDR report(s) were submitted "
                    f"(discussed in Section D and Section F)."
                )
        elif isinstance(serious_count, (int, float)) and serious_count > 0:
            exec_parts.append(
                f"{int(serious_count)} serious incident(s) were reported during this period."
            )
        else:
            exec_parts.append(
                "No serious incidents were reported during this period."
            )
        exec_parts.append(
            f"The complaint trend status is {trend_status.lower().replace('_', ' ')}."
        )

        # Previous actions context
        prev_narrative = v.get("A_PREV_ACTIONS_NARRATIVE", "")
        if prev_narrative:
            exec_parts.append(
                f"Status of previous PSUR actions: {prev_status_readable}."
            )

        v["A_EXECUTIVE_SUMMARY_NARRATIVE"] = " ".join(exec_parts)

        # ── Benefit-Risk Conclusion Statement ────────────────────────
        if conc_a == "NOT_ADVERSELY_IMPACTED_UNCHANGED":
            v["A_BRC_CONCLUSION_STATEMENT"] = (
                f"Based on the review of all available post-market surveillance data "
                f"during this reporting period, the overall benefit-risk profile of "
                f"{device_name} remains favorable and is not adversely impacted. "
                f"The benefits of the device continue to outweigh the residual risks "
                f"when the device is used in accordance with its intended purpose."
            )
        elif conc_a == "ADVERSELY_IMPACTED":
            summary = stringify(brc_a.get("high_level_summary_if_adversely_impacted", "")) if isinstance(brc_a, dict) else ""
            v["A_BRC_CONCLUSION_STATEMENT"] = (
                f"Based on the review of all available post-market surveillance data "
                f"during this reporting period, the overall benefit-risk profile of "
                f"{device_name} has been adversely impacted. {summary}"
            )
        else:
            v["A_BRC_CONCLUSION_STATEMENT"] = ""

        # ── SECTION B: Scope and Device Description ──────────────────
        sec_b = sections.get("B_scope_and_device_description", {})

        def bget(path, default=""):
            return deep_get(sec_b, path, default)

        # Device Information
        v["B_PRODUCT_NAME"] = stringify(bget("device_information.product_name"))
        implant = stringify(bget("device_information.implantable_device", "NO"))
        v["CB_B_IMPLANT_YES"] = cb(implant == "YES")
        v["CB_B_IMPLANT_NO"] = cb(implant != "YES")

        # Device Classification - EU
        eu_class = stringify(bget("device_classification.eu_mdr_classification"))
        v["CB_B_EU_IIA"] = cb(eu_class == "CLASS_IIA")
        v["CB_B_EU_IIB"] = cb(eu_class == "CLASS_IIB")
        v["CB_B_EU_III"] = cb(eu_class == "CLASS_III")
        v["B_EU_TD_NUMBER"] = stringify(bget("device_classification.eu_technical_documentation_number"))
        v["B_CLASSIFICATION_RULE"] = stringify(bget("device_classification.classification_rule_mdr_annex_viii"))

        # Device Classification - UK
        uk_info = bget("device_classification.uk_classification", {})
        uk_class = ""
        if isinstance(uk_info, dict):
            uk_class = stringify(uk_info.get("uk_classification_value", ""))
            v["B_UK_CLASS_TEXT"] = stringify(uk_info.get("description", ""))
        else:
            v["B_UK_CLASS_TEXT"] = stringify(uk_info)
        v["CB_B_UK_IIA"] = cb(uk_class == "CLASS_IIA")
        v["CB_B_UK_IIB"] = cb(uk_class == "CLASS_IIB")
        v["CB_B_UK_III"] = cb(uk_class == "CLASS_III")
        v["B_UK_CONFORMITY"] = stringify(bget("device_classification.uk_conformity_assessment_details"))
        v["B_UK_CLASS_RULE"] = stringify(bget("device_classification.uk_classification_rule"))

        # Device Classification - FDA
        fda_class = stringify(bget("device_classification.us_fda_classification"))
        v["CB_B_FDA_I"] = cb(fda_class == "CLASS_I")
        v["CB_B_FDA_II"] = cb(fda_class == "CLASS_II")
        v["CB_B_FDA_III"] = cb(fda_class == "CLASS_III")
        v["B_FDA_SUBMISSION"] = stringify(bget("device_classification.us_pre_market_submission_number"))

        # Device Timeline & Certification Milestones
        milestones = bget("device_timeline_and_status.certification_milestones", {})
        eu_ms = milestones.get("eu", {}) if isinstance(milestones, dict) else {}
        v["B_EU_DOC_DATE"] = stringify(eu_ms.get("first_declaration_of_conformity_date", ""))
        v["B_EU_CERT_DATE"] = stringify(eu_ms.get("first_ec_eu_certificate_date", ""))
        v["B_EU_CE_DATE"] = stringify(eu_ms.get("first_ce_marking_date", ""))

        uk_ms = milestones.get("uk", {}) if isinstance(milestones, dict) else {}
        if isinstance(uk_ms, dict):
            v["B_UK_CERT_DATE"] = stringify(uk_ms.get("first_cert_or_doc_date", uk_ms.get("first_date_of_certification_or_declaration_of_conformity_for_the_gb_market", "")))
            v["B_UK_CE_DATE"] = stringify(uk_ms.get("first_ce_marking_date", ""))
            v["B_UK_MARKET_DATE"] = stringify(uk_ms.get("first_market_placement", ""))
            v["B_UK_SERVICE_DATE"] = stringify(uk_ms.get("first_service_deployment", ""))
        else:
            v["B_UK_CERT_DATE"] = v["B_UK_CE_DATE"] = v["B_UK_MARKET_DATE"] = v["B_UK_SERVICE_DATE"] = ""

        # PSUR Obligation Status Assessment
        posa = bget("device_timeline_and_status.psur_obligation_status_assessment", {})
        if isinstance(posa, dict):
            v["B_MARKET_STATUS"] = stringify(posa.get("market_status", ""))
            v["B_LAST_SOLD_DATE"] = stringify(posa.get("last_device_sold_date_or_na", posa.get("last_device_sold_date", "")))
            v["B_CERT_STATUS"] = stringify(posa.get("certificate_status", ""))
            v["B_PMS_END_DATE"] = stringify(posa.get("projected_end_of_pms_period", ""))
            v["B_PSUR_OBLIGATION_STATEMENT"] = stringify(posa.get("confirmation_of_ongoing_psur_obligation", ""))
        else:
            v["B_MARKET_STATUS"] = v["B_LAST_SOLD_DATE"] = v["B_CERT_STATUS"] = ""
            v["B_PMS_END_DATE"] = v["B_PSUR_OBLIGATION_STATEMENT"] = ""

        # Device Description
        desc = bget("device_description_and_information", {})
        if isinstance(desc, dict):
            v["B_DEVICE_DESCRIPTION"] = stringify(desc.get("device_description", ""))
            v["B_INTENDED_USE"] = stringify(desc.get("intended_purpose_use", ""))
            v["B_INDICATIONS"] = stringify(desc.get("indications", ""))
            v["B_CONTRAINDICATIONS"] = stringify(desc.get("contraindications", ""))
            v["B_TARGET_POPULATIONS"] = stringify(desc.get("target_populations", ""))
        else:
            v["B_DEVICE_DESCRIPTION"] = stringify(desc)
            v["B_INTENDED_USE"] = v["B_INDICATIONS"] = v["B_CONTRAINDICATIONS"] = v["B_TARGET_POPULATIONS"] = ""

        # MDR Device Table (inline in template - Table 1 in doc)
        mdr_rows = bget("device_information_breakdown.mdr_devices.basic_udi_di_rows", [])
        if isinstance(mdr_rows, list) and mdr_rows:
            r0 = mdr_rows[0]
            v["MDR_UDI_DI"] = stringify(r0.get("basic_udi_di", ""))
            v["MDR_TRADE_NAME"] = stringify(r0.get("device_trade_name", ""))
            v["MDR_EMDN"] = stringify(r0.get("emdn_code", ""))
            v["MDR_CHANGES"] = stringify(r0.get("changes_from_previous_psur", ""))
        else:
            v["MDR_UDI_DI"] = v["MDR_TRADE_NAME"] = v["MDR_EMDN"] = v["MDR_CHANGES"] = ""

        # Legacy Device Table
        leg = bget("device_information_breakdown.legacy_devices", {})
        if isinstance(leg, dict) and leg.get("is_applicable"):
            rows = leg.get("device_group_rows", [])
            if rows:
                r0 = rows[0]
                v["LEG_GROUP"] = stringify(r0.get("device_group", ""))
                v["LEG_NAMES"] = stringify(r0.get("trade_names", ""))
                v["LEG_GMDN"] = stringify(r0.get("gmdn_code", ""))
                v["LEG_MARKETS"] = stringify(r0.get("market_availability", ""))
            else:
                v["LEG_GROUP"] = v["LEG_NAMES"] = v["LEG_GMDN"] = v["LEG_MARKETS"] = ""
        else:
            v["LEG_GROUP"] = v["LEG_NAMES"] = v["LEG_GMDN"] = v["LEG_MARKETS"] = "N/A"

        # Data Collection Period
        dcp_b = bget("data_collection_period_reporting_period_information", {})
        if isinstance(dcp_b, dict):
            dr = dcp_b.get("date_range", dcp_b)
            v["B_DATA_START"] = stringify(dr.get("start_date", "")) if isinstance(dr, dict) else ""
            v["B_DATA_END"] = stringify(dr.get("end_date", "")) if isinstance(dr, dict) else ""
            v["B_PMS_PERIOD_UK"] = stringify(dcp_b.get("pms_period_determination_uk_devices", {}).get("description", "")) if isinstance(dcp_b.get("pms_period_determination_uk_devices"), dict) else ""
            v["B_DEVICE_LIFETIME"] = stringify(dcp_b.get("device_lifetime", ""))
            v["B_PMS_END_PROJECTED"] = stringify(dcp_b.get("projected_end_of_pms_period", ""))
        else:
            v["B_DATA_START"] = v["B_DATA_END"] = v["B_PMS_PERIOD_UK"] = ""
            v["B_DEVICE_LIFETIME"] = v["B_PMS_END_PROJECTED"] = ""

        # Technical Information
        v["B_RMF_NUMBER"] = stringify(bget("technical_information.risk_management_file_number"))

        # Associated Documents (Template Table 3: 4 rows)
        assoc = bget("technical_information.associated_documents", [])
        if isinstance(assoc, list):
            type_map = {
                "PMS Plan": ("ASSOC_PMS_NUM", "ASSOC_PMS_TITLE"),
                "Clinical Evaluation Report": ("ASSOC_CER_NUM", "ASSOC_CER_TITLE"),
                "PMCF Plan": ("ASSOC_PMCF_NUM", "ASSOC_PMCF_TITLE"),
            }
            other_found = False
            for doc_item in assoc:
                if not isinstance(doc_item, dict):
                    continue
                dtype = doc_item.get("document_type", "")
                if dtype in type_map:
                    num_key, title_key = type_map[dtype]
                    v[num_key] = stringify(doc_item.get("document_number", ""))
                    v[title_key] = stringify(doc_item.get("document_title", ""))
                elif not other_found:
                    v["ASSOC_OTHER_TYPE"] = stringify(dtype)
                    v["ASSOC_OTHER_NUM"] = stringify(doc_item.get("document_number", ""))
                    v["ASSOC_OTHER_TITLE"] = stringify(doc_item.get("document_title", ""))
                    other_found = True
        # Fill defaults for any missing
        for prefix in ("ASSOC_PMS", "ASSOC_CER", "ASSOC_PMCF", "ASSOC_OTHER"):
            for suffix in ("NUM", "TITLE"):
                key = f"{prefix}_{suffix}"
                if key not in v:
                    v[key] = ""
        if "ASSOC_OTHER_TYPE" not in v:
            v["ASSOC_OTHER_TYPE"] = ""

        # Model/Catalog Numbers
        mcn = bget("model_catalog_numbers", "")
        if isinstance(mcn, dict):
            v["B_MODEL_CATALOG"] = stringify(mcn.get("complete_listing_reference", ""))
        elif isinstance(mcn, list):
            v["B_MODEL_CATALOG"] = ", ".join(str(m) for m in mcn)
        else:
            v["B_MODEL_CATALOG"] = stringify(mcn)

        # Device Grouping Information
        grp = bget("device_grouping_information", {})
        if isinstance(grp, dict):
            multi = stringify(grp.get("multiple_devices_included", "NO"))
            v["CB_B_MULTI_YES"] = cb(multi == "YES")
            v["CB_B_MULTI_NO"] = cb(multi != "YES")
            v["B_GROUP_JUSTIFICATION"] = stringify(grp.get("justification_for_grouping", ""))
            v["B_LEADING_DEVICE"] = stringify(grp.get("leading_device", ""))
            v["B_LEADING_RATIONALE"] = stringify(grp.get("leading_device_rationale", ""))

            same_cer = stringify(grp.get("same_clinical_evaluation_report", ""))
            v["CB_B_SAME_CER_YES"] = cb(same_cer == "YES")
            v["CB_B_SAME_CER_NO"] = cb(same_cer != "YES")

            same_nb = stringify(grp.get("same_notified_body_for_all_devices", ""))
            v["CB_B_SAME_NB_YES"] = cb(same_nb == "YES")
            v["CB_B_SAME_NB_NO"] = cb(same_nb != "YES")

            grp_change = stringify(grp.get("grouping_changes_from_previous_psur", ""))
            v["CB_B_GROUP_CHANGE_YES"] = cb(grp_change == "YES")
            v["CB_B_GROUP_CHANGE_NO"] = cb(grp_change != "YES")
        else:
            v["CB_B_MULTI_YES"] = CHECK_NO; v["CB_B_MULTI_NO"] = CHECK_YES
            v["B_GROUP_JUSTIFICATION"] = v["B_LEADING_DEVICE"] = v["B_LEADING_RATIONALE"] = ""
            v["CB_B_SAME_CER_YES"] = v["CB_B_SAME_CER_NO"] = CHECK_NO
            v["CB_B_SAME_NB_YES"] = v["CB_B_SAME_NB_NO"] = CHECK_NO
            v["CB_B_GROUP_CHANGE_YES"] = v["CB_B_GROUP_CHANGE_NO"] = CHECK_NO

        # ── SECTION C: Volume of Sales and Population Exposure ───────
        sec_c = sections.get("C_volume_of_sales_and_population_exposure", {})

        criteria = deep_get(sec_c, "sales_methodology.criteria_used_for_sales_data", {})
        if isinstance(criteria, dict):
            v["CB_C_PLACED_MARKET"] = cb(criteria.get("devices_placed_on_market_or_put_into_service"))
            v["CB_C_UNITS_DOC"] = cb(criteria.get("units_distributed_from_doc_or_ec_eu_mark_approval_to_end_date"))
            v["CB_C_UNITS_PERIOD"] = cb(criteria.get("units_distributed_within_each_time_period"))
            v["CB_C_EPISODES"] = cb(criteria.get("episodes_of_use_for_reusable_devices"))
            v["CB_C_INSTALLED"] = cb(criteria.get("active_installed_base"))
            v["CB_C_IMPLANTED"] = cb(criteria.get("units_implanted"))
            other = criteria.get("other", {})
            v["CB_C_OTHER"] = cb(other.get("selected") if isinstance(other, dict) else other)
            v["C_SALES_OTHER_SPECIFY"] = stringify(other.get("rationale", "")) if isinstance(other, dict) else ""
        else:
            for k in ("CB_C_PLACED_MARKET", "CB_C_UNITS_DOC", "CB_C_UNITS_PERIOD",
                       "CB_C_EPISODES", "CB_C_INSTALLED", "CB_C_IMPLANTED", "CB_C_OTHER"):
                v[k] = CHECK_NO
            v["C_SALES_OTHER_SPECIFY"] = ""

        v["C_MARKET_HISTORY"] = stringify(deep_get(sec_c, "sales_methodology.market_history", ""))

        # Table 1 date ranges
        t1 = sec_c.get("table_1_sales_by_region", {})
        t1_fmt = t1.get("annual_format", t1) if isinstance(t1, dict) else {}
        date_ranges = t1_fmt.get("date_ranges", []) if isinstance(t1_fmt, dict) else []

        # Prefer deterministic period labels from _statistics when available
        # (these come from the actual DB-derived 12-month windows). Fall back
        # to whatever the LLM emitted in date_ranges.
        stats = psur.get("_statistics", {}) or {}
        det_labels = stats.get("section_c_period_labels") or []
        if det_labels:
            # det_labels is ordered [P-1, P-2, P-3] (most recent first).
            # Template placeholders are P1=most recent, P2=mid, P3=oldest.
            date_ranges = list(det_labels) + [
                f"{stats.get('surveillance_period', {}).get('start_date', '')} → "
                f"{stats.get('surveillance_period', {}).get('end_date', '')}"
            ]

        v["T1_DATE_RANGE_P1"] = stringify(date_ranges[0]) if len(date_ranges) > 0 else ""
        v["T1_DATE_RANGE_P2"] = stringify(date_ranges[1]) if len(date_ranges) > 1 else ""
        v["T1_DATE_RANGE_P3"] = stringify(date_ranges[2]) if len(date_ranges) > 2 else ""
        v["T1_DATE_RANGE_CURRENT"] = stringify(date_ranges[3]) if len(date_ranges) > 3 else ""

        # Sales narrative
        analysis = sec_c.get("sales_data_analysis", {})
        v["C_SALES_NARRATIVE"] = stringify(analysis.get("narrative_analysis", "")) if isinstance(analysis, dict) else stringify(analysis)

        # Population
        pop = sec_c.get("size_and_characteristics_of_population_using_device", {})
        if isinstance(pop, dict):
            usage = pop.get("usage_frequency", {})
            if isinstance(usage, dict):
                single = stringify(usage.get("single_use_per_patient", ""))
                multi = stringify(usage.get("multiple_uses_per_patient", ""))
            else:
                single = multi = ""
            v["CB_C_SINGLE_YES"] = cb(single == "YES")
            v["CB_C_SINGLE_NO"] = cb(single != "YES")
            v["CB_C_MULTI_YES"] = cb(multi == "YES")
            v["CB_C_MULTI_NO"] = cb(multi != "YES")
            v["C_POPULATION_ESTIMATE"] = stringify(pop.get("estimated_size_of_patient_population_exposed", ""))
            v["C_POPULATION_CHARACTERISTICS"] = stringify(pop.get("characteristics_of_patient_population_exposed", ""))
        else:
            for k in ("CB_C_SINGLE_YES", "CB_C_SINGLE_NO", "CB_C_MULTI_YES", "CB_C_MULTI_NO"):
                v[k] = CHECK_NO
            v["C_POPULATION_ESTIMATE"] = v["C_POPULATION_CHARACTERISTICS"] = ""

        # ── SECTION D: Information on Serious Incidents ──────────────
        sec_d = sections.get("D_information_on_serious_incidents", {})
        v["D_NARRATIVE"] = stringify(sec_d.get("narrative_summary", ""))
        v["D_NEW_INCIDENT_TYPES"] = stringify(sec_d.get("new_incident_types_identified_this_cycle", ""))

        # ── SECTION E: Customer Feedback ─────────────────────────────
        sec_e = sections.get("E_customer_feedback", {})
        v["E_NARRATIVE"] = stringify(sec_e.get("summary", ""))

        # ── SECTION F: Product Complaint Types, Counts, and Rates ────
        sec_f = sections.get("F_product_complaint_types_counts_and_rates", {})
        v["F_COMPLAINT_RATE_METHOD"] = stringify(
            deep_get(sec_f, "complaint_rate_calculation.method_description_and_justification", ""))
        v["F_EXCEEDANCE_COMMENTARY"] = stringify(
            deep_get(sec_f, "annual_number_of_complaints_and_complaint_rate_by_harm_and_medical_device_problem.commentary_context_for_exceedances", ""))

        # Table 7 date range and data
        t7 = sec_f.get("table_7_complaint_rate_and_count", {})
        t7_fmt = t7.get("annual_format", t7) if isinstance(t7, dict) else {}
        v["T7_DATE_RANGE"] = stringify(t7_fmt.get("date_range", "")) if isinstance(t7_fmt, dict) else ""

        # Fill Table 7 harm/MDP placeholders from rows
        t7_rows = t7_fmt.get("rows", []) if isinstance(t7_fmt, dict) else []
        self._fill_table7_placeholders(v, t7_rows, t7_fmt)

        # ── SECTION G: Information from Trend Reporting ──────────────
        sec_g = sections.get("G_information_from_trend_reporting", {})
        trending = sec_g.get("overall_monthly_complaint_rate_trending", {})
        g_parts = []
        if isinstance(trending, dict):
            v["G_TREND_NARRATIVE"] = stringify(trending.get("graph_reference", ""))
            ucl = stringify(trending.get("upper_control_limit_definition", ""))
            breaches = stringify(trending.get("breaches_commentary_and_actions", ""))
            if ucl:
                g_parts.append(ucl)
            if breaches:
                g_parts.append(breaches)
            if g_parts:
                v["G_TREND_NARRATIVE"] = "\n\n".join(g_parts)
        else:
            v["G_TREND_NARRATIVE"] = ""

        trend_summ = sec_g.get("trend_reporting_summary", {})
        g_summ = stringify(
            trend_summ.get("statement_if_not_applicable", "")) if isinstance(trend_summ, dict) else stringify(trend_summ)
        v["G_TREND_REPORTS_SUMMARY"] = g_summ

        # Synthesized full Section G narrative
        g_full = []
        if v.get("G_TREND_NARRATIVE"):
            g_full.append(v["G_TREND_NARRATIVE"])
        if g_summ:
            g_full.append(g_summ)
        v["G_FULL_NARRATIVE"] = "\n\n".join(g_full) if g_full else ""

        # ── SECTION H: FSCA ──────────────────────────────────────────
        sec_h = sections.get("H_information_from_fsca", {})
        v["H_NARRATIVE"] = stringify(sec_h.get("summary_or_na_statement", ""))

        # ── SECTION I: CAPA ──────────────────────────────────────────
        sec_i = sections.get("I_corrective_and_preventive_actions", {})
        v["I_NARRATIVE"] = stringify(sec_i.get("summary_or_na_statement", ""))

        # ── SECTION J: Scientific Literature Review ──────────────────
        sec_j = sections.get("J_scientific_literature_review", {})
        j_parts = []
        for key in ("literature_search_methodology",
                     "summary_of_new_data_performance_or_safety",
                     "newly_observed_uses", "previously_unassessed_risks",
                     "state_of_the_art_changes", "comparison_with_similar_devices"):
            val = sec_j.get(key, "")
            if val:
                j_parts.append(stringify(val))
        v["J_FULL_NARRATIVE"] = "\n\n".join(j_parts) if j_parts else ""

        # ── SECTION K: Review of External Databases ──────────────────
        sec_k = sections.get("K_review_of_external_databases_and_registries", {})
        v["K_NARRATIVE"] = stringify(sec_k.get("registries_reviewed_summary", ""))

        # ── SECTION L: PMCF ──────────────────────────────────────────
        sec_l = sections.get("L_pmcf", {})
        v["L_NARRATIVE"] = stringify(sec_l.get("summary_or_na_statement", ""))

        # ── SECTION M: Findings and Conclusions ──────────────────────
        sec_m = sections.get("M_findings_and_conclusions", {})
        v["M_BENEFIT_RISK_CONCLUSION"] = stringify(sec_m.get("benefit_risk_profile_conclusion", ""))
        v["M_INTENDED_BENEFITS"] = stringify(sec_m.get("intended_benefits_achieved", ""))
        v["M_DATA_LIMITATIONS"] = stringify(sec_m.get("limitations_of_data_and_conclusion", ""))
        v["M_NEW_EMERGING_RISKS"] = stringify(sec_m.get("new_or_emerging_risks_or_new_benefits", ""))
        v["M_ACTIONS_TAKEN"] = stringify(sec_m.get("actions_taken_or_planned", {}).get("action_details_and_follow_up", "")) if isinstance(sec_m.get("actions_taken_or_planned"), dict) else ""
        v["M_OVERALL_CONCLUSION"] = stringify(sec_m.get("overall_performance_conclusion", ""))

        # Synthesized full Section M narrative — used if the template has a
        # single {{M_FULL_NARRATIVE}} placeholder instead of per-field ones.
        m_parts = []
        for m_key in ("benefit_risk_profile_conclusion",
                       "intended_benefits_achieved",
                       "overall_performance_conclusion",
                       "limitations_of_data_and_conclusion",
                       "new_or_emerging_risks_or_new_benefits"):
            m_val = sec_m.get(m_key, "")
            if m_val and isinstance(m_val, str) and len(m_val.strip()) > 10:
                m_parts.append(m_val.strip())
        action_details = (sec_m.get("actions_taken_or_planned", {}).get(
            "action_details_and_follow_up", "")
            if isinstance(sec_m.get("actions_taken_or_planned"), dict) else "")
        if action_details and len(action_details.strip()) > 10:
            m_parts.append(action_details.strip())
        v["M_FULL_NARRATIVE"] = "\n\n".join(m_parts) if m_parts else ""

        # Section M action checkboxes
        actions = sec_m.get("actions_taken_or_planned", {})
        if isinstance(actions, dict):
            action_map = {
                "BRA": ["benefit_risk_assessment_update", "update_benefit_risk_assessment"],
                "RMF": ["risk_management_file_update", "update_risk_management_file"],
                "DESIGN": ["product_design_update", "update_product_design"],
                "MFG": ["manufacturing_process_update", "update_manufacturing_process"],
                "IFU": ["ifu_or_labeling_update", "update_instructions_for_use_or_labeling"],
                "CER": ["clinical_evaluation_report_update", "update_clinical_evaluation_report"],
                "SSCP": ["sscp_update_if_applicable", "sscp_update"],
                "CAPA": ["capa_initiated", "initiate_corrective_and_preventive_action"],
                "FSCA": ["fsca_initiated", "initiate_field_safety_corrective_action"],
            }
            for tag, fields in action_map.items():
                val = None
                for field in fields:
                    val = actions.get(field)
                    if val is not None:
                        break
                is_yes = bool(val) if val is not None else False
                v[f"CB_M_{tag}_YES"] = cb(is_yes)
                v[f"CB_M_{tag}_NO"] = cb(not is_yes)
        else:
            for tag in ("BRA", "RMF", "DESIGN", "MFG", "IFU", "CER", "SSCP", "CAPA", "FSCA"):
                v[f"CB_M_{tag}_YES"] = CHECK_NO
                v[f"CB_M_{tag}_NO"] = CHECK_YES

        # ── HEADER placeholders ──────────────────────────────────────
        v["HDR_PRODUCT_NAME"] = stringify(doc_ctrl.get("product_or_product_family", ""))
        v["HDR_INFOCARD"] = stringify(doc_ctrl.get("infocard_number", ""))
        v["HDR_REVISION"] = stringify(form.get("revision", "C"))

        # Ensure all values are strings
        return {k: stringify(val) for k, val in v.items()}

    # ------------------------------------------------------------------
    # _fill_table7_placeholders
    # ------------------------------------------------------------------
    def _fill_table7_placeholders(self, v: Dict[str, str],
                                   rows: list, t7_fmt: dict):
        """Fill T7_HARM_A, T7_HARM_A_MDP_1, etc. from complaint rows."""
        harm_groups: Dict[str, list] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            harm = row.get("harm", "No Health Consequence or Impact")
            harm_groups.setdefault(harm, []).append(row)

        harm_keys = [k for k in harm_groups if k != "No Health Consequence or Impact"
                     and k != "No Harm"]
        noharm_keys = [k for k in harm_groups if k in ("No Health Consequence or Impact", "No Harm")]

        if harm_keys:
            v["T7_HARM_A"] = strip_imdrf_code(harm_keys[0])
            mdps = harm_groups[harm_keys[0]]
            v["T7_HARM_A_MDP_1"] = strip_imdrf_code(mdps[0].get("medical_device_problem", "")) if len(mdps) > 0 else ""
            v["T7_HARM_A_MDP_2"] = strip_imdrf_code(mdps[1].get("medical_device_problem", "")) if len(mdps) > 1 else ""
        else:
            v["T7_HARM_A"] = "N/A"
            v["T7_HARM_A_MDP_1"] = v["T7_HARM_A_MDP_2"] = ""

        if len(harm_keys) > 1:
            v["T7_HARM_B"] = strip_imdrf_code(harm_keys[1])
            mdps = harm_groups[harm_keys[1]]
            v["T7_HARM_B_MDP_1"] = strip_imdrf_code(mdps[0].get("medical_device_problem", "")) if len(mdps) > 0 else ""
            v["T7_HARM_B_MDP_2"] = strip_imdrf_code(mdps[1].get("medical_device_problem", "")) if len(mdps) > 1 else ""
        else:
            v["T7_HARM_B"] = "N/A"
            v["T7_HARM_B_MDP_1"] = v["T7_HARM_B_MDP_2"] = ""

        if noharm_keys:
            noharm_mdps = harm_groups[noharm_keys[0]]
            v["T7_NOHARM_MDP_1"] = strip_imdrf_code(noharm_mdps[0].get("medical_device_problem", "")) if len(noharm_mdps) > 0 else ""
            v["T7_NOHARM_MDP_2"] = strip_imdrf_code(noharm_mdps[1].get("medical_device_problem", "")) if len(noharm_mdps) > 1 else ""
        else:
            v["T7_NOHARM_MDP_1"] = v["T7_NOHARM_MDP_2"] = ""
