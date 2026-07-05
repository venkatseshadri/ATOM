#!/usr/bin/env python3
"""Run the Phase-3 PORCUPINE scenarios — real-pipeline risk-gate/fault scenarios plus
direct fault-injection checks for Modules 6/7/11. Closes the "still open" Phase 3 gap
in docs/PORCUPINE.md."""
import contextlib
import sys

sys.path.insert(0, "src")

from atom.harness_phase3 import run_all           # noqa: E402
from atom.scenarios_phase3 import DIRECT_CHECKS    # noqa: E402


def report() -> bool:
    print("=== ATOM PORCUPINE harness — Phase 3 (risk gate + stop management faults) ===")
    print("-" * 70)
    print("-- pipeline-driven (runner.run_once, real fixture) --")
    results = run_all()
    all_ok = True
    for r in results:
        flag = "PASS" if r.passed else "FAIL"
        print(f"[{flag}] {r.name}")
        for desc, ok in r.checks:
            print(f"        {'✓' if ok else '✗'} {desc}")
        all_ok = all_ok and r.passed
    n = sum(1 for r in results if r.passed)
    print(f"{n}/{len(results)} pipeline scenarios passed")

    print("\n-- direct fault-injection (Modules 6/7/11, not yet wired into run_once) --")
    d_ok = 0
    for name, title, fn in DIRECT_CHECKS:
        ok = fn()
        d_ok += int(ok)
        print(f"[{'PASS' if ok else 'FAIL'}] {name} — {title}")
        all_ok = all_ok and ok
    print(f"{d_ok}/{len(DIRECT_CHECKS)} direct checks passed")

    print("-" * 70)
    total = len(results) + len(DIRECT_CHECKS)
    passed = n + d_ok
    print(f"{passed}/{total} total scenarios passed")
    return all_ok


def main() -> None:
    with open("logs/harness_phase3.log", "w") as f, contextlib.redirect_stdout(f):
        ok = report()
    ok = report()   # also to stdout
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
