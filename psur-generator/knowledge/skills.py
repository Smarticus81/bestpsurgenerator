"""Skill registry & deterministic invocation.

Skills are pre-LLM helpers that wrap canonical algorithms (e.g. sales
aggregation, IMDRF coding, table construction). Each skill has:

  - name, version, description
  - when_to_use: trigger predicate over a SkillContext
  - invoke: deterministic function (ctx) -> ctx
  - inputs / outputs: keys the skill reads/writes on the context

Skill manifests live under ``knowledge/skills/*.json``; their ``invoke``
function is wired in ``SKILL_IMPLEMENTATIONS`` below by name. This keeps
the manifests inspectable while preserving Python-native execution.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


SKILLS_DIR = Path(__file__).resolve().parent / "skills"


@dataclass
class SkillContext:
    """Mutable context passed through the skill chain.

    Attributes are kept loose intentionally — pipeline phases attach what
    they have (statistics, parsed_data, device_context, etc.) and skills
    read/write the keys declared in their manifest.
    """
    data: Dict[str, Any] = field(default_factory=dict)
    invocations: List[Dict[str, Any]] = field(default_factory=list)

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    def record(self, name: str, version: str, status: str = "ok",
               details: Optional[Dict[str, Any]] = None) -> None:
        self.invocations.append({
            "name": name,
            "version": version,
            "status": status,
            "ran_at": datetime.now(timezone.utc).isoformat(),
            "details": details or {},
        })


SkillImpl = Callable[[SkillContext], SkillContext]


@dataclass
class Skill:
    name: str
    version: str
    description: str
    when_to_use: Optional[str] = None  # regex or python expr over ctx.data
    inputs: List[str] = field(default_factory=list)
    outputs: List[str] = field(default_factory=list)
    impl_name: Optional[str] = None
    docs_path: Optional[str] = None
    enabled: bool = True

    def should_invoke(self, ctx: SkillContext) -> bool:
        if not self.enabled:
            return False
        # All declared inputs must be present (truthy or explicitly False)
        for k in self.inputs:
            if k not in ctx.data:
                return False
        if not self.when_to_use:
            return True
        # Try as Python expr first (safe-ish), fall back to keyword match
        try:
            return bool(eval(  # noqa: S307
                self.when_to_use,
                {"__builtins__": {}},
                dict(ctx.data),
            ))
        except Exception:
            return bool(re.search(self.when_to_use, json.dumps(ctx.data, default=str)))


class SkillRegistry:
    """Loads skill manifests and dispatches invocations."""

    def __init__(self, skills_dir: Path = SKILLS_DIR):
        self._dir = skills_dir
        self._skills: Dict[str, Skill] = {}
        self._impls: Dict[str, SkillImpl] = {}
        self._load()

    def _load(self) -> None:
        if not self._dir.exists():
            return
        for fp in sorted(self._dir.glob("*.json")):
            if fp.name.startswith("__"):
                continue
            try:
                with fp.open(encoding="utf-8") as f:
                    payload = json.load(f)
            except Exception as exc:  # pragma: no cover
                import sys
                print(f"[knowledge.skills] Failed to load {fp.name}: {exc}",
                      file=sys.stderr)
                continue
            try:
                skill = Skill(
                    name=payload["name"],
                    version=payload.get("version", "0.0.0"),
                    description=payload.get("description", ""),
                    when_to_use=payload.get("when_to_use"),
                    inputs=list(payload.get("inputs", [])),
                    outputs=list(payload.get("outputs", [])),
                    impl_name=payload.get("impl"),
                    docs_path=payload.get("docs"),
                    enabled=bool(payload.get("enabled", True)),
                )
            except KeyError as exc:
                import sys
                print(f"[knowledge.skills] Malformed manifest {fp.name}: missing {exc}",
                      file=sys.stderr)
                continue
            self._skills[skill.name] = skill

    # ── Implementation wiring ─────────────────────────────────────

    def register_impl(self, name: str, fn: SkillImpl) -> None:
        self._impls[name] = fn

    def get(self, name: str) -> Optional[Skill]:
        return self._skills.get(name)

    def all(self) -> List[Skill]:
        return list(self._skills.values())

    # ── Dispatch ──────────────────────────────────────────────────

    def invoke(self, name: str, ctx: SkillContext) -> SkillContext:
        skill = self.get(name)
        if skill is None:
            ctx.record(name, "0.0.0", status="not_found")
            return ctx
        if not skill.should_invoke(ctx):
            ctx.record(skill.name, skill.version, status="skipped")
            return ctx
        impl = self._impls.get(skill.impl_name or skill.name)
        if impl is None:
            ctx.record(skill.name, skill.version, status="no_impl")
            return ctx
        try:
            ctx = impl(ctx) or ctx
            ctx.record(skill.name, skill.version, status="ok")
        except Exception as exc:  # pragma: no cover
            ctx.record(skill.name, skill.version, status="error",
                       details={"error": str(exc)})
        return ctx

    def invoke_all_applicable(self, ctx: SkillContext) -> SkillContext:
        for skill in self._skills.values():
            if skill.should_invoke(ctx):
                impl = self._impls.get(skill.impl_name or skill.name)
                if impl is None:
                    ctx.record(skill.name, skill.version, status="no_impl")
                    continue
                try:
                    ctx = impl(ctx) or ctx
                    ctx.record(skill.name, skill.version, status="ok")
                except Exception as exc:  # pragma: no cover
                    ctx.record(skill.name, skill.version, status="error",
                               details={"error": str(exc)})
        return ctx


# ── Singleton + default impl registration ───────────────────────────

_REGISTRY: Optional[SkillRegistry] = None


def get_skill_registry() -> SkillRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = SkillRegistry()
        _register_default_impls(_REGISTRY)
    return _REGISTRY


def _register_default_impls(reg: SkillRegistry) -> None:
    """Bind manifest names to their Python implementations.

    Each impl is a thin shim around existing modules so the canonical
    algorithm lives in one place and the skill layer just orchestrates.
    """

    # ── psur-sales-aggregate ────────────────────────────────────
    def _impl_sales_aggregate(ctx: SkillContext) -> SkillContext:
        try:
            from statistics_tables import (
                determine_12month_periods_from_dates,
                calculate_region_percentages,
            )
        except Exception:
            return ctx
        sales = ctx.get("sales_data") or {}
        start = ctx.get("reporting_period_start")
        end = ctx.get("reporting_period_end")
        if not (sales and start and end):
            return ctx
        try:
            periods = determine_12month_periods_from_dates(start, end)
            ctx.set("sales_periods", periods)
        except Exception:
            pass
        try:
            regions = calculate_region_percentages(sales)
            ctx.set("sales_region_percentages", regions)
        except Exception:
            pass
        return ctx

    # ── psur-imdrf-classify ─────────────────────────────────────
    def _impl_imdrf_classify(ctx: SkillContext) -> SkillContext:
        try:
            from imdrf_coder import code_complaints_with_imdrf
        except Exception:
            return ctx
        complaints = ctx.get("complaints") or []
        if not complaints:
            return ctx
        # Only invoke if any complaint is missing IMDRF coding
        needs_coding = any(
            not (c.get("imdrf_annex_a") or c.get("imdrf_problem_term"))
            or not (c.get("imdrf_annex_f") or c.get("imdrf_harm_term"))
            for c in complaints if isinstance(c, dict)
        )
        if not needs_coding:
            return ctx
        try:
            coded = code_complaints_with_imdrf(complaints)
            ctx.set("complaints", coded)
            ctx.set("imdrf_coded_count", len(coded))
        except Exception:
            pass
        return ctx

    # ── psur-tables ─────────────────────────────────────────────
    def _impl_tables(ctx: SkillContext) -> SkillContext:
        # Tables are largely built inside statistics.py; this skill records
        # that the FormQAR-054 table construction protocol was applied.
        stats = ctx.get("statistics")
        if isinstance(stats, dict):
            ctx.set(
                "tables_built",
                {
                    "table1_present": bool(stats.get("section_c_region_rows")),
                    "table7_present": bool(stats.get("table7_rows")
                                           or stats.get("harm_by_imdrf")),
                },
            )
        return ctx

    # ── psur-validate ───────────────────────────────────────────
    def _impl_validate(ctx: SkillContext) -> SkillContext:
        # Hooks into existing validator. The validator is always run by
        # main.py; this skill just records that the protocol checklist
        # was applied (and exposes a lightweight summary if available).
        report = ctx.get("validation_report")
        if isinstance(report, dict):
            ctx.set("validation_summary", {
                "errors": len(report.get("errors", [])),
                "warnings": len(report.get("warnings", [])),
            })
        return ctx

    reg.register_impl("psur-sales-aggregate", _impl_sales_aggregate)
    reg.register_impl("psur-imdrf-classify", _impl_imdrf_classify)
    reg.register_impl("psur-tables", _impl_tables)
    reg.register_impl("psur-validate", _impl_validate)
