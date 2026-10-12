"""Garmin login handling: token resumption, credential flow, file permissions."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest
from garminconnect import (
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

from garmin_sync import garmin_source as gs
from garmin_sync.paths import AppHome


class FakeLibrary:
    """Replaces ``garminconnect.Garmin``; records how it was constructed and used."""

    instances: list[FakeLibrary] = []
    login_error: Exception | None = None
    token_mode = 0o644  # deliberately lax, like a library that ignores umask
    write_tokens = True

    def __init__(
        self, email: str | None = None, password: str | None = None, **kwargs: object
    ) -> None:
        self.email, self.password, self.kwargs = email, password, kwargs
        self.tokenstore: str | None = None
        FakeLibrary.instances.append(self)

    def login(self, tokenstore: str | None = None) -> tuple[None, None]:
        if FakeLibrary.login_error:
            raise FakeLibrary.login_error
        self.tokenstore = tokenstore
        if self.email and FakeLibrary.write_tokens:  # a credential login writes tokens
            token_file = Path(tokenstore) / "garmin_tokens.json"
            token_file.write_text(json.dumps({"di_token": "x"}))
            token_file.chmod(FakeLibrary.token_mode)
        return None, None


@pytest.fixture(autouse=True)
def fake_library(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeLibrary.instances = []
    FakeLibrary.login_error = None
    FakeLibrary.write_tokens = True
    monkeypatch.setattr(gs, "Garmin", FakeLibrary)


def test_resuming_without_saved_tokens_asks_for_an_interactive_login(home: AppHome) -> None:
    with pytest.raises(gs.AuthRequired, match="auth garmin"):
        gs.login_from_tokens(home)
    assert FakeLibrary.instances == []  # never even constructs a client


def test_resuming_uses_tokens_only_never_credentials(home: AppHome) -> None:
    (home.garmin_token_dir / "garmin_tokens.json").write_text("{}")
    gs.login_from_tokens(home)
    (client,) = FakeLibrary.instances
    assert client.email is None and client.password is None
    assert client.tokenstore == str(home.garmin_token_dir)


def test_rejected_saved_session_asks_for_a_new_login(home: AppHome) -> None:
    (home.garmin_token_dir / "garmin_tokens.json").write_text("{}")
    FakeLibrary.login_error = GarminConnectAuthenticationError("revoked")
    with pytest.raises(gs.AuthRequired, match="invalid"):
        gs.login_from_tokens(home)


def test_interactive_login_passes_credentials_and_mfa_callback_and_locks_down_tokens(
    home: AppHome,
) -> None:
    gs.login_interactive(home, "me@example.com", "pw", prompt_mfa=lambda: "123456")
    (client,) = FakeLibrary.instances
    assert (client.email, client.password) == ("me@example.com", "pw")
    assert client.kwargs["prompt_mfa"]() == "123456"
    token_file = home.garmin_token_dir / "garmin_tokens.json"
    assert (
        stat.S_IMODE(token_file.stat().st_mode) == 0o600
    )  # tightened even though the library wrote 0644


def test_the_password_is_never_written_to_disk(home: AppHome) -> None:
    gs.login_interactive(home, "me@example.com", "correct-horse-battery", prompt_mfa=lambda: "1")
    for path in home.root.rglob("*"):
        if path.is_file():
            assert "correct-horse-battery" not in path.read_text(errors="ignore")


def test_a_login_that_saved_no_tokens_is_not_reported_as_success(home: AppHome) -> None:
    """Regression: the library swallows token-write failures; login used to claim success."""
    FakeLibrary.write_tokens = False
    with pytest.raises(gs.AuthRequired, match="could not be saved"):
        gs.login_interactive(home, "me@example.com", "pw", prompt_mfa=lambda: "1")


def test_the_region_choice_is_remembered_and_reused(home: AppHome) -> None:
    gs.login_interactive(home, "me@example.com", "pw", prompt_mfa=lambda: "1", is_cn=True)
    assert FakeLibrary.instances[0].kwargs["is_cn"] is True
    gs.login_from_tokens(home)
    assert FakeLibrary.instances[1].kwargs["is_cn"] is True


def test_resuming_from_an_in_memory_token_blob_writes_nothing(home: AppHome) -> None:
    before = sorted(home.root.rglob("*"))
    gs.login_from_token_json('{"di_token": "x"}')
    (client,) = FakeLibrary.instances
    assert client.tokenstore == '{"di_token": "x"}'
    assert sorted(home.root.rglob("*")) == before


@pytest.mark.parametrize(
    "blob", ["   ", "", "/etc/passwd", "~/.garmin/tokens", "tokens.json", "[]"]
)
def test_a_token_blob_that_is_not_json_is_refused_without_calling_garmin(blob: str) -> None:
    """The library treats any string that is not JSON as a path to read tokens from."""
    with pytest.raises(gs.AuthRequired):
        gs.login_from_token_json(blob)
    assert FakeLibrary.instances == []


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (GarminConnectTooManyRequestsError("429"), gs.RateLimited),
        (GarminConnectConnectionError("dns"), gs.GarminUnreachable),
    ],
)
def test_other_library_errors_are_translated(
    home: AppHome, error: Exception, expected: type
) -> None:
    (home.garmin_token_dir / "garmin_tokens.json").write_text("{}")
    FakeLibrary.login_error = error
    with pytest.raises(expected):
        gs.login_from_tokens(home)


def test_refresh_token_of_reads_only_the_refresh_token() -> None:
    assert gs.refresh_token_of('{"di_refresh_token": "abc"}') == "abc"
    for bad in (None, "", "not json", "[]", '{"di_refresh_token": 5}', "{}"):
        assert gs.refresh_token_of(bad) is None


def test_token_snapshot_uses_the_client_dump_when_available() -> None:
    class Api:
        client = type("C", (), {"dumps": staticmethod(lambda: '{"a":1}')})()

    assert gs.GarminSource(Api()).token_snapshot() == '{"a":1}'
    assert gs.GarminSource(object()).token_snapshot() is None
