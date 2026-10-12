"""OAuth for the Google Health API: PKCE, state, host allowlist, refresh, secrecy."""

from __future__ import annotations

import base64
import hashlib
import json
import stat
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from garmin_sync.paths import read_json
from garmin_sync.sinks import google_auth as ga
from tests.fakes_http import FakeHttp, FakeResponse

SECRET = "sup3r-secret-value"  # pragma: allowlist secret (fake test value)
ACCESS = "ya29.fake-access-token"
REFRESH = "1//fake-refresh-token"


def write_client(path: Path, **section: object) -> Path:
    body = {
        "client_id": "123.apps.googleusercontent.com",
        "client_secret": SECRET,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["https://www.google.com"],
        **section,
    }
    path.write_text(json.dumps({"web": body}))
    return path


@pytest.fixture
def client(tmp_path: Path) -> ga.OAuthClient:
    return ga.load_client(write_client(tmp_path / "client.json"))


def token_reply(**overrides: object) -> FakeResponse:
    body = {
        "access_token": ACCESS,
        "refresh_token": REFRESH,
        "expires_in": 3600,
        "scope": " ".join(ga.SCOPES),
    }
    return FakeResponse(200, {**body, **overrides})


def begin(client: ga.OAuthClient, tmp_path: Path, now: float = 1000.0) -> tuple[str, dict]:
    pending = tmp_path / "pending.json"
    url = ga.start_authorization(client, pending, clock=lambda: now)
    return url, read_json(pending)


def redirect(state: str, code: str = "4/abc") -> str:
    return "https://www.google.com/?" + urlencode({"code": code, "state": state, "scope": "x"})


# -- client file ------------------------------------------------------------------------------


def test_load_client_reads_web_and_installed_sections(tmp_path: Path) -> None:
    web = ga.load_client(write_client(tmp_path / "a.json"))
    assert web.client_id.startswith("123.") and web.client_secret == SECRET
    path = tmp_path / "b.json"
    path.write_text(json.dumps({"installed": {"client_id": "x"}}))
    assert ga.load_client(path).client_id == "x"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("token_uri", "https://evil.example.com/token"),
        ("token_uri", "http://oauth2.googleapis.com/token"),
        ("token_uri", "https://oauth2.googleapis.com.evil.example/token"),
        ("token_uri", "https://oauth2.googleapis.com/token/../other"),
        ("auth_uri", "https://evil.example.com/auth"),
        ("auth_uri", "https://accounts.google.com.evil.example/auth"),
    ],
)
def test_load_client_refuses_endpoints_other_than_googles_exact_ones(
    tmp_path: Path, field: str, value: str
) -> None:
    with pytest.raises(ga.GoogleAuthError, match=field):
        ga.load_client(write_client(tmp_path / "c.json", **{field: value}))


@pytest.mark.parametrize(
    "token_uri",
    [
        # urlsplit() reads the host as oauth2.googleapis.com, but requests connects to evil.example
        "https://evil.example\\@oauth2.googleapis.com/token",
        "https://oauth2.googleapis.com\\@evil.example/token",
        "https://oauth2.googleapis.com@evil.example/token",
        "https://oauth2.googleapis.com:443@evil.example/token",
        "https://oauth2.googleapis.com#@evil.example/token",
    ],
)
def test_the_backslash_and_userinfo_host_confusion_is_refused(
    tmp_path: Path, token_uri: str
) -> None:
    """Regression (HIGH): a host allow-list on the parsed URL could be bypassed."""
    with pytest.raises(ga.GoogleAuthError, match="token_uri"):
        ga.load_client(write_client(tmp_path / "c.json", token_uri=token_uri))


def test_token_requests_go_to_the_pinned_endpoint_whatever_the_file_says(tmp_path: Path) -> None:
    client = ga.load_client(write_client(tmp_path / "a.json"))
    http = FakeHttp(queue=[token_reply()])
    auth = ga.GoogleAuth(client, ga.MemoryTokenStore(ga.GoogleToken.from_refresh_token("r")), http)
    auth.refresh()
    assert http.calls[0][1] == ga.TOKEN_URI == "https://oauth2.googleapis.com/token"


def test_the_legacy_auth_uri_in_console_downloads_is_accepted_but_not_used(tmp_path: Path) -> None:
    client = ga.load_client(write_client(tmp_path / "a.json"))  # file has .../o/oauth2/auth
    url = ga.start_authorization(client, tmp_path / "pending.json")
    assert url.startswith(ga.AUTH_URI + "?")


