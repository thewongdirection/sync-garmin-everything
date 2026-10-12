"""The unattended run: configuration, safety limits, statelessness and what it reveals."""

from __future__ import annotations

import json
import tempfile
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from garminconnect import GarminConnectAuthenticationError, GarminConnectTooManyRequestsError

from garmin_sync import exitcodes, routine
from garmin_sync.garmin_source import (
    AuthRequired,
    GarminSource,
    GarminUnreachable,
    RateLimited,
)
from garmin_sync.sinks import google_auth as ga
from tests.fakes_http import FakeHttp, FakeResponse
from tests.helpers import (
    FakeGarmin,
    activity_summary,
    sleep_payload,
    unsupported,
    user_summary_payload,
    weigh_in_payload,
)

TODAY = date(2024, 5, 3)
NOW = datetime(2024, 5, 3, 15, 0, tzinfo=UTC)
SECRETS = {
    # fake sentinel values, never real credentials
    "GARMIN_SYNC_GARMIN_TOKENS": (  # pragma: allowlist secret
        '{"di_token":"GARMIN-TOKEN-SENTINEL","di_refresh_token":"R1"}'  # pragma: allowlist secret
    ),
    "GARMIN_SYNC_GOOGLE_CLIENT_ID": "client-id.apps.googleusercontent.com",
    "GARMIN_SYNC_GOOGLE_CLIENT_SECRET": "GOOGLE-SECRET-SENTINEL",  # pragma: allowlist secret
    "GARMIN_SYNC_GOOGLE_REFRESH_TOKEN": "GOOGLE-REFRESH-SENTINEL",  # pragma: allowlist secret
}
SENTINELS = ("GARMIN-TOKEN-SENTINEL", "GOOGLE-SECRET-SENTINEL", "GOOGLE-REFRESH-SENTINEL")


class FakeGoogle:
    """The token endpoint plus a tiny in-memory Google Health API that remembers creates."""

    def __init__(self) -> None:
        self.points: dict[str, list[dict]] = {}
        self.denied: set[str] = set()
        self.token_error: str | None = None
        self.http = FakeHttp(handler=self.handle)

    def handle(self, method: str, url: str, kwargs: dict[str, Any]) -> FakeResponse:
        if url == "https://oauth2.googleapis.com/token":
            if self.token_error:
                return FakeResponse(400, {"error": self.token_error})
            return FakeResponse(
                200, {"access_token": "a", "expires_in": 3600, "scope": " ".join(ga.SCOPES)}
            )
        data_type = url.split("/dataTypes/")[1].split("/")[0]
        if data_type in self.denied:
            return FakeResponse(403, {"error": {"message": "not allowed"}})
        if method == "GET":
            return FakeResponse(200, {"dataPoints": self.points.get(data_type, [])})
        body = kwargs["json"]
        known = {p["name"] for p in self.points.get(data_type, [])}
        if body["name"] in known:
            return FakeResponse(409, {"error": {"message": "exists"}})
        self.points.setdefault(data_type, []).append(body)
        return FakeResponse(200, {"name": "operations/1", "done": True})

    @property
    def created(self) -> Counter[str]:
        return Counter({t: len(points) for t, points in self.points.items()})


def garmin_api() -> FakeGarmin:
    return FakeGarmin(
        {
            "get_user_summary": lambda day: user_summary_payload(day),
            "get_sleep_data": lambda day: sleep_payload() if day == "2024-05-02" else {},
            "get_weigh_ins": lambda *_a: weigh_in_payload(),
            "get_hydration_data": lambda day: (
                {"calendarDate": day, "valueInML": 1800.0} if day == "2024-05-01" else {}
            ),
            "get_activities_by_date": [activity_summary()],
            "get_user_profile": {"id": 1},
        }
    )


def login_with(api: FakeGarmin, calls: list[str] | None = None):
    def login(token_json: str, **_kwargs: Any) -> GarminSource:
        if calls is not None:
            calls.append(token_json)
        return GarminSource(api, delay_s=0, sleep=lambda _s: None)

    return login


def run(env: dict[str, str], google: FakeGoogle | None = None, api: FakeGarmin | None = None, **kw):
    google = google or FakeGoogle()
    return routine.run_routine(
        env,
        http=google.http,
        garmin_login=login_with(api or garmin_api()),
        today=TODAY,
        now=NOW,
        **kw,
    )


def env(**extra: str) -> dict[str, str]:
    return {**SECRETS, **extra}


# -- configuration ----------------------------------------------------------------------------


