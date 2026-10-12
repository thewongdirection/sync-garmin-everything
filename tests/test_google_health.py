"""Google Health payload builders, the HTTP client, and the sink (duplicates, limits, errors)."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta

import pytest

from garmin_sync.coverage import GOOGLE_WRITABLE, TARGETS
from garmin_sync.models import Metric, Record
from garmin_sync.sinks import google_auth as ga
from garmin_sync.sinks.base import Outcome, Status
from garmin_sync.sinks.google_client import (
    BASE_URL,
    GoogleApiError,
    GoogleClient,
    GooglePermissionError,
)
from garmin_sync.sinks.google_health import (
    GoogleHealthSink,
    _dedupe_filter,
    _overlaps,
    _payload_key,
    _Unverifiable,
)
from garmin_sync.sinks.google_payloads import (
    Unbuildable,
    build_data_point,
    data_point_id,
)
from tests.conftest import make_record
from tests.fakes_http import FakeHttp, FakeResponse
from tests.helpers import EDT
from tests.test_push import sample_records

T0 = datetime(2024, 5, 1, 10, 0, tzinfo=UTC)
STAMP = int(T0.timestamp())


def by_metric(metric: Metric) -> Record:
    return next(r for r in sample_records() if r.metric is metric)


def rich_exercise() -> Record:
    """A workout with everything the normalizer can produce."""
    values = {
        "activity_id": "1001",
        "google_type": "RUNNING",
        "hc_type": "RUNNING",
        "name": "Morning Run",
        "description": "Felt good",
        "active_s": 1800,
        "elapsed_s": 1900,
        "has_gps": True,
        "manual": False,
        "distance_m": 5000.0,
        "calories_kcal": 320.0,
        "avg_hr": 150.4,
        "elevation_gain_m": 40.0,
        "steps": 5200,
        "avg_speed_mps": 2.78,
        "avg_pace_s_per_m": 0.3597,
        "run_vo2_max": 52.0,
        "active_zone_minutes": 20,
        "hr_zone_s": {"light": 300, "moderate": 600, "vigorous": 800, "peak": 100},
        "mobility": {
            "ground_contact_ms": 240.0,
            "stride_cm": 105.0,
            "vertical_oscillation_cm": 8.2,
            "vertical_ratio": 7.7,
            "cadence_spm": 168.0,
        },
        "laps": [
            {"start": STAMP, "end": STAMP + 900, "active_s": 880, "distance_m": 2500.0,
             "avg_hr": 148.0, "mobility": {"stride_cm": 104.0}},
            {"start": STAMP + 900, "end": STAMP + 1900},
        ],
        "events": [
            {"time": STAMP, "type": "START"},
            {"time": STAMP + 600, "type": "PAUSE"},
            {"time": STAMP + 700, "type": "RESUME"},
        ],
    }  # fmt: skip
    return Record(
        Metric.EXERCISE,
        "garmin:exercise:1001",
        T0,
        T0 + timedelta(seconds=1900),
        EDT,
        values,
        fetched_at=T0 + timedelta(days=2),
    )


# -- payload builders -------------------------------------------------------------------------


def test_weight_and_body_fat_use_grams_percent_and_sample_time() -> None:
    data_type, body = build_data_point(by_metric(Metric.WEIGHT))
    assert data_type == "weight"
    assert body["weight"] == {
        "sampleTime": {"physicalTime": "2024-05-01T10:00:00Z", "utcOffset": "-14400s"},
        "weightGrams": 72500.0,
    }
    data_type, body = build_data_point(by_metric(Metric.BODY_FAT))
    assert data_type == "body-fat" and body["bodyFat"]["percentage"] == 18.5


def test_manual_weigh_ins_are_marked_manual() -> None:
    record = make_record(values={"grams": 70000.0, "source_type": "MANUAL"})
    assert build_data_point(record)[1]["dataSource"]["recordingMethod"] == "MANUAL"
    assert "recordingMethod" not in build_data_point(by_metric(Metric.WEIGHT))[1]["dataSource"]


def test_hydration_uses_milliliters_and_session_interval() -> None:
    data_type, body = build_data_point(by_metric(Metric.HYDRATION))
    assert data_type == "hydration-log"
    log = body["hydrationLog"]
    assert log["amountConsumed"] == {"milliliters": 500.0, "userProvidedUnit": "MILLILITER"}
    assert log["interval"]["startUtcOffset"] == "-14400s" and log["interval"]["endTime"].endswith(
        "Z"
    )


def test_exercise_interval_is_elapsed_time_and_active_duration_is_timer_time() -> None:
    """Regression: both used the timer duration, so pauses vanished from the workout."""
    _, body = build_data_point(rich_exercise())
    exercise = body["exercise"]
    assert exercise["interval"]["startTime"] == "2024-05-01T10:00:00Z"
    assert exercise["interval"]["endTime"] == "2024-05-01T10:31:40Z"  # +1900 s
    assert exercise["activeDuration"] == "1800s"


def test_exercise_metrics_use_documented_units_and_int64_strings() -> None:
    data_type, body = build_data_point(rich_exercise())
    exercise = body["exercise"]
    assert data_type == "exercise" and exercise["exerciseType"] == "RUNNING"
    assert exercise["displayName"] == "Morning Run"
    assert exercise["notes"] == "Morning Run\nFelt good"
    assert exercise["exerciseMetadata"] == {"hasGps": True}
    assert exercise["metricsSummary"] == {
        "caloriesKcal": 320.0,
        "distanceMillimeters": 5_000_000,
        "steps": "5200",
        "averageHeartRateBeatsPerMinute": "150",
        "averageSpeedMillimetersPerSecond": 2780,
        "averagePaceSecondsPerMeter": 0.3597,
        "elevationGainMillimeters": 40_000,
        "runVo2Max": 52.0,
        "activeZoneMinutes": "20",
        "heartRateZoneDurations": {
            "lightTime": "300s",
            "moderateTime": "600s",
            "vigorousTime": "800s",
            "peakTime": "100s",
        },
        "mobilityMetrics": {
            "avgGroundContactTimeDuration": "0.240s",
            "avgStrideLengthMillimeters": "1050",
            "avgVerticalOscillationMillimeters": "82",
            "avgVerticalRatio": 7.7,
            "avgCadenceStepsPerMinute": 168.0,
        },
    }


def test_laps_are_sent_as_split_summaries_without_the_output_only_duration() -> None:
    _, body = build_data_point(rich_exercise())
    exercise = body["exercise"]
    assert "splits" not in exercise  # that field is Google's own default 1 km / 1 mile splits
    first, second = exercise["splitSummaries"]
    assert first["splitType"] == "MANUAL"
    assert (first["startTime"], first["endTime"]) == (
        "2024-05-01T10:00:00Z",
        "2024-05-01T10:15:00Z",
    )
    assert first["startUtcOffset"] == first["endUtcOffset"] == "-14400s"
    assert first["metricsSummary"] == {
        "distanceMillimeters": 2_500_000,
        "averageHeartRateBeatsPerMinute": "148",
        "mobilityMetrics": {"avgStrideLengthMillimeters": "1040"},
    }
    assert "activeDuration" not in first  # Google computes it
    assert second["metricsSummary"] == {}  # still present: Google requires the field


def test_pause_and_resume_events_carry_offsets() -> None:
    _, body = build_data_point(rich_exercise())
    assert body["exercise"]["exerciseEvents"] == [
        {
            "eventTime": "2024-05-01T10:00:00Z",
            "eventUtcOffset": "-14400s",
            "exerciseEventType": "START",
        },
        {
            "eventTime": "2024-05-01T10:10:00Z",
            "eventUtcOffset": "-14400s",
            "exerciseEventType": "PAUSE",
        },
        {
            "eventTime": "2024-05-01T10:11:40Z",
            "eventUtcOffset": "-14400s",
            "exerciseEventType": "RESUME",
        },
    ]


def test_a_minimal_exercise_has_no_empty_optional_sections() -> None:
    record = by_metric(Metric.EXERCISE)
    record.values.pop("laps")
    _, body = build_data_point(record)
    exercise = body["exercise"]
    assert "splitSummaries" not in exercise and "exerciseEvents" not in exercise
    assert body["exercise"]["notes"] == "Run\nFelt good"


def test_manual_and_device_details_reach_the_data_source() -> None:
    record = rich_exercise()
    record.values.update(manual=True, manufacturer="GARMIN")
    source = build_data_point(record)[1]["dataSource"]
    assert source == {
        "recordingMethod": "MANUAL",
        "device": {"manufacturer": "GARMIN", "formFactor": "WATCH"},
    }
    assert (
        build_data_point(rich_exercise())[1]["dataSource"]["recordingMethod"] == "ACTIVELY_MEASURED"
    )


def test_sleep_carries_stages_offsets_and_an_external_id() -> None:
    data_type, body = build_data_point(by_metric(Metric.SLEEP))
    sleep = body["sleep"]
    assert data_type == "sleep" and sleep["type"] == "STAGES"
    assert sleep["stages"][0]["type"] == "DEEP"
    assert sleep["stages"][0]["startTime"] == "2024-05-01T10:00:00Z"
    assert sleep["metadata"] == {"externalId": "garmin:sleep:x"}
    assert body["dataSource"]["recordingMethod"] == "PASSIVELY_MEASURED"


def test_sleep_without_stages_is_written_as_classic_sleep() -> None:
    record = make_record(Metric.SLEEP, "s", T0, T0 + timedelta(hours=8), {"stages": []})
    _, body = build_data_point(record)
    assert body["sleep"]["type"] == "CLASSIC" and "stages" not in body["sleep"]


def test_a_record_missing_required_values_is_unbuildable_not_a_crash() -> None:
    record = make_record(Metric.WEIGHT, values={})
    with pytest.raises(Unbuildable, match="grams"):
        build_data_point(record)


def test_every_body_names_the_garmin_device_and_is_json_serialisable() -> None:
    for metric in GOOGLE_WRITABLE:
        _, body = build_data_point(by_metric(metric))
        json.dumps(body)
        assert body["dataSource"]["device"]["manufacturer"] == "Garmin"


@pytest.mark.parametrize("metric", sorted(set(Metric) - GOOGLE_WRITABLE))
def test_read_only_types_are_refused_rather_than_sent(metric: Metric) -> None:
    with pytest.raises(Unbuildable, match="not writable"):
        build_data_point(by_metric(metric))


def test_writable_set_matches_what_google_documents() -> None:
    # data-types page, 2026-10-12: create is listed for exactly these among our metrics.
    assert {
        Metric.SLEEP,
        Metric.EXERCISE,
        Metric.WEIGHT,
        Metric.BODY_FAT,
        Metric.HYDRATION,
    } == GOOGLE_WRITABLE
    assert {TARGETS[m].google_type for m in GOOGLE_WRITABLE} == {
        "sleep",
        "exercise",
        "weight",
        "body-fat",
        "hydration-log",
    }


# -- client-chosen data point ids (idempotent creates) ----------------------------------------


def test_data_point_ids_are_stable_valid_and_distinct() -> None:
    records = [by_metric(m) for m in GOOGLE_WRITABLE] + [rich_exercise()]
    ids = {data_point_id(r) for r in records}
    assert len(ids) == len(records)
    for point_id in ids:
        assert re.fullmatch(r"[a-z0-9-]{4,63}", point_id)  # Google's documented constraint
    assert data_point_id(rich_exercise()) == data_point_id(rich_exercise())


def test_every_body_carries_its_client_chosen_name() -> None:
    for metric in GOOGLE_WRITABLE:
        record = by_metric(metric)
        data_type, body = build_data_point(record)
        assert body["name"] == f"users/me/dataTypes/{data_type}/dataPoints/{data_point_id(record)}"


# -- dedupe filters ---------------------------------------------------------------------------


def test_filters_follow_the_documented_aip160_forms() -> None:
    weight = by_metric(Metric.WEIGHT)
    assert _dedupe_filter(weight, "weight") == (
        'weight.sample_time.physical_time >= "2024-05-01T09:45:00Z" '
        'AND weight.sample_time.physical_time < "2024-05-01T10:15:00Z"'
    )
    assert _dedupe_filter(by_metric(Metric.BODY_FAT), "body-fat").startswith(
        "body_fat.sample_time.physical_time >="
    )
    assert _dedupe_filter(by_metric(Metric.SLEEP), "sleep") == (
        'sleep.interval.end_time >= "2024-05-01T10:00:00Z" '
        'AND sleep.interval.end_time < "2024-05-02T18:00:00Z"'
    )
    assert _dedupe_filter(by_metric(Metric.EXERCISE), "exercise") == (
        'exercise.interval.civil_start_time >= "2024-04-30" '
        'AND exercise.interval.civil_start_time < "2024-05-03"'
    )


def test_payload_key_converts_kebab_to_camel() -> None:
    assert [_payload_key(t) for t in ("sleep", "body-fat", "hydration-log")] == [
        "sleep",
        "bodyFat",
        "hydrationLog",
    ]


def interval(start: datetime, end: datetime, offset: str = "Z") -> dict:
    return {
        "interval": {
            "startTime": start.isoformat().replace("+00:00", offset),
            "endTime": end.isoformat().replace("+00:00", offset),
        }
    }


@pytest.mark.parametrize(
    ("existing_start", "existing_end", "overlaps"),
    [
        (T0 - timedelta(minutes=10), T0 + timedelta(minutes=10), True),
        (T0 + timedelta(minutes=29), T0 + timedelta(hours=1), True),
        (T0 + timedelta(minutes=30), T0 + timedelta(hours=1), False),  # touching is not overlapping
        (T0 - timedelta(hours=1), T0, False),
    ],
)
def test_session_overlap_rules(existing_start, existing_end, overlaps) -> None:
    assert _overlaps(by_metric(Metric.EXERCISE), interval(existing_start, existing_end)) is overlaps


def test_overlap_compares_instants_not_clock_text() -> None:
    existing = {
        "interval": {
            "startTime": "2024-05-01T06:00:00-04:00",
            "endTime": "2024-05-01T06:30:00-04:00",
        }
    }
    assert _overlaps(by_metric(Metric.EXERCISE), existing)  # 06:00 at -04:00 is 10:00Z


@pytest.mark.parametrize(
    "existing",
    [
        None,
        "x",
        {},
        {"interval": {}},
        {"interval": {"startTime": "garbage", "endTime": 5}},
        {
            "interval": {"startTime": "2024-05-01T10:00:00", "endTime": "2024-05-01T11:00:00"}
        },  # no zone
    ],
)
def test_unreadable_existing_data_is_unverifiable_not_a_duplicate(existing) -> None:
    """Regression: unsure matches used to be recorded as duplicates, silently dropping data."""
    with pytest.raises(_Unverifiable):
        _overlaps(by_metric(Metric.EXERCISE), existing)


def test_weight_overlap_uses_a_fifteen_minute_window() -> None:
    weight = by_metric(Metric.WEIGHT)
    near = {"sampleTime": {"physicalTime": "2024-05-01T10:14:00Z"}}
    far = {"sampleTime": {"physicalTime": "2024-05-01T10:20:00Z"}}
    assert _overlaps(weight, near) and not _overlaps(weight, far)


# -- sink behaviour ---------------------------------------------------------------------------


class Api:
    """Routes calls to the sink's endpoints; tracks creates; scripted per test."""

    def __init__(self, existing: dict[str, list[dict]] | None = None) -> None:
        self.existing = existing or {}
        self.created: list[tuple[str, dict]] = []
        self.http = FakeHttp(handler=self.handle)
        self.errors: list[FakeResponse] = []

    def handle(self, method: str, url: str, kwargs: dict) -> FakeResponse:
        if self.errors:
            return self.errors.pop(0)
        data_type = url.split("/dataTypes/")[1].split("/")[0]
        if method == "GET":
            return FakeResponse(200, {"dataPoints": self.existing.get(data_type, [])})
        self.created.append((data_type, kwargs["json"]))
        return FakeResponse(200, {"name": f"operations/{len(self.created)}", "done": True})


