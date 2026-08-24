# 2RTK NTRIP Caster 使用條款初稿

生效日期：2026-08-24

> 本文件是供專案採用者調整的初稿，未經律師審核，也不是法律意見。實際營運者在公開部署前，應依營運地區、服務內容及法規取得適當的法律意見。

## 1. 軟體與營運者

2RTK NTRIP Caster 是用於管理 NTRIP／RTCM 連線、帳號、掛載點及基站資訊的軟體。實際提供服務、控制資料及決定存取權限者是部署本軟體的營運者，而不是本專案原作者。

公開部署前，營運者必須補充：

- 營運者或公司名稱：`[請填寫]`
- 聯絡方式：`[請填寫]`
- 適用地區及準據法：`[請填寫]`

## 2. Google Maps

本軟體包含可選的 Google Maps 功能與內容。當營運者設定 `provider = google` 並提供有效的瀏覽器 API Key 時，瀏覽器會直接使用 Google Maps JavaScript API。

Google Maps 功能的使用同時受下列 Google 條款約束：

- [Google Maps／Google Earth Additional Terms of Service](https://maps.google.com/help/terms_maps/)
- [Google Terms of Service](https://policies.google.com/terms)
- [Google Maps Platform Terms of Service](https://cloud.google.com/maps-platform/terms)
- [Google Privacy Policy](https://policies.google.com/privacy)

查核日期為 2026-08-24；上列 Additional Terms 官方頁面目前標示為 Google Maps End User
Additional Terms of Service，並可能由 Google 更新或更名。實際使用一律以 Google 官方頁面的
最新版本為準。

使用者不得遮蔽、移除、修改或規避 Google 地圖顯示的 Google 標誌、版權、條款、attribution 或其他必要聲明。

## 3. OpenStreetMap

OpenStreetMap 是預設底圖及 Google Maps 無法使用時的備援。其資料與圖磚服務受 OpenStreetMap 的授權、attribution 及使用政策約束；介面會保留 `© OpenStreetMap contributors` 聲明。

## 4. NTRIP 與基站資料

營運者必須確認其有權接收、處理及轉送 NTRIP／RTCM 資料、基站座標、掛載點資訊與帳號資料。基站座標可能具有營運或安全敏感性，不應在未授權的情況下公開或交由外部地圖服務處理。

## 5. 帳號與安全

營運者應使用專用強密碼、限制管理介面來源、保護設定檔與備份，並依風險設定 TLS、反向代理及防火牆。不得使用公開範例密碼或把密鑰提交至 Git。

## 6. 可用性與責任限制

本軟體依現況提供，不保證無錯誤、不中斷或適合特定用途。NTRIP、Google Maps、OpenStreetMap、網路或其他第三方服務可能變更、限流或中止。法律允許的最大範圍內，專案貢獻者不對部署、營運、資料遺失、定位錯誤或第三方服務造成的損失負責。

## 7. 條款更新

營運者應維護其實際對外版本、公告生效日期，並在重大變更前依適用法規通知使用者。本專案中的初稿不會自動替營運者完成法律或合規更新。
