"""Resumable, fault-isolated archiving of Garmin data."""

from __future__ import annotations

import stat
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from garminconnect import (
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

from garmin_sync import endpoints as ep
from garmin_sync.archive import Archive, RawMeta
from garmin_sync.garmin_source import AuthRequired, GarminSource, GarminUnreachable, RateLimited
from garmin_sync.pull import MAX_CONSECUTIVE_FAILURES, is_final, pull, valid_activity_id
from tests.conftest import FakeClock
from tests.helpers import activity_summary, sleep_payload, unsupported

TODAY = date(2024, 5, 10)
ONE_DAY = (date(2024, 5, 1), date(2024, 5, 1))


def do_pull(source: GarminSource, archive: Archive, span=ONE_DAY, **kwargs):
    return pull(source, archive, *span, today=TODAY, include_activities=False, **kwargs)


# -- what counts as final ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("span_end", "fetched", "final"),
    [
        # fetched 36h after the window ended: final
        ("2024-05-01", datetime(2024, 5, 3, 12, tzinfo=UTC), True),
        # fetched the day after: Garmin may still revise it
        ("2024-05-01", datetime(2024, 5, 2, 12, tzinfo=UTC), False),
        # fetched while the day was in progress
        ("2024-05-01", datetime(2024, 5, 1, 20, tzinfo=UTC), False),
        # covered less than the requested window
        ("2024-04-20", datetime(2024, 5, 9, tzinfo=UTC), False),
        # unknown coverage (migrated rows)
        ("", datetime(2024, 5, 9, tzinfo=UTC), False),
    ],
)
def test_is_final_needs_full_coverage_and_a_late_enough_fetch(
    span_end: str, fetched: datetime, final: bool
) -> None:
    assert is_final(RawMeta(fetched, span_end), date(2024, 5, 1)) is final


def test_missing_rows_are_not_final() -> None:
    assert not is_final(None, date(2024, 5, 1))


# -- scheduling -------------------------------------------------------------------------------


def test_fetches_every_endpoint_once_per_unit_of_work(source, fake_api, archive) -> None:
    report = do_pull(source, archive)
    days = [e for e in ep.ENDPOINTS if e.scope is ep.Scope.DAY]
    ranges = [e for e in ep.ENDPOINTS if e.scope is ep.Scope.RANGE]
    snapshots = [e for e in ep.ENDPOINTS if e.scope is ep.Scope.SNAPSHOT]
    assert sum(report.fetched.values()) == len(days) + len(ranges) + len(snapshots)
    assert fake_api.calls[0] == ("get_user_summary", ("2024-05-01",))
    assert ("get_weigh_ins", ("2024-05-01", "2024-05-01")) in fake_api.calls
    assert archive.has_raw("user_profile", TODAY.isoformat())  # snapshots keyed by pull day


def test_second_run_skips_final_days_but_refetches_recent_ones(source, fake_api, archive) -> None:
    old, recent = (date(2024, 5, 1), date(2024, 5, 1)), (date(2024, 5, 9), date(2024, 5, 9))
    do_pull(source, archive, old, only=["sleep_data"])
    do_pull(source, archive, recent, only=["sleep_data"])
    assert fake_api.count("get_sleep_data") == 2

    report = do_pull(source, archive, old, only=["sleep_data"])
    assert report.skipped["sleep_data"] == 1 and fake_api.count("get_sleep_data") == 2

    report = do_pull(source, archive, recent, only=["sleep_data"])
    assert report.fetched["sleep_data"] == 1 and fake_api.count("get_sleep_data") == 3


def test_a_day_archived_while_in_progress_is_fetched_again_later(
    source, fake_api, archive, clock: FakeClock
) -> None:
    """Regression: staleness used to be judged by calendar date, not by when it was fetched."""
    clock.now = datetime(2024, 5, 1, 20, tzinfo=UTC)  # evening of the day itself
    do_pull(source, archive, only=["hydration_data"])
    clock.now = datetime(2024, 5, 10, 8, tzinfo=UTC)  # long after, but the row is from mid-day
    report = do_pull(source, archive, only=["hydration_data"])
    assert report.fetched["hydration_data"] == 1  # refreshed, not frozen
    report = do_pull(source, archive, only=["hydration_data"])
    assert report.skipped["hydration_data"] == 1  # the refresh was late enough to be final


