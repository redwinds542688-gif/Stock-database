import { DurableObject } from "cloudflare:workers";
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
                     → 存 KV 鍵 log:<d>:<t>:<key>(兩支手機發同一則 → 同一個鍵,不會重複);KV 時期保留 90 天,2026-10-25 起存 Durable Object,保留 21 天(2026-10-27 使用者「保存21天 新進一天 最後一天就刪除」)
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
// ── 2026-10-29 16:16 repo 要改私人(使用者「開始 一步一步來」):Worker 用 GH_TOKEN 代讀 GitHub 的檔案(private 也讀得到),自己快取(有 Authorization 的請求 Cloudflare 不會自動快取)──
//   ?gh=raw&path=<檔案>[&ref=<commit>]:X-Push-Key(中繼)/ owner 鑰匙 → 什麼都能讀;viewer / trial 鑰匙 → 只能讀 app/viewer.html 和 app/apk/viewer/*;沒鑰匙 → 403
const GH_MIME = { html: "text/html; charset=utf-8", json: "application/json; charset=utf-8", csv: "text/plain; charset=utf-8", js: "text/javascript; charset=utf-8", apk: "application/vnd.android.package-archive", md: "text/plain; charset=utf-8", txt: "text/plain; charset=utf-8", py: "text/plain; charset=utf-8", png: "image/png" };
const GH_REPO_LOGIC = "Stock-logic";   // 2026-10-29 18:03 判一二三搬到的私人庫(使用者:「只要判別式改成私人」);只有 owner 鑰匙 / 中繼金鑰能讀
async function ghRaw(env, path, ref = GH_REF, ttl = 1800, repo = GH_REPO) {
  path = String(path || "").replace(/^\/+/, "").split("?")[0];
  const u = `https://raw.githubusercontent.com/${GH_OWNER}/${repo}/${ref}/${path}`;
  const cache = caches.default, ck = new Request(`https://twse-proxy.internal/__gh2/${repo}/${ref}/${path}`);   // 2026-11-01 __gh → __gh2:換掉舊的 30 分鐘快取(中繼抓不到新 VERSION)
  if (ttl > 0) { const hit = await cache.match(ck); if (hit) return hit; }
  const h = { "User-Agent": "twse-proxy" }; if (env && env.GH_TOKEN) h.Authorization = "token " + env.GH_TOKEN;
  const r = await fetch(u, { headers: h });
  if (!r.ok) return new Response(await r.text(), { status: r.status, headers: { "Content-Type": "text/plain; charset=utf-8" } });
  const ext = (path.split(".").pop() || "").toLowerCase();
  const res = new Response(r.body, { status: 200, headers: { "Content-Type": GH_MIME[ext] || "application/octet-stream", "Cache-Control": ttl > 0 ? `public, max-age=${ttl}` : "no-store" } });
  if (ttl > 0) { try { await cache.put(ck, res.clone()); } catch (e) {} }
  return res;
}
const MIS_HDR = { "User-Agent": "Mozilla/5.0", "Accept": "application/json", "Referer": "https://mis.twse.com.tw/stock/fibest.jsp" };
const twNow = () => new Date(Date.now() + 8 * 3600e3);
const ymdOf = t => t.toISOString().slice(0, 10);
const hhmmOf = t => t.toISOString().slice(11, 16);
// 2026-11-06 15:44 使用者「美股只是參考 每天8.20 拿到資料之後 在當沖的通知欄裡 顯示 美股四大指數的結果 當沖按鍵 左上角 閃紅點」:
//   台灣 08:20 / 08:30 / 08:45 cron 抓前一個美股交易日的道瓊、S&P 500、那斯達克、費半收盤(Yahoo 圖表 API,抓不到改 FRED),存 Hub「usmkt」;GET ?usmkt=get 給手機
const US_IDX = [["^SOX", "費半", "NASDAQSOX"], ["^TWII", "台股", ""], ["^IXIC", "那斯達克", "NASDAQCOM"], ["^N225", "日經", "NIKKEI225"]];   // 2026-11-06 23:02 使用者「把道瓊刪除 那費城半導體移到原道瓊位置 原費半改日經225」   // 2026-11-06 20:03 使用者「這裡的 s&p500 改成台灣股市」:第二格換台灣加權指數(Yahoo ^TWII;FRED 沒有,抓不到就空著)
async function fetchUS(env) {
  const out = [];
  for (const [sym, name, fred] of US_IDX) {
    let row = null;
    try {
      const r = await fetch(`https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(sym)}?range=10d&interval=1d`, { headers: { "User-Agent": "Mozilla/5.0" } });
      const j = await r.json(); const res = j && j.chart && j.chart.result && j.chart.result[0];
      const ts = (res && res.timestamp) || [], cl = (res && res.indicators && res.indicators.quote && res.indicators.quote[0] && res.indicators.quote[0].close) || [], off = +((res && res.meta && res.meta.gmtoffset) || -18000);
      const v = ts.map((t, i) => [t, cl[i]]).filter(x => x[1] != null);
      { const rp = res && res.meta && res.meta.currentTradingPeriod && res.meta.currentTradingPeriod.regular, nowS = Date.now() / 1000;   // 2026-11-06 23:03 還在交易中的那一天不算(只拿已收盤的):盤中手動 force 抓到半天的數字
        if (rp && nowS < rp.end && v.length && v[v.length - 1][0] >= rp.start - 3600) v.pop(); }
      if (v.length >= 2) { const [t1, c1] = v[v.length - 1], c0 = v[v.length - 2][1]; row = { name, close: +c1.toFixed(2), prev: +c0.toFixed(2), pct: +((c1 / c0 - 1) * 100).toFixed(2), date: new Date((t1 + off) * 1000).toISOString().slice(0, 10), src: "Yahoo" }; }
    } catch (e) {}
    if (row && row.src === "Yahoo") {   // 2026-11-06 16:06 使用者「顯示要有點質感」(附 Yahoo 四格走勢圖):那一天的 5 分 K 收盤當走勢線
      try {
        const r = await fetch(`https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(sym)}?range=5d&interval=5m`, { headers: { "User-Agent": "Mozilla/5.0" } });
        const j = await r.json(); const res = j && j.chart && j.chart.result && j.chart.result[0];
        const c0 = (res && res.indicators && res.indicators.quote && res.indicators.quote[0] && res.indicators.quote[0].close) || [], tt = (res && res.timestamp) || [];
        const off = +((res && res.meta && res.meta.gmtoffset) || -18000);
        const cl = tt.map((t, i) => [new Date((t + off) * 1000).toISOString().slice(0, 10), c0[i]]).filter(x => x[0] === row.date && x[1] != null).map(x => +(+x[1]).toFixed(2));   // 2026-11-06 抓 5 天、只留收盤那一天的 5 分 K(盤中 / 換日也拿得到)
        if (cl.length >= 5) { row.spark = cl.length > 90 ? cl.filter((x, i) => i % Math.ceil(cl.length / 90) === 0 || i === cl.length - 1) : cl; }
      } catch (e) {}
    }
    if (row && row.close != null && row.prev != null) row.chg = +(row.close - row.prev).toFixed(2);
    if (!row && fred) {
      try {
        const t = await (await fetch(`https://fred.stlouisfed.org/graph/fredgraph.csv?id=${fred}`, { headers: { "User-Agent": "Mozilla/5.0" } })).text();
        const v = t.trim().split("\n").slice(1).map(l => l.split(",")).filter(x => x[1] && x[1] !== ".");
        if (v.length >= 2) { const c1 = +v[v.length - 1][1], c0 = +v[v.length - 2][1]; row = { name, close: +c1.toFixed(2), prev: +c0.toFixed(2), pct: +((c1 / c0 - 1) * 100).toFixed(2), date: v[v.length - 1][0], src: "FRED" }; }
      } catch (e) {}
    }
    out.push(row || { name, close: null, pct: null, date: "", src: "" });
  }
  const t = new Date(Date.now() + 8 * 3600e3);
  return { d: t.toISOString().slice(0, 10), at: Date.now(), hm: t.toISOString().slice(11, 16), us: out.filter(x => x.name !== "台股" && x.name !== "日經").map(x => x.date).filter(Boolean).sort().pop() || ""   /* 美股日期只看美國那三個(台股日期是台灣的) */, idx: out };
}
async function usGet(env, force) {
  let v = null; try { v = env.HUB ? await hubOf(env).kvGet("usmkt") : null; } catch (e) {}
  const t = new Date(Date.now() + 8 * 3600e3), d = t.toISOString().slice(0, 10), hm = t.toISOString().slice(11, 16), wd = t.getUTCDay();
  const ok = v && v.d === d && v.idx && v.idx.every(x => x.close != null);
  if (force || (!ok && hm >= "08:20")) {   // 2026-11-06 16:11 週六、週日也抓(手機來問時;星期六 08:20 起顯示到星期一 09:00)
    const c = MC.get("usfetch"); if (!force && c && Date.now() - c < 300e3) return v;   // 手機來問、今天還沒有:最多 5 分鐘抓一次
    MC.set("usfetch", Date.now());
    const n = await fetchUS(env); if (n.idx.some(x => x.close != null)) { v = n; try { if (env.HUB) await hubOf(env).kvPut("usmkt", n); } catch (e) {} }
  }
  return v;
}

async function poolCodes(env) {
  const r = await ghRaw(env, "pool.json");
  if (!r.ok) throw new Error("pool.json " + r.status);
  const j = await r.json(); const pool = Array.isArray(j) ? j : (j.pool || []);
  return pool.map(s => ({ code: String(s.code), mkt: (s.market === "tpex" || s.market === "otc") ? "otc" : "tse" }));
}
/* 2026-10-20 09:40 使用者(富喬 09:21 截圖:「差了快二分鐘」→「改」):證交所接口後面有好幾台伺服器,每台更新時間不同,
   開盤時同一秒發 5 個請求拿到的資料相差 25~60 秒,有時 70 秒後還在回同一筆舊資料(09:26~09:31 實測)。
   每分鐘整點的快照抓到舊資料 → 那分鐘「沒成交」不出 K,下一分鐘兩分鐘併成一根(富喬 09:00~09:23 少了 5 根)。
   改成每次同時發 FRESH_N 個請求,每檔取累計量 v 最大(= 最新)的那筆;sysTime 也取最新。快照與手機即時價都用這條。 */
const FRESH_N = 4;
/* 2026-10-22 使用者(「改 Worker 三項都改」):09:00~10:50 原始快照查出有的快照拖到 30 多秒才拿到資料——原本 Promise.all 要等 4 個請求裡最慢那個。
   改成最多等 MIS_WAIT 毫秒,用已經回來的;那時一個都還沒回來,就等第一個回來的(不會整筆沒資料) */
const MIS_WAIT = 4000;
/* 2026-10-22 11:30 修正後 33 分鐘:每分鐘都只存到 1 筆子快照(第二筆從沒成功)。免費方案一次執行最多同時開 6 條連線,
   沒用到的慢請求一直佔著連線,下一輪的請求要排隊 → 用完就取消(abort)剩下的;同時連線數也壓在 6 條內(見 misFetch) */
