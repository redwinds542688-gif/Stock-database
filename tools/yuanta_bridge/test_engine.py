"""python3 test_engine.py:電腦引擎(engine.py)E5~E12 + 寬度自己算的測試(不連雲端、不跑真的模擬器)
2026-11-22 使用者「全部做」(E5~E12、寬度整份名單)"""
import os, sys, json, time, tempfile, threading
from pathlib import Path
from multiprocessing import Pool

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
TMP = Path(tempfile.mkdtemp())
os.environ["ENGINE_DIR"] = str(TMP / "data")
os.environ["BRIDGE_ENV"] = str(TMP / "none.env")   # 不讀真的 bridge.env
os.environ["LOG_DIR"] = str(TMP / "log")
import engine as E

E.DATA = TMP / "data"; E.DATA.mkdir(parents=True, exist_ok=True)
E.SIMD = TMP / "sim"; E.SIMD.mkdir(parents=True, exist_ok=True)
E.CFG.clear(); E.CFG.update({"LOG_DIR": str(TMP / "log"), "WORKER_URL": "http://127.0.0.1:9", "ENGINE_GROUP": "0"})
DAY = "2026-11-20"


def ok(cond, *msg):
    assert cond, msg


def mk(**kw):
    """不跑 __init__(不開工作行程、不讀私人庫):對帳 / 算工作清單要用到的欄位自己填"""
    e = object.__new__(E.Engine)
    e.day, e.dry = DAY, True
    e.pos = {}; e.emitted = set(); e.cloud_in = {}; e.cloud_out = set(); e.fclosed = set()
    e.j12 = set(); e.j63 = set(); e.j9 = set(); e.j8 = set(); e.j5 = set(); e.j4 = set()
    e._cq = []; e._cq_lock = threading.Lock(); e._cq_th = None
    e.closed = []; e.lat = []; e.rounds = []; e.drag = set(); e.no_bar_note = set()
    e.sent = []; e.px = {}; e.last_hm = {}; e.held_note = set(); e.last_done = {}
    e.state_f = None; e.cap = 250000; e.upto_hm = ""
    e.codes = ["2330", "2317"]; e.names = {"2330": "台積電", "2317": "鴻海"}; e.prev = {"2330": 100.0, "2317": 100.0}
    e.mkts = {}; e.canshort = {"2330": True, "2317": True}; e.no_new = set(); e.uni = {}
    e.rs_pri = None; e.tx_req = (); e.envs = {}; e._env_last = {}
    e.br_rs = set()
    e.__dict__.update(kw)
    return e


class FakePool:
    def map(self, fn, jobs, chunksize=1): return [(j[0], j[2], "", "") for j in jobs]
    def imap(self, fn, jobs, chunksize=1): return iter(self.map(fn, jobs))


# ── E5:工作行程規則檔換了 → 丟掉舊的 rs_env(sim_day.py 的快取本來就照修改時間 / 大小)──
RSF = E.SIMD / "rs_env.py"
RSF.write_text("VAL = 1\n", encoding="utf-8")


def _worker_val(_):
    E._simd_fresh(); sys.path.insert(0, str(E.SIMD))
    import rs_env
    return rs_env.VAL


