"""Encryption-at-rest for sensitive credentials (SSH keys/passwords).

Uses Fernet (AES128-CFB + HMAC) from the cryptography package. The key is derived
from ENCRYPTION_KEY if set, otherwise falls back to deriving from SECRET_KEY.
"""
import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken


def _derive_key() -> bytes:
    raw = os.getenv("ENCRYPTION_KEY") or os.getenv("SECRET_KEY", "change-me-in-production")
    digest = hashlib.sha256(raw.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _fernet() -> Fernet:
    return Fernet(_derive_key())


def encrypt(plaintext: str) -> str:
    """Encrypt a string into a storable token. Empty input → empty output."""
    if not plaintext:
        return ""
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str:
    """Decrypt a token back to plaintext. Returns '' on bad/missing token."""
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, Exception):
        return ""
