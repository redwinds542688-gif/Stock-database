# 回測工具(2026-10-20 打包,原本放在雲端 session 的暫存區)

`sim_day.py` 是和 App `replaySignals()` 對齊的單檔單日模擬器(對照 0 差異,見 chk/rs3_cmp2.py);旗標用環境變數。
`agg1.py 日期 "旗標" 池檔`、`agg5.py "旗標"`(10/13~10/19 五天)、`agg9.py "旗標"`(加 10/06、10/07、10/08、10/12 九天)。
K 線讀 `../../kline/<代號>/2026-W4*.csv`(雲端只留 3 週,10/26 起 W41 會被刪);池檔在 `pools/`(hi350_* 是當天實際名單過 350 元上限、hist_* 從 git 歷史重建,10/06、10/07 歷史不足算不了量比)。

## 三份判別式的旗標(2026-10-20 01:20 為準)

判一(第七次修改後,不做逆勢單):
```
OBMASLOPE=4 FPGAP=2.5 TAKE=2.5 VWAPF=1 FP=1 MAXDEV=1 MIRROR=1 PENDVOL=1 VOLMIN=6000 RVOLMIN=0.9 CAP=350000 REVOK=1 ENTRYMAX=3.5 OBVX=2 OBVXMIN=1 OBVX2=1 OB2=30 SLOPEMIN=-99 GAPMODE=dir GAPTH=2 OB3MA=6 REDSLOPE=-2 EXITRESET=tp OBVDIR=30 NEARHL=2 NOWEAK=1 STOPMODE=pts STOPSCOPE=last2 STOPKBAR=close STOPCAP=3
```
判二:
```
FPGAP=2.5 TAKE=2.5 VWAPF=1 FP=1 MAXDEV=1 MIRROR=1 PENDVOL=1 VOLMIN=6000 RVOLMIN=0.9 CAP=350000 REVOK=1 ENTRYMAX=3.5 OBVX=2 OBVXMIN=1 OBVX2=1 OB2=30 SLOPEMIN=-99 GAPMODE=dir GAPTH=2 OB3MA=6 REDSLOPE=-2 EXITRESET=tp MODE=cross XWIN=10 OBMASLOPE=0 XSTR=2 XNEWHI=1 XOB2=20 XOB2DIR=slope XKMA=0 NOLOCK=1 STRONG=1 SREQ=1030,dx,ext SBARS=2 SWIN=5 STRONGMAX=4 SNOAFTER=11:30 MINGAP=4 NOENTRYAFTER=12:00 XOB3=30 WEAKORDER=1 OBVDIR=30 NEARHL=2 STOPMODE=pts STOPSCOPE=last2 STOPKBAR=close STOPCAP=3 STOPMIN=1.5 OB10DIR=1
```
判三(燿華型):
```
FPGAP=2.5 TAKE=2.5 VWAPF=0 FP=1 MAXDEV=99 MIRROR=1 PENDVOL=1 VOLMIN=0 RVOLMIN=0 CAP=350000 REVOK=1 ENTRYMAX=3.5 OBVX=2 OBVXMIN=1 OBVX2=1 OB2=30 SLOPEMIN=-99 GAPMODE=dir GAPTH=99 OB3MA=6 REDSLOPE=-2 EXITRESET=tp OBVDIR=0 NEARHL=0 NOLOCK=1 NOENTRYAFTER=12:00 MODE=brk BRKBELOW=1 BRKVBASE=pre OBMASLOPE=0 BRKGAP=0
```
(2026-10-20 01:30 加 `BRKGAP=0` 開高不做:今開對昨收缺口 ≤ 0% 才做,昨收用 K 線前一天收盤;App 對照用 `BRKGAPREF=pool`。九天 26 筆 57% +54,181)
判四(壓線空點,只做空):
```
FPGAP=2.5 TAKE=2.5 VWAPF=0 FP=1 MAXDEV=99 MIRROR=1 PENDVOL=1 VOLMIN=0 RVOLMIN=0 CAP=350000 REVOK=1 ENTRYMAX=3.5 OBVX=2 OBVXMIN=1 OBVX2=1 OB2=30 SLOPEMIN=-99 GAPMODE=dir GAPTH=99 OB3MA=6 REDSLOPE=-2 EXITRESET=tp OBVDIR=0 NEARHL=0 NOLOCK=1 NOENTRYAFTER=12:00 OBMASLOPE=0 MODE=press
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
