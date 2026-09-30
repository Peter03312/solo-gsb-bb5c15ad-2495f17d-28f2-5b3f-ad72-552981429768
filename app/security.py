"""凭据哈希与校验。

评审人凭据以 PBKDF2-HMAC-SHA256 + 随机盐存储，数据库中不出现明文。
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

_ALGO = "pbkdf2_sha256"
_ITERATIONS = 200_000


def hash_secret(plain: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", plain.encode(), salt.encode(),
                             _ITERATIONS)
    return f"{_ALGO}${_ITERATIONS}${salt}${dk.hex()}"


def verify_secret(plain: str, encoded: str) -> bool:
    try:
        algo, iters, salt, want = encoded.split("$")
        if algo != _ALGO:
            return False
        dk = hashlib.pbkdf2_hmac("sha256", plain.encode(), salt.encode(),
                                 int(iters))
        return hmac.compare_digest(dk.hex(), want)
    except (ValueError, TypeError):
        return False
