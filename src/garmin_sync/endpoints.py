"""Registry of Garmin Connect endpoints archived by ``pull``.

The archive name of an endpoint is its python-garminconnect method name without the
``get_`` prefix. Method names and signatures were verified against garminconnect 0.3.17.

Scopes:
  DAY       one call per calendar day, argument ``YYYY-MM-DD``
  RANGE     one call per window of ``span_days`` days, arguments ``(start, end)``
  SNAPSHOT  no date argument; archived once per pull day (profile, devices, records...)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Scope(StrEnum):
    DAY = "day"
    RANGE = "range"
    SNAPSHOT = "snapshot"


@dataclass(frozen=True)
class Endpoint:
    name: str
    method: str
    scope: Scope
    span_days: int = 1


def _day(*methods: str) -> tuple[Endpoint, ...]:
    return tuple(Endpoint(m.removeprefix("get_"), m, Scope.DAY) for m in methods)


def _range(span_days: int, *methods: str) -> tuple[Endpoint, ...]:
    return tuple(Endpoint(m.removeprefix("get_"), m, Scope.RANGE, span_days) for m in methods)


def _snapshot(*methods: str) -> tuple[Endpoint, ...]:
    return tuple(Endpoint(m.removeprefix("get_"), m, Scope.SNAPSHOT) for m in methods)


ENDPOINTS: tuple[Endpoint, ...] = (
    *_day(
        "get_user_summary",
        "get_steps_data",
        "get_heart_rates",
        "get_rhr_day",
        "get_sleep_data",
        "get_hrv_data",
        "get_spo2_data",
        "get_respiration_data",
        "get_stress_data",
        "get_all_day_events",
        "get_body_battery_events",
        "get_hydration_data",
        "get_intensity_minutes_data",
        "get_floors",
        "get_training_readiness",
        "get_morning_training_readiness",
        "get_training_status",
        "get_training_four_week_load_balance",
        "get_max_metrics",
        "get_fitnessage_data",
        "get_lifestyle_logging_data",
        "get_nutrition_daily_food_log",
        "get_nutrition_daily_meals",
        "get_menstrual_data_for_date",
    ),
    *_range(
        30,
        "get_weigh_ins",
        "get_body_composition",
        "get_blood_pressure",
        "get_body_battery",
        "get_endurance_score",
        "get_hill_score",
    ),
    *_snapshot(
        "get_user_profile",
        "get_userprofile_settings",
        "get_devices",
        "get_heart_rate_zones",
        "get_personal_record",
        "get_cycling_ftp",
        "get_lactate_threshold",
        "get_power_zones",
        "get_goals",
        "get_earned_badges",
        "get_primary_training_device",
        "get_activity_types",
        "get_training_plans",
        "get_golf_summary",
        "get_golf_user_stats",
    ),
)

# Per-activity detail calls: (archive name, garminconnect method). Keyed by activity id.
ACTIVITY_SUMMARY = "activity_summary"
ACTIVITY_DETAILS: tuple[tuple[str, str], ...] = (
    ("activity", "get_activity"),
    ("activity_details", "get_activity_details"),
    ("activity_splits", "get_activity_splits"),
    ("activity_hr_zones", "get_activity_hr_in_timezones"),
    ("activity_weather", "get_activity_weather"),
    ("activity_gear", "get_activity_gear"),
)
# Only fetched for strength activities (other sports return an error).
ACTIVITY_EXERCISE_SETS = ("activity_exercise_sets", "get_activity_exercise_sets")

# What the Google writes are built from. If one of these fails, what would be written is wrong or
# missing, so a failure here makes a run "incomplete"; a failing optional endpoint (golf, menstrual
# data, a feature the device lacks) does not.
GOOGLE_WRITE_ENDPOINTS: tuple[str, ...] = (
    "user_summary",
    "sleep_data",
    "weigh_ins",
    "hydration_data",
)
GOOGLE_WRITE_ACTIVITY_DETAILS: tuple[str, ...] = (
    "activity",
    "activity_splits",
    "activity_hr_zones",
)
# (Heart-rate zones are a fallback for a field the summary usually has, so they are not essential.)
ESSENTIAL: frozenset[str] = frozenset(
    (*GOOGLE_WRITE_ENDPOINTS, ACTIVITY_SUMMARY, "activity", "activity_splits")
)

_BY_NAME = {e.name: e for e in ENDPOINTS}


def endpoint_names() -> list[str]:
    return [e.name for e in ENDPOINTS]


def get_endpoint(name: str) -> Endpoint:
    try:
        return _BY_NAME[name]
    except KeyError:
        raise KeyError(f"unknown endpoint {name!r}") from None
