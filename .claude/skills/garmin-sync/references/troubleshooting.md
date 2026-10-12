# Troubleshooting

Start with `garmin-sync doctor`: it names the failing prerequisite and the single next step.

## Garmin

| Symptom | Meaning / action |
|---|---|
| `Not logged in to Garmin` / `session is invalid` (exit 3) | User runs `garmin-sync auth garmin` in their own terminal. Tokens refresh automatically; a full re-login (with MFA) is needed only if the refresh token expires or is revoked. |
| Pull exits with code 4, `rate_limited` | Garmin returned HTTP 429. Progress is saved; wait (tens of minutes) and re-run the same command. Use smaller ranges and a larger `--delay` (e.g. `--delay 1`). |
| `Garmin unreachable` | Network, a blocked address, or an outage. From a cloud environment, check the allowed domains in [setup-cloud-routine.md](setup-cloud-routine.md). |
| Endpoint listed under `disabled_endpoints` / many `failed` | Three consecutive failures switch an endpoint off for the run (newest days are tried first, so recent data is never starved). Usually the device or account lacks that feature (golf, menstrual, lactate threshold...). Not an error; look at `first_error` if it is an endpoint the user does have. |
| `normalization_issues` mention an endpoint | The payload shape differs from what the normalizer expects. Run `garmin-sync shape <endpoint>` (structure only) and compare with `src/garmin_sync/normalize/*.py`. Fix the normalizer, add a synthetic-fixture test, re-run `push` (no re-download needed). |
| `inventory` lists many unmapped fields | Normal for a new device or firmware. Share the key paths (never values) so the map can be extended. |
| Library breaks after a Garmin change | python-garminconnect is unofficial and pinned exactly. Check its issues page; to move to a fixed release, change the pin in `pyproject.toml`, run `uv lock`, run the tests, and review the library's changelog first (it handles your tokens). |

## Google

| Symptom | Meaning / action |
|---|---|
| `Not logged in to Google` / `not found: ...google_client.json` | `auth google init --client-file <json>`, then `auth google login` (user, terminal). |
| `OAuth client file has an unexpected token_uri` | The file was edited or is not the Cloud Console download. Download it again; it is never used if it points anywhere but Google. |
| `Google rejected the saved login` | Testing-mode tokens last 7 days. Move the OAuth app to production and run `auth google login` again. |
| HTTP 403 | API not enabled for the project, user not a test user, project not onboarded, or permissions missing (`auth google check` lists them). Only that data type is skipped; two denied types stop the run. |
| HTTP 400 on create | Body rejected. Report the (non-sensitive) message; compare with `sinks/google_payloads.py` and the v4 reference; fix and add a test. The record is not ledgered and will be retried. |
| `not written, duplicates unverifiable` | Existing Google data could not be inspected (error, unreadable entry, or more entries than the page limit). Nothing was written; the next run retries. |
| `duplicate_existing` for everything | Google already holds that data (for example from Garmin Connect's own sync), or an earlier run wrote it. This is the intended additive behaviour. |
| Nothing `pushed`, all `deferred_not_final` | The data is too fresh (workouts wait two hours, sleep six, daily totals until the day is over), or the archive was pulled before it was complete. Run `pull` again, later. |
| `held back: not fetched yet: activity_splits` | A workout's detail call failed, so the workout is not written without its laps and notes (a write cannot be corrected). It is retried on the next pull; if it never succeeds, look at `first_error` for that endpoint. |
| `no timezone known; hydration skipped` | The daily summary (`user_summary`) is missing, so the day's edges are unknown. It resolves once that endpoint is fetched. |
| `the Google login lacks permissions` | The consent screen was approved without the read boxes ticked. Run `auth google login` again and tick every permission; without them duplicates cannot be detected, so nothing is written. |

## Health Connect

The export is a file only. Until a companion app imports it, nothing appears on the phone.
See `docs/health-connect-export.md`.

## Local state

* `garmin-sync status` shows what exists and whether permissions are private.
* State lives in `~/.garmin-sync` (override with `--home` / `$GARMIN_SYNC_HOME`). The tool refuses a
  directory that is non-empty and was not created by it, or that is a symlink.
* To start over for one sink, delete its ledger rows (or the whole `archive.sqlite3`). Do this only if
  the user asks.
* "Token path must not be a symlink": the Garmin library refuses symlinked token directories; pick a
  `--home` without symlinks in its path.
