"""python3 test_bridge.py:中繼程式(yuanta_bridge.py)不用元大元件的單元測試
2026-11-21 使用者「全改」(檢查 R1~R11 + 輕微項):Bridge 用 Bridge.__new__ 建(不載入元大元件),需要的屬性 / 方法自己塞假的"""
import datetime as dt, os, sys, tempfile, threading, time, types
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import yuanta_bridge as Y
from bars import BarBook

N_OK = [0]
def ok(cond, *msg):
    if not cond:
        raise AssertionError(" ".join(str(m) for m in msg) or "失敗")
    N_OK[0] += 1

LOGS = []
Y.log = lambda *a: LOGS.append(" ".join(str(x) for x in a))
for k in ("PUSH_KEY", "WORKER_URL", "LOG_DIR", "FILL_ID_FIELD", "BARS_DIR"): Y.CFG.pop(k, None)
TMP = Path(tempfile.mkdtemp())
NOW = [dt.datetime(2026, 11, 23, 10, 0, 30, tzinfo=Y.TW)]
Y.now_tw = lambda: NOW[0]


class T:   # 假 trader
    def __init__(s):
        s.lock = threading.RLock(); s.ignore = {"YY0070"}; s.positions = {}; s.calls = []; s.enabled = True
        s.prehang = {}; s.prehang_deadline = "0"; s.force_exit = "13:20"; s.man_meta = {}
    def prepare_close_all(s): s.calls.append(("prep",))
    def recheck(s, trades, working=(), at=None): s.calls.append(("recheck", list(trades), list(working), set(s.ignore)))
    def reconcile(s, trades, working, tags=None, types=None): s.calls.append(("recon", list(trades), set(s.ignore)))
    def resume(s, *a): s.calls.append(("resume",))


def mk():
    b = Y.Bridge.__new__(Y.Bridge)
    b.logged_in = threading.Event(); b.disconnected = threading.Event(); b.trade_up = threading.Event()
    b.login_fail = None; b.login_soft = None; b.feat_off = {}; b.resume_buf = None
    b.sent_tags = []; b.tag_lock = threading.RLock(); b.qlock = threading.RLock(); b.trader = T(); b.api = None
    b.book = BarBook(); b.sent = set(); b.alerts = []; b.kline_out = None; b.kline_lock = threading.Lock()
    b.phone_alert = lambda title, body="", kind="pc": b.alerts.append(title)
    b.quotes = types.SimpleNamespace(meta={})
    return b


# ── R1 登入回應分永久 / 暫時 ──
ok(Y.login_kind("0001", "成功") == "ok" and Y.login_kind("9999", "", 1) == "ok")
ok(Y.login_kind("0016", "請勿連續進行系統登入作業") == "retry")
# 2026-11-21 使用者「改 A」(A5):0000 → 當天停;沒看過的碼 → unknown(最多再試 2 次)
ok(Y.login_kind("0000", "執行失敗") == "fatal" and Y.login_kind("7777", "沒看過") == "unknown")
ok(Y.login_kind("0102", "密碼凍結或未啟用") == "fatal" and Y.login_kind("0112", "無此權限使用功能") == "fatal")
ok(Y.login_kind("0003", "密碼錯誤") == "fatal" and Y.login_kind("0004", "帳號不存在") == "fatal")
bo = [Y.login_backoff(n) for n in range(1, 11)]
ok(bo[:5] == [30, 60, 120, 240, 300] and max(bo) == 300 and min(bo) >= 30, bo)   # 遠大於「每 4 秒最多 1 次」

b = mk()
L = lambda code, msg, cnt=0: types.SimpleNamespace(LoginStatus=types.SimpleNamespace(MsgCode=code, MsgContent=msg, Count=cnt))
b.on_response(1, 0, "Login", None, L("0016", "請勿連續進行系統登入作業"))
ok(b.login_fail is None and b.login_soft and not b.logged_in.is_set(), b.login_fail, b.login_soft)
b.on_response(1, 0, "Login", None, L("0102", "密碼凍結或未啟用"))
ok(b.login_fail and "0102" in b.login_fail)
b = mk(); b.on_response(1, 0, "Login", None, L("0001", "登入成功", 1)); ok(b.logged_in.is_set() and not b.login_fail and not b.login_soft)

# 非登入的查詢回權限不足(5)→ 只停那個功能,不停交易
b = mk(); b.logged_in.set()
b.on_response(1, 5, "GetConditionStrategy", None, "權限不足")
ok(b.login_fail is None and "GetConditionStrategy" in b.feat_off and len(b.alerts) == 1, b.login_fail, b.feat_off)
b.on_response(1, 5, "GetConditionStrategy", None, "權限不足"); ok(len(b.alerts) == 1)   # 同一個功能只推一次
try: b.get_cond(3); ok(False, "應該丟例外")
except RuntimeError: ok(True)
b.broker_stop_on = lambda: True; ok(b.query_strategies() == [])
b.on_response(1, 5, "GetKLine", None, "權限不足"); ok(b.kline_query("2330", 0, "2026-11-23") == [])
# 還沒登入、沒有功能名的 4 / 9 / 11 = 帳號問題 → 當天停
b = mk(); b.on_response(1, 9, "", None, "憑證異常"); ok(b.login_fail and "9" in b.login_fail)

# ── R7 送單:沒登入不送、例外時編號要拿掉 ──
b = mk()
try: b._send_raw([], "T1", "x"); ok(False)
except ConnectionError: ok(b.sent_tags == [])
b.logged_in.set()
class BadApi:
    def SendStockOrder(s, acc, lst): raise RuntimeError("元件爆了")
b.api = BadApi(); Y.CFG["YT_ACCOUNT"] = "TEST"
try: b._send_raw([], "T2", "x"); ok(False)
except RuntimeError: ok(b.sent_tags == [], b.sent_tags)
class FalseApi:
    def SendStockOrder(s, acc, lst): return False
b.api = FalseApi(); b._send_raw([], "T3", "x"); ok(b.sent_tags == [])
class GoodApi:
    def SendStockOrder(s, acc, lst): return True
b.api = GoodApi(); b._send_raw([], "T4", "x"); ok(b.sent_tags == ["T4"])
# trade_cmd:沒登入時要送單的指令直接拒絕
b = mk(); b.api = None; r = b.trade_cmd({"act": "enter", "code": "2330", "dir": 1, "lots": 1})
ok(r["ok"] is False and "沒有登入" in r["msg"], r)
r = b.trade_cmd({"act": "close_only"}); ok(r["ok"] is False and "沒有登入" in r["msg"], r)

