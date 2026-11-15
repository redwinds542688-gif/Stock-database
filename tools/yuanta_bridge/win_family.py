"""家人帳號安裝(2026-11-15 使用者「大概10個人 每個人的額度不一樣 包含資金不同 多空上限也不同 … 九個判別式都一起啟動
每個人都可以使用 我可以看到所有人的輸贏 但他們只能看到自己的輸贏」)

同一台主機:主帳號照舊在 C:\\Yuanta\\bridge(跑公式引擎、收行情、推雲端);每個家人一個資料夾 C:\\Yuanta\\fam\\<名字>,
跑一份「只下單」的中繼(bridge.env 有 ACCT_NAME):讀同一份訊號(主帳號資料夾的本機通知 + 雲端),照自己的設定用自己的元大帳號下單。

在命令提示字元執行(要先裝好主帳號 C:\\Yuanta\\bridge):
  python win_family.py add <名字>     新增 / 重設一個家人(問元大帳號、電子密碼、資金設定;建工作排程 YuantaBridge-<名字>)
  python win_family.py list            列出所有家人和設定
  python win_family.py set <名字>      只改資金設定(不動帳號密碼)
  python win_family.py off <名字>      停用(刪工作排程,資料夾留著)
帳號、密碼只寫進那個家人資料夾的 bridge.env(只有這個 Windows 帳號讀得到),不上傳、不經過雲端。
"""
import getpass, os, re, shutil, subprocess, sys, tempfile
from pathlib import Path

BASE = Path(r"C:\Yuanta")
MAIN = BASE / "bridge"
FAM = BASE / "fam"
PY = ["yuanta_bridge.py", "bars.py", "trader.py", "quotes.py", "win_pnl.py", "elig.py", "engine.py", "win_keys.py", "win_update.py",
      "hist_kline.py", "win_install.py", "win_family.py", "VERSION", "FILES"]
# 每個人可以不一樣的設定(名稱、說明、預設);鐵律的上限程式會再夾一次(多空上限 ≤ 25 萬、當日總額度 ≤ 500 萬)
MONEY = [("TRADE_MAX_AMOUNT", "當日總額度(買 + 賣成交金額上限,元;最多 5000000)", "5000000"),
         ("TRADE_CAP", "多空上限(每筆最多多少錢,元;最多 250000)", "250000"),
         ("TRADE_MAX_LOSS", "已平倉虧到多少停止新進場(元)", "20000"),
         ("TRADE_KILL_LOSS", "帳面虧到多少全部平倉(元)", "50000")]
# 從主帳號沿用的設定(判別式、規則一樣);帳號、資金、本機路徑、主帳號專用的不沿用
NOT_COPY = re.compile(r"^(YT_ACCOUNT|YT_PASS|YT_PFX|YT_PFX_PASS|ACCT_NAME|ENGINE|ENGINE_DIR|QUOTE_ENABLE|QUOTE_PORT|HIST_AFTER|LOG_DIR|BARS_DIR|"
                      r"TRADE_STATE_FILE|TRADE_BR_FILE|CF_METRICS|TRADE_MAX_AMOUNT|TRADE_CAP|TRADE_MAX_LOSS|TRADE_KILL_LOSS|QUOTA_TOTAL|AUTO_UPDATE|UPDATE_REF)$")


def read_env(f):
    out = {}
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s and not s.startswith("#") and "=" in s:
                k, v = s.split("=", 1); out[k.strip()] = re.split(r"\s+#", v, 1)[0].strip()
    return out


def name_ok(n):
    n = re.sub(r"[\s|\\/:*?\"<>]", "", n or "")[:12]
    if not n: sys.exit("名字不能空白(例如 媽、哥、小明)")
    return n


def ask_money(cur):
    print("\n  資金設定(直接按 Enter = 用括號裡的值)")
    out = {}
    for k, label, dft in MONEY:
        v0 = cur.get(k, dft)
        while True:
            v = input(f"  {label} [{v0}]> ").strip().replace(",", "") or v0
            if re.fullmatch(r"\d+", v): out[k] = v; break
            print("  要整數")
    return out


def write_env(d, name, acct_lines, money):
    main = read_env(MAIN / "bridge.env")
    if not main: sys.exit(f"找不到主帳號設定 {MAIN / 'bridge.env'}(先裝好主帳號)")
    lines = [f"# 家人帳號【{name}】中繼設定(win_family.py 產生;只放這台電腦,不要上傳、不要傳給別人)",
             f"ACCT_NAME={name}", *acct_lines,
             "ENGINE=0", "QUOTE_ENABLE=0", "HIST_AFTER=0", "TRADE_ENABLE=1", "TRADE_ALLOW_PROD=1",
             "AUTO_UPDATE=0",   # 夥伴版:不跟 main 自動更新(要更新就寫 UPDATE_REF=<partner 的 commit> 並改 AUTO_UPDATE=1)
             f"ENGINE_DIR={MAIN / 'engine_data'}",                       # 讀主帳號的本機通知(訊號 1 秒內到)
             f"TRADE_BR_FILE={MAIN / 'engine_data' / 'breadth.json'}",   # 大盤寬度(總停損 / 判二檔數)用主帳號那份
             f"LOG_DIR={d / 'log'}", f"BARS_DIR={d / 'bars_yuanta'}",
             *[f"{k}={v}" for k, v in money.items()],
             "# ── 以下沿用主帳號的設定 ──",
             *[f"{k}={v}" for k, v in main.items() if not NOT_COPY.match(k)], ""]
    f = d / "bridge.env"; f.write_text("\n".join(lines), encoding="utf-8")
    user = os.environ.get("USERNAME", "")
    if os.name == "nt": subprocess.run(["icacls", str(f), "/inheritance:r", "/grant:r", f"{user}:F"], capture_output=True)   # 只有自己讀得到
    return f


