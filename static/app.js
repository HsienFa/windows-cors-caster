// Socket.IO connection configuration
const socket = io({
    timeout: 20000,
    forceNew: false,
    reconnection: true,
    reconnectionDelay: 1000,
    reconnectionDelayMax: 5000,
    maxReconnectionAttempts: 5
});
let currentPage = 'dashboard';
let connectionStatus = 'connecting';

// WebSocket connection status monitoring
socket.on('connect', function() {
    connectionStatus = 'connected';
    // console.log('WebSocket connection established');
    updateConnectionStatus();
    // If currently on dashboard page, request system statistics
    if (currentPage === 'dashboard') {
        requestSystemStats();
    }
});

socket.on('disconnect', function(reason) {
    connectionStatus = 'disconnected';
    // console.log('WebSocket connection disconnected:', reason);
    updateConnectionStatus();
});

socket.on('reconnect', function(attemptNumber) {
    connectionStatus = 'connected';
    // console.log('WebSocket reconnection successful, attempt number:', attemptNumber);
    updateConnectionStatus();
});

socket.on('reconnect_attempt', function(attemptNumber) {
    connectionStatus = 'reconnecting';
    // console.log('WebSocket reconnection attempt:', attemptNumber);
    updateConnectionStatus();
});

socket.on('reconnect_failed', function() {
    connectionStatus = 'failed';
    // console.log('WebSocket reconnection failed');
    updateConnectionStatus();
});

// Update connection status display
function updateConnectionStatus() {
    const statusElement = document.getElementById('connection-status');
    if (statusElement) {
        const statusText = {
        'connecting': '連線中...',
        'connected': '已連線',
        'disconnected': '已中斷連線',
        'reconnecting': '重新連線中...',
        'failed': '連線失敗'
    };
        const statusColor = {
            'connecting': '#ffd93d',
            'connected': '#00ff41',
            'disconnected': '#ff6b6b',
            'reconnecting': '#ffd93d',
            'failed': '#ff6b6b'
        };
        statusElement.textContent = statusText[connectionStatus] || '未知狀態';
        statusElement.style.color = statusColor[connectionStatus] || '#adb5bd';
    }
}

// Page navigation
function navigateTo(page) {
    // Check pages that require login
    const requireLoginPages = ['users', 'mounts', 'monitor', 'settings'];
    if (requireLoginPages.includes(page)) {
        // Check login status
        checkLoginStatusForProtectedPage().then(isLoggedIn => {
            if (!isLoggedIn) {
                // Redirect to login page with target page parameter
                window.location.href = `/login?redirect=${page}`;
                return;
            }
            // Logged in, continue navigation
            performNavigation(page);
        });
    } else {
        // Navigate directly for pages that don't require login
        performNavigation(page);
    }
}

// Execute actual page navigation
function performNavigation(page) {
    if (currentPage === 'monitor' && page !== 'monitor') {
        stopRoverPolling();
        destroyCurrentMap();
    }

    // Update navigation state
    document.querySelectorAll('.nav-item').forEach(item => {
        item.classList.remove('active');
    });
    const activeNavigationItem = document.querySelector(`[data-page="${page}"]`);
    if (activeNavigationItem) activeNavigationItem.classList.add('active');

    currentPage = page;
    
    // Control log panel display
    const logPanel = document.getElementById('log-panel');
    const mainContent = document.querySelector('.main-content');
    
    if (page === 'dashboard') {
        logPanel.style.display = 'block';
        mainContent.classList.add('dashboard-layout');
    } else {
        logPanel.style.display = 'none';
        mainContent.classList.remove('dashboard-layout');
    }
    
    loadPageContent(page);
}

// Check login status (for protected pages)
async function checkLoginStatusForProtectedPage() {
    try {
        const response = await fetch('/api/users');
        return response.status !== 401;
    } catch (error) {
        // console.error('Failed to check login status:', error);
        return false;
    }
}

// Check login status (original function, maintain compatibility)
async function checkLoginStatus() {
    try {
        const response = await fetch('/api/users');
        if (response.status === 401) {
            showAlert('登入狀態已過期，請重新登入', 'warning');
            window.location.href = '/login';
            return false;
        }
        return true;
    } catch (error) {
        // console.error('Failed to check login status:', error);
        return false;
    }
}

// Handle API response
async function handleApiResponse(response, skipAuthRedirect = false) {
    if (response.status === 401) {
        if (!skipAuthRedirect) {
            showAlert('登入狀態已過期，請重新登入', 'warning');
            window.location.href = '/login';
        }
        throw new Error('未授權存取');
    }
    
    if (!response.ok) {
        const errorData = await response.json().catch(() => ({ error: '未知錯誤' }));
        throw new Error(errorData.error || `HTTP ${response.status}`);
    }
    
    return response.json();
}

// Handle API response (for public pages, won't auto-redirect to login)
// Load page content
async function loadPageContent(page) {
    const contentDiv = document.getElementById('page-content');
    
    try {
        let response;
        switch(page) {
            case 'dashboard':
                contentDiv.innerHTML = getDashboardContent();
                // Show content panel for dashboard page
                contentDiv.parentElement.style.display = 'block';
                // Load system statistics
                fetchSystemStats();
                // Request real-time data
                requestSystemStats();
                break;
            case 'users':
                // Ensure content panel is displayed on non-dashboard pages
                contentDiv.parentElement.style.display = 'block';
                response = await fetch('/api/users');
                const users = await handleApiResponse(response);
                // /api/users API already contains correct online status information, use directly
                contentDiv.innerHTML = getUsersContent(users);
                break;
            case 'mounts':
                // Ensure content panel is displayed on non-dashboard pages
                contentDiv.parentElement.style.display = 'block';
                response = await fetch('/api/mounts');
                const mounts = await handleApiResponse(response);
                // /api/mounts API already contains correct online status and connection count information, use directly
                contentDiv.innerHTML = getMountsContent(mounts);
                break;
            case 'monitor':
                // Ensure content panel is displayed on non-dashboard pages
                contentDiv.parentElement.style.display = 'block';
                contentDiv.innerHTML = getMonitorContent();
                bindRoverStatusControls();
                // Update monitoring data display immediately
                updateMonitorData();
                // Add INFO button event handling for STR items
                setTimeout(() => {
                    addInfoButtonsToSTRItems();
                }, 200);
                // Initialize map when monitor page is loaded
                setTimeout(() => {
                    initializeMapForMonitor();
                }, 300);
                startRoverPolling();
                break;
            case 'settings':
                // Ensure content panel is displayed on non-dashboard pages
                contentDiv.parentElement.style.display = 'block';
                contentDiv.innerHTML = getSettingsContent();
                break;
        }
    } catch (error) {
        // console.error('Failed to load page content:', error);
        contentDiv.innerHTML = '<div class="error-message">無法載入頁面內容，請稍後再試。</div>';
    }
}

// Add INFO button event handling for STR items
function addInfoButtonsToSTRItems() {
    const infoButtons = document.querySelectorAll('.str-info-btn');
    
    infoButtons.forEach(button => {
        // Avoid duplicate event binding
        if (button.hasAttribute('data-event-bound')) {
            return;
        }
        
        const mountName = button.getAttribute('data-mount');
        if (!mountName) {
            return;
        }
        
        button.title = `檢視 ${mountName} 的即時 RTCM 解析資料`;
        
        button.addEventListener('click', () => {
            // Start RTCM parsing and update container content
            startRTCMParsing(mountName);
        });
        
        // Mark as event bound
        button.setAttribute('data-event-bound', 'true');
    });
}

// Start RTCM parsing
// Store last position information for position change detection
let lastPosition = { latitude: null, longitude: null };
// Store map center for distance comparison
let mapCenter = { latitude: null, longitude: null };
// Track if this is the first marking
let isFirstMarking = true;
// Store current mount name for map display
let currentMountName = null;
// Store the display name separately from the mount point identifier.
let currentStationName = null;

function startRTCMParsing(mountName) {
    console.log(`[前端] 開始啟動 RTCM 解析：${mountName}`);
    currentMountName = mountName;
    currentStationName = mountName;
    
    // 
    fetch('/api/mount/rtcm-parse/status')
    .then(response => response.json())
    .then(statusData => {
        if (statusData.success) {
            const status = statusData.status;
            console.log(`[前端] 目前解析器狀態：`, status);
            console.log(`[前端] 目前作用中的 Web 掛載點：${status.current_web_mount || '無'}`);
            console.log(`[前端] Web 解析執行緒數：${status.web_parsers}, STR 解析執行緒數：${status.str_parsers}`);
            
            if (status.current_web_mount && status.current_web_mount !== mountName) {
                console.log(`[前端] 偵測到前一個作用中的掛載點：${status.current_web_mount}，將自動清理`);
            }
        }
    })
    .catch(error => {
        console.warn(`[前端] 取得解析器狀態失敗：`, error);
    });
    
    // Reset marking status for new mount point
    isFirstMarking = true;
    lastPosition = { latitude: null, longitude: null };
    mapCenter = { latitude: null, longitude: null };
    clearCurrentMapMarker();
    
    // Update base station information container
    updateStationInfo(mountName);
    
    // Initialize satellite visualization
    initializeSatelliteVisualization();
    
    // Call backend API to start RTCM parsing
    console.log(`[前端] 呼叫後端 API 啟動 RTCM 解析：${mountName}`);
    fetch(`/api/mount/${mountName}/rtcm-parse/start`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        }
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            console.log(`[前端] RTCM 解析啟動成功：${mountName}`);
            // 
            setTimeout(() => {
                fetch('/api/mount/rtcm-parse/status')
                .then(response => response.json())
                .then(statusData => {
                    if (statusData.success) {
                        console.log(`[前端] 啟動後的解析器狀態：`, statusData.status);
                    }
                })
                .catch(error => console.warn(`[前端] 取得啟動後狀態失敗：`, error));
            }, 1000);
        } else {
            console.error(`[前端] RTCM 解析啟動失敗：${data.error || '未知錯誤'}`);
            showAlert(`無法啟動 RTCM 解析：${data.error || '未知錯誤'}`, 'error');
        }
    })
    .catch(error => {
        console.error('[前端] 呼叫 RTCM 解析 API 失敗：', error);
        showAlert('無法呼叫 RTCM 解析 API', 'error');
    });
}

// Calculate distance between two points (meters)
function calculateDistance(lat1, lon1, lat2, lon2) {
    const R = 6371000; // Earth radius (meters)
    const dLat = (lat2 - lat1) * Math.PI / 180;
    const dLon = (lon2 - lon1) * Math.PI / 180;
    const a = Math.sin(dLat/2) * Math.sin(dLat/2) +
              Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
              Math.sin(dLon/2) * Math.sin(dLon/2);
    const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1-a));
    return R * c;
}

// Handle position updates, determine if re-marking is needed
function handlePositionUpdate(latitude, longitude, mountName = null) {
    // First time marking - always mark and use the configured zoom.
    if (isFirstMarking) {
        isFirstMarking = false;
        lastPosition.latitude = latitude;
        lastPosition.longitude = longitude;
        mapCenter.latitude = latitude;
        mapCenter.longitude = longitude;
        updateMapLocation(latitude, longitude, mountName, true);
        return;
    }
    
    // Check both distance conditions - update marker if either condition is met
    let shouldUpdateMarker = false;
    let updateReason = '';
    
    // Check distance from last position (500m threshold)
    if (lastPosition.latitude !== null && lastPosition.longitude !== null) {
        const distance = calculateDistance(
            lastPosition.latitude, lastPosition.longitude,
            latitude, longitude
        );
        
        // If position change is 500 meters or more, should update marker
        if (distance >= 500) {
            shouldUpdateMarker = true;
            updateReason = `position change ${distance.toFixed(1)}m >= 500m threshold`;
        }
    }
    
    // Check distance from map center (50km threshold)
    if (mapCenter.latitude !== null && mapCenter.longitude !== null) {
        const centerDistance = calculateDistance(
            mapCenter.latitude, mapCenter.longitude,
            latitude, longitude
        );
        
        // If distance from map center is 50km or more, should update marker
        if (centerDistance >= 50000) {
            shouldUpdateMarker = true;
            updateReason = `distance from map center ${(centerDistance/1000).toFixed(1)}km >= 50km threshold`;
            // Update map center when re-marking due to distance
            mapCenter.latitude = latitude;
            mapCenter.longitude = longitude;
        }
    }
    
    // Check if marker is visible in the active map provider.
    if (currentMap && !shouldUpdateMarker) {
        if (activeMapProvider === 'osm' && typeof ol !== 'undefined') {
            const view = currentMap.getView();
            const extent = view.calculateExtent(currentMap.getSize());
            const markerCoord = ol.proj.fromLonLat([longitude, latitude]);
            if (!ol.extent.containsCoordinate(extent, markerCoord)) {
                shouldUpdateMarker = true;
                updateReason = 'marker not visible in OpenStreetMap view';
                const currentCenter = ol.proj.toLonLat(view.getCenter());
                mapCenter.latitude = currentCenter[1];
                mapCenter.longitude = currentCenter[0];
            }
        } else if (activeMapProvider === 'google') {
            const bounds = currentMap.getBounds();
            if (bounds && !bounds.contains({ lat: Number(latitude), lng: Number(longitude) })) {
                shouldUpdateMarker = true;
                updateReason = 'marker not visible in Google Maps view';
                const currentCenter = currentMap.getCenter();
                if (currentCenter) {
                    mapCenter.latitude = currentCenter.lat();
                    mapCenter.longitude = currentCenter.lng();
                }
            }
        }
    }
    
    // If neither condition is met, don't update marker
    if (!shouldUpdateMarker) {
        // console.log(`No update needed - position and center distance within thresholds`);
        return;
    }
    
    // console.log(`Updating marker: ${updateReason}`);
    
    // Update position and mark
    lastPosition.latitude = latitude;
    lastPosition.longitude = longitude;
    updateMapLocation(latitude, longitude, mountName, false);
}

// Update base station information
function updateStationInfo(mountName) {
    const stationInfoDiv = document.getElementById('station-info');
    stationInfoDiv.innerHTML = `
        <div class="station-info-loading">
            <p>正在解析 ${mountName} 的 RTCM 資料...</p>
            <div class="loading-spinner"></div>
        </div>
    `;
    
    // Base station information is now displayed through simulated data
}

