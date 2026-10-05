from dataclasses import dataclass
from pathlib import Path
import re
import tomllib

@dataclass(frozen=True)
class Config:
    stores: tuple[str, ...] = ('biedronka', 'lidl', 'aldi', 'kaufland')
    database: str = 'data/blix.sqlite3'
    request_interval_seconds: float = 2.0
    cache_hours: float = 6.0
    stale_hours: float = 24.0
    max_pages_per_leaflet: int = 150

    def __post_init__(self):
        if not 1 <= len(self.stores) <= 12 or any(not re.fullmatch(r'[a-z0-9-]+', s) for s in self.stores):
            raise ValueError('Configure 1..12 store slugs')
        if len(set(self.stores)) != len(self.stores):
            raise ValueError('Duplicate stores')
        if self.request_interval_seconds < 1 or self.cache_hours <= 0 or self.stale_hours <= 0:
            raise ValueError('Interval >= 1 second and positive cache ages required')
        if not 1 <= self.max_pages_per_leaflet <= 500:
            raise ValueError('Page cap must be 1..500')

def load_config(path='config.toml'):
    p = Path(path)
    data = tomllib.loads(p.read_text()) if p.exists() else {}
    if 'stores' in data:
        data['stores'] = tuple(data['stores'])
    return Config(**data)
