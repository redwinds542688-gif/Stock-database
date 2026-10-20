#!/bin/bash
# 2026-10-20 起跌點停損(使用者:中揚光 09:25「反彈沒有超過這個都不必贖回」)九天 Yahoo K + 雲端 K
cd /home/user/Stock-database/tools/backtest
R=results/chase
J1="OBMASLOPE=4 FPGAP=2.5 TAKE=2.5 VWAPF=1 FP=1 MAXDEV=1 MIRROR=1 PENDVOL=1 VOLMIN=6000 RVOLMIN=0.9 CAP=350000 REVOK=1 ENTRYMAX=3.5 OBVX=2 OBVXMIN=1 OBVX2=1 OB2=30 SLOPEMIN=-99 GAPMODE=dir GAPTH=2 OB3MA=6 REDSLOPE=-2 EXITRESET=tp OBVDIR=30 NEARHL=2 NOWEAK=1"
J2="FPGAP=2.5 TAKE=2.5 VWAPF=1 FP=1 MAXDEV=1 MIRROR=1 PENDVOL=1 VOLMIN=6000 RVOLMIN=0.9 CAP=350000 REVOK=1 ENTRYMAX=3.5 OBVX=2 OBVXMIN=1 OBVX2=1 OB2=30 SLOPEMIN=-99 GAPMODE=dir GAPTH=2 OB3MA=6 REDSLOPE=-2 EXITRESET=tp MODE=cross XWIN=10 OBMASLOPE=0 XSTR=2 XNEWHI=1 XOB2=20 XOB2DIR=slope XKMA=0 NOLOCK=1 STRONG=1 SREQ=1030,dx,ext SBARS=2 SWIN=5 STRONGMAX=4 SNOAFTER=11:30 MINGAP=4 NOENTRYAFTER=12:00 XOB3=30 WEAKORDER=1 OBVDIR=30 NEARHL=2"
(python3 agg9.py "$J1 STOPMODE=origin" > $R/j1_os.txt; python3 agg9.py "$J1 STOPMODE=origin STOPCAP=3" > $R/j1_os_c3.txt) &
(python3 agg9.py "$J1 STOPMODE=origin STOPMIN=1.5" > $R/j1_os_m15.txt; python3 agg9.py "$J1 STOPMODE=origin STOPMIN=1.5 STOPCAP=3" > $R/j1_os_m15c3.txt) &
(python3 agg9.py "$J2 STOPMODE=origin" > $R/j2_os.txt; python3 agg9.py "$J2 STOPMODE=origin STOPCAP=3" > $R/j2_os_c3.txt) &
(python3 agg9.py "$J2 STOPMODE=origin STOPMIN=1.5" > $R/j2_os_m15.txt; python3 agg_cloud.py "$J1 STOPMODE=origin" > $R/cloud_j1_os.txt; python3 agg_cloud.py "$J2 STOPMODE=origin" > $R/cloud_j2_os.txt) &
wait
for f in j1_base j1_os j1_os_c3 j1_os_m15 j1_os_m15c3 j2_base j2_os j2_os_c3 j2_os_m15 cloud_j1_os cloud_j2_os; do echo "== $f: $(grep 合計 $R/$f.txt)"; done
