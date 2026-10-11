# Design — job-agent (low-level design, class & API contracts)

| | |
|---|---|
| **Doc set** | `PS-5.4` |
| **Status** | Contracts marked **implemented** exist in `src/jobagent/` today (G0). Contracts marked *(planned)* are the agreed shape for a future stage — signatures are proposals and may be refined when the stage lands, but the **names and protocols are pinned by the plan docs**. |
| **Authoritative sources** | [`../plan-general.md`](../plan-general.md), [`../plan-workday.md`](../plan-workday.md) |
| **Companion docs** | [`architecture.md`](architecture.md) (high-level design), [`../AGENTS.md`](../AGENTS.md) |
| **Maintenance** | Any change to a public class, function, field, CLI command, DB column, config key or HTTP call -> update this file. See `AGENTS.md` §8. |

This is the **low-level design (LLD)**: the concrete contracts a developer or agent codes
against. Where a signature is not yet pinned by a plan document it is marked `(proposed)` —
treat it as a proposal to be confirmed by the stage that implements it.

Conventions used below:

- `Status`: **done** = implemented at G0; **planned** = to be built at the named stage.
- Signatures use Python 3.13 typing. `AsyncIterator` implies an `async def` implementation.
- "Owner" names the plan section that is authoritative for the behaviour.

---

## 1. Package layout

```text
src/jobagent/
  __init__.py     __version__ = "0.0.0"                       done
  config.py       Settings + YAML models + loaders            done
  db.py           DDL, migrations, connection helpers         done
  filters.py      title_gate (include/veto)                   done
  checks.py       doctor checks                               done
  cli.py          Typer app                                   done
  discover/       DiscoverySource, TenantSource impls         planned (G1)
  match/          dedupe, visa.py, scoring                    planned (G2)
  tailor/         resume build + gates                        planned (G2)
  apply/          workday.py, onboard.py, questions.py        planned (G3/G5)
  mail/           worker client, attributer, classifier       planned (G3/G4)
  llm/            client.py, tiers, cost.py                   planned (G1+)
  channels.py     channel-selection policy                    planned (G3)
```

Tests mirror modules (`tests/test_<module>.py`); `scripts/lint_criteria.py` is a
non-shipping gate that runs the same title semantics as the product.

## 2. `config.py` — typed configuration (implemented)

Owner: `plan-general.md` §2.1, §2.2, §1.5. Module constants:

| Name | Value | Notes |
|---|---|---|
| `ROOT` | `Path(__file__).resolve().parents[2]` | repo root; resolves `.env` and repo-relative paths |
| `CONFIG_DIR` | `ROOT / "config"` | where the YAML contract files live |

### 2.1 `Settings` (env-backed, `pydantic_settings.BaseSettings`)

Loaded from `ROOT/.env` (case-insensitive, extra keys ignored). Every field has a default so
`doctor` runs with a blank `.env`.

| Field | Type | Default | Env var | Purpose |
|---|---|---|---|---|
| `deepseek_api_key` | `str` | `""` | `DEEPSEEK_API_KEY` | cloud LLM auth |
| `deepseek_base_url` | `str` | `https://api.deepseek.com` | `DEEPSEEK_BASE_URL` | OpenAI-compatible base URL |
| `deepseek_model` | `str` | `deepseek-flash` | `DEEPSEEK_MODEL` | primary model id (pin the literal; never the `deepseek-chat` alias) |
| `deepseek_model_escalate` | `str` | `deepseek-v4-pro` | `DEEPSEEK_MODEL_ESCALATE` | escalation model |
| `agent_token` | `str` | `""` | `AGENT_TOKEN` | bearer token for the mail worker |
| `mail_worker_base` | `str` | `https://my-email-worker.flso.workers.dev` | `MAIL_WORKER_BASE` | mail worker base URL |
| `jobagent_contact_address` | `str` | `""` | `JOBAGENT_CONTACT_ADDRESS` | the one human-facing address on every application/resume |
| `ollama_base` | `str` | `http://localhost:11434` | `OLLAMA_BASE` | local model host |
| `ollama_classify_model` | `str` | `qwen3:8b` | `OLLAMA_CLASSIFY_MODEL` | classification |
| `ollama_failover_model` | `str` | `qwen3:14b` | `OLLAMA_FAILOVER_MODEL` | escalate/failover |
| `ollama_embed_model` | `str` | `bge-m3` | `OLLAMA_EMBED_MODEL` | embeddings |
| `jobagent_max_cost_per_run_usd` | `float` | `1.00` | `JOBAGENT_MAX_COST_PER_RUN_USD` | hard cap/run |
| `jobagent_max_cost_per_day_usd` | `float` | `3.00` | `JOBAGENT_MAX_COST_PER_DAY_USD` | hard cap/day |
| `jobagent_state_dir` | `Path` | `state` | `JOBAGENT_STATE_DIR` | repo-relative unless absolute |
| `jobagent_artifacts_dir` | `Path` | `artifacts` | `JOBAGENT_ARTIFACTS_DIR` | repo-relative unless absolute |

