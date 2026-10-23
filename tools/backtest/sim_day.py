import os
import numpy as np, pandas as pd, json, sys, datetime, os
ENTRYMAX = float(os.environ.get('ENTRYMAX','5')); MAXDEV = float(os.environ.get('MAXDEV','99')); REVOK=int(os.environ.get('REVOK','0')); MAXFROMEXT=float(os.environ.get('MAXFROMEXT','99')); PENDVOL=os.environ.get('PENDVOL','0')=='1'; RVOLMIN=float(os.environ.get('RVOLMIN','0')); VWAPF=os.environ.get('VWAPF','0')=='1'; SLOPEMIN=float(os.environ.get('SLOPEMIN','-99')); VOLMIN=float(os.environ.get('VOLMIN','0')); KHIGH = os.environ.get('KHIGH','0')=='1'; FIRSTPIERCE = os.environ.get('FP','0')=='1'
MIRROR = os.environ.get('MIRROR','0')=='1'
S=os.environ.get('SIM_DIR', os.path.dirname(os.path.abspath(__file__))); ROOT=os.environ.get('REPO_DIR', os.path.abspath(os.path.join(S, '..', '..')))
code, name, MA, OB = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]); CAP = int(os.environ.get('CAP','350000')); DAY=os.environ.get('DAY','2026-10-15')
P = json.load(open(os.environ.get('POOL', f'{ROOT}/pool.json'), encoding='utf-8'))
P2 = json.load(open(f'{ROOT}/pool.json', encoding='utf-8')); P['pool'] = P['pool'] + [x for x in P2['pool'] if x['code'] not in {y['code'] for y in P['pool']}]
try:
    df = pd.concat([pd.read_csv(f, dtype={'date':str,'time':str}) for f in sorted(__import__('glob').glob(f'{ROOT}/kline/{code}/2026-W4*.csv'))], ignore_index=True)
    t = df[(df.date == DAY) & (df.time >= '09:00') & (df.time <= '13:30')].reset_index(drop=True)
    if os.environ.get('KSRC','yahoo') == 'cloud':   # 2026-10-20 使用者:「好 那只能針對用雲 K」:當天改讀 kline_cloud/(盤中判別式看的雲端快照 K),沒成交的分鐘照 App fillFlat 補平 K
        _cf = sorted(__import__('glob').glob(f'{ROOT}/kline_cloud/{code}/2026-W*.csv'))
        _cd = pd.concat([pd.read_csv(f, dtype={'date':str,'time':str}) for f in _cf], ignore_index=True) if _cf else pd.DataFrame(columns=['date','time'])
        _cd = _cd[(_cd.date == DAY) & (_cd.time >= '09:00') & (_cd.time <= '13:30')].drop_duplicates(['time'], keep='last').sort_values('time').reset_index(drop=True)
        if len(_cd) == 0: raise Exception('no cloud rows')
        _rows = []; _pm = None
        for _r in _cd.itertuples():
            _m = int(_r.time[:2]) * 60 + int(_r.time[3:5])
            if _pm is not None and 1 < _m - _pm[0] <= 60:
                for _k in range(_pm[0] + 1, _m): _rows.append((f'{_k//60:02d}:{_k%60:02d}', _pm[1], _pm[1], _pm[1], _pm[1], 0.0))
            _rows.append((_r.time, float(_r.open), float(_r.high), float(_r.low), float(_r.close), float(_r.volume))); _pm = (_m, float(_r.close))
        t = pd.DataFrame(_rows, columns=['time','open','high','low','close','volume'])
        _yd = df[df.date == DAY]; _yhl = dict(zip(_yd.time, zip(_yd.high.astype(float), _yd.low.astype(float))))   # XHL:Yahoo 同一分鐘的真實高低
    _s = [s for s in P['pool'] if s['code']==code][0]; prev = _s['close'] if _s.get('date','') < DAY else _s['prev']; src='GitHub 官方資料'   # 清單日期早於模擬日 → 昨收 = 清單那天的收盤
    if len(t)==0: raise Exception('no rows')
    _pk = df[df.date < DAY]; prevK = float(_pk.close.values[-1]) if len(_pk) else prev   # 2026-10-20 判三開盤缺口用:K 線裡前一個交易日的最後收盤(回測池檔可能是兩天前的,昨收會錯)
except Exception:
    r = json.load(open(f'{S}/y{code}.json'))['chart']['result'][0]; prev = r['meta']['chartPreviousClose']; q = r['indicators']['quote'][0]
    rows = [(datetime.datetime.fromtimestamp(ts, datetime.timezone(datetime.timedelta(hours=8))).strftime('%H:%M'), o, h, l, c, v or 0)
            for ts, o, h, l, c, v in zip(r['timestamp'], q['open'], q['high'], q['low'], q['close'], q['volume']) if o is not None and c is not None]
    rows = [r for r, ts in zip(rows, [x for x, o, c_ in zip(r['timestamp'], q['open'], q['close']) if o is not None and c_ is not None]) if datetime.datetime.fromtimestamp(ts, datetime.timezone(datetime.timedelta(hours=8))).strftime('%Y-%m-%d') == DAY]
    if not rows: print(f'{name} {DAY} 無資料'); sys.exit(0)
    t = pd.DataFrame(rows, columns=['time','open','high','low','close','volume']); t = t[(t.time >= '09:00') & (t.time <= '13:30')].reset_index(drop=True); src='Yahoo'
# 2026-10-21 使用者(光鼎 10/20 OBV 和元大不一樣 →「有傾斜角度 跟 原來沒傾斜的 判斷會有差別的」):BLK=1 和 App dropBlockTrades 同一套,
#   Yahoo 鉅額交易那根(量 ≥ 同日前後 10 根中位數 80 倍且 ≥ 300 張,09:00、13:30 除外)量改成中位數;量比基準的前 5 天也一樣處理
BLK = os.environ.get('BLK','0')=='1'
def _blk(vol, tms):
    raw = np.array(vol, dtype=float); out = raw.copy()
    for _i in range(len(raw)):
        if tms[_i] in ('09:00','13:30') or raw[_i] < 300000: continue
        _nb = np.concatenate([raw[max(0,_i-10):_i], raw[_i+1:_i+11]])
        if len(_nb) and raw[_i] >= 80*max(float(np.median(_nb)), 500): out[_i] = float(np.median(_nb))
    return out
if BLK: t = t.assign(volume=_blk(t.volume.values, list(t.time)))
c = np.round(t.close.values,2); o = np.round(t.open.values,2); h = np.round(t.high.values,2); l = np.round(t.low.values,2); v = t.volume.values/1000
ma = pd.Series(c).rolling(MA).mean().values
# 2026-10-24 03:05 使用者(「判一到判三 一起改」:Ka 借昨天的 K,09:01 就借昨天 9 根,和元大一樣):KACROSS=1 → 均價 10 跨日,前面接昨天最後 MA−1 根收盤
if os.environ.get('KACROSS','0') == '1':
    try:
        _pk2 = df[(df.date < DAY) & (df.time >= '09:00') & (df.time <= '13:30')].sort_values(['date','time']).close.values[-(MA-1):]
    except Exception: _pk2 = np.array([])
    _cc2 = np.concatenate([np.round(_pk2.astype(float),2), c]); ma = pd.Series(_cc2).rolling(MA).mean().values[len(_pk2):]
ob = np.zeros(len(c)); acc = 0
for i in range(1, len(c)):
    if c[i] > c[i-1]: acc += v[i]
    elif c[i] < c[i-1]: acc -= v[i]
    ob[i] = round(acc)
obma = pd.Series(ob).rolling(OB).mean().values
OB2 = int(os.environ.get('OB2','20')); obma2 = pd.Series(ob).rolling(OB2).mean().values   # 第二條 OBV 均線(平倉用)
OBF = int(os.environ.get('OBF','3')); obmaF = pd.Series(ob).rolling(OBF).mean().values   # 快速 OBV 均線(瞬間反彈警示用)
hi = np.maximum.accumulate(h); lo = np.minimum.accumulate(l)
# 2026-10-21 使用者(和益 09:53 手機停損、模擬器沒停 →「改 模擬器停損用 Yahoo 高低」):雲端 K 每分鐘只有一個快照價,高低點只是前後兩個快照,
#   盤中衝高拉回看不到(和益 09:53 / 09:55 雲端高 57.0 / 57.1,Yahoo 真實高 57.5 / 57.7;手機每 5 秒看即時價 57.2 就停損)。
#   KSRC=cloud 時停損 / 停利改用 Yahoo 同一分鐘的高低(和雲端高低取較寬的);停損出場價 = 停損價(手機碰到就用現價平),跳空超過才用下一根開盤。訊號照舊用雲端 K。XHL=0 關掉
XHL = os.environ.get('KSRC','yahoo') == 'cloud' and os.environ.get('XHL','1') == '1' and '_yhl' in globals()
xh = h.copy(); xl = l.copy()
if XHL:
    for _i, _tm in enumerate(t.time):
        if _tm in _yhl: xh[_i] = max(h[_i], round(_yhl[_tm][0], 2)); xl[_i] = min(l[_i], round(_yhl[_tm][1], 2))
cv = np.cumsum(v); vwapArr = np.cumsum((h+l+c)/3*v)/np.maximum(cv, 1e-9)
rvBase = None
# 2026-10-21 指標三測試(使用者:「先測 判一,二,三 效果好再改」):KAKB = Ka(均價 10,當天)要在 Kb(均價 24,跨日)之上才做多(L)/ 之下才做空(S);
#   ORG2 = 橘線(Oa 穿越 Ob 的點)要「雙升」才做多(L)/「雙降」才做空(S):最近兩個往上穿的低點、最近兩個往下穿的高點都墊高 / 都降低
KAKB = os.environ.get('KAKB',''); ORG2 = os.environ.get('ORG2','')
# 2026-10-23 GAPX = 乖離出場測試(使用者:「部位方向乖離超過 2% 時提醒「乖離過大」搭配判一二三 如何 試看看」):持倉中 (Ka−Kb)/Kb 順向超過 GAPX% 就出;KBP = Kb 期數(App 2026-10-23 起 28)
GAPX = float(os.environ.get('GAPX','0')); KBP = int(os.environ.get('KBP','28'))
if KAKB or GAPX > 0:
    _kbn = 24 if KAKB else KBP
    try:
        _pc = df[(df.date < DAY) & (df.time >= '09:00') & (df.time <= '13:30')].sort_values(['date','time']).close.values[-(_kbn-1):]
    except Exception: _pc = np.array([])
    _cc = np.concatenate([np.round(_pc.astype(float),2), c]); KB = pd.Series(_cc).rolling(_kbn, min_periods=1).mean().values[len(_pc):]
if ORG2:
    _up = []; _dn = []; ORGPAT = np.zeros(len(c))
    for _q in range(1, len(c)):
        if not np.isnan(obma[_q]) and not np.isnan(obma[_q-1]):
            _d0 = ob[_q-1]-obma[_q-1]; _d1 = ob[_q]-obma[_q]
            if _d1 > 0 and _d0 <= 0: _up.append(l[_q])
            elif _d1 < 0 and _d0 >= 0: _dn.append(h[_q])
        if len(_up) >= 2 and len(_dn) >= 2:
            if _up[-1] > _up[-2] and _dn[-1] > _dn[-2]: ORGPAT[_q] = 1
            elif _up[-1] < _up[-2] and _dn[-1] < _dn[-2]: ORGPAT[_q] = -1
