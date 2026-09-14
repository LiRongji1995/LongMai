"""Read-only byte verification of the previously frozen official documents."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from r16_r1_supervise import source_metadata, write_json, now, sha256


def main():
    parser = argparse.ArgumentParser()
    for name in ('db','baseline','output'):
        parser.add_argument('--'+name, required=True)
    args = parser.parse_args()
    baseline = json.loads(Path(args.baseline).read_text(encoding='utf-8'))
    result = {'start': now(), 'source': source_metadata(Path(__file__).resolve().parents[2]),
        'baseline_sha256': sha256(args.baseline), 'read_mode': 'SQLITE_URI_MODE_RO_QUERY_ONLY', 'rows': []}
    last = time.monotonic()
    with sqlite3.connect(Path(args.db).resolve().as_uri()+'?mode=ro', uri=True) as con:
        con.execute('PRAGMA query_only=ON')
        for expected in baseline['rows']:
            namespace, document_id = expected['namespace'], expected['document_id']
            row = con.execute('SELECT rowid,content_hash,created_at FROM documents WHERE namespace=? AND document_id=?',
                              (namespace, document_id)).fetchone()
            if row is None:
                result['rows'].append({'namespace': namespace, 'document_id': document_id, 'matched': False, 'reason': 'MISSING'})
                continue
            h, count = hashlib.sha256(), 0
            with con.blobopen('documents', 'payload', row[0], readonly=True) as blob:
                for data in iter(lambda: blob.read(1024*1024), b''):
                    h.update(data); count += len(data)
            result['rows'].append({'namespace': namespace, 'document_id': document_id,
                'payload_sha256': h.hexdigest(), 'payload_bytes': count,
                'matched': h.hexdigest() == expected['payload_sha256'] == row[1]
                           and count == expected['payload_bytes'] and row[2] == expected['created_at']})
            if time.monotonic()-last >= 10:
                print(json.dumps({'processed': len(result['rows']), 'total': len(baseline['rows'])}), flush=True)
                last = time.monotonic()
        result['connection_total_changes'] = con.total_changes
    result.update(end=now(), matched=all(row['matched'] for row in result['rows']))
    write_json(args.output, result)
    print(json.dumps({'matched': result['matched'], 'rows': len(result['rows'])}), flush=True)
    return 0 if result['matched'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
