import os, json, subprocess, re, sys, glob
# 雲端 K 回測(2026-10-20):跑 kline_cloud/pools/ 裡每一天(只有存了雲端 K 的日子),KSRC=cloud
# 用法:python agg_cloud.py "<旗標>"   例:python agg_cloud.py "$J1"
S=os.environ.get('SIM_DIR', os.path.dirname(os.path.abspath(__file__))); ROOT=os.environ.get('REPO_DIR', os.path.abspath(os.path.join(S, '..', '..')))
flags=dict(kv.split('=',1) for kv in sys.argv[1].split()); flags['KSRC']='cloud'
tot=win=net=0; rows=[]
for pf in sorted(glob.glob(f'{ROOT}/kline_cloud/pools/*.json')):
    d=os.path.basename(pf)[:-5]; P=json.load(open(pf,encoding='utf-8')); dn=dw=dnet=0
    for s in P['pool']:
        env=dict(os.environ, DAY=d, POOL=pf); env.update(flags)
        out=subprocess.run(['python3','-I',f'{S}/sim_day.py',s['code'],s.get('name',s['code']),'10','10'],capture_output=True,text=True,env=env).stdout
        for l in out.splitlines():
            if l.startswith(('做多','做空')):
                m=re.search(r'淨 ([+\-−]?[\d,]+)', l); v=int(m.group(1).replace('−','-').replace(',','')) if m else 0
                tot+=1; dn+=1; net+=v; dnet+=v
                if v>0: win+=1; dw+=1
                rows.append(f'{d[5:]} {s.get("name")} {l.split("  價差")[0]} 淨 {v:+,}')
    print(f'{d[5:]}: {dn} 筆 {dw} 賺 {dn-dw} 賠 淨 {dnet:+,}')
print(f'合計 {tot} 筆 {win} 賺 {tot-win} 賠 勝率 {win*100//max(1,tot)}% 淨 {net:+,}'); print('\n'.join(rows))
