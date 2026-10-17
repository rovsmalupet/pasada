# Traffic observations for PASADA-abm

This folder contains copies of the completed RF-DETR/ByteTrack traffic outputs and provisional speed estimates. The original source files remain intact. `package_manifest.json` records original paths, SHA-256 checksums and file sizes for the handoff.

## Before calculating or recalculating speeds

**The distance between the timing boundaries must first be measured, calculated from a calibrated reference, or manually estimated.** `generate_speeds.py` uses that supplied distance with the recorded entry/exit times; it does not determine a real-world distance in meters from the CSV or video automatically.

For this recording, Matthew manually estimated each whole road box as approximately **27–29 meters** and selected **28 meters** as the central value for both directions. This was a manual approximation, not a surveyed distance or a detector measurement. The exported speeds depend on that assumption.

Before running the Python speed generator for new or revised data:

1. Identify the actual start/end boundaries and determine their road distance along the direction of travel for each carriageway. Record the measurement or estimation method, units and plausible range.
2. Supply the distance that matches those timestamps. Actual full-box entry/exit timestamps use the full box length. This dataset's saved inner-80% gate timestamps instead use **22.4 m** under the **28 m** whole-box assumption (21.6–23.2 m for the 27–29 m scenarios).
3. Record the timed section length in `distance_m` or the saved gate file's `gate_distance_m`. If the whole-box estimate changes, regenerate the gate-distance export using the new calibration before running `generate_speeds.py`; existing saved gate distances otherwise retain their old values.
4. Run the speed calculation only after that distance has been determined and recorded. The Python defaults retain the earlier manual estimate and do not verify it. A different location, box, camera view or recording requires its own distance assessment.

An estimated distance produces an estimated speed. The low/high speed columns describe sensitivity to the assumed distance, not full uncertainty bounds. A distance-only revision needs a speed recalculation, without repeating vehicle detection or traffic counts.

## Files

| File | Purpose |
| --- | --- |
| `vehicle_events.csv` | Primary count ledger: one row per recorded crossing, with video-relative seconds, vehicle type, direction, counting-line ID and tracking ID. Use this for direction-specific traffic demand and calibration. |
| `vehicles_per_min.csv` | Compact summary: minute, total vehicles, cars, buses, motorcycles, trucks and predicted jeepneys. This combines both traffic directions. |
| `vehicle_speeds.csv` | Average longitudinal speed estimates with entry/exit seconds, timed distance, distance scenarios and status. Missing or rejected speeds are blank. |
| `vehicle_tracking_times.csv` | Supporting audit: first/last tracked appearances, source/run identity and track evidence. These appearance times are not necessarily crossings of a measured section. |
| `metadata/counts_source.json` | Source identity, count/category verification, recording duration and published count-file checksum. |
| `metadata/speeds_source.json` | Provenance and checksums for the standalone Python speed-CSV calculation. |
| `metadata/tracking_summary.json` | Completed full-run summary, detector/counting geometry and processing details. |
| `metadata/tracking_verification.json` | Verification of every track's first/last seconds against saved observations and of all minute/category totals. |
| `metadata/speed_calibration_and_method.json` | Assumed road lengths, perspective mapping, interior timing gates, source hashes and speed-method limitations. |

The source recording is `DJI_20261005131923_0003_D.MP4`. Its duration is **2143.89175 seconds (35:43.89175)**. There are 1,574 recorded crossings and 2,011 tracked appearances. The speed export contains 1,322 provisional estimates; 689 rows have blank speeds with reasons. Track rows and crossing counts describe different populations and must not be substituted for one another.

Minute 1 is `[0,60)` seconds after video start. Minute 36 is `[2100,2143.89175)` and lasts 43.89175 seconds. Use the actual interval duration when deriving rates. Neither the video filename nor video-relative times establish a verified recording location or wall-clock observation period.

## Classification and speed limitations

Cars + buses + motorcycles + trucks equal total vehicles in the minute file. Predicted jeepneys are an overlapping subset of those original classes and are already included in total vehicles. The jeepney predictions are not human-verified labels, so adding them again would double-count crossings. The count ledger remains in the original RF-DETR classes.

The user estimates each whole road box as 27–29 m; the central assumption is 28 m. Speeds use the inner 80% gates, so the timed section is 22.4 m under that assumption. Low/high columns vary distance alone and are not confidence intervals. Road measurements, perspective assumptions, bounding-box anchors, tracking reliability and drone movement have not been independently validated. There is no camera stabilization in these estimates.

Source/run IDs scope the tracking identities. Do not join tracks from different runs using the numeric tracking ID alone. Some copied provenance fields and `crop_paths` reference original local files; the video, crop images, SQLite observations, models and environments are not included in this handoff folder.

## Integration status

This is a data handoff folder under `sumo_track/data/real/traffic_count_data/`.

The current ABM manifest reserves `data/real/traffic_counts.csv` but sets `traffic_counts.active: none`. A count importer and final input schema are still to be implemented. This folder does not change that manifest or activate the data in the simulation.

The next integration step is to map the observed counting lines/directions and recording site to the correct SUMO edges, aggregate `vehicle_events.csv` into the required time/type/direction intervals, and connect those records to the background-flow generator. Preserve the recording duration and source provenance. Populate the configured canonical input and activate it only after that importer and mapping are ready. Speeds are optional exploratory calibration data.
