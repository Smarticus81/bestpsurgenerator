# PSUR Validator Question Bank
## FormQAR-054 Rev C — Comprehensive Validation Checklist for `validator.py`

**Purpose:** Each question below is designed to be evaluated programmatically by a `validator.py` agent against a rendered PSUR document. The agent asks itself each question, determines PASS / FAIL / WARNING, and logs the result. Questions are organized by the section hierarchy of FormQAR-054 and are grouped into structural, content, quantitative, cross-reference, and regulatory compliance categories.

---

## 0. GLOBAL DOCUMENT RULES

### 0.1 Structural Integrity

1. Does the document contain all thirteen required sections in exact order: Cover Page, Table of Contents, Section A, Section B, Section C, Section D, Section E, Section F, Section G, Section H, Section I, Section J, Section K, Section L, Section M?
2. Does every section header exactly match the FormQAR-054 title (e.g., "Section A: Executive Summary," not "Section A — Executive Summary" or "A. Executive Summary")?
3. Has any section been added that does not exist in FormQAR-054?
4. Has any section been removed or omitted without an explicit, justified N/A statement?
5. Has any section been renamed, merged with another section, or reordered relative to the template sequence?
6. Does the Table of Contents accurately reflect every section header and its page number in the rendered document?
7. Are page numbers present and sequential throughout the document?

### 0.2 Formatting and Narrative-Only Doctrine

8. Is every narrative field written as continuous professional prose paragraphs, with zero bullet points, numbered lists, hyphens used as list markers, or outline-style formatting anywhere in the document?
9. Are tables present only where FormQAR-054 explicitly requires them (Tables 1–11 and the associated documents table, the Basic UDI-DI table, the model/catalog number listing)?
10. Does every table exactly match the column structure specified in the template for that table number?
11. Is the document free of markdown formatting artifacts (e.g., bold markers, hash headers, code blocks) in the rendered output?
12. Is the entire document written in third-person present tense with passive voice where appropriate, with no first-person references ("I," "we," "our")?
13. Is the document free of promotional language, marketing claims, reassurance phrasing, or minimization of risk?
14. Is the document free of explicit citations to regulation article numbers, standard clause numbers, or guidance document section references in narrative text?

### 0.3 Quantitative Rigor (Global)

15. Is every quantitative claim backed by a specific number traceable to a named data source?
16. Are all rates expressed to exactly two decimal places?
17. Are all percentages expressed to exactly one decimal place?
18. Are all unit counts expressed as whole numbers with no rounding or estimation?
19. Does every calculation show or reference its methodology (numerator, denominator, formula)?

### 0.4 Terminology

20. Are IMDRF codes presented using descriptive terms alongside alphanumeric codes (e.g., "A0502 — Device Breakage") rather than codes alone or terms alone?
21. Are IMDRF Annex A (Medical Device Problem), Annex C (Investigation Findings), Annex D (Investigation Conclusion), and Annex F (Health Impact) terminologies used consistently throughout the document wherever adverse events, complaints, or incidents are discussed?

### 0.5 Benefit-Risk Thread

22. Does every section (A through M) contain at least one sentence that explicitly connects the section's findings to the overall benefit-risk profile of the device?
23. Is the benefit-risk thread internally consistent across all sections, culminating logically in the Section M determination?

### 0.6 Grouped Device Handling

24. If the PSUR covers multiple devices or catalog numbers, are all quantitative analyses (sales, complaints, incidents, rates) broken down by individual device or catalog number in addition to aggregate totals?
25. If the PSUR covers a single device, is grouped device handling appropriately marked as not applicable in Section B?

### 0.7 Data Integrity

26. Does the document contain any invented data, estimated values not explicitly labeled as estimates, assumed trends without statistical justification, or external benchmarks not provided in the input data?
27. Where data is unavailable, does the document explicitly state that the conclusion is based on the absence of evidence rather than making an inference?
28. Does the document clearly distinguish between observed data, analysis of that data, and conclusions drawn from the analysis in every narrative section?
29. Are causal claims made only where explicitly supported by investigation findings or statistical evidence?

---

## 1. COVER PAGE

### 1.1 Manufacturer Information

30. Is the manufacturer company name present and stated as "CooperSurgical, Inc."?
31. Is the complete manufacturer address present, including street, city, state, ZIP, and country?
32. Is the Manufacturer Single Registration Number (SRN) present and in the correct format (XX-MF-XXXXXXXXXX)?
33. Is the Authorized Representative section completed (or marked as not applicable with justification)?
34. If an Authorized Representative is listed, does it include the full legal name, complete address, and SRN in format XX-AR-XXXXXXXXXX?

