"""Google Health API sink: create data points, leaving anything Google already holds alone.

Before creating a record the sink lists existing data points around it (from *any*
source, which needs the read scopes) and skips the record if one overlaps. That keeps the
sync additive: data Garmin Connect already delivered is left alone. The duplicate check
has three outcomes (found, none, cannot tell) and only "none" permits a write.

Creates carry a deterministic client-chosen data point id, so repeating one (after a
timeout, or from a cloud run that has no local ledger) cannot store it twice: Google
answers 409 and the record is reported as already present.

Validated against the published v4 reference only; docs/PLAN.md lists what a live
account still has to confirm.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from garmin_sync.coverage import GOOGLE_WRITABLE
from garmin_sync.models import Metric, Record
from garmin_sync.sanitize import clean
from garmin_sync.sinks.base import Outcome, OutcomeCallback, Status
from garmin_sync.sinks.google_auth import SCOPE_PREFIX, WRITE_PATH_SCOPES, GoogleAuthError
from garmin_sync.sinks.google_client import (
    API_ROOT,
    GoogleApiError,
    GoogleClient,
    GooglePermissionError,
)
from garmin_sync.sinks.google_payloads import Unbuildable, build_data_point
from garmin_sync.timeutil import iso_z, local_date

_MAX_PAGES = 10
_PAGE_SIZE = 25  # the maximum Google allows for sleep and exercise
_SAMPLE_WINDOW = timedelta(minutes=15)
_MAX_CONSECUTIVE_ERRORS = 5
_CONFLICT = 409
_ALREADY_EXISTS = "ALREADY_EXISTS"  # Google's name for the one kind of 409 that means "stored"
_GRPC_ALREADY_EXISTS = 6
_SAMPLE_METRICS = frozenset({Metric.WEIGHT, Metric.BODY_FAT})


class _Unverifiable(Exception):
    """Existing data could not be inspected, so a write would risk a duplicate."""


class GoogleHealthSink:
    name = "google-health"
    updates_in_place = False

    def __init__(self, client: GoogleClient, *, check_duplicates: bool = True) -> None:
        self._client = client
        self._check_duplicates = check_duplicates

    def supports(self, metric: Metric) -> bool:
        return metric in GOOGLE_WRITABLE

    # -- sink interface -------------------------------------------------------------------

    def push(
        self,
        records: Sequence[Record],
        *,
        apply: bool,
        on_outcome: OutcomeCallback,
        max_writes: int | None = None,
    ) -> None:
        if (problem := self._login_problem()) is not None:
            for index, record in enumerate(records):
                status = Status.FAILED if index == 0 else Status.SKIPPED
                on_outcome(Outcome(record, status, problem))
            return
        run = _Run(apply=apply, max_writes=max_writes)
        for record in records:
            if run.stopped:
                on_outcome(Outcome(record, Status.SKIPPED, run.stopped))
                continue
            outcome = self._push_one(record, run)
            run.note(outcome)
            on_outcome(outcome)

    def _login_problem(self) -> str | None:
        """Why this login cannot be used to write safely, or None.

        Without the read scopes, listing only shows data this app wrote itself, so data Garmin
        Connect already sent would be invisible and written a second time.
        """
        try:
            missing = self._client.missing_scopes(WRITE_PATH_SCOPES)
        except GoogleAuthError as exc:
            return clean(exc)
        if missing:
            names = ", ".join(scope.removeprefix(SCOPE_PREFIX) for scope in missing)
            return (
                f"the Google login lacks permissions ({names}); run `garmin-sync auth google "
                "login` again and tick every box"
            )
        return None

    def _push_one(self, record: Record, run: _Run) -> Outcome:
        try:
            data_type, body = build_data_point(record)
        except Unbuildable as exc:
            return Outcome(record, Status.UNSUPPORTED, str(exc))
        if data_type in run.denied:
            return Outcome(record, Status.SKIPPED, f"{data_type}: permission denied earlier")
        try:
            return self._write(record, data_type, body, run)
        except GooglePermissionError as exc:
            run.denied.add(data_type)
            if len(run.denied) >= 2:
                run.stopped = "stopped: permission denied for several data types"
            return Outcome(record, Status.FAILED, _permission_hint(exc))
        except GoogleAuthError as exc:
            run.stopped = f"stopped: {clean(exc)}"
            return Outcome(record, Status.FAILED, clean(exc))
        except _Unverifiable as exc:
            return Outcome(record, Status.FAILED, f"not written, duplicates unverifiable: {exc}")
        except GoogleApiError as exc:
            return Outcome(record, Status.FAILED, clean(exc))

    def _write(self, record: Record, data_type: str, body: dict[str, Any], run: _Run) -> Outcome:
        if self._check_duplicates and (found := self._find_overlap(record, data_type)):
            return Outcome(
                record, Status.DUPLICATE_EXISTING, "equivalent data already in Google", found
            )
        if run.limit_reached:
            return Outcome(record, Status.SKIPPED, "write limit reached for this run")
        run.writes += 1  # an attempted create counts, whatever comes back: it may have landed
        if not run.apply:
            return Outcome(record, Status.WOULD_PUSH, remote_ref=body["name"])
        try:
            result = self._client.post(
                f"{API_ROOT}/users/me/dataTypes/{data_type}/dataPoints", body
            )
        except GoogleApiError as exc:
            if exc.status == _CONFLICT and exc.reason == _ALREADY_EXISTS:
                return Outcome(record, Status.DUPLICATE_EXISTING, "already stored", body["name"])
            raise  # any other conflict (for example ABORTED) is a failure to retry, not a duplicate
        if error := result.get("error"):
            if isinstance(error, dict) and error.get("code") == _GRPC_ALREADY_EXISTS:
                return Outcome(record, Status.DUPLICATE_EXISTING, "already stored", body["name"])
            message = error.get("message") if isinstance(error, dict) else error
            return Outcome(record, Status.FAILED, clean(message or "create failed"))
        done = result.get("done")  # protobuf JSON leaves out `done` while it is false
        if done is True:
            return Outcome(record, Status.PUSHED, remote_ref=body["name"])
        if done is None or done is False:
            # There is no way to poll the operation, so do not claim it finished (and do not
            # record it as stored); the next run's duplicate check finds it if it did.
            return Outcome(record, Status.PENDING, "accepted; Google is still processing it")
        return Outcome(record, Status.FAILED, "unrecognised response to the create")

    # -- duplicate detection --------------------------------------------------------------

    def _find_overlap(self, record: Record, data_type: str) -> str | None:
        """Name of an existing data point that overlaps ``record``; None if there is none.

        Raises :class:`_Unverifiable` if it cannot be established either way.
        """
        query = _dedupe_filter(record, data_type)
        payload_key = _payload_key(data_type)
        params: dict[str, Any] = {"filter": query, "pageSize": _PAGE_SIZE}
        for _ in range(_MAX_PAGES):
            page = self._client.get(
                f"{API_ROOT}/users/me/dataTypes/{data_type}/dataPoints", **params
            )
            if page.get("error"):
                raise _Unverifiable("the lookup answered with an error")
            points = page.get("dataPoints", [])
            if not isinstance(points, list):
                raise _Unverifiable("the existing data list was not a list")
            for point in points:
                if _overlaps(record, _dict_of(point).get(payload_key)):
                    return str(_dict_of(point).get("name") or "existing")
            if not (token := page.get("nextPageToken")):
                return None
            params["pageToken"] = token
        raise _Unverifiable("more existing data points than the page limit")


class _Run:
    """Mutable state of one ``push`` call."""

    def __init__(self, *, apply: bool, max_writes: int | None) -> None:
        self.apply = apply
        self.max_writes = max_writes
        self.writes = 0
        self.denied: set[str] = set()
        self.stopped: str | None = None
        self._consecutive_errors = 0

    @property
    def limit_reached(self) -> bool:
        return self.max_writes is not None and self.writes >= self.max_writes

    def note(self, outcome: Outcome) -> None:
        if outcome.status is Status.FAILED:
            self._consecutive_errors += 1
            if self._consecutive_errors >= _MAX_CONSECUTIVE_ERRORS and not self.stopped:
                self.stopped = "stopped: repeated errors"
        elif outcome.status is not Status.SKIPPED:
            self._consecutive_errors = 0


# -- helpers --------------------------------------------------------------------------------


def _list_of(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict_of(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _payload_key(data_type: str) -> str:
    """``body-fat`` -> ``bodyFat`` (the DataPoint payload field for a data type)."""
    head, *rest = data_type.split("-")
    return head + "".join(part.title() for part in rest)


def _filter_name(data_type: str) -> str:
    return data_type.replace("-", "_")


def _dedupe_filter(record: Record, data_type: str) -> str:
    """AIP-160 filter selecting existing data that could duplicate ``record``."""
    name = _filter_name(data_type)
    if record.metric in _SAMPLE_METRICS:
        low, high = record.start - _SAMPLE_WINDOW, record.start + _SAMPLE_WINDOW
        field = f"{name}.sample_time.physical_time"
        return f'{field} >= "{iso_z(low)}" AND {field} < "{iso_z(high)}"'
    if record.metric is Metric.SLEEP:
        field = "sleep.interval.end_time"
        return (
            f'{field} >= "{iso_z(record.start)}" '
            f'AND {field} < "{iso_z(record.end + timedelta(days=1))}"'
        )
    # Other sessions are filtered by civil start date; a one-day margin either side means a
    # zone difference cannot hide an overlap (the overlap test below is exact).
    first = local_date(record.start, record.utc_offset_s) - timedelta(days=1)
    last = local_date(record.end, record.utc_offset_s) + timedelta(days=2)
    field = f"{name}.interval.civil_start_time"
    return f'{field} >= "{first.isoformat()}" AND {field} < "{last.isoformat()}"'


def _overlaps(record: Record, existing: Any) -> bool:
    """Does an existing payload overlap ``record``? Raises if it cannot be told."""
    if not isinstance(existing, dict):
        raise _Unverifiable("an existing data point has an unreadable payload")
    if record.metric in _SAMPLE_METRICS:
        when = _parse_rfc3339(_dict_of(existing.get("sampleTime")).get("physicalTime"))
        return abs(when - record.start) <= _SAMPLE_WINDOW
    interval = _dict_of(existing.get("interval"))
    start, end = _parse_rfc3339(interval.get("startTime")), _parse_rfc3339(interval.get("endTime"))
    return start < record.end and end > record.start


def _parse_rfc3339(value: Any) -> datetime:
    """Aware UTC datetime from an RFC 3339 string; raises if absent, malformed or zone-less."""
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            parsed = None
        if parsed is not None and parsed.tzinfo is not None:
            return parsed.astimezone(UTC)
    raise _Unverifiable("an existing data point has an unreadable time")


def _permission_hint(exc: GooglePermissionError) -> str:
    return (
        f"{exc}. Check that the Google Health API is enabled for your project, that you are a "
        "listed test user, and that the login granted every requested scope."
    )
