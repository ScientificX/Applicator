"""Normalized domain objects shared across layers.

Why this module exists: the pinned protocols (docs/design.md 7.1 and 7.3) reference `Source`,
`Posting` and `Application`, but no module owned them. Added at G1 for `Source`/`Posting`;
`Application` arrives with `apply/` at G3/G5 rather than being front-run.

DEVIATION (recorded per AGENTS.md 8.1 rule 2): `models.py` is not in the AGENTS.md 10 module
map as of this branch. Mirrored into docs/architecture.md 3 and docs/design.md 1 and 7.

Field names mirror the `postings` table columns (docs/design.md 4.1) so the mapping to SQL is
obvious and drift is visible.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Source:
    """A configured discovery source - one row of the `sources` table."""

    family: str
    config: dict[str, Any] = field(default_factory=dict)
    id: int | None = None
    last_polled: str | None = None


@dataclass
class Posting:
    """One normalized posting, as yielded by `DiscoverySource.discover()`."""

    source: str
    source_id: str
    company: str
    title: str

    location: str | None = None
    remote: bool | None = None
    posted_at: str | None = None
    jd_text: str | None = None
    jd_hash: str | None = None
    apply_url: str | None = None
    channel_hint: str | None = None
    contact_emails: list[str] = field(default_factory=list)
    visa_signals: dict[str, Any] = field(default_factory=dict)

    # channel extensions (plan-workday.md 14): Workday sets external_path/req_id/can_apply
    external_path: str | None = None
    req_id: str | None = None
    can_apply: bool | None = None

    company_id: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)
