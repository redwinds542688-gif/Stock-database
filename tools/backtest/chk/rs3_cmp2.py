import os
import asyncio, csv, datetime, os, sys, json, re, subprocess
from playwright.async_api import async_playwright
S=os.environ.get('SIM_DIR', os.path.dirname(os.path.abspath(__file__)))
RS=sys.argv[1]
J1="OBMASLOPE=4 FPGAP=2.5 TAKE=2.5 VWAPF=1 FP=1 MAXDEV=1 MIRROR=1 PENDVOL=1 VOLMIN=6000 RVOLMIN=0.9 CAP=350000 REVOK=1 ENTRYMAX=3.5 OBVX=2 OBVXMIN=1 OBVX2=1 OB2=30 SLOPEMIN=-99 GAPMODE=dir GAPTH=2 OB3MA=6 REDSLOPE=-2 EXITRESET=tp OBVDIR=30 NEARHL=2 NOWEAK=1 STOPMODE=pts STOPSCOPE=last2 STOPKBAR=close STOPCAP=3 CHOP=1 CHOPW=45"
J3="FPGAP=2.5 TAKE=2.5 VWAPF=0 FP=1 MAXDEV=99 MIRROR=1 PENDVOL=1 VOLMIN=0 RVOLMIN=0 CAP=350000 REVOK=1 ENTRYMAX=3.5 OBVX=2 OBVXMIN=1 OBVX2=1 OB2=30 SLOPEMIN=-99 GAPMODE=dir GAPTH=99 OB3MA=6 REDSLOPE=-2 EXITRESET=tp OBVDIR=0 NEARHL=0 NOLOCK=1 NOENTRYAFTER=12:00 MODE=brk BRKBELOW=1 BRKVBASE=pre OBMASLOPE=0 BRKGAP=0 BRKGAPREF=pool"
J4="FPGAP=2.5 TAKE=2.5 VWAPF=0 FP=1 MAXDEV=99 MIRROR=1 PENDVOL=1 VOLMIN=0 RVOLMIN=0 CAP=350000 REVOK=1 ENTRYMAX=3.5 OBVX=2 OBVXMIN=1 OBVX2=1 OB2=30 SLOPEMIN=-99 GAPMODE=dir GAPTH=99 OB3MA=6 REDSLOPE=-2 EXITRESET=tp OBVDIR=0 NEARHL=0 NOLOCK=1 NOENTRYAFTER=12:00 OBMASLOPE=0 MODE=press CHOP=1 CHOPW=45"   # 2026-10-20 判四 壓線空點
BASE=(J1 if RS=="一" else J3 if RS=="三" else J4 if RS=="四" else "FPGAP=2.5 TAKE=2.5 VWAPF=1 FP=1 MAXDEV=1 MIRROR=1 PENDVOL=1 VOLMIN=6000 RVOLMIN=0.9 CAP=350000 REVOK=1 ENTRYMAX=3.5 OBVX=2 OBVXMIN=1 OBVX2=1 OB2=30 SLOPEMIN=-99 GAPMODE=dir GAPTH=2 OB3MA=5 REDSLOPE=-2 EXITRESET=tp MODE=cross XWIN=10 OBMASLOPE=0 XSTR=2 XNEWHI=1 XOB2=20 XOB2DIR=slope XKMA=0 NOLOCK=1 STRONG=1 SREQ=1030,dx,ext SBARS=2 SWIN=5 STRONGMAX=4 SNOAFTER=11:30 MINGAP=4 NOENTRYAFTER=12:00 XOB3=30 WEAKORDER=1 STOPMODE=pts STOPSCOPE=last2 STOPKBAR=close STOPCAP=3 STOPMIN=1.5 OB10DIR=1 CHOP=1 CHOPW=45") + ("" if RS=="三" else " OBVDIR=30" + (" NEARHL=2" if RS=="二" else ""))
ENV=dict(os.environ); ENV.update(dict(kv.split('=',1) for kv in BASE.split()))
def load(code, upto):
    bars=[]
    for wk in ('2026-W41','2026-W42'):
        f=f'/home/user/Stock-database/kline/{code}/{wk}.csv'
        if not os.path.exists(f): continue
        for r in csv.DictReader(open(f)):
            if r['date']>upto: continue
            t=int(datetime.datetime.strptime(r['date']+' '+r['time'],'%Y-%m-%d %H:%M').replace(tzinfo=datetime.timezone(datetime.timedelta(hours=8))).timestamp())
            bars.append({'time':t,'open':float(r['open']),'high':float(r['high']),'low':float(r['low']),'close':float(r['close']),'volume':int(float(r['volume']))})
    return bars
