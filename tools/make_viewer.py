"""產生看盤版 app/viewer.html(2026-10-29 使用者:「如果 程式給別人用 但我要控制判123在手上」→「先做1.2.3」第 1 項)

從 app/app.html 做:
  1. 檔頭那一大段交接文件(<!-- … -->)換成兩行
  2. /*@@STRIP:名@@*/ … /*@@END:名@@*/ 之間的判一二三程式碼整段拿掉、換成 STUBS 裡的空殼(參數、進出場規則、轉折、背景監控判斷)
  3. <head> 加 <meta name="app-mode" content="viewer">(網頁裡 window.VIEWER 就是 true:今天的通知只從 Worker 讀、不能下單、要鑰匙)
  4. 每個 <script> 用 terser 把註解全部刪掉(不壓縮、不改名;程式裡的註解寫滿了規則說明,也不能給)、<style> 和 HTML 的註解用正則刪
輸出:app/viewer.html;--android 再複製一份到 android/app/src/viewer/assets/app.html(看盤版 APK 內建)
需要 node + terser(npm i terser;環境變數 TERSER 可指定路徑)。GitHub Actions build-viewer.yml 會在 app.html 推上 main 時自動做。
"""
import os, re, sys, subprocess, shutil, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "app" / "app.html"
OUT = ROOT / "app" / "viewer.html"
ANDROID = ROOT / "android" / "app" / "src" / "viewer" / "assets" / "app.html"

# 空殼:名稱、回傳的形狀要和原本一樣,其他畫面(圖、燈號、通知欄)才不會壞;數值一律是「沒有」
STUBS = {
    "RULES": """const RULESET1 = { name: '判別式一' };
const SIG1 = { takePct: 2.5, stopPct: 1.5, skipMin: 15, lampBars: 3, lampWarnBars: 1, scaleBars: 5, scaleMaFull: 0.5, scaleObFull: 20, scaleW: {ma: 0, obma: 0.75, ob: 0.25}, strong: false, entryMode: '' };
const RULESETS = {
  '一': { key: '一', btn: '判一', name: '判別式一', sig: Object.assign({}, SIG1) },
  '二': { key: '二', btn: '判二', name: '判別式二', sig: Object.assign({}, SIG1) },
  '三': { key: '三', btn: '判三', name: '判別式三', sig: Object.assign({}, SIG1) },
};
""",
    "SIGLIGHT": """function signalLight(){ return {cls: 'wait', text: '看盤', why: '看盤版'}; }
function exitSignal(){ return null; }
""",
    "REPLAY": """function replaySignals(bars){ return {marks: [], state: null, stateAt: () => null, rvol: null, entries: []}; }
function strongHolds(){ return false; }
function strongLampDir(){ return 0; }
""",
    "CHOP": """function chopPoints(){ return []; }
window.chopPoints = chopPoints;
""",
    "OSTOP": """function originStop(){ return null; }
window.originStop = originStop;
""",
    "TURN": """function turnPoints(){ return []; }
function turnBias(){ return new Map(); }
""",
    "RULETEXT": """  const rulesHtml = () => '看盤版沒有判別式規則(訊號由主程式提供)';
""",
    "BG": """async function bgRebuild(){}
function bgExit(){}
async function bgTick(force, replay){ return bgTickViewer(force); }
""",
}

HEADER = """<!--
  名揚四海。自動當沖 — 看盤版(viewer):由 tools/make_viewer.py 從 app/app.html 產生,判一二三的程式碼不在這份裡;訊號由 Worker 提供。
-->
"""


def terser():
    t = os.environ.get("TERSER")
    if t and Path(t).exists(): return t
    for c in (ROOT / "node_modules" / ".bin" / "terser", Path("node_modules/.bin/terser")):
        if c.exists(): return str(c)
    w = shutil.which("terser")
    if w: return w
    raise SystemExit("找不到 terser:npm i terser(或設環境變數 TERSER=路徑)")


def strip_js(code):
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(code); src = f.name
    out = src + ".min.js"
    r = subprocess.run([terser(), src, "--format", "comments=false,beautify=false", "-o", out], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("terser 失敗:" + r.stderr[:500])
    res = Path(out).read_text(encoding="utf-8"); os.unlink(src); os.unlink(out)
    return res


def build():
    s = SRC.read_text(encoding="utf-8")
    # 1. 檔頭文件
    i = s.index("<!--"); j = s.index("-->", i) + 3
    assert i < 200 and "<head>" in s[j:j + 20000], "檔頭格式不如預期"
    s = s[:i] + HEADER.rstrip("\n") + s[j:]
    # 2. 判一二三程式碼
    names = re.findall(r"/\*@@STRIP:(\w+)@@\*/", s)
    assert names, "app.html 裡沒有 @@STRIP 標記"
    for n in names:
        assert n in STUBS, f"沒有 {n} 的空殼"
        s2 = re.sub(r"/\*@@STRIP:%s@@\*/.*?/\*@@END:%s@@\*/\n?" % (n, n), lambda m: STUBS[n], s, count=1, flags=re.S)
        assert s2 != s, f"{n} 沒有換到"
        s = s2
    for n in names:
        assert f"@@STRIP:{n}@@" not in s
    # 3. 看盤版旗標
    s = s.replace("<head>", '<head>\n  <meta name="app-mode" content="viewer">', 1)
    # 4. 註解:JS 用 terser,其他用正則
    def js_repl(m):
        return m.group(1) + strip_js(m.group(2)) + m.group(3)
    s = re.sub(r"(<script(?![^>]*src)[^>]*>)(.*?)(</script>)", js_repl, s, flags=re.S)
    s = re.sub(r"(<style[^>]*>)(.*?)(</style>)", lambda m: m.group(1) + re.sub(r"/\*.*?\*/", "", m.group(2), flags=re.S) + m.group(3), s, flags=re.S)
    s = re.sub(r"<!--(?!\n  名揚四海).*?-->", "", s, flags=re.S)
    # 檢查:判一二三的字眼不該留在程式裡(標題 / 按鍵文字除外)
    s = s.replace("window.LOGIC_RULES ? LOGIC_RULES(k) : ", "")   # 看盤版沒有載入器要用的規則文字
    for bad in ("RULESET1 = { name: '判別式一', date", "function replaySignals(bars, ma, ob, obma", "gapMinPct", "obmaSlopeMin", "chopWin", "firstRevPct", "strongDxBars", "noWeakEntry", "longCtx"):
        assert bad not in s, f"看盤版裡還有 {bad}"
    assert "replaySignals" in s   # App 的 Page.autoUpdate 用這個字判斷下載到的是不是完整網頁
    OUT.write_text(s, encoding="utf-8")
    print(f"viewer.html {len(s):,} bytes(原 {SRC.stat().st_size:,})")
    if "--android" in sys.argv:
        ANDROID.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(OUT, ANDROID); print("→", ANDROID.relative_to(ROOT))


if __name__ == "__main__":
    build()
