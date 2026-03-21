"""
Agent-Scoped Context: section constraints, conditional instructions, and addendums.

These complement the global persistent context (global_context.py) with
per-section structural rules governing schema compliance, data grounding,
fabrication prevention, and table handling.

Global writing rules (tone, formatting, terminology) live in global_context.py
and are NOT repeated here.  This module contains only the operational
constraints that vary by section or require more detail than the global rules.
"""

from typing import Dict


# =====================================================================
# 1. CRITICAL CONSTRAINTS (deduplicated — writing rules in global_context)
# =====================================================================

_SECTION_CONSTRAINTS = """\
## CRITICAL CONSTRAINTS

1. **TEMPLATE FIDELITY**: Every field in the schema must be present in your output. \
No extra keys — do not invent or add keys not defined in the schema.

2. **ALLOWED VALUES**: Where the schema specifies an enum, use ONLY those values.

3. **NO REGULATION CITATIONS**: Never cite "MDCG 2022-21", "EU MDR", \
"Regulation (EU) 2017/745", "Article 86/83", "Annex I/II/III/IV/V of MDCG", \
or similar. Apply regulatory guidance silently. Internal document references \
(ISO 14971, IEC 62366) remain permitted when they appear in source data.

4. **DATA GROUNDING**: Every claim must trace to provided statistics or data. \
Use exact numbers from the statistics. Do NOT repeat rates or statistics that \
belong to other sections — each section owns specific data.

5. **BENEFIT-RISK THREAD**: Each section ends with ONE brief sentence connecting \
findings to the overall benefit-risk profile. Do NOT restate quantitative data \
in this sentence. Example: "CooperSurgical considers these findings in the overall \
benefit-risk evaluation."

6. **IMDRF DESCRIPTIVE TERMS ONLY**: Use descriptive terms \
(e.g., "Device breakage or deterioration", "No Harm", "Packaging problem"). \
NEVER include alphanumeric codes (A0701, F0101, C0201) in any narrative or \
table cell.

7. **TABLE COMPLETENESS**: Every table cell must be populated — no empty strings \
or null values. Use "N/A" for text, 0 for counts, 0.00 for rates. Every table \
must have at least one data row. If no data exists, include a single row with \
"N/A" values and explain the absence in the narrative.

8. **EXAMPLES ARE EXAMPLES**: Guidance examples are structural templates only. \
Never copy them verbatim.

9. **CONCISE ANALYTICAL CONTENT**: Write like a safety assessor summarising \
evidence, not like a system explaining how it generated a report. State each \
fact once, interpret it once, move on. Refer to the manufacturer as \
'CooperSurgical' — never use 'we', 'our', or 'I'. The length of your analysis must be \
proportional to the risk signal: a null finding gets 1–2 sentences, not a \
paragraph. A significant safety signal gets a full paragraph with root cause, \
corrective action, and outcome. NEVER explain what a PSUR is, how this report \
was prepared, or why the surveillance system exists. If a sentence answers \
'why does this document exist' instead of 'what the data show', delete it.

10. **SHOW YOUR MATH**: Every rate must show or reference its formula: \
numerator / denominator = rate.

11. **NO MARKETING LANGUAGE**: PSURs are regulatory documents, not marketing \
materials. NEVER use: "superior performance", "best-in-class", "market-leading", \
"world-class", "cutting-edge", "gold standard", "compares favorably", \
"industry-leading", "outstanding". Present facts neutrally.

11. **GROUPED DEVICE HANDLING**: For grouped PSURs, break down all quantitative \
analyses by device or catalog number in addition to aggregate totals. For single \
devices, state this explicitly in Section B.

12. **CROSS-SECTION DATA SCOPE — CRITICAL**: Each section owns specific data. \
Do NOT restate statistics from other sections.
   - C: Sales only (no complaint rates)
   - D: Serious incidents only (no overall rates or UCL)
   - E: Feedback only (no rates, incidents, or UCL)
   - F: Complaint types/counts/rates — the ONLY section for rate calculations
   - G: Trend/UCL — the ONLY section for monthly rate breakdowns
   - H, I: FSCA/CAPA only (no rates)
   - J, K, L: Literature/DB/PMCF only (no rates)
   - M: Synthesizes all — REFERENCE prior conclusions, do not re-derive

**REPETITION IS THE #1 QUALITY DEFECT.** Sections restating complaint rates, \
incident rates, or UCL calculations that are NOT the section's primary subject \
produce a defective document."""


# =====================================================================
# 2. FABRICATION BLOCK (condensed)
# =====================================================================

