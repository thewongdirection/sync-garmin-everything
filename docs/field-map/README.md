# Field map: provenance, confidence and limits

| File | What it is |
|---|---|
| [FIELD-MAP.md](FIELD-MAP.md) | Readable map: numbers, one table per Google-writable data type, the Health Connect-only table, and the archive-only breakdown. Generated. |
| [garmin-fields.csv](garmin-fields.csv) | **Every Garmin field** (4,567 documented fields across 158 methods and FIT message types, plus 10 methods with no field evidence): type, unit, meaning, confidence, evidence, and its destination (`tier`, `google_target`, `hc_target`, `transform`, `reason`). |
| [google-fields.csv](google-fields.csv) | **Every Google Health API v4 payload field** (1,082 across 48 data types plus Profile/Settings/Identity/PairedDevice), with `status` (FILLED / opt-in / no Garmin source / output-only / read-only type) and the Garmin fields that fill it. |
| `tools/field_map/generate_docs.py` | Regenerates `FIELD-MAP.md` from the two CSVs. The CSVs are the source of truth: edit a row, run the script. The classification was first produced by hand-written rules over scratch evidence files that are not kept; `garmin-sync inventory` is how new fields are found from now on. |

## Destination tiers (`tier` column)

| Tier | Meaning |
|---|---|
| `G+H` | Written to the Google Health API **and** exported for Health Connect |
| `G` | Written to the Google Health API |
| `H` | Health Connect export only: Google's API cannot write this data type |
| `GO` | Opt-in only: sensitive (nutrition, cycle tracking) or changes your Google profile/settings |
| `A` | Archived locally only; no Google or Health Connect equivalent. Reason in `reason` |
| `M` | Housekeeping: identifiers, container nodes, social/profile noise |
| `N` | Method with no field evidence anywhere |

## Where the data came from

**Google (authoritative).** Parsed from Google's machine-readable Discovery documents
(`https://health.googleapis.com/$discovery/rest?version=v4` and `v4beta`) plus the public data-types page
for which operations each type supports. Every Google field named in the mapping rules is checked against
this list by the build, so a rule cannot point at a field that does not exist.

**Garmin (best available evidence, not a Garmin schema).** Garmin publishes no schema and python-garminconnect
returns raw JSON, so the inventory is assembled from independent evidence, each field cited:

* `observed` (1,923 fields): key paths present in recorded real API responses (garth's test cassettes and a
  public golf recording), matched to library methods by exact URL. Keys and types only; no values were kept.
* `code` (2,253): keys read or declared by third-party parsers of the real responses (GarminDB, garmin-health-data,
  garmy, garmin-grafana, Home Assistant integrations, several Go clients), cited by file and line or URL.
* `inferred` (391): strongly implied, with the reason stated. Includes FIT fields known only from the FIT SDK profile.

## Limits you should know about

1. **"Every field" is bounded by public evidence.** Fields that only appear for certain devices, firmware or
   features (and the 10 methods with no evidence) cannot be enumerated without an account. The plan closes this with
   an `inventory` command that records the key paths/types (never values) your own account returns and diffs them
   against this map, flagging unmapped fields.
2. **Shapes drift.** Garmin changes responses without notice; some keys differ between the flat activity list and
   the single-activity `summaryDTO`, and some endpoints vary by device (e.g. sleep stage codes depend on REM support).
3. **Mappings marked `DECISION`, `ASSUMES`, `UNVERIFIED` or `EVIDENCE THIN` in `transform` are proposals** that
   need your confirmation or a real-account check.
4. Garmin Connect already syncs some data to Google. The Google sink skips anything Google already holds, so
   "FILLED" means *eligible*, not *will always be written*.

## What the code implements today

The map says where each field *can* go. The first release implements all of the Google-writable and
Health Connect-exportable fields that come from the regular JSON endpoints. Not yet implemented:
fields that exist only in downloaded FIT files (GPS routes, per-second heart rate, speed, power, cadence),
intraday floors, the opt-in tier (`GO`: nutrition, cycle tracking, height, profile and settings), and the
Garmin device display name. Pause/resume events are sent only when every event key is recognised.
