"""開盤前資格複檢(電腦端)— 2026-10-30 11:06(實際時間)使用者「該如何避免再次出現這問題」→「2道都做」第 1 道
   10/30 東台(10/29 收盤後才公布處置)早上還在手機名單裡、程式照樣進場。雲端複檢靠 GitHub 排程(常延遲 1~5 小時),
   這裡改成電腦自己做:開盤前直接向證交所 / 櫃買抓「今天可現股當沖、暫停先賣後買、注意股、處置股」,
   對照名單檔(pool.json)算出今天不能做的股票,存本機 engine_data/elig_<日期>.json、推 Worker(?elig=push)給手機和引擎用。
   trader.py 進場前也查這個檔(第 2 道:名單再怎麼漏也不下單)。
   用法:bridge 開盤前自動跑(啟動時、08:30、08:44);也可以手動 python elig.py
"""
import json, re, sys, time, urllib.request, urllib.parse, datetime as dt
from pathlib import Path

HERE = Path(__file__).resolve().parent
TW = dt.timezone(dt.timedelta(hours=8))
now_tw = lambda: dt.datetime.now(TW)
UA = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

TWSE_DT = "https://openapi.twse.com.tw/v1/exchangeReport/TWTB4U"                     # 上市:當日可現股當沖(含暫停先賣後買註記)
TPEX_DT = "https://www.tpex.org.tw/openapi/v1/tpex_securities"                         # 上櫃:可現股當沖
TPEX_DT_PAUSE = "https://www.tpex.org.tw/openapi/v1/tpex_intraday_trading_pre"          # 上櫃:暫停先賣後買
TWSE_NOTICE = "https://openapi.twse.com.tw/v1/announcement/notice"                     # 上市:注意股(OpenAPI)
TWSE_PUNISH = "https://openapi.twse.com.tw/v1/announcement/punish"                     # 上市:處置股(OpenAPI,隔天早上才更新)
TPEX_NOTICE = "https://www.tpex.org.tw/openapi/v1/tpex_trading_warning_information"    # 上櫃:注意股
TPEX_PUNISH = "https://www.tpex.org.tw/openapi/v1/tpex_disposal_information"           # 上櫃:處置股
TWSE_SITE_NOTICE = "https://www.twse.com.tw/rwd/zh/announcement/notice"                # 上市:注意股(網站,?date=)
TWSE_SITE_PUNISH = "https://www.twse.com.tw/rwd/zh/announcement/punish"                # 上市:處置股(網站,?startDate=&endDate=,當天傍晚就有)


def load_env():
    cfg = {}
    f = HERE / "bridge.env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if "=" in line:
                k, v = line.split("=", 1); cfg[k.strip()] = v.strip().strip('"').strip("'")
    import os
    for k, v in os.environ.items():
        if k in cfg or k in ("WORKER_URL", "PUSH_KEY"): cfg[k] = v
    return cfg


def get_json(url, params=None, timeout=40, tries=2):
    if params: url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    last = None
    for i in range(tries):
        try:
            r = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout)
            return json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:
            last = e; time.sleep(2 * (i + 1))
    raise RuntimeError(f"{url.split('?')[0]}: {last}")


def is_code(c): c = str(c or "").strip(); return len(c) == 4 and c.isdigit() and c[0] != "0"
def field(r, *needles):
    for k, v in r.items():
        kl = str(k).lower()
        if any(n.lower() in kl for n in needles): return str(v or "").strip()
    return ""
def is_flag(v): return str(v or "").strip() in ("Y", "y", "*", "＊", "V", "v", "1", "是", "暫停")
def roc_iso(s):
    s = str(s or "").strip().replace("/", "").replace("-", "")
    if not s.isdigit(): return None
    try:
        if len(s) == 7: return dt.date(int(s[:3]) + 1911, int(s[3:5]), int(s[5:7])).isoformat()
        if len(s) == 8: return dt.date(int(s[:4]), int(s[4:6]), int(s[6:8])).isoformat()
    except ValueError: return None
    return None


