import os
import json, os, glob, csv, datetime, subprocess, re, time, requests, shutil
D='<SIM_DIR>/dl/lu/'
S='<SIM_DIR>/'
R='<SIM_DIR>/root2/'
REPO='/home/user/Stock-database/'
cands=json.load(open(D+'cands.json'))
tz=datetime.timezone(datetime.timedelta(hours=8))
SS=requests.Session(); SS.headers.update({"User-Agent":"Mozilla/5.0"})
def week(d): 
    y,w,_=datetime.date.fromisoformat(d).isocalendar(); return f'{y}-W{w:02d}'
def yahoo(code):
    for suf in ['.TW','.TWO']:
        try:
            j=SS.get(f'https://query1.finance.yahoo.com/v8/finance/chart/{code}{suf}',params={'interval':'1m','range':'7d'},timeout=30).json()
            r=(j.get('chart',{}).get('result') or [None])[0]
            if not r or not r.get('timestamp'): continue
            q=r['indicators']['quote'][0]; rows={}
            for i,t in enumerate(r['timestamp']):
                if q['close'][i] is None: continue
                dt=datetime.datetime.fromtimestamp(t,tz)
                if not ('09:00'<=dt.strftime('%H:%M')<='13:30'): continue
                rows.setdefault(dt.strftime('%Y-%m-%d'),[]).append((dt.strftime('%H:%M'),q['open'][i],q['high'][i],q['low'][i],q['close'][i],int(q['volume'][i] or 0)))
            if rows: return rows
        except Exception as e: print('yahoo fail',code,e)
    return {}
def ensure(code):
    """root2/kline/<code>:先用 repo 的檔,缺的日期用 Yahoo 補"""
    dst=R+'kline/'+code; os.makedirs(dst,exist_ok=True)
    have={}
    for f in glob.glob(REPO+'kline/'+code+'/2026-W4*.csv'):
        for r in csv.DictReader(open(f,encoding='utf-8')): have.setdefault(r['date'],[]).append((r['time'],r['open'],r['high'],r['low'],r['close'],r['volume']))
    y=yahoo(code); time.sleep(0.3)
    for d,rows in y.items():
        if d not in have: have[d]=[(t,o,h,l,c,v) for (t,o,h,l,c,v) in rows]
    byw={}
    for d in sorted(have): byw.setdefault(week(d),[]).extend((d,)+tuple(x) for x in have[d])
    for f in glob.glob(dst+'/*.csv'): os.remove(f)
    for w,rows in byw.items():
        with open(f'{dst}/{w}.csv','w') as fh:
            fh.write('date,time,open,high,low,close,volume\n')
            for r in rows: fh.write(','.join(str(x) for x in r)+'\n')
    return sorted(have)
J1="OBMASLOPE=4 FPGAP=2.5 TAKE=2.5 VWAPF=1 FP=1 MAXDEV=1 MIRROR=1 PENDVOL=1 VOLMIN=6000 RVOLMIN=0.9 CAP=350000 REVOK=1 ENTRYMAX=3.5 OBVX=2 OBVXMIN=1 OBVX2=1 OB2=30 SLOPEMIN=-99 GAPMODE=dir GAPTH=2 OB3MA=6 REDSLOPE=-2 EXITRESET=tp OBVDIR=30 NEARHL=2 NOWEAK=1"
J2="FPGAP=2.5 TAKE=2.5 VWAPF=1 FP=1 MAXDEV=1 MIRROR=1 PENDVOL=1 VOLMIN=6000 RVOLMIN=0.9 CAP=350000 REVOK=1 ENTRYMAX=3.5 OBVX=2 OBVXMIN=1 OBVX2=1 OB2=30 SLOPEMIN=-99 GAPMODE=dir GAPTH=2 OB3MA=6 REDSLOPE=-2 EXITRESET=tp MODE=cross XWIN=10 OBMASLOPE=0 XSTR=2 XNEWHI=1 XOB2=20 XOB2DIR=slope XKMA=0 NOLOCK=1 STRONG=1 SREQ=1030,dx,ext SBARS=2 SWIN=5 STRONGMAX=4 SNOAFTER=11:30 MINGAP=4 NOENTRYAFTER=12:00 XOB3=30 WEAKORDER=1 OBVDIR=30 NEARHL=2"

def mkpool(day, pf, added, tag):
    """模擬器要從 POOL 拿昨收:把新增的股票(昨收 = 前一交易日收盤)補進當天的池檔"""
    P=json.load(open(pf,encoding='utf-8')); have={s['code'] for s in P['pool']}
    for r in added:
        if r['code'] in have: continue
        P['pool'].append({'code':r['code'],'name':r['name'],'close':r['close'],'date':r['date'],'prev':r['prev'],'lots':r['lots'],'amp':r['amp'],'market':r['market']})
    out=D+f'pool_{day}_{tag}.json'; json.dump(P,open(out,'w',encoding='utf-8'),ensure_ascii=False); return out
tot={'一':[], '二':[]}
for day,lst in cands.items():
    pf0=S+('pool_morning.json' if day=='2026-10-19' else f'hi350_{day}.json'); inpool={s['code'] for s in json.load(open(pf0,encoding='utf-8'))['pool']}
    pf=mkpool(day, pf0, lst, 'lu')
    for c in lst:
        code=c['code']; name=c['name']
        if code in inpool: print(day,name,'已在池內(?)'); continue
        ds=ensure(code)
        if day not in ds: print(day,name,'無當天分K'); continue
        for rs,fl in (('一',J1),('二',J2)):
            env=dict(os.environ, DAY=day, POOL=pf); env.update(dict(kv.split('=',1) for kv in fl.split()))
            out=subprocess.run(['python3','-I','sim_tmp.py',code,name,'10','10'],capture_output=True,text=True,env=env).stdout
            head=out.splitlines()[0] if out else ''
            for l in out.splitlines():
                if l.startswith(('做多','做空')):
                    m=re.search(r'淨 ([+\-−]?[\d,]+)', l); v=int(m.group(1).replace('−','-').replace(',','')) if m else 0
                    tot[rs].append((day,name,l.split('  價差')[0],v))
            # 當天走勢摘要
        m=re.search(r'開 ([\d.]+)\s+高 ([\d.]+)\s+低 ([\d.]+)\s+收 ([\d.]+)\s+\(([^)]+)\)',head)
        print(day,name,code,'昨收漲停',c['close'],'→ 當天',head.split('昨收')[1] if '昨收' in head else head[:80], '| 判一',sum(1 for x in tot['一'] if x[0]==day and x[1]==name),'筆 判二',sum(1 for x in tot['二'] if x[0]==day and x[1]==name),'筆')
for rs in ('一','二'):
    L=tot[rs]; print(f'\n=== 判{rs} 新增漲停股交易 {len(L)} 筆 {sum(1 for x in L if x[3]>0)} 賺 {sum(1 for x in L if x[3]<=0)} 賠 淨 {sum(x[3] for x in L):+,}')
    for x in L: print(' ',x[0][5:],x[1],x[2],f'淨 {x[3]:+,}')
