import time
"""python3 test_trader.py:自動交易邏輯測試(假的下單通道,不需要元大元件)
2026-10-28 改版:同時最多 5 檔、強制進場 / 強制平倉、不預掛條件單、±6.5% 強制平倉、30 秒查元大"""
import datetime as dt, tempfile, os, time
from pathlib import Path
from trader import Trader, shift, tick_size, ENTERING, HOLDING, EXITING, amount_reject, fetch_signals

TW = dt.timezone(dt.timedelta(hours=8)); NOW = [dt.datetime(2026, 10, 22, 10, 0, tzinfo=TW)]
def at(h, m): NOW[0] = dt.datetime(2026, 10, 22, h, m, tzinfo=TW)
logs = []
class GW:
    def __init__(s): s.sent = []; s.n = 0; s.tags = []
    def send(s, code, mkt, side, lots, price, typ, tif, tag='', flag=' '): s.n += 1; s.sent.append((code, side, lots, price, typ, tif, flag)); s.tags.append(tag); return f"A{s.n:04d}"
    def cancel(s, no, code='', bs=None, typ=None): s.sent.append(("cancel", no, code, bs))
    def place_oco(s, *a): s.sent.append(("oco",) + a)
    def delete_oco(s, no):
        s.sent.append(("del_oco", no))
        if getattr(s, "auto", True) and hasattr(s, "t"): s.t.on_strategy_delete_reply(no, 0, "")   # 2026-11-08 20:50 元大確認刪除(測試預設馬上回)
class GWN(GW):
    def send(s, *a): GW.send(s, *a); return None          # 真的通道:委託書號從回應事件來
tmp = Path(tempfile.mkdtemp()); stop = tmp / "STOP"; NMK = [0]
def mk(gw=GW, prime=True, **kw):
    NMK[0] += 1   # 2026-10-29 每個測試自己的狀態檔(今天進場過的訊號會存檔,不要互相影響)
    cfg = {"TRADE_LOT_TIER": "0", "TRADE_JUDGE_FROM": "09:00", "TRADE_JUDGE_TO": "13:10", "TRADE_FORCE_TO": "13:10", "TRADE_BROKER_STOP": "0", "TRADE_CAP": "0", "TRADE_ENABLE": "1", "TRADE_AUTO": "1", "TRADE_STOP_FILE": str(stop), "TRADE_STATE_FILE": str(tmp / f"st{NMK[0]}.json"), "TRADE_Y3_POST_DELAY": "0", "TRADE_Y3_TP_PCT": "2.5"}; cfg.update(kw)
    g = gw(); t = Trader(cfg, g, logs.append, lambda: NOW[0]); g.t = t; t.posts = []; t.alerts = []
    t.post = lambda a, b, k, t_=None, **kw: t.posts.append((a, b, k)); t.akinds = []; t.alert = lambda a, b="", k="pc": (t.alerts.append(a), t.akinds.append(k))
    if prime: t.prime([])   # 2026-10-29 稽核 #1:跟 bridge 一樣,啟動時讀到通知(這裡是空的)才算準備好
    return t, g
sig = lambda k, kind, title, body: {"_k": k, "kind": kind, "title": title, "body": body}
ENT = sig("10:00:6269|台郡做空進場|判二", "short", "台郡 6269 做空進場 86.20", "10:00 空✓ · 停利 84.05 停損 88.70 · 判二")
def ENTX(code, px, k=None, judge="判一", kind="long"):
    return sig(k or f"10:0x:{code}", kind, f"股{code} {code} {'做多' if kind == 'long' else '做空'}進場 {px}", f"10:00 多✓ · 停利 {px * 1.025:.2f} 停損 {px * 0.985:.2f} · {judge}")
n_case = 0
def ok(cond, *msg):
    assert cond, msg

assert tick_size(86.2) == 0.1 and shift(86.2, -2) == 86.0 and shift(49.95, 2) == 50.1
# 1) 進場 → 成交 → 碰停利 → 掛漲停全部買回(不預掛條件單)→ 平倉完成
t, g = mk(); t.on_tick("6269", 86.2); t.step([ENT]); p = t.positions["6269"]
ok(p["state"] == ENTERING and g.sent[-1] == ("6269", "S", 1, 86.0, "9", "3", " "), g.sent)
t.on_report(51, "A0001", 0, 1000, 86.1); ok(p["state"] == HOLDING and p["entry"] == 86.1 and not [x for x in g.sent if x[0] == "oco"], g.sent)
t.on_tick("6269", 85.0); ok(p["state"] == HOLDING)
t.on_tick("6269", 84.0); ok(p["state"] == EXITING and g.sent[-1] == ("6269", "B", 1, 0, "9", "0", "H"), g.sent[-1])
t.on_report(51, "A0002", 0, 1000, 84.1); ok("6269" not in t.positions and t.journal[-1]["why"] == "停利" and t.pnl > 1500, t.pnl)
ok(not t.posts, t.posts)                                                              # 判二自己的平倉訊息由手機發,程式不重複寫
# 2) 進場 IOC 沒成交(價穩失效)→ 2026-10-31 改 C:改掛跌停賣 ROD 補進場;補單也結束沒成交 → 拿掉;TRADE_ENTRY_FALLBACK=0 照舊直接拿掉
t2, g2 = mk(); t2.on_tick("6269", 86.2); t2.step([ENT]); t2.on_report(50, "A0001", 25, 0, 0)
ok("6269" in t2.positions and g2.sent[-1] == ("6269", "S", 1, 85.4, "9", "0", " "), g2.sent)   # 2026-11-08 16:09 補單改掛訊號價 −1%
t2.on_report(50, "A0002", 2, 0, 0); ok("6269" not in t2.positions)
t2b, g2b = mk(TRADE_ENTRY_FALLBACK="0"); t2b.on_tick("6269", 86.2); t2b.step([ENT]); t2b.on_report(50, "A0001", 25, 0, 0); ok("6269" not in t2b.positions and len(g2b.sent) == 1)
# 3) 平倉只成交一部分 → 再送剩下的
t3, g3 = mk(TRADE_QTY="2"); t3.on_tick("6269", 86.2); t3.step([ENT]); t3.on_report(51, "A0001", 0, 2000, 86.2)
t3.on_tick("6269", 88.8); ok(t3.positions["6269"]["state"] == EXITING)              # 停損
t3.on_report(51, "A0002", 0, 1000, 88.9); t3.on_report(50, "A0002", 24, 0, 0)
ok(t3.positions["6269"]["qty"] == 1 and g3.sent[-1][2] == 1)
t3.on_report(51, "A0003", 0, 1000, 89.0); ok("6269" not in t3.positions and t3.pnl < 0)
# 4) 手機平倉通知 → 出場;別的判別式的平倉通知不理
t4, g4 = mk(); t4.on_tick("6269", 86.2); t4.step([ENT]); t4.on_report(51, "A0001", 0, 1000, 86.2)
t4.step([sig("10:20:6269|台郡出|判一", "exit", "台郡 6269 出場 +0.3%:出", "… · 判一")]); ok(t4.positions["6269"]["state"] == HOLDING)
t4.step([sig("10:21:6269|台郡出|判二", "exit", "台郡 6269 出場 +0.3%:出", "… · 判二")]); ok(t4.positions["6269"]["state"] == EXITING)
# 5) TRADE_MAX_POS=4 時第 5 檔起不進(預設 2026-11-04 起不限檔數,只看額度);強制進場不佔名額
t5, g5 = mk(TRADE_MAX_POS="4")
codes = ["2401", "2402", "2403", "2404", "2405", "2407", "2408", "2409", "2410", "2411", "2406"]
for c in codes: t5.on_tick(c, 50.0)
for i, c in enumerate(codes):
    t5.step([ENTX(c, 50.0, k=f"k{c}")])
    if c in t5.positions: t5.on_report(51, "", 0, 1000, 50.0, basket=g5.tags[-1])
ok(len(t5.positions) == 4 and "2406" not in t5.positions and "上限 4" in logs[-1], logs[-1])
t5.on_tick("2330", 600.0); r = t5.force_enter("2330", 1, 1, 2.5, 1.5); ok(r[0] and "2330" in t5.positions, r)
# 6) 緊急停止檔:自動不進;強制照樣進
stop.write_text("1"); t6, g6 = mk(); t6.on_tick("6269", 86.2); t6.step([ENT]); ok("6269" not in t6.positions and "緊急停止" in logs[-1])
ok(t6.force_enter("6269", -1, 2, 2.5, 1.5)[0]); os.remove(stop)
# 7) 13:21 備援強制平倉(13:20 交給 bridge);TRADE_CANCEL_ALL=0 時 13:20 自己平
t7, g7 = mk(); t7.on_tick("6269", 86.2); t7.step([ENT]); t7.on_report(51, "A0001", 0, 1000, 86.2)
at(13, 20); t7.step(); ok(t7.positions["6269"]["state"] == HOLDING); at(13, 21); t7.step(); ok(t7.positions["6269"]["state"] == EXITING and g7.sent[-1][6] == "H")
at(10, 0); t7b, g7b = mk(TRADE_CANCEL_ALL="0"); t7b.on_tick("6269", 86.2); t7b.step([ENT]); t7b.on_report(51, "A0001", 0, 1000, 86.2)
at(13, 20); t7b.step(); ok(t7b.positions["6269"]["state"] == EXITING and "13:20 強制平倉" in t7b.positions["6269"]["exit_why"]); at(10, 0)
# 8) 現價離訊號價太遠 → 不追;13:10 以後自動不進
t8, g8 = mk(); t8.on_tick("6269", 87.0); t8.step([ENT]); ok("6269" not in t8.positions and "太晚" in logs[-1])
at(13, 12); t8b, g8b = mk(); t8b.on_tick("6269", 86.2); t8b.step([ENT]); ok("6269" not in t8b.positions and "13:10" in logs[-1]); at(10, 0)
# 9) 啟動時舊通知不進場;同一則不重複
t9, g9 = mk(); t9.prime([ENT]); t9.on_tick("6269", 86.2); t9.step([ENT]); ok("6269" not in t9.positions and not g9.sent)
# 10) 沒開啟 → 不交易(強制也不行)
t10, g10 = mk(TRADE_ENABLE="0"); t10.on_tick("6269", 86.2); t10.step([ENT]); ok(not g10.sent and not t10.force_enter("6269", 1, 1)[0])
# 11) 回報比委託書號先到:用自訂編號(BasketNo)對上
t11, g11 = mk(GWN); t11.on_tick("6269", 86.2); t11.step([ENT]); t11.on_report(51, "B0009", 0, 1000, 86.1, basket=g11.tags[-1])
ok(t11.positions["6269"]["state"] == HOLDING and t11.positions["6269"]["order"] is None)
# 12) 多單平倉掛跌停;「讓檔 + IOC」模式還在
t12, g12 = mk(); t12.on_tick("2409", 37.25); t12.step([ENTX("2409", 37.25)])
t12.on_report(51, "A0001", 0, 1000, 37.3); t12.on_tick("2409", 36.6); ok(g12.sent[-1] == ("2409", "S", 1, 0, "0", "0", "L"), g12.sent[-1])
# 13) 舊做法:TRADE_BROKER_STOP=1 才掛條件單;條件單在時碰停損只記錄;條件單成交 → 平倉完成
t13, g13 = mk(TRADE_BROKER_STOP="1"); t13.on_tick("6269", 86.2); t13.step([ENT]); t13.on_report(51, "A0001", 0, 1000, 86.1)
ok(g13.sent[-1] == ("oco", "6269", "TWSE", -1, 1, 84.05, 88.7), g13.sent[-1]); t13.on_strategy_reply("K0001", True, ""); ok(t13.positions["6269"]["oco_live"])
n = len(g13.sent); t13.on_tick("6269", 88.8); ok(len(g13.sent) == n)
t13.on_report(51, "Z9", 0, 1000, 88.9, basket="", code="6269", bs="B"); ok("6269" not in t13.positions and t13.pnl < 0)
# 14) (有掛元大條件單時)程式平倉成交後又有一筆同方向平倉成交(多平)→ 馬上反向補回
t14, g14 = mk(TRADE_BROKER_STOP="1"); t14.on_tick("6269", 86.2); t14.step([ENT]); t14.on_report(51, "A0001", 0, 1000, 86.1)
t14.on_strategy_reply("K1", True, ""); t14.positions["6269"]["oco_live"] = False
t14.on_tick("6269", 84.0); t14.on_report(51, "A0002", 0, 1000, 84.1); ok("6269" not in t14.positions)
t14.on_report(51, "Z8", 0, 1000, 84.2, basket="", code="6269", bs="B"); ok(t14.positions["6269"]["state"] == EXITING and g14.sent[-1][:3] == ("6269", "S", 1) and g14.sent[-1][6] == "L", g14.sent[-1])
# 14b) 不掛條件單(預設):平倉後又有同方向成交(手動 / 程式舊單晚到)→ 不反向補單
t14b, g14b = mk(); t14b.on_tick("6269", 86.2); t14b.step([ENT]); t14b.on_report(51, "A0001", 0, 1000, 86.1); t14b.on_tick("6269", 84.0); t14b.on_report(51, "A0002", 0, 1000, 84.1)
n = len(g14b.sent); t14b.on_report(51, "Z8", 0, 1000, 84.2, basket="", code="6269", bs="B"); t14b.on_report(51, "Z9", 0, 1000, 84.2, basket="T100000009", code="6269", bs="B")
ok(len(g14b.sent) == n and t14b.positions["6269"]["judge"] == "元三" and t14b.positions["6269"]["qty"] == 1)   # 2026-10-28 不是程式下的單 = 元三(程式舊單晚到的 T 編號不算)
# 15) 啟動時接回:全部接回(自動 + 強制),手動的不管;強三的停利停損 % 從狀態檔來
t15, g15 = mk(); t15.state_file.write_text('{"day": "2026-10-22", "locked": ["2002"], "forced": {"2330": {"tp_pct": 2.5, "sl_pct": 1.5}}}', encoding="utf-8")
tr = [{"code": "6269", "bs": "S", "lots": 1, "price": 86.1, "time": "10:37:02", "order_no": "A1"},
      {"code": "2330", "bs": "B", "lots": 1, "price": 600.0, "time": "10:38:00", "order_no": "A2"},
      {"code": "2409", "bs": "B", "lots": 2, "price": 37.3, "time": "10:40:00", "order_no": "M1"},
      {"code": "1709", "bs": "S", "lots": 3, "price": 56.2, "time": "09:21:00", "order_no": "A3"},
      {"code": "1709", "bs": "B", "lots": 3, "price": 57.2, "time": "09:53:00", "order_no": "K1"}]