// Display base station information
function displayStationInfo(stationData) {
    const stationInfoDiv = document.getElementById('station-info');
    const stationMountName = stationData.mount_name || stationData.mount || currentMountName || stationData.name || '未知';
    currentMountName = stationMountName;
    currentStationName = resolveStationDisplayName(stationData, stationMountName);
    stationInfoDiv.innerHTML = `
        <div class="station-details">
            <!-- 第一行：基本信息 -->
            <div class="info-row-group">
                <div class="info-row">
                    <span class="info-label">掛載點：</span>
                    <span class="info-value" id="station-name">${stationMountName}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">基站 ID：</span>
                    <span class="info-value" id="station-id">${stationData.id || '未知'}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">國家：</span>
                    <span class="info-value" id="station-country">${stationData.country_name || '未知'}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">城市：</span>
                    <span class="info-value" id="station-city">${stationData.city || '未知'}</span>
                </div>
            </div>
            
            <!-- 第二行：设备信息 -->
            <div class="info-row-group">
                <div class="info-row">
                    <span class="info-label">接收器型號：</span>
                    <span class="info-value" id="receiver-type">${stationData.receiver?.name || '未知'}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">接收器韌體：</span>
                    <span class="info-value" id="receiver-version">${stationData.receiver?.firmware || '未知'}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">天線型號：</span>
                    <span class="info-value" id="antenna-type">${stationData.antenna?.name || '未知'}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">天線序號：</span>
                    <span class="info-value" id="antenna-serial">${stationData.antenna?.serial || '未知'}</span>
                </div>
            </div>
            
            <!-- 第三行：坐标信息 -->
            <div class="info-row-group coordinates-group">
                <div class="coordinates-half">
                    <div class="info-row">
                        <span class="info-label">座標：</span>
                        <span class="info-value">經度：<span id="station-longitude">${stationData.longitude || 0}</span>°，緯度：<span id="station-latitude">${stationData.latitude || 0}</span>°，高度：<span id="station-height">${stationData.height || '未知'}</span></span>
                    </div>
                </div>
                <div class="coordinates-half">
                    <div class="info-row">
                        <span class="info-label">ECEF:</span>
                        <span class="info-value" id="station-xyz">X: ${stationData.x || 0}, Y: ${stationData.y || 0}, Z: ${stationData.z || 0}</span>
                    </div>
                </div>
            </div>
        </div>
    `;
    
    // Update base station status
    updateStationStatus(true);
    refreshCurrentMapMarkerDetails();
}

// Map related variables
function parseMapSetting(value, fallback, minimum, maximum) {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return fallback;
    return Math.min(maximum, Math.max(minimum, parsed));
}

function readMapRuntimeConfig() {
    const dataset = document.body ? document.body.dataset : {};
    return {
        provider: dataset.mapProvider === 'google' ? 'google' : 'osm',
        googleEnabled: dataset.googleMapsEnabled === 'true',
        defaultLatitude: parseMapSetting(dataset.mapDefaultLatitude, 23.7, -90, 90),
        defaultLongitude: parseMapSetting(dataset.mapDefaultLongitude, 121.0, -180, 180),
        defaultZoom: parseMapSetting(dataset.mapDefaultZoom, 7, 1, 22)
    };
}

const mapRuntimeConfig = readMapRuntimeConfig();
let currentMap = null;
let activeMapProvider = null;
let osmMarkerLayer = null;
let osmRoverLayer = null;
let osmPopupOverlay = null;
const osmRoverFeatures = new Map();
let googleMarker = null;
let googleInfoWindow = null;
let googleCoverageCircles = [];
const googleRoverMarkers = new Map();
let googleMapsReady = false;
let googleMapsFailed = false;
let currentMarkerDetails = null;
let latestRoverSnapshot = [];
const latestRoversById = new Map();
let selectedRoverConnectionId = null;

window.googleMapsApiReady = function() {
    googleMapsReady = true;
    googleMapsFailed = false;

    if (currentPage === 'monitor' && mapRuntimeConfig.provider === 'google') {
        if (initializeGoogleMap()) {
            restoreLastMapLocation(false);
        } else {
            fallbackToOpenStreetMap('Google 地圖無法初始化，已切換至 OpenStreetMap。');
        }
    }
};

window.googleMapsApiFailed = function() {
    googleMapsReady = false;
    googleMapsFailed = true;
    fallbackToOpenStreetMap('Google 地圖載入失敗，已切換至 OpenStreetMap。');
};

window.gm_authFailure = function() {
    googleMapsReady = false;
    googleMapsFailed = true;
    fallbackToOpenStreetMap('Google 地圖驗證失敗，已切換至 OpenStreetMap。');
};

function showMapStatusMessage(message) {
    const messageElement = document.getElementById('map-tile-error');
    if (!messageElement) return;

    messageElement.textContent = message;
    messageElement.hidden = false;
}

function hideMapStatusMessage() {
    const messageElement = document.getElementById('map-tile-error');
    if (!messageElement) return;

    messageElement.hidden = true;
}

function showMapEmptyState() {
    const emptyState = document.getElementById('map-empty-state');
    if (emptyState) emptyState.hidden = false;
}

function hideMapEmptyState() {
    const emptyState = document.getElementById('map-empty-state');
    if (emptyState) emptyState.hidden = true;
}

function updateMapProviderLabel(provider) {
    const providerLabel = document.getElementById('map-provider-label');
    if (!providerLabel) return;
    providerLabel.textContent = provider === 'google' ? 'Google Maps' : 'OpenStreetMap';
}

function destroyCurrentMap(preserveRoverSnapshot = false) {
    if (activeMapProvider === 'osm' && currentMap && typeof currentMap.setTarget === 'function') {
        currentMap.setTarget(null);
    }
    if (googleMarker) {
        googleMarker.setMap(null);
    }
    googleCoverageCircles.forEach(circle => circle.setMap(null));
    if (googleInfoWindow) {
        googleInfoWindow.close();
    }
    googleRoverMarkers.forEach(marker => marker.setMap(null));
    googleRoverMarkers.clear();
    if (osmRoverLayer) osmRoverLayer.getSource().clear();
    osmRoverFeatures.clear();

    currentMap = null;
    activeMapProvider = null;
    osmMarkerLayer = null;
    osmRoverLayer = null;
    osmPopupOverlay = null;
    googleMarker = null;
    googleInfoWindow = null;
    googleCoverageCircles = [];
    selectedRoverConnectionId = null;
    if (!preserveRoverSnapshot) {
        latestRoverSnapshot = [];
        latestRoversById.clear();
    }
}

function clearCurrentMapMarker() {
    currentMarkerDetails = null;
    if (osmMarkerLayer) {
        osmMarkerLayer.getSource().clear();
    }
    if (osmPopupOverlay && selectedRoverConnectionId === null) {
        osmPopupOverlay.setPosition(undefined);
    }
    const popupElement = document.getElementById('map-marker-popup');
    if (popupElement && selectedRoverConnectionId === null) popupElement.hidden = true;
    if (googleMarker) {
        googleMarker.setMap(null);
        googleMarker = null;
    }
    googleCoverageCircles.forEach(circle => circle.setMap(null));
    googleCoverageCircles = [];
    if (googleInfoWindow && selectedRoverConnectionId === null) googleInfoWindow.close();
    showMapEmptyState();
}

function initializeMap() {
    return initializeConfiguredMap();
}

function initializeMapForMonitor() {
    if (currentPage !== 'monitor' || !document.getElementById('map')) return;

    initializeConfiguredMap();
    restoreLastMapLocation(false);
}

function initializeConfiguredMap() {
    if (mapRuntimeConfig.provider === 'google' && googleMapsReady && initializeGoogleMap()) {
        return true;
    }

    const initialized = initializeOpenStreetMap();
    if (initialized && mapRuntimeConfig.provider === 'google') {
        const message = googleMapsFailed
            ? 'Google 地圖載入失敗，已切換至 OpenStreetMap。'
            : 'Google 地圖載入中，暫時顯示 OpenStreetMap。';
        showMapStatusMessage(message);
    }
    return initialized;
}

function fallbackToOpenStreetMap(message) {
    if (currentPage !== 'monitor' || !document.getElementById('map')) return;
    if (initializeOpenStreetMap()) {
        restoreLastMapLocation(false);
        showMapStatusMessage(message);
    }
}

function restoreLastMapLocation(isInitialMarking) {
    if (lastPosition.latitude === null || lastPosition.longitude === null) return;
    updateMapLocation(
        lastPosition.latitude,
        lastPosition.longitude,
        currentMountName,
        isInitialMarking
    );
}

function initializeOpenStreetMap() {
    if (typeof ol === 'undefined') {
        showMapStatusMessage('本機地圖程式庫載入失敗；其他管理功能仍可正常使用。');
        return false;
    }

    const mapContainer = document.getElementById('map');
    if (!mapContainer) return false;

    destroyCurrentMap(true);
    try {
        const layer = createOSMLayer();
        osmMarkerLayer = new ol.layer.Vector({
            source: new ol.source.Vector(),
            zIndex: 10
        });
        osmRoverLayer = new ol.layer.Vector({
            source: new ol.source.Vector(),
            zIndex: 20
        });
        currentMap = new ol.Map({
            target: mapContainer,
            layers: [layer, osmMarkerLayer, osmRoverLayer],
            view: new ol.View({
                center: ol.proj.fromLonLat([
                    mapRuntimeConfig.defaultLongitude,
                    mapRuntimeConfig.defaultLatitude
                ]),
                zoom: mapRuntimeConfig.defaultZoom
            })
        });
        activeMapProvider = 'osm';
        updateMapProviderLabel('osm');
        hideMapStatusMessage();

        const popupElement = document.getElementById('map-marker-popup');
        if (popupElement) {
            osmPopupOverlay = new ol.Overlay({
                element: popupElement,
                positioning: 'bottom-center',
                offset: [0, -18],
                stopEvent: false
            });
            currentMap.addOverlay(osmPopupOverlay);
            currentMap.on('singleclick', event => {
                const clickedFeature = currentMap.forEachFeatureAtPixel(
                    event.pixel,
                    feature => (
                        feature.get('roverConnectionId')
                        || feature.get('isStationMarker')
                    ) ? feature : null
                );
                const roverConnectionId = clickedFeature
                    ? clickedFeature.get('roverConnectionId')
                    : null;
                if (roverConnectionId && latestRoversById.has(roverConnectionId)) {
                    selectedRoverConnectionId = roverConnectionId;
                    populateRoverMarkerDetails(
                        popupElement,
                        latestRoversById.get(roverConnectionId)
                    );
                    popupElement.hidden = false;
                    osmPopupOverlay.setPosition(
                        clickedFeature.getGeometry().getCoordinates()
                    );
                } else if (clickedFeature && currentMarkerDetails) {
                    selectedRoverConnectionId = null;
                    populateMarkerDetails(popupElement, currentMarkerDetails);
                    popupElement.hidden = false;
                    osmPopupOverlay.setPosition(clickedFeature.getGeometry().getCoordinates());
                } else {
                    selectedRoverConnectionId = null;
                    popupElement.hidden = true;
                    osmPopupOverlay.setPosition(undefined);
                }
            });
        }
        syncRoverMarkers(latestRoverSnapshot);
        return true;
    } catch (error) {
        currentMap = null;
        activeMapProvider = null;
        showMapStatusMessage('OpenStreetMap 無法初始化；其他管理功能仍可正常使用。');
        return false;
    }
}

function initializeGoogleMap() {
    const mapContainer = document.getElementById('map');
    const googleApiAvailable = typeof google !== 'undefined' && google.maps && google.maps.Map;
    if (!mapContainer || !mapRuntimeConfig.googleEnabled || !googleApiAvailable) return false;

    destroyCurrentMap(true);
    try {
        currentMap = new google.maps.Map(mapContainer, {
            center: {
                lat: mapRuntimeConfig.defaultLatitude,
                lng: mapRuntimeConfig.defaultLongitude
            },
            zoom: mapRuntimeConfig.defaultZoom,
            mapTypeId: google.maps.MapTypeId.ROADMAP,
            mapTypeControl: true,
            mapTypeControlOptions: {
                mapTypeIds: [
                    google.maps.MapTypeId.ROADMAP,
                    google.maps.MapTypeId.SATELLITE,
                    google.maps.MapTypeId.HYBRID,
                    google.maps.MapTypeId.TERRAIN
                ]
            },
            streetViewControl: false,
            scaleControl: true,
            fullscreenControl: true
        });
        activeMapProvider = 'google';
        googleInfoWindow = new google.maps.InfoWindow();
        updateMapProviderLabel('google');
        hideMapStatusMessage();
        syncRoverMarkers(latestRoverSnapshot);
        return true;
    } catch (error) {
        currentMap = null;
        activeMapProvider = null;
        return false;
    }
}

function createOSMLayer() {
    const tileSource = new ol.source.OSM({
        attributions: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap contributors</a>',
        transition: 100
    });

    tileSource.on('tileloaderror', () => {
        showMapStatusMessage('無法取得 OpenStreetMap 圖磚；其他管理功能仍可正常使用。');
    });

    return new ol.layer.Tile({
        source: tileSource,
        preload: 1,
        useInterimTilesOnError: true,
        zIndex: 0
    });
}

function isCurrentMountOnline(mountName) {
    if (!mountName || !window.onlineMounts) return false;
    return Object.prototype.hasOwnProperty.call(window.onlineMounts, mountName);
}

function resolveStationDisplayName(stationData = {}, fallbackMountName = null) {
    const mountName = stationData.mount_name
        || stationData.mount
        || fallbackMountName;
    return stationData.station_name
        || stationData.site_name
        || stationData.display_name
        || stationData.name
        || mountName
        || stationData.station_id
        || stationData.id
        || '未知基站';
}

function createMarkerDetails(latitude, longitude, mountName) {
    const resolvedMountName = mountName || currentMountName || '未知';
    return {
        name: currentStationName || resolvedMountName || '未知基站',
        mountName: resolvedMountName,
        latitude: Number(latitude),
        longitude: Number(longitude),
        online: isCurrentMountOnline(resolvedMountName)
    };
}

