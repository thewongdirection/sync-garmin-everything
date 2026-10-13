"""OAuth 2.0 (authorization code + PKCE) for the Google Health API, with no local web server.

Flow, matching Google's documented "Web Server" client with redirect
``https://www.google.com``: ``start_authorization`` returns a URL; the user approves and is
redirected to google.com; they paste the full redirected address back to
``finish_authorization``. Tokens are stored 0600 and are never logged or printed.

Only Google's own endpoints are ever contacted. They are fixed constants here: the downloaded
client file is checked against them but its values are never used, and token requests do not
follow redirects, so a tampered file cannot send the client secret or an authorization code
anywhere else.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qs, urlencode, urlsplit

from garmin_sync.paths import read_json, write_private_json
from garmin_sync.sanitize import clean

SCOPE_PREFIX = "https://www.googleapis.com/auth/googlehealth."
# Read scopes are required as well as write scopes: with write-only scopes Google only
# lets an app see data it wrote itself, which would defeat duplicate detection against
# data Garmin Connect already sent.
SCOPES: tuple[str, ...] = tuple(
    SCOPE_PREFIX + name
    for name in (
        "activity_and_fitness.readonly",
        "activity_and_fitness.writeonly",
        "health_metrics_and_measurements.readonly",
        "health_metrics_and_measurements.writeonly",
        "sleep.readonly",
        "sleep.writeonly",
        "nutrition.readonly",
        "nutrition.writeonly",
    )
)

# What the write path needs: to create sleep, workouts, weight, body fat and hydration, and to read
# what other sources (Garmin Connect's own sync) already stored there.
WRITE_PATH_SCOPES: tuple[str, ...] = tuple(
    SCOPE_PREFIX + f"{group}.{access}"
    for group in ("activity_and_fitness", "health_metrics_and_measurements", "sleep")
    for access in ("readonly", "writeonly")
)

REDIRECT_URI = "https://www.google.com"
AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
# An endpoint URL, not a secret (hence the scanner suppressions).
TOKEN_URI = "https://oauth2.googleapis.com/token"  # noqa: S105  # nosec B105
# What a client file downloaded from the Cloud console may legitimately contain.
_ACCEPTED_AUTH_URIS = frozenset({AUTH_URI, "https://accounts.google.com/o/oauth2/auth"})
_ACCEPTED_TOKEN_URIS = frozenset({TOKEN_URI})
PENDING_TTL_S = 15 * 60
_EXPIRY_MARGIN_S = 60
_HTTP_TIMEOUT_S = 30
_MAX_CLIENT_FIELD = 512


class GoogleAuthError(Exception):
    """Authorization could not be completed; the message is safe to show."""


class GoogleAuthExpired(GoogleAuthError):
    """The refresh token was rejected; run ``garmin-sync auth google login`` again."""


class HttpClient(Protocol):
    """The slice of ``requests.Session`` this module needs."""

    def post(self, url: str, **kwargs: Any) -> Any: ...


@dataclass(frozen=True)
class OAuthClient:
    client_id: str
    client_secret: str = ""

    def __repr__(self) -> str:
        return f"OAuthClient(client_id={self.client_id!r}, client_secret=<redacted>)"


@dataclass(frozen=True)
class GoogleToken:
    access_token: str
    refresh_token: str
    expires_at: float
    scope: str

    def __repr__(self) -> str:
        return f"GoogleToken(expires_at={self.expires_at}, scope={self.scope!r}, <redacted>)"

    def to_json(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "scope": self.scope,
        }

    @classmethod
    def from_json(cls, data: Any) -> GoogleToken:
        try:
            return cls(
                access_token=str(data["access_token"]),
                refresh_token=str(data["refresh_token"]),
                expires_at=float(data["expires_at"]),
                scope=str(data.get("scope", "")),
            )
        except (KeyError, TypeError, ValueError, AttributeError):
            raise GoogleAuthError(
                "The saved Google login is unreadable; run `garmin-sync auth google login` again"
            ) from None

    @classmethod
    def from_refresh_token(cls, refresh_token: str, scope: str = "") -> GoogleToken:
        """A token with nothing but a refresh token: the first use will mint an access token."""
        # The access token is empty on purpose: it forces the first use to refresh.
        return cls(  # nosec B106
            access_token="", refresh_token=refresh_token, expires_at=0.0, scope=scope
        )

    def missing_scopes(self, required: tuple[str, ...] = SCOPES) -> list[str]:
        granted = set(self.scope.split())
        return [s.removeprefix(SCOPE_PREFIX) for s in required if s not in granted]


# -- the OAuth client file -------------------------------------------------------------------


def _client_field(section: dict[str, Any], name: str, *, required: bool) -> str:
    value = section.get(name, "")
    if not isinstance(value, str) or (required and not value):
        raise GoogleAuthError(f"OAuth client file has no usable {name}")
    if len(value) > _MAX_CLIENT_FIELD or any(ch.isspace() or not ch.isprintable() for ch in value):
        raise GoogleAuthError(f"OAuth client file has a malformed {name}")
    return value


def parse_client(raw: Any, source: str = "OAuth client file") -> OAuthClient:
    """Validate a Google Cloud "OAuth client" JSON document."""
    section = raw.get("web") or raw.get("installed") if isinstance(raw, dict) else None
    if not isinstance(section, dict):
        raise GoogleAuthError(f"{source} is not a Google OAuth client JSON file")
    client = OAuthClient(
        client_id=_client_field(section, "client_id", required=True),
        client_secret=_client_field(section, "client_secret", required=False),
    )
    # A tampered file must not be able to point anything at another server. Exact string
    # comparison: URL parsers disagree about hosts, so a parsed hostname proves nothing.
    for name, accepted in (("auth_uri", _ACCEPTED_AUTH_URIS), ("token_uri", _ACCEPTED_TOKEN_URIS)):
        value = section.get(name)
        if name in section and not (isinstance(value, str) and value in accepted):
            raise GoogleAuthError(f"{source} has an unexpected {name}; refusing to use it")
    registered = section.get("redirect_uris")
    if registered is not None and (
        not isinstance(registered, list) or REDIRECT_URI not in registered
    ):
        raise GoogleAuthError(f"Add {REDIRECT_URI} to the client's authorized redirect URIs")
    return client


def load_client(path: Path) -> OAuthClient:
    """Read a Google Cloud "OAuth client" JSON download."""
    if not path.exists():
        raise GoogleAuthError(f"OAuth client file not found: {path}")
    try:
        raw = read_json(path)
    except ValueError:
        raise GoogleAuthError(f"{path.name} is not a Google OAuth client JSON file") from None
    return parse_client(raw, path.name)


# -- authorization ---------------------------------------------------------------------------


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def start_authorization(
    client: OAuthClient, pending_file: Path, clock: Callable[[], float] = time.time
) -> str:
    """Create PKCE + state, persist them privately, and return the URL for the user to open."""
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(32)
    write_private_json(pending_file, {"state": state, "verifier": verifier, "created_at": clock()})
    query = urlencode(
        {
            "client_id": client.client_id,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "scope": " ".join(SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    return f"{AUTH_URI}?{query}"


def _read_pending(pending_file: Path, clock: Callable[[], float]) -> dict[str, Any]:
    if not pending_file.exists():
        raise GoogleAuthError("No login in progress; run `garmin-sync auth google login` first")
    try:
        pending = read_json(pending_file)
        created, _, _ = (
            float(pending["created_at"]),
            str(pending["state"]),
            str(pending["verifier"]),
        )
    except (ValueError, KeyError, TypeError):
        pending_file.unlink(missing_ok=True)
        raise GoogleAuthError("The login in progress is unreadable; start again") from None
    if clock() - created > PENDING_TTL_S:
        pending_file.unlink(missing_ok=True)
        raise GoogleAuthError(
            "Login link expired; start again with `garmin-sync auth google login`"
        )
    return pending


def _parse_redirect(redirected_url: str) -> tuple[str, str]:
    """``(code, state)`` from the pasted address; raises GoogleAuthError with a safe message."""
    try:
        params = parse_qs(urlsplit(redirected_url.strip().strip("'\"")).query)
    except ValueError:
        raise GoogleAuthError("That is not a valid URL; paste the full address bar text") from None
    if "error" in params:
        raise GoogleAuthError(f"Google denied the request: {clean(params['error'][0], limit=60)}")
    code, state = params.get("code", [""])[0], params.get("state", [""])[0]
    if not code or not state:
        raise GoogleAuthError(
            "Paste the full URL from the address bar (it must contain ?code=...&state=...)"
        )
    return code, state


def finish_authorization(
    client: OAuthClient,
    pending_file: Path,
    token_file: Path,
    redirected_url: str,
    http: HttpClient,
    clock: Callable[[], float] = time.time,
) -> GoogleToken:
    """Validate the pasted redirect URL, exchange the code, and store the tokens."""
    pending = _read_pending(pending_file, clock)
    code, returned_state = _parse_redirect(redirected_url)
    # Compare as bytes: compare_digest raises TypeError on non-ASCII text.
    if not hmac.compare_digest(
        returned_state.encode("utf-8"), str(pending["state"]).encode("utf-8")
    ):
        raise GoogleAuthError("State mismatch; this URL is not from the current login attempt")

    data = _token_request(
        client,
        http,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "code_verifier": str(pending["verifier"]),
        },
    )
    if not data.get("refresh_token"):
        raise GoogleAuthError(
            "Google returned no refresh token. Remove this app at "
            "myaccount.google.com/permissions and log in again."
        )
    token = _token_from_response(data, clock, previous=None)
    write_private_json(token_file, token.to_json())
    pending_file.unlink(missing_ok=True)
    return token


def _token_from_response(
    data: dict[str, Any], clock: Callable[[], float], previous: GoogleToken | None
) -> GoogleToken:
    try:
        return GoogleToken(
            access_token=str(data["access_token"]),
            refresh_token=str(
                data.get("refresh_token") or (previous.refresh_token if previous else "")
            ),
            expires_at=clock() + float(data.get("expires_in", 3600)),
            scope=str(data.get("scope", previous.scope if previous else "")),
        )
    except (KeyError, TypeError, ValueError):
        raise GoogleAuthError("Google's token response was not in the expected form") from None


def _token_request(client: OAuthClient, http: HttpClient, fields: dict[str, str]) -> dict[str, Any]:
    body = {"client_id": client.client_id, **fields}
    if client.client_secret:
        body["client_secret"] = client.client_secret
    try:
        response = http.post(TOKEN_URI, data=body, timeout=_HTTP_TIMEOUT_S, allow_redirects=False)
    except OSError as exc:
        raise GoogleAuthError(f"Could not reach Google ({type(exc).__name__})") from None
    try:
        data = response.json()
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    if response.status_code != 200:
        # Only the OAuth error code is surfaced, never the request body or secrets.
        error = clean(data.get("error", f"http_{response.status_code}"), limit=60)
        if error == "invalid_grant" and fields.get("grant_type") == "refresh_token":
            raise GoogleAuthExpired(
                "Google rejected the saved login (apps in Testing mode expire after 7 days). "
                "Run `garmin-sync auth google login` again."
            )
        raise GoogleAuthError(f"Token request failed: {error}")
    return data


# -- stored tokens ---------------------------------------------------------------------------


class TokenStore(Protocol):
    def load(self) -> GoogleToken | None: ...

    def save(self, token: GoogleToken) -> None: ...


class FileTokenStore:
    """Token kept in a private file (local use)."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> GoogleToken | None:
        if not self._path.exists():
            return None
        try:
            return GoogleToken.from_json(read_json(self._path))
        except ValueError:
            raise GoogleAuthError(
                "The saved Google login is unreadable; run `garmin-sync auth google login` again"
            ) from None

    def save(self, token: GoogleToken) -> None:
        write_private_json(self._path, token.to_json())


