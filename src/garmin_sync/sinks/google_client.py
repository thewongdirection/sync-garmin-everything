"""HTTP access to the Google Health API: auth header, token refresh, retries, error mapping.

Only the fixed API origin is ever contacted; redirects are never followed, so a response
cannot send the bearer token anywhere else.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Iterable
from typing import Any, Protocol

from garmin_sync.sanitize import clean
from garmin_sync.sinks.google_auth import GoogleAuth

BASE_URL = "https://health.googleapis.com"
API_ROOT = "/v4"
_TIMEOUT_S = 30
_MAX_RETRY_WAIT_S = 60.0
_ERROR_TEXT_LIMIT = 200


class GoogleApiError(Exception):
    def __init__(self, status: int, message: str, reason: str = "") -> None:
        super().__init__(f"HTTP {status}: {message}" if status else message)
        self.status = status
        #: Google's canonical error name (``ALREADY_EXISTS``, ``ABORTED``...), when it sent one.
        self.reason = reason


class GooglePermissionError(GoogleApiError):
    """HTTP 403: the token lacks a scope, or the project is not enabled/onboarded."""


class HttpClient(Protocol):
    def request(self, method: str, url: str, **kwargs: Any) -> Any: ...


class GoogleClient:
    """Authenticated JSON requests with bounded, method-aware retries."""

    def __init__(
        self,
        auth: GoogleAuth,
        http: HttpClient,
        *,
        max_retries: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._auth = auth
        self._http = http
        self._max_retries = max_retries
        self._sleep = sleep

    def get(self, path: str, **params: Any) -> dict[str, Any]:
        return self._request("GET", path, params=params or None)

    def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", path, json=body)

    def missing_scopes(self, required: Iterable[str]) -> list[str]:
        """Which of ``required`` the current login was not granted (refreshing it if needed)."""
        self._auth.access_token()  # make sure the scope list is from the live token
        granted = set(self._auth.token().scope.split())
        return [scope for scope in required if scope not in granted]

    def identity(self) -> dict[str, Any]:
        """The signed-in user's identity resource; a cheap authenticated read for health checks."""
        return self.get(f"{API_ROOT}/users/me/identity")

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        """One logical request.

        * 401 triggers one token refresh and a repeat that does not use a retry slot.
        * Reads retry on 429, 5xx and transport failures. A create retries only on 429, which
          means "not processed": after a 5xx or a timeout it may or may not have landed, and
          whether a repeat is harmless depends on Google honouring our data point id. So that
          case is reported as a failure, and the next run's duplicate check settles it.
        """
        _check_path(path)
        retriable = method == "GET"
        refreshed = False
        attempt = 0
        while True:
            headers = {"Authorization": f"Bearer {self._auth.access_token()}"}
            try:
                response = self._http.request(
                    method,
                    f"{BASE_URL}{path}",
                    headers=headers,
                    timeout=_TIMEOUT_S,
                    allow_redirects=False,
                    **kwargs,
                )
            except OSError as exc:  # requests' exceptions derive from OSError
                if not retriable or attempt >= self._max_retries:
                    raise GoogleApiError(0, f"network error: {type(exc).__name__}") from None
                self._sleep(_backoff(attempt))
                attempt += 1
                continue
            status = response.status_code
            if status == 401 and not refreshed:
                self._auth.refresh()
                refreshed = True
                continue
            may_retry = status == 429 or (retriable and status >= 500)
            if may_retry and attempt < self._max_retries:
                self._sleep(_retry_delay(response, attempt))
                attempt += 1
                continue
            if status >= 400:
                message, reason = _api_error(response)
                if status == 403:
                    raise GooglePermissionError(status, message, reason)
                raise GoogleApiError(status, message, reason)
            if not 200 <= status < 300:  # redirects are never followed, so a 3xx is a failure
                raise GoogleApiError(status, "unexpected response")
            return _json(response)


def _check_path(path: str) -> None:
    """Paths are built from constants; refuse anything that could change where the URL points."""
    if (
        not path.startswith("/v4/")
        or any(bad in path for bad in ("\\", "@", "//", "..", "?", "#"))
        or not path.isprintable()
        or " " in path
    ):
        raise ValueError("unsafe API path")


def _json(response: Any) -> dict[str, Any]:
    """The JSON object in a successful response. An empty or non-object body is a failure: reading
    it as "no data" would let a duplicate check pass on a response that said nothing."""
    if not response.content:
        raise GoogleApiError(response.status_code, "empty response")
    try:
        data = response.json()
    except ValueError:
        raise GoogleApiError(response.status_code, "response was not JSON") from None
    if not isinstance(data, dict):
        raise GoogleApiError(response.status_code, "response was not a JSON object")
    return data


def _backoff(attempt: int) -> float:
    return min(2.0**attempt, _MAX_RETRY_WAIT_S)


def _retry_delay(response: Any, attempt: int) -> float:
    try:
        wait = float(response.headers.get("Retry-After", ""))
    except (TypeError, ValueError, AttributeError):
        return _backoff(attempt)
    if not math.isfinite(wait):  # "nan" parses as a float, and sleeping on it raises
        return _backoff(attempt)
    return min(max(wait, 0.0), _MAX_RETRY_WAIT_S)


def _api_error(response: Any) -> tuple[str, str]:
    """``(message, canonical status)`` from Google's error body, both sanitized."""
    try:
        error = response.json().get("error", {})
        message, reason = error.get("message", ""), error.get("status", "")
    except (ValueError, AttributeError):
        message = reason = ""
    return clean(message or "request failed", limit=_ERROR_TEXT_LIMIT), clean(reason, limit=40)
