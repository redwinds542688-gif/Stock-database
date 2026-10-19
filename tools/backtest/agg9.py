import os
import json, subprocess, re, os, sys
S=os.environ.get('SIM_DIR', os.path.dirname(os.path.abspath(__file__)))
flags=dict(kv.split('=',1) for kv in sys.argv[1].split())
DAYS={"2026-10-06":f"{S}/hist_2026-10-06.json","2026-10-07":f"{S}/hist_2026-10-07.json","2026-10-08":f"{S}/hist_2026-10-08.json","2026-10-12":f"{S}/hist_2026-10-12.json",'2026-10-13':f'{S}/pools/hi350_2026-10-13.json','2026-10-14':f'{S}/pools/hi350_2026-10-14.json','2026-10-15':f'{S}/pools/hi350_2026-10-15.json','2026-10-16':f'{S}/pools/hi350_2026-10-16.json','2026-10-19':f'{S}/pools/pool_morning.json'}
tot=0; win=0; net=0; rows=[]
for d,pool in DAYS.items():
    P=json.load(open(pool,encoding='utf-8')); dn=dw=dnet=0
    for s in P['pool']:
        env=dict(os.environ, DAY=d, POOL=pool); env.update(flags)
        out=subprocess.run(['python3','-I',f'{S}/sim_day.py',s['code'],s.get('name',s['code']),'10','10'],capture_output=True,text=True,env=env).stdout
        for l in out.splitlines():
            if l.startswith(('做多','做空')):
                m=re.search(r'淨 ([+\-−]?[\d,]+)', l); v=int(m.group(1).replace('−','-').replace(',','')) if m else 0
                tot+=1; dn+=1; net+=v; dnet+=v
                if v>0: win+=1; dw+=1
                rows.append(f'{d[5:]} {s.get("name")} {l.split("  價差")[0]} 淨 {v:+,}')
    print(f'{d[5:]}: {dn} 筆 {dw} 賺 {dn-dw} 賠 淨 {dnet:+,}')
print(f'合計 {tot} 筆 {win} 賺 {tot-win} 賠 勝率 {win*100//max(1,tot)}% 淨 {net:+,}'); print('\n'.join(rows))
