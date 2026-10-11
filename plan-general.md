# job-agent - Generalised Plan (v5)

Umbrella plan for a multi-channel, multi-ATS autonomous job application agent.

- **Project root:** `/home/favou/development/job-agent`
- **Companion docs:** [`plan.md`](plan.md) - Workday channel + `cxs` discovery deep-dive; `../my-email-worker/AGENT_ONBOARDING.md` - mail API contract
- **Status:** design + verified facts. Nothing scaffolded yet. `[V]` = verified this round, `[?]` = to verify.
- **Stack:** Python 3.13 (`uv`) - DeepSeek `deepseek-flash` (cloud) - Ollama (local) - Cloudflare Email Service (send + receive) - Patchright/curl_cffi (web)

## 0. Scope

**In:** posting discovery across many sources - criteria matching - resume tailoring - multi-channel submission (email, ATS API, web form, aggregator) - outbound analytics - identity/IP management - local + cloud LLM.

**Out (v1):** interview prep/scheduling, offer negotiation, mass LinkedIn DM automation.

The pipeline is **hunt -> rank -> tailor -> route -> execute -> track -> learn**. The Workday plan (`plan.md`) is one channel plus the discovery mechanism; this file is the whole system.

## 1. Verified facts (new this round)

### 1.1 Cloudflare Email Service - outbound sending [V]

| Item | Finding |
|---|---|
| Binding | `{"send_email":[{"name":"EMAIL"}]}` -> `env.EMAIL.send({to,from,subject,html?,text?,cc?,bcc?,replyTo?,attachments?,headers?})` -> `{messageId}` |
| **Arbitrary recipients** | **Yes, once a sending domain is onboarded:** "After you onboard a sending domain, you can send to any recipient immediately." Before that, verified destinations only |
| From restriction | **"You can only send from your routing domains"** -> From must be a `toesk.uk` address |
| Content limits | 50 recipients/email (to+cc+bcc combined); subject <= 998 chars; **5 MiB total message size** (25 MiB only to verified destinations); headers <= 16 KB; <= 20 allowlisted non-X custom headers |
| Daily quota | Per-account; "starts conservative and scales up over time based on sending behaviour, deliverability rates and account standing"; increase via the Limit Increase Request Form. **Numeric default is account-specific -> read from the dashboard** `[?]` |
| Error codes | `E_RATE_LIMIT_EXCEEDED`, `E_DAILY_LIMIT_EXCEEDED`, `E_SENDER_DOMAIN_NOT_AVAILABLE`, `E_DELIVERY_FAILED`, `E_CONTENT_TOO_LARGE`, `E_HEADER_NOT_ALLOWED`, `E_HEADER_USE_API_FIELD`, `E_HEADER_*` |
| Bounces / suppression | Suppression list API: 1 entry per address+scope (account-level + per sending domain); bulk import 1,000; 10 import requests/min; "Drop suppressed recipients" toggle (off by default); dedicated "Handle hard bounce emails" guide |
| Outbound observability | Sends from a Worker show as **"dropped"** in the *Email Routing* summary **even when delivered** -> use **Email sending metrics and logs**; **Event subscriptions API** (webhooks) available |
| Other send paths | Email Sending **REST API** (no Worker needed) and **SMTP** |
| Inbound limits | 25 MiB inbound; 200 routing rules/domain; 200 destination addresses/account; **`message.reply()` throws if the incoming mail has >100 References entries** |
| Zone limit | 30 domains per zone (routing + sending combined) |
| Free-plan caveat | Email-handling Workers count toward Workers CPU/memory limits; complex handlers can fail with `EXCEEDED_CPU` -> Paid plan recommended |
| Compliance CF names | CAN-SPAM, GDPR, CASL - "include proper unsubscribe mechanisms", "honor opt-out requests promptly"; framed as required to maintain good standing and deliverability |

### 1.2 Your worker as it stands today [V]

