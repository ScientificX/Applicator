# AGENTS.md

**Audience: AI coding agents.** Read this file before touching anything in the repo.
It tells you where the truth lives, how the project is built and tested, and the
**non-negotiable workflow** for landing a change (worktree -> branch -> merge request)
plus the **documentation duty** that every change carries.

Human entry point: [`README.md`](README.md). Authoritative program design:
[`plan-general.md`](plan-general.md).

---

## 1. What this project is

`job-agent` is an autonomous, multi-channel job-application agent. It discovers postings
that match a target profile, ranks them, tailors a resume per posting, submits through the
best available channel, and measures what actually gets responses.

Pipeline: **hunt -> rank -> tailor -> route -> execute -> track -> learn**.

- Language/runtime: **Python 3.13**, managed with **`uv`** (`.python-version` pins 3.13).
- **Host: Windows 11, native** — repo at `C:\Users\favou\development\job-agent`, sibling
  worktrees beside it, `.venv\Scripts\python.exe` as the local interpreter. **WSL is not the
  runtime host** (it was until the `fix/windows-native-host` change); the code stays
  host-agnostic, but Windows is the platform of record (§4, `docs/architecture.md` §10).
- Storage: **SQLite** (`state/jobagent.db`), schema versioned by `db.py` (`SCHEMA_VERSION`).
- LLMs: **DeepSeek `deepseek-flash`** (cloud) + **Ollama** (local `qwen3:8b`/`qwen3:14b`,
  embeddings `bge-m3`).
- Browsers/HTTP: **Patchright** (patched Playwright) + **curl_cffi**.
- Mail: **`my-email-worker`** (Cloudflare Worker, sibling repo) — **inbound only**;
  the agent talks to it over HTTP.
- Current stage: **`PS-5.4`, G0 landed** (uv scaffold, typed config, SQLite schema v1,
  `doctor`, criteria title gate + test battery). **G1..G6 are not built yet** — most of
  the package tree is planned, not scaffolded.

## 2. Where the truth lives (read these; do not restate them)

| Doc | Role | Authority for |
|---|---|---|
| [`README.md`](README.md) | human entry point | navigation |
| [`plan-general.md`](plan-general.md) | **the program** (authoritative) | anything cross-channel: criteria, visa, channels, sources, tailoring, LLM, email identity, data model, milestones, risks |
| [`plan-workday.md`](plan-workday.md) | workstream W (Workday) | `cxs` endpoints + oracle, shards/site discovery, apply state machine + selectors, onboarding, HAR fixtures |
| [`docs/architecture.md`](docs/architecture.md) | **high-level design** | component map, layering, seams/gates, data flow, runtime view |
| [`docs/design.md`](docs/design.md) | **low-level design** | class/API contracts, DB columns, config schemas, HTTP contracts |
| `../my-email-worker/AGENT_ONBOARDING.md` | mail service contract | the worker's HTTP API only (from a worktree it is `../../my-email-worker/AGENT_ONBOARDING.md`, §9.1) |

**Conflict rule:** if two docs disagree, `plan-general.md` wins and the loser gets fixed.
The workstream doc never restates shared design — it references it.

## 3. Golden rules (non-negotiable)

1. **Naming canon.** `postings` (never `jobs`), `applications`, `messages`, `events`
   (the funnel is a *view over events*, never its own table). Keep table/column/identifier
   names exactly as in `db.py` and `plan-general.md` §10.
2. **Config is the source of truth for targeting.** Title/visa/location/comp logic lives in
   `config/*.yaml` and is validated by Pydantic models in `src/jobagent/config.py`. Do not
   hard-code criteria in Python. If you change criteria, run the lint gate (§6).
3. **Never invent facts.** No fabricated resume content, employers, dates, metrics, or
   API behaviour. Verified facts are marked `[V]`; open questions `[?]` in
   `plan-general.md` §14. If you verify a `[?]`, move it to `[V]` in the plan **and** mirror
   it into `docs/design.md`.
4. **Secrets and private state are gitignored** (`.env`, `state/`, `artifacts/`). Never
   commit them, never print secret values, never add them to docs. Use `.env.example` as
   the template. `doctor` is designed to pass with blank secrets.
5. **One identity, many aliases.** The same name/address/phone must appear on every resume,
   form and account. Addresses are for deliverability/account identity, **never** for
   attribution (attribution comes from message content — `plan-general.md` §6.2).
