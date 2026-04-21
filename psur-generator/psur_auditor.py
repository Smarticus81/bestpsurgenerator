"""LLM-powered PSUR Auditor — audits .docx PSURs and in-memory PSUR JSON dicts
against EU MDR MDCG 2022-21 and UK MDR requirements.

Both EU MDR (MDCG 2022-21) and UK MDR requirements are ALWAYS applied. This
mirrors regulatory reality: any device marketed in both jurisdictions must
satisfy both frameworks simultaneously.

Two-pass architecture:
  1. **Keyword pre-screen** — fast regex / keyword checks for each requirement.
  2. **LLM deep-analysis** — sends section text + requirement description to the
     LLM for nuanced compliance assessment. Batched per PSUR section to minimise
     API calls.

Integration modes:
  - **Pipeline audit** (``run_json_audit``): Audits in-memory PSUR JSON during
    generation and returns per-section remediation instructions so the
    orchestrator can fix gaps iteratively.
  - **Post-hoc DOCX audit** (``run_audit``): Audits a rendered .docx file.
  - **Standalone CLI**: ``python psur_auditor.py path/to/PSUR.docx``
"""

from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from docx import Document  # python-docx
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# ---------------------------------------------------------------------------
# Project imports
# ---------------------------------------------------------------------------
from config import MODEL
from llm_client import create_message

console = Console()

# ═══════════════════════════════════════════════════════════════════════════
# Data models
# ═══════════════════════════════════════════════════════════════════════════


class ComplianceStatus(str, Enum):
    """Traffic-light compliance status."""
    COMPLIANT = "COMPLIANT"
    PARTIAL = "PARTIAL"
    GAP = "GAP"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class Framework(str, Enum):
    EU_MDR = "EU_MDR"
    UK_MDR = "UK_MDR"
    BOTH = "BOTH"


class Criticality(str, Enum):
    CRITICAL = "CRITICAL"
    MAJOR = "MAJOR"
    MINOR = "MINOR"


@dataclass
class Requirement:
    """Single auditable requirement."""
    req_id: str
    title: str
    description: str
    section: str              # PSUR section letter (A–M) or meta
    framework: Framework
    criticality: Criticality
    keywords: List[str]       # fast pre-screen keywords
    guidance_ref: str = ""    # e.g. "MDCG 2022-21 Annex I"


@dataclass
class AuditFinding:
    """Result for one requirement."""
    req_id: str
    title: str
    status: ComplianceStatus
    evidence: str             # quote or summary supporting status
    recommendation: str       # what to fix
    section: str
    framework: str
    criticality: str
    llm_assessed: bool = False


@dataclass
class AuditReport:
    """Full audit output."""
    psur_path: str
    audit_timestamp: str
    total_requirements: int = 0
    compliant: int = 0
    partial: int = 0
    gap: int = 0
    not_applicable: int = 0
    compliance_score: float = 0.0
    findings: List[AuditFinding] = field(default_factory=list)
    llm_summary: str = ""
    uk_mdr_enabled: bool = False
    llm_enabled: bool = True
    token_usage: Dict[str, int] = field(default_factory=lambda: {"input": 0, "output": 0})

    def compute_score(self) -> None:
        self.compliant = sum(1 for f in self.findings if f.status == ComplianceStatus.COMPLIANT)
        self.partial = sum(1 for f in self.findings if f.status == ComplianceStatus.PARTIAL)
        self.gap = sum(1 for f in self.findings if f.status == ComplianceStatus.GAP)
        self.not_applicable = sum(1 for f in self.findings if f.status == ComplianceStatus.NOT_APPLICABLE)
        self.total_requirements = len(self.findings)
        scorable = self.total_requirements - self.not_applicable
        if scorable > 0:
            self.compliance_score = round(
                (self.compliant + 0.5 * self.partial) / scorable * 100, 1
            )


# ═══════════════════════════════════════════════════════════════════════════
# Requirements checklist builder
# ═══════════════════════════════════════════════════════════════════════════

