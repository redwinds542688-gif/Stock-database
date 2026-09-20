# Stock-database

名揚四海自動交易 的雲端資料倉庫。GitHub Actions 每個交易日 **14:30(台灣時間)** 自動執行:

```
證交所 OpenAPI(上市)┐
櫃買 OpenAPI(上櫃)  ├─→ make_pool.py ─→ pool.js / pool.json
FinMind 當沖資格     ┘                        ↓
                         fetch_kline.py ─→ kline/{代號}/{年}-W{週}.csv(50 檔,1 分K,只留最近 2 週)
                                              ↓ commit
                              raw.githubusercontent.com(手機可直接 fetch)
```

## 檔案

| 檔案 | 說明 |
|---|---|
| `.github/workflows/daily.yml` | 排程:週一~五 14:30 台灣時間(備援 16:30);也可手動 Run workflow |
| `make_pool.py` | 產生 `pool.js`(給 HTML)與 `pool.json`(給分K下載器)。規則:上市/上櫃普通股(非 ETF)、15~500 元、振幅 ≥ 4%、成交金額 ≥ 3 億、tick ≤ 0.35%、昨日未漲跌停、官方可當沖名單內、非注意/處置股,依成交金額取前 50 檔;設定都在檔案最上面 |
| `fetch_kline.py` | 依 `pool.json` 抓 Yahoo 1 分K,合併進 `kline/` |
| `requirements.txt` | Python 套件 |
| `pool.js` / `pool.json` | 每日自動產生 |
| `kline/{代號}/{年}-W{週}.csv` | 按週拆檔(例 `kline/3481/2026-W38.csv`),欄位 `date,time,open,high,low,close,volume`;**只滾動保留本週 + 上週**,更早的週檔自動刪除(`KEEP_WEEKS` 可調) |
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
- Yahoo 1 分K 只能回溯 7 天;repo 只留最近 2 週,更早的自動刪掉,所以 repo 大小固定在幾 MB。想留更久改 `fetch_kline.py` 的 `KEEP_WEEKS`。
- repo 保持 **Public**:Actions 免費額度無限,而且 raw 網址才有 CORS 允許手機讀。
