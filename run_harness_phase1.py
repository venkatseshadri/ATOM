#!/usr/bin/env python3
"""Run the Phase-1 PORCUPINE scenarios (real pipeline, not the Phase-0 skeleton tape)."""
import contextlib
import sys

sys.path.insert(0, "src")

from atom.harness_phase1 import run_all   # noqa: E402


def report() -> bool:
    print("=== ATOM PORCUPINE harness — Phase 1 (real pipeline) ===")
    print("-" * 70)
    results = run_all()
    all_ok = True
    for r in results:
        flag = "PASS" if r.passed else "FAIL"
        print(f"[{flag}] {r.name}")
        for desc, ok in r.checks:
            print(f"        {'✓' if ok else '✗'} {desc}")
        all_ok = all_ok and r.passed
    n = sum(1 for r in results if r.passed)
    print("-" * 70)
    print(f"{n}/{len(results)} scenarios passed")
    return all_ok


def main() -> None:
    with open("logs/harness_phase1.log", "w") as f, contextlib.redirect_stdout(f):
        ok = report()
    ok = report()   # also to stdout
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
