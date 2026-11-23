#!/usr/bin/env python3
"""2026-11-22 實盤 vs 總表 每日對帳(判別式聊天室要的 F;使用者「三個都改」,總表要算得跟電腦實際一樣)。

用法:python3 tools/recon.py 日期 signals.json verdicts.json fills.json [總表.json] [輸出資料夾]
  signals  = 電腦引擎當天發的通知(engine_data/live_<日期>.jsonl,中繼每分鐘備份到雲端 ?pcbk name=signals)
  verdicts = 中繼回報每則進場訊號有沒有進場(verdict_<日期>.jsonl,name=verdicts):{_k, ok, why}
  fills    = 元大當天成交(手機明細同一份,name=fills;legs = 每一趟 進 / 出)
  總表     = 判別式聊天室放的 reports/總表_<日期>.json(沒有就只出實盤那份)
輸出:reports/實盤_<日期>.json(每則進場訊號 + 有沒有進 + 實際成交)、reports/對帳_<日期>.json(有總表才出)

總表每筆欄位(判別式聊天室決定;下面這些名稱都認):
  code、rs(判別式代號 一 一2 … 十,或「判十」)、side(1 / -1 / long / short)、t(訊號那根 HH:MM)、
  e(進場價)、lots、xt(出場時間)、x(出場價)、why(出場原因)、net(淨損益)
"""
import json, os, re, sys

RE_CODE = re.compile(r"(?<![\d.])(\d{4,6}[A-Z]?)(?![\d.])")
RE_BAR = re.compile(r"^\s*(\d{1,2}:\d{2})")
RE_PX_END = re.compile(r"([\d,]+(?:\.\d+)?)\s*$")
RE_JUDGE = re.compile(r"判[一二三四五六七八九十]")
RE_LOTS = re.compile(r"·\s*(\d+)張\s*·")
RE_XIN = re.compile(r"(做多|做空)\s*(\d{1,2}:\d{2})\s*進\s*([\d,]+(?:\.\d+)?)\s*→\s*現價\s*([\d,]+(?:\.\d+)?)")
CN = {"判一": "一", "判二": "二", "判三": "三", "判四": "四", "判五": "五", "判六": "六", "判七": "七", "判八": "八", "判九": "九", "判十": "十"}

def tod(s):
    """時間字串 → 'HH:MM:SS'(24 小時制);認得 '09:49'、'09:49:20'、元大的 '2026/11/23 上午 09:49:20' / '下午 01:05:02'(2026-11-23 修:原本只認開頭是時間的,元大成交時間配不到訊號)"""
    s = str(s or ""); m = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", s)
    if not m: return ""
    h = int(m.group(1))
    if ("下午" in s or "PM" in s.upper()) and h < 12: h += 12
    elif ("上午" in s or "AM" in s.upper()) and h == 12: h = 0
    return f"{h:02d}:{m.group(2)}:{m.group(3) or '00'}"
hm = lambda s: tod(s)[:5]
def mi(s):
    s = hm(s); return int(s[:2]) * 60 + int(s[3:5]) if s else None
num = lambda s: float(str(s).replace(",", ""))

def rs_of(judge, body, side):
    """通知上的「判X」→ 判別式代號;判一急彈空 = 一2、判六做空 = 六3(和引擎 JUDGE 對照表反過來)"""
    r = CN.get(judge or "", "")
    if r == "一" and "急彈空" in (body or ""): return "一2"
    if r == "六" and side < 0: return "六3"
    return r

def entries_of(signals, verdicts):
    V = {}
    for v in verdicts or []:
        if isinstance(v, dict) and v.get("_k"): V[v["_k"]] = v
    out = []
    for it in signals or []:
        if not isinstance(it, dict) or it.get("kind") not in ("long", "short"): continue
        title, body = it.get("title", ""), it.get("body", "")
        m = RE_CODE.search(title); code = m.group(1) if m else ""
        side = 1 if it["kind"] == "long" else -1
        mb = RE_BAR.match(body); mp = RE_PX_END.search(title); mj = RE_JUDGE.findall(body); ml = RE_LOTS.search(body)
        v = V.get(it.get("_k") or "")
        out.append({"code": code, "name": title.split(code)[0].split()[-1] if code and title.split(code)[0].split() else "",
                    "rs": rs_of(mj[-1] if mj else "", body, side), "side": side, "t": hm(mb.group(1)) if mb else "", "push": hm(it.get("t")),
                    "px": num(mp.group(1)) if mp else None, "lots_sig": int(ml.group(1)) if ml else None, "over": bool(re.search(r"·\s*超額\s*·", body)),
                    "ok": (v or {}).get("ok"), "why": (v or {}).get("why", "") if v else "沒有中繼回報", "_k": it.get("_k", "")})
    out.sort(key=lambda r: (r["t"], r["push"]))
    return out

