"""元大歷史 1 分 K 批次抓取(只讀,不下單)

2026-10-24 使用者:「能拿元大的 1 分 K 嗎」→ 批次抓回測用的股票 / 日期,存成每天一個 CSV,之後拿去回測(雲端 K 缺 24~29%,元大一天 266 根不缺)。

用法(桌機,中繼程式沒在跑的時候:週末或收盤後;同一個帳號不要兩支程式同時登入):
  cd C:\\Yuanta\\bridge
  python hist_kline.py                 # 照 hist_list.json 全部抓
  python hist_kline.py 2026-10-22      # 只抓某一天

輸出:C:\\Yuanta\\bridge\\kline_yuanta\\<日期>.csv,欄位 code,date,time,open,high,low,close,volume(量 = 股,和 kline/ 一樣)
已經抓過的(檔案裡已有那檔)會跳過,可以中斷後重跑。查詢有限制(一秒最多 3 次),這裡每次間隔 0.5 秒。
"""
import json, sys, time, csv
from pathlib import Path
import yuanta_bridge as yb

HERE = Path(__file__).resolve().parent
OUT = HERE / "kline_yuanta"


def ts_of(t):
    """元大 TimeStamp(中繼程式已轉成字串,例如「2026/10/22 下午 01:01:00」或「2026/10/22 13:01:00」)→ ('YYYY-MM-DD', 'HH:MM')。1 分 K 標開始時間"""
    s = str(t).strip()
    parts = s.split()
    d = parts[0].replace("/", "-")
    y, m, dd = d.split("-")
    d = f"{int(y):04d}-{int(m):02d}-{int(dd):02d}"
    ap = next((x for x in parts[1:] if x in ("上午", "下午", "AM", "PM")), "")
    tm = next(x for x in parts[1:] if ":" in x)
    hh, mi = int(tm.split(":")[0]), int(tm.split(":")[1])
    if ap in ("下午", "PM") and hh < 12:
        hh += 12
    if ap in ("上午", "AM") and hh == 12:
        hh = 0
    return d, f"{hh:02d}:{mi:02d}"


def main():
    lst = json.load(open(HERE / "hist_list.json", encoding="utf-8"))
    only = sys.argv[1] if len(sys.argv) > 1 else None
    OUT.mkdir(exist_ok=True)
    mult = float(yb.CFG.get("VOL_MULT", "1000") or 1000)
    b = yb.Bridge()
    b.kline_out = []
    b.connect()
    n_ok = n_empty = 0
    try:
        for day in sorted(lst, reverse=True):   # 新的先抓(元大一分線只給近 20 天)
            if only and day != only:
                continue
            f = OUT / f"{day}.csv"
            done = set()
            if f.exists():
                with open(f, encoding="utf-8") as fh:
                    done = {r["code"] for r in csv.DictReader(fh)}
            new = not f.exists()
            with open(f, "a", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                if new:
                    w.writerow(["code", "date", "time", "open", "high", "low", "close", "volume"])
                miss = 0
                for code, mk in lst[day]:
                    if code in done:
                        continue
                    if miss >= 2:   # 元大一分線只給近 20 天:這天前 2 檔都沒資料 → 整天跳過(500017「僅提供近20天查詢」)
                        yb.log(f"{day} 超過元大一分線查詢範圍,跳過"); break
                    done.add(code)
                    mkt = b.Y.enumMarketType.TWOTC if mk == "otc" else b.Y.enumMarketType.TWSE
                    b.kline_out = []
                    q = day.replace("-", "/")
                    b.api.GetKLine(yb.CFG["YT_ACCOUNT"], b.Y.KLineType(0), mkt, code, q, q)
                    # 等回應:有資料後 0.4 秒沒再增加就算收完;最多等 4 秒
                    t0 = time.time(); last = -1; still = 0.0
                    while time.time() - t0 < 4:
                        time.sleep(0.1)
                        n = len(b.kline_out)
                        if n and n == last:
                            still += 0.1
                            if still >= 0.4:
                                break
                        else:
                            still = 0.0
                        last = n
                    rows = list(b.kline_out)
                    for k in rows:
                        d, hm = ts_of(k[0])
                        w.writerow([code, d, hm, k[1], k[2], k[3], k[4], int(round(float(k[5]) * mult))])
                    if rows:
                        n_ok += 1; miss = -99
                    else:
                        n_empty += 1; miss += 1
                    yb.log(f"{day} {code} {len(rows)} 根")
                    fh.flush()
                    time.sleep(0.5)
    finally:
        b.close()
    yb.log(f"完成:有資料 {n_ok} 檔次,沒資料 {n_empty} 檔次,存在 {OUT}")


if __name__ == "__main__":
    main()
