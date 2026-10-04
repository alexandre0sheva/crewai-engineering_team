import unittest

from md2html import convert


class ConvertTests(unittest.TestCase):
    def test_heading_and_paragraph(self):
        self.assertEqual(convert("# Hi\n\ntext"), "<h1>Hi</h1>\n<p>text</p>")

    def test_escaping_and_code(self):
        self.assertEqual(convert("a < b `<i>`"), "<p>a &lt; b <code>&lt;i&gt;</code></p>")

    def test_lists(self):
        self.assertEqual(convert("- a\n- b"), "<ul><li>a</li><li>b</li></ul>")


if __name__ == "__main__":
    unittest.main()