Derived members:

```python
Settings._resolve(p: Path) -> Path          # absolute if already, else ROOT / p
Settings.state_dir        -> Path           # @property
Settings.artifacts_dir    -> Path           # @property
Settings.db_path          -> Path           # state_dir / "jobagent.db"
Settings.ensure_dirs()    -> None           # mkdir state_dir, artifacts_dir, artifacts_dir/"har"
```

### 2.2 YAML models

```python
class Locations(BaseModel):   preferred: list[str] = []; next: list[str] = []; also: list[str] = []
class Comp(BaseModel):        IE_min_eur: int | None; UK_min_gbp: int | None; remote_min_eur: int | None
class Target(BaseModel):      seniority: list[str]; title_include: list[str]; title_exclude: list[str]
                              stack_must: list[str] = []; stack_nice: list[str] = []
                              domain_nice: list[str] = []
                              locations: Locations = Locations()
                              comp: Comp = Comp()
                              top_k_per_run: int = 25
                              fresh_window_minutes: int = 60
class Criteria(BaseModel):    target: Target
class VisaRules(BaseModel):   right_to_work: dict[str, str]
                              jd_filter: dict[str, list[str]] = {}
                              answers: dict[str, dict[str, str]] = {}
class Visa(BaseModel):        visa: VisaRules
class Tenants(BaseModel):     tenants: list[dict[str, Any]] = []; defaults: dict[str, Any] = {}
class Answers(BaseModel):     answers: dict[str, str] = {}; disclosures: dict[str, str] = {}
class Proxies(BaseModel):     pools: dict[str, list[str]] = {}
```

Loader functions (each validates and raises on a malformed file):

```python
read_yaml(name: str) -> dict[str, Any]                 # FileNotFoundError if missing
load_criteria()  -> Criteria
load_visa()      -> Visa
load_tenants()   -> Tenants
load_answers()   -> Answers
load_proxies()   -> Proxies
@lru_cache(maxsize=1)
settings()       -> Settings                           # process-wide singleton
```

> Contract notes: `Visa` wraps a top-level `visa:` key, matching the other YAML files. Loader
> functions are the only supported way to read `config/` — do not open the files directly.

## 3. `filters.py` — title gate (implemented)

Owner: `plan-general.md` §2.1. This module pins the matching semantics so nothing drifts.

```python
@dataclass(frozen=True)
class TitleVerdict:
    passed: bool
    include_hit: str | None      # the title_include pattern that matched, or None
    veto_hit: str | None         # the title_exclude pattern that matched, or None

    @property
    def reason(self) -> str: ...  # human-readable explanation

@lru_cache(maxsize=8)
def _compile(patterns: tuple[str, ...]) -> tuple[re.Pattern[str], ...]

def title_gate(title: str, criteria: Criteria) -> TitleVerdict
```

**Semantics (do not change without updating `config/criteria.yaml` comments and the battery):**

```text
pass = any(title_include pattern matches)  AND NOT any(title_exclude pattern matches)
matching is case-insensitive and unanchored (re.search)
```

`title_exclude` is a **veto**, not an allow-list: a posting must still satisfy `title_include`.
The `(?<!Assistant )(?<!Associate )\bVP\b` lookbehind is load-bearing — it stops the `\bVP\b`
veto from killing "Assistant VP" / "Associate VP" while still vetoing a plain "VP".

Coverage: `tests/test_filters.py` (parametrized battery + two guard tests) and
`scripts/lint_criteria.py` (same battery, runnable as a gate). If you edit
`config/criteria.yaml`, both must still pass.

## 4. `db.py` — schema and migrations (implemented)

Owner: `plan-general.md` §10 (canonical) + `plan-workday.md` §14 (extensions).
`SCHEMA_VERSION = 1` is the current version.

```python
SCHEMA_VERSION: int = 1
DDL: str                                              # all CREATE TABLE/INDEX statements

def connect(path: str | Path) -> sqlite3.Connection   # mkdir parents; row_factory=Row;
                                                      # PRAGMA foreign_keys=ON, journal_mode=WAL,
                                                      # busy_timeout=5000
def migrate(conn: sqlite3.Connection) -> int          # executescript(DDL); stamps schema_version
def schema_version(conn: sqlite3.Connection) -> int   # 0 if unset
def table_names(conn: sqlite3.Connection) -> list[str]
def row_counts(conn: sqlite3.Connection) -> dict[str, int]   # excludes schema_meta
```

