# sync-garmin-everything

A [Claude skill](.claude/skills/garmin-sync/SKILL.md) and CLI that pull **all** of your Garmin Connect
data through [python-garminconnect](https://github.com/cyberjunky/python-garminconnect) and sync what
Google can accept to your Google Health account, to augment the small subset Garmin Connect already
sends. It can run on demand or twice a day as an autonomous Claude cloud routine.

## What it can and cannot do

"Sync everything" is limited by what Google lets third parties write. Verified against Google's docs on
2026-10-12 (field-level detail: [docs/field-map/FIELD-MAP.md](docs/field-map/FIELD-MAP.md)):

| Destination | Reach | What it accepts |
|---|---|---|
| **Google Health API** (cloud, successor to the Fitbit Web API) | Direct from this tool | **Writes only:** exercise (with laps, pauses, heart-rate zones, running dynamics), sleep (with stages), weight, body fat, hydration. Steps, heart rate, HRV, SpO2 etc. are read-only there. |
| **Health Connect** (Android, on-device) | Via a JSONL export for a companion app | ~40 record types incl. HRV, SpO2, resting HR, steps, VO2 max. **No cloud API**, so this repo exports a file; the Android importer is not built yet. |
| **Local archive** | Always | *Every* endpoint python-garminconnect can read (stress, Body Battery, training readiness, ...) as raw JSON in SQLite. |

So: everything is archived locally, the writable subset goes to Google Health, and the wider set is exported
in a Health Connect-ready format. `garmin-sync coverage` prints the exact mapping.

**Access caveat.** Google states it is "not onboarding new projects at this time" for the Google Health API,
so you may not be able to create the OAuth client the Google sink needs. The Garmin archive and the Health
Connect export work without it.

**Validation status.** The Google sink is written against the published v4 reference and tested against
fakes; it has **not** been run against a live Google account. Garmin payload mappings are tested against
synthetic fixtures; validate them on your own account with `inventory`, `shape` and a dry run. Start with
`--limit 1`. [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) lists what a live account still has to confirm.

## Setup

```bash
python3.12 -m venv .venv && . .venv/bin/activate
uv sync --frozen                 # exact versions from uv.lock; or: pip install -e ".[dev]"
garmin-sync doctor               # checks everything and tells you the next step
```

State (tokens, archive, exports) lives in `~/.garmin-sync` (override with `--home` or `$GARMIN_SYNC_HOME`),
owner-only. Nothing sensitive is ever stored in the repo.

## Usage

```bash
# 1. Log in, in your own terminal (passwords and codes are read with hidden prompts)
garmin-sync auth garmin                          # email, password, MFA code
garmin-sync auth google init --client-file ~/Downloads/client_secret.json
garmin-sync auth google login                    # prints a URL; paste the redirected address back
garmin-sync timezone Asia/Singapore              # your IANA zone: "today" must be your day, not UTC's
garmin-sync doctor                               # confirms all of it, names anything missing

# 2. Archive and deliver
garmin-sync pull --days 30                       # everything; resumable, safe to re-run
garmin-sync push --sink health-connect --days 30           # dry run
garmin-sync push --sink health-connect --days 30 --apply   # writes exports/*.jsonl
garmin-sync push --sink google-health --days 30 --limit 1            # dry run, incl. duplicate check
garmin-sync push --sink google-health --days 30 --limit 1 --apply    # first real write; check the app
garmin-sync sync --sinks google-health,health-connect --days 3 --apply   # pull + both, repeatable

# 3. Inspect
garmin-sync status        # local state, never secrets
garmin-sync coverage      # endpoint -> Google Health / Health Connect mapping
garmin-sync inventory     # fields your account returns that the field map lacks (paths and types only)
```

Your time zone is an explicit setting, never guessed: cloud machines run in UTC, where it is still
yesterday for anyone ahead of it. If it is unset, the commands and the skill stop and ask; the machine's own zone is used only as a
flagged guess.

Writes are **dry-run by default**; `--apply` is required. Only *final* data is sent (see
[ARCHITECTURE.md](docs/ARCHITECTURE.md)), a ledger records every write as it happens, and duplicate
protection cannot be disabled. Exit codes: 0 ok, 1 error or failed writes, 2 usage, 3 login needed,
4 incomplete.

### Setting up Google access

Follow [setup-google.md](.claude/skills/garmin-sync/references/setup-google.md): enable the *Google Health
API*, create a **Web application** OAuth client with redirect URI `https://www.google.com`, add yourself as a
test user. Apps in *Testing* mode have refresh tokens that **expire after 7 days**; for an unattended routine
move the app to *In production* (it stays unverified, which is fine for your own account).

### Running it on a schedule

`garmin-sync routine` is the unattended run for a Claude cloud routine: configured only by environment
variables, stateless, dry-run unless `GARMIN_SYNC_APPLY=1`, capped, with a kill switch. Follow
[setup-cloud-routine.md](.claude/skills/garmin-sync/references/setup-cloud-routine.md): a short spike first
(is Garmin reachable from the cloud? does its refresh token rotate?), then a dry-run routine, then writes.

## Documentation

* [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): data flow, guarantees, what is still unverified
* [docs/PLAN.md](docs/PLAN.md): decisions and phases
* [docs/field-map/](docs/field-map/README.md): every Garmin field mapped to Google Health / Health Connect
* [docs/health-connect-export.md](docs/health-connect-export.md): export file specification
* [.claude/skills/garmin-sync/SKILL.md](.claude/skills/garmin-sync/SKILL.md): the Claude skill

## Development

```bash
pytest                                   # unit + regression tests, incl. a repo-wide secret scan
ruff check . && ruff format --check .
bandit -r src -c pyproject.toml
pip-audit
```

## Security

* Credentials are only ever typed into your terminal, with hidden prompts; the password is never stored,
  only the refresh-capable tokens python-garminconnect creates (`0600`). Revoke access in your Garmin
  account settings if they leak.
* Google tokens are stored `0600`; OAuth uses PKCE and a `state` check. The Google endpoints are fixed in
  the code; the downloaded client file is checked against them but never trusted to point elsewhere, and
  requests never follow redirects.
* Text that originates from Garmin or Google is sanitized before it is printed or written to a note.
* The archive contains your health data. Keep `~/.garmin-sync` private and out of backups you do not control.
* python-garminconnect is an unofficial client of Garmin's web services (not affiliated with Garmin); it can
  break when Garmin changes them.

## License

MIT