class MemoryTokenStore:
    """Token held only in memory (cloud routine: nothing is written to disk)."""

    def __init__(self, token: GoogleToken) -> None:
        self._token: GoogleToken | None = token

    def load(self) -> GoogleToken | None:
        return self._token

    def save(self, token: GoogleToken) -> None:
        self._token = token


class GoogleAuth:
    """Holds the stored token and hands out a valid access token, refreshing as needed."""

    def __init__(
        self,
        client: OAuthClient,
        store: TokenStore,
        http: HttpClient,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._client = client
        self._store = store
        self._http = http
        self._clock = clock
        self._token: GoogleToken | None = None

    def _load(self) -> GoogleToken:
        if self._token is None:
            self._token = self._store.load()
            if self._token is None:
                raise GoogleAuthError(
                    "Not logged in to Google; run `garmin-sync auth google login`"
                )
        return self._token

    def token(self) -> GoogleToken:
        return self._load()

    def access_token(self) -> str:
        token = self._load()
        if not token.access_token or token.expires_at - self._clock() < _EXPIRY_MARGIN_S:
            token = self.refresh()
        return token.access_token

    def refresh(self) -> GoogleToken:
        current = self._load()
        data = _token_request(
            self._client,
            self._http,
            {"grant_type": "refresh_token", "refresh_token": current.refresh_token},
        )
        refreshed = _token_from_response(data, self._clock, previous=current)
        self._store.save(refreshed)
        self._token = refreshed
        return refreshed
