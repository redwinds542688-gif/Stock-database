# 回測工具(2026-10-20 打包,原本放在雲端 session 的暫存區)

`sim_day.py` 是和 App `replaySignals()` 對齊的單檔單日模擬器(對照 0 差異,見 chk/rs3_cmp2.py);旗標用環境變數。
`agg1.py 日期 "旗標" 池檔`、`agg5.py "旗標"`(10/13~10/19 五天)、`agg9.py "旗標"`(加 10/06、10/07、10/08、10/12 九天)。
K 線讀 `../../kline/<代號>/2026-W4*.csv`(雲端只留 3 週,10/26 起 W41 會被刪);池檔在 `pools/`(hi350_* 是當天實際名單過 350 元上限、hist_* 從 git 歷史重建,10/06、10/07 歷史不足算不了量比)。

## 三份判別式的旗標(2026-10-20 01:20 為準)

判一(第七次修改後,不做逆勢單):
```
OBMASLOPE=4 FPGAP=2.5 TAKE=2.5 VWAPF=1 FP=1 MAXDEV=1 MIRROR=1 PENDVOL=1 VOLMIN=6000 RVOLMIN=0.9 CAP=350000 REVOK=1 ENTRYMAX=3.5 OBVX=2 OBVXMIN=1 OBVX2=1 OB2=30 SLOPEMIN=-99 GAPMODE=dir GAPTH=2 OB3MA=6 REDSLOPE=-2 EXITRESET=tp OBVDIR=30 NEARHL=2 NOWEAK=1
```
判二:
```
FPGAP=2.5 TAKE=2.5 VWAPF=1 FP=1 MAXDEV=1 MIRROR=1 PENDVOL=1 VOLMIN=6000 RVOLMIN=0.9 CAP=350000 REVOK=1 ENTRYMAX=3.5 OBVX=2 OBVXMIN=1 OBVX2=1 OB2=30 SLOPEMIN=-99 GAPMODE=dir GAPTH=2 OB3MA=6 REDSLOPE=-2 EXITRESET=tp MODE=cross XWIN=10 OBMASLOPE=0 XSTR=2 XNEWHI=1 XOB2=20 XOB2DIR=slope XKMA=0 NOLOCK=1 STRONG=1 SREQ=1030,dx,ext SBARS=2 SWIN=5 STRONGMAX=4 SNOAFTER=11:30 MINGAP=4 NOENTRYAFTER=12:00 XOB3=30 WEAKORDER=1 OBVDIR=30 NEARHL=2
```
判三(燿華型):
```
FPGAP=2.5 TAKE=2.5 VWAPF=0 FP=1 MAXDEV=99 MIRROR=1 PENDVOL=1 VOLMIN=0 RVOLMIN=0 CAP=350000 REVOK=1 ENTRYMAX=3.5 OBVX=2 OBVXMIN=1 OBVX2=1 OB2=30 SLOPEMIN=-99 GAPMODE=dir GAPTH=99 OB3MA=6 REDSLOPE=-2 EXITRESET=tp OBVDIR=0 NEARHL=0 NOLOCK=1 NOENTRYAFTER=12:00 MODE=brk BRKBELOW=1 BRKVBASE=pre OBMASLOPE=0
```
測試用旗標(都沒採用):`NOENTRYAFTER`、`OPENMOVE=0.7`(價先走 0.7%)、`SHORTMAXPCT=-0.01`(做空只做黑盤,判一九天 67% → 78%,使用者還沒說改)、`REVWEAK=1`(逆勢單反向做,更差)、`ENTER1=1`(第一訊號就進,更差)、`BRKSIDE=short`/`BRKREV=1`(判三做空鏡像 / 反向做,都賠)。

`results/` 是各版本的九天 / 五天結果;`studies/` 是 OBV 統計(obv_mean10、obv_grid、turn_pt、rise_pt、trace9、sig12)和選股規則回測(lu_*);`chk/` 是 Playwright 對照 App 的腳本(路徑裡的 `<SIM_DIR>` 要改成本目錄)。
