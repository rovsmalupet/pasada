# Santa Rosa City population-grid extraction (2020)

This project filters Meta's Philippines 2020 general population grid to the complete City of Santa Rosa, Laguna administrative polygon. It preserves every original column, row order, and field string, including fractional and zero population values. Each row is a grid-cell estimate, not a person. No population values, cell locations, or spatial resolution are changed.

## Results from the actual run

Completed on **13 October 2026, Asia/Manila**.

| Item | Result |
|---|---|
| Source | `/Users/matthewfuentes/Downloads/phl_general_2020_csv.zip` |
| Source ZIP size | 47,496,079 bytes |
| ZIP member | `phl_general_2020.csv`, 470,681,504 bytes |
| Original columns | `longitude,latitude,phl_general_2020` |
| Source records processed | 10,110,681 |
| Bounding-box candidates | 47,449 |
| Exact polygon matches | 19,788 |
| Sum of population estimates | **362,757.890228** |
| Minimum / maximum population per cell | 0.0 / 114.344461 |
| Zero population cells retained | 136 |
| Fractional population cells retained | 19,652 |
| Invalid or rejected rows | 0 |
| Duplicate output records | 0 |
| Output size | 932,795 bytes |
| Extraction and validation time, including preview | 97.685 seconds |
| Independent audit time | 30.834 seconds |

The existing workspace `phl_general_2020.csv` and the ZIP's uncompressed member have the same SHA-256: `c32662f03749d1ccd5b6148a3592295ffd47b3b367ceafe4eed6c19ef45a657a`. No additional population data was downloaded, and the originals were not modified.

The output is `output/Santa_Rosa_Population_Density_2020.csv`. Its full path is `/Users/matthewfuentes/Documents/Thesis/population_density/population_extraction/output/Santa_Rosa_Population_Density_2020.csv`.

## Reproduce

The isolated `.venv` is installed and ready. Run this one-line command from any directory:

```sh
"/Users/matthewfuentes/Documents/Thesis/population_density/population_extraction/.venv/bin/python" "/Users/matthewfuentes/Documents/Thesis/population_density/population_extraction/extract_santa_rosa.py" --input "/Users/matthewfuentes/Downloads/phl_general_2020_csv.zip" --output "/Users/matthewfuentes/Documents/Thesis/population_density/population_extraction/output/Santa_Rosa_Population_Density_2020.csv" --chunksize 100000 --preview
```

The script accepts either CSV or ZIP input. To use the existing uncompressed file, replace `--input` with `/Users/matthewfuentes/Documents/Thesis/population_density/phl_general_2020.csv`. The verified saved polygon is the default boundary. `--boundary` accepts another verified city GeoJSON with a matching `.metadata.json` sidecar. The sidecar must provide source, version, acquisition date, license, PSGC, and the geometry-file checksum. The script verifies the feature's city name, Laguna province and recognized administrative code, checks geometry validity, and transforms declared CRS to WGS84 with longitude first. It fails closed on unverifiable columns, boundary, ambiguous CSV members, or structural CSV parsing failures. `--zip-member` resolves archives containing multiple candidates. `--preview` is optional.

For a fresh environment, run the following from `population_extraction/` with Python 3.13 (the version used here):

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

`requirements.lock.txt` records every installed package in the actual environment. No existing Conda or thesis environment was changed.

Run the independent audit from the workspace root:

```sh
population_extraction/.venv/bin/python population_extraction/verify_extraction.py --summary population_extraction/output/Santa_Rosa_Population_Summary_2020.json --workspace-csv phl_general_2020.csv
```

That audit is tailored to this verified ZIP and valid source. It rereads all national records with Pandas, uses the separately applied boundary-inclusive `intersects_xy` predicate, and compares all selected field strings to output records in order. It detects missing, extra, changed, or reordered records. It also hashes the uncompressed ZIP member and checks the workspace CSV. Rerun it after extraction to refresh the independent report.

Edge-case tests:

```sh
population_extraction/.venv/bin/python -m unittest discover -s population_extraction -p 'test_*.py' -v
```

All four tests passed: multipart geometry and holes, inclusion of exterior/hole boundaries, exact small fractions and zeroes, invalid values, preservation of source duplicates, ZIP disambiguation, projected-CRS transformation, and refusal of wrong-city/tampered boundaries or unverifiable columns.

## Population source provenance

