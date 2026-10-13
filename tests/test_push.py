"""Push orchestration (ledger, finality, dry run) and the Health Connect exporter."""

from __future__ import annotations

import json
import stat
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from garmin_sync.archive import Archive
from garmin_sync.coverage import HEALTH_CONNECT_METRICS, TARGETS
from garmin_sync.models import Metric, Record
from garmin_sync.push import push_records
from garmin_sync.sinks.base import Outcome, OutcomeCallback, Status
from garmin_sync.sinks.health_connect import (
    _DATA_BUILDERS,
    SCHEMA,
    HealthConnectExporter,
    to_health_connect,
)
from tests.conftest import NOW, make_record
from tests.helpers import EDT

OLD = datetime(2024, 5, 1, 11, 30, tzinfo=UTC)


class RecordingSink:
    def __init__(
        self, *, updates_in_place: bool = False, supported: set[Metric] | None = None
    ) -> None:
        self.name = "recording"
        self.updates_in_place = updates_in_place
        self._supported = supported
        self.received: list[Record] = []
        self.max_writes_seen: int | None = None
        self.status = Status.PUSHED
        self.detail: str | None = None
        self.crash_after: int | None = None
        self.silent_for: set[str] = set()

    def supports(self, metric: Metric) -> bool:
        return self._supported is None or metric in self._supported

    def push(
        self,
        records: Sequence[Record],
        *,
        apply: bool,
        on_outcome: OutcomeCallback,
        max_writes: int | None = None,
    ) -> None:
        self.max_writes_seen = max_writes
        self.received.extend(records)
        status = self.status if apply else Status.WOULD_PUSH
        for index, record in enumerate(records):
            if self.crash_after is not None and index >= self.crash_after:
                raise RuntimeError("network died")
            if record.external_id not in self.silent_for:
                on_outcome(Outcome(record, status, self.detail, "ref"))


def weight(n: int = 1, grams: float = 72500.0, start: datetime = OLD, **kwargs) -> Record:
    return make_record(
        external_id=f"garmin:weight:{n}", values={"grams": grams}, start=start, **kwargs
    )


# -- orchestration ----------------------------------------------------------------------------


def test_dry_run_sends_candidates_but_never_writes_the_ledger(archive: Archive) -> None:
    sink = RecordingSink()
    report = push_records(sink, archive, [weight()], apply=False, now=NOW)
    assert report.outcomes["would_push"] == 1
    assert archive.ledger_get("recording", "garmin:weight:1") is None


def test_apply_records_the_ledger_and_the_next_run_is_a_no_op(archive: Archive) -> None:
    sink = RecordingSink()
    push_records(sink, archive, [weight()], apply=True, now=NOW)
    again = RecordingSink()
    report = push_records(again, archive, [weight()], apply=True, now=NOW)
    assert again.received == [] and report.outcomes["already_synced"] == 1


def test_sinks_without_in_place_updates_never_resend_a_changed_record(archive: Archive) -> None:
    push_records(RecordingSink(), archive, [weight(grams=72500)], apply=True, now=NOW)
    sink = RecordingSink(updates_in_place=False)
    push_records(sink, archive, [weight(grams=71000)], apply=True, now=NOW)
    assert sink.received == []


def test_sinks_with_in_place_updates_resend_when_content_changes(archive: Archive) -> None:
    push_records(
        RecordingSink(updates_in_place=True), archive, [weight(grams=72500)], apply=True, now=NOW
    )
    sink = RecordingSink(updates_in_place=True)
    push_records(sink, archive, [weight(grams=72500)], apply=True, now=NOW)
    assert sink.received == []  # unchanged
    push_records(sink, archive, [weight(grams=71000)], apply=True, now=NOW)
    assert [r.values["grams"] for r in sink.received] == [71000]


def test_records_still_being_assembled_are_deferred(archive: Archive) -> None:
    sink = RecordingSink()
    just_now = NOW - timedelta(minutes=30)  # a weigh-in half an hour ago
    report = push_records(sink, archive, [weight(start=just_now)], apply=True, now=NOW)
    assert sink.received == [] and report.outcomes["deferred_not_final"] == 1


