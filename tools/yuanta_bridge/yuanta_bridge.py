"""元大 SPARK API 中繼程式(2026-10-22 使用者:「先寫好 等營業員回覆」)

盤中登入元大 SPARK API → 訂閱名單上每一檔的逐筆成交(分時明細)→ 拼成精準的 1 分 K →
每分鐘送到 Cloudflare Worker(POST ?ybars=push),手機之後改讀這份,和元大看盤軟體同一份資料。

用法(設定見 bridge.env.example,帳號密碼只放在主機上,不要進 GitHub):
  python3 yuanta_bridge.py run            # 常駐:每天 08:45 連線、13:36 收工,隔天再來
  python3 yuanta_bridge.py test 2330      # 連線 + 登入 + 訂閱一檔,印出逐筆成交 60 秒(驗證環境用)
  python3 yuanta_bridge.py kline 2330 2026/10/22   # 查元大當天 1 分 K(和推上去的比對用;上櫃股最後加 otc)
  python3 yuanta_bridge.py order-test 2885 B 35.15  # 只限測試環境:送 1 張限價單看回應(下單流程驗證)

預設只接行情;TRADE_ENABLE=1 才會照手機通知「一次一單」自動交易(trader.py),正式環境還要 TRADE_ALLOW_PROD=1。
"""
import json, os, re, sys, time, threading, urllib.request, datetime as dt
from pathlib import Path
from bars import BarBook
from trader import Trader, fetch_signals
from quotes import QuoteBook
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

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
    for k in list(cfg) + ["YT_ACCOUNT", "YT_PASS", "YT_PFX", "YT_PFX_PASS", "YT_ENV", "YT_SDK_DIR", "WORKER_URL", "PUSH_KEY", "POOL_URL", "VOL_MULT", "MAX_CODES", "YT_PMM_CHECK", "TRADE_ENABLE", "TRADE_ALLOW_PROD", "QUOTE_PORT", "CF_METRICS", "QUOTE_ENABLE"]:
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
    import System
    from System.Collections.Generic import List
    return Y, List, System


# 元大文件「回應事件」的 dwIndex 對照
SYS_MSG = {0: "其他訊息", 1: "Connect", 2: "Disconnect", 3: "網路異常", 4: "需下載新版 API", 5: "尚未連線", 6: "系統公告"}
QRY_MSG = {0: "總帳 / 子帳登入", 3: "尚未登入", 4: "輸入的登入帳號錯誤", 5: "功能代碼錯誤或權限不足", 6: "訂閱即時回報失敗",
           7: "SocketRPRead 失敗", 9: "加簽失敗 / 憑證異常", 10: "Logout", 11: "帳號資訊異常", 12: "取得已訂閱商品清單異常"}
QRY_FATAL = {4, 5, 9, 11}      # 帳號錯、沒權限、憑證壞、帳號資訊異常:重試也沒用,當天停止(錯的帳密連續試會被鎖)
QRY_RELOGIN = {3, 10}          # 尚未登入、被登出:重新連線登入


class LoginRejected(Exception):
    """元大明確拒絕登入(帳密錯、密碼凍結、沒權限):不能一直重試,錯的密碼連續試會把帳號鎖住"""


