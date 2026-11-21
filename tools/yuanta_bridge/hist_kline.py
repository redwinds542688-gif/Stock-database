"""元大歷史 1 分 K 批次抓取(只讀,不下單)

2026-10-24 使用者:「能拿元大的 1 分 K 嗎」→ 批次抓回測用的股票 / 日期,存成每天一個 CSV,之後拿去回測(雲端 K 缺 24~29%,元大一天 266 根不缺)。

用法(桌機,中繼程式沒在跑的時候:週末或收盤後;同一個帳號不要兩支程式同時登入):
  cd C:\\Yuanta\\bridge
  python hist_kline.py                 # 照 hist_list.json 全部抓
  python hist_kline.py 2026-10-22      # 只抓某一天
  python hist_kline.py today           # 抓今天:前一天收盤符合選股條件的全部股票(pool_full.json,不設檔數上限)+ pool.json;中繼程式收工後會自己跑
                                       # 2026-10-25 使用者「新進榜的股票 要回溯五天抓k」:今天名單裡、前 5 個交易日還沒有 K 的股票(新進榜)一起補抓,推到 Worker 和原本的合併

輸出:C:\\Yuanta\\bridge\\kline_yuanta\\<日期>.csv,欄位 code,date,time,open,high,low,close,volume(量 = 股,和 kline/ 一樣)
已經抓過的(檔案裡已有那檔)會跳過,可以中斷後重跑。查詢有限制(一秒最多 3 次),這裡每次間隔 0.5 秒。
2026-11-22(D5):檔案裡已有但不完整的會重查;查回來不完整的不存;推 Worker 只補缺的分鐘(見 push_day / fetch_code)。
"""
import json, sys, time, csv, urllib.request, urllib.error, datetime
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


RAW = "https://raw.githubusercontent.com/redwinds542688-gif/Stock-database/main/"


def today_list(day):
    """2026-10-25 使用者「符合條件一律收進來…預先抓好抓滿」:今天 = pool_full.json(前一天收盤符合條件的全部)+ pool.json(名單)"""
    out, seen = [], set()
    for name in ("pool.json", "pool_full.json"):
        try:
            j = json.loads(yb.gh_get(name).decode("utf-8"))   # 2026-10-29 16:16 經 Worker 代讀(repo 改私人也讀得到)
        except Exception as e:
            yb.log(f"讀 {name} 失敗", e); continue
        if str(j.get("date", "")) >= day:          # 已經換成明天的名單(今天收盤選的)→ 不是今天交易的那份
            # 2026-10-27 21:30 改用早上中繼程式存在本機的今天名單(pool_今天.json);沒有才略過
            loc = HERE / f"pool_{day}.json"
            if name == "pool.json" and loc.exists():
                try: j = json.loads(loc.read_text(encoding="utf-8")); yb.log(f"{name} 已換成明天的,改用早上存的今天名單 {j.get('date', '')}")
                except Exception as e: yb.log("讀本機今天名單失敗", e); continue
            else:
                yb.log(f"{name} 日期 {j.get('date')} 不是今天以前的,略過"); continue
        for s in j.get("pool", []):
            c = str(s["code"])
            if c not in seen:
                seen.add(c); out.append([c, "otc" if s.get("market") in ("tpex", "otc") else "twse"])
    return out


def worker_has(day):
    """2026-11-22 使用者「全部做」(D5):Worker 那天已經有的 {(分鐘, 代號)}(GET ?ybars=<日期>,和 save_cloud_bars.py 一樣不用金鑰);抓不到回傳 None"""
    try:
        req = urllib.request.Request(yb.CFG["WORKER_URL"].rstrip("/") + f"/?ybars={day}&_={int(time.time())}", headers={"User-Agent": "yuanta-bridge/1"})
        j = json.loads(urllib.request.urlopen(req, timeout=60).read().decode("utf-8"))
        return {(r[0], c) for c, rows in (j.get("bars") or {}).items() for r in rows if r}
    except Exception as e:
        yb.log(f"讀 Worker {day} 已有的元大 K 失敗", e); return None


