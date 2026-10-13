"""Garmin activity ``typeKey`` -> (Google ExerciseType, Health Connect exercise type name).

Google names come from the ``ExerciseType`` enum in the Google Health API reference
(note its spelling ``PICKELBALL``). Health Connect names are the symbolic suffix of
``ExerciseSessionRecord.EXERCISE_TYPE_*``; the export carries the name and the
consumer maps it to the constant, falling back to ``OTHER_WORKOUT``.
"""

from __future__ import annotations

GOOGLE_FALLBACK = "OTHER"
HEALTH_CONNECT_FALLBACK = "OTHER_WORKOUT"

ACTIVITY_TYPES: dict[str, tuple[str, str]] = {
    "running": ("RUNNING", "RUNNING"),
    "track_running": ("RUNNING", "RUNNING"),
    "trail_running": ("TRAIL_RUN", "RUNNING"),
    "treadmill_running": ("TREADMILL", "RUNNING_TREADMILL"),
    "walking": ("WALKING", "WALKING"),
    "casual_walking": ("WALKING", "WALKING"),
    "speed_walking": ("POWER_WALKING", "WALKING"),
    "hiking": ("HIKING", "HIKING"),
    "cycling": ("BIKING", "BIKING"),
    "road_biking": ("BIKING", "BIKING"),
    "gravel_cycling": ("BIKING", "BIKING"),
    "mountain_biking": ("MOUNTAIN_BIKE", "BIKING"),
    "indoor_cycling": ("STATIONARY_BIKE", "BIKING_STATIONARY"),
    "e_bike_fitness": ("ELECTRIC_BIKE", "BIKING"),
    "e_bike_mountain": ("ELECTRIC_BIKE", "BIKING"),
    "lap_swimming": ("SWIMMING_POOL", "SWIMMING_POOL"),
    "open_water_swimming": ("SWIMMING_OPEN_WATER", "SWIMMING_OPEN_WATER"),
    "strength_training": ("STRENGTH_TRAINING", "STRENGTH_TRAINING"),
    "yoga": ("YOGA", "YOGA"),
    "pilates": ("PILATES", "PILATES"),
    "elliptical": ("ELLIPTICAL", "ELLIPTICAL"),
    "stair_climbing": ("STAIRCLIMBER", "STAIR_CLIMBING"),
    "rowing": ("ROWING", "ROWING"),
    "indoor_rowing": ("ROWING_MACHINE", "ROWING_MACHINE"),
    "hiit": ("HIIT", "HIGH_INTENSITY_INTERVAL_TRAINING"),
    "cardio": ("CARDIO_WORKOUT", HEALTH_CONNECT_FALLBACK),
    "indoor_cardio": ("CARDIO_WORKOUT", HEALTH_CONNECT_FALLBACK),
    "resort_skiing_snowboarding_ws": ("SKIING", "SKIING"),
    "cross_country_skiing_ws": ("CROSS_COUNTRY_SKI", "SKIING"),
    "snowshoe_ws": ("SNOWSHOEING", "SNOWSHOEING"),
    "stand_up_paddleboarding_v2": ("PADDLEBOARDING", "PADDLING"),
    "kayaking_v2": ("KAYAKING", "PADDLING"),
    "golf": ("GOLF", "GOLF"),
    "tennis_v2": ("TENNIS", "TENNIS"),
    "pickleball": ("PICKELBALL", HEALTH_CONNECT_FALLBACK),
    "multi_sport": ("MULTISPORT", HEALTH_CONNECT_FALLBACK),
    "inline_skating": ("ROLLERBLADING", HEALTH_CONNECT_FALLBACK),
    "meditation": ("MEDITATE", HEALTH_CONNECT_FALLBACK),
    "breathwork": ("MEDITATE", HEALTH_CONNECT_FALLBACK),
}


def map_activity_type(type_key: object) -> tuple[str, str]:
    """Return ``(google_type, health_connect_type)`` for a Garmin ``typeKey``."""
    if isinstance(type_key, str):
        return ACTIVITY_TYPES.get(type_key, (GOOGLE_FALLBACK, HEALTH_CONNECT_FALLBACK))
    return GOOGLE_FALLBACK, HEALTH_CONNECT_FALLBACK