def test_paused_does_nothing_and_touches_no_service() -> None:
    google, calls = FakeGoogle(), []
    result = routine.run_routine(
        env(GARMIN_SYNC_PAUSED="1"),
        http=google.http,
        garmin_login=login_with(garmin_api(), calls),
        today=TODAY,
        now=NOW,
    )
    assert result.status is routine.Status.PAUSED and result.exit_code == exitcodes.OK
    assert google.http.calls == [] and calls == []


def test_missing_variables_are_named_but_never_their_values() -> None:
    partial = {k: v for k, v in SECRETS.items() if k != "GARMIN_SYNC_GOOGLE_REFRESH_TOKEN"}
    result = run(partial)
    assert result.status is routine.Status.MISCONFIGURED and result.exit_code == exitcodes.ERROR
    assert "GARMIN_SYNC_GOOGLE_REFRESH_TOKEN" in result.detail
    assert not any(s in result.detail for s in SENTINELS)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("GARMIN_SYNC_DAYS", "0"),
        ("GARMIN_SYNC_DAYS", "8"),
        ("GARMIN_SYNC_DAYS", "many"),
        ("GARMIN_SYNC_MAX_WRITES", "0"),
        ("GARMIN_SYNC_MAX_WRITES", "5000"),
        ("GARMIN_SYNC_MAX_WRITES", "-1"),
    ],
)
def test_limits_outside_their_bounds_are_refused(name: str, value: str) -> None:
    result = run(env(**{name: value}))
    assert result.status is routine.Status.MISCONFIGURED and name in result.detail


def test_defaults_are_a_dry_run_over_three_days_with_a_write_cap() -> None:
    config = routine.config_from_env(SECRETS)
    assert (config.apply, config.days, config.max_writes) == (False, 3, 50)


@pytest.mark.parametrize("value", ["true", "yes", "2", "on", "0", ""])
def test_only_exactly_one_turns_writing_on(value: str) -> None:
    assert routine.config_from_env(env(GARMIN_SYNC_APPLY=value)).apply is False
    assert routine.config_from_env(env(GARMIN_SYNC_APPLY="1")).apply is True


# -- behaviour --------------------------------------------------------------------------------


def test_the_default_run_is_a_dry_run_that_writes_nothing() -> None:
    google = FakeGoogle()
    result = run(env(), google)
    assert result.status is routine.Status.DRY_RUN and result.exit_code == exitcodes.OK
    assert google.created == Counter()
    assert result.summary["push"]["outcomes"]["would_push"] > 0
    assert not any(call[0] == "POST" and "dataPoints" in call[1] for call in google.http.calls)


def test_apply_writes_every_writable_kind_with_idempotent_names() -> None:
    google = FakeGoogle()
    result = run(env(GARMIN_SYNC_APPLY="1"), google)
    assert result.status is routine.Status.OK, result.detail
    assert set(google.created) == {"sleep", "exercise", "weight", "body-fat", "hydration-log"}
    assert all(
        p["name"].startswith("users/me/dataTypes/") for ps in google.points.values() for p in ps
    )
    assert result.summary["range"] == "2024-05-01..2024-05-03"


def test_a_second_run_with_no_local_state_writes_nothing_more() -> None:
    """The cloud has no disk: de-duplication must come from Google itself."""
    google = FakeGoogle()
    run(env(GARMIN_SYNC_APPLY="1"), google)
    before = google.created.copy()
    second = run(env(GARMIN_SYNC_APPLY="1"), google)
    assert second.status is routine.Status.OK and google.created == before
    outcomes = second.summary["push"]["outcomes"]
    assert outcomes.get("pushed", 0) == 0 and outcomes["duplicate_existing"] == sum(before.values())


def test_the_write_cap_is_a_hard_limit() -> None:
    google = FakeGoogle()
    result = run(env(GARMIN_SYNC_APPLY="1", GARMIN_SYNC_MAX_WRITES="2"), google)
    assert sum(google.created.values()) == 2
    assert result.summary["push"]["outcomes"]["skipped"] >= 1


def test_only_the_writable_endpoints_are_pulled() -> None:
    api = garmin_api()
    run(env(), api=api)
    methods = {name for name, _ in api.calls}
    assert methods <= {
        "get_user_profile",
        "get_user_summary",
        "get_sleep_data",
        "get_weigh_ins",
        "get_hydration_data",
        "get_activities_by_date",
        "get_activity",
        "get_activity_details",
        "get_activity_splits",
        "get_activity_hr_in_timezones",
        "get_activity_weather",
        "get_activity_gear",
    }
    assert "get_stress_data" not in methods and "get_heart_rates" not in methods