6. **Never bypass the anti-exaggeration gates** when building the tailoring layer: numeric
   provenance -> entity provenance -> entailment -> title/lie -> ATS extraction. A rejected
   rewrite falls back to the source bullet **verbatim**.
7. **`plan-general.md` §11 stage router is the ordering authority.** A stage is entered only
   through its gate (H1a/H1b/H2). Do not pull workstream W (Workday apply) forward before
   its preconditions land.
8. **Keep the docs in step with the code.** This is mandatory — see §8.

## 4. Environment setup

Native Windows checkout — the runtime host. `uv`, `git` (Credential Manager) and Patchright's
Chromium are all per-user installs, so nothing here needs admin rights.

Layout: the **primary checkout** is `C:\Users\favou\development\job-agent`, and every working copy
lives in `C:\Users\favou\development\.worktrees\job-agent-<slug>` (§9.1). `~/development` holds one
directory per project plus `.worktrees` — nothing else.

```powershell
cd C:\Users\favou\development\job-agent
uv sync                                   # create/refresh .venv from pyproject.toml + uv.lock
if (-not (Test-Path .env)) { Copy-Item .env.example .env }   # then fill in (never commit .env)
uv run patchright install chromium
```

- **Ollama belongs on the native Windows host**, on `127.0.0.1:11434` - which is what `OLLAMA_BASE`
  already points at. While a WSL Ollama still runs, WSL's localhost relay (`wslrelay.exe`) owns
  that port on the Windows side, so the native server has to be started with
  `OLLAMA_HOST=127.0.0.1:11435` *and Windows clients silently reach the WSL instance instead*
  (`plan-general.md` §13 item 5 - retire the unit to end this). Never run two Ollamas. Tune the
  native server with the user environment variables `OLLAMA_MAX_LOADED_MODELS=1`,
  `FLASH_ATTENTION=1`, `KV_CACHE_TYPE=q8_0`.
- `src/jobagent/config.py` loads `.env` automatically and resolves repo-relative paths
  (`state/`, `artifacts/`) against the repo root, so `.env` values are host-independent.
- `my-email-worker` is a **sibling repo with no remote**: it lives at
  `C:\Users\favou\development\my-email-worker` and exists nowhere else. Copy it, don't clone.

## 5. Commands

Prefer `uv run ...` in a fresh checkout; the local interpreter works too
(`.venv\Scripts\python.exe` on Windows, `.venv/bin/python` on Linux/WSL).

| Command | Purpose |
|---|---|
| `uv run jobagent --version` | print version |
| `uv run jobagent doctor` | **the G0 acceptance gate**: run all environment checks |
| `uv run jobagent config show [--json]` | print parsed criteria + proxy posture |
| `uv run jobagent db init` | create/upgrade the SQLite schema |
| `uv run jobagent db status` | schema version + row counts |
| `uv run pytest -q` | run the test suite (`tests/`) |
| `uv run python scripts/lint_criteria.py` | run the pinned title battery against `config/criteria.yaml` (exit 1 on mismatch) |
| `uv run patchright install chromium` | install the browser Patchright drives |

`jobagent` subcommands `tenants`, `discover`, `match`, `tailor`, `apply`, `onboard`,
`run`, `report` are **labelled stubs** that exit 2 and name the stage that will build them.
Replace a stub only when you implement that stage.

**Exit codes:** `doctor` -> 1 if any check FAILs (WARN never fails); stub -> 2;
`db status` -> 1 when there is no database yet.

## 6. Definition of done (checklist)

Before you call a change finished:

- [ ] `uv run pytest -q` passes (add/extend tests for new behaviour — see §7).
- [ ] `uv run jobagent doctor` is green (or WARNs are explained and unrelated to your change).
- [ ] `uv run jobagent db init` succeeds if you touched `db.py`/schema (bump `SCHEMA_VERSION`).
- [ ] `uv run python scripts/lint_criteria.py` passes if you touched `config/criteria.yaml`.
- [ ] New/changed public classes, functions, CLI commands, config keys, or HTTP calls are
      documented in `docs/design.md`; structural/layering changes are documented in
      `docs/architecture.md` (**§8 — mandatory**).
