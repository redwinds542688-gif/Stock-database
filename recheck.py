# ══════════════════════════════════════════════════════
#  名揚四海 — 開盤前資格複檢(GitHub Actions 版)
#
#  由 .github/workflows/recheck.yml 每個交易日 08:45 與 09:05(台灣時間)執行。
#
#  為什麼需要:pool.json 是前一天 14:30 用「前一天」的名單做的,
#  但「可現股當沖 / 可先賣後買 / 注意股 / 處置股」是**交易當日**的狀態,
#  證交所、櫃買每天早上開盤前才公布今天的版本——昨天可以當沖的,今天可能不行。
#  這支程式不重做選股,只把 pool.json 裡每一檔的資格欄位換成「今天」的官方名單,
#  並記錄複檢時間,HTML 端會顯示「資格已複檢 HH:MM」或警告「尚未複檢」。
#
#  規則:
#    · 今天不在可當沖名單 → daytrade=0(HTML 端會排除)
#    · 今天暫停先賣後買   → short_first=0(HTML 端會排除)
#    · 今天新列注意股 / 處置股 → 併入 attention / disposed
#    · 名單的日期欄位若還不是今天(證交所還沒更新),就不覆蓋、只記錄 pending,09:05 再試一次
# ══════════════════════════════════════════════════════

import os, json, time, sys
import requests
from datetime import datetime, timedelta, timezone, date

TWSE_DT = "https://openapi.twse.com.tw/v1/exchangeReport/TWTB4U"
TWSE_NOTICE = "https://openapi.twse.com.tw/v1/announcement/notice"
TWSE_PUNISH = "https://openapi.twse.com.tw/v1/announcement/punish"
TPEX_DT = "https://www.tpex.org.tw/openapi/v1/tpex_securities"
TPEX_DT_PAUSE = "https://www.tpex.org.tw/openapi/v1/tpex_intraday_trading_pre"
TPEX_NOTICE = "https://www.tpex.org.tw/openapi/v1/tpex_trading_warning_information"
TPEX_PUNISH = "https://www.tpex.org.tw/openapi/v1/tpex_disposal_information"

TAIPEI = timezone(timedelta(hours=8))
S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0", "Accept": "application/json"})


def now_tw():
    return datetime.now(TAIPEI)


def get_json(url):
    last = None
    for attempt in range(3):
        try:
            r = S.get(url, timeout=60)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last = e
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"{url}: {last}")


def is_stock_code(code):
    return len(code) == 4 and code.isdigit() and code[0] != "0"


def field(r, *needles):
    for k, v in r.items():
        kl = k.lower()
        if any(n.lower() in kl for n in needles):
            return str(v or "").strip()
    return ""


def is_flag(v):
    return str(v or "").strip() in ("Y", "y", "*", "＊", "V", "v", "1", "是", "暫停")


def roc_to_iso(s):
    s = str(s or "").strip().replace("/", "").replace("-", "")
    if not s.isdigit():
        return None
    if len(s) == 7:
        y, m, d = int(s[:3]) + 1911, int(s[3:5]), int(s[5:7])
    elif len(s) == 8:
        y, m, d = int(s[:4]), int(s[4:6]), int(s[6:8])
    else:
        return None
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return None


def parse_daytrade_list(rows):
    """{code: 可先賣後買} 以及名單日期(取最多的那個日期)"""
    out, dates = {}, {}
    for r in rows:
        code = field(r, "Code", "代號")
        if not is_stock_code(code):
            continue
        pause = field(r, "Suspension", "Suspend", "Pause", "暫停", "先賣後買", "Remark", "註記")
        out[code] = 0 if is_flag(pause) else 1
        d = roc_to_iso(field(r, "Date", "日期"))
        if d:
            dates[d] = dates.get(d, 0) + 1
    list_date = max(dates, key=dates.get) if dates else None
    return out, list_date


def code_set(rows):
    return {c for c in (field(r, "Code", "代號") for r in rows) if is_stock_code(c)}


# ── 讀 pool.json ────────────────────────────────
if not os.path.exists("pool.json"):
    sys.exit("找不到 pool.json")
with open("pool.json", encoding="utf-8") as f:
    P = json.load(f)
today = now_tw().strftime("%Y-%m-%d")
print(f"pool.json 資料日期 {P.get('date')},共 {len(P['pool'])} 檔;今天 {today}")

if P.get("elig_checked") == today:
    print("今天已經複檢過,不重做")
    sys.exit(0)