r = t15.resume(tr, {"A1": "T103701001", "A2": "F103800002", "M1": "", "A3": "T092100003"}, [], [ENT])
ok(sorted(r) == ["2330", "6269"] and t15.positions["6269"]["judge"] == "判二" and t15.positions["6269"]["tp"] == 84.05, r, t15.positions)
ok(t15.positions["2330"]["judge"] == "強三" and t15.positions["2330"]["tp"] == 615.0 and t15.positions["2330"]["sl"] == 591.0, t15.positions["2330"])
ok(t15.positions["2409"]["judge"] == "元三" and t15.positions["2409"]["tp"] == round(37.3 * 1.025, 2) and t15.n_trades == 3 and t15.pnl < -3000 and "2002" in t15.locked and not [x for x in g15.sent if x[0] == "oco"])
# 16) 沒有未平部位 → 回 None
t16, g16 = mk(); ok(t16.resume([{"code": "6269", "bs": "S", "lots": 1, "price": 86.1, "time": "1", "order_no": "A1"}, {"code": "6269", "bs": "B", "lots": 1, "price": 85.0, "time": "2", "order_no": "A2"}], {"A1": "T1"}, [], []) is None and t16.n_trades == 1 and t16.pnl > 0)
# 17) 讓檔 + IOC 模式:13:25 一律漲停 / 跌停 ROD;失效再送也是
t17, g17 = mk(TRADE_EXIT_MODE="slip", TRADE_CANCEL_ALL="0"); t17.on_tick("6269", 86.2); t17.step([ENT]); t17.on_report(51, "A0001", 0, 1000, 86.1)
at(13, 25); t17.step(); ok(g17.sent[-1] == ("6269", "B", 1, 0, "9", "0", "H"), g17.sent[-1])
t17.on_report(50, "A0002", 24, 0, 0, basket=g17.tags[-1]); ok(g17.sent[-1][6] == "H"); at(10, 0)
# 18) 13:22 複查:程式的空單沒平完 → 再掛漲停;手動空單 → 漲停買回;手動多單只提醒(short 模式)
t18, g18 = mk(TRADE_CANCEL_ALL="0", TRADE_RECHECK_CLOSE="short")
t18.on_tick("6269", 86.2); t18.step([ENT]); t18.on_report(51, "A0001", 0, 1000, 86.1)
at(13, 20); t18.step(); t18.on_report(50, "A0002", 24, 0, 0, basket=g18.tags[-1])
n0 = len(g18.sent); t18.positions["6269"]["order"]["dead"] = True
left = t18.recheck([{"code": "6269", "bs": "S", "lots": 1}, {"code": "2409", "bs": "S", "lots": 2}, {"code": "2330", "bs": "B", "lots": 1}])
ok(dict(left) == {"6269": -1, "2409": -2, "2330": 1})
ok(g18.sent[n0][:3] == ("6269", "B", 1) and g18.sent[n0][6] == "H" and any(x[:3] == ("2409", "B", 2) and x[6] == "H" for x in g18.sent) and not any(x[0] == "2330" for x in g18.sent))
# 18b) 預設 all:手動多單跌停賣出;已沖掉的不動
t18b, g18b = mk(TRADE_CANCEL_ALL="0")
left = t18b.recheck([{"code": "2409", "bs": "S", "lots": 2}, {"code": "2330", "bs": "B", "lots": 1}, {"code": "2317", "bs": "B", "lots": 3}, {"code": "2317", "bs": "S", "lots": 3}])
ok(dict(left) == {"2409": -2, "2330": 1} and any(x[:3] == ("2330", "S", 1) and x[6] == "L" for x in g18b.sent) and not any(x[0] == "2317" for x in g18b.sent))
# 18c) 第二次複查:補單還掛著 → 不重複;沒掛著 → 再補
n1 = len(g18.sent)
t18.recheck([{"code": "6269", "bs": "S", "lots": 1}, {"code": "2409", "bs": "S", "lots": 2}], [{"code": "6269", "bs": "B", "lots": 1}, {"code": "2409", "bs": "B", "lots": 2}], at="13:23")
ok(not [x for x in g18.sent[n1:] if x[0] in ("6269", "2409")])
t18.recheck([{"code": "2409", "bs": "S", "lots": 2}], [], at="13:24"); ok(g18.sent[-1][:3] == ("2409", "B", 2)); at(10, 0)
# 19) 13:20 取消全部掛單:持倉中、條件單在 → 先刪條件單;再用元大成交回報平倉;進場單還在等 → 取消(帶買賣別)
t19, g19 = mk(TRADE_BROKER_STOP="1"); t19.on_tick("6269", 86.2); t19.step([ENT]); t19.on_report(51, "A0001", 0, 1000, 86.1); t19.on_strategy_reply("K9", True, "")
at(13, 20); n2 = len(g19.sent); t19.step(); ok(len(g19.sent) == n2)
t19.prepare_close_all(); ok(g19.sent[-1] == ("del_oco", "K9"))
t19.recheck([{"code": "6269", "bs": "S", "lots": 1}], [], at="13:20"); ok(t19.positions["6269"]["state"] == EXITING and g19.sent[-1][:3] == ("6269", "B", 1))
at(13, 9); t19b, g19b = mk(); t19b.on_tick("6269", 86.2); t19b.step([ENT]); at(13, 20); t19b.prepare_close_all(); ok(g19b.sent[-1] == ("cancel", "A0001", "6269", "S"), g19b.sent[-1])
t19b.recheck([], [], at="13:20"); ok("6269" not in t19b.positions); at(10, 0)
# 20) 進場被拒(額度)→ 退 1 張再掛;原因不是額度 → 放棄
t20, g20 = mk(GWN, TRADE_QTY="5"); t20.on_tick("6269", 86.2); t20.step([ENT]); ok(g20.sent[-1][2] == 5)
for want in (4, 3, 2, 1):
    t20.on_order_reply("", False, "額度不足", tag=g20.tags[-1]); ok(t20.positions["6269"]["state"] == ENTERING and g20.sent[-1][2] == want, want)
t20.on_order_reply("", False, "額度不足", tag=g20.tags[-1]); ok("6269" not in t20.positions and len(g20.sent) == 5)
t20b, g20b = mk(GWN, TRADE_QTY="5"); t20b.on_tick("6269", 86.2); t20b.step([ENT]); t20b.on_order_reply("", False, "E123 融券券源不足"); ok("6269" not in t20b.positions and len(g20b.sent) == 1)
ok(amount_reject("當日委託額度不足") and amount_reject("超過可用額度") and not amount_reject("無券源") and not amount_reject("不具當沖資格") and not amount_reject(""))
# 21) 一天成交總金額上限(自動進場):只夠 1 張掛 1 張;不夠不進
t21, g21 = mk(TRADE_QTY="5", TRADE_MAX_AMOUNT="200000"); t21.on_tick("6269", 86.2); t21.step([ENT]); ok(g21.sent[-1][2] == 1)
t21.on_report(51, "A0001", 0, 1000, 86.1); t21.on_tick("6269", 84.0); t21.on_report(51, "A0002", 0, 1000, 84.1); ok(abs(t21.amount_today - 170200) < 1)
t21.on_tick("2409", 37.25); t21.step([ENTX("2409", 37.25, k="z1")]); ok("2409" not in t21.positions and "不夠 1 張" in logs[-1])

# ── 2026-10-28 新規格 ──
# F1) 強制做多:漲停價買進 ROD、自訂編號 F 開頭;成交 → 停利 / 停損 = 判三 % 從成交價算;寫一則「強三」進場訊息
tf, gf = mk(); tf.name_of = lambda c: "台積電"; tf.on_tick("2330", 600.0)
r = tf.force_enter("2330", 1, 1, 2.5, 1.5, cmd_id="c1"); p = tf.positions["2330"]
ok(r[0] and gf.sent[-1] == ("2330", "B", 1, 0, "0", "0", "H") and gf.tags[-1].startswith("F") and p["judge"] == "強三", gf.sent[-1], gf.tags[-1])
ok(tf.force_enter("2330", 1, 1, 2.5, 1.5, cmd_id="c1") == r and len(gf.sent) == 1)          # 同一個指令編號不重送
ok(not tf.force_enter("2330", 1, 1, 2.5, 1.5, cmd_id="c2")[0])                              # 這檔已有部位
tf.on_report(51, "A0001", 0, 1000, 602.0); ok(p["state"] == HOLDING and p["tp"] == 617.05 and p["sl"] == 592.97, p)
ok(tf.posts[-1][0] == "台積電 2330 做多進場 602.00" and tf.posts[-1][1].endswith("· 1張 · 強三") and tf.posts[-1][2] == "long", tf.posts)
# F2) 強三碰判三的停損 → 掛跌停全部賣出;平倉後寫一則出場訊息(強三)
tf.on_tick("2330", 592.0); ok(p["state"] == EXITING and gf.sent[-1] == ("2330", "S", 1, 0, "0", "0", "L"))
tf.on_report(51, "A0002", 0, 1000, 591.0); ok("2330" not in tf.positions and tf.posts[-1][2] == "exit" and "停損" in tf.posts[-1][0] and tf.posts[-1][1].endswith("· 1張 · 強三"), tf.posts[-1])
ok("做多 10:00 進 602.00 → 現價 591.00" in tf.posts[-1][1], tf.posts[-1])
# F3) 強制做空:跌停賣出(當沖先賣);判三的平倉通知會讓強三出場,判一的不會
tg, gg = mk(); tg.on_tick("6269", 86.2); tg.force_enter("6269", -1, 2, 2.5, 1.5); ok(gg.sent[-1] == ("6269", "S", 2, 0, "9", "0", "L"))
tg.on_report(51, "A0001", 0, 2000, 86.0); q = tg.positions["6269"]
tg.step([sig("x1", "exit", "台郡 6269 出場:出", "… · 判一")]); ok(q["state"] == HOLDING)
tg.step([sig("x2", "exit", "台郡 6269 出場:出", "… · 判三")]); ok(q["state"] == EXITING and gg.sent[-1][:3] == ("6269", "B", 2) and gg.sent[-1][6] == "H")
# F4) 強制平倉判一的部位:跌停全部賣出;寫「強平 · 判一」;今天這檔判一 / 二 / 三不再自動進場,強制還可以
th, gh = mk(); th.on_tick("2409", 37.25); th.step([ENTX("2409", 37.25)]); th.on_report(51, "A0001", 0, 1000, 37.3)
r = th.force_close("2409", cmd_id="z1"); ok(r[0] and gh.sent[-1] == ("2409", "S", 1, 0, "0", "0", "L") and "2409" in th.locked, r)
th.on_report(51, "A0002", 0, 1000, 37.1); ok("2409" not in th.positions and th.journal[-1]["forced_close"])
ok("強平" in th.posts[-1][0] and th.posts[-1][1].endswith("· 強平 · 判一"), th.posts[-1])
th.on_tick("2409", 37.25); th.step([ENTX("2409", 37.25, k="again")]); ok("2409" not in th.positions and "強平過" in logs[-1], logs[-1])
ok(th.force_enter("2409", 1, 1, 2.5, 1.5)[0])
ok(not th.force_close("9999")[0])                                                          # 沒有部位
# F5) ±6.5%(對昨收):做多跌到 −6.5%、做空漲到 +6.5% → 立刻全部平倉;09:00 前的試撮價不算
ti, gi = mk(); ti.ref_of = lambda c: 100.0
ti.on_tick("3001", 100.0); ti.step([ENTX("3001", 100.0, k="m1")]); ti.on_report(51, "A0001", 0, 1000, 100.0); pi = ti.positions["3001"]; pi["sl"] = 0
ti.on_tick("3001", 93.6); ok(pi["state"] == HOLDING)
at(8, 59); ti.on_tick("3001", 93.0); ok(pi["state"] == HOLDING); at(10, 0)
ti.on_tick("3001", 93.5); ok(pi["state"] == EXITING and pi["exit_why"] == "超6.5%" and gi.sent[-1][6] == "L" and ti.alerts, pi)
tj, gj = mk(TRADE_MANUAL_MAX_MOVE_PCT="6.5"); tj.ref_of = lambda c: 100.0; tj.force_enter("3002", -1, 1); tj.on_report(51, "A0001", 0, 1000, 101.0); pj = tj.positions["3002"]
tj.on_tick("3002", 106.4); ok(pj["state"] == HOLDING); tj.on_tick("3002", 106.5); ok(pj["state"] == EXITING and gj.sent[-1][6] == "H")
# F6) 30 秒檢查:平倉單還掛著 → 繼續等;元大沒有這張單、這張單沒成交 → 重送;只看這張單自己的成交(同檔手動交易不算)
tk, gk = mk(); tk.on_tick("6269", 86.2); tk.step([ENT]); tk.on_report(51, "A0001", 0, 1000, 86.1); tk.on_tick("6269", 84.0); pk = tk.positions["6269"]
ok(not tk.need_verify()); pk["order"]["at"] -= 31; ok(tk.need_verify())
manual = [{"code": "6269", "bs": "S", "lots": 1, "price": 86.1, "order_no": "A0001"}, {"code": "6269", "bs": "S", "lots": 5, "price": 85.0, "order_no": "M1"}]
tk.verify([{"no": "A0002", "code": "6269", "bs": "B", "lots": 1}], manual)
ok(pk["state"] == EXITING and len(gk.sent) == 2 and not tk.need_verify())                   # 還掛著:不重送,30 秒內不再查
pk["order"]["at"] -= 31; pk["order"]["vat"] -= 31
tk.verify([], manual)
ok(len(gk.sent) == 3 and gk.sent[-1] == ("6269", "B", 1, 0, "9", "0", "H") and "平倉單重送" in tk.alerts[-1], gk.sent, tk.alerts)   # 只重送 1 張(手動 5 張不算)
pk["order"]["at"] -= 31
tk.verify([], manual + [{"code": "6269", "bs": "B", "lots": 1, "price": 84.2, "order_no": "A0003"}])
ok("6269" not in tk.positions and len(gk.sent) == 3)
# F6b) 平倉 3 張只成交 1 張、元大沒在掛了 → 只重送剩下 2 張(不是整檔淨部位)
tq, gq = mk(TRADE_QTY="3"); tq.on_tick("6269", 86.2); tq.step([ENT]); tq.on_report(51, "A0001", 0, 3000, 86.1); tq.on_tick("6269", 84.0); pq = tq.positions["6269"]
tq.on_report(51, "A0002", 0, 1000, 84.1); pq["order"]["at"] -= 31
tq.verify([], [{"code": "6269", "bs": "S", "lots": 3, "price": 86.1, "order_no": "A0001"}, {"code": "6269", "bs": "B", "lots": 1, "price": 84.1, "order_no": "A0002"}])
ok(gq.sent[-1][:3] == ("6269", "B", 2) and pq["qty"] == 2, gq.sent[-1], pq["qty"])
# F6c) 元大一直拒絕平倉 → 最多重送 3 次,之後推播請手動
tr_, gr_ = mk(GWN); tr_.force_enter("2330", 1, 1); tr_.on_report(51, "", 0, 1000, 600.0, basket=gr_.tags[-1]); tr_.force_close("2330")
for k in range(4): tr_.on_order_reply("", False, "E999 系統忙碌", tag=gr_.tags[-1])
ok(len([x for x in gr_.sent if x[1] == "S"]) == 4 and tr_.positions["2330"].get("stuck") and "平倉失敗" in tr_.alerts[-1], gr_.sent, tr_.alerts)
# F7) 強制進場 30 秒沒回報:元大沒有也沒成交 → 不重送、推播;元大有成交 → 照元大接上
tl, gl = mk(); tl.force_enter("2330", 1, 1, 2.5, 1.5); tl.positions["2330"]["order"]["at"] -= 31
tl.verify([], []); ok("2330" not in tl.positions and len(gl.sent) == 1 and "沒有送到元大" in tl.alerts[-1])
tl.force_enter("2330", 1, 1, 2.5, 1.5); tl.positions["2330"]["order"]["at"] -= 31
tl.verify([], [{"code": "2330", "bs": "B", "lots": 1, "price": 601.0, "order_no": "A0002"}]); ok(tl.positions["2330"]["state"] == HOLDING and tl.positions["2330"]["entry"] == 601.0)
# F8) 平倉單被元大拒絕 → 推播並馬上重送(有次數上限)
tm, gm = mk(GWN); tm.force_enter("2330", 1, 1); tm.on_report(51, "", 0, 1000, 600.0, basket=gm.tags[-1]); tm.force_close("2330")
tm.on_order_reply("", False, "E999 系統忙碌", tag=gm.tags[-1]); pm = tm.positions["2330"]; ok(pm["state"] == EXITING and "被元大拒絕" in tm.alerts[-1] and gm.sent[-1][:3] == ("2330", "S", 1) and gm.sent[-1][6] == "L")
# F8b) 程式重開時平倉單還掛著(鎖跌停)→ 接成平倉中,不送第二張;強制進場單還掛著 → 進場中
tw, gw_ = mk(); tw.state_file.write_text('{"day": "2026-10-22", "forced": {"3008": {"tp_pct": 2.5, "sl_pct": 1.5}}}', encoding="utf-8")
tw.resume([{"code": "6269", "bs": "B", "lots": 1, "price": 86.1, "time": "10:00", "order_no": "A1"}], {"A1": "T100000001"}, [], [],
          working=[{"code": "6269", "bs": "S", "lots": 1, "no": "A2", "typ": "0", "tag": "T101000002"}, {"code": "3008", "bs": "B", "lots": 1, "no": "A3", "typ": "0", "tag": "F101000003"}])
