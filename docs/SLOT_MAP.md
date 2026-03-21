# FormQAR-054 Rev C — Slot Obligation Map

> Every field (slot) the LLM must populate, organized by section.
> **★** = required by schema · *italic* = optional · Indentation shows nesting depth.

---

## Shared Type Definitions

| Ref Name | Type | Enum Values | Default |
|----------|------|-------------|---------|
| `TriState` | string | `YES`, `NO`, `NOT_SELECTED` | `NOT_SELECTED` |
| `YesNoNA` | string | `YES`, `NO`, `N_A`, `NOT_SELECTED` | `NOT_SELECTED` |
| `MDRClass` | string | `CLASS_IIA`, `CLASS_IIB`, `CLASS_III`, `NOT_SELECTED` | `NOT_SELECTED` |
| `USFDAClass` | string | `CLASS_I`, `CLASS_II`, `CLASS_III`, `NOT_SELECTED` | `NOT_SELECTED` |

---

## Form Header

| Slot | Type | Constraint | Required |
|------|------|-----------|----------|
| `form.form_id` | string | const: `"FormQAR-054"` | ★ |
| `form.form_title` | string | const: `"Periodic Safety Update Report (PSUR)"` | ★ |
| `form.revision` | string | default: `"C"` | ★ |
| `form.document_control.product_or_product_family` | string | minLength: 1 | ★ |
| `form.document_control.infocard_number` | string | minLength: 1 | ★ |
| `form.document_control.page_control.current_page` | integer \| null | min: 1 | |
| `form.document_control.page_control.total_pages` | integer \| null | min: 1 | |

---

## Cover Page (`psur_cover_page`)

### manufacturer_information ★

| Slot | Type | Constraint | Required |
|------|------|-----------|----------|
| `company_name` | string | minLength: 1, default: `"CooperSurgical, Inc."` | ★ |
| `address_lines` | array[string] | minItems: 1 | ★ |
| `manufacturer_srn` | string | pattern: `^[A-Z]{2}-MF-\d{10,}$`, default: `"US-MF-000002607"` | ★ |
| `authorized_representative.is_applicable` | boolean | default: true | ★ |
| `authorized_representative.name` | string | minLength: 1, default: `"CooperSurgical Distribution B.V."` | ★ (if is_applicable) |
| `authorized_representative.address_lines` | array[string] | minItems: 1 | ★ (if is_applicable) |
| `authorized_representative.authorized_representative_srn` | string | pattern: `^[A-Z]{2}-AR-\d{10,}$` | ★ (if is_applicable) |

### regulatory_information ★

| Slot | Type | Constraint | Required |
|------|------|-----------|----------|
| `certificate_number` | string | minLength: 1 | ★ |
| `date_of_issue` | string | format: date | ★ |
| `notified_body.name` | string | minLength: 1, default: `"BSI Group The Netherlands B.V."` | ★ |
| `notified_body.number` | string | pattern: `^\d{4}$`, default: `"2797"` | ★ |
| `psur_available_within_3_working_days` | boolean | default: true | ★ |

### document_information ★

| Slot | Type | Constraint | Required |
|------|------|-----------|----------|
| `data_collection_period.start_date` | string | format: date | ★ |
| `data_collection_period.end_date` | string | format: date | ★ |
| `psur_cadence` | string | enum: `ANNUALLY`, `EVERY_TWO_YEARS` | ★ |

---

## Section A: Executive Summary

`A_executive_summary` — 4 required subsections

### A.1 previous_psur_actions_status ★

| Slot | Type | Constraint | Required |
|------|------|-----------|----------|
| `actions_and_status_from_previous_report` | string | textarea | ★ |
| `status_of_previous_actions.status` | string | enum: `COMPLETED`, `IN_PROGRESS`, `NOT_STARTED`, `NOT_APPLICABLE`, `NOT_SELECTED` | ★ |
| `status_of_previous_actions.details_if_needed` | string | textarea | |

### A.2 notified_body_review_status ★

| Slot | Type | Constraint | Required |
|------|------|-----------|----------|
| `previous_psur_reviewed_by_notified_body` | YesNoNA | select | ★ |
| `notified_body_actions_taken` | string | textarea | |
| `status_of_nb_actions` | string | textarea | |

### A.3 data_collection_period_changes ★

