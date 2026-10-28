"""Password hashing with scrypt (standard library). Hashes are ``scrypt$<salt hex>$<hash hex>``."""

from __future__ import annotations

import hashlib
import hmac
import secrets

N, R, P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=N, r=R, p=P)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=N, r=R, p=P)
    return hmac.compare_digest(digest.hex(), digest_hex)