_FABRICATION_BLOCK = """\
## FABRICATION PROHIBITION

**Fabricating data in a regulated medical device document is a criminal offense.**

DATA MUST COME FROM: DEVICE CONTEXT, PRE-CALCULATED STATISTICS, or ADDITIONAL DATA.

### Source Rules

- **Identifiers** (basic_udi_di, eu_technical_documentation_number, \
us_pre_market_submission_number, emdn_code, risk_management_file_number, \
classification_rule_mdr_annex_viii, certificate_number, fda_clearance): \
Use ONLY from known_identifiers in DEVICE CONTEXT. Missing → "N/A".
- **Document numbers** (associated_documents arrays): ONLY from data. \
Missing → "N/A". Never invent alphanumeric patterns like "PMS-TD103".
- **Dates**: ONLY from DEVICE CONTEXT (start_date, end_date, certificate_date). \
Never invent first-CE-marking, acquisition, or clearance dates.
- **Model/catalog numbers**: ONLY from known_identifiers or ADDITIONAL DATA. \
If none provided → "See technical documentation for complete listing".
- **leading_device**: Must be the product NAME (e.g., "Micropipettes"), \
NEVER a technical documentation number (e.g., "TD103").
- **Complaint numbers**: ONLY from ADDITIONAL DATA. If none → "See complaint records".
- **Historical data**: If has_previous_period_data is false → preceding period \
columns MUST be null.
- **Investigation findings, customer feedback, literature results, external DB \
results, PMCF data, FSCA details**: Only if explicitly provided in ADDITIONAL DATA.
- **Regulatory history** (acquisition dates, registration numbers, TGA ARTG, \
MHRA references): Only if explicitly in the data.

### When Data Is Absent

- Narrative fields: "No [data type] data was available for this reporting period."
- Table arrays: Empty array [].
- Numeric fields: null.
- Identifier/string fields: "N/A" (never verbose explanations).
- Preceding period columns: null.

### ABSOLUTE PROHIBITIONS — ZERO TOLERANCE

The following data types MUST NEVER be fabricated under any circumstances. \
If data was not provided in the input, the section MUST state its absence:

1. **External database results** (Section K): Do NOT invent MAUDE report counts, \
EU Vigilance numbers, MHRA alerts, BfArM reports, TGA results, Health Canada recalls, \
or any quantitative findings from external databases. Do NOT invent industry averages \
or benchmark complaint rates.
2. **Literature search results** (Section J): Do NOT invent article counts, \
author names, journal references, study findings, or meta-analysis results.
3. **PMCF data** (Section L): Do NOT invent registry enrollment numbers, \
patient counts, site counts, complication rates, or PMCF study results.
4. **Trend report details** (Section G): Do NOT invent trend report reference \
numbers (e.g., TR-YYYY-NNN), MHRA reporting dates, root cause determinations, \
or CAPA references not in the input data. Trend reports must ONLY contain data \
from the input. If no trend reports were submitted, state this.
5. **UK Responsible Person details**: Do NOT invent UK RP names, addresses, \
or company details. Use ONLY from device_context. If not provided, write \
"UK Responsible Person details not available in source data."
6. **Comparison benchmarks**: Do NOT invent "industry average" rates, \
"market average" performance, or comparative statistics not in the input.
7. **Marketing claims**: Do NOT use promotional language ("superior performance", \
"best-in-class", "market-leading", "compares favorably"). PSURs are regulatory \
documents, not marketing materials."""


# =====================================================================
# 3. CONDITIONAL INSTRUCTION BLOCKS (only injected where relevant)
# =====================================================================

_DATA_AVAILABILITY = """\
## DATA AVAILABILITY

Check the "available_inputs" field in DEVICE CONTEXT to know what data was provided:
- If "customer_feedback" is NOT listed: Section E must state feedback was not separately collected
- If "external_db" is NOT listed: Section K must state no external database review was conducted
- If "pmcf" is NOT listed: Section L must state no PMCF data was available
- If "literature" is NOT listed: Section J must state no literature search was conducted
- If "fsca" is NOT listed: Section H should state no FSCA data was provided
- If "previous_psur" is NOT listed: No year-over-year comparisons are possible"""


_CROSS_TAB_INSTRUCTIONS = """\
## PRE-COMPUTED CROSS-TABULATIONS

The statistics include pre-computed cross-tabulations. USE THESE EXACT VALUES:
- **harm_by_imdrf**: The exact harm × IMDRF term cross-tabulation for Section F Table 7
- **serious_by_region_imdrf**: The exact regional breakdown of serious incidents for Section D Table 2
- **serious_incidents_detail**: Full list of each serious incident with actual complaint numbers
- **eea_units / eea_countries**: Pre-computed EEA regional aggregate for Section C Table 1
- **uk_units**: Pre-computed UK (Great Britain) aggregate for Section C Table 1 (when UK market detected)

For Section C Table 1: EEA+TR+XI units = eea_units from statistics. DO NOT calculate this yourself.
If UK market data exists, the UK row is pre-computed separately. Do NOT merge UK into EEA or Rest of World.
For Section F Table 7: Use harm_by_imdrf cross-tab EXACTLY. Each complaint appears under ONE harm \
category with ONE IMDRF term. Do NOT double-count.
If table7_rows is provided, use its rows VERBATIM — they already contain the correct IMDRF term, \
harm term, complaint count, and rate."""


_PREBUILT_TABLE_INSTRUCTIONS = """\
## PRE-BUILT TABLE STRUCTURES (from _prefilled)

When _prefilled contains `*_ready_rows` or `table*_annual_format_rows`, these are
complete, verified table rows. Embed them DIRECTLY into your output JSON arrays:

- **table1_ready_rows** (Section C): Region, units, percentage of global. Use as-is.
- **table2_ready_rows** (Section D): Region, IMDRF problem term, count, rate, complaint numbers. Use as-is.
- **table7_annual_format_rows** (Section F): Harm, MDP, count, rate, RACT max rate. Use as-is.
- **table7_grand_total_row** (Section F): Grand total row. Append to annual_format.rows.

Embed these EXACTLY — do not rearrange, recalculate, or modify values.
You may add harm grouping header rows (e.g., {"harm": "Injury", "medical_device_problem": "", ...}) \
above each harm group for Table 7, but data rows must match pre-built values exactly."""


_SURVEILLANCE_PERIOD = """\
## SURVEILLANCE PERIOD DURATION

Calculate the surveillance period duration from start_date and end_date in DEVICE CONTEXT.
Formula: (end_year - start_year) × 12 + (end_month - start_month) + 1 months (both inclusive).
Example: June 2023 to May 2025 = 24 months.
Do NOT hardcode any duration — always compute from the actual dates."""


