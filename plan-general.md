# job-agent - Generalised Plan (v5.1)

Umbrella plan for a multi-channel, multi-ATS autonomous job application agent.

- **Project root:** `/home/favou/development/job-agent`
- **Companion docs:** [`plan.md`](plan.md) - Workday channel + `cxs` discovery deep-dive; `../my-email-worker/AGENT_ONBOARDING.md` - mail service contract
- **Status:** design + verified facts. Nothing scaffolded yet. `[V]` = verified, `[?]` = to verify.
- **Stack:** Python 3.13 (`uv`) - DeepSeek `deepseek-flash` (cloud) - Ollama (local) - Patchright / curl_cffi (web apply) - Cloudflare Email Service (**inbound only, for now**)

> **Revision note (v5.1).** v5 wrongly promoted *outbound* email to a submission channel. The confirmed
> reading of "send an application in using the worker email service" is that the worker supplies the
> **email address used *on* applications** (normal-looking, so ATS spam/fraud heuristics do not flag it)
> and the **inbound feed** for analytics. Applying *by* email is deferred to **Appendix B** - an option,
> not a milestone.

## 0. Scope

**In (now):** posting discovery across many sources - criteria matching - resume tailoring - submission through web-form / ATS-API / aggregator channels - **worker-managed email identity** (the address on every application) - inbound analytics - identity & IP management - local + cloud LLM.

**Out (v1):** applying *by* email as a submission channel (Appendix B), interview prep/scheduling, offer negotiation, mass LinkedIn DM automation.

Pipeline: **hunt -> rank -> tailor -> route -> execute -> track -> learn**. `plan.md` is the deep-dive for one channel (Workday web-form) plus the `cxs` tenant-discovery mechanism; this file is the whole system.

## 1. Verified facts

### 1.1 Mail service - inbound (what we use now) [V]

Your `my-email-worker` (`src/index.ts`, 11.3 KB TS, deployed at `my-email-worker.flso.workers.dev`):

- `postal-mime` inbound parse -> KV `EMAIL_CACHE`; forwards a copy to `FORWARD_TO` (Gmail); `HANDLED_ADDRESSES` allow-list (`@toesk.uk`); spam-keyword scoring (`SPAM_KEYWORDS`/`SPAM_THRESHOLD`).
- Bearer `AGENT_TOKEN` auth on `GET /emails`, `GET /latest`, `GET /email`, `DELETE /email`; CORS enabled; `OPTIONS` -> 204.
- KV key `inbox:<recipient>:<subject-slug>:<ISO>-<uuid>` plus a `latest:` pointer. **Storage is indexed by recipient**, and the `<subject-slug>` (lowercase, hyphenated, <=48-char subject snippet) means a message can be identified **from its key alone, without reading the body** - that is the attribution handle (section 6.2).
- `GET /emails` returns keys + metadata only, and the metadata carries the **full** `subject`, `from` and `receivedAt` - enough to attribute most ATS mail without ever reading a body.
- `401` missing/bogus bearer, `400` missing param, `404` not found, `500` KV error. Round trip ~50-90 ms.
- Vitest + `@cloudflare/vitest-plugin` (`test/index.spec.ts`); `wrangler.jsonc` has `addresses: ["nimagio@toesk.uk"]` and `HANDLED_ADDRESSES: "@toesk.uk"`.
- **No `send_email` binding, no outbound route, no D1.** Good - nothing to undo.

Inbound limits that matter: 25 MiB message size; 200 routing rules per domain; 200 destination addresses per account; **`message.reply()` throws if an incoming mail has >100 References entries**. A **catch-all** rule is required for arbitrary aliases to reach the worker - **confirmed in place**.

### 1.2 Cloudflare outbound sending (deferred - Appendix B) [V]

Researched, verified, and **not needed for v1**. Kept because it is a real future option: `send_email` binding -> `env.EMAIL.send(...)` -> `{messageId}`; **arbitrary recipients once a sending domain is onboarded**; "you can only send from your routing domains"; 50 recipients/msg; 5 MiB message size; reputation-scaled per-account daily quota; suppression-list API; hard-bounce handling; **Worker sends appear as "dropped" in the Email Routing summary even when delivered** (use Email *sending* metrics/logs). Full detail in Appendix B.

### 1.3 Workday `cxs` (see `plan.md`) [V]

