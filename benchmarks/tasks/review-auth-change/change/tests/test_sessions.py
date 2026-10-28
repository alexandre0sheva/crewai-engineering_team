import unittest

from accounts.sessions import Sessions
from accounts.users import Users


class SessionsTest(unittest.TestCase):
    def setUp(self):
        self.users = Users()
        self.users.register("ana@example.org", "correct horse battery")
        self.sessions = Sessions(self.users)

    def test_login_gives_a_token(self):
        session = self.sessions.login("ana@example.org", "correct horse battery")
        self.assertEqual(len(session.token), 32)

    def test_wrong_password_and_lockout(self):
        for _ in range(5):
            self.assertIsNone(self.sessions.login("ana@example.org", "wrong"))
        self.assertIsNone(self.sessions.login("ana@example.org", "correct horse battery"))


if __name__ == "__main__":
    unittest.main()