_EDITORIAL_SCOPE = """\
## EDITORIAL SCOPE

Data-centric sections (C through L) must present their own section-specific facts \
AND provide analytical interpretation proportional to the risk signal:

- NULL/ROUTINE FINDING: State the data point, confirm it is within expectations, \
move on. 1–3 sentences.
- NOTABLE FINDING (e.g., near-threshold rate, UCL exceedance): State the data, \
analyse significance, describe investigation and outcome. One paragraph.
- SIGNIFICANT SAFETY SIGNAL: Full multi-paragraph analysis with root cause, \
corrective action, regulatory notification status, and residual risk assessment.

NEVER explain the purpose of the section, describe the PSUR process, or narrate \
how the analysis was conducted. The reviewer knows. Start with findings.

Save overarching risk-benefit synthesis for Section M. Each data section must \
demonstrate analytical depth through interpretation, not volume. A paragraph \
that restates the same conclusion in multiple wordings is a quality defect."""


_SECTION_M_OVERALL = """\
## SECTION M — OVERALL CONCLUSIONS

Section M (Findings and Conclusions) MUST:
- Synthesise key findings from ALL prior sections (A through L)
- Reference each section by letter; summarise its key finding in 1–2 sentences
- Render an overall benefit-risk conclusion using the exact enum values in the schema
- Address any trends, CAPAs, or changes identified in sections D through L
- Identify data gaps and explain their impact
- State whether any new or emerging risks were identified
- Address ALL NINE boolean action flags with concise justification for each

This is the most critical section of the PSUR. Reference prior section conclusions \
— do NOT re-derive them. A Section M that repeats statistics already stated in \
Sections C–L is a quality defect. Favour sharp, specific cross-references ('The \
complaint rate analysis in Section F confirmed all categories within RACT \
thresholds') over restatement of the underlying numbers."""


_PREFILLED_VALUES = """\
## IMMUTABLE PRE-FILLED VALUES — MANDATORY COMPLIANCE

If the ADDITIONAL DATA contains a key called `_prefilled`, every value within it
is a pre-computed, verified fact. You MUST:

1. Use these values EXACTLY as provided — do not round, recalculate, approximate, \
or paraphrase them.
2. When writing narrative that references a number covered by a prefilled value, \
use the prefilled number verbatim. For example, if `exact_eea_units_current_period` \
is 166630, write "166,630" — not "approximately 167,000".
3. When populating table cells that correspond to prefilled values, copy the value exactly.
4. The `exact_grand_total_rate` in Section F is calculated from raw integers \
(total_complaints / total_units_sold × 100). Do NOT recalculate by summing rounded \
sub-category rates.
5. Document numbers from prefilled values are real identifiers extracted from source \
files. Use them exactly. If no prefilled document number is provided, write "N/A" — \
NEVER invent a document number."""


# =====================================================================
# 3b. UK MDR CONDITIONAL BLOCK (injected when UK sales detected)
# =====================================================================

_UK_MDR_REQUIREMENTS = """\
## UK MDR REGULATORY REQUIREMENTS

The device is placed on the Great Britain market. The UK Medical Devices \
(Post-market Surveillance Requirements) (Amendment) (Great Britain) Regulations 2024 \
(UK MDR) apply IN ADDITION to EU MDR requirements.

### PSUR Content (Reg 44ZM(3))
The PSUR must include:
- Number of devices placed on the UK market (from pre-computed UK row in Table 1)
- Estimated number of devices put into service in the UK
- Estimate of the size and other characteristics of the UK user population
- Estimate of the size and other characteristics of the population outside the UK
- Usage frequency estimate where practicable

### UK Serious Incident Reporting (Reg 44ZH)
- Serious incidents must be reported to the Secretary of State (via MHRA)
- General serious incidents: within 15 days of awareness
- Death or unanticipated serious deterioration: within 10 days
- Serious public health threat: within 2 days
- If serious incidents from the UK are present, note MHRA reporting compliance

### UK Field Safety Corrective Actions (Reg 44ZJ-44ZK)
- FSCAs must be reported to the Secretary of State with risk assessment
- FSCAs taken outside GB must also be reported if the same device model is on the GB market
- FSCAs require an initial report, risk assessment, and field safety notice

### UK Trend Reporting (Reg 44ZN)
- Significant increases in frequency or severity of incidents must be reported to MHRA
- Trend assessment must cover UK-specific data when available

### Documentation (Reg 44ZQ)
- Retain all PMS documentation for the PMS period, or:
  - 15 years for implantable devices
  - 10 years for all other devices

### UK Responsible Person
- The UK Responsible Person is responsible for ensuring UK MDR compliance \
on behalf of the manufacturer for devices placed on the GB market.

INTEGRATION GUIDANCE: Where this PSUR addresses topics covered by UK MDR \
requirements, include UK-specific data and analysis alongside (not replacing) \
EU MDR content. The UK region row in sales/incident tables satisfies the \
Reg 44ZM(3) requirement for UK-specific volume reporting."""


# =====================================================================
# 3c. CLASS I DEVICE CONDITIONAL BLOCK
# =====================================================================

_CLASS_I_NON_STERILE_GUIDANCE = """\
## CLASS I NON-STERILE DEVICE — REGULATORY AWARENESS

This device is classified EU MDR Class I (non-sterile, non-measuring). \
Key implications for this PSUR:

- **No Notified Body involvement**: Conformity assessment is by manufacturer \
self-certification via Declaration of Conformity. Do NOT reference NB reviews, \
NB opinions, NB certificates, NB audit findings, or NB actions.
- **PSUR is voluntary**: The mandatory obligation for Class I non-sterile devices \
is a PMS Report under Article 85, not a PSUR under Article 86. This PSUR-format \
document is prepared voluntarily for internal governance purposes. State this in \
the relevant sections.
- **PMCF**: Not mandatory for well-established Class I devices. Section L should \
note PMCF is voluntarily considered but not required.
- **Cover page NB fields**: May show NB details for administrative/template \
completeness but narratives must not imply NB oversight.
- **Sterility**: This device is non-sterile. Do NOT describe it as sterile, \
reference sterilisation processes, or use phrases like "sterile single-use"."""


