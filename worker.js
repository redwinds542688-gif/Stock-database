/* ══════════════════════════════════════════════════════
   名揚四海 — 盤中即時行情轉接站(Cloudflare Worker)
   ------------------------------------------------------
   手機瀏覽器因 CORS 不能直接呼叫證交所,所以由這個 Worker 代為呼叫
   證交所「個股即時行情」接口(mis.twse.com.tw,每 5 秒更新),
   再加上允許跨站的標頭回傳給手機。

   手機呼叫方式:
     https://twse-proxy.<你的帳號>.workers.dev/?codes=2409,2303,6147&mkt=tse,tse,otc
     codes = 股票代號(逗號分隔,最多 ~100 檔)
     mkt   = 對應市場:tse(上市)或 otc(上櫃),省略則全部當上市

   回傳:證交所原始 JSON(msgArray 內每檔:c 代號、n 名稱、z 最新成交價、
         tv 當盤成交量、v 累積成交量、o 開、h 高、l 低、y 昨收、t 時間、
         a/b 五檔賣/買價、f/g 五檔賣/買量)

   第二條路(2026-10-15 新增):今天的 1 分K(Yahoo)
     https://twse-proxy.<你的帳號>.workers.dev/?yahoo=6770.TW&range=1d
     yahoo = 代號 + .TW(上市)或 .TWO(上櫃);range = 1d(預設)或 5d
     回傳 Yahoo 原始 JSON(chart.result[0].timestamp + indicators.quote[0] 的 open/high/low/close/volume)。
     用途:證交所接口只給最新一筆,程式開啟前的分鐘自己組不出來,開圖時用這條補今天 09:00 起的 K。

   第三條路(2026-10-16 新增):由 Cloudflare 準時叫 GitHub 跑爬蟲
     GitHub 自己的排程常延遲 1~5 小時(10/16 的 14:30 根本沒跑),Cloudflare 的 Cron Trigger 很準,
     所以改成:Cloudflare 到點 → 呼叫 GitHub API 的 workflow_dispatch → GitHub 立刻開始跑。
     需要在 Worker 設定一個 Secret:GH_TOKEN(GitHub fine-grained token,只給 Stock-database 的 Actions:Read and write)。
     Cron Trigger 只要一條(免費方案全帳號上限 5 條,樂透 Worker 已佔用):
       5,20,30,45 0,1,6,8,12 * * MON-FRI → 每天只醒來 20 次,scheduled() 自己判斷台灣時間:
         08:20、08:45、09:05 → 開盤前資格複檢(recheck.yml);14:30、16:30、20:30 → 每日資料(daily.yml)
     檢查:https://twse-proxy.<帳號>.workers.dev/?gh=status   → 顯示 token 有沒有設好、能不能連到 GitHub
     手動觸發(要另設 Secret CRON_KEY):?gh=run&what=daily&key=<CRON_KEY>  或 what=recheck

   第四條路(2026-10-19 新增):通知紀錄存雲端(Cloudflare KV,綁定名 LOGS)
     使用者:「通知欄的訊息需要紀錄在雲端,這樣不管那隻手機都可以看到相同的過往通知」
     POST ?log=add   body = JSON {d:'2026-10-19', key:'…', t:'09:27', title, body, kind, prev[, pos]}  pos = 部位鍵,同一部位只留一則(2026-10-20)
                     → 存 KV 鍵 log:<d>:<t>:<key>(兩支手機發同一則 → 同一個鍵,不會重複);保留 90 天
     GET  ?log=get&d=2026-10-19 → 那天所有通知(JSON 陣列,新的在前)
     GET  ?log=days             → 有紀錄的日期(JSON 陣列,舊到新)
     GET  ?log=del&d=2026-10-19&t=11:18&code=2402 → 刪掉那天那一分鐘那檔的紀錄(誤傳的測試資料用;回刪了幾筆)
     KV 命名空間由 deploy-worker.yml 自動建立並綁定(wrangler kv namespace create LOGS),不用手動設定。

   第五條路(2026-10-19 新增):雲端 1 分K(使用者:「Yahoo 延遲問題有什麼辦法解決嗎」→「做」)
     Yahoo 的今日分K 慢約 20 分鐘,手機自己拼的 K 又只從開程式那一刻開始;改成 Cloudflare 每分鐘自己抓:
     Cron 每分鐘醒來,台灣 09:00~13:35 向證交所抓 pool.json 全部候選股的最新價 + 累計量,存 KV 鍵 bar:<日期>:<HHMM>
     (每分鐘一個鍵,不做讀改寫,保留 14 天;一天約 270 次寫入,免費額度 1000)。
     GET ?bars=2026-10-19[&codes=2409,1802][&since=10:05]
       → {d, last:'13:30', bars:{代號:[[HHMM, 開, 高, 低, 收, 量(股)], …]}}
       每根 = 這一分鐘快照和前一分鐘快照相減:量 = 累計量差 × 1000,收 = 快照時最後成交價,開 = 前一根收,高低 = 開收的大小
       (沒有分鐘內的高低,影線比券商短);這分鐘沒成交就沒有那根(和 Yahoo 一樣)。since = 手機已有的最後一根時間,只回之後的。
       全天的查詢(沒 since)在邊緣快取 50 秒,KV 讀取量才不會爆。
     2026-10-20 09:40 取價改「同時發 4 個請求、每檔取累計量最大(最新)的那筆」(misFresh):證交所接口各伺服器新舊差 25~60 秒,
       單發一次常拿到舊資料,快照那分鐘就少一根 K(使用者:富喬「差了快二分鐘」→「改」)。?codes= 即時價同樣處理。
   ══════════════════════════════════════════════════════ */

