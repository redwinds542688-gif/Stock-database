"""2026-11-13 19:45 使用者「好」(要不要每天存加權指數的 1 分 K → 之後用加權指數算 r「順大盤」重測):
證交所「每 5 秒指數統計」(MI_5MINS_INDEX)→ 發行量加權股價指數 合成 1 分 K(這分鐘第一筆 = 開、最後一筆 = 收),
寫 kline_index/TAIEX/<年-W週>.csv(欄位和 kline_yuanta 一樣:date,time,open,high,low,close,volume;volume 都是 0)。
用法:python3 tools/taiex_1m.py [起日 YYYY-MM-DD] [迄日](不給 = 今天);休市日證交所回「沒有資料」就跳過"""
import csv, datetime as dt, json, sys, time, urllib.request
from pathlib import Path
TW = dt.timezone(dt.timedelta(hours=8)); OUT = Path("kline_index/TAIEX")
def fetch(d):
    u = f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_5MINS_INDEX?date={d:%Y%m%d}&response=json"
    j = json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=30))
    if j.get("stat") != "OK" or not j.get("data"): return None
    f = j.get("fields") or []; i = next((k for k, x in enumerate(f) if "加權" in x and "未含" not in x), 1)
    bars = {}
    for r in j["data"]:
        t = str(r[0])[:5]
        try: p = float(str(r[i]).replace(",", ""))
        except Exception: continue
        if not ("09:00" <= t <= "13:30") or p <= 0: continue
        b = bars.get(t)
        if b is None: bars[t] = [p, p, p, p]
        else: b[1] = max(b[1], p); b[2] = min(b[2], p); b[3] = p
    return bars
def save(d, bars):
    y, w, _ = d.isocalendar(); f = OUT / f"{y}-W{w:02d}.csv"; OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    if f.exists(): rows = [r for r in csv.DictReader(open(f, encoding="utf-8")) if r["date"] != d.isoformat()]
    rows += [{"date": d.isoformat(), "time": t, "open": b[0], "high": b[1], "low": b[2], "close": b[3], "volume": 0} for t, b in sorted(bars.items())]
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
                bars = fetch(d)
                if bars: print(d, len(bars), "根 →", save(d, bars)); n += 1
                else: print(d, "沒有資料(休市?)")
            except Exception as e: print(d, "失敗", e)
            time.sleep(4)   # 證交所連續查太快會擋
        d += dt.timedelta(days=1)
    print("完成", n, "天")
