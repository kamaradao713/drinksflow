import argparse
import getpass
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sqlite3
import time
from collections import OrderedDict
from contextlib import closing
from datetime import datetime, timezone
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import BoundedSemaphore, Lock, Thread
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import store as s

DATA = Path(os.environ.get('DRINKFLOW_DATA',s.ROOT/'data')).resolve()
DATABASE = DATA/'store.sqlite3'
ZONE_NAME = os.environ.get('DRINKFLOW_TZ','Africa/Monrovia')
try:
    ZONE = ZoneInfo(ZONE_NAME)
except ZoneInfoNotFoundError:
    if ZONE_NAME not in ('Africa/Monrovia','UTC'):
        raise
    ZONE = timezone.utc
COOKIE = 'drinkflow_session'
SESSION = 10*60*60
MAX_BODY = 128*1024
STATIC = {'/':('index.html','text/html'),'/styles.css':('styles.css','text/css'),'/app.js':('app.js','text/javascript')}
rate_lock = Lock()
attempts = OrderedDict()
backup_lock = Lock()


def today():
    return datetime.now(ZONE).date().isoformat()


def backup():
    with backup_lock:
        folder = DATA/'backups'
        folder.mkdir(mode=0o700,parents=True,exist_ok=True)
        target = folder/f'drinkflow-{datetime.now(ZONE):%Y%m%d-%H%M%S-%f}.sqlite3'
        partial = target.with_suffix('.tmp')
        try:
            with closing(s.connect(DATABASE)) as source, closing(sqlite3.connect(partial)) as dest:
                source.backup(dest)
                if dest.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise RuntimeError('Backup integrity check failed.')
            partial.replace(target)
        finally:
            partial.unlink(missing_ok=True)
        return target


def daily_backups():
    while True:
        try:
            folder=DATA/'backups'
            if not any(folder.glob(f'drinkflow-{today().replace("-","")}-*.sqlite3')):
                backup()
            cut=time.time()-30*86400
            for path in folder.glob('drinkflow-*.sqlite3'):
                if path.stat().st_mtime<cut:
                    path.unlink(missing_ok=True)
        except Exception:
            logging.exception('Automatic backup failed')
        time.sleep(300)


class LimitedServer(ThreadingHTTPServer):
    daemon_threads = True
    workers = BoundedSemaphore(24)

    def process_request(self, request, address):
        if not self.workers.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request,address)
        except Exception:
            self.workers.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request,address)
        finally:
            self.workers.release()


