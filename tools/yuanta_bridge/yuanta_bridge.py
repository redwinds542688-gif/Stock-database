"""元大 SPARK API 中繼程式(2026-10-22 使用者:「先寫好 等營業員回覆」)

盤中登入元大 SPARK API → 訂閱名單上每一檔的逐筆成交(分時明細)→ 拼成精準的 1 分 K →
每分鐘送到 Cloudflare Worker(POST ?ybars=push),手機之後改讀這份,和元大看盤軟體同一份資料。

用法(設定見 bridge.env.example,帳號密碼只放在主機上,不要進 GitHub):
  python3 yuanta_bridge.py run            # 常駐:每天 08:45 連線、13:36 收工,隔天再來
  python3 yuanta_bridge.py test 2330      # 連線 + 登入 + 訂閱一檔,印出逐筆成交 60 秒(驗證環境用)
  python3 yuanta_bridge.py kline 2330 2026/10/22   # 查元大當天 1 分 K(和推上去的比對用;上櫃股最後加 otc)
  python3 yuanta_bridge.py order-test 2885 B 35.15  # 只限測試環境:送 1 張限價單看回應(下單流程驗證)

預設只接行情;TRADE_ENABLE=1 才會送真單(元三、手機強制模式),再加 TRADE_AUTO=1 才會照手機通知自動進場(trader.py,同時最多 5 檔),正式環境還要 TRADE_ALLOW_PROD=1。
2026-10-28 手機強制進場 / 強制平倉:手機 → Worker(?trade=cmd / ?trade=pos)→ Cloudflare 通道 → 這支程式的 /trade/cmd、/trade/pos;
  要 QUOTE_ENABLE=1(通道);TRADE_CMD_PIN 可不設(2026-10-29 使用者決定不用密碼),有設手機就要帶同一組。
"""
import math
import json, os, re, sys, time, threading, urllib.request, urllib.error, datetime as dt
from pathlib import Path
from bars import BarBook
from trader import Trader, fetch_signals
from quotes import QuoteBook
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
# 2026-10-27 12:40 推 Worker 一整天 HTTP 403:Python 預設的連線身分「Python-urllib」可能被 Cloudflare 擋 → 所有連線一律帶一般瀏覽器的 User-Agent
_op = urllib.request.build_opener()
_op.addheaders = [("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36 YuantaBridge")]
urllib.request.install_opener(_op)

HERE = Path(__file__).resolve().parent
TW = dt.timezone(dt.timedelta(hours=8))
now_tw = lambda: dt.datetime.now(TW)


def fill_fees(ts, rate, minfee):
    """2026-10-30 使用者「金額不同」(和桐 1 張:程式 −188、元大 −216):元大手續費每筆最低 20 元。每筆成交先各自無條件捨去,
       同一張委託(order_no)加起來不到 minfee 就補到 minfee(補在那張委託的第一筆);回傳和 ts 同順序的手續費"""
    fees = [math.floor(t["lots"] * t["price"] * 1000 * rate + 1e-6) for t in ts]
    grp = {}
    for k, t in enumerate(ts): grp.setdefault(t.get("order_no") or ("#", k), []).append(k)
    for ks in grp.values():
        tot = sum(fees[k] for k in ks)
        if tot < minfee: fees[ks[0]] += minfee - tot
    return fees


def fills_pnl(trades, names=None, disc=0.25, broker=0.001425, tpex=0.0, tax=0.0015, minfee=20):
    """2026-10-28 今天成交 → 每檔輸贏(和手機同一套成本:單邊 = 成交金額 ×(證商費 × 折扣 + 證手費);證交稅 = 賣出金額 × 0.15% 當沖減半稅率)
       買賣張數一樣多 = 已沖銷;不一樣 = 有剩(只算對得上的張數,剩下的列 open)"""
    names = names or {}; rows = []; tot = 0
    by = {}
    for t in trades:
        if not t.get("lots"): continue
        by.setdefault(t["code"], []).append(t)
    for code, ts in sorted(by.items()):
        b = [t for t in ts if str(t["bs"]).upper().startswith("B")]; s_ = [t for t in ts if str(t["bs"]).upper().startswith("S")]
        bq = sum(t["lots"] for t in b); sq = sum(t["lots"] for t in s_)
        ba = sum(t["lots"] * t["price"] for t in b) / bq if bq else 0; sa = sum(t["lots"] * t["price"] for t in s_) / sq if sq else 0
        q = min(bq, sq)
        if q:
            buy_amt, sell_amt = ba * q * 1000, sa * q * 1000
            # 2026-10-28 18:25 對元大明細:手續費 = 每邊 成交金額 × 0.1425% × 2.5 折 無條件捨去(沒另收證手費),證交稅 = 賣出金額 × 0.15% 無條件捨去 → 台玻 −129、廣宇 +525、力積電 −3,767 全對
            # 2026-10-29 12:40 使用者「現在 元大是贏5928」(程式算 5,927):元大的手續費、證交稅是每一筆成交各自無條件捨去,不是整檔加總後才捨去,
            #   一檔有好幾筆成交(力積電 3 筆)就會差 1 元 → 買賣張數剛好對上(全沖掉)時改成每筆各算各捨去;有剩沒沖掉的才用均價估
            if bq == sq:
                fee = sum(fill_fees(ts, broker * disc + tpex, minfee))   # 2026-10-30 每筆最低 20 元
                tx = sum(math.floor(t["lots"] * t["price"] * 1000 * tax + 1e-6) for t in s_)
            else:
                fee = max(minfee, math.floor(buy_amt * (broker * disc + tpex) + 1e-6)) + max(minfee, math.floor(sell_amt * (broker * disc + tpex) + 1e-6)); tx = math.floor(sell_amt * tax + 1e-6)
            gross = sell_amt - buy_amt; net = round(gross - fee - tx)
        else:
            gross = fee = tx = 0; net = 0
        first = min(ts, key=lambda t: t.get("time", ""))
        rows.append({"code": code, "name": names.get(code, ""), "dir": "做多" if str(first["bs"]).upper().startswith("B") else "做空",
                     "buy": round(ba, 2), "sell": round(sa, 2), "lots": q, "open": bq - sq,
                     "gross": round(gross), "fee": round(fee), "tax": round(tx), "net": net, "n": len(ts)})
        tot += net
    return {"rows": rows, "net": tot, "n": len(trades)}


def fills_legs(trades, names=None, disc=0.25, broker=0.001425, tpex=0.0, tax=0.0015, minfee=20):
    """2026-10-29 12:45 使用者「要改」(力積電 空 3 張回補不小心買 4 張、再賠 1 張):每一趟一列。同一檔照成交時間先進先出配對:
       反向成交就和最早還沒沖掉的那筆配,配到幾張就是一趟(進價 = 先成交那筆、出價 = 後成交那筆);配不完的剩下留著等下一筆(最後還剩的 = 還沒沖掉,不列)。
       手續費 / 證交稅照元大:每筆成交各自無條件捨去;一筆成交拆成好幾趟時按張數分攤、零頭給最後一趟,所以每趟加起來 = 每檔總額,和元大對得上。"""
    names = names or {}; legs = []
    rate = broker * disc + tpex
    by = {}
    for t in trades:
        if not t.get("lots"): continue
        by.setdefault(t["code"], []).append(t)
    for code, ts in sorted(by.items()):
        ts = sorted(ts, key=lambda t: t.get("time", ""))
        # 每筆成交的費用先算好(整筆無條件捨去),配到幾張就分幾張的份
        fills = []; ffee = fill_fees(ts, rate, minfee)   # 2026-10-30 每筆最低 20 元
        for t, fe0 in zip(ts, ffee):
            amt = t["lots"] * t["price"] * 1000; sell = str(t["bs"]).upper().startswith("S")
            fills.append({"bs": "S" if sell else "B", "lots": t["lots"], "price": t["price"], "time": t.get("time", ""),
                          "fee": fe0, "tax": math.floor(amt * tax + 1e-6) if sell else 0, "left": t["lots"], "fee_left": None, "tax_left": None})
        for f in fills: f["fee_left"], f["tax_left"] = f["fee"], f["tax"]
        def take(f, q):   # 從這筆成交拿 q 張的費用份(最後一張份連零頭一起)
            if q >= f["left"]: fe, tx = f["fee_left"], f["tax_left"]
            else: fe, tx = math.floor(f["fee"] * q / f["lots"]), math.floor(f["tax"] * q / f["lots"])
            f["left"] -= q; f["fee_left"] -= fe; f["tax_left"] -= tx
            return fe, tx
        queue = []
        for f in fills:
            while f["left"] > 0:
                if not queue or queue[0]["bs"] == f["bs"]:
                    queue.append(f); break
                h = queue[0]; q = min(h["left"], f["left"])
                fe1, tx1 = take(h, q); fe2, tx2 = take(f, q)
                long = h["bs"] == "B"; ent, ex = h["price"], f["price"]
                gross = (ex - ent) * q * 1000 * (1 if long else -1); fee = fe1 + fe2; tx = tx1 + tx2
                legs.append({"code": code, "name": names.get(code, ""), "dir": "做多" if long else "做空", "ent": ent, "ex": ex, "lots": q,
                             "t0": h["time"], "t": f["time"], "gross": round(gross), "fee": fee, "tax": tx, "net": round(gross - fee - tx)})
                if h["left"] <= 0: queue.pop(0)
    legs.sort(key=lambda g: g["t"], reverse=True)   # 最新的一趟在最上面(和手機通知一樣)
    return legs


