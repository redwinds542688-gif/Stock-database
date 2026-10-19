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
#    5. 排除金融保險業(產業別 17:金控、銀行、保險、證券)
#    6. 最近 5 個交易日平均振幅 ≥ AVG5_MIN_AMP(振幅歷史存在 amp_history.json,
#       不足 5 天時向 Yahoo 補抓日K)
# ══════════════════════════════════════════════════════

import os, json, time, sys
import requests
from datetime import datetime, timedelta, timezone, date

# ── 選股基準(要和 HTML 裡 settings 的 MONITOR_BASE 一致)──
#   前一交易日:收盤價 LO~HI 元、振幅 ≥ MIN_AMP%,依「成交張數」由大到小取前 SIZE 名
LO, HI, MIN_AMP, SIZE = 15, 500, 4.0, 130   # 2026-10-17 100→130:App 端再以監控高價(300)/多空上限、禁止先賣後買篩掉約 10~15 檔,留 130 才能真的監控到 100
TICK_MAX_PCT = 0.5       # 一檔跳動 ÷ 股價 的上限(%):超過代表一個 tick 就吃掉來回成本。2026-10-19 0.35 → 0.5(使用者「判一,二都改 0.5」):0.35 會把 100~142 元整段擋掉,回測五天判一 +1 筆 +2,402、判二 +3 筆 2/1 +3,632
ENTRY_MAX_CHG = 3.5      # 進場時漲跌幅上限(%,寫進 pool.json 給 HTML / 策略層用;2026-10-15 由 3 改 5,2026-10-16 回測 10/13~16 後改 3.5)
AVG5_MIN_AMP = 4.0       # 最近 5 個交易日「平均振幅」下限(%):擋掉只有昨天偶然動一次的股票
AVG5_MIN_HITS = 3        # 最近 5 個交易日裡,至少要有幾天振幅 ≥ MIN_AMP:擋掉「平常不動、只靠一根大跌把平均撐起來」的股票(例:緯創)
AVG_DAYS = 5
HIST_FILE = "amp_history.json"   # 每日振幅滾動存檔(全市場,保留最近 15 個交易日)
EXCLUDE_INDUSTRY = {"17"}   # 排除的產業別代碼:17 = 金融保險業(金控、銀行、保險、證券)
# 官方產業別抓不到時的備援:代號 28xx 與這幾檔都是金融股
FIN_CODES_FALLBACK = {"5820", "5871", "5876", "5878", "5880", "6005", "6024", "2820"}

# 2026-10-15 實測:OpenAPI 的「日成交資訊」更新很慢——證交所要隔天早上才換成前一天的資料,
# 櫃買傍晚才換。14:30 跑的時候拿到的是前一天的價,pool.json 卻標成當天(上市 43 檔是前一天、
# 上櫃 7 檔是當天,日期取了最大值)。所以改成優先用兩家的「盤後行情」接口(當天 14:00 / 15:00 前後
# 就有),OpenAPI 只當備援;而且上市、上櫃的資料日不一致時,把舊的那個市場剔除,不混用。
TWSE = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"            # 上市:OpenAPI(備援,慢一天)
TPEX = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"   # 上櫃:OpenAPI(備援,傍晚才更新)
TWSE_TODAY = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"             # 上市:當天盤後行情(約 14:00 就有)
TPEX_TODAY = "https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes"       # 上櫃:當天盤後行情(約 15:00 後有)
TPEX_WAIT_TRIES = int(os.environ.get("TPEX_WAIT_TRIES", 4))   # 櫃買盤後還沒出來時,最多再等幾次(測試可設 0)
TPEX_WAIT_SEC = 300                                            # 每次等 5 分鐘
TWSE_DT = "https://openapi.twse.com.tw/v1/exchangeReport/TWTB4U"          # 上市:當日可現股當沖證券
TWSE_NOTICE = "https://openapi.twse.com.tw/v1/announcement/notice"         # 上市:注意股
TWSE_PUNISH = "https://openapi.twse.com.tw/v1/announcement/punish"         # 上市:處置股
TPEX_NOTICE = "https://www.tpex.org.tw/openapi/v1/tpex_trading_warning_information"  # 上櫃:注意股
TPEX_PUNISH = "https://www.tpex.org.tw/openapi/v1/tpex_disposal_information"         # 上櫃:處置股
TWSE_INFO = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"           # 上市:公司基本資料(產業別)
TPEX_INFO = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"        # 上櫃:公司基本資料(產業別)
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
            "limit_hit": 1 if close <= dn else 0,   # 收在跌停(2026-10-19 使用者選「1」:漲停放行、跌停照擋;原本漲停也算)
            "limit_up": 1 if close >= up else 0,    # 收在漲停(只記錄,不再剔除:隔天常開低洗盤,台虹 10/19 就是空單機會)
        })
    return out


