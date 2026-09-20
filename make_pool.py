# ══════════════════════════════════════════════════════
#  名揚四海 — 每日資料產生器(GitHub Actions 版)
#
#  由 .github/workflows/daily.yml 每個交易日 14:30(台灣時間)自動執行
#  產出:
#    pool.js    給 名揚四海自動交易.html 用(格式與 Colab 版完全相同)
#    pool.json  給 fetch_kline.py 用(多了 market 欄位:twse / tpex)
#
#  資料來源:
#    證交所 OpenAPI  上市全市場日成交 + 當日可現股當沖證券名單(免費、免 token)
#    櫃買 OpenAPI    上櫃全市場日成交 + 現股當沖標的 + 暫停先賣後買預告(免費、免 token)
#    FinMind         官方名單抓不到時的備援(需要 token,放在 GitHub Secrets 的 FINMIND_TOKEN)
#                    + 處置股名單
#
#  硬性規則(不受任何設定影響):
#    1. 只收「上市」或「上櫃」的股票
#    2. 只收 4 碼純數字、不以 0 開頭的代號 → 排除 ETF(0050 等 00xx)、ETN、權證、DR
#    3. 必須在官方「可現股當沖」名單裡;查不到資格的一律不收
#    4. 暫停先賣後買的股票會標記 可先賣後買=0(HTML 端會再排除)
# ══════════════════════════════════════════════════════

import os, json, time, sys
import requests
from datetime import datetime, timedelta, timezone, date

# ── 選股基準(要和 HTML 裡 settings 的 MONITOR_BASE 一致)──
#   前一交易日:收盤價 LO~HI 元、振幅 ≥ MIN_AMP%,依「成交金額」由大到小取前 SIZE 名
LO, HI, MIN_AMP, SIZE = 15, 500, 4.0, 50
MIN_VALUE = 3e8          # 前一交易日成交金額下限(元):3 億,流動性不足的不收
TICK_MAX_PCT = 0.35      # 一檔跳動 ÷ 股價 的上限(%):超過代表一個 tick 就吃掉來回成本
ENTRY_MAX_CHG = 3.0      # 進場時漲跌幅上限(%,寫進 pool.json 給 HTML / 策略層用)

TWSE = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TPEX = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
TWSE_DT = "https://openapi.twse.com.tw/v1/exchangeReport/TWTB4U"          # 上市:當日可現股當沖證券
TWSE_NOTICE = "https://openapi.twse.com.tw/v1/announcement/notice"         # 上市:注意股
TWSE_PUNISH = "https://openapi.twse.com.tw/v1/announcement/punish"         # 上市:處置股
TPEX_NOTICE = "https://www.tpex.org.tw/openapi/v1/tpex_trading_warning_information"  # 上櫃:注意股
TPEX_PUNISH = "https://www.tpex.org.tw/openapi/v1/tpex_disposal_information"         # 上櫃:處置股
TPEX_DT = "https://www.tpex.org.tw/openapi/v1/tpex_securities"            # 上櫃:現股當沖交易標的
TPEX_DT_PAUSE = "https://www.tpex.org.tw/openapi/v1/tpex_intraday_trading_pre"  # 上櫃:暫停先賣後買預告
FM_API = "https://api.finmindtrade.com/api/v4/data"

TOKEN = os.environ.get("FINMIND_TOKEN", "").strip()
TAIPEI = timezone(timedelta(hours=8))

S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0", "Accept": "application/json"})


def now_tw():
    return datetime.now(TAIPEI)


def get_json(url, **kw):
    """帶重試的 GET,失敗三次才放棄"""
    last = None
    for attempt in range(3):
        try:
            r = S.get(url, timeout=60, **kw)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last = e
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"{url}: {last}")