# ── R2 / R3 / R6 1 分 K:本機每分鐘都寫(和推雲端無關)、檔被佔用不丟例外、晚到的成交重送 ──
Y.CFG["BARS_DIR"] = str(TMP / "bars")
b = mk(); pushed = []
b.push_async = lambda d, m, bars: pushed.append((m, {c: list(v) for c, v in bars.items()}))
day = "2026-11-23"
NOW[0] = dt.datetime(2026, 11, 23, 9, 3, 1, tzinfo=Y.TW)
b.book.add_tick("2330", 9, 1, 1, 100.0, 1, b.sent); b.book.add_tick("2330", 9, 2, 2, 101.0, 2, b.sent)
b.flush(day)
f = TMP / "bars" / f"{day}.csv"
rows = f.read_text(encoding="utf-8").splitlines()
ok(len(rows) == 3 and rows[1].startswith("2330,2026-11-23,09:01,") and rows[2].startswith("2330,2026-11-23,09:02,"), rows)
ok([m for m, _ in pushed] == ["09:01", "09:02"] and {"09:01", "09:02"} <= b.sent)
# R6:晚到的成交(已取走的分鐘)→ dirty → 下一輪重寫本機 + 重推
ok(b.book.add_tick("2330", 9, 2, 3, 103.0, 1, b.sent)); b.flush(day)
ok(pushed[-1][0] == "09:02" and pushed[-1][1]["2330"][1] == 103.0, pushed[-1])
ok(",09:02,101.0,103.0,101.0,103.0,3000" in f.read_text(encoding="utf-8"), f.read_text(encoding="utf-8"))
# R6 競態:flush 標「已取走」後、take 之前進來的成交也要算(sent 先標)
b2 = mk(); b2.push_async = lambda *a: None
b2.book.add_tick("2330", 9, 1, 1, 100.0, 1, b2.sent)
orig_take = b2.book.take
def take_late(m):   # 模擬 take 的瞬間又來一筆(sent 已經標了 → 要標 dirty)
    if m == "09:01" and not getattr(b2, "_late", False):
        b2._late = True; b2.book.add_tick("2330", 9, 1, 2, 99.0, 1, b2.sent)
    r_ = orig_take(m); return r_
b2.book.take = take_late; b2.flush(day)
late = []; b2.book.take = orig_take; b2.book.add_tick("2330", 9, 1, 3, 98.0, 1, b2.sent); late = b2.book.take_dirty()
ok("09:01" in late, late)
# R3:Windows 檔被引擎開著 → os.replace PermissionError:不丟例外、回 False、下一輪再寫
_rep = os.replace; n_rep = [0]
def bad_replace(a, b_):
    n_rep[0] += 1; raise PermissionError(13, "檔案被另一個程序使用")
Y.os.replace = bad_replace
try:
    b.book.add_tick("2330", 9, 3, 4, 104.0, 1, b.sent); NOW[0] = dt.datetime(2026, 11, 23, 9, 4, 1, tzinfo=Y.TW)
    b.flush(day)   # 不能丟例外
    ok(n_rep[0] >= 2 and b._local_dirty, n_rep[0])
    ok(",09:03," not in f.read_text(encoding="utf-8"))
finally:
    Y.os.replace = _rep
b.flush(day)   # 沒有新分鐘,但上次沒寫成 → 這次要補寫
ok(",09:03," in f.read_text(encoding="utf-8") and not b._local_dirty)
# Worker 掛掉(push 一直失敗)時本機照寫:用真的背景佇列,push 失敗 → 留在佇列、主流程不等
b3 = mk(); Y.CFG["PUSH_KEY"] = "k"; Y.CFG["WORKER_URL"] = "http://127.0.0.1:9"
calls = []
def push_fail(d, m, bars, timeout=None): calls.append(m); return False
b3.push = push_fail
NOW[0] = dt.datetime(2026, 11, 23, 9, 10, 1, tzinfo=Y.TW)
for i, mm in enumerate((5, 6, 7)): b3.book.add_tick("2317", 9, mm, i + 1, 50.0, 1, b3.sent)
t0 = time.time(); b3.flush(day); ok(time.time() - t0 < 1.0)
txt = (TMP / "bars" / f"{day}.csv").read_text(encoding="utf-8")
ok(",09:05," in txt and ",09:07," in txt)
time.sleep(0.5); st = b3._pq_st
ok(calls and calls[0] == "09:05" and len(st["pend"]) == 3, calls, st["pend"].keys())   # 失敗:最舊的試一次就退避,全部留在佇列
st["next"] = 0; b3.push = lambda d, m, bars, timeout=None: (calls.append(m), True)[1]; st["ev"].set()
ok(b3.push_drain(5) and sorted(m for _, m in st["ok"]) == ["09:05", "09:06", "09:07"], st["ok"])
Y.CFG.pop("PUSH_KEY"); Y.CFG.pop("WORKER_URL")

# ── R4 可疑的重複成交 ──
S1 = {"code": "2330", "bs": "S", "lots": 1, "price": 100.0, "time": "09:30:01", "order_no": "A1"}
B1 = {"code": "2330", "bs": "B", "lots": 1, "price": 99.0, "time": "10:00:01", "order_no": "A2"}
safe, sus, amb = Y.fills_guard([S1, B1])
ok(safe == [S1, B1] and not sus and not amb)
# 平倉單的成交回報兩次:照原樣 +1(會再「跌停賣出」= 反向開新倉)、去重複 0 → 判斷不了 → amb
safe, sus, amb = Y.fills_guard([S1, B1, dict(B1)])
ok("2330" in amb and amb["2330"] == (1, 0) and len(sus["2330"]) == 1, amb)
# 程式自己記的部位 = 0(已平)和去重複那份一樣 → 用去重複的
safe, sus, amb = Y.fills_guard([S1, B1, dict(B1)], {"2330": 0}); ok(not amb and sum(t["lots"] for t in safe) == 2)
# 同方向、張數不同 → 用張數少的(只會少平,不會反向)
S2 = dict(S1, lots=2, order_no="A3", time="09:40:00")
safe, sus, amb = Y.fills_guard([S1, dict(S1), S2])
ok(not amb and sum(t["lots"] for t in safe) == 3, safe)
# 有成交序號(FILL_ID_FIELD)時序號不同 = 不同筆 → 不算重複
safe, sus, amb = Y.fills_guard([dict(B1, fill_id="1"), dict(B1, fill_id="2")]); ok(not sus)
# close_all / recheck:判斷不了的檔在 recheck 期間放進不管名單(不送單、不結束部位),之後拿掉,推手機
b = mk(); b.logged_in.set()
b.prehang_pick = lambda at: set()
b.query_today = lambda with_oco=True: {"working": [], "trades": [S1, B1, dict(B1)], "tags": {}}
b.query_strategies = lambda types=(1, 2, 3, 4, 5): []
NOW[0] = dt.datetime(2026, 11, 23, 13, 20, 1, tzinfo=Y.TW)
b.close_all("13:20")
rc = [c for c in b.trader.calls if c[0] == "recheck"][-1]
ok("2330" in rc[3] and "2330" not in b.trader.ignore and "YY0070" in b.trader.ignore, rc[3], b.trader.ignore)
ok(any("疑似重複" in a for a in b.alerts), b.alerts)
b.alerts.clear(); b.recheck_positions("13:22"); ok(not any("疑似重複" in a for a in b.alerts))   # 同一組一天只推一次
rc = [c for c in b.trader.calls if c[0] == "recheck"][-1]; ok("2330" in rc[3])