def make_sink(
    api: Api,
    *,
    refresh_http: FakeHttp | None = None,
    waits: list[float] | None = None,
    max_retries: int = 3,
    **kwargs,
) -> GoogleHealthSink:
    """A sink wired to ``api``; ``refresh_http`` serves refreshes, ``waits`` records sleeps."""
    auth = ga.GoogleAuth(
        ga.OAuthClient("id", "secret"),
        ga.MemoryTokenStore(ga.GoogleToken("tok", "r", 9e9, " ".join(ga.SCOPES))),
        refresh_http or FakeHttp(),
        clock=lambda: 0,
    )
    sleep = waits.append if waits is not None else (lambda _s: None)
    client = GoogleClient(auth, api.http, max_retries=max_retries, sleep=sleep)
    return GoogleHealthSink(client, **kwargs)


def run_push(sink: GoogleHealthSink, records: list[Record], **kwargs) -> list[Outcome]:
    outcomes: list[Outcome] = []
    sink.push(records, on_outcome=outcomes.append, **{"apply": True, **kwargs})
    return outcomes


def test_apply_creates_a_named_data_point_with_bearer_auth_and_no_redirects() -> None:
    api = Api()
    record = by_metric(Metric.EXERCISE)
    (outcome,) = run_push(make_sink(api), [record])
    data_type, body = api.created[0]
    assert outcome.status is Status.PUSHED and outcome.remote_ref == body["name"]
    assert data_type == "exercise" and body["name"].endswith(data_point_id(record))
    for _method, url, kwargs in api.http.calls:
        assert url.startswith(BASE_URL)
        assert kwargs["headers"] == {"Authorization": "Bearer tok"}
        assert kwargs["allow_redirects"] is False  # the bearer token must never follow a redirect
    assert api.http.calls[-1][:2] == (
        "POST",
        f"{BASE_URL}/v4/users/me/dataTypes/exercise/dataPoints",
    )


