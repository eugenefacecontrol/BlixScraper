"""Exact Decimal arithmetic on locally reviewed evidence, never on guessed terms."""
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP
from itertools import combinations
import re
from .model import fold, active, today

ALIASES = {'молоко':['mleko'], 'масло':['maslo'], 'яйца':['jaja','jajka'], 'бананы':['banany'],
           'кофе':['kawa'], 'молотый кофе':['kawa mielona'], 'масло сливочное':['maslo'],
           'сливочное масло':['maslo']}
UNITS = {'kg','l','piece'}

def decimal(value):
    n = Decimal(str(value))
    if not n.is_finite() or n<=0:
        raise ValueError('Quantity must be finite and positive')
    return n

def money(value):
    return str(value.quantize(Decimal('.01'), rounding=ROUND_HALF_UP))

def terms_match(name, phrase):
    return all(re.search(r'(?<!\w)'+re.escape(t)+r'(?!\w)',fold(name)) for t in fold(phrase).split())

def matches(o, item):
    queries = ALIASES.get(item['query'].lower(), [item['query']])
    exact = any(terms_match(o['name'], q) for q in queries)
    substitute = any(terms_match(o['name'], q) for q in item.get('alternatives',[]))
    if not exact and not substitute:
        return False
    if any(not terms_match(o['name'],t) for t in item.get('required_terms',[])):
        return False
    if any(terms_match(o['name'],t) for t in item.get('excluded_terms',[])):
        return False
    if item.get('offer_ids') and o['id'] not in item['offer_ids']:
        return False
    fat = item.get('fat_percent')
    if fat is not None:
        fats = [Decimal(n.replace(',','.')) for n in re.findall(r'(\d+(?:[.,]\d+)?)\s*%',o['name'])]
        evfat = o.get('evidence',{}).get('fat_percent')
        if evfat is not None:
            fats.append(Decimal(str(evfat)))
        if Decimal(str(fat)) not in fats:
            return False
    return True

def validate_evidence(o,e):
    """Called only by local CLI; review is not exposed as an MCP tool."""
    allowed = {'source_url','reviewed_at','note','price_grosz','basis','package_quantity','unit',
               'min_packs','buy','free','required_cards','required_coupons','max_packs','fat_percent',
               'conditions_complete','raw_snapshot','max_quantity','required_confirmations','pack_price_cycle_grosz'}
    if set(e)-allowed:
        raise ValueError('Unknown evidence fields')
    if not e.get('conditions_complete') or not e.get('note') or e.get('source_url') != o['source_url']:
        raise ValueError('Full conditions and a review note at the offer source are required')
    if e.get('basis') not in ('package','per_unit') or e.get('unit') not in UNITS:
        raise ValueError('Explicit price basis and kg/l/piece required')
    if not isinstance(e.get('price_grosz'),int) or isinstance(e['price_grosz'],bool) or e['price_grosz']<=0:
        raise ValueError('Verified positive integer price_grosz required')
    if e['basis']=='package':
        decimal(e.get('package_quantity'))
    elif any(e.get(k) for k in ('buy','free','min_packs','max_packs')):
        raise ValueError('Package promotions cannot apply to loose goods')
    for key in ('min_packs','buy','free','max_packs'):
        if key in e and (not isinstance(e[key],int) or isinstance(e[key],bool) or e[key]<1):
            raise ValueError(f'{key} must be a positive integer')
    cycle = e.get('pack_price_cycle_grosz')
    if cycle is not None:
        if e['basis']!='package' or not isinstance(cycle,list) or not 2<=len(cycle)<=10 or any(not isinstance(v,int) or isinstance(v,bool) or v<0 for v in cycle):
            raise ValueError('Package price cycle needs 2..10 non-negative integer grosz prices')
        if cycle[0]!=e['price_grosz'] or any(k in e for k in ('buy','free')):
            raise ValueError('Cycle must start at base price and cannot combine with buy/free')
    if ('buy' in e) != ('free' in e):
        raise ValueError('buy/free must be specified together')
    if e.get('max_packs',10**9)<e.get('min_packs',1):
        raise ValueError('Promotion limit below minimum')
    if e.get('max_quantity') is not None:
        decimal(e['max_quantity'])
    if e.get('unit')=='piece' and e.get('basis')=='package' and decimal(e.get('package_quantity'))%1:
        raise ValueError('Package pieces must be whole')
    for key in ('required_cards','required_coupons','required_confirmations'):
        if not isinstance(e.get(key,[]),list) or any(not isinstance(v,str) for v in e.get(key,[])):
            raise ValueError('Cards/coupons must be arrays of strings')
    if e.get('fat_percent') is not None:
        f = Decimal(str(e['fat_percent']))
        if not f.is_finite() or not 0<=f<=100:
            raise ValueError('Invalid fat percentage')
    if e.get('reviewed_at'):
        dt = datetime.fromisoformat(e['reviewed_at'])
        if dt.tzinfo is None or dt > datetime.now(timezone.utc):
            raise ValueError('Review time must be timezone-aware and not in the future')


