"""Device API Key hashing (sha256)."""
from __future__ import annotations

import hashlib
import secrets


def generate_api_key() -> str:
    return secrets.token_urlsafe(32)


def hash_api_key(plain: str) -> str:
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()


def verify_api_key(plain: str, hashed_hex: str) -> bool:
    return secrets.compare_digest(hash_api_key(plain), hashed_hex)