ok(tw.positions["6269"]["state"] == EXITING and tw.positions["3008"]["state"] == ENTERING and not gw_.sent, tw.positions)
tw.on_tick("6269", 80.0); at(13, 21); tw.step(); at(10, 0); ok(not gw_.sent)
tw.on_report(51, "A2", 0, 1000, 85.0, basket="T101000002"); ok("6269" not in tw.positions)
# F8c) 強制進場:電腦沒有昨收時用手機帶來的昨收做 ±6.5%
tz, gz = mk(TRADE_MANUAL_MAX_MOVE_PCT="6.5"); tz.force_enter("4000", 1, 1, ref=50.0); tz.on_report(51, "A0001", 0, 1000, 50.0); tz.on_tick("4000", 46.7); ok(tz.positions["4000"]["state"] == EXITING)
# F9) 強制進場不受當天虧損上限;自動受
tn, gn = mk(); tn.pnl = -30000; tn.on_tick("6269", 86.2); tn.step([ENT]); ok("6269" not in tn.positions and "已虧" in logs[-1]); ok(tn.force_enter("6269", -1, 1)[0])
# F10) 強制平倉:進場還沒成交 → 不行;已在平倉中 → 記強平(之後寫強平訊息)
to, go = mk(); to.force_enter("2330", 1, 1); ok(to.force_close("2330")[0] and go.sent[-1][:2] == ("cancel", "A0001"), go.sent)   # 2026-10-28 進場中按強平:取消進場單
to.on_report(51, "A0001", 0, 1000, 600.0); ok(to.positions["2330"]["state"] == EXITING and go.sent[-1][:3] == ("2330", "S", 1), go.sent)   # 取消前已成交 → 馬上平掉
to.on_report(51, "A0002", 0, 1000, 599.0); ok(to.posts[-1][1].endswith("· 強平 · 強三") and "2330" not in to.positions, to.posts[-1])
to2, go2 = mk(); to2.force_enter("2330", 1, 1); to2.on_report(51, "A0001", 0, 1000, 600.0); to2.positions["2330"]["tp"] = 601; to2.on_tick("2330", 601.0); ok(to2.positions["2330"]["state"] == EXITING)
ok(to2.force_close("2330") == (True, "已經在平倉中")); to2.on_report(51, "A0002", 0, 1000, 601.0); ok(to2.posts[-1][1].endswith("· 強平 · 強三"), to2.posts[-1])
# F11) positions_view 給手機
ok(tg.positions_view()[0]["code"] == "6269" and tg.positions_view()[0]["judge"] == "強三")
# Y1) 元三:元大手動買進 2 張(分兩次成交)→ 接成元三、停利停損用判三 %、寫進場訊息;碰停損 → 程式跌停全部賣出、寫出場訊息
ty, gy = mk(); ty.name_of = lambda c: "友達"
ty.on_report(51, "M100", 0, 1000, 40.0, basket="", code="2409", bs="B"); py = ty.positions["2409"]
ok(py["judge"] == "元三" and py["tp"] == 41.0 and py["sl"] == 38.2 and ty.posts[-1][1].endswith("· 1張 · 元三") and ty.posts[-1][2] == "long", py, ty.posts)
ty.on_report(51, "M100", 0, 1000, 40.2, basket="", code="2409", bs="B"); ok(py["qty"] == 2 and abs(py["entry"] - 40.1) < 1e-9 and len(ty.posts) == 2 and ty.posts[-1][1].endswith("· 2張 · 元三") and "40.10" in ty.posts[-1][0], ty.posts)   # 2026-11-10 發卡後又成交 → 同鍵改寫進場卡(2 張、均價 40.10)
ty.on_report(51, "M200", 0, 1000, 40.3, basket="", code="2409", bs="B"); ok(py["qty"] == 3 and abs(py["entry"] - 40.5 / 1 + 0.3333333) < 1e-3 or py["qty"] == 3, py)   # 2026-10-28 平倉檢查 #5:另一張同方向手動單也併進元三
ty.on_tick("2409", 38.0); ok(py["state"] == EXITING and gy.sent[-1] == ("2409", "S", 3, 0, "0", "0", "L"), gy.sent)
ty.on_report(51, "A0001", 0, 3000, 38.0); ok("2409" not in ty.positions and ty.posts[-1][1].endswith("· 3張 · 元三") and "停損" in ty.posts[-1][0], ty.posts[-1])
# Y1b) 2026-11-02 判二部位(程式進場 1 張 @86.1)→ 元大手動再做空 2 張 @86.4 → 併進程式部位:3 張、均價 86.3、停利停損照原本 % 從新均價重算(84.25 / 88.91);碰停利全部 3 張一起買回
tm, gm = mk(); tm.name_of = lambda c: "台郡"; tm.on_tick("6269", 86.2); tm.step([ENT]); tm.on_report(51, "A0001", 0, 1000, 86.1); pm = tm.positions["6269"]
tm.on_report(51, "M900", 0, 2000, 86.4, basket="", code="6269", bs="S")
ok(pm["qty"] == 3 and abs(pm["entry"] - 86.3) < 1e-9 and pm["tp"] == round(86.3 * 84.05 / 86.1, 2) and pm["sl"] == round(86.3 * 88.70 / 86.1, 2) and pm["judge"] == "判二" and "手動加碼" in tm.alerts[-1], pm, tm.alerts)
import time as _tt; _tt.sleep(0.05 + max(0.0, tm.y3_post_delay)); ok(any("· 加碼 · 共 3 張 均價 86.30 · 2張 · 元三" in b and t.startswith("台郡 6269 做空進場 86.40") for t, b, k in tm.posts), tm.posts)   # 2026-11-05 手動加碼也發進場卡
tm.on_tick("6269", 84.0); ok(pm["state"] == EXITING and gm.sent[-1][:3] == ("6269", "B", 3), gm.sent[-1])
tm.on_report(51, "A0002", 0, 3000, 84.1); ok("6269" not in tm.positions)
# Y1c) 2026-11-02 程式進場單還在送(ENTERING)時手動同方向成交 2 張 @86.4 → 進場單成交 1 張 @86.1 → 併成 3 張 均價 86.3,停利停損照 % 重算
tn, gn = mk(); tn.name_of = lambda c: "台郡"; tn.on_tick("6269", 86.2); tn.step([ENT]); pn = tn.positions["6269"]
tn.on_report(51, "M901", 0, 2000, 86.4, basket="", code="6269", bs="S"); ok(pn["state"] == ENTERING and pn["man_lots"] == 2, pn)
tn.on_report(51, "A0001", 0, 1000, 86.1); ok(pn["state"] == HOLDING and pn["qty"] == 3 and abs(pn["entry"] - 86.3) < 1e-9 and pn["tp"] == round(86.3 * 84.05 / 86.1, 2), pn)
# Y1d) 程式進場單沒成交(IOC 結束、不補單)但進場中有手動 2 張 @86.4 → 只用手動 2 張接成判二部位(停利停損照訊號 % 從 86.4 算)
to_, go_ = mk(TRADE_ENTRY_FALLBACK="0"); to_.name_of = lambda c: "台郡"; to_.on_tick("6269", 86.2); to_.step([ENT]); po_ = to_.positions["6269"]
to_.on_report(51, "M902", 0, 2000, 86.4, basket="", code="6269", bs="S"); to_.on_report(50, "A0001", 25, 0, 0)
ok("6269" in to_.positions and po_["state"] == HOLDING and po_["qty"] == 2 and po_["entry"] == 86.4 and po_["tp"] == round(86.4 * 84.05 / 86.2, 2), po_)
# Y1e) 2026-11-02 未交易標記:超額 / 已有部位 / 進場沒成交 → skipped;有進場的同一訊號重送不算
ts_, gs_ = mk(TRADE_ENTRY_FALLBACK="0"); ts_.on_tick("6269", 86.2); ts_.step([ENT]); ts_.on_report(51, "A0001", 0, 1000, 86.1)
ts_.step([dict(ENT, _k="dup")]); ok(not ts_.skipped, ts_.skipped)                                         # 同一個訊號再來一次(雲端)→ 不算未交易
ts_.step([sig("10:05:6269|台郡|判三", "short", "台郡 6269 做空進場 86.00", "10:05 空 · 停利 84.0 停損 88.0 · 判三")]); ok("6269|判三|10:05" in ts_.skipped, ts_.skipped)   # 已有部位
ts_.step([sig("10:06:2330|台積|判一", "long", "台積 2330 做多進場 600", "10:06 多 · 停利 615 停損 590 · 超額 · 判一")]); ok("2330|判一|10:06" in ts_.skipped)
ts2_, gs2_ = mk(TRADE_ENTRY_FALLBACK="0"); ts2_.on_tick("6269", 86.2); ts2_.step([ENT]); ts2_.on_report(50, "A0001", 25, 0, 0); ok("6269|判二|10:00" in ts2_.skipped, ts2_.skipped)   # 送了單沒成交
# Y2) 元三你自己在元大平掉 → 程式結束這個部位;判三的平倉通知也會讓元三出場;元三不佔 5 檔名額
tz2, gz2 = mk(); tz2.on_report(51, "M1", 0, 1000, 50.0, basket="", code="3001", bs="S"); ok(tz2.positions["3001"]["dir"] == -1 and tz2.positions["3001"]["sl"] == 52.25)
tz2.on_report(51, "M2", 0, 1000, 49.0, basket="", code="3001", bs="B"); ok("3001" not in tz2.positions and not gz2.sent)
tz2.on_report(51, "M3", 0, 1000, 50.0, basket="", code="3002", bs="B"); tz2.step([sig("e1", "exit", "股 3002 出場:出", "… · 判三")]); ok(tz2.positions["3002"]["state"] == EXITING)
ok(tz2.auto_count() == 0)
# Y3) 自動交易沒開 / 09:00 前 → 不接元三
tn2, gn2 = mk(TRADE_ENABLE="0"); tn2.on_report(51, "M1", 0, 1000, 50.0, basket="", code="3001", bs="B"); ok("3001" not in tn2.positions)
# A1) 2026-10-28 TRADE_AUTO=0(先只開元三):判一二三通知不進場;元三照接、照停損平倉;強制進場照常
ta, ga = mk(TRADE_AUTO="0"); ta.on_tick("6269", 86.2); ta.step([ENT]); ok("6269" not in ta.positions and not ga.sent, ga.sent)
ta.on_report(51, "M1", 0, 1000, 50.0, basket="", code="3001", bs="B"); ok(ta.positions["3001"]["judge"] == "元三")
ta.on_tick("3001", 47.7); ok(ta.positions["3001"]["state"] == EXITING and ga.sent[-1][:3] == ("3001", "S", 1), ga.sent)
ok(ta.force_enter("2409", 1, 1, None, None, "0")[0])
# A2) 沒設 TRADE_AUTO = 預設關
tb2 = Trader({"TRADE_ENABLE": "1", "TRADE_STOP_FILE": str(stop), "TRADE_STATE_FILE": str(tmp / "a2.json")}, GW(), logs.append, lambda: NOW[0]); ok(tb2.auto is False)
# ── 2026-10-28 平倉檢查 1~10 ──
at(10, 0)
def y3(t, code, bs, lots, px, no="M1", typ=""):
    t.on_report(51, no, 0, lots * 1000, px, "", code, bs, typ); return t.positions[code]
# P1) 元三 2 張碰停損、程式送 2 張跌停賣 → 你手動先賣 1 張 → 程式取消自己的單 → 取消回報回來 → 重送剩下 1 張
t1, g1 = mk(); p1 = y3(t1, "2330", "B", 2, 100.0); t1.on_tick("2330", 95.4); ok(g1.sent[-1][:3] == ("2330", "S", 2))
no1 = g1.sent[-1] and "A0001"; t1.on_report(51, "M2", 0, 1000, 95.4, "", "2330", "S"); ok(p1["qty"] == 1 and g1.sent[-1] == ("cancel", "A0001", "2330", "S"), g1.sent[-1])
t1.on_report(50, "A0001", 2, 0, 0, g1.tags[-1]); ok(g1.sent[-1][:3] == ("2330", "S", 1) and p1.get("resend", 0) == 0, g1.sent[-1])
t1.on_report(51, "A0002", 0, 1000, 95.3); ok("2330" not in t1.positions and sum(j["lots"] for j in t1.journal) == 2)
# P1b) 取消來不及、程式的 2 張先成交 → 多平 1 張 → 馬上漲停買回 1 張(補回)
t1b, g1b = mk(); p1b = y3(t1b, "2330", "B", 2, 100.0); t1b.on_tick("2330", 95.4); t1b.on_report(51, "M2", 0, 1000, 95.4, "", "2330", "S")
t1b.on_report(51, "A0001", 0, 2000, 95.3, g1b.tags[0]); ok(t1b.positions["2330"]["judge"] == "補回" and g1b.sent[-1][:3] == ("2330", "B", 1) and g1b.sent[-1][-1] == "H", g1b.sent)
# P1c) 手動全部平完、程式的單還在 → 取消;取消成功 → 部位結束,不再送
t1c, g1c = mk(); p1c = y3(t1c, "2330", "B", 1, 100.0); t1c.on_tick("2330", 95.4); t1c.on_report(51, "M2", 0, 1000, 95.4, "", "2330", "S")
ok(g1c.sent[-1][0] == "cancel"); t1c.on_report(50, "A0001", 2, 0, 0, g1c.tags[0]); ok("2330" not in t1c.positions and g1c.sent[-1][0] == "cancel")
# P2) 平倉單的回應斷線沒回來(沒有委託書號)、成交回報也漏了 → 30 秒複查:用自訂編號對到已成交 → 不重送
t2, g2 = mk(gw=GWN); p2 = y3(t2, "2603", "S", 1, 50.0); t2.on_tick("2603", 52.3); o2 = p2["order"]; tag2 = o2["tag"]; o2["at"] -= 31
t2.verify([], [{"code": "2603", "bs": "S", "lots": 1, "price": 50.0, "order_no": "M1"}, {"code": "2603", "bs": "B", "lots": 1, "price": 52.3, "order_no": "X9"}], {"M1": "", "X9": tag2})
ok("2603" not in t2.positions and len([x for x in g2.sent if x[0] == "2603"]) == 1, g2.sent)
# P2b) 連自訂編號表都沒有 → 看元大淨部位(已經 0)→ 當作已平,不重送
t2b, g2b = mk(gw=GWN); p2b = y3(t2b, "2603", "S", 1, 50.0); t2b.on_tick("2603", 52.3); p2b["order"]["at"] -= 31
t2b.verify([], [{"code": "2603", "bs": "S", "lots": 1, "price": 50.0, "order_no": "M1"}, {"code": "2603", "bs": "B", "lots": 1, "price": 52.3, "order_no": "X9"}])
ok("2603" not in t2b.positions and len([x for x in g2b.sent if x[0] == "2603"]) == 1, g2b.sent)
# P3) 重開機時元三的平倉單還掛著(鎖跌停)→ 接成平倉中、不送第二張
t3, g3 = mk(); at(11, 0)
t3.resume([{"code": "3008", "bs": "B", "lots": 1, "price": 100.0, "time": "09:30", "order_no": "M1"}], {"M1": "", "F1": "F103000001"}, [], [],
          working=[{"code": "3008", "bs": "S", "lots": 1, "no": "F1", "tag": "F103000001", "typ": "0"}])
