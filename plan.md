# job-agent - Implementation Plan (v4)

Autonomous Workday application agent: hybrid `cxs` API discovery + persistent-identity browser apply + Cloudflare mail-worker onboarding.

- **Project root:** `/home/favou/.cline/data/workspaces/chat/job-agent`
- **Status:** design closed. `[V]` = verified empirically during planning. `[?]` = still to verify (needs HAR).
- **Stack:** Python 3.13 (`uv`) - Patchright - curl_cffi - Ollama (local) - DeepSeek `deepseek-flash` (cloud)

## 0. Locked decisions

| Topic | Decision |
|---|---|
| Tenant discovery | **Inverted-first** (harvest Workday apply-URLs from aggregators), verified by the `cxs` oracle. Breadth brute-force + crt.sh are pluggable providers; refactor path in 6.4 |
| Job discovery | `cxs` `searchText` + `appliedFacets` server-side narrowing, 20/page |
| Apply | Patchright, **persistent context per identity**, `data-automation-id` selectors, upload-first ("Autofill with Resume") |
| Identities | **Multiple per tenant**, config-capped. 1 alias + 1 profile dir + 1 sticky proxy each |
| Aliases | `wd-<tenant>-<nn>@toesk.uk`, distinct local parts, **never `+`-tagged** |
| Resume | master `profile.json` -> select-and-patch -> Jinja2 -> `page.pdf()` -> pdfplumber ATS gate |
| Submit policy | **`park` by default**; `--auto-submit` opt-in |
| LLM | `deepseek-flash` primary - local `qwen3:14b`/`qwen3:8b` for classification + failover - Ollama `bge-m3` embeddings |
| Local GPU | RTX 5060 Ti 16 GB (sm_120 -> needs CUDA 12.8+): **Ollama-only** for GPU work |
| Target profile | mid-senior, AVP-ish, Java/backend, banking/fintech |
| Locations | Dublin (tier 1) > London (tier 2) > remote (tier 3) |
| Visa | Ireland: right to work. **Everywhere else: sponsorship required** |
| Comp floor | IE EUR 70k - UK GBP 75k - remote EUR 70k (confirmed) |
| Key rotation | **Not now** (user decision); rotate DeepSeek + `AGENT_TOKEN` after wiring |
| Proxies | Per-identity sticky creds **to be supplied** -> until then `tenant_concurrency_cap = 1` |

## 1. Verified facts

| Area | Finding |
|---|---|
| Workday search | `POST https://{host}/wday/cxs/{tenant}/{site}/jobs`, body `{"appliedFacets":{},"limit":N,"offset":M,"searchText":"..."}`. **limit <= 20. No auth/CSRF needed.** |
| Response | `{total, jobPostings[{title, externalPath, locationsText, postedOn, bulletFields}], facets[{facetParameter, descriptor, values[{descriptor,id,count}]}], userAuthenticated}` |
| `total` | Caps at **2000** (NVIDIA Engineering alone = 1720) -> **size tenants by `facets`, never `total`** |
| Discovery oracle | **200** valid - **404 `{"errorCode":"S21","message":"not found: Job_Posting_Site_ID=X"}`** = tenant real, site slug wrong - **422 `{"errorCode":"HTTP_422"}`** = no such tenant |
| Drift canary | `errorCaseId` is unique per request -> alert if the error envelope changes |
| DNS | `*.wdN.myworkdayjobs.com` wildcard resolves for **any** slug (incl. bogus); `wd11` = NXDOMAIN -> shard set enumerable, tenant existence only via `cxs` |
| Root `/` | 406 with 0 bytes even with a full Chrome UA -> HTML paths need `curl_cffi impersonate="chrome"` |
| Job detail, apply flow | [?] **VERIFY from HAR at M0** - the only remaining unknown |
| DeepSeek | `deepseek-flash` = V4.1-Flash: **1,048,576 ctx**, 393,216 max out, **text+image** input, `effort: low|high|max` (default high). `deepseek-v4-pro` exists (text-only). `deepseek-chat` is an alias -> **pin literal ids** |
| DeepSeek JSON | `response_format={"type":"json_object"}` [V], round trip **0.715 s** |
| Prompt cache | usage exposes `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens` / `prompt_tokens_details.cached_tokens` -> prefix caching is measurable |
| Balance | **$7.54 USD** -> hard budget caps required |
| Model self-report | Unreliable (claimed "gpt-4o-mini"). Trust only the API `model` field |
| Browser | `patchright` = drop-in patched Playwright (pure `py3-none` wheels, 3.10-3.14). `playwright-stealth` **excluded** (breaks `launch_persistent_context`) |
| Hardware | RTX 5060 Ti 16 GB, ~13 GB free, compute 12.0, driver 610.88, `/dev/dxg` OK. **WSL RAM = 15.9 GB of host 32 GB**, no `.wslconfig` |
| Ollama | 0.16.1 installed. `qwen3:8b` present; `glm-4.7-flash` (19 GB) too big for 13 GB free. **No embedding model yet** |
| PDF tooling | **None** (`pdftotext`/`pypdf`/`pdfplumber` absent) -> add `pypdf`, `pdfplumber` |
| Machine | 16 vCPU (Ryzen 7 7700), 913 GB free disk |

