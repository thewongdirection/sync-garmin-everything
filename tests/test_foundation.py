"""timeutil, paths, models and archive."""

from __future__ import annotations

import sqlite3
import stat
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from garmin_sync import timeutil as t
from garmin_sync.archive import Archive, RawMeta
from garmin_sync.models import EVENT_GRACE, SLEEP_GRACE, Metric, Record
from garmin_sync.paths import (
    HOME_MARKER,
    AppHome,
    UnsafeHomeError,
    ensure_private_dir,
    is_private,
    write_private_bytes,
    write_private_json,
    write_private_text,
)
from garmin_sync.sanitize import clean
from tests.conftest import NOW, make_record
from tests.helpers import EDT, ms

# -- timeutil ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "2024-05-01T03:00:00.0",
        "2024-05-01T03:00:00",
        "2024-05-01 03:00:00",
        "2024-05-01 03:00:00.0",
    ],
)
def test_parse_gmt_accepts_garmin_string_forms(value: str) -> None:
    assert t.parse_gmt(value) == datetime(2024, 5, 1, 3, 0, tzinfo=UTC)


def test_parse_gmt_accepts_epoch_milliseconds() -> None:
    assert t.parse_gmt(ms("2024-05-01T03:00:00")) == datetime(2024, 5, 1, 3, 0, tzinfo=UTC)


@pytest.mark.parametrize("value", [None, "", "not a date", True, [], {}, 1e30, float("nan")])
def test_parse_gmt_rejects_garbage(value: object) -> None:
    assert t.parse_gmt(value) is None


def test_offset_seconds_from_local_and_gmt() -> None:
    assert t.offset_seconds("2024-05-01 06:00:00", "2024-05-01 10:00:00") == EDT
    assert t.offset_seconds(ms("2024-05-01T05:30:00"), ms("2024-05-01T00:00:00")) == 5.5 * 3600


def test_offset_seconds_rounds_to_whole_minutes() -> None:
    assert t.offset_seconds("2024-05-01 06:00:25", "2024-05-01 10:00:00") == EDT + 0


def test_offset_seconds_none_when_missing() -> None:
    assert t.offset_seconds(None, "2024-05-01 10:00:00") is None


def test_chunk_days_covers_range_without_overlap() -> None:
    chunks = list(t.chunk_days(date(2024, 1, 1), date(2024, 3, 5), 30))
    assert chunks[0] == (date(2024, 1, 1), date(2024, 1, 30))
    assert chunks[-1][1] == date(2024, 3, 5)
    flat = [d for a, b in chunks for d in t.iter_days(a, b)]
    assert flat == list(t.iter_days(date(2024, 1, 1), date(2024, 3, 5)))


def test_iter_days_inclusive_and_empty() -> None:
    assert len(list(t.iter_days(date(2024, 5, 1), date(2024, 5, 3)))) == 3
    assert list(t.iter_days(date(2024, 5, 3), date(2024, 5, 1))) == []


def test_day_bounds_and_local_date_agree() -> None:
    start, end = t.day_bounds(date(2024, 5, 1), EDT)
    assert start == datetime(2024, 5, 1, 4, 0, tzinfo=UTC)
    assert end - start == timedelta(days=1)
    assert t.local_date(start, EDT) == date(2024, 5, 1)
    assert t.local_date(start - timedelta(seconds=1), EDT) == date(2024, 4, 30)


def test_iso_z_normalizes_zone() -> None:
    plus_two = datetime(2024, 5, 1, 12, 0, tzinfo=timezone(timedelta(hours=2)))
    assert t.iso_z(plus_two) == "2024-05-01T10:00:00Z"


# -- paths ------------------------------------------------------------------------------------


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_app_home_resolution_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert AppHome.resolve().root == tmp_path / "fakehome" / ".garmin-sync"
    monkeypatch.setenv("GARMIN_SYNC_HOME", str(tmp_path / "from-env"))
    assert AppHome.resolve().root == tmp_path / "from-env"
    assert AppHome.resolve(tmp_path / "explicit").root == tmp_path / "explicit"


def test_ensure_creates_owner_only_tree(tmp_path: Path) -> None:
    home = AppHome(tmp_path / "state")
    home.ensure()
    for directory in (home.root, home.garmin_token_dir, home.exports_dir, home.fit_dir):
        assert _mode(directory) == 0o700


