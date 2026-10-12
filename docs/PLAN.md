# Plan v2: Garmin → Google Health sync (skill + autonomous routine)

Status: **Phase 1 (rebuild) complete; Phase 2 (validation on your real accounts) is next.** The field-map defaults in §7 were
approved on 2026-10-12 and the rebuild implements them. The first draft's defects (§1, Appendix A) are fixed, each with a
regression test (§9). Nothing has run against a live Garmin or Google account yet.

## 0. Summary

* **Field map (done):** 4,567 documented Garmin fields (158 methods + FIT message types) matched against all 1,082 Google
  Health API payload fields. Full detail in [field-map/FIELD-MAP.md](field-map/FIELD-MAP.md) and the two CSVs.
  (Counts below are as of the Phase 1 rebuild: two per-night copies of heart rate and HRV in the sleep payload moved
  from Health Connect to archive-only because the dedicated endpoints already carry them.)
* **What Google will actually take:** only 11 data types accept third-party writes. Of Garmin's fields, **169 go to the Google
  Health API** (sleep, workouts incl. laps/pauses/HR-zones/running-dynamics, weight, body fat, hydration), **102 more go to a
  Health Connect export** (HRV, SpO2, respiration, heart-rate series, steps, calories, floors, blood pressure, VO2 max…),
  **18 are opt-in** (nutrition, cycle tracking, profile/settings), and **3,550 stay archive-only** because neither Google
  surface has anywhere to put them (Body Battery, stress, training readiness/status, sleep scores, coaching text, equipment, golf…).
* **Auth:** guided by the skill, but passwords, MFA codes and tokens only ever pass through *your* terminal, never the chat.
* **Autonomous routine:** feasible, with three real constraints to design around: cloud runs have **no persistent disk**,
  Garmin may **rotate its refresh token** on every run, and Google's **Testing-mode refresh tokens die after 7 days**. §5 has a
  recommended design, a two-stage rollout (dry-run first), and a cheap feasibility spike before we build anything.
* **Decisions:** all settled except the routine's time zone and run times: routine state = stateless with a spike first,
  OAuth = Production-unverified, field-map defaults approved. See §7.

## 1. What changed since the first draft

I built a first draft before the mapping step. The research and two independent reviews found these problems in it, all on the
fix list in Appendix A. **All are fixed in the Phase 1 rebuild**, each with a regression test (§9):

| # | Finding | Source |
|---|---|---|
| 1 | **Security (HIGH, verified):** the OAuth host allow-list can be bypassed with a backslash in `token_uri`; the README's claim that credentials only go to Google was false | security review |
| 2 | **Correctness (HIGH):** staleness is judged by calendar date, not fetch time, so a partly-filled day can be frozen and then pushed to Google irrevocably | correctness review |
| 3 | Sleep stage codes are **device-dependent** (REM-capable: 2 = REM; others: 2 = awake). The draft always mapped 2 → REM | field research |
| 4 | The library's SpO2 call returns spot checks / hourly averages, not the per-minute series the draft read; it would have produced nothing | field research |
| 5 | Google `exercise.interval` should span *elapsed* time, with `activeDuration` = timer time. The draft used timer time for both | field research |
| 6 | The draft sent only a thin workout summary; Google also accepts laps, pause/resume events, HR-zone durations and running dynamics | Discovery doc |
| 7 | Ledger entries are written only after a whole batch; HR buckets collide in half-hour time zones; one 403 aborts the whole batch; sleep falls out of short date windows; exit codes hide failures; several smaller items | both reviews |

The complete list is in Appendix A, and §9 maps each item to its fix and test. The full test, lint, security and secret scans ran
before any code was pushed.

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
| 2. Authorize | **you** | `garmin-sync auth google login`: prints the consent URL (PKCE + state); you paste the redirected address into a hidden prompt (or pipe it with `--stdin`), so the auth code never lands in shell history or chat |
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
| No human in the loop | `--apply` approval can't be interactive | **consent is given when you create the routine**, bounded in code: max writes per run, max look-back window, dedupe cannot be disabled in routine mode, no delete/update path, kill switch env var (`GARMIN_SYNC_PAUSED`), and **stage 1 is dry-run only**. These guard against bugs and runaway runs, not against a prompt-injected agent that holds the secrets (§9) |
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