# ── R11 13:20 查詢失敗不能當成「沒有掛著的委託」──
b = mk(); b.logged_in.set(); b.prehang_pick = lambda at: set()
qn = [0]
def q_fail_then_ok(with_oco=True):
    qn[0] += 1
    return None if qn[0] <= 2 else {"working": [], "trades": [], "tags": {}}
b.query_today = q_fail_then_ok; b.query_strategies = lambda types=(1, 2, 3, 4, 5): []
_sl = Y.time.sleep; Y.time.sleep = lambda s: None
try:
    LOGS.clear(); b.close_all("13:20")
    conf = [x for x in LOGS if "確認:沒有掛著的委託和條件單" in x]
    ok(len(conf) == 1 and qn[0] >= 3, LOGS)
    ok(LOGS.index(conf[0]) > min(i for i, x in enumerate(LOGS) if "查不到" in x))
    # 一直查不到 → 不印「確認」、推手機
    b = mk(); b.logged_in.set(); b.prehang_pick = lambda at: set()
    b.query_today = lambda with_oco=True: None; b.query_strategies = lambda types=(1, 2, 3, 4, 5): None
    LOGS.clear(); b.close_all("13:20")
    ok(not any("確認:沒有掛著" in x for x in LOGS) and any("查不到的掛單" in a for a in b.alerts), b.alerts)
    # 條件單查不到(None)也不算確認
    b = mk(); b.logged_in.set(); b.prehang_pick = lambda at: set()
    b.query_today = lambda with_oco=True: {"working": [], "trades": [], "tags": {}}; b.query_strategies = lambda types=(1, 2, 3, 4, 5): None
    LOGS.clear(); b.close_all("13:20"); ok(not any("確認:沒有掛著" in x for x in LOGS))
finally:
    Y.time.sleep = _sl

# ── R9 接回:查不到不算接回過 ──
b = mk(); b.query_today = lambda with_oco=True: None
ok(b.resume_positions("2026-11-23") is False and not [c for c in b.trader.calls if c[0] == "resume"])
_fs = Y.fetch_signals; Y.fetch_signals = lambda *a: []; Y.CFG["WORKER_URL"] = "http://127.0.0.1:9"
try:
    b.query_today = lambda with_oco=True: {"trades": [], "tags": {}, "oco": [], "working": [], "types": {}}
    NOW[0] = dt.datetime(2026, 11, 23, 8, 40, tzinfo=Y.TW)
    ok(b.resume_positions("2026-11-23") is True and [c for c in b.trader.calls if c[0] == "resume"])
finally:
    Y.fetch_signals = _fs; Y.CFG.pop("WORKER_URL")
b.trader.enabled = False; ok(b.resume_positions("2026-11-23") is True)

# ── R5 / R10 換日清掉 ──
b = mk(); b.fut_bars = {"TXFL6": {"09:00": [1, 1, 1, 1, 1]}}; b.fut_sn = {"TXFL6": {1, 2}}; b.fut_n = {"TXFL6": 2}; b.fut_tf = {"09:00": [1, 1, 1, 1, 0]}
b._fut_hm = "13:45"; b.eng_restarts = 5; b.feat_off = {"GetKLine": "x"}; b.login_soft_n = 7
b.day_reset()
ok(b.fut_bars == {} and b.fut_sn == {} and b.fut_n == {} and b.fut_tf == {} and not hasattr(b, "_fut_hm"))
ok(b.eng_restarts == 0 and b.feat_off == {} and b.login_soft_n == 0)

class P:
    def __init__(s, rc): s.returncode = rc; s.pid = 1
    def poll(s): return s.returncode
spawned = []
import subprocess
_po = subprocess.Popen; subprocess.Popen = lambda *a, **k: (spawned.append(a), P(None))[1]
try:
    b = mk(); b.day_reset(); b.engine = P(2); b.engine_watch()
    ok(b.eng_restarts == 0 and len(spawned) == 1)                     # 鎖被佔走:不算重開次數
    b.engine = P(2); b.engine_watch(); ok(len(spawned) == 1)           # 2 分鐘內不再試
    b.engine = P(1); b.engine_watch(); ok(b.eng_restarts == 1 and len(spawned) == 2)
finally:
    subprocess.Popen = _po

# ── 輕微項:休市日 ──
ok(Y.is_holiday("2026-12-25") and not Y.is_holiday("2026-11-23"))
Y.CFG["MARKET_HOLIDAYS"] = "2026-11-24"; ok(Y.is_holiday("2026-11-24")); Y.CFG.pop("MARKET_HOLIDAYS")
Y.CFG["HOLIDAY_SKIP"] = "0"; ok(not Y.is_holiday("2026-12-25")); Y.CFG.pop("HOLIDAY_SKIP")

# close():LogOut 出錯也要 Close / Dispose
class Api2:
    def __init__(s): s.done = []
    def LogOut(s): raise RuntimeError("x")
    def Close(s): s.done.append("Close")
    def Dispose(s): s.done.append("Dispose")
b = mk(); a2 = Api2(); b.api = a2; b.logged_in.set(); b._unhook = lambda api: a2.done.append("unhook")
b.close(); ok(a2.done == ["unhook", "Close", "Dispose"] and b.api is None and not b.logged_in.is_set(), a2.done)

# ── R8 自動更新:全部下載 + 編譯成功才換、才寫 VERSION ──
_here, _gh, _rs, _ur = Y.HERE, Y.gh_get, Y.restart_self, Y.upd_ref
UP = TMP / "upd"; UP.mkdir(); (UP / "VERSION").write_text("v1\n", encoding="utf-8")
(UP / "a.py").write_text("A = 1\n", encoding="utf-8"); (UP / "b.py").write_text("B = 1\n", encoding="utf-8")
Y.HERE = UP; Y.upd_ref = lambda: "main"; restarted = []
Y.restart_self = lambda: restarted.append(1)
NOW[0] = dt.datetime(2026, 11, 23, 8, 35, tzinfo=Y.TW)
os.environ.pop("MYSH_UPDATED", None); Y.CFG["AUTO_UPDATE"] = "1"
def gh(files):
    def f(path, ref="main", timeout=30, repo=""):
        name = path.split("/")[-1]
        if name == "VERSION": return b"v2\n"
        if name == "FILES": return b"a.py\nb.py\n"
        return files[name]
    return f