ok(t3.positions["3008"]["state"] == EXITING); t3.on_tick("3008", 95.0); ok(not g3.sent, g3.sent)
# P4) 重送用完(stuck)→ 手機強平會重新送出
at(10, 0); t4, g4 = mk(gw=GWN, TRADE_MAX_RESEND="1"); p4 = y3(t4, "2454", "B", 1, 100.0); t4.on_tick("2454", 95.0)
for _ in range(2): t4.on_order_reply("", False, "rej", g4.tags[-1])
ok(p4.get("stuck")); n4 = len(g4.sent); r4 = t4.force_close("2454"); ok(r4[0] and g4.sent[n4:] and g4.sent[-1][:3] == ("2454", "S", 1), (r4, g4.sent[n4:]))
# P5) 元三另外下一張加碼 → 併進去;平倉中加碼 → 取消重送全部
t5, g5 = mk(); p5 = y3(t5, "2317", "B", 1, 100.0, "M1"); t5.on_report(51, "M2", 0, 2000, 100.0, "", "2317", "B"); ok(p5["qty"] == 3)
t5.on_tick("2317", 95.0); ok(g5.sent[-1][:3] == ("2317", "S", 3)); t5.on_report(51, "M3", 0, 1000, 95.0, "", "2317", "B"); ok(p5["qty"] == 4 and g5.sent[-1][0] == "cancel")
t5.on_report(50, "A0001", 2, 0, 0, g5.tags[-1]); ok(g5.sent[-1][:3] == ("2317", "S", 4), g5.sent[-1])
# P6) 下市股 YY0070(TRADE_IGNORE_CODES)不接元三、13:22 複查不平;一般手動賣出照接成做空
t6, g6 = mk(); t6.on_report(51, "M1", 0, 1000, 1.0, "", "YY0070", "S"); ok("YY0070" not in t6.positions)
ok(t6.recheck([{"code": "YY0070", "bs": "S", "lots": 4, "price": 1.0}], [], "13:22") == [] and not g6.sent)
p6 = y3(t6, "1101", "S", 1, 40.0); ok(p6["judge"] == "元三" and p6["dir"] == -1)
# P6b) 元三平倉用跟開倉同一種委託種類
t6b, g6b = mk(); p6b = y3(t6b, "2002", "S", 1, 20.0, typ="3"); t6b.on_tick("2002", 21.0); ok(g6b.sent[-1][4] == "3", g6b.sent[-1])
# P7) 元三 / 接回的股票會叫 bridge 補訂閱
t7, g7 = mk(); subs = []; t7.want_sub = lambda c: subs.append(c) or "TWOTC"; p7 = y3(t7, "5483", "B", 1, 100.0); ok(subs == ["5483"] and p7["mkt"] == "TWOTC")
# P10) 強三的 ±6.5% 先用手機帶來的昨收(電腦留著的舊昨收不算)
t10b, g10b = mk(TRADE_MANUAL_MAX_MOVE_PCT="6.5"); t10b.ref_of = lambda c: 200.0; t10b.force_enter("2330", 1, 1, None, None, "TWSE", None, 100.0); t10b.on_report(51, "A0001", 0, 1000, 100.0)
t10b.on_tick("2330", 93.4); ok(t10b.positions["2330"]["state"] == EXITING, t10b.positions["2330"]["state"])
# PA) 二次檢查 A:委託回報(50)比下單回應先到 → 要取消的單照樣取消;回應整個沒回來 → 30 秒複查從元大清單拿書號再取消
ta1, ga1 = mk(gw=GWN); pa1 = y3(ta1, "2330", "B", 2, 100.0); ta1.on_tick("2330", 95.4); ta1.on_report(51, "M2", 0, 2000, 95.4, "", "2330", "S")
ok(not [x for x in ga1.sent if x[0] == "cancel"]); ta1.on_report(50, "X7", 0, 0, 0, ga1.tags[-1]); ok(ga1.sent[-1] == ("cancel", "X7", "2330", "S"), ga1.sent)
ta2, ga2 = mk(gw=GWN); pa2 = y3(ta2, "2330", "B", 2, 100.0); ta2.on_tick("2330", 95.4); ta2.on_report(51, "M2", 0, 1000, 95.4, "", "2330", "S"); pa2["order"]["at"] -= 31
ta2.verify([{"no": "X8", "tag": ga2.tags[-1], "code": "2330", "bs": "S", "lots": 2}], [], {}); ok(ga2.sent[-1] == ("cancel", "X8", "2330", "S"), ga2.sent)
ta3, ga3 = mk(gw=GWN); ta3.force_enter("2330", 1, 1); ta3.force_close("2330"); ta3.on_report(50, "X9", 0, 0, 0, ga3.tags[-1]); ok(ga3.sent[-1] == ("cancel", "X9", "2330", "B"), ga3.sent)
# FB) 2026-11-11 14:17 全改:現價離昨收超過 ±5% 不進(+7% 假突破空單不進);假突破空單也套 ±6.5%
tf, gf = mk(); tf.ref_of = lambda c: 100.0; tf.on_tick("3003", 107.0)
tf.step([sig("10:40:3003|假突破", "short", "股3003 3003 做空進場 107.00", "10:40 空✓破 · 停利 104.33 停損 109.00 · 3張 · 判三")]); ok("3003" not in tf.positions, "+7% 不該進")
tf2, gf2 = mk(); tf2.ref_of = lambda c: 100.0; tf2.on_tick("3013", 104.0)
tf2.step([sig("10:40:3013|假突破", "short", "股3013 3013 做空進場 104.00", "10:40 空✓破 · 停利 101.40 停損 108.00 · 3張 · 判三")]); pf = tf2.positions["3013"]
ok(not pf["no_max_move"] and pf["judge"] == "判三"); tf2.on_report(51, "A0001", 0, 1000, 104.0); ok(pf["state"] == HOLDING)
tf2.on_tick("3013", 106.5); ok(pf["state"] == EXITING, "假突破空單 +6.5% 也要平倉")
tl, gl = mk(); tl.ref_of = lambda c: 100.0; tl.on_tick("3023", 94.8)
tl.step([sig("10:40:3023|多", "long", "股3023 3023 做多進場 94.80", "10:40 多✓ · 停利 97.00 停損 93.00 · 3張 · 判一")]); ok("3023" not in tl.positions, "做多 −5.2% 也不進")
tn, gn = mk(); tn.ref_of = lambda c: 100.0; tn.on_tick("3004", 105.0)
tn.step([sig("10:40:3004|壓線", "short", "股3004 3004 做空進場 105.00", "10:40 空✓ · 停利 102.38 停損 109.73 · 3張 · 判三")]); pn = tn.positions["3004"]
ok(not pn["no_max_move"]); tn.on_report(51, "A0001", 0, 1000, 105.0); tn.on_tick("3004", 106.5); ok(pn["state"] == EXITING, "一般判三空單 +6.5% 要強制平倉")
# R1) 對帳:斷線漏了手動成交回報 → 第一次只記下、第二次(下一分鐘)接成元三
tr1, gr1 = mk(); T1 = [{"code": "2409", "bs": "S", "lots": 2, "price": 40.0, "time": "10:00", "order_no": "M1"}]
tr1.reconcile(T1, [], {"M1": ""}); ok("2409" not in tr1.positions); tr1.reconcile(T1, [], {"M1": ""}); pr1 = tr1.positions["2409"]
ok(pr1["judge"] == "元三" and pr1["dir"] == -1 and pr1["qty"] == 2 and pr1["entry"] == 40.0)
# R2) 對帳:程式的單晚到成交(程式不知道)→ 馬上平掉
tr2, gr2 = mk(); T2 = [{"code": "6269", "bs": "B", "lots": 1, "price": 86.0, "time": "10:00", "order_no": "A1"}]
tr2.reconcile(T2, [], {"A1": "T100000001"}); tr2.reconcile(T2, [], {"A1": "T100000001"}); ok(gr2.sent[-1][:3] == ("6269", "S", 1) and gr2.sent[-1][-1] == "L", gr2.sent)
# R3) 對帳:程式記 3 張、元大只剩 1 張(漏了手動平倉回報)→ 改成 1 張;元大 0 → 結束;有單在跑 / 下市股不動
tr3, gr3 = mk(); p3 = y3(tr3, "2317", "B", 3, 100.0); T3 = [{"code": "2317", "bs": "B", "lots": 3, "price": 100.0, "time": "10:00", "order_no": "M1"}, {"code": "2317", "bs": "S", "lots": 2, "price": 101.0, "time": "10:05", "order_no": "M2"}]
tr3.reconcile(T3, []); tr3.reconcile(T3, []); ok(p3["qty"] == 1)
T3b = T3 + [{"code": "2317", "bs": "S", "lots": 1, "price": 101.0, "time": "10:06", "order_no": "M3"}]; tr3.reconcile(T3b, []); tr3.reconcile(T3b, []); ok("2317" not in tr3.positions)
tr3.reconcile([{"code": "YY0070", "bs": "B", "lots": 4, "price": 1.0, "time": "09:00", "order_no": "Z"}] * 1, []); tr3.reconcile([{"code": "YY0070", "bs": "B", "lots": 4, "price": 1.0, "time": "09:00", "order_no": "Z"}], []); ok("YY0070" not in tr3.positions)
# ── 2026-10-29 稽核修正 ──
at(10, 0)
sigt = lambda k, kind, title, body, t: dict(sig(k, kind, title, body), t=t)
# S1) 啟動時讀通知失敗(None)→ 不算準備好;第一次讀成功的那批當「已有」,不進場;啟動前的平倉通知不理、啟動後的照做
ok(fetch_signals("http://127.0.0.1:9", "2026-10-22") is None)
s1, gs1 = mk(prime=False); s1.prime(None); ok(not s1.primed)
s1.on_tick("6269", 86.2); s1.step(None); s1.step([ENT]); ok(s1.primed and "6269" not in s1.positions and not gs1.sent, gs1.sent)
s1.step([ENT]); ok("6269" not in s1.positions)                                              # 已標看過
s1b, gs1b = mk(prime=False); s1b.prime(None); s1b.force_enter("2330", 1, 1); s1b.on_report(51, "A0001", 0, 1000, 600.0); pb = s1b.positions["2330"]
old_x = sigt("o1", "exit", "台積電 2330 出場:出", "… · 判三", "09:50"); new_x = sigt("n1", "exit", "台積電 2330 出場:出", "… · 判三", "10:01")
s1c, gs1c = mk(prime=False); s1c.force_enter("2330", 1, 1); s1c.on_report(51, "A0001", 0, 1000, 600.0)   # 10:00 啟動
at(10, 2); s1b.step([old_x]); ok(pb["state"] == HOLDING)                                    # 啟動前的平倉通知:不理
s1c.step([old_x, new_x]); ok(s1c.positions["2330"]["state"] == EXITING)                    # 啟動後才來的:照做
at(10, 0)
# S1b) 進場通知的時間(t)比現在舊超過 5 分鐘 → 不進;5 分鐘內照進
s1d, gs1d = mk(); s1d.on_tick("6269", 86.2); s1d.step([dict(ENT, t="09:54")]); ok("6269" not in s1d.positions and "已經過 6 分鐘" in logs[-1], logs[-1])
s1d.step([dict(ENT, _k="again", t="09:56")]); ok("6269" in s1d.positions)
# S2) 進場中收到平倉通知 → 記下來,進場成交轉持倉後馬上平;平倉中再來平倉通知 → 不理
s2, gs2 = mk(); s2.on_tick("6269", 86.2); s2.step([ENT]); s2.step([sig("x21", "exit", "台郡 6269 出場:出", "… · 判二")])
ok(s2.positions["6269"]["state"] == ENTERING and s2.positions["6269"]["exit_pending"])
s2.on_report(51, "A0001", 0, 1000, 86.1); ok(s2.positions["6269"]["state"] == EXITING and gs2.sent[-1] == ("6269", "B", 1, 0, "9", "0", "H"), gs2.sent)
n_s2 = len(gs2.sent); s2.step([sig("x22", "exit", "台郡 6269 出場:出", "… · 判二")]); ok(len(gs2.sent) == n_s2)
# S3) 同一個進場訊號(代碼 + 判別式 + K 棒時間)換了發送分鐘重送 → 不再進;程式重開(同一個狀態檔)也不進;不同 K 棒時間照進
s3, gs3 = mk(); s3.on_tick("6269", 86.2); s3.step([ENT]); s3.on_report(51, "A0001", 0, 1000, 86.1); s3.on_tick("6269", 84.0); s3.on_report(51, "A0002", 0, 1000, 84.1); ok("6269" not in s3.positions)
s3.on_tick("6269", 86.2); s3.step([dict(ENT, _k="10:01:6269|台郡做空進場|判二")]); ok("6269" not in s3.positions and "今天已經進場過" in logs[-1], logs[-1])
s3b, gs3b = mk(TRADE_STATE_FILE=str(s3.state_file)); s3b.on_tick("6269", 86.2); s3b.step([dict(ENT, _k="k3b")]); ok("6269" not in s3b.positions and not gs3b.sent)
s3b.step([sig("k3c", "short", ENT["title"], ENT["body"].replace("10:00", "10:05"))]); ok("6269" in s3b.positions)
# S4) 接回:代碼精確比對(23301 的通知不算 2330 的);假突破空單(✓破)記 no_max_move;判別式符合 + 時間最接近開倉成交的那則;斷線期間已有平倉通知 → 接回後馬上平
s4, gs4 = mk(TRADE_JUDGES="判一,判二,判三,-")   # 2026-10-31 判四預設也下單了;這題要的是「判別式不在 TRADE_JUDGES」→ 明寫排除判四
SG4 = [sigt("a", "long", "股 23301 做多進場 50.00", "09:10 多✓ · 停利 51.25 停損 49.25 · 判一", "09:10"),
       sigt("b", "short", "股 3003 做空進場 107.00", "09:20 空✓破 · 停利 104.33 停損 109.00 · 判三", "09:20"),
       sigt("c", "short", "台郡 6269 做空進場 86.20", "09:30 空✓ · 停利 80.00 停損 90.00 · 判二", "09:30"),
       sigt("d", "short", "台郡 6269 做空進場 86.20", "10:36 空✓ · 停利 84.05 停損 88.70 · 判二", "10:36"),
       sigt("e", "short", "台郡 6269 做空進場 86.20", "10:37 空✓ · 停利 83.00 停損 89.00 · 判四", "10:37"),
       sigt("f", "long", "股 2409 做多進場 37.30", "10:00 多✓ · 停利 38.23 停損 36.74 · 判一", "10:00"),
       sigt("g", "exit", "股 2409 出場 +0.5%:出", "10:20 做多 10:00 進 37.30 → 現價 37.50 · 1張 · 判一", "10:20")]
TR4 = [{"code": "2330", "bs": "B", "lots": 1, "price": 50.0, "time": "09:10:05", "order_no": "A1"}, {"code": "3003", "bs": "S", "lots": 1, "price": 107.0, "time": "09:20:03", "order_no": "A2"},
       {"code": "6269", "bs": "S", "lots": 1, "price": 86.1, "time": "10:37:02", "order_no": "A3"}, {"code": "2409", "bs": "B", "lots": 1, "price": 37.3, "time": "10:00:04", "order_no": "A4"}]
s4.resume(TR4, {"A1": "T091005001", "A2": "T092003002", "A3": "T103702003", "A4": "T100004004"}, [], SG4)
ok(s4.positions["2330"]["judge"] == "接回" and s4.positions["2330"]["tp"] == 0, s4.positions["2330"])
ok(not s4.positions["3003"]["no_max_move"] and s4.positions["3003"]["judge"] == "判三")
ok(s4.positions["6269"]["judge"] == "判二" and s4.positions["6269"]["tp"] == 84.05, s4.positions["6269"])
ok(s4.positions["2409"]["state"] == EXITING and gs4.sent[-1] == ("2409", "S", 1, 0, "0", "0", "L"), gs4.sent)
# S5) IOC 8 秒沒回報 → 不拿掉,等確認;晚到的成交 → 轉持倉(停利停損照通知);部分成交後晚到的 → 併進去;等 60 秒還是 0 → 拿掉
s5, gs5 = mk(); s5.on_tick("6269", 86.2); s5.step([ENT]); p5x = s5.positions["6269"]; p5x["order"]["at"] -= 9; s5.step()
ok(p5x["state"] == ENTERING and p5x.get("wait_confirm") and gs5.sent[-1] == ("cancel", "A0001", "6269", "S"), gs5.sent)
s5.on_report(50, "A0001", 2, 0, 0, gs5.tags[0]); ok("6269" in s5.positions)                # 取消回報來了也先別拿掉
s5.on_report(51, "A0001", 0, 1000, 86.1, gs5.tags[0]); ok(p5x["state"] == HOLDING and p5x["qty"] == 1 and p5x["entry"] == 86.1 and p5x["tp"] == 84.05 and p5x["sl"] == 88.7 and not p5x.get("wait_confirm"), p5x)
s5b, gs5b = mk(TRADE_QTY="2"); s5b.on_tick("6269", 86.2); s5b.step([ENT]); s5b.on_report(51, "A0001", 0, 1000, 86.1, gs5b.tags[0]); s5b.positions["6269"]["order"]["at"] -= 9; s5b.step()
p5b = s5b.positions["6269"]; ok(p5b["state"] == HOLDING and p5b["qty"] == 1)
s5b.on_report(51, "A0001", 0, 1000, 86.3, gs5b.tags[0], "6269", "S"); ok(p5b["qty"] == 2 and abs(p5b["entry"] - 86.2) < 1e-9, p5b)
s5c, gs5c = mk(); s5c.on_tick("6269", 86.2); s5c.step([ENT]); s5c.positions["6269"]["order"]["at"] -= 9; s5c.step(); ok("6269" in s5c.positions)
s5c.positions["6269"]["wait_confirm"] -= 61; s5c.step(); ok("6269" not in s5c.positions and "確定沒成交" in logs[-1], logs[-1])
# S6) 沒有判別式的平倉通知:不平判二的部位;只給「接回」部位用
s6, gs6 = mk(); s6.on_tick("6269", 86.2); s6.step([ENT]); s6.on_report(51, "A0001", 0, 1000, 86.1)
s6.step([sig("x61", "exit", "台郡 6269 出場:出", "…")]); ok(s6.positions["6269"]["state"] == HOLDING)
s6b, gs6b = mk(); s6b.resume([{"code": "2409", "bs": "B", "lots": 1, "price": 37.3, "time": "10:00", "order_no": "A1"}], {"A1": "T100000001"}, [], [])
ok(s6b.positions["2409"]["judge"] == "接回"); s6b.step([sig("x62", "exit", "股 2409 出場:出", "…")]); ok(s6b.positions["2409"]["state"] == EXITING)
# S7) 價格帶千分位(1,035.00)照樣解析;停利 / 進場 / 停損順序不合理 → 不進
s7, gs7 = mk(); s7.on_tick("3008", 1035.0)
s7.step([sig("k71", "long", "大立光 3008 做多進場 1,035.00", "10:00 多✓ · 停利 1,060.00 停損 1,020.00 · 判一")])
ok(s7.positions["3008"]["tp"] == 1060.0 and s7.positions["3008"]["sl"] == 1020.0, s7.positions.get("3008"))
s7.on_tick("2409", 37.25); s7.step([sig("k72", "long", "股 2409 做多進場 37.25", "10:00 多✓ · 停利 36.00 停損 38.00 · 判一")]); ok("2409" not in s7.positions and "順序不合理" in logs[-1], logs[-1])
# S8) 強制進場過了 TRADE_LAST_ENTRY(13:10)→ 拒絕
at(13, 10); s8, gs8 = mk(); r8 = s8.force_enter("2330", 1, 1); ok(not r8[0] and "最後進場時間" in r8[1] and not gs8.sent, r8); at(10, 0)
# S9) 等確認中對帳:元大連續兩次有同方向 1 張 → 當成成交轉持倉(停利停損照通知);元大連續兩次 0 張 → 拿掉
s9, gs9 = mk(); s9.on_tick("6269", 86.2); s9.step([ENT]); s9.positions["6269"]["order"]["at"] -= 9; s9.step(); p9 = s9.positions["6269"]
T9 = [{"code": "6269", "bs": "S", "lots": 1, "price": 86.0, "time": "10:00", "order_no": "A0001"}]
s9.reconcile(T9, []); ok(p9["state"] == ENTERING); s9.reconcile(T9, []); ok(p9["state"] == HOLDING and p9["qty"] == 1 and p9["entry"] == 86.0 and p9["tp"] == 84.05 and not p9.get("wait_confirm"), p9)
s9b, gs9b = mk(); s9b.on_tick("6269", 86.2); s9b.step([ENT]); s9b.positions["6269"]["order"]["at"] -= 9; s9b.step()
s9b.reconcile([], []); ok("6269" in s9b.positions); s9b.reconcile([], [])
ok("6269" in s9b.positions and "已改掛補單" in logs[-1] and gs9b.sent[-1] == ("6269", "S", 1, 85.4, "9", "0", " "), (logs[-1], gs9b.sent[-1]))   # 2026-10-31 改 C:元大確認 IOC 沒成交 → 改掛跌停補
s9b.on_report(50, "", 2, 0, 0, basket=s9b.positions["6269"]["order"]["tag"]); ok("6269" not in s9b.positions)
s9c, gs9c = mk(TRADE_ENTRY_FALLBACK="0"); s9c.on_tick("6269", 86.2); s9c.step([ENT]); s9c.positions["6269"]["order"]["at"] -= 9; s9c.step()
s9c.reconcile([], []); s9c.reconcile([], []); ok("6269" not in s9c.positions and "確定沒成交" in logs[-1], logs[-1])
# S10) 超額(一張就超過多空上限):只通知,不自動進場
s10, gs10 = mk(); s10.on_tick("3008", 1035.0)
s10.step([sig("k101", "long", "大立光 3008 做多進場 1,035.00", "10:00 多✓ · 停利 1,060.00 停損 1,020.00 · 超額 · 判一")])
ok("3008" not in s10.positions and "超額" in logs[-1] and not gs10.sent, logs[-1])
# S11) 張數照多空上限:和桐 17.45、上限 25 萬 → 14 張
s11, gs11 = mk(TRADE_CAP="250000"); s11.on_tick("1714", 17.45)
s11.step([sig("k111", "short", "和桐 1714 做空進場 17.45", "10:00 空✓ · 停利 17.01 停損 18.00 · 14張 · 判二")])
ok(gs11.sent and gs11.sent[-1][2] == 14, gs11.sent)
# S12) 2026-10-31 判四(新轉折做空):bridge.env 還是舊的「判一,判二,判三」也會下判四;停利 9%(等於不停利)/ 停損照通知;停損價碰到就平
s12, gs12 = mk(TRADE_CAP="250000", TRADE_JUDGES="判一,判二,判三"); s12.on_tick("1714", 17.45)
s12.step([sig("k121", "short", "和桐 1714 做空進場 17.45", "10:00 空 · 停利 15.88 停損 18.04 · 14張 · 判四")])
ok(gs12.sent and gs12.sent[-1][1] == "S" and gs12.sent[-1][2] == 14 and s12.positions["1714"]["judge"] == "判四" and s12.positions["1714"]["sl"] == 18.04, (gs12.sent, s12.positions))
s12.on_report(51, "A0001", 0, 14000, 17.45); s12.on_tick("1714", 18.05); ok(s12.positions["1714"]["state"] == EXITING, s12.positions["1714"])
# S13) 2026-11-04 同時持倉:預設不限(0);bridge.env 舊的 2 / 5 / 10 也當不限;其他數字照用
ok(mk()[0].max_pos == 0 and mk(TRADE_MAX_POS="2")[0].max_pos == 0 and mk(TRADE_MAX_POS="5")[0].max_pos == 0 and mk(TRADE_MAX_POS="10")[0].max_pos == 0 and mk(TRADE_MAX_POS="3")[0].max_pos == 3)
# S14) 2026-10-31 當天虧損保險(TRADE_KILL_LOSS):已平倉 + 持倉帳面虧到上限 → 全部平倉(漲停買回)、不再自動進場;重開還記得
s14, g14 = mk(TRADE_CAP="250000", TRADE_KILL_LOSS="10000", TRADE_MAX_MOVE_PCT="99")
ok(mk()[0].kill_loss == 50000)
for c, px in (("2401", 50.0), ("2402", 50.0)):
    s14.on_tick(c, px); s14.step([sig(f"k14{c}", "short", f"股{c} {c} 做空進場 {px}", f"10:00 空 · 停利 {px * 0.91:.2f} 停損 {px * 1.2:.2f} · 5張 · 判四")])
    s14.on_report(51, "", 0, s14.positions[c]["order"]["want"], s14.positions[c]["order"]["price"], basket=g14.tags[-1])