**Migration contract:** DDL is idempotent (`IF NOT EXISTS`). Adding a column or table means:
edit `DDL`, bump `SCHEMA_VERSION`, and (when a real upgrade path is needed) add the ALTER in
`migrate()`. Never change an existing column's meaning without a version bump.

### 4.1 Table contracts (columns as declared in `DDL`)

| Table | Columns |
|---|---|
| `schema_meta` | `key` PK, `value` |
| `sources` | `id` PK, `family`, `config` (JSON, `'{}'`), `last_polled` |
| `companies` | `id` PK, `name` UNIQUE, `domain`, `ats_family`, `tenant`, `site`, `allow_email_tier` (`1`) |
| `postings` | `id` PK, `source`, `source_id`, `company_id` FK, `title`, `location`, `remote`, `posted_at`, `jd_text`, `jd_hash`, `apply_url`, `channel_hint`, `contact_emails` (`'[]'`), `visa_signals` (`'{}'`), `first_seen`, `last_seen`, `active` (`1`), `external_path`, `req_id`, `can_apply`; `UNIQUE(source, source_id)` |
| `matches` | `id` PK, `posting_id` FK UNIQUE, `score_match`, `score_fresh`, `score_prior`, `decision`, `visa_decision`, `reason`, `created_at` |
| `resumes` | `id` PK, `posting_id` FK, `profile_version`, `path`, `tailor_report` (`'{}'`), `created_at` |
| `aliases` | `id` PK, `address` UNIQUE, `role` (`contact` \| `ats_account`), `ats_family`, `status` (`'active'`), `created_at`, `burned_at` |
| `web_identities` | `id` PK, `ats_family`, `label`, `proxy_ref`, `status` (`'ready'`), `daily_cap` (`25`), `fail_streak` (`0`), `profile_dir`; `UNIQUE(ats_family, label)` |
| `identities` | `id` PK, `tenant_id` FK, `alias` UNIQUE, `alias_version` (`1`), `password_ref`, `status` (`'provisioning'`), `profile_dir`, `proxy_ref`, `next_available_at`, `apps_today` (`0`), `fail_streak` (`0`), `created_at`, `verified_at` |

| Table | Columns |
|---|---|
| `applications` | `id` PK, `posting_id` FK, `channel`, `address_id` FK, `web_identity_id` FK, `status` (`'queued'`), `attempt` (`0`), `external_id`, `submitted_at`, `artifact_dir`, `error`; `UNIQUE(posting_id)` |
| `messages` | `id` PK, `application_id` FK, `match_confidence`, `direction` (`'inbound'`), `recipient`, `from_addr`, `from_domain`, `subject`, `subject_slug`, `rfc_message_id`, `kind`, `kind_confidence`, `received_at`, `raw_key` UNIQUE |
| `events` | `id` PK, `application_id` FK, `kind`, `payload` (`'{}'`), `ts` |
| `runs` | `id` PK, `stage`, `started_at`, `ended_at`, `counts_json` (`'{}'`), `cost_usd` (`0`), `notes` |
| `suppressions` | `address` PK, `reason`, `added_at` |
| `tenants` | `id` PK, `host`, `tenant`, `site`, `wd_shard`, `locale` (`'en-US'`), `status` (`'unverified'`), `source`, `verified_at`, `facet_counts` (`'{}'`), `matched_req_count` (`0`); `UNIQUE(host, tenant)` |
| `tenant_probes` | `id` PK, `slug`, `shard`, `site_guess`, `http_status`, `error_code`, `checked_at`; `UNIQUE(slug, shard, site_guess)` |
| `shards` | `host_suffix` PK, `resolves` (`0`), `last_checked` |
| `answers` | `id` PK, `tenant_id` FK, `question_hash`, `question_text`, `answer`, `source`, `uses` (`0`), `updated_at`; `UNIQUE(tenant_id, question_hash)` |

Indexes: `idx_postings_company`, `idx_postings_hash`, `idx_postings_seen`,
`idx_applications_status`, `idx_messages_received`, `idx_messages_app`.

Enum domains (enforced in code, not by the DB):

- `aliases.role` ∈ {`contact`, `ats_account`}; `aliases.status` ∈ {`active`, `burned`}
- `identities.status` ∈ {`provisioning`, `ready`, `cooldown`, `retired`, `banned`}
- `applications.status` ∈ {`queued`, `submitted`, `parked`, `failed`, `skipped`}
- `messages.kind` ∈ {`ack`, `status_change`, `rejection`, `interview`, `assessment`, `verification`, `other`}
- `messages.match_confidence` ∈ {`exact`, `high`, `low`, `unmatched`}

## 5. `checks.py` — doctor checks (implemented)

Owner: `plan-general.md` §11 (G0 acceptance gate).

