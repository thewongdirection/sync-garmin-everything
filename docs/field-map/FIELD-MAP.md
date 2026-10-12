# Field map: Garmin Connect -> Google Health API / Health Connect

_Generated from the CSVs next to this file by `tools/field_map/generate_docs.py` (see `docs/field-map/README.md`). The CSVs contain every field._

## 1. Numbers

| | Count |
|---|---|
| Garmin read methods + FIT message types inventoried | 158 |
| Garmin fields documented (with cited evidence) | 4567 |
| ...of which observed in recorded real responses / read by third-party parsers / inferred | 1923 / 2253 / 391 |
| Methods with no field evidence anywhere (discoverable only from your account) | 10 |
| Google Health API payload fields (v4) | 1082 |

**Garmin fields by destination**

| Destination | Garmin fields |
|---|---|
| Google Health API write **and** Health Connect export | 77 |
| Google Health API write | 92 |
| Health Connect export only (Google cannot be written) | 102 |
| Opt-in only (sensitive or changes your Google settings) | 18 |
| Local archive only | 3550 |
| Housekeeping (ids, containers, social) | 728 |

**Google fields by status**

| Status | Google fields |
|---|---|
| Filled from a Garmin field | 61 |
| Filled by derivation or a constant (e.g. UTC offsets) | 19 |
| Fillable, opt-in only | 18 |
| Writable, but Garmin has nothing to put there | 66 |
| Set by Google (cannot be written) | 620 |
| Belongs to a data type the API does not let third parties write | 211 |

## 2. Google Health API: what we write, field by field

Only these data types accept `create` (verified in the Discovery document and the data-types page). Each table lists every field that is not output-only; civil-time echo fields are omitted because Google derives them.

### `exercise`  (47 of 80 writable fields filled)

