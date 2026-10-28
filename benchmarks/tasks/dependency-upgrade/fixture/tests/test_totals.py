import unittest

from invoicer.totals import InvoiceError, parse_line, render


class TotalsTest(unittest.TestCase):
    def test_render(self):
        lines = [parse_line("Design=1,200.00"), parse_line("Hosting=€49.99")]
        self.assertEqual(render(lines)[-1], "Total                        €1,249.99")

    def test_bad_lines(self):
        with self.assertRaisesRegex(InvoiceError, "cannot read amount"):
            parse_line("Design=lots")
        with self.assertRaisesRegex(InvoiceError, "DESCRIPTION=AMOUNT"):
            parse_line("no amount")


if __name__ == "__main__":
    unittest.main()