### 1.2 Device Information

35. Is at least one device name present, exactly matching the name on the IFU and EU-type examination certificate?
36. Is the Basic UDI-DI present for MDR devices (or Device Family Name present for legacy devices)?

### 1.3 Regulatory Information

37. Is the EU-type examination certificate number present?
38. Is the certificate date of issue present in a valid date format?
39. Is the Notified Body name present?
40. Is the Notified Body number present as a four-digit numeric string?
41. Is the "PSUR available within 3 working days" confirmation present with a selection (Yes checked)?

### 1.4 Document Information

42. Is the data collection period present with both start date and end date?
43. Is the data collection period end date exactly 12 months (for annual) or 24 months (for biennial) after the start date, within a tolerance of ±3 days?
44. Is the PSUR cadence stated (annually or every two years)?
45. Does the stated cadence match the device classification (Class IIb/III/implantable = annually; Class IIa = every two years)?

---

## 2. SECTION A: EXECUTIVE SUMMARY

### 2.1 Previous PSUR Actions Status

46. Is there a narrative describing actions and status from the previous PSUR, or an explicit statement that this is the first PSUR?
47. For each identified action from the previous PSUR, is the action type stated (CAPA, RMF update, IFU revision, FSCA, design change)?
48. For each action, is the specific issue it addressed described?
49. For each action, is the implementation status described, including what was done during this reporting period?
50. For each action, is the effectiveness assessment included?
51. For each completed action, is the impact on the benefit-risk profile explicitly stated?
52. Is a status checkbox selected (COMPLETED, IN_PROGRESS, NOT_STARTED, or NOT_APPLICABLE)?
53. Is the status selection logically consistent with the narrative (e.g., if one action is open, status should be IN_PROGRESS, not COMPLETED)?
54. If status is IN_PROGRESS or NOT_STARTED, are target completion dates, responsible parties, and interim risk mitigation measures described?

### 2.2 Notified Body Review Status

55. Is a selection made for whether the previous PSUR was reviewed by the Notified Body (YES, NO, or N/A)?
56. If YES, is there a narrative describing any actions, observations, or findings raised by the NB?
57. If the NB raised actions, is the status of each NB action described, including evidence of closure or progress?
58. If an NB action led to changes in safety documentation or risk profile, is the impact stated?
59. Is a status statement provided for NB actions (e.g., "All actions closed" or description of remaining open items)?

### 2.3 Data Collection Period Changes

60. Is a selection made for whether the data collection period changed from the previous PSUR cycle (YES or NO)?
61. If YES, is a substantive regulatory or operational justification provided for the change?
62. If YES, is an assessment of the impact on comparability of results included?
63. Do the dates stated here exactly match the Cover Page data collection period dates?

### 2.4 Benefit-Risk Assessment Conclusion

64. Is a benefit-risk conclusion selection made (NOT_ADVERSELY_IMPACTED_UNCHANGED or ADVERSELY_IMPACTED)?
65. If ADVERSELY_IMPACTED, is there a high-level summary describing the specific adverse change, supporting data, actions taken, and current status?
66. If NOT_ADVERSELY_IMPACTED_UNCHANGED, is the conclusion stated as a definitive, unambiguous declaration?
67. Is the Section A benefit-risk conclusion consistent with the detailed determination in Section M?

---

## 3. SECTION B: SCOPE AND DEVICE DESCRIPTION

### 3.1 Device Information

68. Is the product name stated exactly as it appears on the IFU, CE certificate, and EUDAMED registration?
69. Is the implantable device status indicated (YES or NO)?
70. If the device is implantable, is the PSUR cadence set to annual regardless of classification?

### 3.2 Device Classification

71. Is the EU MDR classification stated (Class IIa, IIb, or III)?
72. Is the EU technical documentation number provided?
73. Is the MDR Annex VIII classification rule stated with rule number and applicability descriptor?
74. Is the UK classification section completed (or explicitly marked as not applicable with justification)?
75. If UK classification is applicable, is the UK classification value stated, the UK conformity assessment route described, and the UK classification rule provided?
76. Is the US FDA classification stated (Class I, II, or III)?
77. Is the US pre-market submission number provided (510(k), PMA, or De Novo reference)?