def build_requirements_checklist(*, include_uk: bool = True) -> List[Requirement]:
    """Build the full requirements checklist (EU MDR + optional UK MDR)."""
    reqs: List[Requirement] = []

    def _add(req_id, title, desc, section, fw, crit, kws, ref=""):
        reqs.append(Requirement(req_id, title, desc, section, fw, crit, kws, ref))

    # ── Section A: Executive Summary ──────────────────────────────────
    _add("A.01", "Executive summary present",
         "PSUR must contain an executive summary per MDCG 2022-21 Annex I.",
         "A", Framework.EU_MDR, Criticality.CRITICAL,
         ["executive summary", "summary"],
         "MDCG 2022-21 Annex I")
    _add("A.02", "Benefit-risk conclusion statement",
         "Executive summary must include a clear and bold statement on whether the benefit-risk profile has been adversely impacted or remains unchanged.",
         "A", Framework.EU_MDR, Criticality.CRITICAL,
         ["benefit-risk", "benefit risk", "unchanged", "adversely impacted", "not been adversely"],
         "MDCG 2022-21 Annex I")
    _add("A.03", "Previous PSUR actions status",
         "Executive summary must describe the status of actions taken based on the previous PSUR.",
         "A", Framework.EU_MDR, Criticality.MAJOR,
         ["previous psur", "prior psur", "actions taken", "prior period", "previous period"],
         "MDCG 2022-21 Annex I")
    _add("A.04", "Notified Body actions status",
         "Description of actions taken by the Notified Body from review of the previous PSUR.",
         "A", Framework.EU_MDR, Criticality.MAJOR,
         ["notified body", "nb review", "nb actions", "nb findings"],
         "MDCG 2022-21 Annex I")
    _add("A.05", "Data collection period stated",
         "Executive summary must state the data collection (surveillance) period.",
         "A", Framework.EU_MDR, Criticality.CRITICAL,
         ["data collection period", "surveillance period", "reporting period"],
         "MDCG 2022-21 Annex I")
    _add("A.06", "Period change justification",
         "If the data collection period changed from the previous PSUR, provide justification and a statement on comparability.",
         "A", Framework.EU_MDR, Criticality.MAJOR,
         ["period change", "comparability", "changed from"],
         "MDCG 2022-21 Annex I")
    _add("A.07", "Main results summarised",
         "Executive summary should summarise the main results from the current PSUR.",
         "A", Framework.EU_MDR, Criticality.MAJOR,
         ["main results", "key findings", "main findings", "overall conclusion"],
         "MDCG 2022-21 Annex I")

    # ── Section B: Scope & Device Description ─────────────────────────
    _add("B.01", "Device identification",
         "Device trade name(s), Basic UDI-DI(s), EMDN code, and classification rule must be present.",
         "B", Framework.EU_MDR, Criticality.CRITICAL,
         ["udi", "basic udi-di", "emdn", "trade name", "classification", "device name"],
         "MDCG 2022-21 Annex I — Art 86.1")
    _add("B.02", "Intended purpose stated",
         "Intended purpose per IFU including indications, contra-indications, and target populations.",
         "B", Framework.EU_MDR, Criticality.CRITICAL,
         ["intended purpose", "intended use", "indication", "contra-indication", "target population"],
         "MDCG 2022-21 Annex I — Art 86.1")
    _add("B.03", "Device status declared",
         "Status of the device: on market, no longer placed on market, recalled, or FSCA initiated.",
         "B", Framework.EU_MDR, Criticality.MAJOR,
         ["on the market", "market status", "placed on the market", "recalled"],
         "MDCG 2022-21 Annex I")
    _add("B.04", "Grouping justification",
         "If multiple devices are grouped in one PSUR, the grouping must be justified with a leading device defined.",
         "B", Framework.EU_MDR, Criticality.MAJOR,
         ["grouping", "leading device", "device group", "device family"],
         "MDCG 2022-21 Section 4")
    _add("B.05", "CE marking / certification date",
         "Date of first CE marking, declaration of conformity, or EU certificate must be stated.",
         "B", Framework.EU_MDR, Criticality.MAJOR,
         ["ce mark", "ce-mark", "certificate", "declaration of conformity", "certification date"],
         "MDCG 2022-21 Annex I — MDR devices")

    # ── Section C: Volume of Sales / Population Exposure ──────────────
    _add("C.01", "Volume of sales data present",
         "Sales or distribution volume data must be provided as the denominator for rate calculations.",
         "C", Framework.EU_MDR, Criticality.CRITICAL,
         ["volume of sales", "units sold", "units distributed", "units shipped", "sales data"],
         "MDCG 2022-21 Annex I — Art 86.1, Annex II Table 1")
    _add("C.02", "Counting method stated",
         "The counting method (units distributed, units shipped, procedures, etc.) must be explicitly stated and consistent.",
         "C", Framework.EU_MDR, Criticality.CRITICAL,
         ["counting method", "denominator", "units distributed", "procedures performed", "counting criteria"],
         "MDCG 2022-21 Annex II")
    _add("C.03", "Year-to-year comparison",
         "Sales data must be presented year-to-year to enable trend detection per Annex II Table 1.",
         "C", Framework.EU_MDR, Criticality.MAJOR,
         ["year-to-year", "year to year", "yoy", "annual comparison", "reporting day"],
         "MDCG 2022-21 Annex II Table 1, Annex III")
    _add("C.04", "Regional breakdown",
         "Data should be split by region: EEA+TR+XI and Worldwide per Annex III.",
         "C", Framework.EU_MDR, Criticality.MAJOR,
         ["region", "eea", "worldwide", "eu", "europe", "regional breakdown", "by region"],
         "MDCG 2022-21 Annex III")
    _add("C.05", "Population exposure estimated",
         "Estimated patient exposure independent of sales volume where applicable.",
         "C", Framework.EU_MDR, Criticality.MAJOR,
         ["population exposure", "patient exposure", "number of patients", "patient population"],
         "MDCG 2022-21 Annex I — Art 86.1")
    _add("C.06", "Denominator consistency",
         "The same denominator type must be used consistently throughout all PSUR sections.",
         "C", Framework.EU_MDR, Criticality.CRITICAL,
         ["consistent", "denominator", "same denominator", "rate calculation"],
         "MDCG 2022-21 Annex III")

    # ── Section D: Serious Incidents ──────────────────────────────────
    _add("D.01", "Serious incidents characterised",
         "Serious incidents must be characterised from three perspectives: device problem, root cause, and health effect.",
         "D", Framework.EU_MDR, Criticality.CRITICAL,
         ["serious incident", "device problem", "root cause", "health effect", "health impact"],
         "MDCG 2022-21 Annex I — Art 87, Annex III")
    _add("D.02", "IMDRF coding used",
         "Incidents must use IMDRF AET terminology: Annex A (device problem), Annex C (investigation findings), Annex F (health impact).",
         "D", Framework.EU_MDR, Criticality.CRITICAL,
         ["imdrf", "annex a", "annex c", "annex f", "adverse event terminology", "device problem code"],
         "MDCG 2022-21 Annex III")
    _add("D.03", "Incident rates provided",
         "Both absolute figures and rates of serious incidents must be reported.",
         "D", Framework.EU_MDR, Criticality.MAJOR,
         ["incident rate", "rate of serious", "absolute", "per unit", "complaint rate"],
         "MDCG 2022-21 Annex II Tables 4-6")

    # ── Section E: Customer Feedback ──────────────────────────────────
    _add("E.01", "Customer complaints summarised",
         "All non-serious complaints and user feedback must be presented.",
         "E", Framework.EU_MDR, Criticality.MAJOR,
         ["customer feedback", "user feedback", "complaint", "non-serious"],
         "MDCG 2022-21 Annex I — Annex III, Annex XIV Part B")
    _add("E.02", "Complaint grouping with IMDRF codes",
         "Complaints grouped by IMDRF AET Annex A codes (device problem) with both codes and terms.",
         "E", Framework.EU_MDR, Criticality.MAJOR,
         ["imdrf", "annex a", "device problem", "complaint group", "complaint categor"],
         "MDCG 2022-21 Annex I")
    _add("E.03", "Occurrence rates reported",
         "Complaint occurrence rates with reference denominator.",
         "E", Framework.EU_MDR, Criticality.MAJOR,
         ["occurrence rate", "complaint rate", "rate per", "per unit"],
         "MDCG 2022-21 Annex I")
    _add("E.04", "CAPA linkage from complaints",
         "Information on whether presented complaints led to initiation of CAPA.",
         "E", Framework.EU_MDR, Criticality.MAJOR,
         ["capa", "corrective action", "preventive action", "capa initiation"],
         "MDCG 2022-21 Annex I")
    _add("E.05", "Exclusion justification",
         "Justification for inclusion and exclusion of complaint groups.",
         "E", Framework.EU_MDR, Criticality.MINOR,
         ["exclusion", "justification", "excluded", "not included", "included"],
         "MDCG 2022-21 Annex I")

    # ── Section F: Complaint Types, Counts, Rates ─────────────────────
    _add("F.01", "Complaint data with IMDRF Annex A",
         "Comprehensive complaint data grouped by IMDRF Annex A codes.",
         "F", Framework.EU_MDR, Criticality.CRITICAL,
         ["imdrf", "annex a", "complaint type", "complaint code", "device problem"],
         "MDCG 2022-21 Annex I, Annex II Tables 4-6")
    _add("F.02", "Complaint rates vs RACT thresholds",
         "Actual complaint rates compared against maximum expected rates from the RACT.",
         "F", Framework.EU_MDR, Criticality.CRITICAL,
         ["ract", "maximum expected", "threshold", "risk acceptance", "acceptable rate", "upper control"],
         "MDCG 2022-21 Annex III")
    _add("F.03", "Year-to-year complaint trending",
         "Year-to-year comparison of complaint rates enabling trend identification.",
         "F", Framework.EU_MDR, Criticality.MAJOR,
         ["year-to-year", "trend", "trending", "annual comparison", "rate change"],
         "MDCG 2022-21 Annex III")
    _add("F.04", "Regional complaint data",
         "Complaint data split by region (EEA/Worldwide) where applicable.",
         "F", Framework.EU_MDR, Criticality.MAJOR,
         ["region", "eea", "worldwide", "by region"],
         "MDCG 2022-21 Annex III")
    _add("F.05", "All IMDRF categories accounted",
         "All IMDRF categories must be addressed, not just the top categories.",
         "F", Framework.EU_MDR, Criticality.MAJOR,
         ["all categor", "complete", "comprehensive", "each code", "all codes"],
         "MDCG 2022-21 Annex III assessment")

    # ── Section G: Trend Reporting ────────────────────────────────────
    _add("G.01", "Trend monitoring methodology described",
         "Description of statistical trend monitoring methodology (SPC, UCL/LCL).",
         "G", Framework.EU_MDR, Criticality.CRITICAL,
         ["trend monitoring", "trend report", "spc", "statistical process control", "methodology"],
         "MDCG 2022-21 Annex I — Art 88")
    _add("G.02", "UCL/LCL control limits defined",
         "Upper and Lower Control Limits clearly stated with calculation basis.",
         "G", Framework.EU_MDR, Criticality.CRITICAL,
         ["ucl", "lcl", "control limit", "upper control", "lower control"],
         "MDCG 2022-21 Annex I — Art 88")
    _add("G.03", "UCL breach investigation",
         "Any UCL breaches must be identified and investigated.",
         "G", Framework.EU_MDR, Criticality.CRITICAL,
         ["breach", "exceedance", "exceeded", "above ucl", "above the upper"],
         "MDCG 2022-21 Annex I — Art 88")
    _add("G.04", "Comparison with previous period",
         "Comparison of trend findings with the previous reporting period.",
         "G", Framework.EU_MDR, Criticality.MAJOR,
         ["previous period", "prior period", "comparison", "compared to prior", "previous psur"],
         "MDCG 2022-21 Annex I — Art 88")
    _add("G.05", "Trend reports to authorities",
         "Any formal trend reports submitted to regulatory authorities during the period.",
         "G", Framework.EU_MDR, Criticality.MAJOR,
         ["trend report", "submitted to", "regulatory", "competent authority", "article 88"],
         "MDCG 2022-21 Annex I — Art 88")
    _add("G.06", "Benefit-risk linkage from trends",
         "Trend analysis linked to the overall benefit-risk determination.",
         "G", Framework.EU_MDR, Criticality.MAJOR,
         ["benefit-risk", "benefit risk", "impact on", "risk profile"],
         "MDCG 2022-21 Annex I")

    # ── Section H: FSCA ───────────────────────────────────────────────
    _add("H.01", "FSCA summary present",
         "Summary of all Field Safety Corrective Actions per Annex II Table 7.",
         "H", Framework.EU_MDR, Criticality.CRITICAL,
         ["fsca", "field safety", "corrective action", "recall", "safety notice"],
         "MDCG 2022-21 Annex I — Art 87, Annex II Table 7")
    _add("H.02", "FSCA details complete",
         "Each FSCA must include: type, date, scope, status, ref number, rationale, impacted regions.",
         "H", Framework.EU_MDR, Criticality.CRITICAL,
         ["type of action", "issuing date", "scope", "status", "reference number", "impacted region"],
         "MDCG 2022-21 Annex II Table 7")

    # ── Section I: CAPA ───────────────────────────────────────────────
    _add("I.01", "CAPA listing present",
         "All relevant CAPAs listed with type, date, scope, status, ref, description, root cause, effectiveness.",
         "I", Framework.EU_MDR, Criticality.CRITICAL,
         ["capa", "corrective", "preventive", "root cause", "effectiveness"],
         "MDCG 2022-21 Annex I — Art 83(4), Annex II Table 8")
    _add("I.02", "CAPA effectiveness documented",
         "Closed CAPAs must have effectiveness documented (resolved/not resolved).",
         "I", Framework.EU_MDR, Criticality.MAJOR,
         ["effectiveness", "closed", "resolved", "not resolved", "verification"],
         "MDCG 2022-21 Annex II Table 8")

    # ── Section J: Literature Review ──────────────────────────────────
    _add("J.01", "Literature search described",
         "Systematic search strategy, databases searched, and inclusion/exclusion criteria.",
         "J", Framework.EU_MDR, Criticality.MAJOR,
         ["literature", "search strategy", "pubmed", "medline", "database search", "systematic"],
         "MDCG 2022-21 Annex I — Annex III, Annex XIV Part B")
    _add("J.02", "Findings assessed for safety impact",
         "Literature findings assessed for relevance to device safety and performance.",
         "J", Framework.EU_MDR, Criticality.MAJOR,
         ["safety", "performance", "clinical", "findings", "relevant", "assessment"],
         "MDCG 2022-21 Annex I")
    _add("J.03", "Comparison with similar devices",
         "Performance and safety compared with similar devices with the same intended purpose.",
         "J", Framework.EU_MDR, Criticality.MAJOR,
         ["similar device", "equivalent", "comparator", "same intended", "comparison"],
         "MDCG 2022-21 Annex III")

    # ── Section K: External Databases & Registries ────────────────────
    _add("K.01", "External databases reviewed",
         "Active surveillance of external regulatory databases (MAUDE, BfArM, etc.).",
         "K", Framework.EU_MDR, Criticality.MAJOR,
         ["maude", "bfarm", "external database", "registry", "eudamed"],
         "MDCG 2022-21 Annex I — Annex III, Annex XIV Part B")
    _add("K.02", "Findings from similar devices",
         "Publicly available information from other manufacturers of similar devices.",
         "K", Framework.EU_MDR, Criticality.MAJOR,
         ["similar device", "publicly available", "sscp", "cochrane", "other manufacturer"],
         "MDCG 2022-21 Annex I")
    _add("K.03", "New risks from external data",
         "Any new risks identified from external data sources must be discussed.",
         "K", Framework.EU_MDR, Criticality.MAJOR,
         ["new risk", "emerging risk", "safety signal", "identified from external"],
         "MDCG 2022-21 Annex I")

    # ── Section L: PMCF ───────────────────────────────────────────────
    _add("L.01", "PMCF activities summarised",
         "Summary of PMCF activities and findings per Annex XIV Part B.",
         "L", Framework.EU_MDR, Criticality.MAJOR,
         ["pmcf", "post-market clinical", "clinical follow-up", "pmcf plan", "pmcf evaluation"],
         "MDCG 2022-21 Annex I — Art 86, Annex XIV Part B")

    # ── Section M: Findings & Conclusions ─────────────────────────────
    _add("M.01", "Overall benefit-risk determination",
         "Final benefit-risk conclusion synthesising all data from all prior sections.",
         "M", Framework.EU_MDR, Criticality.CRITICAL,
         ["benefit-risk", "benefit risk", "overall conclusion", "risk profile", "unchanged"],
         "MDCG 2022-21 Annex I — Summary")
    _add("M.02", "Data limitations acknowledged",
         "Identify limitations to collected data and whether they impact conclusions.",
         "M", Framework.EU_MDR, Criticality.MAJOR,
         ["limitation", "bias", "reduced sales", "data quality", "enrollment"],
         "MDCG 2022-21 Annex I")
    _add("M.03", "New/emerging risks identified",
         "Outline any new or emerging risks identified during the reporting period.",
         "M", Framework.EU_MDR, Criticality.CRITICAL,
         ["new risk", "emerging risk", "safety signal", "newly identified"],
         "MDCG 2022-21 Annex I")
    _add("M.04", "Actions described",
         "Specific actions taken to address newly identified risks or poor performance.",
         "M", Framework.EU_MDR, Criticality.MAJOR,
         ["actions taken", "corrective", "preventive", "initiated", "addressed"],
         "MDCG 2022-21 Annex I — Art 83(3)")

    # ── Structural / Cover Page requirements ──────────────────────────
    _add("N.01", "Manufacturer identification",
         "Manufacturer name, address, and SRN present on cover page or Section B.",
         "N", Framework.EU_MDR, Criticality.CRITICAL,
         ["manufacturer", "srn", "single registration", "address"],
         "MDCG 2022-21 Annex I, Annex V")
    _add("O.01", "PSUR reference number",
         "Unique PSUR reference number assigned and present.",
         "O", Framework.EU_MDR, Criticality.MAJOR,
         ["reference number", "psur number", "psur ref", "document number"],
         "MDCG 2022-21 Terminology")

    # ── Data presentation (Annex III) ─────────────────────────────────
    _add("P.01", "IMDRF terminology used throughout",
         "IMDRF Adverse Event Terminology used consistently across relevant sections.",
         "P", Framework.EU_MDR, Criticality.MAJOR,
         ["imdrf", "adverse event terminology", "annex a", "annex f"],
         "MDCG 2022-21 Annex III")
    _add("P.02", "Stand-alone document",
         "PSUR can be assessed independently from supporting documentation (per MDCG guidance).",
         "P", Framework.EU_MDR, Criticality.MAJOR,
         ["stand-alone", "standalone", "independently", "self-contained"],
         "MDCG 2022-21 Core Principles")

    # ── Quality metrics ───────────────────────────────────────────────
    _add("Q.01", "Absent data justified",
         "If specific datasets are excluded, the manufacturer justifies the absence.",
         "Q", Framework.EU_MDR, Criticality.MAJOR,
         ["absent", "not applicable", "n/a", "not available", "justif", "excluded"],
         "MDCG 2022-21 Core Principles")
    _add("Q.02", "Consistent denominators",
         "Same denominator used across all sections for rate calculations.",
         "Q", Framework.EU_MDR, Criticality.CRITICAL,
         ["denominator", "consistent", "rate calculation", "per unit"],
         "MDCG 2022-21 Annex III")
    _add("Q.03", "Data compared across sources",
         "Findings from all datasets compared to identify conflicting results.",
         "Q", Framework.EU_MDR, Criticality.MAJOR,
         ["compared", "conflicting", "consistent with", "corroborat", "align"],
         "MDCG 2022-21 Annex III")
    _add("Q.04", "Table formats per Annex II",
         "Data tables follow the Annex II format guidance (Tables 1–8).",
         "Q", Framework.EU_MDR, Criticality.MINOR,
         ["table 1", "table 2", "table 3", "table 4", "table 5", "table 6", "table 7", "table 8", "annex ii"],
         "MDCG 2022-21 Annex II")

    # ── Regulatory references ─────────────────────────────────────────
    _add("R.01", "No prohibited regulation citations in narratives",
         "Narratives should not cite specific MDR Article X(Y) or MDCG XXXX patterns — compliance is embedded in the template structure.",
         "R", Framework.EU_MDR, Criticality.MINOR,
         [],  # This is checked by regex, not keyword
         "FormQAR-054 writing rules")
    _add("R.02", "No bullet points in narratives",
         "Narratives should use flowing prose without bullet points.",
         "R", Framework.EU_MDR, Criticality.MINOR,
         [],  # Checked structurally
         "FormQAR-054 writing rules")
    _add("R.03", "Revision and version tracking",
         "PSUR version number and revision history present.",
         "R", Framework.EU_MDR, Criticality.MINOR,
         ["version", "revision", "rev.", "version number"],
         "MDCG 2022-21 Terminology")

    # ── Cross-section coherence ───────────────────────────────────────
    _add("S.01", "FSCA-CAPA cross-reference",
         "FSCAs in Section H should cross-reference related CAPAs in Section I.",
         "S", Framework.EU_MDR, Criticality.MAJOR,
         ["fsca", "capa", "cross-reference", "section h", "section i"],
         "MDCG 2022-21 — assessment practice")
    _add("S.02", "Trend-incident linkage",
         "Trend findings in Section G should reference incidents from Section D.",
         "S", Framework.EU_MDR, Criticality.MAJOR,
         ["trend", "incident", "section d", "section g", "ucl", "breach"],
         "MDCG 2022-21 — assessment practice")
    _add("S.03", "Conclusion supported by sections",
         "Section M conclusions must be supported by evidence from all prior sections.",
         "S", Framework.EU_MDR, Criticality.CRITICAL,
         ["conclusion", "supported by", "evidence", "based on", "demonstrates"],
         "MDCG 2022-21 Annex I — Summary")

    # ── UK MDR 2024 Part 4A requirements (SI 2024/1368) (conditional) ──
    if include_uk:
        # --- 44ZE: PMS System ---
        _add("UK.01", "PMS system maintained",
             "Manufacturer must establish and maintain a post-market surveillance system "
             "that is proportionate to the risk class and appropriate to the type of device.",
             "S", Framework.UK_MDR, Criticality.CRITICAL,
             ["pms system", "post-market surveillance system", "proportionate", "risk class"],
             "UK MDR 2024 — Reg 44ZE")
        _add("UK.02", "PMS data analysis throughout lifetime",
             "The PMS system must include systematic collection and analysis of relevant "
             "data throughout the entire lifetime of the device.",
             "S", Framework.UK_MDR, Criticality.CRITICAL,
             ["pms", "data analysis", "lifetime", "systematic", "collection"],
             "UK MDR 2024 — Reg 44ZE")

        # --- 44ZF: PMS Plan ---
        _add("UK.03", "PMS plan documented",
             "A PMS plan must be documented in a clear, organised and searchable format. "
             "It must specify the device lifetime and include processes for collecting and "
             "analysing incidents, complaints, feedback and trends.",
             "S", Framework.UK_MDR, Criticality.CRITICAL,
             ["pms plan", "clear", "organised", "searchable", "device lifetime"],
             "UK MDR 2024 — Reg 44ZF")
        _add("UK.04", "PMS plan threshold values",
             "The PMS plan must define threshold values and indicators for risk reassessment "
             "including re-evaluation of benefit-risk determination.",
             "S", Framework.UK_MDR, Criticality.MAJOR,
             ["threshold", "indicator", "risk reassessment", "benefit-risk", "pms plan"],
             "UK MDR 2024 — Reg 44ZF")
        _add("UK.05", "PMS plan communication processes",
             "The PMS plan must include effective communication processes between the "
             "manufacturer, UK Responsible Person, approved body and Secretary of State.",
             "S", Framework.UK_MDR, Criticality.MAJOR,
             ["communication", "pms plan", "uk rp", "approved body", "secretary of state"],
             "UK MDR 2024 — Reg 44ZF")
        _add("UK.06", "PMS plan available within 3 working days",
             "The PMS plan must be provided to the Secretary of State within 3 working days "
             "of a request.",
             "S", Framework.UK_MDR, Criticality.CRITICAL,
             ["3 working days", "secretary of state", "pms plan", "request"],
             "UK MDR 2024 — Reg 44ZF")

        # --- 44ZG: Preventive and Corrective Actions ---
        _add("UK.07", "CAPA when risk identified",
             "The manufacturer must take necessary preventive and corrective action as soon "
             "as possible when a risk is identified or non-conformity is suspected, including "
             "field safety corrective actions where appropriate.",
             "I", Framework.UK_MDR, Criticality.CRITICAL,
             ["capa", "preventive", "corrective", "risk identified", "non-conformity"],
             "UK MDR 2024 — Reg 44ZG")
        _add("UK.08", "CAPA notification to UK RP and approved body",
             "The manufacturer must notify the UK Responsible Person, approved body and "
             "Secretary of State when undertaking field safety corrective actions.",
             "I", Framework.UK_MDR, Criticality.CRITICAL,
             ["notify", "uk rp", "approved body", "secretary of state", "fsca"],
             "UK MDR 2024 — Reg 44ZG")

        # --- 44ZH: Initial Reporting of Serious Incidents ---
        _add("UK.09", "Serious incident reporting to Secretary of State",
             "Serious incidents must be reported to the Secretary of State. Default timeline "
             "is 15 days; 2 days for serious public health threats; 10 days for death or "
             "unanticipated serious deterioration in state of health.",
             "D", Framework.UK_MDR, Criticality.CRITICAL,
             ["serious incident", "secretary of state", "15 days", "2 days", "10 days",
              "public health threat", "death", "serious deterioration"],
             "UK MDR 2024 — Reg 44ZH")
        _add("UK.10", "Serious incident report content — manufacturer details",
             "Initial serious incident report must include manufacturer details and UK "
             "Responsible Person details.",
             "D", Framework.UK_MDR, Criticality.MAJOR,
             ["manufacturer details", "uk rp", "uk responsible person", "incident report"],
             "UK MDR 2024 — Reg 44ZH")
        _add("UK.11", "Serious incident report content — device and incident",
             "Initial serious incident report must include device description, incident "
             "description, preliminary conclusions and whether FSCA is being considered.",
             "D", Framework.UK_MDR, Criticality.MAJOR,
             ["device description", "incident description", "preliminary conclusions",
              "fsca", "initial report"],
             "UK MDR 2024 — Reg 44ZH")

        # --- 44ZI: Investigation and Final Reporting ---
        _add("UK.12", "Incident investigation and risk analysis review",
             "The manufacturer must investigate each serious incident, review risk analysis "
             "in light of findings and submit a final report detailing investigation methods "
             "and conclusions.",
             "D", Framework.UK_MDR, Criticality.CRITICAL,
             ["investigate", "risk analysis", "final report", "methods", "conclusions"],
             "UK MDR 2024 — Reg 44ZI")
        _add("UK.13", "Final report — similar incidents and FSCA",
             "The final incident report must include consideration of FSCA, details of any "
             "similar incidents and cooperation with the Secretary of State investigation.",
             "D", Framework.UK_MDR, Criticality.MAJOR,
             ["final report", "similar incidents", "fsca", "cooperate", "secretary of state"],
             "UK MDR 2024 — Reg 44ZI")

        # --- 44ZJ: FSCA and Field Safety Notices ---
        _add("UK.14", "FSCA risk assessment and initial report",
             "Before undertaking a field safety corrective action the manufacturer must "
             "produce a risk assessment and submit an initial report together with the "
             "proposed field safety notice to the Secretary of State.",
             "H", Framework.UK_MDR, Criticality.CRITICAL,
             ["fsca", "risk assessment", "initial report", "field safety notice",
              "secretary of state"],
             "UK MDR 2024 — Reg 44ZJ")
        _add("UK.15", "Field safety notice content",
             "The field safety notice must identify affected devices with UDI, explain the "
             "reasons for the FSCA and describe actions to be taken by users.",
             "H", Framework.UK_MDR, Criticality.CRITICAL,
             ["field safety notice", "udi", "reasons", "user actions", "identify devices"],
             "UK MDR 2024 — Reg 44ZJ")
        _add("UK.16", "FSCA final report with effectiveness evidence",
             "A final report on the FSCA must be submitted to the Secretary of State "
             "including the outcome and evidence of effectiveness of the corrective action.",
             "H", Framework.UK_MDR, Criticality.MAJOR,
             ["fsca", "final report", "outcome", "effectiveness", "evidence"],
             "UK MDR 2024 — Reg 44ZJ")

        # --- 44ZK: FSCA Outside GB ---
        _add("UK.17", "FSCA outside GB reported",
             "Any field safety corrective action taken outside Great Britain for the same "
             "device model must be reported to the Secretary of State, including a "
             "justification if the same FSCA is not being taken in GB.",
             "H", Framework.UK_MDR, Criticality.CRITICAL,
             ["fsca", "outside gb", "outside great britain", "justification",
              "same device model"],
             "UK MDR 2024 — Reg 44ZK")

        # --- 44ZL: Post-Market Surveillance Report (PMSR) ---
        _add("UK.18", "PMSR for Class I / IVD Class A-B",
             "For Class I and IVD Class A-B devices a post-market surveillance report must "
             "be produced within 3 years of placing the device on the UK market and updated "
             "at least every 3 years. It must include a summary of PMS results and "
             "conclusions and a description of preventive and corrective actions taken.",
             "M", Framework.UK_MDR, Criticality.CRITICAL,
             ["pmsr", "class i", "ivd class a", "ivd class b", "3 years",
              "pms results", "corrective actions"],
             "UK MDR 2024 — Reg 44ZL")

        # --- 44ZM: PSUR ---
        _add("UK.19", "UK PSUR required for higher-risk classes",
             "A periodic safety update report is required for Class IIa, IIb, III and IVD "
             "Class C-D devices. The first PSUR must be produced within 1 year of placing "
             "the device on the UK market (2 years for Class IIa) and updated annually "
             "(biennially for Class IIa).",
             "M", Framework.UK_MDR, Criticality.CRITICAL,
             ["psur", "class iia", "class iib", "class iii", "ivd class c", "ivd class d",
              "1 year", "2 years", "annually", "biennially"],
             "UK MDR 2024 — Reg 44ZM")
        _add("UK.20", "UK PSUR content — market and population data",
             "The UK PSUR must include the number of devices placed on the UK market, "
             "population characteristics, estimates of population size in the UK and outside "
             "the UK and usage frequency.",
             "C", Framework.UK_MDR, Criticality.CRITICAL,
             ["uk market", "population", "usage frequency", "number of devices",
              "uk", "great britain"],
             "UK MDR 2024 — Reg 44ZM")
        _add("UK.21", "UK PSUR content — PMS results and risk analysis",
             "The UK PSUR must include a summary of PMS results and conclusions, preventive "
             "and corrective actions taken, risk analysis outcomes and PMCF conclusions.",
             "M", Framework.UK_MDR, Criticality.CRITICAL,
             ["pms results", "risk analysis", "pmcf", "corrective actions", "conclusions"],
             "UK MDR 2024 — Reg 44ZM")
        _add("UK.22", "UK PSUR submitted to approved body",
             "The PSUR must be submitted to the approved body responsible for the device.",
             "M", Framework.UK_MDR, Criticality.MAJOR,
             ["approved body", "submit", "psur", "uk"],
             "UK MDR 2024 — Reg 44ZM")

        # --- 44ZN: Trend Reporting ---
        _add("UK.23", "Trend reporting — statistically significant increase",
             "Any statistically significant increase in frequency or severity of incidents "
             "that could adversely impact the risk analysis must be reported to the Secretary "
             "of State. Statistical methodology must align with the PMS plan.",
             "G", Framework.UK_MDR, Criticality.CRITICAL,
             ["trend", "statistically significant", "frequency", "severity",
              "secretary of state", "risk analysis", "mhra"],
             "UK MDR 2024 — Reg 44ZN")
        _add("UK.24", "Trend reporting — IVD erroneous results",
             "For IVD devices any statistically significant increase in expected erroneous "
             "results must also be reported and investigated with a final report submitted.",
             "G", Framework.UK_MDR, Criticality.MAJOR,
             ["ivd", "erroneous results", "trend", "investigation", "final report"],
             "UK MDR 2024 — Reg 44ZN")

        # --- 44ZQ: Retention ---
        _add("UK.25", "PMS documentation retention period",
             "All PMS documentation must be retained for the PMS period or 15 years for "
             "implantable devices and 10 years for all other devices, whichever is longer.",
             "S", Framework.UK_MDR, Criticality.CRITICAL,
             ["retention", "15 years", "10 years", "implantable", "pms documentation"],
             "UK MDR 2024 — Reg 44ZQ")

        # --- 44ZR: Requests ---
        _add("UK.26", "Documentation available within 3 working days",
             "All PMS documentation must be provided to the Secretary of State within 3 "
             "working days of a request.",
             "S", Framework.UK_MDR, Criticality.CRITICAL,
             ["3 working days", "request", "secretary of state", "documentation"],
             "UK MDR 2024 — Reg 44ZR")

        # --- Cross-cutting: UK RP and UKCA (derived from Part 4A obligations) ---
        _add("UK.27", "UK Responsible Person identified",
             "If the device is marketed in the UK the UK Responsible Person must be clearly "
             "stated in the PSUR scope section.",
             "B", Framework.UK_MDR, Criticality.CRITICAL,
             ["uk responsible person", "ukrp", "uk rp", "uk representative"],
             "UK MDR 2024 — Reg 44ZG/44ZH")
        _add("UK.28", "UKCA marking status",
             "UKCA marking or UK conformity assessment status must be referenced for devices "
             "placed on the GB market.",
             "B", Framework.UK_MDR, Criticality.MAJOR,
             ["ukca", "uk conformity", "uk marking", "gb market"],
             "UK MDR 2024 — Part 4A general")

        # --- Complaint / feedback (derived from 44ZF PMS plan scope) ---
        _add("UK.29", "UK complaint data identifiable",
             "Complaints originating from the UK market must be separately identifiable "
             "within the PSUR complaint analysis.",
             "F", Framework.UK_MDR, Criticality.MAJOR,
             ["uk", "complaint", "gb", "united kingdom", "uk market"],
             "UK MDR 2024 — Reg 44ZF/44ZM")
        _add("UK.30", "UK customer feedback captured",
             "Customer feedback from UK users must be captured as part of the PMS system "
             "and reflected in the PSUR.",
             "E", Framework.UK_MDR, Criticality.MAJOR,
             ["uk", "feedback", "customer", "user", "gb", "united kingdom"],
             "UK MDR 2024 — Reg 44ZF")

    return reqs


