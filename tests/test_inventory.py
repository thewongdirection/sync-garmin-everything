"""``inventory``: finding the fields an account returns that the field map does not know."""

from __future__ import annotations

from pathlib import Path

from garmin_sync import endpoints as ep
from garmin_sync.archive import Archive
from garmin_sync.inventory import (
    build_inventory,
    default_map_path,
    flatten,
    load_known_paths,
    method_for,
)
from tests.helpers import activity_summary, sleep_payload

SECRET_VALUE = "VALUE-THAT-MUST-NOT-APPEAR"


def test_flatten_names_nested_paths_and_list_items() -> None:
    paths = dict(flatten({"a": {"b": 1, "c": [{"d": "x"}, {"d": None}]}, "e": [[1, 2]], "f": True}))
    assert paths == {
        "a": "object",
        "a.b": "int",
        "a.c": "array",
        "a.c[]": "object",
        "a.c[].d": "null",  # the last sample wins; types of every item are merged by the caller
        "e": "array",
        "e[]": "array",
        "e[][]": "int",
        "f": "bool",
    }


def test_flatten_handles_top_level_lists_and_scalars() -> None:
    assert list(flatten([{"x": 1.5}])) == [("[]", "object"), ("[].x", "float")]
    assert list(flatten("text")) == []


def test_flatten_gives_up_on_absurd_nesting() -> None:
    deep: object = "leaf"
    for _ in range(100):
        deep = {"k": deep}
    assert len(list(flatten(deep))) < 100


def test_endpoint_names_map_back_to_library_methods() -> None:
    assert method_for("sleep_data") == "get_sleep_data"
    assert method_for(ep.ACTIVITY_SUMMARY) == "get_activities_by_date"
    assert method_for("activity_splits") == "get_activity_splits"
    assert method_for("activity_exercise_sets") == "get_activity_exercise_sets"


def write_map(path: Path, rows: dict[str, list[str]]) -> Path:
    lines = ["method,path"] + [f"{method},{p}" for method, paths in rows.items() for p in paths]
    path.write_text("\n".join(lines) + "\n")
    return path


def test_unmapped_fields_are_listed_with_types_but_never_values(
    archive: Archive, tmp_path: Path
) -> None:
    payload = sleep_payload()
    payload["dailySleepDTO"]["brandNewField"] = SECRET_VALUE
    payload["brandNewList"] = [{"nested": 5}]
    archive.put_raw("sleep_data", "2024-05-02", payload)
    known = load_known_paths(
        write_map(
            tmp_path / "map.csv",
            {"get_sleep_data": ["dailySleepDTO", "dailySleepDTO.calendarDate"]},
        )
    )
    entry = build_inventory(archive, known)["sleep_data"]
    assert entry["method"] == "get_sleep_data"
    assert entry["unmapped"]["dailySleepDTO.brandNewField"] == ["str"]
    assert entry["unmapped"]["brandNewList[].nested"] == ["int"]
    assert "dailySleepDTO.calendarDate" not in entry["unmapped"]
    assert SECRET_VALUE not in str(entry)
    assert entry["unmapped_count"] == len(entry["unmapped"])


def test_types_seen_across_rows_are_merged(archive: Archive) -> None:
    archive.put_raw("hrv_data", "2024-05-01", {"x": 1})
    archive.put_raw("hrv_data", "2024-05-02", {"x": None})
    assert build_inventory(archive, {})["hrv_data"]["unmapped"]["x"] == ["int", "null"]


def test_activity_rows_are_compared_as_list_items(archive: Archive, tmp_path: Path) -> None:
    """The map lists activity fields as `[].field`, but the archive stores one activity per row."""
    archive.put_raw("activity_summary", "1001", activity_summary())
    known = load_known_paths(
        write_map(
            tmp_path / "map.csv",
            {"get_activities_by_date": ["[]", "[].activityId", "[].activityName"]},
        )
    )
    unmapped = build_inventory(archive, known)["activity_summary"]["unmapped"]
    assert "[].activityId" not in unmapped and "[].distance" in unmapped


def test_the_shipped_field_map_covers_the_core_sleep_fields(archive: Archive) -> None:
    map_path = default_map_path()
    assert map_path is not None and map_path.name == "garmin-fields.csv"
    archive.put_raw("sleep_data", "2024-05-02", sleep_payload())
    unmapped = build_inventory(archive, load_known_paths(map_path))["sleep_data"]["unmapped"]
    assert "dailySleepDTO.sleepStartTimestampGMT" not in unmapped
    assert "sleepLevels[].activityLevel" not in unmapped