def push_day(day, codes=None):
    """2026-10-25 使用者「現在抓股票 都以元大為準」:今天抓好的元大 1 分 K 推到 Worker(?ybars=push,一分鐘一筆、全部股票),
    14:30 排程 save_cloud_bars.py 存進 repo 的 kline_yuanta/,選股(make_pool.py)算「昨天成交分鐘數」就用元大的。沒有 PUSH_KEY 就不推(只留本機)
    2026-11-22 使用者「全部做」(D5):原本今天那份不帶 merge → Worker INSERT OR REPLACE 整分鐘換掉,盤中中繼推的元大 K(含不在這份名單的股票)
    會被這裡(可能不完整的)資料蓋掉;帶 merge 也會把同一檔那分鐘換掉。改成:先讀 Worker 那天已經有的 (分鐘, 代號),
    只推 Worker 沒有的,而且一律 merge = 1(和那分鐘原本的股票合併)→ 只補缺的分鐘,絕不覆蓋已有的。讀不到 Worker 就不推(寧可少補不要蓋錯)"""
    if not yb.CFG.get("PUSH_KEY") or not yb.CFG.get("WORKER_URL"):
        yb.log("沒有 PUSH_KEY,今天的元大 K 只留本機,不推 Worker"); return
    f = OUT / f"{day}.csv"
    if not f.exists():
        return
    have = worker_has(day)
    if have is None:
        yb.log(f"{day} 讀不到 Worker 現有的 K,這次不推(避免蓋掉盤中那份)"); return
    by = {}; skip = 0
    with open(f, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["date"] == day and (codes is None or r["code"] in codes):
                if (r["time"], r["code"]) in have:
                    skip += 1; continue
                by.setdefault(r["time"], {})[r["code"]] = [float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]), int(float(r["volume"]))]
    ok = 0
    for m in sorted(by):
        body = json.dumps({"d": day, "m": m, "bars": by[m], "merge": 1}).encode()   # 一律合併:只加 Worker 那分鐘沒有的股票
        req = urllib.request.Request(yb.CFG["WORKER_URL"].rstrip("/") + "/?ybars=push", data=body, method="POST",
                                     headers={"Content-Type": "application/json", "X-Push-Key": yb.CFG["PUSH_KEY"]})
        for k in range(3):
            try:
                if json.loads(urllib.request.urlopen(req, timeout=15).read().decode()).get("ok"):
                    ok += 1
                break
            except urllib.error.HTTPError as e:
                # 2026-10-27 16:35 被拒時寫出 Worker / Cloudflare 回的原因(error code 1010 = Cloudflare 擋連線身分;{"error":"金鑰不對"} = PUSH_KEY 不對);401 / 403 重試也沒用
                try: why = e.read().decode("utf-8", "replace")[:200].replace("\n", " ")
                except Exception: why = ""
                yb.log(f"推送 {m} 失敗 第 {k + 1} 次 HTTP {e.code}:{why}")
                if e.code in (401, 403):
                    break
                time.sleep(2)
            except Exception as e:
                yb.log(f"推送 {m} 失敗 第 {k + 1} 次", e); time.sleep(2)
        time.sleep(0.05)
    yb.log(f"{day} 元大 1 分 K 推到 Worker:{ok}/{len(by)} 分鐘(Worker 已有、不覆蓋 {skip} 筆)" + (f"(補抓 {len(codes)} 檔)" if codes is not None else ""))


