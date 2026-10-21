import asyncio, json, csv, datetime, os, sys
from playwright.async_api import async_playwright
R='/home/user/Stock-database'
STK=[c for c in sorted(os.listdir(f'{R}/kline')) if c.isdigit()]
part, nparts = int(sys.argv[1]), int(sys.argv[2])
def load(code, days):
    bars=[]
    for wk in ('2026-W41','2026-W42','2026-W43'):
        f=f'{R}/kline/{code}/{wk}.csv'
        if not os.path.exists(f): continue
        for r in csv.DictReader(open(f)):
            if r['date'] not in days or float(r['low'])<=0: continue
            t=int(datetime.datetime.strptime(r['date']+' '+r['time'],'%Y-%m-%d %H:%M').replace(tzinfo=datetime.timezone(datetime.timedelta(hours=8))).timestamp())
            bars.append({'time':t,'open':float(r['open']),'high':float(r['high']),'low':float(r['low']),'close':float(r['close']),'volume':int(float(r['volume']))})
    bars.sort(key=lambda b:b['time']); return bars
EVAL = r"""([bars, br, day]) => {
  const base = Date.parse(day + 'T00:00:00+08:00') / 1000; BREADTH.d = day; BREADTH.base = base; BREADTH.arr = Object.keys(br).sort().map(k => [base + (+k.slice(0, 2)) * 3600 + (+k.slice(3, 5)) * 60, br[k]]);
  RULESET = RULESETS['一']; SIG = RULESET.sig;
  document.getElementById('sheet').classList.add('open'); drawBars(bars, 'x', 'test', false);
  const ld = dayKey(BARS[BARS.length-1].time); const T = BARS.filter(b => dayKey(b.time) === ld);
  const LC = T.map(b => CUR.lamps.get(b.time) || '');
  // 判一
  RULESET = RULESETS['一']; SIG = RULESET.sig;
  const ma = sma(BARS, BASE.maPeriod), ob = obv(BARS), obma = sma(ob, BASE.obvMaPeriod, 'value');
  const pd = BARS.filter(b => dayKey(b.time) < ld); const prev = pd.length ? pd[pd.length-1].close : null;
  const rp = replaySignals(BARS, ma, ob, obma, prev, undefined);
  const ms = rp.marks.filter(m => dayKey(m.time) === ld && m.shape !== 'circle' && /^[多空]/.test(m.text));
  const mAt = new Map(); ms.forEach(m => mAt.set(m.time, m));
  const LP = [], LS = [], LG = []; let dir = 0, conf = false;
  for (let i = 0; i < T.length; i++){
    const t = T[i].time, st = rp.stateAt.get(t);
    LP.push(st && st.side ? (st.side === 'long' ? '做多' : '做空') : '監控');
    const m = mAt.get(t); if (m){ const d = m.text[0] === '多' ? 1 : -1; if (d !== dir){ dir = d; conf = false; } if (m.text.includes('✓') || m.color === CHART_THEME.up || m.color === CHART_THEME.down) conf = true; }
    LS.push(hmKey(t) >= '13:25' ? '監控' : dir === 0 ? '監控' : (conf ? (dir > 0 ? '做多' : '做空') : (dir > 0 ? '漸多' : '漸空')));
    let g = LC[i]; if (g === '做多' && dir <= 0) g = '漸多'; if (g === '做空' && dir >= 0) g = '漸空'; LG.push(g);
  }
  RULESET = RULESETS['一']; SIG = RULESET.sig;
  const ft = (i, sd) => { const e = T[i].close, up = e * 1.01, dn = e * 0.99; for (let j = i + 1; j < Math.min(T.length, i + 61); j++){ const hu = T[j].high >= up, hd = T[j].low <= dn; if (hu && hd) return 0; if (hu) return sd > 0 ? 1 : -1; if (hd) return sd < 0 ? 1 : -1; } return 0; };
  const sc = L => { const o = [0,0,0]; for (let i = 0; i < T.length; i++){ if (L[i] !== L[i-1] && (L[i] === '做多' || L[i] === '做空')) o[ft(i, L[i] === '做多' ? 1 : -1) + 1]++; } return o; };
  return {cur: sc(LC), pos: sc(LP), sig: sc(LS), gate: sc(LG)}; }"""
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path='/opt/pw-browsers/chromium', args=['--no-sandbox'])
        pg = await (await b.new_context(timezone_id='Asia/Taipei')).new_page()
        await pg.add_init_script("window.Android={getRuleset:()=>'一',setRuleset:()=>{},monitorRunning:()=>true,isService:()=>false,notify:()=>{},log:()=>{},ready:()=>{}}; const real=window.fetch; window.fetch=async(u,i)=>{u=String(u); return /\\?(log|bars|codes|breadth)=/.test(u)?new Response('[]',{status:200}):real(u,i)};")
        await pg.goto(f'file://{R}/app/app.html'); await pg.wait_for_timeout(2500)
        BR=json.load(open(f'{R}/tools/backtest/results/breadth_2026-10-06_21.json'))
        tot={k:[0,0,0] for k in ('cur','pos','sig','gate')}; n=0
        for ci, code in enumerate(STK):
            if ci % nparts != part: continue
            for days in (('2026-10-12','2026-10-13'),('2026-10-13','2026-10-14'),('2026-10-14','2026-10-15'),('2026-10-15','2026-10-16'),('2026-10-16','2026-10-19'),('2026-10-19','2026-10-20'),('2026-10-20','2026-10-21')):
                bs=load(code,days)
                if len(bs)<300: continue
                r = await pg.evaluate(EVAL, [bs, BR.get(days[1], {}), days[1]]); n+=1
                for k in tot:
                    o=r[k]; tot[k][0]+=o[2]; tot[k][1]+=o[0]; tot[k][2]+=o[1]
        print(json.dumps({'n':n,'tot':tot}), flush=True)
        await b.close()
asyncio.run(main())
