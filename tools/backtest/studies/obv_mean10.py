import os
import json, csv, glob, numpy as np, collections
S=os.environ.get('SIM_DIR', os.path.dirname(os.path.abspath(__file__)))
DAYS={'2026-10-06':f'{S}/pools/hist_2026-10-06.json','2026-10-07':f'{S}/pools/hist_2026-10-07.json','2026-10-08':f'{S}/pools/hist_2026-10-08.json','2026-10-12':f'{S}/pools/hist_2026-10-12.json','2026-10-13':f'{S}/pools/hi350_2026-10-13.json','2026-10-14':f'{S}/pools/hi350_2026-10-14.json','2026-10-15':f'{S}/pools/hi350_2026-10-15.json','2026-10-16':f'{S}/pools/hi350_2026-10-16.json','2026-10-19':f'{S}/pools/pool_morning.json'}
def bars(code,d):
    out=[]
    for f in sorted(glob.glob(f'/home/user/Stock-database/kline/{code}/2026-W4*.csv')):
        for r in csv.DictReader(open(f,encoding='utf-8')):
            if r['date']==d and '09:00'<=r['time']<='13:30': out.append((r['time'],float(r['open']),float(r['high']),float(r['low']),float(r['close']),float(r['volume'])/1000))
    return out
def expmean(a,n):  # 前 n 根不足時用到目前為止的平均(和 App OBV30 方向一致)
    out=np.empty(len(a))
    for i in range(len(a)): out[i]=a[max(0,i-n+1):i+1].mean()
    return out
stat=collections.defaultdict(lambda: [0,[],[],0,0])   # state → [n, fwd10, fwd30, hit+1 first, hit-1 first]
trans=collections.defaultdict(lambda: [0,[],[],0,0])
ndays=0
for d,pool in DAYS.items():
    P=json.load(open(pool,encoding='utf-8'))
    for s in P['pool']:
        B=bars(s['code'],d)
        if len(B)<60: continue
        ndays+=1
        c=np.array([b[4] for b in B]); v=np.array([b[5] for b in B])
        sign=np.sign(np.diff(c,prepend=c[0])); ob=np.cumsum(sign*v)
        ma10=expmean(ob,10); ma30=expmean(ob,30)
        rng=max(ob.max()-ob.min(),1)
        prev_state=None
        for i in range(12,len(B)-30):    # 09:12 起(i=12),要有之後 30 根
            R=ob[i-10:i].mean(); Bm=ma10[i-10:i].mean(); Pm=ma30[i-10:i].mean()
            st=('紅>藍' if R>Bm else '紅<藍')+' '+('藍>紫' if Bm>Pm else '藍<紫')
            # 往後 10 / 30 分鐘的價格變化,以及 30 分鐘內先碰 +1% 還是 −1%
            e=c[i]; f10=(c[i+10]-e)/e*100; f30=(c[i+30]-e)/e*100
            hit=0
            for j in range(i+1,i+31):
                hi=(B[j][2]-e)/e*100; lo=(B[j][3]-e)/e*100
                if hi>=1 and lo<=-1: break
                if hi>=1: hit=1; break
                if lo<=-1: hit=-1; break
            for tbl,key in ((stat,st),):
                x=tbl[key]; x[0]+=1; x[1].append(f10); x[2].append(f30); x[3]+= hit==1; x[4]+= hit==-1
            if prev_state is not None and st!=prev_state:
                x=trans[prev_state+' → '+st]; x[0]+=1; x[1].append(f10); x[2].append(f30); x[3]+= hit==1; x[4]+= hit==-1
            prev_state=st
print('檔日',ndays)
def show(tbl,title):
    print(f'\n== {title}')
    print(f"{'狀態(09:02~09:11 等前 10 根的平均)':34} {'樣本':>7} {'10分後均':>8} {'30分後均':>8} {'30分內先+1%':>10} {'先-1%':>7}")
    for k,x in sorted(tbl.items(), key=lambda kv:-kv[1][0]):
        if x[0]<30: continue
        print(f"{k:34} {x[0]:7d} {np.mean(x[1]):+8.3f} {np.mean(x[2]):+8.3f} {x[3]/x[0]*100:9.1f}% {x[4]/x[0]*100:6.1f}%")
show(stat,'每一分鐘的狀態 vs 之後走勢')
show(trans,'狀態剛改變的那一分鐘 vs 之後走勢(起漲 / 起跌點候選)')
