"""DOCX output validation mixin for PSURValidator."""
from pathlib import Path
from typing import Any, List, Tuple


class DocxChecksMixin:
    """Validate rendered DOCX structure, headings, TOC/page fields, and table headers."""

    def validate_docx(self, docx_path: Path) -> Tuple[bool, List[str]]:
        """Validate rendered DOCX structure, section headings, TOC/page fields, and table headers."""
        errors: List[str] = []

        try:
            from docx import Document
        except Exception as e:
            return False, [f"DOCX: python-docx not available ({e})"]

        docx_path = Path(docx_path)
        if not docx_path.exists():
            return False, [f"DOCX: file not found: {docx_path}"]

        doc = Document(str(docx_path))
        paragraphs = [p.text.strip() for p in doc.paragraphs if p.text and p.text.strip()]
        package_xml_parts: List[str] = []
        try:
            for part in doc.part.package.parts:
                part_name = str(getattr(part, "partname", ""))
                if not part_name.endswith(".xml"):
                    continue
                blob = getattr(part, "blob", b"")
                if isinstance(blob, (bytes, bytearray)):
                    package_xml_parts.append(blob.decode("utf-8", errors="ignore"))
        except Exception:
            package_xml_parts = []
        package_xml = "\n".join(package_xml_parts)

        # 1) Section headings sequence (A-M) must exist in order
        expected_section_prefixes = [
            "Section A:",
            "Section B:",
            "Section C:",
            "Section D:",
            "Section E:",
            "Section F:",
            "Section G:",
            "Section H:",
            "Section I:",
            "Section J:",
            "Section K:",
            "Section L:",
            "Section M:",
        ]

        last_idx = -1
        for prefix in expected_section_prefixes:
            try:
                idx = next(i for i, t in enumerate(paragraphs) if t.startswith(prefix))
            except StopIteration:
                errors.append(f"DOCX_STRUCTURE: Missing section heading starting with '{prefix}'")
                continue
            if idx <= last_idx:
                errors.append(f"DOCX_STRUCTURE: Section heading order incorrect at '{prefix}'")
            last_idx = idx

        # 2) TOC field presence
        if "TOC" not in package_xml:
            errors.append("DOCX_STRUCTURE: Table of Contents field not detected in DOCX XML")

        # 3) PAGE field presence in footer/header
        if "PAGE" not in package_xml:
            errors.append("DOCX_STRUCTURE: PAGE field not detected; page numbering may be missing")

        # 4) Key table header fidelity in rendered tables
        expected_table_headers = {
            "Table 1": {"Region"},
            "Table 2": {"Region"},
            "Table 3": {"Region"},
            "Table 4": {"IMDRF"},
            "Table 6": {"Feedback Type", "Source"},
            "Table 7": {"Harm", "Medical Device Problem"},
            "Table 8": {"Type of action"},
            "Table 9": {"CAPA Number"},
            "Table 10": {"Database/Registry", "Total matches"},
            "Table 11": {"Specific PMCF Activit"},
        }

        blocks: List[Tuple[str, Any]] = []
        for child in doc._element.body.iterchildren():
            tag = child.tag.split("}")[-1]
            if tag == "p":
                text = "".join(t.text or "" for t in child.iter() if t.tag.split("}")[-1] == "t").strip()
                blocks.append(("p", text))
            elif tag == "tbl":
                blocks.append(("t", child))

        for table_prefix, required_headers in expected_table_headers.items():
            title_index = None
            for i, (kind, payload) in enumerate(blocks):
                if kind == "p" and payload.startswith(table_prefix):
                    title_index = i
                    break
            if title_index is None:
                errors.append(f"DOCX_TABLE: Missing table starting with '{table_prefix}'")
                continue

            tbl_xml = None
            for i in range(title_index + 1, len(blocks)):
                if blocks[i][0] == "t":
                    tbl_xml = blocks[i][1]
                    break
            if tbl_xml is None:
                errors.append(f"DOCX_TABLE: No table found after heading '{table_prefix}'")
                continue

            header_cells = []
            tr = next((c for c in tbl_xml.iterchildren() if c.tag.split("}")[-1] == "tr"), None)
            if tr is not None:
                for tc in tr.iterchildren():
                    if tc.tag.split("}")[-1] != "tc":
                        continue
                    txt = "".join(t.text or "" for t in tc.iter() if t.tag.split("}")[-1] == "t").strip()
                    header_cells.append(txt)

            normalized = {h.strip() for h in header_cells if h and h.strip()}
            missing = [
                h for h in required_headers
                if not any(h in cell for cell in normalized)
            ]
            if missing:
                errors.append(
                    f"DOCX_TABLE: '{table_prefix}' missing expected columns: {missing}"
                )

        return (len(errors) == 0, errors)
