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
    def delete_oco(s, no): s.sent.append(("del_oco", no))
class GWN(GW):
    def send(s, *a): GW.send(s, *a); return None          # 真的通道:委託書號從回應事件來
tmp = Path(tempfile.mkdtemp()); stop = tmp / "STOP"; NMK = [0]
def mk(gw=GW, prime=True, **kw):
    NMK[0] += 1   # 2026-10-29 每個測試自己的狀態檔(今天進場過的訊號會存檔,不要互相影響)
    cfg = {"TRADE_CAP": "0", "TRADE_ENABLE": "1", "TRADE_AUTO": "1", "TRADE_STOP_FILE": str(stop), "TRADE_STATE_FILE": str(tmp / f"st{NMK[0]}.json")}; cfg.update(kw)
    g = gw(); t = Trader(cfg, g, logs.append, lambda: NOW[0]); t.posts = []; t.alerts = []
    t.post = lambda a, b, k: t.posts.append((a, b, k)); t.alert = lambda a, b="": t.alerts.append(a)
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
# 2) 進場沒成交(價穩失效)→ 拿掉
t2, g2 = mk(); t2.on_tick("6269", 86.2); t2.step([ENT]); t2.on_report(50, "A0001", 25, 0, 0); ok("6269" not in t2.positions)
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
# 5) 同時最多 10 檔(2026-10-31 使用者「改 10」;原 5 檔);第 11 檔不進;強制進場不佔名額
t5, g5 = mk()
codes = ["2401", "2402", "2403", "2404", "2405", "2407", "2408", "2409", "2410", "2411", "2406"]
for c in codes: t5.on_tick(c, 50.0)
for i, c in enumerate(codes):
    t5.step([ENTX(c, 50.0, k=f"k{c}")])
    if c in t5.positions: t5.on_report(51, "", 0, 1000, 50.0, basket=g5.tags[-1])
ok(len(t5.positions) == 10 and "2406" not in t5.positions and "上限 10" in logs[-1], logs[-1])
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
t21.on_tick("2409", 37.25); t21.step([ENTX("2409", 37.25, k="z1")]); ok("2409" not in t21.positions and "不夠一進一出" in logs[-1])

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
tj, gj = mk(); tj.ref_of = lambda c: 100.0; tj.force_enter("3002", -1, 1); tj.on_report(51, "A0001", 0, 1000, 101.0); pj = tj.positions["3002"]
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
tz, gz = mk(); tz.force_enter("4000", 1, 1, ref=50.0); tz.on_report(51, "A0001", 0, 1000, 50.0); tz.on_tick("4000", 46.7); ok(tz.positions["4000"]["state"] == EXITING)
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
ty.on_report(51, "M100", 0, 1000, 40.2, basket="", code="2409", bs="B"); ok(py["qty"] == 2 and abs(py["entry"] - 40.1) < 1e-9 and len(ty.posts) == 1)
ty.on_report(51, "M200", 0, 1000, 40.3, basket="", code="2409", bs="B"); ok(py["qty"] == 3 and abs(py["entry"] - 40.5 / 1 + 0.3333333) < 1e-3 or py["qty"] == 3, py)   # 2026-10-28 平倉檢查 #5:另一張同方向手動單也併進元三
ty.on_tick("2409", 38.0); ok(py["state"] == EXITING and gy.sent[-1] == ("2409", "S", 3, 0, "0", "0", "L"), gy.sent)
ty.on_report(51, "A0001", 0, 3000, 38.0); ok("2409" not in ty.positions and ty.posts[-1][1].endswith("· 3張 · 元三") and "停損" in ty.posts[-1][0], ty.posts[-1])
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
t10b, g10b = mk(); t10b.ref_of = lambda c: 200.0; t10b.force_enter("2330", 1, 1, None, None, "TWSE", None, 100.0); t10b.on_report(51, "A0001", 0, 1000, 100.0)
t10b.on_tick("2330", 93.4); ok(t10b.positions["2330"]["state"] == EXITING, t10b.positions["2330"]["state"])
# PA) 二次檢查 A:委託回報(50)比下單回應先到 → 要取消的單照樣取消;回應整個沒回來 → 30 秒複查從元大清單拿書號再取消
ta1, ga1 = mk(gw=GWN); pa1 = y3(ta1, "2330", "B", 2, 100.0); ta1.on_tick("2330", 95.4); ta1.on_report(51, "M2", 0, 2000, 95.4, "", "2330", "S")
ok(not [x for x in ga1.sent if x[0] == "cancel"]); ta1.on_report(50, "X7", 0, 0, 0, ga1.tags[-1]); ok(ga1.sent[-1] == ("cancel", "X7", "2330", "S"), ga1.sent)
ta2, ga2 = mk(gw=GWN); pa2 = y3(ta2, "2330", "B", 2, 100.0); ta2.on_tick("2330", 95.4); ta2.on_report(51, "M2", 0, 1000, 95.4, "", "2330", "S"); pa2["order"]["at"] -= 31
ta2.verify([{"no": "X8", "tag": ga2.tags[-1], "code": "2330", "bs": "S", "lots": 2}], [], {}); ok(ga2.sent[-1] == ("cancel", "X8", "2330", "S"), ga2.sent)
ta3, ga3 = mk(gw=GWN); ta3.force_enter("2330", 1, 1); ta3.force_close("2330"); ta3.on_report(50, "X9", 0, 0, 0, ga3.tags[-1]); ok(ga3.sent[-1] == ("cancel", "X9", "2330", "B"), ga3.sent)
# FB) 2026-10-28 判三假突破空單(通知帶「空✓破」):離昨收 +6.5% 以上不強制平倉,停損照通知的價;一般判三空單照樣 ±6.5%
tf, gf = mk(); tf.ref_of = lambda c: 100.0; tf.on_tick("3003", 107.0)
tf.step([sig("10:40:3003|假突破", "short", "股3003 3003 做空進場 107.00", "10:40 空✓破 · 停利 104.33 停損 109.00 · 3張 · 判三")]); pf = tf.positions["3003"]
ok(pf["no_max_move"] and pf["judge"] == "判三"); tf.on_report(51, "A0001", 0, 1000, 107.0); ok(pf["state"] == HOLDING)
tf.on_tick("3003", 108.5); ok(pf["state"] == HOLDING, "假突破空單 +8.5% 不該被 6.5% 平倉")
tf.on_tick("3003", 109.0); ok(pf["state"] == EXITING, "碰通知的停損 109 要平倉")
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
ok(s4.positions["3003"]["no_max_move"] and s4.positions["3003"]["judge"] == "判三")
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
s9b.reconcile([], []); ok("6269" in s9b.positions); s9b.reconcile([], []); ok("6269" not in s9b.positions and "確定沒成交" in logs[-1], logs[-1])
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
# S13) 2026-10-31 同時持倉:預設 10;bridge.env 舊的 2 / 5 也當 10;其他數字照用
ok(mk()[0].max_pos == 10 and mk(TRADE_MAX_POS="2")[0].max_pos == 10 and mk(TRADE_MAX_POS="5")[0].max_pos == 10 and mk(TRADE_MAX_POS="3")[0].max_pos == 3)
print("trader.py 測試通過(32 + 17 + 3 + 2 + 平倉檢查 18 + 對帳 3 + 稽核修正 13 種情況 + 等確認對帳 2 + 超額 1 + 張數 1 + 判四 1 + 持倉上限 1)")