const MIS_CONN = 6;
async function misFresh(ex_ch, n = FRESH_N) {
  const url = `https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=${ex_ch}&json=1&delay=0&_=`;
  const rs = [], ac = new AbortController();
  await new Promise(done => {
    let left = n, late = false;
    const timer = setTimeout(() => { if (rs.length) done(); else late = true; }, MIS_WAIT);
    for (let i = 0; i < n; i++)
      fetch(url + (Date.now() + i), { headers: MIS_HDR, cf: { cacheTtl: 0 }, signal: ac.signal }).then(r => r.ok ? r.json() : null).catch(() => null)
        .then(j => { if (j && Array.isArray(j.msgArray)) rs.push(j); left--; if (left === 0 || (late && rs.length)) { clearTimeout(timer); done(); } });
  });
  try { ac.abort(); } catch (e) {}
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
  const out = [], parts = [];
  for (let i = 0; i < list.length; i += 50) parts.push(list.slice(i, i + 50).map(s => `${s.mkt}_${s.code}.tw`).join("|"));
  // 2026-10-22 各批(每批 50 檔)同時抓,不再一批等完才抓下一批
  const per = Math.max(1, Math.min(n || FRESH_N, Math.floor(MIS_CONN / Math.max(1, parts.length))));   // 同時連線 ≤ MIS_CONN
  const js = await Promise.all(parts.map(ex_ch => misFresh(ex_ch, per).catch(e => { console.log("mis " + e); return null; })));
  for (const j of js) for (const m of ((j && j.msgArray) || [])) out.push(m);
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
async function snapshotBars(env, subs, at) {
  if (!env || !env.LOGS) return "no KV";
  // 2026-10-22 使用者(「改 Worker 三項都改」):分鐘以 Cron 排定的時間為準(at = event.scheduledTime);:30、:56 照這一分鐘算,
  //   過了就跳過。原本開頭那筆抓超過整分鐘時會去等「下一分鐘」的 :30、:56(09:00~10:50 友達 78 筆子快照有 27 筆跑到下一分鐘,和下一分鐘的抓取撞在一起)
  const m0 = Math.floor((at || Date.now()) / 60000) * 60000;
  const t = new Date(m0 + 8 * 3600e3); const hm = t.getUTCHours() * 60 + t.getUTCMinutes();
  if (hm < 9 * 60 || hm > 13 * 60 + 35) return "off-hours";
  const list = await poolCodes(env);
  const d = ymdOf(t), ymd = d.replace(/-/g, ""), key = hhmmOf(t);
  const sec = () => Math.round((Date.now() - m0) / 100) / 10;   // 這一分鐘開始後幾秒(查問題用,存在 KV metadata,?barsraw 看得到)
  const tm = { s: sec() }, st = Date.now();
  const r0 = buildSnap(await misFetch(list), ymd);
  if (!r0.dOk) return "holiday";
  tm.e = sec();
  /* 2026-10-23 15:40 手機每分鐘 :01 會先叫 ?bars=snap 存這一分鐘的 bar: 鍵(上一根 K 剛收完就抓,通知快 40 秒)。
     KV 免費方案一天只能寫 1,000 次(Cron 一分鐘 bar + bas 2 次 = 540、狀態 56、通知、元大 ≈ 880),再加 270 次會爆。
     所以 Cron 醒來發現這一分鐘已經有 bar:(手機寫的)就不再寫一次,開頭那筆併進 bas: 和 :07 / :27 的子快照一起寫(還是 1 次)。快照點不少、寫入次數不變。 */
  const had = subs ? await snapHas(env, `bar:${d}:${key}`) : null;
  if (!had) await snapPut(env, `bar:${d}:${key}`, JSON.stringify(r0.s), { expirationTtl: 14 * 86400, metadata: tm });
  if (!subs || hm > 13 * 60 + 30) return `bar ${d} ${key} ${r0.n} 檔`;
  const out = had ? [r0.s] : [];
  const sm = had ? { merged: "bar已有(手機snap),開頭併進bas" } : {};
  /* 2026-10-22 11:40 抓取秒數紀錄:Cron 每分鐘都晚約 47 秒才醒(開頭那筆在 :47~:51 抓),:30 永遠跳過,只剩 :48、:56 兩筆擠在一起、後面空 50 秒。
     K 是照證交所資料時間 t 歸分鐘,快照屬於哪個鍵不影響,所以改成從「這次醒來」起平均間隔:醒來 +20 秒、+40 秒各抓一次
     (下一次 Cron 約 60 秒後才醒,不會撞在一起)。key 仍是排定的那一分鐘,拼 K 時照時分排序,時間順序不變 */
  /* 2026-10-23 15:45 使用者(「這一個要如何改最穩當」→「好」):子快照改成對準「下一分鐘 :02 和 :30」(不再是醒來 +20 / +40 秒):
     上一根 K 在 :00 收完,:02 那筆就是它完整的快照,手機 10 秒內拿到 → 通知比原本快約 40 秒;不用手機叫 Worker(15:15 那版,已關),
     KV 寫入、證交所抓取次數都和以前一樣(一分鐘 bar + bas 2 次寫入、3 次抓取)。醒來太晚(離 :02 不到 1 秒)就照舊跳過。 */
  const nextMin = Math.floor(st / 60000) * 60000 + 60000;
  const plan = [[nextMin + 2000, "02"], [nextMin + 30000, "30"]];
  for (const [tAbs, lab] of plan) {
    const n = FRESH_N, at2 = lab, wait = tAbs - Date.now();
    if (wait < 1000) { sm["s" + at2] = "skip@" + sec(); continue; }
    if (wait > 70000) { sm["s" + at2] = "too-far@" + sec(); continue; }
    await new Promise(r => setTimeout(r, wait));
    const b = sec();
    try { const r = buildSnap(await misFetch(list, n), ymd); if (r.n) out.push(r.s); sm["s" + at2] = b + "-" + sec() + (r.n ? "" : " 0檔"); } catch (e) { sm["s" + at2] = "err " + e; console.log("子快照失敗 " + e); }
  }
  if (out.length) await snapPut(env, `bas:${d}:${key}`, JSON.stringify(out), { expirationTtl: 14 * 86400, metadata: sm });
  return `bar ${d} ${key} ${r0.n} 檔 + 子快照 ${out.length}`;
}
/* 2026-10-22 12:05 error 1101:KV 免費方案一天只能 list 1,000 次,兩支手機每 55 秒問 ?bars(list 2 次)+ ?breadth(list 1 次)中午就用完,
   所有讀 KV 的路線都掛到隔天 08:00。快照鍵名是固定的(每分鐘一個 bar: / bas:),不再 list,直接照時分產生鍵名去 get(get 一天 100,000 次) */
const hm2m = hm => +hm.slice(0, 2) * 60 + +hm.slice(3, 5);
const m2hm = m => String(Math.floor(m / 60)).padStart(2, "0") + ":" + String(m % 60).padStart(2, "0");
function minuteKeys(d, pre, from) {
  const now = twNow(), end = d === ymdOf(now) ? Math.min(hm2m(hhmmOf(now)), 13 * 60 + 35) : 13 * 60 + 35;
  const out = [];
  for (let m = Math.max(hm2m(from || "09:00"), 9 * 60); m <= end; m++) out.push(`${pre}:${d}:${m2hm(m)}`);
  return out;
}
// 2026-11-02 快照 SNAP_SINCE 起存 Hub(不吃 KV 額度);之前的日子照舊讀 KV
const SNAP_SINCE = "2026-11-02";
const snapDay = k => k.slice(4, 14);
async function kvGetMany(env, keys) {
  const vals = new Array(keys.length).fill(null), hk = [], hi = [], kk = [], ki = [];
  keys.forEach((k, i) => { if (env.HUB && snapDay(k) >= SNAP_SINCE) { hk.push(k); hi.push(i); } else { kk.push(k); ki.push(i); } });
  if (hk.length) { const v = await hubOf(env).snapGet(hk); v.forEach((x, j) => { vals[hi[j]] = x; }); }
  for (let i = 0; i < kk.length; i += 25) { const v = await Promise.all(kk.slice(i, i + 25).map(k => env.LOGS.get(k).catch(() => null))); v.forEach((x, j) => { vals[ki[i + j]] = x; }); }
  return vals;
}
async function snapPut(env, k, v, opt) { if (env.HUB && snapDay(k) >= SNAP_SINCE) return hubOf(env).snapPut(k, v); return env.LOGS.put(k, v, opt); }
async function snapHas(env, k) { if (env.HUB && snapDay(k) >= SNAP_SINCE) return hubOf(env).snapHas(k); return !!(await env.LOGS.get(k).catch(() => null)); }
/** 把一天的快照拼成每檔的 1 分K
 * 2026-10-22 19:26 使用者「改」(進 K 圖第一次要 8 秒):拼 K 的步驟拆成 feedBars(照順序吃快照、狀態留在 st),
 *   整天的查詢改走 dayBarsFull:已收完的分鐘拼好的狀態存 Cloudflare 邊緣快取(不算 KV 次數),下次只讀新的幾分鐘再接著拼。結果和整天重讀一樣 */
const addM = (hm, k) => m2hm(hm2m(hm) + k);
const sortKeys = keys => keys.sort((a, b) => a.slice(-5) < b.slice(-5) ? -1 : a.slice(-5) > b.slice(-5) ? 1 : (a < b ? -1 : 1));   // 同一分鐘 bar(開頭快照)在 bas(:30、:56 子快照)前面
// 2026-10-19 收盤後對照 Yahoo 整天 K 發現:HH:MM 的快照拿到的是「上一分鐘」的成交(12:01:0x 的快照 = 12:00 那一分鐘),
// 標籤要往前移一分鐘才和 Yahoo / 手機自拼的 K(都以成交那一分鐘的開頭當標籤)一致;09:00 的第一個快照(開盤撮合)仍標 09:00
// 2026-10-21 23:30 有資料時間 t 的快照改照 t 歸分鐘(見 buildSnap);沒有 t 的舊快照照舊
const barLbl = (hm, rec, useT) => { const t = useT && rec && rec[3]; if (t && t.length >= 5) { const m = t.slice(0, 5); return m < "09:00" ? "09:00" : m > "13:30" ? "13:30" : m; } return hm <= "09:00" ? "09:00" : addM(hm, -1); };
const barsState = () => ({ upto: "", kv: "", n: 0, sub: [], prev: {}, bars: {} });
/** 照時分順序吃快照(keys 已排好、vals 對應),st = {n, sub(有子快照的分鐘), prev, bars};want = 只拼哪些檔(null = 全部) */
function feedBars(st, keys, vals, since, want) {
  // 開頭快照只有在前一分鐘有子快照時才照 t 歸分鐘;前一分鐘沒有(舊資料、子快照失敗)= 這筆其實是上一分鐘的收盤,照舊規則標前一分鐘
  // (bas:前一分鐘 排在 bar:這一分鐘 前面,所以邊吃邊記就好)
  const hasSub = new Set(st.sub), bars = st.bars, prev = st.prev;
  keys.forEach((k, j) => {
    if (!vals[j]) return; let v; try { v = JSON.parse(vals[j]); } catch (e) { return; }
    const hm0 = k.slice(-5), obs = [];
    if (k.startsWith("bas:")) { hasSub.add(hm0); for (const sub of (Array.isArray(v) ? v : [])) obs.push([sub, true]); }
    else { st.n++; obs.push([v, hasSub.has(addM(hm0, -1))]); }
    for (const [s, useT] of obs) {
      for (const c in s) {
        if (want && !want.has(c)) continue;
        const rec = s[c], z = rec[0], v = rec[1], p = prev[c], hm = barLbl(hm0, rec, useT);
        // 2026-10-21 使用者(台郡「量不對」):證交所偶爾回舊快照(時間倒退、累計量變少),原本 prev 被拉回舊的量,下一分鐘又把同一段量算一次
        //   (09:54 時 130 檔有 45 檔雲端 K 總量比證交所多 1% 以上,台郡多 1,573 張)。累計量比上一個快照少 → 這個快照整個不算
        if (p && v < p[1]) continue;
        // 2026-10-22 使用者(「改 Worker 三項都改」):累計量一樣、但資料時間比上一筆還舊 = 舊伺服器回的舊快照,也不算(之前會用舊價夾買賣價拼出零量的 K)
        const tt = rec[3] || "";
        if (p && tt && p[2] && tt < p[2]) continue;
        prev[c] = [z, v, tt];
        if (!p) { if (!since) bars[c] = [[hm, z, z, z, z, hm0 <= "09:02" ? Math.round(v * 1000) : 0]]; continue; }   // 當天第一個快照:09:00 開始存的話量 = 開盤撮合的累計;盤中才開始存(例如 2026-10-19 11:57 上線)就給 0,
                                                                                                                         // 不然整個上午的累計量會灌進那一根,OBV 一次跳好幾千張(使用者截圖:燿華 12:05 跳 3,700 張)。那段的量由手機用 Yahoo 補
        const dv = Math.max(0, v - p[1]) * 1000;
        if (dv <= 0 && z === p[0]) continue;                   // 這段沒成交
        const arr = (bars[c] = bars[c] || []), lb = arr[arr.length - 1];
        if (lb && lb[0] >= hm) { lb[2] = Math.max(lb[2], z); lb[3] = Math.min(lb[3], z); lb[4] = z; lb[5] += Math.round(dv); continue; }   // 同一分鐘(或資料時間比上一根還早)併進上一根
        arr.push([hm, p[0], Math.max(p[0], z), Math.min(p[0], z), z, Math.round(dv)]);
      }
    }
  });
  st.sub = [...hasSub].filter(m => m >= "09:00").slice(-3);   // 只會查前一分鐘,留最後幾個就好
}
/** 今天還沒走完的這一分鐘不給(訊號只看收完的 K,和以前一樣);增量查詢只回 since 起的 */
function barsOut(d, bars, n, since, want) {
  const cut = d === ymdOf(twNow()) ? hhmmOf(twNow()) : "99:99", out = {};
  let last = "";
  for (const c in bars) {
    if (want && !want.has(c)) continue;
    const a = bars[c].filter(b => b[0] < cut && (!since || b[0] >= since));
    if (!a.length) continue;
    out[c] = a; const lb = a[a.length - 1][0]; if (lb > last) last = lb;
  }
  return { d, last: last || since || "", n, bars: out };   // last = 最後一根的標籤(手機下次用 since 傳回來)
}
async function dayBars(env, d, codes, since) {
  const from = since ? addM(since, -2) : "09:00";             // 增量:多拿前 2 分鐘當基準(和判斷前一分鐘有沒有子快照)
  const keys = sortKeys([...minuteKeys(d, "bar", from), ...minuteKeys(d, "bas", from)]);
  // 增量:從 since 前 2 分鐘的快照開始(當基準,不出那根),回傳 since 起的 K(since 那根可能還沒拼完,整根重給)
  const vals = await kvGetMany(env, keys);
  const want = codes && codes.length ? new Set(codes) : null, st = barsState();
  feedBars(st, keys, vals, since, want);
  return barsOut(d, st.bars, st.n, since, want);
}
/** 2026-10-22 19:45 使用者(「第一次進去還是那麼久」):大盤寬度也是整天一分鐘一分鐘讀,手機要等它才畫 K 圖 → 寬度也一起放進同一份狀態
 *  寬度只看 bar: 鍵;st.bl = 每檔最後一筆 [z, v, y],st.br = 每分鐘上漲比例(和 dayBreadth 同一個算法) */
const brLbl = hm => hm <= "09:00" ? "09:00" : addM(hm, -1);
async function poolPrev(d) {
  const pp = {};
  try {
    const r = await ghRaw(env, "pool.json");
    const j = await r.json(), pool = Array.isArray(j) ? j : (j.pool || []);
    for (const s of pool) { const y = (s.date || j.date) === d ? +s.prev : (s.date || j.date) < d ? +s.close : NaN; if (y > 0) pp[String(s.code)] = y; }
  } catch (e) {}
  return pp;
}
function feedBreadth(st, keys, vals, pp) {
  const last = st.bl, br = st.br, av = st.av || (st.av = {});   // 2026-11-05 判五:av = 名單上每檔對昨收漲跌 % 的平均(同一批股票、同一個標籤)
  keys.forEach((k, j) => {
    if (!k.startsWith("bar:") || !vals[j]) return; let s; try { s = JSON.parse(vals[j]); } catch (e) { return; }
    for (const c in s) {
      const [z, v, y] = s[c], p = last[c];
      if (p && v < p[1]) continue;                          // 舊快照(累計量變少)不算,同 dayBars
      last[c] = [z, v, y > 0 ? y : (p && p[2]) || pp[c]];
    }
    let u = 0, dn = 0;
    for (const c in last) { const [z, , y] = last[c]; if (!(y > 0)) continue; if (z > y) u++; else if (z < y) dn++; }
    if (u + dn) br[brLbl(k.slice(-5))] = Math.round(u / (u + dn) * 1000) / 1000;
    let sp = 0, np = 0; for (const c in last) { const [z, , y] = last[c]; if (y > 0 && z > 0){ sp += (z / y - 1) * 100; np++; } }
    if (np) av[brLbl(k.slice(-5))] = Math.round(sp / np * 1000) / 1000;
  });
}
/** 整天的查詢(雲端 K、大盤寬度共用):收完的分鐘(現在往前 2 分鐘以前,子快照 :56 也寫好了)拼好的狀態存邊緣快取 + 每 5 分鐘一份 KV,下次只讀之後的鍵 */
async function dayStateFull(env, ctx, origin, d) {
  const today = d === ymdOf(twNow());
  const safe = today ? m2hm(Math.min(hm2m(hhmmOf(twNow())) - 2, 13 * 60 + 35)) : "13:35";
  const sk = new Request(`${origin}/__barsst?d=${d}&v=3`), cache = caches.default, kk = `bst3:${d}`;   // 2026-11-05 v=3 / bst3:多存 av
  const ok = x => x && x.prev && x.bl && x.br && x.av;   // 2026-11-05 加 av(判五名單平均),舊版存檔沒有 av 的重算
  let st = null;
  try { const h = await cache.match(sk); if (h) st = await h.json(); } catch (e) { st = null; }
  // 2026-10-22 19:33 使用者「改」(每天第一次進 K 圖還要 6 秒):邊緣快取沒有(每天第一次、換機房)→ 讀 KV 存的那份(每 5 分鐘存一次),只補之後的幾分鐘
  if (!ok(st)) { try { const v = d >= SNAP_SINCE && env.HUB ? await hubOf(env).kvGet("m:" + kk) : await env.LOGS.get(kk); st = v ? JSON.parse(v) : null; } catch (e) { st = null; } }
  if (!ok(st)) st = { ...barsState(), bl: {}, br: {}, av: {} };
  const from = st.upto ? addM(st.upto, 1) : "09:00";
  const all = sortKeys([...minuteKeys(d, "bar", from), ...minuteKeys(d, "bas", from)]);
  if (!all.length) return st;
  const done = all.filter(k => k.slice(-5) <= safe), tail = all.filter(k => k.slice(-5) > safe);
  const [vals, pp] = await Promise.all([kvGetMany(env, all), poolPrev(d)]);
  if (done.length) {
    const dv = vals.slice(0, done.length);
    feedBars(st, done, dv, "", null); feedBreadth(st, done, dv, pp);
    st.upto = safe;
    // KV 一份:跨過 5 分鐘整點(或那天收完)才寫,一天約 56 次(KV 免費方案一天寫 1,000 次;19:37 使用者「5分」,原 10 分鐘)
    let toKv = Math.floor(hm2m(safe) / 5) > Math.floor(hm2m(st.kv || "00:00") / 5) || (safe === "13:35" && st.kv !== "13:35");
    // 2026-10-29 安全修補:?bars / ?breadth 不用鑰匙,外人可以亂帶日期(未來 / 很久以前)讓每個日期都寫一次 KV → 只有今天往前 14 天內(分K 保留 14 天)才寫
    if (toKv && (d > ymdOf(twNow()) || d < ymdOf(new Date(twNow().getTime() - 14 * 86400e3)))) toKv = false;
    if (toKv) st.kv = safe;
    const body = JSON.stringify(st);
    const put = cache.put(sk, new Response(body, { headers: { "Content-Type": "application/json", "Cache-Control": `public, max-age=${today ? 86400 : 7 * 86400}` } }));
    const kvp = toKv ? (d >= SNAP_SINCE && env.HUB ? hubOf(env).kvPut("m:" + kk, body) : env.LOGS.put(kk, body, { expirationTtl: 3 * 86400 })) : null;
    if (ctx && ctx.waitUntil) { ctx.waitUntil(put); if (kvp) ctx.waitUntil(kvp); } else { await put; if (kvp) await kvp; }
  }
  if (tail.length) { const tv = vals.slice(done.length); feedBars(st, tail, tv, "", null); feedBreadth(st, tail, tv, pp); }   // 還沒收完的幾分鐘只拿來回這一次,不存
  return st;
}
async function dayBarsFull(env, ctx, origin, d, codes) {
  const st = await dayStateFull(env, ctx, origin, d);
  return barsOut(d, st.bars, st.n, "", codes && codes.length ? new Set(codes) : null);
}
async function dayBreadthFull(env, ctx, origin, d) {
  const st = await dayStateFull(env, ctx, origin, d);
  return { d, n: Object.keys(st.bl).length, br: st.br, av: st.av || {} };
}

/* 2026-10-21 使用者「改 判二 大盤偏多只做黑盤空」:大盤寬度 = 名單上每檔最新價 > 昨收的比例(上漲 /(上漲 + 下跌),平盤不算),每分鐘一個值。
   標籤規則和 dayBars 一樣(快照 HH:MM = 上一分鐘的成交);昨收用快照裡的 y,舊快照沒有 y 的用 pool.json(當天的 prev,收盤後換成明天名單時用 close)。
   模擬器 BREADTH 檔(tools/backtest/results/breadth_*.json)是同一個算法 */
async function dayBreadth(env, d, since) {
  // 2026-10-22 12:05 不再 list(見 minuteKeys);since = 增量,只回 since 起的寬度(多讀前 3 分鐘當基準),手機每 55 秒只讀幾個鍵,不用整天重讀 270 個
  const keys = minuteKeys(d, "bar", since ? m2hm(hm2m(since) - 3) : "09:00");
  let pp = {};
  try {
    const r = await ghRaw(env, "pool.json");
    const j = await r.json(), pool = Array.isArray(j) ? j : (j.pool || []);
    for (const s of pool) { const y = (s.date || j.date) === d ? +s.prev : (s.date || j.date) < d ? +s.close : NaN; if (y > 0) pp[String(s.code)] = y; }
  } catch (e) {}
  const lbl = hm => { if (hm <= "09:00") return "09:00"; const m = +hm.slice(0, 2) * 60 + +hm.slice(3, 5) - 1; return String(Math.floor(m / 60)).padStart(2, "0") + ":" + String(m % 60).padStart(2, "0"); };
  const last = {}, br = {}, av = {};
  {
    const part = keys, vals = await kvGetMany(env, keys);
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
      let sp = 0, np = 0; for (const c in last) { const [z, , y] = last[c]; if (y > 0 && z > 0){ sp += (z / y - 1) * 100; np++; } }
      if (np) av[lbl(k.slice(-5))] = Math.round(sp / np * 1000) / 1000;
    });
  }
  if (since) for (const m of [br, av]) for (const k of Object.keys(m)) if (k < since) delete m[k];
  return { d, n: Object.keys(last).length, br, av };
}

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "*",
  "Access-Control-Expose-Headers": "X-Free-End, X-Free-Stage, X-Free-Day",   // 2026-11-01 加 X-Free-Stage(results = 第 8~30 天只看平倉結果)、X-Free-Day(第幾天)   // 2026-10-30 免費 7 天到期的標記,手機(file:// 跨來源)要讀得到
  "Cache-Control": "no-store",
};

