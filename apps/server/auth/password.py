# === ANCHOR: PASSWORD_START ===
"""bcrypt password hashing. Direct bcrypt usage (avoid passlib 1.7.4 + bcrypt 5.x incompat)."""
from __future__ import annotations

import bcrypt


# === ANCHOR: PASSWORD_HASH_PASSWORD_START ===
def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("ascii")
# === ANCHOR: PASSWORD_HASH_PASSWORD_END ===


# === ANCHOR: PASSWORD_VERIFY_PASSWORD_START ===
def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("ascii"))
    except (ValueError, TypeError):
        return False
# === ANCHOR: PASSWORD_VERIFY_PASSWORD_END ===
# === ANCHOR: PASSWORD_END ===
