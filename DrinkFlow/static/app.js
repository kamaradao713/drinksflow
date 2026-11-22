'use strict';

const $ = (selector, root=document) => root.querySelector(selector);
const all = (selector, root=document) => [...root.querySelectorAll(selector)];
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c]);
const main = $('#main');
const dialog = $('#dialog');
const content = $('#dialog-content');
const money = (value,currency='LRD') => currency+' '+(Number(value||0)/100).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
const decimal = value => (Number(value||0)/100).toFixed(2);
const count = value => Number(value||0).toLocaleString('en-US');
const human = key => (key ?? '').replaceAll('_',' ').replace(/\b[a-z]/g,c=>c.toUpperCase());
const title = {dashboard:'Dashboard',sales:'Sales',inventory:'Inventory',purchases:'Purchases',expenses:'Expenditure',
  reports:'Reports',contacts:'Contacts',accounts:'Cash & Accounts',settings:'Settings'};
const sections = [['dashboard','reports','◫'],['sales','sales','▤'],['inventory','stock','▦'],['purchases','purchases','↙'],
  ['expenses','expenses','↗'],['reports','reports','▥'],['contacts','sales purchases accounts','◎'],
  ['accounts','accounts','▣'],['settings','admin','⚙']];
const methods = ['Cash','Mobile money','Bank transfer'];
const roles = ['Owner','Manager','Cashier','Inventory Officer','Accountant'];
const state = {me:null,catalog:{products:[],contacts:[],lots:[]},page:'',dates:{},cursors:{},reportView:'summary'};
const allowed = permission => state.me.allowed.includes(permission);
const selected = (a,b) => String(a)===String(b) ? ' selected' : '';
const options = (values,current='') => values.map(value => {
  const [id,label] = Array.isArray(value) ? value : [value,value];
  return '<option value="'+escapeHTML(id)+'"'+selected(id,current)+'>'+escapeHTML(label)+'</option>';
}).join('');
const label = (name,control,cls='') => '<label class="'+cls+'">'+escapeHTML(name)+control+'</label>';
const input = (name,caption,value='',attrs='',type='text') =>
  label(caption,'<input name="'+name+'" type="'+type+'" value="'+escapeHTML(value)+'" '+attrs+'>');
const select = (name,caption,values,value='',attrs='',cls='') =>
  label(caption,'<select name="'+name+'" '+attrs+'>'+options(values,value)+'</select>',cls);
const textArea = (name,caption,value='',attrs='') =>
  label(caption,'<textarea name="'+name+'" '+attrs+'>'+escapeHTML(value)+'</textarea>','full');
const button = (text,action,cls='',id='') => '<button type="button" class="'+cls+'" data-action="'+action+'" data-id="'+escapeHTML(id)+'">'+escapeHTML(text)+'</button>';
const docLink = (text,id) => button(text,'detail','link',id);
const empty = text => '<p class="empty">'+escapeHTML(text)+'</p>';
const section = (name,html,extra='') => '<section class="card"><header class="card-head"><h2>'+escapeHTML(name)+'</h2>'+extra+'</header>'+html+'</section>';

function table(headers,rows,message='No records for this selection.') {
  if (!rows.length) return empty(message);
  return '<div class="table-wrap" tabindex="0" role="region" aria-label="'+escapeHTML(headers[0]+' table')+'"><table><thead><tr>'+
    headers.map(x=>'<th scope="col">'+escapeHTML(x)+'</th>').join('')+'</tr></thead><tbody>'+
    rows.map(row=>'<tr>'+row.map(cell=>'<td>'+cell+'</td>').join('')+'</tr>').join('')+'</tbody></table></div>';
}

function header(name,description,action='') {
  return '<header class="page-head"><div><h1>'+escapeHTML(name)+'</h1><p>'+escapeHTML(description)+'</p></div>'+action+'</header>';
}

function metric(name,value,caption) {
  return '<article class="metric"><span>'+escapeHTML(name)+'</span><strong>'+escapeHTML(value)+'</strong><small>'+escapeHTML(caption)+'</small></article>';
}

function toast(message) {
  const box=$('#toast');
  box.textContent=message;
  box.hidden=false;
  clearTimeout(toast.timer);
  toast.timer=setTimeout(()=>box.hidden=true,4300);
}

class RequestError extends Error {
  constructor(message,status) {super(message);this.status=status;}
}

function requestKey() {
  return [...crypto.getRandomValues(new Uint8Array(16))].map(x=>x.toString(16).padStart(2,'0')).join('');
}

async function api(path,body,key) {
  const settings=body===undefined ? {} : {
    method:'POST',headers:{'Content-Type':'application/json','X-DrinkFlow':'1','Idempotency-Key':key||requestKey()},body:JSON.stringify(body)
  };
  const response=await fetch('/api/'+path,{...settings,cache:'no-store'});
  const data=await response.json();
  if (!response.ok) {
    if (response.status===401 && path!=='login') loginScreen();
    throw new RequestError(data.error||'The request could not be completed.',response.status);
  }
  return data;
}

function loginScreen() {
  state.me=null;
  if(dialog.open) dialog.close();
  state.catalog={products:[],contacts:[],lots:[]};
  state.document=null;
  state.staff=[];
  state.reportData=null;
  state.reportFilters={};
  state.reportView='summary';
  state.dates={};
  state.cursors={};
  main.replaceChildren();
  content.replaceChildren();
  $('#shell').hidden=true;
  const login=$('#login');
  login.hidden=false;
  login.innerHTML='<article class="login-card"><span class="brand-icon" aria-hidden="true">◒</span><h1>DrinkFlow</h1><p class="muted">Sign in to your store.</p>'+
    '<form id="login-form">'+input('username','Username','','required autocomplete="username" autofocus')+
    input('password','Password','','required autocomplete="current-password"','password')+
    '<button class="primary" type="submit">Sign in</button><p class="form-error" role="alert" hidden></p></form></article>';
  listenForm($('#login-form'),async form=>{await api('login',fields(form));await start();});
  $('[name=username]',login).focus();
}