| Slot | Type | Constraint | Required |
|------|------|-----------|----------|
| `data_collection_period_changed` | TriState | select | ★ |
| `justification_for_change` | string | textarea | ★ (if changed = YES) |
| `impact_on_comparability` | string | textarea | ★ (if changed = YES) |

### A.4 benefit_risk_assessment_conclusion ★

| Slot | Type | Constraint | Required |
|------|------|-----------|----------|
| `conclusion` | string | enum: `NOT_ADVERSELY_IMPACTED_UNCHANGED`, `ADVERSELY_IMPACTED`, `NOT_SELECTED` | ★ |
| `high_level_summary_if_adversely_impacted` | string | textarea | ★ (if ADVERSELY_IMPACTED) |

---

## Section B: Scope and Device Description

`B_scope_and_device_description` — 9 required subsections

### B.1 device_information ★

| Slot | Type | Constraint | Required |
|------|------|-----------|----------|
| `product_name` | string | minLength: 1 | ★ |
| `implantable_device` | TriState | select | ★ |

### B.2 device_classification ★

| Slot | Type | Constraint | Required |
|------|------|-----------|----------|
| `eu_mdr_classification` | MDRClass | select | ★ |
| `eu_technical_documentation_number` | string | minLength: 1 | ★ |
| `classification_rule_mdr_annex_viii` | string | minLength: 1 | ★ |
| `uk_classification.is_applicable` | boolean | default: false | ★ |
| `uk_classification.uk_classification_value` | MDRClass | select | ★ |
| `uk_classification.uk_conformity_assessment_details` | string | textarea | ★ (if is_applicable) |
| `uk_classification.uk_classification_rule` | string | | ★ (if is_applicable) |
| `us_fda_classification` | USFDAClass | select | ★ |
| `us_pre_market_submission_number` | string | minLength: 1 | ★ |

### B.3 device_timeline_and_status ★

#### B.3.1 certification_milestones ★

**EU:**

| Slot | Type | Constraint | Required |
|------|------|-----------|----------|
| `eu.first_declaration_of_conformity_date` | string | format: date | |
| `eu.first_ec_eu_certificate_date` | string | format: date | |
| `eu.first_ce_marking_date` | string | format: date | |

**UK:**

| Slot | Type | Constraint | Required |
|------|------|-----------|----------|
| `uk.is_applicable` | boolean | default: false | ★ |
| `uk.first_date_of_certification_or_doc_for_gb_market` | string | format: date | ★ (if is_applicable) |
| `uk.first_ce_marking_date` | string | format: date | |
| `uk.first_market_placement_date` | string | format: date | ★ (if is_applicable) |
| `uk.first_service_deployment_date` | string | format: date | |

#### B.3.2 psur_obligation_status_assessment ★

| Slot | Type | Constraint | Required |
|------|------|-----------|----------|
| `market_status` | string | minLength: 1, textarea | ★ |
| `last_device_sold_date_or_na` | string | date or "N/A" | |
| `certificate_status` | string | minLength: 1, textarea | ★ |
| `projected_end_of_pms_period` | string | | |
| `confirmation_of_ongoing_psur_obligation` | string | textarea | |

### B.4 device_description_and_information ★

| Slot | Type | Constraint | Required |
|------|------|-----------|----------|
| `device_description` | string | minLength: 1, textarea | ★ |
| `intended_purpose_use` | string | minLength: 1, textarea | ★ |
| `indications` | string | textarea | |
| `contraindications` | string | textarea | |
| `target_populations` | string | textarea | |

### B.5 device_information_breakdown ★

#### B.5.1 mdr_devices ★

`basic_udi_di_rows` — array, minItems: 1 ★

| Row Slot | Type | Constraint | Required |
|----------|------|-----------|----------|
| `basic_udi_di` | string | minLength: 1 | ★ |
| `device_trade_name` | string | minLength: 1 | ★ |
| `emdn_code` | string | minLength: 1 | ★ |
| `changes_from_previous_psur` | string | textarea | |

#### B.5.2 legacy_devices ★

| Slot | Type | Constraint | Required |
|------|------|-----------|----------|
| `is_applicable` | boolean | default: false | ★ |

`device_group_family_rows` — array, minItems: 0 (conditional)

| Row Slot | Type | Constraint | Required |
|----------|------|-----------|----------|
| `device_group` | string | | ★ |
| `trade_names` | string | textarea | ★ |
| `gmdn_code` | string | | ★ |
| `market_availability_member_states` | string | textarea | ★ |

