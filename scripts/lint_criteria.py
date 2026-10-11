#!/usr/bin/env python3
"""Lint config/criteria.yaml: run the pinned title battery through the real gate.

Same semantics as the product (jobagent.filters.title_gate):
    pass = any(title_include) AND NOT any(title_exclude), case-insensitive.
Exit code 1 if any case disagrees with expectation.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jobagent.config import load_criteria  # noqa: E402
from jobagent.filters import title_gate  # noqa: E402

# (title, should_pass) - shared with tests/test_filters.py
CASES = [
    ("Senior Software Engineer", True),
    ("Java Engineer", True),
    ("Software Engineer III", True),
    ("Assistant Vice President, Software Engineering", True),
    ("Associate Vice President - Backend", True),
    ("Assistant VP, Java", True),
    ("Associate VP - Backend", True),
    ("AVP, Java Developer", True),
    ("VP, Software Engineering", False),
    ("Vice President of Engineering", False),
    ("Associate Software Engineer", False),
    ("Engineering Manager", False),
    ("Director of Engineering", False),
    ("Principal Engineer", False),
    ("Graduate Java Engineer", False),
    ("Senior Software Engineer Intern", False),
    ("Analyst, Backend", False),
]


def main() -> int:
    criteria = load_criteria()
    bad = 0
    print(f"{'title':<46} {'verdict':<6} {'expected':<9} reason")
    print("-" * 110)
    for title, expected in CASES:
        v = title_gate(title, criteria)
        agree = v.passed == expected
        bad += not agree
        print(f"{title[:46]:<46} {'PASS' if v.passed else 'DROP':<6} "
              f"{('PASS' if expected else 'DROP'):<9} {'ok' if agree else 'MISMATCH'}  {v.reason}")
    print("-" * 110)
    printfn = print
    printfn("ALL CASES AS EXPECTED" if not bad else f"{bad} MISMATCH(ES)")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
