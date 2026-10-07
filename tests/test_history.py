from dataclasses import replace
from datetime import datetime, timezone
import json
import httpx
import pytest
from blixscraper.collector import Collector, listing
from blixscraper.engine import Shopping
from blixscraper.model import normalize

CARD='<div class="leaflet" data-brand-slug="aldi" data-leaflet-id="100" data-date-start="September 28, 2026 00:00" data-date-end="October 3, 2026 23:59"></div>'

def test_expired_discovery_overlap_and_store():
    assert listing(CARD,'aldi','2026-10-07','2026-10-01','2026-10-02')[0][0]['id']=='100'
    assert not listing(CARD,'aldi','2026-10-07','2026-10-04','2026-10-06')[0]
    assert not listing(CARD,'lidl','2026-10-07','2026-09-01','2026-10-06')[0]
    assert not listing(CARD,'aldi','2026-10-02','2026-09-01','2026-10-02')[0]

def test_history_dedup_pagination_and_old_fetch(setup,monkeypatch):
    cfg,db=setup
    monkeypatch.setattr('blixscraper.engine.today',lambda:'2026-10-07')
    for page,end in [(0,'2026-10-03'),(1,'2026-10-03'),(2,'2026-10-10')]:
        raw={'name':'Jajka wolny wybieg','price':1349,'leafletId':100,'pageNumber':page,
             'dateStart':'2026-09-28','dateEnd':end}
        db.save_offers([normalize(raw,'aldi','https://blix.pl/sklep/aldi/','2020-01-01T00:00:00+00:00')])
    shop=Shopping(cfg,db)
    r=shop.history('яйца','2026-10-01','2026-10-02',limit=1)
    assert r['total_matches']==2 and r['distinct_leaflets']==1 and r['next_offset']==1
    assert r['promotion_start_weekdays']=={'aldi':{'Monday':1}}
    assert r['offers'][0]['source_url'].startswith('https://blix.pl/')
    assert not r['catalog_complete']
    assert shop.history('яйца','2026-10-04')['total_matches']==0
    for kwargs in [{'since':'2026-10-08','until':'2026-10-01'},{'since':'bad'},{'since':'2026-09-01','stores':['evil']}]:
        with pytest.raises(ValueError): shop.history('яйца',**kwargs)

def test_collection_separate_coverage_and_limit(setup,monkeypatch):
    cfg,db=setup
    monkeypatch.setattr('blixscraper.collector.today',lambda:'2026-10-07')
    cfg=replace(cfg,max_pages_per_leaflet=1)
    db.save_coverage('aldi',{'status':'current'})
    c=Collector(cfg,db)
    raw={'name':'Jajka','price':1349,'leafletId':100,'pageNumber':0,'dateStart':'2026-09-28','dateEnd':'2026-10-03'}
    html='window.offers = '+json.dumps([raw])+';'
    monkeypatch.setattr(c,'get',lambda url:(CARD+CARD.replace('100','101')+html if url.endswith('/aldi/') else html,'2026-10-07T00:00:00+00:00'))
    r=c.collect_store('aldi','2026-09-01','2026-10-06',1)
    assert r['leaflet_cap_reached'] and len(r['archive_leaflets'])==1
    assert db.coverage()['aldi']=={'status':'current'}
    assert db.archive_coverage()['aldi']['requested_range']['since']=='2026-09-01'
    assert db.offers()
    c.client.close()

def test_archive_denial_preserves_previous_data(setup,monkeypatch):
    cfg,db=setup
    c=Collector(cfg,db)
    c.client.close()
    c.client=httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(403,request=req)))
    r=c.collect_store('aldi','2026-09-01','2026-10-06',1)
    assert r['status']=='error' and r['errors'] and 'aldi' in c.blocked_stores
    assert db.archive_coverage()['aldi']['status']=='error'
    c.client.close()
