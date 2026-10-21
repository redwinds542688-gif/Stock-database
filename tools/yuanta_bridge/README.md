# 元大 SPARK API 中繼程式

2026-10-22 使用者:「如何讓程式接上元大的 api」→「先寫好 等營業員回覆」。

盤中登入元大 SPARK API,訂閱名單上每一檔的**逐筆成交**,拼成精準的 1 分 K(開高低收、量都和元大看盤軟體一致),
每分鐘送到 Cloudflare Worker。**預設只接行情**;另有「一次一單」自動交易(`trader.py`,預設關閉,見下面)。

```
元大 SPARK API ──逐筆成交──▶ 中繼程式(Linux 主機)──每分鐘 1 分 K──▶ Worker(?ybars=push)──▶ 手機(之後切換)
```

## 檔案

| 檔案 | 用途 |
|---|---|
| `yuanta_bridge.py` | 主程式:`run` 常駐、`test 2330` 驗證連線(印逐筆 60 秒)、`kline 2330 2026/10/22` 查元大 1 分 K |
| `trader.py` / `test_trader.py` | 一次一單自動交易(跟手機通知進出場;`python3 test_trader.py` 測 11 種情況) |
| `bars.py` / `test_bars.py` | 逐筆 → 1 分 K(不需要元大元件,`python3 test_bars.py` 可單獨測) |
| `bridge.env.example` | 設定範本(複製成 `bridge.env`,**只放在主機上**,已被 .gitignore 排除) |
| `install.sh` | Ubuntu 24.04 一鍵安裝(.NET 8、Python venv、pythonnet 3.0.5,照元大文件) |
| `yuanta-bridge.service` | systemd 服務:開機自動跑、當掉自動重啟 |

## 要先準備的(營業員回覆後)

1. **API 權限**:請營業員開通 SPARK API(測試 + 正式)。測試環境要先登記主機的**固定 IP**;正式環境是否也要,問營業員。
2. **SDK 元件包**:向元大取得 YuantaSparkAPI 元件(含 `.dll`、Linux 用 `.so`),解壓縮到 `/opt/yuanta_bridge/sdk`。
3. **憑證**:測試憑證在元大文件「測試環境 & 正式環境說明」頁下載(密碼 `yuanta`);正式憑證用自己的網路下單憑證(.pfx)。
4. **主機**:Ubuntu 24.04、盤中不關機,建議租固定 IP 的 Linux 雲端主機。

## 最快的第一步:Windows 上用元大的測試程式(不用寫程式)

