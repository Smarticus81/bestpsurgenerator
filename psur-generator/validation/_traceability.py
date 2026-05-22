"""
Leakage-prevention rule engine — sentence-level traceability matrix.

Every entity (identifier, date, count, percentage) referenced in PSUR
narrative must be sourced from CURRENT-period parsed input data or the
deterministic _statistics block. Anything else is flagged as leakage.

Entry point: TraceabilityChecksMixin._check_traceability(psur, parsed_data,
device_context, reporting_start, reporting_end) -> (errors, matrix)
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Dict, List, Set, Tuple


# ── entity patterns ──────────────────────────────────────────────────
_ID_PATTERNS = [
    ("CAPA",       re.compile(r"\bCAPA[-\s]?\d{2,6}\b", re.I)),
    ("MDR",        re.compile(r"\bMDR[-\s]?\d{3,10}\b", re.I)),
    ("FSCA",       re.compile(r"\bFSCA[-\s]?[A-Z0-9\-]{3,20}\b", re.I)),
    ("CMP",        re.compile(r"\bCMP[-\s]?\d{3,10}\b", re.I)),
    ("COMPLAINT",  re.compile(r"\bCOMP(?:LAINT)?[-\s]?\d{3,10}\b", re.I)),
    ("PMCF",       re.compile(r"\bPMCF[-\s]?[A-Z0-9\-]{2,20}\b", re.I)),
    ("DOC",        re.compile(r"\b(?:DOC|REF)[-\s]?\d{3,10}\b", re.I)),
    ("STUDY",      re.compile(r"\bSTUDY[-\s]?[A-Z0-9\-]{2,20}\b", re.I)),
]

_MONTH_YEAR = re.compile(
    r"\b(January|February|March|April|May|June|July|August|"
    r"September|October|November|December)\s+(\d{4})\b"
)
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_YEAR_ONLY = re.compile(r"\b(19|20)\d{2}\b")
_PERCENTAGE = re.compile(r"\b\d+(?:\.\d+)?\s?%")
_LARGE_COUNT = re.compile(r"\b\d{1,3}(?:,\d{3})*\b|\b\d{3,}\b")  # handles 12,037 AND 12037

# Regulatory identifiers that should never be flagged as unsourced counts
_REGULATORY_STOPLIST = {
    "510",   # 510(k)
    "2017",  # Regulation (EU) 2017/745
    "745",   # EU 2017/745
    "2022",  # MDCG 2022-21
    "2797",  # BSI NB number
    "0123",  # TUV NB number
    "0344",  # SGS NB number
    "0482",  # DEKRA NB number
    "60601", # IEC 60601
    "14971", # ISO 14971
    "13485", # ISO 13485
    "20916", # ISO 20916
}

# Sentence splitter — naive but adequate for regulatory prose
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")


def _norm_id(value: str) -> str:
    return re.sub(r"[\s\-]", "", str(value)).upper()


def _walk_strings(node: Any, path: str = "") -> List[Tuple[str, str]]:
    """Yield (json_path, string_value) for every non-trivial string in node."""
    out: List[Tuple[str, str]] = []
    if isinstance(node, dict):
        for k, v in node.items():
            out.extend(_walk_strings(v, f"{path}.{k}" if path else k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out.extend(_walk_strings(v, f"{path}[{i}]"))
    elif isinstance(node, str) and len(node.strip()) > 20:
        out.append((path, node))
    return out


class TraceabilityChecksMixin:
    """Sentence-level traceability + leakage detection."""

    # ── registry construction ────────────────────────────────────────
    def _build_traceability_registry(
        self,
        parsed_data: Dict[str, Any],
        stats: Dict[str, Any],
        reporting_start: date | None,
        reporting_end: date | None,
    ) -> Dict[str, Set[str]]:
        """Allowed identifiers, dates, counts, percentages."""
        ids: Set[str] = set()
        dates: Set[str] = set()
        years: Set[str] = set()
        counts: Set[str] = set()
        percentages: Set[str] = set()

        # ─ identifiers from CAPA / complaints / FSCA / PMCF / studies ─
        capa = parsed_data.get("capa") or {}
        if isinstance(capa, dict):
            for rec in capa.get("capa_records", []) or []:
                if isinstance(rec, dict):
                    for k in ("capa_number", "capa_id", "number", "id", "reference"):
                        v = rec.get(k)
                        if v:
                            ids.add(_norm_id(v))

        complaints = parsed_data.get("complaints") or {}
        if isinstance(complaints, dict):
            for s in complaints.get("complaint_summaries", []) or []:
                if isinstance(s, dict):
                    for k in ("complaint_number", "mdr_number", "capa_number",
                              "id", "reference", "complaint_id"):
                        v = s.get(k)
                        if v:
                            ids.add(_norm_id(v))

        for fsca in (parsed_data.get("fsca") or []):
            if isinstance(fsca, dict):
                for k in ("fsca_id", "reference_number", "id", "reference"):
                    v = fsca.get(k)
                    if v:
                        ids.add(_norm_id(v))

        pmcf = parsed_data.get("pmcf") or parsed_data.get("clinical_performance") or {}
        if isinstance(pmcf, dict):
            for s in pmcf.get("studies", []) or []:
                if isinstance(s, dict):
                    for k in ("study_id", "id", "reference"):
                        v = s.get(k)
                        if v:
                            ids.add(_norm_id(v))

        # ─ dates within reporting window ─
        if reporting_start and reporting_end:
            cur = date(reporting_start.year, reporting_start.month, 1)
            while cur <= reporting_end:
                dates.add(cur.strftime("%Y-%m"))
                dates.add(cur.strftime("%B %Y").upper())
                years.add(str(cur.year))
                # advance one month
                if cur.month == 12:
                    cur = date(cur.year + 1, 1, 1)
                else:
                    cur = date(cur.year, cur.month + 1, 1)
            # Also allow the immediate preceding period (always cited as comparator)
            prev_start_y = reporting_start.year - 1
            for m in range(1, 13):
                d = date(prev_start_y, m, 1)
                dates.add(d.strftime("%B %Y").upper())
            years.add(str(prev_start_y))

        # ─ counts and percentages from deterministic stats ─
        def _harvest_numerics(obj: Any):
            if isinstance(obj, dict):
                for v in obj.values():
                    _harvest_numerics(v)
            elif isinstance(obj, list):
                for v in obj:
                    _harvest_numerics(v)
            elif isinstance(obj, (int, float)):
                if isinstance(obj, int) and obj >= 100:
                    counts.add(str(obj))
                    # Also add comma-formatted version (12037 -> "12,037")
                    counts.add(f"{obj:,}")
                if isinstance(obj, float):
                    # Percent-shaped values
                    pct = f"{obj:.2f}".rstrip("0").rstrip(".")
                    percentages.add(pct)

        _harvest_numerics(stats or {})

        return {
            "ids": ids,
            "dates": dates,
            "years": years,
            "counts": counts,
            "percentages": percentages,
        }

    # ── sentence scanner ─────────────────────────────────────────────
    def _scan_sentence(
        self,
        sentence: str,
        registry: Dict[str, Set[str]],
    ) -> List[Dict[str, Any]]:
        findings: List[Dict[str, Any]] = []
        residual = sentence  # IDs/dates removed so digits aren't double-counted

        # Identifiers
        for kind, pat in _ID_PATTERNS:
            for m in pat.findall(sentence):
                norm = _norm_id(m)
                status = "ok" if norm in registry["ids"] else "leakage"
                findings.append({
                    "type": "id",
                    "kind": kind,
                    "value": m,
                    "status": status,
                })
            residual = pat.sub(" ", residual)

        # Dates: Month Year
        for m in _MONTH_YEAR.finditer(sentence):
            month_year = f"{m.group(1)} {m.group(2)}".upper()
            status = "ok" if month_year in registry["dates"] else "out_of_period"
            findings.append({
                "type": "date",
                "kind": "month_year",
                "value": m.group(0),
                "status": status,
            })
        residual = _MONTH_YEAR.sub(" ", residual)

        # Dates: ISO
        for m in _ISO_DATE.finditer(sentence):
            iso_ym = f"{m.group(1)}-{m.group(2)}"
            status = "ok" if iso_ym in registry["dates"] else "out_of_period"
            findings.append({
                "type": "date",
                "kind": "iso",
                "value": m.group(0),
                "status": status,
            })
        residual = _ISO_DATE.sub(" ", residual)

        # Bare years (only flag if outside allowed window)
        if registry["years"]:
            for m in _YEAR_ONLY.finditer(residual):
                yr = m.group(0)
                if yr not in registry["years"]:
                    findings.append({
                        "type": "year",
                        "kind": "year",
                        "value": yr,
                        "status": "out_of_period",
                    })
        residual = _YEAR_ONLY.sub(" ", residual)

        # Percentages (before counts so % digits are excluded)
        for m in _PERCENTAGE.findall(residual):
            num = m.replace("%", "").strip().rstrip("0").rstrip(".")
            status = "ok" if num in registry["percentages"] else "unsourced_percentage"
            findings.append({
                "type": "percentage",
                "kind": "pct",
                "value": m,
                "status": status,
            })
        residual = _PERCENTAGE.sub(" ", residual)

        # Large counts
        for m in _LARGE_COUNT.findall(residual):
            # Normalize: strip commas for registry lookup
            norm = m.replace(",", "")
            # Skip regulatory stoplist items
            if norm in _REGULATORY_STOPLIST:
                continue
            # Skip small fragments that are likely comma-split artifacts
            if len(norm) <= 2:
                continue
            status = "ok" if (norm in registry["counts"]
                             or m in registry["counts"]) else "unsourced_count"
            findings.append({
                "type": "count",
                "kind": "int",
                "value": m,
                "status": status,
            })

        return findings

    # ── main entry point ─────────────────────────────────────────────
    def _check_traceability(
        self,
        psur: Dict[str, Any],
        parsed_data: Dict[str, Any] | None = None,
        device_context: Dict[str, Any] | None = None,
        reporting_start: date | None = None,
        reporting_end: date | None = None,
    ) -> Tuple[List[str], Dict[str, Any]]:
        """Return (errors, traceability_matrix). Matrix is JSON-serialisable."""
        parsed_data = parsed_data or {}
        _ = device_context  # reserved for future device-specific allowances
        stats = psur.get("_statistics", {}) or {}

        # Resolve reporting window from multiple sources if not supplied
        if reporting_start is None or reporting_end is None:
            # Try _statistics block first (most reliable)
            sp = stats.get("surveillance_period", {}) or {}
            if sp.get("start_date") and reporting_start is None:
                try:
                    reporting_start = datetime.fromisoformat(
                        str(sp["start_date"])[:10]).date()
                except ValueError:
                    pass
            if sp.get("end_date") and reporting_end is None:
                try:
                    reporting_end = datetime.fromisoformat(
                        str(sp["end_date"])[:10]).date()
                except ValueError:
                    pass

            # Try cover page document_information
            if reporting_start is None or reporting_end is None:
                cover = psur.get("psur_cover_page", {}) or {}
                doc_info = cover.get("document_information", {}) or {}
                dcp = doc_info.get("data_collection_period", {}) or {}
                for k in ("start_date", "reporting_period_start", "period_start"):
                    v = dcp.get(k) or cover.get(k)
                    if v and reporting_start is None:
                        try:
                            reporting_start = datetime.fromisoformat(
                                str(v)[:10]).date()
                            break
                        except ValueError:
                            pass
                for k in ("end_date", "reporting_period_end", "period_end"):
                    v = dcp.get(k) or cover.get(k)
                    if v and reporting_end is None:
                        try:
                            reporting_end = datetime.fromisoformat(
                                str(v)[:10]).date()
                            break
                        except ValueError:
                            pass

        registry = self._build_traceability_registry(
            parsed_data, stats, reporting_start, reporting_end,
        )

        matrix: Dict[str, List[Dict[str, Any]]] = {}
        errors: List[str] = []

        for path, text in _walk_strings(psur.get("sections", {})):
            section = path.split(".")[0]
            sentences = _SENTENCE_SPLIT.split(text)
            for idx, sent in enumerate(sentences):
                sent = sent.strip()
                if len(sent) < 25:
                    continue
                findings = self._scan_sentence(sent, registry)
                if not findings:
                    continue
                leaks = [f for f in findings if f["status"] != "ok"]
                matrix.setdefault(section, []).append({
                    "path": path,
                    "sentence_idx": idx,
                    "sentence": sent[:280],
                    "entities": findings,
                    "leakage_count": len(leaks),
                })
                for f in leaks:
                    errors.append(
                        f"TRACEABILITY [{section}] {f['status']} "
                        f"{f['type']}='{f['value']}' at {path} sent#{idx}: "
                        f"\"{sent[:120]}…\""
                    )

        # De-duplicate while preserving order
        seen: Set[str] = set()
        unique_errors: List[str] = []
        for e in errors:
            if e not in seen:
                seen.add(e)
                unique_errors.append(e)

        summary = {
            "registry_size": {k: len(v) for k, v in registry.items()},
            "reporting_period": {
                "start": reporting_start.isoformat() if reporting_start else None,
                "end": reporting_end.isoformat() if reporting_end else None,
            },
            "total_sentences_scanned": sum(len(v) for v in matrix.values()),
            "total_leakage_findings": len(unique_errors),
        }

        return unique_errors, {"summary": summary, "by_section": matrix}
