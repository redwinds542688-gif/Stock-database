"""python3 test_trader.py:一次一單交易邏輯測試(假的下單通道,不需要元大元件)"""
import datetime as dt, tempfile, os
from pathlib import Path
from trader import Trader, shift, tick_size, IDLE, ENTERING, HOLDING, EXITING

TW = dt.timezone(dt.timedelta(hours=8)); NOW = [dt.datetime(2026, 10, 22, 10, 0, tzinfo=TW)]
logs = []
class GW:
    def __init__(s): s.sent = []; s.n = 0; s.tags = []
    def send(s, code, mkt, side, lots, price, typ, tif, tag='', flag=' '): s.n += 1; s.sent.append((code, side, lots, price, typ, tif, flag)); s.tags.append(tag); return f"A{s.n:04d}"
    def cancel(s, no, code=''): s.sent.append(("cancel", no))
    def place_oco(s, *a): s.sent.append(("oco",) + a)
    def delete_oco(s, no): s.sent.append(("del_oco", no))
stop = Path(tempfile.mkdtemp()) / "STOP"
def mk(**kw):
    cfg = {"TRADE_ENABLE": "1", "TRADE_STOP_FILE": str(stop), "TRADE_MAX_PER_DAY": "2"}; cfg.update(kw)
    g = GW(); t = Trader(cfg, g, logs.append, lambda: NOW[0]); return t, g
sig = lambda k, kind, title, body: {"_k": k, "kind": kind, "title": title, "body": body}
ENT = sig("10:00:6269|台郡做空進場|判二", "short", "台郡 6269 做空進場 86.20", "10:00 空✓ · 停利 84.05 停損 88.70 · 判二")