function populateMarkerDetails(container, details) {
    container.replaceChildren();
    const rows = [
        ['基站名稱', details.name],
        ['掛載點', details.mountName],
        ['緯度', details.latitude.toFixed(6)],
        ['經度', details.longitude.toFixed(6)],
        ['狀態', details.online ? '線上' : '離線']
    ];

    rows.forEach(([label, value]) => {
        const row = document.createElement('div');
        row.className = 'map-marker-popup-row';
        const labelElement = document.createElement('span');
        labelElement.className = 'map-marker-popup-label';
        labelElement.textContent = `${label}：`;
        const valueElement = document.createElement('span');
        valueElement.textContent = String(value);
        row.append(labelElement, valueElement);
        container.appendChild(row);
    });
}

function updateOpenStreetMapMarker(details, isInitialMarking) {
    if (!currentMap || !osmMarkerLayer) return;
    const center = ol.proj.fromLonLat([details.longitude, details.latitude]);
    const source = osmMarkerLayer.getSource();
    source.clear();

    const markerFeature = new ol.Feature({
        geometry: new ol.geom.Point(center),
        name: details.name,
        isStationMarker: true
    });
    markerFeature.setStyle(new ol.style.Style({
        zIndex: 10,
        image: new ol.style.Icon({
            src: 'data:image/svg+xml;base64,' + btoa(`
                <svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 32 32">
                    <circle cx="16" cy="16" r="12" fill="#ffffff" stroke="#1565c0" stroke-width="3"/>
                    <text x="16" y="21" text-anchor="middle" font-family="Arial, sans-serif" font-size="16" font-weight="bold" fill="#dc143c">T</text>
                </svg>
            `),
            anchor: [0.5, 0.5]
        })
    }));
    source.addFeature(markerFeature);

    const textFeature = new ol.Feature({
        geometry: new ol.geom.Point(center),
        name: '基站名稱標籤'
    });
    textFeature.setStyle(new ol.style.Style({
        zIndex: 11,
        text: new ol.style.Text({
            text: details.name,
            font: 'bold 16px Arial',
            fill: new ol.style.Fill({ color: '#1565c0' }),
            stroke: new ol.style.Stroke({ color: '#ffffff', width: 3 }),
            offsetY: -25
        })
    }));
    source.addFeature(textFeature);

    [
        [5000, 'rgba(21, 101, 192, 0.14)'],
        [10000, 'rgba(66, 165, 245, 0.12)']
    ].forEach(([radius, color]) => {
        const coverageCircle = new ol.Feature({
            geometry: new ol.geom.Circle(center, radius)
        });
        coverageCircle.setStyle(new ol.style.Style({
            fill: new ol.style.Fill({ color })
        }));
        source.addFeature(coverageCircle);
    });

    currentMap.getView().setCenter(center);
    if (isInitialMarking) currentMap.getView().setZoom(mapRuntimeConfig.defaultZoom);

    const popupElement = document.getElementById('map-marker-popup');
    if (popupElement && osmPopupOverlay) {
        selectedRoverConnectionId = null;
        populateMarkerDetails(popupElement, details);
        popupElement.hidden = false;
        osmPopupOverlay.setPosition(center);
    }
}

function openGoogleMarkerInfo() {
    if (!googleInfoWindow || !googleMarker || !currentMarkerDetails || !currentMap) return;
    selectedRoverConnectionId = null;
    const content = document.createElement('div');
    content.className = 'map-marker-popup';
    populateMarkerDetails(content, currentMarkerDetails);
    googleInfoWindow.setContent(content);
    googleInfoWindow.open({ map: currentMap, anchor: googleMarker });
}

function updateGoogleMapMarker(details, isInitialMarking) {
    if (!currentMap || typeof google === 'undefined' || !google.maps) return;
    const position = { lat: details.latitude, lng: details.longitude };

    if (!googleMarker) {
        googleMarker = new google.maps.Marker({
            map: currentMap,
            position,
            title: details.name
        });
        googleMarker.addListener('click', openGoogleMarkerInfo);
    } else {
        googleMarker.setMap(currentMap);
        googleMarker.setPosition(position);
        googleMarker.setTitle(details.name);
    }

    if (googleCoverageCircles.length === 0) {
        googleCoverageCircles = [
            new google.maps.Circle({
                map: currentMap,
                center: position,
                radius: 5000,
                fillColor: '#1565c0',
                fillOpacity: 0.14,
                strokeOpacity: 0
            }),
            new google.maps.Circle({
                map: currentMap,
                center: position,
                radius: 10000,
                fillColor: '#42a5f5',
                fillOpacity: 0.12,
                strokeOpacity: 0
            })
        ];
    } else {
        googleCoverageCircles.forEach(circle => circle.setCenter(position));
    }

    currentMap.setCenter(position);
    if (isInitialMarking) currentMap.setZoom(mapRuntimeConfig.defaultZoom);
    openGoogleMarkerInfo();
}

function updateMapLocation(latitude, longitude, mountName = null, isInitialMarking = false) {
    const numericLatitude = Number(latitude);
    const numericLongitude = Number(longitude);
    if (!Number.isFinite(numericLatitude) || !Number.isFinite(numericLongitude)) return;

    if (!currentMap) initializeMap();
    if (!currentMap) return;

    if (mountName) currentMountName = mountName;
    currentMarkerDetails = createMarkerDetails(
        numericLatitude,
        numericLongitude,
        currentMountName
    );
    hideMapEmptyState();

    if (activeMapProvider === 'google') {
        updateGoogleMapMarker(currentMarkerDetails, isInitialMarking);
    } else if (activeMapProvider === 'osm') {
        updateOpenStreetMapMarker(currentMarkerDetails, isInitialMarking);
    }
}

function refreshCurrentMapMarkerDetails() {
    if (!currentMarkerDetails) return;
    currentMarkerDetails = createMarkerDetails(
        currentMarkerDetails.latitude,
        currentMarkerDetails.longitude,
        currentMarkerDetails.mountName
    );

    if (activeMapProvider === 'google' && googleMarker) {
        googleMarker.setTitle(currentMarkerDetails.name);
        openGoogleMarkerInfo();
    } else if (activeMapProvider === 'osm') {
        const popupElement = document.getElementById('map-marker-popup');
        if (popupElement && !popupElement.hidden) {
            populateMarkerDetails(popupElement, currentMarkerDetails);
        }
    }
}

function roverMarkerColor(rover) {
    const status = RoverState.getPositionStatus(rover);
    if (status.key === 'fixed') return '#2e7d32';
    if (status.key === 'float') return '#ef8c00';
    if (status.key === 'stale') return '#757575';
    return '#00838f';
}

function formatRoverNumber(value, digits, suffix = '') {
    const numericValue = Number(value);
    if (!Number.isFinite(numericValue)) return '—';
    return `${numericValue.toFixed(digits)}${suffix}`;
}

function formatGgaAge(rover) {
    if (!rover || !rover.last_gga_time) return '無位置資料';
    const age = Number(rover.gga_age_seconds);
    if (!Number.isFinite(age)) return '未知';
    if (age < 60) return `${Math.max(0, Math.round(age))} 秒前`;
    if (age < 3600) return `${Math.floor(age / 60)} 分鐘前`;
    return `${Math.floor(age / 3600)} 小時前`;
}

function appendSafePopupRow(container, label, value) {
    const row = document.createElement('div');
    row.className = 'map-marker-popup-row';
    const labelElement = document.createElement('span');
    labelElement.className = 'map-marker-popup-label';
    labelElement.textContent = `${label}：`;
    const valueElement = document.createElement('span');
    valueElement.textContent = String(value);
    row.append(labelElement, valueElement);
    container.appendChild(row);
}

function populateRoverMarkerDetails(container, rover) {
    container.replaceChildren();
    const status = RoverState.getPositionStatus(rover);
    const rows = [
        ['使用者', rover.username || '未知'],
        ['掛載點', rover.mount_name || '未知'],
        ['設備', rover.user_agent || '未知'],
        ['定位', status.label],
        ['衛星', rover.satellites ?? '—'],
        ['HDOP', formatRoverNumber(rover.hdop, 2)],
        ['高程', formatRoverNumber(rover.altitude, 1, ' m')],
        ['主站距離', formatRoverNumber(rover.distance_to_base_km, 2, ' km')],
        ['最後 GGA', formatGgaAge(rover)]
    ];
    rows.forEach(([label, value]) => appendSafePopupRow(container, label, value));
}

function closeRoverMarkerPopup(connectionId) {
    if (selectedRoverConnectionId !== String(connectionId)) return;
    selectedRoverConnectionId = null;
    if (osmPopupOverlay) osmPopupOverlay.setPosition(undefined);
    const popupElement = document.getElementById('map-marker-popup');
    if (popupElement) popupElement.hidden = true;
    if (googleInfoWindow) googleInfoWindow.close();
}

function createOpenStreetMapRoverFeature(rover) {
    const feature = new ol.Feature({
        geometry: new ol.geom.Point(ol.proj.fromLonLat([
            Number(rover.longitude),
            Number(rover.latitude)
        ])),
        roverConnectionId: String(rover.connection_id)
    });
    updateOpenStreetMapRoverFeature(feature, rover);
    osmRoverLayer.getSource().addFeature(feature);
    return feature;
}

function updateOpenStreetMapRoverFeature(feature, rover) {
    feature.getGeometry().setCoordinates(ol.proj.fromLonLat([
        Number(rover.longitude),
        Number(rover.latitude)
    ]));
    feature.setStyle([
        new ol.style.Style({
            zIndex: 20,
            image: new ol.style.Circle({
                radius: 11,
                fill: new ol.style.Fill({ color: roverMarkerColor(rover) }),
                stroke: new ol.style.Stroke({ color: '#ffffff', width: 3 })
            }),
            text: new ol.style.Text({
                text: 'R',
                font: 'bold 12px Arial',
                fill: new ol.style.Fill({ color: '#ffffff' })
            })
        }),
        new ol.style.Style({
            zIndex: 21,
            text: new ol.style.Text({
                text: String(rover.username || 'Rover'),
                font: 'bold 13px Arial',
                textAlign: 'center',
                offsetY: 25,
                fill: new ol.style.Fill({ color: '#1f2937' }),
                stroke: new ol.style.Stroke({ color: '#ffffff', width: 4 })
            })
        })
    ]);
}

function syncOpenStreetMapRovers(rovers) {
    if (!osmRoverLayer) return;
    RoverState.reconcileMarkers(rovers, osmRoverFeatures, {
        create: createOpenStreetMapRoverFeature,
        update: updateOpenStreetMapRoverFeature,
        remove: (feature, connectionId) => {
            osmRoverLayer.getSource().removeFeature(feature);
            closeRoverMarkerPopup(connectionId);
        }
    });
}

function googleRoverMarkerAppearance(rover) {
    return {
        icon: {
            path: google.maps.SymbolPath.CIRCLE,
            fillColor: roverMarkerColor(rover),
            fillOpacity: 1,
            strokeColor: '#ffffff',
            strokeWeight: 3,
            scale: 11
        },
        label: {
            text: 'R',
            color: '#ffffff',
            fontSize: '12px',
            fontWeight: '700'
        }
    };
}

function openGoogleRoverInfo(connectionId, marker) {
    if (!googleInfoWindow || !currentMap || !latestRoversById.has(connectionId)) return;
    selectedRoverConnectionId = connectionId;
    const content = document.createElement('div');
    content.className = 'map-marker-popup';
    populateRoverMarkerDetails(content, latestRoversById.get(connectionId));
    googleInfoWindow.setContent(content);
    googleInfoWindow.open({ map: currentMap, anchor: marker });
}

function createGoogleRoverMarker(rover) {
    const connectionId = String(rover.connection_id);
    const appearance = googleRoverMarkerAppearance(rover);
    const marker = new google.maps.Marker({
        map: currentMap,
        position: {
            lat: Number(rover.latitude),
            lng: Number(rover.longitude)
        },
        title: `${rover.username || 'Rover'} — ${RoverState.getPositionStatus(rover).label}`,
        icon: appearance.icon,
        label: appearance.label
    });
    marker.addListener('click', () => openGoogleRoverInfo(connectionId, marker));
    return marker;
}

function updateGoogleRoverMarker(marker, rover) {
    const appearance = googleRoverMarkerAppearance(rover);
    marker.setMap(currentMap);
    marker.setPosition({
        lat: Number(rover.latitude),
        lng: Number(rover.longitude)
    });
    marker.setTitle(`${rover.username || 'Rover'} — ${RoverState.getPositionStatus(rover).label}`);
    marker.setIcon(appearance.icon);
    marker.setLabel(appearance.label);
}

function syncGoogleRovers(rovers) {
    if (!currentMap || typeof google === 'undefined' || !google.maps) return;
    RoverState.reconcileMarkers(rovers, googleRoverMarkers, {
        create: createGoogleRoverMarker,
        update: updateGoogleRoverMarker,
        remove: (marker, connectionId) => {
            marker.setMap(null);
            closeRoverMarkerPopup(connectionId);
        }
    });
}

function syncRoverMarkers(rovers) {
    latestRoverSnapshot = Array.isArray(rovers) ? rovers.slice() : [];
    latestRoversById.clear();
    latestRoverSnapshot.forEach(rover => {
        if (rover.connection_id) {
            latestRoversById.set(String(rover.connection_id), rover);
        }
    });

    if (activeMapProvider === 'osm') {
        syncOpenStreetMapRovers(latestRoverSnapshot);
    } else if (activeMapProvider === 'google') {
        syncGoogleRovers(latestRoverSnapshot);
    }
}

// SATELLITE
let satelliteContainers = {};
let satelliteData = {};
let frequencyMap = {};

// freq_map
async function loadFrequencyMap() {
    try {
        const response = await fetch('/static/freq_map.json');
        frequencyMap = await response.json();
        // console.log('频率映射表加载成功');
    } catch (error) {
        // console.error('频率映射表加载失败:', error);
    }
}




function getFrequencyInfo(constellation, channel) {
    
    const constellationMap = {
        'GPS': 'GPS',
        'GLONASS': 'GLO', 
        'GALILEO': 'GAL',
        'BDS': 'BDS',
        'QZSS': 'QZS',
        'SBAS': 'SBAS',
        'IRNSS': 'IRN',
        'NAVIC': 'NAV'
    };
    
    const mappedConstellation = constellationMap[constellation];
    if (!mappedConstellation || !frequencyMap[mappedConstellation] || !channel) {
        return { band: '未知', freq: '未知' };
    }
    
    const freqInfo = frequencyMap[mappedConstellation][channel];
    return freqInfo || { band: '未知', freq: '未知' };
}