# ═══════════════════════════════════════════════════════════════════════════
# PSUR Document Parser
# ═══════════════════════════════════════════════════════════════════════════

# Map PSUR section letters to likely heading patterns
_SECTION_HEADING_PATTERNS: Dict[str, List[str]] = {
    "A": [r"executive\s+summary"],
    "B": [r"scope", r"device\s+description", r"description\s+of.*device",
          r"intended\s+(purpose|use)"],
    "C": [r"volume\s+of\s+(sales|distribution)", r"population\s+exposure",
          r"sales\s+and\s+population"],
    "D": [r"serious\s+incident", r"vigilance", r"information\s+on\s+serious"],
    "E": [r"customer\s+feedback", r"user\s+feedback", r"feedback\s+and\s+complaint"],
    "F": [r"complaint\s+type", r"product\s+complaint", r"complaint\s+count",
          r"complaint\s+rate"],
    "G": [r"trend\s+report", r"trend\s+analysis", r"information\s+from\s+trend"],
    "H": [r"field\s+safety", r"fsca"],
    "I": [r"corrective\s+and\s+preventive", r"capa"],
    "J": [r"scientific\s+literature", r"literature\s+review"],
    "K": [r"external\s+database", r"registr", r"publicly\s+available"],
    "L": [r"post-?market\s+clinical", r"pmcf"],
    "M": [r"finding.*conclusion", r"overall\s+conclusion", r"benefit.*risk\s+(?:determination|conclusion)"],
}


