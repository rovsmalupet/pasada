"""Edge-case checks for holes, multipart boundaries, exact values, CRS and ZIPs."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from pyproj import Transformer
from shapely.geometry import Polygon, MultiPolygon, mapping
from shapely.ops import transform

import extract_santa_rosa as e


class ExtractionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='Santa Rosa test ')
        self.root = Path(self.temp.name)
        outer = [(121.01, 14.21), (121.05, 14.21), (121.05, 14.25), (121.01, 14.25)]
        hole = [(121.02, 14.22), (121.03, 14.22), (121.03, 14.23), (121.02, 14.23)]
        self.geometry = MultiPolygon([Polygon(outer, [hole]),
                                      Polygon([(121.06,14.26),(121.07,14.26),(121.07,14.27),(121.06,14.27)])])
        self.boundary = self.root / 'boundary.geojson'
        self.write_boundary(self.geometry)

    def tearDown(self):
        self.temp.cleanup()

    def write_boundary(self, geometry, crs=None):
        data = {'type': 'FeatureCollection', 'features': [{'type': 'Feature',
                'properties': {'adm3_name':'City of Santa Rosa','adm2_name':'Laguna','adm3_pcode':'PH0403428'},
                'geometry':mapping(geometry)}]}
        if crs:
            data['crs'] = {'type':'name','properties':{'name':crs}}
        self.boundary.write_text(json.dumps(data))
        meta = {'source_url':'test fixture', 'dataset_version':'fixture',
                'acquisition_date':'fixture', 'license':'fixture','license_url':'fixture',
                'psgc_code':e.PSGC, 'selected_boundary_sha256':e.sha256(self.boundary)}
        self.boundary.with_suffix('.metadata.json').write_text(json.dumps(meta))

    def test_zip_filter_holes_edges_invalid_duplicates_and_exact_fields(self):
        header = ['Longitude','Latitude','Population','label']
        rows = [
            ['121.015','14.215','0.0000000000000000001234500','fraction'],
            ['121.01','14.21','0','outer boundary'],
            ['121.02','14.225','2.5000000','hole boundary'],
            ['121.025','14.225','3','hole interior'],
            ['121.065','14.265','4.75','other part'],
            ['121.055','14.255','5','bbox but outside'],
            ['nan','14.215','6','invalid coordinate'],
            ['121.015','14.215','-1','negative'],
            ['121.015','14.215','NaN','nonfinite'],
            ['121.015','14.215','oops','nonnumeric'],
        ]
        rows.append(rows[0])  # preserve a real source duplicate
        source = self.root / 'input with spaces.csv'
        with source.open('w', newline='') as f:
            writer = csv.writer(f); writer.writerow(header); writer.writerows(rows)
        archive = self.root / 'input with spaces.zip'
        with zipfile.ZipFile(archive,'w') as z:
            z.write(source,'nested/phl_general_2020.csv')
            z.writestr('readme.txt','A non-CSV archive entry')
            z.writestr('phl_women_2020.csv','irrelevant\n')
        output = self.root / 'output' / 'Santa_Rosa_Population_Density_2020.csv'
        s = e.extract(argparse.Namespace(input=archive, output=output,boundary=self.boundary,
                                        chunksize=2,zip_member=None,preview=False))
        with output.open(newline='') as f:
            actual = list(csv.reader(f))
        self.assertEqual(actual, [header, rows[0], rows[1], rows[2], rows[4], rows[10]])
        self.assertEqual(s['original_rows_processed'], 11)
        self.assertEqual(s['rejected_or_invalid_rows'], 4)
        self.assertEqual(s['zero_population_rows_retained'], 1)
        self.assertEqual(s['validation']['duplicate_output_records'], 1)
        self.assertEqual(s['validation']['accidental_duplicates_introduced'], 0)
        self.assertEqual(s['sum_population_estimates_exact_decimal'], '7.2500000000000000002469000')

    def test_projected_boundary_reprojection(self):
        project = Transformer.from_crs(4326, 3857, always_xy=True)
        self.write_boundary(transform(project.transform, self.geometry), 'EPSG:3857')
        actual, _, crs = e.load_boundary(self.boundary)
        self.assertEqual(crs,'EPSG:3857')
        self.assertLess(actual.hausdorff_distance(self.geometry), 1e-10)

    def test_fail_closed_for_ambiguous_zip_and_boundary_tampering(self):
        archive = self.root / 'ambiguous.zip'
        with zipfile.ZipFile(archive,'w') as z:
            z.writestr('a.csv','a\n'); z.writestr('b.csv','b\n')
        with self.assertRaises(ValueError):
            with e.open_source(archive):
                pass
        self.boundary.write_text(self.boundary.read_text()+' ')
        with self.assertRaisesRegex(ValueError,'checksum'):
            e.load_boundary(self.boundary)

    def test_fail_closed_for_unverified_columns_and_wrong_city(self):
        with self.assertRaises(ValueError):
            e.detect_columns(['x','y','population'])
        data = json.loads(self.boundary.read_text())
        data['features'][0]['properties']['adm2_name'] = 'Nueva Ecija'
        self.boundary.write_text(json.dumps(data))
        meta_path = self.boundary.with_suffix('.metadata.json')
        meta = json.loads(meta_path.read_text()); meta['selected_boundary_sha256'] = e.sha256(self.boundary)
        meta_path.write_text(json.dumps(meta))
        with self.assertRaisesRegex(ValueError,'province'):
            e.load_boundary(self.boundary)


if __name__ == '__main__':
    unittest.main()
