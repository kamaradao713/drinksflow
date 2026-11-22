import hashlib
import json
import re
import secrets
import sqlite3
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent
METHODS = ('Cash', 'Mobile money', 'Bank transfer')
ROLES = {
    'Owner': {'sales', 'stock', 'purchases', 'expenses', 'reports', 'accounts', 'admin'},
    'Manager': {'sales', 'stock', 'purchases', 'expenses', 'reports', 'accounts'},
    'Cashier': {'sales'},
    'Inventory Officer': {'stock', 'purchases'},
    'Accountant': {'expenses', 'reports', 'accounts'},
}
PREFIX = {'sale':'INV', 'purchase':'PUR', 'order':'PO', 'expense':'EXP', 'expense_return':'EXR', 'opening':'OPN',
          'gain':'ADJ', 'loss':'ADJ', 'sale_return':'SR', 'purchase_return':'PR', 'fund_in':'IN', 'fund_out':'OUT'}
STOCK_IN = {'purchase', 'opening', 'gain'}
STOCK_OUT = {'sale', 'loss'}
PERMISSION = {'sale':'sales','sale_return':'sales','purchase':'purchases','order':'purchases','purchase_return':'purchases',
              'expense':'expenses','expense_return':'expenses','opening':'stock','gain':'stock','loss':'stock','fund_in':'accounts','fund_out':'accounts'}


