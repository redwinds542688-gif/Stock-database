"""2026-11-28 使用者「大盤的量呢」:加權指數 1 分 K 的量都是 0 → 改存證交所「每 5 秒委託成交統計」(MI_5MINS,過去日子也查得到)
轉成每分鐘 → kline_index/MKTVOL/<日期>.csv
欄位:time(那一分鐘結束,09:01 = 09:00:00~09:01:00)、vol(這分鐘成交張數)、amt(成交金額 百萬元)、trades(成交筆數)、
      buy_n / buy_q / sell_n / sell_q(這分鐘新增的委託買進筆數 / 張數、委託賣出筆數 / 張數)、cum_vol / cum_amt(累積)
用法:python tools/twse_mktvol.py [日期 ...](不給 = 補 kline_futures/TXF 有、MKTVOL 還沒有的日子 + 今天)"""
import csv, glob, json, os, sys, time, datetime, urllib.request
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); OUT = os.path.join(HERE, "kline_index", "MKTVOL")
def num(s): return float(str(s).replace(",", "") or 0)
def fetch(d):
    u = f"https://www.twse.com.tw/exchangeReport/MI_5MINS?response=json&date={d.replace('-', '')}"
    j = json.loads(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=30).read().decode("utf-8"))
    if j.get("stat") != "OK" or not j.get("data"): return None
    last = {}; rows = []
    for r in j["data"]:
        t = r[0]; v = [num(x) for x in r[1:]]   # 買筆 買量 賣筆 賣量 成交筆 成交量 成交金額(累積)
        hh, mm, ss = map(int, t.split(":")); k = hh * 60 + mm + (1 if ss else 0); m = f"{k // 60:02d}:{k % 60:02d}"
        last[m] = v
    prev = None
    for m in sorted(last):
        v = last[m]
        if prev is None: prev = v; continue
        dv = [a - b for a, b in zip(v, prev)]
        rows.append([m, int(dv[5]), round(dv[6], 2), int(dv[4]), int(dv[0]), int(dv[1]), int(dv[2]), int(dv[3]), int(v[5]), round(v[6], 2)]); prev = v
    return rows
def save(d):
    rows = fetch(d)
    if not rows: print(d, "沒資料(假日 / 還沒收盤)"); return False
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, f"{d}.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["time", "vol", "amt", "trades", "buy_n", "buy_q", "sell_n", "sell_q", "cum_vol", "cum_amt"]); w.writerows(rows)
    print(d, "大盤每分鐘量", len(rows), "分鐘,總成交", rows[-1][8], "張", rows[-1][9], "百萬"); return True
if __name__ == "__main__":
    ds = sys.argv[1:]
    if not ds:
        have = {os.path.basename(x)[:-4] for x in glob.glob(os.path.join(OUT, "*.csv"))}
        tx = set()
        for f in glob.glob(os.path.join(HERE, "kline_futures", "TXF", "*.csv")):
            tx |= {r["date"] for r in csv.DictReader(open(f, encoding="utf-8"))}
        ds = sorted(d for d in tx if d not in have) + [datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y-%m-%d")]
    for i, d in enumerate(dict.fromkeys(ds)):
        if i: time.sleep(8)   # 證交所限流(太快會 307 / 斷線)
        try: save(d)
        except Exception as e: print(d, "抓不到", repr(e)[:120])
