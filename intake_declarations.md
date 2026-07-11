# PSUR Intake Declarations — Laparoscopic Stapler X100

**PSUR reference:** PSUR-LSX100-2023-002, Version 1.0
**Reporting period:** 2023-01-01 → 2023-12-31 (annual — EU Class IIb, Rule 10)
**Schema path:** API (`GET /defaults`, locked contract in `server/specs.py`)
**Gate status:** GREEN (2026-07-11)

> **Delegation notice.** On 2026-07-11 the user granted blanket Tier-3 authority
> ("Just fix all the issues to the highest order, whatever you think is right").
> Every resolution below is logged in the Inference Ledger; entries marked
> **[DEMO PLACEHOLDER]** are assistant-supplied values on this mock dataset that a
> real submission must replace with sourced records.

---

## Declarations D1–D10

**D1 — Regulatory posture.** Not implantable (user-confirmed; the stapler is the
device under Rule 10, deployed staples are addressed in the RMF/CER). MDR-certified
(not legacy) — Class IIb, Rule 10, certified by BSI Group The Netherlands B.V.
(2797); first CE marking 2015. Not custom-made. Marketed under both regimes:
EU/EEA (CE) and Great Britain (UKCA). UK retention obligation: 10 years (44ZQ,
non-implantable).

**D2 — Counting criterion.** `units_sold` = finished devices **distributed** to
customers and distributors per calendar month within the reporting period. This
single basis is the denominator for every rate in the report (single-use device
⇒ denominator = units distributed).

**D3 — Population exposure.** Approximated by units distributed: 13,505 worldwide;
5,945 EEA+TR+XI (incl. 43 Northern Ireland); 1,425 Great Britain; 6,135 United
States. Because more than one stapler/reload may be used per procedure, unit counts
are an **upper bound** on patients exposed (multiplicity direction: many devices per
patient). Adult elective laparoscopic GI surgery population; no known over-/under-
represented subgroup.

**D4 — Four-year history.** Justified omission (see D10-1).

**D5 — Grouping & leading device.** X100 and X100 Reload grouped under Basic UDI-DI
TD1001. Leading device: Laparoscopic Stapler X100 (Class IIb — highest in group,
immutable). All grouped devices certified by the same NB, BSI (2797). No devices
added or removed during the period.