### 3.3 Device Timeline and Status

78. Are all EU certification milestones present: first declaration of conformity date, first EC/EU certificate date, and first CE-marking date?
79. If UK is applicable, are all UK milestones present: first certification/DOC date, first CE-marking date, first market placement date, and first service deployment date?
80. Is the market status stated (on market, no longer marketed, or in service only)?
81. If the device is no longer marketed, is the date sales ceased provided along with the reason?
82. If no longer marketed, is there an estimate of devices still in use in the field, based on device lifetime and historical sales?
83. Is the last device sold date provided (or "N/A — currently in active production" stated)?
84. Is the certificate status stated (Valid with expiry date, Expired, Withdrawn, or Suspended)?
85. Is the projected end of PMS period calculated and stated (or stated as indeterminate for actively marketed devices)?
86. Is there a definitive statement confirming whether the PSUR obligation continues, with the stated reason?

### 3.4 Device Description and Intended Use

87. Is the device description sufficiently detailed for a reviewer unfamiliar with the device to understand its physical form, materials, operating principle, components, and accessories?
88. Does the device description state whether the device is sterile, single-use or reusable, powered or non-powered, and whether it contains software?
89. Are any design features that serve as risk control measures identified in the description?
90. Is the intended purpose/use stated exactly as in the approved labeling, without paraphrasing or expansion?
91. Are indications provided, defining the specific clinical conditions for which the device is indicated?
92. Are contraindications provided?
93. Are target populations defined for both patient population (age, sex, clinical characteristics) and user population (healthcare professional type, setting)?

### 3.5 Device Information Breakdown

94. For MDR devices: Is the Basic UDI-DI table present with columns for Basic UDI-DI, device trade name, EMDN code, and changes from previous PSUR?
95. For legacy devices: If applicable, is the device group/family table present with trade names, GMDN code, and market availability by member state?
96. Does every catalog number appearing in sales or complaint data in subsequent sections appear in the device listing in Section B?

### 3.6 Data Collection Period / Reporting Period

97. Do the dates in this section exactly match the Cover Page and Section A data collection period dates?
98. If UK devices are in scope, is the UK PMS period determination included with device lifetime calculation, shelf life, service life, and projected end of PMS period?

### 3.7 Technical Information

99. Is the Risk Management File document control number provided?
100. Is the associated documents table present listing at minimum: PMS Plan, Clinical Evaluation Report, and PMCF Plan (or justification for why PMCF is not required)?
101. Does each associated document entry include document type, document number, and document title?

### 3.8 Model/Catalog Numbers

102. Is a complete listing of all model and catalog numbers covered by this PSUR provided (either inline or by reference to an appendix or PMS Plan)?

### 3.9 Device Grouping

103. If multiple devices are grouped, is a justification provided addressing shared CER, similar technology, same intended use, and same Notified Body?
104. Is the leading device identified with a rationale based on classification or risk level?
105. Is there confirmation that all grouped devices share the same CER and the same Notified Body?
106. Is it stated whether the grouping has changed from the previous PSUR?
107. If grouping is not applicable (single device), is this explicitly stated?

---

## 4. SECTION C: VOLUME OF SALES AND POPULATION EXPOSURE

### 4.1 Sales Methodology

108. Is the sales counting methodology explicitly stated and justified (units distributed, episodes of use, active installed base, units implanted, etc.)?
109. Is the chosen methodology consistent with the device type (e.g., units distributed for single-use devices)?
110. Is there a statement confirming the methodology is consistent with previous PSURs?

### 4.2 Market History

111. Is there a narrative providing the market introduction history, including first market dates by region and significant market events during the reporting period?
112. Is the current market footprint summarized?

### 4.3 Sales Data Table (Table 1 or Table 2)

113. Is the correct table format used (annual Table 1 for annual cadence; biennial Table 2 for biennial cadence)?
114. Does the table show three preceding 12-month periods plus the current data collection period?
115. Are all required regions present: EEA+TR+XI, Australia, Brazil, Canada, China, Japan, UK, United States, any country with >5% of global sales, Rest of World, and Worldwide total?
116. Does the Worldwide row equal the exact sum of all regional rows for each period column?
117. Does the Percent of Global Sales column sum to exactly 100.0%?
118. Are all unit counts expressed as whole numbers?
119. Are all percentages expressed to one decimal place?
120. Do the date ranges in the column headers match the Cover Page data collection period and correctly span the preceding periods?
121. Are the date ranges in mmm-yyyy to mmm-yyyy format?