const GH_OWNER = "redwinds542688-gif", GH_REPO = "Stock-database", GH_REF = "main";
const WORKFLOWS = { daily: "daily.yml", recheck: "recheck.yml" };

async function dispatch(env, what) {
  const file = WORKFLOWS[what];
  if (!file) return { ok: false, msg: "what 要是 daily 或 recheck" };
  if (!env || !env.GH_TOKEN) return { ok: false, msg: "Worker 沒有設定 GH_TOKEN" };
  const r = await fetch(`https://api.github.com/repos/${GH_OWNER}/${GH_REPO}/actions/workflows/${file}/dispatches`, {
    method: "POST",
    headers: { "Authorization": `Bearer ${env.GH_TOKEN}`, "Accept": "application/vnd.github+json",
               "User-Agent": "mysh-worker", "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json" },
    body: JSON.stringify({ ref: GH_REF }),
  });
  const txt = await r.text();
  return { ok: r.status === 204, status: r.status, msg: r.status === 204 ? `已叫 GitHub 跑 ${file}` : txt.slice(0, 300) };
}

// ── 第五條路:雲端 1 分K ──
const RAW = `https://raw.githubusercontent.com/${GH_OWNER}/${GH_REPO}/${GH_REF}`;
const MIS_HDR = { "User-Agent": "Mozilla/5.0", "Accept": "application/json", "Referer": "https://mis.twse.com.tw/stock/fibest.jsp" };
const twNow = () => new Date(Date.now() + 8 * 3600e3);
const ymdOf = t => t.toISOString().slice(0, 10);
const hhmmOf = t => t.toISOString().slice(11, 16);
async function poolCodes() {
  const r = await fetch(`${RAW}/pool.json`, { cf: { cacheTtl: 1800, cacheEverything: true } });
  if (!r.ok) throw new Error("pool.json " + r.status);
  const j = await r.json(); const pool = Array.isArray(j) ? j : (j.pool || []);
  return pool.map(s => ({ code: String(s.code), mkt: (s.market === "tpex" || s.market === "otc") ? "otc" : "tse" }));
}
/* 2026-10-20 09:40 使用者(富喬 09:21 截圖:「差了快二分鐘」→「改」):證交所接口後面有好幾台伺服器,每台更新時間不同,
   開盤時同一秒發 5 個請求拿到的資料相差 25~60 秒,有時 70 秒後還在回同一筆舊資料(09:26~09:31 實測)。
   每分鐘整點的快照抓到舊資料 → 那分鐘「沒成交」不出 K,下一分鐘兩分鐘併成一根(富喬 09:00~09:23 少了 5 根)。
   改成每次同時發 FRESH_N 個請求,每檔取累計量 v 最大(= 最新)的那筆;sysTime 也取最新。快照與手機即時價都用這條。 */
const FRESH_N = 4;
async function misFresh(ex_ch, n = FRESH_N) {
  const url = `https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=${ex_ch}&json=1&delay=0&_=`;
  const rs = await Promise.all(Array.from({ length: n }, (_, i) =>
    fetch(url + (Date.now() + i), { headers: MIS_HDR, cf: { cacheTtl: 0 } }).then(r => r.ok ? r.json() : null).catch(() => null)));
  let best = null; const by = new Map();
  for (const j of rs) {
    if (!j || !Array.isArray(j.msgArray)) continue;
    const st = (j.queryTime && j.queryTime.sysTime) || "";
    if (!best || st > ((best.queryTime && best.queryTime.sysTime) || "")) best = j;
    for (const m of j.msgArray) {
      if (!m || !m.c) continue;   // 2026-10-20 問錯市場的代號回的是空的一筆
      const o = by.get(m.c);
      const v = parseFloat(m.v) || 0, ov = o ? (parseFloat(o.v) || 0) : -1;
      if (!o || v > ov || (v === ov && (m.tlong || "") > (o.tlong || ""))) by.set(m.c, m);
    }
  }
  if (!best) return null;
  return { ...best, msgArray: [...by.values()] };
}
async function misFetch(list, n) {
  const out = [];
  for (let i = 0; i < list.length; i += 50) {
    const ex_ch = list.slice(i, i + 50).map(s => `${s.mkt}_${s.code}.tw`).join("|");
    try {
      const j = await misFresh(ex_ch, n);
      for (const m of ((j && j.msgArray) || [])) out.push(m);
    } catch (e) { console.log("mis " + e); }
  }
  return out;
}
/** Cron 每分鐘:盤中把全部候選股的 [最新價, 累計量(張)] 存成一個鍵 */
/* 2026-10-21 23:30 使用者(「改 兩個都改」):雲端 K 和 Yahoo 逐分鐘比,11:55 修正後收盤仍有 8% 的分鐘差 0.3% 以上,和益 09:53 真實高 57.5 雲端只有 57.0。
   (1) 每筆存證交所自己的資料時間 t(HH:MM:SS),拼 K 時照 t 歸到那一分鐘,不再用「快照時間 − 1 分鐘」
   (2) 每分鐘除了開頭那個快照(bar:日:時分),再在 :30、:56 各抓一次存到 bas:日:時分(兩筆一起寫,KV 一分鐘 2 次寫入,一天約 540 次 < 免費 1000);
       :56 那筆最接近這分鐘的收盤,三筆合起來才有這分鐘的高低點。子請求 12 + 6 + 12 + pool 1 ≈ 31 < 免費 50
   快照格式 [z, v, y, t](y = 昨收,子快照 0)。舊格式 [z, v] / [z, v, y] 沒有 t,照舊規則歸到快照前一分鐘 */
