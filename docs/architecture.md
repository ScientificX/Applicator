# Architecture — job-agent (high-level design)

| | |
|---|---|
| **Doc set** | `PS-5.4` |
| **Status** | G0 implemented; G1-G6 designed, not built. Contracts marked *(planned)* are proposals, not shipped code. |
| **Authoritative sources** | [`../plan-general.md`](../plan-general.md) (program), [`../plan-workday.md`](../plan-workday.md) (Workday channel) |
| **Companion docs** | [`design.md`](design.md) (low-level design + class/API contracts), [`../AGENTS.md`](../AGENTS.md) (agent workflow) |
| **Maintenance** | Structural change -> update this file. See `AGENTS.md` §8. |

This document is the **high-level design (HLD)**: what the system is made of, how the pieces
talk to each other, and where the seams are. It deliberately does **not** restate design owned
by the plan documents; it references them by section. Concrete class/API contracts live in
[`design.md`](design.md).

---

## 1. Purpose and scope

`job-agent` is an autonomous, multi-channel job-application agent. It:

1. **discovers** postings across many sources (ATS boards + aggregators) faster than a human,
2. **filters/ranks** them against one target profile (title, stack, location, comp, visa),
3. **tailors** a resume per posting without fabricating anything,
4. **routes** the posting to the best submission channel and **executes** it,
5. **tracks** the result from inbound ATS mail, and
6. **learns** — feeding the measured response rate back into ranking and routing.

Pipeline: `hunt -> rank -> tailor -> route -> execute -> track -> learn`.
In/out scope is fixed by `plan-general.md` §0. Applying *by* email is deferred (Appendix B).

## 2. System context

```text
                         +-----------------------------------------------+
                         |              job-agent (this repo)            |
                         |  Python 3.13 CLI, SQLite state, single host   |
                         +-----------------------------------------------+
   discovers / ranks / applies  |         |          |          |
        +-----------------------+         |          |          |
        |              |                  |          |          |
        v              v                  v          v          v
 +-------------+ +-------------+  +-----------+ +--------+ +-----------+
 | ATS boards  | | Aggregators |  | DeepSeek  | | Ollama | | Target    |
 | (Workday    | | (JobSpy,    |  | (cloud    | | (local | | ATS web   |
 |  cxs API,   | |  Adzuna,    |  |  LLM)     | |  LLM)  | | forms     |
 |  Greenhouse,| |  RemoteOK,  |  +-----------+ +--------+ | (browser) |
 |  Lever, ...)| |  HN, ...)   |                          +-----------+
 +-------------+ +-------------+
        ^                                                    ^
        |  inbound mail (acks, status, verification codes)   |  HTTPS via
        |                                                    |  proxies
        +--------------------+          +--------------------+
                             |          |
                    +------------------+------------+
                    |  my-email-worker (Cloudflare) |
                    |  inbound KV + HTTP API        |
                    |  (sibling repo, TypeScript)   |
                    +-------------------------------+
```

Trust and ownership boundaries:

- **The mail worker is a separate service and a separate repo.** The agent talks to it over
  HTTP only (`AGENT_ONBOARDING.md`). The agent never depends on worker internals.
- **The ATS is the system of record for an application.** The agent DB is a local mirror plus
  analytics store; a portal confirmation outranks a local `submitted` status.
- **LLM providers never make policy decisions silently.** Channel routing and filters are
  deterministic code; LLMs extract/classify/rewrite under schema validation with a fallback.
- **Proxies are used only for web/HTTP apply and scraping** — never for discovery, never for
  email (`plan-general.md` §8).

## 3. Layered architecture (component map)

