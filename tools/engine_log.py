#!/usr/bin/env python3
"""2026-11-27 判別式聊天轉使用者(判別式聊天也要看得到電腦引擎的進出場紀錄、不用鑰匙):
把電腦中繼備份到雲端的當天紀錄(?pcbk signals / verdicts / blocked / fills,和 ?log=get 的全部通知)整理成
engine_log/<日期>.txt(公開庫,誰都能讀)。GitHub Actions engine-log.yml 盤中每 5 分鐘、收盤後再跑一次。

用法:python3 tools/engine_log.py <日期> <資料夾>   (資料夾裡有 signals.json / verdicts.json / blocked.json / fills.json / log.json,缺的當空的)
不放任何機密:只輸出白名單欄位(時間 / 股票 / 判別式 / 方向 / 價格 / 張數 / 原因),文字再過濾一次
(身分證、7 位以上的數字、書號 / 委託序號、key / token / 帳號字樣 → ***)。
"""
import json, os, re, sys

D, SRC = sys.argv[1], sys.argv[2]
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'engine_log')


def ld(nm):
    try:
        v = json.load(open(os.path.join(SRC, f'{nm}.json'), encoding='utf-8'))
        return v if v is not None else []
    except Exception:
        return []


_SEC = [(re.compile(r'[A-Z][12]\d{8}'), '***'),                         # 身分證
        (re.compile(r'(書號|委託書號|序號|單號|order_?no|orderno)\s*[:=：]?\s*\S+', re.I), r'\1 ***'),
        (re.compile(r'(key|token|密碼|帳號|account|acct|pass(word)?)\s*[:=：]\s*\S+', re.I), r'\1=***'),
        (re.compile(r'\d{7,}'), '***')]                                   # 7 位以上數字(帳號、書號、時間戳)


def clean(s):
    s = re.sub(r'\s+', ' ', str(s or '')).strip()
    for p, r in _SEC: s = p.sub(r, s)
    return s


def sub_rs(body, rs):
    """判七 → 七2 / 七3 這種細分(和電腦引擎對帳時同一套關鍵字)"""
    b = body or ''
    if rs == '判六' and '恐慌翻紅' in b: return '判六(六4)'
    if rs == '判一' and '急彈空' in b: return '判一(一2)'
    if rs == '判三' and '判新' in b: return '判三(三2)'
    if rs == '判七' and '極端' in b: return '判七(七2)'
    if rs == '判七' and '大跌' in b: return '判七(七3)'
    return rs


def rs_of(body):
    m = re.search(r'判[一二三四五六七八九十]', body or '')
    return sub_rs(body, m.group(0)) if m else ''


def code_name(title):
    m = re.search(r'(\S+)\s+(\d{4,6}[A-Z]?)\b', title or '')
    return (m.group(2), m.group(1).lstrip('✅❌⚠🔴🟢📉📈⛔')) if m else ('', '')


sig = ld('signals'); ver = {v.get('_k'): v for v in ld('verdicts') if isinstance(v, dict)}
blk = ld('blocked'); fills = ld('fills'); logs = ld('log')
rows = []   # (時間, 排序, 文字)
sigkeys = set()
for s in sig if isinstance(sig, list) else []:
    t = str(s.get('t') or '')[:5]; ti = clean(s.get('title')); bo = clean(s.get('body')); k = s.get('kind')
    sigkeys.add((t, ti))
    code, name = code_name(ti); rs = rs_of(bo)
    if k in ('long', 'short'):
        v = ver.get(s.get('_k'))
        vv = '' if v is None else (' 中繼:進場' if v.get('ok') else f" 中繼:未進場({clean(v.get('why'))})")
        rows.append((t, 1, f"{t} 進場訊號 電腦 {rs} {code} {name} {'多' if k == 'long' else '空'} | {ti} | {bo}{vv}"))
    else:
        rows.append((t, 3, f"{t} 出場 電腦 {rs} {code} {name} | {ti} | {bo}"))