def test_records_from_an_archive_pulled_too_early_are_held_back(archive: Archive) -> None:
    """Regression: a day pulled mid-day was pushed, irrevocably, the next morning."""
    day_start = datetime(2024, 5, 9, 4, tzinfo=UTC)  # a local day that is over by NOW
    partial = make_record(
        Metric.HYDRATION,
        "garmin:hydration:2024-05-09",
        day_start,
        day_start + timedelta(days=1),
        {"milliliters": 300.0},
        fetched_at=day_start + timedelta(hours=14),
    )
    sink = RecordingSink()
    report = push_records(sink, archive, [partial], apply=True, now=NOW)
    assert sink.received == [] and report.outcomes["deferred_not_final"] == 1


def test_records_without_provenance_are_never_pushed(archive: Archive) -> None:
    sink = RecordingSink()
    push_records(sink, archive, [weight(fetched_at=None)], apply=True, now=NOW)
    assert sink.received == []


def test_unsupported_metrics_are_counted_and_not_sent(archive: Archive) -> None:
    sink = RecordingSink(supported={Metric.SLEEP})
    report = push_records(sink, archive, [weight()], apply=True, now=NOW)
    assert sink.received == [] and report.by_metric["weight"]["unsupported_metric"] == 1


def test_metrics_filter_limit_and_oldest_first_order(archive: Archive) -> None:
    sink = RecordingSink()
    records = [weight(i, start=OLD + timedelta(hours=5 - i)) for i in range(5)] + [
        make_record(Metric.BODY_FAT, "garmin:body_fat:1", values={"percent": 18})
    ]
    push_records(sink, archive, records, apply=True, now=NOW, metrics={Metric.WEIGHT}, limit=2)
    assert [r.external_id for r in sink.received] == [f"garmin:weight:{i}" for i in (4, 3, 2, 1, 0)]
    assert (
        sink.max_writes_seen == 2
    )  # the sink enforces the cap, because only it knows what is a write


def test_failures_are_reported_but_not_ledgered_and_errors_are_capped(archive: Archive) -> None:
    sink = RecordingSink()
    sink.status, sink.detail = Status.FAILED, "HTTP 400: bad"
    report = push_records(sink, archive, [weight(i) for i in range(9)], apply=True, now=NOW)
    assert report.outcomes["failed"] == 9 and len(report.errors) == 5
    assert not report.ok
    assert archive.ledger_counts() == {}
    retry = RecordingSink()
    push_records(retry, archive, [weight(0)], apply=True, now=NOW)
    assert len(retry.received) == 1  # failed records are retried next run


def test_skipped_and_unsupported_outcomes_are_not_failures(archive: Archive) -> None:
    sink = RecordingSink()
    sink.status = Status.SKIPPED
    assert push_records(sink, archive, [weight()], apply=True, now=NOW).ok


def test_error_details_are_sanitized_before_they_reach_the_report(archive: Archive) -> None:
    sink = RecordingSink()
    sink.status, sink.detail = Status.FAILED, "\x1b[31mdo this\nnow" + "x" * 1000
    report = push_records(sink, archive, [weight()], apply=True, now=NOW)
    (error,) = report.errors
    assert "\x1b" not in error and "\n" not in error and len(error) <= 300


def test_duplicates_found_at_the_destination_are_ledgered_so_they_are_not_rechecked(
    archive: Archive,
) -> None:
    sink = RecordingSink()
    sink.status = Status.DUPLICATE_EXISTING
    push_records(sink, archive, [weight()], apply=True, now=NOW)
    assert archive.ledger_get("recording", "garmin:weight:1") is not None


