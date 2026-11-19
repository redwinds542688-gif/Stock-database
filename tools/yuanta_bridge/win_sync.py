"""雲端 → 電腦同步(2026-11-19 19:30 使用者「雲端更新,電腦也有跟著更新」→「現在馬上做」)

把 GitHub 三個庫(Stock-database / Stock-logic / Lottery-database)的最新版,同步成電腦上的資料夾:
  C:\\Yuanta\\雲端備份\\Stock-database\\...   (和 GitHub 上的檔案一模一樣)
  C:\\Yuanta\\雲端備份\\Stock-logic\\...
  C:\\Yuanta\\雲端備份\\Lottery-database\\...
做法:經 Worker ?gh=tree 拿最新提交的檔案清單(路徑 + git blob 編號),和本機 .sync_index.json 比,
      只下載變了的檔(?gh=blob,內容定址),下載後重算 git blob 編號確認無誤才換上(先寫 .tmp 再改名)。
  - GitHub 上刪掉的檔:電腦這邊不刪(規定:電腦上的檔不自動刪),只在 .sync_index.json 記「upstream_removed」
  - 歷史版本:每天 15:53 雲端排程另外存 git bundle(同一個資料夾的 *_full_*.bundle / *_<日期>.bundle)
  - 中繼每 10 分鐘叫一次 run();盤中(08:30~13:35)一次最多下載 SYNC_MAX_MARKET 個檔,盤後不限
  - 單獨執行:python win_sync.py  (立刻同步一次並印結果)
不碰 bridge 資料夾、不讀鑰匙;只用 bridge.env 的 WORKER_URL / PUSH_KEY(和中繼抓程式更新同一把)。
"""
import hashlib, json, os, re, sys, time, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEST = Path(os.environ.get("SYNC_DEST", r"C:\Yuanta\雲端備份"))
REPOS = (("db", "Stock-database"), ("logic", "Stock-logic"), ("lottery", "Lottery-database"))
SYNC_MAX_MARKET = int(os.environ.get("SYNC_MAX_MARKET", "200"))


def cfg():
    out = {}
    f = HERE / "bridge.env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1); out[k.strip()] = re.split(r"\s+#", v, 1)[0].strip()
    for k in ("WORKER_URL", "PUSH_KEY"):
        if os.environ.get(k): out[k] = os.environ[k]
    return out


def _get(c, q, timeout=60):
    url = c["WORKER_URL"].rstrip("/") + "/?" + q + f"&_={int(time.time())}"
    req = urllib.request.Request(url, headers={"X-Push-Key": c["PUSH_KEY"], "User-Agent": "win-sync/1"})
    return urllib.request.urlopen(req, timeout=timeout).read()


def blob_sha(b):
    return hashlib.sha1(b"blob %d\0" % len(b) + b).hexdigest()


def in_market():
    t = datetime.now(timezone(timedelta(hours=8)))
    return t.weekday() < 5 and "08:30" <= t.strftime("%H:%M") <= "13:35"


def sync_repo(c, key, name, limit, log):
    root = DEST / name; root.mkdir(parents=True, exist_ok=True)
    idx_f = root / ".sync_index.json"
    try: idx = json.loads(idx_f.read_text(encoding="utf-8"))
    except Exception: idx = {}
    files0 = idx.get("files", {})
    tree = json.loads(_get(c, f"gh=tree&repo={key}"))
    if idx.get("commit") == tree["commit"] and not idx.get("pending"):
        return 0, 0, tree["commit"]
    want = {p: s for p, s, _ in tree["files"]}
    files = dict(files0)
    for p, s in want.items():   # 2026-11-19 20:05 中途關機 / 重開:紀錄沒存到的檔,本機已經是對的內容就直接記上,不重抓
        if files.get(p) != s and (root / p).is_file():
            try:
                if blob_sha((root / p).read_bytes()) == s: files[p] = s
            except Exception: pass
    todo = [p for p, s in want.items() if files.get(p) != s or not (root / p).exists()]
    done = 0; bad = 0
    for p in todo[:limit] if limit else todo:
        try:
            b = _get(c, f"gh=blob&repo={key}&sha={want[p]}&ref={tree['commit']}&path={urllib.parse.quote(p)}", timeout=120)
            if blob_sha(b) != want[p]: bad += 1; continue
            dst = root / p; dst.parent.mkdir(parents=True, exist_ok=True)
            tmp = dst.with_name(dst.name + ".synctmp"); tmp.write_bytes(b); tmp.replace(dst)
            files[p] = want[p]; done += 1
            if done % 200 == 0:   # 每 200 個檔存一次進度(中途關機下次接著抓)
                tmp = idx_f.with_name(idx_f.name + ".synctmp"); tmp.write_text(json.dumps(dict(idx, files=files, pending=1), ensure_ascii=False), encoding="utf-8"); tmp.replace(idx_f)
        except Exception as e:
            bad += 1
            if bad <= 3: log(f"同步 {name}:{p} 失敗 {e}")
    removed = sorted(set(files0) - set(want))
    left = len(todo) - done
    idx = {"repo": name, "commit": tree["commit"], "at": tree.get("at"), "synced": datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S"),
           "pending": left, "upstream_removed": removed, "files": {p: files[p] for p in files if p in want}}
    tmp = idx_f.with_name(idx_f.name + ".synctmp"); tmp.write_text(json.dumps(idx, ensure_ascii=False), encoding="utf-8"); tmp.replace(idx_f)
    return done, left, tree["commit"]


def run(log=print):
    c = cfg()
    if not (c.get("WORKER_URL") and c.get("PUSH_KEY")): log("同步:bridge.env 沒有 WORKER_URL / PUSH_KEY"); return
    limit = SYNC_MAX_MARKET if in_market() else 0
    for key, name in REPOS:
        try:
            done, left, sha = sync_repo(c, key, name, limit, log)
            if done or left: log(f"同步 {name}:更新 {done} 個檔" + (f",還有 {left} 個下次補" if left else "") + f"(版本 {sha[:7]})")
        except Exception as e:
            log(f"同步 {name}:失敗 {e}")


if __name__ == "__main__":
    run()