def parse_dt(rows):
    """{code: 可先賣後買(1/0)}, 名單日期"""
    out, dates = {}, {}
    for r in rows:
        c = field(r, "Code", "代號")
        if not is_code(c): continue
        out[c] = 0 if is_flag(field(r, "Suspension", "Suspend", "Pause", "暫停", "先賣後買", "Remark", "註記")) else 1
        d = roc_iso(field(r, "Date", "日期"))
        if d: dates[d] = dates.get(d, 0) + 1
    return out, (max(dates, key=dates.get) if dates else None)


def code_set(rows): return {c for c in (field(r, "Code", "代號") for r in rows) if is_code(c)}


def site_rows(url, **p):
    j = get_json(url, {**p, "response": "json"})
    if not isinstance(j, dict): raise RuntimeError("不是 JSON 物件")
    if j.get("stat") != "OK": return []
    f = j.get("fields") or []
    return [dict(zip(f, row)) for row in (j.get("data") or [])]


def site_punish(target):
    rows = site_rows(TWSE_SITE_PUNISH, startDate=(target - dt.timedelta(days=25)).strftime("%Y%m%d"), endDate=target.strftime("%Y%m%d"))
    out = set()
    for r in rows:
        c = str(r.get("證券代號", "")).strip()
        if not is_code(c): continue
        try:
            a, b = re.split(r"[～~\-－]", str(r.get("處置起迄時間", "")))[:2]
            if roc_iso(b) >= target.isoformat(): out.add(c)
        except Exception: out.add(c)
    return out