# =====================================================================
# 4. SECTION ADDENDUMS (per-section specific instructions)
# =====================================================================

SECTION_ADDENDUMS: Dict[str, str] = {

    "A_executive_summary": """
## SECTION A — SPECIFIC INSTRUCTIONS
- The executive summary must answer four key questions for the Notified Body:
  1. What is the status of actions from the previous PSUR? (Use previous_psur data if available)
  2. Were there any Notified Body actions on the previous PSUR?
  3. Has the data collection period changed?
  4. What is the overall benefit-risk conclusion?
- The benefit_risk_assessment_conclusion field MUST use the exact enum value from the schema.
- Select NOT_ADVERSELY_IMPACTED_UNCHANGED when: all complaint rates within RACT thresholds, \
no UCL breaches requiring regulatory reporting, no new unacceptable risks, serious incidents \
within expected rates.
- Select ADVERSELY_IMPACTED when: serious safety concern identified, confirmed adverse trend, \
or new unacceptable risk found.
- Include quantitative highlights: total complaints, serious incident count, overall complaint \
rate, trend status.
- The executive summary MUST provide enough context that a reviewer reading only this section \
understands the device's current safety posture. Include: device identification, reporting \
period, volume of distribution, complaint overview, serious incident summary, trend status, \
and the benefit-risk conclusion with supporting rationale.
- Write the summary field as a dense, fact-packed narrative — NOT a narrated checklist. \
Model it on the gold-standard example. Every sentence must state a finding or conclusion. \
Do NOT explain what a PSUR is or how this report was prepared.
- Do NOT open with 'This PSUR is prepared as a stand-alone document…' or any variant. \
Open with device identification and reporting period, then go straight to findings.""",

    "B_scope_and_device_description": """
## SECTION B — SPECIFIC INSTRUCTIONS
- This section establishes the complete scope of the PSUR. Every device, model, and catalog \
number covered must be identified.
- Use ONLY identifiers from _prefilled (basic_udi_di, eu_technical_documentation_number, \
etc.) — never invent document numbers.
- device_timeline_and_status must include certification milestones from the CER/device context.
- If this PSUR covers a single device (not grouped), state this explicitly in \
device_grouping_information.
- For model_catalog_numbers: use only what appears in the source data. If none provided, \
state "See technical documentation."
- The leading_device field must be the product/family NAME, never a technical documentation \
number.
- **UK Responsible Person**: Use ONLY the uk_responsible_person data from device_context. \
If no UK RP data was provided in device_context, write "UK Responsible Person details not \
available in source data." Do NOT invent company names, addresses, registration numbers, \
or office locations for the UK RP.""",

    "C_volume_of_sales_and_population_exposure": """
## SECTION C — SPECIFIC INSTRUCTIONS
- Table 1 rows are PRE-COMPUTED in _prefilled.table1_ready_rows. Use them EXACTLY.
- The EEA+TR+XI total is pre-computed as exact_eea_units_current_period. Do NOT recalculate.
- If a UK row is present in table1_ready_rows, it is pre-computed as exact_uk_units_current_period.
- The Worldwide total is pre-computed as exact_worldwide_units_current_period. Do NOT recalculate.
- sales_methodology must select the appropriate checkbox from the schema enum values.
- For single-use devices, the denominator is "units distributed within the reporting period."
- Do NOT mention complaint rates in this section — that belongs to Section F.
- The narrative_analysis MUST be a substantive multi-paragraph narrative that:
  1. Describes the geographic distribution pattern and any notable market concentration
  2. Explains the sales methodology and why it is appropriate for this device type
  3. Discusses any year-over-year volume changes and what drives them (if prior data available)
  4. Characterises the patient population exposed: demographics, clinical settings, usage patterns
  5. Explains how the sales volume establishes the denominator for complaint rate calculations \
in subsequent sections
  6. Notes any markets with disproportionate volume that may warrant region-specific analysis
  7. If UK market data is present, describes the UK distribution volume and notes it as a \
separate regulatory jurisdiction under UK MDR
- Do NOT open with meta-narration like 'This section establishes the statistical foundation…' \
or 'This reporting period establishes the baseline…'. Start with the data.
- This section establishes the statistical foundation for the entire PSUR. A thin narrative \
undermines every subsequent section's analysis.""",

    "D_information_on_serious_incidents": """
## SECTION D — SPECIFIC INSTRUCTIONS
- Table 2 MUST show serious incidents broken down by region (EEA+TR+XI, UK, Worldwide) × \
IMDRF Annex A descriptive term (no alphanumeric codes).
- If UK market data is present, include UK as a separate region in Table 2 and note \
MHRA reporting obligations (15-day/10-day/2-day timelines per UK MDR Reg 44ZH).
- Table 3 MUST show investigation findings using IMDRF Annex C descriptive terms (if \
available, no alphanumeric codes).
- Table 4 MUST cross-tabulate IMDRF Health Impact terms against Investigation Conclusion terms.
- State whether any NEW incident types not in the Risk Management File were identified. \
If none, say so explicitly.
- Compare observed rates to RACT max expected rates if ract_max_expected_rate is in the data.
- Use exact_serious_incident_count and exact_serious_incident_rate from _prefilled.
- Reference actual complaint numbers from the data for each serious incident in Table 2.
- The narrative_summary MUST be a substantive multi-paragraph analysis that:
  1. Characterises the overall serious incident profile: total count, rate per unit, \
comparison to RACT thresholds and prior periods
  2. Analyses each incident type: what device problem occurred, what harm resulted, \
what investigation was conducted, and what the root cause determination was
  3. Discusses the geographic distribution of serious incidents and any regional patterns
  4. Addresses whether any incidents represent new or previously unidentified failure modes
  5. Describes the investigation process and corrective actions taken
  6. Concludes with an assessment of whether serious incident rates indicate any safety concern
- If there were zero serious incidents, still provide a substantive narrative explaining \
the monitoring methodology, how incidents would be classified, and why the absence of \
serious incidents is consistent with the device's risk profile.""",

    "E_customer_feedback": """
## SECTION E — SPECIFIC INSTRUCTIONS
- This section covers NON-COMPLAINT feedback: surveys, training feedback, sales reports, \
customer engagement.
- If no dedicated customer feedback data was provided, state this clearly: "No structured \
customer feedback was collected separately from the formal complaint process during this \
reporting period."
- Do NOT repurpose complaint data as customer feedback — complaints belong in Sections D and F.
- Table 6 should ONLY contain actual customer feedback items. If none exist, use an empty \
array [].
- Do NOT fabricate survey results, response rates, training session counts, or satisfaction \
scores.
- Even when no feedback data exists, the summary narrative must still be substantive and should:
  1. Describe what types of customer feedback mechanisms CooperSurgical maintains
  2. Explain that during this reporting period, no structured feedback separate from \
the complaint system was collected
  3. Describe CooperSurgical's approach to gathering user feedback in general
  4. Note whether any indirect feedback (e.g., from sales representatives, training \
sessions, or user groups) was received
  5. Conclude with how this section's findings contribute to the benefit-risk assessment""",

    "F_product_complaint_types_counts_and_rates": """
## SECTION F — SPECIFIC INSTRUCTIONS
- Table 7 rows are PRE-COMPUTED in _prefilled.table7_annual_format_rows. Map them DIRECTLY \
into annual_format.rows.
- The table7_grand_total_row from _prefilled provides the verified grand total. Append it \
to annual_format.rows.
- Each row has: harm, medical_device_problem, complaint_count, complaint_rate, \
complaint_percentage.
- If ract_max_expected_rate is present in a row, include it and state whether the observed \
rate is WITHIN or EXCEEDS the threshold.
- The Grand Total row must sum exactly. Do NOT invent rows beyond what the pre-computed \
data provides.
- State the complaint rate formula explicitly: (complaint count / units distributed) × 100 \
= rate %.
- Use exact_total_complaints and exact_grand_total_rate from _prefilled.
- The method_description_and_justification MUST explain:
  1. The numerator definition (what constitutes a complaint, how they are classified)
  2. The denominator definition (units distributed, why this is appropriate)
  3. The calculation methodology with the explicit formula
  4. How IMDRF coding was applied to categorise complaints
  5. How RACT thresholds were established and what they represent
- Keep method_description_and_justification concise — regulators expect brief methodology, \
not a tutorial. State the formula, definitions, and rationale without over-explaining.
- The commentary_context_for_exceedances MUST provide:
  1. Analysis of each complaint category: count, rate, comparison to RACT threshold
  2. The dominant complaint types and what they reveal about device performance
  3. For categories exceeding RACT: root cause analysis, corrective actions, justification \
for continued acceptability
  4. For categories within RACT: confirmation of acceptable performance with context
  5. Analysis of harm distribution: what proportion resulted in injury vs. no harm
  6. Overall assessment of the complaint profile and what it means for device safety

### CROSS-SECTION CONSISTENCY WITH SECTION D
If Section D (PRIOR SECTION FINDINGS) reports a specific serious incident count and harm \
classifications, the harm categories in Table 7 MUST be consistent. Specifically:
- The number of complaints classified as "Injury" or "Serious Injury" in Table 7 must be \
consistent with the serious incident count reported in Section D.
- If Section D reports zero serious incidents, Table 7 must NOT show complaint rows with \
"Serious Injury" harm classification (and vice versa).
- Use the SAME harm terminology across D and F. Do NOT use "serious injury" in one section \
and "injury" in another for the same events.

### STERILE STATUS CONSISTENCY
Use ONLY the device's actual sterility status from the DEVICE CONTEXT block. If the device \
is described as "Non-sterile", do NOT use phrases like "sterile single-use devices" or \
reference sterilisation validation in the methodology. Match the sterility status exactly.

### SINGLE-USE / REUSABLE STATUS CONSISTENCY — CRITICAL
Use ONLY the device's actual single-use/reusable status from the DEVICE CONTEXT block. \
If the device is described as "single-use", do NOT use phrases like "non-single-use devices", \
"reusable devices", or "multi-use devices" ANYWHERE in Section F. This includes the \
method_description_and_justification — the denominator justification must say "single-use" \
if the device is single-use. One device = one patient episode for single-use devices.""",

    "G_information_from_trend_reporting": """
## SECTION G — SPECIFIC INSTRUCTIONS
- State the UCL calculation methodology: mean + 3 × standard deviation of monthly complaint \
rates.
- **RATE DISPLAY**: Use the `_prefilled` *_pct fields for ALL human-readable percentages:
  - `mean_monthly_rate_pct` = mean rate as percentage (e.g., 4.40 means "4.40%")
  - `ucl_pct` = UCL as percentage (e.g., 14.76 means "14.76%")
  - `std_dev_pct` = standard deviation as percentage
  - `monthly_rates_pct` = list of monthly rates as percentages
  - Do NOT use raw decimal fields (mean, ucl_3sigma, etc.) with a % sign.
    Raw decimals like 0.044 are NOT percentages — they must be multiplied by 100 first.
    The *_pct fields already have this done for you.
- For months where rate exceeded UCL, describe the month, observed rate (from \
monthly_rates_pct), and UCL value (ucl_pct).
- State whether any Western Electric rule violations were detected (from \
_prefilled.western_electric_violations).
- If no trend reports were submitted to regulatory authorities, state this explicitly.
- Reference the chart: "The monthly complaint rate trend is illustrated in the accompanying \
control chart."
- Do NOT restate total complaint counts or overall complaint rates — those belong to \
Section F.
- The breaches_commentary_and_actions MUST provide substantive analysis:
  1. Explain the statistical process control methodology: why UCL is used, how it is calculated, \
what it represents for this device
  2. Present the mean monthly rate and UCL with exact values from _prefilled
  3. Walk through the monthly rates chronologically, identifying any patterns (seasonal, \
event-driven, production-lot-related)
  4. For each UCL breach: describe the month, the observed rate, the magnitude of exceedance, \
the investigation conducted, root cause if identified, and corrective actions taken
  5. If no breaches occurred, state this in 1–2 sentences. Do NOT pad a null finding \
with paragraphs of explanation about what would have happened if breaches occurred.
  6. Address Western Electric rule violations (if any) and their statistical significance
  7. Compare the trend to prior periods if available
  8. Conclude with the overall trend assessment and implications for the benefit-risk profile
- Do NOT write 'We recognize that the absence of year-over-year data limits…' or similar \
meta-narration about data limitations. If prior-period data is absent, simply state \
trend comparison was not possible and move on.

**ANTI-FABRICATION — TREND REPORTS**:
- Do NOT invent trend report reference numbers (e.g., TR-YYYY-NNN).
- Do NOT invent CAPA references linked to trend reports unless they appear in the CAPA input data.
- Do NOT invent MHRA reporting dates, Competent Authority notifications, or regulatory \
submission dates for trend reports.
- Do NOT invent root cause determinations, failure analysis results, or investigation \
findings for trend reports.
- Only describe trend reports that are explicitly documented in the input data.
- If no formal trend reports were submitted, state this clearly and focus the analysis on \
the statistical process control data from _prefilled.

**WESTERN ELECTRIC RULE INTERPRETATION — CRITICAL**:
When describing Western Electric (WE) rule violations from _prefilled.western_electric_violations:
- Each entry in the list represents ONE detection event (not a count of violations).
- Rule 4 detects a RUN of consecutive months on the same side of the mean. \
Describe it as: "A run of N consecutive months above/below the mean was detected \
from [start] to [end], triggering a Western Electric Rule 4 signal."
- Do NOT say "N violations of Rule 4" — say "a Rule 4 signal was detected \
(N consecutive months above/below mean)."
- Rule 4 below the mean is NOT a safety concern — it indicates rates are \
consistently BELOW average, which is favourable. State this explicitly: \
"This below-mean run indicates a sustained period of lower-than-average \
complaint rates, which does not represent a safety signal."
- Rules 1-3 are potential signals; Rule 4 below-mean is not.""",

    "H_information_from_fsca": """
## SECTION H — SPECIFIC INSTRUCTIONS
- If no FSCA data was provided, the summary_or_na_statement MUST clearly state: \
"No Field Safety Corrective Actions were initiated during this reporting period."
- If FSCAs exist: describe each action's type, scope, affected devices, geographic \
regions, implementation status, and effectiveness.
- Table 8 must include ALL FSCAs initiated during the reporting period AND any open \
FSCAs from prior periods.
- Each FSCA entry must include: type_of_action, manufacturer_reference_number, \
issuing_date, scope, status, rationale_and_description, impacted_regions.
- Cross-reference with Section I (CAPA) if any FSCAs resulted from CAPAs or vice versa.
- Do NOT fabricate FSCA reference numbers, dates, or details.

### CROSS-SECTION CONSISTENCY WITH SECTION G
If Section G (PRIOR SECTION FINDINGS) describes corrective actions such as lot \
quarantine, process improvements, production hold, or investigation — you MUST \
address whether an FSCA assessment was conducted for those actions. Provide EXPLICIT \
rationale:
- If the corrective actions described in G did NOT constitute an FSCA, explain WHY \
(e.g., "The lot-specific investigation and quarantine described in Section G did not \
meet the threshold for a Field Safety Corrective Action because [rationale]").
- Do NOT simply state "No FSCAs were initiated" without addressing the corrective \
actions described in earlier sections. A reviewer reading G and H back-to-back must \
not perceive a contradiction.""",

    "I_corrective_and_preventive_actions": """
## SECTION I — SPECIFIC INSTRUCTIONS
- If no CAPA data was provided, the summary_or_na_statement MUST clearly state: \
"No Corrective and Preventive Actions were initiated during this reporting period \
relating to device safety, performance, or quality."
- If CAPAs exist: for each, state the problem, scope, root cause, status, and \
effectiveness evidence (if completed).
- Table 9 must include: capa_number, initiation_date, scope, status, description, \
root_cause, effectiveness, target_completion_date.
- Cross-reference with Sections D (serious incidents), F (complaint trends), G (UCL \
breaches), and H (FSCAs) as appropriate.
- Do NOT fabricate CAPA numbers, root cause determinations, or effectiveness metrics.

### CROSS-SECTION CONSISTENCY WITH SECTIONS G AND H
If Section G (PRIOR SECTION FINDINGS) describes corrective actions such as process \
improvements, lot quarantine, production changes, or investigation — you MUST address \
whether these warranted formal CAPA initiation. Provide EXPLICIT risk-based rationale:
- If the actions described in G did NOT result in a formal CAPA, explain WHY (e.g., \
"The process adjustments described in Section G were managed through CooperSurgical's routine \
quality system and did not meet the severity/recurrence threshold for a formal \
corrective and preventive action").
- If Section H describes FSCAs, address whether CAPAs were linked to those FSCAs.
- Do NOT simply state "No CAPAs were initiated" without addressing corrective actions \
described in earlier sections. A reviewer reading G, H, and I in sequence must see \
logical consistency.""",

    "J_scientific_literature_review": """
## SECTION J — USER-INPUT SECTION (NO LLM GENERATION OF RESULTS)

**CRITICAL**: Literature search results are provided separately by the user. \
The LLM MUST NOT generate, fabricate, or infer any literature search results.

### What you MUST do:
- Set number_of_relevant_articles_identified to null.
- Set summary_of_new_data_performance_or_safety to: "No formal literature search \
results were provided for this PSUR reporting period. Literature search results \
are maintained separately and will be incorporated by the regulatory affairs team. \
The most recent comprehensive literature review was completed as part of the \
Clinical Evaluation Report [reference CER document number from device context \
if available]."
- For newly_observed_uses: "No newly observed uses were reported during this period."
- For previously_unassessed_risks: "No previously unassessed risks were identified \
during this period."
- For state_of_the_art_changes: "State-of-the-art assessment is maintained in the \
Clinical Evaluation Report."
- For comparison_with_similar_devices: "Comparison with similar devices is documented \
in the Clinical Evaluation Report."
- literature_search_methodology: Describe ONLY the general methodology framework \
(databases, search term categories, inclusion criteria) WITHOUT inventing specific \
results, article counts, or findings.

### What you MUST NOT do:
- Do NOT invent article counts, author names, journal titles, or study findings.
- Do NOT claim a literature search was "conducted" during this period.
- Do NOT fabricate methodology details beyond general framework.
- Do NOT generate benchmark rates or comparative findings from literature.""",

    "K_review_of_external_databases_and_registries": """
## SECTION K — USER-INPUT SECTION (NO LLM GENERATION OF RESULTS)

**CRITICAL**: External database search results are provided separately by the user. \
The LLM MUST NOT generate, fabricate, or infer any external database findings.

### What you MUST do:
- Set registries_reviewed_summary to: "External database and registry review results \
are maintained separately and will be incorporated by the regulatory affairs team. \
CooperSurgical's external database review protocol covers the following databases: FDA MAUDE, \
EU Vigilance/Eudamed, MHRA, BfArM, TGA DAEN, and Health Canada. Search parameters \
include device name, product codes, GMDN codes, and the PSUR reporting period date range. \
No formal external database review results were provided for this PSUR period."
- Set table_10 (adverse events and recalls) to an empty array [].

### What you MUST NOT do:
- Do NOT invent MAUDE report counts or event numbers.
- Do NOT invent EU Vigilance report counts.
- Do NOT invent MHRA field safety notices or alert counts.
- Do NOT invent BfArM, TGA, or Health Canada results.
- Do NOT invent "industry average" complaint rates or benchmark data.
- Do NOT claim any database was "reviewed" or "searched" with results.
- Do NOT invent regulatory actions, recalls, or field corrections for competitors.
- Do NOT use phrases like "compares favorably", "superior performance", \
or any comparative marketing language.
- Do NOT generate ANY quantitative findings (numbers of reports, percentages, \
rates) from external sources.""",

    "L_pmcf": """
## SECTION L — USER-INPUT SECTION (NO LLM GENERATION OF RESULTS)

**CRITICAL**: PMCF data and results are provided separately by the user. \
The LLM MUST NOT generate, fabricate, or infer any PMCF study results.

### What you MUST do:
- Set summary_or_na_statement to: "PMCF data and results are maintained separately \
and will be incorporated by the regulatory affairs team. CooperSurgical's PMCF approach for this \
device is documented in the PMCF Plan [reference plan document from device context \
if available]. PMCF activities include ongoing complaint trend monitoring and \
literature surveillance as part of CooperSurgical's post-market surveillance system. No formal \
PMCF evaluation report results were provided for this PSUR period."
- Table 11 should list ONLY general PMCF activity categories (complaint monitoring, \
literature review) with status "Ongoing" — no invented findings or results.

### What you MUST NOT do:
- Do NOT invent registry enrollment numbers, patient counts, or site counts.
- Do NOT invent complication rates, response rates, or PMCF study findings.
- Do NOT claim PMCF activities "confirmed" device performance (that requires data).
- Do NOT fabricate PMCF evaluation report references or dates.
- Do NOT generate specific PMCF findings or conclusions about device safety.""",

    "M_findings_and_conclusions": """
## SECTION M — SPECIFIC INSTRUCTIONS
- Sub-section (a) benefit_risk_profile_conclusion MUST reference key findings from ALL \
preceding sections A–L by name.
- Use the PRIOR SECTION FINDINGS provided in the data to ensure comprehensive coverage.
- Sub-section (b) intended_benefits_achieved must cite evidence from the reporting period.
- Sub-section (c) limitations_of_data_and_conclusion must identify real data limitations \
(not generic disclaimers).
- Sub-section (d) new_or_emerging_risks_or_new_benefits must provide a definitive statement.
- Sub-section (e) MUST address all NINE boolean action flags, setting each to true or false.
  For each flag set to true, provide brief narrative explaining the action.
- Sub-section (f) overall_performance_conclusion must be a clear, final determination.
- The overall conclusion must be unambiguous — never hedging language.
### SECTION M DEPTH REQUIREMENTS:
- benefit_risk_profile_conclusion: Reference each section A–L with a 1–2 sentence \
summary of its key finding, then synthesise into an overall benefit-risk determination. \
Use sharp cross-references ('Section F confirmed all complaint categories within RACT \
thresholds') rather than restating the underlying numbers. The synthesis must demonstrate \
ALL available evidence was considered — but concisely.
- intended_benefits_achieved: Cite reporting period evidence demonstrating the device's \
clinical benefits are being realised. Reference the intended use and patient population.
- limitations_of_data_and_conclusion: Identify SPECIFIC data gaps for this \
PSUR (not boilerplate). For each gap, state its impact on the assessment in one sentence. \
Do NOT write 'We recognize that the absence of…' — simply state the gap.
- new_or_emerging_risks_or_new_benefits: Definitive statement. \
If no new risks: state this and note what would trigger reassessment. \
If new risks: describe them specifically.
- overall_performance_conclusion: Clear, unambiguous final determination. \
be clear, unambiguous, and reference the key evidence supporting the conclusion.

### IMMUTABLE QUANTITATIVE FACTS FOR SECTION M — USE THESE ONLY
Section M MUST use ONLY the pre-computed values from _prefilled for ALL quantitative claims:
- **Regional sales distribution**: Use exact_region_breakdown from _prefilled. These are \
the same numbers as Section C's Table 1. Do NOT recalculate, approximate, or invent \
different regional totals. If exact_region_breakdown is not available, reference \
Section C's findings without inventing specific numbers.
- **Complaint categories**: Use exact_complaint_categories from _prefilled. These match \
Section F's Table 7. Do NOT invent different complaint category names, counts, or rates.
- **Total units, complaints, rates**: Use total_units_sold, total_complaints, \
exact_overall_complaint_rate, exact_serious_incident_rate from _prefilled.
- **Device classification**: Use device_class_eu and sterility_status from _prefilled \
for any regulatory classification statements.
- **Manufacturer identity**: Use manufacturer_name and manufacturer_srn from _prefilled. \
Do NOT use any other manufacturer name. The legal manufacturer in Section M MUST be \
identical to the cover page. Do NOT invent or substitute alternative company names \
(e.g., "Neotech Products LLC", "Ackrad Laboratories"). If manufacturer_name is in \
_prefilled, use it EXACTLY.
- If a number is not in _prefilled, OMIT it or reference the section where it appears. \
Do NOT estimate or fabricate to fill gaps.

### HANDLING SECTIONS J, K, L IN M'S SYNTHESIS
Sections J, K, and L are user-input sections. When summarising them in M:
- State what data was available (e.g., "No formal literature search results were provided \
for this PSUR period" rather than fabricating findings).
- Identify these as data limitations in sub-section (c).
- Do NOT claim these sections "confirmed" or "validated" device safety — that requires \
actual data.
- Do NOT contradict J/K/L by claiming detailed findings or analyses that were not provided.

### PSUR JUSTIFICATION FOR CLASS I DEVICES
If device_class_eu from _prefilled is "CLASS_I" and sterility_status is non-sterile:
- Section M MUST include a statement explaining that this PSUR-format report is \
prepared voluntarily. The mandatory obligation for Class I non-sterile/non-measuring \
devices under EU MDR is a PMS Report (Article 85), not a PSUR (Article 86).
- State that using the PSUR template is an internal governance choice providing \
structured safety assessment. Do NOT claim it is a regulatory requirement.
- Do NOT reference Notified Body review, NB audit findings, or NB actions.""",
}