function initializeSatelliteVisualization() {
    const satelliteContainer = document.getElementById('satellite-container');
    if (!satelliteContainer) {
        // console.warn('卫星容器不存在，无法初始化卫星可视化');
        return;
    }
    
    
    satelliteContainer.innerHTML = '';
    
    
    const supportedConstellations = ['GPS', 'GLONASS', 'GALILEO', 'BDS', 'QZSS', 'SBAS', 'IRNSS', 'NAVIC'];
    supportedConstellations.forEach(constellation => {
        createConstellationContainer(constellation);
        
        const constellationContainer = document.querySelector(`#chart-${constellation}`).closest('.constellation-container');
        if (constellationContainer) {
            constellationContainer.style.display = 'none';
        }
    });
    
    // console.log('卫星可视化初始化完成，已创建', supportedConstellations.length, '个星座容器（初始隐藏状态）');
}

 
function updateSatelliteVisualization(constellation, satellites) {
     
    satelliteData[constellation] = satellites;
    
    
    updateSatelliteStatus(satellites && satellites.length > 0);
    
     
    updateConstellationChart(constellation, satellites);
}


function createConstellationContainer(constellation) {
    const satelliteContainer = document.getElementById('satellite-container');
    
    const constellationDiv = document.createElement('div');
    constellationDiv.className = 'constellation-container';
    constellationDiv.id = `constellation-${constellation}`;
    
    constellationDiv.innerHTML = `
        <h5 class="constellation-title">${constellation}</h5>
        <div class="satellite-chart" id="chart-${constellation}"></div>
    `;
    
    satelliteContainer.appendChild(constellationDiv);
    satelliteContainers[constellation] = constellationDiv;
}


function updateConstellationChart(constellation, satellites) {
    const chartContainer = document.getElementById(`chart-${constellation}`);
    if (!chartContainer) {
        // console.warn(`图表容器 chart-${constellation} 不存在`);
        return;
    }
    
     
    const currentTime = Date.now();
    
    
    if (!satelliteData[constellation]) {
        satelliteData[constellation] = {};
    }
    
    
    satellites.forEach(satellite => {
        satelliteData[constellation][satellite.name] = {
            ...satellite,
            lastUpdate: currentTime
        };
    });
    
    
    const expireTime = 10000; // 10秒
    Object.keys(satelliteData[constellation]).forEach(satName => {
        if (currentTime - satelliteData[constellation][satName].lastUpdate > expireTime) {
            delete satelliteData[constellation][satName];
        }
    });
    
    
    const constellationContainer = chartContainer.closest('.constellation-container');
    const activeSatelliteCount = Object.keys(satelliteData[constellation]).length;
    
    
    if (activeSatelliteCount === 0) {
        
        if (constellationContainer) {
            constellationContainer.style.display = 'none';
        }
        // console.log(`${constellation} 星座模块已隐藏（无数据）`);
        return;
    } else {
        
        if (constellationContainer) {
            constellationContainer.style.display = 'block';
        }
    }
    
    
    chartContainer.innerHTML = '';
    
    const activeSatellites = Object.values(satelliteData[constellation]);
    const satelliteCount = activeSatellites.length;
    
    
    const containerWidth = chartContainer.offsetWidth || 300; 
    const minBarWidth = 20; 
    const maxBarWidth = 60; 
    const spacing = 5; 
    
    let barWidth = Math.floor((containerWidth - (satelliteCount - 1) * spacing) / satelliteCount);
    barWidth = Math.max(minBarWidth, Math.min(maxBarWidth, barWidth));
    
    activeSatellites.forEach(satellite => {
        const barContainer = document.createElement('div');
        barContainer.className = 'satellite-bar-container';
        barContainer.style.width = `${barWidth}px`;
        barContainer.style.marginRight = `${spacing}px`;
        barContainer.style.display = 'inline-block';
        barContainer.style.verticalAlign = 'bottom';
        
        const bar = document.createElement('div');
        bar.className = 'satellite-bar';
        bar.style.height = `${Math.max(satellite.signalStrength * 2, 10)}px`;
        bar.style.backgroundColor = getSignalColor(satellite.signalStrength);
        bar.style.width = '100%';
        
        const label = document.createElement('div');
        label.className = 'satellite-label';
        label.textContent = satellite.name;
        label.style.fontSize = barWidth < 30 ? '10px' : '12px'; 
        label.style.textAlign = 'center';
        
        const strength = document.createElement('div');
        strength.className = 'satellite-strength';
        strength.textContent = satellite.signalStrength;
        strength.style.fontSize = barWidth < 30 ? '9px' : '11px';
        strength.style.textAlign = 'center';
        
        
        barContainer.addEventListener('mouseenter', (e) => {
            showSatelliteTooltip(e, satellite, constellation);
        });
        
        barContainer.addEventListener('mouseleave', () => {
            
            tooltipHideTimeout = setTimeout(() => {
                hideSatelliteTooltip();
            }, 300);
        });
        
        
        barContainer.addEventListener('mousemove', (e) => {
            updateTooltipPosition(e);
        });
        
        barContainer.appendChild(strength);
        barContainer.appendChild(bar);
        barContainer.appendChild(label);
        chartContainer.appendChild(barContainer);
    });
    
    
    const lastBar = chartContainer.lastElementChild;
    if (lastBar) {
        lastBar.style.marginRight = '0';
    }
    
    
}


function getSignalColor(strength) {
    if (strength >= 40) return '#4CAF50'; // 绿色 Green
    if (strength >= 30) return '#FFC107'; // 黄色 Yellow
    if (strength >= 20) return '#FF9800'; // 橙色 Orange 颜色好像不对~
    return '#F44336'; // 红色 Red
}

let currentTooltip = null;
let tooltipHideTimeout = null;


function showSatelliteTooltip(event, satellite, constellation) {
    
    if (tooltipHideTimeout) {
        clearTimeout(tooltipHideTimeout);
        tooltipHideTimeout = null;
    }
    
    
    hideSatelliteTooltip();
    
    
    const freqInfo = getFrequencyInfo(constellation, satellite.channel);
    
    const tooltip = document.createElement('div');
    tooltip.className = 'satellite-tooltip';
    tooltip.innerHTML = `
        <div><strong>${satellite.name}</strong></div>
        <div>訊號強度：${satellite.signalStrength} dBHz</div>
                    <div>仰角：${satellite.elevation}°</div>
                    <div>方位角：${satellite.azimuth}°</div>
                    <div>頻段：${freqInfo.band}</div>
                    <div>頻率：${freqInfo.freq}</div>
                    <div>通道：${satellite.channel || '未知'}</div>
    `;
    
    tooltip.style.cssText = `
        position: absolute;
        background: rgba(0, 0, 0, 0.9);
        color: white;
        padding: 10px;
        border-radius: 5px;
        font-size: 12px;
        z-index: 10000;
        pointer-events: none;
        box-shadow: 0 2px 10px rgba(0,0,0,0.3);
        max-width: 200px;
        transition: opacity 0.2s ease;
    `;
    
    document.body.appendChild(tooltip);
    currentTooltip = tooltip;
    
    
    updateTooltipPosition(event);
}


function updateTooltipPosition(event) {
    if (!currentTooltip) return;
    
    const tooltip = currentTooltip;
    
    
    let left = event.pageX + 10;
    let top = event.pageY - 10;
    
    
    if (left + tooltip.offsetWidth > window.innerWidth + window.scrollX) {
        left = event.pageX - tooltip.offsetWidth - 10;
    }
    
    
    if (top < window.scrollY) {
        top = event.pageY + 20;
    }
    
    
    if (top + tooltip.offsetHeight > window.innerHeight + window.scrollY) {
        top = event.pageY - tooltip.offsetHeight - 10;
    }
    
    tooltip.style.left = left + 'px';
    tooltip.style.top = top + 'px';
}


function hideSatelliteTooltip() {
    if (currentTooltip) {
        currentTooltip.remove();
        currentTooltip = null;
    }
    if (tooltipHideTimeout) {
        clearTimeout(tooltipHideTimeout);
        tooltipHideTimeout = null;
    }
}


function getDashboardContent() {
    return `
        <div class="page-header">
            <h3>系統狀態</h3>
            <div class="dashboard-timestamp" id="dashboard-timestamp">載入中...</div>
        </div>
        
        <!-- 系统概览卡片 -->
        <div class="dashboard-cards">
            <div class="dashboard-card">
                <div class="card-icon">⏰</div>
                <div class="card-content">
                    <div class="card-title">執行時間</div>
                    <div class="card-value" id="system-uptime">-</div>
                </div>
            </div>
            
            <div class="dashboard-card">
                <div class="card-icon">⚡</div>
                <div class="card-content">
                    <div class="card-title">CPU 使用率</div>
                    <div class="card-value" id="system-cpu">-</div>
                </div>
            </div>
            
            <div class="dashboard-card">
                <div class="card-icon">📈</div>
                <div class="card-content">
                    <div class="card-title">記憶體使用率</div>
                    <div class="card-value" id="system-memory">-</div>
                    <div class="card-detail" id="system-memory-detail">-</div>
                </div>
            </div>
            
            <div class="dashboard-card">
                <div class="card-icon">📻</div>
                <div class="card-content">
                    <div class="card-title">網路頻寬</div>
                    <div class="card-value" id="system-bandwidth">-</div>
                </div>
            </div>
        </div>
        
        <!-- 连接统计 -->
        <div class="dashboard-section">
            <h4>連線統計</h4>
            <div class="stats-grid">
                <div class="stat-item">
                    <span class="stat-label">目前連線數：</span>
                    <span class="stat-value" id="active-connections">-</span>
                </div>
                <div class="stat-item">
                    <span class="stat-label">連線數上限：</span>
                    <span class="stat-value" id="max-connections">-</span>
                </div>
                <div class="stat-item">
                    <span class="stat-label">累計連線數：</span>
                    <span class="stat-value" id="total-connections">-</span>
                </div>
                <div class="stat-item">
                    <span class="stat-label">拒絕連線數：</span>
                    <span class="stat-value" id="rejected-connections">-</span>
                </div>
                <div class="stat-item">
                    <span class="stat-label">線上掛載點：</span>
                    <span class="stat-value" id="total-mounts">-</span>
                </div>
                <div class="stat-item">
                    <span class="stat-label">使用者連線數：</span>
                    <span class="stat-value" id="total-users">-</span>
                </div>
                <div class="stat-item">
                    <span class="stat-label">資料傳輸量：</span>
                    <span class="stat-value" id="total-data">-</span>
                </div>
            </div>
        </div>
        
        <!-- 挂载点详情 -->
        <div class="dashboard-section">
            <h4>掛載點詳細資料</h4>
            <div class="mounts-container" id="mounts-detail">
                <div class="loading-text">載入中...</div>
            </div>
        </div>
        
        <style>
        .dashboard-cards {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 1rem;
            margin-bottom: 1.5rem;
        }
        
        .dashboard-card {
            background: linear-gradient(135deg, rgba(255, 255, 255, 0.95), rgba(255, 255, 255, 0.85));
            backdrop-filter: blur(15px);
            border-radius: 15px;
            padding: 1.2rem;
            box-shadow: 0 6px 24px rgba(0, 0, 0, 0.08), 0 2px 6px rgba(0, 0, 0, 0.04);
            border: 1px solid rgba(255, 255, 255, 0.3);
            display: flex;
            align-items: center;
            gap: 1rem;
            transition: all 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275);
            position: relative;
            overflow: hidden;
            animation: fadeInUp 0.6s ease-out;
        }
        
        .dashboard-card::before {
            content: '';
            position: absolute;
            top: 0;
            left: -100%;
            width: 100%;
            height: 100%;
            background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.4), transparent);
            transition: left 0.6s ease;
        }
        
        .dashboard-card:hover::before {
            left: 100%;
        }
        
        .dashboard-card:hover {
            transform: translateY(-8px) scale(1.02);
            box-shadow: 0 15px 50px rgba(0, 0, 0, 0.15), 0 5px 20px rgba(0, 0, 0, 0.1);
        }
        
        .card-icon {
            font-size: 1.8em;
            background: linear-gradient(135deg, #667eea, #764ba2);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            animation: pulse 2s ease-in-out infinite;
        }
        
        .card-content {
            flex: 1;
            position: relative;
            z-index: 1;
        }
        
        .card-title {
            font-size: 0.8rem;
            color: #555;
            margin-bottom: 0.5rem;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            font-weight: 600;
        }
        
        .card-value {
            font-size: 1.4rem;
            font-weight: 700;
            background: linear-gradient(135deg, #333, #555);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            transition: all 0.3s ease;
        }
        
        .dashboard-card:hover .card-value {
            transform: scale(1.1);
        }
        
        .card-detail {
            font-size: 0.8em;
            color: #888;
            margin-top: 2px;
        }
        
        .dashboard-section {
            background: white;
            border-radius: 8px;
            padding: 20px;
            margin-bottom: 20px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        
        .dashboard-section h4 {
            margin: 0 0 15px 0;
            color: #333;
        }
        
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
        }
        
        .stat-item {
            display: flex;
            justify-content: space-between;
            padding: 10px;
            background: #f8f9fa;
            border-radius: 4px;
        }
        
        .stat-label {
            color: #666;
        }
        
        .stat-value {
            font-weight: bold;
            color: #333;
        }
        
        .mounts-container {
            max-height: 400px;
            overflow-y: auto;
        }
        
        .mount-item {
            background: #f8f9fa;
            border-radius: 4px;
            padding: 15px;
            margin-bottom: 10px;
            border-left: 4px solid #007bff;
        }
        
        .mount-name {
            font-weight: bold;
            color: #333;
            margin-bottom: 5px;
        }
        
        .mount-stats {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
            gap: 10px;
            font-size: 0.9em;
            color: #666;
        }
        
        .dashboard-timestamp {
            color: #666;
            font-size: 0.9em;
        }
        
        .loading-text {
            text-align: center;
            color: #666;
            padding: 20px;
        }
        </style>
    `;
}