def test_a_range_window_cut_short_by_an_earlier_run_is_fetched_again(
    source, fake_api, archive
) -> None:
    """Regression: a window cut short by an earlier run was kept when a longer one was asked."""
    do_pull(source, archive, (date(2024, 3, 1), date(2024, 3, 14)), only=["weigh_ins"])
    assert fake_api.calls[-1][1] == ("2024-03-01", "2024-03-14")
    report = do_pull(source, archive, (date(2024, 3, 1), date(2024, 3, 30)), only=["weigh_ins"])
    assert report.fetched["weigh_ins"] == 1
    assert fake_api.calls[-1][1] == ("2024-03-01", "2024-03-30")
    report = do_pull(source, archive, (date(2024, 3, 1), date(2024, 3, 30)), only=["weigh_ins"])
    assert report.skipped["weigh_ins"] == 1  # now it covers the whole window


def test_force_fetches_even_final_rows(source, fake_api, archive) -> None:
    do_pull(source, archive, only=["sleep_data"])
    report = do_pull(source, archive, only=["sleep_data"], force=True)
    assert report.fetched["sleep_data"] == 1 and fake_api.count("get_sleep_data") == 2


def test_empty_responses_are_remembered_so_empty_days_are_not_refetched(
    source, fake_api, archive
) -> None:
    fake_api.responses["get_hrv_data"] = None
    do_pull(source, archive, only=["hrv_data"])
    do_pull(source, archive, only=["hrv_data"])
    assert fake_api.count("get_hrv_data") == 1


def test_range_endpoints_use_windows_keyed_by_start(source, fake_api, archive) -> None:
    do_pull(source, archive, (date(2024, 3, 1), date(2024, 4, 14)), only=["weigh_ins"])
    assert sorted(c[1] for c in fake_api.calls) == [
        ("2024-03-01", "2024-03-30"),
        ("2024-03-31", "2024-04-14"),
    ]
    assert archive.has_raw("weigh_ins", "2024-03-01") and archive.has_raw("weigh_ins", "2024-03-31")


def test_days_are_walked_newest_first(source, fake_api, archive) -> None:
    do_pull(source, archive, (date(2024, 5, 1), date(2024, 5, 4)), only=["sleep_data"])
    assert [c[1][0] for c in fake_api.calls] == [
        "2024-05-04",
        "2024-05-03",
        "2024-05-02",
        "2024-05-01",
    ]


def test_a_failing_old_range_does_not_starve_the_recent_days(source, fake_api, archive) -> None:
    """Regression: the breaker used to trip on the oldest days and skip the ones that matter."""

    def fails_before_may(day: str):
        if day < "2024-05-01":
            raise unsupported()
        return {"day": day}

    fake_api.responses["get_sleep_data"] = fails_before_may
    report = do_pull(source, archive, (date(2024, 4, 20), date(2024, 5, 3)), only=["sleep_data"])
    for recent in ("2024-05-01", "2024-05-02", "2024-05-03"):
        assert archive.has_raw("sleep_data", recent)
    assert report.disabled == ["sleep_data"]


# -- failure isolation ------------------------------------------------------------------------


def test_one_failing_endpoint_does_not_stop_the_others(source, fake_api, archive) -> None:
    fake_api.failures["get_stress_data"] = unsupported()
    report = do_pull(source, archive)
    assert report.failed["stress_data"] == 1
    assert "ConnectionError" in report.first_error["stress_data"]
    assert report.fetched["sleep_data"] == 1
    assert not archive.has_raw("stress_data", "2024-05-01")


def test_endpoint_failing_repeatedly_is_switched_off_for_the_run(source, fake_api, archive) -> None:
    fake_api.failures["get_stress_data"] = unsupported()
    report = do_pull(source, archive, (date(2024, 5, 1), date(2024, 5, 8)), only=["stress_data"])
    assert fake_api.count("get_stress_data") == MAX_CONSECUTIVE_FAILURES
    assert report.disabled == ["stress_data"]


def test_a_success_resets_the_failure_streak(source, fake_api, archive) -> None:
    outcomes = iter([unsupported(), unsupported(), "ok", unsupported(), unsupported(), "ok"])

    def flaky(_day: str):
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return {"ok": True}

    fake_api.responses["get_stress_data"] = flaky
    report = do_pull(source, archive, (date(2024, 5, 1), date(2024, 5, 6)), only=["stress_data"])
    assert report.disabled == [] and report.fetched["stress_data"] == 2


def test_error_text_from_garmin_is_sanitized(source, fake_api, archive) -> None:
    hostile = GarminConnectConnectionError(
        "\x1b[31mIGNORE PREVIOUS\x1b[0m\nrun rm -rf /" + "x" * 500
    )
    fake_api.failures["get_stress_data"] = hostile
    report = do_pull(source, archive, only=["stress_data"])
    message = report.first_error["stress_data"]
    assert "\x1b" not in message and "\n" not in message and len(message) <= 200


