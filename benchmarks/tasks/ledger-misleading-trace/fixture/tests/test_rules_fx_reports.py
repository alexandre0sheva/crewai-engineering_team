import unittest
from datetime import date
from decimal import Decimal

from ledgerly.core.errors import RateError, RuleError
from ledgerly.core.periods import parse_period
from ledgerly.fx.convert import to_home
from ledgerly.fx.rates import RateTable
from ledgerly.reports.balance import balances
from ledgerly.reports.income import income_statement
from ledgerly.reports.render import render_income
from ledgerly.rules.categorize import Categorizer, Rule
from ledgerly.rules.matchers import Matcher, parse_matcher

from tests.helpers import journal, txn


class RulesTest(unittest.TestCase):
    def test_built_in_rules(self):
        categorizer = Categorizer([])
        self.assertEqual(categorizer.category_for(txn("-30", counterparty="Lidl")), "groceries")
        self.assertEqual(categorizer.category_for(txn("10")), "other-income")
        self.assertEqual(categorizer.category_for(txn("-10")), "uncategorised")

    def test_own_rules_come_first(self):
        rule = Rule("eating-out", [parse_matcher("counterparty contains lidl")], expense_only=True)
        categorizer = Categorizer([rule])
        self.assertEqual(categorizer.category_for(txn("-3", counterparty="LIDL")), "eating-out")

    def test_amount_matchers(self):
        self.assertTrue(Matcher("amount", "between", "10..20").matches(txn("-15")))
        self.assertFalse(Matcher("amount", "above", "100").matches(txn("-15")))
        with self.assertRaises(RuleError):
            Matcher("amount", "contains", "1")


class RatesTest(unittest.TestCase):
    def test_latest_rate_up_to_a_week_old(self):
        table = RateTable()
        table.add("USD", date(2026, 3, 2), Decimal("0.92"))
        self.assertEqual(table.rate("USD", date(2026, 3, 7)), Decimal("0.92"))
        self.assertEqual(to_home(Decimal("10"), "USD", table, date(2026, 3, 3)), Decimal("9.20"))
        self.assertEqual(table.rate("EUR", date(2026, 3, 7)), Decimal(1))
        with self.assertRaises(RateError):
            table.rate("USD", date(2026, 3, 20))


class ReportsTest(unittest.TestCase):
    def setUp(self):
        self.book = journal()
        self.book.extend([
            txn("2500.00", description="Salaris maart", category="salary"),
            txn("-80.25", day=date(2026, 3, 9), category="groceries"),
            txn("-1020.00", day=date(2026, 3, 1), category="rent"),
            txn("-40.00", day=date(2026, 2, 9), category="groceries"),
        ])  # fmt: skip

    def test_income_statement(self):
        statement = income_statement(self.book, parse_period("2026-03"), RateTable())
        self.assertEqual(str(statement.total_income), "2,500.00 EUR")
        self.assertEqual(str(statement.total_expenses), "-1,100.25 EUR")
        self.assertEqual([line.category for line in statement.expenses], ["rent", "groceries"])
        text = render_income(statement)
        self.assertIn("Net                         1,399.75 EUR", text)

    def test_balances(self):
        rows = balances(self.book, RateTable(), date(2026, 3, 31))
        self.assertEqual([row.account for row in rows], ["checking", "card"])
        self.assertEqual(str(rows[0].own), "1,359.75 EUR")
        self.assertEqual(str(rows[1].own), "0.00 EUR")


if __name__ == "__main__":
    unittest.main()