# =====================================================================
# 5. PUBLIC API
# =====================================================================

def build_section_constraints() -> str:
    """Return the deduplicated critical constraints block."""
    return _SECTION_CONSTRAINTS


def build_fabrication_block() -> str:
    """Return the condensed fabrication prohibition block."""
    return _FABRICATION_BLOCK


def build_conditional_instructions(section_key: str, uk_market_detected: bool = False,
                                    class_i_no_nb: bool = False) -> str:
    """Build instruction blocks gated to the section that needs them.

    - DATA AVAILABILITY: all sections
    - CROSS-TABS / PRE-BUILT TABLES: C, D, F only
    - EDITORIAL SCOPE: data sections C–L
    - SURVEILLANCE PERIOD: all sections
    - SECTION M OVERALL: M only
    - PRE-FILLED VALUES: all sections
    - UK MDR REQUIREMENTS: sections B, C, D, F, G, H, L, M when UK sales detected
    - CLASS I GUIDANCE: all sections when device is Class I non-sterile
    """
    letter = section_key.split("_")[0]
    parts: list = []

    # Data availability — all sections
    parts.append(_DATA_AVAILABILITY)

    # Pre-computed tables — only tabular sections
    if letter in ("C", "D", "F"):
        parts.append(_CROSS_TAB_INSTRUCTIONS)
        parts.append(_PREBUILT_TABLE_INSTRUCTIONS)

    # Editorial scope — data sections C–L
    if letter in ("C", "D", "E", "F", "G", "H", "I", "J", "K", "L"):
        parts.append(_EDITORIAL_SCOPE)

    # Surveillance period — all sections
    parts.append(_SURVEILLANCE_PERIOD)

    # Section M overall conclusions — M only
    if letter == "M":
        parts.append(_SECTION_M_OVERALL)

    # Immutable pre-filled values — all sections
    parts.append(_PREFILLED_VALUES)

    # UK MDR requirements — relevant sections when device is on the GB market
    if uk_market_detected and letter in ("B", "C", "D", "F", "G", "H", "L", "M"):
        parts.append(_UK_MDR_REQUIREMENTS)

    # Class I non-sterile guidance — all sections when applicable
    if class_i_no_nb:
        parts.append(_CLASS_I_NON_STERILE_GUIDANCE)

    return "\n\n".join(parts)


def get_section_addendum(section_key: str) -> str:
    """Return the section-specific addendum, or empty string."""
    return SECTION_ADDENDUMS.get(section_key, "")
