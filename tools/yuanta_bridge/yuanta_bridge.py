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
import contextlib, math
import hmac, json, os, re, sys, time, threading, urllib.request, urllib.error, datetime as dt
from pathlib import Path
from bars import BarBook
from trader import Trader, fetch_signals, fetch_cap
from trader import JUDGES_ALL, IRON_AMOUNT, IRON_CAP_MAX   # 自動當沖夥伴版 §6.1 acfg_sync 夾鐵律上限用(只讀常數,不改 trader)
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
    raw = urllib.request.Request(f"https://raw.githubusercontent.com/redwinds542688-gif/Stock-database/{ref}/{path}?t={int(time.time())}", headers={"User-Agent": "yuanta-bridge/1"})
    if CFG.get("PUSH_KEY") and CFG.get("WORKER_URL"):
        url = CFG["WORKER_URL"].rstrip("/") + "/?gh=raw&path=" + path + ("&ref=" + ref if ref != "main" else "") + ("&repo=" + repo if repo else "") + f"&_={int(time.time())}"
        req = urllib.request.Request(url, headers={"X-Push-Key": CFG["PUSH_KEY"], "User-Agent": "yuanta-bridge/1"})
        try: return urllib.request.urlopen(req, timeout=timeout).read()
        except urllib.error.HTTPError as e:
            if e.code == 404 or repo: raise
            log(f"雲端讀不到 {path}(HTTP {e.code})→ 改直接從 GitHub 讀")
        except Exception as e:
            if repo: raise   # 私人庫只能經雲端
            log(f"雲端讀不到 {path}({str(e)[:60]})→ 改直接從 GitHub 讀")   # 2026-11-20 使用者「改1234」 1:公開庫的檔(名單、版本、程式更新)
        return urllib.request.urlopen(raw, timeout=timeout).read()
    return urllib.request.urlopen(raw, timeout=timeout).read()


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


# 2026-11-17 元大委託失敗錯誤碼 → 中文原因(看過的才列;沒列的照元大給的文字 / 開盤前複檢原因)
REJECT_WHY = {"21352": "無券可借,不能放空", "21353": "今天暫停先賣後買(不能當沖放空)"}   # 21353:11/17 華邦電(複檢 noshort)手動放空被退時看到的碼
CFG = load_env()
LOG_LOCK = threading.Lock()
# 2026-11-15 12:10 使用者「大概10個人 每個人的額度不一樣 … 九個判別式都一起啟動 … 我可以看到所有人的輸贏 但他們只能看到自己的輸贏」:
#   家人帳號 = 同一台主機另一個資料夾(C:\Yuanta\fam\<名字>)跑一份中繼,bridge.env 寫 ACCT_NAME=<名字> → 「只下單」模式:
#   讀同一份雲端 / 本機訊號照自己的設定下單;不跑公式引擎、不推 1 分 K / 台指期 / 參考價 / 雲端備份 / 額度、不寫進場結果檔、不跟 5861 主導機的多空上限;
#   推到手機的提醒標題前面加【名字】、交易卡改成提醒(不算進主帳號的元大明細)。ACCT_NAME 空白 = 主帳號(和原本完全一樣)
ACCT = re.sub(r"[\s|]", "", CFG.get("ACCT_NAME", ""))[:12]
FAMILY = bool(ACCT)
if FAMILY and CFG.get("ENGINE_DIR") and not os.environ.get("ENGINE_DIR"): os.environ["ENGINE_DIR"] = CFG["ENGINE_DIR"]   # 家人帳號:讀主帳號資料夾的本機通知(訊號 1 秒內到)


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


class LoginRetry(Exception):
    """2026-11-21 使用者「全改」(檢查 R1):元大暫時不給登入(0016 請勿連續登入、沒看過的碼)→ 退避後再試,不停一整天"""


# 2026-11-21 使用者「全改」(檢查 R1):原本登入回應只要不是成功就整天停(LoginRejected),連中繼自己重連造成的「0016 請勿連續進行系統登入作業」也停 →
#   13:20 全部平倉都不做。改成分兩種:
#   永久(帳密錯、0102 密碼凍結 / 未啟用、0112 無此權限、帳號 / 憑證有問題)→ 當天停(錯的密碼連續試會被鎖);
#   其他(0016、0000 執行失敗、沒看過的碼)→ 暫時:30 / 60 / 120 / 240 / 300 秒退避再登入,當天最多 LOGIN_RETRY_MAX(預設 10)次,手機只提醒一次
LOGIN_OK = ("0001", "00001")
LOGIN_FATAL = {"0102", "0112"}   # 元大文件「登入」:0102 密碼凍結或未啟用、0112 無此權限使用功能(0000 執行失敗 = 原因不明,當暫時)
LOGIN_FATAL_RE = re.compile(r"密碼.{0,6}(錯|誤|不符|凍結|鎖)|帳號.{0,6}(錯|誤|不存在|不符|凍結|停用|鎖)|凍結|未啟用|無此權限|權限不足|憑證.{0,6}(錯|誤|失效|過期|異常)")


# 2026-11-21 使用者「改 A」(A5):原本 0000 執行失敗 / 沒看過的碼都當暫時、最多重試 10 次;元大如果對錯的帳密回 0000 或看不懂的文字,
#   連試 10 次可能把帳號鎖住。改成三種:
#   · fatal(當天停):0000、0102、0112,或訊息提到 密碼 / 帳號 / 身分證 / 憑證 / 錯誤 / 凍結 / 未啟用 / 權限
#   · retry(明確是暫時的,照舊退避重試):0016 請勿連續登入,或訊息提到 連線 / 逾時 / 忙碌 / 稍後 / 連續
#   · unknown(沒看過的碼):最多再試 LOGIN_UNKNOWN_MAX(預設 2)次、每次等 LOGIN_UNKNOWN_WAIT(預設 300)秒,還是不行就當天停 + 推手機
LOGIN_FATAL_CODES = LOGIN_FATAL | {"0000"}
LOGIN_FATAL_WORDS = re.compile(r"密碼|帳號|身分證|身份證|憑證|錯誤|凍結|未啟用|權限")
LOGIN_RETRY_CODES = {"0016"}
LOGIN_RETRY_WORDS = re.compile(r"請勿連續|連續登入|連線|逾時|忙碌|稍後|timeout|busy", re.I)


def login_kind(code, msg="", count=0):
    """登入回應 → "ok" / "fatal"(當天不再試)/ "retry"(明確暫時,退避後再試)/ "unknown"(沒看過,最多再試 2 次)"""
    code = str(code or "").strip(); msg = str(msg or "")
    try: cnt = int(count or 0)
    except Exception: cnt = 0
    if code in LOGIN_OK or cnt > 0: return "ok"
    # 2026-11-21 使用者「改 A」(A5):先看是不是帳密 / 憑證類(寧可停一天也不要把帳號鎖住),再看是不是明確暫時的
    if code in LOGIN_FATAL_CODES or LOGIN_FATAL_RE.search(msg) or LOGIN_FATAL_WORDS.search(msg): return "fatal"
    if code in LOGIN_RETRY_CODES or LOGIN_RETRY_WORDS.search(msg): return "retry"
    return "unknown"


def login_backoff(n):
    """第 n 次暫時被拒後要等幾秒:30、60、120、240、300…(元大規定登入每 4 秒最多 1 次,這裡遠大於 4 秒)"""
    return max(30, min(300, 30 * 2 ** max(0, int(n) - 1)))


# 2026-11-21 使用者「全改」(輕微項):台灣股市休市日(和 app/app.html 的 TW_HOLIDAYS 同一份;週六日另外判斷)。休市日不登入元大
#   (原本整天每分鐘「沒有逐筆 → 斷線重登」);bridge.env 可用 MARKET_HOLIDAYS=YYYY-MM-DD,… 追加(例如颱風停止交易),HOLIDAY_SKIP=0 關掉
TW_HOLIDAYS = {"2026-01-01", "2026-02-12", "2026-02-13", "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20", "2026-02-27",
               "2026-04-03", "2026-04-06", "2026-05-01", "2026-06-19", "2026-09-25", "2026-09-28", "2026-10-09", "2026-10-23", "2026-10-26", "2026-12-25"}


def is_holiday(day):
    if CFG.get("HOLIDAY_SKIP", "1") != "1": return False
    extra = {x.strip() for x in CFG.get("MARKET_HOLIDAYS", "").split(",") if x.strip()}
    return str(day) in TW_HOLIDAYS or str(day) in extra


def fill_key(t):
    """成交回報的「完全相同」鍵(和 trader.resume、quota_from_report 去重複用的同一組:書號 + 時間 + 價 + 量 + 買賣 + 代碼;有成交序號時加上序號)"""
    return (str(t.get("order_no", "")), str(t.get("time", "")), float(t.get("price") or 0), int(t.get("lots") or 0), str(t.get("bs", "")), str(t.get("code", "")), str(t.get("fill_id", "")))


def fills_guard(trades, mine=None):
    """2026-11-21 使用者「全改」(檢查 R4):元大成交回報可能同一筆回兩次,但回報裡沒有確定的「每筆成交序號」(只有書號、DateTime、價、量),
       同一張委託同一時間、同價、同量成交兩筆也是真的有可能 → 不能直接刪。這裡只「看出可疑」:
         每檔算兩個淨部位:原樣(raw)和完全相同的只算一次(dd)。
         · 一樣 → 照原樣;兩個同方向、張數不同 → 用張數少的那份(平少的那份一定不會反向開新倉);
         · 方向不同 / 一個是 0 → 有 mine(程式自己記的部位 {code: 有號張數})且等於其中一個 → 用那一個(不是只靠可疑的差額);
           否則 = 判斷不了 → 這檔列進 amb(呼叫的人跳過這檔、不送單,推手機請人工確認)。
       回傳 (safe_trades, suspects{code: [重複的成交]}, amb{code: (raw, dd)});沒有可疑的檔,safe_trades 就是原樣"""
    mine = mine or {}
    by = {}
    for t in trades or []: by.setdefault(t.get("code"), []).append(t)
    out, sus, amb = [], {}, {}
    sgn = lambda x: (x > 0) - (x < 0)
    for c, ts in by.items():
        seen = set(); dd = []; dup = []
        for t in ts:
            k = fill_key(t)
            if k in seen: dup.append(t); continue
            seen.add(k); dd.append(t)
        if not dup:
            out += ts; continue
        sus[c] = dup
        net = lambda xs: sum(int(x.get("lots") or 0) * (1 if str(x.get("bs", "")).upper().startswith("B") else -1) for x in xs)
        nr, nd = net(ts), net(dd)
        if nr == nd: out += ts
        # 2026-11-21 使用者「改 A」(A2):同一張委託同一秒、同價、同量真的成交兩筆(例如空 2 張分 1+1 成交)也會被當成重複 →
        #   原本同方向一律取張數少的(13:20 只平 1 張)。改成先看程式自己記的部位:等於其中一份就用那一份;兩份都不是才照舊取張數少的
        elif c in mine and mine[c] in (nr, nd): out += (ts if mine[c] == nr else dd)
        elif sgn(nr) == sgn(nd) and nr and nd: out += (ts if abs(nr) < abs(nd) else dd)
        else: amb[c] = (nr, nd); out += dd
    return out, sus, amb


def _bn(x):
    """2026-11-06 查「華新 1605 重開後被當成元三接回」:元大回報 / 查詢裡的自訂編號前面會多「Ewodr」(送出 T104310001 → 回報 EwodrT104310001),
       程式靠第一個字 T / F / R 認自己的單 → 認不得,重開時把程式的部位當成手動(元三)接回。統一拿掉前綴,只留程式送出的那段"""
    b = str(x or "").strip()
    m = re.search(r"([TFR]\d{6,})$", b)
    return m.group(1) if m else b