| Google field | Type | Status | Garmin source / note |
|---|---|---|---|
| `exercise.activeDuration` | string:google-duration | FILLED | activity: duration |
| `exercise.displayName` | string | FILLED | activity: activityName |
| `exercise.exerciseEvents[].eventTime` | string:google-datetime | FILLED | get_activity_splits:eventDTOs[].sectionTypeDTO.key<br> get_activity_splits:eventDTOs[].sectionTypeDTO.sectionTypeKey<br> get_activity_splits:eventDTOs[].startTimeGMT |
| `exercise.exerciseEvents[].eventUtcOffset` | string:google-duration | FILLED (derived/constant) | derived: user's UTC offset (Local - GMT) from the same Garmin record |
| `exercise.exerciseEvents[].exerciseEventType` | string (EXERCISE_EVENT_TYPE_UNSPECIFIED, START, STOP, PAUSE, RESUME, AUTO_PAUSE, AUTO_RESUME) | FILLED | get_activity_splits:eventDTOs[].sectionTypeDTO.key<br> get_activity_splits:eventDTOs[].sectionTypeDTO.sectionTypeKey<br> get_activity_splits:eventDTOs[].startTimeGMT |
| `exercise.exerciseMetadata.hasGps` | boolean | FILLED | activity: hasPolyline |
| `exercise.exerciseMetadata.poolLengthMillimeters` | string:int64 | FILLED | activity: poolLength |
| `exercise.exerciseType` | string (182 values) | FILLED | activity: activityType.typeKey<br> activity: activityTypeDTO.typeKey |
| `exercise.interval.endTime` | string:google-datetime | FILLED | activity: elapsedDuration |
| `exercise.interval.endUtcOffset` | string:google-duration | FILLED | activity: startTimeLocal |
| `exercise.interval.startTime` | string:google-datetime | FILLED | activity: startTimeGMT |
| `exercise.interval.startUtcOffset` | string:google-duration | FILLED | activity: startTimeLocal |
| `exercise.metricsSummary.activeZoneMinutes` | string:int64 | FILLED | activity: moderateIntensityMinutes<br> activity: vigorousIntensityMinutes |
| `exercise.metricsSummary.averageHeartRateBeatsPerMinute` | string:int64 | FILLED | activity: averageHR |
| `exercise.metricsSummary.averagePaceSecondsPerMeter` | number:double | FILLED | activity: averageSpeed |
| `exercise.metricsSummary.averageSpeedMillimetersPerSecond` | number:double | FILLED | activity: averageSpeed |
| `exercise.metricsSummary.caloriesKcal` | number:double | FILLED | activity: calories |
| `exercise.metricsSummary.distanceMillimeters` | number:double | FILLED | activity: distance |
| `exercise.metricsSummary.elevationGainMillimeters` | number:double | FILLED | activity: elevationGain |
| `exercise.metricsSummary.heartRateZoneDurations.lightTime` | string:google-duration | FILLED | activity: hrTimeInZone_1<br> activity: hrTimeInZone_2 |
| `exercise.metricsSummary.heartRateZoneDurations.moderateTime` | string:google-duration | FILLED | activity: hrTimeInZone_3 |
| `exercise.metricsSummary.heartRateZoneDurations.peakTime` | string:google-duration | FILLED | activity: hrTimeInZone_5 |
| `exercise.metricsSummary.heartRateZoneDurations.vigorousTime` | string:google-duration | FILLED | activity: hrTimeInZone_4 |
| `exercise.metricsSummary.mobilityMetrics.avgCadenceStepsPerMinute` | number:double | FILLED | activity: averageRunCadence<br> activity: averageRunningCadenceInStepsPerMinute |
| `exercise.metricsSummary.mobilityMetrics.avgGroundContactTimeDuration` | string:google-duration | FILLED | activity: avgGroundContactTime<br> activity: groundContactTime |
| `exercise.metricsSummary.mobilityMetrics.avgStrideLengthMillimeters` | string:int64 | FILLED | activity: avgStrideLength<br> activity: strideLength |
| `exercise.metricsSummary.mobilityMetrics.avgVerticalOscillationMillimeters` | string:int64 | FILLED | activity: avgVerticalOscillation<br> activity: verticalOscillation |
| `exercise.metricsSummary.mobilityMetrics.avgVerticalRatio` | number:double | FILLED | activity: avgVerticalRatio<br> activity: verticalRatio |
| `exercise.metricsSummary.runVo2Max` | number:double | FILLED | activity: vO2MaxValue |
| `exercise.metricsSummary.steps` | string:int64 | FILLED | activity: steps |
| `exercise.metricsSummary.totalSwimLengths` | number:double | FILLED | activity: activeLengths |
| `exercise.notes` | string | FILLED | activity: activityName<br> activity: description |
| `exercise.splitSummaries[].endTime` | string:google-datetime | FILLED | get_activity_splits:lapDTOs[].elapsedDuration |
| `exercise.splitSummaries[].endUtcOffset` | string:google-duration | FILLED (derived/constant) | derived: user's UTC offset (Local - GMT) from the same Garmin record |
| `exercise.splitSummaries[].metricsSummary.activeZoneMinutes` | string:int64 | writable, no Garmin source |  |
| `exercise.splitSummaries[].metricsSummary.averageHeartRateBeatsPerMinute` | string:int64 | FILLED | get_activity_splits:lapDTOs[].averageHR |
| `exercise.splitSummaries[].metricsSummary.averagePaceSecondsPerMeter` | number:double | writable, no Garmin source |  |
| `exercise.splitSummaries[].metricsSummary.averageSpeedMillimetersPerSecond` | number:double | FILLED | get_activity_splits:lapDTOs[].averageSpeed |
| `exercise.splitSummaries[].metricsSummary.caloriesKcal` | number:double | FILLED | get_activity_splits:lapDTOs[].calories |
| `exercise.splitSummaries[].metricsSummary.distanceMillimeters` | number:double | FILLED | get_activity_splits:lapDTOs[].distance |
| `exercise.splitSummaries[].metricsSummary.elevationGainMillimeters` | number:double | FILLED | get_activity_splits:lapDTOs[].elevationGain |
| `exercise.splitSummaries[].metricsSummary.heartRateZoneDurations.lightTime` | string:google-duration | writable, no Garmin source |  |
| `exercise.splitSummaries[].metricsSummary.heartRateZoneDurations.moderateTime` | string:google-duration | writable, no Garmin source |  |
| `exercise.splitSummaries[].metricsSummary.heartRateZoneDurations.peakTime` | string:google-duration | writable, no Garmin source |  |
| `exercise.splitSummaries[].metricsSummary.heartRateZoneDurations.vigorousTime` | string:google-duration | writable, no Garmin source |  |
| `exercise.splitSummaries[].metricsSummary.mobilityMetrics.avgCadenceStepsPerMinute` | number:double | FILLED | get_activity_splits:lapDTOs[].averageRunCadence |
| `exercise.splitSummaries[].metricsSummary.mobilityMetrics.avgGroundContactTimeDuration` | string:google-duration | FILLED | get_activity_splits:lapDTOs[].groundContactTime |
| `exercise.splitSummaries[].metricsSummary.mobilityMetrics.avgStrideLengthMillimeters` | string:int64 | FILLED | get_activity_splits:lapDTOs[].strideLength |
| `exercise.splitSummaries[].metricsSummary.mobilityMetrics.avgVerticalOscillationMillimeters` | string:int64 | FILLED | get_activity_splits:lapDTOs[].verticalOscillation |
| `exercise.splitSummaries[].metricsSummary.mobilityMetrics.avgVerticalRatio` | number:double | FILLED | get_activity_splits:lapDTOs[].verticalRatio |
| `exercise.splitSummaries[].metricsSummary.runVo2Max` | number:double | writable, no Garmin source |  |
| `exercise.splitSummaries[].metricsSummary.steps` | string:int64 | writable, no Garmin source |  |
| `exercise.splitSummaries[].metricsSummary.totalSwimLengths` | number:double | writable, no Garmin source |  |
| `exercise.splitSummaries[].splitType` | string (SPLIT_TYPE_UNSPECIFIED, MANUAL, DURATION, DISTANCE, CALORIES) | FILLED (derived/constant) | constant: MANUAL (Garmin laps) |
| `exercise.splitSummaries[].startTime` | string:google-datetime | FILLED | get_activity_splits:lapDTOs[].startTimeGMT |
| `exercise.splitSummaries[].startUtcOffset` | string:google-duration | FILLED (derived/constant) | derived: user's UTC offset (Local - GMT) from the same Garmin record |
| `exercise.splits[].endTime` | string:google-datetime | writable, no Garmin source |  |
| `exercise.splits[].endUtcOffset` | string:google-duration | writable, no Garmin source |  |
| `exercise.splits[].metricsSummary.activeZoneMinutes` | string:int64 | writable, no Garmin source |  |
| `exercise.splits[].metricsSummary.averageHeartRateBeatsPerMinute` | string:int64 | writable, no Garmin source |  |
| `exercise.splits[].metricsSummary.averagePaceSecondsPerMeter` | number:double | writable, no Garmin source |  |
| `exercise.splits[].metricsSummary.averageSpeedMillimetersPerSecond` | number:double | writable, no Garmin source |  |
| `exercise.splits[].metricsSummary.caloriesKcal` | number:double | writable, no Garmin source |  |
| `exercise.splits[].metricsSummary.distanceMillimeters` | number:double | writable, no Garmin source |  |
| `exercise.splits[].metricsSummary.elevationGainMillimeters` | number:double | writable, no Garmin source |  |
| `exercise.splits[].metricsSummary.heartRateZoneDurations.lightTime` | string:google-duration | writable, no Garmin source |  |
| `exercise.splits[].metricsSummary.heartRateZoneDurations.moderateTime` | string:google-duration | writable, no Garmin source |  |
| `exercise.splits[].metricsSummary.heartRateZoneDurations.peakTime` | string:google-duration | writable, no Garmin source |  |
| `exercise.splits[].metricsSummary.heartRateZoneDurations.vigorousTime` | string:google-duration | writable, no Garmin source |  |
| `exercise.splits[].metricsSummary.mobilityMetrics.avgCadenceStepsPerMinute` | number:double | writable, no Garmin source |  |
| `exercise.splits[].metricsSummary.mobilityMetrics.avgGroundContactTimeDuration` | string:google-duration | writable, no Garmin source |  |
| `exercise.splits[].metricsSummary.mobilityMetrics.avgStrideLengthMillimeters` | string:int64 | writable, no Garmin source |  |
| `exercise.splits[].metricsSummary.mobilityMetrics.avgVerticalOscillationMillimeters` | string:int64 | writable, no Garmin source |  |
| `exercise.splits[].metricsSummary.mobilityMetrics.avgVerticalRatio` | number:double | writable, no Garmin source |  |
| `exercise.splits[].metricsSummary.runVo2Max` | number:double | writable, no Garmin source |  |
| `exercise.splits[].metricsSummary.steps` | string:int64 | writable, no Garmin source |  |
| `exercise.splits[].metricsSummary.totalSwimLengths` | number:double | writable, no Garmin source |  |
| `exercise.splits[].splitType` | string (SPLIT_TYPE_UNSPECIFIED, MANUAL, DURATION, DISTANCE, CALORIES) | writable, no Garmin source |  |
| `exercise.splits[].startTime` | string:google-datetime | writable, no Garmin source |  |
| `exercise.splits[].startUtcOffset` | string:google-duration | writable, no Garmin source |  |

### `sleep`  (13 of 15 writable fields filled)