try:
    Y.gh_get = gh({"a.py": b"A = 2\n", "b.py": b"B = (\n"})     # b.py 編譯不過
    Y.self_update()
    ok((UP / "a.py").read_text() == "A = 1\n" and (UP / "VERSION").read_text() == "v1\n" and not restarted)
    Y.gh_get = gh({"a.py": b"A = 2\n", "b.py": b"B = 2\n"})
    Y.self_update()
    ok((UP / "a.py").read_text() == "A = 2\n" and (UP / "b.py").read_text() == "B = 2\n" and (UP / "VERSION").read_text() == "v2\n" and restarted)
    ok((UP / "a.py.bak").read_text() == "A = 1\n" and not any(p.name.startswith(".upd_") for p in UP.iterdir()))
    # 盤中不更新
    (UP / "VERSION").write_text("v1\n", encoding="utf-8"); restarted.clear(); os.environ.pop("MYSH_UPDATED", None)
    NOW[0] = dt.datetime(2026, 11, 23, 10, 0, tzinfo=Y.TW); Y.self_update(); ok((UP / "VERSION").read_text() == "v1\n" and not restarted)
finally:
    Y.HERE, Y.gh_get, Y.restart_self, Y.upd_ref = _here, _gh, _rs, _ur
    os.environ.pop("MYSH_UPDATED", None)


# ── 2026-11-21 使用者「改 A」──
# A5:登入回應分類:帳密 / 憑證類當天停;明確暫時的才退避重試;沒看過的 → unknown
ok(Y.login_kind("0000", "執行失敗") == "fatal" and Y.login_kind("0099", "憑證過期") == "fatal" and Y.login_kind("0098", "身分證字號錯誤") == "fatal")
ok(Y.login_kind("0097", "帳號未啟用") == "fatal" and Y.login_kind("0096", "無權限") == "fatal")
ok(Y.login_kind("0016", "請勿連續進行系統登入作業") == "retry" and Y.login_kind("0095", "系統忙碌,請稍後再試") == "retry" and Y.login_kind("0094", "連線逾時") == "retry")
ok(Y.login_kind("0093", "") == "unknown" and Y.login_kind("abcd", "something") == "unknown")
b = mk(); b.on_response(1, 0, "Login", None, L("0093", "看不懂"))
ok(b.login_soft and b.login_soft_kind == "unknown" and not b.login_fail)
b = mk(); b.on_response(1, 0, "Login", None, L("0000", "執行失敗")); ok(b.login_fail and not b.login_soft)

# A2:同方向的可疑重複:程式記的部位等於哪一份就用哪一份(真的分筆成交 1+1 → 照原樣);都不是才取張數少的
SS = {"code": "2330", "bs": "S", "lots": 1, "price": 100.0, "time": "09:30:01", "order_no": "A1"}
safe, sus, amb = Y.fills_guard([SS, dict(SS)], {"2330": -2}); ok(sus and not amb and sum(t["lots"] for t in safe) == 2, safe)
safe, sus, amb = Y.fills_guard([SS, dict(SS)], {"2330": -1}); ok(sum(t["lots"] for t in safe) == 1, safe)
safe, sus, amb = Y.fills_guard([SS, dict(SS)], {"2330": -5}); ok(sum(t["lots"] for t in safe) == 1, safe)   # 都不是 → 照舊取少的
safe, sus, amb = Y.fills_guard([SS, dict(SS)]); ok(sum(t["lots"] for t in safe) == 1, safe)
# 13:20 因疑似重複少平 → 推手機;程式記的部位 = 照原樣 → 不推
b = mk(); LOGS.clear(); b.guard_trades([SS, dict(SS)], "13:20"); ok(any("疑似重複成交而減少" in a for a in b.alerts), b.alerts)
b = mk(); b.trader.positions = {"2330": {"qty": 2, "dir": -1}}; safe, amb = b.guard_trades([SS, dict(SS)], "13:20")
ok(sum(t["lots"] for t in safe) == 2 and not any("減少" in a for a in b.alerts) and any("真的分筆成交" in x for x in LOGS), (b.alerts, LOGS[-3:]))
b = mk(); b.guard_trades([SS, dict(SS)], "對帳"); ok(not any("減少" in a for a in b.alerts))   # 盤中對帳不推(收盤平倉才推)

# A1 / A6:13:20 先照程式部位送平倉單,不等查詢;查詢有時間上限;確認掛單時不取消程式剛送的平倉單;沒登入 / 送不出去 → 之後每圈再做
class T2(T):
    def __init__(s):
        T.__init__(s); s.fail_codes = set(); s.recheck_fail = set()
    def close_now(s, at=None, codes=None):
        s.calls.append(("close_now", None if codes is None else set(codes)))
        want = {"2330", "2317"} if codes is None else set(codes)
        f = want & s.fail_codes; return want - f, f
    def recheck(s, trades, working=(), at=None):
        s.calls.append(("recheck", list(trades), list(working), set(s.ignore))); s.recheck_fail = set()
def mk2():
    b = mk(); b.trader = T2(); b.logged_in.set(); return b
b = mk2(); order = []; caps = []
def q_slow(with_oco=True):
    order.append("q"); caps.append(b.__dict__.get("_qcap")); return None