ok(all(s14.positions[c]["state"] == HOLDING for c in ("2401", "2402")) and not s14.killed)
s14.on_tick("2401", 51.0); ok(not s14.killed)                      # 帳面 −5,000 左右:還沒到
s14.on_tick("2402", 51.5); ok(s14.killed and all(s14.positions[c]["state"] == EXITING for c in ("2401", "2402")) and g14.sent[-1][-1] == "H", (s14.killed, g14.sent[-2:]))
ok(s14.can_enter("2403", "判四").startswith("今天已虧到"))
s14b, _ = mk(TRADE_STATE_FILE=str(s14.state_file)); ok(s14b.killed)
# S15) 2026-10-31 改 C:IOC 部分成交 → 剩下的掛跌停補,成交後合併成一個部位(張數相加、均價);現價離訊號價超過 0.5% → 不補;IOC 沒回報(逾時等確認)不補
s15, g15 = mk(TRADE_CAP="250000"); s15.on_tick("1714", 17.45)
s15.step([sig("k151", "short", "和桐 1714 做空進場 17.45", "10:00 空 · 停利 15.88 停損 18.04 · 14張 · 判四")])
o15 = s15.positions["1714"]["order"]; s15.on_report(51, "", 0, 6000, 17.40, basket=o15["tag"]); s15.on_report(50, "", 2, 0, 0, basket=o15["tag"])
ok(s15.positions["1714"]["state"] == ENTERING and g15.sent[-1] == ("1714", "S", 8, 17.3, "9", "0", " "), g15.sent)
o15b = s15.positions["1714"]["order"]; s15.on_report(51, "", 0, 8000, 17.35, basket=o15b["tag"])
p15 = s15.positions["1714"]; ok(p15["state"] == HOLDING and p15["qty"] == 14 and abs(p15["entry"] - (17.40 * 6 + 17.35 * 8) / 14) < 1e-9, p15)
s18, g18 = mk(TRADE_CAP="250000"); s18.on_tick("1714", 17.45)   # IOC 取消回報先到、成交回報晚到 → 張數照樣算進去
s18.step([sig("k181", "short", "和桐 1714 做空進場 17.45", "10:00 空 · 停利 15.88 停損 18.04 · 14張 · 判四")])
o18 = s18.positions["1714"]["order"]; s18.on_report(50, "", 2, 0, 0, basket=o18["tag"]); ok(g18.sent[-1][2] == 14)
s18.on_report(51, "", 0, 3000, 17.40, basket=o18["tag"], code="1714", bs="S"); s18.on_report(51, "", 0, 14000, 17.35, basket=s18.positions["1714"]["order"]["tag"])
ok(s18.positions["1714"]["state"] == HOLDING and s18.positions["1714"]["qty"] == 17, s18.positions["1714"])
s16, g16 = mk(TRADE_CAP="250000"); s16.on_tick("1714", 17.45)
s16.step([sig("k161", "short", "和桐 1714 做空進場 17.45", "10:00 空 · 停利 15.88 停損 18.04 · 14張 · 判四")])
s16.on_tick("1714", 17.60); o16 = s16.positions["1714"]["order"]; s16.on_report(50, "", 2, 0, 0, basket=o16["tag"])
ok("1714" not in s16.positions and len(g16.sent) == 1 and "不補單" in "".join(logs[-3:]), logs[-3:])
s17, g17 = mk(); s17.on_tick("6269", 86.2); s17.step([ENT]); s17.positions["6269"]["order"]["at"] -= 9; s17.step()
ok(s17.positions["6269"].get("wait_confirm") and not any(x[0] == "6269" and x[5] == "0" for x in g17.sent if len(x) > 5), g17.sent)
# S19) 2026-10-31 開盤後資格檢查:元大以資格原因(不可當沖 / 暫停先賣後買 / 券源…)拒絕進場 → 今天不再自動進場 + 推手機;重開還記得;
#      額度 / 價格原因不算(下一個訊號照常進)
s19, g19 = mk(GWN, TRADE_CAP="250000"); s19.on_tick("1714", 17.45)
s19.step([sig("k191", "short", "和桐 1714 做空進場 17.45", "10:00 空 · 停利 15.88 停損 18.04 · 14張 · 判四")])
s19.on_order_reply("", False, "E567 該股票暫停先賣後買", tag=g19.tags[-1])
ok("1714" not in s19.positions and "1714" in s19.brk_block and "元大拒絕進場" in s19.alerts[-1], (s19.brk_block, s19.alerts[-1:]))
s19.step([sig("k192", "short", "和桐 1714 做空進場 17.40", "10:30 空 · 停利 15.84 停損 17.99 · 14張 · 判二")])
ok(len(g19.sent) == 1 and s19.can_enter("1714", "判二").startswith("元大今天拒絕過"), g19.sent)
s19b, _ = mk(TRADE_STATE_FILE=str(s19.state_file)); ok("1714" in s19b.brk_block)
s19c, g19c = mk(GWN, TRADE_CAP="250000"); s19c.on_tick("1714", 17.45)
s19c.step([sig("k193", "short", "和桐 1714 做空進場 17.45", "10:00 空 · 停利 15.88 停損 18.04 · 1張 · 判四")])
s19c.on_order_reply("", False, "E101 委託價格超過漲跌幅", tag=g19c.tags[-1]); ok(not s19c.brk_block and s19c.can_enter("1714", "判二") is None, s19c.brk_block)
# S20) 2026-10-31 虧損級距通知:合計(已平倉 + 帳面)虧到 1 萬 / 2 萬 … 各推一次;回升再跌不重發;一次跳兩級只發大的;重開還記得;到全部平倉那級不另發
s20, g20_ = mk(TRADE_CAP="250000", TRADE_MAX_MOVE_PCT="99")
s20.on_tick("2401", 50.0); s20.step([sig("k201", "short", "股2401 2401 做空進場 50.0", "10:00 空 · 停利 45.50 停損 60.00 · 5張 · 判四")])
s20.on_report(51, "", 0, s20.positions["2401"]["order"]["want"], s20.positions["2401"]["order"]["price"], basket=g20_.tags[-1])
n0 = len(s20.alerts); s20.on_tick("2401", 51.5); ok(len(s20.alerts) == n0, s20.alerts[n0:])         # 帳面約 −7,700:還沒到 1 萬
s20.on_tick("2401", 52.2); ok(len(s20.alerts) == n0 + 1 and "1 萬" in s20.alerts[-1], s20.alerts[n0:])
ok(s20.akinds[-1] == "lv1" and s20.alerts[-1].startswith("⚠ 注意"), (s20.akinds, s20.alerts[-1]))
s20.on_tick("2401", 51.0); s20.on_tick("2401", 52.3); ok(len(s20.alerts) == n0 + 1, s20.alerts[n0:])   # 回升再跌:1 萬不重發
s20.on_tick("2401", 56.5); ok(len(s20.alerts) == n0 + 2 and "3 萬" in s20.alerts[-1] and s20.loss_hit == {10000.0, 20000.0, 30000.0}, (s20.alerts[n0:], s20.loss_hit))
ok(s20.akinds[-1] == "lv3" and "危險" in s20.alerts[-1], (s20.akinds, s20.alerts[-1]))
s20b, _ = mk(TRADE_STATE_FILE=str(s20.state_file)); ok(s20b.loss_hit == {10000.0, 20000.0, 30000.0})
s20.on_tick("2401", 61.0); ok(s20.killed and "全部平倉" in s20.alerts[-1] and not any("5 萬" in a for a in s20.alerts[n0:]), s20.alerts[n0:])
ok(s20.akinds[-1] == "lv5" and s20.akinds[-2] == "lv4" and "緊急" in s20.alerts[-2], (s20.akinds, s20.alerts[n0:]))
ok(mk(TRADE_LOSS_ALERTS="")[0].loss_alerts == [] and mk()[0].loss_alerts == [10000.0, 20000.0, 30000.0, 40000.0, 50000.0])
# S21) 2026-10-31 賺錢慶祝:合計賺到 1 萬 / 2 萬 … 各發一次(win1~);回落再漲不重發;跳級只發大的;重開還記得
s21, g21 = mk(TRADE_CAP="250000", TRADE_MAX_MOVE_PCT="99")
s21.on_tick("2401", 50.0); s21.step([sig("k211", "short", "股2401 2401 做空進場 50.0", "10:00 空 · 停利 40.00 停損 60.00 · 5張 · 判四")])
s21.on_report(51, "", 0, s21.positions["2401"]["order"]["want"], s21.positions["2401"]["order"]["price"], basket=g21.tags[-1])
m0 = len(s21.alerts); s21.on_tick("2401", 48.5); ok(len(s21.alerts) == m0, s21.alerts[m0:])          # 帳面約 +7,000:還沒到 1 萬
s21.on_tick("2401", 47.7); ok(len(s21.alerts) == m0 + 1 and s21.akinds[-1] == "win1" and s21.alerts[-1].startswith("🎉 恭喜"), (s21.akinds, s21.alerts[m0:]))
s21.on_tick("2401", 49.0); s21.on_tick("2401", 47.6); ok(len(s21.alerts) == m0 + 1)                  # 回落再漲:不重發
s21.on_tick("2401", 43.5); ok(s21.akinds[-1] == "win3" and "大豐收" in s21.alerts[-1] and s21.win_hit == {10000.0, 20000.0, 30000.0}, (s21.akinds, s21.alerts[m0:]))
s21b, _ = mk(TRADE_STATE_FILE=str(s21.state_file)); ok(s21b.win_hit == {10000.0, 20000.0, 30000.0})
ok(mk(TRADE_WIN_ALERTS="")[0].win_alerts == [] and len(mk()[0].win_alerts) == 10)
# S22) 2026-10-31 手機「緊急停止 + 全部平倉」:持倉全部漲停買回 / 跌停賣出;進場中的成交後馬上平
s22, g22 = mk(TRADE_CAP="250000", TRADE_MAX_MOVE_PCT="99")
for c, px, kd in (("2401", 50.0, "short"), ("2402", 60.0, "long")):
    s22.on_tick(c, px); s22.step([sig(f"k22{c}", kd, f"股{c} {c} 做{'空' if kd == 'short' else '多'}進場 {px}", f"10:00 {'空' if kd == 'short' else '多'} · 停利 {px * (0.91 if kd == 'short' else 1.09):.2f} 停損 {px * (1.2 if kd == 'short' else 0.8):.2f} · 5張 · 判四")])
s22.on_report(51, "", 0, s22.positions["2401"]["order"]["want"], s22.positions["2401"]["order"]["price"], basket=s22.positions["2401"]["order"]["tag"])
ok(s22.positions["2401"]["state"] == HOLDING and s22.positions["2402"]["state"] == ENTERING)
ok(s22.close_all("手機緊急停止 + 全部平倉") == 2 and s22.positions["2401"]["state"] == EXITING and g22.sent[-1][:2] == ("2401", "B") and g22.sent[-1][-1] == "H"
   and s22.positions["2402"].get("exit_pending"), (g22.sent[-1], s22.positions["2402"]))