class PSURParser:
    """Extract structured content from a PSUR .docx file."""

    def __init__(self, docx_path: str | Path) -> None:
        self.path = Path(docx_path)
        self.doc = Document(str(self.path))
        self.full_text = ""
        self.paragraphs: List[str] = []
        self.headings: List[Tuple[str, int]] = []  # (text, level)
        self.sections: Dict[str, str] = {}          # section_key → text
        self._parse()

    def _parse(self) -> None:
        """Extract paragraphs, headings, and build section map."""
        for para in self.doc.paragraphs:
            text = para.text.strip()
            if not text:
                continue
            self.paragraphs.append(text)
            style_name = (para.style.name or "").lower()
            if "heading" in style_name:
                try:
                    level = int(re.search(r"\d", style_name).group())
                except (AttributeError, ValueError):
                    level = 1
                self.headings.append((text, level))

        self.full_text = "\n".join(self.paragraphs)

        # Build section map keyed by PSUR section letter
        self._map_sections()

    def _map_sections(self) -> None:
        """Map content to PSUR sections A–M based on heading patterns."""
        # Combine all paragraph text with indices
        para_texts = self.paragraphs
        total = len(para_texts)

        # Find heading indices that match section patterns
        heading_positions: List[Tuple[str, int]] = []  # (section_key, para_index)
        for i, text in enumerate(para_texts):
            text_lower = text.lower()
            for sec_key, patterns in _SECTION_HEADING_PATTERNS.items():
                if any(re.search(p, text_lower) for p in patterns):
                    heading_positions.append((sec_key, i))
                    break

        # Sort by position
        heading_positions.sort(key=lambda x: x[1])

        # Extract text between heading positions
        for idx, (sec_key, start) in enumerate(heading_positions):
            end = heading_positions[idx + 1][1] if idx + 1 < len(heading_positions) else total
            section_text = "\n".join(para_texts[start:end])
            # If multiple headings map to same section, concatenate
            if sec_key in self.sections:
                self.sections[sec_key] += "\n" + section_text
            else:
                self.sections[sec_key] = section_text

    def get_section(self, key: str) -> str:
        """Get text for a PSUR section by letter (A–M)."""
        return self.sections.get(key.upper(), "")

    def search_text(self, pattern: str, case_sensitive: bool = False) -> List[str]:
        """Find all paragraphs matching a regex pattern."""
        flags = 0 if case_sensitive else re.IGNORECASE
        return [p for p in self.paragraphs if re.search(pattern, p, flags)]

    def has_tables(self) -> bool:
        return len(self.doc.tables) > 0

    def get_table_count(self) -> int:
        return len(self.doc.tables)

    def get_word_count(self) -> int:
        return len(self.full_text.split())

    def get_section_count(self) -> int:
        return len(self.sections)


