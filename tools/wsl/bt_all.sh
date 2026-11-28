#!/usr/bin/env bash
# 2026-11-28 判別式任務 #32 / #33:電腦(WSL)跑總表 → 結果複製到桌面 bt_out(判別式聊天直接讀)
# 用法(Ubuntu 裡):
#   bash ~/bt/Stock-database/tools/wsl/bt_all.sh                 16 個判別式,規則沒變的沿用舊列、只補新的日子(快)
#   bash ~/bt/Stock-database/tools/wsl/bt_all.sh --full          16 個全部刪掉重跑(規則 / 資料大改時)
#   bash ~/bt/Stock-database/tools/wsl/bt_all.sh 十 九           只跑指定的判別式(可加 --full)
#   加 --push:跑完把 backtest/tools/table/ 推回 Stock-logic(判別式聊天 11/28 17:20 定的推送方式;09:00~13:30 不推)
# 回測在 WSL 裡跑(~/bt,快);只把結果複製到 C:\Users\redwi\Desktop\bt_out(覆蓋舊的)+ LAST.txt。盤中(08:45~13:45)allsig 會自己擋下
set -o pipefail
FULL=0; PUSH=0; RSS=()
for a in "$@"; do case "$a" in --full) FULL=1;; --push) PUSH=1;; *) RSS+=("$a");; esac; done
[ ${#RSS[@]} -eq 0 ] && RSS=(十 九 一2 六3 七3 八 二 五 四 七2 三2 一 三 七 六 六4)
source "$HOME/bt/env.sh"
OUT="/mnt/c/Users/redwi/Desktop/bt_out"; LOGD="$HOME/bt/logs"; mkdir -p "$LOGD"
TS=$(TZ=Asia/Taipei date +%Y%m%d_%H%M); LOG="$LOGD/bt_$TS.log"
{
git -C "$HOME/bt/stock-logic" pull -q --rebase --autostash; git -C "$HOME/bt/Stock-database" pull -q
SL=$(git -C "$HOME/bt/stock-logic" log -1 --format=%h); SD=$(git -C "$HOME/bt/Stock-database" log -1 --format=%h)
echo "開始 $(TZ=Asia/Taipei date '+%F %T')|Stock-logic $SL、Stock-database $SD、CPU $(nproc) 核、回測 $NP 個|$([ $FULL = 1 ] && echo 全部重跑 || echo 沿用舊列)|判別式 ${RSS[*]}"
cd "$HOME/bt/stock-logic/backtest/tools"; T0=$(date +%s); BAD=0
for rs in "${RSS[@]}"; do
  t=$(date +%s); [ $FULL = 1 ] && rm -f "table/$rs.json" "table/$rs.fp"
  python allsig.py "$rs" 2>&1 | grep -v "^⚠" | tail -1 | cut -c1-40 || BAD=1
  echo "   $rs $(( $(date +%s) - t )) 秒"
done
echo "== ${#RSS[@]} 表共 $(( ($(date +%s) - T0) / 60 )) 分 $(( ($(date +%s) - T0) % 60 )) 秒"
python - "${RSS[@]}" <<'P'
import json, hashlib, sys, os
out = []
for rs in sys.argv[1:]:
    f = f'table/{rs}.json'
    if not os.path.exists(f): out.append(f'{rs}:沒有'); continue
    k = sorted(json.load(open(f)), key=lambda r: (r['day'], r['code']))
    out.append(rs + ':' + hashlib.sha1(json.dumps(k, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:10])
print('結果指紋', ' '.join(out))
P
echo "== alloc 報表"
python alloc.py 2>&1 | grep -v "^⚠" | tail -40
} 2>&1 | tee "$LOG"
cd "$HOME/bt/stock-logic/backtest/tools"
SL=$(git -C "$HOME/bt/stock-logic" log -1 --format=%h); SD=$(git -C "$HOME/bt/Stock-database" log -1 --format=%h)
if mkdir -p "$OUT/table" 2>/dev/null; then
  cp -f table/*.json table/*.fp "$OUT/table/" 2>/dev/null; cp -f table/*.dfp table/RULES_FP table/CHANGED "$OUT/table/" 2>/dev/null
  cp -f "$LOG" "$OUT/bt_last.log"
  { echo "時間 $(TZ=Asia/Taipei date '+%F %T')"; echo "Stock-logic $SL"; echo "Stock-database $SD"; echo "判別式 ${RSS[*]}"; echo "模式 $([ $FULL = 1 ] && echo 全部重跑 || echo 沿用舊列)"
    grep -E "^== .*表共|^結果指紋" "$LOG"; } > "$OUT/LAST.txt"
  echo "== 已複製到桌面 bt_out(table/、bt_last.log、LAST.txt)"
else echo "!! 桌面 bt_out 寫不進去($OUT)"; fi
if [ $PUSH = 1 ]; then
  HM=$(TZ=Asia/Taipei date +%H%M); DW=$(TZ=Asia/Taipei date +%u)
  if [ "$DW" -le 5 ] && [ "$HM" -ge 0900 ] && [ "$HM" -lt 1330 ]; then echo "!! 09:00~13:30 不推(判別式聊天規定),收盤後再加 --push 跑一次"; exit 0; fi
  cd "$HOME/bt/stock-logic"; git pull -q --rebase --autostash
  git add backtest/tools/table/ backtest/tools/sim_allsig.py 2>/dev/null
  if git diff --cached --quiet; then echo "== 總表沒有變,不推"; else
    git commit -q -m "電腦 WSL 總表 ${RSS[*]} $SL" && git push -q && echo "== 已推 Stock-logic $(git log -1 --format=%h)"; fi
fi
