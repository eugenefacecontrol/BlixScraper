from concurrent.futures import ThreadPoolExecutor
import pytest
from blixscraper.purchases import Purchases


def add(p,**kwargs):
    return p.add(**{'store':'Aldi','purchased_on':'2026-10-10',
                    'items':[{'name':'Jajka wolny wybieg','receipt_name':'JAJA L','paid_pln':'19,98','quantity':'2','unit':'pack'},
                             {'name':'Banany','paid_pln':'3.49','quantity':'0.5','unit':'kg'}],
                    'total_paid_pln':'23.47',**kwargs})


def test_exact_net_payments_and_unknown_quantity(setup):
    _,db=setup;p=Purchases(db)
    r=add(p)
    assert r['status']=='saved' and r['receipt']['total_checked']
    assert r['receipt']['items'][0]['price_per_unit_pln']=='9.9900'
    assert r['receipt']['items'][1]['price_per_unit_pln']=='6.9800'
    r=add(p,items=[{'name':'Unknown size','paid_pln':'0'}],total_paid_pln=None)
    assert r['receipt']['items'][0]['quantity'] is None
    assert r['receipt']['items'][0]['price_per_unit_pln'] is None
    assert not r['receipt']['total_checked']
    assert not db.offers()  # Receipts cannot become promotional evidence.


def test_duplicates_atomic_and_reference_identity(setup):
    _,db=setup;p=Purchases(db)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:add(p),range(2)))
    assert sorted(r['status'] for r in results)==['already_saved','saved']
    assert p.search()['total_matches']==2
    assert add(p,receipt_reference='receipt-1')['status']=='saved'
    assert add(p,receipt_reference='receipt-2')['status']=='saved'
    assert add(p,receipt_reference='receipt-1',items=[{'name':'Changed parsing','paid_pln':'23.47'}])['status']=='already_saved'


@pytest.mark.parametrize('kwargs',[
    {'purchased_on':'2026-02-30'}, {'store':'   '}, {'items':[]},
    {'total_paid_pln':'23.46'}, {'total_paid_pln':'NaN'},
    {'items':[{'name':'x','paid_pln':'1.001'}]},
    {'items':[{'name':'x','paid_pln':'-1'}]},
    {'items':[{'name':'x','paid_pln':'Infinity'}]},
    {'items':[{'name':'x','paid_pln':'1','quantity':'0','unit':'pack'}]},
    {'items':[{'name':'x','paid_pln':'1','quantity':'1'}]},
    {'items':[{'name':'x','paid_pln':'1','quantity':'1','unit':'g'}]},
    {'items':[{'name':'x','paid_pln':'1','invented':'value'}]},
])
def test_invalid_receipt_writes_nothing(setup,kwargs):
    _,db=setup;p=Purchases(db)
    with pytest.raises(ValueError):add(p,**kwargs)
    assert p.search()['total_matches']==0


def test_search_pagination_dates_original_label_and_deletion(setup):
    _,db=setup;p=Purchases(db);r=add(p)
    assert p.search('JAJA')['total_matches']==1
    assert p.search('banany',since='2026-10-11')['total_matches']==0
    assert p.search(store='Lidl')['total_matches']==0
    first=p.search(store='ALDI',limit=1)
    assert first['total_matches']==2 and first['next_offset']==1
    assert len(p.search(limit=1,offset=1)['purchases'])==1
    with pytest.raises(ValueError):p.search(since='2026-10-11',until='2026-10-10')
    assert p.delete(r['receipt']['receipt_id'])['status']=='deleted'
    assert p.delete(r['receipt']['receipt_id'])['status']=='not_found'
    assert p.search()['total_matches']==0


def test_extended_receipt_discount_package_and_source(setup):
    _,db=setup;p=Purchases(db)
    text='Filet piKurMePakg 1,548 kg 38,55 rabat 18,44'
    r=add(p,store='Biedronka',purchased_at='2026-10-10T15:24:00+02:00',store_address='Kościuszki 71, Kraków',
          source_text=text,total_paid_pln='20.11',discount_total_pln='18.44',items=[{
              'name':'Filet z piersi kurczaka','receipt_name':'Filet piKurMePakg','category':'meat',
              'paid_pln':'20.11','original_paid_pln':'38.55','discount_pln':'18.44','quantity':'1.548','unit':'kg'}])
    receipt=p.receipt(r['receipt']['receipt_id'])
    assert receipt['source_text']==text and receipt['discount_total_checked']
    assert receipt['items'][0]['discount_pln']=='18.44'
    assert receipt['items'][0]['product_id']
    assert p.search('Filet')['purchases'][0]['store_address']=='Kościuszki 71, Kraków'
    pack=add(p,total_paid_pln='25.98',items=[{'name':'Burgery wołowe','paid_pln':'25.98','quantity':'2','unit':'pack',
                                        'package_quantity':'0.38','package_unit':'kg'}])['receipt']['items'][0]
    assert pack['comparison_quantity']=='0.76' and pack['comparison_unit']=='kg'
    assert pack['comparison_price_pln']=='34.184211'


