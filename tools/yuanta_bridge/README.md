# 元大 SPARK API 中繼程式

2026-10-22 使用者:「如何讓程式接上元大的 api」→「先寫好 等營業員回覆」。

盤中登入元大 SPARK API,訂閱名單上每一檔的**逐筆成交**,拼成精準的 1 分 K(開高低收、量都和元大看盤軟體一致),
每分鐘送到 Cloudflare Worker。**只接行情,不下單。**

```
元大 SPARK API ──逐筆成交──▶ 中繼程式(Linux 主機)──每分鐘 1 分 K──▶ Worker(?ybars=push)──▶ 手機(之後切換)
```

## 檔案

| 檔案 | 用途 |
|---|---|
| `yuanta_bridge.py` | 主程式:`run` 常駐、`test 2330` 驗證連線(印逐筆 60 秒)、`kline 2330 2026/10/22` 查元大 1 分 K |
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
