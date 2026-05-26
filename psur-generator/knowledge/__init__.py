"""Unified PSUR Knowledge Layer.

Provides:
  - registry.py:    Typed rule registry loaded from knowledge/rules/*.json
  - retrieval.py:   Precision retrieval (filter + score) over the registry
  - skills.py:      Machine-loadable skill packs with deterministic invocation
  - provenance.py:  Versioning + per-rule provenance for audit trails

Single import surface: callers should use the symbols re-exported here.
"""
from knowledge.registry import (
    Rule,
    Registry,
    get_registry,
    KNOWLEDGE_VERSION,
)
from knowledge.retrieval import (
    Query,
    ScoredRule,
    retrieve,
    render_rules_for_prompt,
)
from knowledge.skills import (
    Skill,
    SkillRegistry,
    get_skill_registry,
    SkillContext,
)
from knowledge.provenance import (
    Provenance,
    build_meta_block,
)

__all__ = [
    "Rule",
    "Registry",
    "get_registry",
    "KNOWLEDGE_VERSION",
    "Query",
    "ScoredRule",
    "retrieve",
    "render_rules_for_prompt",
    "Skill",
    "SkillRegistry",
    "get_skill_registry",
    "SkillContext",
    "Provenance",
    "build_meta_block",
]