# ── 今天的可當沖名單 ─────────────────────────────
elig, ok_src, pending = {}, [], []
for label, url in (("上市", TWSE_DT), ("上櫃", TPEX_DT)):
    try:
        m, ld = parse_daytrade_list(get_json(url))
        print(f"{label}可當沖名單:{len(m)} 檔,名單日期 {ld}")
        # 2026-10-15 實測:證交所名單早上已是今天;櫃買名單早上仍標昨天(收盤後才換日期),
        # 所以只要名單不超過 4 天舊就採用(櫃買用最新那份),太舊才視為還沒更新
        if ld and (date.fromisoformat(today) - date.fromisoformat(ld)).days > 4:
            pending.append(f"{label}名單日期仍是 {ld}")
            continue
        elig.update({c: (1, sf) for c, sf in m.items()})
        ok_src.append(label)
    except Exception as e:
        print(f"{label}可當沖名單抓取失敗:{e}")
        pending.append(f"{label}抓取失敗")

if "上櫃" in ok_src:
    try:
        paused, _ = parse_daytrade_list(get_json(TPEX_DT_PAUSE))
        for c in paused:
            if c in elig:
                elig[c] = (1, 0)
        print(f"上櫃暫停先賣後買:{len(paused)} 檔")
    except Exception as e:
        print(f"上櫃暫停先賣後買預告抓取失敗(略過):{e}")

if not ok_src:
    print("兩個交易所都還沒更新或抓不到;本次不覆蓋,只記錄 pending:" + "; ".join(pending))
    P["elig_pending"] = {"at": now_tw().strftime("%Y-%m-%d %H:%M"), "why": pending}
    with open("pool.json", "w", encoding="utf-8") as f:
        json.dump(P, f, ensure_ascii=False, indent=1)
    sys.exit(0)

# ── 今天的注意股 / 處置股 ────────────────────────
attention, disposed = set(P.get("attention", [])), set(P.get("disposed", []))
for label, url, target in (("上市注意股", TWSE_NOTICE, attention), ("上櫃注意股", TPEX_NOTICE, attention),
                           ("上市處置股", TWSE_PUNISH, disposed),  ("上櫃處置股", TPEX_PUNISH, disposed)):
    try:
        got = code_set(get_json(url))
        target |= got
        print(f"{label}:{len(got)} 檔")
    except Exception as e:
        print(f"{label}抓取失敗(略過,沿用昨天的):{e}")

# ── 覆寫每檔的資格欄位 ───────────────────────────
changed = []
for s in P["pool"]:
    code = s["code"]
    mk = "上市" if s.get("market") == "twse" else "上櫃"
    if mk not in ok_src:
        continue                          # 這個市場的名單今天還沒更新,維持昨天的值
    dt, sf = elig.get(code, (0, 0))
    before = (s.get("daytrade"), s.get("short_first"))
    s["daytrade"], s["short_first"] = dt, sf
    if (dt, sf) != before:
        changed.append(f"{code} {s['name']} {before}→{(dt, sf)}")

P["attention"] = sorted(attention)
P["disposed"] = sorted(disposed)
P["elig_checked_at"] = now_tw().strftime("%Y-%m-%d %H:%M")
P["elig_source_today"] = ok_src
if len(ok_src) == 2:                      # 上市、上櫃都用今天的名單更新了 → 完成
    P["elig_checked"] = today
    P.pop("elig_pending", None)
else:                                     # 只更新了一個市場 → 標記未完成,09:05 那次會再補另一個
    P["elig_pending"] = {"at": P["elig_checked_at"], "why": pending}

with open("pool.json", "w", encoding="utf-8") as f:
    json.dump(P, f, ensure_ascii=False, indent=1)

# pool.js 同步(離線備援用)
lines = [f"  ['{s['code']}','{s['name']}',{s['close']},{s['dir']},{s['lots']},{s['amp']},"
         f"{s['daytrade']},{s['short_first']}]," for s in P["pool"]]
js = f"""/* 監控股票原始股 — 資料日期 {P.get('date')};資格已於 {P['elig_checked_at']} 以當日官方名單複檢
   欄位:代號 / 名稱 / 收盤 / 漲跌 / 成交張數 / 振幅% / 可當沖 / 可先賣後買 */
window.RAW_POOL_DATE = '{P.get('date')}';
window.RAW_DISPOSED = {json.dumps(P['disposed'])};
window.RAW_POOL = [
{chr(10).join(lines)}
];
"""
with open("pool.js", "w", encoding="utf-8") as f:
    f.write(js)

still = sum(1 for s in P["pool"] if s["daytrade"] == 1 and s["short_first"] == 1)
print(f"\n複檢{'完成' if len(ok_src) == 2 else '部分完成'}({', '.join(ok_src)}):{len(P['pool'])} 檔中今天仍可雙向當沖 {still} 檔,資格變動 {len(changed)} 檔")
for c in changed:
    print("  " + c)
