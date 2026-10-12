"""Build Google Health API ``DataPoint`` request bodies from normalized records.

Field names, units and formats follow the v4 reference (developers.google.com/health/
reference/rest/v4/users.dataTypes.dataPoints): int64 values are JSON strings, durations and
UTC offsets are ``"<seconds>s"`` strings, distances and elevations are millimetres, weights
grams. Every body carries a client-chosen data point name derived from the record's stable
id, which makes creating it idempotent.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from garmin_sync.coverage import TARGETS
from garmin_sync.models import Metric, Record
from garmin_sync.timeutil import iso_z

_MANUFACTURER = "Garmin"
_MM_PER_M = 1000
_CM_TO_MM = 10
_NAME_PREFIX = "garmin-"
_NAME_DIGEST_CHARS = 40  # "garmin-" + 40 hex = 47 chars, inside Google's 4-63 limit

# Our four zones are Garmin's five folded together; the key is Google's field name.
_ZONE_FIELDS = {
    "light": "lightTime",
    "moderate": "moderateTime",
    "vigorous": "vigorousTime",
    "peak": "peakTime",
}


class Unbuildable(Exception):
    """The record lacks something Google requires; message explains what."""


def data_point_id(record: Record) -> str:
    """Client-chosen data point id: lowercase hex under a fixed prefix, same every run."""
    digest = hashlib.sha256(record.external_id.encode("utf-8")).hexdigest()
    return f"{_NAME_PREFIX}{digest[:_NAME_DIGEST_CHARS]}"


def data_point_name(record: Record, data_type: str) -> str:
    return f"users/me/dataTypes/{data_type}/dataPoints/{data_point_id(record)}"


def _duration(seconds: float) -> str:
    whole = int(seconds)
    return f"{whole}s" if whole == seconds else f"{seconds:.3f}s"


def _offset(seconds: int) -> str:
    return f"{seconds}s"


def _from_epoch(epoch_s: int) -> datetime:
    return datetime.fromtimestamp(epoch_s, tz=UTC)


def _sample_time(r: Record) -> dict[str, str]:
    return {"physicalTime": iso_z(r.start), "utcOffset": _offset(r.utc_offset_s)}


def _interval(r: Record) -> dict[str, str]:
    return {
        "startTime": iso_z(r.start),
        "endTime": iso_z(r.end),
        "startUtcOffset": _offset(r.utc_offset_s),
        "endUtcOffset": _offset(r.end_offset),
    }


def _source(record: Record, recording_method: str | None, *, watch: bool) -> dict[str, Any]:
    device: dict[str, Any] = {"manufacturer": record.values.get("manufacturer", _MANUFACTURER)}
    if watch:
        device["formFactor"] = "WATCH"
    source: dict[str, Any] = {"device": device}
    if recording_method:
        source["recordingMethod"] = recording_method
    return source


# -- simple measurements --------------------------------------------------------------------


def _weight(r: Record) -> dict[str, Any]:
    return {"weight": {"sampleTime": _sample_time(r), "weightGrams": r.values["grams"]}}


def _body_fat(r: Record) -> dict[str, Any]:
    return {"bodyFat": {"sampleTime": _sample_time(r), "percentage": r.values["percent"]}}


def _hydration(r: Record) -> dict[str, Any]:
    return {
        "hydrationLog": {
            "interval": _interval(r),
            "amountConsumed": {
                "milliliters": r.values["milliliters"],
                "userProvidedUnit": "MILLILITER",
            },
        }
    }


def _sleep(r: Record) -> dict[str, Any]:
    stages = [
        {
            "startTime": iso_z(_from_epoch(s["start"])),
            "endTime": iso_z(_from_epoch(s["end"])),
            "startUtcOffset": _offset(r.utc_offset_s),
            "endUtcOffset": _offset(r.utc_offset_s),
            "type": s["stage"].upper(),
        }
        for s in r.values.get("stages", [])
    ]
    sleep: dict[str, Any] = {
        "interval": _interval(r),
        "type": "STAGES" if stages else "CLASSIC",
        "metadata": {"externalId": r.external_id},
    }
    if stages:
        sleep["stages"] = stages
    return {"sleep": sleep}


# -- exercise -------------------------------------------------------------------------------


def _mobility_metrics(m: dict[str, float]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if "ground_contact_ms" in m:
        out["avgGroundContactTimeDuration"] = _duration(m["ground_contact_ms"] / 1000)
    if "stride_cm" in m:
        out["avgStrideLengthMillimeters"] = str(round(m["stride_cm"] * _CM_TO_MM))
    if "vertical_oscillation_cm" in m:
        out["avgVerticalOscillationMillimeters"] = str(
            round(m["vertical_oscillation_cm"] * _CM_TO_MM)
        )
    if "vertical_ratio" in m:
        out["avgVerticalRatio"] = m["vertical_ratio"]
    if "cadence_spm" in m:
        out["avgCadenceStepsPerMinute"] = m["cadence_spm"]
    return out


def _metrics_summary(v: dict[str, Any]) -> dict[str, Any]:
    """The MetricsSummary shared by a whole exercise and by each lap (``v`` is the source dict)."""
    out: dict[str, Any] = {}
    if "calories_kcal" in v:
        out["caloriesKcal"] = v["calories_kcal"]
    if "distance_m" in v:
        out["distanceMillimeters"] = round(v["distance_m"] * _MM_PER_M)
    if "steps" in v:
        out["steps"] = str(round(v["steps"]))
    if "avg_hr" in v:
        out["averageHeartRateBeatsPerMinute"] = str(round(v["avg_hr"]))
    if "avg_speed_mps" in v:
        out["averageSpeedMillimetersPerSecond"] = round(v["avg_speed_mps"] * _MM_PER_M)
    if "avg_pace_s_per_m" in v:
        out["averagePaceSecondsPerMeter"] = v["avg_pace_s_per_m"]
    if "elevation_gain_m" in v:
        out["elevationGainMillimeters"] = round(v["elevation_gain_m"] * _MM_PER_M)
    if "run_vo2_max" in v:
        out["runVo2Max"] = v["run_vo2_max"]
    if "active_zone_minutes" in v:
        out["activeZoneMinutes"] = str(round(v["active_zone_minutes"]))
    if "swim_lengths" in v:
        out["totalSwimLengths"] = v["swim_lengths"]
    if zones := v.get("hr_zone_s"):
        out["heartRateZoneDurations"] = {
            _ZONE_FIELDS[name]: _duration(seconds) for name, seconds in zones.items()
        }
    if mobility := _mobility_metrics(v.get("mobility", {})):
        out["mobilityMetrics"] = mobility
    return out


def _split(r: Record, lap: dict[str, Any]) -> dict[str, Any]:
    return {
        "startTime": iso_z(_from_epoch(lap["start"])),
        "endTime": iso_z(_from_epoch(lap["end"])),
        "startUtcOffset": _offset(r.utc_offset_s),
        "endUtcOffset": _offset(r.utc_offset_s),
        "splitType": "MANUAL",
        "metricsSummary": _metrics_summary(lap),
    }


def _notes(v: dict[str, Any]) -> str:
    description = v.get("description")
    return f"{v['name']}\n{description}" if description else v["name"]


def _exercise(r: Record) -> dict[str, Any]:
    v = r.values
    exercise: dict[str, Any] = {
        "interval": _interval(r),  # elapsed time, pauses included
        "exerciseType": v["google_type"],
        "displayName": v["name"],
        "notes": _notes(v),
        "activeDuration": _duration(v["active_s"]),  # timer time, pauses excluded
        "metricsSummary": _metrics_summary(v),
        "exerciseMetadata": {"hasGps": bool(v.get("has_gps"))},
    }
    if laps := v.get("laps"):
        # `splits` holds Google's own default 1 km / 1 mile splits; recorded laps go here.
        exercise["splitSummaries"] = [_split(r, lap) for lap in laps]
    if events := v.get("events"):
        exercise["exerciseEvents"] = [
            {
                "eventTime": iso_z(_from_epoch(e["time"])),
                "eventUtcOffset": _offset(r.utc_offset_s),
                "exerciseEventType": e["type"],
            }
            for e in events
        ]
    return {"exercise": exercise}


def _recording_method(record: Record) -> str | None:
    match record.metric:
        case Metric.SLEEP:
            return "PASSIVELY_MEASURED"
        case Metric.EXERCISE:
            return "MANUAL" if record.values.get("manual") else "ACTIVELY_MEASURED"
        case _:
            return "MANUAL" if record.values.get("source_type") == "MANUAL" else None


_BUILDERS: dict[Metric, tuple[Callable[[Record], dict[str, Any]], bool]] = {
    # metric: (payload builder, device is a watch)
    Metric.WEIGHT: (_weight, False),
    Metric.BODY_FAT: (_body_fat, False),
    Metric.HYDRATION: (_hydration, False),
    Metric.SLEEP: (_sleep, True),
    Metric.EXERCISE: (_exercise, True),
}


def build_data_point(record: Record) -> tuple[str, dict[str, Any]]:
    """Return ``(data type id, request body)`` for ``record``.

    Raises :class:`Unbuildable` for metrics Google cannot accept or records missing
    required content.
    """
    target = TARGETS[record.metric]
    if not target.google_writable or target.google_type is None or record.metric not in _BUILDERS:
        raise Unbuildable(f"{record.metric.value} is not writable via the Google Health API")
    builder, watch = _BUILDERS[record.metric]
    try:
        body = builder(record)
    except KeyError as exc:
        raise Unbuildable(f"record is missing {exc.args[0]!r}") from None
    except (TypeError, ValueError, OverflowError, AttributeError) as exc:
        raise Unbuildable(f"record has unusable values ({type(exc).__name__})") from None
    body["name"] = data_point_name(record, target.google_type)
    body["dataSource"] = _source(record, _recording_method(record), watch=watch)
    return target.google_type, body
