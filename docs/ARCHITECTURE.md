# Architecture

How the tool is built and which guarantees it keeps. For what maps where, field by field, see
[field-map/FIELD-MAP.md](field-map/FIELD-MAP.md); for the history of decisions, [PLAN.md](PLAN.md).

## The constraint that shapes everything

Garmin's side is easy to read: [python-garminconnect](https://github.com/cyberjunky/python-garminconnect)
exposes 119+ read methods. Google's side is the limit (research on 2026-10-12):

| Fact | Source |
|---|---|
| The **Google Health API** (v4, `https://health.googleapis.com`) is the successor to the Fitbit Web API. The legacy API loses support 2026-09-30 and is **turned off 2026-10-30**. | [Release notes](https://developers.google.com/health/release-notes) |
| Google states it is **"not onboarding new projects at this time"**. | same |
| **Create** is documented for only: `exercise`, `sleep`, `weight`, `body-fat`, `height`, `hydration-log`, `nutrition-log`, and write-only women's-health / mood / symptom types. Steps, heart rate, HRV, SpO2, resting HR, VO2 max, etc. are read-only. | [Data types](https://developers.google.com/health/data-types) |
| A data point's id "can be client-provided" (4-63 characters of `a-z`, `0-9`, `-`), so a create can be made idempotent. | [Discovery document](https://health.googleapis.com/$discovery/rest?version=v4), `DataPoint.name` |
| Scopes are `https://www.googleapis.com/auth/googlehealth.<group>.readonly` / `.writeonly`. A write-only token can read **only data it wrote itself**. | [Scopes](https://developers.google.com/health/scopes) |
| Personal use goes through an OAuth client of type *Web Server*, redirect `https://www.google.com`. **Refresh tokens expire after 7 days while the app is in Testing.** | [Setup](https://developers.google.com/health/setup) |
| **Health Connect** (Android) has ~40 writable record types incl. HRV, SpO2, resting HR, steps, VO2 max, but is an **on-device store with no cloud API**. | [Data types](https://developer.android.com/health-and-fitness/health-connect/data-types) |
| Neither target has a place for Body Battery, stress score, training readiness/status, etc. | both pages above |

So "sync everything to Google" is not one write path. Getting *all* the data and delivering it are separate.

## Data flow

```
 Garmin Connect ──python-garminconnect──▶ pull ──▶ SQLite archive (raw JSON + fetched_at + span_end)
                                                         │
                                                         ▼
                                       normalize  (pure function of the archive)
                                         Record(metric, external_id, start, end, offset, values, fetched_at)
                                     ┌───────────────────┴──────────────────┐
                                     ▼                                      ▼
                         Google Health API sink                  Health Connect exporter
                  (sleep, workouts, weight, body fat,        (JSONL for an Android companion app;
                   hydration; checks Google first)            every metric, versioned for upserts)
```

| Module | Role |
|---|---|
| `endpoints.py`, `pull.py`, `garmin_source.py` | What to fetch, resumable fetching, throttled/mapped Garmin access |
| `archive.py` | SQLite: raw payloads with provenance, and the per-sink push ledger |
| `normalize/` | One normalizer per endpoint; activities join their detail payloads by id |
| `models.py`, `coverage.py` | The `Record` type and where each metric can go |
| `push.py` | Selects what is due, honours the ledger, records progress one record at a time |
| `sinks/` | `google_health` (dedupe + create), `google_payloads` (bodies), `google_client` (HTTP), `google_auth` (OAuth), `health_connect` (export) |
| `routine.py` | The unattended, environment-configured run for a cloud routine |
| `cli.py`, `auth_commands.py`, `doctor.py`, `inventory.py` | Commands |
| `paths.py`, `sanitize.py` | Private file handling; neutralizing untrusted text |

## Guarantees

**Nothing is frozen half-finished.** Garmin keeps revising a day after the watch syncs.

* A raw row is final only if it covered the whole requested window *and* was downloaded at least 36 hours
  after the window's UTC midnight (so roughly one to two days after the local day ended, depending on the
  time zone); otherwise `pull` fetches it again (`pull.is_final`). Windows are walked newest
  first, so a feature the device lacks cannot starve recent days.
* A record is pushed only when `Record.is_final(now)`: for running totals (hydration, steps, calories...)
  its local day is over *and* the source was downloaded after the day ended; for discrete events the source
  was downloaded at least two hours (a workout, a weigh-in) or six hours (a night's sleep) after the event
  ended. A record of unknown provenance is never final, and neither is a workout whose detail rows (laps,
  events, notes) have not been fetched: what is written cannot be corrected, so it waits for them. A night belongs to the day you woke up, one rule used both
  for choosing a date range and for deciding finality (`Record.anchor_date`).

**"Today" is the user's today.** Stored data is right regardless (every Garmin record carries its own
UTC offset), but the current day has to come from somewhere, and a cloud machine's clock is UTC: at
07:07 in Singapore it is 23:07 UTC the day before, so a window ending "today" would miss the morning.
`usertime.py` therefore requires an explicit IANA zone (`GARMIN_SYNC_TIMEZONE`, else the setting saved by
`garmin-sync timezone`, else a non-UTC system zone that doctor flags as unconfirmed). Abbreviations,
offsets and legacy fixed-offset names (`SGT`, `UTC+8`, `EST`, `Etc/GMT-8`) are refused, error messages name
the setting but never repeat its value, and a missing zone is a question for the user, never a UTC default.
The routine refuses to run without it (`MISCONFIGURED`) and reports `PARTIAL` when Garmin's own per-day UTC
offsets disagree with it on *every* day it could check (a trip's first days alone only show in the summary);
a day whose clock changed is accepted at either side of the change. `pull(today=...)` is required, and a
test forbids `date.today()` and zone-less `datetime.now()` anywhere in `src/`.

**Writes cannot duplicate.** Google offers no update path for what we write, so the sink is conservative:

* Every create carries a deterministic client-chosen id (`garmin-` + a hash of the record's stable id).
  Repeating a create is answered with 409 `ALREADY_EXISTS` and reported as already present, so a cloud run
  with no local ledger cannot write twice. Any other 409 (for example `ABORTED`) is a failure, not a
  duplicate. A create is retried only on 429 ("not processed"); after a 5xx or a timeout it may or may
  not have landed, so it is reported as failed and the next run's duplicate check settles it.
* Only a 2xx response with a JSON object counts. A redirect (never followed), an empty body or a wrong
  shape is a failure, never "no data" and never "created". A create Google reports as still processing
  is "pending": not recorded as stored, because it cannot be polled.
* The login must hold the read scopes for the types written; without them a listing only shows data this
  app wrote itself, so the duplicate check would be blind. Nothing is written otherwise.
* Before creating, the sink lists existing data around the record from *any* source (that is why the read
  scopes are requested) and skips overlapping data, so data Garmin Connect already sent is left alone. The
  check has three outcomes: found, none, cannot tell. Only "none" permits a write; an unreadable entry, a
  failed lookup or more entries than the page limit mean no write.
* Hydration is a daily total, so it is skipped if Google holds any hydration overlapping that local day.

**Writes are bounded and reversible only by you.** Dry run is the default everywhere. `--limit` /
`GARMIN_SYNC_MAX_WRITES` caps actual writes (not duplicate checks). The tool has no delete or update path.
A 403 blocks only that data type; two denied types, a lost login, or five consecutive errors stop the run.
Every input record gets exactly one outcome, reported as it happens, and the ledger is updated per record.

**Failures are isolated and visible.** One failing endpoint never aborts a pull; auth loss and rate limits
stop it cleanly with progress saved; three consecutive failures switch an endpoint off for the run (FIT
downloads included). Exit codes: 0 ok, 1 error or failed writes, 2 usage, 3 login needed, 4 incomplete.

**Untrusted text is neutralized.** Activity names, error messages and ids come from third parties and are
read by people and AI agents. Remote strings are stripped of control, bidi and zero-width characters and
length-capped before they reach output, reports or notes; activity ids must be 1-20 digits before they are
used as file names or archive keys.

**Secrets stay in your terminal.** Passwords, MFA codes and the Google redirect address are read with hidden
prompts, never from argv; tokens are stored `0600` in a private directory the tool refuses to adopt unless
it is new, empty or its own, and it never follows a symlink there. The Google client file is checked against
Google's exact endpoints but its values are never used. Requests do not follow redirects.

## The cloud routine

`garmin-sync routine` starts from nothing every time: credentials arrive as environment variables, the
archive is a temporary directory, nothing else is written. It pulls only what Google can accept for the last
2-7 days, applies the same finality and duplicate rules (against Google, since there is no ledger), and
prints a status word and a JSON summary of counts. Writes need `GARMIN_SYNC_APPLY=1`; `GARMIN_SYNC_PAUSED` (anything but an explicit off)
stops it. A pull that was cut short writes nothing. These limits guard against bugs and runaway runs; they
are not a security boundary against a prompt-injected agent, which holds the secrets (see the routine setup
guide). Its network needs are four hosts: `diauth.garmin.com`, `connectapi.garmin.com`,
`health.googleapis.com`, `oauth2.googleapis.com`. Setup: `.claude/skills/garmin-sync/references/setup-cloud-routine.md`.

## What a live account still has to confirm

The Google write path has never run against a real account. Phase 2 of the plan is the first real test
(`--limit 1`, verified in the Google Health app). Specifically unverified:

* that `create` honours a client-provided data point id, and answers a repeat with 409 `ALREADY_EXISTS`
  (the design assumes the documented behaviour; if not, the overlap check is the only duplicate guard);
* that recorded laps belong in `exercise.splitSummaries` (Google's text says `splits` is its own default
  1 km / 1 mile splits), and that stages with short gaps filled as awake are accepted;
* sleep without stage data as `CLASSIC` with no `stages`;
* the spellings Garmin uses for pause/resume events. The documented `sectionTypeDTO.key` looks like
  `timerTrigger`, which is not recognised, so pause events are probably not sent until `inventory` on a
  real account shows what the keys are (unknown spellings drop the event list rather than guess);
* Garmin pool-length units (not sent), SpO2 per-minute data (not read: the library's endpoint does not return it);
* whether Garmin's refresh token rotates and whether cloud IPs reach Garmin (the routine spike answers both).

## Not built yet

FIT-file streams (GPS routes, per-second heart rate, speed, power, cadence), intraday floors, the opt-in
tier (nutrition, cycle tracking, height, profile and settings), and the Android Health Connect importer.
