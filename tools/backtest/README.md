# 回測工具(2026-10-20 打包,原本放在雲端 session 的暫存區)

`sim_day.py` 是和 App `replaySignals()` 對齊的單檔單日模擬器(對照 0 差異,見 chk/rs3_cmp2.py);旗標用環境變數。
`agg1.py 日期 "旗標" 池檔`、`agg5.py "旗標"`(10/13~10/19 五天)、`agg9.py "旗標"`(加 10/06、10/07、10/08、10/12 九天)。
K 線讀 `../../kline/<代號>/2026-W4*.csv`(雲端只留 3 週,10/26 起 W41 會被刪);池檔在 `pools/`(hi350_* 是當天實際名單過 350 元上限、hist_* 從 git 歷史重建,10/06、10/07 歷史不足算不了量比)。

## 三份判別式的旗標(2026-10-20 01:20 為準)

判一(第七次修改後,不做逆勢單):
```
OBMASLOPE=4 FPGAP=2.5 TAKE=2.5 VWAPF=1 FP=1 MAXDEV=1 MIRROR=1 PENDVOL=1 VOLMIN=6000 RVOLMIN=0.9 CAP=350000 REVOK=1 ENTRYMAX=3.5 OBVX=2 OBVXMIN=1 OBVX2=1 OB2=30 SLOPEMIN=-99 GAPMODE=dir GAPTH=2 OB3MA=6 REDSLOPE=-2 EXITRESET=tp OBVDIR=30 NEARHL=2 NOWEAK=1 STOPMODE=pts STOPSCOPE=last2 STOPKBAR=close STOPCAP=3 CHOP=1 CHOPW=45
```
判二:
```
FPGAP=2.5 TAKE=2.5 VWAPF=1 FP=1 MAXDEV=1 MIRROR=1 PENDVOL=1 VOLMIN=6000 RVOLMIN=0.9 CAP=350000 REVOK=1 ENTRYMAX=3.5 OBVX=2 OBVXMIN=1 OBVX2=1 OB2=30 SLOPEMIN=-99 GAPMODE=dir GAPTH=2 OB3MA=6 REDSLOPE=-2 EXITRESET=tp MODE=cross XWIN=10 OBMASLOPE=0 XSTR=2 XNEWHI=1 XOB2=20 XOB2DIR=slope XKMA=0 NOLOCK=1 STRONG=1 SREQ=1030,dx,ext SBARS=2 SWIN=5 STRONGMAX=4 SNOAFTER=11:30 MINGAP=4 NOENTRYAFTER=12:00 XOB3=30 WEAKORDER=1 OBVDIR=30 NEARHL=2 STOPMODE=pts STOPSCOPE=last2 STOPKBAR=close STOPCAP=3 STOPMIN=1.5 OB10DIR=1 CHOP=1 CHOPW=45
```
判四(燿華型,原判三;2026-10-20 21:50 改名、隱藏):
```
FPGAP=2.5 TAKE=2.5 VWAPF=0 FP=1 MAXDEV=99 MIRROR=1 PENDVOL=1 VOLMIN=0 RVOLMIN=0 CAP=350000 REVOK=1 ENTRYMAX=3.5 OBVX=2 OBVXMIN=1 OBVX2=1 OB2=30 SLOPEMIN=-99 GAPMODE=dir GAPTH=99 OB3MA=6 REDSLOPE=-2 EXITRESET=tp OBVDIR=0 NEARHL=0 NOLOCK=1 NOENTRYAFTER=12:00 MODE=brk BRKBELOW=1 BRKVBASE=pre OBMASLOPE=0 BRKGAP=0
```
(2026-10-20 01:30 加 `BRKGAP=0` 開高不做:今開對昨收缺口 ≤ 0% 才做,昨收用 K 線前一天收盤;App 對照用 `BRKGAPREF=pool`。九天 26 筆 57% +54,181)
判三(壓線空點,只做空;原判四,2026-10-20 21:50 改名判三。現行另加 VOLMIN=6000 CHOP=1 CHOPW=30,見 chk/rs3_cmp2.py 的 J3):
```
FPGAP=2.5 TAKE=2.5 VWAPF=0 FP=1 MAXDEV=99 MIRROR=1 PENDVOL=1 VOLMIN=0 RVOLMIN=0 CAP=350000 REVOK=1 ENTRYMAX=3.5 OBVX=2 OBVXMIN=1 OBVX2=1 OB2=30 SLOPEMIN=-99 GAPMODE=dir GAPTH=99 OB3MA=6 REDSLOPE=-2 EXITRESET=tp OBVDIR=0 NEARHL=0 NOLOCK=1 NOENTRYAFTER=12:00 OBMASLOPE=0 MODE=press CHOP=1 CHOPW=45
```
(PRESSTOUCH=0.15 PRESSCHASE=2 PRESSRVOL=1.2 PRESSLO=0.5 PRESSGAP=1 PRESSNL=30 為預設)

