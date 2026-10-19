import os
import json, subprocess, re, os, sys
S=os.environ.get('SIM_DIR', os.path.dirname(os.path.abspath(__file__)))
d=sys.argv[1]; flags=dict(kv.split('=',1) for kv in sys.argv[2].split()); pool=sys.argv[3] if len(sys.argv)>3 else '/home/user/Stock-database/pool.json'
P=json.load(open(pool,encoding='utf-8')); tot=0; win=0; net=0; rows=[]
for s in P['pool']:
    env=dict(os.environ, DAY=d, POOL=pool); env.update(flags)
    out=subprocess.run(['python3','-I',f'{S}/sim_day.py',s['code'],s.get('name',s['code']),'10','10'],capture_output=True,text=True,env=env).stdout
    for l in out.splitlines():
        if l.startswith(('做多','做空')):
            m=re.search(r'淨 ([+\-−]?[\d,]+)', l); v=int(m.group(1).replace('−','-').replace(',','')) if m else 0
            tot+=1; net+=v; win+= v>0; rows.append(f'{s.get("name")} {l.split("  價差")[0]} 淨 {v:+,}')
print(f'{d}: {tot} 筆 {win} 賺 {tot-win} 賠 淨 {net:+,}'); print('\n'.join(rows))