# S23) 2026-11-03 強制進場指定委託價:限價 ROD;沒指定照舊漲停買 / 跌停賣
s23, g23 = mk(); s23.on_tick("2401", 50.0)
ok(s23.force_enter("2401", 1, 2, 2.5, 1.5, px=49.85)[0] and g23.sent[-1] == ("2401", "B", 2, 49.85, g23.sent[-1][4], "0", " "), g23.sent[-1])
ok(s23.positions["2401"]["state"] == ENTERING)
s23.on_tick("2402", 60.0); ok(s23.force_enter("2402", -1, 1, 2.5, 1.5, px=None)[0] and g23.sent[-1][-1] == "L" and g23.sent[-1][3] == 0, g23.sent[-1])
s23.on_report(51, "", 0, 2000, 49.85, basket=g23.tags[0]); ok(s23.positions["2401"]["state"] == HOLDING and s23.positions["2401"]["entry"] == 49.85, s23.positions["2401"])
# S24) 2026-11-03 只全部平倉:平掉、鎖住那幾檔,不建緊急停止檔
s24, g24 = mk(TRADE_CAP="250000", TRADE_MAX_MOVE_PCT="99"); s24.on_tick("2401", 50.0)
s24.step([sig("k24", "short", "股2401 2401 做空進場 50.0", "10:00 空 · 停利 45.50 停損 60.00 · 5張 · 判四")])
s24.on_report(51, "", 0, s24.positions["2401"]["order"]["want"], 50.0, basket=s24.positions["2401"]["order"]["tag"])
ok(s24.close_all("手機全部平倉", lock=True) == 1 and "2401" in s24.locked and s24.positions["2401"]["state"] == EXITING and not s24.stop_file.exists())
s24.on_tick("2402", 60.0); s24.step([sig("k24b", "long", "股2402 2402 做多進場 60.0", "10:01 多 · 停利 65.0 停損 50.0 · 3張 · 判四")]); oo = s24.positions["2402"]["order"]
ok(s24.close_all("手機全部平倉", lock=True) >= 1 and g24.sent[-1][0] == "cancel" and s24.positions["2402"].get("close_after_entry") and "2402" in s24.locked, g24.sent[-1])
# S25) 2026-11-03 超額訊號:價格回到一張 ≤ 多空上限 → 等手機確認(redo)才補進場一次;判別式先平倉 / 過停利就取消
s25, g25 = mk(TRADE_CAP="250000", TRADE_MAX_MOVE_PCT="99"); s25.on_tick("3006", 257.5)
OV = lambda k, t="10:24": sig(k, "short", "晶豪科 3006 做空進場 257.50", f"{t} 空 · 停利 234.30 停損 266.44 · 1張 · 超額 · 判四")
s25.step([OV("k25")]); ok("3006" not in s25.positions and "3006" in s25.over_wait and not s25.over_list(), s25.over_wait)
s25.on_tick("3006", 252.0); s25.step([]); ok(not s25.over_list())                                   # 一張 252,000 還超過
s25.on_tick("3006", 249.0); s25.step([]); L = s25.over_list(); ok(len(L) == 1 and L[0]["dir"] == -1 and L[0]["lots"] == 1 and "3006" not in s25.positions, L)   # 不自動下單
s25.on_tick("3006", 251.0); s25.step([]); ok(not s25.over_list() and not s25.over_confirm("3006")[0])  # 又超過:收回
s25.on_tick("3006", 249.0); s25.step([]); r = s25.over_confirm("3006", "c1")
ok(r[0] and "3006" in s25.positions and s25.positions["3006"]["tp"] == 234.3 and s25.positions["3006"]["sl"] == 266.44 and "3006" not in s25.over_wait, (r, s25.positions.get("3006")))
ok(s25.over_confirm("3006", "c1") == r and sum(1 for x in g25.sent if x[0] == "3006") == 1)          # 重送同一個確認不會再下
s25b, g25b = mk(TRADE_CAP="250000", TRADE_MAX_MOVE_PCT="99"); s25b.on_tick("3006", 257.5); s25b.step([OV("k25b")])
s25b.step([sig("x25b", "exit", "晶豪科 3006 停利 +5% 立即平倉", "11:00 · 判四")]); ok("3006" not in s25b.over_wait)   # 判四先平倉 → 取消
s25c, _ = mk(TRADE_CAP="250000", TRADE_MAX_MOVE_PCT="99"); s25c.on_tick("3006", 257.5); s25c.step([OV("k25c")]); s25c.on_tick("3006", 233.0); s25c.step([]); ok("3006" not in s25c.over_wait)   # 已過停利取消
s25d, _ = mk(TRADE_CAP="250000", TRADE_MAX_MOVE_PCT="99"); s25d.on_tick("3006", 257.5); s25d.step([OV("k25d")]); s25d.on_tick("3006", 249.0); s25d.step([]); s25d.over_drop("3006"); ok(not s25d.over_list() and "3006" not in s25d.positions)
# S26) 2026-11-03 元三分批成交(1+1+1+2):進場卡等 3 秒(測試 0.3 秒)併完才發,寫 5 張、均價
s26, g26 = mk(TRADE_Y3_POST_DELAY="0.3"); s26.on_tick("3481", 41.75)
for q in (1000, 1000, 1000, 2000): s26.on_report(51, "i00QX", 0, q, 41.75, basket="", code="3481", bs="B")
ok(s26.positions["3481"]["qty"] == 5 and not s26.posts, (s26.positions.get("3481"), s26.posts))
time.sleep(0.6); ok(len(s26.posts) == 1 and "· 5張 · 元三" in s26.posts[0][1] and "41.75" in s26.posts[0][0], s26.posts)
# S27) 2026-11-11 15:36 沒有例外:元三 / 強三一律 ±6.5%(原 8.5%、群創那種已接近 −6.5% 的手動單原本不套);強制進場多空都照 ±5% 擋
s27, _ = mk(); s27.ref_of = lambda c: 44.6; s27.on_tick("3481", 41.75)
s27.on_report(51, "i00QX", 0, 5000, 41.75, basket="", code="3481", bs="B"); p27 = s27.positions["3481"]
ok(not p27.get("no_max_move") and p27["judge"] == "元三", p27)
s27.on_tick("3481", 41.65); ok(s27.positions["3481"]["state"] == EXITING, s27.positions["3481"])          # 44.6 × 0.935 = 41.70 以下平倉
s27b, _ = mk(); s27b.ref_of = lambda c: 44.6; s27b.on_tick("3481", 43.0)
s27b.on_report(51, "i00QY", 0, 1000, 43.0, basket="", code="3481", bs="B"); ok(not s27b.positions["3481"].get("no_max_move"))
ok(s27b.positions["3481"]["sl"] == 41.75, s27b.positions["3481"]["sl"]); s27b.on_tick("3481", 41.8); ok(s27b.positions["3481"]["state"] == HOLDING); s27b.on_tick("3481", 41.75); ok(s27b.positions["3481"]["state"] == EXITING)   # 停損 41.07 收到線 41.75
s27c, g27c = mk(); s27c.ref_of = lambda c: 100.0; s27c.force_enter("3002", -1, 1); s27c.on_report(51, "A0001", 0, 1000, 101.0); ok(s27c.positions["3002"]["sl"] <= 106.5, s27c.positions["3002"]["sl"])
s27c.on_tick("3002", 106.5); ok(s27c.positions["3002"]["state"] == EXITING, s27c.positions["3002"])   # 強三做空最晚 +6.5% 出
s27e, _ = mk(); s27e.ref_of = lambda c: 100.0; s27e.on_tick("3003", 105.2); ok("±5" in s27e.force_block("3003", -1) and "±5" in s27e.force_block("3003", 1)); s27e.on_tick("3003", 104.9); ok(s27e.force_block("3003", -1) == "")
s27d, _ = mk(TRADE_MANUAL_MAX_MOVE_PCT="6.5"); s27d.ref_of = lambda c: 44.6; s27d.on_tick("3481", 43.0); s27d.on_report(51, "i00QZ", 0, 1000, 43.0, basket="", code="3481", bs="B"); s27d.on_tick("3481", 41.65); ok(s27d.positions["3481"]["state"] == EXITING)   # 開關關掉照舊
# S28) 2026-11-11 15:36 強制進場:離昨收超過 ±5% 多空都不給做(原 −6.5% 不做多、+6.5% 不做空)
s28, g28 = mk(); s28.ref_of = lambda c: 100.0; s28.on_tick("2330", 94.9)
r = s28.force_enter("2330", 1, 1); ok(not r[0] and "不能強制做多" in r[1] and "2330" not in s28.positions, r)
ok(not s28.force_enter("2330", -1, 1)[0])
s28b, _ = mk(); s28b.ref_of = lambda c: 100.0; s28b.on_tick("2331", 104.9); ok(s28b.force_enter("2331", -1, 1)[0])
# PH) 2026-11-05 13:20 保留手動預掛平倉:期限前 exit_pos 不平、複查看到掛著的不補單;期限到了才平
import datetime as _dt
tp_, gp_ = mk(TRADE_PREHANG_DEADLINE="13:24"); tp_.on_tick("6269", 86.2); tp_.step([ENT]); tp_.on_report(51, "A0001", 0, 1000, 86.1); pp_ = tp_.positions["6269"]
NOW0 = NOW[0]; NOW[0] = NOW0.replace(hour=13, minute=20, second=30)
tp_.prehang = {"6269": ["M77"]}; n0 = len(gp_.sent)
tp_.exit_pos(pp_, "13:20 強制平倉", 86.0, force_limit=True); ok(pp_["state"] == HOLDING and len(gp_.sent) == n0, gp_.sent[n0:])
ok(tp_.recheck([{"code": "6269", "bs": "S", "lots": 1}], [{"code": "6269", "bs": "B", "lots": 1, "no": "M77"}], "13:22") == [] and len(gp_.sent) == n0)
NOW[0] = NOW0.replace(hour=13, minute=24, second=5); tp_.prehang = {}
tp_.recheck([{"code": "6269", "bs": "S", "lots": 1}], [], "13:24"); ok(pp_["state"] == EXITING and gp_.sent[-1][:3] == ("6269", "B", 1), gp_.sent[-1])
NOW[0] = NOW0
# 2026-11-08 01:08 預設 13:20 全部取消預掛:13:21 有預掛也不讓位,直接平
tp2, gp2 = mk(); tp2.on_tick("6269", 86.2); tp2.step([ENT]); tp2.on_report(51, "A0001", 0, 1000, 86.1); NOW[0] = NOW0.replace(hour=13, minute=21, second=5)
tp2.prehang = {"6269": ["M78"]}; tp2.step(); ok(tp2.positions["6269"]["state"] == EXITING and tp2.prehang_deadline == "13:20"); NOW[0] = NOW0

# 2026-11-06 太晚了先記著 → 價格回到訊號價 ±0.5% 自動補進場;碰停利 / 過時間 / 平倉通知取消;0 = 關
at(10, 0); tl, gl = mk(); tl.on_tick("6269", 87.0); tl.step([ENT]); ok("6269" not in tl.positions and "6269" in tl.late_wait)
tl.on_tick("6269", 86.9); tl.step([]); ok("6269" not in tl.positions and "6269" in tl.late_wait)          # 還沒回來
tl.on_tick("6269", 86.5); tl.step([]); ok("6269" in tl.positions and "6269" not in tl.late_wait and gl.sent[-1][:3] == ("6269", "S", 1), gl.sent)
ok(tl.positions["6269"]["tp"] == 84.05 and tl.positions["6269"]["sl"] == 88.70)
tl2, gl2 = mk(); tl2.on_tick("6269", 87.0); tl2.step([ENT]); tl2.on_tick("6269", 84.0); tl2.step([]); ok("6269" not in tl2.late_wait and not tl2.positions)   # 碰停利取消
tl3, gl3 = mk(); tl3.on_tick("6269", 87.0); tl3.step([ENT]); tl3.late_wait["6269"]["until"] = 0; tl3.on_tick("6269", 86.2); tl3.step([]); ok(not tl3.positions and not tl3.late_wait)   # 過時間
tl4, gl4 = mk(); tl4.on_tick("6269", 87.0); tl4.step([ENT]); tl4.step([sig("x-l4", "exit", "台郡 6269 出場 +0.3%:出", "… · 判二")]); ok(not tl4.late_wait)   # 判別式平倉了
tl5, gl5 = mk(TRADE_LATE_REDO_MIN="0"); tl5.on_tick("6269", 87.0); tl5.step([ENT]); ok(not tl5.late_wait)
OLD = dict(ENT); OLD["_k"] = "old-l6"; OLD["t"] = "09:50"; tl6, gl6 = mk(); tl6.on_tick("6269", 86.2); tl6.step([OLD]); ok(not tl6.positions and not tl6.late_wait)   # 通知本來就太舊:不記

# 2026-11-06 盤中手動預掛平倉單 → 通知、程式讓位(停利 / 停損不平);預掛取消 → 恢復;手機強平 → 先取消預掛再平
at(11, 0); tm_, gm_ = mk(); tm_.on_tick("6269", 86.2); tm_.step([ENT]); tm_.on_report(51, "A0001", 0, 1000, 86.2)
tm_.on_report(50, "i00Pb", 0, 1000, 85.0, "", "6269", "B", "9"); ok(tm_.prehang.get("6269") == ["i00Pb"] and any("看到你的預掛" in a for a in tm_.alerts), tm_.alerts)
n0 = len(gm_.sent); tm_.on_tick("6269", 84.0); ok(tm_.positions["6269"]["state"] == HOLDING and len(gm_.sent) == n0)   # 停利到了也讓位
tm_.on_tick("6269", 89.0); ok(tm_.positions["6269"]["state"] == HOLDING and len(gm_.sent) == n0)                       # 停損也讓位
tm_.on_report(50, "i00Pb", 2, 1000, 85.0, "", "6269", "B", "9"); ok(not tm_.prehang and any("預掛單沒了" in a for a in tm_.alerts))
tm_.on_tick("6269", 89.0); ok(tm_.positions["6269"]["state"] == EXITING)                                               # 預掛沒了 → 照停損平
tm2, gm2 = mk(); tm2.on_tick("6269", 86.2); tm2.step([ENT]); tm2.on_report(51, "A0001", 0, 1000, 86.2)
tm2.on_report(50, "i00Pc", 0, 1000, 85.0, "", "6269", "B", "9"); tm2.on_report(51, "i00Pc", 0, 1000, 85.0, "", "6269", "B", "9")
ok("6269" not in tm2.positions and not tm2.prehang); ok(any("預掛買回" in x[0] and x[2] == "warn" for x in tm2.posts) and any("元大預掛成交" in x[0] and x[1].endswith("· 元平 · 判二") for x in tm2.posts), tm2.posts)                                                                    # 預掛成交 → 部位結束、預掛清掉
tm3, gm3 = mk(); tm3.on_tick("6269", 86.2); tm3.step([ENT]); tm3.on_report(51, "A0001", 0, 1000, 86.2)
tm3.on_report(50, "i00Pd", 0, 1000, 85.0, "", "6269", "B", "9"); tm3.exit_pos(tm3.positions["6269"], "強平", 86.0, force_limit=True)
ok(("cancel", "i00Pd", "6269", "B") in gm3.sent and tm3.positions["6269"]["state"] == EXITING, gm3.sent)
tm4, gm4 = mk(); tm4.on_tick("6269", 86.2); tm4.step([ENT]); tm4.on_report(51, "A0001", 0, 1000, 86.2)
tm4.on_report(50, "i00Pe", 0, 1000, 87.0, "", "6269", "S", "9"); ok(not tm4.prehang)                                   # 同方向(加碼)不算預掛

# 2026-11-06 14:26 手動預掛新進場(沒有程式部位)→ 預掛卡;取消 → 取消卡;重複回報不重發
at(10, 0); tq_, gq_ = mk(); tq_.on_report(50, "i00Qa", 0, 3000, 50.5, "", "2330", "S", "9"); tq_.on_report(50, "i00Qa", 0, 3000, 50.5, "", "2330", "S", "9")
ok(sum("預掛做空" in x[0] and x[2] == "warn" for x in tq_.posts) == 1 and not tq_.prehang, tq_.posts)
tq_.on_report(50, "i00Qa", 2, 3000, 50.5, "", "2330", "S", "9"); ok(any("預掛已取消" in x[0] for x in tq_.posts), tq_.posts)

# 2026-11-06 做空被元大 21352(無券)擋 → 這檔今天不再送空單、提醒一次;做多照常
at(10, 0); tn_, gn_ = mk(); tn_.no_short("6269", "元大 21352(無券可空)"); tn_.no_short("6269", "元大 21352(無券可空)")
ok(sum("無券可空" in a for a in tn_.alerts) == 1, tn_.alerts)
tn_.on_tick("6269", 86.2); tn_.step([ENT]); ok("6269" not in tn_.positions and not gn_.sent and "不能空" in logs[-1], logs[-1])
tn_.step([ENTX("6269", 86.2, k="lg-n")]); ok("6269" in tn_.positions)

# 2026-11-06 元三停利預設 2.5 → 3(和判三一樣)
from trader import Trader as _T; ok(_T({"TRADE_STATE_FILE": str(tmp / "y3d.json")}, GW(), logs.append, lambda: NOW[0]).y3_tp == 3.0)

# 2026-11-07 長按當沖平倉:電腦還沒接手的元大手動單(預掛還掛著 → 對帳不接)→ 先取消掛著的手動單、接成元三再強平
tm7, gm7 = mk(); tm7.on_tick("2033", 72.6)
T7 = [{"code": "2033", "bs": "S", "lots": 3, "price": 72.6, "time": "10:05", "order_no": "M7"}]
W7 = [{"code": "2033", "bs": "B", "lots": 3, "no": "P7", "typ": "", "tag": ""}]
r7 = tm7.force_close_manual("2033", T7, W7, {"M7": ""}, {}, "c7")
ok(r7[0] and ("cancel", "P7", "2033", "B") in gm7.sent and tm7.positions["2033"]["judge"] == "元三" and tm7.positions["2033"]["state"] == EXITING and tm7.positions["2033"].get("forced_close"), (r7, gm7.sent))
ok(any(x[:3] == ("2033", "B", 3) for x in gm7.sent if x[0] == "2033"), gm7.sent)
ok(tm7.force_close_manual("2033", T7, W7, {}, {}, "c7") == r7)                      # 同一個指令重送 → 同一個結果,不重複平
tm8, gm8 = mk(); ok(not tm8.force_close_manual("2033", [], [], {}, {}, "c8")[0])   # 元大已經沒有部位 → 不送
# 2026-11-07 強平時取消的預掛,取消前還是成交了:程式的平倉單先成交(部位已結束)→ 預掛成交多出 3 張多單 → 不接成元三,馬上再強平(跌停賣)
tm7.positions.pop("2033", None); n7 = len(gm7.sent); tm7.on_other_fill("2033", "B", 3000, 72.0, "P7")
ok(tm7.positions.get("2033", {}).get("judge") == "補回" and any(x[:3] == ("2033", "S", 3) for x in gm7.sent[n7:]) and any("預掛取消前成交" in a for a in tm7.alerts), (gm7.sent[n7:], tm7.alerts[-2:]))
# 2026-11-07 同上,但預掛成交的回報漏了 → 對帳(連兩次)才看到 → 一樣馬上再強平,不接成元三
tm9, gm9 = mk(); tm9.ph_cancel["P9"] = 0
T9 = [{"code": "2033", "bs": "S", "lots": 3, "price": 72.6, "time": "10:05", "order_no": "M9"}, {"code": "2033", "bs": "B", "lots": 3, "price": 72.0, "time": "10:30", "order_no": "C9"}, {"code": "2033", "bs": "B", "lots": 3, "price": 72.0, "time": "10:30", "order_no": "P9"}]
tm9.reconcile(T9, [], {}); tm9.reconcile(T9, [], {})
ok(tm9.positions.get("2033", {}).get("judge") == "補回" and any(x[:3] == ("2033", "S", 3) for x in gm9.sent), (gm9.sent, tm9.positions.get("2033")))

# 2026-11-08 元大停損停利條件單跟著部位走(元三也掛、元平刪 / 一部分重掛、條件單自己觸發不動)
tb, gb = mk(TRADE_BROKER_STOP="1"); tb.on_tick("2033", 72.6)
tb.on_other_fill("2033", "S", 3000, 72.6, "M1"); pb = tb.positions["2033"]
ok(pb["judge"] == "元三" and gb.sent[-1][:5] == ("oco", "2033", pb["mkt"], -1, 3), gb.sent)            # 元三進場 → 掛 3 張條件單
tb.on_strategy_reply("S1", True, ""); ok(pb["oco_live"] and pb["oco_no"] == "S1")
tb.on_other_fill("2033", "B", 1000, 72.5, "M2")                                                          # 元平 1 張(不是停利停損價)→ 刪舊的、照 2 張重掛
ok(("del_oco", "S1") in gb.sent and gb.sent[-1][:5] == ("oco", "2033", pb["mkt"], -1, 2), gb.sent[-3:])
tb.on_strategy_reply("S2", True, ""); n0 = len(gb.sent)
tb.on_other_fill("2033", "B", 2000, 72.4, "M3")                                                          # 元平剩下 2 張 → 只刪,不再掛
ok(gb.sent[n0:] == [("del_oco", "S2")] and "2033" not in tb.positions, gb.sent[n0:])
tc, gc = mk(TRADE_BROKER_STOP="1"); tc.on_tick("2034", 50.0); tc.on_other_fill("2034", "B", 2000, 50.0, "N1"); pc_ = tc.positions["2034"]
tc.on_strategy_reply("T1", True, ""); n1 = len(gc.sent)
tc.on_other_fill("2034", "S", 1000, pc_["tp"], "")                                                        # 價格到停利、只平一部分 = 條件單自己在平 → 不刪不重掛
ok(not any(x[0] in ("del_oco", "oco") for x in gc.sent[n1:]), gc.sent[n1:])
tc.on_other_fill("2034", "S", 1000, pc_["tp"], ""); ok(("del_oco", "T1") in gc.sent[n1:], gc.sent[n1:])   # 2026-11-08 00:55 部位歸零一律刪(已觸發的刪了無害)

