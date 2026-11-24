#!/usr/bin/env python3
"""Read official Delta Force video metadata from the browser-audited account index."""
import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
import sync_weapon_curated as curated

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / 'data/weapon-skins/delta-bilibili-index.json'
REPORT = ROOT / 'local-only/weapon-skins/delta-bilibili-review.json'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--refresh', action='store_true', help='Refresh cached video metadata; default resumes an interrupted run')
    args = parser.parse_args()
    index = json.loads(INDEX.read_text(encoding='utf-8'))
    records = []
    errors = []
    for number, bvid in enumerate(index['candidateBvids'], 1):
        url = 'https://api.bilibili.com/x/web-interface/view?bvid=' + bvid
        try:
            cache = curated.CACHE / ('delta-account-' + bvid + '.json')
            cached = args.offline or (cache.exists() and not args.refresh)
            response = curated.metadata(cache.name, url, cached)['data']
            if response['owner']['mid'] != index['ownerId'] or response['owner']['name'] != '三角洲行动':
                raise ValueError('Official author identity mismatch')
            records.append({
                'bvid': bvid, 'title': response['title'], 'description': response.get('desc', ''),
                'publishedDate': datetime.fromtimestamp(response['pubdate'], timezone.utc).date().isoformat(),
                'coverUrl': response['pic'].replace('http://', 'https://'),
                'duration': response['duration'], 'ownerId': response['owner']['mid'],
                'sourceUrl': 'https://www.bilibili.com/video/' + bvid + '/',
            })
            if not cached:
                time.sleep(1.1)
        except Exception as error:
            errors.append({'bvid': bvid, 'error': str(error)})
            if any(code in str(error) for code in ['412', '429', '-799', '-352', '-509']):
                break
        if number % 20 == 0:
            print(json.dumps({'checked': number, 'total': len(index['candidateBvids']), 'errors': len(errors)}), flush=True)
    curated.atomic_json(REPORT, {'index': index, 'records': records, 'errors': errors,
        'metadataComplete': len(records) == len(index['candidateBvids'])})
    print(json.dumps({'verified': len(records), 'errors': errors, 'report': str(REPORT)}, ensure_ascii=False))
    return int(bool(errors) or len(records) != len(index['candidateBvids']))


if __name__ == '__main__':
    raise SystemExit(main())
