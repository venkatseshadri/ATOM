"""Module 11 — Connectivity & Auth (STUB). Owns the broker Session (single source)."""
from __future__ import annotations

from ..contracts import Session
from ..util import now


class Auth:
    def __init__(self, telemetry) -> None:
        self.t = telemetry

    def login(self) -> Session:
        self.t.emit("auth", "login", {})
        return Session(broker="paper", token="canned-token",
                       state="authenticated", expires_at=now())