def num(v):
    """'1,234.5' / '--' / '' → float 或 None"""
    if v is None:
        return None
    s = str(v).replace(",", "").strip()
    if s in ("", "--", "-", "X", "除權", "除息", "除權息"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def roc_to_iso(s):
    """'1150919' 或 '115/09/19' → '2026-09-19';解析失敗回傳 None"""
    if not s:
        return None
    s = str(s).strip().replace("/", "").replace("-", "")
    if not s.isdigit():
        return None
    if len(s) == 7:          # ROC 民國年
        y, m, d = int(s[:3]) + 1911, int(s[3:5]), int(s[5:7])
    elif len(s) == 8:        # 已經是西元
        y, m, d = int(s[:4]), int(s[4:6]), int(s[6:8])
    else:
        return None
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return None


def is_stock_code(code):
    """普通股代號:4 碼純數字,且不以 0 開頭(00xx 是 ETF / ETN / 債券 ETF)"""
    return len(code) == 4 and code.isdigit() and code[0] != "0"


def tick_of(p):
    """台股升降單位:一檔跳動的元數"""
    if p < 10:    return 0.01
    if p < 50:    return 0.05
    if p < 100:   return 0.1
    if p < 500:   return 0.5
    if p < 1000:  return 1.0
    return 5.0


def limit_prices(prev):
    """由昨收算漲停價 / 跌停價(±10%,漲停向下取到檔位、跌停向上取到檔位)"""
    up = prev * 1.10
    up = int(up / tick_of(up) + 1e-9) * tick_of(up)
    dn = prev * 0.90
    t = tick_of(dn)
    dn = -int(-dn / t + 1e-9) * t          # ceil 到檔位
    return round(up, 2), round(dn, 2)


def pick(r, keys):
    for k in keys:
        if k in r and r[k] not in (None, ""):
            return r[k]
    return None


def normalize(rows, market):
    """證交所 / 櫃買兩種欄位名統一成同一種 dict"""
    out = []
    for r in rows:
        code = str(pick(r, ["Code", "SecuritiesCompanyCode", "CompanyCode", "StockCode"]) or "").strip()
        if not is_stock_code(code):        # 只留普通股,排除 ETF(00xx)、權證、ETN、DR
            continue
        name = str(pick(r, ["Name", "CompanyName", "SecuritiesCompanyName", "StockName"]) or code).strip()
        close = num(pick(r, ["ClosingPrice", "Close", "ClosePrice"]))
        vol = num(pick(r, ["TradeVolume", "TradingShares", "Volume"]))
        val = num(pick(r, ["TradeValue", "TransactionAmount", "TradingValue", "Amount"]))
        chg = num(pick(r, ["Change", "UpDown", "Spread"]))
        high = num(pick(r, ["HighestPrice", "High", "HighPrice"]))
        low = num(pick(r, ["LowestPrice", "Low", "LowPrice"]))
        dt = roc_to_iso(pick(r, ["Date", "date"]))
        if close is None or vol is None or close <= 0 or vol <= 0:
            continue
        if high is None or low is None or chg is None:
            continue
        prev = close - chg
        if prev <= 0:
            continue
        amp = (high - low) / prev * 100
        up, dn = limit_prices(prev)
        tick_pct = tick_of(close) / close * 100
        out.append({
            "code": code, "name": name, "close": round(close, 2),
            "dir": 1 if chg > 0 else (-1 if chg < 0 else 0),
            "lots": int(vol / 1000),                      # 股 → 張
            "value": round((val if val is not None else close * vol) / 1e8, 2),   # 成交金額(億)
            "amp": round(amp, 2), "market": market, "date": dt,
            "prev": round(prev, 2),                       # 昨收(進場 ±3% 的基準)
            "chg_pct": round(chg / prev * 100, 2),
            "tick_pct": round(tick_pct, 3),               # 一檔 ÷ 股價 %
            "limit_hit": 1 if (close >= up or close <= dn) else 0,   # 收在漲停或跌停
        })
    return out


# ── 1. 上市 + 上櫃 全市場 ────────────────────────
print("抓取證交所(上市)…")
twse = normalize(get_json(TWSE), "twse")
print(f"  上市 {len(twse)} 檔")

tpex = []
try:
    print("抓取櫃買(上櫃)…")
    tpex = normalize(get_json(TPEX), "tpex")
    print(f"  上櫃 {len(tpex)} 檔")
except Exception as e:
    print(f"  櫃買失敗,略過:{e}")

rows = twse + tpex
if not rows:
    sys.exit("兩個交易所都沒讀到資料,停止")

# 資料日期:以資料本身帶的日期為準,沒有就用今天(台灣)
dates = sorted({r["date"] for r in rows if r["date"]})
LAST = dates[-1] if dates else now_tw().strftime("%Y-%m-%d")
print(f"資料日期:{LAST}")

# ── 2. 價格 / 振幅 / 成交金額 / tick 成本 / 漲跌停 粗篩,依成交金額排序 ──
def why_out(r):
    if not (LO <= r["close"] <= HI):  return "價格"
    if r["amp"] < MIN_AMP:            return "振幅"
    if r["value"] * 1e8 < MIN_VALUE:  return "成交金額"
    if r["tick_pct"] > TICK_MAX_PCT:  return "tick成本"
    if r["limit_hit"]:                return "漲跌停"
    return ""

reasons = {}
cand = []
for r in rows:
    w = why_out(r)
    if w:
        reasons[w] = reasons.get(w, 0) + 1
    else:
        cand.append(r)
cand.sort(key=lambda x: -x["value"])
print(f"粗篩後 {len(cand)} 檔;排除原因:" + ", ".join(f"{k} {v}" for k, v in reasons.items()))


# ── 3. 官方「可現股當沖」名單 ──────────────────────
#   elig[code] = (可當沖, 可先賣後買)
#   只有出現在名單裡的才算可當沖;查不到就是不可,不猜。
def field(r, *needles):
    """在一筆紀錄裡找欄位名含有指定字串(不分大小寫)的值"""
    for k, v in r.items():
        kl = k.lower()
        if any(n.lower() in kl for n in needles):
            return str(v or "").strip()
    return ""


def is_flag(v):
    v = str(v or "").strip()
    return v in ("Y", "y", "*", "＊", "V", "v", "1", "是", "暫停")


def parse_daytrade_list(rows):
    """把當沖名單轉成 {code: 可先賣後買}。欄位名兩個交易所不同,用關鍵字找"""
    out = {}
    for r in rows:
        code = field(r, "Code", "代號")
        if not is_stock_code(code):
            continue
        pause = field(r, "Suspension", "Suspend", "Pause", "暫停", "先賣後買", "Remark", "註記")
        out[code] = 0 if is_flag(pause) else 1
    return out


elig = {}
src_note = []

# 上市
twse_ok = False
try:
    m = parse_daytrade_list(get_json(TWSE_DT))
    for c, sf in m.items():
        elig[c] = (1, sf)
    twse_ok = bool(m)
    print(f"上市可當沖名單:{len(m)} 檔(其中暫停先賣後買 {sum(1 for v in m.values() if v == 0)} 檔)")
    src_note.append("證交所 TWTB4U")
except Exception as e:
    print(f"上市當沖名單抓取失敗:{e}")

# 上櫃
tpex_ok = False
try:
    m = parse_daytrade_list(get_json(TPEX_DT))
    for c, sf in m.items():
        elig[c] = (1, sf)
    tpex_ok = bool(m)
    print(f"上櫃可當沖名單:{len(m)} 檔")
    src_note.append("櫃買 tpex_securities")
    try:
        paused = {c for c in parse_daytrade_list(get_json(TPEX_DT_PAUSE))}
        for c in paused:
            if c in elig:
                elig[c] = (1, 0)
        print(f"上櫃暫停先賣後買:{len(paused)} 檔")
    except Exception as e:
        print(f"上櫃暫停先賣後買預告抓取失敗(略過):{e}")
except Exception as e:
    print(f"上櫃當沖名單抓取失敗:{e}")

# ── 3b. 官方注意股 / 處置股名單 ──────────────────
def code_set(rows):
    return {c for c in (field(r, "Code", "代號") for r in rows) if is_stock_code(c)}

attention, disposed = set(), set()
official_punish = False
for label, url, target in (("上市注意股", TWSE_NOTICE, attention), ("上櫃注意股", TPEX_NOTICE, attention),
                           ("上市處置股", TWSE_PUNISH, disposed),  ("上櫃處置股", TPEX_PUNISH, disposed)):
    try:
        got = code_set(get_json(url))
        target |= got
        if "處置" in label:
            official_punish = True
        print(f"{label}:{len(got)} 檔")
    except Exception as e:
        print(f"{label}抓取失敗(略過):{e}")

# ── 4. FinMind 備援:官方名單缺的市場,逐檔查;官方處置股抓不到時才用它 ──
if TOKEN:
    S.headers["Authorization"] = f"Bearer {TOKEN}"

    def fm(dataset, **kw):
        j = {}
        for attempt in range(3):
            r = S.get(FM_API, params={"dataset": dataset, **kw}, timeout=60)
            if r.status_code == 402:
                raise RuntimeError("FinMind 額度用完")
            j = r.json()
            if j.get("status") == 200:
                return j.get("data", [])
            if attempt < 2:
                time.sleep(2)
        raise RuntimeError(f"{dataset}: {j.get('msg')}")

    # 哪個市場的官方名單沒抓到,就只對那個市場的候選股逐檔查(最多查 SIZE*2 檔)
    missing = {m for m, ok in (("twse", twse_ok), ("tpex", tpex_ok)) if not ok}
    need = [s for s in cand if s["market"] in missing and s["code"] not in elig][:SIZE * 2]
    if need:
        print(f"FinMind 備援查詢當沖資格:{len(need)} 檔 …")
        since = str(date.today() - timedelta(days=10))
        for i, s in enumerate(need, 1):
            try:
                d = fm("TaiwanStockDayTrading", data_id=s["code"], start_date=since)
                if d:
                    mark = str(d[-1].get("BuyAfterSale") or "")
                    elig[s["code"]] = (1, 0 if ("＊" in mark or "*" in mark) else 1)
            except Exception as e:
                print(f"  {s['code']} 查詢失敗:{e}")
            if i % 20 == 0:
                print(f"  {i}/{len(need)}")
            time.sleep(0.15)
        src_note.append("FinMind 備援")

    if not official_punish:
        print("官方處置股名單抓不到,改查 FinMind …")
        try:
            for r in fm("TaiwanStockDispositionSecuritiesPeriod",
                        start_date=str(date.today() - timedelta(days=30))):
                if (r.get("period_end") or "") >= LAST:
                    disposed.add(r["stock_id"])
            print(f"  {len(disposed)} 檔處置中")
        except Exception as e:
            print(f"  處置股略過({e})")
else:
    print("未設定 FINMIND_TOKEN:不做備援查詢")

if not elig:
    sys.exit("完全沒有可當沖名單(官方與 FinMind 都失敗),停止,不覆蓋舊資料")

# ── 5. 硬性過濾:可當沖、非注意股、非處置股,才取前 SIZE 檔 ──
n0 = len(cand)
pool = [s for s in cand if s["code"] in elig]
n1 = len(pool)
pool = [s for s in pool if s["code"] not in attention and s["code"] not in disposed]
n2 = len(pool)
pool = pool[:SIZE]
print(f"排除不可當沖 {n0 - n1} 檔、注意/處置股 {n1 - n2} 檔 → 最終 {len(pool)} 檔")

# ── 6. 產生 pool.js(格式與 Colab 版相同)──────────
lines = []
for s in pool:
    dt, dts = elig[s["code"]]
    s["daytrade"], s["short_first"] = dt, dts
    lines.append(
        f"  ['{s['code']}','{s['name']}',{s['close']},{s['dir']},"
        f"{s['lots']},{s['amp']},{dt},{dts}],")

stamp = now_tw().strftime("%Y-%m-%d %H:%M")
js = f"""/* ══════════════════════════════════════════════════════
   監控股票原始股 — 由 GitHub Actions 自動產生
   資料日期:{LAST}
   產生時間:{stamp}(台灣)
   來源:證交所 OpenAPI + 櫃買 OpenAPI(行情與可當沖名單)
   資格來源:{' + '.join(src_note) or '無'}
   規則:僅上市/上櫃 4 碼普通股(無 ETF)、官方可現股當沖名單內、非注意/處置股、
         成交金額 ≥ {MIN_VALUE/1e8:g} 億、一檔跳動 ≤ {TICK_MAX_PCT}%、昨日未收在漲跌停;
         依成交金額排序取前 {SIZE} 檔

   欄位:代號 / 名稱 / 收盤 / 漲跌(1漲 0平 -1跌) /
         成交張數 / 振幅% / 可當沖 / 可先賣後買
   ══════════════════════════════════════════════════════ */
window.RAW_POOL_DATE = '{LAST}';
window.RAW_DISPOSED = {json.dumps(sorted(disposed))};
window.RAW_POOL = [
{chr(10).join(lines)}
];
"""
with open("pool.js", "w", encoding="utf-8") as f:
    f.write(js)

with open("pool.json", "w", encoding="utf-8") as f:
    json.dump({"date": LAST, "generated": stamp,
               "rules": {"lo": LO, "hi": HI, "min_amp": MIN_AMP, "size": SIZE,
                         "min_value": MIN_VALUE, "tick_max_pct": TICK_MAX_PCT,
                         "entry_max_chg": ENTRY_MAX_CHG},
               "disposed": sorted(disposed), "attention": sorted(attention),
               "elig_source": src_note, "pool": pool}, f, ensure_ascii=False, indent=1)

print(f"\n完成:{len(pool)} 檔 → pool.js / pool.json")
print("前 5 名:")
for s in pool[:5]:
    print(f"  {s['name']:<8} {s['close']:>8}  {s['value']:>7} 億  {s['lots']:>7} 張  振幅 {s['amp']}%  tick {s['tick_pct']}%  ({s['market']})")