/* 2026-10-22 使用者「先把額度問題改好」:KV 免費方案一天只能寫 1,000 次、list 1,000 次(雲端 K 已寫約 880 次)。
   兩件高頻的事搬到 Durable Object「Hub」(SQLite;免費方案一天 10 萬次請求、10 萬列寫入,而且讀到的一定是最新的):
   (1) 今天的通知紀錄 ?log=add / get / del:手機每分鐘、自動交易每 3 秒讀一次,原本每次都 KV list
   (2) 元大 1 分 K ?ybars=push / 讀:一分鐘一筆
   DO_SINCE 起的日子只存 Hub;之前的日子照舊讀 KV(手機看舊紀錄用)。雲端 K 快照、寬度、其他都不變。 */
const DO_SINCE = "2026-10-25";
export class Hub extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.sql = ctx.storage.sql;
    this.sql.exec("CREATE TABLE IF NOT EXISTS logs (d TEXT, k TEXT, t TEXT, item TEXT, PRIMARY KEY (d, k))");
    this.sql.exec("CREATE TABLE IF NOT EXISTS exited (d TEXT, pos TEXT, PRIMARY KEY (d, pos))");
    this.sql.exec("CREATE TABLE IF NOT EXISTS ybars (d TEXT, m TEXT, bars TEXT, PRIMARY KEY (d, m))");
    this.sql.exec("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT)");   // 2026-10-29 小東西(當沖額度)
    this.sql.exec("CREATE TABLE IF NOT EXISTS snaps (k TEXT PRIMARY KEY, v TEXT)");   // 2026-11-02 雲端 K 快照 bar: / bas:(原本 KV,KV 每日讀取額度被它用完)
  }
  kvPut(k, v) { this.sql.exec("INSERT OR REPLACE INTO kv (k, v) VALUES (?, ?)", k, JSON.stringify(v)); return { ok: true }; }
  kvGet(k) { const r = this.sql.exec("SELECT v FROM kv WHERE k = ?", k).toArray(); return r.length ? JSON.parse(r[0].v) : null; }
  bugList() { return this.sql.exec("SELECT k, length(v) AS n FROM kv WHERE k LIKE 'bug:%' ORDER BY k").toArray().map(r => ({ k: r.k, size: r.n })); }   // 2026-11-03 抓 bug 紀錄改存 Hub
  bugTrim(keep) { const r = this.sql.exec("SELECT k FROM kv WHERE k LIKE 'bug:%' ORDER BY k DESC").toArray(); for (const x of r.slice(keep)) this.sql.exec("DELETE FROM kv WHERE k = ?", x.k); return { ok: true }; }
  kvSetIfAbsent(k, v) { const o = this.kvGet(k); if (o != null) return o; this.kvPut(k, v); return v; }   // 2026-11-01 VAPID 金鑰只產生一次(兩個請求同時產生也只留第一把)
  // 2026-11-01 iPhone 網頁版 App 的推播訂閱(使用者「先做A」):[{id, endpoint, k, dv, made}]
  pushSubPut(rec) { const a = (this.kvGet("pushsubs") || []).filter(x => x.id !== rec.id); a.push(rec); this.kvPut("pushsubs", a.slice(-500)); return { ok: true, n: a.length }; }
  pushSubDel(id) { const a = (this.kvGet("pushsubs") || []).filter(x => x.id !== id); this.kvPut("pushsubs", a); return { ok: true }; }
  pushSubs() { return this.kvGet("pushsubs") || []; }
  // 2026-10-29 安全修補:?keys=pick 取件碼嘗試次數(同步執行,一次一個請求,不會被並發繞過)。每 IP 10 分鐘 10 次、全體 10 分鐘 60 次
  pickGate(ip, now) {
    const W = 600e3, st = this.kvGet("pickrl") || {}, o = {};
    for (const x in st) if (Array.isArray(st[x]) && now - st[x][1] < W) o[x] = st[x];   // 丟掉 10 分鐘前的
    const ipk = "ip:" + String(ip).slice(0, 64), g = o["*"] || [0, now], c = o[ipk] || [0, now];
    if (g[0] >= 60 || c[0] >= 10) return { ok: false };
    o["*"] = [g[0] + 1, g[1]]; o[ipk] = [c[0] + 1, c[1]];
    this.kvPut("pickrl", o);
    return { ok: true };
  }
  logAdd(d, k, item, pos) {
    if (pos) {                                                   // 同一個部位的平倉只留第一則(原本 KV 的 exited: 鍵)
      if (this.sql.exec("SELECT 1 FROM exited WHERE d = ? AND pos = ?", d, pos).toArray().length) return { ok: true, dup: true };
      this.sql.exec("INSERT INTO exited (d, pos) VALUES (?, ?)", d, pos);
    }
    this.sql.exec("INSERT OR REPLACE INTO logs (d, k, t, item) VALUES (?, ?, ?, ?)", d, k, item.t, JSON.stringify(item));
    return { ok: true, key: k };
  }
  logGet(d) {
    return this.sql.exec("SELECT k, item FROM logs WHERE d = ? ORDER BY t DESC", d).toArray().map(r => ({ ...JSON.parse(r.item), _k: r.k.slice(15) }));
  }
  logDel(d, prefix) {
    const n = this.sql.exec("SELECT COUNT(*) AS n FROM logs WHERE d = ? AND substr(k, 1, ?) = ?", d, prefix.length, prefix).one().n;
    this.sql.exec("DELETE FROM logs WHERE d = ? AND substr(k, 1, ?) = ?", d, prefix.length, prefix);
    return n;
  }
  logImport(d, rows) {   // 2026-10-27 21:45 KV 舊日子搬進 Hub(一次)
    for (const [k, item] of rows) this.sql.exec("INSERT OR IGNORE INTO logs (d, k, t, item) VALUES (?, ?, ?, ?)", d, k, item.t || "", JSON.stringify(item));
    return rows.length;
  }
  logDays() {
    return this.sql.exec("SELECT DISTINCT d FROM logs").toArray().map(r => r.d);
  }
  ybarPut(d, m, bars, merge) {
    // 2026-10-25 使用者「新進榜的股票 要回溯五天抓k」:桌機補抓新進榜股票的前 5 天,推上來時 merge = 1 → 和那一分鐘原本的股票合併,不覆蓋別檔
    if (merge) { const old = this.sql.exec("SELECT bars FROM ybars WHERE d = ? AND m = ?", d, m).toArray(); if (old.length) { try { bars = Object.assign(JSON.parse(old[0].bars || "{}"), bars); } catch (e) {} } }
    this.sql.exec("INSERT OR REPLACE INTO ybars (d, m, bars) VALUES (?, ?, ?)", d, m, JSON.stringify(bars));
    if (m === "09:00" && !merge) {                                         // 一天清一次:K 留 14 天;通知留 21 天(2026-10-27 使用者「這裡的訊息一樣保存21天 新進一天 最後一天就刪除」;之前 90 天 → 06:15 永久 → 06:45 定 21 天)
      const old = (n) => new Date(Date.parse(d + "T00:00:00Z") - n * 86400e3).toISOString().slice(0, 10);
      this.sql.exec("DELETE FROM ybars WHERE d < ?", old(14));
      this.sql.exec("DELETE FROM logs WHERE d < ?", old(21));
      this.sql.exec("DELETE FROM exited WHERE d < ?", old(3));
    }
    return { ok: true };
  }
  // 2026-11-02 11:22 使用者「改」(KV 讀取額度用完 → 雲端 K、大盤寬度的快照搬到 Hub):一次讀一整串鍵(手機每 20 秒問一次只算 1 個 Hub 請求)
  snapPut(k, v) { this.sql.exec("INSERT OR REPLACE INTO snaps (k, v) VALUES (?, ?)", k, v);
    if (k.startsWith("bar:") && k.endsWith(":09:00")) this.sql.exec("DELETE FROM snaps WHERE substr(k, 5, 10) < ?", new Date(Date.parse(k.slice(4, 14) + "T00:00:00Z") - 14 * 86400e3).toISOString().slice(0, 10));   // 留 14 天
    return true; }
  kvInc(k, d) { const o = this.kvGet(k) || {}; for (const x in d) o[x] = (o[x] || 0) + (d[x] || 0); this.kvPut(k, o); return o; }
  badLog(rec) { const a = (this.kvGet("badlog") || []); a.push(rec); this.kvPut("badlog", a.slice(-40)); return true; }
  snapHas(k) { return this.sql.exec("SELECT 1 FROM snaps WHERE k = ?", k).toArray().length > 0; }
  snapGet(keys) {
    const m = new Map();
    for (let i = 0; i < keys.length; i += 90) { const part = keys.slice(i, i + 90);
      for (const r of this.sql.exec(`SELECT k, v FROM snaps WHERE k IN (${part.map(() => "?").join(",")})`, ...part).toArray()) m.set(r.k, r.v); }
    return keys.map(k => m.has(k) ? m.get(k) : null);
  }
  ybarDays() { return this.sql.exec("SELECT DISTINCT d FROM ybars ORDER BY d").toArray().map(r => r.d); }
  ybarGet(d, since) {
    return this.sql.exec("SELECT m, bars FROM ybars WHERE d = ? AND m >= ? ORDER BY m", d, since || "").toArray();
  }
}
const hubOf = env => env.HUB.get(env.HUB.idFromName("main"));

// ── 2026-10-29 14:50 鑰匙(使用者「如果 程式給別人用 但我要控制判123在手上」→「先做1.2.3」第 3 項):每個使用者一把,可關 ──
//   KV key:<鑰匙> = {name, role: owner|viewer, on, made};keys:owners = "1" 表示已經有 owner 鑰匙(從此 ?log=add / ?log=del / ?trade=* 一律要 owner 鑰匙或中繼的 X-Push-Key)。
//   讀端點:帶 k 就驗(無效 / 停用 → 403 badKey);沒帶照舊可讀(環境變數 KEYS_REQUIRED=1 時沒帶也擋,給以後全面上鎖用)。
//   管理:?keys=list(GET)、?keys=add(POST {name, role})、?keys=off|on|del&k2=<鑰匙>(POST),要 X-Push-Key(中繼的 win_keys.py)或 owner 鑰匙。
//   額度:每個帶鑰匙的請求 1 次 KV 讀(邊緣快取 300 秒);list 只在管理時用。
const KEY_RE = /^[a-f0-9]{16,40}$/;
// ── 2026-11-01 iPhone 網頁版 App 推播(使用者「這程式蘋果手機可以使用嗎」→「先做A」)──
//   VAPID 金鑰第一次用到時在 Worker 裡產生、存 Hub(不用另外設密鑰);推播不帶內容(不用加密),手機的 sw.js 收到後問 ?push=last 拿內容。
//   誰收得到什麼和讀通知(log=get)同一套:owner 全部;付費看盤 / 免費前 7 天只有判別式的進出場(看不到元大真實買賣、電腦提醒);免費第 8 天起、鑰匙到期 / 停用 → 不推
const b64u = buf => btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const b64uStr = t => b64u(new TextEncoder().encode(t));
const PUSH_KINDS = new Set(["long", "short", "loss", "profit", "exit"]);
const REAL_RE = /·\s*元大中繼\s*$|·\s*(強三|元三)\s*$|·\s*強平\s*·/;
async function vapidKeys(env) {
  const hub = hubOf(env); let v = await hub.kvGet("vapid");
  if (!v) { const kp = await crypto.subtle.generateKey({ name: "ECDSA", namedCurve: "P-256" }, true, ["sign", "verify"]);
    v = await hub.kvSetIfAbsent("vapid", { pub: b64u(await crypto.subtle.exportKey("raw", kp.publicKey)), jwk: await crypto.subtle.exportKey("jwk", kp.privateKey) }); }
  return v;
}
async function vapidAuth(env, endpoint) {
  const v = await vapidKeys(env), aud = new URL(endpoint).origin;
  const key = await crypto.subtle.importKey("jwk", { ...v.jwk, key_ops: ["sign"] }, { name: "ECDSA", namedCurve: "P-256" }, false, ["sign"]);
  const unsigned = b64uStr(JSON.stringify({ typ: "JWT", alg: "ES256" })) + "." + b64uStr(JSON.stringify({ aud, exp: Math.floor(Date.now() / 1000) + 12 * 3600, sub: "https://twse-proxy.redwinds542688.workers.dev" }));
  const sig = await crypto.subtle.sign({ name: "ECDSA", hash: "SHA-256" }, key, new TextEncoder().encode(unsigned));
  return `vapid t=${unsigned}.${b64u(sig)}, k=${v.pub}`;
}
async function pushTier(env, s) {
  if (s.k) { const KI = await keyInfo(env, s.k); if (!KI || !KI.on || (KI.exp && ymdOf(twNow()) > KI.exp)) return ""; return KI.role === "owner" ? "owner" : "viewer"; }
  if (s.dv) { let first = null; try { first = await hubOf(env).kvGet("free:" + s.dv); } catch (e) {} return !first || Date.now() - +first <= 7 * 86400e3 ? "free" : ""; }
  return "";
}
const pushCanSee = (tier, x) => !!tier && !!x && (tier === "owner" ? (x.kind !== "sys" && x.kind !== "warn")
  : PUSH_KINDS.has(x.kind) && !REAL_RE.test(String(x.body || "")) && !String(x.key || x._k || "").startsWith("yt|"));
