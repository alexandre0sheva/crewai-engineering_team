import unittest

from csvcheck.__main__ import validate
from csvcheck.rules import check_value


class RuleTests(unittest.TestCase):
    def test_bounds_are_inclusive(self):
        column = {"name": "n", "type": "int", "min": 1, "max": 3}
        self.assertEqual(check_value(column, "1"), [])
        self.assertEqual(check_value(column, "3"), [])
        self.assertTrue(check_value(column, "0"))

    def test_unique_reports_the_later_row(self):
        columns = [{"name": "id", "type": "int", "unique": True}]
        errors = validate(columns, [{"id": "1"}, {"id": "1"}])
        self.assertEqual(len(errors), 1)
        self.assertTrue(errors[0].startswith("row 2, column id:"))


if __name__ == "__main__":
    unittest.main()
