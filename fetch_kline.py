# ══════════════════════════════════════════════════════
#  名揚四海 — 分 K 下載器(GitHub Actions 版)
#
#  讀 pool.json 裡的股票清單,每檔到 Yahoo Finance 抓 1 分K,
#  寫進 kline/{代號}/{年}-W{週}.csv。
#
#  重點:Yahoo 1 分K 只能回溯 7 天,所以每天抓到的新資料會
#  「合併」進既有 CSV(依 date+time 去重),歷史會一天一天累積,
#  之後回測就有超過 7 天的資料可用。
#
#  檔案按「週」拆,而且只滾動保留最近 KEEP_WEEKS 週(預設 8 = 本週 + 前 7 週):
#  第 9 週開始,第 1 週的檔案自動刪除;第 10 週刪第 2 週……以此類推。
#  不在今天清單裡的股票,其舊週檔也一樣會被清掉,資料夾空了就整個移除。
#
#  產出:
#    kline/{代號}/{年}-W{週}.csv   例 kline/3481/2026-W38.csv(ISO 週,週一~週日)
#                                 欄位 date,time,open,high,low,close,volume
#    kline/index.json             每檔的名稱、市場、資料起迄、筆數、佔用大小
# ══════════════════════════════════════════════════════

import os, json, time, sys
import requests
import pandas as pd

INTERVAL = "1m"
RANGE = "7d"
OUT_DIR = "kline"
KEEP_WEEKS = 8         # 保留幾週(含本週)。8 = 本週 + 前 7 週;第 9 週起滾動刪最舊的一週
SLEEP = 0.6            # 每檔之間停一下,避免被 Yahoo 限流
COLS = ["date", "time", "open", "high", "low", "close", "volume"]

S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})


def yahoo(sym):
    """抓一個 Yahoo 代號,回傳 DataFrame;查不到回傳 None;被限流會重試"""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
    for attempt in range(4):
        try:
            r = S.get(url, params={"interval": INTERVAL, "range": RANGE}, timeout=30)
        except Exception as e:
            print(f"    連線失敗({e}),重試")
            time.sleep(5 * (attempt + 1))
            continue
        if r.status_code == 429:
            print("    被限流,等 30 秒")
            time.sleep(30)
            continue
        if r.status_code == 404:
            return None
        try:
            j = r.json()
        except ValueError:
            time.sleep(5)
            continue
        chart = j.get("chart") or {}
        if chart.get("error"):
            code = (chart["error"] or {}).get("code", "")
            if code in ("Not Found",):
                return None
            print(f"    Yahoo 回錯誤:{chart['error']}")
            time.sleep(5)
            continue
        res = (chart.get("result") or [None])[0]
        if not res or not res.get("timestamp"):
            return pd.DataFrame(columns=COLS)
        q = res["indicators"]["quote"][0]
        df = pd.DataFrame({
            "ts": pd.to_datetime(res["timestamp"], unit="s", utc=True).tz_convert("Asia/Taipei"),
            "open": q.get("open"), "high": q.get("high"), "low": q.get("low"),
            "close": q.get("close"), "volume": q.get("volume"),
        })
        df = df.dropna(subset=["close"])
        t = df.ts.dt.time
        df = df[(t >= pd.Timestamp("09:00").time()) & (t <= pd.Timestamp("13:30").time())]
        df = df.assign(date=df.ts.dt.strftime("%Y-%m-%d"), time=df.ts.dt.strftime("%H:%M"))
        df["volume"] = df["volume"].fillna(0).astype("int64")
        for c in ("open", "high", "low", "close"):
            df[c] = df[c].astype(float).round(2)
        return df[COLS].reset_index(drop=True)
    return None


def merge_save(path, new):
    """新資料併入既有 CSV,依 date+time 去重(新資料優先),排序後存檔"""
    if os.path.exists(path):
        try:
            old = pd.read_csv(path, dtype={"date": str, "time": str})
        except Exception:
            old = pd.DataFrame(columns=COLS)
    else:
        old = pd.DataFrame(columns=COLS)
    allr = pd.concat([old, new], ignore_index=True)
    allr = allr.drop_duplicates(subset=["date", "time"], keep="last")
    allr = allr.sort_values(["date", "time"]).reset_index(drop=True)
    allr.to_csv(path, index=False, encoding="utf-8")
    return allr


def week_key(date_str):
    """'2026-09-18' → '2026-W38'(ISO 週,週一為一週開始)"""
    y, w, _ = pd.Timestamp(date_str).isocalendar()
    return f"{y}-W{w:02d}"


def keep_weeks_set():
    """要保留的週鍵集合:本週往前數 KEEP_WEEKS 週(台灣時間)"""
    today = pd.Timestamp.now(tz="Asia/Taipei").normalize().tz_localize(None)
    return {week_key(today - pd.Timedelta(weeks=i)) for i in range(KEEP_WEEKS)}


KEEP = keep_weeks_set()


