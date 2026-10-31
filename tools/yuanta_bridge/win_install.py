"""元大中繼程式 Windows 安裝(2026-10-24 使用者:桌機當元大連線主機、「08:35 自動開機 停在輸入密碼 08:45 自己啟動 背景執行 直接做」)

在命令提示字元執行:  python win_install.py
做的事:
  1. 從 GitHub 下載中繼程式到 C:\\Yuanta\\bridge
  2. 問元大帳號、電子密碼,寫進 C:\\Yuanta\\bridge\\bridge.env(只放這台電腦、只有自己的 Windows 帳號讀得到)
  3. 建 Windows 工作排程「YuantaBridge」:週一到週五 08:40 啟動;那時沒開機就開機後馬上啟動;
     「不論使用者是否登入均執行」→ 開機停在輸入密碼的畫面也會在背景跑(要輸入一次 Windows 登入密碼,不是 PIN)
  4. 先啟動一次,確認紀錄檔有寫出來
第一階段只收行情:元大 1 分 K 存在 C:\\Yuanta\\bridge\\bars_yuanta\\日期.csv,不推雲端、不下單(TRADE_ENABLE=0)。
移除:schtasks /Delete /TN YuantaBridge /F
"""
import getpass, os, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path

REF = "01e722d9"                      # 下載這個版本的中繼程式(固定版本,避免 GitHub 暫存到舊檔)
RAW = f"https://raw.githubusercontent.com/redwinds542688-gif/Stock-database/{REF}/tools/yuanta_bridge/"
BASE = Path(r"C:\Yuanta")
SDK = BASE / "YuantaSparkAPI_win-x64_Python"
APP = BASE / "bridge"
FILES = ["yuanta_bridge.py", "bars.py", "trader.py", "quotes.py", "win_pnl.py", "elig.py"]
TASK = "YuantaBridge"


def _dl(f):
    """2026-10-29 16:16 repo 改私人:bridge.env 有 PUSH_KEY 就經 Worker 代讀(?gh=raw&ref=REF),沒有就直接抓 raw"""
    cfg = {}
    env = APP / "bridge.env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"): k, v = line.split("=", 1); cfg[k.strip()] = v.split("#")[0].strip()
    if cfg.get("PUSH_KEY") and cfg.get("WORKER_URL"):
        req = urllib.request.Request(cfg["WORKER_URL"].rstrip("/") + f"/?gh=raw&path=tools/yuanta_bridge/{f}&ref={REF}&_={int(time.time())}", headers={"X-Push-Key": cfg["PUSH_KEY"], "User-Agent": "win-install/1"})
        return urllib.request.urlopen(req, timeout=60).read()
    return urllib.request.urlopen(RAW + f, timeout=60).read()


def step(n, s):
    print(f"\n=== 第 {n} 步:{s} ===")


