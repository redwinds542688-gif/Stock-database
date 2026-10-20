# 2026-10-21 使用者:「看一下 指標三 二條線 跟漲跌有沒有什麼關聯」
# 指標三:橘線 = Oa(OBV)穿越 Ob(OBV 均 10)的點連線(往上穿取那根最低價、往下穿取最高價);紫線 = Ka(均價 10,每天重算)穿越 Kb(均價 24,跨日)的點連線(值 = Ka)。
# 每一根只用到這根為止的點(不偷看)。結果:60 根內先順向 1% 的比例、30 根後報酬。九天 Yahoo 1 分K、回測池。
import json, glob, os, sys
import pandas as pd, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from origin_vs_swing import DAYS, S, R
def first_touch(h,l,ent,i0,sd,pct=1.0,win=60):
    up=ent*(1+pct/100); dn=ent*(1-pct/100)
    for j in range(i0,min(len(h),i0+win)):
        hu=h[j]>=up; hd=l[j]<=dn
        if hu and hd: return 0
        if hu: return 1 if sd==1 else -1
        if hd: return 1 if sd==-1 else -1
    return 0
def main():
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
            kb=pd.Series(c).rolling(24,min_periods=1).mean().values
            tc=c[off:]; th=h[off:]; tl=l[off:]; tv=v[off:]
            ka=pd.Series(tc).rolling(10).mean().values
            ob=np.concatenate([[0],np.cumsum(np.sign(np.diff(tc))*tv[1:])]); obma=pd.Series(ob).rolling(10).mean().values
            OX=[]; KX=[]   # (q, side, price)
            for q in range(1,len(tc)):
                if not np.isnan(obma[q]) and not np.isnan(obma[q-1]):
                    d0=ob[q-1]-obma[q-1]; d1=ob[q]-obma[q]
                    if d1>0 and d0<=0: OX.append((q,1,tl[q]))
                    elif d1<0 and d0>=0: OX.append((q,-1,th[q]))
                if not np.isnan(ka[q]) and not np.isnan(ka[q-1]):
                    e0=ka[q-1]-kb[off+q-1]; e1=ka[q]-kb[off+q]
                    if e1>0 and e0<=0: KX.append((q,1,ka[q]))
                    elif e1<0 and e0>=0: KX.append((q,-1,ka[q]))
            oi=ki=0
            for q in range(15,len(tc)-1):
                while oi<len(OX) and OX[oi][0]<=q: oi+=1
                while ki<len(KX) and KX[ki][0]<=q: ki+=1
                ox=OX[:oi]; kx=KX[:ki]
                if len(ox)<3 or len(kx)<2: continue
                i=off+q; ent=o[i+1]
                # 橘線:最近兩個同類點(兩個往上穿的低點 / 兩個往下穿的高點)是墊高還是降低
                ups=[p for p in ox if p[1]==1][-2:]; dns=[p for p in ox if p[1]==-1][-2:]
                o_low = (ups[1][2]-ups[0][2]) if len(ups)==2 else np.nan
                o_high= (dns[1][2]-dns[0][2]) if len(dns)==2 else np.nan
                o_last=ox[-1]; k_last=kx[-1]
                # 紫線斜率:最近兩個 Ka×Kb 點
                k_slope=(kx[-1][2]-kx[-2][2])
                # 兩線位置:橘線最後一點價 vs 紫線最後一點價
                rows.append(dict(day=d[5:],t=q, ft1=first_touch(h,l,ent,i+1,1), ftm1=first_touch(h,l,ent,i+1,-1),
                    r30=(c[min(len(c)-1,i+30)]-ent)/ent*100,
                    o_low=np.sign(o_low) if not np.isnan(o_low) else 0, o_high=np.sign(o_high) if not np.isnan(o_high) else 0,
                    o_side=o_last[1], o_age=q-o_last[0], k_side=k_last[1], k_age=q-k_last[0], k_slope=np.sign(k_slope),
                    o_vs_k=np.sign(o_last[2]-k_last[2]), px_vs_k=np.sign(tc[q]-k_last[2]),
                    kx_now=(k_last[0]==q), ox_now=(o_last[0]==q)))
    df=pd.DataFrame(rows); df.to_csv(f'{S}/results/ind3_lines.csv',index=False)
    def show(g,lab,sd):
        if not len(g): return
        ft=g.ftm1 if sd==-1 else g.ft1; w=(ft==1).sum(); L=(ft==-1).sum()
        print(f"  {lab:34s} {len(g):6d} 次  先順向 1% {w*100//max(1,w+L):3d}%(順 {w} / 逆 {L})  30 根後{'跌' if sd==-1 else '漲'} {(-g.r30 if sd==-1 else g.r30).mean():+.2f}%")
    print('== 基準(每一根)'); show(df,'做多',1); show(df,'做空',-1)
    print('== 紫線(Ka×Kb):最近一次是往上穿 / 往下穿')
    for ks,nm in ((1,'Ka 在 Kb 上(上次往上穿)'),(-1,'Ka 在 Kb 下(上次往下穿)')):
        g=df[df.k_side==ks]; show(g,nm+' 做多',1); show(g,nm+' 做空',-1)
    print('== 紫線剛穿越那根(事件)')
    show(df[df.kx_now&(df.k_side==1)],'Ka 往上穿 Kb → 做多',1); show(df[df.kx_now&(df.k_side==-1)],'Ka 往下穿 Kb → 做空',-1)
    print('== 紫線斜率(最近兩個穿越點)')
    for ks,nm in ((1,'紫線往上'),(-1,'紫線往下')):
        g=df[df.k_slope==ks]; show(g,nm+' 做多',1); show(g,nm+' 做空',-1)
    print('== 橘線(Oa×Ob):最近兩個往上穿的低點 / 往下穿的高點')
    for a,b,nm in ((1,1,'低點墊高 + 高點墊高'),(-1,-1,'低點降低 + 高點降低'),(1,-1,'低點墊高 + 高點降低(收斂)'),(-1,1,'低點降低 + 高點墊高(擴張)')):
        g=df[(df.o_low==a)&(df.o_high==b)]; show(g,nm+' 做多',1); show(g,nm+' 做空',-1)
    print('== 橘線剛穿越那根(事件)')
    show(df[df.ox_now&(df.o_side==1)],'Oa 往上穿 Ob → 做多',1); show(df[df.ox_now&(df.o_side==-1)],'Oa 往下穿 Ob → 做空',-1)
    print('== 兩條線一起:紫線方向 × 橘線剛穿越')
    for ks in (1,-1):
        g=df[df.ox_now&(df.k_side==ks)]
        show(g[g.o_side==1],f"Ka{'上' if ks==1 else '下'} + Oa 往上穿 → 做多",1); show(g[g.o_side==-1],f"Ka{'上' if ks==1 else '下'} + Oa 往下穿 → 做空",-1)
    print('== 兩條線一起:紫線方向 × 橘線型態')
    for ks in (1,-1):
        for a,b,nm in ((1,1,'橘雙升'),(-1,-1,'橘雙降')):
            g=df[(df.k_side==ks)&(df.o_low==a)&(df.o_high==b)]
            show(g,f"Ka{'上' if ks==1 else '下'} + {nm} 做多",1); show(g,f"Ka{'上' if ks==1 else '下'} + {nm} 做空",-1)
    print('== 橘線最後一點在紫線最後一點之上 / 之下')
    for x,nm in ((1,'橘在紫上'),(-1,'橘在紫下')):
        g=df[df.o_vs_k==x]; show(g,nm+' 做多',1); show(g,nm+' 做空',-1)
    print('== 價在紫線最後一點之上 / 之下')
    for x,nm in ((1,'價在紫線點上'),(-1,'價在紫線點下')):
        g=df[df.px_vs_k==x]; show(g,nm+' 做多',1); show(g,nm+' 做空',-1)

if __name__ == "__main__":
    main()
