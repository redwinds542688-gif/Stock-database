#!/usr/bin/env bash
# 2026-11-28 使用者「我想改用電腦作為迴測的工具」(判別式任務 #32):WSL(Ubuntu)裡一次裝好回測環境
# 用法:先照聊天室給的 3 行(裝 gh、登入、下載 Stock-database),再跑 bash ~/bt/Stock-database/tools/wsl/setup_backtest.sh
#   (已經下載過就直接跑,可以重複跑)
# 做的事:① 裝 git / gh / curl;② 裝 uv 和 Python 3.13 + pandas 3.0.5 / numpy 2.5.3 / playwright 1.65.1(和雲端同版本,結果才會一模一樣)
#        ③ 登入 GitHub(瀏覽器輸入代碼,只要一次)④ 下載 Stock-logic、Stock-database 到 ~/bt ⑤ 試跑一個判別式三天
# 只裝在 WSL 裡,不動 Windows、不動 C:\Yuanta 的中繼 / 引擎。盤中(08:45~13:45)回測程式會自己擋下不跑。
set -e
echo "== ① 系統套件(會問 WSL 的密碼)"
sudo apt-get update -qq
sudo apt-get install -y -qq git curl gh ca-certificates >/dev/null
echo "== ② Python 3.13 + 套件(和雲端同版本)"
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
uv venv -q -p 3.13 "$HOME/bt/venv" 2>/dev/null || true
uv pip install -q -p "$HOME/bt/venv/bin/python" pandas==3.0.5 numpy==2.5.3 playwright==1.65.1
"$HOME/bt/venv/bin/python" -m playwright install --with-deps chromium >/dev/null
echo "== ③ GitHub 登入(畫面會給一組代碼,打開網址貼上代碼按授權;已登入會跳過)"
gh auth status >/dev/null 2>&1 || gh auth login -h github.com -p https -w
gh auth setup-git
echo "== ④ 下載程式和資料到 ~/bt"
mkdir -p "$HOME/bt"; cd "$HOME/bt"
[ -d stock-logic ] || gh repo clone redwinds542688-gif/Stock-logic stock-logic -- -q
[ -d Stock-database ] || gh repo clone redwinds542688-gif/Stock-database Stock-database -- -q --depth 30
(cd stock-logic && git pull -q) ; (cd Stock-database && git pull -q)
cat > "$HOME/bt/env.sh" <<'E2'
export PATH="$HOME/bt/venv/bin:$HOME/.local/bin:$PATH"
export STOCKDB="$HOME/bt/Stock-database" RUN_ON_PC=1 NP=$(( $(nproc) - 2 ))
cd "$HOME/bt/stock-logic"
E2
grep -q 'bt/env.sh' "$HOME/.bashrc" || echo 'alias bt="source $HOME/bt/env.sh"' >> "$HOME/.bashrc"
echo "== ⑤ 試跑判九三天(盤中會自己擋)"
source "$HOME/bt/env.sh"
python -c "import os; print('CPU', os.cpu_count(), '核 → 回測用', os.environ['NP'], '個')"
cd backtest/tools; mkdir -p cache; cp -f breadth_src/*.json cache/; t0=$(date +%s)
TAG=pctest python allsig.py 九 --days 2026-11-12,2026-11-19,2026-11-27 | tail -1
echo "判九三天 $(( $(date +%s) - t0 )) 秒(雲端 4 核約 15 秒)"
python - <<'P'
import json, hashlib
T = json.load(open('table/九_pctest.json')); k = sorted(T, key=lambda r: (r['day'], r['code']))
print('結果指紋', hashlib.sha1(json.dumps(k, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16], '(把這行傳給程式聊天室比對)')
P
echo "== 完成。之後開 Ubuntu 打 bt 就進到回測資料夾"