def test_progress_is_ledgered_one_record_at_a_time(archive: Archive) -> None:
    """Regression: the ledger was written after the whole batch, so a crash lost it all."""
    sink = RecordingSink()
    sink.crash_after = 2
    with pytest.raises(RuntimeError, match="network died"):
        push_records(sink, archive, [weight(i) for i in range(5)], apply=True, now=NOW)
    assert archive.ledger_counts() == {"recording": 2}
    rerun = RecordingSink()
    push_records(rerun, archive, [weight(i) for i in range(5)], apply=True, now=NOW)
    assert [r.external_id for r in rerun.received] == [f"garmin:weight:{i}" for i in (2, 3, 4)]


def test_a_pending_create_is_not_recorded_as_stored_and_is_not_a_failure(archive: Archive) -> None:
    """Google can report an operation still running; with no way to poll it, trust only the next
    run's duplicate check, not the ledger."""
    sink = RecordingSink()
    sink.status = Status.PENDING
    report = push_records(sink, archive, [weight()], apply=True, now=NOW)
    assert report.outcomes["pending"] == 1 and report.ok
    assert archive.ledger_get("recording", "garmin:weight:1") is None


def test_a_record_the_sink_never_answered_for_is_reported_as_failed(archive: Archive) -> None:
    sink = RecordingSink()
    sink.silent_for = {"garmin:weight:1"}
    report = push_records(sink, archive, [weight(1), weight(2)], apply=True, now=NOW)
    assert report.outcomes["failed"] == 1 and report.outcomes["pushed"] == 1
    assert archive.ledger_get("recording", "garmin:weight:1") is None


# -- Health Connect ---------------------------------------------------------------------------


def sample_records() -> list[Record]:
    """One record per metric, shaped as the normalizers produce them."""
    t0 = datetime(2024, 5, 1, 10, 0, tzinfo=UTC)
    interval_end = t0 + timedelta(minutes=30)
    stamp = int(t0.timestamp())
    samples = {
        Metric.SLEEP: (
            t0,
            t0 + timedelta(hours=8),
            {"stages": [{"start": stamp, "end": stamp + 3600, "stage": "deep"}]},
        ),
        Metric.EXERCISE: (
            t0,
            interval_end,
            {
                "activity_id": "1",
                "hc_type": "RUNNING",
                "google_type": "RUNNING",
                "name": "Run",
                "description": "Felt good",
                "active_s": 1700,
                "elapsed_s": 1800,
                "distance_m": 5000.0,
                "calories_kcal": 300.0,
                "active_calories_kcal": 260.0,
                "avg_hr": 150.0,
                "laps": [
                    {"start": stamp, "end": stamp + 900, "distance_m": 2500.0},
                    {"start": stamp + 900, "end": stamp + 1800},
                ],
            },
        ),
        Metric.STEPS: (t0, interval_end, {"count": 120}),
        Metric.DISTANCE: (t0, interval_end, {"meters": 900.0}),
        Metric.ACTIVE_CALORIES: (t0, interval_end, {"kcal": 50.0}),
        Metric.TOTAL_CALORIES: (t0, interval_end, {"kcal": 90.0}),
        Metric.BASAL_METABOLIC_RATE: (t0, interval_end, {"kcal_per_day": 1728.0}),
        Metric.FLOORS: (t0, interval_end, {"floors": 3.0}),
        Metric.HYDRATION: (t0, interval_end, {"milliliters": 500.0}),
        Metric.HEART_RATE: (t0, interval_end, {"samples": [[stamp, 60], [stamp + 60, 61]]}),
        Metric.SKIN_TEMPERATURE: (t0, t0 + timedelta(hours=8), {"delta_c": -0.4}),
        Metric.RESTING_HEART_RATE: (t0, t0, {"bpm": 52.0}),
        Metric.HRV: (t0, t0, {"rmssd_ms": 48.0}),
        Metric.SPO2: (t0, t0, {"percent": 96.0}),
        Metric.RESPIRATORY_RATE: (t0, t0, {"breaths_per_min": 14.0}),
        Metric.WEIGHT: (t0, t0, {"grams": 72500.0}),
        Metric.BODY_FAT: (t0, t0, {"percent": 18.5}),
        Metric.BONE_MASS: (t0, t0, {"grams": 3100.0}),
        Metric.BODY_WATER_MASS: (t0, t0, {"grams": 39875.0}),
        Metric.BLOOD_PRESSURE: (t0, t0, {"systolic": 118.0, "diastolic": 76.0}),
        Metric.VO2_MAX: (t0, t0, {"ml_kg_min": 51.2}),
    }
    return [
        Record(m, f"garmin:{m.value}:x", s, e, EDT, v, fetched_at=NOW)
        for m, (s, e, v) in samples.items()
    ]


