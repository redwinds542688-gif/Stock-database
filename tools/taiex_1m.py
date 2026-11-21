"""2026-11-13 19:45 使用者「好」(要不要每天存加權指數的 1 分 K → 之後用加權指數算 r「順大盤」重測):
證交所「每 5 秒指數統計」(MI_5MINS_INDEX)→ 發行量加權股價指數 合成 1 分 K(這分鐘第一筆 = 開、最後一筆 = 收),
寫 kline_index/TAIEX/<年-W週>.csv(欄位和 kline_yuanta 一樣:date,time,open,high,low,close,volume;volume 都是 0)。
用法:python3 tools/taiex_1m.py [起日 YYYY-MM-DD] [迄日](不給 = 今天);休市日證交所回「沒有資料」就跳過
2026-11-21 使用者「全改」(檢查 W6):Worker 不帶金鑰的 ?twidx 每個 IP 10 分鐘最多 20 次,手動補超過 20 天會被 429 而且原本默默跳過 →
  有環境變數 YT_PUSH_KEY 就帶 X-Push-Key(中繼金鑰不受這個限制;金鑰不印出來);429 / 5xx / 連線錯誤等一下再試;最後還有哪天失敗就結束碼 1(GitHub Actions 變紅)"""
import csv, datetime as dt, json, os, sys, time, urllib.error, urllib.request
from pathlib import Path
TW = dt.timezone(dt.timedelta(hours=8)); OUT = Path("kline_index/TAIEX")
def fetch(d):
    # 2026-11-13 19:55 GitHub 機器直接連證交所會被擋(回的不是資料)→ 改走自己的 Worker ?twidx=(Cloudflare 抓、合成好 1 分 K)
    u = f"https://twse-proxy.redwinds542688.workers.dev/?twidx={d:%Y%m%d}"
    h = {"User-Agent": "Mozilla/5.0"}
    if os.environ.get("YT_PUSH_KEY"): h["X-Push-Key"] = os.environ["YT_PUSH_KEY"]
    for i in range(5):   # 2026-11-21(檢查 W6):429 / 5xx / 連不上 → 等 30、60、120、240 秒再試
        try:
            j = json.load(urllib.request.urlopen(urllib.request.Request(u, headers=h), timeout=60)); break
        except urllib.error.HTTPError as e:
            if i == 4 or not (e.code == 429 or e.code >= 500): raise RuntimeError(f"HTTP {e.code}")
            print(d, f"HTTP {e.code},{30 * 2 ** i} 秒後再試")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if i == 4: raise RuntimeError(f"連不上 {e}")
            print(d, f"連不上,{30 * 2 ** i} 秒後再試")
        time.sleep(30 * 2 ** i)
    if j.get("error"): raise RuntimeError(j)
    return {r[0]: r[1:5] for r in j.get("bars") or []} or None
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
    d = a; n = 0; bad = []
    while d <= b:
        if d.weekday() < 5:
            try:
                bars = fetch(d)
                if bars: print(d, len(bars), "根 →", save(d, bars)); n += 1
                else: print(d, "沒有資料(休市?)")
            except Exception as e: print(d, "失敗", e); bad.append(str(d))
            time.sleep(4)   # 證交所連續查太快會擋
        d += dt.timedelta(days=1)
    print("完成", n, "天")
    if bad: print("失敗", len(bad), "天:", " ".join(bad)); sys.exit(1)   # 2026-11-21(檢查 W6):有失敗的日子 → 結束碼 1
