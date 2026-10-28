import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from tasktool.cli import run


class CliTest(unittest.TestCase):
    def test_add_list_done(self):
        with tempfile.TemporaryDirectory() as scratch:
            file = str(Path(scratch, "t.json"))
            run(["--file", file, "add", "Buy milk", "--tag", "home"])
            run(["--file", file, "done", "1"])
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                run(["--file", file, "list", "--all"])
            self.assertEqual(out.getvalue(), "[x] 1 Buy milk [normal]\n")


if __name__ == "__main__":
    unittest.main()
