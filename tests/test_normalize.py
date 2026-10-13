"""Normalizers, context building and range normalization."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from garmin_sync.archive import Archive
from garmin_sync.models import Metric
from garmin_sync.normalize import REGISTRY, Context, build_context, normalize_range
from garmin_sync.normalize.activity_types import ACTIVITY_TYPES, map_activity_type
from tests.conftest import NOW, FakeClock
from tests.helpers import (
    EDT,
    activity_detail,
    activity_summary,
    heart_rate_payload,
    local_ms,
    ms,
    sleep_payload,
    splits_payload,
    user_summary_payload,
    weigh_in_payload,
)


def run(endpoint: str, payload: object, key: str = "2024-05-01", ctx: Context | None = None):
    ctx = ctx or Context(day_offsets={key: EDT}, fallback_offset=EDT)
    return REGISTRY[endpoint].fn(key, payload, ctx), ctx


def stage_names(payload: dict) -> list[str]:
    (record, *_), _ = run("sleep_data", payload, "2024-05-02")
    return [s["stage"] for s in record.values["stages"]]


def levels(*codes: int) -> list[dict]:
    """Consecutive hour-long sleep levels with the given activityLevel codes."""
    return [
        {
            "startGMT": f"2024-05-02T{3 + i:02d}:00:00.0",
            "endGMT": f"2024-05-02T{4 + i:02d}:00:00.0",
            "activityLevel": float(code),
        }
        for i, code in enumerate(codes)
    ]


def night(*codes: int) -> dict:
    """A sleep payload whose window is exactly as long as the given hour-long levels."""
    payload = sleep_payload()
    end = f"2024-05-02T{3 + len(codes):02d}:00:00"
    payload["dailySleepDTO"]["sleepEndTimestampGMT"] = ms(end)
    payload["dailySleepDTO"]["sleepEndTimestampLocal"] = local_ms(end)
    payload["sleepLevels"] = levels(*codes)
    return payload


# -- sleep ------------------------------------------------------------------------------------


def test_sleep_record_has_window_offset_totals_and_ordered_stages() -> None:
    (record, *_), _ = run("sleep_data", sleep_payload(), "2024-05-02")
    assert record.metric is Metric.SLEEP
    assert record.external_id == "garmin:sleep:2024-05-02"
    assert record.start == datetime(2024, 5, 2, 3, tzinfo=UTC)
    assert record.end == datetime(2024, 5, 2, 11, tzinfo=UTC)
    assert record.utc_offset_s == EDT
    assert record.values["total_s"] == 28800
    assert [s["stage"] for s in record.values["stages"]] == [
        "light",
        "deep",
        "rem",
        "awake",
        "light",
    ]
    stages = record.values["stages"]
    assert all(a["end"] <= b["start"] for a, b in zip(stages, stages[1:], strict=False))


def test_rem_capable_devices_use_the_rem_code_table() -> None:
    assert stage_names(night(0, 1, 2, 3)) == ["deep", "light", "rem", "awake"]


def test_devices_without_rem_never_produce_rem_sleep() -> None:
    """Regression: code 2 was always read as REM, inventing REM sleep on older watches."""
    payload = night(0, 1, 2, 3)
    payload["dailySleepDTO"]["deviceRemCapable"] = False
    payload["dailySleepDTO"]["remSleepSeconds"] = 0
    names = stage_names(payload)
    assert "rem" not in names
    assert names == ["deep", "light", "awake"]  # 2 and 3 are both awake, so they merge


def test_remsleepdata_flag_decides_when_device_flag_is_absent() -> None:
    payload = night(2)
    del payload["dailySleepDTO"]["deviceRemCapable"]
    payload["remSleepData"] = True
    assert stage_names(payload) == ["rem"]


def test_rem_seconds_imply_a_rem_capable_device_when_no_flag_exists() -> None:
    payload = night(2)
    del payload["dailySleepDTO"]["deviceRemCapable"]
    assert stage_names(payload) == ["rem"]  # the fixture has remSleepSeconds > 0


def test_unknown_capability_is_treated_as_awake_and_reported() -> None:
    payload = night(2)
    del payload["dailySleepDTO"]["deviceRemCapable"]
    del payload["dailySleepDTO"]["remSleepSeconds"]
    (record, *_), ctx = run("sleep_data", payload, "2024-05-02")
    assert [s["stage"] for s in record.values["stages"]] == ["awake"]
    assert any("capability unknown" in issue for _, issue in ctx.diag.issues)


def test_conflicting_capability_flags_are_distrusted_and_reported() -> None:
    payload = night(2)
    payload["dailySleepDTO"]["deviceRemCapable"] = True
    payload["remSleepData"] = False
    (record, *_), ctx = run("sleep_data", payload, "2024-05-02")
    assert [s["stage"] for s in record.values["stages"]] == ["awake"]  # not REM on a guess
    assert any("conflicting" in issue for _, issue in ctx.diag.issues)


def test_short_unmeasured_gaps_are_filled_as_awake_so_stages_stay_contiguous() -> None:
    """Google wants contiguous stages covering the night; a -1 spell becomes awake time."""
    payload = night(1, 1)
    payload["sleepLevels"] = [
        {
            "startGMT": "2024-05-02T03:00:00.0",
            "endGMT": "2024-05-02T03:50:00.0",
            "activityLevel": 1.0,
        },
        {
            "startGMT": "2024-05-02T03:55:00.0",
            "endGMT": "2024-05-02T04:55:00.0",
            "activityLevel": 0.0,
        },
    ]  # a 5-minute hole in the middle and 5 minutes missing at the end
    (record, *_), _ = run("sleep_data", payload, "2024-05-02")
    stages = record.values["stages"]
    assert [s["stage"] for s in stages] == ["light", "awake", "deep", "awake"]
    assert all(a["end"] == b["start"] for a, b in zip(stages, stages[1:], strict=False))
    assert stages[0]["start"] == int(record.start.timestamp())
    assert stages[-1]["end"] == int(record.end.timestamp())


def test_a_night_with_large_unmeasured_stretches_is_sent_without_stages() -> None:
    payload = night(1, -1, 1)  # an hour with the watch off the wrist in the middle
    (record, *_), ctx = run("sleep_data", payload, "2024-05-02")
    assert record.values["stages"] == []
    assert any("too incomplete" in issue for _, issue in ctx.diag.issues)


def test_stages_that_stop_early_leave_the_night_unstaged() -> None:
    payload = night(1, 1, 1)
    payload["sleepLevels"] = levels(1, 1)  # the last hour has no data
    (record, *_), _ = run("sleep_data", payload, "2024-05-02")
    assert record.values["stages"] == []


def test_a_doubled_local_offset_falls_back_to_the_days_own_offset() -> None:
    """Garmin sometimes applies the account offset twice to Local sleep times (UTC+8 accounts)."""
    plus8 = 8 * 3600
    payload = sleep_payload()
    dto = payload["dailySleepDTO"]
    dto["sleepStartTimestampLocal"] = dto["sleepStartTimestampGMT"] + 2 * plus8 * 1000
    ctx = Context(day_offsets={"2024-05-02": plus8}, fallback_offset=plus8)
    (record, *_), ctx = run("sleep_data", payload, "2024-05-02", ctx)
    assert record.utc_offset_s == plus8


def test_a_local_offset_that_disagrees_with_the_day_is_distrusted() -> None:
    payload = sleep_payload()  # Local says UTC-4
    ctx = Context(day_offsets={"2024-05-02": 5 * 3600}, fallback_offset=5 * 3600)
    (record, *_), ctx = run("sleep_data", payload, "2024-05-02", ctx)
    assert record.utc_offset_s == 5 * 3600 and ctx.diag.issues


def test_a_daylight_saving_difference_is_not_distrusted() -> None:
    ctx = Context(day_offsets={"2024-05-02": EDT + 3600}, fallback_offset=EDT + 3600)
    (record, *_), _ = run("sleep_data", sleep_payload(), "2024-05-02", ctx)
    assert record.utc_offset_s == EDT


def test_a_calendar_date_that_is_not_a_date_never_reaches_an_id() -> None:
    """Regression: calendarDate flowed unchecked into record ids and Google's externalId."""
    payload = sleep_payload()
    payload["dailySleepDTO"]["calendarDate"] = "x\n\x1b[31m" + "y" * 1000
    (record, *_), _ = run("sleep_data", payload, "2024-05-02")
    assert record.external_id == "garmin:sleep:2024-05-02"