```text
            +----------------------------------------------------------+
  CLI /     |  cli.py  (Typer: doctor, config, db, tenants, discover,  |
  entry     |           match, tailor, apply, onboard, run, report)     |
            +----------------------------------------------------------+
                                     |
   +-------------+------------------+------------------+---------------+
   |             |                  |                  |               |
   v             v                  v                  v               v
+---------+  +----------+      +-----------+      +-----------+   +-----------+
| doctor  |  | config   |      | discovery |      | match     |   | tailor    |
| checks  |  | (typed)  |      | (hunt)    |      | (rank)    |   | (resume)  |
|checks.py|  |config.py |      | discover/ |      | match/    |   | tailor/   |
+---------+  +----------+      +-----------+      +-----------+   +-----------+
   |             |                  |                  |               |
   v             v                  v                  v               v
+---------------------------------------------------------------------------+
|                          shared infrastructure                            |
|  db.py (SQLite, migrations)    mail/  (worker client, attributer)         |
|  net.py (curl_cffi HTTP)       models.py (Source, Posting)                |
|  llm/ (client, tiers, cost)    channels.py (channel-selection policy)     |
|  apply/ (ApplyChannel impls, onboarding, question cache)                  |
+---------------------------------------------------------------------------+
                                     |
                                     v
                       state/jobagent.db   +   artifacts/ (HAR, resumes,
                                               screenshots, traces, reports)
```

Layer responsibilities (concern -> module -> owning plan section):

| Layer | Responsibility | Owner doc |
|---|---|---|
| **entry / CLI** | user-facing commands, exit codes, stubs that name their stage | `plan-general.md` §11 |
| **config** | typed `Settings` (env) + validated YAML models; single source of truth for targeting | `plan-general.md` §2, §1.5 |
| **discovery (hunt)** | poll sources, normalise to `postings`, freshness/incremental, dedupe, politeness | `plan-general.md` §4; `plan-workday.md` §6-7 |
| **match (rank)** | hard filters (title gate), visa/right-to-work, scoring `match x freshness x prior` | `plan-general.md` §2, §4; `plan-workday.md` §8 |
| **tailor** | select-and-patch resume + the five anti-exaggeration gates + `tailor_report.json` | `plan-general.md` §5; `plan-workday.md` §9 |
| **route** | deterministic channel selection (API > aggregator > web form) | `plan-general.md` §3 |
| **execute (apply)** | `ApplyChannel` implementations, identity onboarding, question/answer cache | `plan-general.md` §3, §8; `plan-workday.md` §11-13 |
| **track (mail)** | worker HTTP client, two-tier attribution, classification, response metrics | `plan-general.md` §6 |
| **learn** | feed response rate per channel/company/source back into scoring/routing | `plan-general.md` §2, §6.3 |
| **infrastructure** | SQLite access, LLM tiers/budget, doctor checks, events journal | `plan-general.md` §1.5, §10; `plan-workday.md` §10 |

## 4. Pipeline stages and milestone mapping

| Pipeline stage | Program stage | Module (planned) | Key output rows |
|---|---|---|---|
| (scaffold + doctor) | **G0** | `config.py`, `db.py`, `checks.py`, `cli.py` | schema, host preflight |
| hunt | **G1** | `discover/` (`DiscoverySource`, `TenantSource` impls) | `sources`, `companies`, `postings`, `tenants`, `tenant_probes`, `shards` |
| rank + tailor | **G2** | `match/`, `tailor/` | `matches`, `resumes` |
| execute (non-Workday) | **G3** | `apply/`, `channels.py`, `mail/` | `applications`, `aliases`, `messages` |
| track / learn | **G4** | `mail/` (attributer, classifier), `report` | `messages`, `events` |
| execute (Workday) | **G5** (workstream W) | `apply/workday.py`, `apply/onboard.py`, `apply/questions.py` | `identities`, `applications` |
| autonomy + hardening | **G6** | scheduler, canary, cost report, prune/backup | `runs`, `events` |

Acceptance criteria per stage: `plan-general.md` §11; Workday steps: `plan-workday.md` §15.

## 5. Seams and gates

The three gates are the only points where the Workday workstream enters the program. A stage
crosses a gate only when the gate's preconditions (below, from `plan-general.md` §11.1 and
`plan-workday.md` "Entry, exit and code map") are met.

```text
  G0 --(H1a: discovery seam)--> G1 -- G2 -- G3 --(H1b: apply seam)--> G5
                                                |                      |
                                                +---- G4 -------------+
                                                            |
                                            (H2: return seam) --> G6
```

