"""Vitals: heart rate, resting HR, HRV, SpO2, respiration."""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import Any

from garmin_sync.models import Metric, Record
from garmin_sync.normalize.registry import Context, normalizer
from garmin_sync.normalize.util import (
    as_dict,
    as_list,
    from_epoch_ms,
    in_range,
    instant,
    num,
    pairs,
    whole_seconds,
)
from garmin_sync.timeutil import day_bounds, offset_seconds, parse_day, parse_gmt

_SECONDS_PER_HOUR = 3600
_DEFAULT_SPO2_COLUMNS = {"timestamp": 0, "spo2Reading": 1}


@normalizer("heart_rates", Metric.HEART_RATE, Metric.RESTING_HEART_RATE)
def normalize_heart_rates(key: str, payload: Any, ctx: Context) -> list[Record]:
    data = as_dict(payload)
    offset = offset_seconds(data.get("startTimestampLocal"), data.get("startTimestampGMT"))
    if offset is None:
        offset = ctx.offset_for(key)
    day_start = parse_gmt(data.get("startTimestampGMT")) or day_bounds(parse_day(key), offset)[0]
    anchor = whole_seconds(day_start)

    # Hourly buckets, counted from this day's own start. A bucket therefore never straddles
    # two Garmin days, so half-hour time zones cannot give two days the same bucket id.
    buckets: dict[int, list[list[float]]] = defaultdict(list)
    for epoch_ms, bpm in pairs(data.get("heartRateValues")):
        if in_range(bpm, 20, 250) is not None:
            epoch_s = int(epoch_ms // 1000)
            buckets[(epoch_s - anchor) // _SECONDS_PER_HOUR].append([epoch_s, bpm])

    records = []
    for index, samples in sorted(buckets.items()):
        samples.sort()
        first = from_epoch_ms(samples[0][0] * 1000)
        last = from_epoch_ms(samples[-1][0] * 1000)
        if first is None or last is None:
            continue
        records.append(
            Record(
                Metric.HEART_RATE,
                f"garmin:heart_rate:{anchor + index * _SECONDS_PER_HOUR}",
                first,
                last
                + timedelta(seconds=1),  # interval end is exclusive; a lone sample still has length
                offset,
                {"samples": samples},
            )
        )

    resting = in_range(num(data.get("restingHeartRate")), 20, 150)
    if resting is not None:
        records.append(
            Record(
                Metric.RESTING_HEART_RATE,
                f"garmin:resting_heart_rate:{key}",
                day_start,
                day_start,
                offset,
                {"bpm": resting},
            )
        )
    return records


@normalizer("hrv_data", Metric.HRV)
def normalize_hrv(key: str, payload: Any, ctx: Context) -> list[Record]:
    records = []
    for item in as_list(as_dict(payload).get("hrvReadings")):
        reading = as_dict(item)
        moment = parse_gmt(reading.get("readingTimeGMT"))
        value = in_range(num(reading.get("hrvValue")), 1, 500)
        if moment is None or value is None:
            continue
        offset = offset_seconds(reading.get("readingTimeLocal"), reading.get("readingTimeGMT"))
        records.append(
            instant(
                Metric.HRV,
                moment,
                offset if offset is not None else ctx.offset_for(key),
                {"rmssd_ms": value},
            )
        )
    return records


def _spo2_columns(descriptors: Any) -> dict[str, int]:
    """Column positions of an ``spO2SingleValues`` row, from its descriptor list if present."""
    columns = dict(_DEFAULT_SPO2_COLUMNS)
    for item in as_list(descriptors):
        entry = as_dict(item)
        name, index = (
            entry.get("spo2ValueDescriptorKey"),
            num(entry.get("spo2ValueDescriptorIndex")),
        )
        if name in columns and index is not None and index >= 0:
            columns[name] = int(index)
    return columns


@normalizer("spo2_data", Metric.SPO2)
def normalize_spo2(key: str, payload: Any, ctx: Context) -> list[Record]:
    """Spot checks (``spO2SingleValues``) and, in all-day mode, hourly averages.

    The library's endpoint does not return the per-minute series; see docs/PLAN.md.
    """
    data = as_dict(payload)
    if data and not ({"spO2SingleValues", "spO2HourlyAverages"} & data.keys()):
        ctx.diag.warn("spo2_data", "neither spO2SingleValues nor spO2HourlyAverages in payload")
    offset = ctx.offset_for(key)
    records = []

    columns = _spo2_columns(data.get("spO2ValueDescriptorsDTOList"))
    for row in as_list(data.get("spO2SingleValues")):
        if not isinstance(row, list | tuple) or len(row) <= max(columns.values()):
            continue
        epoch_ms, percent = num(row[columns["timestamp"]]), num(row[columns["spo2Reading"]])
        moment = from_epoch_ms(epoch_ms) if epoch_ms is not None else None
        value = in_range(percent, 50, 100)
        if moment is not None and value is not None:
            records.append(instant(Metric.SPO2, moment, offset, {"percent": value}))

    for epoch_ms, percent in pairs(data.get("spO2HourlyAverages")):
        moment, value = from_epoch_ms(epoch_ms), in_range(percent, 50, 100)
        if moment is not None and value is not None:
            records.append(
                Record(
                    Metric.SPO2,
                    f"garmin:spo2:hourly:{whole_seconds(moment)}",
                    moment,
                    moment,
                    offset,
                    {"percent": value, "kind": "hourly_average"},
                )
            )
    return records


@normalizer("respiration_data", Metric.RESPIRATORY_RATE)
def normalize_respiration(key: str, payload: Any, ctx: Context) -> list[Record]:
    data = as_dict(payload)
    if data and "respirationValuesArray" not in data:
        ctx.diag.warn("respiration_data", "no respirationValuesArray in payload (shape changed?)")
    offset = ctx.offset_for(key)
    records = []
    for epoch_ms, rate in pairs(data.get("respirationValuesArray")):
        moment, value = from_epoch_ms(epoch_ms), in_range(rate, 3, 70)  # -1 marks an invalid sample
        if moment is not None and value is not None:
            records.append(
                instant(Metric.RESPIRATORY_RATE, moment, offset, {"breaths_per_min": value})
            )
    return records
