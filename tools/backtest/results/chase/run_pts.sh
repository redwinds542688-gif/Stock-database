#!/bin/bash
# 2026-10-20 起漲 / 起跌點最高(空)/ 最低(多)當停損(使用者:「做空就以最高為準,不管是起漲或起跌;做多就找最低點」)
cd /home/user/Stock-database/tools/backtest
R=results/chase
J1="OBMASLOPE=4 FPGAP=2.5 TAKE=2.5 VWAPF=1 FP=1 MAXDEV=1 MIRROR=1 PENDVOL=1 VOLMIN=6000 RVOLMIN=0.9 CAP=350000 REVOK=1 ENTRYMAX=3.5 OBVX=2 OBVXMIN=1 OBVX2=1 OB2=30 SLOPEMIN=-99 GAPMODE=dir GAPTH=2 OB3MA=6 REDSLOPE=-2 EXITRESET=tp OBVDIR=30 NEARHL=2 NOWEAK=1"
J2="FPGAP=2.5 TAKE=2.5 VWAPF=1 FP=1 MAXDEV=1 MIRROR=1 PENDVOL=1 VOLMIN=6000 RVOLMIN=0.9 CAP=350000 REVOK=1 ENTRYMAX=3.5 OBVX=2 OBVXMIN=1 OBVX2=1 OB2=30 SLOPEMIN=-99 GAPMODE=dir GAPTH=2 OB3MA=6 REDSLOPE=-2 EXITRESET=tp MODE=cross XWIN=10 OBMASLOPE=0 XSTR=2 XNEWHI=1 XOB2=20 XOB2DIR=slope XKMA=0 NOLOCK=1 STRONG=1 SREQ=1030,dx,ext SBARS=2 SWIN=5 STRONGMAX=4 SNOAFTER=11:30 MINGAP=4 NOENTRYAFTER=12:00 XOB3=30 WEAKORDER=1 OBVDIR=30 NEARHL=2"
(python3 agg9.py "$J1 STOPMODE=pts STOPSCOPE=last2" > $R/j1_pts2.txt; python3 agg_cloud.py "$J1 STOPMODE=pts STOPSCOPE=last2" > $R/cloud_j1_pts2.txt) &
(python3 agg9.py "$J1 STOPMODE=pts STOPSCOPE=day" > $R/j1_ptsd.txt; python3 agg_cloud.py "$J1 STOPMODE=pts STOPSCOPE=day" > $R/cloud_j1_ptsd.txt) &
(python3 agg9.py "$J2 STOPMODE=pts STOPSCOPE=last2 STOPMIN=1.5" > $R/j2_pts2_m15.txt; python3 agg_cloud.py "$J2 STOPMODE=pts STOPSCOPE=last2 STOPMIN=1.5" > $R/cloud_j2_pts2_m15.txt) &
(python3 agg9.py "$J2 STOPMODE=pts STOPSCOPE=day STOPMIN=1.5" > $R/j2_ptsd_m15.txt; python3 agg9.py "$J1 STOPMODE=pts STOPSCOPE=last2 STOPCAP=3" > $R/j1_pts2_c3.txt) &
wait
for f in j1_base j1_os j1_pts2 j1_ptsd j1_pts2_c3 j2_base j2_os_m15 j2_pts2_m15 j2_ptsd_m15 cloud_j1_pts2 cloud_j1_ptsd cloud_j2_pts2_m15; do echo "== $f: $(grep 合計 $R/$f.txt)"; done