# ── 1. 上市 + 上櫃 全市場 ────────────────────────
def twse_today(ymd):
    """證交所當天盤後行情 MI_INDEX(ymd = 'YYYYMMDD')→ 轉成和 OpenAPI 同樣的欄位;沒有資料回傳 []"""
    j = get_json(TWSE_TODAY, params={"response": "json", "date": ymd, "type": "ALLBUT0999"})
    if str(j.get("stat", "")).upper() != "OK":
        return []
    for t in j.get("tables", []):
        f = [str(x).strip() for x in (t.get("fields") or [])]
        if "證券代號" not in f or not t.get("data"):
            continue
        ix = {k: i for i, k in enumerate(f)}
        out = []
        for r in t["data"]:
            sign_html = str(r[ix["漲跌(+/-)"]])
            sign = -1 if "-" in sign_html else (1 if "+" in sign_html else 0)
            chg = num(r[ix["漲跌價差"]])
            out.append({"Code": r[ix["證券代號"]], "Name": r[ix["證券名稱"]],
                        "TradeVolume": r[ix["成交股數"]], "TradeValue": r[ix["成交金額"]],
                        "HighestPrice": r[ix["最高價"]], "LowestPrice": r[ix["最低價"]],
                        "ClosingPrice": r[ix["收盤價"]],
                        "Change": (sign * chg) if chg is not None else "",
                        "Date": ymd})
        return out
    return []


def tpex_today(ymd_slash):
    """櫃買當天盤後行情 dailyQuotes(ymd_slash = 'YYYY/MM/DD')→ 轉成和 OpenAPI 同樣的欄位;沒有資料回傳 []"""
    j = get_json(TPEX_TODAY, params={"date": ymd_slash, "response": "json"})
    if str(j.get("stat", "")).lower() != "ok":
        return []
    tabs = j.get("tables") or []
    t = tabs[0] if tabs else {}
    f = [str(x).strip() for x in (t.get("fields") or [])]
    if "代號" not in f or not t.get("data"):
        return []
    ix = {k: i for i, k in enumerate(f)}
    out = []
    for r in t["data"]:
        out.append({"Code": r[ix["代號"]], "Name": r[ix["名稱"]],
                    "Close": r[ix["收盤"]], "Change": r[ix["漲跌"]],
                    "High": r[ix["最高"]], "Low": r[ix["最低"]],
                    "TradingShares": r[ix["成交股數"]], "TransactionAmount": r[ix["成交金額(元)"]],
                    "Date": str(j.get("date") or "").replace("/", "")})
    return out


TODAY8 = now_tw().strftime("%Y%m%d")

# 要抓哪一天的盤後行情:14:00 以前(含半夜過了 0 點以後)是「上一個交易日」,不是今天。
# 2026-10-16 01:16 那次排程延遲到半夜才跑,用 20261016 去要盤後行情當然是 0 筆,退回 OpenAPI 又拿到上市 10/14、上櫃 10/15,
# 結果把上市整個丟掉、池子變成 100 檔全是上櫃。所以改成:先試該試的日子,沒有再往前一天(最多 4 天,跳過週末),都沒有才用 OpenAPI。
def candidate_days(n=4):
    d = now_tw()
    if d.hour < 14: d = d - timedelta(days=1)
    out = []
    while len(out) < n:
        if d.weekday() < 5: out.append(d)
        d = d - timedelta(days=1)
    return out
twse = []
twse = []
for d in candidate_days():
    ymd = d.strftime("%Y%m%d")
    print(f"抓取證交所(上市)盤後行情 {ymd}…")
    try:
        twse = normalize(twse_today(ymd), "twse")
        print(f"  上市(盤後){len(twse)} 檔")
    except Exception as e:
        print(f"  盤後行情失敗:{e}")
    if twse: break
if not twse:
    print("  當天盤後行情還沒有(假日?),改用 OpenAPI(注意:它常慢一天)…")
    twse = normalize(get_json(TWSE), "twse")
    print(f"  上市(OpenAPI){len(twse)} 檔")

tpex = []
TPEX_DAY = candidate_days(1)[0]
print(f"抓取櫃買(上櫃)盤後行情 {TPEX_DAY.strftime('%Y/%m/%d')}…")
for attempt in range(TPEX_WAIT_TRIES + 1):
    tpex = []
    for d in candidate_days():                       # 先試該試的那天,沒有再往前
        try:
            tpex = normalize(tpex_today(d.strftime("%Y/%m/%d")), "tpex")
        except Exception as e:
            print(f"  盤後行情失敗:{e}")
            tpex = []
        if tpex: break
    if tpex or attempt == TPEX_WAIT_TRIES:
        break
    # 只有「今天是交易日且上市已有當天資料、現在是 14:00~17:00」才值得等;其他情況直接備援
    hh = now_tw().strftime("%H:%M")
    if not (twse and twse[0]["date"] == now_tw().strftime("%Y-%m-%d") and "14:00" <= hh <= "17:00"):
        break
    print(f"  櫃買盤後行情還沒出來,{TPEX_WAIT_SEC // 60} 分鐘後再試({attempt + 1}/{TPEX_WAIT_TRIES})…")
    time.sleep(TPEX_WAIT_SEC)
