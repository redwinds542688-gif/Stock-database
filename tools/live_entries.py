"""2026-11-03 使用者「那就放在開放的空間」:每天收盤後把當天盤中真的發過的進場訊息存一份到公開庫(live_entries.json),
   給事後重算的虛擬交易(額度版)對照「盤中有沒有發進場訊息」。
   只存 股票代號|多空|判別式,不存時間、價格(外人看不出判別式規則)。金鑰 YT_PUSH_KEY 只從環境變數讀(GitHub Secrets),不印出來。
   沒存過的日子(最近 45 天的平日)會一起補;今天每次都重抓。"""
import datetime, json, os, re, sys, urllib.request

URL = "https://twse-proxy.redwinds542688.workers.dev/?log=get&d="
KEY = os.environ.get("YT_PUSH_KEY", "")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "live_entries.json")
if not KEY: print("沒有 YT_PUSH_KEY,略過"); sys.exit(0)

def get(d):
    req = urllib.request.Request(URL + d, headers={"X-Push-Key": KEY, "User-Agent": "live-entries"})
    with urllib.request.urlopen(req, timeout=30) as r: return json.loads(r.read().decode("utf-8"))

try: DB = json.load(open(OUT, encoding="utf-8"))
except Exception: DB = {}
now = datetime.datetime.utcnow() + datetime.timedelta(hours=8); today = now.strftime("%Y-%m-%d")
days = [(now - datetime.timedelta(days=k)).strftime("%Y-%m-%d") for k in range(45)]
days = [d for d in days if datetime.date.fromisoformat(d).weekday() < 5 and (d not in DB or d == today) and d >= "2026-10-01"]
if now.hour < 13 or (now.hour == 13 and now.minute < 35): days = [d for d in days if d != today]   # 收盤後才存今天
for d in sorted(days):
    try: items = get(d)
    except Exception as e: print(d, "讀不到:", str(e)[:80]); continue
    if isinstance(items, dict): items = items.get("items") or []
    if not isinstance(items, list): continue
    s = set()
    for x in items:
        if not isinstance(x, dict) or x.get("kind") not in ("long", "short"): continue
        m = re.search(r"\b(\d{4,6}[A-Z]?)\b", str(x.get("title", "")))
        if not m: continue
        rs = (re.search(r"· (判[一二三四]|元三|強三)\s*$", str(x.get("body", ""))) or [None, ""])[1]
        s.add(f"{m.group(1)}|{x['kind']}|{rs}")
    if not s and not items: print(d, "沒有通知(休市或沒存),不寫"); continue
    DB[d] = sorted(s); print(d, len(s), "筆進場")
json.dump(dict(sorted(DB.items())), open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
