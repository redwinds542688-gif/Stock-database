import os
import json, subprocess, re, os, csv, glob, collections
S=os.environ.get('SIM_DIR', os.path.dirname(os.path.abspath(__file__)))
J1="OBMASLOPE=4 FPGAP=2.5 TAKE=2.5 VWAPF=1 FP=1 MAXDEV=1 MIRROR=1 PENDVOL=1 VOLMIN=6000 RVOLMIN=0.9 CAP=350000 REVOK=1 ENTRYMAX=3.5 OBVX=2 OBVXMIN=1 OBVX2=1 OB2=30 SLOPEMIN=-99 GAPMODE=dir GAPTH=2 OB3MA=6 REDSLOPE=-2 EXITRESET=tp OBVDIR=30 NEARHL=2"
DAYS={'2026-10-06':f'{S}/pools/hist_2026-10-06.json','2026-10-07':f'{S}/pools/hist_2026-10-07.json','2026-10-08':f'{S}/pools/hist_2026-10-08.json','2026-10-12':f'{S}/pools/hist_2026-10-12.json','2026-10-13':f'{S}/pools/hi350_2026-10-13.json','2026-10-14':f'{S}/pools/hi350_2026-10-14.json','2026-10-15':f'{S}/pools/hi350_2026-10-15.json','2026-10-16':f'{S}/pools/hi350_2026-10-16.json','2026-10-19':f'{S}/pools/pool_morning.json'}
def bars(code,d):
    out=[]
    for f in sorted(glob.glob(f'/home/user/Stock-database/kline/{code}/2026-W4*.csv')):
        for r in csv.DictReader(open(f,encoding='utf-8')):
            if r['date']==d: out.append((r['time'],float(r['open']),float(r['high']),float(r['low']),float(r['close'])))
    return out
def outcome(B, t, side, tp=2.5, sl=1.5):
    """訊號後下一根開盤進,之後先碰 +tp 還是 −sl;都沒碰 → 13:25 收盤價差"""
    idx=[i for i,b in enumerate(B) if b[0]>t]
    if not idx: return None
    i0=idx[0]; ent=B[i0][1]
    for b in B[i0:]:
        hi=(b[2]-ent)/ent*100*side; lo=(b[3]-ent)/ent*100*side
        if lo<=-sl: return -sl
        if hi>=tp: return tp
    return (B[-1][4]-ent)/ent*100*side
res=[]
for d,pool in DAYS.items():
    P=json.load(open(pool,encoding='utf-8'))
    for s in P['pool']:
        env=dict(os.environ, DAY=d, POOL=pool); env.update(dict(kv.split('=',1) for kv in J1.split()))
        out=subprocess.run(['python3','-I',f'{S}/sim_day.py',s['code'],s.get('name',s['code']),'10','10'],capture_output=True,text=True,env=env).stdout
        B=bars(s['code'],d)
        if not B: continue
        sig={1:[],-1:[]}; trades=[]
        for l in out.splitlines():
            m=re.match(r'\s+(\d\d:\d\d) (強?)(多|空)✓(✕?) 價 ([\d.]+)', l)
            if m and not m.group(4): sig[1 if m.group(3)=='多' else -1].append((m.group(1), float(m.group(5)), '只記錄' in l or '不足' in l or '反向' in l or '鎖住' in l))
            m2=re.match(r'(做多|做空) (\d\d:\d\d) 進 ([\d.]+) ×\d+張 → (\d\d:\d\d) 出', l)
            if m2: trades.append((1 if m2.group(1)=='做多' else -1, m2.group(2), float(m2.group(3)), m2.group(4)))
        for side in (1,-1):
            L=sig[side]
            if len(L)<2: continue
            t1,p1,_=L[0]
            # 第一筆同方向交易的出場時間(若有)
            tr=[t for t in trades if t[0]==side]
            close1=tr[0][3] if tr and tr[0][1]>=t1 else None
            for t2,p2,_ in L[1:]:
                if (t2<=t1): continue
                if close1 and t2<=close1: continue   # 第一筆還沒平倉
                hm=lambda t:int(t[:2])*60+int(t[3:])
                if hm(t2)-hm(t1)<10: continue
                o2=outcome(B,t2,side)
                if o2 is None: continue
                res.append({'day':d[5:],'name':s.get('name'),'side':'多' if side==1 else '空','t1':t1,'p1':p1,'t2':t2,'p2':p2,'higher':(p2>p1) if side==1 else (p2<p1),'after_close':bool(close1),'o2':o2})
                break   # 只看第一對
json.dump(res,open(f'{S}/sig12.json','w'),ensure_ascii=False)
def summ(label, rows):
    if not rows: print(label,'0 筆'); return
    w=[r for r in rows if r['o2']>0]; tp=[r for r in rows if r['o2']>=2.5]; sl=[r for r in rows if r['o2']<=-1.5]
    print(f"{label}: {len(rows)} 筆, 第二訊號之後:到停利 {len(tp)} 先停損 {len(sl)} 其他 {len(rows)-len(tp)-len(sl)}, 正報酬 {len(w)} ({len(w)*100//len(rows)}%), 平均 {sum(r['o2'] for r in rows)/len(rows):+.2f}%")
for side in ('多','空'):
    R=[r for r in res if r['side']==side]
    print(f'=== {side}')
    summ('  第二訊號價位更有利(多:更高/空:更低)', [r for r in R if r['higher']])
    summ('  第二訊號價位較差', [r for r in R if not r['higher']])
    summ('  (第一筆有進場且已平倉)更有利', [r for r in R if r['higher'] and r['after_close']])
    summ('  (第一筆有進場且已平倉)較差', [r for r in R if not r['higher'] and r['after_close']])