class Problem(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status


def connect(path):
    db = sqlite3.connect(path, timeout=10, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA synchronous=FULL')
    return db


def initialize(path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    db = connect(path)
    try:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript((ROOT / 'schema.sql').read_text(encoding='utf-8'))
    finally:
        db.close()


def rows(db, sql, *args):
    return [dict(row) for row in db.execute(sql, args)]


def one(db, sql, *args):
    row = db.execute(sql, args).fetchone()
    return dict(row) if row else None


def add(db, table, **fields):
    names = ','.join(fields)
    placeholders = ','.join('?' for _ in fields)
    return db.execute(f'INSERT INTO {table} ({names}) VALUES ({placeholders})', tuple(fields.values())).lastrowid


def string(value, label, limit=120, required=True):
    if not isinstance(value, str):
        raise Problem(f'{label} must be text.')
    value = value.strip()
    if len(value) > limit or (required and not value):
        raise Problem(f'{label} must contain {"1 to " if required else "at most "}{limit} characters.')
    return value


def choice(value, options, label):
    if not isinstance(value, str) or value not in options:
        raise Problem(f'Choose a valid {label}.')
    return value


def integer(value, label, minimum=1, maximum=1_000_000_000):
    if type(value) is not int or not minimum <= value <= maximum:
        raise Problem(f'{label} must be a whole number from {minimum:,} to {maximum:,}.')
    return value


def money(value, label, scale=100, maximum=1_000_000_000):
    if isinstance(value, bool) or not isinstance(value, (str,int,float)):
        raise Problem(f'Enter a valid {label}.')
    raw = str(value)
    places = len(str(scale))-1
    if not re.fullmatch(rf'\d{{1,10}}(?:\.\d{{1,{places}}})?', raw):
        raise Problem(f'{label} accepts up to {places} decimal places.')
    amount = int(Decimal(raw) * scale)
    if amount > maximum:
        raise Problem(f'{label} exceeds the supported amount.')
    return amount


def day(value, label='Date', optional=False):
    if value == '' and optional:
        return ''
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value or not 1900 <= parsed.year <= 2199:
            raise ValueError
        return value
    except (TypeError, ValueError):
        raise Problem(f'Enter a valid {label.lower()}.')


def ratio(amount, numerator, denominator):
    return (amount * numerator * 2 + denominator) // (denominator * 2)


def audit(db, user, event, target):
    add(db, 'audit', user_id=user, event=event, target=str(target))


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 600_000)
    return salt + '$' + digest.hex()


def user_save(db, data, actor=None):
    username = string(data.get('username'), 'Username', 64)
    password = data.get('password', '')
    role = choice(data.get('role'), ROLES, 'role')
    uid = data.get('id')
    if not re.fullmatch(r'[A-Za-z0-9_.@+-]{3,64}', username):
        raise Problem('Use 3–64 letters, numbers, or username punctuation.')
    if uid is None or password:
        if not isinstance(password,str) or not 12 <= len(password) <= 128:
            raise Problem('Use a password with 12–128 characters.')
    if uid is None:
        uid = add(db, 'users', username=username, password=password_hash(password), role=role)
    else:
        uid = integer(uid, 'User ID')
        active = integer(data.get('active', 1), 'Active', 0, 1)
        old = one(db, 'SELECT * FROM users WHERE id=?', uid)
        if not old:
            raise Problem('User was not found.', 404)
        if uid == actor and (role != 'Owner' or not active):
            raise Problem('Keep your own owner account active.')
        new_hash = password_hash(password) if password else old['password']
        db.execute('UPDATE users SET username=?,password=?,role=?,active=? WHERE id=?',(username,new_hash,role,active,uid))
        if role != old['role'] or not active or password:
            db.execute('DELETE FROM sessions WHERE user_id=?', (uid,))
    audit(db, actor or uid, 'Saved user', uid)
    return uid


def products(db, today):
    data = rows(db, """SELECT p.*, COALESCE(SUM(l.remaining),0) AS quantity,
                        COALESCE(SUM(l.value),0) AS valuation,
                        COALESCE(SUM(CASE WHEN l.expiry='' OR l.expiry>=? THEN l.remaining ELSE 0 END),0) AS sellable
                        FROM products p LEFT JOIN lots l ON p.id=l.product GROUP BY p.id ORDER BY p.name,p.id""", today)
    for product in data:
        product['units'] = json.loads(product['units'])
    return data


def product_save(db, data, uid):
    units = data.get('units')
    if not isinstance(units,list) or not 1 <= len(units) <= 12 or not all(isinstance(x,dict) for x in units):
        raise Problem('Enter one base unit and up to eleven additional units.')
    units = [{'name':string(u.get('name'),'Unit name',35), 'factor':integer(u.get('factor'),'Units per package',1,10000),
              'retail':money(u.get('retail'),'Retail price'), 'wholesale':money(u.get('wholesale'),'Wholesale price')} for u in units]
    if units[0]['factor'] != 1 or len({u['name'].casefold() for u in units}) != len(units):
        raise Problem('The first unit must contain one item. Unit names must be unique.')
    fields = {k:string(data.get(k,''),k.title(),500 if k=='description' else 120,k in ('sku','name'))
              for k in ('sku','name','category','brand','size','description')}
    fields.update(reorder=integer(data.get('reorder',0),'Reorder point',0),units=json.dumps(units),
                  active=integer(data.get('active',1),'Active',0,1))
    pid = data.get('id')
    if pid is None:
        pid = add(db,'products',**fields)
    else:
        pid = integer(pid,'Product ID')
        existing = one(db,'SELECT units FROM products WHERE id=?',pid)
        if not existing:
            raise Problem('Product was not found.',404)
        if json.loads(existing['units'])[0]['name'].casefold()!=units[0]['name'].casefold() and one(db,'SELECT id FROM lines WHERE product=? LIMIT 1',pid):
            raise Problem('Keep the original base unit name after posting transactions.')
        db.execute('UPDATE products SET '+','.join(f'{k}=?' for k in fields)+' WHERE id=?',(*fields.values(),pid))
    audit(db,uid,'Saved product',pid)
    return {'id':pid}


def contact_save(db, data, uid, allow_supplier, allow_credit):
    kind = choice(data.get('kind'),('customer','supplier'),'contact type')
    if kind == 'supplier' and not allow_supplier:
        raise Problem('Supplier access is restricted.',403)
    fields = {k:string(data.get(k,''),k.title(),120,k=='name') for k in ('name','phone','email','category')}
    cid = data.get('id')
    if allow_credit:
        fields['credit'] = money(data.get('credit','0'),'Credit limit')
    elif cid is None:
        fields['credit'] = 0
    if cid is None:
        cid = add(db,'contacts',kind=kind,**fields)
    else:
        cid = integer(cid,'Contact ID')
        cur = db.execute('UPDATE contacts SET '+','.join(f'{k}=?' for k in fields)+' WHERE id=? AND kind=?',(*fields.values(),cid,kind))
        if not cur.rowcount:
            raise Problem('Contact was not found.',404)
    audit(db,uid,'Saved '+kind,cid)
    return {'id':cid}


def doc_number(kind, did):
    return f'{PREFIX[kind]}-{did:06d}'


def balance(db, did):
    return one(db, """SELECT d.total-COALESCE(SUM(p.amount),0) AS due
                      FROM docs d LEFT JOIN payments p ON d.id=p.doc WHERE d.id=? GROUP BY d.id""",did)['due']


def apply_payment(db, doc, amount, method, paid_day, uid):
    if amount <= 0:
        raise Problem('Payment must be greater than zero.')
    remaining = balance(db,doc['id'])
    if paid_day < doc['day'] or amount > remaining:
        raise Problem('Payment exceeds the outstanding amount or predates the document.')
    paid = doc['total'] - remaining
    base = ratio(paid+amount,doc['rate'],10000)-ratio(paid,doc['rate'],10000)
    add(db,'payments',doc=doc['id'],day=paid_day,method=choice(method,METHODS,'payment method'),
        amount=amount,base=base,user_id=uid)


def change_lot(db, lot, did, lid, quantity, amount):
    db.execute('UPDATE lots SET remaining=remaining+?,value=value+? WHERE id=?',(quantity,amount,lot))
    add(db,'movements',doc=did,line=lid,lot=lot,quantity=quantity,value=amount)


def take_stock(db, did, lid, product, quantity, business_day, kind, lot_id):
    lots = rows(db, """SELECT * FROM lots WHERE product=? AND remaining>0
                AND (? IS NULL OR id=?) AND (?='loss' OR expiry='' OR expiry>=?)
                ORDER BY expiry='',expiry,id""",product['id'],lot_id,lot_id,kind,business_day)
    if sum(l['remaining'] for l in lots) < quantity:
        raise Problem(f"Insufficient available stock for {product['name']}.",409)
    cost = 0
    for lot in lots:
        used = min(quantity,lot['remaining'])
        value = ratio(lot['value'],used,lot['remaining'])
        change_lot(db,lot['id'],did,lid,-used,-value)
        cost += value
        quantity -= used
        if not quantity:
            break
    return cost


def trade_lines(db, kind, raw, rate, channel, discount, tax, freight):
    if not isinstance(raw,list) or not 1 <= len(raw) <= 100 or not all(isinstance(x,dict) for x in raw):
        raise Problem('Enter 1 to 100 product lines.')
    output = []
    for entry in raw:
        pid = integer(entry.get('product'),'Product ID')
        product = one(db,'SELECT * FROM products WHERE id=?',pid)
        if not product or (not product['active'] and kind not in ('loss','gain')):
            raise Problem('Choose an active product.')
        unit_name = string(entry.get('unit'),'Unit',35)
        unit = next((u for u in json.loads(product['units']) if u['name']==unit_name),None)
        if not unit:
            raise Problem('Choose a unit defined on the product.')
        qty = integer(entry.get('qty'),'Quantity',1,100000)
        price = 0
        if kind == 'sale':
            price = ratio(unit[channel],10000,rate)
            if not price:
                raise Problem(f"Set a usable {channel} price for {product['name']} ({unit_name}) before selling.")
        elif kind != 'loss':
            price = money(entry.get('price'),'Unit cost')
        lot = entry.get('lot')
        output.append(dict(product=product,unit=unit,qty=qty,price=price,amount=price*qty,
                           lot=integer(lot,'Batch ID') if lot is not None else None,
                           batch=string(entry.get('batch',''),'Batch',60,False),
                           expiry=day(entry.get('expiry',''),'Expiry date',True)))
    subtotal = sum(line['amount'] for line in output)
    if discount > subtotal:
        raise Problem('Discount exceeds the subtotal.')
    if kind in ('purchase','order') and any(line['price']<=0 for line in output):
        raise Problem('Enter a positive cost for each purchase line.')
    cumulative = previous = 0
    for index,line in enumerate(output,1):
        cumulative += line['amount']
        extra = freight - discount
        adjusted = cumulative + (ratio(extra,cumulative,subtotal) if subtotal else ratio(extra,index,len(output)))
        line['base'] = ratio(adjusted,rate,10000)-ratio(previous,rate,10000)
        previous = adjusted
    total = subtotal - discount + freight + tax
    if total > 10_000_000_000 or ratio(total,rate,10000)>1_000_000_000_000:
        raise Problem('Document total is too large.')
    return output,total


def post(db, data, uid, allowed, today):
    kind = choice(data.get('kind'), set(PREFIX)-{'sale_return','purchase_return','expense_return'}, 'transaction type')
    require(allowed,PERMISSION[kind])
    business_day = day(data.get('day',today))
    if business_day>today or (kind in STOCK_IN|STOCK_OUT and business_day!=today):
        raise Problem('Post inventory changes on the current business date.')
    currency = choice(data.get('currency','LRD'),('LRD','USD'),'currency')
    rate = 10000 if currency=='LRD' else money(data.get('rate'),'Exchange rate',10000,100_000_000)
    if not rate:
        raise Problem('Enter the LRD value of one USD.')
    channel = choice(data.get('channel'),('retail','wholesale'),'sales channel') if kind=='sale' else ''
    reference = string(data.get('reference',''),'Reference',120,False)
    note = string(data.get('note',''),'Notes',500,kind in ('gain','loss','fund_in','fund_out'))
    due = day(data.get('due',''),'Due date',True)
    if due and due<business_day:
        raise Problem('Due date cannot precede the transaction.')
    party = data.get('party')
    contact = None
    if party is not None:
        contact = one(db,'SELECT * FROM contacts WHERE id=?',integer(party,'Contact ID'))
        expected = 'customer' if kind=='sale' else 'supplier' if kind in ('purchase','order') else None
        if not contact or contact['kind']!=expected:
            raise Problem('Choose a contact for this transaction.')
    if kind in ('purchase','order') and not contact:
        raise Problem('Choose a supplier.')
    linked = data.get('linked')
    if linked is not None:
        order = one(db,'SELECT * FROM docs WHERE id=?',integer(linked,'Purchase order'))
        if (kind!='purchase' or not order or order['kind']!='order' or order['party']!=party or
                one(db,'SELECT id FROM docs WHERE linked=? AND kind=?',linked,'purchase')):
            raise Problem('Choose an open purchase order for this supplier.',409)
    tax = money(data.get('tax','0'),'Tax') if kind=='sale' else 0
    entries = []
    discount = freight = 0
    if kind in STOCK_IN|STOCK_OUT|{'order'}:
        discount = money(data.get('discount','0'),'Discount') if kind=='sale' else 0
        freight = money(data.get('freight','0'),'Transport') if kind in ('purchase','order') else 0
        entries,total = trade_lines(db,kind,data.get('lines'),rate,channel,discount,tax,freight)
    else:
        total = money(data.get('amount'),'Amount')
        if not total or (kind=='expense' and not reference):
            raise Problem('Enter an amount and, for expenses, a category.')
    paid = (total if kind in ('expense','fund_in','fund_out') else
            money(data.get('paid','0'),'Payment') if kind in ('sale','purchase') else 0)
    if paid>total:
        raise Problem('Payment cannot exceed the total.')
    if kind in ('sale','purchase') and paid<total and (not contact or not due):
        raise Problem('Choose a contact and due date for an unpaid balance.')
    if kind=='sale' and paid<total:
        debt = one(db,"""SELECT COALESCE(SUM(d.base-COALESCE((SELECT SUM(base) FROM payments WHERE doc=d.id),0)),0) AS debt
            FROM docs d WHERE d.party=? AND d.kind='sale' AND NOT EXISTS(SELECT 1 FROM docs r WHERE r.linked=d.id)""",party)['debt']
        if contact['credit'] and debt+ratio(total,rate,10000)-ratio(paid,rate,10000)>contact['credit']:
            raise Problem('This sale exceeds the customer credit limit.',409)
    base = ratio(total,rate,10000)
    tax_base = base - ratio(total-tax,rate,10000)
    did = add(db,'docs',kind=kind,day=business_day,user_id=uid,party=party,reference=reference,channel=channel,
              currency=currency,rate=rate,total=total,base=base,tax=tax_base,tax_entered=tax,discount=discount,freight=freight,
              due=due,note=note,linked=linked)
    cost = 0
    for entry in entries:
        product,unit = entry['product'],entry['unit']
        quantity = entry['qty']*unit['factor']
        if kind=='opening' and one(db,'SELECT l.id FROM lots l JOIN lines x ON x.id=l.source WHERE l.product=? AND x.doc<>? LIMIT 1',product['id'],did):
            raise Problem('Use a positive adjustment after opening stock has been entered.')
        lid = add(db,'lines',doc=did,product=product['id'],label=product['name'],sku=product['sku'],
                  unit=unit['name'],factor=unit['factor'],qty=entry['qty'],price=entry['price'],
                  amount=entry['amount'],base=entry['base'],cost=0,batch=entry['batch'],expiry=entry['expiry'])
        if kind in STOCK_IN:
            lot = add(db,'lots',product=product['id'],source=lid,remaining=0,value=0,batch=entry['batch'],expiry=entry['expiry'])
            change_lot(db,lot,did,lid,quantity,entry['base'])
        if kind in STOCK_OUT:
            used = take_stock(db,did,lid,product,quantity,business_day,kind,entry['lot'])
            db.execute('UPDATE lines SET cost=? WHERE id=?',(used,lid))
            cost += used
    db.execute('UPDATE docs SET cost=? WHERE id=?',(cost,did))
    if paid:
        apply_payment(db,one(db,'SELECT * FROM docs WHERE id=?',did),paid,data.get('method'),business_day,uid)
    audit(db,uid,'Posted '+kind,doc_number(kind,did))
    return {'id':did,'number':doc_number(kind,did)}


def require(allowed, permission):
    if permission not in allowed:
        raise Problem('Your role cannot perform this action.',403)


def payment(db, did, data, uid, allowed, today):
    doc = one(db,'SELECT * FROM docs WHERE id=?',did)
    if not doc or doc['kind'] not in ('sale','purchase'):
        raise Problem('Invoice was not found.',404)
    if 'accounts' not in allowed:
        require(allowed,PERMISSION[doc['kind']])
    if one(db,'SELECT id FROM docs WHERE linked=? AND kind IN (?,?)',did,'sale_return','purchase_return'):
        raise Problem('This invoice was already reversed.',409)
    paid_day = day(data.get('day',today))
    if paid_day>today:
        raise Problem('A payment cannot be dated in the future.')
    apply_payment(db,doc,money(data.get('amount'),'Payment'),data.get('method'),paid_day,uid)
    audit(db,uid,'Recorded payment',doc_number(doc['kind'],did))
    return {'id':did}


def reverse(db, did, data, uid, allowed, today):
    original = one(db,'SELECT * FROM docs WHERE id=?',did)
    if not original or original['kind'] not in ('sale','purchase','expense'):
        raise Problem('Invoice was not found.',404)
    require(allowed,'accounts')
    kind = original['kind']+'_return'
    if one(db,'SELECT id FROM docs WHERE linked=? AND kind=?',did,kind):
        raise Problem('This invoice was already reversed.',409)
    note = string(data.get('note',''),'Return reason',500)
    refunded = original['total']-balance(db,did)
    method = choice(data.get('method'),METHODS,'refund method') if refunded else None
    rid = add(db,'docs',kind=kind,day=today,user_id=uid,party=original['party'],reference=doc_number(original['kind'],did),
              channel=original['channel'],currency=original['currency'],rate=original['rate'],total=original['total'],
              base=original['base'],tax=original['tax'],cost=original['cost'],tax_entered=original['tax_entered'],
              discount=original['discount'],freight=original['freight'],due='',note=note,linked=did)
    for entry in rows(db,'SELECT * FROM lines WHERE doc=? ORDER BY id',did):
        old_id = entry.pop('id')
        entry['doc'] = rid
        lid = add(db,'lines',**entry)
        for movement in rows(db,'SELECT * FROM movements WHERE doc=? AND line=?',did,old_id):
            lot = one(db,'SELECT * FROM lots WHERE id=?',movement['lot'])
            quantity,value = movement['quantity'],movement['value']
            if quantity>0 and (lot['remaining']<quantity or lot['value']<value):
                raise Problem('The entire original purchase batch must be on hand to reverse this purchase.',409)
            change_lot(db,lot['id'],rid,lid,-quantity,-value)
    if refunded:
        apply_payment(db,one(db,'SELECT * FROM docs WHERE id=?',rid),refunded,method,today,uid)
    audit(db,uid,'Posted '+kind,doc_number(kind,rid))
    return {'id':rid,'number':doc_number(kind,rid)}


def read_docs(db, condition, args):
    result = rows(db,"""SELECT d.*, c.name AS contact,u.username,
        COALESCE((SELECT SUM(p.amount) FROM payments p WHERE p.doc=d.id),0) AS paid,
        COALESCE((SELECT SUM(p.base) FROM payments p WHERE p.doc=d.id),0) AS paid_base,
        (SELECT r.id FROM docs r WHERE r.linked=d.id LIMIT 1) AS child
        FROM docs d JOIN users u ON u.id=d.user_id LEFT JOIN contacts c ON c.id=d.party """+condition,*args)
    for item in result:
        item['number'] = doc_number(item['kind'],item['id'])
        open_invoice = item['kind'] in ('sale','purchase') and item['child'] is None
        item['outstanding'] = item['total']-item['paid'] if open_invoice else 0
    return result


def detail(db, did, allowed):
    docs = read_docs(db,'WHERE d.id=?',(did,))
    if not docs:
        raise Problem('Document was not found.',404)
    doc = docs[0]
    permission = PERMISSION[doc['kind']]
    if permission not in allowed and 'reports' not in allowed and not ('accounts' in allowed and permission in ('sales','purchases')):
        raise Problem('Your role cannot view this document.',403)
    doc['lines'] = rows(db,'SELECT * FROM lines WHERE doc=? ORDER BY id',did)
    doc['payments'] = rows(db,'SELECT p.*,u.username FROM payments p JOIN users u ON u.id=p.user_id WHERE p.doc=? ORDER BY p.id',did)
    if 'reports' not in allowed and 'stock' not in allowed and permission=='sales':
        doc.pop('cost',None)
        for line in doc['lines']:
            line.pop('cost',None)
    return doc


CASH_SIGN = """CASE WHEN d.kind IN ('sale','purchase_return','expense_return','fund_in') THEN 1 ELSE -1 END"""


def cash(db, through, before=False):
    comparator = '<' if before else '<='
    return rows(db,f"""SELECT p.method,d.currency,SUM(p.amount*({CASH_SIGN})) AS amount
        FROM payments p JOIN docs d ON d.id=p.doc WHERE p.day {comparator} ?
        GROUP BY p.method,d.currency ORDER BY p.method,d.currency""",through)


def count_save(db,data,uid,today):
    business_day = day(data.get('day',today))
    if business_day>today:
        raise Problem('A closing count cannot be dated in the future.')
    method = choice(data.get('method'),METHODS,'payment method')
    currency = choice(data.get('currency'),('LRD','USD'),'currency')
    actual = money(data.get('amount'),'Actual closing amount')
    note = string(data.get('note',''),'Notes',500,False)
    expected = next((x['amount'] for x in cash(db,business_day) if x['method']==method and x['currency']==currency),0)
    saved = one(db,'SELECT id FROM counts WHERE day=? AND method=? AND currency=?',business_day,method,currency)
    if saved:
        raise Problem('This account and currency already have a closing count for that date.',409)
    cid = add(db,'counts',day=business_day,method=method,currency=currency,actual=actual,expected=expected,note=note,user_id=uid)
    audit(db,uid,'Recorded closing count',cid)
    return {'id':cid}


def overview(db,today):
    current = date.fromisoformat(today)
    week = (current-timedelta(days=current.weekday())).isoformat()
    month = current.replace(day=1).isoformat()
    since = (current-timedelta(days=6)).isoformat()
    totals = rows(db,"""SELECT day,kind,channel,SUM(base) AS base,SUM(tax) AS tax,SUM(cost) AS cost,COUNT(*) AS count
        FROM docs WHERE day BETWEEN ? AND ? AND kind IN ('sale','sale_return','expense','expense_return','purchase','purchase_return','loss')
        GROUP BY day,kind,channel""",min(week,month,since),today)
    def sales_period(start):
        return sum(x['base']*(1 if x['kind']=='sale' else -1) for x in totals if x['kind'] in ('sale','sale_return') and x['day']>=start)
    inventory = products(db,today)
    lots = rows(db,"""SELECT p.name,p.sku,l.* FROM lots l JOIN products p ON p.id=l.product
        WHERE l.remaining>0 AND l.expiry<>'' AND l.expiry<=? ORDER BY l.expiry,l.id LIMIT 50""",(current+timedelta(days=30)).isoformat())
    m = [x for x in totals if x['day']>=month]
    gross = sum((x['base']-x['tax']-x['cost'])*(1 if x['kind']=='sale' else -1)
                for x in m if x['kind'] in ('sale','sale_return'))
    expenses = sum(x['base']*(1 if x['kind']=='expense' else -1) for x in m if x['kind'] in ('expense','expense_return'))
    loss = sum(x['cost'] for x in m if x['kind']=='loss')
    accounts = rows(db,"""SELECT d.kind,SUM(d.base-COALESCE((SELECT SUM(base) FROM payments WHERE doc=d.id),0)) AS amount
        FROM docs d WHERE d.kind IN ('sale','purchase') AND NOT EXISTS (SELECT 1 FROM docs r WHERE r.linked=d.id AND r.kind IN ('sale_return','purchase_return'))
        GROUP BY d.kind""")
    return {'today':sales_period(today),'week':sales_period(week),'month':sales_period(month),'gross':gross,'expenses':expenses,
            'net':gross-expenses-loss,'purchases':sum(x['base']*(1 if x['kind']=='purchase' else -1) for x in m if x['kind'] in ('purchase','purchase_return')),
            'stock_value':sum(x['valuation'] for x in inventory),'stocked':sum(x['quantity']>0 for x in inventory),
            'low':[x for x in inventory if x['active'] and x['sellable']<=x['reorder']],'expiry':lots,'accounts':accounts,
            'cash':cash(db,today),'trend':[{'day':(current-timedelta(days=i)).isoformat(),
                     'amount':sum(x['base']*(1 if x['kind']=='sale' else -1) for x in totals
                     if x['kind'] in ('sale','sale_return') and x['day']==(current-timedelta(days=i)).isoformat())} for i in range(6,-1,-1)],
            'channels':[{ 'channel':c,'amount':sum(x['base']*(1 if x['kind']=='sale' else -1) for x in m if x['channel']==c)}
                        for c in ('retail','wholesale')],
            'top':rows(db,"""SELECT p.name,p.sku,SUM(l.qty*l.factor*CASE WHEN d.kind='sale' THEN 1 ELSE -1 END) AS quantity
                       FROM lines l JOIN docs d ON d.id=l.doc JOIN products p ON p.id=l.product WHERE d.kind IN ('sale','sale_return')
                       AND d.day BETWEEN ? AND ? GROUP BY p.id ORDER BY quantity DESC,p.name LIMIT 5""",month,today),
            'recent':read_docs(db,"WHERE d.kind IN ('sale','sale_return','purchase','expense') ORDER BY d.id DESC LIMIT 8",())}


def report(db, params, today):
    start = day(params.get('from',today[:8]+'01'))
    end = day(params.get('to',today))
    if start>end or end>today:
        raise Problem('Choose a date range ending on or before today.')
    mode = choice(params.get('view','summary'),
                  ('summary','sales','products','purchases','expenses','stock','balances','payments','closing'),'report')
    where = ['d.day BETWEEN ? AND ?']
    args = [start,end]
    for key, column in [('party','d.party'),('user','d.user_id'),('product','l.product')]:
        if params.get(key):
            value = params[key]
            if not value.isdigit() or len(value)>12:
                raise Problem('Choose a valid report filter.')
            if key=='product':
                where.append('EXISTS(SELECT 1 FROM lines l WHERE l.doc=d.id AND l.product=?)')
            else:
                where.append(column+'=?')
            args.append(int(value))
    match = ' AND '.join(where)
    if mode=='stock':
        product_clause, product_args = ('WHERE p.id=?',[int(params['product'])]) if params.get('product') else ('',[])
        stock = rows(db,f"""SELECT p.sku,p.name,
            COALESCE(SUM(CASE WHEN d.day<? THEN m.quantity END),0) AS opening,
            COALESCE(SUM(CASE WHEN d.day>=? AND m.quantity>0 THEN m.quantity END),0) AS incoming,
            COALESCE(SUM(CASE WHEN d.day>=? AND m.quantity<0 THEN -m.quantity END),0) AS outgoing,
            COALESCE(SUM(m.quantity),0) AS closing,COALESCE(SUM(m.value),0) AS valuation
            FROM products p LEFT JOIN (movements m JOIN docs d ON d.id=m.doc AND d.day<=?) ON p.id=(SELECT product FROM lots WHERE id=m.lot)
            {product_clause} GROUP BY p.id ORDER BY p.name""",start,start,start,end,*product_args)
        movements = rows(db,f"""SELECT d.id,d.day,d.kind,p.sku,p.name,l.batch,m.quantity,m.value
            FROM movements m JOIN docs d ON d.id=m.doc JOIN lots l ON l.id=m.lot JOIN products p ON p.id=l.product
            WHERE d.day BETWEEN ? AND ? {'AND p.id=?' if product_args else ''} ORDER BY d.id DESC,m.id DESC""",start,end,*product_args)
        for entry in movements:
            entry['number'] = doc_number(entry['kind'],entry['id'])
            del entry['id']
        return {'sections':[{'title':'Opening and closing stock','rows':stock},{'title':'Stock movements','rows':movements}]}
    if mode=='balances':
        balance_match = ['d.kind IN (\'sale\',\'purchase\')','d.day<=?']
        balance_args = [end]
        if params.get('party'):
            balance_match.append('d.party=?')
            balance_args.append(int(params['party']))
        amounts = rows(db,"""WITH paid AS (
            SELECT doc,SUM(amount) AS amount,SUM(base) AS base FROM payments WHERE day<=? GROUP BY doc
            ) SELECT d.id,d.kind, c.name AS contact,d.day,d.due,d.currency,
            d.total-COALESCE(p.amount,0) AS outstanding,d.base-COALESCE(p.base,0) AS outstanding_lrd
            FROM docs d JOIN contacts c ON c.id=d.party LEFT JOIN paid p ON p.doc=d.id WHERE """
            +' AND '.join(balance_match)+""" AND d.total>COALESCE(p.amount,0)
            AND NOT EXISTS(SELECT 1 FROM docs r WHERE r.linked=d.id AND r.day<=? AND r.kind IN ('sale_return','purchase_return'))
            ORDER BY c.name,d.day,d.id""",end,*balance_args,end)
        for item in amounts:
            item['number'] = doc_number(item['kind'],item.pop('id'))
            item['status'] = 'Overdue' if item['due'] and item['due']<end else 'Open'
        return {'sections':[{'title':'Outstanding balances as of '+end,'rows':amounts}]}
    if mode=='closing':
        counts = rows(db,"""SELECT c.day,c.method,c.currency,c.actual,c.expected,c.actual-c.expected AS difference,
                         c.note,u.username FROM counts c JOIN users u ON u.id=c.user_id
                         WHERE c.day BETWEEN ? AND ? ORDER BY c.day DESC,c.id DESC""",start,end)
        opening = {(x['method'],x['currency']):x['amount'] for x in cash(db,start,True)}
        closing = {(x['method'],x['currency']):x['amount'] for x in cash(db,end)}
        book = [dict(method=k[0],currency=k[1],opening_money=opening.get(k,0),closing_money=closing.get(k,0))
                for k in sorted(opening.keys()|closing.keys())]
        for item in counts:
            item['current_expected'] = next((x['amount'] for x in cash(db,item['day'])
                if x['method']==item['method'] and x['currency']==item['currency']),0)
        return {'sections':[{'title':'Book balances','rows':book},{'title':'Recorded closing counts','rows':counts}]}
    if mode=='payments':
        payment_match = match.replace('d.day BETWEEN','p.day BETWEEN').replace('d.user_id=?','p.user_id=?')
        data = rows(db,f"""SELECT p.day,p.method,d.currency,SUM(p.amount*({CASH_SIGN})) AS amount,
                           SUM(p.base*({CASH_SIGN})) AS amount_lrd
                           FROM payments p JOIN docs d ON d.id=p.doc WHERE {payment_match}
                           GROUP BY p.day,p.method,d.currency ORDER BY p.day DESC,p.method,d.currency""",*args)
        return {'sections':[{'title':'Money received and paid by method','rows':data}]}
    if mode=='products':
        clause = ' AND l.product=?' if params.get('product') else ''
        additional = [int(params['product'])] if clause else []
        data = rows(db,f"""SELECT p.sku,p.name,SUM(l.qty*l.factor*CASE WHEN d.kind='sale' THEN 1 ELSE -1 END) AS quantity,
            SUM(l.base*CASE WHEN d.kind='sale' THEN 1 ELSE -1 END) AS sales_lrd,
            SUM(l.cost*CASE WHEN d.kind='sale' THEN 1 ELSE -1 END) AS cost_lrd
            FROM lines l JOIN docs d ON d.id=l.doc JOIN products p ON p.id=l.product
            WHERE {match} AND d.kind IN ('sale','sale_return') {clause}
            GROUP BY p.id ORDER BY quantity DESC,p.name""",*args,*additional)
        return {'sections':[{'title':'Products ranked by units sold','rows':data}]}
    if mode in ('sales','purchases','expenses'):
        condition = {'sales':"d.kind IN ('sale','sale_return')",'purchases':"d.kind IN ('purchase','purchase_return')",'expenses':"d.kind IN ('expense','expense_return')"}[mode]
        docs = read_docs(db,f'WHERE {match} AND {condition} ORDER BY d.day DESC,d.id DESC',args)
        data = [{k:x[k] for k in ('number','day','kind','channel','contact','username','reference','currency','total','base','outstanding')} for x in docs]
        sections = [{'title':mode.capitalize()+' journal','rows':data}]
        if mode=='expenses':
            categories = rows(db,f"""SELECT COALESCE(o.reference,d.reference) AS category,
                SUM(d.base*CASE WHEN d.kind='expense' THEN 1 ELSE -1 END) AS amount_lrd
                FROM docs d LEFT JOIN docs o ON o.id=d.linked WHERE {match} AND {condition}
                GROUP BY category ORDER BY amount_lrd DESC,category""",*args)
            sections.insert(0,{'title':'Expenditure by category','rows':categories})
        return {'sections':sections}
    if any(params.get(x) for x in ('party','user','product')):
        raise Problem('Choose a journal or product report to apply these filters.')
    totals = rows(db,f"""SELECT kind,SUM(base) AS base,SUM(tax) AS tax,SUM(cost) AS cost FROM docs d WHERE {match} GROUP BY kind""",*args)
    by = {x['kind']:x for x in totals}
    def field(kind,key):
        return by.get(kind,{}).get(key,0)
    sales = field('sale','base')-field('sale_return','base')
    taxes = field('sale','tax')-field('sale_return','tax')
    cost = field('sale','cost')-field('sale_return','cost')
    gross = sales-taxes-cost
    expenses = field('expense','base')-field('expense_return','base')
    loss = field('loss','cost')
    data = [{'name':name,'amount_lrd':amount} for name,amount in (
        ('Net billed sales',sales),('Sales tax',taxes),('Sales excluding tax',sales-taxes),('Cost of goods sold',cost),
        ('Gross profit',gross),('Operating expenses',expenses),('Stock write-offs',loss),('Net operating profit',gross-expenses-loss),
        ('Net stock purchases',field('purchase','base')-field('purchase_return','base')))]
    channels = rows(db,f"""SELECT channel,SUM(base*CASE WHEN kind='sale' THEN 1 ELSE -1 END) AS sales_lrd
        FROM docs d WHERE {match} AND kind IN ('sale','sale_return') GROUP BY channel""",*args)
    return {'sections':[{'title':'Profit and loss','rows':data},{'title':'Sales by channel','rows':channels}]}
