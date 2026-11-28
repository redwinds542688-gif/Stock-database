# 電腦(WSL)跑回測 —— 一行指令

**開盤中(交易日 08:45~13:45)不要跑**,程式也會自己擋。

1. 開 PowerShell(或 Windows Terminal)→ 打 `wsl` 進 Ubuntu
2. 貼下面其中一行:

| 要做什麼 | 指令 |
|---|---|
| 平常(規則沒變,只補新的日子,幾分鐘) | `bash ~/bt/Stock-database/tools/wsl/bt_all.sh` |
| 判別式聊天說「電腦跑全表」(全部重跑,約 20 分) | `bash ~/bt/Stock-database/tools/wsl/bt_all.sh --full` |
| 只跑某幾個判別式 | `bash ~/bt/Stock-database/tools/wsl/bt_all.sh 十 九` |
| 跑完順便推回 Stock-logic(收盤後) | 後面加 `--push`,例如 `bash ~/bt/Stock-database/tools/wsl/bt_all.sh --full --push` |

跑完結果會自動複製到桌面 **bt_out** 資料夾(判別式聊天直接讀):`table/`(總表)、`bt_last.log`(這次的紀錄)、`LAST.txt`(時間、版本、耗時、結果指紋)。

第一次安裝(已經裝好的不用再做):見 `setup_backtest.sh` 開頭說明。

## 自動接回測工作(判別式任務 #34)

裝一次就好(Ubuntu 裡):`bash ~/bt/Stock-database/tools/wsl/setup_jobs.sh`
之後電腦開著、登入中,每 5 分鐘自己去 Stock-database `bt_jobs/` 拿工作來跑(開盤中 08:45~13:45 不接、一次一個、低優先權),
結果在桌面 `bt_out/jobs/<名稱>/`,摘要推到 `bt_jobs/done/`。要停:PowerShell 打 `schtasks /Delete /TN bt_jobs /F`。
工作檔格式見 `bt_jobd.py` 開頭。手動跑一次:`bash ~/bt/Stock-database/tools/wsl/bt_jobd.sh`。紀錄:`~/bt/logs/jobd.log`。