### 4.4 Sales Data Analysis

122. Is there a narrative stating the total units sold worldwide during the current data collection period?
123. Is the period-over-period change stated in both absolute number and percentage?
124. Are the primary markets identified by percentage of global sales?
125. Are significant regional changes explained (new market launches, exits, unusual volume changes)?
126. For grouped devices, are sales trends broken down by individual device/catalog number?
127. Is the sales trajectory's implication for population exposure discussed?
128. Is there a benefit-risk linkage statement explaining how sales volume and geographic distribution affect the representativeness of complaint and incident rates?
129. Is a sales trend chart referenced or embedded?

### 4.5 Population Exposure

130. Is the usage frequency characterized (single-use per patient, or multiple uses with average frequency)?
131. Is the estimated patient population exposure stated with the calculation methodology described?
132. Does the patient exposure estimate logically correspond to the sales figures (e.g., for single-use devices, exposure ≈ units sold)?
133. Are any factors creating uncertainty in the estimate described (devices sold but not yet used, multi-pack configurations, etc.)?
134. Is there a statement on whether the exposure volume is sufficient for meaningful statistical analysis?
135. Are the characteristics of the patient population described (age, sex, clinical conditions, geographic distribution)?
136. Is there a statement confirming whether the actual patient population is consistent with the intended target population from the labeling?
137. Is there an identification of any subpopulations that may experience different benefit-risk profiles?

---

## 5. SECTION D: INFORMATION ON SERIOUS INCIDENTS

### 5.1 Narrative Summary

138. Is the total number of serious incidents during the period stated?
139. Is the breakdown by severity category provided (death, serious injury, malfunction that could have led to death or serious injury)?
140. Is the overall serious incident rate calculated and stated (incidents / units sold × 100)?
141. Are the most common IMDRF Medical Device Problem codes identified with both code and descriptive term?
142. Is the current period serious incident rate compared to preceding periods?
143. Is the observed rate compared to the maximum expected rate of occurrence from the RACT?
144. Are investigation findings and root causes described for each incident?
145. Is there a determination of whether any new incident types were observed that are not in the Risk Management File?
146. Is there a conclusion on whether the serious incident profile is consistent with the known risk profile?
147. Are CAPAs or FSCAs initiated as a result of incident analysis referenced?
148. Is there an explicit benefit-risk linkage statement for the serious incident data?

### 5.2 Table 2: Serious Incidents by IMDRF Annex A by Region

149. Is Table 2 present with columns for Region, IMDRF Problem Code and Term, N (current period), Rate (%), and Complaint Number?
150. Are rates calculated using region-specific sales as denominator for regional rates and worldwide sales for worldwide rates?
151. Are all three required regional breakdowns present (EEA+TR+XI, UK, Worldwide)?
152. Do IMDRF codes include both the alphanumeric code and the descriptive term?

### 5.3 Table 3: Serious Incidents by IMDRF Annex C Investigation Findings by Region

153. Is Table 3 present with the correct column structure?
154. Are investigation findings classified using IMDRF Annex C codes with both code and term?

### 5.4 Table 4: Health Impact by Investigation Conclusion

155. Is Table 4 present as a cross-tabulation of IMDRF Annex F (Health Impact) against Annex D (Investigation Conclusion)?
156. Does every serious incident reported in Tables 2 and 3 have a corresponding entry in Table 4?
157. Do the total counts in Table 4 reconcile with the total serious incidents stated in the narrative?

### 5.5 New Incident Types

158. Is there an explicit statement on whether new incident types were identified, or a definitive statement that none were found?

---

## 6. SECTION E: CUSTOMER FEEDBACK

### 6.1 Summary Narrative

159. Is the total volume of feedback received stated?
160. Are the collection channels identified (surveys, sales visits, training sessions, customer service)?
161. Are feedback themes categorized and quantified where possible?
162. Is there a comparison to feedback from previous periods?
163. Is there a statement on whether feedback indicates any safety or performance concerns not captured in the formal complaint process?
164. Is there a benefit-risk linkage connecting feedback themes to the benefit-risk profile?
165. Are any actions taken in response to feedback described?

### 6.2 Table 6: Feedback by Type and Source

