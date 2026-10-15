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
   ══════════════════════════════════════════════════════ */

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Access-Control-Allow-Headers": "*",
  "Cache-Control": "no-store",
};

export default {
  async fetch(request) {
    if (request.method === "OPTIONS") return new Response(null, { headers: CORS });

    const url = new URL(request.url);

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
