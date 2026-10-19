import os
import json, subprocess, re, os, csv, glob, collections
S=os.environ.get('SIM_DIR', os.path.dirname(os.path.abspath(__file__)))
J1="OBMASLOPE=4 FPGAP=2.5 TAKE=2.5 VWAPF=1 FP=1 MAXDEV=1 MIRROR=1 PENDVOL=1 VOLMIN=6000 RVOLMIN=0.9 CAP=350000 REVOK=1 ENTRYMAX=3.5 OBVX=2 OBVXMIN=1 OBVX2=1 OB2=30 SLOPEMIN=-99 GAPMODE=dir GAPTH=2 OB3MA=6 REDSLOPE=-2 EXITRESET=tp OBVDIR=30 NEARHL=2 TRACE=1"
DAYS={'2026-10-06':f'{S}/pools/hist_2026-10-06.json','2026-10-07':f'{S}/pools/hist_2026-10-07.json','2026-10-08':f'{S}/pools/hist_2026-10-08.json','2026-10-12':f'{S}/pools/hist_2026-10-12.json','2026-10-13':f'{S}/pools/hi350_2026-10-13.json','2026-10-14':f'{S}/pools/hi350_2026-10-14.json','2026-10-15':f'{S}/pools/hi350_2026-10-15.json','2026-10-16':f'{S}/pools/hi350_2026-10-16.json','2026-10-19':f'{S}/pools/pool_morning.json'}
rows=[]
for d,pool in DAYS.items():
    P=json.load(open(pool,encoding='utf-8'))
    # 市場寬度:池內每檔每分鐘 close vs 昨收
    up={}; tot={}
    for s in P['pool']:
        prev = s['close'] if s.get('date','')<d else s.get('prev')
        if not prev: continue
        for f in glob.glob(f'/home/user/Stock-database/kline/{s["code"]}/2026-W4*.csv'):
            for r in csv.DictReader(open(f,encoding='utf-8')):
                if r['date']!=d: continue
                t=r['time']; tot[t]=tot.get(t,0)+1; up[t]=up.get(t,0)+(1 if float(r['close'])>prev else 0)
    breadth={t: up[t]/tot[t]*100 for t in tot if tot[t]>=10}
    for s in P['pool']:
        env=dict(os.environ, DAY=d, POOL=pool); env.update(dict(kv.split('=',1) for kv in J1.split()))
        out=subprocess.run(['python3','-I',f'{S}/sim_day.py',s['code'],s.get('name',s['code']),'10','10'],capture_output=True,text=True,env=env).stdout
        traces={}
        for l in out.splitlines():
            if l.startswith('TRACE'):
                tm=l.split()[1]; traces[tm]=l
        for l in out.splitlines():
            m=re.match(r'(做多|做空) (\d\d:\d\d) 進 ([\d.]+) ×(\d+)張 → (\d\d:\d\d) 出 ([\d.]+) \[(.*?)\].*淨 ([+\-−]?[\d,]+)', l)
            if not m: continue
            tr=traces.get(m.group(2),'')
            feat=dict(re.findall(r'(\S+?) ([+\-]?[\d.]+)%?(?=\s|$)', tr))
            v=int(m.group(8).replace('−','-').replace(',',''))
            tm=m.group(2); b=breadth.get(tm); 
            # 寬度在進場時 & 30 分鐘前
            def bat(t):
                return breadth.get(t)
            hh,mm=map(int,tm.split(':')); t30=f'{(hh*60+mm-30)//60:02d}:{(hh*60+mm-30)%60:02d}'
            rows.append({'day':d[5:],'name':s.get('name'),'side':m.group(1)[1],'tm':tm,'exit':m.group(7),'net':v,'win':int(v>0),'breadth':b,'breadth30':bat(t30),**feat})
json.dump(rows,open(f'{S}/trace9.json','w',encoding='utf-8'),ensure_ascii=False)
print(len(rows),'entries'); print(rows[0])
