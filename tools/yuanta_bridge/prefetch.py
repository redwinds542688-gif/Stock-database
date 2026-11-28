"""2026-11-28 使用者「再找找有哪些可以事先準備的資料 會用到的 先抓下來」:收盤後把「過了就拿不回來」的元大資料先存下來(只讀,不下單)。

元大 1 分 K 只給近 20 天(500017),過了就查不到;所以這支每天跑一次,把下面三樣存在電腦本機(之後要用再上傳 / 回測):
  ① 加權指數 IX0001 元大 1 分 K:今天 + 近 20 天還沒推過的 → 推 Worker ?ixbars=push(Hub ixy:日期)
     → ?twidx / ?ixbars 過去的日子也變元大版(判別式任務 #28 ②「來源一致」);taiex_1m.py 重跑那幾天就會存成元大版
  ② 擴大範圍的個股 1 分 K(不只名單 / pool_full):上市櫃 4 碼股票、收盤 10~1000 元、當天成交 ≥ WIDE_MIN_LOTS 張(預設 1000)
     → 本機 kline_yuanta_wide/<日期>.csv(欄位同 kline_yuanta:code,date,time,open,high,low,close,volume)。不推雲端(量大,先留在電腦)
     每天抓今天,另外往回補還沒抓過的日子(每次最多 WIDE_BACK 天,預設 2),20 天內補齊
  ③ 日 K(元大,可查到 2024 年起):pool_full / 名單 / 擴大範圍的股票,每檔抓一次 2024-01-01 ~ 今天,之後每天只補新的
     → 本機 kline_daily_yuanta/<代號>.csv(date,open,high,low,close,volume)
  ④ 大盤每分鐘量(2026-11-28 使用者「為什麼不存在電腦這邊」):證交所每 5 秒委託成交統計 → 本機 kline_mktvol/<日期>.csv
     (格式同 Stock-database kline_index/MKTVOL;雲端 GitHub 排程也存一份,電腦這份是備份 + 雲端被證交所擋時補)。今天 + 近 40 天還沒存的
用法(桌機,和中繼同一個資料夾;中繼收工後會自己叫,不用手動):python prefetch.py;一次補滿近 20 天:python prefetch.py --back 19(約 2~4 小時,收盤後 / 假日跑)
bridge.env:PREFETCH=0 關掉;WIDE_MIN_LOTS、WIDE_BACK、WIDE_MAX(一天最多幾檔,預設 600)可調
"""
import csv, datetime, json, sys, time, urllib.request
from pathlib import Path
import yuanta_bridge as yb
import hist_kline as hk

HERE = Path(__file__).resolve().parent
WIDE = HERE / "kline_yuanta_wide"; DAILY = HERE / "kline_daily_yuanta"; REC = HERE / "engine_data" / "prefetch_done.json"


def rec_load():
    try: return json.loads(REC.read_text(encoding="utf-8"))
    except Exception: return {}


def rec_save(r):
    try: REC.parent.mkdir(parents=True, exist_ok=True); REC.write_text(json.dumps(r, ensure_ascii=False), encoding="utf-8")
    except Exception as e: yb.log("[預存] 紀錄寫不進去", e)


def weekdays_back(day, n):
    d0 = datetime.date.fromisoformat(day); out = []
    for k in range(1, 40):
        x = d0 - datetime.timedelta(days=k)
        if x.weekday() < 5: out.append(x.isoformat())
        if len(out) >= n: break
    return out


def http_json(u, timeout=30):
    return json.loads(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=timeout).read().decode("utf-8"))


def wide_list():
    """證交所 / 櫃買中心 openapi 今天收盤行情 → [(代號, 'twse'|'otc')],收盤 10~1000、成交 ≥ WIDE_MIN_LOTS 張,依成交量大到小"""
    lots = float(yb.CFG.get("WIDE_MIN_LOTS", "1000") or 1000); out = []
    try:
        for r in http_json("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"):
            c = str(r.get("Code", "")); px = float(str(r.get("ClosingPrice") or 0).replace(",", "") or 0); v = float(str(r.get("TradeVolume") or 0).replace(",", "") or 0)
            if len(c) == 4 and c.isdigit() and 10 <= px <= 1000 and v >= lots * 1000: out.append((v, c, "twse"))
    except Exception as e: yb.log("[預存] 證交所行情抓不到", repr(e)[:120])
    try:
        for r in http_json("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"):
            c = str(r.get("SecuritiesCompanyCode", "")); px = float(str(r.get("Close") or 0).replace(",", "") or 0); v = float(str(r.get("TradingShares") or 0).replace(",", "") or 0)
            if len(c) == 4 and c.isdigit() and 10 <= px <= 1000 and v >= lots * 1000: out.append((v, c, "otc"))
    except Exception as e: yb.log("[預存] 櫃買行情抓不到", repr(e)[:120])
    out.sort(reverse=True); seen = set(); res = []
    for _, c, m in out:
        if c not in seen: seen.add(c); res.append((c, m))
    return res[:int(yb.CFG.get("WIDE_MAX", "600") or 600)]