```python
OK, WARN, FAIL, SKIP = "OK", "WARN", "FAIL", "SKIP"

@dataclass(frozen=True)
class Check:
    name: str
    status: str     # OK | WARN | FAIL | SKIP
    detail: str

# every check has the same signature
def check_<name>(s: Settings) -> Check

CHECKS: tuple[Callable[[Settings], Check], ...]
def run_all(s: Settings) -> list[Check]     # catches exceptions -> FAIL, never raises
```

Host probes — `doctor` must run on the Windows host *and* on WSL, so the OS is detected rather
than assumed, and a dead probe degrades to WARN instead of raising:

```python
def _is_wsl() -> bool                 # "microsoft" in /proc/version (False off Linux)
def _total_ram_gb() -> float          # GlobalMemoryStatusEx on win32 | MemTotal on POSIX
RAM_FLOOR_GB: float = 20.0            # WARN threshold; architecture.md section 7 headroom
```

| Check | PASS when | Notes |
|---|---|---|
| `check_python` | `sys.version_info` is 3.13 | else WARN |
| `check_repo_files` | `pyproject.toml`, `uv.lock`, `README.md`, `plan-general.md`, `plan-workday.md`, `AGENTS.md`, `docs/architecture.md`, `docs/design.md` exist | FAIL lists missing |
| `check_config` | all five `config/*.yaml` parse | reports pattern/skill counts; pools=0 -> concurrency 1 |
| `check_address` | `jobagent_contact_address` set, no `+` | FAIL otherwise |
| `check_db` | connect + migrate succeed | reports schema version + table count |
| `check_memory` | RAM probe succeeds and reports >= `RAM_FLOOR_GB` (20 GB) | `GlobalMemoryStatusEx` on Windows, `/proc/meminfo` on POSIX; on WSL it also reports the host's `.wslconfig`; a probe failure is a WARN, never a raise |
| `check_gpu` | `nvidia-smi` works | WARN if absent; notes sm_120 |
| `check_ollama` | reachable; embed model present | WARN if `bge-m3` missing |
| `check_browser` | Patchright imports and launches headless Chromium | FAIL with install hint |
| `check_mail` | worker reachable **and** returns 401 without a token | WARN if `agent_token` unset; FAIL on wrong status |
| `check_deepseek` | key set, model offered, balance >= one day budget | WARN if key unset or balance low |
| `check_budget` | `per_run <= per_day` | FAIL otherwise |

**Contract:** WARN never fails a run; FAIL does. Add a new check by writing
`check_<name>(s) -> Check` and appending it to `CHECKS`. A check must never raise (so
`run_all` stays total), must not require network to be reachable, and must not print secrets.

## 6. `cli.py` — command surface (implemented)

Entry point declared in `pyproject.toml`: `jobagent = "jobagent.cli:app"`.
Built with **Typer** + **Rich**. Two sub-apps: `config`, `db`. Global `--version` is eager.

| Command | Signature / options | Behaviour | Exit |
|---|---|---|---|
| `jobagent --version` | `--version` | prints `jobagent <__version__>` | 0 |
| `jobagent doctor` | — | runs `run_all(settings())`, renders a table | 0 if no FAIL, else 1 |
| `jobagent config show` | `--json/--no-json` | prints parsed criteria + proxy posture (table or JSON) | 0 |
| `jobagent db init` | — | `ensure_dirs()`, `connect(db_path)`, `migrate()` | 0 |
| `jobagent db status` | — | schema version + row counts | 0, or 1 if no DB |
| `jobagent tenants` | — | stub -> `G1-W1` | 2 |
| `jobagent discover` | — | stub -> `G1` | 2 |
| `jobagent match` | — | stub -> `G2` | 2 |
| `jobagent tailor` | — | stub -> `G2` | 2 |
| `jobagent apply` | — | stub -> `G3 / G5-W2` | 2 |
| `jobagent onboard` | — | stub -> `G5-W1` | 2 |
| `jobagent run` | — | stub -> `G6` (full pipeline) | 2 |
| `jobagent report` | — | stub -> `G4` | 2 |

**Contract:** when a stage is implemented, its `_stub("<stage>")` call is replaced by the real
implementation and the command's options are added to this table. Stubs must keep exiting 2 so
scripts can detect "not implemented yet".

**Console contract.** The module-level `console` is built by `make_console()` so every command's
output survives the Windows host:

```python
def make_console() -> Console        # win32: UTF-8 streams + Rich legacy_windows=False
                                     # else:  Console()
```

On `win32` it reconfigures `sys.stdout`/`sys.stderr` to UTF-8 and refuses Rich's legacy-Windows
render path. Both guards are required: Windows Python encodes a captured stream with the ANSI
code page, and Rich's legacy path (`_win32_console.WindowsConsole`) encodes table glyphs with
that code page regardless of the stream's own encoding — which made `jobagent doctor | tail`
(and any CI or log capture) die with `UnicodeEncodeError` while it printed fine interactively.