- [ ] If this change completes a stage/gate or satisfies an acceptance criterion, the **plan docs
      are updated in the same MR** — milestone status flipped, gate recorded, metrics measured,
      `[?]` -> `[V]` (**§8.1 — mandatory**).
- [ ] `AGENTS.md` still describes the repo accurately; update it if anything in §1-§7 changed.
- [ ] Line endings are still LF: only `.gitattributes` (`* text=auto eol=lf`) decides them and
      nothing was re-written as CRLF (§7).
- [ ] No secrets, no `state/`, no `artifacts/`, no `.env` staged for commit.

## 7. Testing & code conventions

- Tests live in `tests/`, are `pytest`-style (plain `assert`, `@pytest.mark.parametrize`),
  and are run with `uv run pytest -q`. Add a test module per concern
  (`test_<module>.py`). Test the **real** loaded config
  (`from jobagent.config import load_criteria`), not a hand-rolled copy — the filters battery
  works this way on purpose (a criteria edit that flips a case is caught, not masked).
- Type-annotate public callables; modules open with `from __future__ import annotations`.
- **Line endings are LF everywhere**, on both hosts — `.gitattributes` (`* text=auto eol=lf`)
  is the only authority. Git for Windows ships `core.autocrlf=true`, which materialises a
  checkout as CRLF, so on a fresh Windows checkout run `git config core.autocrlf false`, and if
  a tree is already CRLF re-materialise it once with `git rm --cached -r .` then `git reset --hard`.
- **Never let a host assume the OS.** Probe it (`sys.platform`, `/proc`) behind a helper and
  keep the failure mode a WARN, not an exception: `doctor` must run on Windows *and* WSL
  (`checks.py`, `tests/test_checks.py`).
- Keep modules small and single-purpose, with a short module docstring that names the plan
  section it implements (see `db.py`, `filters.py` for the house style).
- Import package internals relatively (`from .config import ...`); the CLI is the only
  entry point (`jobagent.cli:app`).
- Prefer Pydantic models for anything parsed from YAML/JSON/LLM output so a typo fails
  loudly at startup rather than mid-run.
- Do not add a dependency without a reason: the stack is deliberately small (`pyproject.toml`).

## 8. Documentation duty — update the docs after EVERY change (mandatory)

Documentation is part of the change, not a follow-up. **Before you open a merge request,
every document below that your change touches must be updated in the same branch.** A change
that leaves the docs stale is incomplete and must not be submitted.

| If your change... | Then you MUST update |
|---|---|
| adds/renames/removes a module, layer, protocol, seam, or component; changes a data-flow or dependency direction | **`docs/architecture.md`** |
| adds or changes a public class, function, dataclass, Pydantic model, field, CLI command/flag, exit code, config key, DB table/column, or HTTP call | **`docs/design.md`** |
| changes the schema | `SCHEMA_VERSION` in `db.py` **and** the table/column tables in `docs/design.md` (and the schema lists in `plan-general.md` §10 / `plan-workday.md` §14 if the model changed) |
| changes targeting/visa/answers/tenants/proxies config | the relevant `config/*.yaml` **and** the "Config file contracts" section of `docs/design.md`; if the semantics changed, `plan-general.md` §2 too |
| implements a stage/stub/gate, or meets an acceptance criterion | **the plan docs per §8.1** (milestone/step status, gate record, measured metrics, `[?]` -> `[V]`), **plus** `docs/design.md` (contract status: planned -> implemented), `docs/architecture.md`, and `README.md` "Current state" |
| verifies a `[?]` or changes a fact | `plan-general.md` §14 / `plan-workday.md` §1 (`[?]` -> `[V]`) **and** `docs/design.md` |
| changes build/test/run commands, tooling, or conventions | **`AGENTS.md` §4-§7** and `README.md` if navigation changed |

### 8.1 Closing a stage, gate or acceptance criterion — update the plans

Hitting an acceptance criterion or crossing a gate is a **documentation event**, not only a code
event. `plan-general.md` and `plan-workday.md` are the program's status of record, so when an
implementation satisfies them they must be updated **in the same MR** as the code, with the
evidence. A green test suite alone does not close a gate.

When you complete a stage (`G0`-`G6`), a workstream step (`G*-W*`), or meet an acceptance
criterion, update:

| Update | Where |
|---|---|
| **Milestone/step status** — annotate the acceptance cell as *met*, with the date and the evidence (test output, MR, captured artifact). Never delete the criterion; mark it satisfied. | `plan-general.md` §11; `plan-workday.md` §15 |
| **Gate closure** — record the gate (`H1a`/`H1b`/`H2`) as *passed* and tick its **exit** condition from the gate table. | `plan-general.md` §11.1; `plan-workday.md` "Entry, exit and code map" |
| **Measured metrics vs targets** — replace each target with the measured value (`jobagent report`, the DB, the MR evidence), or state the gap explicitly. | `plan-general.md` §2 (headline + supporting metrics); `plan-workday.md` §2 |
| **`[?]` -> `[V]`** — promote every question the implementation answered, and mirror it into the design contracts. | `plan-general.md` §14; `plan-workday.md` §1 + appendix; `docs/design.md` §14 |
| **New facts, quirks and drift** — verified endpoints, selectors, error envelopes and any newly observed behaviour. | `plan-general.md` §1 / §12; `plan-workday.md` §1 / §16 |
| **Stage router advance** — set the next stage and the gate that opens it. | `README.md` "Current state" |
| **Contract status** — flip *planned* -> *implemented* and correct signatures to match the code. | `docs/design.md`; `docs/architecture.md` if the shape moved |

Rules:

1. **A gate is a promise, not a label.** Mark it *passed* only with demonstrated exit evidence
   in the MR (test output, measured metric, captured artifact) — never because the code exists.
2. **Deviations must be recorded, not hidden.** If the implementation diverges from the plan
   (a different protocol, an extra table, a changed policy), update the plan *and* state the
   deviation and why. Code and plan must not silently disagree.
3. **The conflict rule still applies.** If a workstream doc now contradicts `plan-general.md`,
   `plan-general.md` wins and the loser is fixed (`plan-general.md` "START HERE").
4. **Never mark something met that was not measured.** If a target was not measured, write
   "not measured" — that is honest, and the gap is itself a finding worth a risk row.
5. **Update the criterion where it lives.** Acceptance criteria live in the plan documents only;
   do not restate them in `docs/` — reference them.
6. **Close the loop in the MR description**: name the stage/gate, the acceptance criterion met,
   the measured result, and the plan sections updated.

### 8.2 Rules for updating `AGENTS.md` itself

1. If your change makes any statement in §1-§7 inaccurate, fix that statement in the same MR.
2. If your change adds a new command, gate, doc, or convention, add it to the relevant table/list.
3. If you add a document to the doc set, add a row to §2 (doc map) **and** to the README doc map.
4. Never delete a rule without a reason — supersede it in place and note why.
5. Journal-worthy decisions (a deviation from the plan, a new verified fact, a new risk)
   belong in `plan-general.md` (§12/§14), not only here.

Keeping the docs current is the primary reason this project has a doc set at all
(`plan-general.md` §0.1 is the ownership table; mirror its scope when deciding *where* a
fact belongs). When in doubt: `plan-general.md` owns cross-channel design,
`plan-workday.md` owns Workday-only details, `docs/design.md` owns the concrete code
contracts, `docs/architecture.md` owns the shape of the system.

## 9. Mandatory change workflow — worktree -> branch -> merge request

**Every change is made in a fresh git worktree on a new branch, and is delivered as a merge
request (MR). Do not commit directly to `main`.** Never edit files inside the primary
checkout on `main` to "test something quickly" — spin up a worktree.

### 9.1 Create a worktree on a new branch

