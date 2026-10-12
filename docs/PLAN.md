# Plan v2: Garmin → Google Health sync (skill + autonomous routine)

Status: **planning only, for review.** This PR contains documentation only. The first-draft code is deliberately **not**
included (it has known defects, see §1 and Appendix A), and no rebuild starts until the field-map defaults in §7 are approved.

## 0. Summary

* **Field map (done):** 4,567 documented Garmin fields (158 methods + FIT message types) matched against all 1,082 Google
  Health API payload fields. Full detail in [field-map/FIELD-MAP.md](field-map/FIELD-MAP.md) and the two CSVs.
* **What Google will actually take:** only 11 data types accept third-party writes. Of Garmin's fields, **169 go to the Google
  Health API** (sleep, workouts incl. laps/pauses/HR-zones/running-dynamics, weight, body fat, hydration), **106 more go to a
  Health Connect export** (HRV, SpO2, respiration, heart-rate series, steps, calories, floors, blood pressure, VO2 max…),
  **18 are opt-in** (nutrition, cycle tracking, profile/settings), and **3,546 stay archive-only** because neither Google
  surface has anywhere to put them (Body Battery, stress, training readiness/status, sleep scores, coaching text, equipment, golf…).
* **Auth:** guided by the skill, but passwords, MFA codes and tokens only ever pass through *your* terminal, never the chat.
* **Autonomous routine:** feasible, with three real constraints to design around: cloud runs have **no persistent disk**,
  Garmin may **rotate its refresh token** on every run, and Google's **Testing-mode refresh tokens die after 7 days**. §5 has a
  recommended design, a two-stage rollout (dry-run first), and a cheap feasibility spike before we build anything.
* **Decisions:** two are settled (routine state = stateless, spike first; OAuth = Production-unverified), the draft code stays
  local until Phase 1 is done, and the field-map defaults are **still open**, since you asked to keep planning. See §7.

## 1. What changed since the first draft

I built a first draft before the mapping step. It is held back locally and is not part of this PR. The research and two independent
reviews found these problems in it, all on the fix list in Appendix A:

| # | Finding | Source |
|---|---|---|
| 1 | **Security (HIGH, verified):** the OAuth host allow-list can be bypassed with a backslash in `token_uri`; the README's claim that credentials only go to Google was false | security review |
| 2 | **Correctness (HIGH):** staleness is judged by calendar date, not fetch time, so a partly-filled day can be frozen and then pushed to Google irrevocably | correctness review |
| 3 | Sleep stage codes are **device-dependent** (REM-capable: 2 = REM; others: 2 = awake). The draft always mapped 2 → REM | field research |
| 4 | The library's SpO2 call returns spot checks / hourly averages, not the per-minute series the draft read; it would have produced nothing | field research |
| 5 | Google `exercise.interval` should span *elapsed* time, with `activeDuration` = timer time. The draft used timer time for both | field research |
| 6 | The draft sent only a thin workout summary; Google also accepts laps, pause/resume events, HR-zone durations and running dynamics | Discovery doc |
| 7 | Ledger entries are written only after a whole batch; HR buckets collide in half-hour time zones; one 403 aborts the whole batch; sleep falls out of short date windows; exit codes hide failures; several smaller items | both reviews |

The complete list is in Appendix A. All fixes get regression tests, and the full test, lint, security and secret scans run before
any code is pushed.

## 2. What can and cannot go to Google

Google Health API (v4, `health.googleapis.com`): `create` exists only for `exercise`, `sleep`, `weight`, `body-fat`, `height`,
`hydration-log`, `nutrition-log`, `menstrual-period`, `moods`, `symptoms`, `ovulation-test` (plus PATCH of Profile/Settings).
Steps, heart rate, HRV, SpO2, resting HR, VO2 max, calories, distance, floors etc. are **read-only** through the API today.
Health Connect (Android) accepts almost all of those, but only on the phone, so it needs a companion app (not built; the export
format is specified in [health-connect-export.md](health-connect-export.md)).

| Destination | Garmin fields | Examples |
|---|---|---|
| Google write (G, G+H) | 169 | sleep window + stages; workout type/time/distance/calories/HR/pace/elevation/steps; laps; HR-zone times; running dynamics; weight; body fat; hydration |
| Health Connect only (H) | 106 | HR series, resting HR, HRV, SpO2, respiration, steps (15-min), daily calories/distance/floors, blood pressure, body-water/bone mass, VO2 max, skin temperature, GPS routes (from FIT) |
| Opt-in (GO) | 18 | nutrition log, cycle symptoms/mood/flow, height, profile age, display units, time zone |
| Archive only (A) | 3,546 | Body Battery, stress, training readiness/status/load, sleep score/need, race predictions, FTP/lactate threshold, equipment, badges, golf, plans, coaching text |
| Housekeeping (M) | 728 | ids, container nodes, social fields |