166. If structured feedback was received, is Table 6 present with columns for Feedback Type, Source, Count, and Summary?
167. If no structured feedback was received, is the table explicitly removed or marked N/A?
168. Do the counts in Table 6 reconcile with the total feedback volume stated in the narrative?

---

## 7. SECTION F: PRODUCT COMPLAINT TYPES, COUNTS, AND RATES

### 7.1 Complaint Rate Calculation Methodology

169. Is the complaint rate formula explicitly defined (numerator definition, denominator definition)?
170. Is the numerator definition clear on what is included and excluded (e.g., product quality complaints included, administrative/shipping excluded)?
171. Does the denominator match the methodology stated in Section C?
172. Is there a statement confirming consistency with previous PSUR cycles?
173. Is there a benefit-risk linkage explaining how complaint rate relates to risk?

### 7.2 Commentary on Exceedances

174. Is there a narrative addressing any harm/MDP category where the observed complaint rate exceeded the RACT maximum expected rate, or an explicit statement that no exceedances occurred?
175. For each exceedance, is the root cause investigated and explained?
176. For each exceedance, is there an assessment of whether it is statistically significant or within normal variation?
177. For each exceedance, is there a determination of whether a RACT update, CAPA, or other action is required?
178. For each exceedance, is the impact on the benefit-risk profile explicitly stated?
179. Is a definitive YES/NO determination provided on whether risk documentation updates are needed?

### 7.3 Table 7: Complaint Rate and Count

180. Is Table 7 present with the correct column structure matching the PSUR cadence (annual or biennial format)?
181. Does Table 7 include all harm/MDP combinations that have at least one complaint?
182. Does Table 7 include "No Health Consequence or Impact" as a harm category?
183. Is there a Grand Total row?
184. Is the complaint rate for each row calculated as (Complaint Count / Units Sold) × 100?
185. Does the Grand Total complaint count equal the sum of all individual rows?
186. Does the denominator used for rate calculations match the worldwide sales figure from Section C, Table 1?
187. Is the RACT reference included with document number?
188. Are the RACT maximum expected rates present for each harm/MDP combination?
189. Is every complaint rate compared against its corresponding RACT threshold?
190. For grouped devices, are complaints broken down by individual device/catalog number in addition to aggregates?

---

## 8. SECTION G: INFORMATION FROM TREND REPORTING

### 8.1 Monthly Complaint Rate Trending

191. Is a monthly complaint rate control chart referenced or embedded?
192. Is the UCL calculation methodology described (formula, baseline period, mean, standard deviation)?
193. Is the baseline period for UCL calculation explicitly stated?
194. Is the UCL value stated to sufficient precision?

### 8.2 Breach Analysis

195. Is there a narrative addressing any UCL breaches, or an explicit statement that no breaches occurred?
196. For each breach, is the specific month, observed rate, and UCL value identified?
197. For each breach, is the root cause investigated (systematic issue vs. outlier)?
198. For each breach, is a determination made on whether formal trend reporting to a regulatory authority was required?
199. For each breach, is the benefit-risk impact assessed?
200. Is the mean monthly complaint rate for the current period stated and compared to the baseline?

### 8.3 Trend Reporting Summary

201. If no trend reports were submitted, is there an explicit N/A statement?
202. If trend reports were submitted, is the trend reports table present with all required columns: Affected device models, Manufacturer reference number, Date trend first identified, Date reported to MHRA (if applicable), Current status, CAPAs resulted, FSCA reference?
203. For each trend report, is the investigation status and outcome described?
204. Are trend reports cross-referenced to Sections D, F, and I as applicable?

---

## 9. SECTION H: INFORMATION FROM FIELD SAFETY CORRECTIVE ACTIONS (FSCA)

### 9.1 Summary

205. Is there a narrative summarizing FSCAs or an explicit N/A statement?
206. For each FSCA, is the triggering safety concern described?
207. For each FSCA, are the affected devices, type of action, and geographic scope stated?
208. For each FSCA, is the implementation status and effectiveness assessed?
209. For each FSCA, is the impact on the benefit-risk profile explicitly stated?
210. Are any related CAPAs cross-referenced to Section I?

### 9.2 Table 8: FSCA Initiated Current Period and Open FSCAs

211. If FSCAs exist, is Table 8 present with all required columns: Type of action, Manufacturer reference number, Issuing date/Date of final FSN, Scope/Device models, Status, Rationale and description, Impacted regions, Date reported to MHRA?
212. If no FSCAs exist, is the table removed or marked N/A?
213. Are all FSCAs mentioned in the narrative present in the table and vice versa?

