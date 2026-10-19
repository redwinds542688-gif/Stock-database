import asyncio, csv, datetime, json
from playwright.async_api import async_playwright
def load(code, day):
    bars=[]
    for wk in ('2026-W42','2026-W43'):
        try: rows=list(csv.DictReader(open(f'/home/user/Stock-database/kline/{code}/{wk}.csv')))
        except FileNotFoundError: continue
        for r in rows:
            if r['date']>day: continue
            t=int(datetime.datetime.strptime(r['date']+' '+r['time'],'%Y-%m-%d %H:%M').replace(tzinfo=datetime.timezone(datetime.timedelta(hours=8))).timestamp())
            bars.append({'time':t,'open':float(r['open']),'high':float(r['high']),'low':float(r['low']),'close':float(r['close']),'volume':int(float(r['volume']))})
    return bars
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path='/opt/pw-browsers/chromium', args=['--no-sandbox'])
        pg = await (await b.new_context(timezone_id='Asia/Taipei', locale='zh-TW', viewport={'width':412,'height':915})).new_page(); errs=[]; pg.on('pageerror', lambda e: errs.append(str(e)))
        await pg.add_init_script("window.Android = { monitorRunning: () => true, isService: () => false, notify: () => {}, log: () => {}, ready: () => {}, logDays: () => '[]', logFor: () => '[]', getRuleset: () => '一' }; const real = window.fetch; window.fetch = async (u, i) => (String(u).includes('?log=') || String(u).includes('?bars=')) ? new Response('[]', {status:200}) : real(u, i);")
        await pg.goto('file:///home/user/Stock-database/app/app.html'); await pg.wait_for_timeout(4000)
        bars=load('2312','2026-10-19')
        await pg.evaluate("bars => { BARS_MEM.set('2312', {ver: 'x', bars}); openChart('2312','金寶'); }", bars); await pg.wait_for_timeout(4000)
        print('MARKS', await pg.evaluate("() => MARKS.length"), 'showSignalMarks', await pg.evaluate("() => YH.showSignalMarks"), 'rs', await pg.evaluate("() => RULESET.name"))
        print('rp', await pg.evaluate("""bars => { const ma = sma(bars, BASE.maPeriod), ob = obv(bars), obma = sma(ob, BASE.obvMaPeriod, 'value'); const rp = replaySignals(bars, ma, ob, obma, 32.5, undefined);
          return rp.marks.length + ' all | ' + rp.marks.filter(m => m.shape === 'circle' || m.color === CHART_THEME.up || m.color === CHART_THEME.down).map(m => hmKey(m.time) + m.text).join(' ') + ' | sample colors ' + [...new Set(rp.marks.map(m => m.color))].join(',') + ' theme ' + CHART_THEME.up + '/' + CHART_THEME.down; }""", bars))
        print('BARS_CODE', await pg.evaluate("() => [BARS_CODE, (BARS_MEM.get('2312')||{}).bars && BARS_MEM.get('2312').bars.length]"))
        await pg.evaluate("bars => { const i0 = bars.findIndex(b => new Date(b.time*1000).getHours() >= 9 && new Date(b.time*1000).toDateString() === new Date(bars[bars.length-1].time*1000).toDateString()); const n = bars.length; const from = Math.max(0, i0 - 2), to = Math.min(n - 1, i0 + 95); chart.timeScale().setVisibleLogicalRange({from, to}); try{ chartM.timeScale().setVisibleLogicalRange({from, to}); }catch(e){} try{ chartB.timeScale().setVisibleLogicalRange({from, to}); }catch(e){} }", bars); await pg.wait_for_timeout(1500)
        await pg.screenshot(path='chart_marks_2312.png'); print('errors', errs); await b.close()
asyncio.run(main())