Google-side view: of 1,082 fields, 620 are output-only (set by Google), 211 belong to read-only types, **82 are filled** (61
directly + 21 derived/constant such as UTC offsets), 18 are opt-in, and **64 are writable but Garmin has nothing to put
there** (e.g. per-lap HR zones, nutrition food references, out-of-bed segments).

### Mapping proposals that need your call
Marked in the CSV as `DECISION`/`ASSUMES`/`EVIDENCE THIN`; the substantive ones are §7 items 2-4.

### "Every field" caveat
Garmin publishes no schema, so the inventory rests on recorded responses and third-party parsers (confidence tags in the CSV).
Device-specific keys and ten methods (e.g. power zones, menstrual summaries, training plans) have no public evidence. The plan adds an
**`inventory` command** that records the key paths and types (never values) your own account returns, diffs them against the
map, and lists unmapped fields. That is the only way to make the list truly complete, and it runs in Phase 2.

## 3. Product shape

```
 local machine (you)                                     cloud routine (twice a day)
 ───────────────────                                     ───────────────────────────
 garmin-sync auth garmin / google   (interactive)        garmin-sync routine
 garmin-sync pull --days N          (full archive)         ├─ auth --check (fail fast, clear message)
 garmin-sync inventory              (completes the map)    ├─ pull last 2-3 days (temp archive)
 garmin-sync push --sink health-connect                    ├─ normalize + dedupe vs Google
 garmin-sync push --sink google-health                     ├─ write ≤ N records to Google (bounded)
                                                           └─ persist rotated tokens, emit 1-line JSON summary
```

* **Local** is the system of record: the full SQLite archive of everything the library can read, and the Health Connect export.
* **Cloud** does one narrow job: incremental Garmin → Google writes. It cannot hold the archive (no persistent disk).

## 4. Authentication (guided by the skill)

Principle: **the skill orchestrates and verifies; you type the secrets.** In Claude Code, `!` runs a command and puts its *output*
into the conversation, so any command that prints a secret must not be run that way. Secret-handling commands print nothing sensitive.

### Garmin
| Step | Who | How |
|---|---|---|
| 1. Log in (email, password, MFA) | **you**, in your terminal | `garmin-sync auth garmin`: no-echo prompts; tokens saved `0600`; the password is never stored. China accounts: `--cn` |
| 2. Verify | skill | `garmin-sync auth garmin --check`: token-only, one cheap authenticated call, prints `ok / expired / unreachable`, no PII |
| 3. Renewal | automatic | the library refreshes the access token itself; a full re-login (with MFA) is needed only if the refresh token expires or is revoked |

Garmin has no official OAuth for personal use. python-garminconnect emulates the mobile app's sign-in and can break when Garmin changes it.

### Google
| Step | Who | How |
|---|---|---|
| 1. Prerequisites | you, in Google Cloud Console | enable *Google Health API*, create a **Web application** OAuth client with redirect `https://www.google.com`, add yourself as a test user. The skill gives a checklist and an `auth google init` check that parses the downloaded JSON and flags problems |
| 2. Authorize | **you** | `garmin-sync auth google login`: prints the consent URL (PKCE + state); you paste the redirected address into a hidden prompt (or `-` for stdin), so the auth code never lands in shell history or chat |
| 3. Verify | skill | `garmin-sync auth google --check`: refreshes the token, calls `users/me/identity`, lists granted vs missing scopes, warns before expiry |
| 4. Avoid the 7-day trap | you | Testing-mode refresh tokens expire after **7 days**. For an unattended routine, move the OAuth app to *In production* (it stays unverified; fine for one user). I could not confirm from Google's docs how restricted scopes behave in that state for a single owner; **the spike in Phase 0 tests it** |

All eight read+write scopes are requested because write-only scopes hide data Garmin Connect already sent, which would break de-duplication.

### Skill behaviour
The skill gets an *Authentication* section with a decision tree (`auth status` → missing/expired/unreachable → exact next command),
the rule "never ask for or accept a secret in chat", and one `doctor` command that runs every check and prints the single next action.

## 5. Autonomous twice-daily routine

Facts from Anthropic's docs (research-preview feature): a routine starts a **fresh cloud session from a clean clone of the repo's
default branch** each run, executes **without permission prompts**, uses skills committed to that repo, includes **all your
connectors by default**, and its environment variables are **readable by anyone who uses the environment**. Minimum schedule
interval is one hour; runs on the hour can start late. Connector traffic bypasses the network allow-list.

