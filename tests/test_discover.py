"""Discovery layer tests (G1).

Conventions per AGENTS.md section 7: pytest-style, real loaded config, one module per concern.
Network is never touched here - sources take an injectable client.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from jobagent import db
from jobagent.config import load_criteria, load_seed_companies, load_sources
from jobagent.discover.ats import GreenhouseSource, emails_in, strip_html
from jobagent.discover.store import jd_hash, mark_inactive, upsert_postings, utcnow
from jobagent.discover.workday_cxs import TenantRef, parse_job_posting, parse_posted_on
from jobagent.discover.workday_tenants import classify_probe, pascal_cases, site_candidates
from jobagent.models import Posting
from jobagent.net import Resp


class FakeClient:
    """Canned HTTP responses, keyed by URL substring."""

    def __init__(self, responses: dict[str, tuple[int, object]]) -> None:
        self._responses = responses
        self.calls: list[str] = []

    def _respond(self, url: str) -> Resp:
        self.calls.append(url)
        for needle, (status, payload) in self._responses.items():
            if needle in url:
                text = payload if isinstance(payload, str) else json.dumps(payload)
                return Resp(status=status, url=url, text=text)
        return Resp(status=404, url=url, text="{}")

    async def get_json(self, url: str, **kw: object) -> Resp:
        return self._respond(url)

    async def post_json(self, url: str, payload: dict, **kw: object) -> Resp:
        return self._respond(url)


@pytest.fixture()
def conn(tmp_path):
    connection = db.connect(tmp_path / "test.db")
    db.migrate(connection)
    yield connection
    connection.close()


# --- oracle -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (200, "{}", ("live", None)),
        (404, '{"errorCode":"S21","message":"not found: Job_Posting_Site_ID=External"}', ("site_unknown", "S21")),
        (422, '{"errorCode":"HTTP_422","message":""}', ("absent", "HTTP_422")),
        (404, '{"error":"nope"}', ("unknown", None)),
        (503, "Just a moment...", ("unknown", "CHALLENGE")),
        (0, "dns failure", ("unknown", None)),
    ],
)
def test_classify_probe(status: int, body: str, expected: tuple[str, str | None]) -> None:
    assert classify_probe(status, body) == expected


def test_probe_never_maps_failure_to_a_verdict() -> None:
    """A network failure must never look like "this tenant does not exist"."""
    verdict, _ = classify_probe(0, "connection reset")
    assert verdict == "unknown"
    assert verdict not in ("live", "absent", "site_unknown")


# --- site slug candidates ---------------------------------------------------


def test_site_candidates_cover_casing_and_separators() -> None:
    candidates = site_candidates("cisco")
    assert "CiscoExternalCareerSite" in candidates
    assert "CISCOExternalCareerSite" in candidates
    assert "External" in candidates
    assert "Cisco_Careers" in candidates      # the form that resolved cisco live
    assert len(candidates) == len(set(candidates))


def test_pascal_cases() -> None:
    assert pascal_cases("citi") == ["Citi", "CITI"]
    assert pascal_cases("a-b") == ["Ab", "AB"]


# --- workday search mapping -------------------------------------------------


def test_parse_posted_on() -> None:
    today = date(2026, 10, 11)
    assert parse_posted_on("Posted Today", today=today) == "2026-10-11"
    assert parse_posted_on("Posted Yesterday", today=today) == "2026-10-10"
    assert parse_posted_on("Posted 3 Days Ago", today=today) == "2026-10-08"
    assert parse_posted_on("Posted 30+ Days Ago", today=today) == "2026-09-11"
    assert parse_posted_on(None, today=today) is None


def test_parse_job_posting_maps_channel_fields() -> None:
    tenant = TenantRef(host="citi.wd5.myworkdayjobs.com", tenant="citi", site="Citi_Careers")
    posting = parse_job_posting(
        {
            "title": "Senior Software Engineer",
            "externalPath": "/job/Dublin/SWE_JR1",
            "locationsText": "Dublin, Ireland",
            "postedOn": "Posted Today",
            "bulletFields": ["JR1"],
        },
        tenant,
    )
    assert posting.source == "workday"
    assert posting.req_id == "JR1"
    assert posting.external_path == "/job/Dublin/SWE_JR1"
    assert posting.apply_url == "https://citi.wd5.myworkdayjobs.com/en-US/Citi_Careers/job/Dublin/SWE_JR1"
    assert posting.channel_hint == "web_form"
    assert posting.remote is None
    assert posting.extra["posted_on_raw"] == "Posted Today"


def test_parse_job_posting_flags_remote() -> None:
    tenant = TenantRef(host="h", tenant="t", site="s")
    posting = parse_job_posting({"title": "x", "locationsText": "Remote - EMEA"}, tenant)
    assert posting.remote is True


# --- family mapping (offline) ------------------------------------------------


@pytest.mark.asyncio
async def test_greenhouse_source_maps_payload(conn) -> None:
    payload = {
        "jobs": [
            {
                "id": 42,
                "title": "Backend Engineer",
                "updated_at": "2026-10-01T10:00:00-04:00",
                "absolute_url": "https://boards.greenhouse.io/acme/jobs/42",
                "location": {"name": "Dublin, Ireland"},
                "content": "<p>Java &amp; Spring</p><p>Contact jobs@acme.com</p>",
                "company_name": "Acme",
            }
        ]
    }
    client = FakeClient({"boards-api.greenhouse.io": (200, payload)})
    source = GreenhouseSource(client, ["acme"])
    postings = [p async for p in source.discover(None, load_criteria())]  # type: ignore[arg-type]
    assert len(postings) == 1
    posting = postings[0]
    assert (posting.source, posting.source_id) == ("greenhouse", "acme:42")
    assert posting.company == "Acme"
    assert posting.location == "Dublin, Ireland"
    assert posting.contact_emails == ["jobs@acme.com"]
    assert "Java & Spring" in (posting.jd_text or "")
    assert "<p>" not in (posting.jd_text or "")


@pytest.mark.asyncio
async def test_greenhouse_source_skips_bad_board(conn) -> None:
    client = FakeClient({})  # everything 404s
    source = GreenhouseSource(client, ["nope"])
    assert [p async for p in source.discover(None, load_criteria())] == []  # type: ignore[arg-type]


# --- helpers -----------------------------------------------------------------


def test_strip_html_and_emails() -> None:
    assert strip_html("<p>Hi &amp; there</p>") == "Hi & there"
    assert strip_html(None) is None
    assert emails_in("mail JOBS@acme.com or jobs@acme.com") == ["jobs@acme.com"]
    assert emails_in("no address here") == []


def test_jd_hash_stable() -> None:
    assert jd_hash("abc") == jd_hash("abc")
    assert jd_hash(None) is None
    assert jd_hash("abc") != jd_hash("abd")


# --- persistence -------------------------------------------------------------


def _posting(source_id: str = "1", **kw: object) -> Posting:
    base: dict[str, object] = {
        "source": "greenhouse", "source_id": source_id, "company": "Acme",
        "title": "Backend Engineer", "location": "Dublin", "jd_text": "java spring",
    }
    base.update(kw)
    return Posting(**base)  # type: ignore[arg-type]


def test_upsert_is_idempotent_and_preserves_first_seen(conn) -> None:
    first = upsert_postings(conn, [_posting("1"), _posting("2")])
    assert first == {"inserted": 2, "updated": 0}
    conn.commit()
    first_seen = conn.execute("SELECT first_seen FROM postings WHERE source_id='1'").fetchone()[0]

    second = upsert_postings(conn, [_posting("1")])
    assert second == {"inserted": 0, "updated": 1}
    conn.commit()

    rows = conn.execute("SELECT COUNT(*) FROM postings").fetchone()[0]
    assert rows == 2, "re-polling must not duplicate rows"
    assert conn.execute("SELECT first_seen FROM postings WHERE source_id='1'").fetchone()[0] == first_seen


def test_upsert_creates_one_company_per_name(conn) -> None:
    upsert_postings(conn, [_posting("1"), _posting("2")])
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0] == 1


def test_mark_inactive_deactivates_unseen(conn) -> None:
    upsert_postings(conn, [_posting("1"), _posting("2")])
    conn.commit()
    assert mark_inactive(conn, "greenhouse", {"1"}) == 1
    conn.commit()
    rows = dict(conn.execute("SELECT source_id, active FROM postings").fetchall())
    assert rows == {"1": 1, "2": 0}
    assert utcnow()  # timestamp helper returns something usable


# --- config integrity --------------------------------------------------------


def test_sources_config_loads_and_is_data_not_criteria() -> None:
    families = [entry.family for entry in load_sources().sources]
    assert {"greenhouse", "lever", "ashby", "remotive"} <= set(families)


def test_seed_companies_are_slug_candidates() -> None:
    seeds = load_seed_companies()
    assert len(seeds.companies) >= 20
    slugs = [s for company in seeds.companies for s in company.slugs]
    assert all(s == s.lower() for s in slugs), "slugs are lowercased guesses"