### B.6 data_collection_period_reporting_period_information ★

#### B.6.1 date_range ★

| Slot | Type | Constraint | Required |
|------|------|-----------|----------|
| `start_date` | string | format: date | ★ |
| `end_date` | string | format: date | ★ |

#### B.6.2 pms_period_determination_uk_devices

| Slot | Type | Constraint | Required |
|------|------|-----------|----------|
| `is_applicable` | boolean | default: false | ★ |
| `pms_period_determination_text` | string | textarea | |
| `device_lifetime_text` | string | textarea | |
| `projected_end_of_pms_period_text` | string | textarea | |

### B.7 technical_information ★

| Slot | Type | Constraint | Required |
|------|------|-----------|----------|
| `risk_management_file_number` | string | minLength: 1 | ★ |

`associated_documents` — array, minItems: 1 ★

| Row Slot | Type | Constraint | Required |
|----------|------|-----------|----------|
| `document_type` | string | enum: `PMS Plan`, `Clinical Evaluation Report`, `PMCF Plan`, `Other` | ★ |
| `document_number` | string | minLength: 1 | ★ |
| `document_title` | string | minLength: 1 | ★ |

### B.8 model_catalog_numbers ★

| Slot | Type | Constraint | Required |
|------|------|-----------|----------|
| `complete_listing_reference` | string | minLength: 1 | ★ |

### B.9 device_grouping_information ★

| Slot | Type | Constraint | Required |
|------|------|-----------|----------|
| `is_applicable` | boolean | default: false | ★ |
| `multiple_devices_included` | TriState | select | ★ |
| `justification_for_grouping` | string | textarea | |
| `leading_device` | string | | |
| `leading_device_rationale` | string | textarea | |
| `same_clinical_evaluation_report` | TriState | select | |
| `same_notified_body_for_all_devices` | TriState | select | |
| `grouping_changes_from_previous_psur` | TriState | select | |

---

## Section C: Volume of Sales and Population Exposure

`C_volume_of_sales_and_population_exposure` — 4 required subsections

### C.1 sales_methodology ★

| Slot | Type | Constraint | Required |
|------|------|-----------|----------|
| `criteria_used_for_sales_data.devices_placed_on_market_or_put_into_service` | boolean | default: false | |
| `criteria_used_for_sales_data.units_distributed_from_doc_or_ec_eu_mark_approval_to_end_date` | boolean | default: false | |
| `criteria_used_for_sales_data.units_distributed_within_each_time_period` | boolean | default: false | |
| `criteria_used_for_sales_data.episodes_of_use_for_reusable_devices` | boolean | default: false | |
| `criteria_used_for_sales_data.active_installed_base` | boolean | default: false | |
| `criteria_used_for_sales_data.units_implanted` | boolean | default: false | |
| `criteria_used_for_sales_data.other.selected` | boolean | default: false | ★ |
| `criteria_used_for_sales_data.other.rationale` | string | textarea | ★ |
| `market_history` | string | textarea | ★ |

### C.2 table_1_sales_by_region ★

| Slot | Type | Constraint | Required |
|------|------|-----------|----------|
| `use_if_psur_frequency` | string | enum: `ANNUALLY`, `EVERY_TWO_YEARS` | ★ |

**If ANNUALLY → `annual_format`** ★ (conditional)

| Slot | Type | Constraint | Required |
|------|------|-----------|----------|
| `date_ranges` | array[string] | minItems: 4, maxItems: 4 | |
| `rows[]` — each row: | | | |
| `.region` | string | | ★ |
| `.preceding_12_month_periods` | array[number\|null] | minItems: 3, maxItems: 3 | ★ |
| `.current_data_collection_period` | number \| null | | ★ |
| `.percent_of_global_sales` | number \| null | min: 0, max: 100 | |

**If EVERY_TWO_YEARS → `every_two_years_format`** ★ (conditional)

| Slot | Type | Constraint | Required |
|------|------|-----------|----------|
| `date_ranges` | array[string] | minItems: 4, maxItems: 4 | |
| `rows[]` — each row: | | | |
| `.region` | string | | ★ |
| `.period_values_12_month_each` | array[number\|null] | minItems: 4, maxItems: 4 | ★ |
| `.total_24_month` | number \| null | | ★ |
| `.percent_of_global_sales_24_month` | number \| null | min: 0, max: 100 | |

