# job-agent

Autonomous, multi-channel job-application agent. Discovers postings matching my criteria,
tailors a resume per posting, submits through the best available channel, and measures what
actually gets responses.

## Start here

1. [`plan-general.md`](plan-general.md) - **the program.** Read the START block, begin at G0.
2. [`plan-workday.md`](plan-workday.md) - **workstream W (Workday).** Entered only at gate H1a (G1)
   and H1b (G5). Do not start here.
3. `../my-email-worker/AGENT_ONBOARDING.md` - the mail service contract (inbound API).

## Current state

`PS-5.4` - design closed, nothing scaffolded. Next action: **G0**.

## Rules of the doc set

- `plan-general.md` is authoritative for anything cross-channel. The workstream doc never restates
  shared design, it references it.
- Milestone IDs: `G0-G6` (program) and `G*-W*` (workstream steps). The old `M0-M7` series is retired.
- If two docs disagree, `plan-general.md` wins and the loser gets fixed.
- `[V]` = verified empirically. `[?]` = still to verify, tracked in `plan-general.md` section 14.
- Secrets and private state (`.env`, `state/`, `artifacts/`) are gitignored.