| Google field | Type | Status | Garmin source / note |
|---|---|---|---|
| `sleep.interval.endTime` | string:google-datetime | FILLED | get_sleep_data:dailySleepDTO.sleepEndTimestampGMT |
| `sleep.interval.endUtcOffset` | string:google-duration | FILLED | get_sleep_data:dailySleepDTO.sleepEndTimestampLocal |
| `sleep.interval.startTime` | string:google-datetime | FILLED | get_sleep_data:dailySleepDTO.sleepStartTimestampGMT |
| `sleep.interval.startUtcOffset` | string:google-duration | FILLED | get_sleep_data:dailySleepDTO.sleepStartTimestampLocal |
| `sleep.metadata.externalId` | string | FILLED | get_sleep_data:dailySleepDTO.calendarDate |
| `sleep.outOfBedSegments[].endTime` | string:google-datetime | writable, no Garmin source |  |
| `sleep.outOfBedSegments[].endUtcOffset` | string:google-duration | FILLED (derived/constant) | derived: user's UTC offset (Local - GMT) from the same Garmin record |
| `sleep.outOfBedSegments[].startTime` | string:google-datetime | writable, no Garmin source |  |
| `sleep.outOfBedSegments[].startUtcOffset` | string:google-duration | FILLED (derived/constant) | derived: user's UTC offset (Local - GMT) from the same Garmin record |
| `sleep.stages[].endTime` | string:google-datetime | FILLED | get_sleep_data:sleepLevels[].endGMT |
| `sleep.stages[].endUtcOffset` | string:google-duration | FILLED (derived/constant) | derived: user's UTC offset (Local - GMT) from the same Garmin record |
| `sleep.stages[].startTime` | string:google-datetime | FILLED | get_sleep_data:sleepLevels[].startGMT |
| `sleep.stages[].startUtcOffset` | string:google-duration | FILLED (derived/constant) | derived: user's UTC offset (Local - GMT) from the same Garmin record |
| `sleep.stages[].type` | string (SLEEP_STAGE_TYPE_UNSPECIFIED, AWAKE, LIGHT, DEEP, REM, ASLEEP, RESTLESS) | FILLED | FIT:sleep_level:sleep_level.sleep_level<br> FIT:sleep_level:sleep_level.timestamp<br> get_sleep_data:sleepLevels[].activityLevel |
| `sleep.type` | string (SLEEP_TYPE_UNSPECIFIED, CLASSIC, STAGES) | FILLED | get_sleep_data:dailySleepDTO.deviceRemCapable<br> get_sleep_data:remSleepData |

### `weight`  (3 of 4 writable fields filled)

| Google field | Type | Status | Garmin source / note |
|---|---|---|---|
| `weight.notes` | string | writable, no Garmin source |  |
| `weight.sampleTime.physicalTime` | string:google-datetime | FILLED | get_body_composition:dateWeightList[].timestampGMT<br> get_daily_weigh_ins:dateWeightList[].timestampGMT<br> get_weigh_ins:dailyWeightSummaries[].allWeightMetrics[].timestampGMT |
| `weight.sampleTime.utcOffset` | string:google-duration | FILLED | get_body_composition:dateWeightList[].date<br> get_daily_weigh_ins:dateWeightList[].date<br> get_weigh_ins:dailyWeightSummaries[].allWeightMetrics[].date |
| `weight.weightGrams` | number:double | FILLED | get_body_composition:dateWeightList[].weight<br> get_daily_weigh_ins:dateWeightList[].weight<br> get_weigh_ins:dailyWeightSummaries[].allWeightMetrics[].weight |

### `bodyFat`  (3 of 3 writable fields filled)

| Google field | Type | Status | Garmin source / note |
|---|---|---|---|
| `bodyFat.percentage` | number:double | FILLED | get_body_composition:dateWeightList[].bodyFat<br> get_daily_weigh_ins:dateWeightList[].bodyFat<br> get_weigh_ins:dailyWeightSummaries[].allWeightMetrics[].bodyFat |
| `bodyFat.sampleTime.physicalTime` | string:google-datetime | FILLED | get_body_composition:dateWeightList[].timestampGMT<br> get_daily_weigh_ins:dateWeightList[].timestampGMT<br> get_weigh_ins:dailyWeightSummaries[].allWeightMetrics[].timestampGMT |
| `bodyFat.sampleTime.utcOffset` | string:google-duration | FILLED | get_body_composition:dateWeightList[].date<br> get_daily_weigh_ins:dateWeightList[].date<br> get_weigh_ins:dailyWeightSummaries[].allWeightMetrics[].date |

### `hydrationLog`  (6 of 6 writable fields filled)

| Google field | Type | Status | Garmin source / note |
|---|---|---|---|
| `hydrationLog.amountConsumed.milliliters` | number:double | FILLED | get_hydration_data:valueInML |
| `hydrationLog.amountConsumed.userProvidedUnit` | string (9 values) | FILLED (derived/constant) | constant: MILLILITER |
| `hydrationLog.interval.endTime` | string:google-datetime | FILLED | get_hydration_data:calendarDate |
| `hydrationLog.interval.endUtcOffset` | string:google-duration | FILLED (derived/constant) | derived: user's UTC offset (Local - GMT) from the same Garmin record |
| `hydrationLog.interval.startTime` | string:google-datetime | FILLED | get_hydration_data:calendarDate |
| `hydrationLog.interval.startUtcOffset` | string:google-duration | FILLED (derived/constant) | derived: user's UTC offset (Local - GMT) from the same Garmin record |

### `height`  (2 of 3 writable fields filled)

| Google field | Type | Status | Garmin source / note |
|---|---|---|---|
| `height.heightMillimeters` | string:int64 | FILLED (opt-in) | get_user_profile:userData.height |
| `height.sampleTime.physicalTime` | string:google-datetime | writable, no Garmin source |  |
| `height.sampleTime.utcOffset` | string:google-duration | FILLED (derived/constant) | derived: user's UTC offset (Local - GMT) from the same Garmin record |

### `nutritionLog`  (9 of 21 writable fields filled)

| Google field | Type | Status | Garmin source / note |
|---|---|---|---|
| `nutritionLog.energy.kcal` | number:double | FILLED (opt-in) | get_nutrition_daily_food_log:mealDetails[].mealNutritionContent.calories |
| `nutritionLog.energy.userProvidedUnit` | string (ENERGY_UNIT_UNSPECIFIED, JOULE, KILOJOULE, KILOCALORIE, SMALL_CALORIE, CALORIE) | writable, no Garmin source |  |
| `nutritionLog.energyFromFat.kcal` | number:double | writable, no Garmin source |  |
| `nutritionLog.energyFromFat.userProvidedUnit` | string (ENERGY_UNIT_UNSPECIFIED, JOULE, KILOJOULE, KILOCALORIE, SMALL_CALORIE, CALORIE) | writable, no Garmin source |  |
| `nutritionLog.food` | string | writable, no Garmin source |  |
| `nutritionLog.foodDisplayName` | string | writable, no Garmin source |  |
| `nutritionLog.interval.endTime` | string:google-datetime | FILLED (opt-in) | get_nutrition_daily_food_log:mealDetails[].meal.endTime |
| `nutritionLog.interval.endUtcOffset` | string:google-duration | FILLED (derived/constant) | derived: user's UTC offset (Local - GMT) from the same Garmin record |
| `nutritionLog.interval.startTime` | string:google-datetime | FILLED (opt-in) | get_nutrition_daily_food_log:mealDetails[].meal.startTime |
| `nutritionLog.interval.startUtcOffset` | string:google-duration | FILLED (derived/constant) | derived: user's UTC offset (Local - GMT) from the same Garmin record |
| `nutritionLog.mealType` | string (10 values) | FILLED (opt-in) | get_nutrition_daily_food_log:mealDetails[].meal.mealName |
| `nutritionLog.nutrients[]` | array<NutrientQuantity> | FILLED (opt-in) | get_nutrition_daily_food_log:mealDetails[].mealNutritionContent.protein |
| `nutritionLog.nutrients[].nutrient` | string (40 values) | writable, no Garmin source |  |
| `nutritionLog.nutrients[].quantity.grams` | number:double | writable, no Garmin source |  |
| `nutritionLog.nutrients[].quantity.userProvidedUnit` | string (9 values) | writable, no Garmin source |  |
| `nutritionLog.serving.amount` | number:double | writable, no Garmin source |  |
| `nutritionLog.serving.foodMeasurementUnit` | string | writable, no Garmin source |  |
| `nutritionLog.totalCarbohydrate.grams` | number:double | FILLED (opt-in) | get_nutrition_daily_food_log:mealDetails[].mealNutritionContent.carbs |
| `nutritionLog.totalCarbohydrate.userProvidedUnit` | string (9 values) | writable, no Garmin source |  |
| `nutritionLog.totalFat.grams` | number:double | FILLED (opt-in) | get_nutrition_daily_food_log:mealDetails[].mealNutritionContent.fat |
| `nutritionLog.totalFat.userProvidedUnit` | string (9 values) | writable, no Garmin source |  |