測試用旗標(都沒採用):`NOENTRYAFTER`、`OPENMOVE=0.7`(價先走 0.7%)、`SHORTMAXPCT=-0.01`(做空只做黑盤,判一九天 67% → 78%,使用者還沒說改)、`REVWEAK=1`(逆勢單反向做,更差)、`ENTER1=1`(第一訊號就進,更差)、`BRKSIDE=short`/`BRKREV=1`(判三做空鏡像 / 反向做,都賠)、`CHASEHI=2`(不追空:判一變差、判二勝率 61% → 75% 錢不變)、`CHASELO=2`(不追多:判一只剩 5 筆)、`CHASEWAIT=1 LIMITN=30`(追空改掛均價等反彈,判一 / 判二都更差)。

`results/` 是各版本的九天 / 五天結果;`studies/` 是 OBV 統計(obv_mean10、obv_grid、turn_pt、rise_pt、trace9、sig12)和選股規則回測(lu_*);`chk/` 是 Playwright 對照 App 的腳本(路徑裡的 `<SIM_DIR>` 要改成本目錄)。

## 雲端 K 回測(2026-10-20 起,和盤中判別式同一份 K)

盤中判別式只能用雲端快照 K(Worker 每分鐘向證交所抓,沒有分鐘內高低、偶爾併根);Yahoo 精確 K 盤中慢 20 分鐘拿不到。
用 Yahoo K 回測算出來的不是盤中那套系統的表現(10/20 同一天:判二 雲端 K −8,392 / Yahoo K +13,600,進的股票大半不同)。

- 每日 14:30 排程先跑 `save_cloud_bars.py`(在換明天名單之前):把當天雲端 1 分K 存到 `kline_cloud/<代號>/<週>.csv`、名單 + 昨收存到 `kline_cloud/pools/<日期>.json`。
  同一時間標籤兩根(開盤撮合 + 09:01 快照都標 09:00)併成一根;Worker 從 10/20 起也已經這樣回傳。kline_cloud 不刪(一天約 1.9 MB)。
- 模擬器 `KSRC=cloud`:當天的 K 改讀 kline_cloud,沒成交的分鐘照 App 的 fillFlat 補平 K(和手機算訊號的 K 一模一樣)。
- `agg_cloud.py "<旗標>"`:跑 kline_cloud/pools 裡每一天。從 10/20 開始累積,之後的規則修改請以這份為準,Yahoo K 的九天數字只當「理想 K 的上限」參考。

10/20 雲端 K(第一天):判一 6 筆 5 賺 1 賠 +13,960、判二 9 筆 3 賺 6 賠 −8,392、判三 4 筆 1 賺 3 賠 −12,389、判四 6 筆 5 賺 1 賠 +35,494(結果 `results/cloud_j1~4.txt`)。

2026-10-20 18:00 判一、判二停損改起漲 / 起跌點(上面旗標已加;判二多 STOPMIN=1.5)。比較舊數字時記得:改之前的判一 / 判二旗標沒有 STOPMODE。

