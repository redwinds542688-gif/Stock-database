"""更新桌機的中繼程式檔(2026-10-29 16:16 repo 改私人後 curl.exe 直接抓 raw.githubusercontent 會 404,改經 Worker 代讀)

用法(C:\\Yuanta\\bridge):
  python win_update.py <commit>                 # 抓那個版本的 yuanta_bridge.py bars.py trader.py quotes.py win_pnl.py engine.py win_keys.py win_update.py hist_kline.py win_install.py elig.py(清單讀 repo 的 FILES)
  python win_update.py <commit> engine.py       # 只抓指定的檔
抓完印每個檔的大小;要重啟中繼:Stop-ScheduledTask YuantaBridge; Start-ScheduledTask YuantaBridge(盤中不要)
"""
import sys, time, urllib.request, re
from pathlib import Path

HERE = Path(__file__).resolve().parent
FILES = ["yuanta_bridge.py", "bars.py", "trader.py", "quotes.py", "win_pnl.py", "engine.py", "win_keys.py", "win_update.py", "hist_kline.py", "win_install.py", "elig.py"]


def cfg():
    out = {}
    f = HERE / "bridge.env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1); out[k.strip()] = re.split(r"\s+#", v, 1)[0].strip()
    return out


def gh(c, path, ref):
    url = c["WORKER_URL"].rstrip("/") + f"/?gh=raw&path={path}&ref={ref}&_={int(time.time())}"
    return urllib.request.urlopen(urllib.request.Request(url, headers={"X-Push-Key": c["PUSH_KEY"], "User-Agent": "win-update/1"}), timeout=60).read()


def main(a):
    if not a: print(__doc__); return
    ref = a[0]; files = a[1:] or FILES; c = cfg()
    if not (c.get("PUSH_KEY") and c.get("WORKER_URL")): print("bridge.env 要有 WORKER_URL 和 PUSH_KEY"); return
    if not a[1:]:
        try:   # 2026-10-30 檔案清單改讀 repo 的 tools/yuanta_bridge/FILES(新增檔案也抓得到;抓不到用上面寫死的)
            lst = [x.strip() for x in gh(c, "tools/yuanta_bridge/FILES", ref).decode("utf-8").splitlines() if x.strip().endswith(".py")]
            if lst: files = lst
        except Exception: pass
    for f in files:
        url = c["WORKER_URL"].rstrip("/") + f"/?gh=raw&path=tools/yuanta_bridge/{f}&ref={ref}&_={int(time.time())}"
        try:
            b = urllib.request.urlopen(urllib.request.Request(url, headers={"X-Push-Key": c["PUSH_KEY"], "User-Agent": "win-update/1"}), timeout=60).read()
        except Exception as e:
            print(f"{f}: 失敗 {e}"); continue
        tmp = HERE / (f + ".tmp"); tmp.write_bytes(b); tmp.replace(HERE / f)
        print(f"{f}: {len(b):,} bytes")


if __name__ == "__main__":
    main(sys.argv[1:])
