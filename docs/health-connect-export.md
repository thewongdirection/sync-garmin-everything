# Health Connect export format (`garmin-sync/health-connect/1`)

Health Connect is an on-device Android store with no cloud API. `garmin-sync push --sink
health-connect --apply` writes `~/.garmin-sync/exports/healthconnect-<UTC stamp with microseconds>.jsonl` (mode 0600)
for a companion app to insert through the Health Connect SDK. **No such app exists in this repo yet.**
This document is the contract it should implement.

One JSON object per line, UTF-8.

## Envelope

| Field | Type | Meaning |
|---|---|---|
| `schema` | string | Always `garmin-sync/health-connect/1`. Reject unknown values. |
| `recordType` | string | Health Connect record class, e.g. `WeightRecord`. |
| `clientRecordId` | string | Stable id (`garmin:<metric>:<key>`). Use as `Metadata.clientRecordId`. |
| `clientRecordVersion` | integer | Export time (epoch s). Use as `Metadata.clientRecordVersion`; a re-import with a higher version replaces the record, so re-exports never duplicate. |
| `time`, `zoneOffsetSeconds` | RFC 3339 UTC / integer | Instantaneous records. |
| `startTime`, `startZoneOffsetSeconds`, `endTime`, `endZoneOffsetSeconds` | same | Interval records. |
| `data` | object | Type-specific fields below. |

Timestamps are UTC (`...Z`); offsets are the user's local UTC offset in seconds at that time.

## `data` by `recordType`

| recordType | `data` fields |
|---|---|
| `SleepSessionRecord` | `stages`: `[{startTime, endTime, stage}]`, `stage` ∈ `STAGE_TYPE_DEEP/LIGHT/REM/AWAKE` (REM only for watches that measure it) |
| `ExerciseSessionRecord` | `exerciseType` (suffix of `ExerciseSessionRecord.EXERCISE_TYPE_*`, e.g. `RUNNING`; unknown → `OTHER_WORKOUT`), `title`, optional `notes`, `distanceMeters`, `energyKilocalories`, `activeEnergyKilocalories`, `averageHeartRate`, `maxHeartRate`, `elevationGainMeters`, `steps`, `averageSpeedMetersPerSecond`, `runVo2Max`, and `laps`: `[{startTime, endTime, lengthMeters?}]`. The interval spans elapsed time, pauses included |
| `HeartRateRecord` | `samples`: `[{time, beatsPerMinute}]` (one record per hour of each Garmin day, counted from that day's own start so ids never collide) |
| `RestingHeartRateRecord` | `beatsPerMinute` |
| `HeartRateVariabilityRmssdRecord` | `heartRateVariabilityMillis` |
| `OxygenSaturationRecord` | `percentage` (overnight readings, spot checks and, in all-day mode, hourly averages; ids ending `:hourly:<epoch>` are averages) |
| `RespiratoryRateRecord` | `rate` (breaths/min) |
| `StepsRecord` | `count` |
| `DistanceRecord` | `distanceMeters` |
| `ActiveCaloriesBurnedRecord`, `TotalCaloriesBurnedRecord` | `energyKilocalories` |
| `BasalMetabolicRateRecord` | `basalMetabolicRateWatts` (Garmin's kcal/day × 4184 ÷ 86400) |
| `SkinTemperatureRecord` | `deltaCelsius` (overnight deviation from baseline; one delta for the sleep interval), `measurementLocation` (`WRIST`) |
| `FloorsClimbedRecord` | `floors` |
| `WeightRecord` | `weightKilograms` |
| `BodyFatRecord` | `percentage` |
| `BoneMassRecord`, `BodyWaterMassRecord` | `massKilograms` |
| `BloodPressureRecord` | `systolicMillimetersOfMercury`, `diastolicMillimetersOfMercury` |
| `HydrationRecord` | `volumeLiters` |
| `Vo2MaxRecord` | `vo2MillilitersPerMinuteKilogram`, `measurementMethod` (`OTHER`) |

## Importer guidance

* Declare the matching `WRITE_*` permissions and complete the Play Console health declaration.
* Insert via `insertRecords` with `Metadata(clientRecordId, clientRecordVersion, ...)`; set the
  recording method to *automatically recorded* / device type *watch* as appropriate.
* The exercise record carries the session's summary values and laps. Create sibling `DistanceRecord` /
  `TotalCaloriesBurnedRecord` / `ActiveCaloriesBurnedRecord` entries over the same interval if you want them
  attributed to the session. GPS routes and per-second series are not exported yet (they need the FIT files).
* Treat the file as untrusted input: validate `schema`, numeric ranges and record counts.
