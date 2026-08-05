"""Symmetric encryption for data that must not be readable in a DB dump.

Two categories need this in ASHOS:
  * AI provider API keys stored in AI Center (a leaked backup = a leaked bill)
  * biometric embeddings (goal.txt D10 #4)

Key management: ``FIELD_ENCRYPTION_KEY`` if set, otherwise derived from
``SECRET_KEY``. Derivation keeps development frictionless, but rotating
``SECRET_KEY`` then makes every ciphertext unreadable — so production MUST set
``FIELD_ENCRYPTION_KEY`` explicitly and rotate it deliberately.
"""

from __future__ import annotations

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

PREFIX = "enc:v1:"


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    key = getattr(settings, "FIELD_ENCRYPTION_KEY", "") or ""
    if not key:
        digest = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
        key = base64.urlsafe_b64encode(digest).decode()
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt(plaintext: str) -> str:
    if not plaintext:
        return ""
    if plaintext.startswith(PREFIX):
        return plaintext  # already encrypted; do not double-wrap
    return PREFIX + _fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    if not ciphertext:
        return ""
    if not ciphertext.startswith(PREFIX):
        # Value predates encryption or was written directly by a migration.
        return ciphertext
    try:
        return _fernet().decrypt(ciphertext[len(PREFIX) :].encode()).decode()
    except InvalidToken:
        # Wrong key. Returning "" beats crashing the request: the caller falls
        # back to the environment default and the misconfiguration surfaces as
        # an auth failure against the provider, which is loud but recoverable.
        return ""
