"""The published field map: internally consistent, in step with the code, and regenerable."""

from __future__ import annotations

import csv
import importlib.util
from pathlib import Path

import pytest

from garmin_sync.coverage import GOOGLE_WRITABLE, TARGETS

ROOT = Path(__file__).resolve().parent.parent
MAP_DIR = ROOT / "docs" / "field-map"
TIERS = {"G+H", "G", "H", "GO", "A", "M", "N"}


def read(name: str) -> list[dict[str, str]]:
    with (MAP_DIR / name).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture(scope="module")
def garmin() -> list[dict[str, str]]:
    return read("garmin-fields.csv")


@pytest.fixture(scope="module")
def google() -> list[dict[str, str]]:
    return read("google-fields.csv")


def generator():
    spec = importlib.util.spec_from_file_location(
        "generate_docs", ROOT / "tools" / "field_map" / "generate_docs.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_row_has_a_known_tier_and_a_reason_or_a_destination(garmin) -> None:
    for row in garmin:
        assert row["tier"] in TIERS, row
        if row["tier"] in {"A", "M", "N"}:
            assert row["reason"], f"{row['method']}:{row['path']} has no reason"
        if row["tier"] in {"G", "G+H"}:
            assert row["google_target"], f"{row['method']}:{row['path']} has no Google target"
        if row["tier"] in {"H", "GO"}:
            assert row["google_target"] or row["hc_target"], f"{row['method']}:{row['path']}"


def test_every_google_target_exists_in_the_discovery_derived_field_list(garmin, google) -> None:
    known = {r["path"] for r in google}
    known |= {f"{r['google_root']}.{r['path']}" for r in google}
    known |= {r["path"].removeprefix("dataPoint.") for r in google}
    missing = {
        (r["method"], target)
        for r in garmin
        for target in (t.strip() for t in r["google_target"].split(";"))
        if target and target not in known
    }
    assert not missing, sorted(missing)[:10]


def test_every_health_connect_record_the_code_exports_is_in_the_map(garmin) -> None:
    mapped = " ".join(r["hc_target"] for r in garmin)
    absent = {t.health_connect for t in TARGETS.values() if t.health_connect not in mapped}
    assert not absent, absent


def test_every_google_type_the_code_writes_is_marked_writable_in_the_map(google) -> None:
    roots = {r["google_root"].removeprefix("dataPoint.") for r in google if r["status"] == "FILLED"}
    camel = {"hydration-log": "hydrationLog", "body-fat": "bodyFat"}
    for metric in GOOGLE_WRITABLE:
        google_type = TARGETS[metric].google_type
        assert camel.get(google_type, google_type) in roots, google_type


def test_the_committed_field_map_matches_the_csvs(garmin, google) -> None:
    """Edit the CSVs, run tools/field_map/generate_docs.py, commit both."""
    committed = (MAP_DIR / "FIELD-MAP.md").read_text(encoding="utf-8")
    assert generator().build(garmin, google) == committed
