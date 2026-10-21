import csv
import hashlib
import io
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from business_imports import _source_key

root = Path(__file__).resolve().parents[1]
source = Path(r'C:\Users\dynab\Downloads\Transaction_report_20260101_20261020.csv')
prior = json.loads((root/'outputs/ebay_import_20261020.json').read_text())
text = source.read_text(encoding='utf-8-sig')
lines = text.splitlines()
start = next(i for i,s in enumerate(lines) if s.startswith('Transaction creation date,Type,'))
raw = list(csv.DictReader(io.StringIO('\n'.join(lines[start:]))))
def money(v):
    return Decimal(v.replace(',', '')) if v and v != '--' else Decimal('0')
def cents(v):
    return int((v * 100).quantize(Decimal('1')))
def split(v, count):
    base, remainder = divmod(cents(v), count)
    return [Decimal(base + (i < remainder))/100 for i in range(count)]
orders, labels = defaultdict(list), defaultdict(lambda: Decimal('0'))
types = defaultdict(int)
for r in raw:
    types[r['Type']] += 1
    if r['Type'] == 'Order':
        orders[r['Order number']].append(r)
    elif r['Type'] == 'Shipping label':
        labels[r['Order number']] -= money(r['Net amount'])
assert set(types) <= {'Order', 'Shipping label', 'Hold', 'Payout'}
assert sum((money(r['Net amount']) for r in raw if r['Type']=='Hold'),Decimal('0')) == 0
assert not (set(labels)-set(orders))
rows = []
for order, group in orders.items():
    items = [r for r in group if r['Item ID'] != '--']
    summaries = [r for r in group if r['Item ID'] == '--']
    assert items and len(summaries) <= 1
    shares = split(labels[order], len(items))
    expected = money(summaries[0]['Net amount']) if summaries else sum((money(r['Net amount']) for r in items),Decimal('0'))
    calculated = Decimal('0')
    for r, label in zip(items, shares):
        subtotal, shipping = money(r['Item subtotal']), money(r['Shipping and handling'])
        fee_fields = ['Final Value Fee - fixed','Final Value Fee - variable','Regulatory operating fee','Very high "item not as described" fee','Below standard performance fee','International fee','Charity donation','Deposit processing fee']
        fees = -sum((money(r[k]) for k in fee_fields),Decimal('0'))
        order_net = subtotal + shipping + money(r['Seller collected tax']) - fees
        if r['Net amount'] != '--':
            assert order_net == money(r['Net amount'])
        calculated += order_net
        date = datetime.strptime(r['Transaction creation date'],'%b %d, %Y').date().isoformat()
        notes = 'Imported from '+source.name+'. Actual transaction date; fees and shipping labels included.'
        if len(items)>1:
            notes += ' Order shipping-label cost split equally across its two listings; fees follow individual transaction rows.'
        if r['Item ID']=='318508625049':
            notes += ' Hold placed Aug 7 and released Aug 9 cancel out; no refund transaction in this report.'
        rows.append(dict(source_key=_source_key('ebay',order,r['Item ID'],date),sale_date=date,marketplace='eBay',order_number=order,item_id=r['Item ID'],sku='' if r['Custom label']=='--' else r['Custom label'],title=r['Item title'],quantity=int(r['Quantity']),item_subtotal=float(subtotal),shipping_charged=float(shipping),tax_collected=float(money(r['Seller collected tax'])),fees=float(fees),promoted_listing_fees=0.0,shipping_label_cost=float(label),net_amount=float(order_net-label),gross_net_amount=float(order_net-label),buyer=r['Buyer name'] if r['Buyer name']!='--' else r['Buyer username'],payment_method='eBay managed payments',notes=notes))
    assert expected == calculated, (order,expected,calculated)
assert len(rows)==9 and len(orders)==8
expected_net = sum((money(r['Net amount']) for r in raw if r['Type']=='Order'),Decimal('0')) - sum(labels.values(),Decimal('0'))
assert round(sum(r['net_amount'] for r in rows),2)==float(expected_net)
db = sqlite3.connect(root/'price_hunter.db',timeout=30)
db.row_factory=sqlite3.Row
backup = root/('price_hunter.before-transaction-replacement-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.db')
with sqlite3.connect(backup) as dest:
    db.backup(dest)
