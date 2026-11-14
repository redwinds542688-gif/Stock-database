"""公式在電腦(2026-10-29 使用者:「如果 程式給別人用 但我要控制判123在手上」→「先做1.2.3」第 2 項)

判一 / 判二 / 判三(2026-10-31 加判四)的訊號改由電腦算:每分鐘用模擬器 sim_day.py(和手機 App 對照 0 差異的那份)對今天名單每一檔重跑一次,
新出現的進場 / 平倉就照手機通知的格式推上 Worker(?log=add);手機看到電腦的心跳(?engine)就不自己發通知,只收雲端的;
中繼程式(yuanta_bridge.py)照舊讀雲端通知下單。電腦當機 → 心跳停 → 手機 3 分鐘後自動回到自己算。

資料:前幾天的 1 分 K 從 GitHub 抓(kline/、kline_yuanta/ 各兩週),今天的從 Worker ?ybars=today(中繼每分鐘推上去的元大 K);
      名單 = GitHub 的 pool.json(資格複檢後的最新一份;抓不到才用中繼早上存的 pool_<日期>.json),照手機 buildPool / scan / tradable 篩過;
      判二的大盤寬度 ?breadth=today(每分鐘更新)。
參數:私人庫 Stock-logic 的 backtest/chk/rs_env.py,模擬器 backtest/sim_day.py;開工抓一次,之後每 30 分鐘重抓(ENGINE_RULES_MIN)。

2026-10-29 稽核修正(隔天 09:00 真錢上線前;原則:不確定就不發新進場,平倉照發):
  · 寬度每分鐘抓;判二一律帶 BRMISSING=block(今天沒有寬度 → 模擬器不進);寬度抓不到 / 過期 → 判二新進場先不發
  · 每輪拿模擬器「現在」的交易清單對帳:已推的進場在重算裡不見了 → 推平倉「訊號重算消失,平倉」;已推的部位一定等到平倉通知(不再因為太舊就默默丟掉)
  · 名單照手機篩(處置 / 注意 / 價格範圍 / 振幅 / 金融 / 一檔跳動 / 可當沖 + 可先賣);今天通知過但被篩掉的股票照算平倉、不發新進場;不能先賣的帶 CANSHORT=0
  · 心跳:錯誤太多 / 名單空 / K 過期 / K 連續晚到 → on=0(手機接手),原因寫在 msg 和 log
  · 規則版本(內容雜湊)寫進心跳和 log;抓不到 → 大聲 log + 心跳 msg「⚠規則舊」(stale=1);不再退回公開庫 tools/backtest/
  · 算之前等 hm-1 那根 K 上來(最多 15 秒);沒等到照算但不發新進場、不判定「訊號消失」
  · 進場以 (判別式, 代碼, 訊號 K 時間) 去重並存檔;開工 / 每輪讀雲端通知,手機或上一個 engine 發過的不重發、還開著的接回來管平倉
  · 單一執行個體鎖(engine_data/engine.lock);狀態檔先寫暫存再 os.replace
  · 名單每 5 分鐘看一次雲端(generated / elig_checked_at 變了就重讀),09:06 後一定重讀一次(資格複檢後)

用法(桌機 C:\\Yuanta\\bridge;要先 pip install pandas numpy):
  python engine.py                          # 盤中每分鐘跑到 13:31(中繼 bridge.env ENGINE=1 會自己啟動)
  python engine.py --replay 2026-10-29 [代碼,…] [--dry]   # 用那天的資料逐分鐘回放,印出會發的通知(--dry 不推);和雲端紀錄對照用
                                            # 盤中引擎在跑時要回放:另外設 ENGINE_DIR=別的資料夾(同一個資料夾有鎖,兩個不能同時跑)
"""
import os, sys, io, re, json, time, csv, glob, hashlib, urllib.request, urllib.error, datetime as dt, runpy, contextlib, threading
from pathlib import Path
from multiprocessing import Pool

HERE = Path(__file__).resolve().parent
try:   # 2026-10-29 時區一律 Asia/Taipei(Windows 沒裝 tzdata 時 ZoneInfo 會失敗 → 用固定 +8,台灣沒有日光節約,結果一樣)
    from zoneinfo import ZoneInfo
    TW = ZoneInfo("Asia/Taipei")
except Exception:
    TW = dt.timezone(dt.timedelta(hours=8), "Asia/Taipei")
now_tw = lambda: dt.datetime.now(TW)
RAW = "https://raw.githubusercontent.com/redwinds542688-gif/Stock-database/main/"
DATA = Path(os.environ.get("ENGINE_DIR", HERE / "engine_data"))
SIMD = DATA / "sim"
RS_ALL = ["一", "二", "三", "四", "五", "六", "六3", "七"]   # 2026-11-12 23:40 使用者「併入判六」:「六3」= 判六做空(判六3,模擬器 MODE=j63),通知 / 部位名稱一樣寫「判六」
# 2026-11-10 14:03(實際時間)使用者「同時被選 以勝率決定誰進場」:同一分鐘同一檔被兩個以上判別式選中 → 勝率高的先算、先發通知(中繼照先到的進場,後面的「這檔已經有部位」不進);
#   22 天(10/06~11/09)不限額度勝率:判五 91%、判七 88%、判一 86%、判三 83%、判四 81%、判二 70%;判六觀察版放最後。私人庫 rs_env.RS_PRIORITY 有設就用它
RS_PRIORITY = ["六", "五", "七", "一", "三", "四", "二"]   # 2026-11-11 00:20 判六上線(23 天 23 筆全勝,勝率最高排第一)   # 2026-11-10 23:05 判六改成午盤 Ka 穿 VWAP 追多(觀察版)   # 2026-11-10 02:30 加觀察版判六(超賣急彈做多,只發通知,中繼不下單;使用者「改 +3%」)   # 2026-11-09 加觀察版判七(只發通知,中繼不下單); 2026-11-05 加判五(使用者「先加 在公式 增加判五」→「完整上線」;模擬器 MODE=fail5)
_RS_ALL_OLD = ["一", "二", "三", "四"]   # 2026-10-31 使用者「現在把新轉折 直接改名為 判四 加在公式 裡面 燈號啟動 真正的下單也啟動 自動平倉機制也啟動」:判四 = 新轉折做空(模擬器 MODE=turn4)
JUDGE = {"一": "判一", "二": "判二", "三": "判三", "四": "判四", "五": "判五", "六": "判六", "六3": "判六", "七": "判七"}
J5MAX = 6   # 2026-11-05 判五一天最多 6 筆(17:15 使用者「改限6筆」)(全部股票合計、照出現先後;超額的不算);實際用私人庫 rs_env.J5MAX
T4MAX = 10   # 判四一天最多幾筆的預設;實際用私人庫 rs_env.py 的 T4MAX(全部股票合計、照出現先後;超額的不算)
RS_OF = {v: k for k, v in JUDGE.items() if k != "六3"}   # 「判六」對回「六」(六3 是判六的做空那一半)
J63MAX = 5   # 2026-11-12 23:40 使用者選「只限一天最多5筆」:判六做空(判六3)一天最多 5 檔;實際用私人庫 rs_env.J63MAX
TAKE = 2.5
RULE_FILES = (("backtest/sim_day.py", "sys.argv"), ("backtest/chk/rs_env.py", "def env_of"))   # 2026-10-29 只從私人庫 Stock-logic 抓(公開庫 tools/backtest/ 已刪,不再退回);第二欄 = 內容一定要有的字(擋 Worker 回錯誤頁)



def lot_tier_qty(px):
    """2026-11-11 16:43 使用者「股價10-80元最多一筆買3張且金額不超過16萬 80到250最多不超過2張且金額不超過25萬」→「改」(和中繼 trader.lot_tier_qty 同一套):
       ≤ 80 元 最多 3 張且 ≤ 16 萬;80~250 元 最多 2 張且 ≤ 25 萬;至少 1 張"""
    px = float(px or 0)
    if px <= 0: return 1
    return max(1, int(250000 // (px * 1000)))   # 2026-11-14 00:55 🔓→🔒 鐵律 ⑤ 改「不分級」(使用者「如果購買不分級呢」→「解鎖 改」):每筆 25 萬能買幾張就買幾張(原 ≤80 元最多 3 張 ≤16 萬、80~250 元最多 2 張);超過 250 元一張 > 25 萬 = 超額,另外管

def load_env():
    cfg = {}
    f = Path(os.environ.get("BRIDGE_ENV", HERE / "bridge.env"))
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1); cfg[k.strip()] = re.split(r"\s+#", v, 1)[0].strip()
    for k in ("ENGINE_PRE", "ENGINE_PRE_SEC", "ENGINE_PRE_MAXSEC", "ENGINE_START_SEC", "DRAG_ON", "DRAG_MIN", "DRAG_MFE", "DRAG_MKT", "QUOTE_PORT", "WORKER_URL", "PUSH_KEY", "ENGINE_WORKERS", "ENGINE_CAP", "LOG_DIR", "ENGINE_SIM_SRC", "ENGINE_MAX_CODES", "ENGINE_LO", "ENGINE_HI", "ENGINE_AMP",
              "ENGINE_ERR_MAX", "ENGINE_RULES_MIN", "ENGINE_BAR_WAIT"):
        if os.environ.get(k): cfg[k] = os.environ[k]
    return cfg


CFG = load_env()
LOGF = None
RULES = {"ver": "", "stale": False, "at": 0.0, "why": ""}   # 2026-10-29 模擬器 / 參數的版本(兩個檔內容的 sha256 前 8 碼)、是不是抓不到最新的


def log(*a):
    s = now_tw().strftime("%H:%M:%S") + " " + " ".join(str(x) for x in a)
    print(s, flush=True)
    try:
        global LOGF
        d = Path(CFG.get("LOG_DIR", HERE / "log")); d.mkdir(parents=True, exist_ok=True)
        with open(d / f"engine_{now_tw():%Y-%m-%d}.log", "a", encoding="utf-8") as f: f.write(s + "\n")
    except Exception: pass


def hm_add(hm, k):
    m = int(hm[:2]) * 60 + int(hm[3:5]) + k; return f"{m // 60:02d}:{m % 60:02d}"


def write_atomic(p, text):
    """2026-10-29 先寫暫存檔再 os.replace(寫到一半當機不會留下半個檔);Windows 目標檔剛好被讀時 replace 會失敗 → 重試幾次"""
    p = Path(p); p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + f".tmp{os.getpid()}")
    with open(tmp, "w", encoding="utf-8", newline="") as f: f.write(text)
    for k in range(5):
        try: os.replace(tmp, p); return
        except PermissionError:
            if k == 4: raise
            time.sleep(0.2)


def http(url, data=None, key=False, timeout=20):
    h = {"Content-Type": "application/json", "User-Agent": "mysh-engine/1"}   # Cloudflare 會擋 Python-urllib 這個 UA(2026-10-29 測試 403)
    if key: h["X-Push-Key"] = CFG.get("PUSH_KEY", "")
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None, method="POST" if data is not None else "GET", headers=h)
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8"))


def worker_url(q):
    return CFG["WORKER_URL"].rstrip("/") + "/?" + q


CLOUD_SET = {}   # 2026-11-07 主導機的整組設定(cap=get 的 set);目前用到 lots / bars / days


def cloud_cap(cur):
    """2026-11-02 多空上限主控:5861 主導機設定的每筆上限(元);讀不到 / 沒設過 → 原值 cur"""
    try:
        j = http(worker_url(f"cap=get&_={int(time.time())}"), key=True, timeout=8)
        if isinstance(j.get("set"), dict): CLOUD_SET.clear(); CLOUD_SET.update(j["set"])
        v = int(j.get("cap") or 0)
        return min(v, 250000) if 50000 <= v <= 500000 else cur   # 2026-11-11 22:46 使用者「最多上限改25萬」:最多 25 萬(和中繼一樣,超額標記才對得上)
    except Exception:
        return cur


def iso_week(d):
    y, w, _ = dt.date.fromisoformat(d).isocalendar(); return f"{y}-W{w:02d}"


def weeks_back(d, n=3):
    out = []; x = dt.date.fromisoformat(d)
    for i in range(n):
        y, w, _ = (x - dt.timedelta(days=7 * i)).isocalendar(); out.append(f"{y}-W{w:02d}")
    return out


def fetch_raw(path, dest, retry=2, repo=""):
    """GitHub 的檔案:有 PUSH_KEY 就經 Worker 代讀(?gh=raw;repo 改私人也讀得到),沒有就直接抓 raw;repo="logic" = 私人庫 Stock-logic(模擬器在那裡)"""
    for k in range(retry + 1):
        try:
            if CFG.get("PUSH_KEY"):
                req = urllib.request.Request(worker_url("gh=raw&path=" + path + ("&repo=" + repo if repo else "") + f"&_={int(time.time())}"), headers={"X-Push-Key": CFG["PUSH_KEY"], "User-Agent": "mysh-engine/1"})
            else:
                req = urllib.request.Request(RAW.replace("Stock-database", "Stock-logic") + path + f"?t={int(time.time())}" if repo else RAW + path + f"?t={int(time.time())}", headers={"User-Agent": "mysh-engine/1"})
            b = urllib.request.urlopen(req, timeout=30).read()
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(dest.name + f".dl{os.getpid()}"); tmp.write_bytes(b); os.replace(tmp, dest); return True   # 2026-10-29 先寫暫存再換(模擬器不會讀到半個檔)
        except urllib.error.HTTPError as e:
            if e.code == 404: return False
            time.sleep(1)
        except Exception:
            time.sleep(1)
    return False


def rules_hash():
    h = hashlib.sha256()
    for f, _ in RULE_FILES:
        p = SIMD / Path(f).name
        h.update(p.read_bytes() if p.exists() else b"")
    return h.hexdigest()[:8]


def fetch_rules():
    """2026-10-29 抓模擬器 + 參數(私人庫 Stock-logic):兩個都抓到、內容對才換上;抓不到 → 大聲 log、RULES.stale = True、沿用本機上一份;本機也沒有 → 不開工。
       回傳 True = 版本變了(呼叫的人要重新載入參數)"""
    SIMD.mkdir(parents=True, exist_ok=True); old = RULES["ver"]
    if CFG.get("ENGINE_SIM_SRC"):   # 測試用:直接拿本機 Stock-logic 的 clone(2026-10-29 不再退回公開庫舊位置)
        import shutil; src = Path(CFG["ENGINE_SIM_SRC"])
        for f, _ in RULE_FILES:
            s = src / f if (src / f).exists() else src / Path(f).relative_to("backtest")
            shutil.copyfile(s, SIMD / Path(f).name)
        RULES.update(stale=False, why="", at=time.time(), ver=rules_hash())
    else:
        ok = True; why = ""
        for f, must in RULE_FILES:
            nf = SIMD / (Path(f).name + ".new")
            if not fetch_raw(f, nf, repo="logic"):
                ok = False; why = f"抓不到 {f}"; break
            try:
                txt = nf.read_text(encoding="utf-8")
                if must not in txt: raise ValueError(f"內容不像 {Path(f).name}(沒有「{must}」)")
                compile(txt, f, "exec")
            except Exception as e:
                ok = False; why = f"{f} 內容不對:{e}"; break
        if ok:
            for f, _ in RULE_FILES: os.replace(SIMD / (Path(f).name + ".new"), SIMD / Path(f).name)
            RULES.update(stale=False, why="", at=time.time(), ver=rules_hash())
        else:
            for f, _ in RULE_FILES:
                try: (SIMD / (Path(f).name + ".new")).unlink()
                except Exception: pass
            if not all((SIMD / Path(f).name).exists() for f, _ in RULE_FILES):
                raise SystemExit(f"!!!! {why},本機也沒有模擬器 → 不開工(私人庫 Stock-logic;bridge.env 要有 PUSH_KEY,Worker 的 GH_TOKEN 要能讀 Stock-logic)")
            RULES.update(stale=True, why=why, at=time.time(), ver=rules_hash())
            log(f"!!!!!!!! 規則抓不到最新的:{why} → 沿用本機舊的 {RULES['ver']}(心跳標 ⚠規則舊,手機會看到);30 分鐘後再試")
    if RULES["ver"] != old:
        log(f"規則版本 {old or '(開工)'} → {RULES['ver']}{'(⚠ 舊的)' if RULES['stale'] else ''}")
    return bool(old) and RULES["ver"] != old


