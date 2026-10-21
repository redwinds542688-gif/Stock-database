"""元大 SPARK API 中繼程式(2026-10-22 使用者:「先寫好 等營業員回覆」)

盤中登入元大 SPARK API → 訂閱名單上每一檔的逐筆成交(分時明細)→ 拼成精準的 1 分 K →
每分鐘送到 Cloudflare Worker(POST ?ybars=push),手機之後改讀這份,和元大看盤軟體同一份資料。

用法(設定見 bridge.env.example,帳號密碼只放在主機上,不要進 GitHub):
  python3 yuanta_bridge.py run            # 常駐:每天 08:45 連線、13:36 收工,隔天再來
  python3 yuanta_bridge.py test 2330      # 連線 + 登入 + 訂閱一檔,印出逐筆成交 60 秒(驗證環境用)
  python3 yuanta_bridge.py kline 2330 2026/10/22   # 查元大當天 1 分 K(和推上去的比對用)

只接行情,不下單。
"""
import json, os, re, sys, time, threading, urllib.request, datetime as dt
from pathlib import Path
from bars import BarBook

HERE = Path(__file__).resolve().parent
TW = dt.timezone(dt.timedelta(hours=8))
now_tw = lambda: dt.datetime.now(TW)


def load_env():
    """讀 bridge.env(KEY=VALUE),環境變數優先"""
    cfg = {}
    f = Path(os.environ.get("BRIDGE_ENV", HERE / "bridge.env"))
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1); cfg[k.strip()] = re.split(r"\s+#", v, 1)[0].strip()   # 去掉行尾註解
    for k in list(cfg) + ["YT_ACCOUNT", "YT_PASS", "YT_PFX", "YT_PFX_PASS", "YT_ENV", "YT_SDK_DIR", "WORKER_URL", "PUSH_KEY", "POOL_URL", "VOL_MULT", "MAX_CODES", "YT_PMM_CHECK"]:
        if os.environ.get(k): cfg[k] = os.environ[k]
    return cfg


CFG = load_env()
LOG_LOCK = threading.Lock()


def log(*a):
    with LOG_LOCK:
        print(now_tw().strftime("%m-%d %H:%M:%S"), *a, flush=True)


# ── 元大元件 ──
def load_sdk():
    sdk = Path(CFG.get("YT_SDK_DIR", HERE / "sdk")).resolve()
    sys.path.append(str(sdk))
    if sys.platform == "win32":
        os.add_dll_directory(str(sdk))
    from pythonnet import load
    load("coreclr")
    import clr
    clr.AddReference("System.Collections")
    clr.AddReference("YuantaSparkAPI")
    import YuantaOneAPI as Y
    from System.Collections.Generic import List
    return Y, List


class LoginRejected(Exception):
    """元大明確拒絕登入(帳密錯、密碼凍結、沒權限):不能一直重試,錯的密碼連續試會把帳號鎖住"""