if __name__ == "__main__":
    sys.path.insert(0, str(E.SIMD))
    import rs_env as _r0
    ok(_r0.VAL == 1)
    with Pool(1) as pool:   # Linux fork:工作行程一開始就帶著主程式已 import 的 rs_env
        ok(pool.map(_worker_val, [0]) == [1])
        time.sleep(0.01); RSF.write_text("VAL = 22\n", encoding="utf-8")   # 盤中重抓規則(fetch_rules 用 os.replace 換檔)
        ok(pool.map(_worker_val, [0]) == [22], "工作行程還在用舊的 rs_env")
    E._simd_fresh(); ok("rs_env" not in sys.modules)   # 主程式也一樣(reload_envs 沒有模組時會重新 import)
    ok(E._simd_fresh() is False)   # 沒變不再丟
    # sim_day.py 的快取(_sim_code / _ss_split)照修改時間 / 大小重做
    SD = E.SIMD / "sim_day.py"
    SD.write_text("import os\nX = 1\nif os.environ.get('MODE','one') == 'rf':\n    pass\nprint(X)\n", encoding="utf-8")
    c1 = E._sim_code(); k1 = E._ss_split()["key"]
    time.sleep(0.01); SD.write_text("import os\nX = 22\nif os.environ.get('MODE','one') == 'rf':\n    pass\nprint(X)\n", encoding="utf-8")
    ok(E._sim_code() is not c1 and E._ss_split()["key"] != k1)
    print("E5 工作行程重載 rs_env OK")

    # ── E6:有 KZONE 時最後進場時間 = 訊號最後時間 + 等待(不超過 ENTRYTO)──
    J1 = {"NOENTRYAFTER": "12:00", "ENTRYTO": "12:30", "ENTRYFROM": "09:30", "KZONE": "15"}
    J3 = {"NOENTRYAFTER": "12:30", "ENTRYTO": "12:30", "ENTRYFROM": "09:30"}
    J2 = {"NOENTRYAFTER": "12:30", "ENTRYTO": "12:30", "ENTRYFROM": "09:30", "KZONE": "15", "ENTRYDELAY": "2"}
    e = mk(envs={"一": J1, "三": J3, "二": J2}); e.pool = FakePool()
    def jobs_at(e, hm):
        e.upto_hm = hm; res, _, _ = e.compute(); return {(r[1], r[0]) for r in res}
    ok(("一", "2330") in jobs_at(e, "12:20"), "判一 12:00 + KZONE 15 → 12:15 還能進,12:20 要照算")   # 原本 12:11 就不算
    ok(("一", "2330") in jobs_at(e, "12:26") and ("一", "2330") not in jobs_at(e, "12:27"))
    ok(("三", "2330") in jobs_at(e, "12:41") and ("三", "2330") not in jobs_at(e, "12:42"))   # 沒有等待:和原本一樣 12:30 + 11
    ok(("二", "2330") in jobs_at(e, "12:43") and ("二", "2330") not in jobs_at(e, "12:44"))   # ENTRYDELAY 2:12:32 + 11
    e.pos[("一", "2317")] = {"side": -1, "t": "11:00", "px": 100.0, "lots": 1, "text": "空"}
    ok(("一", "2317") in jobs_at(e, "13:00") and ("一", "2330") not in jobs_at(e, "13:00"))   # 過了時段只算開著的
    print("E6 KZONE 延長最後進場時間 OK")

    # ── E7:規則重載後判別式不見了 → 還開著的部位照舊參數算到平倉,不發新進場 ──
    e = mk(envs={"一": J1}, _env_last={"一": J1, "三": J3}); e.pool = FakePool()
    e.pos[("三", "2317")] = {"side": -1, "t": "10:00", "px": 100.0, "lots": 1, "text": "空"}
    js = jobs_at(e, "11:00")
    ok(("三", "2317") in js and ("三", "2330") not in js and ("一", "2330") in js, js)
    del e.pos[("三", "2317")]
    ok(not any(r == "三" for r, _ in jobs_at(e, "11:01")))   # 部位平了就不再算
    out_new = "10:30 空 價 99.00 xx → 進場 99.00 ×2張\n"
    n0 = len(e.sent); e.apply_one(("2330", "三", out_new, ""), "10:32", "10:22", True, True, {})
    ok(len(e.sent) == n0 and ("三", "2330", "10:30") in e.emitted, e.sent)
    print("E7 被拿掉的判別式照算到平倉 OK")

    # ── E8:「訊號消失」要這檔最後一根收完的 K 到 upto-1(形成中那根不算)──
    e = mk(envs={"三": J3})
    e.pos[("三", "2330")] = {"side": -1, "t": "10:00", "px": 100.0, "lots": 1, "text": "空"}
    e.last_hm = {"2330": "10:30"}; e.last_done = {"2330": "10:20", "2317": "10:29"}   # 2330 的 Worker K 卡在 10:20,形成中那根把 last_hm 推到 10:30
    e.apply_one(("2330", "三", "", ""), "10:30", "10:20", True, True, {})
    ok(("三", "2330") in e.pos and not e.sent, "K 卡住不能判定訊號消失")
    e.last_done["2330"] = "10:29"
    e.apply_one(("2330", "三", "", ""), "10:30", "10:20", True, True, {})
    ok(("三", "2330") not in e.pos and e.sent and "訊號重算消失" in e.sent[-1]["title"], e.sent)
    print("E8 訊號消失看收完的 K OK")

    # ── E9:推完馬上存狀態;開工讀本機通知檔(進場 → 接回不重發、平倉 → 部位結束)──
    e = mk(envs={"三": J3}, state_f=E.DATA / f"engine_state_{DAY}.json")
    e.apply_one(("2330", "三", out_new, ""), "10:32", "10:22", True, True, {})
    st = json.loads(e.state_f.read_text(encoding="utf-8"))
    ok(["三", "2330", "10:30"] in st["emitted"] and "三|2330" in st["pos"], st)   # 這輪還沒結束就已經存了
    lf = E.DATA / f"live_{DAY}.jsonl"
    items = [{"d": DAY, "t": "10:32", "title": "台積電 2330 做空進場 99.00", "body": "10:30 空 · 停利 96.00 停損 101.00 · 2張 · 判三", "kind": "short", "key": "k1"},
             {"d": DAY, "t": "11:05", "title": "鴻海 2317 出場 +1.0%:超時", "body": "11:05 做空 10:00 進 100.00 → 現價 99.00 · 1張 · 判三", "kind": "exit", "key": "k2", "pos": "判三|2317|10:00"},
             {"d": DAY, "t": "10:40", "title": "鴻海 2317 做空進場 50.00", "body": "10:39 空 · 停利 48.00 停損 51.00 · 4張 · 判五", "kind": "short", "key": "k3"}]
    lf.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in items), encoding="utf-8")
    e = mk(envs={"三": J3})
    e.pos[("三", "2317")] = {"side": -1, "t": "10:00", "px": 100.0, "lots": 1, "text": "空"}   # 當機前推了平倉、還沒存檔
    e.load_local_sent()
    ok(("三", "2330", "10:30") in e.cloud_in and ("三", "2317", "10:00") in e.cloud_out and "2317" in e.j5, (e.cloud_in, e.cloud_out))
    e.apply_one(("2330", "三", out_new, ""), "10:33", "10:23", True, True, {})
    ok(not e.sent and e.pos.get(("三", "2330"), {}).get("px") == 99.0, "本機已發的進場要接回、不重發")
    e.apply_one(("2317", "三", "", ""), "11:06", "10:56", True, True, {})
    ok(("三", "2317") not in e.pos and not e.sent, "本機已發的平倉不重推")
    lf.unlink()
    print("E9 推完即存 / 開工讀本機通知檔 OK")

    # ── E10 + E12:本機通知檔兩條執行緒一起寫不交錯;進場結果背景一起等、照順序推雲端 ──
    E.CFG["ENGINE_VERDICT_WAIT"] = "1"
    e = mk(envs={"三": J3}); e.dry = False
    sent_cloud = []; e._cloud_send = lambda it, kk: (sent_cloud.append((time.time(), it["title"], kk)), True)[1]
    vf = E.DATA / f"verdict_{DAY}.jsonl"
    big = "x" * 3000
    def spam(tag):
        for i in range(150): e.push({"d": DAY, "t": "10:00", "title": f"{tag}{i}", "body": big, "kind": "lv2", "key": f"{tag}{i}"})
    ths = [threading.Thread(target=spam, args=(t,)) for t in ("A", "B")]
    for t in ths: t.start()
    for t in ths: t.join()
    rows = [json.loads(x) for x in lf.read_text(encoding="utf-8").splitlines()]
    ok(len(rows) == 300 and all(r["body"] == big for r in rows))
    sent_cloud.clear(); t0 = time.time()
    for i in range(3): e.push({"d": DAY, "t": "10:31", "title": f"股{i} 233{i} 做空進場 99.00", "body": "10:30 空 · 停利 96.00 停損 101.00 · 2張 · 判三", "kind": "short", "key": f"v{i}"})
    t_main = time.time() - t0
    k1 = "10:31:v1"
    vf.write_text(json.dumps({"_k": k1, "ok": False, "why": "額度滿"}, ensure_ascii=False) + "\n", encoding="utf-8")
    e.vq_join(10); t_all = time.time() - t0
    ok(t_main < 0.5, f"主程式不能被等待卡住 {t_main:.2f}s")
    ok([x[1] for x in sent_cloud] == ["股0 2330 做空進場 99.00", "股1 2331 做空未進場 99.00", "股2 2332 做空進場 99.00"], sent_cloud)
    ok(t_all < 2.0, f"三則一起等,總共約 1 秒(原本一則一則等 3 秒):{t_all:.2f}s")
    E.CFG["ENGINE_VERDICT_ASYNC"] = "0"; sent_cloud.clear(); t0 = time.time()
    e.push({"d": DAY, "t": "10:32", "title": "股9 2339 做空進場 99.00", "body": "10:31 空 · 判三", "kind": "short", "key": "v9"})
    ok(time.time() - t0 >= 0.9 and len(sent_cloud) == 1)   # 關掉 → 照舊在主程式等
    E.CFG.pop("ENGINE_VERDICT_ASYNC"); E.CFG.pop("ENGINE_VERDICT_WAIT")
    print("E10 本機通知檔加鎖 / E12 進場結果背景一起等 OK")

    # ── E11:每分鐘增量 K 短逾時、整天重抓長一點 ──
    seen = []
    _http0 = E.http
    E.http = lambda url, data=None, key=False, timeout=20: (seen.append((url, timeout)), {"bars": {}, "last": "10:00"})[1]
    try:
        E.today_bars(DAY, since="09:57"); E.today_bars(DAY)
        E._TB.update(d="", rows={}, last="", full_at=0.0); E.today_bars_fast(DAY)
        E.today_bars_fast(DAY)
    finally: E.http = _http0
    ok([t for _, t in seen] == [7, 40, 20, 7], seen)
    print("E11 K 逾時 OK")

    # ── 寬度 / 名單平均電腦自己算:整份名單(和雲端同一批股票)──
    E._TB.update(d=DAY, rows={"1111": {"09:00": ["09:00", 0, 0, 0, 11.0, 1]}, "2222": {"09:00": ["09:00", 0, 0, 0, 9.0, 1]},
                              "3333": {"09:00": ["09:00", 0, 0, 0, 11.0, 1]}, "9999": {"09:00": ["09:00", 0, 0, 0, 1.0, 1]}}, last="09:00")
    pj = {"date": "2026-11-19", "pool": [{"code": "1111", "close": 10.0}, {"code": "2222", "close": 10.0}, {"code": "3333", "close": 10.0, "date": DAY, "prev": 12.0}]}
    E._BR_POOL = E.br_pool_prev(pj, DAY); E._BR_PREV = {"1111": 10.0, "9999": 2.0}   # 9999 = 名單外(監控)股票:雲端不算
    ok(E._BR_POOL == {"1111": 10.0, "2222": 10.0, "3333": 12.0})
    ok(E.br_local(DAY) == {"09:00": round(1 / 3, 3)} and E.av_local(DAY) == {"09:00": round((10 - 10 + (11 / 12 - 1) * 100) / 3, 3)}, E.br_local(DAY))
    E.CFG["ENGINE_BR_POOL"] = "0"
    ok(E.br_local(DAY) == {"09:00": 0.5})   # 照舊:只用監控股
    E.CFG.pop("ENGINE_BR_POOL"); E._BR_POOL = {}
    ok(E.br_local(DAY) == {"09:00": 0.5})   # 名單讀不到 → 照舊
    print("寬度自己算整份名單 OK")

