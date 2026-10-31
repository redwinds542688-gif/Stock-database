> 2026-10-29:判一二三(App 規則 judge.js、模擬器 tools/backtest、判別式*.md)已搬到私人庫 `Stock-logic`;這裡只剩主程式、K 線、名單、Worker、Android。見 交接說明.md。

# Stock-database

## 鐵律(2026-10-18 使用者定案,沒有使用者明確指令不得變動)

> 2026-10-31 使用者「搬公開庫的舊判別式說明」:判別式相關的鐵律(原第 1~10、12~16 條)搬到私人庫 Stock-logic《公開庫舊說明_20261031.md》;這裡只留和判別式無關的。

11. **版面與顯示已定案,沒有使用者指令不得變動**(2026-10-19 使用者指令「寫進鐵律 顯示沒有指令不可改變 設定裡也一樣最大化 一樣寫進鐵律」):通知卡片的字級(由最擠那一列塞滿卡片寬度決定,上限 40px)、欄位順序與寬度、卡片網底與顏色、警示欄;設定面板六個滾輪的標題與數字(文字最大化)、「手續折扣」等字樣;股票清單 30 格字級;標題列彩色漸層;按鍵列五顆與燈號 — 以上全部維持現狀,只有使用者明確說「改 ○○」才動,改了一樣要記錄。
17. **一支股票只有「進場」「出場」兩則訊息**(2026-10-20 使用者:「記住一支股票就只有進場跟出場兩則訊息,當然不同的判別一樣也是一進一出」):同一檔、同一份判別式,一個部位只通知一次進場、一次平倉;部位還在時再出現的進場訊號不通知,平倉通知過就不再通知;注意 / 反彈不再是訊息(2026-10-20 使用者「反彈跟注意取消」→「改顯示在監控欄位的股票下方監控中那邊」):不推手機、不在通知欄出現,只記雲端,股票清單那格顯示「反彈中」「請注意」。通知欄也只顯示這兩張卡。判一、判二各自算,所以同一檔最多四張。 2026-10-20 15:00 起以「部位帳本」實作:進場通知後記住進場價,停利 / 停損 / 13:25 用進場價盯盤(盤中 K 改寫、訊號消失也會平倉),反向進場先平舊部位,帳本存 localStorage,雲端紀錄以部位鍵去重(兩支手機同一部位只留一則)。

名揚四海自動當沖 的雲端資料倉庫。GitHub Actions 每個交易日 **14:30(台灣時間)** 自動執行:

```
證交所 OpenAPI(上市)┐
櫃買 OpenAPI(上櫃)  ├─→ make_pool.py ─→ pool.js / pool.json
FinMind 當沖資格     ┘                        ↓
                         fetch_kline.py ─→ kline/{代號}/{年}-W{週}.csv(100 檔,1 分K,滾動保留 3 週(本週 + 前 2 週:第 3 週結束才刪第 1 週))
                                              ↓ commit
                              raw.githubusercontent.com(手機可直接 fetch)
```

18. **任何修改都不可以影響其他服務的使用量與免費額度**(2026-10-23 使用者:「所有的修改不可以影響其他程式的使用量額度等等 像github cloudflare...等等」):
   Cloudflare Worker 請求(10 萬次/天)、KV 寫入(1,000 次/天)、KV 讀取(10 萬次/天)、KV list(1,000 次/天)、GitHub raw / Actions、證交所抓取頻率。
   要加任何會多打請求、多寫 KV、多抓資料的東西,先算一天會多多少、現在用到多少、還剩多少,超過或接近就不做或換做法(例:2026-10-23 手機每分鐘叫 Worker 存快照會多 270 次 KV 寫入 → 改成 Cron 那分鐘不重寫)。
   目前用量(2026-10-23):KV 寫入約 880/天、KV 讀取約 2.5 萬/支手機/天、Worker 請求約 2 萬/支手機/天、GitHub raw 名單每 5~10 分鐘一次(只在換名單時段)。