function fields(form) {return Object.fromEntries(new FormData(form));}

function listenForm(form,run) {
  form.addEventListener('submit',async event=>{
    event.preventDefault();
    if(form.dataset.saving) return;
    const error=$('.form-error',form);
    if (error) error.hidden=true;
    const save=$('button[type=submit]',form);
    if (save) save.disabled=true;
    form.dataset.saving='1';
    try {await run(form);}
    catch (err) {
      if (error) {error.textContent=err.message||'Save failed.';error.hidden=false;}
      else toast(err.message||'Save failed.');
    } finally {delete form.dataset.saving;if(save) save.disabled=false;}
  });
}

function open(titleText,body) {
  content.innerHTML='<header class="dialog-head"><h2 id="dialog-title">'+escapeHTML(titleText)+'</h2>'+
    '<button type="button" class="close" data-action="close" aria-label="Close">×</button></header>'+body;
  document.body.classList.toggle('receipt-mode',!!$('.receipt',content));
  if (!dialog.open) dialog.showModal();
  dialog.scrollTop=0;
}

function formDialog(name,body,saveText,path,makePayload,after) {
  open(name,'<form>'+body+'<p class="form-error" role="alert" hidden></p><div class="dialog-actions">'+
    button('Cancel','close')+'<button class="primary" type="submit">'+escapeHTML(saveText)+'</button></div></form>');
  const form=$('form',content);
  let previous='',key=requestKey(),uncertain=false;
  listenForm(form,async current=>{
    const payload=makePayload(current);
    const serialized=JSON.stringify(payload);
    if (previous && previous!==serialized) {
      if (uncertain) throw new Error('The last save could have succeeded. Restore the original values and retry, or check the journal before entering it again.');
      key=requestKey();
    }
    previous=serialized;
    let result;
    try {
      result=await api(path,payload,key);
    } catch (err) {
      uncertain=!err.status || err.status>=500;
      throw err;
    }
    dialog.close();
    toast(result.number ? result.number+' saved.' : 'Saved.');
    try {
      await refresh();
      if (after) await after(result);
    } catch (err) {toast('Saved. '+err.message);}
  });
  return form;
}

async function start() {
  try {state.me=await api('me');}
  catch(error) {loginScreen();if(error.status!==401) toast(error.message);return;}
  $('#login').hidden=true;
  $('#shell').hidden=false;
  paintIdentity();
  await navigate();
}

function paintIdentity() {
  const {user,settings}=state.me;
  $('#store-name').textContent=settings.name||'Store management';
  $('#identity').innerHTML='<div class="profile"><div><strong>'+escapeHTML(user.username)+'</strong> <small>'+escapeHTML(user.role)+'</small></div>'+
    button('Sign out','logout')+'</div>';
}

function formatDate(value) {
  return value ? new Date(value+'T12:00:00Z').toLocaleDateString('en-US',{month:'short',day:'numeric',timeZone:'UTC'}) : '';
}

function updateNav() {
  $('#nav').innerHTML=sections.filter(([,perms])=>perms.split(' ').some(allowed)).map(([id,,icon])=>
    '<a href="#'+id+'"'+(state.page===id?' aria-current="page"':'')+'><span class="icon" aria-hidden="true">'+icon+'</span>'+title[id]+'</a>').join('');
}

let navigation=0;
async function navigate() {
  if(!state.me) return;
  const requested=location.hash.slice(1);
  const ready=sections.filter(([,perms])=>perms.split(' ').some(allowed));
  state.page=ready.some(([id])=>id===requested)?requested:ready[0][0];
  updateNav();
  await refresh();
  if (!dialog.open) main.focus({preventScroll:true});
}

async function refresh() {
  if (!state.me) return;
  const generation=++navigation;
  const page=state.page;
  try {
    const catalog=await api('catalog');
    if(generation!==navigation) return;
    state.catalog=catalog;
    let html;
    if(page==='dashboard') html=dashboard(await api('overview'));
    else if(page==='inventory') html=await inventory();
    else if(page==='reports') html=await reports();
    else if(page==='contacts') html=contactsPage();
    else if(page==='settings') html=await settingsPage();
    else html=await journal(page);
    if(generation!==navigation) return;
    main.innerHTML=html;
    wirePage(page);
  } catch (error) {
    if (generation===navigation && state.me) main.innerHTML=header(title[page],error.message)+button('Retry','reload');
  }
}