def test_sleep_stages_are_clipped_to_the_sleep_window_and_never_overlap() -> None:
    payload = sleep_payload()
    payload["sleepLevels"] = [
        {
            "startGMT": "2024-05-02T02:00:00.0",
            "endGMT": "2024-05-02T05:00:00.0",
            "activityLevel": 0.0,
        },
        {
            "startGMT": "2024-05-02T04:00:00.0",
            "endGMT": "2024-05-02T12:00:00.0",
            "activityLevel": 1.0,
        },
    ]
    (record, *_), _ = run("sleep_data", payload, "2024-05-02")
    first, second = record.values["stages"]
    assert first["start"] == int(datetime(2024, 5, 2, 3, tzinfo=UTC).timestamp())
    assert second["start"] == first["end"]
    assert second["end"] == int(datetime(2024, 5, 2, 11, tzinfo=UTC).timestamp())


def test_sleep_ignores_unknown_stage_levels() -> None:
    payload = night(9)
    (record, *_), _ = run("sleep_data", payload, "2024-05-02")
    assert record.values["stages"] == []


def test_no_sleep_recorded_yields_nothing_and_no_warning() -> None:
    records, ctx = run("sleep_data", {"dailySleepDTO": {"calendarDate": "2024-05-02"}})
    assert records == []
    assert not ctx.diag.issues


def test_implausible_sleep_window_is_rejected_with_a_diagnostic() -> None:
    payload = sleep_payload()
    payload["dailySleepDTO"]["sleepEndTimestampGMT"] = ms("2024-05-04T03:00:00")
    records, ctx = run("sleep_data", payload)
    assert records == []
    assert ctx.diag.issues[("sleep_data", "implausible sleep window")] == 1


def test_sleep_payload_also_yields_overnight_spo2_respiration_and_skin_temperature() -> None:
    payload = sleep_payload()
    payload["wellnessEpochSPO2DataDTOList"] = [
        {"epochTimestamp": "2024-05-02T04:00:00.0", "spo2Reading": 95},
        {"epochTimestamp": "2024-05-02T04:01:00.0", "spo2Reading": 12},  # implausible
    ]
    payload["wellnessEpochRespirationDataDTOList"] = [
        {"startTimeGMT": ms("2024-05-02T04:00:00"), "respirationValue": 14.0},
        {"startTimeGMT": ms("2024-05-02T04:02:00"), "respirationValue": -1},
    ]
    payload["avgSkinTempDeviationC"] = -0.4
    records, _ = run("sleep_data", payload, "2024-05-02")
    by_metric = {r.metric: r for r in records}
    assert by_metric[Metric.SPO2].values == {"percent": 95.0}
    assert by_metric[Metric.RESPIRATORY_RATE].values == {"breaths_per_min": 14.0}
    skin = by_metric[Metric.SKIN_TEMPERATURE]
    assert skin.values == {"delta_c": -0.4}
    assert (skin.start, skin.end) == (
        datetime(2024, 5, 2, 3, tzinfo=UTC),
        datetime(2024, 5, 2, 11, tzinfo=UTC),
    )
    assert len(records) == 4


# -- vitals -----------------------------------------------------------------------------------


def test_heart_rate_is_bucketed_hourly_and_filters_bad_samples() -> None:
    records, _ = run("heart_rates", heart_rate_payload())
    series = [r for r in records if r.metric is Metric.HEART_RATE]
    assert [len(r.values["samples"]) for r in series] == [2, 1]  # None and 400 dropped
    assert series[0].utc_offset_s == EDT
    assert series[0].start == datetime(2024, 5, 1, 10, 0, tzinfo=UTC)
    # the interval end is exclusive, so it runs one second past the last sample
    assert series[0].end == datetime(2024, 5, 1, 10, 2, 1, tzinfo=UTC)
    assert series[0].last_instant == datetime(2024, 5, 1, 10, 2, tzinfo=UTC)


def test_a_single_heart_rate_sample_still_has_a_positive_length() -> None:
    payload = heart_rate_payload()
    payload["heartRateValues"] = [[ms("2024-05-01T10:00:00"), 60]]
    (series, _resting), _ = run("heart_rates", payload)
    assert series.end > series.start and not series.is_instant