async function pushFanout(env, item) {
  let subs = []; try { subs = await hubOf(env).pushSubs(); } catch (e) { return; }
  for (const s of subs.slice(0, 40)) {   // 一則訊號最多推 40 支(免費方案一次最多 50 個子請求)
    try { const tier = await pushTier(env, s); if (!pushCanSee(tier, item)) continue;
      const r = await fetch(s.endpoint, { method: "POST", headers: { Authorization: await vapidAuth(env, s.endpoint), TTL: "3600", Urgency: "high" } });
      if (r.status === 404 || r.status === 410) await hubOf(env).pushSubDel(s.id); } catch (e) {}
  }
}
const PWA_FILES = { "/app": "app/viewer.html", "/app/": "app/viewer.html", "/pool.js": "pool.js", "/sw.js": "app/pwa/sw.js", "/manifest.webmanifest": "app/pwa/manifest.webmanifest",
  "/icon-180.png": "app/pwa/icon-180.png", "/icon-192.png": "app/pwa/icon-192.png", "/icon-512.png": "app/pwa/icon-512.png" };

// 2026-11-02 11:10 KV 每日讀取額度(10 萬次)用完 → 所有鑰匙被判無效、手機問不到電腦(使用者「怎麼一直出現這個輸入金鑰的畫面」「趕快接通元大」)。
//   1) 常讀的值(鑰匙、通道網址、主導機設定)在這個執行個體記憶體裡留幾分鐘,不再每個請求都讀 KV(手機報價每幾秒一次,這是用完額度的主因)
//   2) 讀到就備份到 Hub(Durable Object,和 KV 額度分開);KV 讀不到時改讀 Hub 的備份
//   3) 還是沒有(額度用完前沒備份到的鑰匙):用 KV list(另一個額度)確認鑰匙存在;使用者的手機鑰匙(含 5861 或結尾在 EMER_OWN)當 owner,其他當 viewer;不寫回 KV
const MC = globalThis.__mc || (globalThis.__mc = new Map());
const EMER_OWN = ["d2a2"];
const EMER_QURL = "https://refined-civilian-ones-prozac.trycloudflare.com";   // 2026-11-02 08:36 電腦登記的通道(KV 讀不到時用;電腦重開會重新登記)
async function kvGetC(env, key, ttlMs) {
  const c = MC.get(key); if (c && Date.now() - c.at < ttlMs) return c.v;
  let v;
  try { v = await env.LOGS.get(key); }
  catch (e) {
    globalThis.__kvDown = Date.now();
    try { v = env.HUB ? await hubOf(env).kvGet("m:" + key) : null; } catch (e2) { v = null; }
    if (v == null && c) v = c.v;
    MC.set(key, { v, at: Date.now() - ttlMs + 60000 });   // 1 分鐘後再試 KV
    return v;
  }
  if (env.HUB && v != null && (!c || c.v !== v)) { try { await hubOf(env).kvPut("m:" + key, v); } catch (e) {} }
  MC.set(key, { v, at: Date.now() });
  return v;
}
// 2026-11-02 KV 用量計數:每個執行個體記在記憶體,滿 50 次或 2 分鐘才寫一次 Hub(一天幾百次 Hub 請求,不吃 KV)
function kvCounted(env, ctx) {
  if (!env || !env.LOGS || env.__kvc) return env;
  const C = globalThis.__kvcnt || (globalThis.__kvcnt = { get: 0, list: 0, put: 0, del: 0, n: 0, at: Date.now() });
  const bump = k => { C[k]++; C.n++;
    if ((C.n >= 50 || Date.now() - C.at > 120e3) && env.HUB) { const d = { get: C.get, list: C.list, put: C.put, del: C.del }; C.get = C.list = C.put = C.del = C.n = 0; C.at = Date.now();
      const p = hubOf(env).kvInc("kvcount:" + ymdOf(twNow()), d).catch(() => {}); if (ctx && ctx.waitUntil) ctx.waitUntil(p); } };
  const L = env.LOGS, W = {
    get: (...a) => { bump("get"); return L.get(...a); }, getWithMetadata: (...a) => { bump("get"); return L.getWithMetadata(...a); },
    list: (...a) => { bump("list"); return L.list(...a); }, put: (...a) => { bump("put"); return L.put(...a); }, delete: (...a) => { bump("del"); return L.delete(...a); } };
  return Object.assign(Object.create(env), { LOGS: W, __kvc: true });
}
const kvDown = () => globalThis.__kvDown && Date.now() - globalThis.__kvDown < 10 * 60000;
const SEENW = new Map();
const BUGIP = new Map();   // 2026-11-03 抓 bug 上傳不要鑰匙後,每個 IP 每分鐘最多 6 次   // 2026-11-03 鑰匙「最後使用時間」這個執行個體上次試著寫 KV 的時間
// 2026-11-03 20:10 使用者「改」(KV 寫入一天上限 1,000 次,上午爆到 1.6 萬):鑰匙的「綁定手機 / 最後使用時間」改存 Hub(Durable Object,一天 10 萬筆寫入),KV 只留鑰匙本身的設定。
//   讀:每個執行個體每把鑰匙 5 分鐘讀一次 Hub;寫:第一次綁手機馬上寫,最後使用時間每個執行個體每把鑰匙一小時最多一次。舊的 KV 裡已有的 dev 照樣認(Hub 沒有時)
async function ksGet(env, k, fresh) { const c = MC.get("ks:" + k); if (!fresh && c && Date.now() - c.at < (c.v && c.v.dev ? 86400e3 : 60e3)) return c.v;   /* 20:38 使用者「這一天一次就可以了吧」:已綁定的一天讀一次;還沒綁定的 1 分鐘(別台剛綁好要很快知道,不然第二支手機會趁空綁上) */ let v = null; try { v = env.HUB ? await hubOf(env).kvGet("ks:" + k) : null; } catch (e) { if (c) return c.v; return { err: 1 }; } MC.set("ks:" + k, { v, at: Date.now() }); return v; }   // 讀 Hub 失敗 → {err}:不改綁定、也不寫新綁定(安全:不會因為 Hub 讀不到就讓第二支手機綁上)
async function ksPut(env, k, o) { if (!env.HUB) return; await hubOf(env).kvPut("ks:" + k, o); MC.set("ks:" + k, { v: o, at: Date.now() }); }
async function keyInfo(env, k) {
  if (!env || !env.LOGS || !KEY_RE.test(k || "")) return null;
  const v = await kvGetC(env, "key:" + k, 300000);
  // 2026-11-03 使用者「主導鑰匙 失效 主導機變成看盤機效果」:KV 恢復後改讀鑰匙真正的設定,含 5861 的那把在 KV 裡不是 owner(昨天全靠緊急模式才當 owner)。
  //   使用者定的規則是「金鑰裡面有5861的為主導機」→ 含 5861 的鑰匙一律當 owner(新建的鑰匙不會含 5861,見 newKey)
  if (v) { let r; try { r = JSON.parse(v); } catch (e) { return null; } if (k.includes("5861") && r && r.role !== "owner") r.role = "owner";
    try { const s2 = await ksGet(env, k); if (s2 && s2.err && r) r.ksErr = 1; else if (s2 && r){ if ("dev" in s2){ if (s2.dev) r.dev = s2.dev; else delete r.dev; } if (s2.seen && !(r.seen > s2.seen)) r.seen = s2.seen; } } catch (e) {}
    return r; }
  if (!kvDown()) return null;
  const ek = "ex:" + k; let ex = MC.get(ek);
  if (!ex || Date.now() - ex.at > (ex.v ? 3600e3 : 30e3)) {   // 2026-11-02 14:54 剛建的鑰匙 KV list 要幾十秒才看得到 → 「沒有這把」只記 30 秒(原本 1 小時,新鑰匙一直被說無效)
  let has = false; try { const r = await env.LOGS.list({ prefix: "key:" + k, limit: 5 }); has = r.keys.some(x => x.name === "key:" + k); } catch (e) {} ex = { v: has, at: Date.now() }; MC.set(ek, ex); }
  if (!ex.v) return null;
  // 2026-11-02 11:35 使用者「第三把 扣除前二把 就是第三把啊」:第三支手機的鑰匙不知道是哪一把 → 用主程式(不是看盤版)才會問的 ?cap=get / ?trade=pos 認出來(記在 Hub),認到就當 owner
  let emo = MC.get("emo:" + k);
  if (!emo || Date.now() - emo.at > (emo.v ? 600e3 : 15e3)) {   // 2026-11-02 14:50 還不是 owner 的結果只記 15 秒(別的執行個體剛認出來時,這邊不會 10 分鐘都當看盤 → 一直跳鑰匙視窗)
  let v = false; try { v = env.HUB ? !!(await hubOf(env).kvGet("emo:" + k)) : false; } catch (e) {} emo = { v, at: Date.now() }; MC.set("emo:" + k, emo); }
  const own = k.includes("5861") || EMER_OWN.some(s => k.endsWith(s)) || emo.v;
  return { role: own ? "owner" : "viewer", on: true, name: "緊急(KV 額度用完)", emer: true };
}
function newKey() {   // 2026-11-03 新鑰匙不會剛好含 5861(含 5861 = 主導機)或結尾是緊急模式的 owner 尾碼
  for (;;) { const a = new Uint8Array(12); crypto.getRandomValues(a); const k = [...a].map(b => b.toString(16).padStart(2, "0")).join("");
    if (!k.includes("5861") && !EMER_OWN.some(x => k.endsWith(x))) return k; }
}

