"""Workday tenant discovery (`G1-W1`): shard sweep -> oracle -> site resolution -> ranking.

Owner: plan-workday.md section 6. Oracle contract: docs/design.md 8.2.

The oracle is the whole point: one `limit:1` probe per `(slug, shard, site_guess)` discriminates
three states, so tenant hunting is a guided search rather than blind guessing.

| HTTP | Envelope | Meaning                          |
|---|---|---|
| 200  | -                                    | tenant + site valid              |
| 404  | `{"errorCode":"S21"}`               | tenant exists, site slug wrong   |
| 422  | `{"errorCode":"HTTP_422"}`          | no such tenant on this shard     |

Verified at G1: per-tenant DNS says nothing (wildcard), but the per-shard **wildcard label** is a
valid *shard* test. A live shard answers for any label (`zzz.wd5.myworkdayjobs.com` resolves); a
dead shard has no wildcard, so a bogus label NXDOMAINs (`wd11`, `wd9999`). The shard **apex** is
*not* a signal - it is missing for live shards (`wd12`, `wd501`) and present for dead ones
(`wd11` -> an unrelated host), so nothing may gate on `{shard}.myworkdayjobs.com`. Only `cxs`
answers "does this tenant exist".
"""

from __future__ import annotations

import asyncio
import json
import re
import socket
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from typing import Any

from ..config import Criteria, load_seed_companies
from ..net import HttpClient
from .base import TenantCandidate
from .store import utcnow

SHARD_CANDIDATES: tuple[str, ...] = (
    "wd1", "wd2", "wd3", "wd4", "wd5", "wd6", "wd7", "wd8", "wd9", "wd10",
    "wd12", "wd103", "wd105", "wd108", "wd112", "wd115", "wd116", "wd117", "wd120",
    "wd501", "wd503",
)

# DNS label used to test whether a shard exists: it must never be a real tenant, and it only
# resolves where the shard's own wildcard record exists.
SHARD_PROBE_LABEL = "wd-shard-probe"

SITE_TEMPLATES: tuple[str, ...] = (
    "{Pascal}ExternalCareerSite",
    "{Pascal}Careers",
    "External",
    "Careers",
    "{Pascal}External",
    "{Pascal}Global",
    "{Pascal}",
    "{Pascal}ExternalSite",
    "ExternalSite",
    "Global",
    "{Pascal}_External",
    "{Pascal}_Careers",
    "ExternalCareers",
    "External_Careers",
    "{Pascal}CareerSite",
    "{Pascal}Jobs",
    "{Pascal}ExternalJobs",
    "{Pascal}CareersSite",
    "{Pascal}GlobalCareers",
)

ANTI_BOT_MARKERS = ("Just a moment", "cf-challenge", "Attention Required")

# --- site-slug discovery via /robots.txt (plan-workday.md 6.5, verified at G1) -------------
#
# Workday serves `/robots.txt` even when the bare host answers 406 with an empty body, and that
# file names the tenant's published site slugs *exactly* - no guessing:
#
#   Sitemap: https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/siteMap.xml
#   Allow: /NVIDIAExternalCareerSite/
#   Disallow: /2/                <- `citi` publishes ONLY Disallow rules (its slug is literally `2`)
#
# A tenant may publish several sites (nvidia also has a research board; citi an early-careers
# board), so candidates are verified through the oracle and then sized - the biggest wins.
ROBOTS_PATH = "/robots.txt"
MAX_ROBOTS_SITES = 12  # bound the probe fan-out for many-site tenants (equinix lists 17)

_SITEMAP_RE = re.compile(r"/([A-Za-z0-9][A-Za-z0-9_\-]*)/siteMap\.xml")
_RULE_RE = re.compile(r"^(?:Allow|Disallow):\s*/([A-Za-z0-9][A-Za-z0-9_\-]*)", re.MULTILINE)


def parse_robots_sites(body: str | None) -> list[str]:
    """Exact site slugs named by a Workday robots.txt, in file order and deduped.

    Sitemap lines come first: they are the canonical published sites. Rule lines follow and are
    deliberately unfiltered - the oracle verifies every candidate, so a path segment that is not
    a site (`refreshFacet`, `talentcommunity`) simply fails its probe and is dropped.
    """
    ordered: list[str] = []
    for pattern in (_SITEMAP_RE, _RULE_RE):
        for match in pattern.finditer(body or ""):
            slug = match.group(1)
            if slug not in ordered:
                ordered.append(slug)
    return ordered


