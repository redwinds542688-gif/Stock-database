"""元大逐筆成交 → 每檔最新報價(給手機即時價用,格式和證交所 MIS getStockInfo 一樣,手機不用改)

2026-10-22 使用者:「手機即時價也要改用元大」。
欄位(手機 liveTick 用到的):c 代碼、z 成交價、v 累計量(張)、y 昨收、o 開、h 高、l 低、b / a 最佳買 / 賣價("價_" 字串)、
t 最後成交時間 HH:MM:SS、d 交易日 YYYYMMDD、n 名稱、ex tse / otc。
中繼程式盤中才啟動(例如當掉重開)的話,累計量只從啟動那刻算起(價格不受影響)。
"""
import threading, time


class QuoteBook:
    def __init__(self, vol_mult=1000):
        self.vol_mult = vol_mult          # 逐筆量單位是張時 = 1000;累計量回傳「張」
        self.lock = threading.Lock()
        self.q = {}                       # code -> dict
        self.meta = {}                    # code -> {n, ex, y}
        self.last_ts = 0.0

    def set_meta(self, code, name="", ex="tse", prev_close=None):
        self.meta[code] = {"n": name, "ex": ex, "y": prev_close}

    def on_ref(self, code, ref_price):
        """序號 −1(清盤)那筆:成交價欄位 = 開盤參考價(= 昨收)"""
        if ref_price and ref_price > 0:
            with self.lock:
                self.meta.setdefault(code, {"n": "", "ex": "tse", "y": None})["y"] = ref_price

    def on_tick(self, code, day, hh, mm, ss, price, vol, bid=None, ask=None):
        if not (price > 0):
            return
        with self.lock:
            r = self.q.get(code)
            if r is None:
                r = self.q[code] = {"o": price, "h": price, "l": price, "v": 0.0}
            r["z"] = price; r["h"] = max(r["h"], price); r["l"] = min(r["l"], price)
            r["v"] += vol * self.vol_mult / 1000.0
            if bid and bid > 0 and bid < 999999: r["b"] = bid
            if ask and ask > 0 and ask < 999999: r["a"] = ask
            r["t"] = f"{int(hh):02d}:{int(mm):02d}:{int(ss):02d}"; r["d"] = day
            self.last_ts = time.time()

    def mis(self, codes):
        """回傳 {"msgArray": [...], "src": "yuanta", "missing": [...], "age": 秒}"""
        out, missing = [], []
        with self.lock:
            for c in codes:
                r = self.q.get(c); m = self.meta.get(c, {})
                if not r:
                    missing.append(c); continue
                fmt = lambda x: f"{x:.2f}" if x is not None else "-"
                out.append({"c": c, "n": m.get("n", ""), "ex": m.get("ex", "tse"), "z": fmt(r["z"]), "v": str(int(round(r["v"]))),
                            "y": fmt(m.get("y")), "o": fmt(r["o"]), "h": fmt(r["h"]), "l": fmt(r["l"]),
                            "b": (fmt(r["b"]) + "_") if r.get("b") else "-", "a": (fmt(r["a"]) + "_") if r.get("a") else "-",
                            "t": r["t"], "d": r["d"], "src": "yuanta"})
            age = time.time() - self.last_ts if self.last_ts else None
        return {"msgArray": out, "src": "yuanta", "missing": missing, "age": age,
                "queryTime": {"sysTime": time.strftime("%H:%M:%S")}}
