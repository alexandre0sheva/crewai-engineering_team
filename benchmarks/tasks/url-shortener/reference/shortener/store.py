import secrets
import sqlite3
import string
import threading

ALPHABET = string.ascii_letters + string.digits


class Store:
    def __init__(self, path: str = ":memory:") -> None:
        self.lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS links (code TEXT PRIMARY KEY, url TEXT UNIQUE, hits INTEGER DEFAULT 0)"
        )

    def shorten(self, url: str) -> tuple[str, bool]:
        """(code, created)."""
        with self.lock:
            row = self.db.execute("SELECT code FROM links WHERE url = ?", (url,)).fetchone()
            if row:
                return row[0], False
            while True:
                code = "".join(secrets.choice(ALPHABET) for _ in range(6))
                if not self.db.execute("SELECT 1 FROM links WHERE code = ?", (code,)).fetchone():
                    break
            self.db.execute("INSERT INTO links (code, url) VALUES (?, ?)", (code, url))
            self.db.commit()
            return code, True

    def visit(self, code: str) -> str | None:
        with self.lock:
            row = self.db.execute("SELECT url FROM links WHERE code = ?", (code,)).fetchone()
            if row:
                self.db.execute("UPDATE links SET hits = hits + 1 WHERE code = ?", (code,))
                self.db.commit()
            return row[0] if row else None

    def stats(self, code: str) -> dict | None:
        with self.lock:
            row = self.db.execute("SELECT url, hits FROM links WHERE code = ?", (code,)).fetchone()
        return {"code": code, "url": row[0], "hits": row[1]} if row else None