# 2026-11-22 計算迴圈股票順序固定照名單張數大到小(同張數照代碼),在手 / 今天通知過不排前面,名單外手動成交排最後
def _st(c, lots, close=100): return {"code": c, "name": c, "market": "twse", "close": close, "prev": close, "amp": 9, "lots": lots, "daytrade": 1, "short_first": 1, "date": DAY}
_pool = [_st("2330", 5000), _st("2303", 9000), _st("2317", 5000), _st("1301", 100), _st("3008", 7000)]
_sv = (E.pool_rules, E.stock_ok, E.extra_codes)
try:
    E.pool_rules = lambda j, cap: {"lo": 0, "hi": 1e9, "amp": 0, "size": 99, "cnt": 99, "tick": 0, "excl": [], "lots": 0, "bars": 0, "days": 1}
    E.stock_ok = lambda s, j, R: ""
    E.extra_codes = lambda d: [{"code": "6789", "name": "x", "close": 50, "prev": 50, "extra": 1}]
    u = E.build_universe({"pool": _pool}, DAY, 250000, held={"1301", "2317"}, noted={"3008"})
    ok(u["codes"] == ["2303", "3008", "2317", "2330", "1301", "6789"], u["codes"])
    u2 = E.build_universe({"pool": _pool}, DAY, 250000)
    ok(u2["codes"] == ["2303", "3008", "2317", "2330", "1301", "6789"], u2["codes"])