JS='''([bars, prev]) => { const ma = sma(bars, BASE.maPeriod), ob = obv(bars), obma = sma(ob, BASE.obvMaPeriod, 'value');
  const rp = replaySignals(bars, ma, ob, obma, prev, undefined); const ld = dayKey(bars[bars.length-1].time);
  const ms = rp.marks.filter(m => dayKey(m.time) === ld);
  const ent = ms.filter(m => m.shape !== 'circle' && (m.color === CHART_THEME.up || m.color === CHART_THEME.down)).map(m => hmKey(m.time) + (m.text.startsWith('多') ? '多' : '空') + (m.text.includes('強') ? '強' : ''));
  const ex = ms.filter(m => m.shape === 'circle' && ['出','停利','停損','超6%','OBV'].includes(m.text)).map(m => hmKey(m.time) + m.text);
  return {rs: RULESET.name, ent, ex, strong: ms.filter(m => m.text.includes('強')).map(m => hmKey(m.time) + m.text)}; }'''
EXMAP={'停利 2.5%':'停利','停損 1.5%':'停損','13:25 強制平倉':'出','OBV反向穿越(獲利中)':'OBV','反向確認點':'出','逆勢單遇反向點':'出','超過 6%':'超6%'}
def sim(code, name, day):
    out=subprocess.run(['python3','-I',f'{S}/sim_day.py',code,name,'10','10'],capture_output=True,text=True,env=dict(ENV,DAY=day,POOL=f'{S}/pools/hi350_{day}.json')).stdout
    ent=[]; ex=[]
    for l in out.splitlines():
        m=re.match(r'(做多|做空) (\d\d:\d\d) 進 .*?→ (\d\d:\d\d) 出 .*?\[(.*?)\]', l)
        if m: ent.append(m.group(2)+('多' if m.group(1)=='做多' else '空')); ex.append(m.group(3)+('停損' if '停損' in m.group(4) else EXMAP.get(m.group(4),m.group(4))))
    strong=[l.strip() for l in out.splitlines() if '強' in l and '進場' in l]
    return ent, ex, strong, out
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path='/opt/pw-browsers/chromium', args=['--no-sandbox'])
        ctx = await b.new_context(timezone_id='Asia/Taipei', locale='zh-TW'); pg = await ctx.new_page(); errs=[]; pg.on('pageerror', lambda e: errs.append(str(e)))
        await pg.add_init_script("const real = window.fetch; window.fetch = async (u, i) => String(u).includes('?log=') || String(u).includes('?bars=') ? new Response('[]', {status:200}) : real(u, i); window.Android = { monitorRunning: () => true, isService: () => false, notify: () => {}, log: () => {}, ready: () => {}, todayLog: () => '[]', logDays: () => '[]', logFor: (d) => '[]' }; try{ localStorage.setItem('mysh.ruleset', '" + RS + "'); }catch(e){}")
        await pg.goto('file:///home/user/Stock-database/app/app.html'); await pg.wait_for_timeout(5000)
        tot=0; diff=0; nstrong=0
        for day in ['2026-10-13','2026-10-14','2026-10-15','2026-10-16']:
            P=json.load(open(f'{S}/pools/hi350_{day}.json',encoding='utf-8'))
            for s in P['pool']:
                code=s['code']; f=f'/home/user/Stock-database/kline/{code}/2026-W42.csv'
                if not os.path.exists(f) or day not in open(f).read(): continue
                prev = s['close'] if s.get('date','') < day else s['prev']
                r = await pg.evaluate(JS, [load(code, day), prev])
                se, sx, sst, out = sim(code, s.get('name',code), day)
                ae=[e.replace('強','') for e in r['ent']]
                tot+=1; nstrong+=sum(1 for e in r['ent'] if '強' in e)
                if ae!=se or r['ex']!=sx:
                    diff+=1; print(day[5:], s.get('name'), code, '\n  app 進', r['ent'], '出', r['ex'], '\n  sim 進', se, '出', sx)
        print('rs', r['rs'], 'checked', tot, 'diff', diff, 'app強進場', nstrong, 'errors', errs); await b.close()
asyncio.run(main())
