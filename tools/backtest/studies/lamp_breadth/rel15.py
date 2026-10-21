import csv, glob, os, itertools
import numpy as np
R='/home/user/Stock-database'
DAYS=['2026-10-13','2026-10-14','2026-10-15','2026-10-16','2026-10-19','2026-10-20','2026-10-21']
def sma(x, n):
    out = np.full(len(x), np.nan)
    for i in range(n-1, len(x)): out[i] = x[i-n+1:i+1].mean()
    return out
N = {'Oa':0,'Ob':10,'Od':16,'Oc':28}
PAIRS = [('Oa','Ob'),('Oa','Od'),('Oa','Oc'),('Ob','Od'),('Ob','Oc'),('Od','Oc')]   # 快 > 慢 = 偏多
COMBOS = [(p,) for p in PAIRS] + list(itertools.combinations(PAIRS, 2))
res = {c: [0,0,0] for c in COMBOS}; nday = 0
for code in sorted(os.listdir(f'{R}/kline')):
    if not code.isdigit(): continue
    rows=[]
    for f in sorted(glob.glob(f'{R}/kline/{code}/2026-W4*.csv')): rows += list(csv.DictReader(open(f)))
    for d in DAYS:
        T=[r for r in rows if r['date']==d and '09:00'<=r['time']<='13:30' and float(r['low'])>0]
        if len(T) < 150: continue
        nday += 1
        c=np.array([float(r['close']) for r in T]); h=np.array([float(r['high']) for r in T]); l=np.array([float(r['low']) for r in T]); v=np.array([float(r['volume'])/1000 for r in T])
        ob=np.zeros(len(c)); acc=0
        for i in range(1,len(c)):
            if c[i]>c[i-1]: acc+=v[i]
            elif c[i]<c[i-1]: acc-=v[i]
            ob[i]=acc
        L={'Oa':ob,'Ob':sma(ob,10),'Od':sma(ob,16),'Oc':sma(ob,28)}
        sg={p: np.sign(L[p[0]]-L[p[1]]) for p in PAIRS}
        tm=[r['time'] for r in T]
        def ft(i, sd):
            e=c[i]; up=e*1.01; dn=e*0.99
            for j in range(i+1, min(len(c), i+61)):
                hu=h[j]>=up; hd=l[j]<=dn
                if hu and hd: return 0
                if hu: return 1 if sd>0 else -1
                if hd: return 1 if sd<0 else -1
            return 0
        for cb in COMBOS:
            prev=0
            for i in range(28, len(c)):
                if tm[i] >= '13:25': break
                vals=[sg[p][i] for p in cb]
                st = 1 if all(x>0 for x in vals) else -1 if all(x<0 for x in vals) else 0
                if st != 0 and st != prev:
                    o=ft(i, st); res[cb][0 if o==1 else 1 if o==-1 else 2]+=1
                prev=st
name=lambda cb: ' + '.join(f'{a}>{b}' for a,b in cb)
print('股票日', nday)
for title, group in (('六種關係', COMBOS[:6]), ('十五種組合', COMBOS[6:])):
    print('==', title)
    for cb in sorted(group, key=lambda x: -(res[x][0]/max(1,res[x][0]+res[x][1]))):
        w,lo,z=res[cb]; print(f'{name(cb):22s} 次數 {w+lo+z:6d} 對 {w:5d} 錯 {lo:5d} 勝率 {w*100/max(1,w+lo):5.1f}% 淨 {w-lo:+5d}')
