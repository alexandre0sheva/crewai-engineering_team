import unittest
from datetime import date
from decimal import Decimal

from billing.invoice import build_invoice
from billing.quote import build_quote
from billing.receipt import build_receipt

ITEMS = [("Brake pads", Decimal("34.995"), 2), ("Labour", Decimal("60.00"), 1)]


class QuoteTest(unittest.TestCase):
    def test_totals(self):
        quote = build_quote(ITEMS, "DE", 10)
        self.assertEqual(quote.subtotal, Decimal("129.99"))
        self.assertEqual(quote.discount, Decimal("13.00"))
        self.assertEqual(quote.tax, Decimal("22.23"))
        self.assertEqual(quote.total, Decimal("139.22"))

    def test_unknown_region(self):
        with self.assertRaisesRegex(ValueError, "no tax rate"):
            build_quote(ITEMS, "XX")


class InvoiceTest(unittest.TestCase):
    def test_due_date_and_total(self):
        invoice = build_invoice("INV-7", date(2026, 2, 20), ITEMS, "FR")
        self.assertEqual(invoice.due, date(2026, 3, 6))
        self.assertEqual(invoice.total, Decimal("155.99"))


class ReceiptTest(unittest.TestCase):
    def test_tax_included_and_change(self):
        receipt = build_receipt(ITEMS, "NL", Decimal("200"))
        self.assertEqual(receipt.total, Decimal("129.99"))
        self.assertEqual(receipt.tax_included, Decimal("22.56"))
        self.assertEqual(receipt.change, Decimal("70.01"))


if __name__ == "__main__":
    unittest.main()
