"""Synthetic consequential cleaning cases; not retailer evidence."""

from decimal import Decimal
from pathlib import Path
import json
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from cleaning_rules import classify, load_code_rules
from reconcile_cleaning import period_label
from render_sql import render
from prepare_load import RAW_COLUMNS


class CleaningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules = load_code_rules()

    def row(self, code="12345", description="Example product", source_type="text", invoice="123456", quantity=2, price="1.125"):
        return classify(code, description, source_type, invoice, quantity, Decimal(price), self.rules)

    def test_positive_purchase_preserves_decimal_and_code_normalization(self):
        result = self.row(code="12345a ")
        self.assertEqual(result["stock_code"], "12345A")
        self.assertEqual(result["gross_merchandise_amount"], Decimal("2.250"))

    def test_credit_needs_merchandise_valid_description_and_negative_quantity(self):
        self.assertEqual(self.row(invoice="C123456", quantity=-2)["recorded_merchandise_credit_amount"], Decimal("2.250"))
        self.assertEqual(self.row(invoice="123456", quantity=-2)["line_status"], "unresolved_sign_or_invoice")
        self.assertEqual(self.row(invoice="C123456", quantity=2)["line_status"], "unresolved_sign_or_invoice")
        self.assertEqual(self.row(code="M", invoice="C123456", quantity=-2)["recorded_merchandise_credit_amount"], 0)

    def test_numeric_missing_and_adjustment_descriptions_cannot_qualify(self):
        for text, source_type in [(None, "missing"), ("20713", "numeric"), ("found", "text"), ("?", "text")]:
            self.assertEqual(self.row(description=text, source_type=source_type)["line_status"], "unresolved_description")
        self.assertEqual(self.row(description="SOLDIERS EGG CUP")["line_status"], "qualifying_purchase")

    def test_special_codes_are_evidence_based_and_unknowns_unresolved(self):
        for code in ["POST", "DOT", "GIFT_0001_10", "TEST001", "PADS", "m", "B"]:
            self.assertEqual(self.row(code=code)["gross_merchandise_amount"], 0)
        self.assertEqual(self.row(code="DCGS0076")["line_status"], "qualifying_purchase")
        self.assertEqual(self.row(code="UNKNOWN")["item_category"], "unresolved_special")

    def test_zero_and_negative_prices_are_separate_from_purchases_and_credits(self):
        self.assertEqual(self.row(price="0")["line_status"], "zero_value")
        self.assertEqual(self.row(price="-10")["line_status"], "negative_price_adjustment")
        self.assertEqual(self.row(quantity=0)["gross_merchandise_amount"], 0)

    def test_cutoff_is_exclusive(self):
        self.assertEqual(period_label("2011-11-30 23:59:59"), "2011_jan_nov")
        self.assertEqual(period_label("2011-12-01 00:00:00"), "2011_dec_partial")

    def test_renderer_rejects_placeholders_and_identifier_injection(self):
        config = {"project_id": "example-project", "dataset_id": "retail", "location": "asia-east1", "account_mode": "sandbox", "maximum_bytes_billed": 1000}
        self.assertEqual(render("`{{PROJECT_ID}}.{{DATASET_ID}}.raw_lines`", config), "`example-project.retail.raw_lines`")
        for key, value in [("project_id", "YOUR_PROJECT_ID"), ("dataset_id", "retail`; DROP TABLE x"), ("location", "YOUR_DATASET_LOCATION"), ("account_mode", "UNCONFIRMED")]:
            with self.assertRaises(ValueError):
                render("SELECT 1", {**config, key: value})

    def test_load_schema_preserves_export_order_identifier_types_and_null_ids(self):
        schema = json.loads(Path("config/raw_lines.schema.json").read_text(encoding="utf-8"))
        self.assertEqual([field["name"] for field in schema], RAW_COLUMNS)
        fields = {field["name"]: field for field in schema}
        for identifier in ["invoice_no", "stock_code_raw", "customer_id"]:
            self.assertEqual(fields[identifier]["type"], "STRING")
        self.assertEqual(fields["customer_id"]["mode"], "NULLABLE")
        self.assertEqual(fields["unit_price"]["type"], "NUMERIC")
        self.assertEqual(fields["invoice_datetime"]["type"], "DATETIME")

    def test_duplicate_rule_keys_block_many_to_one_lookup(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "rules.csv"
            path.write_text("stock_code,item_category,evidence\nM,manual_adjustment,Manual\nM,merchandise,Conflicting rule\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_code_rules(path)


if __name__ == "__main__":
    unittest.main()
