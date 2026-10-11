"""Title gate: the include/allow + exclude/veto semantics from plan-general.md section 2.1.

SEMANTICS (pinned here so nothing drifts):
    pass = any(title_include pattern matches) AND NOT any(title_exclude pattern matches)
    matching is case-insensitive and unanchored (re.search).

`title_exclude` is a VETO, not an allow-list - a posting must also satisfy title_include.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from .config import Criteria


@dataclass(frozen=True)
class TitleVerdict:
    passed: bool
    include_hit: str | None
    veto_hit: str | None

    @property
    def reason(self) -> str:
        if self.passed:
            return f"include matched {self.include_hit!r}"
        if self.include_hit is None:
            return "no title_include pattern matched"
        return f"vetoed by {self.veto_hit!r}"


@lru_cache(maxsize=8)
def _compile(patterns: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p, re.IGNORECASE) for p in patterns)


def title_gate(title: str, criteria: Criteria) -> TitleVerdict:
    """Apply the include/allow-list and the exclude/veto to one posting title."""
    target = criteria.target
    inc = _compile(tuple(target.title_include))
    exc = _compile(tuple(target.title_exclude))

    include_hit = next((p.pattern for p in inc if p.search(title)), None)
    veto_hit = next((p.pattern for p in exc if p.search(title)), None)
    return TitleVerdict(
        passed=include_hit is not None and veto_hit is None,
        include_hit=include_hit,
        veto_hit=veto_hit,
    )
