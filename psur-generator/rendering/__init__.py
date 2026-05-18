"""rendering - PSUR DOCX rendering package.

Public API:
    from rendering import PSURTemplateRenderer
"""
from rendering.renderer import PSURTemplateRenderer


def _period_from_psur(psur):
    cover = psur.get("psur_cover_page", {}) if isinstance(psur, dict) else {}
    doc_info = cover.get("document_information", {}) if isinstance(cover, dict) else {}
    period = doc_info.get("data_collection_period", {}) if isinstance(doc_info, dict) else {}
    stats_period = (psur.get("_statistics", {}) or {}).get("surveillance_period", {}) if isinstance(psur, dict) else {}
    start = period.get("start_date") or stats_period.get("start_date") or ""
    end = period.get("end_date") or stats_period.get("end_date") or ""
    return start, end


_original_render = PSURTemplateRenderer.render


def _render_with_table_skills(self, psur, *args, **kwargs):
    """Apply deterministic PSUR table skills immediately before DOCX render."""
    try:
        from deterministic_tables import apply_psur_table_skills

        stats = psur.get("_statistics", {}) if isinstance(psur, dict) else {}
        start_date, end_date = _period_from_psur(psur)
        if stats and not psur.get("_skill_tables_applied"):
            apply_psur_table_skills(
                psur,
                stats=stats,
                parsed_data=psur.get("_parsed_data", {}) if isinstance(psur, dict) else {},
                start_date=start_date,
                end_date=end_date,
            )
    except Exception:
        # Rendering must remain possible for legacy JSON files even when the
        # deterministic table helper cannot infer enough context.
        pass
    return _original_render(self, psur, *args, **kwargs)


PSURTemplateRenderer.render = _render_with_table_skills

__all__ = ["PSURTemplateRenderer"]