# ═══════════════════════════════════════════════════════════════════════════
# Keyword pre-screen engine
# ═══════════════════════════════════════════════════════════════════════════

def _keyword_prescreen(
    req: Requirement,
    parser: PSURParser,
) -> Tuple[ComplianceStatus, str]:
    """Fast keyword/regex check. Returns (status, evidence_snippet)."""

    # --- Special structural checks ---
    if req.req_id == "R.01":
        # Check for prohibited regulation citations
        bad_patterns = [r"MDR\s+Article\s+\d+", r"MDCG\s+\d{4}"]
        hits = []
        for bp in bad_patterns:
            hits.extend(parser.search_text(bp))
        if hits:
            return ComplianceStatus.GAP, f"Prohibited citations found: {hits[0][:120]}"
        return ComplianceStatus.COMPLIANT, "No prohibited regulation citations detected."

    if req.req_id == "R.02":
        # Check for bullet points (lines starting with •, -, *)
        bullet_lines = [p for p in parser.paragraphs if re.match(r"^\s*[•\-\*]\s", p)]
        if len(bullet_lines) > 5:
            return ComplianceStatus.GAP, f"Found {len(bullet_lines)} bullet-point lines."
        elif bullet_lines:
            return ComplianceStatus.PARTIAL, f"Found {len(bullet_lines)} bullet-point lines."
        return ComplianceStatus.COMPLIANT, "No prohibited bullet points detected."

    if not req.keywords:
        return ComplianceStatus.PARTIAL, "No keywords defined for pre-screen; needs LLM analysis."

    # --- Normal keyword check ---
    # Determine search scope: section-specific or full document
    section_text = ""
    if req.section in _SECTION_HEADING_PATTERNS:
        section_text = parser.get_section(req.section)

    search_text = section_text if section_text else parser.full_text

    if not search_text:
        return ComplianceStatus.GAP, "Section not found in document."

    search_lower = search_text.lower()
    matched_kw = [kw for kw in req.keywords if kw.lower() in search_lower]
    match_ratio = len(matched_kw) / len(req.keywords) if req.keywords else 0

    if match_ratio >= 0.6:
        # Find first matching snippet
        for kw in matched_kw:
            idx = search_lower.find(kw.lower())
            if idx >= 0:
                start = max(0, idx - 60)
                end = min(len(search_text), idx + len(kw) + 60)
                snippet = search_text[start:end].replace("\n", " ")
                return ComplianceStatus.COMPLIANT, f"...{snippet}..."
        return ComplianceStatus.COMPLIANT, f"Keywords found: {', '.join(matched_kw)}"
    elif match_ratio > 0:
        snippet = f"Partial match ({len(matched_kw)}/{len(req.keywords)} keywords): {', '.join(matched_kw)}"
        return ComplianceStatus.PARTIAL, snippet
    else:
        return ComplianceStatus.GAP, f"None of the expected keywords found: {', '.join(req.keywords[:5])}"


# ═══════════════════════════════════════════════════════════════════════════
# LLM deep-analysis engine
# ═══════════════════════════════════════════════════════════════════════════

_LLM_SYSTEM_PROMPT = """\
You are an expert medical device regulatory auditor specialising in EU MDR \
(Regulation (EU) 2017/745) and MDCG 2022-21 guidance on PSURs. You also have \
deep knowledge of UK MDR post-market surveillance requirements.

You are auditing a PSUR (Periodic Safety Update Report) document. For each \
requirement presented, you will receive:
- The requirement ID, title, description, and regulatory reference
- The relevant section text extracted from the PSUR document

Your task: Assess whether the PSUR text satisfies the requirement. Return a \
JSON object with EXACTLY these fields:
{
  "status": "COMPLIANT" | "PARTIAL" | "GAP" | "NOT_APPLICABLE",
  "evidence": "<specific quote or summary from the text supporting your assessment — max 200 chars>",
  "recommendation": "<what the manufacturer should fix/add — max 200 chars, empty string if COMPLIANT>"
}

Rules:
- COMPLIANT: The requirement is fully addressed with adequate detail.
- PARTIAL: Some relevant content exists but is incomplete or lacks depth.
- GAP: The requirement is not addressed or critically missing.
- NOT_APPLICABLE: The requirement does not apply to the document context (e.g. no UK sales for UK requirements).
- Be precise. Quote specific text as evidence when possible.
- If the section text is empty, return GAP with evidence "Section not found in document."
- Return ONLY the JSON object, no other text.
"""


def _build_llm_audit_batch(
    requirements: List[Tuple[Requirement, ComplianceStatus, str]],
    parser: PSURParser,
    section_key: str,
) -> Optional[str]:
    """Build a single LLM prompt for auditing a batch of requirements for one section.

    Returns None if no requirements need LLM analysis.
    """
    # Only send requirements that are PARTIAL or GAP from keyword pre-screen
    needs_llm = [(r, kw_status, kw_evidence)
                 for r, kw_status, kw_evidence in requirements
                 if kw_status in (ComplianceStatus.PARTIAL, ComplianceStatus.GAP)]

    if not needs_llm:
        return None

    # Get section text (truncated for token budget)
    section_text = parser.get_section(section_key)
    if not section_text and section_key not in _SECTION_HEADING_PATTERNS:
        # For meta-sections (N, O, P, Q, R, S), use full text (truncated)
        section_text = parser.full_text

    if not section_text:
        section_text = "(Section not found in the document)"

    # Truncate to ~8000 chars to stay within token budget
    max_chars = 8000
    if len(section_text) > max_chars:
        section_text = section_text[:max_chars] + "\n... [truncated]"

    # Build requirement list
    req_items = []
    for r, kw_status, kw_evidence in needs_llm:
        req_items.append(
            f"- **{r.req_id}** ({r.title}): {r.description}\n"
            f"  Reference: {r.guidance_ref}\n"
            f"  Pre-screen result: {kw_status.value} — {kw_evidence}"
        )

    prompt = (
        f"## PSUR Section Text\n\n{section_text}\n\n"
        f"---\n\n"
        f"## Requirements to Audit ({len(needs_llm)})\n\n"
        + "\n".join(req_items)
        + "\n\n---\n\n"
        f"For EACH requirement above, provide your assessment as a JSON array "
        f"of objects (one per requirement, in order). Each object must have "
        f"exactly: {{\"req_id\": \"...\", \"status\": \"...\", \"evidence\": \"...\", \"recommendation\": \"...\"}}\n\n"
        f"Return ONLY the JSON array."
    )
    return prompt


