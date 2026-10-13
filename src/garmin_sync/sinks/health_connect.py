"""Health Connect export: JSON Lines shaped for an Android companion app to ingest.

Health Connect is an on-device store with no cloud API, so this sink writes a file that a
companion app (not part of this repo) inserts through the Health Connect SDK. Each line
carries a ``clientRecordId`` and a monotonically increasing ``clientRecordVersion`` so that
re-imports upsert instead of duplicating. The format is specified in
docs/health-connect-export.md.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from garmin_sync.coverage import HEALTH_CONNECT_METRICS, TARGETS
from garmin_sync.models import Metric, Record, utc_now
from garmin_sync.paths import write_private_text
from garmin_sync.sinks.base import Outcome, OutcomeCallback, Status
from garmin_sync.timeutil import iso_z

SCHEMA = "garmin-sync/health-connect/1"
_KCAL_TO_JOULE = 4184.0
_SECONDS_PER_DAY = 86_400
_SLEEP_STAGE = {
    "deep": "STAGE_TYPE_DEEP",
    "light": "STAGE_TYPE_LIGHT",
    "rem": "STAGE_TYPE_REM",
    "awake": "STAGE_TYPE_AWAKE",
}
_EXERCISE_SCALARS = (
    ("distanceMeters", "distance_m"),
    ("energyKilocalories", "calories_kcal"),
    ("activeEnergyKilocalories", "active_calories_kcal"),
    ("averageHeartRate", "avg_hr"),
    ("maxHeartRate", "max_hr"),
    ("elevationGainMeters", "elevation_gain_m"),
    ("steps", "steps"),
    ("averageSpeedMetersPerSecond", "avg_speed_mps"),
    ("runVo2Max", "run_vo2_max"),
)


def _kg(grams: float) -> float:
    return round(grams / 1000, 4)


def _at(epoch_s: float) -> str:
    return iso_z(datetime.fromtimestamp(epoch_s, tz=UTC))


def _sleep(r: Record) -> dict[str, Any]:
    stages = [
        {"startTime": _at(s["start"]), "endTime": _at(s["end"]), "stage": _SLEEP_STAGE[s["stage"]]}
        for s in r.values.get("stages", [])
    ]
    return {"stages": stages}


def _heart_rate(r: Record) -> dict[str, Any]:
    return {
        "samples": [
            {"time": _at(ts), "beatsPerMinute": round(bpm)} for ts, bpm in r.values["samples"]
        ]
    }


def _exercise(r: Record) -> dict[str, Any]:
    v = r.values
    data: dict[str, Any] = {"exerciseType": v["hc_type"], "title": v["name"]}
    if v.get("description"):
        data["notes"] = v["description"]
    data.update({out: v[src] for out, src in _EXERCISE_SCALARS if src in v})
    if laps := v.get("laps"):
        data["laps"] = [
            {
                "startTime": _at(lap["start"]),
                "endTime": _at(lap["end"]),
                **({"lengthMeters": lap["distance_m"]} if "distance_m" in lap else {}),
            }
            for lap in laps
        ]
    return data


def _basal_rate(r: Record) -> dict[str, Any]:
    watts = r.values["kcal_per_day"] * _KCAL_TO_JOULE / _SECONDS_PER_DAY
    return {"basalMetabolicRateWatts": round(watts, 3)}


_DATA_BUILDERS: dict[Metric, Callable[[Record], dict[str, Any]]] = {
    Metric.SLEEP: _sleep,
    Metric.EXERCISE: _exercise,
    Metric.HEART_RATE: _heart_rate,
    Metric.RESTING_HEART_RATE: lambda r: {"beatsPerMinute": round(r.values["bpm"])},
    Metric.HRV: lambda r: {"heartRateVariabilityMillis": r.values["rmssd_ms"]},
    Metric.SPO2: lambda r: {"percentage": r.values["percent"]},
    Metric.RESPIRATORY_RATE: lambda r: {"rate": r.values["breaths_per_min"]},
    Metric.STEPS: lambda r: {"count": round(r.values["count"])},
    Metric.DISTANCE: lambda r: {"distanceMeters": r.values["meters"]},
    Metric.ACTIVE_CALORIES: lambda r: {"energyKilocalories": r.values["kcal"]},
    Metric.TOTAL_CALORIES: lambda r: {"energyKilocalories": r.values["kcal"]},
    Metric.BASAL_METABOLIC_RATE: _basal_rate,
    Metric.FLOORS: lambda r: {"floors": r.values["floors"]},
    Metric.SKIN_TEMPERATURE: lambda r: {
        "deltaCelsius": r.values["delta_c"],
        "measurementLocation": "WRIST",
    },
    Metric.WEIGHT: lambda r: {"weightKilograms": _kg(r.values["grams"])},
    Metric.BODY_FAT: lambda r: {"percentage": r.values["percent"]},
    Metric.BONE_MASS: lambda r: {"massKilograms": _kg(r.values["grams"])},
    Metric.BODY_WATER_MASS: lambda r: {"massKilograms": _kg(r.values["grams"])},
    Metric.BLOOD_PRESSURE: lambda r: {
        "systolicMillimetersOfMercury": r.values["systolic"],
        "diastolicMillimetersOfMercury": r.values["diastolic"],
    },
    Metric.HYDRATION: lambda r: {"volumeLiters": round(r.values["milliliters"] / 1000, 4)},
    Metric.VO2_MAX: lambda r: {
        "vo2MillilitersPerMinuteKilogram": r.values["ml_kg_min"],
        "measurementMethod": "OTHER",
    },
}


def to_health_connect(record: Record, version: int) -> dict[str, Any]:
    """One export line for ``record``."""
    line: dict[str, Any] = {
        "schema": SCHEMA,
        "recordType": TARGETS[record.metric].health_connect,
        "clientRecordId": record.external_id,
        "clientRecordVersion": version,
    }
    if record.is_instant:
        line["time"] = iso_z(record.start)
        line["zoneOffsetSeconds"] = record.utc_offset_s
    else:
        line["startTime"] = iso_z(record.start)
        line["startZoneOffsetSeconds"] = record.utc_offset_s
        line["endTime"] = iso_z(record.end)
        line["endZoneOffsetSeconds"] = record.end_offset
    line["data"] = _DATA_BUILDERS[record.metric](record)
    return line


class HealthConnectExporter:
    name = "health-connect"
    updates_in_place = True

    def __init__(self, exports_dir: Path, clock: Callable[[], datetime] = utc_now) -> None:
        self._exports_dir = exports_dir
        self._clock = clock

    def supports(self, metric: Metric) -> bool:
        return metric in HEALTH_CONNECT_METRICS

    def push(
        self,
        records: Sequence[Record],
        *,
        apply: bool,
        on_outcome: OutcomeCallback,
        max_writes: int | None = None,
    ) -> None:
        chosen = list(records) if max_writes is None else list(records)[:max_writes]
        for record in list(records)[len(chosen) :]:
            on_outcome(Outcome(record, Status.SKIPPED, "write limit reached for this run"))
        if not apply or not chosen:
            for record in chosen:
                on_outcome(Outcome(record, Status.WOULD_PUSH))
            return

        now = self._clock()
        version = int(now.timestamp())
        lines = [json.dumps(to_health_connect(r, version), separators=(",", ":")) for r in chosen]
        path = self._exports_dir / f"healthconnect-{now.strftime('%Y%m%dT%H%M%S%fZ')}.jsonl"
        write_private_text(path, "\n".join(lines) + "\n")
        # The file is complete and in place before anything is recorded as exported.
        for record in chosen:
            on_outcome(Outcome(record, Status.PUSHED, remote_ref=path.name))
