#!/usr/bin/env bash
# 2026-11-28 判別式任務 #34:安裝「電腦自動接回測工作」(只要裝一次)。Ubuntu 裡打:bash ~/bt/Stock-database/tools/wsl/setup_jobs.sh
# 做三件事:① git 推送用 gh 的登入(bt_jobd 要推 bt_jobs/done)② 寫一個隱藏視窗的啟動檔 C:\Users\<你>\bt\bt_jobs.vbs
#          ③ Windows 工作排程器加「bt_jobs」每 5 分鐘叫一次(不用系統管理員;關掉:schtasks.exe /Delete /TN bt_jobs /F)
set -e
WU=$(cmd.exe /c "echo %USERNAME%" 2>/dev/null | tr -d '\r'); WD="/mnt/c/Users/$WU/bt"; mkdir -p "$WD"
gh auth setup-git >/dev/null 2>&1 && echo "① git 推送登入 OK" || echo "① gh auth setup-git 失敗(之後推不上去再處理)"
printf 'CreateObject("WScript.Shell").Run "wsl.exe -d %s -e bash -lc ""bash ~/bt/Stock-database/tools/wsl/bt_jobd.sh""", 0, False\r\n' "$WSL_DISTRO_NAME" > "$WD/bt_jobs.vbs"
echo "② 啟動檔 C:\\Users\\$WU\\bt\\bt_jobs.vbs"
schtasks.exe /Create /F /SC MINUTE /MO 5 /TN bt_jobs /TR "wscript.exe \"C:\\Users\\$WU\\bt\\bt_jobs.vbs\"" | tr -d '\r'
echo "③ 排程 bt_jobs 每 5 分鐘(電腦開著、登入中才會跑)"
bash ~/bt/Stock-database/tools/wsl/bt_jobd.sh && echo "== 試跑一次 OK(沒有工作就什麼都不做);紀錄 ~/bt/logs/jobd.log"
