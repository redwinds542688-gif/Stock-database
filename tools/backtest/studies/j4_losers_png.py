# 2026-10-20 判四輸單圖:K + 均價10(紫)+ VWAP(橘虛線)+ 昨收(白虛線),OBV(黃)+ 均10(藍)+ 均30(紫);▼ 進場、● 出場、紅虛線 = 停損
import pandas as pd, numpy as np, glob, matplotlib; matplotlib.use('Agg')
import matplotlib.pyplot as plt; from matplotlib import font_manager as fm
fm.fontManager.addfont('/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc'); plt.rcParams['font.family']='WenQuanYi Zen Hei'; plt.rcParams['axes.unicode_minus']=False
R='/home/user/Stock-database'; OUT=f'{R}/tools/backtest/results/shots'
L=[('2026-10-12','2472','立隆電','09:31','11:25',-4592,'停損 1.5%'),('2026-10-16','3518','柏騰','09:47','10:06',-8817,'停損 1.5%'),
   ('2026-10-19','3062','建漢','09:34','13:25',-1576,'13:25 平倉'),('2026-10-19','5465','富驊','10:26','11:54',-5752,'停損 1.5%')]
for d,code,nm,tin,tout,net,why in L:
    df=pd.concat([pd.read_csv(f,dtype={'date':str,'time':str}) for f in glob.glob(f'{R}/kline/{code}/2026-W4*.csv')])
    prev=float(df[df.date<d].sort_values(['date','time']).close.values[-1])
    t=df[(df.date==d)&(df.time>='09:00')&(df.time<='13:30')&(df.low>0)].drop_duplicates('time',keep='last').sort_values('time').reset_index(drop=True)
    c,o,h,l=t.close.values,t.open.values,t.high.values,t.low.values; v=t.volume.values/1000; x=np.arange(len(t))
    ob=np.concatenate([[0],np.cumsum(np.sign(np.diff(c))*v[1:])]); s=lambda a,n: pd.Series(a).rolling(n).mean().values
    ma=s(c,10); vw=np.cumsum((h+l+c)/3*v)/np.maximum(np.cumsum(v),1e-9); vw[np.cumsum(v)==0]=np.nan
    i=int(np.where(t.time.values==tin)[0][0]); ent=o[i+1]; j=int(np.where(t.time.values<=tout)[0][-1])
    fig,(a1,a2)=plt.subplots(2,1,figsize=(9,11),gridspec_kw={'height_ratios':[2.2,1]},sharex=True,facecolor='black')
    for a in (a1,a2): a.set_facecolor('black'); a.tick_params(colors='#ccc',labelsize=11); [sp.set_color('#444') for sp in a.spines.values()]; a.grid(color='#222')
    for k in x:
        col='#ff3b3b' if c[k]>=o[k] else '#22c55e'
        a1.vlines(k,l[k],h[k],color=col,lw=0.8); a1.add_patch(plt.Rectangle((k-0.35,min(o[k],c[k])),0.7,max(abs(c[k]-o[k]),1e-6),color=col))
    a1.plot(x,ma,color='#c084fc',lw=1.2,label='均價10'); a1.plot(x,vw,'--',color='#fb923c',lw=1,label='VWAP'); a1.axhline(prev,color='#aaa',ls=':',lw=1,label=f'昨收 {prev:g}')
    a1.axhline(ent*1.015,color='#ff3b3b',ls='--',lw=0.8,label=f'停損 {ent*1.015:.2f}')
    a1.scatter([i+1],[h[i+1]*1.003],marker='v',s=140,color='#22d3ee',zorder=5); a1.annotate(f'空 {tin}\n{ent:g}',(i+1,h[i+1]*1.004),color='#22d3ee',fontsize=12,ha='center',va='bottom')
    a1.scatter([j],[c[j]],marker='o',s=90,color='white',zorder=5); a1.annotate(f'{why} {tout}',(j,c[j]),color='white',fontsize=12,xytext=(5,-18),textcoords='offset points')
    a1.set_ylim(min(l.min(), ent)*0.995, max(h.max(), ent*1.015)*1.005)
    a1.legend(loc='upper right',fontsize=10,facecolor='#111',labelcolor='#ddd'); a1.set_title(f'判四 輸 {nm} {code} {d}  淨 {net:+,}',color='white',fontsize=15)
    a2.plot(x,ob,color='#FFE14D',lw=1.1,label='OBV'); a2.plot(x,s(ob,10),color='#22d3ee',lw=1.1,label='均10'); a2.plot(x,s(ob,30),color='#c084fc',lw=1.1,label='均30')
    a2.axvline(i,color='#22d3ee',ls=':',lw=1); a2.legend(loc='upper right',fontsize=10,facecolor='#111',labelcolor='#ddd')
    tk=[k for k in x if t.time.values[k].endswith(':00') or t.time.values[k].endswith(':30')]; a2.set_xticks(tk); a2.set_xticklabels([t.time.values[k] for k in tk])
    plt.tight_layout(); fig.savefig(f'{OUT}/判四輸_{nm}_{d[5:7]}{d[8:]}.png',dpi=90,facecolor='black'); plt.close(fig); print('ok',nm)
