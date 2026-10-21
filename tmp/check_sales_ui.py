import tempfile
tempfile.tempdir = r'C:\Coding\Price Hunter\tmp'
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from unittest.mock import patch
from streamlit.testing.v1 import AppTest
import database

with tempfile.TemporaryDirectory() as folder, patch.object(database, 'DB_PATH', Path(folder)/'test.db'):
    database.initialize()
    card = database.add_card({'card_name':'UI card','quantity':2,'cost':10})
    database.import_sales([
        {'source_key':'ui','title':'UI sale','sale_date':'2026-10-20','item_subtotal':100,'net_amount':90,'gross_net_amount':90,'fees':10},
        {'source_key':'ui2','title':'Second sale','sale_date':'2026-10-19','item_subtotal':50,'net_amount':50,'gross_net_amount':50},
    ])
    app = AppTest.from_string('from business_ui import render_sales\nrender_sales()').run(timeout=30)
    def clean():
        assert not app.exception, [e.message for e in app.exception]
    clean()
    next(x for x in app.number_input if x.label=='Platform fees' and str(x.key).startswith('sale_edit')).set_value(15).run()
    clean()
    assert next(m.value for m in app.metric if m.label=='Net after saving')=='$85.00'
    next(b for b in app.button if b.label=='Save sale changes').click().run()
    clean()
    assert database.all_sales()[0]['net_amount']==85
    next(x for x in app.multiselect if x.label=='Cards sold').set_value([card]).run()
    clean()
    next(b for b in app.button if b.label=='Save linked cards').click().run()
    clean()
    assert database.get_card(card)['quantity']==1
    next(b for b in app.button if b.label=='Save linked cards').click().run()
    clean()
    assert database.get_card(card)['quantity']==1
    selector = app.selectbox(key='edit_sale_selection')
    selector.select(selector.options[1]).run()
    clean()
    assert next(m.value for m in app.metric if m.label=='Net after saving')=='$50.00'
    app.text_input(key='sale_search').set_value('nothing matches').run()
    clean()
    print('PASS: render, live preview, fee save, stock deduction, repeated save, sale switching, empty search')
