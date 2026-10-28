import unittest
from datetime import date
from decimal import Decimal

from ledgerly.core.errors import ImportError_
from ledgerly.io.bank_formats import parse_amount
from ledgerly.io.csv_import import import_statement, read_statement
from ledgerly.rules.categorize import Categorizer

from tests.helpers import journal, write

GENERIC = """date,amount,currency,description,counterparty,iban
2026-03-01,-42.10,EUR,Weekly shop,Albert Heijn 1234,
2026-03-02,+2500.00,EUR,Salary March,Acme BV,NL91ABNA0417164300
"""

ING = """Datum;Naam / Omschrijving;Tegenrekening;Af Bij;Bedrag (EUR);Mededelingen
20260303;Spotify AB;;Af;9,99;Ref: 7781
20260304;Gemeente Utrecht;NL91ABNA0417164300;Bij;1.234,56;Teruggave
"""

RABO = """Datum,Bedrag,Munt,Naam tegenpartij,Tegenrekening IBAN/BBAN,Omschrijving-1
2026-03-01,,EUR,,,Saldo begin
2026-03-05,"-12,50",EUR,Jumbo Utrecht,,Boodschappen
2026-03-06,"-1.020,00",EUR,Woonstichting,NL91ABNA0417164300,Huur maart
"""


class AmountTest(unittest.TestCase):
    def test_decimal_comma(self):
        self.assertEqual(parse_amount("1.234,56", decimal_comma=True), Decimal("1234.56"))
        self.assertEqual(parse_amount("-12,50", decimal_comma=True), Decimal("-12.50"))
        self.assertIsNone(parse_amount("  ", decimal_comma=True))

    def test_decimal_point(self):
        self.assertEqual(parse_amount("+1,250.00", decimal_comma=False), Decimal("1250.00"))
        self.assertIsNone(parse_amount("abc", decimal_comma=False))


class FormatTest(unittest.TestCase):
    def test_generic(self):
        rows = read_statement(write(GENERIC), "generic", "checking")
        self.assertEqual([r.amount for r in rows], [Decimal("-42.10"), Decimal("2500.00")])
        self.assertEqual(rows[1].source, "statement.csv:3")

    def test_ing_signs_from_af_bij(self):
        rows = read_statement(write(ING), "ing", "checking")
        self.assertEqual([r.amount for r in rows], [Decimal("-9.99"), Decimal("1234.56")])
        self.assertEqual(rows[0].description, "Spotify AB")
        self.assertEqual(rows[1].booked, date(2026, 3, 4))

    def test_rabo_skips_balance_lines(self):
        rows = read_statement(write(RABO), "rabo", "checking")
        self.assertEqual([r.amount for r in rows], [Decimal("-12.50"), Decimal("-1020.00")])

    def test_errors_name_the_line(self):
        bad = GENERIC.replace("-42.10", "lots")
        with self.assertRaisesRegex(ImportError_, "statement.csv:2"):
            read_statement(write(bad), "generic", "checking")
        with self.assertRaisesRegex(ImportError_, "unknown format"):
            read_statement(write(GENERIC), "abn", "checking")


class ImportTest(unittest.TestCase):
    def test_import_categorises_and_deduplicates(self):
        book = journal()
        path = write(RABO)
        first = import_statement(book, path, "rabo", "checking", Categorizer([]))
        again = import_statement(book, path, "rabo", "checking", Categorizer([]))
        self.assertEqual((first.added, again.added, again.duplicates), (2, 0, 2))
        self.assertEqual(sorted(t.category for t in book), ["groceries", "rent"])


if __name__ == "__main__":
    unittest.main()