for b in blk if isinstance(blk, list) else []:
    t = str(b.get('t') or '')[:5]
    why = clean(b.get('miss') or b.get('line'))
    rows.append((t, 0, f"{t} 被擋/等待 電腦 {sub_rs(why, str(b.get('rs') or ''))}({b.get('rsk', '')}) {b.get('code', '')} {clean(b.get('name'))} | {why}"))
for it in logs if isinstance(logs, list) else []:
    t = str(it.get('t') or '')[:5]; ti = clean(it.get('title')); bo = clean(it.get('body'))
    if (t, ti) in sigkeys: continue   # 電腦發的已經列過
    k = it.get('kind') or ''
    if '元大中繼' in bo and k not in ('long', 'short', 'exit', 'profit', 'loss'): continue   # 中繼提醒(當天帳戶輸贏、留倉設定…)是帳戶私事,公開檔不放
    elif '· 手機' in bo or '強三' in bo or '強制' in ti: src = '手機'
    else: src = '手機/雲端'
    code, name = code_name(ti)
    lab = '進場訊號' if k in ('long', 'short') else '出場' if k in ('exit', 'profit', 'loss') else '通知'
    rows.append((t, 2 if lab == '進場訊號' else 4, f"{t} {lab} {src} {rs_of(bo)} {code} {name} | {ti} | {bo}"))
for sn in ld('pnl_snaps') or []:   # 2026-11-27 #16 輸贏通知當下每檔部位(中繼算帳面用的現價)
    try:
        t = str(sn.get('t') or '')[:5]
        rows.append((t, 6, f"{t} 輸贏通知當下部位 中繼 | " + ' '.join(f"{r.get('code')}{'多' if r.get('dir', 0) > 0 else '空'}{r.get('qty')}@{r.get('entry')}→{r.get('px')}({r.get('u', 0):+,}" + (f",{r.get('age')}秒前" if r.get('age') is not None else '') + (',留倉' if r.get('carry') else '') + ')' for r in sn.get('pos') or [])))
    except Exception: pass
legs = (fills.get('legs') if isinstance(fills, dict) else None) or []
for g in legs:
    t0 = str(g.get('t0') or '')[:5]; t1 = str(g.get('t') or '')[:5]
    rows.append((t1 or t0, 5, f"{t1 or t0} 元大成交(配對) {g.get('code', '')} {clean(g.get('name'))} {g.get('dir', '')} {g.get('lots', '')}張 "
                              f"{t0} 進 {g.get('ent')} → {t1} 出 {g.get('ex')} 淨 {g.get('net', 0):+,}"))
openf = []
if isinstance(fills, dict):
    for r in fills.get('rows') or []:   # 還沒沖銷的(每檔合併)
        try:
            if int(r.get('open') or 0): openf.append(f"  {r.get('code', '')} {clean(r.get('name'))} 未沖 {r.get('open')} 張")
        except Exception: pass
rows.sort(key=lambda x: (x[0], x[1]))
os.makedirs(OUT, exist_ok=True)
hdr = [f"# {D} 電腦引擎進出場紀錄(自動產生,engine-log.yml;來源:電腦中繼備份到雲端的 signals / verdicts / blocked / fills + 雲端通知)",
       "# 欄位:時間 類別 來源(電腦 = 電腦引擎發 / 手機 = 手機發;中繼的帳戶提醒不放)判別式 代碼 名稱 | 通知標題 | 內容(原因、價格、張數)",
       f"# 進場訊號 {sum(1 for r in rows if r[1] in (1, 2))} 則、出場 {sum(1 for r in rows if r[1] in (3, 4))} 則、被擋/等待 {sum(1 for r in rows if r[1] == 0)} 則、元大成交 {len(legs)} 趟",
       "# 不含任何金鑰 / 帳號 / 委託書號(寫入前過濾)", ""]
txt = '\n'.join(hdr + [r[2] for r in rows] + ([''] + ['# 目前未沖銷'] + openf if openf else [])) + '\n'
open(os.path.join(OUT, f'{D}.txt'), 'w', encoding='utf-8').write(txt)
print(f'engine_log/{D}.txt:{len(rows)} 行')
