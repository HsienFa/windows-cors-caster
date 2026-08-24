# 第三方套件聲明

本專案將下列瀏覽器端套件固定版本保存在 `static/vendor/`，使管理介面的基本功能不必在執行階段從 CDN 下載資源。檔案均直接取自官方 npm Registry 發行封包，未修改內容。

## OpenLayers

- 套件名稱：`ol`
- 版本：`8.2.0`
- 來源：[官方 npm 套件頁](https://www.npmjs.com/package/ol/v/8.2.0)
- 發行封包：`https://registry.npmjs.org/ol/-/ol-8.2.0.tgz`
- 授權：BSD 2-Clause
- 發行封包 SHA-256：`1af6370001ae473d5cfc85095a3c66637590a966500461d388f8a143cb2891d4`
- 授權全文：`static/vendor/openlayers/8.2.0/LICENSE.md`

| 本機檔案 | SHA-256 |
| --- | --- |
| `static/vendor/openlayers/8.2.0/ol.js` | `ae5e487a52b7fdc7167dce953f3a3968d305053051e380751d8bdb154d9bba6d` |
| `static/vendor/openlayers/8.2.0/ol.css` | `b46a588ec4f9db4f824ea15ab2b78bd9d1dfb17172a785c69e23fa8953db437f` |
| `static/vendor/openlayers/8.2.0/LICENSE.md` | `6c4347b83a8c9feef18d57b18e3b6c44cf901b3c344a4a1fbd837e421555ab8e` |

## Socket.IO Client

- 套件名稱：`socket.io-client`
- 版本：`4.0.1`
- 來源：[官方 npm 套件頁](https://www.npmjs.com/package/socket.io-client/v/4.0.1)
- 發行封包：`https://registry.npmjs.org/socket.io-client/-/socket.io-client-4.0.1.tgz`
- 授權：MIT
- 發行封包 SHA-256：`4e4750bc3cdd58549dbb2085cc6e6605974a28bb4542210bf892e01b07b321c9`
- 授權全文：`static/vendor/socket.io-client/4.0.1/LICENSE`

| 本機檔案 | SHA-256 |
| --- | --- |
| `static/vendor/socket.io-client/4.0.1/socket.io.min.js` | `e8da407a321da9d28520d362f6202b458b1f5718240de5d47ab5dbc8911842e7` |
| `static/vendor/socket.io-client/4.0.1/LICENSE` | `62e2032a1e1458b1d92a62f5fc51be48e08b95062295c91a9f3bd3686809d37e` |

## 選用的執行階段地圖服務

### Google Maps JavaScript API

- 供應者：Google Maps Platform
- 官方載入來源：`https://maps.googleapis.com/maps/api/js`
- 使用條件：只有 `map.provider = google` 且本機提供非空白、非範例 Google API Key 時才載入。
- 授權與服務條款：[Google Maps Platform Terms of Service](https://cloud.google.com/maps-platform/terms)
- 本機保存：未保存 Google JavaScript、圖磚或其他 Google 地圖內容，因此沒有可記錄的本機檔案 SHA-256。
- 備援：API 載入或驗證失敗時，自動切換至本機 OpenLayers 搭配 OpenStreetMap。
- 資料範圍：瀏覽器會把地圖中心及基站座標交給 Google Maps JavaScript API 顯示；標記內容只使用基站名稱、掛載點、經緯度與線上狀態，不包含登入帳號、密碼或 Authorization 資料。
- 金鑰處理：金鑰只會成為官方 Maps JavaScript API script URL 的 `key` 查詢參數，不會放入 Git、JSON API、Socket.IO、日誌或畫面文字。
- Demo Key 僅供測試。瀏覽器金鑰應限制為 Maps JavaScript API，並套用適當的 HTTP referrer 限制。
- 本專案提供自己的[使用條款初稿](TERMS-OF-USE.md)與[隱私權政策初稿](PRIVACY-POLICY.md)。實際營運者必須在公開部署前填入自身資料並完成法律審查。
- Google Maps 功能亦受 [Google Maps／Google Earth Additional Terms of Service](https://maps.google.com/help/terms_maps/) 與 [Google Privacy Policy](https://policies.google.com/privacy) 約束。
- 上述 Additional Terms 連結截至 2026-08-24 由 Google 標示為 Google Maps End User Additional Terms of Service；實際使用以官方頁面的最新版本為準。
- 管理介面不得遮蔽、覆蓋、移除或修改 Google 地圖自帶的 Google 標誌、版權、條款及 attribution。

Google Maps JavaScript API 可能依地圖類型載入多個 Google 官方網域。防火牆或 CSP 管理者應以 Google 維護的[官方網域清單](https://developers.google.com/maps/domains)為準，不要固定 IP 位址。

### OpenStreetMap

- 用途：預設底圖，以及 Google 未啟用或失敗時的備援。
- 圖磚來源：OpenLayers 內建的 OpenStreetMap 官方圖磚設定。
- Attribution：`© OpenStreetMap contributors`，連結至 `https://www.openstreetmap.org/copyright`。
- 離線行為：圖磚無法取得時顯示提示，其他管理功能仍可繼續使用。
- 隱私說明：圖磚請求可能向 OpenStreetMap 服務揭露 IP、瀏覽器標頭、referrer、圖磚座標及縮放層級；詳見本專案的[隱私權政策初稿](PRIVACY-POLICY.md)。