def test_private_writes_are_0600_even_under_permissive_umask(tmp_path: Path) -> None:
    target = tmp_path / "d" / "secret.json"
    write_private_json(target, {"k": "v"})
    assert _mode(target) == 0o600
    assert _mode(target.parent) == 0o700
    assert is_private(target)


def test_private_write_replaces_atomically_and_leaves_no_temp_files(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    write_private_text(target, "one")
    write_private_text(target, "two")
    assert target.read_text() == "two"
    assert [p.name for p in tmp_path.iterdir()] == ["f.txt"]


def test_private_write_cleans_up_temp_file_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        write_private_bytes(tmp_path / "f.bin", b"x")
    assert list(tmp_path.iterdir()) == []


def test_is_private_detects_group_or_world_access(tmp_path: Path) -> None:
    f = tmp_path / "f"
    f.write_text("x")
    f.chmod(0o640)
    assert not is_private(f)
    f.chmod(0o600)
    assert is_private(f)


# -- models -----------------------------------------------------------------------------------


def test_record_requires_aware_utc_datetimes() -> None:
    naive = datetime(2024, 5, 1, 11, 30)
    with pytest.raises(ValueError, match="aware UTC"):
        Record(Metric.WEIGHT, "x", naive, naive, 0)
    eastern = datetime(2024, 5, 1, 11, 30, tzinfo=timezone(timedelta(hours=-4)))
    with pytest.raises(ValueError, match="aware UTC"):
        Record(Metric.WEIGHT, "x", eastern, eastern, 0)


def test_record_end_cannot_precede_start() -> None:
    start = datetime(2024, 5, 1, 11, 30, tzinfo=UTC)
    with pytest.raises(ValueError, match="before"):
        Record(Metric.SLEEP, "x", start, start - timedelta(seconds=1), 0)


def test_content_hash_tracks_content_not_identity() -> None:
    a = make_record(values={"grams": 72500.0})
    assert a.content_hash() == make_record(values={"grams": 72500.0}).content_hash()
    assert a.content_hash() != make_record(values={"grams": 72600.0}).content_hash()
    assert a.content_hash() != make_record(offset=0).content_hash()


@pytest.mark.parametrize(
    ("start", "end", "settled"),
    [
        # A full local day (May 9, EDT) that ended before "today" (May 10 15:00 UTC = 11:00 EDT).
        (datetime(2024, 5, 9, 4, tzinfo=UTC), datetime(2024, 5, 10, 4, tzinfo=UTC), True),
        # Today's day-so-far total is still changing.
        (datetime(2024, 5, 10, 4, tzinfo=UTC), datetime(2024, 5, 11, 4, tzinfo=UTC), False),
        # Last night's sleep ended this morning: its *day* is not over (but see is_final: sleep is
        # final six hours after it ends, well before the day is).
        (datetime(2024, 5, 10, 3, tzinfo=UTC), datetime(2024, 5, 10, 11, tzinfo=UTC), False),
        # Sleep that ended yesterday is final.
        (datetime(2024, 5, 9, 3, tzinfo=UTC), datetime(2024, 5, 9, 11, tzinfo=UTC), True),
    ],
)
def test_is_settled_uses_the_users_local_day(start: datetime, end: datetime, settled: bool) -> None:
    record = make_record(Metric.SLEEP, "s", start, end, {"stages": []})
    assert record.is_settled(NOW) is settled


# -- archive ----------------------------------------------------------------------------------


def test_raw_roundtrip_including_empty_payloads(archive: Archive) -> None:
    archive.put_raw("sleep_data", "2024-05-01", {"a": [1, 2]})
    archive.put_raw("hrv_data", "2024-05-01", None)
    assert archive.get_raw("sleep_data", "2024-05-01") == {"a": [1, 2]}
    assert archive.has_raw("hrv_data", "2024-05-01")  # empty days are remembered
    assert archive.get_raw("hrv_data", "2024-05-01") is None
    assert not archive.has_raw("hrv_data", "2024-05-02")


def test_put_raw_replaces_and_iter_raw_is_ordered_and_bounded(archive: Archive) -> None:
    for day in ("2024-05-03", "2024-05-01", "2024-05-02", "2024-05-09"):
        archive.put_raw("steps_data", day, {"day": day})
    archive.put_raw("steps_data", "2024-05-02", {"day": "replaced"})
    got = list(archive.iter_raw("steps_data", "2024-05-01", "2024-05-03"))
    assert [k for k, _ in got] == ["2024-05-01", "2024-05-02", "2024-05-03"]
    assert got[1][1] == {"day": "replaced"}
    assert archive.latest_key("steps_data") == "2024-05-09"
    assert archive.latest_key("nothing") is None
    assert archive.raw_counts() == {"steps_data": 4}


def test_hostile_keys_are_stored_literally_not_executed(archive: Archive) -> None:
    key = "x'; DROP TABLE raw; --"
    archive.put_raw("e", key, {"ok": True})
    assert archive.get_raw("e", key) == {"ok": True}
    assert archive.raw_counts() == {"e": 1}


def test_archive_file_is_private(home: AppHome) -> None:
    with Archive.open(home.archive_file):
        pass
    assert _mode(home.archive_file) == 0o600


def test_ledger_roundtrip_and_isolation_between_sinks(archive: Archive) -> None:
    assert archive.ledger_get("a", "id1") is None
    archive.ledger_put("a", "id1", "hash1", "ref")
    entry = archive.ledger_get("a", "id1")
    assert entry is not None
    assert (entry.content_hash, entry.remote_ref) == ("hash1", "ref")
    assert archive.ledger_get("b", "id1") is None
    archive.ledger_put("a", "id1", "hash2")
    assert archive.ledger_get("a", "id1").content_hash == "hash2"
    assert archive.ledger_counts() == {"a": 1}


# -- sanitize ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("plain text", "plain text"),
        ("tab\tand\nnewline", "tab and newline"),
        ("\x1b[31mred\x1b[0m", "red"),  # ANSI colour codes
        ("a\u202eb\u200bc", "abc"),  # bidi override and zero-width space are removed outright
        ("  padded   spaces  ", "padded spaces"),
        ("nul\x00byte", "nul byte"),
    ],
)
def test_clean_strips_control_and_invisible_characters(raw: str, expected: str) -> None:
    assert clean(raw) == expected