def save_by_week(code, new):
    """把抓到的資料依週拆開,各自併入 kline/{code}/{週}.csv(超出保留範圍的週直接不存)"""
    folder = os.path.join(OUT_DIR, code)
    os.makedirs(folder, exist_ok=True)
    for wk, part in new.groupby(new["date"].map(week_key)):
        if wk in KEEP:
            merge_save(os.path.join(folder, f"{wk}.csv"), part)


def stats(code):
    """該股票資料夾的統計;沒有檔案回傳 None"""
    folder = os.path.join(OUT_DIR, code)
    files = sorted(f for f in os.listdir(folder) if f.endswith(".csv"))
    if not files:
        return None
    size = sum(os.path.getsize(os.path.join(folder, f)) for f in files)
    first = pd.read_csv(os.path.join(folder, files[0]), dtype=str)["date"].min()
    last = pd.read_csv(os.path.join(folder, files[-1]), dtype=str)["date"].max()
    return {"first": first, "last": last, "weeks": len(files), "kb": round(size / 1024)}


def cleanup():
    """刪掉所有股票資料夾裡超出保留範圍的週檔;空資料夾整個移除。回傳被刪的檔案數"""
    removed = 0
    for code in os.listdir(OUT_DIR):
        folder = os.path.join(OUT_DIR, code)
        if not os.path.isdir(folder):
            continue
        for f in os.listdir(folder):
            if f.endswith(".csv") and f[:-4] not in KEEP:
                os.remove(os.path.join(folder, f))
                removed += 1
        if not os.listdir(folder):
            os.rmdir(folder)
    return removed


# ── 讀股票清單 ─────────────────────────────────
if not os.path.exists("pool.json"):
    sys.exit("找不到 pool.json,請先執行 make_pool.py")
with open("pool.json", encoding="utf-8") as f:
    P = json.load(f)
stocks = P["pool"]
print(f"清單日期 {P['date']},共 {len(stocks)} 檔")
os.makedirs(OUT_DIR, exist_ok=True)

print(f"保留週:{sorted(KEEP)}")
n = cleanup()
print(f"清除過期週檔:{n} 個")

# 既有的 index(保留之前抓過、但今天不在清單的股票的名稱/市場資訊)
idx_path = os.path.join(OUT_DIR, "index.json")
old_index = {}
if os.path.exists(idx_path):
    try:
        with open(idx_path, encoding="utf-8") as f:
            old_index = json.load(f).get("stocks", {})
    except Exception:
        old_index = {}
index = {}

ok = fail = 0
for i, s in enumerate(stocks, 1):
    code, name, market = s["code"], s["name"], s.get("market", "twse")
    # 上市 .TW,上櫃 .TWO;猜錯就換另一個再試
    order = [".TW", ".TWO"] if market == "twse" else [".TWO", ".TW"]
    df, used = None, None
    for suf in order:
        df = yahoo(code + suf)
        if df is not None and len(df):
            used = suf
            break
        time.sleep(SLEEP)
    if df is None or not len(df):
        print(f"[{i:>3}/{len(stocks)}] {code} {name:<8} 抓不到")
        fail += 1
        continue
    save_by_week(code, df)
    st = stats(code)
    if st is None:                       # 抓到的全是保留範圍外的舊資料(理論上不會)
        print(f"[{i:>3}/{len(stocks)}] {code} {name:<8} 沒有可保留的資料")
        fail += 1
        continue
    index[code] = {"name": name, "market": market, "yahoo": code + used, **st}
    print(f"[{i:>3}/{len(stocks)}] {code} {name:<8} 新 {len(df):>5} 筆 → "
          f"{st['first']}~{st['last']} 共 {st['weeks']} 週 {st['kb']} KB")
    ok += 1
    time.sleep(SLEEP)

# 今天不在清單、但上週檔還在保留期內的股票,也列進 index
for code in sorted(os.listdir(OUT_DIR)):
    if code in index or not os.path.isdir(os.path.join(OUT_DIR, code)):
        continue
    st = stats(code)
    if st:
        meta = old_index.get(code, {})
        index[code] = {"name": meta.get("name", code), "market": meta.get("market", ""),
                       "yahoo": meta.get("yahoo", ""), **st}

total_kb = sum(v.get("kb", 0) for v in index.values())
with open(idx_path, "w", encoding="utf-8") as f:
    json.dump({"pool_date": P["date"], "interval": INTERVAL,
               "updated": time.strftime("%Y-%m-%d %H:%M", time.gmtime(time.time() + 8 * 3600)),
               "total_mb": round(total_kb / 1024, 1), "stock_count": len(index),
               "stocks": index}, f, ensure_ascii=False, indent=1)

print(f"\n完成:成功 {ok} 檔,失敗 {fail} 檔 → {OUT_DIR}/  目前總量 {total_kb/1024:.1f} MB")
if ok == 0:
    sys.exit("一檔都沒抓到,視為失敗")