if tpex:
    print(f"  上櫃(盤後){len(tpex)} 檔")
else:
    try:
        print("  當天盤後行情還沒有,改用 OpenAPI(注意:它傍晚才更新)…")
        tpex = normalize(get_json(TPEX), "tpex")
        print(f"  上櫃(OpenAPI){len(tpex)} 檔")
    except Exception as e:
        print(f"  櫃買失敗,略過:{e}")

if not twse and not tpex:
    sys.exit("兩個交易所都沒讀到資料,停止")

# 資料日期:以資料本身帶的日期為準。上市、上櫃日期不一致時,只留最新那天的市場,
# 不把前一天的價混進來(之前就是這樣把 10/13 的價標成 10/14)
def market_date(lst):
    ds = sorted({r["date"] for r in lst if r["date"]})
    return ds[-1] if ds else None
d_twse, d_tpex = market_date(twse), market_date(tpex)
print(f"資料日:上市 {d_twse}、上櫃 {d_tpex}")
LAST = max(d for d in (d_twse, d_tpex) if d) if (d_twse or d_tpex) else now_tw().strftime("%Y-%m-%d")
if d_twse and d_twse != LAST:
    print(f"  ⚠ 上市資料({d_twse})比上櫃舊,這次不用上市的資料")
    twse = []
if d_tpex and d_tpex != LAST:
    print(f"  ⚠ 上櫃資料({d_tpex})比上市舊,這次不用上櫃的資料")
    tpex = []
rows = [r for r in twse + tpex if r["date"] == LAST]
print(f"資料日期:{LAST},共 {len(rows)} 檔")

# ── 1b. 產業別(用來排除金融保險業)────────────────
def field_any(r, *needles):
    for k, v in r.items():
        kl = k.lower()
        if any(n.lower() in kl for n in needles):
            return str(v or "").strip()
    return ""

industry = {}
for label, url in (("上市", TWSE_INFO), ("上櫃", TPEX_INFO)):
    try:
        n = 0
        for r in get_json(url):
            code = field_any(r, "公司代號", "SecuritiesCompanyCode", "Code")
            ind = field_any(r, "產業別", "IndustryCode", "Industry")
            if code and ind:
                industry[code] = ind.zfill(2)
                n += 1
        print(f"{label}產業別:{n} 檔")
    except Exception as e:
        print(f"{label}產業別抓取失敗(改用代號備援):{e}")


def is_financial(r):
    ind = industry.get(r["code"])
    if ind is not None:
        return ind in EXCLUDE_INDUSTRY
    return r["code"].startswith("28") or r["code"] in FIN_CODES_FALLBACK   # 備援


for r in rows:
    r["industry"] = industry.get(r["code"], "")

# ── 2. 價格 / 振幅 / tick 成本 / 跌停 / 金融股 粗篩,依成交張數排序 ──(漲停 2026-10-19 起放行)
def why_out(r):
    if is_financial(r):               return "金融股"
    if not (LO <= r["close"] <= HI):  return "價格"
    if r["amp"] < MIN_AMP:            return "振幅"
    if r["tick_pct"] > TICK_MAX_PCT:  return "tick成本"
    if r["limit_hit"]:                return "跌停"      # 2026-10-19 起只擋跌停,漲停放行(使用者選「1」)
    return ""

reasons = {}
cand = []
for r in rows:
    w = why_out(r)
    if w:
        reasons[w] = reasons.get(w, 0) + 1
    else:
        cand.append(r)
cand.sort(key=lambda x: -x["lots"])
print(f"粗篩後 {len(cand)} 檔;排除原因:" + ", ".join(f"{k} {v}" for k, v in reasons.items()))


# ── 2b. 振幅歷史:今天全市場的振幅寫進 amp_history.json ──
#   結構 {code: {"2026-09-18": 4.75, ...}},只留最近 15 個交易日
hist = {}
if os.path.exists(HIST_FILE):
    try:
        with open(HIST_FILE, encoding="utf-8") as f:
            hist = json.load(f)
    except Exception:
        hist = {}
for r in rows:
    hist.setdefault(r["code"], {})[LAST] = r["amp"]
