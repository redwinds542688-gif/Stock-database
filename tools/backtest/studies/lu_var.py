import os
import json, os, re, subprocess, sys
exec(open('lu_why.py',encoding='utf-8').read().split("rows.sort(")[0])
exec(open('lu_run.py',encoding='utf-8').read().split("tot={'一'")[0].replace("cands=json.load(open(D+'cands.json'))",""))
exec("J1="+repr(re.search(r'J1="([^"]+)"',open('lu_run.py').read()).group(1))); exec("J2="+repr(re.search(r'J2="([^"]+)"',open('lu_run.py').read()).group(1)))
VAR=sys.argv[1]
S='<SIM_DIR>/'
pools={'20261012':'2026-10-13','20261013':'2026-10-14','20261014':'2026-10-15','20261015':'2026-10-16','20261016':'2026-10-19'}
def ok(r,P,day,var):
    c=r['code']; i=ind.get(c); pd_=json.load(open(D+f'poolday_{day}.json',encoding='utf-8'))
    att=set(pd_.get('attention',[])); dis=set(pd_.get('disposed',[]))
    if ((i=='17') if i is not None else c.startswith('28')): return False
    if not (15<=r['close']<=500) or r['amp']<4: return False
    if r['tick_pct']>({'tick':0.5,'tick04':0.4,'tick045':0.45}.get(var,0.35)): return False
    up,dn=ns['limit_prices'](r['prev'])
    if r['close']<=dn: return False
    if c not in elig or c in dis: return False
    if c in att and var!='att': return False
    h=hist.get(c,{}); ds=sorted(d for d in h if d<=P)[-5:]
    if len(ds)<3: return False
    a5=sum(h[d] for d in ds)/len(ds); hits=sum(1 for d in ds if h[d]>=4)
    if a5<(3.5 if var=='avg35' else 4): return False
    if hits<(2 if var=='hits2' else 3): return False
    return True
def load_rp(f):
    out=set()
    for l in open(S+f,encoding='utf-8'):
        m=re.match(r'(\d\d-\d\d) (\S+) (做[多空] \d\d:\d\d)',l.strip())
        if m: out.add((m.group(1),m.group(2),m.group(3)))
    return out

def mkpool(day, pf, added, tag):
    """模擬器要從 POOL 拿昨收:把新增的股票(昨收 = 前一交易日收盤)補進當天的池檔"""
    P=json.load(open(pf,encoding='utf-8')); have={s['code'] for s in P['pool']}
    for r in added:
        if r['code'] in have: continue
        P['pool'].append({'code':r['code'],'name':r['name'],'close':r['close'],'date':r['date'],'prev':r['prev'],'lots':r['lots'],'amp':r['amp'],'market':r['market']})
    out=D+f'pool_{day}_{tag}.json'; json.dump(P,open(out,'w',encoding='utf-8'),ensure_ascii=False); return out
RP={'一':load_rp('rp_j1.txt'),'二':load_rp('rp_j2.txt')}
tr={'一':[], '二':[]}
for p,day in pools.items():
    rows=normalize(twse_rows(json.load(open(D+f'twse_{p}.json')),p),'twse')+normalize(tpex_rows(json.load(open(D+f'tpex_{p}.json'))),'tpex')
    P=p[:4]+'-'+p[4:6]+'-'+p[6:]
    cb={r['code']:r for r in sorted([r for r in rows if ok(r,P,day,'base')], key=lambda r:-r['lots'])[:130] if r['close']<=350}
    cv={r['code']:r for r in sorted([r for r in rows if ok(r,P,day,VAR)], key=lambda r:-r['lots'])[:130] if r['close']<=350}
    added=[cv[c] for c in cv if c not in cb]
    print(f"{day}: 新增 {len(added)} 檔:",' '.join(f"{r['name']}({r['close']},{r['lots']//1000}k)" for r in added))
    pf=mkpool(day, S+('pool_morning.json' if day=='2026-10-19' else f'hi350_{day}.json'), added, VAR)
    for r in added:
        code=r['code']; name=r['name']; ds=ensure(code)
        if day not in ds: print('   ',name,'無當天分K'); continue
        for rs,fl in (('一',J1),('二',J2)):
            env=dict(os.environ, DAY=day, POOL=pf); env.update(dict(kv.split('=',1) for kv in fl.split()))
            out=subprocess.run(['python3','-I','sim_tmp.py',code,name,'10','10'],capture_output=True,text=True,env=env).stdout
            for l in out.splitlines():
                if l.startswith(('做多','做空')):
                    m=re.search(r'淨 ([+\-−]?[\d,]+)', l); v=int(m.group(1).replace('−','-').replace(',','')) if m else 0
                    key=(day[5:],name,l[:8])
                    if key in RP[rs]: continue
                    tr[rs].append((day[5:],name,l.split('  價差')[0],v))
base={'一':(20,18,78662),'二':(21,19,93044)}
for rs in ('一','二'):
    A=tr[rs]; n0,w0,net0=base[rs]; n=n0+len(A); w=w0+sum(1 for x in A if x[3]>0); net=net0+sum(x[3] for x in A)
    print(f"=== {VAR} 判{rs}:多 {len(A)} 筆 {sum(1 for x in A if x[3]>0)} 賺 {sum(1 for x in A if x[3]<=0)} 賠 淨 {sum(x[3] for x in A):+,} → 五天 {n} 筆 {w}/{n-w} {w*100//n}% {net:+,}")
    for x in A: print('   +',x[0],x[1],x[2],f'淨 {x[3]:+,}')
