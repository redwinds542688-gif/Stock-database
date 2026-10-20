# 壓線空點的鏡像「撐線多點」(2026-10-20 使用者:「訊號完全相反 也可以做多」):均價 10 向上、回檔碰均價被撐住收紅、紅 > 藍 > 紫且藍紫向上、OBV 創 30 根新高而價未破高、不追多、開平不做
import json, csv, glob, numpy as np, collections, sys, os
exec(open('obv_mean10.py',encoding='utf-8').read().split("stat=collections")[0])
TOUCH=0.15; CH=float(sys.argv[1]) if len(sys.argv)>1 else 2.0; RV=1.2; LO=0.5; GAP=float(sys.argv[2]) if len(sys.argv)>2 else 1.0; NL=30
def fwd(c,h,l,i,n,th):
    e=c[i]
    for j in range(i+1,min(len(c),i+n+1)):
        hi=(h[j]-e)/e*100; lo=(l[j]-e)/e*100
        if hi>=th and lo<=-th: return 0
        if hi>=th: return 1
        if lo<=-th: return -1
    return 0
st=collections.defaultdict(float); trades=[]; daily=collections.defaultdict(lambda:[0,0,0])
for d,pool in DAYS.items():
    P=json.load(open(pool,encoding='utf-8'))
    for s in P['pool']:
        B=bars(s['code'],d)
        if len(B)<60: continue
        c=np.array([b[4] for b in B]); h=np.array([b[2] for b in B]); l=np.array([b[3] for b in B]); v=np.array([b[5] for b in B]); o=np.array([b[1] for b in B])
        sign=np.sign(np.diff(c,prepend=c[0])); ob=np.cumsum(sign*v); ma10=expmean(ob,10); ma30=expmean(ob,30); pma=expmean(c,10)
        prev=s.get('prev') or s.get('prev_close') or s.get('close')
        gapok = (not prev) or GAP<=0 or abs(o[0]-prev)/prev*100>GAP
        done=False
        for i in range(NL,len(B)-31):
            maUp=pma[i]>pma[i-1]>pma[i-2]
            touch=min(l[i],l[i-1])<=pma[i]*(1+TOUCH/100)
            over=c[i]>pma[i] and c[i]>o[i]
            obvUp=ob[i]>ma10[i]>ma30[i] and ma10[i]>ma10[i-1] and ma30[i]>ma30[i-1]
            newhi=ob[i]>=ob[i-NL:i].max() and c[i]<h[i-NL:i].max()*0.997
            offlo=(c[i]-l[:i+1].min())/c[i]*100; offhi=(h[:i+1].max()-c[i])/c[i]*100
            rv=v[max(0,i-10):i+1].mean()/max(v[:i+1].mean(),1e-9)
            if maUp and touch and over and obvUp and newhi and offlo<=CH and offhi>=LO and rv<RV and gapok:
                r=fwd(c,h,l,i,30,1); st['n']+=1; st['up']+=r==1; st['dn']+=r==-1
                if not done and '09:15'<=B[i][0]<='12:00':
                    done=True; ent=o[i+1]; lots=int(350000//(ent*1000))
                    if lots==0: continue
                    ex=None; why=''
                    for j in range(i+1,len(B)):
                        if B[j][0]>='13:25': ex=c[j]; why='13:25'; break
                        if (l[j]-ent)/ent*100<=-1.5: ex=ent*0.985; why='停損'; break
                        if (h[j]-ent)/ent*100>=2.5: ex=ent*1.025; why='停利'; break
                    if ex is None: ex=c[-1]; why='收盤'
                    net=(ex-ent)*lots*1000 - ent*lots*1000*0.001425*0.28*2 - ex*lots*1000*0.0015
                    trades.append((d[5:],s.get('name'),B[i][0],round(ent,2),lots,round(ex,2),why,int(net)))
                    daily[d[5:]][0]+=1; daily[d[5:]][1]+= net>0; daily[d[5:]][2]+=net
print(f'撐線多點:不追多 離今低 ≤ {CH}%、開平 ±{GAP}% 不做')
print(f"訊號 {int(st['n'])} 次:30 分內先+1% {st['up']/max(1,st['n'])*100:.1f}%  先-1% {st['dn']/max(1,st['n'])*100:.1f}%")
tot=len(trades); win=sum(1 for t in trades if t[7]>0); net=sum(t[7] for t in trades)
print(f'模擬做多(每檔每天第一次,09:15~12:00,停利 2.5 停損 1.5,13:25 平倉):{tot} 筆 {win} 賺 {tot-win} 賠 勝率 {win*100//max(1,tot)}% 淨 {net:+,}')
for d in sorted(daily): print(' ',d,daily[d][0],'筆',daily[d][1],'賺',f'{daily[d][2]:+,.0f}')
if '-v' in sys.argv:
    for t in trades: print('  ',*t)
