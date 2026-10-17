# Traffic observations for PASADA-abm

This folder contains copies of the completed RF-DETR/ByteTrack traffic outputs and provisional speed estimates. The original source files remain intact. `package_manifest.json` records original paths, SHA-256 checksums and file sizes for the handoff.

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