`my-email-worker/src/index.ts` (11.3 KB, TypeScript): `postal-mime` inbound parse -> KV `EMAIL_CACHE`; forwards a copy to `FORWARD_TO`; `HANDLED_ADDRESSES` allow-list (`@toesk.uk`); spam-keyword scoring (`SPAM_KEYWORDS`/`SPAM_THRESHOLD`); Bearer `AGENT_TOKEN` auth on `GET /emails`, `GET /latest`, `GET /email`, `DELETE /email`; CORS; KV key `inbox:<recipient>:<subject-slug>:<ISO>-<uuid>` plus a `latest:` pointer. **No `send_email` binding, no outbound route, no D1.** Tests: vitest + `@cloudflare/vitest-plugin` (`test/index.spec.ts`).

### 1.3 Carried over

Workday `cxs` oracle (200 / 404 `S21` / 422), 20/page, `limit<=20`, no CSRF, `total` caps at 2000 -> use `facets` (see `plan.md`). DeepSeek `deepseek-flash` (1M ctx, text+image, JSON mode verified, prompt-cache counters, $7.54 balance). Hardware: RTX 5060 Ti 16 GB (sm_120 -> Ollama-only), WSL RAM 15.9 GB (raise to 28 GB).

## 2. Objective & funnel

| Stage | What happens |
|---|---|
| **Hunt** | crawl all sources on a schedule, freshness-first |
| **Rank** | dedupe -> hard filters -> visa -> `score = match x freshness x channel-success prior` |
| **Tailor** | select-and-patch resume + anti-exaggeration gate |
| **Route** | pick the best channel for this posting |
| **Execute** | email - ATS API - web form - aggregator easy-apply |
| **Track** | sends, bounces, replies, ATS status mails |
| **Learn** | feed reply rate per channel/company/subject back into Route and Rank |

**Headline metric: reply rate per channel.** Supporting: apps/day, cost/app, time-to-apply after posting, bounce rate < 2%, zero duplicate applications.

## 3. Channels

| Channel | When to use | Speed | Risk | Analytics quality |
|---|---|---|---|---|
| **ATS JSON API** (Greenhouse, Lever, Ashby, Workable, Recruitee) | board exposes an API | 1-3 s | low | portal confirm + status mail |
| **Web form** (browser: Workday, Taleo, iCIMS, custom) | no API | 15-35 s | medium (bot detection) | portal confirm |
| **Email** (via the worker) | a human/`jobs@` address is discoverable, or the posting invites email | ~1 s | medium (deliverability) | **full: send, bounce, reply, outcome** |
| **Aggregator easy-apply** (LinkedIn/Indeed) | req is syndicated with easy-apply | 5-15 s | medium (ToS) | portal only |
| **Referral / outreach** | you identify a hiring manager | ~1 s | low | full |

**Channel-selection policy** (deterministic; the LLM never decides silently):

1. ATS API adapter exists -> **API**
2. else an email address is discoverable and the company allows it -> **email** (optionally *both*, framed honestly: "I have also submitted via your portal")
3. else an aggregator easy-apply URL exists -> **aggregator**
4. else -> **web form**

The decision **and its reason** are persisted on the application row, so every send is auditable.

## 4. Sources (the hunt layer)

| Family | Mechanism | Notes |
|---|---|---|
| Workday | `POST /wday/cxs/{tenant}/{site}/jobs` + oracle | see `plan.md`; shard + slug discovery |
| Greenhouse | `boards-api.greenhouse.io/v1/boards/{t}/jobs?content=true` | documented read API |
| Lever | `api.lever.co/v0/postings/{co}?mode=json` | documented; response carries `applyUrl` |
| Ashby | `api.ashbyhq.com/posting-api/job-board/{org}` | documented |
| Workable | `apply.workable.com/api/v1/widget/accounts/{sub}?details=true` | read |
| Recruitee | `{co}.recruitee.com/api/offers/` | documented |
| SmartRecruiters, Personio, Teamtailor, Breezy, JazzHR | public read endpoints | add on demand |
| Aggregators | JobSpy (LinkedIn/Indeed/Glassdoor/Google/Zip), Adzuna, Jooble, Remotive, RemoteOK, Arbeitnow, HN Who's Hiring | **carry apply URLs -> yields ATS tenants AND email addresses** |
| Company-direct | target-list careers pages + sitemap/RSS | highest precision for named targets |
| Address harvest | posting body/HTML, careers page, aggregator metadata, named recruiter | **published addresses only - never pattern-guess** (bounces poison the sending domain) |