Worktrees live **outside** the repo, under `~/development/.worktrees/`
(`C:\Users\favou\development\.worktrees\` on Windows) — **not** as siblings of the repo. `~/development`
holds one directory per project plus `.worktrees`; a directory per branch is exactly the clutter this
rule prevents.

```powershell
cd C:\Users\favou\development\job-agent
git fetch --all --prune                 # if a remote exists
New-Item -ItemType Directory -Force ..\.worktrees | Out-Null         # one-off
git worktree add ../.worktrees/job-agent-<slug> -b <type>/<slug>     # branches from current main
cd ../.worktrees/job-agent-<slug>
uv sync                                 # a worktree has no .venv of its own
Copy-Item ..\job-agent\.env .env        # ...and no .env either: it is gitignored, so `doctor`
                                        # FAILs (`address` unset) until you copy it in
```

POSIX equivalent, when working in a Linux/WSL checkout:
`git worktree add ~/development/.worktrees/job-agent-<slug> -b <type>/<slug>`.

- `<type>` follows the commit convention: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`.
- `<slug>` is short and describes the unit of work, e.g. `g1-workday-cxs-discovery`,
  `fix/title-veto-avp-guard`.
- One worktree = one branch = one MR. Do not bundle unrelated changes.
- From a worktree the sibling mail repo is **two** levels up:
  `../../my-email-worker/AGENT_ONBOARDING.md` (see §2).
- A worktree created under the old `../job-agent-<slug>` convention can be relocated with
  `git worktree move ../job-agent-<slug> ~/development/.worktrees/job-agent-<slug>`.
- When done, remove the worktree: `git worktree remove ~/development/.worktrees/job-agent-<slug>`
  (from the primary checkout, after the branch is merged/pushed).

### 9.2 Do the work

Follow §3 golden rules, §6 definition of done and §8 documentation duty. Commit as you go.

### 9.3 Commit convention

Conventional Commits with a stage/area scope, matching existing history
(`feat(g0): ...`, `docs(criteria): ...`):

```
<type>(<scope>): <imperative summary>

<why: the problem, the plan section or gate it satisfies, and any decision made>
```

Scopes seen in this repo: `g0`, `g1`, `criteria`, `db`, `config`, `cli`, `doctor`, `docs`.
Keep commits focused; the MR description, not the commit body, carries the narrative.

### 9.4 Push the branch and open a merge request

`origin` is configured as **`https://github.com/ScientificX/Applicator.git`** (HTTPS).
SSH is **not** usable from this machine (no key under `~/.ssh`); HTTPS works through the
VS Code git credential helper. Confirm before pushing, and never push to a remote you did not
verify:

```bash
git remote -v
git push -u origin <type>/<slug>
```

**Open the MR/PR against `main`.** `gh` is **not authenticated** in this environment, so
`gh pr create` exits `4` — either authenticate once (`gh auth login`) or open the PR through
the compare URL:

```bash
# GitHub, once `gh auth login` has been run
gh pr create --repo ScientificX/Applicator --base main --head <type>/<slug> \
  --title "<type>(<scope>): <summary>" --body-file <file>

# Or open this link in a browser (base `main` <- your head branch):
#   https://github.com/ScientificX/Applicator/compare/main...<type>/<slug>?expand=1
```

**MR description must include**: the plan section/gate it advances; what changed; the §8
doc-update checklist (ticked); test evidence (`uv run pytest -q`, `uv run jobagent doctor`);
and any `[?]` resolved or new risk added.

If `origin` is ever unset (fresh clone), the human must add it — do not invent a URL. The
repo's default branch is `main`; if GitHub's default `HEAD` is ever left on a feature branch
(it defaults to the first branch pushed into an empty repo), fix it once with
`gh repo edit --default-branch main`.

### 9.5 After the MR

- Do not merge your own MR unless the human explicitly asks.
- Delete the worktree/branch once merged:
  `git worktree remove ~/development/.worktrees/job-agent-<slug>` and `git branch -d <type>/<slug>`
  (or `git push origin --delete <type>/<slug>`).

## 10. Where code goes (module map)

Implemented today: `config.py`, `db.py`, `filters.py`, `checks.py`, `cli.py`.
Planned (see `docs/architecture.md` and `docs/design.md`; do not create modules before the
stage that needs them):

```
src/jobagent/
  config.py      # Settings + YAML models + loaders            (G0, done)
  db.py          # SQLite DDL, migrations, helpers             (G0, done)
  filters.py     # title gate (include/veto)                   (G0, done)
  checks.py      # doctor checks                               (G0, done)
  cli.py         # Typer app: doctor, config, db + stubs       (G0, done)
  discover/      # DiscoverySource + TenantSource impls        (G1)
  match/         # dedupe, filters, visa, scoring              (G2)
  tailor/        # select-and-patch resume + gates             (G2)
  apply/         # ApplyChannel impls, onboarding, questions   (G3/G5)
  mail/          # worker client, attributer, classifier       (G3/G4)
  llm/           # client, tiers, cost                         (G1+)
  channels.py    # deterministic channel-selection policy      (G3)
```

Keep this map, `docs/architecture.md` ("Module layout") and `docs/design.md` in sync.




