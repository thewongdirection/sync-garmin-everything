"""Authenticated, throttled access to Garmin Connect via python-garminconnect.

Login is split in two on purpose. :func:`login_interactive` needs the password and an
MFA code, so it must run in the user's own terminal. :func:`login_from_tokens` and
:func:`login_from_token_json` are non-interactive and are what ``pull`` and the cloud
routine use; they never prompt and never see a password.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

from garmin_sync.paths import PRIVATE_FILE_MODE, AppHome, read_json, write_private_json
from garmin_sync.sanitize import clean

# A file name, not a secret (hence the scanner suppressions).
TOKEN_FILENAME = "garmin_tokens.json"  # noqa: S105  # nosec B105
REGION_FILENAME = "region.json"


class AuthRequired(Exception):
    """No valid Garmin session; the user must run ``garmin-sync auth garmin`` in a terminal."""


class RateLimited(Exception):
    """Garmin returned HTTP 429. Progress is archived; retry later."""


class GarminUnreachable(Exception):
    """Garmin could not be contacted (network, blocked address, outage)."""


class GarminSource:
    """Calls Garmin methods with a polite delay and maps auth/rate-limit errors."""

    def __init__(
        self,
        api: Any,
        *,
        delay_s: float = 0.3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._api = api
        self._delay_s = delay_s
        self._sleep = sleep
        self._first_call = True

    def call(self, method: str, *args: Any) -> Any:
        if not self._first_call:
            self._sleep(self._delay_s)
        self._first_call = False
        try:
            return getattr(self._api, method)(*args)
        except GarminConnectTooManyRequestsError as exc:
            raise RateLimited("Garmin rate limit hit (HTTP 429)") from exc
        except GarminConnectAuthenticationError as exc:
            raise AuthRequired("Garmin session rejected; run `garmin-sync auth garmin`") from exc

    def probe(self) -> None:
        """One cheap authenticated call. Raises AuthRequired, RateLimited or GarminUnreachable."""
        try:
            self.call("get_user_profile")
        except GarminConnectConnectionError as exc:
            raise GarminUnreachable(f"Garmin unreachable: {clean(exc)}") from exc

    def download_original_activity(self, activity_id: str) -> bytes:
        """Original FIT file (zipped) for an activity."""
        fmt = Garmin.ActivityDownloadFormat.ORIGINAL
        data = self.call("download_activity", activity_id, fmt)
        if not isinstance(data, bytes | bytearray):
            raise TypeError("unexpected download payload type")
        return bytes(data)

    def token_snapshot(self) -> str | None:
        """The current session tokens as JSON (secret: never print or log), if available."""
        client = getattr(self._api, "client", None)
        dumps = getattr(client, "dumps", None)
        return dumps() if callable(dumps) else None


def refresh_token_of(token_json: str | None) -> str | None:
    """The refresh-token value inside a token JSON blob, for rotation checks only."""
    if not token_json:
        return None
    try:
        value = json.loads(token_json).get("di_refresh_token")
    except (ValueError, AttributeError):
        return None
    return value if isinstance(value, str) else None


def _token_dir(home: AppHome) -> str:
    return str(home.garmin_token_dir)


def _is_cn(home: AppHome) -> bool:
    region = home.garmin_token_dir / REGION_FILENAME
    if not region.exists():
        return False
    try:
        return bool(read_json(region).get("is_cn"))
    except (ValueError, AttributeError):
        return False


def _login_with(api: Garmin, tokenstore: str) -> None:
    """Run the library login, translating its exceptions into ours."""
    try:
        api.login(tokenstore=tokenstore)
    except GarminConnectAuthenticationError as exc:
        raise AuthRequired("Garmin session is invalid or expired") from exc
    except GarminConnectTooManyRequestsError as exc:
        raise RateLimited("Garmin rate limit hit (HTTP 429)") from exc
    except GarminConnectConnectionError as exc:
        raise GarminUnreachable(f"Garmin unreachable: {clean(exc)}") from exc


def login_from_tokens(home: AppHome, *, delay_s: float = 0.3) -> GarminSource:
    """Resume the session saved under ``home``. Raises :class:`AuthRequired` if there is none."""
    if not (home.garmin_token_dir / TOKEN_FILENAME).exists():
        raise AuthRequired(
            "Not logged in to Garmin; run `garmin-sync auth garmin` in your terminal"
        )
    api = Garmin(is_cn=_is_cn(home))
    _login_with(api, _token_dir(home))
    return GarminSource(api, delay_s=delay_s)


def login_from_token_json(
    token_json: str, *, is_cn: bool = False, delay_s: float = 0.3
) -> GarminSource:
    """Resume a session from an in-memory token blob (cloud routine); nothing is written to disk."""
    if not token_json.strip().startswith("{"):
        # The library treats any other string as a path to read tokens from.
        raise AuthRequired("No Garmin tokens provided (expected the token JSON)")
    api = Garmin(is_cn=is_cn)
    _login_with(api, token_json)
    return GarminSource(api, delay_s=delay_s)


def login_interactive(
    home: AppHome,
    email: str,
    password: str,
    prompt_mfa: Callable[[], str],
    *,
    is_cn: bool = False,
) -> GarminSource:
    """Log in with credentials and persist tokens (never the password) under ``home``."""
    home.ensure()
    api = Garmin(email=email, password=password, prompt_mfa=prompt_mfa, is_cn=is_cn)
    _login_with(api, _token_dir(home))
    token_file = home.garmin_token_dir / TOKEN_FILENAME
    if not token_file.exists():
        # The library swallows write failures (e.g. a symlinked path); do not claim success.
        raise AuthRequired(
            "Login worked but the tokens could not be saved; choose a --home without symlinks"
        )
    token_file.chmod(PRIVATE_FILE_MODE)
    write_private_json(home.garmin_token_dir / REGION_FILENAME, {"is_cn": is_cn})
    return GarminSource(api)