def refresh(cfg=None, log=print, pool=None):
    """抓官方名單 → 對照 pool.json → 存檔 + 推 Worker;回傳結果 dict"""
    cfg = cfg or load_env(); today = now_tw().date(); d = today.isoformat()
    src, errs = [], []
    # 名單檔
    if pool is None:
        try: pool = json.loads(gh_get(cfg, "pool.json"))
        except Exception as e: errs.append(f"pool.json {e}"); pool = {}
    rows = pool.get("pool", []) if isinstance(pool, dict) else []
    codes = {str(s.get("code")): str(s.get("market") or "") for s in rows if is_code(s.get("code"))}
    pool_date = (pool.get("date") if isinstance(pool, dict) else None) or ""
    # 可當沖 / 先賣後買
    dt_ok, ok_mk = {}, set()
    for mk, url in (("twse", TWSE_DT), ("tpex", TPEX_DT)):
        try:
            m, ld = parse_dt(get_json(url))
            if not m: raise RuntimeError("名單是空的")
            if ld and (today - dt.date.fromisoformat(ld)).days > 4: raise RuntimeError(f"名單日期 {ld} 太舊")
            dt_ok.update(m); ok_mk.add(mk); src.append(f"{'上市' if mk == 'twse' else '上櫃'}可當沖 {len(m)} 檔({ld})")
        except Exception as e: errs.append(f"{mk} 可當沖名單:{e}")
    pause = set()
    try:
        p, _ = parse_dt(get_json(TPEX_DT_PAUSE)); pause = set(p); src.append(f"上櫃暫停先賣後買 {len(pause)} 檔")
    except Exception as e: errs.append(f"上櫃暫停先賣後買:{e}")
    # 注意 / 處置
    attention, disposed = set(), set()
    for label, fn, target in (("上市注意股", lambda: code_set(get_json(TWSE_NOTICE)), attention),
                              ("上櫃注意股", lambda: code_set(get_json(TPEX_NOTICE)), attention),
                              ("上市處置股", lambda: code_set(get_json(TWSE_PUNISH)), disposed),
                              ("上櫃處置股", lambda: code_set(get_json(TPEX_PUNISH)), disposed),
                              ("上市注意股(網站)", lambda: {str(r.get("證券代號", "")).strip() for r in site_rows(TWSE_SITE_NOTICE, date=(pool_date or d).replace("-", "")) if is_code(str(r.get("證券代號", "")).strip())}, attention),
                              ("上市處置股(網站)", lambda: site_punish(today), disposed)):
        try:
            got = fn(); target |= got; src.append(f"{label} {len(got)}")
        except Exception as e: errs.append(f"{label}:{e}")
    # 對照名單
    nodt = sorted(c for c, mk in codes.items() if mk in ok_mk and c not in dt_ok)
    noshort = sorted(c for c, mk in codes.items() if mk in ok_mk and c in dt_ok and (dt_ok.get(c) == 0 or c in pause))
    out = {"d": d, "hm": now_tw().strftime("%H:%M"), "at": int(time.time() * 1000), "pool_date": pool_date, "n_pool": len(codes),
           "disposed": sorted(disposed & set(codes)), "attention": sorted(attention & set(codes)), "nodt": nodt, "noshort": noshort,
           "all_disposed": sorted(disposed), "src": src, "err": errs}
    names = {str(s.get("code")): s.get("name", "") for s in rows}
    bad = [(c, names.get(c, ""), w) for k, w in (("disposed", "處置"), ("attention", "注意"), ("nodt", "不可當沖"), ("noshort", "不可先賣")) for c in out[k]]
    log(f"[複檢] {d} {out['hm']} 名單 {len(codes)} 檔(名單日 {pool_date}):今天不能做 {len(bad)} 檔 " + " ".join(f"{c}{n}({w})" for c, n, w in bad) + (f" | 失敗:{'; '.join(errs)}" if errs else ""))
    try:
        dd = HERE / "engine_data"; dd.mkdir(parents=True, exist_ok=True)
        (dd / f"elig_{d}.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    except Exception as e: log("[複檢] 存檔失敗", e)
    if cfg.get("WORKER_URL") and cfg.get("PUSH_KEY"):
        try:
            body = json.dumps(out, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(cfg["WORKER_URL"].rstrip("/") + "/?elig=push", data=body, method="POST",
                                         headers={"Content-Type": "application/json", "X-Push-Key": cfg["PUSH_KEY"], "User-Agent": "yuanta-bridge/1"})
            urllib.request.urlopen(req, timeout=20).read(); log("[複檢] 已推上 Worker(手機 / 引擎會照這份排除)")
        except Exception as e: log("[複檢] 推 Worker 失敗", e)
    return out


def gh_get(cfg, path):
    if cfg.get("PUSH_KEY") and cfg.get("WORKER_URL"):
        url = cfg["WORKER_URL"].rstrip("/") + "/?gh=raw&path=" + path + f"&_={int(time.time())}"
        req = urllib.request.Request(url, headers={"X-Push-Key": cfg["PUSH_KEY"], "User-Agent": "yuanta-bridge/1"})
    else:
        req = urllib.request.Request(f"https://raw.githubusercontent.com/redwinds542688-gif/Stock-database/main/{path}?t={int(time.time())}", headers={"User-Agent": "yuanta-bridge/1"})
    return urllib.request.urlopen(req, timeout=30).read()


_CACHE = {"d": "", "mt": 0.0, "v": None}
def load_today():
    """今天的複檢結果(本機檔);沒有就 None"""
    d = now_tw().date().isoformat(); f = HERE / "engine_data" / f"elig_{d}.json"
    try:
        mt = f.stat().st_mtime
        if _CACHE["d"] != d or _CACHE["mt"] != mt:
            _CACHE.update(d=d, mt=mt, v=json.loads(f.read_text(encoding="utf-8")))
        return _CACHE["v"]
    except Exception: return None


def blocked(code, kind=""):
    """進場前最後一關:今天不能做這檔的原因;可以就回空字串(沒有複檢檔也回空:只擋查得到的)"""
    e = load_today(); c = str(code)
    if not e: return ""
    if c in e.get("disposed", []) or c in e.get("all_disposed", []): return "今天是處置股"
    if c in e.get("attention", []): return "今天是注意股"
    if c in e.get("nodt", []): return "今天不可現股當沖"
    if kind == "short" and c in e.get("noshort", []): return "今天不可先賣後買(暫停放空)"
    return ""


if __name__ == "__main__":
    r = refresh()
    print(json.dumps({k: r[k] for k in ("d", "hm", "disposed", "attention", "nodt", "noshort", "err")}, ensure_ascii=False, indent=1))