function dashboard(data) {
  const list = (items,render,message) => items.length?'<ul class="list">'+items.map(render).join('')+'</ul>':empty(message);
  const low=list(data.low.slice(0,5),x=>'<li><strong>'+escapeHTML(x.name)+'<small>'+escapeHTML(x.sku)+'</small></strong><span class="tag warn">'+count(x.sellable)+' sellable</span></li>','Stock is above reorder levels.');
  const expiry=list(data.expiry.slice(0,5),x=>'<li><strong>'+escapeHTML(x.name)+'<small>'+escapeHTML(x.batch||x.sku)+'</small></strong><span class="tag warn">'+escapeHTML(x.expiry)+'</span></li>','No lots expire in the next 30 days.');
  const account = kind => data.accounts.find(x=>x.kind===kind)?.amount||0;
  const goals=data.channels.map(x=>'<li><strong>'+human(x.channel)+'</strong><span>'+money(x.amount)+'</span></li>').join('');
  const salesAction=allowed('sales')?button('＋ New sale','trade','primary','sale'):'';
  return header('Dashboard',new Date(state.me.today+'T12:00:00Z').toLocaleDateString('en-GB',{weekday:'long',day:'numeric',month:'long',year:'numeric',timeZone:'UTC'}),salesAction)+
    '<section class="hero"><div><h2>'+escapeHTML(state.me.settings.name||'Store overview')+'</h2><p>'+count(data.stocked)+' stocked products · '+count(data.low.length)+' at or below reorder level</p></div>'+
    '<a class="button" href="#reports">View reports →</a></section>'+
    '<div class="metrics">'+metric('Sales today',money(data.today),'Net billed sales')+metric('Sales this week',money(data.week),'Since Monday')+
    metric('Sales this month',money(data.month),'Month to date')+metric('Net operating profit',money(data.net),'Month to date')+
    metric('Gross profit',money(data.gross),'Month to date')+metric('Purchases',money(data.purchases),'Month to date')+
    metric('Expenditure',money(data.expenses),'Month to date')+metric('Inventory value',money(data.stock_value),'At recorded cost')+
    '</div><div class="split"><div>'+section('Sales trend','<canvas class="chart" id="trend" role="img" aria-label="'+escapeHTML(data.trend.map(x=>formatDate(x.day)+' '+money(x.amount)).join(', '))+
      '" data-points="'+escapeHTML(JSON.stringify(data.trend))+'"></canvas><p class="chart-summary">Net billed sales in LRD</p>','<span>Last 7 days</span>')+
    section('Recent activity',docTable(data.recent,false))+
    section('Best sellers',list(data.top,x=>'<li><strong>'+escapeHTML(x.name)+'</strong><span>'+count(x.quantity)+' net units</span></li>','Sales will appear after posting your first invoice.'),'<span>This month</span>')+'</div><div>'+
    section('Stock to replenish',low,'<span>'+count(data.low.length)+' products</span>')+section('Expiry watch',expiry,'<span>Expired and next 30 days</span>')+
    section('Sales channels','<ul class="list">'+goals+'</ul>','<span>This month</span>')+
    section('Money & balances',list(data.cash,x=>'<li><strong>'+escapeHTML(x.method)+'</strong><span>'+money(x.amount,x.currency)+'</span></li>','No payments recorded.')+
    '<ul class="list"><li><strong>Customer receivables</strong><span>'+money(account('sale'))+'</span></li><li><strong>Supplier payables</strong><span>'+money(account('purchase'))+'</span></li></ul>')+'</div></div>';
}

function drawTrend(canvas) {
  const points=JSON.parse(canvas.dataset.points), w=canvas.clientWidth, h=canvas.clientHeight, dpr=window.devicePixelRatio||1;
  if(!w) return;
  canvas.width=w*dpr;canvas.height=h*dpr;
  const ctx=canvas.getContext('2d');
  ctx.scale(dpr,dpr);
  const values=points.map(x=>x.amount);
  const min=Math.min(0,...values),max=Math.max(1,...values);
  const top=15,bottom=h-30;
  const y=value=>bottom-(value-min)/(max-min)*(bottom-top);
  const step=(w-12)/points.length;
  ctx.font='11px system-ui';
  ctx.textAlign='center';
  ctx.strokeStyle='#c9ded5';
  ctx.beginPath();ctx.moveTo(5,y(0));ctx.lineTo(w-5,y(0));ctx.stroke();
  points.forEach((point,i)=>{
    const center=6+step*(i+.5), bar=Math.min(39,step*.59);
    ctx.fillStyle=point.amount<0?'#aa5635':'#25846e';
    ctx.fillRect(center-bar/2,Math.min(y(0),y(point.amount)),bar,Math.max(2,Math.abs(y(0)-y(point.amount))));
    ctx.fillStyle='#526564';
    ctx.fillText(formatDate(point.day),center,h-7,step-2);
  });
}

function range(page) {
  return state.dates[page]||{from:state.me.today.slice(0,8)+'01',to:state.me.today};
}

function filters(page,extra='') {
  const date=range(page);
  return '<form class="filter" id="filters">'+select('period','Period',[['','Custom'],['today','Today'],['week','This week'],['month','This month'],['year','This year']])+
    input('from','From',date.from,'required max="'+state.me.today+'"','date')+input('to','Through',date.to,'required max="'+state.me.today+'"','date')+
    extra+'<button type="submit">Apply</button></form>';
}

function applyPeriod(form) {
  const value=form.elements.period.value,today=state.me.today;
  let from=today;
  if (!value) return;
  if(value==='week') {
    const date=new Date(today+'T12:00:00Z');
    date.setUTCDate(date.getUTCDate()-(date.getUTCDay()+6)%7);
    from=date.toISOString().slice(0,10);
  }
  if(value==='month') from=today.slice(0,8)+'01';
  if(value==='year') from=today.slice(0,4)+'-01-01';
  form.elements.from.value=from;
  form.elements.to.value=today;
}

function docTable(records,withBalances=true) {
  const headers=['Document','Date','Type','Contact / reference','Total'];
  if(withBalances) headers.push('Status');
  return table(headers,records.map(d=>{
    const status=d.child?(d.kind==='order'?'Received':'Reversed'):d.outstanding?money(d.outstanding,d.currency)+' due':
      d.kind==='order'?'Open':d.kind.endsWith('_return')?'Return posted':'Posted';
    const cells=[docLink(d.number,d.id),escapeHTML(d.day),escapeHTML(human(d.kind))+(d.channel?'<small class="subline">'+human(d.channel)+'</small>':''),
      escapeHTML(d.contact||d.reference||'—'),escapeHTML(money(d.total,d.currency))];
    if(withBalances) cells.push('<span class="tag'+(d.outstanding?' warn':'')+'">'+escapeHTML(status)+'</span>');
    return cells;
  }));
}

async function journal(page) {
  const {rows,next}=await api('docs?'+new URLSearchParams({group:page,...range(page),before:state.cursors[page]||0}));
  const actions={
    sales:button('＋ New sale','trade','primary','sale'),
    purchases:button('＋ Receive stock','trade','primary','purchase')+' '+button('Create purchase order','trade','','order'),
    expenses:button('＋ Record expense','trade','primary','expense'),
    accounts:button('Record closing count','cash-count','primary')+' '+button('Add funds','trade','','fund_in')+' '+button('Withdraw funds','trade','','fund_out')
  };
  const descriptions={sales:'Retail and wholesale invoices, collections and returns.',purchases:'Supplier orders, deliveries and outstanding payments.',
    expenses:'Record and review day-to-day operating costs.',accounts:'Customer collections, supplier payments and cash reconciliation.'};
  return header(title[page],descriptions[page],'<div class="table-actions">'+actions[page]+'</div>')+filters(page)+
    section(page==='accounts'?'Account activity':'Transaction history',docTable(rows)+
    (next||state.cursors[page]?'<div class="bar">'+(state.cursors[page]?button('Newest','cursor','',0):'')+
      (next?button('Older records','cursor','',next):'')+'</div>':''));
}

