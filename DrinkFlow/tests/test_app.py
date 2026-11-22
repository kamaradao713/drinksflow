import concurrent.futures
import http.cookiejar
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import store

PASSWORD = 'test-only pass phrase 42'


class Client:
    def __init__(self,url):
        self.url=url
        self.cookies=http.cookiejar.CookieJar()
        self.opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))

    def call(self,path,body=None,key=None,status=200,headers=None,raw=False):
        req_headers=headers if headers is not None else {
            'Content-Type':'application/json','X-DrinkFlow':'1','Idempotency-Key':key or uuid.uuid4().hex}
        request=urllib.request.Request(self.url+'/api/'+path,
             data=json.dumps(body).encode() if body is not None else None,headers=req_headers)
        try:
            response=self.opener.open(request,timeout=15)
        except urllib.error.HTTPError as error:
            response=error
        with response:
            payload=response.read()
            assert response.status==status, (path,response.status,payload)
            return payload if raw else json.loads(payload)

    def login(self,name='owner'):
        self.call('login',{'username':name,'password':PASSWORD})


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.dbpath=Path(self.folder.name)/'store.sqlite3'
        store.initialize(self.dbpath)
        db=store.connect(self.dbpath)
        db.execute('BEGIN IMMEDIATE')
        store.user_save(db,{'username':'owner','password':PASSWORD,'role':'Owner'})
        db.commit()
        db.close()
        self.server=subprocess.Popen([sys.executable,'server.py','serve','--port','0'],cwd=ROOT,
            env={**os.environ,'DRINKFLOW_DATA':self.folder.name},stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        self.addCleanup(self.stop_server)
        self.url=self.server.stdout.readline().split()[-1]
        self.client=Client(self.url)
        self.client.login()
        self.today=self.client.call('me')['today']

    def stop_server(self):
        self.server.terminate()
        stdout,stderr=self.server.communicate(timeout=8)
        if 'Traceback' in stderr:
            print(stderr,file=sys.stderr)

    def seed(self):
        shop=self.client
        product=shop.call('products',{'sku':'10001','name':'Ginger Ale','reorder':5,'units':[
            {'name':'Bottle','factor':1,'retail':'150','wholesale':'100'},
            {'name':'Carton','factor':24,'retail':'3200','wholesale':'2400'}]})['id']
        customer=shop.call('contacts',{'kind':'customer','name':'Party Shop','credit':'3000'})['id']
        supplier=shop.call('contacts',{'kind':'supplier','name':'Local Supply'})['id']
        return product,customer,supplier

    def test_store_lifecycle(self):
        product,customer,supplier=self.seed()
        client=self.client
        item={'product':product,'qty':2,'unit':'Carton','price':'1440','batch':'A'}
        order=client.call('docs',{'kind':'order','party':supplier,'lines':[item]})['id']
        self.assertEqual(client.call('catalog')['products'][0]['quantity'],0)
        buy={'kind':'purchase','party':supplier,'lines':[item],'linked':order,'freight':'120',
             'paid':'2000','method':'Cash','due':self.today}
        purchase=client.call('docs',buy)['id']
        client.call('docs',buy,status=409)
        self.assertEqual(client.call('catalog')['products'][0]['valuation'],300000)
        expired={**item,'qty':1,'expiry':'2020-01-01','batch':'Expired'}
        client.call('docs',{'kind':'purchase','party':supplier,'lines':[expired],'paid':'1440','method':'Cash'})
        totals=client.call('catalog')['products'][0]
        self.assertEqual((totals['quantity'],totals['sellable']),(72,48))
        sale={'kind':'sale','channel':'wholesale','party':customer,'lines':[{**item,'qty':1}],
              'currency':'USD','rate':'200.0000','discount':'0.50','tax':'0.50','paid':'2.00','method':'Mobile money','due':self.today}
        key=uuid.uuid4().hex
        saved=client.call('docs',sale,key)
        self.assertEqual(saved,client.call('docs',sale,key))
        client.call('docs',{**sale,'paid':'4.00'},key,status=409)
        invoice=client.call('docs/'+str(saved['id']))
        self.assertEqual((invoice['total'],invoice['base'],invoice['tax'],invoice['cost'],invoice['outstanding']),(1200,240000,10000,150000,1000))
        client.call('docs/'+str(saved['id'])+'/payment',{'amount':'3.25','method':'Bank transfer'})
        client.call('docs/'+str(purchase)+'/payment',{'amount':'1000','method':'Cash'})
        self.assertEqual(client.call('docs/'+str(purchase))['outstanding'],0)
        client.call('docs/'+str(purchase)+'/return',{'note':'Already sold part of batch','method':'Cash'},status=409)
        client.call('docs',{'kind':'sale','channel':'retail','lines':[{**item,'qty':2}], 'paid':'6400','method':'Cash'},status=409)
        self.assertEqual(client.call('catalog')['products'][0]['quantity'],48)
        expense=client.call('docs',{'kind':'expense','reference':'Electricity','amount':'100','method':'Cash'})
        self.assertRegex(expense['number'],r'^EXP-')
        client.call('docs',{'kind':'fund_in','amount':'10000','method':'Cash','note':'Starting float'})
        client.call('counts',{'amount':'5460','currency':'LRD','method':'Cash'})
        dash=client.call('overview')
        self.assertEqual((dash['today'],dash['stock_value'],dash['gross'],dash['net']),(240000,294000,80000,70000))
        data={key:client.call('reports?view='+key) for key in ['summary','sales','products','purchases','expenses','stock','balances','payments','closing']}
        self.assertEqual(data['stock']['sections'][0]['rows'][0]['closing'],48)
        self.assertEqual(data['balances']['sections'][0]['rows'][0]['outstanding'],675)
        self.assertEqual(data['closing']['sections'][1]['rows'][0]['difference'],0)
        self.assertEqual(len(client.call('statement?party='+str(customer))['payments']),2)
        client.call('docs/'+str(saved['id'])+'/return',{'note':'All stock returned','method':'Mobile money'})
        self.assertEqual(client.call('overview')['today'],0)
        client.call('docs/'+str(saved['id'])+'/payment',{'amount':'1','method':'Cash'},status=409)
        client.call('docs/'+str(purchase)+'/return',{'note':'Complete batch returned','method':'Cash'})
        self.assertEqual(client.call('catalog')['products'][0]['quantity'],24)
        client.call('docs/'+str(expense['id'])+'/return',{'note':'Expense entry cancelled','method':'Cash'})
        self.assertEqual(client.call('reports?view=expenses')['sections'][0]['rows'][0]['amount_lrd'],0)
        blob=client.call('backup',raw=True)
        restored=Path(self.folder.name)/'restored.sqlite3'
        restored.write_bytes(blob)
        with sqlite3.connect(restored) as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertEqual(db.execute('SELECT SUM(remaining) FROM lots').fetchone()[0],24)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
        restored_env={**os.environ,'DRINKFLOW_DATA':self.folder.name+'/restore'}
        Path(restored_env['DRINKFLOW_DATA']).mkdir()
        subprocess.run([sys.executable,'server.py','restore',str(restored)],cwd=ROOT,env=restored_env,
                       check=True,capture_output=True,text=True)
        refused=subprocess.run([sys.executable,'server.py','restore',str(restored)],cwd=ROOT,env=restored_env,
                               capture_output=True,text=True)
        self.assertNotEqual(refused.returncode,0)
        server=subprocess.Popen([sys.executable,'server.py','serve','--port','0'],cwd=ROOT,env=restored_env,
                                stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            restarted=Client(server.stdout.readline().split()[-1])
            restarted.login()
            self.assertEqual(restarted.call('catalog')['products'][0]['quantity'],24)
        finally:
            server.terminate()
            server.communicate(timeout=8)

    def test_concurrent_checkout_permissions_and_rollback(self):
        client=self.client
        product,customer,supplier=self.seed()
        client.call('docs',{'kind':'opening','lines':[{'product':product,'qty':1,'unit':'Carton','price':'1000'}]})
        client.call('users',{'username':'cashier','password':PASSWORD,'role':'Cashier'})
        staff=Client(self.url)
        staff.login('cashier')
        staff.call('overview',status=403)
        staff.call('backup',status=403)
        staff.call('products',{},status=403)
        staff.call('docs',{},status=415,headers={'Content-Type':'application/json'})
        staff.call('contacts',{'id':customer,'kind':'customer','name':'Same Shop','credit':'0'})
        self.assertEqual(next(x for x in client.call('catalog')['contacts'] if x['id']==customer)['credit'],300000)
        line={'product':product,'qty':1,'unit':'Carton'}
        sale={'kind':'sale','channel':'retail','lines':[line],'paid':'3200','method':'Cash'}
        client.call('docs',{**sale,'lines':[line,line],'paid':'6400'},status=409)
        self.assertEqual(client.call('catalog')['products'][0]['quantity'],24)
        def attempt(_):
            peer=Client(self.url)
            peer.login('cashier')
            try:
                return peer.call('docs',sale)['number']
            except AssertionError as error:
                assert error.args[0][1]==409, error
                return None
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            outcomes=list(pool.map(attempt,range(2)))
        self.assertEqual(sum(value is not None for value in outcomes),1)
        self.assertEqual(client.call('catalog')['products'][0]['quantity'],0)
        staff.call('logout',{})
        staff.call('me',status=401)


class BrowserTest(StoreTest):
    test_store_lifecycle=None
    test_concurrent_checkout_permissions_and_rollback=None

    @unittest.skipUnless(os.environ.get('DRINKFLOW_BROWSER'),'Set DRINKFLOW_BROWSER to the Chromium executable')
    def test_browser_desktop_and_phone(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=os.environ['DRINKFLOW_BROWSER'],headless=True,args=['--no-sandbox'])
            page=browser.new_page(viewport={'width':1440,'height':900},accept_downloads=True)
            errors=[]
            page.on('pageerror',lambda error: errors.append(str(error)))
            page.on('console',lambda entry: errors.append(entry.text) if entry.type=='error' and ('CSP' in entry.text or 'Content Security Policy' in entry.text) else None)
            page.goto(self.url)
            page.get_by_label('Username').fill('owner')
            page.get_by_label('Password').fill(PASSWORD)
            page.get_by_role('button',name='Sign in').click()
            page.get_by_role('heading',name='Dashboard').wait_for()
            page.get_by_role('link',name='Inventory').click()
            page.get_by_role('button',name='New product').click()
            page.get_by_label('Product name').fill('Orange Soda')
            page.get_by_label('SKU / barcode').fill('2000')
            page.get_by_label('Retail price',exact=True).fill('150')
            page.get_by_label('Wholesale price',exact=True).fill('100')
            page.get_by_role('button',name='Add package').click()
            page.get_by_label('Package name').fill('Crate')
            page.get_by_label('Items per unit').last.fill('12')
            page.get_by_label('Retail price').last.fill('1600')
            page.get_by_label('Wholesale price').last.fill('1200')
            page.get_by_role('button',name='Save product').click()
            page.get_by_text('Orange Soda',exact=True).wait_for()
            page.get_by_role('button',name='Opening stock',exact=True).click()
            page.get_by_label('Product',exact=True).select_option(label='Orange Soda · 2000')
            page.get_by_label('Unit',exact=True).select_option('Crate')
            page.get_by_label('Quantity').fill('2')
            page.get_by_label('Cost per selected unit').fill('600')
            page.get_by_role('button',name='Post transaction').click()
            page.get_by_text('24',exact=True).first.wait_for()
            page.get_by_role('link',name='Sales',exact=False).first.click()
            page.get_by_role('button',name='New sale').click()
            page.get_by_label('Scan or enter SKU').fill('2000')
            page.get_by_label('Scan or enter SKU').press('Enter')
            page.get_by_label('Unit',exact=True).select_option('Crate')
            page.get_by_role('button',name='Post transaction').click()
            page.get_by_text(re.compile(r'INV-\d+ · Sale')).wait_for()
            page.pdf(path=Path(self.folder.name)/'receipt.pdf')
            page.get_by_role('button',name='Close',exact=True).click()
            page.get_by_role('link',name='Reports').click()
            page.get_by_role('heading',name='Profit and loss').wait_for()
            self.assertIn('1,000.00',page.locator('main').inner_text())
            with page.expect_download() as download:
                page.get_by_role('button',name='Export CSV').click()
            self.assertIn('1000.00',download.value.path().read_text(encoding='utf-8-sig'))
            page.set_viewport_size({'width':390,'height':844})
            for tab in ('Dashboard','Sales','Inventory','Purchases','Expenditure','Reports','Contacts','Cash & Accounts','Settings'):
                page.get_by_role('link',name=tab,exact=False).first.click()
                page.get_by_role('heading',name=tab,exact=True).wait_for()
                self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'),tab)
            page.get_by_role('link',name='Sales',exact=False).first.click()
            page.get_by_role('button',name='New sale').click()
            page.get_by_role('dialog').wait_for()
            self.assertTrue(page.evaluate('document.querySelector("dialog").scrollWidth <= document.querySelector("dialog").clientWidth+1'))
            self.assertEqual(errors,[])
            browser.close()


if __name__=='__main__':
    unittest.main()
