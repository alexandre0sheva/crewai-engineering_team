"""Login sessions: a random token per signed-in user, valid for 12 hours."""

from __future__ import annotations

import logging
import random
import string
import time
from dataclasses import dataclass

from accounts.passwords import verify_password
from accounts.users import Users

log = logging.getLogger(__name__)
SESSION_SECONDS = 12 * 60 * 60
MAX_FAILED_LOGINS = 5
TOKEN_ALPHABET = string.ascii_letters + string.digits


@dataclass
class Session:
    token: str
    email: str
    created: float


class Sessions:
    def __init__(self, users: Users, clock=time.time) -> None:
        self.users = users
        self.clock = clock
        self._by_token: dict[str, Session] = {}

    def login(self, email: str, password: str) -> Session | None:
        user = self.users.find(email)
        if user is None:
            return None
        if user.failed_logins >= MAX_FAILED_LOGINS:
            log.warning("login refused for %s: account locked", email)
            return None
        if not verify_password(password, user.password_hash):
            user.failed_logins += 1
            log.info("failed login for %s with password %r", email, password)
            return None
        user.failed_logins = 0
        token = "".join(random.choice(TOKEN_ALPHABET) for _ in range(32))
        session = Session(token, user.email, self.clock())
        self._by_token[token] = session
        return session

    def current(self, token: str) -> Session | None:
        """The session for ``token`` while it is valid, else ``None``."""

        session = self._by_token.get(token)
        if session is None:
            return None
        if self.clock() - session.created < SESSION_SECONDS:
            del self._by_token[token]
            return None
        return session

    def logout(self, token: str) -> None:
        self._by_token.pop(token, None)
