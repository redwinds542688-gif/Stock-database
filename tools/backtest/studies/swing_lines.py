# 2026-10-20 使用者:「這照高低連線 有看出什麼邏輯嗎 如傾斜角度 看空或是看多的訊號」
# 每一根只用「到這根為止」的 K 算高低點(不偷看),最後一個點還在走、不算;用最近兩個確定的高點、兩個確定的低點:
#   型態:雙降(高點降低 + 低點降低)/ 雙升 / 收斂(高降低升)/ 擴張(高升低降);斜率 = 兩點連線每 10 根走幾 %。
# 結果看「之後先走 1% 順向還是先走 1% 逆向」(60 根內),和 30 根後的報酬。九天 Yahoo 1 分K、回測池。
import json, glob, os, re, sys
import pandas as pd, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from origin_vs_swing import swings, origins, DAYS, S, R
def first_touch(h,l,ent,i0,sd,pct=1.0,win=60):
    up=ent*(1+pct/100); dn=ent*(1-pct/100)
    for j in range(i0,min(len(h),i0+win)):
        hu=h[j]>=up; hd=l[j]<=dn
        if hu and hd: return 0
        if hu: return 1 if sd==1 else -1
        if hd: return 1 if sd==-1 else -1
    return 0
ev=[]; base=[]
for d,pool in DAYS.items():
    P=json.load(open(f'{S}/{pool}',encoding='utf-8'))
    for s in P['pool']:
        fs=sorted(glob.glob(f"{R}/kline/{s['code']}/2026-W4*.csv"))
        if not fs: continue
        df=pd.concat([pd.read_csv(f,dtype={'date':str,'time':str}) for f in fs]).drop_duplicates(['date','time']).sort_values(['date','time'])
        df=df[(df.time>='09:00')&(df.time<='13:30')&(df.low>0)]
        prv=df[df.date<d].tail(60); t=df[df.date==d]
        if len(t)<100: continue
        A=pd.concat([prv,t]).reset_index(drop=True); off=len(prv)
        h=A.high.values; l=A.low.values; c=A.close.values; o=A.open.values
        th=t.high.values; tl=t.low.values; tc=t.close.values; tv=t.volume.values/1000; tm=t.time.values
        ma=pd.Series(tc).rolling(10).mean().values; ob=np.concatenate([[0],np.cumsum(np.sign(np.diff(tc))*tv[1:])]); obma=pd.Series(ob).rolling(10).mean().values
        org={q:sd for q,sd in origins(d,th,tl,tc,ma,ob,obma,True)}
        prevcat=None; prevbreak={1:False,-1:False}
        for q in range(15, len(tc)-1):   # 開盤 15 根後
            i=off+q
            Pv=swings(h[:i+1],l[:i+1],c[:i+1])[:-1]   # 去掉還在走的最後一點
            Hs=[p for p in Pv if p[0]>0][-2:]; Ls=[p for p in Pv if p[0]<0][-2:]
            if len(Hs)<2 or len(Ls)<2: continue
            hs=(Hs[1][2]-Hs[0][2])/Hs[0][2]*100/max(1,Hs[1][1]-Hs[0][1])*10   # 每 10 根 %
            ls=(Ls[1][2]-Ls[0][2])/Ls[0][2]*100/max(1,Ls[1][1]-Ls[0][1])*10
            hd=Hs[1][2]>Hs[0][2]; ld=Ls[1][2]>Ls[0][2]
            cat='雙升' if hd and ld else '雙降' if (not hd and not ld) else '收斂' if (not hd and ld) else '擴張'
            # 延伸線在這根的位置
            hl=Hs[1][2]+(Hs[1][2]-Hs[0][2])/max(1,Hs[1][1]-Hs[0][1])*(i-Hs[1][1])
            ll=Ls[1][2]+(Ls[1][2]-Ls[0][2])/max(1,Ls[1][1]-Ls[0][1])*(i-Ls[1][1])
            ent=o[i+1]; r30=(c[min(len(c)-1,i+30)]-ent)/ent*100
            ft={sd: first_touch(h,l,ent,i+1,sd) for sd in (1,-1)}
            row=dict(day=d[5:],name=s.get('name'),t=tm[q],cat=cat,hs=hs,ls=ls,r30=r30,ftL=ft[1],ftS=ft[-1],
                     org=org.get(q,0), brkdn=bool(c[i]<ll and c[i-1]>=ll-(ll-ll)), brkup=bool(c[i]>hl))
            # 跌破低點延伸線 / 突破高點延伸線:這根才發生(前一根還沒)
            pl=Ls[1][2]+(Ls[1][2]-Ls[0][2])/max(1,Ls[1][1]-Ls[0][1])*(i-1-Ls[1][1]); ph=Hs[1][2]+(Hs[1][2]-Hs[0][2])/max(1,Hs[1][1]-Hs[0][1])*(i-1-Hs[1][1])
            row['brkdn']=bool(c[i]<ll and c[i-1]>=pl); row['brkup']=bool(c[i]>hl and c[i-1]<=ph)
            # 2026-10-20 使用者:「也可以看高線與低線的距離 放遠或縮近」:兩條延伸線在這根的距離(% of 價),和 10 根前比
            row['w']=(hl-ll)/c[i]*100; row['pos']=(c[i]-ll)/(hl-ll) if hl>ll else np.nan
            row['key']=(d,s['code']); row['q']=q
            base.append(row)
