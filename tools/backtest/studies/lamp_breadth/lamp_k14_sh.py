import asyncio, json
from playwright.async_api import async_playwright
R='/home/user/Stock-database'
PATCH = r"""() => {
  const S0 = ind3Series;
  window.ind3Series = function(bars, ma, ma24, ob, obma, obma30){
    const out = S0(bars, ma, ma24, ob, obma, obma30);
    const mk = toTimeMap(ma), mb = toTimeMap(ma24), m14 = toTimeMap(sma(bars, 14, 'close', false));
    for (const b of bars){ const st = out.get(b.time); if (!st) continue;
      const a = mk.get(b.time), k = m14.get(b.time), kb = mb.get(b.time);
      st.ak = (a != null && k != null) ? Math.sign(a - k) : 0; st.kk = (k != null && kb != null) ? Math.sign(k - kb) : 0; }
    return out;
  };
  const R0 = ind3Raw;
  const post = (r, st, time) => {
    if (YH.lampObOc && r === '做多' && !(st.c > 0)) r = '漸多'; if (YH.lampObOc && r === '做空' && !(st.c < 0)) r = '漸空';
    if (YH.lampBreadth > 0 && time != null && (r === '做多' || r === '做空')){ const br = breadthAt(time);
      if (br != null && r === '做空' && br >= YH.lampBreadth) r = '漸空'; if (br != null && r === '做多' && br <= 1 - YH.lampBreadth) r = '漸多'; }
    return r; };
  const lv = b => b >= 0.75 ? '做多' : b >= 0.25 ? '漸多' : b > -0.25 ? '監控' : b > -0.75 ? '漸空' : '做空';
  window.ind3Raw = function(st, time, waiting){
    const V = window.__V || 'base';
    let r = R0(st, time, waiting);
    if (V === 'base' || waiting || !st || (time != null && hmKey(time) >= '13:25')) return r;
    const base4 = ind3Bias(st) * 4;
    if (V === 'F1') r = post(lv((base4 + st.ak) / 5), st, time);
    if (V === 'F5') r = post(lv((base4 + st.kk) / 5), st, time);
    if (V === 'F2'){ if (r === '做多' && !(st.ak > 0)) r = '漸多'; if (r === '做空' && !(st.ak < 0)) r = '漸空'; }
    if (V === 'F6'){ if (st.ak < 0 && st.k < 0 && (r === '做多' || r === '漸多' || r === '監控')) r = '漸空'; if (st.ak > 0 && st.k > 0 && (r === '做空' || r === '漸空' || r === '監控')) r = '漸多'; }
    return r;
  };
  return 'ok';
}"""
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path='/opt/pw-browsers/chromium', args=['--no-sandbox'])
        pg = await (await b.new_context(timezone_id='Asia/Taipei')).new_page()
        await pg.add_init_script("window.Android={getRuleset:()=>'一',setRuleset:()=>{},monitorRunning:()=>true,isService:()=>false,notify:()=>{},log:()=>{},ready:()=>{}}; const real=window.fetch; window.fetch=async(u,i)=>{u=String(u); return /\\?(log|codes|breadth)=/.test(u)?new Response('[]',{status:200}):real(u,i)};")
        await pg.goto(f'file://{R}/app/app.html'); await pg.wait_for_timeout(2500)
        await pg.evaluate(PATCH)
        br = json.load(open(f'{R}/tools/backtest/results/breadth_2026-10-06_21.json'))['2026-10-21']
        bars = await pg.evaluate("""async () => { BARS_MEM.delete('4566'); const h = (await loadMinuteBars('4566')).filter(b => b.time < Date.parse('2026-10-21T00:00:00+08:00') / 1000);
            const j = await (await fetch(BASE.liveUrl + '/?bars=2026-10-21&codes=4566')).json(); const base = Date.parse('2026-10-21T00:00:00+08:00') / 1000;
            return h.slice(-1500).concat(j.bars['4566'].map(b => ({time: base + (+b[0].slice(0, 2)) * 3600 + (+b[0].slice(3, 5)) * 60, open: b[1], high: b[2], low: b[3], close: b[4], volume: b[5]}))); }""")
        for V in ('base','F1','F2','F5','F6'):
            seq = await pg.evaluate("""([bars, br, V]) => { window.__V = V; const base = Date.parse('2026-10-21T00:00:00+08:00') / 1000; BREADTH.base = base; BREADTH.arr = Object.keys(br).sort().map(k => [base + (+k.slice(0, 2)) * 3600 + (+k.slice(3, 5)) * 60, br[k]]);
              document.getElementById('sheet').classList.add('open'); drawBars(bars, '4566', '時碩', false);
              const ld = dayKey(BARS[BARS.length-1].time); return BARS.filter(b => dayKey(b.time) === ld).map(b => [hmKey(b.time), CUR.lamps.get(b.time) || '']); }""", [bars, br, V])
            w = ' '.join(f"{t}{l}" for k, (t, l) in enumerate(seq) if '10:40' <= t <= '11:45' and (k == 0 or l != seq[k-1][1] or t == '11:05'))
            print(f'{V:5s} 時碩 {w}', flush=True)
        await b.close()
asyncio.run(main())
