import os
import json, subprocess, re, os, sys
exec(open('sig12.py',encoding='utf-8').read().split('res=[]')[0])
EXTRA=sys.argv[1] if len(sys.argv)>1 else ''
res=[]
for d,pool in DAYS.items():
    P=json.load(open(pool,encoding='utf-8'))
    for s in P['pool']:
        env=dict(os.environ, DAY=d, POOL=pool); env.update(dict(kv.split('=',1) for kv in (J1+' '+EXTRA).split()))
        out=subprocess.run(['python3','-I',f'{S}/sim_day.py',s['code'],s.get('name',s['code']),'10','10'],capture_output=True,text=True,env=env).stdout
        B=bars(s['code'],d)
        if not B: continue
        lines=out.splitlines()
        trades=[]
        for l in lines:
            m2=re.match(r'(做多|做空) (\d\d:\d\d) 進 ([\d.]+) ×\d+張 → (\d\d:\d\d) 出 ([\d.]+) \[(.*?)\].*淨 ([+\-−]?[\d,]+)', l)
            if m2: trades.append({'side':1 if m2.group(1)=='做多' else -1,'t':m2.group(2),'p':float(m2.group(3)),'x':m2.group(4),'why':m2.group(6),'net':int(m2.group(7).replace('−','-').replace(',',''))})
        for side in (1,-1):
            tr=[t for t in trades if t['side']==side]
            if not tr: continue
            t1=tr[0]
            # 第一筆平倉後,下一個「乾淨」的同方向確認點(沒有任何 [ ] 擋住的標記),或下一筆實際進場
            cand=None
            for l in lines:
                m=re.match(r'\s+(\d\d:\d\d) 強?(多|空)✓ 價 ([\d.]+)', l)
                if not m or (m.group(2)=='多')!=(side==1): continue
                if m.group(1)<=t1['x']: continue
                pass  # 第二次訊號不管有沒有被判一的保護擋住都算(要看的是價位高低的規律)
                cand=(m.group(1), float(m.group(3)), '→ 進場' in l); break
            if not cand: continue
            t2,p2,entered=cand
            res.append({'day':d[5:],'name':s.get('name'),'side':'多' if side==1 else '空','t1':t1['t'],'p1':t1['p'],'x1':t1['x'],'why1':t1['why'],'net1':t1['net'],'t2':t2,'p2':p2,'entered2':entered,'better':(p2>t1['p']) if side==1 else (p2<t1['p']),'o2':outcome(B,t2,side)})
json.dump(res,open(f'{S}/sig12b.json','w'),ensure_ascii=False)
def summ(label, rows):
    if not rows: print(label,'0 筆'); return
    tp=[r for r in rows if r['o2']>=2.5]; sl=[r for r in rows if r['o2']<=-1.5]; w=[r for r in rows if r['o2']>0]
    print(f"{label}: {len(rows)} 筆 | 第二次之後:到停利 {len(tp)}、先停損 {len(sl)}、其他 {len(rows)-len(tp)-len(sl)} | 正報酬 {len(w)} ({len(w)*100//len(rows)}%) 平均 {sum(r['o2'] for r in rows)/len(rows):+.2f}%")
for side in ('多','空'):
    R=[r for r in res if r['side']==side]; print(f'=== {side}(第一筆為實際進場並平倉)')
    summ('  第二次價位比第一次進場價更有利', [r for r in R if r['better']])
    summ('  第二次價位較差', [r for r in R if not r['better']])
print('--- 多單明細(第一筆進場價 → 第二次訊號價;第一筆結果;第二次之後)')
for r in sorted([r for r in res if r['side']=='多'],key=lambda r:(r['better'],r['o2'])): print(f"  {r['day']} {r['name']:6} 第一 {r['t1']} {r['p1']} {r['why1'][:5]} {r['net1']:+,} → 第二 {r['t2']} {r['p2']} {'更高' if r['better'] else '較低'} 之後 {r['o2']:+.2f}% {'(有進場)' if r['entered2'] else ''}")
