# 2026-10-20 使用者:「看起漲起跌點跟高低點的關係」
# 高低點 = App swingPoints(收盤在均價 10 上 / 下分段、連 2 根換段、相鄰差 < 0.8% 拿掉、高 = 兩低之間最高);
# 起漲 / 起跌點 = App originPoints(整根 K 穿均價 + OBV 穿 OBV 均 10,相差 ≤ 2 根;今天創新高後第一次 = 起跌、創新低後第一次 = 起漲)
#   另外看「輪流版」(震盪判斷用的 chopPoints:每次穿越都算,起漲起跌輪流)。九天 Yahoo 1 分K、回測池股票。
import json, glob, os, re, sys
import pandas as pd, numpy as np
S=os.path.dirname(os.path.dirname(os.path.abspath(__file__))); R=os.path.abspath(os.path.join(S,'..','..'))
DAYS=dict(re.findall(r'["\'](2026-10-\d\d)["\']:f["\']\{S\}/(pools/[^"\']+)["\']', open(f'{S}/agg9.py').read()))
def swings(h,l,c,n=10,pct=0.8,minb=2):
    legs=[]; s_=0; pend=0; ps=-1; csum=0; ma=[]
    for i in range(len(c)):
        csum+=c[i]
        if i>=n: csum-=c[i-n]
        ma.append(csum/min(i+1,n))
    def ext(L,i):
        if (L[0]>0 and h[i]>h[L[1]]) or (L[0]<0 and l[i]<l[L[1]]): L[1]=i
    for i in range(1,len(c)):
        sd=1 if c[i]>ma[i] else -1 if c[i]<ma[i] else 0
        if not legs:
            if sd: legs.append([sd,i])
            continue
        L=legs[-1]
        if sd==0 or sd==L[0]: pend=0 if sd else pend; ext(L,i); continue
        if pend!=sd: pend=sd; ps=i
        if i-ps+1>=minb:
            N=[sd,ps]
            for j in range(ps,i+1): ext(N,j)
            legs.append(N); pend=0
        else: ext(L,i)
    P=[[s,k,h[k] if s>0 else l[k]] for s,k in legs]
    while True:
        best=-1; bm=1e9
        for q in range(len(P)-2):
            mv=abs(P[q][2]-P[q+1][2])/min(P[q][2],P[q+1][2])*100
            if mv<pct and mv<bm: bm=mv; best=q
        if best<0: break
        A,B=P[best],P[best+1]
        if best>0 and ((B[0]>0 and B[2]>P[best-1][2]) or (B[0]<0 and B[2]<P[best-1][2])): P[best-1]=B
        if (A[0]>0 and A[2]>P[best+2][2]) or (A[0]<0 and A[2]<P[best+2][2]): P[best+2]=A
        del P[best:best+2]
    for _ in range(5):
        mv=False
        for q in range(len(P)):
            a=P[q-1][1]+1 if q else 0; z=P[q+1][1]-1 if q<len(P)-1 else len(c)-1; k=P[q][1]
            for j in range(a,z+1):
                if (P[q][0]>0 and h[j]>h[k]) or (P[q][0]<0 and l[j]<l[k]): k=j
            if k!=P[q][1]: P[q]=[P[q][0],k,h[k] if P[q][0]>0 else l[k]]; mv=True
        if not mv: break
    return P
def origins(day,h,l,c,ma,ob,obma,armed=True):
    out=[]; hi=-1e18; lo=1e18; armD=armU=False; lk={1:-99,-1:-99}; lo2={1:-99,-1:-99}; last=0
    for q in range(len(c)):
        if h[q]>hi: hi=h[q]; armD=True
        if l[q]<lo: lo=l[q]; armU=True
        if q==0 or np.isnan(ma[q]) or np.isnan(ma[q-1]) or np.isnan(obma[q]) or np.isnan(obma[q-1]): continue
        for sd in (1,-1):
            kin=(lambda z: l[z]>ma[z]) if sd==1 else (lambda z: h[z]<ma[z])
            if kin(q) and not kin(q-1): lk[sd]=q
            if (ob[q]-obma[q])*sd>0 and (ob[q-1]-obma[q-1])*sd<=0: lo2[sd]=q
        for sd in (-1,1):
            hit = q-lk[sd]<=2 and q-lo2[sd]<=2 and (lk[sd]==q or lo2[sd]==q)
            if not hit: continue
            if armed:
                if sd==-1 and armD: out.append((q,-1)); armD=False
                if sd==1 and armU: out.append((q,1)); armU=False
            elif last!=sd: out.append((q,sd)); last=sd
    return out