元大給的範例程式包裡有 `YuantaAPI_TestAP.exe`(元大文件「環境建置-C#」):
1. 電腦裝 .NET 8(https://dotnet.microsoft.com/zh-tw/download/dotnet/8.0)
2. 匯入測試憑證(憑證密碼 `yuanta`)
3. 開 `YuantaAPI_TestAP.exe` → 帳號種類 `S`、帳號 `98875005091`、密碼 `1234`、選「測試環境」→ 按 Open → 按 Login
4. 收到連線和登入成功訊息 = 你的 API 權限、網路(固定 IP)都通了,再來裝 Linux 主機
   (正式環境:帳號換成自己的 11 碼證券帳號、選「正式環境」,憑證用自己的網路下單憑證)

範例程式包裡的 `NuGetPackages` 資料夾就是 SDK 元件(YuantaSparkAPI 套件),Linux 主機要的 `.dll` / `.so` 從這裡取。

## 安裝與測試

```bash
sudo bash install.sh                       # 裝環境(/opt/yuanta_bridge)
# 放 SDK 到 /opt/yuanta_bridge/sdk、憑證到 /opt/yuanta_bridge/cert,編輯 /opt/yuanta_bridge/bridge.env
sudo -u yuanta /opt/yuanta_bridge/venv/bin/python /opt/yuanta_bridge/yuanta_bridge.py test 2330
```

`test` 會連線、登入、訂閱 2330,印 60 秒逐筆成交。確認:登入回 `0001`、有逐筆進來、成交量單位(張或股,決定 `VOL_MULT`)。

## Worker 設定

在 Cloudflare Worker(twse-proxy)→ Settings → Variables and Secrets 加一個密鑰 **`YT_PUSH_KEY`**(自己產生一串亂碼,例如 `openssl rand -hex 24`),
同一串填到主機 `bridge.env` 的 `PUSH_KEY`。

- `POST ?ybars=push`(帶 `X-Push-Key`):中繼程式送 1 分 K,一分鐘一個 KV 鍵 `ybar:日期:時分`
- `GET ?ybars=today`(或 `YYYY-MM-DD`,可加 `&codes=2330,2409&since=10:00`):讀回來,格式同 `?bars`

## 上線順序

1. 測試環境(UAT)用元大公開測試帳號跑 `test`,確認程式和元件正常
2. 正式環境(PROD)用自己的帳號跑 `run` 一天,收盤後拿 `?ybars=` 和 `?bars=`、元大看盤軟體逐分鐘比對
3. 準了再改手機讀 `?ybars=`(那時再改 app.html;雲端快照可以停掉,省 KV 寫入)

## 限制(元大文件「使用限制說明」)

- 單次訂閱最多 200 檔、一秒最多 10 次;每條連線訂閱上限 2,000 檔
- 登入失敗後每 4 秒最多重試 1 次、一天最多登入 1,000 次(程式斷線重連間隔 10~60 秒)
- 呼叫超量會暫停 1 分鐘;一小時被暫停 10 次會停用帳號的 API

**安全**:帳號、電子密碼、憑證密碼只放在主機的 `bridge.env`(權限 600),不要貼到對話或 GitHub。

## 一次一單自動交易(trader.py)

2026-10-22 使用者:「真的接通 可以先試 一次只掛一單 成交後才能繼續」→「先寫好 預設測試環境」。

- **訊號**:手機的通知(雲端紀錄 `?log=get`,每 3 秒看一次)。進場通知 → 進場;同一檔的平倉通知 → 出場。啟動時當天已有的通知不算。
- **一次一單**:空閒 → 進場中 → 持倉中 → 平倉中 → 空閒;不是空閒時其他訊號一律不接(只記錄)。
- **快速出場**:通知裡的停利 / 停損價由程式用元大逐筆成交盯,碰到就送平倉單(約 1 秒內);13:25 一律平倉。
- **下單方式**:進場 = 限價 + IOC(讓 `TRADE_SLIP_TICKS` 檔,沒成交就不追)。
  **平倉 = 空單買回掛漲停、多單賣出掛跌停(ROD)**(使用者:「直接掛漲停價贖回,不管股價怎麼跑都會在第一時間平倉」):
  照價格優先從最好的價位開始成交,實際成交價 = 當下市價;用 ROD 是因為價格穩定措施(暫緩撮合)時 IOC / 市價會失效,ROD 會留著等撮合。
- **進場當下預掛停損(元大條件單)**(使用者:「在股票交易啟動當下 直接預掛停損是最好方式」):進場成交後馬上在元大主機掛**二擇一條件單(OCO)**——
  停損、停利兩個成交價條件,哪個先到就送漲停買回 / 跌停賣出(ROD),另一個取消。程式或網路斷了元大照樣平倉。
  OCO 在的時候,程式自己的停利停損只記錄不下單;程式因為手機平倉通知 / 13:25 要平倉時,**先刪 OCO 再送平倉單**;
  萬一兩邊同時成交(多平了),程式偵測到馬上反向補回。OCO 掛失敗就改由程式自己盯。`TRADE_BROKER_STOP=0` 可關掉。
  **上線前要和元大確認**:條件單的「現股買進」能不能沖銷當沖賣出的空單(先賣後買)。
- **以元大回報為準**:即時回報 51(成交)才算成交,用自訂編號(BasketNo)對單。
- **保護**:每筆張數、一天筆數、當天虧損上限、現價離訊號價太遠不追、`touch /opt/yuanta_bridge/STOP` 緊急停止(不再進場,有部位照樣平)。
- **預設關閉**:`TRADE_ENABLE=1` 才會下單;正式環境還要 `TRADE_ALLOW_PROD=1`。

上線順序:測試環境 `order-test` 試下單回應 → 測試環境 `TRADE_ENABLE=1` 跑幾天(元大測試環境依委託書號尾碼模擬全成交 / 不成交 / 部分成交 / 價穩失效)
→ 正式環境 1 張、一天 3 筆跑一兩週。**做空的委託種類(`TRADE_TYPE_SHORT`)上線前要和元大確認**(先賣後買要有當沖資格)。

限制:手機通知經過雲端紀錄,KV 列表最慢可能延遲約一分鐘,所以「進場」可能比通知晚;「出場」的停利停損由這裡即時盯,不受影響。
