from datetime import datetime,timezone
import pytest
from blixscraper.config import Config
from blixscraper.db import Database
from blixscraper.model import normalize,today
from blixscraper.engine import validate_evidence

@pytest.fixture
def setup(tmp_path):
    config=Config(database=str(tmp_path/'test.sqlite3'))
    return config,Database(config.database)

def offer(db,store='biedronka',name='Masło 82% 200 g',price=499,verified=True,**evidence):
    raw={'name':name,'price':price,'leafletId':123,'pageNumber':1,'hash':name+store,
         'dateStart':{'date':today()+' 00:00:00'},'dateEnd':{'date':today()+' 23:59:59'}}
    o=normalize(raw,store,'https://blix.pl/sklep/'+store+'/',datetime.now(timezone.utc).isoformat())
    db.save_offers([o])
    if verified:
        e={'conditions_complete':True,'source_url':o['source_url'],'note':'Synthetic test evidence',
           'basis':'package','package_quantity':'0.2','unit':'kg','price_grosz':price,**evidence}
        validate_evidence(o,e)
        e['raw_snapshot']=raw
        db.verify(o['id'],e)
    return next(x for x in db.offers() if x['id']==o['id'])
