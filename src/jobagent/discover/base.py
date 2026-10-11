"""Discovery protocols - the seam between the program and each channel implementation.

Signatures are pinned by docs/design.md 7.1 (DiscoverySource) and 7.2 (TenantSource).
Do not re-invent these: a channel implements them and the hunt layer consumes them without
channel-specific branches (plan-workday.md 6.4, 11).
"""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..config import Criteria
from ..models import Posting, Source


@dataclass(frozen=True)
class TenantCandidate:
    """A company believed to run a given ATS board. Never authoritative until probed."""

    host: str
    tenant: str
    site: str | None
    source: str
    evidence: dict[str, Any] = field(default_factory=dict)


class DiscoverySource(Protocol):
    """One posting family (an ATS board API, or an aggregator)."""

    family: str

    def discover(self, source_row: Source, criteria: Criteria) -> AsyncIterator[Posting]:
        """Yield normalized postings for one configured source; idempotent by (source, source_id)."""

    async def poll(self, conn: sqlite3.Connection, criteria: Criteria) -> int:
        """Upsert postings, refresh first_seen/last_seen/active, return the count seen."""


class TenantSource(Protocol):
    """One way of proposing ATS tenants (inverted harvest, seed list, brute force)."""

    def yield_candidates(self, criteria: Criteria) -> AsyncIterator[TenantCandidate]:
        """Yield unverified tenant candidates for a channel's oracle to verify."""


class BaseDiscoverySource:
    """Convenience base: implement `discover()`, inherit a correct `poll()`.

    Keeps every family module thin and guarantees the upsert/freshness semantics are identical
    across families (plan-general.md section 4).
    """

    family: str = "base"
    batch_size: int = 200

    async def discover(self, source_row: Source, criteria: Criteria) -> AsyncIterator[Posting]:
        raise NotImplementedError
        yield  # pragma: no cover - makes this an async generator for type checkers

    async def poll(self, conn: sqlite3.Connection, criteria: Criteria, *, source_row: Source | None = None) -> int:
        from .store import mark_inactive, touch_source, upsert_postings, utcnow

        row = source_row or Source(family=self.family)
        started = utcnow()
        batch: list[Posting] = []
        seen: set[str] = set()
        count = 0
        async for posting in self.discover(row, criteria):
            batch.append(posting)
            seen.add(posting.source_id)
            count += 1
            if len(batch) >= self.batch_size:
                upsert_postings(conn, batch)
                batch.clear()
        if batch:
            upsert_postings(conn, batch)
        if seen:
            mark_inactive(conn, self.family, seen)
        touch_source(conn, self.family, started)
        conn.commit()
        return count