---

## 10. SECTION I: CORRECTIVE AND PREVENTIVE ACTIONS

### 10.1 Summary

214. Is the total number of CAPAs initiated during the reporting period stated, or an explicit N/A statement provided?
215. For each CAPA, is the problem statement, scope, and current status described?
216. Is the root cause for each CAPA identified?
217. For completed CAPAs, is effectiveness verification described with evidence?
218. For open CAPAs, are interim risk mitigation measures and target completion dates stated?
219. Is there a benefit-risk linkage explaining how each CAPA contributes to maintaining the benefit-risk profile?
220. Are CAPAs cross-referenced to the vigilance data (Section D), complaint data (Section F), trend data (Section G), or FSCA data (Section H) that triggered them?

### 10.2 Table 9: CAPA Initiated Current Reporting Period

221. If CAPAs exist, is Table 9 present with all required columns: CAPA Number, Initiation Date, Scope, Status, Description, Root cause, Effectiveness, Target date?
222. If no CAPAs exist, is the table removed or marked N/A?
223. Are all CAPAs mentioned in the narrative present in the table and vice versa?
224. Does the CAPA data align with CooperSurgical's BSR-QAR-005 CAPA procedure requirements (investigation, verification, effectiveness review)?

---

## 11. SECTION J: SCIENTIFIC LITERATURE REVIEW

### 11.1 Literature Search Methodology

225. Is the search methodology described, including databases searched, search terms, date range, inclusion/exclusion criteria, and screening methodology?
226. Are the number of initial hits, deduplicated results, screened results, and final included articles stated?
227. Is the search date range consistent with the data collection period?

### 11.2 Findings

228. Is the number of relevant articles identified explicitly stated?
229. Is there a narrative summarizing new safety or performance data from the literature?
230. Are published complication rates and performance outcomes compared to the device's observed complaint and incident rates from Sections D and F?
231. Is there a statement on newly observed uses (or explicit confirmation that none were found)?
232. Is there a statement on previously unassessed risks (or explicit confirmation that none were found)?
233. Is there a statement on state-of-the-art changes (or explicit confirmation that none occurred)?
234. Is there a comparison with similar devices based on published data?
235. Is there a reference to the location of detailed literature search results within the technical documentation (e.g., CER appendix)?
236. Is there a benefit-risk linkage stating how literature findings affect the benefit-risk determination?

---

## 12. SECTION K: REVIEW OF EXTERNAL DATABASES AND REGISTRIES

### 12.1 Summary Narrative

237. Is there a narrative listing all databases and registries reviewed with search methodology?
238. At minimum, are FDA MAUDE, a European vigilance database (e.g., BfArM), and MHRA database reviewed?
239. For each database, are total matches, relevant findings, and comparison with similar devices reported?
240. Is there a determination of whether external database review identified any new risks not in the RMF?
241. Is there a benefit-risk linkage statement?

### 12.2 Table 10: Adverse Events and Recalls

242. Is Table 10 present with columns for Database/Registry, Total matches, Relevant findings, Benchmark vs similar devices, Regulatory actions affecting similar devices, and RMF update reference?
243. Are all databases mentioned in the narrative represented in the table?
244. Is every RMF update reference either a specific document reference or an explicit "No update required" statement?

---

## 13. SECTION L: POST-MARKET CLINICAL FOLLOW-UP (PMCF)

### 13.1 Summary

245. Is there a narrative summarizing PMCF activities, or an explicit N/A statement with justification for why PMCF is not required?
246. If PMCF activities were conducted, are the specific activities described (studies, surveys, registry analyses)?
247. Are key findings summarized, including safety outcomes and performance data?
248. Is there a statement on whether off-label use was identified?
249. Is there a statement on whether new risks were identified beyond those in the RMF?
250. Is there a statement on how PMCF findings have been integrated into the CER?
251. Is there a benefit-risk linkage statement?

### 13.2 Table 11: PMCF Activities

252. If PMCF activities were conducted, is Table 11 present with columns for Specific Activities, Key Findings, Impact on safety/performance, RMF/CER update, and PMCF Evaluation Report reference?
253. If PMCF is not applicable, is the table removed or marked N/A?

---

## 14. SECTION M: FINDINGS AND CONCLUSIONS

### 14.1 Sub-section (a): Benefit-Risk Profile Conclusion

