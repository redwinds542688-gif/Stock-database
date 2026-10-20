# 判四「壓線空點」參數 / 濾網搜尋(2026-10-20 使用者:「既然要做空 那就把它修正讓勝率跟贏的金額增加」)
# 和模擬器一致的部分:均價 / OBV 均線用滾動 10 / 30(前面算不出來不看)、離昨收 ±3.5% 與 +3% 不做空、09:15~12:00 進場、停利 2.5 停損 1.5、13:25 平倉、每檔每天第一筆
import json, csv, glob, numpy as np, collections, sys, os, itertools
exec(open('obv_mean10.py',encoding='utf-8').read().split("stat=collections")[0])
def roll(a,n):
    out=np.full(len(a),np.nan); cs=np.cumsum(np.insert(a,0,0.0))
    for i in range(n-1,len(a)): out[i]=(cs[i+1]-cs[i+1-n])/n
    return out
DATA=[]
for d,pool in DAYS.items():
    P=json.load(open(pool,encoding='utf-8'))
    for s in P['pool']:
        B=bars(s['code'],d)
        if len(B)<60: continue
        c=np.array([b[4] for b in B]); h=np.array([b[2] for b in B]); l=np.array([b[3] for b in B]); v=np.array([b[5] for b in B]); o=np.array([b[1] for b in B])
        sign=np.sign(np.diff(c,prepend=c[0])); ob=np.cumsum(sign*v)
        prev=s.get('prev') or s.get('prev_close') or s.get('close')
        if not prev: continue
        vw=np.cumsum(c*v)/np.maximum(np.cumsum(v),1)
        DATA.append(dict(d=d[5:],name=s.get('name'),t=[b[0] for b in B],c=c,h=h,l=l,v=v,o=o,ob=ob,pma=roll(c,10),m10=roll(ob,10),m30=roll(ob,30),prev=prev,vw=vw,
                         cumv=np.cumsum(v),hmax=np.maximum.accumulate(h),lmin=np.minimum.accumulate(l)))
