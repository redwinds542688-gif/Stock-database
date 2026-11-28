# 電腦自動接回測工作(判別式任務 #34)

判別式聊天把工作寫成 `bt_jobs/<YYYYMMDD_HHMM>_<名稱>.json` 推到 main,使用者電腦(WSL)每 5 分鐘來拿,檔名排序最舊的先做,一次一個。
做完:結果 → 桌面 `bt_out/jobs/<name>/`(log.txt + outputs),摘要 → `bt_jobs/done/<同檔名>.txt`,工作檔搬到 `bt_jobs/done/`。失敗 / 開盤中止也會寫 done。

```json
{
  "name": "pullv_grid",
  "branch": "verify-1128-pullv",
  "cwd": "backtest/tools",
  "env": {"EXTRA": "PVIX=1 PVFROM=10:00", "TAG": "pv1"},
  "cmds": ["python3 allsig.py 強回"],
  "report": ["alloc"],
  "outputs": ["table/強回_pv1.json"],
  "timeout_min": 240
}
```
- `branch` 不給 = main;`commit` 可指定 commit。`cmds` 依序在 stock-logic/`cwd` 用 bash 跑(nice 19),一個失敗就停。
- 整支腳本也行:`"cmds": ["bash ../studies/pullv_1128/grid.sh"]`。
- 跑完 stock-logic 會切回 main。和 `bt_all.sh` 共用鎖,不會同時跑。