19. **所有文字都在顯示範圍內最大化**(2026-10-27 使用者:「所有文字都在顯示範圍內最大化 寫進鐵律 所有新創的功能 與舊功能 都要照此執行」):
   任何畫面、任何欄位的文字,字級都要放大到剛好塞滿它的顯示範圍(寬度或高度先碰到為止),不留多餘空白、也不能被切掉或折行;
   同一列 / 同一張表的欄位用同一個字級(取最擠那格算出來的),欄位才會對齊。新做的功能一開始就要這樣做,舊功能發現沒做到就要補上(通知欄卡片 `fit()`、當沖表「字級 = 2 欄寬」是現成的做法)。

## 檔案

| 檔案 | 說明 |
|---|---|
| `.github/workflows/daily.yml` | 排程:週一~五 14:30 台灣時間(備援 16:30、18:30、20:30);也可手動 Run workflow |
| `.github/workflows/recheck.yml` + `recheck.py` | 週一~五 08:45 / 09:05:用**當天**的官方可當沖 / 暫停先賣後買 / 注意股 / 處置股名單覆寫 pool.json 的資格欄位(昨天可當沖今天未必) |
| `make_pool.py` | 產生 `pool.js`(給 HTML)與 `pool.json`(給分K下載器)。規則:上市/上櫃普通股(非 ETF、非金融保險業)、15~500 元、昨日成交 ≥ 6000 張(2026-10-21 起)、振幅 ≥ 3%、5 日平均振幅 ≥ 3%(2026-10-21 由 4% 改,不再要求 5 天中 3 天達標)、tick ≤ 0.5%(2026-10-19 由 0.35 放寬)、昨日未收跌停(漲停 2026-10-19 起放行)、官方可當沖名單內、非注意/處置股,依成交張數取前 130 檔;設定都在檔案最上面 |
| `fetch_kline.py` | 依 `pool.json` 抓 Yahoo 1 分K,合併進 `kline/` |
| `worker.js` | Cloudflare Worker:盤中即時行情轉接站(證交所 mis.twse.com.tw → 加 CORS 回傳手機)。部署到 Cloudflare,不在 Actions 跑 |
| `requirements.txt` | Python 套件 |
| `pool.js` / `pool.json` | 每日自動產生 |
| `amp_history.json` | 全市場每日振幅,滾動保留 15 個交易日(算 5 日平均振幅用) |
| `kline/{代號}/{年}-W{週}.csv` | 按週拆檔(例 `kline/3481/2026-W38.csv`),欄位 `date,time,open,high,low,close,volume`;**滾動保留 3 週(本週 + 前 2 週:第 3 週結束才刪第 1 週)**,更早的週檔自動刪除(`KEEP_WEEKS` 可調) |
| `kline/index.json` | 每檔的名稱、市場、資料起迄日、筆數 |

## 手機讀取網址

```
https://raw.githubusercontent.com/redwinds542688-gif/Stock-database/main/pool.js
https://raw.githubusercontent.com/redwinds542688-gif/Stock-database/main/pool.json
https://raw.githubusercontent.com/redwinds542688-gif/Stock-database/main/kline/3481/2026-W38.csv
https://raw.githubusercontent.com/redwinds542688-gif/Stock-database/main/kline/index.json
```

## Secrets

`Settings → Secrets and variables → Actions → New repository secret`

| 名稱 | 內容 |
|---|---|
| `FINMIND_TOKEN` | FinMind token(沒設也能跑,只是不會標記可當沖/處置股) |

## 注意

- GitHub 排程有時會延遲幾分鐘到幾十分鐘,不是故障。
- Yahoo 1 分K 只能回溯 7 天;repo 保留本週 + 前 2 週(約 12 MB),更早的自動刪掉。想留更久改 `fetch_kline.py` 的 `KEEP_WEEKS`。
- repo 保持 **Public**:Actions 免費額度無限,而且 raw 網址才有 CORS 允許手機讀。