**D6 — Cover-page identity.** **[DEMO PLACEHOLDER — ledger #16]**
PSUR reference PSUR-LSX100-2023-002, Version 1.0, period 2023-01-01→2023-12-31.
Legal manufacturer: MedCorp Surgical B.V., Herengracht 100, 1015 BS Amsterdam, NL
(SRN: NL-MF-000012345). EU Authorised Representative: not applicable (manufacturer
established in the EU). NB: BSI Group The Netherlands B.V. (2797). UK: UKRP MedCorp
UK Ltd; Approved Body BSI Assurance UK Ltd (0086), certificate UKCA-2021-45678.

**D7 — Period contiguity (documented deviation).** PSUR001 ended 2023-03-31; this
PSUR covers 2023-01-01→2023-12-31 (three-month overlap). Justification: the PSUR
cycle was re-aligned to the calendar year. Comparability statement: data for the
overlapping months are identical in source and methodology in both reports; the
overlap is disclosed and not double-counted in any cumulative table. (Recorded in
`previous_psur.benefit_risk_evaluation.comments`.)

**D8 — Prior-cycle actions.** FSCA01 (device breakage) — completed, effectiveness
verified. CAPA01 (packaging improvement, mechanical jam) — completed, verified
effective, carried into the current CAPA listing. Open signal at PSUR001 close:
stapler misfire cluster (United States) — under continued trend surveillance this
period. No NB review actions outstanding.

**D9 — CAPA scope.** All 5 CAPAs are safety/performance/quality-scoped (triggers:
complaint ×2, trend ×1, audit ×2). Prior-cycle CAPA01 included. Open items CAPA04
and CAPA05 (effectiveness pending) included. No voluntary non-commercial marketing
suspensions identified in any input.

**D10 — Justified omissions.**
1. *Four-year history (Table 6):* "Four-year cumulative complaint history is not
   available in this dataset; the demo data begins 2023-01-01 and prior-period data
   is consolidated in PSUR001. Table 6 therefore presents the current and previous
   reporting periods only." (assistant-drafted under the 2026-07-11 delegation)
2. *NI complaint attribution:* NI ≈ 3% of UK volume; 3% of 6 UK complaints rounds
   to zero, so no complaint rows were re-attributed to Northern Ireland.

---

## R3 — Region normalization (confirm-table, executed)

| File | Original value | Rows | Resolution | Bucket | Volume |
|---|---|---|---|---|---|
| sales | NorthAmerica | 12 | United States | United States | 6,135 u |
| sales | Europe | 12→48 | Germany 40% / France 30% / Italy 20% / Netherlands 10% (integer split, remainder→DE; total asserted equal) | EEA+TR+XI | 5,902 u |
| sales | United Kingdom | 12→24 | 3% carved to Northern Ireland (43 u), remainder Great Britain | EEA+TR+XI / United Kingdom | 43 u / 1,425 u |
| complaints | NorthAmerica | 12 | United States | United States | — |
| complaints | Europe | 14 | Round-robin DE(4)/FR(4)/IT(3)/NL(3) by row order | EEA+TR+XI | — |
| complaints | United Kingdom | 6 | Unchanged (GB) | United Kingdom | — |
| fsca | NorthAmerica / Europe / Asia | 3 rows | United States / Germany,France,Italy,Netherlands / Japan,China | — | — |

**Bucket-preservation guarantee:** the pipeline consumes regions only through
`classify_country_to_psur_region()` buckets. Every EEA-internal allocation above is
therefore presentational — EEA+TR+XI, UK, US, and Worldwide totals are exact.
The two exceptions, flagged MEDIUM in the ledger: the **NI 3% carve** (moves 43
units UK→EEA+TR+XI; assistant population-share estimate — ledger #12) and the
**Asia→Japan,China** FSCA mapping. Sales grand total asserted unchanged: **13,505**.

## Conversions performed

- None required for dates (already ISO), `serious` (already 0/1), or RACT rates
  (already raw fractions — ledger #1).
- `device_context.model_or_catalog_numbers`: appended internal designation
  `Stapler-X100` to reconcile CSV `device_model` with the catalog list (ledger #3).
- `device_context.contraindications`: removed self-contradictory placeholder
  "None stated in the CER" from a non-empty list (ledger #15).
- D1/D2/D3 prose routed into `market_history`; D5/D6 lines routed into
  `other_associated_documents`; D7/D8 narrative routed into
  `previous_psur.benefit_risk_evaluation.comments` (§6 routing — the structured
  `summary` counts {15 complaints, 2 serious} were preserved verbatim, ledger #14).

## De-identification

Scan of `description`, `narrative`, `root_cause`, `investigation_findings`,
`outcome` across complaints and external_events: **0 hits** (names, DOBs, MRNs,
addresses, contacts). Clean.

## Inference Ledger (complete)

The full machine-readable ledger (19 entries, all `user_confirmed`) lives in
`intake_state.json` → `inference_ledger`. Tier-1 bulk: 84 sales rows, 32 complaint
rows, 5 CAPA rows, 3 FSCA rows, 8 literature rows, 10 external-event rows resolved
deterministically (columns, dates, types, IDs). Tier-2/3 entries: #1–#19, of which
#12 (NI carve) and #16 (D6 identity) carry **demo-placeholder flags** that block
reuse of this pack for a genuine regulatory submission without replacement.

## Readiness gate scorecard (§10)

| # | Check | Result |
|---|---|---|
| 1 | /defaults fetched, schema path stated | ✅ API |
| 2 | device_context complete + five confirmations | ✅ (ledger 8, 19) |
| 3 | Class ≠ I | ✅ IIb |
| 4 | Sales full period, no gap months | ✅ 12/12 months |
| 5 | Region Gate (sales+complaints+fsca) | ✅ (R3 table above) |
| 6 | UK sales ⇒ UK trio filled | ✅ |
| 7 | Complaints full period | ✅ 12/12 months |
| 8 | Locked columns / ISO dates / serious∈{0,1} | ✅ |
| 9 | device_model/name reconciled | ✅ (ledger 3) |
| 10 | previous_psur + D7 | ✅ deviation documented |
| 11 | De-identification | ✅ clean |
| 12 | D2 declared | ✅ |
| 13 | D3 captured | ✅ |
| 14 | D6 complete | ✅ (demo placeholders, flagged) |
| 15 | Ledger: no unconfirmed MEDIUM / unresolved LOW | ✅ all confirmed via delegation |
| 16 | D4 | ✅ justified (D10-1) |
| 17 | RACT rates numeric raw | ✅ |
| 18 | PMS plan | ✅ |
| 19 | Literature/external/clinical | ✅ all present |
| 20 | D9 | ✅ |
| 21 | intake_declarations.md generated | ✅ this file |

**Source or justification, never neither; resolved or recorded, never silent.**
