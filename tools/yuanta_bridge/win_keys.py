"""手機鑰匙管理(2026-10-29 使用者「如果 程式給別人用 但我要控制判123在手上」→「先做1.2.3」第 3 項)

每支手機一把鑰匙:owner = 自己的手機(可寫通知、可下單);viewer = 給別人的看盤版(只能看)。
鑰匙存在 Worker 的 KV;關掉(off)那支手機馬上就不能用,重新開(on)就回來。

用法(桌機 C:\\Yuanta\\bridge,讀 bridge.env 的 WORKER_URL / PUSH_KEY):
  python win_keys.py list                  # 列出全部鑰匙
  python win_keys.py add 我的手機 owner    # 建一把 owner 鑰匙(自己的手機;名字自己取;兩支手機各建一把)
  python win_keys.py add 小王 viewer       # 建一把 viewer 鑰匙(給別人)
  python win_keys.py add 小王 viewer 30    # 30 天後自動到期
  方案(2026-10-29 使用者定價):
  (2026-11-01 試用方案取消:免鑰匙就能用 30 天(前 7 天有交易通知),不用再發試用鑰匙)
  python win_keys.py add 小王 m1           # 一個月  600 元(30 天)
  python win_keys.py add 小王 m3           # 三個月 1700 元(90 天)
  python win_keys.py add 小王 y1           # 一年   6000 元(365 天)
  python win_keys.py unbind <鑰匙>         # 2026-10-29 一人一機:鑰匙綁在第一支用它的手機;換手機先解綁
  python win_keys.py qr <鑰匙> [名字]       # 2026-11-01 產生 QR Code 圖(下面印鑰匙),手機 公式 → 鑰匙 → 掃描 就能存;add 建好鑰匙時也會自動產生
  python win_keys.py send <鑰匙>           # 2026-10-29 取件碼:印 6 位數,手機 公式 → 鑰匙 輸入 6 位數就拿到(10 分鐘內、用一次),不用抄 24 碼
  python win_keys.py off <鑰匙>            # 停用
  python win_keys.py on <鑰匙>             # 重新啟用
  python win_keys.py del <鑰匙>            # 刪掉
  python win_keys.py delend d2a2          # 2026-11-02 用「結尾幾碼」刪(至少 4 碼,剛好一把符合才刪;不用打整串)
  python win_keys.py delstart 39d192      # 用「開頭幾碼」刪(QR Code 圖檔名後面那 6 碼)
建好的鑰匙給那支手機:公式 → 鑰匙 → 貼上 → 存。
注意:建了第一把 owner 鑰匙以後,沒帶 owner 鑰匙的手機就不能寫通知、不能下單(看還是看得到),所以自己的手機要先輸入。
"""
import json, sys, urllib.request
import yuanta_bridge as yb

CFG = yb.CFG


def call(q, body=None):
    url = CFG["WORKER_URL"].rstrip("/") + "/?keys=" + q
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, method="POST" if body is not None else "GET",
                                 headers={"Content-Type": "application/json", "X-Push-Key": CFG.get("PUSH_KEY", "")})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=20).read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}"}