# ── 2026-11-22 使用者「全部做」(D5):查 K 的回應處理 ─────────────────────────────
#   原本:有資料後 0.4 秒沒增加就算收完、最多等 4 秒 → 慢的回應被截斷 / 當成沒資料;
#         b.kline_out 沒標是哪一檔 → 上一檔晚到的回應會寫進下一檔;
#         檔案裡已經有的代號一律跳過 → 截斷 / 混到的資料永遠不會被修正。
#   改成:
#     · 每次查詢用自己的收集器(Sink,記著代號);收完後換成「晚到收集器」再等一下,上一檔有晚到的回應 → 那檔作廢重查(不會混進下一檔)
#     · 等待:有資料後 QUIET 秒沒增加才算收完,最多等 MAX_WAIT 秒
#     · 完整性:同一分鐘不能重複(重複 = 混到別檔)、要有 09:05 以前的 K 和 13:30 收盤那根(今天收盤前跑的只檢查不重複)
#       不完整 → 重查(最多 TRIES 次);連續兩次結果一模一樣就當作那天真的長這樣(冷門 / 暫停交易),照樣收
#     · 檔案裡已有、但不完整的代號 → 重查,收完把舊的那幾行換掉(compact)
QUIET, MAX_WAIT, LATE_WAIT, TRIES = 1.0, 15.0, 1.0, 3


class Sink(list):
    def __init__(self, code):
        super().__init__(); self.code = code


def rows_ok(times, day):
    """times = 這檔這天所有 K 的 HH:MM(含重複);回傳 (完整?, 原因)"""
    if not times:
        return False, "沒資料"
    if len(set(times)) != len(times):
        return False, "同一分鐘重複(混到別檔的回應)"
    if day == yb.now_tw().strftime("%Y-%m-%d") and yb.now_tw().strftime("%H:%M") < "13:35":
        return True, ""
    if min(times) > "09:05":
        return False, f"第一根 {min(times)}(缺開盤)"
    if "13:30" not in times:
        return False, f"最後一根 {max(times)}(缺 13:30 收盤)"
    return True, ""


def query(b, code, mkt, day):
    """查一檔一天;回傳 (rows, late):rows = 原始 K 列表,late = 收完後還有晚到的回應(這次結果不可信)"""
    sink = Sink(code); b.kline_out = sink
    q = day.replace("-", "/")
    b.api.GetKLine(yb.CFG["YT_ACCOUNT"], b.Y.KLineType(0), mkt, code, q, q)
    t0 = time.time(); last = -1; still = 0.0
    while time.time() - t0 < MAX_WAIT:
        time.sleep(0.1)
        n = len(sink)
        if n and n == last:
            still += 0.1
            if still >= QUIET:
                break
        else:
            still = 0.0
        last = n
    late = Sink(code); b.kline_out = late      # 之後才到的回應進這裡,不會混進下一檔
    time.sleep(LATE_WAIT)
    b.kline_out = None
    return list(sink), len(late) > 0


def fetch_code(b, code, mkt, day, mult):
    """查到完整為止(最多 TRIES 次);回傳 (rows[(d, hm, o, h, l, c, 股數)], 完整?, 原因)"""
    prev = None; why = ""; lastr = []
    for k in range(TRIES):
        raw, late = query(b, code, mkt, day)
        rows = []
        for x in raw:
            d, hm = ts_of(x[0])
            if d == day:
                rows.append((d, hm, x[1], x[2], x[3], x[4], int(round(float(x[5]) * mult))))
        lastr = rows or lastr
        if late:
            why = "收完後還有晚到的回應"; yb.log(f"{day} {code} {why},重查"); prev = None; time.sleep(0.5); continue
        ok, why = rows_ok([r[1] for r in rows], day)
        if ok:
            return rows, True, ""
        if not rows:
            return rows, False, why           # 沒資料(超過查詢範圍 / 那天沒交易):不重查,交給 miss 計數
        if prev is not None and prev == rows:
            return rows, True, f"兩次結果相同,照收({why})"
        prev = rows
        yb.log(f"{day} {code} 不完整:{why},重查 {k + 1}/{TRIES - 1}" if k < TRIES - 1 else f"{day} {code} 不完整:{why}")
        time.sleep(0.5)
    return prev or lastr, False, why     # 有資料但一直不完整 → 呼叫端不存檔(下次重查),也不算「沒資料」


