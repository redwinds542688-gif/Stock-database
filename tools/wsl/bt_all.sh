#!/usr/bin/env bash
# 2026-11-28 判別式任務 #32:電腦(WSL)全 16 個判別式總表重跑,報耗時 + 每表結果指紋(和雲端同一 commit 逐表比對)
# 用法:bash ~/bt/Stock-database/tools/wsl/bt_all.sh   (盤中 08:45~13:45 不要跑)
set -e
source "$HOME/bt/env.sh"
git -C "$HOME/bt/stock-logic" pull -q; git -C "$HOME/bt/Stock-database" pull -q
echo "Stock-logic $(git -C "$HOME/bt/stock-logic" log -1 --format=%h)、Stock-database $(git -C "$HOME/bt/Stock-database" log -1 --format=%h)、CPU $(nproc) 核、回測 $NP 個"
cd "$HOME/bt/stock-logic/backtest/tools"; T0=$(date +%s)
for rs in 十 九 一2 六3 七3 八 二 五 四 七2 三2 一 三 七 六 六4; do
  t=$(date +%s); rm -f "table/${rs}_pcfull.json"
  TAG=pcfull python allsig.py "$rs" 2>&1 | grep -v "^⚠" | tail -1 | cut -c1-30
  echo "   $rs $(( $(date +%s) - t )) 秒"
done
echo "== 16 表共 $(( ($(date +%s) - T0) / 60 )) 分 $(( ($(date +%s) - T0) % 60 )) 秒"
python - <<'P'
import json, hashlib, glob
out = []
for f in sorted(glob.glob('table/*_pcfull.json')):
    T = json.load(open(f)); k = sorted(T, key=lambda r: (r['day'], r['code']))
    out.append(f.split('/')[-1].split('_')[0] + ':' + hashlib.sha1(json.dumps(k, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:10])
print('結果指紋(整段截圖給程式聊天室)'); print(' '.join(out))
P
