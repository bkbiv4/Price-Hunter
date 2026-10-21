import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
import database

results=[(19,'21053106','9.5'),(170,'21053110','9.5'),(471,'21053115','9.5'),(139,'21053118','10'),(7887,'21053126','10'),(43,'21053129','9.5'),(237,'21053133','9.5'),(200,'21053135','10'),(398,'21053137','9.5'),(143,'21053142','9'),(7927,'21053152','9.5'),(210,'21053154','10'),(33,'21053157','9.5')]
db=sqlite3.connect(root/'price_hunter.db',timeout=30)
db.row_factory=sqlite3.Row
backup=root/('price_hunter.before-bgs-upgrade-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.db')
with sqlite3.connect(backup) as dest:
    db.backup(dest)
before={r['id']:dict(r) for r in db.execute('SELECT * FROM cards')}
audit={'backup':str(backup),'updated':[],'already_correct':[],'grader_basis':'BGS inferred from existing certificate 21053157 in the supplied batch; grades and certificate numbers supplied by user.'}
db.execute('PRAGMA foreign_keys=ON')
db.execute('BEGIN IMMEDIATE')
try:
    for card_id,cert,grade in results:
        card=before[card_id]
        found=db.execute('SELECT id FROM cards WHERE certification_number=?',(cert,)).fetchall()
        assert not found or (len(found)==1 and found[0]['id']==card_id)
        target={'condition':'Graded','grader':'BGS','grade':grade,'certification_number':cert}
        if all(card[k]==v for k,v in target.items()):
            audit['already_correct'].append({'id':card_id,'certificate':cert})
            continue
        assert card['condition']=='Raw / Ungraded' and not card['grader'] and not card['grade'] and not card['certification_number']
        assert card['quantity']==1
        db.execute("UPDATE cards SET condition='Graded',grader='BGS',grade=?,certification_number=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",(grade,cert,card_id))
        database._record_event(db,card_id,'Graded',f'User-provided grading result: BGS {grade}; certification {cert}',0)
        audit['updated'].append({'id':card_id,'sku':card['sku'],'card_name':card['card_name'],'before':{k:card[k] for k in target},'after':target})
    allowed={'condition','grader','grade','certification_number','updated_at'}
    changed={r['id'] for r in audit['updated']}
    for r in db.execute('SELECT * FROM cards'):
        old=before[r['id']]
        assert all(r[k]==v for k,v in old.items() if r['id'] not in changed or k not in allowed)
    for card_id,cert,grade in results:
        r=db.execute('SELECT * FROM cards WHERE id=?',(card_id,)).fetchone()
        assert r['grader']=='BGS' and r['grade']==grade and r['certification_number']==cert and r['condition']=='Graded'
    assert len(audit['updated'])==12 and len(audit['already_correct'])==1
    assert not list(db.execute('PRAGMA foreign_key_check'))
    assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    db.commit()
except BaseException:
    db.rollback()
    raise
audit['verification']='All 13 certificates matched exactly once; 12 raw cards upgraded; quantities, costs, PSA copy and all other card fields preserved; database integrity passed.'
out=root/'outputs/bgs_upgrades_20261020.json'
out.write_text(json.dumps(audit,indent=2),encoding='utf-8')
print(json.dumps({'updated':len(audit['updated']),'already_correct':len(audit['already_correct']),'backup':str(backup),'report':str(out)}))
db.close()