### Constraints and how the design meets them
| Constraint | Consequence | Design |
|---|---|---|
| Fresh VM each run, no disk | no local archive, ledger or token file survives | cloud job is incremental Google sync only; de-dup uses Google itself (overlap query + `sleep.metadata.externalId`) instead of a local ledger |
| Garmin may rotate the refresh token (`client.py` replaces it whenever Garmin returns a new one) | a stored token can go stale after one run | **Phase 0 spike measures rotation.** If it rotates, persist the refreshed token (see below) |
| Env vars visible to the agent and anyone using the environment | secrets are exposed to prompt-injection | dedicated environment only you use; **no connectors**; **Custom network allow-list of exactly 4 hosts**: `diauth.garmin.com`, `connectapi.garmin.com`, `health.googleapis.com`, `oauth2.googleapis.com` (the SSO login hosts are needed only for the one-time interactive login, never in the routine) |
| Garmin/Google responses contain third-party-writable text (activity names, error messages) | prompt-injection channel | CLI sanitizes and length-caps every remote string; routine prompt is "run one command, report its JSON"; outbound hosts are limited as above |
| No human in the loop | `--apply` approval can't be interactive | **consent is given when you create the routine**, bounded in code: max writes per run, max look-back window, dedupe cannot be disabled in routine mode, no delete/update path, kill switch env var (`GARMIN_SYNC_PAUSED=1`), and **stage 1 is dry-run only** |
| "Green" run ≠ task succeeded | silent failure risk | `routine` exits non-zero on any failure/skip; completion notification (push/email) on; first line of output is a status word |

### Where do rotated Garmin tokens live? (decision §7-6)
| Option | How | Pros | Cons |
|---|---|---|---|
| **A. Stateless** (try first) | tokens in an env var; nothing written back | no connectors, smallest blast radius | breaks if Garmin invalidates the old refresh token after refresh |
| **B. Encrypted blob in your Google Drive** | routine reads/writes one small file via the Drive connector; key in an env var | survives rotation | needs the Drive connector in an autonomous session (all its tools, incl. write/trash, are usable); token bytes pass through the model context (encrypted) |
| **C. GitHub Actions instead of a Claude routine** | scheduled workflow, encrypted GitHub Secrets, cache for state, no LLM | secrets not visible to an agent; cheapest; deterministic | not a "Claude routine"; token write-back still needs a PAT or cache |

Recommendation: **A, verified by the spike; fall back to B if rotation is real. Consider C if you value hardening over
having Claude supervise.** The sync itself needs no LLM; the routine's value is diagnosis and notifications.

### Rollout
1. **Phase 0 spike (15 min, you):** put tokens in a throwaway environment; I run `auth garmin --check` from a cloud session to learn (a) are
   Garmin's endpoints reachable from Anthropic's cloud IPs (they sit behind Cloudflare), (b) does the refresh token rotate, (c) does
   Google accept a Production-status refresh token after 7+ days (checked later).
2. Create the routine **in dry-run mode** (`GARMIN_SYNC_APPLY=0`), schedule e.g. `07:07` and `19:07` your local time, connectors = none, notifications on.
3. Review 3-5 dry-run summaries, then flip `GARMIN_SYNC_APPLY=1`.
4. Weekly liveness is automatic: any auth expiry makes a run fail loudly and notifies you.

Plan B if cloud IPs are blocked by Garmin: a Desktop scheduled task or cron on your own machine (needs the machine on), or GitHub Actions.

Prerequisites: the repo has **no commits and no default branch yet**; the routine clones the default branch, so the skill and package must
be merged to it first.

## 6. Implementation phases

| Phase | Deliverable | Acceptance |
|---|---|---|
| **0 Approvals** | decisions §7; default branch; Google API access check; spike | answers + spike result |
| **1 Rebuild** | fix all review findings; implement the map (§2) incl. device-dependent sleep stages, laps/events/zones, SpO2 sources, interval/active duration; per-outcome ledger; `fetched_at` staleness; exit codes; output sanitization; `auth`, `inventory`, `doctor`, `routine` commands; skill + docs updated | every item in the fix list has a regression test; tests, lint, bandit, pip-audit, secret scan all clean; independent re-review |
| **2 Local validation** | your real accounts: `inventory` regenerates the map; dry runs; Google canary `--limit 1`, verified in the Google Health app | zero normalization warnings or each explained; canary visible; no duplicates |
| **3 Cloud routine** | environment (allow-list, vars), routine in dry-run, then apply | 3-5 clean dry-runs; first applied run verified |
| **4 Optional** | Android Health Connect importer; Google Sheets/Drive daily digest for archive-only metrics | separate decision |

## 7. Decisions needed

