import hashlib
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import business_imports

root = Path(__file__).resolve().parents[1]
source = Path(r'C:\Users\dynab\Downloads\eBay-ListingsSalesReport-Oct-20-2026-13_05_38-0700-13335874516.csv')
rows, errors = business_imports.read_ebay_workbook(source)
assert not errors and len(rows) == 9
assert len({r['item_id'] for r in rows}) == 9
matches = {'317579410693': 2048, '317705476090': 1739, '317752066468': 1809, '317752129864': 870}
db = sqlite3.connect(root / 'price_hunter.db', timeout=30)
db.row_factory = sqlite3.Row
backup = root / ('price_hunter.before-ebay-import-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.db')
with sqlite3.connect(backup) as dest:
    db.backup(dest)
audit = {'source': str(source), 'sha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'backup': str(backup), 'added': [], 'updated': [], 'new_matches': [], 'unmatched': []}
protected = ['cards', 'purchases', 'expenses', 'purchase_allocations', 'receipts', 'grading_items', 'grading_submissions', 'inventory_events']
snapshot = {t: [tuple(r) for r in db.execute(f'SELECT * FROM {t} ORDER BY id')] for t in protected}
db.execute('PRAGMA foreign_keys=ON')
db.execute('BEGIN IMMEDIATE')
try:
    ids = []
    for row in rows:
        assert abs(round(row['item_subtotal'] + row['shipping_charged'] - row['fees'] - row['promoted_listing_fees'] - row['shipping_label_cost'], 2) - row['net_amount']) < .01
        existing = db.execute("SELECT * FROM sales WHERE marketplace='eBay' AND item_id=? AND sale_date BETWEEN '2026-01-01' AND '2026-10-20'", (row['item_id'],)).fetchall()
        assert len(existing) <= 1, 'Overlapping sales need manual reconciliation'
        notes = row['notes'] + '; source file: ' + source.name
        if existing:
            old = dict(existing[0])
            assert old['quantity'] == row['quantity']
            assert not any(old[k] for k in ['refunded_amount', 'additional_expenses', 'return_shipping_cost'])
            fields = {k: row[k] for k in ['item_subtotal', 'shipping_charged', 'tax_collected', 'fees', 'promoted_listing_fees', 'shipping_label_cost', 'net_amount', 'gross_net_amount']}
            if row['net_amount'] < 0:
                notes += '; REVIEW: report shows zero revenue and retained selling costs. Refund/return status and inventory disposition are not supplied.'
            fields['notes'] = notes + '; original order ID and sale date retained.'
            fields['profit'] = round(row['net_amount'] - old['cost_of_goods'], 2) if old['cost_of_goods'] is not None else None
            sale_id = old['id']
            db.execute('UPDATE sales SET ' + ','.join(k+'=?' for k in fields) + ' WHERE id=?', (*fields.values(), sale_id))
            db.execute('UPDATE sale_items SET unit_price=? WHERE sale_id=?', (round(row['item_subtotal']/row['quantity'],2), sale_id))
            audit['updated'].append({'id': sale_id, 'before': old, 'after': fields})
        else:
            row = dict(row)
            row['notes'] = notes + '; sale_date is report end date, actual transaction date unavailable.'
            card_id = matches.get(row['item_id'])
            if card_id:
                card = dict(db.execute('SELECT * FROM cards WHERE id=?', (card_id,)).fetchone())
                assert not card['grader'] and not card['grade']
                row['card_id'] = card_id
                row['sku'] = card['sku']
                row['cost_of_goods'] = round((card['cost'] + card['grading_cost']) * row['quantity'], 2)
                row['profit'] = round(row['net_amount'] - row['cost_of_goods'], 2)
            sale_id = db.execute('INSERT INTO sales ('+','.join(row)+') VALUES ('+','.join('?' for _ in row)+')', tuple(row.values())).lastrowid
            audit['added'].append({'id': sale_id, **row})
            if card_id:
                db.execute('INSERT INTO sale_items (sale_id,card_id,card_sku,card_name,quantity,unit_price,unit_cost,acquisition_unit_cost,grading_unit_cost) VALUES (?,?,?,?,?,?,?,?,?)', (sale_id,card_id,card['sku'],card['card_name'],row['quantity'],round(row['item_subtotal']/row['quantity'],2),card['cost']+card['grading_cost'],card['cost'],card['grading_cost']))
                audit['new_matches'].append({'sale_id': sale_id, 'card_id': card_id})
            else:
                audit['unmatched'].append({'sale_id': sale_id, 'title': row['title'], 'reason': 'Inventory has no verified matching graded card.'})
        ids.append(sale_id)
    assert len(set(ids)) == 9
    for t, before in snapshot.items():
        assert [tuple(r) for r in db.execute(f'SELECT * FROM {t} ORDER BY id')] == before
    actual_net = db.execute('SELECT SUM(net_amount) FROM sales WHERE id IN ('+','.join('?' for _ in ids)+')', ids).fetchone()[0]
    audit['report_net'] = round(sum(r['net_amount'] for r in rows), 2)
    assert round(actual_net, 2) == audit['report_net']
    assert not list(db.execute('PRAGMA foreign_key_check'))
    assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    db.commit()
except BaseException:
    db.rollback()
    raise
audit['verification'] = 'Nine report listings represented once; net reconciled; protected tables unchanged; database integrity passed. Inventory quantities unchanged, consistent with historical sales import behavior.'
output = root / 'outputs' / 'ebay_import_20261020.json'
output.write_text(json.dumps(audit, indent=2), encoding='utf-8')
print(json.dumps({k: len(audit[k]) for k in ['added','updated','new_matches','unmatched']}))
print('Report net:', audit['report_net'])
print('Backup:', backup)
print('Audit:', output)
db.close()