def main():
    if sys.platform != "win32":
        sys.exit("這支只給 Windows 用")
    if not (SDK / "YuantaSparkAPI.dll").exists():
        sys.exit(f"找不到 {SDK}\\YuantaSparkAPI.dll(元大元件要先解壓縮到 C:\\Yuanta)")

    step(1, "下載中繼程式")
    APP.mkdir(parents=True, exist_ok=True)
    for f in FILES:
        data = _dl(f)
        (APP / f).write_bytes(data)
        print(f"  {f} {len(data):,} bytes")

    step(2, "元大帳號設定")
    env = APP / "bridge.env"
    keep = False
    if env.exists():
        keep = input("  已經有設定檔,沿用嗎?(直接按 Enter = 沿用,輸入 n = 重新輸入)> ").strip().lower() != "n"
    if not keep:
        acct = input("  證券帳號(分公司 4 碼 + 帳號 7 碼,例如 981R0123456)> ").strip().replace("-", "")
        acct = "S" + (acct[1:] if acct[:1] in ("S", "s") and len(acct) == 12 else acct)
        pwd = getpass.getpass("  電子密碼(輸入時不會顯示)> ")
        if "#" in pwd:
            print("  (密碼裡有 #,設定檔照樣可以讀)")
        env.write_text("\n".join([
            "# 元大中繼程式設定(win_install.py 產生;只放這台電腦,不要上傳、不要傳給別人)",
            "YT_ENV=PROD",
            f"YT_ACCOUNT={acct}",
            f"YT_PASS={pwd}",
            f"YT_SDK_DIR={SDK}",
            "WORKER_URL=https://twse-proxy.redwinds542688.workers.dev",
            "PUSH_KEY=",
            "VOL_MULT=1000",
            "MAX_CODES=200",
            "TRADE_ENABLE=0",
            "QUOTE_ENABLE=0",
            f"LOG_DIR={APP / 'log'}",
            f"BARS_DIR={APP / 'bars_yuanta'}",
            ""]), encoding="utf-8")
        pwd = None
        user = os.environ.get("USERNAME", "")
        subprocess.run(["icacls", str(env), "/inheritance:r", "/grant:r", f"{user}:F"], capture_output=True)   # 只有自己讀得到
        print(f"  已寫入 {env}")

    step(3, "建立工作排程(要輸入 Windows 登入密碼)")
    print("  說明:這是登入 Windows 用的密碼(不是元大密碼,也不是 PIN 碼);用微軟帳號登入的話就是微軟帳號密碼。")
    print("  Windows 會加密保存,只給工作排程用。")
    ps = f"""
$ErrorActionPreference = 'Stop'
$u = "$env:USERDOMAIN\\$env:USERNAME"
# 2026-10-24 Get-Credential 的視窗會被黑色視窗擋住找不到 → 改在黑色視窗裡直接輸入(不顯示)
Write-Host "  Windows 帳號:$u"
$sp = Read-Host -AsSecureString '  輸入 Windows 登入密碼(不是 PIN,打的時候不會顯示,打完按 Enter)'
$c = New-Object System.Management.Automation.PSCredential($u, $sp)
$act = New-ScheduledTaskAction -Execute '{sys.executable}' -Argument 'yuanta_bridge.py run' -WorkingDirectory '{APP}'
$trg1 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 08:40
# 2026-10-28 10/28 12:08 Windows Update 盤中重開機,開機後程式沒有自己回來(只有 08:40 啟動)→ 加「開機後 1 分鐘」和「每 5 分鐘檢查一次」
#   (同時只會跑一個:-MultipleInstances IgnoreNew,程式在跑時這兩個觸發什麼都不做;程式本身會看時間,非交易時段只等著)
$trg2 = New-ScheduledTaskTrigger -AtStartup; $trg2.Delay = 'PT1M'
$trg3 = New-ScheduledTaskTrigger -Once -At 08:00 -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 3650)
$trg = @($trg1, $trg2, $trg3)
$set = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5)
Register-ScheduledTask -TaskName '{TASK}' -Action $act -Trigger $trg -Settings $set -User $c.UserName -Password $c.GetNetworkCredential().Password -RunLevel Limited -Force | Out-Null
Write-Host '工作排程建立完成'
"""
    with tempfile.NamedTemporaryFile("w", suffix=".ps1", delete=False, encoding="utf-8-sig") as t:
        t.write(ps)
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", t.name])
    os.unlink(t.name)
    if r.returncode != 0:
        sys.exit("!! 工作排程沒有建成功。最常見:密碼打成 PIN 碼,或打錯。再跑一次 python win_install.py(設定檔會沿用)")

    step(4, "先啟動一次,確認它跑得起來")
    subprocess.run(["schtasks", "/Run", "/TN", TASK], capture_output=True)
    time.sleep(20)
    logs = sorted((APP / "log").glob("bridge_*.log"))
    if logs:
        print(f"  紀錄檔:{logs[-1]}")
        print("  " + "\n  ".join(logs[-1].read_text(encoding="utf-8").splitlines()[-5:]))
        print("\n完成!週一到週五 08:40 會自動啟動(那時還沒開機就開機後馬上啟動),08:45~13:36 收元大行情。")
        print(f"元大 1 分 K 每天存在 {APP / 'bars_yuanta'}")
    else:
        print("!! 20 秒還沒看到紀錄檔。打開「工作排程器」找 YuantaBridge,看「上次執行結果」,截圖給 Claude")


if __name__ == "__main__":
    main()
