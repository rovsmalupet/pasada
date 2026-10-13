"""Independent full-source audit using Pandas and intersects_xy, plus edge sensitivity."""
import argparse
import csv
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import time
import zipfile

import numpy as np
import pandas as pd
import pyproj
import shapely
from shapely.ops import transform

from extract_santa_rosa import load_boundary, sha256


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--summary', type=Path, required=True)
    parser.add_argument('--workspace-csv', type=Path)
    args = parser.parse_args()
    start = time.perf_counter()
    summary = json.loads(args.summary.read_text())
    source = Path(summary['source_path'])
    output = Path(summary['output_path'])
    geometry, _, _ = load_boundary(Path(summary['boundary_path']))
    to_meters = pyproj.Transformer.from_crs(4326, 32651, always_xy=True)
    to_degrees = pyproj.Transformer.from_crs(32651, 4326, always_xy=True)
    projected = transform(to_meters.transform, geometry)
    expanded = transform(to_degrees.transform, projected.buffer(100))
    envelopes = {m: projected.buffer(m) for m in (-100, -50, 0, 50, 100)}
    bounds = expanded.bounds
    names = summary['column_roles']
    counts = {m: 0 for m in envelopes}
    totals = {m: Decimal(0) for m in envelopes}
    processed = selected = 0
    with source.open('rb') as check:
        assert hashlib.file_digest(check, 'sha256').hexdigest() == summary['source_sha256']
    with output.open(newline='') as f, zipfile.ZipFile(source) as archive:
        reader = csv.reader(f)
        assert next(reader) == summary['source_columns']
        with archive.open(summary['zip_member']) as stream:
            for chunk in pd.read_csv(stream, dtype=str, keep_default_na=False, chunksize=100000):
                processed += len(chunk)
                lon = pd.to_numeric(chunk[names['longitude']], errors='raise').to_numpy()
                lat = pd.to_numeric(chunk[names['latitude']], errors='raise').to_numpy()
                assert np.isfinite(lon).all() and np.isfinite(lat).all()
                mask = ((lon >= bounds[0]) & (lon <= bounds[2]) & (lat >= bounds[1]) & (lat <= bounds[3]))
                candidate = chunk.loc[mask]
                x, y = lon[mask], lat[mask]
                inside = shapely.intersects_xy(geometry, x, y)
                for row in candidate.loc[inside].itertuples(index=False, name=None):
                    assert next(reader, None) == list(row), 'Output differs from independent source selection'
                    selected += 1
                if len(candidate):
                    mx, my = to_meters.transform(x, y)
                    populations = [Decimal(v) for v in candidate[names['population']]]
                    for meters, envelope in envelopes.items():
                        hit = shapely.intersects_xy(envelope, mx, my)
                        counts[meters] += int(hit.sum())
                        totals[meters] += sum((p for p, keep in zip(populations, hit) if keep), Decimal(0))
                if processed % 2000000 == 0:
                    print(f'Audited {processed:,} source rows; matched {selected:,} output rows', flush=True)
        assert next(reader, None) is None, 'Output has extra rows'
        assert selected == summary['santa_rosa_rows_extracted']
        assert processed == summary['original_rows_processed']
        h = hashlib.sha256()
        with archive.open(summary['zip_member']) as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                h.update(block)
        member_hash = h.hexdigest()
    workspace_check = None
    if args.workspace_csv:
        workspace_check = {'path': str(args.workspace_csv.resolve()),
                           'sha256': sha256(args.workspace_csv),
                           'size_bytes': args.workspace_csv.stat().st_size}
        assert workspace_check['sha256'] == member_hash, 'ZIP and existing CSV differ'
    sensitivity = [{'boundary_buffer_meters': m, 'rows': counts[m],
                    'population_exact_decimal': str(totals[m]),
                    'population_difference_from_actual': float(totals[m] - Decimal(summary['sum_population_estimates_exact_decimal']))}
                   for m in envelopes]
    audit = {'all_output_records_match_independent_full_source_selection_in_order': True,
             'all_population_strings_and_all_other_fields_unchanged': True,
             'no_missing_selected_source_records': True, 'no_extra_output_records': True,
             'independent_predicate': 'Shapely intersects_xy(polygon, longitude, latitude)',
             'input_records_audited': processed, 'selected_records_matched': selected,
             'source_uncompressed_csv_sha256': member_hash, 'workspace_csv_matches_zip_member': workspace_check,
             'boundary_area_sqkm_epsg32651': projected.area / 1e6,
             'boundary_perimeter_km_epsg32651': projected.length / 1000,
             'boundary_sensitivity_diagnostics': sensitivity,
             'diagnostic_note': 'Buffers are sensitivity checks only. The delivered CSV uses the original unbuffered polygon.',
             'audit_execution_time_seconds': round(time.perf_counter() - start, 3)}
    path = args.summary.parent / 'Santa_Rosa_Independent_Validation_2020.json'
    path.write_text(json.dumps(audit, indent=2) + '\n')
    summary['independent_validation_report'] = str(path.resolve())
    summary['validation']['independent_full_source_selection_matches'] = True
    summary['source_uncompressed_csv_sha256'] = member_hash
    summary['psa_2020_reasonableness_check']['investigation'] = {
        'independent_full_source_audit_passed': True,
        'boundary_feature_valid_on': summary['boundary_provenance']['selected_feature_properties'].get('valid_on'),
        'population_with_100_meter_diagnostic_expansion': float(totals[100]),
        'additional_population_from_100_meter_expansion': float(totals[100] - Decimal(summary['sum_population_estimates_exact_decimal'])),
        'conclusion': 'A 100-m expansion explains only part of the census gap. Exact cause remains unresolved; model/census input vintage and boundary differences are possible contributors. Output is unbuffered and unscaled.',
        'methodology_source': 'https://ai.meta.com/ai-for-good/docs/methodology-high-resolution-population-density-maps/',
        'methodology_context': 'Meta describes estimates based on Gridded Population of the World, census information and imagery-derived settlements; the 2020 label does not establish use of the PSA 2020 census.',
    }
    args.summary.write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(audit, indent=2))


if __name__ == '__main__':
    main()