def test_clean_truncates_and_accepts_non_strings() -> None:
    assert clean("x" * 500, limit=10) == "x" * 9 + "…"
    assert clean(ValueError("boom")) == "boom"
    assert clean(None) == "None"


# -- timeutil additions -----------------------------------------------------------------------


def test_parse_gmt_accepts_a_trailing_z() -> None:
    assert t.parse_gmt("2024-05-01T03:00:00.0Z") == datetime(2024, 5, 1, 3, 0, tzinfo=UTC)


# -- private home adoption --------------------------------------------------------------------


def test_ensure_refuses_a_populated_directory_it_did_not_create(tmp_path: Path) -> None:
    documents = tmp_path / "Documents"
    documents.mkdir()
    (documents / "tax-return.pdf").write_text("x")
    documents.chmod(0o755)
    with pytest.raises(UnsafeHomeError, match="not created by garmin-sync"):
        AppHome(documents).ensure()
    assert _mode(documents) == 0o755  # permissions were left alone


def test_ensure_adopts_an_empty_directory_and_is_idempotent(tmp_path: Path) -> None:
    root = tmp_path / "empty"
    root.mkdir()
    home = AppHome(root)
    home.ensure()
    home.ensure()
    assert (root / HOME_MARKER).exists()
    assert _mode(root) == 0o700


