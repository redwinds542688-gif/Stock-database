"""2026-11-28 判別式任務 #34(使用者 20:07「除了開盤中 所有電腦可以跑的 都給電腦」):電腦 WSL 自動接回測工作。
Windows 工作排程器每 5 分鐘叫一次(tools/wsl/setup_jobs.sh 裝),每次最多做一個工作:
  ① git pull Stock-database → 看 bt_jobs/*.json(檔名排序,最舊的先做)
  ② 交易日 08:45~13:45 不接(和 pc_guard 一樣);同時只跑一個(和 bt_all.sh 共用鎖 ~/bt/.bt.lock)
  ③ stock-logic 切到工作指定的分支 / commit → 照順序跑 cmds(nice 19)→ 要的報表(alloc)→ 結果到桌面 bt_out/jobs/<name>/
  ④ Stock-database 推 bt_jobs/done/<name>.txt(摘要 + 最後幾行紀錄,不推大檔),工作檔搬到 bt_jobs/done/;失敗也寫 done
  ⑤ stock-logic 切回 main

工作檔格式(判別式聊天寫,放 Stock-database/bt_jobs/<時間>_<名稱>.json):
{
  "name": "pullv_grid",                       # 結果資料夾名稱(英數 _ -)
  "branch": "verify-1128-pullv",              # stock-logic 分支(不給 = main);"commit": "abc123" 可指定 commit
  "cwd": "backtest/tools",                    # 指令在哪個資料夾跑(相對 stock-logic,預設 backtest/tools)
  "env": {"EXTRA": "PVIX=1", "TAG": "pv1"},   # 給每個指令的環境變數(可省)
  "cmds": ["python3 allsig.py 強回", "python3 alloc.py"],   # 依序執行(bash),一個失敗就停
  "report": ["alloc"],                        # 跑完另外附的報表:alloc = alloc.py 現行報表(可省)
  "outputs": ["table/強回*.json"],            # 要複製到桌面結果資料夾的檔案(相對 cwd,可用 *,可省)
  "timeout_min": 240                          # 整個工作最多幾分鐘(預設 240)
}
手動跑一次:bash ~/bt/Stock-database/tools/wsl/bt_jobd.sh(有工作就做一個)"""
import datetime, fcntl, glob, json, os, re, shutil, subprocess, sys, time

HOME = os.path.expanduser("~"); BT = os.path.join(HOME, "bt")
SD = os.path.join(BT, "Stock-database"); SL = os.path.join(BT, "stock-logic")
JOBS = os.path.join(SD, "bt_jobs"); DONE = os.path.join(JOBS, "done")
DESK = os.environ.get("BT_OUT", "/mnt/c/Users/redwi/Desktop/bt_out")
LOGF = os.path.join(BT, "logs", "jobd.log")


def tw(): return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))


def log(*a):
    s = tw().strftime("%F %T ") + " ".join(str(x) for x in a)
    print(s, flush=True)
    try:
        os.makedirs(os.path.dirname(LOGF), exist_ok=True)
        with open(LOGF, "a", encoding="utf-8") as f: f.write(s + "\n")
    except Exception: pass


