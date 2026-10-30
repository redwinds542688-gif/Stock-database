"""把判一二三從公開庫搬到私人庫(2026-10-29 使用者:「我沒有要把全部改成私人 只要判別式改成私人」「主程式 k線..都可以在github公開區域」)

做什麼(一次性,可重跑;已經搬過就只重產 judge.js):
  1. app/app.html 裡 /*@@STRIP:名@@*/ … /*@@END:名@@*/ 之間的判一二三程式碼 → 私人庫 Stock-logic/judge.js
     (RULES 區塊的 const RULESET1 / SIG1 / RULESETS 改成指定給公開庫留的 var 空殼;RULETEXT 變成 window.LOGIC_RULES;最後把 RULESET / SIG 重新指到真的參數)
  2. 公開庫的 app.html:這些區塊換成空殼(和看盤版一樣的形狀,但 RULES 用 var 讓 judge.js 能蓋掉),標記留著給 make_viewer.py 用
  3. app.html 檔頭那一大段規格 / 變更紀錄 → Stock-logic/app_header.md,公開庫只留幾行
用法:python3 tools/split_logic.py [--logic ../stock-logic]
之後改判一二三 = 改 Stock-logic/judge.js(不是 app.html),App 開啟時用 owner 鑰匙經 Worker 拿 judge.js 裝上去。
"""
import re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOGIC = Path(sys.argv[sys.argv.index("--logic") + 1]).resolve() if "--logic" in sys.argv else (ROOT.parent / "stock-logic")
APP = ROOT / "app" / "app.html"

STUBS = {
    "RULES": """var RULESET1 = { name: '判別式一' };   // 真的參數在私人庫 Stock-logic/judge.js(owner 鑰匙經 Worker 載入後蓋掉這些 var)
var SIG1 = { takePct: 2.5, stopPct: 1.5, skipMin: 15, lampBars: 3, lampWarnBars: 1, scaleBars: 5, scaleMaFull: 0.5, scaleObFull: 20, scaleW: {ma: 0, obma: 0.75, ob: 0.25}, strong: false, entryMode: '' };
var RULESETS = {
  '一': { key: '一', btn: '判一', name: '判別式一', sig: Object.assign({}, SIG1) },
  '二': { key: '二', btn: '判二', name: '判別式二', sig: Object.assign({}, SIG1) },
  '三': { key: '三', btn: '判三', name: '判別式三', sig: Object.assign({}, SIG1) },
};
""",
    "SIGLIGHT": "function signalLight(){ return null; }\nfunction exitSignal(){ return null; }\n",
    "REPLAY": "function replaySignals(bars){ return {marks: [], state: null, stateAt: () => null, rvol: null, entries: []}; }\nfunction strongHolds(){ return false; }\nfunction strongLampDir(){ return 0; }\n",
    "CHOP": "function chopPoints(){ return []; }\nwindow.chopPoints = chopPoints;\n",
    "OSTOP": "function originStop(){ return null; }\nwindow.originStop = originStop;\n",
    "TURN": "function turnPoints(){ return []; }\n",
    "RULETEXT": "  const rulesHtml = (k = ACTIVE_RS) => (window.LOGIC_RULES ? LOGIC_RULES(k) : '判別式規則在私人庫,要 owner 鑰匙載入後才看得到');\n",
    "BG": "async function bgRebuild(){}\nfunction bgExit(){}\nfunction bgManagePos(){}\nwindow.bgManagePos = bgManagePos;\nfunction bgBarsKey(){ return ''; }\nasync function bgTick(force, replay){ return bgTickViewer(force); }\n",
}
HEADER = """<!--
  名揚四海。自動當沖 — 主程式(公開庫 Stock-database)。
  2026-10-29 判一二三的程式碼、規格與變更紀錄全文搬到私人庫 Stock-logic(judge.js、app_header.md、判別式*.md、backtest/);
  自己的 App 用 owner 鑰匙經 Worker 載入 judge.js;看盤版(viewer.html)沒有。交接先讀 交接說明.md。
  變更紀錄(2026-10-29 搬家以後,只寫畫面 / 功能,規則細節寫在 Stock-logic):
-->
"""


def main():
    s = APP.read_text(encoding="utf-8")
    blocks = {}
    for m in re.finditer(r"/\*@@STRIP:(\w+)@@\*/\n(.*?)/\*@@END:\1@@\*/\n", s, re.S):
        blocks[m.group(1)] = m.group(2)
    real = {n: b for n, b in blocks.items() if b.strip() != STUBS[n].strip()}
    if not real:
        print("app.html 裡已經沒有判一二三的程式碼(都是空殼),不重搬"); return
    assert set(real) == set(STUBS), f"區塊不齊:{sorted(real)}"
    # 1. judge.js
    js = ["// 判一二三(私人庫 Stock-logic)— 由 tools/split_logic.py 從公開庫 app.html 抽出(2026-10-29)。",
          "// 改規則改這裡(App + 模擬器 backtest/sim_day.py + backtest/chk/rs_env.py 三個一起改,跑 backtest/chk/rs3_cmp2.py 對照 0 差異)。",
          "// App 用 owner 鑰匙經 Worker(?gh=raw&repo=logic&path=judge.js)載入;看盤版沒有。", ""]
    for n in ("RULES", "SIGLIGHT", "REPLAY", "CHOP", "OSTOP", "TURN", "BG", "RULETEXT"):
        b = real[n]
        if n == "RULES":
            b = re.sub(r"^const (RULESET1|SIG1|RULESETS) =", r"\1 =", b, flags=re.M)
            assert "const RULESET" not in b and "const SIG1" not in b
        if n == "RULETEXT":
            b = re.sub(r"^  const rulesHtml = ", "window.LOGIC_RULES = ", b, count=1, flags=re.M)
            assert b.startswith("window.LOGIC_RULES = ")
        js.append(f"// ───── {n} ─────"); js.append(b)
    js.append("// 載入後把現用的那份指回真的參數(app.html 的 let RULESET / SIG 原本指著空殼)")
    js.append("try{ RULESET = RULESETS[ACTIVE_RS] || RULESETS['一']; SIG = RULESET.sig; }catch(e){}")
    js.append("window.LOGIC_LOADED = true; try{ if (window.onLogicLoaded) onLogicLoaded(); }catch(e){}")
    js.append("")
    (LOGIC / "judge.js").write_text("\n".join(js), encoding="utf-8")
    # 2. 公開庫 app.html:空殼
    for n in real:
        s = re.sub(r"/\*@@STRIP:%s@@\*/\n.*?/\*@@END:%s@@\*/\n" % (n, n), lambda m: f"/*@@STRIP:{n}@@*/\n{STUBS[n]}/*@@END:{n}@@*/\n", s, count=1, flags=re.S)
    # 3. 檔頭
    i = s.index("<!--"); j = s.index("-->", i) + 3
    (LOGIC / "app_header.md").write_text("# app.html 原檔頭(規格、交接、變更紀錄全文;2026-10-29 搬到私人庫)\n\n```\n" + s[i:j] + "\n```\n", encoding="utf-8")
    s = s[:i] + HEADER.rstrip("\n") + s[j:]
    APP.write_text(s, encoding="utf-8")
    print(f"judge.js {(LOGIC / 'judge.js').stat().st_size:,} bytes;app_header.md {(LOGIC / 'app_header.md').stat().st_size:,} bytes;app.html {APP.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