Source: [Meta/Facebook population data on HDX](https://data.humdata.org/dataset/philippines-high-resolution-population-density-maps-demographic-estimates). HDX lists `phl_general_2020_csv.zip` as 47,496,079 bytes, last modified 16 December 2022. Its license is listed as Creative Commons Attribution International (CC BY). The saved HDX API snapshot and summary retain the resource URL, ID, date and license information. The local filename, header and size match that resource; HDX provides no published SHA-256 for comparison.

The dataset is labeled 2020. That year is not a claim that the source reproduces PSA's 2020 census. Meta's [methodology](https://ai.meta.com/ai-for-good/docs/methodology-high-resolution-population-density-maps/) describes population estimates based on Gridded Population of the World, census information, and imagery-derived settlements. The user's [technical documentation link](https://ai.meta.com/ai-for-good/docs/high-resolution-population-density-maps-demographic-estimates-documentation/) is also recorded in the summary. These sources describe approximately 30-meter cells with estimated people per cell.

## Boundary provenance and target verification

Source: [Philippines COD-AB administrative boundaries on HDX](https://data.humdata.org/dataset/cod-ab-phl), sourced from **NAMRIA and PSA**, contributed by OCHA Philippines. The downloaded resource is `phl_admin_boundaries.geojson.zip`, last modified **28 May 2026**. Its dataset and feature version is **03 / v03**. Acquired **13 October 2026** under **CC BY-IGO**, with the [CC BY 3.0 IGO legal code](http://creativecommons.org/licenses/by/3.0/igo/legalcode) specified in HDX metadata. Attribute the source organizations and OCHA/HDX when reusing the boundary.

Selected feature:

- Name: `City of Santa Rosa`
- Province: `Laguna`, `PH04034`
- Region: `Region IV-A (Calabarzon)`
- ADM3 code: `PH0403428`
- PSGC: **0403428000**, correspondence code **043428000**
- Feature `valid_on`: **2025-02-13**
- Valid Polygon, 2,545 exterior vertices, WGS84, full ADM3 feature without clipping, simplification or repairs.
- Bounding box, longitude/latitude: `[121.04637207600001, 14.216109864000089, 121.13056747300004, 14.336565695000104]`.

The HDX code is `PH` plus the 10-digit PSGC with its three city-level trailing zeroes omitted. PSA independently confirms both the [city code and 18 barangays](https://psa.gov.ph/classification/psgc/barangays/0403428000) and [membership in Laguna](https://psa.gov.ph/classification/psgc/citimuni/0403400000). The complete city feature is used; no barangay subset is selected.

**Temporal limitation:** HDX's general notes say boundaries were created/edited 9 February 2018, valid for humanitarian use 26 March 2018, and reviewed 1 April 2024. The actual downloaded feature says valid on 13 February 2025. Both are recorded verbatim in provenance. This is a verified administrative polygon, but not a certified 2020 boundary snapshot. No older local geographic dataset was found; the available PSGC spreadsheets are tables, not polygons.

`obtain_boundary.py` used checked HTTP range requests and ZIP CRC checks to retrieve ADM3 without downloading the approximately 1.06 GB archive. The acquired national layer was removed after the 238 KB city feature and full provenance/checksums were saved. Running the acquisition script again reuses the saved verified city feature. `--refresh` reacquires from the saved HDX resource snapshot, and `--keep-layer` retains the national layer if needed. A fresh acquisition temporarily needs about 556 MB of disk space and memory for JSON parsing. Ordinary extraction needs only the saved city polygon.

## Selection and integrity

The extractor streams the ZIP member through Python's CSV reader in 100,000-row chunks. All fields remain strings for writing. Numeric copies are used for validation and filtering only. The bounding box is a performance prefilter; **Shapely `covers(polygon, point)` is the final predicate**, including exact boundary points and respecting holes/multipart geometry. Longitude is X; latitude is Y. Population totals use Decimal arithmetic and include an exact decimal string in summary metadata.

Cells are selected by their centers. Cells straddling the boundary are not proportionally divided. No missing cells are created, neighboring cells aggregated, values rounded, estimates normalized, or source records deduplicated. Valid source duplicates would be retained and reported. Coordinates outside global geographic ranges, nonnumeric/nonfinite values, negative population, or wrong field counts are rejected and logged with original fields and source-record numbers. Structural CSV errors stop the run. This input contained no invalid records.

The output is validated before replacing its final CSV path. Checks cover every output point, nonempty content, header and record count, source-selected versus output field hashes, duplicate multiplicities, Pandas readability and source immutability. The independent complete-source audit adds assurance that all and only eligible source rows were retained. The PNG was visually reviewed.

## Census discrepancy

PSA reports **414,812** for Santa Rosa, Laguna in its [2020 census release](https://rsso04a.psa.gov.ph/content/highlights-region-iv-calabarzon-population-2020-census-population-and-housing-2020-cph). The grid sum is **52,054.109772 lower (12.5488437586%)**. This is a material difference, not an extraction error identified by validation.

As a diagnostic only, an EPSG:32651 buffer test gave:

| Change to city boundary | Estimated population |
|---|---:|
| Contract 100 m | 352,568.654129 |
| Contract 50 m | 358,053.735289 |
| Original polygon, delivered CSV | **362,757.890228** |
| Expand 50 m | 369,803.352685 |
| Expand 100 m | 378,170.870629 |

A 100 m expansion adds 15,412.980401, accounting for about 30% of the census gap. This indicates that small boundary differences can matter but do not explain the full gap in this test. Possible remaining contributors include model/census input vintage, settlement detection/distribution and larger boundary differences. The exact cause is unresolved. Buffered results include neighboring territory and are **not** alternative city counts. No buffered selection, census rescaling, 2024 population totals, or invented values were used in the delivered CSV.

## Files

- `extract_santa_rosa.py`: reusable ZIP/CSV streaming extractor.
- `verify_extraction.py`: independent source audit and boundary sensitivity diagnostics.
- `obtain_boundary.py`: boundary acquisition/reuse.
- `boundaries/Santa_Rosa_City_Laguna.geojson`: complete verified city polygon.
- `boundaries/Santa_Rosa_City_Laguna.metadata.json`: geographic provenance and checksum.
- `output/Santa_Rosa_Population_Density_2020.csv`: primary deliverable.
- `output/Santa_Rosa_Population_Summary_2020.json`: computed statistics, methodology, provenance and validation.
- `output/Santa_Rosa_Independent_Validation_2020.json`: full-source audit and sensitivity results.
- `output/Santa_Rosa_Rejected_Rows_2020.jsonl`: empty because no invalid rows were found.
- `output/Santa_Rosa_Population_Preview.png`: geographic preview; all 19,788 centers are practical to display.
- Run logs, dependency requirements and edge-case tests.

No commuter generation, households, occupations, census-ratio changes or changes to other thesis scripts are included.