if RVOLMIN:
    import glob as _g
    fs = sorted(_g.glob(f'{ROOT}/kline/{code}/*.csv'))
    if fs:
        dfa = pd.concat([pd.read_csv(f, dtype={'date':str,'time':str}) for f in fs]).drop_duplicates(['date','time']).sort_values(['date','time'])
        dfa = dfa[(dfa.date < DAY) & (dfa.time >= '09:00') & (dfa.time <= '13:30')]
        if BLK: dfa = pd.concat([g.assign(volume=_blk(g.volume.values, list(g.time))) for _, g in dfa.groupby('date')])
        dfa['cum'] = dfa.groupby('date')['volume'].cumsum()
        pv = dfa.pivot_table(index='date', columns='time', values='cum', aggfunc='last').sort_index(axis=1).ffill(axis=1).tail(5)
        rvBase = pv.mean(axis=0).to_dict() if len(pv) >= 3 else None
print(f'{name} {DAY} [{src}] 均價{MA}/OBV{OB}  昨收 {prev}  開 {o[0]}  高 {h.max()}  低 {l.min()}  收 {c[-1]}  ({(c[-1]-prev)/prev*100:+.2f}%)')
TAKE = float(os.environ.get('TAKE','4'))
cyc = None; last = {1: None, -1: None}; first = {1: None, -1: None}; locked = {1: False, -1: False}; pending = {1: False, -1: False}; trades = []; log = []
LIMITFAR = os.environ.get('LIMITFAR',''); LIMITN = int(os.environ.get('LIMITN','10')); limitOrd = None   # 2026-10-18 測試:離均價 >1% 的確認點不追,改掛回檔價(low=訊號 K 的低/高點、ma=均價線),LIMITN 分鐘內沒成交取消
seq = {1: 0, -1: 0}; firstT = {1: None, -1: None}; lastTP = {1: None, -1: None}; COOLDOWN = int(os.environ.get('COOLDOWN','0'))   # 停利後同方向幾分鐘內不再進(999 = 當天不再進)
falseFirst = {1: None, -1: None}; FALSEGAP = float(os.environ.get('FALSEGAP','0'))   # 第一個虛假點(條件都對、只差沒壓線)的價格;FALSEGAP>0 時,確認點離它超過 FALSEGAP% 就不進
GAPTH = float(os.environ.get('GAPTH','3'));  GAPMODE = os.environ.get('GAPMODE','off')   # off=照舊;need3=跳空股要第 3 個訊號;skip=跳空股訊號全部失效
isGap = abs((o[0]-prev)/prev*100) > GAPTH
def _origin_stop(sd, upto):
    """2026-10-20 使用者(中揚光 09:25:「K 剛穿過均線、OBV 也剛穿過 OBV 均線,把這個點記起來,62.6~63.4 反彈沒有超過這個都不必贖回」):
    起跌點 / 起漲點 = 最近一次「K 收盤穿過均價」且「OBV 穿過 OBV 均線」(兩個穿越相差 ≤ STOPXW 根)那根;做空停損放在那根的高點,做多放在低點"""
    XW = int(os.environ.get('STOPXW','2'))
    _order = range(upto, 0, -1)
    if os.environ.get('STOPPICK','first') == 'first':   # first = 今天高點(做空)/ 低點(做多)之後第一次兩個都穿越的那根 = 起跌點本身;recent = 進場前最近一次
        _x = int(np.argmax(h[:upto+1])) if sd == -1 else int(np.argmin(l[:upto+1]))
        _order = list(range(max(1, _x+1), upto+1)) + list(range(upto, 0, -1))
    for j in _order:
        if np.isnan(ma[j]) or np.isnan(ma[j-1]): continue
        _kin = (lambda q: l[q] > ma[q]) if sd == 1 else (lambda q: h[q] < ma[q])   # 2026-10-20 使用者:起跌點用 K 最高價、起漲點用 K 最低價(整根 K 穿過均價才算)
        if os.environ.get('STOPKBAR','whole') == 'close': _kin = lambda q: (c[q]-ma[q])*sd > 0
        if not (_kin(j) and not _kin(j-1)): continue
        for k in range(max(1, j-XW), min(upto, j+XW)+1):
            if np.isnan(obma[k]) or np.isnan(obma[k-1]): continue
            if (ob[k]-obma[k])*sd > 0 and (ob[k-1]-obma[k-1])*sd <= 0:
                sb = max(j, k); return (h[sb] if sd == -1 else l[sb]), t.time[sb]
    return None, None
def _origin_points(upto):
    """2026-10-20 和 App originPoints 同一套:今天創新高後第一次「整根 K 跌破均價 + OBV 跌破 OBV 均線」(相差 ≤ 2 根)= 起跌;創新低後第一次兩個都往上穿 = 起漲。回傳 [(index, side)]"""
    out = []; hi = -1e18; lo = 1e18; armD = armU = False; lk = {1: -99, -1: -99}; lo2 = {1: -99, -1: -99}
    for q in range(0, upto + 1):
        if h[q] > hi: hi = h[q]; armD = True
        if l[q] < lo: lo = l[q]; armU = True
        if q == 0 or np.isnan(ma[q]) or np.isnan(ma[q-1]) or np.isnan(obma[q]) or np.isnan(obma[q-1]): continue
        for sd in (1, -1):
            _in = (lambda z: l[z] > ma[z]) if sd == 1 else (lambda z: h[z] < ma[z])
            if os.environ.get('STOPKBAR','whole') == 'close': _in = lambda z, sd=sd: (c[z]-ma[z])*sd > 0
            if _in(q) and not _in(q-1): lk[sd] = q
            if (ob[q]-obma[q])*sd > 0 and (ob[q-1]-obma[q-1])*sd <= 0: lo2[sd] = q
        if armD and q - lk[-1] <= 2 and q - lo2[-1] <= 2 and (lk[-1] == q or lo2[-1] == q): out.append((q, -1)); armD = False
        if armU and q - lk[1] <= 2 and q - lo2[1] <= 2 and (lk[1] == q or lo2[1] == q): out.append((q, 1)); armU = False
    return out
def _pts_stop(sd, upto, ent):
    """2026-10-20 使用者:「找出起漲起跌點,做空就以最高為準,不管是起漲或起跌;做多就找起漲起跌最低點」
    STOPSCOPE=last2:進場前最近一個起漲 + 最近一個起跌,做空取兩根 K 的最高價較高者、做多取最低價較低者;day:今天進場前全部的點。只取在進場價虧損那一側的"""
    P = _origin_points(upto)
    if os.environ.get('STOPSCOPE','last2') == 'last2':
        lastU = [q for q, s_ in P if s_ == 1][-1:]; lastD = [q for q, s_ in P if s_ == -1][-1:]; idx = lastU + lastD
    else: idx = [q for q, s_ in P]
    if sd == -1:
        cand = [h[q] for q in idx if h[q] > ent]; return (max(cand), ','.join(t.time[q] for q in idx)) if cand else (None, None)
    cand = [l[q] for q in idx if l[q] < ent]; return (min(cand), ','.join(t.time[q] for q in idx)) if cand else (None, None)
def _alt_points(upto):
    """2026-10-20 起漲 / 起跌點(整根 K、輪流):給震盪判斷用。回傳 [(index, side, price)]"""
    out = []; lk = {1: -99, -1: -99}; lo2 = {1: -99, -1: -99}; last = None
    for q in range(1, upto + 1):
        if np.isnan(ma[q-1]) or np.isnan(obma[q-1]) or np.isnan(ma[q]) or np.isnan(obma[q]): continue
        for sd in (1, -1):
            _in = (lambda z: l[z] > ma[z]) if sd == 1 else (lambda z: h[z] < ma[z])
            if _in(q) and not _in(q-1): lk[sd] = q
            if (ob[q]-obma[q])*sd > 0 and (ob[q-1]-obma[q-1])*sd <= 0: lo2[sd] = q
        for sd in (-1, 1):
            if q - lk[sd] <= 2 and q - lo2[sd] <= 2 and (lk[sd] == q or lo2[sd] == q) and last != sd:
                out.append((q, sd, h[q] if sd == -1 else l[q])); last = sd
    return out
def _is_chop(i):
    """2026-10-20 使用者:「起漲起跌距離太近,感覺就是橫盤,也就是震盪盤」:過去 CHOPW 根內 ≥ CHOPN 個輪流的起漲 / 起跌點,價格都在 CHOPR% 內 = 震盪"""
    W = int(os.environ.get('CHOPW','60')); N = int(os.environ.get('CHOPN','3')); RG = float(os.environ.get('CHOPR','1.5'))
    rec = [p for p in _alt_points(i) if i - W <= p[0] <= i]
    if os.environ.get('CHOPMODE','n') == 'pair': rec = rec[-2:]; N = 2   # 2026-10-20 測試:使用者「起漲跟起跌距離很近才是震盪」:只看最近一組相鄰的起漲 + 起跌
    if len(rec) < N: return False
    if os.environ.get('PAIRBRK','0') == '1' and len(rec) >= 2:   # 2026-10-20 測試:「第三個起漲是真正的起漲,價格就是看出真漲的訊號」:最新起漲整根 K 最低 > 前一個起跌最高(空:最新起跌最高 < 前一個起漲最低)= 真漲 / 真跌,不算震盪
        a, b = rec[-2], rec[-1]
        if (b[1] == 1 and a[1] == -1 and b[2] > a[2]) or (b[1] == -1 and a[1] == 1 and b[2] < a[2]): return False
    px = [p[2] for p in rec]
    if (max(px) - min(px)) / min(px) * 100 > RG: return False
    if os.environ.get('CHOPBRK','0') == '1' and globals().get('_chop_side'):   # 2026-10-20 測試:價格已經跌破(空)/ 站上(多)橫盤範圍 = 突破,放行
        if (globals()['_chop_side'] == -1 and c[i] < min(px)) or (globals()['_chop_side'] == 1 and c[i] > max(px)): return False
    return True
def _obc_str(i):
    """2026-10-20 使用者(富喬 10:00~10:55 OBV 三條線在 -20K~-24K 遊走):「用 OBV 觀測震盪」。進場點印出:視窗內 OBV / 均10 / 均30 三條線的上下寬度 ÷ 視窗成交量(eff)、÷ 當日 OBV 範圍(day)、藍紫交叉次數(x)"""
    out = []
    for W in (20, 30, 45):
        a = max(0, i-W+1); seg = np.concatenate([ob[a:i+1], obma[a:i+1], obma2[a:i+1]]); seg = seg[~np.isnan(seg)]
        band = (seg.max() - seg.min()) if len(seg) else 0; vs = max(np.sum(v[a:i+1]), 1e-9); dr = (np.nanmax(ob[:i+1]) - np.nanmin(ob[:i+1])) or 1
        d_ = obma[a:i+1] - obma2[a:i+1]; d_ = d_[~np.isnan(d_)]; x = int(np.sum(np.sign(d_[1:]) != np.sign(d_[:-1]))) if len(d_) > 1 else 0
        out.append(f'W{W} eff {band/vs:.3f} day {band/dr:.3f} x {x}')
    return ' | '.join(out)
def close_trade(sd, k0, ent, lots, tm, ex, why):
    gross = (ex-ent)*sd*1000*lots; fee = (ent+ex)*1000*lots*(0.001425*0.30 + 0.0000565); tax = (ex if sd==1 else ent)*1000*lots*0.0015
    trades.append((sd, k0, ent, lots, tm, ex, why, gross, fee, tax))
