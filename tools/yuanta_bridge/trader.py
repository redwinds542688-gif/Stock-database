"""自動交易(2026-10-22 使用者:「真的接通 可以先試 一次只掛一單 成交後才能繼續」→「先寫好 預設測試環境」)

2026-10-28 06:27 使用者「改 判一二三暫時解除鎖定」(強制進場 / 強制平倉規格定案):
  · 同時最多 TRADE_MAX_POS(5)檔判一 / 二 / 三的部位;一檔股票同時只會有一個部位。取消「一天最多幾筆」的限制
  · 強制進場(手機長按股票 → 滑到底):做多掛漲停買、做空掛跌停賣(ROD,一定成交),張數由手機算好送來;
    判別式記「強三」,停利 / 停損用判三的 % 從實際成交價算;不佔 5 檔名額,不受「當天虧損上限」和 STOP 檔限制
  · 強制平倉(強平):一律全部平倉,做多跌停賣、做空漲停買;強平過的股票今天判一 / 二 / 三不再自動進場(強制進場仍可)
  · 全部不預掛元大條件單(TRADE_BROKER_STOP 預設 0):價格碰到停利 / 停損 / 昨收 ±TRADE_MAX_MOVE_PCT(6.5%)→ 程式立刻掛漲停 / 跌停全部平倉
  · 漲停 / 跌停單(ROD)送出 TRADE_VERIFY_WAIT(30)秒還沒成交完 → bridge 查元大:單子還掛著 = 繼續等;元大沒有這張單 = 重送一次並推播

訊號來源 = 手機的通知(雲端紀錄 ?log=get):進場通知(做多 / 做空進場)→ 進場;同一檔的平倉通知 → 出場。
保護:每筆 TRADE_QTY 張、當天虧損到 TRADE_MAX_LOSS 就不再自動進場、13:20 一律平倉、
      STOP 檔案(緊急停止:不再自動進場,有部位照樣平)、正式環境要 TRADE_ALLOW_PROD=1 才會下單。
以元大的即時回報為準(RR_RealReport:51 = 股票成交),不自己假設成交。

這個檔不依賴元大元件:下單 / 取消透過 gateway(bridge 提供),測試時可以換成假的。
"""
import json, math, re, time, threading, urllib.request
from pathlib import Path


def tick_size(p):
    """台股升降單位"""
    return 0.01 if p < 10 else 0.05 if p < 50 else 0.1 if p < 100 else 0.5 if p < 500 else 1 if p < 1000 else 5


def shift(p, ticks):
    """價格往上(+)/ 往下(−)移 ticks 檔"""
    for _ in range(abs(ticks)):
        p = p + tick_size(p) if ticks > 0 else p - tick_size(p - 1e-9)
    return round(p, 2)


IDLE, ENTERING, HOLDING, EXITING, HALT = "空閒", "進場中", "持倉中", "平倉中", "停止"
RE_CODE = re.compile(r"\b(\d{4,6})\b")
# 2026-10-29 稽核 #7:價格可能帶千分位(1,035.00),原本 [\d.]+ 會只抓到「1」→ 改成 [\d,]+(?:\.\d+)?,用 num() 去掉逗號
RE_TPSL = re.compile(r"停利\s*([\d,]+(?:\.\d+)?)\s*停損\s*([\d,]+(?:\.\d+)?)")
RE_PX_END = re.compile(r"([\d,]+(?:\.\d+)?)\s*$")      # 標題最後的進場價
RE_BAR = re.compile(r"^\s*(\d{1,2}:\d{2})")            # 2026-10-29 稽核 #3:通知內容開頭的 K 棒時間(例如「09:05 多✓ · …」)
RE_JUDGE = re.compile(r"判[一二三四]")
EXIT_KINDS = ("loss", "profit", "exit")


def num(s):
    return float(str(s).replace(",", ""))


def code_of(title):
    """通知標題裡的股票代碼(跟 on_signal 同一種取法;2026-10-29 稽核 #4 接回改用這個精確比對,不用 `c in title` 子字串)"""
    m = RE_CODE.search(title or "")
    return m.group(1) if m else None


def judge_of(body):
    m = RE_JUDGE.search(body or "")
    return m.group(0) if m else None


def minutes_of(s):
    """'HH:MM' / 'HH:MM:SS' / '2026/10/29 下午 01:05:02' / 'HHMMSS' → 一天的第幾分鐘;看不懂回 None"""
    s = str(s or "")
    m = re.search(r"(\d{1,2}):(\d{2})", s)
    if m:
        h = int(m.group(1))
        if "下午" in s or "PM" in s.upper():
            h = h + 12 if h < 12 else h
        elif ("上午" in s or "AM" in s.upper()) and h == 12:
            h = 0
        return h * 60 + int(m.group(2))
    m = re.fullmatch(r"\s*(\d{2})(\d{2})(\d{2})?\d*\s*", s)
    if m and int(m.group(1)) < 24:
        return int(m.group(1)) * 60 + int(m.group(2))
    return None
FORCED = "強三"
MANUAL = "元三"      # 2026-10-28 使用者:在元大(投資先生)自己下的單,成交後交給判三管