b.query_today = q_slow; b.query_strategies = lambda types=(1, 2, 3, 4, 5): []
b.prehang_pick = lambda at: (order.append("pick"), set())[1]
_tc = b.trader.calls
NOW[0] = dt.datetime(2026, 11, 23, 13, 20, 1, tzinfo=Y.TW)
t0 = time.time(); r1 = b.close_all_step("13:20"); ok(r1 is False and time.time() - t0 < 5)
ok([c[0] for c in _tc[:2]] == ["prep", "close_now"] and order[0] == "pick", (_tc, order))   # 平倉單在任何查詢之前
ok(caps and all(c is not None and c <= 8 for c in caps), caps)   # 平倉路上的查詢最多等 8 秒
ok(b._ca["fast"] == {"2330", "2317"} and b._ca["ph"] == "confirm")
# 確認掛單:程式 13:20 之後送的平倉單(T1320…)、程式記著的平倉單不取消;手動 / 舊的取消
ok(Y.Bridge._is_mine_close({"tag": "EwodrT132001002"}, "13:20") and Y.Bridge._is_mine_close({"tag": "R132005 2330"}, "13:20"))
ok(not Y.Bridge._is_mine_close({"tag": "T131500001"}, "13:20") and not Y.Bridge._is_mine_close({"tag": ""}, "13:20") and Y.Bridge._is_mine_close({"tag": "T131500001"}, "13:20", {"T131500001"}))
cancels = []
b.cancel_order = lambda no, code, bs=None, typ=None: cancels.append(no)
W = [{"no": "M1", "code": "2603", "bs": "S", "lots": 1, "typ": "0", "tag": ""}, {"no": "Z9", "code": "2330", "bs": "B", "lots": 1, "typ": "0", "tag": "EwodrT132001002"}]
b.query_today = lambda with_oco=True: {"working": W, "trades": [], "tags": {}}
b._ca["next"] = 0; _sl = Y.time.sleep; Y.time.sleep = lambda s_: None
try:
    ok(b.close_all_step("13:20") is False and cancels == ["M1"], cancels)
    b.query_today = lambda with_oco=True: {"working": W[1:], "trades": [], "tags": {}}
    b._ca["next"] = 0; ok(b.close_all_step("13:20") is True and b._ca["ph"] == "done")   # 只剩自己的平倉單 = 確認 → 複查 → 做完
    rc = [c for c in _tc if c[0] == "recheck"][-1]; ok([w["no"] for w in rc[2]] == ["Z9"], rc[2])   # 自己的平倉單算「已掛著」,不重複下單
    # A6:沒登入 → 什麼都不做、不算做完;登入後才送;送不出去的檔之後每圈重送,全部送出才算做完
    b = mk2(); b.logged_in.clear(); b.trader.fail_codes = {"2317"}
    b.query_today = lambda with_oco=True: {"working": [], "trades": [], "tags": {}}; b.query_strategies = lambda types=(1, 2, 3, 4, 5): []
    b.prehang_pick = lambda at: set()
    ok(b.close_all_step("13:20") is False and not b.trader.calls)
    b.logged_in.set(); ok(b.close_all_step("13:20") is False and b._ca["fail"] == {"2317"} and any("送不出去" in a for a in b.alerts), b._ca)
    n_ = 0
    while b._ca["ph"] != "done" and n_ < 10:
        b._ca["next"] = 0; n_ += 1
        if n_ == 3: b.trader.fail_codes = set()
        b.close_all_step("13:20")
    ok(b._ca["ph"] == "done" and ("close_now", {"2317"}) in b.trader.calls, b.trader.calls)
    ok(sum(1 for c in b.trader.calls if c[0] == "close_now" and c[1] is None) == 1)   # 第一次全部送,之後只重送失敗的
    # 手動預掛到期:一次只確認一輪,還沒取消掉 → 回 False(主迴圈下一圈再做),確認後才複查
    b = mk2(); b.trader.prehang = {"2603": ["P1"]}; b.trader.prehang_deadline = "13:25"; cancels = []
    b.cancel_order = lambda no, code, bs=None, typ=None: cancels.append(no)
    WP = [{"no": "P1", "code": "2603", "bs": "S", "lots": 1, "typ": "0", "tag": ""}]
    b.query_today = lambda with_oco=True: {"working": WP, "trades": [], "tags": {}}
    NOW[0] = dt.datetime(2026, 11, 23, 13, 25, 1, tzinfo=Y.TW)
    ok(b.recheck_positions("13:25") is False and cancels == ["P1"] and not [c for c in b.trader.calls if c[0] == "recheck"])
    b.query_today = lambda with_oco=True: {"working": [], "trades": [], "tags": {}}; b._phx["next"] = 0
    ok(b.recheck_positions("13:25") is True and [c for c in b.trader.calls if c[0] == "recheck"])
finally:
    Y.time.sleep = _sl

# A7:Worker 一直不收的那一分鐘試 3 次就丟,後面照推;前幾天的佇列換日清掉;佇列有上限
b7 = mk(); Y.CFG["PUSH_KEY"] = "k"; Y.CFG["WORKER_URL"] = "http://127.0.0.1:9"; pushed = []
def push7(d, m, bars, timeout=None):
    pushed.append(m); return "perm" if m == "09:05" else True
b7.push = push7
for mm in ("09:05", "09:06", "09:07"): b7.push_async("2026-11-23", mm, {"2330": [1, 1, 1, 1, 1]})
t_end = time.time() + 15
while time.time() < t_end and len(b7._pq_st["ok"]) < 2:
    b7._pq_st["next"] = 0; b7._pq_st["ev"].set(); time.sleep(0.1)
ok(sorted(m for _, m in b7._pq_st["ok"]) == ["09:06", "09:07"] and pushed.count("09:05") == 3 and not b7._pq_st["pend"], (pushed, b7._pq_st))
b7.push = lambda d, m, bars, timeout=None: False   # Worker 掛了:留在佇列
for mm in ("13:00", "13:01"): b7.push_async("2026-11-20", mm, {"2330": [1, 1, 1, 1, 1]})
b7.push_async("2026-11-23", "13:02", {"2330": [1, 1, 1, 1, 1]})
NOW[0] = dt.datetime(2026, 11, 23, 8, 40, tzinfo=Y.TW); b7.day_reset()
ok(set(b7._pq_st["pend"]) | ({b7._pq_st["busy"]} - {None}) == {("2026-11-23", "13:02")}, b7._pq_st["pend"])
Y.CFG["PUSH_QMAX"] = "10"
for i in range(15): b7.push_async("2026-11-23", f"10:{i:02d}", {"2330": [1, 1, 1, 1, 1]})
ok(len(b7._pq_st["pend"]) <= 10 and ("2026-11-23", "10:14") in b7._pq_st["pend"], len(b7._pq_st["pend"]))
for k_ in ("PUSH_QMAX", "PUSH_KEY", "WORKER_URL"): Y.CFG.pop(k_, None)

# A4:即時成交回報的成交時間(不用委託時間)
b = mk(); ok(b.rr_time(types.SimpleNamespace(TradeTime="09:45:10.123", OrderTime="09:30:00")) == "09:45:10.123")
ok(b.rr_time(types.SimpleNamespace(OrderTime="09:30:00")) == "")
Y.CFG["RR_TIME_FIELD"] = "MyTm"; ok(b.rr_time(types.SimpleNamespace(MyTm="101010")) == "101010"); Y.CFG.pop("RR_TIME_FIELD")

# A9:雲端讀不到 → 等夠久 / 連續讀不到就用本機通知檔 prime
class T9(T):
    primed = False
    def prime_local(s, items, why=""): s.calls.append(("prime_local", list(items))); s.primed = True; return True
ED = TMP / "eng9"; ED.mkdir(); os.environ["ENGINE_DIR"] = str(ED)
(ED / "live_2026-11-23.jsonl").write_text('{"_k": "a", "t": "09:20"}\n{"_k": "b", "t": "09:21"}\n', encoding="utf-8")
b = mk(); b.trader = T9(); b._prime_t0 = time.time(); b._sf_bad = 0
b.prime_local_maybe("2026-11-23"); ok(not b.trader.calls)                     # 還沒等夠、雲端也還沒明顯掛掉
b._sf_bad = 5; b.prime_local_maybe("2026-11-23")
ok(b.trader.calls and [x["_k"] for x in b.trader.calls[-1][1]] == ["a", "b"] and any("本機訊號" in a for a in b.alerts), b.trader.calls)
b = mk(); b.trader = T9(); b._prime_t0 = time.time() - 200; b._sf_bad = 0; b.prime_local_maybe("2026-11-23"); ok(b.trader.calls)
os.environ.pop("ENGINE_DIR", None)

