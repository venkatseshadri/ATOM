"""Module 15 — Telemetry & Audit (Phase 0: minimal working collector).

Every module emits a TraceEvent here so a full pass is auditable.
"""
from __future__ import annotations

from .contracts import TraceEvent
from .util import now


class Telemetry:
    def __init__(self, echo: bool = True) -> None:
        self.events: list[TraceEvent] = []
        self.echo = echo

    def emit(self, source: str, type: str, payload: dict | None = None) -> TraceEvent:
        ev = TraceEvent(source=source, type=type, payload=payload or {}, ts=now())
        self.events.append(ev)
        if self.echo:
            print(f"[trace] {source:<16} {type:<22} {payload or ''}")
        return ev

    def sources(self) -> set[str]:
        return {e.source for e in self.events}
