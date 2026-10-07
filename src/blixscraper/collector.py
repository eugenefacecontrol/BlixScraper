"""Only public HTML, robots-aware, cached, sequential requests; no private API."""
from datetime import datetime, timezone
import re
import fcntl
from pathlib import Path
import time
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser
from bs4 import BeautifulSoup
import httpx
from .model import extract_offers, normalize, today

BASE = 'https://blix.pl'
USER_AGENT = 'BlixScraper/0.1 (local personal shopping research)'

def timestamp():
    return datetime.now(timezone.utc).isoformat()

def public_url(url):
    u = urlparse(url)
    return u.scheme == 'https' and u.netloc == 'blix.pl' and (u.path == '/robots.txt' or bool(re.fullmatch(r'/sklep/[a-z0-9-]+/(?:gazetka/\d+/)?',u.path)))

def listing(html, store, day, since=None, until=None):
    soup = BeautifulSoup(html, 'html.parser')
    refs, unknown = {}, []
    for node in soup.select('.leaflet[data-leaflet-id]'):
        if node.get('data-brand-slug') != store:
            continue
        id_ = node['data-leaflet-id']
        if not id_.isdigit():
            continue
        try:
            start = datetime.strptime(node['data-date-start'], '%B %d, %Y %H:%M').date().isoformat()
            end = datetime.strptime(node['data-date-end'], '%B %d, %Y %H:%M').date().isoformat()
        except (KeyError, ValueError):
            unknown.append(id_)
            continue
        if (since is None and start <= day <= end) or (since is not None and start <= until and end >= since and end < day):
            refs[id_] = {'id':id_, 'url':f'{BASE}/sklep/{store}/gazetka/{id_}/', 'start':start, 'end':end}
    all_ids = set(re.findall(r'/sklep/'+re.escape(store)+r'/gazetka/(\d+)',html))
    classified = {n.get('data-leaflet-id') for n in soup.select('.leaflet[data-leaflet-id]') if n.get('data-brand-slug')==store}
    unknown = sorted(set(unknown) | (all_ids-classified))
    return list(refs.values()), unknown

def page_links(html, base):
    """Follow only page URLs actually linked by HTML, never fabricate API calls."""
    soup = BeautifulSoup(html, 'html.parser')
    links = {base}
    for n in soup.select('[href], [data-url]'):
        u = urljoin(base, n.get('href') or n.get('data-url') or '')
        p = urlparse(u)
        if public_url(u) and p.path == urlparse(base).path and re.fullmatch(r'pageNumber=\d+',p.query):
            links.add(u)
    return sorted(links)