### C.3 sales_data_analysis ★

| Slot | Type | Constraint | Required |
|------|------|-----------|----------|
| `sales_trend_over_time_chart_reference` | string | | |
| `narrative_analysis` | string | textarea | ★ |

### C.4 size_and_characteristics_of_population_using_device ★

| Slot | Type | Constraint | Required |
|------|------|-----------|----------|
| `usage_frequency.single_use_per_patient` | TriState | select | ★ |
| `usage_frequency.multiple_uses_per_patient` | TriState | select | ★ |
| `usage_frequency.average_uses_per_patient` | number \| null | min: 0 | |
| `estimated_size_of_patient_population_exposed` | string | textarea | ★ |
| `characteristics_of_patient_population_exposed` | string | textarea | ★ |

---

## Section D: Information on Serious Incidents

`D_information_on_serious_incidents` — 4 required slots

| Slot | Type | Constraint | Required |
|------|------|-----------|----------|
| `narrative_summary` | string | textarea | ★ |
| `new_incident_types_identified_this_cycle` | string | textarea | |

### D.1 table_2_serious_incidents_by_imdrf_annex_a_by_region ★

Array, minItems: 0. Each row:

| Row Slot | Type | Constraint | Required |
|----------|------|-----------|----------|
| `region` | string | | ★ |
| `imdrf_problem_code_and_term` | string | | ★ |
| `n_current_period` | integer \| null | min: 0 | ★ |
| `rate_percent` | number \| null | min: 0, max: 100 | |
| `complaint_number` | string | | |

### D.2 table_3_serious_incidents_by_imdrf_annex_c_investigation_findings_by_region ★

Array, minItems: 0. Each row:

| Row Slot | Type | Constraint | Required |
|----------|------|-----------|----------|
| `region` | string | | ★ |
| `imdrf_cause_code_and_term` | string | | ★ |
| `n_current_period` | integer \| null | min: 0 | ★ |
| `rate_percent` | number \| null | min: 0, max: 100 | |
| `complaint_number` | string | | |

### D.3 table_4_health_impact_by_investigation_conclusion ★

Array, minItems: 0. Each row:

| Row Slot | Type | Constraint | Required |
|----------|------|-----------|----------|
| `region` | string | | ★ |
| `imdrf_health_impact_annex_f_code_and_term` | string | | ★ |
| `number_of_serious_incidents` | integer \| null | min: 0 | ★ |
| `investigation_conclusion_1.code_and_term` | string | | |
| `investigation_conclusion_1.percent` | number \| null | min: 0, max: 100 | |
| `investigation_conclusion_2.code_and_term` | string | | |
| `investigation_conclusion_2.percent` | number \| null | min: 0, max: 100 | |
| `investigation_conclusion_3.code_and_term` | string | | |
| `investigation_conclusion_3.percent` | number \| null | min: 0, max: 100 | |
| `investigation_conclusion_4.code_and_term` | string | | |
| `investigation_conclusion_4.percent` | number \| null | min: 0, max: 100 | |

---

## Section E: Customer Feedback

`E_customer_feedback` — 2 required slots

| Slot | Type | Constraint | Required |
|------|------|-----------|----------|
| `summary` | string | textarea | ★ |

### E.1 table_6_feedback_by_type_and_source ★

Array, minItems: 0. Each row:

| Row Slot | Type | Constraint | Required |
|----------|------|-----------|----------|
| `feedback_type` | string | | ★ |
| `source` | string | | ★ |
| `count` | integer \| null | min: 0 | ★ |
| `summary` | string | textarea | ★ |

---

## Section F: Product Complaint Types, Counts, and Rates

`F_product_complaint_types_counts_and_rates` — 3 required subsections

### F.1 complaint_rate_calculation ★

| Slot | Type | Constraint | Required |
|------|------|-----------|----------|
| `method_description_and_justification` | string | textarea | ★ |

### F.2 annual_number_of_complaints_and_complaint_rate_by_harm_and_medical_device_problem ★

| Slot | Type | Constraint | Required |
|------|------|-----------|----------|
| `commentary_context_for_exceedances` | string | textarea | |
| `risk_documentation_update_needed` | TriState | select | ★ |

### F.3 table_7_complaint_rate_and_count ★

