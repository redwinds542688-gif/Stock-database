# 2026-10-20 使用者:「用 OBV 觀測震盪 是否可以」(富喬 OBV 三條線在 -20K~-24K 遊走)
# 不加震盪過濾跑一次,每筆進場印 OBV 帶寬指標,對照輸贏,找門檻。用法:python studies/obc_calib.py "<旗標(不含 CHOP)>" > out.txt
import os, json, subprocess, re, sys, glob
S=os.path.dirname(os.path.dirname(os.path.abspath(__file__))); ROOT=os.path.abspath(os.path.join(S,'..','..'))
flags=dict(kv.split('=',1) for kv in sys.argv[1].split()); flags.update(OBCTRACE='1', CHOPW='45')
DAYS=[(d,p,'yahoo') for d,p in re.findall(r'"(2026-10-\d\d)":f"\{S\}/(pools/[^"]+)"', open(f'{S}/agg9.py').read())]
DAYS+= [(d,p,'yahoo') for d,p in re.findall(r"'(2026-10-\d\d)':f'\{S\}/(pools/[^']+)'", open(f'{S}/agg9.py').read())]
DAYS=[(d,f'{S}/{p}',k) for d,p,k in DAYS]+[(os.path.basename(p)[:-5],p,'cloud') for p in sorted(glob.glob(f'{ROOT}/kline_cloud/pools/*.json'))]
for d,pool,ks in DAYS:
    P=json.load(open(pool,encoding='utf-8'))
    for s in P['pool']:
        env=dict(os.environ, DAY=d, POOL=pool); env.update(flags)
        if ks=='cloud': env['KSRC']='cloud'
        out=subprocess.run(['python3','-I',f'{S}/sim_day.py',s['code'],s.get('name',s['code']),'10','10'],capture_output=True,text=True,env=env).stdout
        obc={}
        for l in out.splitlines():
            if l.startswith('OBC '): obc[l.split()[1]]=l[10:]
        for l in out.splitlines():
            if l.startswith(('做多','做空')):
                k0=l.split()[1]; m=re.search(r'淨 ([+\-−]?[\d,]+)', l); v=int(m.group(1).replace('−','-').replace(',','')) if m else 0
                print(f'{ks} {d[5:]} {s.get("name")} {l[:2]} {k0} 淨 {v:+,} || {obc.get(k0,"?")}', flush=True)