finally:
    E.pool_rules, E.stock_ok, E.extra_codes = _sv
print("計算順序照張數 OK")
print("test_engine 全部 OK")
# 2026-11-25 七3(判七第三套「大跌日順勢空」):解析進場行、內文「空 大跌」、自訂出場、雲端對回 七3
_ev73 = E.parse_events("  10:09 判七3(大跌日) 加權 -2.80%、股 -3.00%、離今高 −4.00% → 進場空 97.00 ×2張\n  10:30 停利 出 93.10", "七3")
assert _ev73[0][1] == "entry" and _ev73[0][2]["text"] == "空 大跌" and _ev73[0][2]["px"] == 97.0 and "大跌日" in _ev73[0][2]["note"], _ev73
assert "七3" in E.RS_ALL and E.JUDGE["七3"] == "判七" and "七3" in E.CUSTOM_EXIT_RS and E.RS_OF["判七"] == "七"
assert not E.parse_events("  10:09 判七2(極端日) 加權 +5.4% → 進場空 18.80 ×13張", "七3")[0][2]["text"].endswith("大跌")
print("七3 解析 OK")
# 2026-11-25 六4(判六第四套「恐慌反轉翻紅做多」)
_ev64 = E.parse_events("  09:34 判六4(恐慌翻紅) 加權今低 -8.99%、反彈 1.74%、股今低 -10.00%、現 +1.48% → 進場多 23.95 ×10張\n  13:20 13:20 強制平倉 出 25.95", "六4")
assert _ev64[0][1] == "entry" and _ev64[0][2]["side"] == 1 and _ev64[0][2]["text"] == "多 恐慌翻紅" and _ev64[0][2]["px"] == 23.95, _ev64
assert "六4" in E.RS_ALL and E.JUDGE["六4"] == "判六" and "六4" in E.CUSTOM_EXIT_RS and E.RS_OF["判六"] == "六"
print("六4 解析 OK")