def test_dry_run_checks_for_duplicates_but_never_creates() -> None:
    api = Api()
    (outcome,) = run_push(make_sink(api), [by_metric(Metric.WEIGHT)], apply=False)
    assert outcome.status is Status.WOULD_PUSH
    assert api.created == [] and [c[0] for c in api.http.calls] == ["GET"]


def test_overlapping_existing_data_is_skipped_not_duplicated() -> None:
    existing = {
        "exercise": [
            {
                "name": "users/me/dataTypes/exercise/dataPoints/abc",
                "exercise": interval(T0 + timedelta(minutes=5), T0 + timedelta(minutes=40)),
            }
        ]
    }
    api = Api(existing)
    (outcome,) = run_push(make_sink(api), [by_metric(Metric.EXERCISE)])
    assert outcome.status is Status.DUPLICATE_EXISTING and outcome.remote_ref.endswith("/abc")
    assert api.created == []


def test_non_overlapping_existing_data_does_not_block_the_write() -> None:
    existing = {
        "exercise": [{"exercise": interval(T0 + timedelta(hours=5), T0 + timedelta(hours=6))}]
    }
    (outcome,) = run_push(make_sink(Api(existing)), [by_metric(Metric.EXERCISE)])
    assert outcome.status is Status.PUSHED