1. **Tiers:** default-on Google writes = sleep, workouts (full richness), weight, body fat, hydration; opt-in = nutrition, cycle data, profile/settings. OK?
2. **Heart-rate zones:** Garmin has 5, Google 4. Proposal: Z1+Z2 → light, Z3 → moderate, Z4 → vigorous, Z5 → peak. OK?
3. **Active Zone Minutes:** derive `moderate + 2 × vigorous` from Garmin intensity minutes (assumes both weight vigorous double). OK?
4. **SpO2:** use sleep-embedded readings + hourly averages (no extra endpoint), or also call the library-unwrapped per-minute `dailySpo2` endpoint via the generic client? Recommend the former first.
5. **When Google already has the data** (e.g. from Garmin Connect's own sync): skip (recommended) vs. write anyway.
6. ~~**Routine hosting & token persistence**~~ **DECIDED: A, stateless, spike first.** Fall back to B (Drive blob) only if the spike shows Garmin rotates its refresh token.
7. ~~**Google OAuth app status**~~ **DECIDED: Production-unverified** for unattended use; the spike verifies the refresh token survives past 7 days.
8. **Schedule:** your time zone and the two run times; and OK to create the empty repo's default branch / merge via PR when Phase 1 is done. *(open)*
9. ~~**Draft code**~~ **DECIDED: stays local and uncommitted until Phase 1 is done**; then the reviewed rebuild and these docs are published together.

Items 1-5 (the field-map defaults) are **open**: you chose to keep planning before the Phase 1 rebuild starts.

## 8. Risks I can't remove

* python-garminconnect is unofficial; Garmin can change or block it (and Cloudflare may block cloud IPs).
* Google is "not onboarding new projects"; your project may be refused. The archive and Health Connect export still work.
* The Google write path has never run against a live account; Phase 2 canary is the first real test.
* Some Garmin fields are only knowable from your account (`inventory`).
* Routines are a research-preview feature; limits and behaviour may change.

## Appendix A. Fix list for the first draft (held back, to be fixed in Phase 1)

**Security**
* HIGH (verified): the OAuth host allow-list for `token_uri`/`auth_uri` can be bypassed with a backslash (`https://evil.example\@oauth2.googleapis.com/token`
  passes `urlsplit` but `requests` connects to `evil.example`). Fix: pin the exact Google URIs.
* MEDIUM: Garmin-supplied `activityId` becomes part of a file path (FIT download); the ledger is written only after a whole batch (a mid-batch network
  error loses it, enabling duplicate writes); `sync --apply` writes to both sinks by default; no cap on date range; `--no-dedupe` reachable by an agent.
* LOW: sanitize remote text before printing (prompt-injection channel); OAuth code passed via argv; `allow_redirects=False` on token calls; `ensure_private_dir`
  chmods pre-existing directories; login prints success unconditionally; `.gitignore` gaps (`.*.tmp`, `*.sqlite`, `token*.json`, `*.jsonl`); non-ASCII `state`
  raises `TypeError`; dependency lock and upper bound.

**Correctness**
* HIGH: staleness is judged by calendar date rather than fetch time (a partly-filled day can be frozen and pushed irrevocably); range windows are skipped as
  settled even when the requested range extended past them.
* MEDIUM: heart-rate hour buckets collide across days in half-hour-offset zones; hydration de-dup matches any nearby day and records unsure matches as
  duplicates; the circuit breaker starves recent data when old days fail, and FIT downloads bypass it; a single 403 aborts the whole batch instead of one
  data type; `sync --sinks` is not validated (a typo targets Google); sleep is range-filtered by start date but settled by wake date, so short windows miss it.
* LOW: exit code 0 on failed/skipped sinks; single-sample HR exported in instant form; a 401 refresh consumes a retry slot; duplicate lookup fails open after
  250 points; `--limit -1`, `--days 0` and `--only <typo>` mishandled; export filenames collide at one-second resolution.

**Data model (from the field research)**
* Sleep `activityLevel` codes depend on the device: REM-capable nights 0 deep / 1 light / 2 REM / 3 awake / -1 unmeasurable; others 0 deep / 1 light / 2 awake / 3 awake.
* The library's SpO2 call returns `spO2SingleValues` or `spO2HourlyAverages`, not a per-minute `spO2ValuesArray`.
* Exercise `interval` must span elapsed time with `activeDuration` = timer time; exercise should also carry laps, events, HR-zone durations and running dynamics.

**Tests and hygiene**
* Some tests pin buggy behaviour (blanket 403 stop, date-based skip); two are tautological; SpO2 fixtures encode the wrong shape.
* The planning scripts under `tools/field_map/` need lint clean-up (or to be folded into the real `inventory` command) before they are committed.
