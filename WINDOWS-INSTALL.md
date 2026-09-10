# Windows 11 原生安裝與啟動

本說明適用於不使用 Docker、WSL 或 Linux 的 Windows 11 原生環境。建議使用 **64-bit Python 3.11**；Redis 不是核心服務的必要元件。

## 1. 前置需求

- Windows 11 64-bit
- 64-bit Python 3.11，並包含 Python Launcher (`py`)
- 可使用 PowerShell 或命令提示字元

確認 Python 版本與位元數：

```powershell
py -3.11 -c "import struct, sys; print(sys.version); print(str(struct.calcsize('P') * 8) + '-bit')"
```

輸出應顯示 Python 3.11 與 `64-bit`。

## 2. 建立虛擬環境與安裝 Python 依賴

在專案根目錄開啟 PowerShell：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

啟動腳本不會自動安裝套件，也不會修改系統 Python。

## 3. 準備設定檔

Windows 啟動器固定使用專案根目錄的 `config.windows.local.ini`，而且不會退回使用 `config.ini`。若本機設定檔不存在，請由安全範例建立：

```powershell
Copy-Item config.ini.example config.windows.local.ini
```

接著編輯 `config.windows.local.ini`：

- 將 `security.secret_key` 替換為使用 Python `secrets` 產生、至少 32 個字元的隨機值。
- 將 `admin.password` 替換為僅供本機使用的強密碼。
- 不得保留範例佔位值或已知預設密碼，否則程式會拒絕啟動。

`config.windows.local.ini` 已加入 `.gitignore`，不得強制加入 Git，也不要把實際密鑰或密碼貼到文件、問題回報或日誌。

相對路徑會固定以專案根目錄為基準。例如：

```ini
[database]
path = data/2rtk.db

[logging]
log_dir = logs
```

以上會分別解析為 `<專案根目錄>\data\2rtk.db` 與 `<專案根目錄>\logs`。程式啟動時會建立缺少的資料夾。

### 選用 Google Maps

預設地圖是 OpenStreetMap，不需要 Google API Key。若只在公司內部離線或受限網路環境測試，請維持 `provider = osm`。

若要使用 Google Maps JavaScript API，請只在已被 Git 忽略的 `config.windows.local.ini` 加入或修改下列欄位：

```ini
[map]
provider = google
google_maps_api_key = YOUR_DEMO_KEY
default_latitude = 23.7
default_longitude = 121.0
default_zoom = 7
```

也可以在啟動程式的同一個 PowerShell 視窗設定環境變數；環境變數會覆蓋本機設定檔：

```powershell
$env:GOOGLE_MAPS_API_KEY = 'YOUR_DEMO_KEY'
.\start-windows.ps1
Remove-Item Env:GOOGLE_MAPS_API_KEY
```

- Demo Key 僅供測試，不應用於正式環境。
- 請在 Google Cloud 將瀏覽器金鑰限制為 Maps JavaScript API，並設定 HTTP referrer，例如 `http://127.0.0.1:5757/*` 與 `http://localhost:5757/*`。
- 瀏覽器端 Maps JavaScript API 的金鑰必須隨官方 script 請求送到 Google，因此可在瀏覽器開發者工具看到；安全性應依靠 API 與網站來源限制，而不是把金鑰寫入 Git。
- 金鑰空白、仍是 `YOUR_DEMO_KEY`、Google 載入失敗或驗證失敗時，管理介面會自動使用 OpenStreetMap。
- Google 模式提供一般地圖、衛星、混合及地形四種官方模式。
- 本專案不會透過 OpenLayers 擷取 Google 圖磚，也不使用非官方 Google 圖磚網址。