export default {
  // ── Cloudflare Cron Trigger 到點 → 叫 GitHub 跑對應的 workflow ──
  // Workers 免費方案整個帳號只能有 5 條 Cron(樂透的 Worker 已經用掉),所以這裡只用 1 條:
  //   5,20,30,45 0,1,6,8,12 * * MON-FRI  → 每天醒來 20 次,只在下面這幾個台灣時間真的去叫 GitHub,其他時候什麼都不做
  // 2026-10-19 起 Cron 改成每分鐘(* 0-6,8,12 * * MON-FRI):台灣 09:00~13:35 每分鐘存一次雲端 1 分K 快照;原本的六個時段照舊叫 GitHub
  async scheduled(event, env, ctx) {
    env = kvCounted(env, ctx);
    const t = new Date(Date.now() + 8 * 3600e3);                      // 台灣時間
    const key = `${t.getUTCHours()}:${String(t.getUTCMinutes()).padStart(2, "0")}`;
    const SLOTS = { "8:20": "recheck", "8:45": "recheck", "9:05": "recheck", "14:30": "daily", "16:30": "daily", "20:30": "daily" };
    const what = SLOTS[key];
    if (what) { const r = await dispatch(env, what); console.log(`cron ${event.cron} 台灣 ${key} → ${what}: ${r.msg}`); }
    if (key === "8:20" || key === "8:30" || key === "8:45") { try { const v = await usGet(env, false); console.log(`cron 台灣 ${key} 美股:${v ? v.us + " " + v.idx.map(x => x.name + " " + x.pct + "%").join(" ") : "抓不到"}`); } catch (e) { console.log("美股失敗 " + e); } }
    try { const r = await snapshotBars(env, true, event.scheduledTime); if (r !== "off-hours") console.log(`cron 台灣 ${key} 分K:${r}`); } catch (e) { console.log("分K 快照失敗 " + e); }
  },

  async fetch(request, env, ctx) {
    env = kvCounted(env, ctx);   // 2026-11-02 使用者「做1,2」:算每天 KV 讀 / 寫 / list 次數(?kvping 看得到)
    if (request.method === "OPTIONS") return new Response(null, { headers: CORS });

    const url = new URL(request.url);
    // 2026-11-01 iPhone 網頁版 App(看盤版):/app = 看盤版網頁,加上 manifest / sw.js / 圖示(公開庫的檔,不用鑰匙;內容和 Android 看盤版一樣)
    if (request.method === "GET" && PWA_FILES[url.pathname] && env && env.GH_TOKEN) {
      const r = await ghRaw(env, PWA_FILES[url.pathname], GH_REF, 60);
      const h = new Headers(r.headers); h.set("Cache-Control", "no-cache");
      if (url.pathname === "/manifest.webmanifest") h.set("Content-Type", "application/manifest+json");
      if (url.pathname === "/sw.js") h.set("Service-Worker-Allowed", "/");
      return new Response(r.body, { status: r.status, headers: h });
    }
    const J = (obj, status = 200) => {
      // 2026-11-02 15:16 查「一進去就跳輸入鑰匙」:被拒(badKey)的請求記到 Hub(鑰匙只留頭尾 4 碼),?kvping 看得到
      try { if (status === 403 && obj && obj.badKey && env && env.HUB && ctx && ctx.waitUntil) { const kk = (url.searchParams.get("k") || request.headers.get("X-App-Key") || "").trim();
        const q = url.search.replace(/([?&]k=)[^&]*/, "$1*").replace(/([?&]dv=)[^&]*/, "$1*").slice(0, 120);
        ctx.waitUntil(hubOf(env).badLog({ t: new Date(Date.now() + 8 * 3600e3).toISOString().slice(11, 19), k: kk ? kk.slice(0, 4) + "…" + kk.slice(-4) : "", dv: !!url.searchParams.get("dv"), q, e: String(obj.error || obj.msg || "").slice(0, 60), ki: typeof KI === "undefined" || !KI ? null : (KI.role + (KI.emer ? "(緊急)" : "")) }).catch(() => {})); } } catch (e) {}
      return new Response(JSON.stringify(obj, null, 1), { status, headers: { ...CORS, "Content-Type": "application/json; charset=utf-8" } });
    };

    // 2026-11-02 11:05 診斷:KV 讀得到嗎(手機鑰匙一直被說無效 → 查是不是 KV 每日讀取額度用完);不回任何鑰匙內容
    if (url.searchParams.get("kvping") !== null) {
      const out = {};
      try { const v = await env.LOGS.get("keys:owners"); out.kv = v ? "ok" : "空"; } catch (e) { out.kv = "錯誤:" + String(e && e.message || e).slice(0, 160); }
      try { const v = await env.LOGS.get("keys:owners", { cacheTtl: 300 }); out.kvCache = v ? "ok" : "空"; } catch (e) { out.kvCache = "錯誤:" + String(e && e.message || e).slice(0, 160); }
      // 2026-11-02 使用者「當然 金鑰也是機密」:這裡不用鑰匙就能看 → 只給 KV 狀態和用量;被拒紀錄(含鑰匙頭尾碼)、主導機設定、鑰匙數量(會用掉 list 額度)
      //   只有中繼金鑰或 owner 鑰匙看得到
      const kpK = (url.searchParams.get("k") || "").trim(), kpOwner = (env.YT_PUSH_KEY && request.headers.get("X-Push-Key") === env.YT_PUSH_KEY) || (kpK && await (async () => { try { const ki = await keyInfo(env, kpK); return !!(ki && ki.on && ki.role === "owner"); } catch (e) { return false; } })());
      try { out.count = env.HUB ? await hubOf(env).kvGet("kvcount:" + ymdOf(twNow())) : null; } catch (e) {}
      if (!kpOwner) return J(out);
      try { const v = env.HUB ? await hubOf(env).kvGet("m:cfg:cap") : null; const c = v ? JSON.parse(v) : null; out.cfg = c ? { cap: c.cap, at: new Date(c.at + 8 * 3600e3).toISOString().slice(5, 16), set: c.set || null } : "雲端備份還沒有(主導機還沒送過)"; } catch (e) { out.cfg = "讀不到"; }   // 2026-11-02 看主導機設定有沒有送上來(只有數值,沒有鑰匙)
      try { out.bad = env.HUB ? (await hubOf(env).kvGet("badlog") || []).slice(-15) : []; } catch (e) {}
      try { const r = await env.LOGS.list({ prefix: "key:", limit: 1000 }); out.keys = r.keys.length; out.meta = r.keys.filter(k => k.metadata).length; } catch (e) { out.keys = "錯誤:" + String(e && e.message || e).slice(0, 120); }
      return J(out);
    }
    // ── 2026-10-29 鑰匙 ──
    const pushOk = !!(env && env.YT_PUSH_KEY && request.headers.get("X-Push-Key") === env.YT_PUSH_KEY);
    const kq = (url.searchParams.get("k") || request.headers.get("X-App-Key") || "").trim();
    let KI = null;
    // 2026-10-29 安全修補:dv 提到外面,下面 ownerKI 要用
    const dv = (url.searchParams.get("dv") || request.headers.get("X-App-Dev") || "").trim().slice(0, 40);
    if (kq) { KI = await keyInfo(env, kq); if (!KI || !KI.on) return J({ error: "鑰匙無效或已停用", badKey: true }, 403);
      // 2026-10-29 15:29 一人一機(使用者「我就是要這樣」):第一支帶這把鑰匙來的手機(dv = 手機自己產生的識別碼)被記住,之後別支手機拿同一把來就拒絕;
      //   到期日(exp)過了也拒絕;最後使用時間每小時最多寫一次 KV(免費額度一天 1,000 次寫入)
      const today = ymdOf(twNow());
      if (KI.exp && today > KI.exp) return J({ error: "鑰匙已到期(" + KI.exp + ")", badKey: true }, 403);
      if (KI.dev && dv && KI.dev !== dv && !KI.emer){ try { const s3 = await ksGet(env, kq, true); if (s3 && !s3.err && "dev" in s3){ if (s3.dev) KI.dev = s3.dev; else delete KI.dev; } } catch (e) {} }   // 不符時才重讀一次 Hub(剛解綁換手機,不用等快取一天)
      if (KI.dev && dv && KI.dev !== dv) return J({ error: "這把鑰匙已綁在另一支手機", badKey: true }, 403);
      // 2026-11-03 11:55 查到 KV 寫入一天爆到 1.5 萬次(上限 1,000):keyInfo 讀的是快取 5 分鐘的舊字串,seen 永遠是舊的 → 每個請求都寫一次「最後使用時間」;
      //   額度用完後寫入失敗,seen 更寫不進去,更是每個請求都試 → 改成:同一個執行個體同一把鑰匙一小時最多試一次(成功失敗都算),寫完把快取也換成新的
      if (dv && !KI.emer && !KI.ksErr && (!KI.dev || !KI.seen || Date.now() - KI.seen > 86400e3)) { const sw = SEENW.get(kq) || 0;   // 最後使用時間一天記一次就好
        if (!KI.dev || Date.now() - sw > 86400e3) { SEENW.set(kq, Date.now()); try { if (!KI.dev) KI.dev = dv; KI.seen = Date.now(); await ksPut(env, kq, { dev: KI.dev, seen: KI.seen }); } catch (e) {} } } }   // 2026-11-03 20:10 改寫 Hub(不寫 KV)
    // 2026-10-29 安全修補:owner 鑰匙做「寫入 / 下單 / 管鑰匙」時,鑰匙已綁手機就一定要帶 dv(手機 App 的 fetch 包裝每次都帶;不帶 dv 不能繞過一人一機去下單)。
    //   讀的端點(gh=raw 更新等)照舊不要求 dv,避免 Page.kt 偏好裡沒有 dev 時更新失敗
    if (KI && KI.emer && KI.role !== "owner" && (url.searchParams.get("cap") === "get" || ["pos", "fills"].includes(url.searchParams.get("trade") || "") || url.searchParams.get("repo") === "logic" || (url.searchParams.get("gh") === "raw" && /^app\/app\.html/.test(url.searchParams.get("path") || ""))) && dv && env.HUB) {   // 主程式的手機 → 記成 owner(緊急模式才有)
      try { await hubOf(env).kvPut("emo:" + kq, { dv, at: Date.now() }); } catch (e) {}
      MC.set("emo:" + kq, { v: true, at: Date.now() }); KI = { ...KI, role: "owner" };
    }
    const ownerKI = !!(KI && KI.role === "owner" && (!KI.dev || dv));
    const keysQ = url.searchParams.get("keys");
    // 2026-10-29 17:39 取件碼(使用者:「存在你這 你幫我遮起來 我用手機來複製」→ 鑰匙不經對話):桌機 ?keys=stash(中繼金鑰)把鑰匙存成 6 位數取件碼(10 分鐘、用一次就刪),
    //   手機在鑰匙視窗輸入 6 位數 → ?keys=pick&code= 拿到鑰匙
    if (keysQ === "pick") {
      const code = String(url.searchParams.get("code") || "").trim();
      if (!/^\d{6}$/.test(code) || !env || !env.LOGS) return J({ error: "取件碼要 6 位數" }, 400);
      // 2026-10-29 安全修補:防猜取件碼。每次嘗試先在 Hub(Durable Object,單執行緒 = 計數不會被並發繞過、不吃 KV 寫入額度)記一次:
      //   同一個 IP 10 分鐘最多 10 次、全部加起來 10 分鐘最多 60 次;超過回 429。Hub 掛了 → 不給取(手機還能直接貼 24 碼鑰匙)
      //   (不用 Cache API:workers.dev 網域上 cache.put 不保證有效)
      if (!env.HUB) return J({ error: "取件暫停(Worker 沒有 Hub),請直接輸入鑰匙" }, 503);
      let gate; try { gate = await hubOf(env).pickGate(request.headers.get("CF-Connecting-IP") || "?", Date.now()); } catch (e) { gate = null; }
      if (!gate || !gate.ok) return J({ error: gate ? "取件碼試太多次,10 分鐘後再試(或直接輸入鑰匙)" : "取件暫停,請稍後再試或直接輸入鑰匙" }, gate ? 429 : 503);
      const k = await env.LOGS.get("pick:" + code);
      if (!k) return J({ error: "取件碼不對或已過期(10 分鐘內有效、只能用一次)" }, 404);
      await env.LOGS.delete("pick:" + code);
      return J({ ok: true, k });
    }
    if (keysQ === "me") {   // 2026-10-29 15:45 手機問自己這把鑰匙的身分(試用 / 看盤 / owner、到期日);日期用 Worker 的時間,手機改時間沒用
      if (!KI) return J({ error: "沒有鑰匙", badKey: true }, 403);
      const today = ymdOf(twNow()); const left = KI.exp ? Math.max(0, Math.round((Date.parse(KI.exp + "T00:00:00+08:00") - Date.parse(today + "T00:00:00+08:00")) / 86400e3)) : null;
      return J({ ok: true, role: KI.role, name: KI.name, exp: KI.exp || null, left, today, plan: KI.plan || null });
    }
    // 2026-11-02 多空上限主控(使用者:「金鑰裡面有5861的為主導機」「其他二隻跟電腦偵測到 都自動改成與5861這隻一樣的金額」):
    //   ?cap=get → {cap, at, by}(中繼 X-Push-Key 或 owner 鑰匙);?cap=set&v=<元>(POST)只有鑰匙含 5861 的 owner 手機(帶 dv)能改,5萬~50萬
    //   KV 只在改的時候寫一次;讀用 cacheTtl 60(不吃額度)
    const capQ = url.searchParams.get("cap");
    if (capQ) {
      if (!env || !env.LOGS) return J({ error: "Worker 還沒綁定 KV(LOGS)" }, 503);
      const isOwner = !!(KI && KI.role === "owner");
      if (capQ === "get") {
        if (!pushOk && !isOwner) return J({ error: "要中繼金鑰或 owner 鑰匙" }, 403);
        let v = null; try { v = JSON.parse((await kvGetC(env, "cfg:cap", 60000)) || "null"); } catch (e) {}
        // 2026-11-03 KV 寫入額度用完那天主導機改的設定只存在 Hub → 兩邊比 at,拿新的(Hub 的 1 分鐘問一次)
        try { if (env.HUB){ let h = MC.get("hubcap"); if (!h || Date.now() - h.at > 60000){ h = { v: await hubOf(env).kvGet("m:cfg:cap"), at: Date.now() }; MC.set("hubcap", h); }
          const hv = h.v ? JSON.parse(h.v) : null; if (hv && (!v || (hv.at || 0) > (v.at || 0))) v = hv; } } catch (e) {}
        return J({ ok: true, ...(v || { cap: null }) });
      }
      if (capQ === "set") {
        if (request.method !== "POST") return J({ error: "要用 POST" }, 405);
        if (!ownerKI || !kq.includes("5861")) return J({ error: "只有主導機(5861)能改" }, 403);
        { const t = twNow(), wd = t.getUTCDay(), m = t.getUTCHours() * 60 + t.getUTCMinutes(); if (wd > 0 && wd < 6 && m >= 540 && m <= 810) return J({ error: "開盤中不能改設定(13:30 後才能用)" }, 403); }
        const n = Math.round(Number(url.searchParams.get("v")));
        if (!(n >= 50000 && n <= 500000)) return J({ error: "金額要 5萬~50萬" }, 400);
        // 2026-11-02 09:24 使用者「所有設定下的數值 皆由主導機節制」:POST body {set:{hi,lo,amp,cnt,cap,disc,broker,exch,tax}} 一起存
        let set = null; try { const b = await request.json(); if (b && b.set && typeof b.set === "object") { set = {};
          for (const k of ["hi", "lo", "amp", "cnt", "cap", "disc", "broker", "exch", "tax"]) if (b.set[k] != null) set[k] = String(b.set[k]).slice(0, 12); } } catch (e) {}
        const v = { cap: n, at: Date.now(), by: "5861", ...(set ? { set } : {}) };
        let kvOk = true; try { await env.LOGS.put("cfg:cap", JSON.stringify(v)); } catch (e) { kvOk = false; }   // 2026-11-03 KV 寫入額度用完時照樣存 Hub(get 會拿兩邊比較新的)
        MC.set("cfg:cap", { v: JSON.stringify(v), at: Date.now() }); try { if (env.HUB) await hubOf(env).kvPut("m:cfg:cap", JSON.stringify(v)); } catch (e) { if (!kvOk) return J({ error: "雲端存不進去,請稍後再試" }, 503); }
        return J({ ok: true, ...v });
      }
      return J({ error: "cap 只有 get / set" }, 400);
    }
    if (keysQ) {
      if (!env || !env.LOGS) return J({ error: "Worker 還沒綁定 KV(LOGS)" }, 503);
      if (!pushOk && !ownerKI) return J({ error: "要中繼金鑰或 owner 鑰匙" }, 403);   // 2026-10-29 安全修補:owner 鑰匙已綁手機要帶 dv
      if (keysQ === "list") {
        const out = []; let cursor;
        do { const r = await env.LOGS.list({ prefix: "key:", cursor, limit: 1000 });
          for (const k of r.keys) { try { const v = await env.LOGS.get(k.name); if (v) out.push({ k: k.name.slice(4), ...JSON.parse(v) }); } catch (e) {} }
          cursor = r.list_complete ? null : r.cursor; } while (cursor);
        return J(out);
      }
      if (request.method !== "POST") return J({ error: "要用 POST" }, 405);
      if (keysQ === "stash") {
        let b; try { b = await request.json(); } catch (e) { b = {}; }
        if (!KEY_RE.test(b.k || "") || !(await keyInfo(env, b.k))) return J({ error: "沒有這把鑰匙" }, 404);
        const code = String(100000 + crypto.getRandomValues(new Uint32Array(1))[0] % 900000);   // 2026-10-29 安全修補:改用密碼學亂數(原 Math.random)
        await env.LOGS.put("pick:" + code, b.k, { expirationTtl: 600 });
        return J({ ok: true, code, minutes: 10 });
      }
      if (keysQ === "add") {
        let b; try { b = await request.json(); } catch (e) { b = {}; }
        // 2026-10-29 15:54 方案(使用者:「金鑰分試用一個月+自帶系統交易成績單 / 一個月 / 三個月 / 一年 對應 免費/600/1700/6000」);16:00 試用和看盤內容一樣,只差 30 天免費
        const PLANS = { /* 2026-11-01 試用方案拿掉(和免費合併成「免鑰匙 30 天」) */ m1: { role: "viewer", days: 30, price: 600, label: "一個月" }, m3: { role: "viewer", days: 90, price: 1700, label: "三個月" }, y1: { role: "viewer", days: 365, price: 6000, label: "一年" } };
        const pl = PLANS[b.plan] || null;
        // 2026-11-01 試用方案已拿掉:舊的發鑰匙工具還會送 plan / role = trial,原本會變成「不到期的看盤鑰匙」→ 直接拒絕
        if (b.plan === "trial" || b.role === "trial") return J({ error: "試用方案已取消(免鑰匙就有 30 天);付費請用 m1 / m3 / y1" }, 400);
        if (b.plan && !pl) return J({ error: "沒有這個方案:" + String(b.plan).slice(0, 10) + "(m1 / m3 / y1)" }, 400);
        const role = pl ? pl.role : b.role === "owner" ? "owner" : "viewer", name = String(b.name || "").slice(0, 40), k = newKey();
        const rec = { name, role, on: true, made: ymdOf(twNow()) };
        if (pl) { rec.plan = b.plan; rec.price = pl.price; }
        const days = +b.days > 0 ? Math.floor(+b.days) : pl ? pl.days : role === "trial" ? 30 : 0;   // 試用(trial):30 天,期間沒有今天的判一二三訊號(過去的成績單看得到)
        if (days > 0) rec.exp = ymdOf(new Date(twNow().getTime() + days * 86400e3));   // 到期日(天數,不填 = 不到期;日期是 Worker 的時間,手機改時間沒用)
        await env.LOGS.put("key:" + k, JSON.stringify(rec));
        if (role === "owner") await env.LOGS.put("keys:owners", "1");
        return J({ ok: true, k, ...rec });
      }
      // 2026-11-02 14:29 使用者刪鑰匙打錯(整串鑰匙要手打):?keys=delsfx&sfx=結尾幾碼 / ?keys=delpfx&pfx=開頭幾碼(至少 4 碼,只用 KV list、不讀內容,KV 讀取額度用完也能刪);
      //   剛好一把符合才刪,回傳只露頭尾 4 碼
      // 2026-11-03 使用者「不知道KEY放哪」(主導機要解綁,手上只有結尾 859cf):用結尾 / 開頭幾碼解除綁定(中繼金鑰或 owner 鑰匙才能做,同 del 的權限),剛好一把符合才做
      if (keysQ === "unbindsfx" || keysQ === "unbindpfx" || keysQ === "unbindhas") {   // unbindhas = 鑰匙中間含這幾碼(主導機:含 5861)
        const part = String(url.searchParams.get(keysQ === "unbindsfx" ? "sfx" : keysQ === "unbindpfx" ? "pfx" : "has") || "").trim().toLowerCase();
        if (!/^[a-f0-9]{4,40}$/.test(part)) return J({ error: "要給至少 4 碼(0-9 a-f)" }, 400);
        const names = []; let cursor;
        do { const r = await env.LOGS.list({ prefix: "key:", cursor, limit: 1000 }); for (const x of r.keys) names.push(x.name.slice(4)); cursor = r.list_complete ? null : r.cursor; } while (cursor);
        const hit = names.filter(k => keysQ === "unbindsfx" ? k.endsWith(part) : keysQ === "unbindpfx" ? k.startsWith(part) : k.includes(part)), mask = k => k.slice(0, 4) + "…" + k.slice(-4);
        if (hit.length !== 1) return J({ error: hit.length ? `有 ${hit.length} 把符合,請多給幾碼` : "沒有符合的鑰匙", match: hit.map(mask) }, hit.length ? 409 : 404);
        const cur2 = await keyInfo(env, hit[0]); if (!cur2 || cur2.emer) return J({ error: "讀不到這把鑰匙(KV 額度?)" }, 503);
        delete cur2.dev; await env.LOGS.put("key:" + hit[0], JSON.stringify(cur2)); MC.delete("key:" + hit[0]); try { await ksPut(env, hit[0], { dev: null, seen: cur2.seen || 0 }); } catch (e) {}   // Hub 的綁定也清掉
        return J({ ok: true, unbound: true, k: mask(hit[0]), role: cur2.role, name: cur2.name || "" });
      }
      if (keysQ === "delsfx" || keysQ === "delpfx") {
        const part = String(url.searchParams.get(keysQ === "delsfx" ? "sfx" : "pfx") || "").trim().toLowerCase();
        if (!/^[a-f0-9]{4,40}$/.test(part)) return J({ error: "要給至少 4 碼(0-9 a-f)" }, 400);
        const names = []; let cursor;
        do { const r = await env.LOGS.list({ prefix: "key:", cursor, limit: 1000 }); for (const x of r.keys) names.push(x.name.slice(4)); cursor = r.list_complete ? null : r.cursor; } while (cursor);
        const hit = names.filter(k => keysQ === "delsfx" ? k.endsWith(part) : k.startsWith(part)), mask = k => k.slice(0, 4) + "…" + k.slice(-4);
        if (hit.length !== 1) return J({ error: hit.length ? `有 ${hit.length} 把符合,請多給幾碼` : "沒有符合的鑰匙", match: hit.map(mask) }, hit.length ? 409 : 404);
        await env.LOGS.delete("key:" + hit[0]); MC.delete("key:" + hit[0]); MC.delete("ex:" + hit[0]);
        return J({ ok: true, deleted: true, k: mask(hit[0]), left: names.length - 1 });
      }
      const k2 = url.searchParams.get("k2") || ""; const cur = await keyInfo(env, k2);
      if (!cur) return J({ error: "沒有這把鑰匙" }, 404);
      if (keysQ === "del") { await env.LOGS.delete("key:" + k2); return J({ ok: true, k: k2, deleted: true }); }
      if (keysQ === "unbind") { delete cur.dev; await env.LOGS.put("key:" + k2, JSON.stringify(cur)); MC.delete("key:" + k2); try { await ksPut(env, k2, { dev: null, seen: cur.seen || 0 }); } catch (e) {} return J({ ok: true, k: k2, ...cur }); }   // 換手機:解綁,下一支用的手機重新綁
      if (keysQ === "off" || keysQ === "on") { cur.on = keysQ === "on"; await env.LOGS.put("key:" + k2, JSON.stringify(cur)); return J({ ok: true, k: k2, ...cur }); }
      return J({ error: "keys=list / add / off / on / del / unbind / me / stash / pick" }, 400);
    }
    // 2026-11-01 iPhone 網頁版 App 推播:?push=key(公鑰)/ sub(訂閱:帶 k 就記鑰匙,免費記 dv)/ unsub / last(sw.js 收到推播後拿這支手機看得到的最新訊號)
    const pq = url.searchParams.get("push");
    if (pq && env && env.HUB) {
      if (pq === "key") return J({ pub: (await vapidKeys(env)).pub });
      let b = {}; try { b = await request.json(); } catch (e) {}
      const ep = String((b.sub && b.sub.endpoint) || b.endpoint || "");
      if (!/^https:\/\/[\w.-]+\.(push\.apple\.com|googleapis\.com|mozilla\.com|windows\.com)\//.test(ep)) return J({ error: "不是推播網址" }, 400);
      const id = b64u(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(ep))).slice(0, 22);
      if (pq === "sub") { const fdv = /^[a-f0-9]{16}$/.test(String(b.dv || "")) ? String(b.dv) : "";
        return J(await hubOf(env).pushSubPut({ id, endpoint: ep, k: KI ? kq : "", dv: KI ? "" : fdv, made: Date.now() })); }
      if (pq === "unsub") return J(await hubOf(env).pushSubDel(id));
      if (pq === "last") {
        const s = (await hubOf(env).pushSubs()).find(x => x.id === id); if (!s) return J({ items: [] });
        const tier = await pushTier(env, s), all = (await hubOf(env).logGet(ymdOf(twNow()))).filter(x => pushCanSee(tier, x));
        const now = hhmmOf(twNow()), m = +now.slice(0, 2) * 60 + +now.slice(3) - 3, since = String(Math.floor(m / 60)).padStart(2, "0") + ":" + String(m % 60).padStart(2, "0");
        const recent = all.filter(x => (x.t || "") >= since).slice(0, 3);
        return J({ items: (recent.length ? recent : all.slice(0, 1)).map(x => ({ title: x.title, body: x.body, key: x._k || x.title })) });
      }
      return J({ error: "push=key / sub / unsub / last" }, 400);
    }
    if (env && env.KEYS_REQUIRED === "1" && !KI && !pushOk && !url.searchParams.get("gh")) return J({ error: "要帶鑰匙(k)", badKey: true, needKey: true }, 403);
    // 寫入 / 下單類:只有 owner 鑰匙或中繼金鑰可以用(看盤版 viewer 鑰匙不行)
    // 2026-10-29 安全修補:拿掉「KV 讀 keys:owners 失敗 / 沒有 keys:owners 就放行」—— KV 一出錯外人就能塞假訊號、下單。
    //   owner 鑰匙已上線(交接說明:兩支手機都輸入 owner 鑰匙;App 用 owner 鑰匙讀 judge.js),中繼 / engine 都帶 X-Push-Key,所以一律要鑰匙,不再讀 KV
    const ownerOk = async () => pushOk || ownerKI;

    // ── 第五條路:?bars=YYYY-MM-DD[&codes=…][&since=HH:MM] → 雲端 1 分K ──
    const bd = url.searchParams.get("bars");
    if (bd === "snap") {
      // 手動 / 補救:現在就存一個快照(這一分鐘已經有就不重存)。手機發現雲端 K 落後 2 分鐘以上會呼叫,Cron 沒跑也不會斷
      if (!env || !env.LOGS) return J({ error: "Worker 還沒綁定 KV(LOGS)" }, 503);
      try {
        const t = twNow(); const k = `bar:${ymdOf(t)}:${hhmmOf(t)}`;
        if (await snapHas(env, k)) return J({ ok: true, msg: "這一分鐘已有快照 " + k });
        const r = await snapshotBars(env, false);   // 手動補救只存開頭那一筆,不等 :30 / :56
        return J({ ok: true, msg: r });
      } catch (e) { return J({ ok: false, error: String(e && e.stack || e) }, 500); }
    }
    // 2026-10-22 09:10 檢查(友達 / 時碩缺 09:02、09:07 兩根):?barsraw=today&code=2409 → 每個快照 / 子快照裡這檔的原始 [z, v, y, t],只讀、查問題用
    const br = url.searchParams.get("barsraw");
    if (br) {
      if (!env || !env.LOGS) return J({ error: "Worker 還沒綁定 KV(LOGS)" }, 503);
      const d = br === "today" ? ymdOf(twNow()) : br, code = url.searchParams.get("code") || "";
      if (!/^\d{4}-\d{2}-\d{2}$/.test(d) || !code) return J({ error: "要 barsraw=YYYY-MM-DD|today&code=代碼" }, 400);
      const from = url.searchParams.get("from") || "00:00", to = url.searchParams.get("to") || "99:99";
      const keys = [];
      const meta = {};   // 2026-10-22 12:10 不 list(KV 一天 list 上限),照鍵名直接讀,metadata 用 getWithMetadata
      for (const k of [...minuteKeys(d, "bar", from), ...minuteKeys(d, "bas", from)]) if (k.slice(-5) <= to) keys.push(k);
      keys.sort((a, b) => a.slice(-5) < b.slice(-5) ? -1 : a.slice(-5) > b.slice(-5) ? 1 : (a < b ? -1 : 1));
      const out = [];
      for (const k of keys.slice(0, 120)) {
        let v; try { const r = await env.LOGS.getWithMetadata(k); if (r.metadata) meta[k] = r.metadata; v = JSON.parse(r.value || "null"); } catch (e) { v = null; }
        if (v == null) continue;
        if (k.startsWith("bas:")) (Array.isArray(v) ? v : []).forEach((s, i) => out.push([k + "#" + i, s && s[code] || null]));
        else out.push([k, v && v[code] || null]);
      }
      return J({ d, code, rows: out, meta });
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
      let out; try { out = since ? await dayBars(env, d, codes, since) : await dayBarsFull(env, ctx, url.origin, d, codes); } catch (e) { return J({ error: "雲端 K 讀取失敗:" + String(e && e.message || e).slice(0, 200) }, 503); }   // 2026-11-02 失敗回 503 + 原因(原本整個 Worker 例外 1101)
      const body = JSON.stringify(out);
      const resp = new Response(body, { status: 200, headers: { ...CORS, "Content-Type": "application/json; charset=utf-8" } });
      const ttl = since ? 10 : 50;    // 全天的查詢要讀幾百個鍵,邊緣快取 50 秒;增量的 10 秒(2026-10-23 15:40 原 20 秒;手機每 10 秒問一次,快取 20 秒等於白問一半。KV get 一天約 2.5 萬次 / 支手機 < 10 萬)
      const cached = new Response(body, { status: 200, headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": `public, max-age=${ttl}` } });
      if (ctx && ctx.waitUntil) ctx.waitUntil(cache.put(ck, cached)); else await cache.put(ck, cached);
      return resp;
    }

    // ── 2026-10-29 當沖額度(使用者「我說的可用資金是訊息右上那個」):POST ?quota=push {d,hm,fill,work,total}(中繼程式每 ~30 秒,要金鑰);
    //    GET ?quota=get → 最近一筆(邊緣快取 10 秒);手機訊息右上角「資金」= total − fill − work − hold ──
    const qt = url.searchParams.get("quota");
    if (qt) {
      if (!env || !env.HUB) return J({ error: "Worker 還沒綁定 Durable Object(HUB)" }, 503);
      if (qt === "push") {
        if (request.method !== "POST") return J({ error: "要用 POST" }, 405);
        if (!env.YT_PUSH_KEY || request.headers.get("X-Push-Key") !== env.YT_PUSH_KEY) return J({ error: "金鑰不對" }, 403);
        let b; try { b = await request.json(); } catch (e) { return J({ error: "JSON 格式錯誤" }, 400); }
        if (!b || !/^\d{4}-\d{2}-\d{2}$/.test(b.d || "")) return J({ error: "要有 d(YYYY-MM-DD)" }, 400);
        await hubOf(env).kvPut("quota", { d: b.d, hm: String(b.hm || ""), fill: +b.fill || 0, work: +b.work || 0, hold: +b.hold || 0, total: +b.total || 0, at: Date.now() });   // 2026-11-02 hold = 還沒平倉部位 × 漲停價(元大先扣)
        return J({ ok: true });
      }
      const v = await hubOf(env).kvGet("quota");
      return new Response(JSON.stringify(v || {}), { status: 200, headers: { ...CORS, "Content-Type": "application/json; charset=utf-8", "Cache-Control": "public, max-age=10" } });
    }

    // ── 2026-10-30 11:08(實際時間)電腦開盤前資格複檢(tools/yuanta_bridge/elig.py;使用者「該如何避免再次出現這問題」→「2道都做」):
    //    POST ?elig=push {d, hm, disposed, attention, nodt, noshort, …}(中繼金鑰)/ GET ?elig=get → 手機把這些股票從監控拿掉(不靠 GitHub 排程)
    if (url.searchParams.get("usmkt")) {   // 2026-11-06 美股四大指數(當沖通知欄;公開資料不用鑰匙)
      const v = await usGet(env, url.searchParams.get("usmkt") === "force");
      return new Response(JSON.stringify(v || {}), { status: 200, headers: { ...CORS, "Content-Type": "application/json; charset=utf-8", "Cache-Control": "public, max-age=60" } });
    }
    // 2026-11-06 名單外手動成交的股票(中繼 extra_add 補好 K 後推上來):POST ?extra=push {d, codes:[{code,name,market,prev}]}(中繼金鑰)/ GET ?extra=get → 手機也加進名單
    const exq = url.searchParams.get("extra");
    if (exq) {
      if (!env || !env.HUB) return J({ error: "Worker 還沒綁定 Durable Object(HUB)" }, 503);
      if (exq === "push") {
        if (request.method !== "POST") return J({ error: "要用 POST" }, 405);
        if (!env.YT_PUSH_KEY || request.headers.get("X-Push-Key") !== env.YT_PUSH_KEY) return J({ error: "金鑰不對" }, 403);
        let b; try { b = await request.json(); } catch (e) { return J({ error: "JSON 格式錯誤" }, 400); }
        if (!b || !/^\d{4}-\d{2}-\d{2}$/.test(b.d || "")) return J({ error: "要有 d(YYYY-MM-DD)" }, 400);
        const codes = (Array.isArray(b.codes) ? b.codes : []).filter(x => x && /^[1-9]\d{3}$/.test(String(x.code))).slice(0, 50)
          .map(x => ({ code: String(x.code), name: String(x.name || x.code).slice(0, 12), market: x.market === "tpex" ? "tpex" : "twse", prev: +x.prev || null, at: String(x.at || "").slice(0, 5) }));
        await hubOf(env).kvPut("extra", { d: b.d, codes });
        return J({ ok: true, n: codes.length });
      }
      const v = await hubOf(env).kvGet("extra");
      return new Response(JSON.stringify(v || {}), { status: 200, headers: { ...CORS, "Content-Type": "application/json; charset=utf-8", "Cache-Control": "public, max-age=30" } });
    }
    const el = url.searchParams.get("elig");
    if (el) {
      if (!env || !env.HUB) return J({ error: "Worker 還沒綁定 Durable Object(HUB)" }, 503);
      if (el === "push") {
        if (request.method !== "POST") return J({ error: "要用 POST" }, 405);
        if (!env.YT_PUSH_KEY || request.headers.get("X-Push-Key") !== env.YT_PUSH_KEY) return J({ error: "金鑰不對" }, 403);
        let b; try { b = await request.json(); } catch (e) { return J({ error: "JSON 格式錯誤" }, 400); }
        if (!b || !/^\d{4}-\d{2}-\d{2}$/.test(b.d || "")) return J({ error: "要有 d(YYYY-MM-DD)" }, 400);
        const lst = k => Array.isArray(b[k]) ? b[k].map(String).filter(x => /^[1-9]\d{3}$/.test(x)).slice(0, 300) : [];
        await hubOf(env).kvPut("elig", { d: b.d, hm: String(b.hm || "").slice(0, 8), at: +b.at || Date.now(), pool_date: String(b.pool_date || "").slice(0, 10), n_pool: +b.n_pool || 0,
          disposed: lst("disposed"), attention: lst("attention"), nodt: lst("nodt"), noshort: lst("noshort"), all_disposed: lst("all_disposed"), src: (Array.isArray(b.src) ? b.src : []).map(x => String(x).slice(0, 60)).slice(0, 12), err: (Array.isArray(b.err) ? b.err : []).map(x => String(x).slice(0, 120)).slice(0, 8) });
        return J({ ok: true });
      }
      const v = await hubOf(env).kvGet("elig");
      return new Response(JSON.stringify(v || {}), { status: 200, headers: { ...CORS, "Content-Type": "application/json; charset=utf-8", "Cache-Control": "public, max-age=60" } });
    }

    // ── 2026-10-29 公式在電腦(engine.py):POST ?engine=push {d, hm, on, n}(每分鐘心跳,中繼金鑰)/ GET ?engine=get → 手機看到 3 分鐘內的心跳就不自己發通知、只收雲端的 ──
    const eg = url.searchParams.get("engine");
    if (eg) {
      if (!env || !env.HUB) return J({ error: "Worker 還沒綁定 Durable Object(HUB)" }, 503);
      if (eg === "push") {
        if (request.method !== "POST") return J({ error: "要用 POST" }, 405);
        if (!env.YT_PUSH_KEY || request.headers.get("X-Push-Key") !== env.YT_PUSH_KEY) return J({ error: "金鑰不對" }, 403);
        let b; try { b = await request.json(); } catch (e) { return J({ error: "JSON 格式錯誤" }, 400); }
        if (!b || !/^\d{4}-\d{2}-\d{2}$/.test(b.d || "")) return J({ error: "要有 d(YYYY-MM-DD)" }, 400);
        await hubOf(env).kvPut("engine", { d: b.d, hm: String(b.hm || ""), on: b.on ? 1 : 0, n: +b.n || 0, msg: String(b.msg || "").slice(0, 120), ver: String(b.ver || "").slice(0, 16), stale: b.stale ? 1 : 0, at: Date.now() });   // 2026-10-29 23:50 多存規則版本 / 規則舊(手機警示用)
        return J({ ok: true });
      }
      const v = await hubOf(env).kvGet("engine");
      return new Response(JSON.stringify(v || {}), { status: 200, headers: { ...CORS, "Content-Type": "application/json; charset=utf-8", "Cache-Control": "public, max-age=20" } });
    }

    // ── 元大中繼(tools/yuanta_bridge):POST ?ybars=push 收 1 分 K;GET ?ybars=today|YYYY-MM-DD[&codes=][&since=] 讀(格式同 ?bars)──
    // 2026-10-22 使用者(元大 SPARK API「先寫好 等營業員回覆」):先只收、只讀給比對用,手機還是讀 ?bars;驗證準了再切換
    const yb = url.searchParams.get("ybars");
    if (yb) {
      if (!env || !env.HUB) return J({ error: "Worker 還沒綁定 Durable Object(HUB)" }, 503);
      if (yb === "push") {
        if (request.method !== "POST") return J({ error: "要用 POST" }, 405);
        if (!env.YT_PUSH_KEY) return J({ error: "Worker 沒有設定 YT_PUSH_KEY 密鑰" }, 503);
        if (request.headers.get("X-Push-Key") !== env.YT_PUSH_KEY) return J({ error: "金鑰不對" }, 403);
        let b; try { b = await request.json(); } catch (e) { return J({ error: "JSON 格式錯誤" }, 400); }
        if (!b || !/^\d{4}-\d{2}-\d{2}$/.test(b.d || "") || !/^\d{2}:\d{2}$/.test(b.m || "") || typeof b.bars !== "object") return J({ error: "要有 d(YYYY-MM-DD)、m(HH:MM)、bars" }, 400);
        await hubOf(env).ybarPut(b.d, b.m, b.bars, !!b.merge);   // 一分鐘一列(晚到的成交整分鐘重送覆蓋);2026-10-24 起存 Hub,不用 KV 寫入額度
        return J({ ok: true, n: Object.keys(b.bars).length });
      }
      if (yb === "days") return J({ days: await hubOf(env).ybarDays() });   // 2026-10-25 save_cloud_bars.py 存補抓的前幾天用
      const d = yb === "today" ? ymdOf(twNow()) : yb;
      if (!/^\d{4}-\d{2}-\d{2}$/.test(d)) return J({ error: "ybars 要是 push、days、today 或 YYYY-MM-DD" }, 400);
      const want = (url.searchParams.get("codes") || "").split(",").map(x => x.trim()).filter(Boolean), since = url.searchParams.get("since") || "";
      const rows = await hubOf(env).ybarGet(d, since), bars = {};
      for (const r of rows) { let o; try { o = JSON.parse(r.bars || "{}"); } catch (e) { continue; }
        for (const c in o) if (!want.length || want.includes(c)) (bars[c] = bars[c] || []).push([r.m, ...o[c]]); }
      return J({ d, src: "yuanta", last: rows.length ? rows[rows.length - 1].m : since, n: rows.length, bars });
    }

    // ── 元大即時價通道:POST ?yquote=register(中繼程式登記 cloudflared 通道網址);GET ?yquote=status ──
    // 2026-10-22 使用者:「手機即時價也要改用元大」:手機的 ?codes 先問元大中繼(通道),不通才問證交所,手機不用改
    const yq = url.searchParams.get("yquote");
    if (yq) {
      if (!env || !env.LOGS) return J({ error: "Worker 還沒綁定 KV(LOGS)" }, 503);
      if (yq === "register") {
        if (request.method !== "POST") return J({ error: "要用 POST" }, 405);
        if (!env.YT_PUSH_KEY || request.headers.get("X-Push-Key") !== env.YT_PUSH_KEY) return J({ error: "金鑰不對" }, 403);
        let b; try { b = await request.json(); } catch (e) { return J({ error: "JSON 格式錯誤" }, 400); }
        if (!b || !/^https:\/\/[\w.-]+$/.test(b.url || "")) return J({ error: "url 要是 https://主機名" }, 400);
        await env.LOGS.put("yquote:url", b.url, { expirationTtl: 3 * 86400 });
        MC.set("yquote:url", { v: b.url, at: Date.now() }); try { if (env.HUB) await hubOf(env).kvPut("m:yquote:url", b.url); } catch (e) {}
        return J({ ok: true, url: b.url });
      }
      const u = (await kvGetC(env, "yquote:url", 60000)) || (kvDown() ? EMER_QURL : null);
      let health = null;
      if (u) { try { health = await (await fetch(u + "/health", { signal: AbortSignal.timeout(3000) })).json(); } catch (e) { health = { error: String(e) }; } }
      return J({ url: u || null, health });
    }

    // ── 2026-10-28 抓 bug 紀錄:手機長按左上角燈上傳(POST ?bug=put),Claude 讀(?bug=list / ?bug=get&k=)。KV 保留 7 天,一次上傳 1 次寫入 ──
    const bq0 = url.searchParams.get("bug");
    if (bq0) {
      if (!env || !env.LOGS) return J({ error: "Worker 還沒綁定 KV(LOGS)" }, 503);
      if (bq0 === "put") {
        if (request.method !== "POST") return J({ error: "要用 POST" }, 405);
        // 2026-10-29 安全修補:上傳要 owner 鑰匙或中繼金鑰(外人狂傳會吃光 KV 一天 1,000 次寫入,分K 快照 / 寬度跟著停);手機 fetch 包裝會自動帶 k= / dv=
        //   大小上限照舊 1.5MB(snaps 異常前 10 秒完整紀錄大小不定,改小怕手機傳不上去)
        // 2026-11-03 12:56 使用者「上傳不需要 鑰匙」:不再要 owner 鑰匙(現在存 Hub、最多留 20 份,不吃 KV 額度);防亂傳:同一個 IP 每分鐘最多 6 次
        { const ip = request.headers.get("CF-Connecting-IP") || "?", now = Date.now(), q = (BUGIP.get(ip) || []).filter(t => now - t < 60000);
          if (q.length >= 6) return J({ error: "上傳太頻繁,等一分鐘" }, 429); q.push(now); BUGIP.set(ip, q); if (BUGIP.size > 500) BUGIP.clear(); }
        const cl = +request.headers.get("Content-Length") || 0; if (cl > 1500000) return J({ error: "太大" }, 413);
        const body = await request.text();
        if (body.length > 1500000) return J({ error: "太大" }, 413);
        const k = "bug:" + new Date(Date.now() + 8 * 3600000).toISOString().slice(0, 19).replace(/[-:T]/g, "");
        // 2026-11-03 11:56 使用者「抓bug也出現失敗」:改存 Hub(不吃 KV 每天 1,000 次寫入),最多留 20 份;沒有 Hub 才用 KV
        try { if (env.HUB){ const h = hubOf(env); await h.kvPut(k, body); await h.kvPut("buglast", k); await h.bugTrim(20); }
          else { await env.LOGS.put(k, body, { expirationTtl: 7 * 86400, metadata: { size: body.length } }); await env.LOGS.put("buglast", k, { expirationTtl: 7 * 86400 }); } }
        catch (e) { return J({ error: "雲端存不進去:" + String(e && e.message || e).slice(0, 80) }, 503); }   // 最新一份的鍵(讀的時候不用 list,不吃 KV list 額度)
        return J({ ok: true, key: k });
      }
      if (bq0 === "list") { let out = []; try { if (env.HUB) out = await hubOf(env).bugList(); } catch (e) {} try { const l = await env.LOGS.list({ prefix: "bug:" }); out = out.concat(l.keys.map(x => ({ k: x.name, size: x.metadata && x.metadata.size }))); } catch (e) {} return J(out); }
      // 2026-10-29 安全修補:只准讀 bug: 開頭的鍵(原本 k 可指任何 KV 鍵,例如 pick:取件碼);k 不是 bug: 開頭就讀最新一份
      if (bq0 === "get") { const bk0 = url.searchParams.get("k") || ""; let v = null;
        try { if (env.HUB){ const h = hubOf(env); const bk = bk0.startsWith("bug:") ? bk0 : (await h.kvGet("buglast")); if (bk) v = await h.kvGet(bk); } } catch (e) {}   // 2026-11-03 先找 Hub(新的存那裡)
        if (v == null) v = await env.LOGS.get(bk0.startsWith("bug:") ? bk0 : ((await env.LOGS.get("buglast")) || "bug:none"));
        return v ? new Response(v, { headers: { ...CORS, "Content-Type": "application/json; charset=utf-8" } }) : J({ error: "沒有" }, 404); }
      return J({ error: "bug=put / list / get" }, 400);
    }

    // ── 2026-10-28 手機強制進場 / 強制平倉:GET ?trade=pos(這檔有沒有部位)、POST ?trade=cmd(送指令)→ 轉給電腦的元大中繼(Cloudflare 通道)──
    //   使用者「改」(強制模式規格定案)。不輪詢:手機長按 / 滑到底才各打一次,每次讀 1 次 KV(通道網址,邊緣快取 60 秒)。
    //   交易密碼(TRADE_CMD_PIN)由電腦端檢查,Worker 不存;通道用 YT_PUSH_KEY 當金鑰(只有 Worker 和電腦知道)
    const tq = url.searchParams.get("trade");
    if (tq === "pos" || tq === "cmd" || tq === "fills") {   // 2026-10-29 fills:今天元大帳號的成交 + 每檔輸贏(含手動單;使用者「現在真實贏多少」)
      if (!env || !env.LOGS || !env.YT_PUSH_KEY) return J({ ok: false, msg: "Worker 沒有設定通道金鑰" }, 503);
      if (!(await ownerOk())) return J({ ok: false, msg: "下單 / 查部位要 owner 鑰匙(設定裡輸入)", badKey: true }, 403);   // 2026-10-29 鑰匙:看盤版不能下單
      const qu = (await kvGetC(env, "yquote:url", 60000)) || (kvDown() ? EMER_QURL : null);
      if (!qu) return J({ ok: false, msg: "電腦沒有連上(沒有登記通道,請確認電腦開機、元大中繼在跑)" }, 503);
      try {
        const init = { headers: { "X-Quote-Key": env.YT_PUSH_KEY }, signal: AbortSignal.timeout(8000) };
        if (tq === "cmd") {
          if (request.method !== "POST") return J({ ok: false, msg: "要用 POST" }, 405);
          const body = await request.text();
          if (body.length > 2000) return J({ ok: false, msg: "指令太長" }, 400);
          Object.assign(init, { method: "POST", body, headers: { "X-Quote-Key": env.YT_PUSH_KEY, "Content-Type": "application/json" } });
        }
        const fd = url.searchParams.get("d") || "";   // 2026-10-31 ?trade=fills&d=YYYY-MM-DD:前幾天的元大成交(電腦每天收盤前存的 fills\日期.json;使用者「先1」:10/30 明細空白要補上)
        const r = await fetch(`${qu}/trade/${tq}` + (tq === "fills" && /^\d{4}-\d{2}-\d{2}$/.test(fd) ? `?d=${fd}` : ""), init);
        const j = await r.json();
        return J(j, r.ok ? 200 : r.status);
      } catch (e) { return J({ ok: false, msg: "電腦沒有回應(可能關機、斷線或元大中繼沒在跑)" }, 504); }
    }

    // ── ?breadth=today|YYYY-MM-DD → 大盤寬度(每分鐘上漲比例),邊緣快取 50 秒 ──
    const bq = url.searchParams.get("breadth");
    if (bq) {
      if (!env || !env.LOGS) return J({ error: "Worker 還沒綁定 KV(LOGS)" }, 503);
      const d = bq === "today" ? ymdOf(twNow()) : bq;
      if (!/^\d{4}-\d{2}-\d{2}$/.test(d)) return J({ error: "breadth 要是 YYYY-MM-DD 或 today" }, 400);
      const since = url.searchParams.get("since") || "";
      if (since && !/^\d{2}:\d{2}$/.test(since)) return J({ error: "since 要是 HH:MM" }, 400);
      const ck = new Request(`${url.origin}/__breadth?d=${d}&since=${since}`), cache = caches.default;
      const hit = await cache.match(ck);
      if (hit) { const h = new Headers(hit.headers); for (const [k, v] of Object.entries(CORS)) h.set(k, v); return new Response(hit.body, { status: 200, headers: h }); }
      let body; try { body = JSON.stringify(since ? await dayBreadth(env, d, since) : await dayBreadthFull(env, ctx, url.origin, d)); } catch (e) { return J({ error: "大盤寬度讀取失敗:" + String(e && e.message || e).slice(0, 200) }, 503); }
      const cached = new Response(body, { status: 200, headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "public, max-age=50" } });
      if (ctx && ctx.waitUntil) ctx.waitUntil(cache.put(ck, cached)); else await cache.put(ck, cached);
      return new Response(body, { status: 200, headers: { ...CORS, "Content-Type": "application/json; charset=utf-8" } });
    }

    // ── 第三條路:?gh=status / ?gh=run&what=daily&key=… ──
    const gh = url.searchParams.get("gh");
    if (gh === "raw") {   // 2026-10-29 repo 改私人:手機 / 中繼 / 看盤版從這裡拿 GitHub 的檔案(見 ghRaw 上方說明)
      const path = String(url.searchParams.get("path") || "").replace(/^\/+/, "").split("?")[0];
      if (!path || path.includes("..") || !/^[\w./\-\u4e00-\u9fff]+$/.test(path)) return J({ error: "path 不對" }, 400);
      const ref = /^[0-9a-f]{7,40}$/.test(url.searchParams.get("ref") || "") ? url.searchParams.get("ref") : GH_REF;
      const full = pushOk || (KI && KI.role === "owner");
      const logic = url.searchParams.get("repo") === "logic";   // 私人庫 Stock-logic:判一二三的程式、模擬器、說明
      const viewerOk = !logic && (path === "app/viewer.html" || path.startsWith("app/apk/viewer/"));
      // 2026-11-02 kvping 的「被拒」紀錄:沒鑰匙的手機(免費 / iPhone 網頁版)、看盤鑰匙讀 pool.json / prob_model.json / log_backfill.json 一直被 403(名單、機率表、舊紀錄出不來)。
      //   這三個檔本來就放在公開庫(任何人都能從 GitHub 直接讀),不算機密 → 不用鑰匙也給讀
      //   使用者「唯一機密的只有判一二三四」→ 公開庫的檔全部不用鑰匙(本來就是公開的);只有私人庫(repo=logic,判一二三四)要 owner 鑰匙 / 中繼金鑰
      const publicOk = !logic;
      if (!full && !publicOk && !(KI && viewerOk)) return J({ error: KI ? "這把鑰匙不能讀這個檔" : "要鑰匙", badKey: !KI }, 403);
      if (!env || !env.GH_TOKEN) return J({ error: "Worker 沒有 GH_TOKEN" }, 503);
      // 2026-10-30 10:34(實際時間)使用者「東台 今天是處置股 為何還會被挑選進來」:pool.json 早上 08:20 起每 15~20 分鐘會被資格複檢改寫(新處置股 / 不能當沖),
      //   這裡原本快取 30 分鐘,手機 08:15~09:50 每 5 分鐘來抓也可能拿到舊的 → pool.json 和 html / js 一樣只快取 60 秒
      // 2026-11-01 使用者重開中繼兩次都沒換到新 VERSION:中繼的檔(VERSION / FILES / *.py)和私人庫(模擬器 / 規則)原本快取 30 分鐘,
      //   剛推的新版要等半小時才抓得到,而且 VERSION 和程式檔可能一新一舊 → 這兩類也只快取 60 秒
      const ttl = ref !== GH_REF ? 86400 : (/\.(html|js)$/.test(path) || /(version|pool|bt_fill|live_entries)\.json$/.test(path) || path.startsWith("tools/yuanta_bridge/") || logic) ? 60 : 1800;
      const r = await ghRaw(env, path, ref, ttl, logic ? GH_REPO_LOGIC : GH_REPO);
      const h = new Headers(r.headers); for (const [k, v] of Object.entries(CORS)) h.set(k, v); h.set("Cache-Control", "no-store");
      return new Response(r.body, { status: r.status, headers: h });
    }
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
      let logicOk = "沒有 GH_TOKEN";
      if (hasToken) { try { const lr = await fetch(`https://api.github.com/repos/${GH_OWNER}/${GH_REPO_LOGIC}`, { headers: { Authorization: "token " + env.GH_TOKEN, "User-Agent": "twse-proxy" } }); logicOk = lr.status === 200 ? "讀得到" : "讀不到 HTTP " + lr.status; } catch (e) { logicOk = "錯誤 " + e; } }
      return J({ GH_TOKEN: hasToken ? "已設定" : "沒有設定", CRON_KEY: hasKey ? "已設定" : "沒有設定(手動觸發關閉)", logic_repo: GH_REPO_LOGIC + " " + logicOk, github,
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
        if (!(await ownerOk())) return J({ error: "寫通知要 owner 鑰匙", badKey: true }, 403);   // 2026-10-29 鑰匙:看盤版 / 外人不能塞假訊號(自動交易會照通知下單)
        let it; try { it = await request.json(); } catch (e) { return J({ error: "body 不是 JSON" }, 400); }
        if (!it || !okDate(it.d) || !/^\d{2}:\d{2}$/.test(it.t || "") || !it.title) return J({ error: "缺 d / t / title" }, 400);
        const item = { t: it.t, title: String(it.title).slice(0, 200), body: String(it.body || "").slice(0, 500), kind: String(it.kind || "").slice(0, 10) };
        if (it.prev != null && isFinite(+it.prev)) item.prev = +it.prev;
        const key = `log:${it.d}:${it.t}:${String(it.key || item.title).replace(/[^\w\u4e00-\u9fff|:.\-]/g, "").slice(0, 120)}`;
        if (it.d >= DO_SINCE && env.HUB) {                        // 2026-10-24 起存 Hub(不用 KV 寫入 / list 額度)
          const res = await hubOf(env).logAdd(it.d, key, item, it.pos ? String(it.pos).replace(/[^\w\u4e00-\u9fff|:.\-]/g, "").slice(0, 80) : "");
          // 2026-11-01 iPhone 網頁版 App:今天的新通知(不是重複的平倉)→ 推播給訂閱的手機(背景做,不拖慢回應)
          if (res && !res.dup && it.d === ymdOf(twNow()) && ctx && ctx.waitUntil) ctx.waitUntil(pushFanout(env, { ...item, key: String(it.key || "") }).catch(() => {}));
          return J(res);
        }
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
      // 2026-10-29 16:00 使用者:「一樣可以有訊號 通知欄一樣會顯示 當沖 明細 都有 只不過是系統的 不是真實的」:試用鑰匙的通知和看盤鑰匙一樣(原本擋今天的,拿掉)
      if (lg === "get") {
        const d = url.searchParams.get("d");
        if (!okDate(d)) return J({ error: "d 要是 YYYY-MM-DD" }, 400);
        // 2026-10-30 00:35 使用者「免費使用者 只有七天可以看到 由程式發出的交易訊息 七天後 系統便不再發給該名免費使用者 任何關於交易的訊息」:
        //   沒鑰匙(免費)= 用手機識別碼 dv 記第一次讀通知的時間(Hub,伺服器時間,手機改時間沒用);滿 7 天後所有日子的通知都回空的,並帶 X-Free-End: 1。
        //   沒帶 dv 的免費請求一律回空的。有鑰匙(owner / 付費 / 試用)、中繼金鑰不受影響
        // 2026-11-01 使用者(免費和試用合併:「免鑰匙 30天使用權 7天交易通知」「第 8~30 天 看圖、看報價,沒有交易通知 除此之外 也能看到平倉後的結果」「30天後也不能使用了」「同手機 再下載第二次也無用」→「改」):
        //   第 1~7 天:照舊全部;第 8~30 天:只回已經平倉的交易(平倉那則 + 它的進場那則),不回進行中的單 / 反彈注意,帶 X-Free-Stage: results(手機不跳通知);
        //   第 31 天起:回空的 + X-Free-End: 1(手機整個畫面蓋上「請輸入鑰匙」)。天數照 dv(Android ID)第一次讀通知算,伺服器時間
        let freeStage = "", freeDay = 0;
        if (!KI && !pushOk) {
          const free = { "X-Free-End": "1" };
          if (!/^[a-f0-9]{16}$/.test(dv) || !env.HUB) return new Response("[]", { headers: { ...CORS, ...free, "Content-Type": "application/json; charset=utf-8" } });
          let first = null; try { first = await hubOf(env).kvGet("free:" + dv); if (!first) { first = Date.now(); await hubOf(env).kvPut("free:" + dv, first); } } catch (e) { first = Date.now(); }
          const age = Date.now() - +first; freeDay = Math.floor(age / 86400e3) + 1;
          if (age > 30 * 86400e3) return new Response("[]", { headers: { ...CORS, ...free, "X-Free-Day": String(freeDay), "Content-Type": "application/json; charset=utf-8" } });
          if (age > 7 * 86400e3) freeStage = "results";
        }
        // 2026-11-01 只給自己(owner 鑰匙 / 中繼)看的:電腦中繼推的提醒(內容結尾「· 元大中繼」:當天虧損 / 賺錢金額、緊急停止、元大拒單、對帳…)
        //   是你帳戶的私事,看盤版 / 試用 / 免費的人讀通知時拿掉(只留判別式的進出場訊號)
        //   使用者「除了我 永久會員以外 能看到我元大買賣的資訊 其他看到的資訊都是程式虛擬交易的狀況與結果」:元大真實買賣也只給自己看 ——
        //   強三(手機強制進場)/ 元三(元大手動單接回)的進出場、任何部位被強平的平倉訊息(內容有「· 強平 ·」),看盤版 / 試用 / 免費都拿掉;判別式的訊號(虛擬交易)照給
        const REAL = /·\s*元大中繼\s*$|·\s*(強三|元三)\s*$|·\s*強平\s*·/;
        // 2026-11-04 強平過的部位,引擎照算的虛擬出場(內容有「· 已強平 ·」):看盤版 / 免費照常看(拿掉「已強平」這幾個字,一進一出),永久版 App 自己藏
        const FCV = /\s*·\s*已強平(?=\s*·)/;
        const mine = pushOk || ownerKI, scrub0 = a => mine || !Array.isArray(a) ? a : a.filter(x => !REAL.test(String((x && x.body) || "")) && !String((x && x.key) || "").startsWith("yt|")).map(x => FCV.test(String(x.body || "")) ? { ...x, body: String(x.body).replace(FCV, "") } : x);
        // 免費第 8~30 天:只留平倉的(kind loss / profit / exit)和配得上的進場(同代碼、同判別式、進場那根時間 = 平倉內容裡的「HH:MM 進」)
        const closedOnly = a => {
          if (!Array.isArray(a)) return a;
          const codeOf = x => ((String(x.title || "").match(/(\d{4,6}[A-Z]*)/) || [])[1]) || "", rsOf = x => ((String(x.body || "").match(/·\s*(判[一二三四])\s*$/) || [])[1]) || "";
          const EX = new Set(["loss", "profit", "exit"]), done = new Set();
          for (const x of a) if (EX.has(x.kind)) { const t = (String(x.body || "").match(/(\d{2}:\d{2}) 進/) || [])[1]; if (t) done.add(codeOf(x) + "|" + rsOf(x) + "|" + t); }
          return a.filter(x => EX.has(x.kind) || ((x.kind === "long" || x.kind === "short") && done.has(codeOf(x) + "|" + rsOf(x) + "|" + ((String(x.body || "").match(/^(\d{2}:\d{2})/) || [])[1] || ""))));
        };
        const scrub = a => freeStage === "results" ? closedOnly(scrub0(a)) : scrub0(a);
        const JL = a => freeStage || freeDay ? new Response(JSON.stringify(a), { headers: { ...CORS, "Content-Type": "application/json; charset=utf-8", ...(freeStage ? { "X-Free-Stage": freeStage } : {}), "X-Free-Day": String(freeDay) } }) : J(a);
        if (d >= DO_SINCE && env.HUB) return JL(scrub(await hubOf(env).logGet(d)));   // 手機每分鐘、自動交易每 3 秒:讀 Hub,不 list
        // 2026-10-27 21:45 DO_SINCE 以前的日子:先看 Hub 有沒有搬過來,有就直接回(不 list);沒有才 list KV 一次、搬進 Hub(10/27 KV 一天 1,000 次 list 被用完過)
        if (env.HUB) { const h = await hubOf(env).logGet(d); if (h && h.length) return JL(scrub(h)); }
        const out = [], rows = []; let cursor;
        try {
        do {
          const r = await env.LOGS.list({ prefix: `log:${d}:`, cursor, limit: 1000 });
          for (const k of r.keys) {
            let it = null;
            if (k.metadata && !k.metadata.big) it = k.metadata;
            else { try { const v = await env.LOGS.get(k.name); if (v) it = JSON.parse(v); } catch (e) {} }
            if (it) { out.push({ ...it, _k: k.name.slice(15) }); rows.push([k.name, it]); }   // _k = 鍵的後半(時間:代碼|標題|判別式),對照 / 刪除用
          }
          cursor = r.list_complete ? null : r.cursor;
        } while (cursor);
        } catch (e) { return J({ error: "KV 今天的 list 額度用完了(台灣時間 08:00 恢復),這天的通知稍後再讀" }, 503); }
        out.sort((a, b) => (b.t || "").localeCompare(a.t || ""));   // 新的在前,和手機本機紀錄一樣
        if (env.HUB && rows.length) { try { await hubOf(env).logImport(d, rows); } catch (e) {} }
        return JL(scrub(out));
      }
      if (lg === "del") {
        if (!(await ownerOk())) return J({ error: "刪通知要 owner 鑰匙", badKey: true }, 403);   // 2026-10-29 鑰匙
        const d = url.searchParams.get("d"), t = url.searchParams.get("t") || "", code = url.searchParams.get("code") || "";
        if (!okDate(d) || !/^\d{2}:\d{2}$/.test(t) || !/^\d{4,6}[A-Z]*$/.test(code)) return J({ error: "要 d=YYYY-MM-DD、t=HH:MM、code=代號" }, 400);
        if (d >= DO_SINCE && env.HUB) return J({ ok: true, deleted: await hubOf(env).logDel(d, `log:${d}:${t}:${code}|`) });
        const r = await env.LOGS.list({ prefix: `log:${d}:${t}:${code}|`, limit: 1000 });
        for (const k of r.keys) await env.LOGS.delete(k.name);
        return J({ ok: true, deleted: r.keys.length });
      }
      if (lg === "days") {
        // 2026-10-27 21:45 不再 list KV(一個執行個體 list 一次,一天 1,000 次額度 10/27 被用完過):KV 只存到 10/24、不會再多 → 寫死;之後的在 Hub
        globalThis.__kvDays = ["2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23", "2026-10-24"];
        const all = new Set(globalThis.__kvDays);
        if (env.HUB) for (const x of await hubOf(env).logDays()) all.add(x);
        return J([...all].sort());
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
    // 2026-10-22 使用者:「手機即時價也要改用元大」:先問元大中繼(格式和證交所一樣);沒登記通道、連不上、超過 1.5 秒、
    //   有要的股票元大沒有、或盤中元大資料超過 60 秒沒更新 → 照舊問證交所。&src=mis 強制問證交所
    if (env && env.LOGS && env.YT_PUSH_KEY && url.searchParams.get("src") !== "mis") {
      try {
        const qu = (await kvGetC(env, "yquote:url", 60000)) || (kvDown() ? EMER_QURL : null);
        if (qu) {
          const r = await fetch(`${qu}/quotes?codes=${codes.join(",")}`, { headers: { "X-Quote-Key": env.YT_PUSH_KEY }, signal: AbortSignal.timeout(1500) });
          if (r.ok) {
            const y = await r.json(), hm = hhmmOf(twNow());
            const live = hm >= "09:00" && hm <= "13:31";
            if (y && Array.isArray(y.msgArray) && !(y.missing || []).length && (!live || (y.age != null && y.age < 60)))
              return new Response(JSON.stringify(y), { status: 200, headers: { ...CORS, "Content-Type": "application/json; charset=utf-8" } });
          }
        }
      } catch (e) { /* 元大通道不通 → 證交所 */ }
    }
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