def _parse_llm_response(
    response_text: str,
    requirement_ids: List[str],
) -> Dict[str, Dict[str, str]]:
    """Parse LLM response JSON array into a dict keyed by req_id."""
    results: Dict[str, Dict[str, str]] = {}

    # Extract JSON array using bracket matching
    text = response_text.strip()
    start = text.find("[")
    if start == -1:
        # Try to parse as single object
        start = text.find("{")
        if start == -1:
            return results
        # Wrap in array
        text = "[" + text[start:] + "]"
    else:
        text = text[start:]

    # Find matching closing bracket
    depth = 0
    end = -1
    for i, ch in enumerate(text):
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end > 0:
        text = text[:end]

    try:
        items = json.loads(text)
        if isinstance(items, list):
            for item in items:
                rid = item.get("req_id", "")
                if rid:
                    results[rid] = {
                        "status": item.get("status", "PARTIAL"),
                        "evidence": item.get("evidence", ""),
                        "recommendation": item.get("recommendation", ""),
                    }
        elif isinstance(items, dict):
            rid = items.get("req_id", requirement_ids[0] if requirement_ids else "")
            if rid:
                results[rid] = {
                    "status": items.get("status", "PARTIAL"),
                    "evidence": items.get("evidence", ""),
                    "recommendation": items.get("recommendation", ""),
                }
    except json.JSONDecodeError:
        pass

    return results


def _generate_audit_summary(
    report: AuditReport,
    parser: PSURParser,
) -> str:
    """Generate an LLM-powered executive summary of the audit findings."""
    # Build a concise summary of findings for the LLM
    gap_findings = [f for f in report.findings if f.status == ComplianceStatus.GAP]
    partial_findings = [f for f in report.findings if f.status == ComplianceStatus.PARTIAL]

    findings_text = ""
    if gap_findings:
        findings_text += "### Critical Gaps:\n"
        for f in gap_findings:
            findings_text += f"- {f.req_id} ({f.title}): {f.evidence}\n"
    if partial_findings:
        findings_text += "\n### Partial Compliance:\n"
        for f in partial_findings[:10]:  # Limit for token budget
            findings_text += f"- {f.req_id} ({f.title}): {f.evidence}\n"

    prompt = (
        f"You are a senior regulatory auditor writing the executive summary for a "
        f"PSUR compliance audit report.\n\n"
        f"## Audit Statistics\n"
        f"- Total requirements: {report.total_requirements}\n"
        f"- Compliant: {report.compliant}\n"
        f"- Partial: {report.partial}\n"
        f"- Gap: {report.gap}\n"
        f"- Not applicable: {report.not_applicable}\n"
        f"- Compliance score: {report.compliance_score}%\n"
        f"- UK MDR scope: {'Yes' if report.uk_mdr_enabled else 'No'}\n"
        f"- Document word count: {parser.get_word_count()}\n"
        f"- Sections identified: {parser.get_section_count()}\n"
        f"- Tables present: {parser.get_table_count()}\n\n"
        f"{findings_text}\n\n"
        f"Write a concise 3-5 paragraph executive summary of the audit findings. "
        f"Highlight the most critical gaps, areas of strength, and priority "
        f"recommendations. Use a professional regulatory tone. Do NOT use bullet "
        f"points. Return ONLY the narrative text."
    )

    try:
        resp = create_message(
            model=MODEL,
            max_tokens=1500,
            temperature=0.2,
            system="You are a senior medical device regulatory auditor.",
            messages=[{"role": "user", "content": prompt}],
        )
        report.token_usage["input"] += resp.usage.input_tokens
        report.token_usage["output"] += resp.usage.output_tokens
        return resp.content[0].text.strip()
    except Exception as e:
        return f"(LLM summary generation failed: {e})"


# ═══════════════════════════════════════════════════════════════════════════
# In-pipeline JSON audit engine  (for the generation loop)
# ═══════════════════════════════════════════════════════════════════════════

# Maps PSUR section keys (A_executive_summary, etc.) → requirement section
# letters used in the requirements checklist, PLUS cross-section meta-reqs.
_SECTION_KEY_TO_LETTERS: Dict[str, List[str]] = {
    "A_executive_summary":                           ["A"],
    "B_scope_and_device_description":                ["B"],
    "C_volume_of_sales_and_population_exposure":     ["C"],
    "D_information_on_serious_incidents":             ["D"],
    "E_customer_feedback":                           ["E"],
    "F_product_complaint_types_counts_and_rates":    ["F"],
    "G_information_from_trend_reporting":             ["G"],
    "H_information_from_fsca":                       ["H"],
    "I_corrective_and_preventive_actions":            ["I"],
    "J_scientific_literature_review":                 ["J"],
    "K_review_of_external_databases_and_registries":  ["K"],
    "L_pmcf":                                        ["L"],
    "M_findings_and_conclusions":                     ["M"],
}

# Requirements whose section letters map to meta-categories (N-S) are checked
# against the full PSUR only once, not per-section.
_META_SECTION_LETTERS = {"N", "O", "P", "Q", "R", "S"}