`POST /wday/cxs/{tenant}/{site}/jobs`, `limit <= 20`, no auth/CSRF, response `{total, jobPostings[], facets[]}`; `total` caps at 2000 so size tenants by `facets`. Discovery oracle: **200** valid / **404 `S21`** = tenant real but site slug wrong / **422** = no such tenant. `*.wdN` wildcard resolves for any slug (DNS is not a validity test). Root `/` returns 406.

### 1.4 LLM + hardware [V]

`deepseek-flash` (1M ctx, text+**image**, JSON mode verified, prompt-cache counters exposed, $7.54 balance), `deepseek-v4-pro` for escalation. Local: Ollama 0.16.1, `qwen3:8b` present, `bge-m3` to pull; RTX 5060 Ti 16 GB (sm_120 -> Ollama-only for GPU), WSL RAM 15.9 GB (raise to 28 GB).

## 2. Objective & funnel

| Stage | What happens |
|---|---|
| **Hunt** | crawl all sources on a schedule, freshness-first |
| **Rank** | dedupe -> hard filters -> visa -> `score = match x freshness x source-response prior` |
| **Tailor** | select-and-patch resume + anti-exaggeration gate |
| **Route** | choose the submission channel for this posting |
| **Execute** | ATS JSON API - web form - aggregator easy-apply |
| **Track** | ATS status mail + human replies arriving at the worker |
| **Learn** | feed response rate per channel/company/source back into Route and Rank |

**Headline metric: response rate per channel and per source** - measured from inbound ATS mail, not guessed. Supporting: apps/day, cost/app, time-to-apply after posting, zero duplicate applications, attribution accuracy and unattributed rate.

## 3. Channels

| Channel | When to use | Speed | Risk | Analytics quality |
|---|---|---|---|---|
| **ATS JSON API** (Greenhouse, Lever, Ashby, Workable, Recruitee) | board exposes an API | 1-3 s | low | portal confirm + status mail |
| **Web form** (browser: Workday, Taleo, iCIMS, custom) | no API available | 15-35 s | medium (bot detection) | portal confirm + status mail |
| **Aggregator easy-apply** (LinkedIn/Indeed) | req is syndicated with easy-apply | 5-15 s | medium (ToS) | portal only |
| **Referral / outreach** | you identify a hiring manager | manual | low | best (human reply) |
| ~~Email submission~~ | **deferred - Appendix B** | - | - | - |

**Channel-selection policy** (deterministic; the LLM never decides silently):

1. ATS API adapter exists -> **API**
2. else an aggregator easy-apply URL exists -> **aggregator**
3. else -> **web form**

Email is *not* in the policy. The decision and its reason are persisted on the application row.

## 4. Sources (the hunt layer)

| Family | Mechanism | Notes |
|---|---|---|
| Workday | `POST /wday/cxs/{tenant}/{site}/jobs` + oracle | see `plan.md`; shard + slug discovery |
| Greenhouse | `boards-api.greenhouse.io/v1/boards/{t}/jobs?content=true` | documented read API |
| Lever | `api.lever.co/v0/postings/{co}?mode=json` | documented; carries `applyUrl` |
| Ashby | `api.ashbyhq.com/posting-api/job-board/{org}` | documented |
| Workable | `apply.workable.com/api/v1/widget/accounts/{sub}?details=true` | read |
| Recruitee | `{co}.recruitee.com/api/offers/` | documented |
| SmartRecruiters, Personio, Teamtailor, Breezy, JazzHR | public read endpoints | add on demand |
| Aggregators | JobSpy (LinkedIn/Indeed/Glassdoor/Google/Zip), Adzuna, Jooble, Remotive, RemoteOK, Arbeitnow, HN Who's Hiring | carry apply URLs -> reveals the ATS family + tenant |
| Company-direct | target-list careers pages + sitemap/RSS | highest precision for named targets |
| Contact extraction | recruiter/hiring-manager names + addresses from postings, careers pages, aggregator metadata | used for referral outreach now, and for the deferred email channel (Appendix B) |

Unified `JobPosting`: `source, source_id, company, tenant/site, title, location, remote, posted_at, jd_text, jd_hash, apply_url, channel_hint, contact_emails[], visa_signals, first_seen, last_seen, active`.

**Freshness engine** - the point of "hunt": poll targets every 15-60 min; new = first seen in window or posted today; queue with `priority = f(match_score, age)`. Applying within hours of a posting going live is one of the strongest real levers on response rate, so the scheduler is **recency-first** among near-equal matches.