def test_nothing_is_left_on_disk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    run(env(GARMIN_SYNC_APPLY="1"))
    assert list(scratch.iterdir()) == []  # the archive lived and died in a temp directory
    assert not (tmp_path / "fakehome").exists()  # and no state directory was created


def test_the_output_never_contains_a_secret() -> None:
    google = FakeGoogle()
    result = run(env(GARMIN_SYNC_APPLY="1"), google)
    shown = json.dumps(result.summary) + result.detail + repr(result)
    assert not any(s in shown for s in SENTINELS)


# -- failure modes ----------------------------------------------------------------------------


def test_an_expired_google_login_is_reported_before_garmin_is_contacted() -> None:
    google, calls = FakeGoogle(), []
    google.token_error = "invalid_grant"
    result = routine.run_routine(
        env(), http=google.http, garmin_login=login_with(garmin_api(), calls), today=TODAY, now=NOW
    )
    assert result.status is routine.Status.AUTH_REQUIRED and result.exit_code == exitcodes.AUTH
    assert "Google" in result.detail and calls == []


@pytest.mark.parametrize(
    ("error", "status", "exit_code"),
    [
        (AuthRequired("expired"), routine.Status.AUTH_REQUIRED, exitcodes.AUTH),
        (RateLimited("429"), routine.Status.RATE_LIMITED, exitcodes.INCOMPLETE),
        (GarminUnreachable("down"), routine.Status.UNREACHABLE, exitcodes.INCOMPLETE),
    ],
)
def test_garmin_login_problems_map_to_distinct_statuses(error, status, exit_code) -> None:
    def failing(_tokens: str, **_k: Any) -> GarminSource:
        raise error

    result = routine.run_routine(
        env(), http=FakeGoogle().http, garmin_login=failing, today=TODAY, now=NOW
    )
    assert (result.status, result.exit_code) == (status, exit_code)


def test_a_session_rejected_mid_run_is_an_auth_failure() -> None:
    api = garmin_api()
    api.failures["get_sleep_data"] = GarminConnectAuthenticationError("401")
    result = run(env(), api=api)
    assert result.status is routine.Status.AUTH_REQUIRED


def test_a_rate_limit_mid_run_is_incomplete_not_green() -> None:
    api = garmin_api()
    api.failures["get_weigh_ins"] = GarminConnectTooManyRequestsError("429")
    result = run(env(), api=api)
    assert result.status is routine.Status.RATE_LIMITED and result.exit_code == exitcodes.INCOMPLETE


def test_a_failed_endpoint_makes_the_run_partial() -> None:
    """'Green' must not hide a skipped day: a failure in the essential set is not a success."""
    api = garmin_api()
    api.failures["get_hydration_data"] = unsupported()
    result = run(env(GARMIN_SYNC_APPLY="1"), api=api)
    assert result.status is routine.Status.PARTIAL and result.exit_code == exitcodes.INCOMPLETE
    assert "hydration_data" in result.detail


def test_failed_writes_make_the_run_partial() -> None:
    google = FakeGoogle()
    google.denied = {"sleep"}
    result = run(env(GARMIN_SYNC_APPLY="1"), google)
    assert result.status is routine.Status.PARTIAL and "failed to write" in result.detail
    assert google.created["weight"] == 1  # other kinds were still written


class RotatingApi(FakeGarmin):
    """A session whose refresh token changed during the run."""

    client = type("C", (), {"dumps": staticmethod(lambda: '{"di_refresh_token":"R2"}')})()


def test_a_rotated_garmin_refresh_token_is_flagged() -> None:
    """Stateless mode cannot persist a rotated token; the run must say so loudly."""
    api = RotatingApi(garmin_api().responses)
    result = run(env(), api=api)
    assert result.summary["garmin_token_rotated"] is True
    assert result.status is routine.Status.PARTIAL and "rotated" in result.detail


def test_an_unrotated_token_is_not_flagged() -> None:
    result = run(env())
    assert result.summary["garmin_token_rotated"] is False


# -- review findings --------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", "anything", " 1 "])
def test_the_kill_switch_fails_closed(value: str) -> None:
    """Regression: only the exact string "1" paused the routine; "true" kept writing."""
    google = FakeGoogle()
    result = run(env(GARMIN_SYNC_PAUSED=value, GARMIN_SYNC_APPLY="1"), google)
    assert result.status is routine.Status.PAUSED and google.http.calls == []


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "OFF"])
def test_only_an_explicit_off_lets_it_run(value: str) -> None:
    assert run(env(GARMIN_SYNC_PAUSED=value)).status is routine.Status.DRY_RUN