def test_heart_rate_bucket_ids_never_collide_across_days_in_half_hour_zones() -> None:
    """Regression: UTC-hour ids made two Garmin days share a bucket in zones like UTC+5:30."""
    ist = int(5.5 * 3600)

    def day_payload(day_start_gmt: str, sample_gmt: str) -> dict:
        return {
            "startTimestampGMT": ms(day_start_gmt),
            "startTimestampLocal": ms(day_start_gmt) + ist * 1000,
            "heartRateValues": [[ms(sample_gmt), 70]],
        }

    # Garmin day Apr 30 (IST) runs 2024-04-29T18:30Z .. 2024-04-30T18:30Z; day May 1 starts there.
    apr30 = day_payload("2024-04-29T18:30:00", "2024-04-30T18:10:00")
    may1 = day_payload("2024-04-30T18:30:00", "2024-04-30T18:40:00")
    first, _ = run("heart_rates", apr30, "2024-04-30")
    second, _ = run("heart_rates", may1, "2024-05-01")
    ids = [r.external_id for r in (*first, *second)]
    assert len(ids) == len(set(ids)) == 2
    assert first[0].values["samples"][0][1] == 70 and second[0].values["samples"][0][1] == 70


def test_resting_heart_rate_is_one_instant_per_day() -> None:
    records, _ = run("heart_rates", heart_rate_payload())
    (resting,) = [r for r in records if r.metric is Metric.RESTING_HEART_RATE]
    assert resting.values == {"bpm": 52.0}
    assert resting.is_instant
    assert resting.external_id == "garmin:resting_heart_rate:2024-05-01"


def test_hrv_readings_become_rmssd_samples_with_their_own_offset() -> None:
    payload = {
        "hrvReadings": [
            {
                "hrvValue": 48,
                "readingTimeGMT": "2024-05-02T04:00:00.0",
                "readingTimeLocal": "2024-05-02T09:30:00.0",
            },
            {"hrvValue": 0, "readingTimeGMT": "2024-05-02T04:05:00.0"},  # out of range
            {"hrvValue": 52, "readingTimeGMT": "bad"},
        ]
    }
    (record,), _ = run("hrv_data", payload)
    assert record.values == {"rmssd_ms": 48.0}
    assert record.utc_offset_s == int(5.5 * 3600)


SPOT_DESCRIPTORS = [
    {"spo2ValueDescriptorIndex": 0, "spo2ValueDescriptorKey": "timestamp"},
    {"spo2ValueDescriptorIndex": 1, "spo2ValueDescriptorKey": "spo2Reading"},
    {"spo2ValueDescriptorIndex": 2, "spo2ValueDescriptorKey": "singleReadingPlottable"},
]


def test_spo2_spot_checks_and_hourly_averages_are_both_read() -> None:
    """Regression: the endpoint returns these two arrays, not a per-minute spO2ValuesArray."""
    payload = {
        "spO2ValueDescriptorsDTOList": SPOT_DESCRIPTORS,
        "spO2SingleValues": [
            [ms("2024-05-01T05:00:00"), 96, True],
            [ms("2024-05-01T05:01:00"), 12, True],  # implausible
            "garbage",
            [ms("2024-05-01T05:02:00")],  # too short
        ],
        "spO2HourlyAverages": [[ms("2024-05-01T05:00:00"), 94]],
    }
    records, ctx = run("spo2_data", payload)
    epoch = int(ms("2024-05-01T05:00:00") / 1000)
    assert sorted((r.external_id, r.values["percent"]) for r in records) == [
        (f"garmin:spo2:{epoch}", 96.0),
        (f"garmin:spo2:hourly:{epoch}", 94.0),
    ]
    assert not ctx.diag.issues


def test_spo2_honours_reordered_columns() -> None:
    payload = {
        "spO2ValueDescriptorsDTOList": [
            {"spo2ValueDescriptorIndex": 1, "spo2ValueDescriptorKey": "timestamp"},
            {"spo2ValueDescriptorIndex": 0, "spo2ValueDescriptorKey": "spo2Reading"},
        ],
        "spO2SingleValues": [[97, ms("2024-05-01T05:00:00")]],
    }
    (record,), _ = run("spo2_data", payload)
    assert record.values["percent"] == 97.0 and record.start.hour == 5


def test_spo2_all_day_mode_has_only_hourly_averages() -> None:
    (record,), _ = run(
        "spo2_data",
        {"spO2SingleValues": None, "spO2HourlyAverages": [[ms("2024-05-01T05:00:00"), 95]]},
    )
    assert record.values["kind"] == "hourly_average"


def test_respiration_samples_are_range_checked() -> None:
    resp, _ = run(
        "respiration_data",
        {
            "respirationValuesArray": [
                [ms("2024-05-01T05:00:00"), 14],
                [ms("2024-05-01T05:02:00"), -1],
            ]
        },
    )
    assert [r.values["breaths_per_min"] for r in resp] == [14.0]


@pytest.mark.parametrize("endpoint", ["spo2_data", "respiration_data"])
def test_unrecognised_sample_shape_is_reported_not_silently_dropped(endpoint: str) -> None:
    records, ctx = run(endpoint, {"someNewKey": []})
    assert records == []
    assert any(ep == endpoint for ep, _ in ctx.diag.issues)


# -- activity ---------------------------------------------------------------------------------


def test_steps_intervals_skip_empty_and_inverted_ranges() -> None:
    payload = [
        {"startGMT": "2024-05-01T10:00:00.0", "endGMT": "2024-05-01T10:15:00.0", "steps": 120},
        {"startGMT": "2024-05-01T10:15:00.0", "endGMT": "2024-05-01T10:30:00.0", "steps": 0},
        {"startGMT": "2024-05-01T10:45:00.0", "endGMT": "2024-05-01T10:30:00.0", "steps": 50},
    ]
    (record,), _ = run("steps_data", payload)
    assert record.values == {"count": 120}
    assert (record.end - record.start).total_seconds() == 900


