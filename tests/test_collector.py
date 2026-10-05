from datetime import datetime,timezone
from dataclasses import replace
import json
import httpx
import pytest
from blixscraper.collector import Collector,listing,public_url,page_links
from blixscraper.model import extract_offers,normalize,package_from_name,active,today

# Two independent public Blix constructs sampled during research: Biedronka and Aldi cards.
CARDS='''<div class="leaflet" data-brand-slug="biedronka" data-leaflet-id="123"
 data-date-start="October 5, 2026 00:00" data-date-end="October 10, 2026 23:59"><a href="/sklep/biedronka/gazetka/123/">A</a></div>
 <div class="leaflet" data-brand-slug="aldi" data-leaflet-id="456"
 data-date-start="October 5, 2026 00:00" data-date-end="October 10, 2026 23:59"><a href="/sklep/aldi/gazetka/456/">B</a></div>'''

def test_discovery_all_active_and_unknown_dates():
    refs,unknown=listing(CARDS+'<a href="/sklep/biedronka/gazetka/789/">unknown</a>','biedronka','2026-10-05')
    assert [r['id'] for r in refs]==['123'] and unknown==['789']
    assert listing(CARDS,'aldi','2026-10-05')[0][0]['id']=='456'
    assert not listing(CARDS,'biedronka','2026-10-11')[0]

def test_safe_json_not_js_eval():
    assert extract_offers('window.offers = [{"name":"a ] \\\" b", "price":249}]; evil()')[0]['price']==249
    with pytest.raises(ValueError): extract_offers('window.offers=evil()')
    with pytest.raises(ValueError): extract_offers('no offers')

@pytest.mark.parametrize('url',['http://blix.pl/sklep/lidl/','https://blix.pl/api/offers','https://evil.com/','https://blix.pl@evil.com/sklep/lidl/','https://blix.pl/sklep/../../api/x'])
def test_url_allowlist(url): assert not public_url(url)

def test_only_discovered_pages():
    base='https://blix.pl/sklep/aldi/gazetka/123/'
    h='<a href="?pageNumber=2">p2</a><a href="/api/offers">api</a><div data-url="?pageNumber=3"></div>'
    assert page_links(h,base)==[base,base+'?pageNumber=2',base+'?pageNumber=3']

@pytest.mark.parametrize('name,expected',[('Mleko 1 l',{'quantity':'1','unit':'l'}),('Masło 200 g',{'quantity':'0.2','unit':'kg'}),('Jaja 10 szt.',{'quantity':'10','unit':'piece'}),('Kawa 2 x 250 g',{'quantity':'0.5','unit':'kg'}),('Kawa 250 g lub 500 g',None),('Banany',None)])
def test_quantity_hints(name,expected): assert package_from_name(name)==expected

def test_price_null_date_and_duplicates(setup):
    _,db=setup
    raw={'name':'Masło','price':249,'leafletId':123,'pageNumber':1}
    o=normalize(raw,'aldi','https://blix.pl/sklep/aldi/',datetime.now(timezone.utc).isoformat())
    assert o['display_price_pln']=='2.49' and not active(o,today())
    db.save_offers([o,o]); assert len(db.offers())==1
    assert normalize({**raw,'price':None},'aldi',o['source_url'],o['fetched_at'])['price_grosz'] is None

def test_access_denial_stops_store_and_preserves_failure(setup):
    cfg,db=setup
    collector=Collector(cfg,db)
    calls=[]
    def handler(req):
        calls.append(str(req.url))
        return httpx.Response(403,request=req)
    collector.client.close()
    collector.client=httpx.Client(transport=httpx.MockTransport(handler))
    collector.collect_store('biedronka')
    r=db.coverage()['biedronka']
    assert r['status']=='error' and r['catalog_complete'] is False and r['errors']
    with pytest.raises(RuntimeError): collector.get('https://blix.pl/sklep/biedronka/gazetka/123/')
    assert len(calls)==1
    collector.client.close()

def test_cache_timestamp_and_no_network(setup):
    cfg,db=setup
    ts=datetime.now(timezone.utc).isoformat()
    url='https://blix.pl/sklep/aldi/'
    db.save_page(url,ts,'saved')
    c=Collector(cfg,db)
    assert c.get(url)==('saved',ts)
    c.client.close()

def test_json_page_zero_based_proof_link():
    o=normalize({'name':'Milk','leafletId':123,'pageNumber':0},'aldi','https://blix.pl/sklep/aldi/','2026-10-05T00:00:00+00:00')
    assert o['source_url'].endswith('?pageNumber=1') and o['display_page_number']==1
