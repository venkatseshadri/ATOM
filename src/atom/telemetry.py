"""Module 15 — Telemetry & Audit (Phase 0: minimal working collector).

Records a TraceEvent per call (for audit + the 16/16 conformance test) and prints a
human-readable narration line so a full pass reads like an operator session log.
"""
from __future__ import annotations

from .contracts import TraceEvent
from .util import now


class Telemetry:
    def __init__(self, echo: bool = True) -> None:
        self.events: list[TraceEvent] = []
        self.echo = echo

    def emit(self, source: str, type: str, payload: dict | None = None,
             msg: str | None = None) -> TraceEvent:
        ev = TraceEvent(source=source, type=type, payload=payload or {}, ts=now())
        self.events.append(ev)
        if self.echo:
            if msg:
                print(f"  [{source:<16}] {msg}")
            else:
                print(f"  [{source:<16}] {type} {payload or ''}")
        return ev

    def stage(self, title: str) -> None:
        if self.echo:
            print(f"\n>>> {title}")

    def sources(self) -> set[str]:
        return {e.source for e in self.events}
