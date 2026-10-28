import unittest

from notes.service import NoteService, NotFound
from notes.storage import NoteStore


class NewEndpointsTest(unittest.TestCase):
    def setUp(self):
        self.service = NoteService(NoteStore())
        for n in range(3):
            self.service.create("ana", f"Note {n}", "milk and bread" if n == 1 else "")

    def test_search_finds_words_in_the_body(self):
        self.assertEqual([n.title for n in self.service.search("ana", "bread")], ["Note 1"])

    def test_delete_removes_the_note(self):
        note = self.service.mine("ana")[0]
        self.service.delete("ana", note.id)
        with self.assertRaises(NotFound):
            self.service.read("ana", note.id)


if __name__ == "__main__":
    unittest.main()
