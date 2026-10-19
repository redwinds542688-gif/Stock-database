import json, sys, re, subprocess
sys.path.insert(0,'/home/user/Stock-database')
src=open('/home/user/Stock-database/make_pool.py',encoding='utf-8').read()
# 只取函式定義段(到 TODAY8 之前),避免執行抓取
head=src.split('TODAY8 = ')[0]
ns={}; exec(head, ns)
normalize=ns['normalize']; roc_to_iso=ns['roc_to_iso']; num=ns['num']; is_stock_code=ns['is_stock_code']
D='<SIM_DIR>/dl/lu/'
def twse_rows(j, ymd):
    for t in j.get('tables',[]):
        f=[str(x).strip() for x in (t.get('fields') or [])]
        if '證券代號' not in f or not t.get('data'): continue
        ix={k:i for i,k in enumerate(f)}; out=[]
        for r in t['data']:
            sh=str(r[ix['漲跌(+/-)']]); sign=-1 if '-' in sh else (1 if '+' in sh else 0); chg=num(r[ix['漲跌價差']])
            out.append({"Code":r[ix['證券代號']],"Name":r[ix['證券名稱']],"TradeVolume":r[ix['成交股數']],"TradeValue":r[ix['成交金額']],"HighestPrice":r[ix['最高價']],"LowestPrice":r[ix['最低價']],"ClosingPrice":r[ix['收盤價']],"Change":(sign*chg) if chg is not None else "","Date":ymd})
        return out
    return []
def tpex_rows(j):
    t=(j.get('tables') or [{}])[0]; f=[str(x).strip() for x in (t.get('fields') or [])]; ix={k:i for i,k in enumerate(f)}
    return [{"Code":r[ix['代號']],"Name":r[ix['名稱']],"Close":r[ix['收盤']],"Change":r[ix['漲跌']],"High":r[ix['最高']],"Low":r[ix['最低']],"TradingShares":r[ix['成交股數']],"TransactionAmount":r[ix['成交金額(元)']],"Date":str(j.get('date') or '').replace('/','')} for r in t['data']]
def field(r,*needles):
    for k,v in r.items():
        if any(n.lower() in k.lower() for n in needles): return str(v or '').strip()
    return ''
def is_flag(v): return str(v or '').strip() in ("Y","y","*","＊","V","v","1","是","暫停")
elig={}
for r in json.load(open(D+'twse_dt.json')):
    c=field(r,'Code','代號'); 
    if is_stock_code(c): elig[c]=(1, 0 if is_flag(field(r,'Suspension','Suspend','Pause','暫停','先賣後買','Remark','註記')) else 1)
for r in json.load(open(D+'tpex_dt.json')):
    c=field(r,'Code','代號')
    if is_stock_code(c): elig[c]=(1, 0 if is_flag(field(r,'Suspension','Suspend','Pause','暫停','先賣後買','Remark','註記')) else 1)
for r in json.load(open(D+'tpex_pause.json')):
    c=field(r,'Code','代號')
    if c in elig: elig[c]=(1,0)
bad=set()
for n in ['twse_notice','twse_punish','tpex_notice','tpex_punish']:
    for r in json.load(open(D+n+'.json')):
        c=field(r,'Code','代號')
        if is_stock_code(c): bad.add(c)
ind={}
for n in ['twse_info','tpex_info']:
    for r in json.load(open(D+n+'.json')):
        c=field(r,'公司代號','SecuritiesCompanyCode','Code'); i=field(r,'產業別','IndustryCode','Industry')
        if c and i: ind[c]=i.zfill(2)
hist=json.load(open('/home/user/Stock-database/amp_history.json',encoding='utf-8'))
LO,HI,MIN_AMP,TICK_MAX=15,500,4.0,0.35
pools={'20261012':'2026-10-13','20261013':'2026-10-14','20261014':'2026-10-15','20261015':'2026-10-16','20261016':'2026-10-19'}
S='<SIM_DIR>/'
res={}
for p,day in pools.items():
    rows=normalize(twse_rows(json.load(open(D+f'twse_{p}.json')),p),'twse')+normalize(tpex_rows(json.load(open(D+f'tpex_{p}.json'))),'tpex')
    P=p[:4]+'-'+p[4:6]+'-'+p[6:]
    # 當天實際池(回測用,監控高價 350)
    pf=S+('pool_morning.json' if day=='2026-10-19' else f'hi350_{day}.json'); AP=json.load(open(pf,encoding='utf-8'))['pool']
    inpool={s['code'] for s in AP}; minlots=min(s['lots'] for s in AP)
    lu=[]
    for r in rows:
        if not r['limit_hit']: continue
        up,dn=ns['limit_prices'](r['prev'])
        if r['close']<=dn: continue              # 跌停照擋
        i=ind.get(r['code']); fin=(i in {'17'}) if i is not None else (r['code'].startswith('28'))
        if fin or not (LO<=r['close']<=HI) or r['amp']<MIN_AMP or r['tick_pct']>TICK_MAX: continue
        if r['code'] not in elig or r['code'] in bad: continue
        h=hist.get(r['code'],{}); ds=sorted(d for d in h if d<=P)[-5:]
        if len(ds)<3: continue
        a5=sum(h[d] for d in ds)/len(ds); hits=sum(1 for d in ds if h[d]>=MIN_AMP)
        if a5<4.0 or hits<3: continue
        if r['lots']<minlots: continue
        if r['close']>350: continue               # App 監控高價 350
        r['amp5']=round(a5,2); r['short_first']=elig[r['code']][1]; r['day']=day
        lu.append(r)
    res[day]=lu
    print(day,'候選(昨收漲停,其他都過):',[(x['code'],x['name'],x['close'],x['lots'],'先賣'+str(x['short_first'])) for x in lu])
json.dump(res,open(D+'cands.json','w'),ensure_ascii=False)