def facet_size(harvested: dict[str, Any]) -> int:
    """Size a tenant board. `total` caps at 2000, so fall back to the summed facet counts."""
    reported = int(harvested.get("total_reported") or 0)
    summed = sum(int(v) for v in (harvested.get("sizes") or {}).values())
    return max(reported, summed)


@dataclass(frozen=True)
class ResolvedTenant:
    host: str
    tenant: str
    site: str
    shard: str
    status: str
    facet_counts: dict[str, Any]


@dataclass
class OracleStats:
    probes: int = 0
    cached: int = 0
    live: int = 0
    site_unknown: int = 0
    absent: int = 0
    unknown: int = 0
    robots_sites: int = 0
    robots_resolved: int = 0
    template_resolved: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "probes": self.probes, "cached": self.cached, "live": self.live,
            "site_unknown": self.site_unknown, "absent": self.absent, "unknown": self.unknown,
            "robots_sites": self.robots_sites, "robots_resolved": self.robots_resolved,
            "template_resolved": self.template_resolved,
        }


def classify_probe(status: int, body: str) -> tuple[str, str | None]:
    """Pure oracle classification. Returns (verdict, error_code)."""
    if 200 <= status < 300:
        return "live", None
    error_code = None
    try:
        error_code = (json.loads(body) or {}).get("errorCode")
    except Exception:
        error_code = None
    if status == 404:
        if error_code == "S21" or "Job_Posting_Site_ID" in body:
            return "site_unknown", error_code or "S21"
        return "unknown", error_code
    if status == 422:
        return "absent", error_code or "HTTP_422"
    if any(marker in body for marker in ANTI_BOT_MARKERS):
        return "unknown", "CHALLENGE"
    return "unknown", error_code


def pascal_cases(slug: str) -> list[str]:
    """Site slugs are tenant-authored, so casing is a guess: try both common forms."""
    cleaned = "".join(ch for ch in slug if ch.isalnum())
    return [cleaned.capitalize(), cleaned.upper()]


def site_candidates(slug: str) -> list[str]:
    out: list[str] = []
    for case in pascal_cases(slug):
        for template in SITE_TEMPLATES:
            candidate = template.replace("{Pascal}", case)
            if candidate not in out:
                out.append(candidate)
    return out


def host_for(tenant: str, shard: str) -> str:
    return f"{tenant}.{shard}.myworkdayjobs.com"


def _resolves(host: str) -> bool:
    try:
        socket.getaddrinfo(host, 443)
        return True
    except OSError:
        return False


async def shard_sweep(shards: Iterable[str] = SHARD_CANDIDATES) -> dict[str, bool]:
    """Which `wdN` shards exist. DNS only - no HTTP, no tenant knowledge.

    Probes `{SHARD_PROBE_LABEL}.{shard}.myworkdayjobs.com`: only a live shard's wildcard record
    answers for an arbitrary label. Never probe the bare `{shard}` apex - live shards `wd12` and
    `wd501` have no apex record, so an apex test silently drops more than half of the real shards.
    """
    results = await asyncio.gather(
        *(asyncio.to_thread(_resolves, f"{SHARD_PROBE_LABEL}.{s}.myworkdayjobs.com") for s in shards)
    )
    return dict(zip(shards, results, strict=True))



class SeedTenantSource:
    """TenantSource over config/seed_companies.yaml - unverified slug guesses for the oracle."""

    source = "seed"

    def __init__(self, shards: Iterable[str]) -> None:
        self._shards = list(shards)
        self._seeds = load_seed_companies()

    def yield_candidates(self, criteria: Criteria) -> AsyncIterator[TenantCandidate]:
        async def _gen() -> AsyncIterator[TenantCandidate]:
            for company in self._seeds.companies:
                for slug in company.slugs:
                    yield TenantCandidate(
                        host="", tenant=slug, site=None, source=self.source,
                        evidence={"company": company.name, "shards": self._shards},
                    )
        return _gen()


