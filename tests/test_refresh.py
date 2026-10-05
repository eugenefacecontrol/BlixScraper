from dataclasses import replace
from datetime import datetime,timezone,timedelta
from threading import Event
from blixscraper.refresh import RefreshManager
from blixscraper.model import today


def test_daily_only_once_per_warsaw_day(setup,monkeypatch):
    cfg,db=setup
    calls=[]
    class FakeCollector:
        def __init__(self,c,d): calls.append(c)
        def refresh(self): return {}
    monkeypatch.setattr('blixscraper.refresh.Collector',FakeCollector)
    manager=RefreshManager(cfg,db)
    assert manager.daily_due()
    assert manager.start(daily=True)['accepted']
    manager.worker.join(timeout=2)
    assert not manager.daily_due()
    assert manager.status()['state']=='completed'
    assert len(calls)==1


def test_current_listing_requires_no_daily_collection(setup):
    cfg,db=setup
    for s in cfg.stores:
        db.save_coverage(s,{'listing_fetched_at':datetime.now(timezone.utc).isoformat()})
    assert not RefreshManager(cfg,db).daily_due()


def test_force_bypasses_cache_not_rate_limit_and_deduplicates(setup,monkeypatch):
    cfg,db=setup
    entered,release=Event(),Event()
    configs=[]
    class FakeCollector:
        def __init__(self,c,d): configs.append(c)
        def refresh(self):
            entered.set(); release.wait(2)
            return {}
    monkeypatch.setattr('blixscraper.refresh.Collector',FakeCollector)
    manager=RefreshManager(cfg,db)
    try:
        assert manager.start(force=True)['accepted']
        assert entered.wait(2)
        assert RefreshManager(cfg,db).start()['reason']=='already_running'
        assert manager.status()['running']
    finally:
        release.set(); manager.worker.join(timeout=2)
    assert configs[0].cache_hours<cfg.cache_hours
    assert configs[0].request_interval_seconds==cfg.request_interval_seconds
    assert manager.start(force=True)['reason']=='cooldown_5_minutes'


def test_failure_keeps_previous_data_and_attempt_day(setup,monkeypatch):
    cfg,db=setup
    db.save_coverage('aldi',{'listing_fetched_at':'2026-01-01T00:00:00+00:00'})
    class BrokenCollector:
        def __init__(self,c,d): pass
        def refresh(self): raise RuntimeError('network failure')
    monkeypatch.setattr('blixscraper.refresh.Collector',BrokenCollector)
    manager=RefreshManager(cfg,db)
    manager.start(daily=True); manager.worker.join(timeout=2)
    assert manager.status()['state']=='failed'
    assert db.coverage()['aldi']['listing_fetched_at'].startswith('2026-01-01')
    assert not manager.daily_due()


def test_interrupted_refresh_and_next_day_retry(setup):
    cfg,db=setup
    db.save_refresh_state({'state':'running','attempt_day':'2000-01-01','started_at':'2000-01-01T00:00:00+00:00'})
    manager=RefreshManager(cfg,db)
    assert manager.status()['state']=='interrupted'
    assert manager.daily_due()
    assert not RefreshManager(replace(cfg,auto_refresh=False),db).daily_due()