def prep_k(day, codes):
    """每檔前幾週的 K(kline + kline_yuanta);2026-10-29 抓不到的要 log(原本靜靜跳過,模擬器少了跨日資料也不知道)"""
    for k in [k for k in _WT_OLD if any(f"{os.sep}{c}{os.sep}" in k[0] or f"/{c}/" in k[0] for c in codes)]: _WT_OLD.pop(k, None)   # 2026-11-07 重抓歷史 → 記憶體裡前幾天的列作廢
    wk = weeks_back(day, 3); n = 0; miss = []; nohist = []
    for c in codes:
        have_prev = False
        for src in ("kline", "kline_yuanta"):
            for w in wk:
                p = DATA / src / c / f"{w}.csv"
                if p.exists() and w != iso_week(day):   # 以前的週不會再變
                    have_prev = True; continue
                if fetch_raw(f"{src}/{c}/{w}.csv", p): n += 1
                elif not p.exists(): miss.append(f"{src}/{c}/{w}")
                if p.exists() and w != iso_week(day): have_prev = True
        if not have_prev: nohist.append(c)
    if miss: log(f"前 3 週 K 抓不到 {len(miss)} 個(本週 / 新股可能本來就沒有):{', '.join(miss[:12])}{' …' if len(miss) > 12 else ''}")
    if nohist: log(f"!! 這些股票前幾週的 K 一個都沒有(Ka / OBV 跨日會不準):{', '.join(nohist)}")
    return n


def prep_day(day, codes):
    """開工:抓 sim_day.py / rs_env.py、pool.json、每檔前幾週的 K(kline + kline_yuanta)"""
    fetch_rules()
    if not fetch_raw("pool.json", DATA / "pool.json") and not (DATA / "pool.json").exists():   # 模擬器一定要讀 REPO_DIR/pool.json(抓不到就拿今天的名單當)
        import shutil; shutil.copyfile(DATA / "pool_today.json", DATA / "pool.json"); log("!! pool.json 抓不到,模擬器改用今天名單那份")
    n = prep_k(day, codes)
    log(f"開工:{len(codes)} 檔,抓了 {n} 個 K 檔(前 3 週),模擬器 {SIMD},規則 {RULES['ver']}{' ⚠舊' if RULES['stale'] else ''}")


def today_bars(day, since=""):
    """Worker ?ybars=<day> → {code: [[hm, o, h, l, c, v], …]}(量 = 股)"""
    j = http(worker_url(f"ybars={day}" + (f"&since={since}" if since else "")), timeout=40)
    return j.get("bars", {}), j.get("last", "")


_TB = {"d": "", "rows": {}, "last": "", "full_at": 0.0}
def today_bars_fast(day):
    """2026-11-07 13:25 使用者「看看還有什麼會拖慢速度的」:原本每分鐘(等 K 時每秒)整天的 K 全部重抓一次(下午 ~90 檔 × 260 根,上百 KB);
       改成只抓最近 3 分鐘(since,晚到成交整分鐘重送也抓得到)併進手上那份,每 10 分鐘(或換日、出錯)整天重抓一次對齊。回傳格式和 today_bars 一樣"""
    full = _TB["d"] != day or not _TB["last"] or time.time() - _TB["full_at"] > 600 or CFG.get("ENGINE_BARS_INC", "1") != "1"
    try:
        if full:
            bars, last = today_bars(day); _TB.update(d=day, rows={c: {r[0]: r for r in rows} for c, rows in bars.items()}, last=last or latest_bar(bars), full_at=time.time())
        else:
            bars, last = today_bars(day, since=hm_add(_TB["last"], -3))
            for c, rows in bars.items():
                dc = _TB["rows"].setdefault(c, {})
                for r in rows: dc[r[0]] = r
            lb = last or latest_bar(bars)
            if lb and lb > _TB["last"]: _TB["last"] = lb
    except Exception:
        _TB["full_at"] = 0.0; raise
    return {c: [d[m] for m in sorted(d)] for c, d in _TB["rows"].items()}, _TB["last"]


def latest_bar(bars, last=""):
    xs = [r[0] for rows in bars.values() for r in rows]
    return max(xs + ([last] if last else [])) if (xs or last) else ""


_WT_OLD = {}
def write_today(day, bars, upto=None, forming=None):
    """把今天的 K 併進 kline_yuanta/<code>/<本週>.csv(前幾天的列保留、今天的整段換掉);upto = 只到這分鐘(不含);
       forming = {code: [hm, o, h, l, c, v]} 正在形成的這一分鐘(即時價):模擬器要看到下一根才會判定前一根的訊號,手機用的就是正在形成的那根(2026-10-29 回放發現)"""
    wk = iso_week(day)
    for c, rows in bars.items():
        if forming and c in forming: rows = [r for r in rows if r[0] != forming[c][0]] + [forming[c]]
        p = DATA / "kline_yuanta" / c / f"{wk}.csv"
        # 2026-11-07 使用者「再檢查」:前幾天的列盤中不會變 → 一檔只讀一次存在記憶體(原本每分鐘 90 個檔都重讀一遍),今天的照舊整段換
        ck = (str(p), day); prev = _WT_OLD.get(ck)
        if prev is None:
            old = []
            if p.exists():
                with open(p, encoding="utf-8") as f:
                    old = [r for r in csv.DictReader(f) if r["date"] != day]
            prev = "".join(f"{r['date']},{r['time']},{r['open']},{r['high']},{r['low']},{r['close']},{r['volume']}\n" for r in old)
            if prev: _WT_OLD[ck] = prev   # 檔案還沒有前幾天的(還沒抓歷史)不記,下次重讀
        out = ["date,time,open,high,low,close,volume\n", prev]
        for hm, o, h, l, cl, v in sorted(rows):
            if upto and hm > upto: continue   # upto 那分鐘 = 正在形成的(forming),之後的不要
            if upto and hm == upto and not (forming and c in forming): continue
            out.append(f"{day},{hm},{o},{h},{l},{cl},{int(v)}\n")
        write_atomic(p, "".join(out))


def live_forming(codes, mkts, hm, bars):
    """Worker ?codes=(走元大通道或證交所)→ 每檔正在形成的這一分鐘:開高低收都用最新價、量 = 今天累計量 − 已完成分鐘的量(股)
       2026-11-07 使用者「看看還有什麼會拖慢速度的 再檢查」:每 40 檔一批原本一批一批輪流問(90 檔 = 3 趟),改成同時問"""
    out = {}
    def one(cs):
        try:
            ms = [mkts.get(c, "tse") for c in cs]
            return http(worker_url("codes=" + ",".join(cs) + "&mkt=" + ",".join(ms)), timeout=15).get("msgArray", [])
        except Exception as e:
            log("即時價抓不到(這批這分鐘沒有形成中的 K)", e); return []
    groups = [codes[i:i + 40] for i in range(0, len(codes), 40)]
    res = [None] * len(groups)
    def run(k): res[k] = one(groups[k])
    ths = [threading.Thread(target=run, args=(k,), daemon=True) for k in range(len(groups))]
    for t in ths: t.start()
    for t in ths: t.join(20)
    for arr in res:
        for q in arr or []:
            c = q.get("c") or q.get("ch", "").split(".")[0]; z = q.get("z") or q.get("pz") or ""
            try: z = float(z)
            except Exception: continue
            if not (z > 0): continue
            try: tv = float(q.get("v") or 0) * 1000
            except Exception: tv = 0
            done = sum(r[5] for r in bars.get(c, []) if r[0] < hm)
            out[c] = [hm, z, z, z, z, max(0, tv - done)]
    return out


def local_bars():
    """2026-10-30 預判:中繼本機服務 127.0.0.1:QUOTE_PORT/bars(元大逐筆組的 1 分 K,含正在形成的這分鐘);拿不到回 None"""
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{CFG.get('QUOTE_PORT', '8787')}/bars", headers={"X-Quote-Key": CFG.get("PUSH_KEY", "")})
        j = json.loads(urllib.request.urlopen(req, timeout=3).read().decode("utf-8"))
        b = j.get("bars")
        return b if isinstance(b, dict) and b else None
    except Exception as e:
        log("預判:本機 K 拿不到", e); return None


def pre_frame(bars, upto, codes=None, base=None):
    """2026-10-30 預判用的 K:base = 上一輪整輪用的 Worker K(已收完的分鐘以它為準),base 沒有的最新分鐘才用本機 K
       (中繼盤中重開過,本機只有重開後的 K,不能拿來蓋整天);upto 前一分鐘可能還在形成 = 假設用現在的價收;
       upto 那分鐘當成正在形成的那根(開高低收 = 最新價、量 = 那分鐘到目前的量;和盤中 live_forming 同一種做法)"""
    bs, fm = {}, {}
    for c, rows in bars.items():
        if codes is not None and c not in codes: continue
        if base is not None:
            b0 = base.get(c) or []
            if not b0: continue   # 這檔 Worker 今天還沒有 K → 不預判(整輪會處理)
            last0 = max(r[0] for r in b0); rows = sorted(b0) + [r for r in rows if r[0] > last0]
        done = [r for r in rows if r[0] < upto]
        if not done: continue
        cur = [r for r in rows if r[0] == upto]; z = cur[-1][4] if cur else done[-1][4]
        bs[c] = done; fm[c] = [upto, z, z, z, z, cur[-1][5] if cur else 0]
    return bs, fm


def write_breadth(day):
    """2026-10-29 每分鐘抓;回傳 (抓到沒, 今天幾分鐘, 最後一分鐘)。抓不到 / 今天沒資料:不覆蓋已經有的檔(歷史不變,已推的判二部位不會因為重算變了被誤平),
       檔案不存在就寫 {}(模擬器不會因為沒有檔當掉;判二帶 BRMISSING=block → 今天沒資料就不進)"""
    f = DATA / "breadth.json"
    try:
        j = http(worker_url(f"breadth={day}"), timeout=30); br = j.get("br") or {}
        av = j.get("av") or {}
        if av: write_atomic(DATA / "mktavg.json", json.dumps({day: av}))   # 2026-11-05 判五:名單平均(Worker ?breadth 的 av)
        elif not (DATA / "mktavg.json").exists(): write_atomic(DATA / "mktavg.json", "{}")
        br = br_fill(day, br)
        if br:
            write_atomic(f, json.dumps({day: br})); return True, len(br), max(br)
        if not f.exists(): write_atomic(f, "{}")
        return False, 0, ""
    except Exception as e:
        log("寬度抓不到", e)
        try:
            br = br_fill(day, {})
            if br: write_atomic(f, json.dumps({day: br})); return True, len(br), max(br)
            if not f.exists(): write_atomic(f, "{}")
        except Exception: pass
        return False, 0, ""


_BR_PREV = {}
def br_local(day):
    """2026-11-13 12:15 使用者「改」(雲端寬度 12:05 少一分鐘 → 「電腦資料停住」、總停損那幾分鐘判斷不到):
       用電腦手上的元大 1 分 K(_TB,Worker ?ybars 每分鐘併進來的)+ 昨收(元大參考價)自己算每分鐘的寬度(收盤價 > 昨收 的比例,和 Worker 同一套)"""
    if _TB.get("d") != day or not _TB.get("rows") or not _BR_PREV: return {}
    ser = {}
    for c, dc in _TB["rows"].items():
        y = float(_BR_PREV.get(c) or 0)
        if y > 0 and dc: ser[c] = (y, sorted(dc.items()))
    if not ser: return {}
    last = _TB.get("last") or ""
    out = {}; idx = {c: 0 for c in ser}; cur = {}
    t = "09:00"
    while t <= last and t <= "13:30":
        u = dn = 0
        for c, (y, rows) in ser.items():
            i = idx[c]
            while i < len(rows) and rows[i][0] <= t: cur[c] = float(rows[i][1][4]); i += 1
            idx[c] = i
            z = cur.get(c)
            if z is None: continue
            if z > y: u += 1
            elif z < y: dn += 1
        if u + dn: out[t] = round(u / (u + dn), 3)
        t = hm_add(t, 1)
    return out


def br_fill(day, br):
    """雲端寬度缺的分鐘(或整個抓不到)→ 用電腦自己算的補上;雲端有的照雲端"""
    try:
        loc = br_local(day)
        if not loc: return br
        add = {k: v for k, v in loc.items() if k not in br}
        if add:
            log(f"寬度:雲端缺 {len(add)} 分鐘({min(add)}~{max(add)}),用電腦元大 K 補上")
            br = dict(br); br.update(add); br = dict(sorted(br.items()))
    except Exception as e: log("寬度自己算失敗", repr(e))
    return br


# ── 模擬器:每個工作行程只載入一次 pandas,之後用 runpy 一檔一檔跑 ──
BASE_ENV = dict(os.environ)   # 每次跑模擬器前把環境變數還原(判一的 OBVCROSS / GAPMIN… 不能留給判二;2026-10-29 回放抓到的 bug)
_CSV = {}
def _csv_cache_on():
    """2026-11-06 加速:工作行程裡 pandas.read_csv 依(檔案, 修改時間, 大小, 參數)快取,前幾週的 K 不每次重讀;回傳複本(模擬器改了也不影響快取)"""
    import pandas as pd
    if getattr(pd.read_csv, "_cached", False): return
    raw = pd.read_csv
    def rc(f, *a, **kw):
        try:
            st = os.stat(f); key = (str(f), st.st_mtime_ns, st.st_size, repr(a), repr(sorted(kw.items())))
        except Exception:
            return raw(f, *a, **kw)
        df = _CSV.get(key)
        if df is None:
            if len(_CSV) > 4000: _CSV.clear()
            df = _CSV[key] = raw(f, *a, **kw)
        return df.copy()
    rc._cached = True; pd.read_csv = rc

_SIM_CO = {"key": None, "co": None}
def _sim_code():
    """2026-11-06 13:21 使用者「分秒必爭」:sim_day.py(1,800 行)每個工作都重新讀檔、編譯一次 → 每個工作行程只編譯一次;檔案改了(規則每 30 分鐘重抓)才重編"""
    f = SIMD / "sim_day.py"; st = f.stat(); key = (st.st_mtime_ns, st.st_size)
    if _SIM_CO["key"] != key:
        _SIM_CO["co"] = compile(f.read_bytes(), str(f), "exec"); _SIM_CO["key"] = key
    return _SIM_CO["co"]

def _sim_one(args):
    code, name, rs, env, day = args
    sys.path.insert(0, str(SIMD))
    os.environ.clear(); os.environ.update(BASE_ENV)
    os.environ.update(env); os.environ["DAY"] = day; os.environ["REPO_DIR"] = str(DATA); os.environ["SIM_DIR"] = str(SIMD)
    os.environ["POOL"] = str(DATA / "pool_today.json")
    for k in ("TRACESTOP", "TRACEF", "TRACEX", "TRACET", "TRACES", "TRACELC", "TRACED", "TRACEG", "TRACE", "OBCTRACE"): os.environ.pop(k, None)
    buf = io.StringIO(); sys.argv = ["sim_day.py", code, name, "10", "10"]
    try:
        co = _sim_code()
        if CFG.get("ENGINE_CSV_CACHE", "1") == "1": _csv_cache_on()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            exec(co, {"__name__": "__main__", "__file__": str(SIMD / "sim_day.py"), "__builtins__": __builtins__})   # 2026-11-06 加速:同 runpy.run_path(每次全新的變數空間),但程式只編譯一次
    except SystemExit:
        pass
    except Exception as e:
        return (code, rs, "", f"{type(e).__name__}: {e}")
    return (code, rs, buf.getvalue(), "")