def test_a_one_day_window_is_refused_because_it_could_never_see_a_finished_day() -> None:
    result = run(env(GARMIN_SYNC_DAYS="1"))
    assert result.status is routine.Status.MISCONFIGURED and "GARMIN_SYNC_DAYS" in result.detail


def test_an_interrupted_pull_writes_nothing() -> None:
    """Regression: a rate limit mid-pull still pushed records that lacked their detail."""
    api = garmin_api()
    api.failures["get_activity_splits"] = GarminConnectTooManyRequestsError("429")
    google = FakeGoogle()
    result = run(env(GARMIN_SYNC_APPLY="1"), google, api)
    assert result.status is routine.Status.RATE_LIMITED
    assert google.created == Counter() and "push" not in result.summary


def test_an_exercise_whose_detail_calls_failed_is_not_written_degraded() -> None:
    """Regression: laps, events and notes were lost for good when a detail call failed."""
    api = garmin_api()
    api.failures["get_activity_splits"] = unsupported()
    google = FakeGoogle()
    result = run(env(GARMIN_SYNC_APPLY="1"), google, api)
    assert "exercise" not in google.created
    assert {"sleep", "weight", "body-fat", "hydration-log"} <= set(google.created)
    assert result.status is routine.Status.PARTIAL and "held back" in result.detail


def test_hydration_is_not_written_when_the_timezone_could_not_be_learned() -> None:
    """Regression: with user_summary down, a partial day was framed in UTC and frozen."""
    api = garmin_api()
    api.failures["get_user_summary"] = unsupported()
    google = FakeGoogle()
    result = run(env(GARMIN_SYNC_APPLY="1"), google, api)
    assert "hydration-log" not in google.created
    assert result.status is routine.Status.PARTIAL and "user_summary" in result.detail


def test_a_login_without_the_read_scopes_is_refused_before_garmin_is_contacted() -> None:
    """Regression: write-only scopes hide data Garmin Connect already sent, so it was duplicated."""
    google, calls = FakeGoogle(), []

    def write_only(method: str, url: str, kwargs: dict) -> FakeResponse:
        if url == "https://oauth2.googleapis.com/token":
            scope = " ".join(s for s in ga.SCOPES if s.endswith(".writeonly"))
            return FakeResponse(200, {"access_token": "a", "expires_in": 3600, "scope": scope})
        return google.handle(method, url, kwargs)

    google.http = FakeHttp(handler=write_only)
    result = routine.run_routine(
        env(GARMIN_SYNC_APPLY="1"),
        http=google.http,
        garmin_login=login_with(garmin_api(), calls),
        today=TODAY,
        now=NOW,
    )
    assert result.status is routine.Status.AUTH_REQUIRED and "permissions" in result.detail
    assert calls == [] and google.created == Counter()


def test_existing_data_from_another_source_blocks_the_write() -> None:
    """The routine's duplicate check must really consult Google; it has no ledger."""
    google = FakeGoogle()
    google.points["sleep"] = [
        {  # e.g. what Garmin Connect's own sync already stored for the same night
            "name": "users/me/dataTypes/sleep/dataPoints/other-source",
            "sleep": {
                "interval": {"startTime": "2024-05-02T03:30:00Z", "endTime": "2024-05-02T10:30:00Z"}
            },
        }
    ]
    result = run(env(GARMIN_SYNC_APPLY="1"), google)
    assert len(google.points["sleep"]) == 1  # nothing added
    assert result.summary["push"]["by_metric"]["sleep"]["duplicate_existing"] == 1


def test_only_the_detail_calls_the_google_write_needs_are_made() -> None:
    api = garmin_api()
    run(env(), api=api)
    called = {name for name, _ in api.calls}
    assert {"get_activity", "get_activity_splits", "get_activity_hr_in_timezones"} <= called
    assert called.isdisjoint({"get_activity_weather", "get_activity_gear", "get_activity_details"})


def test_an_unexpected_error_still_ends_in_a_status_not_a_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(*_a: Any, **_k: Any) -> None:
        raise RuntimeError(f"surprise containing {SECRETS['GARMIN_SYNC_GOOGLE_CLIENT_SECRET']}")

    monkeypatch.setattr(routine, "pull", explode)
    result = run(env(GARMIN_SYNC_APPLY="1"))
    assert result.status is routine.Status.FAILED and result.exit_code == exitcodes.ERROR
    assert "RuntimeError" in result.detail and "SENTINEL" not in result.detail  # type only
    assert result.summary["apply"] is True
