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
RS_ALL = ["一", "二", "三", "四"]   # 2026-10-31 使用者「現在把新轉折 直接改名為 判四 加在公式 裡面 燈號啟動 真正的下單也啟動 自動平倉機制也啟動」:判四 = 新轉折做空(模擬器 MODE=turn4)
JUDGE = {"一": "判一", "二": "判二", "三": "判三", "四": "判四"}
T4MAX = 10   # 判四一天最多幾筆的預設;實際用私人庫 rs_env.py 的 T4MAX(全部股票合計、照出現先後;超額的不算)
RS_OF = {v: k for k, v in JUDGE.items()}
TAKE = 2.5
RULE_FILES = (("backtest/sim_day.py", "sys.argv"), ("backtest/chk/rs_env.py", "def env_of"))   # 2026-10-29 只從私人庫 Stock-logic 抓(公開庫 tools/backtest/ 已刪,不再退回);第二欄 = 內容一定要有的字(擋 Worker 回錯誤頁)


def load_env():
    cfg = {}
    f = Path(os.environ.get("BRIDGE_ENV", HERE / "bridge.env"))
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1); cfg[k.strip()] = re.split(r"\s+#", v, 1)[0].strip()
    for k in ("ENGINE_PRE", "ENGINE_PRE_SEC", "QUOTE_PORT", "WORKER_URL", "PUSH_KEY", "ENGINE_WORKERS", "ENGINE_CAP", "LOG_DIR", "ENGINE_SIM_SRC", "ENGINE_MAX_CODES", "ENGINE_LO", "ENGINE_HI", "ENGINE_AMP",
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


def latest_bar(bars, last=""):
    xs = [r[0] for rows in bars.values() for r in rows]
    return max(xs + ([last] if last else [])) if (xs or last) else ""


def write_today(day, bars, upto=None, forming=None):
    """把今天的 K 併進 kline_yuanta/<code>/<本週>.csv(前幾天的列保留、今天的整段換掉);upto = 只到這分鐘(不含);
       forming = {code: [hm, o, h, l, c, v]} 正在形成的這一分鐘(即時價):模擬器要看到下一根才會判定前一根的訊號,手機用的就是正在形成的那根(2026-10-29 回放發現)"""
    wk = iso_week(day)
    for c, rows in bars.items():
        if forming and c in forming: rows = [r for r in rows if r[0] != forming[c][0]] + [forming[c]]
        p = DATA / "kline_yuanta" / c / f"{wk}.csv"; old = []
        if p.exists():
            with open(p, encoding="utf-8") as f:
                old = [r for r in csv.DictReader(f) if r["date"] != day]
        out = ["date,time,open,high,low,close,volume\n"]
        for r in old: out.append(f"{r['date']},{r['time']},{r['open']},{r['high']},{r['low']},{r['close']},{r['volume']}\n")
        for hm, o, h, l, cl, v in sorted(rows):
            if upto and hm > upto: continue   # upto 那分鐘 = 正在形成的(forming),之後的不要
            if upto and hm == upto and not (forming and c in forming): continue
            out.append(f"{day},{hm},{o},{h},{l},{cl},{int(v)}\n")
        write_atomic(p, "".join(out))


def live_forming(codes, mkts, hm, bars):
    """Worker ?codes=(走元大通道或證交所)→ 每檔正在形成的這一分鐘:開高低收都用最新價、量 = 今天累計量 − 已完成分鐘的量(股)"""
    out = {}
    try:
        for i in range(0, len(codes), 40):
            cs = codes[i:i + 40]; ms = [mkts.get(c, "tse") for c in cs]
            j = http(worker_url("codes=" + ",".join(cs) + "&mkt=" + ",".join(ms)), timeout=15)
            for q in j.get("msgArray", []):
                c = q.get("c") or q.get("ch", "").split(".")[0]; z = q.get("z") or q.get("pz") or ""
                try: z = float(z)
                except Exception: continue
                if not (z > 0): continue
                try: tv = float(q.get("v") or 0) * 1000
                except Exception: tv = 0
                done = sum(r[5] for r in bars.get(c, []) if r[0] < hm)
                out[c] = [hm, z, z, z, z, max(0, tv - done)]
    except Exception as e:
        log("即時價抓不到(這分鐘沒有形成中的 K)", e)
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
        if br:
            write_atomic(f, json.dumps({day: br})); return True, len(br), max(br)
        if not f.exists(): write_atomic(f, "{}")
        return False, 0, ""
    except Exception as e:
        log("寬度抓不到", e)
        try:
            if not f.exists(): write_atomic(f, "{}")
        except Exception: pass
        return False, 0, ""


# ── 模擬器:每個工作行程只載入一次 pandas,之後用 runpy 一檔一檔跑 ──
BASE_ENV = dict(os.environ)   # 每次跑模擬器前把環境變數還原(判一的 OBVCROSS / GAPMIN… 不能留給判二;2026-10-29 回放抓到的 bug)
def _sim_one(args):
    code, name, rs, env, day = args
    sys.path.insert(0, str(SIMD))
    os.environ.clear(); os.environ.update(BASE_ENV)
    os.environ.update(env); os.environ["DAY"] = day; os.environ["REPO_DIR"] = str(DATA); os.environ["SIM_DIR"] = str(SIMD)
    os.environ["POOL"] = str(DATA / "pool_today.json")
    for k in ("TRACESTOP", "TRACEF", "TRACEX", "TRACET", "TRACES", "TRACELC", "TRACED", "TRACEG", "TRACE", "OBCTRACE"): os.environ.pop(k, None)
    buf = io.StringIO(); sys.argv = ["sim_day.py", code, name, "10", "10"]
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            runpy.run_path(str(SIMD / "sim_day.py"), run_name="__main__")
    except SystemExit:
        pass
    except Exception as e:
        return (code, rs, "", f"{type(e).__name__}: {e}")
    return (code, rs, buf.getvalue(), "")


RE_ENTRY = re.compile(r"^\s*(\d\d:\d\d) (強)?(多|空)(✓)?[✓✕]* 價 ([\d.]+) .*→ 進場 ([\d.]+) ×(\d+)張")
RE_FB = re.compile(r"^\s*(\d\d:\d\d) 假突破跌破 Ka.*→ 進場空 ([\d.]+) ×(\d+)張,停損 ([\d.]+)")
RE_T4 = re.compile(r"^\s*(\d\d:\d\d) 判四 .*→ 進場空 ([\d.]+) ×(\d+)張,停損 ([\d.]+)")   # 2026-10-31 判四(新轉折做空)進場,停損價由模擬器算好
RE_EXIT = re.compile(r"^\s*(\d\d:\d\d) (.+?) 出 ([\d.]+)\s*$")
RE_REV = re.compile(r"^\s*(\d\d:\d\d) .*→ (反向確認點|逆勢單遇反向點) 平倉 ([\d.]+)")


def parse_events(out):
    """模擬器輸出 → [(tm, 'entry'|'exit', dict)] 依時間順序"""
    ev = []
    for line in out.splitlines():
        m = RE_ENTRY.match(line)
        if m:
            ev.append((m.group(1), "entry", {"side": 1 if m.group(3) == "多" else -1, "strong": bool(m.group(2)), "sig": float(m.group(5)), "px": float(m.group(6)), "lots": int(m.group(7)), "text": ("強" if m.group(2) else "") + m.group(3) + (m.group(4) or "")})); continue   # text 和手機 m.text 一樣:空✓ / 多✓ / 強空…
        m = RE_FB.match(line)
        if m:
            ev.append((m.group(1), "entry", {"side": -1, "strong": False, "sig": float(m.group(2)), "px": float(m.group(2)), "lots": int(m.group(3)), "text": "空✓破", "sl": float(m.group(4))})); continue
        m = RE_T4.match(line)
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
    if "停損" in why: return "停損"
    if why.startswith("OBV"): return "OBV"
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
    return {"lo": max(_f(r.get("lo"), 15), _f(CFG.get("ENGINE_LO"), 15)),                      # 手機 BASE.lo 15 / 滾輪「監控低價」
            "hi": min(_f(r.get("hi"), 500), _f(CFG.get("ENGINE_HI"), 500)),                      # 手機 BASE.hi 500 / 滾輪「監控高價」(2026-10-30 不再和「多空上限 ÷ 1000」取小:超額的照算、通知標「超額」)
            "amp": max(_f(r.get("min_amp"), 3.0), _f(CFG.get("ENGINE_AMP"), 3.0)),              # 手機 BASE.amp 3.0 / 滾輪「振幅」
            "size": int(_f(r.get("size"), 130)), "cnt": int(_f(CFG.get("ENGINE_MAX_CODES"), 99)), # 原始池前 size 名 / 滾輪「監控檔數」(2026-10-31 預設 49 → 99:使用者「現在不是99嗎」手機監控檔數是 99,電腦要一樣;「所有 15到500的股票 只要符合資格 都要放進來」)
            "tick": _f(r.get("tick_max_pct"), 0.5), "excl": [str(x) for x in (r.get("exclude_industry") or ["17"])],
            "a5": _f(r.get("avg5_min_amp"), 3.0), "hits": r.get("avg5_min_hits")}


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
    if s.get("limit_hit") == 1: return "昨收跌停"
    if (str(s["industry"]) in R["excl"]) if s.get("industry") else (c.startswith("28") or c in FIN_FALLBACK): return "金融保險"
    if s.get("amp5") is not None and float(s["amp5"]) < R["a5"]: return "5 日振幅"
    if s.get("amp_hits") is not None and R["hits"] is not None and float(s["amp_hits"]) < float(R["hits"]): return "5 日振幅天數"
    if (float(s["tick_pct"]) if s.get("tick_pct") is not None else tick_pct(close)) > R["tick"]: return "一檔跳動"
    if s.get("daytrade") != 1: return "不可當沖"
    if s.get("short_first") != 1: return "不可先賣"   # 手機 tradable():dt && dtShort 才列入(沒標記 = 不可)
    return None


def build_universe(j, day, cap, held=(), noted=(), old_src=None):
    """名單 → 今天要算的股票。held = 已推進場還開著的;noted = 今天推過通知的(和手機 scan 一樣不受篩選、不佔檔數)
       回傳 dict:codes / names / prev / mkts / canshort / no_new(今天通知過但現在不合格 → 只算平倉、不發新進場)/ src / stamp / why"""
    pool = j.get("pool", j if isinstance(j, list) else []) if isinstance(j, (dict, list)) else []
    if isinstance(j, list): j = {"pool": j}
    R = pool_rules(j, cap)
    src = {str(s["code"]): s for s in pool if "code" in s}
    for c, s in (old_src or {}).items():
        if c not in src and (c in held or c in noted): src[c] = s   # 換名單後不見的、今天有通知的:用舊資料照算(平倉要靠它)
    carried = [c for c in list(held) + list(noted) if c in src]
    carried = list(dict.fromkeys(carried))
    stage1 = sorted([s for s in pool if str(s.get("code")) not in carried and re.fullmatch(r"[1-9]\d{3}", str(s.get("code", ""))) and s.get("market") in ("twse", "tpex")
                     and str(s.get("code")) not in set(map(str, j.get("disposed", []))) and 15 <= _f(s.get("close"), 0) <= 500 and s.get("amp") is not None and _f(s.get("amp"), 0) >= 3.0],
                    key=lambda s: -_f(s.get("lots"), 0))[: R["size"]]   # buildPool:成交張數大到小,前 size 名
    why = {}; rest = []
    for s in stage1:
        w = stock_ok(s, j, R)
        if w: why[w] = why.get(w, 0) + 1
        else: rest.append(str(s["code"]))
    n_held = len([c for c in held if c in src])
    codes = carried + rest[: max(0, R["cnt"] - n_held)]
    no_new = {c for c in carried if stock_ok(src[c], j, R)}
    names, prev, mkts, canshort = {}, {}, {}, {}
    for c in codes:
        s = src[c]; names[c] = s.get("name", "")
        prev[c] = s.get("close") if str(s.get("date") or j.get("date", "")) < day else s.get("prev")
        mkts[c] = "otc" if str(s.get("market", "")).lower() in ("otc", "tpex") else "tse"
        canshort[c] = s.get("daytrade") == 1 and s.get("short_first") == 1   # 2026-10-29 不能當沖 / 不能先賣 → 模擬器帶 CANSHORT=0(空單只記錄)
    out = dict(j); out["pool"] = [src[c] for c in src]   # 模擬器用代碼查昨收,換名單後不見的股票也要在裡面
    write_atomic(DATA / "pool_today.json", json.dumps(out, ensure_ascii=False))
    return {"codes": codes, "names": names, "prev": prev, "mkts": mkts, "canshort": canshort, "no_new": no_new, "src": src,
            "stamp": f"{j.get('generated', '')}|{j.get('elig_checked_at', '')}", "why": why, "R": R}


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
        self.j4 = set()       # 2026-10-31 判四今天已進場(推了、或雲端已有,超額的不算)的股票:一天最多 T4MAX 檔
        self.sent = []; self.px = {}; self.last_hm = {}; self.held_note = set()   # px / last_hm:每檔最新價、今天 K 到哪一分鐘(判定「訊號消失」前要確認這檔的 K 有到)
        self.state_f = DATA / f"engine_state_{day}.json" if state else None
        if self.state_f:
            try:
                j = json.loads(self.state_f.read_text(encoding="utf-8"))
                self.pos = {tuple(k.split("|")): v for k, v in j.get("pos", {}).items()}
                self.emitted = set(tuple(x) for x in j.get("emitted", []))
                self.j4 = set(j.get("j4", []))
                self.emitted |= {(x[0], x[1], x[2]) for x in j.get("seen", []) if len(x) == 4 and x[3] == "entry"}   # 舊版狀態檔
                if self.pos or self.emitted: log(f"接回狀態:開著 {len(self.pos)} 筆、處理過的進場 {len(self.emitted)} 個")
            except FileNotFoundError: pass
            except Exception as e: log("!! 狀態檔讀不懂(當作沒有;雲端通知會再對一次)", e)
        self.cap = int(CFG.get("ENGINE_CAP", "250000"))   # 手機的多空上限(通知裡的張數);2026-10-29 手機設 25 萬
        self.set_universe(uni)
        self.reload_envs()
        self.pool = Pool(int(CFG.get("ENGINE_WORKERS", "3")))

    def set_universe(self, uni):
        self.uni = uni; self.codes = uni["codes"]; self.names = uni["names"]; self.prev = uni["prev"]; self.mkts = uni["mkts"]
        self.canshort = uni["canshort"]; self.no_new = uni["no_new"]

    def carried(self):
        held = {c for _, c in self.pos}
        return held, {c for _, c, _ in self.emitted} - held

    def reload_envs(self):
        sys.path.insert(0, str(SIMD))
        import importlib
        if "rs_env" in sys.modules: rs_env = importlib.reload(sys.modules["rs_env"])
        else: rs_env = importlib.import_module("rs_env")
        self.envs = {}
        for rs in RS_ALL: self.envs[rs] = rs_env.env_of(rs, str(DATA / "breadth.json") if rs == "二" else None)
        self.envs["二"]["BRMISSING"] = "block"   # 2026-10-29 稽核 #1:今天沒有寬度資料 → 判二不進(sim_day.py 已支援)

    def save(self):
        if not self.state_f: return
        try: write_atomic(self.state_f, json.dumps({"emitted": sorted(self.emitted), "j4": sorted(self.j4), "pos": {"|".join(k): v for k, v in self.pos.items()}, "ver": RULES["ver"]}, ensure_ascii=False))
        except Exception as e: log("!! 狀態檔寫不進去", e)

    def push(self, it):
        """回傳 True = 推上去了(dry 也算);失敗的進場不記,下一輪(10 分鐘內)再試;失敗的平倉部位留著,下一輪再推"""
        self.sent.append(it)
        log(f"[通知] {it['kind']:6} {it['t']} {it['title']} | {it['body']}")
        if self.dry: return True
        # 2026-10-30 01:32 使用者「如何確保進場股票價格一出來的瞬間 交易可以成立」→ A「先做A」:同一台電腦的中繼不用等 Worker、不用每 3 秒輪詢 →
        #   推 Worker 之前先寫一行到本機 engine_data/live_<日期>.jsonl,中繼每秒讀(_k 和 Worker 存的鍵同一套,之後 Worker 那份讀到會被當成同一則,不會重複下單)
        try:
            kk = it["t"] + ":" + re.sub(r"[^A-Za-z0-9_\u4e00-\u9fff|:.\-]", "", str(it.get("key") or it["title"]))[:120]
            with open(DATA / f"live_{it.get('d') or self.day}.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps({**it, "_k": kk}, ensure_ascii=False) + "\n"); f.flush(); os.fsync(f.fileno())
        except Exception as e: log("!! 本機通知檔寫不進去(中繼改等 Worker)", e)
        for k in range(3):
            try:
                r = http(worker_url("log=add"), it, key=True)
                if r.get("ok"): return True
                log("推通知被拒", r); return False
            except Exception as e:
                log(f"推通知失敗 第 {k + 1} 次", e); time.sleep(2)
        return False

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
            mj = re.search(r"判[一二三四]", it.get("body", "")); rs = RS_OF.get(mj.group(0)) if mj else None
            if not rs: continue
            if it.get("kind") in ("long", "short"):
                mc = re.search(r"\b(\d{4,6})\b", it.get("title", "")); mt = re.match(r"\s*(\d\d:\d\d)", it.get("body", ""))
                if mc and mt: self.cloud_in[(rs, mc.group(1), mt.group(1))] = it
                if mc and rs == "四" and not re.search(r"·\s*超額\s*·", it.get("body", "")): self.j4.add(mc.group(1))   # 2026-10-31 手機 / 上一個 engine 發過的判四也算進一天上限
            elif it.get("pos"):
                p = str(it["pos"]).split("|")
                if len(p) == 3 and p[0] in RS_OF: self.cloud_out.add((RS_OF[p[0]], p[1], p[2]))
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
        body = f"{tm} {frm}現價 {ex:.2f} · {'超額' if p.get('over') else str(p['lots']) + '張'} · {j}"   # 2026-10-30 超額的部位平倉也標「超額」
        it = {"d": self.day, "t": at, "title": title, "body": body, "kind": kind, "key": self.key_of(code, title, rs), "pos": f"{j}|{code}|{p['t']}"}
        y = self.prev.get(code)
        if y: it["prev"] = y
        return self.push(it)

    def push_entry(self, rs, code, tm, d, at):
        nm = self.names.get(code, code); j = JUDGE[rs]
        side = "做多" if d["side"] > 0 else "做空"; px = d["px"]
        tp = px * (1 + (float(self.envs[rs].get("T4TAKE", "9")) if rs == "四" else TAKE) / 100 * d["side"])   # 2026-10-31 判四不停利(9% 才停,通知要有停利價中繼才收)
        sl = d.get("sl") or (px * (1 - (float(self.envs[rs].get("STOPCAP", self.envs[rs].get("STOP", "4.5"))) / 100) * d["side"]))   # 判一 pts 停損價模擬器沒印,用上限 3% 當外圍(真正的停損由平倉通知來)
        qty = max(1, int(self.cap // (px * 1000)))
        # 2026-10-30 01:55 使用者「超過限額的股票 也要出現在訊息通知欄裡 只是註記“超額”不交易 給強制進場提供一個發揮的平台」:一張就超過多空上限 → 照推、張數改寫「超額」(中繼看到不自動進場)
        over = px * 1000 > self.cap
        title = f"{nm} {code} {side}{'強訊號' if d['strong'] else ''}進場 {px:.2f}"
        body = f"{tm} {d['text']} · 停利 {tp:.2f} 停損 {sl:.2f} · {'超額' if over else str(qty) + '張'} · {j}"
        it = {"d": self.day, "t": at, "title": title, "body": body, "kind": "long" if d["side"] > 0 else "short", "key": self.key_of(code, title, rs)}
        y = self.prev.get(code)
        if y: it["prev"] = y
        if self.push(it):
            self.pos[(rs, code)] = {"side": d["side"], "t": tm, "px": px, "lots": qty, "text": d["text"], "over": over}
            if rs == "四" and not over: self.j4.add(code)
            return True
        return False

    def compute(self, only=None):
        """only = {(判別式, 代碼)}:只算這些(2026-10-30 預判收盤確認用)"""
        jobs = []
        for rs in RS_ALL:
            for c in self.codes:
                if only is not None and (rs, c) not in only: continue
                env = dict(self.envs[rs])
                if not self.canshort.get(c, False): env["CANSHORT"] = "0"   # 2026-10-29 稽核 #3:不能先賣 → 空單只記錄
                jobs.append((c, self.names.get(c, c), rs, env, self.day))
        t0 = time.time(); res = self.pool.map(_sim_one, jobs, chunksize=4) if jobs else []
        return res, time.time() - t0, len(jobs)

    def apply(self, res, upto, allow_new=True, allow_phantom=True, rs_block=None):
        """對帳(2026-10-29 稽核 #2 / #7):每檔每判,拿模擬器現在的交易清單和已推的部位對;
           allow_new = 這輪可以發新進場;allow_phantom = 這輪資料完整、可以判定「訊號消失」;rs_block = {判別式: 原因} 這輪不發新進場"""
        cutoff = hm_add(upto, -10); n_ev = 0; rs_block = rs_block or {}
        for code, rs, out, err in res:
            if err: log(f"模擬器錯誤 {JUDGE[rs]} {code}: {err[:160]}"); continue
            ev = parse_events(out); n_ev += sum(1 for tm, _, _ in ev if tm < upto)
            trades, open_tm = sim_trades(ev, upto); k = (rs, code)
            p = self.pos.get(k)
            if p and (rs, code, p["t"]) in self.cloud_out:   # 雲端已有這個部位的平倉(手機發的 / 重開前發的)
                log(f"{JUDGE[rs]} {code} {p['t']} 雲端已有平倉通知 → 結束部位"); self.pos.pop(k); p = None
            if p:
                tr = trades.get(p["t"])
                if tr is None or tr[0]["side"] != p["side"]:
                    have = self.last_hm.get(code, "") >= hm_add(p["t"], 1) and p["t"] < upto and "無資料" not in out   # 這檔今天的 K 沒到 / 不夠 → 不是訊號消失,是資料缺
                    if allow_phantom and have:
                        log(f"!! {JUDGE[rs]} {code} {p['t']} 已推的進場在重算裡不見了 → 推平倉")
                        if self.push_exit(rs, code, p, upto, self.px.get(code, p["px"]), "", upto, phantom=True): self.pos.pop(k)
                    elif (k, p["t"]) not in self.held_note:
                        self.held_note.add((k, p["t"])); log(f"{JUDGE[rs]} {code} {p['t']} 這輪重算看不到它,但 K 不完整 → 先不判定")
                elif tr[1] is not None:   # 2026-10-29 已推的部位不管多舊都要推平倉(原本出場時間早於 10 分鐘就默默丟掉,中繼的部位沒人平)
                    if self.push_exit(rs, code, p, tr[1][0], tr[1][1]["px"], tr[1][1]["why"], upto): self.pos.pop(k)
            if k in self.pos or open_tm is None: continue
            ek = (rs, code, open_tm); d = trades[open_tm][0]
            if ek in self.emitted: continue
            ci = self.cloud_in.get(ek)
            if ek in self.cloud_out:
                self.emitted.add(ek); continue
            if ci and (ci.get("kind") == "long") == (d["side"] > 0):   # 雲端已經有(手機 / 上一個 engine)→ 接回來管平倉,不重發
                mq = re.search(r"·\s*(\d+)張", ci.get("body", "")); mp = re.search(r"([\d.]+)\s*$", ci.get("title", ""))
                px = float(mp.group(1)) if mp else d["px"]
                self.pos[k] = {"side": d["side"], "t": open_tm, "px": px, "lots": int(mq.group(1)) if mq else max(1, int(self.cap // (px * 1000))), "text": d["text"]}
                self.emitted.add(ek); log(f"{JUDGE[rs]} {code} {open_tm} 雲端已有這筆進場 → 接回、不重發"); continue
            if open_tm < cutoff:
                self.emitted.add(ek); log(f"{JUDGE[rs]} {code} {open_tm} 進場已超過 10 分鐘 → 不發"); continue
            if code in self.no_new: why = "今天已不合格(只算平倉)"
            elif not allow_new: why = "這輪資料不完整"
            elif rs in rs_block: why = rs_block[rs]
            elif rs == "四" and code not in self.j4 and len(self.j4) >= int(_f(self.envs.get("四", {}).get("T4MAX"), T4MAX)):   # 2026-10-31 判四一天最多 T4MAX 檔(到了就不再發,記 emitted)
                self.emitted.add(ek); log(f"判四 {code} {open_tm} 今天判四已 {len(self.j4)} 檔(到上限)→ 不發"); continue
            else: why = None
            if why:
                log(f"{JUDGE[rs]} {code} {open_tm} 進場先不發:{why}"); continue   # 不記 emitted:10 分鐘內恢復就照發
            if self.push_entry(rs, code, open_tm, d, upto): self.emitted.add(ek)
        self.save()
        return n_ev

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
        res, dtm, njobs = self.compute()
        errs = sum(1 for r in res if r[3])
        n_ev = self.apply(res, upto, **kw)
        return dtm, errs, n_ev

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
    cap = int(CFG.get("ENGINE_CAP", "250000"))
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
    err_max = float(CFG.get("ENGINE_ERR_MAX", "0.2")); rules_every = float(CFG.get("ENGINE_RULES_MIN", "30")) * 60; bar_wait = float(CFG.get("ENGINE_BAR_WAIT", "15"))
    done = set(); pool_chk = time.time(); pool_906 = False; late_n = 0; br = {"ok_at": 0.0, "last": ""}
    # 2026-10-30 01:40 使用者「能夠預判嗎 … 股價已經來到判一二三的臨界點啟動預判」→「做法一」:每分鐘第 ENGINE_PRE_SEC(45)秒先用本機 K「假設這根用現價收」全部算一遍,
    #   挑出快要成立的(候選);下一分鐘第 0 秒這根真的收了,只重算候選(本機 K,不等 Worker),條件還成立就馬上推(本機檔 → 中繼 1 秒內送單)。
    #   判斷依據還是收盤後的 K(和回測一樣);第 4 秒的整輪照舊(已推的不會重發,沒趕上的照發)。ENGINE_PRE=0 關掉
    pre_on = CFG.get("ENGINE_PRE", "1") == "1"; pre_sec = int(CFG.get("ENGINE_PRE_SEC", "45"))
    pre = {"for": "", "cand": set(), "done": set(), "fast": set(), "ok": False, "rs_block": {}}
    try:
        while True:
            n = now_tw(); hm = n.strftime("%H:%M")
            if hm >= "13:32": break
            if pre_on and "09:00" <= hm < "13:24" and n.second >= pre_sec and hm not in pre["done"] and pre["ok"]:
                pre["done"].add(hm); t0 = time.time()
                try:
                    lb_ = local_bars()
                    if lb_:
                        nxt = hm_add(hm, 1); bs, fm = pre_frame(lb_, nxt, set(eng.codes), pre.get("base"))
                        write_today(day, bs, upto=nxt, forming=fm)
                        res_, dtm_, nj_ = eng.compute()
                        pre["for"], pre["cand"] = nxt, eng.candidates(res_, nxt)
                        log(f"預判 {hm} 這根(假設現價收):{len(pre['cand'])} 個候選 {sorted(JUDGE[r] + c for r, c in pre['cand'])},{time.time() - t0:.1f} 秒")
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
            if hm < "09:01" or n.second < 4 or hm in done:
                time.sleep(0.5 if pre_on else 1); continue
            done.add(hm)
            try:
                flags = []; off = []
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
                # 等 hm-1 那根上來(稽核 #6)
                want = hm_add(hm, -1); t_end = time.time() + bar_wait
                while True:
                    bars, last = today_bars(day); lb = latest_bar(bars, last)
                    if lb >= want or time.time() >= t_end: break
                    time.sleep(3)
                forming = live_forming(eng.codes, eng.mkts, hm, bars)
                write_today(day, bars, upto=hm, forming=forming)
                eng.last_hm = {}
                for c, rows in bars.items():
                    if rows: r = max(rows); eng.px[c] = r[4]; eng.last_hm[c] = r[0]
                for c, r in forming.items(): eng.px[c] = r[4]; eng.last_hm[c] = max(eng.last_hm.get(c, ""), r[0])
                # 寬度每分鐘(稽核 #1)
                ok, nbr, blast = write_breadth(day)
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
                eng.sync_cloud()
                res, dtm, njobs = eng.compute()
                errs = sum(1 for r in res if r[3])
                if njobs and errs / njobs > err_max: off.append(f"模擬器錯誤 {errs}/{njobs}")
                healthy = not off
                n_ev = eng.apply(res, hm, allow_new=healthy and not late, allow_phantom=healthy and not late, rs_block=rs_block)
                pre["ok"] = healthy and not late and bool(bars); pre["rs_block"] = dict(rs_block); pre["base"] = bars   # 2026-10-30 預判只在上一輪健康時做,判二沒寬度那種封鎖也照帶
                if off: log(f"!! {hm} 心跳送 on=0(手機接手):{';'.join(off)}")
                eng.heartbeat(healthy, f"{hm} {dtm:.0f}s {n_ev} 事件" + (" " + ";".join(off) if off else ""), flags)
                log(f"{hm} 算完 {dtm:.1f} 秒,K 到 {lb},事件 {n_ev},錯誤 {errs}/{njobs},開著 {len(eng.pos)}{' ' + ' '.join(flags) if flags else ''}")
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