Google 官方參考：[載入 Maps JavaScript API](https://developers.google.com/maps/documentation/javascript/load-maps-js-api)、[API Key 安全建議](https://developers.google.com/maps/api-security-best-practices)。

啟用外部地圖前，請閱讀本專案的[使用條款初稿](TERMS-OF-USE.md)與[隱私權政策初稿](PRIVACY-POLICY.md)。這兩份文件未經律師審核；實際營運者公開部署前必須填入自己的公司名稱、聯絡方式、資料保存期限及適用地區。管理介面不會遮蔽或修改 Google 地圖自帶的標誌、版權、條款與 attribution。

## 4. 啟動服務

PowerShell：

```powershell
.\start-windows.ps1
```

命令提示字元或雙擊：

```bat
start-windows.bat
```

兩個啟動器都不接受自訂設定檔參數，並且只會使用 `config.windows.local.ini`。若要在 CMD 中只檢查必要檔案是否存在而不啟動服務，可執行：

```bat
start-windows.bat --check
```

預設服務位址：

- Web 管理介面：`http://localhost:5757`
- NTRIP 服務：`localhost:2101`

按 `Ctrl+C` 可停止前景服務。

## 5. Windows 防火牆

Windows 本機設定預設且建議維持 `127.0.0.1`，因此不需要建立對外防火牆規則。只調整防火牆
並不會讓服務對外監聽；若日後確有遠端連線需求，應另外進行威脅評估，明確修改本機設定，
並同時配置來源限制、TLS／VPN 與強認證。不要為了方便而直接公開 Web 管理介面。

## 6. 健康檢查

先啟動主服務，再開另一個終端執行：

```powershell
.\.venv\Scripts\python.exe healthcheck.py --json
```

健康檢查使用 `psutil` 取得記憶體與專案所在磁碟資訊，不依賴 `/proc` 或 `/app`。

## 7. 相容性測試

不啟動 NTRIP 或 Web 服務的單元測試：

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p "test_windows_compat.py" -v
```

### Socket.IO 套件更新驗收紀錄（2026-09-08）

- 已合併並推送 main。Feature commit：`7d8fa615e72b7dc60ae3076062631bb8ad4eaf20`；main squash commit：`b7e26b5a5a9faa1c265ea6f20b663874e7a2a6c7`。
- 前批本機 199 項通過，是各測試檔獨立執行結果的加總；不是一次整合執行，也不是本批重新驗證的結果。Feature CI #12 三個工作成功；CI 個別測試的 skip 數量未獨立核對。
- **使用者回報**：隔離瀏覽器純 polling 持續 130 秒，8 次 ping、8 次 PONG 排入送出、43 次更新，沒有斷線、Engine 關閉或升級；模擬離線後自行重連並恢復更新，未重新整理或手動呼叫 `connect()`。
- **使用者回報**：正式電腦更新程式與套件，安裝未失敗；重啟服務後移動站可固定解，管理頁可登入且時間持續更新。正式電腦的完整 SHA、實際套件版本及 `pip check` 完整輸出未全部回傳，未由 Agent 獨立核實。
- 以上是該套件更新的基本驗收，不代表所有安全事項已完成。匿名 Web 資料存取與登出撤銷另批處理；NTRIP 握手總期限、慢接收端／forwarder 隔離仍屬後續待辦。

### Web 資料存取修正的隔離驗證與限制

本批已完成下列開發電腦隔離驗證，尚未部署；不能視為正式環境已完成驗收：

- 公開首頁只提供明列的系統／連線統計及掛載點摘要；STR、位置、設備、RTCM 解析詳細資料與管理日誌需有效管理員權限。公開 HTML 不輸出設定的地圖中心或 Google 地圖載入設定。
- 同一次登入的分頁共用可撤銷登入識別；HTTP 登出撤銷該登入的全部 Socket.IO 管理權限，其他獨立登入不受影響。保留公開連線，不停止全域 parser。
- 登入識別及連線對應只存在單一 `WebManager` 程序的記憶體。沿用目前單程序、threading 模式；不支援多 worker／多程序之間共用撤銷狀態。服務重啟或升級前留下的登入狀態需重新登入。
- 登入時清除舊 session，未設定 `session.permanent = True`，因此 cookie 沒有固定的瀏覽器到期日期，不能宣稱瀏覽器一定保存 31 天。伺服器另以登入當下的 `time.monotonic()` 加上 Flask `permanent_session_lifetime` 建立固定期限；專案未覆寫此設定，Flask 3.0.3 預設為 31 天。HTTP 活動與 Socket.IO 重連不延長這個期限；Flask 收到 cookie 時也會檢查簽章時間上限。
- 斷線清除連線對應，登出／到期刪除登入紀錄。有效登入紀錄上限為 1024，達上限時新登入回覆 503，不踢除其他有效登入；使用原登入重新登入時先撤銷原紀錄，再建立替代紀錄。
- 權限撤銷與詳細推送排入送出共用鎖；登出完成後不再排入新的管理訊息。已在登出前送出或排入傳輸佇列的資料無法追回，也不清除瀏覽器原已顯示的資料。
- 前輪相關測試共 108 項通過；聚焦修正後另執行 Web 測試檔 `tests/test_rover_web_api.py`，33 項通過、0 失敗、0 skip。兩者是不同階段且範圍重疊的結果，不能相加。本次補紀錄未重跑程式測試。
- **Agent 實際操作（2026-09-10）**：Windows Chrome 152 真實瀏覽器搭配既有隔離 Python 3.11.9 環境，四個未提交檔案與隔離副本逐一比對一致。資料庫、解析器及資料來源使用非空合成替身，僅連線本機隔離 Web 後端；不是設備端到端驗證。
- 公開首頁摘要持續更新，詳細 HTTP API 回覆 401，公開事件未出現合成敏感標記；管理員可取得 STR、解析資料、管理日誌及地圖設定，兩個詳細查詢保留成功回覆事件與空參數 ACK。匿名新頁面不輸出設定座標。
- 純 polling 持續 179 秒，89 次摘要更新、12 次 ping／PONG，斷線、Engine 關閉及傳輸升級均為 0。隔離代理中斷傳輸 12 秒後，記錄 1 次斷線、4 次重連嘗試，瀏覽器自行恢復更新，未重新整理或手動呼叫 `connect()`。
- 同次登入跨分頁登出後，保留連線收不到其後新產生的管理詳細資料，詳細 HTTP API 為 401，事件與 ACK 回覆未授權；獨立登入不受影響。撤銷後舊連線重連僅取得公開摘要，重新登入後詳細功能恢復。合成登入到期後，持續連線同樣無法取得新管理資料；監控頁在登出或到期後導向登入畫面，公開首頁仍可使用。
- 隔離程序已停止，測試連接埠無 LISTENING 紀錄；未操作正式服務、安裝套件或接入基站、移動站及上游。
- 驗證限制：外部地圖圖磚刻意禁止連線，未驗證底圖下載；到期以合成方式推進伺服器期限，未實際等待完整期限；本輪瀏覽器未重放舊 cookie。上述結果不代表實際設備或正式部署已驗收，也不代表所有安全問題已解決。
- 後續待辦：正式部署與設備驗收另行授權；`/classic` 缺少模板、NTRIP 握手總期限、慢接收端／forwarder 資源隔離不納入本批。多程序撤銷狀態與登出前已排入佇列資料的限制仍如上所述。

## 8. 常見問題

### 找不到 `.venv` 的 Python

確認已在專案根目錄執行 `py -3.11 -m venv .venv`，且安裝的是 64-bit Python 3.11。

### 連接埠已被占用

```powershell
Get-NetTCPConnection -LocalPort 2101,5757 -ErrorAction SilentlyContinue
```

請停止占用程式，或調整 `config.windows.local.ini` 中的 `[ntrip] port` 與 `[web] port`。

### 無法建立資料庫或日誌

確認目前帳號對專案目錄具有寫入權限，並避免把專案放在受保護的系統目錄。

### 是否需要 Redis

不需要。核心 Python 程式使用 SQLite 與記憶體內緩衝區；Redis 只存在於可選的容器部署設定。