def gh_get(path, ref="main", timeout=30, repo=""):
    """2026-10-29 16:16 repo 改私人:GitHub 的檔案經 Worker 代讀(?gh=raw,帶中繼金鑰);沒設 PUSH_KEY 就直接抓 raw(repo 還公開時能用)
       18:05 repo="logic" = 私人庫 Stock-logic(判一二三、模擬器),一定要經 Worker"""
    if CFG.get("PUSH_KEY") and CFG.get("WORKER_URL"):
        url = CFG["WORKER_URL"].rstrip("/") + "/?gh=raw&path=" + path + ("&ref=" + ref if ref != "main" else "") + ("&repo=" + repo if repo else "") + f"&_={int(time.time())}"
        req = urllib.request.Request(url, headers={"X-Push-Key": CFG["PUSH_KEY"], "User-Agent": "yuanta-bridge/1"})
    else:
        req = urllib.request.Request(f"https://raw.githubusercontent.com/redwinds542688-gif/Stock-database/{ref}/{path}?t={int(time.time())}", headers={"User-Agent": "yuanta-bridge/1"})
    return urllib.request.urlopen(req, timeout=timeout).read()


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
        line = " ".join([now_tw().strftime("%m-%d %H:%M:%S")] + [str(x) for x in a])
        try: print(line, flush=True)
        except Exception: pass
        # 2026-10-24 Windows 工作排程器在背景跑(沒有視窗):LOG_DIR 有設就每天一個紀錄檔
        if CFG.get("LOG_DIR"):
            try:
                d = Path(CFG["LOG_DIR"]); d.mkdir(parents=True, exist_ok=True)
                with open(d / f"bridge_{now_tw().strftime('%Y-%m-%d')}.log", "a", encoding="utf-8") as f: f.write(line + "\n")
            except Exception: pass


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
        self.resume_buf = None   # 啟動時接回部位的查詢結果
        self.qlock = threading.RLock()   # 2026-10-28 平倉檢查 #9:查詢一次一個
        self.trade_up = threading.Event()   # 交易主機已連上(登入要等它)
        self.sent_tags = []      # 2026-10-28 送單順序(自訂編號;取消單是空字串):下單回應照順序回來,對回是哪一筆(同時有好幾檔在下單)
        self.tag_lock = threading.RLock()   # 送單 + 記編號是同一步(三個執行緒都會送單,順序不能交錯;獨立檢查 #3)
        self.pending_sub = []    # 手機強制進場的股票不在名單上 → 主迴圈補訂閱(元大元件只在主執行緒呼叫)
        # 2026-10-30 16:50 使用者(找領先指標 →「開始收內外盤和五檔」→「要做」):每一筆逐筆成交原樣存一份(時間到毫秒、序號、價、量、內外盤旗標、當下買價 / 賣價),
        #   存在 TICK_DIR\日期.csv,之後拿來研究主動買賣力道是不是領先。TICK_LOG=0 關掉
        self.tick_buf = []; self.tick_lock = threading.Lock()

    # 回應事件(元大的執行緒呼叫,不能在這裡做耗時的事)
    def on_response(self, intMark, dwIndex, strIndex, objHandle, objValue):
        try:
            if intMark == 0:
                msg = str(objValue)
                log(f"系統 [{dwIndex} {SYS_MSG.get(int(dwIndex), '')}] {msg}")
                if dwIndex in (2, 3, 5):        # 2 斷線、3 網路異常、5 尚未連線
                    self.disconnected.set(); self.logged_in.clear()
                    if "交易主機" in msg: self.trade_up.clear()
                if dwIndex == 1 and "交易主機" in msg:
                    self.trade_up.set()
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
                    for i in range(max(1, st.Count)):
                        with self.tag_lock:
                            tag = self.sent_tags.pop(0) if self.sent_tags else None   # 一次送一筆,回應照送出順序
                        try:                                                       # 回應本身有帶自訂編號就以它為準(元件有這個欄位的話)
                            bn = str(getattr(r.ResultList[i], "BasketNo", "") or "").strip() if st.Count else ""
                            if bn: tag = bn
                        except Exception: pass
                        if st.Count == 0:
                            log(f"下單回應 {st.MsgCode} {st.MsgContent}(沒有結果){' 編號 ' + tag if tag else ''}")
                            if self.trader and tag: self.trader.on_order_reply("", False, f"{st.MsgCode} {st.MsgContent}", tag)
                            break
                        d = r.ResultList[i]
                        ok = int(d.ReplyCode) == 0
                        log(f"下單回應 {st.MsgCode} {st.MsgContent} 委託書號 {d.OrderNO} 編號 {tag or '-'} {'成功' if ok else '失敗 ' + str(d.ErrNO) + ' ' + str(d.Advisory)}")
                        if self.trader and tag: self.trader.on_order_reply(str(d.OrderNO), ok, f"{d.ErrNO} {d.Advisory}", tag)
                    return
                if strIndex in ("SendAlgoCOOdrStrategy", "DeleteAlgoCOOdrStrategy"):
                    lst = objValue.ResultList
                    for i in range(lst.Count):
                        d = lst[i]; st = int(d.OrdStatus)   # 策略狀態:0 建立、1 執行中 = 成功;9 無法建立、911 建立失敗
                        log(f"條件單回應 {strIndex} 狀態 {st} 編號 {d.OrderNo} {d.Msg}")
                        if strIndex == "SendAlgoCOOdrStrategy" and self.trader:
                            self.trader.on_strategy_reply(str(d.OrderNo), st in (0, 1, 100, 101), str(d.Msg))
                    return
                if strIndex == "GetOrderTradeReport" and self.resume_buf is not None:
                    r = objValue
                    lots_of = lambda q: q // 1000 if q >= 1000 else q
                    for i in range(r.StkOrderList.Count):
                        o = r.StkOrderList[i]; self.resume_buf["tags"][str(o.OrderNo).strip()] = str(o.BasketNo).strip()
                        self.resume_buf.setdefault("types", {})[str(o.OrderNo).strip()] = str(o.OrderType).strip()
                        try: _cq = int(o.CancelQty)
                        except Exception: _cq = 0
                        rest = int(o.AfterQty) - int(o.OkQty) - _cq   # 2026-10-29 使用者實測(win_order_test):已取消的單 OrderStatus=30、CancelQty=1000、AfterQty=1000、OkQty=0 → 不扣 CancelQty 會當成還掛著
                        if int(o.OrderStatus) not in (1, 2, 24, 25, 30) and rest > 0:   # 還有沒成交的量、而且不是失敗 / 取消 / 失效 / 價穩失效 = 還掛著(含部分成交;2026-10-28 獨立檢查 #1,原本只認 0 / 20)
                            self.resume_buf["working"].append({"code": str(o.CompanyNo).strip(), "bs": str(o.BS).strip(), "lots": lots_of(rest),
                                                               "no": str(o.OrderNo).strip(), "typ": str(o.OrderType).strip(), "tag": str(o.BasketNo).strip()})
                    for i in range(r.StkTradeList.Count):
                        x = r.StkTradeList[i]; q = int(x.OkQty)
                        self.resume_buf["trades"].append({"code": str(x.CompanyNo).strip(), "bs": str(x.BS).strip(), "lots": q // 1000 if q >= 1000 else q,   # 成交量單位文件沒寫:股(≥1000)或張都能處理;使用者只做整張、不做零股(2026-10-22),不會混淆
                                                          "price": float(x.SPrice), "time": str(x.DateTime), "order_no": str(x.OrderNo).strip()})
                    self.resume_buf["got_rpt"] = True
                    log(f"接回查詢:今天委託 {r.StkOrderList.Count} 筆、成交 {r.StkTradeList.Count} 筆")
                    try: self.quota_from_report(r)
                    except Exception as e: log("額度計算失敗", e)
                    return
                if strIndex == "GetConditionStrategy" and self.resume_buf is not None:
                    lst = objValue.ConditionStrategyList
                    for i in range(lst.Count):
                        x = lst[i]
                        if int(x.StrategyStatus) in (0, 1, 99, 100, 101):          # 建立 / 執行中 / 觸發子單中 = 還有效
                            self.resume_buf["oco"].append({"no": str(x.StrategyNo).strip(), "code": str(x.StkCode).strip(), "status": int(x.StrategyStatus), "type": int(x.StrategyType)})
                    self.resume_buf["n_oco"] = self.resume_buf.get("n_oco", 0) + 1
                    self.resume_buf["got_oco"] = self.resume_buf["n_oco"] >= self.resume_buf.get("want_oco", 1)
                    log(f"接回查詢:有效的二擇一條件單 {len(self.resume_buf['oco'])} 張")
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
                    if CFG.get("TICK_LOG", "1") == "1":   # 2026-10-30 逐筆原樣記錄(內外盤研究);只放進記憶體,主迴圈每 10 秒寫檔
                        try:
                            with self.tick_lock: self.tick_buf.append(f"{code},{t.bytHour:02d}:{t.bytMin:02d}:{t.bytSec:02d}.{t.ushtMSec:03d},{sn},{px},{int(r.DealVol)},{flag},{float(r.BuyPrice)},{float(r.SellPrice)}\n")
                        except Exception: pass
                    ok = self.book.add_tick(code, t.bytHour, t.bytMin, sn, px, int(r.DealVol), self.sent)
                    if ok:
                        self.n_ticks += 1; self.last_tick = time.time()
                        self.quotes.on_tick(code, now_tw().strftime("%Y%m%d"), t.bytHour, t.bytMin, t.bytSec, px, int(r.DealVol), float(r.BuyPrice), float(r.SellPrice))
                        if self.trader and px > 0: self.trader.on_tick(code, px)
                    if self.printer:
                        self.printer(f"{r.StkCode} {t.bytHour:02d}:{t.bytMin:02d}:{t.bytSec:02d}.{t.ushtMSec:03d} 序 {sn} 價 {r.DealPrice} 量 {r.DealVol} 內外 {flag}")
                    return
                if strIndex == "RR_RealReport":       # 即時回報(登入即訂閱):50 股票委託、51 股票成交
                    r = objValue; rt = int(r.RptType)
                    if rt in (50, 51):
                        log(f"回報 {'成交' if rt == 51 else '委託'} {r.CompanyNo} {r.BS} 數量 {r.OrderQty} 價 {r.Price} 狀態 {r.OrderStatus} 書號 {r.OrderNo} 編號 {r.BasketNo}")
                        typ = str(getattr(r, "OrderType", "") or "").strip()   # 2026-10-28 平倉檢查 #6:委託種類(有的話元三平倉用同一種)
                        if self.trader: self.trader.on_report(rt, str(r.OrderNo), int(r.OrderStatus), int(r.OrderQty), float(r.Price), str(r.BasketNo), str(r.CompanyNo), str(r.BS).strip(), typ)
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
        self.disconnected.clear(); self.logged_in.clear(); self.trade_up.clear()
        with self.tag_lock: self.sent_tags.clear()                         # 重連:舊連線的回應不會來了,順序重新算
        self.api.Open(mode)
        # 2026-10-24 Windows 第一次實測:Open 後 2 秒就登入,交易主機還沒連上,登入請求被丟掉、元大完全不回應 → 等「交易主機 Is Connected」再登入
        if not self.trade_up.wait(30):
            raise RuntimeError("30 秒還沒連上元大交易主機(網路?)")
        time.sleep(1)
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

    def flush_ticks(self, day):
        """2026-10-30 逐筆記錄寫檔:TICK_DIR(預設 ticks_yuanta)\日期.csv,欄位 code,time,sn,price,vol,flag,bid,ask(flag = 元大 InOutFlag 內外盤)"""
        with self.tick_lock: buf, self.tick_buf = self.tick_buf, []
        if not buf: return
        try:
            d = Path(CFG.get("TICK_DIR", HERE / "ticks_yuanta")); d.mkdir(parents=True, exist_ok=True); f = d / f"{day}.csv"; new = not f.exists()
            with open(f, "a", encoding="utf-8") as fh:
                if new: fh.write("code,time,sn,price,vol,flag,bid,ask\n")
                fh.writelines(buf)
        except Exception as e:
            log("逐筆記錄寫不進去", e)

    def subscribe(self, codes):
        Y = self.Y
        for i in range(0, len(codes), 200):          # 單次最多 200 檔;一秒最多 10 次
            lst = self.List[Y.StockTick]()
            for code, mkt in codes[i:i + 200]:
                s = Y.StockTick(); s.MarketType = mkt; s.StockCode = code; lst.Add(s)
            ok = self.api.SubscribeStockTick(CFG["YT_ACCOUNT"], lst)
            log(f"訂閱 {i + 1}~{min(i + 200, len(codes))} 檔:{'送出' if ok else '回傳失敗(10/27 回傳失敗照樣收到 51 萬筆逐筆,以收工時的逐筆筆數為準)'}")
            time.sleep(0.5)

    def load_codes_safe(self):
        """2026-10-28 平倉檢查 #8:盤中重開時 GitHub 剛好連不到 → 不能整天停擺(連 13:20 都不平):重試 3 次,再不行用本機存的名單"""
        for i in range(3):
            try: return self.load_codes()
            except Exception as e: log(f"抓名單失敗(第 {i + 1} 次)", e); time.sleep(10)
        fs = sorted(HERE.glob("pool_*.json"))
        if fs:
            log(f"!! 改用本機名單 {fs[-1].name}"); self.phone_alert("⚠ 抓不到今天的名單", f"中繼程式改用本機 {fs[-1].name} 繼續(部位照常管理)")
            return self.load_codes(json.loads(fs[-1].read_text(encoding="utf-8")))
        log("!! 本機也沒有名單,先不訂閱名單股票(部位照常接回管理)"); self.phone_alert("⚠ 抓不到名單", "中繼程式只管部位,不收名單行情")
        return []

    def load_codes(self, j=None):
        if j is None:   # 2026-10-29 16:16 名單經 Worker 代讀(repo 改私人也讀得到);POOL_URL 有設就照舊直接抓那個網址
            j = json.loads((urllib.request.urlopen(CFG["POOL_URL"], timeout=30).read() if CFG.get("POOL_URL") else gh_get("pool.json")).decode("utf-8"))
        pool = j.get("pool", j) if isinstance(j, dict) else j
        Y = self.Y; out = []; today = now_tw().strftime("%Y-%m-%d")
        for s in pool[: int(CFG.get("MAX_CODES", "200"))]:
            otc = s.get("market") in ("tpex", "otc")
            out.append((str(s["code"]), Y.enumMarketType.TWOTC if otc else Y.enumMarketType.TWSE))
            y = s.get("close") if (s.get("date") or j.get("date", "")) < today else s.get("prev")     # 名單日期比今天早 → 那天的收盤 = 昨收
            self.quotes.set_meta(str(s["code"]), s.get("name", ""), "otc" if otc else "tse", y)
        log(f"名單 {j.get('date', '') if isinstance(j, dict) else ''} 共 {len(out)} 檔")
        # 2026-10-27 21:30 早上用的名單存一份在本機:14:30 雲端名單換成明天的之後,17:05 補抓今天的 1 分 K 還找得到今天交易的是哪些股票(原本 0 檔)
        try: (HERE / f"pool_{today}.json").write_text(json.dumps(j, ensure_ascii=False), encoding="utf-8")
        except Exception as e: log("存今天名單失敗", e)
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
        with self.tag_lock:                                                 # 記編號和送出是同一步,別的執行緒插不進來
            self.sent_tags.append(tag)
            ok = self.api.SendStockOrder(CFG["YT_ACCOUNT"], lst)
            if not ok:
                log("!! SendStockOrder 回傳失敗")
                if self.sent_tags and self.sent_tags[-1] == tag: self.sent_tags.pop()   # 沒送出去就不會有回應
        return None   # 委託書號從回應事件來(trader 用 BasketNo 對)

    def cancel_order(self, order_no, code, bs=None, typ=None):
        lo = getattr(self, "_last_order", None) or {}
        o = self.Y.StockOrder()
        o.Identify = 2; o.Account = CFG["YT_ACCOUNT"]; o.APCode = 0; o.TradeKind = 4; o.OrderType = str(typ or lo.get("typ", "0"))   # TradeKind 04 = 取消
        o.StkCode = code or lo.get("code", ""); o.PriceFlag = " "; o.Price = 0; o.OrderQty = 0; o.BuySell = bs or lo.get("side", "B")
        o.OrderNo = order_no; o.TradeDate = now_tw().strftime("%Y/%m/%d"); o.BasketNo = ""; o.Time_in_force = "0"
        lst = self.List[self.Y.StockOrder](); lst.Add(o)
        with self.tag_lock:                                                 # 取消單的回應不給 trader
            self.sent_tags.append("")
            if not self.api.SendStockOrder(CFG["YT_ACCOUNT"], lst):
                if self.sent_tags and self.sent_tags[-1] == "": self.sent_tags.pop()

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
        self.delete_strategy(3, no)                                                     # 3 二擇一

    def delete_strategy(self, typ, no):
        Y = self.Y
        x = Y.DeleteStrategy(); x.Account = CFG["YT_ACCOUNT"]; x.StrategyType = Y.StrategyType(int(typ)); x.StrategyNo = str(no).strip()
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
                if self.headers.get("X-Quote-Key") != CFG.get("PUSH_KEY"):
                    return self.reply({"error": "not found"}, 404)
                if u.path == "/trade/pos":                               # 2026-10-28 手機長按股票:先問電腦這檔有沒有部位
                    return self.reply(bridge.trade_pos())
                if u.path == "/trade/fills":                             # 2026-10-28 18:20 今天元大帳號的成交 + 每檔輸贏(含手動單)
                    d = parse_qs(u.query).get("d", [""])[0]                # 2026-10-31 ?d=YYYY-MM-DD:前幾天的(收盤前存的 fills\日期.json;使用者「先1」10/30 明細空白)
                    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) and d != now_tw().strftime("%Y-%m-%d"):
                        return self.reply(bridge.fills_of_day(d))
                    return self.reply(bridge.trade_fills())
                if u.path == "/bars":                                    # 2026-10-30 預判(使用者「做法一」):電腦引擎直接拿本機逐筆組好的 1 分 K(含正在形成的這分鐘),不等推上 Worker
                    return self.reply(bridge.local_bars())
                if u.path != "/quotes":
                    return self.reply({"error": "not found"}, 404)
                codes = [c for c in parse_qs(u.query).get("codes", [""])[0].split(",") if c]
                self.reply(bridge.quotes.mis(codes))
            def do_POST(self):
                u = urlparse(self.path)
                if u.path != "/trade/cmd" or self.headers.get("X-Quote-Key") != CFG.get("PUSH_KEY"):
                    return self.reply({"error": "not found"}, 404)
                try:
                    n = int(self.headers.get("Content-Length") or 0); cmd = json.loads(self.rfile.read(min(n, 4096)).decode("utf-8") or "{}")
                except Exception:
                    return self.reply({"ok": False, "msg": "指令格式不對"}, 400)
                self.reply(bridge.trade_cmd(cmd))
            def reply(self, obj, code=200):
                b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(code); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Content-Length", str(len(b)))
                self.end_headers(); self.wfile.write(b)
        port = int(CFG.get("QUOTE_PORT", "8787"))
        srv = ThreadingHTTPServer(("127.0.0.1", port), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        threading.Thread(target=self.register_tunnel, daemon=True).start()
        log(f"手機即時價服務:127.0.0.1:{port}")

    def local_bars(self):
        """2026-10-30 今天元大逐筆組好的 1 分 K:{code: [[hm, o, h, l, c, v(股)], …]},含正在形成的這分鐘"""
        bk = getattr(self, "book", None); out = {}
        if bk is None: return {"bars": {}, "now": now_tw().strftime("%H:%M:%S")}
        with bk.lock:
            for m, row in bk.bars.items():
                for c, (o, h, l, cl, v) in row.items(): out.setdefault(c, []).append([m, o, h, l, cl, v])
        for c in out: out[c].sort()
        return {"bars": out, "now": now_tw().strftime("%H:%M:%S")}

    # ── 2026-10-28 手機強制進場 / 強制平倉 ──
    def trade_pos(self):
        t = self.trader
        if not t:
            return {"ok": False, "msg": "電腦的交易程式還沒啟動(盤前 / 收盤後)"}
        return {"ok": True, "enabled": t.enabled, "pos": t.positions_view(), "locked": sorted(t.locked), "at": now_tw().strftime("%H:%M:%S")}

    def trade_cmd(self, cmd):
        pin = CFG.get("TRADE_CMD_PIN", "")
        # 2026-10-29 12:50 使用者「直接改好 不用密碼」(下單分秒必爭):bridge.env 沒設(或留空)TRADE_CMD_PIN 就不檢查密碼;有設才檢查(通道本身仍要 Worker 的金鑰才進得來)
        if pin and str(cmd.get("pin", "")) != pin:
            log("[交易] 強制指令:交易密碼不對"); return {"ok": False, "msg": "交易密碼不對", "badPin": True}
        t = self.trader
        if not t:
            return {"ok": False, "msg": "電腦的交易程式還沒啟動(盤前 / 收盤後)"}
        code = re.sub(r"\D", "", str(cmd.get("code", "")))[:6]; cid = str(cmd.get("id", ""))[:40] or None
        if not code:
            return {"ok": False, "msg": "沒有股票代碼"}
        act = cmd.get("act")
        log(f"[交易] 手機強制指令 {act} {code} {cmd.get('dir', '')} {cmd.get('lots', '')} 張(編號 {cid})")
        if act == "close":
            ok, msg = t.force_close(code, cid)
        elif act == "enter":
            mkt = self.mkt_of_code(code)
            if code not in [c for c, _ in self.codes]:               # 不在名單上:主迴圈補訂閱逐筆,才盯得到停利停損
                self.codes.append((code, mkt)); self.pending_sub.append((code, mkt))
            ref = cmd.get("ref")
            if ref and not (self.quotes.meta.get(code) or {}).get("y"):
                try: self.quotes.meta.setdefault(code, {"n": cmd.get("name", "") or code, "ex": "tse", "y": None})["y"] = float(ref)   # 手機帶來的昨收
                except Exception: pass
            ok, msg = t.force_enter(code, int(cmd.get("dir", 0)), int(cmd.get("lots", 0)), cmd.get("tpPct"), cmd.get("slPct"), str(mkt), cid, ref)
        else:
            return {"ok": False, "msg": "不認得的指令"}
        return {"ok": ok, "msg": msg, "pos": t.positions_view()}

    def trade_fills(self):
        """2026-10-28 18:20 使用者「看今天 元大帳號裡的輸贏」:向元大查今天全部成交(程式單 + 手動單,斷線那段也查得到),算每檔輸贏。
           30 秒內重複問回上一次的結果(查詢類一秒最多 3 次);電腦正在做別的查詢(接回 / 複查)就請等一下。"""
        c = getattr(self, "_fills_cache", None)
        if c and time.time() - c[0] < 30: return c[1]
        if not self.api or not self.logged_in.is_set():
            # 2026-10-29 13:38 使用者「明細沒了」:13:36 收工登出後再問就只回「電腦還沒登入元大」,手機明細整段空掉
            #   → 收工前最後查一次存起來(記憶體 + fills\日期.json),登出後 / 重開程式後回今天最後那份
            last = self.fills_saved()
            if last: return last
            return {"ok": False, "msg": "電腦還沒登入元大"}
        r = self.query_today(with_oco=False)
        if r is None:
            return {"ok": False, "msg": "查不到元大成交回報"}
        names = {k: (v or {}).get("n", "") for k, v in self.quotes.meta.items()}
        out = fills_pnl(r["trades"], names, disc=float(CFG.get("FEE_DISC", "0.25")), minfee=int(CFG.get("FEE_MIN", "20")))
        try: out["legs"] = fills_legs(r["trades"], names, disc=float(CFG.get("FEE_DISC", "0.25")), minfee=int(CFG.get("FEE_MIN", "20")))   # 2026-10-29 12:45 每一趟一列(使用者「要改」)
        except Exception as e: log(f"成交配對失敗 {e!r}"); out["legs"] = None
        out.update(ok=True, at=now_tw().strftime("%H:%M:%S"), d=now_tw().strftime("%Y-%m-%d"))
        self._fills_cache = (time.time(), out)
        try:
            fd = HERE / "fills"; fd.mkdir(exist_ok=True)
            (fd / f"{out['d']}.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
        except Exception as e: log(f"存成交紀錄失敗 {e!r}")
        return out

    def fills_of_day(self, d):
        """2026-10-31 前幾天的元大成交:每天收盤前(13:36 收工)存的 fills\\日期.json;沒有那天的檔回 ok=False"""
        try:
            f = HERE / "fills" / f"{d}.json"
            if f.exists(): return dict(json.loads(f.read_text(encoding="utf-8")), closed=True, d=d)
        except Exception as e: return {"ok": False, "msg": f"讀不到 {d} 的成交紀錄 {e!r}", "d": d}
        return {"ok": False, "msg": f"電腦沒有 {d} 的成交紀錄", "d": d}

    def fills_saved(self):
        """今天最後一次查到的元大成交(收工登出後用);不是今天的不回"""
        d = now_tw().strftime("%Y-%m-%d")
        c = getattr(self, "_fills_cache", None)
        if c and (c[1] or {}).get("d") == d: return dict(c[1], closed=True)
        try:
            f = HERE / "fills" / f"{d}.json"
            if f.exists(): return dict(json.loads(f.read_text(encoding="utf-8")), closed=True)
        except Exception: pass
        return None

    def mkt_of_code(self, code):
        for c, m in self.codes:
            if c == code: return m
        ex = (self.quotes.meta.get(code) or {}).get("ex")
        if not ex:                                                   # 2026-10-28 平倉檢查 #7:不在今天名單 → 查以前的名單(hist_list.json / pool_*.json)
            ex = self.ex_from_files(code)
        return self.Y.enumMarketType.TWOTC if ex == "otc" else self.Y.enumMarketType.TWSE

    def ex_from_files(self, code):
        try:
            if not hasattr(self, "_ex_cache"):
                self._ex_cache = {}
                f = HERE / "hist_list.json"
                if f.exists():
                    for lst in json.loads(f.read_text(encoding="utf-8")).values():
                        for c, m in lst: self._ex_cache[str(c)] = "otc" if m in ("otc", "tpex") else "tse"
                for f in sorted(HERE.glob("pool_*.json")):
                    j = json.loads(f.read_text(encoding="utf-8")); pool = j.get("pool", j) if isinstance(j, dict) else j
                    for x in pool: self._ex_cache[str(x["code"])] = "otc" if x.get("market") in ("tpex", "otc") else "tse"
            return self._ex_cache.get(code)
        except Exception as e:
            log("查市場別失敗", e); return None

    def want_sub(self, code):
        """2026-10-28 平倉檢查 #7:元三 / 接回的股票不在名單上 → 主迴圈補訂閱逐筆(才盯得到停利停損、±6.5%),回傳市場別"""
        mkt = self.mkt_of_code(code)
        if code not in [c for c, _ in self.codes]:
            self.codes.append((code, mkt)); self.pending_sub.append((code, mkt)); log(f"[交易] {code} 不在名單上 → 補訂閱逐筆")
        return str(mkt)

    def trade_post(self, title, body, kind):
        """2026-10-28 強三進出場、強平 → 寫一則交易訊息到手機的訊息欄(Worker ?log=add,和手機自己發的同一套)"""
        try:
            n = now_tw(); m = re.match(r"^\S+ (\d{4,6})", title); code = m.group(1) if m else ""
            rs = (re.search(r"· (判[一二三四])\s*$", body) or [None, ""])[1]
            it = {"d": n.strftime("%Y-%m-%d"), "t": n.strftime("%H:%M"), "title": title, "body": body, "kind": kind,
                  "key": code + "|" + re.sub(r"[0-9.,+−%↑↓\s⚠✅-]", "", title) + "|" + rs}   # 鍵和手機一樣:代碼|標題去掉數字|判別式
            y = (self.quotes.meta.get(code) or {}).get("y")
            if y: it["prev"] = y
            req = urllib.request.Request(CFG["WORKER_URL"].rstrip("/") + "/?log=add", data=json.dumps(it).encode(), method="POST", headers={"Content-Type": "application/json", "X-Push-Key": CFG.get("PUSH_KEY", "")})   # 2026-10-29 鑰匙:寫通知要中繼金鑰
            urllib.request.urlopen(req, timeout=10)
        except Exception as e:
            log("寫交易訊息到手機失敗", e)

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

    def quota_from_report(self, r):
        """2026-10-29 使用者:「我說的可用資金是訊息右上那個」→「我現在要做的是2」:當沖額度剩多少 =
           總額度(QUOTA_TOTAL,預設 = TRADE_MAX_AMOUNT 490 萬)− 今天成交(買 + 賣,含手動)− 還掛著沒成交的委託金額(含手動;取消會退回)。
           每次查到今天委託成交回報(30 秒複查、接回、手機查輸贏)就算一次,最多每 25 秒推一次 Worker(?quota=push),
           手機訊息右上角「資金」就顯示這個數字。QUOTA_PUSH=0 關掉。"""
        if not CFG.get("PUSH_KEY") or CFG.get("QUOTA_PUSH", "1") != "1": return
        fill = work = 0.0
        for i in range(r.StkTradeList.Count):
            x = r.StkTradeList[i]; fill += float(x.SPrice) * int(x.OkQty)          # 成交:股數 × 成交價
        for i in range(r.StkOrderList.Count):
            o = r.StkOrderList[i]
            try: cq = int(o.CancelQty)
            except Exception: cq = 0
            rest = int(o.AfterQty) - int(o.OkQty) - cq
            if int(o.OrderStatus) in (1, 2, 24, 25, 30) or rest <= 0: continue    # 失敗 / 取消 / 失效 / 已取消 / 沒剩量 = 不佔額度
            work += float(o.Price or 0) * rest                                      # 還掛著:剩餘股數 × 委託價(市價單 Price 0 算不到,先略過)
        now = time.time()
        if now - getattr(self, "_quota_at", 0) < 25: return
        self._quota_at = now
        total = float(CFG.get("QUOTA_TOTAL", CFG.get("TRADE_MAX_AMOUNT", "4900000")))
        body = json.dumps({"d": now_tw().strftime("%Y-%m-%d"), "hm": now_tw().strftime("%H:%M:%S"), "fill": round(fill), "work": round(work), "total": total}).encode()
        def go():
            try:
                req = urllib.request.Request(CFG["WORKER_URL"].rstrip("/") + "/?quota=push", data=body, method="POST",
                                             headers={"Content-Type": "application/json", "X-Push-Key": CFG["PUSH_KEY"]})
                urllib.request.urlopen(req, timeout=15).read()
            except Exception as e:
                log("額度推送失敗", e)
        threading.Thread(target=go, daemon=True).start()

    def broker_stop_on(self):
        """有沒有開「預掛元大條件單」(TRADE_BROKER_STOP=1);沒開 = 程式盯價,不查條件單"""
        t = getattr(self, "trader", None)
        return bool(getattr(t, "broker_stop", CFG.get("TRADE_BROKER_STOP", "0") == "1"))

    def query_today(self, with_oco=True):
        """查元大今天的委託成交回報(+ 有效的二擇一條件單);查不到回 None。
           2026-10-28 平倉檢查 #9:同一時間只做一個查詢(手機查輸贏 / 30 秒複查 / 13:20 平倉共用 resume_buf,重疊會互相清掉)"""
        if not self.qlock.acquire(timeout=40):
            log("!! 查詢排隊超過 40 秒,這次不查"); return None
        with_oco = with_oco and self.broker_stop_on()   # 2026-10-29 16:43 使用者「改」:不掛條件單(TRADE_BROKER_STOP=0)就不查條件單(元大元件的參數和文件對不上,查了只會記一行失敗)
        self.resume_buf = {"trades": [], "tags": {}, "types": {}, "oco": [], "working": [], "got_rpt": False, "got_oco": not with_oco}
        try:
            self.api.GetOrderTradeReport(True, CFG["YT_ACCOUNT"])                       # True = 不列出取消單
            if with_oco:
                time.sleep(1.2)                                                         # 帳務 / 查詢類一秒最多 3 次
                try: self.api.GetConditionStrategy(CFG["YT_ACCOUNT"], self.Y.StrategyType(3), "")   # 3 = 二擇一;空字串 = 全部股票
                except Exception as e:   # 2026-10-29 12:03 正式環境實測:No method matches given arguments (str, StrategyType, str) → 參數型別 / 順序和文件不同,先當作沒有條件單,不讓它把整個查詢弄掛(TRADE_BROKER_STOP=0 本來就不掛條件單)
                    self.resume_buf["got_oco"] = True; log("查二擇一條件單失敗(當作沒有):", e)
            for _ in range(30):
                if self.resume_buf["got_rpt"] and self.resume_buf["got_oco"]: break
                time.sleep(0.5)
            return dict(self.resume_buf) if self.resume_buf["got_rpt"] else None
        finally:
            self.resume_buf = None; self.qlock.release()

    def query_strategies(self, types=(1, 2, 3, 4, 5)):
        """查全部有效條件單(1 停損利、2 移動鎖利、3 二擇一、4 母子單、5 多條件)"""
        if not self.broker_stop_on(): return []   # 2026-10-29 16:43 不掛條件單就不查(同 query_today)
        if not self.qlock.acquire(timeout=40):
            log("!! 查詢排隊超過 40 秒,這次不查條件單"); return []
        self.resume_buf = {"trades": [], "tags": {}, "oco": [], "working": [], "got_rpt": True, "got_oco": False, "want_oco": len(types), "n_oco": 0}
        try:
            for t in types:
                try: self.api.GetConditionStrategy(CFG["YT_ACCOUNT"], self.Y.StrategyType(t), ""); time.sleep(1.2)   # 查詢類一秒最多 3 次
                except Exception as e:   # 2026-10-29 同上:簽章不合就當作沒有條件單
                    self.resume_buf["got_oco"] = True; log("查條件單失敗(當作沒有):", e); break
            for _ in range(20):
                if self.resume_buf["got_oco"]: break
                time.sleep(0.5)
            return list(self.resume_buf["oco"])
        finally:
            self.resume_buf = None; self.qlock.release()

    def close_all(self, at):
        """2026-10-22 使用者:「13:20 就要先取消所有掛單 再進行贖回動作」:
           1. 程式自己的條件單 / 委託先取消 2. 查元大全部還掛著的委託 + 全部有效條件單,通通取消(含手動的) 3. 再查確認(最多 3 次)
           4. 用最新的成交回報算還沒沖銷的部位 → 漲停買回 / 跌停賣出 ROD(trader.recheck)"""
        log(f"[交易] {at} 取消全部掛單 → 再平倉")
        self.trader.prepare_close_all()
        for k in range(3):
            r = self.query_today(with_oco=False) or {"working": [], "trades": []}
            st = self.query_strategies()
            if not r["working"] and not st:
                log(f"[交易] {at} 確認:沒有掛著的委託和條件單"); break
            for w in r["working"]:
                log(f"[交易] {at} 取消委託 {w['code']} {w['bs']} {w['lots']} 張 書號 {w['no']}")
                self.cancel_order(w["no"], w["code"], w["bs"], w["typ"]); time.sleep(0.15)   # 交易類一秒最多 10 次
            for x in st:
                log(f"[交易] {at} 刪除條件單 {x['code']} 類別 {x['type']} 編號 {x['no']}")
                self.delete_strategy(x["type"], x["no"]); time.sleep(0.15)
            time.sleep(2)
        else:
            self.phone_alert(f"⚠ {at} 還有取消不掉的掛單", "請在投資先生確認委託 / 條件單")
        r = self.query_today(with_oco=False)
        if not r:
            log(f"!! {at} 查不到成交回報,改用程式記的部位平倉")
            with self.trader.lock:
                r = {"trades": [{"code": p["code"], "bs": "B" if p["dir"] > 0 else "S", "lots": p["qty"]} for p in list(self.trader.positions.values()) if p["qty"]], "working": []}
        self.trader.recheck(r["trades"], r["working"], at)

    def resume_positions(self, day):
        """2026-10-22 使用者:「加 啟動時接回部位」:查元大今天的委託成交回報 + 有效的二擇一條件單,交給 trader 接回程式開的未平部位"""
        if not self.trader or not self.trader.enabled:
            return
        try:
            r = self.query_today()
            if not r:
                log("!! 接回:查不到今天的成交回報,不接回(有部位請用投資先生手動處理)"); return
            self.trader.resume(r["trades"], r["tags"], r["oco"], fetch_signals(CFG["WORKER_URL"], day, CFG.get("PUSH_KEY", "")), r.get("working", []), r.get("types"))
        except Exception as e:
            log("接回失敗", e)

    def recheck_positions(self, at):
        """2026-10-22 使用者:「13:23 分要再確認一次 還有沒有未沖銷的股票」→「13:22 / 13:23 / 13:24 確認三次 以保安全」"""
        if not self.trader or not self.trader.enabled:
            return
        try:
            r = self.query_today(with_oco=False)
            if not r:
                log(f"!! {at} 複查:查不到成交回報"); self.trader.alert(f"⚠ {at} 複查失敗", "查不到元大成交回報,請在投資先生確認有沒有未沖銷的股票"); return
            self.trader.recheck(r["trades"], r["working"], at)
        except Exception as e:
            log("複查失敗", e)

    def phone_alert(self, title, body=""):
        """發一則提醒到手機的「今日監控紀錄」(Worker ?log=add)"""
        try:
            n = now_tw(); it = {"d": n.strftime("%Y-%m-%d"), "t": n.strftime("%H:%M"), "title": title, "body": body + " · 元大中繼", "kind": "warn", "key": "yt|" + title}
            req = urllib.request.Request(CFG["WORKER_URL"].rstrip("/") + "/?log=add", data=json.dumps(it).encode(), method="POST", headers={"Content-Type": "application/json", "X-Push-Key": CFG.get("PUSH_KEY", "")})   # 2026-10-29 鑰匙
            urllib.request.urlopen(req, timeout=10)
        except Exception as e:
            log("發手機提醒失敗", e)

    def elig_run(self, why):
        """開盤前資格複檢(elig.py)另開執行緒跑,不卡住行情;13:30 後啟動不用做"""
        if now_tw().strftime("%H:%M") >= "13:30" or now_tw().weekday() >= 5: return
        def _go():
            try:
                import elig; elig.refresh(CFG, log=log)
            except Exception as e: log(f"[複檢] 失敗({why})", e)
        threading.Thread(target=_go, daemon=True, name="elig").start()

    def make_trader(self):
        cfg = dict(CFG)
        if CFG.get("YT_ENV", "UAT").upper() == "PROD" and CFG.get("TRADE_ALLOW_PROD", "0") != "1":
            if cfg.get("TRADE_ENABLE") == "1": log("[交易] 正式環境沒有 TRADE_ALLOW_PROD=1,自動交易關閉(只接行情)")
            cfg["TRADE_ENABLE"] = "0"
        cfg.setdefault("TRADE_STATE_FILE", str(HERE / "trade_state.json"))
        gw = type("GW", (), {"send": lambda _s, *a: self.send_order(*a), "cancel": lambda _s, no, code="", bs=None, typ=None: self.cancel_order(no, code, bs, typ),
                             "place_oco": lambda _s, *a: self.place_oco(*a), "delete_oco": lambda _s, no: self.delete_oco(no)})()
        t = Trader(cfg, gw, log, now_tw); t.alert = self.phone_alert; t.post = self.trade_post
        t.ref_of = lambda c: (self.quotes.meta.get(c) or {}).get("y")
        t.name_of = lambda c: (self.quotes.meta.get(c) or {}).get("n") or c
        t.want_sub = self.want_sub
        for c, m in self.codes:
            t.mkt_of[c] = str(m)
        return t

    # ── 推到 Worker ──
    def push(self, day, minute, bars):
        if not bars:
            return True
        self.save_local(day, minute, bars)          # 2026-10-25 有 PUSH_KEY 也照樣存本機一份(比對 / 備份用)
        if not CFG.get("PUSH_KEY"):
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
            except urllib.error.HTTPError as e:
                # 2026-10-27 12:40 被拒時把 Worker 回的原因寫進紀錄:{"error":"金鑰不對"} = bridge.env 的 PUSH_KEY 和 Worker 的 YT_PUSH_KEY 不一樣;
                #   HTML / error code 1010 = Cloudflare 擋連線身分。403 / 401 重試也沒用,直接停
                try: why = e.read().decode("utf-8", "replace")[:200].replace("\n", " ")
                except Exception: why = ""
                log(f"推送失敗 {minute} 第 {k + 1} 次 HTTP {e.code}:{why}")
                if e.code in (401, 403):
                    return False
                time.sleep(2)
            except Exception as e:
                log(f"推送失敗 {minute} 第 {k + 1} 次", e); time.sleep(2)
        return False

    def save_local(self, day, minute, bars):
        """2026-10-24 第一階段:Cloudflare KV 一天只能寫 1,000 次(雲端 K 已用約 880),元大 1 分 K 先存在本機 CSV 比對,不推 Worker。
        BARS_DIR/日期.csv:code,date,time,open,high,low,close,volume(量 = 股,和 kline/ 一樣);每次整檔重寫(晚到的成交會改到舊分鐘)"""
        loc = getattr(self, "local", None)
        if loc is None or loc.get("_d") != day:
            loc = self.local = {"_d": day}
        loc[minute] = bars
        d = Path(CFG.get("BARS_DIR", HERE / "bars_yuanta")); d.mkdir(parents=True, exist_ok=True)
        tmp = d / f"{day}.csv.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("code,date,time,open,high,low,close,volume\n")
            for m in sorted(k for k in loc if k != "_d"):
                for c, (o, h, l, cl, v) in sorted(loc[m].items()):
                    f.write(f"{c},{day},{m},{o},{h},{l},{cl},{int(v)}\n")
        os.replace(tmp, d / f"{day}.csv")
        return True

    def flush(self, day, final=False):
        """推送已經走完的分鐘(現在分鐘之前);收盤後 final=True 全推"""
        cur = now_tw().strftime("%H:%M")
        for m in self.book.minutes():
            if m in self.sent or (not final and m >= cur):
                continue
            if self.push(day, m, self.book.take(m)):
                self.sent.add(m)
            else:
                break        # 2026-10-27 12:40 一分鐘推不上去,這一輪後面的分鐘先不推(原本每一輪把所有沒送成功的分鐘各試 3 次,今天落後 2.5 小時、白打 Worker 5 千多次);下一輪從最舊的再試
        for m in self.book.take_dirty():          # 晚到的成交 → 整分鐘重送
            if m in self.sent:
                self.push(day, m, self.book.take(m))

    def local_signals(self, day):
        """2026-10-30 讀電腦引擎寫的本機通知檔 engine_data/live_<日期>.jsonl 新增的行(開工時從檔尾開始,之前的當舊的;換日換檔)"""
        f = Path(os.environ.get("ENGINE_DIR", HERE / "engine_data")) / f"live_{day}.jsonl"
        st = getattr(self, "_loc", None)
        if not st or st["day"] != day:
            st = self._loc = {"day": day, "off": f.stat().st_size if f.exists() else 0, "buf": ""}
        try:
            if not f.exists(): return []
            sz = f.stat().st_size
            if sz < st["off"]: st["off"] = 0
            if sz == st["off"]: return []
            with open(f, "rb") as fh: fh.seek(st["off"]); raw = fh.read()
            st["off"] += len(raw); txt = st["buf"] + raw.decode("utf-8", "ignore"); lines = txt.split("\n"); st["buf"] = lines.pop()
            out = []
            for ln in lines:
                try: out.append(json.loads(ln))
                except Exception: pass
            if out: log(f"[公式] 本機通知 {len(out)} 則:" + " / ".join(x.get("title", "") for x in out))
            return out
        except Exception as e:
            log("本機通知檔讀取失敗", e); return []

    def run_day(self):
        day = now_tw().strftime("%Y-%m-%d")
        self.book = BarBook(vol_mult=int(CFG.get("VOL_MULT", "1000"))); self.sent = set(); self.n_ticks = 0
        self.quotes.q.clear()            # 新的一天:即時價從頭累計
        for m in self.quotes.meta.values(): m["y"] = None   # 2026-10-28 平倉檢查 #10:昨收每天重抓(不在今天名單的股票不會留著前幾天的)
        self.codes = self.load_codes_safe()
        self.trader = self.make_trader()
        self.trader.prime(fetch_signals(CFG["WORKER_URL"], day, CFG.get("PUSH_KEY", "")))
        self._loc = None; self.local_signals(day)   # 2026-10-30 本機通知檔從現在的檔尾開始讀(之前的是舊的)
        self.engine = None   # 2026-10-29 公式在電腦(使用者「先做1.2.3」第 2 項):ENGINE=1 → 另開一個 engine.py 行程,每分鐘用模擬器算判一二三推雲端;收工時關掉
        if CFG.get("ENGINE", "0") == "1":
            try:
                import subprocess
                self.engine = subprocess.Popen([sys.executable, str(HERE / "engine.py")], cwd=str(HERE)); log(f"[公式] engine.py 已啟動(pid {self.engine.pid}),判一二三由電腦算、手機只收雲端通知")
            except Exception as e: log("[公式] engine.py 啟動失敗", e)
        fails = 0; k = 0; resumed = False; rechecked = set(); closed_all = False
        self.last_tick = time.time(); self.last_kick = 0.0; self.n_kick = 0; self.last_recon = time.time()
        # 2026-10-30 11:08(實際時間)使用者「該如何避免再次出現這問題」→「2道都做」第 1 道:電腦自己做開盤前資格複檢(elig.py),
        #   啟動時先做一次(08:45 前),08:30、08:44 再各做一次(證交所 / 櫃買早上才換今天的名單);結果存本機 + 推 Worker,手機、引擎、trader 都照它排除
        self.elig_done = set(); self.elig_run("啟動")
        while now_tw().strftime("%H:%M") < "13:36":
            try:
                if self.login_fail:
                    raise LoginRejected(self.login_fail)
                hm_e = now_tw().strftime("%H:%M")
                if hm_e in ("08:30", "08:44") and hm_e not in self.elig_done: self.elig_done.add(hm_e); self.elig_run(hm_e)
                # 2026-10-28 14:29 使用者「當沒有k 資料進來的時候就要馬上檢查連線 不是嗎」(10/28 12:07 起逐筆整個停掉、程式沒發現):
                #   盤中(09:01~13:24,開盤前試撮和 13:25 收盤集合競價本來就沒有逐筆)連續 TICK_WATCH 秒(預設 60)一筆逐筆都沒有 → 當作斷線,重新登入、重新訂閱;
                #   重連後還是沒有,每次間隔拉長(60 → 120 → 240 秒,最多 5 分鐘),避免一直登入被元大鎖;第一次和每第 3 次發手機提醒
                hm_w = now_tw().strftime("%H:%M")
                if self.api is not None and not self.disconnected.is_set() and "09:01" <= hm_w < "13:25":
                    quiet = time.time() - self.last_tick; gap = min(300, int(CFG.get("TICK_WATCH", "60")) * (2 ** min(self.n_kick, 3)))
                    if quiet > int(CFG.get("TICK_WATCH", "60")) and time.time() - self.last_kick > gap:
                        self.last_kick = time.time(); self.n_kick += 1
                        log(f"!! 已經 {quiet:.0f} 秒沒有收到任何逐筆成交(盤中不可能)→ 當作斷線,重新登入、重新訂閱(第 {self.n_kick} 次)")
                        if self.n_kick == 1 or self.n_kick % 3 == 0:
                            self.phone_alert(f"⚠ 元大資料中斷 {quiet:.0f} 秒", f"中繼程式重新連線中(第 {self.n_kick} 次)")
                        self.disconnected.set()
                    elif quiet <= 5 and self.n_kick:
                        log(f"逐筆恢復(重連 {self.n_kick} 次)"); self.n_kick = 0
                if self.api is None or self.disconnected.is_set():
                    self.close(); self.connect(); self.subscribe(self.codes); fails = 0; self.last_tick = time.time()   # 重連後重新計時
                    self.last_recon = time.time() - 50                       # 重連後 10 秒就對帳一次
                    if not resumed:
                        self.resume_positions(day); resumed = True          # 今天第一次連上:接回程式開的未平部位(斷線重連不用,部位一直在記憶體裡)
                time.sleep(1); k += 1
                if self.pending_sub:                                     # 手機強制進場的股票補訂閱(在主執行緒呼叫元大元件)
                    subs, self.pending_sub = self.pending_sub, []
                    try: self.subscribe(subs)
                    except Exception as e: log("補訂閱失敗", e)
                sig = fetch_signals(CFG["WORKER_URL"], day, CFG.get("PUSH_KEY", "")) if (self.trader.enabled and k % 3 == 0) else None   # 手機通知每 3 秒看一次
                # 2026-10-30 01:32 使用者「先做A」:電腦引擎寫在本機的通知每秒讀一次,不用等 Worker / 3 秒輪詢(訊號算出來 1 秒內送單);還沒 prime 好不讀(避免舊訊號)
                if self.trader.enabled and getattr(self.trader, "primed", False):
                    loc = self.local_signals(day)
                    if loc: sig = loc + (sig or []) if sig is not None else loc
                self.trader.step(sig)
                # 2026-10-29 額度(使用者「我現在要做的是2」):沒開自動交易時沒有人查今天的委託成交 → 每 QUOTA_SEC(60)秒查一次,查到就由 quota_from_report 推上雲端
                if CFG.get("QUOTA_PUSH", "1") == "1" and CFG.get("PUSH_KEY") and k % int(CFG.get("QUOTA_SEC", "60")) == 0 and self.logged_in.is_set():
                    try: self.query_today(with_oco=False)
                    except Exception as e: log("額度查詢失敗", e)
                if self.trader.enabled and self.trader.need_verify():   # 2026-10-28 漲停 / 跌停單 30 秒沒成交完 → 查元大:還掛著就等、沒有就重送
                    r = self.query_today(with_oco=False)
                    if r: self.trader.verify(r["working"], r["trades"], r.get("tags"))   # 平倉檢查 #2:委託書號沒回來也能用自訂編號對
                    else: log("[交易] 30 秒檢查:查不到元大委託回報,下一輪再查")
                hm_now = now_tw().strftime("%H:%M")
                # 2026-10-28 模擬測試:斷線 / 重開漏掉回報 → 每分鐘(重連後馬上)跟元大對一次帳,程式不知道的部位接回來 / 平掉
                if self.trader.enabled and "09:01" <= hm_now < self.trader.force_exit and time.time() - self.last_recon > 60:
                    self.last_recon = time.time()
                    r = self.query_today(with_oco=False)
                    if r: self.trader.reconcile(r["trades"], r["working"], r.get("tags"), r.get("types"))
                if self.trader.enabled and self.trader.cancel_all and not closed_all and hm_now >= self.trader.force_exit:
                    closed_all = True
                    try: self.close_all(self.trader.force_exit)
                    except Exception as e: log("13:20 取消 / 平倉失敗(13:21 trader 會自己強制平倉)", e)
                for at in self.trader.recheck_times:                     # 13:22 / 13:23 / 13:24 各一次
                    if at not in rechecked and hm_now >= at:
                        rechecked.add(at); self.recheck_positions(at); break
                if k % 10 == 0:
                    self.flush(day); self.flush_ticks(day)
                # 2026-10-30 引擎看門狗:盤中引擎行程結束了(當掉 / 被關)→ 重新開(一天最多 5 次);13:30 後不管
                if CFG.get("ENGINE", "0") == "1" and k % 30 == 0 and "08:45" <= hm_now < "13:30" and getattr(self, "engine", None) is not None and self.engine.poll() is not None:
                    self.eng_restarts = getattr(self, "eng_restarts", 0) + 1
                    if self.eng_restarts <= 5:
                        try:
                            import subprocess
                            log(f"!! [公式] engine.py 不見了(結束碼 {self.engine.returncode}),第 {self.eng_restarts} 次重新啟動")
                            self.engine = subprocess.Popen([sys.executable, str(HERE / "engine.py")], cwd=str(HERE))
                        except Exception as e: log("[公式] engine.py 重新啟動失敗", e)
            except LoginRejected as e:
                log(f"!! 元大拒絕登入:{e}。今天不再重試,請檢查 bridge.env 的帳號 / 密碼 / 權限後重啟服務")
                self.phone_alert("⚠⚠ 元大拒絕登入", f"{e} · 今天程式不會管部位,有部位請用元大 App 手動平倉")
                self.close(); break
            except Exception as e:
                fails += 1; log("錯誤", e, f"(第 {fails} 次)")
                self.close(); time.sleep(min(60, 10 * fails))   # 登入失敗不能太頻繁(每 4 秒最多 1 次,一天 1000 次)
        self.flush(day, final=True); self.flush_ticks(day)   # 2026-10-30 收工前把剩下的逐筆寫完
        try:   # 2026-10-29 13:38 收工登出前最後查一次今天成交存起來(手機明細登出後照樣看得到)
            if self.api and self.logged_in.is_set():
                self._fills_cache = None; fj = self.trade_fills(); log(f"收工前存今天成交:{'好' if fj.get('ok') else fj.get('msg')}")
        except Exception as e: log("收工前存成交失敗", e)
        log(f"收工:逐筆 {self.n_ticks} 筆,推送 {len(self.sent)} 分鐘;交易 {self.trader.n_trades} 筆 淨 {self.trader.pnl:+,.0f} {self.trader.journal}")
        if getattr(self, "engine", None):
            try:
                self.engine.wait(timeout=120)   # engine.py 13:32 自己收工;沒結束就關掉
            except Exception:
                try: self.engine.terminate()
                except Exception: pass
            log("[公式] engine.py 已結束")
        self.close()


_BRIDGE_LOCK = None
def bridge_lock():
    """2026-10-30 18:40 使用者查「昨天做預測 有起到實效嗎」→ 查出 10/30 08:45 兩個中繼同時啟動(自動更新的重新啟動在 Windows 是開新行程、
       排程以為結束了,第二次 schtasks /Run 又開一個):一個被元大拒絕登入(0016 請勿連續登入)收工、120 秒後把它開的引擎關掉,
       另一個開的引擎因為鎖被佔走沒跑 → 08:47~10:28 沒有引擎。改成中繼也只准一個:同一資料夾的 bridge.lock 被鎖就不跑。
       自動更新重新啟動時舊行程可能還沒放開 → 最多等 30 秒"""
    global _BRIDGE_LOCK
    f = open(HERE / "bridge.lock", "a+")
    for k in range(30):
        try:
            if os.name == "nt":
                import msvcrt; f.seek(0); msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl; fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            _BRIDGE_LOCK = f; return
        except OSError:
            time.sleep(1)
    log("!! 已經有另一個中繼在跑(bridge.lock 被鎖)→ 這個不跑"); sys.exit(0)


def self_update():
    """2026-10-30 02:58 使用者「你有辦法推上電腦嗎」:Claude 不能直接碰 Windows 桌機,改成中繼自己更新 ——
       啟動時(排程 08:40 / 開機 / 崩潰重拉,不在盤中)讀 GitHub 的 tools/yuanta_bridge/VERSION(一個版本號,改了就代表要更新),
       和本機 VERSION 不同就經 Worker 把 main 最新的程式檔抓下來(每個檔先編譯檢查,壞的不換,舊檔留 .bak),寫好 VERSION 後重新啟動自己。
       改中繼程式的人:推完 main 後把 VERSION 改成新的版本號(例如 日期+序號 2026103001)一起推。
       bridge.env AUTO_UPDATE=0 可關。"""
    if CFG.get("AUTO_UPDATE", "1") != "1" or os.environ.get("MYSH_UPDATED"): return
    hm = now_tw().strftime("%H:%M")
    if "08:45" <= hm < "13:40" and now_tw().weekday() < 5: return          # 盤中不換程式
    try:
        want = gh_get("tools/yuanta_bridge/VERSION", timeout=20).decode("utf-8").strip().split()[0]
    except Exception as e:
        log("自動更新:讀不到 VERSION", e); return
    cur = ""
    try: cur = (HERE / "VERSION").read_text(encoding="utf-8").strip().split()[0]
    except Exception: pass
    if not re.fullmatch(r"[\w.\-]{1,40}", want) or want == cur: return
    log(f"自動更新:VERSION {cur or '無'} → {want},下載中")
    files = ["yuanta_bridge.py", "bars.py", "trader.py", "quotes.py", "win_pnl.py", "engine.py", "win_keys.py", "win_update.py", "hist_kline.py", "win_install.py", "elig.py"]
    try:   # 2026-10-30 11:10(實際時間)檔案清單改讀 repo 的 tools/yuanta_bridge/FILES(新增檔案時舊版程式也抓得到;抓不到就用上面寫死的)
        lst = [x.strip() for x in gh_get("tools/yuanta_bridge/FILES", timeout=30).decode("utf-8").splitlines() if x.strip().endswith(".py")]
        if lst: files = lst
    except Exception: pass
    import py_compile, shutil
    ok = 0
    for f in files:
        try:
            b = gh_get(f"tools/yuanta_bridge/{f}", timeout=60)   # main 最新
            tmp = HERE / (f + ".new"); tmp.write_bytes(b); py_compile.compile(str(tmp), doraise=True)
            if (HERE / f).exists(): shutil.copyfile(HERE / f, HERE / (f + ".bak"))
            os.replace(tmp, HERE / f); ok += 1
        except Exception as e:
            log(f"自動更新:{f} 失敗(保留舊檔)", e)
            try: (HERE / (f + ".new")).unlink()
            except Exception: pass
    if ok == 0: return
    (HERE / "VERSION").write_text(want + "\n", encoding="utf-8")
    log(f"自動更新:{ok}/{len(files)} 個檔換成 {want},重新啟動")
    os.environ["MYSH_UPDATED"] = "1"
    try:
        if _BRIDGE_LOCK: _BRIDGE_LOCK.close()   # 2026-10-30 放開單一執行個體鎖,新行程才拿得到
    except Exception: pass
    os.execv(sys.executable, [sys.executable] + sys.argv)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        bridge_lock()   # 2026-10-30 18:40 單一執行個體(10/30 兩個中繼同時跑:一個被元大拒絕登入收工、連帶把引擎關掉)
        try: self_update()
        except Exception as e: log("自動更新失敗(照舊版繼續)", e)
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
    if not CFG.get("WORKER_URL"):
        sys.exit("缺少設定 WORKER_URL")
    if not CFG.get("PUSH_KEY"):
        log("沒有 PUSH_KEY:元大 1 分 K 只存本機", CFG.get("BARS_DIR", str(HERE / "bars_yuanta")), "(不推 Worker,不用 KV 額度)")
    if CFG.get("QUOTE_ENABLE", "0") == "1":        # 預設關閉:要使用者自己決定要不要讓手機即時價走元大(會透過 Cloudflare Tunnel 對外開一個要金鑰的服務;2026-10-28 起也接手機的強制交易指令,要 TRADE_CMD_PIN)
        b.serve_quotes()
    hist_done = set()
    while True:
        n = now_tw(); hm = n.strftime("%H:%M")
        # 2026-10-29 13:38 使用者「明細沒了」:收盤後(或重開程式時)今天還沒存到元大成交 → 登入一次查完存起來就登出(一天只試一次,手機明細才有今天的真實成交)
        fk = n.strftime("%Y-%m-%d")
        if n.weekday() < 5 and "13:36" <= hm < "23:00" and fk not in hist_done and not (HERE / "fills" / f"{fk}.json").exists():
            hist_done.add(fk)
            try:
                b.connect(); fj = b.trade_fills(); log(f"收盤後補查今天成交:{'好,' + str(fj.get('n', '')) + ' 筆' if fj.get('ok') else fj.get('msg')}")
            except Exception as e: log("收盤後補查今天成交失敗", e)
            finally:
                try: b.close()
                except Exception: pass
        # 2026-10-25 使用者「符合條件一律收進來…預先抓好抓滿」:收工後把今天「前一天收盤符合選股條件的全部股票」的元大 1 分 K 抓回本機 kline_yuanta\
        #   14:05 一次、17:05 再補一次(已抓過的跳過);同一支程式依序登入,不會兩支同時登入。bridge.env 設 HIST_AFTER=0 可關掉
        if n.weekday() < 5 and CFG.get("HIST_AFTER", "1") == "1" and "14:05" <= hm < "20:00":
            for at, until in (("14:05", "17:05"), ("17:05", "20:00")):
                key = n.strftime("%Y-%m-%d") + at
                if at <= hm < until and key not in hist_done:
                    hist_done.add(key); log(f"收工後抓今天全部符合條件股票的 1 分 K({at})")
                    try:
                        import subprocess
                        subprocess.run([sys.executable, str(HERE / "hist_kline.py"), "today"], cwd=str(HERE), timeout=3 * 3600)
                    except Exception as e:
                        log("抓今天 1 分 K 失敗", e)
        if n.weekday() < 5 and "08:45" <= hm < "13:36":
            try:
                b.run_day()
            except Exception as e:
                # 2026-10-28 平倉檢查 #8:啟動那段出錯(網路 / 名單 / 元件)→ 30 秒後重來,不再整天停擺
                log("當天執行失敗,30 秒後重來", e)
                try: b.close()
                except Exception: pass
                b._fail_n = getattr(b, "_fail_n", 0) + 1
                if b._fail_n == 1 or b._fail_n % 10 == 0:
                    try: b.phone_alert("⚠ 中繼程式出錯,30 秒後重來", f"第 {b._fail_n} 次 · {str(e)[:120]}")
                    except Exception: pass
                time.sleep(30); continue
            while now_tw().strftime("%H:%M") < "13:36":   # 今天收工(或登入被拒)後等到收盤時間過了,不重跑
                time.sleep(60)
        else:
            time.sleep(30)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException:
        import traceback; log("!! 程式中止", traceback.format_exc()); raise
