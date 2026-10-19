import os
import json, os, re, subprocess, sys, time
exec(open('lu_why.py',encoding='utf-8').read().split("rows.sort(")[0])   # 載入 normalize/twse_rows/tpex_rows/elig/bad/ind/hist
exec(open('lu_run.py',encoding='utf-8').read().split("tot={'一'")[0].replace("cands=json.load(open(D+'cands.json'))",""))   # yahoo/ensure/week 等
exec("J1="+repr(re.search(r'J1="([^"]+)"',open('lu_run.py').read()).group(1))); exec("J2="+repr(re.search(r'J2="([^"]+)"',open('lu_run.py').read()).group(1)))
AMP=float(sys.argv[1]) if len(sys.argv)>1 else 3.0
S='<SIM_DIR>/'
pools={'20261012':'2026-10-13','20261013':'2026-10-14','20261014':'2026-10-15','20261015':'2026-10-16','20261016':'2026-10-19'}
def ok(r,P,AMP):
    c=r['code']; i=ind.get(c)
    if ((i=='17') if i is not None else c.startswith('28')): return False
    if not (15<=r['close']<=500) or r['amp']<AMP or r['tick_pct']>0.35: return False
    up,dn=ns['limit_prices'](r['prev'])
    if r['close']<=dn: return False
    if c not in elig or c in bad: return False
    h=hist.get(c,{}); ds=sorted(d for d in h if d<=P)[-5:]
    if len(ds)<3: return False
    a5=sum(h[d] for d in ds)/len(ds); hits=sum(1 for d in ds if h[d]>=AMP)
    return a5>=AMP and hits>=3
# 既有五天回測的每筆(判一 rp_j1.txt、判二 rp_j2.txt)
def load_rp(f):
    out={}
    for l in open(S+f,encoding='utf-8'):
        m=re.match(r'(\d\d-\d\d) (\S+) (做[多空] .*?) 淨 ([+\-−]?[\d,]+)',l.strip())
        if m: out.setdefault((m.group(1),m.group(2)),[]).append((m.group(3),int(m.group(4).replace('−','-').replace(',',''))))
    return out
RP={'一':load_rp('rp_j1.txt'),'二':load_rp('rp_j2.txt')}

def mkpool(day, pf, added, tag):
    """模擬器要從 POOL 拿昨收:把新增的股票(昨收 = 前一交易日收盤)補進當天的池檔"""
    P=json.load(open(pf,encoding='utf-8')); have={s['code'] for s in P['pool']}
    for r in added:
        if r['code'] in have: continue
        P['pool'].append({'code':r['code'],'name':r['name'],'close':r['close'],'date':r['date'],'prev':r['prev'],'lots':r['lots'],'amp':r['amp'],'market':r['market']})
    out=D+f'pool_{day}_{tag}.json'; json.dump(P,open(out,'w',encoding='utf-8'),ensure_ascii=False); return out
added_tr={'一':[], '二':[]}; dropped_tr={'一':[], '二':[]}
def run(code,name,day,pf,bucket):
    ds=ensure(code)
    if day not in ds: print('   ',name,'無當天分K'); return
    for rs,fl in (('一',J1),('二',J2)):
        env=dict(os.environ, DAY=day, POOL=pf); env.update(dict(kv.split('=',1) for kv in fl.split()))
        out=subprocess.run(['python3','-I','sim_tmp.py',code,name,'10','10'],capture_output=True,text=True,env=env).stdout
        for l in out.splitlines():
            if l.startswith(('做多','做空')):
                m=re.search(r'淨 ([+\-−]?[\d,]+)', l); v=int(m.group(1).replace('−','-').replace(',','')) if m else 0
                bucket[rs].append((day[5:],name,l.split('  價差')[0],v))
for p,day in pools.items():
    rows=normalize(twse_rows(json.load(open(D+f'twse_{p}.json')),p),'twse')+normalize(tpex_rows(json.load(open(D+f'tpex_{p}.json'))),'tpex')
    P=p[:4]+'-'+p[4:6]+'-'+p[6:]
    c4={r['code']:r for r in sorted([r for r in rows if ok(r,P,4.0)], key=lambda r:-r['lots'])[:130] if r['close']<=350}
    c3={r['code']:r for r in sorted([r for r in rows if ok(r,P,AMP)], key=lambda r:-r['lots'])[:130] if r['close']<=350}
    added=[c3[c] for c in c3 if c not in c4]; dropped=[c4[c] for c in c4 if c not in c3]
    print(f"{day}: 4% 池 {len(c4)} 檔 → {AMP}% 池 {len(c3)} 檔;新增 {len(added)} 檔、剔除 {len(dropped)} 檔")
    print('  新增:',' '.join(f"{r['name']}({r['amp']}%,{r['lots']//1000}k)" for r in added))
    print('  剔除:',' '.join(f"{r['name']}({r['amp']}%,{r['lots']//1000}k)" for r in dropped))
    pf=mkpool(day, S+('pool_morning.json' if day=='2026-10-19' else f'hi350_{day}.json'), added+dropped, 'amp')
    for r in added: run(r['code'],r['name'],day,pf,added_tr)
    for r in dropped: run(r['code'],r['name'],day,pf,dropped_tr)
for rs in ('一','二'):
    A=added_tr[rs]; Dd=dropped_tr[rs]
    print(f"\n=== 判{rs}:{AMP}% 新收進的股票 {len(A)} 筆 {sum(1 for x in A if x[3]>0)} 賺 {sum(1 for x in A if x[3]<=0)} 賠 淨 {sum(x[3] for x in A):+,};被擠掉的股票 {len(Dd)} 筆 {sum(1 for x in Dd if x[3]>0)} 賺 {sum(1 for x in Dd if x[3]<=0)} 賠 淨 {sum(x[3] for x in Dd):+,}")
    for x in A: print('   +',x[0],x[1],x[2],f'淨 {x[3]:+,}')
    for x in Dd: print('   -',x[0],x[1],x[2],f'淨 {x[3]:+,}')