### `menstrualPeriod`  (5 of 5 writable fields filled)

| Google field | Type | Status | Garmin source / note |
|---|---|---|---|
| `menstrualPeriod.interval.endTime` | string:google-datetime | FILLED (opt-in) | get_menstrual_data_for_date:dayLog.flow |
| `menstrualPeriod.interval.endUtcOffset` | string:google-duration | FILLED (derived/constant) | derived: user's UTC offset (Local - GMT) from the same Garmin record |
| `menstrualPeriod.interval.startTime` | string:google-datetime | FILLED (opt-in) | get_menstrual_data_for_date:dayLog.flow |
| `menstrualPeriod.interval.startUtcOffset` | string:google-duration | FILLED (derived/constant) | derived: user's UTC offset (Local - GMT) from the same Garmin record |
| `menstrualPeriod.notes` | string | FILLED (opt-in) | get_menstrual_data_for_date:dayLog.notes |

### `symptoms`  (2 of 3 writable fields filled)

| Google field | Type | Status | Garmin source / note |
|---|---|---|---|
| `symptoms.sampleTime.physicalTime` | string:google-datetime | writable, no Garmin source |  |
| `symptoms.sampleTime.utcOffset` | string:google-duration | FILLED (derived/constant) | derived: user's UTC offset (Local - GMT) from the same Garmin record |
| `symptoms.symptoms[]` | array<string> (66 values) | FILLED (opt-in) | get_menstrual_data_for_date:dayLog.symptoms |

### `moods`  (2 of 3 writable fields filled)

| Google field | Type | Status | Garmin source / note |
|---|---|---|---|
| `moods.moods[]` | array<string> (69 values) | FILLED (opt-in) | get_menstrual_data_for_date:dayLog.moods |
| `moods.sampleTime.physicalTime` | string:google-datetime | writable, no Garmin source |  |
| `moods.sampleTime.utcOffset` | string:google-duration | FILLED (derived/constant) | derived: user's UTC offset (Local - GMT) from the same Garmin record |

### `ovulationTest`  (0 of 3 writable fields filled)

| Google field | Type | Status | Garmin source / note |
|---|---|---|---|
| `ovulationTest.result` | string (OVULATION_TEST_RESULT_UNSPECIFIED, NEGATIVE, LUTEINIZING_HORMONE_SURGE, ESTROGEN_SURGE, POSITIVE, INDETERMINATE) | writable, no Garmin source |  |
| `ovulationTest.sampleTime.physicalTime` | string:google-datetime | writable, no Garmin source |  |
| `ovulationTest.sampleTime.utcOffset` | string:google-duration | writable, no Garmin source |  |

### `Profile`  (1 of 4 writable fields filled)

| Google field | Type | Status | Garmin source / note |
|---|---|---|---|
| `age` | integer:int32 | FILLED (opt-in) | get_user_profile:userData.birthDate |
| `name` | string | writable, no Garmin source |  |
| `userConfiguredRunningStrideLengthMm` | integer:int32 | writable, no Garmin source |  |
| `userConfiguredWalkingStrideLengthMm` | integer:int32 | writable, no Garmin source |  |

### `Settings`  (5 of 14 writable fields filled)

| Google field | Type | Status | Garmin source / note |
|---|---|---|---|
| `autoStrideEnabled` | boolean | writable, no Garmin source |  |
| `distanceUnit` | string (DISTANCE_UNIT_UNSPECIFIED, DISTANCE_UNIT_MILES, DISTANCE_UNIT_KILOMETERS) | FILLED (opt-in) | get_user_profile:userData.measurementSystem |
| `glucoseUnit` | string (GLUCOSE_UNIT_UNSPECIFIED, GLUCOSE_UNIT_MG_DL, GLUCOSE_UNIT_MMOL_L) | writable, no Garmin source |  |
| `heightUnit` | string (HEIGHT_UNIT_UNSPECIFIED, HEIGHT_UNIT_INCHES, HEIGHT_UNIT_CENTIMETERS) | FILLED (opt-in) | get_user_profile:userData.measurementSystem |
| `languageLocale` | string | writable, no Garmin source |  |
| `name` | string | writable, no Garmin source |  |
| `strideLengthRunningType` | string (STRIDE_LENGTH_TYPE_UNSPECIFIED, STRIDE_LENGTH_TYPE_DEFAULT, STRIDE_LENGTH_TYPE_MANUAL, STRIDE_LENGTH_TYPE_AUTO) | writable, no Garmin source |  |
| `strideLengthWalkingType` | string (STRIDE_LENGTH_TYPE_UNSPECIFIED, STRIDE_LENGTH_TYPE_DEFAULT, STRIDE_LENGTH_TYPE_MANUAL, STRIDE_LENGTH_TYPE_AUTO) | writable, no Garmin source |  |
| `swimUnit` | string (SWIM_UNIT_UNSPECIFIED, SWIM_UNIT_METERS, SWIM_UNIT_YARDS) | writable, no Garmin source |  |
| `temperatureUnit` | string (TEMPERATURE_UNIT_UNSPECIFIED, TEMPERATURE_UNIT_CELSIUS, TEMPERATURE_UNIT_FAHRENHEIT) | writable, no Garmin source |  |
| `timeZone` | string | FILLED (opt-in) | get_userprofile_settings:timeZone |
| `utcOffset` | string:google-duration | FILLED (derived/constant) | derived: user's UTC offset (Local - GMT) from the same Garmin record |
| `waterUnit` | string (WATER_UNIT_UNSPECIFIED, WATER_UNIT_ML, WATER_UNIT_FL_OZ, WATER_UNIT_CUP) | writable, no Garmin source |  |
| `weightUnit` | string (WEIGHT_UNIT_UNSPECIFIED, WEIGHT_UNIT_POUNDS, WEIGHT_UNIT_STONE, WEIGHT_UNIT_KILOGRAMS) | FILLED (opt-in) | get_user_profile:userData.measurementSystem |

