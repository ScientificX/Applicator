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

`PS-5.4` — **G0 landed and re-verified on the Windows host**: `uv`/Python 3.13 scaffold, typed
config, SQLite schema v1, `doctor`, and the criteria title gate + test battery. The runtime
host is now **native Windows 11** (agent + Chromium natively, and Ollama natively once the WSL
unit is retired so it can take `localhost:11434`); WSL is no longer the platform of record.
`doctor` exits 0 with one WARN (`AGENT_TOKEN` unset), `pytest -q` is 28 passed. Next action:
**G1** (hunt layer) via gate H1a.


## Rules of the doc set

- `plan-general.md` is authoritative for anything cross-channel. The workstream doc never restates
  shared design, it references it.
- Milestone IDs: `G0-G6` (program) and `G*-W*` (workstream steps). The old `M0-M7` series is retired.
- If two docs disagree, `plan-general.md` wins and the loser gets fixed.
- `[V]` = verified empirically. `[?]` = still to verify, tracked in `plan-general.md` section 14.
- Secrets and private state (`.env`, `state/`, `artifacts/`) are gitignored.
