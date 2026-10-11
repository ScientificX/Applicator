"""SQLite schema and migrations - the canonical model.

Source of truth: plan-general.md section 10 (canonical) plus the channel extensions declared
in plan-workday.md section 14 (tenants, tenant_probes, shards) and the Workday account
identities table. Naming canon: postings (never jobs), events (never funnel).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1

DDL = """
CREATE TABLE IF NOT EXISTS schema_meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sources (
  id          INTEGER PRIMARY KEY,
  family      TEXT NOT NULL,
  config      TEXT NOT NULL DEFAULT '{}',
  last_polled TEXT
);

CREATE TABLE IF NOT EXISTS companies (
  id               INTEGER PRIMARY KEY,
  name             TEXT NOT NULL UNIQUE,
  domain           TEXT,
  ats_family       TEXT,
  tenant           TEXT,
  site             TEXT,
  allow_email_tier INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS postings (
  id             INTEGER PRIMARY KEY,
  source         TEXT NOT NULL,
  source_id      TEXT NOT NULL,
  company_id     INTEGER REFERENCES companies(id),
  title          TEXT NOT NULL,
  location       TEXT,
  remote         INTEGER,
  posted_at      TEXT,
  jd_text        TEXT,
  jd_hash        TEXT,
  apply_url      TEXT,
  channel_hint   TEXT,
  contact_emails TEXT NOT NULL DEFAULT '[]',
  visa_signals   TEXT NOT NULL DEFAULT '{}',
  first_seen     TEXT NOT NULL,
  last_seen      TEXT,
  active         INTEGER NOT NULL DEFAULT 1,
  external_path  TEXT,
  req_id         TEXT,
  can_apply      INTEGER,
  UNIQUE(source, source_id)
);
CREATE INDEX IF NOT EXISTS idx_postings_company ON postings(company_id);
CREATE INDEX IF NOT EXISTS idx_postings_hash    ON postings(jd_hash);
CREATE INDEX IF NOT EXISTS idx_postings_seen    ON postings(first_seen);

CREATE TABLE IF NOT EXISTS matches (
  id            INTEGER PRIMARY KEY,
  posting_id    INTEGER NOT NULL REFERENCES postings(id),
  score_match   REAL,
  score_fresh   REAL,
  score_prior   REAL,
  decision      TEXT,
  visa_decision TEXT,
  reason        TEXT,
  created_at    TEXT NOT NULL,
  UNIQUE(posting_id)
);

CREATE TABLE IF NOT EXISTS resumes (
  id              INTEGER PRIMARY KEY,
  posting_id      INTEGER NOT NULL REFERENCES postings(id),
  profile_version TEXT NOT NULL,
  path            TEXT NOT NULL,
  tailor_report   TEXT NOT NULL DEFAULT '{}',
  created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS aliases (
  id         INTEGER PRIMARY KEY,
  address    TEXT NOT NULL UNIQUE,
  role       TEXT NOT NULL,
  ats_family TEXT,
  status     TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL,
  burned_at  TEXT
);

CREATE TABLE IF NOT EXISTS web_identities (
  id          INTEGER PRIMARY KEY,
  ats_family  TEXT NOT NULL,
  label       TEXT NOT NULL,
  proxy_ref   TEXT,
  status      TEXT NOT NULL DEFAULT 'ready',
  daily_cap   INTEGER NOT NULL DEFAULT 25,
  fail_streak INTEGER NOT NULL DEFAULT 0,
  profile_dir TEXT,
  UNIQUE(ats_family, label)
);

CREATE TABLE IF NOT EXISTS identities (
  id                INTEGER PRIMARY KEY,
  tenant_id         INTEGER REFERENCES tenants(id),
  alias             TEXT NOT NULL UNIQUE,
  alias_version     INTEGER NOT NULL DEFAULT 1,
  password_ref      TEXT,
  status            TEXT NOT NULL DEFAULT 'provisioning',
  profile_dir       TEXT,
  proxy_ref         TEXT,
  next_available_at TEXT,
  apps_today        INTEGER NOT NULL DEFAULT 0,
  fail_streak       INTEGER NOT NULL DEFAULT 0,
  created_at        TEXT,
  verified_at       TEXT
);

CREATE TABLE IF NOT EXISTS applications (
  id              INTEGER PRIMARY KEY,
  posting_id      INTEGER NOT NULL REFERENCES postings(id),
  channel         TEXT NOT NULL,
  address_id      INTEGER REFERENCES aliases(id),
  web_identity_id INTEGER REFERENCES web_identities(id),
  status          TEXT NOT NULL DEFAULT 'queued',
  attempt         INTEGER NOT NULL DEFAULT 0,
  external_id     TEXT,
  submitted_at    TEXT,
  artifact_dir    TEXT,
  error           TEXT,
  UNIQUE(posting_id)
);
CREATE INDEX IF NOT EXISTS idx_applications_status ON applications(status);

CREATE TABLE IF NOT EXISTS messages (
  id               INTEGER PRIMARY KEY,
  application_id   INTEGER REFERENCES applications(id),
  match_confidence TEXT,
  direction        TEXT NOT NULL DEFAULT 'inbound',
  recipient        TEXT,
  from_addr        TEXT,
  from_domain      TEXT,
  subject          TEXT,
  subject_slug     TEXT,
  rfc_message_id   TEXT,
  kind             TEXT,
  kind_confidence  REAL,
  received_at      TEXT,
  raw_key          TEXT,
  UNIQUE(raw_key)
);
CREATE INDEX IF NOT EXISTS idx_messages_received ON messages(received_at);
CREATE INDEX IF NOT EXISTS idx_messages_app      ON messages(application_id);

CREATE TABLE IF NOT EXISTS events (
  id             INTEGER PRIMARY KEY,
  application_id INTEGER REFERENCES applications(id),
  kind           TEXT NOT NULL,
  payload        TEXT NOT NULL DEFAULT '{}',
  ts             TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
  id          INTEGER PRIMARY KEY,
  stage       TEXT NOT NULL,
  started_at  TEXT NOT NULL,
  ended_at    TEXT,
  counts_json TEXT NOT NULL DEFAULT '{}',
  cost_usd    REAL NOT NULL DEFAULT 0,
  notes       TEXT
);

CREATE TABLE IF NOT EXISTS suppressions (
  address  TEXT PRIMARY KEY,
  reason   TEXT,
  added_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tenants (
  id                INTEGER PRIMARY KEY,
  host              TEXT NOT NULL,
  tenant            TEXT NOT NULL,
  site              TEXT,
  wd_shard          TEXT,
  locale            TEXT NOT NULL DEFAULT 'en-US',
  status            TEXT NOT NULL DEFAULT 'unverified',
  source            TEXT,
  verified_at       TEXT,
  facet_counts      TEXT NOT NULL DEFAULT '{}',
  matched_req_count INTEGER NOT NULL DEFAULT 0,
  UNIQUE(host, tenant)
);

CREATE TABLE IF NOT EXISTS tenant_probes (
  id          INTEGER PRIMARY KEY,
  slug        TEXT NOT NULL,
  shard       TEXT NOT NULL,
  site_guess  TEXT NOT NULL,
  http_status INTEGER,
  error_code  TEXT,
  checked_at  TEXT NOT NULL,
  UNIQUE(slug, shard, site_guess)
);

CREATE TABLE IF NOT EXISTS shards (
  host_suffix  TEXT PRIMARY KEY,
  resolves     INTEGER NOT NULL DEFAULT 0,
  last_checked TEXT
);

CREATE TABLE IF NOT EXISTS answers (
  id            INTEGER PRIMARY KEY,
  tenant_id     INTEGER REFERENCES tenants(id),
  question_hash TEXT NOT NULL,
  question_text TEXT NOT NULL,
  answer        TEXT NOT NULL,
  source        TEXT NOT NULL,
  uses          INTEGER NOT NULL DEFAULT 0,
  updated_at    TEXT NOT NULL,
  UNIQUE(tenant_id, question_hash)
);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    """Open (creating parent dirs) with sane pragmas and row access by name."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def migrate(conn: sqlite3.Connection) -> int:
    """Create/upgrade the schema. Returns the resulting schema version."""
    conn.executescript(DDL)
    row = conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
    current = int(row["value"]) if row else 0
    if current < SCHEMA_VERSION:
        conn.execute(
            "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(SCHEMA_VERSION),),
        )
        conn.commit()
    return SCHEMA_VERSION


def schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
    return int(row["value"]) if row else 0


def table_names(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name"
    ).fetchall()
    return [r["name"] for r in rows]


def row_counts(conn: sqlite3.Connection) -> dict[str, int]:
    out: dict[str, int] = {}
    for name in table_names(conn):
        if name == "schema_meta":
            continue
        out[name] = conn.execute(f"SELECT COUNT(*) AS n FROM {name}").fetchone()["n"]
    return out