assert tick_size(86.2) == 0.1 and shift(86.2, -2) == 86.0 and shift(49.95, 2) == 50.1
# 1) 進場 → 成交 → 碰停利 → 平倉成交 → 空閒
t, g = mk(); t.on_tick("6269", 86.2); t.step([ENT])
assert t.state == ENTERING and g.sent[-1] == ("6269", "S", 1, 86.0, "9", "3", " "), g.sent
t.on_report(51, "A0001", 0, 1000, 86.1); assert t.state == HOLDING and t.pos["entry"] == 86.1
t.step([sig("10:01:2409|友達做多進場|判一", "long", "友達 2409 做多進場 37.25", "10:01 多✓ · 停利 38.18 停損 36.80 · 判一")])
assert t.state == HOLDING and "一次只做一筆" in logs[-1]                      # 持倉中不接新訊號
t.on_tick("6269", 85.0); assert t.state == HOLDING
t.on_tick("6269", 84.0); assert t.state == EXITING and g.sent[-1] == ("6269", "B", 1, 0, "9", "0", "H"), g.sent[-1]   # 空單買回掛漲停 ROD
t.on_report(51, "A0002", 0, 1000, 84.1); assert t.state == IDLE and t.journal[-1]["why"] == "停利" and t.pnl > 1500, t.pnl
# 2) 進場沒成交(價穩失效)→ 回空閒
t2, g2 = mk(); t2.on_tick("6269", 86.2); t2.step([ENT]); t2.on_report(50, "A0001", 25, 0, 0); assert t2.state == IDLE and t2.pos is None
# 3) 平倉只成交一部分 → 再送剩下的
t3, g3 = mk(TRADE_QTY="2"); t3.on_tick("6269", 86.2); t3.step([ENT]); t3.on_report(51, "A0001", 0, 2000, 86.2)
t3.on_tick("6269", 88.8); assert t3.state == EXITING                               # 停損
t3.on_report(51, "A0002", 0, 1000, 88.9); t3.on_report(50, "A0002", 24, 0, 0)    # 成交 1 張、剩下失效
assert t3.state == EXITING and t3.pos["qty"] == 1 and g3.sent[-1][2] == 1          # 再送 1 張
t3.on_report(51, "A0003", 0, 1000, 89.0); assert t3.state == IDLE and t3.pnl < 0
# 4) 手機平倉通知 → 出場
t4, g4 = mk(); t4.on_tick("6269", 86.2); t4.step([ENT]); t4.on_report(51, "A0001", 0, 1000, 86.2)
t4.step([sig("10:20:6269|台郡出|判二", "exit", "台郡 6269 出場 +0.3%:出", "")]); assert t4.state == EXITING
# 5) 一天上限 2 筆
t5, g5 = mk(); t5.n_trades = 2; t5.on_tick("6269", 86.2); t5.step([ENT]); assert t5.state == IDLE and "上限" in logs[-1]
# 6) 緊急停止檔
stop.write_text("1"); t6, g6 = mk(); t6.on_tick("6269", 86.2); t6.step([ENT]); assert t6.state == IDLE and "緊急停止" in logs[-1]; os.remove(stop)
# 7) 13:25 強制平倉
t7, g7 = mk(TRADE_CANCEL_ALL="0"); t7.on_tick("6269", 86.2); t7.step([ENT]); t7.on_report(51, "A0001", 0, 1000, 86.2)
NOW[0] = dt.datetime(2026, 10, 22, 13, 25, tzinfo=TW); t7.step(); assert t7.state == EXITING; NOW[0] = dt.datetime(2026, 10, 22, 10, 0, tzinfo=TW)
# 8) 現價離訊號價太遠 → 不追
t8, g8 = mk(); t8.on_tick("6269", 87.0); t8.step([ENT]); assert t8.state == IDLE and "太晚" in logs[-1]
# 9) 啟動時舊通知不進場;同一則不重複
t9, g9 = mk(); t9.prime([ENT]); t9.on_tick("6269", 86.2); t9.step([ENT]); assert t9.state == IDLE and not g9.sent
# 12) 多單平倉掛跌停;舊的「讓檔 + IOC」模式還在
t12, g12 = mk(); t12.on_tick("2409", 37.25)
t12.step([sig("10:02:2409|友達做多進場|判一", "long", "友達 2409 做多進場 37.25", "10:02 多✓ · 停利 38.18 停損 36.80 · 判一")])
t12.on_report(51, "A0001", 0, 1000, 37.3); t12.on_tick("2409", 36.7); assert g12.sent[-1] == ("2409", "S", 1, 0, "0", "0", "L"), g12.sent[-1]
t13, g13 = mk(TRADE_EXIT_MODE="slip"); t13.on_tick("6269", 86.2); t13.step([ENT]); t13.on_report(51, "A0001", 0, 1000, 86.1)
t13.on_tick("6269", 84.0); assert g13.sent[-1][3] == 84.2 and g13.sent[-1][5] == "3"
# 11) 回報比委託書號先到:用自訂編號(BasketNo)對上
class GW2(GW):
    def send(s, *a): GW.send(s, *a); return None          # 下單當下拿不到委託書號
