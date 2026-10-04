import unittest

import app


class TodoTests(unittest.TestCase):
    def setUp(self):
        app.TODOS.clear()

    def test_add_rejects_blank(self):
        self.assertIsNone(app.add("   "))
        self.assertEqual(app.add("x")["id"], 1)

    def test_render_escapes(self):
        app.add("<b>")
        self.assertIn("&lt;b&gt;", app.render())


if __name__ == "__main__":
    unittest.main()
