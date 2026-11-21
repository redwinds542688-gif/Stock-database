"""自動當沖夥伴版 · 夥伴服務(2026-11-17 使用者「1 選 a,2 開始 先畫流程」→ 流程圖 →「改」)

主導$ 用手機「夥伴加入」新增夥伴 → 帳號密碼在手機上用這台電腦的「鎖」(RSA 公開鑰)加密 → 雲端只轉送(解不開)
→ 這支程式每 30 秒到雲端拿待辦 → 用這台電腦的私鑰解開 → 建 C:\\Yuanta\\fam\\<名字>(和 win_family.py 同一種資料夾)、開工作排程、啟動夥伴的交易程式
→ 看夥伴的紀錄檔:登入元大成功 → 回報雲端「自動當沖中」(雲端自動發他的看盤金鑰);被元大拒絕 → 回報原因(手機上看得到)。
移除夥伴:停掉並刪掉他的工作排程(資料夾留著)。

不改主導$ 自己的交易程式(C:\\Yuanta\\bridge 的 yuanta_bridge.py 照常跑),只是另外一支小程式。
夥伴的元大憑證:主導$ 先用元大憑證 e 管家裝進這台電腦(Windows 憑證存放區),這裡不碰憑證。

用法(放在 C:\\Yuanta\\bridge,讀同資料夾 bridge.env 的 WORKER_URL / PUSH_KEY):
  python fam_agent.py install    第一次:產生這台電腦的鎖、登記到雲端、建工作排程「YuantaFamAgent」(登入 Windows 就自動跑)、馬上啟動
  python fam_agent.py mgrkey     發「夥伴加入」的管理鑰匙:存成 QR Code 圖(只有圖,不印鑰匙)並打開 → 手機「夥伴加入」按掃描
  python fam_agent.py run        (工作排程跑這個)一直跑,每 30 秒看一次待辦
  python fam_agent.py status     看這台電腦的夥伴服務狀態
"""
import base64, hashlib, json, os, re, secrets, subprocess, sys, tempfile, time, urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
KEYF = HERE / "fam_agent_key.json"      # 這台電腦的私鑰(只有這個 Windows 帳號讀得到)
LOGF = HERE / "fam_agent.log"
FAM = Path(r"C:\Yuanta\fam")
TASK = "YuantaFamAgent"
TW = timezone(timedelta(hours=8))


def now_tw(): return datetime.now(TW)


def log(*a):
    line = now_tw().strftime("%m-%d %H:%M:%S ") + " ".join(str(x) for x in a)
    try: print(line, flush=True)
    except Exception: pass
    try:
        with open(LOGF, "a", encoding="utf-8") as f: f.write(line + "\n")
    except Exception: pass


def read_env(f):
    out = {}
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s and not s.startswith("#") and "=" in s:
                k, v = s.split("=", 1); out[k.strip()] = re.split(r"\s+#", v, 1)[0].strip()
    return out


CFG = read_env(HERE / "bridge.env")


