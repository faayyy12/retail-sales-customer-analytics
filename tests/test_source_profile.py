"""Synthetic edge cases for source diagnostics, never business evidence."""

from decimal import Decimal
from pathlib import Path
import sys
import tempfile
import unittest
from zipfile import ZipFile

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from profile_source import FIELDS, cross_sheet_matches, identifier, money_sum, records, xml_controls


class SourceDiagnosticTests(unittest.TestCase):
    def test_missing_ids_stay_missing_and_text_codes_stay_text(self):
        for value in [None, float("nan"), pd.NA]:
            self.assertIsNone(identifier(value))
        self.assertEqual(identifier(12345.0), "12345")
        self.assertEqual(identifier("00123"), "00123")
        self.assertEqual(identifier("C123456"), "C123456")
        with self.assertRaises(ValueError):
            identifier(12345.5)

    def test_decimal_controls_keep_subpenny_and_negative_values(self):
        self.assertEqual(money_sum([3 * Decimal("0.001"), Decimal("-0.1"), Decimal("0.2")]), Decimal("0.103"))
        result = records(pd.DataFrame({"Price": [Decimal("0.001")]}))
        self.assertEqual(result[0]["Price"], "0.001")

    def test_overlap_matches_null_ids_without_join_multiplication(self):
        row = ["123456", "12345", "Example", 2, pd.Timestamp("2010-12-01"), Decimal("0.125"), None, "UK"]
        left = pd.DataFrame([row] * 3, columns=FIELDS).assign(source_sheet="left")
        right = pd.DataFrame([row] * 2, columns=FIELDS).assign(source_sheet="right")
        result = cross_sheet_matches(left, right)
        self.assertEqual(result["distinct_all_field_matches"], 1)
        self.assertEqual(result["matched_row_copies_min_multiplicity"], 2)
        self.assertEqual(Decimal(result["matched_raw_signed_amount_gbp"]), Decimal("0.5"))

    def test_nonoverlapping_sheets_have_no_matches(self):
        row = ["123456", "12345", "Example", 2, pd.Timestamp("2010-12-01"), Decimal("1.25"), "12345", "UK"]
        left = pd.DataFrame([row], columns=FIELDS).assign(source_sheet="left")
        right = pd.DataFrame([row], columns=FIELDS).assign(source_sheet="right")
        right["InvoiceDate"] = pd.Timestamp("2011-01-01")
        result = cross_sheet_matches(left, right)
        self.assertIsNone(result["timestamp_overlap_start"])
        self.assertEqual(result["matched_row_copies_min_multiplicity"], 0)

    def test_xml_controls_record_binary_serialization_tails_separately(self):
        # Minimal synthetic XLSX package: no real-source business evidence.
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "synthetic.xlsx"
            with ZipFile(path, "w") as archive:
                archive.writestr("xl/workbook.xml", '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="synthetic" sheetId="1" r:id="rId1"/></sheets></workbook>')
                archive.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
                archive.writestr("xl/worksheets/sheet1.xml", '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="2"><c r="D2"><v>3</v></c><c r="F2"><v>2.5499999999999998</v></c></row><row r="3"><c r="D3"><v>-1</v></c><c r="F3"><v>0.001</v></c></row></sheetData></worksheet>')
            control = xml_controls(path)[0]
        self.assertEqual(control["rows"], 2)
        self.assertEqual(Decimal(control["raw_signed_line_amount_gbp"]), Decimal("7.649"))
        self.assertEqual(control["price_serialization_tail_rows"], 1)
        self.assertEqual(Decimal(control["canonical_minus_literal_signed_amount_gbp"]), Decimal("0.0000000000000006"))


if __name__ == "__main__":
    unittest.main()