def compact(f, replaced):
    """replaced 的代號:只留這次新寫的那一份(同一 code+date+time 留最後一筆)"""
    if not replaced or not f.exists():
        return
    with open(f, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    keep = {}
    for r in rows:
        keep[(r["code"], r["date"], r["time"])] = r          # 後寫的(這次重查的)蓋掉前面的
    tmp = f.with_suffix(".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh); w.writerow(["code", "date", "time", "open", "high", "low", "close", "volume"])
        for r in sorted(keep.values(), key=lambda r: (r["code"], r["date"], r["time"])):
            w.writerow([r["code"], r["date"], r["time"], r["open"], r["high"], r["low"], r["close"], r["volume"]])
    tmp.replace(f)


def main():
    lst = json.load(open(HERE / "hist_list.json", encoding="utf-8"))
    only = sys.argv[1] if len(sys.argv) > 1 else None
    back = []
    if only == "today":
        only = yb.now_tw().strftime("%Y-%m-%d")
        lst = {only: today_list(only)}
        yb.log(f"今天 {only} 要抓 {len(lst[only])} 檔")
        # 2026-10-25 使用者「新進榜的股票 要回溯五天抓k」:往前找平日(最多 9 個),同一份名單,已經有的會跳過 → 只補新進榜的;
        #   遇到假日(前 2 檔都沒資料)那天不算,湊滿 5 個交易日為止
        d0 = datetime.date.fromisoformat(only)
        for k in range(1, 14):
            x = d0 - datetime.timedelta(days=k)
            if x.weekday() < 5:
                back.append(x.isoformat())
            if len(back) >= 9:
                break
        for x in back:
            lst[x] = lst[only]
    OUT.mkdir(exist_ok=True)
    mult = float(yb.CFG.get("VOL_MULT", "1000") or 1000)
    b = yb.Bridge()
    b.kline_out = None
    b.connect()
    n_ok = n_empty = n_bad = 0
    try:
        got = {}; n_trade = 0
        for day in sorted(lst, reverse=True):   # 新的先抓(元大一分線只給近 20 天)
            if only and day != only and day not in back:
                continue
            if day in back and n_trade >= 5:
                break
            f = OUT / f"{day}.csv"
            have = {}
            if f.exists():
                with open(f, encoding="utf-8") as fh:
                    for r in csv.DictReader(fh):
                        have.setdefault(r["code"], []).append(r["time"])
            # 2026-11-22(D5):檔案裡已有、而且完整的才跳過;不完整(以前被截斷 / 混到別檔)的重查
            done = {c for c, ts in have.items() if rows_ok(ts, day)[0]}
            redo = set(have) - done
            if redo:
                yb.log(f"{day} 檔案裡 {len(redo)} 檔不完整,重查:{' '.join(sorted(redo)[:20])}")
            had = set(have)   # 之前就抓到的(有 K 才會寫進檔)
            new = not f.exists()
            replaced = set()
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
                    rows, ok, why = fetch_code(b, code, mkt, day, mult)
                    if rows and ok:
                        for r in rows:
                            w.writerow([code, *r])
                        if code in have:
                            replaced.add(code)
                        n_ok += 1; miss = -99; got.setdefault(day, set()).add(code)
                    elif rows:
                        n_bad += 1; miss = -99   # 有資料但不完整:不寫檔(下次再重查),不推 Worker
                    else:
                        n_empty += 1; miss += 1
                    yb.log(f"{day} {code} {len(rows)} 根" + ("" if ok else f"(不完整,不存:{why})" if rows else "") + (f"({why})" if ok and why else ""))
                    fh.flush()
                    time.sleep(0.5)
            compact(f, replaced)
            if day in back and (had or got.get(day)):
                n_trade += 1   # 這天有交易(之前就有檔、或這次抓到)才算一個交易日
    finally:
        b.close()
    yb.log(f"完成:有資料 {n_ok} 檔次,不完整沒存 {n_bad} 檔次,沒資料 {n_empty} 檔次,存在 {OUT}")
    if len(sys.argv) > 1 and sys.argv[1] == "today":
        push_day(only)   # 2026-11-22(D5):只補 Worker 沒有的分鐘,不覆蓋
        for day in back:
            if got.get(day):
                push_day(day, got[day])   # 只推這次補抓到的新進榜股票,和 Worker 原本的合併


if __name__ == "__main__":
    main()