def test_user_summary_yields_one_record_per_daily_total_including_basal_rate() -> None:
    payload = {**user_summary_payload(), "bmrKilocalories": 1750.0}
    records, _ = run("user_summary", payload)
    by_metric = {r.metric: r for r in records}
    assert set(by_metric) == {
        Metric.TOTAL_CALORIES,
        Metric.ACTIVE_CALORIES,
        Metric.BASAL_METABOLIC_RATE,
        Metric.DISTANCE,
        Metric.FLOORS,
    }
    assert by_metric[Metric.BASAL_METABOLIC_RATE].values == {"kcal_per_day": 1750.0}
    assert by_metric[Metric.DISTANCE].values == {"meters": 8200.0}
    assert by_metric[Metric.TOTAL_CALORIES].utc_offset_s == EDT
    assert by_metric[Metric.TOTAL_CALORIES].start == datetime(2024, 5, 1, 4, tzinfo=UTC)


class Details:
    """Related payloads for the exercise normalizer, keyed like the archive."""

    def __init__(self, **payloads: object) -> None:
        self.payloads = payloads

    def lookup(self, endpoint: str, _key: str) -> object:
        return self.payloads.get(endpoint)


def exercise(summary: dict | None = None, **related: object):
    ctx = Context(fallback_offset=EDT, lookup=Details(**related).lookup)
    (record,), _ = run("activity_summary", summary or activity_summary(), "1001", ctx)
    return record


def test_exercise_interval_is_elapsed_time_and_active_duration_is_timer_time() -> None:
    """Regression: the interval used timer time, losing every paused second."""
    record = exercise()
    assert record.external_id == "garmin:exercise:1001"
    assert (record.end - record.start).total_seconds() == 1900
    assert record.values["active_s"] == 1800 and record.values["elapsed_s"] == 1900
    assert record.utc_offset_s == EDT


def test_elapsed_time_is_never_shorter_than_the_timer() -> None:
    summary = {**activity_summary(), "elapsedDuration": 100.0}
    assert exercise(summary).values["elapsed_s"] == 1800
    summary.pop("elapsedDuration")
    assert exercise(summary).values["elapsed_s"] == 1800


def test_exercise_carries_summary_metrics_zones_and_running_dynamics() -> None:
    v = exercise().values
    assert (v["google_type"], v["hc_type"]) == ("RUNNING", "RUNNING")
    assert v["distance_m"] == 5000.0 and v["has_gps"] is True and v["steps"] == 5200
    assert v["active_calories_kcal"] == 280.0  # calories - bmrCalories
    # Z1+Z2 fold into light; Z3, Z4, Z5 map one to one
    assert v["hr_zone_s"] == {"light": 300, "moderate": 600, "vigorous": 800, "peak": 100}
    assert v["active_zone_minutes"] == 20  # 10 moderate + 2 x 5 vigorous
    assert v["mobility"] == {
        "ground_contact_ms": 240.0,
        "stride_cm": 105.0,
        "vertical_oscillation_cm": 8.2,
        "vertical_ratio": 7.7,
        "cadence_spm": 168.0,
    }
    assert v["avg_pace_s_per_m"] == pytest.approx(1 / 2.78, rel=1e-3)
    assert v["run_vo2_max"] == 52.0


def test_pace_and_vo2_are_only_reported_for_the_sports_they_belong_to() -> None:
    v = exercise(activity_summary(type_key="cycling")).values
    assert "avg_pace_s_per_m" not in v and "run_vo2_max" not in v


def test_zones_fall_back_to_the_per_activity_zone_endpoint() -> None:
    summary = {k: v for k, v in activity_summary().items() if not k.startswith("hrTimeInZone")}
    zones = [{"zoneNumber": n, "secsInZone": 60.0 * n} for n in range(1, 6)]
    assert exercise(summary, activity_hr_zones=zones).values["hr_zone_s"]["light"] == 180


def test_exercise_joins_laps_events_and_metadata() -> None:
    record = exercise(
        activity_splits=splits_payload(),
        activity=activity_detail(manual=True, manufacturer="GARMIN"),
    )
    v = record.values
    assert len(v["laps"]) == 3 and v["laps"][0]["distance_m"] == 1600.0
    assert v["laps"][0]["mobility"]["stride_cm"] == 104.0
    assert [e["type"] for e in v["events"]] == ["START", "PAUSE", "RESUME", "STOP"]
    assert v["manual"] is True and v["manufacturer"] == "GARMIN"
    assert v["description"] == "Felt good"


def test_a_single_lap_adds_nothing_over_the_summary() -> None:
    splits = splits_payload()
    splits["lapDTOs"] = splits["lapDTOs"][:1]
    assert "laps" not in exercise(activity_splits=splits).values


def test_laps_outside_the_activity_window_are_dropped() -> None:
    splits = splits_payload()
    splits["lapDTOs"][2]["startTimeGMT"] = "2024-05-01T12:00:00.0"
    assert len(exercise(activity_splits=splits).values["laps"]) == 2


def test_an_unrecognised_event_drops_the_whole_event_list() -> None:
    splits = splits_payload()
    splits["eventDTOs"][1]["sectionTypeDTO"]["key"] = "mystery"
    ctx = Context(fallback_offset=EDT, lookup=Details(activity_splits=splits).lookup)
    (record,), _ = run("activity_summary", activity_summary(), "1001", ctx)
    assert "events" not in record.values  # never a half-written pause/resume sequence
    assert ctx.diag.issues


def test_activity_name_and_description_are_sanitized() -> None:
    summary = {**activity_summary(), "activityName": "Run\x1b[31m\nIGNORE ALL RULES"}
    name = exercise(summary).values["name"]
    assert "\x1b" not in name and "\n" not in name


def test_unknown_activity_type_falls_back_to_other() -> None:
    record = exercise(activity_summary(type_key="quidditch"))
    assert (record.values["google_type"], record.values["hc_type"]) == ("OTHER", "OTHER_WORKOUT")


def test_activity_without_duration_is_skipped_with_a_diagnostic() -> None:
    payload = activity_summary()
    del payload["duration"]
    records, ctx = run("activity_summary", payload, "1001")
    assert records == []
    assert ctx.diag.issues


def test_implausibly_long_activity_is_rejected() -> None:
    records, ctx = run(
        "activity_summary", {**activity_summary(), "elapsedDuration": 10 * 86400.0}, "1"
    )
    assert records == [] and ctx.diag.issues