## 2. Objective & success criteria

Discover Workday tenants -> discover matching reqs -> rank -> tailor one resume per job -> apply via a persistent per-identity session -> park what it cannot finish.

| Metric | Target |
|---|---|
| Cost per application | < $0.01 |
| Apply time (warm session) | 20-35 s |
| LLM calls per application | <= 2 |
| Submit rate | >= 80% |
| Park rate | < 10%, never blocks other tenants |
| Tenant discovery | >= 20 live tenants, >= 80% correct site slugs, **zero manual entries** |

## 3. Target profile - `config/criteria.yaml`

```yaml
target:
  seniority: [mid, senior]                       # "AVP-ish" IC
  title_include: ['\bAVP\b','Assistant Vice President','Senior Software Engineer',
                  'Backend Engineer','Java Engineer','Software Engineer (III|IV)','Lead Engineer']
  title_exclude: ['Intern','Graduate','Analyst','Associate','Principal','Director','\bVP\b','Manager']
  stack_must: ['Java','Spring','microservices','REST','SQL']
  stack_nice: ['Kafka','Kubernetes','Docker','AWS','Azure','GCP','Terraform','low.?latency',
               'distributed','JUnit','Mockito','CI/CD','observability']
  domain_nice: ['bank','financial services','payments','trading','fintech','capital markets']
  locations:
    preferred: ['Dublin','Ireland']                   # tier 1
    next:      ['London','United Kingdom']            # tier 2
    also:      ['Remote','EMEA Remote','EU Remote']   # tier 3
  comp: { IE_min_eur: 70000, UK_min_gbp: 75000, remote_min_eur: 70000 }
  top_k_per_run: 25
```

## 4. Visa / right-to-work - `config/visa.yaml`, enforced by `match/visa.py`

```yaml
visa:
  right_to_work: { IE: none, default: sponsorship_required }
  jd_filter:
    exclude_if: ['no sponsorship','unable to sponsor','must already have the right to work',
                 'we do not (provide|offer) visa','no visa sponsorship']
    boost_if:   ['visa sponsorship (is )?available','we (do )?sponsor','sponsorship provided',
                 'relocation support']
  answers:                       # country-scoped; seeded into the answer cache pre-LLM
    IE:      { require_sponsorship: 'No',  right_to_work: 'Yes' }
    GB:      { require_sponsorship: 'Yes', right_to_work: 'No' }
    default: { require_sponsorship: 'Yes' }
```

Country is inferred from job location/tenant, so IE and UK answers can never cross-contaminate. Ranking penalty for `exclude_if`, boost for `boost_if`. `matches` stores `visa_decision` for auditability.

## 5. Architecture

