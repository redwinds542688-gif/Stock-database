import asyncio, json, csv, datetime, os, sys, glob
from playwright.async_api import async_playwright
R='/home/user/Stock-database'
STK=[c for c in sorted(os.listdir(f'{R}/kline')) if c.isdigit()]
def rows(path, ok):
    out=[]
    for f in sorted(glob.glob(path)):
        for r in csv.DictReader(open(f)):
            if not ok(r['date']) or float(r['low'])<=0: continue
            t=int(datetime.datetime.strptime(r['date']+' '+r['time'],'%Y-%m-%d %H:%M').replace(tzinfo=datetime.timezone(datetime.timedelta(hours=8))).timestamp())
            out.append({'time':t,'open':float(r['open']),'high':float(r['high']),'low':float(r['low']),'close':float(r['close']),'volume':int(float(r['volume']))})
    out.sort(key=lambda b:b['time']); return out
NS=[int(x) for x in sys.argv[1].split(',')]; ALL = len(sys.argv) < 3
SH = rows(f'{R}/kline/4566/2026-W4*.csv', lambda d: '2026-10-16' <= d <= '2026-10-20') + rows(f'{R}/kline_cloud/4566/2026-W43.csv', lambda d: d == '2026-10-21')
EVAL = """(bars) => { document.getElementById('sheet').classList.add('open'); drawBars(bars, 'x', 'test', false);
  const ld = dayKey(BARS[BARS.length-1].time); const T = BARS.filter(b => dayKey(b.time) === ld); const L = T.map(b => CUR.lamps.get(b.time));
  const ft = (i, sd) => { const e = T[i].close, up = e * 1.01, dn = e * 0.99; for (let j = i + 1; j < Math.min(T.length, i + 61); j++){ const hu = T[j].high >= up, hd = T[j].low <= dn; if (hu && hd) return 0; if (hu) return sd > 0 ? 1 : -1; if (hd) return sd < 0 ? 1 : -1; } return 0; };
  const o = [0,0,0]; for (let i = 0; i < T.length; i++){ if (L[i] !== L[i-1] && (L[i] === '做多' || L[i] === '做空')) o[ft(i, L[i] === '做多' ? 1 : -1) + 1]++; }
  return [o, T.map((b, i) => [hmKey(b.time), L[i] || '']) ]; }"""
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path='/opt/pw-browsers/chromium', args=['--no-sandbox'])
        pg = await (await b.new_context(timezone_id='Asia/Taipei')).new_page()
        await pg.add_init_script("window.Android={getRuleset:()=>'一',setRuleset:()=>{},monitorRunning:()=>true,isService:()=>false,notify:()=>{},log:()=>{},ready:()=>{}}; const real=window.fetch; window.fetch=async(u,i)=>{u=String(u); return /\\?(log|codes|breadth)=/.test(u)?new Response('[]',{status:200}):real(u,i)};")
        await pg.goto(f'file://{R}/app/app.html'); await pg.wait_for_timeout(2500)
        await pg.evaluate("() => { const s0 = sma; window.sma = function(bars, n, key, daily){ if (n === 24 && daily === false) n = window.__KB || 24; return s0(bars, n, key, daily); }; }")
        SHP = await pg.evaluate("""async () => { BARS_MEM.delete('4566'); const h = (await loadMinuteBars('4566')).filter(b => b.time < Date.parse('2026-10-21T00:00:00+08:00') / 1000);
            const r = await fetch(BASE.liveUrl + '/?bars=2026-10-21&codes=4566'); const j = await r.json(); const base = Date.parse('2026-10-21T00:00:00+08:00') / 1000;
            const t = j.bars['4566'].map(b => ({time: base + (+b[0].slice(0, 2)) * 3600 + (+b[0].slice(3, 5)) * 60, open: b[1], high: b[2], low: b[3], close: b[4], volume: b[5]}));
            return h.slice(-1500).concat(t); }""")
        print('bars', len(SHP), flush=True)
        data=[]
        if ALL:
            for code in STK:
                for days in (('2026-10-12','2026-10-13'),('2026-10-13','2026-10-14'),('2026-10-14','2026-10-15'),('2026-10-15','2026-10-16'),('2026-10-16','2026-10-19'),('2026-10-19','2026-10-20'),('2026-10-20','2026-10-21')):
                    bs=rows(f'{R}/kline/{code}/2026-W4*.csv', lambda d, days=days: d in days)
                    if len(bs)>=300: data.append(bs)
        for N in NS:
            await pg.evaluate("n => { window.__KB = n; }", N)
            r = await pg.evaluate(EVAL, SHP); seq = r[1]
            win = ' '.join(f"{t}{l}" for k, (t, l) in enumerate(seq) if '10:40' <= t <= '11:45' and (k == 0 or l != seq[k-1][1] or t == '11:05'))
            tot=[0,0,0]
            for bs in data:
                o = (await pg.evaluate(EVAL, bs))[0]; tot[0]+=o[2]; tot[1]+=o[0]; tot[2]+=o[1]
            print(f"K{N:2d} | 時碩 {win} | 全部 對 {tot[0]} 錯 {tot[1]} 勝率 {tot[0]*100/max(1,tot[0]+tot[1]):.1f}% 淨 {tot[0]-tot[1]}", flush=True)
        await b.close()
asyncio.run(main())