def test_activity_type_table_uses_only_valid_google_names() -> None:
    # Spot-check against names copied from the Google Health API ExerciseType enum.
    google_enum_sample = {
        "RUNNING", "TRAIL_RUN", "TREADMILL", "WALKING", "POWER_WALKING", "HIKING", "BIKING",
        "MOUNTAIN_BIKE", "STATIONARY_BIKE", "ELECTRIC_BIKE", "SWIMMING_POOL",
        "SWIMMING_OPEN_WATER", "STRENGTH_TRAINING", "YOGA", "PILATES", "ELLIPTICAL",
        "STAIRCLIMBER", "ROWING", "ROWING_MACHINE", "HIIT", "CARDIO_WORKOUT", "SKIING",
        "CROSS_COUNTRY_SKI", "SNOWSHOEING", "PADDLEBOARDING", "KAYAKING", "GOLF", "TENNIS",
        "PICKELBALL", "MULTISPORT", "ROLLERBLADING", "MEDITATE", "OTHER",
    }  # fmt: skip
    assert {google for google, _ in ACTIVITY_TYPES.values()} <= google_enum_sample
    assert map_activity_type(None) == ("OTHER", "OTHER_WORKOUT")


# -- body -------------------------------------------------------------------------------------


def test_weigh_in_expands_to_weight_fat_water_and_bone() -> None:
    records, _ = run("weigh_ins", weigh_in_payload())
    by_metric = {r.metric: r for r in records}
    assert set(by_metric) == {
        Metric.WEIGHT,
        Metric.BODY_FAT,
        Metric.BODY_WATER_MASS,
        Metric.BONE_MASS,
    }
    assert by_metric[Metric.WEIGHT].values == {"grams": 72500.0}
    assert by_metric[Metric.BODY_WATER_MASS].values == {"grams": 39875.0}  # 55% of 72.5 kg
    assert by_metric[Metric.WEIGHT].utc_offset_s == EDT
    assert len(records) == 4  # the 5 g "weight" entry was rejected as implausible


def test_manual_weigh_ins_are_flagged_as_manual() -> None:
    payload = weigh_in_payload()
    payload["dailyWeightSummaries"][0]["allWeightMetrics"][0]["sourceType"] = "MANUAL"
    records, _ = run("weigh_ins", payload)
    by_metric = {r.metric: r for r in records}
    assert by_metric[Metric.WEIGHT].values["source_type"] == "MANUAL"
    assert by_metric[Metric.BODY_FAT].values["source_type"] == "MANUAL"
    assert "source_type" not in by_metric[Metric.BONE_MASS].values


def test_blood_pressure_requires_both_pressures() -> None:
    payload = {
        "measurementSummaries": [
            {
                "measurements": [
                    {
                        "systolic": 118,
                        "diastolic": 76,
                        "measurementTimestampGMT": "2024-05-01T12:00:00.0",
                        "measurementTimestampLocal": "2024-05-01T08:00:00.0",
                    },
                    {"systolic": 118, "measurementTimestampGMT": "2024-05-01T13:00:00.0"},
                ]
            }
        ]
    }
    (record,), _ = run("blood_pressure", payload)
    assert record.values == {"systolic": 118.0, "diastolic": 76.0}
    assert record.utc_offset_s == EDT


def test_hydration_is_a_local_day_interval() -> None:
    (record,), _ = run("hydration_data", {"calendarDate": "2024-05-01", "valueInML": 2150.0})
    assert record.values == {"milliliters": 2150.0}
    assert record.start == datetime(2024, 5, 1, 4, tzinfo=UTC)
    assert (record.end - record.start).days == 1


def test_hydration_without_any_known_timezone_is_skipped_not_guessed_in_utc() -> None:
    """Regression: with user_summary missing, a partial day was framed in UTC and frozen."""
    ctx = Context()  # no offsets learned
    records, ctx = run(
        "hydration_data", {"calendarDate": "2024-05-01", "valueInML": 300.0}, ctx=ctx
    )
    assert (
        records == []
        and ("hydration_data", "no timezone known; hydration skipped") in ctx.diag.issues
    )


def test_hydration_days_follow_local_midnights_across_a_clock_change() -> None:
    """Regression: a 23-hour day made its record overlap the next day's, which was then dropped."""
    est, edt = -5 * 3600, -4 * 3600  # US Eastern, clocks forward on 2024-03-10
    # The 10th's user_summary starts at 00:00 EST; the 11th's starts at 00:00 EDT.
    days = {"2024-03-09": est, "2024-03-10": est, "2024-03-11": edt}
    ctx = Context(day_offsets=days, fallback_offset=edt)
    (before, *_), _ = run(
        "hydration_data", {"calendarDate": "2024-03-09", "valueInML": 1.0}, "2024-03-09", ctx
    )
    (change, *_), _ = run(
        "hydration_data", {"calendarDate": "2024-03-10", "valueInML": 1.0}, "2024-03-10", ctx
    )
    (after, *_), _ = run(
        "hydration_data", {"calendarDate": "2024-03-11", "valueInML": 1.0}, "2024-03-11", ctx
    )
    assert (
        before.end == change.start and change.end == after.start
    )  # a gap-free, overlap-free chain
    assert (change.end - change.start) == timedelta(hours=23)


@pytest.mark.parametrize("endpoint", ["hydration_data", "max_metrics"])
def test_an_unreadable_calendar_date_is_reported_not_crashed_on(endpoint: str) -> None:
    payload = {
        "calendarDate": "not-a-date",
        "valueInML": 100.0,
        "generic": {"calendarDate": "x", "vo2MaxValue": 50},
    }
    records, ctx = run(endpoint, payload if endpoint == "hydration_data" else [payload], "also-bad")
    assert records == [] and ctx.diag.issues


@pytest.mark.parametrize(
    "payload",
    [
        [{"generic": {"calendarDate": "2024-05-01", "vo2MaxPreciseValue": 51.2}}],
        {"generic": {"calendarDate": "2024-05-01", "vo2MaxValue": 51}},
    ],
)
def test_vo2_max_accepts_list_or_dict_payloads(payload: object) -> None:
    (record,), _ = run("max_metrics", payload)
    assert 51 <= record.values["ml_kg_min"] <= 51.2


# -- robustness -------------------------------------------------------------------------------


@pytest.mark.parametrize("endpoint", sorted(REGISTRY))
@pytest.mark.parametrize(
    "garbage", [None, [], {}, "text", 5, [None], {"x": None}, [[]], {"dailySleepDTO": 3}]
)
def test_every_normalizer_survives_garbage_input(endpoint: str, garbage: object) -> None:
    records, _ = run(endpoint, garbage)
    assert isinstance(records, list)