for code in list(hist):
    # 只留 ≤ 資料日期的日子(擋掉 Yahoo 未完成 K 棒之類的未來日期),且最多 15 個交易日
    ds = sorted(d for d in hist[code] if d <= LAST)[-15:]
    hist[code] = {d: hist[code][d] for d in ds}


def yahoo_daily_amp(code, market):
    """向 Yahoo 抓最近一個月日K,回傳 {date: 振幅%};失敗回傳 {}"""
    out = {}
    for suf in ([".TW", ".TWO"] if market == "twse" else [".TWO", ".TW"]):
        try:
            r = S.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{code}{suf}",
                      params={"interval": "1d", "range": "1mo"}, timeout=30)
            j = r.json()
            res = (j.get("chart", {}).get("result") or [None])[0]
            if not res or not res.get("timestamp"):
                continue
            q = res["indicators"]["quote"][0]
            ts, hi, lo, cl = res["timestamp"], q["high"], q["low"], q["close"]
            for i in range(1, len(ts)):
                if None in (hi[i], lo[i], cl[i - 1]) or not cl[i - 1]:
                    continue
                d = datetime.fromtimestamp(ts[i], TAIPEI).strftime("%Y-%m-%d")
                a = (hi[i] - lo[i]) / cl[i - 1] * 100
                if d > LAST or a <= 0 or a > 25:      # 未來日期 / 未完成 K 棒 / 明顯異常值不收
                    continue
                out[d] = round(a, 2)
            if out:
                return out
        except Exception:
            pass
    return {}


def avg_amp(code, market, fill=True):
    """最近 AVG_DAYS 個交易日平均振幅;歷史不足且 fill=True 時向 Yahoo 補"""
    h = hist.get(code, {})
    if len(h) < AVG_DAYS and fill:
        got = yahoo_daily_amp(code, market)
        for d, a in got.items():
            h.setdefault(d, a)          # 官方資料優先,Yahoo 只補缺的日期
        hist[code] = {d: h[d] for d in sorted(h)[-15:]}
        h = hist[code]
        time.sleep(0.3)
    ds = sorted(h)[-AVG_DAYS:]
    if not ds:
        return None
    return round(sum(h[d] for d in ds) / len(ds), 2)


def amp_hits(code):
    """最近 AVG_DAYS 個交易日裡,振幅 ≥ MIN_AMP 的天數"""
    h = hist.get(code, {})
    ds = sorted(h)[-AVG_DAYS:]
    return sum(1 for d in ds if h[d] >= MIN_AMP)


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

# 5 日平均振幅:只對前 SIZE*3 檔候選算(歷史不足的會向 Yahoo 補抓),省時間
print("計算 5 日平均振幅 …")
kept = []
for s in pool[:SIZE * 3]:
    a5 = avg_amp(s["code"], s["market"])
    s["amp5"] = a5
    s["amp_days"] = len(hist.get(s["code"], {}))
    s["amp_hits"] = amp_hits(s["code"])
    if a5 is not None and a5 >= AVG5_MIN_AMP and s["amp_hits"] >= AVG5_MIN_HITS:
        kept.append(s)
    if len(kept) >= SIZE:
        break
n3 = len(kept)
pool = kept[:SIZE]
print(f"排除不可當沖 {n0 - n1} 檔、注意/處置股 {n1 - n2} 檔、5 日均振幅不足 {min(n2, SIZE * 3) - n3} 檔 → 最終 {len(pool)} 檔")

with open(HIST_FILE, "w", encoding="utf-8") as f:
    json.dump(hist, f, ensure_ascii=False, separators=(",", ":"))

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
         非金融保險業、一檔跳動 ≤ {TICK_MAX_PCT}%、昨日未收在跌停(漲停放行)、
         最近 {AVG_DAYS} 日平均振幅 ≥ {AVG5_MIN_AMP}%;
         依成交張數排序取前 {SIZE} 檔

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
                         "tick_max_pct": TICK_MAX_PCT, "exclude_industry": sorted(EXCLUDE_INDUSTRY),
                         "avg5_min_amp": AVG5_MIN_AMP, "avg5_min_hits": AVG5_MIN_HITS, "avg_days": AVG_DAYS,
                         "entry_max_chg": ENTRY_MAX_CHG},
               "disposed": sorted(disposed), "attention": sorted(attention),
               "elig_source": src_note, "pool": pool}, f, ensure_ascii=False, indent=1)

print(f"\n完成:{len(pool)} 檔 → pool.js / pool.json")
print("前 5 名:")
for s in pool[:5]:
    print(f"  {s['name']:<8} {s['close']:>8}  {s['lots']:>7} 張  振幅 {s['amp']}%  5日均 {s['amp5']}%({s['amp_hits']}/{s['amp_days']}天≥門檻)  ({s['market']})")