2026-10-20 20:00 震盪盤不進場:判一、判二、判四旗標加 CHOP=1 CHOPW=45;判三不加。


2026-10-20 21:50 判三 / 判四對調:判三 = 壓線空點(MODE=press)、判四 = 燿華型(MODE=brk)。`chk/rs3_cmp2.py 三` / `四` 跟著對調。results/ 裡在這之前的檔名 j3_* = 燿華型、j4_* = 壓線空點。

2026-10-21 `BLK=1`:Yahoo 鉅額交易那根量不算(和 App `dropBlockTrades` 同一套:量 ≥ 同日前後 10 根中位數 80 倍且 ≥ 300 張 → 改成中位數,09:00、13:30 除外;量比基準的前 5 天也一樣)。
起因:光鼎 10/20 11:20 Yahoo 一根 7,291 張,OBV 斷崖,元大沒有;10/19、10/20 約 4 成股票日有這種根。
九天 Yahoo K:判一 26 筆 73% +59,277 不變;判二 45 筆 71% +110,730 → +100,234(3 筆不同);判三 4 筆不變。10/20:三份淨額都不變(`results/blk9_*`、`blk20_*`)。
盤中用雲端 K(證交所快照,沒有鉅額交易),本來就不受影響。

2026-10-21 判一「平」(OBMASLOPE,OBV 均線 5 根斜率門檻)放寬回測(使用者:時碩 11:15 空✓被平擋):九天 Yahoo K(BLK=1)/ 10/20 雲端 K
- 4(現行):26 筆 73% +59,277 / 4 筆 100% +18,906
- 3:27 筆 70% +55,517 / 5 筆 100% +26,480
- 2:33 筆 66% +64,025 / 6 筆 100% +29,054
- 1:38 筆 60% +59,288 / 7 筆 100% +38,066
- 0(關):41 筆 56% +48,351 / 7 筆 100% +38,111
(`results/j1_obmaslope*`)。時碩 10/21 11:15 放寬到 2~3 仍被「向」(OBVDIR)擋,≤1 才在 11:27 進。

2026-10-21 `SHRINK`(量縮下跌不做空:做空時最近 10 根均量 ÷ 今天每分鐘均量 < 門檻只記錄;使用者 GIS「回測量縮下跌不做空」)。九天 Yahoo K(BLK=1)/ 10/20 雲端 K:
- 判一:現行 26 筆 73% +59,277 / 4 筆 +18,906;0.3 一樣 / 3 筆 +11,005;0.5 23 筆 73% +55,321 / 2 筆 +4,153
- 判二:現行 45 筆 71% +100,234 / 3 筆 +8,034;0.3 43 筆 72% +98,660 / 2 筆 +750;0.5 36 筆 77% +97,580 / 2 筆 +750
- 判三:現行 4 筆 100% +18,831 / 3 筆 +14,662;0.3 一樣;0.5 1 筆 +3,647 / 1 筆 +7,736
擋掉的空單賺賠都有(判二 0.5 擋掉 5 賠也擋掉 5 賺),淨額都變少;GIS 10/21 的空單量比 0.49~0.63 擋不到。沒採用(`results/shrink_*`)。

2026-10-21 GIS(紅盤 +1.2% 在今低附近做空,之後漲停)延伸回測,九天 Yahoo K(BLK=1)/ 10/20 雲端 K:
- 只做黑盤空(SHORTMAXPCT=0):判一 25 筆 76% +62,521 / 3 筆 +11,005;判二 39 筆 71% +80,571 / 1 筆 +750
- 當日漲 ≤1% 才做空(SHORTMAXPCT=1):判一 25 筆 76% +62,530 / 3 筆 +11,005;判二 43 筆 69% +84,482 / 3 筆 +8,034
- 離今低 <0.5% 不空(MINROOM=0.5):判一 14 筆 57% +28,855;判二 32 筆 65% +68,066
- 離今低 <1% 不空(MINROOM=1):判一 8 筆 50% +12,354;判二 17 筆 76% +43,353
(現行:判一 26 筆 73% +59,277 / 4 筆 +18,906;判二 45 筆 71% +100,234 / 3 筆 +8,034)。沒採用,`results/gis_*`。

