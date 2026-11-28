#!/usr/bin/env bash
# 2026-11-28 判別式任務 #34:電腦自動接回測工作(Windows 工作排程器每 5 分鐘叫;手動跑也可以)。說明見 bt_jobd.py 開頭
[ -f "$HOME/bt/env.sh" ] && source "$HOME/bt/env.sh" >/dev/null 2>&1
exec python3 "$HOME/bt/Stock-database/tools/wsl/bt_jobd.py" "$@"