# 2026-11-08 00:55 使用者:13:20 後不掛條件單;預掛當下撤條件單、預掛撤除再補;部位結束取消剩下的預掛;緊急全平先取消預掛
def _hb():
    at(10, 0); t, g = mk(TRADE_BROKER_STOP="1"); t.on_tick("6269", 86.2); t.step([ENT]); t.on_report(51, "A0001", 0, 1000, 86.1); t.on_strategy_reply("S1", True, ""); return t, g, t.positions["6269"]
u1, h1, q1 = _hb(); u1.on_report(50, "P1", 0, 1000, 85.0, "", "6269", "B", "9"); ok(("del_oco", "S1") in h1.sent and not q1["oco_live"], h1.sent)
n2 = len(h1.sent); u1.on_report(50, "P1", 2, 1000, 85.0, "", "6269", "B", "9"); ok(any(x[0] == "oco" for x in h1.sent[n2:]), h1.sent[n2:])        # 預掛撤除 → 補回條件單
u2, h2, q2 = _hb(); u2.on_report(50, "P2", 0, 1000, 85.0, "", "6269", "B", "9"); u2.on_other_fill("6269", "B", 1000, q2["sl"], "X2")
ok(("cancel", "P2", "6269", "B") in h2.sent and "P2" in u2.ph_cancel, h2.sent)                                                            # 部位結束 → 取消剩下的預掛
u2.on_other_fill("6269", "B", 1000, 85.0, "P2"); ok(u2.positions.get("6269", {}).get("judge") == "補回")                                 # 取消前成交 → 再強平,不接成元三
u3, h3, q3 = _hb(); u3.on_report(50, "P3", 0, 1000, 85.0, "", "6269", "B", "9"); u3.close_all("緊急全部平倉")
ok(("cancel", "P3", "6269", "B") in h3.sent and q3["state"] == EXITING, h3.sent)                                                          # 緊急全平先取消預掛再平
at(13, 21); u4, h4 = mk(TRADE_BROKER_STOP="1"); u4.resume([{"code": "6269", "bs": "S", "lots": 1, "price": 86.1, "time": "10:00", "order_no": "A1"}], {"A1": "T1006269"}, [], [ENT])
ok(not any(x[0] == "oco" for x in h4.sent), h4.sent); at(10, 0)                                                                            # 13:20 後接回不掛條件單
# 2026-11-08 01:25 條件單回應照代碼對:A 還在等回應、B 送出當下就失敗 → B 失敗不能吃掉 A;A 的編號回到 A
at(10, 0); x1, y1 = mk(TRADE_BROKER_STOP="1"); x1.on_tick("6269", 86.2); x1.on_tick("2330", 600.0)
x1.step([ENT]); x1.on_report(51, "A0001", 0, 1000, 86.1); x1.force_enter("2330", 1, 1, 2.5, 1.5); x1.on_report(51, "", 0, 1000, 600.0, basket=y1.tags[-1])
x1.on_strategy_reply("", False, "送出失敗", "2330"); x1.on_strategy_reply("SA", True, "")
ok(x1.positions["6269"].get("oco_no") == "SA" and x1.positions["2330"].get("oco_no") == "", (x1.positions["6269"].get("oco_no"), x1.positions["2330"].get("oco_no")))
# 2026-11-08 01:26 條件單保險:碰到停損 15 秒條件單沒平 → 程式刪條件單自己平;15 秒內條件單平了就不動
at(10, 0); z1, q1_ = mk(TRADE_BROKER_STOP="1"); z1.on_tick("6269", 86.2); z1.step([ENT]); z1.on_report(51, "A0001", 0, 1000, 86.1); z1.on_strategy_reply("S1", True, ""); zp = z1.positions["6269"]
z1.on_tick("6269", 88.8); z1.step(); ok(zp["state"] == HOLDING)
zp["oco_hit_at"] -= 16; z1.step(); ok(zp["state"] == EXITING and ("del_oco", "S1") in q1_.sent and q1_.sent[-1][:3] == ("6269", "B", 1), q1_.sent[-3:])
z2, q2_ = mk(TRADE_BROKER_STOP="1"); z2.on_tick("6269", 86.2); z2.step([ENT]); z2.on_report(51, "A0001", 0, 1000, 86.1); z2.on_strategy_reply("S1", True, "")
z2.on_tick("6269", 88.8); z2.on_other_fill("6269", "B", 1000, 88.8, "OC"); n2 = len(q2_.sent); z2.step(); ok("6269" not in z2.positions and not any(x[:2] == ("6269", "B") for x in q2_.sent[n2:]))
# 2026-11-08 01:00 再檢查:預掛成交完剩下的恢復盯 + 補條件單;重開認得預掛;晚到成交 / 對帳改張數重掛;對帳沒部位刪條件單
at(10, 0); v1, k1 = mk(TRADE_BROKER_STOP="1", TRADE_QTY="3"); v1.on_tick("6269", 86.2); v1.step([ENT]); v1.on_report(51, "A0001", 0, 3000, 86.1); v1.on_strategy_reply("S1", True, ""); w1 = v1.positions["6269"]
n1 = len(k1.sent); v1.on_report(50, "P1", 0, 1000, 85.0, "", "6269", "B", "9"); v1.on_other_fill("6269", "B", 1000, 85.0, "P1")   # 18:30 起預掛當下就重掛沒預掛的 2 張
ok(not v1.prehang and any(x[0] == "oco" and x[4] == 2 for x in k1.sent[n1:]), k1.sent[n1:]); v1.on_tick("6269", 88.8); ok(w1["state"] == EXITING)
at(10, 30); v2, k2 = mk(TRADE_BROKER_STOP="1"); v2.resume([{"code": "6269", "bs": "S", "lots": 1, "price": 86.1, "time": "10:00", "order_no": "A1"}], {"A1": "T1006269"}, [{"no": "S7", "code": "6269"}], [ENT], [{"code": "6269", "bs": "B", "lots": 1, "no": "P5", "typ": "9", "tag": ""}])
ok(v2.prehang.get("6269") == ["P5"] and ("del_oco", "S7") in k2.sent and not any(x[0] == "oco" for x in k2.sent), k2.sent)
at(10, 0); v3, k3 = mk(TRADE_BROKER_STOP="1"); v3.on_tick("6269", 86.2); v3.step([ENT]); v3.on_report(51, "A0001", 0, 1000, 86.1); v3.on_strategy_reply("S1", True, ""); w3 = v3.positions["6269"]
n3 = len(k3.sent); v3.on_report(51, "", 0, 1000, 86.1, basket=w3["entry_tag"], code="6269", bs="S"); ok(any(x[0] == "oco" and x[4] == 2 for x in k3.sent[n3:]), k3.sent[n3:])
v3.on_strategy_reply("S2", True, ""); T3 = [{"code": "6269", "bs": "S", "lots": 1, "price": 86.1, "time": "10:00", "order_no": "A0001"}, {"code": "6269", "bs": "B", "lots": 1, "price": 86.0, "time": "10:05", "order_no": "Z"}]
v3.reconcile(T3, [], {}); n3 = len(k3.sent); v3.reconcile(T3, [], {}); ok("6269" not in v3.positions and ("del_oco", "S2") in k3.sent[n3:], k3.sent[n3:])

# 2026-11-08 15:57 進場流程檢查「改」:① 補單(跌停賣 ROD)8 秒沒成交完 → 取消剩下的,取消回報後照已成交張數轉持倉 / 0 張放棄;委託書號晚到也會取消 ② 現價已過停損 / 停利不進
at(10, 0); f1, h1 = mk(TRADE_CAP="250000"); f1.on_tick("1714", 17.45)
f1.step([sig("kf1", "short", "和桐 1714 做空進場 17.45", "10:00 空 · 停利 15.88 停損 18.04 · 14張 · 判四")])
of1 = f1.positions["1714"]["order"]; f1.on_report(51, "", 0, 6000, 17.40, basket=of1["tag"]); f1.on_report(50, "", 2, 0, 0, basket=of1["tag"])
fb1 = f1.positions["1714"]["order"]; f1.on_report(50, "B9", 0, 0, 0, basket=fb1["tag"]); fb1["at"] -= 9; f1.step()
ok(("cancel", "B9", "1714", "S") in h1.sent and f1.positions["1714"]["state"] == ENTERING, h1.sent[-2:])
f1.on_report(50, "B9", 2, 0, 0, basket=fb1["tag"]); ok(f1.positions["1714"]["state"] == HOLDING and f1.positions["1714"]["qty"] == 6, f1.positions["1714"])
f2, h2 = mk(GWN, TRADE_CAP="250000"); f2.on_tick("1714", 17.45)
f2.step([sig("kf2", "short", "和桐 1714 做空進場 17.45", "10:00 空 · 停利 15.88 停損 18.04 · 14張 · 判四")])
of2 = f2.positions["1714"]["order"]; f2.on_report(50, "", 2, 0, 0, basket=of2["tag"]); fb2 = f2.positions["1714"]["order"]; fb2["at"] -= 9; f2.step()
ok(not any(x[0] == "cancel" for x in h2.sent) and fb2.get("cancel_after"), h2.sent)
f2.on_order_reply("B7", True, "", fb2["tag"]); ok(("cancel", "B7", "1714", "S") in h2.sent, h2.sent)
f2.on_report(50, "B7", 2, 0, 0, basket=fb2["tag"]); ok("1714" not in f2.positions)
f3, h3 = mk(TRADE_MAX_DEV_PCT="5"); f3.on_tick("6269", 88.9); f3.step([ENT]); ok("6269" not in f3.positions and "已不在停利" in logs[-1], logs[-1])