Crawl politeness: 1-2 concurrent per host, jitter, conditional requests, negative caching, global politeness budget. **IP rotation belongs here** (section 8), never on the mail identity.

## 5. Resume tailoring (shared by every channel)

As `plan.md` section 9, with the anti-exaggeration gate made explicit:

- The master profile holds atomic, tagged, metric-bearing bullets. **Nothing enters a resume that is not in the master profile.**
- Permitted operations: **SELECT, REORDER, REPHRASE, EMPHASISE** (mirror the JD's literal keywords), **TRIM**.
- Forbidden: inventing technologies, employers, dates or metrics; upgrading a title or seniority; turning "contributed to" into "led" without source support; inflating team size, scope or scale.
- Gates, in order: (1) **numeric provenance** - every number traces to its source bullet; (2) **entity provenance** - every proper noun exists in the master profile; (3) **entailment check** - a second model pass asks "is this supported by the source bullet?" and rejects on *no*; (4) **title/lie check**; (5) **ATS extraction test** - `pdfplumber` recovers name, each company/title and dates in order.
- Any rejected rewrite falls back to the source bullet **verbatim** - never ship nothing, never ship a fabrication.
- Persist `tailor_report.json` per application (selected / changed / why) so drift is auditable.

## 6. Email identity layer (what the worker is for today)

The worker is **not** a submission channel in v1. It does three jobs:

1. **Supply the address on every application** - a normal-looking, deliverable address instead of a throwaway/`+alias` that trips ATS spam and fraud heuristics.
2. **Receive and index everything the ATS sends back** - "application received", status changes, rejections, interview invites, assessment links.
3. **Account verification** - signup codes/links for ATS accounts (Workday and others), read by the agent.

### 6.1 Addressing: stable addresses, content-based attribution

**Decision: attribution does NOT come from unique email addresses.** Addresses exist for deliverability and account identity; attribution comes from the **content of the mail**, chiefly the subject slug already embedded in the KV key.

| Address | Used for | Notes |
|---|---|---|
| `favourotiger@toesk.uk` (one canonical human-facing address, plus at most 1-2 variants) | the email field on every application and the resume header | normal-looking, no `+` tags, same address everywhere so the identity stays consistent |
| `wd-<tenant>-<nn>@toesk.uk` | **only** where an ATS keys an account on the email address (Workday tenants) | a distinct login identity per tenant; never used for attribution; never rotated (it is the login + password-reset path) |
| `nimagio@toesk.uk` | the address currently routed in `wrangler.jsonc` | already wired |

Why no per-application addresses: a recruiter-visible `app-8f3c@...` looks like a burner (its own rejection risk), and uniqueness buys nothing once the subject key does the work. Uniform, stable addressing also keeps the identity consistent across resume, form and ATS account - which is what fraud heuristics actually look at.

Rules: **never `+`-tagged** (some Workday validators reject plus-addressing); rotate an address only when it is burned, and keep history so older accounts stay recoverable.

### 6.2 Attribution from the KV key (the mechanism)

`inbox:<recipient>:<subject-slug>:<ISO>-<uuid>` - the `<subject-slug>` is a lowercase, hyphenated, **<=48-char** snippet of the subject. ATS subjects routinely name the company and the role ("Acme Corp - Senior Java Engineer: Application Received"), so the slug is a ready-made attribution handle, and **`GET /emails` returns keys + metadata without reading bodies**.

Algorithm, cheapest signal first:

1. **List** `GET /emails?recipient=<address>` -> keys + metadata (`from`, **full** `subject`, `receivedAt`). Sort client-side by `receivedAt`; never assume KV list order is chronological.
2. **`from` domain -> company/ATS**: `*.myworkdayjobs.com` yields the tenant directly (`nvidia.wd5...`); `*.greenhouse.io`, `hire.lever.co`, `*.ashbyhq.com` identify the family; the token in `from` or the body links identifies the company. This alone usually narrows to one company.
3. **Subject slug + full subject -> role/req id**: extract an explicit req id (`JR2018784`, `REQ-12345`, `#123456`) or match the role title against that company open applications.
4. **Join** to `applications` on `(company, role|req_id)`, constrained by `submitted_at <= receivedAt` within a window; accept when a single candidate remains, record `match_confidence`, and queue ambiguous cases for review instead of guessing.
5. **Body read only on failure**: `GET /email?key=` -> parse the role/req/company out of the text. Bodies are read lazily; most attribution never needs them.

Verification mail is the one case where the body **is** always read: the signup flow needs the code or link (see `plan.md` onboarding state machine).

**Known limits, designed around:** the slug is truncated at 48 chars, so a verbose subject can clip the role - hence the full subject from metadata, then the body, are the fallbacks. And an ATS that sends a bare "Thank you for your application" (no company or role in subject *or* body) is attributable only by `from` domain + time window; those land as `match_confidence: low` and count toward the `unattributed` metric rather than being silently forced onto a wrong application.

### 6.3 Analytics & client etiquette

- Every mail is classified (`ack`, `status_change`, `rejection`, `interview`, `assessment`, `verification`, `other`) and joined as above -> **response rate per channel / company / source**, the number that drives Route and Rank.
- Hard bounces of our address from an ATS host -> flag it, stop using that address for that ATS.
- Etiquette (non-negotiable, from `AGENT_ONBOARDING.md`): URL-encode `recipient`/`key`; `DELETE` after read (read-once); `drain()` before a flow; reject anything with `receivedAt < flow_start`; idempotency on `message_id`; use `/latest` only when the single newest message is genuinely what you want - otherwise list and sort by `receivedAt`.
## 7. What actually prevents auto-rejection (honest version)

The levers, ordered by impact:

1. **Speed.** Applying the day a req posts beats a perfect resume three weeks later. (This is why Hunt exists.)
2. **Requirement coverage.** ATS ranking is largely lexical; Tailor mirrors the JD's literal terms.
3. **Parseable completeness.** Correct file type, single column, standard headings, no tables - enforced by the `pdfplumber` gate.
4. **A credible contact address.** A throwaway or `+alias` address is a fraud signal; a real, normal-looking address on a real domain is not. (Section 6.)
5. **Reaching a human.** Referral/outreach mail converts best; overlaps the deferred email channel.
6. **Not looking automated.** One application per req, no duplicate submissions, sane timing, consistent identity across resume/address/profile.

Nothing here requires sending mail. The email channel is a *tactic*, not a prerequisite.

## 8. Identity, IP rotation & pacing

| Concern | Mail identity (worker) | Web / HTTP channels |
|---|---|---|
| Rotation | **Never rotate** - it is a login identity and a reply path, not a send reputation | **Yes** - sticky residential/ISP proxy per identity |
| Volume | n/a (receive-only) | per-tenant pacing + per-identity daily caps |
| Failure response | alias burned -> mint a new one, keep history | rotate identity, cooldown, quarantine tenant, requeue unsubmitted work |
| Consistency | the same name/address/phone must appear on every resume, form and account | same |

**Consistency is the real anti-fraud signal.** The fastest way to look fake is a mismatch between the resume header, the application email field, and the ATS account. One canonical identity, many aliases.

## 9. Worker service extensions (revised - inbound-first)

**Good news: v1 needs almost no worker changes.** The existing API (list/read/latest/delete by recipient, KV-indexed by recipient, Bearer auth) already covers alias attribution and verification. Work items are small:

- **Agent-side:** alias registry (pool, tier, purpose, burn state); per-`receivedAt` staleness handling; `message_id` idempotency; classification of ATS mail.
- **Worker, phase 1:** keep as-is. Only add `GET /stats?since=` (counts by alias/kind/day) and a retention policy note - KV entries never expire on their own, so the agent must prune as it reads.
- **Worker, phase 2 (when volume grows):** optional **D1** binding for queryable history + exact counters (KV is list-only and eventually consistent); outbound-event plumbing is *not* needed yet.
- **Worker, phase 3:** dashboard/`/stats` JSON consumed by `jobagent report`.
- Any new route: keep `AGENT_TOKEN` auth + CORS, extend `test/index.spec.ts`.

## 10. Data model (agent side)

Supersedes `plan.md` section 14 where they differ:

- `sources(id, family, config, last_polled)`
- `companies(id, name, domain, ats_family, tenant, site, allow_email_tier)`
- `postings(id, source, source_id, company_id, title, location, remote, posted_at, jd_text, jd_hash, apply_url, channel_hint, contact_emails, visa_signals, first_seen, last_seen, active)`
- `matches(posting_id, score_match, score_fresh, score_prior, decision, visa_decision, reason)`
- `resumes(id, posting_id, profile_version, path, tailor_report, created_at)`
- `aliases(id, address, role, ats_family, status, created_at, burned_at)` where `role in (contact, ats_account)` - addresses serve deliverability and account identity, **never attribution**
- `applications(id, posting_id, channel, address_id, web_identity_id, status, attempt, external_id, submitted_at, artifact_dir, UNIQUE(posting_id))`
- `messages(id, application_id, match_confidence, direction, recipient, from_addr, from_domain, subject, subject_slug, rfc_message_id, kind, kind_confidence, received_at, raw_key)`
- `web_identities(id, ats_family, label, proxy_ref, status, daily_cap, fail_streak, profile_dir)`
- `events(id, application_id, kind, payload, ts)`
- `suppressions(address, reason, added_at)`

## 11. Milestones

| # | Scope | Acceptance |
|---|---|---|
| **G0** | Repo scaffold (uv, py3.13), config, migrations, CLI, `doctor`; `.wslconfig` 28 GB; Ollama tuned + `bge-m3` | `doctor` green |
| **G1** | **Hunt layer**: 3 non-Workday ATS families + 1 aggregator, freshness engine, unified posting model, cross-source dedupe | new postings detected <= 60 min after posting; dedupe clean |
| **G2** | **Rank + tailor**: filters, visa, freshness weighting, select-and-patch resume, anti-exaggeration gates | 50-sample audit shows zero fabrications; provenance + entailment gates pass; `tailor_report.json` written |
| **G3** | **Submission with worker addresses** (non-Workday ATS first): contact address on the form, ATS account signup + verification through the worker, inbound ack attributed back to the application | 5 real applications submitted; every inbound ack attributed with `match_confidence >= high`; unattributed count reported |
| **G4** | **Inbound analytics**: subject-key attribution, classification, response-rate report per channel/company/source | report matches a manual spot-check of 20 applications; unattributed rate < 10% |
| **G5** | **Workday channel** (`plan.md` M1a-M6) as the web-form reference | see `plan.md` acceptance rows |
| **G6** | **Autonomy + hardening**: scheduler caps, identity rotation, canary, cost report, prune/backup, runbook | 7-day unattended soak; zero duplicate applications; attribution accuracy >= 95% |
| *(deferred)* | *Email as a submission channel* | *Appendix B - not scheduled* |

## 12. Risks -> mitigations

| Risk | Mitigation |
|---|---|
| Burner-looking address -> ATS rejection | one stable, name-based address used consistently on every application and resume (section 6.1) |
| Attribution fails (vague subject, no req id, company unnamed) | multi-signal join: `from` domain -> company, subject slug + full subject -> role, then time window; record `match_confidence` and report an `unattributed` metric instead of guessing |
| 48-char subject slug clips the role | fall back to the full subject in KV metadata, then the body text; never attribute from a truncated slug alone |
| ATS mail mis-classified | sample audit each week; keep `confidence`; fall back to `other` and queue for review |
| KV fills up (entries never expire) | agent prunes on read; add retention + optional D1 at volume |
| Workday/ATS account bans | per-identity proxies, pacing, cooldown, alias rotation, requeue unsubmitted work |
| Duplicate or embarrassing repeat applications | `UNIQUE(posting_id)`, company+title dedupe, application ledger |
| Expectation risk: nothing here bypasses an ATS | section 7 ladder + measure response rate per channel and re-weight `score_prior` from real data |
| Workers Free-plan CPU limits | Workers Paid; keep handlers thin; heavy parsing stays in the agent |
| Two-repo confusion (Python agent vs TS worker) | worker stays in `my-email-worker`; contract in `AGENT_ONBOARDING.md`; agent talks HTTP only |

## 13. Human actions

1. ~~Confirm the catch-all~~ - **done**: catch-all confirmed on `toesk.uk`.
2. Approve the **canonical contact address** (the one that appears on applications and resumes) - default `favourotiger@toesk.uk`.
3. Confirm **Workers Paid** (Free-plan CPU limits can drop mail handlers).
4. **Proxy creds** for the web channels.
5. Optional but useful: an initial **target company list** to seed G1/G3.
6. *(Only if Appendix B is taken up later)* onboard a sending subdomain with SPF/DKIM/DMARC.

## 14. Appendix A - verified vs assumed

**Verified:** the worker's current shape, routes, auth, KV key format and test setup; the **catch-all** on `toesk.uk` (confirmed); that the key/metadata design supports attribution without reading bodies (`<subject-slug>` in the key, full subject in metadata); inbound limits (25 MiB, 200 rules/domain, 200 destinations/account, >100 References throws on `reply()`); the `cxs` oracle and response schema; DeepSeek models / JSON mode / cache counters / balance; hardware and Ollama state; Cloudflare outbound-sending capability and limits (Appendix B).

**Assumed / to verify:**
- Whether ATS subjects/senders consistently expose company + role - measured by the `unattributed` metric in G4; the design degrades to body reads and time windows rather than guessing (`[?]`).
- How ATS spam heuristics weight aliased vs plain addresses (`[?]` - moot for attribution now; the single stable address removes the variable anyway).
- Non-Workday ATS apply endpoints (Greenhouse/Ashby apply POSTs are undocumented internals, same as Workday - `[?]` verify by HAR).
- Whether ATS status mail consistently includes a req id/title that lets us disambiguate aliases that carry several applications (`[?]` - fall back to `submitted_at` window matching).
- The account's numeric daily **sending** quota (only relevant to Appendix B).

## 15. Appendix B - DEFERRED OPTION: email as a submission channel

Not scheduled. Kept because the capability is verified, real, and occasionally the *only* path (agencies, small firms with no ATS, postings that literally say "email your resume"). Do **not** build this before G1-G4 pay off on the primary channels.

### B.1 What makes it possible [V]

| Item | Finding |
|---|---|
| Binding | `{"send_email":[{"name":"EMAIL"}]}` -> `env.EMAIL.send({to,from,subject,html?,text?,cc?,bcc?,replyTo?,attachments?,headers?})` -> `{messageId}` |
| Arbitrary recipients | **Yes, once a sending domain is onboarded** - "After you onboard a sending domain, you can send to any recipient immediately" (before that, verified destinations only) |
| From restriction | **"You can only send from your routing domains"** |
| Content limits | 50 recipients/msg (to+cc+bcc); subject <= 998 chars; **5 MiB** message size (25 MiB to verified destinations); headers <= 16 KB; <= 20 allowlisted non-X custom headers |
| Daily quota | Per-account, "starts conservative and scales with sending behaviour, deliverability rates and account standing"; raise via the Limit Increase Request Form |
| Error codes | `E_RATE_LIMIT_EXCEEDED`, `E_DAILY_LIMIT_EXCEEDED`, `E_SENDER_DOMAIN_NOT_AVAILABLE`, `E_DELIVERY_FAILED`, `E_CONTENT_TOO_LARGE`, `E_HEADER_NOT_ALLOWED`, `E_HEADER_USE_API_FIELD` |
| Bounces | Suppression list API (1 entry per address+scope; bulk import 1,000; 10 imports/min); "Drop suppressed recipients" toggle (off by default); dedicated hard-bounce guide |
| Observability | Worker sends show as **"dropped"** in the Email Routing summary **even when delivered** -> use Email *sending* metrics/logs; **Event subscriptions API** (webhooks) exists |
| Alternatives | Email Sending REST API (no Worker needed) and SMTP |

### B.2 What it would require

- A **sending subdomain** (e.g. `apply.toesk.uk`) onboarded with SPF/DKIM/DMARC (start `p=none`, then `p=quarantine` -> `reject`), keeping the apex clean for routing.
- A **human-facing From** (name + address), not a machine alias; **Reply-To = the stable contact address** - replies are attributed by subject slug + `from` domain (section 6.2), so no per-application addresses are needed.
- `POST /send` on the worker (single egress, single log, single quota) + pre-send guards (suppression, per-domain cap, duplicate recipient).
- A **reputation ramp** (5 -> 10 -> 20 -> 40/day, stepping up only while bounce rate stays ~0), per-company-domain caps, hard-bounce suppression.
- Message hygiene: <= 150 words, 3 short paragraphs, one tailored PDF attachment, no tracking pixels, no link shorteners, no graphic signatures, varied subjects, one recipient per message, Tue-Thu 07:00-09:00 recipient-local.
- Compliance treated as deliverability: CAN-SPAM / GDPR / CASL per Cloudflare's own docs - one-line human opt-out, honoured instantly via suppression; minimal data; deletion on request.

### B.3 The honest caveat

Email does **not** bypass an ATS. Many companies pipe `jobs@` into the same ATS; some ignore it. What it buys you is a human reading two sentences, no form-parsing loss, and coverage of employers with no ATS at all. If it is ever enabled, it must be measured (reply rate vs the primary channels) and re-weighted from data - not assumed to be better.
