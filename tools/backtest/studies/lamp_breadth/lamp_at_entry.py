import asyncio, json, csv, datetime, os, re, glob
from playwright.async_api import async_playwright
R='/home/user/Stock-database'; B=f'{R}/tools/backtest'
nm={}
for f in glob.glob(f'{B}/pools/*.json')+glob.glob(f'{R}/kline_cloud/pools/*.json'):
    try:
        for s in json.load(open(f,encoding='utf-8'))['pool']: nm[s.get('name')]=s['code']
    except Exception: pass
def rows(path, ok):
    out=[]
    for f in sorted(glob.glob(path)):
        for r in csv.DictReader(open(f)):
            if not ok(r['date']) or float(r['low'])<=0: continue
            t=int(datetime.datetime.strptime(r['date']+' '+r['time'],'%Y-%m-%d %H:%M').replace(tzinfo=datetime.timezone(datetime.timedelta(hours=8))).timestamp())
            out.append({'time':t,'open':float(r['open']),'high':float(r['high']),'low':float(r['low']),'close':float(r['close']),'volume':int(float(r['volume']))})
    out.sort(key=lambda b:b['time']); return out
def trades(fn, cloud, judge):
    out=[]
    for l in open(f'{B}/results/{fn}',encoding='utf-8'):
        m=re.match(r'(\d\d-\d\d) (\S+) (做多|做空) (\d\d:\d\d) 進 .*淨 ([+\-−]?[\d,]+)',l)
        if m: d,n,sd,t,net=m.groups(); out.append(dict(judge=judge,day='2026-'+d,name=n,side=sd,tm=t,net=int(net.replace('−','-').replace(',','')),cloud=cloud))
    return out
T = trades('blk9_j1_b1.txt',0,'判一')+trades('xhl_j1_1_cloud2d.txt',1,'判一')+trades('breadth_j2_black_9d_blk.txt',0,'判二')+trades('breadth_j2_black_cloud2d.txt',1,'判二')
BR=json.load(open(f'{B}/results/breadth_2026-10-06_21.json'))
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path='/opt/pw-browsers/chromium', args=['--no-sandbox'])
        pg = await (await b.new_context(timezone_id='Asia/Taipei')).new_page()
        await pg.add_init_script("window.Android={getRuleset:()=>'一',setRuleset:()=>{},monitorRunning:()=>true,isService:()=>false,notify:()=>{},log:()=>{},ready:()=>{}}; const real=window.fetch; window.fetch=async(u,i)=>{u=String(u); return /\\?(log|bars|codes|breadth)=/.test(u)?new Response('[]',{status:200}):real(u,i)};")
        await pg.goto(f'file://{R}/app/app.html'); await pg.wait_for_timeout(2500)
        out=[]
        for tr in T:
            code=nm.get(tr['name']); d=tr['day']
            if not code: print('no code', tr['name']); continue
            hist=rows(f'{R}/kline/{code}/2026-W4*.csv', lambda x: x < d)
            if not hist: continue
            pdays=sorted({datetime.datetime.fromtimestamp(x['time'],datetime.timezone(datetime.timedelta(hours=8))).strftime('%Y-%m-%d') for x in hist})[-1:]
            hist=[x for x in hist if datetime.datetime.fromtimestamp(x['time'],datetime.timezone(datetime.timedelta(hours=8))).strftime('%Y-%m-%d') in pdays]
            today=rows(f'{R}/kline_cloud/{code}/2026-W*.csv' if tr['cloud'] else f'{R}/kline/{code}/2026-W4*.csv', lambda x: x == d)
            r = await pg.evaluate("""([bars, br, day, tm]) => { const base = Date.parse(day + 'T00:00:00+08:00') / 1000; BREADTH.base = base; BREADTH.arr = Object.keys(br).sort().map(k => [base + (+k.slice(0, 2)) * 3600 + (+k.slice(3, 5)) * 60, br[k]]);
                document.getElementById('sheet').classList.add('open'); drawBars(bars, 'x', 'x', false);
                const ld = dayKey(BARS[BARS.length-1].time); const D = BARS.filter(b => dayKey(b.time) === ld);
                let at = null, i0 = -1; D.forEach((b, i) => { if (hmKey(b.time) <= tm){ at = CUR.lamps.get(b.time); i0 = i; } });
                return [at || '', i0 > 0 ? (CUR.lamps.get(D[i0-1].time) || '') : '']; }""", [hist+today, BR.get(d,{}), d, tr['tm']])
            tr['lamp'], tr['lamp_prev'] = r; out.append(tr)
        json.dump(out, open('/tmp/claude-0/-home-user/63200f43-e3d1-5183-9eb6-04fa1ce41864/scratchpad/lamp_at_entry.json','w'), ensure_ascii=False)
        await b.close()
asyncio.run(main())
