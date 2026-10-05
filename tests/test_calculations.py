from decimal import Decimal
import pytest
from blixscraper.engine import quote,Shopping,validate_evidence
from conftest import offer

def test_whole_packs_extra_and_unit_price(setup):
    _,db=setup
    q,_=quote(offer(db),{'quantity':'0.45','unit':'kg'})
    assert (q['packs'],q['total_pln'],q['extra'],q['effective_unit_price_pln'])==(3,'14.97','0.15','24.95')

@pytest.mark.parametrize('qty,total,packs', [('0.2','4.99',1),('0.4','9.98',2),('0.6','9.98',3),('0.8','14.97',4),('1.2','19.96',6)])
def test_two_plus_one(setup,qty,total,packs):
    _,db=setup
    q,_=quote(offer(db,buy=2,free=1),{'quantity':qty,'unit':'kg'})
    assert (q['total_pln'],q['packs'])==(total,packs)

def test_minimum_and_limit(setup):
    _,db=setup
    o=offer(db,min_packs=3,max_packs=3)
    q,_=quote(o,{'quantity':'.2','unit':'kg'})
    assert q['extra']=='0.4' and q['total_pln']=='14.97'
    assert quote(o,{'quantity':'.8','unit':'kg'})[0] is None

def test_card_and_coupon_activation(setup):
    _,db=setup
    o=offer(db,required_cards=['Moja Biedronka'],required_coupons=['butter'])
    i={'quantity':'.4','unit':'kg'}
    assert quote(o,i)[0] is None
    assert quote(o,i,['Moja Biedronka'])[0] is None
    assert quote(o,i,['Moja Biedronka'],['butter'])[0]['total_pln']=='9.98'

def test_loose_kg_and_rounding(setup):
    _,db=setup
    o=offer(db,name='Banany',price=299,basis='per_unit',unit='kg')
    q,_=quote(o,{'quantity':'1.125','unit':'kg'})
    assert q['packs'] is None and q['total_pln']=='3.36' and q['extra']=='0.000'

def test_missing_conditions_are_not_guessed(setup):
    _,db=setup
    assert quote(offer(db,verified=False),{'quantity':'.4','unit':'kg'})[0] is None

@pytest.mark.parametrize('field,value',[('package_quantity','NaN'),('package_quantity',0),('price_grosz',True),('min_packs',0),('free',1)])
def test_bad_evidence(setup,field,value):
    _,db=setup
    o=offer(db)
    e={**o['evidence'],field:value}
    with pytest.raises((ValueError,ArithmeticError)):
        validate_evidence(o,e)

def test_confirmations_invalidated_on_raw_price_change(setup):
    _,db=setup
    offer(db)
    changed=offer(db,price=599,verified=False)
    assert 'evidence' not in changed

def test_complete_two_store_basket_vs_partial(setup):
    cfg,db=setup
    offer(db,'biedronka','Masło 82% 200 g',499)
    offer(db,'lidl','Masło 82% 200 g',399)
    offer(db,'biedronka','Mleko 3,2% 1 l',299,unit='l',package_quantity=1)
    shop=Shopping(cfg,db)
    result=shop.compare([{'query':'masło','quantity':'.4','unit':'kg','fat_percent':82},
                         {'query':'mleko','quantity':2,'unit':'l','fat_percent':3.2}])
    assert result['best_complete']['total_pln']=='13.96'
    assert result['best_complete']['stores']==['biedronka','lidl']
    assert next(p for p in result['one_store'] if p['selected_stores']==['lidl'])['complete'] is False
    assert shop.compare([{'query':'jaja','quantity':10,'unit':'piece'}])['best_complete'] is None

def test_one_store_constraint(setup):
    cfg,db=setup
    offer(db,'biedronka','Masło',499)
    offer(db,'lidl','Masło',299)
    offer(db,'biedronka','Mleko',299,unit='l',package_quantity=1)
    r=Shopping(cfg,db).compare([{'query':'masło','quantity':'.4','unit':'kg'},{'query':'mleko','quantity':2,'unit':'l'}],max_stores=1)
    assert r['best_complete']['total_pln']=='15.96'

@pytest.mark.parametrize('qty', ['0','-1','NaN','Infinity'])
def test_bad_basket_quantity(setup,qty):
    cfg,db=setup
    with pytest.raises((ValueError,ArithmeticError)):
        Shopping(cfg,db).compare([{'query':'mleko','quantity':qty,'unit':'l'}])

def test_overlap_rejected(setup):
    cfg,db=setup
    offer(db)
    with pytest.raises(ValueError,match='Overlapping'):
        Shopping(cfg,db).compare([{'query':'masło','quantity':'.2','unit':'kg'}]*2)

def test_stale_offer_excluded(setup):
    cfg,db=setup
    o=offer(db)
    o['fetched_at']='2000-01-01T00:00:00+00:00'
    db.save_offers([o])
    r=Shopping(cfg,db).compare([{'query':'masło','quantity':'.4','unit':'kg'}])
    assert r['best_complete'] is None and 'stale' in r['unconfirmed'][0]['reason']

def test_required_terms_fat_and_explicit_substitution(setup):
    cfg,db=setup
    offer(db,name='Kawa ziarnista 500 g')
    offer(db,name='Kawa mielona 500 g')
    shop=Shopping(cfg,db)
    r=shop.compare([{'query':'kawa','quantity':'.5','unit':'kg','required_terms':['mielona']}])
    assert all('mielona' in q['name'] for q in r['best_complete']['lines'])
    assert shop.compare([{'query':'masło','quantity':'.4','unit':'kg','fat_percent':82}])['best_complete'] is None

def test_daily_eligibility_and_loose_limit(setup):
    _,db=setup
    o=offer(db,name='Banany',price=299,basis='per_unit',unit='kg',max_quantity=2,
            required_cards=['Kaufland Card XTRA'],required_confirmations=['first_receipt_today'])
    i={'quantity':1,'unit':'kg'}
    assert quote(o,i,['Kaufland Card XTRA'])[0] is None
    assert quote(o,i,['Kaufland Card XTRA'],[],['first_receipt_today'])[0]['total_pln']=='2.99'
    assert quote(o,{**i,'quantity':3},['Kaufland Card XTRA'],[],['first_receipt_today'])[0] is None

@pytest.mark.parametrize('pieces,total',[(10,'13.49'),(20,'19.98'),(30,'33.47'),(40,'39.96'),(11,'19.98')])
def test_every_second_egg_pack(setup,pieces,total):
    _,db=setup
    o=offer(db,name='Jajka z wolnego wybiegu L',price=1349,unit='piece',package_quantity=10,
            pack_price_cycle_grosz=[1349,649])
    q,_=quote(o,{'quantity':pieces,'unit':'piece'})
    assert q['total_pln']==total
    assert q['extra']==str(q['packs']*10-pieces)

@pytest.mark.parametrize('cycle',[[1349],[999,649],[1349,-1],[1349,True],'1349,649'])
def test_invalid_pack_price_cycle(setup,cycle):
    _,db=setup
    o=offer(db)
    with pytest.raises(ValueError):
        validate_evidence(o,{**o['evidence'],'pack_price_cycle_grosz':cycle})

def test_cycle_does_not_stack_with_free_packs(setup):
    _,db=setup
    o=offer(db)
    with pytest.raises(ValueError):
        validate_evidence(o,{**o['evidence'],'pack_price_cycle_grosz':[499,199],'buy':2,'free':1})
