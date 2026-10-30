"""元大逐筆成交 → 1 分 K(不依賴元大元件,可單獨測試)

每筆成交 (code, 時, 分, 秒, 序號, 成交價, 成交量) 依成交時間歸到那一分鐘:
  開 = 該分鐘第一筆、高 / 低 = 最高 / 最低、收 = 最後一筆、量 = 加總(張 × VOL_MULT = 股)
09:00 以前(開盤撮合)歸 09:00,13:30 以後(收盤撮合)歸 13:30;同一檔序號不大於上一筆的(重送)不算。
"""
import threading


def hm_of(h, m):
    t = f"{int(h):02d}:{int(m):02d}"
    return "09:00" if t < "09:00" else "13:30" if t > "13:30" else t


class BarBook:
    def __init__(self, vol_mult=1000):
        self.vol_mult = vol_mult
        self.lock = threading.Lock()
        self.bars = {}      # minute -> {code: [o, h, l, c, v]}
        self.last_sn = {}   # code -> 最後一筆序號
        self.dirty = set()  # 已送出後又收到成交(晚到)的分鐘,要重送

    def add_tick(self, code, h, m, sn, price, vol, sent=()):
        if not (price > 0) or vol is None or vol < 0:
            return False
        with self.lock:
            if sn is not None and sn > 0:
                if sn <= self.last_sn.get(code, 0):
                    return False
                self.last_sn[code] = sn
            mm = hm_of(h, m)
            b = self.bars.setdefault(mm, {}).get(code)
            v = vol * self.vol_mult
            if b is None:
                self.bars[mm][code] = [price, price, price, price, v]
            else:
                b[1] = max(b[1], price); b[2] = min(b[2], price); b[3] = price; b[4] += v
            if mm in sent:
                self.dirty.add(mm)
            return True

    def take(self, minute):
        """回傳某分鐘的 {code: [o,h,l,c,v]}(複製一份,原資料保留給晚到的成交更新)"""
        with self.lock:
            self.dirty.discard(minute)
            return {c: list(v) for c, v in self.bars.get(minute, {}).items()}

    def take_dirty(self):
        with self.lock:
            d = sorted(self.dirty); self.dirty.clear()
            return d

    def minutes(self):
        with self.lock:
            return sorted(self.bars)