## 7. Planned protocols (pinned names, proposed signatures)

Owner: `plan-general.md` §3, §4; `plan-workday.md` 6.4, 11. These are the seams that keep the
Workday channel out of the core. Names are fixed by the plan; signatures marked *(proposed)*
are the intended shape and are confirmed when the implementing stage lands.

### 7.1 `DiscoverySource` — one per ATS family *(planned, G1)*

```python
class DiscoverySource(Protocol):
    family: str                                                        # e.g. "greenhouse"
    async def discover(self, source_row: Source, criteria: Criteria) -> AsyncIterator[Posting]:
        """Yield normalized postings for one configured source; idempotent by (source, source_id)."""
    async def poll(self, conn: sqlite3.Connection, criteria: Criteria) -> int:
        """Upsert postings, refresh first_seen/last_seen/active, return the count seen. (proposed)"""
```

Implementations: `discover/workday_cxs.py` (`WorkdayCxsDiscovery`), then board APIs
(Greenhouse/Lever/Ashby/Workable/Recruitee). Must not leak family-specific types upward.

### 7.2 `TenantSource` — tenant candidate providers *(planned, G1-W1)*

```python
@dataclass(frozen=True)
class TenantCandidate:
    host: str
    tenant: str
    site: str | None
    source: str
    evidence: dict[str, Any]                                          # (proposed) e.g. apply_url

class TenantSource(Protocol):
    async def yield_candidates(self, criteria: Criteria) -> AsyncIterator[TenantCandidate]: ...
```

Implementations: `WorkdayTenantDiscovery` (inverted aggregator harvest — default), and later
`BruteForceBreadthSource`, `CrtShCustomDomainSource`. Adding one = one class + one config entry.
Verification is always via the `cxs` oracle (§8.2); sources may run in parallel with weights.

### 7.3 `ApplyChannel` — one per submission channel *(planned, G3/G5)*

```python
class ApplyResult(StrEnum):                 # (proposed)
    SUBMITTED = "submitted"
    PARKED    = "parked"
    FAILED    = "failed"
    SKIPPED   = "skipped"

@dataclass
class ApplyOutcome:
    status: ApplyResult
    external_id: str | None = None
    artifact_dir: Path | None = None
    reason: str | None = None

class ApplyChannel(Protocol):
    channel: str                                                       # "api" | "aggregator" | "web"
    async def can_handle(self, posting: Posting, conn: sqlite3.Connection) -> bool: ...
    async def apply(self, posting: Posting, application: Application,
                    *, dry_run: bool, auto_submit: bool) -> ApplyOutcome: ...
```

Implementations: `apply/workday.py` (`WorkdayWebApply`), plus non-Workday API adapters at G3.
`parked` is a first-class outcome, never an exception.

### 7.4 `Mailbox` — the mail worker client *(planned, G3/G4)*

```python
@dataclass(frozen=True)
class InboundMessage:
    key: str
    recipient: str
    from_addr: str
    subject: str
    received_at: str
    rfc_message_id: str | None

class Mailbox(Protocol):
    async def list(self, recipient: str) -> list[InboundMessage]: ...
    async def read(self, key: str) -> dict[str, Any]: ...              # full payload
    async def latest(self, recipient: str) -> dict[str, Any] | None: ...
    async def delete(self, key: str) -> None: ...
    async def await_code(self, recipient: str, *, since: str, timeout_s: float) -> str | None: ...
    async def drain(self, recipient: str) -> None: ...
```

Consumed by `apply/onboard.py` for verification codes and by `mail/attributer.py`. The HTTP
contract it wraps is §8.1; honor the persistence rule (persist before delete) from
`plan-general.md` §6.2.

## 8. External API contracts

### 8.1 Mail worker HTTP API (implemented upstream; consumed from G3)

Source of truth: `../my-email-worker/AGENT_ONBOARDING.md`. Base URL: `Settings.mail_worker_base`.
All requests: `Authorization: Bearer <Settings.agent_token>`. `recipient` and `key` **must be
URL-encoded**.

| Endpoint | Params | Returns |
|---|---|---|
| `GET /emails` | `recipient` | `{"emails": [{"key", "metadata": {recipient, from, subject, receivedAt}}]}` — keys + metadata only, **no bodies**; KV order is **not** chronological |
| `GET /email` | `key` | full message payload (below) |
| `GET /latest` | `recipient` | the most recent message for that recipient |
| `DELETE /email` | `key` | `{"ok": true}` |

Message payload fields: `id`, `recipient`, `from`, `to`, `subject`, `text`, `html`, `date`,
`receivedAt`, `messageId` (any of `subject`/`text`/`html`/`date`/`messageId` may be absent).

