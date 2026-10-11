"""Posting persistence: idempotent upsert, freshness bookkeeping, dedupe.

Idempotency is `(source, source_id)` - the `UNIQUE(source, source_id)` constraint plus
`plan-general.md` section 4 ("incremental via last_seen watermark"). Dedupe across sources is a
G2 concern (`match/`); this module only guarantees one row per source item.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone

from ..models import Posting

_COLS = (
    "source, source_id, company_id, title, location, remote, posted_at, jd_text, jd_hash, "
    "apply_url, channel_hint, contact_emails, visa_signals, first_seen, last_seen, active, "
    "external_path, req_id, can_apply"
)

_UPSERT = f"""
INSERT INTO postings ({_COLS})
VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?)
ON CONFLICT(source, source_id) DO UPDATE SET
  title = excluded.title,
  location = excluded.location,
  remote = excluded.remote,
  posted_at = excluded.posted_at,
  jd_text = excluded.jd_text,
  jd_hash = excluded.jd_hash,
  apply_url = excluded.apply_url,
  channel_hint = excluded.channel_hint,
  contact_emails = excluded.contact_emails,
  visa_signals = excluded.visa_signals,
  last_seen = excluded.last_seen,
  active = 1,
  external_path = excluded.external_path,
  req_id = excluded.req_id,
  can_apply = excluded.can_apply
"""


def utcnow() -> str:
    """ISO-8601 UTC, second precision - the timestamp format used across the schema."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def jd_hash(text: str | None) -> str | None:
    if not text:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def ensure_company(
    conn: sqlite3.Connection,
    name: str,
    *,
    domain: str | None = None,
    ats_family: str | None = None,
    tenant: str | None = None,
    site: str | None = None,
) -> int:
    row = conn.execute("SELECT id FROM companies WHERE name = ?", (name,)).fetchone()
    if row:
        return int(row["id"])
    conn.execute(
        "INSERT INTO companies(name, domain, ats_family, tenant, site) VALUES (?,?,?,?,?) "
        "ON CONFLICT(name) DO NOTHING",
        (name, domain, ats_family, tenant, site),
    )
    return int(conn.execute("SELECT id FROM companies WHERE name = ?", (name,)).fetchone()["id"])


def _exists(conn: sqlite3.Connection, source: str, source_id: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM postings WHERE source = ? AND source_id = ?", (source, source_id)
    ).fetchone() is not None


def upsert_postings(conn: sqlite3.Connection, postings: Iterable[Posting]) -> dict[str, int]:
    """Insert-or-refresh postings. Returns {'inserted': n, 'updated': n}."""
    seen_at = utcnow()
    inserted = updated = 0
    for p in postings:
        company_id = p.company_id or ensure_company(conn, p.company)
        is_new = not _exists(conn, p.source, p.source_id)
        conn.execute(
            _UPSERT,
            (
                p.source, p.source_id, company_id, p.title, p.location,
                None if p.remote is None else int(p.remote), p.posted_at, p.jd_text,
                p.jd_hash or jd_hash(p.jd_text), p.apply_url, p.channel_hint,
                json.dumps(p.contact_emails), json.dumps(p.visa_signals), seen_at, seen_at,
                p.external_path, p.req_id, None if p.can_apply is None else int(p.can_apply),
            ),
        )
        if is_new:
            inserted += 1
        else:
            updated += 1
    return {"inserted": inserted, "updated": updated}


def mark_inactive(conn: sqlite3.Connection, source: str, seen_ids: Sequence[str] | set[str]) -> int:
    """Deactivate postings of a source that were absent from the latest full poll."""
    seen = list(seen_ids)
    if not seen:
        return 0
    placeholders = ",".join("?" * len(seen))
    cur = conn.execute(
        f"UPDATE postings SET active = 0, last_seen = ? WHERE source = ? AND active = 1 "
        f"AND source_id NOT IN ({placeholders})",
        (utcnow(), source, *seen),
    )
    return int(cur.rowcount or 0)


def touch_source(conn: sqlite3.Connection, family: str, when: str | None = None) -> None:
    when = when or utcnow()
    cur = conn.execute("UPDATE sources SET last_polled = ? WHERE family = ?", (when, family))
    if not cur.rowcount:
        conn.execute(
            "INSERT INTO sources(family, config, last_polled) VALUES (?, '{}', ?)", (family, when)
        )