def collect(exporter: HealthConnectExporter, records: Sequence[Record], **kwargs) -> list[Outcome]:
    outcomes: list[Outcome] = []
    exporter.push(records, on_outcome=outcomes.append, **kwargs)
    return outcomes


def test_the_fixture_covers_every_metric() -> None:
    assert {r.metric for r in sample_records()} == set(Metric)


def test_the_export_spec_documents_every_record_type() -> None:
    """Regression guard: the spec an importer is written from must not fall behind the exporter."""
    spec = (
        Path(__file__).resolve().parent.parent / "docs" / "health-connect-export.md"
    ).read_text()
    missing = {t.health_connect for t in TARGETS.values() if f"`{t.health_connect}`" not in spec}
    assert not missing, missing


def test_every_metric_has_a_health_connect_target_and_builder() -> None:
    assert set(TARGETS) == set(Metric) == set(_DATA_BUILDERS) == HEALTH_CONNECT_METRICS


@pytest.mark.parametrize("record", sample_records(), ids=lambda r: r.metric.value)
def test_every_record_exports_as_valid_json_with_the_documented_envelope(record: Record) -> None:
    line = to_health_connect(record, version=7)
    json.dumps(line)
    assert line["schema"] == SCHEMA
    assert line["recordType"] == TARGETS[record.metric].health_connect
    assert line["clientRecordId"] == record.external_id and line["clientRecordVersion"] == 7
    if record.is_instant:
        assert line["time"].endswith("Z") and line["zoneOffsetSeconds"] == EDT
    else:
        assert line["startTime"].endswith("Z") and line["endZoneOffsetSeconds"] == EDT
    assert line["data"]


def test_export_converts_units_and_stage_names() -> None:
    by_metric = {r.metric: to_health_connect(r, 1)["data"] for r in sample_records()}
    assert by_metric[Metric.WEIGHT] == {"weightKilograms": 72.5}
    assert by_metric[Metric.HYDRATION] == {"volumeLiters": 0.5}
    assert by_metric[Metric.SLEEP]["stages"][0]["stage"] == "STAGE_TYPE_DEEP"
    assert by_metric[Metric.HEART_RATE]["samples"][0]["beatsPerMinute"] == 60
    assert by_metric[Metric.BLOOD_PRESSURE] == {
        "systolicMillimetersOfMercury": 118.0,
        "diastolicMillimetersOfMercury": 76.0,
    }
    # 1728 kcal/day x 4184 J/kcal / 86400 s = 83.68 W
    assert by_metric[Metric.BASAL_METABOLIC_RATE] == {"basalMetabolicRateWatts": 83.68}
    assert by_metric[Metric.SKIN_TEMPERATURE] == {
        "deltaCelsius": -0.4,
        "measurementLocation": "WRIST",
    }


def test_exercise_export_carries_notes_active_energy_and_laps() -> None:
    (record,) = [r for r in sample_records() if r.metric is Metric.EXERCISE]
    data = to_health_connect(record, 1)["data"]
    assert data["exerciseType"] == "RUNNING" and data["notes"] == "Felt good"
    assert data["activeEnergyKilocalories"] == 260.0 and data["energyKilocalories"] == 300.0
    assert [lap.get("lengthMeters") for lap in data["laps"]] == [2500.0, None]


