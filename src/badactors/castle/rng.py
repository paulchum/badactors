"""Seeded rolls as a cursor, not an object.

The world holds a ``seed`` and a ``rng_cursor``. A roll is a pure function of the
two plus a purpose string, so a saved world replays every roll exactly, and each
roll is recorded on the ledger entry that consumed it.
"""

from __future__ import annotations

import hashlib

from badactors.castle.contracts import RngDraw

_SCALE = float(1 << 53)


def draw(
    seed: int,
    cursor: int,
    purpose: str,
    *,
    threshold: float | None = None,
) -> RngDraw:
    """Return the roll at ``cursor`` without touching any global state."""
    digest = hashlib.sha256(f"{seed}:{cursor}:{purpose}".encode()).digest()
    value = (int.from_bytes(digest[:8], "big") >> 11) / _SCALE
    return RngDraw(
        cursor=cursor,
        purpose=purpose,
        value=value,
        threshold=threshold,
        success=None if threshold is None else value < threshold,
    )
