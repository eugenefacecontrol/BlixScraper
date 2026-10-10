from pathlib import Path
import json
import sqlite3
from contextlib import contextmanager

class Database:
    def __init__(self, path):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS products(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS receipt_items(receipt_id TEXT NOT NULL REFERENCES receipts(id) ON DELETE CASCADE,
                line_index INTEGER NOT NULL, product_id TEXT NOT NULL REFERENCES products(id), payload TEXT NOT NULL,
                PRIMARY KEY(receipt_id,line_index));
            CREATE INDEX IF NOT EXISTS receipt_items_product ON receipt_items(product_id);
            CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY, purchased_on TEXT NOT NULL, store TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS offers(id TEXT PRIMARY KEY, store TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS pages(url TEXT PRIMARY KEY, fetched_at TEXT NOT NULL, html TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS archive_coverage(store TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS coverage(store TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS refresh_state(id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS evidence(offer_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            ''')

    @contextmanager
    def connect(self):
        with sqlite3.connect(self.path, timeout=30) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            yield db

    def save_offers(self, offers):
        with self.connect() as db:
            db.executemany('INSERT OR REPLACE INTO offers VALUES(?,?,?)', [(o['id'],o['store'],json.dumps(o,ensure_ascii=False)) for o in offers])

    def offers(self):
        with self.connect() as db:
            result = [json.loads(r['payload']) for r in db.execute('SELECT payload FROM offers ORDER BY id')]
            evidence = {r['offer_id']:json.loads(r['payload']) for r in db.execute('SELECT * FROM evidence')}
        for o in result:
            if o['id'] in evidence:
                e = evidence[o['id']]
                # Evidence cannot silently transfer to changed prices or products.
                if e['raw_snapshot'] == o['raw']:
                    o['evidence'] = e
                    o['verification'] = 'locally_verified'
        return result

    def page(self, url):
        with self.connect() as db:
            r = db.execute('SELECT * FROM pages WHERE url=?',(url,)).fetchone()
            return dict(r) if r else None

    def save_page(self, url, timestamp, html):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO pages VALUES(?,?,?)',(url,timestamp,html))

    def save_coverage(self, store, data):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO coverage VALUES(?,?)',(store,json.dumps(data)))

    def coverage(self):
        with self.connect() as db:
            return {r['store']:json.loads(r['payload']) for r in db.execute('SELECT * FROM coverage')}

    def verify(self, offer_id, evidence):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO evidence VALUES(?,?)',(offer_id,json.dumps(evidence)))

    def refresh_state(self):
        with self.connect() as db:
            row = db.execute('SELECT payload FROM refresh_state WHERE id=1').fetchone()
            return json.loads(row['payload']) if row else {}

    def save_refresh_state(self, state):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO refresh_state VALUES(1,?)',(json.dumps(state),))

    def save_archive_coverage(self, store, report):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO archive_coverage VALUES(?,?)',(store,json.dumps(report)))

    def archive_coverage(self):
        with self.connect() as db:
            return {r['store']:json.loads(r['payload']) for r in db.execute('SELECT * FROM archive_coverage')}
