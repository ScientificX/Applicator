"""Workday `cxs` discovery (`G1-W2`).

Owner: plan-workday.md section 7. Contract: docs/design.md 8.2.

Verified constraints honoured here (docs/design.md 8.2):
  * `limit` is capped at 20 - a value of 100 returns no `jobPostings` at all.
  * `total` caps at 2000, so a tenant is sized by summing `facets`, never by `total`.
  * No auth and no CSRF token are required for this endpoint.
  * Paging is `offset`-based; we bound it with `max_pages` so one huge tenant cannot
    monopolise a poll.

`postedOn` arrives as prose ("Posted Today", "Posted 3 Days Ago", "Posted 30+ Days Ago"),
not a date. `parse_posted_on()` converts it to a best-effort ISO date so freshness ordering
works; the raw string is preserved in `Posting.extra` and the approximation is documented
rather than hidden.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from ..config import Criteria
from ..models import Posting, Source
from ..net import HttpClient
from .base import BaseDiscoverySource

PAGE_LIMIT = 20  # verified cap


@dataclass(frozen=True)
class TenantRef:
    """A verified tenant, i.e. a row of `tenants` with status `live`."""

    host: str
    tenant: str
    site: str
    locale: str = "en-US"
    facet_counts: dict[str, Any] = field(default_factory=dict)

    @property
    def base(self) -> str:
        return f"https://{self.host}/wday/cxs/{self.tenant}/{self.site}"

    def job_url(self, external_path: str) -> str:
        return f"https://{self.host}/{self.locale}/{self.site}{external_path}"


def parse_posted_on(raw: str | None, *, today: date | None = None) -> str | None:
    """Best-effort ISO date from Workday's prose `postedOn`. Documented approximation."""
    if not raw:
        return None
    today = today or date.today()
    text = raw.lower()
    if "today" in text:
        return today.isoformat()
    if "yesterday" in text:
        return (today - timedelta(days=1)).isoformat()
    match = re.search(r"(\d+)\s*\+?\s*day", text)
    if match:
        return (today - timedelta(days=int(match.group(1)))).isoformat()
    if "30+" in text:
        return (today - timedelta(days=30)).isoformat()
    return None


def parse_job_posting(job: dict[str, Any], tenant: TenantRef) -> Posting:
    """Map one `jobPostings[]` entry to a normalized `Posting`. Pure function - unit tested."""
    external_path = job.get("externalPath") or ""
    bullets = job.get("bulletFields") or []
    req_id = bullets[0] if bullets else None
    locations = job.get("locationsText") or ""
    posted_on = job.get("postedOn")
    return Posting(
        source="workday",
        source_id=f"{tenant.tenant}:{external_path or job.get('title', '')}",
        company=tenant.tenant,
        title=(job.get("title") or "").strip(),
        location=locations or None,
        remote="remote" in locations.lower() or None,
        posted_at=parse_posted_on(posted_on),
        apply_url=tenant.job_url(external_path) if external_path else None,
        channel_hint="web_form",
        external_path=external_path or None,
        req_id=req_id,
        extra={
            "posted_on_raw": posted_on,
            "location_count": locations,
            "tenant": tenant.tenant,
            "host": tenant.host,
            "site": tenant.site,
        },
    )


class WorkdayCxsDiscovery(BaseDiscoverySource):
    family = "workday"

    def __init__(
        self,
        client: HttpClient,
        tenants: list[TenantRef],
        *,
        max_pages: int = 3,
        search_text: str = "",
        applied_facets: dict[str, list[str]] | None = None,
    ) -> None:
        self._client = client
        self._tenants = tenants
        self._max_pages = max_pages
        self._search_text = search_text
        self._facets = applied_facets or {}

    async def search(self, tenant: TenantRef, *, offset: int = 0, search_text: str | None = None) -> dict[str, Any] | None:
        payload = {
            "appliedFacets": self._facets,
            "limit": PAGE_LIMIT,
            "offset": offset,
            "searchText": self._search_text if search_text is None else search_text,
        }
        resp = await self._client.post_json(f"{tenant.base}/jobs", payload)
        if not resp.ok:
            return None
        return resp.json()

    async def discover(self, source_row: Source, criteria: Criteria) -> AsyncIterator[Posting]:
        for tenant in self._tenants:
            for page in range(self._max_pages):
                payload = await self.search(tenant, offset=page * PAGE_LIMIT)
                if payload is None:
                    break
                jobs = payload.get("jobPostings") or []
                if not jobs:
                    break
                for job in jobs:
                    yield parse_job_posting(job, tenant)
                if len(jobs) < PAGE_LIMIT:
                    break

    async def fetch_detail(self, tenant: TenantRef, external_path: str) -> dict[str, Any] | None:
        """Job detail. Gated behind the `G0-W` HAR probe - see plan-workday.md section 1."""
        resp = await self._client.get_json(f"{tenant.base}{external_path}")
        if not resp.ok:
            return None
        return resp.json()
