#!/bin/bash
cd /home/user/Stock-database/tools/backtest
R=results/chase
J4="FPGAP=2.5 TAKE=2.5 VWAPF=0 FP=1 MAXDEV=99 MIRROR=1 PENDVOL=1 VOLMIN=0 RVOLMIN=0 CAP=350000 REVOK=1 ENTRYMAX=3.5 OBVX=2 OBVXMIN=1 OBVX2=1 OB2=30 SLOPEMIN=-99 GAPMODE=dir GAPTH=99 OB3MA=6 REDSLOPE=-2 EXITRESET=tp OBVDIR=0 NEARHL=0 NOLOCK=1 NOENTRYAFTER=12:00 OBMASLOPE=0 MODE=press"
(python3 agg9.py "$J4 OBVXMIN=99" > $R/j4_base_nox.txt) &
(python3 agg9.py "$J4 PRESSTOUCH=0.5 PRESSCHASE=3 PRESSNL=20 PRESSNLM=0 PRESSRED=1 OBVXMIN=99" > $R/j4_v2a_nox.txt) &
(python3 agg9.py "$J4 PRESSTOUCH=0.5 PRESSCHASE=3 PRESSNL=20 PRESSNLM=0 OBVXMIN=99" > $R/j4_v2c_nox.txt) &
wait
for f in j4_base_nox j4_v2a_nox j4_v2c_nox; do echo "== $f: $(grep 合計 $R/$f.txt)"; done