def make_qr(key, name="", note=""):
    """2026-11-01 使用者「金鑰產生器必須要可以產生一個QR Code的圖形底下才打著金鑰的密碼」:QR Code(內容 = 鑰匙)+ 下面印鑰匙,存 keys 資料夾(名字_鑰匙前 6 碼.png) 並打開。
       第一次用會自動安裝 qrcode / pillow 兩個套件"""
    import subprocess, os
    try:
        import qrcode
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("第一次產生 QR Code:安裝 qrcode / pillow 套件 …")
        subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "qrcode", "pillow"], check=False)
        import qrcode
        from PIL import Image, ImageDraw, ImageFont
    q = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=12, border=4)
    q.add_data(key); q.make(fit=True)
    img = q.make_image(fill_color="black", back_color="white").convert("RGB")
    W = img.size[0]
    def font(names, size):
        for n in names:
            for d in ("C:/Windows/Fonts/", "/usr/share/fonts/truetype/dejavu/", ""):
                try: return ImageFont.truetype(d + n, size)
                except Exception: pass
        return ImageFont.load_default()
    fk = font(["consola.ttf", "cour.ttf", "DejaVuSansMono.ttf", "arial.ttf"], 30)
    fn = font(["msjh.ttc", "msjhbd.ttc", "mingliu.ttc", "arial.ttf"], 24)
    lines = [(key, fk, "black")] + ([(f"{name}  {note}".strip(), fn, "#555555")] if (name or note) else [])
    H = img.size[1] + 20 + sum(50 for _ in lines)
    out = Image.new("RGB", (W, H), "white"); out.paste(img, (0, 0)); d = ImageDraw.Draw(out)
    y = img.size[1] - 10
    for t, f, c in lines:
        tw = d.textlength(t, font=f) if hasattr(d, "textlength") else len(t) * 15
        if tw > W - 20:   # 鑰匙太長就縮字
            f = font(["consola.ttf", "cour.ttf", "DejaVuSansMono.ttf", "arial.ttf"], max(14, int(30 * (W - 20) / tw))); tw = d.textlength(t, font=f)
        d.text(((W - tw) / 2, y), t, font=f, fill=c); y += 50
    folder = yb.HERE / "keys"; folder.mkdir(exist_ok=True)
    safe = "".join(ch for ch in (name or "key") if ch.isalnum() or ch in "-_") or "key"
    path = folder / f"{safe}_{key[:6]}.png"; out.save(path)
    print(f"QR Code 圖:{path}")
    try: os.startfile(str(path))   # Windows:直接打開圖
    except Exception: pass
    return path


def main(a):
    if not CFG.get("PUSH_KEY"):
        print("bridge.env 沒有 PUSH_KEY(先跑 win_pushkey.py)"); return
    if not a or a[0] == "list":
        r = call("list")
        if isinstance(r, dict): print(r); return
        if not r: print("(還沒有鑰匙)"); return
        import datetime
        for k in r:
            seen = datetime.datetime.fromtimestamp(k['seen'] / 1000, yb.TW).strftime("%m-%d %H:%M") if k.get("seen") else "沒用過"
            print(f"{k['k']}  {k.get('plan') or k.get('role','?'):6}  {'啟用' if k.get('on') else '停用'}  建 {k.get('made','')}  到期 {k.get('exp') or '無':10}  手機 {(k.get('dev') or '未綁')[:8]:8}  最後 {seen:11}  {k.get('name','')}")
        return
    if a[0] == "add":
        name = a[1] if len(a) > 1 else ""; kind = a[2] if len(a) > 2 else "viewer"; days = int(a[3]) if len(a) > 3 else 0
        PLANS = {"m1": "一個月 600", "m3": "三個月 1700", "y1": "一年 6000"}   # 2026-11-01 trial 拿掉
        body = {"name": name, "days": days}
        if kind in PLANS: body["plan"] = kind
        else: body["role"] = kind
        r = call("add", body); print(r)
        if r.get("ok"):
            print(f"\n鑰匙({PLANS.get(kind, kind)}{',到期 ' + r['exp'] if r.get('exp') else ''}):{r['k']}\n給那支手機:公式 → 鑰匙 → 掃描 QR Code(或貼上這串)→ 存")
            try: make_qr(r["k"], name, PLANS.get(kind, kind) + (f" 到期 {r['exp']}" if r.get("exp") else ""))
            except Exception as e: print("產生 QR Code 失敗:", e)
        return
    if a[0] in ("delend", "delstart") and len(a) > 1:
        print(call(("delsfx&sfx=" if a[0] == "delend" else "delpfx&pfx=") + a[1].strip().lower(), {})); return
    if a[0] == "qr" and len(a) > 1:
        make_qr(a[1], a[2] if len(a) > 2 else ""); return
    if a[0] == "send" and len(a) > 1:
        r = call("stash", {"k": a[1]}); print(r)
        if r.get("ok"): print(f"\n取件碼:{r['code']}(10 分鐘內有效、用一次)→ 手機 公式 → 鑰匙 → 輸入這 6 位數 → 存")
        return
    if a[0] in ("off", "on", "del", "unbind") and len(a) > 1:
        print(call(f"{a[0]}&k2={a[1]}", {})); return
    print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
