import asyncio, json, copy
from playwright.async_api import async_playwright
P=json.load(open('/home/user/Stock-database/pool.json',encoding='utf-8'))
A=copy.deepcopy(P); A['date']='2026-10-16'; A['generated']='2026-10-16 16:30'; A['elig_checked_at']='2026-10-19 09:05'
A['pool']=[s for s in A['pool'] if s['name'] not in ('台虹','富喬','凱崴')]
B=copy.deepcopy(P); B['generated']='2026-10-19 17:46'; B.pop('elig_checked_at',None)
C=copy.deepcopy(B); C['elig_checked_at']='2026-10-20 08:45'
STATE={'v':A,'hits':0}
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path='/opt/pw-browsers/chromium', args=['--no-sandbox'])
        ctx = await b.new_context(timezone_id='Asia/Taipei', locale='zh-TW', viewport={'width':412,'height':915})
        pg = await ctx.new_page(); errs=[]; pg.on('pageerror', lambda e: errs.append(str(e)))
        async def route(r):
            u=r.request.url
            if 'pool.json' in u: STATE['hits']+=1; await r.fulfill(status=200, content_type='application/json', body=json.dumps(STATE['v'],ensure_ascii=False))
            elif '?log=' in u or '?bars=' in u: await r.fulfill(status=200, body='[]')
            else: await r.continue_()
        await pg.route('**/*', route)
        await pg.add_init_script("window.Android = { monitorRunning: () => true, isService: () => false, notify: () => {}, log: () => {}, ready: () => {}, logDays: () => '[]', logFor: () => '[]' };")
        await pg.clock.install(time='2026-10-19T14:20:00+08:00')
        await pg.goto('file:///home/user/Stock-database/app/app.html'); await pg.wait_for_timeout(4000)
        st = lambda: pg.evaluate("() => ({date: SRC_DATE, stamp: SRC_STAMP, n: LIST_CODES.length, has: ['8039','1815'].map(c => LIST_CODES.includes(c)), status: document.getElementById('status').textContent})")
        print('初始(14:20,昨天名單)', await st(), 'hits', STATE['hits'])
        STATE['v']=B
        print('14:20 poolWatch →', await pg.evaluate("() => poolWatch()"), 'hits', STATE['hits'])
        await pg.clock.set_system_time('2026-10-19T14:41:00+08:00'); await pg.clock.set_fixed_time('2026-10-19T14:41:00+08:00')
        print('14:41 poolWatch →', await pg.evaluate("() => poolWatch()"), 'hits', STATE['hits']); await pg.wait_for_timeout(1500)
        print('換名單後', await st())
        print('14:45 再呼叫(10 分內) →', await pg.evaluate("() => poolWatch()"))
        await pg.clock.set_fixed_time('2026-10-19T14:52:00+08:00')
        print('14:52 沒變 →', await pg.evaluate("() => poolWatch()"), 'hits', STATE['hits'])
        STATE['v']=C
        await pg.clock.set_fixed_time('2026-10-20T08:46:00+08:00')
        print('次日 08:46 複檢 →', await pg.evaluate("() => poolWatch()")); await pg.wait_for_timeout(1000); print('複檢後', await st())
        await pg.clock.set_fixed_time('2026-10-20T10:30:00+08:00')
        print('盤中 10:30 →', await pg.evaluate("() => poolWatch()"))
        print('errors', errs); await b.close()
asyncio.run(main())