```
tenants discover  <- inverted: aggregators -> Workday apply-URLs   (+ pluggable breadth)
   |
jobs discover     <- cxs searchText + appliedFacets (server-side narrow, 20/page)
   |
match             <- dedupe -> hard filters -> visa filter -> BM25 + embeddings -> top-K
   |
tailor            <- JD->requirements (cached) -> bullet select -> 1 rewrite -> validate -> PDF -> ATS gate
   |
apply             <- deep-link -> autofill-with-resume -> a11y diff -> Q&A cache -> review -> submit | park
```

**Stage-split scheduler** (VRAM/RAM guard): `discover/match/tailor` in an Ollama-hot batch, then `apply` browsers-hot with Ollama unloaded (`OLLAMA_KEEP_ALIVE=0`). Stage-based, not per-job - deliberately.

## 6. Tenant discovery (`discover/`)

**6.1 Oracle** - one `limit:1` probe per `(slug, shard, site_guess)`:

| Result | Meaning | Action |
|---|---|---|
| 200 | tenant + site valid | store, harvest `facets` |
| 404 `S21` | **tenant exists**, site slug wrong | brute slug candidates; else browser/aggregator escalation |
| 422 | no such tenant | negative-cache 30 days |

**6.2 Shard discovery** - enumerate `wdN` via DNS once, cache resolvers, learn new shards from observed hosts. Free and definitive.

**6.3 Inverted source (default)** - aggregator/JobSpy results for the criteria -> harvest Workday apply URLs -> yields `host + tenant + site + externalPath` **and** proof of active hiring. Zero blind probing. Use the 200 response's `facets` (locations, `jobFamilyGroup`) to score tenant yield -> onboard identities only where yield justifies an account.

**6.4 Refactor path to both strategies** (documented, deliberate):

```python
class TenantSource(Protocol):
    async def yield_candidates(self, criteria) -> AsyncIterator[TenantCandidate]: ...
```

Adding `BruteForceBreadthSource` (companies x slug variants x live shards, backed by `companies` + `tenant_probes`) or `CrtShCustomDomainSource` later = **one new class + one config entry**. The oracle, schema, and pipeline are unchanged; sources may run in parallel with weights. Inverted-only is today's default; both is a config change tomorrow.

**6.5 Site-slug acquisition order** - (1) aggregator/search URL (exact, free), (2) `curl_cffi impersonate="chrome"` landing fetch ([?] confirm 406 clears), (3) generated candidates (`{Pascal}ExternalCareerSite`, `{Pascal}Careers`, `External`, `Careers`, `{Pascal}External`, `{Pascal}Global`, ...), probe-verified.

## 7. Job discovery (`discover/workday_cxs.py`)

```python
# once per tenant: cache the facet id map
POST {base}/jobs {"appliedFacets":{}, "limit":1, "searchText":""}
# then narrow server-side: 20/page, paginate until short page
POST {base}/jobs {"appliedFacets":{"jobFamilyGroup":["0c40f6bd..."]},
                  "limit":20, "offset":0, "searchText":"java"}
```

Cuts per-tenant cost from ~100 pages to 1-3. `bulletFields[0]` = `JR...` req id -> dedupe key with `externalPath`. Filter `canApply`. Incremental via `last_seen` watermark + `total`/facet drift.

## 8. Matching (`match/`)

1. Dedupe on `(tenant, job_posting_id)`; cross-channel dedupe on `(company, normalized_title, location)` for the route-around-Workday branch.
2. Hard filters: title include/exclude, `stack_must` present, location tier, comp floor, blacklists.
3. **Visa filter** (section 4).
4. Rank: `0.6*cosine(embeddings) + 0.4*BM25` over title + bulletFields + first 2 kB of JD.
5. Emit top-K with a human-readable `reason`. Below-cut jobs are stored but never reach the LLM.

## 9. Tailoring (`tailor/`)

`profile.json` holds **atomic, tagged, metric-bearing bullets**. Per job (<=2 LLM calls):

