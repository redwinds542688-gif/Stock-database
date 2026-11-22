"""2026-11-13 20:00 使用者「台指期 略有參考價值 如何取得」:期交所「前 30 個交易日期貨每筆成交資料」(Daily_YYYY_MM_DD.zip,每天收盤後公布,只留 30 個交易日)
→ 台指期(商品代號 TX)當天成交量最大的月份(近月)日盤 08:45~13:45 合成 1 分 K(這分鐘第一筆 = 開、最後一筆 = 收、量 = 口數;
  2026-11-22(L1)起量 = 單邊口數 = 檔案的「成交數量(B+S)」÷ 2)
→ kline_futures/TXF/<年-W週>.csv(欄位 date,time,open,high,low,close,volume)。先直接連期交所,不行再走自己的 Worker ?taifex= 轉送。
用法:python3 tools/txf_1m.py [起日 YYYY-MM-DD] [迄日](不給 = 今天)
2026-11-21 使用者「全改」(檢查 W6):走 Worker ?taifex 時不帶金鑰每個 IP 10 分鐘最多 20 次 → 有環境變數 YT_PUSH_KEY 就帶 X-Push-Key(只送給自己的 Worker,不送期交所;不印出來);
  Worker 回 429 / 5xx / 連不上就等一下再試;最後還有哪天抓不到(不是休市)就結束碼 1(GitHub Actions 變紅)"""
import csv, datetime as dt, io, os, sys, time, urllib.error, urllib.request, zipfile, collections
from pathlib import Path
TW = dt.timezone(dt.timedelta(hours=8)); OUT = Path("kline_futures/TXF")
WK = "https://twse-proxy.redwinds542688.workers.dev/"
class Fail(Exception): pass   # 2026-11-21(檢查 W6):真的抓不到(429 / 5xx / 連不上重試完)≠ 休市
def get(u):
    h = {"User-Agent": "Mozilla/5.0"}
    if u.startswith(WK) and os.environ.get("YT_PUSH_KEY"): h["X-Push-Key"] = os.environ["YT_PUSH_KEY"]   # 只送給自己的 Worker
    return urllib.request.urlopen(urllib.request.Request(u, headers=h), timeout=180).read()
def get_retry(u, d):
    for i in range(5):   # 429 / 5xx / 連不上 → 等 30、60、120、240 秒再試
        try: return get(u)
        except urllib.error.HTTPError as e:
            if e.code != 429 and e.code < 500: raise
            if i == 4: raise Fail(f"HTTP {e.code}")
            print(d, "Worker", f"HTTP {e.code},{30 * 2 ** i} 秒後再試")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if i == 4: raise Fail(f"連不上 {e}")
            print(d, "Worker 連不上", f"{30 * 2 ** i} 秒後再試")
        time.sleep(30 * 2 ** i)