def test_hydration_is_skipped_only_when_hydration_overlaps_that_same_day() -> None:
    day_start = T0 - timedelta(hours=6)
    record = make_record(
        Metric.HYDRATION, "h", day_start, day_start + timedelta(days=1), {"milliliters": 500.0}
    )
    same_day = {"hydration-log": [{"hydrationLog": interval(T0, T0 + timedelta(minutes=1))}]}
    assert run_push(make_sink(Api(same_day)), [record])[0].status is Status.DUPLICATE_EXISTING
    """Regression: any hydration logged near the day used to count, even from another day."""
    next_day = {
        "hydration-log": [
            {
                "hydrationLog": interval(
                    day_start + timedelta(days=1), day_start + timedelta(days=1, minutes=1)
                )
            }
        ]
    }
    assert run_push(make_sink(Api(next_day)), [record])[0].status is Status.PUSHED


def test_duplicate_check_follows_pagination() -> None:
    pages = [
        FakeResponse(
            200,
            {
                "dataPoints": [
                    {"exercise": interval(T0 + timedelta(hours=9), T0 + timedelta(hours=10))}
                ],
                "nextPageToken": "p2",
            },
        ),
        FakeResponse(
            200,
            {
                "dataPoints": [
                    {"name": "found", "exercise": interval(T0, T0 + timedelta(minutes=30))}
                ]
            },
        ),
    ]
    api = Api()
    api.http = FakeHttp(queue=pages)
    assert run_push(make_sink(api), [by_metric(Metric.EXERCISE)])[0].remote_ref == "found"
    assert api.http.calls[1][2]["params"]["pageToken"] == "p2"


def test_too_many_existing_points_to_check_means_no_write() -> None:
    """Regression: the lookup used to fail open after 250 points and write anyway."""
    api = Api()
    api.http = FakeHttp(
        handler=lambda m, u, k: FakeResponse(
            200,
            {
                "dataPoints": [
                    {"exercise": interval(T0 + timedelta(days=3), T0 + timedelta(days=3, hours=1))}
                ],
                "nextPageToken": "more",
            },
        )
    )
    (outcome,) = run_push(make_sink(api), [by_metric(Metric.EXERCISE)])
    assert outcome.status is Status.FAILED and "unverifiable" in outcome.detail
    assert all(call[0] == "GET" for call in api.http.calls)  # nothing was created


def test_an_unreadable_existing_point_blocks_the_write() -> None:
    api = Api({"exercise": [{"exercise": "???"}]})
    (outcome,) = run_push(make_sink(api), [by_metric(Metric.EXERCISE)])
    assert outcome.status is Status.FAILED and api.created == []


def test_a_failed_duplicate_lookup_blocks_the_write_but_not_other_records() -> None:
    api = Api()
    api.errors = [FakeResponse(400, {"error": {"message": "bad filter"}})]
    outcomes = run_push(make_sink(api), [by_metric(Metric.WEIGHT), by_metric(Metric.BODY_FAT)])
    assert [o.status for o in outcomes] == [Status.FAILED, Status.PUSHED]
    assert [t for t, _ in api.created] == ["body-fat"]


def test_duplicate_check_can_be_disabled_for_internal_callers() -> None:
    api = Api({"weight": [{"weight": {"sampleTime": {"physicalTime": "2024-05-01T10:00:00Z"}}}]})
    sink = make_sink(api, check_duplicates=False)
    assert run_push(sink, [by_metric(Metric.WEIGHT)])[0].status is Status.PUSHED
    assert all(c[0] == "POST" for c in api.http.calls)


