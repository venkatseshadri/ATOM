#!/usr/bin/env python3
"""Run one Phase 0 skeleton pass and print the result."""
import sys
sys.path.insert(0, "src")

from atom.orchestrator import Orchestrator  # noqa: E402


def main() -> None:
    print("=== ATOM Phase 0 skeleton — one pass ===")
    orch = Orchestrator()
    result = orch.run_cycle("NIFTY")
    print("\n=== contract objects produced (pipeline order) ===")
    for name in ("session", "instrument", "snapshot", "regime", "decision",
                 "plan", "account", "verdict", "fills", "position", "parameter_set"):
        print(f"  {name:<14} -> {type(getattr(result, name)).__name__}")
    print(f"\nmodules that emitted a trace: {len(orch.t.sources())}/16")


if __name__ == "__main__":
    main()