| Gate | Enters | Preconditions (abbrev.) | Exit |
|---|---|---|---|
| **H1a** | G1 | general `DiscoverySource` protocol + `postings`/`companies`/`tenant_probes`/`shards` migrations + `curl_cffi` client wrapper exist | `WorkdayCxsDiscovery` + `WorkdayTenantDiscovery` pass, consumed through the protocol with no Workday-specific branches |
| **H1b** | G5 | G3 proven on a non-Workday channel; attribution working on real mail incl. a Workday ack; address model live; per-identity proxies; HAR captured; `doctor` green; budget caps | `WorkdayWebApply` satisfies `ApplyChannel`; `--dry-run` reaches Review with 0 manual inputs; parks are first-class outcomes |
| **H2** | G6 | — | submit/park/unattributed rates into §2; quirks + drift signatures into §12; HAR -> recorded fixtures; verified `cxs` behaviour into §1 |

**Design consequence:** the Workday-specific logic must be reachable *only* through the shared
protocols, so the discovery and apply layers stay channel-agnostic. Do not leak
`workday_*` types into `match/`, `tailor/`, or the scheduler.

## 6. Data architecture

- **One SQLite database** at `state/jobagent.db` (WAL, `foreign_keys=ON`, `busy_timeout=5000`).
  Schema is created/upgraded by `db.migrate()`; version tracked in `schema_meta` table
  (`SCHEMA_VERSION` in `db.py`).
- **Canonical model owner: `plan-general.md` §10.** Channel extensions (Workday) are declared
  in `plan-workday.md` §14 and are physically in the same DB/DDL. Full column lists are in
  [`design.md`](design.md) §"Data model".
- **Naming canon:** `postings` (never `jobs`), `applications`, `messages`, `events`; the funnel
  is a *view over `events`*, never its own table.
- **Event journal:** `events(id, application_id, kind, payload, ts)` records state transitions
  so analytics never depends on mutable status columns; `runs` records per-stage execution and
  cost.
- **Filesystem artifacts** (`artifacts/`, gitignored): HAR captures, tailored resumes,
  screenshots/traces for parks, `tailor_report.json`, reports. Paths are referenced from the
  DB (e.g. `applications.artifact_dir`, `resumes.path`).
- **Secrets** live in `.env` and (Workday) `state/secrets.json` (0600) — never in the DB, never
  in git.

Data flow for one application (happy path, non-Workday):

```text
source poll -> postings -> title_gate/visa -> matches -> resume -> resumes
   -> channel policy -> applications(queued) -> apply -> applications(submitted)
   -> inbound mail -> messages(attributed) -> events -> report (response rate)
```

## 7. Runtime and process model

- **Single-user, single-host batch CLI.** There is no server and no daemon in v1. Runs are
  invoked through the CLI (`jobagent run`, or one stage at a time) and are safe to re-invoke;
  all durable state lives in SQLite + `state/` + `artifacts/`.
- **Stage-split scheduler.** Ollama-hot batches (classification/normalisation) run separately
  from browser-hot batches, with `OLLAMA_KEEP_ALIVE=0` before browser work, so a 16 GB GPU and
  the host's 31 GB of RAM are not contended (`plan-workday.md` §16).
- **One host, one `localhost`.** The agent, Chromium and - once the WSL Ollama unit is retired -
  Ollama all run natively on the Windows host, so `OLLAMA_BASE=http://localhost:11434` needs no
  host-IP lookup and no WSL boundary crossing. Until that unit goes, WSL's relay owns port 11434
  and the native server has to run on an alternate port. This is deliberate: a service running
  *inside* WSL is republished by WSL's `wslrelay.exe` on the Windows side, which both shadows the
  port for Windows clients and blocks a native process from binding it (`plan-general.md` §1.4).
- **Pacing and concurrency.** Per-host politeness (1-2 concurrent, jitter), per-identity daily
  caps, per-tenant `tenant_concurrency_cap` (forced to 1 while no proxy pool exists), and a
  global politeness budget.
- **Budget enforcement.** Hard per-run and per-day USD caps (`Settings`) checked around every
  LLM call; `cost.py` reads DeepSeek prompt-cache counters. Balance is small — respect the caps.
- **Failure model.** A `park` (manual intervention needed) is a first-class outcome, not an
  error: artifact + screenshot + trace are written, `attempt` incremented, backoff applied,
  and `fail_streak >= 3` quarantines the tenant without blocking other work.