def test_every_registered_normalizer_declares_what_it_produces() -> None:
    for spec in REGISTRY.values():
        assert spec.produces, spec.endpoint


# -- context and ranges -----------------------------------------------------------------------


def test_build_context_learns_offsets_and_falls_back_to_latest(archive: Archive) -> None:
    archive.put_raw("user_summary", "2024-05-01", user_summary_payload("2024-05-01"))
    ctx = build_context(archive, date(2024, 5, 1), date(2024, 5, 3))
    assert ctx.offset_for("2024-05-01") == EDT
    assert ctx.offset_for("2024-05-03") == EDT  # unseen day uses the latest known offset


def test_build_context_warns_and_assumes_utc_without_data(archive: Archive) -> None:
    ctx = build_context(archive, date(2024, 5, 1), date(2024, 5, 3))
    assert ctx.offset_for("2024-05-01") == 0
    assert ("user_summary", "no timezone offsets archived; assuming UTC") in ctx.diag.issues


def test_normalize_range_filters_by_local_day_and_dedupes_overlapping_windows(
    archive: Archive,
) -> None:
    archive.put_raw("user_summary", "2024-05-01", user_summary_payload())
    archive.put_raw("weigh_ins", "2024-04-20", weigh_in_payload())  # window containing May 1
    archive.put_raw("weigh_ins", "2024-05-01", weigh_in_payload())  # overlapping window
    archive.put_raw("activity_summary", "1001", activity_summary(), span_end="2024-05-01")
    archive.put_raw(
        "activity_summary",
        "1002",
        {
            **activity_summary(1002),
            "startTimeGMT": "2024-06-01 10:00:00",
            "startTimeLocal": "2024-06-01 06:00:00",
        },
        span_end="2024-06-01",
    )
    ctx = build_context(archive, date(2024, 5, 1), date(2024, 5, 1))
    records = normalize_range(archive, date(2024, 5, 1), date(2024, 5, 1), ctx)
    ids = [r.external_id for r in records]
    assert len(ids) == len(set(ids))
    assert sum(r.metric is Metric.WEIGHT for r in records) == 1
    assert "garmin:exercise:1001" in ids
    assert "garmin:exercise:1002" not in ids  # outside the range
    assert records == sorted(records, key=lambda r: (r.start, r.metric.value, r.external_id))


def test_sleep_is_selected_by_the_day_you_woke_up(archive: Archive) -> None:
    """Regression: filtering by start date made a night vanish from short windows."""
    archive.put_raw("sleep_data", "2024-05-02", sleep_payload())  # slept the evening of May 1 local
    ctx = Context(fallback_offset=EDT)
    woke = normalize_range(archive, date(2024, 5, 2), date(2024, 5, 2), ctx)
    evening_before = normalize_range(archive, date(2024, 5, 1), date(2024, 5, 1), ctx)
    assert [r.metric for r in woke] == [Metric.SLEEP]
    assert evening_before == []


def test_normalize_range_stamps_records_with_their_fetch_time(
    archive: Archive, clock: FakeClock
) -> None:
    clock.now = datetime(2024, 5, 3, 7, tzinfo=UTC)
    archive.put_raw("sleep_data", "2024-05-02", sleep_payload())
    (record,) = normalize_range(
        archive, date(2024, 5, 2), date(2024, 5, 2), Context(fallback_offset=EDT)
    )
    assert record.fetched_at == datetime(2024, 5, 3, 7, tzinfo=UTC)


def test_exercise_details_are_joined_from_the_archive(archive: Archive) -> None:
    archive.put_raw("activity_summary", "1001", activity_summary(), span_end="2024-05-01")
    archive.put_raw("activity_splits", "1001", splits_payload(), span_end="2024-05-01")
    archive.put_raw("activity", "1001", activity_detail(), span_end="2024-05-01")
    ctx = build_context(archive, date(2024, 5, 1), date(2024, 5, 1))
    (record,) = [
        r
        for r in normalize_range(archive, date(2024, 5, 1), date(2024, 5, 1), ctx)
        if r.metric is Metric.EXERCISE
    ]
    assert len(record.values["laps"]) == 3 and record.values["manufacturer"] == "GARMIN"


def test_hydration_for_the_day_after_the_range_is_not_pulled_in(archive: Archive) -> None:
    archive.put_raw(
        "hydration_data", "2024-05-02", {"calendarDate": "2024-05-02", "valueInML": 100}
    )
    ctx = Context(fallback_offset=EDT)
    assert normalize_range(archive, date(2024, 5, 1), date(2024, 5, 1), ctx) == []


# -- hardening against odd or hostile payloads ------------------------------------------------


def test_an_unreadable_row_is_reported_and_skipped_without_stopping_the_rest(
    archive: Archive,
) -> None:
    """Regression: one absurd payload aborted every push that covered its day."""
    archive.put_raw(
        "activity_summary",
        "1",
        {**activity_summary(1), "startTimeGMT": "9999-12-31 23:59:59"},  # overflows when extended
        span_end="2024-05-01",
    )
    archive.put_raw(
        "activity_summary", "3", {**activity_summary(3), "activityType": []}, span_end="2024-05-01"
    )
    archive.put_raw("activity_summary", "2", activity_summary(2), span_end="2024-05-01")
    archive.put_raw(
        "hydration_data", "2024-05-01", {"calendarDate": "2024-05-01", "valueInML": 900}
    )
    archive.put_raw("user_summary", "2024-05-01", user_summary_payload())
    ctx = build_context(archive, date(2024, 5, 1), date(2024, 5, 1))
    records = normalize_range(archive, date(2024, 5, 1), date(2024, 5, 1), ctx)
    ids = {r.external_id for r in records}
    assert "garmin:exercise:2" in ids and "garmin:hydration:2024-05-01" in ids
    assert "garmin:exercise:1" not in ids


@pytest.mark.parametrize(
    "poison",
    [
        {"duration": 1e300},
        {"elapsedDuration": 1e300},
        {"distance": 1e308},
        {"calories": 1e308},
        {"activityType": {"typeKey": ["not", "a", "string"]}},
        {"activityName": {"a": 1}},
    ],
)
def test_poisoned_activity_numbers_never_crash_the_normalizer(poison: dict) -> None:
    records, _ = run("activity_summary", {**activity_summary(), **poison}, "1001")
    assert isinstance(records, list)