df=pd.DataFrame(base)
df['w10']=df.groupby('key')['w'].shift(10); df['dw']=df.w-df.w10
df.drop(columns=['key']).to_csv(f'{S}/results/swing_lines.csv',index=False)
def show(g,lab,sd):
    if not len(g): return
    ft=g.ftS if sd==-1 else g.ftL; w=(ft==1).sum(); L_=(ft==-1).sum()
    print(f"  {lab:24s} {len(g):6d} 次  先順向 1% {w*100//max(1,w+L_):3d}%(順 {w} / 逆 {L_})  30 根後{'跌' if sd==-1 else '漲'} 平均 {(-g.r30 if sd==-1 else g.r30).mean():+.2f}%")
print('== 每一根(基準):做空 / 做多')
show(df,'全部 做空',-1); show(df,'全部 做多',1)
print('== 依型態(每一根)')
for cat in ('雙降','雙升','收斂','擴張'):
    g=df[df.cat==cat]; show(g,f'{cat} 做空',-1); show(g,f'{cat} 做多',1)
print('== 依斜率(高低兩條線都往下 / 往上,每 10 根 %)')
for a,b in ((-9,-0.3),(-0.3,-0.1),(-0.1,0),(0,0.1),(0.1,0.3),(0.3,9)):
    g=df[(df.hs>=a)&(df.hs<b)&(df.ls>=a)&(df.ls<b)]
    show(g,f'兩條都在 {a}~{b}% 做空',-1); show(g,f'兩條都在 {a}~{b}% 做多',1)
print('== 起跌 / 起漲點 × 型態')
for cat in ('雙降','雙升','收斂','擴張'):
    show(df[(df.org==-1)&(df.cat==cat)],f'起跌 + {cat}',-1); show(df[(df.org==1)&(df.cat==cat)],f'起漲 + {cat}',1)
show(df[df.org==-1],'起跌 全部',-1); show(df[df.org==1],'起漲 全部',1)
print('== 跌破低點延伸線(做空)/ 突破高點延伸線(做多)')
show(df[df.brkdn],'跌破低點線 做空',-1); show(df[df.brkup],'突破高點線 做多',1)
for cat in ('雙降','雙升','收斂','擴張'):
    show(df[df.brkdn&(df.cat==cat)],f'跌破低點線 + {cat}',-1); show(df[df.brkup&(df.cat==cat)],f'突破高點線 + {cat}',1)

print('== 高線與低線的距離(延伸線在這根的寬度,% of 價)')
for a,b in ((-99,0),(0,0.5),(0.5,1),(1,2),(2,99)):
    g=df[(df.w>=a)&(df.w<b)]; show(g,f'寬 {a}~{b}% 做空',-1); show(g,f'寬 {a}~{b}% 做多',1)
print('== 距離和 10 根前比:放遠 / 縮近')
for a,b,lab in ((-99,-0.5,'縮近 >0.5%'),(-0.5,-0.1,'縮近 0.1~0.5%'),(-0.1,0.1,'差不多'),(0.1,0.5,'放遠 0.1~0.5%'),(0.5,99,'放遠 >0.5%')):
    g=df[(df.dw>=a)&(df.dw<b)]; show(g,f'{lab} 做空',-1); show(g,f'{lab} 做多',1)
print('== 起跌 / 起漲 × 距離變化')
for a,b,lab in ((-99,-0.1,'縮近'),(-0.1,0.1,'差不多'),(0.1,99,'放遠')):
    g=df[(df.dw>=a)&(df.dw<b)]
    show(g[g.org==-1],f'起跌 + {lab}',-1); show(g[g.org==1],f'起漲 + {lab}',1)
print('== 起跌 + 雙降 × 距離變化')
for a,b,lab in ((-99,-0.1,'縮近'),(-0.1,0.1,'差不多'),(0.1,99,'放遠')):
    g=df[(df.dw>=a)&(df.dw<b)&(df.cat=='雙降')]; show(g[g.org==-1],f'起跌 + 雙降 + {lab}',-1)
print('== 價在兩線之間的位置(0 = 低線、1 = 高線)')
for a,b in ((-99,0),(0,0.33),(0.33,0.67),(0.67,1),(1,99)):
    g=df[(df.pos>=a)&(df.pos<b)]; show(g,f'位置 {a}~{b} 做空',-1); show(g,f'位置 {a}~{b} 做多',1)