function buildSnap(msgs, ymd) {
  const s = {}; let n = 0, dOk = false;
  for (const m of msgs) {
    if (m.d && m.d !== ymd) continue;                       // 證交所回的交易日不是今天 → 休市
    dOk = true;
    let z = parseFloat(m.z); if (!isFinite(z) && m.trade) z = parseFloat(m.trade.z); if (!isFinite(z)) z = parseFloat(m.pz);
    // 2026-10-21 使用者(時碩 11:44「同股票 同時間 價格不同」:元大 87.00、App 86.50):這 5 秒沒成交時 z 是 "-",備用的 trade(最後一筆)
    //   有時好幾分鐘沒更新(時碩停在 11:40:59 86.50,其實已經成交到 87);11:46 抽查 130 檔有 47 檔落後超過 1 分鐘。
    //   z 是 "-" 時把價格夾在目前最佳買價 ~ 最佳賣價之間(舊價已經離開買賣價區間 = 價格早就動了)
    if (!isFinite(parseFloat(m.z)) && isFinite(z)) { const b1 = parseFloat(String(m.b || "").split("_")[0]), a1 = parseFloat(String(m.a || "").split("_")[0]); if (b1 > 0 && a1 > 0 && b1 <= a1) z = Math.min(Math.max(z, b1), a1); }
    const v = parseFloat(m.v);
    if (!isFinite(z) || !isFinite(v)) continue;
    const y = parseFloat(m.y), t = /^\d\d:\d\d:\d\d$/.test(m.t || "") ? m.t : "";
    s[m.c] = [z, v, isFinite(y) && y > 0 ? y : 0, t]; n++;   // 2026-10-21 加昨收 y(?breadth)、資料時間 t(拼 K 用)
  }
  return { s, n, dOk };
}
async function snapshotBars(env, subs) {
  if (!env || !env.LOGS) return "no KV";
  const t = twNow(); const hm = t.getUTCHours() * 60 + t.getUTCMinutes();
  if (hm < 9 * 60 || hm > 13 * 60 + 35) return "off-hours";
  const list = await poolCodes();
  const d = ymdOf(t), ymd = d.replace(/-/g, ""), key = hhmmOf(t);
  const r0 = buildSnap(await misFetch(list), ymd);
  if (!r0.dOk) return "holiday";
  await env.LOGS.put(`bar:${d}:${key}`, JSON.stringify(r0.s), { expirationTtl: 14 * 86400 });
  if (!subs || hm > 13 * 60 + 30) return `bar ${d} ${key} ${r0.n} 檔`;
  // 同一分鐘的 :30、:56 再各抓一次(:30 只問 2 次省子請求;:56 問 4 次取最新,當這分鐘的收盤)
  const out = [];
  for (const [sec, n] of [[30, 2], [56, FRESH_N]]) {
    const wait = sec * 1000 - (Date.now() % 60000);
    if (wait < 1000) continue;                               // 已經過了(Cron 晚醒)就跳過
    await new Promise(r => setTimeout(r, wait));
    try { const r = buildSnap(await misFetch(list, n), ymd); if (r.n) out.push(r.s); } catch (e) { console.log("子快照失敗 " + e); }
  }
  if (out.length) await env.LOGS.put(`bas:${d}:${key}`, JSON.stringify(out), { expirationTtl: 14 * 86400 });
  return `bar ${d} ${key} ${r0.n} 檔 + 子快照 ${out.length}`;
}
/** 把一天的快照拼成每檔的 1 分K */
async function dayBars(env, d, codes, since) {
  const keys = [];
  for (const pre of ["bar", "bas"]) {
    let cursor;
    do {
      const r = await env.LOGS.list({ prefix: `${pre}:${d}:`, cursor, limit: 1000 });
      for (const k of r.keys) keys.push(k.name);
      cursor = r.list_complete ? null : r.cursor;
    } while (cursor);
  }
  // 依時分排,同一分鐘 bar(開頭快照)在 bas(:30、:56 子快照)前面
  keys.sort((a, b) => a.slice(-5) < b.slice(-5) ? -1 : a.slice(-5) > b.slice(-5) ? 1 : (a < b ? -1 : 1));
  const addM = (hm, k) => { const m = +hm.slice(0, 2) * 60 + +hm.slice(3, 5) + k; return String(Math.floor(m / 60)).padStart(2, "0") + ":" + String(m % 60).padStart(2, "0"); };
  let use = keys;
  // 增量:從 since 前一分鐘的快照開始(當基準,不出那根),回傳 since 起的 K(since 那根可能還沒拼完,整根重給)
  if (since) { const sk = addM(since, -1); use = keys.filter(k => k.slice(-5) >= sk); }
  const obs = [];   // [快照時分, {code: rec}, 照 t 歸分鐘?]
  // 開頭快照(:0x)只有在前一分鐘有 :30 / :56 子快照時才照 t 歸分鐘;前一分鐘沒有(舊資料、子快照失敗)= 這筆其實是上一分鐘的收盤,照舊規則標前一分鐘
  const hasSub = new Set(keys.filter(k => k.startsWith("bas:")).map(k => k.slice(-5)));
  for (let i = 0; i < use.length; i += 25) {
    const part = use.slice(i, i + 25);
    const vals = await Promise.all(part.map(k => env.LOGS.get(k)));
    part.forEach((k, j) => {
      if (!vals[j]) return; let v; try { v = JSON.parse(vals[j]); } catch (e) { return; }
      if (k.startsWith("bas:")) { for (const sub of (Array.isArray(v) ? v : [])) obs.push([k.slice(-5), sub, true]); } else obs.push([k.slice(-5), v, hasSub.has(addM(k.slice(-5), -1))]);
    });
  }
  const nSnap = use.filter(k => k.startsWith("bar:")).length;
  const want = codes && codes.length ? new Set(codes) : null;
  const bars = {}, prev = {};
  // 2026-10-19 收盤後對照 Yahoo 整天 K 發現:HH:MM 的快照拿到的是「上一分鐘」的成交(12:01:0x 的快照 = 12:00 那一分鐘),
  // 標籤要往前移一分鐘才和 Yahoo / 手機自拼的 K(都以成交那一分鐘的開頭當標籤)一致;09:00 的第一個快照(開盤撮合)仍標 09:00
  // 2026-10-21 23:30 有資料時間 t 的快照改照 t 歸分鐘(見 buildSnap);沒有 t 的舊快照照舊
  const lbl = (hm, rec, useT) => { const t = useT && rec && rec[3]; if (t && t.length >= 5) { const m = t.slice(0, 5); return m < "09:00" ? "09:00" : m > "13:30" ? "13:30" : m; } return hm <= "09:00" ? "09:00" : addM(hm, -1); };
  for (const [hm0, s, useT] of obs) {
    for (const c in s) {
      if (want && !want.has(c)) continue;
      const rec = s[c], z = rec[0], v = rec[1], p = prev[c], hm = lbl(hm0, rec, useT);
      // 2026-10-21 使用者(台郡「量不對」):證交所偶爾回舊快照(時間倒退、累計量變少),原本 prev 被拉回舊的量,下一分鐘又把同一段量算一次
      //   (09:54 時 130 檔有 45 檔雲端 K 總量比證交所多 1% 以上,台郡多 1,573 張)。累計量比上一個快照少 → 這個快照整個不算
      if (p && v < p[1]) continue;
      prev[c] = [z, v];
      if (!p) { if (!since) bars[c] = [[hm, z, z, z, z, hm0 <= "09:02" ? Math.round(v * 1000) : 0]]; continue; }   // 當天第一個快照:09:00 開始存的話量 = 開盤撮合的累計;盤中才開始存(例如 2026-10-19 11:57 上線)就給 0,
                                                                                                                       // 不然整個上午的累計量會灌進那一根,OBV 一次跳好幾千張(使用者截圖:燿華 12:05 跳 3,700 張)。那段的量由手機用 Yahoo 補
      const dv = Math.max(0, v - p[1]) * 1000;
      if (dv <= 0 && z === p[0]) continue;                   // 這段沒成交
      const arr = (bars[c] = bars[c] || []), lb = arr[arr.length - 1];
      if (lb && lb[0] >= hm) { lb[2] = Math.max(lb[2], z); lb[3] = Math.min(lb[3], z); lb[4] = z; lb[5] += Math.round(dv); continue; }   // 同一分鐘(或資料時間比上一根還早)併進上一根
      arr.push([hm, p[0], Math.max(p[0], z), Math.min(p[0], z), z, Math.round(dv)]);
    }
  }
  // 今天還沒走完的這一分鐘不給(訊號只看收完的 K,和以前一樣);增量查詢只回 since 起的
  const cut = d === ymdOf(twNow()) ? hhmmOf(twNow()) : "99:99";
  let last = "";
  for (const c in bars) {
    bars[c] = bars[c].filter(b => b[0] < cut && (!since || b[0] >= since));
    if (!bars[c].length) { delete bars[c]; continue; }
    const lb = bars[c][bars[c].length - 1][0]; if (lb > last) last = lb;
  }
  return { d, last: last || since || "", n: nSnap, bars };   // last = 最後一根的標籤(手機下次用 since 傳回來)
}

