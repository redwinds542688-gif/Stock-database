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
t7, g7 = mk(); t7.on_tick("6269", 86.2); t7.step([ENT]); t7.on_report(51, "A0001", 0, 1000, 86.2)
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
# 10) 沒開啟 → 不交易
t10, g10 = mk(TRADE_ENABLE="0"); t10.on_tick("6269", 86.2); t10.step([ENT]); assert not g10.sent
print("trader.py 測試通過(17 種情況)")
