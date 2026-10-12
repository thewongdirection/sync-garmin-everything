"""The unattended run: Garmin -> Google Health, configured entirely through the environment.

Built for a Claude cloud routine, where each run starts on a fresh machine with no files:
credentials arrive as environment variables, the archive lives in a temporary directory that
is deleted afterwards, and nothing is written outside it. The run is deliberately narrow:

* it only pulls and writes what the Google Health API accepts (sleep, workouts, weight,
  body fat, hydration) for the last few days;
* it is a dry run unless ``GARMIN_SYNC_APPLY=1``;
* the number of writes per run is capped, and ``GARMIN_SYNC_PAUSED=1`` stops it at once;
* duplicates are always checked against Google itself (there is no local ledger to trust);
* its output is a status word and a JSON summary of counts and sanitized, length-capped error
  messages, never health values or secrets.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from garmin_sync import endpoints as ep
from garmin_sync import exitcodes
from garmin_sync.archive import Archive
from garmin_sync.coverage import GOOGLE_WRITABLE
from garmin_sync.garmin_source import (
    AuthRequired,
    GarminSource,
    GarminUnreachable,
    RateLimited,
    login_from_token_json,
    refresh_token_of,
)
from garmin_sync.models import utc_now
from garmin_sync.normalize import build_context, normalize_range
from garmin_sync.pull import pull
from garmin_sync.push import push_records
from garmin_sync.sanitize import clean
from garmin_sync.sinks import google_auth
from garmin_sync.sinks.google_client import GoogleClient
from garmin_sync.sinks.google_health import GoogleHealthSink

ENV_PREFIX = "GARMIN_SYNC_"
ENV_GARMIN_TOKENS = ENV_PREFIX + "GARMIN_TOKENS"
ENV_GARMIN_IS_CN = ENV_PREFIX + "GARMIN_IS_CN"
ENV_GOOGLE_CLIENT_ID = ENV_PREFIX + "GOOGLE_CLIENT_ID"
ENV_GOOGLE_CLIENT_SECRET = ENV_PREFIX + "GOOGLE_CLIENT_SECRET"
ENV_GOOGLE_REFRESH_TOKEN = ENV_PREFIX + "GOOGLE_REFRESH_TOKEN"
ENV_APPLY = ENV_PREFIX + "APPLY"
ENV_PAUSED = ENV_PREFIX + "PAUSED"
ENV_DAYS = ENV_PREFIX + "DAYS"
ENV_MAX_WRITES = ENV_PREFIX + "MAX_WRITES"

REQUIRED_SECRETS = (
    ENV_GARMIN_TOKENS,
    ENV_GOOGLE_CLIENT_ID,
    ENV_GOOGLE_CLIENT_SECRET,
    ENV_GOOGLE_REFRESH_TOKEN,
)
DEFAULT_DAYS = 3
# Yesterday's totals only become final today, so a one-day window would never see a finished day.
MIN_DAYS = 2
MAX_DAYS = 7
DEFAULT_MAX_WRITES = 50
MAX_WRITES_CEILING = 200
# Exactly what the Google writes need: day offsets, then the four writable record kinds.
ENDPOINTS = (*ep.GOOGLE_WRITE_ENDPOINTS, "activities")


class Status(StrEnum):
    OK = "OK"
    DRY_RUN = "DRY_RUN"
    PAUSED = "PAUSED"
    PARTIAL = "PARTIAL"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    RATE_LIMITED = "RATE_LIMITED"
    UNREACHABLE = "UNREACHABLE"
    MISCONFIGURED = "MISCONFIGURED"
    FAILED = "FAILED"


_EXIT_CODE = {
    Status.OK: exitcodes.OK,
    Status.DRY_RUN: exitcodes.OK,
    Status.PAUSED: exitcodes.OK,
    Status.PARTIAL: exitcodes.INCOMPLETE,
    Status.AUTH_REQUIRED: exitcodes.AUTH,
    Status.RATE_LIMITED: exitcodes.INCOMPLETE,
    Status.UNREACHABLE: exitcodes.INCOMPLETE,
    Status.MISCONFIGURED: exitcodes.ERROR,
    Status.FAILED: exitcodes.ERROR,
}


@dataclass(frozen=True)
class RoutineConfig:
    garmin_tokens: str
    google_client_id: str
    google_client_secret: str
    google_refresh_token: str
    is_cn: bool
    apply: bool
    days: int
    max_writes: int

    def __repr__(self) -> str:  # never let a stray print or traceback show the secrets
        return (
            f"RoutineConfig(apply={self.apply}, days={self.days}, "
            f"max_writes={self.max_writes}, <redacted>)"
        )


@dataclass(frozen=True)
class RoutineResult:
    status: Status
    detail: str
    summary: dict[str, Any]

    @property
    def exit_code(self) -> int:
        return _EXIT_CODE[self.status]


class ConfigError(Exception):
    """The environment is incomplete or has an invalid value."""


def _flag(env: Mapping[str, str], name: str) -> bool:
    return env.get(name, "").strip() == "1"


def _bounded_int(env: Mapping[str, str], name: str, default: int, low: int, high: int) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a whole number") from None
    if not low <= value <= high:
        raise ConfigError(f"{name} must be between {low} and {high}")
    return value


_NOT_PAUSED = frozenset({"", "0", "false", "no", "off"})


def is_paused(env: Mapping[str, str]) -> bool:
    """The kill switch fails closed: anything but an explicit "off" pauses.

    (Writing is the opposite: only an exact "1" turns it on.)
    """
    return env.get(ENV_PAUSED, "").strip().lower() not in _NOT_PAUSED


def config_from_env(env: Mapping[str, str]) -> RoutineConfig:
    """Read and validate the configuration; messages name variables, never their values."""
    missing = [name for name in REQUIRED_SECRETS if not env.get(name, "").strip()]
    if missing:
        raise ConfigError("missing environment variables: " + ", ".join(missing))
    return RoutineConfig(
        garmin_tokens=env[ENV_GARMIN_TOKENS],
        google_client_id=env[ENV_GOOGLE_CLIENT_ID].strip(),
        google_client_secret=env[ENV_GOOGLE_CLIENT_SECRET].strip(),
        google_refresh_token=env[ENV_GOOGLE_REFRESH_TOKEN].strip(),
        is_cn=_flag(env, ENV_GARMIN_IS_CN),
        apply=_flag(env, ENV_APPLY),
        days=_bounded_int(env, ENV_DAYS, DEFAULT_DAYS, MIN_DAYS, MAX_DAYS),
        max_writes=_bounded_int(env, ENV_MAX_WRITES, DEFAULT_MAX_WRITES, 1, MAX_WRITES_CEILING),
    )


GarminLogin = Callable[..., GarminSource]


def run_routine(
    env: Mapping[str, str],
    *,
    http: Any,
    garmin_login: GarminLogin = login_from_token_json,
    today: date | None = None,
    now: datetime | None = None,
    progress: Callable[[str], None] = lambda _msg: None,
) -> RoutineResult:
    """Run one unattended sync. Never raises: whatever happens, the answer is a status."""
    if is_paused(env):
        return RoutineResult(Status.PAUSED, "GARMIN_SYNC_PAUSED is set: nothing was done", {})
    try:
        config = config_from_env(env)
    except ConfigError as exc:
        return RoutineResult(Status.MISCONFIGURED, str(exc), {})
    summary: dict[str, Any] = {
        "apply": config.apply,
        "days": config.days,
        "max_writes": config.max_writes,
    }
    try:
        return _run(config, summary, http, garmin_login, today or date.today(), now, progress)
    except Exception as exc:  # noqa: BLE001 - a scheduler needs a status line, not a traceback
        return RoutineResult(Status.FAILED, f"unexpected {type(exc).__name__}", summary)


def _run(
    config: RoutineConfig,
    summary: dict[str, Any],
    http: Any,
    garmin_login: GarminLogin,
    today: date,
    now: datetime | None,
    progress: Callable[[str], None],
) -> RoutineResult:
    now = now or utc_now()
    try:
        auth = google_auth.GoogleAuth(
            google_auth.OAuthClient(config.google_client_id, config.google_client_secret),
            google_auth.MemoryTokenStore(
                google_auth.GoogleToken.from_refresh_token(config.google_refresh_token)
            ),
            http,
        )
        token = auth.refresh()  # fail fast, before touching Garmin
        if missing := token.missing_scopes(google_auth.WRITE_PATH_SCOPES):
            # Without the read scopes the duplicate check cannot see other sources' data.
            raise google_auth.GoogleAuthError("the login lacks permissions: " + ", ".join(missing))
        source = garmin_login(config.garmin_tokens, is_cn=config.is_cn)
        source.probe()
    except google_auth.GoogleAuthError as exc:
        return RoutineResult(Status.AUTH_REQUIRED, f"Google: {clean(exc)}", summary)
    except AuthRequired as exc:
        return RoutineResult(Status.AUTH_REQUIRED, f"Garmin: {clean(exc)}", summary)
    except RateLimited as exc:
        return RoutineResult(Status.RATE_LIMITED, clean(exc), summary)
    except GarminUnreachable as exc:
        return RoutineResult(Status.UNREACHABLE, clean(exc), summary)

    start, end = today - timedelta(days=config.days - 1), today
    summary["range"] = f"{start}..{end}"
    with (
        tempfile.TemporaryDirectory(prefix="garmin-sync-") as tmp,
        Archive.open(Path(tmp) / "archive.sqlite3") as archive,
    ):
        pulled = pull(
            source,
            archive,
            start,
            end,
            only=ENDPOINTS,
            activity_details=ep.GOOGLE_WRITE_ACTIVITY_DETAILS,
            progress=progress,
        )
        summary["pull"] = pulled.to_dict()
        if pulled.aborted:
            # An interrupted pull leaves records without their detail; nothing is written from it.
            return _stopped(pulled.aborted, summary)
        ctx = build_context(archive, start, end)
        records = normalize_range(archive, start, end, ctx)
        pushed = push_records(
            GoogleHealthSink(GoogleClient(auth, http)),
            archive,
            records,
            apply=config.apply,
            now=now,
            metrics=GOOGLE_WRITABLE,
            limit=config.max_writes,
        )
    summary["push"] = pushed.to_dict()
    summary["normalization_issues"] = ctx.diag.to_list()
    summary["garmin_token_rotated"] = _rotated(config.garmin_tokens, source)
    return _conclude(config, summary, sorted(set(pulled.failed) & ep.ESSENTIAL), ctx, pushed.ok)


def _rotated(original: str, source: GarminSource) -> bool:
    """True if Garmin issued a new refresh token during the run (stateless mode then breaks)."""
    before, after = refresh_token_of(original), refresh_token_of(source.token_snapshot())
    return bool(before and after and before != after)


def _stopped(reason: str, summary: dict[str, Any]) -> RoutineResult:
    if reason == "auth_required":
        return RoutineResult(
            Status.AUTH_REQUIRED, "Garmin session rejected during the run", summary
        )
    return RoutineResult(Status.RATE_LIMITED, "Garmin rate limit hit; will retry next run", summary)


def _conclude(
    config: RoutineConfig,
    summary: dict[str, Any],
    failed_essential: list[str],
    ctx: Any,
    push_ok: bool,
) -> RoutineResult:
    problems = []
    if failed_essential:
        problems.append(
            "Garmin endpoints the sync depends on failed: " + ", ".join(failed_essential)
        )
    held_back = sum(
        count for (_, issue), count in ctx.diag.issues.items() if issue.startswith("held back")
    )
    if held_back:
        problems.append(f"{held_back} records held back until their data is complete")
    if not push_ok:
        problems.append("some records failed to write")
    if summary["garmin_token_rotated"]:
        problems.append(
            "Garmin rotated its refresh token; stateless mode cannot keep it (see docs)"
        )
    if problems:
        return RoutineResult(Status.PARTIAL, "; ".join(problems), summary)
    if not config.apply:
        return RoutineResult(Status.DRY_RUN, "dry run: nothing was written", summary)
    return RoutineResult(Status.OK, "synced", summary)
