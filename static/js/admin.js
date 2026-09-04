/* ============================================================
   WASDW SOC Admin Panel — admin.js
   - Token: URL query param ?token= veya window.__ADMIN_TOKEN
   - Stats: /api/admin/stats (5s polling)
   - Logs : /api/admin/logs (sayfa yüklenince + WS push)
   - Bans : /api/admin/bans (30s polling)
   ============================================================ */
(function () {
    'use strict';

    /* ── Token ── */
    const ADMIN_TOKEN = (function () {
        if (window.__ADMIN_TOKEN) return window.__ADMIN_TOKEN;
        const p = new URLSearchParams(location.search);
        return p.get('token') || '';
    })();

    const SERVER_HOST = window.__SERVER_HOST || location.host;

    const AUTH_HEADERS = {
        'X-Admin-Token': ADMIN_TOKEN,
        'Content-Type':  'application/json',
    };

    /* ── DOM refs ── */
    const $ = id => document.getElementById(id);

    const els = {
        wsStatus:      $('wsStatus'),
        logsContainer: $('logsContainer'),
        bansContainer: $('bansContainer'),
        bansCount:     $('bansCount'),
        // stat cards
        totalEvents:   $('statTotalEvents'),
        totalBlocked:  $('statTotalBlocked'),
        vpnBlocked:    $('statVpnBlocked'),
        botBlocked:    $('statBotBlocked'),
        powFailed:     $('statPowFailed'),
        powSolved:     $('statPowSolved'),
        wallPassed:    $('statWallPassed'),
        successLogins: $('statSuccessLogins'),
        avgSolve:      $('statAvgSolve'),
        avgScore:      $('statAvgScore'),
        activeBans:    $('statActiveBans'),
        totalUsers:    $('statTotalUsers'),
        // info
        apiAddr:       $('infoApiAddr'),
        dbPath:        $('infoDbPath'),
        logCount:      $('logsCount'),
    };

    /* ── Helpers ── */
    function fmt(n)   { return (n === undefined || n === null) ? '—' : Number(n).toLocaleString(); }
    function fmtF(n)  { return (n === undefined || n === null) ? '—' : parseFloat(n).toFixed(2); }
    function fmtTime(ts) {
        if (!ts) return '—';
        const d = new Date(ts * 1000);
        return d.toLocaleTimeString('tr-TR', { hour12: false }) +
               ' ' + d.toLocaleDateString('tr-TR', { day: '2-digit', month: '2-digit' });
    }
    function relTime(ts) {
        const diff = Math.floor((Date.now() / 1000) - ts);
        if (diff < 60)  return diff + 's önce';
        if (diff < 3600) return Math.floor(diff / 60) + 'dk önce';
        if (diff < 86400) return Math.floor(diff / 3600) + 'sa önce';
        return Math.floor(diff / 86400) + 'g önce';
    }
    function escHtml(s) {
        return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    }
    function set(el, val) { if (el) el.textContent = val; }

    /* ── Stats fetch & render ── */
    async function fetchStats() {
        try {
            const r = await fetch('/api/admin/stats', { headers: AUTH_HEADERS });
            if (!r.ok) return;
            const s = await r.json();
            set(els.totalEvents,   fmt(s.total_events));
            set(els.totalBlocked,  fmt(s.total_blocked));
            set(els.vpnBlocked,    fmt(s.vpn_blocked));
            set(els.botBlocked,    fmt(s.bot_blocked));
            set(els.powFailed,     fmt(s.pow_failed));
            set(els.powSolved,     fmt(s.pow_solved));
            set(els.wallPassed,    fmt(s.wall_passed));
            set(els.successLogins, fmt(s.success_logins));
            set(els.avgSolve,      fmtF(s.avg_solve_sec) + 's');
            set(els.avgScore,      fmtF(s.avg_bot_score));
            set(els.activeBans,    fmt(s.active_bans));
            set(els.totalUsers,    fmt(s.total_users));
        } catch (e) { /* sessiz geç */ }
    }

    /* ── Logs fetch (initial) ── */
    let _logsSeen = new Set();

    async function fetchLogs() {
        try {
            const r = await fetch('/api/admin/logs?limit=100', { headers: AUTH_HEADERS });
            if (!r.ok) return;
            const data = await r.json();
            const logs = data.logs || [];
            if (els.logCount) els.logCount.textContent = data.count + ' kayıt';
            // Tarihe göre sırala (yeni→eski)
            logs.sort((a, b) => b.timestamp - a.timestamp);
            if (els.logsContainer) els.logsContainer.innerHTML = '';
            _logsSeen = new Set();
            logs.forEach(log => addLogEntry(log, false));
        } catch (e) { /* sessiz geç */ }
    }

    /* ── Log entry render ── */
    function addLogEntry(log, isNew) {
        if (!els.logsContainer) return;
        if (_logsSeen.has(log.id)) return;
        _logsSeen.add(log.id);

        const blocked  = !!log.blocked;
        const level    = (log.threat_level || '').toUpperCase();
        const details  = log.details || {};

        const badgeMap = {
            CRITICAL: 'badge-critical',
            HIGH:     'badge-high',
            MEDIUM:   'badge-medium',
            LOW:      'badge-low',
            SAFE:     'badge-safe',
            INFO:     'badge-info',
        };
        const badgeCls = badgeMap[level] || 'badge-info';

        // PoW / bot detayları
        const solveLine = details.solve_time != null
            ? `<span class="detail-tag">⏱ ${parseFloat(details.solve_time).toFixed(2)}s</span>` : '';
        const scoreLine = details.bot_score != null
            ? `<span class="detail-tag ${details.bot_score > 40 ? 'detail-warn' : ''}">🤖 ${details.bot_score}</span>` : '';
        const hitLine   = details.hit_count != null
            ? `<span class="detail-tag detail-warn">hit×${details.hit_count}</span>` : '';
        const extraDetails = solveLine + scoreLine + hitLine;

        const ua = escHtml((log.user_agent || '').substring(0, 90));

        const row = document.createElement('div');
        row.className = `log-entry ${blocked ? 'blocked' : 'allowed'} ${isNew ? 'pulse-new' : ''}`;
        row.dataset.id = log.id;
        row.innerHTML = `
            <div class="log-time">${fmtTime(log.timestamp)}</div>
            <div class="log-ip" title="${escHtml(log.ip)}">${escHtml(log.ip || '—')}</div>
            <div class="log-event"><span class="badge ${badgeCls}">${escHtml(log.event_type || '')}</span></div>
            <div class="log-reason">
                <strong>${escHtml(log.reason || '')}</strong>
                ${extraDetails ? `<div class="log-details">${extraDetails}</div>` : ''}
                ${ua ? `<div class="log-ua">${ua}</div>` : ''}
            </div>
            <div class="log-status">
                <span class="${blocked ? 'status-blocked' : 'status-passed'}">
                    ${blocked ? '🚫' : '✅'}
                </span>
            </div>`;

        if (isNew) {
            els.logsContainer.insertBefore(row, els.logsContainer.firstChild);
            // 200 girişten fazlasını temizle — bellek tasarrufu
            const children = els.logsContainer.children;
            if (children.length > 200) {
                const last = children[children.length - 1];
                if (last.dataset.id) _logsSeen.delete(Number(last.dataset.id));
                last.remove();
            }
        } else {
            els.logsContainer.appendChild(row);
        }
    }

    /* ── Ban listesi ── */
    async function fetchBans() {
        try {
            const r = await fetch('/api/admin/bans', { headers: AUTH_HEADERS });
            if (!r.ok) return;
            const data = await r.json();
            const bans = data.bans || [];
            if (els.bansCount) els.bansCount.textContent = bans.length + ' aktif ban';
            if (!els.bansContainer) return;
            if (bans.length === 0) {
                els.bansContainer.innerHTML = '<div class="empty-msg">Aktif ban yok</div>';
                return;
            }
            els.bansContainer.innerHTML = bans.map(b => {
                const remaining = Math.max(0, Math.floor(b.expires_at - Date.now() / 1000));
                const remStr = remaining < 60 ? remaining + 's'
                             : remaining < 3600 ? Math.floor(remaining / 60) + 'dk'
                             : Math.floor(remaining / 3600) + 'sa';
                return `<div class="ban-row">
                    <span class="ban-ip">${escHtml(b.ip)}</span>
                    <span class="ban-reason">${escHtml(b.reason)}</span>
                    <span class="ban-exp">${remStr} kaldı</span>
                    <button class="ban-lift" data-ip="${escHtml(b.ip)}">Kaldır</button>
                </div>`;
            }).join('');
            // Ban kaldırma butonları
            els.bansContainer.querySelectorAll('.ban-lift').forEach(btn => {
                btn.addEventListener('click', async function () {
                    const ip = this.dataset.ip;
                    try {
                        await fetch('/api/admin/clear-bans', {
                            method: 'POST',
                            headers: AUTH_HEADERS,
                        });
                        fetchBans();
                        fetchStats();
                    } catch (e) {}
                });
            });
        } catch (e) { /* sessiz geç */ }
    }

    /* ── WebSocket ── */
    function connectWebSocket() {
        const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
        // Token'i WS URL'e ekle — WS header desteği kısıtlı
        const wsUrl = `${proto}//${location.host}/ws/soc-feed?token=${encodeURIComponent(ADMIN_TOKEN)}`;
        const ws = new WebSocket(wsUrl);

        ws.onopen = () => {
            if (els.wsStatus) els.wsStatus.innerHTML =
                '<span class="ws-dot online"></span> Canlı İzleme Bağlandı';
        };
        ws.onmessage = (ev) => {
            try {
                const data = JSON.parse(ev.data);
                if (data.type === 'new_log') {
                    addLogEntry(data.log, true);
                    // Yeni log gelince stats sayacını da yenile (hızlı güncelleme)
                    fetchStats();
                }
            } catch (e) {}
        };
        ws.onclose = () => {
            if (els.wsStatus) els.wsStatus.innerHTML =
                '<span class="ws-dot offline"></span> Bağlantı Kesildi (yeniden deneniyor...)';
            setTimeout(connectWebSocket, 4000);
        };
        ws.onerror = () => ws.close();
    }

    /* ── Info kutusu doldur ── */
    function fillInfo() {
        const proto = location.protocol;
        if (els.apiAddr) {
            const host = SERVER_HOST || location.host;
            els.apiAddr.textContent = `${proto}//${host}`;
        }
    }

    /* ── Clear all bans butonu ── */
    const clearAllBtn = $('btnClearAllBans');
    if (clearAllBtn) {
        clearAllBtn.addEventListener('click', async () => {
            if (!confirm('Tüm IP banlarını kaldır?')) return;
            try {
                const r = await fetch('/api/admin/clear-bans', { method: 'POST', headers: AUTH_HEADERS });
                if (r.ok) { fetchBans(); fetchStats(); }
            } catch (e) {}
        });
    }

    /* ── Başlat ── */
    fillInfo();
    fetchStats();
    fetchLogs();
    fetchBans();
    connectWebSocket();

    // Periyodik polling
    setInterval(fetchStats, 5000);   // 5s
    setInterval(fetchBans,  30000);  // 30s

})();