- **Observability.** `events` journal + `runs` (stage, timings, counts, cost) + Rich tables in
  the CLI. `doctor` is the preflight gate for the environment.

## 8. Cross-cutting concerns

| Concern | How it is handled | Owner |
|---|---|---|
| **Identity consistency** | one canonical contact address + one name/phone across resume, form and account | `plan-general.md` §6.1, §8 |
| **Attribution** | content-based, two-tier (company then application) from KV key + metadata + body; `match_confidence`; never guess | `plan-general.md` §6.2 |
| **Anti-fabrication** | five ordered gates before any resume text ships; verbatim fallback | `plan-general.md` §5 |
| **Determinism** | filters/routing/scoring are deterministic code; LLM only under schema validation with a deterministic fallback | `plan-general.md` §1.5, §3 |
| **Cost control** | per-call accounting + per-run/per-day caps; local models for high-volume tiny calls | `plan-general.md` §1.5 |
| **Secrets** | `.env` (agent) + `state/secrets.json` 0600 (Workday accounts); gitignored; `doctor` passes with blanks | `plan-general.md` §13 |
| **Idempotency** | `UNIQUE(posting_id)` on `applications`, `UNIQUE(source, source_id)` on `postings`, `message_id` idempotency on mail reads | `plan-general.md` §10, §6.3 |
| **Config validation** | Pydantic models fail loudly at startup on a YAML typo | `src/jobagent/config.py` |

## 9. Extension points

- **New discovery source:** implement `DiscoverySource` (per-family board API) — one module in
  `discover/`, registered in config. No change to the pipeline.
- **New tenant source:** implement `TenantSource.yield_candidates(criteria)` — e.g.
  `BruteForceBreadthSource`, `CrtShCustomDomainSource`; inverted aggregator harvest is today's
  default (`plan-workday.md` §6.4).
- **New apply channel:** implement `ApplyChannel` and add its HAR as a fixture. Adding a channel
  is *one class + one config entry*; the channel-selection policy picks it up automatically.
- **New ATS family:** add a board-API module (Greenhouse/Lever/Ashby/Workable/Recruitee/...)
  and, if it needs accounts, an `onboard/` path that reuses the mail worker and answer cache.

All four are additive: schema, orchestration and the CLI surface stay unchanged. If a change
would need to alter the pipeline, the DB model or a protocol, that is a design change and must
go through `plan-general.md` first.

## 10. Quality attributes and constraints

| Attribute | Target / constraint |
|---|---|
| **Correctness of attribution** | company-level >= 95%, application-level >= 80% (G4 acceptance) |
| **Zero duplicates** | one application per posting, enforced by schema + company/title dedupe |
| **Freshness** | new postings detected <= 60 min after posting; recency-first scheduling |
| **Cost** | < $0.01 per application; per-run <= $1.00, per-day <= $3.00 (hard caps) |
| **Throughput** | >= 80% submit rate; >= 20 live Workday tenants auto-discovered |
| **Runtime platform** | Native **Windows 11** on one host (agent + Ollama + Chromium on one `localhost`); RTX 5060 Ti 16 GB (sm_120 -> Ollama-only for GPU); 31 GB RAM; Workers Paid for the mail handler. Host-agnostic code: WSL is detected, not assumed |
| **Portability** | `doctor` passes on a machine with blank secrets (network checks degrade to WARN) |
| **Auditability** | every shipped resume has `tailor_report.json`; every application has an `events` trail |

## 11. Risks and where they are tracked

Program-level risks and mitigations: `plan-general.md` §12. Workday-specific:
`plan-workday.md` §16. Verified vs assumed facts and the live `[?]` register:
`plan-general.md` §14 (mirrored into [`design.md`](design.md) where they affect a code
contract). Do not duplicate risk text here — reference it.

## 12. How this document changes

Update this file whenever a change alters the **shape** of the system: a new module/layer, a
new or moved protocol, a changed data-flow direction, a new external dependency, or a new
gate. Contract-level detail goes in [`design.md`](design.md); facts and decisions go in the
plan documents. This rule is enforced by [`../AGENTS.md`](../AGENTS.md) §8.



