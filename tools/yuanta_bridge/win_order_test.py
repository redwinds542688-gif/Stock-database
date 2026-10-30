"""元大下單功能測試:掛一張不會成交的單,再取消(Windows,正式環境)

2026-10-24 使用者:「先準備掛單取消的測試程式」。目的:元大測試環境要固定 IP 用不了,下單 / 取消 / 回報從沒實際跑過,
自動交易開之前先用真的帳號驗證欄位格式。
做法:盤中用「跌停價」買 1 張便宜、穩定的股票(預設 2883),只有那檔當下跌停才會成交,幾乎不可能;
      送出 → 等委託回報 → 取消 → 等取消回報 → 查今天委託確認已取消。全程印出元大回傳的每個欄位。
用法(在 C:\\Yuanta\\YuantaSparkAPI_win-x64_Python,盤中 09:05~13:15):
    python win_order_test.py           (預設 2883)
    python win_order_test.py 2892      (換別檔)
帳號密碼讀 C:\\Yuanta\\bridge\\bridge.env;中繼程式(工作排程 YuantaBridge)會先暫停,測完再啟動(同一帳號不要同時登入兩個)。
"""
import os, sys, time, datetime, pathlib, re, subprocess, threading

HERE = pathlib.Path(__file__).parent.resolve()
ENV = pathlib.Path(r"C:\Yuanta\bridge\bridge.env")
TASK = "YuantaBridge"
TW = datetime.timezone(datetime.timedelta(hours=8))
now = lambda: datetime.datetime.now(TW)

code = (sys.argv[1] if len(sys.argv) > 1 else "2883").strip()
hm = now().strftime("%H:%M")
if not ("09:05" <= hm <= "13:15") or now().weekday() >= 5:
    sys.exit(f"現在 {hm}:這個測試只在週一到週五 09:05~13:15 跑(要盤中才看得到真的委託回報)")
if not (HERE / "YuantaSparkAPI.dll").exists():
    sys.exit("請放在元大 YuantaSparkAPI_win-x64_Python 資料夾執行")

cfg = {}
if ENV.exists():
    for line in ENV.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1); cfg[k.strip()] = re.split(r"\s+#", v, 1)[0].strip()
acct, pwd = cfg.get("YT_ACCOUNT", ""), cfg.get("YT_PASS", "")
if not acct or not pwd:
    import getpass
    a = input("證券帳號(分公司 4 碼 + 帳號 7 碼)> ").strip().replace("-", "")
    acct = "S" + (a[1:] if a[:1] in ("S", "s") and len(a) == 12 else a)
    pwd = getpass.getpass("電子密碼(不會顯示)> ")

from pythonnet import load
load("coreclr")
import clr
sys.path.append(str(HERE)); os.add_dll_directory(str(HERE))
clr.AddReference("System.Collections"); clr.AddReference("YuantaSparkAPI")
from System.Collections.Generic import List
from YuantaOneAPI import YuantaSparkAPITrader, enumEnvironmentMode, OnResponseEventHandler, enumLogType, StockOrder

ev = {k: threading.Event() for k in ("trade", "login", "order", "cancel", "rpt", "query", "reply")}
got = {"order_no": "", "login_ok": False, "filled": False, "replies": 0}


def dump(obj, depth=0, name=""):
    """把元大回傳的物件所有欄位印出來(驗證欄位名稱 / 單位用)
    2026-10-29 使用者實測:GetProperties() 回空的(元大的回傳物件屬性反射不到),三個 ↓ 底下什麼都沒印 →
    改成 屬性 → 欄位(GetFields)→ Python dir() 三種都試,還是沒有就印物件本身和「(沒有欄位)」"""
    pad = "    " * (depth + 1)
    items = []
    try:
        t = obj.GetType()
        for p in t.GetProperties(): items.append((p.Name, (lambda o, p=p: p.GetValue(o, None))))
        for f in t.GetFields(): items.append((f.Name, (lambda o, f=f: f.GetValue(o))))
    except Exception:
        pass
    if not items:
        skip = {"Equals", "GetHashCode", "GetType", "ToString", "ReferenceEquals", "MemberwiseClone", "Overloads", "Finalize", "GetEnumerator"}
        for n in dir(obj):
            if n.startswith("_") or n in skip: continue
            items.append((n, (lambda o, n=n: getattr(o, n))))
    if not items:
        print(f"{pad}{name}{obj} (沒有欄位)"); return
    shown = 0
    for nm, get in items:
        try: v = get(obj)
        except Exception as e: v = f"(讀不到 {e})"
        if callable(v) and not hasattr(v, "Count"): continue
        tn = str(getattr(v, "GetType", lambda: type(v))()) if v is not None else ""
        if v is not None and ("List" in tn or "YuantaOneAPI" in tn) and depth < 2 and not isinstance(v, (str, int, float)):
            try:
                n = v.Count
                print(f"{pad}{nm}: ({n} 筆)")
                for i in range(min(n, 5)): print(f"{pad}  [{i}]"); dump(v[i], depth + 2)
                shown += 1; continue
            except Exception:
                print(f"{pad}{nm}:"); dump(v, depth + 1); shown += 1; continue
        print(f"{pad}{nm} = {v}"); shown += 1
    if not shown: print(f"{pad}{name}{obj} (沒有欄位)")


