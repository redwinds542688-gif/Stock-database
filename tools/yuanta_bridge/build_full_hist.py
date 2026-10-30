"""元大歷史 1 分 K 的「抓滿」名單(GitHub Actions 跑,桌機的 hist_kline.py 照這份抓)

2026-10-25 使用者:「不管選股名單設幾檔 符合條件一律收進來 因為名單隨時可改 萬一原設49 改設99 你哪裡找不足的來補 預先抓好抓滿」
→ 每個交易日 D 的名單 = 前一個交易日(D-1)收盤資料符合選股條件的「全部」股票,不設檔數上限、不套 App 的監控高價。
   條件(和 make_pool.py 一致,只拿掉檔數上限;歷史查不到的當沖資格 / 跌停 / 5 日平均振幅不擋,寧多勿少):
   4 碼普通股、非金融、收盤 LO~HI 元、成交 ≥ MIN_LOTS 張、振幅 ≥ MIN_AMP%、一檔跳動 ≤ TICK_MAX_PCT%
   振幅用 amp_history.json(全市場每天振幅,make_pool 每天寫);成交量 / 收盤向證交所、櫃買盤後行情要(Actions 才連得到)。
結果併進 tools/yuanta_bridge/hist_list.json({交易日: [[代號, 'twse'|'otc'], ...]}),原本的名單股照留。
"""
import json, os, sys, time
from datetime import date, timedelta
import requests

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUT = os.path.join(ROOT, "tools", "yuanta_bridge", "hist_list.json")
LO, HI, MIN_AMP, MIN_LOTS, TICK_MAX_PCT = 15, 500, 3.0, 6000, 0.5
FIN = {"5820", "5871", "5876", "5878", "5880", "6005", "6024", "2820"}
FROM = os.environ.get("FROM", "2026-10-02")       # 最早的資料日(10/02 收盤 → 10/05 交易);2026-10-25 使用者「從10月5號開始 10月5號之前的不用再補」
FIRST = "2026-10-05"                               # 清單只留這天以後的交易日
S = requests.Session(); S.headers["User-Agent"] = "Mozilla/5.0"


def get_json(url, **kw):
    for k in range(3):
        try:
            r = S.get(url, timeout=60, **kw); r.raise_for_status(); return r.json()
        except Exception as e:
            print("  重試", url, e); time.sleep(5 * (k + 1))
    return {}


def num(v):
    s = str(v or "").replace(",", "").strip()
    try: return float(s)
    except ValueError: return None


def tick_of(p):
    return 0.01 if p < 10 else 0.05 if p < 50 else 0.1 if p < 100 else 0.5 if p < 500 else 1.0 if p < 1000 else 5.0


def twse(d):
    j = get_json("https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX", params={"response": "json", "date": d.replace("-", ""), "type": "ALLBUT0999"})
    for t in j.get("tables", []) or []:
        f = [str(x).strip() for x in (t.get("fields") or [])]
        if "證券代號" in f and t.get("data"):
            ix = {k: i for i, k in enumerate(f)}
            return [(r[ix["證券代號"]].strip(), num(r[ix["成交股數"]]), num(r[ix["收盤價"]]), num(r[ix["最高價"]]), num(r[ix["最低價"]])) for r in t["data"]]
    return []


def tpex(d):
    j = get_json("https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes", params={"date": d.replace("-", "/"), "response": "json"})
    t = (j.get("tables") or [{}])[0]
    f = [str(x).strip() for x in (t.get("fields") or [])]
    if "代號" not in f or not t.get("data"):
        return []
    ix = {k: i for i, k in enumerate(f)}
    return [(r[ix["代號"]].strip(), num(r[ix["成交股數"]]), num(r[ix["收盤"]]), num(r[ix["最高"]]), num(r[ix["最低"]])) for r in t["data"]]


def main():
    amp = json.load(open(os.path.join(ROOT, "amp_history.json"), encoding="utf-8"))
    days = sorted({d for v in amp.values() for d in v if len(v) > 0})
    days = [d for d in days if sum(1 for v in amp.values() if d in v) > 500]   # 全市場都有的日子才是交易日
    lst = json.load(open(OUT, encoding="utf-8")) if os.path.exists(OUT) else {}
    for i, dd in enumerate(days[:-1]):
        if dd < FROM:
            continue
        td = days[i + 1]            # dd 收盤選出來的名單 → 隔一個交易日 td 交易
        rows = [(c, v, cl, h, l, "twse") for c, v, cl, h, l in twse(dd)]; time.sleep(3)
        rows += [(c, v, cl, h, l, "otc") for c, v, cl, h, l in tpex(dd)]; time.sleep(3)
        got = []
        for c, v, cl, h, l, mk in rows:
            if not (len(c) == 4 and c.isdigit() and c[0] != "0") or c.startswith("28") or c in FIN:
                continue
            if not cl or not v or not (LO <= cl <= HI) or v / 1000 < MIN_LOTS:
                continue
            a = amp.get(c, {}).get(dd)
            if a is None or a < MIN_AMP or tick_of(cl) / cl * 100 > TICK_MAX_PCT:
                continue
            got.append((c, mk, v))
        got.sort(key=lambda x: -x[2])
        old = lst.get(td, [])
        have = {c for c, _ in old}
        lst[td] = old + [[c, mk] for c, mk, _ in got if c not in have]
        print(f"{td}(用 {dd} 收盤):行情 {len(rows)} 檔,符合 {len(got)} 檔,原名單 {len(old)} → 合計 {len(lst[td])}")
    lst = {k: v for k, v in lst.items() if k >= FIRST}
    json.dump(dict(sorted(lst.items())), open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=0)


if __name__ == "__main__":
    main()
