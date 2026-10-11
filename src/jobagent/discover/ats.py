"""Non-Workday discovery sources: three documented ATS board APIs + one aggregator.

Every family yields `Posting` and inherits the upsert/freshness `poll()` from
`BaseDiscoverySource`. Each takes an injectable `HttpClient` so tests run offline.

Endpoint shapes were verified live (probe log in the G1 MR):
  greenhouse  GET boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true  -> {"jobs": [...]}
  lever       GET api.lever.co/v0/postings/{co}?mode=json                       -> [ {...} ]
  ashby       GET api.ashbyhq.com/posting-api/job-board/{org}                   -> {"jobs": [...]}
  remotive    GET remotive.com/api/remote-jobs                                  -> {"jobs": [...]}
"""

from __future__ import annotations

import html
import re
from collections.abc import AsyncIterator

from ..config import Criteria
from ..models import Posting, Source
from ..net import HttpClient
from .base import BaseDiscoverySource

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t\r\f\v]+")
_NL = re.compile(r"\n{3,}")


def strip_html(raw: str | None) -> str | None:
    """Cheap HTML -> text for jd_text. Whitespace is collapsed; blank lines preserved."""
    if not raw:
        return None
    text = _TAG.sub(" ", raw)
    text = html.unescape(text)
    text = _WS.sub(" ", text)
    text = _NL.sub("\n\n", text)
    return text.strip() or None


def emails_in(text: str | None) -> list[str]:
    """Published contact addresses only. Never pattern-guessed (plan-general.md section 6.4)."""
    if not text:
        return []
    found = re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)
    return sorted({e.lower() for e in found})


class GreenhouseSource(BaseDiscoverySource):
    family = "greenhouse"
    URL = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"

    def __init__(self, client: HttpClient, boards: list[str]) -> None:
        self._client, self._boards = client, boards

    async def discover(self, source_row: Source, criteria: Criteria) -> AsyncIterator[Posting]:
        for board in self._boards:
            resp = await self._client.get_json(self.URL.format(board=board))
            if not resp.ok:
                continue
            for job in resp.json().get("jobs", []):
                jd = strip_html(job.get("content"))
                yield Posting(
                    source=self.family,
                    source_id=f"{board}:{job.get('id')}",
                    company=job.get("company_name") or board,
                    title=(job.get("title") or "").strip(),
                    location=(job.get("location") or {}).get("name"),
                    remote=None,
                    posted_at=job.get("updated_at"),
                    jd_text=jd,
                    apply_url=job.get("absolute_url"),
                    channel_hint="ats_api",
                    contact_emails=emails_in(jd),
                    extra={"board": board},
                )


class LeverSource(BaseDiscoverySource):
    family = "lever"
    URL = "https://api.lever.co/v0/postings/{board}?mode=json"

    def __init__(self, client: HttpClient, boards: list[str]) -> None:
        self._client, self._boards = client, boards

    async def discover(self, source_row: Source, criteria: Criteria) -> AsyncIterator[Posting]:
        for board in self._boards:
            resp = await self._client.get_json(self.URL.format(board=board))
            if not resp.ok:
                continue
            payload = resp.json()
            if not isinstance(payload, list):
                continue
            for job in payload:
                cats = job.get("categories") or {}
                jd = job.get("descriptionPlain") or strip_html(job.get("description"))
                yield Posting(
                    source=self.family,
                    source_id=f"{board}:{job.get('id')}",
                    company=board,
                    title=(job.get("text") or "").strip(),
                    location=cats.get("location"),
                    remote="remote" in (cats.get("location") or "").lower(),
                    posted_at=job.get("createdAt"),
                    jd_text=jd,
                    apply_url=job.get("hostedUrl"),
                    channel_hint="ats_api",
                    contact_emails=emails_in(jd),
                    extra={"board": board, "team": cats.get("team")},
                )


class AshbySource(BaseDiscoverySource):
    family = "ashby"
    URL = "https://api.ashbyhq.com/posting-api/job-board/{board}"

    def __init__(self, client: HttpClient, boards: list[str]) -> None:
        self._client, self._boards = client, boards

    async def discover(self, source_row: Source, criteria: Criteria) -> AsyncIterator[Posting]:
        for board in self._boards:
            resp = await self._client.get_json(self.URL.format(board=board))
            if not resp.ok:
                continue
            for job in resp.json().get("jobs", []):
                jd = strip_html(job.get("descriptionHtml"))
                yield Posting(
                    source=self.family,
                    source_id=f"{board}:{job.get('id')}",
                    company=board,
                    title=(job.get("title") or "").strip(),
                    location=job.get("location"),
                    remote=bool(job.get("isRemote")) if "isRemote" in job else None,
                    posted_at=job.get("publishedAt"),
                    jd_text=jd,
                    apply_url=job.get("jobUrl"),
                    channel_hint="ats_api",
                    contact_emails=emails_in(jd),
                    extra={"board": board, "department": job.get("department")},
                )


class RemotiveSource(BaseDiscoverySource):
    """Aggregator. Verified to expose no employer-ATS URLs, so it is a posting source only -
    it cannot feed the Workday inverted harvest (plan-workday.md 6.3)."""

    family = "remotive"
    URL = "https://remotive.com/api/remote-jobs"

    def __init__(self, client: HttpClient, boards: list[str] | None = None) -> None:
        self._client = client

    async def discover(self, source_row: Source, criteria: Criteria) -> AsyncIterator[Posting]:
        resp = await self._client.get_json(self.URL)
        if not resp.ok:
            return
        for job in resp.json().get("jobs", []):
            jd = strip_html(job.get("description"))
            yield Posting(
                source=self.family,
                source_id=str(job.get("id")),
                company=(job.get("company_name") or "").strip(),
                title=(job.get("title") or "").strip(),
                location=job.get("candidate_required_location"),
                remote=True,
                posted_at=job.get("publication_date"),
                jd_text=jd,
                apply_url=job.get("url"),
                channel_hint="aggregator",
                contact_emails=emails_in(jd),
                extra={"category": job.get("category"), "job_type": job.get("job_type")},
            )


FAMILIES: dict[str, type[BaseDiscoverySource]] = {
    GreenhouseSource.family: GreenhouseSource,
    LeverSource.family: LeverSource,
    AshbySource.family: AshbySource,
    RemotiveSource.family: RemotiveSource,
}
