"""Select records for a sink, honour the push ledger, and record what was written.

Rules that keep the sync safe to run repeatedly:
* A record already in the ledger with the same content is never sent again.
* Sinks that cannot update in place (Google Health) also never re-send a changed record.
* A record is sent only once it is *final* (``Record.is_final``): its source data was
  downloaded after the moment it stopped changing. Everything else waits for a later run.
* Progress is written to the ledger one record at a time, as each outcome arrives.
* Nothing is written (or ledgered) unless ``apply`` is True.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from garmin_sync.archive import Archive
from garmin_sync.models import Metric, Record
from garmin_sync.sanitize import clean
from garmin_sync.sinks.base import Outcome, Sink, Status

_MAX_SAMPLE_ERRORS = 5


@dataclass
class PushReport:
    sink: str
    applied: bool
    outcomes: Counter[str] = field(default_factory=Counter)
    by_metric: dict[str, Counter[str]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def count(self, label: str, metric: Metric | None = None) -> None:
        self.outcomes[label] += 1
        if metric is not None:
            self.by_metric.setdefault(metric.value, Counter())[label] += 1

    @property
    def ok(self) -> bool:
        """False if any record failed (skipped, deferred and unsupported are not failures)."""
        return not self.outcomes[Status.FAILED.value]

    def to_dict(self) -> dict[str, object]:
        return {
            "sink": self.sink,
            "applied": self.applied,
            "outcomes": dict(self.outcomes),
            "by_metric": {m: dict(c) for m, c in sorted(self.by_metric.items())},
            "sample_errors": self.errors,
        }


def push_records(
    sink: Sink,
    archive: Archive,
    records: Sequence[Record],
    *,
    apply: bool,
    now: datetime,
    metrics: Collection[Metric] | None = None,
    limit: int | None = None,
) -> PushReport:
    """Send the records that are due to ``sink``; ``limit`` caps how many are written."""
    report = PushReport(sink=sink.name, applied=apply)
    candidates = [r for r in records if _is_due(sink, archive, r, report, now, metrics)]
    candidates.sort(key=lambda r: (r.start, r.external_id))  # oldest first

    pending = {r.external_id: r for r in candidates}

    def record_outcome(outcome: Outcome) -> None:
        pending.pop(outcome.record.external_id, None)
        _account(report, archive, sink, outcome, apply)

    sink.push(candidates, apply=apply, on_outcome=record_outcome, max_writes=limit)
    for record in list(pending.values()):  # a sink must answer for every record; if not, say so
        record_outcome(Outcome(record, Status.FAILED, "sink gave no result"))
    return report


def _is_due(
    sink: Sink,
    archive: Archive,
    record: Record,
    report: PushReport,
    now: datetime,
    metrics: Collection[Metric] | None,
) -> bool:
    if metrics is not None and record.metric not in metrics:
        return False
    if not sink.supports(record.metric):
        report.count("unsupported_metric", record.metric)
        return False
    if not record.is_final(now):
        report.count("deferred_not_final", record.metric)
        return False
    entry = archive.ledger_get(sink.name, record.external_id)
    if entry is not None and (
        not sink.updates_in_place or entry.content_hash == record.content_hash()
    ):
        report.count("already_synced", record.metric)
        return False
    return True


def _account(
    report: PushReport, archive: Archive, sink: Sink, outcome: Outcome, apply: bool
) -> None:
    report.count(outcome.status.value, outcome.record.metric)
    if outcome.status is Status.FAILED and len(report.errors) < _MAX_SAMPLE_ERRORS:
        report.errors.append(clean(f"{outcome.record.external_id}: {outcome.detail}", limit=300))
    if apply and outcome.status in (Status.PUSHED, Status.DUPLICATE_EXISTING):
        archive.ledger_put(
            sink.name, outcome.record.external_id, outcome.record.content_hash(), outcome.remote_ref
        )
