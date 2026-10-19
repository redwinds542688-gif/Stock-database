import json, csv, glob, numpy as np, collections, sys
exec(open('obv_mean10.py',encoding='utf-8').read().split("stat=collections")[0])
VOLX=float(sys.argv[1]) if len(sys.argv)>1 else 2.0; VWIN=int(sys.argv[2]) if len(sys.argv)>2 else 3; XWIN=int(sys.argv[3]) if len(sys.argv)>3 else 2
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
        sign=np.sign(np.diff(c,prepend=c[0])); ob=np.cumsum(sign*v); ma10=expmean(ob,10); ma30=expmean(ob,30)
        done=False
        for i in range(20,len(B)-2):
            rng=max(ob[:i+1].max()-ob[:i+1].min(),1); w=slice(i-20,i)
            flat=(h[w].max()-l[w].min())/c[i]*100<=1.0
            tight=(np.abs(ob[i-10:i]-ma10[i-10:i]).mean()+np.abs(ma10[i-10:i]-ma30[i-10:i]).mean())/rng*100<3
            crossBP=any(ma10[k]>ma30[k] and ma10[k-1]<=ma30[k-1] for k in range(max(1,i-XWIN),i+1))   # 藍線在最近 XWIN+1 根內上穿紫線
            redUp=ob[i]>ma10[i] and ma10[i]>ma30[i]
            bigv=v[i-VWIN+1:i+1].max()>=VOLX*max(v[w].mean(),1)
            brk=c[i]>=h[w].max()
            if (flat or tight) and crossBP and redUp and bigv and brk:
                if i+30<len(B):
                    h30=fwd(c,h,l,i,30,1); st['n']+=1; st['up']+=h30==1; st['dn']+=h30==-1; st['f30']+=(c[i+30]-c[i])/c[i]*100
                if not done and '09:15'<=B[i][0]<='12:00':
                    done=True; ent=o[i+1]; lots=int(350000//(ent*1000)) or 0
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
print(f'量 ≥ {VOLX} 倍(最近 {VWIN} 根內)、藍紫交叉在最近 {XWIN+1} 根內')
print(f"訊號 {int(st['n'])} 次:30 分內先+1% {st['up']/st['n']*100:.1f}%  先-1% {st['dn']/st['n']*100:.1f}%  30 分後均 {st['f30']/st['n']:+.3f}%")
tot=len(trades); win=sum(1 for t in trades if t[7]>0); net=sum(t[7] for t in trades)
print(f'模擬交易(每檔每天第一次訊號,09:15~12:00,停利 2.5 停損 1.5,13:25 平倉,35 萬):{tot} 筆 {win} 賺 {tot-win} 賠 勝率 {win*100//max(1,tot)}% 淨 {net:+,}')
for d in sorted(daily): print(' ',d,daily[d][0],'筆',daily[d][1],'賺',f'{daily[d][2]:+,.0f}')
for t in trades: print('  ',*t)