t11 = Trader({"TRADE_ENABLE": "1", "TRADE_STOP_FILE": str(stop)}, GW2(), logs.append, lambda: NOW[0]); g11 = t11.gw
t11.on_tick("6269", 86.2); t11.step([ENT]); t11.on_report(51, "B0009", 0, 1000, 86.1, basket=g11.tags[-1])
assert t11.state == HOLDING and t11.order is None
# 14) 進場成交 → 掛元大 OCO;OCO 在時程式碰停損只記錄不下單;OCO 成交(不是程式送的單)→ 平倉完成
t14, g14 = mk(); t14.on_tick("6269", 86.2); t14.step([ENT]); t14.on_report(51, "A0001", 0, 1000, 86.1)
assert g14.sent[-1] == ("oco", "6269", "TWSE", -1, 1, 84.05, 88.7), g14.sent[-1]
t14.on_strategy_reply("K0001", True, ""); assert t14.pos["oco_live"]
n = len(g14.sent); t14.on_tick("6269", 88.8); assert len(g14.sent) == n and t14.state == HOLDING      # 不重複下單
t14.on_report(51, "Z9", 0, 1000, 88.9, basket="", code="6269", bs="B")
assert t14.state == IDLE and t14.journal[-1]["why"].startswith("元大條件單") and t14.pnl < 0
# 15) OCO 在、手機平倉通知 → 先刪 OCO 再送平倉
t15, g15 = mk(); t15.on_tick("6269", 86.2); t15.step([ENT]); t15.on_report(51, "A0001", 0, 1000, 86.1); t15.on_strategy_reply("K0002", True, "")
t15.step([sig("10:30:6269|台郡出|判二", "exit", "台郡 6269 出場:出", "")])
assert g15.sent[-2] == ("del_oco", "K0002") and g15.sent[-1][:3] == ("6269", "B", 1) and t15.state == EXITING
# 16) 程式平倉成交後 OCO 也成交了(多平 1 張 → 變成多單)→ 馬上賣出補回
t15.on_report(51, "A0002", 0, 1000, 85.5, basket=g15.tags[-1]); assert t15.state == IDLE
t15.on_report(51, "Z8", 0, 1000, 85.6, basket="", code="6269", bs="B")
assert t15.state == EXITING and g15.sent[-1][:3] == ("6269", "S", 1) and g15.sent[-1][6] == "L", g15.sent[-1]
# 17) OCO 掛失敗 → 程式自己盯停利停損
t17, g17 = mk(); t17.on_tick("6269", 86.2); t17.step([ENT]); t17.on_report(51, "A0001", 0, 1000, 86.1); t17.on_strategy_reply("", False, "權限不足")
t17.on_tick("6269", 88.8); assert t17.state == EXITING and g17.sent[-1][6] == "H"
# 18) 啟動時接回:程式開的空單還沒平、條件單還在 → 接回持倉、不重掛條件單;手動部位不管
t18, g18 = mk()
tr = [{"code": "6269", "bs": "S", "lots": 1, "price": 86.1, "time": "10:37:02", "order_no": "A1"},
      {"code": "2409", "bs": "B", "lots": 2, "price": 37.3, "time": "10:40:00", "order_no": "M1"}]
r = t18.resume(tr, {"A1": "T103701001", "M1": ""}, [{"no": "K77", "code": "6269", "status": 1}], [ENT])
assert r == "6269" and t18.state == HOLDING and t18.pos["dir"] == -1 and t18.pos["entry"] == 86.1 and t18.pos["oco_live"] and t18.pos["tp"] == 84.05
assert not [x for x in g18.sent if x[0] == "oco"] and t18.n_trades == 1
t18.step([sig("10:50:6269|台郡出|判二", "exit", "台郡 6269 出場:出", "")]); assert g18.sent[-2] == ("del_oco", "K77")   # 接回後照常管理
# 19) 接回:條件單不在 → 用手機通知的停利停損重掛;已平倉的筆數和損益算回來
t19, g19 = mk()
tr = [{"code": "1709", "bs": "S", "lots": 3, "price": 56.2, "time": "09:21:00", "order_no": "A1"},
      {"code": "1709", "bs": "B", "lots": 3, "price": 57.2, "time": "09:53:00", "order_no": "K1"},
      {"code": "6269", "bs": "S", "lots": 1, "price": 86.1, "time": "10:37:02", "order_no": "A2"}]
