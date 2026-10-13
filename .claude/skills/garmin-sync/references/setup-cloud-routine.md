# The twice-daily cloud routine

A Claude *routine* starts a fresh cloud session from a clean clone of the repo's **default branch**
each time, runs without permission prompts, and has **no persistent disk**. This repo is built for
that: `garmin-sync routine` is stateless, configured only by environment variables, narrow (only
what Google can accept: sleep, workouts, weight, body fat, hydration), capped, and dry-run unless told
otherwise. Duplicates are prevented by Google itself (every write has a deterministic data point id,
and existing data is checked first), not by a local ledger.

Settings screens move around; the current steps are at
https://code.claude.com/docs/en/claude-code-on-the-web and
https://code.claude.com/docs/en/cloud-environments#network-access. Names below are the ones to look for.

## Before you start

* This repo's default branch must contain the package and skill (merge the PR first).
* Local logins work: `garmin-sync doctor` shows no failures, and the Google OAuth app is **In
  production** (see [setup-google.md](setup-google.md)).
* Your **time zone** is saved: `garmin-sync timezone` shows it (if not, ask the user, then
  `garmin-sync timezone Asia/Singapore` or their own zone). The cloud machine runs in UTC, so
  without this setting the routine would stop a day short of "today" for anyone ahead of UTC; it
  refuses to run (`MISCONFIGURED`) rather than guess.
* Treat everything put in the routine's environment as **readable by the agent and by anyone who can
  use that environment**. Use a dedicated environment only you use.

## Phase 0: spike (about 15 minutes; do not skip)

The design assumes two things nobody has verified from Anthropic's cloud: Garmin's endpoints are
reachable (they sit behind Cloudflare), and Garmin does not invalidate the old refresh token when it
issues a new one. Learn both before building anything else.

1. In your terminal: `garmin-sync auth export-cloud-env`. It writes `~/.garmin-sync/cloud-env.txt`
   (owner-only) and prints only the variable *names*. Open the file yourself; never paste its contents
   into a chat.
2. Create a **new, throwaway cloud environment**: *Network access* → custom/limited with exactly these
   allowed domains and nothing else (leave the box for package managers ticked so the setup script can
   install dependencies):
   `diauth.garmin.com`, `connectapi.garmin.com`, `health.googleapis.com`, `oauth2.googleapis.com`.
   *Setup script*: `pip install uv && uv sync --frozen --no-dev` (installs exactly the versions in the
   committed `uv.lock`, which has hashes; a bare `pip install -e .` would resolve fresh, unpinned
   versions of libraries that run next to every secret). *Environment variables*: the six lines from
   the file (the five logins plus `GARMIN_SYNC_TIMEZONE`, which is not secret), plus
   `GARMIN_SYNC_APPLY=0`. Remove all connectors.
