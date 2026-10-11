#!/usr/bin/env python3
"""Live, read-only endpoint probe.

Validates the curl_cffi HTTP layer and resolves open [?] items:
  * Workday landing page under Chrome impersonation (was 406/~0 bytes to plain HTTP)
  * the shape/availability of each planned source family

Run: uv run python scripts/probe_endpoints.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jobagent.net import HttpClient  # noqa: E402

CXS = "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite/jobs"
LANDING = "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite"
GH = "https://boards-api.greenhouse.io/v1/boards/{}/jobs?content=true"
LEVER = "https://api.lever.co/v0/postings/{}?mode=json"
ASHBY = "https://api.ashbyhq.com/posting-api/job-board/{}"
REMOTIVE = "https://remotive.com/api/remote-jobs?limit=3"

GH_TOKENS = ["anthropic", "stripe", "figma", "datadog"]
LEVER_COS = ["netflix", "palantir", "plaid"]
ASHBY_ORGS = ["ramp", "linear", "openai"]


def line(label: str, status: int, note: str) -> None:
    flag = "OK " if 200 <= status < 300 else "-- "
    print(f"{flag}{label:<46} {status:>3}  {note}")


async def main() -> int:
    async with HttpClient() as c:
        print("--- workday cxs (POST search) ---")
        r = await c.post_json(CXS, {"appliedFacets": {}, "limit": 2, "offset": 0, "searchText": ""})
        note = ""
        if r.ok:
            d = r.json()
            jps = d.get("jobPostings", [])
            note = f"total={d.get('total')} facets={len(d.get('facets', []))} first={jps[0]['title'][:34] if jps else '-'}"
        line("cxs search nvidia/NVIDIAExternalCareerSite", r.status, note or r.snippet(80))

        print("--- workday landing page (the [#?] 406 question) ---")
        r = await c.request("GET", LANDING, headers={"Accept": "text/html,application/xhtml+xml"})
        note = f"{len(r.text)} bytes"
        if r.ok:
            has_site = "NVIDIAExternalCareerSite" in r.text
            note += f", site slug present={has_site}"
        line("GET landing page (chrome impersonation)", r.status, note)

        print("--- non-workday ATS families ---")
        for t in GH_TOKENS:
            r = await c.get_json(GH.format(t))
            n = len(r.json().get("jobs", [])) if r.ok else 0
            line(f"greenhouse:{t}", r.status, f"{n} postings" if r.ok else r.snippet(70))
        for co in LEVER_COS:
            r = await c.get_json(LEVER.format(co))
            n = len(r.json()) if r.ok and isinstance(r.json(), list) else 0
            line(f"lever:{co}", r.status, f"{n} postings" if r.ok else r.snippet(70))
        for org in ASHBY_ORGS:
            r = await c.get_json(ASHBY.format(org))
            n = len(r.json().get("jobs", [])) if r.ok else 0
            line(f"ashby:{org}", r.status, f"{n} postings" if r.ok else r.snippet(70))

        print("--- aggregator ---")
        r = await c.get_json(REMOTIVE)
        if r.ok:
            jobs = r.json().get("jobs", [])
            urls = [j.get("url", "") for j in jobs]
            any_wd = any("myworkdayjobs" in u for u in urls)
            line("remotive (remote-jobs API)", r.status, f"{len(jobs)} postings, workday urls={any_wd}")
        else:
            line("remotive (remote-jobs API)", r.status, r.snippet(80))

        print(f"\nhttp calls made: {c.calls}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
