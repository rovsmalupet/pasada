"""Download only ADM3 from HDX's large ZIP using verified HTTP byte ranges."""
import hashlib
import argparse
import io
import json
from pathlib import Path
import urllib.request
import zipfile
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent / 'boundaries'


class RemoteZip(io.RawIOBase):
    def __init__(self, url, size):
        self.url, self.size, self.pos = url, size, 0

    def seekable(self):
        return True

    def seek(self, offset, whence=0):
        self.pos = offset if whence == 0 else (self.pos if whence == 1 else self.size) + offset
        return self.pos

    def tell(self):
        return self.pos

    def read(self, n=-1):
        n = min(self.size - self.pos, n if n >= 0 else self.size)
        if n <= 0:
            return b''
        end = self.pos + n - 1
        req = urllib.request.Request(self.url, headers={'Range': f'bytes={self.pos}-{end}'})
        with urllib.request.urlopen(req, timeout=120) as response:
            expected = f'bytes {self.pos}-{end}/{self.size}'
            if response.status != 206 or response.headers.get('Content-Range') != expected:
                raise RuntimeError('Server did not honor byte range; refusing full archive download')
            data = response.read()
        if len(data) != n:
            raise RuntimeError('Incomplete byte range')
        self.pos += n
        return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--refresh', action='store_true', help='Reacquire using the saved HDX resource metadata')
    parser.add_argument('--keep-layer', action='store_true', help='Retain the large downloaded national ADM3 layer')
    args = parser.parse_args()
    city_path = ROOT / 'Santa_Rosa_City_Laguna.geojson'
    sidecar_path = ROOT / 'Santa_Rosa_City_Laguna.metadata.json'
    if city_path.exists() and sidecar_path.exists() and not args.refresh:
        recorded = json.loads(sidecar_path.read_text())
        if hashlib.sha256(city_path.read_bytes()).hexdigest() != recorded['selected_boundary_sha256']:
            raise RuntimeError('Saved city boundary checksum mismatch')
        print('Verified saved boundary already available:', city_path)
        return
    metadata_url = 'https://data.humdata.org/api/3/action/package_show?id=cod-ab-phl'
    metadata = json.loads((ROOT / 'cod-ab-phl_hdx_metadata.json').read_text())['result']
    resource = next(x for x in metadata['resources'] if x['format'] == 'GeoJSON')
    cached = ROOT / 'phl_admin3.geojson'
    inventory = None
    if cached.exists():
        member = cached.name
        data = cached.read_bytes()
        print('Using previously acquired ADM3 layer:', cached, flush=True)
    else:
        with zipfile.ZipFile(RemoteZip(resource['url'], resource['size'])) as archive:
            inventory = [{'name': x.filename, 'bytes': x.file_size, 'compressed_bytes': x.compress_size}
                         for x in archive.infolist()]
            print(json.dumps(inventory, indent=2), flush=True)
            candidates = [x for x in archive.namelist()
                          if x.lower().endswith('.geojson') and 'admin3' in x.lower()]
            if len(candidates) != 1:
                raise RuntimeError(f'ADM3 ambiguous: {candidates}')
            member = candidates[0]
            data = archive.read(member)  # zipfile checks member CRC
        cached.write_bytes(data)
    collection = json.loads(data)
    matches = [f for f in collection['features']
               if 'rosa' in str(f['properties']).lower()]
    print('Santa Rosa candidates:', json.dumps([f['properties'] for f in matches], indent=2), flush=True)
    selected = [f for f in collection['features']
                if f['properties'].get('adm3_pcode') in ('PH0403428', 'PH043428000', 'PH0403428000')
                and f['properties'].get('adm2_name') == 'Laguna'
                and 'santa rosa' in f['properties'].get('adm3_name', '').lower()]
    if len(selected) != 1:
        raise RuntimeError('Could not uniquely verify Santa Rosa City, Laguna by name, province and code')
    feature = selected[0]
    city = {'type': 'FeatureCollection', 'features': [feature]}
    if 'crs' in collection:
        city['crs'] = collection['crs']
    city_path.write_text(json.dumps(city, indent=2) + '\n')
    provenance = {
        'source_url': 'https://data.humdata.org/dataset/cod-ab-phl',
        'metadata_api_url': metadata_url,
        'dataset_name': metadata['title'],
        'source_organizations': metadata['dataset_source'],
        'contributor': 'OCHA Philippines',
        'dataset_version': 'COD-AB version 03 (as stated in HDX notes)',
        'dataset_notes': metadata['notes'],
        'resource_url': resource['url'],
        'resource_last_modified': resource['last_modified'],
        'resource_member': member,
        'archive_inventory': inventory,
        'acquisition_date': datetime.now(ZoneInfo('Asia/Manila')).isoformat(),
        'license': metadata['license_title'],
        'license_url': metadata['license_url'],
        'adm3_layer_sha256': hashlib.sha256(data).hexdigest(),
        'adm3_layer_size_bytes': len(data),
        'selected_boundary_sha256': hashlib.sha256(city_path.read_bytes()).hexdigest(),
        'selected_feature_properties': feature['properties'],
        'psgc_code': '0403428000',
        'psgc_correspondence_code': '043428000',
        'hdx_pcode_mapping': 'PH0403428 = PH + 10-digit PSGC 0403428000 with the three city-level trailing zeros omitted',
        'psgc_verification_url': 'https://psa.gov.ph/classification/psgc/barangays/0403428000',
        'province_verification_url': 'https://psa.gov.ph/classification/psgc/citimuni/0403400000',
        'crs': collection.get('crs', 'EPSG:4326 (GeoJSON default)'),
        'selection': 'Single complete ADM3 feature; no clipping, simplification or geometry repair',
    }
    (ROOT / 'Santa_Rosa_City_Laguna.metadata.json').write_text(json.dumps(provenance, indent=2) + '\n')
    if not args.keep_layer:
        cached.unlink()
    print('Saved verified city feature:', city_path, flush=True)


if __name__ == '__main__':
    main()
