# job-agent - Workstream W: Workday Channel (v4.1)

**Doc set `PS-5.4`.** This is the **implementation doc for the Workday channel**, not a program.
Start at [`plan-general.md`](plan-general.md) (or [`README.md`](README.md)); this file is entered only
at the gates below.

- **Project root:** `/home/favou/development/job-agent` (repo root; sibling of `my-email-worker`)
- **Owns:** `cxs` endpoints + oracle, shard/site discovery, the Workday apply state machine + selectors, Workday account onboarding, HAR fixtures
- **Defers to `plan-general.md` for:** criteria + visa (2.1-2.2), channels (3), sources/hunt (4), tailoring (5), LLM layer (1.5), email identity + attribution (6), identity/proxy policy (8), data model (10), milestones (11)
- **Status:** design closed. `[V]` = verified empirically. `[?]` = to verify, tracked in `plan-general.md` section 14.
- **Stack:** Python 3.13 (`uv`) - Patchright - curl_cffi - Ollama (local) - DeepSeek `deepseek-flash` (cloud)

## Entry, exit and code map (the seam)

| Gate | Enters at | Preconditions | Deliverable |
|---|---|---|---|
| **H1a** discovery | `G1` | general `DiscoverySource` protocol; `postings`/`companies`/`tenant_probes`/`shards` migrations; `curl_cffi` wrapper | `G1-W1` `WorkdayCxsDiscovery`, `G1-W2` `WorkdayTenantDiscovery` |
| **H1b** apply | `G5` | G3 proven on a non-Workday channel; attribution working on real mail incl. a Workday ack; address model live; per-identity proxies; **HAR captured**; `doctor` green; budget caps | `G5-W1..W3` `WorkdayWebApply` + onboarding |
| **H2** return | `G4`/`G6` | - | metrics, drift signatures and fixtures back into `plan-general.md` sections 2, 12, 1 |

| Interface (owned by general) | Implemented / consumed here |
|---|---|
| `DiscoverySource` | `src/jobagent/discover/workday_cxs.py` |
| `TenantSource` | `src/jobagent/discover/workday_tenants.py` |
| `ApplyChannel` | `src/jobagent/apply/workday.py` |
| `Mailbox` (consumed) | `src/jobagent/apply/onboard.py` |
| answer cache (consumed) | `src/jobagent/apply/questions.py` |
## 0. Locked decisions

| Topic | Decision |
|---|---|
| Tenant discovery | **Inverted-first** (harvest Workday apply-URLs from aggregators), verified by the `cxs` oracle. Breadth brute-force + crt.sh are pluggable providers; refactor path in 6.4 |
| Job discovery | `cxs` `searchText` + `appliedFacets` server-side narrowing, 20/page |
| Apply | Patchright, **persistent context per identity**, `data-automation-id` selectors, upload-first ("Autofill with Resume") |
| Identities | **Multiple per tenant**, config-capped. 1 alias + 1 profile dir + 1 sticky proxy each |
| Account addresses | `wd-<tenant>-<nn>@toesk.uk`, distinct local parts, **never `+`-tagged**, **never used for attribution** (`plan-general.md` 6.1) |
| Resume | owned by **`plan-general.md` 5**; Workday-specific parser requirements in section 9 |
| Submit policy | **`park` by default**; `--auto-submit` opt-in |
| LLM | see **`plan-general.md` 1.5** (tiers + budget); Workday-specific notes in section 10 |
| Local GPU | RTX 5060 Ti 16 GB (sm_120 -> needs CUDA 12.8+): **Ollama-only** for GPU work |
| Criteria, visa, comp | **`plan-general.md` 2.1-2.2** (system-wide). Workday-specific notes only in section 3 |
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

## 3. Criteria, visa and targeting

**Moved to `plan-general.md` sections 2.1-2.2** (system-wide, not channel-specific): the target
profile (`config/criteria.yaml`) and the visa / right-to-work config (`config/visa.yaml`, enforced by
`match/visa.py`). Workday-specific notes only:

- The `cxs` search endpoint accepts `searchText` and `appliedFacets`; the criteria feed both (section 7).
- Location tiers matter more here than on other channels: Workday tenants skew to large employers with Dublin/London offices, so the tier-1 filter does most of the work.
- Country for visa answers is derived from the posting location; Workday `locationsText` is the usual source.

## 5. Workday slice of the pipeline

The program pipeline (hunt -> rank -> tailor -> route -> execute -> track -> learn) is defined in
`plan-general.md` section 2. This workstream implements exactly two slices of it:

- **hunt** (G1): `cxs` search + facets -> normalized postings; tenant discovery through the oracle.
- **execute** (G5): deep-link -> autofill-with-resume -> a11y diff -> Q&A cache -> review -> submit | park.

The stage-split scheduler (Ollama-hot batches, then browsers-hot with `OLLAMA_KEEP_ALIVE=0`) is defined
in `plan-general.md`; this channel is a consumer of it, not an owner.


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

## 8. Matching (Workday notes)

Ranking, visa filtering and dedupe are **owned by `plan-general.md` sections 4 and 10**. Workday-specific inputs to that layer:

- `bulletFields[0]` gives the `JR...` requisition id -> the strongest dedupe key available, alongside `externalPath`.
- `facets` from the same search response give per-location and per-`jobFamilyGroup` counts, so the hunt layer can size a tenant without paging through it.
- Filter on `canApply`: the 200 response includes postings that are not open to external applicants.


## 9. Tailoring

**Owned by `plan-general.md` section 5** (master profile, permitted operations, the five anti-exaggeration gates, the ATS extraction gate, `tailor_report.json`). Workday-specific requirements:

- Workday parses the uploaded file itself, so the ATS extraction gate matters more here than on any other channel - a badly parsed PDF becomes dozens of manual field corrections at `G5-W2`.
- Single column, standard headings, selectable text; DOCX stays an A/B option if the Workday parser handles it better.


## 10. LLM layer

**Owned by `plan-general.md` section 1.5** (tiers, wiring, budget caps). Workday-specific notes:

- `effort: low` for JD->requirements extraction; the tailoring call stays on `deepseek-flash`.
- The vision fallback (`deepseek-flash` image input) exists mainly for this channel: unknown Workday fields that neither `data-automation-id` nor label matching resolves get a screenshot.


## 11. Identity & mail (`onboard/`)

Address policy, attribution, mail etiquette and the read-then-DELETE persistence rule are **owned by `plan-general.md` section 6**. Workday-specific:

- Account address `wd-<tenant>-<nn>@toesk.uk` - a Workday account is keyed on the email, so each tenant needs its own; random 24-char password per identity in `state/secrets.json` (0600). Never rotated (it is the login and password-reset path); the contact address on the form is the canonical one.
- Lifecycle `provisioning -> ready -> cooldown(403/429) -> retired | banned`; `onboard --rotate <tenant>` mints `-02` and requeues that identity unsubmitted work.
- Scheduler: capacity-aware LRU (most remaining daily budget) respecting `next_available_at`; `tenant_concurrency_cap = 1` until proxies are supplied.
- **Verification links must be opened inside the same browser context** as the signup (else the token lands in the wrong cookie jar). Race: logged-in-state vs verification-screen; "Resend code" once on timeout.
- Workday acks land in the general attributer; **tier 1 resolves them even when the subject names only the company** (observed: `Thank you for your interest in Citi!`).


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

## 14. Data model

**Canonical model: `plan-general.md` section 10.** This workstream declares extensions and Workday-specific columns only:

- `tenants(id, host, tenant, site, wd_shard, locale, status, source, verified_at, facet_counts, matched_req_count)`
- `tenant_probes(slug, shard, site_guess, http_status, error_code, checked_at)` - negative cache behind the S21/422 triage
- `shards(host_suffix, resolves, last_checked)`
- `postings` additions used here: `external_path`, `req_id` (from `bulletFields[0]`), `can_apply`
- `answers(tenant_id, question_hash, question_text, answer, source, uses)` - the Workday question cache is an instance of the shared cache
- `identities` (Workday accounts): `alias`, `alias_version`, `password_ref`, `profile_dir`, `proxy_ref`, `next_available_at`, `apps_today`, `fail_streak`

