"""Provenance & versioning helpers.

Each generated PSUR JSON receives a ``_meta`` block that records:
  - knowledge_version: semver from knowledge/VERSION
  - rules_applied:     list of {id, framework, citation, version, hash} actually used
  - skills_invoked:    list of {name, version, ran_at}
  - generated_at:      ISO timestamp

The renderer ignores ``_meta`` blocks, so the DOCX output is unaffected
while the JSON remains fully auditable.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from knowledge.registry import Rule, get_registry, KNOWLEDGE_VERSION


@dataclass
class Provenance:
    rule_id: str
    framework: str
    citation: str
    version: str
    source_hash: Optional[str] = None
    section: Optional[str] = None

    @classmethod
    def from_rule(cls, rule: Rule, section: Optional[str] = None) -> "Provenance":
        return cls(
            rule_id=rule.id,
            framework=rule.framework,
            citation=rule.citation,
            version=rule.version,
            source_hash=rule.source_hash,
            section=section,
        )


def build_meta_block(
    rules_applied: Iterable[str],
    skills_invoked: Optional[List[Dict[str, Any]]] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the ``_meta`` provenance block to attach to a PSUR JSON output."""
    reg = get_registry()
    rules_block: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for rid in rules_applied:
        if not rid or rid in seen:
            continue
        seen.add(rid)
        r = reg.by_id(rid)
        if r is None:
            rules_block.append({"id": rid, "status": "unknown_id"})
            continue
        rules_block.append(
            {
                "id": r.id,
                "framework": r.framework,
                "citation": r.citation,
                "version": r.version,
                "source_hash": r.source_hash,
                "criticality": r.criticality,
            }
        )

    meta: Dict[str, Any] = {
        "knowledge_version": KNOWLEDGE_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rules_applied": rules_block,
        "skills_invoked": list(skills_invoked or []),
    }
    if extra:
        meta.update(extra)
    return meta


def collect_rule_ids_from_sections(sections: Dict[str, Any]) -> List[str]:
    """Walk a sections dict and extract every ``_meta.rules_applied`` entry."""
    ids: List[str] = []
    if not isinstance(sections, dict):
        return ids
    for sec in sections.values():
        if not isinstance(sec, dict):
            continue
        meta = sec.get("_meta") or {}
        applied = meta.get("rules_applied") or []
        for entry in applied:
            if isinstance(entry, str):
                ids.append(entry)
            elif isinstance(entry, dict) and entry.get("id"):
                ids.append(entry["id"])
    return ids