@pytest.mark.parametrize("field", ["client_id", "client_secret"])
@pytest.mark.parametrize("value", ["has space", "new\nline", "x" * 600, 5, ["a"]])
def test_malformed_client_credentials_are_refused(
    tmp_path: Path, field: str, value: object
) -> None:
    with pytest.raises(ga.GoogleAuthError):
        ga.load_client(write_client(tmp_path / "c.json", **{field: value}))


def test_load_client_requires_the_documented_redirect_uri(tmp_path: Path) -> None:
    with pytest.raises(ga.GoogleAuthError, match="redirect"):
        ga.load_client(write_client(tmp_path / "c.json", redirect_uris=["http://localhost"]))


@pytest.mark.parametrize("content", ["not json", "[]", '{"web": {}}', '{"other": 1}'])
def test_load_client_rejects_other_files_without_echoing_them(tmp_path: Path, content: str) -> None:
    path = tmp_path / "c.json"
    path.write_text(content)
    with pytest.raises(ga.GoogleAuthError, match="not a Google OAuth client"):
        ga.load_client(path)


def test_parse_client_validates_a_dict_without_touching_disk() -> None:
    client = ga.parse_client({"web": {"client_id": "abc", "client_secret": "s"}})
    assert (client.client_id, client.client_secret) == ("abc", "s")
    with pytest.raises(ga.GoogleAuthError):
        ga.parse_client("not a dict")


@pytest.mark.parametrize("uri", [[], {}, 5, None, ["https://oauth2.googleapis.com/token"]])
def test_a_non_string_endpoint_is_a_clean_refusal_not_a_crash(tmp_path: Path, uri: object) -> None:
    """Regression: an unhashable `token_uri` raised TypeError instead of being refused."""
    for field in ("token_uri", "auth_uri"):
        with pytest.raises(ga.GoogleAuthError, match=field):
            ga.load_client(write_client(tmp_path / "c.json", **{field: uri}))


def test_the_write_path_scopes_are_a_subset_that_needs_no_nutrition_access() -> None:
    assert set(ga.WRITE_PATH_SCOPES) < set(ga.SCOPES)
    assert not [s for s in ga.WRITE_PATH_SCOPES if "nutrition" in s]
    token = ga.GoogleToken("a", "r", 0, scope=" ".join(ga.WRITE_PATH_SCOPES))
    assert token.missing_scopes(ga.WRITE_PATH_SCOPES) == [] and token.missing_scopes()


def test_load_client_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ga.GoogleAuthError, match="not found"):
        ga.load_client(tmp_path / "nope.json")


# -- authorization start ----------------------------------------------------------------------


