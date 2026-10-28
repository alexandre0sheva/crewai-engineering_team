import unittest

from accounts.passwords import verify_password
from accounts.users import Users


class UsersTest(unittest.TestCase):
    def test_register_hashes_the_password(self):
        user = Users().register(" Ana@Example.org ", "correct horse battery")
        self.assertEqual(user.email, "ana@example.org")
        self.assertTrue(verify_password("correct horse battery", user.password_hash))
        self.assertFalse(verify_password("wrong", user.password_hash))

    def test_duplicate_and_short_passwords_are_refused(self):
        users = Users()
        users.register("ana@example.org", "correct horse battery")
        with self.assertRaises(ValueError):
            users.register("ANA@example.org", "another long password")
        with self.assertRaises(ValueError):
            users.register("bo@example.org", "short")


if __name__ == "__main__":
    unittest.main()
