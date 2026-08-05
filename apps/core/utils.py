"""Small, dependency-free helpers shared by every app."""

from __future__ import annotations

import os
import secrets
import threading
import time
import uuid
from decimal import ROUND_HALF_UP, Decimal

__all__ = ["uuid7", "money", "short_code", "mask"]

_uuid_lock = threading.Lock()
_uuid_last_ms = 0
_uuid_counter = 0

_COUNTER_MAX = 0x0FFF  # 12 bits of rand_a, RFC 9562 monotonic counter method


def uuid7() -> uuid.UUID:
    """Time-ordered UUID (RFC 9562 v7) with a monotonic sub-millisecond counter.

    Random UUIDv4 primary keys destroy B-tree locality: every insert lands in a
    random leaf page, which on a table with millions of folio lines turns into
    constant page splits and a cold cache. v7 keeps inserts append-mostly while
    staying globally unique and non-guessable enough for public IDs.

    The counter matters. Bulk inserts happen far faster than the millisecond
    timestamp advances, and without it two rows created in the same millisecond
    sort randomly — which quietly breaks cursor pagination ordered by id and any
    "latest row wins" logic.

    ``uuid.uuid7`` only exists from Python 3.14; ASHOS pins 3.12 (goal.txt D01),
    so it is implemented here and swapped out when the pin moves.
    """
    global _uuid_last_ms, _uuid_counter

    with _uuid_lock:
        now_ms = int(time.time() * 1000) & 0xFFFFFFFFFFFF
        if now_ms > _uuid_last_ms:
            _uuid_last_ms = now_ms
            _uuid_counter = secrets.randbelow(_COUNTER_MAX >> 1)  # leave headroom
        else:
            _uuid_counter += 1
            if _uuid_counter > _COUNTER_MAX:
                # Counter exhausted inside one millisecond: step the clock
                # forward rather than wrap and break ordering.
                _uuid_last_ms += 1
                _uuid_counter = 0
            now_ms = _uuid_last_ms
        timestamp, counter = now_ms, _uuid_counter

    raw = bytearray(timestamp.to_bytes(6, "big") + secrets.token_bytes(10))
    raw[6] = 0x70 | (counter >> 8)  # version 7 + counter high nibble
    raw[7] = counter & 0xFF  # counter low byte
    raw[8] = (raw[8] & 0x3F) | 0x80  # RFC 4122 variant
    return uuid.UUID(bytes=bytes(raw))


def money(value: Decimal | float | int | str, places: str = "0.01") -> Decimal:
    """Quantise to currency precision with banker-safe rounding.

    Every monetary value in ASHOS goes through this. Float arithmetic on money
    is how invoices end up off by one poisha and a night audit fails to balance.
    """
    return Decimal(str(value)).quantize(Decimal(places), rounding=ROUND_HALF_UP)


def short_code(length: int = 8, alphabet: str = "ABCDEFGHJKMNPQRSTUVWXYZ23456789") -> str:
    """Human-readable code for confirmations and QR check-in.

    Ambiguous glyphs (0/O, 1/I/L) are excluded because guests read these aloud
    over the phone and staff type them in from a printed slip.
    """
    return "".join(secrets.choice(alphabet) for _ in range(length))


def mask(value: str | None, keep: int = 4) -> str:
    """Mask an identifier for logs. Passport numbers must never appear in full."""
    if not value:
        return ""
    if len(value) <= keep:
        return "*" * len(value)
    return "*" * (len(value) - keep) + value[-keep:]


def env_flag(name: str, default: bool = False) -> bool:
    """Read a boolean env var outside Django settings (entrypoints, scripts)."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}
