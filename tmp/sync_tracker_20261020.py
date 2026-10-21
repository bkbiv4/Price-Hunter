import hashlib
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import business_imports as imports

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(r'D:\iCloudDrive\4. Business & Side Hustles\Hidden Richz LLC\Excel Sheets\CardBusiness_Starter_Tracker2.xlsx')
# Individually reviewed matches: same products, quantities and costs, with
# corrected dates/names or additional workbook details.
MATCHES = {'purchases': {
    'd8e77e1c': 141, '16183e99': 134, 'ce636300': 144, '14de26ce': 145,
    'b4379627': 142, 'e2ecbc42': 143, 'b4a3ab3a': 146, '986c2148': 150,
    'd504ff04': 149, '03d35c38': 151, 'e2a75b96': 152, '04d3e07f': 153,
    'b7806e9d': 154, '4529aae5': 155, '99e4aa20': 147, 'da3d791f': 148,
}, 'expenses': {'10d79dd8': 50}}

def run(apply=False):
    db = sqlite3.connect(ROOT / 'price_hunter.db', timeout=30)
    db.row_factory = sqlite3.Row
    report = {'source': str(SOURCE), 'source_sha256': hashlib.sha256(SOURCE.read_bytes()).hexdigest(), 'tables': {}}
    protected = ['cards', 'sales', 'purchase_allocations', 'receipts', 'grading_submissions', 'grading_items', 'inventory_events', 'sale_items']
    def snapshots():
        return {t: [tuple(r) for r in db.execute(f'SELECT * FROM {t} ORDER BY id')] for t in protected}
    before = snapshots()
    plans = []
    for table, reader, total in [('purchases', imports.read_purchase_workbook, 'total_cost'), ('expenses', imports.read_expense_workbook, 'total')]:
        rows, errors = reader(SOURCE)
        existing = [dict(r) for r in db.execute(f'SELECT * FROM {table}')]
        bykey = {r['source_key']: r for r in existing if r['source_key']}
        byid = {r['id']: r for r in existing}
        updates, additions, used = [], [], set()
        for row in rows:
            old = bykey.get(row['source_key'])
            if old is None:
                matches = [i for prefix, i in MATCHES[table].items() if row['source_key'].split(':')[1].startswith(prefix)]
                assert len(matches) <= 1
                if matches:
                    old = byid[matches[0]]
                    assert old[total] == row[total] and old['quantity'] == row['quantity']
            target = dict(row)
            if old:
                assert old['id'] not in used
                used.add(old['id'])
                # Workbook blanks must not erase receipt-derived provenance.
                for field in ['receipt_ref', 'notes', 'payment_account']:
                    if not target[field]:
                        target[field] = old[field]
                diff = {k: [old[k], v] for k, v in target.items() if old[k] != v}
                if diff:
                    updates.append({'id': old['id'], 'description': target['description'], 'changes': diff})
                    plans.append((table, old['id'], target))
            else:
                additions.append(target)
                plans.append((table, None, target))
        report['tables'][table] = {'source_valid_rows': len(rows), 'skipped': errors, 'updates': updates, 'additions': additions, 'added_total': round(sum(r[total] for r in additions), 2), 'unchanged': len(rows)-len(updates)-len(additions)}
    output = ROOT / 'outputs' / 'tracker_sync_20261020.json'
    if apply:
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        backup = ROOT / f'price_hunter.before-tracker-sync-{stamp}.db'
        with sqlite3.connect(backup) as dest:
            db.backup(dest)
        report['backup'] = str(backup)
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('BEGIN IMMEDIATE')
        try:
            assert snapshots() == before, 'Database changed during preparation'
            for table, record_id, row in plans:
                if record_id is None:
                    db.execute(f"INSERT INTO {table} ({','.join(row)}) VALUES ({','.join('?' for _ in row)})", tuple(row.values()))
                else:
                    db.execute(f"UPDATE {table} SET {','.join(k+'=?' for k in row)} WHERE id=?", (*row.values(), record_id))
            assert snapshots() == before, 'Unrelated data changed'
            assert not list(db.execute('PRAGMA foreign_key_check'))
            assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
            for table, reader in [('purchases', imports.read_purchase_workbook), ('expenses', imports.read_expense_workbook)]:
                for row in reader(SOURCE)[0]:
                    found = db.execute(f'SELECT * FROM {table} WHERE source_key=?', (row['source_key'],)).fetchall()
                    assert len(found) == 1
                    for key, value in row.items():
                        if key in ['receipt_ref', 'notes', 'payment_account'] and not value:
                            continue
                        assert found[0][key] == value, (table, key)
            db.commit()
        except BaseException:
            db.rollback()
            raise
        report['verified'] = 'All valid source rows matched once; unrelated tables unchanged; integrity and foreign keys pass.'
    output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({t: {'added': len(v['additions']), 'updated': len(v['updates']), 'unchanged': v['unchanged'], 'added_total': v['added_total'], 'skipped': v['skipped']} for t,v in report['tables'].items()}, indent=2))
    print('APPLIED' if apply else 'PREVIEW', output)
    if apply:
        print('Backup:', report['backup'])
    db.close()

if __name__ == '__main__':
    run('--apply' in sys.argv)