# ── 2026-11-26 缺資料改擋:缺資料 → 不發新進場(不記 emitted,資料回來 10 分鐘內照判),原因記 blocked_<日期>.jsonl「缺資料:X」──
_J3m = {"NOENTRYAFTER": "12:30", "ENTRYTO": "12:30", "ENTRYFROM": "09:30"}
_out = "10:30 空 價 99.00 xx → 進場 99.00 ×2張\n"
def _blk_miss():
    f = E.DATA / f"blocked_{DAY}.jsonl"
    return [json.loads(x).get("miss") for x in f.read_text(encoding="utf-8").splitlines() if x.strip()] if f.exists() else []
# 2026-11-26 缺資料改擋:沒有昨收 → 不發(原本照發、通知停損不收 ±6.5%)
e = mk(envs={"三": dict(_J3m)}, prev={"2330": 0, "2317": 100.0})
e.apply_one(("2330", "三", _out, ""), "10:32", "10:22", True, True, {})
ok(not e.sent and ("三", "2330", "10:30") not in e.emitted and "缺資料:昨收" in _blk_miss(), (e.sent, _blk_miss()))
e.prev["2330"] = 100.0; e.apply_one(("2330", "三", _out, ""), "10:32", "10:22", True, True, {})
ok(len(e.sent) == 1 and ("三", "2330", "10:30") in e.emitted, "昨收回來照發")
print("缺資料改擋:引擎沒昨收不發 OK")
# 2026-11-26 缺資料改擋:⑧ 強弱前一天收盤 / 今天台指期加權缺 → 不發(私人庫 rs_env.txrel 資料不全會「先不看」放行)
def _txrel_rsenv(sd, tm, txf, ix, tp, ip, th):   # 和私人庫 rs_env.txrel 一樣:資料不全 → (True, 先不看)
    if not txf or not ix or not tp or not ip: return True, "⑧ 強弱資料不全,先不看"
    return True, "⑧ 台指期比加權 +0.00%"
_TXF = {f"10:{m:02d}": 17000.0 - m for m in range(0, 31)}; _IX = {f"10:{m:02d}": 22000.0 - m for m in range(0, 31)}
def _mk_tx(env, txf=_TXF, ix=_IX, tp=17100.0, ip=22100.0):
    e_ = mk(envs={"三": dict(_J3m, **env)})
    e_.txsync_fn = lambda sd, tm, st, txf_, n, r, *a, **k: (True, "⑧ ok"); e_.txrel_fn = _txrel_rsenv; e_.txcover_fn = None
    e_.txbounce_fn = lambda sd, tm, m, th: (True, ""); e_.txdrop_fn = lambda sd, tm, m, th: (True, "")
    e_._txf_c = (time.time() + 999, DAY, dict(txf)); e_._txfl_c = (time.time() + 999, DAY, dict(txf)); e_._ix_c = (time.time() + 999, DAY, dict(ix))
    e_._txrel_p = (DAY, tp, ip, time.time(), True)
    return e_