def test_unwritable_and_unbuildable_records_are_unsupported_not_errors() -> None:
    steps = by_metric(Metric.STEPS)
    empty_weight = make_record(Metric.WEIGHT, values={})
    outcomes = run_push(make_sink(Api()), [steps, empty_weight])
    assert [o.status for o in outcomes] == [Status.UNSUPPORTED] * 2


def conflict(status: str) -> FakeResponse:
    return FakeResponse(409, {"error": {"message": "conflict", "status": status}})


def push_with_create_reply(reply: FakeResponse) -> Outcome:
    api = Api()
    api.http = FakeHttp(queue=[FakeResponse(200, {"dataPoints": []}), reply])
    return run_push(make_sink(api), [by_metric(Metric.WEIGHT)])[0]


def test_creating_something_that_already_exists_is_reported_as_a_duplicate() -> None:
    """Idempotency: the same client-chosen id again is answered with 409, not stored twice."""
    outcome = push_with_create_reply(conflict("ALREADY_EXISTS"))
    assert outcome.status is Status.DUPLICATE_EXISTING and outcome.remote_ref


@pytest.mark.parametrize("reason", ["ABORTED", "", "FAILED_PRECONDITION"])
def test_other_conflicts_are_failures_not_duplicates(reason: str) -> None:
    """Regression: every 409 counted as 'already stored', so contention silently lost data."""
    outcome = push_with_create_reply(conflict(reason))
    assert outcome.status is Status.FAILED


def test_operation_results_are_interpreted() -> None:
    def push_with(reply: dict) -> Outcome:
        api = Api()
        api.http = FakeHttp(queue=[FakeResponse(200, {"dataPoints": []}), FakeResponse(200, reply)])
        return run_push(make_sink(api), [by_metric(Metric.WEIGHT)])[0]

    assert push_with({"name": "operations/1", "done": True}).status is Status.PUSHED
    pending = push_with({"name": "operations/1", "done": False})
    assert pending.status is Status.PENDING and "processing" in pending.detail
    assert pending.remote_ref is None  # not recorded as stored: it cannot be polled
    assert (
        push_with({"error": {"code": 6, "message": "exists"}}).status is Status.DUPLICATE_EXISTING
    )
    failed = push_with({"error": {"code": 3, "message": "bad\x1b[31m value"}})
    assert failed.status is Status.FAILED and "\x1b" not in failed.detail


# -- write limit, ordering and outcomes -------------------------------------------------------


def test_the_write_limit_counts_writes_not_duplicates() -> None:
    existing = {"weight": [{"weight": {"sampleTime": {"physicalTime": "2024-05-01T10:00:00Z"}}}]}
    api = Api(existing)
    first = by_metric(Metric.WEIGHT)  # a duplicate: does not use the budget
    others = [
        make_record(Metric.WEIGHT, f"w{i}", T0 + timedelta(hours=i), values={"grams": 70000.0 + i})
        for i in (1, 2, 3)
    ]
    outcomes = run_push(make_sink(api), [first, *others], max_writes=2)
    assert [o.status for o in outcomes] == [
        Status.DUPLICATE_EXISTING,
        Status.PUSHED,
        Status.PUSHED,
        Status.SKIPPED,
    ]
    assert len(api.created) == 2


def test_a_dry_run_applies_the_same_limit() -> None:
    records = [make_record(external_id=f"w{i}", start=T0 + timedelta(hours=i)) for i in range(3)]
    outcomes = run_push(make_sink(Api()), records, apply=False, max_writes=1)
    assert [o.status for o in outcomes] == [Status.WOULD_PUSH, Status.SKIPPED, Status.SKIPPED]


def test_every_record_gets_exactly_one_outcome_in_order() -> None:
    records = [
        by_metric(Metric.STEPS),
        by_metric(Metric.WEIGHT),
        make_record(Metric.WEIGHT, values={}),
        by_metric(Metric.BODY_FAT),
    ]
    outcomes = run_push(make_sink(Api()), records)
    assert [o.record for o in outcomes] == records


def test_outcomes_arrive_one_at_a_time_so_callers_can_checkpoint() -> None:
    api = Api()
    seen_creates: list[int] = []
    sink = make_sink(api)
    records = [make_record(external_id=f"w{i}", start=T0 + timedelta(hours=i)) for i in range(3)]
    sink.push(records, apply=True, on_outcome=lambda _o: seen_creates.append(len(api.created)))
    assert seen_creates == [1, 2, 3]


# -- permission and failure handling ----------------------------------------------------------


def test_a_permission_error_blocks_only_that_data_type() -> None:
    """Regression: one 403 used to abort the whole batch, even types the token may write."""
    api = Api()
    api.errors = [FakeResponse(403, {"error": {"message": "API not enabled"}})]
    outcomes = run_push(
        make_sink(api),
        [by_metric(Metric.WEIGHT), by_metric(Metric.WEIGHT), by_metric(Metric.BODY_FAT)],
    )
    statuses = [o.status for o in outcomes]
    assert statuses == [Status.FAILED, Status.SKIPPED, Status.PUSHED]
    assert "test user" in outcomes[0].detail
    assert [t for t, _ in api.created] == ["body-fat"]


def test_permission_errors_on_several_types_stop_the_run() -> None:
    api = Api()
    api.errors = [FakeResponse(403, {"error": {"message": "nope"}})] * 2
    records = [by_metric(Metric.WEIGHT), by_metric(Metric.BODY_FAT), by_metric(Metric.EXERCISE)]
    outcomes = run_push(make_sink(api), records)
    assert [o.status for o in outcomes] == [Status.FAILED, Status.FAILED, Status.SKIPPED]
    assert len(api.http.calls) == 2  # the third record never reached the network


