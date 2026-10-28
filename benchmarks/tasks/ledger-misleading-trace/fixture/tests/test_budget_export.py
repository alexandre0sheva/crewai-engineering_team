import unittest
from datetime import date
from decimal import Decimal

from ledgerly.core.errors import LedgerError
from ledgerly.core.periods import parse_period
from ledgerly.fx.rates import RateTable
from ledgerly.io.export import export_csv
from ledgerly.reports.budget import compare, load_budgets, render_budget

from tests.helpers import journal, txn, write


class BudgetTest(unittest.TestCase):
    def setUp(self):
        self.book = journal()
        self.book.extend([
            txn("-95.00", category="groceries"),
            txn("-20.00", day=date(2026, 3, 3), category="eating-out"),
            txn("-150.00", day=date(2026, 3, 4), category="eating-out"),
        ])  # fmt: skip

    def test_spending_against_budget(self):
        budgets = load_budgets(write("category,monthly\ngroceries,100\neating-out,120\n"), self.book)
        lines = compare(self.book, parse_period("2026-03"), RateTable(), budgets)
        self.assertEqual([(l.category, l.status) for l in lines], [("eating-out", "over"), ("groceries", "close")])
        self.assertEqual(lines[1].left.amount, Decimal("5.00"))
        self.assertIn("OVER", render_budget(lines, parse_period("2026-03")))

    def test_a_quarter_has_three_months_of_budget(self):
        budgets = {"groceries": Decimal("100")}
        line = compare(self.book, parse_period("2026-Q1"), RateTable(), budgets)[0]
        self.assertEqual(line.budget.amount, Decimal("300"))

    def test_budget_file_errors(self):
        with self.assertRaisesRegex(LedgerError, "not an expense"):
            load_budgets(write("category,monthly\nsalary,10\n"), self.book)
        with self.assertRaisesRegex(LedgerError, "bad amount"):
            load_budgets(write("category,monthly\ngroceries,ten\n"), self.book)


class ExportTest(unittest.TestCase):
    def test_csv(self):
        book = journal()
        book.extend([txn("-5", category="groceries"), txn("7", day=date(2026, 4, 1))])
        text = export_csv(book, parse_period("2026-03"))
        self.assertEqual(text.splitlines()[1], "2026-03-02,checking,-5.00,EUR,groceries,,test,")
        self.assertEqual(len(export_csv(book).splitlines()), 3)


if __name__ == "__main__":
    unittest.main()