async function inventory() {
  const history=await api('docs?'+new URLSearchParams({group:'stock',...range('inventory'),before:state.cursors.inventory||0}));
  return header('Inventory','Products, available stock and batch expiry.',button('＋ New product','product','primary'))+
    '<div class="bar">'+button('Opening stock','trade','','opening')+button('Add stock adjustment','trade','','gain')+button('Write off stock','trade','danger','loss')+
    label('Find by name, SKU or barcode','<input id="product-search" type="search" placeholder="Search catalog">','end')+'</div>'+
    section('Product catalog','<div id="catalog-table">'+productTable('')+'</div>')+
    section('In-stock batches',table(['Product','Batch','Expiry','Base units'],state.catalog.lots.map(x=>[
      escapeHTML(state.catalog.products.find(p=>p.id===x.product)?.name),escapeHTML(x.batch||'—'),escapeHTML(x.expiry||'—'),count(x.remaining)])))+
    filters('inventory')+section('Stock adjustment history',docTable(history.rows,false)+
      (history.next||state.cursors.inventory?'<div class="bar">'+(state.cursors.inventory?button('Newest','cursor','',0):'')+
      (history.next?button('Older records','cursor','',history.next):'')+'</div>':''));
}

function productTable(search) {
  const list=state.catalog.products.filter(x=>[x.name,x.sku,x.category,x.brand].join(' ').toLowerCase().includes(search.toLowerCase()));
  return table(['Product','Available','On hand','Reorder at','Value',''],list.map(x=>[
    '<strong>'+escapeHTML(x.name)+'</strong><small class="subline">'+escapeHTML([x.sku,x.category,x.brand,x.size].filter(Boolean).join(' · '))+
      (x.active?'':' · Inactive')+'</small>', '<span class="tag'+(x.sellable<=x.reorder?' warn':'')+'">'+count(x.sellable)+'</span>',
    count(x.quantity),count(x.reorder),money(x.valuation),button('Edit','product','',x.id)
  ]),'No matching products.');
}

const reportOptions=[['summary','Profit & loss'],['sales','Sales journal'],['products','Sales by product'],['purchases','Purchases by supplier'],
  ['expenses','Expense journal'],['stock','Inventory & movements'],['balances','Outstanding balances'],['payments','Payments by method'],['closing','Cash reconciliation']];
const headings={day:'Date',kind:'Type',username:'Staff member',base:'Total in LRD',cost_lrd:'Cost in LRD',sales_lrd:'Net sales excluding tax in LRD',
  amount_lrd:'Amount in LRD',outstanding_lrd:'Outstanding in LRD',number:'Document',due:'Due date',amount:'Amount',value:'Value in LRD',
  valuation:'Closing value in LRD',opening_money:'Opening book balance',closing_money:'Closing book balance'};
const moneyKeys=new Set(['total','outstanding','amount','value','valuation','actual','expected','difference','opening_money','closing_money','current_expected','base']);
function reportValue(key,row,formatted=true) {
  const value=row[key];
  if(value==null) return '';
  if(key.endsWith('_lrd')||moneyKeys.has(key)) return formatted?money(value,key.endsWith('_lrd')||['value','valuation','base'].includes(key)?'LRD':row.currency):decimal(value);
  if(['kind','channel'].includes(key)) return human(value);
  return value;
}
function keysFor(rows) {return Object.keys(rows[0]||{});}
function renderReport(report) {
  return report.sections.map(group=>{
    const keys=keysFor(group.rows);
    return section(group.title,table(keys.map(k=>headings[k]||human(k)),group.rows.map(row=>keys.map(k=>escapeHTML(reportValue(k,row))))));
  }).join('');
}

async function reports() {
  const view=state.reportView;
  const filter=state.reportFilters||{};
  const staff=(await api('staff')).map(x=>[x.id,x.username]);
  const products=state.catalog.products.map(x=>[x.id,x.name+' · '+x.sku]);
  const contactTypes=view==='sales'?'customer':view==='purchases'?'supplier':null;
  const contacts=state.catalog.contacts.filter(x=>!contactTypes||x.kind===contactTypes).map(x=>[x.id,x.name]);
  let fieldsHTML=select('view','Report',reportOptions,view);
  if(['sales','purchases','balances','payments','products'].includes(view)) fieldsHTML+=select('party','Contact',[['','All'],...contacts],filter.party);
  if(['sales','purchases','expenses','payments','products'].includes(view)) fieldsHTML+=select('user','Staff',[['','All'],...staff],filter.user);
  if(['sales','purchases','stock','products','payments'].includes(view)) fieldsHTML+=select('product','Product',[['','All'],...products],filter.product);
  state.reportData=await api('reports?'+new URLSearchParams({...range('reports'),view,...filter}));
  return header('Reports','Review business performance by period and export the results.',
    '<div class="table-actions">'+button('Export CSV','report-export')+button('Print / PDF','print')+'</div>')+
    filters('reports',fieldsHTML)+renderReport(state.reportData);
}

function exportReport() {
  const lines=[];
  const csv=value=>{
    let text=String(value??'');
    if(typeof value!=='number' && /^[\s]*[=+@-]/.test(text) && !/^-\d+(\.\d+)?$/.test(text)) text="'"+text;
    return '"'+text.replaceAll('"','""')+'"';
  };
  for(const item of state.reportData.sections) {
    const keys=keysFor(item.rows);
    lines.push(csv(item.title),keys.map(k=>csv(headings[k]||human(k))).join(','),
      ...item.rows.map(row=>keys.map(k=>csv(reportValue(k,row,false))).join(',')),'');
  }
  const href=URL.createObjectURL(new Blob(['\ufeff'+lines.join('\r\n')],{type:'text/csv;charset=utf-8'}));
  const link=document.createElement('a');
  link.href=href;link.download='drinkflow-'+state.reportView+'-'+range('reports').to+'.csv';
  link.click();
  setTimeout(()=>URL.revokeObjectURL(href),1000);
}

