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
