"""python3 test_bars.py:BarBook 單元測試(不需要元大元件)"""
from bars import BarBook, hm_of

b = BarBook(vol_mult=1000)
assert hm_of(8, 59) == "09:00" and hm_of(13, 31) == "13:30" and hm_of(9, 5) == "09:05"
b.add_tick("2330", 8, 59, 1, 100.0, 50)          # 開盤撮合 → 09:00
b.add_tick("2330", 9, 0, 2, 101.0, 2)
b.add_tick("2330", 9, 0, 3, 99.5, 1)
b.add_tick("2330", 9, 0, 3, 120.0, 9)            # 重送(序號沒變大)不算
b.add_tick("2330", 9, 1, 4, 100.5, 3)
assert b.take("09:00")["2330"] == [100.0, 101.0, 99.5, 99.5, 53000], b.take("09:00")
assert b.take("09:01")["2330"] == [100.5, 100.5, 100.5, 100.5, 3000]
b.add_tick("2330", 9, 0, 5, 98.0, 1, sent={"09:00"})   # 已送出的分鐘又來成交 → 標記重送
assert b.take_dirty() == ["09:00"] and b.take("09:00")["2330"][2] == 98.0
assert not b.add_tick("2330", 9, 2, 6, 0, 1)      # 沒成交價不算
print("bars.py 測試通過")