/* 2026-10-21 使用者「改 判二 大盤偏多只做黑盤空」:大盤寬度 = 名單上每檔最新價 > 昨收的比例(上漲 /(上漲 + 下跌),平盤不算),每分鐘一個值。
   標籤規則和 dayBars 一樣(快照 HH:MM = 上一分鐘的成交);昨收用快照裡的 y,舊快照沒有 y 的用 pool.json(當天的 prev,收盤後換成明天名單時用 close)。
   模擬器 BREADTH 檔(tools/backtest/results/breadth_*.json)是同一個算法 */
async function dayBreadth(env, d) {
  const keys = []; let cursor;
  do {
    const r = await env.LOGS.list({ prefix: `bar:${d}:`, cursor, limit: 1000 });
    for (const k of r.keys) keys.push(k.name);
    cursor = r.list_complete ? null : r.cursor;
  } while (cursor);
  keys.sort();
  let pp = {};
  try {
    const r = await fetch(`${RAW}/pool.json`, { cf: { cacheTtl: 1800, cacheEverything: true } });
    const j = await r.json(), pool = Array.isArray(j) ? j : (j.pool || []);
    for (const s of pool) { const y = (s.date || j.date) === d ? +s.prev : (s.date || j.date) < d ? +s.close : NaN; if (y > 0) pp[String(s.code)] = y; }
  } catch (e) {}
  const lbl = hm => { if (hm <= "09:00") return "09:00"; const m = +hm.slice(0, 2) * 60 + +hm.slice(3, 5) - 1; return String(Math.floor(m / 60)).padStart(2, "0") + ":" + String(m % 60).padStart(2, "0"); };
  const last = {}, br = {};
  for (let i = 0; i < keys.length; i += 25) {
    const part = keys.slice(i, i + 25);
    const vals = await Promise.all(part.map(k => env.LOGS.get(k)));
    part.forEach((k, j) => {
      if (!vals[j]) return; let s; try { s = JSON.parse(vals[j]); } catch (e) { return; }
      for (const c in s) {
        const [z, v, y] = s[c], p = last[c];
        if (p && v < p[1]) continue;                          // 舊快照(累計量變少)不算,同 dayBars
        last[c] = [z, v, y > 0 ? y : (p && p[2]) || pp[c]];
      }
      let u = 0, dn = 0;
      for (const c in last) { const [z, , y] = last[c]; if (!(y > 0)) continue; if (z > y) u++; else if (z < y) dn++; }
      if (u + dn) br[lbl(k.slice(-5))] = Math.round(u / (u + dn) * 1000) / 1000;
    });
  }
  return { d, n: Object.keys(last).length, br };
}

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "*",
  "Cache-Control": "no-store",
};