function contactsPage() {
  const buttons=(allowed('sales')||allowed('accounts')?button('＋ Customer','contact','primary','customer'):'')+
    (allowed('purchases')||allowed('accounts')?button('＋ Supplier','contact','','supplier'):'');
  return header('Contacts','Customer and supplier details.', '<div class="table-actions">'+buttons+'</div>')+
    section('Directory',table(['Name','Type','Category','Phone','Email',''],state.catalog.contacts.map(c=>[
      escapeHTML(c.name),human(c.kind),escapeHTML(c.category||'—'),escapeHTML(c.phone||'—'),escapeHTML(c.email||'—'),
      button('Edit','edit-contact','',c.id)+(allowed('reports')?' '+button('Statement','statement','',c.id):'')
    ])));
}

async function settingsPage() {
  const [staff,audit]=await Promise.all([api('staff'),api('audit')]);
  state.staff=staff;
  return header('Settings','Store information, user accounts and backups.')+
    section('Store details','<p>'+escapeHTML(state.me.settings.name||'DrinkFlow')+'</p><p>Default USD rate · '+
      escapeHTML(state.me.settings.rate ? 'LRD '+Number(state.me.settings.rate)/10000 : 'Set before your first USD sale')+'</p>'+button('Edit store details','store-settings'))+
    section('Staff accounts',table(['Username','Role','Status',''],staff.map(x=>[
      escapeHTML(x.username),escapeHTML(x.role),x.active?'Active':'Disabled',button('Edit','staff','',x.id)])),button('＋ Add user','staff','primary'))+
    section('Database backup','<p class="muted">A daily snapshot is kept on this server for 30 days.</p>'+
      '<a class="button" href="/api/backup" download>Download backup</a>')+
    section('Recent audit trail',table(['UTC timestamp','Staff','Event','Record'],audit.map(x=>[
      escapeHTML(x.created),escapeHTML(x.username||'System'),escapeHTML(x.event),escapeHTML(x.target)])));
}

function wirePage(page) {
  const canvas=$('#trend',main);
  if (canvas) drawTrend(canvas);
  const search=$('#product-search',main);
  if(search) search.addEventListener('input',()=>$('#catalog-table').innerHTML=productTable(search.value));
  const form=$('#filters',main);
  if(!form) return;
  form.elements.period.addEventListener('change',()=>applyPeriod(form));
  form.addEventListener('change',event=>{
    if(['from','to'].includes(event.target.name)) form.elements.period.value='';
    if(event.target.name==='view') {
      state.reportView=event.target.value;state.reportFilters={};
      const {from,to}=fields(form);state.dates.reports={from,to};
      refresh();
    }
  });
  listenForm(form,async current=>{
    const {from,to,party,user,product}=fields(current);
    state.dates[page]={from,to};state.cursors[page]=0;
    if(page==='reports') state.reportFilters=Object.fromEntries(Object.entries({party,user,product}).filter(([,v])=>v));
    await refresh();
  });
}

function productForm(product) {
  const p=product||{};
  const body='<div class="form-grid">'+input('name','Product name',p.name,'required maxlength="120"')+
    input('sku','SKU / barcode',p.sku,'required maxlength="120"')+input('category','Category',p.category,'maxlength="120"')+
    input('brand','Brand',p.brand,'maxlength="120"')+input('size','Size',p.size,'maxlength="120"')+
    input('reorder','Reorder point (base units)',p.reorder||0,'required min="0" step="1"','number')+
    textArea('description','Description',p.description,'maxlength="500"')+
    select('active','Status',[[1,'Active'],[0,'Inactive']],p.active??1)+'</div>'+
    '<fieldset><legend>Packaging and prices in LRD</legend><div id="units"></div>'+button('＋ Add package','add-unit')+'</fieldset>';
  formDialog(product?'Edit product':'New product',body,'Save product','products',form=>{
    const data=fields(form);
    const units=all('.entry',form).map(row=>({
      name:$('[name=unit_name]',row).value,factor:Number($('[name=factor]',row).value),
      retail:$('[name=retail]',row).value,wholesale:$('[name=wholesale]',row).value
    }));
    return {id:p.id,name:data.name,sku:data.sku,category:data.category,brand:data.brand,size:data.size,
      description:data.description,reorder:Number(data.reorder),active:Number(data.active),units};
  });
  const start=p.units||[{name:'Bottle',factor:1,retail:0,wholesale:0}];
  start.forEach((unit,i)=>appendUnit(unit,i===0));
}

function appendUnit(unit={},base=false) {
  const target=$('#units',content);
  if(!target||target.children.length>=12) return;
  target.insertAdjacentHTML('beforeend','<div class="entry">'+input('unit_name',base?'Base unit':'Package name',unit.name,'required maxlength="35"')+
    input('factor','Items per unit',unit.factor||2,'required min="1" max="10000" step="1" '+(base?'readonly':''),'number')+
    input('retail','Retail price',decimal(unit.retail),'required min="0" step="0.01"','number')+
    input('wholesale','Wholesale price',decimal(unit.wholesale),'required min="0" step="0.01"','number')+
    (base?'':button('Remove package','remove-entry','remove'))+'</div>');
}

function contactForm(kind,person) {
  const p=person||{};
  const body='<div class="form-grid">'+input('name','Name',p.name,'required maxlength="120"')+input('phone','Phone',p.phone,'maxlength="120"','tel')+
    input('email','Email',p.email,'maxlength="120"','email')+
    (kind==='customer'?select('category','Customer type',['Regular','Retail','Wholesale'],p.category||'Regular')+
      (allowed('accounts')?input('credit','Credit limit in LRD (0 = no limit)',decimal(p.credit),'min="0" step="0.01"','number'):''):
      input('category','Category',p.category,'maxlength="120"'))+'</div>';
  formDialog((person?'Edit ':'New ')+kind,body,'Save '+kind,'contacts',form=>({...fields(form),id:p.id,kind}));
}