KV key format: `inbox:<recipient>:<subject-slug>:<ISO timestamp>-<uuid>` — the `subject-slug`
(lowercase, hyphenated, <=48 chars) makes a message identifiable **from its key alone**.

Status codes: `200` ok · `204` CORS preflight · `400` missing param · `401` bad/missing token ·
`404` not found · `500` KV error.

Consumption rules (from `plan-general.md` §6.2-6.3):

1. Attribute in **two tiers**: tier 1 = `from` domain + subject company token (body-free);
   tier 2 = req id in subject -> body read (`text`, falling back to tag-stripped `html`) ->
   portal link id -> time window. Never guess; record `match_confidence`.
2. **Persist to the DB before `DELETE`**; leave unresolved mail in KV.
3. Idempotency on `messageId`; reject `receivedAt < flow_start`; prefer listing + client-side
   sort by `receivedAt` over `/latest`.
4. Read-once delete is deferred until attribution is resolved and persisted.

### 8.2 Workday `cxs` discovery API (verified; used from G1-W1/W2)

Verified facts: `plan-workday.md` §1 and the probe log (appendix).

```text
POST https://{host}/wday/cxs/{tenant}/{site}/jobs
Body: {"appliedFacets": {...}, "limit": N, "offset": M, "searchText": "..."}
Headers: none required (no auth, no CSRF); a browser-like TLS fingerprint is needed for HTML paths
Response: {"total", "jobPostings": [{"title", "externalPath", "locationsText", "postedOn",
           "bulletFields"}], "facets": [{"facetParameter", "descriptor",
           "values": [{"descriptor", "id", "count"}]}], "userAuthenticated"}
```

| Constraint | Value |
|---|---|
| `limit` | **max 20** (a value of 100 returns no `jobPostings`) |
| `total` | **caps at 2000** -> size a tenant by summing `facets`, never by `total` |
| CSRF/auth | none for this endpoint |

Oracle for tenant/site validity (one `limit:1` probe per `(slug, shard, site_guess)`):

| HTTP | Envelope | Meaning | Action |
|---|---|---|---|
| `200` | — | tenant + site valid | store, harvest `facets` |
| `404` | `{"errorCode": "S21", "message": "not found: Job_Posting_Site_ID=X"}` | tenant exists, **site slug wrong** | brute slug candidates / escalate |
| `422` | `{"errorCode": "HTTP_422", "message": ""}` | no such tenant | negative-cache 30 days |

Drift canary signal: `errorCaseId` is unique per request; alert if the error envelope changes.
DNS note: `*.wdN.myworkdayjobs.com` resolves for **any** slug (browsing DNS is not a validity
test) — existence is only provable via `cxs`. Root `/` returns `406`.

Job detail / apply-flow endpoints: **[?] to verify from the `G0-W` HAR** (`plan-general.md` §14).

## 9. Workday apply state machine (planned, G5-W2)

Owner: `plan-workday.md` §12. Implemented in `apply/workday.py` (`WorkdayWebApply`).

```text
ensure_session(tenant)
  |- logged in? -> continue
  |- else login(creds)  |  else create_account() -> mail.await_code() -> same-context link/code
  |- captcha/unknown -> park("needs_manual_signup") + `jobagent signup --headed`
open_application  : {host}/{locale}/{site}{externalPath}/apply
  -> "Autofill with Resume"      (NOT "Apply Manually")
  -> upload tailored PDF -> wait for parse (network idle AND a known field non-empty)
  -> My Information  : a11y diff vs profile -> patch only mismatches
  -> My Experience   : reconcile parsed history; add missing roles/skills/education
  -> Questions       : [data-automation-id^="formField-"] -> answers.yaml -> cache -> LLM -> persist hash
                       handle multiSelect, date pickers, repeaters ("Add Another")
  -> Disclosures     : static defaults (Decline to self-identify)
  -> Review          : screenshot + text artifact + self-consistency check
  -> submit | park   : policy-gated (park by default; --auto-submit opts in)
```

Hold these invariants:

- **Primary selectors are `data-automation-id`** (`legalNameSection_firstName`, `email`,
  `file-upload-drop-zone`, `bottom-navigation-next-button`, `createAccountSubmitButton`,
  `formField-<id>`). Fallback order: `autocomplete` -> `get_by_label` -> fuzzy label ->
  LLM (vision, last resort, cached).
- **Verification links must open in the same browser context** as the signup, or the token
  lands in the wrong cookie jar.
- **Failure -> `trace.zip` + screenshot + `page.content()`** into `artifacts/`; `attempt += 1`;
  backoff; `fail_streak >= 3` -> quarantine the tenant and continue. **A park is not an error.**
