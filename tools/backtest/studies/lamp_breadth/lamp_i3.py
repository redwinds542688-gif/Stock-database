import asyncio, json, csv, datetime, os, sys
from playwright.async_api import async_playwright
STK=[c for c in sorted(os.listdir('/home/user/Stock-database/kline')) if c.isdigit()]
def load(code, days):
    bars=[]
    for wk in ('2026-W41','2026-W42','2026-W43'):
        f=f'/home/user/Stock-database/kline/{code}/{wk}.csv'
        if not os.path.exists(f): continue
        for r in csv.DictReader(open(f)):
            if r['date'] not in days or float(r['low'])<=0: continue
            t=int(datetime.datetime.strptime(r['date']+' '+r['time'],'%Y-%m-%d %H:%M').replace(tzinfo=datetime.timezone(datetime.timedelta(hours=8))).timestamp())
            bars.append({'time':t,'open':float(r['open']),'high':float(r['high']),'low':float(r['low']),'close':float(r['close']),'volume':int(float(r['volume']))})
    bars.sort(key=lambda b:b['time']); return bars
PATCH = r"""() => {
  const S0 = ind3Series;
  window.ind3Series = function(bars, ma, ma24, ob, obma, obma30){
    const out = S0(bars, ma, ma24, ob, obma, obma30);
    const mk = toTimeMap(ma), mb = toTimeMap(ma24), mo = toTimeMap(ob), mm = toTimeMap(obma);
    let pv = null, pday = null, pts = [], op = [], prevO = null;
    for (const b of bars){
      const st = out.get(b.time); if (!st) continue;
      const dk = dayKey(b.time); if (dk !== pday){ pv = null; prevO = null; pday = dk; }
      const a = mk.get(b.time), c = mb.get(b.time);
      if (a != null && c != null){ const d = a - c; if (pv != null && ((d > 0 && pv <= 0) || (d < 0 && pv >= 0))) pts.push(a); pv = d; } else pv = null;
      const o = mo.get(b.time), m = mm.get(b.time);
      if (o != null && m != null){ const d = o - m; if (prevO != null){ if (d > 0 && prevO <= 0) op.push(b.low); else if (d < 0 && prevO >= 0) op.push(b.high); } prevO = d; } else prevO = null;
      st.pd = pts.length >= 2 ? Math.sign(pts[pts.length-1] - pts[pts.length-2]) : 0;
      st.od = op.length >= 2 ? Math.sign(op[op.length-1] - op[op.length-2]) : 0;
    }
    return out;
  };
  const R0 = ind3Raw;
  window.ind3Raw = function(st, time, waiting){
    const V = window.__V || 'base';
    if (V === 'base' || waiting || !st || (time != null && hmKey(time) >= '13:25')) return R0(st, time, waiting);
    const org = st.lo > 0 && st.hi > 0 ? 1 : st.lo < 0 && st.hi < 0 ? -1 : 0;
    const two = (x, y) => (x > 0 && y > 0) ? '做多' : (x < 0 && y < 0) ? '做空' : ((x > 0 && y >= 0) || (y > 0 && x >= 0)) ? '漸多' : ((x < 0 && y <= 0) || (y < 0 && x <= 0)) ? '漸空' : '監控';
    if (V === 'a') return two(st.k, org);
    if (V === 'b') return two(st.pd, org);
    if (V === 'c') return two(st.k, st.od);
    if (V === 'd') return two(st.pd, st.od);
    if (V === 'e'){ const n = st.k + st.pd + org; return n >= 3 ? '做多' : n <= -3 ? '做空' : n >= 2 ? '漸多' : n <= -2 ? '漸空' : '監控'; }
    return R0(st, time, waiting);
  };
  return typeof ind3Series;
}"""
VS = sys.argv[1].split(',')
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path='/opt/pw-browsers/chromium', args=['--no-sandbox'])
        pg = await (await b.new_context(timezone_id='Asia/Taipei')).new_page()
        await pg.add_init_script("window.Android={getRuleset:()=>'一',setRuleset:()=>{},monitorRunning:()=>true,isService:()=>false,notify:()=>{},log:()=>{},ready:()=>{}}; const real=window.fetch; window.fetch=async(u,i)=>{u=String(u); return /\\?(log|bars|codes|breadth)=/.test(u)?new Response('[]',{status:200}):real(u,i)};")
        await pg.goto('file:///home/user/Stock-database/app/app.html'); await pg.wait_for_timeout(2500)
        print('patch', await pg.evaluate(PATCH))
        data=[]
        for code in STK:
            for days in (('2026-10-12','2026-10-13'),('2026-10-13','2026-10-14'),('2026-10-14','2026-10-15'),('2026-10-15','2026-10-16'),('2026-10-16','2026-10-19'),('2026-10-19','2026-10-20'),('2026-10-20','2026-10-21')):
                bars=load(code,days)
                if len(bars)<300: continue
                data.append((code,days[1],bars))
        for V in VS:
            await pg.evaluate("v => { window.__V = v; }", V)
            tot=[0,0,0]; nseg=0
            for code,day,bars in data:
                r = await pg.evaluate("""(bars) => { document.getElementById('sheet').classList.add('open'); drawBars(bars, 'x', 'test', false);
                   const ld = dayKey(BARS[BARS.length-1].time); const T = BARS.filter(b => dayKey(b.time) === ld); const L = T.map(b => CUR.lamps.get(b.time));
                   const ft = (i, sd) => { const e = T[i].close, up = e * 1.01, dn = e * 0.99; for (let j = i + 1; j < Math.min(T.length, i + 61); j++){ const hu = T[j].high >= up, hd = T[j].low <= dn; if (hu && hd) return 0; if (hu) return sd > 0 ? 1 : -1; if (hd) return sd < 0 ? 1 : -1; } return 0; };
                   const o = [0,0,0]; for (let i = 0; i < T.length; i++){ if (L[i] !== L[i-1] && (L[i] === '做多' || L[i] === '做空')) o[ft(i, L[i] === '做多' ? 1 : -1) + 1]++; } return o; }""", bars)
                tot[0]+=r[2]; tot[1]+=r[0]; tot[2]+=r[1]
            print(f"{V:5s} 對 {tot[0]:4d} 錯 {tot[1]:4d} 平 {tot[2]:4d}  勝率 {tot[0]*100/max(1,tot[0]+tot[1]):.1f}%  股票日 {len(data)}", flush=True)
        await b.close()
asyncio.run(main())
