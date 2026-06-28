#!/usr/bin/env python3
"""Run one Phase 0 skeleton pass and print the result.

Each module method prints an ENTER line (with its file + module.method) and an EXIT
line; the body is an unimplemented stub. Lets you validate the wiring against the
actually-built files.
"""
import glob
import sys

sys.path.insert(0, "src")

from atom.orchestrator import Orchestrator  # noqa: E402


def main() -> None:
    print("=== ATOM Phase 0 — walking skeleton: one session flow (dummy data) ===")

    print("\n=== built files (Phase 0) ===")
    for f in sorted(glob.glob("src/atom/**/*.py", recursive=True)):
        print(f"  {f}")

    print("\n=== traced session pass (ENTER/EXIT per module method) ===")
    orch = Orchestrator()
    result = orch.run_cycle("NIFTY")

    print("\n=== contract objects produced (pipeline order) ===")
    for name in ("session", "instrument", "snapshot", "regime", "decision",
                 "plan", "account", "verdict", "fills", "position", "parameter_set"):
        print(f"  {name:<14} -> {type(getattr(result, name)).__name__}")
    print(f"\nmodules that emitted a trace: {len(orch.t.sources())}/16")


if __name__ == "__main__":
    main()
