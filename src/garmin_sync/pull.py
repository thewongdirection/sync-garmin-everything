"""Download Garmin data into the local archive. Resumable and fault-isolated.

* A stored payload is **final** only if it covered the whole requested window *and* was
  fetched after that window had settled (:func:`is_final`). Anything else is fetched again,
  so a day archived while it was still in progress, or a range window that an earlier
  ``--until`` cut short, is never frozen with partial data.
* Windows are walked newest first, so a feature the device lacks (which fails on every
  day) switches itself off after three tries without starving the recent days that matter.
* One failing endpoint never aborts the run; auth loss and HTTP 429 abort it cleanly with
  everything fetched so far kept.
* Everything that comes from Garmin and ends up in a report or a path is validated or
  sanitized first.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, TypeVar

from garmin_sync import endpoints as ep
from garmin_sync.archive import Archive, RawMeta
from garmin_sync.garmin_source import AuthRequired, GarminSource, RateLimited
from garmin_sync.paths import write_private_bytes
from garmin_sync.sanitize import clean
from garmin_sync.timeutil import chunk_days, iter_days

MAX_CONSECUTIVE_FAILURES = 3
# Garmin may revise a day's data after the watch syncs, which can be a day or more later.
SETTLE_AFTER = timedelta(hours=36)
MAX_FIT_BYTES = 100 * 1024 * 1024
# Per pull. Far beyond any real range; a bound on what a hostile reply can make us do.
MAX_ACTIVITIES = 2000
_ACTIVITY_ID = re.compile(r"[0-9]{1,20}")

Progress = Callable[[str], None]
T = TypeVar("T")


@dataclass
class PullReport:
    fetched: Counter[str] = field(default_factory=Counter)
    skipped: Counter[str] = field(default_factory=Counter)
    failed: Counter[str] = field(default_factory=Counter)
    first_error: dict[str, str] = field(default_factory=dict)
    disabled: list[str] = field(default_factory=list)
    rejected_ids: int = 0
    aborted: str | None = None  # "rate_limited" | "auth_required"

    def to_dict(self) -> dict[str, object]:
        return {
            "fetched": dict(self.fetched),
            "skipped": dict(self.skipped),
            "failed": dict(self.failed),
            "first_error": self.first_error,
            "disabled_endpoints": self.disabled,
            "rejected_activity_ids": self.rejected_ids,
            "aborted": self.aborted,
        }


def is_final(
    meta: RawMeta | None, window_end: date, settle_after: timedelta = SETTLE_AFTER
) -> bool:
    """True if ``meta`` describes a fetch that cannot have missed any of the window.

    The row must have covered the window to its last day, and been fetched at least
    ``settle_after`` after that day ended (measured from UTC midnight, which is within a
    day of the local midnight in any time zone and therefore covered by the margin).
    """
    if meta is None or not meta.span_end or meta.span_end < window_end.isoformat():
        return False
    window_over = datetime.combine(window_end + timedelta(days=1), time.min, tzinfo=UTC)
    return meta.fetched_at >= window_over + settle_after


def valid_activity_id(raw: object) -> str | None:
    """``raw`` as a safe activity id (1-20 digits), else None. Ids end up in file names."""
    if isinstance(raw, bool) or not isinstance(raw, int | str):
        return None
    text = str(raw)
    return text if _ACTIVITY_ID.fullmatch(text) else None


class _Puller:
    def __init__(
        self,
        source: GarminSource,
        archive: Archive,
        *,
        force: bool,
        today: date,
        progress: Progress,
        detail_names: frozenset[str] | None,
    ) -> None:
        self.source = source
        self.archive = archive
        self.force = force
        self.today = today
        self.progress = progress
        self.detail_names = detail_names
        self.report = PullReport()
        self._streak: Counter[str] = Counter()
        self._disabled: set[str] = set()

    def final(self, name: str, key: str, window_end: date | None) -> bool:
        if self.force or window_end is None:
            return False
        return is_final(self.archive.raw_meta(name, key), window_end)

    def attempt(self, name: str, key: str, call: Callable[[], T]) -> tuple[bool, T | None]:
        """Run one Garmin call under the failure policy. Returns ``(succeeded, value)``."""
        if name in self._disabled:
            return False, None
        try:
            value = call()
        except (RateLimited, AuthRequired):
            raise
        except Exception as exc:  # noqa: BLE001 - isolate any per-endpoint failure
            self._record_failure(name, key, exc)
            return False, None
        self._streak[name] = 0
        return True, value

    def fetch(
        self, name: str, method: str, key: str, *args: object, span_end: str | None = None
    ) -> bool:
        """Fetch and archive one item. Returns True if the payload was archived."""
        ok, payload = self.attempt(name, key, lambda: self.source.call(method, *args))
        if ok:
            self.archive.put_raw(name, key, payload, span_end=span_end)
            self.report.fetched[name] += 1
        return ok

    def record_failure(self, name: str, key: str, exc: Exception) -> None:
        self._record_failure(name, key, exc)

    def _record_failure(self, name: str, key: str, exc: Exception) -> None:
        self.report.failed[name] += 1
        self.report.first_error.setdefault(
            name, clean(f"{key}: {type(exc).__name__}: {exc}", limit=200)
        )
        self._streak[name] += 1
        if self._streak[name] >= MAX_CONSECUTIVE_FAILURES and name not in self._disabled:
            self._disabled.add(name)
            self.report.disabled.append(name)
            self.progress(f"  {name}: failing repeatedly, skipping for the rest of this run")

    def skip(self, name: str) -> None:
        self.report.skipped[name] += 1


def pull(
    source: GarminSource,
    archive: Archive,
    start: date,
    end: date,
    *,
    only: Iterable[str] | None = None,
    force: bool = False,
    include_activities: bool = True,
    activity_details: Iterable[str] | None = None,
    fit_dir: Path | None = None,
    today: date | None = None,
    progress: Progress = lambda _msg: None,
) -> PullReport:
    """Archive everything between ``start`` and ``end`` (inclusive).

    ``activity_details`` limits which per-activity detail calls are made (default: all).
    Raises ``ValueError`` for an unknown name in ``only``.
    """
    puller = _Puller(
        source,
        archive,
        force=force,
        today=today or date.today(),
        progress=progress,
        detail_names=None if activity_details is None else frozenset(activity_details),
    )
    requested = list(only) if only is not None else None
    selected = _select(requested)
    try:
        for endpoint in selected:
            progress(f"{endpoint.name} ...")
            _pull_endpoint(puller, endpoint, start, end)
        if include_activities and (requested is None or "activities" in requested):
            progress("activities ...")
            _pull_activities(puller, start, end, fit_dir)
    except RateLimited:
        puller.report.aborted = "rate_limited"
    except AuthRequired:
        puller.report.aborted = "auth_required"
    return puller.report


def _select(only: list[str] | None) -> list[ep.Endpoint]:
    if only is None:
        return list(ep.ENDPOINTS)
    try:
        return [ep.get_endpoint(name) for name in only if name != "activities"]
    except KeyError as exc:
        raise ValueError(f"unknown endpoint {exc.args[0]!r}") from None


def _pull_endpoint(puller: _Puller, endpoint: ep.Endpoint, start: date, end: date) -> None:
    match endpoint.scope:
        case ep.Scope.DAY:
            for day in reversed(list(iter_days(start, end))):
                key = day.isoformat()
                if puller.final(endpoint.name, key, day):
                    puller.skip(endpoint.name)
                else:
                    puller.fetch(endpoint.name, endpoint.method, key, key)
        case ep.Scope.RANGE:
            for window_start, window_end in reversed(
                list(chunk_days(start, end, endpoint.span_days))
            ):
                key = window_start.isoformat()
                if puller.final(endpoint.name, key, window_end):
                    puller.skip(endpoint.name)
                else:
                    puller.fetch(
                        endpoint.name,
                        endpoint.method,
                        key,
                        key,
                        window_end.isoformat(),
                        span_end=window_end.isoformat(),
                    )
        case ep.Scope.SNAPSHOT:
            key = puller.today.isoformat()
            if puller.archive.has_raw(endpoint.name, key):
                puller.skip(endpoint.name)
            else:
                puller.fetch(endpoint.name, endpoint.method, key)


def _pull_activities(puller: _Puller, start: date, end: date, fit_dir: Path | None) -> None:
    name = ep.ACTIVITY_SUMMARY
    ok, summaries = puller.attempt(
        name,
        f"{start}..{end}",
        lambda: puller.source.call("get_activities_by_date", start.isoformat(), end.isoformat()),
    )
    if not ok:
        return
    for summary in (summaries if isinstance(summaries, list) else [])[:MAX_ACTIVITIES]:
        activity_id = valid_activity_id(
            summary.get("activityId") if isinstance(summary, dict) else None
        )
        if activity_id is None:
            puller.report.rejected_ids += 1
            continue
        day = _activity_day(summary)
        puller.archive.put_raw(name, activity_id, summary, span_end=day.isoformat() if day else "")
        puller.report.fetched[name] += 1
        _pull_activity_details(puller, activity_id, summary, day)
        if fit_dir is not None:
            _download_fit(puller, activity_id, fit_dir)


def _activity_day(summary: dict[str, Any]) -> date | None:
    """Local start date of an activity, or None if it cannot be read."""
    for key in ("startTimeLocal", "startTimeGMT"):
        value = summary.get(key)
        if isinstance(value, str):
            try:
                return date.fromisoformat(value[:10])
            except ValueError:
                continue
    return None


def _is_strength(summary: dict[str, Any]) -> bool:
    activity_type = summary.get("activityType")
    type_key = activity_type.get("typeKey", "") if isinstance(activity_type, dict) else ""
    return "strength" in str(type_key)


def _pull_activity_details(
    puller: _Puller, activity_id: str, summary: dict[str, Any], day: date | None
) -> None:
    details = list(ep.ACTIVITY_DETAILS)
    if _is_strength(summary):
        details.append(ep.ACTIVITY_EXERCISE_SETS)
    span = day.isoformat() if day else ""
    for name, method in details:
        if puller.detail_names is not None and name not in puller.detail_names:
            continue
        if puller.final(name, activity_id, day):
            puller.skip(name)
        else:
            puller.fetch(name, method, activity_id, activity_id, span_end=span)


def _download_fit(puller: _Puller, activity_id: str, fit_dir: Path) -> None:
    target = fit_dir / f"{activity_id}.zip"  # activity_id is digits only (valid_activity_id)
    if target.exists():
        puller.skip("fit_download")
        return

    def download() -> bytes:
        data = puller.source.download_original_activity(activity_id)
        if len(data) > MAX_FIT_BYTES:
            raise ValueError("FIT download larger than the size limit")
        return data

    ok, data = puller.attempt("fit_download", activity_id, download)
    if ok and data is not None:
        write_private_bytes(target, data)
        puller.report.fetched["fit_download"] += 1