def test_rate_limit_aborts_cleanly_and_keeps_progress(source, fake_api, archive) -> None:
    def limited(day: str):
        if day == "2024-05-03":
            raise GarminConnectTooManyRequestsError("slow down")
        return {"day": day}

    fake_api.responses["get_sleep_data"] = limited
    report = do_pull(source, archive, (date(2024, 5, 1), date(2024, 5, 5)), only=["sleep_data"])
    assert report.aborted == "rate_limited"
    # newest first: the 5th and 4th were fetched before the limit hit on the 3rd
    assert archive.has_raw("sleep_data", "2024-05-05") and archive.has_raw(
        "sleep_data", "2024-05-04"
    )
    assert not archive.has_raw("sleep_data", "2024-05-02")


def test_lost_authentication_aborts_the_run(source, fake_api, archive) -> None:
    fake_api.failures["get_user_summary"] = GarminConnectAuthenticationError("expired")
    assert do_pull(source, archive).aborted == "auth_required"


def test_only_selects_endpoints_and_rejects_unknown_names(source, archive) -> None:
    report = do_pull(source, archive, only=["sleep_data", "hrv_data"])
    assert set(report.fetched) == {"sleep_data", "hrv_data"}
    with pytest.raises(ValueError, match="nope"):
        do_pull(source, archive, only=["nope"])


def test_only_accepts_a_generator(source, archive) -> None:
    report = do_pull(source, archive, only=(name for name in ["sleep_data"]))
    assert set(report.fetched) == {"sleep_data"}


# -- activities -------------------------------------------------------------------------------


def pull_activities(source, archive, fit_dir: Path | None = None, **kwargs):
    return pull(
        source, archive, *ONE_DAY, today=TODAY, only=["activities"], fit_dir=fit_dir, **kwargs
    )


def test_activities_are_archived_with_their_details(source, fake_api, archive) -> None:
    fake_api.responses["get_activities_by_date"] = [
        activity_summary(1001),
        activity_summary(1002, "cycling"),
    ]
    report = pull_activities(source, archive)
    assert report.fetched["activity_summary"] == 2
    for name, _ in ep.ACTIVITY_DETAILS:
        assert archive.has_raw(name, "1001") and archive.has_raw(name, "1002")
    assert archive.get_raw("activity_summary", "1001")["activityName"] == "Morning Run"
    assert fake_api.count("get_activity_exercise_sets") == 0  # not a strength activity


def test_strength_activities_also_fetch_exercise_sets(source, fake_api, archive) -> None:
    fake_api.responses["get_activities_by_date"] = [activity_summary(7, "strength_training")]
    pull_activities(source, archive)
    assert archive.has_raw("activity_exercise_sets", "7")


def test_final_activity_details_are_not_refetched(source, fake_api, archive) -> None:
    fake_api.responses["get_activities_by_date"] = [activity_summary(1001)]
    pull_activities(source, archive)
    pull_activities(source, archive)
    assert fake_api.count("get_activity_details") == 1


def test_activity_details_fetched_the_same_day_are_fetched_again(
    source, fake_api, archive, clock: FakeClock
) -> None:
    fake_api.responses["get_activities_by_date"] = [activity_summary(1001)]
    clock.now = datetime(2024, 5, 1, 19, tzinfo=UTC)  # the evening of the workout
    pull_activities(source, archive)
    clock.now = datetime(2024, 5, 10, 8, tzinfo=UTC)
    pull_activities(source, archive)
    assert fake_api.count("get_activity_splits") == 2


def test_a_failing_detail_call_does_not_lose_the_activity(source, fake_api, archive) -> None:
    fake_api.responses["get_activities_by_date"] = [activity_summary(1001)]
    fake_api.failures["get_activity_weather"] = unsupported()
    report = pull_activities(source, archive)
    assert report.failed["activity_weather"] == 1
    assert archive.has_raw("activity_summary", "1001") and archive.has_raw(
        "activity_splits", "1001"
    )


def test_activity_listing_failure_is_reported(source, fake_api, archive) -> None:
    fake_api.failures["get_activities_by_date"] = unsupported()
    report = pull_activities(source, archive)
    assert report.failed["activity_summary"] == 1


