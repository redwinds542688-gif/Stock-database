"""一次一單的自動交易(2026-10-22 使用者:「真的接通 可以先試 一次只掛一單 成交後才能繼續」→「先寫好 預設測試環境」)

訊號來源 = 手機的通知(雲端紀錄 ?log=get):進場通知(做多 / 做空進場)→ 進場;同一檔的平倉通知 → 出場。
停利 / 停損價(通知裡寫的)由這裡用元大逐筆成交盯,碰到就平倉(約 1 秒內),不等 1 分 K、不等通知。

狀態:空閒 → 進場中(等成交回報)→ 持倉中 → 平倉中(等成交回報)→ 空閒。只有空閒時才接新訊號。
保護:每筆 TRADE_QTY 張、一天最多 TRADE_MAX_PER_DAY 筆、當天虧損到 TRADE_MAX_LOSS 就停、13:25 一律平倉、
      STOP 檔案(緊急停止:不再進場,有部位照樣平)、正式環境要 TRADE_ALLOW_PROD=1 才會下單。
以元大的即時回報為準(RR_RealReport:51 = 股票成交),不自己假設成交。

這個檔不依賴元大元件:下單 / 取消透過 gateway(bridge 提供),測試時可以換成假的。
"""
import json, re, time, threading, urllib.request
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
RE_TPSL = re.compile(r"停利\s*([\d.]+)\s*停損\s*([\d.]+)")
RE_JUDGE = re.compile(r"判[一二三四]")


