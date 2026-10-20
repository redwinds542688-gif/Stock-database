import asyncio, json, csv, datetime, os
from playwright.async_api import async_playwright
STK=[('5498','凱崴'),('1815','富喬'),('2409','友達'),('4927','泰鼎'),('2489','瑞軒'),('4906','正文'),('6226','光鼎'),('4989','榮科'),('3717','聯嘉投控'),('5340','建榮'),('1809','中釉'),('6505','台塑化'),('6668','中揚光'),('6209','今國光')]
def load(code, days):
    bars=[]
    for wk in ('2026-W42','2026-W43'):
        f=f'/home/user/Stock-database/kline/{code}/{wk}.csv'
        if not os.path.exists(f): continue
        for r in csv.DictReader(open(f)):
            if r['date'] not in days or float(r['low'])<=0: continue
            t=int(datetime.datetime.strptime(r['date']+' '+r['time'],'%Y-%m-%d %H:%M').replace(tzinfo=datetime.timezone(datetime.timedelta(hours=8))).timestamp())
            bars.append({'time':t,'open':float(r['open']),'high':float(r['high']),'low':float(r['low']),'close':float(r['close']),'volume':int(float(r['volume']))})
    bars.sort(key=lambda b:b['time']); return bars
LAMPS_JS = open('ind3Lamps_hook.js').read()
CFGS = json.load(open('cfgs.json'))
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path='/opt/pw-browsers/chromium', args=['--no-sandbox'])
        pg = await (await b.new_context(timezone_id='Asia/Taipei')).new_page()
        await pg.add_init_script("window.Android={getRuleset:()=>'一',setRuleset:()=>{},monitorRunning:()=>true,isService:()=>false,notify:()=>{},log:()=>{},ready:()=>{}}; const real=window.fetch; window.fetch=async(u,i)=>{u=String(u); return (u.includes('?log=')||u.includes('?bars=')||u.includes('?codes='))?new Response('[]',{status:200}):real(u,i)};")
        await pg.goto('file:///home/user/Stock-database/app/app.html'); await pg.wait_for_timeout(2500)
        await pg.evaluate(LAMPS_JS)
        data=[]
        for code,nm in STK:
            for days in (('2026-10-16','2026-10-19'),('2026-10-19','2026-10-20')):
                bars=load(code,days)
                if len([x for x in bars if True])<300: continue
                data.append((nm,days[1],bars))
        res={}
        for cname,cfg in CFGS.items():
            tot=[0,0,0]; per={}
            for nm,day,bars in data:
                r = await pg.evaluate("""([bars, cfg]) => { window.__cfg = cfg; document.getElementById('sheet').classList.add('open'); drawBars(bars, 'x', 'test', false);
                   const ld = dayKey(BARS[BARS.length-1].time); const T = BARS.filter(b => dayKey(b.time) === ld); const L = T.map(b => CUR.lamps.get(b.time));
                   const ft = (i, sd) => { const e = T[i].close, up = e * 1.005, dn = e * 0.995; for (let j = i + 1; j < Math.min(T.length, i + 31); j++){ const hu = T[j].high >= up, hd = T[j].low <= dn; if (hu && hd) return 0; if (hu) return sd > 0 ? 1 : -1; if (hd) return sd < 0 ? 1 : -1; } return 0; };
                   const segs = []; for (let i = 0; i < T.length; i++){ if (L[i] !== L[i-1] && (L[i] === '做多' || L[i] === '做空')) segs.push(hmKey(T[i].time) + L[i][1] + ['✗','–','✓'][ft(i, L[i] === '做多' ? 1 : -1) + 1]); }
                   return [segs, L.join(',')]; }""", [bars, cfg])
                segs=r[0]; tot[0]+=sum(s.endswith('✓') for s in segs); tot[1]+=sum(s.endswith('✗') for s in segs); tot[2]+=sum(s.endswith('–') for s in segs)
                per[nm+day[5:]]=(segs, r[1])
            res[cname]=(tot,per)
        base=res.get('現行')
        for cname,(tot,per) in res.items():
            ch=[k for k in per if base and per[k][1]!=base[1][k][1]]
            print(f"{cname:22s} 對 {tot[0]:3d} 錯 {tot[1]:3d} 平 {tot[2]:3d}  勝率 {tot[0]*100//max(1,tot[0]+tot[1])}%  有變的股票日 {len(ch)}/{len(per)}")
        json.dump({k:{kk:vv[0] for kk,vv in v[1].items()} for k,v in res.items()}, open('lamp_search_out.json','w'), ensure_ascii=False)
        await b.close()
asyncio.run(main())
