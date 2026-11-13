"""2026-11-13 20:00 使用者「台指期 略有參考價值 如何取得」:期交所「前 30 個交易日期貨每筆成交資料」(Daily_YYYY_MM_DD.zip,每天收盤後公布,只留 30 個交易日)
→ 台指期(商品代號 TX)當天成交量最大的月份(近月)日盤 08:45~13:45 合成 1 分 K(這分鐘第一筆 = 開、最後一筆 = 收、量 = 口數)
→ kline_futures/TXF/<年-W週>.csv(欄位 date,time,open,high,low,close,volume)。先直接連期交所,不行再走自己的 Worker ?taifex= 轉送。
用法:python3 tools/txf_1m.py [起日 YYYY-MM-DD] [迄日](不給 = 今天)"""
import csv, datetime as dt, io, sys, time, urllib.request, zipfile, collections
from pathlib import Path
TW = dt.timezone(dt.timedelta(hours=8)); OUT = Path("kline_futures/TXF")
def get(u):
    return urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=180).read()
def fetch(d):
    raw = None
    for u in (f"https://www.taifex.com.tw/file/taifex/Dailydownload/DailydownloadCSV/Daily_{d:%Y_%m_%d}.zip",
              f"https://twse-proxy.redwinds542688.workers.dev/?taifex={d:%Y_%m_%d}"):
        try:
            b = get(u)
            if b[:2] == b"PK": raw = b; break
            print(d, "不是 zip:", u.split("/")[2], b[:80])
        except Exception as e: print(d, "抓不到", u.split("/")[2], e)
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
    m = vol.most_common(1)[0][0]; bars = {}
    for tm, p, q in ticks[m]:
        t = f"{tm[:2]}:{tm[2:4]}"; b = bars.get(t)
        if b is None: bars[t] = [p, p, p, p, q]
        else: b[1] = max(b[1], p); b[2] = min(b[2], p); b[3] = p; b[4] += q
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
    d = a; n = 0
    while d <= b:
        if d.weekday() < 5:
            try:
                x = fetch(d)
                if x: print(d, "近月", x[0], len(x[1]), "根 →", save(d, x[1])); n += 1
                else: print(d, "沒有資料(休市 / 超過 30 個交易日?)")
            except Exception as e: print(d, "失敗", e)
            time.sleep(3)
        d += dt.timedelta(days=1)
    print("完成", n, "天")