RE_ENTRY = re.compile(r"^\s*(\d\d:\d\d) (強)?(多|空)(✓)?[✓✕]* 價 ([\d.]+) .*→ 進場 ([\d.]+) ×(\d+)張")
RE_FB = re.compile(r"^\s*(\d\d:\d\d) 假突破跌破 Ka.*→ 進場空 ([\d.]+) ×(\d+)張,停損 ([\d.]+)")
RE_T4 = re.compile(r"^\s*(\d\d:\d\d) 判四 .*→ 進場空 ([\d.]+) ×(\d+)張,停損 ([\d.]+)")
RE_T5 = re.compile(r"^\s*(\d\d:\d\d) 判五 .*→ 進場空 ([\d.]+) ×(\d+)張,停損 ([\d.]+)")   # 2026-11-05 判五(衝高失敗、回頭跌破做空)進場   # 2026-10-31 判四(新轉折做空)進場,停損價由模擬器算好
RE_T7 = re.compile(r"^\s*(\d\d:\d\d) 判七 .*?量縮 ([\d.]+) 離今高 ([\d.]+)% 今高離VWAP ([-\d.]+)% → 進場空 ([\d.]+) ×(\d+)張,停損 ([\d.]+)")   # 2026-11-09 觀察版判七進場(帶量縮 / 離今高 / 今高離 VWAP,12/06 驗證用)
RE_T6 = re.compile(r"^\s*(\d\d:\d\d) 判六 超賣急彈 低 ([\d.]+).*?彈 ([\d.]+)% RSI ([\d.]+) 跳空 ([-+\d.]+)% → 進場多 ([\d.]+) ×(\d+)張 停損 ([\d.]+)")   # 2026-11-10 觀察版判六(超賣急彈做多)進場
RE_T6K = re.compile(r"^\s*(\d\d:\d\d) 判六 午盤Ka穿VWAP 離低 ([\d.]+)% Oa−Ob ([-\d.]+)% RSI ([\d.]+) → 進場多 ([\d.]+) ×(\d+)張 停利 ([\d.]+) 停損 ([\d.]+)")   # 2026-11-10 23:05 判六改成「午盤 Ka 穿 VWAP 追多」(使用者「改為判六 原判六封存」;觀察版,中繼不下單)
RE_T63 = re.compile(r"^\s*(\d\d:\d\d) 判六3 Ka跌破VWAP 離高 ([\d.]+)% RSI ([\d.]+) → 進場空 ([\d.]+) ×(\d+)張 停利 ([\d.]+) 停損 ([\d.]+)")   # 2026-11-12 23:40 判六做空(判六3)併入判六
RE_EXIT = re.compile(r"^\s*(\d\d:\d\d) (.+?) 出 ([\d.]+)\s*$")
RE_REV = re.compile(r"^\s*(\d\d:\d\d) .*→ (反向確認點|逆勢單遇反向點) 平倉 ([\d.]+)")
# 2026-11-04 22:40 漏洞修正(使用者「找 判一二三四 漏洞」):判二 11/04 起晚 2 根進場(ENTRYDELAY=2),模擬器的訊號那行改成「→ 2 分鐘後進場(模擬通知延遲)」、
#   真正進場是後面另一行「延遲進場 35.30 ×9張(訊號 11:30)」,RE_ENTRY 兩行都對不上 → 11/04 電腦判二一筆進場都沒發。訊號行記下方向,延遲進場那行才算進場(時間 = 進場那根,和手機「空進」同一根)
RE_DSIG = re.compile(r"^\s*(\d\d:\d\d) (強)?(多|空)(✓)?[✓✕]* 價 ([\d.]+) .*→ \d+ 分鐘後進場")
RE_DLY = re.compile(r"^\s*(\d\d:\d\d) 延遲進場 ([\d.]+) ×(\d+)張")


def parse_events(out):
    """模擬器輸出 → [(tm, 'entry'|'exit', dict)] 依時間順序"""
    ev = []; dsig = None
    for line in out.splitlines():
        m = RE_DSIG.match(line)
        if m: dsig = {"side": 1 if m.group(3) == "多" else -1, "strong": bool(m.group(2)), "sig": float(m.group(5))}; continue
        m = RE_DLY.match(line)
        if m:
            if dsig: ev.append((m.group(1), "entry", {"side": dsig["side"], "strong": dsig["strong"], "sig": dsig["sig"], "px": float(m.group(2)), "lots": int(m.group(3)), "text": ("多" if dsig["side"] > 0 else "空") + "進"}))
            dsig = None; continue
        m = RE_ENTRY.match(line)
        if m:
            ev.append((m.group(1), "entry", {"side": 1 if m.group(3) == "多" else -1, "strong": bool(m.group(2)), "sig": float(m.group(5)), "px": float(m.group(6)), "lots": int(m.group(7)), "text": ("強" if m.group(2) else "") + m.group(3) + (m.group(4) or "")})); continue   # text 和手機 m.text 一樣:空✓ / 多✓ / 強空…
        m = RE_FB.match(line)
        if m:
            ev.append((m.group(1), "entry", {"side": -1, "strong": False, "sig": float(m.group(2)), "px": float(m.group(2)), "lots": int(m.group(3)), "text": "空✓破", "sl": float(m.group(4))})); continue
        m = RE_T63.match(line)
        if m:
            ev.append((m.group(1), "entry", {"side": -1, "strong": False, "sig": float(m.group(4)), "px": float(m.group(4)), "lots": int(m.group(5)), "text": "空", "tp": float(m.group(6)), "sl": float(m.group(7)),
                                             "note": f"午盤Ka跌破VWAP 離高 {m.group(2)}% RSI {m.group(3)}"})); continue
        m = RE_T6K.match(line)
        if m:
            ev.append((m.group(1), "entry", {"side": 1, "strong": False, "sig": float(m.group(5)), "px": float(m.group(5)), "lots": int(m.group(6)), "text": "多", "tp": float(m.group(7)), "sl": float(m.group(8)),
                                             "note": f"午盤Ka穿VWAP 離低 {m.group(2)}% Oa−Ob {m.group(3)}% RSI {m.group(4)}"})); continue
        m = RE_T6.match(line)
        if m:
            ev.append((m.group(1), "entry", {"side": 1, "strong": False, "sig": float(m.group(6)), "px": float(m.group(6)), "lots": int(m.group(7)), "text": "多", "sl": float(m.group(8)),
                                             "tp": round(float(m.group(2)) * (1 + float(os.environ.get("T6RTPL_SHOW", "3")) / 100), 2), "note": f"低 {m.group(2)} 彈 {m.group(3)}% RSI {m.group(4)} 跳空 {m.group(5)}%"})); continue
        m = RE_T7.match(line)
        if m:
            ev.append((m.group(1), "entry", {"side": -1, "strong": False, "sig": float(m.group(5)), "px": float(m.group(5)), "lots": int(m.group(6)), "text": "空", "sl": float(m.group(7)),
                                             "note": f"量縮 {m.group(2)} 離今高 {m.group(3)}% 今高離VWAP {m.group(4)}%"})); continue
        m = RE_T4.match(line) or RE_T5.match(line)
        if m:
            ev.append((m.group(1), "entry", {"side": -1, "strong": False, "sig": float(m.group(2)), "px": float(m.group(2)), "lots": int(m.group(3)), "text": "空", "sl": float(m.group(4))})); continue
        m = RE_REV.match(line)
        if m:
            ev.append((m.group(1), "exit", {"why": m.group(2), "px": float(m.group(3))})); continue
        m = RE_EXIT.match(line)
        if m and not line.startswith("  [") and "進場" not in line:
            ev.append((m.group(1), "exit", {"why": m.group(2), "px": float(m.group(3))}))
    return ev


def sim_trades(ev, upto):
    """2026-10-29 事件 → 模擬器現在的交易清單 {進場 K 時間: (進場 dict, 出場 (tm, dict) 或 None)} + 最後還開著的那筆的進場時間"""
    trades = {}; cur = None
    for tm, kind, d in ev:
        if tm >= upto: continue
        if kind == "entry":
            if cur is None: cur = tm; trades[tm] = (d, None)
        elif cur is not None:
            trades[cur] = (trades[cur][0], (tm, d)); cur = None
    return trades, cur


def exit_text(why):
    if why.startswith("停利"): return "停利"
    if "總停損" in why: return "總平"           # 2026-11-11 22:47 使用者「總停損 =總平 移動停損利=移平」:通知標題用短名(原「總停損」)   # 2026-11-11 22:35 使用者「改 1 2 3 4 5」 5:鐵律出場照原名寫進通知標題,中繼才認得(有權取消預掛立即平)
    if "移動停損利" in why: return "移平"
    if "停損" in why: return "停損"
    if why.startswith("OBV"): return "OBV"
    if "量縮轉折" in why: return "量縮轉折"   # 2026-11-06 判四第十九次修改:反向量縮轉折出場
    if why.startswith("超過"): return "超" + why.replace("超過", "").strip()
    if why.endswith("強制平倉"): return "出"
    if "反向" in why: return "反向"
    if "沒走" in why: return "沒走"
    if "保本" in why: return "保本"
    if "Kb" in why: return "破Kb"   # 2026-10-30 判一 / 判二 zoneHold:量縮回到 Ka~Kb 續抱後收盤越過 Kb 0.3% 出場
    return "出"


# ── 名單:照手機 buildPool / scan / tradable 篩(2026-10-29 稽核 #3;原本整份名單都算)──
FIN_FALLBACK = {"5820", "5871", "5876", "5878", "5880", "6005", "6024", "2820"}


def tick_pct(p):
    t = 0.01 if p < 10 else 0.05 if p < 50 else 0.1 if p < 100 else 0.5 if p < 500 else 1 if p < 1000 else 5
    return t / p * 100 if p > 0 else 99


def _f(v, d):
    try: return float(v) if v not in (None, "") else float(d)
    except Exception: return float(d)


def pool_rules(j, cap):
    r = (j.get("rules") or {}) if isinstance(j, dict) else {}
    # 2026-11-07 19:16 使用者「這裡面的設定 程式要照設定走」:監控低價 / 高價 / 最低振幅 / 監控檔數 改跟主導機的設定(cap=get 的 set);讀不到才用 bridge.env 的 ENGINE_*
    cs = lambda k, d: _f(re.sub(r"[^\d.]", "", str(CLOUD_SET.get(k, ""))) or d, d)
    return {"lo": max(_f(r.get("lo"), 10), cs("lo", _f(CFG.get("ENGINE_LO"), 10))),           # 滾輪「監控低價」(名單底線 10 元以下不會有)
            "hi": min(_f(r.get("hi"), 500), cs("hi", _f(CFG.get("ENGINE_HI"), 500))),           # 滾輪「監控高價」
            "amp": max(_f(r.get("min_amp"), 3.0), cs("amp", _f(CFG.get("ENGINE_AMP"), 3.0))),   # 滾輪「最低振幅」
            "size": int(_f(r.get("size"), 130)), "cnt": int(cs("cnt", _f(CFG.get("ENGINE_MAX_CODES"), 99))),   # 滾輪「監控檔數」
            "tick": _f(r.get("tick_max_pct"), 0.5), "excl": [str(x) for x in (r.get("exclude_industry") or ["17"])],
            "a5": _f(r.get("avg5_min_amp"), 3.0), "hits": r.get("avg5_min_hits"),
            # 2026-11-07 16:15 使用者加手機滾輪「最低張數 / 成交K棒 / 連續天數」(初始 6000 / 250 / 1):跟主導機設定,讀不到用初始值
            "lots": _f(CLOUD_SET.get("lots"), 6000), "bars": _f(CLOUD_SET.get("bars"), 250), "days": int(_f(re.sub(r"\D", "", str(CLOUD_SET.get("days", "1"))) or 1, 1))}   # 2026-11-07 20:23 使用者「改回1」:讀不到用 1


def vnet(sd, e, x, q):
    """2026-11-13 虛擬一筆的淨輸贏(檢討 / 雲端虛擬明細同一個算法)"""
    buy, sell = (e, x) if sd > 0 else (x, e); b = buy * q * 1000; s_ = sell * q * 1000; fr, tx = cost_rates(); fee = lambda a: max(20, int(a * fr + 1e-6))
    return round(s_ - b - fee(b) - fee(s_) - int(s_ * tx + 1e-6))


def cost_rates():
    """2026-11-07 19:16 手續費跟主導機的設定(手機 cost() 同一套):單邊 = 證商費 × 手續折扣 + 證手費;證交稅只算賣出那邊"""
    p = lambda k, d: _f(re.sub(r"[^\d.]", "", str(CLOUD_SET.get(k, ""))) or d, d) / 100
    return p("broker", 0.1425) * p("disc", 25) + p("exch", 0), p("tax", 0.15)


def days_ok(s, R):
    """手機 daysOk 同一套:連續 R["days"] 天,每天 成交張數 ≥ lots、有成交的 1 分K ≥ bars、振幅 ≥ amp(d3 = [[日期, 張數, K棒, 振幅], …] 新到舊)"""
    d3 = s.get("d3")
    if not isinstance(d3, list):   # 舊名單沒有 d3:只看前一天
        if s.get("lots") is not None and _f(s.get("lots"), 0) < R["lots"]: return False
        if s.get("bars") is not None and _f(s.get("bars"), 0) < R["bars"]: return False
        return True
    for i in range(R["days"]):
        if i >= len(d3) or not d3[i]: return False
        _, lo, br, am = (list(d3[i]) + [None] * 4)[:4]
        if lo is not None and lo < R["lots"]: return False
        if br is not None and br < R["bars"]: return False
        if am is not None and am < R["amp"]: return False
    return True


def stock_ok(s, j, R):
    """手機 loadRemotePool 的 filter + buildPool 的 filter + scan 的價格 / 振幅 + tradable();回傳 None = 可以,否則是原因"""
    c = str(s.get("code", ""))
    if not re.fullmatch(r"[1-9]\d{3}", c): return "非普通股"
    if s.get("market") not in ("twse", "tpex"): return "市場"
    if c in set(map(str, j.get("disposed", []))) or s.get("disposed") is True: return "處置股"
    if c in set(map(str, j.get("attention", []))): return "注意股"
    try:   # 2026-10-30 11:08(實際時間)電腦開盤前複檢(elig.py)的結果也套上:名單檔漏的(10/30 東台)這裡擋
        import elig; eb = elig.blocked(c, "short")
        if eb: return eb
    except Exception: pass
    try: close = float(s.get("close"))
    except Exception: return "沒有收盤價"
    if not (R["lo"] <= close <= R["hi"]): return "價格範圍"
    if s.get("amp") is None or float(s["amp"]) < R["amp"]: return "振幅"
    # 2026-10-31 使用者選 B:昨收跌停也放行(和手機 tradable 一樣拿掉)
    if (str(s["industry"]) in R["excl"]) if s.get("industry") else (c.startswith("28") or c in FIN_FALLBACK): return "金融保險"
    if s.get("amp5") is not None and float(s["amp5"]) < R["a5"]: return "5 日振幅"
    if s.get("amp_hits") is not None and R["hits"] is not None and float(s["amp_hits"]) < float(R["hits"]): return "5 日振幅天數"
    if (float(s["tick_pct"]) if s.get("tick_pct") is not None else tick_pct(close)) > R["tick"]: return "一檔跳動"
    if s.get("daytrade") != 1: return "不可當沖"
    if s.get("short_first") != 1: return "不可先賣"   # 手機 tradable():dt && dtShort 才列入(沒標記 = 不可)
    return None


def set_desc(j, cap):
    """2026-11-11 17:02 使用者「主導機 明明設6000 成交K 明明設250 怎麼電腦自己變了」→ 開工 / 重選時把實際用的選股設定寫進紀錄(讀沒讀到主導機一看就知道)"""
    try:
        R = pool_rules(j if isinstance(j, dict) else {"pool": j}, cap); src = "主導機" if CLOUD_SET else "讀不到主導機,用預設"
        return f"選股設定({src}):{R['lo']:g}~{R['hi']:g} 元、振幅 ≥{R['amp']:g}%、張數 ≥{R['lots']:g}、成交K棒 ≥{R['bars']:g}、連續 {R['days']} 天、監控 {R['cnt']} 檔、多空上限 {cap:,}"
    except Exception as e: return f"選股設定(寫紀錄失敗 {e!r})"

