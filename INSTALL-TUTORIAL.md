# NTRIP Caster Linux 原生安裝

目前的 `install.sh` 以 Debian／Ubuntu、systemd、64-bit Python 3.11 為目標。其他發行版請依本
文件的安全原則手動調整，勿直接沿用舊版設定範例。

## 安全預設

- NTRIP 監聽 `0.0.0.0:2101`，供遠端用戶端連線；安裝腳本只開放這個 TCP 連接埠。
- Web 管理介面只監聽 `127.0.0.1:5757`，不會由安裝腳本建立公開 HTTP 反向代理。
- 管理員密碼必須由管理員提供；互動模式使用隱藏輸入，沒有公開預設值。
- Flask secret 由安全工具產生，只寫入 `/etc/2rtk/config.ini`，不會顯示於終端機或日誌。
- 執行設定權限限制為服務帳號可讀寫，資料與日誌由非 root 服務帳號管理。

## 需求

- Debian 或 Ubuntu，使用 systemd
- root 權限（只用於安裝系統套件、帳號、目錄、防火牆及 systemd unit）
- 官方套件來源可提供 64-bit Python 3.11
- 可存取專案 Git repository 與 Python 套件來源

## 安裝

```bash
git clone https://github.com/Rampump/NTRIPcaster.git
cd NTRIPcaster
chmod +x install.sh
sudo ./install.sh
```

腳本會以隱藏方式要求輸入管理員密碼並再次確認。非互動安裝則必須由管理員預先在程序環境
提供必要值；不要把值寫在 shell 歷史、部署腳本或公開 CI 設定中。

安裝完成後：

- 程式：`/opt/2rtk`
- 執行設定：`/etc/2rtk/config.ini`（模式 `0600`）
- 資料庫：`/opt/2rtk/data/2rtk.db`
- 日誌：`/var/log/2rtk`
- systemd 服務：`2rtk.service`

`config.ini.example` 只供閱讀，不能直接作為執行設定。安裝腳本使用
`scripts/deployment_config.py` 建立符合目前小寫 section/key schema 的設定。

## 存取與防火牆

NTRIP 對外開放會允許任何可達主機探測服務及嘗試登入。請把防火牆來源限制在必要網段，並
定期檢查帳號、日誌與異常連線。

Web 管理預設只能在伺服器本機使用。遠端維運可先建立 SSH tunnel：

```bash
ssh -L 5757:127.0.0.1:5757 operator@server
```

之後在本機瀏覽器開啟 `http://127.0.0.1:5757`。若需公開管理介面，營運者必須自行部署具
TLS、身分驗證、來源限制及正確 WebSocket 轉送的反向代理；不要以明文 HTTP 直接公開。

## 服務管理

```bash
sudo systemctl status 2rtk
sudo systemctl restart 2rtk
sudo journalctl -u 2rtk --since today
```

不要將設定檔內容或完整日誌貼到公開位置。設定缺少必要認證時，應用程式會拒絕啟動並提供
不含認證值的繁體中文錯誤。

## 地圖服務

原生 Linux 設定預設使用 OpenStreetMap。Google Maps 是選用服務；如要啟用，請只在受保護
的執行設定或程序環境保存 API Key。Google 模式使用官方 Maps JavaScript API，缺少金鑰或
載入失敗時回退 OpenStreetMap。

外部地圖會接收瀏覽器請求及顯示區域資訊。公開部署前請閱讀並完成
[使用條款](TERMS-OF-USE.md)、[隱私權政策](PRIVACY-POLICY.md) 與
[第三方套件聲明](THIRD-PARTY-NOTICES.md)。

## 更新與備份

更新前應備份受保護的執行設定、資料庫與必要日誌，並在維護窗口停止服務。請勿把備份加入
Git。更新程式碼及虛擬環境後，先執行測試與設定驗證，再重新啟動 systemd 服務。
