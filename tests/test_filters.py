"""Title-gate battery - the cases validated by hand during planning.

These pin the semantics of config/criteria.yaml. If someone edits the include/veto lists
and a case here flips, that is the edit being caught, not a broken test.
"""

from __future__ import annotations

import pytest

from jobagent.config import load_criteria
from jobagent.filters import title_gate

# (title, should_pass)
CASES: list[tuple[str, bool]] = [
    # --- must pass -----------------------------------------------------------
    ("Senior Software Engineer", True),
    ("Java Engineer", True),
    ("Software Engineer III", True),
    ("Assistant Vice President, Software Engineering", True),
    ("Associate Vice President - Backend", True),
    ("Assistant VP, Java", True),
    ("Associate VP - Backend", True),
    ("AVP, Java Developer", True),
    # --- must be dropped -----------------------------------------------------
    ("VP, Software Engineering", False),          # vetoed by the guarded \bVP\b
    ("Vice President of Engineering", False),     # dropped by include (no veto hit)
    ("Associate Software Engineer", False),       # include has to match, veto is not enough
    ("Engineering Manager", False),
    ("Director of Engineering", False),
    ("Principal Engineer", False),
    ("Graduate Java Engineer", False),            # matches include, then vetoed
    ("Senior Software Engineer Intern", False),   # matches include, then vetoed
    ("Analyst, Backend", False),
]


@pytest.mark.parametrize(("title", "expected"), CASES)
def test_title_gate(title: str, expected: bool) -> None:
    verdict = title_gate(title, load_criteria())
    assert verdict.passed is expected, f"{title!r}: {verdict.reason}"


def test_veto_guard_never_eats_avp_equivalents() -> None:
    """The (?<!Assistant )(?<!Associate ) lookbehind is load-bearing."""
    criteria = load_criteria()
    for title in ("Assistant VP, Java", "Associate VP - Backend"):
        verdict = title_gate(title, criteria)
        assert verdict.passed, f"{title!r} should not be vetoed ({verdict.reason})"
        assert verdict.veto_hit is None


def test_veto_still_kills_plain_vp() -> None:
    verdict = title_gate("VP, Software Engineering", load_criteria())
    assert not verdict.passed
    assert verdict.veto_hit is not None, "plain VP must be caught by the veto"