def test_exporter_writes_a_private_versioned_jsonl_file(tmp_path: Path) -> None:
    exporter = HealthConnectExporter(tmp_path / "exports", clock=lambda: NOW)
    outcomes = collect(exporter, sample_records(), apply=True)
    (path,) = list((tmp_path / "exports").iterdir())
    assert path.name == "healthconnect-20240510T150000000000Z.jsonl"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(lines) == len(outcomes) == len(Metric)
    assert {line["clientRecordVersion"] for line in lines} == {int(NOW.timestamp())}
    assert all(o.status is Status.PUSHED and o.remote_ref == path.name for o in outcomes)


def test_exporter_dry_run_and_empty_input_write_nothing(tmp_path: Path) -> None:
    exporter = HealthConnectExporter(tmp_path / "exports", clock=lambda: NOW)
    assert all(
        o.status is Status.WOULD_PUSH for o in collect(exporter, sample_records(), apply=False)
    )
    assert collect(exporter, [], apply=True) == []
    assert not (tmp_path / "exports").exists()


def test_exporter_honours_the_write_limit(tmp_path: Path) -> None:
    exporter = HealthConnectExporter(tmp_path / "exports", clock=lambda: NOW)
    outcomes = collect(exporter, sample_records(), apply=True, max_writes=3)
    statuses = [o.status for o in outcomes]
    assert statuses.count(Status.PUSHED) == 3
    assert statuses.count(Status.SKIPPED) == len(Metric) - 3
    (path,) = list((tmp_path / "exports").iterdir())
    assert len(path.read_text().splitlines()) == 3


def test_outcomes_are_reported_only_after_the_file_is_in_place(tmp_path: Path) -> None:
    exports = tmp_path / "exports"
    seen_on_disk: list[bool] = []

    def on_outcome(_outcome: Outcome) -> None:
        seen_on_disk.append(any(exports.iterdir()))

    HealthConnectExporter(exports, clock=lambda: NOW).push(
        sample_records(), apply=True, on_outcome=on_outcome
    )
    assert seen_on_disk and all(seen_on_disk)


def test_a_later_export_carries_a_higher_version_so_imports_upsert(tmp_path: Path) -> None:
    times = iter([NOW, NOW + timedelta(hours=1)])
    exporter = HealthConnectExporter(tmp_path / "exports", clock=lambda: next(times))
    collect(exporter, [weight()], apply=True)
    collect(exporter, [weight(grams=71000)], apply=True)
    versions = [
        json.loads(p.read_text())["clientRecordVersion"]
        for p in sorted((tmp_path / "exports").iterdir())
    ]
    assert versions[0] < versions[1]


def test_two_exports_in_the_same_second_do_not_overwrite_each_other(tmp_path: Path) -> None:
    """Regression: file names had one-second resolution."""
    times = iter([NOW, NOW + timedelta(microseconds=5)])
    exporter = HealthConnectExporter(tmp_path / "exports", clock=lambda: next(times))
    collect(exporter, [weight(1)], apply=True)
    collect(exporter, [weight(2)], apply=True)
    assert len(list((tmp_path / "exports").iterdir())) == 2


def test_end_to_end_exporter_through_push_records(archive: Archive, tmp_path: Path) -> None:
    exporter = HealthConnectExporter(tmp_path / "exports", clock=lambda: NOW)
    later = NOW + timedelta(days=3)
    records = [replace_fetched(r, later) for r in sample_records()]
    report = push_records(exporter, archive, records, apply=True, now=later)
    assert report.outcomes["pushed"] == len(Metric)
    rerun = push_records(exporter, archive, records, apply=True, now=later)
    assert rerun.outcomes["already_synced"] == len(Metric)
    assert len(list((tmp_path / "exports").iterdir())) == 1


def replace_fetched(record: Record, fetched_at: datetime) -> Record:
    return Record(
        record.metric,
        record.external_id,
        record.start,
        record.end,
        record.utc_offset_s,
        record.values,
        fetched_at=fetched_at,
    )