const trades={sale:'New sale',purchase:'Receive stock',order:'Purchase order',expense:'Record expense',
  opening:'Opening stock',gain:'Add stock adjustment',loss:'Write off stock',fund_in:'Add funds',fund_out:'Withdraw funds'};
const goods=new Set(['sale','purchase','order','opening','gain','loss']);
const partyGoods=new Set(['sale','purchase','order']);
const costGoods=new Set(['purchase','order','opening','gain']);
const lotGoods=new Set(['purchase','opening','gain']);

function tradeForm(kind,order) {
  const hasGoods=goods.has(kind),isSale=kind==='sale',fulfill=kind==='purchase'&&order;
  const partyType=isSale?'customer':'supplier';
  const available=state.catalog.contacts.filter(p=>p.kind===partyType);
  const contacts=[['',isSale?'Walk-in':'Choose supplier'],...available.map(p=>[p.id,p.name])];
  if(hasGoods&&!state.catalog.products.some(p=>p.active||['gain','loss'].includes(kind))) {
    open(trades[kind],empty('Add a product to the catalog before entering this transaction.')+
      (allowed('stock')?button('Add product','product','primary'):''));
    return;
  }
  const rate=fulfill ? order.rate/10000 : (state.me.settings.rate ? Number(state.me.settings.rate)/10000 : '');
  let top=select('currency','Currency',['LRD','USD'],fulfill?order.currency:'LRD')+input('rate','LRD per USD',rate,'min="0.0001" step="0.0001"','number');
  if (isSale) top+=select('channel','Sales channel',[['retail','Retail'],['wholesale','Wholesale']],'retail');
  if(partyGoods.has(kind)) top+=select('party',isSale?'Customer':'Supplier',contacts,fulfill?order.party:'',isSale?'':'required');
  if (!hasGoods||kind==='order') top+=input('day','Business date',state.me.today,'required max="'+state.me.today+'"','date');
  if(kind==='purchase'||kind==='order') top+=input('reference','Supplier reference',fulfill?order.number:'','maxlength="120"');
  if(kind==='expense') top+=input('reference','Expense category','','required maxlength="120" list="expense-types"')+
    '<datalist id="expense-types">'+options(['Transport','Rent','Salaries','Electricity','Maintenance','Other'])+'</datalist>';
  if (!hasGoods) top+=input('amount','Amount','','required min="0.01" step="0.01"','number');
  const lineBody=hasGoods?'<fieldset><legend>Products</legend>'+input('scan','Scan or enter SKU','','placeholder="Enter barcode and press Enter"')+
    '<div id="trade-lines"></div>'+button('＋ Add line','add-line','',kind)+'</fieldset>':'';
  let bottom='';
  if(isSale) bottom+=input('discount','Invoice discount','0.00','min="0" step="0.01"','number')+input('tax','Tax amount','0.00','min="0" step="0.01"','number');
  if(kind==='purchase'||kind==='order') bottom+=input('freight','Transport cost',fulfill?decimal(order.total-order.lines.reduce((sum,line)=>sum+line.amount,0)):'0.00','min="0" step="0.01"','number');
  if(isSale||kind==='purchase') bottom+=select('payment_type','Payment status',[['full','Paid in full'],['partial','Part paid'],['credit','Pay later']],'full')+
    input('paid','Amount received or paid','0.00','min="0" step="0.01"','number')+input('due','Balance due date','','min="'+state.me.today+'"','date');
  if(!hasGoods||isSale||kind==='purchase') bottom+=select('method','Payment method',methods);
  bottom+=textArea('note','Notes / reason','','maxlength="500" '+(['gain','loss','fund_in','fund_out'].includes(kind)?'required':''));
  const showEstimate=hasGoods&&kind!=='loss';
  const body='<div class="form-grid">'+top+'</div>'+lineBody+'<div class="form-grid">'+bottom+'</div>'+
    (showEstimate?'<div class="sumline"><span>Calculated total</span><strong id="estimate"></strong></div>':'');
  const form=formDialog(trades[kind],body,kind==='order'?'Save order':'Post transaction','docs',current=>{
    const data=fields(current);
    const lines=all('#trade-lines .entry',current).map(row=>{
      const get=name=>$('[name='+name+']',row)?.value||'';
      return {product:Number(get('product')),unit:get('unit'),qty:Number(get('qty')),price:get('price')||'0',
        batch:get('batch'),expiry:get('expiry'),lot:Number(get('lot'))||null};
    });
    const paid=data.payment_type==='full'?decimal(estimation(current,kind)):data.payment_type==='partial'?data.paid||'0':'0';
    return {...data,kind,party:Number(data.party)||null,linked:fulfill?order.id:null,lines,paid,
      tax:data.tax||'0',discount:data.discount||'0',freight:data.freight||'0'};
  },async result=>{if(['sale','purchase','order'].includes(kind)) await loadDetail(result.id);});
  if(hasGoods) {
    if(fulfill) order.lines.forEach(line=>appendLine(kind,line));
    else appendLine(kind);
    form.elements.scan.addEventListener('keydown',event=>{
      if(event.key!=='Enter') return;
      event.preventDefault();
      const match=state.catalog.products.find(p=>p.sku.toLowerCase()===event.target.value.trim().toLowerCase()&&(p.active||['gain','loss'].includes(kind)));
      if (!match) {toast('No active product matches that barcode.');return;}
      const vacant=all('[name=product]',form).find(field=>!field.value);
      if (vacant) {vacant.value=match.id;vacant.dispatchEvent(new Event('change',{bubbles:true}));}
      else appendLine(kind,{product:match.id});
      event.target.value='';
      updateTrade(form,kind);
    });
  }
  if (kind==='purchase'&&!available.length) toast('Add a supplier on the Contacts page before receiving stock.');
  form.addEventListener('input',()=>updateTrade(form,kind));
  form.addEventListener('change',event=>{
    if(event.target.name==='product') fillLine(event.target.closest('.entry'),kind);
    updateTrade(form,kind);
  });
  updateTrade(form,kind);
}