// user
function getUsersContent(users) {
    let usersHtml = users.map(user => {
        //两种方式 API获取和socket推送 可以备用
        const isOnline = user.online !== undefined ? user.online : (window.onlineUsers && (user.username in window.onlineUsers));
        const statusHtml = isOnline ? 
            '<span style="color: #28a745; font-weight: bold;">● 線上</span>' :
            '<span style="color: #6c757d;">○ 離線</span>';
        return `
            <tr class="user-row" data-username="${user.username}">
                <td>${user.username}</td>
                <td class="user-status">${statusHtml}</td>
                <td>${user.connection_count || 0}</td>
                <td>${user.connect_time || '-'}</td>
                <td>
                    <button class="btn btn-primary btn-sm edit-user-btn" data-username="${user.username}">編輯</button>
                    <button class="btn btn-danger btn-sm delete-user-btn" data-username="${user.username}">刪除</button>
                </td>
            </tr>
        `;
    }).join('');
    
    
    setTimeout(() => {
        
        document.querySelectorAll('.edit-user-btn').forEach(btn => {
            btn.addEventListener('click', function() {
                const username = this.getAttribute('data-username');
                editUser(username);
            });
        });
        
        
        document.querySelectorAll('.delete-user-btn').forEach(btn => {
            btn.addEventListener('click', function() {
                const username = this.getAttribute('data-username');
                // console.log('Delete button clicked for user:', username);
                deleteUser(username);
            });
        });
    }, 0);
    
    return `
        <div class="page-header">
            <h3>使用者管理</h3>
            <button onclick="showAddUserForm()" class="btn btn-primary">新增使用者</button>
        </div>
        <div class="table-container">
            <table class="data-table">
                <thead>
                    <tr>
                        <th>使用者名稱</th>
                        <th>狀態</th>
                        <th>連線數</th>
                        <th>連線時間</th>
                        <th>操作</th>
                    </tr>
                </thead>
                <tbody>
                    ${usersHtml}
                </tbody>
            </table>
        </div>
    `;
}

// 挂载点管理内容
function getMountsContent(mounts) {
    let mountsHtml = mounts.map(mount => {
        // 优先使用从API获取的在线状态，如果没有则使用WebSocket数据
        const isOnline = mount.active !== undefined ? mount.active : (window.onlineMounts && (mount.mount in window.onlineMounts));
        const statusHtml = isOnline ? 
            '<span style="color: #28a745; font-weight: bold;">● 線上</span>' :
            '<span style="color: #6c757d;">○ 離線</span>';
        return `
            <tr class="mount-row" data-mount="${mount.mount}">
                <td>${mount.mount}</td>
                <td class="mount-status">${statusHtml}</td>
                <td>${mount.connections || 0}</td>
                <td>${mount.username || '未指定'}</td>
                <td>${mount.description || '-'}</td>
                <td>
                    <button onclick="editMount('${mount.mount}')" class="btn btn-primary btn-sm">編輯</button>
                    <button onclick="deleteMount('${mount.mount}')" class="btn btn-danger btn-sm">刪除</button>
                </td>
            </tr>
        `;
    }).join('');
    
    return `
        <div class="page-header">
            <h3>掛載點管理</h3>
            <button onclick="showAddMountForm()" class="btn btn-primary">新增掛載點</button>
        </div>
        <div class="table-container">
            <table class="data-table">
                <thead>
                    <tr>
                        <th>掛載點</th>
                        <th>狀態</th>
                        <th>連線數</th>
                        <th>所屬使用者</th>
                        <th>說明</th>
                        <th>操作</th>
                    </tr>
                </thead>
                <tbody>
                    ${mountsHtml}
                </tbody>
            </table>
        </div>
    `;
}

const ROVER_POLL_INTERVAL_MS = 3000;
let roverPollingTimer = null;
let roverPollingAbortController = null;
let roverFetchInFlight = false;
let roverPollingGeneration = 0;

function stopRoverPolling() {
    roverPollingGeneration += 1;
    if (roverPollingTimer !== null) {
        window.clearInterval(roverPollingTimer);
        roverPollingTimer = null;
    }
    if (roverPollingAbortController) {
        roverPollingAbortController.abort();
        roverPollingAbortController = null;
    }
    roverFetchInFlight = false;
}

function startRoverPolling() {
    stopRoverPolling();
    if (currentPage !== 'monitor') return;
    fetchRoverStatus();
    roverPollingTimer = window.setInterval(fetchRoverStatus, ROVER_POLL_INTERVAL_MS);
}

async function fetchRoverStatus() {
    if (currentPage !== 'monitor' || roverFetchInFlight) return;
    const requestGeneration = roverPollingGeneration;
    roverFetchInFlight = true;
    const abortController = new AbortController();
    roverPollingAbortController = abortController;
    try {
        const response = await fetch('/api/rovers', {
            signal: abortController.signal,
            headers: { Accept: 'application/json' }
        });
        if (response.status === 401) {
            stopRoverPolling();
            window.location.href = '/login?redirect=monitor';
            return;
        }
        const payload = await handleApiResponse(response, true);
        if (
            currentPage !== 'monitor'
            || requestGeneration !== roverPollingGeneration
        ) return;
        renderRoverStatus(Array.isArray(payload.rovers) ? payload.rovers : []);
        const updateElement = document.getElementById('rover-last-update');
        if (updateElement) updateElement.textContent = '已更新';
    } catch (error) {
        if (error.name !== 'AbortError' && currentPage === 'monitor') {
            const updateElement = document.getElementById('rover-last-update');
            if (updateElement) updateElement.textContent = '暫時無法更新';
        }
    } finally {
        if (requestGeneration === roverPollingGeneration) {
            roverFetchInFlight = false;
            if (roverPollingAbortController === abortController) {
                roverPollingAbortController = null;
            }
        }
    }
}

function bindRoverStatusControls() {
    const filterInput = document.getElementById('rover-username-filter');
    if (!filterInput) return;
    filterInput.addEventListener('input', () => renderRoverTable(
        RoverState.filterByUsername(latestRoverSnapshot, filterInput.value)
    ));
}

function updateRoverSummary(rovers) {
    const summary = RoverState.summarize(rovers);
    const values = {
        'rover-summary-online': summary.online,
        'rover-summary-valid': summary.valid,
        'rover-summary-fixed': summary.fixed,
        'rover-summary-float': summary.float,
        'rover-summary-other': summary.other,
        'rover-summary-missing': summary.noPosition
    };
    Object.entries(values).forEach(([elementId, value]) => {
        const element = document.getElementById(elementId);
        if (element) element.textContent = String(value);
    });
}

function appendRoverTableCell(row, value, className = '') {
    const cell = document.createElement('td');
    if (className) cell.className = className;
    cell.textContent = value === null || value === undefined || value === ''
        ? '—'
        : String(value);
    row.appendChild(cell);
    return cell;
}

function renderRoverTable(rovers) {
    const container = document.getElementById('rover-table-container');
    if (!container) return;
    container.replaceChildren();

    const table = document.createElement('table');
    table.className = 'data-table rover-table';
    const header = document.createElement('thead');
    const headerRow = document.createElement('tr');
    [
        '使用者', '掛載點', '設備 / User-Agent', 'IP', '定位狀態',
        '衛星數', 'HDOP', '高程', '最後 GGA', '上線時間'
    ].forEach(label => {
        const cell = document.createElement('th');
        cell.textContent = label;
        headerRow.appendChild(cell);
    });
    header.appendChild(headerRow);
    table.appendChild(header);

    const body = document.createElement('tbody');
    if (rovers.length === 0) {
        const emptyRow = document.createElement('tr');
        const emptyCell = document.createElement('td');
        emptyCell.colSpan = 10;
        emptyCell.className = 'rover-table-empty';
        emptyCell.textContent = '目前沒有符合條件的在線 Rover。';
        emptyRow.appendChild(emptyCell);
        body.appendChild(emptyRow);
    } else {
        rovers.forEach(rover => {
            const row = document.createElement('tr');
            appendRoverTableCell(row, rover.username);
            appendRoverTableCell(row, rover.mount_name);
            appendRoverTableCell(row, rover.user_agent || '未知');
            appendRoverTableCell(row, rover.ip_address);

            const status = RoverState.getPositionStatus(rover);
            const statusCell = appendRoverTableCell(row, status.label);
            statusCell.classList.add('rover-position-status', `rover-status-${status.key}`);
            appendRoverTableCell(row, rover.satellites);
            appendRoverTableCell(row, formatRoverNumber(rover.hdop, 2));
            appendRoverTableCell(row, formatRoverNumber(rover.altitude, 1, ' m'));
            appendRoverTableCell(row, formatGgaAge(rover));
            appendRoverTableCell(row, rover.connect_datetime);
            body.appendChild(row);
        });
    }
    table.appendChild(body);
    container.appendChild(table);
}

function renderRoverStatus(rovers) {
    syncRoverMarkers(rovers);
    updateRoverSummary(rovers);
    const filterInput = document.getElementById('rover-username-filter');
    renderRoverTable(RoverState.filterByUsername(
        rovers,
        filterInput ? filterInput.value : ''
    ));
}

// RTCM监控内容
function getMonitorContent() {
    return `
        <div class="page-header">
            <h3><i class="fas fa-satellite-dish"></i> 基站 STR 資訊</h3>
            <p class="page-subtitle">即時監控 NTRIP 資料串流與基站狀態</p>
        </div>
        
        <div class="monitor-dashboard">
            <!-- 主要内容区域 -->
            <div class="monitor-grid">
                <div class="monitor-card full-width">
                    <div class="card-header">
                        <h4><i class="fas fa-location-arrow"></i> Rover 狀態總覽</h4>
                        <span id="rover-last-update" class="card-status">等待更新</span>
                    </div>
                    <div class="card-content rover-summary-grid">
                        <div class="rover-summary-item"><span>在線使用者</span><strong id="rover-summary-online">0</strong></div>
                        <div class="rover-summary-item"><span>有效位置</span><strong id="rover-summary-valid">0</strong></div>
                        <div class="rover-summary-item fixed"><span>RTK 固定</span><strong id="rover-summary-fixed">0</strong></div>
                        <div class="rover-summary-item float"><span>RTK 浮點</span><strong id="rover-summary-float">0</strong></div>
                        <div class="rover-summary-item other"><span>其他定位</span><strong id="rover-summary-other">0</strong></div>
                        <div class="rover-summary-item missing"><span>無位置 / 逾時</span><strong id="rover-summary-missing">0</strong></div>
                    </div>
                </div>

                <!-- STR数据表 - 全宽 -->
                <div class="monitor-card full-width">
                    <div class="card-header">
                        <h4><i class="fas fa-table"></i> STR 資料表</h4>
                    </div>
                    <div class="card-content" id="str-data">
                        <p class="loading-text"><i class="fas fa-spinner fa-spin"></i> 正在載入 STR 資料表...</p>
                    </div>
                </div>

                <!-- 基准站信息 - 全宽 -->
                <div class="monitor-card full-width">
                    <div class="card-header">
                        <h4><i class="fas fa-broadcast-tower"></i> 基站資訊</h4>
                        <div class="card-status" id="station-status">
                            <span class="status-dot waiting"></span>
                            <span>等待選擇</span>
                        </div>
                    </div>
                    <div class="card-content">
                        <div id="station-info" class="station-info-container">
                            <div class="empty-state">
                                <i class="fas fa-mouse-pointer"></i>
                                <p>請按一下 STR 資料表中的「資訊」按鈕以選擇掛載點</p>
                            </div>
                        </div>
                    </div>
                </div>

                <!-- 基准站位置 - 全宽 -->
                <div class="monitor-card full-width">
                    <div class="card-header">
                        <h4><i class="fas fa-map-marker-alt"></i> 基站與 Rover 即時位置</h4>
                        <span id="map-provider-label" class="map-provider-label">載入中</span>
                    </div>
                    <div class="card-content map-content">
                        <div id="map-container" class="map-container">
                            <div id="map" class="map-display"></div>
                            <div id="map-empty-state" class="map-empty-state" role="status">
                                尚未收到基站位置，先顯示台灣預設中心。
                            </div>
                            <div id="map-tile-error" class="map-tile-error" role="status" aria-live="polite" hidden>
                                地圖服務暫時無法使用；其他管理功能仍可正常使用。
                            </div>
                            <div id="map-marker-popup" class="map-marker-popup" hidden></div>
                        </div>
                        <div class="map-reference-legend" aria-label="單基站使用參考範圍說明">
                            <div class="map-reference-item">
                                <span class="map-reference-swatch map-reference-swatch-5" aria-hidden="true"></span>
                                <span><strong>5 km</strong>：單基站使用參考範圍</span>
                            </div>
                            <div class="map-reference-item">
                                <span class="map-reference-swatch map-reference-swatch-10" aria-hidden="true"></span>
                                <span><strong>10 km</strong>：單基站延伸使用參考範圍</span>
                            </div>
                            <p class="map-reference-note">※ 此範圍僅供使用參考，非定位精度保證。</p>
                        </div>
                    </div>
                </div>

                <div class="monitor-card full-width">
                    <div class="card-header rover-table-header">
                        <h4><i class="fas fa-list"></i> 在線 Rover</h4>
                        <label class="rover-filter-label" for="rover-username-filter">
                            <span>搜尋使用者</span>
                            <input id="rover-username-filter" type="search" autocomplete="off" placeholder="輸入 username">
                        </label>
                    </div>
                    <div class="card-content rover-table-container" id="rover-table-container">
                        <p class="loading-text">正在載入 Rover 狀態...</p>
                    </div>
                </div>

                <!-- 卫星数据可视化 - 全宽 -->
                <div class="monitor-card full-width">
                    <div class="card-header">
                        <h4><i class="fas fa-satellite"></i> 衛星資料視覺化</h4>
                        <div class="card-status" id="satellite-status">
                            <span class="status-dot waiting"></span>
                            <span>等待資料</span>
                        </div>
                    </div>
                    <div class="card-content">
                        <div id="satellite-container" class="satellite-container">
                            <div class="empty-state">
                                <i class="fas fa-satellite-dish"></i>
                                <p>等待衛星資料...</p>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    `;
}

// settings
function getSettingsContent() {
    return `
        <div class="page-header">
            <h3>系統設定</h3>
        </div>
        <div class="settings-container">
            <div class="settings-section">
                <h4>安全性設定</h4>
                <div class="form-group">
                    <label for="admin-password">新密碼：</label>
                    <input type="password" id="admin-password" placeholder="輸入新密碼" class="form-control">
                </div>
                <div class="form-group">
                    <label for="confirm-password">確認密碼：</label>
                    <input type="password" id="confirm-password" placeholder="再次輸入密碼" class="form-control">
                </div>
                <button onclick="changePassword()" class="btn btn-primary">變更管理員密碼</button>
            </div>

            <div class="settings-section">
                <h4>系統控制</h4>
                <button onclick="shutdownProgram()" class="btn btn-warning" style="background-color: #f39c12; border-color: #f39c12;">安全關閉程式</button>
            </div>
        </div>
    `;
}

