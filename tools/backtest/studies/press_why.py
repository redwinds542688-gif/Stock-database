# 壓線空點 B 賠的為什麼賠:每筆進場時的狀況,分賺 / 賠比較
import json, csv, glob, numpy as np, collections, sys, os
exec(open('obv_mean10.py',encoding='utf-8').read().split("stat=collections")[0])
TOUCH=0.15
rows=[]
# 每天大盤強弱:候選股當天(到進場時)價在今開之下的比例
for d,pool in DAYS.items():
    P=json.load(open(pool,encoding='utf-8'))
    allB={}
    for s in P['pool']:
        B=bars(s['code'],d)
        if len(B)>=60: allB[s['code']]=(s,B)
    # 市場:每個時間點,價 < 今開 的比例
    def breadth(t):
        n=0;k=0
        for code,(s,B) in allB.items():
            idx=[i for i,b in enumerate(B) if b[0]<=t]
            if not idx: continue
            n+=1; k+= B[idx[-1]][4]<B[0][1]
        return k/max(1,n)*100
    for code,(s,B) in allB.items():
        c=np.array([b[4] for b in B]); h=np.array([b[2] for b in B]); l=np.array([b[3] for b in B]); v=np.array([b[5] for b in B]); o=np.array([b[1] for b in B])
        sign=np.sign(np.diff(c,prepend=c[0])); ob=np.cumsum(sign*v); ma10=expmean(ob,10); ma30=expmean(ob,30); pma=expmean(c,10)
        vw=np.cumsum(c*v)/np.maximum(np.cumsum(v),1)
        prev=s.get('prev') or s.get('prev_close') or s.get('close')
        for i in range(30,len(B)-31):
            if not ('09:15'<=B[i][0]<='12:00'): continue
            maDown=pma[i]<pma[i-1]<pma[i-2]; touch=max(h[i],h[i-1])>=pma[i]*(1-TOUCH/100); under=c[i]<pma[i] and c[i]<o[i]
            obvDown=ob[i]<ma10[i]<ma30[i] and ma10[i]<ma10[i-1] and ma30[i]<ma30[i-1] and ob[i]<=ob[i-30:i].min() and c[i]>l[i-30:i].min()*1.003
            if not (maDown and touch and under and obvDown): continue
            ent=o[i+1]; lots=int(350000//(ent*1000))
            if lots==0: break
            ex=None; why=''
            for j in range(i+1,len(B)):
                if B[j][0]>='13:25': ex=c[j]; why='13:25'; break
                if (h[j]-ent)/ent*100>=1.5: ex=ent*1.015; why='停損'; break
                if (l[j]-ent)/ent*100<=-2.5: ex=ent*0.975; why='停利'; break
            if ex is None: ex=c[-1]; why='收盤'
            net=(ent-ex)*lots*1000 - ent*lots*1000*0.001425*0.28*2 - ent*lots*1000*0.0015
            f=dict(d=d[5:],name=s.get('name'),t=B[i][0],net=int(net),why=why,
                black=c[i]<o[0], vwap_below=c[i]<vw[i], vs_prev=((c[i]-prev)/prev*100) if prev else None,
                gap=((o[0]-prev)/prev*100) if prev else None, off_hi=(h[:i+1].max()-c[i])/c[i]*100, off_lo=(c[i]-l[:i+1].min())/c[i]*100,
                drop20=(h[i-20:i].max()-c[i])/c[i]*100, rvol=v[i-10:i+1].mean()/max(v[:i+1].mean(),1e-9),
                breadth=breadth(B[i][0]), slope=(pma[i]-pma[i-5])/pma[i]*100, hour=B[i][0][:2])
            rows.append(f); break
json.dump(rows,open(f'{S}/results/press_why.json','w',encoding='utf-8'),ensure_ascii=False,default=lambda x:bool(x) if isinstance(x,np.bool_) else float(x))
W=[r for r in rows if r['net']>0]; L=[r for r in rows if r['net']<=0]
print(f'{len(rows)} 筆:賺 {len(W)} 賠 {len(L)}')
def pct(a,key,cond): 
    x=[r for r in a if cond(r)]; return f'{len(x)}'
def cmp(label,cond):
    w=sum(1 for r in W if cond(r)); l=sum(1 for r in L if cond(r)); n=w+l
    net=sum(r['net'] for r in rows if cond(r))
    print(f'{label:<28} {n:>4} 筆  賺 {w:>3} 賠 {l:>3}  勝率 {w*100//max(1,n):>3}%  淨 {net:+,}')
cmp('黑盤(價 < 今開)',lambda r:r['black']); cmp('紅盤(價 ≥ 今開)',lambda r:not r['black'])
cmp('價 < VWAP',lambda r:r['vwap_below']); cmp('價 ≥ VWAP',lambda r:not r['vwap_below'])
cmp('價 < 昨收',lambda r:r['vs_prev'] is not None and r['vs_prev']<0); cmp('價 ≥ 昨收',lambda r:r['vs_prev'] is not None and r['vs_prev']>=0)
cmp('開高(今開 > 昨收 +1%)',lambda r:r['gap'] is not None and r['gap']>1); cmp('開平(±1%)',lambda r:r['gap'] is not None and -1<=r['gap']<=1); cmp('開低(< −1%)',lambda r:r['gap'] is not None and r['gap']<-1)
cmp('離今高 < 1%',lambda r:r['off_hi']<1); cmp('離今高 1~2%',lambda r:1<=r['off_hi']<2); cmp('離今高 ≥ 2%',lambda r:r['off_hi']>=2)
cmp('離今低 < 0.5%',lambda r:r['off_lo']<0.5); cmp('離今低 0.5~1.5%',lambda r:0.5<=r['off_lo']<1.5); cmp('離今低 ≥ 1.5%',lambda r:r['off_lo']>=1.5)
cmp('前 20 根跌 < 1%',lambda r:r['drop20']<1); cmp('前 20 根跌 ≥ 1%',lambda r:r['drop20']>=1)
cmp('量比 < 0.7',lambda r:r['rvol']<0.7); cmp('量比 0.7~1.2',lambda r:0.7<=r['rvol']<1.2); cmp('量比 ≥ 1.2',lambda r:r['rvol']>=1.2)
cmp('大盤弱(> 60% 股票在今開下)',lambda r:r['breadth']>60); cmp('大盤中(40~60%)',lambda r:40<=r['breadth']<=60); cmp('大盤強(< 40%)',lambda r:r['breadth']<40)
cmp('均價 10 五根斜率 < −0.3%',lambda r:r['slope']<-0.3); cmp('斜率 −0.3~0',lambda r:-0.3<=r['slope']<0)
cmp('09 點',lambda r:r['hour']=='09'); cmp('10 點',lambda r:r['hour']=='10'); cmp('11 點',lambda r:r['hour']=='11')
print('\n賠的 75 筆:')
for r in sorted(L,key=lambda r:r['net']): print(f"  {r['d']} {r['name']:<8} {r['t']} {r['why']} {r['net']:>7,}  {'黑' if r['black'] else '紅'}盤 {'V下' if r['vwap_below'] else 'V上'} 昨收{r['vs_prev']:+.1f}% 開{r['gap']:+.1f}% 離今高{r['off_hi']:.1f}% 離今低{r['off_lo']:.1f}% 量比{r['rvol']:.1f} 大盤弱{r['breadth']:.0f}%")
