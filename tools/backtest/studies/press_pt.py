# 「壓線空點」研究(2026-10-20 使用者看光鼎 09:49 元大圖:「這種壓在線上 指標全部向下 就是最漂亮的空點」)
# 條件:均價 10 向下;這根(或前一根)最高碰到均價 10(距離 ≤ TOUCH%),收在均價 10 之下且收黑;OBV < 藍線、藍線向下、紫線向下
import json, csv, glob, numpy as np, collections, sys, os
exec(open('obv_mean10.py',encoding='utf-8').read().split("stat=collections")[0])
TOUCH=float(sys.argv[1]) if len(sys.argv)>1 else 0.15   # 最高價距均價 10 的百分比
BLACK=int(sys.argv[2]) if len(sys.argv)>2 else 0         # 1 = 只做價在今開之下(黑盤)
DROP=float(sys.argv[3]) if len(sys.argv)>3 else 0.0      # 前 20 根高點到現在至少跌多少 %(反彈前要有一段跌)
def fwd(c,h,l,i,n,th):
    e=c[i]
    for j in range(i+1,min(len(c),i+n+1)):
        hi=(h[j]-e)/e*100; lo=(l[j]-e)/e*100
        if hi>=th and lo<=-th: return 0
        if hi>=th: return 1
        if lo<=-th: return -1
    return 0
st=collections.defaultdict(float); base=collections.defaultdict(float); trades=[]; daily=collections.defaultdict(lambda:[0,0,0])
for d,pool in DAYS.items():
    P=json.load(open(pool,encoding='utf-8'))
    for s in P['pool']:
        B=bars(s['code'],d)
        if len(B)<60: continue
        c=np.array([b[4] for b in B]); h=np.array([b[2] for b in B]); l=np.array([b[3] for b in B]); v=np.array([b[5] for b in B]); o=np.array([b[1] for b in B])
        sign=np.sign(np.diff(c,prepend=c[0])); ob=np.cumsum(sign*v); ma10=expmean(ob,10); ma30=expmean(ob,30); pma=expmean(c,10)
        dayopen=o[0]
        done=False
        for i in range(30,len(B)-31):
            r=fwd(c,h,l,i,30,1); base['n']+=1; base['dn']+=r==-1; base['up']+=r==1
            maDown=pma[i]<pma[i-1]<pma[i-2]
            touch=max(h[i],h[i-1])>=pma[i]*(1-TOUCH/100)
            under=c[i]<pma[i] and c[i]<o[i]
            obvDown=ob[i]<ma10[i] and ma10[i]<ma10[i-1] and ma30[i]<ma30[i-1]
            black=(c[i]<dayopen) if BLACK else True
            drop=((h[i-20:i].max()-c[i])/c[i]*100>=DROP) if DROP>0 else True
            if maDown and touch and under and obvDown and black and drop:
                st['n']+=1; st['dn']+=r==-1; st['up']+=r==1; st['f30']+=(c[i+30]-c[i])/c[i]*100
                if not done and '09:15'<=B[i][0]<='12:00':
                    done=True; ent=o[i+1]; lots=int(350000//(ent*1000))
                    if lots==0: continue
                    ex=None; why=''
                    for j in range(i+1,len(B)):
                        if B[j][0]>='13:25': ex=c[j]; why='13:25'; break
                        if (h[j]-ent)/ent*100>=1.5: ex=ent*1.015; why='停損'; break
                        if (l[j]-ent)/ent*100<=-2.5: ex=ent*0.975; why='停利'; break
                    if ex is None: ex=c[-1]; why='收盤'
                    net=(ent-ex)*lots*1000 - ent*lots*1000*0.001425*0.28*2 - ent*lots*1000*0.0015
                    trades.append((d[5:],s.get('name'),B[i][0],round(ent,2),lots,round(ex,2),why,int(net)))
                    daily[d[5:]][0]+=1; daily[d[5:]][1]+= net>0; daily[d[5:]][2]+=net
print(f'碰線 ≤ {TOUCH}%、黑盤限制 {BLACK}、前 20 根跌幅 ≥ {DROP}%')
print(f"全部 K 基準:30 分內先-1% {base['dn']/base['n']*100:.1f}%  先+1% {base['up']/base['n']*100:.1f}%")
print(f"訊號 {int(st['n'])} 次:30 分內先-1% {st['dn']/max(1,st['n'])*100:.1f}%  先+1% {st['up']/max(1,st['n'])*100:.1f}%  30 分後均 {st['f30']/max(1,st['n']):+.3f}%")
tot=len(trades); win=sum(1 for t in trades if t[7]>0); net=sum(t[7] for t in trades)
print(f'模擬做空(每檔每天第一次訊號,09:15~12:00,停利 2.5 停損 1.5,13:25 平倉,35 萬):{tot} 筆 {win} 賺 {tot-win} 賠 勝率 {win*100//max(1,tot)}% 淨 {net:+,}')
for d in sorted(daily): print(' ',d,daily[d][0],'筆',daily[d][1],'賺',f'{daily[d][2]:+,.0f}')
if '-v' in sys.argv:
    for t in trades: print('  ',*t)
