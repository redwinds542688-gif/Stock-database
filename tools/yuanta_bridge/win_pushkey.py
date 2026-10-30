"""元大推 K 線金鑰設定(Windows 桌機,2026-10-25 使用者「做」)

在 C:\\Yuanta\\bridge 執行:  python win_pushkey.py
做的事:
  1. (2026-10-28 拿掉下載:舊版會蓋掉 win_install.py 裝好的新版中繼程式)
  2. 產生一組隨機金鑰,寫進 bridge.env 的 PUSH_KEY(不顯示設定檔內容)
  3. 金鑰複製到剪貼簿 → 你到 GitHub Secrets 新增 YT_PUSH_KEY,按 Ctrl+V 貼上
  4. 重新啟動中繼程式(工作排程 YuantaBridge)
金鑰不會印在畫面上,也不要貼到對話或其他地方。已經設過的話會問你要沿用還是重產。
"""
import re, secrets, subprocess, sys
from pathlib import Path

APP = Path(r"C:\Yuanta\bridge")
ENV = APP / "bridge.env"
TASK = "YuantaBridge"

if not ENV.exists():
    sys.exit(f"找不到 {ENV}(要先跑過 win_install.py)")

print("\n=== 2. 金鑰 ===")
lines = ENV.read_text(encoding="utf-8").splitlines()
cur = ""
for l in lines:
    if l.startswith("PUSH_KEY="):
        cur = re.split(r"\s+#", l.split("=", 1)[1], 1)[0].strip()
if cur and input("  已經有金鑰。直接按 Enter = 沿用(再複製一次到剪貼簿);輸入 n = 重產一組 > ").strip().lower() != "n":
    key = cur
    print("  沿用原本的金鑰")
else:
    key = secrets.token_hex(24)
    out, done = [], False
    for l in lines:
        if l.startswith("PUSH_KEY="):
            out.append(f"PUSH_KEY={key}"); done = True
        else:
            out.append(l)
    if not done:
        out.append(f"PUSH_KEY={key}")
    ENV.write_text("\n".join(out) + "\n", encoding="utf-8")
    print("  已產生新金鑰,寫進 bridge.env")

subprocess.run("clip", input=key.encode("ascii"), check=True)
key = None
print("  ★ 金鑰已複製到剪貼簿(畫面不顯示)")

print("\n=== 3. 重新啟動中繼程式 ===")
subprocess.run(["schtasks", "/End", "/TN", TASK], capture_output=True)
subprocess.run(["schtasks", "/Run", "/TN", TASK], capture_output=True)
print("  已重新啟動")

print("""
=== 4. 接下來在瀏覽器做 ===
  打開 https://github.com/redwinds542688-gif/Stock-database/settings/secrets/actions
  找到 YT_PUSH_KEY → 按右邊鉛筆(Update)(還沒有的話按 New repository secret,Name 填 YT_PUSH_KEY)
  Value 欄位按 Ctrl+V 貼上(剪貼簿裡就是金鑰)
  按 Update secret / Add secret,然後告訴 Claude「填好了」。
  (剪貼簿裡的金鑰不要貼到別的地方)""")