- Persistent context **per identity** (`identities.profile_dir`), one sticky proxy per identity
  (`identities.proxy_ref`); `tenant_concurrency_cap = 1` until proxies exist.

## 10. Config file contracts

Every file is validated by the Pydantic models in §2.2; a typo fails at startup. `plan-general.md`
§2 is authoritative for the criteria/visa semantics.

### 10.1 `config/criteria.yaml` -> `Criteria`

```yaml
target:
  seniority: [str, ...]                 # required
  title_include: [regex, ...]           # required; allow-list
  title_exclude: [regex, ...]           # required; VETO (see §3)
  stack_must: [str, ...]                # default []
  stack_nice: [str, ...]                # default []
  domain_nice: [str, ...]               # default []
  locations: { preferred: [..], next: [..], also: [..] }   # tiers 1/2/3
  comp: { IE_min_eur: int, UK_min_gbp: int, remote_min_eur: int }
  top_k_per_run: int                    # default 25
  fresh_window_minutes: int             # default 60
```

Patterns are Python regexes, matched case-insensitively and unanchored. Semantics are pinned in
§3 and by `scripts/lint_criteria.py`.

### 10.2 `config/visa.yaml` -> `Visa` (`visa:` top key)

```yaml
visa:
  right_to_work: { IE: none, default: sponsorship_required }   # country -> rule
  jd_filter:
    exclude_if: [regex, ...]            # ranking penalty / drop
    boost_if:   [regex, ...]            # ranking boost
  answers:
    IE: { require_sponsorship: 'No', right_to_work: 'Yes' }
    GB: { require_sponsorship: 'Yes', right_to_work: 'No' }
    default: { require_sponsorship: 'Yes' }
```

Country is inferred from the posting location so IE/GB answers cannot cross-contaminate.
`matches.visa_decision` records the outcome.

### 10.3 `config/answers.yaml` -> `Answers`

```yaml
answers:       # canonical question -> answer; value may be 'country:<key>' to resolve via visa.yaml
  '<normalised question>': '<answer or country:<key>>'
disclosures:   # voluntary self-ID: always decline, never guess
  gender: 'Decline to self-identify'
  race_ethnicity: 'Decline to self-identify'
  veteran_status: 'Decline to self-identify'
  disability_status: 'Decline to self-identify'
  preferred_name: ''            # blank => legal first name
```

### 10.4 `config/tenants.yaml` -> `Tenants`

```yaml
tenants: [ {id, host, tenant, site, wd_shard, locale, status, source, verified_at,
            facet_counts, matched_req_count, ...operator knobs} ]
defaults:
  ats_family: workday
  locale: en-US
  identities_per_tenant: 1                  # ceiling, not a target
  tenant_concurrency_cap: 1                 # forced to 1 while no proxy pool exists
  max_apps_per_identity_per_day: 25
  proxy_pool: null
  status: unverified
```

### 10.5 `config/proxies.yaml` -> `Proxies`

```yaml
pools:
  <pool-name>: ['http://user:pass@gate.provider.net:8000', ...]
```

Hard rules baked into the code: a tenant whose identities have no pool forces
`tenant_concurrency_cap = 1`; proxies are **never** used for discovery and **never** for email.
An empty `pools: {}` is valid (doctor reports it).

## 11. Question / answer cache contract (planned, G3 + G5-W3)

Owner: `plan-general.md` §6.2; `plan-workday.md` §11-12. Implemented in
`apply/questions.py`; table `answers`.

Resolution order for a web-form question (stop at the first hit):

1. `config/answers.yaml` (canonical seed), with `country:<key>` resolved via `config/visa.yaml`
   for the posting's country.
2. Tenant-scoped DB cache: `answers` keyed by `UNIQUE(tenant_id, question_hash)`.
3. Local LLM (`ollama_classify_model`) — only on a genuine cache miss.
4. Escalate to `deepseek_model` if the local model is unsure.
5. Persist the answer with `source` and bump `uses`; on failure, **park** rather than guess.

**Question normalisation (must match between seeder and resolver):** lowercase; strip trailing
`*` / `required` markers; strip punctuation; collapse whitespace; then hash. A mismatch here
silently defeats the cache, so the normaliser is a single shared function — never re-implement it.

Voluntary self-ID fields always take the `answers.yaml` `disclosures` value (decline); never
infer demographics.

## 12. Cross-cutting contracts

### 12.1 LLM layer (planned, G1+)

Owner: `plan-general.md` §1.5. Implemented in `llm/` (`client.py`, `cost.py`).

```python
class LLMTier(StrEnum):  PRIMARY = "primary"; ESCALATE = "escalate"
                         CLASSIFY = "classify"; FAILOVER = "failover"
class LLMClient(Protocol):
    async def complete(self, *, tier: LLMTier, messages: list[Message],
                       schema: type[BaseModel] | None = None,
                       effort: Literal["low", "high", "max"] = "high") -> BaseModel | str: ...
    async def embed(self, texts: list[str]) -> list[list[float]]: ...     # bge-m3
```

