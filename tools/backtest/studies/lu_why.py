import json, sys
src=open('/home/user/Stock-database/make_pool.py',encoding='utf-8').read(); ns={}; exec(src.split('TODAY8 = ')[0], ns)
normalize=ns['normalize']; num=ns['num']; is_stock_code=ns['is_stock_code']
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
p='20261019'
rows=normalize(twse_rows(json.load(open(D+f'twse_{p}.json')),p),'twse')+normalize(tpex_rows(json.load(open(D+f'tpex_{p}.json'))),'tpex')
elig={}
for n in ['twse_dt','tpex_dt']:
    for r in json.load(open(D+n+'.json')):
        c=field(r,'Code','代號')
        if is_stock_code(c): elig[c]=1
bad={}
for n,lab in [('twse_notice','注意股'),('twse_punish','處置股'),('tpex_notice','注意股'),('tpex_punish','處置股')]:
    for r in json.load(open(D+n+'.json')):
        c=field(r,'Code','代號')
        if is_stock_code(c): bad[c]=lab
ind={}
for n in ['twse_info','tpex_info']:
    for r in json.load(open(D+n+'.json')):
        c=field(r,'公司代號','SecuritiesCompanyCode','Code'); i=field(r,'產業別','IndustryCode','Industry')
        if c and i: ind[c]=i.zfill(2)
hist=json.load(open('/home/user/Stock-database/amp_history.json',encoding='utf-8'))
pool={s['code'] for s in json.load(open('/home/user/Stock-database/pool.json',encoding='utf-8'))['pool']}
def why(r):
    c=r['code']; i=ind.get(c)
    if (i=='17') if i is not None else c.startswith('28'): return '金融股'
    if not (15<=r['close']<=500): return f'價格 {r["close"]} 不在 15~500'
    if r['amp']<4: return f'今日振幅 {r["amp"]}% < 4%'
    if r['tick_pct']>0.35: return f'tick 成本 {r["tick_pct"]}% > 0.35%'
    if r['limit_hit']: return '收跌停'
    if c not in elig: return '不在官方可現股當沖名單'
    if c in bad: return bad[c]
    h=hist.get(c,{}); ds=sorted(h)[-5:]; a5=sum(h[d] for d in ds)/max(1,len(ds)); hits=sum(1 for d in ds if h[d]>=4)
    if a5<4: return f'5 日均振幅 {a5:.2f}% < 4%(5 天:{", ".join(str(h[d]) for d in ds)})'
    if hits<3: return f'5 天只有 {hits} 天振幅 ≥ 4%(5 天:{", ".join(str(h[d]) for d in ds)})'
    return '(規則都過,但排名在 130 名外)'
rows.sort(key=lambda r:-r['lots'])
print('今天成交張數前 40 名:')
for r in rows[:40]:
    tag='✅ 在名單' if r['code'] in pool else '❌ '+why(r)
    print(f"{r['lots']:>8,} 張  {r['name']:<6} {r['code']}  收 {r['close']:<7} 振幅 {r['amp']:<5}%  {tag}")
