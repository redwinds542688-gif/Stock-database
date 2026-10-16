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
       */5 0-1,6-12 * * MON-FRI → 每 5 分鐘醒來,scheduled() 自己判斷台灣時間:
         08:20、08:45、09:05 → 開盤前資格複檢(recheck.yml);14:30、16:30、20:30 → 每日資料(daily.yml)
     檢查:https://twse-proxy.<帳號>.workers.dev/?gh=status   → 顯示 token 有沒有設好、能不能連到 GitHub
     手動觸發(要另設 Secret CRON_KEY):?gh=run&what=daily&key=<CRON_KEY>  或 what=recheck
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

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Access-Control-Allow-Headers": "*",
  "Cache-Control": "no-store",
};

export default {
  // ── Cloudflare Cron Trigger 到點 → 叫 GitHub 跑對應的 workflow ──
  // Workers 免費方案整個帳號只能有 5 條 Cron(樂透的 Worker 已經用掉),所以這裡只用 1 條:
  //   */5 0-1,6-12 * * MON-FRI  → 每 5 分鐘醒來一次,只在下面這幾個台灣時間真的去叫 GitHub,其他時候什麼都不做
  async scheduled(event, env, ctx) {
    const t = new Date(Date.now() + 8 * 3600e3);                      // 台灣時間
    const key = `${t.getUTCHours()}:${String(t.getUTCMinutes()).padStart(2, "0")}`;
    const SLOTS = { "8:20": "recheck", "8:45": "recheck", "9:05": "recheck", "14:30": "daily", "16:30": "daily", "20:30": "daily" };
    const what = SLOTS[key];
    if (!what) { console.log(`cron ${event.cron} 台灣 ${key}:不是排定時段,略過`); return; }
    const r = await dispatch(env, what);
    console.log(`cron ${event.cron} 台灣 ${key} → ${what}: ${r.msg}`);
  },

  async fetch(request, env) {
    if (request.method === "OPTIONS") return new Response(null, { headers: CORS });

    const url = new URL(request.url);
    const J = (obj, status = 200) => new Response(JSON.stringify(obj, null, 1), { status, headers: { ...CORS, "Content-Type": "application/json; charset=utf-8" } });

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
                 cron: ["*/5 0-1,6-12 * * MON-FRI(一條就好)→ 台灣 08:20/08:45/09:05 複檢,14:30/16:30/20:30 每日資料"],
                 now_taipei: new Date(Date.now() + 8 * 3600e3).toISOString().replace("T", " ").slice(0, 19) });
    }
    if (gh === "run") {
      if (!env || !env.CRON_KEY || url.searchParams.get("key") !== env.CRON_KEY) return J({ error: "key 不對或沒設 CRON_KEY" }, 403);
      return J(await dispatch(env, url.searchParams.get("what") || "daily"));
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
    const ex_ch = codes.map((c, i) => `${(mkts[i] || "tse") === "otc" ? "otc" : "tse"}_${c}.tw`).join("|");
    const target = `https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=${ex_ch}&json=1&delay=0&_=${Date.now()}`;

    try {
      const r = await fetch(target, {
        headers: {
          "User-Agent": "Mozilla/5.0",
          "Accept": "application/json",
          "Referer": "https://mis.twse.com.tw/stock/fibest.jsp",
        },
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
  },
};