class WorkdayOracle:
    """Probes candidates, resolves site slugs, and persists tenants + a negative probe cache."""

    def __init__(
        self,
        client: HttpClient,
        conn: Any,
        *,
        shards: list[str],
        refresh: bool = False,
        concurrency: int = 6,
        max_site_probes: int = 24,
    ) -> None:
        self._client = client
        self._conn = conn
        self._shards = shards
        self._refresh = refresh
        self._sem = asyncio.Semaphore(concurrency)
        self._max_site_probes = max_site_probes
        self.stats = OracleStats()

    # --- probe cache -----------------------------------------------------
    def _cached(self, slug: str, shard: str, site: str) -> tuple[int, str | None] | None:
        if self._refresh:
            return None
        row = self._conn.execute(
            "SELECT http_status, error_code FROM tenant_probes WHERE slug=? AND shard=? AND site_guess=?",
            (slug, shard, site),
        ).fetchone()
        if not row:
            return None
        return int(row["http_status"]), row["error_code"]

    def _record(self, slug: str, shard: str, site: str, status: int, error_code: str | None) -> None:
        self._conn.execute(
            "INSERT INTO tenant_probes(slug, shard, site_guess, http_status, error_code, checked_at) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT(slug, shard, site_guess) DO UPDATE SET "
            "http_status=excluded.http_status, error_code=excluded.error_code, checked_at=excluded.checked_at",
            (slug, shard, site, status, error_code, utcnow()),
        )

    async def probe(self, slug: str, shard: str, site: str) -> tuple[int, str, str | None]:
        """One oracle probe. Cached; never raises; a network failure is `unknown`, not a verdict."""
        cached = self._cached(slug, shard, site)
        if cached is not None:
            self.stats.cached += 1
            status, error_code = cached
            if 200 <= status < 300:
                verdict = "live"
            elif error_code == "S21":
                verdict = "site_unknown"
            elif status == 422:
                verdict = "absent"
            else:
                verdict = "unknown"
            return status, verdict, error_code
        host = host_for(slug, shard)
        status, body = 0, ""
        async with self._sem:
            try:
                resp = await self._client.post_json(
                    f"https://{host}/wday/cxs/{slug}/{site}/jobs",
                    {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""},
                )
                status, body = resp.status, resp.text
            except Exception as exc:  # network/DNS failure -> unknown
                status, body = 0, str(exc)
        verdict, error_code = classify_probe(status, body)
        self.stats.probes += 1
        setattr(self.stats, verdict, getattr(self.stats, verdict) + 1)
        self._record(slug, shard, site, status, error_code)
        self._conn.commit()
        return status, verdict, error_code

    async def harvest_facets(self, slug: str, shard: str, site: str) -> dict[str, Any]:
        """One `limit:1` search -> the facet id map plus a size estimate.

        Sizing a tenant by summing facet counts, never by `total` (which caps at 2000).
        """
        resp = await self._client.post_json(
            f"https://{host_for(slug, shard)}/wday/cxs/{slug}/{site}/jobs",
            {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""},
        )
        if not resp.ok:
            return {}
        payload = resp.json()
        facets: dict[str, Any] = {}
        sizes: dict[str, int] = {}
        for facet in payload.get("facets") or []:
            param = facet.get("facetParameter") or facet.get("descriptor") or "unknown"
            values = [
                {"descriptor": v.get("descriptor"), "id": v.get("id"), "count": v.get("count")}
                for v in facet.get("values") or []
            ]
            facets[param] = values
            sizes[param] = sum(int(v.get("count") or 0) for v in values)
        return {"facets": facets, "sizes": sizes, "total_reported": payload.get("total")}

    async def robots_sites(self, slug: str, shard: str) -> list[str]:
        """Site slugs named by the tenant's `/robots.txt`; `[]` if the file is missing/blocked.

        Verified at G1 (plan-workday.md 6.5): the bare host 406s, but `/robots.txt` still answers
        200 and names the *exact* published site slugs, which is what unlocks the tenants the
        generated templates cannot guess (`NVIDIAExternalCareerSite`, `CorporateCareers`, `2`).
        """
        try:
            async with self._sem:
                resp = await self._client.get(f"https://{host_for(slug, shard)}{ROBOTS_PATH}")
        except Exception:
            return []
        if not resp.ok:
            return []
        return parse_robots_sites(resp.text)

    async def _best_of(self, slug: str, shard: str, candidates: Iterable[str]) -> tuple[str, dict[str, Any]] | None:
        """Verify each candidate through the oracle; return the largest board (multi-site tenants)."""
        live: list[str] = []
        for site in candidates:
            if site in live:
                continue
            _, verdict, _ = await self.probe(slug, shard, site)
            if verdict == "live":
                live.append(site)
        if not live:
            return None
        sized: list[tuple[str, dict[str, Any]]] = []
        for site in live:
            sized.append((site, await self.harvest_facets(slug, shard, site)))
        return max(sized, key=lambda item: facet_size(item[1]))

    async def _resolve_site(self, slug: str, shard: str) -> tuple[str, dict[str, Any]] | None:
        """Phase 2: find a working site slug (plan-workday.md 6.5 order).

        `robots.txt` first - exact, and a tenant may publish several boards, so the candidates are
        verified and then sized. Generated templates are the fallback and the first hit wins, which
        keeps the common path to one or two requests.
        """
        robots = await self.robots_sites(slug, shard)
        if robots:
            self.stats.robots_sites += len(robots)
            best = await self._best_of(slug, shard, robots[:MAX_ROBOTS_SITES])
            if best is not None:
                self.stats.robots_resolved += 1
                return best
        for site in site_candidates(slug)[: self._max_site_probes]:
            _, verdict, _ = await self.probe(slug, shard, site)
            if verdict == "live":
                self.stats.template_resolved += 1
                return site, await self.harvest_facets(slug, shard, site)
        return None

    def persist_tenant(
        self, slug: str, shard: str, site: str | None, status: str, facet_counts: dict[str, Any] | None = None
    ) -> None:
        self._conn.execute(
            "INSERT INTO tenants(host, tenant, site, wd_shard, locale, status, source, verified_at, facet_counts) "
            "VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(host, tenant) DO UPDATE SET site=excluded.site, wd_shard=excluded.wd_shard, "
            "status=excluded.status, verified_at=excluded.verified_at, facet_counts=excluded.facet_counts",
            (
                host_for(slug, shard), slug, site, shard, "en-US", status, "seed",
                utcnow(), json.dumps(facet_counts or {}),
            ),
        )
        self._conn.commit()

    async def resolve(self, candidate: TenantCandidate) -> ResolvedTenant | None:
        """Phase 1: find the shard (S21 confirms the tenant). Phase 2: resolve the site slug."""
        slug = candidate.tenant
        shards = candidate.evidence.get("shards") or self._shards
        for shard in shards:
            _, verdict, _ = await self.probe(slug, shard, "External")
            if verdict == "absent":
                continue
            if verdict == "unknown":
                continue
            if verdict == "site_unknown":
                resolved = await self._resolve_site(slug, shard)
                if resolved is None:
                    self.persist_tenant(slug, shard, None, "site_unknown")
                    return None
                site, harvested = resolved
                self.persist_tenant(slug, shard, site, "live", harvested)
                return ResolvedTenant(
                    host=host_for(slug, shard), tenant=slug, site=site, shard=shard,
                    status="live", facet_counts=harvested,
                )
            harvested = await self.harvest_facets(slug, shard, "External")
            self.persist_tenant(slug, shard, "External", "live", harvested)
            return ResolvedTenant(
                host=host_for(slug, shard), tenant=slug, site="External", shard=shard,
                status="live", facet_counts=harvested,
            )
        return None

    async def run(self, candidates: Iterable[TenantCandidate], *, concurrency: int = 6) -> dict[str, Any]:
        """Resolve every candidate. Bounded by the same semaphore that gates HTTP."""
        pending: list[TenantCandidate] = list(candidates)
        resolved: list[ResolvedTenant] = []
        gate = asyncio.Semaphore(concurrency)

        async def _one(candidate: TenantCandidate) -> ResolvedTenant | None:
            async with gate:
                try:
                    return await self.resolve(candidate)
                except Exception:
                    self.stats.unknown += 1
                    return None

        for result in await asyncio.gather(*(_one(c) for c in pending)):
            if result is not None:
                resolved.append(result)
        return {"candidates": len(pending), "tenants_live": len(resolved), **self.stats.as_dict()}