def _flatten_json_to_text(obj: Any, depth: int = 0) -> str:
    """Recursively flatten a JSON section dict into searchable text."""
    parts: List[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.startswith("_"):
                continue
            child_text = _flatten_json_to_text(v, depth + 1)
            if child_text.strip():
                parts.append(child_text)
    elif isinstance(obj, list):
        for item in obj:
            parts.append(_flatten_json_to_text(item, depth + 1))
    elif isinstance(obj, str) and obj.strip():
        parts.append(obj)
    elif isinstance(obj, (int, float)):
        parts.append(str(obj))
    return "\n".join(parts)


def _keyword_prescreen_json(
    req: Requirement,
    section_text: str,
    full_text: str,
) -> Tuple[ComplianceStatus, str]:
    """Keyword pre-screen against flattened JSON text (no PSURParser needed)."""
    # Special structural checks
    if req.req_id == "R.01":
        bad_patterns = [r"MDR\s+Article\s+\d+", r"MDCG\s+\d{4}"]
        hits = [m.group() for bp in bad_patterns
                for m in re.finditer(bp, full_text, re.IGNORECASE)]
        if hits:
            return ComplianceStatus.GAP, f"Prohibited citations found: {hits[0][:120]}"
        return ComplianceStatus.COMPLIANT, "No prohibited regulation citations detected."

    if req.req_id == "R.02":
        bullet_lines = re.findall(r"^\s*[•\-\*]\s", full_text, re.MULTILINE)
        if len(bullet_lines) > 5:
            return ComplianceStatus.GAP, f"Found {len(bullet_lines)} bullet-point lines."
        elif bullet_lines:
            return ComplianceStatus.PARTIAL, f"Found {len(bullet_lines)} bullet-point lines."
        return ComplianceStatus.COMPLIANT, "No prohibited bullet points detected."

    if not req.keywords:
        return ComplianceStatus.PARTIAL, "No keywords for pre-screen; needs LLM analysis."

    # Use section text when available, fall back to full text
    search_text = section_text if section_text else full_text
    if not search_text:
        return ComplianceStatus.GAP, "Section not found in PSUR."

    search_lower = search_text.lower()
    matched_kw = [kw for kw in req.keywords if kw.lower() in search_lower]
    match_ratio = len(matched_kw) / len(req.keywords) if req.keywords else 0

    if match_ratio >= 0.6:
        for kw in matched_kw:
            idx = search_lower.find(kw.lower())
            if idx >= 0:
                start = max(0, idx - 60)
                end = min(len(search_text), idx + len(kw) + 60)
                snippet = search_text[start:end].replace("\n", " ")
                return ComplianceStatus.COMPLIANT, f"...{snippet}..."
        return ComplianceStatus.COMPLIANT, f"Keywords found: {', '.join(matched_kw)}"
    elif match_ratio > 0:
        return ComplianceStatus.PARTIAL, (
            f"Partial ({len(matched_kw)}/{len(req.keywords)} keywords): {', '.join(matched_kw)}"
        )
    else:
        return ComplianceStatus.GAP, (
            f"None of the expected keywords found: {', '.join(req.keywords[:5])}"
        )


def _build_llm_json_audit_batch(
    requirements: List[Tuple[Requirement, ComplianceStatus, str]],
    section_text: str,
) -> Optional[str]:
    """Build LLM prompt for auditing a batch of reqs against JSON-derived text."""
    needs_llm = [(r, s, e) for r, s, e in requirements
                 if s in (ComplianceStatus.PARTIAL, ComplianceStatus.GAP)]
    if not needs_llm:
        return None

    if not section_text:
        section_text = "(Section not found in the PSUR)"

    max_chars = 10000
    if len(section_text) > max_chars:
        section_text = section_text[:max_chars] + "\n... [truncated]"

    req_items = []
    for r, kw_status, kw_evidence in needs_llm:
        req_items.append(
            f"- **{r.req_id}** ({r.title}): {r.description}\n"
            f"  Reference: {r.guidance_ref}\n"
            f"  Pre-screen: {kw_status.value} — {kw_evidence}"
        )

    return (
        f"## PSUR Section Content\n\n{section_text}\n\n---\n\n"
        f"## Requirements to Audit ({len(needs_llm)})\n\n"
        + "\n".join(req_items)
        + "\n\n---\n\n"
        f"For EACH requirement above, return a JSON array of objects "
        f"(one per requirement, in order). Each object must have exactly: "
        f'{{\"req_id\": \"...\", \"status\": \"...\", \"evidence\": \"...\", \"recommendation\": \"...\"}}\n\n'
        f"Return ONLY the JSON array."
    )


@dataclass
class SectionAuditResult:
    """Audit result for a single PSUR section, used by the remediation loop."""
    section_key: str
    findings: List[AuditFinding]
    has_gaps: bool = False
    has_critical_gaps: bool = False
    remediation_prompt: str = ""

    def build_remediation_prompt(self) -> str:
        """Build a focused remediation prompt from gap/partial findings."""
        actionable = [f for f in self.findings
                      if f.status in (ComplianceStatus.GAP, ComplianceStatus.PARTIAL)]
        if not actionable:
            self.remediation_prompt = ""
            return ""

        lines = [
            "## COMPLIANCE AUDIT FINDINGS — MANDATORY REMEDIATION\n",
            "The following compliance gaps were identified by the PSUR auditor. "
            "You MUST address ALL of them in your revised output.\n",
        ]

        for f in actionable:
            severity = "CRITICAL GAP" if f.criticality == "CRITICAL" else (
                "MAJOR GAP" if f.status == ComplianceStatus.GAP else "PARTIAL"
            )
            lines.append(
                f"### [{severity}] {f.req_id}: {f.title}\n"
                f"- Framework: {f.framework}\n"
                f"- Finding: {f.evidence}\n"
                f"- Required action: {f.recommendation}\n"
            )

        lines.append(
            "\n## INSTRUCTIONS\n"
            "1. Read each finding above carefully.\n"
            "2. Ensure your revised JSON output addresses EVERY finding.\n"
            "3. For GAP findings: add the missing content entirely.\n"
            "4. For PARTIAL findings: expand and deepen the existing content.\n"
            "5. Do NOT remove any existing valid content.\n"
            "6. Output the COMPLETE section JSON.\n"
        )

        self.remediation_prompt = "\n".join(lines)
        return self.remediation_prompt


def run_json_audit(
    psur: Dict[str, Any],
    *,
    uk_market_detected: bool = False,
    use_llm: bool = True,
    verbose: bool = False,
) -> Tuple[List[SectionAuditResult], AuditReport]:
    """Audit an in-memory PSUR JSON dict during generation.

    This is the pipeline-integrated audit. It always applies both EU MDR
    (MDCG 2022-21) and UK MDR requirements:
      - EU MDR requirements always apply.
      - UK MDR requirements apply when uk_market_detected is True; otherwise
        they are assessed but marked NOT_APPLICABLE.

    Args:
        psur: The PSUR dict (with psur["sections"] containing A–M).
        uk_market_detected: Whether UK sales exist.
        use_llm: Whether to invoke LLM for deep analysis (True by default).
        verbose: Console output.

    Returns:
        Tuple of:
          - List of SectionAuditResult (one per section, with remediation prompts)
          - Full AuditReport for logging/reporting
    """
    t0 = time.time()

    # Always include UK requirements — they'll be NOT_APPLICABLE if no UK sales
    requirements = build_requirements_checklist(include_uk=True)
    sections = psur.get("sections", {})

    # Pre-flatten all section texts
    section_texts: Dict[str, str] = {}
    for sec_key, sec_data in sections.items():
        section_texts[sec_key] = _flatten_json_to_text(sec_data)

    full_text = "\n".join(section_texts.values())

    if verbose:
        console.print(f"\n[bold]  Auditing PSUR ({len(requirements)} requirements, "
                       f"{len(sections)} sections)...[/bold]")

    # ── 1. Keyword pre-screen ─────────────────────────────────────────
    keyword_results: Dict[str, Tuple[ComplianceStatus, str]] = {}
    for req in requirements:
        # UK requirements → NOT_APPLICABLE when no UK market
        if req.framework == Framework.UK_MDR and not uk_market_detected:
            keyword_results[req.req_id] = (
                ComplianceStatus.NOT_APPLICABLE,
                "No UK market detected — UK MDR requirements not applicable.",
            )
            continue

        # Get the section text for this requirement
        sec_text = ""
        if req.section in _SECTION_HEADING_PATTERNS:
            # Requirement maps to a PSUR section letter — find the matching key
            for sec_key, letters in _SECTION_KEY_TO_LETTERS.items():
                if req.section in letters and sec_key in section_texts:
                    sec_text = section_texts[sec_key]
                    break

        status, evidence = _keyword_prescreen_json(req, sec_text, full_text)
        keyword_results[req.req_id] = (status, evidence)

    # ── 2. LLM deep-analysis (batched per section) ────────────────────
    llm_results: Dict[str, Dict[str, str]] = {}

    if use_llm:
        # Group requirements by their section letter
        section_groups: Dict[str, List[Tuple[Requirement, ComplianceStatus, str]]] = {}
        for req in requirements:
            kw_status, kw_evidence = keyword_results[req.req_id]
            if kw_status == ComplianceStatus.NOT_APPLICABLE:
                continue
            section_groups.setdefault(req.section, []).append(
                (req, kw_status, kw_evidence)
            )

        for sec_letter, group in sorted(section_groups.items()):
            # Get the flattened text for this section
            sec_text = ""
            for sec_key, letters in _SECTION_KEY_TO_LETTERS.items():
                if sec_letter in letters and sec_key in section_texts:
                    sec_text = section_texts[sec_key]
                    break
            if not sec_text and sec_letter in _META_SECTION_LETTERS:
                sec_text = full_text

            prompt = _build_llm_json_audit_batch(group, sec_text)
            if prompt is None:
                continue

            needs_ids = [r.req_id for r, s, _ in group
                         if s in (ComplianceStatus.PARTIAL, ComplianceStatus.GAP)]

            if verbose:
                console.print(f"    [dim]LLM auditing section {sec_letter} "
                               f"({len(needs_ids)} reqs)...[/dim]")

            try:
                resp = create_message(
                    model=MODEL,
                    max_tokens=2000,
                    temperature=0.1,
                    system=_LLM_SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": prompt}],
                )
                parsed = _parse_llm_response(resp.content[0].text, needs_ids)
                llm_results.update(parsed)
            except Exception as e:
                if verbose:
                    console.print(f"    [yellow]LLM audit failed for section "
                                   f"{sec_letter}: {e}[/yellow]")

    # ── 3. Build findings ─────────────────────────────────────────────
    all_findings: List[AuditFinding] = []
    for req in requirements:
        kw_status, kw_evidence = keyword_results[req.req_id]

        if req.req_id in llm_results:
            llm_data = llm_results[req.req_id]
            try:
                final_status = ComplianceStatus(llm_data["status"])
            except (ValueError, KeyError):
                final_status = kw_status
            evidence = llm_data.get("evidence", kw_evidence)
            recommendation = llm_data.get("recommendation", "")
            llm_assessed = True
        else:
            final_status = kw_status
            evidence = kw_evidence
            recommendation = "" if final_status == ComplianceStatus.COMPLIANT else (
                f"Review {req.guidance_ref} for compliance guidance."
            )
            llm_assessed = False

        all_findings.append(AuditFinding(
            req_id=req.req_id,
            title=req.title,
            status=final_status,
            evidence=evidence[:300],
            recommendation=recommendation[:300],
            section=req.section,
            framework=req.framework.value,
            criticality=req.criticality.value,
            llm_assessed=llm_assessed,
        ))

    # ── 4. Build per-section audit results ────────────────────────────
    section_results: List[SectionAuditResult] = []
    for sec_key, letters in _SECTION_KEY_TO_LETTERS.items():
        sec_findings = [f for f in all_findings if f.section in letters]
        # Also include meta-section findings for cross-cutting concerns
        # (assign to M_findings_and_conclusions for remediation)
        if sec_key == "M_findings_and_conclusions":
            meta_findings = [f for f in all_findings if f.section in _META_SECTION_LETTERS]
            sec_findings.extend(meta_findings)

        result = SectionAuditResult(
            section_key=sec_key,
            findings=sec_findings,
            has_gaps=any(f.status == ComplianceStatus.GAP for f in sec_findings),
            has_critical_gaps=any(
                f.status == ComplianceStatus.GAP and f.criticality == "CRITICAL"
                for f in sec_findings
            ),
        )
        if result.has_gaps or any(f.status == ComplianceStatus.PARTIAL for f in sec_findings):
            result.build_remediation_prompt()
        section_results.append(result)

    # ── 5. Build full report ──────────────────────────────────────────
    report = AuditReport(
        psur_path="(in-memory)",
        audit_timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
        uk_mdr_enabled=True,
        llm_enabled=use_llm,
        findings=all_findings,
    )
    report.compute_score()

    elapsed = time.time() - t0
    if verbose:
        console.print(
            f"    Score: [{'green' if report.compliance_score >= 80 else 'yellow' if report.compliance_score >= 60 else 'red'}]"
            f"{report.compliance_score}%[/] "
            f"({report.compliant}C / {report.partial}P / {report.gap}G / "
            f"{report.not_applicable}NA)  [{elapsed:.1f}s]"
        )

    return section_results, report


# ═══════════════════════════════════════════════════════════════════════════
# Main DOCX audit engine
# ═══════════════════════════════════════════════════════════════════════════

def run_audit(
    psur_path: str | Path,
    *,
    include_uk: bool = False,
    use_llm: bool = True,
    verbose: bool = False,
) -> AuditReport:
    """Run a full PSUR compliance audit.

    Args:
        psur_path: Path to a PSUR .docx file.
        include_uk: Include UK MDR requirements.
        use_llm: Use LLM for deep analysis (default True). Set False for keyword-only mode.
        verbose: Print progress to console.

    Returns:
        AuditReport with all findings, scores, and optional LLM summary.
    """
    psur_path = Path(psur_path)
    if not psur_path.exists():
        raise FileNotFoundError(f"PSUR file not found: {psur_path}")

    t0 = time.time()

    if verbose:
        console.print(f"\n[bold blue]{'='*56}[/bold blue]")
        console.print(f"[bold blue]  PSUR Auditor — MDCG 2022-21 Compliance Check[/bold blue]")
        console.print(f"[bold blue]{'='*56}[/bold blue]")
        console.print(f"\n  Document: {psur_path.name}")
        console.print(f"  UK MDR:   {'Enabled' if include_uk else 'Disabled'}")
        console.print(f"  LLM:      {'Enabled' if use_llm else 'Disabled (keyword only)'}\n")

    # ── 1. Parse the PSUR ─────────────────────────────────────────────
    if verbose:
        console.print("[bold]Parsing PSUR document...[/bold]")
    parser = PSURParser(psur_path)

    if verbose:
        console.print(f"  Words: {parser.get_word_count():,}")
        console.print(f"  Sections identified: {parser.get_section_count()}")
        console.print(f"  Tables: {parser.get_table_count()}")
        console.print(f"  Headings: {len(parser.headings)}\n")

    # ── 2. Build requirements checklist ───────────────────────────────
    requirements = build_requirements_checklist(include_uk=include_uk)
    if verbose:
        console.print(f"[bold]Auditing {len(requirements)} requirements...[/bold]\n")

    # ── 3. Keyword pre-screen ─────────────────────────────────────────
    keyword_results: Dict[str, Tuple[ComplianceStatus, str]] = {}
    for req in requirements:
        status, evidence = _keyword_prescreen(req, parser)
        keyword_results[req.req_id] = (status, evidence)

    if verbose:
        kw_comp = sum(1 for s, _ in keyword_results.values() if s == ComplianceStatus.COMPLIANT)
        kw_part = sum(1 for s, _ in keyword_results.values() if s == ComplianceStatus.PARTIAL)
        kw_gap = sum(1 for s, _ in keyword_results.values() if s == ComplianceStatus.GAP)
        console.print(f"  Keyword pre-screen: {kw_comp} compliant, {kw_part} partial, {kw_gap} gap")

    # ── 4. LLM deep-analysis (batched per section) ────────────────────
    llm_results: Dict[str, Dict[str, str]] = {}

    if use_llm:
        # Group requirements by section
        section_groups: Dict[str, List[Tuple[Requirement, ComplianceStatus, str]]] = {}
        for req in requirements:
            sec = req.section
            kw_status, kw_evidence = keyword_results[req.req_id]
            section_groups.setdefault(sec, []).append((req, kw_status, kw_evidence))

        # Count how many need LLM
        total_llm = sum(
            1 for req in requirements
            if keyword_results[req.req_id][0] in (ComplianceStatus.PARTIAL, ComplianceStatus.GAP)
        )
        if verbose:
            console.print(f"  LLM deep-analysis: {total_llm} requirements need assessment\n")

        for sec_key, group_reqs in sorted(section_groups.items()):
            prompt = _build_llm_audit_batch(group_reqs, parser, sec_key)
            if prompt is None:
                continue

            needs_llm_ids = [r.req_id for r, s, _ in group_reqs
                            if s in (ComplianceStatus.PARTIAL, ComplianceStatus.GAP)]

            if verbose:
                console.print(f"  [dim]LLM analysing section {sec_key} ({len(needs_llm_ids)} reqs)...[/dim]")

            try:
                resp = create_message(
                    model=MODEL,
                    max_tokens=2000,
                    temperature=0.1,
                    system=_LLM_SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": prompt}],
                )
                parsed = _parse_llm_response(resp.content[0].text, needs_llm_ids)
                llm_results.update(parsed)

                # Track token usage
                if "input" not in llm_results:
                    llm_results["_tokens"] = {"input": 0, "output": 0}
                # (stored in report below)
            except Exception as e:
                if verbose:
                    console.print(f"    [yellow]LLM call failed for section {sec_key}: {e}[/yellow]")

    # ── 5. Merge results and build findings ───────────────────────────
    report = AuditReport(
        psur_path=str(psur_path),
        audit_timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
        uk_mdr_enabled=include_uk,
        llm_enabled=use_llm,
    )

    for req in requirements:
        kw_status, kw_evidence = keyword_results[req.req_id]

        # If LLM provided an override, use it
        if req.req_id in llm_results:
            llm_data = llm_results[req.req_id]
            try:
                final_status = ComplianceStatus(llm_data["status"])
            except (ValueError, KeyError):
                final_status = kw_status
            evidence = llm_data.get("evidence", kw_evidence)
            recommendation = llm_data.get("recommendation", "")
            llm_assessed = True
        else:
            final_status = kw_status
            evidence = kw_evidence
            recommendation = "" if final_status == ComplianceStatus.COMPLIANT else (
                f"Review {req.guidance_ref} for compliance guidance."
            )
            llm_assessed = False

        report.findings.append(AuditFinding(
            req_id=req.req_id,
            title=req.title,
            status=final_status,
            evidence=evidence[:300],
            recommendation=recommendation[:300],
            section=req.section,
            framework=req.framework.value,
            criticality=req.criticality.value,
            llm_assessed=llm_assessed,
        ))

    report.compute_score()

    # ── 6. LLM executive summary ──────────────────────────────────────
    if use_llm:
        if verbose:
            console.print("\n  [dim]Generating audit summary...[/dim]")
        report.llm_summary = _generate_audit_summary(report, parser)

    elapsed = time.time() - t0
    if verbose:
        console.print(f"\n  [dim]Audit completed in {elapsed:.1f}s[/dim]\n")

    return report


# ═══════════════════════════════════════════════════════════════════════════
# Console report rendering
# ═══════════════════════════════════════════════════════════════════════════

_STATUS_COLORS = {
    ComplianceStatus.COMPLIANT: "green",
    ComplianceStatus.PARTIAL: "yellow",
    ComplianceStatus.GAP: "red",
    ComplianceStatus.NOT_APPLICABLE: "dim",
}

_CRITICALITY_COLORS = {
    "CRITICAL": "bold red",
    "MAJOR": "bold yellow",
    "MINOR": "dim",
}


def print_audit_report(report: AuditReport) -> None:
    """Pretty-print the audit report to the console."""
    # ── Score card ────────────────────────────────────────────────────
    score_color = "green" if report.compliance_score >= 80 else (
        "yellow" if report.compliance_score >= 60 else "red"
    )
    console.print(Panel(
        f"[{score_color} bold]{report.compliance_score}% Compliance Score[/{score_color} bold]\n\n"
        f"  [green]{report.compliant}[/green] Compliant  "
        f"  [yellow]{report.partial}[/yellow] Partial  "
        f"  [red]{report.gap}[/red] Gap  "
        f"  [dim]{report.not_applicable}[/dim] N/A  "
        f"  (Total: {report.total_requirements})",
        title="PSUR Audit Results",
        border_style=score_color,
    ))

    # ── LLM Summary ──────────────────────────────────────────────────
    if report.llm_summary:
        console.print(Panel(
            report.llm_summary,
            title="Audit Executive Summary",
            border_style="blue",
        ))

    # ── Findings table ────────────────────────────────────────────────
    table = Table(title="Detailed Findings", show_lines=True)
    table.add_column("ID", style="bold", width=6)
    table.add_column("Title", width=30)
    table.add_column("Status", width=12)
    table.add_column("Crit.", width=8)
    table.add_column("Evidence", width=50)
    table.add_column("Recommendation", width=40)

    # Sort: GAP first, then PARTIAL, then COMPLIANT
    sort_order = {
        ComplianceStatus.GAP: 0,
        ComplianceStatus.PARTIAL: 1,
        ComplianceStatus.COMPLIANT: 2,
        ComplianceStatus.NOT_APPLICABLE: 3,
    }
    sorted_findings = sorted(report.findings, key=lambda f: sort_order.get(f.status, 9))

    for f in sorted_findings:
        status_color = _STATUS_COLORS.get(f.status, "white")
        crit_color = _CRITICALITY_COLORS.get(f.criticality, "white")
        llm_marker = " *" if f.llm_assessed else ""

        table.add_row(
            f.req_id,
            f.title,
            f"[{status_color}]{f.status.value}{llm_marker}[/{status_color}]",
            f"[{crit_color}]{f.criticality}[/{crit_color}]",
            f.evidence[:80] + ("..." if len(f.evidence) > 80 else ""),
            f.recommendation[:60] + ("..." if len(f.recommendation) > 60 else ""),
        )

    console.print(table)
    console.print("[dim]* = LLM-assessed finding[/dim]\n")

    # ── Token usage ───────────────────────────────────────────────────
    if report.llm_enabled and (report.token_usage.get("input", 0) > 0):
        console.print(
            f"[dim]LLM tokens: {report.token_usage['input']:,} in / "
            f"{report.token_usage['output']:,} out[/dim]"
        )


# ═══════════════════════════════════════════════════════════════════════════
# Standalone CLI
# ═══════════════════════════════════════════════════════════════════════════

def main() -> None:
    """Standalone CLI entry point (also usable via main.py audit)."""
    import argparse

    ap = argparse.ArgumentParser(
        description="LLM-powered PSUR Auditor — MDCG 2022-21 compliance check"
    )
    ap.add_argument("psur_docx", help="Path to PSUR .docx file")
    ap.add_argument("--uk-mdr", action="store_true",
                    help="Include UK MDR requirements")
    ap.add_argument("--no-llm", action="store_true",
                    help="Keyword-only mode (no LLM calls)")
    ap.add_argument("--output", "-o", type=str, default=None,
                    help="Save JSON report to this path")
    ap.add_argument("--verbose", "-v", action="store_true", default=True,
                    help="Verbose console output (default: on)")
    ap.add_argument("--ollama-model", type=str, default=None,
                    help="Use a local Ollama model instead of Claude")
    ap.add_argument("--ollama-url", type=str, default=None,
                    help="Ollama API base URL")
    args = ap.parse_args()

    # Ollama override
    if args.ollama_model:
        from llm_client import set_ollama_override
        set_ollama_override(args.ollama_model, url=args.ollama_url)

    report = run_audit(
        args.psur_docx,
        include_uk=args.uk_mdr,
        use_llm=not args.no_llm,
        verbose=args.verbose,
    )

    print_audit_report(report)

    if args.output:
        out_path = Path(args.output)
        with open(out_path, "w") as f:
            json.dump(asdict(report), f, indent=2, default=str)
        console.print(f"\n[green]Report saved: {out_path}[/green]")

    # Exit code: 0 if >=80%, 1 otherwise
    sys.exit(0 if report.compliance_score >= 80 else 1)


if __name__ == "__main__":
    main()
