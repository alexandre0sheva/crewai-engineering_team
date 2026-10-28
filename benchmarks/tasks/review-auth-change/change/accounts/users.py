"""The user registry (in memory; the site persists it elsewhere)."""

from __future__ import annotations

from dataclasses import dataclass

from accounts.passwords import hash_password


@dataclass
class User:
    email: str
    password_hash: str
    failed_logins: int = 0


class Users:
    def __init__(self) -> None:
        self._by_email: dict[str, User] = {}

    def register(self, email: str, password: str) -> User:
        email = email.strip().lower()
        if email in self._by_email:
            raise ValueError("that email is already registered")
        if len(password) < 10:
            raise ValueError("passwords have at least 10 characters")
        user = User(email, hash_password(password))
        self._by_email[email] = user
        return user

    def find(self, email: str) -> User | None:
        return self._by_email.get(email.strip().lower())
