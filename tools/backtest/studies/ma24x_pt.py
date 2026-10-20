# 「均價 10 穿過均價 24 + OBV 三線同向」研究(2026-10-20 使用者看富喬 11:42:「最漂亮的做空點」)
# 做空:均價 10 這根剛跌破均價 24(上一根還在上面)+ 紅 < 藍 < 紫、藍紫都向下 + 收盤 < 均價 10;做多鏡像。每檔每天第一次,09:15~12:00,停利 2.5、停損 1.5、13:25 平倉
import json, numpy as np, collections, sys
exec(open('obv_mean10.py',encoding='utf-8').read().split("stat=collections")[0])
SIDE=sys.argv[1] if len(sys.argv)>1 else 'short'; NEEDOBV=sys.argv[2] if len(sys.argv)>2 else 'all'
def roll(a,n):
    out=np.full(len(a),np.nan)
    for i in range(n-1,len(a)): out[i]=a[i-n+1:i+1].mean()
    return out
st=collections.defaultdict(float); trades=[]; daily=collections.defaultdict(lambda:[0,0,0])
for d,pool in DAYS.items():
    P=json.load(open(pool,encoding='utf-8'))
    for s in P['pool']:
        B=bars(s['code'],d)
        if len(B)<60: continue
        c=np.array([b[4] for b in B]); h=np.array([b[2] for b in B]); l=np.array([b[3] for b in B]); v=np.array([b[5] for b in B]); o=np.array([b[1] for b in B])
        sign=np.sign(np.diff(c,prepend=c[0])); ob=np.cumsum(sign*v); m10=roll(ob,10); m30=roll(ob,30); ma=roll(c,10); ma24=roll(c,24)
        prev=s.get('prev') or s.get('close')
        done=False
        for sd in ((-1,) if SIDE=='short' else (1,) if SIDE=='long' else (-1,1)):
            done=False
            for i in range(31,len(B)-1):
                if np.isnan(ma24[i-1]) or np.isnan(m30[i-1]): continue
                x=(ma[i]-ma24[i])*sd>0 and (ma[i-1]-ma24[i-1])*sd<=0
                if not x: continue
                okc=(c[i]-ma[i])*sd>0
                okobv = ((ob[i]-m10[i])*sd>0 and (m10[i]-m30[i])*sd>0 and (m10[i]-m10[i-1])*sd>0 and (m30[i]-m30[i-1])*sd>0) if NEEDOBV=='all' else ((m10[i]-m10[i-1])*sd>0) if NEEDOBV=='blue' else True
                if not (okc and okobv): continue
                pct=(c[i]-prev)/prev*100 if prev else 0
                e=c[i]; r=0
                for j in range(i+1,min(len(c),i+31)):
                    up=(h[j]-e)/e*100; dn=(l[j]-e)/e*100
                    if up>=1 and dn<=-1: break
                    if up>=1: r=1; break
                    if dn<=-1: r=-1; break
                st['n']+=1; st['fav']+= (r==sd); st['adv']+= (r==-sd)
                if done or not ('09:15'<=B[i][0]<='12:00') or abs(pct)>3.5: continue
                done=True; ent=o[i+1]; lots=int(350000//(ent*1000))
                if lots==0: continue
                ex=None; why=''
                for j in range(i+1,len(B)):
                    if B[j][0]>='13:25': ex=c[j]; why='13:25'; break
                    if ((l[j] if sd==1 else h[j])-ent)/ent*100*sd<=-1.5: ex=ent*(1-0.015*sd); why='停損'; break
                    if ((h[j] if sd==1 else l[j])-ent)/ent*100*sd>=2.5: ex=ent*(1+0.025*sd); why='停利'; break
                if ex is None: ex=c[-1]; why='收盤'
                net=(ex-ent)*sd*lots*1000 - (ent+ex)*lots*1000*0.001425*0.28 - (ex if sd==1 else ent)*lots*1000*0.0015
                trades.append((d[5:],s.get('name'),'多' if sd==1 else '空',B[i][0],round(ent,2),lots,round(ex,2),why,int(net)))
                daily[d[5:]][0]+=1; daily[d[5:]][1]+= net>0; daily[d[5:]][2]+=net
tot=len(trades); win=sum(1 for t in trades if t[8]>0); net=sum(t[8] for t in trades)
print(f'{SIDE} OBV條件={NEEDOBV}:訊號 {int(st["n"])} 次,30 分內先順向 1% {st["fav"]/max(1,st["n"])*100:.1f}% 先逆向 1% {st["adv"]/max(1,st["n"])*100:.1f}% | 交易 {tot} 筆 {win} 賺 勝率 {win*100//max(1,tot)}% 淨 {net:+,}')
if '-v' in sys.argv:
    for d in sorted(daily): print(' ',d,daily[d][0],'筆',daily[d][1],'賺',f'{daily[d][2]:+,.0f}')
