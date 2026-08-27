(function(globalObject) {
    'use strict';

    function numericQuality(rover) {
        const quality = Number(rover && rover.gga_fix_quality);
        return Number.isFinite(quality) ? quality : null;
    }

    function getPositionStatus(rover) {
        const quality = numericQuality(rover);
        if (!rover || !rover.last_gga_time) {
            return { key: 'missing', label: '無位置資料' };
        }
        if (quality === 0) {
            return { key: 'no-fix', label: '無定位' };
        }
        if (rover.has_valid_position && !rover.position_fresh) {
            return { key: 'stale', label: '位置逾時' };
        }
        if (!rover.has_valid_position) {
            return { key: 'missing', label: '無有效位置' };
        }
        if (quality === 4) {
            return { key: 'fixed', label: 'RTK 固定' };
        }
        if (quality === 5) {
            return { key: 'float', label: 'RTK 浮點' };
        }
        if (quality === 1) {
            return { key: 'other', label: '單點' };
        }
        if (quality === 2) {
            return { key: 'other', label: 'DGPS' };
        }
        return {
            key: 'other',
            label: quality === null ? '其他定位' : `其他定位 (${quality})`
        };
    }

    function hasMarkerPosition(rover) {
        const latitude = Number(rover && rover.latitude);
        const longitude = Number(rover && rover.longitude);
        return Boolean(
            rover
            && rover.connection_id
            && rover.has_valid_position
            && numericQuality(rover) !== 0
            && Number.isFinite(latitude)
            && Number.isFinite(longitude)
            && latitude >= -90
            && latitude <= 90
            && longitude >= -180
            && longitude <= 180
        );
    }

    function summarize(rovers) {
        const summary = {
            online: 0,
            valid: 0,
            fixed: 0,
            float: 0,
            other: 0,
            noPosition: 0
        };
        (Array.isArray(rovers) ? rovers : []).forEach(rover => {
            summary.online += 1;
            const valid = Boolean(rover.has_valid_position && rover.position_fresh);
            if (!valid) {
                summary.noPosition += 1;
                return;
            }
            summary.valid += 1;
            const quality = numericQuality(rover);
            if (quality === 4) summary.fixed += 1;
            else if (quality === 5) summary.float += 1;
            else summary.other += 1;
        });
        return summary;
    }

    function filterByUsername(rovers, query) {
        const normalizedQuery = String(query || '').trim().toLocaleLowerCase();
        const snapshot = Array.isArray(rovers) ? rovers : [];
        if (!normalizedQuery) return snapshot.slice();
        return snapshot.filter(rover => String(rover.username || '')
            .toLocaleLowerCase()
            .includes(normalizedQuery));
    }

    function reconcileMarkers(rovers, markerMap, callbacks) {
        const seen = new Set();
        (Array.isArray(rovers) ? rovers : []).forEach(rover => {
            if (!hasMarkerPosition(rover)) return;
            const connectionId = String(rover.connection_id);
            seen.add(connectionId);
            if (markerMap.has(connectionId)) {
                callbacks.update(markerMap.get(connectionId), rover);
            } else {
                markerMap.set(connectionId, callbacks.create(rover));
            }
        });

        Array.from(markerMap.entries()).forEach(([connectionId, marker]) => {
            if (seen.has(connectionId)) return;
            callbacks.remove(marker, connectionId);
            markerMap.delete(connectionId);
        });
    }

    globalObject.RoverState = Object.freeze({
        filterByUsername,
        getPositionStatus,
        hasMarkerPosition,
        reconcileMarkers,
        summarize
    });
})(typeof window !== 'undefined' ? window : globalThis);
