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

    print("\n=== traced FULL-DAY session (ENTER/EXIT per module method) ===")
    orch = Orchestrator()
    final = orch.run_session("NIFTY")

    print(f"\n=== end of day → position {final.fsm_state}, "
          f"realized P&L ₹{final.realized_pnl:+,.0f} ===")
    print(f"modules that emitted a trace: {len(orch.t.sources())}/16")


if __name__ == "__main__":
    main()