prior_ids = [r['id'] for r in prior['added']]
affected_ids = prior_ids + [r['id'] for r in prior['updated']]
protected_tables = ['cards','purchases','expenses','receipts','purchase_allocations','grading_items','grading_submissions','inventory_events']
protected = {t:[tuple(r) for r in db.execute(f'SELECT * FROM {t} ORDER BY id')] for t in protected_tables}
other_sales=[tuple(r) for r in db.execute('SELECT * FROM sales ORDER BY id') if r['id'] not in affected_ids]
other_items=[tuple(r) for r in db.execute('SELECT * FROM sale_items ORDER BY id') if r['sale_id'] not in affected_ids]
audit={'source':str(source),'sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'backup':str(backup),'removed_summary_ids':prior_ids,'transaction_types':dict(types),'sales':[],'net_total':float(expected_net)}
matches={r['item_id']:r['card_id'] for r in prior['added'] if r.get('card_id')}
db.execute('PRAGMA foreign_keys=ON')
db.execute('BEGIN IMMEDIATE')
try:
    for r in prior['added']:
        old=db.execute('SELECT * FROM sales WHERE id=?',(r['id'],)).fetchone()
        assert old and old['source_key']==r['source_key']
        assert old['net_amount']==r['net_amount']
        db.execute('DELETE FROM sale_items WHERE sale_id=?',(r['id'],))
        db.execute('DELETE FROM sales WHERE id=?',(r['id'],))
    for row in rows:
        found=db.execute("SELECT * FROM sales WHERE marketplace='eBay' AND order_number=? AND item_id=?",(row['order_number'],row['item_id'])).fetchall()
        assert len(found)<=1
        if found:
            old=dict(found[0])
            assert old['id'] in affected_ids and old['quantity']==row['quantity']
            assert old['refunded_amount']==old['additional_expenses']==old['return_shipping_cost']==0
            row['profit']=round(row['net_amount']-old['cost_of_goods'],2) if old['cost_of_goods'] is not None else None
            sid=old['id']
            db.execute('UPDATE sales SET '+','.join(k+'=?' for k in row)+' WHERE id=?',(*row.values(),sid))
            db.execute('UPDATE sale_items SET unit_price=? WHERE sale_id=?',(row['item_subtotal']/row['quantity'],sid))
            action='updated'
        else:
            card_id=matches.get(row['item_id'])
            if card_id:
                card=dict(db.execute('SELECT * FROM cards WHERE id=?',(card_id,)).fetchone())
                row.update(card_id=card_id,sku=card['sku'],cost_of_goods=round((card['cost']+card['grading_cost'])*row['quantity'],2))
                row['profit']=round(row['net_amount']-row['cost_of_goods'],2)
            sid=db.execute('INSERT INTO sales ('+','.join(row)+') VALUES ('+','.join('?' for _ in row)+')',tuple(row.values())).lastrowid
            if card_id:
                db.execute('INSERT INTO sale_items (sale_id,card_id,card_sku,card_name,quantity,unit_price,unit_cost,acquisition_unit_cost,grading_unit_cost) VALUES (?,?,?,?,?,?,?,?,?)',(sid,card_id,card['sku'],card['card_name'],row['quantity'],row['item_subtotal']/row['quantity'],card['cost']+card['grading_cost'],card['cost'],card['grading_cost']))
            action='added'
        audit['sales'].append({'id':sid,'action':action,**row})
    new_ids={r['id'] for r in audit['sales']}
    assert len(new_ids)==9
    assert [tuple(r) for r in db.execute('SELECT * FROM sales ORDER BY id') if r['id'] not in new_ids]==other_sales
    assert [tuple(r) for r in db.execute('SELECT * FROM sale_items ORDER BY id') if r['sale_id'] not in new_ids]==other_items
    for t, old in protected.items():
        assert [tuple(r) for r in db.execute(f'SELECT * FROM {t} ORDER BY id')]==old
    for r in rows:
        assert db.execute('SELECT COUNT(*) FROM sales WHERE source_key=?',(r['source_key'],)).fetchone()[0]==1
    assert not list(db.execute('PRAGMA foreign_key_check'))
    assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    db.commit()
except BaseException:
    db.rollback()
    raise
audit['verification']='Order totals reconciled; 9 item records across 8 orders; original summary records removed; unrelated records and inventory quantities unchanged; integrity passed.'
out=root/'outputs/ebay_transaction_replacement_20261020.json'
out.write_text(json.dumps(audit,indent=2),encoding='utf-8')
print(json.dumps({'orders':len(orders),'item_records':len(rows),'net_total':float(expected_net),'removed_summaries':len(prior_ids),'backup':str(backup),'report':str(out)}))
db.close()