// Socket.IO

socket.on('log_message', function(data) {
    addLogLine(data.message, data.type);
});

// user
socket.on('online_users_update', function(data) {
    window.onlineUserCount = Number(data.online_user_count) || 0;
    updateOnlineStatus();
});

// mounts
socket.on('online_mounts_update', function(data) {
    window.onlineMounts = data.mounts;
    updateOnlineStatus();
    refreshCurrentMapMarkerDetails();
});

// STR
socket.on('str_data_update', function(data) {
    window.strData = data.str_data;
    updateMonitorData();
});

// system
socket.on('system_stats_update', function(data) {
    if (currentPage === 'dashboard') {
        updateSystemStats(data.stats);
    } else if (currentPage === 'monitor') {
        updateMonitorStatus(data.stats);
    }
});

// 调试RTCM 数据
socket.on('rtcm_realtime_data', function(data) {
    // console.log('[前端接收] 收到RTCM实时数据:', data);
    // console.log('[前端接收] 数据类型:', typeof data);
    // console.log('[前端接收] 数据键:', Object.keys(data || {}));
    
    // 调试信息：显示除MSM类型外的其他数据类型
    if (data && data.data_type && data.data_type !== 'msm_satellite') {
        // console.log('[调试] 收到非MSM数据:', {
        //     数据类型: data.data_type,
        //     挂载点: data.mount_name || data.mount,
        //     时间戳: data.timestamp,
        //     数据内容: data
        // });
    }
    
    // 特别关注天线和设备信息
    if (data && data.data_type && ['device_info', 'antenna_info', 'receiver_info'].includes(data.data_type)) {
        // console.log('[天线设备调试] 收到天线/设备信息:', {
        //     数据类型: data.data_type,
        //     挂载点: data.mount_name || data.mount,
        //     接收机: data.receiver,
        //     固件: data.firmware,
        //     天线: data.antenna,
        //     天线序列号: data.antenna_firmware || data.antenna_serial,
        //     完整数据: data
        // });
    }
    
    if (!data || !data.data_type) {
        // console.warn('收到无效的RTCM数据:', data);
        return;
    }
    
    try {
        switch (data.data_type) {
            case 'station_position':
                // 处理基准站位置信息
                if (data.latitude && data.longitude) {
                    // console.log(`收到位置信息: ${data.latitude}, ${data.longitude}`);
                    currentMountName = data.mount_name || data.mount || currentMountName;
                    currentStationName = resolveStationDisplayName(data, currentMountName);
                    
                    if (!currentMap && currentPage === 'monitor') {
                        initializeMap();
                    }
                    
                    handlePositionUpdate(data.latitude, data.longitude, currentMountName);
                    
                    
                    updateElement('station-latitude', data.latitude.toFixed(6));
                    updateElement('station-longitude', data.longitude.toFixed(6));
                }
                break;
                
            case 'station_info':
               
                // console.log('收到基准站信息:', data);
                displayStationInfo(data);
                break;
                
            case 'msm_satellite':
               
                // console.log('收到卫星信号数据:', data);
                if (data.gnss && data.sats && Array.isArray(data.sats)) {
                    // 确保卫星可视化容器已初始化（只在第一次初始化）
                    if (currentPage === 'monitor') {
                        const satelliteContainer = document.getElementById('satellite-container');
                        if (satelliteContainer && !satelliteContainer.querySelector('.constellation-container')) {
                            initializeSatelliteVisualization();
                        }
                        
                        
                        const rtcmSatellites = data.sats.map(sat => ({
                            name: sat.id || sat.prn || '未知',
                            signalStrength: sat.snr || sat.signal_strength || 0,
                            frequency: sat.frequency || 0,
                            channel: sat.signal_type || '未知'
                        }));
                        
                        
                        let constellation = data.gnss.toUpperCase();
                        if (constellation === 'BDS' || constellation === 'BEIDOU') {
                            constellation = 'BDS';
                        } else if (constellation === 'GLONASS' || constellation === 'GLO') {
                            constellation = 'GLONASS';
                        } else if (constellation === 'GPS') {
                            constellation = 'GPS';
                        } else if (constellation === 'GALILEO') {
                            constellation = 'GALILEO';
                        } else if (constellation === 'QZSS') {
                            constellation = 'QZSS';
                        } else if (constellation === 'IRNSS') {
                            constellation = 'IRNSS';
                        } else if (constellation === 'NAVIC' || constellation === 'NAV') {
                            constellation = 'NAVIC';
                        }
                        
                        updateSatelliteVisualization(constellation, rtcmSatellites);
                    }
                }
                break;
                
            case 'geography':
                // （1005/1006）
                // console.log('[地理信息调试] 收到地理位置信息:', data);
    // console.log('[地理信息调试] 当前页面:', currentPage);
                
                // 只在monitor页面处理基准站信息显示
                if (currentPage !== 'monitor') {
                    // console.log('[地理信息调试] 不在monitor页面，跳过基准站信息显示');
                    break;
                }
                
                
                const stationInfoDiv = document.getElementById('station-info');
                // console.log('[地理信息调试] station-info元素:', stationInfoDiv);
    // console.log('[地理信息调试] station-info内容:', stationInfoDiv ? stationInfoDiv.innerHTML : 'station-info不存在');
    // console.log('[地理信息调试] 是否有empty-state:', stationInfoDiv ? stationInfoDiv.querySelector('.empty-state') : 'station-info不存在');
    // console.log('[地理信息调试] 是否有station-details:', stationInfoDiv ? stationInfoDiv.querySelector('.station-details') : 'station-info不存在');
                
                if (stationInfoDiv && (stationInfoDiv.querySelector('.empty-state') || !stationInfoDiv.querySelector('.station-details'))) {
                    // 如果还是空状态，先创建基础结构
                    // console.log('[地理信息调试] 检测到empty-state，创建基础结构');
                    const stationData = {
                        name: data.name || null,
                        mount_name: data.mount_name || data.mount || currentMountName,
                        station_name: data.station_name || null,
                        site_name: data.site_name || null,
                        display_name: data.display_name || null,
                        id: data.station_id || '未知',
                        country: data.country || '未知',
                        city: data.city || '未知',
                        latitude: data.lat || 0,
                        longitude: data.lon || 0,
                        height: data.height || '未知',
                        x: data.x || 0,
                        y: data.y || 0,
                        z: data.z || 0,
                        receiver: { name: '未知', firmware: '未知' },
                        antenna: { name: '未知', serial: '未知' }
                    };
                    // console.log('[地理信息调试] 准备显示基准站信息:', stationData);
                    displayStationInfo(stationData);
                    if (data.lat !== undefined && data.lon !== undefined) {
                        if (!currentMap) initializeMap();
                        handlePositionUpdate(data.lat, data.lon, currentMountName);
                    }
                } else {
                    // 如果结构已存在，直接更新数据
                    // console.log('[地理信息调试] 基础结构已存在，更新数据');
        // console.log('[地理信息调试] 完整数据内容:', data);
                    
                    
                    if (data.mount_name || data.mount) {
                        // console.log('[地理信息调试] 更新挂载点名称:', data.mount_name || data.mount);
                        currentMountName = data.mount_name || data.mount;
                        updateElement('station-name', currentMountName);
                    }
                    
                    
                    if (data.station_id !== undefined) {
                        // console.log('[地理信息调试] 更新基准站ID:', data.station_id);
                        updateElement('station-id', data.station_id.toString());
                        currentStationName = resolveStationDisplayName(data, currentMountName);
                    }
                    
                    
                    if (data.lat !== undefined && data.lon !== undefined) {
                        // console.log('[地理信息调试] 更新经纬度:', data.lat, data.lon);
                        
                        // 存储当前挂载点名称
                        currentMountName = data.mount_name || data.mount || currentMountName;
                        currentStationName = resolveStationDisplayName(data, currentMountName);
                        
                        
                        if (!currentMap && currentPage === 'monitor') {
                            initializeMap();
                        }
                        
                        handlePositionUpdate(data.lat, data.lon, currentMountName);
                        updateElement('station-latitude', data.lat.toFixed(6));
                        updateElement('station-longitude', data.lon.toFixed(6));
                    }
                    
                   
                    if (data.height !== undefined) {
                        // console.log('[地理信息调试] 更新高程:', data.height);
                        updateElement('station-height', data.height.toFixed(3) + ' m');
                    }
                    
                    // ECEF  XYZ
                    if (data.x !== undefined && data.y !== undefined && data.z !== undefined) {
                        // console.log('[地理信息调试] 更新XYZ坐标:', data.x, data.y, data.z);
                        updateElement('station-xyz', `X: ${data.x.toFixed(3)}, Y: ${data.y.toFixed(3)}, Z: ${data.z.toFixed(3)}`);
                    }
                    
                    // country
                    if (data.country || data.country_name) {
                        // console.log('[地理信息调试] 更新国家:', data.country_name || data.country);
                        updateElement('station-country', data.country_name || '未知');
                    }
                    
                    // city
                    if (data.city) {
                        // console.log('[地理信息调试] 更新城市:', data.city);
                        updateElement('station-city', data.city);
                    }
                }
                break;
                
            case 'device_info':
                // （1033）
                // console.log('收到设备信息:', data);
                if (data.receiver) {
                    updateElement('receiver-type', data.receiver);
                }
                if (data.firmware) {
                    updateElement('receiver-version', data.firmware);
                }
                if (data.antenna) {
                    updateElement('antenna-type', data.antenna);
                }
                if (data.antenna_firmware) {
                    updateElement('antenna-serial', data.antenna_firmware);
                }
                break;
                
            case 'antenna_info':
                // console.log('收到天线信息:', data);
                if (data.antenna_type) {
                    updateElement('antenna-type', data.antenna_type);
                }
                if (data.antenna_serial) {
                    updateElement('antenna-serial', data.antenna_serial);
                }
                break;
                
            case 'receiver_info':
                // console.log('收到接收机信息:', data);
                if (data.receiver_type) {
                    updateElement('receiver-type', data.receiver_type);
                }
                if (data.receiver_version) {
                    updateElement('receiver-version', data.receiver_version);
                }
                break;
                
            default:
                // console.log(`未处理的数据类型: ${data.data_type}`, data);
                break;
        }
    } catch (error) {
        // console.error('处理RTCM数据时发生错误:', error, data);
    }
});


function updateElement(id, value) {
    const element = document.getElementById(id);
    if (element) {
        element.textContent = value;
    }
}


function updateSystemStats(stats) {
    if (!stats) return;
    
    const timestamp = new Date().toLocaleString('zh-TW');
    updateElement('dashboard-timestamp', `最後更新：${timestamp}`);
    
    if (stats.uptime !== undefined) {
        updateElement('system-uptime', formatUptime(stats.uptime));
    }
    
    if (stats.cpu_percent !== undefined) {
        updateElement('system-cpu', `${stats.cpu_percent.toFixed(1)}%`);
    }
    
    if (stats.memory) {
        const memUsed = (stats.memory.used / (1024 * 1024 * 1024)).toFixed(1);
        const memTotal = (stats.memory.total / (1024 * 1024 * 1024)).toFixed(1);
        const memPercent = stats.memory.percent.toFixed(1);
        updateElement('system-memory', `${memPercent}%`);
        updateElement('system-memory-detail', `${memUsed}GB / ${memTotal}GB`);
    }
    
    if (stats.network_bandwidth) {
        const bandwidth = stats.network_bandwidth;
        let bandwidthText = '';
        if (bandwidth.sent_rate || bandwidth.recv_rate) {
            const sent = formatBytes(bandwidth.sent_rate);
            const recv = formatBytes(bandwidth.recv_rate);
            bandwidthText = `↑${sent}/s ↓${recv}/s`;
        } else {
            bandwidthText = '0 B/s';
        }
        updateElement('system-bandwidth', bandwidthText);
    }
    

    if (stats.connections) {
        const conn = stats.connections;
        updateElement('active-connections', conn.active || 0);
        updateElement('max-connections', conn.max_concurrent || 0);
        updateElement('total-connections', conn.total || 0);
        updateElement('rejected-connections', conn.rejected || 0);
    }
    
    if (stats.mounts) {
        updateElement('total-mounts', Object.keys(stats.mounts).length);
        updateMountDetails(stats.mounts);
    }
    
    if (stats.user_count !== undefined) {
        updateElement('total-users', Number(stats.user_count) || 0);
    }
    
    if (stats.data_transfer) {
        const transfer = stats.data_transfer;
        const totalData = formatBytes(transfer.total_bytes || 0);
        updateElement('total-data', totalData);
    }
}


function formatBytes(bytes) {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}


function requestSystemStats() {
    socket.emit('request_system_stats');
}

// （API）
async function fetchSystemStats() {
    try {
        const response = await fetch('/api/system/stats');
        if (response.ok) {
            const stats = await response.json();
            updateSystemStats(stats);
        } else {
            // console.error('获取系统统计数据失败:', response.status);
        }
    } catch (error) {
        // console.error('获取系统统计数据异常:', error);
    }
}


function updateMountDetails(mounts) {
    const container = document.getElementById('mounts-detail');
    if (!container) return;
    
    if (!mounts || mounts.length === 0) {
        container.innerHTML = '<div class="loading-text">目前沒有掛載點資料</div>';
        return;
    }
    
    const mountsHtml = mounts.map(mount => {
        const mountName = mount.mount_name || '未知';
        const userCount = mount.user_count || 0;
        const dataCount = mount.data_count || 0;
        const uptime = mount.uptime || 0;
        const status = mount.status || 'unknown';
        const statusText = {
            online: '線上',
            offline: '離線',
            validated: '已驗證',
            unknown: '未知'
        }[status] || status;
        
        // time
        const uptimeStr = formatUptime(uptime);
        
        return `
            <div class="mount-item">
                <div class="mount-name">${mountName}</div>
                <div class="mount-stats">
                    <div>👤 ${userCount} 位使用者</div>
            <div>📈 ${dataCount} 個資料封包</div>
                    <div>⏱️ ${uptimeStr}</div>
                    <div>⚙️ ${statusText}</div>
                </div>
            </div>
        `;
    }).join('');
    
    container.innerHTML = mountsHtml;
}


