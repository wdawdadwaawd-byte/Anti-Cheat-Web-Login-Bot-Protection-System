/**
 * WASDW Behavioral Collector — Server-Side Analysis
 * 
 * Ham event time-series toplar (mouse, rAF, scroll), client-side scoring YOK.
 * Data sunucuya POST → backend ML-based scoring.
 * 
 * Design:
 * - Client: Event listener + buffer
 * - Buffer: 100 event max (memory efficient)
 * - Metrics: (timestamp, x, y, type) tuple'ları — threshold YOK
 * - Server: `/api/behavioral-submit` endpoint → Python ML scoring
 * 
 * Anti-bypass:
 * - Client-side threshold YOK → reverse engineer edilemez
 * - Ham data → velocity, acceleration, entropy server-side hesaplanır
 * - Adaptive baseline (per-user) → synthetic detection
 */

(function(window) {
    'use strict';
    
    // ── Config ────────────────────────────────────────────────────────────
    var MAX_EVENTS = 100;           // Buffer limit (memory constraint)
    var SUBMIT_DELAY = 3000;        // Auto-submit after 3s idle
    var ENDPOINT = '/api/behavioral-submit';
    
    // ── State ─────────────────────────────────────────────────────────────
    var eventBuffer = [];
    var rafBuffer = [];
    var scrollBuffer = [];
    var startTime = Date.now();
    var submitTimer = null;
    var submitted = false;
    
    // ── Mouse tracking ────────────────────────────────────────────────────
    function onMouseMove(e) {
        if (eventBuffer.length >= MAX_EVENTS) return;
        eventBuffer.push({
            t: Date.now() - startTime,  // relative timestamp (ms)
            x: e.clientX,
            y: e.clientY,
            type: 'mouse'
        });
        scheduleSubmit();
    }
    
    // ── rAF jitter tracking ───────────────────────────────────────────────
    var lastRAF = 0;
    function rafCallback(timestamp) {
        if (lastRAF > 0 && rafBuffer.length < MAX_EVENTS) {
            var delta = timestamp - lastRAF;
            rafBuffer.push({
                t: Date.now() - startTime,
                delta: delta,  // ms between frames
                type: 'raf'
            });
        }
        lastRAF = timestamp;
        if (rafBuffer.length < MAX_EVENTS) {
            requestAnimationFrame(rafCallback);
        }
    }
    
    // ── Scroll tracking ───────────────────────────────────────────────────
    function onScroll(e) {
        if (scrollBuffer.length >= MAX_EVENTS) return;
        scrollBuffer.push({
            t: Date.now() - startTime,
            x: window.scrollX || window.pageXOffset || 0,
            y: window.scrollY || window.pageYOffset || 0,
            type: 'scroll'
        });
        scheduleSubmit();
    }
    
    // ── Auto-submit scheduler ─────────────────────────────────────────────
    function scheduleSubmit() {
        if (submitTimer) clearTimeout(submitTimer);
        submitTimer = setTimeout(submitData, SUBMIT_DELAY);
    }
    
    // ── Submit to server ──────────────────────────────────────────────────
    function submitData() {
        if (submitted) return;
        submitted = true;
        
        var payload = {
            mouse: eventBuffer,
            raf: rafBuffer,
            scroll: scrollBuffer,
            meta: {
                duration: Date.now() - startTime,
                userAgent: navigator.userAgent,
                screen: {
                    width: window.screen.width,
                    height: window.screen.height
                },
                viewport: {
                    width: window.innerWidth,
                    height: window.innerHeight
                }
            }
        };
        
        // Fetch POST (async, non-blocking)
        fetch(ENDPOINT, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
            credentials: 'same-origin'
        }).then(function(response) {
            if (response.ok) {
                console.log('[WASDW] Behavioral data submitted');
            } else {
                console.warn('[WASDW] Behavioral submit failed:', response.status);
            }
        }).catch(function(err) {
            console.error('[WASDW] Behavioral submit error:', err);
        });
    }
    
    // ── Public API ────────────────────────────────────────────────────────
    window.WASDWBehavior = {
        start: function() {
            // Attach listeners
            document.addEventListener('mousemove', onMouseMove, { passive: true });
            document.addEventListener('scroll', onScroll, { passive: true });
            
            // Start rAF sampling
            if (typeof requestAnimationFrame !== 'undefined') {
                requestAnimationFrame(rafCallback);
            }
            
            console.log('[WASDW] Behavioral collector started');
        },
        
        stop: function() {
            document.removeEventListener('mousemove', onMouseMove);
            document.removeEventListener('scroll', onScroll);
            console.log('[WASDW] Behavioral collector stopped');
        },
        
        forceSubmit: function() {
            submitData();
        },
        
        getStats: function() {
            return {
                mouse: eventBuffer.length,
                raf: rafBuffer.length,
                scroll: scrollBuffer.length,
                submitted: submitted
            };
        }
    };
    
    // Auto-start on DOMContentLoaded
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function() {
            window.WASDWBehavior.start();
        });
    } else {
        window.WASDWBehavior.start();
    }
    
})(window);
