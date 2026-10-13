"""``doctor``: what it reports, what it recommends, and that it never prints a secret."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

from garminconnect import GarminConnectAuthenticationError, GarminConnectConnectionError

from garmin_sync import doctor
from garmin_sync.archive import Archive
from garmin_sync.garmin_source import GarminSource
from garmin_sync.paths import AppHome
from garmin_sync.sinks import google_auth as ga
from tests.fakes_http import FakeHttp, FakeResponse
from tests.helpers import FakeGarmin, user_summary_payload
from tests.test_google_auth import write_client

SENTINEL = "DOCTOR-SECRET-SENTINEL"


def checks_by_name(checks: list[doctor.Check]) -> dict[str, doctor.Check]:
    return {c.name: c for c in checks}


def garmin_login(api: FakeGarmin):
    return lambda _home: GarminSource(api, delay_s=0, sleep=lambda _s: None)


def log_in_to_garmin(home: AppHome) -> None:
    (home.garmin_token_dir / "garmin_tokens.json").write_text(json.dumps({"di_token": SENTINEL}))
    (home.garmin_token_dir / "garmin_tokens.json").chmod(0o600)


def log_in_to_google(home: AppHome, scope: str | None = None) -> None:
    write_client(home.google_client_file)
    home.google_token_file.write_text(
        json.dumps(
            {
                "access_token": SENTINEL,
                "refresh_token": SENTINEL,
                "expires_at": 9e9,
                "scope": " ".join(ga.SCOPES) if scope is None else scope,
            }
        )
    )
    home.google_token_file.chmod(0o600)


def run(
    home: AppHome, *, online: bool = False, http=None, api: FakeGarmin | None = None, today=None
):
    return doctor.run_doctor(
        home,
        online=online,
        http=http or FakeHttp(),
        garmin_login=garmin_login(api or FakeGarmin({"get_user_profile": {"id": 1}})),
        today=today or date(2024, 5, 10),
    )


def test_a_fresh_machine_is_told_to_log_in_to_garmin_first() -> None:
    from pathlib import Path

    checks = doctor.run_doctor(
        AppHome(Path("/nonexistent/state")), online=False, http=FakeHttp(), today=date(2024, 5, 10)
    )
    by_name = checks_by_name(checks)
    assert by_name["garmin_login"].status == doctor.FAIL
    assert "auth garmin" in doctor.next_action(checks)
    assert "terminal" not in by_name["garmin_login"].message


def test_next_action_prefers_failures_over_warnings(home) -> None:
    log_in_to_garmin(home)
    checks = run(home)
    assert checks_by_name(checks)["google_client"].status == doctor.FAIL
    assert "setup-google.md" in doctor.next_action(checks)


def test_everything_in_order_offline(home) -> None:
    log_in_to_garmin(home)
    log_in_to_google(home)
    with Archive.open(home.archive_file) as archive:
        archive.put_raw("user_summary", "2024-05-09", user_summary_payload("2024-05-09"))
    checks = run(home)
    assert all(c.status == doctor.OK for c in checks), [c for c in checks if c.status != doctor.OK]
    assert doctor.next_action(checks) is None


def test_missing_scopes_are_a_failure_with_a_fix(home) -> None:
    log_in_to_garmin(home)
    log_in_to_google(home, scope=ga.SCOPES[0])
    scopes = checks_by_name(run(home))["google_scopes"]
    assert scopes.status == doctor.FAIL and "sleep.writeonly" in scopes.message
    assert "tick every permission" in scopes.fix


def test_loose_token_permissions_are_flagged(home) -> None:
    log_in_to_garmin(home)
    log_in_to_google(home)
    (home.garmin_token_dir / "garmin_tokens.json").chmod(0o644)
    home.google_token_file.chmod(0o640)
    by_name = checks_by_name(run(home))
    assert by_name["garmin_token_permissions"].status == doctor.WARN
    assert by_name["google_token_permissions"].status == doctor.WARN


def test_a_corrupt_google_token_file_is_a_failure_not_a_crash(home) -> None:
    log_in_to_garmin(home)
    write_client(home.google_client_file)
    home.google_token_file.write_text("{not json")
    assert checks_by_name(run(home))["google_login"].status == doctor.FAIL


def test_a_stale_archive_is_a_warning(home) -> None:
    log_in_to_garmin(home)
    with Archive.open(home.archive_file) as archive:
        archive.put_raw("user_summary", "2024-04-01", user_summary_payload("2024-04-01"))
    archive_check = checks_by_name(run(home))["archive"]
    assert archive_check.status == doctor.WARN and "days old" in archive_check.message


def test_no_archive_suggests_a_first_pull(home) -> None:
    check = checks_by_name(run(home))["archive"]
    assert check.status == doctor.WARN and "pull" in check.fix


# -- online -----------------------------------------------------------------------------------


def test_online_garmin_outcomes(home) -> None:
    log_in_to_garmin(home)
    ok = checks_by_name(run(home, online=True))["garmin_login"]
    assert ok.status == doctor.OK

    api = FakeGarmin()
    api.failures["get_user_profile"] = GarminConnectAuthenticationError("401")
    assert checks_by_name(run(home, online=True, api=api))["garmin_login"].status == doctor.FAIL

    api.failures["get_user_profile"] = GarminConnectConnectionError("\x1b[31mdns down")
    down = checks_by_name(run(home, online=True, api=api))["garmin_login"]
    assert down.status == doctor.WARN and "\x1b" not in down.message


def test_online_google_outcomes(home) -> None:
    log_in_to_garmin(home)
    log_in_to_google(home)
    healthy = FakeHttp(queue=[FakeResponse(200, {"name": "users/me/identity"})])
    assert checks_by_name(run(home, online=True, http=healthy))["google_login"].status == doctor.OK

    expired = FakeHttp(queue=[FakeResponse(400, {"error": "invalid_grant"})])
    home.google_token_file.write_text(
        json.dumps(
            {
                "access_token": "",
                "refresh_token": "r",
                "expires_at": 0,
                "scope": " ".join(ga.SCOPES),
            }
        )
    )
    result = checks_by_name(run(home, online=True, http=expired))["google_login"]
    assert result.status == doctor.FAIL and "auth google login" in result.fix

    denied = FakeHttp(
        handler=lambda m, u, k: FakeResponse(403, {"error": {"message": "API disabled"}})
    )
    home.google_token_file.write_text(
        json.dumps(
            {
                "access_token": "t",
                "refresh_token": "r",
                "expires_at": 9e9,
                "scope": " ".join(ga.SCOPES),
            }
        )
    )
    result = checks_by_name(run(home, online=True, http=denied))["google_login"]
    assert result.status == doctor.FAIL and "test user" in result.fix


def test_no_secret_ever_appears_in_the_report(home) -> None:
    log_in_to_garmin(home)
    log_in_to_google(home)
    http = FakeHttp(queue=[FakeResponse(200, {"name": "users/me/identity"})])
    shown = json.dumps([c.to_dict() for c in run(home, online=True, http=http)])
    assert SENTINEL not in shown


# -- time zone --------------------------------------------------------------------------------


def sgt_user_summary(day: str) -> dict:
    """A day in Singapore: local midnight is 16:00 UTC the evening before."""
    previous = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
    return {
        "calendarDate": day,
        "wellnessStartTimeGmt": f"{previous}T16:00:00.0",
        "wellnessStartTimeLocal": f"{day}T00:00:00.0",
    }


def zone_check(home, **kwargs) -> doctor.Check:
    return checks_by_name(run(home, **kwargs))["timezone"]


def test_an_unset_zone_fails_and_says_to_ask_the_user(home, monkeypatch) -> None:
    monkeypatch.delenv("GARMIN_SYNC_TIMEZONE")
    check = zone_check(home)
    assert check.status == doctor.FAIL and "not set" in check.message
    assert "Ask the user" in check.fix and "garmin-sync timezone" in check.fix
    assert "Ask the user" in doctor.next_action(run(home))


def test_an_invalid_zone_fails_with_the_reason(home, monkeypatch) -> None:
    monkeypatch.setenv("GARMIN_SYNC_TIMEZONE", "SGT")
    check = zone_check(home)
    assert check.status == doctor.FAIL and "IANA" in check.message


def test_a_zone_taken_from_the_machine_is_only_a_warning_to_confirm(home, monkeypatch) -> None:
    monkeypatch.delenv("GARMIN_SYNC_TIMEZONE")
    monkeypatch.setattr(doctor.usertime, "system_zone_name", lambda: "Asia/Singapore")
    check = zone_check(home)
    assert check.status == doctor.WARN and "not confirmed" in check.message
    assert "garmin-sync timezone Asia/Singapore" in check.fix


def test_a_saved_zone_counts_as_confirmed(home, monkeypatch) -> None:
    monkeypatch.delenv("GARMIN_SYNC_TIMEZONE")
    home.settings_file.write_text('{"timezone": "Asia/Singapore"}')
    check = zone_check(home)
    assert check.status == doctor.OK and "Asia/Singapore (UTC+08:00)" in check.message


def test_garmin_offsets_that_agree_with_the_zone_are_fine(home, monkeypatch) -> None:
    monkeypatch.setenv("GARMIN_SYNC_TIMEZONE", "Asia/Singapore")
    with Archive.open(home.archive_file) as archive:
        archive.put_raw("user_summary", "2024-05-09", sgt_user_summary("2024-05-09"))
    assert zone_check(home).status == doctor.OK


def test_garmin_offsets_that_disagree_with_the_zone_are_flagged(home, monkeypatch) -> None:
    monkeypatch.setenv("GARMIN_SYNC_TIMEZONE", "Asia/Singapore")
    with Archive.open(home.archive_file) as archive:
        archive.put_raw("user_summary", "2024-05-09", user_summary_payload("2024-05-09"))  # UTC-4
    check = zone_check(home)
    assert check.status == doctor.WARN and "differs on 1 recent day" in check.message
    assert "travelling" in check.fix


def test_freshness_is_judged_by_the_users_date_not_utc(home, monkeypatch, freeze_time) -> None:
    """At 23:07 UTC on 9 May it is already 10 May in Singapore: a 6 May archive is 4 days old."""
    monkeypatch.setenv("GARMIN_SYNC_TIMEZONE", "Asia/Singapore")
    freeze_time(datetime(2024, 5, 9, 23, 7, tzinfo=UTC))
    with Archive.open(home.archive_file) as archive:
        archive.put_raw("user_summary", "2024-05-06", sgt_user_summary("2024-05-06"))
    stale = doctor.run_doctor(home, online=False, http=FakeHttp())
    assert "4 days old" in checks_by_name(stale)["archive"].message
    monkeypatch.setenv("GARMIN_SYNC_TIMEZONE", "America/New_York")  # still 9 May there: 3 days
    fine = doctor.run_doctor(home, online=False, http=FakeHttp())
    assert checks_by_name(fine)["archive"].status == doctor.OK


def test_a_bad_zone_names_where_it_was_set_but_not_what_it_was(home, monkeypatch) -> None:
    monkeypatch.setenv("GARMIN_SYNC_TIMEZONE", "SGT-SECRET-LOOKING-VALUE")
    check = zone_check(home)
    assert check.status == doctor.FAIL and "GARMIN_SYNC_TIMEZONE" in check.message
    assert "SECRET-LOOKING" not in check.message
    monkeypatch.delenv("GARMIN_SYNC_TIMEZONE")
    home.settings_file.write_text('{"timezone": "SGT"}')
    assert "saved time zone" in zone_check(home).message
