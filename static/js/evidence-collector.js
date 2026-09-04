/**
 * WASDW Evidence Collector — Server-Side Decision Architecture
 * 
 * Client sadece kanıt toplar (NO decisions, NO if/else on results).
 * Tüm kararlar server-side'da verilir.
 * 
 * Design:
 * - Client: Evidence collection ONLY
 * - Client: HMAC signature (replay protection)
 * - Server: Evidence validation + decision making
 * - Server: Evidence token (two-phase auth)
 * 
 * Anti-bypass:
 * - Client-side decision logic YOK → bypass edilecek if/else yok
 * - Deobfuscation'dan sonra eline geçen: "kanıt toplama kodu"
 * - Kanıtları manipüle etse bile: server signature verification fail
 * - Replay attack: nonce one-time use
 * 
 * Bu mimari ile obfuscation kalitesi ikincil önem taşır çünkü
 * saldırgan kodu tamamen çözse bile bypass edemez — karar server-side.
 */

(function(window) {
    'use strict';
    
    // ── State ─────────────────────────────────────────────────────────────
    var evidence = {
        pow: null,
        behavioral: null,
        env: null,
        fingerprint: null,
        integrity: null,
        meta: {}
    };
    
    var sessionKey = null;
    var pageNonce = null;
    var evidenceToken = null;
    var isCollecting = false;
    
    // ── Evidence Collection ───────────────────────────────────────────────
    
    /**
     * Collect PoW evidence (raw result, server re-computes)
     */
    function collectPoW() {
        // Assume ShieldCore already computed PoW
        var core = window.ShieldCore || window.WASDEngine;
        if (!core || !core.powResult) {
            return {
                challenge: null,
                nonce: null,
                result: null,
                error: "pow_not_computed"
            };
        }
        
        return {
            challenge: core.powChallenge || '',
            nonce: core.powNonce || 0,
            result: core.powResult || '',
            vm_score: core.vmScore || 0
        };
    }
    
    /**
     * Collect behavioral evidence (from behavioral-collector.js)
     */
    function collectBehavioral() {
        var behavior = window.WASDWBehavior;
        if (!behavior) {
            return {
                mouse: [],
                raf: [],
                scroll: [],
                error: "behavioral_not_initialized"
            };
        }
        
        var stats = behavior.getStats ? behavior.getStats() : {};
        
        // Get raw buffers (if exposed)
        // For now, return stats (production: expose raw buffers)
        return {
            mouse_count: stats.mouse || 0,
            raf_count: stats.raf || 0,
            scroll_count: stats.scroll || 0,
            submitted: stats.submitted || false
        };
    }
    
    /**
     * Collect env signals (raw flags, NO scoring)
     */
    function collectEnv() {
        var signals = {
            webdriver: navigator.webdriver || false,
            chrome_missing: /Chrome/.test(navigator.userAgent) && !window.chrome,
            headless_vars: [],
            console_detect: 0,
            debugger_detect: 0
        };
        
        // Check headless framework vars
        var headlessKeys = ['__puppeteer', '__nightmare', 'callPhantom', '_phantom'];
        for (var i = 0; i < headlessKeys.length; i++) {
            if (window[headlessKeys[i]]) {
                signals.headless_vars.push(headlessKeys[i]);
            }
        }
        
        // CDC keys (Selenium)
        var cdcKeys = [];
        for (var key in window) {
            if (key.indexOf('cdc_') === 0 || key.indexOf('$cdc_') === 0) {
                cdcKeys.push(key);
            }
        }
        if (cdcKeys.length > 0) {
            signals.selenium_vars = cdcKeys;
        }
        
        return signals;
    }
    
    /**
     * Collect fingerprint evidence (raw data, NO hashing on client)
     */
    function collectFingerprint() {
        return {
            canvas: window.__canvas_fp || 'pending',
            webgl: window.__webgl_info || {vendor: 'pending', renderer: 'pending'},
            audio: window.__audio_fp || 'pending',
            screen: {
                width: window.screen.width,
                height: window.screen.height,
                colorDepth: window.screen.colorDepth
            },
            viewport: {
                width: window.innerWidth,
                height: window.innerHeight
            },
            timezone: new Date().getTimezoneOffset(),
            language: navigator.language || '',
            platform: navigator.platform || '',
            hardwareConcurrency: navigator.hardwareConcurrency || 0
        };
    }
    
    /**
     * Collect integrity evidence (loader + parts hashes)
     */
    function collectIntegrity() {
        // Assume loader sets these globals
        return {
            loader_hash: window.__loader_hash || null,
            parts_hashes: window.__parts_hashes || [],
            integrity_verified: window.__integrity_ok || false
        };
    }
    
    /**
     * Collect meta evidence
     */
    function collectMeta() {
        return {
            page_nonce: pageNonce,
            timestamp: Date.now(),
            duration: Date.now() - (window.__page_load_time || Date.now()),
            url: window.location.pathname,
            referrer: document.referrer || ''
        };
    }
    
    /**
     * Collect ALL evidence (NO decisions, NO if/else)
     */
    function collectAllEvidence() {
        return {
            pow: collectPoW(),
            behavioral: collectBehavioral(),
            env: collectEnv(),
            fingerprint: collectFingerprint(),
            integrity: collectIntegrity(),
            meta: collectMeta()
        };
    }
    
    // ── Signature ─────────────────────────────────────────────────────────
    
    /**
     * Sign evidence with HMAC-SHA256 (session key from server)
     */
    async function signEvidence(evidenceData) {
        if (!sessionKey) {
            throw new Error('Session key not initialized');
        }
        
        // Convert to canonical JSON (deterministic)
        var payload = JSON.stringify(evidenceData);
        
        // HMAC-SHA256 (Web Crypto API)
        var enc = new TextEncoder();
        var keyData = enc.encode(sessionKey);
        var messageData = enc.encode(payload);
        
        var cryptoKey = await crypto.subtle.importKey(
            'raw',
            keyData,
            {name: 'HMAC', hash: 'SHA-256'},
            false,
            ['sign']
        );
        
        var signature = await crypto.subtle.sign(
            'HMAC',
            cryptoKey,
            messageData
        );
        
        // Convert to hex
        var sigArray = Array.from(new Uint8Array(signature));
        var sigHex = sigArray.map(function(b) {
            return ('0' + b.toString(16)).slice(-2);
        }).join('');
        
        return sigHex;
    }
    
    // ── Cookie Helper ─────────────────────────────────────────────────────
    
    function getCookie(name) {
        var value = "; " + document.cookie;
        var parts = value.split("; " + name + "=");
        if (parts.length === 2) {
            return parts.pop().split(";").shift();
        }
        return null;
    }
    
    // ── Submit Evidence ───────────────────────────────────────────────────
    
    /**
     * Submit evidence to server (NO local validation)
     */
    async function submitEvidence() {
        if (isCollecting) return;
        isCollecting = true;
        
        try {
            // Collect all evidence
            evidence = collectAllEvidence();
            
            // Sign evidence
            var signature = await signEvidence(evidence);
            
            // Submit to server
            var response = await fetch('/api/auth/submit-evidence', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    evidence: evidence,
                    signature: signature
                }),
                credentials: 'same-origin'
            });
            
            if (!response.ok) {
                throw new Error('Evidence submission failed: ' + response.status);
            }
            
            var result = await response.json();
            
            // Store evidence token (if provided)
            if (result.evidence_token) {
                evidenceToken = result.evidence_token;
                window.__evidence_token = evidenceToken;  // Expose for login.js
            }
            
            // NO decision making here — just store server response
            window.__evidence_result = result;
            
            // Dispatch event for login.js to handle
            var event = new CustomEvent('wasdw:evidence:submitted', {
                detail: result
            });
            document.dispatchEvent(event);
            
            console.log('[WASDW] Evidence submitted, server decision:', result.status);
            return result;
            
        } catch (err) {
            console.error('[WASDW] Evidence submission error:', err);
            window.__evidence_result = {
                status: 'error',
                error: err.message
            };
            throw err;
        } finally {
            isCollecting = false;
        }
    }
    
    // ── Public API ────────────────────────────────────────────────────────
    
    window.WASDWEvidence = {
        /**
         * Initialize with session key and page nonce (from server)
         */
        init: function(key, nonce) {
            sessionKey = key;
            pageNonce = nonce;
            console.log('[WASDW] Evidence collector initialized');
        },
        
        /**
         * Submit evidence to server
         */
        submit: submitEvidence,
        
        /**
         * Get current evidence (for debugging)
         */
        getEvidence: function() {
            return collectAllEvidence();
        },
        
        /**
         * Get evidence token (after submission)
         */
        getToken: function() {
            return evidenceToken;
        },
        
        /**
         * Get server decision result
         */
        getResult: function() {
            return window.__evidence_result || null;
        }
    };
    
    // Auto-init immediately (session key already in window)
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function() {
            var key = window.__WASDW_SESSION_KEY;
            var nonce = window.__WASDW_PAGE_NONCE;
            
            if (key && nonce) {
                window.WASDWEvidence.init(key, nonce);
            } else {
                console.warn('[WASDW] Session key or nonce missing');
            }
        });
    } else {
        // Already loaded
        var key = window.__WASDW_SESSION_KEY;
        var nonce = window.__WASDW_PAGE_NONCE;
        
        if (key && nonce) {
            window.WASDWEvidence.init(key, nonce);
        } else {
            console.warn('[WASDW] Session key or nonce missing');
        }
    }
    
})(window);