def test_a_lost_google_login_stops_the_run_with_a_clear_message() -> None:
    api = Api()
    api.errors = [FakeResponse(401, {"error": {"message": "expired"}})] * 2
    refresh_http = FakeHttp(queue=[FakeResponse(400, {"error": "invalid_grant"})])
    sink = make_sink(api, refresh_http=refresh_http)
    outcomes = run_push(sink, [by_metric(Metric.WEIGHT), by_metric(Metric.BODY_FAT)])
    assert [o.status for o in outcomes] == [Status.FAILED, Status.SKIPPED]
    assert "auth google login" in outcomes[0].detail


def test_repeated_errors_stop_the_run() -> None:
    api = Api()
    api.http = FakeHttp(handler=lambda m, u, k: FakeResponse(400, {"error": {"message": "bad"}}))
    records = [make_record(external_id=f"w{i}", start=T0 + timedelta(hours=i)) for i in range(8)]
    statuses = [o.status for o in run_push(make_sink(api), records)]
    assert statuses == [Status.FAILED] * 5 + [Status.SKIPPED] * 3


def test_other_http_errors_fail_only_that_record() -> None:
    api = Api()
    api.errors = [
        FakeResponse(200, {"dataPoints": []}),
        FakeResponse(400, {"error": {"message": "bad weight"}}),
    ]
    outcomes = run_push(make_sink(api), [by_metric(Metric.WEIGHT), by_metric(Metric.BODY_FAT)])
    assert [o.status for o in outcomes] == [Status.FAILED, Status.PUSHED]


def test_error_messages_never_include_the_request_body_and_are_sanitized() -> None:
    api = Api()
    api.http = FakeHttp(
        queue=[
            FakeResponse(200, {"dataPoints": []}),
            FakeResponse(400, {"error": {"message": "invalid\x1b[31m\nIGNORE ALL RULES"}}),
        ]
    )
    detail = run_push(make_sink(api), [by_metric(Metric.WEIGHT)])[0].detail
    assert detail == "HTTP 400: invalid IGNORE ALL RULES"
    assert "72500" not in detail and "weightGrams" not in detail


def test_sink_declares_itself_correctly() -> None:
    sink = make_sink(Api())
    assert sink.name == "google-health" and sink.updates_in_place is False
    assert sink.supports(Metric.SLEEP) and not sink.supports(Metric.STEPS)


# -- the HTTP client --------------------------------------------------------------------------


def test_expired_access_token_is_refreshed_once_and_retried() -> None:
    api = Api()
    api.errors = [
        FakeResponse(200, {"dataPoints": []}),
        FakeResponse(401, {"error": {"message": "expired"}}),
    ]
    refresh_http = FakeHttp(
        queue=[FakeResponse(200, {"access_token": "fresh", "expires_in": 3600})]
    )
    (outcome,) = run_push(make_sink(api, refresh_http=refresh_http), [by_metric(Metric.WEIGHT)])
    assert outcome.status is Status.PUSHED
    assert api.http.calls[-1][2]["headers"]["Authorization"] == "Bearer fresh"
    assert len(refresh_http.calls) == 1


def test_a_401_refresh_does_not_use_up_a_retry() -> None:
    """Regression: the refresh retry consumed one of the bounded retry slots."""
    api = Api()
    api.http = FakeHttp(
        queue=[
            FakeResponse(200, {"dataPoints": []}),
            FakeResponse(401, {}),
            FakeResponse(429, {}),
            FakeResponse(200, {"name": "operations/1", "done": True}),
        ]
    )
    refresh_http = FakeHttp(
        queue=[FakeResponse(200, {"access_token": "fresh", "expires_in": 3600})]
    )
    sink = make_sink(api, refresh_http=refresh_http, max_retries=1)
    assert run_push(sink, [by_metric(Metric.WEIGHT)])[0].status is Status.PUSHED


def test_persistent_401_fails_the_record() -> None:
    api = Api()
    api.errors = [FakeResponse(200, {"dataPoints": []})] + [
        FakeResponse(401, {"error": {"message": "nope"}})
    ] * 2
    refresh_http = FakeHttp(
        queue=[FakeResponse(200, {"access_token": "fresh", "expires_in": 3600})]
    )
    (outcome,) = run_push(make_sink(api, refresh_http=refresh_http), [by_metric(Metric.WEIGHT)])
    assert outcome.status is Status.FAILED and "401" in outcome.detail


def test_reads_retry_on_429_and_5xx_and_creates_retry_only_on_429() -> None:
    waits: list[float] = []
    api = Api()
    api.http = FakeHttp(
        queue=[
            FakeResponse(503, {}),  # the duplicate lookup is a read: retried
            FakeResponse(200, {"dataPoints": []}),
            FakeResponse(429, {}, headers={"Retry-After": "7"}),  # create: "not processed"
            FakeResponse(200, {"name": "operations/9", "done": True}),
        ]
    )
    outcome = run_push(make_sink(api, waits=waits), [by_metric(Metric.WEIGHT)])[0]
    assert outcome.status is Status.PUSHED and waits == [1.0, 7.0]


def failing_create_handler(api: Api, failure: Exception | FakeResponse):
    """Lookups succeed with nothing found; every create attempt is recorded, then fails."""

    def handler(method: str, url: str, kwargs: dict) -> FakeResponse:
        if method == "GET":
            return FakeResponse(200, {"dataPoints": []})
        api.created.append(("weight", kwargs["json"]))
        if isinstance(failure, Exception):
            raise failure
        return failure

    return handler


def test_a_create_that_may_have_landed_is_not_blindly_repeated() -> None:
    """After a 5xx or a timeout we cannot know the create did not happen; the next run's duplicate
    check settles it, so repeating it here could store the record twice."""
    for failure in (
        FakeResponse(503, {"error": {"message": "unavailable"}}),
        ConnectionError("reset"),
    ):
        api = Api()
        api.http = FakeHttp(handler=failing_create_handler(api, failure))
        (outcome,) = run_push(make_sink(api), [by_metric(Metric.WEIGHT)])
        assert outcome.status is Status.FAILED
        assert len(api.created) == 1  # exactly one attempt


