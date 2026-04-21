"""TableMixin — all table fill/manipulation logic for the DOCX renderer."""
import copy
import logging
from typing import Any, Dict

from docx.oxml.ns import qn
from docx.oxml import OxmlElement

from imdrf_coder import strip_imdrf_code
from rendering._helpers import stringify, PH_RE

logger = logging.getLogger(__name__)


class TableMixin:
    """Provides all table-related fill methods for PSURTemplateRenderer."""

    # ==================================================================
    # Master dispatcher
    # ==================================================================
    def _fill_all_tables(self, psur: Dict[str, Any], values: Dict[str, str]):
        """Fill data into all tables by detecting content, not index."""
        sections = psur.get("sections", {})
        stats = psur.get("_statistics", {}) or {}

        sec_c = sections.get("C_volume_of_sales_and_population_exposure", {})
        t1_data = sec_c.get("table_1_sales_by_region", {})
        t1_fmt = t1_data.get("annual_format", t1_data) if isinstance(t1_data, dict) else {}
        sales_rows = t1_fmt.get("rows", []) if isinstance(t1_fmt, dict) else []
        date_ranges = t1_fmt.get("date_ranges", []) if isinstance(t1_fmt, dict) else []

        # ── Deterministic override: rebuild sales rows from _statistics ──
        # The LLM may emit a partial set of regions; the template has 11
        # fixed slots that must all reconcile to Worldwide. Use the pre-
        # computed section_c_region_rows when available so totals always
        # add up and no slot is silently em-dashed.
        det_rows = stats.get("section_c_region_rows") or []
        if det_rows:
            total_units = stats.get("total_units_sold", 0) or 0
            sales_rows = [
                {
                    "region": r.get("region", ""),
                    # Pull the 3 historical 12-month units directly from
                    # statistics so the template's "Preceding 12-Month"
                    # columns are filled deterministically when DB history
                    # is available.
                    "preceding_12_month_periods": [
                        r.get("units_p1"),
                        r.get("units_p2"),
                        r.get("units_p3"),
                    ],
                    "current_data_collection_period": r.get("units", 0),
                    "percent_of_global_sales": (
                        round((r.get("units", 0) / total_units) * 100, 1)
                        if total_units > 0 else 0.0
                    ),
                }
                for r in det_rows
            ]
            # Surface period header labels for downstream use (renderer can
            # optionally stamp them into the Table 1 column headers).
            date_ranges = stats.get("section_c_period_labels") or date_ranges

        sec_d = sections.get("D_information_on_serious_incidents", {})
        sec_e = sections.get("E_customer_feedback", {})
        sec_f = sections.get("F_product_complaint_types_counts_and_rates", {})
        t7_data = sec_f.get("table_7_complaint_rate_and_count", {})
        t7_fmt = t7_data.get("annual_format", t7_data) if isinstance(t7_data, dict) else {}
        complaint_rows = t7_fmt.get("rows", []) if isinstance(t7_fmt, dict) else []
        grand_total = t7_fmt.get("grand_total", {}) if isinstance(t7_fmt, dict) else {}

        # ── Deterministic override: rebuild Table 7 rows + grand total ──
        # The LLM-built rows can drop uncoded complaints, breaking the
        # Grand Total = total_complaints reconciliation expected by auditors.
        # Use the pre-computed table7_rows when available.
        det_t7 = stats.get("table7_rows") or []
        if det_t7:
            complaint_rows = [
                {
                    "harm": r.get("harm", "No Harm"),
                    "medical_device_problem": r.get("medical_device_problem", ""),
                    "current_12_month_complaint_count": r.get("complaint_count", 0),
                    "current_12_month_complaint_rate": r.get("complaint_percentage", 0.0),
                    "max_expected_rate_of_occurrence_from_ract": (
                        r.get("ract_max_expected_rate")
                        if r.get("ract_max_expected_rate") is not None
                        else None
                    ),
                }
                for r in det_t7
            ]
            t7_total_count = sum(r.get("complaint_count", 0) for r in det_t7)
            wu = stats.get("total_units_sold", 0) or 0
            grand_total = {
                "complaint_count": t7_total_count,
                "complaint_rate": (
                    round((t7_total_count / wu) * 100, 4) if wu > 0 else 0.0
                ),
            }


        sec_h = sections.get("H_information_from_fsca", {})
        sec_i = sections.get("I_corrective_and_preventive_actions", {})
        sec_k = sections.get("K_review_of_external_databases_and_registries", {})
        sec_l = sections.get("L_pmcf", {})

        filled_tables: set = set()
        sales_table_filled = False
        complaint_table_filled = False

        for i, table in enumerate(self.doc.tables):
            header_text = self._get_table_header_text(table)
            ht = header_text.lower()
            logger.info(f"Table {i}: header='{header_text[:120]}'")

            if i in filled_tables:
                self._fill_table_placeholders(table, values)
                continue

            # ── IMDRF tables FIRST ──
            if "health impact" in ht or "annex f" in ht:
                logger.info(f"  → Table {i} matched: HEALTH_IMPACT")
                rows = sec_d.get("table_4_health_impact_by_investigation_conclusion", [])
                self._fill_incident_table(table, rows)
                filled_tables.add(i)

            elif "imdrf" in ht and ("annex a" in ht or "problem code" in ht):
                logger.info(f"  → Table {i} matched: INCIDENT_ANNEX_A")
                rows = sec_d.get("table_2_serious_incidents_by_imdrf_annex_a_by_region", [])
                self._fill_incident_table(table, rows)
                filled_tables.add(i)

            elif "imdrf" in ht and ("annex c" in ht or "cause code" in ht or "cause" in ht):
                logger.info(f"  → Table {i} matched: INCIDENT_ANNEX_C")
                rows = sec_d.get("table_3_serious_incidents_by_imdrf_annex_c_investigation_findings_by_region", [])
                self._fill_incident_table(table, rows)
                filled_tables.add(i)

            # ── Sales tables ──
            elif ("region" in ht and "imdrf" not in ht
                    and ("sales" in ht or "units" in ht or "volume" in ht
                         or "devices sold" in ht or "distributed" in ht
                         or "percent of global" in ht)):
                logger.info(f"  → Table {i} matched: SALES (filled={sales_table_filled})")
                if not sales_table_filled:
                    self._fill_sales_table(table, sales_rows, date_ranges, values)
                    sales_table_filled = True
                else:
                    self._clear_duplicate_table(table)
                filled_tables.add(i)

            elif "feedback" in ht and ("type" in ht or "source" in ht or "customer" in ht):
                logger.info(f"  → Table {i} matched: CUSTOMER_FEEDBACK")
                rows = sec_e.get("table_6_feedback_by_type_and_source", [])
                self._fill_generic_table(table, rows, start_row=1)
                filled_tables.add(i)

            elif (("harm" in ht and "medical device problem" in ht)
                    or ("complaint" in ht and ("rate" in ht or "count" in ht
                        or "harm" in ht or "mdp" in ht))):
                logger.info(f"  → Table {i} matched: COMPLAINT_RATE (filled={complaint_table_filled})")
                if not complaint_table_filled:
                    self._fill_complaint_table(table, complaint_rows, grand_total, values)
                    complaint_table_filled = True
                else:
                    self._clear_duplicate_table(table)
                filled_tables.add(i)

            elif "fsca" in ht or "field safety" in ht:
                logger.info(f"  → Table {i} matched: FSCA")
                rows = sec_h.get("table_8_fsca_initiated_current_period_and_open_fscas", [])
                self._fill_generic_table(table, rows, start_row=1)
                filled_tables.add(i)

            elif "capa" in ht or ("corrective" in ht and "preventive" in ht):
                logger.info(f"  → Table {i} matched: CAPA")
                rows = sec_i.get("table_9_capa_initiated_current_reporting_period", [])
                self._fill_generic_table(table, rows, start_row=1)
                filled_tables.add(i)

            elif (("database" in ht and ("registr" in ht or "total matches" in ht))
                    or "maude" in ht or "bfarm" in ht or "mhra" in ht):
                logger.info(f"  → Table {i} matched: EXTERNAL_DB")
                rows = sec_k.get("table_10_adverse_events_and_recalls", [])
                self._fill_generic_table(table, rows, start_row=1)
                filled_tables.add(i)

            elif "pmcf" in ht or "post-market clinical" in ht or "post market clinical" in ht:
                logger.info(f"  → Table {i} matched: PMCF")
                rows = sec_l.get("table_11_pmcf_activities", [])
                self._fill_generic_table(table, rows, start_row=1)
                filled_tables.add(i)

            else:
                logger.info(f"  → Table {i}: UNMATCHED")

            self._fill_table_placeholders(table, values)

    # ==================================================================
    # Header detection
    # ==================================================================
    def _get_table_header_text(self, table) -> str:
        """Get text from the first 2 rows of a table for identification."""
        tbl_el = table._tbl
        trs = list(tbl_el.iterchildren(qn("w:tr")))
        texts = []
        for tr in trs[:2]:
            texts.append(self._get_row_text(tr))
        return " ".join(texts)

    # ==================================================================
    # Placeholder substitution in table cells
    # ==================================================================
    def _fill_table_placeholders(self, table, values: Dict[str, str]):
        """Replace {{PLACEHOLDER}} markers in all table cells via raw XML."""
        tbl_el = table._tbl
        for tr in tbl_el.iterchildren(qn("w:tr")):
            for tc in tr.iterchildren(qn("w:tc")):
                for p in tc.iterchildren(qn("w:p")):
                    runs = p.findall(f".//{qn('w:r')}")
                    if not runs:
                        continue
                    texts = []
                    for r in runs:
                        t_el = r.find(qn("w:t"))
                        if t_el is not None and t_el.text:
                            texts.append(t_el.text)
                    full = "".join(texts)
                    if "{{" not in full:
                        continue
                    new_full = PH_RE.sub(
                        lambda m: values.get(m.group(1), m.group(0)), full)
                    if new_full == full:
                        continue
                    first = True
                    for r in runs:
                        t_el = r.find(qn("w:t"))
                        if t_el is not None:
                            if first:
                                t_el.text = new_full
                                t_el.set(qn("xml:space"), "preserve")
                                first = False
                            else:
                                t_el.text = ""

    # ==================================================================
    # Duplicate table clearing
    # ==================================================================
    def _clear_duplicate_table(self, table):
        """Clear data cells in a duplicate table with '—'."""
        tbl_el = table._tbl
        trs = list(tbl_el.iterchildren(qn("w:tr")))
        for tr in trs[2:]:
            tcs = list(tr.iterchildren(qn("w:tc")))
            for tc in tcs[1:]:
                self._set_cell_text(tc, "—")

    # ==================================================================
    # Sales table
    # ==================================================================
    def _fill_sales_table(self, table, sales_rows: list,
                          date_ranges: list, values: Dict[str, str]):
        """Fill the sales-by-region table."""
        tbl_el = table._tbl
        trs = list(tbl_el.iterchildren(qn("w:tr")))

        region_data = {}
        for row in sales_rows:
            if not isinstance(row, dict):
                continue
            region = row.get("region", "")
            preceding = row.get("preceding_12_month_periods", [])
            if not isinstance(preceding, list):
                preceding = []
            current = row.get("current_data_collection_period", "")
            pct = row.get("percent_of_global_sales", "")

            def _fmt_units(v):
                """Match the 'current' column's number formatting for trend cells."""
                if v is None or v == "":
                    return "—"
                if isinstance(v, (int, float)):
                    return f"{int(v):,}" if float(v) >= 0 else stringify(v)
                return stringify(v)

            region_data[region] = {
                "p1": _fmt_units(preceding[0]) if len(preceding) > 0 else "—",
                "p2": _fmt_units(preceding[1]) if len(preceding) > 1 else "—",
                "p3": _fmt_units(preceding[2]) if len(preceding) > 2 else "—",
                "current": f"{current:,}" if isinstance(current, (int, float)) else ("—" if not current else stringify(current)),
                "pct": f"{pct}%" if pct else "—",
            }

        template_regions = [
            "EEA+TR+XI", "Australia", "Brazil", "Canada", "China",
            "Japan", "UK", "United States",
            "Unknown / Unattributed",
            "Rest of World", "Worldwide",
        ]

        for ri, region_name in enumerate(template_regions):
            data_row_idx = ri + 3
            if data_row_idx >= len(trs):
                break

            tr = trs[data_row_idx]
            tcs = list(tr.iterchildren(qn("w:tc")))

            if region_name is None:
                self._set_cell_text(tcs[0], "—")
                for tc in tcs[1:]:
                    self._set_cell_text(tc, "—")
                continue

            data = region_data.get(region_name)
            if not data:
                for rk, rv in region_data.items():
                    if region_name.lower() in rk.lower() or rk.lower() in region_name.lower():
                        data = rv
                        break

            if data and len(tcs) >= 6:
                # Stamp the region label so repurposed slots (e.g. the blank
                # divider row now used for "Unknown / Unattributed") get a
                # visible name. Existing rows already have correct labels in
                # the template; overwriting with the same string is a no-op.
                self._set_cell_text(tcs[0], region_name)
                self._set_cell_text(tcs[1], data["p1"])
                self._set_cell_text(tcs[2], data["p2"])
                self._set_cell_text(tcs[3], data["p3"])
                self._set_cell_text(tcs[4], data["current"])
                self._set_cell_text(tcs[5], data["pct"])
            elif len(tcs) >= 6:
                for tc in tcs[1:]:
                    self._set_cell_text(tc, "—")

    # ==================================================================
    # Complaint table
    # ==================================================================
    def _fill_complaint_table(self, table, complaint_rows: list,
                               grand_total: dict, values: Dict[str, str]):
        """Fill the complaint rate table."""
        tbl_el = table._tbl
        trs = list(tbl_el.iterchildren(qn("w:tr")))

        if not complaint_rows:
            if len(trs) > 2:
                tcs = list(trs[2].iterchildren(qn("w:tc")))
                if tcs:
                    self._set_cell_text(tcs[0], "None reported during this period.")
            return

        harm_groups: Dict[str, list] = {}
        for row in complaint_rows:
            if not isinstance(row, dict):
                continue
            harm = row.get("harm", "No Health Consequence or Impact")
            harm_groups.setdefault(harm, []).append(row)

        noharm_key = None
        other_harm_keys = []
        for hk in harm_groups:
            if "no health" in hk.lower() or "no harm" in hk.lower():
                noharm_key = hk
            else:
                other_harm_keys.append(hk)

        def _fill_mdp_row(tr_el, mdp_row: dict):
            tcs = list(tr_el.iterchildren(qn("w:tc")))
            if len(tcs) < 3:
                return
            mdp_name = strip_imdrf_code(mdp_row.get("medical_device_problem", ""))
            self._set_cell_text(tcs[0], mdp_name)
            count = mdp_row.get("current_12_month_complaint_count", "")
            rate = mdp_row.get("current_12_month_complaint_rate", "")
            count_str = f"{count:,}" if isinstance(count, (int, float)) else stringify(count)
            rate_str = f"{rate}%" if rate else ""
            self._set_cell_text(tcs[1], f"{count_str} ({rate_str})" if rate_str else count_str)
            ract = mdp_row.get("max_expected_rate_of_occurrence_from_ract")
            self._set_cell_text(tcs[2], stringify(ract) if ract else "—")

        def _fill_harm_header_row(tr_el, harm_name: str, total_count=None):
            tcs = list(tr_el.iterchildren(qn("w:tc")))
            if tcs:
                self._set_cell_text(tcs[0], strip_imdrf_code(harm_name))
            if len(tcs) >= 2 and total_count is not None:
                self._set_cell_text(tcs[1], f"{total_count:,}" if isinstance(total_count, (int, float)) else stringify(total_count))

        # Harm A (rows 2-4)
        if other_harm_keys:
            harm_a_key = other_harm_keys[0]
            harm_a_rows = harm_groups[harm_a_key]
            harm_a_total = sum(r.get("current_12_month_complaint_count", 0) for r in harm_a_rows)
            if len(trs) > 2:
                _fill_harm_header_row(trs[2], harm_a_key, harm_a_total)
            if len(trs) > 3 and len(harm_a_rows) > 0:
                _fill_mdp_row(trs[3], harm_a_rows[0])
            if len(trs) > 4 and len(harm_a_rows) > 1:
                _fill_mdp_row(trs[4], harm_a_rows[1])
            if len(harm_a_rows) > 2:
                insert_after = trs[4]
                for extra in harm_a_rows[2:]:
                    new_tr = copy.deepcopy(trs[3])
                    insert_after.addnext(new_tr)
                    _fill_mdp_row(new_tr, extra)
                    insert_after = new_tr
                trs = list(tbl_el.iterchildren(qn("w:tr")))
        else:
            if len(trs) > 2:
                tcs = list(trs[2].iterchildren(qn("w:tc")))
                if tcs:
                    self._set_cell_text(tcs[0], "N/A")

        # Harm B
        harm_b_start = None
        for idx, tr in enumerate(trs):
            text = self._get_row_text(tr)
            if "T7_HARM_B" in text and "MDP" not in text:
                harm_b_start = idx
                break

        if harm_b_start and len(other_harm_keys) > 1:
            harm_b_key = other_harm_keys[1]
            harm_b_rows = harm_groups[harm_b_key]
            harm_b_total = sum(r.get("current_12_month_complaint_count", 0) for r in harm_b_rows)
            _fill_harm_header_row(trs[harm_b_start], harm_b_key, harm_b_total)
            if harm_b_start + 1 < len(trs) and len(harm_b_rows) > 0:
                _fill_mdp_row(trs[harm_b_start + 1], harm_b_rows[0])
            if harm_b_start + 2 < len(trs) and len(harm_b_rows) > 1:
                _fill_mdp_row(trs[harm_b_start + 2], harm_b_rows[1])
        elif harm_b_start:
            tcs = list(trs[harm_b_start].iterchildren(qn("w:tc")))
            if tcs:
                self._set_cell_text(tcs[0], "N/A")

        # No Health Consequence section
        noharm_start = None
        for idx, tr in enumerate(trs):
            text = self._get_row_text(tr)
            if "No Health Consequence" in text:
                noharm_start = idx
                break

        if noharm_start is not None and noharm_key:
            noharm_rows = harm_groups[noharm_key]
            noharm_total = sum(r.get("current_12_month_complaint_count", 0) for r in noharm_rows)
            _fill_harm_header_row(trs[noharm_start], "No Health Consequence or Impact", noharm_total)

            mdp_slot_idx = noharm_start + 1
            filled = 0
            while mdp_slot_idx < len(trs) and filled < min(2, len(noharm_rows)):
                text = self._get_row_text(trs[mdp_slot_idx])
                if "Grand Total" in text:
                    break
                _fill_mdp_row(trs[mdp_slot_idx], noharm_rows[filled])
                filled += 1
                mdp_slot_idx += 1

            if len(noharm_rows) > 2:
                insert_after_idx = noharm_start + 2
                if insert_after_idx < len(trs):
                    insert_after = trs[insert_after_idx]
                    template_mdp_tr = trs[noharm_start + 1]
                    for extra_row in noharm_rows[2:]:
                        new_tr = copy.deepcopy(template_mdp_tr)
                        insert_after.addnext(new_tr)
                        _fill_mdp_row(new_tr, extra_row)
                        insert_after = new_tr
                    trs = list(tbl_el.iterchildren(qn("w:tr")))

        # Grand Total row
        for tr in reversed(trs):
            text = self._get_row_text(tr)
            if "Grand Total" in text:
                tcs = list(tr.iterchildren(qn("w:tc")))
                total_count = grand_total.get("complaint_count", "")
                total_rate = grand_total.get("complaint_rate", "")
                if len(tcs) >= 2:
                    count_str = f"{total_count:,}" if isinstance(total_count, (int, float)) else stringify(total_count)
                    rate_str = f"{total_rate}%" if total_rate else ""
                    self._set_cell_text(tcs[1], f"{count_str} ({rate_str})" if rate_str else count_str)
                break

    # ==================================================================
    # Incident table
    # ==================================================================
    def _fill_incident_table(self, table, rows: list):
        """Fill serious incident tables. If no data, write 'None reported'."""
        tbl_el = table._tbl
        trs = list(tbl_el.iterchildren(qn("w:tr")))

        if not rows:
            _region_keywords = (
                "EEA", "UK", "Worldwide", "United States", "US", "Rest of World",
                "Australia", "Canada", "Japan", "China", "Brazil",
            )
            for tr in trs[1:]:
                tcs = list(tr.iterchildren(qn("w:tc")))
                text = self._get_row_text(tr)
                if any(region in text for region in _region_keywords):
                    for tc in tcs[1:]:
                        self._set_cell_text(tc, "0")
                elif text.strip():
                    for tc in tcs[1:]:
                        self._set_cell_text(tc, "0")
                else:
                    if tcs:
                        self._set_cell_text(tcs[0], "None reported during this period.")
            return

        for row_data in rows:
            if not isinstance(row_data, dict):
                continue
            region = row_data.get("region", "")
            for tr in trs[1:]:
                text = self._get_row_text(tr)
                if region and region in text:
                    tcs = list(tr.iterchildren(qn("w:tc")))
                    col_keys = ["imdrf_code_and_term", "count", "rate", "complaint_number"]
                    for ci, key in enumerate(col_keys):
                        if ci + 1 < len(tcs):
                            val = row_data.get(key, "")
                            val_str = stringify(val) if val else "0"
                            if key == "imdrf_code_and_term":
                                val_str = strip_imdrf_code(val_str)
                            self._set_cell_text(tcs[ci + 1], val_str)

    # ==================================================================
    # Generic table fill
    # ==================================================================
    def _fill_generic_table(self, table, rows: list, start_row: int = 1):
        """Fill a simple table with dict rows."""
        tbl_el = table._tbl
        trs = list(tbl_el.iterchildren(qn("w:tr")))

        if not rows:
            if start_row < len(trs):
                tcs = list(trs[start_row].iterchildren(qn("w:tc")))
                if tcs:
                    self._set_cell_text(tcs[0], "None reported during this period.")
                    for tc in tcs[1:]:
                        self._set_cell_text(tc, "—")
            return

        if trs:
            header_tcs = list(trs[0].iterchildren(qn("w:tc")))
            headers = [self._get_cell_text(tc).lower().replace(" ", "_").replace("/", "_")
                       for tc in header_tcs]
        else:
            headers = []

        for ri, row_data in enumerate(rows):
            if not isinstance(row_data, dict):
                continue
            data_row_idx = start_row + ri
            if data_row_idx >= len(trs):
                template_tr = trs[start_row] if start_row < len(trs) else trs[-1]
                new_tr = copy.deepcopy(template_tr)
                tbl_el.append(new_tr)
                trs = list(tbl_el.iterchildren(qn("w:tr")))

            tr = trs[data_row_idx]
            tcs = list(tr.iterchildren(qn("w:tc")))

            row_keys = list(row_data.keys())
            for ci, tc in enumerate(tcs):
                val = None
                if ci < len(headers):
                    for rk, rv in row_data.items():
                        rk_norm = rk.lower().replace(" ", "_").replace("/", "_")
                        if rk_norm == headers[ci] or headers[ci] in rk_norm or rk_norm in headers[ci]:
                            val = rv
                            break
                if val is None and ci < len(row_keys):
                    val = row_data[row_keys[ci]]

                if val is not None:
                    self._set_cell_text(tc, stringify(val))

    # ==================================================================
    # XML cell helpers
    # ==================================================================
    def _set_cell_text(self, tc, text: str):
        """Set the text content of a table cell (w:tc), preserving formatting."""
        paragraphs = list(tc.iterchildren(qn("w:p")))
        if not paragraphs:
            return

        p = paragraphs[0]
        runs = p.findall(f".//{qn('w:r')}")

        if runs:
            t_el = runs[0].find(qn("w:t"))
            if t_el is None:
                t_el = OxmlElement("w:t")
                runs[0].append(t_el)
            t_el.text = text
            t_el.set(qn("xml:space"), "preserve")
            for r in runs[1:]:
                t_el2 = r.find(qn("w:t"))
                if t_el2 is not None:
                    t_el2.text = ""
        else:
            r = OxmlElement("w:r")
            t_el = OxmlElement("w:t")
            t_el.text = text
            t_el.set(qn("xml:space"), "preserve")
            r.append(t_el)
            p.append(r)

    def _get_cell_text(self, tc) -> str:
        """Get text content of a w:tc element."""
        texts = []
        for t in tc.iter(qn("w:t")):
            if t.text:
                texts.append(t.text)
        return "".join(texts).strip()

    def _get_row_text(self, tr) -> str:
        """Get concatenated text of all cells in a w:tr element."""
        texts = []
        for tc in tr.iterchildren(qn("w:tc")):
            texts.append(self._get_cell_text(tc))
        return " ".join(texts)
