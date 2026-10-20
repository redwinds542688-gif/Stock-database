# 「橫盤後帶量破底」起跌點(2026-10-20 使用者友達 11:39 截圖):前 N 根價幅 ≤ FLAT%、這根收盤跌破前 N 根最低、量 ≥ 前 N 根平均 VOLX 倍、紅線跌破藍線(或已在藍下)
import json, csv, glob, numpy as np, collections, sys
exec(open('obv_mean10.py',encoding='utf-8').read().split("stat=collections")[0])
N=int(sys.argv[1]) if len(sys.argv)>1 else 15; FLAT=float(sys.argv[2]) if len(sys.argv)>2 else 0.5; VOLX=float(sys.argv[3]) if len(sys.argv)>3 else 2.0; OBX=int(sys.argv[4]) if len(sys.argv)>4 else 1
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
        prev=s.get('prev') or s.get('prev_close') or s.get('close')
        done=False
        for i in range(N+1,len(B)-31):
            w=slice(i-N,i)
            flat=(h[w].max()-l[w].min())/c[i]*100<=FLAT
            brk=c[i]<l[w].min()
            bigv=v[i]>=VOLX*max(v[w].mean(),1e-9)
            obx = (ob[i]<ma10[i]) and (ob[i-1]>=ma10[i-1] if OBX==1 else True)
            if not (flat and brk and bigv and obx): continue
            pct=(c[i]-prev)/prev*100 if prev else 0
            r=fwd(c,h,l,i,30,1); st['n']+=1; st['dn']+=r==-1; st['up']+=r==1
            if done or not ('09:15'<=B[i][0]<='12:00') or abs(pct)>3.5 or pct>3: continue
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
tot=len(trades); win=sum(1 for t in trades if t[7]>0); net=sum(t[7] for t in trades)
print(f'前 {N} 根價幅 ≤ {FLAT}%、破底、量 ≥ {VOLX} 倍、{"紅線剛跌破藍線" if OBX else "紅線在藍下"}:訊號 {int(st["n"])} 次 30 分內先跌 1% {st["dn"]/max(1,st["n"])*100:.1f}% 先漲 1% {st["up"]/max(1,st["n"])*100:.1f}% | 做空 {tot} 筆 {win} 賺 勝率 {win*100//max(1,tot)}% 淨 {net:+,}')
if '-v' in sys.argv:
    for t in trades: print('  ',*t)
