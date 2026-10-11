# job-agent

Autonomous, multi-channel job-application agent. Discovers postings matching my criteria,
tailors a resume per posting, submits through the best available channel, and measures what
actually gets responses.

## Start here

1. [`AGENTS.md`](AGENTS.md) - **working rules for agents** (and humans): commands, conventions,
   the mandatory worktree -> branch -> merge-request flow, and the doc-update duty.
2. [`plan-general.md`](plan-general.md) - **the program.** Read the START block, begin at G0.
3. [`plan-workday.md`](plan-workday.md) - **workstream W (Workday).** Entered only at gate H1a (G1)
   and H1b (G5). Do not start here.
4. [`docs/architecture.md`](docs/architecture.md) - **high-level design**: components, layers,
   seams, data flow.
5. [`docs/design.md`](docs/design.md) - **low-level design**: class/API contracts, DB schema,
   config schemas, external HTTP contracts.
6. `../my-email-worker/AGENT_ONBOARDING.md` - the mail service contract (inbound API).

## Current state

`PS-5.4` — **G0 met**, **G1 partial**, on the **native Windows host**.

- **Host**: Windows 11, native — the agent and Chromium run natively, and Ollama too once the WSL
  unit is retired so it can take `localhost:11434`. WSL is no longer the platform of record.
- **G0 met 2026-10-11** — `uv`/Python 3.13 scaffold, typed config, SQLite schema v1, `doctor`,
  criteria title gate + test battery, re-verified on the Windows host (`plan-general.md` §11).
  `doctor` exits 0 (1 WARN: `AGENT_TOKEN` unset by design); `pytest -q` is **49 passed**.
- **G1 partial 2026-10-11** — hunt layer + Workday `cxs` discovery: **3775 postings stored,
  0 duplicate `(source, source_id)` rows**, Workday postings carrying a req id **320/320**.
  **Gap: 8 live tenants vs the >= 20 target** and site-slug resolution **8/16 = 50% vs >= 80%**,
  so gate **H1a is not passed** (`plan-general.md` §12 carries the mitigation).
- Next action: **close the G1 tenant gap**, then **G2** (rank + tailor).


## Rules of the doc set

- `plan-general.md` is authoritative for anything cross-channel. The workstream doc never restates
  shared design, it references it.
- Milestone IDs: `G0-G6` (program) and `G*-W*` (workstream steps). The old `M0-M7` series is retired.
- If two docs disagree, `plan-general.md` wins and the loser gets fixed.
- `[V]` = verified empirically. `[?]` = still to verify, tracked in `plan-general.md` section 14.
- Secrets and private state (`.env`, `state/`, `artifacts/`) are gitignored.