def mkt_of(b, m):
    return b.Y.enumMarketType.TWOTC if m == "otc" else b.Y.enumMarketType.TWSE


def ix_backfill(b, today, rec):
    """① 加權指數 IX0001 元大 1 分 K → Worker ?ixbars=push(今天 + 近 19 個平日還沒推過的)"""
    if not yb.CFG.get("PUSH_KEY"): return
    done = set(rec.get("ix", []))
    for d in [today] + weekdays_back(today, 19):
        if d in done and d != today: continue
        raw, _ = hk.query(b, yb.IX_CODE, b.Y.enumMarketType.TWSE, d)
        rows = []
        for x in raw:
            dd, hm = hk.ts_of(x[0])
            if dd == d and "09:00" <= hm <= "13:30": rows.append([hm, float(x[1]), float(x[2]), float(x[3]), float(x[4])])
        time.sleep(1.1)
        if not rows:
            yb.log(f"[預存] {d} 加權 1 分 K 沒資料(假日 / 超過 20 天)"); continue
        try:
            rq = urllib.request.Request(yb.CFG["WORKER_URL"].rstrip("/") + "/?ixbars=push", data=json.dumps({"d": d, "bars": rows}).encode(), method="POST",
                                        headers={"Content-Type": "application/json", "X-Push-Key": yb.CFG["PUSH_KEY"]})
            j = json.loads(urllib.request.urlopen(rq, timeout=30).read().decode("utf-8"))
            if j.get("ok"): done.add(d); yb.log(f"[預存] {d} 加權元大 1 分 K {len(rows)} 根推上雲端")
        except Exception as e: yb.log(f"[預存] {d} 加權推不上去", repr(e)[:120])
    rec["ix"] = sorted(done)[-60:]; rec_save(rec)