Retired from this doc: the `jobs` and `funnel` table names (now `postings` and `events`, per the naming canon in `plan-general.md` section 10).


## 15. Milestones (workstream steps)

Program stages live in `plan-general.md` section 11. Workday steps hang off them:

| Step | Stage | Scope | Acceptance |
|---|---|---|---|
| **G0-W** | G0 | HAR capture of one real Workday application; Patchright engine smoke test; canary tenant pick (Citi likely) | HAR saved to `artifacts/har/`; `data-automation-id` smoke test passes on a real tenant |
| **G1-W1** | G1 | Tenant discovery: shard DNS, the 200/S21/422 oracle, slug candidates, inverted aggregator harvest, facet ranking | >= 20 live tenants auto-found, >= 80% correct site slugs, zero manual entries |
| **G1-W2** | G1 | Job discovery: `searchText` + `appliedFacets`, 20/page, incremental | criteria-matching reqs for 20 tenants < 3 min; re-run incremental |
| **G5-W1** | G5 | Identity onboarding: alias mint, persistent context, account creation, mail-worker verification (code **and** same-context link), rotation | 3 tenants onboarded unattended; sessions survive restart |
| **G5-W2** | G5 | Apply state machine, `--dry-run` to Review | 3 real jobs at Review with 0 manual inputs |
| **G5-W3** | G5 | Multi-identity: scheduler, caps, per-identity proxy, ban detection, requeue | 20 apps / 3 tenants, >= 80% submitted, no duplicate req |
| **G6-W** | G6 | Canary incl. `errorCode` drift detection, recorded fixtures, profile prune/backup | canary green 7 days |

**Retired series:** `M0` -> `G0` (+ `G0-W`), `M1a` -> `G1-W1`, `M1b` -> `G1-W2`, `M2`/`M3` -> `plan-general.md` G2 (not Workday-specific), `M4` -> `G5-W1`, `M5` -> `G5-W2`, `M6` -> `G5-W3`, `M7` -> `G6` + `G6-W`.


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

## 17. Human actions (Workday-specific)

Cross-channel actions are in `plan-general.md` section 13. Workday-only:

1. **HAR capture** of one complete manual Workday application on a target tenant -> `artifacts/har/` (this is `G0-W`, and it is the spec for the apply flow).
2. **Canary tenant**: confirm Citi (or another named tenant) as the first real target.
3. **Proxy creds** assigned to Workday identities before `G5-W3`; until then `tenant_concurrency_cap = 1`.


## 18. Appendix - Workday probe log (2026-10-11)

- `POST /wday/cxs/nvidia/NVIDIAExternalCareerSite/jobs` -> **200**, real data, `userAuthenticated:false`, **no CSRF needed**
- wrong site slug -> **404** `{"errorCode":"S21","message":"not found: Job_Posting_Site_ID=External"}`
- bogus tenant, or a wrong tenant name on a real host -> **422** `{"errorCode":"HTTP_422","message":""}`
- `nvidia.wd11.myworkdayjobs.com` NXDOMAIN; `nvidia.wd1/3/5/12`, `citi.wd1/3/5`, `salesforce.wd1/12` resolve (per-shard wildcard certs)
- root `/` -> 406 with 0 bytes for **both** real and bogus hosts (needs browser-like TLS). `[?]` re-test with `curl_cffi impersonate="chrome"` at `G1-W1`
- `limit:100` returned no `jobPostings` -> the limit is capped at **20**
- NVIDIA reported `total: 2000` while its facets summed higher -> **treat `total` as a ceiling and use `facets`**

Cross-channel probe results (mail worker, DeepSeek, hardware) are in `plan-general.md` sections 1 and 14.
