"""PIN hashing and brute-force lockout maths. Pure."""
from __future__ import annotations

import hashlib
import hmac
import os
from typing import Optional

ITERATIONS = 200_000


def valid_pin(pin: str) -> Optional[str]:
    if not pin.isdigit() or not (4 <= len(pin) <= 8):
        return "Use 4 to 8 digits."
    if len(set(pin)) == 1:
        return "Choose a PIN that isn't a single repeated digit."
    return None


def new_salt() -> str:
    return os.urandom(16).hex()


def hash_pin(pin: str, salt_hex: str, iterations: int = ITERATIONS) -> str:
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt_hex), iterations).hex()


def verify(pin: str, salt_hex: str, hash_hex: str, iterations: int = ITERATIONS) -> bool:
    return hmac.compare_digest(hash_pin(pin, salt_hex, iterations), hash_hex)


def lockout_seconds(failures: int) -> int:
    """No delay for the first 4 wrong PINs, then 30 s doubling up to 1 hour."""
    return 0 if failures < 5 else min(30 * 2 ** (failures - 5), 3600)