def test_retry_after_is_capped_and_garbage_falls_back_to_backoff() -> None:
    waits: list[float] = []
    api = Api()
    api.http = FakeHttp(
        queue=[
            FakeResponse(200, {"dataPoints": []}),
            FakeResponse(429, {}, headers={"Retry-After": "86400"}),
            FakeResponse(429, {}, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}),
            FakeResponse(200, {"name": "operations/9", "done": True}),
        ]
    )
    run_push(make_sink(api, waits=waits), [by_metric(Metric.WEIGHT)])
    assert waits == [60.0, 2.0]


def test_network_failures_are_retried_then_reported_without_details() -> None:
    attempts = {"n": 0}

    def flaky(method: str, url: str, kwargs: dict) -> FakeResponse:
        attempts["n"] += 1
        raise ConnectionError("https://secret-proxy.internal refused")

    api = Api()
    api.http = FakeHttp(handler=flaky)
    (outcome,) = run_push(make_sink(api, max_retries=2), [by_metric(Metric.WEIGHT)])
    assert outcome.status is Status.FAILED and attempts["n"] == 3
    assert "secret-proxy" not in outcome.detail and "ConnectionError" in outcome.detail


def test_retries_are_bounded() -> None:
    api = Api()
    api.http = FakeHttp(queue=[FakeResponse(500, {"error": {"message": "boom"}})] * 4)
    outcome = run_push(make_sink(api, max_retries=3), [by_metric(Metric.WEIGHT)])[0]
    assert outcome.status is Status.FAILED and "500" in outcome.detail and "boom" in outcome.detail
    assert len(api.http.calls) == 4  # the first try and three retries


def test_a_non_json_response_is_a_clean_error() -> None:
    class Html(FakeResponse):
        def json(self):
            raise ValueError("not json")

    api = Api()
    api.http = FakeHttp(queue=[Html(200, {"x": 1})])
    (outcome,) = run_push(make_sink(api), [by_metric(Metric.WEIGHT)])
    assert outcome.status is Status.FAILED and "not JSON" in outcome.detail


def test_identity_is_a_plain_authenticated_read() -> None:
    api = Api()
    api.http = FakeHttp(queue=[FakeResponse(200, {"name": "users/me/identity"})])
    auth = ga.GoogleAuth(
        ga.OAuthClient("id", "s"),
        ga.MemoryTokenStore(ga.GoogleToken("tok", "r", 9e9, "")),
        FakeHttp(),
    )
    assert GoogleClient(auth, api.http).identity() == {"name": "users/me/identity"}
    assert api.http.calls[0][:2] == ("GET", f"{BASE_URL}/v4/users/me/identity")


def test_error_classes_carry_their_status() -> None:
    assert GooglePermissionError(403, "x").status == 403
    assert isinstance(GooglePermissionError(403, "x"), GoogleApiError)


# -- login scopes -----------------------------------------------------------------------------


def sink_with_scope(api: Api, scope: str) -> GoogleHealthSink:
    auth = ga.GoogleAuth(
        ga.OAuthClient("id", "secret"),
        ga.MemoryTokenStore(ga.GoogleToken("tok", "r", 9e9, scope)),
        FakeHttp(),
        clock=lambda: 0,
    )
    return GoogleHealthSink(GoogleClient(auth, api.http, sleep=lambda _s: None))


@pytest.mark.parametrize("apply", [True, False])
def test_a_login_without_the_read_scopes_writes_nothing(apply: bool) -> None:
    """Regression: write-only scopes hide data Garmin Connect already sent, so it was duplicated."""
    api = Api()
    write_only = " ".join(s for s in ga.SCOPES if s.endswith(".writeonly"))
    records = [by_metric(Metric.WEIGHT), by_metric(Metric.BODY_FAT), by_metric(Metric.SLEEP)]
    outcomes = run_push(sink_with_scope(api, write_only), records, apply=apply)
    assert [o.status for o in outcomes] == [Status.FAILED, Status.SKIPPED, Status.SKIPPED]
    assert "lacks permissions" in outcomes[0].detail and "sleep.readonly" in outcomes[0].detail
    assert api.http.calls == [] and api.created == []


def test_an_unknown_scope_list_counts_as_missing() -> None:
    outcomes = run_push(sink_with_scope(Api(), ""), [by_metric(Metric.WEIGHT)])
    assert outcomes[0].status is Status.FAILED


def test_the_nutrition_scopes_are_not_needed_to_write() -> None:
    no_nutrition = " ".join(s for s in ga.SCOPES if "nutrition" not in s)
    assert (
        run_push(sink_with_scope(Api(), no_nutrition), [by_metric(Metric.WEIGHT)])[0].status
        is Status.PUSHED
    )


# -- responses that are not what they claim ---------------------------------------------------


def test_a_redirect_or_wrong_status_is_a_failure_not_success() -> None:
    """Regression: a 302 on the duplicate lookup read as 'no data', and on create as 'pushed'."""
    for reply in (FakeResponse(204, None), FakeResponse(301, {}), FakeResponse(302, None)):
        api = Api()
        api.http = FakeHttp(queue=[reply])
        (outcome,) = run_push(make_sink(api), [by_metric(Metric.WEIGHT)])
        assert outcome.status is Status.FAILED and api.created == []
    create = push_with_create_reply(FakeResponse(302, {}))
    assert create.status is Status.FAILED


@pytest.mark.parametrize("body", [None, [], "text", 5])
def test_an_empty_or_non_object_body_is_a_failure(body) -> None:
    class Raw(FakeResponse):
        @property
        def content(self) -> bytes:
            return b"" if self.body is None else b"x"

        def json(self):
            return self.body

    api = Api()
    api.http = FakeHttp(queue=[Raw(200, body)])
    (outcome,) = run_push(make_sink(api), [by_metric(Metric.WEIGHT)])
    assert outcome.status is Status.FAILED and api.created == []


