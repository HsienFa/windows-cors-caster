# NTRIP Caster Docker 安裝與使用

本文件以目前的 `Dockerfile`、`docker-compose.yml` 與部署腳本為準。容器不會把
`config.ini.example` 當成可執行設定；第一次啟動時，入口腳本會在具名 volume 內建立
小寫 schema 的安全設定檔。

## 安全基準

- `.env`、執行設定、資料庫與日誌都不得提交至 Git。
- 管理員密碼沒有公開預設值。缺少或不安全時，容器會停止。
- Flask secret 由安全工具產生並只寫入容器設定 volume，不會顯示於終端機或日誌。
- Web 的 Docker host publishing 預設只綁定 `127.0.0.1:5757`。
- NTRIP 預設發布於 `0.0.0.0:2101`，因此可被遠端探測及嘗試登入。正式環境必須使用防火牆限制來源，並使用強密碼；若只需本機測試，請把 `NTRIP_PUBLISH_HOST` 設為 `127.0.0.1`。

## 需求

- Docker Engine 24 或更新版本
- Docker Compose v2（`docker compose`）
- Python 3.11，用來安全建立被忽略的 `.env`

## 建議啟動流程

Linux/macOS：

```bash
python3 scripts/deployment_config.py prepare-env --env-file .env --example .env.example
chmod 600 .env
```

在第一次啟動前，以文字編輯器開啟 `.env`，設定只有管理員知道的密碼。不要把內容貼到
終端機、日誌、Issue 或聊天訊息。接著進行靜態檢查並啟動核心服務：

```bash
docker compose config --quiet
docker compose up -d ntrip-caster
```

也可執行互動式流程：

```bash
chmod +x quick-start.sh docker-deploy.sh docker-entrypoint.sh
./quick-start.sh
```

Windows CMD：

```bat
docker-deploy.bat --check
docker-deploy.bat up
```

`docker-deploy.bat up` 會建立被忽略的 `.env` 與必要目錄，不會顯示認證內容。

## 連接埠與監聽

| 服務 | 容器內監聽 | Docker host 預設發布 | 說明 |
|---|---|---|---|
| NTRIP | `0.0.0.0:2101` | `0.0.0.0:2101` | 供 NTRIP 用戶端使用；預設可能對外公開 |
| Web | `0.0.0.0:5757` | `127.0.0.1:5757` | 容器內需接受轉送，但 host 預設僅本機 |
| Nginx | 容器內 HTTP/HTTPS | `127.0.0.1:80/443` | 只在 nginx profile 啟用；公開前須自行加固 |
| Grafana | 容器內服務埠 | `127.0.0.1:3000` | 只在 monitoring profile 啟用 |
| Prometheus | 容器內服務埠 | `127.0.0.1:9090` | 只在 monitoring profile 啟用 |
| Redis | 容器內服務埠 | `127.0.0.1:6379` | 只在 cache profile 啟用；核心服務不依賴 Redis |

若管理介面需遠端存取，建議透過 VPN 或具 TLS、身分驗證與來源限制的反向代理。除非已完成
這些防護，請勿把 `WEB_PUBLISH_HOST` 或 `NGINX_PUBLISH_HOST` 改為公開位址。

## 設定與持久化

- Docker Compose 把執行設定保存於 `ntrip-config` volume 的 `/app/config/config.ini`。
- 資料與日誌分別位於 `ntrip-data`、`ntrip-logs` volume。
- 第一次啟動需要 `.env` 中的管理員認證；入口腳本只在設定檔不存在時建立設定，絕不以公開範例值啟動。
- 已存在的設定不會被自動覆寫。需要變更管理員認證時，請使用管理介面或依維運程序修改受保護的執行設定。

## Monitoring profile

先安全建立 monitoring 所需值，再啟動：

```bash
python3 scripts/deployment_config.py prepare-env --env-file .env --example .env.example --monitoring --profiles monitoring
docker compose --profile monitoring config --quiet
docker compose --profile monitoring up -d
```

Grafana 啟動前會再次檢查認證。空白、範例值、已知預設值或過短的值都會使服務停止。

## 地圖與外連

- 預設 `MAP_PROVIDER=osm`。瀏覽器會向 OpenStreetMap 圖磚服務提出請求。
- 若選用 Google Maps，請把 provider 改為 `google`，並只在被忽略的 `.env` 設定本機 API Key。
- Google Maps 僅使用官方 Maps JavaScript API；缺少金鑰或載入失敗時會回退 OpenStreetMap。
- 使用外部地圖前，請閱讀 [使用條款](TERMS-OF-USE.md)、[隱私權政策](PRIVACY-POLICY.md) 與 [第三方套件聲明](THIRD-PARTY-NOTICES.md)。

## 管理與檢查

```bash
docker compose ps
docker compose logs --tail 100 ntrip-caster
docker compose exec ntrip-caster python /app/healthcheck.py
docker compose down
```

不要把 `.env`、容器執行設定或完整日誌貼到公開位置。若容器第一次啟動即停止，先確認
`.env` 存在且必要認證已安全設定，再使用 `docker compose config --quiet` 檢查 Compose 結構。