1. `jd.py` -> structured requirements + `keywords_verbatim`; cached by `jd_hash`.
2. `select.py` -> hybrid retrieval of bullets; constraints: <=6/role, <=2 pages, chronology preserved, >=1 metric-bearing bullet per role.
3. `rewrite.py` -> **one** call: new summary + rewrite only the <=K bullets missing JD keywords. Prefix-cache friendly (profile first, JD last).
4. `validate.py` -> every number must exist in the source bullet; every proper noun must exist in the master profile; page count; hard-skill coverage. Fail -> deterministic fallback to un-rewritten bullets.
5. `render.py` -> Jinja2 -> HTML -> `page.pdf()` (single column, standard headings, selectable text). Optional DOCX A/B.
6. **ATS gate** -> `pdfplumber` must extract name, each company/title, and dates in order; else re-render with the fallback template. *(A pretty PDF that parses badly costs 40 manual corrections - this gate matters more than prompt tuning.)*
7. Cache by `(profile_version, jd_hash, template_version)`.

## 10. LLM layer (`llm/`)

| Role | Model | Why |
|---|---|---|
| JD->requirements, resume rewrite | `deepseek-flash` (`effort: low` for extraction) | verified JSON mode, 0.71 s, 1M ctx, cache counters |
| Hard cases only | `deepseek-v4-pro` | text-only escalation |
| **Vision fallback** for unknown Workday fields | `deepseek-flash` (image input) | replaces a separate vision-model seam |
| Classification, question normalisation, answer-cache misses | local `qwen3:14b` / `qwen3:8b` | thousands of tiny calls; free, no rate limit |
| Offline prompt/schema iteration + tests | local | zero API cost, CI-safe |
| Failover on 429/outage | local `qwen3:14b` | keeps runs alive |
| Embeddings | Ollama `bge-m3` (fallback `nomic-embed-text`, or `fastembed` CPU) | sm_120 makes Python CUDA wheels fragile; Ollama ships a compatible runtime |

Wiring is config, not code (Ollama speaks OpenAI at `/v1`):

```yaml
llm:
  primary:  { provider: deepseek,          base_url: https://api.deepseek.com, model: deepseek-flash }
  escalate: { provider: deepseek,          base_url: https://api.deepseek.com, model: deepseek-v4-pro }
  classify: { provider: openai_compatible, base_url: http://localhost:11434/v1, model: qwen3:14b }
  failover: { provider: openai_compatible, base_url: http://localhost:11434/v1, model: qwen3:8b }
embeddings: { provider: openai_compatible, base_url: http://localhost:11434/v1, model: bge-m3 }
budget: { max_cost_per_run_usd: 1.00, max_cost_per_day_usd: 3.00 }   # balance is $7.54
```

`cost.py` reads `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens` / `cached_tokens`. pydantic-validate every structured output: one retry, then deterministic fallback.

## 11. Identity & mail (`onboard/`, `notify/`)

- Alias `wd-<tenant>-<nn>@toesk.uk`; random 24-char password per identity; `state/secrets.json` (0600).
- Lifecycle `provisioning -> ready -> cooldown(403/429) -> retired | banned`; `onboard --rotate <tenant>` mints `-02` and requeues that identity's unsubmitted work.
- Scheduler: capacity-aware LRU (most remaining daily budget), respecting `next_available_at`.
- Mail API: `/latest` only for newest (`/emails` is lexicographic), URL-encode `recipient`/`key`, **DELETE after read**, `mail_events.message_id UNIQUE`, `drain()` before each flow, reject `receivedAt < flow_start`, `404` = empty, `500` = backoff.
- **Verification links must be opened inside the same browser context** (else the token lands in the wrong cookie jar). Race: logged-in-state vs verification-screen; "Resend code" once on timeout.
- Mailbox poller also harvests application-received/outcome mail -> closes the funnel (`funnel.stage = response`).

## 12. Apply state machine (`apply/workday.py`)