def api(q, body=None, timeout=20):
    url = CFG["WORKER_URL"].rstrip("/") + "/?" + q
    req = urllib.request.Request(url, data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None, method="POST" if body is not None else "GET",
                                 headers={"Content-Type": "application/json", "X-Push-Key": CFG.get("PUSH_KEY", ""), "User-Agent": "fam-agent/1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r: return json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try: return json.loads(e.read().decode("utf-8") or "{}")
        except Exception: return {"error": f"HTTP {e.code}"}


def lock_down(f):
    if os.name == "nt": subprocess.run(["icacls", str(f), "/inheritance:r", "/grant:r", f"{os.environ.get('USERNAME', '')}:F"], capture_output=True)


# ── RSA(純 Python,不用另外裝套件):2048 位元金鑰 + RSA-OAEP(SHA-256)解密,和手機瀏覽器 WebCrypto「RSA-OAEP / SHA-256」對得上 ──
def _is_prime(n, rounds=48):
    if n < 2: return False
    for p in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if n % p == 0: return n == p
    d, s = n - 1, 0
    while d % 2 == 0: d //= 2; s += 1
    for _ in range(rounds):
        a = secrets.randbelow(n - 3) + 2; x = pow(a, d, n)
        if x in (1, n - 1): continue
        for _ in range(s - 1):
            x = pow(x, 2, n)
            if x == n - 1: break
        else: return False
    return True


def _prime(bits):
    while True:
        p = secrets.randbits(bits) | (1 << (bits - 1)) | (1 << (bits - 2)) | 1
        if _is_prime(p): return p


def b64u(n): b = n.to_bytes((n.bit_length() + 7) // 8, "big"); return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def new_key():
    e = 65537
    while True:
        p, q = _prime(1024), _prime(1024)
        if p == q: continue
        phi = (p - 1) * (q - 1)
        try: d = pow(e, -1, phi)
        except ValueError: continue
        n = p * q
        if n.bit_length() == 2048: return {"n": n, "e": e, "d": d, "p": p, "q": q}


def load_key():
    if not KEYF.exists(): return None
    k = json.loads(KEYF.read_text(encoding="utf-8"))
    return {x: int(k[x]) for x in ("n", "e", "d", "p", "q")}


def mgf1(seed, ln):
    out, c = b"", 0
    while len(out) < ln: out += hashlib.sha256(seed + c.to_bytes(4, "big")).digest(); c += 1
    return out[:ln]


def oaep_decrypt(key, b64):
    n, d, p, q = key["n"], key["d"], key["p"], key["q"]
    k = (n.bit_length() + 7) // 8
    c = int.from_bytes(base64.b64decode(b64), "big")
    if c >= n: raise ValueError("密文不對")
    m1, m2 = pow(c, d % (p - 1), p), pow(c, d % (q - 1), q)   # CRT 加速
    h = (pow(q, -1, p) * (m1 - m2)) % p
    em = (m2 + h * q).to_bytes(k, "big")
    hl = 32
    if em[0] != 0: raise ValueError("解密失敗(不是用這台電腦的鎖加密的)")
    ms, mdb = em[1:1 + hl], em[1 + hl:]
    seed = bytes(a ^ b for a, b in zip(ms, mgf1(mdb, hl)))
    db = bytes(a ^ b for a, b in zip(mdb, mgf1(seed, k - hl - 1)))
    if db[:hl] != hashlib.sha256(b"").digest(): raise ValueError("解密失敗(資料被改過)")
    i = db.index(b"\x01", hl)
    return db[i + 1:]


def ensure_key():
    k = load_key()
    if not k:
        log("第一次:產生這台電腦的鎖(2048 位元,約 10~60 秒)…")
        k = new_key()
        KEYF.write_text(json.dumps({x: str(v) for x, v in k.items()}), encoding="utf-8"); lock_down(KEYF)
        log("鎖已存好:", KEYF)
    r = api("fam=pub", {"jwk": {"kty": "RSA", "n": b64u(k["n"]), "e": b64u(k["e"])}})
    if not r.get("ok"): log("!! 登記公開鎖到雲端失敗:", r.get("error"))
    return k


# ── 建夥伴 / 移除夥伴(資料夾、bridge.env 和 win_family.py 同一套)──
def task_create(name, d):
    t = f"YuantaBridge-{name}"
    ps = f"""
$ErrorActionPreference = 'Stop'
$act = New-ScheduledTaskAction -Execute $env:FA_PY -Argument 'yuanta_bridge.py run' -WorkingDirectory $env:FA_DIR
$trg1 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 08:41
$trg2 = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\\$env:USERNAME"; $trg2.Delay = 'PT2M'
$trg3 = New-ScheduledTaskTrigger -Once -At 08:01 -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 3650)
$set = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5)
$pr = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $env:FA_TASK -Action $act -Trigger @($trg1, $trg2, $trg3) -Settings $set -Principal $pr -Force | Out-Null
"""
    # 2026-11-21 漏洞檢查 1:名字、路徑用環境變數交給 PowerShell,不直接拼進指令(名字裡有引號也不會變成指令)
    env = dict(os.environ, FA_PY=str(sys.executable), FA_DIR=str(d), FA_TASK=t)
    with tempfile.NamedTemporaryFile("w", suffix=".ps1", delete=False, encoding="utf-8-sig") as f: f.write(ps)
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", f.name], capture_output=True, text=True, env=env); os.unlink(f.name)
    if r.returncode != 0: raise RuntimeError("建工作排程失敗:" + (r.stderr or r.stdout)[-200:])
    subprocess.run(["schtasks", "/Run", "/TN", t], capture_output=True)


def name_ok(n):
    """漏洞檢查 1(2026-11-21):名字會變成資料夾 C:\\Yuanta\\fam\\<名字> 和工作排程名;只收中英文字、數字、_ -(和雲端、夥伴加入同一套),擋 Windows 保留名"""
    return isinstance(n, str) and bool(re.fullmatch(r"[\w-]{1,12}", n)) and not re.fullmatch(r"(?i)(con|prn|aux|nul|com\d|lpt\d)", n)


def add_partner(name, enc, key):
    import win_family as wf
    if not name_ok(name): raise ValueError("名字不能用(只能中英文字、數字、_ -,也不能是 CON 這類電腦保留名)")
    info = json.loads(oaep_decrypt(key, enc).decode("utf-8"))
    acct = re.sub(r"[\s-]", "", str(info.get("acct", "")))
    acct = "S" + (acct[1:] if acct[:1] in ("S", "s") and len(acct) == 12 else acct)
    pwd = str(info.get("pass", ""))
    if not re.fullmatch(r"S[0-9A-Za-z]{11}", acct) or not pwd: raise ValueError("證券帳號或密碼格式不對")
    d = FAM / name
    if d.resolve().parent != FAM.resolve(): raise ValueError("名字不能用(資料夾位置不對)")
    t = f"YuantaBridge-{name}"   # 漏洞檢查 2:重送前先停掉舊的交易程式(不然新的開不起來,還會讀到舊的失敗紀錄)
    subprocess.run(["schtasks", "/End", "/TN", t], capture_output=True)
    wf.copy_code(d)
    money = {k: dft for k, _, dft in wf.MONEY}   # 額度的底值;實際用雲端「夥伴加入」設的(夥伴交易程式每分鐘讀 ?acfg=get)
    wf.write_env(d, name, [f"YT_ACCOUNT={acct}", f"YT_PASS={pwd}"], money)
    pwd = info = None
    task_create(name, d)
    log(f"【{name}】資料夾、工作排程建好,夥伴交易程式啟動中(帳號 …{acct[-4:]})")


def del_partner(name):
    t = f"YuantaBridge-{name}"
    subprocess.run(["schtasks", "/End", "/TN", t], capture_output=True)
    subprocess.run(["schtasks", "/Delete", "/TN", t, "/F"], capture_output=True)
    log(f"【{name}】已停掉並刪除工作排程(資料夾 {FAM / name} 留著)")


def login_state(name, since):
    """看夥伴的紀錄檔,回 (結果, 訊息, chk):結果 True = 登入成功 / False = 失敗 / None = 還沒結果;
       chk = {acct, cert, api}:y 通過、n 有問題、w 還不知道(2026-11-18 使用者「憑證 與 api 程式自己偵測 … 沒問題 自動打勾 有問題 打叉」)
       元大的代碼:登入 0102 密碼凍結 / 未啟用 → 帳號密碼 ✗;0112 無此權限 → API 下單 ✗;查詢 [9 加簽失敗 / 憑證異常] → 憑證 ✗;[4 帳號錯] [11 帳號資訊異常] → 帳號密碼 ✗;[5 權限不足] → API ✗"""
    f = FAM / name / "log" / f"bridge_{now_tw().strftime('%Y-%m-%d')}.log"
    if not f.exists(): return None, "", {}
    try: lines = f.read_text(encoding="utf-8", errors="ignore")[-40000:].splitlines()
    except Exception: return None, "", {}
    # 漏洞檢查 2(2026-11-21):只看這次送出(since)以後的紀錄,早上那次失敗 / 成功不算
    t0 = datetime.fromtimestamp(since - 5, TW).strftime("%m-%d %H:%M:%S") if since else ""
    lines = [x for x in lines if not t0 or x[:14] >= t0]
    ok = None; net = ""
    for line in lines:
        m = re.search(r"查詢回應 \[(\d+) ([^\]]*)\]", line)
        if m and m.group(1) in ("4", "5", "9", "11"):
            d = m.group(1); k = {"9": "cert", "5": "api"}.get(d, "acct")
            chk = {"acct": "y", "cert": "y", "api": "y"} if k != "acct" else {"acct": "n", "cert": "w", "api": "w"}
            chk[k] = "n"; return False, f"元大回:{d} {m.group(2)}", chk
        m = re.search(r"登入 (\S+) (.*) 帳號數 (\d+)", line)
        if m:
            if m.group(1) in ("0001", "00001") or int(m.group(3)) > 0: ok = True; continue
            if m.group(1) == "0112" or "權限" in m.group(2): return False, f"元大回:{m.group(1)} {m.group(2)}", {"acct": "y", "cert": "w", "api": "n"}
            return False, f"元大回:{m.group(1)} {m.group(2)}", {"acct": "n", "cert": "w", "api": "w"}
        if "30 秒還沒連上元大交易主機" in line or "登入逾時" in line:
            net = "連不上元大(網路 / 固定 IP?)"   # 2026-11-21 漏洞檢查 2:一時連不上不算失敗,交易程式會自己重連;一直連不上才算
    if ok: return True, "元大登入成功", {"acct": "y", "cert": "w", "api": "y"}
    return None, net, {}


def run():
    key = ensure_key()
    log("夥伴服務開始(每 30 秒看一次待辦)")
    watch = {}   # 名字 → 開始等登入的時間
    while True:
        try:
            t = now_tw(); mkt = t.weekday() < 5 and "09:00" <= t.strftime("%H:%M") < "13:30"   # 2026-11-17 盤中不新增 / 不移除夥伴(13:30 後才處理)
            r = api("fam=jobs")
            for wv in (r.get("watch", []) if isinstance(r, dict) else []):   # 漏洞檢查 3:重開後接著看還在「設定中」的夥伴
                if wv.get("name") and wv["name"] not in watch: t0 = (wv.get("at") or 0) / 1000 or time.time(); watch[wv["name"]] = {"t0": t0, "w0": t0}
            for j in (r.get("jobs", []) if isinstance(r, dict) else []):
                if mkt and j.get("op") != "add": continue   # 盤中只先做「新增」(使用者「暫時還是要允許盤中加入新夥伴 畢竟還是要測試」);移除等 13:30 後
                n = j.get("name", "")
                try:
                    if j.get("op") == "add":
                        api("fam=status", {"name": n, "st": "pc", "msg": "電腦收到,建立中"})
                        add_partner(n, j.get("enc", ""), key)
                        t0 = time.time(); api("fam=status", {"name": n, "st": "pc", "msg": "電腦登入元大中", "chk": {"acct": "w", "cert": "w", "api": "w"}, "since": int(t0 * 1000)}); watch[n] = {"t0": t0, "w0": t0}
                    elif j.get("op") == "del":
                        watch.pop(n, None); del_partner(n); api("fam=status", {"name": n, "st": "gone"})   # 漏洞檢查 3:移除了就不再看他的登入
                except (Exception, SystemExit) as e:   # 漏洞檢查 4:win_family 出錯是用 sys.exit,也要接住,不然整個服務會停
                    if j.get("op") == "add": watch.pop(n, None)
                    log(f"!! 【{n}】處理失敗:", e); api("fam=status", {"name": n, "st": "fail", "msg": str(e)[:150]})
                api("fam=done", {"name": n})
            # 2026-11-21 漏洞檢查 2:夥伴交易程式只在平日開盤前(08:41)起來登入;晚上 / 假日送的,等到那時候才開始算 30 分鐘
            inwin = t.weekday() < 5 and "08:25" <= t.strftime("%H:%M") < "23:00"
            for n, w in list(watch.items()):
                ok, msg, chk = login_state(n, w["t0"])
                if ok is None and not inwin:
                    if not w.get("off"): w["off"] = 1; api("fam=status", {"name": n, "st": "pc", "msg": "等開盤前(平日 08:41)登入元大", "chk": {"acct": "w", "cert": "w", "api": "w"}, "since": int(w["t0"] * 1000)})
                    w["w0"] = time.time(); continue
                w["off"] = 0
                if ok is False:
                    api("fam=status", {"name": n, "st": "fail", "msg": msg, "chk": chk}); log(f"!! 【{n}】", msg); watch.pop(n)
                elif ok is True and not w.get("ok_at"):
                    w["ok_at"] = time.time(); api("fam=status", {"name": n, "st": "pc", "msg": "元大登入成功,檢查憑證中", "chk": chk}); log(f"【{n}】元大登入成功,等 3 分鐘看憑證")
                elif ok is True and time.time() - w["ok_at"] > 180:   # 登入後 3 分鐘查成交都沒有「加簽失敗 / 憑證異常」→ 憑證也打勾
                    api("fam=status", {"name": n, "st": "run", "msg": "元大已登入", "chk": {"acct": "y", "cert": "y", "api": "y"}}); log(f"【{n}】三項檢查都通過"); watch.pop(n)
                elif ok is None and time.time() - w["w0"] > 1800:
                    api("fam=status", {"name": n, "st": "fail", "msg": ((msg + ",") if msg else "") + f"30 分鐘還沒登入元大(看 {FAM / n / 'log'})", "chk": {}}); watch.pop(n)
        except (Exception, SystemExit) as e:
            log("!! 夥伴服務這一輪出錯:", e)
        time.sleep(30)


def install():
    if not CFG.get("WORKER_URL") or not CFG.get("PUSH_KEY"): sys.exit(f"找不到 {HERE / 'bridge.env'} 的 WORKER_URL / PUSH_KEY(這支要放在 C:\\Yuanta\\bridge)")
    ensure_key()
    py = Path(sys.executable); pyw = py.with_name("pythonw.exe") if py.with_name("pythonw.exe").exists() else py
    ps = f"""
$ErrorActionPreference = 'Stop'
$act = New-ScheduledTaskAction -Execute $env:FA_PY -Argument ('"' + $env:FA_SCRIPT + '" run') -WorkingDirectory $env:FA_DIR
$trg1 = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\\$env:USERNAME"; $trg1.Delay = 'PT1M'
$trg2 = New-ScheduledTaskTrigger -Once -At 00:00 -RepetitionInterval (New-TimeSpan -Minutes 10) -RepetitionDuration (New-TimeSpan -Days 3650)
$set = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$pr = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $env:FA_TASK -Action $act -Trigger @($trg1, $trg2) -Settings $set -Principal $pr -Force | Out-Null
"""
    env = dict(os.environ, FA_PY=str(pyw), FA_SCRIPT=str(HERE / "fam_agent.py"), FA_DIR=str(HERE), FA_TASK=TASK)
    with tempfile.NamedTemporaryFile("w", suffix=".ps1", delete=False, encoding="utf-8-sig") as f: f.write(ps)
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", f.name], capture_output=True, text=True, env=env); os.unlink(f.name)
    if r.returncode != 0: sys.exit("!! 建工作排程失敗:" + (r.stderr or r.stdout)[-300:])
    subprocess.run(["schtasks", "/Run", "/TN", TASK], capture_output=True)
    print(f"\n完成:夥伴服務已安裝並啟動(工作排程 {TASK},登入 Windows 自動跑)。紀錄在 {LOGF}")
    print("下一步:python fam_agent.py mgrkey  → 用手機「夥伴加入」掃描管理鑰匙")


def mgrkey():
    r = api("keys=add", {"name": "夥伴加入管理", "role": "mgr"})
    k = r.get("k") if isinstance(r, dict) else None
    if not k or r.get("role") != "mgr": sys.exit("!! 發管理鑰匙失敗:" + str(r.get("error") if isinstance(r, dict) else r))
    try:
        import qrcode
    except ImportError:
        print("第一次產生 QR Code:安裝 qrcode / pillow 套件 …")
        subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "qrcode", "pillow"], check=False)
        import qrcode
    q = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=14, border=4); q.add_data(k); q.make(fit=True)
    folder = HERE / "keys"; folder.mkdir(exist_ok=True)
    path = folder / f"夥伴加入管理鑰匙_{now_tw().strftime('%m%d%H%M')}.png"
    q.make_image(fill_color="black", back_color="white").save(path); k = None
    print(f"管理鑰匙 QR Code(只有圖):{path}\n用手機「夥伴加入」入口按「掃描」掃這張 → 綁在那支手機(別支手機不能用)。掃完可以刪掉這張圖。")
    try: os.startfile(str(path))
    except Exception: pass


def status():
    k = load_key(); print("這台電腦的鎖:", "有" if k else "還沒有(先 install)")
    r = subprocess.run(["schtasks", "/Query", "/TN", TASK], capture_output=True, text=True)
    print("工作排程:", "有" if r.returncode == 0 else "沒有(先 install)")
    if LOGF.exists(): print("最後紀錄:\n" + "\n".join(LOGF.read_text(encoding="utf-8").splitlines()[-8:]))


if __name__ == "__main__":
    a = sys.argv[1:]
    cmd = a[0] if a else ""
    if cmd == "install": install()
    elif cmd == "mgrkey": mgrkey()
    elif cmd == "run": run()
    elif cmd == "status": status()
    else: sys.exit(__doc__)
