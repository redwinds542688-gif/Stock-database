# 2026-10-21 使用者:「轉折點會慢(友達 09:25 亮做多時 09:26 就是最高點、富喬 13:01 亮做空時 13:00 就是最低點)這怎能辦」
# 燈號(現行:四件分數 + 震盪只監控 + 突破 0.5% 連 2 根放行 + 連 3 根才換)再加「不追」條件,九天各版本的做多 / 做空燈之後 60 根內先順向 1% 的比例
import json, glob, os, sys
import pandas as pd, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from origin_vs_swing import DAYS, S, R
from ind3_lines import first_touch
def lampOf(sc): return '做多' if sc>=0.75 else '漸多' if sc>=0.25 else '監控' if sc>-0.25 else '漸空' if sc>-0.75 else '做空'
V={'現行':{}, '離Ka 1%不追':{'dev':1.0}, '離Ka 0.7%不追':{'dev':0.7}, '貼當日高低0.3%不追':{'ext':0.3}, '貼當日高低0.5%不追':{'ext':0.5}, '離Ka1%+貼高低0.3%':{'dev':1.0,'ext':0.3}}
res={k:[] for k in V}; spot={k:{} for k in V}
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
            if len(px)<3: return False
            mn,mx=min(px),max(px)
            if (mx-mn)/mn*100>1.5: return False
            out=lambda k: k>=0 and (tc[k]<mn*0.995 or tc[k]>mx*1.005)
            return not (out(q) and out(q-1))
        up=[]; dn=[]; prev=None; st={k:[None,None,0] for k in V}
        hi=-1e9; lo=1e9
        for q in range(len(tc)-1):
            hi=max(hi,th[q]); lo=min(lo,tl[q])
            if not np.isnan(obma[q]):
                dd=ob[q]-obma[q]
                if prev is not None:
                    if dd>0 and prev<=0: up.append(tl[q])
                    elif dd<0 and prev>=0: dn.append(th[q])
                prev=dd
            if q<14: continue
            k=np.sign(ka[q]-kb[q]) if not np.isnan(ka[q]) else 0
            lo_=np.sign(up[-1]-up[-2]) if len(up)>=2 else 0; hi_=np.sign(dn[-1]-dn[-2]) if len(dn)>=2 else 0
            org=1 if lo_>0 and hi_>0 else -1 if lo_<0 and hi_<0 else 0
            sob=np.sign(obma[q]-obma[q-5]) if q>=5 and not np.isnan(obma[q-5]) else None
            soc=np.sign(ob30[q]-ob30[q-5]) if q>=5 and not np.isnan(ob30[q-5]) else None
            o2=0 if sob is None else (sob if soc is None else (sob if sob==soc else 0))
            ks=np.sign(ka[q]-ka[q-2]) if q>=2 and not np.isnan(ka[q-2]) else 0
            raw0=lampOf((k+org+o2+ks)/4) if tm[q]<'13:25' else '監控'
            if raw0 not in ('監控',) and chop(q): raw0='監控'
            i=off+q; ent=o[i+1]; ft=None
            for nm,cf in V.items():
                raw=raw0
                if raw in ('做多','漸多','做空','漸空'):
                    sd=1 if raw in ('做多','漸多') else -1
                    if 'dev' in cf and not np.isnan(ka[q]) and (tc[q]-ka[q])/ka[q]*100*sd > cf['dev']: raw='監控'
                    if 'ext' in cf and ((sd==1 and tc[q]>=hi*(1-cf['ext']/100)) or (sd==-1 and tc[q]<=lo*(1+cf['ext']/100))): raw='監控'
                S_=st[nm]
                if S_[0] is None or tm[q]>='13:25': S_[0]=raw; S_[1]=None; S_[2]=0
                elif raw==S_[0]: S_[1]=None; S_[2]=0
                else:
                    if raw==S_[1]: S_[2]+=1
                    else: S_[1]=raw; S_[2]=1
                    if S_[2]>=3: S_[0]=raw; S_[1]=None; S_[2]=0
                if S_[0] in ('做多','做空'):
                    if ft is None: ft=(first_touch(h,l,ent,i+1,1), first_touch(h,l,ent,i+1,-1))
                    res[nm].append((S_[0], ft[0] if S_[0]=='做多' else ft[1]))
                if s['name'] in ('友達','富喬') and d=='2026-10-19': pass
for nm,rows in res.items():
    D=pd.DataFrame(rows,columns=['lamp','ft'])
    out=[]
    for s_ in ('做多','做空'):
        g=D[D.lamp==s_]; w=(g.ft==1).sum(); L=(g.ft==-1).sum(); out.append(f"{s_} {len(g):6d} 根 {w*100//max(1,w+L)}%(順 {w}/逆 {L})")
    print(f'{nm:16s}', ' | '.join(out))
