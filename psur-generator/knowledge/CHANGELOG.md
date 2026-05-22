# Knowledge Layer Changelog

All notable changes to the rule registry and skill manifests are recorded here.
The semver in `VERSION` increments according to:
  - MAJOR: rule semantics or schema changes that may break consumers
  - MINOR: new rules, new frameworks, new skills
  - PATCH: editorial fixes, citation corrections, agent_instruction wording

## 1.0.0 — 2026-05-14

Initial unified knowledge layer.

Frameworks loaded:
- EU_MDR (Reg 2017/745, Articles 83-87)
- MDCG_2022_21 (PSUR guidance, Annexes I-IV)
- UK_MDR_2024 (SI 2024/1368, Part 4A, Regs 44ZC-44ZR)
- IMDRF Adverse Event Terminology (Annex A, F)
- ISO_14971:2019
- RACT occurrence codes (O1-O5)
- FORMQAR_054 (CooperSurgical PSUR template Rev C)
- HOUSE (CooperSurgical drafting and fabrication-prevention conventions)

Skills enabled:
- psur-sales-aggregate
- psur-imdrf-classify
- psur-tables
- psur-validate