class Trader:
    def __init__(self, cfg, gateway, log, now):
        """gateway:send(code, mkt, side 'B'/'S', qty 張, price, order_type, tif, tag, price_flag ' '限價 / H 漲停 / L 跌停) -> 委託書號或 None;cancel(order_no, code)
           place_oco(code, mkt, dir, 張, 停利價, 停損價)(結果從 on_strategy_reply 回來);delete_oco(策略編號)
           now():回傳台灣時間 datetime"""
        self.cfg, self.gw, self.log, self.now = cfg, gateway, log, now
        self.enabled = cfg.get("TRADE_ENABLE", "0") == "1"
        # 2026-10-28 18:34 使用者「改」(先只開元三):TRADE_ENABLE = 總開關(會不會送真單);TRADE_AUTO = 照手機判一二三通知自動進場(預設 0 = 關)。
        #   AUTO=0 時:不自動進場;元三(手動單接管)、手機長按強三 / 強平、已有部位的停利 / 停損 / ±6.5% / 13:20 平倉照常
        self.auto = cfg.get("TRADE_AUTO", "0") == "1"
        self.qty = int(cfg.get("TRADE_QTY", "1"))
        # 2026-10-30 10:16 使用者「和桐 是程式自己下單的 … 照25萬上限 應該成交14張 可是卻只交易一張」:自動進場張數 = 多空上限 ÷ 一張(和手機 / 通知同一套),
        #   TRADE_CAP 沒設就用 ENGINE_CAP(25 萬);TRADE_CAP=0 才回到固定 TRADE_QTY 張
        self.cap = int(float(cfg.get("TRADE_CAP", cfg.get("ENGINE_CAP", "250000")) or 0))
        self.max_pos = int(cfg.get("TRADE_MAX_POS", "5"))           # 2026-10-28 使用者「開放同時可以五檔股票在線 強制進場不在受限範圍內」
        self.max_loss = float(cfg.get("TRADE_MAX_LOSS", "20000"))   # 2026-10-26 使用者「改。當天虧 20,000 就停止進場」(原 3000)
        # 2026-10-27 使用者:一天買 + 賣成交金額上限(元大額度 500 萬留緩衝 490 萬);自動進場前算這筆一進一出要用多少,不夠就少掛幾張、連 1 張都不夠就不進;
        #   進場委託被拒(額度 / 金額)→ 少 1 張再掛,一直退到 1 張還被拒才放棄。強制進場不看這個(使用者:元大自己會擋)
        self.max_amount = float(cfg.get("TRADE_MAX_AMOUNT", "4900000"))
        self.judges = set(x.strip() for x in cfg.get("TRADE_JUDGES", "判一,判二,判三,判四").split(",") if x.strip())   # 2026-10-31 預設加判四(使用者「判四 … 真正的下單也啟動 自動平倉機制也啟動」);bridge.env 有寫 TRADE_JUDGES 的要自己加
        if self.judges == {"判一", "判二", "判三"}: self.judges.add("判四")   # bridge.env 還是舊範本那行(判一,判二,判三)= 沒特別排除 → 一樣加判四;要排除就寫成別的(例如 判一,判二,判三,-)
        self.slip = int(cfg.get("TRADE_SLIP_TICKS", "2"))          # 讓價檔數(確保成交)
        self.max_dev = float(cfg.get("TRADE_MAX_DEV_PCT", "0.5"))  # 現價離訊號價超過 % 就不追
        self.fill_wait = float(cfg.get("TRADE_FILL_WAIT", "8"))    # 自動進場(限價 IOC)送單後等成交回報幾秒
        # 2026-10-29 稽核 #5:IOC 等 fill_wait 秒沒回報 → 不馬上拿掉部位,改「等確認」再等 TRADE_CONFIRM_WAIT 秒(晚到的 T 成交照樣接成持倉)
        self.confirm_wait = float(cfg.get("TRADE_CONFIRM_WAIT", "60"))
        # 2026-10-29 稽核 #1:進場通知的時間(t,發通知那分鐘)比現在舊超過幾分鐘就不進場
        self.sig_max_age = float(cfg.get("TRADE_SIGNAL_MAX_AGE_MIN", "5"))
        # 2026-10-22 使用者:「30 元做空反漲到 30.5 觸發平倉,直接掛漲停價贖回」:平倉空單掛漲停(H)買回、多單掛跌停(L)賣出,ROD
        self.exit_mode = cfg.get("TRADE_EXIT_MODE", "limitup")
        # 2026-10-28 使用者「這1取消 所有都不預掛 只要股價觸發停利/停損 程式立刻啟動強制平倉機制」:預設不掛元大條件單(1 = 舊做法)
        self.broker_stop = cfg.get("TRADE_BROKER_STOP", "0") == "1"
        # 2026-10-28 使用者「+6.5 是針對做空的股票 -6.5 是針對做多的股票 全部股票都列入管理」:離昨收 ±6.5%(對部位不利的方向)→ 程式立刻全部平倉
        self.max_move = float(cfg.get("TRADE_MAX_MOVE_PCT", "6.5"))
        # 2026-10-28 使用者「如果30秒內沒有強制平倉成功 則再次檢查元大 是平倉單還掛著未成交 還是強制平倉訊息沒傳到元大 前者再等 後者再掛一次」
        self.verify_wait = float(cfg.get("TRADE_VERIFY_WAIT", "30"))
        self.max_resend = int(cfg.get("TRADE_MAX_RESEND", "3"))     # 平倉單最多自動重送幾次(之後推播請手動)
        # 2026-10-28 12:27 使用者「我在元大自己下的股票 成交後也顯示在這邊 受判三管制」→「這個都下單的叫做 元三」→「改」:
        #   不是程式送的單成交、這檔程式又沒有部位 → 接成「元三」部位,停利 / 停損用判三的 %(TRADE_Y3_TP_PCT / TRADE_Y3_SL_PCT,從成交價算),±6.5%、13:20、強平照樣管
        self.y3 = cfg.get("TRADE_Y3", "1") == "1"
        self.y3_tp = float(cfg.get("TRADE_Y3_TP_PCT", "2.5"))
        self.y3_sl = float(cfg.get("TRADE_Y3_SL_PCT", "4.5"))
        self.force_exit = cfg.get("TRADE_FORCE_EXIT", "13:20")
        self.ignore = set(x.strip() for x in cfg.get("TRADE_IGNORE_CODES", "YY0070").split(",") if x.strip())   # 2026-10-28 庫存裡下市、不能賣的股票:元三不接、13:22 複查不平
        self.last_entry = cfg.get("TRADE_LAST_ENTRY", "13:10")
        self.recheck_times = [x.strip() for x in cfg.get("TRADE_RECHECK", "13:22,13:23,13:24").split(",") if x.strip()]
        self.recheck_at = self.recheck_times[0] if self.recheck_times else "13:23"
        self.recheck_close = cfg.get("TRADE_RECHECK_CLOSE", "all")
        self.alert = lambda title, body="": None      # bridge 會換成「發提醒到手機」
        self.post = lambda title, body, kind: None    # bridge 會換成「寫一則交易訊息到手機的訊息欄」(強三進出場、強平)
        self.ref_of = lambda code: None                # bridge 會換成「這檔的昨收」(±6.5% 用)
        self.name_of = lambda code: code               # bridge 會換成「這檔的名稱」
        self.want_sub = lambda code: None              # 2026-10-28 平倉檢查 #7:bridge 會換成「這檔補訂閱逐筆、回傳市場」(元三 / 接回的股票不在名單上也盯得到價)
        self.cancel_all = cfg.get("TRADE_CANCEL_ALL", "1") == "1"
        self.type_long = cfg.get("TRADE_TYPE_LONG", "0")           # 委託種類:0 現股
        self.type_short = cfg.get("TRADE_TYPE_SHORT", "9")         # 9 現股當沖委託控管(先賣後買)
        self.stop_file = Path(cfg.get("TRADE_STOP_FILE", "/opt/yuanta_bridge/STOP"))
        sf = cfg.get("TRADE_STATE_FILE", "")
        self.state_file = Path(sf) if sf else None                 # 強三的停利 / 停損 % 和今天強平過的股票(程式重開接回用)
        self.lock = threading.RLock()
        self.reset_day()

    def reset_day(self):
        self.positions = {}        # code → {code, mkt, dir, qty(張), entry, tp, sl, judge, typ, title, state, order, forced, ...}
        self.seen = set()          # 已處理的通知 _k
        self.n_trades, self.pnl, self.last_px = 0, 0.0, {}
        self.amount_today = 0.0
        self.mkt_of = {}
        self.journal = []
        self.flat_watch = {}       # code → 剛平倉的部位 {dir, typ, mkt, until}:這段時間又有同方向平倉成交(手動 / 條件單同時觸發)= 多平了,要補回來
        self.locked = set()        # 今天強平過的股票:判一 / 二 / 三不再自動進場
        self.cmd_seen = {}         # 強制指令編號 → 結果(手機重送 / 滑兩次只做一次)
        self.oco_q = []
        # 2026-10-29 稽核 #1:啟動時要「成功」讀到一次手機通知(prime)才算準備好;沒準備好不自動進場、不理平倉通知
        self.primed = False
        self.start_hm = self.hm()
        # 2026-10-29 稽核 #3:今天已進場過的訊號(代碼|判別式|K 棒時間),同一個訊號換了發送分鐘重送也不再進;存狀態檔(程式重開還在)
        self.entered_keys = set(self.load_state().get("entered") or [])

    # ── 小工具 ──
    @property
    def state(self):
        """整體狀態(給紀錄 / 舊介面):停止 / 空閒 / 有部位"""
        if not self.enabled:
            return HALT
        return IDLE if not self.positions else HOLDING

    def auto_count(self):
        return sum(1 for p in self.positions.values() if not p.get("forced"))

    def hm(self):
        return self.now().strftime("%H:%M")

    def new_pos(self, code, d, judge, title, forced=False, tp=0.0, sl=0.0):
        p = {"code": code, "mkt": self.mkt_of.get(code, "TWSE"), "dir": d, "qty": 0, "entry": 0.0, "tp": tp, "sl": sl, "judge": judge,
             "typ": self.type_long if d > 0 else self.type_short, "title": title, "state": ENTERING, "order": None, "forced": forced,
             "closed_lots": 0, "closed_cost": 0.0}
        self.positions[code] = p
        return p

    def save_state(self):
        if not self.state_file:
            return
        try:
            js = {"day": self.now().strftime("%Y-%m-%d"), "locked": sorted(self.locked),
                  "forced": {c: {"tp_pct": p.get("tp_pct"), "sl_pct": p.get("sl_pct")} for c, p in self.positions.items() if p.get("forced")}}
            old = self.load_state()
            for c, v in (old.get("forced") or {}).items():
                js["forced"].setdefault(c, v)                       # 已平倉的也留著(同一天程式重開接回時還找得到)
            js["entered"] = sorted(set(old.get("entered") or []) | self.entered_keys)   # 2026-10-29 稽核 #3
            self.state_file.write_text(json.dumps(js, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            self.log(f"[交易] 存狀態檔失敗 {e}")

    def load_state(self):
        try:
            if self.state_file and self.state_file.exists():
                js = json.loads(self.state_file.read_text(encoding="utf-8"))
                if js.get("day") == self.now().strftime("%Y-%m-%d"):
                    return js
        except Exception:
            pass
        return {}

    def prime(self, signals):
        """啟動時把當天已經有的通知都標成「看過」,不會拿舊訊號進場
        2026-10-29 稽核 #1:signals = None(讀取失敗)→ 不算準備好(step 之後每次讀成功的那一次才 prime);
          延後 prime 時,啟動那分鐘之後才發的平倉通知照常處理(啟動前的不理),進場通知一律不補"""
        if signals is None:
            self.log("[交易] !! 啟動:讀不到手機通知(網路 / Worker 失敗)→ 先不自動進場、不理平倉通知,之後每 3 秒重讀,讀到才開始")
            return
        later = []
        for it in signals:
            if it.get("kind") in EXIT_KINDS and it.get("t") and str(it["t"]) > self.start_hm:
                later.append(it); continue                          # 啟動之後才來的平倉通知:prime 完照常處理
            self.seen.add(it.get("_k") or (it.get("t", "") + it.get("title", "")))
        self.primed = True
        self.log(f"[交易] 啟動:略過今天已有的 {len(signals) - len(later)} 則通知;{('開啟' + ('' if self.auto else '(自動進場關,只管元三 / 強制模式)')) if self.enabled else '關閉(TRADE_ENABLE=0)'},"
                 f"每筆 {"多空上限 " + format(self.cap, ",") + " ÷ 一張" if self.cap > 0 else str(self.qty) + " 張"}、同時最多 {self.max_pos} 檔、虧損上限 {self.max_loss:,.0f}、±{self.max_move}% 強制平倉、"
                 f"{'預掛元大條件單' if self.broker_stop else '不預掛條件單(程式盯價)'}")
        for it in later:
            self.on_signal(it)

    def resume(self, trades, order_tags, strategies, signals, working=(), order_types=None):
        """2026-10-22 使用者:「加 啟動時接回部位」:程式重開時,從元大今天的成交回報算出還沒平的部位,是這支程式開的就接回來管理。
        trades:[{code, bs 'B'/'S', lots, price, time, order_no}](時間舊到新);order_tags:{委託書號: BasketNo};
        strategies:[{no, code, status}](有效的條件單);signals:手機今天的通知(找停利 / 停損價)。
        只接「程式送的單」(BasketNo 以 T = 自動 / F = 強制 開頭)開的部位;手動在投資先生做的不管。今天已做筆數、已實現損益也一起算回來。
        2026-10-28 改成全部接回(最多 5 檔 + 強制單),回傳接回的代碼清單"""
        with self.lock:
            st = self.load_state()
            self.locked |= set(st.get("locked") or [])
            net, cost, by, otyp, t_open = {}, {}, {}, {}, {}
            order_types = order_types or {}
            signals = signals or []                                  # 2026-10-29 稽核 #1:讀通知失敗(None)當作沒有
            for t in sorted(trades, key=lambda x: x.get("time", "")):
                c, q = t["code"], t["lots"] * (1 if t["bs"] == "B" else -1)
                n0 = net.get(c, 0); n1 = n0 + q
                if n0 == 0 and n1 != 0:                                     # 開新部位
                    cost[c] = [abs(n1), t["price"] * abs(n1)]
                    tag = str(order_tags.get(t["order_no"], ""))
                    by[c] = "F" if tag.startswith("F") else "T" if tag.startswith("T") else ""
                    otyp[c] = str(order_types.get(t["order_no"], "") or "").strip()   # 開倉那張單的委託種類(元三平倉用同一種;判斷是不是賣庫存)
                    t_open[c] = t.get("time", "")                    # 2026-10-29 稽核 #4:開倉成交時間(找最接近的進場通知)
                    if by[c]: self.n_trades += 1
                    self.amount_today += t["price"] * abs(q) * 1000
                elif n0 != 0 and (n0 > 0) == (q > 0):                       # 同方向加碼
                    cost[c][0] += abs(q); cost[c][1] += t["price"] * abs(q); self.amount_today += t["price"] * abs(q) * 1000
                elif n0 != 0:                                               # 平倉
                    self.amount_today += t["price"] * min(abs(q), abs(n0)) * 1000
                    if by.get(c):
                        lots = min(abs(q), abs(n0)); avg = cost[c][1] / cost[c][0]; d = 1 if n0 > 0 else -1
                        self.pnl += (t["price"] - avg) * lots * 1000 * d - self.cost_amt(avg if d > 0 else t["price"], t["price"] if d > 0 else avg, lots)
                net[c] = n1
            open_mine = [c for c, n in net.items() if n != 0 and by.get(c)]
            for w in working:                                       # 強制進場單還掛著、還沒成交(沒有部位)→ 接成「進場中」
                c, tg = w["code"], str(w.get("tag", ""))
                if tg[:1] == "F" and net.get(c, 0) == 0 and c not in self.positions:
                    m = (st.get("forced") or {}).get(c) or {}
                    d = 1 if w["bs"] == "B" else -1
                    p = self.new_pos(c, d, FORCED, "強制部位(接回)", forced=True); p["tp_pct"], p["sl_pct"] = m.get("tp_pct"), m.get("sl_pct")
                    p["order"] = {"no": w["no"], "tag": tg, "purpose": "進場", "side": w["bs"], "want": w["lots"] * 1000, "filled": 0, "cost": 0.0,
                                  "at": time.time(), "price": 0, "typ": w.get("typ", p["typ"]), "flag": "H" if d > 0 else "L", "tif": "0"}
                    self.log(f"[交易] 接回:{c} 強制進場單 {w['no']} 還掛著 → 進場中")
            manual = [c for c, n in net.items() if n != 0 and not by.get(c)]
            if manual and self.y3 and self.enabled:                 # 2026-10-28 元三:今天手動開、還沒平的也接回來(判三管)
                for c in manual:
                    n = net[c]; d = 1 if n > 0 else -1; avg = cost[c][1] / cost[c][0]
                    why = self.y3_skip(d, otyp.get(c, ""), c)
                    if why:
                        self.log(f"[交易] 接回:{c} 手動{'買' if d > 0 else '賣'} {abs(n)} 張 {why},不接管"); continue
                    p = self.new_pos(c, d, MANUAL, "元大手動(接回)", forced=True)
                    if otyp.get(c): p["typ"] = otyp[c]
                    p.update({"qty": abs(n), "entry": avg, "state": HOLDING, "t0": "", "tp_pct": self.y3_tp, "sl_pct": self.y3_sl}); self.set_forced_tpsl(p, avg)
                    self.resume_exiting(p, working)
                    self.sub(p)
                    self.log(f"[交易] 接回元三 {c} {'多' if d > 0 else '空'} {abs(n)} 張 均價 {avg:.2f}(停利 {p['tp']} 停損 {p['sl']})")
            elif manual:
                self.log(f"[交易] 接回:{', '.join(manual)} 有未平部位但不是程式開的(手動),不管")
            if not open_mine:
                self.log(f"[交易] 接回:沒有程式開的未平部位;今天已做 {self.n_trades} 筆、已實現 {self.pnl:+,.0f}")
                return [c for c in manual if c in self.positions] or None
            for c in open_mine:
                n = net[c]; d = 1 if n > 0 else -1; avg = cost[c][1] / cost[c][0]
                if by[c] == "F":
                    m = (st.get("forced") or {}).get(c) or {}
                    p = self.new_pos(c, d, FORCED, "強制部位(接回)", forced=True)
                    p["tp_pct"], p["sl_pct"] = m.get("tp_pct"), m.get("sl_pct")
                    self.set_forced_tpsl(p, avg)
                else:
                    tp = sl = 0.0; title = ""; judge = "接回"; body = ""; exit_it = None
                    # 2026-10-29 稽核 #4:代碼用 code_of 精確比對(原本 `c in title` 子字串,2330 會配到 23301);
                    #   有好幾則時:判別式在 TRADE_JUDGES 的優先,再選通知時間最接近元大開倉成交時間的,都一樣取最新的
                    cands = [(i, it) for i, it in enumerate(signals) if it.get("kind") in ("long", "short") and code_of(it.get("title")) == c and (it["kind"] == "long") == (d > 0)]
                    if cands:
                        m0 = minutes_of(t_open.get(c))
                        def score(x):
                            i, it = x; ms = minutes_of(it.get("t"))
                            return (0 if judge_of(it.get("body")) in self.judges else 1, abs(ms - m0) if (ms is not None and m0 is not None) else 0, -i)
                        i0, it = min(cands, key=score); body = it.get("body", "")
                        m = RE_TPSL.search(body)
                        if m: tp, sl, title = num(m.group(1)), num(m.group(2)), it.get("title", "")
                        judge = judge_of(body) or judge
                        if judge != "接回":                                   # 這則進場通知之後(斷線期間)同一判別式、同一檔已經發過平倉通知 → 接回後馬上平
                            exit_it = next((x for x in signals[i0 + 1:] if x.get("kind") in EXIT_KINDS and code_of(x.get("title")) == c and judge_of(x.get("body")) == judge), None)
                    p = self.new_pos(c, d, judge, title or "接回部位", tp=tp, sl=sl)
                    p["no_max_move"] = "✓破" in body                         # 2026-10-29 稽核 #4:假突破空單接回後一樣不套 ±6.5%
                    if exit_it: p["resume_exit"] = "接回:斷線期間已有平倉通知 " + exit_it.get("title", "")
                p["qty"], p["entry"], p["state"] = abs(n), avg, HOLDING
                p["t0"] = ""
                self.resume_exiting(p, working)
                rx = p.pop("resume_exit", None)
                if rx and p["state"] == HOLDING:
                    self.log(f"[交易] {c} {p['judge']} 程式沒在跑時已經發過平倉通知 → 接回後馬上平倉")
                    self.sub(p); self.exit_pos(p, rx, self.last_px.get(c, avg)); continue
                self.sub(p)
                oco = [x for x in strategies if x.get("code") == c]
                if oco:
                    p["oco_no"] = oco[-1]["no"]; p["oco_live"] = True
                self.log(f"[交易] 接回部位 {c} {p['judge']} {'多' if d > 0 else '空'} {abs(n)} 張 均價 {avg:.2f} 停利 {p['tp'] or '?'} 停損 {p['sl'] or '?'};"
                         f"元大條件單 {'在(' + p['oco_no'] + ')' if oco else '沒有'}")
                if not oco and self.broker_stop and p["tp"] and p["sl"]:
                    p["oco_no"] = None; self.oco_q.append(c)
                    self.gw.place_oco(c, p["mkt"], d, abs(n), p["tp"], p["sl"])
                elif not (p["tp"] and p["sl"]):
                    self.log(f"[交易] !! {c} 找不到停利 / 停損價,只剩 ±{self.max_move}%、手機平倉通知、強平和 13:20 會出場")
            self.log(f"[交易] 接回 {len(open_mine)} 檔;今天已做 {self.n_trades} 筆、已實現 {self.pnl:+,.0f}")
            return open_mine

    def resume_exiting(self, p, working):
        """2026-10-28 獨立檢查 #4 / 平倉檢查 #3:接回時,程式送的平倉方向委託還掛著(例如鎖跌停)→ 接成「平倉中」,不再送第二張(元三也適用)"""
        c, d = p["code"], p["dir"]
        wx = [w for w in working if w["code"] == c and w["bs"] == ("S" if d > 0 else "B") and str(w.get("tag", ""))[:1] in ("T", "F", "R")]
        if not wx:
            return
        w = wx[-1]; p["state"] = EXITING; p["force_limit"] = True
        p["order"] = {"no": w["no"], "tag": w.get("tag", ""), "purpose": "平倉", "side": w["bs"], "want": w["lots"] * 1000, "filled": 0, "cost": 0.0,
                      "at": time.time(), "price": 0, "typ": w.get("typ", p["typ"]), "flag": "H" if d < 0 else "L", "tif": "0"}
        if w["lots"] < p["qty"]:
            p["order"]["want"] = w["lots"] * 1000; p["order"]["partial"] = True     # 掛著的比部位少:成交後剩下的再送
        self.log(f"[交易] 接回:{c} 平倉單 {w['no']} 還掛著({w['lots']} 張)→ 平倉中,等它成交")

    def sub(self, p):
        """平倉檢查 #7:部位的股票補訂閱逐筆(不在名單上也盯得到價);bridge 回傳市場就用它"""
        try:
            m = self.want_sub(p["code"])
            if m: p["mkt"] = self.mkt_of[p["code"]] = str(m)
        except Exception as e:
            self.log(f"[交易] {p['code']} 補訂閱失敗 {e}")

    def y3_skip(self, d, typ, code=""):
        """平倉檢查 #6(2026-10-28 18:57 使用者「13.20 開始 一連串平倉動作 皆以元大庫存為主 讓庫存裡只剩下一檔我以前買過現在已經下市無法刪除的股票」):
           帳戶平常沒有庫存(只有下市的 YY0070),所以手動賣出 = 當沖放空,照接;只排除 TRADE_IGNORE_CODES 裡的股票(下市股)"""
        if code and code in self.ignore:
            return "在 TRADE_IGNORE_CODES(不管的股票)"
        return None

    def reconcile(self, trades, working, tags=None, types=None):
        """2026-10-28 模擬測試(幾百個隨機交易日)找到:斷線 / 重開時漏掉的回報 → 元大有部位、程式不知道(或張數不對),要到 13:20 才會被平。
        盤中每分鐘(和每次重連後)拿元大今天的成交對一次帳;同一個差異連續兩次都在(排除回報還在路上)才處理:
          · 元大有、程式沒有,開倉那筆是手動 → 接成元三(停利 / 停損照判三 %)
          · 元大有、程式沒有,開倉那筆是程式的單(晚到的成交 / 多平反向)→ 馬上漲停 / 跌停平掉
          · 程式有、張數跟元大不一樣(沒有單在跑)→ 以元大為準;元大已經 0 → 結束這個部位"""
        tags = tags or {}; types = types or {}
        with self.lock:
            net, opener, otyp = {}, {}, {}
            for t in sorted(trades, key=lambda x: x.get("time", "")):
                c = t["code"]; n0 = net.get(c, 0); n1 = n0 + t["lots"] * (1 if t["bs"] == "B" else -1)
                if n1 != 0 and (n0 == 0 or (n0 > 0) != (n1 > 0)):
                    opener[c] = str(tags.get(t["order_no"], "") or ""); otyp[c] = str(types.get(t["order_no"], "") or "").strip()
                net[c] = n1
            mytags = {p["order"]["tag"] for p in self.positions.values() if p.get("order")}
            busy = {w["code"] for w in working if w.get("tag") not in mytags}   # 別的(手動)委託還掛著 = 先不動;程式自己的單不算
            seen, self.recon_seen = getattr(self, "recon_seen", {}), {}
            for c in set(net) | set(self.positions):
                if c in self.ignore or c in busy:
                    continue
                n = net.get(c, 0); p = self.positions.get(c)
                if p and p["state"] == EXITING and p.get("order") and not p["order"].get("dead") and p["order"]["purpose"] == "平倉":
                    # 模擬測試 seed 2233:平倉單掛著(例如鎖漲停)時,漏了回報的手動加碼 → 平倉單張數不夠;元大淨部位跟「部位 − 已成交」對不上兩次 → 改張數取消重送
                    o = p["order"]; left = (p["qty"] - o["filled"] // 1000) * p["dir"]
                    if n == left or n == 0 or (n > 0) != (p["dir"] > 0):
                        continue
                    key = ("x", n, left)
                    if seen.get(c) != key:
                        self.recon_seen[c] = key; continue
                    self.log(f"[交易] 對帳:{c} 平倉中,元大還有 {abs(n)} 張、平倉單只剩 {abs(left)} 張 → 改成 {abs(n)} 張重送")
                    p["qty"] = abs(n) + o["filled"] // 1000; self.resize_exit(p, "對帳張數不同"); continue
                if p and p.get("wait_confirm") and p["state"] == ENTERING and p.get("order") and not p["order"].get("filled"):
                    # 2026-10-29 23:55 使用者「現在做」:進場單「等確認」中(IOC 沒收到回報),元大的成交才是準的:連續兩次看到
                    #   元大有同方向的部位 → 當成這張單成交了(張數、均價照元大),走一般進場成交流程(停利 / 停損照通知);元大 0 張 → 確定沒成交,拿掉
                    if n != 0 and (n > 0) != (p["dir"] > 0):
                        continue                                          # 方向相反 = 不是這張單,交給 60 秒逾時 / 下面的規則
                    key = ("w", n)
                    if seen.get(c) != key:
                        self.recon_seen[c] = key; continue
                    o = p["order"]
                    if n == 0:
                        self.log(f"[交易] 對帳:{c} 進場單等確認中,元大連續兩次都沒有這檔部位 → 確定沒成交,拿掉"); p["order"] = None; self.positions.pop(c, None); continue
                    side = "B" if p["dir"] > 0 else "S"; ts = [t for t in trades if t["code"] == c and t["bs"] == side]
                    q = sum(t["lots"] for t in ts); avg = (sum(t["lots"] * t["price"] for t in ts) / q) if q else (self.last_px.get(c) or p["entry"])
                    self.log(f"[交易] 對帳:{c} 進場單等確認中,元大有 {abs(n)} 張 → 當成成交(均價 {avg:.2f}),轉持倉")
                    o["filled"] = abs(n) * 1000; o["cost"] = avg * abs(n) * 1000; o["dead"] = True; self.after_report(p); continue
                if p and (p.get("order") or p["state"] != HOLDING):
                    continue                                              # 程式自己的單還在跑,以它為準
                mine = p["qty"] * p["dir"] if p else 0
                if mine == n:
                    continue
                key = (n, mine)
                if seen.get(c) != key:
                    self.recon_seen[c] = key; continue                    # 第一次看到:可能回報還在路上,下一分鐘再確認
                if p:
                    if n == 0 or (n > 0) != (p["dir"] > 0):
                        self.log(f"[交易] 對帳:{c} 元大已經沒有這個部位(程式記 {mine})→ 結束"); p["order"] = None; self.positions.pop(c, None)
                        if n == 0: continue
                    else:
                        self.log(f"[交易] 對帳:{c} 程式記 {abs(mine)} 張、元大 {abs(n)} 張 → 以元大為準"); p["qty"] = abs(n); continue
                d = 1 if n > 0 else -1; px = self.last_px.get(c) or 0
                if opener.get(c, "")[:1] in ("T", "F", "R"):
                    self.log(f"[交易] !! 對帳:{c} 元大有 {abs(n)} 張{'多' if d > 0 else '空'}單、程式不知道(程式的單晚到 / 多平)→ 馬上平掉")
                    self.alert(f"⚠ {c} 對帳發現 {abs(n)} 張沒管到", f"程式的單晚到成交 / 多平,已{'跌停賣出' if d > 0 else '漲停買回'}")
                    self.rebuy(c, d, abs(n), px or 0, None, otyp.get(c) or None)
                elif self.y3 and self.enabled:
                    p = self.new_pos(c, d, MANUAL, "元大手動(對帳接回)", forced=True)
                    if otyp.get(c): p["typ"] = otyp[c]
                    avg = px
                    p.update({"qty": abs(n), "entry": avg, "state": HOLDING, "t0": self.hm(), "tp_pct": self.y3_tp, "sl_pct": self.y3_sl})
                    cs = [t for t in trades if t["code"] == c and t["bs"] == ("B" if d > 0 else "S")]
                    if cs: p["entry"] = sum(t["price"] * t["lots"] for t in cs) / sum(t["lots"] for t in cs)
                    self.set_forced_tpsl(p, p["entry"]); self.sub(p)
                    self.log(f"[交易] 對帳:{c} 元大有手動 {abs(n)} 張{'多' if d > 0 else '空'}單、程式沒收到回報 → 接成元三(均價 {p['entry']:.2f})")
                    self.alert(f"⚠ {c} 對帳接回元三 {abs(n)} 張", "斷線時漏了回報,程式已接手(停利 / 停損照判三)")

    def prepare_close_all(self):
        """13:20 取消全部掛單的第一步:刪程式自己的條件單、取消還在等的委託;部位以之後查到的元大成交回報為準"""
        with self.lock:
            for p in self.positions.values():
                o = p.get("order")
                if p.get("oco_live"):
                    self.gw.delete_oco(p["oco_no"]); p["oco_live"] = False; self.log(f"[交易] 13:20 取消:刪除元大條件單 {p['oco_no']}")
                if o and not o.get("dead") and o.get("no"):
                    self.gw.cancel(o["no"], p["code"], o["side"], o["typ"]); self.log(f"[交易] 13:20 取消:{p['code']} {o['purpose']}委託 {o['no']}")
                p["order"] = None
                p["force_limit"] = True

    def recheck(self, trades, working=(), at=None):
        """收盤前複查:trades = 元大今天的成交回報(同 resume);working = 還掛著沒成交的委託 [{code, bs, lots}]。
        回傳還沒沖銷、而且掛著的委託也不夠平掉的 [(code, 淨張數)]"""
        at = at or self.recheck_at
        with self.lock:
            net = {}
            for t in trades:
                net[t["code"]] = net.get(t["code"], 0) + t["lots"] * (1 if t["bs"] == "B" else -1)
            for c, p in list(self.positions.items()):
                o = p.get("order")
                if net.get(c, 0) == 0 and not (o and not o.get("dead")):
                    self.log(f"[交易] {at} 複查:元大顯示 {c} 已經沒有部位 → 程式這邊結束"); del self.positions[c]
            left = []
            for c, n in net.items():
                if n == 0 or c in self.ignore:
                    continue
                d = 1 if n > 0 else -1
                pend = sum(w["lots"] for w in working if w["code"] == c and w["bs"] == ("B" if d < 0 else "S"))   # 已經掛著的平倉方向委託
                if pend >= abs(n):
                    self.log(f"[交易] {at} 複查:{c} 未沖銷 {abs(n)} 張,已有 {pend} 張平倉委託掛著,不重複下單"); continue
                left.append((c, n, abs(n) - pend))
            if not left:
                self.log(f"[交易] {at} 複查:沒有需要補單的未沖銷股票"); return []
            for c, n, need in left:
                d = 1 if n > 0 else -1; what = f"{c} {'多' if d > 0 else '空'} {abs(n)} 張"
                p = self.positions.get(c)
                if p:                                                      # 程式的部位
                    if p["qty"] != abs(n):
                        self.log(f"[交易] 複查:{c} 程式記 {p['qty']} 張、元大 {abs(n)} 張 → 以元大為準"); p["qty"] = abs(n)
                    o = p.get("order")
                    if p["state"] in (ENTERING, HOLDING):
                        if p["state"] == ENTERING:
                            p["entry"] = p["entry"] or self.last_px.get(c, 0); p["state"] = HOLDING; p["order"] = None
                        self.exit_pos(p, f"{at} 平倉", self.last_px.get(c, p["entry"]), force_limit=True)
                    elif p["state"] == EXITING and o and not o.get("dead"):
                        self.log(f"[交易] {at} 複查:{c} 程式的平倉單還在送 / 等回報,不重複")
                    else:
                        p["order"] = None; p["state"] = EXITING; p["force_limit"] = True; p["qty"] = need; self.send_exit(p, need, self.last_px.get(c, p["entry"]))
                    self.log(f"[交易] {at} 複查:程式的 {what} 還沒平完,已掛漲停 / 跌停 ROD")
                    self.alert(f"⚠ {at} 還有未沖銷 {what}", "程式已掛漲停 / 跌停 ROD,13:30 前會成交(鎖漲停 / 跌停除外)")
                    continue
                close = self.recheck_close == "all" or (self.recheck_close == "short" and d < 0)
                if close:
                    tag = f"R{self.now().strftime('%H%M%S')}{c}"[:32]
                    typ = self.type_short if d < 0 else self.type_long
                    self.gw.send(c, self.mkt_of.get(c, "TWSE"), "B" if d < 0 else "S", need, 0, typ, "0", tag, "H" if d < 0 else "L")
                    self.log(f"[交易] {at} 複查:{what} 不是程式開的(手動),已掛{'漲停買回' if d < 0 else '跌停賣出'} {need} 張 ROD")
                    self.alert(f"⚠ {at} 手動部位未沖銷 {what}", f"程式已掛{'漲停買回' if d < 0 else '跌停賣出'} {need} 張 ROD")
                else:
                    self.log(f"[交易] {at} 複查:{what} 不是程式開的(手動),只提醒不處理")
                    self.alert(f"⚠ {at} 手動部位未沖銷 {what}", "程式沒有處理,請在投資先生確認")
            return [(c, n) for c, n, _ in left]

    # ── 外部事件 ──
    def on_tick(self, code, price):
        self.last_px[code] = price
        with self.lock:
            p = self.positions.get(code)
            if not p or p["state"] != HOLDING:
                return
            if self.hm() < "09:00":                                   # 2026-10-28 開盤前的試撮價不算
                return
            d = p["dir"]
            hit_tp = p["tp"] > 0 and ((price >= p["tp"]) if d > 0 else (price <= p["tp"]))
            hit_sl = p["sl"] > 0 and ((price <= p["sl"]) if d > 0 else (price >= p["sl"]))
            ref = p.get("ref") or self.ref_of(code) or 0             # 平倉檢查 #10:強三先用手機帶來的昨收(電腦的可能是前幾天留下的)
            if not ref and not p.get("ref_warned"):
                p["ref_warned"] = True; self.log(f"[交易] !! {code} 沒有昨收,±{self.max_move:g}% 保護這檔暫時沒作用")
                if p.get("forced"): self.alert(f"⚠ {code} 沒有昨收", f"±{self.max_move:g}% 保護暫時沒作用,停利 / 停損 / 13:20 照常")
            hit_mv = not p.get("no_max_move") and ref > 0 and self.max_move > 0 and ((price <= ref * (1 - self.max_move / 100)) if d > 0 else (price >= ref * (1 + self.max_move / 100)))
            if not (hit_tp or hit_sl or hit_mv):
                return
            why = f"超{self.max_move:g}%" if hit_mv else "停利" if hit_tp else "停損"
            if p.get("oco_live") and not hit_mv:
                if not p.get("oco_noted"):
                    p["oco_noted"] = True; self.log(f"[交易] {code} 價格 {price} 碰到{why},由元大條件單(OCO)平倉")
                return
            self.exit_pos(p, why, price, force_limit=True)
            if hit_mv:
                self.alert(f"⚠ {code} 離昨收 {'−' if d > 0 else '+'}{self.max_move:g}% 系統強制平倉", f"{'做多跌停賣出' if d > 0 else '做空漲停買回'} {p['qty']} 張")

    def find_order(self, order_no, basket):
        b, n = str(basket or "").strip(), str(order_no or "").strip()
        for p in self.positions.values():
            o = p.get("order")
            if o and ((b and b == o["tag"]) or (o["no"] and n and n == o["no"])):
                return p
        return None

    def on_report(self, rpt_type, order_no, status, qty_shares, price, basket="", code="", bs="", typ=""):
        """元大即時回報:rpt_type 50 委託、51 成交;status 見文件(1 失敗、2 取消成功、24 失效、25 價穩失效…)
           用自訂編號 basket(下單時帶的 BasketNo)或委託書號對;委託書號可能比回報晚到,所以以 basket 為主"""
        with self.lock:
            p = self.find_order(order_no, basket)
            if not p:
                if rpt_type == 51 and qty_shares > 0 and code:
                    if str(basket or "").strip()[:1] in ("T", "F", "R"):   # 2026-10-28 獨立檢查 #5 #6:程式自己的舊單(已結束 / 已取消)晚到的成交 → 只記錄,不當成別人的平倉
                        p2 = self.positions.get(str(code).strip())
                        if p2 and p2.get("entry_tag") and p2["entry_tag"] == str(basket).strip() and p2["state"] in (HOLDING, EXITING):
                            # 2026-10-29 稽核 #5:IOC 進場已部分成交轉持倉後,同一張進場單剩下的晚到成交 → 併進部位(張數、均價)
                            lots = qty_shares // 1000; tot = p2["qty"] + lots
                            p2["entry"] = (p2["entry"] * p2["qty"] + price * lots) / tot if tot else p2["entry"]; p2["qty"] = tot; self.amount_today += price * lots * 1000
                            self.log(f"[交易] {p2['code']} 進場單({p2['entry_tag']})晚到的成交 {lots} 張 @ {price} → 併進部位,共 {tot} 張 均價 {p2['entry']:.2f}")
                            if p2["state"] == EXITING:
                                self.resize_exit(p2, "平倉中又收到進場單晚到的成交")
                            return
                        self.log(f"[交易] {code} 程式舊委託({str(basket).strip()})晚到的成交 {qty_shares // 1000} 張 @ {price},只記錄(13:22 複查以元大為準)")
                        return
                    self.on_other_fill(str(code).strip(), bs, qty_shares, price, str(order_no or "").strip(), typ)
                return
            o = p["order"]
            if order_no and str(order_no).strip() and basket and str(basket).strip() == o["tag"]:
                o["no"] = str(order_no).strip()                 # 回報(用自訂編號對上的)的委託書號最準,蓋掉下單回應給的
            elif not o["no"] and order_no:
                o["no"] = str(order_no).strip()
            self.pending_cancel(p)
            if rpt_type == 51 and qty_shares > 0:
                o["filled"] += qty_shares; o["cost"] += qty_shares * price
                self.log(f"[交易] 成交 {p['code']} {o['purpose']} {o['side']} {qty_shares // 1000} 張 @ {price}(累計 {o['filled'] // 1000}/{o['want'] // 1000})")
            elif rpt_type == 50 and status in (1, 2, 24, 25):
                o["dead"] = True
                self.log(f"[交易] {p['code']} 委託結束 狀態 {status}({ {1: '委託失敗', 2: '取消成功', 24: '委託失效', 25: '價穩失效'}[status] })")
            self.after_report(p)

    def on_other_fill(self, code, bs, qty_shares, price, order_no="", typ=""):
        """不是這支程式送的單成交了:手動開新部位(元三)、手動加碼、手動 / 條件單平倉,或程式平倉時條件單也同時觸發(多平了)"""
        p = self.positions.get(code)
        close_side = lambda d: "B" if d < 0 else "S"
        lots = qty_shares // 1000
        if p and p.get("judge") == MANUAL and bs != close_side(p["dir"]) and p["state"] in (HOLDING, EXITING):
            # 平倉檢查 #5:元三同方向再成交(同一張分批成交,或另外下一張加碼)→ 併進去、成本平均,停利 / 停損照新均價
            tot = p["qty"] + lots
            p["entry"] = (p["entry"] * p["qty"] + price * lots) / tot; p["qty"] = tot; self.amount_today += price * lots * 1000
            self.set_forced_tpsl(p, p["entry"]); self.log(f"[交易] 元三 {code} 同方向再成交 {lots} 張(委託 {order_no})→ 共 {tot} 張 均價 {p['entry']:.2f}")
            if p["state"] == EXITING:
                self.resize_exit(p, "元三平倉中又加碼")                 # 平倉單張數不夠了 → 取消重送全部
            return
        if p and bs != close_side(p["dir"]):
            self.log(f"[交易] !! {code} 程式已有部位({p['judge']}),又有一筆不是程式下的同方向成交 {lots} 張 @ {price} → 不合併,請自己處理")
            self.alert(f"⚠ {code} 手動同方向成交 {lots} 張", f"程式已有這檔的部位({p['judge']}),手動這 {lots} 張沒有交給程式管,請自己處理(13:22 複查會以元大為準平掉)")
            return
        if not p and self.y3 and self.enabled and lots > 0 and self.hm() >= "09:00":
            if self.broker_stop and self.flat_watch.get(code) and time.time() < self.flat_watch[code]["until"] and bs == close_side(self.flat_watch[code]["dir"]):
                pass                                                # 有掛條件單時,剛平倉又來的同方向平倉成交 = 多平(下面處理)
            else:
                d = 1 if bs == "B" else -1
                why = self.y3_skip(d, typ, code)
                if why:
                    self.log(f"[交易] {code} 手動{'買' if d > 0 else '賣'} {lots} 張 @ {price}:{why},不接成元三")
                    return
                p = self.new_pos(code, d, MANUAL, f"元大手動{'做多' if d > 0 else '做空'} {code}", forced=True)
                if str(typ or "").strip(): p["typ"] = str(typ).strip()      # 平倉用跟開倉同一種委託種類
                p.update({"qty": lots, "entry": price, "state": HOLDING, "t0": self.hm(), "tp_pct": self.y3_tp, "sl_pct": self.y3_sl, "y3_no": order_no})
                self.set_forced_tpsl(p, price); self.amount_today += price * lots * 1000; self.save_state()
                self.sub(p)
                self.log(f"[交易] 元三:元大手動{'買進' if d > 0 else '賣出'} {code} {lots} 張 @ {price}(委託種類 {typ or '?'})→ 交給判三管(停利 {p['tp']} 停損 {p['sl']})")
                self.post(f"{self.name_of(code)} {code} {'做多' if d > 0 else '做空'}進場 {price:.2f}",
                          f"{p['t0']} 元大 · 停利 {p['tp']:.2f} 停損 {p['sl']:.2f} · {lots}張 · {MANUAL}", "long" if d > 0 else "short")
                return
        if p and p["state"] in (HOLDING, EXITING) and bs == close_side(p["dir"]):
            take = min(lots, max(p["qty"], 0)); extra = lots - take
            self.log(f"[交易] {code} 不是程式送的平倉單成交 {lots} 張 @ {price}(元大條件單 / 手動)")
            p["exit_why"] = p.get("exit_why") or "元大條件單 / 手動"
            self.book_close(p, take, price)
            if extra > 0:
                self.alert(f"⚠ {code} 手動平倉多了 {extra} 張", f"程式記 {take} 張,手動平了 {lots} 張,多出來的 {extra} 張變反向部位,請自己處理")
            o = p.get("order")
            if p["state"] == EXITING and o and not o.get("dead"):
                # 平倉檢查 #1:程式自己的平倉單也還在 → 取消;取消回來後照剩下的張數重送(剩 0 就結束);它若先成交,多出來的會自動反向補回
                self.resize_exit(p, "手動先平了一部分" if p["qty"] > 0 else "手動已經平完")
                return
            if p["qty"] <= 0:
                p["order"] = None; self.finish_pos(p)
            return
        fw = self.flat_watch.get(code) if self.broker_stop else None   # 多平補回只在有掛元大條件單時才需要(獨立檢查 #5)
        if fw and bs == close_side(fw["dir"]) and time.time() < fw["until"] and code not in self.positions:
            lots = qty_shares // 1000
            self.log(f"[交易] !! {code} 已經平倉了又有 {lots} 張平倉成交(條件單同時觸發)→ 馬上反向補回")
            self.rebuy(code, -fw["dir"], lots, price, fw["mkt"], fw["typ"])

    def pending_cancel(self, p):
        """二次檢查 A:要取消的單(手動先平 / 加碼 shrink、進場中按強平 cancel_after)委託書號一知道就馬上取消(回報、下單回應、30 秒複查哪個先到都算)"""
        o = p.get("order")
        if o and o.get("no") and not o.get("dead") and (o.get("shrink") or o.get("cancel_after")) and not o.get("cancel_sent"):
            o["cancel_sent"] = True; self.gw.cancel(o["no"], p["code"], o["side"], o["typ"])
            self.log(f"[交易] {p['code']} 委託書號 {o['no']} 回來了 → 取消({'張數要改' if o.get('shrink') else '進場中按了強平'})")

    def resize_exit(self, p, why):
        """平倉單張數跟部位對不上了(手動先平一部分 / 元三又加碼)→ 取消程式的平倉單;取消回報回來時 after_report 照剩下的張數重送(不算重送次數)"""
        o = p.get("order")
        if not o or o.get("dead"):
            return
        o["shrink"] = True
        if o.get("no") and not o.get("cancel_sent"):
            o["cancel_sent"] = True; self.gw.cancel(o["no"], p["code"], o["side"], o["typ"])
        self.log(f"[交易] {p['code']} {why} → 取消程式的平倉單{' ' + o['no'] if o.get('no') else '(等委託書號回來再取消)'},之後照剩下 {max(p['qty'], 0)} 張處理")

    def rebuy(self, code, d, lots, price, mkt=None, typ=None):
        """多平了 → 反向的張數馬上用漲停 / 跌停平掉(補回)"""
        p = self.new_pos(code, d, "補回", "多平補回", forced=True)
        p.update({"qty": lots, "entry": price, "state": HOLDING, "t0": self.hm()})
        if mkt: p["mkt"] = mkt
        if typ: p["typ"] = typ
        self.exit_pos(p, "多平補回", price, force_limit=True)

    def cost_amt(self, buy, sell, lots):
        """2026-10-28 18:29 使用者「改」(對元大明細 10/28 −3,371):手續費 = 每邊 成交金額 × 0.1425% × 2.5 折 無條件捨去(沒另收證手費),
           證交稅 = 賣出金額 × 0.15% 無條件捨去;折扣用 TRADE_FEE_DISCOUNT(沒設 = 0.25,原 0.3)"""
        r = 0.001425 * float(self.cfg.get("TRADE_FEE_DISCOUNT", "0.25"))
        return math.floor(buy * lots * 1000 * r + 1e-6) + math.floor(sell * lots * 1000 * r + 1e-6) + math.floor(sell * lots * 1000 * 0.0015 + 1e-6)   # +1e-6:浮點誤差

    def book_close(self, p, lots, avg):
        if not lots:
            return
        pnl = (avg - p["entry"]) * lots * 1000 * p["dir"]
        net = pnl - (self.cost_amt(p["entry"], avg, lots) if p["dir"] > 0 else self.cost_amt(avg, p["entry"], lots)); self.pnl += net
        self.amount_today += avg * lots * 1000                  # 2026-10-27 今天成交金額(買 + 賣)
        p["qty"] -= lots; p["closed_lots"] += lots; p["closed_cost"] += avg * lots
        self.journal.append({"code": p["code"], "dir": p["dir"], "entry": p["entry"], "exit": avg, "lots": lots, "net": round(net),
                             "why": p.get("exit_why"), "judge": p.get("judge"), "forced_close": bool(p.get("forced_close"))})
        self.log(f"[交易] 平倉 {p['code']} {lots} 張 @ {avg:.2f} 淨 {net:+,.0f}(今天累計 {self.pnl:+,.0f})")

    def finish_pos(self, p):
        c = p["code"]
        self.flat_watch[c] = {"mkt": p["mkt"], "dir": p["dir"], "typ": p["typ"], "until": time.time() + 120}
        self.positions.pop(c, None)
        # 2026-10-28 強三的出場、任何部位的強平 → 寫一則平倉訊息到手機訊息欄(判一 / 二 / 三自己的平倉訊息照舊由手機發)
        if (p.get("forced") or p.get("forced_close")) and p.get("judge") in ("判一", "判二", "判三", FORCED, MANUAL) and p["closed_lots"]:
            ex = p["closed_cost"] / p["closed_lots"]; d = p["dir"]
            pl = (ex - p["entry"]) / p["entry"] * 100 * d if p["entry"] else 0
            why = "強平" if p.get("forced_close") else (p.get("exit_why") or "出")
            body = f"{self.hm()} {'做多' if d > 0 else '做空'} {p.get('t0') or self.hm()} 進 {p['entry']:.2f} → 現價 {ex:.2f} · {p['closed_lots']}張" + (" · 強平" if p.get("forced_close") else "") + f" · {p['judge']}"
            self.post(f"{self.name_of(c)} {c} 出場 {'+' if pl >= 0 else '−'}{abs(pl):.1f}%:{why}", body, "exit")

    def on_strategy_reply(self, strategy_no, ok, msg):
        """元大新增條件單回應(只有 TRADE_BROKER_STOP=1 才會掛)"""
        with self.lock:
            c = self.oco_q.pop(0) if self.oco_q else None
            p = self.positions.get(c) if c else None
            if not p or p.get("oco_no") is not None:
                return
            if ok and strategy_no:
                p["oco_no"] = str(strategy_no).strip(); p["oco_live"] = True
                self.log(f"[交易] {c} 元大條件單(OCO)已掛:策略編號 {p['oco_no']}(停利 {p['tp']} / 停損 {p['sl']})")
            else:
                p["oco_no"] = ""; p["oco_live"] = False
                self.log(f"[交易] !! {c} 元大條件單掛失敗:{msg} → 改由程式自己盯停利停損")

    def on_order_reply(self, order_no, ok, msg, tag=None):
        """元大下單回應。tag = 這筆回應對應的自訂編號(bridge 照送單順序對好;取消單是空字串);沒給就找最近一張還沒拿到委託書號的單"""
        with self.lock:
            if tag is not None:
                p = self.find_order("", tag) if tag else None
            else:
                cand = [x for x in self.positions.values() if x.get("order") and not x["order"].get("no") and not x["order"].get("dead")]
                p = max(cand, key=lambda x: x["order"]["at"]) if cand else None
            c = p["code"] if p else None
            o = p.get("order") if p else None
            if not o or o.get("no") or o.get("dead"):
                return
            if ok:
                o["no"] = str(order_no).strip()
                self.pending_cancel(p)
                return
            lots = o["want"] // 1000
            # 2026-10-27 22:05 使用者:元大回的原因跟金額 / 額度有關才退 1 張再掛;其他原因(沒券源、不能放空、沒當沖資格、處置股…)張數再少也沒用 → 直接放棄
            amt = amount_reject(msg)
            if o["purpose"] == "進場" and lots > 1 and p["state"] == ENTERING and amt:
                self.log(f"[交易] {c} 委託被拒:{msg} → 額度 / 金額問題,退 1 張,改掛 {lots - 1} 張")
                self.send(p, "進場", o["side"], lots - 1, o["price"], o["typ"], o.get("flag", " "), o.get("tif", "3"))
                return
            o["dead"] = True; self.log(f"[交易] {c} 委託被拒:{msg}" + ("" if amt or o["purpose"] != "進場" else "(不是額度 / 金額問題,不退張數重掛,這個訊號放棄)"))
            if p.get("forced") and o["purpose"] == "進場":
                self.alert(f"⚠ 強制進場 {c} 被元大拒絕", str(msg))
            if o["purpose"] == "平倉":
                self.alert(f"⚠ {c} 平倉單被元大拒絕", f"{msg} · 會自動重送(最多 {self.max_resend} 次)")
            self.after_report(p)

    # ── 每秒呼叫一次 ──
    def step(self, signals=None):
        with self.lock:
            hm = self.hm()
            fe = self.force_exit
            if self.cancel_all:                                   # 13:20 由 bridge 先取消全部掛單再平倉;這裡晚 1 分鐘當備援
                h, m = map(int, fe.split(":")); fe = f"{h + (m + 1) // 60:02d}:{(m + 1) % 60:02d}"
            for p in list(self.positions.values()):
                if p["state"] == HOLDING and hm >= fe:
                    self.exit_pos(p, f"{self.force_exit} 強制平倉", self.last_px.get(p["code"], p["entry"]), force_limit=True)
                o = p.get("order")
                if o and not o.get("dead") and o.get("tif") != "0" and time.time() - o["at"] > self.fill_wait:
                    if o.get("no"):
                        self.log(f"[交易] {p['code']} {o['purpose']} 委託 {self.fill_wait:.0f} 秒沒成交完,取消剩餘"); self.gw.cancel(o["no"], p["code"], o["side"], o["typ"])
                    if o["purpose"] == "進場":
                        o["timeout"] = True                            # 2026-10-29 稽核 #5:是程式自己等到逾時(不是元大回報結束)→ after_report 改「等確認」
                    o["dead"] = True; self.after_report(p)
                if p.get("wait_confirm") and p["state"] == ENTERING and time.time() - p["wait_confirm"] > self.confirm_wait:
                    oo = p.get("order")
                    if not (oo and oo.get("filled")):
                        self.log(f"[交易] {p['code']} 進場單等確認 {self.confirm_wait:.0f} 秒還是沒有成交回報 → 確定沒成交,放棄(對帳 / 13:22 複查仍以元大為準)")
                        p["order"] = None; self.positions.pop(p["code"], None)
            if signals is not None and not self.primed:               # 2026-10-29 稽核 #1:啟動時沒讀到通知 → 第一次讀成功的這批先當「啟動時已有」,不拿來進場
                self.prime(signals); signals = None
            for it in (signals or []):
                self.on_signal(it)

    def need_verify(self):
        """有漲停 / 跌停單(ROD)送出超過 30 秒還沒成交完 → bridge 要查元大(查詢有次數限制,同一張單 30 秒最多查一次)"""
        with self.lock:
            now = time.time()
            for p in self.positions.values():
                o = p.get("order")
                if o and not o.get("dead") and o.get("tif") == "0" and now - o["at"] > self.verify_wait and now - o.get("vat", 0) > self.verify_wait:
                    return True
            return False

    def verify(self, working, trades, tags=None):
        """2026-10-28 使用者:30 秒沒成交 → 查元大:單子還掛著(未成交 / 部分成交)→ 再等;元大沒有這張單(沒送到 / 被拒)→ 再掛一次。
        working = 還掛著的委託 [{no, tag, code, bs, lots}];trades = 今天的成交 [{code, bs, lots, price, order_no}]。
        只看「這張單自己」的成交(委託書號對得上的),不拿整檔淨部位算 —— 同一檔的手動交易不會被算進來(獨立檢查 #1 #2)"""
        with self.lock:
            now = time.time()
            for c, p in list(self.positions.items()):
                o = p.get("order")
                if not o or o.get("dead") or o.get("tif") != "0" or now - o["at"] <= self.verify_wait or now - o.get("vat", 0) <= self.verify_wait:
                    continue
                o["vat"] = now
                ws = [w for w in working if (o["no"] and w.get("no") == o["no"]) or (w.get("tag") and w.get("tag") == o["tag"])]
                alive = bool(ws)
                if ws and not o["no"] and ws[0].get("no"):
                    o["no"] = str(ws[0]["no"]).strip(); self.log(f"[交易] {c} 下單回應沒回來,從元大委託清單拿到委託書號 {o['no']}")
                    self.pending_cancel(p)
                if alive:
                    self.log(f"[交易] {c} {o['purpose']}單送出 {now - o['at']:.0f} 秒:元大顯示還掛著沒成交完(鎖漲跌停 / 暫緩撮合?),繼續等")
                    continue
                # 這張單在元大的成交:委託書號對得上的,或(委託書號沒回來時)用自訂編號對(平倉檢查 #2)
                tags = tags or {}
                mine = [t for t in trades if (o["no"] and str(t.get("order_no", "")).strip() == o["no"]) or (o.get("tag") and tags.get(str(t.get("order_no", "")).strip()) == o["tag"])]
                f_lots = sum(t["lots"] for t in mine) if mine else o["filled"] // 1000
                f_cost = sum(t["price"] * t["lots"] for t in mine) * 1000 if mine else o["cost"]
                if not mine and not o["no"] and o["purpose"] == "平倉" and not tags:
                    # 委託書號、自訂編號都對不到(斷線掉了回應)→ 不盲目重送:看元大這檔的淨部位還剩多少
                    net = sum(t["lots"] * (1 if t["bs"] == "B" else -1) for t in trades if t["code"] == c)
                    have = abs(net) if net and (net > 0) == (p["dir"] > 0) else 0
                    gone = max(0, min(o["want"] // 1000, p["qty"] - have))
                    if gone * 1000 > o["filled"]:
                        cs = [t for t in trades if t["code"] == c and t["bs"] == o["side"]][-gone:] if gone else []
                        f_lots = gone; f_cost = (sum(t["price"] * t["lots"] for t in cs) / max(1, sum(t["lots"] for t in cs))) * gone * 1000 if cs else self.last_px.get(c, p["entry"]) * gone * 1000
                        self.log(f"[交易] {c} 平倉單對不到委託書號,元大這檔淨部位剩 {have} 張 → 當作已平 {gone} 張")
                if f_lots * 1000 > o["filled"]:
                    self.log(f"[交易] {c} 元大顯示這張{o['purpose']}單已成交 {f_lots} 張(回報只收到 {o['filled'] // 1000} 張)→ 照元大")
                    o["filled"], o["cost"] = f_lots * 1000, f_cost
                o["dead"] = True                                    # 元大沒有在掛了:這張單結束,照成交多少處理
                if o["purpose"] == "平倉":
                    want = o["want"] // 1000
                    if f_lots < want:
                        p["resend"] = p.get("resend", 0) + 1
                        self.log(f"[交易] !! {c} 平倉單元大那邊沒在掛了,只成交 {f_lots}/{want} 張(沒送到 / 被拒)→ 剩下的重送(第 {p['resend']} 次)")
                        self.alert(f"⚠ {c} 平倉單重送", f"30 秒沒成交完、元大查不到這張單 → 重送 {want - f_lots} 張{'漲停買回' if p['dir'] < 0 else '跌停賣出'}")
                    self.after_report(p)                            # 記成交的部分;還有剩 → after_report 會再送(有次數上限)
                else:                                               # 強制進場(漲停 / 跌停 ROD)
                    if f_lots == 0:
                        self.log(f"[交易] !! {c} 強制進場單元大那邊沒有、也沒成交 → 不重送(避免重複進場),請確認")
                        self.alert(f"⚠ 強制進場 {c} 沒有送到元大", "元大查不到這張單、也沒有成交;沒有自動重送,要進場請再長按一次")
                        p["order"] = None; self.positions.pop(c, None)
                    else:
                        self.log(f"[交易] {c} 強制進場:元大顯示成交 {f_lots} 張 → 照元大接上")
                        self.after_report(p)

    # ── 手機的強制指令 ──
    def set_forced_tpsl(self, p, avg):
        d = p["dir"]; tpp, slp = p.get("tp_pct"), p.get("sl_pct")
        p["tp"] = round(avg * (1 + float(tpp) / 100 * d), 2) if tpp else 0.0
        p["sl"] = round(avg * (1 - float(slp) / 100 * d), 2) if slp else 0.0

    def force_enter(self, code, d, lots, tp_pct=None, sl_pct=None, mkt=None, cmd_id=None, ref=None):
        """2026-10-28 強制進場:做多掛漲停買、做空掛跌停賣(ROD,一定成交);不佔 5 檔名額、不受虧損上限 / STOP 檔限制。回傳 (成功?, 訊息)"""
        with self.lock:
            if cmd_id and cmd_id in self.cmd_seen:
                return self.cmd_seen[cmd_id]
            lots = int(lots or 0)
            if not self.enabled:
                r = (False, "電腦的自動交易沒有開(TRADE_ENABLE / TRADE_ALLOW_PROD)")
            elif self.hm() >= self.last_entry:                         # 2026-10-29 稽核 #8:強制進場原本沒有時間限制(13:20 前一刻進場馬上又被平)
                r = (False, f"已過最後進場時間 {self.last_entry}(TRADE_LAST_ENTRY),不接受強制進場")
            elif code in self.positions:
                r = (False, "這檔已經有部位(只能強制平倉)")
            elif d not in (1, -1) or lots < 1:
                r = (False, "方向或張數不對")
            else:
                if mkt: self.mkt_of[code] = mkt
                p = self.new_pos(code, d, FORCED, f"強制{'做多' if d > 0 else '做空'} {code}", forced=True)
                p["tp_pct"], p["sl_pct"] = tp_pct, sl_pct
                try: p["ref"] = float(ref) if ref else None          # 手機帶來的昨收(電腦這檔沒有昨收時 ±6.5% 用)
                except Exception: p["ref"] = None
                self.send(p, "進場", "B" if d > 0 else "S", lots, 0, p["typ"], "H" if d > 0 else "L", "0")
                self.save_state()
                self.log(f"[交易] 強制進場 {code} {'做多(漲停買)' if d > 0 else '做空(跌停賣)'} {lots} 張")
                r = (True, f"已送出強制{'做多' if d > 0 else '做空'} {lots} 張")
            if cmd_id: self.cmd_seen[cmd_id] = r
            return r

    def force_close(self, code, cmd_id=None):
        """2026-10-28 強制平倉:全部平倉,做多跌停賣、做空漲停買;之後今天這檔判一 / 二 / 三不再自動進場"""
        with self.lock:
            if cmd_id and cmd_id in self.cmd_seen:
                return self.cmd_seen[cmd_id]
            p = self.positions.get(code)
            if not self.enabled:
                r = (False, "電腦的自動交易沒有開(TRADE_ENABLE / TRADE_ALLOW_PROD)")
            elif not p:
                r = (False, "程式這邊沒有這檔的部位")
            elif p["state"] == ENTERING:
                # 平倉檢查:進場單還沒成交完 → 取消剩下的,已成交的部分一成交就馬上平掉
                p["forced_close"] = True; p["close_after_entry"] = True; self.locked.add(code); self.save_state()
                o = p.get("order")
                if o and not o.get("dead"):
                    if o.get("no") and not o.get("cancel_sent"): o["cancel_sent"] = True; self.gw.cancel(o["no"], code, o["side"], o["typ"])
                    else: o["cancel_after"] = True
                r = (True, "進場單取消中,已成交的會馬上平倉")
            elif p["state"] == EXITING:
                p["forced_close"] = True; self.locked.add(code); self.save_state()
                o = p.get("order")
                if not o or o.get("dead") or p.get("stuck"):
                    # 平倉檢查 #4:重送 3 次失敗(stuck)/ 沒有在掛的平倉單 → 強平重新送出,次數歸零
                    p["stuck"] = False; p["resend"] = 0; p["order"] = None; p["force_limit"] = True
                    self.send_exit(p, p["qty"], self.last_px.get(code, p["entry"]))
                    r = (True, f"已重新送出強制平倉 {p['qty']} 張")
                else:
                    r = (True, "已經在平倉中")
            else:
                p["forced_close"] = True; self.locked.add(code)
                self.exit_pos(p, "強平", self.last_px.get(code, p["entry"]), force_limit=True)
                self.save_state()
                r = (True, f"已送出強制平倉 {p['qty']} 張")
            if cmd_id: self.cmd_seen[cmd_id] = r
            return r

    def positions_view(self):
        with self.lock:
            return [{"code": p["code"], "dir": p["dir"], "qty": p["qty"], "entry": round(p["entry"], 2), "judge": p["judge"], "state": p["state"],
                     "tp": p["tp"], "sl": p["sl"], "t0": p.get("t0", "")} for p in self.positions.values()]

    # ── 訊號 ──
    def exit_match(self, p, judge):
        """平倉通知對不對得上這個部位:同一份判別式(強三 / 元三 跟判三);
        2026-10-29 稽核 #6:沒有判別式的平倉通知只給「接回」(不知道判別式)的部位用,免得把另一份判別式剛開的新部位平掉"""
        if judge is None:
            return p["judge"] == "接回"
        return judge == p["judge"] or p["judge"] == "接回" or (p["judge"] in (FORCED, MANUAL) and judge == "判三")

    def sig_age(self, t):
        """通知時間 t(HH:MM 台灣)離現在幾分鐘;沒有 / 看不懂回 None"""
        ms = minutes_of(t) if t else None
        if ms is None:
            return None
        n = self.now(); return n.hour * 60 + n.minute - ms

    def on_signal(self, it):
        if not self.primed:                                       # 2026-10-29 稽核 #1:還沒成功讀過一次通知 → 什麼都不做(也不標看過)
            return
        k = it.get("_k") or (it.get("t", "") + it.get("title", ""))
        if k in self.seen:
            return
        self.seen.add(k)
        title, body, kind = it.get("title", ""), it.get("body", ""), it.get("kind", "")
        code = code_of(title)
        if not code:
            return
        judge = judge_of(body)
        # 平倉通知:同一檔、同一份判別式(強三跟判三)、持倉中 → 出場
        if kind in EXIT_KINDS:
            p = self.positions.get(code)
            if p and self.exit_match(p, judge):
                if p["state"] == HOLDING:
                    self.exit_pos(p, "手機平倉通知:" + title, self.last_px.get(code, p["entry"]))
                elif p["state"] == ENTERING:
                    # 2026-10-29 稽核 #2:進場單還沒回報就來了平倉通知 → 記下來,進場成交轉持倉時馬上平(原本整則丟掉)
                    p["exit_pending"] = "手機平倉通知:" + title
                    self.log(f"[交易] {code} 進場中收到平倉通知 → 等進場成交後馬上平倉")
                # 平倉中:已經在平,不理
            return
        if kind not in ("long", "short"):
            return
        if re.search(r"·\s*超額\s*·", body):                   # 2026-10-30 使用者「超過限額的股票 … 只是註記“超額”不交易」:只通知,要做請用手機強制進場
            self.log(f"[交易] 不進場 {title}:超額(一張就超過多空上限),只通知;要做請用強制進場"); return
        age = self.sig_age(it.get("t"))
        if age is not None and age > self.sig_max_age:          # 2026-10-29 稽核 #1:太舊的進場通知(例如重開 / 讀取延遲才看到)不進
            self.log(f"[交易] 不進場 {title}:通知時間 {it.get('t')} 已經過 {age:.0f} 分鐘(上限 {self.sig_max_age:g},TRADE_SIGNAL_MAX_AGE_MIN)"); return
        why = self.can_enter(code, judge)
        if why:
            self.log(f"[交易] 不進場 {title}:{why}"); return
        # 2026-10-30 11:08(實際時間)使用者「2道都做」第 2 道:送單前最後一關 — 電腦開盤前向證交所 / 櫃買查的今天資格(elig.py),
        #   處置股 / 注意股 / 不可現股當沖 / 做空但暫停先賣後買 → 不進場(名單再怎麼漏也進不了單;沒有複檢檔就不擋)
        try:
            import elig; eb = elig.blocked(code, kind)
        except Exception: eb = ""
        if eb:
            self.log(f"[交易] 不進場 {title}:{eb}(電腦開盤前複檢)"); return
        mb = RE_BAR.search(body); ekey = f"{code}|{judge}|{mb.group(1)}" if mb else None
        if ekey and ekey in self.entered_keys:                  # 2026-10-29 稽核 #3:同一個訊號(代碼 + 判別式 + K 棒時間)換了發送分鐘重送 → 不再進
            self.log(f"[交易] 不進場 {title}:同一個訊號({ekey})今天已經進場過"); return
        tpsl = RE_TPSL.search(body); mp = RE_PX_END.search(title); px_sig = num(mp.group(1)) if mp else 0
        if not tpsl or not px_sig:
            self.log(f"[交易] 不進場 {title}:通知裡找不到進場價 / 停利 / 停損"); return
        tp_sig, sl_sig = num(tpsl.group(1)), num(tpsl.group(2))
        if not ((sl_sig < px_sig < tp_sig) if kind == "long" else (tp_sig < px_sig < sl_sig)):   # 2026-10-29 稽核 #7:價格順序不合理(解析錯 / 通知錯)不進
            self.log(f"[交易] 不進場 {title}:停利 {tp_sig} / 進場 {px_sig} / 停損 {sl_sig} 順序不合理({'做多要 停損<進場<停利' if kind == 'long' else '做空要 停利<進場<停損'})"); return
        last = self.last_px.get(code)
        if not last:
            self.log(f"[交易] 不進場 {title}:還沒收到 {code} 的元大即時價"); return
        if abs(last - px_sig) / px_sig * 100 > self.max_dev:
            self.log(f"[交易] 不進場 {title}:現價 {last} 離訊號價 {px_sig} 超過 {self.max_dev}%(太晚了)"); return
        d = 1 if kind == "long" else -1
        price = shift(last, self.slip * d)                     # 買進往上讓、賣出往下讓
        lots = max(1, int(self.cap // (px_sig * 1000))) if self.cap > 0 else self.qty   # 2026-10-30 張數照多空上限(訊號價算,和通知上的張數一樣)
        if self.max_amount > 0:                                 # 2026-10-27 一天成交總金額上限(買 + 賣都算):剩的額度夠幾張就掛幾張
            room = self.max_amount - self.amount_today
            fit = int(room // (price * 1000 * 2))
            if fit < 1:
                self.log(f"[交易] 不進場 {title}:今天成交金額已 {self.amount_today:,.0f},剩 {room:,.0f} 不夠一進一出 1 張(上限 {self.max_amount:,.0f})"); return
            if fit < lots:
                self.log(f"[交易] 額度只夠 {fit} 張(今天成交金額 {self.amount_today:,.0f} / 上限 {self.max_amount:,.0f}),{lots} 張改掛 {fit} 張"); lots = fit
        p = self.new_pos(code, d, judge, title, tp=tp_sig, sl=sl_sig)
        p["no_max_move"] = "✓破" in body   # 2026-10-28 使用者選「元大那邊對假突破空單不套 6.5%」:判三「假突破跌破 Ka 空」(通知帶「空✓破」)常在離昨收 +6.5% 以上才進場,不套 ±6.5% 強制平倉(停利 / 停損 / 13:20 / 手機平倉通知照舊)
        if ekey:
            self.entered_keys.add(ekey); self.save_state()      # 2026-10-29 稽核 #3:送單就記(沒成交也不再追同一個訊號)
        self.send(p, "進場", "B" if d > 0 else "S", lots, price, p["typ"])

    def can_enter(self, code, judge):
        if not self.enabled:
            return "自動交易關閉"
        if not self.auto:
            return "自動進場關閉(TRADE_AUTO=0,只管元三 / 強制模式)"
        if self.stop_file.exists():
            return "緊急停止(STOP 檔案存在)"
        if self.judges and judge not in self.judges:
            return f"{judge} 不在 TRADE_JUDGES"
        if code in self.positions:
            return f"這檔已經有部位({self.positions[code]['judge']} {self.positions[code]['state']})"
        if code in self.locked:
            return "今天強平過這檔,判一 / 二 / 三不再自動進場"
        if self.auto_count() >= self.max_pos:
            return f"已經有 {self.auto_count()} 檔部位(上限 {self.max_pos})"
        if self.pnl <= -self.max_loss:
            return f"今天已虧 {-self.pnl:,.0f}(上限 {self.max_loss:,.0f})"
        hm = self.hm()
        if not ("09:00" <= hm < self.last_entry):
            return f"不在 09:00~{self.last_entry}"
        return None

    # ── 下單 ──
    def send(self, p, purpose, side, lots, price, typ, flag=" ", tif="3"):
        self.seq = getattr(self, "seq", 0) + 1
        tag = f"{'F' if p.get('forced') else 'T'}{self.now().strftime('%H%M%S')}{self.seq:03d}"   # 自訂編號(BasketNo,限 32 英數字),回報會帶回來;F = 強制單(接回時認得)
        o = {"no": None, "tag": tag, "purpose": purpose, "side": side, "want": lots * 1000, "filled": 0, "cost": 0.0, "at": time.time(),
             "price": price, "typ": typ, "flag": flag, "tif": tif}
        p["order"] = o
        how = {"H": "漲停價", "L": "跌停價"}.get(flag, f"限價 {price}")
        self.log(f"[交易] 送單 {purpose} {p['code']} {'買' if side == 'B' else '賣'} {lots} 張 {how}(委託種類 {typ},{'ROD' if tif == '0' else 'IOC'})")
        no = self.gw.send(p["code"], p["mkt"], side, lots, price, typ, tif, tag, flag)
        if no:
            o["no"] = str(no).strip()

    def exit_pos(self, p, why, px, force_limit=False):
        if not p or p["state"] != HOLDING:
            return
        self.log(f"[交易] 出場 {p['code']}:{why}(現價 {px})")
        p["exit_why"] = why
        if p.get("oco_live"):                                   # 先刪元大條件單,免得和程式的平倉單一起成交
            self.gw.delete_oco(p["oco_no"]); p["oco_live"] = False
            self.log(f"[交易] 刪除元大條件單 {p['oco_no']}")
        p["force_limit"] = force_limit or self.hm() >= "13:25"
        p["state"] = EXITING
        self.send_exit(p, p["qty"], px)

    def send_exit(self, p, lots, px):
        side = "S" if p["dir"] > 0 else "B"
        if self.exit_mode == "limitup" or p.get("force_limit") or self.hm() >= "13:25":
            self.send(p, "平倉", side, lots, 0, p["typ"], "H" if side == "B" else "L", "0")      # 空單買回掛漲停、多單賣出掛跌停,ROD
        else:
            self.send(p, "平倉", side, lots, shift(px, -self.slip * p["dir"]), p["typ"])

    def after_report(self, p):
        o = p.get("order")
        if not o:
            return
        done = o["filled"] >= o["want"] or o.get("dead")
        if not done:
            return
        lots = o["filled"] // 1000; avg = o["cost"] / o["filled"] if o["filled"] else 0
        p["order"] = None
        if o["purpose"] == "進場":
            if lots == 0:
                if o.get("timeout") and not p.get("close_after_entry"):
                    # 2026-10-29 稽核 #5:IOC 等 fill_wait 秒沒回報 ≠ 沒成交(回報可能晚到)→ 部位留著「等確認」,晚到的成交照樣接;
                    #   confirm_wait 秒後還是 0(step 檢查)才拿掉
                    p["order"] = o
                    if not p.get("wait_confirm"):
                        p["wait_confirm"] = time.time()
                        self.log(f"[交易] {p['code']} 進場 {self.fill_wait:.0f} 秒沒成交回報 → 等確認(再等 {self.confirm_wait:.0f} 秒,晚到的成交照樣接)")
                    return
                self.log(f"[交易] {p['code']} 進場沒成交,放棄"); self.positions.pop(p["code"], None)
                if p.get("forced"): self.alert(f"⚠ 強制進場 {p['code']} 沒有成交", "元大回報委託結束、沒有成交")
                return
            if p.pop("wait_confirm", None):
                self.log(f"[交易] {p['code']} 等確認中收到晚到的進場成交 {lots} 張 @ {avg:.2f} → 轉持倉")
            p["qty"], p["entry"], p["state"], p["t0"] = lots, avg, HOLDING, self.hm()
            p["entry_tag"] = o["tag"]                           # 2026-10-29 稽核 #5:同一張進場單之後晚到的成交(on_report)認得、併進來
            self.amount_today += o["cost"]                      # 2026-10-27 今天成交金額(買 + 賣)
            if p.get("close_after_entry"):                      # 平倉檢查:進場中就按了強平 → 成交多少馬上全部平掉
                self.n_trades += 1; self.log(f"[交易] {p['code']} 進場成交 {lots} 張,之前已按強平 → 馬上平倉")
                self.exit_pos(p, "強平", avg, force_limit=True); return
            if p.get("exit_pending"):                           # 2026-10-29 稽核 #2:進場中收到平倉通知 → 成交轉持倉後馬上平
                self.n_trades += 1; why = p.pop("exit_pending"); self.log(f"[交易] {p['code']} 進場成交 {lots} 張,進場中已收到平倉通知 → 馬上平倉")
                self.exit_pos(p, why, self.last_px.get(p["code"], avg)); return
            self.n_trades += 1
            if p.get("forced"):
                self.set_forced_tpsl(p, avg); self.save_state()
                c = p["code"]
                self.post(f"{self.name_of(c)} {c} {'做多' if p['dir'] > 0 else '做空'}進場 {avg:.2f}",
                          f"{p['t0']} 強制 · 停利 {p['tp']:.2f} 停損 {p['sl']:.2f} · {lots}張 · {FORCED}", "long" if p["dir"] > 0 else "short")
            self.log(f"[交易] 持倉 {p['code']} {p['judge']} {'多' if p['dir'] > 0 else '空'} {lots} 張 均價 {avg:.2f} 停利 {p['tp']} 停損 {p['sl']}")
            if self.broker_stop and p["tp"] and p["sl"]:
                p["oco_no"] = None; self.oco_q.append(p["code"])
                self.gw.place_oco(p["code"], p["mkt"], p["dir"], lots, p["tp"], p["sl"])
            return
        # 平倉
        take = min(lots, max(p["qty"], 0)); extra = lots - take
        self.book_close(p, take, avg)
        if extra > 0:
            # 平倉檢查 #1 #2:程式的平倉單成交得比部位多(手動先平了 / 重送重複)= 多平了 → 反向的張數馬上補回
            self.log(f"[交易] !! {p['code']} 平倉成交 {lots} 張,部位只剩 {take} 張 → 多平 {extra} 張,馬上反向補回")
            self.alert(f"⚠ {p['code']} 多平 {extra} 張,已自動補回", f"{'漲停買回' if p['dir'] > 0 else '跌停賣出'} {extra} 張 ROD")
            self.finish_pos(p)
            self.rebuy(p["code"], -p["dir"], extra, avg, p["mkt"])
            return
        if p["qty"] > 0:      # 還沒平完(委託失效 / 被取消)→ 再送
            px = self.last_px.get(p["code"], avg or p["entry"])
            if o.get("shrink") or o.get("partial"):              # 是程式自己取消(手動先平一部分 / 加碼 / 接回時只掛一部分)→ 重送剩下的,不算失敗次數
                self.log(f"[交易] {p['code']} 平倉單已取消 / 成交 {lots} 張,剩 {p['qty']} 張重新送出")
                p["force_limit"] = True; self.send_exit(p, p["qty"], px); return
            p["resend"] = p.get("resend", 0) + 1
            if p["resend"] > self.max_resend:                  # 2026-10-28 獨立檢查 #8:元大一直拒絕 → 不無限重送,推播請手動處理(13:20 / 13:22 複查還會再平)
                self.log(f"[交易] !! {p['code']} 平倉已重送 {self.max_resend} 次還沒平完,停止自動重送,請手動確認")
                self.alert(f"⚠⚠ {p['code']} 平倉失敗 {p['qty']} 張", f"已重送 {self.max_resend} 次,請立刻到投資先生手動平倉")
                p["stuck"] = True; return
            self.log(f"[交易] {p['code']} 還剩 {p['qty']} 張沒平,再送(第 {p['resend']} 次)")
            if self.exit_mode == "limitup" or p.get("force_limit") or self.hm() >= "13:25":
                self.send_exit(p, p["qty"], px)
            else:
                self.send(p, "平倉", "S" if p["dir"] > 0 else "B", p["qty"], shift(px, -(self.slip + 3) * p["dir"]), p["typ"])
            return
        self.finish_pos(p)


AMT_YES = ("額度", "金額", "上限", "限額", "超過", "不足")
AMT_NO = ("券", "庫存", "資格", "暫停", "停止", "處置", "全額", "注意股", "漲停", "跌停", "價格", "未開放", "權限")
def amount_reject(msg):
    """2026-10-27 元大回的拒絕原因是不是跟金額 / 額度有關(是 → 退 1 張再掛);券源不足、沒資格、處置股、價格不對這些不算"""
    m = str(msg or "")
    return any(k in m for k in AMT_YES) and not any(k in m for k in AMT_NO)


def fetch_signals(worker_url, day, push_key=""):
    """抓手機今天的通知(雲端紀錄,新的在前)→ 回傳舊到新。2026-10-29 鑰匙:帶中繼金鑰(以後 Worker 全面要鑰匙時照樣讀得到)"""
    try:
        r = urllib.request.urlopen(urllib.request.Request(f"{worker_url.rstrip('/')}/?log=get&d={day}", headers={"X-Push-Key": push_key or ""}), timeout=10)
        js = json.loads(r.read().decode("utf-8"))
        return list(reversed(js)) if isinstance(js, list) else None
    except Exception:
        return None     # 2026-10-29 稽核 #1:讀取失敗回 None(跟「今天還沒有通知」的 [] 分開),prime 才不會把失敗當成「沒有舊通知」