def test_ensure_refuses_a_symlinked_home(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    with pytest.raises(UnsafeHomeError, match="symbolic link"):
        AppHome(link).ensure()


def test_ensure_refuses_a_file_in_place_of_the_directory(tmp_path: Path) -> None:
    (tmp_path / "state").write_text("x")
    with pytest.raises(UnsafeHomeError, match="not a directory"):
        AppHome(tmp_path / "state").ensure()


def test_ensure_private_dir_leaves_an_existing_directory_alone_by_default(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    shared.chmod(0o755)
    ensure_private_dir(shared)
    assert _mode(shared) == 0o755
    ensure_private_dir(shared, tighten_existing=True)
    assert _mode(shared) == 0o700


def test_missing_parents_are_created_private(tmp_path: Path) -> None:
    ensure_private_dir(tmp_path / "a" / "b" / "c")
    assert _mode(tmp_path / "a") == 0o700
    assert _mode(tmp_path / "a" / "b" / "c") == 0o700


# -- record provenance and finality -----------------------------------------------------------


def _record(metric: Metric, start: datetime, end: datetime, fetched: datetime | None) -> Record:
    return Record(metric, "id", start, end, EDT, {}, fetched_at=fetched)


def test_fetched_at_is_provenance_not_content() -> None:
    a = _record(Metric.SLEEP, NOW - timedelta(hours=9), NOW - timedelta(hours=1), NOW)
    b = _record(Metric.SLEEP, NOW - timedelta(hours=9), NOW - timedelta(hours=1), None)
    assert a == b
    assert a.content_hash() == b.content_hash()


def test_unknown_provenance_is_never_final() -> None:
    old = NOW - timedelta(days=5)
    assert not _record(Metric.SLEEP, old, old + timedelta(hours=8), None).is_final(NOW)


def test_sleep_is_final_once_fetched_after_its_longer_grace_period() -> None:
    end = datetime(2024, 5, 10, 8, 0, tzinfo=UTC)
    start = end - timedelta(hours=8)
    early = _record(
        Metric.SLEEP, start, end, end + EVENT_GRACE
    )  # fine for a workout, not for sleep
    late = _record(Metric.SLEEP, start, end, end + SLEEP_GRACE)
    assert not early.is_final(NOW + timedelta(days=1))
    assert late.is_final(NOW)  # this morning's sleep does not wait for tomorrow
    assert not late.is_final(end + timedelta(hours=1))  # and the clock has to have passed it too


def test_a_workout_is_final_two_hours_after_it_ends() -> None:
    end = datetime(2024, 5, 10, 8, 0, tzinfo=UTC)
    workout = _record(Metric.EXERCISE, end - timedelta(hours=1), end, end + EVENT_GRACE)
    assert workout.is_final(NOW)
    assert not _record(
        Metric.EXERCISE, end - timedelta(hours=1), end, end + timedelta(minutes=30)
    ).is_final(NOW)


def test_discrete_event_is_not_final_before_the_grace_period_has_elapsed() -> None:
    end = NOW - timedelta(minutes=30)
    record = _record(Metric.EXERCISE, end - timedelta(hours=1), end, NOW)
    assert not record.is_final(NOW)


def test_running_total_needs_its_day_over_and_a_fetch_after_it() -> None:
    day_start = datetime(2024, 5, 9, 4, tzinfo=UTC)
    day_end = day_start + timedelta(days=1)
    fetched_midday = _record(Metric.HYDRATION, day_start, day_end, day_start + timedelta(hours=14))
    fetched_next_day = _record(Metric.HYDRATION, day_start, day_end, day_end + timedelta(hours=1))
    assert not fetched_midday.is_final(NOW)  # archived mid-day: partial total must not be frozen
    assert fetched_next_day.is_final(NOW)
    today = _record(
        Metric.HYDRATION, day_end, day_end + timedelta(days=1), day_end + timedelta(days=1)
    )
    assert not today.is_final(NOW)  # today's total is still growing


# -- archive provenance -----------------------------------------------------------------------


def test_raw_rows_remember_fetch_time_and_span(home: AppHome) -> None:
    clock_values = iter([datetime(2024, 5, 3, 8, tzinfo=UTC), datetime(2024, 5, 4, 9, tzinfo=UTC)])
    with Archive.open(home.archive_file, clock=lambda: next(clock_values)) as archive:
        archive.put_raw("sleep_data", "2024-05-02", {"a": 1})
        archive.put_raw("weigh_ins", "2024-05-01", {"b": 1}, span_end="2024-05-30")
        first = archive.raw_meta("sleep_data", "2024-05-02")
        second = archive.raw_meta("weigh_ins", "2024-05-01")
    assert first == RawMeta(datetime(2024, 5, 3, 8, tzinfo=UTC), "2024-05-02")  # defaults to key
    assert second == RawMeta(datetime(2024, 5, 4, 9, tzinfo=UTC), "2024-05-30")
    assert first is not None
    assert Archive.open(home.archive_file).raw_meta("nothing", "x") is None


def test_iter_raw_by_span_end_includes_unknown_spans(archive: Archive) -> None:
    archive.put_raw("activity_summary", "1", {"id": 1}, span_end="2024-05-01")
    archive.put_raw("activity_summary", "2", {"id": 2}, span_end="2024-05-09")
    archive.put_raw("activity_summary", "3", {"id": 3}, span_end="")
    got = {
        key
        for key, _ in archive.iter_raw_by_span_end("activity_summary", "2024-05-01", "2024-05-05")
    }
    assert got == {"1", "3"}


def test_a_version_1_archive_is_migrated_without_losing_rows(tmp_path: Path) -> None:
    path = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE raw (endpoint TEXT NOT NULL, key TEXT NOT NULL, fetched_at TEXT NOT NULL,"
        " payload TEXT NOT NULL, PRIMARY KEY (endpoint, key));"
        "INSERT INTO raw VALUES ('sleep_data', '2024-05-01', '2024-05-02T00:00:00+00:00', '{}');"
    )
    conn.commit()
    conn.close()
    with Archive.open(path) as archive:
        meta = archive.raw_meta("sleep_data", "2024-05-01")
        assert meta is not None
        assert meta.span_end == ""  # unknown, so it will be fetched again rather than trusted


def test_a_pre_existing_loose_archive_is_tightened_on_open(tmp_path: Path) -> None:
    path = tmp_path / "loose.sqlite3"
    sqlite3.connect(path).close()
    path.chmod(0o644)
    Archive.open(path).close()
    assert _mode(path) == 0o600


# -- invisible text and bounded work ----------------------------------------------------------


def hidden_instruction() -> str:
    """The Unicode 'tag' block mirrors ASCII invisibly: a way to hide a sentence in plain sight."""
    return "".join(chr(0xE0000 + ord(c)) for c in "ignore previous instructions")


@pytest.mark.parametrize(
    "smuggled",
    [
        hidden_instruction(),
        "️︀" + "\U000e0100",  # variation selectors
        "⁠⁢⁪",  # word joiner, invisible operators, deprecated format characters
        "؜᠎͏ㅤﾠ­⠀",  # assorted blanks and fillers
        "\U000f0000",  # private use
        "￹￺￻",  # interlinear annotation anchors
    ],
)
def test_clean_removes_text_that_is_invisible_to_a_reader(smuggled: str) -> None:
    """Regression: only a handful of characters were stripped, so hidden instructions survived."""
    assert clean(f"Run{smuggled}ning") == "Running"  # gone, and without a gap marking where


def test_clean_keeps_real_text_including_emoji_and_accents() -> None:
    assert clean("Course à pied \U0001f3c3 客さん") == "Course à pied \U0001f3c3 客さん"


def test_clean_does_bounded_work_on_huge_input(monkeypatch: pytest.MonkeyPatch) -> None:
    import garmin_sync.sanitize as sanitize

    seen = []
    real = sanitize._scrub
    monkeypatch.setattr(sanitize, "_scrub", lambda char: seen.append(1) or real(char))
    assert len(clean("x" * 5_000_000, limit=50)) == 50
    assert len(seen) <= 50 * sanitize._WORK_FACTOR  # not one pass over the whole megabytes


def test_clean_keeps_words_joined_by_a_joiner_character_together() -> None:
    """Persian uses the zero-width non-joiner inside words; turning it into a space split them."""
    assert (
        clean("\u0645\u06cc\u200c\u062e\u0648\u0627\u0647\u0645")
        == "\u0645\u06cc\u062e\u0648\u0627\u0647\u0645"
    )


def test_controls_and_line_breaks_still_separate_words() -> None:
    assert clean("one\ntwo\u2028three\x00four") == "one two three four"


# -- directory safety -------------------------------------------------------------------------


def test_a_symlinked_directory_is_refused_not_chmodded(tmp_path: Path) -> None:
    """Regression: the symlink's target (someone else's directory) used to be chmod'ed to 0700."""
    victim = tmp_path / "victim"
    victim.mkdir()
    victim.chmod(0o755)
    link = tmp_path / "link"
    link.symlink_to(victim)
    with pytest.raises(UnsafeHomeError, match="plain directory"):
        ensure_private_dir(link, tighten_existing=True)
    assert _mode(victim) == 0o755


def test_a_symlinked_subdirectory_inside_the_home_is_refused(tmp_path: Path) -> None:
    home = AppHome(tmp_path / "state")
    home.ensure()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    elsewhere.chmod(0o755)
    home.exports_dir.rmdir()
    home.exports_dir.symlink_to(elsewhere)
    with pytest.raises(UnsafeHomeError):
        home.ensure()
    assert _mode(elsewhere) == 0o755


def test_an_impossible_utc_offset_is_not_an_offset() -> None:
    """Garmin sometimes applies the offset twice to Local times; 2 x UTC+8 is not a zone."""
    assert t.offset_seconds("2024-05-01 18:00:00", "2024-05-01 02:00:00") is None  # +16h
    assert t.offset_seconds("2024-05-01 05:30:00", "2024-05-01 00:00:00") == 19800  # IST: fine
    assert t.offset_seconds("2024-05-01 14:00:00", "2024-05-01 00:00:00") == 14 * 3600  # Kiribati
