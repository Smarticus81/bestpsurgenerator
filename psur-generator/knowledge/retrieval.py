"""Precision retrieval over the rule registry.

The retrieval pipeline is deterministic-first:

  1. Hard filter by ``applies_to`` (section, device class, market, sterility)
  2. Evaluate ``triggers.when`` expression against the Query.findings dict
  3. Score by keyword/finding overlap and criticality
  4. Optional embedding rerank if ``KB_USE_EMBEDDINGS=1`` (not enabled by default)

This avoids the need for a vector database for the small rule corpus
(<500 rules) while remaining swap-in compatible with one.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from knowledge.registry import Rule, Registry


# ── Query types ─────────────────────────────────────────────────────

@dataclass
class Query:
    section: Optional[str] = None
    device_class_eu: Optional[str] = None
    device_class_uk: Optional[str] = None
    sterility: Optional[str] = None  # "sterile" | "non-sterile"
    markets: Set[str] = field(default_factory=set)  # {"EU","UK","WW"}
    findings: Dict[str, Any] = field(default_factory=dict)
    free_text: Optional[str] = None
    max_rules: int = 30
    include_global: bool = True


@dataclass
class ScoredRule:
    rule: Rule
    score: float
    reasons: List[str] = field(default_factory=list)


# ── Helpers ─────────────────────────────────────────────────────────

def _norm_class(s: Optional[str]) -> str:
    if not s:
        return ""
    return re.sub(r"[^A-Z0-9]", "", s.upper())


def _matches_class(rule_classes: List[str], device_class: str) -> bool:
    if not rule_classes:
        return True  # rule is class-agnostic
    nd = _norm_class(device_class)
    for rc in rule_classes:
        nrc = _norm_class(rc)
        if not nrc:
            continue
        if nrc == nd or nrc in nd or nd in nrc:
            return True
    return False


def _matches_market(rule_markets: List[str], q_markets: Set[str]) -> bool:
    if not rule_markets:
        return True
    return bool(set(m.upper() for m in rule_markets) & {m.upper() for m in q_markets})


def _matches_sterility(rule_sterility: List[str], q_sterility: Optional[str]) -> bool:
    if not rule_sterility:
        return True
    if not q_sterility:
        return True
    qs = q_sterility.lower()
    return any(s.lower() in qs or qs in s.lower() for s in rule_sterility)


# Safe ``when`` expression evaluator. Only allows boolean ops and
# comparisons against names in ``findings``. Refuses anything else.
_SAFE_WHEN = re.compile(
    r"^[\w\s\.\(\)<>=!&|\-+\"'\d,]*$"
)


def _eval_when(expr: Optional[str], findings: Dict[str, Any]) -> bool:
    if not expr:
        return True
    if not _SAFE_WHEN.match(expr):
        return False
    # Map symbolic operators to Python
    py = expr.replace("&&", " and ").replace("||", " or ")
    try:
        return bool(eval(py, {"__builtins__": {}}, dict(findings)))  # noqa: S307
    except Exception:
        return False


# ── Public API ──────────────────────────────────────────────────────

def retrieve(query: Query, registry: Registry) -> List[ScoredRule]:
    """Return scored rules ranked by relevance, capped at ``query.max_rules``."""
    if query.section:
        candidates = registry.by_section(query.section)
    else:
        candidates = registry.all()

    scored: List[ScoredRule] = []
    free_text = (query.free_text or "").lower()
    finding_flags = {k for k, v in query.findings.items() if v}

    for rule in candidates:
        reasons: List[str] = []

        # ── Hard filters ─────────────────────────────────────────
        if not _matches_class(rule.applies_to.device_classes, query.device_class_eu or ""):
            # Try UK class as fallback
            if not _matches_class(
                rule.applies_to.device_classes, query.device_class_uk or ""
            ):
                continue
        if not _matches_market(rule.applies_to.markets, query.markets):
            continue
        if not _matches_sterility(rule.applies_to.sterility, query.sterility):
            continue
        if not _eval_when(rule.triggers.when, query.findings):
            continue

        # ── Scoring ──────────────────────────────────────────────
        score = 0.0

        # Section specificity: rules that explicitly target the section beat globals
        if query.section and rule.applies_to.sections:
            short = query.section.split("_", 1)[0]
            if short in rule.applies_to.sections or query.section in rule.applies_to.sections:
                score += 3.0
                reasons.append("section_specific")
        elif not rule.applies_to.sections:
            score += 0.5
            reasons.append("global_rule")

        # Criticality weighting
        score += {"CRITICAL": 2.0, "MAJOR": 1.0, "MINOR": 0.25}.get(
            rule.criticality.upper(), 0.5
        )

        # Finding flag overlap
        finding_hits = finding_flags & set(rule.triggers.findings)
        if finding_hits:
            score += 1.5 * len(finding_hits)
            reasons.append("finding:" + ",".join(sorted(finding_hits)))

        # Market specificity
        if "UK" in {m.upper() for m in query.markets} and any(
            m.upper() == "UK" for m in rule.applies_to.markets
        ):
            score += 1.5
            reasons.append("uk_market")

        # Class specificity
        if rule.applies_to.device_classes and (query.device_class_eu or query.device_class_uk):
            score += 1.0
            reasons.append("class_specific")

        # Free-text keyword scoring
        if free_text:
            kw_hits = sum(
                1 for k in rule.triggers.keywords if k.lower() in free_text
            )
            if kw_hits:
                score += 0.5 * kw_hits
                reasons.append(f"keywords:{kw_hits}")

        scored.append(ScoredRule(rule=rule, score=score, reasons=reasons))

    # Optional embedding rerank
    if os.environ.get("KB_USE_EMBEDDINGS") == "1":
        try:
            scored = _embedding_rerank(query, scored)
        except Exception:
            pass  # silent degradation; deterministic scoring still applies

    scored.sort(key=lambda s: (-s.score, s.rule.id))
    return scored[: query.max_rules]


def render_rules_for_prompt(
    scored: List[ScoredRule],
    header: str = "## APPLIED REGULATORY REQUIREMENTS",
) -> str:
    """Render scored rules as a numbered prompt block.

    Each rule is shown with its ID so the agent can echo it back in the
    output's ``_meta.rules_applied`` array for audit traceability.
    """
    if not scored:
        return ""
    lines = [
        header,
        "",
        "These requirements have been retrieved from the knowledge base for "
        "this section based on device class, markets, and findings. Apply each "
        "one. When you finalize the section JSON, include the array "
        "`_meta.rules_applied: [\"<rule_id>\", ...]` listing every rule "
        "you applied.",
        "",
    ]
    for i, sr in enumerate(scored, 1):
        r = sr.rule
        lines.append(
            f"{i}. [{r.id}] ({r.framework} — {r.citation}, {r.criticality})"
        )
        instr = (r.agent_instruction or r.obligation).strip()
        # Indent multi-line instructions
        for ln in instr.splitlines():
            lines.append(f"   {ln}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ── Optional embedding rerank stub ──────────────────────────────────

def _embedding_rerank(query: Query, scored: List[ScoredRule]) -> List[ScoredRule]:
    """Optional rerank pass. Disabled by default. Requires sentence-transformers."""
    try:
        from sentence_transformers import SentenceTransformer, util  # type: ignore
    except Exception:
        return scored
    model_name = os.environ.get("KB_EMBED_MODEL", "BAAI/bge-small-en-v1.5")
    model = SentenceTransformer(model_name)
    qtext = " ".join(
        filter(None, [query.section, query.free_text, " ".join(query.findings)])
    )
    if not qtext.strip():
        return scored
    qv = model.encode(qtext, convert_to_tensor=True, normalize_embeddings=True)
    for sr in scored:
        rtext = f"{sr.rule.obligation} {sr.rule.agent_instruction}"
        rv = model.encode(rtext, convert_to_tensor=True, normalize_embeddings=True)
        sr.score += float(util.cos_sim(qv, rv).item())  # type: ignore
        sr.reasons.append("embed")
    return scored
