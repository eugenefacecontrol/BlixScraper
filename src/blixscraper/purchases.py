"""Text receipts parsed by the client; exact money and atomic, deduplicated storage."""
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pydantic import BaseModel, ConfigDict, Field
from .model import fold


class PurchaseItem(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=200)
    paid_pln: str = Field(description='Final total paid for this entire line after discounts, e.g. 19.98; not unit price')
    receipt_name: str | None = Field(default=None, max_length=200, description='Original abbreviated receipt label')
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

    def add(self,store,purchased_on,items,total_paid_pln=None,receipt_reference=None):
        store=store.strip()
        if not store or len(store)>100 or not 1<=len(items)<=200:
            raise ValueError('Store and 1..200 receipt lines required')
        purchased_on=day(purchased_on)
        if receipt_reference is not None and (not receipt_reference.strip() or len(receipt_reference)>200):
            raise ValueError('Receipt reference must be nonempty and <=200 characters')
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
            lines.append({'name':name,'receipt_name':item.receipt_name,'paid_grosz':int(paid*100),
                          'paid_pln':format(paid,'.2f'),'quantity':str(quantity) if quantity is not None else None,
                          'unit':item.unit,'price_per_unit_pln':format(paid/quantity,'.4f') if quantity is not None else None})
        total=sum(x['paid_grosz'] for x in lines)
        if total_paid_pln is not None and int(number(total_paid_pln,True)*100)!=total:
            raise ValueError('Receipt total differs from sum of net line payments; resolve discounts/missing lines before saving, or omit total for an explicitly partial receipt')
        reference=receipt_reference.strip() if receipt_reference else None
        # Receipt reference wins over wording variations in client parsing.
        identity=[fold(store),purchased_on,reference] if reference else [fold(store),purchased_on,sorted((fold(x['receipt_name'] or x['name']),x['paid_grosz'],x['quantity'] or '',x['unit'] or '') for x in lines)]
        id_=sha256(json.dumps(identity,ensure_ascii=False).encode()).hexdigest()[:24]
        payload={'receipt_id':id_,'store':store,'purchased_on':purchased_on,'items':lines,
                 'total_paid_pln':format(Decimal(total)/100,'.2f'),'receipt_reference':reference,
                 'total_checked':total_paid_pln is not None,'saved_at':datetime.now(timezone.utc).isoformat(),
                 'source':'user_receipt_text','currency':'PLN'}
        with self.db.connect() as db:
            inserted=db.execute('INSERT OR IGNORE INTO receipts VALUES(?,?,?,?)',(id_,purchased_on,store,json.dumps(payload,ensure_ascii=False))).rowcount
            saved=json.loads(db.execute('SELECT payload FROM receipts WHERE id=?',(id_,)).fetchone()['payload'])
        return {'status':'saved' if inserted else 'already_saved','receipt':saved,
                'warning':'Purchase history, not remaining stock or current shop prices. Without a receipt reference, identical purchases at the same store on the same date are treated as duplicates.'}

    def search(self,query='',since=None,until=None,store=None,limit=50,offset=0):
        if len(query)>200 or not 1<=limit<=100 or not 0<=offset<=100000:
            raise ValueError('Query <=200 characters, limit 1..100, valid offset required')
        since=day(since) if since else '0001-01-01'
        until=day(until) if until else '9999-12-31'
        if since>until:
            raise ValueError('Invalid date range')
        with self.db.connect() as db:
            receipts=[json.loads(r['payload']) for r in db.execute('SELECT payload FROM receipts WHERE purchased_on BETWEEN ? AND ? ORDER BY purchased_on DESC,id',(since,until))]
        rows=[]
        for receipt in receipts:
            if store is not None and fold(store.strip())!=fold(receipt['store']):
                continue
            for index,item in enumerate(receipt['items']):
                text=fold(item['name']+' '+(item['receipt_name'] or ''))
                if all(term in text for term in fold(query).split()):
                    rows.append({**item,'receipt_id':receipt['receipt_id'],'line_index':index,'store':receipt['store'],'purchased_on':receipt['purchased_on'],'currency':'PLN'})
        return {'purchases':rows[offset:offset+limit],'total_matches':len(rows),
                'next_offset':offset+limit if len(rows)>offset+limit else None,
                'warning':'Historical net payments from user text, not live prices or remaining inventory. Unit prices comparable only for matching products, quantities and units.'}

    def delete(self,receipt_id):
        with self.db.connect() as db:
            deleted=db.execute('DELETE FROM receipts WHERE id=?',(receipt_id,)).rowcount
        return {'receipt_id':receipt_id,'status':'deleted' if deleted else 'not_found'}
