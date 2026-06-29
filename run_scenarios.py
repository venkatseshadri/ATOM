#!/usr/bin/env python3
"""Run every Phase 0 scenario, writing one log per scenario to logs/scenarios/.

Each log carries a MOCK/REAL legend so it stays meaningful as phases turn modules real.
"""
import contextlib
import os
import sys

sys.path.insert(0, "src")

from atom.orchestrator import Orchestrator      # noqa: E402
from atom.scenarios import SCENARIOS            # noqa: E402
from atom.status import PHASE, print_legend     # noqa: E402


def main() -> None:
    os.makedirs("logs/scenarios", exist_ok=True)
    for sc in SCENARIOS:
        path = f"logs/scenarios/{sc.name}.log"
        with open(path, "w") as f, contextlib.redirect_stdout(f):
            print(f"# ATOM scenario log — {sc.name}")
            print(f"# title : {sc.title}")
            print(f"# expect: {sc.expect}")
            print(f"# phase : {PHASE} (walking skeleton — values illustrative)")
            print_legend()
            print("-" * 70)
            orch = Orchestrator()
            final = orch.run_scenario(sc)
            print(f"\n=== end → state {final.fsm_state}, "
                  f"realized P&L ₹{final.realized_pnl:+,.0f} ===")
            print(f"modules traced: {len(orch.t.sources())}/16")
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
