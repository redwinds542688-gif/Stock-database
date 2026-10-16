# 讓 Cloudflare 準時叫 GitHub 跑爬蟲(設定一次就好)

GitHub 自己的排程常拖 1~5 小時(10/16 的 14:30 根本沒跑),Cloudflare 的 Cron 很準。
做法:Cloudflare 到點 → 通知 GitHub → GitHub 立刻跑。總共三步,手機就能做。

## 第 1 步:在 GitHub 做一把「鑰匙」(token)
1. 手機瀏覽器開 https://github.com/settings/personal-access-tokens/new
   (或:GitHub 右上角頭像 → Settings → 最下面 Developer settings → Personal access tokens → Fine-grained tokens → Generate new token)
2. Token name:`cloudflare-cron`
3. Expiration:選最長(1 年;到期前 GitHub 會寄信提醒,再做一把換掉)
4. Repository access:選 **Only select repositories** → 勾 `Stock-database`
5. Permissions → Repository permissions → 找 **Actions** → 選 **Read and write**(其他都不用動)
6. 最下面 **Generate token** → 畫面會出現一串 `github_pat_…`,**複製起來**(只會顯示這一次)

## 第 2 步:把新的 worker.js 貼到 Cloudflare
1. 開 https://dash.cloudflare.com → Workers & Pages → 點 **twse-proxy**
2. 右上 **Edit code**(或「編輯程式碼」)
3. 全選刪掉,貼上我給的 `worker.js` 全部內容 → 右上 **Deploy**

## 第 3 步:設定鑰匙與排程
1. 回到 twse-proxy → **Settings**(設定)
2. **Variables and Secrets** → Add:
   - Type:**Secret**
   - Variable name:`GH_TOKEN`
   - Value:貼第 1 步的 `github_pat_…`
   - Deploy / Save
3. 同一頁 **Triggers**(觸發程序)→ **Cron Triggers** → Add,三條各加一次(照打,含空格):
   - `20,45 0 * * MON-FRI`
   - `5 1 * * MON-FRI`
   - `30 6,8,12 * * MON-FRI`
   (Cloudflare 的星期 1 = 星期日,所以要用 MON-FRI;時間是 UTC,換成台灣時間 = 08:20、08:45、09:05 複檢,14:30、16:30、20:30 每日資料)

## 檢查有沒有成功
手機開這個網址:
https://twse-proxy.redwinds542688.workers.dev/?gh=status

看到 `"GH_TOKEN": "已設定"` 和 `"github": "連線正常(token 有效)"` 就完成了。
之後每個交易日 14:30 GitHub 的 Actions 頁面會準時多一筆「每日台股資料」,08:20 多一筆「開盤前資格複檢」。

## (選用)想從手機手動叫它跑
Variables and Secrets 再加一個 Secret:`CRON_KEY`,值自己隨便打一串(例如 `mysh8888`)。
之後開
https://twse-proxy.redwinds542688.workers.dev/?gh=run&what=daily&key=mysh8888
就會立刻叫 GitHub 跑每日資料;`what=recheck` 是複檢。

## 注意
- GitHub 原本的排程我沒拿掉,當備援;同一天跑兩次沒有副作用(資料一樣就不會重複 commit)。
- token 到期(1 年後)要重做第 1 步,再到 Cloudflare 把 GH_TOKEN 換成新的。