```
ensure_session(tenant)
  |- logged in? -> continue
  |- else login(creds)  |  else create_account() -> mail.await_code() -> same-context link/code
  |- captcha/unknown -> park("needs_manual_signup") + `jobagent signup --headed`
open_application  : {host}/{locale}/{site}{externalPath}/apply
  -> "Autofill with Resume"   (NOT "Apply Manually")
  -> upload tailored PDF -> wait for parse (network idle AND a known field non-empty)
  -> My Information  : a11y diff vs profile -> patch only mismatches
  -> My Experience   : reconcile parsed history; add missing roles/skills/education
  -> Questions       : [data-automation-id^="formField-"] -> answers.yaml -> cache -> LLM -> persist hash
                       handle multiSelect, date pickers, repeaters ("Add Another")
  -> Disclosures     : static defaults (Decline to self-identify)
  -> Review          : screenshot + text artifact + self-consistency check
  -> submit | park   : policy-gated
```

Primary selectors: `data-automation-id` (`legalNameSection_firstName`, `email`, `file-upload-drop-zone`, `bottom-navigation-next-button`, `createAccountSubmitButton`, `formField-<id>`). Fallbacks: `autocomplete` -> `get_by_label` -> fuzzy label -> LLM (last resort, cached).

Failure -> `trace.zip` + screenshot + `page.content()` into `artifacts/`; `attempt+=1`; backoff; `fail_streak >= 3` -> quarantine tenant and continue. **A park is a first-class outcome.**

## 13. Proxy

Provider creds format: `http://user:pass@gate.provider.net:8000` - **one sticky endpoint per identity** (residential/ISP), pinned via `proxy_ref`, health-checked by `doctor` (IP echo, latency, geo). Not used for discovery.

**Status: creds pending.** Until supplied, `tenant_concurrency_cap = 1` is enforced and extra identities act as rotation reserve only (extra accounts from one IP add ban surface with no throughput gain).

## 14. Data model (`state/jobagent.db`)

`shards(host_suffix, resolves, last_checked)` - `companies(id, name, domain, slug_candidates, source)` - `tenant_probes(slug, shard, site_guess, http_status, error_code, checked_at)` *(negative cache)* - `tenants(id, host, tenant, site, wd_shard, locale, status, source, verified_at, facet_counts, matched_req_count)` - `jobs(tenant_id, job_posting_id, external_path, req_id, title, location, posted_on, jd_text, jd_hash, can_apply, first_seen, last_seen, active)` - `matches(job_id, score_sem, score_bm25, score_total, visa_decision, reason)` - `identities(id, tenant_id, alias, alias_version, password_ref, status, profile_dir, proxy_ref, next_available_at, apps_today, fail_streak)` - `applications(id, tenant_id, job_posting_id, identity_id, profile_version, resume_path, status, attempt, external_app_id, submitted_at, artifact_dir, error, UNIQUE(tenant_id, job_posting_id))` - `answers(tenant_id, question_hash, question_text, answer, source, uses)` - `mail_events(message_id UNIQUE, alias, subject, received_at, kind, code, link, consumed_at)` - `funnel(application_id, stage, ts, latency_ms, cost_usd)` - `runs(id, stage, started_at, ended_at, counts_json, cost_usd)`

## 15. Milestones

