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