## 3. Health Connect export: data Google's API will not accept

These Garmin fields have no writable Google field, but Health Connect (Android, on-device) has a record for them. They go into the export file for an Android companion app.

| Health Connect record | Garmin field | From (method) | Transform / note |
|---|---|---|---|
| `ActiveCaloriesBurnedRecord.energy` | `bmrCalories` | `get_activities`, `get_activities_by_date`, `get_activity`, `get_last_activity` | active = calories - bmrCalories (derive) |
| `ActiveCaloriesBurnedRecord.energy` | `activeKilocalories` | `get_stats`, `get_stats_and_body`, `get_user_summary` | daily interval; Google active-energy-burned is read-only |
| `BasalMetabolicRateRecord.basalMetabolicRate` | `bmrKilocalories` | `get_stats`, `get_stats_and_body`, `get_user_summary` | kcal/day -> watts; Google basal-energy-burned is read-only |
| `BloodPressureRecord.diastolic` | `measurementSummaries[].measurements[].diastolic` | `get_blood_pressure` | mmHg |
| `BloodPressureRecord.systolic` | `measurementSummaries[].measurements[].systolic` | `get_blood_pressure` | mmHg; Google v4 has no blood-pressure type |
| `BloodPressureRecord.time` | `measurementSummaries[].measurements[].measurementTimestampGMT` | `get_blood_pressure` | ISO GMT string |
| `BloodPressureRecord.zoneOffset` | `measurementSummaries[].measurements[].measurementTimestampLocal` | `get_blood_pressure` | (Local - GMT) |
| `BodyWaterMassRecord.mass` | `bodyWater` | `get_body_composition`, `get_daily_weigh_ins`, `get_weigh_ins` | derive: percent x weight; no Google body-water type |
| `BoneMassRecord.mass` | `boneMass` | `get_body_composition`, `get_daily_weigh_ins`, `get_weigh_ins` | grams -> kg; no Google bone-mass type |
| `DistanceRecord.distance` | `totalDistanceMeters` | `get_stats`, `get_stats_and_body`, `get_user_summary` | metres; daily interval |
| `ExerciseRoute.locations[]` | `record.altitude` | `FIT:record` | semicircles -> degrees; route is Health Connect only |
| `ExerciseRoute.locations[]` | `record.enhanced_altitude` | `FIT:record` | semicircles -> degrees; route is Health Connect only |
| `ExerciseRoute.locations[]` | `record.position_lat` | `FIT:record` | semicircles -> degrees; route is Health Connect only |
| `ExerciseRoute.locations[]` | `record.position_long` | `FIT:record` | semicircles -> degrees; route is Health Connect only |
| `FloorsClimbedRecord` | `floorValuesArray` | `get_floors` | rows named by floorsValueDescriptorDTOList (confirm column keys on a real account); finer than the daily total |
| `FloorsClimbedRecord` | `floorValuesArray[]` | `get_floors` | rows named by floorsValueDescriptorDTOList (confirm column keys on a real account); finer than the daily total |
| `FloorsClimbedRecord` | `floorValuesArray[][]` | `get_floors` | rows named by floorsValueDescriptorDTOList (confirm column keys on a real account); finer than the daily total |
| `FloorsClimbedRecord.floors` | `floorsAscended` | `get_stats`, `get_stats_and_body`, `get_user_summary` | daily interval; Google floors is read-only |
| `HeartRateRecord.samples[]` | `heartRateValues[][]` | `get_heart_rates` | rows [epoch_ms, bpm\|null]; null = no reading; Google heart-rate is read-only |
| `HeartRateRecord.samples[].beatsPerMinute` | `record.heart_rate` | `FIT:record` | bpm per second; Google heart-rate is read-only |
| `HeartRateVariabilityRmssdRecord.heartRateVariabilityMillis` | `beat_intervals.time` | `FIT:beat_intervals` | raw R-R intervals: derive RMSSD; Google equivalent is read-only |
| `HeartRateVariabilityRmssdRecord.heartRateVariabilityMillis` | `beat_intervals.timestamp_ms` | `FIT:beat_intervals` | raw R-R intervals: derive RMSSD; Google equivalent is read-only |
| `HeartRateVariabilityRmssdRecord.heartRateVariabilityMillis` | `hrv.time` | `FIT:hrv` | hrv.time = R-R intervals (s): derive RMSSD per 5 min; Google equivalent is read-only |
| `HeartRateVariabilityRmssdRecord.heartRateVariabilityMillis` | `hrv_value.hrv_value` | `FIT:hrv_value` | hrv.time = R-R intervals (s): derive RMSSD per 5 min; Google equivalent is read-only |
| `HeartRateVariabilityRmssdRecord.heartRateVariabilityMillis` | `hrv_value.value` | `FIT:hrv_value` | hrv.time = R-R intervals (s): derive RMSSD per 5 min; Google equivalent is read-only |
| `HeartRateVariabilityRmssdRecord.heartRateVariabilityMillis` | `hrvReadings[].hrvValue` | `get_hrv_data` | ms; Google heart-rate-variability is read-only |
| `HeartRateVariabilityRmssdRecord.time` | `hrv_value.timestamp` | `FIT:hrv_value` | FIT timestamp |
| `HeartRateVariabilityRmssdRecord.time` | `hrvReadings[].readingTimeGMT` | `get_hrv_data` | ISO GMT string |
| `HeartRateVariabilityRmssdRecord.zoneOffset` | `hrvReadings[].readingTimeLocal` | `get_hrv_data` | (Local - GMT) |
| `Metadata.clientRecordId` | `activityId` | `get_activities`, `get_activities_by_date`, `get_activity`, `get_last_activity` | stable id 'garmin:exercise:<id>'; Google exercise has no external-id field, so de-dup is by time overlap |
| `Metadata.clientRecordId` | `samplePk` | `get_body_composition`, `get_daily_weigh_ins`, `get_weigh_ins` | 'garmin:weight:<samplePk>' |
| `OxygenSaturationRecord.percentage` | `spo2_data.reading_spo2` | `FIT:spo2_data` | percent; Google equivalent is read-only |
| `OxygenSaturationRecord.percentage` | `wellnessEpochSPO2DataDTOList[].spo2Reading` | `get_sleep_data` | percent; Google oxygen-saturation is read-only |
| `OxygenSaturationRecord.time` | `spo2_data.timestamp` | `FIT:spo2_data` | FIT timestamp |
| `OxygenSaturationRecord.time` | `wellnessEpochSPO2DataDTOList[].epochTimestamp` | `get_sleep_data` | ISO UTC string -> time |
| `PowerRecord.samples[].power` | `record.power` | `FIT:record` | watts |
| `RespiratoryRateRecord` | `respirationValuesArray[]` | `get_respiration_data` | rows [epoch_ms, breaths/min]; -1 = invalid; ~2-minute resolution; Google respiratory types are read-only |
| `RespiratoryRateRecord` | `respirationValuesArray[][]` | `get_respiration_data` | rows [epoch_ms, breaths/min]; -1 = invalid; ~2-minute resolution; Google respiratory types are read-only |
| `RespiratoryRateRecord.rate` | `respiration_rate.enhanced_respiration_rate` | `FIT:respiration_rate` | breaths/min; Google equivalent is read-only |
| `RespiratoryRateRecord.rate` | `respiration_rate.respiration_rate` | `FIT:respiration_rate` | breaths/min; Google equivalent is read-only |
| `RespiratoryRateRecord.rate` | `wellnessEpochRespirationDataDTOList[].respirationValue` | `get_sleep_data` | breaths/min; Google daily-respiratory-rate / respiratory-rate-sleep-summary are read-only |
| `RespiratoryRateRecord.time` | `respiration_rate.timestamp` | `FIT:respiration_rate` | FIT timestamp |
| `RespiratoryRateRecord.time` | `wellnessEpochRespirationDataDTOList[].startTimeGMT` | `get_sleep_data` | ms epoch -> time |
| `RestingHeartRateRecord.beatsPerMinute` | `monitoring_hr_data.current_day_resting_heart_rate` | `FIT:monitoring_hr_data` | bpm; Google equivalent is read-only |
| `RestingHeartRateRecord.beatsPerMinute` | `monitoring_hr_data.resting_heart_rate` | `FIT:monitoring_hr_data` | bpm; Google equivalent is read-only |
| `RestingHeartRateRecord.beatsPerMinute` | `restingHeartRate` | `get_heart_rates` | one per day |
| `RestingHeartRateRecord.beatsPerMinute` | `restingHeartRate` | `get_stats`, `get_stats_and_body`, `get_user_summary` | one per day; Google daily-resting-heart-rate is read-only |
| `RestingHeartRateRecord.time` | `monitoring_hr_data.timestamp` | `FIT:monitoring_hr_data` | FIT timestamp |
| `SkinTemperatureRecord.baseline/deltas` | `avgSkinTempDeviationC` | `get_sleep_data` | overnight delta vs baseline (degC); Google dailySleepTemperatureDerivations is read-only |
| `SkinTemperatureRecord.deltas` | `skin_temp_overnight.average_deviation` | `FIT:skin_temp_overnight` | overnight skin-temperature deviation; Google equivalent is read-only |
| `SkinTemperatureRecord.deltas` | `skin_temp_overnight.nightly_value` | `FIT:skin_temp_overnight` | overnight skin-temperature deviation; Google equivalent is read-only |
| `SpeedRecord.samples[].speed` | `record.enhanced_speed` | `FIT:record` | m/s |
| `SpeedRecord.samples[].speed` | `record.speed` | `FIT:record` | m/s |
| `StepsCadenceRecord / CyclingPedalingCadenceRecord` | `record.cadence` | `FIT:record` | by sport |
| `StepsCadenceRecord / CyclingPedalingCadenceRecord` | `record.fractional_cadence` | `FIT:record` | by sport |
| `StepsRecord.count` | `steps` | `get_steps_data` | Google steps is read-only (list/rollup only) |
| `StepsRecord.endTime` | `endGMT` | `get_steps_data` | GMT string |
| `StepsRecord.startTime` | `startGMT` | `get_steps_data` | 15-minute bucket; GMT string |
| `TotalCaloriesBurnedRecord.energy` | `totalKilocalories` | `get_stats`, `get_stats_and_body`, `get_user_summary` | daily interval; Google total-calories is read-only |
| `Vo2MaxRecord.time` | `generic.calendarDate` | `get_max_metrics`, `get_max_metrics_range` | local date -> day start in user's zone |
| `Vo2MaxRecord.vo2MillilitersPerMinuteKilogram` | `generic.vo2MaxPreciseValue` | `get_max_metrics`, `get_max_metrics_range` | ml/kg/min; Google vo2-max / daily-vo2-max are read-only |

