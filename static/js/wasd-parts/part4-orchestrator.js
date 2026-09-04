// WASD-core Part 4: Orchestrator
// WASDSensorEngine constructor + performSecurityHandshake + interval
// Global property yok — CustomEvent tabanlı handshake ile loader/login iletişimi
(function (W) {
    'use strict';
    var E = W.WASDEngine;

    if (!E) {
        console.error('[WASD] Engine parts not loaded');
        return;
    }

    class WASDSensorEngine {
        constructor() {
            this.startTime = performance.now();
            this.currentTicket = null;
            this.isSolving = false;
            this._handshakeDone = false;
            this._solvePromise = null;
        }
    }

    var proto = WASDSensorEngine.prototype;
    var methods = [
        'sha256', 'hmacSha256', 'hexToBytes', 'bytesToHex', 'decryptEphemeralKey',
        'getCanvasFingerprint', 'getWebGLInfo', 'getAudioFingerprint', 'detectAutomationArtifacts',
        'solvePoW', 'executeVmTransformation', 'decryptOps'
    ];
    methods.forEach(function (m) {
        if (typeof E[m] === 'function') proto[m] = E[m];
    });

    proto.performSecurityHandshake = function () {
        if (this._handshakeDone && this.currentTicket) {
            return Promise.resolve({ success: true, ticket: this.currentTicket, solveTimeMs: 0 });
        }
        if (this._solvePromise) {
            return this._solvePromise;
        }

        var self = this;
        self.isSolving = true;

        self._solvePromise = (async function () {
            try {
                const chalUrl = '/api/security/challenge' + window.location.search;
                const chalRes = await fetch(chalUrl, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    credentials: 'same-origin'
                });
                if (!chalRes.ok) {
                    const errJson = await chalRes.json().catch(() => ({}));
                    throw new Error(errJson.message || errJson.error || 'Challenge alinamadi');
                }
                const chalData = await chalRes.json();

                // Capability pattern: window._wasd_nc_XXXX() fonksiyonunu çağır
                // Yoksa fallback olarak window.__WASDW_PAGE_NONCE'u oku (geriye uyumluluk)
                let pageNonce = null;
                const capKeys = Object.keys(window).filter(k => k.startsWith('_wasd_nc_'));
                if (capKeys.length > 0) {
                    const capFn = window[capKeys[0]];
                    if (typeof capFn === 'function') {
                        pageNonce = capFn();
                    }
                }
                if (!pageNonce && window.__WASDW_PAGE_NONCE) {
                    pageNonce = window.__WASDW_PAGE_NONCE;
                }
                if (!pageNonce) throw new Error('page_nonce eksik');
                const ephemeralKey = await self.decryptEphemeralKey(chalData.chal_ek, pageNonce);

                const t0 = performance.now();
                const powResult = await self.solvePoW(chalData.chal_salt, chalData.chal_diff);
                const solveTimeMs = Math.round(performance.now() - t0);
                if (!powResult) throw new Error('PoW solve failed');

                const canvasHash = await self.getCanvasFingerprint();
                const audioHash = await self.getAudioFingerprint();
                const webgl = self.getWebGLInfo();
                const automation = self.detectAutomationArtifacts();
                const hpElem = document.getElementById(window.__WASDW_HP_ID || 'hp_trap_field');
                const hpValue = hpElem ? hpElem.value : '';
                const dwellTimeMs = Math.round(performance.now() - self.startTime);

                const telemetrySummary = canvasHash + ':' + audioHash + ':' + webgl.vendor + ':' + webgl.renderer + ':' + powResult.solution;

                // chal_ops plain geliyor — doğrudan kullan
                var decryptedOps = chalData.chal_ops;

                const sensorSignature = await self.executeVmTransformation(
                    telemetrySummary, decryptedOps, chalData.chal_salt, ephemeralKey
                );

                // ── Canvas binding imzası ─────────────────────────────────────
                // HMAC-SHA256(ephemeralKey, canvasHash + ":" + bindingNonce)
                // Sunucu bunu doğrulayarak sahte canvas hash'ini reddeder.
                var bindingSig = '';
                var bindingNonce = chalData.binding_nonce || '';
                if (bindingNonce) {
                    try {
                        var bkBuf = new TextEncoder().encode(ephemeralKey);
                        var bCryptoKey = await crypto.subtle.importKey(
                            'raw', bkBuf, { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']
                        );
                        var bData = new TextEncoder().encode(canvasHash + ':' + bindingNonce);
                        var bSig  = await crypto.subtle.sign('HMAC', bCryptoKey, bData);
                        bindingSig = Array.from(new Uint8Array(bSig)).map(b => b.toString(16).padStart(2,'0')).join('');
                    } catch (_be) { bindingSig = ''; }
                }

                // ── WASM token post-maskeleme (sunucu _wasm_token_mask ile eşleşmeli) ──
                // HMAC-SHA256(SECRET_KEY, "WASM_MASK_V1:" + hex(raw_token))[:4 bytes]
                // SECRET_KEY JS'e açık değil — maskeleme sunucu-side WASM token verify'da yapılıyor.
                // JS raw token'ı gönderir, sunucu maskeleyip karşılaştırır.
                // Yani JS tarafında ekstra adım YOK — mevcut sensorSignature formatı korunuyor.

                const verifyPayload = {
                    challenge_token: chalData.chal_payload,
                    solution_nonce: powResult.solution,
                    sensor_signature: sensorSignature,
                    telemetry_summary: telemetrySummary,
                    canvas_hash: canvasHash,
                    audio_hash: audioHash,
                    webgl_vendor: webgl.vendor,
                    webgl_renderer: webgl.renderer,
                    webdriver: !!navigator.webdriver,
                    automation_artifacts: automation,
                    plugins_len: navigator.plugins ? navigator.plugins.length : 0,
                    screen_w: window.screen.width,
                    screen_h: window.screen.height,
                    color_depth: window.screen.colorDepth,
                    dwell_time_ms: dwellTimeMs,
                    hp_field: hpValue,
                    binding_sig: bindingSig,
                };

                const verifyUrl = '/api/security/verify-challenge' + window.location.search;
                const verifyRes = await fetch(verifyUrl, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    credentials: 'same-origin',
                    body: JSON.stringify(verifyPayload)
                });

                const verifyData = await verifyRes.json();

                if (verifyRes.ok && verifyData.status === 'ok') {
                    self.currentTicket = verifyData.login_ticket;
                    self._handshakeDone = true;
                    self.isSolving = false;
                    self._solvePromise = null;

                    // ── RC4 key derivation için session_key global'e yaz ──────────
                    // session_key: sunucu PoW + VM + bot check zincirinden geçtikten
                    // Session key'i capability pattern ile set et
                    // _wasd_sc_XXXX capability fonksiyonunu oluştur (closure içinde)
                    if (verifyData.session_key && typeof window !== 'undefined') {
                        const sessionCapKeys = Object.keys(window).filter(k => k.startsWith('_wasd_sc_'));
                        const sessionCapName = sessionCapKeys.length > 0 ? sessionCapKeys[0] : '_wasd_sc_fallback';
                        
                        // Capability wrapper (one-time-use)
                        (function(){
                            let _sk = verifyData.session_key;
                            let _used = false;
                            window[sessionCapName] = function(){
                                if(_used) return null;
                                _used = true;
                                const ret = _sk;
                                _sk = null;
                                return ret;
                            };
                        })();
                        
                        // Fallback — eski kod uyumluluğu için
                        window['__WASDW_SESSION_KEY'] = verifyData.session_key;
                    }

                    return { success: true, ticket: verifyData.login_ticket, solveTimeMs: solveTimeMs };
                } else {
                    self.isSolving = false;
                    self._solvePromise = null;
                    return { success: false, reason: verifyData.message || 'Dogrulama basarisiz' };
                }
            } catch (err) {
                self.isSolving = false;
                self._solvePromise = null;
                return { success: false, reason: err.message };
            }
        })();

        return self._solvePromise;
    };

    // ── Global property YOK — CustomEvent ile dağıt ──────────────────────────
    // window['ShieldCore'] yerine event detail içinde instance geçer.
    // Yarış durumu için: window.__wasd_core_instance'a da yaz (login.js erken yüklenirse okur)
    var _instance = new WASDSensorEngine();
    window.__wasd_core_instance = _instance;  // race condition guard

    document.dispatchEvent(new CustomEvent('__WASD_EV_KEY__', {
        detail: { core: _instance },
        bubbles: false,
        cancelable: false
    }));

    // 2) Visibility refresh hook — closure üzerinden, global property olmadan
    document.addEventListener('visibilitychange', function () {
        if (document.visibilityState === 'visible' &&
            _instance._handshakeDone && _instance.currentTicket) {
            // visibility refresh hook
        }
    });

    // 3) Periyodik ticket yenileme
    setInterval(async function () {
        _instance._handshakeDone = false;
        _instance.currentTicket = null;
        _instance.isSolving = false;
        _instance._solvePromise = null;
        await _instance.performSecurityHandshake();
    }, 150000);

    W.__wasd_p4_ready = true;
    delete W.WASDEngine;
})(window);