3. Start a session in that environment on this repo and ask it only to run
   `.venv/bin/garmin-sync routine`. Read the first line and the JSON:
   * `STATUS: DRY_RUN` and `garmin_token_rotated: false` → the stateless design works.
   * `UNREACHABLE` or a Cloudflare/HTTP 403 message from Garmin → cloud IPs are blocked; use the
     fallback below.
   * `PARTIAL` with `garmin_token_rotated: true` → Garmin rotates its refresh token. A stored token
     goes stale after one run; the design needs a place to persist the new token (see "If the token
     rotates").
4. A week later, run it once more: if Google answers with `AUTH_REQUIRED` the production-mode refresh
   token expired anyway, and the fallback applies.

## Phase 1: create the routine (dry run)

* Environment: the one from the spike (or a copy). **No connectors.** Notifications on, so a failed
  run reaches you; a "green" run does not mean the sync succeeded, so the status word matters.
* Variables: the six from `cloud-env.txt` (including `GARMIN_SYNC_TIMEZONE`), plus
  `GARMIN_SYNC_APPLY=0`. Optional: `GARMIN_SYNC_DAYS` (2–7, default 3) and `GARMIN_SYNC_MAX_WRITES`
  (1–200, default 50).
* Schedule: twice a day at the times you choose, **not on the hour** (runs on the hour can start
  late), for example 07:07 and 19:07 **in your time zone**. The minimum interval is one hour.
  Cron fields are **UTC** unless the expression starts with `CRON_TZ=<zone> `, so write the zone in
  explicitly. For Singapore: `CRON_TZ=Asia/Singapore 7 7,19 * * *` (the same as `7 11,23 * * *` in
  UTC: 19:07 and 07:07 SGT). Use the same zone as `GARMIN_SYNC_TIMEZONE`; if the routine screen has
  its own time-zone picker, set that too and check the "next run" it shows.
* Prompt (exactly this; nothing secret belongs in it):

  > Run `.venv/bin/garmin-sync routine` from the repository root and show its full output. If the first line
  > does not start with `STATUS: OK` or `STATUS: DRY_RUN`, say in one sentence what the user must do
  > (log in again, wait, or fix a setting). Do not attempt repairs, do not run any other command, and
  > never print environment variables.

## Phase 2: review, then enable writes

Read three to five dry-run results. Each shows what *would* be written and what Google already holds.
When you are satisfied, set `GARMIN_SYNC_APPLY=1`. Writes are bounded by `GARMIN_SYNC_MAX_WRITES`,
look back at most `GARMIN_SYNC_DAYS` days, and cannot delete or modify anything.

* **Kill switch:** set `GARMIN_SYNC_PAUSED=1` (any value except empty, `0`, `false`, `no`, `off`
  pauses); the next run does nothing and says so.

## What the limits do and do not protect against

The write cap, look-back window, dry-run default and kill switch protect against **bugs and runaway
runs**. They are not a security boundary against a prompt-injected agent: it holds every secret in the
environment, can set the same variables itself, and can call Garmin and Google directly with the
tokens. The real protections are a dedicated environment only you use, no connectors, the four-host
network allow-list, tokens with only the scopes needed, and keeping the routine's prompt free of
anything that makes the agent read untrusted text as instructions. For a stronger boundary, run the
same command from a scheduler where the secrets are never exposed to a model (see the last section).
* **Weekly liveness:** any login expiry makes the run fail with `AUTH_REQUIRED` and notifies you.

## Status words and exit codes

| First line | Exit | Meaning |
|---|---|---|
| `OK` | 0 | synced |
| `DRY_RUN` | 0 | nothing written (writes are off) |
| `PAUSED` | 0 | kill switch is on |
| `PARTIAL` | 4 | something failed, records were held back until their data is complete, Garmin rotated its token, or Garmin's UTC offset differs from `GARMIN_SYNC_TIMEZONE` on *every* recent day (moved, travelling for a while, or the zone is wrong: ask the user); read the detail. A trip's first days alone only appear in the JSON as `timezone_mismatch_days` |
| `RATE_LIMITED`, `UNREACHABLE` | 4 | try again next run |
| `AUTH_REQUIRED` | 3 | a login expired: run `auth garmin` / `auth google login`, re-export, update the variables |
| `MISCONFIGURED` | 1 | a variable is missing or out of range (named, never shown). A missing or invalid `GARMIN_SYNC_TIMEZONE` lands here: ask the user which zone they are in |
| `FAILED` | 1 | an unexpected error; the line names its type only |

## If the token rotates, or Garmin blocks the cloud

* **Rotation:** options are an encrypted token blob in your Google Drive (needs the Drive connector
  inside an autonomous session, so weigh that carefully), or hosting the schedule elsewhere. Ask the
  user which trade-off they prefer; do not decide it for them.
* **Blocked cloud IPs, or expiring Google tokens:** run the same command from a scheduler you control
  (cron or a desktop scheduled task on a machine that is on, or a GitHub Actions workflow with the same
  variables stored as encrypted secrets). The command is identical.