def copy_code(d):
    # 自動當沖夥伴版 2026-11-15:程式從「win_family.py 自己所在的資料夾」(partner 分支的程式)複製,不從 C:\Yuanta\bridge(那裡是 main 的程式,沒有夥伴邏輯)
    src = Path(__file__).resolve().parent
    if "FAMILY = bool(ACCT)" not in (src / "yuanta_bridge.py").read_text(encoding="utf-8", errors="ignore"):
        sys.exit(f"{src} 的 yuanta_bridge.py 不是夥伴版(沒有 ACCT_NAME 只下單模式),不能拿來建夥伴資料夾")
    d.mkdir(parents=True, exist_ok=True)
    for f in PY:
        if (src / f).exists() and src != d: shutil.copyfile(src / f, d / f)
    if (MAIN / "sdk").exists() and not (d / "sdk").exists(): shutil.copytree(MAIN / "sdk", d / "sdk")


def task(name, d):
    t = f"YuantaBridge-{name}"
    ps = f"""
$ErrorActionPreference = 'Stop'
$u = "$env:USERDOMAIN\\$env:USERNAME"
Write-Host "  Windows 帳號:$u"
$sp = Read-Host -AsSecureString '  輸入 Windows 登入密碼(不是 PIN,打的時候不會顯示,打完按 Enter)'
$c = New-Object System.Management.Automation.PSCredential($u, $sp)
$act = New-ScheduledTaskAction -Execute '{sys.executable}' -Argument 'yuanta_bridge.py run' -WorkingDirectory '{d}'
$trg1 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 08:41
$trg2 = New-ScheduledTaskTrigger -AtStartup; $trg2.Delay = 'PT2M'
$trg3 = New-ScheduledTaskTrigger -Once -At 08:01 -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 3650)
$set = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5)
Register-ScheduledTask -TaskName '{t}' -Action $act -Trigger @($trg1, $trg2, $trg3) -Settings $set -User $c.UserName -Password $c.GetNetworkCredential().Password -RunLevel Limited -Force | Out-Null
Write-Host '工作排程建立完成:{t}'
"""
    with tempfile.NamedTemporaryFile("w", suffix=".ps1", delete=False, encoding="utf-8-sig") as f: f.write(ps)
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", f.name]); os.unlink(f.name)
    if r.returncode != 0: sys.exit("!! 工作排程沒有建成功(最常見:密碼打成 PIN)。再跑一次同一個指令")
    subprocess.run(["schtasks", "/Run", "/TN", t], capture_output=True)


def add(name):
    d = FAM / name; cur = read_env(d / "bridge.env")
    print(f"=== 家人帳號【{name}】→ {d} ===")
    copy_code(d)
    acct = input("  證券帳號(分公司 4 碼 + 帳號 7 碼,例如 981R0123456)> ").strip().replace("-", "")
    acct = "S" + (acct[1:] if acct[:1] in ("S", "s") and len(acct) == 12 else acct)
    pwd = getpass.getpass("  電子密碼(輸入時不會顯示)> ")
    pfx = input("  電子憑證檔路徑(.pfx;沒有 / 用 Windows 已安裝的憑證直接按 Enter)> ").strip().strip('"')
    acc = [f"YT_ACCOUNT={acct}", f"YT_PASS={pwd}"]
    if pfx: acc += [f"YT_PFX={pfx}", f"YT_PFX_PASS={getpass.getpass('  憑證密碼(不會顯示)> ')}"]
    pwd = None
    f = write_env(d, name, acc, ask_money(cur)); acc = None
    print(f"  已寫入 {f}")
    if os.name == "nt": task(name, d)
    print(f"\n完成。【{name}】週一到週五 08:41 自動啟動;紀錄在 {d / 'log'}。手機會收到「【{name}】…」開頭的提醒。")


def set_money(name):
    d = FAM / name; f = d / "bridge.env"; cur = read_env(f)
    if not cur: sys.exit(f"沒有【{name}】(先 add)")
    acc = [f"{k}={cur[k]}" for k in ("YT_ACCOUNT", "YT_PASS", "YT_PFX", "YT_PFX_PASS") if cur.get(k)]
    write_env(d, name, acc, ask_money(cur)); acc = None
    print(f"  【{name}】已更新,下次啟動生效(盤中改的話要等收盤後重開)")


def lst():
    if not FAM.exists(): return print("還沒有家人帳號")
    for d in sorted(FAM.iterdir()):
        c = read_env(d / "bridge.env")
        if c: print(f"【{c.get('ACCT_NAME', d.name)}】帳號 …{c.get('YT_ACCOUNT', '')[-4:]}  " + "  ".join(f"{k}={c.get(k, '-')}" for k, _, _ in MONEY))


def main():
    a = sys.argv[1:]
    if not a or a[0] not in ("add", "set", "list", "off"): sys.exit(__doc__)
    if a[0] == "list": return lst()
    if len(a) < 2: sys.exit("要寫名字,例如:python win_family.py add 媽")
    n = name_ok(a[1])
    if a[0] == "add": add(n)
    elif a[0] == "set": set_money(n)
    elif a[0] == "off":
        subprocess.run(["schtasks", "/Delete", "/TN", f"YuantaBridge-{n}", "/F"]); print(f"【{n}】已停用(資料夾留著,要再用 add)")


if __name__ == "__main__":
    main()