class Bridge:
    def __init__(self):
        self.Y, self.List = load_sdk()
        self.api = None
        self.logged_in = threading.Event()
        self.login_fail = None   # 登入被拒的原因(帳密錯、凍結、沒權限)→ 今天不再重試,免得把帳號鎖住
        self.disconnected = threading.Event()
        self.book = BarBook(vol_mult=int(CFG.get("VOL_MULT", "1000")))
        self.sent = set()
        self.codes = []          # [(code, enumMarketType)]
        self.printer = None      # test 模式:每筆印出
        self.kline_out = None
        self.n_ticks = 0

    # 回應事件(元大的執行緒呼叫,不能在這裡做耗時的事)
    def on_response(self, intMark, dwIndex, strIndex, objHandle, objValue):
        try:
            if intMark == 0:
                msg = str(objValue)
                log(f"系統 [{dwIndex}] {msg}")
                if dwIndex in (2, 3, 5):        # 2 斷線、3 網路異常、5 尚未連線
                    self.disconnected.set(); self.logged_in.clear()
                if dwIndex == 4:
                    log("!! 元大要求下載新版 API 元件,請更新 sdk/ 後重啟")
                return
            if intMark == 1:
                if strIndex == "Login":
                    st = objValue.LoginStatus
                    log(f"登入 {st.MsgCode} {st.MsgContent} 帳號數 {st.Count}")
                    if st.MsgCode in ("0001", "00001") or st.Count > 0:
                        self.logged_in.set()
                    else:
                        # 元大文件「登入」:0000 執行失敗、0102 密碼凍結或未啟用、0112 無此權限使用功能
                        self.login_fail = f"{st.MsgCode} {st.MsgContent}"
                    return
                if strIndex == "GetKLine" and self.kline_out is not None:
                    lst = objValue.KLineList
                    for i in range(lst.Count):
                        k = lst[i]
                        self.kline_out.append((str(k.TimeStamp), k.OpenPrice, k.HighPrice, k.LowPrice, k.ClosePrice, k.DealVol))
                    return
                if not strIndex:
                    log(f"查詢錯誤 [{dwIndex}] {objValue}")
                    return
                log(f"查詢回應 {strIndex} [{dwIndex}]")
                return
            if intMark == 2:
                if strIndex == "SubscribeStockTick":
                    r = objValue
                    flag = int(r.InOutFlag)
                    if flag > 3:                 # 10~15 = 揭示 / 暫緩撮合 / 暫停交易,不是成交
                        return
                    t = r.Time
                    sn = int(r.SerialNo)
                    if sn < 0:                   # -1 = 清盤(漲跌停 / 參考價),不是成交
                        return
                    ok = self.book.add_tick(str(r.StkCode).strip(), t.bytHour, t.bytMin, sn, float(r.DealPrice), int(r.DealVol), self.sent)
                    if ok:
                        self.n_ticks += 1
                    if self.printer:
                        self.printer(f"{r.StkCode} {t.bytHour:02d}:{t.bytMin:02d}:{t.bytSec:02d}.{t.ushtMSec:03d} 序 {sn} 價 {r.DealPrice} 量 {r.DealVol} 內外 {flag}")
                    return
                if not strIndex:
                    log(f"訂閱錯誤 [{dwIndex}] {objValue}")
                return
        except Exception as e:      # 事件裡的例外不能丟回元大元件
            log("on_response 例外", e)

    def connect(self):
        Y = self.Y
        mode = Y.enumEnvironmentMode.PROD if CFG.get("YT_ENV", "UAT").upper() == "PROD" else Y.enumEnvironmentMode.UAT
        self.api = Y.YuantaSparkAPITrader(str(HERE / "log"))
        self.api.SetLogType(Y.enumLogType.COMMON)
        if CFG.get("YT_PMM_CHECK", "1") == "0":
            self.api.SetPMMServerCheck(False)    # 元大文件「API 相關設定」:是否檢查 PMMServer(預設檢查);連不上時可試著關掉
        self.api.OnResponse += self.on_response
        self.disconnected.clear(); self.logged_in.clear()
        self.api.Open(mode)
        time.sleep(2)
        acc, pw = CFG["YT_ACCOUNT"], CFG["YT_PASS"]
        self.login_fail = None
        if sys.platform == "win32":
            self.api.Login(acc, pw)
        else:
            self.api.Login(CFG["YT_PFX"], CFG["YT_PFX_PASS"], acc, pw)   # Linux / Mac 要憑證絕對路徑 + 憑證密碼
        for _ in range(40):
            if self.logged_in.wait(0.5): return
            if self.login_fail: raise LoginRejected(self.login_fail)
        raise RuntimeError("登入逾時(網路、憑證、API 權限、固定 IP 是否開通?)")

    def close(self):
        try:
            if self.api:
                self.api.LogOut(); self.api.Close(); self.api.Dispose()
        except Exception as e:
            log("關閉例外", e)
        self.api = None

    def subscribe(self, codes):
        Y = self.Y
        for i in range(0, len(codes), 200):          # 單次最多 200 檔;一秒最多 10 次
            lst = self.List[Y.StockTick]()
            for code, mkt in codes[i:i + 200]:
                s = Y.StockTick(); s.MarketType = mkt; s.StockCode = code; lst.Add(s)
            ok = self.api.SubscribeStockTick(CFG["YT_ACCOUNT"], lst)
            log(f"訂閱 {i + 1}~{min(i + 200, len(codes))} 檔:{'送出' if ok else '失敗'}")
            time.sleep(0.5)

    def load_codes(self):
        url = CFG.get("POOL_URL", "https://raw.githubusercontent.com/redwinds542688-gif/Stock-database/main/pool.json")
        j = json.loads(urllib.request.urlopen(url, timeout=30).read().decode("utf-8"))
        pool = j.get("pool", j) if isinstance(j, dict) else j
        Y = self.Y; out = []
        for s in pool[: int(CFG.get("MAX_CODES", "200"))]:
            otc = s.get("market") in ("tpex", "otc")
            out.append((str(s["code"]), Y.enumMarketType.TWOTC if otc else Y.enumMarketType.TWSE))
        log(f"名單 {j.get('date', '') if isinstance(j, dict) else ''} 共 {len(out)} 檔")
        return out

    # ── 推到 Worker ──
    def push(self, day, minute, bars):
        if not bars:
            return True
        body = json.dumps({"d": day, "m": minute, "bars": bars}).encode()
        req = urllib.request.Request(CFG["WORKER_URL"].rstrip("/") + "/?ybars=push", data=body, method="POST",
                                     headers={"Content-Type": "application/json", "X-Push-Key": CFG["PUSH_KEY"]})
        for k in range(3):
            try:
                r = json.loads(urllib.request.urlopen(req, timeout=15).read().decode())
                if r.get("ok"):
                    return True
                log("推送被拒", r)
                return False
            except Exception as e:
                log(f"推送失敗 {minute} 第 {k + 1} 次", e); time.sleep(2)
        return False

    def flush(self, day, final=False):
        """推送已經走完的分鐘(現在分鐘之前);收盤後 final=True 全推"""
        cur = now_tw().strftime("%H:%M")
        for m in self.book.minutes():
            if m in self.sent or (not final and m >= cur):
                continue
            if self.push(day, m, self.book.take(m)):
                self.sent.add(m)
        for m in self.book.take_dirty():          # 晚到的成交 → 整分鐘重送
            if m in self.sent:
                self.push(day, m, self.book.take(m))

    def run_day(self):
        day = now_tw().strftime("%Y-%m-%d")
        self.book = BarBook(vol_mult=int(CFG.get("VOL_MULT", "1000"))); self.sent = set(); self.n_ticks = 0
        self.codes = self.load_codes()
        fails = 0
        while now_tw().strftime("%H:%M") < "13:36":
            try:
                if self.api is None or self.disconnected.is_set():
                    self.close(); self.connect(); self.subscribe(self.codes); fails = 0
                for _ in range(10):
                    time.sleep(1)
                self.flush(day)
            except LoginRejected as e:
                log(f"!! 元大拒絕登入:{e}。今天不再重試,請檢查 bridge.env 的帳號 / 密碼 / 權限後重啟服務")
                self.close(); break
            except Exception as e:
                fails += 1; log("錯誤", e, f"(第 {fails} 次)")
                self.close(); time.sleep(min(60, 10 * fails))   # 登入失敗不能太頻繁(每 4 秒最多 1 次,一天 1000 次)
        self.flush(day, final=True)
        log(f"收工:逐筆 {self.n_ticks} 筆,推送 {len(self.sent)} 分鐘")
        self.close()


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    for k in ("YT_ACCOUNT", "YT_PASS"):
        if not CFG.get(k):
            sys.exit(f"缺少設定 {k}(見 bridge.env.example)")
    b = Bridge()
    if cmd == "test":
        code = sys.argv[2] if len(sys.argv) > 2 else "2330"
        b.printer = log
        b.connect(); b.subscribe([(code, b.Y.enumMarketType.TWSE)])
        time.sleep(60); b.close(); return
    if cmd == "kline":
        code, day = sys.argv[2], sys.argv[3]
        b.kline_out = []; b.connect()
        b.api.GetKLine(CFG["YT_ACCOUNT"], b.Y.KLineType(0), b.Y.enumMarketType.TWSE, code, day, day)   # 0 = 一分線
        time.sleep(5)
        for k in b.kline_out: print(*k)
        b.close(); return
    # run:常駐
    for k in ("WORKER_URL", "PUSH_KEY"):
        if not CFG.get(k):
            sys.exit(f"缺少設定 {k}")
    while True:
        n = now_tw(); hm = n.strftime("%H:%M")
        if n.weekday() < 5 and "08:45" <= hm < "13:36":
            try:
                b.run_day()
            except Exception as e:
                log("當天執行失敗", e)
            while now_tw().strftime("%H:%M") < "13:36":   # 今天收工(或登入被拒)後等到收盤時間過了,不重跑
                time.sleep(60)
        else:
            time.sleep(30)


if __name__ == "__main__":
    main()
