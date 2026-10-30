"""今天元大帳號的輸贏(2026-10-28 18:20 使用者「看今天 元大帳號裡的輸贏」)

在 C:\\Yuanta\\bridge 執行:  python win_pnl.py
問本機正在跑的中繼程式(127.0.0.1:8787 /trade/fills),由它向元大查今天全部成交(程式單 + 手動單),算每檔輸贏。
不另外登入元大、不經過雲端;金鑰從 bridge.env 讀,不印出來。
成本照元大明細(2026-10-28 對過):單邊 = 成交金額 × 0.1425% × 折扣 無條件捨去,證交稅 = 賣出金額 × 0.15%(當沖)無條件捨去;折扣用 bridge.env 的 FEE_DISC(沒設 = 0.25)。
"""
import json, re, sys, urllib.request
from pathlib import Path

ENV = Path(__file__).resolve().parent / "bridge.env"
cfg = {}
for l in ENV.read_text(encoding="utf-8").splitlines():
    l = l.strip()
    if l and not l.startswith("#") and "=" in l:
        k, v = l.split("=", 1); cfg[k.strip()] = re.split(r"\s+#", v, 1)[0].strip()
url = f"http://127.0.0.1:{cfg.get('QUOTE_PORT', '8787')}/trade/fills"


def direct():
    """收盤後中繼程式沒登入元大(只有 08:45~13:36 登入)→ 這裡自己登入一次、查完就登出(和 14:05 / 17:05 補抓 K 線同一種做法)"""
    print("中繼程式現在沒登入元大(收盤後),改由這裡登入查詢,約 10~30 秒…")
    sys.path.insert(0, str(ENV.parent)); import yuanta_bridge as yb
    b = yb.Bridge()
    try:
        try: b.load_codes()                       # 股票名稱(今天名單上的)
        except Exception: pass
        b.connect()
        r = b.query_today(with_oco=False)
        if r is None: sys.exit("查不到元大成交回報")
        names = {k: (v or {}).get("n", "") for k, v in b.quotes.meta.items()}
        out = yb.fills_pnl(r["trades"], names, disc=float(cfg.get("FEE_DISC", "0.25")))
        out.update(ok=True, at=yb.now_tw().strftime("%H:%M:%S"))
        return out
    finally:
        b.close()


try:
    req = urllib.request.Request(url, headers={"X-Quote-Key": cfg.get("PUSH_KEY", "")})
    j = json.loads(urllib.request.urlopen(req, timeout=40).read().decode("utf-8"))
except Exception:
    j = {"ok": False, "msg": "電腦還沒登入元大"}   # 中繼程式沒開通道 / 沒在跑 → 一樣自己登入查
if not j.get("ok") and "沒登入" in str(j.get("msg", "")):
    j = direct()
if not j.get("ok"):
    sys.exit("查詢失敗:" + str(j.get("msg") or j.get("error")))

w = lambda s, n: s + " " * max(0, n - sum(2 if ord(c) > 127 else 1 for c in s))   # 中文佔兩格
print(f"\n元大今天成交 {j['n']} 筆(查詢時間 {j['at']})\n")
print(w("股票", 16) + w("方向", 6) + w("買均價", 9) + w("賣均價", 9) + w("張", 4) + w("毛利", 9) + w("手續費", 8) + w("稅", 7) + "淨輸贏")
print("-" * 78)
for r in j["rows"]:
    nm = f"{r['code']} {r['name']}".strip()
    if not r["lots"]:
        print(w(nm, 16) + w(r["dir"], 6) + f"還沒沖銷:{'買' if r['open'] > 0 else '賣'} {abs(r['open'])} 張(不是今天當沖,或還沒平倉)")
        continue
    tail = f"(另有 {abs(r['open'])} 張沒沖銷)" if r["open"] else ""
    print(w(nm, 16) + w(r["dir"], 6) + w(f"{r['buy']:.2f}", 9) + w(f"{r['sell']:.2f}", 9) + w(str(r["lots"]), 4)
          + w(f"{r['gross']:+,}", 9) + w(f"{r['fee']:,}", 8) + w(f"{r['tax']:,}", 7) + f"{r['net']:+,}" + tail)
print("-" * 78)
print(f"合計淨輸贏:{j['net']:+,} 元(以元大對帳單為準)\n")
