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
ok(Y.login_kind("0000", "執行失敗") == "retry" and Y.login_kind("7777", "沒看過") == "retry")
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

print(f"test_bridge 全部通過({N_OK[0]} 項)")
