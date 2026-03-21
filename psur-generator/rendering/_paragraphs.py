"""ParagraphMixin — paragraph/run replacement and chart insertion for DOCX renderer."""
import logging
from pathlib import Path
from typing import Dict

from docx.shared import Inches
from docx.oxml import OxmlElement

from rendering._helpers import PH_RE

logger = logging.getLogger(__name__)


class ParagraphMixin:
    """Provides paragraph-level fill, chart insertion, and header/footer update."""

    def _fill_paragraph(self, para, values: Dict[str, str]):
        """Replace {{PLACEHOLDER}} markers and [INSERT CHART] directives in a paragraph's runs."""
        full_text = para.text

        # [INSERT CHART] text-based directives
        upper_text = full_text.upper()
        if "[INSERT CHART" in upper_text:
            chart_key = None
            if "SALES" in upper_text or "VOLUME" in upper_text:
                chart_key = "sales_trend"
            elif "TREND" in upper_text or "UCL" in upper_text or "COMPLAINT RATE" in upper_text:
                chart_key = "trend_ucl"
            if chart_key and chart_key in self.chart_paths:
                chart_path = self.chart_paths[chart_key]
                for run in para.runs:
                    run.text = ""
                if Path(chart_path).exists():
                    run = para.add_run()
                    run.add_picture(str(chart_path), width=Inches(6))
                return

        if "{{" not in full_text:
            return

        # {{PLACEHOLDER}} style chart placeholders (legacy)
        chart_map = {
            "sales_trend": "C_SALES_CHART_PLACEHOLDER",
            "trend_ucl": "G_TREND_CHART_PLACEHOLDER",
        }
        for chart_key, chart_path in self.chart_paths.items():
            ph_name = chart_map.get(chart_key)
            if ph_name and f"{{{{{ph_name}}}}}" in full_text:
                for run in para.runs:
                    run.text = ""
                if Path(chart_path).exists():
                    run = para.add_run()
                    run.add_picture(str(chart_path), width=Inches(6))
                return

        self._replace_in_runs(para, values)

    def _insert_unplaced_charts(self, values: Dict[str, str]):
        """Insert charts that had no [INSERT CHART] or {{PLACEHOLDER}} in the template.

        Specifically handles the UCL trend chart which has no template placeholder.
        Inserts it after the G_TREND_NARRATIVE paragraph in Section G.
        """
        if "trend_ucl" not in self.chart_paths:
            return
        chart_path = self.chart_paths["trend_ucl"]
        if not Path(chart_path).exists():
            return

        # Check if the UCL chart was already placed by an [INSERT CHART] directive
        # Strategy: look for known Section G markers in order of reliability
        # 1. A paragraph whose text was replaced from {{G_TREND_NARRATIVE}}
        # 2. The heading "Overall Monthly Complaint Rate Trending"
        # 3. The heading containing "Trend" within Section G area
        insert_after_idx = None

        g_narrative = values.get("G_TREND_NARRATIVE", "")
        g_reports = values.get("G_TREND_REPORTS_SUMMARY", "")

        for i, para in enumerate(self.doc.paragraphs):
            text = para.text.strip()

            # Best match: the paragraph that was filled from G_TREND_NARRATIVE
            if g_narrative and text and g_narrative[:60] in text:
                insert_after_idx = i
                break

            # Fallback: find the "Trend Reporting" sub-heading (one before G_TREND_REPORTS_SUMMARY)
            if g_reports and text and g_reports[:60] in text:
                # Insert BEFORE the reports summary, so chart goes between narrative and reports
                insert_after_idx = i - 1 if i > 0 else i
                break

        if insert_after_idx is None:
            # Last resort: find Section G heading area
            for i, para in enumerate(self.doc.paragraphs):
                text = para.text.strip().upper()
                if "OVERALL MONTHLY COMPLAINT RATE TRENDING" in text:
                    insert_after_idx = i
                    break

        if insert_after_idx is None:
            logger.warning("Could not find Section G insertion point for UCL trend chart")
            return

        # Insert a new paragraph with the chart image after the target.
        # We add the paragraph via the document body so python-docx tracks
        # the part relationship correctly (avoids CT_Body.part AttributeError).
        target_para = self.doc.paragraphs[insert_after_idx]
        new_para_elem = OxmlElement("w:p")
        target_para._element.addnext(new_para_elem)

        # Re-fetch the paragraph list so the new element is wrapped properly
        # by python-docx with a valid .part reference.
        for p in self.doc.paragraphs:
            if p._element is new_para_elem:
                chart_para = p
                break
        else:
            logger.warning("Could not locate newly inserted paragraph for UCL chart")
            return

        run = chart_para.add_run()
        run.add_picture(str(chart_path), width=Inches(6))
        logger.info("Inserted UCL trend chart after paragraph %d", insert_after_idx)

    def _replace_in_runs(self, para, values: Dict[str, str]):
        """Replace placeholders across runs, handling split markers."""
        runs = para.runs
        if not runs:
            return

        full = "".join(r.text for r in runs)
        if "{{" not in full:
            return

        new_full = PH_RE.sub(lambda m: values.get(m.group(1), m.group(0)), full)

        if new_full == full:
            return

        runs[0].text = new_full
        for r in runs[1:]:
            r.text = ""

    def _update_headers(self, psur, values: Dict[str, str]):
        """Fill header/footer placeholders with product info."""
        if not self.doc or not self.doc.sections:
            return

        for section in self.doc.sections:
            for para in section.header.paragraphs:
                if "{{" in para.text:
                    self._replace_in_runs(para, values)
            for para in section.footer.paragraphs:
                if "{{" in para.text:
                    self._replace_in_runs(para, values)