rows=[]
for d,pool in DAYS.items():
    P=json.load(open(f'{S}/{pool}',encoding='utf-8'))
    for s in P['pool']:
        fs=sorted(glob.glob(f"{R}/kline/{s['code']}/2026-W4*.csv"))
        if not fs: continue
        df=pd.concat([pd.read_csv(f,dtype={'date':str,'time':str}) for f in fs]).drop_duplicates(['date','time']).sort_values(['date','time'])
        df=df[(df.time>='09:00')&(df.time<='13:30')&(df.low>0)]
        prv=df[df.date<d].tail(30); t=df[df.date==d]
        if len(t)<100: continue
        allb=pd.concat([prv,t]).reset_index(drop=True); off=len(prv)
        h=allb.high.values; l=allb.low.values; c=allb.close.values; o=allb.open.values; v=allb.volume.values/1000
        SW=[p for p in swings(h,l,c) if p[1]>=off]
        th=t.high.values; tl=t.low.values; tc=t.close.values; tv=t.volume.values/1000; to=t.open.values; tm=t.time.values
        ma=pd.Series(tc).rolling(10).mean().values; ob=np.concatenate([[0],np.cumsum(np.sign(np.diff(tc))*tv[1:])]); obma=pd.Series(ob).rolling(10).mean().values
        sw=[(s_,k-off,v_) for s_,k,v_ in SW]
        for armed in (True,False):
            for q,sd in origins(d,th,tl,tc,ma,ob,obma,armed):
                if q+1>=len(tc): continue
                prev=[p for p in sw if p[1]<=q]; nxt=[p for p in sw if p[1]>q]
                if not prev or not nxt: continue
                P0=prev[-1]; N1=nxt[0]
                ent=to[q+1]
                # 起跌(sd=-1):對的位置 = 前一個點是高點(在下跌段裡),下一個點是低點
                inleg = (P0[0]==-sd*-1*-1) if False else (P0[0]==(1 if sd==-1 else -1))
                same=[p for p in nxt if p[0]==(-1 if sd==-1 else 1)]   # 下一個同向終點(起跌 → 下一個低點)
                tgt=same[0] if same else None
                opp=[p for p in nxt if p[0]==(1 if sd==-1 else -1)]   # 下一個反向點(起跌 → 下一個高點)
                rows.append(dict(day=d[5:],name=s.get('name'),t=tm[q],sd=sd,armed=armed,inleg=inleg,
                    lag=q-P0[1], lagpct=abs(P0[2]-(th[q] if sd==-1 else tl[q]))/P0[2]*100 if inleg else np.nan,
                    swing=abs(P0[2]-N1[2])/P0[2]*100 if inleg else np.nan,
                    rest=((ent-tgt[2])*(-sd)*-1/ent*100*-1 if False else (ent-tgt[2])/ent*100*(1 if sd==-1 else -1)) if tgt else np.nan,
                    adverse=((opp[0][2]-ent)/ent*100*(1 if sd==-1 else -1)) if opp and (not tgt or opp[0][1]<tgt[1]) else 0.0))
df=pd.DataFrame(rows)
df.to_csv(f'{S}/results/origin_vs_swing.csv',index=False)
for armed in (True,False):
    print('=====', '起漲起跌點(圖上標的,創新高 / 新低後第一次)' if armed else '輪流版(每次穿越都算)')
    for sd,nm in ((-1,'起跌'),(1,'起漲')):
        x=df[(df.armed==armed)&(df.sd==sd)]
        if not len(x): continue
        a=x[x.inleg]; b=x[~x.inleg]
        print(f"{nm} {len(x)} 個:在對的段裡(起跌在高點之後、低點之前) {len(a)} 個 {len(a)*100//len(x)}% / 在反的段裡 {len(b)} 個")
        if len(a): print(f"   對的:離前一個{'高' if sd==-1 else '低'}點 中位 {a.lag.median():.0f} 根、價差 {a.lagpct.median():.2f}%(這段全長中位 {a.swing.median():.2f}%);進場後到下一個{'低' if sd==-1 else '高'}點還有 中位 {a['rest'].median():.2f}%、平均 {a['rest'].mean():.2f}%;≥1% 的 {(a['rest']>=1).mean()*100:.0f}%")
        if len(b): print(f"   反的:進場後先被反向推 中位 {b.adverse.median():.2f}%;之後到下一個{'低' if sd==-1 else '高'}點 中位 {b['rest'].median():.2f}%")