| Slot | Type | Constraint | Required |
|------|------|-----------|----------|
| `use_if_psur_frequency` | string | enum: `ANNUALLY`, `EVERY_TWO_YEARS` | ★ |

**If ANNUALLY → `annual_format`** ★ (conditional)

| Slot | Type | Constraint | Required |
|------|------|-----------|----------|
| `date_range` | string | | ★ |
| `rows[]` — each row: | | | |
| `.harm` | string | | ★ |
| `.medical_device_problem` | string | | ★ |
| `.current_12_month_complaint_count` | integer \| null | min: 0 | |
| `.current_12_month_complaint_rate` | number \| null | min: 0 | |
| `.max_expected_rate_of_occurrence_from_ract` | number \| null | min: 0 | |
| `grand_total.complaint_count` | integer \| null | min: 0 | |
| `grand_total.complaint_rate` | number \| null | min: 0 | |

**If EVERY_TWO_YEARS → `every_two_years_format`** ★ (conditional)

| Slot | Type | Constraint | Required |
|------|------|-----------|----------|
| `date_ranges` | array[string] | minItems: 2, maxItems: 2 | ★ |
| `rows[]` — each row: | | | |
| `.harm` | string | | ★ |
| `.medical_device_problem` | string | | ★ |
| `.period_1_complaint_count` | integer \| null | min: 0 | |
| `.period_1_complaint_rate` | number \| null | min: 0 | |
| `.period_2_complaint_count` | integer \| null | min: 0 | |
| `.period_2_complaint_rate` | number \| null | min: 0 | |
| `.max_expected_rate_of_occurrence_from_ract` | number \| null | min: 0 | |
| `grand_total.period_1_complaint_count` | integer \| null | min: 0 | |
| `grand_total.period_1_complaint_rate` | number \| null | min: 0 | |
| `grand_total.period_2_complaint_count` | integer \| null | min: 0 | |
| `grand_total.period_2_complaint_rate` | number \| null | min: 0 | |

---

## Section G: Information from Trend Reporting

`G_information_from_trend_reporting` — 2 required subsections

### G.1 overall_monthly_complaint_rate_trending ★

| Slot | Type | Constraint | Required |
|------|------|-----------|----------|
| `graph_reference` | string | | |
| `upper_control_limit_definition` | string | textarea | |
| `breaches_commentary_and_actions` | string | textarea | ★ |

### G.2 trend_reporting_summary ★

| Slot | Type | Constraint | Required |
|------|------|-----------|----------|
| `statement_if_not_applicable` | string | textarea | |

`trend_reports` — array, minItems: 0 ★. Each row:

| Row Slot | Type | Constraint | Required |
|----------|------|-----------|----------|
| `affected_device_models_or_trade_names` | string | textarea | ★ |
| `manufacturer_reference_number` | string | | ★ |
| `date_trend_first_identified` | string | format: date | ★ |
| `date_reported_to_mhra_if_applicable` | string | format: date | |
| `current_status_of_trend_investigation` | string | textarea | ★ |
| `corrective_or_preventive_actions_resulted` | string | textarea | |
| `fsca_reference_number_if_relevant` | string | | |

---

## Section H: Information from FSCA

`H_information_from_fsca` — 2 required slots

| Slot | Type | Constraint | Required |
|------|------|-----------|----------|
| `summary_or_na_statement` | string | textarea | ★ |

### H.1 table_8_fsca_initiated_current_period_and_open_fscas ★

Array, minItems: 0. Each row:

| Row Slot | Type | Constraint | Required |
|----------|------|-----------|----------|
| `type_of_action` | string | | ★ |
| `manufacturer_reference_number` | string | | ★ |
| `issuing_date_or_date_of_final_fsn` | string | format: date | ★ |
| `scope_of_fsca_device_models_within_scope` | string | textarea | ★ |
| `status_of_fsca` | string | | ★ |
| `rationale_and_description_of_action_taken` | string | textarea | ★ |
| `impacted_regions` | string | textarea | ★ |
| `date_reported_to_mhra_if_applicable` | string | format: date | |

---

## Section I: Corrective and Preventive Actions

`I_corrective_and_preventive_actions` — 2 required slots

| Slot | Type | Constraint | Required |
|------|------|-----------|----------|
| `summary_or_na_statement` | string | textarea | ★ |

### I.1 table_9_capa_initiated_current_reporting_period ★