# ── 2026-11-22 使用者「全部做」(U2 / U4 / D7)──
_sl22 = Y.time.sleep; Y.time.sleep = lambda s: None
try:
    S17 = {"code": "2317", "bs": "S", "lots": 1, "price": 200.0, "time": "09:35:00", "order_no": "Z1"}
    W17 = {"no": "M1", "code": "2317", "bs": "B", "lots": 1, "typ": "0", "tag": "", "price": 199.0}
    def mk22():
        b = mk(); b.logged_in.set(); b.trader.prehang_deadline = "13:25"; b.trader.prehang = {"2317": ["M1"]}
        b.cancels = []; b.cancel_order = lambda no, code, bs, typ: b.cancels.append(no)
        b.query_strategies = lambda types=(1, 2, 3, 4, 5): []
        return b
    NOW[0] = dt.datetime(2026, 11, 23, 13, 20, 1, tzinfo=Y.TW)
    # U2-1:查詢失敗 → 回 None、盤中記的預掛不動;查成功才換
    b = mk22(); b.query_today = lambda with_oco=True: None
    ok(b.prehang_pick("13:20") is None and b.trader.prehang == {"2317": ["M1"]}, b.trader.prehang)
    b.query_today = lambda with_oco=True: {"working": [W17], "trades": [S17], "tags": {}}
    ok(b.prehang_pick("13:20") == {"M1"} and b.trader.prehang == {"2317": ["M1"]})
    b.query_today = lambda with_oco=True: {"working": [], "trades": [S17], "tags": {}}
    ok(b.prehang_pick("13:20") == set() and b.trader.prehang == {})   # 查成功、預掛不在了 → 清掉
    # U2-2:13:20 流程查預掛前 2 次查不到 → 重查,第 3 次查到 → 保留預掛、不取消
    b = mk22(); qn22 = [0]
    def q22(with_oco=True):
        qn22[0] += 1
        return None if qn22[0] <= 2 else {"working": [W17], "trades": [S17], "tags": {}}
    b.query_today = q22; LOGS.clear(); b.close_all("13:20")
    ok(b._ca["keep"] == {"M1"} and not b.cancels and b.trader.prehang == {"2317": ["M1"]}, b._ca, b.cancels, LOGS)
    ok(sum("查手動預掛:元大委託查不到" in x for x in LOGS) == 2, LOGS)
    rc = [c for c in b.trader.calls if c[0] == "recheck"][-1]; ok([w["no"] for w in rc[2]] == ["M1"], rc)
    # U2-3:一直查不到(超過 CLOSE_CONFIRM_TRIES)→ 保留盤中看到的預掛、推手機,確認時不取消它
    b = mk22(); qn22[0] = 0
    def q22b(with_oco=True):
        qn22[0] += 1
        return None if qn22[0] <= 6 else {"working": [W17, dict(W17, no="X9", tag="", code="2330", bs="S")], "trades": [S17], "tags": {}}
    b.query_today = q22b; b.close_all("13:20")
    ok(b._ca["keep"] == {"M1"} and set(b.cancels) == {"X9"} and any("查不到元大委託" in a for a in b.alerts), b._ca["keep"], b.cancels, b.alerts)
    # U4:複查查不到 → 回 "fail";同一分鐘內不推手機,過了這分鐘才推
    b = mk22(); b.trader.prehang = {}; b.trader.alerts = []; b.trader.alert = lambda a, body="": b.trader.alerts.append(a)
    b.query_today = lambda with_oco=True: None
    NOW[0] = dt.datetime(2026, 11, 23, 13, 22, 5, tzinfo=Y.TW)
    ok(b.recheck_positions("13:22") == "fail" and not b.trader.alerts)
    NOW[0] = dt.datetime(2026, 11, 23, 13, 23, 0, tzinfo=Y.TW)
    ok(b.recheck_positions("13:22") == "fail" and b.trader.alerts == ["⚠ 13:22 複查失敗"], b.trader.alerts)
    b.query_today = lambda with_oco=True: {"working": [], "trades": [S17], "tags": {}}
    ok(b.recheck_positions("13:23") is True and b.trader.calls[-1][0] == "recheck")
finally:
    Y.time.sleep = _sl22

# D7:名單外股票的可當沖 / 可先賣
import elig as _el
_gj, _lt, _here = _el.get_json, _el.load_today, Y.HERE
try:
    NOW[0] = dt.datetime(2026, 11, 23, 10, 0, tzinfo=Y.TW)
    def _fake_lists(url, params=None, timeout=40, tries=2):
        if url == _el.TWSE_DT: return [{"Code": "2330", "Suspension": ""}, {"Code": "2317", "Suspension": "Y"}]
        if url == _el.TPEX_DT: return [{"SecuritiesCompanyCode": "6269"}, {"SecuritiesCompanyCode": "3105"}]
        if url == _el.TPEX_DT_PAUSE: return [{"SecuritiesCompanyCode": "6269"}]
        raise RuntimeError("x")
    _el.get_json = _fake_lists; _el.load_today = lambda: {"noshort": ["3105"]}
    b = mk()
    ok(b.extra_dt("2330")[:2] == (1, 1) and b.extra_dt("2317")[:2] == (1, 0), b.extra_dt("2317"))
    ok(b.extra_dt("6269", True)[:2] == (1, 0) and b.extra_dt("3105", True)[:2] == (1, 0) and b.extra_dt("1101")[:2] == (0, 0))
    b.trader.noshort = {"2330": "無券"}; ok(b.extra_dt("2330")[:2] == (1, 0))
    # 官方名單抓不到 → 本機最近的名單檔;也沒有 → 保守不可先賣
    _el.get_json = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("斷網")); _el.load_today = lambda: None
    Y.HERE = TMP; (TMP / "pool_2026-11-21.json").write_text('{"pool": [{"code": "3008", "daytrade": 1, "short_first": 1}, {"code": "2603", "daytrade": 1, "short_first": 0}]}', encoding="utf-8")
    (TMP / "pool_2026-10-01.json").write_text('{"pool": [{"code": "2002", "daytrade": 1, "short_first": 1}]}', encoding="utf-8")   # 太舊不用
    b = mk()
    ok(b.extra_dt("3008")[:2] == (1, 1) and b.extra_dt("2603")[:2] == (1, 0) and b.extra_dt("2002")[:2] == (1, 0) and b.extra_dt("9999")[:2] == (1, 0), b.extra_dt("2002"))
    # 2026-11-26 情境回放:注意股原本只留名單裡的 → 名單外手動部位(例:富喬 1815 注意股)mon_excluded / blocked 認不出來;全市場那份另存 all_attention,名單內照舊
    _bl = {"2330": "", "1815": "", "3105": ""}
    def _fake6(url, params=None, timeout=40, tries=2):
        if url == _el.TWSE_DT: return [{"Code": "2330", "Suspension": ""}, {"Code": "1815", "Suspension": ""}]
        if url == _el.TPEX_DT: return [{"SecuritiesCompanyCode": "3105"}]
        if url == _el.TWSE_NOTICE: return [{"Code": "1815"}, {"Code": "2330"}]
        return []
    _el.get_json = _fake6; _sr0, _st0 = _el.site_rows, _el.site_punish; _el.site_rows = lambda *a, **k: []; _el.site_punish = lambda t: set()
    _el.load_today = _lt; Y.HERE = _here; _elh = _el.HERE; _el.HERE = TMP
    try:
        r6 = _el.refresh({"WORKER_URL": ""}, log=lambda *a: None, pool={"date": "2026-11-23", "pool": [{"code": "2330", "market": "twse"}, {"code": "3105", "market": "tpex"}]})
        ok(r6["attention"] == ["2330"] and "1815" in r6["all_attention"], (r6["attention"], r6.get("all_attention")))   # attention 照舊只有名單內
        ok(_el.blocked("1815") == "今天是注意股" and _el.blocked("2330") == "今天是注意股" and _el.blocked("3105") == "", [_el.blocked(c) for c in ("1815", "2330", "3105")])
    finally: _el.HERE = _elh; _el.site_rows, _el.site_punish = _sr0, _st0
    print("情境回放 6:名單外注意股也認得 OK")
