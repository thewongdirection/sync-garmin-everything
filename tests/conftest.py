from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from garmin_sync.archive import Archive
from garmin_sync.garmin_source import GarminSource
from garmin_sync.models import Metric, Record
from garmin_sync.paths import AppHome
from tests.helpers import EDT, FakeGarmin

NOW = datetime(2024, 5, 10, 15, 0, tzinfo=UTC)


class FakeClock:
    """A settable clock handed to the archive so tests control every ``fetched_at``."""

    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Never touch the real home directory or inherit real credentials."""
    monkeypatch.setenv("HOME", str(tmp_path / "fakehome"))
    for name in ("GARMIN_SYNC_HOME", "GARMINTOKENS", "GARMIN_EMAIL", "GARMIN_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    os.umask(0o022)


@pytest.fixture
def home(tmp_path: Path) -> AppHome:
    app_home = AppHome(tmp_path / "state")
    app_home.ensure()
    return app_home


@pytest.fixture
def archive(home: AppHome, clock: FakeClock) -> Iterator[Archive]:
    with Archive.open(home.archive_file, clock=clock) as opened:
        yield opened


@pytest.fixture
def fake_api() -> FakeGarmin:
    return FakeGarmin()


@pytest.fixture
def source(fake_api: FakeGarmin) -> GarminSource:
    return GarminSource(fake_api, delay_s=0, sleep=lambda _s: None)


def make_record(
    metric: Metric = Metric.WEIGHT,
    external_id: str = "garmin:weight:1",
    start: datetime | None = None,
    end: datetime | None = None,
    values: dict | None = None,
    offset: int = EDT,
    fetched_at: datetime | None = NOW,
) -> Record:
    """A record; by default one that was fetched at ``NOW`` (so old ones are final)."""
    start = start or datetime(2024, 5, 1, 11, 30, tzinfo=UTC)
    return Record(
        metric,
        external_id,
        start,
        end or start,
        offset,
        values if values is not None else {"grams": 72500.0},
        fetched_at=fetched_at,
    )