Array, minItems: 0. Each row:

| Row Slot | Type | Constraint | Required |
|----------|------|-----------|----------|
| `capa_number_or_manufacturer_reference_number` | string | | ★ |
| `initiation_date` | string | format: date | ★ |
| `scope_of_capa` | string | textarea | ★ |
| `status_of_capa` | string | | ★ |
| `capa_description` | string | textarea | ★ |
| `root_cause` | string | textarea | ★ |
| `effectiveness_of_capa` | string | textarea | ★ |
| `target_date_for_completion_if_ongoing` | string | format: date | ★ |

---

## Section J: Scientific Literature Review

`J_scientific_literature_review` — 2 required slots

| Slot | Type | Constraint | Required |
|------|------|-----------|----------|
| `literature_search_methodology` | string | textarea | ★ |
| `number_of_relevant_articles_identified` | integer \| null | min: 0 | |
| `summary_of_new_data_performance_or_safety` | string | textarea | ★ |
| `newly_observed_uses` | string | textarea | |
| `previously_unassessed_risks` | string | textarea | |
| `state_of_the_art_changes` | string | textarea | |
| `comparison_with_similar_devices` | string | textarea | |
| `technical_documentation_search_results_reference` | string | | |

---

## Section K: Review of External Databases and Registries

`K_review_of_external_databases_and_registries` — 2 required slots

| Slot | Type | Constraint | Required |
|------|------|-----------|----------|
| `registries_reviewed_summary` | string | textarea | ★ |

### K.1 table_10_adverse_events_and_recalls ★

Array, minItems: 0. Each row:

| Row Slot | Type | Constraint | Required |
|----------|------|-----------|----------|
| `database_or_registry` | string | | ★ |
| `total_matches` | integer \| null | min: 0 | ★ |
| `relevant_findings` | string | textarea | ★ |
| `benchmark_vs_similar_devices` | string | textarea | |
| `regulatory_actions_affecting_similar_devices` | string | textarea | |
| `rmf_update_reference` | string | | |

---

## Section L: Post-Market Clinical Follow-up (PMCF)

`L_pmcf` — 2 required slots

| Slot | Type | Constraint | Required |
|------|------|-----------|----------|
| `summary_or_na_statement` | string | textarea | ★ |

### L.1 table_11_pmcf_activities ★

Array, minItems: 0. Each row:

| Row Slot | Type | Constraint | Required |
|----------|------|-----------|----------|
| `specific_pmcf_activities` | string | textarea | ★ |
| `key_findings` | string | textarea | ★ |
| `impact_on_safety_performance` | string | textarea | ★ |
| `rmf_or_cer_update` | string | textarea | |
| `pmcf_evaluation_report_reference` | string | | |

---

## Section M: Findings and Conclusions

`M_findings_and_conclusions` — 3 required slots

| Slot | Type | Constraint | Required |
|------|------|-----------|----------|
| `benefit_risk_profile_conclusion` | string | textarea | ★ |
| `intended_benefits_achieved` | string | textarea | |
| `limitations_of_data_and_conclusion` | string | textarea | |
| `new_or_emerging_risks_or_new_benefits` | string | textarea | |
| `overall_performance_conclusion` | string | textarea | ★ |

### M.1 actions_taken_or_planned ★

| Slot | Type | Constraint | Required |
|------|------|-----------|----------|
| `benefit_risk_assessment_update` | boolean | default: false | |
| `risk_management_file_update` | boolean | default: false | |
| `product_design_update` | boolean | default: false | |
| `manufacturing_process_update` | boolean | default: false | |
| `ifu_or_labeling_update` | boolean | default: false | |
| `clinical_evaluation_report_update` | boolean | default: false | |
| `sscp_update_if_applicable` | boolean | default: false | |
| `capa_initiated` | boolean | default: false | |
| `fsca_initiated` | boolean | default: false | |
| `action_details_and_follow_up` | string | textarea | ★ |

---

## Summary Statistics

| Metric | Count |
|--------|-------|
| Top-level sections | 13 (A–M) |
| Total required subsection objects | ~50 |
| Total leaf-level slots (all sections) | ~175 |
| Conditional slots (allOf/if/then) | ~12 |
| Table/array slots | 11 |
| Enum/select fields | ~20 |
| Checkbox group fields | 2 (`criteria_used_for_sales_data`, `actions_taken_or_planned`) |