def test_authorization_url_has_pkce_state_scopes_and_offline_access(client, tmp_path) -> None:
    url, pending = begin(client, tmp_path)
    query = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
    assert url.startswith("https://accounts.google.com/")
    assert query["redirect_uri"] == "https://www.google.com"
    assert query["response_type"] == "code" and query["access_type"] == "offline"
    assert query["code_challenge_method"] == "S256"
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(pending["verifier"].encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    assert query["code_challenge"] == expected
    assert query["state"] == pending["state"]
    assert set(query["scope"].split()) == set(ga.SCOPES)
    assert "client_secret" not in url and SECRET not in url


def test_every_scope_is_a_googlehealth_scope_and_reads_accompany_writes() -> None:
    assert all(s.startswith("https://www.googleapis.com/auth/googlehealth.") for s in ga.SCOPES)
    writes = {s.removesuffix(".writeonly") for s in ga.SCOPES if s.endswith(".writeonly")}
    reads = {s.removesuffix(".readonly") for s in ga.SCOPES if s.endswith(".readonly")}
    assert writes == reads  # needed to see data Garmin Connect already sent


def test_each_login_attempt_gets_fresh_secrets_and_pending_state_is_private(
    client, tmp_path
) -> None:
    _, first = begin(client, tmp_path)
    _, second = begin(client, tmp_path)
    assert first["state"] != second["state"] and first["verifier"] != second["verifier"]
    assert stat.S_IMODE((tmp_path / "pending.json").stat().st_mode) == 0o600
    assert len(first["verifier"]) >= 43  # RFC 7636 minimum


# -- authorization finish ---------------------------------------------------------------------


def finish(client, tmp_path, url: str, http: FakeHttp, now: float = 1100.0):
    return ga.finish_authorization(
        client, tmp_path / "pending.json", tmp_path / "token.json", url, http, clock=lambda: now
    )


def test_successful_exchange_stores_private_tokens_and_clears_pending(client, tmp_path) -> None:
    _, pending = begin(client, tmp_path)
    http = FakeHttp(queue=[token_reply()])
    token = finish(client, tmp_path, redirect(pending["state"]), http)
    assert token.refresh_token == REFRESH and token.expires_at == 1100.0 + 3600
    saved = tmp_path / "token.json"
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600
    assert read_json(saved)["refresh_token"] == REFRESH
    assert not (tmp_path / "pending.json").exists()  # single use
    ((url, kwargs),) = http.calls_to("POST")
    assert url == "https://oauth2.googleapis.com/token"
    assert kwargs["data"]["code_verifier"] == pending["verifier"]
    assert kwargs["data"]["redirect_uri"] == "https://www.google.com"
    assert kwargs["timeout"] > 0
    assert kwargs["allow_redirects"] is False  # a redirect must never carry the code elsewhere


def test_state_mismatch_is_rejected_before_any_network_call(client, tmp_path) -> None:
    begin(client, tmp_path)
    http = FakeHttp()
    with pytest.raises(ga.GoogleAuthError, match="State mismatch"):
        finish(client, tmp_path, redirect("attacker-state"), http)
    assert http.calls == []


@pytest.mark.parametrize(
    "url", ["4/just-a-code", "https://www.google.com/?code=abc", "https://www.google.com/?state=s"]
)
def test_url_must_contain_code_and_state(client, tmp_path, url: str) -> None:
    begin(client, tmp_path)
    with pytest.raises(ga.GoogleAuthError, match="full URL"):
        finish(client, tmp_path, url, FakeHttp())


def test_a_non_ascii_state_is_a_clean_error_not_a_crash(client, tmp_path) -> None:
    """Regression: compare_digest raised TypeError on non-ASCII text."""
    begin(client, tmp_path)
    with pytest.raises(ga.GoogleAuthError, match="State mismatch"):
        finish(client, tmp_path, redirect("stäte-ünïcode"), FakeHttp())


@pytest.mark.parametrize(
    "pasted", ["http://[::1", "https://www.google.com:99999999/?code=1&state=2"]
)
def test_an_unparseable_address_is_a_clean_error(client, tmp_path, pasted: str) -> None:
    begin(client, tmp_path)
    with pytest.raises(ga.GoogleAuthError):
        finish(client, tmp_path, pasted, FakeHttp())


def test_quotes_around_the_pasted_address_are_tolerated(client, tmp_path) -> None:
    _, pending = begin(client, tmp_path)
    http = FakeHttp(queue=[token_reply()])
    token = finish(client, tmp_path, f"'{redirect(pending['state'])}'", http)
    assert token.refresh_token == REFRESH


def test_a_corrupt_pending_file_is_a_clean_error(client, tmp_path) -> None:
    (tmp_path / "pending.json").write_text("{not json")
    with pytest.raises(ga.GoogleAuthError, match="unreadable"):
        finish(client, tmp_path, "x", FakeHttp())


def test_denied_consent_is_reported(client, tmp_path) -> None:
    begin(client, tmp_path)
    with pytest.raises(ga.GoogleAuthError, match="access_denied"):
        finish(client, tmp_path, "https://www.google.com/?error=access_denied", FakeHttp())


def test_expired_and_missing_pending_logins(client, tmp_path) -> None:
    with pytest.raises(ga.GoogleAuthError, match="No login in progress"):
        finish(client, tmp_path, "x", FakeHttp())
    _, pending = begin(client, tmp_path, now=0)
    with pytest.raises(ga.GoogleAuthError, match="expired"):
        finish(client, tmp_path, redirect(pending["state"]), FakeHttp(), now=ga.PENDING_TTL_S + 1)
    assert not (tmp_path / "pending.json").exists()


def test_missing_refresh_token_is_explained(client, tmp_path) -> None:
    _, pending = begin(client, tmp_path)
    http = FakeHttp(queue=[FakeResponse(200, {"access_token": ACCESS, "expires_in": 3600})])
    with pytest.raises(ga.GoogleAuthError, match="no refresh token"):
        finish(client, tmp_path, redirect(pending["state"]), http)
    assert not (tmp_path / "token.json").exists()


def test_unreachable_google_is_a_clean_error(client, tmp_path) -> None:
    _, pending = begin(client, tmp_path)

    def down(*_a, **_k):
        raise ConnectionError("secret-host-detail")

    http = FakeHttp(handler=down)
    with pytest.raises(ga.GoogleAuthError, match="Could not reach Google"):
        finish(client, tmp_path, redirect(pending["state"]), http)


def test_failed_exchange_reports_only_the_error_code(client, tmp_path) -> None:
    _, pending = begin(client, tmp_path)
    http = FakeHttp(
        queue=[FakeResponse(400, {"error": "invalid_grant", "error_description": f"bad {SECRET}"})]
    )
    with pytest.raises(ga.GoogleAuthError) as caught:
        finish(client, tmp_path, redirect(pending["state"]), http)
    assert "invalid_grant" in str(caught.value) and SECRET not in str(caught.value)


# -- using and refreshing tokens --------------------------------------------------------------


def stored_auth(
    client, tmp_path, http: FakeHttp, expires_at: float, now: float = 5000.0
) -> ga.GoogleAuth:
    path = tmp_path / "token.json"
    path.write_text(
        json.dumps(
            {"access_token": "old", "refresh_token": REFRESH, "expires_at": expires_at, "scope": ""}
        )
    )
    return ga.GoogleAuth(client, ga.FileTokenStore(path), http, clock=lambda: now)


def test_valid_token_is_used_without_network(client, tmp_path) -> None:
    http = FakeHttp()
    assert stored_auth(client, tmp_path, http, expires_at=9999).access_token() == "old"
    assert http.calls == []


def test_expiring_token_is_refreshed_and_persisted_privately(client, tmp_path) -> None:
    http = FakeHttp(queue=[FakeResponse(200, {"access_token": "new", "expires_in": 3600})])
    auth = stored_auth(client, tmp_path, http, expires_at=5030)  # inside the safety margin
    assert auth.access_token() == "new"
    saved = read_json(tmp_path / "token.json")
    assert (
        saved["access_token"] == "new" and saved["refresh_token"] == REFRESH
    )  # kept when not rotated
    assert stat.S_IMODE((tmp_path / "token.json").stat().st_mode) == 0o600
    assert http.calls_to("POST")[0][1]["data"]["grant_type"] == "refresh_token"
    assert http.calls_to("POST")[0][1]["allow_redirects"] is False
    auth.access_token()
    assert len(http.calls) == 1  # cached afterwards


def test_rejected_refresh_token_asks_for_a_new_login(client, tmp_path) -> None:
    http = FakeHttp(queue=[FakeResponse(400, {"error": "invalid_grant"})])
    with pytest.raises(ga.GoogleAuthExpired, match="7 days"):
        stored_auth(client, tmp_path, http, expires_at=0).access_token()


def test_not_logged_in(client, tmp_path) -> None:
    auth = ga.GoogleAuth(client, ga.FileTokenStore(tmp_path / "none.json"), FakeHttp())
    with pytest.raises(ga.GoogleAuthError, match="auth google login"):
        auth.access_token()


@pytest.mark.parametrize(
    "content", ["{not json", "[]", '{"access_token": "a"}', '{"expires_at": "x"}']
)
def test_a_corrupt_token_file_is_a_clean_error(client, tmp_path, content: str) -> None:
    (tmp_path / "token.json").write_text(content)
    auth = ga.GoogleAuth(client, ga.FileTokenStore(tmp_path / "token.json"), FakeHttp())
    with pytest.raises(ga.GoogleAuthError, match="unreadable"):
        auth.access_token()


def test_a_memory_token_store_works_from_a_bare_refresh_token_and_writes_no_file(
    client, tmp_path
) -> None:
    http = FakeHttp(queue=[FakeResponse(200, {"access_token": "minted", "expires_in": 3600})])
    store = ga.MemoryTokenStore(ga.GoogleToken.from_refresh_token(REFRESH))
    auth = ga.GoogleAuth(client, store, http, clock=lambda: 10.0)
    assert auth.access_token() == "minted"  # no access token yet, so the first use refreshes
    assert store.load().refresh_token == REFRESH
    assert list(tmp_path.iterdir()) == [] or all(
        p.name == "client.json" for p in tmp_path.iterdir()
    )


def test_a_malformed_token_response_is_a_clean_error(client) -> None:
    http = FakeHttp(queue=[FakeResponse(200, {"no_access_token": 1})])
    auth = ga.GoogleAuth(client, ga.MemoryTokenStore(ga.GoogleToken.from_refresh_token("r")), http)
    with pytest.raises(ga.GoogleAuthError, match="expected form"):
        auth.refresh()


def test_missing_scopes_are_listed(client, tmp_path) -> None:
    token = ga.GoogleToken("a", "r", 0, scope=ga.SCOPES[0])
    assert "sleep.writeonly" in token.missing_scopes()
    assert ga.GoogleToken("a", "r", 0, scope=" ".join(ga.SCOPES)).missing_scopes() == []


def test_secrets_never_appear_in_reprs(client) -> None:
    token = ga.GoogleToken(ACCESS, REFRESH, 1.0, "s")
    for obj in (client, token):
        text = repr(obj) + str(obj)
        assert SECRET not in text and ACCESS not in text and REFRESH not in text
