"""Typed registry of regulatory rules used to drive PSUR generation.

A *rule* is the atomic unit of regulatory knowledge. Each rule carries:
  - id            stable dotted identifier (e.g. "UK_MDR.44ZM.3.units_uk_market")
  - framework     EU_MDR | UK_MDR_2024 | MDCG_2022_21 | IMDRF | ISO_14971 | RACT
                  | FORMQAR_054 | HOUSE
  - citation      human-readable source citation
  - version       date the rule text was sourced (ISO date)
  - applies_to    {sections, device_classes, markets, sterility}
  - triggers      {when (expr), keywords}
  - obligation    plain-English regulatory requirement
  - agent_instruction  string injected into LLM section prompts
  - validator_check    name of a validator function (or null)
  - criticality   CRITICAL | MAJOR | MINOR
  - source_hash   sha256 of the obligation+citation, used to detect drift

Rule files live under ``knowledge/rules/*.json`` and are merged at load time.
A registry is created once per process via :func:`get_registry`.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set


KNOWLEDGE_DIR = Path(__file__).resolve().parent
RULES_DIR = KNOWLEDGE_DIR / "rules"
VERSION_FILE = KNOWLEDGE_DIR / "VERSION"

# Loaded once for the process
KNOWLEDGE_VERSION: str = (
    VERSION_FILE.read_text(encoding="utf-8").strip()
    if VERSION_FILE.exists()
    else "0.0.0"
)


@dataclass
class AppliesTo:
    sections: List[str] = field(default_factory=list)
    device_classes: List[str] = field(default_factory=list)
    markets: List[str] = field(default_factory=list)
    sterility: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "AppliesTo":
        d = d or {}
        return cls(
            sections=list(d.get("sections", []) or []),
            device_classes=list(d.get("device_classes", []) or []),
            markets=list(d.get("markets", []) or []),
            sterility=list(d.get("sterility", []) or []),
        )


@dataclass
class Triggers:
    when: Optional[str] = None
    keywords: List[str] = field(default_factory=list)
    findings: List[str] = field(default_factory=list)  # symbolic finding flags

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "Triggers":
        d = d or {}
        return cls(
            when=d.get("when"),
            keywords=list(d.get("keywords", []) or []),
            findings=list(d.get("findings", []) or []),
        )


@dataclass
class Rule:
    id: str
    framework: str
    citation: str
    version: str
    obligation: str
    agent_instruction: str
    applies_to: AppliesTo = field(default_factory=AppliesTo)
    triggers: Triggers = field(default_factory=Triggers)
    validator_check: Optional[str] = None
    criticality: str = "MAJOR"
    effective_from: Optional[str] = None
    notes: Optional[str] = None
    source_file: Optional[str] = None  # populated by Registry
    source_hash: Optional[str] = None  # populated by Registry

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Rule":
        return cls(
            id=d["id"],
            framework=d["framework"],
            citation=d.get("citation", ""),
            version=d.get("version", ""),
            obligation=d.get("obligation", ""),
            agent_instruction=d.get("agent_instruction", d.get("obligation", "")),
            applies_to=AppliesTo.from_dict(d.get("applies_to")),
            triggers=Triggers.from_dict(d.get("triggers")),
            validator_check=d.get("validator_check"),
            criticality=d.get("criticality", "MAJOR"),
            effective_from=d.get("effective_from"),
            notes=d.get("notes"),
        )

    def to_dict(self) -> Dict[str, Any]:
        out = asdict(self)
        return out


def _hash_rule(r: Rule) -> str:
    payload = f"{r.id}|{r.citation}|{r.version}|{r.obligation}".encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()[:16]


class Registry:
    """In-memory index over all loaded rules."""

    def __init__(self, rules_dir: Path = RULES_DIR):
        self._rules_dir = rules_dir
        self._by_id: Dict[str, Rule] = {}
        self._by_section: Dict[str, List[Rule]] = {}
        self._by_framework: Dict[str, List[Rule]] = {}
        self._loaded_files: List[str] = []
        self._load()

    # ── Loading ──────────────────────────────────────────────────────

    def _load(self) -> None:
        if not self._rules_dir.exists():
            return
        files = sorted(self._rules_dir.glob("*.json"))
        for fp in files:
            try:
                with fp.open(encoding="utf-8") as f:
                    payload = json.load(f)
            except Exception as exc:  # pragma: no cover - bad file shouldn't crash
                import sys
                print(
                    f"[knowledge.registry] Failed to load {fp.name}: {exc}",
                    file=sys.stderr,
                )
                continue

            rule_dicts = payload.get("rules", []) if isinstance(payload, dict) else payload
            for rd in rule_dicts:
                try:
                    rule = Rule.from_dict(rd)
                except KeyError as exc:
                    import sys
                    print(
                        f"[knowledge.registry] Malformed rule in {fp.name}: missing {exc}",
                        file=sys.stderr,
                    )
                    continue
                rule.source_file = fp.name
                rule.source_hash = _hash_rule(rule)
                self._index(rule)
            self._loaded_files.append(fp.name)

    def _index(self, rule: Rule) -> None:
        if rule.id in self._by_id:
            import sys
            print(
                f"[knowledge.registry] Duplicate rule id: {rule.id} "
                f"(in {rule.source_file}); keeping first.",
                file=sys.stderr,
            )
            return
        self._by_id[rule.id] = rule
        for s in rule.applies_to.sections or ["*"]:
            self._by_section.setdefault(s, []).append(rule)
        self._by_framework.setdefault(rule.framework, []).append(rule)

    # ── Query API ────────────────────────────────────────────────────

    def all(self) -> List[Rule]:
        return list(self._by_id.values())

    def by_id(self, rule_id: str) -> Optional[Rule]:
        return self._by_id.get(rule_id)

    def by_section(self, section_key: str) -> List[Rule]:
        """Return rules that apply to a section. Section key may be either
        the long form ('A_executive_summary') or short letter ('A').
        Rules with empty applies_to.sections are also returned (global)."""
        short = section_key.split("_", 1)[0] if "_" in section_key else section_key
        candidates: List[Rule] = []
        seen: Set[str] = set()
        for key in (section_key, short, "*"):
            for r in self._by_section.get(key, []):
                if r.id in seen:
                    continue
                # Empty sections list = global rule
                if not r.applies_to.sections or short in r.applies_to.sections \
                        or section_key in r.applies_to.sections:
                    seen.add(r.id)
                    candidates.append(r)
        # Also include genuinely global rules (empty applies_to.sections)
        for r in self._by_id.values():
            if not r.applies_to.sections and r.id not in seen:
                seen.add(r.id)
                candidates.append(r)
        return candidates

    def by_framework(self, framework: str) -> List[Rule]:
        return list(self._by_framework.get(framework, []))

    def frameworks(self) -> List[str]:
        return sorted(self._by_framework.keys())

    def loaded_files(self) -> List[str]:
        return list(self._loaded_files)

    def version(self) -> str:
        return KNOWLEDGE_VERSION

    def stats(self) -> Dict[str, Any]:
        return {
            "version": KNOWLEDGE_VERSION,
            "total_rules": len(self._by_id),
            "frameworks": {fw: len(rs) for fw, rs in self._by_framework.items()},
            "files": self._loaded_files,
        }


# ── Process-wide singleton ──────────────────────────────────────────

_REGISTRY: Optional[Registry] = None


def get_registry(force_reload: bool = False) -> Registry:
    """Return (and lazily build) the process-wide rule registry."""
    global _REGISTRY
    if _REGISTRY is None or force_reload:
        _REGISTRY = Registry()
    return _REGISTRY