_Every Health Connect record also needs a timestamp and zone offset; they come from the paired GMT/Local fields of the same response (for example `startTimestampGMT`/`startTimestampLocal`, `wellnessStartTimeGmt`/`wellnessStartTimeLocal`)._

## 4. Archive only: everything else, and why

These fields are still downloaded and kept in the local SQLite archive (so nothing is lost and mappings can be revised without re-downloading), but have no Google or Health Connect destination.

| Reason | Fields |
|---|---|
| FIT message duplicating mapped JSON fields, or Garmin-only/low-level device data | 530 |
| no Google or Health Connect equivalent | 493 |
| Garmin-proprietary training/fitness analytics (no Google or Health Connect equivalent) | 326 |
| Google PairedDevice is output-only (battery, last sync); Garmin device registry has no write path | 316 |
| per-activity detail with no Google field (GPS/streams/weather/zones); streams can feed Health Connect via FIT | 224 |
| golf scorecards/shots: no equivalent (Google has a GOLF exercise type for the session only) | 214 |
| gamification/goals: no equivalent | 169 |
| planned workouts/plans: Google exercise stores completed sessions only (Health Connect PlannedExerciseSessionRecord could take them, not in scope) | 169 |
| Garmin daily rollups with no Google/Health Connect type, or duplicated by a dedicated endpoint | 165 |
| account profile data with no safe default write path | 150 |
| Garmin's per-split-type bests carry no start/end times, which Google SplitSummary requires | 83 |
| duplicates of allWeightMetrics entries, neighbours or period averages | 83 |
| Garmin-only concept (stress score, Body Battery, intensity-minute goals); no Google or Health Connect type | 74 |
| Garmin proprietary sleep score / sleep-need model; Google has no score field | 56 |
| rounded duplicate, cycling VO2 (Health Connect has one VO2 record without sport), heat/altitude acclimation | 54 |
| Garmin physiological overlay metrics have no equivalent on the exercise type | 43 |
| Cycling power: Health Connect PowerRecord exists but needs the per-second stream (FIT), not the summary | 36 |
| day aggregates / sleep-window timestamps; the readings above carry the data | 28 |
| equipment tracking: no equivalent | 24 |
| Garmin training-effect/load metrics have no Google or Health Connect equivalent | 24 |
| Garmin coaching text / classification with no Google or Health Connect equivalent | 24 |
| binary file download (FIT/TCX/GPX/ZIP); fields are listed under FIT:* | 21 |
| no equivalent (Health Connect LeanBodyMass is not skeletal muscle mass; BMI is derivable) | 21 |
| aggregates / descriptor lists | 20 |
| night summaries; the underlying series is mapped from the epoch lists above | 19 |

### Per-method coverage

