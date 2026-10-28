import unittest
from decimal import Decimal

from ledgerly.core.errors import ImportError_
from ledgerly.io.bank_formats import parse_amount
from ledgerly.io.csv_import import read_statement

from tests.helpers import write

HEADER = "Datum,Bedrag,Munt,Naam tegenpartij,Tegenrekening IBAN/BBAN,Omschrijving-1\n"


class RaboSignsTest(unittest.TestCase):
    def test_plus_signed_amounts(self):
        self.assertEqual(parse_amount("+2.750,00", decimal_comma=True), Decimal("2750.00"))
        rows = read_statement(
            write(HEADER + '2026-03-25,"+2.750,00",EUR,Acme BV,,Salaris maart\n'), "rabo", "checking"
        )
        self.assertEqual([r.amount for r in rows], [Decimal("2750.00")])

    def test_an_unreadable_amount_stops_the_import(self):
        path = write(HEADER + '2026-03-25,"12,5x",EUR,Acme BV,,Salaris maart\n')
        with self.assertRaisesRegex(ImportError_, "statement.csv:2"):
            read_statement(path, "rabo", "checking")


if __name__ == "__main__":
    unittest.main()