def exits_of(signals):
    """引擎的虛擬出場通知(判別式照模擬算的出場;實盤出場看 legs)"""
    out = []
    for it in signals or []:
        if not isinstance(it, dict) or it.get("kind") not in ("exit", "profit", "loss"): continue
        title, body = it.get("title", ""), it.get("body", "")
        m = RE_CODE.search(title); mx = RE_XIN.search(body); mj = RE_JUDGE.findall(body); mb = RE_BAR.match(body)
        if not m or not mx: continue
        side = 1 if mx.group(1) == "做多" else -1
        out.append({"code": m.group(1), "rs": rs_of(mj[-1] if mj else "", body, side), "side": side, "t": hm(mx.group(2)), "xt": hm(mb.group(1)) if mb else hm(it.get("t")),
                    "x": num(mx.group(4)), "why": title.split(m.group(1), 1)[1].strip()})
    return out

def real_of(entries, legs):
    """元大每一趟(legs)配回進場訊號:同代碼同方向、開倉時間 ≥ 訊號那根 的最後一則「有進場」訊號。配不到的 = 手動 / 元三 / 強三"""
    ok = [e for e in entries if e["ok"]]
    for e in ok: e["legs"] = []
    other = []
    legs = [dict(g, t0=tod(g.get("t0")) or g.get("t0"), t=tod(g.get("t")) or g.get("t")) for g in (legs or [])]
    for g in sorted(legs, key=lambda g: str(g.get("t0", ""))):
        side = 1 if g.get("dir") == "做多" else -1; t0 = mi(g.get("t0"))
        cand = [e for e in ok if e["code"] == str(g.get("code")) and e["side"] == side and t0 is not None and mi(e["t"]) is not None and mi(e["t"]) <= t0]
        if cand: cand[-1]["legs"].append(g)
        else: other.append(g)
    for e in ok:
        L = e["legs"]
        if not L: e["real"] = None; continue
        n = sum(int(g["lots"]) for g in L)
        e["real"] = {"lots": n, "e": round(sum(g["ent"] * g["lots"] for g in L) / n, 4), "t0": min(str(g["t0"]) for g in L),
                     "x": round(sum(g["ex"] * g["lots"] for g in L) / n, 4), "xt": max(str(g["t"]) for g in L), "net": sum(int(g.get("net") or 0) for g in L)}
    return other

def bt_rows(B):
    rows = B.get("trades", B) if isinstance(B, dict) else B
    out = []
    for r in rows or []:
        sd = r.get("side"); sd = 1 if sd in (1, "1", "long", "做多", "多") else -1
        rs = str(r.get("rs") or r.get("judge") or ""); rs = CN.get(rs, rs.replace("判", "") if rs.startswith("判") else rs)
        out.append({"code": str(r.get("code")), "rs": rs, "side": sd, "t": hm(r.get("t")), "e": r.get("e"), "lots": r.get("lots"),
                    "xt": hm(r.get("xt")), "x": r.get("x"), "why": r.get("why", ""), "net": r.get("net")})
    return out

