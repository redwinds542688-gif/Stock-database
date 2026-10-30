"""python3 test_quotes.py:QuoteBook 測試"""
from quotes import QuoteBook
q = QuoteBook(); q.set_meta("6269", "台郡", "tse", 88.3)
q.on_tick("6269", "20261022", 9, 0, 0, 90.2, 1123, 90.1, 90.2)
q.on_tick("6269", "20261022", 9, 0, 5, 92.8, 50, 92.7, 92.8)
q.on_tick("6269", "20261022", 9, 1, 3, 91.5, 10, 999999999, -999999999)   # 市價買 / 賣的特殊值不當最佳價
m = q.mis(["6269", "2409"]); r = m["msgArray"][0]
assert r["z"] == "91.50" and r["v"] == "1183" and r["h"] == "92.80" and r["l"] == "90.20" and r["o"] == "90.20" and r["y"] == "88.30", r
assert r["b"] == "92.70_" and r["a"] == "92.80_" and r["t"] == "09:01:03" and r["d"] == "20261022" and m["missing"] == ["2409"]
q.on_ref("2409", 37.25); q.on_tick("2409", "20261022", 9, 0, 0, 37.3, 3000); assert q.mis(["2409"])["msgArray"][0]["y"] == "37.25"
print("quotes.py 測試通過")
