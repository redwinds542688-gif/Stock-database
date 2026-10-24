# -*- coding: utf-8 -*-
"""
存當天的雲端 1 分K(2026-10-20 使用者:「好 那只能針對用雲 K」)

盤中判別式只能用雲端快照 K(Worker 每分鐘向證交所抓的,沒有分鐘內高低、偶爾併根),
Yahoo 的精確 K 盤中慢 20 分鐘拿不到。回測若用 Yahoo K,算出來的就不是盤中那套系統的表現
(10/20 同一天同一套規則:判二 雲端 K −5,423 / Yahoo K +13,600)。
所以每天收盤後把雲端 K 存一份到 kline_cloud/,回測用 KSRC=cloud 讀它,和盤中看同一份 K。

  kline_cloud/<代號>/<YYYY-Www>.csv   欄位同 kline/:date,time,open,high,low,close,volume(股)
  kline_cloud/pools/<YYYY-MM-DD>.json  當天交易的名單 + 昨收(模擬器的 POOL 用)

  · 同一個時間標籤出現兩次(開盤撮合 + 09:01 快照都標 09:00)併成一根
  · 沒成交的分鐘不補平 K(和 Worker 一樣);模擬器讀的時候照 App 的 fillFlat 補
  · 已經存過的那天再跑一次 = 覆寫(收盤後 KV 不會再變,覆寫結果一樣)
  · 用法:python save_cloud_bars.py [YYYY-MM-DD](不給 = 台灣今天)
"""
import json, os, sys, csv, datetime, glob, urllib.request

WORKER = "https://twse-proxy.redwinds542688.workers.dev"
ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "kline_cloud")

def tw_today():
    return (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).strftime("%Y-%m-%d")

def week_key(d):
    y, w, _ = datetime.date.fromisoformat(d).isocalendar()
    return f"{y}-W{w:02d}"