def compare(entries, bt):
    """總表 vs 實盤。同一筆 = 同代碼、同判別式、同方向、訊號那根差 ≤ 1 分鐘"""
    used = set(); both, only_bt = [], []
    for b in bt:
        j = next((i for i, e in enumerate(entries) if i not in used and e["code"] == b["code"] and e["rs"] == b["rs"] and e["side"] == b["side"]
                  and mi(e["t"]) is not None and mi(b["t"]) is not None and abs(mi(e["t"]) - mi(b["t"])) <= 1), None)
        if j is None:
            only_bt.append(dict(b, 原因="引擎沒有發這個進場訊號")); continue
        used.add(j); e = entries[j]
        if not e["ok"]:
            only_bt.append(dict(b, 原因=f"引擎有發、中繼沒進場:{e['why'] or '?'}", 訊號價=e["px"], 推送=e["push"])); continue
        r = e.get("real")
        if not r:
            only_bt.append(dict(b, 原因="中繼說有進場,但元大成交配不到(沒成交 / 被取消)", 訊號價=e["px"])); continue
        d = {}
        if b["lots"] is not None and int(b["lots"]) != r["lots"]: d["張數"] = [b["lots"], r["lots"]]
        if b["e"] is not None and abs(float(b["e"]) - r["e"]) > 1e-6: d["進場價"] = [b["e"], r["e"]]
        if b["x"] is not None and abs(float(b["x"]) - r["x"]) > 1e-6: d["出場價"] = [b["x"], r["x"]]
        if b["xt"] and hm(r["xt"]) and b["xt"] != hm(r["xt"]): d["出場時間"] = [b["xt"], hm(r["xt"])]
        if b["net"] is not None and int(b["net"]) != r["net"]: d["淨損益"] = [b["net"], r["net"]]
        both.append({"code": b["code"], "rs": b["rs"], "side": b["side"], "t": b["t"], "總表": b, "實盤": r, "不同": d})
    only_real = [{k: e[k] for k in ("code", "name", "rs", "side", "t", "push", "px")} | {"實盤": e["real"]} for i, e in enumerate(entries) if i not in used and e["ok"] and e.get("real")]
    return both, only_bt, only_real

def main(a):
    d, fs, fv, ff = a[:4]; fb = a[4] if len(a) > 4 and a[4] else ""; od = a[5] if len(a) > 5 else "reports"
    ld = lambda f: (json.load(open(f, encoding="utf-8")) if f and os.path.exists(f) else None)
    S, V, F = ld(fs), ld(fv), ld(ff)
    S = (S or {}).get("v", S) if isinstance(S, dict) else S; V = (V or {}).get("v", V) if isinstance(V, dict) else V; F = (F or {}).get("v", F) if isinstance(F, dict) and "v" in F else F
    ent = entries_of(S or [], V or []); legs = (F or {}).get("legs") or []
    other = real_of(ent, legs)
    os.makedirs(od, exist_ok=True)
    real = {"d": d, "說明": "電腦引擎當天每則進場訊號、中繼有沒有進場(why = 沒進原因)、元大實際成交(real;legs = 每一趟)。exits = 引擎虛擬出場通知;other = 配不到訊號的成交(手動 / 元三 / 強三)",
            "entries": [{k: v for k, v in e.items() if k != "_k"} for e in ent], "exits": exits_of(S or []), "other": other,
            "pnl": {"實盤淨損益(配到訊號的)": sum((e.get("real") or {}).get("net", 0) for e in ent if e.get("ok")), "全部成交淨損益": sum(int(g.get("net") or 0) for g in legs)},
            "有資料": {"signals": S is not None, "verdicts": V is not None, "fills": F is not None}}
    json.dump(real, open(os.path.join(od, f"實盤_{d}.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    B = ld(fb)
    if B is None: print(d, "實盤", len(ent), "則進場訊號,沒有總表 → 只出實盤"); return
    both, only_bt, only_real = compare(ent, bt_rows(B))
    diff = [x for x in both if x["不同"]]
    rep = {"d": d, "摘要": {"兩邊都有": len(both), "其中有不同": len(diff), "總表有實盤沒有": len(only_bt), "實盤有總表沒有": len(only_real),
                            "總表淨損益": sum(int(b.get("net") or 0) for b in bt_rows(B)), "實盤淨損益(配到訊號的)": real["pnl"]["實盤淨損益(配到訊號的)"]},
           "總表有實盤沒有": only_bt, "實盤有總表沒有": only_real, "兩邊都有但不同": diff, "兩邊一樣": [x for x in both if not x["不同"]]}
    json.dump(rep, open(os.path.join(od, f"對帳_{d}.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(d, json.dumps(rep["摘要"], ensure_ascii=False))

if __name__ == "__main__":
    main(sys.argv[1:])
