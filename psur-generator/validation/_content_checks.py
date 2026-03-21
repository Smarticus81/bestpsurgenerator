"""Content integrity and data checks mixin for PSURValidator."""
import re
from typing import Any, Dict, List


class ContentChecksMixin:
    """Content integrity, enums, SRN format, cover page, IMDRF, Table 7, empty cells."""

    def _check_content_integrity(self, psur: Dict[str, Any], stats: Dict[str, Any]) -> List[str]:
        """Cross-check PSUR content against pre-calculated statistics."""
        errors = []
        sections = psur.get("sections", {})

        # Check Section C: EEA aggregate
        sec_c = sections.get("C_volume_of_sales_and_population_exposure", {})
        table1 = sec_c.get("table_1_sales_by_region", {})
        annual = table1.get("annual_format", {})
        rows = annual.get("rows", [])

        eea_row = None
        worldwide_row = None
        for row in rows:
            region = row.get("region", "")
            if "EEA" in region or "EU" in region:
                eea_row = row
            if region == "Worldwide":
                worldwide_row = row

        if worldwide_row:
            ww_current = worldwide_row.get("current_data_collection_period", 0)
            expected_total = stats.get("total_units_sold", 0)
            if ww_current and expected_total and ww_current != expected_total:
                errors.append(
                    f"CONTENT: Section C worldwide total ({ww_current:,}) "
                    f"does not match statistics ({expected_total:,})"
                )

        eea_expected = stats.get("eea_units", 0)
        if eea_row and eea_expected > 0:
            eea_current = eea_row.get("current_data_collection_period", 0)
            if eea_current and abs(eea_current - eea_expected) > 1:
                errors.append(
                    f"CONTENT: Section C EEA total ({eea_current:,}) "
                    f"does not match pre-computed EEA aggregate ({eea_expected:,})"
                )

        # Check UK total if UK market data exists
        uk_expected = stats.get("uk_units", 0)
        if uk_expected > 0:
            uk_row = None
            for row in rows:
                region = row.get("region", "")
                if region == "UK":
                    uk_row = row
                    break
            if uk_row:
                uk_current = uk_row.get("current_data_collection_period", 0)
                if uk_current and abs(uk_current - uk_expected) > 1:
                    errors.append(
                        f"CONTENT: Section C UK total ({uk_current:,}) "
                        f"does not match pre-computed UK aggregate ({uk_expected:,})"
                    )
            else:
                errors.append(
                    "CONTENT: Section C is missing a UK region row but UK sales "
                    f"data exists ({uk_expected:,} units)"
                )

        if not stats.get("has_previous_period_data", False):
            for row in rows:
                preceding = row.get("preceding_12_month_periods", [])
                if preceding and any(v is not None and v > 0 for v in preceding):
                    region = row.get("region", "unknown")
                    errors.append(
                        f"CONTENT: Section C has fabricated historical data for '{region}' "
                        f"but no previous period data was provided"
                    )
                    break

        # Check Section F: complaint counts
        sec_f = sections.get("F_product_complaint_types_counts_and_rates", {})
        table7 = sec_f.get("table_7_complaint_rate_and_count", {})
        f_annual = table7.get("annual_format", {})
        f_rows = f_annual.get("rows", [])

        imdrf_totals = {}
        for row in f_rows:
            mdp = row.get("medical_device_problem", "")
            count = row.get("current_12_month_complaint_count")
            if mdp and count is not None:
                for stat_code in stats.get("complaints_by_imdrf", {}):
                    code_prefix = stat_code.split(" - ")[0] if " - " in stat_code else stat_code
                    if code_prefix in mdp or mdp.split(" (")[0] in stat_code:
                        imdrf_totals[stat_code] = imdrf_totals.get(stat_code, 0) + count

        for code, total in imdrf_totals.items():
            expected = stats.get("complaints_by_imdrf", {}).get(code, 0)
            if total > expected:
                errors.append(
                    f"CONTENT: Section F complaint count for '{code}' "
                    f"({total}) exceeds statistics total ({expected}) - possible double-counting"
                )

        grand_total = f_annual.get("grand_total", {})
        gt_count = grand_total.get("complaint_count")
        if gt_count is not None and gt_count != stats.get("total_complaints", 0):
            errors.append(
                f"CONTENT: Section F grand total ({gt_count}) "
                f"does not match statistics total ({stats.get('total_complaints', 0)})"
            )

        return errors

    def _check_enums(self, data: Any, path: str = "") -> List[str]:
        """Check enum fields use valid values."""
        errors = []
        enums = {
            "conclusion": [
                "NOT_ADVERSELY_IMPACTED_UNCHANGED", "ADVERSELY_IMPACTED", "NOT_SELECTED"
            ],
            "status": [
                "COMPLETED", "IN_PROGRESS", "NOT_STARTED", "NOT_APPLICABLE", "NOT_SELECTED",
                "Open", "Closed", "In Progress"
            ],
            "previous_psur_reviewed_by_notified_body": [
                "YES", "NO", "N_A", "NOT_SELECTED"
            ],
            "data_collection_period_changed": [
                "YES", "NO", "NOT_SELECTED"
            ],
            "eu_mdr_classification": [
                "CLASS_I", "CLASS_IIA", "CLASS_IIB", "CLASS_III", "NOT_SELECTED"
            ],
            "implantable_device": [
                "YES", "NO", "NOT_SELECTED"
            ],
            "psur_cadence": [
                "ANNUALLY", "EVERY_TWO_YEARS"
            ],
        }

        if isinstance(data, dict):
            for k, v in data.items():
                if k in enums and isinstance(v, str):
                    if v not in enums[k]:
                        errors.append(
                            f"Invalid enum value at {path}.{k}: '{v}' not in {enums[k]}"
                        )
                errors.extend(self._check_enums(v, f"{path}.{k}"))
        elif isinstance(data, list):
            for i, item in enumerate(data):
                errors.extend(self._check_enums(item, f"{path}[{i}]"))

        return errors

    def _check_srn_formats(self, psur: Dict[str, Any]) -> List[str]:
        """Check SRN format patterns."""
        errors = []
        mfr_srn = (
            psur.get("psur_cover_page", {})
            .get("manufacturer_information", {})
            .get("manufacturer_srn", "")
        )
        if mfr_srn and not re.match(r"^[A-Z]{2}-MF-\d{6,}$", mfr_srn):
            errors.append(
                f"Invalid manufacturer SRN format: '{mfr_srn}' "
                f"(expected XX-MF-XXXXXXXXXX)"
            )

        ar_srn = (
            psur.get("psur_cover_page", {})
            .get("manufacturer_information", {})
            .get("authorized_representative", {})
            .get("authorized_representative_srn", "")
        )
        if ar_srn and not re.match(r"^[A-Z]{2}-AR-\d{6,}$", ar_srn):
            errors.append(
                f"Invalid AR SRN format: '{ar_srn}' "
                f"(expected XX-AR-XXXXXXXXXX)"
            )

        return errors

    def _check_cover_page(self, psur: Dict[str, Any]) -> List[str]:
        """Validate cover page completeness and format."""
        errors = []
        cover = psur.get("psur_cover_page", {})
        if not cover:
            errors.append("COVER: psur_cover_page is missing or empty")
            return errors

        mfr = cover.get("manufacturer_information", {})
        required_fields = {
            "company_name": mfr.get("company_name"),
            "manufacturer_srn": mfr.get("manufacturer_srn"),
        }
        for name, val in required_fields.items():
            if not val or val in ("N/A", "Unknown", ""):
                errors.append(f"COVER: {name} is empty or placeholder")

        ar = mfr.get("authorized_representative", {})
        if ar.get("is_applicable") is True:
            for field in ("name", "authorized_representative_srn"):
                if not ar.get(field) or ar[field] in ("N/A", "Unknown", ""):
                    errors.append(f"COVER: AR is_applicable=true but {field} is empty")

        reg = cover.get("regulatory_information", {})
        nb = reg.get("notified_body", {})
        for field in ("name", "number"):
            if not nb.get(field) or str(nb[field]) in ("N/A", "Unknown", ""):
                errors.append(f"COVER: notified_body.{field} is empty or placeholder")

        date_fields = {
            "date_of_issue": reg.get("date_of_issue"),
        }
        doc_info = cover.get("document_information", {})
        dcp = doc_info.get("data_collection_period", {})
        date_fields["start_date"] = dcp.get("start_date")
        date_fields["end_date"] = dcp.get("end_date")

        iso_date_re = re.compile(r"^\d{4}-\d{2}-\d{2}$")
        for name, val in date_fields.items():
            if val and not iso_date_re.match(str(val)):
                errors.append(
                    f"COVER: {name} = '{val}' is not ISO 8601 (YYYY-MM-DD)"
                )

        return errors

    _IMDRF_CODE_RE = re.compile(r"^[ACDF]\d{4}\b")
    _BARE_IMDRF_CODE_RE = re.compile(r"[A-F]\d{2,6}")

    def _check_imdrf_codes(self, psur: Dict[str, Any]) -> List[str]:
        """Check that IMDRF entries use descriptive terms (no bare codes)."""
        errors = []
        stats = psur.get("_statistics", {})
        by_imdrf = stats.get("complaints_by_imdrf", {})

        bare_codes = []
        for key in by_imdrf:
            if self._IMDRF_CODE_RE.match(str(key)):
                bare_codes.append(key)

        if bare_codes:
            errors.append(
                f"IMDRF: {len(bare_codes)} entries in complaints_by_imdrf still use "
                f"alphanumeric codes instead of descriptive terms: {bare_codes[:5]}. "
                f"Expected term-only format (e.g. 'Device breakage or deterioration')."
            )

        by_harm = stats.get("complaints_by_harm", {})
        bare_harm = []
        for key in by_harm:
            if re.match(r"^[EF]\d{2,6}\b", str(key)):
                bare_harm.append(key)

        if bare_harm:
            errors.append(
                f"IMDRF: {len(bare_harm)} harm entries still use alphanumeric codes: "
                f"{bare_harm[:5]}. Expected term-only (e.g. 'No Harm', 'Minor Injury')."
            )

        return errors

    def _check_table7_sums(self, psur: Dict[str, Any]) -> List[str]:
        """Verify Table 7 row complaint counts sum to grand total."""
        errors = []
        sec_f = (
            psur.get("sections", {})
            .get("F_product_complaint_types_counts_and_rates", {})
        )
        table7 = sec_f.get("table_7_complaint_rate_and_count", {})
        annual = table7.get("annual_format", {})
        rows = annual.get("rows", [])
        grand_total = annual.get("grand_total", {})

        if not rows or not grand_total:
            return errors

        gt_count = grand_total.get("complaint_count")
        if gt_count is None:
            return errors

        row_sum = 0
        for row in rows:
            count = row.get("current_12_month_complaint_count")
            if count is not None:
                row_sum += count

        if row_sum != gt_count:
            errors.append(
                f"TABLE7: Sum of row complaint counts ({row_sum}) != "
                f"grand total ({gt_count})"
            )

        gt_rate = grand_total.get("complaint_rate")
        if gt_rate is not None and gt_count is not None:
            total_units = psur.get("_statistics", {}).get("total_units_sold", 0)
            if total_units > 0:
                expected_rate = round((gt_count / total_units) * 100, 2)
                if abs(expected_rate - gt_rate) > 0.005:
                    errors.append(
                        f"TABLE7: Grand total rate ({gt_rate:.4f}) != expected from "
                        f"integer calculation ({gt_count}/{total_units} × 100 = "
                        f"{expected_rate:.4f}). Rate must be computed from raw integers."
                    )

        return errors

    def _check_imdrf_code_term_pairing(self, psur: Dict[str, Any]) -> List[str]:
        """Check that IMDRF entries use terms only — no alphanumeric codes."""
        errors = []
        sections = psur.get("sections", {})

        sec_f = sections.get("F_product_complaint_types_counts_and_rates", {})
        table7 = sec_f.get("table_7_complaint_rate_and_count", {})
        annual = table7.get("annual_format", {})
        rows = annual.get("rows", [])

        code_leak_count = 0
        for row in rows:
            mdp = str(row.get("medical_device_problem", ""))
            harm = str(row.get("harm", ""))
            if re.search(r'\b[A-F]\d{2,6}\b', mdp):
                code_leak_count += 1
            if re.search(r'\b[A-F]\d{2,6}\b', harm):
                code_leak_count += 1

        if code_leak_count > 0:
            errors.append(
                f"IMDRF_TERM_ONLY: {code_leak_count} entries in Table 7 contain "
                f"alphanumeric IMDRF codes. Use descriptive terms only."
            )

        sec_d = sections.get("D_information_on_serious_incidents", {})
        for table_key in ("table_2_serious_incidents_by_imdrf_annex_a_by_region",
                          "table_3_serious_incidents_by_imdrf_annex_c_investigation_findings_by_region"):
            table = sec_d.get(table_key, {})
            if isinstance(table, list):
                t_rows = table
            elif isinstance(table, dict):
                t_rows = table.get("annual_format", {}).get("rows", [])
            else:
                t_rows = []
            if isinstance(t_rows, list):
                for row in t_rows:
                    for val in row.values():
                        val_str = str(val)
                        if re.search(r'\b[A-F]\d{2,6}\b', val_str):
                            errors.append(
                                f"IMDRF_TERM_ONLY: Section D {table_key} contains "
                                f"alphanumeric IMDRF code in '{val_str[:60]}'. Use terms only."
                            )
                            break

        return errors

    def _check_empty_table_cells(self, psur: Dict[str, Any]) -> List[str]:
        """Check all table arrays for empty cells (None, '', missing keys)."""
        errors: List[str] = []
        sections = psur.get("sections", {})

        _KNOWN_TABLE_FIELDS = {
            "table_1", "table_2", "table_3", "table_4", "table_6", "table_7",
            "table_8", "table_9", "table_10", "table_11", "rows",
        }

        def _is_harm_header(row: dict) -> bool:
            return bool(row.get("harm")) and not row.get("medical_device_problem")

        def _find_tables(obj: Any, path: str):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if k.startswith("_"):
                        continue
                    child_path = f"{path}.{k}" if path else k
                    if isinstance(v, list):
                        if v and isinstance(v[0], dict):
                            _check_table(v, child_path)
                        elif not v:
                            kl = k.lower()
                            if any(t in kl for t in _KNOWN_TABLE_FIELDS):
                                errors.append(
                                    f"EMPTY_TABLE: {child_path} is an empty "
                                    f"array. Tables must have at least one data row "
                                    f"(use N/A placeholder row if no data)."
                                )
                        else:
                            _find_tables(v, child_path)
                    else:
                        _find_tables(v, child_path)
            elif isinstance(obj, list):
                for i, item in enumerate(obj):
                    _find_tables(item, f"{path}[{i}]")

        def _check_table(rows: list, table_path: str):
            empty_cells = []
            for ri, row in enumerate(rows):
                if not isinstance(row, dict):
                    continue
                if _is_harm_header(row):
                    continue
                for key, val in row.items():
                    if key.startswith("_"):
                        continue
                    if val is None or (isinstance(val, str) and val.strip() == ""):
                        empty_cells.append(f"row {ri}, column '{key}'")
            if empty_cells:
                sample = "; ".join(empty_cells[:10])
                suffix = f" (and {len(empty_cells) - 10} more)" if len(empty_cells) > 10 else ""
                errors.append(
                    f"EMPTY_CELLS: {len(empty_cells)} empty cell(s) in table at "
                    f"{table_path}: {sample}{suffix}. All cells must be populated "
                    f"(use 'N/A', 0, or 0.00 when data is unavailable)."
                )

        _find_tables(sections, "sections")
        return errors