t19.resume(tr, {"A1": "T092100001", "A2": "T103701002"}, [], [ENT])
assert t19.n_trades == 2 and t19.pnl < -3000 and g19.sent[-1] == ("oco", "6269", "TWSE", -1, 1, 84.05, 88.7)
# 20) 沒有未平部位 → 空閒
t20, g20 = mk(); assert t20.resume([{"code": "6269", "bs": "S", "lots": 1, "price": 86.1, "time": "1", "order_no": "A1"}, {"code": "6269", "bs": "B", "lots": 1, "price": 85.0, "time": "2", "order_no": "A2"}], {"A1": "T1"}, [], []) is None and t20.state == IDLE and t20.n_trades == 1 and t20.pnl > 0
# 21) 平倉設定成「讓檔 + IOC」也一樣:13:25 強制平倉一律掛漲停 / 跌停 ROD(集合競價不收 IOC)
t21, g21 = mk(TRADE_EXIT_MODE="slip", TRADE_CANCEL_ALL="0"); t21.on_tick("6269", 86.2); t21.step([ENT]); t21.on_report(51, "A0001", 0, 1000, 86.1)
NOW[0] = dt.datetime(2026, 10, 22, 13, 25, tzinfo=TW); t21.step()
assert g21.sent[-1] == ("6269", "B", 1, 0, "9", "0", "H"), g21.sent[-1]
t21.on_report(50, "A0002", 24, 0, 0, basket=g21.tags[-1]); assert g21.sent[-1][6] == "H"      # 失效再送也是漲停 ROD
NOW[0] = dt.datetime(2026, 10, 22, 10, 0, tzinfo=TW)
# 22) 13:20 強制平倉(集合競價前,還在逐筆撮合);13:10 以後不進場
t22, g22 = mk(TRADE_CANCEL_ALL="0"); t22.on_tick("6269", 86.2); t22.step([ENT]); t22.on_report(51, "A0001", 0, 1000, 86.1)
NOW[0] = dt.datetime(2026, 10, 22, 13, 19, tzinfo=TW); t22.step(); assert t22.state == HOLDING
NOW[0] = dt.datetime(2026, 10, 22, 13, 20, tzinfo=TW); t22.step(); assert t22.state == EXITING and g22.sent[-1][5:] == ("0", "H") and "13:20 強制平倉" in t22.pos["exit_why"]
NOW[0] = dt.datetime(2026, 10, 22, 13, 12, tzinfo=TW); t23, g23 = mk(); t23.on_tick("6269", 86.2); t23.step([ENT]); assert t23.state == IDLE and "13:10" in logs[-1]
NOW[0] = dt.datetime(2026, 10, 22, 10, 0, tzinfo=TW)
# 24) 13:23 複查:程式的空單還沒平(平倉單失效了)→ 再掛漲停;手動空單 → 掛漲停買回;手動多單 → 只提醒
alerts = []
t24, g24 = mk(TRADE_CANCEL_ALL="0", TRADE_RECHECK_CLOSE="short"); t24.alert = lambda a, b="": alerts.append(a)
t24.on_tick("6269", 86.2); t24.step([ENT]); t24.on_report(51, "A0001", 0, 1000, 86.1)
NOW[0] = dt.datetime(2026, 10, 22, 13, 20, tzinfo=TW); t24.step(); t24.on_report(50, "A0002", 24, 0, 0, basket=g24.tags[-1])   # 平倉單失效 → 會自己再送
n0 = len(g24.sent); t24.order["dead"] = True                                                                          # 假裝重送的也失效
left = t24.recheck([{"code": "6269", "bs": "S", "lots": 1}, {"code": "2409", "bs": "S", "lots": 2}, {"code": "2330", "bs": "B", "lots": 1}])
assert dict(left) == {"6269": -1, "2409": -2, "2330": 1}
assert g24.sent[n0][:3] == ("6269", "B", 1) and g24.sent[n0][6] == "H"                    # 程式的 → 漲停 ROD
assert any(x[:3] == ("2409", "B", 2) and x[6] == "H" for x in g24.sent)                     # 手動空單 → 漲停買回
assert not any(x[0] == "2330" for x in g24.sent) and len(alerts) == 3                       # 手動多單只提醒
# 24b) 預設 TRADE_RECHECK_CLOSE=all(使用者:「平倉也是一樣 要包含投資先生手動交易尚未平倉的股票」):手動空單漲停買回、手動多單跌停賣出
t24b, g24b = mk(TRADE_CANCEL_ALL="0")
left = t24b.recheck([{"code": "2409", "bs": "S", "lots": 2}, {"code": "2330", "bs": "B", "lots": 1}, {"code": "2317", "bs": "B", "lots": 3}, {"code": "2317", "bs": "S", "lots": 3}])
assert dict(left) == {"2409": -2, "2330": 1}                                                # 2317 今天已經沖掉,不動
assert any(x[:3] == ("2409", "B", 2) and x[6] == "H" for x in g24b.sent)
assert any(x[:3] == ("2330", "S", 1) and x[6] == "L" and x[5] == "0" for x in g24b.sent)      # 手動多單 → 跌停賣出(現股)
assert not any(x[0] == "2317" for x in g24b.sent)
# 25) 第二次複查(13:23):上一次的補單還掛著沒成交 → 不重複下單;補單失效(沒掛著)→ 再補
n1 = len(g24.sent)
working = [{"code": "6269", "bs": "B", "lots": 1}, {"code": "2409", "bs": "B", "lots": 2}]
left = t24.recheck([{"code": "6269", "bs": "S", "lots": 1}, {"code": "2409", "bs": "S", "lots": 2}, {"code": "2330", "bs": "B", "lots": 1}], working, at="13:23")
assert not [x for x in g24.sent[n1:] if x[0] in ("6269", "2409")], g24.sent[n1:]          # 掛著的夠了,不重複
left = t24.recheck([{"code": "2409", "bs": "S", "lots": 2}], [], at="13:24")
assert g24.sent[-1][:3] == ("2409", "B", 2) and g24.sent[-1][6] == "H"                      # 沒掛著了 → 再補
assert t24.recheck_times == ["13:22", "13:23", "13:24"]
# 26) 13:20 取消全部掛單 → 再平倉:持倉中、條件單在 → 先刪條件單;13:20 這裡不自己平(等 bridge),用元大成交回報平倉
NOW[0] = dt.datetime(2026, 10, 22, 10, 0, tzinfo=TW); t26, g26 = mk(); t26.on_tick("6269", 86.2); t26.step([ENT]); t26.on_report(51, "A0001", 0, 1000, 86.1); t26.on_strategy_reply("K9", True, "")
NOW[0] = dt.datetime(2026, 10, 22, 13, 20, tzinfo=TW); n2 = len(g26.sent); t26.step(); assert len(g26.sent) == n2      # 13:20 交給 bridge
t26.prepare_close_all(); assert g26.sent[-1] == ("del_oco", "K9")
t26.recheck([{"code": "6269", "bs": "S", "lots": 1}], [], at="13:20"); assert t26.state == EXITING and g26.sent[-1][:3] == ("6269", "B", 1) and g26.sent[-1][6] == "H"
# 27) bridge 沒做(例如查詢失敗)→ 13:21 自己強制平倉
t27, g27 = mk(); NOW[0] = dt.datetime(2026, 10, 22, 10, 0, tzinfo=TW); t27.on_tick("6269", 86.2); t27.step([ENT]); t27.on_report(51, "A0001", 0, 1000, 86.1)
NOW[0] = dt.datetime(2026, 10, 22, 13, 21, tzinfo=TW); t27.step(); assert t27.state == EXITING and g27.sent[-1][6] == "H"
# 28) 進場單還在等就到 13:20:取消後元大顯示成交了 1 張 → 平 1 張;元大顯示沒成交 → 回空閒
NOW[0] = dt.datetime(2026, 10, 22, 13, 9, tzinfo=TW); t28, g28 = mk(); t28.on_tick("6269", 86.2); t28.step([ENT]); assert t28.state == ENTERING
NOW[0] = dt.datetime(2026, 10, 22, 13, 20, tzinfo=TW); t28.order["no"] = "A0001"; t28.prepare_close_all(); assert g28.sent[-1][0] == "cancel"
t28.recheck([{"code": "6269", "bs": "S", "lots": 1}], [], at="13:20"); assert t28.state == EXITING and g28.sent[-1][:3] == ("6269", "B", 1)
t29, g29 = mk(); NOW[0] = dt.datetime(2026, 10, 22, 13, 9, tzinfo=TW); t29.on_tick("6269", 86.2); t29.step([ENT]); NOW[0] = dt.datetime(2026, 10, 22, 13, 20, tzinfo=TW)
t29.prepare_close_all(); t29.recheck([], [], at="13:20"); assert t29.state == IDLE and t29.pos is None
NOW[0] = dt.datetime(2026, 10, 22, 10, 0, tzinfo=TW)
# 10) 沒開啟 → 不交易
t10, g10 = mk(TRADE_ENABLE="0"); t10.on_tick("6269", 86.2); t10.step([ENT]); assert not g10.sent
print("trader.py 測試通過(30 種情況)")
