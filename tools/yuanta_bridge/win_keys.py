"""手機鑰匙管理(2026-10-29 使用者「如果 程式給別人用 但我要控制判123在手上」→「先做1.2.3」第 3 項)

每支手機一把鑰匙:owner = 自己的手機(可寫通知、可下單);viewer = 給別人的看盤版(只能看)。
鑰匙存在 Worker 的 KV;關掉(off)那支手機馬上就不能用,重新開(on)就回來。

用法(桌機 C:\\Yuanta\\bridge,讀 bridge.env 的 WORKER_URL / PUSH_KEY):
  python win_keys.py list                  # 列出全部鑰匙
  python win_keys.py add 我的手機 owner    # 建一把 owner 鑰匙(自己的手機;名字自己取;兩支手機各建一把)
  python win_keys.py add 小王 viewer       # 建一把 viewer 鑰匙(給別人)
  python win_keys.py add 小王 viewer 30    # 30 天後自動到期
  方案(2026-10-29 使用者定價):
  python win_keys.py add 小王 trial        # 試用一個月 免費:和看盤一樣有訊號 / 當沖 / 明細(都是系統模擬);30 天到期(日期看 Worker,手機改時間沒用)
  python win_keys.py add 小王 m1           # 一個月  600 元(30 天)
  python win_keys.py add 小王 m3           # 三個月 1700 元(90 天)
  python win_keys.py add 小王 y1           # 一年   6000 元(365 天)
  python win_keys.py unbind <鑰匙>         # 2026-10-29 一人一機:鑰匙綁在第一支用它的手機;換手機先解綁
  python win_keys.py send <鑰匙>           # 2026-10-29 取件碼:印 6 位數,手機 公式 → 鑰匙 輸入 6 位數就拿到(10 分鐘內、用一次),不用抄 24 碼
  python win_keys.py off <鑰匙>            # 停用
  python win_keys.py on <鑰匙>             # 重新啟用
  python win_keys.py del <鑰匙>            # 刪掉
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
        PLANS = {"trial": "試用一個月 免費", "m1": "一個月 600", "m3": "三個月 1700", "y1": "一年 6000"}
        body = {"name": name, "days": days}
        if kind in PLANS: body["plan"] = kind
        else: body["role"] = kind
        r = call("add", body); print(r)
        if r.get("ok"): print(f"\n鑰匙({PLANS.get(kind, kind)}{',到期 ' + r['exp'] if r.get('exp') else ''}):{r['k']}\n給那支手機:公式 → 鑰匙 → 貼上這串 → 存")
        return
    if a[0] == "send" and len(a) > 1:
        r = call("stash", {"k": a[1]}); print(r)
        if r.get("ok"): print(f"\n取件碼:{r['code']}(10 分鐘內有效、用一次)→ 手機 公式 → 鑰匙 → 輸入這 6 位數 → 存")
        return
    if a[0] in ("off", "on", "del", "unbind") and len(a) > 1:
        print(call(f"{a[0]}&k2={a[1]}", {})); return
    print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
