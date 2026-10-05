"""Daily background collection and explicitly requested shopping-only refresh."""
from dataclasses import replace
from datetime import datetime, timezone
import fcntl
import threading
from .collector import Collector, timestamp
from .model import today, WARSAW

class RefreshManager:
    def __init__(self, config, db):
        self.config,self.db = config,db
        self.worker = None
        self.stop_event = threading.Event()

    def status(self):
        state = self.db.refresh_state()
        with open(self.config.database+'.refresh.lock','a+') as lock:
            try:
                fcntl.flock(lock,fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {**state,'running':True}
        return {**state,'running':False,'state':'interrupted' if state.get('state')=='running' else state.get('state','idle')}

    def daily_due(self):
        if not self.config.auto_refresh:
            return False
        day = today()
        if self.db.refresh_state().get('attempt_day')==day:
            return False
        coverage = self.db.coverage()
        for store in self.config.stores:
            ts = coverage.get(store,{}).get('listing_fetched_at')
            if not ts or datetime.fromisoformat(ts).astimezone(WARSAW).date().isoformat()!=day:
                return True
        return False

    def start(self, force=False, daily=False):
        lock = open(self.config.database+'.refresh.lock','a+')
        try:
            fcntl.flock(lock,fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            return {'accepted':False,'reason':'already_running','refresh':self.status()}
        state = self.db.refresh_state()
        if daily and not self.daily_due():
            lock.close()
            return {'accepted':False,'reason':'already_checked_today','refresh':self.status()}
        ts = state.get('started_at')
        if ts and (datetime.now(timezone.utc)-datetime.fromisoformat(ts)).total_seconds()<300:
            lock.close()
            return {'accepted':False,'reason':'cooldown_5_minutes','refresh':self.status()}
        state={'state':'running','attempt_day':today(),'started_at':timestamp(),'force':force or daily,'error':None}
        self.db.save_refresh_state(state)
        self.worker = threading.Thread(target=self._collect,args=(lock,state),daemon=True,name='blix-refresh')
        self.worker.start()
        return {'accepted':True,'reason':'started_in_background','refresh':{**state,'running':True},
                'next_step':'Use data_status later; existing cached offers remain available while updating.'}

    def _collect(self, lock, state):
        try:
            # Tiny positive TTL bypasses HTML cache, while preserving robots and request limits.
            cfg = replace(self.config,cache_hours=1e-12) if state['force'] else self.config
            coverage = Collector(cfg,self.db).refresh()
            failures = any(r.get('errors') or any(p.get('errors') for p in r.get('leaflets',[])) for s,r in coverage.items() if s in cfg.stores)
            state={**state,'state':'completed_with_errors' if failures else 'completed','finished_at':timestamp()}
        except Exception as e:
            state={**state,'state':'failed','finished_at':timestamp(),'error':str(e)}
        finally:
            self.db.save_refresh_state(state)
            lock.close()

    def start_daily_scheduler(self):
        def loop():
            while not self.stop_event.is_set():
                if self.daily_due():
                    self.start(daily=True)
                self.stop_event.wait(60)
        if self.config.auto_refresh:
            threading.Thread(target=loop,daemon=True,name='blix-daily-refresh').start()