def fetch(d):
    raw = None
    try:
        b = get(f"https://www.taifex.com.tw/file/taifex/Dailydownload/DailydownloadCSV/Daily_{d:%Y_%m_%d}.zip")
        if b[:2] == b"PK": raw = b
        else: print(d, "不是 zip:", "www.taifex.com.tw", b[:80])
    except Exception as e: print(d, "抓不到", "www.taifex.com.tw", e)
    if raw is None:
        try:
            b = get_retry(f"{WK}?taifex={d:%Y_%m_%d}", d)
            if b[:2] == b"PK": raw = b
            else: print(d, "不是 zip:", "Worker", b[:80])
        except urllib.error.HTTPError as e: print(d, "抓不到", "Worker", f"HTTP {e.code}")   # 404 等 = 期交所沒有這天(休市 / 超過 30 個交易日)
    if raw is None: return None
    z = zipfile.ZipFile(io.BytesIO(raw)); txt = z.read(z.namelist()[0]).decode("big5", "ignore")
    ticks = collections.defaultdict(list); vol = collections.Counter()
    for r in csv.reader(io.StringIO(txt)):
        if len(r) < 6 or r[1].strip() != "TX": continue
        mth = r[2].strip()
        if not mth.isdigit(): continue   # 跨月價差(202611/202612)不算
        ds, tm = r[0].strip(), r[3].strip().zfill(6)
        if ds != f"{d:%Y%m%d}" or not ("084500" <= tm <= "134500"): continue   # 只要當天日盤(檔案裡也有夜盤)
        try: p = float(r[4]); q = int(float(r[5]))
        except Exception: continue
        ticks[mth].append((tm, p, q)); vol[mth] += q
    if not vol: return None
    def third_wed(y, mo):
        x = dt.date(y, mo, 1); x += dt.timedelta(days=(2 - x.weekday()) % 7); return x + dt.timedelta(days=14)
    ny, nm = d.year, d.month
    if d > third_wed(ny, nm): ny, nm = (ny + 1, 1) if nm == 12 else (ny, nm + 1)
    near = f"{ny}{nm:02d}"   # 2026-11-18 21:05 使用者「改」:固定近月(結算日當天還是本月;原本取成交量最大,結算日會拿到下個月)
    m = near if near in vol else vol.most_common(1)[0][0]; bars = {}
    settle = d == third_wed(d.year, d.month) and m == f"{d:%Y%m}"   # 結算日近月 13:30 收盤
    for tm, p, q in ticks[m]:
        t = f"{tm[:2]}:{tm[2:4]}"
        if settle and t > "13:30": continue
        b = bars.get(t)
        if b is None: bars[t] = [p, p, p, p, q]
        else: b[1] = max(b[1], p); b[2] = min(b[2], p); b[3] = p; b[4] += q
    # 2026-11-22 判別式聊天轉達(使用者):結算日近月 13:30 就收了,13:30~13:45 沒有 K → ⑧ 強弱「前一天台指期收盤(13:45 前最後一根)」拿到近月 13:29,
    #   隔天近月已經換成下個月,兩個合約差一個價差(11/18 約 0.7%)→ 11/19 判十 11 筆空單全被擋。改成:結算日 13:30 以後接下個月合約(隔天的近月)的真實成交
    if settle:
        nx = f"{d.year + 1}01" if d.month == 12 else f"{d.year}{d.month + 1:02d}"
        nb = {}
        for tm, p, q in ticks.get(nx, []):
            t = f"{tm[:2]}:{tm[2:4]}"
            if t < "13:30" or t in bars: continue
            b = nb.get(t)
            if b is None: nb[t] = [p, p, p, p, q]
            else: b[1] = max(b[1], p); b[2] = min(b[2], p); b[3] = p; b[4] += q
        bars.update(nb)
    # 2026-11-22 使用者「全部做」(L1):期交所每筆成交檔的「成交數量(B+S)」是買賣雙邊合計(每筆 = 2 × 口數),
    #   原本直接加總 → 量是實際口數的 2 倍,和中繼推的元大台指期 K(save_cloud_bars.save_fbars,單邊口數)對不起來。
    #   改成除以 2(單邊口數)。每筆都是偶數,合計除 2 一定整除;萬一出現奇數就四捨五入。
    for b in bars.values(): b[4] = int(round(b[4] / 2))
    return m, bars
def save(d, bars):
    y, w, _ = d.isocalendar(); f = OUT / f"{y}-W{w:02d}.csv"; OUT.mkdir(parents=True, exist_ok=True)
    rows = [r for r in csv.DictReader(open(f, encoding="utf-8")) if r["date"] != d.isoformat()] if f.exists() else []
    rows += [{"date": d.isoformat(), "time": t, "open": b[0], "high": b[1], "low": b[2], "close": b[3], "volume": b[4]} for t, b in sorted(bars.items())]
    rows.sort(key=lambda r: (r["date"], r["time"]))
    with open(f, "w", newline="", encoding="utf-8") as fh:
        w_ = csv.DictWriter(fh, fieldnames=["date", "time", "open", "high", "low", "close", "volume"]); w_.writeheader(); w_.writerows(rows)
    return f
if __name__ == "__main__":
    today = dt.datetime.now(TW).date()
    a = dt.date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1] else today
    b = dt.date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2] else a if len(sys.argv) > 1 and sys.argv[1] else today
    d = a; n = 0; bad = []
    while d <= b:
        if d.weekday() < 5:
            try:
                x = fetch(d)
                if x: print(d, "近月", x[0], len(x[1]), "根 →", save(d, x[1])); n += 1
                else: print(d, "沒有資料(休市 / 超過 30 個交易日?)")
            except Exception as e: print(d, "失敗", e); bad.append(str(d))
            time.sleep(3)
        d += dt.timedelta(days=1)
    print("完成", n, "天")
    if bad: print("失敗", len(bad), "天:", " ".join(bad)); sys.exit(1)   # 2026-11-21(檢查 W6):有失敗的日子 → 結束碼 1
