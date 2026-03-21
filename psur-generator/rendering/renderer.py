"""
PSURTemplateRenderer — thin facade composing rendering mixins.

Clones FormQAR-054_template.docx and fills it from PSUR JSON data.
"""
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from docx import Document

from config import DOCX_TEMPLATE_PATH
from rendering._value_map import ValueMapMixin
from rendering._paragraphs import ParagraphMixin
from rendering._tables import TableMixin
from rendering._formatting import FormattingMixin

logger = logging.getLogger(__name__)


class PSURTemplateRenderer(ValueMapMixin, ParagraphMixin, TableMixin, FormattingMixin):
    """Clone the FormQAR-054 DOCX template and fill it from PSUR JSON."""

    def __init__(self, template_path: Optional[Path] = None):
        self.template_path = Path(template_path) if template_path else DOCX_TEMPLATE_PATH
        self.doc: Optional[Document] = None
        self.chart_paths: Dict[str, Path] = {}

    def render(self, psur: Dict[str, Any], output_path: Path,
               chart_paths: Optional[Dict[str, Path]] = None) -> None:
        """Clone the template, fill all placeholders, save to *output_path*."""
        output_path = Path(output_path)
        if not self.template_path.exists():
            raise FileNotFoundError(
                f"Template not found: {self.template_path}\n"
                "Ensure FormQAR-054_template.docx is in the constraints/ folder."
            )

        self.doc = Document(str(self.template_path))
        self.chart_paths = chart_paths or {}

        values = self._build_value_map(psur)

        # 1) Fill placeholders in body paragraphs
        for para in self.doc.paragraphs:
            self._fill_paragraph(para, values)

        # 1b) Insert charts that have no template placeholder
        self._insert_unplaced_charts(values)

        # 2) Fill data into all tables + placeholder substitution
        self._fill_all_tables(psur, values)

        # 3) Update headers/footers
        self._update_headers(psur, values)

        # 4) Insert Section A synthesized content (exec summary + B-R conclusion)
        self._insert_section_a_content(values)

        # 5) Apply universal formatting (fonts, sizes, bold/underline rules)
        self._apply_universal_formatting()

        output_path.parent.mkdir(parents=True, exist_ok=True)
        self.doc.save(str(output_path))


# ═════════════════════════════════════════════════════════════════════
# CLI ENTRY POINT
# ═════════════════════════════════════════════════════════════════════

def main():
    """CLI: render a PSUR JSON into a filled DOCX."""
    import argparse
    parser = argparse.ArgumentParser(description="Render PSUR JSON into FormQAR-054 template")
    parser.add_argument("json_path", help="Path to PSUR JSON file")
    parser.add_argument("-o", "--output", default="PSUR_RENDERED.docx", help="Output DOCX path")
    parser.add_argument("-t", "--template", default=None, help="Template DOCX path")
    parser.add_argument("--sales-chart", default=None, help="Sales trend chart image path")
    parser.add_argument("--trend-chart", default=None, help="UCL trend chart image path")
    args = parser.parse_args()

    with open(args.json_path) as f:
        psur = json.load(f)

    charts = {}
    if args.sales_chart:
        charts["sales_trend"] = Path(args.sales_chart)
    if args.trend_chart:
        charts["trend_ucl"] = Path(args.trend_chart)

    renderer = PSURTemplateRenderer(template_path=args.template)
    renderer.render(psur, Path(args.output), chart_paths=charts)
    print(f"Rendered: {args.output}")


if __name__ == "__main__":
    main()