function formatUptime(seconds) {
    if (!seconds || seconds < 0) return '0 秒';
    
    const days = Math.floor(seconds / 86400);
    const hours = Math.floor((seconds % 86400) / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const secs = Math.floor(seconds % 60);
    
    if (days > 0) {
        return `${days} 天 ${hours} 小時 ${minutes} 分鐘`;
    } else if (hours > 0) {
        return `${hours} 小時 ${minutes} 分鐘`;
    } else if (minutes > 0) {
        return `${minutes} 分鐘 ${secs} 秒`;
    } else {
        return `${secs} 秒`;
    }
}

// 
function updateOnlineStatus() {
    
    if (currentPage === 'users') {
        const userRows = document.querySelectorAll('.user-row');
        userRows.forEach(row => {
            const username = row.dataset.username;
            const statusElement = row.querySelector('.user-status');
            if (statusElement) {
                if (window.onlineUsers) {
                    const isOnline = username in window.onlineUsers;
                    statusElement.innerHTML = isOnline ? 
                        '<span style="color: #28a745; font-weight: bold;">● 線上</span>' :
                        '<span style="color: #6c757d;">○ 離線</span>';
                }
            }
        });
    }
    

    if (currentPage === 'mounts') {
        const mountRows = document.querySelectorAll('.mount-row');
        mountRows.forEach(row => {
            const mountName = row.dataset.mount;
            const statusElement = row.querySelector('.mount-status');
            if (statusElement) {
                
                if (window.onlineMounts) {
                    const isOnline = mountName in window.onlineMounts;
                    statusElement.innerHTML = isOnline ? 
                        '<span style="color: #28a745; font-weight: bold;">● 線上</span>' :
                        '<span style="color: #6c757d;">○ 離線</span>';
                }
            }
        });
    }
    
    updateDashboardCounts();
}

//
function updateDashboardCounts() {
    // users
    const onlineUsersCount = Number(window.onlineUserCount) || 0;
    const dashboardOnlineUsersElement = document.getElementById('dashboard-online-users');
    if (dashboardOnlineUsersElement) {
        dashboardOnlineUsersElement.textContent = onlineUsersCount;
    }
    
    // mounts
    const activeMountsCount = window.onlineMounts ? Object.keys(window.onlineMounts).length : 0;
    const dashboardActiveMountsElement = document.getElementById('dashboard-active-mounts');
    if (dashboardActiveMountsElement) {
        dashboardActiveMountsElement.textContent = activeMountsCount;
    }
}

// INFO Buttons
                setTimeout(() => {
                    addInfoButtonsToSTRItems();
                }, 200);


function updateMonitorData() {
    if (currentPage === 'monitor' && window.strData) {
        const strDataElement = document.getElementById('str-data');
        if (strDataElement) {
            if (Object.keys(window.strData).length === 0) {
                strDataElement.innerHTML = '<div class="empty-state"><i class="fas fa-table"></i><p>目前沒有 STR 資料表資料</p></div>';
            } else {
                let strHtml = '';
                Object.entries(window.strData).forEach(([mountName, strContent]) => {
                    strHtml += `
                        <div class="str-row">
                            <button class="str-info-btn" data-mount="${mountName}">資訊</button>
                            <div class="str-content-wrapper">
                                <div class="str-content-inline">${strContent || '目前沒有資料'}</div>
                            </div>
                        </div>
                    `;

                });
                strDataElement.innerHTML = strHtml;
                
                addInfoButtonsToSTRItems();
            }
        }
    }
}


function refreshSTRData() {
    const strContainer = document.getElementById('str-data');
    if (strContainer) {
        strContainer.innerHTML = '<p class="loading-text"><i class="fas fa-spinner fa-spin"></i> 正在重新整理 STR 資料表...</p>';
    }
    
    
    if (socket && socket.connected) {
        socket.emit('request_str_data');
    }
}


function updateMonitorStatus(systemStatus) {
    
    const connectionStatus = document.getElementById('connection-status-monitor');
    if (connectionStatus) {
        connectionStatus.textContent = socket && socket.connected ? '已連線' : '已中斷連線';
    }
    
    
    const runtime = document.getElementById('runtime-monitor');
    if (runtime && systemStatus && systemStatus.uptime) {
        runtime.textContent = formatUptime(systemStatus.uptime);
    }
    
    
    const dataFlow = document.getElementById('data-flow-monitor');
    if (dataFlow && systemStatus && systemStatus.total_bytes) {
        dataFlow.textContent = formatBytes(systemStatus.total_bytes);
    }
}


function updateStationStatus(hasData) {
    const stationStatus = document.getElementById('station-status');
    if (stationStatus) {
        const statusDot = stationStatus.querySelector('.status-dot');
        const statusText = stationStatus.querySelector('span:last-child');
        
        if (hasData) {
            statusDot.className = 'status-dot online';
            statusText.textContent = '已選擇';
        } else {
            statusDot.className = 'status-dot waiting';
            statusText.textContent = '等待選擇';
        }
    }
}


function updateSatelliteStatus(hasData) {
    const satelliteStatus = document.getElementById('satellite-status');
    if (satelliteStatus) {
        const statusDot = satelliteStatus.querySelector('.status-dot');
        const statusText = satelliteStatus.querySelector('span:last-child');
        
        if (hasData) {
            statusDot.className = 'status-dot online';
            statusText.textContent = '接收中';
        } else {
            statusDot.className = 'status-dot waiting';
            statusText.textContent = '等待資料';
        }
    }
}


function validateAlphanumeric(input, fieldName) {
   
    const validPattern = /^[a-zA-Z0-9_-]+$/;
    
    if (!input || input.trim() === '') {
        return { valid: false, message: `${fieldName}不得為空白` };
    }
    
    if (!validPattern.test(input)) {
        return { valid: false, message: `${fieldName}僅能包含英文字母、數字、底線與連字號，不得包含其他特殊符號或中文字元` };
    }
    
    return { valid: true, message: '' };
}

// Add log line
// Debounced scroll function
let scrollTimeout = null;
function debouncedScroll(container) {
    if (scrollTimeout) {
        clearTimeout(scrollTimeout);
    }
    scrollTimeout = setTimeout(() => {
        container.scrollTop = container.scrollHeight;
    }, 10);
}

function addLogLine(message, type = 'info') {
    const logContainer = document.getElementById('log-terminal');
    if (logContainer) {
        // Use requestAnimationFrame to ensure DOM updates at appropriate time
        requestAnimationFrame(() => {
            const logEntry = document.createElement('div');
            logEntry.className = `log-line ${type}`;
            logEntry.textContent = `[${type.toUpperCase()}] ${message}`;
            
            // Disable animation to avoid flickering
            logEntry.style.animation = 'none';
            logEntry.style.transform = 'translateZ(0)'; // Enable hardware acceleration
            logEntry.style.willChange = 'auto';
            
            // Add directly to container, avoid extra overhead of document fragments
            logContainer.appendChild(logEntry);
            
            // Use debounced scroll
            debouncedScroll(logContainer);
            
            // Limit log entries, batch delete to reduce reflow
            const logEntries = logContainer.children;
            if (logEntries.length > 100) {
                // Delete first 10 entries to reduce frequent deletions
                requestAnimationFrame(() => {
                    for (let i = 0; i < 10 && logContainer.firstChild; i++) {
                        logContainer.removeChild(logContainer.firstChild);
                    }
                });
            }
        });
    }
}

// Initialization after page load completion
document.addEventListener('DOMContentLoaded', function() {
    // Initialize page
    const requestedPage = new URLSearchParams(window.location.search).get('page');
    const knownPages = ['dashboard', 'users', 'mounts', 'monitor', 'settings'];
    navigateTo(knownPages.includes(requestedPage) ? requestedPage : 'dashboard');
    
    // Load frequency mapping table
    loadFrequencyMap();
    

    
    // Load application information
    loadAppInfo();
    
    // Navigation event listeners
    document.querySelectorAll('.nav-item').forEach(item => {
        item.addEventListener('click', function(e) {
            e.preventDefault();
            const page = this.getAttribute('data-page');
            if (page) {
                navigateTo(page);
            }
        });
    });
});

// User management functions
function showAddUserForm() {
    const formHtml = `
        <div class="modal-overlay" id="userModal">
            <div class="modal-content">
                <h4>新增使用者</h4>
                <div class="form-group">
                    <label>使用者名稱</label>
                    <input type="text" id="newUsername" placeholder="輸入使用者名稱" maxlength="50">
                </div>
                <div class="form-group">
                    <label>密碼</label>
                    <input type="password" id="newPassword" placeholder="輸入密碼" maxlength="100">
                </div>
                <div class="form-actions">
                    <button class="btn btn-secondary" onclick="closeModal('userModal')">取消</button>
                    <button class="btn btn-success" onclick="submitAddUser()">新增</button>
                </div>
            </div>
        </div>
    `;
    document.body.insertAdjacentHTML('beforeend', formHtml);
}

function submitAddUser() {
    const username = document.getElementById('newUsername').value.trim();
    const password = document.getElementById('newPassword').value;
    
    // username
    const usernameValidation = validateAlphanumeric(username, '使用者名稱');
    if (!usernameValidation.valid) {
        showAlert(usernameValidation.message, 'error');
        return;
    }
    
    if (username.length < 3 || username.length > 50) {
        showAlert('使用者名稱長度必須介於 3 至 50 個字元', 'error');
        return;
    }
    
    // password
    const passwordValidation = validateAlphanumeric(password, '密碼');
    if (!passwordValidation.valid) {
        showAlert(passwordValidation.message, 'error');
        return;
    }
    
    if (password.length < 6 || password.length > 100) {
        showAlert('密碼長度必須介於 6 至 100 個字元', 'error');
        return;
    }
    
    addUser(username, password);
    closeModal('userModal');
}

async function addUser(username, password) {
    try {
        const response = await fetch('/api/users', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ username, password })
        });
        
        const result = await handleApiResponse(response);
        loadPageContent('users'); // Refresh user list
    } catch (error) {
        if (error.message !== '未授權存取') {
            showAlert('新增使用者失敗：' + error.message, 'error');
        }
    }
}

function editUser(username) {
    const isAdmin = username === 'admin';
    const formHtml = `
        <div class="modal-overlay" id="editUserModal">
            <div class="modal-content">
                <h4>編輯使用者 - ${username}</h4>
                ${!isAdmin ? `
                <div class="form-group">
                    <label>使用者名稱</label>
                    <input type="text" id="editUsername" value="${username}" maxlength="50">
                </div>
                ` : `
                <div class="form-group">
                    <label>使用者名稱</label>
                    <input type="text" value="${username}" disabled>
                    <small>管理員的使用者名稱無法修改</small>
                </div>
                `}
                <div class="form-group">
                    <label>新密碼（選填）</label>
                    <input type="password" id="editPassword" placeholder="留白可保留目前密碼" maxlength="100">
                </div>
                <div class="form-actions">
                    <button class="btn btn-secondary" onclick="closeModal('editUserModal')">取消</button>
                    <button class="btn btn-success" onclick="submitEditUser('${username}')">儲存</button>
                </div>
            </div>
        </div>
    `;
    document.body.insertAdjacentHTML('beforeend', formHtml);
}

function submitEditUser(originalUsername) {
    const newUsername = document.getElementById('editUsername')?.value.trim();
    const newPassword = document.getElementById('editPassword').value.trim();
    
    const updateData = {};
    
    // If password is entered, validate and add to update data
    if (newPassword) {
        const passwordValidation = validateAlphanumeric(newPassword, '密碼');
        if (!passwordValidation.valid) {
            showAlert(passwordValidation.message, 'error');
            return;
        }
        if (newPassword.length < 6 || newPassword.length > 100) {
            showAlert('密碼長度必須介於 6 至 100 個字元', 'error');
            return;
        }
        updateData.password = newPassword;
    }
    
    // If not admin and username has changed
    if (originalUsername !== 'admin' && newUsername && newUsername !== originalUsername) {
        const usernameValidation = validateAlphanumeric(newUsername, '使用者名稱');
        if (!usernameValidation.valid) {
            showAlert(usernameValidation.message, 'error');
            return;
        }
        if (newUsername.length < 3 || newUsername.length > 50) {
            showAlert('使用者名稱長度必須介於 3 至 50 個字元', 'error');
            return;
        }
        updateData.username = newUsername;
    }
    
    // Check if there are any updates
    if (Object.keys(updateData).length === 0) {
        showAlert('沒有需要儲存的變更', 'warning');
        return;
    }
    
    updateUser(originalUsername, updateData);
    closeModal('editUserModal');
}

async function updateUser(username, data) {
    try {
        const response = await fetch(`/api/users/${username}`, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(data)
        });                
        const result = await handleApiResponse(response);
        showAlert(result.message, 'success');
        loadPageContent('users'); // Refresh user list
    } catch (error) {
        if (error.message !== '未授權存取') {
            showAlert('更新使用者失敗：' + error.message, 'error');
        }
    }
}

function deleteUser(username) {
    // console.log('deleteUser called with username:', username);
    showConfirmDialog(
        '確認刪除使用者',
        `確定要刪除使用者「${username}」嗎？此操作無法復原。`,
        () => {
            // console.log('User confirmed deletion');
            removeUser(username);
        },
        () => {
            // console.log('User cancelled deletion');
        }
    );
}

async function removeUser(username) {
    // console.log('removeUser called with username:', username);
    try {
        // console.log('Sending DELETE request to:', `/api/users/${username}`);
        const response = await fetch(`/api/users/${username}`, {
            method: 'DELETE'
        });
        
        // console.log('Response status:', response.status);
        const result = await handleApiResponse(response);
        // console.log('API response result:', result);
        // Refresh list directly after successful deletion, no success popup
        loadPageContent('users'); // Refresh user list
    } catch (error) {
        // console.error('Error in removeUser:', error);
        if (error.message !== '未授權存取') {
            showAlert('刪除使用者失敗：' + error.message, 'error');
        }
    }
}