def git(repo, *a, check=True):
    r = subprocess.run(["git", "-C", repo, *a], capture_output=True, text=True)
    if check and r.returncode: raise RuntimeError(f"git {' '.join(a)}:{(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout.strip()


def market_open():
    n = tw(); hm = n.strftime("%H:%M")
    return n.weekday() < 5 and "08:45" <= hm < "13:45"


def push_sd(msg):
    for i in range(5):
        try:
            git(SD, "commit", "-q", "-m", msg, check=False)
            git(SD, "pull", "-q", "--rebase", "--autostash")
            git(SD, "push", "-q"); return True
        except Exception as e:
            log("推 Stock-database 失敗,重試", i + 1, e); time.sleep(10 * (i + 1))
    return False


def run_job(path):
    fn = os.path.basename(path)
    try: J = json.load(open(path, encoding="utf-8"))
    except Exception as e: J = {"name": fn[:-5]}; return finish(path, J, "失敗", f"工作檔讀不到:{e!r}", "", 0, None)
    name = re.sub(r"[^\w\-]", "_", str(J.get("name") or fn[:-5]))[:60]
    out = os.path.join(DESK, "jobs", name); os.makedirs(out, exist_ok=True)
    lp = os.path.join(out, "log.txt"); t0 = time.time(); br = J.get("branch") or "main"
    log("開始工作", fn, "分支", br)
    with open(lp, "w", encoding="utf-8") as L:
        L.write(f"工作 {fn} 開始 {tw():%F %T}\n{json.dumps(J, ensure_ascii=False, indent=1)}\n\n"); L.flush()
        try:
            git(SL, "fetch", "-q", "origin", f"+refs/heads/{br}:refs/remotes/origin/{br}")
            git(SL, "checkout", "-q", "-B", br, f"origin/{br}") if not J.get("commit") else git(SL, "checkout", "-q", "--detach", J["commit"])
        except Exception as e: return finish(path, J, "失敗", f"切分支失敗:{e}", lp, t0, name)
        sha = git(SL, "log", "-1", "--format=%h"); L.write(f"stock-logic {br} {sha}\n\n"); L.flush()
        cwd = os.path.join(SL, J.get("cwd") or "backtest/tools")
        env = dict(os.environ, **{k: str(v) for k, v in (J.get("env") or {}).items()})
        dl = t0 + 60 * float(J.get("timeout_min") or 240); st = "完成"; why = ""
        cmds = list(J.get("cmds") or []) + [("python3 alloc.py 2>&1 | grep -v '^⚠' | tail -40" if r == "alloc" else None) for r in (J.get("report") or [])]
        for c in [x for x in cmds if x]:
            if market_open(): st, why = "中止", "到了開盤時間(08:45)"; break
            L.write(f"$ {c}\n"); L.flush(); ts = time.time()
            try:
                r = subprocess.run(["nice", "-n", "19", "bash", "-lc", c], cwd=cwd, env=env, stdout=L, stderr=subprocess.STDOUT, timeout=max(60, dl - time.time()))
                L.write(f"(結束碼 {r.returncode},{time.time() - ts:.0f} 秒)\n\n"); L.flush()
                if r.returncode: st, why = "失敗", f"指令失敗(結束碼 {r.returncode}):{c[:80]}"; break
            except subprocess.TimeoutExpired: st, why = "失敗", f"超過 {J.get('timeout_min') or 240} 分鐘"; break
        for g in J.get("outputs") or []:
            for f in glob.glob(os.path.join(cwd, g)):
                try: shutil.copy2(f, out)
                except Exception as e: L.write(f"複製 {f} 失敗 {e!r}\n")
    try: git(SL, "checkout", "-q", "main"); git(SL, "pull", "-q", "--rebase", "--autostash", check=False)
    except Exception as e: log("stock-logic 切回 main 失敗", e)
    return finish(path, J, st, why, lp, t0, name, sha)


def finish(path, J, st, why, lp, t0, name, sha=""):
    fn = os.path.basename(path); name = name or fn[:-5]; os.makedirs(DONE, exist_ok=True)
    tail = ""
    try: tail = "".join(open(lp, encoding="utf-8").readlines()[-30:]) if lp else ""
    except Exception: pass
    mins = (time.time() - t0) / 60 if t0 else 0
    head = f"{st} {name}|{tw():%F %T}|耗時 {mins:.1f} 分|stock-logic {J.get('branch') or 'main'} {sha}|結果:桌面 bt_out/jobs/{name}/" + (f"|原因:{why}" if why else "")
    with open(os.path.join(DONE, f"{fn[:-5]}.txt"), "w", encoding="utf-8") as f: f.write(head + "\n\n最後 30 行紀錄:\n" + tail)
    try:
        if os.path.exists(path): git(SD, "mv", "-f", os.path.relpath(path, SD), os.path.relpath(os.path.join(DONE, fn), SD))
    except Exception:
        try: shutil.move(path, os.path.join(DONE, fn))
        except Exception: pass
    git(SD, "add", "-A", "bt_jobs", check=False)
    log(head); push_sd(f"電腦工作 {st}:{name}({mins:.0f} 分)")
    return st


def main():
    os.makedirs(BT, exist_ok=True)
    lk = open(os.path.join(BT, ".bt.lock"), "w")
    try: fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError: return   # 已經有工作 / bt_all.sh 在跑
    if market_open(): return
    try: git(SD, "pull", "-q", "--rebase", "--autostash")
    except Exception as e: log("Stock-database 拉不下來", e); return
    todo = sorted(glob.glob(os.path.join(JOBS, "*.json")))
    if not todo: return
    run_job(todo[0])


if __name__ == "__main__":
    main()