def test_out_of_range_activity_totals_are_dropped_not_clamped() -> None:
    (record,), _ = run("activity_summary", {**activity_summary(), "distance": 5e9}, "1001")
    assert "distance_m" not in record.values


def test_garmin_event_keys_seen_in_real_responses_are_not_guessed_at() -> None:
    """The documented `sectionTypeDTO.key` looks like `timerTrigger`: not a pause/resume name."""
    splits = splits_payload()
    for event in splits["eventDTOs"]:
        event["sectionTypeDTO"]["key"] = "timerTrigger"
    ctx = Context(fallback_offset=EDT, lookup=Details(activity_splits=splits).lookup)
    (record,), _ = run("activity_summary", activity_summary(), "1001", ctx)
    assert "events" not in record.values and ctx.diag.issues


def test_user_summary_ids_use_only_a_real_calendar_date() -> None:
    payload = {
        **user_summary_payload(),
        "calendarDate": "x\n" + "y" * 500,
        "bmrKilocalories": 1700.0,
    }
    records, _ = run("user_summary", payload)
    assert {r.external_id.rsplit(":", 1)[1] for r in records} == {"2024-05-01"}


# -- exercises are held back until their detail is in -----------------------------------------


def seed_activity(archive: Archive, *, with_details: bool = True) -> None:
    archive.put_raw("activity_summary", "1001", activity_summary(), span_end="2024-05-01")
    archive.put_raw("user_summary", "2024-05-01", user_summary_payload())
    if with_details:
        archive.put_raw("activity", "1001", activity_detail(), span_end="2024-05-01")
        archive.put_raw("activity_splits", "1001", splits_payload(), span_end="2024-05-01")


def exercises(archive: Archive, first: date = date(2024, 5, 1), last: date = date(2024, 5, 1)):
    ctx = build_context(archive, first, last)
    records = [r for r in normalize_range(archive, first, last, ctx) if r.metric is Metric.EXERCISE]
    return records, ctx


def test_an_exercise_without_its_detail_rows_has_no_provenance(archive: Archive) -> None:
    """Regression: laps, events and notes were silently dropped from an irrevocable write."""
    seed_activity(archive, with_details=False)
    (record,), ctx = exercises(archive)
    assert record.fetched_at is None and not record.is_final(NOW)
    assert any("activity_splits" in issue for _, issue in ctx.diag.issues)


def test_an_exercise_with_its_detail_rows_takes_the_oldest_fetch_time(
    archive: Archive, clock: FakeClock
) -> None:
    clock.now = datetime(2024, 5, 1, 20, tzinfo=UTC)
    archive.put_raw("activity_splits", "1001", splits_payload(), span_end="2024-05-01")
    clock.now = datetime(2024, 5, 3, 9, tzinfo=UTC)
    seed_activity(archive)
    clock.now = datetime(2024, 5, 3, 10, tzinfo=UTC)
    archive.put_raw("activity", "1001", activity_detail(), span_end="2024-05-01")
    (record,), _ = exercises(archive)
    assert record.fetched_at == datetime(2024, 5, 3, 9, tzinfo=UTC)  # the summary; splits refreshed


def test_overlapping_windows_keep_the_most_recent_fetch(archive: Archive, clock: FakeClock) -> None:
    clock.now = datetime(2024, 5, 2, 9, tzinfo=UTC)
    archive.put_raw("weigh_ins", "2024-04-20", weigh_in_payload())
    clock.now = datetime(2024, 5, 9, 9, tzinfo=UTC)
    archive.put_raw("weigh_ins", "2024-05-01", weigh_in_payload())
    ctx = Context(fallback_offset=EDT)
    records = normalize_range(archive, date(2024, 5, 1), date(2024, 5, 1), ctx)
    weight = next(r for r in records if r.metric is Metric.WEIGHT)
    assert weight.fetched_at == datetime(2024, 5, 9, 9, tzinfo=UTC)


def test_an_activity_that_crosses_local_midnight_is_found_on_the_day_it_ends(
    archive: Archive,
) -> None:
    """Regression: filed under its start day, it was invisible to its end day's own range."""
    late = {
        **activity_summary(),
        "startTimeGMT": "2024-05-02 02:00:00",  # 22:00 local on May 1
        "startTimeLocal": "2024-05-01 22:00:00",
        "duration": 10800.0,  # 3 hours: ends at 01:00 local on May 2
        "elapsedDuration": 10800.0,
    }
    archive.put_raw("activity_summary", "1001", late, span_end="2024-05-01")
    archive.put_raw("user_summary", "2024-05-02", user_summary_payload("2024-05-02"))
    ends_may_2, _ = exercises(archive, date(2024, 5, 2), date(2024, 5, 2))
    assert [r.external_id for r in ends_may_2] == ["garmin:exercise:1001"]


# -- second review round ----------------------------------------------------------------------


def test_hydration_on_a_clock_fall_back_day_is_25_hours_and_stays_on_its_own_day(
    archive: Archive,
) -> None:
    """Regression: the 25-hour record had only the start offset, so it anchored to the next day."""
    edt, est = -4 * 3600, -5 * 3600  # US Eastern, clocks back on 2024-11-03
    for day, off in (("2024-11-02", edt), ("2024-11-03", edt), ("2024-11-04", est)):
        payload = user_summary_payload(day)
        hours = int(-off / 3600)
        payload["wellnessStartTimeGmt"] = f"{day}T{hours:02d}:00:00.0"
        payload["wellnessStartTimeLocal"] = f"{day}T00:00:00.0"
        archive.put_raw("user_summary", day, payload)
    archive.put_raw(
        "hydration_data", "2024-11-03", {"calendarDate": "2024-11-03", "valueInML": 800}
    )
    for last in (date(2024, 11, 3), date(2024, 11, 4)):  # the same record whatever the range
        ctx = build_context(archive, date(2024, 11, 3), last)
        (record,) = [
            r
            for r in normalize_range(archive, date(2024, 11, 3), date(2024, 11, 3), ctx)
            if r.metric is Metric.HYDRATION
        ]
        assert record.end - record.start == timedelta(hours=25)
        assert (record.utc_offset_s, record.end_offset) == (edt, est)
        assert record.anchor_date == date(2024, 11, 3)