function appendLine(kind,initial={}) {
  const container=$('#trade-lines',content);
  if(!container||container.children.length>=100) return;
  const choices=state.catalog.products.filter(x=>x.active||['gain','loss'].includes(kind)).map(x=>[x.id,x.name+' · '+x.sku]);
  container.insertAdjacentHTML('beforeend','<div class="entry">'+select('product','Product',[['','Choose product'],...choices],initial.product,'required','wide')+
    select('unit','Unit',[['','Choose unit']],'','required')+input('qty','Quantity',initial.qty||1,'required min="1" max="100000" step="1"','number')+
    (costGoods.has(kind)?input('price','Cost per selected unit',initial.price==null?'':decimal(initial.price),'required min="0" step="0.01"','number'):'')+
    (lotGoods.has(kind)?input('batch','Batch',initial.batch,'maxlength="60"')+input('expiry','Expiry',initial.expiry,'','date'):'')+
    (kind==='loss'?select('lot','Batch',[['','Automatically choose']]):'')+button('Remove line','remove-entry','remove',kind)+'</div>');
  fillLine(container.lastElementChild,kind,initial.unit);
  updateTrade(container.closest('form'),kind);
}

function fillLine(row,kind,initialUnit) {
  const p=state.catalog.products.find(x=>x.id===Number($('[name=product]',row).value));
  $('[name=unit]',row).innerHTML=options(p?p.units.map(u=>u.name):[['','Choose unit']],initialUnit);
  if(kind==='loss') $('[name=lot]',row).innerHTML=options([['','Automatically choose'],...state.catalog.lots.filter(x=>x.product===p?.id)
    .map(x=>[x.id,(x.batch||'Batch #'+x.id)+' · '+(x.expiry||'No expiry')+' · '+x.remaining+' units'])]);
}

function cents(value) {return Math.round(Number(value||0)*100);}
function estimation(form,kind) {
  const values=fields(form);
  const rate=values.currency==='USD'?Math.round(Number(values.rate||0)*10000):10000;
  let sum=0;
  for(const row of all('#trade-lines .entry',form)) {
    const p=state.catalog.products.find(x=>x.id===Number($('[name=product]',row).value));
    const unit=p?.units.find(u=>u.name===$('[name=unit]',row).value);
    const qty=Number($('[name=qty]',row).value)||0;
    const price=kind==='sale'&&rate&&unit?Math.round(unit[values.channel]*10000/rate):cents($('[name=price]',row)?.value);
    sum+=qty*price;
  }
  return sum+cents(values.tax)+cents(values.freight)-cents(values.discount);
}
function updateTrade(form,kind) {
  const e=form.elements;
  e.rate.closest('label').hidden=e.currency.value!=='USD';
  e.rate.required=e.currency.value==='USD';
  if(e.payment_type) {
    const option=e.payment_type.value;
    e.paid.closest('label').hidden=option!=='partial';
    e.due.closest('label').hidden=option==='full';
    e.due.required=option!=='full';
    e.method.closest('label').hidden=option==='credit';
  }
  const estimate=$('#estimate',form);
  if(estimate) estimate.textContent=money(estimation(form,kind),e.currency.value);
}

async function loadDetail(id) {
  const doc=await api('docs/'+id);
  state.document=doc;
  const invoices=['sale','purchase'].includes(doc.kind),actionable=invoices&&!doc.child;
  const reversible=(invoices||doc.kind==='expense')&&!doc.child;
  const lineTable=table(['Product','Unit','Quantity','Unit price','Line subtotal'],doc.lines.map(line=>[
    escapeHTML(line.label)+'<small class="subline">'+escapeHTML(line.sku)+'</small>',escapeHTML(line.unit),count(line.qty),
    money(line.price,doc.currency),money(line.amount,doc.currency)
  ]));
  const paymentTable=table(['Date','Method','Amount','Staff'],doc.payments.map(item=>[
    escapeHTML(item.day),escapeHTML(item.method),money(item.amount,doc.currency),escapeHTML(item.username)]));
  const receipt='<article class="receipt"><h3>'+escapeHTML(state.me.settings.name||'DrinkFlow')+'</h3>'+
    '<p><strong>'+escapeHTML(doc.number)+' · '+escapeHTML(human(doc.kind))+'</strong></p><div class="receipt-meta">'+
    '<span>'+escapeHTML(doc.day)+'</span><span>Staff · '+escapeHTML(doc.username)+'</span>'+
    (doc.contact?'<span>Contact · '+escapeHTML(doc.contact)+'</span>':'')+
    (doc.reference?'<span>Reference · '+escapeHTML(doc.reference)+'</span>':'')+
    (doc.channel?'<span>Channel · '+human(doc.channel)+'</span>':'')+
    (doc.currency==='USD'?'<span>Exchange rate · LRD '+escapeHTML(doc.rate/10000)+' per USD</span>':'')+'</div>'+
    (doc.lines.length?lineTable:'')+
    (doc.discount?'<p>Discount · '+money(doc.discount,doc.currency)+'</p>':'')+
    (doc.tax_entered?'<p>Tax · '+money(doc.tax_entered,doc.currency)+'</p>':'')+
    (doc.freight?'<p>Transport · '+money(doc.freight,doc.currency)+'</p>':'')+
    '<div class="sumline"><span>Total</span><strong>'+money(doc.total,doc.currency)+'</strong></div>'+
    (doc.outstanding?'<p>Outstanding · '+money(doc.outstanding,doc.currency)+' · Due '+escapeHTML(doc.due)+'</p>':'')+
    (reversible===false&&doc.child&&doc.kind!=='order'?'<p>Reversed by document #'+count(doc.child)+'</p>':'')+
    (doc.note?'<p>'+escapeHTML(doc.note)+'</p>':'')+(doc.payments.length?'<h3>Payment history</h3>'+paymentTable:'')+'</article>';
  let actions=button('Print / PDF','print');
  if(actionable&&doc.outstanding&&(allowed('accounts')||allowed(doc.kind==='sale'?'sales':'purchases'))) actions+=button('Record payment','payment','primary');
  if(reversible&&allowed('accounts')) actions+=button(doc.kind==='expense'?'Reverse expense':'Reverse invoice','reverse','danger');
  if(doc.kind==='order'&&!doc.child&&allowed('purchases')) actions+=button('Receive this order','receive','primary');
  open(doc.number,receipt+'<div class="dialog-actions">'+actions+'</div>');
}