def wide_fetch(b, today, rec, codes):
    """② 擴大範圍個股 1 分 K → 本機 kline_yuanta_wide/<日期>.csv(今天 + 往回補 WIDE_BACK 天)"""
    mult = float(yb.CFG.get("VOL_MULT", "1000") or 1000); WIDE.mkdir(exist_ok=True)
    done_days = set(rec.get("wide", []))
    nb = int(ARGS.get("back") or yb.CFG.get("WIDE_BACK", "2") or 2)   # 2026-11-28 使用者「為什麼不是 10/6 開始」:python prefetch.py --back 19 一次補滿(元大只給近 20 天)
    back = [d for d in weekdays_back(today, 19) if d not in done_days][:nb]
    for d in [today] + back:
        f = WIDE / f"{d}.csv"; have = set()
        if f.exists():
            with open(f, encoding="utf-8") as fh: have = {r["code"] for r in csv.DictReader(fh)}
        new = not f.exists(); n = miss = 0
        with open(f, "a", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            if new: w.writerow(["code", "date", "time", "open", "high", "low", "close", "volume"])
            for c, m in codes:
                if c in have: continue
                if miss >= 3 and n == 0: yb.log(f"[預存] {d} 前 3 檔都沒資料(假日 / 超過 20 天),跳過"); break
                rows, ok, why = hk.fetch_code(b, c, mkt_of(b, m), d, mult)
                if rows and ok:
                    for r in rows: w.writerow([c, *r])
                    n += 1; miss = -99
                elif not rows: miss += 1
                fh.flush(); time.sleep(0.5)
        if n or miss >= 3: done_days.add(d)
        yb.log(f"[預存] {d} 擴大範圍 1 分 K:新抓 {n} 檔(已有 {len(have)})")
    rec["wide"] = sorted(done_days)[-60:]; rec_save(rec)


def daily_fetch(b, today, rec, codes):
    """③ 元大日 K → 本機 kline_daily_yuanta/<代號>.csv;第一次抓 2024-01-01 起,之後只補最後一天之後"""
    DAILY.mkdir(exist_ok=True); mult = float(yb.CFG.get("VOL_MULT", "1000") or 1000); n = 0
    for c, m in codes:
        f = DAILY / f"{c}.csv"; last = ""
        if f.exists():
            try:
                with open(f, encoding="utf-8") as fh: last = max((r["date"] for r in csv.DictReader(fh)), default="")
            except Exception: last = ""
        if last >= today: continue
        a = (datetime.date.fromisoformat(last) + datetime.timedelta(days=1)).isoformat() if last else "2024-01-01"
        sink = hk.Sink(c); b.kline_out = sink
        b.api.GetKLine(yb.CFG["YT_ACCOUNT"], b.Y.KLineType(11), mkt_of(b, m), c, a.replace("-", "/"), today.replace("-", "/"))
        t0 = time.time(); lastn = -1; still = 0.0
        while time.time() - t0 < 15:
            time.sleep(0.1); k = len(sink)
            if k and k == lastn:
                still += 0.1
                if still >= 1.0: break
            else: still = 0.0
            lastn = k
        b.kline_out = None
        rows = []
        for x in list(sink):
            dd, _ = hk.ts_of(x[0])
            if a <= dd <= today: rows.append([dd, x[1], x[2], x[3], x[4], int(round(float(x[5]) * mult))])
        if rows:
            new = not f.exists()
            with open(f, "a", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                if new: w.writerow(["date", "open", "high", "low", "close", "volume"])
                for r in sorted({r[0]: r for r in rows}.values()): w.writerow(r)
            n += 1
        time.sleep(1.1)
    yb.log(f"[預存] 日 K:更新 {n} 檔")


def mktvol_fetch(today):
    """④ 大盤每分鐘量 → 本機 kline_mktvol/(不用元大連線)"""
    OUT = HERE / "kline_mktvol"; OUT.mkdir(exist_ok=True)
    try: import twse_mktvol as M   # 同資料夾(FILES 有列,win_update 會下載)
    except Exception as e: yb.log("[預存] 大盤量程式沒有(twse_mktvol.py)", repr(e)[:80]); return
    M.OUT = str(OUT); n = 0
    have = {p.stem for p in OUT.glob("*.csv")}
    for i, d in enumerate([today] + [x for x in weekdays_back(today, 40) if x not in have]):
        if i: time.sleep(8)   # 證交所限流
        try: n += bool(M.save(d))
        except Exception as e: yb.log(f"[預存] {d} 大盤量抓不到", repr(e)[:80])
    yb.log(f"[預存] 大盤每分鐘量:存 {n} 天")


ARGS = {}


def main():
    a = sys.argv[1:]
    if "--back" in a: ARGS["back"] = a[a.index("--back") + 1]
    if yb.CFG.get("PREFETCH", "1") != "1" and not ARGS: return
    today = yb.now_tw().strftime("%Y-%m-%d")
    try: mktvol_fetch(today)
    except Exception as e: yb.log("[預存] 大盤量失敗", repr(e)[:120])
    rec = rec_load()
    base = hk.today_list(today)
    wide = wide_list()
    yb.log(f"[預存] 開始:名單 / pool_full {len(base)} 檔、擴大範圍 {len(wide)} 檔")
    b = yb.Bridge(); b.kline_out = None; b.connect()
    try:
        try: ix_backfill(b, today, rec)
        except Exception as e: yb.log("[預存] 加權失敗", repr(e)[:120])
        bs = {c for c, _ in base}
        try: wide_fetch(b, today, rec, [x for x in wide if x[0] not in bs])
        except Exception as e: yb.log("[預存] 擴大範圍失敗", repr(e)[:120])
        seen = set(); allc = []
        for c, m in base + wide:
            if c not in seen: seen.add(c); allc.append((c, m))
        try: daily_fetch(b, today, rec, allc)
        except Exception as e: yb.log("[預存] 日 K 失敗", repr(e)[:120])
    finally:
        try: b.close()
        except Exception: pass
    yb.log("[預存] 完成")


if __name__ == "__main__":
    main()