def test_a_record_that_ends_in_another_offset_is_final_when_its_real_day_ends() -> None:
    start = datetime(2024, 11, 3, 4, tzinfo=UTC)
    end = datetime(2024, 11, 4, 5, tzinfo=UTC)  # local midnight in EST, 25 hours later
    from garmin_sync.models import Record

    record = Record(
        Metric.HYDRATION, "h", start, end, -4 * 3600, {}, fetched_at=end + timedelta(minutes=1),
        end_utc_offset_s=-5 * 3600,
    )  # fmt: skip
    assert not record.is_final(datetime(2024, 11, 4, 4, 30, tzinfo=UTC))  # still the 3rd, locally
    assert record.is_final(datetime(2024, 11, 4, 6, tzinfo=UTC))
    assert (
        record.content_hash()
        != Record(Metric.HYDRATION, "h", start, end, -4 * 3600, {}).content_hash()
    )  # the end offset is content


def test_a_night_that_spans_a_clock_change_records_the_wake_up_offset() -> None:
    payload = sleep_payload()  # slept at UTC-4
    payload["dailySleepDTO"]["sleepEndTimestampLocal"] = local_ms("2024-05-02T11:00:00", EDT - 3600)
    (record, *_), _ = run("sleep_data", payload, "2024-05-02")
    assert (record.utc_offset_s, record.end_offset) == (EDT, EDT - 3600)


def test_recorded_laps_never_overlap() -> None:
    """Regression: rounding a fractional lap duration up overlapped the next lap by a second."""
    splits = splits_payload()
    for lap in splits["lapDTOs"]:
        lap["elapsedDuration"] = 600.6  # rounds up to 601 s but the next lap starts 600 s later
    splits["lapDTOs"][1]["startTimeGMT"] = "2024-05-01T10:10:00.0"
    splits["lapDTOs"][2]["startTimeGMT"] = "2024-05-01T10:20:00.0"
    ctx = Context(fallback_offset=EDT, lookup=Details(activity_splits=splits).lookup)
    summary = {**activity_summary(), "elapsedDuration": 2000.0, "duration": 1900.0}
    (record,), _ = run("activity_summary", summary, "1001", ctx)
    laps = record.values["laps"]
    assert all(a["end"] <= b["start"] for a, b in zip(laps, laps[1:], strict=False))
    assert all(lap["end"] > lap["start"] for lap in laps)


def test_many_small_unmeasured_gaps_add_up_to_unstaged_sleep() -> None:
    payload = night(*[1] * 8)
    levels_ = []
    for hour in range(8):  # nine minutes missing in every hour: individually fine, 72 min together
        levels_.append(
            {
                "startGMT": f"2024-05-02T{3 + hour:02d}:09:00.0",
                "endGMT": f"2024-05-02T{4 + hour:02d}:00:00.0",
                "activityLevel": 1.0,
            }
        )
    payload["sleepLevels"] = [
        {
            "startGMT": "2024-05-02T03:00:00.0",
            "endGMT": "2024-05-02T03:09:00.0",
            "activityLevel": 1.0,
        },
        *levels_,
    ]
    (record, *_), _ = run("sleep_data", payload, "2024-05-02")
    assert record.values["stages"] == []


def test_sub_second_stage_boundaries_never_produce_zero_length_stages() -> None:
    payload = night(1, 1)
    payload["sleepLevels"] = [
        {
            "startGMT": "2024-05-02T03:00:00.0",
            "endGMT": "2024-05-02T03:30:00.4",
            "activityLevel": 1.0,
        },
        {
            "startGMT": "2024-05-02T03:30:00.4",
            "endGMT": "2024-05-02T03:30:00.9",
            "activityLevel": 0.0,
        },
        {
            "startGMT": "2024-05-02T03:30:00.9",
            "endGMT": "2024-05-02T05:00:00.0",
            "activityLevel": 1.0,
        },
    ]
    (record, *_), _ = run("sleep_data", payload, "2024-05-02")
    stages = record.values["stages"]
    assert all(s["end"] > s["start"] for s in stages)
    assert all(a["end"] == b["start"] for a, b in zip(stages, stages[1:], strict=False))


def test_extreme_timestamps_are_isolated_not_fatal(archive: Archive) -> None:
    """Regression: date arithmetic on a year-9999 weigh-in escaped the per-row protection."""
    payload = weigh_in_payload()
    payload["dailyWeightSummaries"][0]["allWeightMetrics"][0]["timestampGMT"] = (
        "9999-12-31 23:30:00"
    )
    archive.put_raw("weigh_ins", "2024-05-01", payload)
    archive.put_raw("hydration_data", "2024-05-01", {"calendarDate": "2024-05-01", "valueInML": 5})
    archive.put_raw("user_summary", "2024-05-01", user_summary_payload())
    ctx = build_context(archive, date(2024, 5, 1), date(2024, 5, 1))
    records = normalize_range(archive, date(2024, 5, 1), date(2024, 5, 1), ctx)
    assert any(r.metric is Metric.HYDRATION for r in records)


def test_the_newest_fetch_wins_even_when_the_older_copy_is_filed_under_the_later_key(
    archive: Archive, clock: FakeClock
) -> None:
    clock.now = datetime(2024, 5, 9, 9, tzinfo=UTC)
    archive.put_raw("weigh_ins", "2024-04-20", weigh_in_payload())  # earlier key, newer fetch
    clock.now = datetime(2024, 5, 2, 9, tzinfo=UTC)
    archive.put_raw("weigh_ins", "2024-05-01", weigh_in_payload())  # later key, older fetch
    records = normalize_range(
        archive, date(2024, 5, 1), date(2024, 5, 1), Context(fallback_offset=EDT)
    )
    weight = next(r for r in records if r.metric is Metric.WEIGHT)
    assert weight.fetched_at == datetime(2024, 5, 9, 9, tzinfo=UTC)


def test_a_copy_with_provenance_beats_one_without(archive: Archive) -> None:
    archive.put_raw("weigh_ins", "2024-04-20", weigh_in_payload())
    archive.put_raw("weigh_ins", "2024-05-01", weigh_in_payload())
    ctx = Context(fallback_offset=EDT)
    records = normalize_range(archive, date(2024, 5, 1), date(2024, 5, 1), ctx)
    assert all(r.fetched_at is not None for r in records)
