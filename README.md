# Stock-database

## 鐵律(2026-10-18 使用者定案,沒有使用者明確指令不得變動)

1. **判別式一已鎖定**:選股、進場、出場、資金的每一條規則和每一個數字(`RULESET`、`SIG` 裡的交易參數、
   `BASE.entryMaxChg`、`YH.confirmByObv` / `YH.exitRule`、make_pool.py 的選股條件)都不可以改,
   包含「順手調整」「回測後覺得更好」「修 bug 時一起改」都不行。
2. 要改任何一條,必須是使用者在對話裡明確說「改 ○○」;改之前要先講清楚會改到什麼、改了之後回測差多少,
   改完要寫進 app.html 最上面的變更紀錄和《判別式一.md》。
3. 想試新邏輯一律另開「判別式二」(複製一份、另外命名、分開回測),判別式一本身不動。
4. 不屬於判別式一的東西(多空天秤、燈號、圖表操作、版面、通知文字、效能)可以改,但不能動到上面第 1 點的任何值。
5. 每次改程式前先確認:這次改動有沒有碰到判別式一?有就停下來問。
6. 判別式一目前鎖定的數字之一:**量比門檻 0.7**(2026-10-18 使用者指令由 1.0 改成 0.7,從此以 0.7 為準)。

名揚四海自動交易 的雲端資料倉庫。GitHub Actions 每個交易日 **14:30(台灣時間)** 自動執行:

```
證交所 OpenAPI(上市)┐
櫃買 OpenAPI(上櫃)  ├─→ make_pool.py ─→ pool.js / pool.json
FinMind 當沖資格     ┘                        ↓
                         fetch_kline.py ─→ kline/{代號}/{年}-W{週}.csv(100 檔,1 分K,滾動保留 3 週(本週 + 前 2 週:第 3 週結束才刪第 1 週))
                                              ↓ commit
                              raw.githubusercontent.com(手機可直接 fetch)
```

## 檔案

| 檔案 | 說明 |
|---|---|
| `.github/workflows/daily.yml` | 排程:週一~五 14:30 台灣時間(備援 16:30、18:30、20:30);也可手動 Run workflow |
| `.github/workflows/recheck.yml` + `recheck.py` | 週一~五 08:45 / 09:05:用**當天**的官方可當沖 / 暫停先賣後買 / 注意股 / 處置股名單覆寫 pool.json 的資格欄位(昨天可當沖今天未必) |
| `make_pool.py` | 產生 `pool.js`(給 HTML)與 `pool.json`(給分K下載器)。規則:上市/上櫃普通股(非 ETF、非金融保險業)、15~500 元、振幅 ≥ 4%、5 日平均振幅 ≥ 4% 且 5 日中至少 3 天振幅 ≥ 4%、tick ≤ 0.35%、昨日未漲跌停、官方可當沖名單內、非注意/處置股,依成交張數取前 100 檔;設定都在檔案最上面 |
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
