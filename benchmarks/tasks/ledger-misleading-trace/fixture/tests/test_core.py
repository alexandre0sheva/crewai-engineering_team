import unittest
from datetime import date
from decimal import Decimal

from ledgerly.core.accounts import Account, Chart, Kind, normalise_iban
from ledgerly.core.errors import LedgerError
from ledgerly.core.money import Money, round_cents, total
from ledgerly.core.periods import parse_period

from tests.helpers import journal, txn


class MoneyTest(unittest.TestCase):
    def test_arithmetic_keeps_the_currency(self):
        self.assertEqual(Money(Decimal("1.10")) + Money(Decimal("2.20")), Money(Decimal("3.30")))
        with self.assertRaises(ValueError):
            Money(Decimal("1"), "EUR") + Money(Decimal("1"), "USD")

    def test_display_rounds_half_up(self):
        self.assertEqual(str(Money(Decimal("1234.565"))), "1,234.57 EUR")
        self.assertEqual(str(Money(Decimal("1234.5"), "JPY")), "1,235 JPY")
        self.assertEqual(round_cents(Decimal("0.125"), bankers=True), Decimal("0.12"))

    def test_money_needs_a_decimal(self):
        with self.assertRaises(TypeError):
            Money(1.5)  # type: ignore[arg-type]

    def test_total_of_nothing_is_zero(self):
        self.assertEqual(total([]), Money(Decimal("0")))


class AccountTest(unittest.TestCase):
    def test_iban_is_normalised_and_checked(self):
        self.assertEqual(normalise_iban("nl91 abna 0417 1643 00"), "NL91ABNA0417164300")
        with self.assertRaises(LedgerError):
            normalise_iban("NL92ABNA0417164300")

    def test_ids_are_slugs(self):
        with self.assertRaises(LedgerError):
            Account("My Account", "x", Kind.ASSET)

    def test_chart_lookups(self):
        chart = Chart()
        self.assertIn("groceries", chart)
        self.assertEqual([a.id for a in chart.by_kind(Kind.INCOME)], ["interest", "other-income", "salary"])
        with self.assertRaises(LedgerError):
            chart.get("nope")


class PeriodTest(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(parse_period("2026-02").last, date(2026, 2, 28))
        self.assertEqual(parse_period("2026-Q2").first, date(2026, 4, 1))
        self.assertEqual(parse_period("2026").days, 365)
        self.assertEqual(parse_period("2026-01-15..2026-01-20").days, 6)
        self.assertEqual(parse_period("2026-03").previous().label, "2026-02")
        with self.assertRaises(LedgerError):
            parse_period("March")


class JournalTest(unittest.TestCase):
    def test_duplicates_are_skipped(self):
        book = journal()
        self.assertEqual(book.extend([txn("-5"), txn("-5"), txn("-6")]), (2, 1))

    def test_money_goes_into_accounts_not_categories(self):
        with self.assertRaises(LedgerError):
            journal().add(txn("-5", account="groceries"))


if __name__ == "__main__":
    unittest.main()