def on_response(intMark, dwIndex, strIndex, objHandle, objValue):
    try:
        t = now().strftime("%H:%M:%S")
        if intMark == 0:
            print(f"[{t} 系統] {dwIndex} {objValue}")
            if "交易主機" in str(objValue) and "Connected!!" in str(objValue): ev["trade"].set()
            return
        if strIndex == "Login":
            st = objValue.LoginStatus
            got["login_ok"] = str(st.MsgCode) in ("0001", "00001") or int(st.Count) > 0
            print(f"[{t} 登入] {st.MsgCode} {st.MsgContent}"); ev["login"].set(); return
        if strIndex == "SendStockOrder":
            print(f"[{t} 下單回應] ↓"); dump(objValue)
            r = objValue; got["last_ok"] = False
            for i in range(r.ResultCount.Count):
                d = r.ResultList[i]
                if int(d.ReplyCode) == 0: got["last_ok"] = True
                if int(d.ReplyCode) == 0 and str(d.OrderNO).strip() and not got["order_no"]:
                    got["order_no"] = str(d.OrderNO).strip()
            got["replies"] += 1
            (ev["cancel"] if got["replies"] > 1 else ev["order"]).set(); ev["reply"].set(); return
        if "RealReport" in str(strIndex):
            print(f"[{t} 即時回報 {strIndex}] ↓"); dump(objValue)
            try:
                if int(objValue.RptType) == 51: got["filled"] = True
            except Exception: pass
            ev["rpt"].set(); return
        if strIndex == "GetOrderTradeReport":
            print(f"[{t} 今天委託成交查詢] ↓"); dump(objValue); ev["query"].set(); return
        print(f"[{t} 回應] intMark={intMark} dwIndex={dwIndex} strIndex={strIndex}"); dump(objValue)
    except Exception as e:
        print(f"[回應處理錯誤] {strIndex}: {e}")


def stock_order(kind, order_no="", qty=1):
    o = StockOrder()
    o.Identify = 1 if kind == 0 else 2
    o.Account = acct; o.APCode = 0; o.TradeKind = kind; o.OrderType = "0"
    o.StkCode = code; o.BuySell = "B"
    o.PriceFlag = "L" if kind == 0 else " "; o.Price = 0.0          # 新單:跌停價(L,價格填 0);取消:限價欄位不用
    o.OrderQty = qty; o.OrderNo = order_no
    o.TradeDate = now().strftime("%Y/%m/%d"); o.BasketNo = "TEST" + now().strftime("%H%M%S"); o.Time_in_force = "0"
    lst = List[StockOrder](); lst.Add(o)
    return api.SendStockOrder(acct, lst)


print(f"=== 元大下單測試:跌停價買 {code} 1 張 → 取消 ===")
print(f"帳號 {acct[:5]}******(讀 {ENV})")
if input(f"確定要送出「{code} 跌停價買 1 張 ROD」再馬上取消?輸入 YES 才會送 > ").strip() != "YES":
    sys.exit("沒有送出")

print("暫停中繼程式(同一帳號不要同時登入)…")
subprocess.run(["schtasks", "/End", "/TN", TASK], capture_output=True)
time.sleep(3)

api = YuantaSparkAPITrader()
api.OnResponse += OnResponseEventHandler(on_response)
api.SetLogType(enumLogType.COMMON)
try:
    api.Open(enumEnvironmentMode.PROD)
    if not ev["trade"].wait(30): sys.exit("!! 30 秒沒連上交易主機")
    time.sleep(1); api.Login(acct, pwd); pwd = None
    if not ev["login"].wait(30) or not got["login_ok"]: sys.exit("!! 登入失敗")
    time.sleep(2)

    print(f"\n--- 1. 送出:{code} 跌停價買 1 張 ROD ---")
    print("SendStockOrder 回傳", stock_order(0))
    if not ev["order"].wait(15): print("!! 15 秒沒有下單回應")
    ev["rpt"].wait(10); time.sleep(2)
    no = got["order_no"]
    print(f"\n委託書號:{no or '(沒拿到)'}")
    if got["filled"]:
        print(f"\n!!!!!! 成交了({code} 剛好跌停)!請到投資先生把這 1 張賣掉或留著 !!!!!!")

    if no and not got["filled"]:
        print(f"\n--- 2. 取消委託 {no} ---")
        ev["rpt"].clear()
        print("SendStockOrder(取消)回傳", stock_order(4, no, 0))
        if not ev["cancel"].wait(15): print("!! 15 秒沒有取消回應")
        ev["rpt"].wait(10); time.sleep(2)
        if not got.get("last_ok"):                           # 取消的數量欄位文件沒寫:0 被拒就改填原委託張數再試一次
            print("\n取消被拒,改用「數量 = 1」再取消一次")
            ev["reply"].clear(); ev["rpt"].clear()
            print("SendStockOrder(取消,數量 1)回傳", stock_order(4, no, 1))
            if not ev["reply"].wait(15): print("!! 15 秒沒有取消回應")
            ev["rpt"].wait(10); time.sleep(2)

    print("\n--- 3. 查今天的委託(含取消單),確認狀態 ---")
    api.GetOrderTradeReport(False, acct)                  # False = 取消單也列出來
    if not ev["query"].wait(15): print("!! 15 秒沒有查詢回應")
    time.sleep(1)
finally:
    try: api.LogOut(); api.Close(); api.Dispose()
    except Exception: pass
    print("\n重新啟動中繼程式…")
    subprocess.run(["schtasks", "/Run", "/TN", TASK], capture_output=True)
    print("完成。請把上面全部內容截圖或複製給 Claude(帳號已經只顯示前 5 碼)。")
    print(f"也請到投資先生「委託查詢」確認 {code} 那筆是「已取消」。")
