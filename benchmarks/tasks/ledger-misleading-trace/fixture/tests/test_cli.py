import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from ledgerly.__main__ import run

from tests.test_import import ING


class CliTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="ledgerly-cli-"))
        self.ledger = str(self.dir / "ledger.json")

    def ledgerly(self, *args: str) -> str:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(run(["--ledger", self.ledger, "--rates", str(self.dir / "r.csv"), *args]), 0)
        return out.getvalue()

    def test_import_and_report(self):
        (self.dir / "ing.csv").write_text(ING)
        self.ledgerly("account", "add", "checking", "Checking")
        self.assertIn("2 added", self.ledgerly("import", str(self.dir / "ing.csv"), "--format", "ing", "--account", "checking"))
        report = self.ledgerly("report", "income", "2026-03")
        self.assertIn("Subscriptions", report)
        self.assertIn("Net                         1,224.57 EUR", report)


if __name__ == "__main__":
    unittest.main()