254. Does the opening summary reference all data sources analyzed: total sales volume, total complaints and rate, total serious incidents and rate, number of literature articles reviewed, external databases reviewed, CAPAs, FSCAs, and PMCF findings?
255. Are the key findings from each preceding section (A through L) synthesized?
256. Is the definitive determination present as a clear, bold, unambiguous statement (NOT adversely impacted / remains UNCHANGED, or HAS been adversely impacted)?
257. Does this determination match the selection made in Section A?
258. If the determination is "adversely impacted," is the specific impact, supporting quantitative data, corrective actions, and monitoring plan described?
259. Are complaint rates confirmed as within or exceeding RACT thresholds, consistent with Section F?
260. Are serious incident rates confirmed as within or exceeding expected ranges, consistent with Section D?
261. Are literature and external database conclusions confirmed as consistent with Sections J and K?
262. Are PMCF conclusions confirmed as consistent with Section L?

### 14.2 Sub-section (b): Intended Benefits Achieved

263. Are all intended benefits from the device's intended use statement listed?
264. For each benefit, is evidence from the reporting period cited (PMCF data, feedback, literature, complaint rates)?
265. Is there a definitive statement on whether all intended benefits were achieved, partially achieved, or not achieved?

### 14.3 Sub-section (c): Limitations of Data and Conclusion

266. Are specific data limitations identified (incomplete reporting, time lags, small samples, reliance on estimates)?
267. For each limitation, is the impact on the validity and reliability of conclusions assessed?
268. Is there a concluding statement on whether the data was adequate for robust benefit-risk assessment despite limitations?

### 14.4 Sub-section (d): New or Emerging Risks or New Benefits

269. Is there a definitive statement on whether new or emerging risks were identified?
270. If new risks exist, are they fully described (source, frequency, severity, affected population)?
271. If new risks exist, have they been incorporated into the RMF with risk controls described?
272. Is there a definitive statement on whether new benefits were identified?
273. Is the impact on the overall risk profile and benefit-risk determination stated?

### 14.5 Sub-section (e): Actions Taken or Planned

274. Are all nine boolean action flags addressed (benefit-risk assessment update, RMF update, product design update, manufacturing process update, IFU/labeling update, CER update, SSCP update, CAPA initiated, FSCA initiated)?
275. For each flag set to true, is there a narrative describing the specific action, rationale, implementation status, and follow-up plan?
276. For each flag set to true, is the benefit-risk linkage explained?
277. Are the actions stated here consistent with the CAPAs in Section I and FSCAs in Section H?
278. Is a follow-up monitoring plan for the next PSUR cycle described?

### 14.6 Sub-section (f): Overall Performance Conclusion

279. Is there a final statement affirming or denying that the device continues to perform as intended?
280. Does the conclusion reference the application area, target population, and key performance characteristics?
281. Is evidence from across the PSUR summarized (sales stability, complaint rates, PMCF data, literature)?
282. Is continued suitability for intended purpose and target population confirmed?
283. Is the next PSUR update period and schedule stated?
284. Is there a final affirmation that the benefit-risk profile remains acceptable (or a description of remediation if not)?

---

## 15. CROSS-REFERENCE AND INTERNAL CONSISTENCY CHECKS

### 15.1 Date Consistency

285. Do the data collection period start and end dates match across the Cover Page, Section A, Section B, Section C column headers, and Section M?
286. Do preceding period date ranges in Section C tables align with the stated cadence and current period dates?

### 15.2 Sales / Denominator Consistency

287. Is the worldwide sales figure in Section C Table 1 used consistently as the denominator for all rate calculations in Sections D, F, and G?
288. Are regional sales figures in Table 1 used as denominators for regional rate calculations in Tables 2, 3, and 7?
289. Does the patient exposure estimate in Section C correspond to the sales figures used as denominators?

### 15.3 Complaint / Incident Count Reconciliation

290. Does the total number of complaints stated in Section F (Grand Total in Table 7) reconcile with the complaints discussed across Sections D, E, F, and G?
291. Are the serious incidents in Section D a subset of total complaints in Section F (i.e., serious incidents ≤ total complaints)?
292. Do the IMDRF codes used in Section D Tables 2, 3, and 4 align with the codes used in Section F Table 7?
293. Are complaint reference numbers consistent across all sections where they appear?

### 15.4 CAPA / FSCA Cross-References