finally:
    _el.get_json, _el.load_today, Y.HERE = _gj, _lt, _here

# 2026-11-22 結算日 13:30 以後台指期改收下個月(判別式聊天轉達:11/18 只到 13:29 → 11/19 ⑧ 強弱偏 0.82%)
import json as _json, io as _io
_uo, _th, _here2 = Y.urllib.request.urlopen, Y.threading.Thread, Y.HERE
try:
    Y.HERE = TMP
    class _SyncT:
        def __init__(s, target=None, daemon=None): s.t = target
        def start(s): s.t()
    Y.threading.Thread = _SyncT
    Q = {}
    Y.urllib.request.urlopen = lambda rq, timeout=None: _io.BytesIO(_json.dumps({"RtData": {"QuoteList": Q["L"]}}).encode())
    b = Y.Bridge.__new__(Y.Bridge); b.fut_n = None; b.fut_src_save = lambda *a: None
    ok(b.fut_settle_next(dt.date(2026, 12, 16)) == (2027, 1) and b.fut_settle_next(dt.date(2026, 12, 15)) is None and b.fut_settle_next(dt.date(2026, 12, 17)) is None, "結算日判斷")
    def poll(hm, near_px, nx_px, near_t, nx_t):
        NOW[0] = dt.datetime(2026, 12, 16, int(hm[:2]), int(hm[3:]), 5, tzinfo=Y.TW); b._tf_at = 0
        Q["L"] = [{"SymbolID": "TXFL6-F", "CLastPrice": str(near_px), "CTotalVolume": "100", "CTime": near_t},
                  {"SymbolID": "TXFA7-F", "CLastPrice": str(nx_px), "CTotalVolume": "50", "CTime": nx_t}]
        b.fut_poll()
    poll("13:28", 40000, 40300, "132800", "132800")
    poll("13:29", 40001, 40301, "132900", "132900")
    poll("13:30", 40002, 40302, "133000", "133000")   # 13:30 以後:收下個月
    poll("13:44", 40002, 40310, "133000", "134400")
    ok(b.fut_tf["13:29"][3] == 40001 and b.fut_tf["13:30"][3] == 40302 and b.fut_tf["13:44"][3] == 40310, b.fut_tf)
    NOW[0] = dt.datetime(2026, 12, 16, 13, 46, 0, tzinfo=Y.TW); b.fut_flush()
    rows = open(TMP / "kline_futures" / "TXF" / "2026-12-16.csv", encoding="utf-8").read().split()
    ok(rows[-1].split(",")[1] == "13:44" and rows[-1].split(",")[5] == "40310.0", rows[-3:])
    # 一般日子不變:13:30 以後照收近月
    b2 = Y.Bridge.__new__(Y.Bridge); b2.fut_n = None; b2.fut_src_save = lambda *a: None; b = b2
    NOW[0] = dt.datetime(2026, 12, 15, 13, 40, 5, tzinfo=Y.TW); b._tf_at = 0
    Q["L"] = [{"SymbolID": "TXFL6-F", "CLastPrice": "40100", "CTotalVolume": "100", "CTime": "134000"}, {"SymbolID": "TXFA7-F", "CLastPrice": "40400", "CTotalVolume": "50", "CTime": "134000"}]
    b.fut_poll(); ok(b.fut_tf["13:40"][3] == 40100, b.fut_tf)
finally:
    Y.urllib.request.urlopen, Y.threading.Thread, Y.HERE = _uo, _th, _here2

print(f"test_bridge 全部通過({N_OK[0]} 項)")

# 2026-11-26 元大成交時間 12 小時制(下午 01:20 = 13:20):先進先出配對不能排錯(聯電 / 台玻明細變做多)
import yuanta_bridge as _YB
assert _YB.hms24("2026/11/26 下午 01:20:01") == "13:20:01" and _YB.hms24("2026/11/26 上午 11:02:25") == "11:02:25" and _YB.hms24("下午 12:11:26") == "12:11:26"
_L = _YB.fills_legs([{"code": "2303", "bs": "S", "lots": 2, "price": 88.6, "time": "2026/11/26 上午 11:02:25"}, {"code": "2303", "bs": "S", "lots": 2, "price": 89.7, "time": "2026/11/26 下午 12:38:11"},
                     {"code": "2303", "bs": "B", "lots": 4, "price": 89.5, "time": "2026/11/26 下午 01:20:01"}])
assert len(_L) == 2 and all(g["dir"] == "做空" for g in _L), _L
print("12 小時制成交時間配對 OK")

# 2026-11-26 舊版存下的錯配明細 → legs_fix 重新配對,總淨額不變
_old = [{"code": "1802", "name": "台玻", "dir": "做多", "ent": 59.6, "ex": 59.3, "lots": 4, "t0": "2026/11/26 下午 01:20:01", "t": "2026/11/26 下午 12:57:09", "gross": -1200, "fee": 524, "tax": 0, "net": -1724},
        {"code": "1802", "name": "台玻", "dir": "做空", "ent": 59.4, "ex": 58.6, "lots": 4, "t0": "2026/11/26 上午 10:34:48", "t": "2026/11/26 上午 10:53:27", "gross": 3200, "fee": 523, "tax": 0, "net": 2677},
        {"code": "1802", "name": "台玻", "dir": "做多", "ent": 59.6, "ex": 58.6, "lots": 4, "t0": "2026/11/26 下午 01:20:01", "t": "2026/11/26 下午 12:11:26", "gross": -4000, "fee": 518, "tax": 0, "net": -4518}]