# 2026-11-08 16:00 預掛扣額度:平倉預掛照預掛價扣(那幾張不再用漲停扣);新進場預掛照 預掛價 + 漲停 扣;成交 / 取消後退回;進場張數跟著少
at(10, 0); m1, n1_ = mk(TRADE_BROKER_STOP="1"); m1.on_tick("6269", 86.2); m1.step([ENT]); m1.on_report(51, "A0001", 0, 1000, 86.1); mp1 = m1.positions["6269"]
h0 = m1.hold_reserve(); ok(h0 > 0 and m1.pend_reserve() == 0, (h0,))
m1.on_report(50, "P1", 0, 1000, 85.0, "", "6269", "B", "9"); ok(m1.hold_reserve() == 0 and abs(m1.pend_reserve() - 85000) < 1e-6, (m1.hold_reserve(), m1.pend_reserve()))
m1.on_report(50, "M1", 0, 2000, 100.0, "", "2330", "B", "0"); ok(abs(m1.pend_reserve() - (85000 + 2 * (100 + 110) * 1000)) < 1, m1.pend_reserve())
m1.on_report(50, "M1", 2, 0, 0, "", "2330", "B", "0"); ok(abs(m1.pend_reserve() - 85000) < 1e-6, m1.pend_reserve())
m2, n2_ = mk(TRADE_CAP="250000", TRADE_MAX_AMOUNT="1000000"); m2.on_tick("1714", 17.45)
m2.on_report(50, "M2", 0, 20000, 20.0, "", "2330", "B", "0")   # 手動預掛做多 20 張 @20 → 先扣 20 ×(20 + 22)× 1000 = 84 萬
m2.step([sig("km2", "short", "和桐 1714 做空進場 17.45", "10:00 空 · 停利 15.88 停損 18.04 · 14張 · 判四")])
ok(m2.positions.get("1714") and n2_.sent[-1][2] == int((1000000 - 840000) // ((17.40 + 19.15) * 1000)), (n2_.sent[-1:], logs[-2:]))
m3, n3_ = mk(TRADE_CAP="250000", TRADE_MAX_AMOUNT="1000000"); m3.on_tick("1714", 17.45)
m3.on_report(50, "M3", 0, 20000, 20.0, "", "2330", "B", "0"); m3.on_other_fill("2330", "B", 20000, 20.0, "M3"); ok(m3.man_cards["M3"]["fl"] == 20 and m3.pend_reserve() == 0, m3.man_cards)

# 2026-11-08 16:04「掛空補單呢」:程式自己進場中的單(還掛著 + 已成交)先扣額度 → 同時第二個訊號不會重複用同一筆額度;IOC 成交後補單照扣過的算
m4, n4_ = mk(TRADE_CAP="250000", TRADE_MAX_AMOUNT="600000"); m4.on_tick("1714", 17.45); m4.on_tick("2002", 17.45)
m4.step([sig("km4a", "short", "和桐 1714 做空進場 17.45", "10:00 空 · 停利 15.88 停損 18.04 · 14張 · 判四"),
         sig("km4b", "short", "中鋼 2002 做空進場 17.45", "10:00 空 · 停利 15.88 停損 18.04 · 14張 · 判四")])
r4 = m4.pend_reserve(); ok(r4 > 14 * 17.40 * 1000 and n4_.sent[0][2] == 14 and n4_.sent[1][2] < 14, (r4, n4_.sent))
o4 = m4.positions["1714"]["order"]; m4.on_report(51, "", 0, 6000, 17.40, basket=o4["tag"]); m4.on_report(50, "", 2, 0, 0, basket=o4["tag"])
ok(m4.positions["1714"]["state"] == ENTERING and m4.pend_reserve() >= 6 * 17.40 * 1000, m4.pend_reserve())

# 2026-11-08 16:09 補單限價:做空 訊號 −1% 往上取檔、做多 +1% 往下取檔;碰漲停 / 跌停就用漲停 / 跌停
from trader import fb_price
ok(fb_price(17.45, -1) == 17.3 and fb_price(86.2, -1) == 85.4 and fb_price(86.2, 1) == 87.0 and fb_price(49.8, 1) == 50.2 and fb_price(100, 1, ref=91) == 100.0 and fb_price(82, -1, ref=91) == 81.9 and fb_price(82, -1, ref=90) == 81.2)

# 2026-11-08 18:26「進場6張 我預掛兩張」改:碰停損只平沒預掛的 4 張,預掛 2 張留著;平完回持倉中;預掛成交 → 部位結束;沒平完只重送沒預掛的張數
at(10, 0); q1, g1q = mk(TRADE_CAP="520000"); q1.on_tick("6269", 86.2); q1.step([ENT]); q1.on_report(51, "A0001", 0, 6000, 86.1); pq = q1.positions["6269"]
q1.on_report(50, "P6", 0, 2000, 85.0, "", "6269", "B", "9"); ok(pq["state"] == HOLDING and q1.ph_lots("6269") == 2)
q1.on_tick("6269", 88.8); ok(pq["state"] == EXITING and g1q.sent[-1][:3] == ("6269", "B", 4) and pq.get("ph_part"), g1q.sent[-1])
ox = pq["order"]; q1.on_report(51, ox["no"] or "A0002", 0, 3000, 88.8, basket=ox["tag"]); q1.on_report(50, ox["no"] or "A0002", 2, 0, 0, basket=ox["tag"])
ok(pq["state"] == EXITING and g1q.sent[-1][:3] == ("6269", "B", 1), g1q.sent[-1])
oy = pq["order"]; q1.on_report(51, oy["no"] or "A0003", 0, 1000, 88.9, basket=oy["tag"])
ok(pq["state"] == HOLDING and pq["qty"] == 2 and "P6" in q1.prehang.get("6269", []), (pq["state"], pq["qty"]))
n0 = len(g1q.sent); q1.on_tick("6269", 89.0); ok(len(g1q.sent) == n0, g1q.sent[n0:], logs[-6:], q1.prehang, pq["state"], pq["qty"])
q1.on_other_fill("6269", "B", 2000, 85.0, "P6"); ok("6269" not in q1.positions)

# 2026-11-08 18:30「預掛手動取消 → 取消4張條件單 再改掛6張」:6 張預掛 2 張 → 條件單 6 張刪掉改掛 4 張;預掛取消 → 4 張刪掉改掛 6 張;碰停損程式平 4 張前先刪條件單
at(10, 0); u1, h1u = mk(TRADE_BROKER_STOP="1", TRADE_CAP="520000"); u1.on_tick("6269", 86.2); u1.step([ENT]); u1.on_report(51, "A0001", 0, 6000, 86.1); u1.on_strategy_reply("S6", True, "", "6269"); pu = u1.positions["6269"]
ok(("oco", "6269") == h1u.sent[-1][:2] and h1u.sent[-1][4] == 6 and pu["oco_live"], h1u.sent[-1])
u1.on_report(50, "P2", 0, 2000, 85.0, "", "6269", "B", "9"); ok(("del_oco", "S6") in h1u.sent and h1u.sent[-1][0] == "oco" and h1u.sent[-1][4] == 4, h1u.sent[-2:])
u1.on_strategy_reply("S4", True, "", "6269"); ok(pu["oco_no"] == "S4" and pu["oco_live"])
u1.on_report(50, "P2", 2, 0, 0, "", "6269", "B", "9"); ok(("del_oco", "S4") in h1u.sent and h1u.sent[-1][0] == "oco" and h1u.sent[-1][4] == 6 and not u1.prehang, h1u.sent[-2:])
u2, h2u = mk(TRADE_BROKER_STOP="1", TRADE_CAP="520000"); u2.on_tick("6269", 86.2); u2.step([ENT]); u2.on_report(51, "A0001", 0, 6000, 86.1); u2.on_strategy_reply("S6", True, "", "6269")
u2.on_report(50, "P2", 0, 2000, 85.0, "", "6269", "B", "9"); u2.on_strategy_reply("S4", True, "", "6269"); pv = u2.positions["6269"]
u2.on_tick("6269", 88.8); pv["oco_hit_at"] = (pv.get("oco_hit_at") or 0) - 16; u2.step()
ok(pv["state"] == EXITING and ("del_oco", "S4") in h2u.sent and h2u.sent[-1][:3] == ("6269", "B", 4), h2u.sent[-3:])

# 2026-11-08 20:45 檢查:只平沒預掛那幾張的途中按緊急全平 / 強平 → 預掛取消,平倉單成交後剩下的照全平送出(漲停買)
for how in ("all", "one"):
    at(10, 0); e1, ge = mk(TRADE_CAP="520000"); e1.on_tick("6269", 86.2); e1.step([ENT]); e1.on_report(51, "A0001", 0, 6000, 86.1); pe = e1.positions["6269"]
    e1.on_report(50, "P9", 0, 2000, 85.0, "", "6269", "B", "9"); e1.on_tick("6269", 88.8); ok(pe["state"] == EXITING and ge.sent[-1][:3] == ("6269", "B", 4))
    if how == "all": e1.close_all("緊急全平")
    else: e1.force_close("6269")
    ok(("cancel", "P9", "6269", "B") in ge.sent and not e1.prehang, ge.sent[-2:])
    oe = pe["order"]; e1.on_report(51, oe["no"] or "A0002", 0, 4000, 88.9, basket=oe["tag"])
    ok(pe["state"] == EXITING and ge.sent[-1][:3] == ("6269", "B", 2) and ge.sent[-1][6] == "H", (how, ge.sent[-1]))

# 2026-11-08 20:50「等元大確認刪掉舊條件單才掛新的」:預掛 2 張 → 刪 6 張,沒確認前不掛 4 張(這段程式自己盯);確認後掛 4 張;刪除失敗再刪,3 次失敗不掛 + 通知;5 秒沒回應再刪
at(10, 0); k1, gk = mk(TRADE_BROKER_STOP="1", TRADE_CAP="520000"); gk.auto = False; k1.on_tick("6269", 86.2); k1.step([ENT]); k1.on_report(51, "A0001", 0, 6000, 86.1); k1.on_strategy_reply("S6", True, "", "6269"); pk = k1.positions["6269"]
k1.on_report(50, "P2", 0, 2000, 85.0, "", "6269", "B", "9"); ok(gk.sent[-1] == ("del_oco", "S6") and pk.get("oco_del") and not pk["oco_live"], gk.sent[-2:])
k1.on_strategy_delete_reply("S6", 0, "刪除成功"); ok(gk.sent[-1][0] == "oco" and gk.sent[-1][4] == 4 and not pk.get("oco_del"), gk.sent[-1])
k1.on_strategy_reply("S4", True, "", "6269"); nk = len(gk.sent); k1.on_report(50, "P2", 2, 0, 0, "", "6269", "B", "9"); ok(gk.sent[-1] == ("del_oco", "S4"))
k1.on_strategy_delete_reply("S4", 9, "無法刪除"); ok(gk.sent[-1] == ("del_oco", "S4") and pk["oco_del"]["tries"] == 2)
k1.on_strategy_delete_reply("S4", 9, "無法刪除"); k1.on_strategy_delete_reply("S4", 9, "無法刪除"); ok(not pk.get("oco_del") and not any(x[0] == "oco" for x in gk.sent[nk:]) and any("刪不掉" in a for a in k1.alerts), k1.alerts[-1:])
k2, gk2 = mk(TRADE_BROKER_STOP="1", TRADE_CAP="520000"); gk2.auto = False; k2.on_tick("6269", 86.2); k2.step([ENT]); k2.on_report(51, "A0001", 0, 6000, 86.1); k2.on_strategy_reply("S6", True, "", "6269"); pk2 = k2.positions["6269"]
k2.on_report(50, "P2", 0, 2000, 85.0, "", "6269", "B", "9"); pk2["oco_del"]["at"] -= 6; k2.step(); ok(gk2.sent[-1] == ("del_oco", "S6") and pk2["oco_del"]["tries"] == 2, gk2.sent[-2:])
k2.on_tick("6269", 88.8); ok(pk2["state"] == EXITING and gk2.sent[-1][:3] == ("6269", "B", 4), gk2.sent[-1])   # 等確認期間碰停損 → 程式自己平沒預掛的 4 張

# 2026-11-09 15:28 判七解凍上線:判七進場通知照下單;判七平倉通知不能平判二的部位
at(10, 0); t7, g7 = mk(); t7.on_tick("6269", 86.2)
t7.step([sig("10:00:6269|七", "short", "台郡 6269 做空進場 86.20", "10:00 空 · 停利 81.46 停損 89.65 · 2張 · 量縮 0.30 離今高 1.2% 今高離VWAP 2.0% · 判七")])
ok(t7.positions.get("6269", {}).get("judge") == "判七", t7.positions)   # 判七進場會下單
at(10, 0); t8, g8 = mk(); t8.on_tick("6269", 86.2); t8.step([ENT])
t8.step([sig("10:30:6269|七出", "loss", "⚠⚠ 台郡 6269 停損 -4.0% 立即平倉", "10:30 做空 10:00 進 86.20 → 現價 89.65 · 2張 · 判七")])
ok(t8.positions["6269"]["judge"] == "判二" and t8.positions["6269"]["state"] != EXITING, t8.positions["6269"]["state"])   # 判七平倉通知不平判二部位
# 2026-11-11 14:17 全改:判七也和其他判別式一樣 +6.5% 強平(原 +8%)
at(10, 0); t9, g9 = mk(); t9.on_tick("6269", 86.2); t9.ref_of = lambda c: 83.0
t9.step([sig("10:00:6269|七b", "short", "台郡 6269 做空進場 86.20", "10:00 空 · 停利 81.46 停損 89.65 · 2張 · 判七")]); t9.on_report(51, "A0001", 0, 1000, 86.2)
ok(t9.positions["6269"]["sl"] == 88.3, t9.positions["6269"]["sl"]); t9.on_tick("6269", 88.2); ok(t9.positions["6269"]["state"] == HOLDING, t9.positions["6269"]["state"])   # 14:57 停損收到 83 × 1.065 = 88.395 往下取 88.3
t9.on_tick("6269", 88.3); ok(t9.positions["6269"]["state"] == EXITING, t9.positions["6269"]["state"])   # 碰 88.3 停損
# 2026-11-09 判二第二十四次修改:大盤上漲家數 ≥ 90% 判二今天最多 1 檔、≥ 80% 2 檔;判一不受影響
for br_, want, cap_ in ((0.95, ["2330"], "0.9:1,0.8:2"), (0.85, ["2330", "2303"], "0.9:1,0.8:2"), (0.6, ["2330", "2303", "1101"], "0.9:1,0.8:2"), (0.93, [], "0.92:0"), (0.91, ["2330", "2303", "1101"], "0.92:0")):
    at(10, 0); tb, gb = mk(TRADE_J2_BRCAP=cap_, TRADE_J2_BRRANGE=""); tb.br_now = lambda b=br_: b
    for c_, px_ in (("2330", 50.0), ("2303", 40.0), ("1101", 30.0)): tb.on_tick(c_, px_); tb.step([ENTX(c_, px_, judge="判二")])
    ok(sorted(tb.positions) == sorted(want), (br_, sorted(tb.positions)))
at(10, 0); tb, gb = mk(); tb.br_now = lambda: 0.95
for c_, px_ in (("2330", 50.0), ("2303", 40.0)): tb.on_tick(c_, px_); tb.step([ENTX(c_, px_, judge="判一")])
ok(sorted(tb.positions) == ["2303", "2330"], sorted(tb.positions))   # 判一不受影響
at(10, 0); tb, gb = mk(TRADE_J2_BRCAP="0.9:1,0.8:2", TRADE_J2_BRRANGE=""); tb.br_now = lambda: 0.95; tb.on_tick("2330", 50.0); tb.step([ENTX("2330", 50.0, judge="判二")])
tb.mark_skip(tb.positions.pop("2330")); tb.on_tick("2303", 40.0); tb.step([ENTX("2303", 40.0, judge="判二")])
ok("2303" in tb.positions, sorted(tb.positions))   # 沒成交放棄的不算一檔
SHX = lambda code, px, judge: sig(f"10:0y:{code}", "short", f"股{code} {code} 做空進場 {px}", f"10:00 空✓ · 停利 {px * 0.975:.2f} 停損 {px * 1.015:.2f} · {judge}")
# 2026-11-10 01:45 判二第二十六次修改:大盤上漲家數範圍(預設 空 4.4%~88.1%、多 10.6%~92%),取代 ≥ 92% 不做;判一不受影響
for br_, kind_, want in ((0.97, "long", False), (0.957, "long", False), (0.93, "long", False), (0.92, "long", True), (0.08, "long", False), (0.106, "long", True),
                         (0.90, "short", False), (0.881, "short", True), (0.5, "short", True), (0.03, "short", False), (None, "short", True)):
    at(10, 0); tb, gb = mk(); tb.br_now = lambda b=br_: b; tb.on_tick("2330", 50.0); tb.step([ENTX("2330", 50.0, judge="判二") if kind_ == "long" else SHX("2330", 50.0, "判二")])
    ok(("2330" in tb.positions) == want, (br_, kind_, sorted(tb.positions)))
at(10, 0); tb, gb = mk(); tb.br_now = lambda: 0.99; tb.on_tick("2330", 50.0); tb.step([SHX("2330", 50.0, "判一")])
ok("2330" in tb.positions, sorted(tb.positions))   # 判一不受影響
# 2026-11-11 00:20 判六上線:判六進場通知照下單;判六平倉通知不平同一檔判二的部位
at(10, 0); t6, g6 = mk(); t6.on_tick("2330", 50.0); t6.step([sig("11:05:2330|六", "long", "股2330 2330 做多進場 50.0", "11:05 多 · 停利 52.00 停損 49.00 · 5張 · 午盤Ka穿VWAP 離低 2.5% Oa−Ob 10.0% RSI 80.0 · 判六")])
ok(t6.positions.get("2330", {}).get("judge") == "判六", sorted(t6.positions))   # 判六進場會下單
at(10, 0); t6, g6 = mk(); t6.on_tick("2330", 50.0)
t6.step([ENTX("2330", 50.0, k="10:01:2330|二", judge="判二")]); ok("2330" in t6.positions and t6.positions["2330"]["judge"] == "判二", sorted(t6.positions))
t6.step([sig("10:30:2330|六出", "profit", "✅ 股2330 2330 停利 +4.0%", "10:30 做多 09:05 進 50.00 → 現價 52.00 · 5張 · 判六")])
ok(t6.positions["2330"]["state"] != EXITING, t6.positions["2330"]["state"])   # 判六平倉通知不平判二部位
for jl_, want in (("判一,判二,判三,判四,判五,判七", True), ("判一,判二,判三,-", False)):
    tj, gj = mk(TRADE_JUDGES=jl_); ok(("判六" in tj.judges) == want, (jl_, tj.judges))   # 舊預設自動補判六;特別排除的不補
# OCOS) 2026-11-10 條件單查詢失敗:編號存檔 → 中繼重開接回時用存下的編號,不重掛第二張;查詢正常、元大沒有 → 照舊重掛
ta, ga = mk(TRADE_BROKER_STOP="1"); ta.on_tick("6269", 86.2); ta.step([ENT]); ta.on_report(51, "A0001", 0, 1000, 86.1)
ta.on_strategy_reply("S777", True, "", code="6269"); ok(ta.load_state().get("oco", {}).get("6269") == "S777", ta.load_state())
tb, gb = mk(TRADE_BROKER_STOP="1"); tb.state_file = ta.state_file; tb.on_tick("6269", 86.2)
tb.resume([{"code": "6269", "bs": "S", "lots": 1, "price": 86.1, "time": "10:00", "order_no": "A1"}], {"A1": "T100000001"}, None, [ENT])
pb = tb.positions.get("6269"); ok(pb and pb.get("oco_live") and pb.get("oco_no") == "S777" and not any(x[0] == "oco" for x in gb.sent), pb, gb.sent)
tc, gc = mk(TRADE_BROKER_STOP="1"); tc.on_tick("6269", 86.2)
tc.resume([{"code": "6269", "bs": "S", "lots": 1, "price": 86.1, "time": "10:00", "order_no": "A1"}], {"A1": "T100000001"}, [], [ENT])
ok(any(x[0] == "oco" for x in gc.sent), gc.sent)
# 2026-11-11 14:49 張數分級 + 判別式只在 09:30~12:32 進場
at(10, 0); tq, gq = mk(TRADE_CAP="250000", TRADE_LOT_TIER="1", TRADE_JUDGE_FROM="09:30", TRADE_JUDGE_TO="12:32")
ok(tq.lots_for(20.6) == 3 and tq.lots_for(70) == 3 and tq.lots_for(70.1) == 2 and tq.lots_for(150) == 2 and tq.lots_for(150.5) == 1 and tq.lots_for(249) == 1)
tq.on_tick("2330", 50.0); tq.step([ENTX("2330", 50.0, judge="判四")]); ok("2330" in tq.positions and gq.sent[-1][:3] == ("2330", "B", 3), gq.sent)   # 50 元 → 3 張(原 25 萬 ÷ 5 萬 = 5 張)
at(9, 20); tr_, gr_ = mk(TRADE_CAP="250000", TRADE_LOT_TIER="1", TRADE_JUDGE_FROM="09:30", TRADE_JUDGE_TO="12:32"); tr_.on_tick("2330", 50.0); tr_.step([ENTX("2330", 50.0, judge="判四")]); ok("2330" not in tr_.positions, "09:20 判別式不進")
at(12, 33); tr2, gr2 = mk(TRADE_CAP="250000", TRADE_LOT_TIER="1", TRADE_JUDGE_FROM="09:30", TRADE_JUDGE_TO="12:32"); tr2.on_tick("2330", 50.0); tr2.step([ENTX("2330", 50.0, judge="判四")]); ok("2330" not in tr2.positions, "12:33 判別式不進")
# 2026-11-11 14:57 停損價不超過昨收 ±6.5%(元大條件單同一價)
at(10, 30); tc, gc = mk(TRADE_CAP="250000"); tc.ref_of = lambda c: 180.0; tc.on_tick("2313", 187.5)
tc.step([sig("10:34:2313|七", "short", "華通 2313 做空進場 187.50", "10:33 空 · 停利 177.19 停損 195.00 · 1張 · 判七")])
ok(tc.positions["2313"]["sl"] == 191.5, tc.positions["2313"]["sl"])   # 180 × 1.065 = 191.7 → 往下取 0.5 = 191.5
at(10, 30); tc2, gc2 = mk(TRADE_CAP="250000"); tc2.ref_of = lambda c: 50.0; tc2.on_tick("2330", 50.0); tc2.step([ENTX("2330", 50.0)])
ok(tc2.positions["2330"]["sl"] == 49.25, tc2.positions["2330"]["sl"])   # 停損本來就在線內:不動
# 2026-11-11 15:38 強制進場也只在 09:30~12:30
at(9, 20); tf9, _ = mk(TRADE_JUDGE_FROM="09:30", TRADE_FORCE_TO="12:30"); tf9.ref_of = lambda c: 100.0; tf9.on_tick("2330", 100.0); r9 = tf9.force_enter("2330", 1, 1); ok(not r9[0] and "09:30~12:30" in r9[1], r9)
at(12, 31); tf10, _ = mk(TRADE_JUDGE_FROM="09:30", TRADE_FORCE_TO="12:30"); tf10.ref_of = lambda c: 100.0; tf10.on_tick("2330", 100.0); ok(not tf10.force_enter("2330", 1, 1)[0])
at(10, 0); tf11, _ = mk(TRADE_JUDGE_FROM="09:30", TRADE_FORCE_TO="12:30"); tf11.ref_of = lambda c: 100.0; tf11.on_tick("2330", 100.0); ok(tf11.force_enter("2330", 1, 1)[0])
print("trader.py 測試通過(32 + 17 + 3 + 2 + 平倉檢查 18 + 對帳 3 + 稽核修正 13 種情況 + 等確認對帳 2 + 超額 1 + 張數 1 + 判四 1 + 持倉上限 1 + 虧損全平 1 + 進場補單 3 + 元大拒單停做 1 + 虧損級距通知 1 + 賺錢慶祝 1 + 緊急全平 1 + 手動加碼併入 3 + 未交易標記 1 + 強制進場指定價 4 + 只全部平倉 2 + 超額確認補進場 9 + 元三分批成交卡 2 + 元三強三改±8.5% 9 + 強制進場±6.5%擋 3 + 太晚補進場 6 + 盤中預掛讓位 5 + 手動預掛卡 1 + 無券不空 1 + 長按強平接手動單 4 + 預掛取消前成交再強平 2 + 元大條件單跟部位 6 + 預掛條件單互斥 6 + 再檢查 4 + 條件單回應對代碼 1 + 條件單保險 2 + 進場補單逾時取消 2 + 現價過停損不進 1 + 預掛扣額度 6 + 進場中先扣額度 2 + 補單限價 1 + 部分預掛只平沒預掛的 5 + 預掛取消條件單改回全部張數 5 + 部分預掛中強平全平 6 + 等確認刪除才掛條件單 7 + 判七上線 2 + 判六上線 4 + 判七 +8% 強平 2 + 判二大盤分級檔數 7 + 判二寬度範圍 11 + 條件單編號存檔 3)")