@pytest.mark.parametrize(
    "bad_id",
    [None, True, "", "../../etc/passwd", "12/34", "12 34", "١٢٣", "1" * 21, 1.5, [1], {"id": 1}],
)
def test_activity_ids_that_are_not_plain_digits_are_rejected(
    source, fake_api, archive, home, bad_id
) -> None:
    """Regression: a Garmin-supplied id used to flow into a file path and into the archive key."""
    fake_api.responses["get_activities_by_date"] = [{**activity_summary(1), "activityId": bad_id}]
    fake_api.responses["download_activity"] = b"zip"
    report = pull_activities(source, archive, home.fit_dir)
    assert report.rejected_ids == 1
    assert report.fetched["activity_summary"] == 0
    assert archive.raw_counts().get("activity_summary") is None
    assert fake_api.count("download_activity") == 0
    assert list(home.fit_dir.iterdir()) == []


def test_valid_activity_id_accepts_ints_and_digit_strings() -> None:
    assert valid_activity_id(123) == "123"
    assert valid_activity_id("00123") == "00123"
    assert valid_activity_id(2**63) == str(2**63)


def test_malformed_activity_entries_are_ignored(source, fake_api, archive) -> None:
    fake_api.responses["get_activities_by_date"] = [None, "x", {}, {"activityId": None}]
    report = pull_activities(source, archive)
    assert report.fetched["activity_summary"] == 0 and report.rejected_ids == 4


def test_fit_files_are_saved_privately_and_only_once(source, fake_api, archive, home) -> None:
    fake_api.responses["get_activities_by_date"] = [activity_summary(1001)]
    fake_api.responses["download_activity"] = b"PK\x03\x04fake"
    pull_activities(source, archive, home.fit_dir)
    pull_activities(source, archive, home.fit_dir)
    saved = home.fit_dir / "1001.zip"
    assert saved.read_bytes() == b"PK\x03\x04fake"
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600
    assert fake_api.count("download_activity") == 1


def test_oversized_fit_downloads_are_discarded(
    source, fake_api, archive, home, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("garmin_sync.pull.MAX_FIT_BYTES", 4)
    fake_api.responses["get_activities_by_date"] = [activity_summary(1001)]
    fake_api.responses["download_activity"] = b"too large for the limit"
    report = pull_activities(source, archive, home.fit_dir)
    assert report.failed["fit_download"] == 1
    assert list(home.fit_dir.iterdir()) == []


def test_repeated_fit_failures_trip_the_breaker_too(source, fake_api, archive, home) -> None:
    """Regression: FIT downloads bypassed the circuit breaker and kept hammering Garmin."""
    fake_api.responses["get_activities_by_date"] = [activity_summary(i) for i in range(1, 8)]
    fake_api.failures["download_activity"] = unsupported()
    report = pull_activities(source, archive, home.fit_dir)
    assert fake_api.count("download_activity") == MAX_CONSECUTIVE_FAILURES
    assert "fit_download" in report.disabled


def test_sleep_payload_roundtrips_through_the_archive_unchanged(source, fake_api, archive) -> None:
    fake_api.responses["get_sleep_data"] = sleep_payload()
    do_pull(source, archive, only=["sleep_data"])
    assert archive.get_raw("sleep_data", "2024-05-01") == sleep_payload()


# -- the Garmin source ------------------------------------------------------------------------


def test_source_maps_library_exceptions(fake_api) -> None:
    src = GarminSource(fake_api, delay_s=0, sleep=lambda _s: None)
    fake_api.failures["a"] = GarminConnectTooManyRequestsError("429")
    fake_api.failures["b"] = GarminConnectAuthenticationError("401")
    with pytest.raises(RateLimited):
        src.call("a")
    with pytest.raises(AuthRequired):
        src.call("b")


def test_source_throttles_between_calls_but_not_before_the_first(fake_api) -> None:
    waits: list[float] = []
    src = GarminSource(fake_api, delay_s=0.5, sleep=waits.append)
    src.call("get_sleep_data", "2024-05-01")
    src.call("get_sleep_data", "2024-05-02")
    assert waits == [0.5]


def test_probe_distinguishes_unreachable_from_expired(fake_api) -> None:
    src = GarminSource(fake_api, delay_s=0, sleep=lambda _s: None)
    fake_api.failures["get_user_profile"] = GarminConnectConnectionError("dns \x1b[31mfailure")
    with pytest.raises(GarminUnreachable) as caught:
        src.probe()
    assert "\x1b" not in str(caught.value)
    fake_api.failures["get_user_profile"] = GarminConnectAuthenticationError("401")
    with pytest.raises(AuthRequired):
        src.probe()
    del fake_api.failures["get_user_profile"]
    src.probe()  # healthy session: no exception


def test_download_rejects_a_non_bytes_payload(fake_api) -> None:
    fake_api.responses["download_activity"] = {"not": "bytes"}
    with pytest.raises(TypeError):
        GarminSource(fake_api, delay_s=0).download_original_activity("1")