def build_universe(j, day, cap, held=(), noted=(), old_src=None):
    """名單 → 今天要算的股票。held = 已推進場還開著的;noted = 今天推過通知的(和手機 scan 一樣不受篩選、不佔檔數)
       回傳 dict:codes / names / prev / mkts / canshort / no_new(今天通知過但現在不合格 → 只算平倉、不發新進場)/ src / stamp / why"""
    pool = j.get("pool", j if isinstance(j, list) else []) if isinstance(j, (dict, list)) else []
    if isinstance(j, list): j = {"pool": j}
    R = pool_rules(j, cap)
    src = {str(s["code"]): s for s in pool if "code" in s}
    # 2026-11-06 名單外手動成交的股票(中繼 extra_add 寫 engine_data/extra_codes.json):當成名單股照算判一~五,不篩、不佔檔數
    ex = [s for s in extra_codes(day) if str(s.get("code")) not in src]
    for s in ex: src[str(s["code"])] = s
    held = list(held) + [str(s["code"]) for s in ex]
    for c, s in (old_src or {}).items():
        if c not in src and (c in held or c in noted): src[c] = s   # 換名單後不見的、今天有通知的:用舊資料照算(平倉要靠它)
    carried = [c for c in list(held) + list(noted) if c in src]
    carried = list(dict.fromkeys(carried))
    stage1 = sorted([s for s in pool if str(s.get("code")) not in carried and re.fullmatch(r"[1-9]\d{3}", str(s.get("code", ""))) and s.get("market") in ("twse", "tpex")
                     and str(s.get("code")) not in set(map(str, j.get("disposed", []))) and R["lo"] <= _f(s.get("close"), 0) <= R["hi"] and s.get("amp") is not None and _f(s.get("amp"), 0) >= R["amp"] and days_ok(s, R)],
                    key=lambda s: -_f(s.get("lots"), 0))[: R["size"]]   # buildPool:成交張數大到小,前 size 名
    # 2026-11-03 20:50 使用者「查 味全」:11/02 最低價改 10 元時這裡還寫死 15 元 → 味全 13.65、彩晶 13.45 在名單裡卻沒算(電腦只算 60 檔),改成和手機 buildPool 一樣用 BASE.lo / hi / amp
    why = {}; rest = []
    for s in stage1:
        w = stock_ok(s, j, R)
        if w: why[w] = why.get(w, 0) + 1
        else: rest.append(str(s["code"]))
    n_held = len([c for c in held if c in src])
    codes = carried + rest[: max(0, R["cnt"] - n_held)]
    no_new = {c for c in carried if not src[c].get("extra") and stock_ok(src[c], j, R)}
    names, prev, mkts, canshort = {}, {}, {}, {}
    for c in codes:
        s = src[c]; names[c] = s.get("name", "")
        prev[c] = s.get("close") if str(s.get("date") or j.get("date", "")) < day else s.get("prev")
        mkts[c] = "otc" if str(s.get("market", "")).lower() in ("otc", "tpex") else "tse"
        canshort[c] = s.get("daytrade") == 1 and s.get("short_first") == 1   # 2026-10-29 不能當沖 / 不能先賣 → 模擬器帶 CANSHORT=0(空單只記錄)
    out = dict(j); out["pool"] = [src[c] for c in src]   # 模擬器用代碼查昨收,換名單後不見的股票也要在裡面
    write_atomic(DATA / "pool_today.json", json.dumps(out, ensure_ascii=False))
    return {"codes": codes, "names": names, "prev": prev, "mkts": mkts, "canshort": canshort, "no_new": no_new, "src": src,
            "stamp": f"{j.get('generated', '')}|{j.get('elig_checked_at', '')}", "why": why, "R": R, "j": j, "xstamp": extra_stamp()}


def extra_codes(day):
    """2026-11-06 名單外股票(中繼盤中補好 K 才寫進來);不是今天的不算"""
    try:
        x = json.loads((DATA / "extra_codes.json").read_text(encoding="utf-8"))
        return [s for s in (x.get("codes") or []) if s.get("code")] if x.get("day") == day else []
    except Exception:
        return []


def extra_stamp():
    try:
        st = (DATA / "extra_codes.json").stat(); return f"{st.st_mtime_ns}|{st.st_size}"
    except Exception:
        return ""


def fetch_pool_json(day):
    """GitHub 最新的 pool.json(資格複檢後會變);資料日期要比今天早(14:30 後換成明天的名單不能用)"""
    f = DATA / "pool_fetch.json"
    if not fetch_raw("pool.json", f): return None
    try:
        j = json.loads(f.read_text(encoding="utf-8"))
        if not isinstance(j, dict) or not j.get("pool"): return None
        if str(j.get("date", "")) >= day: log(f"雲端 pool.json 日期 {j.get('date')} 不早於今天 {day}(已換成下個交易日的名單)→ 不用"); return None
        return j
    except Exception as e:
        log("雲端 pool.json 讀不懂", e); return None


def load_pool(day, fresh=True):
    """2026-10-29 先用雲端最新的 pool.json(資格複檢後);抓不到才用中繼早上存的 pool_<day>.json、再不行用上次抓的"""
    DATA.mkdir(parents=True, exist_ok=True)   # 2026-10-29 桌機第一次跑:engine_data 還不存在(使用者截圖 FileNotFoundError)
    j = fetch_pool_json(day) if fresh else None
    if j is None:
        for p in (HERE / f"pool_{day}.json", DATA / "pool.json"):
            if p.exists():
                try: j = json.loads(p.read_text(encoding="utf-8")); log(f"名單用本機 {p.name}"); break
                except Exception: pass
    if j is None:
        fetch_raw("pool.json", DATA / "pool.json"); j = json.loads((DATA / "pool.json").read_text(encoding="utf-8"))
    return j


