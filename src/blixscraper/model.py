from datetime import datetime
from decimal import Decimal
from hashlib import sha256
import json
import re
import unicodedata
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo('Europe/Warsaw')

def today():
    return datetime.now(WARSAW).date().isoformat()

def fold(text):
    text = text.lower().replace('ł', 'l')
    return ''.join(c for c in unicodedata.normalize('NFKD', text) if not unicodedata.combining(c))

def extract_offers(html):
    m = re.search(r'window\.offers\s*=\s*', html)
    if not m:
        raise ValueError('window.offers missing; coverage unknown')
    data = json.JSONDecoder().raw_decode(html[m.end():])[0]
    if not isinstance(data, list) or any(not isinstance(o, dict) for o in data):
        raise ValueError('Unsupported offer schema')
    return data

def datepart(value):
    if isinstance(value, dict):
        value = value.get('date')
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value[:10]).date().isoformat()
    except ValueError:
        return None

def active(offer, day):
    return bool(offer.get('valid_from') and offer.get('valid_to') and offer['valid_from'] <= day <= offer['valid_to'])

def package_from_name(name):
    """Only unambiguous quantities in product names; never infer from category."""
    matches = list(re.finditer(r'(?<![\w.,])(?:(\d+)\s*[x×]\s*)?(\d+(?:[.,]\d+)?)\s*(kg|ml|g|l|szt\.?)(?!\w)', fold(name)))
    if len(matches) != 1:
        return None
    m = matches[0]
    n = Decimal(m[2].replace(',', '.')) * Decimal(m[1] or 1)
    unit = m[3].rstrip('.')
    if unit in ('g', 'ml'):
        n /= 1000
    return {'quantity': str(n), 'unit': {'g':'kg','ml':'l','szt':'piece'}.get(unit, unit)} if n > 0 else None

def normalize(raw, store, source, fetched_at):
    price = raw.get('price')
    price = price if isinstance(price, int) and not isinstance(price, bool) and price > 0 else None
    identity = [store, raw.get('leafletId'), raw.get('productLeafletPageUuid'), raw.get('hash'), raw.get('name'), raw.get('pageNumber'), raw.get('area')]
    id_ = sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]
    leaflet = raw.get('leafletId')
    page = raw.get('pageNumber')
    url = f'https://blix.pl/sklep/{store}/gazetka/{leaflet}/?pageNumber={page+1}' if isinstance(leaflet, int) and isinstance(page, int) else source
    return {'id': id_, 'store':store, 'name':raw.get('name') or '', 'brand':raw.get('brandName'),
            'price_grosz':price, 'display_price_pln':str(Decimal(price)/100) if price else None,
            'valid_from':datepart(raw.get('dateStart')), 'valid_to':datepart(raw.get('dateEnd')),
            'leaflet_id':leaflet, 'page_number':page, 'display_page_number':page+1 if isinstance(page,int) else None, 'source_url':url, 'collected_from':source,
            'image_url':raw.get('image'), 'area':raw.get('area'), 'fetched_at':fetched_at,
            'package_hint':package_from_name(raw.get('name') or ''),
            'verification':'requires_review', 'warnings':['JSON does not confirm price basis or complete promotion conditions. Check leaflet image.'],
            'raw':raw}
