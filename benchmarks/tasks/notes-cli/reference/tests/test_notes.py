import os
import tempfile
import unittest
from pathlib import Path

from notes import store


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        os.environ["NOTES_FILE"] = str(Path(self.directory.name) / "n.json")

    def tearDown(self):
        self.directory.cleanup()

    def test_ids_are_never_reused(self):
        store.add("a", [])
        store.add("b", [])
        store.remove(1)
        self.assertEqual(store.add("c", [])["id"], 3)

    def test_find_by_tag_and_word(self):
        store.add("Buy milk", ["home"])
        store.add("Pay rent", [])
        self.assertEqual([n["id"] for n in store.find(tag="home")], [1])
        self.assertEqual([n["id"] for n in store.find(word="RENT")], [2])


if __name__ == "__main__":
    unittest.main()
