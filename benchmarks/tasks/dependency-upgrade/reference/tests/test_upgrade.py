import unittest
from decimal import Decimal

import moneyfmt

from invoicer.totals import parse_line, total


class UpgradeTest(unittest.TestCase):
    def test_uses_moneyfmt_2(self):
        self.assertEqual(moneyfmt.__version__, "2.0.0")

    def test_amounts_are_exact(self):
        lines = [parse_line("A=0.10"), parse_line("B=0.20")]
        self.assertEqual(total(lines), Decimal("0.30"))


if __name__ == "__main__":
    unittest.main()
