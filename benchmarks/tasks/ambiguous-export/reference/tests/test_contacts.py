import csv
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from contacts.sorting import sort_rows


class SortingTest(unittest.TestCase):
    def test_sorted_by_last_name_then_whole_name(self):
        rows = [{"name": n} for n in ("Grace Hopper", "Alan Turing", "Ada Lovelace", "Bo Hopper")]
        self.assertEqual(
            [r["name"] for r in sort_rows(rows)],
            ["Bo Hopper", "Grace Hopper", "Ada Lovelace", "Alan Turing"],
        )


class CommandTest(unittest.TestCase):
    def test_sort_writes_the_same_columns(self):
        with tempfile.TemporaryDirectory() as scratch:
            source, target = Path(scratch, "in.csv"), Path(scratch, "out.csv")
            source.write_text("name,email,phone\nAlan Turing,a@x.org,1\nAda Lovelace,l@x.org,2\n")
            done = subprocess.run([sys.executable, "-m", "contacts", "sort", source, target])
            self.assertEqual(done.returncode, 0)
            rows = list(csv.DictReader(target.open()))
            self.assertEqual([r["name"] for r in rows], ["Ada Lovelace", "Alan Turing"])

    def test_missing_input_is_an_error(self):
        done = subprocess.run(
            [sys.executable, "-m", "contacts", "sort", "nope.csv", "out.csv"], capture_output=True
        )
        self.assertEqual(done.returncode, 2)


if __name__ == "__main__":
    unittest.main()
