import asyncio, json
from playwright.async_api import async_playwright
items=[{"t":"10:38","title":"✅ 金寶 2312 OBV 反轉提早停利 +1.6% 立即平倉","body":"10:38 做空 09:23 進 31.95 → 現價 31.45 · 判一","kind":"profit","prev":32.5},
 {"t":"09:23","title":"金寶 2312 做空進場 31.95","body":"09:23 空✓ · 停利 31.15 停損 32.43 · 判一","kind":"short","prev":32.5},
 {"t":"12:20","title":"⚠ 燿華 2367 可能正在反彈,注意","body":"12:20 現價 46.4 · OBV 反向穿越 OBV 均20 · 判一","kind":"warn","prev":46.0},
 {"t":"12:15","title":"⚠ 台玻 1802 股價已反彈","body":"12:14 現價 67.4 · OBV 反向穿越 OBV 均30,帳面未達 1% 不平倉 · 判二","kind":"warn","prev":66.9},
 {"t":"11:48","title":"⚠ 台玻 1802 可能正在反彈,注意","body":"11:48 現價 68.2 · OBV 反向穿越 OBV 均20 · 判二","kind":"warn","prev":66.9},
 {"t":"12:41","title":"台玻 1802 出場:出","body":"12:38 現價 67.3 · 判一","kind":"exit","prev":66.9},
 {"t":"12:38","title":"台玻 1802 出場:出","body":"12:38 現價 67.4 · 判一","kind":"exit","prev":66.9},
 {"t":"12:36","title":"台玻 1802 出場:出","body":"12:35 現價 67.5 · 判一","kind":"exit","prev":66.9},
 {"t":"11:17","title":"台玻 1802 做多進場 67.50","body":"11:14 多✓ · 停利 69.19 停損 66.49 · 判二","kind":"long","prev":66.9},
 {"t":"10:52","title":"台玻 1802 做多強訊號進場 67.40","body":"10:52 多✓強 · 停利 69.08 停損 66.39 · 判二","kind":"long","prev":66.9},
 {"t":"10:02","title":"台玻 1802 做多進場 67.70","body":"10:02 多✓ · 停利 69.39 停損 66.68 · 判一","kind":"long","prev":66.9},
 {"t":"09:50","title":"燿華 2367 做多進場 46.00","body":"09:50 多✓ · 停利 47.15 停損 45.31 · 判一","kind":"long","prev":46.0}]
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path='/opt/pw-browsers/chromium', args=['--no-sandbox'])
        pg = await (await b.new_context(timezone_id='Asia/Taipei', locale='zh-TW', viewport={'width':412,'height':915})).new_page(); errs=[]; pg.on('pageerror', lambda e: errs.append(str(e)))
        await pg.add_init_script("""const n = new Date(); const TODAY = n.getFullYear() + '-' + String(n.getMonth()+1).padStart(2,'0') + '-' + String(n.getDate()).padStart(2,'0'); const ITEMS = __L__;
          window.Android = { monitorRunning: () => true, isService: () => false, notify: () => {}, log: () => {}, ready: () => {}, logDays: () => JSON.stringify([TODAY]), logFor: (d) => d === TODAY ? JSON.stringify(ITEMS) : '[]', getRuleset: () => '一' };
          const real = window.fetch; window.fetch = async (u, i) => (String(u).includes('?log=') || String(u).includes('?bars=')) ? new Response('[]', {status:200}) : real(u, i);""".replace('__L__', json.dumps(items, ensure_ascii=False)))
        await pg.goto('file:///home/user/Stock-database/app/app.html'); await pg.wait_for_timeout(5000)
        r = await pg.evaluate("() => [...document.querySelectorAll('#notifLog .card')].map(c => (c.querySelector('.nm')||{}).textContent + ' ' + [...c.querySelectorAll('.rs')].map(x => x.textContent).join('/') + ' | ' + [...c.querySelectorAll('.al')].map(x => x.className + '=' + getComputedStyle(x).color).join(','))")
        print(await pg.evaluate("() => [...document.querySelectorAll('#notifLog .card')].map(c => c.className + ' | ' + c.querySelector('.nm').textContent)"))
        await pg.wait_for_timeout(1500); print('NL_STATUS', await pg.evaluate('() => window.NL_STATUS'))
        print('清單格', await pg.evaluate("() => [...document.querySelectorAll('#bands .row')].slice(0,6).map(r => r.querySelector('.nm').textContent + ':' + r.querySelector('.posline').textContent + '(' + (r.querySelector('.pos')||{}).className + ')')"))
        await pg.screenshot(path='status_shot2.png', full_page=False)
        print('cards', len(r), '則標', await pg.evaluate("() => document.querySelector('#notifLog .hd').textContent"), 'errors', errs); await b.close()
asyncio.run(main())