export default {
  // ── Cloudflare Cron Trigger 到點 → 叫 GitHub 跑對應的 workflow ──
  // Workers 免費方案整個帳號只能有 5 條 Cron(樂透的 Worker 已經用掉),所以這裡只用 1 條:
  //   5,20,30,45 0,1,6,8,12 * * MON-FRI  → 每天醒來 20 次,只在下面這幾個台灣時間真的去叫 GitHub,其他時候什麼都不做
  // 2026-10-19 起 Cron 改成每分鐘(* 0-6,8,12 * * MON-FRI):台灣 09:00~13:35 每分鐘存一次雲端 1 分K 快照;原本的六個時段照舊叫 GitHub
  async scheduled(event, env, ctx) {
    const t = new Date(Date.now() + 8 * 3600e3);                      // 台灣時間
    const key = `${t.getUTCHours()}:${String(t.getUTCMinutes()).padStart(2, "0")}`;
    const SLOTS = { "8:20": "recheck", "8:45": "recheck", "9:05": "recheck", "14:30": "daily", "16:30": "daily", "20:30": "daily" };
    const what = SLOTS[key];
    if (what) { const r = await dispatch(env, what); console.log(`cron ${event.cron} 台灣 ${key} → ${what}: ${r.msg}`); }
    try { const r = await snapshotBars(env, true); if (r !== "off-hours") console.log(`cron 台灣 ${key} 分K:${r}`); } catch (e) { console.log("分K 快照失敗 " + e); }
  },

  async fetch(request, env, ctx) {
    if (request.method === "OPTIONS") return new Response(null, { headers: CORS });

    const url = new URL(request.url);
    const J = (obj, status = 200) => new Response(JSON.stringify(obj, null, 1), { status, headers: { ...CORS, "Content-Type": "application/json; charset=utf-8" } });

    // ── 第五條路:?bars=YYYY-MM-DD[&codes=…][&since=HH:MM] → 雲端 1 分K ──
    const bd = url.searchParams.get("bars");
    if (bd === "snap") {
      // 手動 / 補救:現在就存一個快照(這一分鐘已經有就不重存)。手機發現雲端 K 落後 2 分鐘以上會呼叫,Cron 沒跑也不會斷
      if (!env || !env.LOGS) return J({ error: "Worker 還沒綁定 KV(LOGS)" }, 503);
      try {
        const t = twNow(); const k = `bar:${ymdOf(t)}:${hhmmOf(t)}`;
        if (await env.LOGS.get(k)) return J({ ok: true, msg: "這一分鐘已有快照 " + k });
        const r = await snapshotBars(env, false);   // 手動補救只存開頭那一筆,不等 :30 / :56
        return J({ ok: true, msg: r });
      } catch (e) { return J({ ok: false, error: String(e && e.stack || e) }, 500); }
    }
    if (bd) {
      if (!env || !env.LOGS) return J({ error: "Worker 還沒綁定 KV(LOGS)" }, 503);
      const d = bd === "today" ? ymdOf(twNow()) : bd;
      if (!/^\d{4}-\d{2}-\d{2}$/.test(d)) return J({ error: "bars 要是 YYYY-MM-DD 或 today" }, 400);
      const codes = (url.searchParams.get("codes") || "").split(",").map(x => x.trim()).filter(Boolean);
      const since = url.searchParams.get("since") || "";
      if (since && !/^\d{2}:\d{2}$/.test(since)) return J({ error: "since 要是 HH:MM" }, 400);
      const ck = new Request(`${url.origin}/__bars?d=${d}&codes=${codes.join(",")}&since=${since}`);
      const cache = caches.default;
      const hit = await cache.match(ck);
      if (hit) { const h = new Headers(hit.headers); for (const [k, v] of Object.entries(CORS)) h.set(k, v); return new Response(hit.body, { status: 200, headers: h }); }
      const out = await dayBars(env, d, codes, since);
      const body = JSON.stringify(out);
      const resp = new Response(body, { status: 200, headers: { ...CORS, "Content-Type": "application/json; charset=utf-8" } });
      const ttl = since ? 20 : 50;    // 全天的查詢要讀幾百個鍵,邊緣快取 50 秒;增量的 20 秒
      const cached = new Response(body, { status: 200, headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": `public, max-age=${ttl}` } });
      if (ctx && ctx.waitUntil) ctx.waitUntil(cache.put(ck, cached)); else await cache.put(ck, cached);
      return resp;
    }

    // ── 元大中繼(tools/yuanta_bridge):POST ?ybars=push 收 1 分 K;GET ?ybars=today|YYYY-MM-DD[&codes=][&since=] 讀(格式同 ?bars)──
    // 2026-10-22 使用者(元大 SPARK API「先寫好 等營業員回覆」):先只收、只讀給比對用,手機還是讀 ?bars;驗證準了再切換
    const yb = url.searchParams.get("ybars");
    if (yb) {
      if (!env || !env.LOGS) return J({ error: "Worker 還沒綁定 KV(LOGS)" }, 503);
      if (yb === "push") {
        if (request.method !== "POST") return J({ error: "要用 POST" }, 405);
        if (!env.YT_PUSH_KEY) return J({ error: "Worker 沒有設定 YT_PUSH_KEY 密鑰" }, 503);
        if (request.headers.get("X-Push-Key") !== env.YT_PUSH_KEY) return J({ error: "金鑰不對" }, 403);
        let b; try { b = await request.json(); } catch (e) { return J({ error: "JSON 格式錯誤" }, 400); }
        if (!b || !/^\d{4}-\d{2}-\d{2}$/.test(b.d || "") || !/^\d{2}:\d{2}$/.test(b.m || "") || typeof b.bars !== "object") return J({ error: "要有 d(YYYY-MM-DD)、m(HH:MM)、bars" }, 400);
        await env.LOGS.put(`ybar:${b.d}:${b.m}`, JSON.stringify(b.bars), { expirationTtl: 14 * 86400 });   // 一分鐘一個鍵(晚到的成交整分鐘重送覆蓋)
        return J({ ok: true, n: Object.keys(b.bars).length });
      }
      const d = yb === "today" ? ymdOf(twNow()) : yb;
      if (!/^\d{4}-\d{2}-\d{2}$/.test(d)) return J({ error: "ybars 要是 push、today 或 YYYY-MM-DD" }, 400);
      const want = (url.searchParams.get("codes") || "").split(",").map(x => x.trim()).filter(Boolean), since = url.searchParams.get("since") || "";
      const keys = []; let cursor;
      do { const r = await env.LOGS.list({ prefix: `ybar:${d}:`, cursor, limit: 1000 }); for (const k of r.keys) keys.push(k.name); cursor = r.list_complete ? null : r.cursor; } while (cursor);
      keys.sort();
      const use = keys.filter(k => !since || k.slice(-5) >= since), bars = {};
      for (let i = 0; i < use.length; i += 25) {
        const part = use.slice(i, i + 25), vals = await Promise.all(part.map(k => env.LOGS.get(k)));
        part.forEach((k, j) => { let o; try { o = JSON.parse(vals[j] || "{}"); } catch (e) { return; }
          for (const c in o) if (!want.length || want.includes(c)) (bars[c] = bars[c] || []).push([k.slice(-5), ...o[c]]); });
      }
      return J({ d, src: "yuanta", last: use.length ? use[use.length - 1].slice(-5) : since, n: use.length, bars });
    }

    // ── ?breadth=today|YYYY-MM-DD → 大盤寬度(每分鐘上漲比例),邊緣快取 50 秒 ──
    const bq = url.searchParams.get("breadth");
    if (bq) {
      if (!env || !env.LOGS) return J({ error: "Worker 還沒綁定 KV(LOGS)" }, 503);
      const d = bq === "today" ? ymdOf(twNow()) : bq;
      if (!/^\d{4}-\d{2}-\d{2}$/.test(d)) return J({ error: "breadth 要是 YYYY-MM-DD 或 today" }, 400);
      const ck = new Request(`${url.origin}/__breadth?d=${d}`), cache = caches.default;
      const hit = await cache.match(ck);
      if (hit) { const h = new Headers(hit.headers); for (const [k, v] of Object.entries(CORS)) h.set(k, v); return new Response(hit.body, { status: 200, headers: h }); }
      const body = JSON.stringify(await dayBreadth(env, d));
      const cached = new Response(body, { status: 200, headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "public, max-age=50" } });
      if (ctx && ctx.waitUntil) ctx.waitUntil(cache.put(ck, cached)); else await cache.put(ck, cached);
      return new Response(body, { status: 200, headers: { ...CORS, "Content-Type": "application/json; charset=utf-8" } });
    }

    // ── 第三條路:?gh=status / ?gh=run&what=daily&key=… ──
    const gh = url.searchParams.get("gh");
    if (gh === "status") {
      const hasToken = !!(env && env.GH_TOKEN), hasKey = !!(env && env.CRON_KEY);
      let github = "未測";
      if (hasToken) {
        try {
          const r = await fetch(`https://api.github.com/repos/${GH_OWNER}/${GH_REPO}/actions/workflows`, {
            headers: { "Authorization": `Bearer ${env.GH_TOKEN}`, "Accept": "application/vnd.github+json", "User-Agent": "mysh-worker" } });
          github = r.status === 200 ? "連線正常(token 有效)" : `GitHub 回 ${r.status}(token 可能沒有 Actions 權限或過期)`;
        } catch (e) { github = "連線失敗 " + e; }
      }
      return J({ GH_TOKEN: hasToken ? "已設定" : "沒有設定", CRON_KEY: hasKey ? "已設定" : "沒有設定(手動觸發關閉)", github,
                 cron: ["5,20,30,45 0,1,6,8,12 * * MON-FRI(一條就好)→ 台灣 08:20/08:45/09:05 複檢,14:30/16:30/20:30 每日資料"],
                 now_taipei: new Date(Date.now() + 8 * 3600e3).toISOString().replace("T", " ").slice(0, 19) });
    }
    if (gh === "run") {
      if (!env || !env.CRON_KEY || url.searchParams.get("key") !== env.CRON_KEY) return J({ error: "key 不對或沒設 CRON_KEY" }, 403);
      return J(await dispatch(env, url.searchParams.get("what") || "daily"));
    }

    // ── 第四條路:?log=add / get / days → 通知紀錄(KV)──
    const lg = url.searchParams.get("log");
    if (lg) {
      if (!env || !env.LOGS) return J({ error: "Worker 還沒綁定 KV(LOGS);deploy-worker.yml 會自動建立,看 Actions 紀錄" }, 503);
      const okDate = d => /^\d{4}-\d{2}-\d{2}$/.test(d || "");
      if (lg === "add") {
        if (request.method !== "POST") return J({ error: "log=add 要用 POST" }, 405);
        let it; try { it = await request.json(); } catch (e) { return J({ error: "body 不是 JSON" }, 400); }
        if (!it || !okDate(it.d) || !/^\d{2}:\d{2}$/.test(it.t || "") || !it.title) return J({ error: "缺 d / t / title" }, 400);
        const item = { t: it.t, title: String(it.title).slice(0, 200), body: String(it.body || "").slice(0, 500), kind: String(it.kind || "").slice(0, 10) };
        if (it.prev != null && isFinite(+it.prev)) item.prev = +it.prev;
        const key = `log:${it.d}:${it.t}:${String(it.key || item.title).replace(/[^\w\u4e00-\u9fff|:.\-]/g, "").slice(0, 120)}`;
        // 2026-10-20 部位鍵去重(使用者:同一筆平倉發三次 → 「改」):手機帶 pos = 判別式|代碼|進場時間,同一個部位的平倉雲端只留第一則(兩支手機、服務重啟都不重複)
        if (it.pos) {
          const mk = `exited:${it.d}:${String(it.pos).replace(/[^\w\u4e00-\u9fff|:.\-]/g, "").slice(0, 80)}`;
          if (await env.LOGS.get(mk)) return J({ ok: true, dup: true, key: mk });
          await env.LOGS.put(mk, item.t, { expirationTtl: 3 * 86400 });
        }
        const meta = JSON.stringify(item);
        // KV 的 list 只回 metadata(上限 1024 bytes),一則通知通常 300~500 bytes 就放 metadata,一次 list 全拿;太長的放 value
        const big = new TextEncoder().encode(meta).length > 1000;
        await env.LOGS.put(key, big ? meta : "", { metadata: big ? { t: item.t, big: 1 } : item, expirationTtl: 90 * 86400 });
        return J({ ok: true, key });
      }
      if (lg === "get") {
        const d = url.searchParams.get("d");
        if (!okDate(d)) return J({ error: "d 要是 YYYY-MM-DD" }, 400);
        const out = []; let cursor;
        do {
          const r = await env.LOGS.list({ prefix: `log:${d}:`, cursor, limit: 1000 });
          for (const k of r.keys) {
            let it = null;
            if (k.metadata && !k.metadata.big) it = k.metadata;
            else { try { const v = await env.LOGS.get(k.name); if (v) it = JSON.parse(v); } catch (e) {} }
            if (it) out.push({ ...it, _k: k.name.slice(15) });   // _k = 鍵的後半(時間:代碼|標題|判別式),對照 / 刪除用
          }
          cursor = r.list_complete ? null : r.cursor;
        } while (cursor);
        out.sort((a, b) => (b.t || "").localeCompare(a.t || ""));   // 新的在前,和手機本機紀錄一樣
        return J(out);
      }
      if (lg === "del") {
        const d = url.searchParams.get("d"), t = url.searchParams.get("t") || "", code = url.searchParams.get("code") || "";
        if (!okDate(d) || !/^\d{2}:\d{2}$/.test(t) || !/^\d{4,6}[A-Z]*$/.test(code)) return J({ error: "要 d=YYYY-MM-DD、t=HH:MM、code=代號" }, 400);
        const r = await env.LOGS.list({ prefix: `log:${d}:${t}:${code}|`, limit: 1000 });
        for (const k of r.keys) await env.LOGS.delete(k.name);
        return J({ ok: true, deleted: r.keys.length });
      }
      if (lg === "days") {
        const days = new Set(); let cursor;
        do {
          const r = await env.LOGS.list({ prefix: "log:", cursor, limit: 1000 });
          for (const k of r.keys) days.add(k.name.slice(4, 14));
          cursor = r.list_complete ? null : r.cursor;
        } while (cursor);
        return J([...days].sort());
      }
      return J({ error: "log 要是 add / get / days" }, 400);
    }

    // ── 第二條路:?yahoo=6770.TW → Yahoo 今天的 1 分K ──
    const ysym = (url.searchParams.get("yahoo") || "").trim().toUpperCase();
    if (ysym) {
      if (!/^\d{4,6}[A-Z]?\.TWO?$/.test(ysym)) {
        return new Response(JSON.stringify({ error: "yahoo 參數格式應為 代號.TW 或 代號.TWO" }), {
          status: 400, headers: { ...CORS, "Content-Type": "application/json" } });
      }
      const range = ["1d", "5d"].includes(url.searchParams.get("range")) ? url.searchParams.get("range") : "1d";
      const ytarget = `https://query1.finance.yahoo.com/v8/finance/chart/${ysym}?interval=1m&range=${range}`;
      try {
        const r = await fetch(ytarget, {
          headers: { "User-Agent": "Mozilla/5.0", "Accept": "application/json" },
          cf: { cacheTtl: 0 },
        });
        const body = await r.text();
        return new Response(body, {
          status: r.status,
          headers: { ...CORS, "Content-Type": "application/json; charset=utf-8" },
        });
      } catch (e) {
        return new Response(JSON.stringify({ error: String(e) }), {
          status: 502, headers: { ...CORS, "Content-Type": "application/json" } });
      }
    }

    const codes = (url.searchParams.get("codes") || "").split(",").map(s => s.trim()).filter(Boolean);
    const mkts = (url.searchParams.get("mkt") || "").split(",").map(s => s.trim());
    if (!codes.length) {
      return new Response(JSON.stringify({ error: "缺少 codes 參數" }), {
        status: 400, headers: { ...CORS, "Content-Type": "application/json" } });
    }

    // 組證交所要的格式:tse_2409.tw|otc_6147.tw|...
    // 2026-10-20 mkt=any:不知道上市上櫃(App 名單裡沒有的股票),兩邊都問,證交所只會回對的那一邊(錯的回空的,misFresh 會丟掉)
    const ex_ch = codes.flatMap((c, i) => (mkts[i] === "any") ? [`tse_${c}.tw`, `otc_${c}.tw`] : [`${(mkts[i] || "tse") === "otc" ? "otc" : "tse"}_${c}.tw`]).join("|");
    try {
      // 2026-10-20:同時發 FRESH_N 個請求、每檔取最新的那筆(見 misFresh)
      const j = await misFresh(ex_ch);
      if (!j) return new Response(JSON.stringify({ error: "證交所接口沒有回應" }), { status: 502, headers: { ...CORS, "Content-Type": "application/json" } });
      return new Response(JSON.stringify(j), {
        status: 200,
        headers: { ...CORS, "Content-Type": "application/json; charset=utf-8" },
      });
    } catch (e) {
      return new Response(JSON.stringify({ error: String(e) }), {
        status: 502, headers: { ...CORS, "Content-Type": "application/json" } });
    }
  },
};