class Bridge:
    def __init__(self):
        self.Y, self.List, self.System = load_sdk()
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
        self.trader = None       # 一次一單自動交易(trader.py;TRADE_ENABLE=1 才會下單)
        self.quotes = QuoteBook(vol_mult=int(CFG.get("VOL_MULT", "1000")))   # 手機即時價(quotes.py)

    # 回應事件(元大的執行緒呼叫,不能在這裡做耗時的事)
    def on_response(self, intMark, dwIndex, strIndex, objHandle, objValue):
        try:
            if intMark == 0:
                msg = str(objValue)
                log(f"系統 [{dwIndex} {SYS_MSG.get(int(dwIndex), '')}] {msg}")
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
                if strIndex == "SendStockOrder":
                    r = objValue; st = r.ResultCount
                    for i in range(st.Count):
                        d = r.ResultList[i]
                        ok = int(d.ReplyCode) == 0
                        log(f"下單回應 {st.MsgCode} {st.MsgContent} 委託書號 {d.OrderNO} {'成功' if ok else '失敗 ' + str(d.ErrNO) + ' ' + str(d.Advisory)}")
                        if self.trader: self.trader.on_order_reply(str(d.OrderNO), ok, f"{d.ErrNO} {d.Advisory}")
                    if st.Count == 0 and self.trader:
                        self.trader.on_order_reply("", False, f"{st.MsgCode} {st.MsgContent}")
                    return
                if strIndex in ("SendAlgoCOOdrStrategy", "DeleteAlgoCOOdrStrategy"):
                    lst = objValue.ResultList
                    for i in range(lst.Count):
                        d = lst[i]; st = int(d.OrdStatus)   # 策略狀態:0 建立、1 執行中 = 成功;9 無法建立、911 建立失敗
                        log(f"條件單回應 {strIndex} 狀態 {st} 編號 {d.OrderNo} {d.Msg}")
                        if strIndex == "SendAlgoCOOdrStrategy" and self.trader:
                            self.trader.on_strategy_reply(str(d.OrderNo), st in (0, 1, 100, 101), str(d.Msg))
                    return
                if strIndex == "GetKLine" and self.kline_out is not None:
                    lst = objValue.KLineList
                    for i in range(lst.Count):
                        k = lst[i]
                        self.kline_out.append((str(k.TimeStamp), k.OpenPrice, k.HighPrice, k.LowPrice, k.ClosePrice, k.DealVol))
                    return
                if not strIndex or int(dwIndex) in QRY_FATAL or int(dwIndex) in QRY_RELOGIN:
                    d = int(dwIndex)
                    log(f"查詢回應 [{d} {QRY_MSG.get(d, '')}] {strIndex or ''} {objValue}")
                    if d in QRY_FATAL:
                        self.login_fail = f"{d} {QRY_MSG[d]} {objValue}"
                    elif d in QRY_RELOGIN:
                        self.logged_in.clear(); self.disconnected.set()
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
                    if sn < 0:                   # -1 = 清盤(漲跌停 / 參考價),不是成交;成交價欄位 = 開盤參考價(昨收)
                        self.quotes.on_ref(str(r.StkCode).strip(), float(r.DealPrice))
                        return
                    code, px = str(r.StkCode).strip(), float(r.DealPrice)
                    ok = self.book.add_tick(code, t.bytHour, t.bytMin, sn, px, int(r.DealVol), self.sent)
                    if ok:
                        self.n_ticks += 1
                        self.quotes.on_tick(code, now_tw().strftime("%Y%m%d"), t.bytHour, t.bytMin, t.bytSec, px, int(r.DealVol), float(r.BuyPrice), float(r.SellPrice))
                        if self.trader and px > 0: self.trader.on_tick(code, px)
                    if self.printer:
                        self.printer(f"{r.StkCode} {t.bytHour:02d}:{t.bytMin:02d}:{t.bytSec:02d}.{t.ushtMSec:03d} 序 {sn} 價 {r.DealPrice} 量 {r.DealVol} 內外 {flag}")
                    return
                if strIndex == "RR_RealReport":       # 即時回報(登入即訂閱):50 股票委託、51 股票成交
                    r = objValue; rt = int(r.RptType)
                    if rt in (50, 51):
                        log(f"回報 {'成交' if rt == 51 else '委託'} {r.CompanyNo} {r.BS} 數量 {r.OrderQty} 價 {r.Price} 狀態 {r.OrderStatus} 書號 {r.OrderNo} 編號 {r.BasketNo}")
                        if self.trader: self.trader.on_report(rt, str(r.OrderNo), int(r.OrderStatus), int(r.OrderQty), float(r.Price), str(r.BasketNo), str(r.CompanyNo), str(r.BS).strip())
                    return
                if not strIndex or int(dwIndex) == 1:
                    log(f"!! 訂閱 / 取消訂閱失敗 [{dwIndex}] {strIndex or ''} {objValue}")
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
        Y = self.Y; out = []; today = now_tw().strftime("%Y-%m-%d")
        for s in pool[: int(CFG.get("MAX_CODES", "200"))]:
            otc = s.get("market") in ("tpex", "otc")
            out.append((str(s["code"]), Y.enumMarketType.TWOTC if otc else Y.enumMarketType.TWSE))
            y = s.get("close") if (s.get("date") or j.get("date", "")) < today else s.get("prev")     # 名單日期比今天早 → 那天的收盤 = 昨收
            self.quotes.set_meta(str(s["code"]), s.get("name", ""), "otc" if otc else "tse", y)
        log(f"名單 {j.get('date', '') if isinstance(j, dict) else ''} 共 {len(out)} 檔")
        return out

    # ── 下單通道(給 trader.py 用)──
    def send_order(self, code, mkt, side, lots, price, typ, tif, tag, flag=" "):
        """國內證券下單(元大文件「國內證券下單」):整股、委託單;flag ' ' 限價 / H 漲停 / L 跌停(非限價 Price 填 0);tif 0 ROD / 3 IOC / 4 FOK"""
        o = self.Y.StockOrder()
        o.Identify = 1; o.Account = CFG["YT_ACCOUNT"]; o.APCode = 0; o.TradeKind = 0; o.OrderType = str(typ)
        o.StkCode = code; o.PriceFlag = flag; o.Price = float(price) if flag == " " else 0.0; o.OrderQty = int(lots); o.BuySell = side
        o.OrderNo = ""; o.TradeDate = now_tw().strftime("%Y/%m/%d"); o.BasketNo = tag; o.Time_in_force = str(tif)
        lst = self.List[self.Y.StockOrder](); lst.Add(o)
        self._last_order = {"code": code, "side": side, "lots": lots, "price": price, "typ": typ}
        ok = self.api.SendStockOrder(CFG["YT_ACCOUNT"], lst)
        if not ok: log("!! SendStockOrder 回傳失敗")
        return None   # 委託書號從回應事件來(trader 用 BasketNo 對)

    def cancel_order(self, order_no, code):
        lo = getattr(self, "_last_order", None) or {}
        o = self.Y.StockOrder()
        o.Identify = 2; o.Account = CFG["YT_ACCOUNT"]; o.APCode = 0; o.TradeKind = 4; o.OrderType = str(lo.get("typ", "0"))   # TradeKind 04 = 取消
        o.StkCode = code or lo.get("code", ""); o.PriceFlag = " "; o.Price = 0; o.OrderQty = 0; o.BuySell = lo.get("side", "B")
        o.OrderNo = order_no; o.TradeDate = now_tw().strftime("%Y/%m/%d"); o.BasketNo = ""; o.Time_in_force = "0"
        lst = self.List[self.Y.StockOrder](); lst.Add(o)
        self.api.SendStockOrder(CFG["YT_ACCOUNT"], lst)

    def place_oco(self, code, mkt, d, lots, tp, sl):
        """元大二擇一條件單(元大文件「新增條件單」OCOStrategy):停損、停利兩個成交價條件,觸發就停;
           平倉單 = 空單買回掛漲停 / 多單賣出掛跌停,ROD。做空 d=-1:停損 = 成交價 ≥ sl、停利 = 成交價 ≤ tp;做多相反"""
        Y = self.Y
        mk = Y.enumMarketType.TWOTC if str(mkt).upper() in ("TWOTC", "OTC", "TPEX") else Y.enumMarketType.TWSE
        today = now_tw(); dt_ = self.System.DateTime(today.year, today.month, today.day)
        def cond(value, direction):
            c = Y.StrategySettings(); c.MarketType = mk; c.StkCode = code
            c.Condition = Y.StrategyCondition1(1); c.Direction = direction; c.Value = float(value)   # 1 成交價;方向 1 ≥、2 ≤
            return c
        def order():
            o = Y.OrderSettings2[Y.OrderType2, Y.OrderPriceType1]()
            o.MarketType = mk; o.StkCode = code; o.TimeInforce = 0; o.OrderValue = 0; o.OrderQty = int(lots)
            o.OrderType = Y.OrderType2(1 if d < 0 else 2)            # 1 現股買進(空單買回)、2 現股賣出(多單賣出)
            o.PriceType = Y.OrderPriceType1(3 if d < 0 else 5)       # 3 漲停、5 跌停
            return o
        x = Y.OCOStrategy(); x.Account = CFG["YT_ACCOUNT"]; x.EffTime = dt_; x.ExpTime = dt_; x.Order = Y.StrategyOrder1(2)   # 觸發就停
        x.StrategySettings1 = cond(sl, 1 if d < 0 else 2); x.OrderSettings1 = order()     # 條件 1:停損
        x.StrategySettings2 = cond(tp, 2 if d < 0 else 1); x.OrderSettings2 = order()     # 條件 2:停利
        lst = self.List[Y.OCOStrategy](); lst.Add(x)
        ok = self.api.SendAlgoCOOdrStrategy[Y.OCOStrategy](CFG["YT_ACCOUNT"], lst)
        log(f"[交易] 送元大條件單 OCO {code} {'空' if d < 0 else '多'} {lots} 張 停損 {sl} 停利 {tp}:{'送出' if ok else '失敗'}")
        if not ok and self.trader: self.trader.on_strategy_reply("", False, "SendAlgoCOOdrStrategy 回傳失敗")

    def delete_oco(self, no):
        Y = self.Y
        x = Y.DeleteStrategy(); x.Account = CFG["YT_ACCOUNT"]; x.StrategyType = Y.StrategyType(3); x.StrategyNo = str(no).strip()   # 3 二擇一
        lst = self.List[Y.DeleteStrategy](); lst.Add(x)
        self.api.DeleteAlgoCOOdrStrategy(CFG["YT_ACCOUNT"], lst)

    # ── 手機即時價:本機小服務 + Cloudflare Tunnel(Worker 的 ?codes 先問這裡,不通才問證交所)──
    def serve_quotes(self):
        bridge = self
        class H(BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_GET(self):
                u = urlparse(self.path)
                if u.path == "/health":
                    return self.reply({"ok": True, "age": bridge.quotes.mis([])["age"]})
                if u.path != "/quotes" or self.headers.get("X-Quote-Key") != CFG.get("PUSH_KEY"):
                    return self.reply({"error": "not found"}, 404)
                codes = [c for c in parse_qs(u.query).get("codes", [""])[0].split(",") if c]
                self.reply(bridge.quotes.mis(codes))
            def reply(self, obj, code=200):
                b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(code); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Content-Length", str(len(b)))
                self.end_headers(); self.wfile.write(b)
        port = int(CFG.get("QUOTE_PORT", "8787"))
        srv = ThreadingHTTPServer(("127.0.0.1", port), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        threading.Thread(target=self.register_tunnel, daemon=True).start()
        log(f"手機即時價服務:127.0.0.1:{port}")

    def register_tunnel(self):
        """cloudflared 快速通道每次啟動網址會變:每分鐘看一次(cloudflared metrics 的 /quicktunnel),變了就告訴 Worker"""
        last = None
        while True:
            try:
                j = json.loads(urllib.request.urlopen(f"http://{CFG.get('CF_METRICS', '127.0.0.1:20241')}/quicktunnel", timeout=5).read().decode())
                host = j.get("hostname")
                if host and host != last:
                    body = json.dumps({"url": f"https://{host}"}).encode()
                    req = urllib.request.Request(CFG["WORKER_URL"].rstrip("/") + "/?yquote=register", data=body, method="POST",
                                                 headers={"Content-Type": "application/json", "X-Push-Key": CFG["PUSH_KEY"]})
                    r = json.loads(urllib.request.urlopen(req, timeout=15).read().decode())
                    if r.get("ok"): last = host; log(f"即時價通道登記到 Worker:https://{host}")
                    else: log("即時價通道登記失敗", r)
            except Exception as e:
                if last is None: log("等 cloudflared 通道…", e)
            time.sleep(60)

    def make_trader(self):
        cfg = dict(CFG)
        if CFG.get("YT_ENV", "UAT").upper() == "PROD" and CFG.get("TRADE_ALLOW_PROD", "0") != "1":
            if cfg.get("TRADE_ENABLE") == "1": log("[交易] 正式環境沒有 TRADE_ALLOW_PROD=1,自動交易關閉(只接行情)")
            cfg["TRADE_ENABLE"] = "0"
        gw = type("GW", (), {"send": lambda _s, *a: self.send_order(*a), "cancel": lambda _s, no, code="": self.cancel_order(no, code),
                             "place_oco": lambda _s, *a: self.place_oco(*a), "delete_oco": lambda _s, no: self.delete_oco(no)})()
        return Trader(cfg, gw, log, now_tw)

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
        self.quotes.q.clear()            # 新的一天:即時價從頭累計
        self.codes = self.load_codes()
        self.trader = self.make_trader()
        self.trader.prime(fetch_signals(CFG["WORKER_URL"], day))
        fails = 0; k = 0
        while now_tw().strftime("%H:%M") < "13:36":
            try:
                if self.login_fail:
                    raise LoginRejected(self.login_fail)
                if self.api is None or self.disconnected.is_set():
                    self.close(); self.connect(); self.subscribe(self.codes); fails = 0
                time.sleep(1); k += 1
                sig = fetch_signals(CFG["WORKER_URL"], day) if (self.trader.enabled and k % 3 == 0) else None   # 手機通知每 3 秒看一次
                self.trader.step(sig)
                if k % 10 == 0:
                    self.flush(day)
            except LoginRejected as e:
                log(f"!! 元大拒絕登入:{e}。今天不再重試,請檢查 bridge.env 的帳號 / 密碼 / 權限後重啟服務")
                self.close(); break
            except Exception as e:
                fails += 1; log("錯誤", e, f"(第 {fails} 次)")
                self.close(); time.sleep(min(60, 10 * fails))   # 登入失敗不能太頻繁(每 4 秒最多 1 次,一天 1000 次)
        self.flush(day, final=True)
        log(f"收工:逐筆 {self.n_ticks} 筆,推送 {len(self.sent)} 分鐘;交易 {self.trader.n_trades} 筆 淨 {self.trader.pnl:+,.0f} {self.trader.journal}")
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
        mkt = b.Y.enumMarketType.TWOTC if (len(sys.argv) > 4 and sys.argv[4].lower() in ("otc", "tpex", "twotc")) else b.Y.enumMarketType.TWSE
        b.kline_out = []; b.connect()
        b.api.GetKLine(CFG["YT_ACCOUNT"], b.Y.KLineType(0), mkt, code, day, day)   # KLineType 0 = 一分線(元大文件「列舉物件」);上櫃股加參數 otc
        time.sleep(5)
        for k in b.kline_out: print(*k)
        b.close(); return
    if cmd == "order-test":
        # 只在測試環境:送 1 張限價單,印 20 秒的下單回應和回報(元大測試環境依委託書號尾碼模擬全成交 / 不成交 / 部分成交 / 價穩失效)
        if CFG.get("YT_ENV", "UAT").upper() != "UAT":
            sys.exit("order-test 只能在測試環境(YT_ENV=UAT)")
        code, side, price = sys.argv[2], sys.argv[3].upper(), float(sys.argv[4])
        b.connect(); b.send_order(code, "TWSE", side, 1, price, "0", "0", "TEST1")
        time.sleep(20); b.close(); return
    # run:常駐
    for k in ("WORKER_URL", "PUSH_KEY"):
        if not CFG.get(k):
            sys.exit(f"缺少設定 {k}")
    if CFG.get("QUOTE_ENABLE", "0") == "1":        # 預設關閉:要使用者自己決定要不要讓手機即時價走元大(會透過 Cloudflare Tunnel 對外開一個唯讀、要金鑰的報價服務)
        b.serve_quotes()
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