class Trader:
    def __init__(self, cfg, gateway, log, now):
        """gateway:send(code, mkt, side 'B'/'S', qty 張, price, order_type, tif, tag, price_flag ' '限價 / H 漲停 / L 跌停) -> 委託書號或 None;cancel(order_no, code)
           place_oco(code, mkt, dir, 張, 停利價, 停損價)(結果從 on_strategy_reply 回來);delete_oco(策略編號)
           now():回傳台灣時間 datetime"""
        self.cfg, self.gw, self.log, self.now = cfg, gateway, log, now
        self.enabled = cfg.get("TRADE_ENABLE", "0") == "1"
        self.qty = int(cfg.get("TRADE_QTY", "1"))
        self.max_n = int(cfg.get("TRADE_MAX_PER_DAY", "3"))
        self.max_loss = float(cfg.get("TRADE_MAX_LOSS", "3000"))
        self.judges = set(x.strip() for x in cfg.get("TRADE_JUDGES", "判一,判二,判三").split(",") if x.strip())
        self.slip = int(cfg.get("TRADE_SLIP_TICKS", "2"))          # 讓價檔數(確保成交)
        self.max_dev = float(cfg.get("TRADE_MAX_DEV_PCT", "0.5"))  # 現價離訊號價超過 % 就不追
        self.fill_wait = float(cfg.get("TRADE_FILL_WAIT", "8"))    # 進場送單後等成交回報幾秒(平倉不取消,等到成交)
        # 2026-10-22 使用者:「30 元做空反漲到 30.5 觸發平倉,直接掛漲停價贖回,不管股價怎麼跑都會在第一時間平倉」:
        #   平倉空單掛漲停(H)買回、平倉多單掛跌停(L)賣出,ROD。照價格優先會從最好的賣價 / 買價開始成交 = 實際成交是市價,保證排最前面;
        #   用 ROD 不用 IOC:價格穩定措施(暫緩撮合)時 IOC / 市價會直接失效,ROD 會留著等撮合。TRADE_EXIT_MODE=slip 改回限價讓檔 + IOC
        self.exit_mode = cfg.get("TRADE_EXIT_MODE", "limitup")
        # 2026-10-22 使用者:「在股票交易啟動當下 直接預掛停損是最好方式」:進場成交後馬上在元大主機掛二擇一條件單(OCO)——
        #   停損、停利兩個條件,觸發就送漲停買回 / 跌停賣出(ROD)。程式或網路斷了元大照樣平倉。OCO 在的時候,程式自己的停利停損只記錄不下單
        self.broker_stop = cfg.get("TRADE_BROKER_STOP", "1") == "1"
        self.type_long = cfg.get("TRADE_TYPE_LONG", "0")           # 委託種類:0 現股(使用者:「做多只需要 現股 亮起來即可」),買進、賣出都用 0
        self.type_short = cfg.get("TRADE_TYPE_SHORT", "9")         # 9 現股當沖委託控管(先賣後買)= 元大 App 下單畫面的「沖」(使用者:「做空要按 沖 亮起來才能做空」)
        self.stop_file = Path(cfg.get("TRADE_STOP_FILE", "/opt/yuanta_bridge/STOP"))
        self.lock = threading.RLock()
        self.reset_day()

    def reset_day(self):
        self.state = IDLE if self.enabled else HALT
        self.seen = set()          # 已處理的通知 _k
        self.pos = None            # {code, mkt, dir, qty(張), entry, tp, sl, judge, typ}
        self.order = None          # 進行中的委託 {no, side, want(股), filled(股), cost, at, purpose}
        self.n_trades, self.pnl, self.last_px = 0, 0.0, {}
        self.mkt_of = {}
        self.journal = []
        self.flat_watch = None     # 剛平倉的部位 {code, dir, typ, until}:這段時間如果又有同方向平倉成交(OCO 同時觸發)= 多平了,要補回來

    def prime(self, signals):
        """啟動時把當天已經有的通知都標成「看過」,不會拿舊訊號進場"""
        for it in signals:
            self.seen.add(it.get("_k") or (it.get("t", "") + it.get("title", "")))
        self.log(f"[交易] 啟動:略過今天已有的 {len(signals)} 則通知;{'開啟' if self.enabled else '關閉(TRADE_ENABLE=0)'},每筆 {self.qty} 張、一天最多 {self.max_n} 筆、虧損上限 {self.max_loss:,.0f}")

    def resume(self, trades, order_tags, strategies, signals):
        """2026-10-22 使用者:「加 啟動時接回部位」:程式重開時,從元大今天的成交回報算出還沒平的部位,是這支程式開的就接回來管理。
        trades:[{code, bs 'B'/'S', lots, price, time, order_no}](時間舊到新);order_tags:{委託書號: BasketNo};
        strategies:[{no, code, status}](有效的二擇一條件單);signals:手機今天的通知(找停利 / 停損價)。
        只接「程式送的單」(BasketNo 以 T 開頭)開的部位;手動在投資先生做的不管。今天已做筆數、已實現損益也一起算回來。"""
        with self.lock:
            net, cost, opened_by_me = {}, {}, {}
            for t in sorted(trades, key=lambda x: x.get("time", "")):
                c, q = t["code"], t["lots"] * (1 if t["bs"] == "B" else -1)
                n0 = net.get(c, 0); n1 = n0 + q
                if n0 == 0 and n1 != 0:                                     # 開新部位
                    cost[c] = [abs(n1), t["price"] * abs(n1)]
                    opened_by_me[c] = str(order_tags.get(t["order_no"], "")).startswith("T")
                    if opened_by_me[c]: self.n_trades += 1
                elif n0 != 0 and (n0 > 0) == (q > 0):                       # 同方向加碼
                    cost[c][0] += abs(q); cost[c][1] += t["price"] * abs(q)
                elif n0 != 0 and opened_by_me.get(c):                       # 平倉:算已實現損益
                    lots = min(abs(q), abs(n0)); avg = cost[c][1] / cost[c][0]; d = 1 if n0 > 0 else -1
                    self.pnl += (t["price"] - avg) * lots * 1000 * d - (t["price"] + avg) * lots * 1000 * 0.001425 * float(self.cfg.get("TRADE_FEE_DISCOUNT", "0.3")) - (t["price"] if d > 0 else avg) * lots * 1000 * 0.0015
                net[c] = n1
            open_mine = [c for c, n in net.items() if n != 0 and opened_by_me.get(c)]
            manual = [c for c, n in net.items() if n != 0 and not opened_by_me.get(c)]
            if manual:
                self.log(f"[交易] 接回:{', '.join(manual)} 有未平部位但不是程式開的(手動),不管")
            if not open_mine:
                self.log(f"[交易] 接回:沒有程式開的未平部位;今天已做 {self.n_trades} 筆、已實現 {self.pnl:+,.0f}")
                return None
            c = open_mine[-1]
            if len(open_mine) > 1:
                self.log(f"[交易] !! 接回:程式開的未平部位有 {len(open_mine)} 檔({', '.join(open_mine)}),只接最後一檔 {c},其他請手動處理")
            n = net[c]; d = 1 if n > 0 else -1; avg = cost[c][1] / cost[c][0]
            tp = sl = 0.0; title = ""
            for it in reversed(signals):                                     # 最近一則這檔的進場通知
                if it.get("kind") in ("long", "short") and c in it.get("title", "") and (it["kind"] == "long") == (d > 0):
                    m = RE_TPSL.search(it.get("body", ""))
                    if m: tp, sl, title = float(m.group(1)), float(m.group(2)), it.get("title", "")
                    break
            self.pos = {"code": c, "mkt": self.mkt_of.get(c, "TWSE"), "dir": d, "qty": abs(n), "entry": avg, "tp": tp, "sl": sl,
                        "judge": "接回", "typ": self.type_long if d > 0 else self.type_short, "title": title or "接回部位"}
            oco = [x for x in strategies if x.get("code") == c]
            if oco:
                self.pos["oco_no"] = oco[-1]["no"]; self.pos["oco_live"] = True
            self.state = HOLDING
            self.log(f"[交易] 接回部位 {c} {'多' if d > 0 else '空'} {abs(n)} 張 均價 {avg:.2f} 停利 {tp or '?'} 停損 {sl or '?'};"
                     f"元大條件單 {'在(' + self.pos['oco_no'] + ')' if oco else '沒有'};今天已做 {self.n_trades} 筆、已實現 {self.pnl:+,.0f}")
            if not oco and self.broker_stop and tp and sl:
                self.pos["oco_no"] = None
                self.gw.place_oco(c, self.pos["mkt"], d, abs(n), tp, sl)
            elif not oco and not (tp and sl):
                self.log("[交易] !! 找不到這檔的停利 / 停損價,只剩手機平倉通知和 13:25 強制平倉會出場")
            return c

    # ── 外部事件 ──
    def on_tick(self, code, price):
        self.last_px[code] = price
        with self.lock:
            p = self.pos
            if self.state == HOLDING and p and p["code"] == code:
                hit_tp = (price >= p["tp"]) if p["dir"] > 0 else (price <= p["tp"])
                hit_sl = (price <= p["sl"]) if p["dir"] > 0 else (price >= p["sl"])
                if hit_tp or hit_sl:
                    if p.get("oco_live"):
                        if not p.get("oco_noted"):
                            p["oco_noted"] = True; self.log(f"[交易] 價格 {price} 碰到{'停利' if hit_tp else '停損'},由元大條件單(OCO)平倉")
                    else:
                        self.exit_pos("停利" if hit_tp else "停損", price)

    def on_report(self, rpt_type, order_no, status, qty_shares, price, basket="", code="", bs=""):
        """元大即時回報:rpt_type 50 委託、51 成交;status 見文件(1 失敗、2 取消成功、24 失效、25 價穩失效…)
           用自訂編號 basket(下單時帶的 BasketNo)或委託書號對;委託書號可能比回報晚到,所以以 basket 為主"""
        with self.lock:
            o = self.order
            mine = o and ((basket and str(basket).strip() == o["tag"]) or (o["no"] and str(order_no).strip() == o["no"]))
            if not mine:
                if rpt_type == 51 and qty_shares > 0 and code:
                    self.on_other_fill(str(code).strip(), bs, qty_shares, price)
                return
            if not o["no"] and order_no:
                o["no"] = str(order_no).strip()
            if rpt_type == 51 and qty_shares > 0:
                o["filled"] += qty_shares; o["cost"] += qty_shares * price
                self.log(f"[交易] 成交 {o['purpose']} {o['side']} {qty_shares // 1000} 張 @ {price}(累計 {o['filled'] // 1000}/{o['want'] // 1000})")
            elif rpt_type == 50 and status in (1, 2, 24, 25):
                o["dead"] = True
                self.log(f"[交易] 委託結束 狀態 {status}({ {1: '委託失敗', 2: '取消成功', 24: '委託失效', 25: '價穩失效'}[status] })")
            self.after_report()

    def on_other_fill(self, code, bs, qty_shares, price):
        """不是這支程式送的單成交了:元大條件單(OCO)觸發的平倉,或程式平倉時 OCO 也同時觸發(多平了)"""
        p = self.pos
        close_side = lambda d: "B" if d < 0 else "S"
        if p and p["code"] == code and self.state in (HOLDING, EXITING) and bs == close_side(p["dir"]):
            lots = qty_shares // 1000
            self.log(f"[交易] 元大條件單平倉成交 {code} {lots} 張 @ {price}")
            p["exit_why"] = p.get("exit_why") or "元大條件單(OCO)"
            self.book_close(lots, price)
            if p["qty"] <= 0:
                if self.state == EXITING and self.order:           # 程式自己的平倉單也還在 → 取消,避免多平
                    if self.order.get("no"): self.gw.cancel(self.order["no"], code)
                    self.order = None
                self.finish_pos()
            return
        fw = self.flat_watch
        if fw and fw["code"] == code and bs == close_side(fw["dir"]) and time.time() < fw["until"]:
            lots = qty_shares // 1000
            self.log(f"[交易] !! 已經平倉了又有 {lots} 張平倉成交(條件單同時觸發)→ 馬上反向補回")
            self.pos = {"code": code, "mkt": fw["mkt"], "dir": -fw["dir"], "qty": lots, "entry": price, "tp": 0, "sl": 0, "judge": "補回", "typ": fw["typ"], "title": "多平補回"}
            self.state = HOLDING; self.exit_pos("多平補回", price)

    def book_close(self, lots, avg):
        p = self.pos
        if not lots:
            return
        pnl = (avg - p["entry"]) * lots * 1000 * p["dir"]
        fee = (avg + p["entry"]) * lots * 1000 * 0.001425 * float(self.cfg.get("TRADE_FEE_DISCOUNT", "0.3"))
        tax = (avg if p["dir"] > 0 else p["entry"]) * lots * 1000 * 0.0015
        net = pnl - fee - tax; self.pnl += net
        p["qty"] -= lots
        self.journal.append({"code": p["code"], "dir": p["dir"], "entry": p["entry"], "exit": avg, "lots": lots, "net": round(net), "why": p.get("exit_why")})
        self.log(f"[交易] 平倉 {p['code']} {lots} 張 @ {avg:.2f} 淨 {net:+,.0f}(今天累計 {self.pnl:+,.0f})")

    def finish_pos(self):
        p = self.pos
        self.flat_watch = {"code": p["code"], "mkt": p["mkt"], "dir": p["dir"], "typ": p["typ"], "until": time.time() + 120}
        self.pos = None; self.state = IDLE

    def on_strategy_reply(self, strategy_no, ok, msg):
        """元大新增條件單回應"""
        with self.lock:
            p = self.pos
            if not p or p.get("oco_no") is not None:
                return
            if ok and strategy_no:
                p["oco_no"] = str(strategy_no).strip(); p["oco_live"] = True
                self.log(f"[交易] 元大條件單(OCO)已掛:策略編號 {p['oco_no']}(停利 {p['tp']} / 停損 {p['sl']})")
            else:
                p["oco_no"] = ""; p["oco_live"] = False
                self.log(f"[交易] !! 元大條件單掛失敗:{msg} → 改由程式自己盯停利停損")

    def on_order_reply(self, order_no, ok, msg):
        with self.lock:
            o = self.order
            if not o or o.get("no"):
                return
            if ok:
                o["no"] = str(order_no).strip()
            else:
                o["dead"] = True; self.log(f"[交易] 委託被拒:{msg}"); self.after_report()

    # ── 每秒呼叫一次 ──
    def step(self, signals=None):
        with self.lock:
            hm = self.now().strftime("%H:%M")
            if self.state == HOLDING and hm >= "13:25":
                # 2026-10-22 使用者:「1:25 後還有沒贖回的股票,必須掛漲停價(做空)或跌停價(做多)贖回,1:30 一定會成交(除非漲停或跌停)」:
                #   13:25~13:30 收盤集合競價不接受市價 / IOC / FOK,一律掛漲停 / 跌停 ROD(不管 TRADE_EXIT_MODE)
                self.exit_pos("13:25 強制平倉", self.last_px.get(self.pos["code"], self.pos["entry"]), force_limit=True)
            o = self.order
            if o and o["purpose"] == "平倉" and o.get("tif") == "0":
                if not o.get("dead") and time.time() - o["at"] > 30 and not o.get("warned"):
                    o["warned"] = True; self.log(f"[交易] !! 漲停 / 跌停平倉單 30 秒還沒全部成交(可能鎖漲停 / 暫緩撮合),繼續等")
            elif o and not o.get("dead") and time.time() - o["at"] > self.fill_wait:
                if o.get("no"):
                    self.log(f"[交易] {o['purpose']} 委託 {self.fill_wait:.0f} 秒沒成交完,取消剩餘"); self.gw.cancel(o["no"], self.pos["code"] if self.pos else "")
                o["dead"] = True; self.after_report()
            for it in (signals or []):
                self.on_signal(it)

    # ── 訊號 ──
    def on_signal(self, it):
        k = it.get("_k") or (it.get("t", "") + it.get("title", ""))
        if k in self.seen:
            return
        self.seen.add(k)
        title, body, kind = it.get("title", ""), it.get("body", ""), it.get("kind", "")
        m = RE_CODE.search(title)
        if not m:
            return
        code = m.group(1); mj = RE_JUDGE.search(body); judge = mj.group(0) if mj else None
        # 平倉通知:同一檔、持倉中 → 出場
        if kind in ("loss", "profit", "exit") and self.state == HOLDING and self.pos and self.pos["code"] == code:
            self.exit_pos("手機平倉通知:" + title, self.last_px.get(code, self.pos["entry"]))
            return
        if kind not in ("long", "short"):
            return
        why = self.can_enter(code, judge)
        if why:
            self.log(f"[交易] 不進場 {title}:{why}"); return
        tpsl = RE_TPSL.search(body); px_sig = float((re.search(r"([\d.]+)\s*$", title) or [0, 0])[1] or 0)
        if not tpsl or not px_sig:
            self.log(f"[交易] 不進場 {title}:通知裡找不到進場價 / 停利 / 停損"); return
        last = self.last_px.get(code)
        if not last:
            self.log(f"[交易] 不進場 {title}:還沒收到 {code} 的元大即時價"); return
        if abs(last - px_sig) / px_sig * 100 > self.max_dev:
            self.log(f"[交易] 不進場 {title}:現價 {last} 離訊號價 {px_sig} 超過 {self.max_dev}%(太晚了)"); return
        d = 1 if kind == "long" else -1
        typ = self.type_long if d > 0 else self.type_short
        self.pos = {"code": code, "mkt": self.mkt_of.get(code, "TWSE"), "dir": d, "qty": 0, "entry": 0.0,
                    "tp": float(tpsl.group(1)), "sl": float(tpsl.group(2)), "judge": judge, "typ": typ, "title": title}
        price = shift(last, self.slip * d)                     # 買進往上讓、賣出往下讓
        self.send("進場", "B" if d > 0 else "S", self.qty, price, typ)
        self.state = ENTERING

    def can_enter(self, code, judge):
        if self.state != IDLE:
            return f"目前{self.state}(一次只做一筆)"
        if self.stop_file.exists():
            return "緊急停止(STOP 檔案存在)"
        if self.judges and judge not in self.judges:
            return f"{judge} 不在 TRADE_JUDGES"
        if self.n_trades >= self.max_n:
            return f"今天已做 {self.n_trades} 筆(上限 {self.max_n})"
        if self.pnl <= -self.max_loss:
            return f"今天已虧 {-self.pnl:,.0f}(上限 {self.max_loss:,.0f})"
        hm = self.now().strftime("%H:%M")
        if not ("09:00" <= hm < "13:20"):
            return "不在 09:00~13:20"
        return None

    # ── 下單 ──
    def send(self, purpose, side, lots, price, typ, flag=" ", tif="3"):
        p = self.pos
        self.seq = getattr(self, "seq", 0) + 1
        tag = f"T{self.now().strftime('%H%M%S')}{self.seq:03d}"           # 自訂編號(BasketNo,限 32 英數字),回報會帶回來
        self.order = {"no": None, "tag": tag, "purpose": purpose, "side": side, "want": lots * 1000, "filled": 0, "cost": 0.0, "at": time.time(), "price": price}
        how = {"H": "漲停價", "L": "跌停價"}.get(flag, f"限價 {price}")
        self.log(f"[交易] 送單 {purpose} {p['code']} {'買' if side == 'B' else '賣'} {lots} 張 {how}(委託種類 {typ},{'ROD' if tif == '0' else 'IOC'})")
        self.order["tif"] = tif
        no = self.gw.send(p["code"], p["mkt"], side, lots, price, typ, tif, tag, flag)
        if no:
            self.order["no"] = str(no).strip()

    def exit_pos(self, why, px, force_limit=False):
        p = self.pos
        if self.state != HOLDING or not p:
            return
        self.log(f"[交易] 出場 {p['code']}:{why}(現價 {px})")
        p["exit_why"] = why
        if p.get("oco_live"):                                   # 先刪元大條件單,免得和程式的平倉單一起成交
            self.gw.delete_oco(p["oco_no"]); p["oco_live"] = False
            self.log(f"[交易] 刪除元大條件單 {p['oco_no']}")
        p["force_limit"] = force_limit or self.now().strftime("%H:%M") >= "13:25"
        self.send_exit(p["qty"], px)
        self.state = EXITING

    def send_exit(self, lots, px):
        p = self.pos; side = "S" if p["dir"] > 0 else "B"
        if self.exit_mode == "limitup" or p.get("force_limit") or self.now().strftime("%H:%M") >= "13:25":
            self.send("平倉", side, lots, 0, p["typ"], "H" if side == "B" else "L", "0")      # 空單買回掛漲停、多單賣出掛跌停,ROD
        else:
            self.send("平倉", side, lots, shift(px, -self.slip * p["dir"]), p["typ"])

    def after_report(self):
        o, p = self.order, self.pos
        if not o:
            return
        done = o["filled"] >= o["want"] or o.get("dead")
        if not done:
            return
        lots = o["filled"] // 1000; avg = o["cost"] / o["filled"] if o["filled"] else 0
        self.order = None
        if o["purpose"] == "進場":
            if lots == 0:
                self.log("[交易] 進場沒成交,回到空閒"); self.pos = None; self.state = IDLE; return
            p["qty"], p["entry"] = lots, avg
            self.state = HOLDING; self.n_trades += 1
            self.log(f"[交易] 持倉 {p['code']} {'多' if p['dir'] > 0 else '空'} {lots} 張 均價 {avg:.2f} 停利 {p['tp']} 停損 {p['sl']}")
            if self.broker_stop and p["tp"] and p["sl"]:
                p["oco_no"] = None
                self.gw.place_oco(p["code"], p["mkt"], p["dir"], lots, p["tp"], p["sl"])
            return
        # 平倉
        if not p:
            return
        self.book_close(lots, avg)
        if p["qty"] > 0:      # 還沒平完(委託失效 / 被取消)→ 再送
            px = self.last_px.get(p["code"], avg or p["entry"])
            self.log(f"[交易] 還剩 {p['qty']} 張沒平,再送")
            if self.exit_mode == "limitup" or p.get("force_limit") or self.now().strftime("%H:%M") >= "13:25":
                self.send_exit(p["qty"], px)
            else:
                self.send("平倉", "S" if p["dir"] > 0 else "B", p["qty"], shift(px, -(self.slip + 3) * p["dir"]), p["typ"])
            return
        self.finish_pos()


def fetch_signals(worker_url, day):
    """抓手機今天的通知(雲端紀錄,新的在前)→ 回傳舊到新"""
    try:
        r = urllib.request.urlopen(f"{worker_url.rstrip('/')}/?log=get&d={day}", timeout=10)
        return list(reversed(json.loads(r.read().decode("utf-8"))))
    except Exception:
        return []
