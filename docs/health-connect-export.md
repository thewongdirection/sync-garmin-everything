> **Draft specification.** Written for the first-draft exporter, which is not in this repository yet. Field names follow the Health Connect
> SDK, but record shapes (for example heart-rate series and sleep stages) will be revised with the Phase 1 rebuild and the field map in
> [field-map/FIELD-MAP.md](field-map/FIELD-MAP.md).

# Health Connect export format (`garmin-sync/health-connect/1`)

Health Connect is an on-device Android store with no cloud API. `garmin-sync push --sink
health-connect --apply` writes `~/.garmin-sync/exports/healthconnect-<UTC stamp>.jsonl` (mode 0600)
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
| `SleepSessionRecord` | `stages`: `[{startTime, endTime, stage}]`, `stage` ∈ `STAGE_TYPE_DEEP/LIGHT/REM/AWAKE` |
| `ExerciseSessionRecord` | `exerciseType` (suffix of `ExerciseSessionRecord.EXERCISE_TYPE_*`, e.g. `RUNNING`; unknown → `OTHER_WORKOUT`), `title`, optional `distanceMeters`, `energyKilocalories`, `averageHeartRate`, `maxHeartRate`, `elevationGainMeters`, `steps` |
| `HeartRateRecord` | `samples`: `[{time, beatsPerMinute}]` (one record per UTC hour) |
| `RestingHeartRateRecord` | `beatsPerMinute` |
| `HeartRateVariabilityRmssdRecord` | `heartRateVariabilityMillis` |
| `OxygenSaturationRecord` | `percentage` |
| `RespiratoryRateRecord` | `rate` (breaths/min) |
| `StepsRecord` | `count` |
| `DistanceRecord` | `distanceMeters` |
| `ActiveCaloriesBurnedRecord`, `TotalCaloriesBurnedRecord` | `energyKilocalories` |
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
* The exercise record carries summary values only; create sibling `DistanceRecord` /
  `TotalCaloriesBurnedRecord` entries over the same interval if you want them attributed to the session.
* Treat the file as untrusted input: validate `schema`, numeric ranges and record counts.