294. Does every CAPA in Section I Table 9 that was triggered by complaint or incident data have a corresponding reference in the relevant Section D, F, G, or H narrative?
295. Does every FSCA in Section H Table 8 that resulted in a CAPA have a cross-reference in Section I?
296. Are the CAPA and FSCA boolean flags in Section M consistent with the content of Sections H and I?

### 15.5 Risk Management Alignment

297. Are all RACT maximum expected rates cited in Section F traceable to the Risk Management File document number stated in Section B?
298. Is the UCL calculation in Section G based on a defined baseline period, and are the baseline statistics traceable to data from previous PSUR cycles?
299. If any complaint rates exceeded the RACT threshold, is this reflected in the Section M determination and the actions-taken flags?

### 15.6 Benefit-Risk Thread Continuity

300. Is the benefit-risk conclusion in Section A consistent with the detailed determination in Section M sub-section (a)?
301. Does the Section M synthesis reference findings from every preceding section (A through L) without omitting any section?
302. If any section identified a concern (e.g., a UCL breach in Section G, an exceedance in Section F, an FSCA in Section H), is that concern explicitly addressed and resolved in Section M?

---

## 16. REGULATORY COMPLIANCE META-CHECKS

### 16.1 MDCG 2022-21 Alignment

303. Does the PSUR comply with MDCG 2022-21 Annex I template structure?
304. Does the data presentation follow MDCG 2022-21 Annex II table formats?
305. Does the data assessment follow MDCG 2022-21 Annex III rules (data split by Basic UDI-DI, by region, by year; IMDRF terminology used)?
306. Does the PSUR cadence comply with MDCG 2022-21 Annex IV requirements for the device classification?

### 16.2 EU MDR Article 86 Compliance

307. Does the PSUR summarize the results and conclusions of the analysis of PMS data?
308. Does the PSUR include the conclusions of the benefit-risk determination?
309. Does the PSUR include the main findings of the PMCF?
310. Does the PSUR include the volume of sales and an estimate of population exposure?

### 16.3 Internal QMS Procedure Compliance

311. Does complaint handling data align with the BSR-QAR-044 Complaint Handling Procedure definitions (complaint = written, electronic, or oral communication alleging deficiencies)?
312. Does CAPA documentation align with BSR-QAR-005 CAPA Procedure requirements (investigation, root cause, effectiveness verification, closure)?
313. Does risk management alignment follow BSR-ENG-007 Procedure for Risk Management lifecycle principles?
314. Does trend reporting methodology align with BSR-QAR-029 Post-Market Surveillance Trending Procedure?
315. Does the PSUR structure comply with WI-QAR-004 Work Instruction for PSUR?

### 16.4 UK-Specific Requirements (If Applicable)

316. If UKCA devices are in scope, does the PSUR include UK-specific classification, conformity assessment details, and classification rule?
317. If UKCA devices are in scope, is the UK PMS period determination included with device lifetime and projected end of PMS period?
318. If UKCA devices are in scope, are UK-specific sales data isolated and presented separately from EEA data?
319. If FSCAs or trend reports were submitted, is the MHRA reporting date included where applicable?

---

## 17. ABSENCE-OF-EVIDENCE DISCIPLINE

320. Where no serious incidents occurred, does the document state "No serious incidents were reported" rather than implying safety through silence?
321. Where no customer feedback was received, does the document state this explicitly rather than omitting the section?
322. Where no CAPAs were initiated, does the document provide an explicit N/A statement?
323. Where no FSCAs were initiated, does the document provide an explicit N/A statement?
324. Where no trend reports were filed, does the document provide an explicit N/A statement?
325. Where PMCF is not applicable, does the document provide a justification for the determination rather than omitting the section?
326. Where no new risks, new incident types, new benefits, or state-of-the-art changes were identified, does the document state each absence explicitly?

---

## 18. TONE AND PROFESSIONAL STANDARDS

327. Is the document free of reassurance phrasing that minimizes risk (e.g., "only a minor issue," "nothing to be concerned about," "extremely safe")?
328. Is the document free of marketing language (e.g., "industry-leading," "best-in-class," "superior performance")?
329. Is precision valued over readability throughout (e.g., exact rates rather than "approximately" or "roughly")?
330. Is the tone consistent with a document that will be audited line-by-line by a Notified Body?
331. Are all example texts from the guidance JSON absent from the rendered document (i.e., no verbatim copying of illustrative examples)?

---

*Total: 331 validation questions across 18 categories.*