_new = _YB.legs_fix(_old)
assert all(g["dir"] == "做空" for g in _new) and sum(g["net"] for g in _new) == sum(g["net"] for g in _old) and _new[0]["t"] == "13:20:01", _new
assert _YB.legs_fix(_new) == _new
print("舊明細重新配對 OK")

# ── 2026-11-26 使用者「全改」稽核 8 / 10 回歸測試 ──
import json as _js, types as _ty, threading as _thr, time as _tm
_uo26 = _YB.urllib.request.urlopen
try:
    # 2026-11-26 稽核 8:ref_need(trader 在交易鎖裡叫)只開背景執行緒查昨收,馬上回(不等網路)
    b8 = mk(); b8.quotes = _ty.SimpleNamespace(meta={}, q={}, lock=_thr.Lock()); _ev = _thr.Event()
    b8.ensure_ref = lambda c, to=5: (_tm.sleep(3), _ev.set())
    _t0 = _tm.time(); r8 = b8.ref_need_bg("1815"); ok(r8 is False and _tm.time() - _t0 < 0.5, "ref_need 不能在鎖裡等網路", _tm.time() - _t0)
    b8.quotes.meta["1815"] = {"y": 101.5}; ok(b8.ref_need_bg("1815") is True)
    # 2026-11-26 稽核 10:tick_guard 只餵真的成交價(沒成交不拿買賣價中間)
    RESP = {"msgArray": [{"z": "-", "b": "37.10_37.05_", "a": "37.20_37.25_", "y": "37.00"}]}
    _YB.urllib.request.urlopen = lambda *a, **k: _ty.SimpleNamespace(read=lambda: _js.dumps(RESP).encode())
    b10 = mk(); b10.quotes = _ty.SimpleNamespace(meta={"2409": {"y": 37.0, "ex": "tse"}}, q={}, lock=_thr.Lock()); b10.pending_sub = []; b10.mkt_of_code = lambda c: None
    TK = []; b10.trader = _ty.SimpleNamespace(on_tick=lambda c, z: TK.append((c, z)))
    b10.tick_guard("2409"); ok(not TK, "沒成交不能餵買賣價中間", TK)
    RESP["msgArray"][0]["z"] = "37.15"; b10.tick_guard("2409"); ok(TK == [("2409", 37.15)], TK)
    # 2026-11-26 稽核 10:名單外股票的昨收先用證交所 y(ensure_ref),查不到才用前一天最後一根 1 分 K 收盤
    _here26 = _YB.HERE; _YB.HERE = TMP
    def _mk_x(ref_ok):
        bx = mk(); bx.quotes = _ty.SimpleNamespace(meta={}, q={}, lock=_thr.Lock(), seed=lambda *a: None); bx.pending_sub = []
        bx.Y = _ty.SimpleNamespace(enumMarketType=_ty.SimpleNamespace(TWOTC="otc", TWSE="tse"))
        bx.kline_query = lambda code, mkt, d: [(d, "09:01", 40.0, 40.0, 40.0, 41.0 if d == NOW[0].strftime("%Y-%m-%d") else 48.0, 1000)]
        def _er(c, to=5):
            if ref_ok: bx.quotes.meta.setdefault(c, {"n": "", "ex": "tse", "y": None})["y"] = 50.0
            return ref_ok
        bx.ensure_ref = _er; bx.extra_dt = lambda *a: (_ for _ in ()).throw(RuntimeError("測試到這裡就好"))
        return bx
    bx1 = _mk_x(True); bx1.extra_add("9999", "tse"); ok(bx1.quotes.meta["9999"]["y"] == 50.0, "證交所查得到 → 用 y", bx1.quotes.meta)
    bx2 = _mk_x(False); bx2.extra_add("9998", "tse"); ok(bx2.quotes.meta["9998"]["y"] == 48.0, "查不到 → 前一天最後一根", bx2.quotes.meta)
    _YB.HERE = _here26
    # 2026-11-26 情境回放:備援模式(沒元大逐筆)原本 30 秒才問一次證交所 → 富喬 11:47 108.5 這種尖峰會漏;改成這檔一條背景每 5 秒(測試 0.05 秒)問,不重疊,逐筆回來 / 部位沒了就停
    NOW[0] = dt.datetime(2026, 11, 23, 11, 47, 0, tzinfo=Y.TW)
    ZS = ["107.0", "107.5", "108.5", "108.0"]; CALLS = [0]; INFL = [0, 0]
    def _uo5(*a, **k):
        INFL[0] += 1; INFL[1] = max(INFL[1], INFL[0]); _tm.sleep(0.01); INFL[0] -= 1
        z = ZS[min(CALLS[0], len(ZS) - 1)]; CALLS[0] += 1
        return _ty.SimpleNamespace(read=lambda: _js.dumps({"msgArray": [{"z": z, "y": "101.50"}]}).encode())
    _YB.urllib.request.urlopen = _uo5
    b5 = mk(); b5.quotes = _ty.SimpleNamespace(meta={"1815": {"y": 101.5, "ex": "tse"}}, q={}, lock=_thr.Lock()); b5.pending_sub = []; b5.mkt_of_code = lambda c: None; b5.tg_fb_s = 0.05
    TK5 = []; b5.trader = _ty.SimpleNamespace(on_tick=lambda c, z: TK5.append(z), positions={"1815": {"code": "1815", "qty": 2}})
    for _ in range(3): b5.tick_guard("1815")   # 主迴圈連叫 3 次 → 只開一條
    ok(sum(1 for t_ in _thr.enumerate() if t_.name == "tick_fb_1815") == 1, [t_.name for t_ in _thr.enumerate()])
    _tm.sleep(0.4); ok(108.5 in TK5 and len(TK5) >= 4 and INFL[1] == 1, (TK5, INFL))   # 30 秒內問了好幾次、抓到 108.5、同時最多一個請求
    b5.quotes.q["1815"] = {"t": "11:47:00", "d": "20261123"}; _tm.sleep(0.2)   # 元大逐筆回來 → 停
    ok(not b5._tg_th["1815"].is_alive(), "逐筆回來背景要停"); n5 = len(TK5); _tm.sleep(0.15); ok(len(TK5) == n5)
    b5.quotes.q.clear(); b5.tick_guard("1815"); _tm.sleep(0.1); ok(b5._tg_th["1815"].is_alive() and len(TK5) > n5, "又斷 → 重開一條")
    b5.trader.positions.clear(); _tm.sleep(0.2); ok(not b5._tg_th["1815"].is_alive(), "部位沒了背景要停")
    print("情境回放 5:持倉備援每 5 秒問證交所、一檔一條 OK")
finally:
    _YB.urllib.request.urlopen = _uo26
print("稽核 8 / 10 中繼昨收背景查、只餵成交價 OK")
