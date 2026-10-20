# 2026-10-21 燈號(指標三分數版 + 連 3 根才換)加「震盪盤只顯示監控」前後,九天各燈號之後 60 根內先順向 1% 的比例
import json, glob, os, sys
import pandas as pd, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from origin_vs_swing import DAYS, S, R
from ind3_lines import first_touch
def lampOf(sc): return '做多' if sc>=0.75 else '漸多' if sc>=0.25 else '監控' if sc>-0.25 else '漸空' if sc>-0.75 else '做空'
res={'無震盪':[], '加震盪':[]}
for d,pool in DAYS.items():
    P=json.load(open(f'{S}/{pool}',encoding='utf-8'))
    for s in P['pool']:
        fs=sorted(glob.glob(f"{R}/kline/{s['code']}/2026-W4*.csv"))
        if not fs: continue
        df=pd.concat([pd.read_csv(f,dtype={'date':str,'time':str}) for f in fs]).drop_duplicates(['date','time']).sort_values(['date','time'])
        df=df[(df.time>='09:00')&(df.time<='13:30')&(df.low>0)]
        prv=df[df.date<d].tail(30); t=df[df.date==d]
        if len(t)<100: continue
        A=pd.concat([prv,t]).reset_index(drop=True); off=len(prv)
        c=A.close.values; h=A.high.values; l=A.low.values; o=A.open.values; v=A.volume.values/1000
        kb=pd.Series(c).rolling(24,min_periods=1).mean().values[off:]
        tc=c[off:]; th=h[off:]; tl=l[off:]; tv=v[off:]; tm=t.time.values
        ka=pd.Series(tc).rolling(10).mean().values
        ob=np.concatenate([[0],np.cumsum(np.sign(np.diff(tc))*tv[1:])]); obma=pd.Series(ob).rolling(10).mean().values; ob30=pd.Series(ob).rolling(30).mean().values
        # 震盪用的輪流起漲 / 起跌點(App chopPoints)
        alt=[]; lk={1:-99,-1:-99}; lo2={1:-99,-1:-99}; lastsd=0
        for q in range(1,len(tc)):
            if np.isnan(ka[q]) or np.isnan(ka[q-1]) or np.isnan(obma[q]) or np.isnan(obma[q-1]): continue
            for sd in (1,-1):
                kin=(lambda z: tl[z]>ka[z]) if sd==1 else (lambda z: th[z]<ka[z])
                if kin(q) and not kin(q-1): lk[sd]=q
                if (ob[q]-obma[q])*sd>0 and (ob[q-1]-obma[q-1])*sd<=0: lo2[sd]=q
            for sd in (-1,1):
                if q-lk[sd]<=2 and q-lo2[sd]<=2 and (lk[sd]==q or lo2[sd]==q) and lastsd!=sd: alt.append((q, th[q] if sd<0 else tl[q])); lastsd=sd
        def chop(q):
            px=[p for k,p in alt if q-45<=k<=q]
            return len(px)>=3 and (max(px)-min(px))/min(px)*100<=1.5
        up=[]; dn=[]; prev=None; cur={'無震盪':None,'加震盪':None}; cand={'無震盪':None,'加震盪':None}; cnt={'無震盪':0,'加震盪':0}
        for q in range(len(tc)-1):
            if not np.isnan(obma[q]):
                dd=ob[q]-obma[q]
                if prev is not None:
                    if dd>0 and prev<=0: up.append(tl[q])
                    elif dd<0 and prev>=0: dn.append(th[q])
                prev=dd
            if q<14: continue
            k=np.sign(ka[q]-kb[q]) if not np.isnan(ka[q]) else 0
            lo=np.sign(up[-1]-up[-2]) if len(up)>=2 else 0; hi=np.sign(dn[-1]-dn[-2]) if len(dn)>=2 else 0
            org=1 if lo>0 and hi>0 else -1 if lo<0 and hi<0 else 0
            sob=np.sign(obma[q]-obma[q-5]) if q>=5 and not np.isnan(obma[q-5]) else None
            soc=np.sign(ob30[q]-ob30[q-5]) if q>=5 and not np.isnan(ob30[q-5]) else None
            o2=0 if sob is None else (sob if soc is None else (sob if sob==soc else 0))
            ks=np.sign(ka[q]-ka[q-2]) if q>=2 and not np.isnan(ka[q-2]) else 0
            raw0=lampOf((k+org+o2+ks)/4) if tm[q]<'13:25' else '監控'
            i=off+q; ent=o[i+1]
            for nm in ('無震盪','加震盪'):
                raw=raw0 if nm=='無震盪' or raw0=='監控' or not chop(q) else '監控'
                if cur[nm] is None or tm[q]>='13:25': cur[nm]=raw; cand[nm]=None; cnt[nm]=0
                elif raw==cur[nm]: cand[nm]=None; cnt[nm]=0
                else:
                    if raw==cand[nm]: cnt[nm]+=1
                    else: cand[nm]=raw; cnt[nm]=1
                    if cnt[nm]>=3: cur[nm]=raw; cand[nm]=None; cnt[nm]=0
                res[nm].append((cur[nm], first_touch(h,l,ent,i+1,1), first_touch(h,l,ent,i+1,-1)))
for nm,rows in res.items():
    D=pd.DataFrame(rows,columns=['lamp','ft1','ftm1'])
    print('==',nm,'(連 3 根才換)')
    for s_,sd in (('做多',1),('漸多',1),('監控',1),('漸空',-1),('做空',-1)):
        g=D[D.lamp==s_]; ft=g.ftm1 if sd==-1 else g.ft1; w=(ft==1).sum(); L=(ft==-1).sum()
        print(f"  {s_} {len(g):6d} 根({len(g)*100/len(D):4.1f}%) 先{'跌' if sd==-1 else '漲'} 1% {w*100//max(1,w+L)}%(順 {w} / 逆 {L})")