def test_an_existing_data_list_of_the_wrong_type_blocks_the_write() -> None:
    api = Api()
    api.http = FakeHttp(queue=[FakeResponse(200, {"dataPoints": {"surprise": 1}})])
    (outcome,) = run_push(make_sink(api), [by_metric(Metric.WEIGHT)])
    assert outcome.status is Status.FAILED and "unverifiable" in outcome.detail


def test_an_empty_object_means_no_existing_data() -> None:
    """Google omits empty lists from JSON, so `{}` is the normal answer for 'nothing there'."""
    api = Api()
    api.http = FakeHttp(
        queue=[FakeResponse(200, {}), FakeResponse(200, {"name": "operations/1", "done": True})]
    )
    assert run_push(make_sink(api), [by_metric(Metric.WEIGHT)])[0].status is Status.PUSHED


@pytest.mark.parametrize(
    "path",
    [
        "/v3/x",
        "v4/x",
        "/v4/x@evil.example",
        "/v4//x",
        "/v4/../x",
        "/v4/x\\y",
        "/v4/x?y=1",
        "/v4/x y",
    ],
)
def test_the_client_refuses_paths_that_could_redirect_the_token(path: str) -> None:
    """A path that began with `@` or `.` would turn the base URL's host into userinfo."""
    api = Api()
    client = GoogleClient(
        ga.GoogleAuth(
            ga.OAuthClient("id", "s"),
            ga.MemoryTokenStore(ga.GoogleToken("tok", "r", 9e9, "")),
            FakeHttp(),
        ),
        api.http,
    )
    with pytest.raises(ValueError, match="unsafe"):
        client.get(path)
    assert api.http.calls == []


def test_a_numeric_hazard_in_a_record_is_unbuildable_not_a_crash() -> None:
    record = rich_exercise()
    record.values["distance_m"] = 1e308
    with pytest.raises(Unbuildable):
        build_data_point(record)


# -- second review round ----------------------------------------------------------------------


def test_the_write_cap_counts_every_create_that_may_have_landed() -> None:
    """Regression: creates answered with a 5xx never counted, so --limit 1 could write several."""
    api = Api()
    api.http = FakeHttp(handler=failing_create_handler(api, FakeResponse(503, {})))
    records = [make_record(external_id=f"w{i}", start=T0 + timedelta(hours=i)) for i in range(4)]
    outcomes = run_push(make_sink(api), records, max_writes=1)
    assert len(api.created) == 1  # one attempt, not four
    assert [o.status for o in outcomes] == [
        Status.FAILED,
        Status.SKIPPED,
        Status.SKIPPED,
        Status.SKIPPED,
    ]


def test_a_pending_create_uses_up_the_write_cap() -> None:
    api = Api()
    api.http = FakeHttp(
        handler=lambda m, u, k: (
            FakeResponse(200, {"dataPoints": []})
            if m == "GET"
            else FakeResponse(200, {"name": "operations/1"})
        )  # `done` left out: still running
    )
    records = [make_record(external_id=f"w{i}", start=T0 + timedelta(hours=i)) for i in range(3)]
    outcomes = run_push(make_sink(api), records, max_writes=1)
    assert [o.status for o in outcomes] == [Status.PENDING, Status.SKIPPED, Status.SKIPPED]


def test_a_lookup_that_answers_with_an_error_body_blocks_the_write() -> None:
    api = Api()
    api.http = FakeHttp(
        queue=[FakeResponse(200, {"error": {"code": 14, "message": "unavailable"}})]
    )
    (outcome,) = run_push(make_sink(api), [by_metric(Metric.WEIGHT)])
    assert (
        outcome.status is Status.FAILED and "unverifiable" in outcome.detail and api.created == []
    )


@pytest.mark.parametrize(
    ("reply", "status"),
    [
        ({"error": "quota exceeded"}, Status.FAILED),  # a string where an object belongs
        ({"error": {"code": 8, "message": "quota"}}, Status.FAILED),
        ({"done": "false"}, Status.FAILED),  # not a boolean: do not guess
        ({"done": True}, Status.PUSHED),
        ({"done": False}, Status.PENDING),
        ({"name": "operations/1"}, Status.PENDING),  # protobuf JSON omits `done` while it is false
    ],
)
def test_create_replies_are_interpreted_strictly(reply: dict, status: Status) -> None:
    assert push_with_create_reply(FakeResponse(200, reply)).status is status


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "", "soon"])
def test_a_retry_after_that_is_not_a_finite_number_falls_back_to_backoff(value: str) -> None:
    """Regression: `Retry-After: nan` made time.sleep raise out of the client."""
    waits: list[float] = []
    api = Api()
    api.http = FakeHttp(
        queue=[
            FakeResponse(200, {"dataPoints": []}),
            FakeResponse(429, {}, headers={"Retry-After": value}),
            FakeResponse(200, {"name": "operations/1", "done": True}),
        ]
    )
    outcome = run_push(make_sink(api, waits=waits), [by_metric(Metric.WEIGHT)])[0]
    assert outcome.status is Status.PUSHED and waits == [1.0]


def test_the_end_offset_reaches_the_google_body() -> None:
    record = make_record(Metric.HYDRATION, "h", T0, T0 + timedelta(hours=25), {"milliliters": 1.0})
    changed = Record(
        record.metric, record.external_id, record.start, record.end, EDT, record.values,
        end_utc_offset_s=EDT - 3600,
    )  # fmt: skip
    interval = build_data_point(changed)[1]["hydrationLog"]["interval"]
    assert (interval["startUtcOffset"], interval["endUtcOffset"]) == ("-14400s", "-18000s")
