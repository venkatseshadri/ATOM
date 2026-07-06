#!/usr/bin/env python3
"""Run the Phase-4 PORCUPINE scenarios — direct fault injection for Modules 14/15/16
(no single pipeline path spans all three the way Module 5 did for Phase 3)."""
import contextlib
import sys

sys.path.insert(0, "src")

from atom.scenarios_phase4 import DIRECT_CHECKS    # noqa: E402


def report() -> bool:
    print("=== ATOM PORCUPINE harness — Phase 4 (ledger/audit/config faults) ===")
    print("-" * 70)
    all_ok = True
    passed = 0
    for name, title, fn in DIRECT_CHECKS:
        ok = fn()
        passed += int(ok)
        print(f"[{'PASS' if ok else 'FAIL'}] {name} — {title}")
        all_ok = all_ok and ok
    print("-" * 70)
    print(f"{passed}/{len(DIRECT_CHECKS)} scenarios passed")
    return all_ok


def main() -> None:
    with open("logs/harness_phase4.log", "w") as f, contextlib.redirect_stdout(f):
        ok = report()
    ok = report()   # also to stdout
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
