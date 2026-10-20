"""Synthetic recorded-credit cases, separate from historical business evidence."""

import csv
from decimal import Decimal
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from cleaning_rules import load_code_rules
from compare_recorded_credits import compare
from monthly_sales_baseline import aggregate as monthly_aggregate, COLUMNS as MONTHLY_COLUMNS
from recorded_credit_analysis import build_outputs, COLUMNS, extract_credits, REMAINDER
from test_monthly_sales import source_row


def credit(invoice="C123456", **kwargs):
    return {**source_row(invoice=invoice, quantity="-1", **kwargs), "source_row": "2"}


class RecordedCreditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules = load_code_rules()

    def source(self):
        return [credit(), credit(invoice="C123457", timestamp="2010-02-10 10:00:00", customer="__NULL__"),
                credit(invoice="C123458", timestamp="2011-01-10 10:00:00", sheet="Year 2010-2011", price="10"),
                credit(invoice="C123459", timestamp="2011-02-10 10:00:00", sheet="Year 2010-2011", code="12346", price="2")]

    def outputs(self, source=None, top_n=1):
        credits, _ = extract_credits(source or self.source(), self.rules, "__NULL__")
        return build_outputs(credits, top_n=top_n)

    def test_classification_repeats_anonymous_and_exclusions(self):
        source = self.source() + [credit(), credit(invoice="C123460", code="POST", price="100"),
                                  credit(invoice="C123461", timestamp="2010-12-01 00:00:00"),
                                  credit(invoice="C123462", timestamp="2011-12-01 00:00:00", sheet="Year 2010-2011"),
                                  credit(invoice="C123463", price="-10")]
        rows, _, _ = self.outputs(source)
        summary = next(row for row in rows if row['section'] == 'period_summary' and row['period'] == '2010_jan_nov')
        self.assertEqual(summary['credit_invoice_count'], '2')
        self.assertEqual(summary['credit_line_count'], '3')
        self.assertEqual(Decimal(summary['recorded_credits_gbp']), Decimal('3.375'))

    def test_recorded_months_preserved_even_for_one_credit_invoice(self):
        source = [credit(), credit(timestamp="2010-02-10 10:00:00")]
        rows, months, _ = self.outputs(source)
        self.assertEqual(months['2010-01-01'], Decimal('1.125'))
        self.assertEqual(months['2010-02-01'], Decimal('1.125'))
        summary = next(row for row in rows if row['section'] == 'period_summary' and row['period'] == '2010_jan_nov')
        self.assertEqual(summary['credit_invoice_count'], '1')

    def test_invoice_ties_and_all_remainders_reconcile(self):
        rows, _, findings = self.outputs()
        chosen = next(row for row in rows if row['section'] == 'invoice_concentration' and row['period'] == '2010_jan_nov' and row['row_rank'] == '1')
        self.assertEqual(chosen['detail_key'], 'C123456')
        for section in ('invoice_concentration', 'stock_change'):
            self.assertEqual(sum(row['section'] == section and row['detail_key'] == REMAINDER for row in rows), 2)
        self.assertTrue(findings['all_remainders_reconcile'])

    def test_stock_increase_decrease_sets_disjoint_and_counts_not_added(self):
        source = [credit(code='12345', price='10'), credit(invoice='C123457', code='12346', price='20'),
                  credit(invoice='C123458', timestamp='2011-01-10 10:00:00', sheet='Year 2010-2011', code='12345', price='20'),
                  credit(invoice='C123459', timestamp='2011-01-10 10:00:00', sheet='Year 2010-2011', code='12346', price='5')]
        rows, _, findings = self.outputs(source)
        self.assertEqual([row['stock_code'] for row in findings['selected_stock_changes']], ['12345', '12346'])
        for row in rows:
            if row['section'] == 'stock_change':
                self.assertEqual(row['credit_invoice_count'], '')
                self.assertEqual(row['average_credit_per_invoice_gbp'], '')

    def comparison(self, mutate):
        source = self.source()
        rows, _, _ = self.outputs(source)
        monthly, _, _ = monthly_aggregate(source, self.rules, '__NULL__')
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            expected, observed, baseline = [Path(folder) / name for name in ('expected.csv', 'observed.csv', 'baseline.csv')]
            def write(path, values, columns):
                with path.open('w', encoding='utf-8', newline='') as stream:
                    writer = csv.DictWriter(stream, fieldnames=columns)
                    writer.writeheader()
                    writer.writerows(values)
            write(expected, rows, COLUMNS)
            changed, columns = mutate([row.copy() for row in rows])
            write(observed, changed, columns)
            write(baseline, monthly, MONTHLY_COLUMNS)
            before = [path.read_bytes() for path in (expected, observed, baseline)]
            result = compare(expected, observed, baseline)
            self.assertEqual(before, [path.read_bytes() for path in (expected, observed, baseline)])
            return result

    def test_comparison_accepts_order_scale_and_explicit_nulls(self):
        def mutate(rows):
            rows[0]['recorded_credits_gbp'] = '2.250000000'
            return list(reversed(rows)), list(reversed(COLUMNS))
        self.assertEqual(self.comparison(mutate)['status'], 'passed')

    def test_comparison_detects_tiny_money_wrong_count_ratio_and_null_zero(self):
        def mutate(rows):
            rows[0]['recorded_credits_gbp'] = '2.250000001'
            rows[0]['credit_invoice_count'] = '3'
            rows[0]['share_of_period_credits'] = '0.999999999'
            stock = next(row for row in rows if row['section'] == 'stock_change')
            stock['credit_invoice_count'] = '0'
            return rows, COLUMNS
        result = self.comparison(mutate)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(len(result['field_mismatches']), 4)
        self.assertTrue(result['population_errors'])

    def test_comparison_rejects_duplicate_missing_keys_and_schema(self):
        def duplicate(rows):
            rows[1] = rows[0].copy()
            return rows, COLUMNS
        self.assertEqual(self.comparison(duplicate)['status'], 'failed')
        def missing(rows):
            rows[0]['detail_key'] = 'unexpected'
            return rows, COLUMNS
        self.assertEqual(self.comparison(missing)['status'], 'failed')
        def schema(rows):
            return [{key: value for key, value in row.items() if key != COLUMNS[-1]} for row in rows], COLUMNS[:-1]
        self.assertEqual(self.comparison(schema)['status'], 'failed')

    def test_comparison_rejects_nonfinite_and_negative_credit_amount(self):
        for value in ('NaN', '-0.001'):
            def mutate(rows):
                rows[0]['recorded_credits_gbp'] = value
                return rows, COLUMNS
            self.assertEqual(self.comparison(mutate)['status'], 'failed')


if __name__ == '__main__':
    unittest.main()
