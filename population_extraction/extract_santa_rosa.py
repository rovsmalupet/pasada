#!/usr/bin/env python3
"""Stream original population fields through a verified boundary-inclusive filter."""
import argparse
from collections import Counter
from contextlib import contextmanager
import csv
from datetime import datetime
from decimal import Decimal, localcontext
import hashlib
import io
from itertools import islice
import json
import logging
import math
from pathlib import Path
import platform
import time
import zipfile
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pyproj
import shapely
from shapely.geometry import shape
from shapely.ops import transform

ROOT = Path(__file__).resolve().parent
LOG = logging.getLogger('population_extraction')
PSGC = '0403428000'
CENSUS_URL = 'https://rsso04a.psa.gov.ph/content/highlights-region-iv-calabarzon-population-2020-census-population-and-housing-2020-cph'
SOURCE_URL = 'https://data.humdata.org/dataset/philippines-high-resolution-population-density-maps-demographic-estimates'


def sha256(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def row_digest(digest, row):
    # Canonical field framing is independent of CSV quoting and newline style.
    digest.update(json.dumps(row, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
    digest.update(b'\n')


@contextmanager
def open_source(path, member=None):
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            inventory = [{'name': x.filename, 'size_bytes': x.file_size,
                          'compressed_bytes': x.compress_size} for x in z.infolist()]
            csvs = [x.filename for x in z.infolist() if not x.is_dir()
                    and x.filename.lower().endswith('.csv')]
            general = [x for x in csvs if Path(x).name.lower() == 'phl_general_2020.csv']
            if member is None:
                if len(general) == 1:
                    member = general[0]
                elif len(csvs) == 1:
                    member = csvs[0]
                else:
                    raise ValueError(f'Ambiguous ZIP CSV members: {csvs}; specify --zip-member')
            if member not in csvs:
                raise ValueError('Requested ZIP member is not a CSV in the archive')
            LOG.info('ZIP inventory: %s; selected %s', inventory, member)
            with z.open(member) as binary, io.TextIOWrapper(binary, encoding='utf-8-sig', newline='') as f:
                yield f, {'zip_member': member, 'uncompressed_csv_bytes': z.getinfo(member).file_size,
                          'archive_inventory': inventory}
    else:
        if member:
            raise ValueError('--zip-member requires ZIP input')
        with path.open(encoding='utf-8-sig', newline='') as f:
            yield f, {'zip_member': None, 'uncompressed_csv_bytes': path.stat().st_size}


def detect_columns(header):
    if not header or len(header) != len(set(header)):
        raise ValueError('Missing or duplicate CSV columns')
    normalized = {x.strip().lower(): x for x in header}
    if len(normalized) != len(header):
        raise ValueError('Ambiguous case/whitespace variants in CSV header')
    def one(names, role):
        matches = [normalized[x] for x in names if x in normalized]
        if len(matches) != 1:
            raise ValueError(f'Cannot verify {role} column: {header}')
        return matches[0]
    return {
        'longitude': one(['longitude', 'lon', 'long', 'lng'], 'longitude'),
        'latitude': one(['latitude', 'lat'], 'latitude'),
        'population': one(['phl_general_2020', 'population'], '2020 general population'),
    }


def load_boundary(path):
    if path.suffix.lower() not in ('.geojson', '.json'):
        raise ValueError('Supply a verified Santa Rosa GeoJSON and its .metadata.json sidecar')
    metadata_path = path.with_suffix('.metadata.json')
    if not metadata_path.exists():
        raise ValueError(f'Boundary provenance sidecar missing: {metadata_path}')
    provenance = json.loads(metadata_path.read_text())
    required = ['source_url', 'dataset_version', 'acquisition_date', 'license', 'license_url']
    if any(not provenance.get(k) for k in required) or provenance.get('psgc_code') != PSGC:
        raise ValueError('Incomplete boundary provenance or wrong PSGC')
    if provenance.get('selected_boundary_sha256') != sha256(path):
        raise ValueError('Boundary checksum does not match verified provenance')
    data = json.loads(path.read_text())
    features = data.get('features', [])
    if len(features) != 1:
        raise ValueError('Expected a single complete city-level boundary feature')
    p = features[0]['properties']
    if (p.get('adm3_pcode') not in ('PH0403428', 'PH043428000', 'PH0403428000')
            or p.get('adm2_name') != 'Laguna'
            or 'santa rosa' not in p.get('adm3_name', '').lower()):
        raise ValueError('Boundary name, province and administrative code do not identify Santa Rosa')
    crs_info = data.get('crs')
    source_crs = pyproj.CRS.from_user_input(crs_info['properties']['name'] if crs_info else 'EPSG:4326')
    geometry = shape(features[0]['geometry'])
    if geometry.geom_type not in ('Polygon', 'MultiPolygon') or geometry.is_empty or not geometry.is_valid:
        raise ValueError(f'Invalid polygon: {shapely.is_valid_reason(geometry)}')
    if not source_crs.equals(pyproj.CRS.from_epsg(4326), ignore_axis_order=True):
        geometry = transform(pyproj.Transformer.from_crs(source_crs, 4326, always_xy=True).transform, geometry)
    if not geometry.is_valid or geometry.is_empty:
        raise ValueError('Invalid transformed geometry')
    # This extent guard catches unrelated Santa Rosas and incorrect CRS/axis handling.
    minx, miny, maxx, maxy = geometry.bounds
    if not (120.9 < minx < maxx < 121.3 and 14.1 < miny < maxy < 14.5):
        raise ValueError(f'Boundary extent is incompatible with Santa Rosa, Laguna: {geometry.bounds}')
    shapely.prepare(geometry)
    LOG.info('Verified boundary %s, CRS %s → EPSG:4326, bounds %s', p['adm3_name'], source_crs, geometry.bounds)
    return geometry, provenance, source_crs.to_string()


def parse_record(row, header, cols):
    if len(row) != len(header):
        return None, 'wrong_field_count'
    try:
        lon, lat = float(row[cols['longitude']]), float(row[cols['latitude']])
    except (ValueError, OverflowError):
        return None, 'nonnumeric_coordinate'
    if not (math.isfinite(lon) and math.isfinite(lat) and -180 <= lon <= 180 and -90 <= lat <= 90):
        return None, 'invalid_coordinate_range_or_nonfinite'
    try:
        pop = Decimal(row[cols['population']])
    except ArithmeticError:
        return None, 'nonnumeric_population'
    if not pop.is_finite() or pop < 0:
        return None, 'negative_or_nonfinite_population'
    return (lon, lat, pop), None


def validate_output(path, header, cols, geometry, expected_hash, expected_rows):
    actual = hashlib.sha256()
    counts = Counter()
    outside = invalid = rows = 0
    # Only the small city output is held for duplicate audit, never the national source.
    with path.open(encoding='utf-8', newline='') as f:
        reader = csv.reader(f, strict=True)
        if next(reader) != header:
            raise ValueError('Output columns differ from source')
        for row in reader:
            parsed, error = parse_record(row, header, cols)
            invalid += bool(error)
            if parsed and not geometry.covers(shapely.Point(parsed[0], parsed[1])):
                outside += 1
            row_digest(actual, row)
            counts[tuple(row)] += 1
            rows += 1
    if not rows or rows != expected_rows or actual.hexdigest() != expected_hash or outside or invalid:
        raise ValueError('Output failed count, unchanged-fields, coordinate, or nonempty validation')
    pandas_rows = 0
    for chunk in pd.read_csv(path, dtype=str, keep_default_na=False, chunksize=100000):
        if list(chunk.columns) != header:
            raise ValueError('Pandas header validation failed')
        pandas_rows += len(chunk)
    if pandas_rows != rows:
        raise ValueError('Pandas row count differs')
    return {'exists': True, 'columns_match_source': True, 'not_empty': True,
            'all_points_covered_by_polygon': True, 'outside_polygon_rows': outside,
            'invalid_output_rows': invalid, 'pandas_readable': True,
            'pandas_rows': pandas_rows, 'all_original_field_strings_unchanged': True,
            'source_selected_fields_sha256': expected_hash,
            'output_fields_sha256': actual.hexdigest(),
            'duplicate_output_records': sum(n - 1 for n in counts.values())}, counts


def preview(path, csv_path, names, geometry, total):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.colors import LogNorm
    # Reservoir sampling keeps previews bounded even for much larger outputs.
    rng = np.random.default_rng(2020)
    sample, seen = [], 0
    for chunk in pd.read_csv(csv_path, dtype=str, keep_default_na=False, chunksize=50000):
        for row in chunk[[names['longitude'], names['latitude'], names['population']]].itertuples(index=False, name=None):
            values = tuple(map(float, row))
            seen += 1
            if len(sample) < 50000:
                sample.append(values)
            else:
                index = int(rng.integers(seen))
                if index < 50000:
                    sample[index] = values
    points = np.asarray(sample)
    fig, ax = plt.subplots(figsize=(8, 8))
    polys = list(geometry.geoms) if geometry.geom_type == 'MultiPolygon' else [geometry]
    for poly in polys:
        for ring in [poly.exterior, *poly.interiors]:
            x, y = ring.xy
            ax.plot(x, y, color='#26384c', linewidth=1.2)
    positive = points[:, 2] > 0
    if positive.any():
        plotted = ax.scatter(points[positive, 0], points[positive, 1], c=points[positive, 2],
                             s=2.5, cmap='viridis', norm=LogNorm(), linewidths=0)
        fig.colorbar(plotted, ax=ax, shrink=.72, label='Estimated people per source cell (log color scale)')
    if (~positive).any():
        ax.scatter(points[~positive, 0], points[~positive, 1], s=2.5, color='gray', label='Zero population')
    ax.set_aspect(1 / math.cos(math.radians(points[:, 1].mean())))
    ax.set(xlabel='Longitude (WGS84)', ylabel='Latitude (WGS84)',
           title=f'Santa Rosa City, Laguna • 2020 population grid\n{seen:,} retained cells • modeled total {total:,.2f}')
    fig.text(.5, .025, f'NAMRIA / PSA via OCHA HDX, COD-AB v03 • {len(sample):,} centers displayed\n'
             'Grid-center selection; boundary-straddling cells are not divided.', ha='center', fontsize=8)
    fig.tight_layout(rect=(0, .07, 1, 1))
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return {'path': str(path), 'displayed_points': len(sample), 'sampling_seed': 2020}


def extract(args):
    start = time.perf_counter()
    input_path, output = args.input.resolve(), args.output.resolve()
    if not input_path.is_file():
        raise ValueError(f'Input inaccessible: {input_path}')
    if input_path == output or args.boundary.resolve() == output:
        raise ValueError('Output must not overwrite source or boundary')
    if args.chunksize < 1:
        raise ValueError('--chunksize must be positive')
    geometry, provenance, boundary_crs = load_boundary(args.boundary.resolve())
    source_hash = sha256(input_path)
    source_stat = input_path.stat()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + '.partial')
    summary_path = output.parent / 'Santa_Rosa_Population_Summary_2020.json'
    invalid_path = output.parent / 'Santa_Rosa_Rejected_Rows_2020.jsonl'
    retained_hash = hashlib.sha256()
    selected_counts, errors = Counter(), Counter()
    processed = retained = bbox_rows = zero_rows = fractional_rows = 0
    total, pop_min, pop_max = Decimal(0), None, None
    extent = [math.inf, math.inf, -math.inf, -math.inf]
    minx, miny, maxx, maxy = geometry.bounds
    LOG.info('Source: %s (%s bytes); SHA256 %s', input_path, source_stat.st_size, source_hash)
    with localcontext() as ctx:
        ctx.prec = 80
        with open_source(input_path, args.zip_member) as (f, input_info), temporary.open('w', encoding='utf-8', newline='') as out, invalid_path.open('w', encoding='utf-8') as rejected:
            reader = csv.reader(f, strict=True)
            header = next(reader)
            names = detect_columns(header)
            cols = {k: header.index(v) for k, v in names.items()}
            LOG.info('Detected columns: %s; roles: %s', header, names)
            writer = csv.writer(out, lineterminator='\n')
            writer.writerow(header)
            chunk_number = 0
            while True:
                chunk = list(islice(reader, args.chunksize))
                if not chunk:
                    break
                candidates = []
                for row in chunk:
                    processed += 1
                    parsed, error = parse_record(row, header, cols)
                    if error:
                        errors[error] += 1
                        rejected.write(json.dumps({'source_record': processed, 'reason': error, 'original_fields': row}) + '\n')
                        continue
                    lon, lat, pop = parsed
                    if minx <= lon <= maxx and miny <= lat <= maxy:
                        candidates.append((row, lon, lat, pop))
                bbox_rows += len(candidates)
                if candidates:
                    points = shapely.points([r[1] for r in candidates], [r[2] for r in candidates])
                    mask = shapely.covers(geometry, points)
                    for item, keep in zip(candidates, mask):
                        if not keep:
                            continue
                        row, lon, lat, pop = item
                        writer.writerow(row)
                        row_digest(retained_hash, row)
                        selected_counts[tuple(row)] += 1
                        retained += 1
                        total += pop
                        zero_rows += pop == 0
                        fractional_rows += pop != pop.to_integral_value()
                        pop_min = pop if pop_min is None else min(pop_min, pop)
                        pop_max = pop if pop_max is None else max(pop_max, pop)
                        extent = [min(extent[0], lon), min(extent[1], lat), max(extent[2], lon), max(extent[3], lat)]
                chunk_number += 1
                if chunk_number == 1 or chunk_number % 4 == 0:
                    LOG.info('Chunks %s; processed %s; retained %s; rejected %s', chunk_number, processed, retained, sum(errors.values()))
    if not retained:
        raise ValueError('No cells retained; refusing to publish an empty dataset')
    LOG.info('Validating every output point and field; auditing duplicates and Pandas readability')
    validation, output_counts = validate_output(temporary, header, cols, geometry, retained_hash.hexdigest(), retained)
    if output_counts != selected_counts:
        raise ValueError('Output record multiplicities differ from selected source records')
    validation['source_selected_duplicate_records'] = sum(n - 1 for n in selected_counts.values())
    validation['accidental_duplicates_introduced'] = 0
    if sha256(input_path) != source_hash or input_path.stat().st_mtime_ns != source_stat.st_mtime_ns:
        raise ValueError('Source changed during extraction')
    validation['source_file_unchanged'] = True
    temporary.replace(output)
    diff = total - Decimal(414812)
    comparison = {'official_psa_2020_population': 414812, 'source_url': CENSUS_URL,
                  'difference': float(diff), 'difference_percent': float(diff / Decimal(414812) * 100),
                  'significant_discrepancy_threshold_percent': 10,
                  'significant_discrepancy': abs(diff / Decimal(414812)) > Decimal('.10'),
                  'interpretation': 'Modeled estimate, not a census count; no rescaling performed.'}
    summary = {
        'source_dataset_name': 'Meta High Resolution Population Density Maps and Demographic Estimates — Philippines general population 2020',
        'source_dataset_url': SOURCE_URL,
        'source_documentation_url': 'https://ai.meta.com/ai-for-good/docs/high-resolution-population-density-maps-demographic-estimates-documentation/',
        'source_path': str(input_path), 'source_size_bytes': source_stat.st_size, 'source_sha256': source_hash,
        **input_info, 'dataset_year': 2020, 'source_columns': header, 'column_roles': names,
        'geographic_target': 'City of Santa Rosa, Laguna, CALABARZON, Philippines', 'psgc_code': PSGC,
        'boundary_path': str(args.boundary.resolve()), 'boundary_dataset_source': provenance['source_url'],
        'boundary_dataset_version': provenance['dataset_version'], 'boundary_provenance': provenance,
        'boundary_original_crs': boundary_crs, 'coordinate_reference_system': 'EPSG:4326 / WGS84',
        'boundary_bounds_longitude_latitude': list(geometry.bounds),
        'extraction_method': 'Streaming CSV in chunks; bounding-box prefilter then Shapely covers(polygon, point); longitude=X, latitude=Y. Original field strings and order retained.',
        'grid_center_selection': True, 'boundary_straddling_cells_proportionally_divided': False,
        'original_rows_processed': processed, 'bbox_candidate_rows': bbox_rows, 'santa_rosa_rows_extracted': retained,
        'sum_population_estimates': float(total), 'sum_population_estimates_exact_decimal': str(total),
        'minimum_population': float(pop_min), 'maximum_population': float(pop_max),
        'minimum_population_exact_decimal': str(pop_min), 'maximum_population_exact_decimal': str(pop_max),
        'minimum_longitude': extent[0], 'minimum_latitude': extent[1], 'maximum_longitude': extent[2], 'maximum_latitude': extent[3],
        'zero_population_rows_retained': zero_rows, 'fractional_population_rows_retained': fractional_rows,
        'rejected_or_invalid_rows': sum(errors.values()), 'invalid_rows_by_reason': dict(errors),
        'invalid_rows_report': str(invalid_path), 'output_filename': output.name, 'output_path': str(output),
        'output_file_size_bytes': output.stat().st_size, 'output_sha256': sha256(output),
        'validation': validation, 'psa_2020_reasonableness_check': comparison,
        'completed_at': datetime.now(ZoneInfo('Asia/Manila')).isoformat(),
        'runtime': {'python': platform.python_version(), 'shapely': shapely.__version__, 'pandas': pd.__version__, 'pyproj': pyproj.__version__},
        'limitations': ['Approximately 30-m source grid; records are estimated people per cell, not individual persons or population per km².',
                        'Entire cell estimate retained when its center is inside or exactly on the boundary; cells are not divided.',
                        'Boundary geometry temporal provenance follows HDX notes; this is not certified as a dedicated 2020 boundary snapshot.',
                        'Population filename/header indicate 2020; original model inputs and census vintage require reference to Meta methodology.'],
    }
    source_metadata_path = ROOT / 'boundaries/philippines-high-resolution-population-density-maps-demographic-estimates_hdx_metadata.json'
    if source_metadata_path.exists():
        hdx = json.loads(source_metadata_path.read_text())['result']
        resources = [r for r in hdx['resources'] if r['name'] == 'phl_general_2020_csv.zip']
        summary['source_hdx_provenance'] = {
            'saved_metadata_path': str(source_metadata_path),
            'license': hdx.get('license_title'), 'license_url': hdx.get('license_url'),
            'metadata_modified': hdx.get('metadata_modified'),
            'general_2020_resource': resources[0] if len(resources) == 1 else None,
            'methodology_url': 'https://ai.meta.com/ai-for-good/docs/methodology-high-resolution-population-density-maps/',
            'note': 'HDX identifies this as the general 2020 resource. The 2020 label is not a claim that it reproduces the PSA 2020 census.',
        }
    if args.preview:
        summary['preview'] = preview(output.parent / 'Santa_Rosa_Population_Preview.png', output, names, geometry, float(total))
    summary['extraction_execution_time_seconds'] = round(time.perf_counter() - start, 3)
    summary_path.write_text(json.dumps(summary, indent=2, allow_nan=False) + '\n')
    LOG.info('Saved %s (%s bytes); %s cells; exact population %s', output, output.stat().st_size, retained, total)
    LOG.info('Validation passed; invalid rows %s; census difference %.2f%%; summary %s', sum(errors.values()), comparison['difference_percent'], summary_path)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--boundary', type=Path, default=ROOT / 'boundaries/Santa_Rosa_City_Laguna.geojson')
    parser.add_argument('--output', type=Path, default=ROOT / 'output/Santa_Rosa_Population_Density_2020.csv')
    parser.add_argument('--chunksize', type=int, default=100000)
    parser.add_argument('--zip-member')
    parser.add_argument('--preview', action='store_true')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    try:
        extract(args)
    except Exception:
        LOG.exception('Extraction failed; no successful validation is claimed')
        raise SystemExit(1)


if __name__ == '__main__':
    main()