Unified `JobPosting`: `source, source_id, company, tenant/site, title, location, remote, posted_at, jd_text, jd_hash, apply_url, channel_hint, contact_emails[], visa_signals, first_seen, last_seen, active`.

**Freshness engine** - this is the entire point of "hunt": poll targets every 15-60 min; new = first seen in window or posted today; queue with `priority = f(match_score, age)`. Applying within hours of a posting going live is one of the strongest real levers on response rate, so the scheduler is **recency-first** among near-equal matches, not match-first.

Crawl politeness: 1-2 concurrent per host, jitter, conditional requests, negative caching, global politeness budget. **IP rotation lives here** (scraping), never in email (section 8).

## 5. Resume tailoring (shared by every channel)

As `plan.md` section 9, with the anti-exaggeration gate made explicit because you asked for "not overly exaggerated":

- The master profile holds atomic, tagged, metric-bearing bullets. **Nothing enters a resume that is not in the master profile.**
- Permitted operations: **SELECT, REORDER, REPHRASE, EMPHASISE** (mirror the JD's literal keywords), **TRIM**.
- Forbidden: inventing technologies, employers, dates or metrics; upgrading a title or seniority; turning "contributed to" into "led" without source support; inflating team size, scope or scale.
- Gates, in order:
  1. **Numeric provenance** - every number in a rewritten bullet traces to its source bullet.
  2. **Entity provenance** - every proper noun exists somewhere in the master profile.
  3. **Entailment check** - a second model pass asks "is this claim supported by the source bullet?" and rejects on *no*.
  4. **Title/lie check** - no title or seniority upgrade, ever.
  5. **ATS extraction test** - `pdfplumber` must recover name, each company/title and dates in order.
- Any rejected rewrite falls back to the source bullet **verbatim** - never ship nothing, never ship a fabrication.
- Persist `tailor_report.json` per application (what was selected, what changed, why) so drift is auditable and you can sample-audit at will.

## 6. Email channel (the new capability)

### 6.1 Sending infrastructure

- **Sending domain = a subdomain**, e.g. `apply.toesk.uk`, onboarded for Email Sending with SPF, DKIM and DMARC (start `p=none`, monitor, then `p=quarantine` -> `reject`). MTA-STS optional. Using a subdomain protects the apex that receives your routing mail.
- Worker binding: `"send_email": [{ "name": "EMAIL" }]` alongside the existing `EMAIL_CACHE` KV binding.
- **Sending identity: a human-facing From** (e.g. `Favourotiger <favourotiger@toesk.uk>`), NOT a machine alias. Machine aliases (`wd-<tenant>-<nn>@toesk.uk`) remain for ATS account signups only.
- **Reply-To: a per-application alias** `app-<application_id>@toesk.uk`. The catch-all already routes it to the worker, which indexes by recipient - so replies are attributed perfectly **with zero tracking pixels**. This is the analytics backbone.
- **All outbound goes through the worker** (`POST /send`): one egress, one log, one quota, one auth, one correlation table. Do not scatter sends across the REST API and SMTP.

### 6.2 Message design (normal-looking)

- Plain text primary, optional minimal HTML. **No images, no tracking pixels, no link shorteners, no graphic signatures.** One attachment: the tailored PDF, named `Firstname_Lastname_Resume.pdf` (DOCX only if explicitly requested).
- Three short paragraphs, <= 150 words total: (1) the specific role, where you saw it, the req id; (2) two concrete, *verifiable* matches taken from the tailored resume; (3) one line on availability plus a question that invites a reply.
- Subject patterns, varied and role-specific: `Application: Senior Backend Engineer (JR2018784)`, `Java Engineer - Dublin - <Name>`, `Re: <exact posted title>`. Never `URGENT`, `Job Application`, emoji, or ALL CAPS.
- Content varies per send by construction (it is tailored), which is exactly what avoids spam fingerprinting - never send an identical body twice.
- One recipient per message. No cc/bcc. No blasts.
- Greeting: `Hi <Name>,` when a person is known, else `Hi <Team>,` - never `Dear Sir/Madam`.
- Send window: Tue-Thu, 07:00-09:00 **recipient-local**; skip local public holidays.
- Replies land in KV via the catch-all; the agent classifies intent (positive / negative / auto-reply / bounce) and can draft a response for your approval.

### 6.3 Deliverability engineering

The daily quota is reputation currency. Treat it that way:

- **Ramp**: 5 -> 10 -> 20 -> 40/day per sending domain, stepping up only while bounce rate stays ~0 and replies are landing. Cloudflare scales the account quota by behaviour anyway.
- **Caps**: per-company-domain max 1-2/day; global daily cap; and **never** send twice to the same address (local ledger + suppression check before every send).
- **Bounces**: soft -> retry once after 24 h; hard -> suppress permanently, mark the application dead, never retry (`Handle hard bounce emails` + suppression list API).
- **Monitor**: Email sending metrics/logs, Postmaster, a seed-list placement test (send to your own Gmail/Outlook AND check spam folder), and a pre-send spam-score check.
- **Content hygiene**: no URL shorteners, <= 1 link or none, attachments <= 1-2 MB, no "click here".
- **Compliance is also deliverability** (CF names CAN-SPAM / GDPR / CASL): a one-line human opt-out ("If you would prefer I do not follow up, just say so and I will not"), honoured instantly via suppression; minimal personal data retained; deletion on request.

### 6.4 Address discovery

- Harvest from the posting body/HTML, the careers page, aggregator metadata, and named recruiters where published.
- **Never pattern-guess** (`firstname.lastname@bigcorp.com`) - high bounce rates poison the sending domain.
- Optional verification: syntax + MX record + disposable-domain blocklist. **No SMTP probing.**

### 6.5 Correlation & attribution

1. **Primary:** the unique Reply-To alias - the reply's `recipient` identifies the application exactly, using the worker's existing recipient-indexed storage.
2. **Secondary:** `In-Reply-To` / `References` on the reply matched against the outbound log `[?]` (verify whether outbound Email logs expose the platform Message-ID, and whether a custom `Message-ID` header is permitted).
3. **Third:** ATS status mail ("application received", "next steps", rejection) arrives at the *account* alias and is classified and joined to the application.
4. Every stored message carries `application_id`, so attribution never depends on parsing body text.

## 7. What actually prevents auto-rejection (honest version)

Email does **not** "bypass" an ATS - plenty of companies pipe `jobs@` straight into the same ATS, and some ignore it entirely. The real levers, ordered by impact:

1. **Speed.** Applying the day a req posts beats a perfect resume three weeks later. (This is why Hunt exists.)
2. **Requirement coverage.** ATS ranking is largely lexical; the Tailor step mirrors the JD's literal terms.
3. **Parseable completeness.** Correct file type, single column, standard headings, no tables - enforced by the `pdfplumber` gate.
4. **Reaching a human.** The email channel's genuine value: no form-parsing loss, a person actually reads the first two sentences, and it works where no ATS exists (agencies, small firms).
5. **Referral / known name.** Highest conversion; overlaps the email channel.
6. **Not looking automated.** One recipient, varied content, human-looking address, sane frequency, no tracking pixels.

Therefore: use email where a human address exists or the posting invites it; use the portal otherwise; send both only when you can frame it honestly ("I have also submitted via your portal").

## 8. Identity & IP rotation - what applies where

| Concern | Email channel | Web / HTTP channels |
|---|---|---|
| **IP rotation** | **No. Never rotate.** Cloudflare owns the sending IPs and DKIM alignment; rotating breaks reputation | **Yes** - sticky residential/ISP proxy per identity |
| Identity | 1 human-facing sender + N per-application reply-to aliases | N account identities (per ATS/tenant) |
| Volume control | reputation-gated ramp + per-domain caps | per-tenant pacing + per-identity daily caps |
| Ban response | watch bounce/complaint rate; pause, re-warm, request quota | rotate identity, cooldown, quarantine tenant, requeue unsubmitted work |

"IP rotation" was a Workday-plan feature for **browser sessions**. Applying it to email would be actively harmful - it is the single most common way to get a sending domain blackholed.

## 9. Worker service extensions (concrete)

**Phase 1 - email channel MVP** (edit `my-email-worker/src/index.ts`):

- Add the `send_email` binding to `wrangler.jsonc`.
- `POST /send` -> `{ to, from?, subject, text, html?, replyTo, attachments[{filename,type,content(base64)}], applicationId, headers? }` -> `env.EMAIL.send(...)` -> return `{ messageId }`. Persist `out:<messageId>`, `replyal:<app-<id>>`, and `quota:<YYYY-MM-DD>`.
- Pre-send guard: suppression check, per-domain daily cap, global daily cap, duplicate-recipient check. Reject with a clear error rather than burning quota.
- `GET /stats?since=` -> sends / bounces / replies by day, channel and company.
- `POST /suppress` + `GET /suppressions` (wrap the CF suppression list API); call the check automatically inside `/send`.
- Extend `test/index.spec.ts` (vitest) for every new route; keep `AGENT_TOKEN` auth and CORS as they are.

**Phase 2 - analytics**:

- Enable Email sending metrics/logs and subscribe to outbound events (delivered / bounced / complained) via the **Event subscriptions API**; store them and expose `GET /events`.
- Reply classification + `GET /threads?application_id=` so a whole application thread is retrievable.
- Optional **D1** binding: exact quota accounting and queryable history (KV counters are racy and list-only; KV stays for hot lookups).

**Phase 3 - dashboard**: `GET /stats` JSON consumed by `jobagent report`, or a small static page on the same worker.

> Gotcha to remember: Worker-sent mail appears as **dropped** in the Email Routing summary even when delivered - always read the Email **sending** metrics/logs for outbound truth.

## 10. Data model (agent side, generalised)

Supersedes `plan.md` section 14 where they differ:

- `sources(id, family, config, last_polled)`
- `companies(id, name, domain, ats_family, tenant, site, email_domains, allow_email_channel)`
- `postings(id, source, source_id, company_id, title, location, remote, posted_at, jd_text, jd_hash, apply_url, channel_hint, contact_emails, visa_signals, first_seen, last_seen, active)`
- `matches(posting_id, score_match, score_fresh, score_prior, decision, visa_decision, reason)`
- `resumes(id, posting_id, profile_version, path, tailor_report, created_at)`
- `messages(id, application_id, direction, alias, from_addr, to_addr, subject, rfc_message_id, cf_message_id, status, error_code, sent_at, received_at)`
- `applications(id, posting_id, channel, identity_id, status, attempt, external_id, submitted_at, artifact_dir, UNIQUE(posting_id))`
- `identities(id, kind, label, alias, proxy_ref, status, daily_cap, fail_streak)` where `kind in (email_sender, reply_alias, web_account)`
- `events(id, application_id, kind, payload, ts)`
- `experiments(id, name, variant_a, variant_b, metric)`
- Plus `suppressions(address, reason, added_at)` mirrored from the worker.

## 11. Milestones

| # | Scope | Acceptance |
|---|---|---|
| **G0** | Repo scaffold (uv, py3.13), config, migrations, CLI, `doctor`; `.wslconfig` 28 GB; Ollama tuned + `bge-m3` | `doctor` green |
| **G1** | **Sending domain + worker outbound**: subdomain onboarding, SPF/DKIM/DMARC, `send_email` binding, `POST /send`, vitest coverage, seed-list placement test | a real email to your Gmail lands in **inbox** (not spam); `/send` returns and logs a `messageId`; quota counter increments |
| **G2** | **Hunt layer**: 3 non-Workday ATS families + 1 aggregator, freshness engine, unified posting model, dedupe | new postings detected <= 60 min after posting; cross-source dedupe clean |
| **G3** | **Rank + tailor**: filters, visa, freshness weighting, select-and-patch resume, anti-exaggeration gates | 50-sample audit shows zero fabrications; provenance + entailment gates pass; `tailor_report.json` written |
| **G4** | **Email channel end-to-end**: address discovery, message builder, send via worker, suppression/bounce handling, reply attribution | 5 real applications sent; 0 bounces; every reply attributed to the right application by alias |
| **G5** | **Workday channel** (`plan.md` M1a-M6) as the web/ATS-API reference | see `plan.md` acceptance rows |
| **G6** | **Analytics**: outbound events, reply classification, per-channel funnel, subject A/B | reply-rate report per channel / company / subject variant |
| **G7** | **Autonomy + hardening**: scheduler caps, canary, cost report, runbook, prune/backup | 7-day unattended soak; quota never exceeded; zero duplicate applications |

## 12. Risks -> mitigations

| Risk | Mitigation |
|---|---|
| Sending-domain reputation damage | subdomain isolation, ramp, per-domain caps, no pixels, suppression, metrics/Postmaster watch |
| Daily quota lower than hoped | reputation ramp + Limit Increase Request Form; overflow routes to other channels instead of queueing forever |
| Recruiter perceives cold mail as spam | personalise, low volume, honest framing, instant opt-out, at most **one** nudge after 7-10 days |
| Bounce list poisoning | published addresses only, MX check, hard-bounce suppression, no pattern guessing |
| GDPR / CAN-SPAM exposure (you are in IE) | legitimate-interest outreach, minimal data, opt-out honoured instantly, deletion support, short retention |
| Expectation risk: email does not bypass an ATS | the honest ladder in section 7 + measure reply rate per channel and re-weight `score_prior` from real data |
| Workers Free-plan CPU limits kill email handlers | Workers Paid; keep handlers thin; heavy parsing stays in the agent |
| Two-repo confusion (Python agent vs TS worker) | worker stays in `my-email-worker`; contract documented in `AGENT_ONBOARDING.md`; agent talks HTTP only |
| Double/embarrassing duplicate application | `UNIQUE(posting_id)`, per-address ledger, suppression + duplicate checks pre-send |

## 13. Human actions

1. **Onboard a sending subdomain** (e.g. `apply.toesk.uk`) with SPF/DKIM/DMARC in Cloudflare, and confirm the account's daily sending quota.
2. Confirm the **catch-all** routes `app-<id>@toesk.uk` to the worker (required for reply attribution).
3. Give me the **human-facing From** (display name + address) to use as the sender identity.
4. Confirm **Workers Paid** (Free-plan CPU limits can drop email handlers).
5. **Proxy creds** for the web channels - email needs none.
6. Decide the **nudge policy** (one follow-up after 7-10 days: yes/no).
7. Optional but useful: an initial **target company list** to seed G2/G4.

## 14. Appendix - verified vs assumed

**Verified this round:** CF email sending capability, "any recipient after onboarding a sending domain", from-domain restriction, all content limits, quota behaviour, error codes, suppression API, the "dropped in routing summary" gotcha, Event subscriptions API, REST + SMTP alternatives, inbound limits, your worker's current shape and test setup.

**Assumed / to verify:**
- The account's **numeric** daily sending quota (dashboard-specific).
- Whether outbound **Email logs expose the Message-ID** (secondary correlation path).
- Whether a custom `Message-ID` header is permitted (`E_HEADER_NOT_ALLOWED` / `E_HEADER_USE_API_FIELD` suggest it may not be) - hence the per-application Reply-To alias is the primary attribution path.
- How permissive `X-` custom headers are (limits mention "allowlisted non-X custom headers", implying X- is freer).
- That replies to `app-<id>@toesk.uk` reliably reach the worker (catch-all dependent).
- Non-Workday ATS apply endpoints (Greenhouse/Ashby apply POSTs are undocumented internals - same pattern as Workday: verify by HAR).
