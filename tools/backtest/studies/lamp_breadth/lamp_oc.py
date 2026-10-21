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
    let pday = null, lastC = null, pc = null;
    for (const b of bars){
      const dk = dayKey(b.time); if (dk !== pday){ pc = lastC; pday = dk; } lastC = b.close;
      const st = out.get(b.time); if (!st) continue;
      st.cl = b.close; st.pc = pc; st.br = breadthAt(b.time);
    }
    return out;
  };
  const R0 = ind3Raw;
  window.ind3Raw = function(st, time, waiting){
    const V = window.__V || 'base';
    let r = R0(st, time, waiting);
    if (V === 'base' || waiting || !st || st.br == null || (time != null && hmKey(time) >= '13:25')) return r;
    const br = st.br, pct = st.pc > 0 ? (st.cl - st.pc) / st.pc * 100 : 0;
    if (V === 'W1' || V === 'W3'){ const th = V === 'W1' ? 0.8 : 0.7;
      if (r === '做空' && br >= th && pct > 0) r = '漸空'; if (r === '做多' && br <= 1 - th && pct < 0) r = '漸多'; }
    if (V === 'W2'){ if (r === '做空' && br >= 0.8) r = '漸空'; if (r === '做多' && br <= 0.2) r = '漸多'; }
    if (V === 'W5'){ if (r === '做空' && br > 0.5) r = '漸空'; if (r === '做多' && br < 0.5) r = '漸多'; }
    if (V === 'W6'){ if (r === '做空' && br >= 0.65) r = '漸空'; if (r === '做多' && br <= 0.35) r = '漸多'; }
    if (V === 'W4'){
      const org = st.lo > 0 && st.hi > 0 ? 1 : st.lo < 0 && st.hi < 0 ? -1 : 0, bv = br >= 0.6 ? 1 : br <= 0.4 ? -1 : 0;
      const b = (st.k + org + (st.o2||0) + (st.ks||0) + bv) / 5;
      r = b >= 0.75 ? '做多' : b >= 0.25 ? '漸多' : b > -0.25 ? '監控' : b > -0.75 ? '漸空' : '做空';
      if (YH.lampObOc && r === '做多' && !(st.c > 0)) r = '漸多'; if (YH.lampObOc && r === '做空' && !(st.c < 0)) r = '漸空';
    }
    return r;
  };
  return typeof breadthAt;
}"""
VS = sys.argv[1].split(',')
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path='/opt/pw-browsers/chromium', args=['--no-sandbox'])
        pg = await (await b.new_context(timezone_id='Asia/Taipei')).new_page()
        await pg.add_init_script("window.Android={getRuleset:()=>'一',setRuleset:()=>{},monitorRunning:()=>true,isService:()=>false,notify:()=>{},log:()=>{},ready:()=>{}}; const real=window.fetch; window.fetch=async(u,i)=>{u=String(u); return /\\?(log|bars|codes|breadth)=/.test(u)?new Response('[]',{status:200}):real(u,i)};")
        await pg.goto('file:///home/user/Stock-database/app/app.html'); await pg.wait_for_timeout(2500)
        print('patch', await pg.evaluate(PATCH))
        await pg.evaluate("() => { const s0 = sma; window.sma = function(bars, n, key, daily){ if (n === 30 && key === 'value') n = window.__OC || 30; return s0(bars, n, key, daily); }; }")
        data=[]
        for code in STK:
            for days in (('2026-10-12','2026-10-13'),('2026-10-13','2026-10-14'),('2026-10-14','2026-10-15'),('2026-10-15','2026-10-16'),('2026-10-16','2026-10-19'),('2026-10-19','2026-10-20'),('2026-10-20','2026-10-21')):
                bars=load(code,days)
                if len(bars)<300: continue
                data.append((code,days[1],bars))
        BR=json.load(open('/home/user/Stock-database/tools/backtest/results/breadth_2026-10-06_21.json'))
        for V in VS:
            await pg.evaluate("v => { window.__OC = +v; window.__V = 'base'; }", V)
            tot=[0,0,0]; nseg=0
            for code,day,bars in data:
                await pg.evaluate("([d, br]) => { const base = Date.parse(d + 'T00:00:00+08:00') / 1000; BREADTH.d = d; BREADTH.base = base; BREADTH.arr = Object.keys(br).sort().map(k => [base + (+k.slice(0, 2)) * 3600 + (+k.slice(3, 5)) * 60, br[k]]); }", [day, BR.get(day, {})])
                r = await pg.evaluate("""(bars) => { document.getElementById('sheet').classList.add('open'); drawBars(bars, 'x', 'test', false);
                   const ld = dayKey(BARS[BARS.length-1].time); const T = BARS.filter(b => dayKey(b.time) === ld); const L = T.map(b => CUR.lamps.get(b.time));
                   const ft = (i, sd) => { const e = T[i].close, up = e * 1.01, dn = e * 0.99; for (let j = i + 1; j < Math.min(T.length, i + 61); j++){ const hu = T[j].high >= up, hd = T[j].low <= dn; if (hu && hd) return 0; if (hu) return sd > 0 ? 1 : -1; if (hd) return sd < 0 ? 1 : -1; } return 0; };
                   const o = [0,0,0]; for (let i = 0; i < T.length; i++){ if (L[i] !== L[i-1] && (L[i] === '做多' || L[i] === '做空')) o[ft(i, L[i] === '做多' ? 1 : -1) + 1]++; } return o; }""", bars)
                tot[0]+=r[2]; tot[1]+=r[0]; tot[2]+=r[1]
            print(f"{V:5s} 對 {tot[0]:4d} 錯 {tot[1]:4d} 平 {tot[2]:4d}  勝率 {tot[0]*100/max(1,tot[0]+tot[1]):.1f}%  股票日 {len(data)}", flush=True)
        await b.close()
asyncio.run(main())