class Engine:
    def __init__(self, day, uni, dry=False, state=True):
        self.day, self.dry = day, dry
        self.pos = {}         # (rs, code) → {side, t, px, lots, text}:已推進場、還沒推平倉的
        self.emitted = set()  # (rs, code, 訊號 K 時間):已經處理過的進場(推了、或太舊 / 雲端已有),不再發(2026-10-29 稽核 #7,存檔)
        self.cloud_in = {}    # (rs, code, tm) → item:雲端今天的進場通知(手機 / 上一個 engine 發的)
        self.cloud_out = set()  # (rs, code, tm):雲端已經有平倉通知的部位
        self.fclosed = set()    # 2026-11-02 今天手機強平過的股票(雲端有「· 強平 ·」的平倉):這檔不再發任何判一~四的進場 / 平倉通知
        self.j63 = set()      # 2026-11-12 23:40 判六做空(判六3)今天已進場的股票:一天最多 J63MAX 檔
        self.j5 = set()       # 2026-11-05 判五今天已進場的股票:一天最多 J5MAX 檔
        self.j4 = set()       # 2026-10-31 判四今天已進場(推了、或雲端已有,超額的不算)的股票:一天最多 T4MAX 檔
        self.closed = []; self.lat = []; self.rounds = []   # 2026-11-07 收盤檢討(A):今天平倉的部位、進場通知晚幾秒、每輪幾秒
        self.drag = set()     # 2026-11-07 已發過「可能拖輸」提示的部位(判別式|代碼|進場時間)
        self.no_bar_note = set()   # 2026-11-10 今天還沒成交、先不算的股票(log 只寫一次)
        self.sent = []; self.px = {}; self.last_hm = {}; self.held_note = set()   # px / last_hm:每檔最新價、今天 K 到哪一分鐘(判定「訊號消失」前要確認這檔的 K 有到)
        self.state_f = DATA / f"engine_state_{day}.json" if state else None
        if self.state_f:
            try:
                j = json.loads(self.state_f.read_text(encoding="utf-8"))
                self.pos = {tuple(k.split("|")): v for k, v in j.get("pos", {}).items()}
                self.emitted = set(tuple(x) for x in j.get("emitted", []))
                self.j4 = set(j.get("j4", [])); self.j5 = set(j.get("j5", [])); self.j63 = set(j.get("j63", []))
                self.drag = set(j.get("drag", []))
                self.closed = j.get("closed", []); self.lat = j.get("lat", [])
                self.emitted |= {(x[0], x[1], x[2]) for x in j.get("seen", []) if len(x) == 4 and x[3] == "entry"}   # 舊版狀態檔
                if self.pos or self.emitted: log(f"接回狀態:開著 {len(self.pos)} 筆、處理過的進場 {len(self.emitted)} 個")
            except FileNotFoundError: pass
            except Exception as e: log("!! 狀態檔讀不懂(當作沒有;雲端通知會再對一次)", e)
        self.cap = min(int(CFG.get("ENGINE_CAP", "250000")) if dry else cloud_cap(int(CFG.get("ENGINE_CAP", "250000"))), 250000)   # 2026-11-11 使用者「最多上限改25萬」   # 2026-11-02 跟 5861 主導機(回放 --dry 不讀雲端);手機的多空上限(通知裡的張數);2026-10-29 手機設 25 萬
        self.set_universe(uni)
        self.upto_hm = ""   # 2026-11-06 這輪算到幾點(compute 用來判斷判別式是不是已過進場時段)
        self.reload_envs()
        self.pool = Pool(int(CFG.get("ENGINE_WORKERS", "12")))   # 2026-11-07 20:02 使用者「改」:8 → 12(記憶體 15.9 GB、平常用 7.9 GB;前 10 個吃實體核心,後 2 個用超執行緒)   # 2026-11-06 3 → 8(電腦 Xeon E5-2680 v2 10 核 20 緒;一輪 ~55 秒 → 目標 ~20 秒)

    def set_universe(self, uni):
        self.uni = uni; self.codes = uni["codes"]; self.names = uni["names"]; self.prev = uni["prev"]; self.mkts = uni["mkts"]
        global _BR_PREV; _BR_PREV = self.prev   # 2026-11-13 寬度自己算用(同一份,元大參考價更新也跟著)
        self.canshort = uni["canshort"]; self.no_new = uni["no_new"]
        try: write_atomic(DATA / f"codes_{self.day}.json", json.dumps({"codes": list(self.codes), "names": {c: self.names.get(c, "") for c in self.codes}, "stamp": uni.get("stamp", ""), "R": uni.get("R")}, ensure_ascii=False, default=str))   # 2026-11-12 20:22 引擎實際監控的股票(中繼備份到雲端;11/12 回測名單對不上)
        except Exception as e: log("監控名單寫檔失敗", repr(e))
        try: self.apply_refs(force=True)
        except Exception as e: log("昨收對照失敗", repr(e))

    def apply_refs(self, force=False):
        """2026-11-10 17:26(實際時間)使用者「官方昨收 應該要有備用補齊通道」→「改」:今天每檔的昨收(=交易所參考價,除權息已扣)依序取
           ① 中繼寫的 ref_<日期>.json(元大清盤參考價)② Worker ?codes 的 y(證交所 / 櫃買即時報價的昨收,10 分鐘最多問一次)③ 名單昨收 ④ 模擬器自己的 K 線(最後備援)。
           ①② 拿到的寫進 pool_today.json(prev、date=今天、ref_src),所有判別式的模擬器、通知都用同一份;和名單昨收差 > 0.5% 的記 log、推一則提醒(一天一次)"""
        if not getattr(self, "codes", None): return
        R = self.__dict__.setdefault("_ref", {"map": {}, "src": {}, "w_at": 0.0, "warned": set()})
        try: yt = json.loads((DATA / f"ref_{self.day}.json").read_text(encoding="utf-8")) or {}
        except Exception: yt = {}
        for c, v in yt.items():
            try:
                if float(v) > 0: R["map"][c] = float(v); R["src"][c] = "元大"
            except Exception: pass
        miss = [c for c in self.codes if c not in R["map"]]
        if miss and (force or time.time() - R["w_at"] > 600):
            R["w_at"] = time.time()
            for i in range(0, len(miss), 40):
                cs = miss[i:i + 40]
                try:
                    for m in http(worker_url("codes=" + ",".join(cs) + "&mkt=" + ",".join(self.mkts.get(c, "tse") for c in cs)), timeout=15).get("msgArray", []):
                        c, y = str(m.get("c", "")), _f(m.get("y"), 0)
                        if c and y > 0 and R["src"].get(c) != "元大": R["map"][c] = y; R["src"][c] = "證交所"
                except Exception as e: log("昨收備援(證交所)抓不到", repr(e))
        if not R["map"]: return
        try: pj = json.loads((DATA / "pool_today.json").read_text(encoding="utf-8"))
        except Exception: return
        changed = False; diff = []
        for s_ in pj.get("pool", []):
            c = str(s_.get("code", "")); v = R["map"].get(c)
            if not v: continue
            p0 = s_.get("close") if str(s_.get("date") or pj.get("date", "")) < self.day else s_.get("prev")
            if s_.get("ref_src"): p0 = s_.get("pool_prev", p0)
            if p0 and abs(v / float(p0) - 1) > 0.005: diff.append((c, s_.get("name", ""), float(p0), v, R["src"][c]))
            if s_.get("prev") != v or s_.get("date") != self.day or s_.get("ref_src") != R["src"][c]:
                if not s_.get("ref_src"): s_["pool_prev"] = p0
                s_["prev"] = v; s_["date"] = self.day; s_["ref_src"] = R["src"][c]; changed = True
            if c in self.prev: self.prev[c] = v
        if changed: write_atomic(DATA / "pool_today.json", json.dumps(pj, ensure_ascii=False))
        new = [x for x in diff if x[0] not in R["warned"]]
        if new:
            for x in new: R["warned"].add(x[0])
            txt = "、".join(f"{n or c} {c} 名單 {p0:g} → 參考價 {v:g}({(v / p0 - 1) * 100:+.1f}%,{sr})" for c, n, p0, v, sr in new[:12])
            log(f"!! 昨收和名單不同 {len(new)} 檔(除權息或名單資料錯,判別式改用參考價):{txt}")
            if not self.dry:
                try: http(worker_url("log=add"), {"d": self.day, "t": now_tw().strftime("%H:%M"), "title": f"ℹ 今天昨收和名單不同 {len(new)} 檔", "body": txt + " · 元大中繼", "kind": "pc", "key": f"refdiff|{len(R['warned'])}"}, key=True)
                except Exception as e: log("昨收提醒推不上去", repr(e))

    def carried(self):
        held = {c for _, c in self.pos}
        return held, {c for _, c, _ in self.emitted} - held

    def reload_envs(self):
        sys.path.insert(0, str(SIMD))
        import importlib
        if "rs_env" in sys.modules: rs_env = importlib.reload(sys.modules["rs_env"])
        else: rs_env = importlib.import_module("rs_env")
        self.rs_pri = getattr(rs_env, "RS_PRIORITY", None)   # 2026-11-10 同一分鐘同一檔的判別式優先順序(勝率);私人庫沒設就用 engine 的 RS_PRIORITY
        self.envs = {}
        self.txsync_fn = getattr(rs_env, "txsync", None)   # 2026-11-13 22:30 ⑧ 台指期同步進場(使用者「解鎖 改 全部套用」):判斷寫在私人庫 rs_env.txsync(模擬器同一份)
        self.j5max = int(getattr(rs_env, "J5MAX", J5MAX))   # 2026-11-05 判五一天上限(私人庫 rs_env.J5MAX;apply 裡沒有 rs_env 這個名字)
        for rs in RS_ALL: self.envs[rs] = rs_env.env_of(rs, str(DATA / "breadth.json") if rs in ("一", "二", "五", "六", "六3") else None)
        for _e in self.envs.values():
            if _e.get("TXSYNC"): _e["TXFDIR"] = str(HERE / "kline_futures" / "TXF")   # ⑧ 模擬器讀中繼存的台指期 1 分 K   # 2026-11-10 23:05 判六(午盤 Ka 穿 VWAP)要寬度 10 分鐘變化   # 判五:名單平均檔 DATA/mktavg.json(write_breadth 寫)
        if self.envs.get("六", {}).get("MODE") not in ("j6k", "j6r"):   # 2026-11-10 23:05 判六 = 午盤 Ka 穿 VWAP(j6k;舊超賣急彈 j6r 封存但還認得);私人庫沒有 → 判六先不跑
            self.envs.pop("六", None); log("!! 私人庫 rs_env.py 還沒有觀察版判六(J6 MODE=j6k),判六今天先不跑")
        if self.envs.get("六3", {}).get("MODE") != "j63" or "六" not in self.envs:   # 2026-11-12 23:40 判六做空(判六3):私人庫沒有 J63 / 判六沒開 → 不跑
            self.envs.pop("六3", None); log("!! 私人庫 rs_env.py 沒有判六3(J63)或判六沒開,判六做空今天先不跑")
        self.j63max = int(getattr(rs_env, "J63MAX", J63MAX))
        if self.envs.get("七", {}).get("MODE") != "j7":   # 2026-11-09 私人庫 rs_env.py 還沒有觀察版判七(J7)→ 判七先不跑
            self.envs.pop("七", None); log("!! 私人庫 rs_env.py 還沒有判七(J7),判七今天先不跑")
        if self.envs.get("五", {}).get("MODE") != "fail5":   # 私人庫的 rs_env.py 還是舊版(沒有 J5)→ 判五先不跑(不然會拿判二的參數亂算)
            self.envs.pop("五", None); log("!! 私人庫 rs_env.py 還沒有判五(J5),判五今天先不跑")
        self.envs["二"]["BRMISSING"] = "block"   # 2026-10-29 稽核 #1:今天沒有寬度資料 → 判二不進(sim_day.py 已支援)

    def save(self):
        if not self.state_f: return
        try: write_atomic(self.state_f, json.dumps({"emitted": sorted(self.emitted), "j4": sorted(self.j4), "j5": sorted(self.j5), "j63": sorted(getattr(self, "j63", set())), "drag": sorted(self.drag), "closed": self.closed, "lat": self.lat, "pos": {"|".join(k): v for k, v in self.pos.items()}, "ver": RULES["ver"]}, ensure_ascii=False))
        except Exception as e: log("!! 狀態檔寫不進去", e)

    def push(self, it):
        """回傳 True = 推上去了(dry 也算);失敗的進場不記,下一輪(10 分鐘內)再試;失敗的平倉部位留著,下一輪再推"""
        it.setdefault("ts", int(time.time() * 1000))   # 2026-11-13 14:30 使用者「開始」(Cloudflare 第二輪 1 延遲紀錄):引擎推出的時間(毫秒),中繼 / 雲端各自記收到的時間
        self.sent.append(it)
        log(f"[通知] {it['kind']:6} {it['t']} {it['title']} | {it['body']}")
        if self.dry: return True
        # 2026-10-30 01:32 使用者「如何確保進場股票價格一出來的瞬間 交易可以成立」→ A「先做A」:同一台電腦的中繼不用等 Worker、不用每 3 秒輪詢 →
        #   推 Worker 之前先寫一行到本機 engine_data/live_<日期>.jsonl,中繼每秒讀(_k 和 Worker 存的鍵同一套,之後 Worker 那份讀到會被當成同一則,不會重複下單)
        kk = None
        try:
            kk = it["t"] + ":" + re.sub(r"[^A-Za-z0-9_\u4e00-\u9fff|:.\-]", "", str(it.get("key") or it["title"]))[:120]
            with open(DATA / f"live_{it.get('d') or self.day}.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps({**it, "_k": kk}, ensure_ascii=False) + "\n"); f.flush(); os.fsync(f.fileno())
        except Exception as e: log("!! 本機通知檔寫不進去(中繼改等 Worker)", e); kk = None
        if kk and it.get("kind") in ("long", "short"): it = self.verdict(it, kk)
        for k in range(3):
            try:
                r = http(worker_url("log=add"), it, key=True)
                if r.get("ok"): return True
                log("推通知被拒", r); return False
            except Exception as e:
                log(f"推通知失敗 第 {k + 1} 次", e); time.sleep(2)
        return False

    def verdict(self, it, kk):
        """2026-11-13 11:20 使用者選 A(和益在不管名單沒進場,手機卻跳「進場」):進場通知推雲端前,等中繼回報有沒有進場(engine_data/verdict_<日期>.jsonl,
           最多 ENGINE_VERDICT_WAIT 秒,預設 3);沒進場 → 標題「進場」改「未進場」、內容判別式前面加「未進場:原因」;等不到就照原樣推"""
        try: w = float(CFG.get("ENGINE_VERDICT_WAIT", "3") or 0)
        except Exception: w = 3.0
        if w <= 0: return it
        f = DATA / f"verdict_{it.get('d') or self.day}.jsonl"; end = time.time() + min(w, 8)
        while True:
            try:
                if f.exists():
                    for ln in f.read_text(encoding="utf-8").splitlines():
                        if kk not in ln: continue
                        v = json.loads(ln)
                        if v.get("_k") != kk: continue
                        if v.get("ok"): return it
                        why = str(v.get("why") or "").strip()
                        t2 = it["title"].replace("進場", "未進場", 1)
                        b2 = re.sub(r"(\s*·\s*判[一二三四五六七])\s*$", f" · 未進場{':' + why if why else ''}\\1", it["body"])
                        log(f"[通知] 中繼沒進場({why or '?'})→ 推「{t2}」")
                        return {**it, "title": t2, "body": b2}
            except Exception as e: log("讀進場結果檔失敗", e); return it
            if time.time() >= end: log(f"[通知] 等不到中繼的進場結果({w:g} 秒),照原樣推:{it['title']}"); return it
            time.sleep(0.2)

    def key_of(self, code, title, rs):
        return code + "|" + re.sub(r"[0-9.,+−%↑↓\s⚠✅-]", "", title) + "|" + JUDGE[rs]

    def sync_cloud(self):
        """2026-10-29 稽核 #7:讀雲端今天的通知(手機 / 上一個 engine / 重開前的自己發的),同一個訊號不重發、還開著的接回來管平倉"""
        if self.dry or not CFG.get("WORKER_URL"): return False
        try:
            r = urllib.request.urlopen(urllib.request.Request(worker_url(f"log=get&d={self.day}&_={int(time.time())}"), headers={"X-Push-Key": CFG.get("PUSH_KEY", ""), "User-Agent": "mysh-engine/1"}), timeout=10)
            arr = json.loads(r.read().decode("utf-8"))
        except Exception as e:
            log("讀雲端通知失敗(這輪只靠本機狀態去重)", e); return False
        for it in arr if isinstance(arr, list) else []:
            if re.search(r"·\s*強平\s*·", it.get("body", "")):   # 2026-11-02 10:23 使用者「這檔已經平倉者 就不用再通知顯示停利或其他了」
                mf = re.search(r"\b(\d{4,6})\b", it.get("title", ""))
                if mf and mf.group(1) not in self.fclosed: self.fclosed.add(mf.group(1)); log(f"{mf.group(1)} 手機強平過 → 今天這檔不再發通知")
            mj = re.search(r"判[一二三四五六七]", it.get("body", "")); rs = RS_OF.get(mj.group(0)) if mj else None   # 2026-11-13 00:02 使用者「改 A B」 A:判六也對帳(原本漏掉,重開又沒狀態檔時判六會重發、做空一天 5 檔上限接不回來)
            if not rs: continue
            if rs == "六" and (it.get("kind") == "short" or (it.get("kind") not in ("long", "short") and re.search(r"做空\s+\d\d:\d\d\s+進", it.get("body", "")))): rs = "六3"   # B:判六做空(進場 kind=short、平倉內容「做空 HH:MM 進」)對回 六3
            if it.get("kind") in ("long", "short"):
                mc = re.search(r"\b(\d{4,6})\b", it.get("title", "")); mt = re.match(r"\s*(\d\d:\d\d)", it.get("body", ""))
                if mc and mt: self.cloud_in[(rs, mc.group(1), mt.group(1))] = it
                if mc and rs == "四" and not re.search(r"·\s*超額\s*·", it.get("body", "")): self.j4.add(mc.group(1))
                if mc and rs == "五" and not re.search(r"·\s*超額\s*·", it.get("body", "")): self.j5.add(mc.group(1))
                if mc and rs == "六3" and not re.search(r"·\s*超額\s*·", it.get("body", "")): self.j63.add(mc.group(1))   # A:判六做空一天上限也從雲端接回   # 2026-10-31 手機 / 上一個 engine 發過的判四也算進一天上限
            elif it.get("pos"):
                p = str(it["pos"]).split("|")
                if len(p) == 3 and p[0] in RS_OF: self.cloud_out.add(("六3" if rs == "六3" else RS_OF[p[0]], p[1], p[2]))   # B:判六做空的平倉記成 六3
        return True

    def push_exit(self, rs, code, p, tm, ex, why, at, phantom=False):
        nm = self.names.get(code, code); j = JUDGE[rs]
        pl = (ex - p["px"]) / p["px"] * 100 * p["side"]; plTxt = f" {pl:+.1f}%"
        side = "做多" if p["side"] > 0 else "做空"; frm = f"{side} {p['t']} 進 {p['px']:.2f} → "
        if phantom:   # 2026-10-29 稽核 #2:已推的進場在重算裡不見了(形成中那根變了)→ 一定要叫中繼平掉,不然部位沒人管
            title, kind = f"{nm} {code} 出場:訊號重算消失,平倉", "exit"
        else:
            text = exit_text(why)
            if pl < 0: title, kind = f"⚠⚠ {nm} {code} {text}{plTxt} 立即平倉", "loss"
            elif text == "停利": title, kind = f"✅ {nm} {code} 停利{plTxt}", "profit"
            elif text == "OBV": title, kind = f"✅ {nm} {code} OBV 反轉提早停利{plTxt} 立即平倉", "profit"
            else: title, kind = f"{nm} {code} 出場{plTxt}:{text}", "exit"
        body = f"{tm} {frm}現價 {ex:.2f} · {'超額' if p.get('over') else str(p['lots']) + '張'}{' · 已強平' if p.get('fc') and not p.get('over') else ''} · {j}"   # 2026-10-30 超額的部位平倉也標「超額」
        it = {"d": self.day, "t": at, "title": title, "body": body, "kind": kind, "key": self.key_of(code, title, rs), "pos": f"{j}|{code}|{p['t']}"}
        y = self.prev.get(code)
        if y: it["prev"] = y
        ok = self.push(it)
        if ok:   # 2026-11-10 15:01(實際時間)使用者「金額對不上」(明細虛擬 +4,934、檢討 +2,961):「訊號重算消失」的平倉(群創 +1,260、富喬 +713)明細有算、檢討沒算 → 一起算進檢討
            try: self.closed.append({"rs": j, "code": code, "name": nm, "side": p["side"], "t": p["t"], "e": p["px"], "xt": tm, "x": ex, "lots": p.get("lots") or 1, "over": bool(p.get("over")), "why": "訊號消失" if phantom else exit_text(why)})
            except Exception: pass
            threading.Thread(target=self.push_vdone, daemon=True).start()   # 2026-11-13 雲端虛擬明細(唯一來源)
        return ok

    @staticmethod
    def raw123(rows, i, d):
        """2026-11-07 判七原始 ①②③(不看 4 特徵),d=+1 做多方向(①爆量殺到底 ②量縮 ③力道翻多),d=-1 反過來;rows = 今天 [hm,o,h,l,c,v](和回測 rb_j5x._raw123 同一套)"""
        if i < 12: return False
        v = [r[5] for r in rows[:i + 1]]; c = [r[4] for r in rows[:i + 1]]; h = [r[2] for r in rows[:i + 1]]; l = [r[3] for r in rows[:i + 1]]
        a = max(0, i - 60); va = sum(v) / len(v)
        if (v[i - 1] + v[i]) / 2 > va * 0.35 + 1e-9: return False
        def R(j):
            bb = max(1, j - 9); u = sum(v[k] for k in range(bb, j + 1) if c[k] > c[k - 1]); dn = sum(v[k] for k in range(bb, j + 1) if c[k] < c[k - 1])
            return (u - dn) / (u + dn) if u + dn > 0 else 0.0
        r = R(i)
        if not (r * d > 0 and min(R(i - q) * d for q in (1, 2, 3)) < 0): return False
        if d > 0:
            seg = l[a:i + 1]; ex = min(seg); ie = a + seg.index(ex); oth = max(h[a:ie + 1])
            if (oth - ex) / ex * 100 < 2 - 1e-9 or (c[i] - ex) / ex * 100 > 1.5 + 1e-9 or ie >= i: return False
        else:
            seg = h[a:i + 1]; ex = max(seg); ie = a + seg.index(ex); oth = min(l[a:ie + 1])
            if (ex - oth) / oth * 100 < 2 - 1e-9 or (ex - c[i]) / ex * 100 > 1.5 + 1e-9 or ie >= i: return False
        return max(v[max(a, ie - 5):ie + 1]) >= va * 1.3 - 1e-9

    def push_vdone(self, fin=False):
        """2026-11-13 15:15 使用者「這樣已經好幾次不同 能統一在雲端 再讓手機讀取嗎」→「改」:每平倉一筆就把今天全部虛擬成交(含淨輸贏)推 Worker(?vdone=push),
           手機 / 電腦版的虛擬明細、燈號、監控區已交易都讀這一份,不再自己算(fin = 收盤後最後一份)"""
        if self.dry: return
        try:
            L = [{**c, "net": vnet(c["side"], float(c["e"]), float(c["x"]), int(c["lots"]))} for c in list(self.closed) if not c.get("over")]
            r = http(worker_url("vdone=push"), {"d": self.day, "list": L, "fin": bool(fin)}, key=True)
            if not r.get("ok"): log("[虛擬明細] 推雲端被拒", r)
        except Exception as e: log("[虛擬明細] 推雲端失敗", repr(e))

    def push_review(self):
        """2026-11-07 14:32 使用者「檢討」A+B 的 A:收盤後電腦自己算今天的數字推 Worker(?review=push),App 資訊頁「檢討」顯示;重大發現(B)由每天的 Claude 排程寫"""
        net = vnet
        L = []; L7 = []; L6 = []   # 2026-11-09 觀察版判七另外算 → 15:28 判七解凍上線,和其他判別式一起算(L7 留空);2026-11-10 觀察版判六另外算(L6)
        for c in self.closed:
            if c.get("over"): continue
            L.append({**c, "net": net(c["side"], float(c["e"]), float(c["x"]), int(c["lots"]))})   # 2026-11-11 00:20 判六上線:和其他判別式一起算(L6 留空)
        byj = {}
        for c in L:
            b = byj.setdefault(c["rs"], {"n": 0, "w": 0, "net": 0}); b["n"] += 1; b["w"] += c["net"] > 0; b["net"] += c["net"]
        med = lambda a: sorted(a)[len(a) // 2] if a else None
        auto = {"n": len(L), "w": sum(c["net"] > 0 for c in L), "net": sum(c["net"] for c in L), "by": byj,
                "best": max(L, key=lambda c: c["net"]) if L else None, "worst": min(L, key=lambda c: c["net"]) if L else None,
                "list": sorted(L, key=lambda c: c["t"]),
                "drag": sorted({k.split("|")[1] for k in self.drag}), "lat_med": med(self.lat), "lat_max": max(self.lat) if self.lat else None,
                "round_med": med(self.rounds), "round_max": max(self.rounds) if self.rounds else None,
                "obs": {k_: {"n": len(v_), "w": sum(c["net"] > 0 for c in v_), "net": sum(c["net"] for c in v_), "list": sorted(v_, key=lambda c: c["t"])} for k_, v_ in (("判七", L7), ("判六", L6)) if v_}}
        if not L and not L7 and not L6 and not self.lat and not self.drag: log("[檢討] 今天沒有資料(收盤後才開?),不推"); return auto
        if self.dry: log("[檢討] " + json.dumps(auto, ensure_ascii=False)[:300]); return auto
        self.push_vdone(fin=True)
        try:
            r = http(worker_url("review=push"), {"d": self.day, "auto": auto}, key=True)
            log(f"[檢討] 推上去:{len(L)} 筆 {auto['net']:+,}" if r.get("ok") else f"[檢討] 被拒 {r}")
        except Exception as e: log("[檢討] 推不上去", repr(e))
        return auto

    def drag_check(self, bars, hm):
        """2026-11-07 11:55 使用者「做出 中級 提示」(定義:「進場後一直沒往對的方向走、帳面小虧、大盤反向走」= 可能拖到 13:20 的輸單):
           開著的部位進場滿 DRAG_MIN(60)分鐘後,只要「曾順向 < DRAG_MFE(0.3)%、現在帳面虧、名單平均反向走 ≥ DRAG_MKT(0.5)點」→ 推一則中級提示(kind lv2 = 手機「⚠⚠ 警告」那級的顏色 / 聲音)。
           只提示、不平倉(21 天回測提早平倉反而少賺);每筆部位只發一次。11/06 聯合再生 11:24 空 16.80 → 12:27 會發(實際 12:55 停損 −9,902)"""
        if CFG.get("DRAG_ON", "1") != "1" or not self.pos: return
        try: mins = int(CFG.get("DRAG_MIN", "60")); a_mfe = float(CFG.get("DRAG_MFE", "0.3")); a_mkt = float(CFG.get("DRAG_MKT", "0.5"))
        except Exception: mins, a_mfe, a_mkt = 60, 0.3, 0.5
        try: av = (json.loads((DATA / "mktavg.json").read_text(encoding="utf-8")) or {}).get(self.day) or {}
        except Exception: av = {}
        def mk(t):
            ks = [k for k in av if k <= t and av[k] is not None]
            return float(av[max(ks)]) if ks else None
        for (rs, code), p in list(self.pos.items()):
            k = f"{rs}|{code}|{p['t']}"
            if k in self.drag or p.get("over"): continue
            rows = sorted(r for r in (bars.get(code) or []) if p["t"] < r[0] <= hm)
            if len(rows) < mins: continue
            e = float(p["px"]); sd = p["side"]
            if not e: continue
            mfe = max(((e - r[3]) if sd < 0 else (r[2] - e)) / e * 100 for r in rows)
            cur = ((e - rows[-1][4]) if sd < 0 else (rows[-1][4] - e)) / e * 100
            m0, m1 = mk(p["t"]), mk(hm)
            if m0 is None or m1 is None: continue
            against = (m1 - m0) * (-sd)
            if mfe < a_mfe - 1e-9 and cur < 0 and against >= a_mkt - 1e-9:
                # 2026-11-07 13:10 使用者「123先出現 表示反手增加 再出現拖輸 可能就是要反攻了」→ 好:通知加「①②③ 什麼時候出現 / 幾分鐘前」(反方向的原始 ①②③,進場後第一次);
                #   21 天:①②③ 早於提示 ≥ 45 分 11 筆 9 輸、< 45 分 5 筆全贏(樣本小,先只顯示、不改規則)
                allr = sorted(bars.get(code) or []); i0 = next((q for q, r in enumerate(allr) if r[0] > p["t"]), None); t123 = None
                if i0 is not None:
                    for q in range(i0, len(allr)):
                        if allr[q][0] > hm: break
                        if self.raw123(allr, q, -sd): t123 = allr[q][0]; break
                def _mm(x): return int(x[:2]) * 60 + int(x[3:5])
                s123 = f"、①②③ {t123}({_mm(hm) - _mm(t123)} 分鐘前)" if t123 else "、沒有①②③"
                try:
                    with open(DATA / f"drag_{self.day}.jsonl", "a", encoding="utf-8") as f:   # 累積實盤紀錄(之後看 ①②③ 間隔準不準)
                        f.write(json.dumps({"d": self.day, "hm": hm, "rs": rs, "code": code, "t": p["t"], "px": e, "side": sd, "mfe": round(mfe, 3), "cur": round(cur, 3), "mkt": round(against, 3), "t123": t123}, ensure_ascii=False) + "\n")
                except Exception: pass
                nm = self.names.get(code, code); side = "做多" if sd > 0 else "做空"
                title = f"⚠⚠ 可能拖輸 {nm} {code}"
                body = (f"{hm} {side} {p['t']} 進 {e:.2f} · {p.get('lots') or max(1, int(self.cap // (e * 1000)))}張,進場 {len(rows)} 分鐘:曾順向只有 {mfe:.2f}%、現價 {rows[-1][4]:.2f} 帳面 {cur:+.2f}%、"
                        f"名單平均 {m0:+.2f} → {m1:+.2f}(反向 {against:+.2f} 點){s123} · {JUDGE[rs]} · 只提示不平倉,請自行判斷")
                it = {"d": self.day, "t": hm, "title": title, "body": body, "kind": "lv2", "key": f"drag|{code}|{JUDGE[rs]}|{p['t']}"}
                # 2026-11-07 使用者「這會不會影響通知速度」:提示改在背景執行緒推(Worker 慢 / 掛掉時最多會卡 ~66 秒,不能擋到下一分鐘的進場通知);推成功才記,下一輪存檔
                self.drag.add(k)
                def _send(it=it, k=k):
                    if not self.push(it): self.drag.discard(k)
                threading.Thread(target=_send, daemon=True).start()

    def push_entry(self, rs, code, tm, d, at):
        nm = self.names.get(code, code); j = JUDGE[rs]
        side = "做多" if d["side"] > 0 else "做空"; px = d["px"]
        tp = d.get("tp") or px * (1 + (float(self.envs[rs].get("T4TAKE", "9")) if rs == "四" else float(self.envs[rs].get("T5TAKE", "3")) if rs == "五" else float(self.envs[rs].get("T7TAKE", "5.5")) if rs == "七" else _f(self.envs[rs].get("TAKE"), TAKE)) / 100 * d["side"])   # 2026-10-31 22:48 停利 % 改讀私人庫參數(判三改 3%;原本寫死 2.5)   # 2026-10-31 判四不停利(9% 才停,通知要有停利價中繼才收)
        sl = d.get("sl") or (px * (1 - (float(self.envs[rs].get("STOPCAP", self.envs[rs].get("STOP", "4.5"))) / 100) * d["side"]))   # 判一 pts 停損價模擬器沒印,用上限 3% 當外圍(真正的停損由平倉通知來)
        try:   # 2026-11-11 14:57 使用者「缺口 一樣要跟剛剛設定的進出場範圍一樣」:通知上的停損價不超過昨收 ±6.5%(和中繼 / 元大條件單同一價)
            _y = float(self.prev.get(code) or 0); _mm = float(CFG.get("TRADE_MAX_MOVE_PCT", "6.5") or 0); _mm = _mm if 0 < _mm <= 6.5 else 6.5   # 鐵律 6.5%
            if _y > 0 and _mm > 0:
                import math as _m; _v = _y * (1 - d["side"] * _mm / 100); _t = 0.01 if _v < 10 else 0.05 if _v < 50 else 0.1 if _v < 100 else 0.5 if _v < 500 else 1 if _v < 1000 else 5
                _lim = round(_m.floor(_v / _t + 1e-6) * _t if d["side"] < 0 else _m.ceil(_v / _t - 1e-6) * _t, 2)
                sl = min(sl, _lim) if d["side"] < 0 else max(sl, _lim)
        except Exception: pass
        qty = lot_tier_qty(px)   # 鐵律 ⑤(2026-11-11 19:29):張數分級關不掉   # 2026-11-11 14:49 張數分級(和中繼一樣):≤ 70 元 3 張、≤ 150 元 2 張、其他 1 張;一張超過多空上限照舊「超額」
        # 2026-10-30 01:55 使用者「超過限額的股票 也要出現在訊息通知欄裡 只是註記“超額”不交易 給強制進場提供一個發揮的平台」:一張就超過多空上限 → 照推、張數改寫「超額」(中繼看到不自動進場)
        over = px * 1000 > self.cap
        title = f"{nm} {code} {side}{'強訊號' if d['strong'] else ''}進場 {px:.2f}"
        body = f"{tm} {d['text']} · 停利 {tp:.2f} 停損 {sl:.2f} · {'超額' if over else str(qty) + '張'} · {j}"
        if rs in ("六", "六3", "七"): body = f"{tm} {d['text']} · 停利 {tp:.2f} 停損 {sl:.2f} · {'超額' if over else str(qty) + '張'} · {d.get('note', '')} · {j}"   # 2026-11-10 觀察版判六一樣帶研究數字   # 2026-11-09 判七帶 12/06 驗證用的三個數字(15:28 解凍上線:照張數下單)
        it = {"d": self.day, "t": at, "title": title, "body": body, "kind": "long" if d["side"] > 0 else "short", "key": self.key_of(code, title, rs)}
        y = self.prev.get(code)
        if y: it["prev"] = y
        if self.push(it):
            try:   # 2026-11-07 收盤檢討:進場通知比訊號那根 K 收完晚幾秒
                n_ = now_tw(); self.lat.append(round((n_.hour * 3600 + n_.minute * 60 + n_.second) - (int(tm[:2]) * 3600 + int(tm[3:5]) * 60 + 60)))
            except Exception: pass
            self.pos[(rs, code)] = {"side": d["side"], "t": tm, "px": px, "lots": qty, "text": d["text"], "over": over, "tp": round(tp, 2), "sl": round(sl, 2), "ver": RULES.get("ver", "")}   # 2026-11-10 記停利 / 停損 / 規則版本(訊號因資料微調消失時續抱用)
            if rs == "四" and not over: self.j4.add(code)
            if rs == "五" and not over: self.j5.add(code)
            if rs == "六3" and not over: self.j63.add(code)
            return True
        return False

    def compute(self, only=None, stream=False):
        """only = {(判別式, 代碼)}:只算這些(2026-10-30 預判收盤確認用)"""
        jobs = []
        hm_now = self.upto_hm or ""
        # 2026-11-07 19:52 使用者「加速 程式運行跟進場訊息」:不可能再發進場、也沒持倉的不算 —— 強平過的股票;判四 / 判五今天檔數已滿、又不是已進過的股票
        #   (對帳那邊本來就不會發,算了也白算);ENGINE_SKIP_IDLE=0 全部照算
        skip_on = CFG.get("ENGINE_SKIP_IDLE", "1") == "1"
        full = {"四": len(self.j4) >= int(_f(self.envs.get("四", {}).get("T4MAX"), T4MAX)), "五": len(self.j5) >= getattr(self, "j5max", J5MAX), "六3": len(self.j63) >= getattr(self, "j63max", J63MAX)}
        pri = list(getattr(self, "rs_pri", None) or RS_PRIORITY)
        for rs in sorted(RS_ALL, key=lambda x: pri.index(x) if x in pri else 99):   # 2026-11-10 同一分鐘同一檔:勝率高的判別式先算、先發
            if rs not in self.envs: continue   # 2026-11-05 判五規則還沒到
            # 2026-11-06 加速:過了這個判別式最後進場時間 + 11 分鐘(10 分鐘內的進場還會補發),只算還開著虛擬部位的股票
            if rs == "六3" and hm_now and hm_now < "10:29" and not any(k_[0] == "六3" for k_ in self.pos): continue   # 2026-11-12 23:40 判六做空 10:30 才開始,之前不用算
            e_ = self.envs[rs]; last_ = e_.get("NOENTRYAFTER") or e_.get("T5TO") or e_.get("T4NOENTRY") or ""
            idle = bool(last_) and bool(hm_now) and hm_now > hm_add(last_, 11) and CFG.get("ENGINE_SKIP_IDLE", "1") == "1"
            for c in self.codes:
                if only is not None and (rs, c) not in only: continue
                if idle and (rs, c) not in self.pos: continue
                if skip_on and only is None and (rs, c) not in self.pos and (c in self.fclosed or (full.get(rs) and c not in (self.j4 if rs == "四" else self.j63 if rs == "六3" else self.j5))): continue
                # 2026-11-10 13:35(實際時間)使用者「修」:今天還沒有任何成交(元大第一根 K 還沒出來,例:11/10 中華電 2412、3605 09:02 才第一筆)的股票先不算 ——
                #   原本模擬器找不到今天的 K 會退到 Yahoo 備用檔(引擎沒有)→ FileNotFoundError,log 出現假的「錯誤 14/462」;沒成交本來就不會有訊號
                if self.last_hm and c not in self.last_hm and (rs, c) not in self.pos:
                    if c not in self.no_bar_note: self.no_bar_note.add(c); log(f"{c} 今天還沒有成交(沒有 K)→ 先不算,有 K 了再算")
                    continue
                env = dict(self.envs[rs])
                if not self.canshort.get(c, False): env["CANSHORT"] = "0"   # 2026-10-29 稽核 #3:不能先賣 → 空單只記錄
                jobs.append((c, self.names.get(c, c), rs, env, self.day))
        if skip_on: jobs.sort(key=lambda x: 0 if (x[2], x[0]) in self.pos else 1)   # 2026-11-07 開著的部位先算(停損 / 停利早一點發);其他照原本順序(新進場的先後不變)
        if stream:   # 2026-11-06 算完一檔就交回(照原本順序,對帳順序和以前一樣)
            return (self.pool.imap(_sim_one, jobs, chunksize=1) if jobs else iter(())), len(jobs)
        t0 = time.time(); res = self.pool.map(_sim_one, jobs, chunksize=4) if jobs else []
        return res, time.time() - t0, len(jobs)

    def apply(self, res, upto, allow_new=True, allow_phantom=True, rs_block=None):
        """對帳(2026-10-29 稽核 #2 / #7):每檔每判,拿模擬器現在的交易清單和已推的部位對;
           allow_new = 這輪可以發新進場;allow_phantom = 這輪資料完整、可以判定「訊號消失」;rs_block = {判別式: 原因} 這輪不發新進場"""
        cutoff = hm_add(upto, -10); n_ev = 0; rs_block = rs_block or {}
        for r in res:
            n_ev += self.apply_one(r, upto, cutoff, allow_new, allow_phantom, rs_block)
        self.save()
        return n_ev

    def apply_one(self, r, upto, cutoff, allow_new, allow_phantom, rs_block):
        """2026-11-06 拆出來:一檔一判的對帳(apply 的迴圈內容原封不動;continue → return),好讓算完一檔就先對帳發通知"""
        code, rs, out, err = r; n = 0
        if err: log(f"模擬器錯誤 {JUDGE[rs]} {code}: {err[:160]}"); return n
        ev = parse_events(out); n = sum(1 for tm, _, _ in ev if tm < upto)
        trades, open_tm = sim_trades(ev, upto); k = (rs, code)
        try: self.note_blocked(rs, code, out, upto)   # 2026-11-12 20:29 使用者「再來」(物盡其用 4):被鐵律 ⑦ / 大盤條件擋掉、在等回 Ka~Kb 的訊號記下來(中繼備份到雲端)
        except Exception: pass
        fc = code in self.fclosed   # 2026-11-02 強平過:部位已由中繼平掉,不發新進場
        # 2026-11-04 使用者「我強平過的 當沖最後就不用顯示未平倉的訊息 看盤版就是照程式設定來 該怎麼平倉就怎麼平倉」(一進一出):
        #   還開著的虛擬部位照算到程式自己的出場(停利 / 停損 / 13:20…),平倉卡內容加「· 已強平」—— 永久版 App 藏、不響,中繼不理;Worker 給看盤版時拿掉這個字
        if fc:
            p = self.pos.get(k)
            if not p:
                if open_tm: self.emitted.add((rs, code, open_tm))
                return n
            if not p.get("fc"): p["fc"] = 1; log(f"{JUDGE[rs]} {code} 已強平 → 虛擬部位照算到程式出場(只給看盤版)")
        p = self.pos.get(k)
        if p and (rs, code, p["t"]) in self.cloud_out:   # 雲端已有這個部位的平倉(手機發的 / 重開前發的)
            log(f"{JUDGE[rs]} {code} {p['t']} 雲端已有平倉通知 → 結束部位"); self.pos.pop(k); p = None
        if p:
            tr = trades.get(p["t"])
            if tr is None or tr[0]["side"] != p["side"]:
                have = self.last_hm.get(code, "") >= hm_add(p["t"], 1) and p["t"] < upto and "無資料" not in out   # 這檔今天的 K 沒到 / 不夠 → 不是訊號消失,是資料缺
                # 2026-11-10 12:55(實際時間)使用者「要改善修正」→ 選「不平倉,照停利停損走」(富喬 11:30 判四:進場價離 VWAP −4.06% 剛好過門檻,
                #   下一分鐘元大 K 補了幾筆成交、VWAP 變 −4.09% → 訊號消失被平倉 −287):規則沒改版(進場時和現在同一版)= 資料微調 → 不推平倉,
                #   部位續抱,照進場時的停利 / 停損 / 13:20 出場(這裡用最新價判斷、推平倉通知;中繼本來也自己盯停利停損、條件單)。
                #   規則改版造成的消失(群創 09:47 那種)照使用者 09:50「以後就照新規定走」→ 照舊推平倉
                same_rule = bool(p.get("ver")) and p.get("ver") == RULES.get("ver") and p.get("tp") and p.get("sl")
                if allow_phantom and have and same_rule:
                    if not p.get("orphan"): p["orphan"] = 1; log(f"!! {JUDGE[rs]} {code} {p['t']} 已推的進場在重算裡不見了,但規則沒改版(資料微調)→ 不平倉,照停利 {p['tp']} / 停損 {p['sl']} / 13:20 續抱")
                    px_ = self.px.get(code, p["px"]); sd = p["side"]
                    hit = "停損" if (px_ - p["sl"]) * sd <= 0 else "停利" if (px_ - p["tp"]) * sd >= 0 else ("13:20 強制平倉" if upto >= "13:20" else "")
                    if hit and self.push_exit(rs, code, p, upto, px_, hit, upto): self.pos.pop(k)
                elif allow_phantom and have:
                    log(f"!! {JUDGE[rs]} {code} {p['t']} 已推的進場在重算裡不見了 → 推平倉")
                    if self.push_exit(rs, code, p, upto, self.px.get(code, p["px"]), "", upto, phantom=True): self.pos.pop(k)
                elif (k, p["t"]) not in self.held_note:
                    self.held_note.add((k, p["t"])); log(f"{JUDGE[rs]} {code} {p['t']} 這輪重算看不到它,但 K 不完整 → 先不判定")
            elif tr[1] is not None:   # 2026-10-29 已推的部位不管多舊都要推平倉(原本出場時間早於 10 分鐘就默默丟掉,中繼的部位沒人平)
                if self.push_exit(rs, code, p, tr[1][0], tr[1][1]["px"], tr[1][1]["why"], upto): self.pos.pop(k)
        if fc:   # 強平過的股票不發新進場(和原本一樣)
            if open_tm and k not in self.pos: self.emitted.add((rs, code, open_tm))
            return n
        if k in self.pos or open_tm is None: return n
        ek = (rs, code, open_tm); d = trades[open_tm][0]
        if ek in self.emitted: return n
        ci = self.cloud_in.get(ek)
        if ek in self.cloud_out:
            self.emitted.add(ek); return n
        if ci and (ci.get("kind") == "long") == (d["side"] > 0):   # 雲端已經有(手機 / 上一個 engine)→ 接回來管平倉,不重發
            mq = re.search(r"·\s*(\d+)張", ci.get("body", "")); mp = re.search(r"([\d.]+)\s*$", ci.get("title", ""))
            px = float(mp.group(1)) if mp else d["px"]
            self.pos[k] = {"side": d["side"], "t": open_tm, "px": px, "lots": int(mq.group(1)) if mq else max(1, int(self.cap // (px * 1000))), "text": d["text"]}
            self.emitted.add(ek); log(f"{JUDGE[rs]} {code} {open_tm} 雲端已有這筆進場 → 接回、不重發"); return n
        if open_tm < cutoff:
            self.emitted.add(ek); log(f"{JUDGE[rs]} {code} {open_tm} 進場已超過 10 分鐘 → 不發"); return n
        if code in self.no_new: why = "今天已不合格(只算平倉)"
        elif not allow_new: why = "這輪資料不完整"
        elif rs in rs_block: why = rs_block[rs]
        elif rs == "四" and code not in self.j4 and len(self.j4) >= int(_f(self.envs.get("四", {}).get("T4MAX"), T4MAX)):   # 2026-10-31 判四一天最多 T4MAX 檔(到了就不再發,記 emitted)
            self.emitted.add(ek); log(f"判四 {code} {open_tm} 今天判四已 {len(self.j4)} 檔(到上限)→ 不發"); return n
        elif rs == "五" and code not in self.j5 and len(self.j5) >= getattr(self, "j5max", J5MAX):   # 2026-11-05 判五一天最多 J5MAX 筆
            self.emitted.add(ek); log(f"判五 {code} {open_tm} 今天判五已 {len(self.j5)} 檔(到上限)→ 不發"); return n
        elif rs == "六3" and code not in self.j63 and len(self.j63) >= getattr(self, "j63max", J63MAX):   # 2026-11-12 23:40 判六做空一天最多 J63MAX 檔
            self.emitted.add(ek); log(f"判六(做空){code} {open_tm} 今天判六做空已 {len(self.j63)} 檔(到上限)→ 不發"); return n
        elif rs in ("六", "六3") and (("六3" if rs == "六" else "六"), code) in self.pos:   # 2026-11-12 23:40 判六做多 / 做空同一檔同時只留一筆
            self.emitted.add(ek); log(f"判六 {code} {open_tm} 這檔判六另一個方向的部位還開著 → 不發"); return n
        elif not self.tx_ok(rs, code, open_tm, d["side"]):   # 2026-11-13 22:30 ⑧ 台指期同步進場(23:32 改只看方向):台指期 60 分鐘往不利方向 → 這筆不做(記 emitted,不再發)
            self.emitted.add(ek); return n
        else: why = None
        if why:
            log(f"{JUDGE[rs]} {code} {open_tm} 進場先不發:{why}"); return n   # 不記 emitted:10 分鐘內恢復就照發
        if self.push_entry(rs, code, open_tm, d, upto): self.emitted.add(ek)
        return n

    def txf_map(self):
        """⑧ 中繼(yuanta_bridge.py)每分鐘寫的台指期近月 1 分 K:HERE/kline_futures/TXF/<日期>.csv → {HH:MM: 收盤};20 秒快取"""
        c = self.__dict__.get("_txf_c")
        if c and time.time() - c[0] < 20 and c[1] == self.day: return c[2]
        m = {}
        try:
            with open(HERE / "kline_futures" / "TXF" / f"{self.day}.csv", encoding="utf-8") as fh:
                for r in csv.DictReader(fh):
                    if r.get("date") == self.day and "08:45" <= r["time"] <= "13:45": m[r["time"]] = float(r["close"])
        except Exception: pass
        self._txf_c = (time.time(), self.day, m); return m

    def ix_map(self):
        """⑧ 備援 3:Worker ?ixbars=今天(每分鐘加權指數收盤,09:00 起);20 秒快取"""
        c = self.__dict__.get("_ix_c")
        if c and time.time() - c[0] < 20 and c[1] == self.day: return c[2]
        m = {}
        try: m = {k: float(v) for k, v in ((http(worker_url(f"ixbars={self.day}"), timeout=6) or {}).get("m") or {}).items()}
        except Exception: m = (c[2] if c and c[1] == self.day else {})
        self._ix_c = (time.time(), self.day, m); return m

    def stock_closes(self, code):
        """⑧ 這檔今天 09:00~13:30 的 1 分 K 收盤(模擬器同一份 kline_yuanta/<代號>/<週>.csv)"""
        try:
            d = dt.date.fromisoformat(self.day); y, w, _ = d.isocalendar(); rows = {}
            for sub in ("kline_yuanta", "kline"):
                f = DATA / sub / code / f"{y}-W{w:02d}.csv"
                if not f.exists(): continue
                with open(f, encoding="utf-8") as fh:
                    for r in csv.DictReader(fh):
                        if r.get("date") == self.day and "09:00" <= r["time"] <= "13:30": rows.setdefault(r["time"], float(r["close"]))
                if rows: break
            return sorted(rows.items())
        except Exception: return []

    def tx_ok(self, rs, code, tm, side):
        """⑧ 台指期同步進場(2026-11-13 22:17 使用者「解鎖 改 全部套用」;23:32「只看台指期方向 解鎖改」):rs_env 有 TXSYNC 才看;判斷用私人庫 rs_env.txsync(和模擬器同一份)。
           這天完全沒有台指期 → 先不看(照舊進場)+ 推一次警告;其他算不出來 / 不合 → 不做"""
        c = self.envs.get(rs, {}).get("TXSYNC"); fn = getattr(self, "txsync_fn", None)
        if not c or not fn: return True
        try:
            txf = self.txf_map(); frm = None
            if not txf:   # 2026-11-13 23:58 ⑧ 備援 3:元大 / 期交所台指期都沒有 → 用 Worker 記的加權指數(09:00 起)
                txf = self.ix_map(); frm = "09:00" if txf else None
            if not txf:
                if not self.__dict__.get("_txf_warn") and now_tw().strftime("%H:%M") >= "09:05":
                    self._txf_warn = True; log("!! ⑧ 今天沒有台指期 1 分 K(中繼沒收到)→ 台指期同步進場先不看")
                    try: http(worker_url("log=add"), {"d": self.day, "t": now_tw().strftime("%H:%M"), "title": "⚠ 沒有台指期資料,鐵律 ⑧ 暫停", "body": "中繼沒收到台指期逐筆,台指期同步進場今天先不看(照舊進場) · 元大中繼", "kind": "pc", "key": "txf|missing"}, key=True)
                    except Exception: pass
                return True
            _p = c.split(":"); n_ = int(float(_p[0])); r_ = float(_p[1]) if len(_p) > 1 and _p[1] else None   # 「60」= 只看台指期方向(2026-11-13 23:32 使用者「只看台指期方向 解鎖改」)
            mvs = float(self.envs.get(rs, {}).get("TXMVS") or 0) or None   # 2026-11-14 12:56 判二(使用者「解鎖 改 0.25%」):做空時台指期 60 分要跌 ≥ TXMVS%(rs_env J2 TXMVS=0.25)
            ok, why = fn(side, tm, self.stock_closes(code), txf, n_, r_, frm, mvs=mvs) if frm else fn(side, tm, self.stock_closes(code), txf, n_, r_, mvs=mvs)
            if frm: why = why.replace("台指期", "加權指數(台指期沒資料)")
            if not ok: log(f"{JUDGE[rs]} {code} {tm} {why} → 不發")
            return ok
        except Exception as e:
            log(f"⑧ 檢查失敗(照舊進場)", e); return True

    RE_BLK = re.compile(r"^\s*(\d\d:\d\d) .*(不在 Ka~Kb|沒回 Ka~Kb|Ka~Kb 沒回|大盤寬度|不做多|延遲進場取消|等回 Ka~Kb|回到 Ka~Kb)")
    def note_blocked(self, rs, code, out, upto):
        """被擋 / 等待中的訊號(模擬器那行原文)→ engine_data/blocked_<日期>.jsonl,同一行只記一次"""
        seen = self.__dict__.get("_blk")
        if seen is None:   # 引擎重開:先讀今天已記過的,避免重複
            seen = self._blk = set()
            try:
                for x in (DATA / f"blocked_{self.day}.jsonl").read_text(encoding="utf-8").splitlines():
                    try: o = json.loads(x); seen.add((o.get("rsk"), o.get("code"), o.get("line")))
                    except Exception: pass
            except Exception: pass
        new = []
        for line in out.splitlines():
            m = self.RE_BLK.match(line)
            if not m or m.group(1) >= upto: continue
            key = (rs, code, line.strip())
            if key in seen: continue
            seen.add(key); new.append({"t": m.group(1), "rs": JUDGE.get(rs, rs), "rsk": rs, "code": code, "name": self.names.get(code, ""), "line": line.strip()})
        if new:
            with open(DATA / f"blocked_{self.day}.jsonl", "a", encoding="utf-8") as f:
                for x in new: f.write(json.dumps(x, ensure_ascii=False) + "\n")

    def candidates(self, res, upto):
        """2026-10-30 預判:模擬器結果裡 upto 前一分鐘(還在形成、假設用現價收)會出新進場的 (判別式, 代碼);已推 / 已處理過的不算"""
        sig = hm_add(upto, -1); out = set()
        for code, rs, out_, err in res:
            if err: continue
            trades, open_tm = sim_trades(parse_events(out_), upto)
            if open_tm == sig and (rs, code) not in self.pos and (rs, code, sig) not in self.emitted: out.add((rs, code))
        return out

    def step(self, upto, **kw):
        """跑到 upto(不含)那分鐘的 K;發 upto 前 10 分鐘內的新事件(回放用;盤中用 compute + apply)"""
        if CFG.get("ENGINE_REPLAY_OLD") == "1":   # 驗證用:舊的整輪算完再對帳
            res, dtm, njobs = self.compute()
            errs = sum(1 for r in res if r[3])
            n_ev = self.apply(res, upto, **kw)
            return dtm, errs, n_ev
        # 2026-11-06 回放和盤中走同一條路:算完一檔就對帳、過了進場時段只算持倉
        self.upto_hm = upto; t0 = time.time(); it, njobs = self.compute(stream=True); errs = 0; n_ev = 0
        cutoff = hm_add(upto, -10); an = kw.get("allow_new", True); ap = kw.get("allow_phantom", True); rb = kw.get("rs_block") or {}
        for r in it:
            errs += bool(r[3]); n_ev += self.apply_one(r, upto, cutoff, an, ap, rb)
        self.save()
        return time.time() - t0, errs, n_ev

    def heartbeat(self, on, msg="", flags=()):
        if self.dry or not CFG.get("PUSH_KEY"): return
        fl = "".join(flags)
        try: http(worker_url("engine=push"), {"d": self.day, "hm": now_tw().strftime("%H:%M"), "on": 1 if on else 0, "n": len(self.codes),
                                               "msg": (fl + " " if fl else "") + msg + f" 規則{RULES['ver']}", "ver": RULES["ver"], "stale": 1 if RULES["stale"] else 0}, key=True, timeout=10)
        except Exception as e: log("心跳推不上", e)


def acquire_lock():
    """2026-10-29 稽核 #7:同一個資料夾只能有一個 engine(兩個一起跑 → 每則通知發兩次、K 檔互相蓋)。鎖跟著行程,當掉自動放開"""
    DATA.mkdir(parents=True, exist_ok=True)
    f = open(DATA / "engine.lock", "a+")
    try:
        if os.name == "nt":
            import msvcrt; f.seek(0); msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl; fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log(f"!! 已經有另一個 engine 在用 {DATA}(engine.lock 被鎖)→ 這個不跑"); raise SystemExit(2)
    return f


def live():
    lockf = acquire_lock()
    day = now_tw().strftime("%Y-%m-%d")
    cap = cloud_cap(int(CFG.get("ENGINE_CAP", "250000")))   # 2026-11-02 跟 5861 主導機
    j = load_pool(day)
    try:   # 先看狀態檔裡今天通知過哪些股票(和手機一樣不受篩選,平倉要靠它)
        sj = json.loads((DATA / f"engine_state_{day}.json").read_text(encoding="utf-8"))
        held = {k.split("|")[1] for k in sj.get("pos", {})}; noted = {x[1] for x in sj.get("emitted", [])} | {x[1] for x in sj.get("seen", []) if len(x) == 4}
    except Exception: held, noted = set(), set()
    uni = build_universe(j, day, cap, held, noted)
    prep_day(day, uni["codes"])
    eng = Engine(day, uni)
    eng.sync_cloud()
    log(f"公式在電腦:{len(eng.codes)} 檔 × 判一二三四,每分鐘重算;名單 {uni['stamp']},篩掉 {uni['why']};規則 {RULES['ver']};心跳 ?engine")
    log(set_desc(j, cap))
    err_max = float(CFG.get("ENGINE_ERR_MAX", "0.2")); rules_every = float(CFG.get("ENGINE_RULES_MIN", "30")) * 60; bar_wait = float(CFG.get("ENGINE_BAR_WAIT", "15"))
    done = set(); pool_chk = time.time(); pool_906 = False; late_n = 0; br = {"ok_at": 0.0, "last": ""}
    # 2026-10-30 01:40 使用者「能夠預判嗎 … 股價已經來到判一二三的臨界點啟動預判」→「做法一」:每分鐘第 ENGINE_PRE_SEC(45)秒先用本機 K「假設這根用現價收」全部算一遍,
    #   挑出快要成立的(候選);下一分鐘第 0 秒這根真的收了,只重算候選(本機 K,不等 Worker),條件還成立就馬上推(本機檔 → 中繼 1 秒內送單)。
    #   判斷依據還是收盤後的 K(和回測一樣);第 4 秒的整輪照舊(已推的不會重發,沒趕上的照發)。ENGINE_PRE=0 關掉
    # 2026-11-06 13:14 使用者「那把預判拿掉 浪費時間」:一輪要 ~53 秒,第 45 秒開始的預判算完已過下一分鐘第 4 秒 → 快速確認幾乎跑不到
    #   (11/02~11/06 約 960 次預判只提早推出 4 則,11/04 起 0 則),還連續搶走整輪(11/06 華經 10:22 訊號連 3 輪預判,10:26:35 才通知)→ 預設關;ENGINE_PRE=1 可再開
    pre_on = CFG.get("ENGINE_PRE", "1") == "1"; pre_sec = int(CFG.get("ENGINE_PRE_SEC", "45")); pre_max = float(CFG.get("ENGINE_PRE_MAXSEC", "15"))   # 2026-11-08 20:28 預設改回開(有 15 秒保險)
    try: start_sec = int(CFG.get("ENGINE_START_SEC", "2"))
    except Exception: start_sec = 2
    pre = {"for": "", "cand": set(), "done": set(), "fast": set(), "ok": False, "rs_block": {}}
    try:
        while True:
            n = now_tw(); hm = n.strftime("%H:%M")
            if hm >= "13:32":
                try: eng.push_review()   # 2026-11-07 收盤檢討(A)
                except Exception as e: log("[檢討] 失敗", repr(e))
                break
            # 2026-11-08 20:28 使用者「改」:預判重新打開(ENGINE_PRE=1),加保險 —— 最近 3 輪都在 ENGINE_PRE_MAXSEC(15)秒內算完才做;慢了就自動不做,照一般整輪跑(不再像 11/06 一輪 53 秒時插隊)
            pre_fast = len(eng.rounds) >= 3 and max(eng.rounds[-3:]) <= pre_max and not pre.get("off")
            if pre_on and pre_fast and "09:00" <= hm < "13:24" and n.second >= pre_sec and hm not in pre["done"] and pre["ok"]:
                pre["done"].add(hm); t0 = time.time()
                try:
                    lb_ = local_bars()
                    if lb_:
                        nxt = hm_add(hm, 1); bs, fm = pre_frame(lb_, nxt, set(eng.codes), pre.get("base"))
                        write_today(day, bs, upto=nxt, forming=fm)
                        res_, dtm_, nj_ = eng.compute()
                        pre["for"], pre["cand"] = nxt, eng.candidates(res_, nxt)
                        log(f"預判 {hm} 這根(假設現價收):{len(pre['cand'])} 個候選 {sorted(JUDGE[r] + c for r, c in pre['cand'])},{time.time() - t0:.1f} 秒")
                        if time.time() - t0 > pre_max:   # 2026-11-08 20:30 使用者「改」:預判自己超過 15 秒 → 今天不再預判(不讓它拖到下一分鐘的整輪)
                            pre["off"] = True; pre["cand"] = set(); log(f"!! 預判 {time.time() - t0:.1f} 秒超過 {pre_max:.0f} 秒 → 今天不再預判,照一般整輪跑")
                except Exception as e: log("預判失敗", repr(e))
                continue
            if pre_on and pre["cand"] and hm == pre["for"] and hm not in pre["fast"] and n.second < 4:
                pre["fast"].add(hm); t0 = time.time(); cand = pre["cand"]; pre["cand"] = set()
                try:
                    lb_ = local_bars()
                    if lb_:
                        bs, fm = pre_frame(lb_, hm, {c for _, c in cand}, pre.get("base"))
                        write_today(day, bs, upto=hm, forming=fm)
                        if any(r == "二" for r, _ in cand): write_breadth(day)
                        res_, dtm_, nj_ = eng.compute(only=cand)
                        nb = len(eng.sent); eng.apply(res_, hm, allow_new=True, allow_phantom=False, rs_block=pre["rs_block"])
                        log(f"預判收盤確認 {hm}:候選 {len(cand)} 個,推出 {len(eng.sent) - nb} 則,{time.time() - t0:.1f} 秒")
                except Exception as e: log("預判確認失敗", repr(e))
                continue
            if hm in ("08:50", "08:58") and ("cfg" + hm) not in done:   # 2026-11-07 19:32 使用者「開盤中不用追蹤主導機設定 因為開盤中 設定是封閉的」:
                done.add("cfg" + hm)                                       #   開盤前 08:50、08:58 各讀一次主導機設定(最後一次為準),選股設定變了就重選監控股票;09:00 後不再讀
                try:
                    keys = ("hi", "lo", "amp", "cnt", "lots", "bars", "days"); old = {k: CLOUD_SET.get(k) for k in keys}
                    c2 = cloud_cap(eng.cap)
                    if c2 != eng.cap: log(f"多空上限 {eng.cap:,} → {c2:,}(5861 主導機)"); eng.cap = cap = c2
                    if {k: CLOUD_SET.get(k) for k in keys} != old:
                        held, noted = eng.carried(); nu = build_universe(eng.uni.get("j") or j, day, cap, held, noted, eng.uni.get("src"))
                        add = [c for c in nu["codes"] if c not in eng.codes]
                        if add: prep_day(day, add)
                        log(f"主導機選股設定改了({old} → { {k: CLOUD_SET.get(k) for k in keys} }):重選 {len(nu['codes'])} 檔,新增 {len(add)} 檔")
                        log(set_desc(eng.uni.get("j") or j, cap))
                        eng.set_universe(nu)
                except Exception as e: log("開盤前讀主導機設定失敗", repr(e))
            if hm < "09:01" or n.second < start_sec or hm in done:   # 2026-11-07 中繼換分鐘第 1 秒就推 K → 開算從第 4 秒提早到第 2 秒(ENGINE_START_SEC;下面還是會等 hm-1 那根到齊)
                time.sleep(0.2); continue   # 2026-11-07 等開算 1 → 0.2 秒(第 2 秒準時開)
            done.add(hm)
            c2 = cloud_cap(eng.cap) if hm < "09:00" else eng.cap   # 2026-11-07 19:32 使用者「開盤中不用追蹤主導機設定」:原 09:03 前 → 09:00 前(開盤前最後一次為準)   # 2026-11-02 跟 5861 主導機的多空上限(通知裡的張數);盤中設定禁改 → 只在開盤頭兩分鐘再確認
            if c2 != eng.cap: log(f"多空上限 {eng.cap:,} → {c2:,}(5861 主導機)"); eng.cap = c2
            try:
                flags = []; off = []
                # 2026-11-06 名單外股票:中繼補好 K 寫 extra_codes.json → 這一輪就加進來(不等 5 分鐘重讀名單)
                if extra_stamp() != eng.uni.get("xstamp", ""):
                    try:
                        held, noted = eng.carried(); nu = build_universe(eng.uni.get("j") or {"pool": list(eng.uni["src"].values())}, day, cap, held, noted, eng.uni["src"])
                        add = [c for c in nu["codes"] if c not in eng.codes]
                        if add: log(f"名單外股票加入:{add}(中繼已補 K)")
                        eng.set_universe(nu)
                    except Exception as e: log("名單外股票加入失敗", repr(e))
                # 2026-11-07 使用者「看看還有什麼會拖慢速度的」:寬度 / 名單平均(Worker ?breadth)和雲端通知對帳(?log=get)原本排在等到 K 之後一個一個做 → 一開始就在背景同時做,開算前等它們做完
                _bg = {}
                def _bg_br():
                    try: _bg["br"] = write_breadth(day)
                    except Exception as e: _bg["br"] = (False, 0, ""); log("寬度背景抓失敗", repr(e))
                def _bg_sync():
                    try: eng.sync_cloud()
                    except Exception as e: log("雲端通知對帳失敗", repr(e))
                _ths = [threading.Thread(target=_bg_br, daemon=True), threading.Thread(target=_bg_sync, daemon=True)]
                for _t in _ths: _t.start()
                # 等 hm-1 那根上來(稽核 #6)
                want = hm_add(hm, -1); t_end = time.time() + bar_wait
                while True:
                    bars, last = today_bars_fast(day); lb = latest_bar(bars, last)   # 2026-11-07 只抓最近幾分鐘
                    if lb >= want or time.time() >= t_end: break
                    time.sleep(0.5)   # 2026-11-06 3 → 1 秒;2026-11-07 → 0.5 秒
                forming = live_forming(eng.codes, eng.mkts, hm, bars)
                write_today(day, bars, upto=hm, forming=forming)
                eng.last_hm = {}
                for c, rows in bars.items():
                    if rows: r = max(rows); eng.px[c] = r[4]; eng.last_hm[c] = r[0]
                for c, r in forming.items(): eng.px[c] = r[4]; eng.last_hm[c] = max(eng.last_hm.get(c, ""), r[0])
                try: eng.apply_refs()   # 2026-11-10 每分鐘對一次昨收(中繼的元大參考價檔有新的就套上)
                except Exception as e: log("昨收對照失敗", repr(e))
                # 寬度每分鐘(稽核 #1)
                _ths[0].join(35); ok, nbr, blast = _bg.get("br", (False, 0, ""))
                if ok: br.update(ok_at=time.time(), last=blast)
                rs_block = {}
                if not br["last"] or time.time() - br["ok_at"] > 180 or (hm < "13:26" and br["last"] < hm_add(hm, -5)):
                    rs_block["二"] = f"大盤寬度{'沒有' if not br['last'] else '過期(到 ' + br['last'] + ')'}"; flags.append("⚠寬度")
                late = bool(lb < want) and hm < "13:26"   # 13:25 起收盤集合競價本來就沒有 K
                stale_bars = (not lb or lb < hm_add(hm, -3)) and hm < "13:26"
                late_n = late_n + 1 if late else 0
                if late: flags.append("K晚到")
                if RULES["stale"]: flags.append("⚠規則舊")
                if not eng.codes: off.append("名單是空的")
                if stale_bars: off.append(f"K 只到 {lb or '(沒有)'}")
                if late_n >= 3: off.append(f"K 連續 {late_n} 分鐘晚到")
                _ths[1].join(15)
                # 2026-11-06 13:24 使用者「還有什麼可以再更快的」:算完一檔就對帳發通知,不等整輪(順序和以前一樣)
                eng.upto_hm = hm
                t0c = time.time(); it, njobs = eng.compute(stream=True); res = []; errs = 0; n_ev = 0
                cutoff = hm_add(hm, -10); ok0 = not off and not late
                for r in it:
                    res.append(r); errs += bool(r[3])
                    ok = ok0 and not (njobs and errs / njobs > err_max)
                    n_ev += eng.apply_one(r, hm, cutoff, ok, ok, rs_block)
                eng.save(); dtm = time.time() - t0c
                eng.rounds.append(round(dtm, 1))
                try: eng.drag_check(bars, hm)   # 2026-11-07 可能拖輸提示(中級)
                except Exception as e: log("可能拖輸檢查出錯", repr(e))
                if njobs and errs / njobs > err_max: off.append(f"模擬器錯誤 {errs}/{njobs}")
                healthy = not off
                pre["ok"] = healthy and not late and bool(bars); pre["rs_block"] = dict(rs_block); pre["base"] = bars   # 2026-10-30 預判只在上一輪健康時做,判二沒寬度那種封鎖也照帶
                if off: log(f"!! {hm} 心跳送 on=0(手機接手):{';'.join(off)}")
                eng.heartbeat(healthy, f"{hm} {dtm:.0f}s {n_ev} 事件" + (" " + ";".join(off) if off else ""), flags)
                log(f"{hm} 算完 {dtm:.1f} 秒,K 到 {lb},事件 {n_ev},錯誤 {errs}/{njobs},開著 {len(eng.pos)}{' ' + ' '.join(flags) if flags else ''}")
                # 2026-11-06 加速:規則重抓 / 名單重讀移到算完之後(原本在算之前,碰到的那一輪會晚幾秒)
                # 規則每 30 分鐘重抓(稽核 #5)
                if time.time() - RULES["at"] >= rules_every:
                    try:
                        if fetch_rules(): eng.reload_envs()
                    except SystemExit: raise
                    except Exception as e: RULES.update(stale=True, why=repr(e), at=time.time()); log("!!!!!!!! 規則重抓出錯", repr(e))
                # 名單:每 5 分鐘看雲端;09:06 後一定重讀一次(資格複檢後)(稽核 #8)
                if time.time() - pool_chk >= 300 or (hm >= "09:06" and not pool_906):
                    pool_chk = time.time(); first906 = hm >= "09:06" and not pool_906; pool_906 = pool_906 or hm >= "09:06"
                    nj = fetch_pool_json(day)
                    if nj is not None:
                        stamp = f"{nj.get('generated', '')}|{nj.get('elig_checked_at', '')}"
                        if stamp != eng.uni["stamp"] or first906:
                            held, noted = eng.carried()
                            nu = build_universe(nj, day, cap, held, noted, eng.uni["src"])
                            new = [c for c in nu["codes"] if c not in eng.codes]
                            if new: prep_k(day, new)
                            fetch_raw("pool.json", DATA / "pool.json")
                            if stamp != eng.uni["stamp"] or set(nu["codes"]) != set(eng.codes) or nu["canshort"] != eng.canshort:
                                log(f"名單更新 {eng.uni['stamp']} → {stamp}:{len(eng.codes)} → {len(nu['codes'])} 檔,新增 {new},拿掉 {[c for c in eng.codes if c not in nu['codes']]},只算平倉 {sorted(nu['no_new'])},篩掉 {nu['why']}")
                            eng.set_universe(nu)
                    else:
                        log("名單重讀:雲端 pool.json 抓不到,沿用手上的")
            except SystemExit: raise
            except Exception as e:
                log("這一分鐘失敗", repr(e)); eng.heartbeat(False, "錯誤 " + repr(e)[:80])
    except KeyboardInterrupt:
        log("手動中斷")
    finally:
        eng.heartbeat(False, "收工"); eng.pool.terminate(); log("收工")
        try: lockf.close()
        except Exception: pass


def replay(day, only=None, dry=True):
    lockf = acquire_lock()
    j = load_pool(day, fresh=False)
    cap = int(CFG.get("ENGINE_CAP", "250000"))
    uni = build_universe(j, day, cap)
    if only:   # 指定代碼:不管篩選,照算(對照用)
        pool = {str(s["code"]): s for s in j.get("pool", [])}
        uni["codes"] = [c for c in only if c]
        for c in uni["codes"]:
            s = pool.get(c, {}); uni["names"][c] = s.get("name", ""); uni["prev"][c] = s.get("close") if str(s.get("date") or j.get("date", "")) < day else s.get("prev")
            uni["mkts"][c] = "otc" if str(s.get("market", "")).lower() in ("otc", "tpex") else "tse"; uni["canshort"][c] = s.get("daytrade") == 1 and s.get("short_first") == 1
        uni["no_new"] = set()
    prep_day(day, uni["codes"])
    bars, last = today_bars(day); write_breadth(day)
    eng = Engine(day, uni, dry=dry, state=not dry)   # 2026-10-29 --dry 不讀不寫狀態檔(不會蓋到盤中那份)
    log(f"回放 {day}:{len(eng.codes)} 檔,K 到 {last},規則 {RULES['ver']}")
    mins = sorted({hm for rows in bars.values() for hm, *_ in rows})
    by = {c: {r[0]: r for r in rows} for c, rows in bars.items()}
    try:
      for hm0 in mins:
          hm = hm_add(hm0, 1)   # 這根走完的下一分鐘
          forming = {c: [hm, by[c][hm][1], by[c][hm][1], by[c][hm][1], by[c][hm][1], 0] for c in eng.codes if c in by and hm in by[c]}   # 回放:形成中的那根用那分鐘的開盤價當即時價
          write_today(day, bars, upto=hm, forming=forming)
          for c in eng.codes:
              if c in forming: eng.px[c] = forming[c][4]; eng.last_hm[c] = hm
              elif c in by and hm0 in by[c]: eng.px[c] = by[c][hm0][4]; eng.last_hm[c] = hm0
          dtm, errs, n_ev = eng.step(hm)
    except KeyboardInterrupt:   # 2026-10-29 使用者「怎麼 CTRL+C 無法中斷」:Windows 的工作行程會卡住,接住後直接把它們殺掉
        print("\n中斷"); eng.pool.terminate(); eng.pool.join(); return
    eng.pool.terminate()
    print(); print(f"共發 {len(eng.sent)} 則")
    for it in eng.sent: print(f"{it['t']} {it['kind']:6} {it['title']} | {it['body']}")


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "--replay":
        only = a[2].split(",") if len(a) > 2 and not a[2].startswith("--") else None
        replay(a[1], only, dry="--dry" in a or not CFG.get("PUSH_KEY"))
    else:
        live()
