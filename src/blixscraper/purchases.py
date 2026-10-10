"""Text receipts parsed by the client; exact money and atomic, deduplicated storage."""
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from zoneinfo import ZoneInfo
import json
from pydantic import BaseModel, ConfigDict, Field
from .model import fold


class PurchaseItem(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=200)
    paid_pln: str = Field(description='Final total paid for this entire line after discounts, e.g. 19.98; not unit price')
    receipt_name: str | None = Field(default=None, max_length=200, description='Original abbreviated receipt label')
    product_id: str | None = Field(default=None, description='Existing catalogue ID only for explicitly identified same product')
    category: str | None = Field(default=None, max_length=100)
    original_paid_pln: str | None = Field(default=None, description='Line total BEFORE discounts, not unit price')
    discount_pln: str | None = Field(default=None, description='Discount on this line; nonnegative')
    package_quantity: str | None = Field(default=None, description='Contents of ONE pack if explicit, e.g. 0.38 kg')
    package_unit: str | None = Field(default=None, description='kg, l or piece, paired with package_quantity')
    quantity: str | None = Field(default=None, description='Only explicit quantity on receipt; do not guess package contents')
    unit: str | None = Field(default=None, description='piece, pack, kg or l; null if unknown')


def number(value, money=False):
    if not isinstance(value,str) or len(value)>40:
        raise ValueError('Amounts must be decimal strings')
    try:
        n=Decimal(value.replace(',','.'))
    except InvalidOperation as e:
        raise ValueError('Invalid decimal amount') from e
    if not n.is_finite() or n<0 or n>Decimal('1000000') or (money and n!=n.quantize(Decimal('.01'))):
        raise ValueError('Finite nonnegative amount <=1000000; money has at most 2 decimal places')
    if not money and (n<=0 or n.as_tuple().exponent < -6):
        raise ValueError('Quantity must be positive with at most 6 decimal places')
    return n


def day(value):
    if not isinstance(value,str) or len(value)!=10:
        raise ValueError('Date must be YYYY-MM-DD')
    return date.fromisoformat(value).isoformat()


