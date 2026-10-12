"""``doctor``: check every prerequisite and say what to do next.

Checks never print token or credential values; the online checks make one cheap
authenticated call to each service.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from garmin_sync.archive import Archive
from garmin_sync.garmin_source import (
    TOKEN_FILENAME,
    AuthRequired,
    GarminSource,
    GarminUnreachable,
    RateLimited,
    login_from_tokens,
)
from garmin_sync.paths import HOME_MARKER, AppHome, is_private
from garmin_sync.sanitize import clean
from garmin_sync.sinks import google_auth
from garmin_sync.sinks.google_client import GoogleApiError, GoogleClient

OK, WARN, FAIL = "ok", "warn", "fail"
_STALE_AFTER = timedelta(days=3)


@dataclass(frozen=True)
class Check:
    name: str
    status: str  # ok | warn | fail
    message: str
    fix: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "status": self.status, "message": self.message, "fix": self.fix}


def next_action(checks: list[Check]) -> str | None:
    """The fix for the most serious problem found (failures before warnings)."""
    for wanted in (FAIL, WARN):
        for check in checks:
            if check.status == wanted and check.fix:
                return check.fix
    return None


def run_doctor(
    home: AppHome,
    *,
    online: bool,
    http: Any,
    garmin_login: Callable[..., GarminSource] = login_from_tokens,
    today: date | None = None,
) -> list[Check]:
    checks = [_check_home(home)]
    checks += _check_garmin(home, online, garmin_login)
    checks += _check_google(home, online, http)
    checks.append(_check_archive(home, today or date.today()))
    return checks


def _check_home(home: AppHome) -> Check:
    if not (home.root / HOME_MARKER).exists():
        return Check("state_dir", WARN, f"{home.root} not created yet", "garmin-sync auth garmin")
    return Check("state_dir", OK, str(home.root))


def _check_garmin(home: AppHome, online: bool, login: Callable[..., GarminSource]) -> list[Check]:
    token_file = home.garmin_token_dir / TOKEN_FILENAME
    if not token_file.exists():
        return [
            Check(
                "garmin_login",
                FAIL,
                "not logged in to Garmin",
                "Run `garmin-sync auth garmin` in your own terminal (it asks for your password)",
            )
        ]
    checks = []
    if not is_private(token_file):
        checks.append(
            Check(
                "garmin_token_permissions",
                WARN,
                "token file is readable by others",
                f"chmod 600 {token_file}",
            )
        )
    if not online:
        return [*checks, Check("garmin_login", OK, "token file present (not verified online)")]
    try:
        login(home).probe()
    except AuthRequired:
        checks.append(
            Check(
                "garmin_login",
                FAIL,
                "Garmin rejected the saved session",
                "Run `garmin-sync auth garmin` again",
            )
        )
    except RateLimited:
        checks.append(Check("garmin_login", WARN, "Garmin is rate limiting; try again later"))
    except GarminUnreachable as exc:
        checks.append(
            Check("garmin_login", WARN, clean(exc), "Check the network connection and retry")
        )
    else:
        checks.append(Check("garmin_login", OK, "Garmin session is valid"))
    return checks


def _check_google(home: AppHome, online: bool, http: Any) -> list[Check]:
    try:
        client = google_auth.load_client(home.google_client_file)
    except google_auth.GoogleAuthError as exc:
        return [
            Check(
                "google_client",
                FAIL,
                clean(exc),
                "Follow references/setup-google.md, then run "
                "`garmin-sync auth google init --client-file <json>`",
            )
        ]
    checks = [Check("google_client", OK, "OAuth client file is valid")]
    store = google_auth.FileTokenStore(home.google_token_file)
    try:
        token = store.load()
    except google_auth.GoogleAuthError as exc:
        return [
            *checks,
            Check("google_login", FAIL, clean(exc), "Run `garmin-sync auth google login`"),
        ]
    if token is None:
        return [
            *checks,
            Check(
                "google_login",
                FAIL,
                "not logged in to Google",
                "Run `garmin-sync auth google login`",
            ),
        ]
    if not is_private(home.google_token_file):
        checks.append(
            Check(
                "google_token_permissions",
                WARN,
                "token file is readable by others",
                f"chmod 600 {home.google_token_file}",
            )
        )
    if missing := token.missing_scopes():
        checks.append(
            Check(
                "google_scopes",
                FAIL,
                "missing scopes: " + ", ".join(missing),
                "Run `garmin-sync auth google login` and tick every permission box",
            )
        )
    if not online:
        return [*checks, Check("google_login", OK, "token present (not verified online)")]
    auth = google_auth.GoogleAuth(client, store, http)
    try:
        GoogleClient(auth, http).identity()
    except google_auth.GoogleAuthExpired as exc:
        checks.append(
            Check("google_login", FAIL, clean(exc), "Run `garmin-sync auth google login`")
        )
    except google_auth.GoogleAuthError as exc:
        checks.append(
            Check("google_login", FAIL, clean(exc), "Run `garmin-sync auth google login`")
        )
    except GoogleApiError as exc:
        checks.append(
            Check(
                "google_login",
                FAIL,
                clean(exc),
                "Enable the Google Health API for your project and add yourself as a test user",
            )
        )
    else:
        checks.append(Check("google_login", OK, "Google login works"))
    return checks


def _check_archive(home: AppHome, today: date) -> Check:
    if not home.archive_file.exists():
        return Check("archive", WARN, "no archive yet", "garmin-sync pull --days 30")
    with Archive.open(home.archive_file) as archive:
        counts = archive.raw_counts()
        latest = archive.latest_key("user_summary")
    if latest is None:
        return Check("archive", WARN, "archive has no daily data", "garmin-sync pull --days 30")
    age = today - date.fromisoformat(latest)
    detail = f"{sum(counts.values())} rows, newest day {latest}"
    if age > _STALE_AFTER:
        return Check(
            "archive", WARN, f"{detail} ({age.days} days old)", "garmin-sync pull --days 7"
        )
    return Check("archive", OK, detail)