class Handler(BaseHTTPRequestHandler):
    server_version = 'DrinkFlow'
    sys_version = ''

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def send_data(self,status,data,content_type='application/json',headers=None):
        body = data if isinstance(data,bytes) else json.dumps(data,ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type',content_type)
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('X-Frame-Options','DENY')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        for key,value in (headers or {}).items():
            self.send_header(key,value)
        self.end_headers()
        self.wfile.write(body)

    def cookie(self,value,age):
        secure = '; Secure' if self.server.secure else ''
        return f'{COOKIE}={value}; Path=/; Max-Age={age}; HttpOnly; SameSite=Strict{secure}'

    def token(self):
        try:
            cookie = SimpleCookie(self.headers.get('Cookie','')).get(COOKIE)
            if cookie and re.fullmatch('[a-f0-9]{64}',cookie.value):
                return hashlib.sha256(cookie.value.encode()).hexdigest()
        except ValueError:
            pass
        return ''

    def identify(self,db):
        return s.one(db,'''SELECT u.id,u.username,u.role FROM users u JOIN sessions x ON x.user_id=u.id
                         WHERE x.token=? AND x.expires>? AND u.active=1''',self.token(),int(time.time()))

    def payload(self):
        if self.headers.get_content_type()!='application/json' or self.headers.get('X-DrinkFlow')!='1':
            raise s.Problem('Use the app to submit this request.',415)
        if self.headers.get('Sec-Fetch-Site') in ('cross-site','same-site'):
            raise s.Problem('Cross-origin requests are refused.',403)
        if self.headers.get('Transfer-Encoding') or not self.headers.get('Content-Length','').isdigit():
            raise s.Problem('Missing or unsupported request length.',411)
        length=int(self.headers['Content-Length'])
        if not 1<=length<=MAX_BODY:
            raise s.Problem('Request is too large.',413)
        try:
            data=json.loads(self.rfile.read(length))
        except (ValueError,UnicodeError,RecursionError):
            raise s.Problem('Invalid request data.')
        if not isinstance(data,dict):
            raise s.Problem('Expected a JSON object.')
        return data

    def login(self,db,data):
        username=s.string(data.get('username'),'Username',64)
        password=data.get('password')
        if not isinstance(password,str) or not 1<=len(password)<=128:
            raise s.Problem('Enter your password.')
        keys=[('user',username.casefold()),('peer',self.client_address[0])]
        now=time.monotonic()
        with rate_lock:
            for key,limit in zip(keys,(10,120)):
                history=[t for t in attempts.get(key,[]) if now-t<300]
                if len(history)>=limit:
                    raise s.Problem('Too many sign-in attempts. Try again in five minutes.',429)
            for key in keys:
                attempts[key]=[t for t in attempts.get(key,[]) if now-t<300]+[now]
                attempts.move_to_end(key)
            while len(attempts)>4096:
                attempts.popitem(last=False)
        user=s.one(db,'SELECT * FROM users WHERE username=? AND active=1',username)
        saved=user['password'] if user else '00000000000000000000000000000000$'+'0'*64
        result=s.password_hash(password,saved.split('$')[0])
        if not user or not hmac.compare_digest(result,saved):
            raise s.Problem('Username or password is incorrect.',401)
        with rate_lock:
            attempts.pop(keys[0],None)
        raw=secrets.token_hex(32)
        db.execute('BEGIN IMMEDIATE')
        current=s.one(db,'SELECT password,active FROM users WHERE id=?',user['id'])
        if not current or not current['active'] or current['password']!=saved:
            raise s.Problem('Username or password is incorrect.',401)
        db.execute('DELETE FROM sessions WHERE expires<=? OR token=?',(int(time.time()),self.token()))
        s.add(db,'sessions',token=hashlib.sha256(raw.encode()).hexdigest(),user_id=user['id'],expires=int(time.time())+SESSION)
        db.commit()
        return {'ok':True},{'Set-Cookie':self.cookie(raw,SESSION)}

    def do_GET(self):
        self.handle_call('GET')

    def do_POST(self):
        self.handle_call('POST')

    def handle_call(self,method):
        try:
            parsed=urlsplit(self.path)
            path=parsed.path
            if method=='GET' and path in STATIC:
                filename,mime=STATIC[path]
                self.send_data(200,(s.ROOT/'static'/filename).read_bytes(),mime+'; charset=UTF-8')
                return
            if not path.startswith('/api/'):
                raise s.Problem('Page was not found.',404)
            data=self.payload() if method=='POST' else {k:v[-1] for k,v in parse_qs(parsed.query,max_num_fields=30).items()}
            headers={}
            with closing(s.connect(DATABASE)) as db:
                if path=='/api/login' and method=='POST':
                    result,headers=self.login(db,data)
                else:
                    db.execute('BEGIN IMMEDIATE' if method=='POST' else 'BEGIN')
                    user=self.identify(db)
                    if not user:
                        raise s.Problem('Please sign in.',401)
                    if method=='POST' and path=='/api/logout':
                        db.execute('DELETE FROM sessions WHERE token=?',(self.token(),))
                        result,headers={'ok':True},{'Set-Cookie':self.cookie('',0)}
                    elif method=='POST':
                        key=self.headers.get('Idempotency-Key','')
                        if not re.fullmatch('[a-f0-9-]{32,36}',key):
                            raise s.Problem('Reload the form before retrying.')
                        digest=hashlib.sha256((path+json.dumps(data,sort_keys=True)).encode()).hexdigest()
                        old=s.one(db,'SELECT hash,response FROM requests WHERE user_id=? AND key=?',user['id'],key)
                        if old and old['hash']!=digest:
                            raise s.Problem('This submission key has already been used.',409)
                        if old:
                            result=json.loads(old['response'])
                        else:
                            result=self.route(db,method,path,data,user)
                            s.add(db,'requests',user_id=user['id'],key=key,hash=digest,response=json.dumps(result))
                    else:
                        result=self.route(db,method,path,data,user)
                    db.commit()
            if isinstance(result,Path):
                self.send_data(200,result.read_bytes(),'application/octet-stream',{'Content-Disposition':f'attachment; filename="{result.name}"'})
            else:
                self.send_data(200,result,headers=headers)
        except s.Problem as exc:
            self.send_data(exc.status,{'error':exc.message})
        except sqlite3.IntegrityError as exc:
            if 'UNIQUE constraint failed' in str(exc):
                self.send_data(409,{'error':'A record already exists with those details.'})
            else:
                logging.exception('Database integrity error')
                self.send_data(500,{'error':'The transaction failed and was rolled back.'})
        except (BrokenPipeError,ConnectionResetError,TimeoutError):
            pass
        except Exception:
            logging.exception('Request failed')
            self.send_data(500,{'error':'The request failed. Retry with the same form if you were saving a transaction.'})

    def route(self,db,method,path,data,user):
        allowed=s.ROLES[user['role']]
        uid=user['id']
        now=today()
        if method=='GET':
            if path=='/api/me':
                settings={x['key']:x['value'] for x in s.rows(db,'SELECT * FROM settings')}
                return {'user':user,'allowed':sorted(allowed),'today':now,'settings':settings}
            if path=='/api/catalog':
                catalog=s.products(db,now)
                if not allowed.intersection({'stock','purchases','reports'}):
                    for item in catalog:
                        item.pop('valuation')
                contacts=s.rows(db,'SELECT * FROM contacts ORDER BY name')
                if not allowed.intersection({'accounts','reports'}):
                    contacts=[x for x in contacts if
                              (x['kind']=='customer' and 'sales' in allowed) or
                              (x['kind']=='supplier' and 'purchases' in allowed)]
                if not allowed.intersection({'accounts','reports'}):
                    contacts=[{k:v for k,v in x.items() if k!='credit'} for x in contacts]
                lots=s.rows(db,'SELECT id,product,remaining,batch,expiry FROM lots WHERE remaining>0 ORDER BY expiry,id') if 'stock' in allowed else []
                return {'products':catalog,'contacts':contacts,'lots':lots}
            if path=='/api/overview':
                s.require(allowed,'reports')
                return s.overview(db,now)
            if path=='/api/reports':
                s.require(allowed,'reports')
                return s.report(db,data,now)
            if path=='/api/docs':
                group=s.choice(data.get('group'),('sales','purchases','expenses','stock','accounts'),'journal')
                s.require(allowed,group)
                kinds={'sales':('sale','sale_return'),'purchases':('purchase','purchase_return','order'),
                       'expenses':('expense','expense_return'),'stock':('opening','gain','loss'),'accounts':('fund_in','fund_out','sale','purchase','sale_return','purchase_return')}[group]
                start=s.day(data.get('from',now[:8]+'01'))
                end=s.day(data.get('to',now))
                if start>end or end>now:
                    raise s.Problem('Choose a date range ending on or before today.')
                before=data.get('before','0')
                if not before.isdigit() or len(before)>12:
                    raise s.Problem('Invalid page cursor.')
                docs=s.read_docs(db,'WHERE d.kind IN ('+','.join('?' for _ in kinds)+') AND d.day BETWEEN ? AND ? AND (?=0 OR d.id<?) ORDER BY d.id DESC LIMIT 101',
                                 (*kinds,start,end,int(before),int(before)))
                if group=='sales' and 'reports' not in allowed:
                    for doc in docs:
                        doc.pop('cost',None)
                return {'rows':docs[:100],'next':docs[99]['id'] if len(docs)>100 else None}
            if path=='/api/statement':
                s.require(allowed,'reports')
                party=data.get('party','')
                if not party.isdigit() or len(party)>12:
                    raise s.Problem('Choose a contact.')
                cid=int(party)
                docs=s.read_docs(db,"WHERE d.party=? AND d.kind<>'order' ORDER BY d.day DESC,d.id DESC",(cid,))
                payments=s.rows(db,'''SELECT p.day,p.doc,p.method,p.amount,d.kind,d.currency
                    FROM payments p JOIN docs d ON d.id=p.doc WHERE d.party=? ORDER BY p.day DESC,p.id DESC''',cid)
                for item in payments:
                    item['number']=s.doc_number(item['kind'],item['doc'])
                return {'docs':docs,'payments':payments}
            if path=='/api/staff':
                s.require(allowed,'reports')
                return s.rows(db,'SELECT id,username,role,active FROM users ORDER BY username')
            if path=='/api/audit':
                s.require(allowed,'admin')
                return s.rows(db,'SELECT a.*,u.username FROM audit a LEFT JOIN users u ON u.id=a.user_id ORDER BY a.id DESC LIMIT 200')
            if path=='/api/backup':
                s.require(allowed,'admin')
                return backup()
        if method=='POST':
            if path=='/api/products':
                s.require(allowed,'stock')
                return s.product_save(db,data,uid)
            if path=='/api/contacts':
                if ('accounts' not in allowed and
                        (data.get('kind')!='customer' or 'sales' not in allowed) and
                        (data.get('kind')!='supplier' or 'purchases' not in allowed)):
                    raise s.Problem('Contact editing is restricted.',403)
                return s.contact_save(db,data,uid,bool(allowed.intersection({'purchases','accounts'})),'accounts' in allowed)
            if path=='/api/docs':
                return s.post(db,data,uid,allowed,now)
            if path=='/api/counts':
                s.require(allowed,'accounts')
                return s.count_save(db,data,uid,now)
            if path=='/api/users':
                s.require(allowed,'admin')
                return {'id':s.user_save(db,data,uid)}
            if path=='/api/settings':
                s.require(allowed,'admin')
                name=s.string(data.get('name'),'Store name',80)
                rate=s.money(data.get('rate'),'Default exchange rate',10000,100_000_000)
                if rate<=0:
                    raise s.Problem('Enter a positive USD exchange rate.')
                for key,value in [('name',name),('rate',str(rate))]:
                    db.execute('INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,value))
                s.audit(db,uid,'Saved store settings','store')
                return {'ok':True}
        match=re.fullmatch(r'/api/docs/(\d{1,12})(?:/(payment|return))?',path)
        if match:
            did=int(match[1])
            if method=='GET' and match[2] is None:
                return s.detail(db,did,allowed)
            if method=='POST' and match[2]=='payment':
                return s.payment(db,did,data,uid,allowed,now)
            if method=='POST' and match[2]=='return':
                return s.reverse(db,did,data,uid,allowed,now)
        raise s.Problem('Action was not found.',404)


def restore(snapshot):
    source = snapshot.resolve(strict=True)
    DATA.mkdir(parents=True,exist_ok=True,mode=0o700)
    pending = DATA / ('restore-'+secrets.token_hex(12)+'.tmp')
    try:
        with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as src, closing(sqlite3.connect(pending)) as dst:
            src.backup(dst)
            if (dst.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or
                    dst.execute('PRAGMA foreign_key_check').fetchone()):
                raise ValueError('Snapshot failed database integrity checks.')
            if not dst.execute("SELECT id FROM users WHERE role='Owner' AND active=1").fetchone():
                raise ValueError('Snapshot has no active owner account.')
            dst.execute('DELETE FROM sessions')
            dst.commit()
        os.link(pending,DATABASE)
    finally:
        pending.unlink(missing_ok=True)


def main():
    os.umask(0o077)
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    parser=argparse.ArgumentParser()
    sub=parser.add_subparsers(dest='command',required=True)
    setup=sub.add_parser('setup')
    setup.add_argument('username')
    reset=sub.add_parser('reset-password')
    reset.add_argument('username')
    sub.add_parser('backup')
    restore_parser=sub.add_parser('restore')
    restore_parser.add_argument('snapshot',type=Path)
    serve=sub.add_parser('serve')
    serve.add_argument('--port',default=8000,type=int)
    serve.add_argument('--secure-cookie',action='store_true',help='Enable behind an HTTPS reverse proxy')
    args=parser.parse_args()
    if args.command=='restore':
        if DATABASE.exists() or DATABASE.with_suffix('.sqlite3-wal').exists() or DATABASE.with_suffix('.sqlite3-shm').exists():
            parser.error('Set DRINKFLOW_DATA to a new directory for restoration.')
        try:
            restore(args.snapshot)
        except (OSError,sqlite3.Error,ValueError) as exc:
            parser.error(str(exc))
        print('Restored '+str(DATABASE))
        return
    s.initialize(DATABASE)
    if args.command in ('setup','reset-password'):
        with closing(s.connect(DATABASE)) as db:
            db.execute('BEGIN IMMEDIATE')
            if args.command=='setup' and s.one(db,'SELECT id FROM users LIMIT 1'):
                parser.error('The store already has an owner.')
            if args.command=='reset-password':
                user=s.one(db,'SELECT id,username,role,active FROM users WHERE username=?',args.username)
                if not user:
                    parser.error('That account does not exist.')
            else:
                user={'username':args.username,'role':'Owner'}
            password=getpass.getpass('Password (12 characters or more): ')
            if password!=getpass.getpass('Repeat password: '):
                parser.error('Passwords differ.')
            try:
                s.user_save(db,{**user,'password':password})
                db.commit()
            except s.Problem as exc:
                parser.error(exc.message)
            print('Account saved.')
    elif args.command=='backup':
        print(backup())
    else:
        with closing(s.connect(DATABASE)) as db:
            if not s.one(db,'SELECT id FROM users WHERE role=? AND active=1','Owner'):
                parser.error('Create the owner first with: python server.py setup owner')
        server=LimitedServer(('127.0.0.1',args.port),Handler)
        server.secure=args.secure_cookie
        Thread(target=daily_backups,daemon=True).start()
        print(f'DrinkFlow is listening at http://127.0.0.1:{server.server_port}',flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


if __name__=='__main__':
    main()
