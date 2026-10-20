# 2026-10-21 使用者:「已經改合理了嗎 也要參考 ka kb 的變化」
# 燈號(指標三版)各狀態之後的漲跌:Ka/Kb 位置(k)、橘線雙升 / 雙降、指標二 Ob/Oc 5 根方向(o2)、再加 Ka/Kb 5 根方向(ks)
import json, glob, os, sys
import pandas as pd, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from origin_vs_swing import DAYS, S, R
from ind3_lines import first_touch
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
        A=pd.concat([prv,t]).reset_index(drop=True); off=len(prv)
        c=A.close.values; h=A.high.values; l=A.low.values; o=A.open.values; v=A.volume.values/1000
        kb=pd.Series(c).rolling(24,min_periods=1).mean().values[off:]
        tc=c[off:]; th=h[off:]; tl=l[off:]; tv=v[off:]; tm=t.time.values
        ka=pd.Series(tc).rolling(10).mean().values
        ob=np.concatenate([[0],np.cumsum(np.sign(np.diff(tc))*tv[1:])]); obma=pd.Series(ob).rolling(10).mean().values; ob30=pd.Series(ob).rolling(30).mean().values
        up=[]; dn=[]; prev=None
        for q in range(len(tc)):
            if not np.isnan(obma[q]):
                dd=ob[q]-obma[q]
                if prev is not None:
                    if dd>0 and prev<=0: up.append(tl[q])
                    elif dd<0 and prev>=0: dn.append(th[q])
                prev=dd
            if q<15 or q>=len(tc)-1 or tm[q]>='13:25': continue
            k=np.sign(ka[q]-kb[q]) if not np.isnan(ka[q]) else 0
            lo=np.sign(up[-1]-up[-2]) if len(up)>=2 else 0; hi=np.sign(dn[-1]-dn[-2]) if len(dn)>=2 else 0
            org=1 if lo>0 and hi>0 else -1 if lo<0 and hi<0 else 0
            N=5
            sob=np.sign(obma[q]-obma[q-N]) if q>=N and not np.isnan(obma[q-N]) else None
            soc=np.sign(ob30[q]-ob30[q-N]) if q>=N and not np.isnan(ob30[q-N]) else None
            o2=0 if sob is None else (sob if soc is None else (sob if sob==soc else 0))
            ska=np.sign(ka[q]-ka[q-N]) if q>=N and not np.isnan(ka[q-N]) else None; skb=np.sign(kb[q]-kb[q-N]) if q>=N else None
            ks=0 if ska is None else (ska if ska==skb else 0)
            ks2=np.sign(ka[q]-ka[q-2]) if q>=2 and not np.isnan(ka[q-2]) else 0   # 2026-10-21 Ka 2 根方向(使用者:「Ka 瞬間高又低下來 燈號要配合」)
            ks1=np.sign(ka[q]-ka[q-1]) if q>=1 and not np.isnan(ka[q-1]) else 0
            i=off+q; ent=o[i+1]
            rows.append(dict(k=k,org=org,o2=o2,ks=ks,ks2=ks2,ks1=ks1,ft1=first_touch(h,l,ent,i+1,1),ftm1=first_touch(h,l,ent,i+1,-1),r30=(c[min(len(c)-1,i+30)]-ent)/ent*100))
df=pd.DataFrame(rows); df.to_csv(f'{S}/results/lamp_ind3.csv',index=False)
def lamp(r,fs):
    vals=[r[f] for f in fs]; bull=sum(1 for x in vals if x>0); bear=sum(1 for x in vals if x<0)
    if bull==len(fs): return '做多'
    if bear==len(fs): return '做空'
    if bull and not bear: return '漸多'
    if bear and not bull: return '漸空'
    return '監控'
def show(g,lab,sd):
    ft=g.ftm1 if sd==-1 else g.ft1; w=(ft==1).sum(); L=(ft==-1).sum()
    print(f"  {lab:14s} {len(g):6d} 根({len(g)*100/len(df):4.1f}%) 先{'跌' if sd==-1 else '漲'} 1% {w*100//max(1,w+L):3d}%(順 {w} / 逆 {L}) 30 根後{'跌' if sd==-1 else '漲'} {(-g.r30 if sd==-1 else g.r30).mean():+.2f}%")
for nm,fs in (('兩件(Ka/Kb 位置 + 橘線)',['k','org']),('三件(+ 指標二方向,現在)',['k','org','o2']),('四件(+ Ka/Kb 方向)',['k','org','o2','ks'])):
    print('==',nm)
    st=df.apply(lambda r: lamp(r,fs),axis=1)
    for s_,sd in (('做多',1),('漸多',1),('監控',1),('漸空',-1),('做空',-1)):
        show(df[st==s_],s_,sd)
# 2026-10-21 使用者:「Ka 瞬間拉那麼高 訊號只顯示監控 也不合理」→ 改用四件分數平均(Ka/Kb 位置 + 橘線(低點方向 + 高點方向)/2 + 指標二方向 + Ka/Kb 方向)/ 4:
#   ≥ 0.75 做多、0.25~0.75 漸多、−0.25~0.25 監控、−0.75~−0.25 漸空、≤ −0.75 做空
def lamp_score(r):
    org=(np.sign(r['org']) if False else 0)
    return r['sc']
df['sc']=(df.k+df.org+df.o2+df.ks)/4
print('== 分數版(四件平均,門檻 0.25 / 0.75)')
for s_,sd,a,b in (('做多',1,0.75,9),('漸多',1,0.25,0.75),('監控',1,-0.25,0.25),('漸空',-1,-0.75,-0.25),('做空',-1,-9,-0.75)):
    g=df[(df.sc>=a)&(df.sc<b)] if s_!='做多' else df[df.sc>=a]
    if s_=='做空': g=df[df.sc<=-0.75]
    show(g,s_,sd)

for col,nm in (('ks2','Ka 2 根方向'),('ks1','Ka 1 根方向')):
    df['sc2']=(df.k+df.org+df.o2+df[col])/4
    print(f'== 分數版,第四件改成 {nm}(不看 Kb)')
    for s_,sd,a,b in (('做多',1,0.75,9),('漸多',1,0.25,0.75),('監控',1,-0.25,0.25),('漸空',-1,-0.75,-0.25),('做空',-1,-9,-0.75)):
        g=df[(df.sc2>a-1e-9)&(df.sc2<b-1e-9)] if s_ not in ('做多','做空') else (df[df.sc2>=0.75] if s_=='做多' else df[df.sc2<=-0.75])
        show(g,s_,sd)
