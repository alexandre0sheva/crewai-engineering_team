import unittest

from notes.service import NoteService, NotFound
from notes.storage import NoteStore


class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.service = NoteService(NoteStore())

    def test_users_only_see_their_own_notes(self):
        note = self.service.create("ana", "Groceries")
        self.service.create("bo", "Secret")
        self.assertEqual([n.title for n in self.service.mine("ana")], ["Groceries"])
        with self.assertRaises(NotFound):
            self.service.read("bo", note.id)

    def test_titles_are_required(self):
        with self.assertRaises(ValueError):
            self.service.create("ana", "  ")


if __name__ == "__main__":
    unittest.main()
