# === ANCHOR: DEVICE_START ===
"""Device API Key hashing (sha256)."""
from __future__ import annotations

import hashlib
import secrets


# === ANCHOR: DEVICE_GENERATE_API_KEY_START ===
def generate_api_key() -> str:
    return secrets.token_urlsafe(32)
# === ANCHOR: DEVICE_GENERATE_API_KEY_END ===


# === ANCHOR: DEVICE_HASH_API_KEY_START ===
def hash_api_key(plain: str) -> str:
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()
# === ANCHOR: DEVICE_HASH_API_KEY_END ===


# === ANCHOR: DEVICE_VERIFY_API_KEY_START ===
def verify_api_key(plain: str, hashed_hex: str) -> bool:
    return secrets.compare_digest(hash_api_key(plain), hashed_hex)
# === ANCHOR: DEVICE_VERIFY_API_KEY_END ===
# === ANCHOR: DEVICE_END ===