| Method / FIT message | Fields | Google (G, G+H) | Health Connect only | Opt-in | Archive only | Housekeeping |
|---|---|---|---|---|---|---|
| `FIT:activity` | 8 | 0 | 0 | 0 | 8 | 0 |
| `FIT:activity_metrics` | 4 | 0 | 0 | 0 | 4 | 0 |
| `FIT:battery` | 2 | 0 | 0 | 0 | 2 | 0 |
| `FIT:beat_intervals` | 3 | 0 | 2 | 0 | 1 | 0 |
| `FIT:best_effort` | 5 | 0 | 0 | 0 | 5 | 0 |
| `FIT:blood_pressure` | 8 | 0 | 0 | 0 | 8 | 0 |
| `FIT:device_info` | 17 | 0 | 0 | 0 | 17 | 0 |
| `FIT:device_settings` | 16 | 0 | 0 | 0 | 16 | 0 |
| `FIT:device_used` | 4 | 0 | 0 | 0 | 4 | 0 |
| `FIT:event` | 9 | 0 | 0 | 0 | 9 | 0 |
| `FIT:file_creator` | 2 | 0 | 0 | 0 | 2 | 0 |
| `FIT:file_id` | 7 | 0 | 0 | 0 | 7 | 0 |
| `FIT:hrv` | 1 | 0 | 1 | 0 | 0 | 0 |
| `FIT:hrv_status_summary` | 15 | 0 | 0 | 0 | 15 | 0 |
| `FIT:hrv_value` | 3 | 0 | 3 | 0 | 0 | 0 |
| `FIT:hsa_*` | 25 | 0 | 0 | 0 | 25 | 0 |
| `FIT:lap` | 55 | 0 | 0 | 0 | 55 | 0 |
| `FIT:length` | 18 | 0 | 0 | 0 | 18 | 0 |
| `FIT:monitoring` | 29 | 0 | 0 | 0 | 29 | 0 |
| `FIT:monitoring_hr_data` | 3 | 0 | 3 | 0 | 0 | 0 |
| `FIT:monitoring_info` | 6 | 0 | 0 | 0 | 6 | 0 |
| `FIT:nap_event` | 10 | 0 | 0 | 0 | 10 | 0 |
| `FIT:record` | 41 | 0 | 12 | 0 | 29 | 0 |
| `FIT:respiration_rate` | 3 | 0 | 3 | 0 | 0 | 0 |
| `FIT:session` | 93 | 0 | 0 | 0 | 93 | 0 |
| `FIT:set` | 11 | 0 | 0 | 0 | 11 | 0 |
| `FIT:skin_temp_overnight` | 5 | 0 | 2 | 0 | 3 | 0 |
| `FIT:sleep_assessment` | 14 | 0 | 0 | 0 | 14 | 0 |
| `FIT:sleep_data_info` | 1 | 0 | 0 | 0 | 1 | 0 |
| `FIT:sleep_disruption_overnight_severity` | 2 | 0 | 0 | 0 | 2 | 0 |
| `FIT:sleep_disruption_severity_period` | 3 | 0 | 0 | 0 | 3 | 0 |
| `FIT:sleep_end` | 1 | 0 | 0 | 0 | 1 | 0 |
| `FIT:sleep_level` | 2 | 2 | 0 | 0 | 0 | 0 |
| `FIT:split` | 38 | 0 | 0 | 0 | 38 | 0 |
| `FIT:split_summary` | 25 | 0 | 0 | 0 | 25 | 0 |
| `FIT:spo2_data` | 4 | 0 | 2 | 0 | 2 | 0 |
| `FIT:sport` | 3 | 0 | 0 | 0 | 3 | 0 |
| `FIT:stress_level` | 4 | 0 | 0 | 0 | 4 | 0 |
| `FIT:time_in_zone` | 16 | 0 | 0 | 0 | 16 | 0 |
| `FIT:user_metrics` | 5 | 0 | 0 | 0 | 5 | 0 |
| `FIT:user_profile` | 21 | 0 | 0 | 0 | 21 | 0 |
| `FIT:weight_scale` | 13 | 0 | 0 | 0 | 13 | 0 |
| `FIT:zones_target` | 5 | 0 | 0 | 0 | 5 | 0 |
| `download_activity` | 19 | 0 | 0 | 0 | 19 | 0 |
| `download_health_snapshot` | 2 | 0 | 0 | 0 | 2 | 0 |
| `get_activities` | 198 | 32 | 2 | 0 | 130 | 34 |
| `get_activities_by_date` | 198 | 32 | 2 | 0 | 130 | 34 |
| `get_activity` | 221 | 22 | 2 | 0 | 154 | 43 |
| `get_activity_details` | 92 | 0 | 0 | 0 | 84 | 8 |
| `get_activity_exercise_sets` | 22 | 0 | 0 | 0 | 20 | 2 |
| `get_activity_gear` | 19 | 0 | 0 | 0 | 14 | 5 |
| `get_activity_hr_in_timezones` | 4 | 0 | 0 | 0 | 3 | 1 |
| `get_activity_power_in_timezones` | 4 | 0 | 0 | 0 | 3 | 1 |
| `get_activity_split_summaries` | 42 | 0 | 0 | 0 | 38 | 4 |
| `get_activity_splits` | 55 | 16 | 0 | 0 | 32 | 7 |
| `get_activity_typed_splits` | 46 | 0 | 0 | 0 | 42 | 4 |
| `get_activity_types` | 7 | 0 | 0 | 0 | 4 | 3 |
| `get_activity_weather` | 19 | 0 | 0 | 0 | 16 | 3 |
| `get_adhoc_challenges` | 10 | 0 | 0 | 0 | 6 | 4 |
| `get_all_day_events` | 1 | 0 | 0 | 0 | 0 | 1 |
| `get_all_day_stress` | 24 | 0 | 0 | 0 | 15 | 9 |
| `get_available_badge_challenges` | 14 | 0 | 0 | 0 | 9 | 5 |
| `get_available_badges` | 53 | 0 | 0 | 0 | 36 | 17 |
| `get_badge_challenges` | 13 | 0 | 0 | 0 | 8 | 5 |
| `get_blood_pressure` | 19 | 0 | 4 | 0 | 10 | 5 |
| `get_body_battery` | 22 | 0 | 0 | 0 | 17 | 5 |
| `get_body_battery_events` | 19 | 0 | 0 | 0 | 13 | 6 |
| `get_body_composition` | 31 | 5 | 3 | 0 | 20 | 3 |
| `get_calories_daily` | 5 | 0 | 0 | 0 | 4 | 1 |
| `get_cycling_ftp` | 6 | 0 | 0 | 0 | 4 | 2 |
| `get_daily_steps` | 5 | 0 | 0 | 0 | 4 | 1 |
| `get_daily_training_status` | 35 | 0 | 0 | 0 | 28 | 7 |
| `get_daily_weigh_ins` | 31 | 5 | 3 | 0 | 20 | 3 |
| `get_device_alarms` | 8 | 0 | 0 | 0 | 6 | 2 |
| `get_device_last_used` | 8 | 0 | 0 | 0 | 7 | 1 |
| `get_device_settings` | 20 | 0 | 0 | 0 | 16 | 4 |
| `get_device_solar_data` | 13 | 0 | 0 | 0 | 9 | 4 |
| `get_devices` | 274 | 4 | 0 | 0 | 266 | 4 |
| `get_earned_badges` | 53 | 0 | 0 | 0 | 36 | 17 |
| `get_endurance_score` | 27 | 0 | 0 | 0 | 19 | 8 |
| `get_fitnessage_data` | 13 | 0 | 0 | 0 | 12 | 1 |
| `get_floors` | 10 | 0 | 3 | 0 | 6 | 1 |
| `get_full_name` | 66 | 0 | 0 | 0 | 56 | 10 |
| `get_functional_threshold_power_range` | 6 | 0 | 0 | 0 | 5 | 1 |
| `get_gear` | 19 | 0 | 0 | 0 | 14 | 5 |
| `get_gear_activities` | 8 | 0 | 0 | 0 | 6 | 2 |
| `get_gear_defaults` | 4 | 0 | 0 | 0 | 1 | 3 |
| `get_gear_stats` | 5 | 0 | 0 | 0 | 3 | 2 |
| `get_goals` | 31 | 0 | 0 | 0 | 25 | 6 |
| `get_golf_club_stats` | 26 | 0 | 0 | 0 | 20 | 6 |
| `get_golf_scorecard` | 146 | 0 | 0 | 0 | 127 | 19 |
| `get_golf_shot_data` | 25 | 0 | 0 | 0 | 16 | 9 |
| `get_golf_summary` | 22 | 0 | 0 | 0 | 17 | 5 |
| `get_golf_user_stats` | 41 | 0 | 0 | 0 | 34 | 7 |
| `get_heart_rate_zones` | 13 | 0 | 0 | 0 | 12 | 1 |
| `get_heart_rates` | 17 | 0 | 6 | 0 | 6 | 5 |
| `get_hill_score` | 19 | 0 | 0 | 0 | 12 | 7 |
| `get_hrv_data` | 27 | 0 | 3 | 0 | 18 | 6 |
| `get_hrv_data_range` | 15 | 0 | 0 | 0 | 10 | 5 |
| `get_hydration_data` | 15 | 2 | 0 | 0 | 11 | 2 |
| `get_in_progress_badges` | 53 | 0 | 0 | 0 | 36 | 17 |
| `get_inprogress_virtual_challenges` | 8 | 0 | 0 | 0 | 5 | 3 |
| `get_intensity_minutes_data` | 9 | 0 | 0 | 0 | 8 | 1 |
| `get_lactate_threshold` | 36 | 0 | 0 | 0 | 27 | 9 |
| `get_last_activity` | 197 | 32 | 2 | 0 | 130 | 33 |
| `get_lifestyle_logging_data` | 9 | 0 | 0 | 0 | 5 | 4 |
| `get_max_metrics` | 32 | 0 | 2 | 0 | 27 | 3 |
| `get_max_metrics_range` | 32 | 0 | 2 | 0 | 27 | 3 |
| `get_menstrual_calendar_data` | 8 | 0 | 0 | 0 | 3 | 5 |
| `get_menstrual_data_for_date` | 24 | 0 | 0 | 7 | 15 | 2 |
| `get_morning_training_readiness` | 35 | 0 | 0 | 0 | 33 | 2 |
| `get_next_scheduled_workout` | 20 | 0 | 0 | 0 | 15 | 5 |
| `get_non_completed_badge_challenges` | 13 | 0 | 0 | 0 | 8 | 5 |
| `get_nutrition_daily_food_log` | 27 | 0 | 0 | 7 | 10 | 10 |
| `get_nutrition_daily_meals` | 6 | 0 | 0 | 0 | 3 | 3 |
| `get_nutrition_daily_settings` | 6 | 0 | 0 | 0 | 5 | 1 |
| `get_personal_record` | 21 | 0 | 0 | 0 | 16 | 5 |
| `get_primary_training_device` | 25 | 2 | 0 | 0 | 12 | 11 |
| `get_progress_summary_between_dates` | 11 | 0 | 0 | 0 | 9 | 2 |
| `get_race_predictions` | 8 | 0 | 0 | 0 | 7 | 1 |
| `get_respiration_data` | 27 | 0 | 2 | 0 | 20 | 5 |
| `get_rhr_daily` | 3 | 0 | 0 | 0 | 2 | 1 |
| `get_rhr_day` | 9 | 0 | 0 | 0 | 5 | 4 |
| `get_running_tolerance` | 12 | 0 | 0 | 0 | 11 | 1 |
| `get_scheduled_workout_by_id` | 1 | 0 | 0 | 0 | 0 | 1 |
| `get_scheduled_workouts` | 80 | 0 | 0 | 0 | 65 | 15 |
| `get_sleep_daily` | 3 | 0 | 0 | 0 | 1 | 2 |
| `get_sleep_data` | 170 | 10 | 5 | 0 | 116 | 39 |
| `get_spo2_data` | 30 | 0 | 0 | 0 | 28 | 2 |
| `get_stats` | 119 | 0 | 9 | 0 | 99 | 11 |
| `get_stats_and_body` | 130 | 0 | 9 | 0 | 110 | 11 |
| `get_steps_data` | 7 | 0 | 3 | 0 | 3 | 1 |
| `get_stress_data` | 24 | 0 | 0 | 0 | 15 | 9 |
| `get_training_four_week_load_balance` | 21 | 0 | 0 | 0 | 15 | 6 |
| `get_training_load_activities` | 13 | 0 | 0 | 0 | 10 | 3 |
| `get_training_plans` | 5 | 0 | 0 | 0 | 2 | 3 |
| `get_training_readiness` | 36 | 0 | 0 | 0 | 33 | 3 |
| `get_training_status` | 92 | 0 | 0 | 0 | 73 | 19 |
| `get_unit_system` | 1 | 0 | 0 | 0 | 1 | 0 |
| `get_user_profile` | 74 | 0 | 0 | 3 | 58 | 13 |
| `get_user_summary` | 119 | 0 | 9 | 0 | 99 | 11 |
| `get_userprofile_settings` | 48 | 0 | 0 | 1 | 35 | 12 |
| `get_weekly_intensity_minutes` | 5 | 0 | 0 | 0 | 4 | 1 |
| `get_weekly_steps` | 8 | 0 | 0 | 0 | 6 | 2 |
| `get_weekly_stress` | 3 | 0 | 0 | 0 | 2 | 1 |
| `get_weigh_ins` | 83 | 5 | 3 | 0 | 64 | 11 |
| `get_workout_by_id` | 95 | 0 | 0 | 0 | 62 | 33 |
| `get_workouts` | 42 | 0 | 0 | 0 | 25 | 17 |

### Methods with no field evidence (zero documented fields)

`get_training_plan_by_id`, `get_adaptive_training_plan_by_id`, `download_workout`, `get_activities_fordate`, `get_power_zones`, `get_power_zones_for_sport`, `get_menstrual_cycle_summary`, `get_menstrual_last_confirmed`, `get_menstrual_reports`, `get_pregnancy_summary`
