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


def limit_up(ref):
    """2026-11-02 漲停價 = 昨收 × 1.1 往下取到升降單位;沒昨收回 0"""
    if not ref or ref <= 0: return 0.0
    v = ref * 1.1; t = tick_size(v); lu = math.floor(v / t + 1e-6) * t
    if tick_size(lu) != t: lu = math.floor(v / tick_size(lu) + 1e-6) * tick_size(lu)
    return round(lu, 2)


def fb_price(sig, d, ref=0.0, pct=1.0):
    """2026-11-08 16:09 使用者「改」:進場補單的限價 = 訊號價往不利方向 pct%(做多往上、做空往下),取到升降單位(做多往下取、做空往上取 → 不超過 pct%);
       超過漲停 / 跌停就用漲停 / 跌停"""
    v = sig * (1 + d * pct / 100); t = tick_size(v)
    px = math.floor(v / t + 1e-6) * t if d > 0 else math.ceil(v / t - 1e-6) * t
    if ref and ref > 0:
        lu = limit_up(ref); ld = ref * 0.9; tl = tick_size(ld); ld = math.ceil(ld / tl - 1e-6) * tl
        px = min(px, lu) if d > 0 else max(px, ld)
    return round(px, 2)


def close_side(d):
    return "B" if d < 0 else "S"

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
RE_JUDGE = re.compile(r"判[一二三四五六七]")   # 2026-11-11 00:20 判六上線(使用者「把判六啟動 加入明天進場行列」:照通知自動下單);2026-11-10 加觀察版判六
_RE_JUDGE_OLD = re.compile(r"判[一二三四五七]")   # 2026-11-05 加判五;2026-11-09 加判七(15:28 使用者「判七 解凍上線」:照通知自動下單)
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
        self.max_pos = int(cfg.get("TRADE_MAX_POS", "0"))          # 2026-10-28 使用者「開放同時可以五檔股票在線 強制進場不在受限範圍內」
        # 2026-10-31 03:55 使用者「改 10」(16 天回測:同時 2 檔 +72,395、5 檔 +212,220、10 檔 +338,404、不限 +371,487;超過 10 檔是一天 490 萬先用完):
        #   預設 10;bridge.env 還是以前設的 2 或 5(10/29 開自動交易時 2、10/28 的 5)也當 10,不用動桌機檔案;要別的數字就寫成 2、5 以外的值
        # 2026-11-04 22:20 使用者「不用固定這10檔 反正買到不能買為止就好」:預設 0 = 不限檔數,只看 500 萬額度(已成交 + 漲停預留);bridge.env 舊的 2 / 5 / 10 也當不限
        #   19 天額度版(判一~四、判四 OBV40 2.5%):10 檔 +529,419 → 不限 +543,756(判四 115 → 122 筆),輸的日子一樣只有 10/28
        if self.max_pos in (2, 5, 10): self.max_pos = 0
        self.max_loss = float(cfg.get("TRADE_MAX_LOSS", "20000"))
        # 2026-10-31 使用者「加一道虧到多少就全部平倉」→「改 5 萬」:當天 已平倉 + 持倉帳面 虧到 TRADE_KILL_LOSS 就全部平倉、今天不再自動進場(強制進場照收);0 = 關
        #   16 天回測(判一二三四、同時 10 檔):5 萬一次都沒觸發;3 萬會在 10/20 09:31 帳面 −30,378 鎖死(收盤本來 +36,265),所以只當意外保險
        self.kill_loss = float(cfg.get("TRADE_KILL_LOSS", "50000"))
        # 2026-10-31 使用者「再設一個 浮輸一萬 二萬 三萬 四萬 五萬 的通知」:當天(已平倉 + 持倉帳面)虧到每一級各推手機一次(一天一次,重開還記得)
        # 2026-10-31 使用者「如果贏錢超過一萬呢」→「要有慶祝的通知」:當天合計賺到每一級各推一次慶祝通知(一級比一級熱鬧;只通知,不影響交易)
        self.win_alerts = sorted({float(x) for x in re.split(r"[,\s]+", str(cfg.get("TRADE_WIN_ALERTS", "10000,20000,30000,40000,50000,60000,70000,80000,90000,100000"))) if x.strip().replace(".", "", 1).isdigit() and float(x) > 0})
        self.loss_alerts = sorted({float(x) for x in re.split(r"[,\s]+", str(cfg.get("TRADE_LOSS_ALERTS", "10000,20000,30000,40000,50000"))) if x.strip().replace(".", "", 1).isdigit() and float(x) > 0})
        # 2026-10-31 使用者「改 C」(進場一定成交):進場先掛 IOC(讓 TRADE_SLIP_TICKS 檔);元大回報 IOC 結束還有沒成交的張數 →
        #   現價還在訊號價 ±TRADE_MAX_DEV_PCT 內、13:10 前、沒觸發全部平倉 → 剩下的張數改掛漲停買 / 跌停賣(ROD,保證成交,只補一次);0 = 關(照舊沒成交就放棄)
        self.entry_fallback = cfg.get("TRADE_ENTRY_FALLBACK", "1") == "1"
        self.fb_pct = float(cfg.get("TRADE_FALLBACK_PCT", "1") or 1)   # 2026-11-08 16:09 補單限價離訊號價 %   # 2026-10-26 使用者「改。當天虧 20,000 就停止進場」(原 3000)
        # 2026-10-27 使用者:一天買 + 賣成交金額上限(元大額度 500 萬留緩衝 490 萬);自動進場前算這筆一進一出要用多少,不夠就少掛幾張、連 1 張都不夠就不進;
        #   進場委託被拒(額度 / 金額)→ 少 1 張再掛,一直退到 1 張還被拒才放棄。強制進場不看這個(使用者:元大自己會擋)
        self.max_amount = float(cfg.get("TRADE_MAX_AMOUNT", "5000000"))
        # 2026-10-31 14:57 使用者「490萬 改成500萬」:預設 500 萬;bridge.env 還是以前寫的 4900000 也當 500 萬(不用動桌機檔案)
        if self.max_amount == 4900000: self.max_amount = 5000000
        self.judges = set(x.strip() for x in cfg.get("TRADE_JUDGES", "判一,判二,判三,判四,判五,判六,判七").split(",") if x.strip())   # 2026-10-31 預設加判四(使用者「判四 … 真正的下單也啟動 自動平倉機制也啟動」);bridge.env 有寫 TRADE_JUDGES 的要自己加
        if self.judges == {"判一", "判二", "判三"}: self.judges.add("判四")
        if self.judges == {"判一", "判二", "判三", "判四"}: self.judges.add("判五")   # 2026-11-05 使用者「完整上線」判五:bridge.env 寫的是判一~四(舊預設)= 沒特別排除 → 一樣加判五   # bridge.env 還是舊範本那行(判一,判二,判三)= 沒特別排除 → 一樣加判四;要排除就寫成別的(例如 判一,判二,判三,-)
        if self.judges == {"判一", "判二", "判三", "判四", "判五", "判七"}: self.judges.add("判六")   # 2026-11-11 00:20 判六上線:bridge.env 寫的是舊預設(一~五 + 七)= 沒特別排除 → 一樣加判六
        self.slip = int(cfg.get("TRADE_SLIP_TICKS", "2"))          # 讓價檔數(確保成交)
        self.max_dev = float(cfg.get("TRADE_MAX_DEV_PCT", "0.5"))  # 現價離訊號價超過 % 就不追
        # 2026-11-03 12:08 使用者(晶豪科 257.50 超額沒進,跌到 250 以下也沒補)「改」:超額的進場訊號先記著,現價回到一張 ≤ 多空上限、還在停利 / 停損之間、
        #   這份判別式還沒發平倉通知、沒過最後進場時間 → 補進場一次(照當下價格,停利 / 停損照原訊號);0 = 關
        #   12:10 使用者「這種股票要加上 確認按鍵」:條件到了不自動下單,手機跳「確認補進場」(?trade=pos 的 redo),按了才進(act=redo)
        self.over_redo = cfg.get("TRADE_OVER_REDO", "1") == "1"
        # 2026-11-06 使用者「如果價格又回到原預計空的點位呢」→「改重啟一次」:因「太晚了」沒進的訊號先記著,
        #   N 分鐘內現價回到訊號價 ±TRADE_MAX_DEV_PCT 以內、還沒碰到停利 / 停損 → 自動補進場(0 = 關)
        self.late_redo_min = float(cfg.get("TRADE_LATE_REDO_MIN", "10"))
        self.late_wait = {}
        self.y3_post_delay = float(cfg.get("TRADE_Y3_POST_DELAY", "3"))
        self.y3_near = float(cfg.get("TRADE_Y3_NEAR_PCT", "1.5"))
        self.man_move = float(cfg.get("TRADE_MANUAL_MAX_MOVE_PCT", "8.5"))   # 2026-11-03 13:17 元三 / 強三離昨收 ±8.5% 強制平倉(判一~四照舊 ±6.5%);設成跟 TRADE_MAX_MOVE_PCT 一樣 = 回到原本
        self.j7_move = float(cfg.get("TRADE_J7_MAX_MOVE_PCT", "8"))   # 2026-11-11 10:40 判四 / 判五也用這條(使用者「判四判五 都跟判七一樣」→「解鎖 改」)   # 2026-11-09 判七空單強平線(離昨收 %);0 = 跟其他判別式一樣用 TRADE_MAX_MOVE_PCT
        self.y3_free_mv = self.man_move > 0
        self.force_block_pct = float(cfg.get("TRADE_FORCE_BLOCK_PCT", "6.5"))   # 2026-11-03 強制進場:跌超過這麼多 % 不給做多、漲超過不給做空(0 = 不擋)   # 2026-11-03 手動單進場時離 ±6.5% 不到這麼多(%)= 已經接近 → 不套 ±6.5%   # 2026-11-03 元三進場卡等分批成交併完再發(秒)
        self.over_wait = {}
        self.fill_wait = float(cfg.get("TRADE_FILL_WAIT", "8"))    # 自動進場(限價 IOC)送單後等成交回報幾秒
        # 2026-10-29 稽核 #5:IOC 等 fill_wait 秒沒回報 → 不馬上拿掉部位,改「等確認」再等 TRADE_CONFIRM_WAIT 秒(晚到的 T 成交照樣接成持倉)
        self.confirm_wait = float(cfg.get("TRADE_CONFIRM_WAIT", "60"))
        # 2026-10-29 稽核 #1:進場通知的時間(t,發通知那分鐘)比現在舊超過幾分鐘就不進場
        self.sig_max_age = float(cfg.get("TRADE_SIGNAL_MAX_AGE_MIN", "5"))
        # 2026-10-22 使用者:「30 元做空反漲到 30.5 觸發平倉,直接掛漲停價贖回」:平倉空單掛漲停(H)買回、多單掛跌停(L)賣出,ROD
        self.exit_mode = cfg.get("TRADE_EXIT_MODE", "limitup")
        # 2026-10-28 使用者「這1取消 所有都不預掛 只要股價觸發停利/停損 程式立刻啟動強制平倉機制」:預設不掛元大條件單(1 = 舊做法)
        self.broker_stop = cfg.get("TRADE_BROKER_STOP", "1") == "1"   # 2026-11-08 00:14 使用者「改」(電腦斷線時元大自己守停利停損):預設改成掛元大停損停利條件單(二擇一)
        # 2026-10-28 使用者「+6.5 是針對做空的股票 -6.5 是針對做多的股票 全部股票都列入管理」:離昨收 ±6.5%(對部位不利的方向)→ 程式立刻全部平倉
        self.max_move = float(cfg.get("TRADE_MAX_MOVE_PCT", "6.5"))
        # 2026-10-28 使用者「如果30秒內沒有強制平倉成功 則再次檢查元大 是平倉單還掛著未成交 還是強制平倉訊息沒傳到元大 前者再等 後者再掛一次」
        self.verify_wait = float(cfg.get("TRADE_VERIFY_WAIT", "30"))
        self.max_resend = int(cfg.get("TRADE_MAX_RESEND", "3"))     # 平倉單最多自動重送幾次(之後推播請手動)
        # 2026-10-28 12:27 使用者「我在元大自己下的股票 成交後也顯示在這邊 受判三管制」→「這個都下單的叫做 元三」→「改」:
        #   不是程式送的單成交、這檔程式又沒有部位 → 接成「元三」部位,停利 / 停損用判三的 %(TRADE_Y3_TP_PCT / TRADE_Y3_SL_PCT,從成交價算),±6.5%、13:20、強平照樣管
        self.y3 = cfg.get("TRADE_Y3", "1") == "1"
        self.oco_grace = float(cfg.get("TRADE_OCO_GRACE", "15") or 0)   # 2026-11-08 01:26 使用者「改」:價格碰到停利 / 停損 N 秒部位還在(條件單沒觸發)→ 程式刪條件單自己平;0 = 關
        self.y3_tp = float(cfg.get("TRADE_Y3_TP_PCT", "3"))   # 2026-11-06 15:29 使用者「改一樣」:2.5 → 3(判三 TAKE=3)
        self.y3_sl = float(cfg.get("TRADE_Y3_SL_PCT", "4.5"))
        self.force_exit = cfg.get("TRADE_FORCE_EXIT", "13:20")
        self.ignore = set(x.strip() for x in cfg.get("TRADE_IGNORE_CODES", "YY0070").split(",") if x.strip())   # 2026-10-28 庫存裡下市、不能賣的股票:元三不接、13:22 複查不平
        self.last_entry = cfg.get("TRADE_LAST_ENTRY", "13:10")
        self.recheck_times = [x.strip() for x in cfg.get("TRADE_RECHECK", "13:22,13:23,13:24").split(",") if x.strip()]
        self.recheck_at = self.recheck_times[0] if self.recheck_times else "13:23"
        self.recheck_close = cfg.get("TRADE_RECHECK_CLOSE", "all")
        # 2026-11-05 13:29 使用者「程式要檢查 我是否預掛」→「改」:13:20 時手動預掛的平倉單(方向對、張數剛好等於未沖銷張數)保留,程式不另外平;
        #   到 TRADE_PREHANG_DEADLINE(13:24)還沒成交 → 取消那張、程式照舊漲停 / 跌停平倉。0 = 關(照舊 13:20 全部取消)
        # 2026-11-08 01:08 使用者「13點20分也要取消所有元大預掛」:預設 13:24 → 13:20(盤中照舊讓位給預掛;13:20 預掛和條件單、委託全部取消,程式平倉)
        self.prehang_deadline = cfg.get("TRADE_PREHANG_DEADLINE", "13:20").strip()
        self.j2_brcap = [(float(a_), int(b_)) for a_, b_ in (x.split(":") for x in cfg.get("TRADE_J2_BRCAP", "").split(",") if ":" in x)]   # 2026-11-10 01:45 判二第二十六次修改:預設關(改用 TRADE_J2_BRRANGE)
        # 2026-11-10 01:45 判二第二十六次修改(使用者「判二 贏單 大盤寬度上下多少可以涵蓋所有贏單」→「解鎖 改」):TRADE_J2_BRRANGE="short:0.044:0.881,long:0.106:0.92",空字串 = 關(01:52 使用者「解鎖改 判二做多:10.6%~92%」做多上限 95.7% → 92%)
        self.j2_brng = {}
        for x_ in cfg.get("TRADE_J2_BRRANGE", "short:0.044:0.881,long:0.106:0.92").split(","):
            try: k_, lo_, hi_ = x_.split(":"); self.j2_brng[k_.strip()] = (float(lo_), float(hi_))
            except Exception: pass   # 2026-11-09 判二大盤分級檔數(見 reset_day 說明)
        self.br_file = Path(cfg.get("TRADE_BR_FILE") or (Path(__file__).resolve().parent / "engine_data" / "breadth.json")); self._br = (0.0, None)
        self.prehang = {}   # code → 保留的手動預掛委託書號 [no, …]
        self.del_q = []     # 2026-11-08 20:50 等元大確認刪除的條件單編號
        self.man_cards = {}  # 2026-11-06 14:26 手動預掛(新進場 / 加碼)發過卡的書號 → {lots, px, d, end}
        self.man_meta = {}  # 2026-11-06 手動預掛的書號 → {bs, typ}(強平時取消用)
        self.ph_cancel = {}  # 2026-11-07 23:34 強平時程式取消掉的手動委託書號 → 時間;取消前一兩秒它還是成交了 → 多出來的部位程式馬上再強平
        self.noshort = dict(self.load_state().get("noshort") or {})   # 2026-11-06 今天元大回 21352(無券)的股票 → 原因;之後的空單訊號不再送
        self.alert = lambda title, body="", kind="pc": None      # bridge 會換成「發提醒到手機」(kind:pc = 一般、lv1~lv5 = 虧損分級)
        self.post = lambda title, body, kind, t=None: None    # bridge 會換成「寫一則交易訊息到手機的訊息欄」(強三進出場、強平)
        self.ref_of = lambda code: None                # bridge 會換成「這檔的昨收」(±6.5% 用)
        self.name_of = lambda code: code               # bridge 會換成「這檔的名稱」
        self.want_sub = lambda code: None              # 2026-10-28 平倉檢查 #7:bridge 會換成「這檔補訂閱逐筆、回傳市場」(元三 / 接回的股票不在名單上也盯得到價)
        self.cancel_all = cfg.get("TRADE_CANCEL_ALL", "1") == "1"
        self.type_long = cfg.get("TRADE_TYPE_LONG", "0")           # 委託種類:0 現股
        self.type_short = cfg.get("TRADE_TYPE_SHORT", "9")         # 9 現股當沖委託控管(先賣後買)
        self.stop_file = stop_path(cfg)
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
        self.traded_keys = set(self.load_state().get("traded") or []) if getattr(self, "state_file", None) else set()   # 2026-11-09 存檔(判二大盤分級檔數要算今天真的進過幾檔,程式重開也記得)
        # 2026-11-09 14:45 判二第二十四次修改(使用者「上漲家數大於90%只限制做一檔 80%二檔 70%沒限制」→「解鎖 改」):
        #   判二進場那一刻看名單上漲家數比例(engine_data/breadth.json,和判二 / 判三 / 判四同一份):≥ 90% → 今天判二最多 1 檔、≥ 80% → 2 檔、以下不限;
        #   檔數只算真的有送單變成部位的(太晚了 / 超額 / 沒成交放棄的不算);TRADE_J2_BRCAP="0.9:1,0.8:2",空字串 = 關
        #   15:02 判二第二十五次修改(使用者「92%以上不做 其他都做呢」→「解鎖 改」):改成 TRADE_J2_BRCAP="0.92:0" = 上漲家數 ≥ 92% 判二多空都不進,其他照做
        self.skipped = set()       # 2026-11-02 今天沒變成部位的進場訊號(代碼|判別式|訊號那根),手機標「未交易」
        # 2026-11-03 和益 1709 判三 OBV 停利平倉卡在主程式訊息欄不見(使用者「和益 已經平倉 進場資訊欄 沒顯示平倉」):
        #   手機收到平倉通知才問 ?trade=pos,電腦已經照同一則通知平完 → 手機以為元大根本沒這檔、把平倉卡藏了。
        #   這裡記「程式自己平掉的部位」(代碼|判別式;強平、元大條件單 / 手動平的不算),?trade=pos 帶給手機,手機看到就照常顯示平倉卡
        try: self.closed_ok = set(self.load_state().get("closed_ok") or []) if getattr(self, "state_file", None) else set()
        except Exception: self.closed_ok = set()
        self.cmd_seen = {}         # 強制指令編號 → 結果(手機重送 / 滑兩次只做一次)
        self.oco_q = []
        # 2026-10-29 稽核 #1:啟動時要「成功」讀到一次手機通知(prime)才算準備好;沒準備好不自動進場、不理平倉通知
        self.primed = False
        self.start_hm = self.hm()
        # 2026-10-29 稽核 #3:今天已進場過的訊號(代碼|判別式|K 棒時間),同一個訊號換了發送分鐘重送也不再進;存狀態檔(程式重開還在)
        self.entered_keys = set(self.load_state().get("entered") or [])
        self.killed = bool(self.load_state().get("killed"))   # 2026-10-31 今天已經虧到 TRADE_KILL_LOSS 全部平倉過(程式重開還記得)
        self.loss_hit = {float(x) for x in (self.load_state().get("loss_hit") or [])}   # 2026-10-31 今天已經通知過的虧損級距
        self.win_hit = {float(x) for x in (self.load_state().get("win_hit") or [])}     # 2026-10-31 今天已經慶祝過的賺錢級距
        # 2026-10-31 使用者「增加一個開盤後檢查股票是否真能當沖的機制」:元大以「資格」類原因(不可當沖 / 暫停先賣後買 / 處置 / 全額交割…)拒絕進場單的股票,
        #   今天不再自動進場(元大才是最後的真相;官方名單、雲端 / 電腦複檢都漏掉時,第一次被拒就記住,不會整天一直送一直被拒);存狀態檔(程式重開還在)
        self.brk_block = dict(self.load_state().get("brk_block") or {})

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
                  "oco": {c: p["oco_no"] for c, p in self.positions.items() if p.get("oco_live") and p.get("oco_no")},   # 2026-11-10 15:16 使用者「改」:元大條件單查詢一直失敗(簽章不合)→ 掛著的條件單編號存檔,中繼重開接回時用它,不重複掛
                  "forced": {c: {"tp_pct": p.get("tp_pct"), "sl_pct": p.get("sl_pct")} for c, p in self.positions.items() if p.get("forced")}}
            old = self.load_state()
            for c, v in (old.get("forced") or {}).items():
                js["forced"].setdefault(c, v)                       # 已平倉的也留著(同一天程式重開接回時還找得到)
            js["entered"] = sorted(set(old.get("entered") or []) | self.entered_keys)   # 2026-10-29 稽核 #3
            js["killed"] = bool(getattr(self, "killed", False) or old.get("killed"))
            js["win_hit"] = sorted({float(x) for x in (old.get("win_hit") or [])} | getattr(self, "win_hit", set()))      # 2026-10-31 賺錢慶祝
            js["loss_hit"] = sorted({float(x) for x in (old.get("loss_hit") or [])} | getattr(self, "loss_hit", set()))   # 2026-10-31 虧損級距通知
            js["closed_ok"] = sorted(set(old.get("closed_ok") or []) | getattr(self, "closed_ok", set()))   # 2026-11-03 程式自己平掉的部位(手機照常顯示平倉卡)
            js["noshort"] = {**(old.get("noshort") or {}), **getattr(self, "noshort", {})}   # 2026-11-06 元大 21352 無券
            js["brk_block"] = {**(old.get("brk_block") or {}), **getattr(self, "brk_block", {})}   # 2026-10-31 元大拒絕(資格)的股票
            js["traded"] = sorted(getattr(self, "traded_keys", set()))   # 2026-11-09 判二大盤分級檔數
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
                 f"每筆 {"多空上限 " + format(self.cap, ",") + " ÷ 一張" if self.cap > 0 else str(self.qty) + " 張"}、同時最多 {self.max_pos if self.max_pos > 0 else '不限'} 檔、虧損上限 {self.max_loss:,.0f}、±{self.max_move}% 強制平倉、"
                 f"{'預掛元大條件單' if self.broker_stop else '不預掛條件單(程式盯價)'}")
        for it in later:
            self.on_signal(it)

    def resume(self, trades, order_tags, strategies, signals, working=(), order_types=None):
        """2026-10-22 使用者:「加 啟動時接回部位」:程式重開時,從元大今天的成交回報算出還沒平的部位,是這支程式開的就接回來管理。
        trades:[{code, bs 'B'/'S', lots, price, time, order_no}](時間舊到新);order_tags:{委託書號: BasketNo};
        strategies:[{no, code, status}](有效的條件單);signals:手機今天的通知(找停利 / 停損價)。
        只接「程式送的單」(BasketNo 以 T = 自動 / F = 強制 開頭)開的部位;手動在投資先生做的不管。今天已做筆數、已實現損益也一起算回來。
        2026-10-28 改成全部接回(最多 5 檔 + 強制單),回傳接回的代碼清單"""
        self._oco_saved = dict(self.load_state().get("oco") or {})   # 2026-11-10 開頭先讀(接回過程中存檔會蓋掉)
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
            if self.hm() < self.force_exit:   # 2026-11-08 01:00 盤中重開:元大上還掛著的手動平倉單 = 預掛(程式讓位、不掛條件單)
                for w in working:
                    c = w["code"]; n = net.get(c, 0)
                    if n and not str(w.get("tag") or "").strip() and w["bs"] == ("S" if n > 0 else "B") and w.get("no"):
                        self.prehang.setdefault(c, []).append(w["no"]); self.man_meta[w["no"]] = {"bs": w["bs"], "typ": w.get("typ", ""), "lots": w["lots"], "fl": 0, "px": w.get("px") or 0}
                        self.log(f"[交易] 接回:{c} 元大有手動預掛平倉 {w['lots']} 張(書號 {w['no']})→ 程式讓位、不掛條件單")
            for w in working:                                       # 2026-11-08 16:00 預掛扣額度:重開時元大上還掛著的手動新進場 / 加碼預掛也記起來(進場前先扣額度,不重發卡)
                no = w.get("no")
                if no and not str(w.get("tag") or "").strip() and no not in self.man_meta and no not in self.man_cards:
                    self.man_cards[no] = {"lots": w["lots"], "px": w.get("px") or 0, "d": 1 if w["bs"] == "B" else -1, "fl": 0, "code": w["code"]}
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
                    self.sub(p); p["y3_posted"] = True
                    if p["state"] == HOLDING:
                        mo = [x for x in strategies if x.get("code") == c]
                        if mo: p["oco_no"] = mo[-1]["no"]; p["oco_live"] = True
                        self.oco_refresh(p, "接回元三")   # 2026-11-08 01:00 元三接回也照規則掛 / 刪條件單
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
                if strategies is None:   # 2026-11-10 15:16 元大條件單查詢失敗 → 用狀態檔存下的編號(重開前程式掛的那張),不再當作沒有、重掛一張(兩張同時在,碰停損會平兩次)
                    sv = self._oco_saved.get(c)
                    oco = [{"code": c, "no": str(sv)}] if sv else []
                    self.log(f"[交易] 接回:{c} 元大條件單查不到 → " + (f"用存下的編號 {sv}" if sv else "狀態檔也沒有,當作沒有條件單"))
                else:
                    oco = [x for x in strategies if x.get("code") == c]
                if oco:
                    p["oco_no"] = oco[-1]["no"]; p["oco_live"] = True
                self.log(f"[交易] 接回部位 {c} {p['judge']} {'多' if d > 0 else '空'} {abs(n)} 張 均價 {avg:.2f} 停利 {p['tp'] or '?'} 停損 {p['sl'] or '?'};"
                         f"元大條件單 {'在(' + p['oco_no'] + ')' if oco else '沒有'}")
                if oco and (not self.oco_ok(c) or self.prehang.get(c)):   # 13:20 後接回 / 有預掛 → 元大上還在的條件單刪掉(有預掛的下面照沒預掛的張數重掛)
                    self.gw.delete_oco(p["oco_no"]); p["oco_live"] = False; self.log(f"[交易] 接回:{c} 已過 {self.force_exit} 或有預掛 → 刪除元大條件單 {p['oco_no']}")
                if (not oco or self.prehang.get(c)) and self.oco_ok(c) and p["tp"] and p["sl"]:
                    p["oco_no"] = None; self.oco_q.append(c)
                    self.gw.place_oco(c, p["mkt"], d, self.oco_lots(c), p["tp"], p["sl"])
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
                    ml, mc = p.get("man_lots", 0), p.get("man_cost", 0.0)   # 2026-11-02 元大的張數包含進場中的手動成交,扣掉才是程式這張單成交的
                    if n == 0 or abs(n) <= ml:
                        # 2026-10-31 改 C:元大確認 IOC 一張都沒成交 → 照樣可以改掛漲停 / 跌停補進場(現價還在訊號價 ±0.5% 內才補)
                        if (self.entry_fallback and not o.get("fallback") and o.get("tif") != "0" and not p.get("forced") and not p.get("close_after_entry")
                                and self.entry_fallback_send(p, o)):
                            self.log(f"[交易] 對帳:{c} 進場單等確認中,元大連續兩次都沒有這檔部位 → 確定 IOC 沒成交,已改掛補單"); continue
                        if self.man_only(p, "對帳:進場單確定沒成交"): continue
                        self.log(f"[交易] 對帳:{c} 進場單等確認中,元大連續兩次都沒有這檔部位 → 確定沒成交,拿掉"); p["order"] = None; self.positions.pop(c, None); self.mark_skip(p); continue
                    side = "B" if p["dir"] > 0 else "S"; ts = [t for t in trades if t["code"] == c and t["bs"] == side]
                    q = sum(t["lots"] for t in ts); avg = (sum(t["lots"] * t["price"] for t in ts) / q) if q else (self.last_px.get(c) or p["entry"])
                    np_ = abs(n) - ml; pc = avg * abs(n) * 1000 - mc; avg_p = pc / (np_ * 1000) if np_ > 0 and pc > 0 else avg
                    self.log(f"[交易] 對帳:{c} 進場單等確認中,元大有 {abs(n)} 張(手動 {ml})→ 程式這張當成成交 {np_} 張(均價 {avg_p:.2f}),轉持倉")
                    o["filled"] = np_ * 1000; o["cost"] = avg_p * np_ * 1000; o["dead"] = True; self.after_report(p); continue
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
                        if p.get("oco_live"): self.gw.delete_oco(p["oco_no"]); p["oco_live"] = False; self.log(f"[交易] 對帳:{c} 部位沒了 → 刪除元大條件單 {p['oco_no']}")
                        if n == 0: continue
                    else:
                        self.log(f"[交易] 對帳:{c} 程式記 {abs(mine)} 張、元大 {abs(n)} 張 → 以元大為準"); p["qty"] = abs(n); self.oco_refresh(p, "對帳張數以元大為準"); continue
                d = 1 if n > 0 else -1; px = self.last_px.get(c) or 0
                if opener.get(c, "")[:1] in ("T", "F", "R"):
                    self.log(f"[交易] !! 對帳:{c} 元大有 {abs(n)} 張{'多' if d > 0 else '空'}單、程式不知道(程式的單晚到 / 多平)→ 馬上平掉")
                    self.alert(f"⚠ {c} 對帳發現 {abs(n)} 張沒管到", f"程式的單晚到成交 / 多平,已{'跌停賣出' if d > 0 else '漲停買回'}")
                    self.rebuy(c, d, abs(n), px or 0, None, otyp.get(c) or None)
                elif any(t.get("order_no") in self.ph_cancel for t in trades if t["code"] == c and t["bs"] == ("B" if d > 0 else "S")):
                    # 2026-11-07 23:38 使用者「再補」:漏了回報、對帳才看到的部位,其實是強平時程式取消掉的預掛(取消前成交)→ 不接成元三,馬上再強平
                    self.log(f"[交易] !! 對帳:{c} 元大有 {abs(n)} 張{'多' if d > 0 else '空'}單,是強平時取消掉的手動預掛成交的 → 馬上再強平")
                    self.alert(f"⚠ {self.name_of(c)} {c} 預掛取消前成交了", f"對帳才發現多出 {abs(n)} 張{'多' if d > 0 else '空'}單,程式已再強平({'跌停賣出' if d > 0 else '漲停買回'})")
                    self.rebuy(c, d, abs(n), px or 0, None, otyp.get(c) or None)
                elif self.y3 and self.enabled:
                    p = self.new_pos(c, d, MANUAL, "元大手動(對帳接回)", forced=True)
                    if otyp.get(c): p["typ"] = otyp[c]
                    avg = px
                    p.update({"qty": abs(n), "entry": avg, "state": HOLDING, "t0": self.hm(), "tp_pct": self.y3_tp, "sl_pct": self.y3_sl})
                    cs = [t for t in trades if t["code"] == c and t["bs"] == ("B" if d > 0 else "S")]
                    if cs: p["entry"] = sum(t["price"] * t["lots"] for t in cs) / sum(t["lots"] for t in cs)
                    self.set_forced_tpsl(p, p["entry"]); self.sub(p); p["y3_posted"] = True; self.oco_refresh(p, "對帳接回元三")
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
    def check_kill(self):
        """2026-10-31 當天虧損保險:已平倉損益 + 持倉帳面(現價、扣手續費 / 稅)≤ −TRADE_KILL_LOSS → 全部平倉(漲停買 / 跌停賣),今天不再自動進場"""
        if self.killed or self.hm() < "09:00" or (self.kill_loss <= 0 and not self.loss_alerts and not self.win_alerts):
            return
        with self.lock:
            unreal = 0.0
            for p in self.positions.values():
                if p["state"] != HOLDING: continue
                px = self.last_px.get(p["code"]) or p["entry"]; lots = p["qty"]
                buy, sell = (p["entry"], px) if p["dir"] > 0 else (px, p["entry"])
                unreal += (px - p["entry"]) * p["dir"] * lots * 1000 - self.cost_amt(buy, sell, lots)
            total = self.pnl + unreal
            kill = self.kill_loss > 0 and total <= -self.kill_loss
            # 2026-10-31 虧損級距通知(1 萬 / 2 萬 / 3 萬 / 4 萬 / 5 萬):每一級一天一次;同時到全部平倉的那級不另發(全部平倉那則已經寫了)
            new = [lv for lv in self.loss_alerts if total <= -lv and lv not in self.loss_hit]
            if new:
                self.loss_hit |= set(new); self.save_state()
                show = [x for x in new if not (kill and x >= self.kill_loss)]
                if show:
                    # 2026-10-31 使用者「浮輸1,2,3,4 訊息通知 要有危機感 一則要比一則嚴重」:第幾級(1 萬 = 1 … )標題、內容、手機聲音 / 震動一級比一級強(kind lv1~lv5)
                    amt = max(show); n = self.loss_alerts.index(amt) + 1; w = lambda v: f"{v / 10000:g} 萬"
                    head = {1: "⚠ 注意", 2: "⚠⚠ 警告", 3: "🔴🔴🔴 危險", 4: "🚨🚨🚨🚨 緊急"}.get(n, "🚨🚨🚨🚨🚨 最高警戒")
                    body = f"已平倉 {self.pnl:+,.0f}、持倉帳面 {unreal:+,.0f},合計 {total:+,.0f}"
                    if n >= 2 and self.max_loss > 0:
                        body += ";已停止新進場" if self.pnl <= -self.max_loss else f";已平倉虧到 {w(self.max_loss)} 會停止新進場"
                    if self.kill_loss > 0:
                        body += f";再虧 {self.kill_loss + total:,.0f} 就全部平倉" + ("!!" if n >= 4 else "")
                    if n >= 3:
                        body += ";要停手請長按標題啟動緊急停止"
                    self.log(f"[交易] 當天虧損達 {w(amt)}(第 {n} 級):{body}")
                    self.alert(f"{head}:當天虧損 {w(amt)}", body, f"lv{min(n, 5)}")
            # 2026-10-31 賺錢慶祝(1 萬 / 2 萬 / … / 10 萬):每一級一天一次(回落再漲回去不重發);一次跳好幾級只發最大的;kind win1~win5(手機綠 → 金、提示音 / 震動越來越熱鬧)
            gain = [lv for lv in self.win_alerts if total >= lv and lv not in self.win_hit]
            if gain:
                self.win_hit |= set(gain); self.save_state()
                amt = max(gain); n = self.win_alerts.index(amt) + 1; w = lambda v: f"{v / 10000:g} 萬"
                head = {1: "🎉 恭喜", 2: "🎉🎉 太棒了", 3: "🎊🎉🎉🎉 大豐收", 4: "🏆🎉🎉🎉🎉 超強"}.get(n, "💰🏆🎆🎉🎊 名揚四海")
                body = f"已平倉 {self.pnl:+,.0f}、持倉帳面 {unreal:+,.0f},合計 {total:+,.0f}"
                if unreal > 0 and unreal >= total / 2:
                    body += ";大部分還在帳面上,還沒落袋"
                self.log(f"[交易] 當天賺到 {w(amt)}(第 {n} 級):{body}")
                self.alert(f"{head}!當天賺 {w(amt)}", body, f"win{min(n, 5)}")
            if not kill:
                return
            self.killed = True; self.save_state()
            why = f"當天虧 {-total:,.0f}(上限 {self.kill_loss:,.0f})全部平倉"
            self.log(f"[交易] !!!! {why}:已平倉 {self.pnl:+,.0f}、持倉帳面 {unreal:+,.0f};今天不再自動進場")
            self.alert("🚨🚨🚨🚨🚨 當天虧損到上限,全部平倉", f"已平倉 {self.pnl:+,.0f}、持倉帳面 {unreal:+,.0f},合計 {total:+,.0f};今天不再自動進場", "lv5")
            self.close_all(why)

    def close_all(self, why, lock=False):
        """全部平倉(漲停買回 / 跌停賣出);進場單還沒成交的,成交轉持倉後馬上平。回傳處理的檔數。
           2026-10-31 虧到 TRADE_KILL_LOSS 和手機「緊急停止 + 全部平倉」共用"""
        n = 0
        with self.lock:
            for p in list(self.positions.values()):
                if p["state"] in (HOLDING, ENTERING) and lock: self.locked.add(p["code"])   # 2026-11-03「只全部平倉」:自動進場照常,但平掉的這幾檔今天不再自動進(和強平一樣)
                if p["state"] == HOLDING: self.exit_pos(p, why, self.last_px.get(p["code"]) or p["entry"], force_limit=True, kill_ph=True); n += 1
                elif p["state"] == ENTERING and lock:
                    # 2026-11-03 11:45 使用者「進場單還沒成交完 … 應該是還沒成交撤銷吧」:只全部平倉 → 進場單沒成交的部分撤銷;已經成交的那幾張馬上平(和強平進場中一樣)
                    p["forced_close"] = True; p["close_after_entry"] = True; n += 1
                    o = p.get("order")
                    if o and not o.get("dead"):
                        if o.get("no") and not o.get("cancel_sent"): o["cancel_sent"] = True; self.gw.cancel(o["no"], p["code"], o["side"], o["typ"])
                        else: o["cancel_after"] = True
                elif p["state"] == ENTERING: p["exit_pending"] = why; n += 1   # 進場單還沒成交:成交轉持倉後馬上平
                elif p["state"] == EXITING and self.prehang.get(p["code"]):
                    # 2026-11-08 20:45 檢查:只平沒預掛那幾張的途中(ph_part)按緊急全平 → 預掛也要取消,平倉單成交後剩下的(原預掛張數)照全平送出
                    self.cancel_ph(p, why); n += 1
            if lock and n: self.save_state()
        return n

    def on_tick(self, code, price):
        self.last_px[code] = price
        self.check_kill()
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
            # 2026-11-03 13:11 使用者「以後如果下跌6.5 不管元買 或是元賣 強買或賣 都不受6.5%管束」:元三(元大手動)、強三(手機強制進場)做多做空都不套 ±6.5%,只用停利 / 停損 / 13:20;判一~四照舊
            #   13:17 使用者「那就改 該股票+-8.5% 為停損」:元三 / 強三改用離昨收 ±8.5%(做多跌到 −8.5%、做空漲到 +8.5%)強制平倉,不是完全不管
            man = p.get("judge") in (MANUAL, FORCED) and self.man_move > 0
            mm = self.man_move if man else self.j7_move if p.get("judge") in ("判七", "判四", "判五") and self.j7_move > 0 else self.max_move   # 2026-11-09 15:43 判七第一次修改(使用者「解鎖改」):判七空單漲到昨收 +8% 才強平(其他判別式照舊 ±6.5%)
            hit_mv = (man or not p.get("no_max_move")) and ref > 0 and mm > 0 and ((price <= ref * (1 - mm / 100)) if d > 0 else (price >= ref * (1 + mm / 100)))
            if not (hit_tp or hit_sl or hit_mv):
                return
            why = f"超{mm:g}%" if hit_mv else "停利" if hit_tp else "停損"
            if p.get("oco_live") and not hit_mv:
                if not p.get("oco_noted"):
                    p["oco_noted"] = True; p["oco_hit_at"] = time.time(); p["oco_hit_why"] = why
                    self.log(f"[交易] {code} 價格 {price} 碰到{why},由元大條件單(OCO)平倉" + (f"({self.oco_grace:g} 秒內沒平掉由程式平)" if self.oco_grace > 0 else ""))
                return
            self.exit_pos(p, why, price, force_limit=True)
            if hit_mv and p["state"] == EXITING:   # 讓位給手動預掛時沒平,不要每個價都提醒
                self.alert(f"⚠ {code} 離昨收 {'−' if d > 0 else '+'}{mm:g}% 系統強制平倉", f"{'做多跌停賣出' if d > 0 else '做空漲停買回'} {p['qty']} 張")

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
                        if p2 and p2.get("ioc_tag") and p2["ioc_tag"] == str(basket).strip() and p2["state"] == ENTERING and p2.get("pre_filled") is not None:
                            # 2026-10-31 改 C:IOC 已結束、補單掛著時,IOC 晚到的成交 → 算進補單前的張數(補單成交後一起合併)
                            p2["pre_filled"] += qty_shares; p2["pre_cost"] = p2.get("pre_cost", 0.0) + qty_shares * price
                            self.log(f"[交易] {p2['code']} IOC 晚到的成交 {qty_shares // 1000} 張 @ {price} → 算進部位(補單還掛著)"); return
                        if p2 and p2.get("entry_tag") and p2["entry_tag"] == str(basket).strip() and p2["state"] in (HOLDING, EXITING):
                            # 2026-10-29 稽核 #5:IOC 進場已部分成交轉持倉後,同一張進場單剩下的晚到成交 → 併進部位(張數、均價)
                            lots = qty_shares // 1000; tot = p2["qty"] + lots
                            p2["entry"] = (p2["entry"] * p2["qty"] + price * lots) / tot if tot else p2["entry"]; p2["qty"] = tot; self.amount_today += price * lots * 1000
                            self.log(f"[交易] {p2['code']} 進場單({p2['entry_tag']})晚到的成交 {lots} 張 @ {price} → 併進部位,共 {tot} 張 均價 {p2['entry']:.2f}")
                            if p2["state"] == HOLDING: self.oco_refresh(p2, "進場晚到成交併入")
                            if p2["state"] == EXITING:
                                self.resize_exit(p2, "平倉中又收到進場單晚到的成交")
                            return
                        self.log(f"[交易] {code} 程式舊委託({str(basket).strip()})晚到的成交 {qty_shares // 1000} 張 @ {price},只記錄(13:22 複查以元大為準)")
                        return
                    self.on_other_fill(str(code).strip(), bs, qty_shares, price, str(order_no or "").strip(), typ)
                elif rpt_type == 50 and code and str(basket or "").strip()[:1] not in ("T", "F", "R"):
                    self.man_order(str(code).strip(), str(order_no or "").strip(), status, qty_shares, price, bs, typ)
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
                o["dead"] = True; o["end_status"] = status
                self.log(f"[交易] {p['code']} 委託結束 狀態 {status}({ {1: '委託失敗', 2: '取消成功', 24: '委託失效', 25: '價穩失效'}[status] })")
            self.after_report(p)

    def no_short(self, code, why):
        """2026-11-06 做空被元大 21352 擋(程式或手動):這檔今天不再送空單,手機提醒一次"""
        with self.lock:
            if self.noshort.get(code): return
            self.noshort[code] = why; self.save_state()
        self.log(f"[交易] {code} {why} → 今天不再送這檔的空單")
        try: self.alert(f"⚠ {self.name_of(code)} {code} 無券可空", f"{why};今天這檔的做空訊號只通知、不下單")
        except Exception: pass

    def man_order(self, code, no, status, qty_shares, price, bs, typ=""):
        """2026-11-06 11:32 使用者「大聯大 預掛 程式沒有提示」→ 選「程式讓位給預掛」:程式持倉中,元大手動掛了平倉方向的單 →
           記成預掛(prehang)、通知手機;預掛還在時程式的停利 / 停損 / ±% / 平倉通知都先不平(只靠預掛出場),13:20 / 13:24 照舊處理;
           預掛取消 / 失效 → 拿掉、通知手機,程式恢復盯停利停損。手機強平不受影響(先取消預掛再平)"""
        if not no: return
        p = self.positions.get(code); lst = self.prehang.get(code) or []
        # 2026-11-06 14:26 使用者「元大這邊 … 進場通知欄 可以看到我在程式裡強平 或是在元大手動交易 … 手動預掛 進場通知欄也可以看到預掛卡」:
        #   不是平程式部位的手動預掛(新進場 / 加碼)也發預掛卡;取消 / 失效 / 被退回再發一張;成交照舊變元三進場卡
        if no not in lst and not (p and p["state"] == HOLDING and bs == close_side(p["dir"])):
            mc = self.man_cards.get(no)
            if status == 0 and mc is None:
                lots = qty_shares // 1000; d = 1 if bs == "B" else -1
                what = ("預掛加碼" if p and p["dir"] == d else "預掛") + ("做多" if d > 0 else "做空")
                self.man_cards[no] = {"lots": lots, "px": price, "d": d, "fl": 0, "code": code}
                self.log(f"[交易] {code} 看到手動{what} {lots} 張 @ {price or '市價'}(書號 {no})→ 進場欄發預掛卡,成交後變元三")
                try: self.post(f"{self.name_of(code)} {code} {what} {price:g}" if price else f"{self.name_of(code)} {code} {what} 市價",
                               f"{self.hm()} {'做多' if d > 0 else '做空'} · {lots}張 · 預掛 · 元三", "warn")
                except Exception as e: self.log(f"[交易] 預掛卡發送失敗 {e!r}")
                try: self.alert(f"📌 {self.name_of(code)} {code} {what} {price:g}" if price else f"📌 {self.name_of(code)} {code} {what} 市價", f"{lots} 張,成交後變元三由程式管")   # 2026-11-07 10:36 使用者「改」:新進場 / 加碼預掛手機也跳通知
                except Exception as e: self.log(f"[交易] 預掛通知失敗 {e!r}")
            elif status in (1, 2, 24, 25) and mc and not mc.get("end"):
                mc["end"] = status; why = {1: "被元大退回", 2: "已取消", 24: "失效", 25: "價穩失效"}[status]
                try: self.post(f"{self.name_of(code)} {code} 預掛{why}", f"{self.hm()} {'做多' if mc['d'] > 0 else '做空'} · {mc['lots']}張 @ {mc['px'] or '市價'} · 預掛 · 元三", "warn")
                except Exception as e: self.log(f"[交易] 預掛卡發送失敗 {e!r}")
            return
        if status in (1, 2, 24, 25):
            if no in lst:
                lst.remove(no)
                if not lst: self.prehang.pop(code, None)
                self.man_meta.pop(no, None)
                if p and p["state"] == HOLDING:
                    self.log(f"[交易] {code} 手動預掛 {no} 結束(狀態 {status})→ 程式恢復盯停利 {p['tp']} / 停損 {p['sl']}")
                    self.alert(f"↩ {self.name_of(code)} {code} 預掛單沒了", f"程式恢復盯停利 {p['tp']} / 停損 {p['sl']}")
                    if not lst: self.oco_refresh(p, "預掛撤除 → 條件單補回")   # 2026-11-08 00:55 使用者「預掛撤除 條件單再補上去」
                    # 2026-11-07 10:14 使用者「好」:平倉預掛取消 / 失效也發一張卡,手機進場卡上的黃色「預掛」標籤跟著拿掉
                    try:
                        why = {1: "被元大退回", 2: "已取消", 24: "失效", 25: "價穩失效"}.get(status, "已取消")
                        self.post(f"{self.name_of(code)} {code} 預掛{why}", f"{self.hm()} {'做多' if p['dir'] > 0 else '做空'} {p.get('t0') or ''} 進 {p['entry']:.2f} · 預掛 · {p.get('judge') or ''} · 元大中繼", "warn")
                    except Exception as e: self.log(f"[交易] 預掛卡發送失敗 {e!r}")
            return
        if status != 0 or not p or p["state"] != HOLDING or bs != close_side(p["dir"]) or no in lst: return
        lst.append(no); self.prehang[code] = lst; self.man_meta[no] = {"bs": bs, "typ": typ, "lots": qty_shares // 1000, "fl": 0, "px": price or 0}
        self.oco_refresh(p, "手動預掛:預掛的張數不掛條件單")   # 2026-11-08 00:55 使用者「預掛當下 條件單直接撤除」;18:30 只預掛一部分 → 刪掉重掛沒預掛的張數
        lots = qty_shares // 1000
        self.log(f"[交易] {code} 看到手動預掛平倉 {'買回' if bs == 'B' else '賣出'} {lots} 張 @ {price}(書號 {no})→ 程式讓位:停利 / 停損先不平,等預掛成交({self.prehang_deadline} 前沒成交由程式平)")
        self.alert(f"✅ {self.name_of(code)} {code} 看到你的預掛", f"{'買回' if bs == 'B' else '賣出'} {lots} 張 @ {price}(程式部位 {p['qty']} 張);程式停利 / 停損先不動,{self.prehang_deadline} 前沒成交由程式平")
        # 2026-11-06 11:34 使用者「程式在進場那邊 要出現大聯大 預掛 如果成交 就改成元三」:訊息欄發一張預掛卡;成交平倉時(finish_pos)再發一張元三平倉卡
        try: self.post(f"{self.name_of(code)} {code} 預掛{'買回' if bs == 'B' else '賣出'} {price:g}",
                       f"{self.hm()} {'做多' if p['dir'] > 0 else '做空'} {p.get('t0') or ''} 進 {p['entry']:.2f} · {lots}張 · 預掛 · {p.get('judge') or ''} · 元大中繼", "warn")   # 結尾「元大中繼」:Worker 當真實卡(看盤版看不到),虛擬也不列
        except Exception as e: self.log(f"[交易] 預掛卡發送失敗 {e!r}")
        if lots != p["qty"]: self.alert(f"⚠ {code} 預掛張數 {lots} 和程式部位 {p['qty']} 張不一樣", "成交後剩下的(或多出來的)請自己確認")

    def on_other_fill(self, code, bs, qty_shares, price, order_no="", typ=""):
        """不是這支程式送的單成交了:手動開新部位(元三)、手動加碼、手動 / 條件單平倉,或程式平倉時條件單也同時觸發(多平了)"""
        p = self.positions.get(code)
        close_side = lambda d: "B" if d < 0 else "S"
        lots = qty_shares // 1000
        mc = self.man_cards.get(order_no) if order_no else None
        if mc is not None: mc["fl"] = mc.get("fl", 0) + lots   # 2026-11-08 16:00 預掛扣額度:手動預掛(新進場 / 加碼)成交的張數不再算掛著
        # 2026-11-07 23:34 使用者「如果真的出現這樣情況 電腦需要再次出手強平」:強平時程式取消的手動預掛,取消前還是成交了 →
        #   不接成元三,多出來的部位馬上再強平(做多跌停賣、做空漲停買)
        if order_no and order_no in self.ph_cancel and lots > 0 and not (p and p["state"] in (HOLDING, EXITING) and bs == close_side(p["dir"])):
            d = 1 if bs == "B" else -1
            self.log(f"[交易] !! {code} 強平時取消的手動委託 {order_no} 還是成交了 {lots} 張 @ {price} → 多出 {lots} 張{'多' if d > 0 else '空'}單,馬上再強平")
            self.alert(f"⚠ {self.name_of(code)} {code} 預掛取消前成交了", f"多出 {lots} 張{'多' if d > 0 else '空'}單,程式已再強平({'跌停賣出' if d > 0 else '漲停買回'})")
            self.flatten_later(code, d, lots, price, typ or None); return
        if p and bs != close_side(p["dir"]) and p["state"] in (HOLDING, EXITING) and lots > 0:
            # 平倉檢查 #5:元三同方向再成交(同一張分批成交,或另外下一張加碼)→ 併進去、成本平均,停利 / 停損照新均價
            # 2026-11-02 11:43 使用者「新做空的股票我需要程式一併接手可以嗎」:判一~四 / 強三的部位,元大手動同方向加碼也併進去一起管
            #   (張數加總、均價重算;停利 / 停損從新均價重算(判一~四照原本離進場價的 %,強三 / 元三照設定 %);平倉時全部一起平)
            tot = p["qty"] + lots; e0 = p["entry"]
            p["entry"] = (p["entry"] * p["qty"] + price * lots) / tot; p["qty"] = tot; self.amount_today += price * lots * 1000
            p["y3_lots"] = max(p.get("y3_lots") or 0, tot); p["y3_px"] = p["entry"]
            if p.get("judge") in (MANUAL, FORCED) and (p.get("tp_pct") or p.get("sl_pct")): self.set_forced_tpsl(p, p["entry"])
            elif e0 > 0:   # 2026-11-02 11:45 使用者「這邊需要重新再算過停利跟停損」:判一~四照原本停利 / 停損離進場價的 % 不變,從新均價重算
                if p.get("tp"): p["tp"] = round(p["entry"] * p["tp"] / e0, 2)
                if p.get("sl"): p["sl"] = round(p["entry"] * p["sl"] / e0, 2)
            self.log(f"[交易] {p['judge']} {code} 元大手動同方向成交 {lots} 張(委託 {order_no})→ 併進程式部位,共 {tot} 張 均價 {p['entry']:.2f} 停利 {p['tp']} 停損 {p['sl']}")
            if p.get("y3_card") and p.get("judge") == MANUAL:
                # 2026-11-10 13:37(實際時間)使用者「改」:元三進場卡發出後同一張 / 另一張同方向又成交(友達 10:22 卡寫 2 張、實際 8 張)
                #   → 用同一個鍵(同一分鐘 + 同標題去掉數字)重寫那張卡:總張數、新均價、新停利停損;Worker 同鍵覆蓋,不會多一張
                try:
                    dd = p["dir"]; self.post(f"{self.name_of(code)} {code} {'做多' if dd > 0 else '做空'}進場 {p['entry']:.2f}",
                                             f"{p.get('t0', '')} 元大 · 停利 {(p.get('tp') or 0):.2f} 停損 {(p.get('sl') or 0):.2f} · {p['qty']}張 · {MANUAL}", "long" if dd > 0 else "short", t=p["y3_card"]["t"])
                    self.log(f"[交易] 元三 {code} 進場卡改寫:{p['qty']} 張 均價 {p['entry']:.2f}")
                except Exception as e: self.log(f"[交易] 元三 {code} 進場卡改寫失敗 {e}")
            if p["state"] == HOLDING: self.oco_refresh(p, "手動加碼")
            if self.prehang.get(code):   # 2026-11-08 01:00 預掛期間又加碼 → 預掛張數不夠了,提醒
                ph = sum((self.man_meta.get(n) or {}).get("lots", 0) - (self.man_meta.get(n) or {}).get("fl", 0) for n in self.prehang[code])
                self.alert(f"ℹ {self.name_of(code)} {code} 加碼後預掛只蓋住一部分", f"部位 {tot} 張、預掛 {ph} 張;沒預掛的 {max(0, tot - ph)} 張程式管(條件單已照這個張數重掛),預掛的等成交({self.prehang_deadline} 前沒成交由程式平)")   # 2026-11-08 18:58 跟著 18:30 部分預掛規則改字
            if p.get("judge") != MANUAL:
                self.alert(f"✅ {self.name_of(code)} {code} 手動加碼 {lots} 張已交給程式", f"共 {tot} 張 均價 {p['entry']:.2f}(停利 {p['tp']} / 停損 {p['sl']}),平倉時全部一起平")
            # 2026-11-05 12:33 使用者「我是永久會員 這手動單 要加入進場」(宏碁手動加買 3 張,當沖有、進場沒有):手動加碼也在進場欄發一張卡(標「加碼」,張數 = 這次加的);
            #   同一張委託分批成交等 3 秒併成一張卡
            try:
              if not (order_no and str(order_no) == str(p.get("y3_no") or "")):   # 元三開倉那張委託分批成交的後幾筆不算加碼(開倉卡已經寫總張數)
                ak = (code, str(order_no or "")); A = self.__dict__.setdefault("_add_post", {})
                a0 = A.get(ak); first = a0 is None
                if first: a0 = A[ak] = {"lots": 0, "cost": 0.0, "t": self.hm(), "dir": p["dir"]}
                a0["lots"] += lots; a0["cost"] += price * lots
                if first:
                    def _post_add(ak=ak, code=code):
                        with self.lock:
                            x = self.__dict__.get("_add_post", {}).pop(ak, None); q = self.positions.get(code) or {}
                        if not x or x["lots"] <= 0: return
                        px = x["cost"] / x["lots"]
                        self.post(f"{self.name_of(code)} {code} {'做多' if x['dir'] > 0 else '做空'}進場 {px:.2f}",
                                  f"{x['t']} 元大 · 加碼 · 共 {q.get('qty', '?')} 張 均價 {float(q.get('entry') or 0):.2f} · {x['lots']}張 · {MANUAL}", "long" if x["dir"] > 0 else "short")
                    threading.Timer(max(0.0, self.y3_post_delay), _post_add).start()
            except Exception as e: self.log(f"[交易] 手動加碼發卡失敗 {e!r}")
            self.save_state()
            if p["state"] == EXITING:
                self.resize_exit(p, "平倉中又手動加碼")                 # 平倉單張數不夠了 → 取消重送全部
            return
        if p and bs != close_side(p["dir"]) and p["state"] == ENTERING and lots > 0:
            # 2026-11-02 11:47 使用者「不管是誰做的 只要有成交 程式一律接管」:程式的進場單還在送時手動同方向成交 → 先記著,進場單結束時一起併成同一個部位
            #   (程式的進場單沒成交也照樣把手動這幾張接成部位,停利 / 停損照訊號的 % 從手動均價算)
            p["man_lots"] = p.get("man_lots", 0) + lots; p["man_cost"] = p.get("man_cost", 0.0) + price * lots * 1000
            self.log(f"[交易] {p['judge']} {code} 進場中又有手動同方向成交 {lots} 張 @ {price}(委託 {order_no})→ 進場單結束時一起併進程式部位")
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
                # 2026-11-03 13:03 使用者(群創跌超過 6.5% 才手動做多,程式一接手就被 ±6.5% 平掉)選 1:手動單進場時已經超過 ±6.5%(做多跌超過、做空漲超過)→ 這筆不套 ±6.5%,只用停利 / 停損管
                ref0 = self.ref_of(code) or 0
                #   群創實際是 −6.4% 進場(還差一點才到 −6.5%)、20 秒後跌到 −6.6% 被平 → 「已經接近」也算:進場時往不利方向已經超過 ±(6.5 − 1.5)= ±5%(TRADE_Y3_NEAR_PCT)就不套
                mv0 = self.max_move - self.y3_near
                if ref0 > 0 and self.max_move > 0 and ((price <= ref0 * (1 - mv0 / 100)) if d > 0 else (price >= ref0 * (1 + mv0 / 100))):
                    p["no_max_move"] = True; self.log(f"[交易] 元三 {code} 進場時已經離昨收 {(price / ref0 - 1) * 100:+.1f}%(超過 ±{mv0:g}%)→ 這筆不套 ±{self.max_move:g}%,只用停利 / 停損")
                self.set_forced_tpsl(p, price); self.amount_today += price * lots * 1000; self.save_state()
                self.sub(p)
                self.log(f"[交易] 元三:元大手動{'買進' if d > 0 else '賣出'} {code} {lots} 張 @ {price}(委託種類 {typ or '?'})→ 交給判三管(停利 {p['tp']} 停損 {p['sl']})")
                # 2026-11-03 13:00 使用者「我做多是5張 訊息寫一張」:同一張委託分批成交(1+1+1+2)時,原本第一筆成交就發卡(寫 1 張)→ 等 3 秒把分批成交併完再發,寫總張數 / 均價
                p["y3_lots"], p["y3_px"] = lots, price
                def _post_y3(p=p, code=code, d=d):
                    with self.lock:
                        if p.get("y3_posted"): return
                        p["y3_posted"] = True; n = p.get("y3_lots") or p.get("qty") or lots; px = p.get("y3_px") or price
                        tp, sl, t0 = p.get("tp") or 0, p.get("sl") or 0, p.get("t0", "")
                    tc = self.hm(); p["y3_card"] = {"t": tc}   # 2026-11-10 記下發卡那分鐘:之後又分批成交 → 用同一個鍵(同一分鐘)重寫這張卡
                    self.post(f"{self.name_of(code)} {code} {'做多' if d > 0 else '做空'}進場 {px:.2f}",
                              f"{t0} 元大 · 停利 {tp:.2f} 停損 {sl:.2f} · {n}張 · {MANUAL}", "long" if d > 0 else "short", t=tc)
                    with self.lock: self.oco_refresh(p, "元三進場")   # 2026-11-08 元三也掛元大停損停利條件單(分批成交併完才掛)
                if self.y3_post_delay > 0: threading.Timer(self.y3_post_delay, _post_y3).start()
                else: _post_y3()
                return
        if p and p["state"] in (HOLDING, EXITING) and bs == close_side(p["dir"]):
            ph_done = False
            if order_no and order_no in (self.prehang.get(code) or []):
                self.__dict__.setdefault("_ph_filled", set()).add(order_no)
                m = self.man_meta.get(order_no) or {}; m["fl"] = m.get("fl", 0) + lots
                if m.get("lots") and m["fl"] >= m["lots"]:            # 2026-11-08 01:00 預掛這張成交完了 → 從預掛清單拿掉(剩下的部位程式恢復盯 + 補條件單)
                    lst = self.prehang.get(code) or []; lst.remove(order_no); self.man_meta.pop(order_no, None); ph_done = not lst
                    if not lst: self.prehang.pop(code, None)
            take = min(lots, max(p["qty"], 0)); extra = lots - take
            self.log(f"[交易] {code} 不是程式送的平倉單成交 {lots} 張 @ {price}(元大條件單 / 手動)")
            p["exit_why"] = p.get("exit_why") or "元大條件單 / 手動"
            # 2026-11-08 使用者「元三強三 變成 元平強平 條件單呢會自己消失嗎」→「改」:不是條件單自己觸發的平倉(元平:手動平倉 / 預掛成交)→
            #   元大的條件單不會自己消失 → 程式刪掉;只平一部分 → 照剩下張數重掛。價格到了停利 / 停損 = 條件單自己在平,不動它
            oco_hit = p.get("oco_live") and p.get("tp") and p.get("sl") and ((price >= p["tp"] or price <= p["sl"]) if p["dir"] > 0 else (price <= p["tp"] or price >= p["sl"]))
            self.book_close(p, take, price)
            if ph_done and p["state"] == HOLDING and p["qty"] > 0:
                self.log(f"[交易] {code} 手動預掛成交完,還剩 {p['qty']} 張 → 程式恢復盯停利 {p['tp']} / 停損 {p['sl']}")
                self.alert(f"↩ {self.name_of(code)} {code} 預掛成交完,還剩 {p['qty']} 張", f"程式恢復盯停利 {p['tp']} / 停損 {p['sl']}")
                self.oco_refresh(p, "預掛成交完 → 條件單補回剩下張數")
            if p.get("oco_live") and (not oco_hit or p["qty"] <= 0):   # 2026-11-08 00:55 部位歸零一律刪(條件單已觸發的刪了也無害;沒刪會留著開出反向新部位)
                if p["state"] == HOLDING and p["qty"] > 0 and self.positions.get(code) is p: self.oco_refresh(p, "元平一部分")
                else: self.gw.delete_oco(p["oco_no"]); p["oco_live"] = False; self.log(f"[交易] {code} 元平 → 刪除元大條件單 {p['oco_no']}")
            if extra > 0 and (p.get("forced_close") or (order_no and order_no in self.ph_cancel)):
                # 2026-11-07 23:34 強平中(預掛取消前成交 / 程式平倉單也成交)多出來的反向部位 → 程式自己再強平
                self.alert(f"⚠ {self.name_of(code)} {code} 平倉多了 {extra} 張", f"強平時預掛也成交,多出 {extra} 張反向部位,程式已再強平")
                self.flatten_later(code, 1 if bs == "B" else -1, extra, price, typ or None)
            elif extra > 0:
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

    def flatten_later(self, code, d, lots, price, typ=None, tries=20):
        """2026-11-07 多出來的部位要再強平:這檔程式還有部位在收尾時先等它結束(每 1.5 秒看一次,最多 30 秒),再用 rebuy 漲停 / 跌停平掉"""
        def go(n=tries):
            with self.lock:
                q = self.positions.get(code)
                if q and n > 0:
                    threading.Timer(1.5, go, (n - 1,)).start(); return
                if q:
                    self.alert(f"⚠⚠ {code} 多出的 {lots} 張還沒平", "程式部位一直沒結束,請立刻到投資先生手動平倉"); return
                self.rebuy(code, d, lots, price, None, typ); self.save_state()
        go()

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
        r = 0.001425 * float(self.cfg.get("TRADE_FEE_DISCOUNT", "0.25")); tx = 0.0015
        # 2026-11-07 20:41 使用者同意「元大真單的手續費估算跟設定走」:主導機設定頁的 證商費 × 手續折扣 + 證手費、證交稅(cap=get 的 set,開盤前讀);讀不到才用上面的固定值
        try:
            pc = lambda k: float(re.sub(r"[^\d.]", "", str(CLOUD_SET[k]))) / 100
            if all(k in CLOUD_SET for k in ("broker", "disc", "tax")): r = pc("broker") * pc("disc") + (pc("exch") if "exch" in CLOUD_SET else 0); tx = pc("tax")
        except Exception: pass
        return math.floor(buy * lots * 1000 * r + 1e-6) + math.floor(sell * lots * 1000 * r + 1e-6) + math.floor(sell * lots * 1000 * tx + 1e-6)   # +1e-6:浮點誤差

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
        if not p.get("forced_close") and p.get("exit_why") != "元大條件單 / 手動" and p.get("judge"):   # 2026-11-03 程式自己平掉的 → 手機照常顯示平倉卡
            self.closed_ok.add(f"{p['code']}|{p['judge']}"); self.save_state()

    def finish_pos(self, p):
        c = p["code"]
        self.flat_watch[c] = {"mkt": p["mkt"], "dir": p["dir"], "typ": p["typ"], "until": time.time() + 120}
        self.positions.pop(c, None)
        for no in self.prehang.pop(c, []) if self.hm() < "13:20" else []:
            # 2026-11-08 00:55 部位已經結束(條件單 / 程式平掉)還掛著的手動預掛 → 取消;取消前成交的照「強平取消的預掛」再強平,不接成元三
            m = self.man_meta.pop(no, {}) or {}
            if no not in self.__dict__.setdefault("_ph_filled", set()):
                self.gw.cancel(no, c, m.get("bs") or ("B" if p["dir"] < 0 else "S"), m.get("typ") or p["typ"]); self.ph_cancel[no] = time.time()
                self.log(f"[交易] {c} 部位結束 → 取消還掛著的手動預掛 {no}")   # 2026-11-06 部位沒了,盤中記的預掛跟著清(13:20 後的由 prehang_pick / expire 管)
        # 2026-10-28 強三的出場、任何部位的強平 → 寫一則平倉訊息到手機訊息欄(判一 / 二 / 三自己的平倉訊息照舊由手機發)
        if (p.get("forced") or p.get("forced_close")) and p.get("judge") in ("判一", "判二", "判三", "判四", "判五", FORCED, MANUAL) and p["closed_lots"]:   # 2026-11-02 判四強平也要寫訊息(使用者「強制平倉 訊息欄要顯示」;欣銓 3264 判四強平沒出現)
            ex = p["closed_cost"] / p["closed_lots"]; d = p["dir"]
            pl = (ex - p["entry"]) / p["entry"] * 100 * d if p["entry"] else 0
            why = "強平" if p.get("forced_close") else (p.get("exit_why") or "出")
            body = f"{self.hm()} {'做多' if d > 0 else '做空'} {p.get('t0') or self.hm()} 進 {p['entry']:.2f} → 現價 {ex:.2f} · {p['closed_lots']}張" + (" · 強平" if p.get("forced_close") else "") + f" · {p['judge']}"
            # 2026-11-04 使用者「強平 還是要通知比較好吧」→「直接 富喬 1815 強平 +1.1%」:強平的標題改成「名稱 代碼 強平 ±x.x%」(手機收到會跳通知)
            ttl = f"{self.name_of(c)} {c} 強平 {'+' if pl >= 0 else '−'}{abs(pl):.1f}%" if p.get("forced_close") else f"{self.name_of(c)} {c} 出場 {'+' if pl >= 0 else '−'}{abs(pl):.1f}%:{why}"
            self.post(ttl, body, "exit")

        elif p.get("exit_why") == "元大條件單 / 手動" and p["closed_lots"] and not p.get("forced_close"):
            ex = p["closed_cost"] / p["closed_lots"]; d = p["dir"]
            pl = (ex - p["entry"]) / p["entry"] * 100 * d if p["entry"] else 0
            self.post(f"{'✅ ' if pl >= 0 else '⚠ '}{self.name_of(c)} {c} 出場 {'+' if pl >= 0 else '−'}{abs(pl):.1f}%:元大預掛成交",
                      f"{self.hm()} {'做多' if d > 0 else '做空'} {p.get('t0') or self.hm()} 進 {p['entry']:.2f} → 現價 {ex:.2f} · {p['closed_lots']}張 · 元平 · {p.get('judge') or ''}",   # 2026-11-07 10:36 使用者「元大手動平倉是不是應該叫做元平」→「改」   # 標原判別式才會和那張進場卡配成同一筆、算進輸贏;「· 強平 ·」讓手機藏掉之後引擎的虛擬出場
                      "profit" if pl >= 0 else "loss")

    def oco_ok(self, c):
        """2026-11-08 00:55 使用者:13:20 以後一律處在平倉狀態(接回 / 加碼都不再掛條件單);手動預掛跟條件單不會同時存在(有預掛就不掛)
           2026-11-08 18:30 使用者「取消4張條件單 再改掛6張條件單」:預掛只蓋住一部分 → 沒預掛的張數照掛條件單(同一張不會同時有預掛和條件單)"""
        return self.broker_stop and self.hm() < self.force_exit and self.oco_lots(c) > 0

    def oco_lots(self, c):
        """條件單該掛幾張 = 部位 − 還沒成交的預掛張數;預掛張數不知道 → 0(整個交給預掛)"""
        p = self.positions.get(c)
        if not p: return 0
        lst = self.prehang.get(c) or []
        if any(not (self.man_meta.get(n) or {}).get("lots") for n in lst): return 0
        return max(0, p["qty"] - self.ph_lots(c)) if lst else p["qty"]

    def oco_refresh(self, p, why=""):
        """2026-11-08 元大停損停利條件單跟著部位走:張數 / 停利停損變了 → 刪掉舊的照現在的重掛;部位沒了(元平平完)→ 只刪。
           元三開倉分批成交還沒併完(發卡前)先不掛,_post_y3 併完再掛;條件單還在等元大回應時,回來後再重掛一次"""
        if not self.broker_stop or not p: return
        c = p["code"]
        if p.get("judge") == MANUAL and not p.get("y3_posted") and p["state"] == HOLDING and p["qty"] > 0 and not p.get("oco_live"): return
        if p.get("oco_no") is None and c in self.oco_q:
            p["oco_redo"] = True; return
        if p.get("oco_del"): return                    # 舊條件單還在等元大確認刪掉 → 確認後(on_strategy_delete_reply)照當時的張數再掛
        if p.get("oco_live"):
            p["oco_live"] = False
            self.log(f"[交易] {c} 刪除元大條件單 {p['oco_no']}({why or '部位變了'})")
            # 2026-11-08 20:50 使用者「改」:等元大確認舊的刪掉才掛新的(避免刪單失敗時新舊兩張同時在、碰到停損一起平 → 多平);等的這段沒條件單,程式自己盯停損停利
            if p["state"] == HOLDING and p["qty"] > 0 and self.positions.get(c) is p and self.oco_ok(c):
                p["oco_del"] = {"no": p["oco_no"], "at": time.time(), "tries": 1, "why": why}; self.del_q.append(p["oco_no"])
                self.log(f"[交易] {c} 等元大確認刪掉 {p['oco_no']} 再掛新的條件單(這段由程式盯停損停利)")
                self.gw.delete_oco(p["oco_no"]); return
            self.gw.delete_oco(p["oco_no"])
        self.save_state()   # 2026-11-10 刪掉的條件單編號從狀態檔拿掉
        if p["state"] == HOLDING and p["qty"] > 0 and p.get("tp") and p.get("sl") and self.positions.get(c) is p and self.oco_ok(c):
            p["oco_no"] = None; self.oco_q.append(c); p.pop("oco_noted", None); p.pop("oco_hit_at", None)
            n = self.oco_lots(c); self.gw.place_oco(c, p["mkt"], p["dir"], n, p["tp"], p["sl"])
            self.log(f"[交易] {c} 掛元大停損停利條件單 {n} 張(停利 {p['tp']} / 停損 {p['sl']}){'(預掛 ' + str(p['qty'] - n) + ' 張不掛)' if n < p['qty'] else ''}")

    def on_strategy_delete_reply(self, strategy_no, status, msg=""):
        """2026-11-08 20:50 元大刪除條件單的回應:是程式在等的那張 → 成功就照現在的張數掛新的;失敗(9 / 911 / 無法刪除)最多再刪 3 次"""
        with self.lock:
            no = str(strategy_no or "").strip()
            if no and no in self.del_q: self.del_q.remove(no)
            elif self.del_q:   # 回應沒帶編號 / 編號對不上(元大刪除回應的欄位還沒實盤看過)→ 照送出順序對最早那張
                self.log(f"[交易] 刪除條件單回應編號 {no or '(空)'} 對不上,照順序當成 {self.del_q[0]}"); no = self.del_q.pop(0)
            else: return
            p = next((x for x in self.positions.values() if (x.get("oco_del") or {}).get("no") == no), None)
            if not p: return
            w = p["oco_del"]; bad = status in (9, 911) or any(k in str(msg) for k in ("失敗", "無法", "錯誤"))
            if bad:
                self.log(f"[交易] !! {p['code']} 刪除條件單 {no} 失敗(狀態 {status} {msg})")
                if w["tries"] < 3:
                    w["tries"] += 1; w["at"] = time.time(); self.del_q.append(no); self.gw.delete_oco(no); return
                p.pop("oco_del", None)
                self.alert(f"⚠ {self.name_of(p['code'])} {p['code']} 元大條件單刪不掉", f"舊條件單 {no} 刪除失敗 3 次,先不掛新的(程式自己盯停損停利);請在投資先生確認條件單")
                return
            p.pop("oco_del", None); self.log(f"[交易] {p['code']} 元大確認刪掉條件單 {no} → 掛新的")
            self.oco_refresh(p, w.get("why") or "")

    def on_strategy_reply(self, strategy_no, ok, msg, code=None):
        """元大新增條件單回應(只有 TRADE_BROKER_STOP=1 才會掛)。
           2026-11-08 01:25 稽核:回應有股票代碼就照代碼對(原本只照送出順序,送出當下就失敗的那筆會把排在前面那檔的回應吃掉 → 條件單編號張冠李戴)"""
        with self.lock:
            code = str(code or "").strip() or None
            if code and code in self.oco_q: self.oco_q.remove(code); c = code
            else: c = self.oco_q.pop(0) if self.oco_q else None
            p = self.positions.get(c) if c else None
            if not p or p.get("oco_no") is not None:
                return
            if ok and strategy_no:
                p["oco_no"] = str(strategy_no).strip(); p["oco_live"] = True
                self.log(f"[交易] {c} 元大條件單(OCO)已掛:策略編號 {p['oco_no']}(停利 {p['tp']} / 停損 {p['sl']})")
                self.save_state()   # 2026-11-10 條件單編號存檔(重開接回用)
                if p.pop("oco_redo", False): self.oco_refresh(p, "等回應時部位變了")
                elif p["state"] != HOLDING or p["qty"] <= 0 or self.positions.get(c) is not p or not self.oco_ok(c):   # 掛上去前部位已經在平 / 平完了 / 有預掛 / 過 13:20 → 刪掉
                    self.gw.delete_oco(p["oco_no"]); p["oco_live"] = False; self.log(f"[交易] {c} 條件單掛好時部位已經不在持倉 → 刪除 {p['oco_no']}")
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
            if o["purpose"] == "進場" and not amt and elig_reject(msg) and c not in self.brk_block:
                # 2026-10-31 開盤後真正的資格檢查:元大說這檔今天不能做 → 今天不再自動進場,推手機
                self.brk_block[c] = str(msg)[:80]; self.save_state()
                self.log(f"[交易] {c} 元大以資格原因拒絕進場 → 今天不再自動進場")
                if not p.get("forced"):
                    self.alert(f"⚠ {c} 元大拒絕進場,今天不再自動做這檔", f"{msg} · 名單 / 複檢沒擋到,元大說今天不能做")
            if p.get("forced") and o["purpose"] == "進場":
                self.alert(f"⚠ 強制進場 {c} 被元大拒絕", str(msg))
            if o["purpose"] == "平倉":
                self.alert(f"⚠ {c} 平倉單被元大拒絕", f"{msg} · 會自動重送(最多 {self.max_resend} 次)")
            self.after_report(p)

    # ── 每秒呼叫一次 ──
    def step(self, signals=None):
        self.check_kill()
        with self.lock:
            hm = self.hm()
            fe = self.force_exit
            if self.cancel_all:                                   # 13:20 由 bridge 先取消全部掛單再平倉;這裡晚 1 分鐘當備援
                h, m = map(int, fe.split(":")); fe = f"{h + (m + 1) // 60:02d}:{(m + 1) % 60:02d}"
            for p in list(self.positions.values()):
                w = p.get("oco_del")
                if w and time.time() - w["at"] > 5:   # 2026-11-08 20:50 刪條件單 5 秒沒回應 → 再刪一次(最多 3 次);還是沒回應就不掛新的,程式自己盯
                    if w["tries"] < 3:
                        w["tries"] += 1; w["at"] = time.time()
                        if w["no"] not in self.del_q: self.del_q.append(w["no"])
                        self.gw.delete_oco(w["no"]); self.log(f"[交易] {p['code']} 刪條件單 {w['no']} 沒回應,再刪一次(第 {w['tries']} 次)")
                    else:
                        p.pop("oco_del", None)
                        self.alert(f"⚠ {self.name_of(p['code'])} {p['code']} 元大條件單刪除沒回應", f"舊條件單 {w['no']} 刪了 3 次沒回應,先不掛新的(程式自己盯停損停利);請在投資先生確認條件單")
                if (p["state"] == HOLDING and p.get("oco_live") and p.get("oco_hit_at") and self.oco_grace > 0
                        and time.time() - p["oco_hit_at"] >= self.oco_grace and hm < fe):
                    # 2026-11-08 01:26 條件單保險:碰到停利 / 停損 N 秒了部位還在 → 條件單沒觸發,程式先刪條件單再自己平(漲停 / 跌停)
                    w = p.pop("oco_hit_why", "停利停損"); p.pop("oco_hit_at", None)
                    self.log(f"[交易] !! {p['code']} 碰到{w} {self.oco_grace:g} 秒元大條件單還沒平 → 程式刪條件單自己平")
                    self.alert(f"⚠ {self.name_of(p['code'])} {p['code']} 元大條件單沒觸發", f"碰到{w} {self.oco_grace:g} 秒還沒平,程式已自己平倉")
                    self.exit_pos(p, f"{w}(條件單沒觸發)", self.last_px.get(p["code"], p["entry"]), force_limit=True)
                if p["state"] == HOLDING and hm >= fe:
                    self.exit_pos(p, f"{self.force_exit} 強制平倉", self.last_px.get(p["code"], p["entry"]), force_limit=True)
                o = p.get("order")
                if (o and not o.get("dead") and o.get("fallback") and o["purpose"] == "進場" and not o.get("fb_cut")
                        and time.time() - o["at"] > self.fill_wait):
                    # 2026-11-08 15:57 使用者「改」:進場補單(漲停買 / 跌停賣 ROD)N 秒沒成交完 → 剩下的取消不追(鎖漲跌停時排隊,晚很久才成交、價格早就離訊號很遠)
                    #   取消回報回來 → after_report 照已成交的張數(IOC + 補單)轉持倉,0 張就放棄;回報沒來由 30 秒複查(verify)照元大處理
                    o["fb_cut"] = True
                    if o.get("no") and not o.get("cancel_sent"): o["cancel_sent"] = True; self.gw.cancel(o["no"], p["code"], o["side"], o["typ"])
                    else: o["cancel_after"] = True
                    self.log(f"[交易] {p['code']} 進場補單 {self.fill_wait:.0f} 秒沒成交完(已成交 {o['filled'] // 1000}/{o['want'] // 1000} 張),取消剩下的不追")
                if o and not o.get("dead") and o.get("tif") != "0" and time.time() - o["at"] > self.fill_wait:
                    if o.get("no"):
                        self.log(f"[交易] {p['code']} {o['purpose']} 委託 {self.fill_wait:.0f} 秒沒成交完,取消剩餘"); self.gw.cancel(o["no"], p["code"], o["side"], o["typ"])
                    if o["purpose"] == "進場":
                        o["timeout"] = True                            # 2026-10-29 稽核 #5:是程式自己等到逾時(不是元大回報結束)→ after_report 改「等確認」
                    o["dead"] = True; self.after_report(p)
                if p.get("wait_confirm") and p["state"] == ENTERING and time.time() - p["wait_confirm"] > self.confirm_wait:
                    oo = p.get("order")
                    if not (oo and oo.get("filled")):
                        if not self.man_only(p, "進場單等確認逾時沒成交"):
                            self.log(f"[交易] {p['code']} 進場單等確認 {self.confirm_wait:.0f} 秒還是沒有成交回報 → 確定沒成交,放棄(對帳 / 13:22 複查仍以元大為準)")
                            p["order"] = None; self.positions.pop(p["code"], None); self.mark_skip(p)
            if signals is not None and not self.primed:               # 2026-10-29 稽核 #1:啟動時沒讀到通知 → 第一次讀成功的這批先當「啟動時已有」,不拿來進場
                self.prime(signals); signals = None
            for it in (signals or []):
                self.on_signal(it)
            self.over_check()
            self.late_check()

    def over_ok(self, code, w):
        """超額訊號現在能不能補進(回 (能?, 現價, 取消原因))"""
        if code in self.positions: return False, None, "已經有部位"
        if self.hm() >= self.last_entry: return False, None, f"已過最後進場時間 {self.last_entry}"
        last = self.last_px.get(code)
        if not last or self.cap <= 0: return False, last, ""
        if not ((w["sl"] < last < w["tp"]) if w["kind"] == "long" else (w["tp"] < last < w["sl"])):
            return False, last, f"現價 {last} 已不在停利 {w['tp']} / 停損 {w['sl']} 之間"
        return last * 1000 <= self.cap, last, ""

    def over_check(self):
        """超額訊號(見 over_redo):每一輪 step 看一次;條件到了標 ready、發提醒到手機,等手機按「確認補進場」"""
        for code, w in list(self.over_wait.items()):
            ok, last, why = self.over_ok(code, w)
            if why:
                self.over_wait.pop(code, None); self.log(f"[交易] {code} 超額等補進場取消:{why}"); continue
            if ok and not w.get("ready"):
                w["ready"] = time.time(); self.log(f"[交易] {code} 現價 {last} 一張 ≤ 多空上限,等手機確認補進場(原訊號 {w['px']})")
                self.alert(f"↩ {self.name_of(code)} {code} 可以補進場", f"原訊號 {'做多' if w['kind'] == 'long' else '做空'} {w['px']} 超額沒進,現價 {last} 回到多空上限內,打開 App 按確認")
            elif not ok and w.get("ready"):
                w["ready"] = None                                  # 又超過上限:收回確認

    def late_check(self):
        """太晚了沒進的訊號(見 late_redo_min):現價回到訊號價附近就自動補進場"""
        for code, w in list(self.late_wait.items()):
            last = self.last_px.get(code)
            why = ""
            if code in self.positions: why = "已經有部位"
            elif time.time() > w["until"]: why = f"等了 {self.late_redo_min:g} 分鐘價格沒回來"
            elif self.hm() >= self.last_entry: why = f"已過最後進場時間 {self.last_entry}"
            elif last and not ((w["sl"] < last < w["tp"]) if w["kind"] == "long" else (w["tp"] < last < w["sl"])):
                why = f"現價 {last} 已碰到停利 {w['tp']} / 停損 {w['sl']}"
            if why:
                self.late_wait.pop(code, None); self.log(f"[交易] {code} 太晚了等補進場取消:{why}"); continue
            if not last or abs(last - w["px"]) / w["px"] * 100 > self.max_dev: continue
            self.late_wait.pop(code, None)
            it = w["it"]; it2 = dict(it); it2["_late"] = True
            it2["_k"] = (it.get("_k") or (it.get("t", "") + it.get("title", ""))) + "|晚補"
            self.log(f"[交易] {code} 價格回到訊號價:現價 {last}(訊號 {w['px']},±{self.max_dev:g}% 內)→ 自動補進場")
            self.on_signal(it2)
            if code in self.positions: self.alert(f"↩ {self.name_of(code)} {code} 補進場", f"原訊號 {'做多' if w['kind'] == 'long' else '做空'} {w['px']} 太晚沒進,現價 {last} 回來了,已自動進場")

    def over_list(self):
        with self.lock:
            out = []
            for code, w in self.over_wait.items():
                if not w.get("ready"): continue
                last = self.last_px.get(code) or 0
                out.append({"code": code, "name": self.name_of(code), "dir": 1 if w["kind"] == "long" else -1, "px": w["px"], "tp": w["tp"], "sl": w["sl"], "judge": w["judge"],
                            "last": last, "lots": max(1, int(self.cap // (last * 1000))) if last and self.cap > 0 else 1})
            return out

    def over_confirm(self, code, cmd_id=None):
        """手機按「確認補進場」"""
        with self.lock:
            if cmd_id and cmd_id in self.cmd_seen: return self.cmd_seen[cmd_id]
            w = self.over_wait.get(code)
            if not w: r = (False, "這檔已經不能補進場(判別式平倉了 / 過了停利停損 / 時間過了)")
            elif not self.enabled: r = (False, "電腦的自動交易沒有開")
            else:
                ok, last, why = self.over_ok(code, w)
                if not ok: r = (False, why or f"現價 {last} 一張又超過多空上限了")
                else:
                    self.over_wait.pop(code, None)
                    it = w["it"]; it2 = dict(it); it2["body"] = re.sub(r"·\s*超額\s*·", "·", it.get("body", "")); it2["_over"] = True
                    it2["_k"] = (it.get("_k") or (it.get("t", "") + it.get("title", ""))) + "|補"; it2["t"] = self.now().strftime("%H:%M")
                    self.log(f"[交易] {code} 手機確認補進場:現價 {last}(原訊號 {w['px']},超額沒進)")
                    self.on_signal(it2)
                    r = (True, "已送出補進場") if code in self.positions else (False, "沒有進場(可能額度 / 檔數上限 / 緊急停止,看電腦紀錄)")
            if cmd_id: self.cmd_seen[cmd_id] = r
            return r

    def over_drop(self, code):
        with self.lock:
            w = self.over_wait.pop(code, None)
            if w: self.log(f"[交易] {code} 手機按「不要」:不補進場")
            return (True, "不補進場")

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

    def force_enter(self, code, d, lots, tp_pct=None, sl_pct=None, mkt=None, cmd_id=None, ref=None, px=None):
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
            elif self.force_block(code, d, ref):
                r = (False, self.force_block(code, d, ref))
            else:
                if mkt: self.mkt_of[code] = mkt
                p = self.new_pos(code, d, FORCED, f"強制{'做多' if d > 0 else '做空'} {code}", forced=True)
                p["tp_pct"], p["sl_pct"] = tp_pct, sl_pct
                try: p["ref"] = float(ref) if ref else None          # 手機帶來的昨收(電腦這檔沒有昨收時 ±6.5% 用)
                except Exception: p["ref"] = None
                # 2026-11-03 11:20 使用者「需要加上下調整價格的功能 漲停價跟跌停價」:手機指定委託價(px)→ 限價 ROD(沒到價不成交,成交後照樣接手);沒指定 = 原本的漲停買 / 跌停賣
                try: lp = round(float(px), 2) if px not in (None, "") else 0.0
                except Exception: lp = 0.0
                if lp > 0:
                    self.send(p, "進場", "B" if d > 0 else "S", lots, lp, p["typ"], " ", "0")
                    how = f"限價 {lp:.2f}"
                else:
                    self.send(p, "進場", "B" if d > 0 else "S", lots, 0, p["typ"], "H" if d > 0 else "L", "0")
                    how = "漲停買" if d > 0 else "跌停賣"
                self.save_state()
                self.log(f"[交易] 強制進場 {code} {'做多' if d > 0 else '做空'}({how}){lots} 張")
                r = (True, f"已送出強制{'做多' if d > 0 else '做空'} {lots} 張({how})")
            if cmd_id: self.cmd_seen[cmd_id] = r
            return r

    def force_block(self, code, d, ref=None):
        """2026-11-03 13:21 使用者「加 強制進場 該股票-6.5%不做多 反之亦然」:現價離昨收 ≤ −6.5% 不給強制做多、≥ +6.5% 不給強制做空;回擋下的原因(沒擋回空字串)"""
        try: y = float(ref) if ref else 0.0
        except Exception: y = 0.0
        y = y or (self.ref_of(code) or 0); last = self.last_px.get(code) or 0; lim = self.force_block_pct
        if not (y > 0 and last > 0 and lim > 0): return ""
        ch = (last / y - 1) * 100
        if d > 0 and ch <= -lim: return f"現價 {last} 已跌 {ch:.1f}%(超過 −{lim:g}%),不能強制做多"
        if d < 0 and ch >= lim: return f"現價 {last} 已漲 {ch:+.1f}%(超過 +{lim:g}%),不能強制做空"
        return ""

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
                if self.prehang.get(code): self.cancel_ph(p, "強平")   # 2026-11-08 20:45 檢查:只平沒預掛那幾張的途中按強平 → 預掛先取消,平倉單成交後剩下的照強平送出
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

    def force_close_manual(self, code, trades, working, tags=None, types=None, cmd_id=None):
        """2026-11-07 23:30 使用者「改」(長按當沖平倉碰到電腦還沒接手的元大手動單):立刻接成元三再強平;
           這檔還掛著的手動單(預掛)先全部取消。程式已經有這檔部位 → 照一般強平"""
        tags = tags or {}; types = types or {}
        with self.lock:
            if cmd_id and cmd_id in self.cmd_seen:
                return self.cmd_seen[cmd_id]
            if code in self.positions or not self.enabled:
                return self.force_close(code, cmd_id)
            if code in self.ignore:
                return (False, "這檔在 TRADE_IGNORE_CODES(不管的股票)")
            n, otyp = 0, ""
            for t in sorted((t for t in trades if t["code"] == code), key=lambda x: x.get("time", "")):
                n0 = n; n += t["lots"] * (1 if t["bs"] == "B" else -1)
                if n != 0 and (n0 == 0 or (n0 > 0) != (n > 0)): otyp = str(types.get(t.get("order_no"), "") or "").strip()
            mytags = {q["order"]["tag"] for q in self.positions.values() if q.get("order")}
            for w in working:
                if w.get("code") == code and w.get("tag") not in mytags and w.get("no"):
                    self.gw.cancel(w["no"], code, w.get("bs") or "", w.get("typ") or ""); self.ph_cancel[w["no"]] = time.time()
                    self.log(f"[交易] {code} 強平(還沒接手的手動單):先取消手動委託 {w['no']}")
            if n == 0:
                r = (False, "元大已經沒有這檔部位")
                if cmd_id: self.cmd_seen[cmd_id] = r
                return r
            d = 1 if n > 0 else -1
            cs = [t for t in trades if t["code"] == code and t["bs"] == ("B" if d > 0 else "S")]
            avg = (sum(t["price"] * t["lots"] for t in cs) / sum(t["lots"] for t in cs)) if cs else (self.last_px.get(code) or 0.0)
            p = self.new_pos(code, d, MANUAL, "元大手動(強平接回)", forced=True)
            if otyp: p["typ"] = otyp
            p.update({"qty": abs(n), "entry": avg, "state": HOLDING, "t0": self.hm(), "tp_pct": self.y3_tp, "sl_pct": self.y3_sl})
            self.set_forced_tpsl(p, avg); self.sub(p)
            self.log(f"[交易] {code} 長按強平:元大有手動 {abs(n)} 張{'多' if d > 0 else '空'}單、程式還沒接手 → 接成元三(均價 {avg:.2f})馬上強平")
            return self.force_close(code, cmd_id)

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
        # 2026-11-02 13:53 使用者選「標未交易 不寫原因」:進場訊號沒變成程式的部位(太晚、超額、強平過、額度不夠、已有部位…)→ 記「代碼|判別式|訊號那根」,
        #   手機問部位(?trade=pos)時一起給,訊息欄那張進場卡和之後同一筆的平倉卡標「未交易」;送了單但沒成交(放棄)也記
        title, body, kind = it.get("title", ""), it.get("body", ""), it.get("kind", "")
        code = code_of(title) if kind in ("long", "short") else None
        before = self.positions.get(code) if code else None
        seen0 = (it.get("_k") or (it.get("t", "") + title)) in self.seen
        self._on_signal(it)
        if code and self.primed and not seen0:
            after = self.positions.get(code)
            sk = self.sig_key(code, judge_of(body), body)
            if after is not None and after is not before:
                after["sigkey"] = sk; self.traded_keys.add(sk); self.skipped.discard(sk)
            elif sk not in self.traded_keys:   # 同一個訊號(本機 + 雲端各來一次)已經進場過的不算未交易
                self.skipped.add(sk)

    def sig_key(self, code, judge, body):
        mb = RE_BAR.search(body or "")
        return f"{code}|{judge or ''}|{mb.group(1) if mb else ''}"

    def mark_skip(self, p):
        if p and p.get("sigkey"): self.traded_keys.discard(p["sigkey"]); self.skipped.add(p["sigkey"])

    def _on_signal(self, it):
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
            if re.search(r"·\s*已強平\s*·", body):   # 2026-11-04 強平過的部位,引擎照算的虛擬出場(只給看盤版看)→ 不理(強平後又強制進場 / 元大手動的部位不能被它平掉)
                return
            w = self.over_wait.get(code)
            if w and (judge is None or judge == w["judge"] or (w["judge"] in ("強三", "元三") and judge == "判三")):
                self.over_wait.pop(code, None); self.log(f"[交易] {code} 超額等補進場取消:{w['judge']} 已發平倉通知")
            w = self.late_wait.get(code)
            if w and (judge is None or judge == w["judge"]):
                self.late_wait.pop(code, None); self.log(f"[交易] {code} 太晚了等補進場取消:{w['judge']} 已發平倉通知")
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
        if kind == "short" and self.noshort.get(code):
            self.log(f"[交易] 不進場 {title}:{self.noshort[code]},今天這檔不能空"); return
        if re.search(r"·\s*超額\s*·", body):                   # 2026-10-30 使用者「超過限額的股票 … 只是註記“超額”不交易」:只通知,要做請用手機強制進場
            self.log(f"[交易] 不進場 {title}:超額(一張就超過多空上限),只通知;要做請用強制進場")
            tpsl = RE_TPSL.search(body); mp = RE_PX_END.search(title)
            if self.over_redo and self.cap > 0 and tpsl and mp and code not in self.positions:
                self.over_wait[code] = {"it": dict(it), "judge": judge, "kind": kind, "px": num(mp.group(1)), "tp": num(tpsl.group(1)), "sl": num(tpsl.group(2))}
                self.log(f"[交易] {code} 超額先記著:現價回到一張 ≤ 多空上限 {self.cap:,.0f} 時補進場(停利 {num(tpsl.group(1))} / 停損 {num(tpsl.group(2))})")
            return
        age = self.sig_age(it.get("t"))
        if age is not None and age > self.sig_max_age and not it.get("_late"):   # 太晚補進場:原訊號本來就舊了,時間由 late_redo_min 管          # 2026-10-29 稽核 #1:太舊的進場通知(例如重開 / 讀取延遲才看到)不進
            self.log(f"[交易] 不進場 {title}:通知時間 {it.get('t')} 已經過 {age:.0f} 分鐘(上限 {self.sig_max_age:g},TRADE_SIGNAL_MAX_AGE_MIN)"); return
        why = self.can_enter(code, judge, kind)
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
        if not it.get("_over") and abs(last - px_sig) / px_sig * 100 > self.max_dev:   # 超額補進場:本來就是價格變了才補,不看離訊號價多遠(停利 / 停損之間另外檢查)
            self.log(f"[交易] 不進場 {title}:現價 {last} 離訊號價 {px_sig} 超過 {self.max_dev}%(太晚了)")
            if self.late_redo_min > 0 and not it.get("_late") and code not in self.positions:
                self.late_wait[code] = {"it": dict(it), "judge": judge, "kind": kind, "px": px_sig, "tp": tp_sig, "sl": sl_sig, "until": time.time() + self.late_redo_min * 60}
                self.log(f"[交易] {code} 太晚了先記著:{self.late_redo_min:g} 分鐘內現價回到 {px_sig} ±{self.max_dev:g}% 且沒碰停利 {tp_sig} / 停損 {sl_sig} → 自動補進場")
            return
        if not ((sl_sig < last < tp_sig) if kind == "long" else (tp_sig < last < sl_sig)):   # 2026-11-08 15:57 使用者「改」:現價已經碰到停利 / 停損就不進(太晚 / 超額補進場本來就有這關)
            self.log(f"[交易] 不進場 {title}:現價 {last} 已不在停利 {tp_sig} / 停損 {sl_sig} 之間"); return
        d = 1 if kind == "long" else -1
        price = shift(last, self.slip * d)                     # 買進往上讓、賣出往下讓
        lots = max(1, int(self.cap // (px_sig * 1000))) if self.cap > 0 else self.qty   # 2026-10-30 張數照多空上限(訊號價算,和通知上的張數一樣)
        if it.get("_over") and self.cap > 0: lots = max(1, int(self.cap // (last * 1000)))   # 超額補進場:照現價算張數
        if self.max_amount > 0:                                 # 2026-10-27 一天成交總金額上限(買 + 賣都算):剩的額度夠幾張就掛幾張
            hold = self.hold_reserve(); lu = self.lu_of(code, price); pend = self.pend_reserve()
            room = self.max_amount - self.amount_today - hold - pend   # 2026-11-02 照元大:持倉的部位先用漲停價扣贖回額度;2026-11-08 手動預掛還掛著的也先扣
            fit = int(room // ((price + lu) * 1000))                    #   新單每張 = 進場價 + 漲停價(元大一樣這樣先扣)
            if fit < 1:
                self.log(f"[交易] 不進場 {title}:今天成交 {self.amount_today:,.0f} + 持倉漲停預留 {hold:,.0f} + 預掛 {pend:,.0f},剩 {room:,.0f} 不夠 1 張(進場 {price} + 漲停 {lu};上限 {self.max_amount:,.0f})"); return
            if fit < lots:
                self.log(f"[交易] 額度只夠 {fit} 張(今天成交金額 {self.amount_today:,.0f} / 上限 {self.max_amount:,.0f}),{lots} 張改掛 {fit} 張"); lots = fit
        p = self.new_pos(code, d, judge, title, tp=tp_sig, sl=sl_sig)
        p["px_sig"] = px_sig                                       # 2026-10-31 改 C:補單前檢查現價離訊號價多遠
        p["no_max_move"] = "✓破" in body   # 2026-10-28 使用者選「元大那邊對假突破空單不套 6.5%」:判三「假突破跌破 Ka 空」(通知帶「空✓破」)常在離昨收 +6.5% 以上才進場,不套 ±6.5% 強制平倉(停利 / 停損 / 13:20 / 手機平倉通知照舊)
        if ekey:
            self.entered_keys.add(ekey); self.save_state()      # 2026-10-29 稽核 #3:送單就記(沒成交也不再追同一個訊號)
        self.send(p, "進場", "B" if d > 0 else "S", lots, price, p["typ"])

    def can_enter(self, code, judge, kind=None):
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
        if code in getattr(self, "brk_block", {}):
            return f"元大今天拒絕過這檔({self.brk_block[code]})"
        if self.max_pos > 0 and self.auto_count() >= self.max_pos:
            return f"已經有 {self.auto_count()} 檔部位(上限 {self.max_pos})"
        if getattr(self, "killed", False):
            return f"今天已虧到 {self.kill_loss:,.0f} 全部平倉過,不再自動進場"
        if self.pnl <= -self.max_loss:
            return f"今天已虧 {-self.pnl:,.0f}(上限 {self.max_loss:,.0f})"
        hm = self.hm()
        if not ("09:00" <= hm < self.last_entry):
            return f"不在 09:00~{self.last_entry}"
        if judge == "判二" and kind in ("long", "short") and self.j2_brng.get(kind):   # 2026-11-10 01:45 判二第二十六次修改(使用者「解鎖 改」):大盤上漲家數要在 22 天贏單範圍內(空 4.4%~88.1%、多 10.6%~92%),取代 ≥ 92% 不做
            b = self.br_now(); lo_, hi_ = self.j2_brng[kind]
            if b is not None and not (lo_ - 1e-9 <= b <= hi_ + 1e-9):
                return f"大盤上漲家數 {b:.1%} 不在判二{'做空' if kind == 'short' else '做多'}範圍 {lo_:.1%}~{hi_:.1%}"
        if judge == "判二" and self.j2_brcap:   # 2026-11-09 判二第二十四次修改:大盤上漲家數分級限制檔數
            b = self.br_now()
            if b is not None:
                n2 = sum(1 for k in self.traded_keys if k.split("|")[1:2] == ["判二"])
                for th, cap in self.j2_brcap:
                    if b >= th - 1e-9:
                        if n2 >= cap: return f"大盤上漲家數 {b:.0%} ≥ {th:.0%},判二不做" if cap == 0 else f"大盤上漲家數 {b:.0%} ≥ {th:.0%},判二今天已做 {n2} 檔(上限 {cap})"
                        break
        return None

    def br_now(self):
        """今天名單上漲家數比例(最新一筆);讀不到 / 今天沒資料 → None(不擋)。30 秒讀一次檔"""
        try:
            if time.time() - self._br[0] > 30:
                d = json.loads(self.br_file.read_text(encoding="utf-8")).get(self.now().strftime("%Y-%m-%d")) or {}
                hm = self.hm(); ks = [k for k in d if k <= hm and d[k] is not None]
                self._br = (time.time(), float(d[max(ks)]) if ks else None)
            return self._br[1]
        except Exception:
            self._br = (time.time(), None); return None

    # 2026-11-02 16:16 使用者「500萬 就是買和賣的總和 當沖還未贖回 皆以漲停價 暗自計算 … 再扣除如果做空 就以漲停價贖回的價格 剩餘的 才是能再交易的資金」→「改」:
    #   元大可用額度 = 500 萬 − 已成交(買 + 賣)− 還沒平倉的部位 × 漲停價(多空都用漲停,使用者「皆以漲停價」);新單每張要預留 進場價 + 漲停價
    def lu_of(self, code, px=0.0):
        lu = limit_up(self.ref_of(code) or 0)
        return lu if lu > 0 else round((px or 0) * 1.1, 2)

    def hold_reserve(self, states=None):
        """還沒平倉的張數(進場中已成交的、持倉中、平倉中還沒賣 / 買回的)× 漲停價 × 1000;states 指定只算哪些狀態(額度推手機用:平倉單已掛在委託裡,不重算)"""
        s = 0.0
        for c, p in self.positions.items():
            if p.get("state") in (states or (ENTERING, HOLDING, EXITING)) and p.get("qty", 0) > 0:
                q = max(0, p["qty"] - self.ph_lots(c))           # 2026-11-08 16:00 有手動預掛平倉單的張數改照預掛價扣(pend_reserve),不重複用漲停扣
                s += q * self.lu_of(c, p.get("entry") or 0) * 1000
        return s

    def ph_lots(self, code):
        """這檔手動預掛平倉單還沒成交的張數"""
        return sum(max(0, (self.man_meta.get(no) or {}).get("lots", 0) - (self.man_meta.get(no) or {}).get("fl", 0)) for no in (self.prehang.get(code) or []))

    # 2026-11-08 16:00 使用者「預掛單 需要先扣資金額度」:元大掛單當下就扣額度 → 程式進場前算剩多少額度時,手動預掛還掛著的也要先扣:
    #   平倉預掛 = 剩下張數 × 預掛價(那幾張不再用漲停扣,見 hold_reserve);新進場 / 加碼預掛 = 剩下張數 ×(預掛價 + 漲停價),跟程式自己的新單一樣;市價單沒價格用漲停價
    def pend_reserve(self):
        s = 0.0
        for c, lst in self.prehang.items():
            lu = self.lu_of(c, self.last_px.get(c) or 0)
            for no in lst:
                m = self.man_meta.get(no) or {}; left = max(0, m.get("lots", 0) - m.get("fl", 0))
                s += left * (m.get("px") or lu) * 1000
        # 2026-11-08 16:04 使用者「掛空補單呢」:程式自己還在「進場中」的單也要先扣(原本要等轉持倉才算進成交 / 漲停預留):
        #   已成交的(IOC + 補單)= 成交金額 + 張數 × 漲停(之後買回 / 賣出的預留);還掛著的 = 剩下張數 ×(委託價 + 漲停),漲停買用漲停價、跌停賣用現價
        #   → IOC 成交一部分後補單算張數時,IOC 那幾張不會被當成沒用到額度;同時兩個訊號進場也不會重複用同一筆額度
        for c, p in self.positions.items():
            if p.get("state") != ENTERING: continue
            lu = self.lu_of(c, self.last_px.get(c) or p.get("px_sig") or 0); o = p.get("order") or {}
            f = (o.get("filled") or 0) + (p.get("pre_filled") or 0); cost = (o.get("cost") or 0.0) + (p.get("pre_cost") or 0.0)
            s += cost + f * lu
            if o and not o.get("dead") and o.get("purpose") == "進場":
                left = max(0, (o.get("want") or 0) - (o.get("filled") or 0))
                px = o.get("price") or (lu if o.get("side") == "B" else (self.last_px.get(c) or p.get("px_sig") or lu))
                s += left * (px + lu)
        for no, mc in self.man_cards.items():
            if mc.get("end"): continue
            left = max(0, mc.get("lots", 0) - mc.get("fl", 0))
            if left:
                c = mc.get("code", ""); lu = self.lu_of(c, mc.get("px") or self.last_px.get(c) or 0)
                s += left * ((mc.get("px") or lu) + lu) * 1000
        return s

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

    def cancel_ph(self, p, why):
        """取消這檔所有手動預掛(強平 / 緊急全平用);取消前成交的照「強平取消的預掛」處理(多出來的再強平)"""
        for no in self.prehang.pop(p["code"], []):
            m = self.man_meta.pop(no, {}); self.gw.cancel(no, p["code"], m.get("bs") or close_side(p["dir"]), m.get("typ") or p["typ"]); self.ph_cancel[no] = time.time()
            self.log(f"[交易] {p['code']} {why}:先取消手動預掛 {no}")
        p["forced_close"] = True; p["force_limit"] = True

    def prehang_wait(self, code):
        """這檔有保留的手動預掛平倉單、還沒到期限 → True(程式先不平)"""
        return bool(self.prehang.get(code)) and self.prehang_deadline not in ("", "0") and self.hm() < self.prehang_deadline

    def exit_pos(self, p, why, px, force_limit=False, kill_ph=False):
        if not p or p["state"] != HOLDING:
            return
        if self.prehang.get(p["code"]) and (str(why).startswith("強平") or kill_ph):   # 2026-11-08 00:55 緊急 / 虧損全平也先取消預掛(條件單下面刪)   # 手機強平:先取消預掛再平,不讓位
            for no in self.prehang.pop(p["code"], []):
                m = self.man_meta.pop(no, {}); self.gw.cancel(no, p["code"], m.get("bs") or close_side(p["dir"]), m.get("typ") or p["typ"]); self.ph_cancel[no] = time.time()
                self.log(f"[交易] {p['code']} 強平:先取消手動預掛 {no}")
        if self.prehang_wait(p["code"]):
            # 2026-11-08 18:26 使用者「進場6張 我預掛兩張」→「改」:預掛只蓋住一部分 → 沒預掛的張數程式照停利 / 停損平掉,預掛的照樣留著等成交
            #   (不掛元大條件單,維持「預掛跟條件單不同時存在」;平完剩預掛張數 → 回持倉中繼續等預掛)
            free = p["qty"] - self.ph_lots(p["code"])
            if any(not (self.man_meta.get(n) or {}).get("lots") for n in self.prehang.get(p["code"]) or []): free = 0   # 預掛張數不知道 → 當作全部都有預掛,照舊等
            if free > 0:
                self.log(f"[交易] 出場 {p['code']}:{why}(現價 {px})→ 預掛 {p['qty'] - free} 張留著,只平沒預掛的 {free} 張")
                self.alert(f"✂ {self.name_of(p['code'])} {p['code']} {why}", f"平沒預掛的 {free} 張;預掛 {p['qty'] - free} 張照樣等成交({self.prehang_deadline} 前沒成交由程式平)")
                p["exit_why"] = why; p["ph_part"] = True
                if p.get("oco_live"):                       # 沒預掛那幾張的條件單先刪,免得和程式的平倉單一起成交
                    self.gw.delete_oco(p["oco_no"]); p["oco_live"] = False; self.log(f"[交易] 刪除元大條件單 {p['oco_no']}")
                p["force_limit"] = force_limit or self.hm() >= "13:25"
                p["state"] = EXITING
                self.send_exit(p, free, px); return
            if not p.get("_ph_log"): p["_ph_log"] = True; self.log(f"[交易] 出場 {p['code']}:{why} → 有手動預掛的平倉單,{self.prehang_deadline} 前先等它成交")
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

    def entry_fallback_lots(self, p, left):
        """改 C:IOC 沒成交完的張數能不能補、補幾張;不能補回 0(原因寫 log)"""
        c = p["code"]; last = self.last_px.get(c); sig = p.get("px_sig") or p["entry"] or last
        why = ("自動交易關閉" if not self.enabled else "緊急停止" if self.stop_file.exists() else
               "今天已全部平倉過" if getattr(self, "killed", False) else f"已過 {self.last_entry}" if self.hm() >= self.last_entry else
               "沒有即時價" if not last else f"現價 {last} 離訊號價 {sig} 超過 {self.max_dev}%" if sig and abs(last - sig) / sig * 100 > self.max_dev else "")
        if why:
            self.log(f"[交易] {c} 進場 IOC 沒成交 {left} 張,不補單:{why}"); return 0
        if self.max_amount > 0:
            fit = int((self.max_amount - self.amount_today - self.hold_reserve() - self.pend_reserve()) // ((last + self.lu_of(c, last)) * 1000))   # 2026-11-02 照元大:持倉漲停預留 + 每張 進場 + 漲停
            if fit < 1:
                self.log(f"[交易] {c} 進場 IOC 沒成交 {left} 張,不補單:今天成交金額額度不夠"); return 0
            left = min(left, fit)
        return left

    def entry_fallback_send(self, p, o):
        """改 C:IOC 這張單(o)確定結束,沒成交的張數改掛漲停買 / 跌停賣 ROD;送出回 True"""
        left = self.entry_fallback_lots(p, (o["want"] - o["filled"]) // 1000)
        if left <= 0:
            return False
        p["pre_filled"] = p.get("pre_filled", 0) + o["filled"]; p["pre_cost"] = p.get("pre_cost", 0.0) + o["cost"]
        p.pop("wait_confirm", None)
        side = "B" if p["dir"] > 0 else "S"
        sig = p.get("px_sig") or p["entry"] or self.last_px.get(p["code"])
        px = fb_price(sig, p["dir"], self.ref_of(p["code"]) or 0, self.fb_pct)   # 2026-11-08 16:09 使用者「改」:補單不再掛漲停 / 跌停(買方 / 賣方很少時會一路吃下去),改掛訊號價 ±1% 限價
        self.log(f"[交易] {p['code']} 進場 IOC 成交 {o['filled'] // 1000}/{o['want'] // 1000} 張 → 剩 {left} 張改掛{'買' if side == 'B' else '賣'} {px}(訊號 {sig} {'+' if side == 'B' else '−'}{self.fb_pct:g}%,ROD,{self.fill_wait:.0f} 秒沒成交完取消)補進場")
        p["ioc_tag"] = o["tag"]
        self.send(p, "進場", side, left, px, p["typ"], " ", "0")
        p["order"]["fallback"] = True
        return True

    def man_only(self, p, why):
        """2026-11-02 程式的進場單確定沒成交,但進場中有手動同方向成交 → 只用手動那幾張接成部位(不拿掉);沒有手動成交回 False"""
        ml, mc = p.pop("man_lots", 0), p.pop("man_cost", 0.0)
        if not ml:
            return False
        avg = mc / (ml * 1000); base = p.get("px_sig") or avg
        p["order"] = None; p.pop("wait_confirm", None)
        if p.get("forced"): self.set_forced_tpsl(p, avg)
        elif base:
            if p.get("tp"): p["tp"] = round(avg * p["tp"] / base, 2)
            if p.get("sl"): p["sl"] = round(avg * p["sl"] / base, 2)
        p.update({"qty": ml, "entry": avg, "state": HOLDING, "t0": self.hm()}); self.amount_today += mc; self.n_trades += 1; self.save_state()
        self.log(f"[交易] {p['code']} {why},但有手動同方向成交 {ml} 張 → 接成程式部位 {p['judge']} 均價 {avg:.2f} 停利 {p['tp']} 停損 {p['sl']}")
        self.alert(f"✅ {self.name_of(p['code'])} {p['code']} 手動 {ml} 張已交給程式", f"均價 {avg:.2f}(停利 {p['tp']} / 停損 {p['sl']})")
        return True

    def after_report(self, p):
        o = p.get("order")
        if not o:
            return
        done = o["filled"] >= o["want"] or o.get("dead")
        if not done:
            return
        # 2026-10-31 改 C:進場 IOC 確定結束(元大回報取消 / 失效 / 價穩失效,不是程式自己等逾時)還有沒成交的 → 剩下的改掛漲停 / 跌停 ROD 補一次
        if (o["purpose"] == "進場" and self.entry_fallback and o.get("dead") and not o.get("timeout") and not o.get("fallback") and o.get("tif") != "0"
                and o.get("end_status") in (2, 24, 25) and not p.get("forced") and not p.get("close_after_entry") and o["filled"] < o["want"]):
            if self.entry_fallback_send(p, o):
                return
        if o["purpose"] == "進場" and p.get("pre_filled") is not None:   # 補單結束:和 IOC 那次的成交合併成同一個部位
            o["filled"] += p.pop("pre_filled"); o["cost"] += p.pop("pre_cost", 0.0)
        lots = o["filled"] // 1000; avg = o["cost"] / o["filled"] if o["filled"] else 0
        p["order"] = None
        if o["purpose"] == "進場" and lots > 0 and p.get("man_lots"):   # 2026-11-02 進場中的手動同方向成交一起併進來,停利 / 停損照原本 % 從新均價重算
            ml, mc = p.pop("man_lots"), p.pop("man_cost", 0.0); base = avg
            o["cost"] += mc; lots += ml; avg = o["cost"] / (lots * 1000)
            if not p.get("forced") and base:
                if p.get("tp"): p["tp"] = round(avg * p["tp"] / base, 2)
                if p.get("sl"): p["sl"] = round(avg * p["sl"] / base, 2)
            self.log(f"[交易] {p['code']} 進場單成交 {lots - ml} 張 + 手動 {ml} 張 → 併成 {lots} 張 均價 {avg:.2f}")
            self.alert(f"✅ {self.name_of(p['code'])} {p['code']} 手動 {ml} 張已交給程式", f"共 {lots} 張 均價 {avg:.2f}")
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
                if self.man_only(p, "進場單沒成交"): return
                self.log(f"[交易] {p['code']} 進場沒成交,放棄"); self.positions.pop(p["code"], None); self.mark_skip(p)
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
            if self.oco_ok(p["code"]) and p["tp"] and p["sl"]:
                p["oco_no"] = None; self.oco_q.append(p["code"])
                self.gw.place_oco(p["code"], p["mkt"], p["dir"], p["qty"] or lots, p["tp"], p["sl"])
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
        if (p.get("ph_part") or self.prehang_wait(p["code"])) and p["qty"] > 0 and self.prehang.get(p["code"]) and not p.get("forced_close"):   # 2026-11-08 20:50 檢查:重開接回時 ph_part 記號沒了,預掛期間一樣只補沒預掛的
            # 2026-11-08 18:26 只平沒預掛的那幾張:平完 → 回持倉中、剩下的等預掛;沒平完 → 只重送沒預掛的張數
            keep = self.ph_lots(p["code"]); px = self.last_px.get(p["code"], avg or p["entry"])
            if p["qty"] <= keep:
                p.pop("ph_part", None); p["order"] = None; p["state"] = HOLDING; p["_ph_log"] = True
                self.log(f"[交易] {p['code']} 沒預掛的張數平完,剩預掛 {p['qty']} 張 → 回持倉中等預掛成交({self.prehang_deadline} 前沒成交由程式平)"); return
            p["resend"] = p.get("resend", 0) + 1
            if p["resend"] > self.max_resend:
                self.log(f"[交易] !! {p['code']} 平倉已重送 {self.max_resend} 次還沒平完,停止自動重送,請手動確認")
                self.alert(f"⚠⚠ {p['code']} 平倉失敗 {p['qty'] - keep} 張", f"已重送 {self.max_resend} 次,請立刻到投資先生手動平倉")
                p["stuck"] = True; return
            self.log(f"[交易] {p['code']} 沒預掛的還剩 {p['qty'] - keep} 張沒平,再送(第 {p['resend']} 次)")
            p["force_limit"] = True; self.send_exit(p, p["qty"] - keep, px); return
        p.pop("ph_part", None)
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
def stop_path(cfg):
    """2026-10-31 緊急停止檔:這個檔案存在 = 不再自動進場(已有部位照常平倉、手機強制進場照常)。
       使用者「長按名揚四海。自動當沖 啟動緊急停止」:手機長按標題 → 電腦建立 / 刪除這個檔。
       預設放在程式資料夾(Windows = C:\\Yuanta\\bridge\\STOP);舊 bridge.env 寫的 Linux 路徑 /opt/... 在 Windows 上也改用程式資料夾"""
    import os
    v = str(cfg.get("TRADE_STOP_FILE", "") or "").strip()
    if not v or (os.name == "nt" and v.startswith("/")):
        return Path(__file__).resolve().parent / "STOP"
    return Path(v)


ELIG_NO = ("當沖", "先賣", "處置", "資格", "暫停", "停止", "全額", "注意股", "未開放", "權限", "券", "分盤", "預收")
def elig_reject(msg):
    """2026-10-31 元大拒絕原因是不是「這檔今天不能做」(資格類);價格 / 額度 / 金額這類不算(換個價、少張數還可能成交)"""
    m = str(msg or "")
    return any(k in m for k in ELIG_NO)


def amount_reject(msg):
    """2026-10-27 元大回的拒絕原因是不是跟金額 / 額度有關(是 → 退 1 張再掛);券源不足、沒資格、處置股、價格不對這些不算"""
    m = str(msg or "")
    return any(k in m for k in AMT_YES) and not any(k in m for k in AMT_NO)


CLOUD_SET = {}   # 2026-11-07 主導機的整組設定(手續費估算用);fetch_cap 讀到就更新


def fetch_cap(worker_url, push_key=""):
    """2026-11-02 多空上限主控(使用者:「金鑰裡面有5861的為主導機」):讀 5861 主導機設定的每筆上限(元);讀不到 / 沒設過 / 超出 5萬~50萬 → None(維持原值)"""
    try:
        r = urllib.request.urlopen(urllib.request.Request(f"{worker_url.rstrip('/')}/?cap=get&_={int(time.time())}", headers={"X-Push-Key": push_key or "", "User-Agent": "yuanta-bridge/1"}), timeout=8)
        j = json.loads(r.read().decode("utf-8"))
        if isinstance(j.get("set"), dict): CLOUD_SET.clear(); CLOUD_SET.update(j["set"])
        v = int(j.get("cap") or 0)
        return v if 50000 <= v <= 500000 else None
    except Exception:
        return None


def fetch_signals(worker_url, day, push_key=""):
    """抓手機今天的通知(雲端紀錄,新的在前)→ 回傳舊到新。2026-10-29 鑰匙:帶中繼金鑰(以後 Worker 全面要鑰匙時照樣讀得到)"""
    try:
        r = urllib.request.urlopen(urllib.request.Request(f"{worker_url.rstrip('/')}/?log=get&d={day}", headers={"X-Push-Key": push_key or ""}), timeout=10)
        js = json.loads(r.read().decode("utf-8"))
        return list(reversed(js)) if isinstance(js, list) else None
    except Exception:
        return None     # 2026-10-29 稽核 #1:讀取失敗回 None(跟「今天還沒有通知」的 [] 分開),prime 才不會把失敗當成「沒有舊通知」