e = _mk_tx({"TXSYNC": "60", "TXREL": "0.7"}, tp=None)
e.apply_one(("2330", "三", _out, ""), "10:32", "10:22", True, True, {})
ok(not e.sent and ("三", "2330", "10:30") not in e.emitted and any(str(x).startswith("缺資料:⑧ 強弱(前一天台指期收盤") for x in _blk_miss()), _blk_miss())
e._txrel_p = (DAY, 17100.0, 22100.0, time.time(), True); e.apply_one(("2330", "三", _out, ""), "10:32", "10:22", True, True, {})
ok(len(e.sent) == 1, "前一天收盤補到照發")
e = _mk_tx({"TXSYNC": "60", "TXREL": "0.7"}, ix={k: v for k, v in _IX.items() if k <= "10:20"})   # 加權停在 10:20(斷了 10 分鐘)
ok(not e.tx_ok("三", "2330", "10:30", -1) and "加權" in e._tx_miss, e._tx_miss)
e = _mk_tx({"TXSYNC": "60", "TXREL": "0.7"}); e.txrel_fn = None
ok(not e.tx_ok("三", "2330", "10:30", -1), "⑧ 強弱程式沒載入 → 不發(原本放行)")
# 急彈 / 急殺:判斷程式沒載入、或台指期 / 加權到訊號那根都斷了 → 不發(原本放行 / rs_env 沒資料「不看」)
e = _mk_tx({"TXBOUNCE": "0.8"}); e.txbounce_fn = None
ok(not e.tx_ok("三", "2330", "10:30", -1), "沒有 TXSYNC 的急彈程式沒載入 → 不發")
e = _mk_tx({"TXDROP": "0.8"}, txf={k: v for k, v in _TXF.items() if k <= "10:10"}, ix={})
ok(not e.tx_ok("三", "2330", "10:30", 1) and e._tx_miss.startswith("缺資料:⑧ 急殺"), e._tx_miss)
e = _mk_tx({"TXDROP": "0.8"}, txf={k: v for k, v in _TXF.items() if k <= "10:10"})
ok(e.tx_ok("三", "2330", "10:30", 1), "台指期斷了但加權有 → 用加權照判")
e = _mk_tx({"TXSYNC": "60", "TXBOUNCE": "0.8"}, txf={k: v for k, v in _TXF.items() if k <= "10:10"}, ix={k: v for k, v in _IX.items() if k <= "10:10"})
ok(not e.tx_ok("三", "2330", "10:30", -1) and "急彈" in e._tx_miss, e._tx_miss)
e = _mk_tx({"TXSYNC": "60", "TXFROMLO": "2.5"}, txf={k: v for k, v in _TXF.items() if k <= "10:10"}); e.txfromlo_fn = lambda sd, tm, m, th, lo=None: (True, "")
ok(not e.tx_ok("三", "2330", "10:30", -1) and "空單離今低" in e._tx_miss, e._tx_miss)   # 有 TXSYNC 的只看台指期(模擬器同一套)
ok(e.tx_fresh({"10:28": 1.0}, "10:30") and not e.tx_fresh({"10:26": 1.0}, "10:30") and not e.tx_fresh({}, "10:30") and not e.tx_fresh({"10:31": 1.0}, "10:30"))
print("缺資料改擋:引擎 ⑧ 缺台指期 / 加權 / 前一天收盤 / 判斷程式 → 不發 OK")
# 2026-11-26 缺資料改擋:規則抓不到最新的、本機那份不是今天抓的 → 全部判別式不發新進場(rs_block)
_R0 = dict(E.RULES)
for f_, _ in E.RULE_FILES: (E.SIMD / Path(f_).name).write_text("x", encoding="utf-8")
E.RULES.update(stale=False); ok(E.rules_miss(DAY) == "")
E.RULES.update(stale=True, why="抓不到"); ok(E.rules_miss("2099-01-01").startswith("缺資料:規則") and E.rules_miss("2000-01-01") == "", E.rules_miss("2099-01-01"))
E.RULES.clear(); E.RULES.update(_R0)
e = mk(envs={"三": dict(_J3m)}); e.apply_one(("2330", "三", _out, ""), "10:32", "10:22", True, True, {"三": "缺資料:規則(測試)"})
ok(not e.sent and ("三", "2330", "10:30") not in e.emitted and "缺資料:規則(測試)" in _blk_miss(), _blk_miss())
print("缺資料改擋:規則缺 → 不發 OK")