// Mount point management functions
async function showAddMountForm() {
    // Get user list for dropdown selection
    let usersOptions = '<option value="">不綁定使用者</option>';
    try {
        const response = await fetch('/api/users');
        if (response.ok) {
            const users = await response.json();
            users.forEach(user => {
                usersOptions += `<option value="${user.id}">${user.username}</option>`;
            });
        }
    } catch (error) {
        // console.error('Failed to get user list:', error);
    }
    
    const formHtml = `
        <div class="modal-overlay" id="mountModal">
            <div class="modal-content">
                <h4>新增掛載點</h4>
                <div class="form-group">
                    <label>掛載點名稱</label>
                    <input type="text" id="newMountName" placeholder="輸入掛載點名稱" maxlength="50">
                </div>
                <div class="form-group">
                    <label>密碼（NTRIP 1.0）</label>
                    <input type="password" id="newMountPassword" placeholder="輸入密碼" maxlength="100">
                </div>
                <div class="form-group">
                    <label>綁定使用者（NTRIP 2.0）</label>
                    <select id="newMountUser">
                        ${usersOptions}
                    </select>
                </div>
                <div class="form-actions">
                    <button class="btn btn-secondary" onclick="closeModal('mountModal')">取消</button>
                    <button class="btn btn-success" onclick="submitAddMount()">新增</button>
                </div>
            </div>
        </div>
    `;
    document.body.insertAdjacentHTML('beforeend', formHtml);
}

function submitAddMount() {
    const mountName = document.getElementById('newMountName').value.trim();
    const password = document.getElementById('newMountPassword').value;
    const userId = document.getElementById('newMountUser').value;
    
    // Validate mount point name
    const mountNameValidation = validateAlphanumeric(mountName, '掛載點名稱');
    if (!mountNameValidation.valid) {
        showAlert(mountNameValidation.message, 'error');
        return;
    }
    
    // Validate password
    const passwordValidation = validateAlphanumeric(password, '密碼');
    if (!passwordValidation.valid) {
        showAlert(passwordValidation.message, 'error');
        return;
    }
    
    if (mountName.length < 3 || mountName.length > 50) {
        showAlert('掛載點名稱長度必須介於 3 至 50 個字元', 'error');
        return;
    }
    
    if (password.length < 6 || password.length > 100) {
        showAlert('密碼長度必須介於 6 至 100 個字元', 'error');
        return;
    }
    
    const mountData = { mount: mountName, password: password };
    if (userId) {
        mountData.user_id = parseInt(userId);
    }
    
    addMount(mountData);
    closeModal('mountModal');
}

async function addMount(mountData) {
    try {
        const response = await fetch('/api/mounts', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(mountData)
        });
        
        const result = await handleApiResponse(response);
        loadPageContent('mounts'); // Refresh mount point list
    } catch (error) {
        if (error.message !== '未授權存取') {
            showAlert('新增掛載點失敗：' + error.message, 'error');
        }
    }
}

async function editMount(mount) {
    let currentMountData = null;
    let currentUsername = '';
    
    try {
        // Get current mount point information
        const mountResponse = await fetch('/api/mounts');
        if (mountResponse.ok) {
            const mounts = await mountResponse.json();
            currentMountData = mounts.find(m => m.mount === mount);
        }
        
        // If mount point is bound to a user, get username
        if (currentMountData && currentMountData.user_id) {
            const usersResponse = await fetch('/api/users');
            if (usersResponse.ok) {
                const users = await usersResponse.json();
                const currentUser = users.find(u => u.id === currentMountData.user_id);
                if (currentUser) {
                    currentUsername = currentUser.username;
                }
            }
        }
    } catch (error) {
        // console.error('Failed to get data:', error);
    }
    
    const formHtml = `
        <div class="modal-overlay" id="editMountModal">
            <div class="modal-content">
                <h4>編輯掛載點 - ${mount}</h4>
                <div class="form-group">
                    <label>掛載點名稱</label>
                    <input type="text" id="editMountName" value="${mount}" maxlength="50">
                </div>
                <div class="form-group">
                    <label>新密碼（NTRIP 1.0）</label>
                    <input type="password" id="editMountPassword" placeholder="留白可保留目前密碼" maxlength="100">
                </div>
                <div class="form-group">
                    <label>綁定使用者（NTRIP 2.0）</label>
                    <input type="text" id="editMountUser" value="${currentUsername}" placeholder="輸入使用者名稱；留白表示不綁定" maxlength="50">
                </div>
                <div class="form-actions">
                    <button class="btn btn-secondary" onclick="closeModal('editMountModal')">取消</button>
                    <button class="btn btn-success" onclick="submitEditMount('${mount}')">儲存</button>
                </div>
            </div>
        </div>
    `;
    document.body.insertAdjacentHTML('beforeend', formHtml);
}

async function submitEditMount(originalMount) {
    const newMountName = document.getElementById('editMountName').value.trim();
    const newPassword = document.getElementById('editMountPassword').value.trim();
    const username = document.getElementById('editMountUser').value.trim();
    
    const updateData = {};
    
    // If password is entered, validate and add to update data
    if (newPassword) {
        const passwordValidation = validateAlphanumeric(newPassword, '密碼');
        if (!passwordValidation.valid) {
            showAlert(passwordValidation.message, 'error');
            return;
        }
        if (newPassword.length < 6 || newPassword.length > 100) {
            showAlert('密碼長度必須介於 6 至 100 個字元', 'error');
            return;
        }
        updateData.password = newPassword;
    }
    
    // If mount point name has changed and is not empty
    if (newMountName && newMountName !== originalMount) {
        const mountNameValidation = validateAlphanumeric(newMountName, '掛載點名稱');
        if (!mountNameValidation.valid) {
            showAlert(mountNameValidation.message, 'error');
            return;
        }
        if (newMountName.length < 3 || newMountName.length > 50) {
            showAlert('掛載點名稱長度必須介於 3 至 50 個字元', 'error');
            return;
        }
        updateData.mount_name = newMountName;
    }
    
    // Handle username binding
    if (username) {
        const usernameValidation = validateAlphanumeric(username, '使用者名稱');
        if (!usernameValidation.valid) {
            showAlert(usernameValidation.message, 'error');
            return;
        }
    }
    updateData.username = username || "";
    
    // Check if there are any updates
    if (Object.keys(updateData).length === 0) {
        showAlert('沒有需要儲存的變更', 'warning');
        return;
    }
    
    updateMount(originalMount, updateData);
    closeModal('editMountModal');
}

async function updateMount(mount, data) {
    try {
        const response = await fetch(`/api/mounts/${mount}`, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(data)
        });
        
        const result = await handleApiResponse(response);
        showAlert(result.message, 'success');
        loadPageContent('mounts'); // Refresh mount point list
    } catch (error) {
        if (error.message !== '未授權存取') {
            showAlert('更新掛載點失敗：' + error.message, 'error');
        }
    }
}

function deleteMount(mount) {
    showConfirmDialog(
        '確認刪除掛載點',
        `確定要刪除掛載點「${mount}」嗎？此操作無法復原。`,
        () => {
            removeMount(mount);
        },
        () => {
            // User cancelled deletion
        }
    );
}

async function removeMount(mount) {
        try {
            const response = await fetch(`/api/mounts/${mount}`, {
                method: 'DELETE'
            });
            
            const result = await handleApiResponse(response);
            // Refresh list directly after successful deletion, no success popup
            loadPageContent('mounts'); // Refresh mount point list
        } catch (error) {
            if (error.message !== '未授權存取') {
                showAlert('刪除掛載點失敗：' + error.message, 'error');
            }
        }
    }
    

    
    async function changePassword() {
        const newPassword = document.getElementById('admin-password').value;
        const confirmPassword = document.getElementById('confirm-password').value;
        
        if (!newPassword || !confirmPassword) {
            showAlert('請輸入新密碼並再次確認', 'warning');
            return;
        }
        
        // Validate new password
        const passwordValidation = validateAlphanumeric(newPassword, '新密碼');
        if (!passwordValidation.valid) {
            showAlert(passwordValidation.message, 'error');
            return;
        }
        
        // Validate confirm password
        const confirmPasswordValidation = validateAlphanumeric(confirmPassword, '確認密碼');
        if (!confirmPasswordValidation.valid) {
            showAlert(confirmPasswordValidation.message, 'error');
            return;
        }
        
        if (newPassword !== confirmPassword) {
            showAlert('兩次輸入的密碼不一致', 'error');
            return;
        }
        
        if (newPassword.length < 6) {
            showAlert('密碼至少需要 6 個字元', 'error');
            return;
        }
        
        try {
            const response = await fetch('/api/users/admin', {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ password: newPassword })
            });
            
            const result = await response.json();
            
            if (response.ok) {
                showAlert('管理員密碼變更成功', 'success');
                document.getElementById('admin-password').value = '';
                document.getElementById('confirm-password').value = '';
            } else {
                showAlert('錯誤：' + result.error, 'error');
            }
        } catch (error) {
            // console.error('Failed to change password:', error);
            showAlert('變更密碼失敗：' + error.message, 'error');
        }
    }
    
    async function shutdownProgram() {
        showConfirmDialog(
        '確認安全關閉',
        '確定要安全關閉程式嗎？所有連線都會中斷，請謹慎操作。',
        async function() {
            await performShutdown();
        }
    );
}

async function performShutdown() {
        
        try {
            const response = await fetch('/api/system/restart', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                }
            });
            
            if (response.ok) {
                showAlert('已送出安全關機指令；如需重新啟動，請由服務管理器執行。', 'success');
            } else {
                const result = await response.json();
                showAlert('安全關機失敗：' + (result.error || '未知錯誤'), 'error');
            }
        } catch (error) {
            // console.error('Failed to shut down program:', error);
            showAlert('無法安全關閉程式：' + error.message, 'error');
        }
    }


function closeModal(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) {
        modal.remove();
    }
}


function showAlert(message, type = 'info') {
    const modalId = 'alertDialog';
    
    
    const existingModal = document.getElementById(modalId);
    if (existingModal) {
        existingModal.remove();
    }
    
    const iconMap = {
        'info': 'ℹ️',
        'success': '✔️',
        'error': '✖️',
        'warning': '⚠️'
    };
    
    const colorMap = {
        'info': '#3498db',
        'success': '#27ae60',
        'error': '#e74c3c',
        'warning': '#f39c12'
    };
    
    const modalHtml = `
        <div class="modal-overlay" id="${modalId}">
            <div class="modal-content" style="max-width: 400px; text-align: center;">
                <div style="font-size: 2rem; margin-bottom: 1rem;">${iconMap[type] || iconMap['info']}</div>
                <p style="margin-bottom: 2rem; color: #666; line-height: 1.5; font-size: 1.1rem;">${message}</p>
                <div style="display: flex; justify-content: center;">
                    <button class="btn" style="background: ${colorMap[type] || colorMap['info']}; color: white; border: none;" onclick="closeModal('${modalId}')">確定</button>
                </div>
            </div>
        </div>
    `;
    
    document.body.insertAdjacentHTML('beforeend', modalHtml);
    
    // Click background to close
    document.getElementById(modalId).addEventListener('click', function(e) {
        if (e.target === this) {
            closeModal(modalId);
        }
    });
    
    // Close with ESC key
    const escHandler = function(e) {
        if (e.key === 'Escape') {
            closeModal(modalId);
            document.removeEventListener('keydown', escHandler);
        }
    };
    document.addEventListener('keydown', escHandler);
}

// Show confirmation dialog
function showConfirmDialog(title, message, onConfirm, onCancel) {
    const modalId = 'confirmDialog';
    
    // Remove existing confirmation dialog
    const existingModal = document.getElementById(modalId);
    if (existingModal) {
        existingModal.remove();
    }
    
    const modalHtml = `
        <div class="modal-overlay" id="${modalId}">
            <div class="modal-content" style="max-width: 400px;">
                <h4>${title}</h4>
                <p style="margin-bottom: 2rem; color: #666; line-height: 1.5;">${message}</p>
                <div style="display: flex; gap: 1rem; justify-content: flex-end;">
                    <button class="btn btn-secondary" onclick="cancelConfirm()">取消</button>
                    <button class="btn btn-primary" onclick="confirmAction()">確定</button>
                </div>
            </div>
        </div>
    `;
    
    document.body.insertAdjacentHTML('beforeend', modalHtml);
    
    // Temporarily store callback functions
    window.tempConfirmCallback = onConfirm;
    window.tempCancelCallback = onCancel;
    
    
    document.getElementById(modalId).addEventListener('click', function(e) {
        if (e.target === this) {
            cancelConfirm();
        }
    });
}

// Confirm action
function confirmAction() {
    if (window.tempConfirmCallback) {
        window.tempConfirmCallback();
    }
    closeModal('confirmDialog');
    // Clean up temporary callbacks
    window.tempConfirmCallback = null;
    window.tempCancelCallback = null;
}

// Cancel action
function cancelConfirm() {
    if (window.tempCancelCallback) {
        window.tempCancelCallback();
    }
    closeModal('confirmDialog');
    // Clean up temporary callbacks
    window.tempConfirmCallback = null;
    window.tempCancelCallback = null;
}
    
    // Click modal background to close
    document.addEventListener('click', function(e) {
        if (e.target.classList.contains('modal-overlay')) {
            e.target.remove();
        }
    });
    
    // Close modal with ESC key
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape') {
            const modals = document.querySelectorAll('.modal-overlay');
            modals.forEach(modal => modal.remove());
        }
    });
    
    // Logout function
    function logout() {
        // Simplified logout process, execute logout operation directly
        showConfirmDialog(
            '確認登出',
            '確定要登出嗎？',
            () => {
                stopRoverPolling();
                fetch('/logout', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    }
                }).then(() => {
                    window.location.href = '/login';
                }).catch(error => {
                     // console.error('Logout failed:', error);
                     window.location.href = '/login';
                 });
             },
             () => {
                 // User cancelled logout
             }
         );
    }


async function loadAppInfo() {
    try {
        const response = await fetch('/api/app_info');
        if (response.ok) {
            const appInfo = await response.json();
            
            // Update footer information
            document.getElementById('app-name').textContent = appInfo.name;
            document.getElementById('app-version').textContent = `v${appInfo.version}`;
            document.getElementById('app-author').textContent = appInfo.author;
            
            const contactElement = document.getElementById('app-contact');
            contactElement.textContent = appInfo.contact;
            contactElement.href = `mailto:${appInfo.contact}`;
            
            const websiteElement = document.getElementById('app-website');
            websiteElement.textContent = appInfo.website.replace('https://', '').replace('http://', '');
            websiteElement.href = appInfo.website;
        }
    } catch (error) {
        // console.error('Failed to load application information:', error);
    }
}