def quote(o,item,cards=(),coupons=(),confirmations=()):
    e = o.get('evidence')
    if not e:
        return None,'Promotion conditions / price basis require image review'
    if e['unit'] != item['unit']:
        return None,'Incompatible unit'
    if not set(e.get('required_cards',[]))<=set(cards):
        return None,'Required loyalty card missing'
    if not set(e.get('required_coupons',[]))<=set(coupons):
        return None,'Required activated coupon missing'
    if not set(e.get('required_confirmations',[]))<=set(confirmations):
        return None,'Required eligibility confirmation missing (e.g. first receipt today)'
    qty = decimal(item['quantity'])
    price = Decimal(e['price_grosz'])/100
    if e['basis']=='per_unit':
        packs,obtained,cost = None,qty,(qty*price).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
        unitprice = price
    else:
        pack = decimal(e['package_quantity'])
        packs = max(int((qty/pack).to_integral_value(rounding=ROUND_CEILING)),e.get('min_packs',1))
        if e.get('max_packs') and packs>e['max_packs']:
            return None,'Required quantity exceeds verified promotion limit'
        paid = packs
        if e.get('buy'):
            # Same SKU only; pay buy packs and receive free identical packs per complete group.
            buy,free = e['buy'],e['free']
            paid -= (packs//(buy+free))*free
        obtained,cost = packs*pack,price*paid
        if e.get('pack_price_cycle_grosz'):
            cycle = e['pack_price_cycle_grosz']
            groups,remainder = divmod(packs,len(cycle))
            cost = Decimal(groups*sum(cycle)+sum(cycle[:remainder]))/100
        unitprice = cost/obtained
    if e.get('max_quantity') is not None and obtained>decimal(e['max_quantity']):
        return None,'Purchase quantity exceeds verified promotion limit'
    return {'offer_id':o['id'],'store':o['store'],'name':o['name'],'requested':str(qty),'unit':item['unit'],
            'packs':packs,'obtained':str(obtained),'extra':str(obtained-qty),'total_pln':money(cost),
            'effective_unit_price_pln':money(unitprice),'source_url':o['source_url'], 'conditions':{k:v for k,v in e.items() if k!='raw_snapshot'},
            'match_kind':'exact' if any(terms_match(o['name'],q) for q in ALIASES.get(item.get('query',o['name']).lower(),[item.get('query',o['name'])])) else 'allowed_substitute'},None

class Shopping:
    def __init__(self, config, db):
        self.config,self.db = config,db

    def freshness(self):
        cov = self.db.coverage()
        rows = self.db.offers()
        now = datetime.now(timezone.utc)
        stores = {}
        for s in self.config.stores:
            c = cov.get(s,{})
            dates = [o['fetched_at'] for o in rows if o['store']==s]
            ages = [(now-datetime.fromisoformat(t)).total_seconds()/3600 for t in dates]
            stores[s] = {**c, 'cached_offers':len(ages), 'oldest_offer_age_hours':round(max(ages),2) if ages else None,
                         'newest_offer_age_hours':round(min(ages),2) if ages else None,
                         'stale':not ages or max(ages)>self.config.stale_hours,
                         'catalog_complete':False,'status':c.get('status','never_collected')}
        return {'today':today(),'timezone':'Europe/Warsaw','stores':stores,'stale_after_hours':self.config.stale_hours,
                'scope':'Promotions observed on public HTML; not full assortment or local stock.'}

    def search(self,query,stores=None,day=None,limit=50,offset=0):
        day = self._day(day)
        stores = self._stores(stores)
        if not query.strip() or len(query)>200 or not 1<=limit<=100 or not 0<=offset<=100000:
            raise ValueError('Non-empty query <=200 characters, limit 1..100 and valid offset required')
        rows = [o for o in self.db.offers() if o['store'] in stores and active(o,day) and matches(o,{'query':query})]
        rows.sort(key=lambda o:(o['price_grosz'] is None,o['price_grosz'] or 0,o['id']))
        return {'offers':[self._public(o) for o in rows[offset:offset+limit]],'total_matches':len(rows),
                'next_offset':offset+limit if len(rows)>offset+limit else None,'coverage':self.freshness(),
                'warning':'Displayed price is not a confirmed payable package price; inspect evidence.'}

    @staticmethod
    def _public(o):
        return {k:v for k,v in o.items() if k!='raw'}

    def details(self,offer_id):
        for o in self.db.offers():
            if o['id']==offer_id and o['store'] in self.config.stores:
                return {**o,'active_today':active(o,today()),'coverage':self.freshness()}
        raise ValueError('Unknown offer id')

    def _day(self,day):
        day = day or today()
        return datetime.strptime(day,'%Y-%m-%d').date().isoformat()

    def _stores(self,stores):
        stores = list(self.config.stores if stores is None else stores)
        if not stores or len(set(stores))!=len(stores) or not set(stores)<=set(self.config.stores):
            raise ValueError('Select unique configured stores')
        return stores

    def compare(self,items,max_stores=2,stores=None,cards=None,coupons=None,day=None,confirmations=None):
        stores,day = self._stores(stores),self._day(day)
        if not 1<=max_stores<=min(4,len(stores)) or not 1<=len(items)<=20:
            raise ValueError('Use 1..4 stores (within selected stores) and 1..20 basket items')
        keys = {'query','quantity','unit','required_terms','excluded_terms','alternatives','fat_percent','offer_ids'}
        for i in items:
            if set(i)-keys or not isinstance(i.get('query'),str) or not i['query'].strip() or len(i['query'])>200 or i.get('unit') not in UNITS:
                raise ValueError('Basket item needs query, positive quantity and unit kg/l/piece')
            decimal(i.get('quantity'))
            if i['unit']=='piece' and decimal(i['quantity'])%1:
                raise ValueError('Requested pieces must be whole')
            for field in ('required_terms','excluded_terms','alternatives','offer_ids'):
                if not isinstance(i.get(field,[]),list) or any(not isinstance(t,str) or not t.strip() for t in i.get(field,[])):
                    raise ValueError(f'{field} must be an array of non-empty strings')
            if i.get('fat_percent') is not None:
                f=Decimal(str(i['fat_percent']))
                if not f.is_finite() or not 0<=f<=100: raise ValueError('Invalid requested fat percentage')
        cov = self.freshness()
        now = datetime.now(timezone.utc)
        offers = [o for o in self.db.offers() if o['store'] in stores and active(o,day)]
        per_item,unconfirmed,used_ids = [],[],set()
        for index,i in enumerate(items):
            opts = []
            ids = {o['id'] for o in offers if matches(o,i)}
            if used_ids & ids:
                raise ValueError('Overlapping basket lines: merge quantities or constrain products/offer_ids to avoid double-counting promotion limits')
            used_ids |= ids
            for o in offers:
                if o['id'] not in ids: continue
                q,reason = quote(o,i,cards or [],coupons or [],confirmations or [])
                age = (now-datetime.fromisoformat(o['fetched_at'])).total_seconds()/3600
                if age>self.config.stale_hours:
                    q,reason = None,'Cached offer is stale; refresh before comparing'
                if q:
                    q['item_index'] = index
                    opts.append(q)
                else:
                    unconfirmed.append({'item_index':index,'offer':self._public(o),'reason':reason})
            per_item.append(opts)
        def plan(subset):
            lines,missing = [],[]
            for idx,opts in enumerate(per_item):
                candidates = [q for q in opts if q['store'] in subset]
                if candidates:
                    lines.append(min(candidates,key=lambda q:(Decimal(q['total_pln']),q['offer_id'])))
                else:
                    missing.append({'item_index':idx,**items[idx]})
            return {'stores':sorted({q['store'] for q in lines}), 'selected_stores':list(subset),'lines':lines,
                    'missing':missing,'complete':not missing,'total_pln':money(sum((Decimal(q['total_pln']) for q in lines),Decimal(0))),
                    'total_kind':'full_basket' if not missing else 'partial_subtotal'}
        singles = [plan([s]) for s in stores]
        plans = [plan(subset) for size in range(1,max_stores+1) for subset in combinations(stores,size)]
        complete = [p for p in plans if p['complete']]
        best = min(complete,key=lambda p:(Decimal(p['total_pln']),len(p['stores']),p['stores'])) if complete else None
        partial = sorted((p for p in plans if not p['complete']),key=lambda p:(len(p['missing']),Decimal(p['total_pln']),len(p['selected_stores'])))[:5]
        return {'wording':'Самый дешёвый вариант среди проверенных предложений', 'day':day,
                'best_complete':best,'one_store':singles,'partial_options':partial,'unconfirmed':unconfirmed,
                'coverage':cov,'limitations':['No guarantee of national minimum price or store stock.',
                'One SKU per basket line; splitting a line across SKUs/stores is not optimized.',
                'Only locally reviewed evidence is eligible; unknown dates and stale offers are excluded.']}