class Purchases:
    def __init__(self,db):
        self.db=db
        # Backfill legacy JSON receipts once, atomically; original payloads remain intact.
        with db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            for row in conn.execute('SELECT id,payload FROM receipts WHERE NOT EXISTS (SELECT 1 FROM receipt_items WHERE receipt_id=receipts.id)').fetchall():
                receipt=json.loads(row['payload'])
                for index,line in enumerate(receipt['items']):
                    self._save_line(conn,row['id'],index,line)

    @staticmethod
    def _save_line(conn,receipt_id,index,line):
        product_id=line.get('product_id')
        if product_id:
            row=conn.execute('SELECT payload FROM products WHERE id=?',(product_id,)).fetchone()
            if not row:
                raise ValueError('Unknown product_id; use search_products first')
            product=json.loads(row['payload'])
            if any(line.get(k)!=product.get(pk) for k,pk in [('unit','purchase_unit'),('package_quantity','package_quantity'),('package_unit','package_unit')]):
                raise ValueError('Product units/package contents differ; use separate variant')
        else:
            identity=[fold(line['name']),line.get('unit'),line.get('package_quantity'),line.get('package_unit')]
            product_id=sha256(json.dumps(identity,ensure_ascii=False).encode()).hexdigest()[:24]
            product={'product_id':product_id,'name':line['name'],'category':line.get('category'),
                     'purchase_unit':line.get('unit'),'package_quantity':line.get('package_quantity'),
                     'package_unit':line.get('package_unit')}
            conn.execute('INSERT OR IGNORE INTO products VALUES(?,?)',(product_id,json.dumps(product,ensure_ascii=False)))
        line['product_id']=product_id
        conn.execute('INSERT INTO receipt_items VALUES(?,?,?,?)',(receipt_id,index,product_id,json.dumps(line,ensure_ascii=False)))

    def add(self,store,purchased_on,items,total_paid_pln=None,receipt_reference=None,
            purchased_at=None,store_address=None,source_text=None,discount_total_pln=None):
        store=store.strip()
        if not store or len(store)>100 or not 1<=len(items)<=200:
            raise ValueError('Store and 1..200 receipt lines required')
        purchased_on=day(purchased_on)
        if receipt_reference is not None and (not receipt_reference.strip() or len(receipt_reference)>200):
            raise ValueError('Receipt reference must be nonempty and <=200 characters')
        if purchased_at is not None:
            dt=datetime.fromisoformat(purchased_at)
            if dt.tzinfo is None or dt.astimezone(ZoneInfo('Europe/Warsaw')).date().isoformat()!=purchased_on:
                raise ValueError('purchased_at needs timezone and must match purchased_on in Warsaw')
        if store_address is not None and len(store_address)>500:
            raise ValueError('Address <=500 characters')
        if source_text is not None and len(source_text)>50000:
            raise ValueError('Source text <=50000 characters')
        lines=[]
        for item in items:
            item=PurchaseItem.model_validate(item) if isinstance(item,dict) else item
            name=item.name.strip()
            if not name:
                raise ValueError('Product name cannot be blank')
            paid=number(item.paid_pln,True)
            if (item.quantity is None)!=(item.unit is None):
                raise ValueError('Quantity and unit must both be provided or both unknown')
            quantity=number(item.quantity) if item.quantity is not None else None
            if item.unit not in (None,'piece','pack','kg','l'):
                raise ValueError('Unit must be piece, pack, kg or l')
            original=number(item.original_paid_pln,True) if item.original_paid_pln is not None else None
            discount=number(item.discount_pln,True) if item.discount_pln is not None else None
            if original is not None and discount is not None and original-discount!=paid:
                raise ValueError('Original line payment minus discount must equal paid payment')
            if original is not None:
                discount=original-paid if discount is None else discount
                if discount<0: raise ValueError('Original payment cannot be below net payment')
            elif discount is not None:
                original=paid+discount
            if (item.package_quantity is None)!=(item.package_unit is None):
                raise ValueError('Package quantity and unit must both be provided')
            pack=number(item.package_quantity) if item.package_quantity is not None else None
            if item.package_unit not in (None,'kg','l','piece') or (pack is not None and item.unit!='pack'):
                raise ValueError('Package contents require pack purchase unit and kg/l/piece contents')
            if item.unit in ('piece','pack') and quantity!=quantity.to_integral_value():
                raise ValueError('Piece/pack quantity must be whole')
            if item.package_unit=='piece' and pack!=pack.to_integral_value():
                raise ValueError('Pieces per pack must be whole')
            base_qty=quantity*pack if pack is not None else quantity
            base_unit=item.package_unit if pack is not None else item.unit
            lines.append({'product_id':item.product_id,'category':item.category,
                          'original_paid_pln':format(original,'.2f') if original is not None else None,
                          'discount_pln':format(discount,'.2f') if discount is not None else None,
                          'package_quantity':format(pack.normalize(),'f') if pack is not None else None,'package_unit':item.package_unit,
                          'comparison_quantity':str(base_qty) if base_qty is not None else None,'comparison_unit':base_unit,
                          'comparison_price_pln':format(paid/base_qty,'.6f') if base_qty is not None else None,
                          'name':name,'receipt_name':item.receipt_name,'paid_grosz':int(paid*100),
                          'paid_pln':format(paid,'.2f'),'quantity':str(quantity) if quantity is not None else None,
                          'unit':item.unit,'price_per_unit_pln':format(paid/quantity,'.4f') if quantity is not None else None})
        total=sum(x['paid_grosz'] for x in lines)
        if total_paid_pln is not None and int(number(total_paid_pln,True)*100)!=total:
            raise ValueError('Receipt total differs from sum of net line payments; resolve discounts/missing lines before saving, or omit total for an explicitly partial receipt')
        discount_total=number(discount_total_pln,True) if discount_total_pln is not None else None
        discounts_known=all(x['discount_pln'] is not None for x in lines)
        if discounts_known:
            derived=sum((Decimal(x['discount_pln']) for x in lines),Decimal(0))
            if discount_total is not None and derived!=discount_total:
                raise ValueError('Receipt discount total differs from line discounts')
            discount_total=derived if discount_total is None else discount_total
        reference=receipt_reference.strip() if receipt_reference else None
        # Receipt reference wins over wording variations in client parsing.
        identity=[fold(store),purchased_on,reference] if reference else [fold(store),purchased_on,sorted((fold(x['receipt_name'] or x['name']),x['paid_grosz'],x['quantity'] or '',x['unit'] or '') for x in lines)]
        if reference is None and purchased_at is not None:
            identity.append(purchased_at)
        id_=sha256(json.dumps(identity,ensure_ascii=False).encode()).hexdigest()[:24]
        payload={'purchased_at':purchased_at,'store_address':store_address,'source_text':source_text,
                 'discount_total_pln':format(discount_total,'.2f') if discount_total is not None else None,
                 'discount_total_checked':discounts_known,'receipt_id' :id_,'store':store,'purchased_on':purchased_on,'items':lines,
                 'total_paid_pln':format(Decimal(total)/100,'.2f'),'receipt_reference':reference,
                 'total_checked':total_paid_pln is not None,'saved_at':datetime.now(timezone.utc).isoformat(),
                 'source':'user_receipt_text','currency':'PLN'}
        with self.db.connect() as db:
            inserted=db.execute('INSERT OR IGNORE INTO receipts VALUES(?,?,?,?)',(id_,purchased_on,store,json.dumps(payload,ensure_ascii=False))).rowcount
            if inserted:
                for index,line in enumerate(lines):
                    self._save_line(db,id_,index,line)
                db.execute('UPDATE receipts SET payload=? WHERE id=?',(json.dumps(payload,ensure_ascii=False),id_))
            saved=json.loads(db.execute('SELECT payload FROM receipts WHERE id=?',(id_,)).fetchone()['payload'])
        return {'status':'saved' if inserted else 'already_saved','receipt':saved,
                'warning':'Purchase history, not remaining stock or current shop prices. Without a receipt reference, identical purchases at the same store on the same date are treated as duplicates.'}

    def search(self,query='',since=None,until=None,store=None,limit=50,offset=0,product_id=None):
        if len(query)>200 or not 1<=limit<=100 or not 0<=offset<=100000:
            raise ValueError('Query <=200 characters, limit 1..100, valid offset required')
        since=day(since) if since else '0001-01-01'
        until=day(until) if until else '9999-12-31'
        if since>until:
            raise ValueError('Invalid date range')
        with self.db.connect() as db:
            receipts=[json.loads(r['payload']) for r in db.execute('SELECT payload FROM receipts WHERE purchased_on BETWEEN ? AND ? ORDER BY purchased_on DESC,id',(since,until))]
            indexed={(r['receipt_id'],r['line_index']):json.loads(r['payload']) for r in db.execute('SELECT * FROM receipt_items')}
            products={r['id']:json.loads(r['payload']) for r in db.execute('SELECT * FROM products')}
        receipts.sort(key=lambda r:(r['purchased_on'],datetime.fromisoformat(r['purchased_at']).timestamp() if r.get('purchased_at') else float('-inf'),r['receipt_id']),reverse=True)
        rows=[]
        for receipt in receipts:
            if store is not None and fold(store.strip())!=fold(receipt['store']):
                continue
            for index,item in enumerate(receipt['items']):
                item=indexed[(receipt['receipt_id'],index)]
                if product_id is not None and item['product_id']!=product_id:
                    continue
                product=products[item['product_id']]
                text=fold(product['name']+' '+item['name']+' '+(item['receipt_name'] or ''))
                if all(term in text for term in fold(query).split()):
                    rows.append({**item,'receipt_id':receipt['receipt_id'],'line_index':index,'store':receipt['store'],'purchased_on':receipt['purchased_on'],'purchased_at':receipt.get('purchased_at'),'store_address':receipt.get('store_address'),'product':product,'currency':'PLN'})
        return {'purchases':rows[offset:offset+limit],'total_matches':len(rows),
                'next_offset':offset+limit if len(rows)>offset+limit else None,
                'warning':'Historical net payments from user text, not live prices or remaining inventory. Unit prices comparable only for matching products, quantities and units.'}

    def delete(self,receipt_id):
        with self.db.connect() as db:
            deleted=db.execute('DELETE FROM receipts WHERE id=?',(receipt_id,)).rowcount
        return {'receipt_id':receipt_id,'status':'deleted' if deleted else 'not_found'}

    def receipt(self,receipt_id):
        with self.db.connect() as db:
            row=db.execute('SELECT payload FROM receipts WHERE id=?',(receipt_id,)).fetchone()
            if not row: raise ValueError('Unknown receipt_id')
            receipt=json.loads(row['payload'])
            receipt['items']=[json.loads(r['payload']) for r in db.execute('SELECT payload FROM receipt_items WHERE receipt_id=? ORDER BY line_index',(receipt_id,))]
            return receipt

    def products(self,query='',limit=50,offset=0):
        if len(query)>200 or not 1<=limit<=100 or not 0<=offset<=100000:
            raise ValueError('Invalid product query/pagination')
        with self.db.connect() as db:
            products=[json.loads(r['payload']) for r in db.execute('SELECT payload FROM products ORDER BY id')]
            aliases={}
            for row in db.execute('SELECT product_id,payload FROM receipt_items'):
                line=json.loads(row['payload'])
                aliases.setdefault(row['product_id'],set()).update([line['name'],line.get('receipt_name') or ''])
        rows=[]
        for p in products:
            p['aliases']=sorted(x for x in aliases.get(p['product_id'],set()) if x)
            if all(t in fold(p['name']+' '+' '.join(p['aliases'])) for t in fold(query).split()): rows.append(p)
        return {'products':rows[offset:offset+limit],'total_matches':len(rows),'next_offset':offset+limit if len(rows)>offset+limit else None}

    def link(self,receipt_id,line_index,product_id):
        with self.db.connect() as db:
            p=db.execute('SELECT payload FROM products WHERE id=?',(product_id,)).fetchone()
            r=db.execute('SELECT payload FROM receipt_items WHERE receipt_id=? AND line_index=?',(receipt_id,line_index)).fetchone()
            if not p or not r: raise ValueError('Unknown product or receipt line')
            line=json.loads(r['payload']); product=json.loads(p['payload'])
            if any(line.get(k)!=product.get(pk) for k,pk in [('unit','purchase_unit'),('package_quantity','package_quantity'),('package_unit','package_unit')]):
                raise ValueError('Product units/package contents differ; use a separate variant')
            line['product_id']=product_id
            db.execute('UPDATE receipt_items SET product_id=?,payload=? WHERE receipt_id=? AND line_index=?',(product_id,json.dumps(line,ensure_ascii=False),receipt_id,line_index))
        return {'status':'linked','receipt_id':receipt_id,'line_index':line_index,'product_id':product_id}

    def price_history(self,product_id,since=None,until=None,store=None,candidate_unit_price_pln=None,unit=None):
        rows=[]; offset=0
        while True:
            page=self.search(since=since,until=until,store=store,limit=100,offset=offset,product_id=product_id)
            rows+=page['purchases']
            if page['next_offset'] is None: break
            offset=page['next_offset']
        groups={}
        for row in rows:
            qty=row.get('comparison_quantity') or row.get('quantity')
            u=row.get('comparison_unit') or row.get('unit')
            if not qty: continue
            price=Decimal(row['paid_pln'])/Decimal(qty)
            key=(fold(row['store']),u)
            group=groups.setdefault(key,{'store':row['store'],'unit':u,'count':0,'minimum':None,'latest':None})
            record={'price_pln':format(price,'.6f'),'purchased_on':row['purchased_on'],'receipt_id':row['receipt_id']}
            group['count']+=1
            if group['minimum'] is None or price<group['_exact_minimum']:
                group['minimum']=record
                group['_exact_minimum']=price
            if group['latest'] is None:group['latest']=record
        for g in groups.values():g.pop('_exact_minimum')
        result={'product_id':product_id,'groups':list(groups.values()),'purchases_count':len(rows),
                'warning':'Historical net prices; same catalogue product only. Explicit links represent user-approved equivalence. Comparison price is user-supplied, not verified current Blix eligibility.'}
        if candidate_unit_price_pln is not None:
            candidate=number(candidate_unit_price_pln,True)
            if unit not in ('kg','l','piece','pack'):raise ValueError('Comparison unit required')
            result['comparison']=[{'store':g['store'],'unit':unit,'historical_minimum_pln':g['minimum']['price_pln'],
                                   'candidate_unit_price_pln':str(candidate),'difference_pln':format(candidate-Decimal(g['minimum']['price_pln']),'.6f')}
                                  for g in groups.values() if g['unit']==unit]
        return result
