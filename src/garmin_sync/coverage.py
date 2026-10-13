"""Where each normalized metric can go. Single source of truth for sink capabilities.

``google_writable`` is True only for Google Health API data types whose reference lists a
``create`` operation (verified against developers.google.com/health/data-types on
2026-10-12). Everything else is read-only there, so it is routed to the Health Connect
export, or stays in the local archive.
"""

from __future__ import annotations

from dataclasses import dataclass

from garmin_sync.models import Metric


@dataclass(frozen=True)
class Target:
    google_type: str | None  # Google Health API data type id, if the type exists there
    google_writable: bool  # True only when the API documents `create` for it
    health_connect: str  # Android Health Connect record class


TARGETS: dict[Metric, Target] = {
    Metric.SLEEP: Target("sleep", True, "SleepSessionRecord"),
    Metric.EXERCISE: Target("exercise", True, "ExerciseSessionRecord"),
    Metric.WEIGHT: Target("weight", True, "WeightRecord"),
    Metric.BODY_FAT: Target("body-fat", True, "BodyFatRecord"),
    Metric.HYDRATION: Target("hydration-log", True, "HydrationRecord"),
    Metric.STEPS: Target("steps", False, "StepsRecord"),
    Metric.DISTANCE: Target("distance", False, "DistanceRecord"),
    Metric.ACTIVE_CALORIES: Target("active-energy-burned", False, "ActiveCaloriesBurnedRecord"),
    Metric.TOTAL_CALORIES: Target("total-calories", False, "TotalCaloriesBurnedRecord"),
    Metric.BASAL_METABOLIC_RATE: Target("basal-energy-burned", False, "BasalMetabolicRateRecord"),
    Metric.SKIN_TEMPERATURE: Target(
        "daily-sleep-temperature-derivations", False, "SkinTemperatureRecord"
    ),
    Metric.FLOORS: Target("floors", False, "FloorsClimbedRecord"),
    Metric.HEART_RATE: Target("heart-rate", False, "HeartRateRecord"),
    Metric.RESTING_HEART_RATE: Target("daily-resting-heart-rate", False, "RestingHeartRateRecord"),
    Metric.HRV: Target("heart-rate-variability", False, "HeartRateVariabilityRmssdRecord"),
    Metric.SPO2: Target("oxygen-saturation", False, "OxygenSaturationRecord"),
    Metric.RESPIRATORY_RATE: Target(
        "respiratory-rate-sleep-summary", False, "RespiratoryRateRecord"
    ),
    Metric.BONE_MASS: Target(None, False, "BoneMassRecord"),
    Metric.BODY_WATER_MASS: Target(None, False, "BodyWaterMassRecord"),
    Metric.BLOOD_PRESSURE: Target(None, False, "BloodPressureRecord"),
    Metric.VO2_MAX: Target("vo2-max", False, "Vo2MaxRecord"),
}

GOOGLE_WRITABLE: frozenset[Metric] = frozenset(m for m, t in TARGETS.items() if t.google_writable)
HEALTH_CONNECT_METRICS: frozenset[Metric] = frozenset(TARGETS)
