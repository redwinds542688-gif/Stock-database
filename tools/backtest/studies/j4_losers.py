# 2026-10-20 使用者:「看一下 判四 最近輸的」:判四(現行 30 根震盪)九天 + 10/20 每筆進場點的特徵,輸贏對照
import pandas as pd, numpy as np, json, glob, sys, os
R='/home/user/Stock-database'
T=[('2026-10-12','2472','立隆電','09:31',-4592,'hist_2026-10-12'),('2026-10-12','8039','台虹','09:31',2749,'hist_2026-10-12'),
   ('2026-10-15','2344','華邦電','09:52',7106,'hi350_2026-10-15'),('2026-10-15','2313','華通','09:42',5329,'hi350_2026-10-15'),
   ('2026-10-16','3518','柏騰','09:47',-8817,'hi350_2026-10-16'),('2026-10-19','1326','台化','09:33',3647,'pool_morning'),
   ('2026-10-19','3605','宏致','09:34',7610,'pool_morning'),('2026-10-19','3062','建漢','09:34',-1576,'pool_morning'),
   ('2026-10-19','3663','鑫科','09:46',4161,'pool_morning'),('2026-10-19','2241','艾姆勒','09:43',7636,'pool_morning'),
   ('2026-10-19','5465','富驊','10:26',-5752,'pool_morning'),
   ('2026-10-20','1301','台塑','09:49',6926,'cloud'),('2026-10-20','4919','新唐','10:30',6222,'cloud'),('2026-10-20','6505','台塑化','10:28',7736,'cloud')]
def load(d,code,src):
    if src=='cloud':
        df=pd.concat([pd.read_csv(f,dtype={'date':str,'time':str}) for f in glob.glob(f'{R}/kline_cloud/{code}/*.csv')])
    else:
        df=pd.concat([pd.read_csv(f,dtype={'date':str,'time':str}) for f in glob.glob(f'{R}/kline/{code}/2026-W4*.csv')])
    prevK=float(df[df.date<d].sort_values(['date','time']).close.values[-1]) if len(df[df.date<d]) else None
    t=df[(df.date==d)&(df.time>='09:00')&(df.time<='13:30')].drop_duplicates('time',keep='last').sort_values('time').reset_index(drop=True)
    return t,prevK
for d,code,nm,tm,net,pool in T:
    try: t,prev=load(d,code,'cloud' if pool=='cloud' else 'y')
    except Exception as e: print(nm,'無資料',e); continue
    c=t.close.values; h=t.high.values; l=t.low.values; o=t.open.values; v=t.volume.values/1000
    ob=np.concatenate([[0],np.cumsum(np.sign(np.diff(c))*v[1:])]); obma=pd.Series(ob).rolling(10).mean().values; obma30=pd.Series(ob).rolling(30).mean().values
    ma=pd.Series(c).rolling(10).mean().values; vw=np.cumsum((h+l+c)/3*v)/np.maximum(np.cumsum(v),1e-9)
    if tm not in set(t.time): print(nm,'找不到',tm); continue
    i=int(np.where(t.time.values==tm)[0][0]); ent=o[i+1] if i+1<len(o) else c[i]
    hi=h[:i+1].max(); lo=l[:i+1].min()
    after=slice(i+1,min(len(c),i+31))
    mfe=(ent-l[after].min())/ent*100; mae=(h[after].max()-ent)/ent*100
    k10=(ma[i]-ma[i-5])/ma[i]*100 if i>=5 else np.nan
    dayrng=(hi-lo)/prev*100
    print(f"{'輸' if net<0 else '贏'} {d[5:]} {nm:4s} {tm} 淨{net:+6d} | 當日 {(c[i]-prev)/prev*100:+.2f}% 開 {(o[0]-prev)/prev*100:+.2f}% 離低 {(c[i]-lo)/lo*100:.2f}% 離高 {(hi-c[i])/hi*100:.2f}% 振幅 {dayrng:.2f}% | 均10 5根 {k10:+.2f}% VWAP {(c[i]-vw[i])/vw[i]*100:+.2f}% | 開盤至今 {i} 根 | 30根內 最多賺 {mfe:.2f}% 最多虧 {mae:.2f}% | 開盤後 OBV {ob[i]:+.0f} 量 {np.sum(v[:i+1]):.0f}")