| Tier | Model | Use |
|---|---|---|
| `primary` | `deepseek-flash` | JD -> requirements, resume rewrite; `effort: low` for extraction |
| `escalate` | `deepseek-v4-pro` | hard cases only (text-only) |
| `classify` | `qwen3:14b` (Ollama) | classification, question normalisation, cache misses |
| `failover` | `qwen3:8b` (Ollama) | keeps runs alive on 429/outage |
| embeddings | `bge-m3` (Ollama) | fallback `nomic-embed-text` / `fastembed` CPU |

Rules: every structured output is Pydantic-validated with **one retry**, then a **deterministic
fallback** (never an unbounded retry loop). Ollama speaks OpenAI at `{ollama_base}/v1`, so the
tier wiring is config, not code. Vision fallback = `deepseek-flash` image input, used only for
unknown web-form fields after the selector/label chain fails.

### 12.2 Cost accounting (planned, G1+)

```python
class CostMeter:
    def add_usage(self, usage: ChatUsage) -> float: ...       # returns running run-cost USD
    def run_cost(self) -> float: ...
    def day_cost(self) -> float: ...
    def assert_within_budget(self) -> None: ...               # raises BudgetExceeded
```

Read `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens` / `prompt_tokens_details.cached_tokens`
from DeepSeek responses. `BudgetExceeded` must stop the run cleanly (records a `runs` row) rather
than silently downgrading. Caps come from `Settings.jobagent_max_cost_per_run_usd` /
`..._per_day_usd`.

### 12.3 Error and result conventions

- **Park, don't crash.** A step that cannot proceed without a human writes artifacts and returns
  a parked outcome; the orchestrator continues with other work.
- **Deterministic fallback.** Any LLM/parse failure falls back to a conservative, non-fabricated
  result (e.g. the verbatim source bullet; `other` classification; `low` confidence).
- **Idempotency keys.** `postings UNIQUE(source, source_id)`, `applications UNIQUE(posting_id)`,
  `messages UNIQUE(raw_key)`, `answers UNIQUE(tenant_id, question_hash)`.
- **CLI exit codes.** `0` success · `1` a doctor FAIL or a missing DB · `2` not-implemented stub.
  Do not introduce other exit codes without documenting them here.
- **Never log secrets.** `doctor` detail strings must stay secret-free.

## 13. Testing contract

- Runner: `uv run pytest -q` (config in `pyproject.toml`: `testpaths = ["tests"]`).
- One module per concern: `tests/test_<module>.py`. Shipping gates today: `tests/test_filters.py`
  (title gate) and `tests/test_checks.py` (`doctor`'s platform probes + the console).
- Platform-specific code is exercised on the host that owns the branch
  (`sys.platform == "win32"` + `pytest.skip` elsewhere), so the suite stays green on Windows and
  on WSL — `doctor` is documented to work on both.
- Prefer **table-driven** tests (`@pytest.mark.parametrize`) for gate/filter/normaliser logic.
- Test against the **real** loaded config (`load_criteria()`), not a copy — an intentional edit
  that flips a case must surface, not be masked.
- CLI-level gates (`doctor`) are exercised through `checks.run_all()` rather than spawning the
  process, so tests do not depend on the network.
- `scripts/lint_criteria.py` is the runnable counterpart of the filters battery and must agree
  with it; keep the two case lists in sync when criteria change.

## 14. Open questions affecting contracts

Mirrored from `plan-general.md` §14 / `plan-workday.md` §1. Resolve by moving `[?]` -> `[V]`
in the plan **and** updating the relevant contract above.

| `[?]` | Affects | Resolved at |
|---|---|---|
| Workday job-detail + apply-flow endpoints, selectors | §9 state machine | `G0-W` HAR |
| Does `curl_cffi impersonate="chrome"` clear the Workday `406`? | §8.2 landing fetch | `G1-W1` |
| Do ATS bodies reliably name role/req id? | §8.1 tier-2 attribution | `G4` |
| Do ATS subjects/senders consistently expose the company? | §8.1 tier-1 attribution | `G3` |
| Non-Workday ATS apply endpoints (undocumented internals) | §7.3 | HAR per channel |

## 15. How this document changes

Update this file on **any** change to a public class, function, dataclass field, Pydantic model,
CLI command or flag, exit code, config key, DB table/column, or external HTTP call. When a
*(planned)* contract is implemented, flip its status to **implemented** and correct the signature
to match the code. When a `[?]` is verified, update §14 and the contract it affects. This rule is
enforced by [`../AGENTS.md`](../AGENTS.md) §8.