class Collector:
    def __init__(self, config, db):
        self.config, self.db = config, db
        self.last_request = 0.0
        self.blocked_stores = set()
        self.client = httpx.Client(timeout=30, follow_redirects=False, headers={'User-Agent':USER_AGENT})
        self.robots = None

    def get(self, url):
        if not public_url(url):
            raise ValueError('URL outside shopping allowlist')
        if self.robots and not self.robots.can_fetch(USER_AGENT,url):
            raise ValueError('robots.txt disallows URL')
        cached = self.db.page(url)
        if cached and (datetime.now(timezone.utc)-datetime.fromisoformat(cached['fetched_at'])).total_seconds() < self.config.cache_hours*3600:
            return cached['html'], cached['fetched_at']
        store = urlparse(url).path.split('/')[2] if '/sklep/' in url else None
        if store in self.blocked_stores:
            raise RuntimeError('Store collection stopped after access/rate-limit denial')
        # Coordinate HTTP requests across local refresh processes sharing a database.
        with open(self.config.database+'.network.lock','a+') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            lock.seek(0)
            previous = float(lock.read() or '0')
            interval = max(self.config.request_interval_seconds, self.robots.crawl_delay(USER_AGENT) or 0) if self.robots else self.config.request_interval_seconds
            time.sleep(max(0,interval-(time.time()-previous)))
            lock.seek(0); lock.truncate(); lock.write(str(time.time())); lock.flush()
            response = self.client.get(url)
        if response.status_code in (401,403,429):
            self.blocked_stores.add(store)
        response.raise_for_status()
        ts = timestamp()
        self.db.save_page(url,ts,response.text)
        return response.text,ts

    def refresh(self):
        try:
            robots,_ = self.get(BASE+'/robots.txt')
            self.robots = RobotFileParser()
            self.robots.parse(robots.splitlines())
            delay = self.robots.crawl_delay(USER_AGENT)
            if delay:
                time.sleep(delay)
            for store in self.config.stores:
                self.collect_store(store)
            return self.db.coverage()
        finally:
            self.client.close()

    def collect_store(self, store, since=None, until=None, max_leaflets=5):
        report = {'attempted_at':timestamp(), 'day':today(), 'status':'partial', 'discovery_complete':False,
                  'active_leaflets':[], 'unknown_date_leaflets':[], 'leaflets':[], 'errors':[], 'offers_collected':0,
                  'catalog_complete':False, 'reason':'Public HTML does not prove a complete catalogue or promotion terms.'}
        collected = {}
        def ingest(html,url,ts,expected=None):
            rows = extract_offers(html)
            soup = BeautifulSoup(html,'html.parser')
            images = {}
            for n in soup.select('.page-wrapper[data-page-number]'):
                im = n.select_one('img')
                if im:
                    images[n['data-page-number']] = im.get('data-src') or im.get('src')
            for r in rows:
                if expected is not None and str(r.get('leafletId')) != expected:
                    continue
                o = normalize(r,store,url,ts)
                if isinstance(o['page_number'],int):
                    o['leaflet_page_image_url'] = images.get(str(o['page_number']+1))
                collected[o['id']] = o
            return len(rows)
        try:
            url = f'{BASE}/sklep/{store}/'
            html,ts = self.get(url)
            report['listing_fetched_at'] = ts
            try:
                ingest(html,url,ts)
            except ValueError as e:
                report['errors'].append(str(e))
            refs,unknown = listing(html,store,today(),since,until)
            if since is not None:
                refs.sort(key=lambda r:(r["start"],r["id"]),reverse=True)
                report["visible_matching_leaflets"] = len(refs)
                report["leaflet_cap_reached"] = len(refs)>max_leaflets
                refs = refs[:max_leaflets]
                report["requested_range"] = {"since":since,"until":until}
            report['archive_leaflets' if since is not None else 'active_leaflets'] = refs
            report['unknown_date_leaflets'] = unknown
            # Even all visible refs are only observed discovery, not proven exhaustive.
            report['discovery_complete'] = False
            for ref in refs:
                lr = {'id':ref['id'], 'pages_discovered':0,'pages_fetched':0,'errors':[], 'complete':False}
                report['leaflets'].append(lr)
                if store in self.blocked_stores:
                    lr['errors'].append('Skipped after access denial; no bypass/retry')
                    continue
                pending,seen = [ref['url']],set()
                while pending and len(seen)<self.config.max_pages_per_leaflet:
                    u = pending.pop(0)
                    if u in seen:
                        continue
                    seen.add(u)
                    try:
                        h,t = self.get(u)
                        ingest(h,u,t,ref['id'])
                        lr['pages_fetched'] += 1
                        pending += [p for p in page_links(h,ref['url']) if p not in seen and p not in pending]
                    except (httpx.HTTPError, ValueError, RuntimeError) as e:
                        lr['errors'].append(f'{u}: {e}')
                        if store in self.blocked_stores:
                            break
                lr['pages_discovered'] = len(seen | set(pending))
                lr['page_cap_reached'] = bool(pending)
        except (httpx.HTTPError,ValueError,RuntimeError) as e:
            report['errors'].append(str(e))
            report['status'] = 'error'
        self.db.save_offers(list(collected.values()))
        report['offers_collected'] = len(collected)
        report['finished_at'] = timestamp()
        if since is None:
            self.db.save_coverage(store,report)
        else:
            self.db.save_archive_coverage(store,report)
        return report