@pytest.mark.parametrize('kwargs',[
    {'purchased_at':'2026-10-10T15:24:00'}, {'purchased_at':'2026-10-11T15:24:00+02:00'},
    {'discount_total_pln':'1','items':[{'name':'x','paid_pln':'23.47','discount_pln':'2'}]},
    {'items':[{'name':'x','paid_pln':'23.47','original_paid_pln':'20'}]},
    {'items':[{'name':'x','paid_pln':'23.47','original_paid_pln':'25','discount_pln':'1'}]},
    {'items':[{'name':'x','paid_pln':'23.47','quantity':'1.5','unit':'pack'}]},
    {'items':[{'name':'x','paid_pln':'23.47','quantity':'1','unit':'pack','package_quantity':'0.5'}]},
    {'items':[{'name':'x','paid_pln':'23.47','quantity':'1','unit':'kg','package_quantity':'0.5','package_unit':'kg'}]},
    {'items':[{'name':'x','paid_pln':'23.47','product_id':'missing'}]},
])
def test_extended_invalid_atomic(setup,kwargs):
    _,db=setup;p=Purchases(db)
    with pytest.raises(ValueError):add(p,**kwargs)
    assert p.search()['total_matches']==0 and p.products()['total_matches']==0


def test_product_links_preserve_source_and_distinct_variants(setup):
    _,db=setup;p=Purchases(db)
    first=add(p,items=[{'name':'Sól drobna','paid_pln':'23.47','quantity':'1','unit':'pack','package_quantity':'0.35','package_unit':'kg'}])
    second=add(p,receipt_reference='second',items=[{'name':'Sol dr 350','receipt_name':'SOL DR','paid_pln':'23.47','quantity':'1','unit':'pack','package_quantity':'0.350','package_unit':'kg'}])
    third=add(p,receipt_reference='third',items=[{'name':'Sól himalajska','paid_pln':'23.47','quantity':'1','unit':'pack','package_quantity':'0.5','package_unit':'kg'}])
    pid=first['receipt']['items'][0]['product_id'];rid=second['receipt']['receipt_id']
    p.link(rid,0,pid)
    assert p.search(product_id=pid)['total_matches']==2
    assert 'SOL DR' in next(x for x in p.products('SOL DR')['products'] if x['product_id']==pid)['aliases']
    assert p.receipt(rid)['items'][0]['name']=='Sol dr 350'
    with pytest.raises(ValueError):p.link(third['receipt']['receipt_id'],0,pid)
    with pytest.raises(ValueError):add(p,receipt_reference='bad',items=[{'name':'x','paid_pln':'23.47','quantity':'1','unit':'kg','product_id':pid}])


def test_historical_minimum_by_product_unit_and_store(setup):
    _,db=setup;p=Purchases(db)
    first=add(p,items=[{'name':'Kurczak','paid_pln':'12.99','quantity':'1','unit':'kg'}],total_paid_pln='12.99')
    pid=first['receipt']['items'][0]['product_id']
    add(p,purchased_on='2026-10-11',items=[{'name':'Kurczak','paid_pln':'16.99','quantity':'1','unit':'kg','product_id':pid}],total_paid_pln='16.99')
    add(p,store='Lidl',items=[{'name':'Kurczak','paid_pln':'15','quantity':'1','unit':'kg','product_id':pid}],total_paid_pln='15')
    result=p.price_history(pid,candidate_unit_price_pln='16.99',unit='kg')
    aldi=next(g for g in result['groups'] if g['store']=='Aldi')
    assert aldi['count']==2 and aldi['minimum']['price_pln']=='12.990000'
    assert aldi['latest']['price_pln']=='16.990000'
    assert next(g for g in result['comparison'] if g['store']=='Aldi')['difference_pln']=='4.000000'
    assert not p.price_history(pid,candidate_unit_price_pln='16.99',unit='pack')['comparison']
    assert p.price_history(pid,until='2026-10-10',store='Aldi')['purchases_count']==1


def test_legacy_receipts_backfill_idempotent_and_delete_cascade(setup):
    import json
    _,db=setup
    legacy={'receipt_id':'old','store':'Aldi','purchased_on':'2026-10-10','items':[{
        'name':'Banany','receipt_name':'BAN','paid_pln':'3.00','paid_grosz':300,'quantity':'1','unit':'kg'}]}
    with db.connect() as conn:
        conn.execute('INSERT INTO receipts VALUES(?,?,?,?)',('old','2026-10-10','Aldi',json.dumps(legacy)))
    p=Purchases(db);pid=p.receipt('old')['items'][0]['product_id']
    assert p.price_history(pid)['groups'][0]['minimum']['price_pln']=='3.000000'
    assert Purchases(db).search()['total_matches']==1
    p.delete('old')
    with db.connect() as conn:assert conn.execute('SELECT COUNT(*) FROM receipt_items').fetchone()[0]==0


def test_same_day_latest_uses_purchase_time_and_discount_unknown(setup):
    _,db=setup;p=Purchases(db)
    early=add(p,purchased_at='2026-10-10T09:00:00+02:00',items=[{'name':'Kurczak','paid_pln':'12','quantity':'1','unit':'kg'}],total_paid_pln='12')
    late=add(p,purchased_at='2026-10-10T15:00:00+02:00',items=[{'name':'Kurczak','paid_pln':'13','quantity':'1','unit':'kg'}],total_paid_pln='13',discount_total_pln='5')
    assert not late['receipt']['discount_total_checked']
    assert late['receipt']['items'][0]['discount_pln'] is None
    pid=early['receipt']['items'][0]['product_id']
    history=p.price_history(pid)
    assert history['groups'][0]['latest']['price_pln']=='13.000000'
    assert history['groups'][0]['minimum']['price_pln']=='12.000000'