BASE=dict(TOUCH=0.15,CH=2.0,RV=1.2,LO=0.5,GAP=1.0,NL=30,NLM=0.3,T0='09:15',T1='12:00',BLACK=0,VWAP=0,SLOPE=0.0,OPEN='any',MAX=3.5,TRD=3.0,UNDER=0.0,DROP=0.0,OBSL=0,BAR=0.0,TP=2.5,SL=1.5)
def run(P, verbose=False):
    n=0; up=0; dn=0; trades=[]
    for D in DATA:
        c,h,l,v,o,ob,pma,m10,m30,prev,vw,t=D['c'],D['h'],D['l'],D['v'],D['o'],D['ob'],D['pma'],D['m10'],D['m30'],D['prev'],D['vw'],D['t']
        gap=(o[0]-prev)/prev*100
        if P['GAP']>0 and abs(gap)<=P['GAP']: continue
        if P['OPEN']=='low' and gap>=0: continue
        if P['OPEN']=='high' and gap<=0: continue
        done=False
        for i in range(max(P['NL'],30),len(c)-1):
            if t[i]<P['T0'] or t[i]>P['T1']: continue
            if np.isnan(m30[i-1]) or np.isnan(pma[i-2]): continue
            if not (pma[i]<pma[i-1]<pma[i-2]): continue
            if not (max(h[i],h[i-1])>=pma[i]*(1-P['TOUCH']/100)): continue
            if not (c[i]<pma[i] and c[i]<o[i]): continue
            if P['UNDER']>0 and (pma[i]-c[i])/pma[i]*100<P['UNDER']: continue
            if not (ob[i]<m10[i]<m30[i] and m10[i]<m10[i-1] and m30[i]<m30[i-1]): continue
            if not (ob[i]<=np.min(ob[i-P['NL']:i]) and c[i]>np.min(l[i-P['NL']:i])*(1+P['NLM']/100)): continue
            offhi=(D['hmax'][i]-c[i])/c[i]*100; offlo=(c[i]-D['lmin'][i])/c[i]*100
            if offhi>P['CH'] or offlo<P['LO']: continue
            rv=np.mean(v[max(0,i-10):i+1])/max(D['cumv'][i]/(i+1),1e-9)
            if rv>=P['RV']: continue
            pct=(c[i]-prev)/prev*100
            if abs(pct)>P['MAX'] or pct>P['TRD']: continue
            if P['BLACK']==1 and c[i]>=o[0]: continue
            if P['BLACK']==2 and c[i]<o[0]: continue
            if P['VWAP']==1 and c[i]>=vw[i]: continue
            if P['SLOPE']>0 and (pma[i-5]-pma[i])/pma[i]*100<P['SLOPE']: continue
            if P['DROP']>0 and (np.max(h[max(0,i-20):i])-c[i])/c[i]*100<P['DROP']: continue
            if P['OBSL']>0 and not all(ob[i-j]<ob[i-j-1] for j in range(P['OBSL'])): continue
            if P['BAR']>0 and (o[i]-c[i])/c[i]*100<P['BAR']: continue
            # 30 分統計
            e=c[i]; r=0
            for j in range(i+1,min(len(c),i+31)):
                hi_=(h[j]-e)/e*100; lo_=(l[j]-e)/e*100
                if hi_>=1 and lo_<=-1: break
                if hi_>=1: r=1; break
                if lo_<=-1: r=-1; break
            n+=1; up+=r==1; dn+=r==-1
            if done: continue
            done=True; ent=o[i+1]; lots=int(350000//(ent*1000))
            if lots==0: continue
            ex=None; why=''
            for j in range(i+1,len(c)):
                if t[j]>='13:25': ex=c[j]; why='13:25'; break
                if (h[j]-ent)/ent*100>=P['SL']: ex=ent*(1+P['SL']/100); why='停損'; break
                if (l[j]-ent)/ent*100<=-P['TP']: ex=ent*(1-P['TP']/100); why='停利'; break
            if ex is None: ex=c[-1]; why='收盤'
            net=(ent-ex)*lots*1000 - ent*lots*1000*0.001425*0.28*2 - ent*lots*1000*0.0015
            trades.append((D['d'],D['name'],t[i],round(ent,2),lots,round(ex,2),why,int(net)))
    tot=len(trades); win=sum(1 for x in trades if x[7]>0); net=sum(x[7] for x in trades)
    if verbose:
        for x in trades: print('   ',*x)
    return dict(sig=n,dn=dn/max(1,n)*100,up=up/max(1,n)*100,n=tot,win=win,wr=win*100//max(1,tot),net=net)
def show(label,P,verbose=False):
    r=run(P,verbose); print(f"{label:<34} 訊號 {r['sig']:>4} 先跌 {r['dn']:4.1f}% 先漲 {r['up']:4.1f}% | {r['n']:>3} 筆 {r['win']:>2} 賺 勝率 {r['wr']:>3}% 淨 {r['net']:+,}"); return r
if __name__=='__main__':
    show('基準(判四現行)',BASE, '-v' in sys.argv)
    V=[('NL',[20,40,60]),('NLM',[0.0,0.6,1.0]),('TOUCH',[0.05,0.3,0.5]),('CH',[1.0,1.5,3.0]),('LO',[0.3,1.0,1.5]),('RV',[0.8,1.0,1.5,2.0]),('GAP',[0.0,0.5,2.0]),
       ('T1',['10:00','10:30','11:00']),('T0',['09:30','09:45']),('BLACK',[1,2]),('VWAP',[1]),('SLOPE',[0.2,0.4]),('OPEN',['low','high']),('UNDER',[0.2,0.4]),('DROP',[0.5,1.0]),('OBSL',[2,3]),('BAR',[0.2,0.4]),
       ('TP',[2.0,3.0]),('SL',[1.0,2.0])]
    for k,vals in V:
        for val in vals:
            P=dict(BASE); P[k]=val; show(f'{k}={val}',P)