def _ts_of(t):
    """元大 K 的 TimeStamp → ('YYYY-MM-DD', 'HH:MM')(和 hist_kline.ts_of 同一套)"""
    parts = str(t).strip().split(); y, m, dd = parts[0].replace("/", "-").split("-")
    ap = next((x for x in parts[1:] if x in ("上午", "下午", "AM", "PM")), ""); tm = next(x for x in parts[1:] if ":" in x)
    hh, mi = int(tm.split(":")[0]), int(tm.split(":")[1])
    if ap in ("下午", "PM") and hh < 12: hh += 12
    if ap in ("上午", "AM") and hh == 12: hh = 0
    return f"{int(y):04d}-{int(m):02d}-{int(dd):02d}", f"{hh:02d}:{mi:02d}"


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
        self.kline_lock = threading.Lock()   # 2026-11-06 盤中補名單外股票的 1 分 K(extra_add)和其他查 K 不要同時用 kline_out
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
        self.login_soft = None   # 2026-11-21 使用者「全改」(檢查 R1):暫時被拒(0016 等)的原因 → 退避後再登入
        self.feat_off = {}       # 2026-11-21(檢查 R1):查詢權限不足等 → 只停這個功能(功能名 → 原因),不停交易

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
                    kind = login_kind(st.MsgCode, st.MsgContent, st.Count)
                    if kind == "ok":
                        self.logged_in.set()
                    elif kind == "fatal":
                        # 元大文件「登入」:0000 執行失敗、0102 密碼凍結或未啟用、0112 無此權限使用功能
                        self.login_fail = f"{st.MsgCode} {st.MsgContent}"
                    else:   # 2026-11-21 使用者「全改」(檢查 R1):0016 請勿連續登入 = 暫時,退避後再登入(不停一整天)
                        self.login_soft = f"{st.MsgCode} {st.MsgContent}"
                        self.login_soft_kind = kind   # 2026-11-21 使用者「改 A」(A5):"retry" 明確暫時 / "unknown" 沒看過的碼(最多再試 2 次)
                    return
                if strIndex == "SendStockOrder":
                    r = objValue; st = r.ResultCount
                    for i in range(max(1, st.Count)):
                        with self.tag_lock:
                            tag = self.sent_tags.pop(0) if self.sent_tags else None   # 一次送一筆,回應照送出順序
                        try:                                                       # 回應本身有帶自訂編號就以它為準(元件有這個欄位的話)
                            bn = _bn(getattr(r.ResultList[i], "BasketNo", "")) if st.Count else ""
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
                        if strIndex == "DeleteAlgoCOOdrStrategy" and self.trader:   # 2026-11-08 20:50 刪除確認後才掛新的條件單
                            self.trader.on_strategy_delete_reply(str(d.OrderNo), st, str(d.Msg))
                        if strIndex == "SendAlgoCOOdrStrategy" and self.trader:
                            self.trader.on_strategy_reply(str(d.OrderNo), st in (0, 1, 100, 101), str(d.Msg), str(getattr(d, "StkCode", "") or "").strip() or None)
                    return
                if strIndex == "GetOrderTradeReport" and self.resume_buf is not None:
                    r = objValue
                    lots_of = lambda q: q // 1000 if q >= 1000 else q
                    for i in range(r.StkOrderList.Count):
                        o = r.StkOrderList[i]; self.resume_buf["tags"][str(o.OrderNo).strip()] = _bn(o.BasketNo)
                        self.resume_buf.setdefault("types", {})[str(o.OrderNo).strip()] = str(o.OrderType).strip()
                        try: _cq = int(o.CancelQty)
                        except Exception: _cq = 0
                        rest = int(o.AfterQty) - int(o.OkQty) - _cq   # 2026-10-29 使用者實測(win_order_test):已取消的單 OrderStatus=30、CancelQty=1000、AfterQty=1000、OkQty=0 → 不扣 CancelQty 會當成還掛著
                        if int(o.OrderStatus) not in (1, 2, 24, 25, 30) and rest > 0:   # 還有沒成交的量、而且不是失敗 / 取消 / 失效 / 價穩失效 = 還掛著(含部分成交;2026-10-28 獨立檢查 #1,原本只認 0 / 20)
                            self.resume_buf["working"].append({"code": str(o.CompanyNo).strip(), "bs": str(o.BS).strip(), "lots": lots_of(rest),
                                                               "no": str(o.OrderNo).strip(), "typ": str(o.OrderType).strip(), "tag": _bn(o.BasketNo),
                                                               "px": float(o.Price or 0)})
                    fid_f = CFG.get("FILL_ID_FIELD", "").strip(); _fseen = set()
                    for i in range(r.StkTradeList.Count):
                        x = r.StkTradeList[i]; q = int(x.OkQty)
                        if i == 0: self.trade_fields_note(x)   # 2026-11-21(檢查 R4):成交明細有哪些欄位記一次(找每筆成交的序號用)
                        t_ = {"code": str(x.CompanyNo).strip(), "bs": str(x.BS).strip(), "lots": q // 1000 if q >= 1000 else q,   # 成交量單位文件沒寫:股(≥1000)或張都能處理;使用者只做整張、不做零股(2026-10-22),不會混淆
                              "price": float(x.SPrice), "time": str(x.DateTime), "order_no": str(x.OrderNo).strip()}
                        # 2026-11-21 使用者「全改」(檢查 R4):bridge.env 設了 FILL_ID_FIELD(看過 log 確定是「每筆成交唯一的序號」欄位)→ 帶上它,
                        #   書號 + 序號一樣的才是同一筆重送,這裡直接去掉(之後所有用到成交的地方都乾淨);沒設 → 不刪,交給 fills_guard 看可疑
                        if fid_f:
                            try: fv = str(getattr(x, fid_f, "") or "").strip()
                            except Exception: fv = ""
                            if fv:
                                t_["fill_id"] = fv
                                if (t_["order_no"], fv) in _fseen:
                                    log(f"成交回報重複(書號 {t_['order_no']} 序號 {fv})→ 只算一次"); continue
                                _fseen.add((t_["order_no"], fv))
                        self.resume_buf["trades"].append(t_)
                    self.resume_buf["got_rpt"] = True
                    log(f"接回查詢:今天委託 {r.StkOrderList.Count} 筆、成交 {r.StkTradeList.Count} 筆")
                    try: self.quota_from_report(r)
                    except Exception as e: log("額度計算失敗", e)
                    return
                if strIndex == "GetConditionStrategy" and self.resume_buf is not None:
                    try: lst = objValue.ConditionStrategyList
                    except Exception as e:   # 2026-11-10 回應欄位名稱還沒實盤看過 → 把回應物件的欄位全部記進 log(之後照它改),這次當作沒有條件單
                        try:
                            T = objValue.GetType(); names = [f"{x.Name}:{x.PropertyType.Name}" for x in T.GetProperties()] + [f"{x.Name}:{x.FieldType.Name}" for x in T.GetFields()]
                            log(f"[條件單查詢] 回應 {T.FullName} 沒有 ConditionStrategyList({e});欄位:" + ", ".join(names))
                        except Exception as e2: log("[條件單查詢] 回應欄位讀不到", e2)
                        self.resume_buf["got_oco"] = True; self.resume_buf["oco_failed"] = True; return
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
                        # 2026-11-21 使用者「全改」(檢查 R1):原本任何查詢回 4 / 5 / 9 / 11 都整天停(條件單、K 線查詢權限不足也會停掉交易和 13:20 平倉)→
                        #   只有「還沒登入成功、沒有功能名」(登入階段)才當帳號問題整天停;其他只停那個功能、記紀錄、推手機一次
                        if not strIndex and not self.logged_in.is_set():
                            self.login_fail = f"{d} {QRY_MSG[d]} {objValue}"
                        else:
                            self.feature_off(strIndex or "?", f"{d} {QRY_MSG[d]} {objValue}")
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
                    if getattr(self, "fut_codes", None) and str(r.StkCode).strip() in self.fut_codes:   # 2026-11-13 台指期:只收資料,不進股票 K / 即時價 / 交易
                        self.fut_tick(str(r.StkCode).strip(), t, sn, float(r.DealPrice), int(r.DealVol)); return
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
                        if rt == 50 and int(r.OrderStatus) == 1:   # 2026-11-02 使用者「還沒解決 一個一個來」(台化 / 大甲委託失敗只記到「狀態 1」):把元大回報裡的原因欄位全記下來,並推到手機
                            why = ""
                            try:
                                fs = {}
                                for k in dir(r):
                                    if k.startswith("_"): continue
                                    try: v = getattr(r, k)
                                    except Exception: continue
                                    if callable(v): continue
                                    sv = str(v).strip()
                                    if sv and sv not in ("0", "None"): fs[k] = sv[:80]
                                log("!! 委託失敗 回報全部欄位:", fs)
                                why = next((fs[k] for k in fs if re.search(r"err|msg|reason|note|memo|desc", k, re.I) and not re.fullmatch(r"[\d.\s]+", fs[k])), "")
                            except Exception as e: log("委託失敗欄位讀取失敗", e)
                            cn = str(r.CompanyNo).strip()   # 推手機在另一條執行緒做(不能卡住元大的事件)
                            se = str(getattr(r, "StkErrorNo", "") or "").strip()
                            if se == "21352" and str(r.BS).strip() == "S":   # 2026-11-06 使用者「應該是無券可藉來空 … 我手動操作元大也是一樣無法做空」:做空被 21352 擋 → 這檔今天不再送空單
                                if self.trader: self.trader.no_short(cn, "元大 21352(無券可空)")
                            else:
                                # 2026-11-17 15:20 使用者「1 要通知 通知說明原因」(華邦電 12:02 手動放空被退,原本只寫 X999):標題寫股票 / 買賣 / 張數 / 價格,內容寫原因
                                #   原因:已知錯誤碼對照 → 開盤前複檢(處置 / 注意 / 不可當沖 / 不可先賣)→ 元大給的文字 / 錯誤碼
                                bs_ = str(r.BS).strip(); oe = str(getattr(r, "OrderErrorNo", "") or "").strip()
                                rs_ = REJECT_WHY.get(se) or REJECT_WHY.get(oe) or ""
                                if not rs_:
                                    try: import elig; rs_ = elig.blocked(cn, "short" if bs_ == "S" else "long")
                                    except Exception: rs_ = ""
                                if not rs_ and why and why not in (oe, se): rs_ = why
                                try: lots_ = int(r.OrderQty) // 1000; px_ = float(r.Price)
                                except Exception: lots_, px_ = 0, 0.0
                                bd = (rs_ or "原因元大沒寫") + f"(元大錯誤碼 {'/'.join(x for x in (oe, se) if x) or '?'})"
                                def _rej(cn=cn, bs_=bs_, lots_=lots_, px_=px_, bd=bd):   # 查名稱可能要連網,放另一條執行緒
                                    try: nm_ = self.stock_name(cn)
                                    except Exception: nm_ = ""
                                    self.phone_alert(f"⛔ {nm_ + ' ' if nm_ and nm_ != cn else ''}{cn} {'賣出' if bs_ == 'S' else '買進'} {lots_} 張{f' @ {px_:g}' if px_ else ''} 被元大退單", bd, "pc")
                                threading.Thread(target=_rej, daemon=True).start()
                        typ = str(getattr(r, "OrderType", "") or "").strip()   # 2026-10-28 平倉檢查 #6:委託種類(有的話元三平倉用同一種)
                        tm_ = self.rr_time(r) if rt == 51 else ""   # 2026-11-21 使用者「改 A」(A4):成交時間一起給 trader(分辨晚到的舊回報 / 新的成交)
                        if self.trader: self.trader.on_report(rt, str(r.OrderNo), int(r.OrderStatus), int(r.OrderQty), float(r.Price), _bn(r.BasketNo), str(r.CompanyNo), str(r.BS).strip(), typ, tm=tm_)
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
        self.login_fail = None; self.login_soft = None
        if sys.platform == "win32":
            self.api.Login(acc, pw)
        else:
            self.api.Login(CFG["YT_PFX"], CFG["YT_PFX_PASS"], acc, pw)   # Linux / Mac 要憑證絕對路徑 + 憑證密碼
        for _ in range(40):
            if self.logged_in.wait(0.5):
                self.login_soft_n = 0   # 2026-11-21(檢查 R1):登入成功 → 暫時被拒的次數歸零
                self.login_unk_n = 0    # 2026-11-21 使用者「改 A」(A5):看不懂的回應次數也歸零
                return
            if self.login_fail: raise LoginRejected(self.login_fail)
            if self.login_soft:   # 2026-11-21(檢查 R1):暫時被拒 → 退避後再試
                e_ = LoginRetry(self.login_soft); e_.unknown = getattr(self, "login_soft_kind", "retry") == "unknown"; raise e_
        raise RuntimeError("登入逾時(網路、憑證、API 權限、固定 IP 是否開通?)")

    def close(self):
        """2026-11-21 使用者「全改」(輕微項):LogOut / Close / Dispose 各自 try(原本 LogOut 出錯後面就不做,舊連線沒關乾淨),
           並拿掉 OnResponse(舊連線的事件不會再進來改登入狀態)"""
        api = self.api; self.api = None
        self.logged_in.clear()
        if not api: return
        for nm, fn in (("LogOut", lambda: api.LogOut()), ("解除事件", lambda: self._unhook(api)),
                       ("Close", lambda: api.Close()), ("Dispose", lambda: api.Dispose())):
            try: fn()
            except Exception as e: log(f"關閉例外({nm})", e)

    def _unhook(self, api):
        api.OnResponse -= self.on_response

    def feature_off(self, name, why):
        """2026-11-21 使用者「全改」(檢查 R1):某個查詢功能元大回「權限不足 / 帳號資訊異常」→ 今天只停這個功能(條件單查詢、K 線查詢…),
           交易、13:20 平倉照常;記紀錄、推手機一次(在元大的事件執行緒呼叫 → 推手機走背景)"""
        fo = self.__dict__.setdefault("feat_off", {})
        first = name not in fo
        fo[name] = why
        buf = self.resume_buf
        if buf is not None:   # 正在等這個查詢的回應:不用等到逾時
            if name == "GetConditionStrategy": buf["got_oco"] = True; buf["oco_failed"] = True
            if name == "GetOrderTradeReport": buf["rpt_fail"] = True
        if first:
            log(f"!! 元大查詢 {name} 回 {why} → 今天停用這個功能(交易、13:20 平倉照常)")
            self.phone_alert(f"⚠ 元大 {name} 沒有權限", f"{why[:120]};今天停用這個功能,交易和 13:20 平倉照常")

    def trade_fields_note(self, x):
        """2026-11-21(檢查 R4):元大成交明細(StkTradeList)每筆有哪些欄位 → 一個行程記一次(只記欄位名,和序號 / 時間類欄位的值;帳號類不記),
           找到「每筆成交唯一的序號」欄位後在 bridge.env 設 FILL_ID_FIELD=<欄位名>,成交重複回報就能確定去掉"""
        if getattr(self, "_tf_noted", False): return
        self._tf_noted = True
        try:
            names = [k for k in dir(x) if not k.startswith("_")]
            vals = {}
            for k in names:
                if re.search(r"acc|acct|id$|pass|cert", k, re.I): continue
                if re.search(r"seq|serial|no$|num|time|date|match|deal", k, re.I):
                    try:
                        v = getattr(x, k)
                        if not callable(v): vals[k] = str(v)[:40]
                    except Exception: pass
            log("[成交明細欄位] " + ", ".join(names)[:600] + " | 值:" + json.dumps(vals, ensure_ascii=False)[:400])
        except Exception as e: log("[成交明細欄位] 讀不到", e)

    RR_TIME_FIELDS = ("TradeTime", "MatchTime", "DealTime", "OkTime", "FillTime", "TrxTime", "ReportTime")

    def rr_time(self, r):
        """2026-11-21 使用者「改 A」(A4):即時成交回報(RR 51)的成交時間(給 trader.rc_skip 分辨「晚到的舊回報」和「同一張單之後的新成交」)。
           bridge.env RR_TIME_FIELD=<欄位名> 優先;沒設就找常見的成交時間欄位(不用委託時間 OrderTime:同一張單分批成交時間都一樣);
           都沒有回 ""(trader 改用「對帳記錄 3 分鐘內才算同一筆」)。第一次收到成交回報時把欄位名記進紀錄(找欄位用)"""
        if not getattr(self, "_rr_noted", False):
            self._rr_noted = True
            try:
                vals = {}
                for k in dir(r):
                    if k.startswith("_") or not re.search(r"time|date|seq|serial|match|deal", k, re.I): continue
                    try:
                        v = getattr(r, k)
                        if not callable(v): vals[k] = str(v)[:40]
                    except Exception: pass
                log("[成交回報欄位] 時間 / 序號類:" + json.dumps(vals, ensure_ascii=False)[:400])
            except Exception: pass
        f = CFG.get("RR_TIME_FIELD", "").strip()
        for k in ((f,) if f else ()) + self.RR_TIME_FIELDS:
            try: v = getattr(r, k, None)
            except Exception: v = None
            if v is not None and str(v).strip() not in ("", "0", "None"): return str(v).strip()
        return ""

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

    # ── 2026-11-13 使用者「台指期 元大可以拿到」「下午開工 改」:台指期近月逐筆 → 1 分 K(08:45~13:45),只收資料:
    #   不下單、判別式不看、不進股票的 K / 即時價 / 交易;收工時存本機 kline_futures/TXF/<日期>.csv + 推 Worker 一次(?fbars=push),
    #   14:30 排程存進 GitHub kline_futures/TXF/<週>.csv。代號格式不確定 → 幾種寫法一起訂,紀錄哪個收得到逐筆
    @staticmethod
    def fut_near(d=None):
        d = d or now_tw().date()
        def third_wed(y, m):
            x = dt.date(y, m, 1); x += dt.timedelta(days=(2 - x.weekday()) % 7); return x + dt.timedelta(days=14)
        y, m = d.year, d.month
        if d > third_wed(y, m): y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        return y, m

    def fut_settle_next(self, d=None):
        """結算日(今天近月是本月、明天就換月)→ 回傳下個月 (年, 月);其他日子 None"""
        d = d or now_tw().date(); y, m = self.fut_near(d)
        if (y, m) == (d.year, d.month) and self.fut_near(d + dt.timedelta(days=1)) != (y, m): return self.fut_near(d + dt.timedelta(days=1))
        return None

    def fut_sub(self):
        if FAMILY or CFG.get("FUT_ENABLE", "1") != "1": return   # 2026-11-15 家人帳號:台指期由主帳號收(公式引擎在主帳號)
        try:
            y, m = self.fut_near(); L = "ABCDEFGHIJKL"[m - 1]
            cands = [f"TXF{L}{y % 10}", f"TXF{y}{m:02d}", "TXF1", f"TXF{m:02d}", "TXF"]
            extra = [x.strip() for x in CFG.get("FUT_CODES", "").split(",") if x.strip()]
            self.fut_codes = set(cands + extra)
            if not hasattr(self, "fut_bars"): self.fut_bars, self.fut_sn, self.fut_n = {}, {}, {}
            if not getattr(self, "_fut_api_noted", False):
                self._fut_api_noted = True
                try: log("[台指期] 元大元件裡和訂閱 / 期貨有關的功能:" + ", ".join(n for n in dir(self.api) if re.search(r"Subscribe|Futur|Option", n)))
                except Exception: pass
            Y = self.Y; lst = self.List[Y.StockTick]()
            for c in sorted(self.fut_codes):
                s = Y.StockTick(); s.MarketType = Y.enumMarketType.TAIFEX; s.StockCode = c; lst.Add(s)
            ok = self.api.SubscribeStockTick(CFG["YT_ACCOUNT"], lst)
            log(f"[台指期] 訂閱近月 {y}/{m:02d}(試 {', '.join(sorted(self.fut_codes))}):{'送出' if ok else '回傳失敗'}")
        except Exception as e: log("[台指期] 訂閱失敗(不影響股票)", e)

    def fut_tick(self, code, t, sn, px, vol):
        try:
            if sn < 0 or px <= 0: return
            S = self.fut_sn.setdefault(code, set())
            if sn in S: return
            S.add(sn); n = self.fut_n[code] = self.fut_n.get(code, 0) + 1
            if n == 1: log(f"[台指期] {code} 收到第一筆逐筆:{t.bytHour:02d}:{t.bytMin:02d}:{t.bytSec:02d} 價 {px} 量 {vol}(這個代號寫法可用)")
            hm = f"{t.bytHour:02d}:{t.bytMin:02d}"
            if not ("08:45" <= hm <= "13:45"): return
            if hm != getattr(self, "_fut_hm", None):   # 2026-11-13 22:35 ⑧ 台指期同步進場(使用者「解鎖 改 全部套用」):每換一分鐘就把到目前的 1 分 K 寫本機(電腦引擎讀)+ 推 Worker(手機讀)
                self._fut_hm = hm
                try: self.fut_flush()
                except Exception: pass
            b = self.fut_bars.setdefault(code, {}).get(hm)
            if b is None: self.fut_bars[code][hm] = [px, px, px, px, vol]
            else: b[1] = max(b[1], px); b[2] = min(b[2], px); b[3] = px; b[4] += vol
        except Exception: pass

    def fut_poll(self):
        """⑧ 備援 2(2026-11-13 23:55 使用者選「1」三層備援):電腦直接問期交所即時行情(mis.taifex,雲端主機會被擋,台灣家用網路可以),
           08:44~13:46 每 10 秒一次,台指期近月(成交量最大的 TXF??-F)最新價 → 1 分 K(self.fut_tf);元大逐筆沒來時 fut_flush 改用這份"""
        if FAMILY or CFG.get("FUT_TAIFEX", "1") != "1": return
        n = now_tw()
        if n.weekday() >= 5 or not ("08:44" <= n.strftime("%H:%M") <= "13:46") or time.time() - getattr(self, "_tf_at", 0) < 10: return
        self._tf_at = time.time()
        def run():
            try:
                body = {"MarketType": "0", "SymbolType": "F", "KindID": "1", "CID": "TXF", "ExpireMonth": "", "RowSize": "全部", "PageNo": "", "SortColumn": "", "AscDesc": "A"}
                rq = urllib.request.Request("https://mis.taifex.com.tw/futures/api/getQuoteList", data=json.dumps(body).encode(), method="POST",
                                            headers={"Content-Type": "application/json;charset=UTF-8", "User-Agent": "Mozilla/5.0", "Referer": "https://mis.taifex.com.tw/futures/RegularSession/EquityIndices/FuturesDomestic/"})
                js = json.loads(urllib.request.urlopen(rq, timeout=8).read().decode("utf-8"))
                L = ((js.get("RtData") or {}).get("QuoteList") or [])
                best = None
                _y, _m = self.fut_near(); _near = f"TXF{'ABCDEFGHIJKL'[_m - 1]}{_y % 10}-F"   # 2026-11-18 21:00 使用者「改」:固定近月代號(結算日下個月成交量比較大,原本「量最大」會選到下個月)
                # 2026-11-22 判別式聊天轉達(使用者):結算日近月 13:30 收 → 13:30 以後改收下個月合約(隔天的近月),隔天 ⑧ 強弱的「前一天台指期收盤」才是同一個合約
                _nx = self.fut_settle_next()
                if _nx and now_tw().strftime("%H:%M") >= "13:30":
                    _near = f"TXF{'ABCDEFGHIJKL'[_nx[1] - 1]}{_nx[0] % 10}-F"
                    if getattr(self, "_tf_nx_day", None) != now_tw().date():   # 第一次換:丟掉 13:30 以後還是近月的那幾根
                        self._tf_nx_day = now_tw().date()
                        for _k in [k for k in (getattr(self, "fut_tf", {}) or {}) if k >= "13:30"]: self.fut_tf.pop(_k, None)
                        log(f"[台指期·期交所] 結算日 13:30 以後改收下個月 {_near}")
                if any(str(q.get("SymbolID", "")) == _near for q in L): L = [q for q in L if str(q.get("SymbolID", "")) == _near]
                for q in L:
                    sid = str(q.get("SymbolID", ""))
                    if not re.match(r"^TXF[A-L]\d-F$", sid): continue
                    try: px = float(str(q.get("CLastPrice") or "0").replace(",", "")); vol = float(str(q.get("CTotalVolume") or "0").replace(",", ""))
                    except Exception: continue
                    if px > 0 and (best is None or vol > best[2]): best = (sid, px, vol, str(q.get("CTime") or ""))
                if not best:
                    self._tf_bad = getattr(self, "_tf_bad", 0) + 1
                    if self._tf_bad in (1, 30): log(f"[台指期·期交所] 回應裡找不到台指期近月:{str(js)[:200]}")
                    return
                ct = best[3].zfill(6); hm = f"{ct[:2]}:{ct[2:4]}" if ct.isdigit() and ct != "000000" else now_tw().strftime("%H:%M")
                if not ("08:45" <= hm <= "13:45"): return
                if _nx and best[0] == _near and _near != f"TXF{'ABCDEFGHIJKL'[_m - 1]}{_y % 10}-F" and hm < "13:30": return
                bars = self.__dict__.setdefault("fut_tf", {}); b = bars.get(hm); px = best[1]
                if b is None: bars[hm] = [px, px, px, px, 0]
                else: b[1] = max(b[1], px); b[2] = min(b[2], px); b[3] = px
                if not getattr(self, "_tf_ok", False): self._tf_ok = True; log(f"[台指期·期交所] 收得到:{best[0]} {hm} {px}")
                self.fut_flush()
            except Exception as e:
                self._tf_err = getattr(self, "_tf_err", 0) + 1
                if self._tf_err in (1, 30) or self._tf_err % 300 == 0: log(f"[台指期·期交所] 抓不到(第 {self._tf_err} 次)", repr(e)[:160])
        threading.Thread(target=run, daemon=True).start()

    def ix_poll(self):
        """2026-11-18 21:05 使用者「加權指數可能延遲好幾分鐘 … 直接改」:電腦直接問證交所即時行情(mis.twse tse_t00 加權指數),
           09:00~13:31 每 5 秒一次 → 每分鐘最後價寫 HERE/engine_data/ix_live/<日期>.json({"y": 昨收, "m": {HH:MM: 收}}),電腦引擎 ix_map / txrel_prev 優先用它
           (11/18 引擎用 Worker ?ixbars,加權落後約 5 分鐘,⑧ 強弱算偏)"""
        if FAMILY or CFG.get("IX_LIVE", "1") != "1": return
        n = now_tw()
        if n.weekday() >= 5 or not ("09:00" <= n.strftime("%H:%M") <= "13:31") or time.time() - getattr(self, "_ix_at", 0) < 5: return
        self._ix_at = time.time()
        def run():
            try:
                u = f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=tse_t00.tw&json=1&delay=0&_={int(time.time() * 1000)}"
                r = json.loads(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://mis.twse.com.tw/stock/index.jsp"}), timeout=4).read().decode("utf-8"))
                q = (r.get("msgArray") or [{}])[0]
                z = str(q.get("z") or "").replace(",", ""); t = str(q.get("t") or ""); y = str(q.get("y") or "").replace(",", "")
                try: px = float(z)
                except Exception: return
                if px <= 0 or len(t) < 5: return
                hm = t[:5]
                if not ("09:00" <= hm <= "13:30"): return
                day = n.strftime("%Y-%m-%d")
                if getattr(self, "_ix_day", None) != day: self._ix_day = day; self._ix_m = {}; self._ix_y = None
                self._ix_m[hm] = px
                try: self._ix_y = float(y) or self._ix_y
                except Exception: pass
                f = HERE / "engine_data" / "ix_live"; f.mkdir(parents=True, exist_ok=True); tmp = f / f"{day}.json.tmp"
                tmp.write_text(json.dumps({"y": self._ix_y, "m": dict(sorted(self._ix_m.items()))}), encoding="utf-8"); os.replace(tmp, f / f"{day}.json")
                if not getattr(self, "_ix_ok", False): self._ix_ok = True; log(f"[加權·證交所] 收得到:{hm} {px}(昨收 {self._ix_y})")
            except Exception as e:
                self._ix_err = getattr(self, "_ix_err", 0) + 1
                if self._ix_err in (1, 30) or self._ix_err % 300 == 0: log(f"[加權·證交所] 抓不到(第 {self._ix_err} 次)", repr(e)[:160])
        threading.Thread(target=run, daemon=True).start()

    def fut_pick(self):
        """2026-11-17 16:13 使用者「哪一個穩定就固定哪一個 另外一個當備援」(11/17 TXFK6 / TXF1 逐筆一樣多,原本誰多用誰 → 來源一直跳):
           固定用近月明確代號(例 TXFK6,和期交所 TXFK6-F 同一個);它沒資料或比別的代號落後超過 2 分鐘 → 才改用別的代號(TXF1 等)"""
        n = getattr(self, "fut_n", None) or {}
        if not n: return ""
        y, m = self.fut_near(); prim = f"TXF{'ABCDEFGHIJKL'[m - 1]}{y % 10}"
        mi = lambda h: int(h[:2]) * 60 + int(h[3:5])
        last = {c: max(self.fut_bars.get(c) or {"00:00": 0}) for c in n}
        top = max(mi(v) for v in last.values())
        for c in [prim] + sorted((c for c in n if c != prim), key=lambda c: -n[c]):
            if self.fut_bars.get(c) and mi(last[c]) >= top - 2: return c
        return max(n, key=lambda c: n[c])

    def fut_flush(self):
        """⑧ 盤中每分鐘:台指期 1 分 K → HERE/kline_futures/TXF/<日期>.csv(電腦引擎 tx_ok 讀)+ 背景推 Worker ?fbars=push(手機讀)。
           來源:元大逐筆(逐筆最多的那個代號)優先;元大沒資料 / 落後超過 2 分鐘 → 用電腦直接問期交所的那份(fut_poll)"""
        lk = self.__dict__.setdefault("_fut_lk", threading.Lock())
        with lk:
            day = now_tw().strftime("%Y-%m-%d"); ya = {}; code = ""
            if getattr(self, "fut_n", None):
                code = self.fut_pick(); ya = dict(self.fut_bars.get(code) or {})
            tf = dict(getattr(self, "fut_tf", {}) or {})
            last = lambda m: max(m) if m else "00:00"; mi = lambda h: int(h[:2]) * 60 + int(h[3:5])
            use, src = (ya, "元大 " + code) if ya and (not tf or mi(last(ya)) >= mi(last(tf)) - 2) else (tf, "期交所")
            _nd = now_tw().date(); _y, _m = self.fut_near(_nd)
            if (_y, _m) == (_nd.year, _nd.month) and self.fut_near(_nd + dt.timedelta(days=1)) != (_y, _m) and ya and mi(last(ya)) >= mi("13:28"):   # 2026-11-18 21:00 使用者「改」:結算日近月 13:30 收,之後期交所那份(下個月)不准蓋掉整天
                use, src = ya, "元大 " + code
            if not use: return
            if src != getattr(self, "_fut_src", None): self._fut_src = src; log(f"[台指期] 1 分 K 來源:{src}")
            cur = now_tw().strftime("%H:%M")
            f = HERE / "kline_futures" / "TXF"; f.mkdir(parents=True, exist_ok=True); tmp = f / f"{day}.csv.tmp"
            # 2026-11-16 14:26 使用者「台指期 第一天使用 覺得如何」:盤中重開中繼(12:13 / 12:33)後記憶體只有重開後的分鐘,整份覆蓋掉本機 CSV 和雲端 →
            #   早上的台指期 K 不見、⑧ 60 分方向算不出來(重開後 1 小時進場全被擋)。改成:先讀本機今天那份(重開前存的),再用期交所那份、元大那份蓋上去(新的優先)
            if getattr(self, "_fut_old_d", None) != day:
                self._fut_old_d = day; self._fut_old = {}
                try:
                    with open(f / f"{day}.csv", encoding="utf-8") as fh:
                        for ln in fh:
                            x = ln.strip().split(",")
                            if len(x) >= 7 and x[0] == day: self._fut_old[x[1]] = tuple(float(v) for v in x[2:7])
                    if self._fut_old: log(f"[台指期] 接回本機今天已存的 1 分 K {len(self._fut_old)} 根({min(self._fut_old)}~{max(self._fut_old)})")
                except FileNotFoundError: pass
                except Exception as e: log("[台指期] 讀本機舊 1 分 K 失敗", e)
            mg = dict(self._fut_old); mg.update(tf if use is ya else {}); mg.update(use)
            if self.fut_near(_nd + dt.timedelta(days=1)) != (_y, _m) and (_y, _m) == (_nd.year, _nd.month):   # 2026-11-18 21:00 結算日:近月 13:30 收盤,元大那份(近月)之後不寫
                # 2026-11-22 判別式聊天轉達:13:30 以後只寫期交所那份(fut_poll 已改收下個月合約)或重開前存的,隔天 ⑧ 強弱的前一天收盤才不會停在近月 13:29
                mg = {k: v for k, v in mg.items() if k < "13:30" or (getattr(self, "_tf_nx_day", None) == _nd and k in tf) or (k in self._fut_old and k not in ya)}
            rows = [[hm] + list(v) for hm, v in sorted(mg.items()) if hm < cur]   # 只寫已經收完的分鐘
            if not rows: return
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write("date,time,open,high,low,close,volume\n")
                for r in rows: fh.write(f"{day},{r[0]},{r[1]},{r[2]},{r[3]},{r[4]},{r[5]}\n")
            os.replace(tmp, f / f"{day}.csv")
            # 2026-11-17 15:55 使用者「5 可以」(台指期資料拆開):合併那份照舊給引擎;另外各來源各存一份 + 每分鐘用哪個來源,出問題查得出是哪邊斷
            try:
                sm = {hm: "接回" for hm in self._fut_old}
                if use is ya: sm.update({hm: "期交所" for hm in tf})
                sm.update({hm: (f"元大 {code}" if use is ya else "期交所") for hm in use})
                self.fut_src_save(day, {"元大": ya, "期交所": tf}, sm, cur)
            except Exception as e: log("[台指期] 分來源存檔失敗", e)
            if CFG.get("PUSH_KEY") and CFG.get("WORKER_URL") and rows[-1][0] != getattr(self, "_fut_pushed", None):
                self._fut_pushed = rows[-1][0]
                def _p():
                    try:
                        rq = urllib.request.Request(CFG["WORKER_URL"].rstrip("/") + "/?fbars=push", data=json.dumps({"d": day, "code": code or "TXF(期交所)", "bars": rows}).encode(), method="POST",
                                                    headers={"Content-Type": "application/json", "X-Push-Key": CFG["PUSH_KEY"], "User-Agent": "yuanta-bridge"})
                        urllib.request.urlopen(rq, timeout=15).read()
                    except Exception: pass
                threading.Thread(target=_p, daemon=True).start()

    def fut_src_save(self, day, srcs, smap, cur="99:99"):
        """台指期分來源存檔:kline_futures/TXF_src/<日期>_元大.csv、<日期>_期交所.csv(各自原始 1 分 K)、<日期>_來源.csv(合併那份每分鐘用哪個來源)"""
        d = HERE / "kline_futures" / "TXF_src"; d.mkdir(parents=True, exist_ok=True)
        for nm, bars in srcs.items():
            rows = [(hm, v) for hm, v in sorted((bars or {}).items()) if hm < cur]
            if not rows: continue
            tmp = d / f"{day}_{nm}.csv.tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write("date,time,open,high,low,close,volume\n")
                for hm, v in rows: fh.write(f"{day},{hm}," + ",".join(str(x) for x in list(v)[:5]) + "\n")
            os.replace(tmp, d / f"{day}_{nm}.csv")
        rows = [(hm, w) for hm, w in sorted(smap.items()) if hm < cur]
        if rows:
            tmp = d / f"{day}_來源.csv.tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write("date,time,src\n")
                for hm, w in rows: fh.write(f"{day},{hm},{w}\n")
            os.replace(tmp, d / f"{day}_來源.csv")

    def fut_save(self, day):
        """收工:等到 13:46(台指期 13:45 收盤)→ 存本機 + 推 Worker 一次"""
        if FAMILY: return
        try:
            if not getattr(self, "fut_n", None): log("[台指期] 今天沒收到任何台指期逐筆(代號寫法都不對 / 元件不給),明天看上面那行元件功能清單再調"); return
            while now_tw().strftime("%H:%M") < "13:46" and self.api: time.sleep(5)
            code = self.fut_pick(); bars = self.fut_bars.get(code) or {}
            mg = dict(getattr(self, "_fut_old", {}) or {}) if getattr(self, "_fut_old_d", None) == day else {}   # 2026-11-16 14:26 盤中重開過:重開前存的早上那段也併進來(不被收工這份蓋掉)
            mg.update(getattr(self, "fut_tf", {}) or {})   # 2026-11-17 元大缺的分鐘用期交所那份補(和盤中合併一樣,元大優先)
            mg.update(bars); rows = [[hm] + list(v) for hm, v in sorted(mg.items())]
            f = HERE / "kline_futures" / "TXF"; f.mkdir(parents=True, exist_ok=True)
            with open(f / f"{day}.csv", "w", encoding="utf-8") as fh:
                fh.write("date,time,open,high,low,close,volume\n")
                for r in rows: fh.write(f"{day},{r[0]},{r[1]},{r[2]},{r[3]},{r[4]},{r[5]}\n")
            log(f"[台指期] {code} 1 分 K {len(rows)} 根存好(逐筆 {self.fut_n[code]} 筆;各代號筆數 {self.fut_n})")
            try:   # 2026-11-17 分來源:收工時元大那份存完整;來源表標「元大」(重開前接回的標「接回」)
                tf_ = dict(getattr(self, "fut_tf", {}) or {}); sm = {hm: ("期交所" if hm in tf_ else "接回") for hm in mg if hm not in bars}; sm.update({hm: f"元大 {code}" for hm in bars})
                self.fut_src_save(day, {"元大": bars, "期交所": dict(getattr(self, "fut_tf", {}) or {})}, sm)
            except Exception as e: log("[台指期] 分來源存檔失敗", e)
            if rows and CFG.get("PUSH_KEY") and CFG.get("WORKER_URL"):
                rq = urllib.request.Request(CFG["WORKER_URL"].rstrip("/") + "/?fbars=push", data=json.dumps({"d": day, "code": code, "bars": rows}).encode(), method="POST",
                                            headers={"Content-Type": "application/json", "X-Push-Key": CFG["PUSH_KEY"], "User-Agent": "yuanta-bridge"})
                log(f"[台指期] 推 Worker:{json.loads(urllib.request.urlopen(rq, timeout=20).read().decode())}")
        except Exception as e: log("[台指期] 存檔 / 推送失敗", e)

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
        self._send_raw(lst, tag, f"{code} {side} {lots} 張")
        return None   # 委託書號從回應事件來(trader 用 BasketNo 對)

    def _send_raw(self, lst, tag, what=""):
        """2026-11-21 使用者「全改」(檢查 R7):送單 + 記編號。沒登入 / 元件不在 → 不送、丟出清楚的錯誤;SendStockOrder 丟例外或回傳失敗 →
           把剛記的編號拿掉(原本例外時編號留在 sent_tags,之後每一筆下單回應都對到前一筆的編號 → 回報對錯單)"""
        api = self.api
        if api is None or not self.logged_in.is_set():
            log(f"!! 元大沒有登入(重連中),不送單 {what}")
            raise ConnectionError(f"元大沒有登入(重連中),沒有送出 {what}")
        with self.tag_lock:                                                 # 記編號和送出是同一步,別的執行緒插不進來
            self.sent_tags.append(tag)
            try:
                ok = api.SendStockOrder(CFG["YT_ACCOUNT"], lst)
            except BaseException:
                if self.sent_tags and self.sent_tags[-1] == tag: self.sent_tags.pop()
                raise
            if not ok:
                log(f"!! SendStockOrder 回傳失敗 {what}")
                if self.sent_tags and self.sent_tags[-1] == tag: self.sent_tags.pop()   # 沒送出去就不會有回應
        return ok

    def cancel_order(self, order_no, code, bs=None, typ=None):
        lo = getattr(self, "_last_order", None) or {}
        o = self.Y.StockOrder()
        o.Identify = 2; o.Account = CFG["YT_ACCOUNT"]; o.APCode = 0; o.TradeKind = 4; o.OrderType = str(typ or lo.get("typ", "0"))   # TradeKind 04 = 取消
        o.StkCode = code or lo.get("code", ""); o.PriceFlag = " "; o.Price = 0; o.OrderQty = 0; o.BuySell = bs or lo.get("side", "B")
        o.OrderNo = order_no; o.TradeDate = now_tw().strftime("%Y/%m/%d"); o.BasketNo = ""; o.Time_in_force = "0"
        lst = self.List[self.Y.StockOrder](); lst.Add(o)
        self._send_raw(lst, "", f"取消 {order_no}")                          # 取消單的回應不給 trader(編號空字串)

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
        if not ok and self.trader: self.trader.on_strategy_reply("", False, "SendAlgoCOOdrStrategy 回傳失敗", code)

    def delete_oco(self, no):
        self.delete_strategy(3, no)                                                     # 3 二擇一

    def get_cond(self, typ):
        """2026-11-10 15:31(實際時間)使用者「如何解決」:GetConditionStrategy 用 (帳號, StrategyType, 代碼) 一直「No method matches given arguments」→
           失敗時用 .NET 反射把元大元件真正的參數(型別 / 名稱)記進 log,並照型別自動組參數試每一種寫法(查詢,不會下單);試成功的記住,下次直接用"""
        if "GetConditionStrategy" in getattr(self, "feat_off", {}):   # 2026-11-21(檢查 R1):條件單查詢今天沒權限 → 當作查詢失敗(呼叫的人照舊處理)
            raise RuntimeError("條件單查詢今天停用(元大回權限不足)")
        if getattr(self, "_cond_how", None) is not None:
            m, mk = self._cond_how; return m.Invoke(self.api, mk(typ))
        try:   # 2026-11-10 16:07 使用者在電腦跑 win_cond_sig.py 讀出元件真正的寫法:GetConditionStrategy(String 帳號, Int32 類別, String 代碼, enumLangType 語言);語言 NORMAL=0 / UTF8=1 / SC=2
            return self.api.GetConditionStrategy(CFG["YT_ACCOUNT"], int(typ), "", self.Y.enumLangType(0))
        except Exception as e0:
            S = self.System; ms = [m for m in self.api.GetType().GetMethods() if m.Name == "GetConditionStrategy"]
            sigs = ["(" + ", ".join(f"{q.ParameterType.FullName} {q.Name}" for q in m.GetParameters()) + ")" for m in ms]
            if not getattr(self, "_cond_logged", False): self._cond_logged = True; log(f"[條件單查詢] 元大元件的 GetConditionStrategy 有 {len(ms)} 種寫法:" + " | ".join(sigs))
            for m in ms:
                ps = list(m.GetParameters())
                def mk(t, ps=ps):
                    out = []
                    for q in ps:
                        ty = q.ParameterType; nm = str(q.Name).lower(); tn = str(ty.FullName)
                        if ty.IsEnum: out.append(S.Enum.ToObject(ty, int(t)))
                        elif tn == "System.String": out.append(CFG["YT_ACCOUNT"] if ("acc" in nm or "acct" in nm) else "")
                        elif tn in ("System.Int32", "System.Int16", "System.Int64", "System.Byte"): out.append(S.Convert.ChangeType(int(t), ty))
                        elif tn == "System.Boolean": out.append(True)
                        else: raise TypeError("不會組 " + tn)
                    return S.Array[S.Object](out)
                try:
                    r = m.Invoke(self.api, mk(typ)); self._cond_how = (m, mk)
                    log(f"[條件單查詢] 這種寫法成功:{sigs[ms.index(m)]}(之後都用它)"); return r
                except Exception as e1: log(f"[條件單查詢] {sigs[ms.index(m)]} 也不行:{str(e1)[:120]}")
            raise e0

    def delete_strategy(self, typ, no):
        Y = self.Y
        x = Y.DeleteStrategy(); x.Account = CFG["YT_ACCOUNT"]; x.StrategyType = Y.StrategyType(int(typ)); x.StrategyNo = str(no).strip()
        lst = self.List[Y.DeleteStrategy](); lst.Add(x)
        self.api.DeleteAlgoCOOdrStrategy(CFG["YT_ACCOUNT"], lst)

    # ── 手機即時價:本機小服務 + Cloudflare Tunnel(Worker 的 ?codes 先問這裡,不通才問證交所)──
    def serve_quotes(self):
        bridge = self
        def key_ok(k):
            want = CFG.get("PUSH_KEY") or ""
            return bool(want) and bool(k) and hmac.compare_digest(str(k).encode("utf-8"), want.encode("utf-8"))
        class H(BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_GET(self):
                u = urlparse(self.path)
                if u.path == "/health":
                    return self.reply({"ok": True, "age": bridge.quotes.mis([])["age"]})
                if not key_ok(self.headers.get("X-Quote-Key")):   # 2026-11-21 使用者「全改」(輕微項):沒設 PUSH_KEY 時原本 None == None 會放行 → 一律拒絕
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
                if u.path != "/trade/cmd" or not key_ok(self.headers.get("X-Quote-Key")):
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
        from trader import stop_path
        halted = (t.stop_file if t else stop_path(CFG)).exists()   # 2026-10-31 緊急停止狀態(手機標題變灰色)
        if not t:
            return {"ok": False, "msg": "電腦的交易程式還沒啟動(盤前 / 收盤後)", "halted": halted}
        return {"ok": True, "enabled": t.enabled, "pos": t.positions_view(), "locked": sorted(t.locked), "skipped": sorted(getattr(t, "skipped", set()))[-300:], "closed": sorted(getattr(t, "closed_ok", set()))[-300:], "at": now_tw().strftime("%H:%M:%S"), "halted": halted, "pxOk": True, "orders": t.live_orders() if hasattr(t, "live_orders") else None, "redo": t.over_list() if hasattr(t, "over_list") else [], "ignore": sorted(c for c in getattr(t, "ignore", set()) if c != "YY0070")}   # ignore:2026-11-17 09:46 使用者「庫存的和益是借卷 空 我已經贖回 這邊不用顯示和益做多」→ 手機監控區 / 當沖不列不管名單的成交   # redo:2026-11-03 超額訊號回到上限內、等手機確認補進場的   # pxOk:2026-11-03 起強制進場可指定委託價(舊版電腦沒有這個 → 手機不顯示調價格)

    def trade_cmd(self, cmd):
        pin = CFG.get("TRADE_CMD_PIN", "")
        # 2026-10-29 12:50 使用者「直接改好 不用密碼」(下單分秒必爭):bridge.env 沒設(或留空)TRADE_CMD_PIN 就不檢查密碼;有設才檢查(通道本身仍要 Worker 的金鑰才進得來)
        if pin and str(cmd.get("pin", "")) != pin:
            log("[交易] 強制指令:交易密碼不對"); return {"ok": False, "msg": "交易密碼不對", "badPin": True}
        t = self.trader
        act = cmd.get("act")
        # 2026-11-21 使用者「全改」(檢查 R7):要送單的指令(強制進場 / 強平 / 全部平倉 / 補進場)在元大沒登入(重連中)時直接回清楚的原因,不送到一半丟例外
        broker_up = self.api is not None and self.logged_in.is_set()
        if act in ("close_only", "close", "enter", "redo") and t and not broker_up:
            log(f"[交易] 手機指令 {act}:元大沒有登入(重連中),不執行")
            return {"ok": False, "msg": "電腦目前沒有登入元大(重連中),指令沒有執行,請稍後再試或到投資先生操作"}
        if act == "close_only":
            # 2026-11-03 11:39 使用者「加一個只全部平倉 訊息正常」:所有未平倉部位馬上漲停買回 / 跌停賣出,但不建緊急停止檔(自動進場、通知照常,標題不變灰);平掉的那幾檔今天不再自動進
            from trader import stop_path
            sp = t.stop_file if t else stop_path(CFG)
            n = t.close_all("手機全部平倉", lock=True) if t else 0
            log(f"[交易] 手機只全部平倉(不停止進場):{n} 檔")
            threading.Thread(target=self.phone_alert, daemon=True, args=("🔻 全部平倉", (f"{n} 檔未平倉部位正在用漲停買回 / 跌停賣出平倉" if n else "目前沒有未平倉部位") + ";自動進場照常(平掉的這幾檔今天不再進)")).start()
            return {"ok": True, "halted": sp.exists(), "closed": n, "msg": f"{n} 檔全部平倉中,自動進場照常" if n else "目前沒有未平倉部位"}
        if act in ("halt", "resume", "halt_close"):
            # 2026-10-31 使用者「在 名揚四海。自動當沖 這 設下 長按 即啟動緊急停止功能」:建立 / 刪除緊急停止檔(交易程式沒在跑也能設,開盤時照樣生效)
            from trader import stop_path
            sp = t.stop_file if t else stop_path(CFG)
            try:
                if act in ("halt", "halt_close"):
                    sp.parent.mkdir(parents=True, exist_ok=True); sp.write_text(now_tw().strftime("%Y-%m-%d %H:%M:%S") + (" 手機長按進場(停止所有進場,隔天開工自動解除)" if cmd.get("src") == "entry" and act == "halt" else " 手機長按標題"), encoding="utf-8")   # 2026-11-22 src=entry = 下排進場鍵長按
                else:
                    sp.unlink(missing_ok=True)
            except Exception as e:
                log("[交易] 緊急停止檔設定失敗", e); return {"ok": False, "msg": f"電腦設定失敗:{e}", "halted": sp.exists()}
            on = sp.exists()
            if act == "halt_close":
                # 2026-10-31 使用者「改」(緊急停止多一個「停止進場 + 全部平倉」):所有未平倉部位馬上漲停買回 / 跌停賣出(和虧到 5 萬同一個做法)
                if t and not broker_up:   # 2026-11-21(檢查 R7):停止檔照建,平倉要等登入
                    log("[交易] 手機緊急停止 + 全部平倉:元大沒有登入(重連中),只建停止檔、沒有平倉")
                    return {"ok": False, "halted": on, "closed": 0, "msg": "已啟動緊急停止,但電腦目前沒有登入元大(重連中),全部平倉沒有執行,請到投資先生平倉"}
                n = t.close_all("手機緊急停止 + 全部平倉") if t else 0
                log(f"[交易] 手機啟動緊急停止 + 全部平倉:{n} 檔({sp})")
                threading.Thread(target=self.phone_alert, daemon=True, args=("⛔ 已啟動緊急停止 + 全部平倉",
                                 (f"{n} 檔未平倉部位正在用漲停買回 / 跌停賣出平倉" if n else "目前沒有未平倉部位") + ";不再自動進場,強制進場照常")).start()
                return {"ok": True, "halted": on, "closed": n, "msg": f"已啟動緊急停止,{n} 檔全部平倉中" if n else "已啟動緊急停止(沒有未平倉部位)"}
            log(f"[交易] 手機{'啟動' if on else '解除'}緊急停止({sp})")
            threading.Thread(target=self.phone_alert, daemon=True, args=("⛔ 已啟動緊急停止" if on else "✅ 已解除緊急停止",   # 另開執行緒:不拖慢回應(Worker 8 秒逾時)
                             "不再自動進場;已有部位照常停利 / 停損 / 13:20 平倉,強制進場照常" if on else "恢復自動進場")).start()
            return {"ok": True, "halted": on, "msg": "已啟動緊急停止" if on else "已解除緊急停止"}
        if not t:
            return {"ok": False, "msg": "電腦的交易程式還沒啟動(盤前 / 收盤後)"}
        code = re.sub(r"\D", "", str(cmd.get("code", "")))[:6]; cid = str(cmd.get("id", ""))[:40] or None
        if not code:
            return {"ok": False, "msg": "沒有股票代碼"}
        log(f"[交易] 手機強制指令 {act} {code} {cmd.get('dir', '')} {cmd.get('lots', '')} 張(編號 {cid})")
        if act == "close":
            if code not in t.positions and cmd.get("manual"):   # 2026-11-07 手機長按當沖:元大有、程式還沒接手的手動單 → 查元大成交,接成元三再強平(掛著的手動單先取消)
                r = self.query_today(with_oco=False)
                ok, msg = t.force_close_manual(code, r["trades"], r["working"], r.get("tags"), r.get("types"), cid) if r else (False, "查不到元大今天的成交,請自己到投資先生平")
            else:
                ok, msg = t.force_close(code, cid)
        elif act == "redo":                                          # 2026-11-03 手機「確認補進場」(超額訊號回到多空上限內)
            ok, msg = t.over_confirm(code, cid)
        elif act == "redo_no":
            ok, msg = t.over_drop(code)
        elif act == "enter":
            mkt = self.mkt_of_code(code)
            if code not in [c for c, _ in self.codes]:               # 不在名單上:主迴圈補訂閱逐筆,才盯得到停利停損
                self.codes.append((code, mkt)); self.pending_sub.append((code, mkt))
            ref = cmd.get("ref")
            # 2026-11-11 23:06 使用者「全改」(第三輪) 4:手機帶來的昨收不再寫進電腦的昨收(原本會被這檔之後的判別式 ±5% / 6.5% 拿去用);只給 force_enter 在電腦沒有昨收時用
            ok, msg = t.force_enter(code, int(cmd.get("dir", 0)), int(cmd.get("lots", 0)), cmd.get("tpPct"), cmd.get("slPct"), str(mkt), cid, ref, cmd.get("px"))   # 2026-11-03 px = 手機指定的委託價
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
        try:   # 2026-11-12 09:01 使用者「和益是做空的 怎麼寫做多」:留倉部位(carry_positions.json,前一天的賣出)今天回補 → 明細只看到今天的買進,被當成「做多 1 張沒沖」
            #   → 把留倉那筆前一天的成交當成今天第一筆放進來,今天回補就和它配成一趟(顯示做空、已沖銷、輸贏照前一天賣價算)
            _cf = HERE / "carry_positions.json"
            if _cf.exists():
                _td = now_tw().strftime("%Y-%m-%d")
                _carry = [{"code": str(it["code"]), "bs": "S" if int(it["dir"]) < 0 else "B", "lots": int(it["lots"]), "price": float(it["entry"]), "time": "00:00:00", "order_no": "CARRY"}
                          for it in (json.loads(_cf.read_text(encoding="utf-8")) or []) if it.get("date", _td) == _td and not it.get("nofill")]   # 只放「今天」接回的那筆;nofill = 今天自己的成交(明細裡本來就有)
                if _carry: r = dict(r, trades=_carry + list(r["trades"]))
        except Exception as _e: log("留倉放進明細失敗", _e)
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
            if CFG.get("EXTRA_ADD", "1") == "1" and "09:00" <= now_tw().strftime("%H:%M") < "13:25":
                threading.Thread(target=self.extra_add, args=(code, mkt), daemon=True).start()
        return str(mkt)

    def kline_query(self, code, mkt, day):
        """用中繼這條已登入的連線查元大 1 分 K(day = YYYY-MM-DD);回傳 [(YYYY-MM-DD, HH:MM, o, h, l, c, 股數)]"""
        if "GetKLine" in getattr(self, "feat_off", {}): return []   # 2026-11-21(檢查 R1):K 線查詢今天沒權限 → 不查
        with self.kline_lock:
            self.kline_out = []
            try:
                self.api.GetKLine(CFG["YT_ACCOUNT"], self.Y.KLineType(0), mkt, code, day.replace("-", "/"), day.replace("-", "/"))
                t0 = time.time(); last = -1; still = 0.0
                while time.time() - t0 < 5:
                    time.sleep(0.1); n = len(self.kline_out)
                    if n and n == last:
                        still += 0.1
                        if still >= 0.4: break
                    else: still = 0.0
                    last = n
                rows = list(self.kline_out)
            finally:
                self.kline_out = None
        mult = float(CFG.get("VOL_MULT", "1000") or 1000); out = []
        for k in rows:
            d, hm = _ts_of(k[0])
            if d == day and "09:00" <= hm <= "13:30": out.append((d, hm, float(k[1]), float(k[2]), float(k[3]), float(k[4]), int(round(float(k[5]) * mult))))
        time.sleep(0.5)                      # 查詢一秒最多 3 次
        return out

    def extra_add(self, code, mkt):
        """2026-11-06 使用者 10:14「如果我在元大下單 監控裡面沒有的股票 … 程式必須重新拉這檔股票進入監控名單 k圖回補五天資料」→「完全加入名單」:
           元三成交的名單外股票 → 用元大 1 分 K 補今天到目前 + 前 5 個交易日,寫進引擎的 K 檔、推上 Worker,寫 engine_data/extra_codes.json(引擎每分鐘看,當成名單股照算判一~五)"""
        try:
            day = now_tw().strftime("%Y-%m-%d"); d0 = dt.date.fromisoformat(day)
            days = [day] + [(d0 - dt.timedelta(days=k)).isoformat() for k in range(1, 15) if (d0 - dt.timedelta(days=k)).weekday() < 5]
            got = {}; n_trade = 0
            for i, d in enumerate(days):
                rows = self.kline_query(code, mkt, d)
                if i == 0 and not rows and str(mkt) != str(self.Y.enumMarketType.TWOTC):   # 今天查不到:可能是上櫃股(名單外不知道市場)→ 改上櫃再查
                    m2 = self.Y.enumMarketType.TWOTC; r2 = self.kline_query(code, m2, d)
                    if r2: rows = r2; mkt = m2; self.pending_sub.append((code, m2)); log(f"[名單外] {code} 是上櫃股,改上櫃訂閱")
                if rows: got[d] = rows
                if i == 0 and rows and self.trader:   # 2026-11-11 22:44 使用者「全改」 11:臨時訂閱的股票補今天 1 分 K → 移動停損利馬上有 Ka
                    try: self.trader.seed_mc(code, rows)
                    except Exception as e: log("補 Ka 失敗", e)
                if d != day and rows: n_trade += 1
                if n_trade >= 5: break
            if not got:
                log(f"[名單外] {code} 元大查不到 1 分 K,不加入名單"); return
            byw = {}
            for d, rows in got.items():
                y, w, _ = dt.date.fromisoformat(d).isocalendar(); byw.setdefault(f"{y}-W{w:02d}", {})[d] = rows
            # 模擬器先讀 kline/(雲端 K)再用 kline_yuanta/ 蓋;名單外股票 kline/ 一個檔都沒有會整檔讀失敗 → 兩邊都寫
            for ed, wk, dd in [(HERE / "engine_data" / sub / code, wk, dd) for sub in ("kline_yuanta", "kline") for wk, dd in byw.items()]:
                ed.mkdir(parents=True, exist_ok=True)
                f = ed / f"{wk}.csv"; old = []
                if f.exists():
                    with open(f, encoding="utf-8") as fh: old = [r for r in __import__("csv").DictReader(fh) if r["date"] not in dd]
                lines = ["date,time,open,high,low,close,volume\n"] + [f"{r['date']},{r['time']},{r['open']},{r['high']},{r['low']},{r['close']},{r['volume']}\n" for r in old]
                for d in sorted(dd): lines += [f"{a},{b},{o},{h},{l},{c},{v}\n" for a, b, o, h, l, c, v in dd[d]]
                tmp = f.with_suffix(".tmp"); tmp.write_text("".join(lines), encoding="utf-8"); os.replace(tmp, f)
            prev_days = sorted(d for d in got if d < day); prev = got[prev_days[-1]][-1][5] if prev_days else (self.quotes.meta.get(code) or {}).get("y")
            name = ""
            try: name = self.trader.name_of(code) if self.trader else ""
            except Exception: pass
            otc = str(mkt) == str(self.Y.enumMarketType.TWOTC)
            # 2026-11-22 使用者「全部做」(D7):名單外股票原本固定寫 daytrade=1、short_first=1 → 引擎 canshort=True,不能放空的股票也發空單訊號。
            #   改成照查得到的資料判斷(官方可當沖 / 暫停先賣後買名單、今天的資格複檢、元大回過無券、本機以前的名單檔),查不到一律保守當作不可先賣
            dtf, sff, dsrc = self.extra_dt(code, otc)
            log(f"[名單外] {code} 當沖資格:可當沖 {dtf}、可先賣 {sff}({dsrc})")
            xf = HERE / "engine_data" / "extra_codes.json"
            try: xj = json.loads(xf.read_text(encoding="utf-8"))
            except Exception: xj = {}
            if xj.get("day") != day: xj = {"day": day, "codes": []}
            xj["codes"] = [s for s in xj["codes"] if s.get("code") != code] + [{"code": code, "name": name if name and name != code else code, "market": "tpex" if otc else "twse",
                           "close": prev, "prev": prev, "date": prev_days[-1] if prev_days else "", "daytrade": dtf, "short_first": sff, "extra": 1, "at": now_tw().strftime("%H:%M")}]
            tmp = xf.with_suffix(".tmp"); tmp.write_text(json.dumps(xj, ensure_ascii=False), encoding="utf-8"); os.replace(tmp, xf)
            n_k = sum(len(r) for r in got.values())
            log(f"[名單外] {code} 加入今天的監控名單:補 {len(got)} 天 {n_k} 根元大 1 分 K(今天 {len(got.get(day, []))} 根),昨收 {prev}")
            threading.Thread(target=self.push_extra_k, args=(code, got), daemon=True).start()
            threading.Thread(target=self.push_extra_list, args=(xj,), daemon=True).start()
            self.phone_alert(f"✅ {name or code} {code} 已加入監控名單", f"名單外手動成交 → 補了 {len(got)} 天元大 1 分 K,判一~五開始照算")
        except Exception as e:
            log(f"[名單外] {code} 加入名單失敗", e)

    def extra_dt(self, code, otc=False):
        """2026-11-22 使用者「全部做」(D7):名單外股票的 (daytrade, short_first, 來源說明)。
           1. 官方名單(和 elig.py / make_pool.py 同一份:證交所 TWTB4U、櫃買可當沖 + 暫停先賣後買),一天抓一次(失敗 10 分鐘後再試)
           2. 官方名單抓不到 → 本機最近 7 天的名單檔 pool_*.json 裡有這檔的標記
           3. 都查不到 → 可當沖 1(只影響空單)、可先賣 0(保守:引擎不發空單)
           之後再套:今天資格複檢檔(不可當沖 / 不可先賣 / 處置)、元大今天回過無券(trader.noshort)→ 一律不可先賣"""
        c = str(code); dtf, sff, src = None, None, ""
        try:
            import elig
            day = now_tw().strftime("%Y-%m-%d"); L = self.__dict__.get("_dtl")
            if not L or L.get("day") != day or (not L.get("ok") and time.time() - L.get("t", 0) > 600):
                m, ok, pause = {}, set(), set()
                for mk, url in (("twse", elig.TWSE_DT), ("tpex", elig.TPEX_DT)):
                    try:
                        got, _ = elig.parse_dt(elig.get_json(url, timeout=15, tries=1))
                        if got: m.update(got); ok.add(mk)
                    except Exception as e: log(f"[名單外] 抓{'上市' if mk == 'twse' else '上櫃'}可當沖名單失敗", e)
                try: pause = set(elig.parse_dt(elig.get_json(elig.TPEX_DT_PAUSE, timeout=15, tries=1))[0])
                except Exception as e: log("[名單外] 抓上櫃暫停先賣後買失敗", e)
                L = self._dtl = {"day": day, "m": m, "ok": ok, "pause": pause, "t": time.time()}
            mk = "tpex" if otc else "twse"
            if c in L["m"]:
                dtf, sff, src = 1, 1 if L["m"][c] == 1 and c not in L["pause"] else 0, "官方可當沖名單"
            elif L["ok"] >= {"twse", "tpex"} or mk in L["ok"]:
                dtf, sff, src = 0, 0, "不在官方可當沖名單"
        except Exception as e: log(f"[名單外] {c} 查官方當沖名單失敗", e)
        if dtf is None:
            try:
                lim = (now_tw().date() - dt.timedelta(days=7)).isoformat()
                for f in sorted(HERE.glob("pool_*.json"), reverse=True)[:10]:
                    if f.stem[5:] < lim: break
                    j = json.loads(f.read_text(encoding="utf-8")); pool = j.get("pool", j) if isinstance(j, dict) else j
                    x = next((x for x in pool if str(x.get("code")) == c and "short_first" in x), None)
                    if x:
                        dtf, sff, src = (1 if x.get("daytrade") == 1 else 0), (1 if x.get("short_first") == 1 else 0), f"本機名單檔 {f.name}"; break
            except Exception as e: log(f"[名單外] {c} 查本機名單檔失敗", e)
        if dtf is None: dtf, sff, src = 1, 0, "查不到資料,保守當作不可先賣"
        try:
            import elig; e = elig.load_today() or {}
            if c in (e.get("nodt") or []): dtf, sff, src = 0, 0, src + ";今天複檢不可當沖"
            if sff and (c in (e.get("noshort") or []) or c in (e.get("disposed") or []) or c in (e.get("all_disposed") or [])): sff, src = 0, src + ";今天複檢不可先賣 / 處置"
        except Exception: pass
        try:
            if sff and self.trader and c in (getattr(self.trader, "noshort", None) or {}): sff, src = 0, src + ";元大今天回過無券"
        except Exception: pass
        if not dtf: sff = 0
        return dtf, sff, src

    def push_extra_list(self, xj):
        """名單外股票清單推上 Worker(?extra=push),手機自己算判別式時(電腦離線)也會把它們加進名單"""
        if not CFG.get("PUSH_KEY") or not CFG.get("WORKER_URL"): return
        try:
            req = urllib.request.Request(CFG["WORKER_URL"].rstrip("/") + "/?extra=push", data=json.dumps({"d": xj["day"], "codes": xj["codes"]}).encode(), method="POST",
                                         headers={"Content-Type": "application/json", "X-Push-Key": CFG["PUSH_KEY"], "User-Agent": "yuanta-bridge"})
            log(f"[名單外] 清單推上 Worker:{urllib.request.urlopen(req, timeout=15).read().decode()[:80]}")
        except Exception as e:
            log("[名單外] 清單推上 Worker 失敗", e)

    def push_extra_k(self, code, got):
        """補的 K 推上 Worker(?ybars=push merge,和那分鐘原本的股票合併),雲端 / 手機也看得到"""
        if not CFG.get("PUSH_KEY") or not CFG.get("WORKER_URL"): return
        ok = n = 0
        for d, rows in sorted(got.items()):
            for _, hm, o, h, l, c, v in rows:
                n += 1
                body = json.dumps({"d": d, "m": hm, "bars": {code: [o, h, l, c, v]}, "merge": 1}).encode()
                req = urllib.request.Request(CFG["WORKER_URL"].rstrip("/") + "/?ybars=push", data=body, method="POST",
                                             headers={"Content-Type": "application/json", "X-Push-Key": CFG["PUSH_KEY"], "User-Agent": "yuanta-bridge"})
                try:
                    if json.loads(urllib.request.urlopen(req, timeout=15).read().decode()).get("ok"): ok += 1
                except Exception as e:
                    if n - ok <= 3: log(f"[名單外] {code} {d} {hm} 推 Worker 失敗", e)
                time.sleep(0.03)
        log(f"[名單外] {code} 補的 K 推上 Worker {ok}/{n} 分鐘")

    def trade_post(self, title, body, kind, t=None, pos=None):
        """2026-10-28 強三進出場、強平 → 寫一則交易訊息到手機的訊息欄(Worker ?log=add,和手機自己發的同一套)"""
        if FAMILY:   # 2026-11-15 家人帳號:不寫交易卡(會混進主帳號的元大明細 / 進場清單),改成標名字的提醒
            return self.phone_alert(title, re.sub(r"\s*·\s*元大中繼\s*$", "", body or ""), "pc")
        try:
            n = now_tw(); m = re.match(r"^\S+ (\d{4,6})", title); code = m.group(1) if m else ""
            rs = (re.search(r"· (判[一二三四五六七八九十])\s*$", body) or [None, ""])[1]   # 2026-11-13 10:30 判五~七也帶判別式(鍵和手機一樣)
            it = {"d": n.strftime("%Y-%m-%d"), "t": t or n.strftime("%H:%M"), "title": title,   # 2026-11-10 t:元三進場卡改寫時用原本那分鐘(同鍵覆蓋)
                  "body": body, "kind": kind,
                  "key": code + "|" + re.sub(r"[0-9.,+−%↑↓\s⚠✅-]", "", title) + "|" + rs}   # 鍵和手機一樣:代碼|標題去掉數字|判別式
            if pos: it["pos"] = pos   # 2026-11-13 10:30 部位鍵:和電腦引擎同一部位的平倉卡雲端只留一則(誰先到留誰)
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
        if FAMILY or not CFG.get("PUSH_KEY") or CFG.get("QUOTA_PUSH", "1") != "1": return   # 2026-11-15 家人帳號:額度不推(手機資金顯示的是主帳號)
        fill = work = 0.0
        # 2026-11-02 13:51 使用者「強平時程式掛漲停價買回,元大是用漲停價先扣額度 — 元大就是這樣做」:成交照「委託價」算額度(漲停買回 / 跌停賣出用委託價,
        #   比成交價高的話用委託價;市價單 / 拿不到委託價照成交價),和元大的剩餘額度對齊(今天程式以為剩 44 萬、元大只剩 24 萬,台化 / 大甲被退單)
        oprice = {}
        for i in range(r.StkOrderList.Count):
            try: oprice[str(r.StkOrderList[i].OrderNo).strip()] = float(r.StkOrderList[i].Price or 0)
            except Exception: pass
        _seen = set()   # 2026-11-16 12:06 使用者「用成交序號去掉重複的成交」:同一筆成交(書號 + 時間 + 價 + 量 + 買賣 + 代碼)回報兩次只算一次
        for i in range(r.StkTradeList.Count):
            x = r.StkTradeList[i]; dp = float(x.SPrice)
            _k = (str(x.OrderNo).strip(), str(x.DateTime), dp, int(x.OkQty), str(x.BS).strip(), str(x.CompanyNo).strip())
            if _k in _seen: continue
            _seen.add(_k)
            fill += dp * int(x.OkQty)   # 2026-11-04 11:24 使用者「應該是平倉後 資金額度會回補回來」→「改」:已成交的照實際成交價算(強平掛漲停 / 跌停,成交後元大把多扣的補回);還掛著的單照委託價(work)、沒平倉的部位照漲停預留(hold)不變  # 原 11/02 版:成交照 max(成交價, 委託價)
        if self.trader and abs(fill - getattr(self.trader, "amount_today", 0)) >= 1:   # 2026-11-16 12:03 使用者「那就是計算出了問題 元大總資金還380幾萬」→「500萬扣元大明細裡的總價金」:程式「今天已用額度」一律照元大成交總價金(原本只往上調;11/16 斷線重開接回後程式算 5,784,900、元大實際約 340 萬 → 10:51 後都不進場、世紀手動加碼被平)
            try: self.trader.log(f"[交易] 今天成交金額照元大總價金校正 {getattr(self.trader, 'amount_today', 0):,.0f} → {fill:,.0f}") if abs(fill - getattr(self.trader, "amount_today", 0)) >= 10000 else None
            except Exception: pass
            self.trader.amount_today = fill
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
        total = float(CFG.get("QUOTA_TOTAL", CFG.get("TRADE_MAX_AMOUNT", "5000000")))
        if total == 4900000: total = 5000000   # 2026-10-31 14:57 使用者「490萬 改成500萬」(bridge.env 舊的 490 萬也當 500 萬)
        # 2026-11-02 16:16 使用者「當沖還未贖回 皆以漲停價 暗自計算 … 剩餘的 才是能再交易的資金」→「改」:還沒平倉的部位(還沒掛平倉單的)用漲停價先扣 = hold
        hold = 0.0
        try:
            if self.trader: hold = self.trader.hold_reserve(("進場中", "持倉中"))
        except Exception: hold = 0.0
        body = json.dumps({"d": now_tw().strftime("%Y-%m-%d"), "hm": now_tw().strftime("%H:%M:%S"), "fill": round(fill), "work": round(work), "hold": round(hold), "total": total}).encode()
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
        cap = self.__dict__.get("_qcap")   # 2026-11-21 使用者「改 A」(A1):13:20 平倉路上的查詢有時間上限(元大不回應時不能卡主迴圈)
        if not self.qlock.acquire(timeout=min(40, cap) if cap else 40):
            log(f"!! 查詢排隊超過 {min(40, cap) if cap else 40:g} 秒,這次不查"); return None
        with_oco = with_oco and self.broker_stop_on()   # 2026-10-29 16:43 使用者「改」:不掛條件單(TRADE_BROKER_STOP=0)就不查條件單(元大元件的參數和文件對不上,查了只會記一行失敗)
        self.resume_buf = {"trades": [], "tags": {}, "types": {}, "oco": [], "working": [], "got_rpt": False, "got_oco": not with_oco}
        try:
            self.api.GetOrderTradeReport(True, CFG["YT_ACCOUNT"])                       # True = 不列出取消單
            if with_oco:
                time.sleep(1.2)                                                         # 帳務 / 查詢類一秒最多 3 次
                try: self.get_cond(3)   # 3 = 二擇一;2026-11-10 改走 get_cond(失敗時自動找對的參數寫法)
                except Exception as e:   # 2026-10-29 12:03 正式環境實測:No method matches given arguments (str, StrategyType, str) → 參數型別 / 順序和文件不同,先當作沒有條件單,不讓它把整個查詢弄掛(TRADE_BROKER_STOP=0 本來就不掛條件單)
                    self.resume_buf["got_oco"] = True; self.resume_buf["oco_failed"] = True; log("查二擇一條件單失敗(改用狀態檔存的編號):", e)
            for _ in range(max(1, int(cap / 0.5)) if cap else 30):
                if self.resume_buf["got_rpt"] and self.resume_buf["got_oco"]: break
                if self.resume_buf.get("rpt_fail"): break   # 2026-11-21(檢查 R1):元大回這個查詢沒權限 → 不用等逾時
                time.sleep(0.5)
            return dict(self.resume_buf) if self.resume_buf["got_rpt"] else None
        finally:
            self.resume_buf = None; self.qlock.release()

    def query_strategies(self, types=(1, 2, 3, 4, 5)):
        """查全部有效條件單(1 停損利、2 移動鎖利、3 二擇一、4 母子單、5 多條件)
           2026-11-21 使用者「全改」(檢查 R11):查不到(排隊逾時 / 元大沒回)回 None,不再回 [](原本 13:20 會被當成「確認沒有條件單」);
           沒開條件單 / 今天條件單查詢沒權限 → [](本來就不會有程式掛的條件單)"""
        if not self.broker_stop_on(): return []   # 2026-10-29 16:43 不掛條件單就不查(同 query_today)
        if "GetConditionStrategy" in getattr(self, "feat_off", {}): return []
        cap = self.__dict__.get("_qcap")   # 2026-11-21 使用者「改 A」(A1):同 query_today
        if not self.qlock.acquire(timeout=min(40, cap) if cap else 40):
            log("!! 查詢排隊超過時間,這次不查條件單"); return None
        self.resume_buf = {"trades": [], "tags": {}, "oco": [], "working": [], "got_rpt": True, "got_oco": False, "want_oco": len(types), "n_oco": 0}
        try:
            for t in types:
                try: self.get_cond(t); time.sleep(1.2)   # 查詢類一秒最多 3 次
                except Exception as e:   # 2026-10-29 同上:簽章不合就當作沒有條件單
                    self.resume_buf["got_oco"] = True; log("查條件單失敗(當作沒有):", e); break
            for _ in range(max(1, int(cap / 0.5)) if cap else 20):
                if self.resume_buf["got_oco"]: break
                time.sleep(0.5)
            if not self.resume_buf["got_oco"]:
                log("!! 條件單查詢元大沒回應(這次當作查不到)"); return None
            return list(self.resume_buf["oco"])
        finally:
            self.resume_buf = None; self.qlock.release()

    @contextlib.contextmanager
    def qcap(self, sec):
        """2026-11-21 使用者「改 A」(A1):這段時間內的元大查詢(query_today / query_strategies)最多等 sec 秒(排隊 + 等回應)"""
        old = self.__dict__.get("_qcap"); self._qcap = sec
        try: yield
        finally: self._qcap = old

    def close_all(self, at):
        """一次做完整個 13:20 取消 / 平倉流程(測試、手動用;主迴圈用 close_all_step 分好幾圈做,不卡主迴圈)"""
        self._ca = None
        for _ in range(500):
            if self.close_all_step(at, paced=False): return True
            if not self.logged_in.is_set(): return False
            time.sleep(2)
        return False

    def close_all_step(self, at, paced=True):
        """2026-10-22 使用者:「13:20 就要先取消所有掛單 再進行贖回動作」:
           1. 程式自己的條件單 / 委託先取消 2. 查元大全部還掛著的委託 + 全部有效條件單,通通取消(含手動的) 3. 再查確認(最多 3 次)
           4. 用最新的成交回報算還沒沖銷的部位 → 漲停買回 / 跌停賣出 ROD(trader.recheck)
           2026-11-21 使用者「全改」(檢查 R11):查詢失敗(None / 逾時)原本被當成「沒有掛著的委託」→ 印「確認:沒有掛著的委託和條件單」就去平倉,
             其實可能還有手動單掛著。改成:查不到不算確認,重查(最多 CLOSE_CONFIRM_TRIES 輪,預設 6),要一次「委託 + 條件單都查成功而且都沒有」才算確認
           2026-11-21 使用者「改 A」(A1):元大查詢不回應時,原本整段(6 輪確認、每輪最多等 15 秒)卡住主迴圈 134~149 秒,trader 每秒檢查(含 13:21 備援平倉)
             都沒跑,平倉單 13:22:14 才送出。改成分階段、每圈最多 CLOSE_PASS_SEC(預設 20)秒、查詢每次最多等 CLOSE_QUERY_WAIT(預設 8)秒:
             第 1 圈先取消程式自己的單,馬上照程式自己記的部位送平倉單(trader.close_now,不等任何查詢);之後每圈做一步
             (查手動預掛 → 確認 / 取消掛單一輪 → 最後查成交複查),中間主迴圈照常跑(trader.step、看門狗、推 K)。
             確認掛單時程式剛送出的平倉單(自訂編號時間 ≥ 13:20 或是程式記著的平倉單)不取消。
           2026-11-21 使用者「改 A」(A6):原本第一檔送單失敗(元大沒登入丟 ConnectionError)整個 close_all 中斷、而且已經標成做完 → 其他檔要等 13:21 / 13:22。
             改成每檔各自 try,失敗的檔記下來,之後每圈(重連登入後)再送;全部送出、複查也沒有失敗才算做完。
           回傳 True = 全部做完"""
        t = self.trader; day = now_tw().strftime("%Y-%m-%d")
        ca = self.__dict__.get("_ca")
        if not ca or ca.get("day") != day or ca.get("at") != at:
            ca = self._ca = {"day": day, "at": at, "ph": "start", "k": 0, "keep": set(), "fast": set(), "fail": set(), "next": 0.0, "nf": 0, "t0": time.time()}
        if ca["ph"] == "done": return True
        if not self.logged_in.is_set():   # A6:沒登入(重連中)→ 什麼都送不出去,重連後下一圈接著做
            if not ca.get("off"): ca["off"] = True; log(f"!! [交易] {at} 平倉:元大沒有登入(重連中)→ 連上後馬上接著做")
            return False
        ca["off"] = False
        if paced and time.time() < ca["next"]: return False
        t0 = time.time(); budget = float(CFG.get("CLOSE_PASS_SEC", "20") or 20)
        qw = max(1.0, min(float(CFG.get("CLOSE_QUERY_WAIT", "8") or 8), budget / 2.5))
        left = lambda: budget - (time.time() - t0)
        tries = max(3, int(CFG.get("CLOSE_CONFIRM_TRIES", "6") or 6))
        cnow = getattr(t, "close_now", None)
        if ca["ph"] == "start":
            log(f"[交易] {at} 取消全部掛單 → 平倉(先照程式記的部位馬上送平倉單,再確認 / 取消掛單)")
            try: t.prepare_close_all()
            except Exception as e: log(f"!! [交易] {at} 取消程式自己的掛單出錯(照樣先送平倉單)", e)
            ca["ph"] = "fast"
        if ca["ph"] == "fast" or ca["fail"]:
            redo = ca["ph"] != "fast"
            sent, fail = cnow(at, codes=set(ca["fail"]) if redo else None) if cnow else (set(), set())
            ca["fast"] |= set(sent); ca["fail"] = set(fail)
            if sent: log(f"[交易] {at} 已照程式部位{'補' if redo else '馬上'}送平倉單:{', '.join(sorted(sent))}")
            if fail:
                log(f"!! [交易] {at} 平倉單送不出去:{', '.join(sorted(fail))} → 下一圈(重連後)再送")
                if not ca.get("fail_alerted"):
                    ca["fail_alerted"] = True; self.phone_alert(f"⚠ {at} 平倉單送不出去 {len(fail)} 檔", f"{', '.join(sorted(fail))};中繼每圈重送(元大重連後),請留意")
            if ca["ph"] == "fast": ca["ph"] = "pick"
        with self.qcap(qw):
            if ca["ph"] == "pick" and left() >= qw:
                kp = self.prehang_pick(at)   # 2026-11-05 使用者「程式要檢查 我是否預掛」→「改」:手動預掛、剛好平掉未沖銷張數的平倉單保留(書號)
                if kp is None:
                    # 2026-11-22 使用者「全部做」(U2):查不到元大委託 → 不清掉盤中看到的預掛、2 秒後(下一圈)重查;
                    #   重查 CLOSE_CONFIRM_TRIES 次還是查不到 → 保留盤中看到的手動預掛(trader 也照它們等到期限,期限到了照舊取消再平),其他照舊確認 / 取消
                    ca["pk"] = ca.get("pk", 0) + 1; ca["next"] = time.time() + 2
                    if ca["pk"] < tries:
                        log(f"[交易] {at} 查手動預掛:元大委託查不到(第 {ca['pk']} 次)→ 預掛資訊先不動,下一圈重查")
                        return False
                    fast_ = ca.get("fast") or set()
                    with t.lock: kp = {n for c_, v in (t.prehang or {}).items() if c_ not in fast_ for n in v}
                    log(f"!! [交易] {at} 查手動預掛:{ca['pk']} 次都查不到 → 保留盤中看到的手動預掛 {len(kp)} 筆,其他照舊取消")
                    if kp: self.phone_alert(f"⚠ {at} 查不到元大委託", f"保留盤中看到的手動預掛 {len(kp)} 筆,{t.prehang_deadline} 前沒成交由程式平,請在投資先生確認")
                ca["keep"] = set(kp); ca["ph"] = "confirm"
            if ca["ph"] == "confirm" and left() >= qw:
                keep = ca["keep"]; ca["k"] += 1; k = ca["k"]; ca["next"] = time.time() + 2
                r = self.query_today(with_oco=False)
                st = self.query_strategies() if left() >= qw * 0.5 or not self.broker_stop_on() else None
                if r is None or st is None:
                    log(f"[交易] {at} 確認掛單:{'委託' if r is None else '條件單'}查不到(第 {k} 次)→ 不算確認,重查")
                mine = self._close_mine(at)
                wk = [w for w in (r or {}).get("working", []) if w.get("no") not in keep and not self._is_mine_close(w, at, mine)]
                if r is not None and st is not None and not wk and not st:
                    log(f"[交易] {at} 確認:沒有掛著的委託和條件單" + (f"(保留手動預掛 {len(keep)} 張委託)" if keep else "")); ca["ph"] = "final"; ca["next"] = 0.0
                else:
                    for w in wk:
                        try:
                            log(f"[交易] {at} 取消委託 {w['code']} {w['bs']} {w['lots']} 張 書號 {w['no']}")
                            self.cancel_order(w["no"], w["code"], w["bs"], w["typ"]); time.sleep(0.15)   # 交易類一秒最多 10 次
                        except Exception as e: log(f"!! [交易] {at} 取消委託 {w.get('no')} 失敗(下一輪再取消)", e)
                    for x in st or []:
                        try:
                            log(f"[交易] {at} 刪除條件單 {x['code']} 類別 {x['type']} 編號 {x['no']}")
                            self.delete_strategy(x["type"], x["no"]); time.sleep(0.15)
                        except Exception as e: log(f"!! [交易] {at} 刪除條件單 {x.get('no')} 失敗(下一輪再刪)", e)
                    if k >= tries:
                        self.phone_alert(f"⚠ {at} 還有取消不掉 / 查不到的掛單", "元大委託或條件單沒有查到確認,請在投資先生確認委託 / 條件單")
                        ca["ph"] = "final"
                    return False   # 剛取消 / 查不到:2 秒後(下一圈)再查
            if ca["ph"] == "final" and left() >= qw:
                ca["next"] = time.time() + 2
                r = self.query_today(with_oco=False)
                try: t.recheck_fail = set()
                except Exception: pass
                if not r:
                    log(f"!! {at} 查不到成交回報,改用程式記的部位平倉")
                    with t.lock:
                        # 2026-11-22 使用者選 B(留倉對帳):trader.recheck 會自己加上留倉期初張數(with_carry)→ 這裡照程式部位扣掉留倉期初,才不會算兩次
                        cb = t.carry_base() if hasattr(t, "carry_base") else {}
                        sq = {p["code"]: p["qty"] * (1 if p["dir"] > 0 else -1) for p in list(t.positions.values()) if p["qty"]}
                        sq = {c: sq.get(c, 0) - cb.get(c, 0) for c in set(sq) | set(cb)}
                        r = {"trades": [{"code": c, "bs": "B" if n > 0 else "S", "lots": abs(n)} for c, n in sq.items() if n], "working": []}
                    t.recheck(r["trades"], [], at)
                else:
                    safe, amb = self.guard_trades(r["trades"], at)   # 2026-11-21(檢查 R4):可疑的重複成交回報不能讓平倉單反向開新倉
                    mine = self._close_mine(at)
                    with self.skip_codes(amb):
                        t.recheck(safe, [w for w in r["working"] if w.get("no") in ca["keep"] or self._is_mine_close(w, at, mine)], at)
                rf = set(getattr(t, "recheck_fail", None) or ())
                if not rf and not ca["fail"]:
                    ca["ph"] = "done"; log(f"[交易] {at} 取消 / 平倉流程做完(從開始 {time.time() - ca['t0']:.0f} 秒)"); return True
                ca["nf"] += 1
                log(f"!! [交易] {at} 複查有送不出去的:{', '.join(sorted(rf | ca['fail']))}(第 {ca['nf']} 次)→ 下一圈再做")
                if now_tw().strftime("%H:%M") >= "13:30" or ca["nf"] >= 60:
                    self.phone_alert(f"⚠ {at} 平倉沒有全部送出", f"{', '.join(sorted(rf | ca['fail']))};請到投資先生手動平倉"); ca["ph"] = "done"; return True
        return False

    def _close_mine(self, at):
        """2026-11-21 使用者「改 A」(A1):程式現在掛著的平倉單自訂編號(13:20 確認掛單時不能取消自己剛送的平倉單)"""
        out = set(); t = self.trader
        try:
            with t.lock:
                for p in t.positions.values():
                    o = p.get("order")
                    if o and not o.get("dead") and o.get("purpose") == "平倉" and o.get("tag"): out.add(str(o["tag"]))
        except Exception: pass
        return out

    @staticmethod
    def _is_mine_close(w, at, mine=()):
        """這張掛著的委託是不是程式在 at(13:20)之後送的單 / 程式記著的平倉單(自訂編號 T / F / R + 時分秒)"""
        tg = _bn(w.get("tag") or "")
        if tg and tg in mine: return True
        m = re.match(r"[TFR](\d{6})", tg)
        return bool(m) and m.group(1) >= str(at).replace(":", "")[:4] + "00"

    def _mine_net(self):
        """程式自己記的部位 {code: 有號張數}(fills_guard 判斷可疑重複時當第二個證據)"""
        out = {}; t = self.trader
        try:
            with t.lock:
                cb = t.carry_base() if hasattr(t, "carry_base") else {}
                for c, p in t.positions.items():
                    # 2026-11-22 使用者選 B(留倉對帳):元大成交回報只有今天的 → 留倉部位要扣掉留倉期初張數才是「今天成交應該有的淨張數」
                    if p.get("qty"): out[c] = int(p["qty"]) * (1 if p["dir"] > 0 else -1) - (cb.get(c, 0) if p.get("carry") else 0)
        except Exception: pass
        return out

    def guard_trades(self, trades, at=""):
        """2026-11-21 使用者「全改」(檢查 R4):成交回報先過 fills_guard(見上)。回傳 (安全的成交清單, 判斷不了的股票集合);
           有可疑重複 → 記紀錄、推手機(同一檔同一組重複一天只推一次);判斷不了的檔呼叫的人用 skip_codes 跳過(不送單、不結束部位)"""
        safe, sus, amb = fills_guard(trades, self._mine_net())
        if sus:
            noted = self.__dict__.setdefault("_dup_noted", set()); day = now_tw().strftime("%Y-%m-%d")
            for c, dup in sus.items():
                key = (day, c, len(dup), c in amb, bool(re.fullmatch(r"\d{1,2}:\d{2}", str(at or ""))))   # 2026-11-21「改 A」(A2):收盤平倉的提醒和盤中對帳分開算
                if key in noted: continue
                noted.add(key)
                d0 = dup[0]; what = f"{c} {d0.get('bs')} {d0.get('lots')} 張 @ {d0.get('price')}(書號 {d0.get('order_no')} {d0.get('time')})"
                if c in amb:
                    nr, nd = amb[c]
                    log(f"!! {at} 成交回報疑似重複 {len(dup)} 筆:{what};照原樣淨 {nr:+d}、去重複淨 {nd:+d} 方向不同 → 這檔程式不自動補單 / 平倉,請人工確認")
                    self.phone_alert(f"⚠ {self.name_of_safe(c)} {c} 成交回報疑似重複", f"{what};照原樣未沖銷 {nr:+d} 張、去掉重複 {nd:+d} 張,程式判斷不了 → 這檔不自動補單,請到投資先生確認部位")
                else:
                    # 2026-11-21 使用者「改 A」(A2):看實際用了哪一份(照原樣 / 去重複);收盤平倉 / 複查(at 是時間)因為疑似重複而少平 → 推手機請人工確認
                    net_ = lambda xs: sum(int(x.get("lots") or 0) * (1 if str(x.get("bs", "")).upper().startswith("B") else -1) for x in xs if x.get("code") == c)
                    nr_, ns_ = net_(trades), net_(safe)
                    if nr_ == ns_:
                        log(f"[交易] {at} 成交回報疑似重複 {len(dup)} 筆:{what};程式自己記的部位和照原樣一樣 → 當成真的分筆成交,照原樣 {nr_:+d} 張處理")
                    else:
                        log(f"[交易] {at} 成交回報疑似重複 {len(dup)} 筆:{what}(不影響平倉方向,照較少的張數處理:照原樣 {nr_:+d}、採用 {ns_:+d})")
                        if re.fullmatch(r"\d{1,2}:\d{2}", str(at or "")):
                            self.phone_alert(f"⚠ {at} {self.name_of_safe(c)} {c} 平倉張數因疑似重複成交而減少",
                                             f"{what};照原樣未沖銷 {nr_:+d} 張、程式採用 {ns_:+d} 張(程式記 {self._mine_net().get(c, 0):+d})→ 可能少平 {abs(nr_) - abs(ns_)} 張,請到投資先生確認")
        return safe, set(amb)

    def name_of_safe(self, c):
        """只查記憶體裡的名稱(不連網,13:20 平倉路上不能等)"""
        try: return (self.quotes.meta.get(c) or {}).get("n") or c
        except Exception: return c

    @contextlib.contextmanager
    def skip_codes(self, codes):
        """暫時把這幾檔放進 trader.ignore(不管名單):recheck / reconcile 跳過它們(不送單、不結束部位),離開時拿掉(原本就在不管名單的不動)"""
        t = self.trader; codes = set(codes or ())
        if not codes or t is None:
            yield; return
        with t.lock:
            add = codes - set(t.ignore); t.ignore |= add
            try: yield
            finally: t.ignore -= add

    def prehang_pick(self, at):
        """13:20 查元大還掛著的委託:手動下的(沒有程式的自訂編號)、方向是平倉、同一檔加起來不超過未沖銷張數 → 保留,回傳書號集合;
           張數比未沖銷少也保留(沒預掛的張數程式平,2026-11-17);太多 / 期限設 0 → 不保留(照舊取消,程式全部平)"""
        # 2026-11-22 使用者「全部做」(U2):原本一開頭就把 t.prehang 清空,查詢失敗回 set() 也不重試 → 盤中看到的手動預掛資訊整個不見,
        #   確認掛單時被當成一般掛單取消、13:21 trader 備援照全部張數平(模擬 phdrop:13:20 就把要等到 13:25 的預掛取消了)。
        #   改:查詢失敗回 None、不動原本的預掛資訊(close_all_step 下一圈重查);查成功才整個換成這次查到的
        t = self.trader
        if t.prehang_deadline in ("", "0") or t.prehang_deadline <= t.force_exit:   # 2026-11-08 01:08 期限 ≤ 13:20 = 13:20 全部取消(預設)
            t.prehang = {}; return set()
        r = self.query_today(with_oco=False)
        if not r: return None
        ph = {}
        trs, amb = self.guard_trades(r["trades"], at)   # 2026-11-21(檢查 R4):可疑重複成交的檔判斷不了未沖銷張數 → 不保留預掛(照舊取消),複查也跳過、推手機請人工確認
        net = {}
        for x in (t.with_carry(trs) if hasattr(t, "with_carry") else trs): net[x["code"]] = net.get(x["code"], 0) + x["lots"] * (1 if x["bs"] == "B" else -1)   # 2026-11-22 使用者選 B(留倉對帳):加上留倉期初張數
        keep = set()
        fast = (self.__dict__.get("_ca") or {}).get("fast") or set()
        for c, n in net.items():
            if n == 0 or c in amb or c in fast: continue   # 2026-11-21 使用者「改 A」(A1):13:20 已經照程式部位整個送了平倉單的檔,手動預掛不保留(照舊取消,免得多平)
            cs = "S" if n > 0 else "B"
            man = [w for w in r["working"] if w["code"] == c and w["bs"] == cs and not str(w.get("tag") or "").strip()]
            q = sum(w["lots"] for w in man)
            if not man: continue
            if 0 < q <= abs(n):   # 2026-11-17 14:05 使用者「四張平倉 二張照預掛邏輯 一直到13點25分」→「改」:預掛比部位少也保留,沒預掛的張數 13:20 由程式平
                keep |= {w["no"] for w in man}; ph[c] = [w["no"] for w in man]
                for w in man:   # ph_lots 要知道每張預掛的張數(盤中沒看到的預掛這裡補記),才能只平沒預掛的張數
                    if not (t.man_meta.get(w["no"]) or {}).get("lots"): t.man_meta[w["no"]] = {"bs": w["bs"], "typ": w.get("typ"), "lots": w["lots"], "fl": 0, "px": w.get("price") or 0}
                log(f"[交易] {at} {c} 有手動預掛平倉 {q} 張(未沖銷 {abs(n)} 張)→ 保留,{t.prehang_deadline} 前沒成交才由程式平" + (f";沒預掛的 {abs(n) - q} 張程式現在平" if q < abs(n) else ""))
            else:
                log(f"[交易] {at} {c} 手動預掛 {q} 張比未沖銷 {abs(n)} 張多 → 照舊取消,程式全部平")
        t.prehang = ph   # U2:查成功才換
        if keep: self.phone_alert(f"✅ {at} 保留手動預掛平倉 {len(t.prehang)} 檔", f"{', '.join(t.prehang)};{t.prehang_deadline} 前沒成交會改由程式平倉")
        return keep

    def prehang_expire(self, at):
        """期限到了:保留的手動預掛還掛著 → 取消,讓複查照舊平倉
           2026-11-21 使用者「改 A」(A1):原本在這裡連續查 / 取消最多 6 輪(元大不回應時卡主迴圈 2 分多鐘)→ 改成每次呼叫只做一輪(查詢有時間上限),
           回傳 (狀態, 取消的書號, 這輪查到的回報):狀態 None = 還在確認(主迴圈下一圈再叫)、True = 做完(r 不是 None 就是剛確認過的最新回報,複查可以直接用)"""
        t = self.trader
        px = self.__dict__.get("_phx")
        if not px or px.get("at") != at:
            if not t.prehang: return True, set(), None
            px = self._phx = {"at": at, "nos": {n for v in t.prehang.values() for n in v}, "k": 0, "next": 0.0, "done": False}; t.prehang = {}
        nos = px["nos"]
        if px["done"]: return True, nos, None
        if time.time() < px["next"]: return None, nos, None
        tries = max(3, int(CFG.get("CLOSE_CONFIRM_TRIES", "6") or 6))
        # 2026-11-05 13:33 使用者「13.24還沒成交 程式需要先幫我取消預掛 再啟動平倉」:先取消、再查到確定取消掉(最多 3 次),之後的複查才用最新成交回報平倉
        #   (取消前剛好成交的,成交回報會算進去,不會多平)
        px["k"] += 1; k = px["k"]; px["next"] = time.time() + 2
        r = self.query_today(with_oco=False)
        if r is None:   # 2026-11-21 使用者「全改」(檢查 R11):查不到不能當成「已取消」,重查
            log(f"[交易] {at} 手動預掛確認:查不到元大委託(第 {k} 次)→ 重查")
        else:
            left = [w for w in r["working"] if w.get("no") in nos]
            if not left:
                log(f"[交易] {at} 手動預掛已確認取消 / 成交,開始平倉"); px["done"] = True
                return True, nos, r
            for w in left:
                try:
                    log(f"[交易] {at} 手動預掛 {w['code']} {w['bs']} {w['lots']} 張到期限沒成交 → 先取消(第 {k} 次),確認後由程式平倉")
                    self.cancel_order(w["no"], w["code"], w["bs"], w["typ"]); time.sleep(0.15)
                except Exception as e: log(f"!! [交易] {at} 取消手動預掛 {w.get('no')} 失敗(下一輪再取消)", e)
        if k >= tries:
            self.phone_alert(f"⚠ {at} 手動預掛取消不掉", "程式照樣平倉,請在投資先生確認有沒有多平")
            px["done"] = True; return True, nos, None
        return None, nos, None

    def fam_backup(self, day):
        """2026-11-15 家人帳號第二階段(使用者「再來發布的金鑰 就是自己只能看到自己的」):每分鐘 POST ?pcbk=put {d, acct, items:{pos, pnl, fills, info}}
           → 雲端存 pcbka:<名字>:<日期>:*;owner 鑰匙看全部人、家人鑰匙(fa11…)只看自己(?acct=list / ?acct=get)。
           成交每 2 分鐘向元大查一次(30 秒內重複問會用上一次的結果);失敗只記紀錄,不影響交易"""
        try:
            items = {}
            if self.api and self.logged_in.is_set() and time.time() - getattr(self, "_ff_at", 0) > 120:
                self._ff_at = time.time()
                try: self.trade_fills()
                except Exception as e: log("[家人] 查成交失敗", e)
            fc = (getattr(self, "_fills_cache", None) or (0, None))[1] or self.fills_saved()
            if fc: items["fills"] = fc
            t = self.trader
            if t is not None:
                with t.lock: items["pos"] = [{k: p.get(k) for k in ("code", "dir", "qty", "entry", "judge", "tp", "sl", "state", "t0", "typ")} for p in t.positions.values()]
                items["pnl"] = {"pnl": round(getattr(t, "pnl", 0) or 0), "n": getattr(t, "n_trades", 0), "amount": round(getattr(t, "amount_today", 0) or 0)}
                items["info"] = {"acct": "…" + str(CFG.get("YT_ACCOUNT", ""))[-4:], "max_amount": getattr(t, "max_amount", None), "cap": getattr(t, "cap", None),
                                 "max_loss": getattr(t, "max_loss", None), "kill_loss": getattr(t, "kill_loss", None), "enabled": bool(getattr(t, "enabled", False)),
                                 "names": {c: (self.quotes.meta.get(c) or {}).get("n", "") for c in list(t.positions)},
                                 # 2026-11-17 使用者「資金 多空上限那些設定 也要打勾」:回報現在用的設定 + 套用的是雲端哪一次(at),夥伴加入拿來一項一項比對
                                 "cfg_at": getattr(self, "_acfg_at", 0), "lot_mode": getattr(t, "lot_mode", 0), "auto": bool(getattr(t, "auto", False)),
                                 "judges": sorted(x for x in (getattr(t, "judges", None) or ()) if x in JUDGES_ALL)}
            req = urllib.request.Request(CFG["WORKER_URL"].rstrip("/") + "/?pcbk=put", data=json.dumps({"d": day, "acct": ACCT, "items": items}, ensure_ascii=False, default=str).encode(), method="POST",
                                         headers={"Content-Type": "application/json", "X-Push-Key": CFG.get("PUSH_KEY", "")})
            urllib.request.urlopen(req, timeout=15).read()
        except Exception as e:
            n = getattr(self, "_bk_fail", 0) + 1; self._bk_fail = n
            if n == 1 or n % 30 == 0: log(f"[家人 {ACCT}] 雲端備份失敗(第 {n} 次)", e)

    def cap_sync(self):
        """2026-11-02 多空上限主控(使用者:「其他二隻跟電腦偵測到 都自動改成與5861這隻一樣的金額」):讀雲端主導機的值改 trader.cap(之後新進場的張數照它算);
           TRADE_CAP=0(固定張數)不動;讀不到維持原值(bridge.env 的 TRADE_CAP / ENGINE_CAP)"""
        if FAMILY: return   # 2026-11-15 家人帳號:多空上限照自己的 bridge.env,不跟 5861 主導機
        v = fetch_cap(CFG["WORKER_URL"], CFG.get("PUSH_KEY", ""))
        if v: v = min(v, 250000)   # 2026-11-11 22:46 使用者「最多上限改25萬」
        try: import trader as _trc; _cached = bool(getattr(_trc, "CAP_FROM_CACHE", False))
        except Exception: _cached = False
        if v and _cached: log(f"多空上限:雲端讀不到,用電腦存的最後一次 {v:,}(雲端恢復會再讀)")
        if v: self.trader.master_cap = v
        if v and not _cached: self.cap_ok_day = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 8 * 3600))   # 2026-11-20:用電腦存的不算「今天讀到了」,雲端恢復還會再讀   # 2026-11-11 22:35 使用者「改 1 2 3 4 5」 4:讀到了(今天)
        if self.trader.cap <= 0: return
        if v and v != self.trader.cap:
            log(f"多空上限 {self.trader.cap:,} → {v:,}(5861 主導機)"); self.trader.cap = v
        try:   # 2026-11-18 00:26 🔓→🔒 鐵律 ⑤(使用者「解鎖 改 都改」):主帳號的分級下單照主導機選的(App 設定:1 能買幾張 / 2 只買 1 張 / 3 分級縮;trader.lots_for 和夥伴同一套);沒設 = 1
            import trader as _tr
            lm = int(float((_tr.CLOUD_SET or {}).get("lot_mode") or 1))
            if v and lm in (1, 2, 3) and lm != getattr(self.trader, "lot_mode", 0):
                log(f"分級下單 {getattr(self.trader, 'lot_mode', 0)} → {lm}(5861 主導機)"); self.trader.lot_mode = lm
        except Exception as e: log("分級下單讀取失敗(照舊)", e)
        try:   # 2026-11-18 00:46 使用者「投資金額 / 禁止入場 / 全部平倉」→「照 浮輸 然後 改」:主導機 App 設定(set.amt / stop / kill,「500萬」「2萬」「5萬」)→ trader.max_amount / max_loss / kill_loss(都看 已平倉 + 帳面);範圍和夥伴一樣,超出 / 讀不到 = 照舊
            import trader as _tr
            S = _tr.CLOUD_SET or {}
            def _w(k, lo, hi):
                try:
                    x = float(str(S.get(k, "")).replace("萬", "")) * 10000
                    return x if lo <= x <= hi else None
                except Exception: return None
            for k, attr, lo, hi in (("amt", "max_amount", 1000000, IRON_AMOUNT), ("stop", "max_loss", 5000, 30000), ("kill", "kill_loss", 10000, 50000)):
                x = _w(k, lo, hi)
                if v and x is not None and abs(float(getattr(self.trader, attr, 0) or 0) - x) > 0.5:
                    log(f"{attr} {getattr(self.trader, attr, 0):,.0f} → {x:,.0f}(5861 主導機)"); setattr(self.trader, attr, x)
        except Exception as e: log("投資金額 / 停止進場 / 全部平倉讀取失敗(照舊)", e)   # 以主導機為準(鐵律 ⑤ 一張上限 = 主導機多空上限,2026-11-11 22:24 使用者「針對 2項 改鐵律」:一張上限改以主導機多空上限為準,不再固定最多 25 萬)

    def acfg_sync(self):
        """自動當沖夥伴版 §6.1(2026-11-15 使用者「夥伴版 只修改 增加夥伴所有的接口 設定 畫面 不能修改程式的核心邏輯」):
           夥伴中繼(FAMILY)每分鐘讀雲端 ?acfg=get&a=<自己> —— 主導$ 在 App「夥設」改的設定 —— 套到 trader 現有的設定值(不改 trader 程式):
             judges → trader.judges(全關 = {"-"},沒有判別式符合 → 不進場);enabled → trader.auto(關 = 不自動進場;不動 trader.enabled,已在場上的部位照常停損停利、13:20 平倉、手動平倉照常)
             max_amount(≤ 鐵律 500 萬)、cap(≤ 25 萬;固定張數模式 cap=0 不動;鐵律 ⑤ 沒解鎖前不影響張數)、max_loss、kill_loss
           只影響之後的新進場;雲端沒寫的欄位 = 用 bridge.env 的原值;讀不到雲端 = 維持現狀。主帳號不做。"""
        if not FAMILY or not self.trader: return
        try:
            r = urllib.request.urlopen(urllib.request.Request(CFG["WORKER_URL"].rstrip("/") + "/?acfg=get&a=" + urllib.parse.quote(ACCT) + f"&_={int(time.time())}",
                                       headers={"X-Push-Key": CFG.get("PUSH_KEY", ""), "User-Agent": "yuanta-bridge/1"}), timeout=8)
            c = json.loads(r.read().decode("utf-8"))
            if not c.get("ok"): return
        except Exception:
            return
        _tw = time.gmtime(time.time() + 8 * 3600); self._acfg_seen = True   # 讀到雲端了:啟動那次算過;08:45 以後讀到的算今天那次
        if time.strftime("%H:%M", _tw) >= "08:45": self._acfg_day = time.strftime("%Y-%m-%d", _tw)
        t = self.trader
        if not hasattr(self, "_acfg_base"):   # 第一次:記下 bridge.env 的原值(雲端拿掉某欄時回到原值)
            self._acfg_base = {"judges": set(t.judges), "auto": t.auto, "max_amount": t.max_amount, "cap": t.cap, "max_loss": t.max_loss, "kill_loss": t.kill_loss, "lot_mode": getattr(t, "lot_mode", 0)}
        b = self._acfg_base; want = dict(b)
        j = c.get("judges")
        if isinstance(j, dict) and j:
            on = {k for k in JUDGES_ALL if j.get(k, k in b["judges"]) is True}
            want["judges"] = on or {"-"}
        if isinstance(c.get("enabled"), bool): want["auto"] = b["auto"] and c["enabled"]
        def num(k, lo, hi):
            try:
                v = float(c[k]); return v if lo <= v <= hi else None
            except Exception: return None
        v = num("max_amount", 1000000, IRON_AMOUNT)   # 2026-11-17 使用者「要」:夥伴範圍 總額度 100~500 萬、停止 0.5~3 萬、全平 1~5 萬(和夥伴加入畫面、雲端同一套)
        if v: want["max_amount"] = v
        v = num("cap", 100000, IRON_CAP_MAX)   # 2026-11-17 使用者「多空是10-25」:夥伴多空上限 10~25 萬
        if v and b["cap"] > 0: want["cap"] = int(v)
        for k, lo, hi in (("max_loss", 5000, 30000), ("kill_loss", 10000, 50000)):
            v = num(k, lo, hi)
            if v: want[k] = v
        if c.get("lot_mode") in (1, 2, 3): want["lot_mode"] = int(c["lot_mode"])   # 2026-11-17 進場張數方式(1 能買幾張 / 2 只買 1 張 / 3 分級照比例縮;trader.lots_for)
        chg = []
        with t.lock:
            for k, v in want.items():
                if getattr(t, k, None) != v: chg.append(f"{k} {getattr(t, k)} → {v}"); setattr(t, k, v)
        if chg: log(f"【{ACCT}】套用主導$ 雲端設定(之後的新進場才生效):" + ";".join(str(x) for x in chg))
        self._acfg_at = c.get("at") or 0   # 2026-11-17 回報「套用的是雲端哪一次設定」(夥伴加入打勾用)

    def resume_positions(self, day):
        """2026-10-22 使用者:「加 啟動時接回部位」:查元大今天的委託成交回報 + 有效的二擇一條件單,交給 trader 接回程式開的未平部位
           2026-11-21 使用者「全改」(檢查 R9):回傳 True = 這件事做完了(接回成功 / 不用接回);False = 查不到成交回報,主迴圈每分鐘再試到成功
           (原本查不到也當作接回過 → 盤中重開那天程式的部位整天沒人管,對帳還可能把程式自己的部位當成「晚到的成交」平掉)"""
        if not self.trader or not self.trader.enabled:
            return True
        try:
            r = self.query_today()
        except Exception as e:
            log("接回:查詢失敗(下一分鐘再試)", e); return False
        if not r:
            n = getattr(self, "_resume_fail", 0) + 1; self._resume_fail = n
            log(f"!! 接回:查不到今天的成交回報(第 {n} 次),一分鐘後再試")
            if n == 3: self.phone_alert("⚠ 接回部位查不到元大成交回報", "中繼每分鐘再試;接回前程式不對帳,有部位請留意")
            return False
        try:
            self.trader.resume(r["trades"], r["tags"], None if r.get("oco_failed") else r["oco"], fetch_signals(CFG["WORKER_URL"], day, CFG.get("PUSH_KEY", "")), r.get("working", []), r.get("types"))
            if "09:00" <= now_tw().strftime("%H:%M") < "13:25":   # 2026-11-11 22:44 使用者「全改」 11:盤中重開 → 接回的部位補今天的 1 分 K(移動停損利不用等 10 分鐘)
                cs = list(self.trader.positions)
                if cs: threading.Thread(target=self.seed_ka, args=(cs, day), daemon=True).start()
        except Exception as e:   # trader.resume 做到一半出錯:不重做(可能已經接回一部分,重做會重複接),推手機
            log("接回失敗", e); self.phone_alert("⚠ 接回部位出錯", f"{e!r}"[:150] + ";請在投資先生確認部位")
        self._resume_fail = 0
        return True

    def seed_ka(self, codes, day):
        for c in codes:
            try:
                m = self.mkt_of_code(c); rows = self.kline_query(c, m, day)
                if rows: self.trader.seed_mc(c, rows)
            except Exception as e: log(f"補 {c} 1 分 K 失敗", e)

    def recheck_positions(self, at):
        """2026-10-22 使用者:「13:23 分要再確認一次 還有沒有未沖銷的股票」→「13:22 / 13:23 / 13:24 確認三次 以保安全」"""
        if not self.trader or not self.trader.enabled:
            return True
        try:
            gone = set(); r = None; px = self.__dict__.get("_phx") or {}
            qw = max(1.0, float(CFG.get("CLOSE_QUERY_WAIT", "8") or 8))
            if (self.trader.prehang and self.trader.prehang_deadline not in ("", "0") and at >= self.trader.prehang_deadline) or (px.get("at") == at and not px.get("done")):
                # 2026-11-21 使用者「改 A」(A1):取消手動預掛一次只做一輪,還沒確認 → 回 False,主迴圈下一圈再來(不卡主迴圈)
                with self.qcap(qw): st_, gone, r = self.prehang_expire(at)
                gone = gone or set()
                if st_ is None: return False
            if r is None:
                with self.qcap(qw): r = self.query_today(with_oco=False)   # 2026-11-21「改 A」(A1):複查查詢也有時間上限
            if not r:
                # 2026-11-22 使用者「全部做」(U4):原本查不到就 return(None)→ 主迴圈當成做完、這分鐘的複查就沒了。
                #   改:回 "fail",主迴圈在同一分鐘內每 3 秒重查;過了這分鐘還是查不到才算這次複查失敗、推手機(一次)
                log(f"!! {at} 複查:查不到成交回報")
                if now_tw().strftime("%H:%M") > at:
                    self.trader.alert(f"⚠ {at} 複查失敗", "查不到元大成交回報,請在投資先生確認有沒有未沖銷的股票")
                return "fail"
            safe, amb = self.guard_trades(r["trades"], at)   # 2026-11-21(檢查 R4)
            with self.skip_codes(amb):
                self.trader.recheck(safe, [w for w in r["working"] if w.get("no") not in gone], at)   # 剛取消的手動預掛不算掛著(取消回報可能還沒到)
        except Exception as e:
            log("複查失敗", e)
        return True

    def sig_waiter(self, day):
        """2026-11-12 21:00 使用者「都改」(Cloudflare 1 推送):背景執行緒對 Worker 長輪詢 ?log=wait(最多 25 秒,雲端一有新通知 / 手機指令就回)→ 叫主迴圈馬上讀。
           回傳 True = 長輪詢正常(主迴圈改成有通知才讀 + 每 15 秒保險讀一次);False = 不通(舊版 Worker / 斷網)→ 主迴圈照舊每秒讀"""
        if not hasattr(self, "_sig_kick"):
            self._sig_kick = threading.Event(); self._lp_ok = 0.0
            def run():
                v = ""; bad = 0
                while True:
                    _n = now_tw()
                    if _n.weekday() >= 5 or not ("08:30" <= _n.strftime("%H:%M") <= "13:40"): time.sleep(30); continue   # 盤外不問(省額度)
                    try:
                        dd = _n.strftime("%Y-%m-%d")
                        r = urllib.request.urlopen(urllib.request.Request(f"{CFG['WORKER_URL'].rstrip('/')}/?log=wait&d={dd}&v={urllib.parse.quote(v)}", headers={"X-Push-Key": CFG.get("PUSH_KEY", "")}), timeout=40)
                        js = json.loads(r.read().decode("utf-8")); nv = str(js.get("v") or "")
                        if not nv: raise ValueError("沒有 v")
                        if v and nv != v: self._sig_kick.set()
                        v = nv; self._lp_ok = time.time(); bad = 0
                    except Exception as e:
                        bad += 1
                        if bad in (1, 10) or bad % 60 == 0: log(f"[推送] 長輪詢不通(第 {bad} 次),先照舊每秒讀:{e!r}"[:200])
                        time.sleep(min(30, 2 * bad))
            threading.Thread(target=run, daemon=True).start()
        return time.time() - self._lp_ok < 45

    def pc_backup(self, day):
        """2026-11-12 20:22 使用者「改」(升級 Workers Paid 後物盡其用 1):每分鐘一次 POST ?pcbk=put —— 今天的大盤寬度、引擎實際監控的股票、
           中繼部位(含總停損 / 移動停損利的起點)、狀態檔;回測 / 檢討 / 電腦重灌都用同一份。失敗只記紀錄,不影響交易"""
        if FAMILY: return self.fam_backup(day)   # 2026-11-15 家人帳號:分人存(部位 / 成交 / 輸贏),不碰主帳號那份
        try:
            ed = Path(os.environ.get("ENGINE_DIR", HERE / "engine_data")); items = {}
            try: items["breadth"] = (json.loads((ed / "breadth.json").read_text(encoding="utf-8")) or {}).get(day) or {}
            except Exception: pass
            try: items["codes"] = json.loads((ed / f"codes_{day}.json").read_text(encoding="utf-8"))
            except Exception: pass
            try: items["engine"] = json.loads((ed / f"engine_state_{day}.json").read_text(encoding="utf-8"))
            except Exception: pass
            try: items["state"] = json.loads(Path(CFG.get("TRADE_STATE_FILE", str(HERE / "trade_state.json"))).read_text(encoding="utf-8"))
            except Exception: pass
            try: items["blocked"] = [json.loads(x) for x in (ed / f"blocked_{day}.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()][-400:]   # 2026-11-12 20:29 被擋 / 等待中的訊號
            except Exception: pass
            try: items["signals"] = [json.loads(x) for x in (ed / f"live_{day}.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()][-1500:]   # 引擎發出的通知(2026-11-22 對帳用:300 → 1500 則,一天全部)
            except Exception: pass
            try: items["verdicts"] = [json.loads(x) for x in (ed / f"verdict_{day}.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()][-1500:]   # 2026-11-22 每則進場訊號有沒有進場(實盤 vs 總表對帳,tools/recon.py)
            except Exception: pass
            try: items["fills"] = (getattr(self, "_fills_cache", None) or (0, None))[1]   # 元大成交明細(手機明細同一份)
            except Exception: pass
            t = self.trader
            if t is not None:
                try:
                    with t.lock: items["pos"] = [{k: p.get(k) for k in ("code", "dir", "qty", "entry", "judge", "tp", "sl", "state", "t0", "typ")} for p in t.positions.values()]
                    items["pnl"] = {"pnl": round(getattr(t, "pnl", 0) or 0), "n": getattr(t, "n_trades", 0), "amount": round(getattr(t, "amount_today", 0) or 0)}
                    items["lat"] = t.lat_rows()   # 2026-11-13 14:30 使用者「開始」(Cloudflare 第二輪 1):每個進場訊號 引擎→中繼→送單→成交 的時間
                except Exception: pass
            req = urllib.request.Request(CFG["WORKER_URL"].rstrip("/") + "/?pcbk=put", data=json.dumps({"d": day, "items": items}, ensure_ascii=False, default=str).encode(), method="POST",
                                         headers={"Content-Type": "application/json", "X-Push-Key": CFG.get("PUSH_KEY", "")})
            urllib.request.urlopen(req, timeout=15).read()
        except Exception as e:
            n = getattr(self, "_bk_fail", 0) + 1; self._bk_fail = n
            if n == 1 or n % 30 == 0: log(f"雲端備份失敗(第 {n} 次)", e)

    def phone_alert(self, title, body="", kind="pc"):
        """發一則提醒到手機的「今日監控紀錄」(Worker ?log=add)
           2026-10-31 kind 原本是 warn:手機只把 warn 當股票的「注意 / 反彈」狀態、不跳通知 → 電腦的提醒手機都沒跳出來。改成 pc(一般提醒,會跳通知);
           虧損分級 lv1~lv5(手機一級比一級強的顏色、聲音、震動)
           2026-11-21 使用者「全改」(檢查 R2):改成排進背景執行緒依序送(原本在主迴圈 / 13:20 平倉路上同步送,Worker 慢就卡 10 秒);時間照叫的那一刻"""
        import queue
        q = self.__dict__.get("_alert_q")
        if q is None:
            with LOG_LOCK:
                q = self.__dict__.get("_alert_q")
                if q is None:
                    q = self._alert_q = queue.Queue(maxsize=500)
                    def run():
                        while True:
                            a = q.get()
                            try: self._phone_alert_now(*a)
                            except Exception as e: log("發手機提醒失敗", e)
                    threading.Thread(target=run, daemon=True, name="phone_alert").start()
        try: q.put_nowait((title, body, kind, now_tw()))
        except Exception as e: log("手機提醒佇列滿了,這則沒送", title, e)

    def _phone_alert_now(self, title, body="", kind="pc", n=None):
        try:
            if FAMILY: title = f"【{ACCT}】{title}"   # 2026-11-15 家人帳號:提醒標題標名字
            n = n or now_tw(); it = {"d": n.strftime("%Y-%m-%d"), "t": n.strftime("%H:%M"), "title": title, "body": body + " · 元大中繼", "kind": kind or "pc", "key": "yt|" + title}
            req = urllib.request.Request(CFG["WORKER_URL"].rstrip("/") + "/?log=add", data=json.dumps(it).encode(), method="POST", headers={"Content-Type": "application/json", "X-Push-Key": CFG.get("PUSH_KEY", "")})   # 2026-10-29 鑰匙
            urllib.request.urlopen(req, timeout=10)
        except Exception as e:
            log("發手機提醒失敗", e)

    def elig_need(self):
        """2026-10-31 盤中要不要再複檢:今天還沒有複檢結果、可當沖名單還不是今天的、或有來源抓失敗 → 要"""
        try:
            import elig; r = elig.load_today()
        except Exception: return True
        return (not r) or bool(r.get("stale", True)) or bool(r.get("err"))

    def elig_run(self, why):
        """開盤前資格複檢(elig.py)另開執行緒跑,不卡住行情;13:30 後啟動不用做"""
        if now_tw().strftime("%H:%M") >= "13:30" or now_tw().weekday() >= 5: return
        def _go():
            try:
                import elig; r = elig.refresh(CFG, log=log)
                # 2026-10-31 使用者「增加一個開盤後檢查股票是否真能當沖的機制」:開盤後再抓官方名單,有新的不能做的股票 → 推手機(進場前 trader 也會照新結果擋)
                if why.startswith("盤中") and r:
                    nm = {"disposed": "處置", "attention": "注意", "nodt": "不可當沖", "noshort": "不可先賣"}
                    if r.get("new"):
                        self.phone_alert(f"⚠ 盤中複檢:{len(r['new'])} 檔今天不能做", "、".join(f"{c}({nm.get(k, k)})" for c, k in r["new"]) + " · 已停止自動進場這幾檔")
                    if r.get("stale") and why == "盤中 09:05":
                        self.phone_alert("⚠ 證交所 / 櫃買今天的可當沖名單還沒更新", f"名單日 {r.get('dt_date') or '抓不到'},程式會繼續重抓;元大拒單的股票也會自動停做")
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
        t.name_of = self.stock_name   # 2026-11-12 09:16 使用者「股票名稱怎麼變成1709」:名單外股票(留倉接回 / 手動)沒有名稱 → 查本機舊名單、證交所
        t.want_sub = self.want_sub
        for c, m in self.codes:
            t.mkt_of[c] = str(m)
        return t

    def stock_name(self, c):
        """2026-11-12 代號 → 名稱:今天名單 → 本機前幾天的名單 pool_*.json → 證交所 MIS(上市 / 上櫃各試一次);查到就記住,查不到也記住不重查"""
        c = str(c); n = (self.quotes.meta.get(c) or {}).get("n")
        if n and n != c: return n
        cache = self.__dict__.setdefault("_names", {})
        if c in cache: return cache[c] or c
        nm = ""
        try:
            for f in sorted(HERE.glob("pool_*.json"), reverse=True)[:30]:
                try:
                    j = json.loads(f.read_text(encoding="utf-8")); pool = j.get("pool", j) if isinstance(j, dict) else j
                    nm = next((str(s.get("name") or "") for s in pool if str(s.get("code")) == c and s.get("name")), "")
                except Exception: nm = ""
                if nm: break
        except Exception: pass
        if not nm:
            for ex in ("tse", "otc"):
                try:
                    u = f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch={ex}_{c}.tw&json=1&delay=0"
                    r = json.loads(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=4).read().decode("utf-8"))
                    nm = str(((r.get("msgArray") or [{}])[0]).get("n") or "")
                except Exception: nm = ""
                if nm: break
        cache[c] = nm
        if nm:
            m = self.quotes.meta.get(c)
            if m is not None and not m.get("n"): m["n"] = nm
            log(f"名稱 {c} → {nm}")
        return nm or c

    # ── 推到 Worker ──
    # 2026-11-21 使用者「全改」(檢查 R2 / R3 / R6):
    #   R2 原本本機 CSV 是在 push 裡面寫、flush 遇到第一分鐘推不上去就整輪停 → Worker 掛掉時本機 1 分 K 也停寫(引擎雲端讀不到時就是改讀這份),
    #      而且主迴圈每一輪最多卡 3 次 × 15 秒。改成:每一分鐘收完就先寫本機(和推雲端無關);推雲端放到背景執行緒排隊(每次一筆、逾時 PUSH_TIMEOUT 秒),
    #      推不上去的分鐘留在佇列,退避後從最舊的再推,主迴圈完全不等
    #   R3 Windows 上引擎剛好開著 bars_yuanta/<日>.csv 時 os.replace 會 PermissionError → 原本一路丟到主迴圈變成斷線重登;改成短暫重試幾次,不行就這次不寫、下一輪再寫,絕不往外丟
    #   R6 原本 flush 先 take、推完(很慢)才標 sent,推的期間晚到的成交不會標「要重送」→ 永遠不重送;改成 take 之前就先標(已取走),之後晚到的成交都會標 dirty 重寫本機 + 重推
    def push(self, day, minute, bars, timeout=None):
        """推一分鐘到 Worker(背景執行緒呼叫,只試一次):True 成功 / False 失敗(之後再推)/ "auth" 金鑰被拒(401 / 403,隔久一點再試)"""
        if not bars or FAMILY or not CFG.get("PUSH_KEY"):      # 2026-11-15 家人帳號:1 分 K 由主帳號推
            return True
        body = json.dumps({"d": day, "m": minute, "bars": bars}).encode()
        req = urllib.request.Request(CFG["WORKER_URL"].rstrip("/") + "/?ybars=push", data=body, method="POST",
                                     headers={"Content-Type": "application/json", "X-Push-Key": CFG["PUSH_KEY"]})
        nf = getattr(self, "_push_fail_n", 0); loud = nf < 3 or nf % 30 == 0   # 一直推不上去時紀錄不要每次都寫
        try:
            r = json.loads(urllib.request.urlopen(req, timeout=timeout or float(CFG.get("PUSH_TIMEOUT", "8") or 8)).read().decode())
            if r.get("ok"):
                self._push_fail_n = 0; return True
            self._push_fail_n = nf + 1
            if loud: log("推送被拒", r)
            return False
        except urllib.error.HTTPError as e:
            # 2026-10-27 12:40 被拒時把 Worker 回的原因寫進紀錄:{"error":"金鑰不對"} = bridge.env 的 PUSH_KEY 和 Worker 的 YT_PUSH_KEY 不一樣;
            #   HTML / error code 1010 = Cloudflare 擋連線身分。403 / 401 馬上重試也沒用
            try: why = e.read().decode("utf-8", "replace")[:200].replace("\n", " ")
            except Exception: why = ""
            self._push_fail_n = nf + 1
            if loud: log(f"推送失敗 {minute} HTTP {e.code}:{why}(第 {nf + 1} 次)")
            # 2026-11-21 使用者「改 A」(A7):其他 4xx(429 太多次、408 逾時除外)= 這分鐘的資料 Worker 一直不收 → "perm"(試幾次就丟掉,不擋後面的分鐘)
            if e.code in (401, 403): return "auth"
            return "perm" if 400 <= e.code < 500 and e.code not in (408, 429) else False
        except Exception as e:
            self._push_fail_n = nf + 1
            if loud: log(f"推送失敗 {minute}(第 {nf + 1} 次)", e)
            return False

    def _pq(self):
        st = self.__dict__.get("_pq_st")
        if st is None:
            st = self._pq_st = {"lock": threading.Lock(), "pend": {}, "ev": threading.Event(), "ok": set(), "fail": 0, "next": 0.0, "busy": None}
            threading.Thread(target=self._push_loop, args=(st,), daemon=True, name="ybars_push").start()
        return st

    def push_async(self, day, minute, bars):
        """排進背景推送佇列(同一分鐘有新的就換成新的那份)"""
        if not bars or FAMILY or not CFG.get("PUSH_KEY"): return
        st = self._pq()
        with st["lock"]:
            st["pend"][(day, minute)] = bars
            mx = max(10, int(CFG.get("PUSH_QMAX", "600") or 600))   # 2026-11-21 使用者「改 A」(A7):佇列有上限,超過丟最舊的(本機 CSV 有存)
            if len(st["pend"]) > mx:
                old = sorted(st["pend"])[:len(st["pend"]) - mx]
                for k_ in old: st["pend"].pop(k_, None)
                log(f"!! 推送佇列超過 {mx} 分鐘 → 丟掉最舊的 {len(old)} 分鐘({old[0][0]} {old[0][1]} 起;本機 CSV 有存)")
        st["ev"].set()

    def _push_loop(self, st):
        """背景推送:從最舊的分鐘推起;失敗就放回佇列、退避(2、4…最多 30 秒;金鑰被拒 60 秒)再從最舊的推(和原本「一分鐘推不上去,後面先不推」一樣,不白打 Worker)
           2026-11-21 使用者「改 A」(A7):Worker 一直不收的那一分鐘(4xx,金鑰 / 太多次除外)試 PUSH_PERM_TRIES(預設 3)次就丟掉(記紀錄;本機 CSV 有),
             不再擋住後面所有分鐘;佇列最多 PUSH_QMAX(預設 600)分鐘,超過丟最舊的;前幾天的在 day_reset 清掉"""
        while True:
            st["ev"].wait(2); st["ev"].clear()
            while time.time() >= st["next"]:
                with st["lock"]:
                    if not st["pend"]: break
                    key = min(st["pend"]); bars = st["pend"].pop(key)
                st["busy"] = key
                try: r = self.push(key[0], key[1], bars)
                except Exception as e: r = False; log("推送例外", e)
                st["busy"] = None
                if r is True:
                    st["fail"] = 0; st["ok"].add(key); st.setdefault("perm", {}).pop(key, None); continue
                if r == "perm":
                    pm = st.setdefault("perm", {}); pm[key] = pm.get(key, 0) + 1
                    if pm[key] >= max(1, int(CFG.get("PUSH_PERM_TRIES", "3") or 3)):
                        pm.pop(key, None); st.setdefault("dropped", []).append(key)
                        log(f"!! {key[0]} {key[1]} 的 1 分 K Worker 一直不收(試了 {CFG.get('PUSH_PERM_TRIES', '3')} 次)→ 丟掉這分鐘,先推後面的(本機 CSV 有存)")
                        continue
                with st["lock"]: st["pend"].setdefault(key, bars)   # 失敗:放回(這段期間有更新的那份就用新的)
                st["fail"] += 1; st["next"] = time.time() + (60 if r == "auth" else 2 if r == "perm" else min(30, 2 * st["fail"]))
                break

    def push_drain(self, timeout=30):
        """收工:最多等 timeout 秒讓佇列推完"""
        st = self.__dict__.get("_pq_st")
        if not st: return True
        t_end = time.time() + timeout
        while time.time() < t_end:
            with st["lock"]: left = len(st["pend"])
            if not left and st["busy"] is None: return True
            st["ev"].set(); time.sleep(0.5)
        log(f"收工時還有 {len(st['pend'])} 分鐘 1 分 K 沒推上 Worker(本機 CSV 已存)")
        return False

    def save_local(self, day, minute, bars):
        """2026-10-24 第一階段:Cloudflare KV 一天只能寫 1,000 次(雲端 K 已用約 880),元大 1 分 K 先存在本機 CSV 比對,不推 Worker。
        BARS_DIR/日期.csv:code,date,time,open,high,low,close,volume(量 = 股,和 kline/ 一樣);每次整檔重寫(晚到的成交會改到舊分鐘)"""
        self.local_put(day, minute, bars)
        return self.local_write(day)

    def local_put(self, day, minute, bars):
        loc = getattr(self, "local", None)
        if loc is None or loc.get("_d") != day:
            loc = self.local = {"_d": day}
        loc[minute] = bars
        self._local_dirty = True

    def local_write(self, day):
        """整檔重寫本機 1 分 K;絕不丟例外(2026-11-21 檢查 R3)。寫不進去 → 回 False、留著 _local_dirty,下一輪 flush 再寫"""
        loc = getattr(self, "local", None)
        if not loc or loc.get("_d") != day: return True
        try:
            d = Path(CFG.get("BARS_DIR", HERE / "bars_yuanta")); d.mkdir(parents=True, exist_ok=True)
            tmp = d / f"{day}.csv.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write("code,date,time,open,high,low,close,volume\n")
                for m in sorted(k for k in loc if k != "_d"):
                    for c, (o, h, l, cl, v) in sorted(loc[m].items()):
                        f.write(f"{c},{day},{m},{o},{h},{l},{cl},{int(v)}\n")
            err = None
            for i in range(4):   # Windows:引擎正在讀這個檔 → os.replace 拒絕(PermissionError / WinError 5、32),等一下下再試(合計約 0.5 秒)
                try:
                    os.replace(tmp, d / f"{day}.csv"); self._local_dirty = False; return True
                except OSError as e:
                    err = e; time.sleep(0.05 * (i + 1))
            raise err
        except Exception as e:
            hm = now_tw().strftime("%H:%M")
            if getattr(self, "_local_err_hm", None) != hm:   # 一分鐘只記一次
                self._local_err_hm = hm; log("本機 1 分 K 這次寫不進去(引擎正在讀?),下一輪再寫", e)
            return False

    def flush(self, day, final=False):
        """已經走完的分鐘(現在分鐘之前;收盤後 final=True 全部):先標「已取走」→ 取資料 → 寫本機 → 排進背景推送;晚到的成交(dirty)整分鐘重寫 + 重推"""
        cur = now_tw().strftime("%H:%M")
        out = []
        for m in self.book.minutes():
            if m in self.sent or (not final and m >= cur):
                continue
            self.sent.add(m)                       # R6:take 之前先標,之後晚到的成交 add_tick 會標 dirty
            out.append(m)
        for m in self.book.take_dirty():          # 晚到的成交 → 整分鐘重寫 / 重推
            if m in self.sent and m not in out: out.append(m)
        for m in out:
            bars = self.book.take(m)
            if not bars: continue
            self.local_put(day, m, bars)           # R2:本機每分鐘都寫,和推雲端成不成功無關(2026-10-25 有 PUSH_KEY 也照樣存本機一份)
            self.push_async(day, m, bars)
        if out or getattr(self, "_local_dirty", False):
            self.local_write(day)

    def write_verdicts(self, day):
        """2026-11-13 進場訊號的結果(有沒有進場)寫給電腦引擎,推雲端前標「未進場」(2026-11-21 抽出來:nap 裡處理的訊號也馬上寫,不等主迴圈)"""
        try:
            vs = self.trader.__dict__.pop("verdicts", None)
            if vs and not FAMILY:   # 2026-11-15 家人帳號沒進場不代表主帳號沒進場,不寫
                with open(Path(os.environ.get("ENGINE_DIR", HERE / "engine_data")) / f"verdict_{day}.jsonl", "a", encoding="utf-8") as f:
                    for v in vs: f.write(json.dumps(v, ensure_ascii=False) + "\n")
        except Exception as e: log("寫進場結果檔失敗", e)

    def nap(self, day, total=1.0):
        """2026-11-20 使用者「改1,4」:主迴圈每圈睡 1 秒 → 引擎的進場通知平均晚 0.5 秒才被讀到。
           改成睡的時候每 0.2 秒看 live_<日期>.jsonl 大小,變大了就馬上讀、交給 trader(主執行緒,和主迴圈同一條路);主迴圈其他每 N 秒的工作節奏不變"""
        t_end = time.time() + total; step = float(CFG.get("SIG_LOCAL_SEC", "0.2") or 0.2)
        f = Path(os.environ.get("ENGINE_DIR", HERE / "engine_data")) / f"live_{day}.jsonl"
        while True:
            left = t_end - time.time()
            if left <= 0: return
            time.sleep(min(step, left))
            try:
                t_ = self.trader
                if t_ is None or not (t_.enabled and getattr(t_, "primed", False)): continue
                st = getattr(self, "_loc", None)
                if not st or st.get("day") != day or not f.exists() or f.stat().st_size == st.get("off"): continue
                loc = self.local_signals(day)
                if loc: self.trader.step(loc); self.write_verdicts(day)
            except Exception as e:
                log("[交易] !! 本機通知快讀出錯(下一圈照常讀)", e)
                if time.time() - getattr(self, "_step_err", 0) > 300:   # 2026-11-21 使用者「改 3」:和主迴圈 trader.step 出錯一樣推手機(5 分鐘一次)
                    self._step_err = time.time()
                    try: self.phone_alert("⚠ 電腦交易程式出錯", f"{e!r}"[:150] + ";程式照樣繼續跑,請看電腦紀錄")
                    except Exception: pass

    def local_signals(self, day):
        """2026-10-30 讀電腦引擎寫的本機通知檔 engine_data/live_<日期>.jsonl 新增的行(開工時從檔尾開始,之前的當舊的;換日換檔)"""
        f = Path(os.environ.get("ENGINE_DIR", HERE / "engine_data")) / f"live_{day}.jsonl"
        st = getattr(self, "_loc", None)
        if not st or st["day"] != day:
            st = self._loc = {"day": day, "off": f.stat().st_size if f.exists() else 0, "buf": b""}
        try:
            if not f.exists(): return []
            sz = f.stat().st_size
            if sz < st["off"]: st["off"] = 0
            if sz == st["off"]: return []
            with open(f, "rb") as fh: fh.seek(st["off"]); raw = fh.read()
            # 2026-11-21 使用者「改 3」:沒讀完的那一行留「位元組」,不先解碼(原本切在中文字中間會被 ignore 丟掉,那則 JSON 解析失敗就漏了)
            st["off"] += len(raw); lines = (st["buf"] + raw).split(b"\n"); st["buf"] = lines.pop()
            out = []
            for ln in lines:
                try: out.append(json.loads(ln.decode("utf-8")))
                except Exception as e:
                    if ln.strip(): log("本機通知有一行讀不懂(雲端那份會補)", repr(e)[:80], ln[:120])
            if out: log(f"[公式] 本機通知 {len(out)} 則:" + " / ".join(x.get("title", "") for x in out))
            return out
        except Exception as e:
            log("本機通知檔讀取失敗", e); return []

    def sig_inflight(self):
        th = (self.__dict__.get("_sf") or {}).get("th")
        return th is not None

    def signals_fetch(self, day, due=True):
        """2026-11-21 使用者「全改」(檢查 R2):雲端通知(fetch_signals,逾時 10 秒)改在背景抓;主迴圈最多等 SIG_WAIT_SEC(預設 1.5)秒,
           還沒回來就這一輪先不用、下一輪拿(不會漏:回來的結果留著給下一輪);同一時間只抓一個"""
        st = self.__dict__.setdefault("_sf", {"th": None})
        th = st["th"]
        if th is None:
            if not due: return None
            box = {}
            def run():
                try: box["r"] = fetch_signals(CFG["WORKER_URL"], day, CFG.get("PUSH_KEY", ""))
                except Exception as e: box["e"] = e
            th = threading.Thread(target=run, daemon=True, name="sig_fetch"); th.box = box; th.start(); st["th"] = th
        th.join(float(CFG.get("SIG_WAIT_SEC", "1.5") or 1.5) if due else 0)
        if th.is_alive(): return None
        st["th"] = None
        if "e" in th.box: log("讀雲端通知失敗", th.box["e"])
        self._sf_bad = 0 if th.box.get("r") is not None else getattr(self, "_sf_bad", 0) + 1   # 2026-11-21「改 A」(A9):連續讀不到幾次
        return th.box.get("r")

    def prime_local_maybe(self, day):
        """2026-11-21 使用者「改 A」(A9):開工後 PRIME_LOCAL_SEC(預設 150)秒還讀不到雲端通知,或已經連續 PRIME_LOCAL_FAILS(預設 5)次讀不到(雲端明顯掛了)
           → 用本機通知檔 live_<日期>.jsonl 到目前為止的全部 prime(都當成舊的),之後電腦引擎寫的本機訊號照常交易"""
        t = self.trader
        if t is None or getattr(t, "primed", False) or not hasattr(t, "prime_local"): return
        waited = time.time() - getattr(self, "_prime_t0", time.time()); bad = getattr(self, "_sf_bad", 0)
        if waited < float(CFG.get("PRIME_LOCAL_SEC", "150") or 150) and bad < int(CFG.get("PRIME_LOCAL_FAILS", "5") or 5): return
        f = Path(os.environ.get("ENGINE_DIR", HERE / "engine_data")) / f"live_{day}.jsonl"
        items = []
        try:
            if f.exists():
                for ln in f.read_bytes().split(b"\n"):
                    try: items.append(json.loads(ln.decode("utf-8")))
                    except Exception: pass
        except Exception as e: log("[交易] 讀本機通知檔(prime 用)失敗", e)
        if t.prime_local(items, f"已等 {waited:.0f} 秒、連續 {bad} 次讀不到"):
            self.phone_alert("⚠ 雲端通知讀不到,改用電腦本機訊號", f"已等 {waited:.0f} 秒;電腦引擎的訊號照常交易,手機通知 / 指令要等雲端恢復")

    def day_reset(self):
        """2026-11-21 使用者「全改」(檢查 R5 / R10 / R1):中繼可能好幾天不重開,掛在 Bridge 物件上的「當天」資料換日要清掉:
           R5 台指期元大逐筆 1 分 K(fut_bars / fut_sn / fut_n)、期交所那份(fut_tf)原本一直留著 → 隔天 08:45 的 K 會混到前一天同時間的那根、
              逐筆序號隔天從 1 開始會被當成重複丟掉;R10 引擎重開次數(一天最多 5 次)原本永不歸零;R1 今天停用的查詢功能、登入退避次數"""
        self.fut_bars, self.fut_sn, self.fut_n, self.fut_tf = {}, {}, {}, {}
        for k in ("_fut_hm", "_fut_pushed", "_fut_src", "_tf_ok", "_tf_bad"): self.__dict__.pop(k, None)
        self.eng_restarts = 0; self._eng_lock_wait = 0
        self.feat_off = {}; self.login_soft_n = 0; self.login_soft = None; self._relogin_at = 0.0
        self.login_unk_n = 0   # 2026-11-21 使用者「改 A」(A5):看不懂的登入回應次數每天重來
        self._ca = None; self._phx = None; self._ca_exc = 0   # 2026-11-21「改 A」(A1):13:20 平倉 / 預掛取消的分段狀態每天重來
        # 2026-11-21 使用者「改 A」(A7):前幾天還沒推上 Worker 的 1 分 K 清掉(本機 CSV 都有),不然一直留在記憶體、擋住今天的
        st = self.__dict__.get("_pq_st")
        if st:
            today = now_tw().strftime("%Y-%m-%d")
            with st["lock"]:
                old = [k_ for k_ in st["pend"] if k_[0] != today]
                for k_ in old: st["pend"].pop(k_, None)
                st["ok"] = {k_ for k_ in st["ok"] if k_[0] == today}
                st["perm"] = {}
            if old: log(f"換日:丟掉前幾天還沒推上 Worker 的 1 分 K {len(old)} 分鐘(本機 CSV 有存)")
        self._resume_fail = 0; self._no_tick_day = False; self.__dict__.pop("_conn_at", None)

    def engine_stop_old(self):
        old = getattr(self, "engine", None)
        if old is None: return
        try:
            if old.poll() is None:
                log(f"[公式] 上一個 engine.py(pid {old.pid})還在跑 → 先關掉"); old.terminate()
                try: old.wait(timeout=10)
                except Exception:
                    try: old.kill(); old.wait(timeout=5)
                    except Exception: pass
        except Exception as e: log("[公式] 關舊引擎失敗", e)

    def engine_watch(self):
        """2026-10-30 引擎看門狗:盤中引擎行程結束了(當掉 / 被關)→ 重新開(一天最多 5 次)。
           2026-11-21 使用者「全改」(檢查 R10):結束碼 2 = engine.lock 被別的引擎拿著(舊的還沒放開)→ 不算重開次數,2 分鐘後再試;不然一直被鎖會把 5 次額度用光"""
        eng = getattr(self, "engine", None)
        if eng is None or eng.poll() is None: return
        import subprocess
        rc = eng.returncode
        if rc == 2:
            if time.time() < getattr(self, "_eng_lock_wait", 0): return
            self._eng_lock_wait = time.time() + 120
            log("!! [公式] engine.py 拿不到 engine.lock(另一個引擎還在跑?)→ 不算重開次數,2 分鐘後再試")
            try: self.engine = subprocess.Popen([sys.executable, str(HERE / "engine.py")], cwd=str(HERE))
            except Exception as e: log("[公式] engine.py 重新啟動失敗", e)
            return
        self.eng_restarts = getattr(self, "eng_restarts", 0) + 1
        if self.eng_restarts <= 5:
            try:
                log(f"!! [公式] engine.py 不見了(結束碼 {rc}),第 {self.eng_restarts} 次重新啟動")
                self.engine = subprocess.Popen([sys.executable, str(HERE / "engine.py")], cwd=str(HERE))
            except Exception as e: log("[公式] engine.py 重新啟動失敗", e)
        elif self.eng_restarts == 6:
            log("!! [公式] engine.py 今天已重開 5 次,不再重開"); self.phone_alert("⚠ 電腦公式引擎一直結束", "今天已重開 5 次,不再重開;請看電腦紀錄")

    def run_day(self):
        day = now_tw().strftime("%Y-%m-%d")
        self.book = BarBook(vol_mult=int(CFG.get("VOL_MULT", "1000"))); self.sent = set(); self.n_ticks = 0
        self.day_reset()                 # 2026-11-21 使用者「全改」(檢查 R5 / R10 / R1):台指期 K、引擎重開次數、停用的查詢功能、登入退避次數每天重來
        self.quotes.q.clear()            # 新的一天:即時價從頭累計
        for m in self.quotes.meta.values(): m["y"] = None; m["yref"] = None   # 2026-10-28 平倉檢查 #10:昨收每天重抓(不在今天名單的股票不會留著前幾天的)
        self.codes = self.load_codes_safe()
        self.trader = self.make_trader()
        self.trader.prime(fetch_signals(CFG["WORKER_URL"], day, CFG.get("PUSH_KEY", "")))
        self._prime_t0 = time.time(); self._sf_bad = 0   # 2026-11-21 使用者「改 A」(A9):雲端讀不到時多久後改用本機通知 prime
        self._loc = None; self.local_signals(day)   # 2026-10-30 本機通知檔從現在的檔尾開始讀(之前的是舊的)
        self.engine_stop_old()   # 2026-11-21(檢查 R10):前一天的引擎還沒結束(收工時等 120 秒逾時那種)→ 先關掉,不然新的拿不到 engine.lock
        self.engine = None   # 2026-10-29 公式在電腦(使用者「先做1.2.3」第 2 項):ENGINE=1 → 另開一個 engine.py 行程,每分鐘用模擬器算判一二三推雲端;收工時關掉
        if CFG.get("ENGINE", "0") == "1" and FAMILY: log(f"[家人 {ACCT}] 只下單模式:不開公式引擎(訊號讀主帳號的雲端 / 本機通知)")
        if CFG.get("ENGINE", "0") == "1" and not FAMILY:
            try:
                import subprocess
                self.engine = subprocess.Popen([sys.executable, str(HERE / "engine.py")], cwd=str(HERE)); log(f"[公式] engine.py 已啟動(pid {self.engine.pid}),判一二三由電腦算、手機只收雲端通知")
            except Exception as e: log("[公式] engine.py 啟動失敗", e)
        fails = 0; k = 0; resumed = False; rechecked = set(); closed_all = False; resume_try = 0.0; rck_next = {}   # rck_next:2026-11-22(U4)複查查不到 → 下次重查的時間
        self.last_tick = time.time(); self.last_kick = 0.0; self.n_kick = 0; self.last_recon = time.time()
        # 2026-10-30 11:08(實際時間)使用者「該如何避免再次出現這問題」→「2道都做」第 1 道:電腦自己做開盤前資格複檢(elig.py),
        #   啟動時先做一次(08:45 前),08:30、08:44 再各做一次(證交所 / 櫃買早上才換今天的名單);結果存本機 + 推 Worker,手機、引擎、trader 都照它排除
        self.elig_done = set(); self.elig_run("啟動")
        # 2026-11-03 使用者「讓QR CODE 自動存進 電腦 KEY目錄裡面」:啟動時另開執行緒,把每把鑰匙的 QR Code 圖存進 keys 資料夾(已有的不重做)
        def _qr():
            try:
                import win_keys; win_keys.sync_qr(log=log)
            except Exception as e: log("[鑰匙] 存 QR Code 失敗", e)
        if not FAMILY: threading.Thread(target=_qr, daemon=True, name="keyqr").start()
        while now_tw().strftime("%H:%M") < "13:36":
            try:
                if self.login_fail:
                    raise LoginRejected(self.login_fail)
                hm_e = now_tw().strftime("%H:%M")
                if hm_e in ("08:30", "08:44") and hm_e not in self.elig_done: self.elig_done.add(hm_e); self.elig_run(hm_e)
                # 2026-10-31 使用者「增加一個開盤後檢查股票是否真能當沖的機制」→「改成需要才查」:09:01 照查(在判四最早進場前);
                #   09:05 之後只有今天的複檢還沒抓到當天的可當沖名單(stale)或有抓失敗才繼續查,早上正常就不再查(資格盤中不會變,多抓反而可能抓到殘缺名單誤擋)
                if hm_e not in self.elig_done and (hm_e == "09:01" or (hm_e in ("09:05", "09:15", "09:45", "10:30", "11:30", "12:30") and self.elig_need())):
                    self.elig_done.add(hm_e); self.elig_run("盤中 " + hm_e)
                # 2026-10-28 14:29 使用者「當沒有k 資料進來的時候就要馬上檢查連線 不是嗎」(10/28 12:07 起逐筆整個停掉、程式沒發現):
                #   盤中(09:01~13:24,開盤前試撮和 13:25 收盤集合競價本來就沒有逐筆)連續 TICK_WATCH 秒(預設 60)一筆逐筆都沒有 → 當作斷線,重新登入、重新訂閱;
                #   重連後還是沒有,每次間隔拉長(60 → 120 → 240 秒,最多 5 分鐘),避免一直登入被元大鎖;第一次和每第 3 次發手機提醒
                hm_w = now_tw().strftime("%H:%M")
                if "09:00" <= hm_w <= "13:36" and time.time() - getattr(self, '_bk_at', 0) >= 60:   # 2026-11-12 20:22 使用者「改」:每分鐘把電腦關鍵資料備份到雲端(背景,不卡主迴圈)
                    self._bk_at = time.time(); threading.Thread(target=self.pc_backup, args=(day,), daemon=True).start()
                # 2026-11-21 使用者「全改」(輕微項,休市日):連上 10 分鐘以上、09:10 了還一筆逐筆都沒有 → 多半是休市(颱風假、清單沒列到的休市日)或訂閱整個沒成功,
                #   今天不再「沒逐筆 → 斷線重登」(原本整天每幾分鐘重登一次,可能被元大鎖),推手機一次;之後真的有逐筆進來就恢復看門狗
                if self.n_ticks == 0 and hm_w >= "09:10" and not self._no_tick_day and time.time() - getattr(self, "_conn_at", time.time()) >= 600:
                    self._no_tick_day = True
                    log("!! 09:10 了今天還沒有任何逐筆成交(休市?訂閱沒成功?)→ 今天停用「沒逐筆就重登」")
                    self.phone_alert("⚠ 今天到 09:10 都沒有元大逐筆", "可能休市或訂閱沒成功;中繼不再自動重登,部位管理 / 13:20 平倉照常,請確認元大連線")
                elif self._no_tick_day and self.n_ticks > 0:
                    self._no_tick_day = False; log("逐筆開始進來了 → 恢復「沒逐筆就重登」看門狗")
                if self.api is not None and not self.disconnected.is_set() and "09:01" <= hm_w < "13:25" and not self._no_tick_day:
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
                    if time.time() < self._relogin_at:                       # 2026-11-21(檢查 R1):元大暫時不給登入 → 退避時間還沒到,先不登入(也不讀訊號:沒登入送不出單)
                        time.sleep(1); continue
                    self.close(); self.connect(); self.subscribe(self.codes); self.fut_sub(); fails = 0; self.last_tick = time.time()   # 重連後重新計時
                    self.__dict__.setdefault("_conn_at", time.time())
                    self.last_recon = time.time() - 50                       # 重連後 10 秒就對帳一次
                # 今天第一次連上:接回程式開的未平部位(斷線重連不用,部位一直在記憶體裡)。2026-11-21 使用者「全改」(檢查 R9):查不到成交回報不算接回過,每分鐘再試到成功
                if not resumed and self.api is not None and self.logged_in.is_set() and time.time() - resume_try >= 60:
                    resume_try = time.time(); resumed = self.resume_positions(day)
                self.nap(day); k += 1   # 2026-11-20 使用者「改1,4」:原 time.sleep(1);等的這 1 秒裡每 0.2 秒看一次電腦引擎的本機通知檔,有新的馬上交給 trader
                try: self.fut_poll()   # ⑧ 備援 2:電腦直接問期交所台指期(每 10 秒,08:44~13:46)
                except Exception: pass
                try: self.ix_poll()   # 2026-11-18 21:05 加權指數直接問證交所(每 5 秒,09:00~13:31)
                except Exception: pass
                if self.pending_sub:                                     # 手機強制進場的股票補訂閱(在主執行緒呼叫元大元件)
                    subs, self.pending_sub = self.pending_sub, []
                    try: self.subscribe(subs)
                    except Exception as e: log("補訂閱失敗", e)
                _lp = self.sig_waiter(day)   # 2026-11-12 21:00 使用者「都改」(Cloudflare 1):雲端有新通知才讀(長輪詢,手機下單 0.x 秒到);長輪詢不通就照舊每 SIG_POLL_SEC 秒讀
                _due = (k % max(1, int(CFG.get("SIG_POLL_SEC", "1") or 1)) == 0) if not _lp else (self._sig_kick.is_set() or k % 15 == 0)
                if _lp and _due: self._sig_kick.clear()
                sig = self.signals_fetch(day, due=_due) if (self.trader.enabled and (_due or self.sig_inflight())) else None   # 2026-11-21(檢查 R2):改背景抓、主迴圈最多等 SIG_WAIT_SEC 秒; 手機通知每 SIG_POLL_SEC 秒看一次(2026-11-12 20:22 使用者「改」:升級 Workers Paid 後 3 → 1 秒,手機強制進場 / 強平 1 秒內收到;約 +12,000 次 / 天)
                if self.trader.enabled and not getattr(self.trader, "primed", False): self.prime_local_maybe(day)   # 2026-11-21 使用者「改 A」(A9)
                if sig is not None and getattr(self.trader, "primed_local", None) and not getattr(self.trader, "cloud_merged", True):
                    with self.trader.lock:   # 2026-11-21「改 A」(A9):本機 prime 後雲端第一次讀到 → 本機 prime 那分鐘以前的進場通知不補
                        self.trader.cloud_merged = True; sig = self.trader.merge_cloud(sig)
                # 2026-10-30 01:32 使用者「先做A」:電腦引擎寫在本機的通知每秒讀一次,不用等 Worker / 3 秒輪詢(訊號算出來 1 秒內送單);還沒 prime 好不讀(避免舊訊號)
                if self.trader.enabled and getattr(self.trader, "primed", False):
                    loc = self.local_signals(day)
                    if loc: sig = loc + (sig or []) if sig is not None else loc
                try: self.trader.step(sig)   # 2026-11-12 00:24 使用者「還有什麼漏洞」:trader 出錯不能讓整個主迴圈斷線重連(那段時間所有部位都沒人盯)
                except Exception as e:
                    log("[交易] !! trader.step 出錯", e)
                    if time.time() - getattr(self, "_step_err", 0) > 300: self._step_err = time.time(); self.phone_alert("⚠ 電腦交易程式出錯", f"{e!r}"[:150] + ";程式照樣繼續跑,請看電腦紀錄")
                self.write_verdicts(day)
                _tw = time.gmtime(time.time() + 8 * 3600)   # 2026-11-11 22:35 使用者「改 1 2 3 4 5」 4:盤中重開 / 開盤前沒讀到 → 盤中也每分鐘讀,讀到為止(主導機盤中禁改,讀到一次就好)
                if k % 60 == 1 and (time.strftime("%H:%M", _tw) < "09:00" or getattr(self, "cap_ok_day", "") != time.strftime("%Y-%m-%d", _tw)): threading.Thread(target=self.cap_sync, daemon=True).start()   # 2026-11-07 19:32「開盤中不用追蹤主導機設定」09:03 → 09:00 前;2026-11-02 跟 5861 主導機的多空上限:開盤前每分鐘讀,盤中設定禁改 → 09:03 後不讀
                # 自動當沖夥伴版 §6.1:主導$ 的夥伴設定 —— 2026-11-17 使用者「盤中修改設定 電腦與雲端不可以接受 … 只需要在盤前8.45檢查一次 即可」→「改」:
                #   原本每分鐘讀 → 改成 啟動時讀一次(新夥伴第一次才有設定)+ 每天 08:45 讀一次;盤中設定不會變(雲端盤中也不收修改)
                if FAMILY and k % 60 == 31 and (not getattr(self, "_acfg_seen", False) or (time.strftime("%H:%M", _tw) >= "08:45" and getattr(self, "_acfg_day", "") != time.strftime("%Y-%m-%d", _tw))):
                    threading.Thread(target=self.acfg_sync, daemon=True).start()
                # 2026-10-29 額度(使用者「我現在要做的是2」):沒開自動交易時沒有人查今天的委託成交 → 每 QUOTA_SEC(60)秒查一次,查到就由 quota_from_report 推上雲端
                if CFG.get("QUOTA_PUSH", "1") == "1" and CFG.get("PUSH_KEY") and k % int(CFG.get("QUOTA_SEC", "60")) == 0 and self.logged_in.is_set():
                    try: self.query_today(with_oco=False)
                    except Exception as e: log("額度查詢失敗", e)
                if self.trader.enabled and self.trader.need_verify():   # 2026-10-28 漲停 / 跌停單 30 秒沒成交完 → 查元大:還掛著就等、沒有就重送
                    r = self.query_today(with_oco=False)
                    if r: self.trader.verify(r["working"], self.guard_trades(r["trades"], "30 秒檢查")[0], r.get("tags"))   # 平倉檢查 #2:委託書號沒回來也能用自訂編號對;2026-11-21(檢查 R4)可疑重複照較少張數算
                    else: log("[交易] 30 秒檢查:查不到元大委託回報,下一輪再查")
                hm_now = now_tw().strftime("%H:%M")
                # 2026-10-28 模擬測試:斷線 / 重開漏掉回報 → 每分鐘(重連後馬上)跟元大對一次帳,程式不知道的部位接回來 / 平掉
                # 2026-11-21 使用者「全改」(檢查 R9):還沒接回成功前不對帳(對帳會把程式自己開的部位當成「晚到的成交」平掉);(檢查 R4)可疑重複成交判斷不了的檔跳過
                if self.trader.enabled and resumed and "09:01" <= hm_now < self.trader.force_exit and time.time() - self.last_recon > 60:
                    self.last_recon = time.time()
                    r = self.query_today(with_oco=False)
                    if r:
                        safe, amb = self.guard_trades(r["trades"], "對帳")
                        with self.skip_codes(amb):
                            self.trader.reconcile(safe, r["working"], r.get("tags"), r.get("types"))
                if self.trader.enabled and self.trader.cancel_all and not closed_all and hm_now >= self.trader.force_exit:
                    # 2026-11-21 使用者「改 A」(A1 / A6):分好幾圈做(每圈最多約 20 秒),做完才標 closed_all;沒登入 / 有檔送不出去 → 之後每圈(重連後)接著做
                    try: closed_all = self.close_all_step(self.trader.force_exit)
                    except Exception as e:
                        self._ca_exc = getattr(self, "_ca_exc", 0) + 1
                        log(f"13:20 取消 / 平倉出錯(第 {self._ca_exc} 次,下一圈再做;13:21 trader 也會自己強制平倉)", e)
                        if self._ca_exc >= 5:
                            closed_all = True; self.phone_alert(f"⚠ {self.trader.force_exit} 平倉流程一直出錯", f"{e!r}"[:150] + ";13:21 / 13:22 備援照常,請在投資先生確認")
                ca_ok = closed_all or not (self.trader.enabled and self.trader.cancel_all)   # 2026-11-21「改 A」(A1):13:20 流程做完才做 13:22 起的複查(和原本 close_all 做完才複查的順序一樣)
                for at in self.trader.recheck_times:                     # 13:22 / 13:23 / 13:24 各一次
                    if at not in rechecked and hm_now >= at and ca_ok:
                        if time.time() < rck_next.get(at, 0.0): break
                        rr_ = self.recheck_positions(at)
                        # 2026-11-22 使用者「全部做」(U4):查詢失敗("fail")原本也標成做完 → 這分鐘的複查不見了。
                        #   改:查成功才算做完;查不到 → 同一分鐘內 3 秒後重查,過了這分鐘(recheck_positions 已推手機)才放棄這一次
                        if rr_ == "fail":
                            if now_tw().strftime("%H:%M") > at: rechecked.add(at)
                            else: rck_next[at] = time.time() + 3
                        elif rr_ is not False: rechecked.add(at)   # False = 手動預掛還在確認取消,下一圈再做
                        break
                # 2026-11-07 12:55 使用者「能再加快進場訊息速度嗎」:原本每 10 秒才推一次 K → 一分鐘收完最慢要等 10 秒(平均 5 秒)才推上去,電腦算訊號也跟著晚。
                #   改成:換分鐘後第 1 秒就推剛走完的那根(晚到的成交照舊整分鐘重送);其他時間維持每 10 秒
                _nw = now_tw(); _hm = _nw.strftime("%H:%M")
                if k % 10 == 0 or (_nw.second >= 1 and getattr(self, "_flush_hm", "") != _hm):
                    self._flush_hm = _hm
                    self.flush(day)
                    if k % 10 == 0: self.flush_ticks(day)
                # 2026-11-10 17:26(實際時間)使用者「官方昨收 應該要有備用補齊通道」→「改」:元大清盤給的參考價(除權息已扣)每 30 秒寫 engine_data/ref_<日期>.json,電腦引擎昨收第一優先用它
                if k % 30 == 0 and not FAMILY:   # 2026-11-15 家人帳號:參考價由主帳號寫 / 推
                    try:
                        _rf = {c: m["yref"] for c, m in list(self.quotes.meta.items()) if m.get("yref")}
                        if _rf and _rf != getattr(self, "_ref_last", None):
                            self._ref_last = dict(_rf); _p = Path(os.environ.get("ENGINE_DIR", HERE / "engine_data")) / f"ref_{day}.json"
                            _p.parent.mkdir(parents=True, exist_ok=True); _t = _p.with_suffix(".tmp"); _t.write_text(json.dumps(_rf), encoding="utf-8"); os.replace(_t, _p)
                    except Exception as e: log("參考價寫檔失敗", e)
                    # 2026-11-12 22:06 使用者「那就改用元大參考價來算」:參考價也推一份到 Worker(?yref=push,變了才推、5 分鐘最多一次),14:30 存進 GitHub 給回測當昨收
                    try:
                        _rf2 = {c: m["yref"] for c, m in list(self.quotes.meta.items()) if m.get("yref")}
                        if _rf2 and _rf2 != getattr(self, "_ref_pushed", None) and time.time() - getattr(self, "_ref_push_t", 0) > 300 and CFG.get("PUSH_KEY") and CFG.get("WORKER_URL"):
                            self._ref_push_t = time.time()
                            def _ref_go(rf=_rf2):   # 2026-11-21(檢查 R2):背景推,不卡主迴圈
                                try:
                                    _rq = urllib.request.Request(CFG["WORKER_URL"].rstrip("/") + "/?yref=push", data=json.dumps({"d": day, "ref": rf}).encode(), method="POST",
                                                                 headers={"Content-Type": "application/json", "X-Push-Key": CFG["PUSH_KEY"], "User-Agent": "yuanta-bridge"})
                                    if json.loads(urllib.request.urlopen(_rq, timeout=15).read().decode()).get("ok"): self._ref_pushed = dict(rf)
                                except Exception as e: log("參考價推 Worker 失敗", e)
                            threading.Thread(target=_ref_go, daemon=True).start()
                    except Exception as e: log("參考價推 Worker 失敗", e)
                # 2026-10-30 引擎看門狗:盤中引擎行程結束了(當掉 / 被關)→ 重新開(一天最多 5 次);13:30 後不管
                if CFG.get("ENGINE", "0") == "1" and k % 30 == 0 and "08:45" <= hm_now < "13:30" and getattr(self, "engine", None) is not None:
                    self.engine_watch()
            except LoginRetry as e:
                # 2026-11-21 使用者「全改」(檢查 R1):暫時被拒(0016 請勿連續登入等)→ 退避 30 / 60 / 120 / 240 / 300 秒再登入,當天最多 LOGIN_RETRY_MAX 次;手機只提醒第一次
                self.close()
                if getattr(e, "unknown", False):
                    # 2026-11-21 使用者「改 A」(A5):沒看過的登入回應碼 → 最多再試 LOGIN_UNKNOWN_MAX(2)次、每次等 LOGIN_UNKNOWN_WAIT(300)秒;
                    #   還是不行 → 當天停 + 推手機(可能是帳密錯,連續試會被鎖)
                    self.login_unk_n = getattr(self, "login_unk_n", 0) + 1; n = self.login_unk_n
                    mx = int(CFG.get("LOGIN_UNKNOWN_MAX", "2") or 2)
                    if n > mx:
                        log(f"!! 元大登入回應看不懂:{e}(已再試 {n - 1} 次)→ 今天不再重試(避免帳號被鎖)")
                        self.phone_alert("⚠⚠ 元大登入一直失敗(回應看不懂)", f"{e} · 已再試 {n - 1} 次,今天不再重試;請檢查帳號 / 密碼後重啟服務,有部位請用元大 App 手動平倉")
                        break
                    w = max(60, int(CFG.get("LOGIN_UNKNOWN_WAIT", "300") or 300)); self._relogin_at = time.time() + w
                    log(f"!! 元大登入回應看不懂:{e}(第 {n} 次)→ {w} 秒後再試(最多再試 {mx} 次)")
                    if n == 1: self.phone_alert("⚠ 元大登入回應看不懂", f"{e} · 中繼 {w} 秒後再試,最多 {mx} 次,還是不行今天就停(避免帳號被鎖)")
                    continue
                self.login_soft_n = getattr(self, "login_soft_n", 0) + 1; n = self.login_soft_n
                mx = int(CFG.get("LOGIN_RETRY_MAX", "10") or 10)
                if n > mx:
                    log(f"!! 元大暫時拒絕登入已經 {n - 1} 次({e}),今天不再重試")
                    self.phone_alert("⚠⚠ 元大一直不給登入", f"{e} · 已試 {n - 1} 次,今天不再重試;有部位請用元大 App 手動平倉")
                    break
                w = login_backoff(n); self._relogin_at = time.time() + w
                log(f"!! 元大暫時不給登入:{e}(第 {n} 次)→ {w} 秒後再登入")
                if n == 1: self.phone_alert("⚠ 元大暫時不給登入", f"{e} · 中繼 {w} 秒後自動再登入(最多 {mx} 次),13:20 平倉照常")
            except LoginRejected as e:
                log(f"!! 元大拒絕登入:{e}。今天不再重試,請檢查 bridge.env 的帳號 / 密碼 / 權限後重啟服務")
                self.phone_alert("⚠⚠ 元大拒絕登入", f"{e} · 今天程式不會管部位,有部位請用元大 App 手動平倉")
                self.close(); break
            except Exception as e:
                fails += 1; log("錯誤", e, f"(第 {fails} 次)")
                self.close(); time.sleep(min(60, 10 * fails))   # 登入失敗不能太頻繁(每 4 秒最多 1 次,一天 1000 次)
        self.flush(day, final=True); self.flush_ticks(day)   # 2026-10-30 收工前把剩下的逐筆寫完
        self.push_drain(30)   # 2026-11-21(檢查 R2):背景佇列裡還沒推上去的分鐘,最多等 30 秒
        try:   # 2026-10-29 13:38 收工登出前最後查一次今天成交存起來(手機明細登出後照樣看得到)
            if self.api and self.logged_in.is_set():
                self._fills_cache = None; fj = self.trade_fills(); log(f"收工前存今天成交:{'好' if fj.get('ok') else fj.get('msg')}")
        except Exception as e: log("收工前存成交失敗", e)
        try: self.pc_backup(day)   # 2026-11-12 21:00 使用者「都改」(Cloudflare 2/3/5):收工最後一份備份(含今天完整成交),雲端 14:05 存 R2 / D1、寫 AI 檢討
        except Exception as e: log("收工備份失敗", e)
        _ok = len([x for x in (self.__dict__.get("_pq_st") or {}).get("ok", ()) if x[0] == day])
        log(f"收工:逐筆 {self.n_ticks} 筆,1 分 K {len(self.sent)} 分鐘(推上 Worker {_ok});交易 {self.trader.n_trades} 筆 淨 {self.trader.pnl:+,.0f} {self.trader.journal}")
        if getattr(self, "engine", None):
            try:
                self.engine.wait(timeout=120)   # engine.py 13:32 自己收工;沒結束就關掉
            except Exception:
                try: self.engine.terminate()
                except Exception: pass
            log("[公式] engine.py 已結束")
        self.fut_save(day)   # 2026-11-13 台指期:等到 13:46 存 1 分 K、推 Worker 一次
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
    # 2026-11-06 使用者「1,2,3都做」第 3 項:排程每 5 分鐘叫一次中繼(當掉才會重拉),中繼本來就在跑時原本寫「!! 已經有另一個中繼在跑」,
    #   看起來像錯誤 → 改成一般說明,而且一小時只寫一次
    mk = HERE / "log" / ".bridge_running_note"
    try: quiet = mk.exists() and time.time() - mk.stat().st_mtime < 3600
    except Exception: quiet = False
    if not quiet:
        log("中繼已經在執行中(排程每 5 分鐘檢查一次,這是正常的;一小時只記一次)")
        try: mk.parent.mkdir(exist_ok=True); mk.write_text(str(time.time()))
        except Exception: pass
    sys.exit(0)


def upd_ref():
    """自動當沖夥伴版 2026-11-15:夥伴中繼(FAMILY)不可跟 main 更新 —— main 的程式沒有 ACCT_NAME 邏輯,換上去就會變回主帳號模式(開引擎、推雲端蓋掉主導$ 的資料)。
       夥伴中繼只在 bridge.env 有 UPDATE_REF=<commit 編號>(partner 分支上的某一版)時才更新到那一版;沒寫 → 回 "" 不更新。
       (線上 Worker ?gh=raw 的 ref 只收 commit 編號,不收分支名)主帳號照舊回 "main"。"""
    if not FAMILY: return "main"
    r = CFG.get("UPDATE_REF", "").strip().lower()
    return r if re.fullmatch(r"[0-9a-f]{7,40}", r) else ""


def self_update():
    """2026-10-30 02:58 使用者「你有辦法推上電腦嗎」:Claude 不能直接碰 Windows 桌機,改成中繼自己更新 ——
       啟動時(排程 08:40 / 開機 / 崩潰重拉,不在盤中)讀 GitHub 的 tools/yuanta_bridge/VERSION(一個版本號,改了就代表要更新),
       和本機 VERSION 不同就經 Worker 把 main 最新的程式檔抓下來(每個檔先編譯檢查,壞的不換,舊檔留 .bak),寫好 VERSION 後重新啟動自己。
       改中繼程式的人:推完 main 後把 VERSION 改成新的版本號(2026-11-01 起用「年月日時分」,和手機網頁的版本戳同一種寫法,例如 202611011100;只比對是否相同,不比大小)一起推。
       bridge.env AUTO_UPDATE=0 可關。"""
    if CFG.get("AUTO_UPDATE", "1") != "1" or os.environ.get("MYSH_UPDATED"): return
    ref = upd_ref()
    if not ref: return
    hm = now_tw().strftime("%H:%M")
    if "09:00" <= hm < "13:40" and now_tw().weekday() < 5: return          # 盤中不換程式(2026-11-11 00:27 使用者「把不更新從 8 點 45 改為 9 點」:原 08:45;電腦 08:45 自動開機也能先更新)
    try:
        want = gh_get("tools/yuanta_bridge/VERSION", ref=ref, timeout=20).decode("utf-8").strip().split()[0]
    except Exception as e:
        log("自動更新:讀不到 VERSION", e); return
    cur = ""
    try: cur = (HERE / "VERSION").read_text(encoding="utf-8").strip().split()[0]
    except Exception: pass
    if not re.fullmatch(r"[\w.\-]{1,40}", want) or want == cur: return
    log(f"自動更新:VERSION {cur or '無'} → {want},下載中")
    # 2026-11-21 使用者「全改」(檢查 R8):原本一個檔一個檔下載就換掉、只要有 1 個成功就寫 VERSION → 部分新、部分舊的程式也算「已更新」,之後不會再補;
    #   main 在下載途中又被推新版,也可能拿到兩個版本混在一起。改成:
    #   1. 主帳號先問 Worker main 現在的提交編號(?gh=tree 回的 commit),全部檔案都用 ref=<那個編號> 抓(同一版);問不到就照舊抓 main,最後再讀一次 VERSION 要和開始時一樣
    #   2. 全部先下載到暫存資料夾、每個都編譯檢查;有任何一個失敗 → 全部不換、不寫 VERSION(下次再試)
    #   3. 全部換上(舊檔留 .bak);換到一半失敗 → 已換的還原,不寫 VERSION;全部成功才寫 VERSION
    pin = ref
    if ref == "main" and CFG.get("PUSH_KEY") and CFG.get("WORKER_URL"):
        try:
            rq = urllib.request.Request(CFG["WORKER_URL"].rstrip("/") + "/?gh=tree&repo=db", headers={"X-Push-Key": CFG["PUSH_KEY"], "User-Agent": "yuanta-bridge/1"})
            sha = str(json.loads(urllib.request.urlopen(rq, timeout=60).read().decode("utf-8")).get("commit") or "")
            if re.fullmatch(r"[0-9a-f]{40}", sha):
                w2 = gh_get("tools/yuanta_bridge/VERSION", ref=sha, timeout=20).decode("utf-8").strip().split()[0]
                if w2 == want: pin = sha; log(f"自動更新:固定抓提交 {sha[:10]}")
                else: log(f"自動更新:提交 {sha[:10]} 的 VERSION 是 {w2}(剛好又推了新版)→ 這次不換,下次再試"); return
        except Exception as e: log("自動更新:問不到 main 的提交編號,照舊抓 main(最後再對一次 VERSION)", e)
    files = ["yuanta_bridge.py", "bars.py", "trader.py", "quotes.py", "win_pnl.py", "engine.py", "win_keys.py", "win_update.py", "hist_kline.py", "win_install.py", "elig.py"]
    try:   # 2026-10-30 11:10(實際時間)檔案清單改讀 repo 的 tools/yuanta_bridge/FILES(新增檔案時舊版程式也抓得到;抓不到就用上面寫死的)
        lst = [x.strip() for x in gh_get("tools/yuanta_bridge/FILES", ref=pin, timeout=30).decode("utf-8").splitlines() if x.strip().endswith(".py")]
        if lst: files = lst
    except Exception: pass
    files = [f for f in files if re.fullmatch(r"[\w.\-]+\.py", f)]   # 只收同一資料夾的 .py
    import py_compile, shutil
    stage = HERE / f".upd_{want}"
    try:
        shutil.rmtree(stage, ignore_errors=True); stage.mkdir(parents=True, exist_ok=True)
        for f in files:   # 2:全部先下載 + 編譯
            b_ = gh_get(f"tools/yuanta_bridge/{f}", ref=pin, timeout=60)
            (stage / f).write_bytes(b_); py_compile.compile(str(stage / f), cfile=str(stage / (f + ".pyc_chk")), doraise=True)
        if pin == ref:   # 沒固定提交:下載完再對一次 VERSION(途中被推新版 → 檔案可能混到兩版,這次不換)
            w3 = gh_get("tools/yuanta_bridge/VERSION", ref=ref, timeout=20).decode("utf-8").strip().split()[0]
            if w3 != want:
                log(f"自動更新:下載途中 VERSION 變成 {w3} → 這次不換,下次再試"); shutil.rmtree(stage, ignore_errors=True); return
    except Exception as e:
        log("自動更新:下載 / 編譯檢查沒有全部成功 → 一個都不換、不寫 VERSION(下次再試)", e); shutil.rmtree(stage, ignore_errors=True); return
    done = []   # 3:換上;失敗就還原
    try:
        for f in files:
            dst = HERE / f; had = dst.exists()
            if had: shutil.copyfile(dst, HERE / (f + ".bak"))
            os.replace(stage / f, dst); done.append((f, had))
    except Exception as e:
        log("自動更新:換檔到一半失敗 → 還原已換的檔,不寫 VERSION", e)
        for f, had in reversed(done):
            try:
                if had: shutil.copyfile(HERE / (f + ".bak"), HERE / f)
                else: (HERE / f).unlink()
            except Exception as e2: log(f"自動更新:還原 {f} 失敗!", e2)
        shutil.rmtree(stage, ignore_errors=True); return
    shutil.rmtree(stage, ignore_errors=True)
    (HERE / "VERSION").write_text(want + "\n", encoding="utf-8")
    log(f"自動更新:{len(files)} 個檔全部換成 {want},重新啟動")
    os.environ["MYSH_UPDATED"] = "1"
    try:
        if _BRIDGE_LOCK: _BRIDGE_LOCK.close()   # 2026-10-30 放開單一執行個體鎖,新行程才拿得到
    except Exception: pass
    restart_self()


def restart_self():
    """2026-11-21 使用者「全改」(輕微項):原本 os.execv(sys.executable, [sys.executable] + sys.argv) 在 Windows 參數不會加引號,
       路徑有空白(例如 C:/Program Files/Python…)會被拆開 → 新行程起不來。改成 subprocess.Popen(清單參數,Windows 會正確加引號)開新行程後自己結束"""
    import subprocess
    args = [sys.executable, os.path.abspath(sys.argv[0])] + sys.argv[1:]
    subprocess.Popen(args, cwd=str(HERE), env=dict(os.environ), close_fds=True)
    os._exit(0)


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
    if cmd == "cond-test":
        # 2026-11-10 16:10 使用者「現在開元大能找到嗎」:只查詢、不下單 —— 登入 → 查今天委託 / 成交 + 二擇一條件單(新的參數寫法)→ 再查全部類別條件單 → 登出;結果寫進 log
        b.connect(); time.sleep(2)
        r = b.query_today(with_oco=True) or {}
        log(f"[條件單測試] 接回查詢:條件單 {len(r.get('oco') or [])} 張{'(查詢失敗)' if r.get('oco_failed') else ''} {r.get('oco')}")
        st = b.query_strategies() or []
        log(f"[條件單測試] 全部類別有效條件單 {len(st)} 張 {st}")
        b.close(); return
    if cmd == "kline":
        code, day = sys.argv[2], sys.argv[3]
        mkt = b.Y.enumMarketType.TWOTC if (len(sys.argv) > 4 and sys.argv[4].lower() in ("otc", "tpex", "twotc")) else b.Y.enumMarketType.TWSE
        b.kline_out = []; b.connect()
        b.api.GetKLine(CFG["YT_ACCOUNT"], b.Y.KLineType(0), mkt, code, day, day)   # KLineType 0 = 一分線(元大文件「列舉物件」);上櫃股加參數 otc
        time.sleep(5)
        for k in b.kline_out: print(*k)
        b.close(); return
    if cmd == "fut-kline":
        # 2026-11-16 15:50 使用者「台指期 補K 盤中無法補 只能盤後嗎」→「好 要試」:試元大 GetKLine 能不能抓台指期當天 1 分 K(盤中斷線補回用);只查詢、不下單
        #   用法:python yuanta_bridge.py fut-kline [日期 YYYY/MM/DD,預設今天];近月代號幾種寫法都試,每種印根數 / 第一根 / 最後一根,結果也寫 log
        day = sys.argv[2] if len(sys.argv) > 2 else now_tw().strftime("%Y/%m/%d")
        y, m = b.fut_near(); L = "ABCDEFGHIJKL"[m - 1]
        cands = [f"TXF{L}{y % 10}", f"TXF{y}{m:02d}", "TXF1", f"TXF{m:02d}", "TXF", f"TX{L}{y % 10}", "TXFR1", "TX00"]
        b.connect(); time.sleep(2)
        for c in cands:
            for kt in (0,):
                b.kline_out = []
                try: b.api.GetKLine(CFG["YT_ACCOUNT"], b.Y.KLineType(kt), b.Y.enumMarketType.TAIFEX, c, day, day)
                except Exception as e: log(f"[台指期補K測試] {c} 呼叫失敗 {e!r}"); continue
                time.sleep(4); k = b.kline_out or []
                log(f"[台指期補K測試] {c} {day}:{len(k)} 根" + (f",第一根 {k[0]},最後一根 {k[-1]}" if k else ""))
                print(f"{c}: {len(k)} 根", (k[0], k[-1]) if k else "")
        b.close(); return
    if cmd == "order-test":
        # 只在測試環境:送 1 張限價單,印 20 秒的下單回應和回報(元大測試環境依委託書號尾碼模擬全成交 / 不成交 / 部分成交 / 價穩失效)
        if CFG.get("YT_ENV", "UAT").upper() != "UAT":
            sys.exit("order-test 只能在測試環境(YT_ENV=UAT)")
        code, side, price = sys.argv[2], sys.argv[3].upper(), float(sys.argv[4])
        b.connect(); b.send_order(code, "TWSE", side, 1, price, "0", "0", "TEST1")
        time.sleep(20); b.close(); return
    # run:常駐
    if FAMILY: log(f"[家人 {ACCT}] 只下單模式啟動(帳號 …{str(CFG.get('YT_ACCOUNT', ''))[-4:]};設定照本資料夾 bridge.env;訊號讀 {os.environ.get('ENGINE_DIR') or CFG.get('ENGINE_DIR') or '雲端'})")
    if not CFG.get("WORKER_URL"):
        sys.exit("缺少設定 WORKER_URL")
    if not CFG.get("PUSH_KEY"):
        log("沒有 PUSH_KEY:元大 1 分 K 只存本機", CFG.get("BARS_DIR", str(HERE / "bars_yuanta")), "(不推 Worker,不用 KV 額度)")
    if CFG.get("QUOTE_ENABLE", "0") == "1":        # 預設關閉:要使用者自己決定要不要讓手機即時價走元大(會透過 Cloudflare Tunnel 對外開一個要金鑰的服務;2026-10-28 起也接手機的強制交易指令,要 TRADE_CMD_PIN)
        b.serve_quotes()
    hist_done = set()
    # 2026-11-19 19:30 使用者「雲端更新,電腦也有跟著更新」→「現在馬上做」:另開一條執行緒,每 10 分鐘把 GitHub 三個庫同步到 C:\Yuanta\雲端備份\<庫名>(win_sync.py);
    #   盤中一次最多 200 個檔(不影響行情 / 下單),盤後不限;bridge.env 設 CLOUD_SYNC=0 可關掉
    if CFG.get("CLOUD_SYNC", "1") == "1" and not FAMILY:
        def _sync_loop():
            time.sleep(60)
            while True:
                try:
                    import importlib, win_sync; importlib.reload(win_sync); win_sync.run(log)
                except Exception as e: log("雲端同步失敗", e)
                time.sleep(600)
        threading.Thread(target=_sync_loop, daemon=True, name="cloud_sync").start()
    while True:
        n = now_tw(); hm = n.strftime("%H:%M")
        # 2026-10-29 13:38 使用者「明細沒了」:收盤後(或重開程式時)今天還沒存到元大成交 → 登入一次查完存起來就登出(一天只試一次,手機明細才有今天的真實成交)
        fk = n.strftime("%Y-%m-%d")
        hol = is_holiday(fk)   # 2026-11-21 使用者「全改」(輕微項):休市日不登入元大
        if hol and fk + "hol" not in hist_done and n.weekday() < 5:
            hist_done.add(fk + "hol"); log(f"今天 {fk} 是休市日,不登入元大(bridge.env HOLIDAY_SKIP=0 可關掉)")
        if n.weekday() < 5 and not hol and "13:36" <= hm < "23:00" and fk not in hist_done and not (HERE / "fills" / f"{fk}.json").exists():
            hist_done.add(fk)
            try:
                b.connect(); fj = b.trade_fills(); log(f"收盤後補查今天成交:{'好,' + str(fj.get('n', '')) + ' 筆' if fj.get('ok') else fj.get('msg')}")
            except Exception as e: log("收盤後補查今天成交失敗", e)
            finally:
                try: b.close()
                except Exception: pass
        # 2026-10-25 使用者「符合條件一律收進來…預先抓好抓滿」:收工後把今天「前一天收盤符合選股條件的全部股票」的元大 1 分 K 抓回本機 kline_yuanta\
        #   14:05 一次、17:05 再補一次(已抓過的跳過);同一支程式依序登入,不會兩支同時登入。bridge.env 設 HIST_AFTER=0 可關掉
        if n.weekday() < 5 and not hol and CFG.get("HIST_AFTER", "0" if FAMILY else "1") == "1" and "14:05" <= hm < "20:00":   # 2026-11-15 家人帳號預設不抓(主帳號抓一份就好)
            for at, until in (("14:05", "17:05"), ("17:05", "20:00")):
                key = n.strftime("%Y-%m-%d") + at
                if at <= hm < until and key not in hist_done:
                    hist_done.add(key); log(f"收工後抓今天全部符合條件股票的 1 分 K({at})")
                    try:
                        import subprocess
                        subprocess.run([sys.executable, str(HERE / "hist_kline.py"), "today"], cwd=str(HERE), timeout=3 * 3600)
                    except Exception as e:
                        log("抓今天 1 分 K 失敗", e)
        # 2026-11-02 20:33 使用者「你測電腦是否更新」:中繼一直開著(電腦沒關機)就永遠不會更新(只在啟動時檢查)→ 不在盤中時每 30 分鐘看 GitHub 的 VERSION,
        #   不一樣就結束自己,工作排程(每 5 分鐘檢查一次)重新拉起來時就會更新。盤中(08:40~13:40)和收盤後補查 / 抓 K 進行中不做
        if not ("09:00" <= hm < "13:40") and CFG.get("AUTO_UPDATE", "1") == "1" and upd_ref() and time.time() - getattr(b, "_ver_chk", 0) > 1800:   # 2026-11-11 00:27 原 08:40 → 09:00(使用者「改為 9 點」)
            b._ver_chk = time.time()
            try:
                want = gh_get("tools/yuanta_bridge/VERSION", ref=upd_ref(), timeout=20).decode("utf-8").strip().split()[0]
                cur = (HERE / "VERSION").read_text(encoding="utf-8").strip().split()[0] if (HERE / "VERSION").exists() else ""
                if want and want != cur:
                    log(f"有新版 {cur} → {want}:結束自己,讓工作排程重開時更新"); os._exit(0)
            except Exception as e: log("檢查新版失敗", e)
        if n.weekday() < 5 and not hol and CFG.get("DAY_START", "08:30") <= hm < "13:36":   # ⑧ 2026-11-11 20:25 使用者「123478改」:08:45 → 08:30 連線訂閱,收得到元大開盤前的清盤參考價(昨收,除權息已扣;原本 08:45 才訂閱就錯過了,ref 檔一直寫不出來)
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