def prev_close_from_kline(code, d):
    """kline/(Yahoo)裡這檔在 d 之前最後一根的收盤"""
    best = None
    for f in sorted(glob.glob(os.path.join(ROOT, "kline", code, "*.csv"))):
        with open(f, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if r["date"] < d and (best is None or (r["date"], r["time"]) > best[0]):
                    best = ((r["date"], r["time"]), float(r["close"]))
    return best[1] if best else None

def save_yuanta(d):
    """2026-10-24 使用者「把元大資料作為使用第一首選」:桌機中繼程式推到 Worker 的元大 1 分 K(?ybars=日期)也存一份到 kline_yuanta/<代號>/<週>.csv,
    回測(sim_day.py KSRC 預設 yuanta)就有每天的元大 K。Worker 沒有元大 K(桌機沒開 / 還沒設推送金鑰)就不寫。一天只多 1 次 Worker 請求"""
    try:
        req = urllib.request.Request(f"{WORKER}/?ybars={d}&_={int(datetime.datetime.now().timestamp())}", headers={"User-Agent": "mysh-save-cloud"})
        with urllib.request.urlopen(req, timeout=60) as r:
            j = json.load(r)
    except Exception as e:
        print(f"{d} 讀元大 1 分K 失敗:{e}"); return
    bars = j.get("bars") or {}
    if not bars:
        print(f"{d} Worker 沒有元大 1 分K,不寫"); return
    out = os.path.join(ROOT, "kline_yuanta"); n = 0
    for code, rows in sorted(bars.items()):
        rows = [r for r in rows if "09:00" <= r[0] <= "13:30"]
        if not rows:
            continue
        folder = os.path.join(out, code); os.makedirs(folder, exist_ok=True)
        f = os.path.join(folder, f"{week_key(d)}.csv"); keep = []
        if os.path.exists(f):
            with open(f, encoding="utf-8") as fh:
                keep = [r for r in csv.DictReader(fh) if r["date"] != d]
        with open(f, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh); w.writerow(["date", "time", "open", "high", "low", "close", "volume"])
            for r in keep:
                w.writerow([r["date"], r["time"], r["open"], r["high"], r["low"], r["close"], r["volume"]])
            for t, o, h, l, c, v in rows:
                w.writerow([d, t, o, h, l, c, int(v)])
        n += 1
    print(f"{d} 元大 1 分K 存了 {n} 檔")

def main():
    d = sys.argv[1] if len(sys.argv) > 1 else tw_today()
    save_yuanta(d)
    url = f"{WORKER}/?bars={d}&_={int(datetime.datetime.now().timestamp())}"
    req = urllib.request.Request(url, headers={"User-Agent": "mysh-save-cloud"})
    with urllib.request.urlopen(req, timeout=60) as r:
        j = json.load(r)
    bars = j.get("bars") or {}
    if not bars:
        print(f"{d} 雲端沒有 1 分K(休市或 Worker 沒存),不寫")
        return
    # 名單 + 昨收:pool.json 若還是今天交易用的(date < d)→ 昨收 = close;已換成明天的(date == d)→ 昨收 = prev;都沒有就看 kline/
    pool = json.load(open(os.path.join(ROOT, "pool.json"), encoding="utf-8"))
    P = {s["code"]: s for s in (pool if isinstance(pool, list) else pool.get("pool", []))}
    # 同一天第二次以後跑(16:30 / 20:30 備援):pool.json 已換成明天的名單,今天的名字 / 昨收用第一次存的那份
    old = os.path.join(OUT, "pools", f"{d}.json")
    OLD = {s["code"]: s for s in json.load(open(old, encoding="utf-8"))["pool"]} if os.path.exists(old) else {}
    out_pool = []
    n = 0
    for code, rows in sorted(bars.items()):
        merged = []
        for t, o, h, l, c, v in rows:
            if not ("09:00" <= t <= "13:30"):
                continue
            if merged and merged[-1][0] == t:
                m = merged[-1]; m[2] = max(m[2], h); m[3] = min(m[3], l); m[4] = c; m[5] += v
            else:
                merged.append([t, o, h, l, c, v])
        if not merged:
            continue
        s = P.get(code, {})
        prev = None
        if code in OLD:
            prev = OLD[code]["prev"]; s = s or OLD[code]
        elif s:
            prev = s.get("close") if s.get("date", "") < d else s.get("prev")
        if not prev:
            prev = prev_close_from_kline(code, d)
        if not prev:
            print(f"  {code} 找不到昨收,略過")
            continue
        nm = s.get("name") or (OLD.get(code) or {}).get("name") or code
        out_pool.append({"code": code, "name": nm, "market": s.get("market", "") or (OLD.get(code) or {}).get("market", ""),
                         "close": float(prev), "prev": float(prev), "date": ""})
        folder = os.path.join(OUT, code); os.makedirs(folder, exist_ok=True)
        f = os.path.join(folder, f"{week_key(d)}.csv")
        keep = []
        if os.path.exists(f):
            with open(f, encoding="utf-8") as fh:
                keep = [r for r in csv.DictReader(fh) if r["date"] != d]
        with open(f, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh); w.writerow(["date", "time", "open", "high", "low", "close", "volume"])
            for r in keep:
                w.writerow([r["date"], r["time"], r["open"], r["high"], r["low"], r["close"], r["volume"]])
            for t, o, h, l, c, v in merged:
                w.writerow([d, t, o, h, l, c, int(v)])
        n += 1
    os.makedirs(os.path.join(OUT, "pools"), exist_ok=True)
    json.dump({"date": d, "src": "save_cloud_bars.py", "pool": out_pool},
              open(os.path.join(OUT, "pools", f"{d}.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    print(f"{d} 雲端 1 分K 存了 {n} 檔(快照 {j.get('n')} 個,最後 {j.get('last')})")

if __name__ == "__main__":
    main()