function paymentForm(doc) {
  formDialog('Record payment for '+doc.number,'<p>Outstanding '+escapeHTML(money(doc.outstanding,doc.currency))+'</p><div class="form-grid">'+
    input('amount','Amount in '+doc.currency,decimal(doc.outstanding),'required min="0.01" max="'+decimal(doc.outstanding)+'" step="0.01"','number')+
    select('method','Payment method',methods)+input('day','Payment date',state.me.today,
      'required min="'+escapeHTML(doc.day)+'" max="'+state.me.today+'"','date')+'</div>',
    'Record payment','docs/'+doc.id+'/payment',fields,()=>loadDetail(doc.id));
}

function reverseForm(doc) {
  const isSale=doc.kind==='sale',isExpense=doc.kind==='expense';
  formDialog('Reverse '+doc.number,'<p>'+escapeHTML(isExpense?'This entry records the money returned to the store.':isSale?'Record a full return only when every item has been physically returned. All original units are restored to their batches.':'All quantities from the original purchase must still be available for a full return.')+
    '</p><div class="form-grid">'+textArea('note','Reason','','required maxlength="500"')+
    (doc.paid?select('method',(isSale?'Refund paid ':'Refund received ')+money(doc.paid,doc.currency)+' via',methods):'')+'</div>',
    (isExpense?'Post reversal':'Post full return'),'docs/'+doc.id+'/return',fields,result=>loadDetail(result.id));
}

function cashCount() {
  formDialog('Record closing count','<div class="form-grid">'+input('day','Closing date',state.me.today,'required max="'+state.me.today+'"','date')+
    select('method','Account',methods)+select('currency','Currency',['LRD','USD'],'LRD')+
    input('amount','Actual closing amount','','required min="0" step="0.01"','number')+textArea('note','Notes','','maxlength="500"')+
    '</div>','Save closing count','counts',fields);
}

function staffForm(person) {
  const p=person||{};
  formDialog(person?'Edit user':'Add user','<div class="form-grid">'+
    input('username','Username',p.username,'required minlength="3" maxlength="64" autocomplete="off"')+select('role','Role',roles,p.role||'Cashier')+
    input('password',person?'New password (optional)':'Password','',(person?'':'required ')+'minlength="12" maxlength="128" autocomplete="new-password"','password')+
    (person?select('active','Status',[[1,'Active'],[0,'Disabled']],p.active):'')+'</div>','Save user','users',form=>{
    const data=fields(form);
    return {...data,id:p.id,active:Number(data.active??1)};
  });
}

function settingsForm() {
  const store=state.me.settings;
  formDialog('Store details','<div class="form-grid">'+input('name','Store name',store.name||'DrinkFlow','required maxlength="80"')+
    input('rate','Default LRD per USD',store.rate?store.rate/10000:'','required min="0.0001" step="0.0001"','number')+
    '</div>','Save store details','settings',fields,async()=>{state.me=await api('me');paintIdentity();await refresh();});
}

const actions={
  close:()=>dialog.close(),
  reload:()=>refresh(),
  logout:async()=>{await api('logout',{});loginScreen();},
  detail:id=>loadDetail(id),
  product:id=>productForm(state.catalog.products.find(p=>p.id===Number(id))),
  'add-unit':()=>appendUnit(),
  trade:id=>tradeForm(id),
  'add-line':id=>appendLine(id),
  'remove-entry':(kind,buttonEl)=>{
    const row=buttonEl.closest('.entry');
    const form=row.closest('form');
    row.remove();
    if(kind) updateTrade(form,kind);
  },
  contact:kind=>contactForm(kind),
  'edit-contact':id=>{const person=state.catalog.contacts.find(x=>x.id===Number(id));if(person) contactForm(person.kind,person);},
  statement:async id=>{
    const person=state.catalog.contacts.find(x=>x.id===Number(id));
    if (!person) return;
    const statement=await api('statement?'+new URLSearchParams({party:id}));
    open(person.name+' statement','<div class="receipt">'+
      section('Invoices and receipts',docTable(statement.docs))+
      section('Payments',table(['Date','Document','Type','Method','Amount'],statement.payments.map(x=>[
        escapeHTML(x.day),docLink(x.number,x.doc),human(x.kind),escapeHTML(x.method),money(x.amount,x.currency)])))+
      '</div><div class="dialog-actions">'+button('Print / PDF','print')+'</div>');
  },
  cursor:(id)=>{state.cursors[state.page]=Number(id);return refresh();},
  'report-export':exportReport,
  print:()=>window.print(),
  payment:()=>paymentForm(state.document),
  reverse:()=>reverseForm(state.document),
  receive:()=>tradeForm('purchase',state.document),
  'cash-count':cashCount,
  staff:id=>staffForm(state.staff.find(x=>x.id===Number(id))),
  'store-settings':settingsForm
};

document.addEventListener('click',async event=>{
  const trigger=event.target.closest('[data-action]');
  if(!trigger||!actions[trigger.dataset.action]) return;
  try {await actions[trigger.dataset.action](trigger.dataset.id,trigger);}
  catch(error) {toast(error.message);}
});
dialog.addEventListener('close',()=>{if(!dialog.open) document.body.classList.remove('receipt-mode');});
dialog.addEventListener('cancel',event=>{if($('form[data-saving]',dialog)) event.preventDefault();});
window.addEventListener('hashchange',navigate);
window.addEventListener('resize',()=>{const canvas=$('#trend');if(canvas) drawTrend(canvas);});
start();
