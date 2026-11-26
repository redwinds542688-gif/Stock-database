"""2026-11-27 判別式任務 #2(六4 每日上限驗證):測元大 GetKLine 的歷史 1 分 K 最遠能抓到哪天。
特別要的:2024-08-05、2025-04-07 ~ 04-09(恐慌日)。

用法(電腦上,和 yuanta_bridge.py 同一個資料夾,用同一份 bridge.env):
    python test_kline_depth.py
請在中繼沒在跑的時候執行(收盤後 / 週末;同一個帳號兩邊同時登入可能互踢):
    schtasks /Change /TN YuantaBridge /DISABLE
    schtasks /End /TN YuantaBridge
    python test_kline_depth.py
    schtasks /Change /TN YuantaBridge /ENABLE
約 1 分鐘。把最後「===== 結果 =====」整段截圖回來。只查詢,不下單、不改設定、不寫雲端。
"""
import sys, time, threading
import builtins as _b
def print(*a, **k): _b.print(*a, **k, flush=True)
print("載入元大元件中…")
import yuanta_bridge as YB

CFG = YB.CFG
DAYS = ["2026-11-02", "2026-08-03", "2026-05-04", "2026-01-05", "2025-10-06", "2025-07-07",
        "2025-04-07", "2025-04-08", "2025-04-09", "2025-01-06", "2024-08-05", "2024-01-08"]
CODES = [("TWSE", "2330"), ("TWSE", "IX0001")]

Y, List, System = YB.load_sdk()
ev_trade, ev_login = threading.Event(), threading.Event()
kl = []; msgs = []


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
                if lst is None: msgs.append("GetKLine 回應沒有資料"); return
                for i in range(lst.Count):
                    k = lst[i]; kl.append((str(k.TimeStamp), float(k.ClosePrice)))
            else:
                msgs.append(f"查詢回應 [{dwIndex}] {strIndex} {str(objValue)[:200]}")
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
print("登入成功,開始查(約 1 分鐘)")

out = []
for kt, kname in ((0, "1分K"), (11, "日K")):
    for d in DAYS:
        q = d.replace("-", "/")
        for mk, c in CODES:
            if kt == 11 and c != "2330": continue
            kl.clear()
            try: api.GetKLine(acc, Y.KLineType(kt), getattr(Y.enumMarketType, mk), c, q, q)
            except Exception as e: out.append(f"   {kname} {d} {c}:呼叫失敗 {e!r}"[:160]); time.sleep(1.2); continue
            t0 = time.time()
            while time.time() - t0 < 3 and not kl: time.sleep(0.1)
            time.sleep(0.5)
            r = f"{len(kl)} 根" + (f",{kl[0][0]} ~ {kl[-1][0]} 收 {kl[-1][1]}" if kl else "")
            out.append(f"   {kname} {d} {c}:{r}"); print(out[-1])
            time.sleep(1.2)   # 元大:GetKLine 每秒最多 1 次
out.append("   最近的元大訊息:" + " | ".join(msgs[-6:])[:500])
try: api.LogOut()
except Exception: pass
print("\n===== 結果(整段截圖回來) =====")
print("\n".join(out))
