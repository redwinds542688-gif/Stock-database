"""2026-11-26 使用者「回到電腦了,測元大能不能訂閱指數」:測元大 API 能不能拿到加權指數(資料來源以元大為主、證交所為輔)。

用法(電腦上,和 yuanta_bridge.py 同一個資料夾,用同一份 bridge.env 帳密):
    python test_index_probe.py
最好在中繼沒在跑的時候執行(盤後即可;同一個帳號兩邊同時登入可能互踢)。約 1 分鐘跑完,把畫面最後的「結果」整段貼回來。

測三件事:
  1. 元大元件裡有沒有跟指數有關的功能(名稱含 Index / Quote / Subscribe 的方法)、市場別有哪些
  2. 查今天的 1 分 K(GetKLine):用幾個常見的加權指數代碼試,看哪個有資料(盤後也查得到)
  3. 訂閱逐筆(SubscribeStockTick)同樣那幾個代碼 20 秒,看有沒有收到(盤中才會有;盤後只看會不會被拒)
不下單、不改任何設定、不寫雲端。
"""
import sys, time, threading, datetime as dt
import builtins as _b
def print(*a, **k): _b.print(*a, **k, flush=True)   # 2026-11-26 邊跑邊印(原本跑完才印,看起來像卡住)
print("載入元大元件中…")
import yuanta_bridge as YB

CFG = YB.CFG
CANDS = ["#001", "IX0001", "t00", "TSE", "TAIEX", "0000", "001", "1000", "Y9999", "TWII", "IX0043"]

Y, List, System = YB.load_sdk()
ev_trade, ev_login = threading.Event(), threading.Event()
kl = []; ticks = []; msgs = []


def onr(intMark, dwIndex, strIndex, objHandle, objValue):
    try:
        if intMark == 0:
            s = str(objValue); msgs.append(f"系統 [{dwIndex}] {s}")
            if int(dwIndex) == 1 and "交易主機" in s: ev_trade.set()
        elif intMark == 1:
            if strIndex == "Login":
                st = objValue.LoginStatus; msgs.append(f"登入 {st.MsgCode} {st.MsgContent}")
                if YB.login_kind(st.MsgCode, st.MsgContent, st.Count) == "ok": ev_login.set()
            elif strIndex == "GetKLine":
                lst = objValue.KLineList
                if lst is None: return
                for i in range(lst.Count):
                    k = lst[i]; kl.append((str(k.TimeStamp), float(k.ClosePrice), float(k.DealVol)))
            else:
                msgs.append(f"查詢回應 [{dwIndex}] {strIndex} {str(objValue)[:200]}")
        elif intMark == 2 and strIndex == "SubscribeStockTick":
            r = objValue; ticks.append((str(r.StkCode).strip(), float(r.DealPrice), int(r.SerialNo)))
    except Exception as e:
        msgs.append(f"回應處理錯誤 {e!r}")


print("連線元大中…")
mode = Y.enumEnvironmentMode.PROD if CFG.get("YT_ENV", "UAT").upper() == "PROD" else Y.enumEnvironmentMode.UAT
api = Y.YuantaSparkAPITrader(str(YB.HERE / "log"))
api.OnResponse += onr
api.Open(mode)
if not ev_trade.wait(30): print("30 秒連不上元大交易主機"); sys.exit(1)
time.sleep(1)
acc = CFG["YT_ACCOUNT"]
api.Login(acc, CFG["YT_PASS"]) if sys.platform == "win32" else api.Login(CFG["YT_PFX"], CFG["YT_PFX_PASS"], acc, CFG["YT_PASS"])
if not ev_login.wait(20): print("登入失敗:", msgs[-3:]); sys.exit(1)
print("登入成功,開始測試(約 1~2 分鐘)")

out = []
out.append("1) 元件功能:" + ", ".join(m for m in dir(api) if any(k in m for k in ("Index", "Quote", "Subscribe", "KLine", "Future"))))
mkts_all = [m for m in dir(Y.enumMarketType) if not m.startswith("_") and m[0].isupper()]
mkts = [m for m in mkts_all if m in ("TWSE", "TWOTC") or any(k in m.upper() for k in ("IDX", "INDEX"))]   # 只試股票市場 + 名字像指數的
out.append("   市場別(全部):" + ", ".join(mkts_all)); print(out[0]); print(out[1])

day = dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))
d = day.strftime("%Y/%m/%d")
if day.weekday() >= 5: d = (day - dt.timedelta(days=day.weekday() - 4)).strftime("%Y/%m/%d")
out.append(f"2) 1 分 K 查詢(日期 {d}):")
for mk in mkts:
    try: mv = getattr(Y.enumMarketType, mk)
    except Exception: continue
    for c in CANDS:
        kl.clear()
        try: api.GetKLine(acc, Y.KLineType(0), mv, c, d, d)
        except Exception as e: out.append(f"   {mk} {c}:呼叫失敗 {e!r}"[:160]); continue
        t0 = time.time()
        while time.time() - t0 < 2 and not kl: time.sleep(0.1)
        time.sleep(0.4)
        if kl: out.append(f"   ✅ {mk} {c}:{len(kl)} 根,最後 {kl[-1]}")
        print(f"   查 {mk} {c}:{'有 ' + str(len(kl)) + ' 根' if kl else '沒有'}")
        time.sleep(0.4)
if not any("✅" in x for x in out): out.append("   (都沒有資料)")

out.append("3) 訂閱逐筆 20 秒(只訂 IX0001、IX0043,一檔一次):"); print("訂閱測試 20 秒…")
for mk, c in (("TWSE", "IX0001"), ("TWOTC", "IX0043")):   # 2026-11-26 第一次整包 11 個代碼一起訂 → 回傳失敗(可能被無效代碼拖累),改成只訂有 K 線的兩檔
    try:
        lst = List[Y.StockTick](); s = Y.StockTick(); s.MarketType = getattr(Y.enumMarketType, mk); s.StockCode = c; lst.Add(s)
        ok = api.SubscribeStockTick(acc, lst); out.append(f"   {mk} {c}:訂閱{'送出' if ok else '回傳失敗'}")
    except Exception as e: out.append(f"   {mk} {c}:訂閱錯誤 {e!r}"[:160])
    time.sleep(0.5)
time.sleep(20)
got = {}
for c, p, sn in ticks: got.setdefault(c, []).append(p)
out.append("   收到:" + (", ".join(f"{c} {len(v)} 筆 最後 {v[-1]}" for c, v in got.items()) if got else "沒有(盤後本來就不會有,盤中再測一次)"))
out.append("   最近的元大訊息:" + " | ".join(m for m in msgs[-8:])[:600])
try: api.LogOut()
except Exception: pass
print("\n===== 結果(整段貼回來) =====")
print("\n".join(out))
