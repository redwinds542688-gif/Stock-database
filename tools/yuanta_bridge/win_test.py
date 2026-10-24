"""元大 SPARK API 第一次連線測試(Windows,只讀,不下單)

2026-10-24 使用者:「先試著接通元大 api」。
放在元大 YuantaSparkAPI_win-x64_Python 資料夾裡(和 YuantaSparkAPI.dll 同一層),在那個資料夾開命令提示字元執行:
    python win_test.py
會問:環境(正式 / 測試)、帳號、電子密碼。密碼用隱藏輸入,不存檔、不印出來。
做的事:連線 → 登入 → 查 2330、2409 最近一個交易日的 1 分 K → 存成 CSV、印摘要 → 登出關閉。
這支程式裡沒有任何下單功能。
"""
import os, sys, time, datetime, getpass, pathlib, threading

HERE = pathlib.Path(__file__).parent.resolve()
if not (HERE / "YuantaSparkAPI.dll").exists():
    sys.exit("找不到 YuantaSparkAPI.dll:請把 win_test.py 放進元大 YuantaSparkAPI_win-x64_Python 資料夾再執行")

from pythonnet import load
load("coreclr")
import clr
sys.path.append(str(HERE))
if sys.platform == "win32":
    os.add_dll_directory(str(HERE))
clr.AddReference("YuantaSparkAPI")
from YuantaOneAPI import YuantaSparkAPITrader, enumEnvironmentMode, OnResponseEventHandler, enumLogType, enumMarketType, KLineType

got = {}                      # strIndex -> 回應
ev = {k: threading.Event() for k in ("Login", "GetKLine")}
kl = []


def on_response(intMark, dwIndex, strIndex, objHandle, objValue):
    try:
        if intMark == 0:                                   # 系統訊息(連線 / 斷線 / 錯誤)
            print(f"[系統] {dwIndex} {objValue}")
        elif intMark == 1 and strIndex == "Login":
            st = objValue.LoginStatus
            got["Login"] = (str(st.MsgCode), str(st.MsgContent), int(st.Count))
            print(f"[登入] 代碼 {st.MsgCode}  {st.MsgContent}  帳號筆數 {st.Count}")
            ev["Login"].set()
        elif intMark == 1 and strIndex == "GetKLine":
            lst = objValue.KLineList
            rows = [(str(lst[i].TimeStamp), lst[i].OpenPrice, lst[i].HighPrice, lst[i].LowPrice, lst[i].ClosePrice, lst[i].DealVol) for i in range(lst.Count)]
            kl.append((str(objValue.StockCode), rows))
            ev["GetKLine"].set()
        elif intMark == 1:
            print(f"[查詢回應] {strIndex}")
    except Exception as e:
        print(f"[回應處理錯誤] {strIndex}: {e}")


def last_trade_day():
    d = datetime.date.today()
    if datetime.datetime.now().hour < 14:                  # 今天還沒收盤就查前一天
        d -= datetime.timedelta(days=1)
    while d.weekday() >= 5:                                # 六、日往前推
        d -= datetime.timedelta(days=1)
    return d


def main():
    print("=== 元大 SPARK API 連線測試(只讀,不下單)===")
    env = input("環境:1 = 正式環境(預設)  2 = 測試環境 > ").strip()
    mode = enumEnvironmentMode.UAT if env == "2" else enumEnvironmentMode.PROD
    if env == "2":
        acct, pwd = "S98875005091", "1234"                 # 元大公開的測試帳號(測試環境要先登記固定 IP)
        print("測試環境:用元大公開測試帳號 S98875005091(要先匯入測試憑證、營業員要先登記固定 IP)")
    else:
        acct = input("證券帳號(分公司 4 碼 + 帳號 7 碼,例如 981R0123456)> ").strip().replace("-", "")
        # 分公司代號可能有英文字母(例如竹山 981R;元大範例寫成小寫 981r),照使用者打的大小寫送,不轉大寫
        acct = "S" + (acct[1:] if acct[:1] in ("S", "s") and len(acct) == 12 else acct)
        pwd = getpass.getpass("電子密碼(輸入時不會顯示)> ")

    api = YuantaSparkAPITrader()
    api.OnResponse += OnResponseEventHandler(on_response)
    api.SetLogType(enumLogType.COMMON)
    print("連線中…")
    api.Open(mode)
    time.sleep(2)
    print("登入中…")
    api.Login(acct, pwd)
    pwd = None
    if not ev["Login"].wait(20):
        print("!! 20 秒沒有收到登入回應。請看上面 [系統] 訊息;常見原因:API 還沒開通、憑證沒匯入、測試環境沒登記固定 IP")
        api.Close(); api.Dispose(); return
    code, msg, n = got["Login"]
    if not (code in ("0001", "00001") or n > 0):
        print(f"!! 登入失敗:{code} {msg}")
        api.Close(); api.Dispose(); return
    print("登入成功")
    time.sleep(2)

    day = last_trade_day().strftime("%Y/%m/%d")
    out = HERE / f"yuanta_kline_{day.replace('/', '')}.csv"
    with open(out, "w", encoding="utf-8-sig") as f:
        f.write("code,time,open,high,low,close,vol\n")
        for c in ("2330", "2409"):
            ev["GetKLine"].clear(); kl.clear()
            print(f"查 {c} {day} 1 分 K…")
            api.GetKLine(acct, KLineType(0), enumMarketType.TWSE, c, day, day)
            if not ev["GetKLine"].wait(15):
                print(f"!! {c} 15 秒沒有回應"); continue
            _, rows = kl[0]
            print(f"  {c}:{len(rows)} 根")
            for r in rows[:3] + ([("…",)] if len(rows) > 6 else []) + rows[-3:]:
                print("   ", *r)
            for r in rows:
                f.write(",".join([c] + [str(x) for x in r]) + "\n")
            time.sleep(1)
    print(f"已存檔:{out}")
    try:
        api.LogOut(); api.Close(); api.Dispose()
    except Exception:
        pass
    print("完成。請把上面印出來的內容(不含帳號)貼給 Claude。")


if __name__ == "__main__":
    main()
