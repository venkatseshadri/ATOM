"""Tiny shared helpers."""
from __future__ import annotations

from datetime import datetime, timezone


def now() -> str:
    """ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()
