import json, csv, glob, numpy as np, collections
exec(open('obv_mean10.py',encoding='utf-8').read().split("stat=collections")[0])
def fwd(c,h,l,i,n,th):
    e=c[i]
    for j in range(i+1,min(len(c),i+n+1)):
        hi=(h[j]-e)/e*100; lo=(l[j]-e)/e*100
        if hi>=th and lo<=-th: return 0
        if hi>=th: return 1
        if lo<=-th: return -1
    return 0
T=collections.defaultdict(lambda: collections.defaultdict(float)); base=collections.defaultdict(float)
def add(x,f10,f30,h10,h30): x['n']+=1; x['f10']+=f10; x['f30']+=f30; x['up10']+=h10==1; x['dn10']+=h10==-1; x['up30']+=h30==1; x['dn30']+=h30==-1
for d,pool in DAYS.items():
    P=json.load(open(pool,encoding='utf-8'))
    for s in P['pool']:
        B=bars(s['code'],d)
        if len(B)<60: continue
        c=np.array([b[4] for b in B]); h=np.array([b[2] for b in B]); l=np.array([b[3] for b in B]); v=np.array([b[5] for b in B])
        sign=np.sign(np.diff(c,prepend=c[0])); ob=np.cumsum(sign*v); ma10=expmean(ob,10); ma30=expmean(ob,30)
        for i in range(20,len(B)-31):
            f10=(c[i+10]-c[i])/c[i]*100; f30=(c[i+30]-c[i])/c[i]*100; h10=fwd(c,h,l,i,10,1); h30=fwd(c,h,l,i,30,1)
            add(base,f10,f30,h10,h30)
            rng=max(ob[:i+1].max()-ob[:i+1].min(),1)
            w=slice(i-20,i); va=v[w].mean(); bigv=v[i]>=2*max(va,1)
            brkU=c[i]>=h[w].max(); brkD=c[i]<=l[w].min()
            tight=(np.abs(ob[i-10:i]-ma10[i-10:i]).mean()+np.abs(ma10[i-10:i]-ma30[i-10:i]).mean())/rng*100<3   # 之前 10 根三線黏在一起
            pr=(h[w].max()-l[w].min())/c[i]*100; flat=pr<=1.0   # 之前 20 根價幅 ≤ 1%
            xU=ma10[i]>ma30[i] and ma10[i-1]<=ma30[i-1] and ob[i]>ma10[i]     # 藍線剛上穿紫線、紅在藍上
            xD=ma10[i]<ma30[i] and ma10[i-1]>=ma30[i-1] and ob[i]<ma10[i]
            if xU:
                add(T['多 A:藍上穿紫+紅在藍上'],f10,f30,h10,h30)
                if bigv: add(T['多 B:A + 量 ≥ 前20均 2 倍'],f10,f30,h10,h30)
                if brkU: add(T['多 C:A + 收破前 20 根高'],f10,f30,h10,h30)
                if bigv and brkU: add(T['多 D:A + 量 + 破高(燿華型)'],f10,f30,h10,h30)
                if bigv and brkU and (tight or flat): add(T['多 E:D + 之前盤整(三線黏/價幅≤1%)'],f10,f30,h10,h30)
            if xD:
                add(T['空 A:藍下穿紫+紅在藍下'],f10,f30,h10,h30)
                if bigv: add(T['空 B:A + 量 ≥ 2 倍'],f10,f30,h10,h30)
                if brkD: add(T['空 C:A + 收破前 20 根低'],f10,f30,h10,h30)
                if bigv and brkD: add(T['空 D:A + 量 + 破低(台玻型)'],f10,f30,h10,h30)
                if brkD and (ob[i]-ob[i-1])/rng*100<-2: add(T['空 F:A + 破低 + 紅線單根掉 ≥ 2% 範圍'],f10,f30,h10,h30)
            # 不要求藍紫交叉,只看 量+破高/低
            if bigv and brkU and ob[i]>ma10[i]>ma30[i]: add(T['多 G:量+破高+三線多頭(不限剛交叉)'],f10,f30,h10,h30)
            if bigv and brkD and ob[i]<ma10[i]<ma30[i]: add(T['空 G:量+破低+三線空頭(不限剛交叉)'],f10,f30,h10,h30)
def row(lab,x):
    n=x['n']; print(f"{lab:36} {int(n):7d} {x['f10']/n:+7.3f} {x['f30']/n:+7.3f} {x['up10']/n*100:7.1f} {x['dn10']/n*100:7.1f} {x['up30']/n*100:7.1f} {x['dn30']/n*100:7.1f}")
print(f"{'型態':36} {'樣本':>7} {'10分後':>7} {'30分後':>7} {'10分內+1%':>7} {'-1%':>7} {'30分內+1%':>7} {'-1%':>7}")
row('全部(基準)',base)
for lab in sorted(T): row(lab,T[lab])