2026-10-21 判二放寬回測(使用者:「回測判二放寬 3.5% 不追跟離均價 1% 好再改 不好不改」),九天 Yahoo K(BLK=1):
- 現行(ENTRYMAX 3.5 / MAXDEV 1):45 筆 71% +100,234;10/20 雲端 3 筆 +8,034
- ENTRYMAX 4.5:49 筆 69% +95,092;5.5:50 筆 70% +99,476
- MAXDEV 1.5:48 筆 70% +103,415(10/20 雲端 5 筆 80% +22,460);MAXDEV 2:同 1.5
- 4.5 + 1.5:52 筆 69% +98,273;5.5 + 2:54 筆 68% +93,561
GIS 10/21 放寬後仍不進(多方只有第 1 個訊號,沒有確認點)。`results/j2_loose_*`。
判三鏡像做多(PRESSSIDE=long):九天 1 筆 −1,275;both 5 筆 80% +17,556(現行 4 筆 100% +18,831)。沒採用,`results/j3_press_*`。

2026-10-21 出場規則回測(使用者:今天和益 +1.6%、台郡 +0.9% 沒保住 → 檢討 →「跑」),九天 Yahoo K(BLK=1):
- 判一:現行 26 筆 73% +59,277;保本 1% 48% +22,700;保本 1.5% 51% +26,876;曾賺 1% 算獲利中(OBVXPEAK=1)76% +57,887;OBV 出場門檻 0.5%(OBVXMIN=0.5)80% +62,681
- 判二:現行 45 筆 71% +100,234;保本 1% 54% +63,618;保本 1.5% 63% +86,280;OBVXPEAK=1 66% +70,599;OBVXMIN=0.5 73% +100,288
雲端 K 兩天(10/20、10/21):判一 現行 5 筆 +16,970 / 0.5% +3,203(聯嘉、正文、中揚光提早出場);判二 一樣 +1,267。都沒採用(`results/exit_*`)。

2026-10-21 大盤寬度(名單上漲家數比例,`results/breadth_2026-10-06_21.json`)偏多 ≥ 80% 時做空變嚴格、偏空 ≤ 20% 時做多變嚴格(使用者:「應該是偏多 少做空(讓做空變嚴格)反之亦然」)。BREADTH=檔 BRTH=0.8 BRMODE=room(離高低點 ≥1%)/ black(做空只黑盤、做多只紅盤)/ both / ban。九天 Yahoo K(BLK=1)/ 雲端 K 兩天:
- 判一:現行 26 筆 73% +59,277 / 5 筆 +16,970;room 24 筆 70% +48,942 / 4 筆 +18,906;black 26 筆 73% +59,268 / 3 筆 +11,005;both 70% +48,933;ban 69% +42,097
- 判二:現行 45 筆 71% +100,234 / 7 筆 57% +1,267;room 44 筆 70% +93,921 / 3 筆 +8,034;black 44 筆 70% +93,128 / 5 筆 80% +14,753;both 69% +86,815;ban 68% +77,723
判二 black 只動到 3 筆:九天擋掉南亞 10/16 空單 +7,106(賺),雲端擋掉 10/21 瑞軒 −6,812、GIS −6,674(賠)。樣本太少,沒採用。
2026-10-21 17:20 使用者「改 判二 大盤偏多只做黑盤空」→ 判二採用 black(BRTH=0.8)。判二旗標從此加 `BREADTH=results/breadth_2026-10-06_21.json BRTH=0.8 BRMODE=black`(新的一天要先把寬度加進這份檔,或用 Worker `?breadth=YYYY-MM-DD`)。`chk/rs3_cmp2.py 二` 已帶上,210 檔日 App 對模擬器 0 差異。