| # | Scope | Acceptance |
|---|---|---|
| M0 | py3.13 `uv` project, config, migrations, CLI, `doctor`; write `.wslconfig` (28 GB); tune Ollama; pull `bge-m3`; add `pypdf`/`pdfplumber`; **HAR capture -> [?] verify detail/apply endpoints** | `doctor` green (Ollama+GPU+models, mail API, Patchright, DeepSeek balance + budget) |
| M1a | Tenant discovery: inverted source + oracle + slug resolver + shard DNS + facet ranking | >=20 live tenants auto-found, >=80% correct slugs, zero manual entries |
| M1b | Job discovery via `searchText` + `appliedFacets`, 20/page, incremental | matching reqs for 20 tenants < 3 min |
| M2 | Match: dedupe -> filters -> visa -> BM25 + embeddings -> top-K | ranked top-K with reasons + visa decision, $0 |
| M3 | Tailor: JD parse -> select -> rewrite -> validate -> PDF -> ATS gate | <=2 pp, text assertions pass, < $0.01/app |
| M4 | Identity provisioning + mail verify (code **and** same-context link) + rotation | 3 tenants onboarded unattended; sessions survive restart |
| M5 | Apply single tenant `--dry-run` to Review (default `park`) | 3 real jobs at Review, 0 manual inputs |
| M6 | Multi-identity: scheduler, caps, per-identity proxy, ban detection, requeue | 20 apps / 3 tenants, >=80% submitted, no duplicate req |
| M7 | Hardening: canary (incl. `errorCode` drift), cost report, prune/backup, response telemetry | 7-day green soak |

## 16. Risks -> mitigations

| Risk | Mitigation |
|---|---|
| `cxs` endpoint drift | recorded fixtures + daily canary + `diff-traffic old.har new.har` |
| Site-slug misses | inverted source eliminates most; oracle + browser escalation for the rest |
| Stale verification codes | drain + `since=flow_start` + delete-on-read + `message_id UNIQUE` |
| CF blocks mass signup | 3-5 tenants/day, jitter, warmed profiles, `--headed` fallback |
| sm_120 wheel breakage | Ollama-only for local GPU; `fastembed` CPU fallback |
| VRAM/RAM contention | stage-split scheduler; `.wslconfig` 28 GB; `OLLAMA_MAX_LOADED_MODELS=1`, `FLASH_ATTENTION=1`, `KV_CACHE_TYPE=q8_0` |
| Hallucinated metrics | numeric/entity provenance gate + deterministic fallback |
| Budget exhaustion ($7.54) | hard per-run/per-day caps + cost report |
| Account bans | pacing, no same-tenant parallelism, proxy isolation, alias rotation |
| Resume parses badly | ATS-safe template + pdfplumber gate + DOCX fallback |

## 17. Human actions

1. **Proxy creds** (or "skip" -> concurrency stays 1).
2. **`wsl --shutdown`** from Windows after `.wslconfig` is written (cannot be run from inside WSL - it kills this session).
3. **Rotate** DeepSeek key + `AGENT_TOKEN` after wiring (deferred by your call).
4. Confirm `@toesk.uk` catch-all routing exists (`doctor` verifies delivery, cannot fix CF config).
5. **HAR capture**: DevTools -> Network -> Preserve log -> complete one manual Workday application on one target tenant -> save to `artifacts/har/`. This is the spec for M4.
6. Provide an initial **target company list** (optional - discovery finds tenants, but a seed list improves the first run).

## 18. Appendix - probe log (2026-10-11)

- `POST /wday/cxs/nvidia/NVIDIAExternalCareerSite/jobs` -> 200, real data, `userAuthenticated:false`, no CSRF
- wrong site -> 404 `S21 not found: Job_Posting_Site_ID=External`; bogus tenant -> 422 `HTTP_422`
- `nvidia.wd11...` NXDOMAIN; `nvidia.wd1/3/5/12`, `citi.wd1/3/5`, `salesforce.wd1/12` resolve (wildcard)
- root `/` -> 406/0 bytes for both real and bogus hosts
- DeepSeek `/models` -> `deepseek-flash` (V4.1-Flash, 1M ctx, text+image), `deepseek-v4-pro`; balance $7.54
- DeepSeek JSON-mode round trip 0.715 s; cache counters present
- worker: 401 missing/bogus bearer, 204 OPTIONS preflight, ~50-90 ms
- `nvidia-smi`: RTX 5060 Ti 16311 MiB, driver 610.88, compute 12.0; WSL MemTotal 15.9 GB
- resume: valid PDF 1.5, 137619 bytes, no PDF tooling on the box
- `patchright` py3-none wheels (3.10-3.14); `playwright-stealth` broken for `launch_persistent_context`