Prerequisites: the routine clones the default branch (`main`), so the package and skill must be merged to it first; until then `main` is an empty root commit.

## 6. Implementation phases

| Phase | Deliverable | Acceptance |
|---|---|---|
| **0 Approvals** | decisions §7; default branch; Google API access check; spike | answers + spike result |
| **1 Rebuild** (done, §9) | fix all review findings; implement the map (§2) incl. device-dependent sleep stages, laps/events/zones, SpO2 sources, interval/active duration; per-outcome ledger; `fetched_at` staleness; exit codes; output sanitization; `auth`, `inventory`, `doctor`, `routine` commands; skill + docs updated | every item in the fix list has a regression test; tests, lint, bandit, pip-audit, secret scan all clean; independent re-review |
| **2 Local validation** | your real accounts: `inventory` regenerates the map; dry runs; Google canary `--limit 1`, verified in the Google Health app | zero normalization warnings or each explained; canary visible; no duplicates |
| **3 Cloud routine** | environment (allow-list, vars), routine in dry-run, then apply | 3-5 clean dry-runs; first applied run verified |
| **4 Optional** | Android Health Connect importer; Google Sheets/Drive daily digest for archive-only metrics | separate decision |

## 7. Decisions needed

1. ~~**Tiers:**~~ **APPROVED.** Default-on Google writes = sleep, workouts (full richness), weight, body fat, hydration; opt-in = nutrition, cycle data, profile/settings. OK?
2. ~~**Heart-rate zones:**~~ **APPROVED.** Garmin has 5, Google 4. Proposal: Z1+Z2 → light, Z3 → moderate, Z4 → vigorous, Z5 → peak. OK?
3. ~~**Active Zone Minutes:**~~ **APPROVED.** derive `moderate + 2 × vigorous` from Garmin intensity minutes (assumes both weight vigorous double). OK?
4. ~~**SpO2:**~~ **APPROVED** (sleep-embedded readings plus the library endpoint's spot checks and hourly averages). use sleep-embedded readings + hourly averages (no extra endpoint), or also call the library-unwrapped per-minute `dailySpo2` endpoint via the generic client? Recommend the former first.
5. ~~**When Google already has the data**~~ **APPROVED:** skip. (e.g. from Garmin Connect's own sync): skip (recommended) vs. write anyway.
6. ~~**Routine hosting & token persistence**~~ **DECIDED: A, stateless, spike first.** Fall back to B (Drive blob) only if the spike shows Garmin rotates its refresh token.
7. ~~**Google OAuth app status**~~ **DECIDED: Production-unverified** for unattended use; the spike verifies the refresh token survives past 7 days.
8. **Schedule:** your time zone and the two run times; and OK to create the empty repo's default branch / merge via PR when Phase 1 is done. *(open)*
9. ~~**Draft code**~~ **DONE:** stayed local until Phase 1 passed its gates; the reviewed rebuild and these docs are published together (this PR).

Items 1-5 (the field-map defaults) were **approved as proposed on 2026-10-12** ("approve the defaults and start Phase 1"). Item 8 is
still open: the time zone and run times; the default branch now exists (an empty root commit, so the routine has something to clone).

## 8. Risks I can't remove

* python-garminconnect is unofficial; Garmin can change or block it (and Cloudflare may block cloud IPs).
* Google is "not onboarding new projects"; your project may be refused. The archive and Health Connect export still work.
* The Google write path has never run against a live account; Phase 2 canary is the first real test.
* Some Garmin fields are only knowable from your account (`inventory`).
* Routines are a research-preview feature; limits and behaviour may change.

## 9. Phase 1 results

Everything on the fix list is fixed and pinned by a test. What changed beyond the list:

* **Idempotent writes (found in the Discovery document).** `DataPoint.name` may be client-provided, so every create now carries
  `garmin-<hash of the record id>`. A repeat is answered with 409 and reported as already present. That makes retries safe and
  means the stateless cloud run cannot write twice even though it has no ledger. Unverified until the Phase 2 canary.
* **Finality is a property of each record** (`Record.is_final`), not of the calendar: running totals wait for their day to end and for
  a fetch after it; discrete events (sleep, workouts, weigh-ins) wait two hours after they end. This replaces "defer today".
* **`--no-dedupe` was removed**, `sync` requires `--sinks`, ranges are capped at 366 days, and writes have a per-run cap.
* **New commands:** `auth garmin|google init|login|check|export-cloud-env`, `doctor`, `inventory`, `routine`. Exit codes: 0 ok,
  1 error, 2 usage, 3 login needed, 4 incomplete.
* **Not built (deferred):** FIT-file streams (routes, per-second heart rate/speed/power/cadence), intraday floors, the opt-in tier
  (nutrition, cycle tracking, height, profile/settings), the Android importer.

| Fix-list item | Fix | Regression test |
|---|---|---|
| OAuth host allow-list bypass (HIGH) | exact, pinned Google endpoints; the file's values are checked, never used | `test_google_auth.py::test_the_backslash_and_userinfo_host_confusion_is_refused`, `::test_token_requests_go_to_the_pinned_endpoint_whatever_the_file_says` |
| Staleness judged by date (HIGH) | fetch-time finality in `pull` and in `Record.is_final` | `test_pull.py::test_a_day_archived_while_in_progress_is_fetched_again_later`, `test_push.py::test_records_from_an_archive_pulled_too_early_are_held_back` |
| Range windows skipped though the request grew | rows remember the span they covered | `test_pull.py::test_a_range_window_cut_short_by_an_earlier_run_is_fetched_again` |
| `activityId` used in file paths | digits-only validation before any use | `test_pull.py::test_activity_ids_that_are_not_plain_digits_are_rejected` |
| Ledger written after the whole batch | one record at a time, via a per-outcome callback | `test_push.py::test_progress_is_ledgered_one_record_at_a_time` |
| `sync --apply` wrote both sinks; `--no-dedupe`; no range cap; bad `--days`/`--limit`/`--sinks`/`--only` | argument validation, required `--sinks`, option removed | `test_cli.py::test_invalid_arguments_are_rejected_before_anything_runs`, `::test_resolve_range_rejects_bad_ranges`, `::test_unknown_endpoint_name_is_an_error_not_a_silent_pull_of_nothing` |
| Remote text printed unsanitized | `sanitize.clean` on every remote string | `test_foundation.py::test_clean_*`, `test_pull.py::test_error_text_from_garmin_is_sanitized`, `test_normalize.py::test_activity_name_and_description_are_sanitized` |
| OAuth code passed in argv | option removed; hidden prompt or stdin | `test_cli.py::test_google_login_has_no_command_line_option_for_the_redirect_url`, `test_security.py::test_no_command_line_option_takes_a_secret` |
| Redirects followed on token calls | `allow_redirects=False` everywhere | `test_security.py::test_every_google_request_refuses_redirects` |
| `ensure_private_dir` chmod'ed existing directories | adopt only empty or own directories; never symlinks | `test_foundation.py::test_ensure_refuses_a_populated_directory_it_did_not_create` and neighbours |
| Login printed success unconditionally | verifies the token file exists | `test_login.py::test_a_login_that_saved_no_tokens_is_not_reported_as_success` |
| `.gitignore` gaps; no lockfile/upper bounds; non-ASCII `state` | patterns added; `uv.lock`, `<` bounds; bytes comparison | `test_security.py::test_gitignore_blocks_sensitive_artifacts`, `::test_dependencies_have_upper_bounds_and_the_lockfile_is_committed`, `test_google_auth.py::test_a_non_ascii_state_is_a_clean_error_not_a_crash` |
| Heart-rate buckets collide in half-hour zones | buckets counted from each Garmin day's own start | `test_normalize.py::test_heart_rate_bucket_ids_never_collide_across_days_in_half_hour_zones` |
| Hydration/unsure matches treated as duplicates; lookup failed open after 250 points | tri-state duplicate check; only "none" permits a write | `test_google_health.py::test_hydration_is_skipped_only_when_hydration_overlaps_that_same_day`, `::test_unreadable_existing_data_is_unverifiable_not_a_duplicate`, `::test_too_many_existing_points_to_check_means_no_write` |
| Breaker starved recent days; FIT bypassed it | newest first; breaker covers FIT | `test_pull.py::test_a_failing_old_range_does_not_starve_the_recent_days`, `::test_repeated_fit_failures_trip_the_breaker_too` |
| One 403 aborted the batch | per-data-type denial; two types stop the run | `test_google_health.py::test_a_permission_error_blocks_only_that_data_type` |
| Sleep missing from short windows | one `anchor_date` rule for range and finality | `test_normalize.py::test_sleep_is_selected_by_the_day_you_woke_up` |
| Exit code 0 on failures | distinct exit codes | `test_cli.py::test_a_failing_sink_makes_the_exit_code_nonzero`, `::test_pull_exit_code_*` |
| Single-sample HR in instant form; export file names collided | positive-length interval; microsecond names | `test_normalize.py::test_a_single_heart_rate_sample_still_has_a_positive_length`, `test_push.py::test_two_exports_in_the_same_second_do_not_overwrite_each_other` |
| A 401 refresh used a retry slot | separate bookkeeping | `test_google_health.py::test_a_401_refresh_does_not_use_up_a_retry` |
| Sleep stage codes are device-dependent | REM-capable vs not tables; unknown treated as awake and reported | `test_normalize.py::test_devices_without_rem_never_produce_rem_sleep` and neighbours |
| SpO2 shape wrong | spot checks and hourly averages, descriptor-aware | `test_normalize.py::test_spo2_spot_checks_and_hourly_averages_are_both_read` |
| Exercise interval/duration; no laps, events, zones, dynamics | elapsed interval, timer `activeDuration`, splits, events, zones, mobility metrics | `test_google_health.py::test_exercise_interval_is_elapsed_time_and_active_duration_is_timer_time`, `::test_laps_are_sent_as_split_summaries_without_the_output_only_duration`, `test_normalize.py::test_exercise_joins_laps_events_and_metadata` |
| Tests pinning buggy behaviour; planning scripts needing lint clean-up | tests rewritten; only `tools/field_map/generate_docs.py` ships (the CSVs are the source of truth) | the whole suite; ruff clean |

### Review round on the rebuild

Two independent reviewers (correctness, security) then went through the rebuilt code; a third checked the fixes. Their
findings were verified before acting; the ones that held, and what changed:

| Finding | Fix | Regression test |
|---|---|---|
| Routine pushed after a failed or interrupted pull, writing workouts without laps/notes forever (HIGH) | joined detail rows are required for a workout to be final; an aborted pull pushes nothing; the routine pulls only the detail calls it needs | `test_routine.py::test_an_interrupted_pull_writes_nothing`, `::test_an_exercise_whose_detail_calls_failed_is_not_written_degraded`, `test_normalize.py::test_an_exercise_without_its_detail_rows_has_no_provenance` |
| Missing `user_summary` made hydration use a UTC day and freeze a partial total (HIGH) | hydration needs a known timezone and ends at the next day's own midnight | `test_normalize.py::test_hydration_without_any_known_timezone_is_skipped_not_guessed_in_utc`, `::test_hydration_days_follow_local_midnights_across_a_clock_change` |
| Nothing enforced the read scopes, so the duplicate check could be blind (HIGH) | sink and routine refuse to write without them | `test_google_health.py::test_a_login_without_the_read_scopes_writes_nothing`, `test_routine.py::test_a_login_without_the_read_scopes_is_refused_before_garmin_is_contacted` |
| Every 409 treated as "already stored" | only `ALREADY_EXISTS` is a duplicate | `test_google_health.py::test_other_conflicts_are_failures_not_duplicates` |
| Redirects, empty or wrong-shape bodies read as success or "no data" | 2xx and a JSON object required; URL paths validated | `test_google_health.py::test_a_redirect_or_wrong_status_is_a_failure_not_success` and neighbours |
| Creates retried blindly after a 5xx or timeout | creates retry only on 429 | `test_google_health.py::test_a_create_that_may_have_landed_is_not_blindly_repeated` |
| Laps sent in `exercise.splits` (Google's own km splits) | sent as `splitSummaries`; field map corrected | `test_google_health.py::test_laps_are_sent_as_split_summaries_without_the_output_only_duration`, `test_field_map.py` |
| Non-contiguous sleep stages; conflicting REM flags; doubled Local offsets (China) | gaps filled or stages dropped; conflict distrusted; offsets cross-checked and bounded | `test_normalize.py::test_short_unmeasured_gaps_are_filled_as_awake_so_stages_stay_contiguous`, `::test_conflicting_capability_flags_are_distrusted_and_reported`, `::test_a_doubled_local_offset_falls_back_to_the_days_own_offset` |
| Library log text bypassed sanitization (MEDIUM) | log records routed through `clean` | `test_cli.py::test_library_log_output_is_sanitized_before_it_reaches_stderr` |
| Invisible "tag"/variation-selector characters survived `clean` | category-based stripping, bounded work | `test_foundation.py::test_clean_removes_text_that_is_invisible_to_a_reader` |
| `calendarDate`, JSON keys and ids reached output/Google unvalidated | validated or cleaned and capped | `test_normalize.py::test_a_calendar_date_that_is_not_a_date_never_reaches_an_id`, `test_cli.py::test_shape_and_inventory_do_not_echo_hostile_keys_verbatim` |
| One poisoned payload crashed the run | per-row isolation, bounded numbers, `Unbuildable` | `test_normalize.py::test_an_unreadable_row_is_reported_and_skipped_without_stopping_the_rest` |
| Kill switch failed open; one-day window could never sync hydration; no status line on a crash | fail-closed, minimum 2 days, catch-all `FAILED` | `test_routine.py::test_the_kill_switch_fails_closed`, `::test_a_one_day_window_is_refused_...`, `::test_an_unexpected_error_still_ends_in_a_status_not_a_traceback` |
| `pull`/`sync` exit 0 when the endpoints the writes need failed | exit 4 for essential failures only | `test_cli.py::test_pull_fails_when_an_endpoint_the_google_writes_need_fails` |
| Install path ignored `uv.lock`; lock pinned just-published releases | `uv sync --frozen`, exact `garminconnect` pin, `exclude-newer` cooling-off | `test_security.py::test_dependencies_have_upper_bounds_and_the_lockfile_is_committed` |
| A clock-change day's hydration anchored to the wrong day; laps could overlap by a second; the routine's status line was not first | records carry an end offset; laps truncated and clipped; no progress noise | `test_normalize.py::test_hydration_on_a_clock_fall_back_day_is_25_hours_and_stays_on_its_own_day`, `::test_recorded_laps_never_overlap`, `test_cli.py::test_the_routine_prints_its_status_line_first_and_no_progress_noise` |
| Creates that may have landed did not count toward the write cap; a 200 with an error body, `done` as a string or `Retry-After: nan` slipped through | every attempted create counts; strict reply parsing; non-finite waits ignored | `test_google_health.py::test_the_write_cap_counts_every_create_that_may_have_landed`, `::test_create_replies_are_interpreted_strictly`, `::test_a_retry_after_that_is_not_a_finite_number_falls_back_to_backoff` |
| `clean()` mangled Persian joiners and missed a few invisible characters; unsorted gap budget in sleep stages | invisible characters removed outright; a total gap budget | `test_foundation.py::test_clean_keeps_words_joined_by_a_joiner_character_together`, `test_normalize.py::test_many_small_unmeasured_gaps_add_up_to_unstaged_sleep` |
| Directory handling followed symlinks; `export-cloud-env` ran without a terminal; weak secret/leak scanners | `O_NOFOLLOW`+`fchmod`; terminal required; scanners extended | `test_foundation.py::test_a_symlinked_directory_is_refused_not_chmodded`, `test_cli.py::test_export_cloud_env_refuses_to_run_without_a_terminal`, `test_security.py` |

Deliberately not changed: the 2-hour/6-hour finality grace periods are a judgement, not a measurement (a live account will tell);
the in-code caps are not a boundary against a prompt-injected agent (documented, with GitHub Actions as the stronger option);
no CI workflow or `.claude/settings.json` was added, since neither was asked for.

## Appendix A. Fix list for the first draft (all fixed in Phase 1; see §9)

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