for i in range(1, len(c)):
    tm = t.time[i]
    if cyc:
        sd, k0, ent, lots = cyc; best = xh[i] if sd == 1 else xl[i]; worst = xl[i] if sd == 1 else xh[i]; _spx = None
        gain = (best-ent)/ent*100*sd; loss = (ent-worst)/ent*100*sd; move = (c[i]-prev)/prev*100
        why = None
        _FE = os.environ.get('FORCEEXIT', '13:20')   # 強制平倉時間:2026-10-22 起 13:20(使用者:「手機通知也改成13:20」),舊版 FORCEEXIT=13:25
        if tm >= _FE: why = f'{_FE} 強制平倉'
        elif gain >= TAKE: why = f'停利 {TAKE:g}%'
        # 2026-10-21 測試(使用者:今天和益 +1.6%、台郡 +0.9% 沒保住 →「跑」):BEPCT = 帳面曾經賺到 x% 後,價格回到進場價就保本出場;OBVXPEAK = 曾經賺到 x% 也算 OBV 反轉出場的「獲利中」
        if globals().get('_mfk') != (k0, ent): globals()['_mfk'] = (k0, ent); globals()['_mfe'] = 0.0
        _BE = float(os.environ.get('BEPCT','0'))
        if why is None and _BE > 0 and globals()['_mfe'] >= _BE and ((h[i] >= ent) if sd == -1 else (l[i] <= ent)): why = f'保本停損(曾賺 {globals()["_mfe"]:.1f}%)'
        elif os.environ.get('STOPMODE','pct') in ('origin', 'pts') and globals().get('_osk') != (k0, ent):
            globals()['_osk'] = (k0, ent); globals()['_ost'] = _origin_stop(sd, i-1) if os.environ.get('STOPMODE') == 'origin' else _pts_stop(sd, i-1, ent)
            if os.environ.get('TRACESTOP'): print(f'TRACESTOP {k0} 進 {ent} 起{"跌" if sd==-1 else "漲"}點 {globals()["_ost"]}')
        if why is None and os.environ.get('STOPMODE','pct') in ('origin', 'pts') and globals().get('_ost', (None,))[0] is not None and (globals()['_ost'][0]-ent)*sd < 0:
            _op = globals()['_ost'][0]; _risk = abs(_op-ent)/ent*100
            _cap = float(os.environ.get('STOPCAP','99')); _mn = float(os.environ.get('STOPMIN','0'))
            if _risk > _cap: _hit = loss >= _cap; _lb = f'停損 {_cap:g}%(起點太遠)'; _spx = ent*(1-_cap/100*sd)
            elif _risk < _mn: _hit = loss >= _mn; _lb = f'停損 {_mn:g}%(起點太近)'; _spx = ent*(1-_mn/100*sd)
            else: _hit = (xh[i] > _op) if sd == -1 else (xl[i] < _op); _lb = f'起{"跌" if sd==-1 else "漲"}點停損 {_op:g}'; _spx = _op
            if _hit: why = _lb
        elif why is None and loss >= float(os.environ.get('STOP','1.5')): why = f'停損 {float(os.environ.get("STOP","1.5")):g}%'; _spx = ent*(1-float(os.environ.get('STOP','1.5'))/100*sd)
        elif (sd == -1 and move >= 6) or (sd == 1 and move <= -6): why = '超過 6%'
        if why is None and GAPX > 0 and not np.isnan(ma[i]) and (ma[i]-KB[i])/KB[i]*100*sd >= GAPX: why = f'乖離 {GAPX:g}%'
        # 2026-10-22 測試(使用者:「回測進場那根 K 的量能 跟反向爆量出場」):REVSPIKE = 持倉中出現反向 K(做空遇紅 K、做多遇黑 K)且這根量 ≥ 前 REVN 根平均 × REVSPIKE 倍 → 下一根開盤出場
        #   REVPL=gain:只在帳面賺錢時才這樣出;預設 any
        REVX = float(os.environ.get('REVSPIKE','0'))
        if why is None and REVX > 0 and i >= 2:
            _n = int(os.environ.get('REVN','20')); _base = np.mean(v[max(1, i-_n):i])
            if (c[i] - o[i]) * sd < 0 and _base > 0 and v[i] >= REVX * _base and (os.environ.get('REVPL','any') == 'any' or (c[i]-ent)/ent*100*sd >= 0): why = f'反向爆量 {v[i]/_base:.1f} 倍'
        OBVX = os.environ.get('OBVX','0'); OBVXMIN = float(os.environ.get('OBVXMIN','0'))
        OBFPCT = float(os.environ.get('OBFPCT','0')); OBFMODE = os.environ.get('OBFMODE','all')   # 快速 OBV 均線:紅線反向超過藍線 OBFPCT%(÷當日 OBV 範圍)就出;mode all/gain0/gain1
        if why is None and OBFPCT > 0 and i >= OBF and not np.isnan(obmaF[i]):
            if os.environ.get('OBFBASE','range') == 'vol':
                _base = (np.mean(v[max(0,i-20):i]) if i > 0 else 1) or 1   # 最近 20 分鐘平均每分鐘量
                _d = (ob[i]-obmaF[i])*(-sd)/_base                            # 單位:倍(這幾分鐘淨流入 = 平常一分鐘量的幾倍)
            else:
                _rng = (np.nanmax(ob[:i+1]) - np.nanmin(ob[:i+1])) or 1
                _d = (ob[i]-obmaF[i])*(-sd)/_rng*100      # 做空:紅線高於藍線為正;做多:紅線低於藍線為正
            _pl = (c[i]-ent)/ent*100*sd
            if _d >= OBFPCT and os.environ.get('TRACEF'): print(f'TRACEF {tm} 紅線反向超過藍線{OBF} {_d:.1f}% 帳面 {_pl:+.2f}%')
            if _d >= OBFPCT and (OBFMODE=='all' or (OBFMODE=='gain0' and _pl>=0) or (OBFMODE=='gain1' and _pl>=1)): why = f'OBV{OBF}反向{_d:.0f}%'
        if why is None and OBVX != '0' and i >= 1:
            _m = obma2 if os.environ.get('OBVX2','0')=='1' else obma   # OBVX2=1 用第二條(較慢)OBV 均線判斷穿越
            _cross = (not np.isnan(_m[i-1])) and ((sd == -1 and ob[i] > _m[i] and ob[i-1] <= _m[i-1]) or (sd == 1 and ob[i] < _m[i] and ob[i-1] >= _m[i-1]))
            _pl = (c[i]-ent)/ent*100*sd
            if _cross and os.environ.get('TRACEX'): print(f'TRACEX {tm} OBV反向穿越 帳面 {_pl:+.2f}%')
            _pk = float(os.environ.get('OBVXPEAK','99'))
            if _cross and (OBVX == '1' or (OBVX == '2' and (_pl >= OBVXMIN or globals().get('_mfe', 0) >= _pk))): why = f'OBV反向穿越{"(獲利中)" if OBVX=="2" else ""}'
        OBVTURN = os.environ.get('OBVTURN','0'); OBVTURNMIN = float(os.environ.get('OBVTURNMIN','-99')); OBVTURNBARS = int(os.environ.get('OBVTURNBARS','1'))   # 2026-10-18 測試:紅線穿過藍線(OBV 均線)且藍線隨即轉向 → 平倉(判別式二候選)
        if why is None and OBVTURN != '0' and i >= OBVTURNBARS + 1 and not np.isnan(obma[i-OBVTURNBARS-1]):
            _cr = (sd == 1 and ob[i] < obma[i] and ob[i-1] >= obma[i-1]) or (sd == -1 and ob[i] > obma[i] and ob[i-1] <= obma[i-1])
            _turn = all((obma[i-k] < obma[i-k-1]) if sd == 1 else (obma[i-k] > obma[i-k-1]) for k in range(OBVTURNBARS))
            _pl = (c[i]-ent)/ent*100*sd
            if OBVTURN == '2':   # 穿過後 2 分鐘內藍線轉向也算
                _cr = any(((sd == 1 and ob[i-k] < obma[i-k] and ob[i-k-1] >= obma[i-k-1]) or (sd == -1 and ob[i-k] > obma[i-k] and ob[i-k-1] <= obma[i-k-1])) for k in range(0, min(3, i-1)))
            if _cr and _turn and os.environ.get('TRACET'): print(f'TRACET {tm} 紅穿藍且藍轉向 帳面 {_pl:+.2f}%')
            if _cr and _turn and _pl >= OBVTURNMIN: why = 'OBV穿越藍轉向'
        DXEXIT = os.environ.get('DXEXIT',''); DXEXITMIN = float(os.environ.get('DXEXITMIN','-99')); DXEXITBARS = int(os.environ.get('DXEXITBARS','3'))   # 2026-10-18 測試(使用者:「差距逐漸縮小就是反彈,o10 大於 o30 就是上漲」):narrow=OBV10/30 差距連縮 N 根就出;cross=OBV10 反向穿過 OBV30 就出;both
        if why is None and DXEXIT and i >= DXEXITBARS + 1 and not np.isnan(obma2[i-DXEXITBARS-1]):
            _gd = lambda k: (obma2[k]-obma[k])*(-sd)   # 持倉方向的順向差距(空:OBV30−OBV10)
            _pl = (c[i]-ent)/ent*100*sd
            _nar = _gd(i) > 0 and all(_gd(i-k) < _gd(i-k-1) for k in range(DXEXITBARS))
            _cr = _gd(i) <= 0 and _gd(i-1) > 0
            if _pl >= DXEXITMIN:
                if DXEXIT in ('narrow','both') and _nar: why = f'OBV10/30差距連縮{DXEXITBARS}根'
                elif DXEXIT in ('cross','both') and _cr: why = 'OBV10反向穿過OBV30'
        if why is None: globals()['_mfe'] = max(globals()['_mfe'], gain)
        if why:
            if why.endswith('強制平倉'): ex = o[i]
            elif why.startswith('OBV'): ex = o[i+1] if i+1 < len(c) else c[i]
            elif why.startswith('停利'): ex = round(ent*(1+TAKE/100*sd), 2)
            elif why.startswith('保本'): ex = ent
            else: ex = o[i+1] if i+1 < len(c) else c[i]
            if XHL and '停損' in why and _spx is not None and (ex - _spx)*sd > 0: ex = round(_spx, 2)   # 停損價還沒到下一根開盤就已經碰到 → 用停損價
            close_trade(sd, k0, ent, lots, tm, ex, why); log.append(f'{tm} {why} 出 {ex:.2f}'); cyc = None
            if why.startswith('停利') or why.startswith('OBV'): lastTP[sd] = i   # 2026-10-18 測試:停利後同方向冷卻
            _er = os.environ.get('EXITRESET','0')   # 2026-10-18 測試:出場後同方向的「第 1 個點」作廢,要重新數(tp=只有停利後;all=任何出場後)
            if _er == 'all' or (_er == 'tp' and why.startswith('停利')): last[sd] = None
            if why.endswith('強制平倉'): break
    if os.environ.get('DEBUGWIN') and os.environ['DEBUGWIN'].split('-')[0] <= tm <= os.environ['DEBUGWIN'].split('-')[1] and not np.isnan(ma[i-1]):
        _kUp = c[i] > ma[i] and ma[i] > ma[i-1]; _kDn = c[i] < ma[i] and ma[i] < ma[i-1]
        _oUp = ob[i] > ob[i-1] and obma[i] > obma[i-1] and ob[i] > obma[i]; _oDn = ob[i] < ob[i-1] and obma[i] < obma[i-1] and ob[i] < obma[i]
        print(f'  [{tm}] 開{o[i]:.2f} 高{h[i]:.2f} 低{l[i]:.2f} 收{c[i]:.2f} 均{ma[i]:.2f}({"升" if ma[i]>ma[i-1] else "降"}) OBV {ob[i]:.0f}/{obma[i]:.0f}({"升" if obma[i]>obma[i-1] else "降"}) 量{cv[i]:.0f} | K多{"✓" if _kUp else "✗"} K空{"✓" if _kDn else "✗"} OBV多{"✓" if _oUp else "✗"} OBV空{"✓" if _oDn else "✗"} 低碰均{"✓" if l[i] <= ma[i]+1e-4 else "✗"} 高碰均{"✓" if h[i] >= ma[i]-1e-4 else "✗"} 在高低間{"✓" if lo[i] < c[i] < hi[i] else "✗"}')
    if i+1 < int(os.environ.get('SKIPMIN','15')) or np.isnan(ma[i-1]) or np.isnan(obma[i-1]) or i+1 >= len(c): continue
    if 'delayOrd' not in globals(): globals()['delayOrd'] = None
    if delayOrd is not None and cyc is None and i >= delayOrd['at']:   # 2026-10-20 測試(使用者:友達 09:30 多單通知 09:33 才到、K 已在線下 → 「先跑看看利弊勝負」):訊號後 ENTRYDELAY 根才進,進場前再確認
        sd = delayOrd['side']; ok = True; why = ''
        if os.environ.get('RECHECK','0') == '1' and not ((c[i-1] - ma[i-1]) * sd > 0): ok = False; why = f'現價 {c[i-1]:.2f} 已在均價 {ma[i-1]:.2f} 另一側'
        RM = float(os.environ.get('RECHECKMAX','99'))
        if ok and abs(c[i-1] - delayOrd['sig']) / delayOrd['sig'] * 100 > RM: ok = False; why = f'離訊號價 {abs(c[i-1]-delayOrd["sig"])/delayOrd["sig"]*100:.2f}% 超過 {RM}%'
        if ok:
            ent = o[i]; lots = int(CAP // (ent*1000)); cyc = (sd, tm, ent, lots); last[-sd] = None; pending[sd] = False
            log.append(f'{tm} 延遲進場 {ent:.2f} ×{lots}張(訊號 {delayOrd["tm"]})')
        else: log.append(f'{tm} 延遲進場取消:{why}(訊號 {delayOrd["tm"]})')
        globals()['delayOrd'] = None
    if limitOrd is not None and cyc is None:
        if i > limitOrd['exp']: log.append(f'{tm} 掛單 {limitOrd["px"]:.2f} 逾時取消'); limitOrd = None
        elif (limitOrd['side']==1 and l[i] <= limitOrd['px']) or (limitOrd['side']==-1 and h[i] >= limitOrd['px']):
            sd = limitOrd['side']; ent = limitOrd['px']; lots = int(CAP // (ent*1000)); cyc = (sd, tm, ent, lots); last[-sd] = None; pending[sd] = False
            log.append(f'{tm} 掛單成交 → 進場 {ent:.2f} ×{lots}張(訊號 {limitOrd["tm"]})'); limitOrd = None
    kUp = (h[i] if (KHIGH or (FIRSTPIERCE and last[1] is None)) else c[i]) > ma[i] and ma[i] > ma[i-1] + 1e-9; kDn = (l[i] if (KHIGH or (FIRSTPIERCE and last[-1] is None)) else c[i]) < ma[i] and ma[i] < ma[i-1] - 1e-9
    oUp = ob[i] > ob[i-1] and obma[i] > obma[i-1] and ob[i] > obma[i]; oDn = ob[i] < ob[i-1] and obma[i] < obma[i-1] and ob[i] < obma[i]
    between = lo[i] < c[i] < hi[i]; pUp = l[i] <= ma[i] + 1e-4; pDn = h[i] >= ma[i] - 1e-4; doji = abs(c[i]-o[i]) < 1e-4
    MODE = os.environ.get('MODE','one')   # 2026-10-18 測試(使用者提議):cross = 以「紅線剛穿過藍線」為第 1 個點,之後 XWIN 分鐘內 K 線跟上(收在均價上 + 均價上升)就是確認點
    if MODE == 'cross':
        XWIN = int(os.environ.get('XWIN','10')); XSTR = float(os.environ.get('XSTR','0')); XPRESS = os.environ.get('XPRESS','0')=='1'
        XNEWHI = int(os.environ.get('XNEWHI','0'))        # 確認點前紅線要創幾次「穿越後新高(多)/ 新低(空)」,0 = 不看
        XOB2 = int(os.environ.get('XOB2','0')); XOB2DIR = os.environ.get('XOB2DIR','slope')   # 第二條慢 OBV 均線(例 20):slope=要同向、side=紅線要在它的順向側、both=兩個都要
        if 'cand' not in globals(): globals()['cand'] = {1: None, -1: None}
        if XOB2 and 'obmaS' not in globals(): globals()['obmaS'] = pd.Series(ob).rolling(XOB2).mean().values
        _rng = (np.nanmax(ob[:i+1]) - np.nanmin(ob[:i+1])) or 1
        if os.environ.get('XMODE','cross') == 'gap':   # gap 模式:不看「剛穿越那一根」,看紅藍距離第一次拉開到 XSTR%(前一根還不到)— 友達 10/16 10:30 這種「早就在下面、這根才真的拉開」也算
            _g1 = (ob[i]-obma[i])/_rng*100; _g0 = (ob[i-1]-obma[i-1])/((np.nanmax(ob[:i]) - np.nanmin(ob[:i])) or 1)*100
            xUp = _g1 >= XSTR and _g0 < XSTR; xDn = -_g1 >= XSTR and -_g0 < XSTR
        else:
            xUp = ob[i] > obma[i] and ob[i-1] <= obma[i-1] and (ob[i]-obma[i])/_rng*100 >= XSTR
            xDn = ob[i] < obma[i] and ob[i-1] >= obma[i-1] and (obma[i]-ob[i])/_rng*100 >= XSTR
        side = 0; xconf = False
        XVOL = float(os.environ.get('XVOL','0')); XVOLN = int(os.environ.get('XVOLN','0')); XVOLAT = os.environ.get('XVOLAT','cross')   # 穿越(或確認)那根的量 ≥ XVOL × 平均每分鐘量(XVOLN=0 看當天到此刻,否則看最近 N 根)
        def _volok(j):
            if XVOL <= 0: return True
            base = (np.mean(v[max(0, j-XVOLN):j]) if XVOLN else (cv[j-1]/j)) if j > 0 else 0
            return base > 0 and v[j] >= XVOL * base
        if XVOLAT == 'cross' and not _volok(i): xUp = xDn = False
        if xUp: cand[1] = {'exp': i + XWIN, 'i0': i}; cand[-1] = None; side = 1
        elif xDn: cand[-1] = {'exp': i + XWIN, 'i0': i}; cand[1] = None; side = -1
        else:
            if cand[1] is not None and (i > cand[1]['exp'] or ob[i] < obma[i]): cand[1] = None     # 窗口過期或紅線又跌回藍線下 → 候選作廢
            if cand[-1] is not None and (i > cand[-1]['exp'] or ob[i] > obma[i]): cand[-1] = None
            def _newext(sd):
                i0 = cand[sd]['i0']; n = 0; ext = ob[i0]
                for k in range(i0+1, i+1):
                    if (sd == 1 and ob[k] > ext) or (sd == -1 and ob[k] < ext): n += 1; ext = ob[k]
                return n, (ob[i] >= ext - 1e-9 if sd == 1 else ob[i] <= ext + 1e-9)
            def _ob2ok(sd):
                if not XOB2 or np.isnan(obmaS[i]) or np.isnan(obmaS[i-1]): return not XOB2
                okS = (obmaS[i]-obmaS[i-1])*sd > 0; okD = (ob[i]-obmaS[i])*sd > 0
                XOB3 = int(os.environ.get('XOB3','0'))   # 2026-10-19 測試:確認點再要求第三條 OBV 均線(例 30)也同向(1 根)
                if XOB3 and not np.isnan(obma2[i-1]) and (obma2[i]-obma2[i-1])*sd <= 0: return False
                return okS if XOB2DIR == 'slope' else okD if XOB2DIR == 'side' else (okS and okD)
            for sd in (1, -1):
                if cand[sd] is None: continue
                if os.environ.get('XKMA','1') == '0':   # XKMA=0:K 線只要「收盤在均價之上(多)/ 之下(空)」,不要求均價線方向
                    kOk = (c[i] > ma[i] and (not XPRESS or pUp)) if sd == 1 else (c[i] < ma[i] and (not XPRESS or pDn))
                else:
                    kOk = (c[i] > ma[i] and ma[i] > ma[i-1] + 1e-9 and (not XPRESS or pUp)) if sd == 1 else (c[i] < ma[i] and ma[i] < ma[i-1] - 1e-9 and (not XPRESS or pDn))
                if not (kOk and between and not doji): continue
                n, atExt = _newext(sd)
                if XNEWHI and not (n >= XNEWHI and atExt): continue   # 紅線要在穿越後創過 XNEWHI 次新高/新低,而且現在就在極值上
                if not _ob2ok(sd): continue
                if XVOLAT == 'confirm' and not _volok(i): continue
                side = sd; xconf = True; break
    elif MODE == 'gapdir':   # 2026-10-18 測試(使用者:「相減之差為起始 + 紅線在兩條藍線之上且 + 逐漸加大 = 做多起點,反之做空」)
        GW = int(os.environ.get('GWIN','10')); GK = int(os.environ.get('GK','2')); GSTART = os.environ.get('GSTART','cross')   # GSTART=cross:差剛翻正(負)算起始,GW 根內成立就進;any:不管起始,狀態成立就進
        if 'gcand' not in globals(): globals()['gcand'] = {1: None, -1: None}; globals()['gd'] = obma - obma2
        side = 0; xconf = False
        for sd in (1, -1):
            g = gd*sd
            if i-GK-1 < 0 or np.isnan(g[i-GK-1]): continue
            if g[i] > 0 and g[i-1] <= 0: gcand[sd] = i + GW; gcand[-sd] = None
            if gcand[sd] is not None and (i > gcand[sd] or g[i] <= 0): gcand[sd] = None
            ok = (GSTART == 'any' or gcand[sd] is not None) and g[i] > 0 and all(g[i-j] > g[i-j-1] for j in range(GK)) and (ob[i]-obma[i])*sd > 0 and (ob[i]-obma2[i])*sd > 0 and between and not doji
            if ok: side = sd; xconf = True; break
    elif MODE == 'seq':   # 2026-10-19 判別式三(使用者:「要先看價格上去,紅線藍線紫線有沒有照上升的規則走」→「先放進判別三」):價創 SEQHI 根新高 + 紅線在 OBV10 上、OBV10 升、OBV30 升,且三者依 紅→藍→紫 的順序在 SEQWIN 根內先後轉正
        SEQHI = int(os.environ.get('SEQHI','10')); SEQWIN = int(os.environ.get('SEQWIN','20')); SEQSIDE = os.environ.get('SEQSIDE','long')
        side = 0; xconf = False
        for sd in ((1, -1) if SEQSIDE == 'both' else ((1,) if SEQSIDE == 'long' else (-1,))):
            if i < max(SEQHI, SEQWIN) + 2 or np.isnan(obma2[i-SEQWIN-1]): continue
            red = lambda q: (ob[q]-obma[q])*sd > 0; blue = lambda q: (obma[q]-obma[q-1])*sd > 0; purp = lambda q: (obma2[q]-obma2[q-1])*sd > 0
            def since(cond):
                for j in range(0, SEQWIN):
                    if cond(i-j) and not cond(i-j-1): return j
                return 99
            newExt = c[i] >= max(c[i-SEQHI:i]) if sd == 1 else c[i] <= min(c[i-SEQHI:i])
            if not (newExt and red(i) and blue(i) and purp(i) and between and not doji): continue
            rs_, bs_, ps_ = since(red), since(blue), since(purp)
            if rs_ < 99 and rs_ > bs_ > ps_: side = sd; xconf = True; break
    elif MODE == 'brk':   # 2026-10-20 判別式三「燿華型」起漲點(使用者:「燿華型寫入判三」):前 20 根盤整(價幅 ≤ BRKFLAT% 或三線黏合 < BRKTIGHT% OBV 範圍)+ 藍線(OBV 均 10)最近 BRKXWIN+1 根內上穿紫線(OBV 均 30)
        #   + 紅線在藍上、藍在紫上 + 最近 BRKVWIN 根內有一根量 ≥ 前 20 根平均 BRKVOLX 倍 + 收盤 ≥ 前 20 根最高 + (BRKBELOW=1)價還在今開之下。只做多。均線用「當天到目前為止」的平均(不足 10/30 根時用全部)
        BRKFLAT = float(os.environ.get('BRKFLAT','1.0')); BRKTIGHT = float(os.environ.get('BRKTIGHT','3')); BRKXWIN = int(os.environ.get('BRKXWIN','2')); BRKVWIN = int(os.environ.get('BRKVWIN','3')); BRKVOLX = float(os.environ.get('BRKVOLX','2')); BRKBELOW = os.environ.get('BRKBELOW','1') == '1'
        side = 0; xconf = False
        if i >= 20:
            if 'brkMa10' not in globals():
                _cs = np.cumsum(np.insert(ob, 0, 0.0))
                globals()['brkMa10'] = np.array([(_cs[q+1]-_cs[max(0,q-9)])/min(10,q+1) for q in range(len(ob))]); globals()['brkMa30'] = np.array([(_cs[q+1]-_cs[max(0,q-29)])/min(30,q+1) for q in range(len(ob))])
            m10 = globals()['brkMa10']; m30 = globals()['brkMa30']
            _rng = max(np.max(ob[:i+1]) - np.min(ob[:i+1]), 1); _w = slice(i-20, i)
            _flat = (np.max(h[_w]) - np.min(l[_w])) / c[i] * 100 <= BRKFLAT
            _tight = (np.mean(np.abs(ob[i-10:i] - m10[i-10:i])) + np.mean(np.abs(m10[i-10:i] - m30[i-10:i]))) / _rng * 100 < BRKTIGHT
            BRKSIDE = os.environ.get('BRKSIDE','long'); BRKREV = os.environ.get('BRKREV','0') == '1'   # 2026-10-20 使用者:「反之可以做空」「也可以試看看相反的」:short = 鏡像做空;both = 兩邊;BRKREV=1 = 型態出現時做反方向
            _sd = -1 if BRKSIDE == 'short' else 1
            _cross = any((m10[k]-m30[k])*_sd > 0 and (m10[k-1]-m30[k-1])*_sd <= 0 for k in range(max(1, i-BRKXWIN), i+1))
            _redUp = (ob[i]-m10[i])*_sd > 0 and (m10[i]-m30[i])*_sd > 0
            _vb = v[i-BRKVWIN+1-20:i-BRKVWIN+1] if os.environ.get('BRKVBASE','pre') == 'pre' else v[_w]   # 量的基準:pre = 大量那幾根之前的 20 根(預設;大量那根不算進自己的基準),cur = 本根之前 20 根
            _bigv = (i-BRKVWIN+1-20 >= 0) and np.max(v[i-BRKVWIN+1:i+1]) >= BRKVOLX * max(np.mean(_vb), 1e-9)
            _brk = (c[i] >= np.max(h[_w])) if _sd == 1 else (c[i] <= np.min(l[_w]))
            _bm = os.environ.get('BRKBELOW','1'); _ref = prev if _bm == '2' else o[0]; _below = (_bm == '0') or ((c[i] - _ref) * _sd < 0)   # 1 多:價在今開之下 / 空:今開之上;2 用昨收;0 不限
            _gapok = (o[0]/(prev if os.environ.get('BRKGAPREF','k') == 'pool' else globals().get('prevK', prev)) - 1)*100*_sd <= float(os.environ.get('BRKGAP','99'))   # 2026-10-20 測試(使用者:「只做開平或開低」):今開對昨收的缺口 ≤ BRKGAP% 才做(0 = 開高不做);昨收用 K 線前一天收盤(BRKGAPREF=pool 改用池檔昨收,只給 App 對照用,App 盤中用證交所昨收)
            if (_flat or _tight) and _cross and _redUp and _bigv and _brk and _below and _gapok: side = (-_sd if BRKREV else _sd); xconf = True
    elif MODE == 'press':   # 2026-10-20 判別式四「壓線空點」(使用者看光鼎 09:49、友達 10:37 的元大圖:「壓在線上 指標全部向下 就是最漂亮的空點」→「我想把這個設為判四」),只做空:
        #   均價 10 連 2 根向下 + 這根或前一根最高碰到均價 10(差 ≤ PRESSTOUCH%)+ 收在均價 10 之下且收黑 + 紅 < 藍 < 紫、藍紫都向下
        #   + 量先價行:OBV 創 PRESSNL 根新低而價還在 PRESSNL 根低點之上 0.3%
        #   + 不追空:離今高 ≤ PRESSCHASE%、離今低 ≥ PRESSLO%、最近 10 根均量 ÷ 今天均量 < PRESSRVOL(量沒爆)+ 不是開平(|今開−昨收| > PRESSGAP%,0 = 不看)
        PT = float(os.environ.get('PRESSTOUCH','0.15')); PCH = float(os.environ.get('PRESSCHASE','2')); PRV = float(os.environ.get('PRESSRVOL','1.2')); PLO = float(os.environ.get('PRESSLO','0.5')); PGAP = float(os.environ.get('PRESSGAP','1')); PNL = int(os.environ.get('PRESSNL','30')); PNLM = float(os.environ.get('PRESSNLM','0.3')); PRED = os.environ.get('PRESSRED','0') == '1'   # 2026-10-20 修正搜尋:PRESSNLM 價要在 PNL 根低點之上幾 %;PRESSRED=1 只做價還在今開之上(紅盤)的
        side = 0; xconf = False
        if i >= PNL and i >= 2 and not any(np.isnan(x) for x in (ma[i], ma[i-1], ma[i-2], obma[i], obma[i-1], obma2[i], obma2[i-1])):
            _maDown = ma[i] < ma[i-1] < ma[i-2]
            _touch = max(h[i], h[i-1]) >= ma[i] * (1 - PT/100)
            _under = c[i] < ma[i] and c[i] < o[i]
            _obvDown = ob[i] < obma[i] < obma2[i] and obma[i] < obma[i-1] and obma2[i] < obma2[i-1]
            _newlow = ob[i] <= np.min(ob[i-PNL:i]) and c[i] > np.min(l[i-PNL:i]) * (1 + PNLM/100)
            _redok = (not PRED) or c[i] >= o[0]
            _offhi = (np.max(h[:i+1]) - c[i]) / c[i] * 100; _offlo = (c[i] - np.min(l[:i+1])) / c[i] * 100
            _rv = np.mean(v[max(0,i-10):i+1]) / max(np.mean(v[:i+1]), 1e-9)
            _gapok = PGAP <= 0 or abs(o[0] - prev) / prev * 100 > PGAP
            PSIDE = os.environ.get('PRESSSIDE', 'short')   # 2026-10-21 測試(使用者:「判三 做多不行嗎」):long = 鏡像「撐線多點」、both = 兩邊都做
            if PSIDE in ('short', 'both') and _maDown and _touch and _under and _obvDown and _newlow and _offhi <= PCH and _offlo >= PLO and _rv < PRV and _gapok and _redok: side = -1; xconf = True
            if PSIDE in ('long', 'both') and side == 0:
                _maUp = ma[i] > ma[i-1] > ma[i-2]
                _touchL = min(l[i], l[i-1]) <= ma[i] * (1 + PT/100)
                _over = c[i] > ma[i] and c[i] > o[i]
                _obvUp = ob[i] > obma[i] > obma2[i] and obma[i] > obma[i-1] and obma2[i] > obma2[i-1]
                _newhi = ob[i] >= np.max(ob[i-PNL:i]) and c[i] < np.max(h[i-PNL:i]) * (1 - PNLM/100)
                if _maUp and _touchL and _over and _obvUp and _newhi and _offlo <= PCH and _offhi >= PLO and _rv < PRV and _gapok: side = 1; xconf = True
    else:
        side = 1 if (kUp and oUp and between and pUp and not doji) else (-1 if (kDn and oDn and between and pDn and not doji) else 0)
    STRONG = os.environ.get('STRONG','0'); strong = False   # 2026-10-18 測試(使用者提議,友達 10/16 10:30):強訊號 = 紅線在 OBV10 與 OBV30 之下 + 兩條藍線都在降 + 價在均價下且均價降;剛成立那根直接當確認點
    if STRONG != '0' and i >= 7 and not np.isnan(obma2[i-2]) and tm < os.environ.get('SNOAFTER','13:00'):   # SNOAFTER:強訊號幾點以後不看
        SBARS = int(os.environ.get('SBARS','1')); SREQ = os.environ.get('SREQ','')   # SBARS=OBV30 要連續降幾根;SREQ 含 1030 → OBV10 也要在 OBV30 順向側;含 slope5 → OBV30 5 根斜率順向;含 ext → 紅線是穿越後(近 10 根)極值
        def _stf(k, sd):
            if any(np.isnan(x) for x in (obma2[k-SBARS], obma[k-1], ma[k-1])): return False
            ok = (ob[k]-obma[k])*sd > 0 and (ob[k]-obma2[k])*sd > 0 and (obma[k]-obma[k-1])*sd > 0 and (c[k]-ma[k])*sd > 0 and (ma[k]-ma[k-1])*sd > 0
            ok = ok and all((obma2[k-j]-obma2[k-j-1])*sd > 0 for j in range(SBARS))
            if '1030' in SREQ: ok = ok and (obma[k]-obma2[k])*sd > 0
            if 'slope5' in SREQ: ok = ok and k >= 5 and not np.isnan(obma2[k-5]) and (obma2[k]-obma2[k-5])*sd > 0
            if 'x30' in SREQ:   # 紅線最近 SXWIN 根內才剛從 OBV30 的另一側穿過來(只抓「剛穿越」那一次,不要一直重複觸發)
                _w = int(os.environ.get('SXWIN','10')); ok = ok and any((ob[k-j]-obma2[k-j])*sd <= 0 for j in range(1, _w+1) if k-j >= 0 and not np.isnan(obma2[k-j]))
            if 'x10' in SREQ:   # 紅線最近 SX10WIN 根內也才剛穿過 OBV10(兩條都是剛穿,不是早就在下面很久)
                _w = int(os.environ.get('SX10WIN','20')); ok = ok and any((ob[k-j]-obma[k-j])*sd <= 0 for j in range(1, _w+1) if k-j >= 0 and not np.isnan(obma[k-j]))
            if 'dx' in SREQ:    # OBV10 最近 SDXWIN 根內才剛穿過 OBV30(死亡/黃金交叉)
                _w = int(os.environ.get('SDXWIN','5')); ok = ok and any((obma[k-j]-obma2[k-j])*sd <= 0 for j in range(1, _w+1) if k-j >= 0 and not np.isnan(obma2[k-j]))
            if 'ext' in SREQ: ok = ok and ((ob[k] >= np.nanmax(ob[k-10:k+1])-1e-9) if sd == 1 else (ob[k] <= np.nanmin(ob[k-10:k+1])+1e-9))
            return ok
        if 'scand' not in globals(): globals()['scand'] = {1: None, -1: None}
        SWIN = int(os.environ.get('SWIN','0'))   # 強訊號剛成立後 SWIN 根內,第一根「不是十字線、收在當日高低之間」的 K 就當確認(0 = 只看剛成立那一根)
        for sd in ((-1, 1) if STRONG in ('1', 'both') else (-1,)):   # STRONG=short → 只做強空
            if _stf(i, sd) and not _stf(i-1, sd): scand[sd] = i + SWIN
            if scand[sd] is not None and i > scand[sd]: scand[sd] = None
            if scand[sd] is not None and _stf(i, sd) and between and not doji:
                strong = True; side = sd; scand[sd] = None; break
        if os.environ.get('TRACES') and strong: print(f'TRACES {tm} 強{"多" if side==1 else "空"} 價 {c[i]:.2f} ({(c[i]-prev)/prev*100:+.2f}%) 紅 {ob[i]:.0f} OBV10 {obma[i]:.0f} OBV30 {obma2[i]:.0f}')
    if FALSEGAP > 0:
        fside = 1 if (kUp and oUp and between and not doji and not pUp) else (-1 if (kDn and oDn and between and not doji and not pDn) else 0)
        if fside and falseFirst[fside] is None and last[fside] is None: falseFirst[fside] = (tm, c[i])
    if not side: continue
    if FALSEGAP > 0: falseFirst[-side] = None
    prv = last[side]
    conf = prv is not None and ((side==1 and ob[i]>prv[0] and obma[i]>prv[1] and c[i]>prv[2]) or (side==-1 and ob[i]<prv[0] and obma[i]<prv[1] and c[i]<prv[2])) and abs(c[i]-prv[2])/prv[2]*100 <= 2.0
    if MODE in ('cross', 'gapdir', 'seq', 'brk', 'press'): conf = xconf
    if os.environ.get('ENTER1','0')=='1' and MODE not in ('cross','gapdir','seq','brk','press'): conf = True   # 2026-10-19 測試(使用者:「驗證第一訊號進場跟第二訊號才進場」):每個訊號都當確認點,第 1 個訊號就進
    if strong: conf = True
    seq[side] = seq[side] + 1 if conf else 1
    if isGap and GAPMODE == 'need3' and conf and seq[side] < 3: conf = False   # 跳空股:第 2 個點不算確認,要第 3 個
    if isGap and GAPMODE == 'skip': conf = False                                # 跳空股:訊號全部失效
    if GAPMODE == 'dir' and conf:                                               # 2026-10-18 測試:跳空方向不追(跳低開不做空、跳高開不做多)
        _g = (o[0]-prev)/prev*100
        if (side==-1 and _g < -GAPTH) or (side==1 and _g > GAPTH): conf = False
    # 2026-10-24 00:50 使用者「只做跳空 ≥1%」:GAPMIN = 今開對昨收 |跳空| 小於這個 % 的股票當天不進場(App SIG.gapMinPct)
    if float(os.environ.get('GAPMIN','0')) > 0 and conf and abs((o[0]-prev)/prev*100) < float(os.environ.get('GAPMIN','0')): conf = False
    FPGAP = float(os.environ.get('FPGAP','0'))   # 第1點到確認點的「極值價差」上限:做空看第1點的高 → 確認點的低;做多看第1點的低 → 確認點的高
    if FPGAP > 0 and conf and prv is not None and len(prv) >= 5 and MODE != 'cross':
        _g = (prv[3]-l[i])/prv[3]*100 if side==-1 else (h[i]-prv[4])/prv[4]*100
        if _g > FPGAP: conf = False; log.append(f'{tm} {"多" if side==1 else "空"} 價 {c[i]:.2f} [第1點{"高" if side==-1 else "低"}{prv[3] if side==-1 else prv[4]:.2f}→確認點{"低" if side==-1 else "高"}{l[i] if side==-1 else h[i]:.2f} 差 {_g:.2f}%,太遠,不算確認]')
    last[side] = (ob[i], obma[i], c[i], h[i], l[i])
    if os.environ.get('RESETOPP','0')=='1' and not cyc: last[-side] = None   # 反方向出現一個點,就把對方的「第一個點」作廢,要重新數
    if first[side] is None: first[side] = c[i]; firstT[side] = i
    pct = (c[i]-prev)/prev*100
    GAPBASE = os.environ.get('GAPBASE','prev')   # prev=以昨收算漲跌幅(目前);open=以今開算;both=昨收或今開任一在範圍內就放行
    pctO = (c[i]-o[0])/o[0]*100
    TRENDOK = os.environ.get('TRENDOK','0'); TRENDCAP = float(os.environ.get('TRENDCAP','99'))
    if GAPBASE == 'open':
        gated = abs(pctO) > ENTRYMAX or (side==-1 and pctO > 3) or (side==1 and pctO < -3)
    elif GAPBASE == 'both':
        g1 = abs(pct) > ENTRYMAX or (side==-1 and pct > 3) or (side==1 and pct < -3)
        g2 = abs(pctO) > ENTRYMAX or (side==-1 and pctO > 3) or (side==1 and pctO < -3)
        gated = g1 and g2
    elif TRENDOK == '1':   # 超過 +ENTRYMAX% 只做多(不做空)、低於 -ENTRYMAX% 只做空(不做多);順勢方向最多到 TRENDCAP%
        gated = (side==-1 and pct > ENTRYMAX) or (side==1 and pct < -ENTRYMAX) or abs(pct) > TRENDCAP
    elif strong:   # 強訊號:不受 ±3% 順勢規則限制,只看 STRONGMAX(預設同 ENTRYMAX)
        gated = abs(pct) > float(os.environ.get('STRONGMAX', ENTRYMAX))
    else:
        gated = abs(pct) > ENTRYMAX or (side==-1 and pct > 3) or (side==1 and pct < -3)
    nm = '多' if side == 1 else '空'
    tag = f'{tm} {"強" if strong else ""}{nm}{"✓" if conf else ""}{"✕" if gated else ""} 價 {c[i]:.2f} ({pct:+.2f}%) OBV {ob[i]:.0f}/{obma[i]:.0f}'
    if conf and gated and not locked[side] and os.environ.get('NOLOCK','0')!='1': locked[side] = True; tag += ' [此方向今天鎖住]'   # NOLOCK=1:測試不鎖方向(2026-10-18)
    farDev = abs(c[i]-ma[i])/ma[i]*100 > MAXDEV
    chaseWait = False   # 2026-10-20 CHASEWAIT=1:追空的確認點不直接進,改掛均價線等反彈壓線再空
    far = farDev and not LIMITFAR
    _fe = (c[i]-lo[i])/lo[i]*100 if side==1 else (hi[i]-c[i])/hi[i]*100
    if _fe > MAXFROMEXT: far = True; tag += f' [離當日{"低" if side==1 else "高"}點 {_fe:.1f}%,只記錄]'
    if COOLDOWN > 0 and lastTP[side] is not None and i - lastTP[side] <= COOLDOWN: far = True; tag += f' [停利後 {i-lastTP[side]} 分鐘內,同方向不追]'
    MINROOM = float(os.environ.get('MINROOM','0'))   # 2026-10-18 測試:進場方向還要有多少空間(做空:價離當日低點 ≥ x%;做多:離當日高點 ≥ x%)
    _room = (c[i]-lo[i])/c[i]*100 if side==-1 else (hi[i]-c[i])/c[i]*100
    if MINROOM > 0 and _room < MINROOM: far = True; tag += f' [離當日{"低" if side==-1 else "高"}點只有 {_room:.1f}%,只記錄]'
    if VWAPF and not (strong and os.environ.get('SNOVWAP','0')=='1') and ((side==1 and c[i] < vwapArr[i]) or (side==-1 and c[i] > vwapArr[i])): far = True; tag += ' [VWAP 反向]'   # SNOVWAP=1:強訊號不看 VWAP(測試)
    if i >= 10 and (vwapArr[i]-vwapArr[i-10])/vwapArr[i]*100*side < SLOPEMIN: far = True; tag += f' [VWAP 斜率 {(vwapArr[i]-vwapArr[i-10])/vwapArr[i]*100*side:+.2f}% 不順]'
    volShort = bool(VOLMIN and cv[i] < VOLMIN)
    if RVOLMIN and rvBase and rvBase.get(tm, 0) > 0:
        _rv = cv[i]*1000 / rvBase[tm]
        if _rv < RVOLMIN:
            if os.environ.get('RVOLPEND','0') == '1': volShort = True   # RVOLPEND=1:量比不足和量不足一樣當「等量夠」(App 的做法),不是 far
            else: far = True
            tag += f' [量比 {_rv:.2f} 不足]'
    if volShort: tag += f' [量 {cv[i]:.0f} 張不足]'
    if PENDVOL:
        if conf and volShort and not far and not gated and not locked[side] and tm < os.environ.get('NOENTRYAFTER','13:00'): pending[side] = True; tag += ' [等量夠]'
        if not volShort and pending[side] and not far and not gated and (MODE != 'cross' or os.environ.get('XPENDCONF','0') != '1' or xconf): conf = True; tag += ' [量夠,補進]'   # XPENDCONF=1:穿越模式補進也要是確認點
    if volShort: far = True
    if farDev: tag += f' [離均價 {abs(c[i]-ma[i])/ma[i]*100:.2f}%,只記錄]'
    if FALSEGAP > 0 and conf and falseFirst[side] is not None:
        _fg = abs(c[i]-falseFirst[side][1])/falseFirst[side][1]*100
        if _fg > FALSEGAP: far = True; tag += f' [離虛假點{falseFirst[side][0]} {_fg:.2f}%,太遠]'
    OBMASLOPE = float(os.environ.get('OBMASLOPE','0'))   # OBV 均線最近 5 根的斜率(÷當日 OBV 波動範圍 ×100)下限;低於就只記錄
    if OBMASLOPE > 0 and conf and i >= 5:
        _rng = (np.nanmax(ob[:i+1]) - np.nanmin(ob[:i+1])) or 1; _s5 = (obma[i]-obma[i-5])*side/_rng*100
        REDMIN = float(os.environ.get('REDMIN','0'))   # 2026-10-18 測試:藍線太平時看紅線 5 根斜率(÷當日擺動),≥ REDMIN% 就放行
        _r5 = (ob[i]-ob[i-5])*side/_rng*100
        OB3 = int(os.environ.get('OB3MA','0'))   # 2026-10-18 測試:藍線太平時改看另一條 OBV 均線(OB3MA 根)的 5 根斜率,≥ OBMASLOPE 就放行
        _s3 = None
        if OB3 > 0 and i >= OB3 + 5:
            _m3a = np.nanmean(ob[i-OB3+1:i+1]); _m3b = np.nanmean(ob[i-5-OB3+1:i-5+1]); _s3 = (_m3a-_m3b)*side/_rng*100
        if _s5 < OBMASLOPE:
            if REDMIN > 0 and _r5 >= REDMIN: tag += f' [OBV均斜率 {_s5:.1f}% 不足,但紅線 {_r5:.1f}% 夠,放行]'
            elif _s3 is not None and _s3 >= OBMASLOPE: tag += f' [OBV均斜率 {_s5:.1f}% 不足,但 OBV均{OB3} 斜率 {_s3:.1f}% 夠,放行]'
            else: far = True; tag += f' [OBV均斜率 {_s5:.1f}% 不足]'
    DUAL = os.environ.get('DUAL','')   # 2026-10-18 測試(使用者提議):OBV10 之外再用 OBV30 雙重驗證。side=紅線要在 OBV30 的順向側;slope=OBV30 要順向(1 根);slope5=OBV30 5 根斜率 ≥ DUALSLOPE%;cross=OBV10 要在 OBV30 順向側;both=side+slope
    if conf and DUAL and not far:
        _mp = int(os.environ.get('DUALMIN','0'))   # DUALMIN>0:不足 30 根時用「目前有的根數」算平均(最少 DUALMIN 根)
        _dm = int(os.environ.get('DUALMA', OB2))   # DUALMA:雙重驗證用的 OBV 均線根數(預設 OB2)
        _m2 = pd.Series(ob).rolling(_dm, min_periods=_mp or None).mean().values
        if np.isnan(_m2[i]) or np.isnan(_m2[i-1]):
            if os.environ.get('DUALNAN','pass') == 'block': far = True; tag += f' [OBV{_dm} 還沒算出來,只記錄]'
        else:
            _ok = True
            if DUAL in ('side','both') and not ((ob[i]-_m2[i])*side > 0): _ok = False
            if DUAL in ('slope','both') and not ((_m2[i]-_m2[i-1])*side > 0): _ok = False
            if DUAL == 'cross' and not ((obma[i]-_m2[i])*side > 0): _ok = False
            if DUAL == 'cross2' and not ((_m2[i]-obma[i])*side > 0): _ok = False   # 驗證均線(快)要在 OBV10 的順向側
            if DUAL == 'slope5' and i >= 5 and not np.isnan(_m2[i-5]):
                _rng = (np.nanmax(ob[:i+1]) - np.nanmin(ob[:i+1])) or 1
                if (_m2[i]-_m2[i-5])*side/_rng*100 < float(os.environ.get('DUALSLOPE','2')): _ok = False
            if os.environ.get('TRACED'): print(f'TRACED {tm} {"多" if side==1 else "空"} 紅 {ob[i]:.0f} OBV{_dm} {_m2[i]:.0f}({"順" if (_m2[i]-_m2[i-1])*side>0 else "逆"}) 紅在順向側 {"是" if (ob[i]-_m2[i])*side>0 else "否"} OBV10在順向側 {"是" if (obma[i]-_m2[i])*side>0 else "否"}')
            if not _ok: far = True; tag += f' [OBV{_dm} 雙重驗證不過,只記錄]'
    GAPGROW = float(os.environ.get('GAPGROW','0'))   # 2026-10-18 測試(使用者提議):確認點的紅藍距離要 ≥ 第 1 點距離 × GAPGROW,距離在縮就不進
    if conf and prv is not None:
        _g0 = (prv[0]-prv[1])*side; _g1 = (ob[i]-obma[i])*side
        if os.environ.get('TRACEG'): print(f'TRACEG {tm} {"多" if side==1 else "空"} 第1點距離 {_g0:.0f} 確認點距離 {_g1:.0f} {"縮" if _g1 < _g0 else "拉遠"}')
        if GAPGROW > 0 and not far and _g1 < _g0*GAPGROW: far = True; tag += f' [紅藍距離 {_g0:.0f}→{_g1:.0f} 在縮,只記錄]'
    REDSLOPE = os.environ.get('REDSLOPE','')   # 2026-10-18 測試:確認點的 OBV 紅線 5 根斜率(÷當日擺動 ×100)要 ≥ 這個值(0 = 紅線不能在跌)
    if conf and REDSLOPE != '' and not far and i >= 5:
        _rng = (np.nanmax(ob[:i+1]) - np.nanmin(ob[:i+1])) or 1; _r5 = (ob[i]-ob[i-5])*side/_rng*100
        if _r5 < float(REDSLOPE): far = True; tag += f' [紅線 5 根斜率 {_r5:.1f}% 在退,只記錄]'
    MINGAP = float(os.environ.get('MINGAP','0'))   # 2026-10-18 測試(判別式二 13 筆賠錢單分析):確認點紅藍距離 ÷ 當日 OBV 範圍 ×100 要 ≥ MINGAP%,不夠就只記錄
    if conf and MINGAP > 0 and not far:
        _rng = (np.nanmax(ob[:i+1]) - np.nanmin(ob[:i+1])) or 1; _g = (ob[i]-obma[i])*side/_rng*100
        if _g < MINGAP: far = True; tag += f' [紅藍差距 {_g:.1f}% 不足,只記錄]'
    globals()['_chop_side'] = side
    if conf and not far and KAKB and (('L' in KAKB and side == 1 and not (ma[i] > KB[i])) or ('S' in KAKB and side == -1 and not (ma[i] < KB[i]))): far = True; tag += ' [Ka/Kb 不符,只記錄]'
    if conf and not far and ORG2 and (('L' in ORG2 and side == 1 and ORGPAT[i] != 1) or ('S' in ORG2 and side == -1 and ORGPAT[i] != -1)): far = True; tag += ' [指標三橘線不符,只記錄]'
    if conf and not far and os.environ.get('CHOP','0') == '1' and _is_chop(i): far = True; tag += ' [震盪盤,只記錄]'
    OB10DIR = os.environ.get('OB10DIR','0') == '1'   # 2026-10-20 測試(使用者看富喬 09:26:「OBV10 藍線也沒有往下」):確認點 OBV 均 10(藍)最近 1 根要和方向同向
    if conf and not far and OB10DIR and not np.isnan(obma[i-1]) and not ((obma[i] - obma[i-1]) * side > 0): far = True; tag += ' [藍線方向不符,只記錄]'
    KMADIR = os.environ.get('KMADIR','0') == '1'; KMA2 = int(os.environ.get('KMA2','0'))   # 2026-10-20 測試(使用者看友達元大圖:「判二 9:37 K10 是下跌、9:42 K10 在 K24 下面,這兩種情況都不適合做多」):確認點(含強訊號)均價 10 要同向;均價 10 要在均價 KMA2 的正確側
    if conf and not far and KMADIR and not ((ma[i] - ma[i-1]) * side > 0): far = True; tag += f' [均價 10 {"降" if side==1 else "升"},不符方向,只記錄]'
    SHRINK = float(os.environ.get('SHRINK','0'))   # 2026-10-21 測試(使用者:GIS「回測量縮下跌不做空」):做空時最近 10 根均量 ÷ 今天到此刻每分鐘均量 < SHRINK → 只記錄
    if conf and not far and SHRINK > 0 and side == -1 and i >= 10 and np.mean(v[i-9:i+1]) / max(cv[i] / (i + 1), 1e-9) < SHRINK: far = True; tag += f' [量縮下跌 {np.mean(v[i-9:i+1]) / max(cv[i] / (i + 1), 1e-9):.2f},只記錄]'
    if conf and not far and KMA2 > 0:
        if 'kma2' not in globals() or globals().get('kma2n') != KMA2: globals()['kma2'] = pd.Series(c).rolling(KMA2).mean().values; globals()['kma2n'] = KMA2
        _m2 = globals()['kma2'][i]
        if np.isnan(_m2) or (ma[i] - _m2) * side < 0: far = True; tag += f' [均價 10 在均價 {KMA2} {"之下" if side==1 else "之上"},只記錄]'
        KMA2X = int(os.environ.get('KMA2X','0'))   # 2026-10-20 測試(使用者看富喬 10:15:「做多需 K10 穿越 K24」):均價 10 要在最近 KMA2X 根內才剛穿過均價 KMA2(穿越後的那一段才算)
        if not far and KMA2X > 0:
            _k2 = globals()['kma2']
            if not any(i-j-1 >= 0 and not np.isnan(_k2[i-j-1]) and (ma[i-j]-_k2[i-j])*side > 0 and (ma[i-j-1]-_k2[i-j-1])*side <= 0 for j in range(KMA2X)): far = True; tag += f' [均價 10 沒有在 {KMA2X} 根內穿過均價 {KMA2},只記錄]'
    NEARHL = float(os.environ.get('NEARHL','0'))   # 2026-10-19 測試(台玻 11:44 做多在高點 69.7 之下 2%):做多時價離當日高點超過 NEARHL% 不進(做空看當日低點),第二波不追
    if conf and NEARHL > 0 and not far:
        _dh = np.nanmax(h[:i+1]); _dl = np.nanmin(l[:i+1])
        _dist = (_dh - c[i]) / _dh * 100 if side == 1 else (c[i] - _dl) / _dl * 100
        if _dist > NEARHL: far = True; tag += f' [離當日{"高" if side==1 else "低"}點 {_dist:.1f}% 太遠,只記錄]'
    CHASEHI = float(os.environ.get('CHASEHI','0')); CHASELO = float(os.environ.get('CHASELO','0'))   # 2026-10-20 測試(使用者:「不追空如何」):做空時價離當日高點超過 CHASEHI% = 已跌一段,不追空;做多鏡像 CHASELO 看當日低點
    if conf and not far and (CHASEHI > 0 or CHASELO > 0):
        _dh = np.nanmax(h[:i+1]); _dl = np.nanmin(l[:i+1])
        _cw = os.environ.get('CHASEWAIT','0') == '1'
        if side == -1 and CHASEHI > 0 and (_dh - c[i]) / _dh * 100 > CHASEHI:
            if _cw: chaseWait = True; tag += f' [離今高 {(_dh-c[i])/_dh*100:.1f}%,不追空,等反彈到均價]'
            else: far = True; tag += f' [離今高 {(_dh-c[i])/_dh*100:.1f}% 已跌一段,不追空,只記錄]'
        if side == 1 and CHASELO > 0 and (c[i] - _dl) / _dl * 100 > CHASELO:
            if _cw: chaseWait = True; tag += f' [離今低 {(c[i]-_dl)/_dl*100:.1f}%,不追多,等回檔到均價]'
            else: far = True; tag += f' [離今低 {(c[i]-_dl)/_dl*100:.1f}% 已漲一段,不追多,只記錄]'
    SHORTMAXPCT = float(os.environ.get('SHORTMAXPCT','99')); LONGMINPCT = float(os.environ.get('LONGMINPCT','-99'))   # 2026-10-19 測試(使用者「找破解」):做空只在當日漲跌 ≤ SHORTMAXPCT%、做多只在 ≥ LONGMINPCT%
    if conf and not far and ((side==-1 and pct > SHORTMAXPCT) or (side==1 and pct < LONGMINPCT)): far = True; tag += f' [當日 {pct:+.2f}% 不符{"做空" if side==-1 else "做多"}條件,只記錄]'
    # 2026-10-21 測試(使用者:「應該是偏多 少做空(讓做空變嚴格)反之亦然」):BREADTH=每分鐘名單上漲家數比例檔;比例 ≥ BRTH 時做空變嚴格、≤ 1−BRTH 時做多變嚴格
    #   BRMODE=room:逆勢方向要離今天低點(做空)/ 高點(做多)≥ BRROOM%;black:做空只做黑盤、做多只做紅盤;ban:逆勢方向直接不做(對照)
    if conf and not far and os.environ.get('BREADTH'):
        if 'BRD' not in globals(): globals()['BRD'] = json.load(open(os.environ['BREADTH'])).get(DAY, {})
        _br = BRD.get(tm); _th = float(os.environ.get('BRTH', '0.8')); _md = os.environ.get('BRMODE', 'room')
        _against = _br is not None and ((side == -1 and _br >= _th) or (side == 1 and _br <= 1 - _th))
        if _against:
            _rm = (c[i]-lo[i])/c[i]*100 if side == -1 else (hi[i]-c[i])/c[i]*100
            if (_md == 'ban') or (_md == 'room' and _rm < float(os.environ.get('BRROOM', '1'))) or (_md == 'black' and pct*side < 0) or (_md == 'both' and (_rm < float(os.environ.get('BRROOM', '1')) or pct*side < 0)):
                far = True; tag += f' [大盤{"偏多" if side == -1 else "偏空"} {_br:.0%},逆勢條件不足,只記錄]'

    OPENMOVE = float(os.environ.get('OPENMOVE','0'))   # 2026-10-19 測試(使用者:「訊號沒錯但我們忽視了什麼關鍵的訊號」):價要先相對今開朝方向走 OPENMOVE% 以上才進(只有 OBV、價還沒跟上 = 不進)
    # 2026-10-22 測試(同上):ENTVOLX = 進場(確認點)那根 K 的量要 ≥ 前 ENTVOLN 根平均 × ENTVOLX 倍,不夠只記錄(09:00 開盤撮合那根不算進平均)
    ENTVOLX = float(os.environ.get('ENTVOLX','0'))
    if conf and not far and ENTVOLX > 0 and i >= 2:
        _n = int(os.environ.get('ENTVOLN','20')); _base = np.mean(v[max(1, i-_n):i])
        if _base > 0 and v[i] < ENTVOLX * _base: far = True; tag += f' [進場量 {v[i]/_base:.1f} 倍不足,只記錄]'
    if conf and OPENMOVE > 0 and not far:
        _om = (c[i]-o[0])/o[0]*100*side
        if _om < OPENMOVE: far = True; tag += f' [離今開 {_om:+.2f}% 不足 {OPENMOVE}%,只記錄]'
    OBVDIR = int(os.environ.get('OBVDIR','0')); OBVDIRK = int(os.environ.get('OBVDIRK','1'))   # 2026-10-19 使用者:「OBV30 藍線向上不做空只做多,反之亦然」→ 確認點 OBV 均 OBVDIR 最近 OBVDIRK 根要和方向同向(持平也不進)
    if conf and OBVDIR > 0 and not far:
        if 'obmaDir' not in globals() or globals().get('obmaDirN') != OBVDIR: globals()['obmaDir'] = pd.Series(ob).rolling(OBVDIR).mean().values; globals()['obmaDirN'] = OBVDIR
        _md = globals()['obmaDir']
        _nan = i - OBVDIRK < 0 or np.isnan(_md[i]) or np.isnan(_md[i-OBVDIRK])
        _mode = os.environ.get('OBVDIRNAN','expand')   # 開盤 30 根內算不出 OBV30:expand(預設,2026-10-19 使用者「以當時時間為依據,09:15 就是 OBV15」)= 用今天到目前全部 K 的平均;skip = 不看;block = 擋
        if _nan and _mode == 'block': far = True; tag += f' [OBV{OBVDIR} 還算不出來,只記錄]'
        elif _nan and _mode == 'expand' and i - OBVDIRK >= 1:
            _cur = np.mean(ob[:i+1]); _pv = np.mean(ob[:i-OBVDIRK+1])
            if (_cur-_pv)*side <= 0: far = True; tag += f' [OBV{i+1}(開盤累計均)方向不同向,只記錄]'
        elif not _nan and (_md[i]-_md[i-OBVDIRK])*side <= 0: far = True; tag += f' [OBV{OBVDIR} 方向不同向,只記錄]'
    DXWID = int(os.environ.get('DXWID','0')); DXGAP = float(os.environ.get('DXGAP','0'))   # 2026-10-18 測試(使用者:「o30 大於 o10 且差逐漸擴大則是下跌」):確認點 OBV10 要在 OBV30 順向側、差距連續擴大 DXWID 根(DXGAP:差距 ÷ 當日 OBV 範圍 ≥ x%)
    if conf and (DXWID > 0 or DXGAP > 0) and not far and not np.isnan(obma2[i-DXWID-1] if i-DXWID-1 >= 0 else np.nan):
        _rng = (np.nanmax(ob[:i+1]) - np.nanmin(ob[:i+1])) or 1; _gd = lambda k: (obma2[k]-obma[k])*(-side)   # 空:OBV30−OBV10 > 0;多:鏡像
        _ok = _gd(i) > 0 and all(_gd(i-j) > _gd(i-j-1) for j in range(DXWID)) and _gd(i)/_rng*100 >= DXGAP
        if not _ok: far = True; tag += f' [OBV10/30 差距 {_gd(i)/_rng*100:+.1f}% 沒擴大,只記錄]'
    SPIKE = float(os.environ.get('SPIKE','0')); SPIKEW = int(os.environ.get('SPIKEW','3'))   # 2026-10-19 測試(欣銓 10/15 10:18 一根 451 張撐起所有條件):確認點前 SPIKEW 根內有單根量 ≥ SPIKE × 當天平均每分鐘量 → 只記錄
    if conf and SPIKE > 0 and not far and i >= 5:
        _avg = cv[i-1]/i
        if _avg > 0 and max(v[max(0,i-SPIKEW+1):i+1]) >= SPIKE*_avg: far = True; tag += f' [近 {SPIKEW} 根有爆量 {max(v[max(0,i-SPIKEW+1):i+1])/_avg:.1f} 倍,只記錄]'
    OBVPEAK = os.environ.get('OBVPEAK','0'); PRICEPEAK = os.environ.get('PRICEPEAK','0')   # 2026-10-18 測試(判別式二候選):確認點的 OBV 紅線 / 價格要是「當日」(day)或「最近 N 根」極值,否則只記錄
    if conf and OBVPEAK != '0' and not far:
        _n = i+1 if OBVPEAK == 'day' else int(OBVPEAK)
        _seg = ob[max(0, i-_n+1):i+1]
        _ok = (ob[i] >= np.nanmax(_seg)-1e-9) if side==1 else (ob[i] <= np.nanmin(_seg)+1e-9)
        if not _ok: far = True; tag += f' [OBV 紅線不是{"當日" if OBVPEAK=="day" else "近"+OBVPEAK+"根"}極值 {ob[i]:.0f} vs {np.nanmax(_seg) if side==1 else np.nanmin(_seg):.0f},只記錄]'
    if conf and PRICEPEAK != '0' and not far:
        _n = i+1 if PRICEPEAK == 'day' else int(PRICEPEAK)
        _ok = (c[i] >= np.max(h[max(0, i-_n+1):i])-1e-9) if side==1 else (c[i] <= np.min(l[max(0, i-_n+1):i])+1e-9)
        if not _ok: far = True; tag += f' [價格不是{"當日" if PRICEPEAK=="day" else "近"+PRICEPEAK+"根"}極值,只記錄]'
    can = conf and not gated and tm < os.environ.get('NOENTRYAFTER','13:00') and not locked[side] and not far
    if can and os.environ.get('NOWEAK','0')=='1' and first[1] is not None and first[-1] is not None and ((side==1 and first[-1] > first[1]) or (side==-1 and first[1] > first[-1])): can = False; far = True; tag += ' [逆勢單(反方向第1點價位較有利),不進]'   # 2026-10-19 測試:逆勢單乾脆不進
    if can and os.environ.get('REVWEAK','0')=='1' and first[1] is not None and first[-1] is not None and ((side==1 and first[-1] > first[1]) or (side==-1 and first[1] > first[-1])): side = -side; tag += f' [逆勢單 → 反向改做{"多" if side==1 else "空"}]'   # 2026-10-19 使用者:「逆勢單反向做呢」
    if limitOrd is not None and cyc is None and conf and side != limitOrd['side']: log.append(f'{tm} 反向確認點,取消掛單 {limitOrd["px"]:.2f}'); limitOrd = None
    if globals().get('delayOrd') is not None and cyc is None and conf and side != delayOrd['side']: log.append(f'{tm} 反向確認點,取消等待中的延遲進場'); globals()['delayOrd'] = None
    if cyc and side == cyc[0]: log.append(tag + ' (同向,略)'); continue
    if cyc and side != cyc[0]:
        weak = first[1] is not None and first[-1] is not None and ((cyc[0]==1 and first[-1] > first[1]) or (MIRROR and cyc[0]==-1 and first[1] > first[-1]))
        if weak and os.environ.get('WEAKORDER','0')=='1':   # 2026-10-18 測試:只有「反方向的第 1 個點比自己方向的第 1 個點早出現」才算逆勢(多單:第 1 個空點要先於第 1 個多點)
            if (cyc[0]==1 and not (firstT[-1] < firstT[1])) or (cyc[0]==-1 and not (firstT[1] < firstT[-1])): weak = False
        if REVOK and conf and not weak and (far or farDev or (REVOK==2 and gated)): log.append(tag + ' (反向確認點但被擋,不出)'); continue
        if not conf and (not weak or (MODE == 'cross' and os.environ.get('XREVCONF','0') == '1')): log.append(tag + ' (反向第1個,續抱)'); continue   # XREVCONF=1:穿越模式逆勢單也要等反向確認點才出
        sd, k0, ent, lots = cyc; ex = o[i+1]; why = '反向確認點' if conf else '逆勢單遇反向點'
        close_trade(sd, k0, ent, lots, tm, ex, why); log.append(tag + f' → {why} 平倉 {ex:.2f}'); cyc = None
        if not conf: continue
    if can:
        if os.environ.get('TRACE'):
            _rng = (np.nanmax(ob[:i+1]) - np.nanmin(ob[:i+1])) or 1; _gap = (ob[i]-obma[i])*side/_rng*100
            _sl5 = (ob[i]-ob[i-5])*side/_rng*100; _obext = (ob[i] >= np.nanmax(ob[:i+1])-1e-9) if side==1 else (ob[i] <= np.nanmin(ob[:i+1])+1e-9)
            _pext = (c[i] >= hi[i]-1e-9) if side==1 else (c[i] <= lo[i]+1e-9)
            _obma5 = (obma[i]-obma[i-5])*side/_rng*100
            _pos = (c[i]-lo[i])/((hi[i]-lo[i]) or 1)*100; _vwd = (c[i]-vwapArr[i])/vwapArr[i]*100*side; _opd = (c[i]-o[0])/o[0]*100*side; _ma5 = (ma[i]-ma[i-5])/ma[i]*100*side if i>=5 else 0
            _amp = (hi[i]-lo[i])/prev*100; _rv = (cv[i]*1000/rvBase[tm]) if (RVOLMIN and rvBase and rvBase.get(tm,0)>0) else 0
            print(f'TRACE {tm} {"多" if side==1 else "空"} {"強" if strong else "穿"} 差距 {_gap:.1f}% 紅5 {_sl5:.1f}% 藍5 {_obma5:.1f}% OBV極 {"是" if _obext else "否"} 價極 {"是" if _pext else "否"} 位置 {_pos:.0f}% 離VWAP {_vwd:+.2f}% 離今開 {_opd:+.2f}% 均5 {_ma5:+.2f}% 振幅 {_amp:.1f}% 量比 {_rv:.2f} 漲跌 {pct:+.2f}% weak {int(first[1] is not None and first[-1] is not None and ((side==1 and first[-1] > first[1]) or (side==-1 and first[1] > first[-1])))} bar {i} 開缺 {(o[0]-prev)/prev*100*side:+.2f}% 離均 {(c[i]-ma[i])/ma[i]*100*side:+.2f}% 均30斜 {((ma[i]-ma[i-30])/ma[i]*100*side if i>=30 else 0):+.2f}% OBV30斜 {((globals()["obmaDir"][i]-globals()["obmaDir"][i-5])*side/_rng*100 if i>=35 and "obmaDir" in globals() and not np.isnan(globals()["obmaDir"][i-5]) else 0):+.1f}% 距首同 {i-(firstT[side] if firstT[side] is not None else i)} 距首反 {(i-firstT[-side]) if firstT[-side] is not None else -1} K幅 {(h[i]-l[i])/c[i]*100:.2f}% 今高低幅 {(hi[i]-lo[i])/prev*100:.2f}%')
        if (LIMITFAR and farDev) or chaseWait:
            px = round(ma[i], 2) if chaseWait else ((l[i] if side==1 else h[i]) if LIMITFAR=='low' else round(ma[i], 2))
            limitOrd = {'side': side, 'px': px, 'exp': i + LIMITN, 'tm': tm}; last[-side] = None; pending[side] = False
            log.append(tag + f' [離均價 {abs(c[i]-ma[i])/ma[i]*100:.2f}%] → 掛單 {px:.2f} 等回檔({LIMITN} 分鐘內)'); continue
        ED = int(os.environ.get('ENTRYDELAY','0'))
        if ED > 0:
            globals()['delayOrd'] = {'side': side, 'at': i + ED, 'sig': c[i], 'tm': tm}; last[-side] = None; pending[side] = False; falseFirst[1] = falseFirst[-1] = None
            log.append(tag + f' → {ED} 分鐘後進場(模擬通知延遲)'); continue
        if os.environ.get('OBCTRACE'): globals()['_chop_side'] = side; print(f'OBC {tm} ' + _obc_str(i) + f' | chop45 {int(_is_chop(i))}')
        ent = o[i+1]; lots = int(CAP // (ent*1000)); cyc = (side, tm, ent, lots); last[-side] = None; pending[side] = False; falseFirst[1] = falseFirst[-1] = None; log.append(tag + f' → 進場 {ent:.2f} ×{lots}張')
        if MODE == 'cross': cand[side] = None
        if MODE == 'gapdir': gcand[side] = None
    elif not cyc: log.append(tag)
for s in log: print('  ' + s)
print(); tot = 0
for sd, k0, ent, lots, tm, ex, why, gross, fee, tax in trades:
    net = gross - fee - tax; tot += net
    print(f"{'做多' if sd==1 else '做空'} {k0} 進 {ent:.2f} ×{lots}張 → {tm} 出 {ex:.2f} [{why}]  價差 {gross:+,.0f}  手續費 {fee:,.0f}  稅 {tax:,.0f}  淨 {net:+,.0f}")
print(f'合計 {len(trades)} 筆  淨損益 {tot:+,.0f} 元')
