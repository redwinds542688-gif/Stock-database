import json, csv, glob, numpy as np, itertools, collections
exec(open('obv_mean10.py',encoding='utf-8').read().split("stat=collections")[0])
# 先把每個檔日的序列和「之後 30 分鐘先碰 +1% / −1%」算好
days=[]
for d,pool in DAYS.items():
    P=json.load(open(pool,encoding='utf-8'))
    for s in P['pool']:
        B=bars(s['code'],d)
        if len(B)<60: continue
        c=np.array([b[4] for b in B]); h=np.array([b[2] for b in B]); l=np.array([b[3] for b in B]); v=np.array([b[5] for b in B])
        sign=np.sign(np.diff(c,prepend=c[0])); ob=np.cumsum(sign*v); ma10=expmean(ob,10); ma30=expmean(ob,30)
        n=len(B); hit=np.zeros(n); f30=np.zeros(n)
        for i in range(n-30):
            e=c[i]; f30[i]=(c[i+30]-e)/e*100
            for j in range(i+1,i+31):
                hi=(h[j]-e)/e*100; lo=(l[j]-e)/e*100
                if hi>=1 and lo<=-1: break
                if hi>=1: hit[i]=1; break
                if lo<=-1: hit[i]=-1; break
        days.append((c,ob,ma10,ma30,hit,f30,B[0][1]))
print('檔日',len(days))
def roll(a,w):  # 前 w 根(不含當根)的平均,不足 w 用到目前的平均
    out=np.full(len(a),np.nan)
    cs=np.cumsum(np.insert(a,0,0))
    for i in range(1,len(a)):
        k=min(w,i); out[i]=(cs[i]-cs[i-k])/k
    return out
res=[]
for wr,wb,wp in itertools.product([1,3,5,10,15],[3,5,10,15,20],[5,10,15,20,30]):
    bull=[0,0,0,0.0]; bear=[0,0,0,0.0]; bullB=[0,0,0,0.0]; bearS=[0,0,0,0.0]
    for c,ob,ma10,ma30,hit,f30,o0 in days:
        R=roll(ob,wr); Bm=roll(ma10,wb); Pm=roll(ma30,wp)
        st=(R>Bm).astype(int)*2+(Bm>Pm).astype(int)   # 3 = 多頭排列, 0 = 空頭排列
        for i in range(12,len(c)-30):
            if st[i]==st[i-1]: continue
            if st[i]==3:
                bull[0]+=1; bull[1]+= hit[i]==1; bull[2]+= hit[i]==-1; bull[3]+=f30[i]
                if c[i]<o0: bullB[0]+=1; bullB[1]+= hit[i]==1; bullB[2]+= hit[i]==-1; bullB[3]+=f30[i]
            elif st[i]==0:
                bear[0]+=1; bear[1]+= hit[i]==1; bear[2]+= hit[i]==-1; bear[3]+=f30[i]
                if c[i]>o0: bearS[0]+=1; bearS[1]+= hit[i]==1; bearS[2]+= hit[i]==-1; bearS[3]+=f30[i]
    res.append((wr,wb,wp,bull,bear,bullB,bearS))
def edge(x,sign): return (x[1]-x[2])/x[0]*100*sign if x[0] else 0
print('\n== 多頭排列剛完成(所有):依「先+1% − 先−1%」排序,前 8')
print(f"{'紅':>3} {'藍':>3} {'紫':>3} {'樣本':>6} {'先+1%':>6} {'先-1%':>6} {'差':>6} {'30分均':>7}")
for wr,wb,wp,b,s,bb,ss in sorted(res,key=lambda r:-edge(r[3],1))[:8]: print(f"{wr:3d} {wb:3d} {wp:3d} {b[0]:6d} {b[1]/b[0]*100:6.1f} {b[2]/b[0]*100:6.1f} {edge(b,1):+6.1f} {b[3]/b[0]:+7.3f}")
print('\n== 多頭排列剛完成 且 價在今開之下:前 8')
for wr,wb,wp,b,s,bb,ss in sorted(res,key=lambda r:-edge(r[5],1))[:8]:
    if bb[0]>=200: print(f"{wr:3d} {wb:3d} {wp:3d} {bb[0]:6d} {bb[1]/bb[0]*100:6.1f} {bb[2]/bb[0]*100:6.1f} {edge(bb,1):+6.1f} {bb[3]/bb[0]:+7.3f}")
print('\n== 空頭排列剛完成(所有):依「先−1% − 先+1%」排序,前 8')
for wr,wb,wp,b,s,bb,ss in sorted(res,key=lambda r:-edge(r[4],-1))[:8]: print(f"{wr:3d} {wb:3d} {wp:3d} {s[0]:6d} {s[1]/s[0]*100:6.1f} {s[2]/s[0]*100:6.1f} {edge(s,-1):+6.1f} {s[3]/s[0]:+7.3f}")
print('\n== 空頭排列剛完成 且 價在今開之上:前 8')
for wr,wb,wp,b,s,bb,ss in sorted(res,key=lambda r:-edge(r[6],-1))[:8]:
    if ss[0]>=200: print(f"{wr:3d} {wb:3d} {wp:3d} {ss[0]:6d} {ss[1]/ss[0]*100:6.1f} {ss[2]/ss[0]*100:6.1f} {edge(ss,-1):+6.1f} {ss[3]/ss[0]:+7.3f}")
print('\n== 對照:原本 10/10/10')
for wr,wb,wp,b,s,bb,ss in res:
    if (wr,wb,wp)==(10,10,10): print('多', b[0], f"{b[1]/b[0]*100:.1f} {b[2]/b[0]*100:.1f}", '| 多+價在今開下', bb[0], f"{bb[1]/bb[0]*100:.1f} {bb[2]/bb[0]*100:.1f}", '| 空', s[0], f"{s[1]/s[0]*100:.1f} {s[2]/s[0]*100:.1f}")
