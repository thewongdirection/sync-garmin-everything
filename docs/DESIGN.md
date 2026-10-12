> **Superseded by [PLAN.md](PLAN.md).** This document describes the *first draft* of the implementation, which is not part of this
> repository yet and has known defects (see PLAN.md §1 and Appendix A). It is kept for its research findings; the architecture and
> mappings will be revised in the Phase 1 rebuild.

# Design

## Goal and the constraint that shapes everything

Sync as much of a person's Garmin data as possible into their Google health account, augmenting the
small subset Garmin Connect already sends.

Garmin's side is easy to read: [python-garminconnect](https://github.com/cyberjunky/python-garminconnect)
exposes 119+ read methods. Google's side is the limit. Research on 2026-10-12:

| Fact | Source |
|---|---|
| The **Google Health API** (v4, `https://health.googleapis.com`) is the successor to the Fitbit Web API. The legacy API loses support 2026-09-30 and is **turned off 2026-10-30**. | [Release notes](https://developers.google.com/health/release-notes), [newsletter](https://developers.google.com/health/newsletters) |
| Google states it is **"not onboarding new projects at this time"**. | same |
| **Create** is documented for only: `exercise`, `sleep`, `weight`, `body-fat`, `height`, `hydration-log`, `nutrition-log`, and write-only women's-health / mood / symptom types. Steps, heart rate, HRV, SpO2, resting HR, VO2 max, etc. list `list/reconcile/rollup` only. | [Data types](https://developers.google.com/health/data-types) |
| Scopes are `https://www.googleapis.com/auth/googlehealth.<group>.readonly` / `.writeonly`. A write-only token can read **only data it wrote itself**. | [Scopes](https://developers.google.com/health/scopes), [list reference](https://developers.google.com/health/reference/rest/v4/users.dataTypes.dataPoints/list) |
| Personal use goes through an OAuth client of type *Web Server*, redirect `https://www.google.com`, consent screen *External / Testing*, you as test user. **Refresh tokens expire after 7 days in Testing mode.** | [Setup](https://developers.google.com/health/setup) |
| **Health Connect** (Android) has ~40 writable record types incl. HRV, SpO2, resting HR, steps, VO2 max, but is an **on-device store with no cloud API**. | [Data types](https://developer.android.com/health-and-fitness/health-connect/data-types) |
| Neither Google target has a place for Body Battery, stress score, training readiness/status, etc. | both pages above |

Consequence: "sync everything to Google" is not achievable by one write path. The design therefore
separates *getting all the data* from *delivering it*.

## Architecture

```
 Garmin Connect ──python-garminconnect──▶  pull  ──▶  SQLite archive (raw JSON, source of truth)
                                                          │
                                                          ▼
                                                    normalize (pure function of the archive)
                                                          │  Record(metric, external_id, start, end, offset, values)
                                      ┌───────────────────┴───────────────────┐
                                      ▼                                       ▼
                          Google Health API sink                   Health Connect exporter
                       (writable subset, dedupes vs Google)    (JSONL for an Android companion app)
```

| Module | Responsibility |
|---|---|
| `endpoints.py` | Registry of every Garmin endpoint archived (day / range / snapshot / per-activity). |
| `garmin_source.py` | Login (interactive vs token-only), throttling, maps 401/429 to typed errors. |
| `pull.py` | Resumable archiving; per-endpoint fault isolation; circuit breaker; refresh window. |
| `archive.py` | SQLite: `raw(endpoint,key,payload)` and `ledger(sink,external_id,hash)`. |
| `normalize/` | Garmin payload → `Record`. Decorator registry; defensive parsing; diagnostics. |
| `coverage.py` | Single source of truth for which metric can go where. |
| `push.py` | Selects records for a sink, applies ledger/settling rules, records results. |
| `sinks/google_*.py` | OAuth (PKCE), payload builders, API client with duplicate detection. |
| `sinks/health_connect.py` | JSONL export (see [health-connect-export.md](health-connect-export.md)). |
| `cli.py`, `reports.py` | Commands; `coverage`, `status`, `shape` views that never print values/secrets. |

### Key decisions

* **Raw archive first.** Every endpoint is stored verbatim; normalization is recomputed from it. Mapping bugs are fixed and re-applied without re-hitting Garmin (which is rate-limited and unofficial).
* **Idempotency by ledger, not by trusting the destination.** `external_id` is deterministic (`garmin:<metric>:<epoch|day|activityId>`). A ledger row per (sink, id) with a content hash means nothing is sent twice. Sinks that cannot update in place (Google) never resend a changed record; the exporter does, with a higher `clientRecordVersion` so Health Connect upserts.
* **Additive to Garmin Connect's own sync.** Before each create, the Google sink lists existing data points from *any* source around the record (needs the read scopes) and skips on overlap. Unparseable existing data counts as overlap: when unsure, don't write.
* **Dry run by default.** Nothing is written, ledgered or exported without `--apply`. `--limit 1` supports a first live canary.
* **Today is deferred.** Records whose local day is not over are skipped, because Garmin revises them (sleep scores, daily totals).
* **Credentials never pass through the agent.** `login` and `google-login` are run by the user in their terminal; the skill only ever calls token-only commands. State lives in `~/.garmin-sync` (0700/0600), outside the repo.
* **Fault isolation.** One failing endpoint never aborts a pull. Three consecutive failures switch it off for the run (typical for features a device lacks). 401/429 abort cleanly with progress kept.

## Coverage matrix

Generated live by `garmin-sync coverage`. Summary:

| Garmin endpoint | Normalized metric(s) | Google Health API (write) | Health Connect record |
|---|---|---|---|
| `sleep_data` | sleep (stages) | `sleep` | SleepSessionRecord |
| `activity_summary` | exercise | `exercise` | ExerciseSessionRecord |
| `weigh_ins` | weight, body fat, body water mass, bone mass | `weight`, `body-fat` | Weight/BodyFat/BodyWaterMass/BoneMass |
| `hydration_data` | hydration | `hydration-log` | HydrationRecord |
| `heart_rates` | heart rate, resting HR | – (read-only) | HeartRate, RestingHeartRate |
| `hrv_data` | HRV (RMSSD) | – | HeartRateVariabilityRmssd |
| `spo2_data` | SpO2 | – | OxygenSaturation |
| `respiration_data` | respiratory rate | – | RespiratoryRate |
| `steps_data` | steps | – | Steps |
| `user_summary` | total/active calories, distance, floors | – | TotalCalories, ActiveCalories, Distance, FloorsClimbed |
| `blood_pressure` | blood pressure | – | BloodPressure |
| `max_metrics` | VO2 max | – | Vo2Max |
| everything else (stress, Body Battery, training readiness/status, intensity minutes, endurance/hill score, per-activity splits/weather/gear, profile, devices, goals, badges, ...) | – | – | **archive only** |

## Known limitations and unverified assumptions

Stated plainly, because the build environment had no Garmin or Google account:

1. **The Google sink has not run against the live API.** Request shapes follow the published v4 reference and are unit-tested against fakes. Specifically unverified: (a) the `create` body for each type, (b) that `dataSource` is accepted as sent, (c) that a client-supplied data point id is not needed (we don't send one; `metadata.externalId` is set for sleep), (d) the create response is an `Operation` that we do **not** poll for completion. Use `--limit 1` and inspect the result in the Google Health app before bulk loading.
2. **Garmin payload shapes come from synthetic fixtures.** Assumptions worth checking on a real account with `garmin-sync shape <endpoint>`: `sleepLevels[].activityLevel` 0/1/2/3 = deep/light/REM/awake; `spO2ValuesArray` and `respirationValuesArray` as `[[epochMs, value]]`; `wellnessStartTimeGmt/Local` in `user_summary`; the weigh-in field names. A changed shape produces a *diagnostic* (shown in the report) rather than silent data loss where detectable.
3. **Access may be unobtainable.** New projects may not be onboardable; Testing-mode tokens last 7 days.
4. **Health Connect needs a companion Android app** to ingest the export. Not built; the file format is specified so it can be.
5. **Unofficial Garmin API.** python-garminconnect is not affiliated with Garmin and can break when Garmin changes its services; rate limits are not documented.
6. **Not handled yet:** naps; sleep scores/HRV status/skin-temperature as Google types; GPS routes and per-sample activity streams (archived raw, not mapped); `height`, `nutrition-log` and women's-health types (Google supports them, Garmin data exists, mapping not written); Google `reconcile`-based dedupe for read-only types.
7. Daily calorie/distance totals and exercise sessions overlap in Health Connect; Health Connect's data-origin priority handles this on-device.

## Future work

* Validate the Google sink on a real account; add `Operation` polling if required.
* Android companion importer for the Health Connect export.
* Map `height`, `nutrition-log`, and women's-health data.
* Activity streams → Health Connect `ExerciseRoute` / heart-rate series per session.
