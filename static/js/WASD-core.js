(function (window, document) {
    'use strict';

    class WASDSensorEngine {
        constructor() {
            this.startTime = performance.now();
            this.currentTicket = null;
            this.isSolving = false;
            this._handshakeDone = false;
        }

        async sha256(str) {
            const buffer = new TextEncoder().encode(str);
            const digest = await crypto.subtle.digest('SHA-256', buffer);
            return Array.from(new Uint8Array(digest)).map(b => b.toString(16).padStart(2, '0')).join('');
        }

        async hmacSha256(keyStr, dataBytes) {
            const keyBuffer = new TextEncoder().encode(keyStr);
            const cryptoKey = await crypto.subtle.importKey(
                'raw', keyBuffer, { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']
            );
            const signature = await crypto.subtle.sign('HMAC', cryptoKey, dataBytes);
            return Array.from(new Uint8Array(signature)).map(b => b.toString(16).padStart(2, '0')).join('');
        }

        hexToBytes(hex) {
            const bytes = new Uint8Array(hex.length / 2);
            for (let i = 0; i < bytes.length; i++) {
                bytes[i] = parseInt(hex.substr(i * 2, 2), 16);
            }
            return bytes;
        }

        bytesToHex(bytes) {
            return Array.from(bytes).map(b => b.toString(16).padStart(2, '0')).join('');
        }

        async decryptEphemeralKey(encryptedHex, pageNonce) {
            const ekBytes = this.hexToBytes(encryptedHex);
            const nonceBuffer = new TextEncoder().encode(pageNonce);
            const digest = await crypto.subtle.digest('SHA-256', nonceBuffer);
            const pad = new Uint8Array(digest).slice(0, ekBytes.length);
            const decrypted = new Uint8Array(ekBytes.length);
            for (let i = 0; i < ekBytes.length; i++) {
                decrypted[i] = ekBytes[i] ^ pad[i];
            }
            return this.bytesToHex(decrypted);
        }

        async getCanvasFingerprint() {
            try {
                const canvas = document.createElement('canvas');
                canvas.width = 240;
                canvas.height = 60;
                const ctx = canvas.getContext('2d');
                if (!ctx) return 'canvas_unsupported';
                ctx.textBaseline = 'top';
                ctx.font = "14px 'Arial', sans-serif";
                ctx.fillStyle = '#f60';
                ctx.fillRect(125, 1, 62, 20);
                ctx.fillStyle = '#069';
                ctx.fillText('WASDW_Shield_\uD83D\uDD12_892', 2, 15);
                ctx.fillStyle = 'rgba(102, 204, 0, 0.7)';
                ctx.fillText('WASDW_Marketplace', 4, 35);
                return await this.sha256(canvas.toDataURL());
            } catch (e) {
                return 'canvas_error';
            }
        }

        getWebGLInfo() {
            try {
                const canvas = document.createElement('canvas');
                const gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
                if (!gl) return { vendor: 'no_webgl', renderer: 'no_webgl' };
                const debugInfo = gl.getExtension('WEBGL_debug_renderer_info');
                if (!debugInfo) return { vendor: 'generic', renderer: 'generic' };
                return {
                    vendor: gl.getParameter(debugInfo.UNMASKED_VENDOR_WEBGL) || '',
                    renderer: gl.getParameter(debugInfo.UNMASKED_RENDERER_WEBGL) || ''
                };
            } catch (e) {
                return { vendor: 'error', renderer: 'error' };
            }
        }

        async getAudioFingerprint() {
            try {
                const AudioCtx = window.OfflineAudioContext || window.webkitOfflineAudioContext;
                if (!AudioCtx) return 'audio_unsupported';
                const context = new AudioCtx(1, 8820, 44100);
                const oscillator = context.createOscillator();
                oscillator.type = 'triangle';
                oscillator.frequency.setValueAtTime(10000, context.currentTime);
                const compressor = context.createDynamicsCompressor();
                compressor.threshold.setValueAtTime(-50, context.currentTime);
                compressor.knee.setValueAtTime(40, context.currentTime);
                compressor.ratio.setValueAtTime(12, context.currentTime);
                compressor.attack.setValueAtTime(0, context.currentTime);
                compressor.release.setValueAtTime(0.25, context.currentTime);
                oscillator.connect(compressor);
                compressor.connect(context.destination);
                oscillator.start(0);
                const buffer = await context.startRendering();
                let output = 0;
                const channelData = buffer.getChannelData(0);
                for (let i = 4500; i < Math.min(5000, channelData.length); i++) {
                    output += Math.abs(channelData[i]);
                }
                return output.toString();
            } catch (e) {
                return 'audio_error';
            }
        }

        detectAutomationArtifacts() {
            const artifacts = [];
            if (navigator.webdriver) artifacts.push('navigator.webdriver=true');
            const cdcKeys = Object.keys(window).filter(k => k.startsWith('cdc_') || k.startsWith('$cdc_') || k.includes('driver'));
            if (cdcKeys.length > 0) artifacts.push('selenium_vars:' + cdcKeys.join(','));
            if (window.__puppeteer_evaluation_script__ || window.__nightmare || window.callPhantom || window._phantom) {
                artifacts.push('headless_framework_vars');
            }
            if (/Chrome/.test(navigator.userAgent) && !window.chrome) artifacts.push('missing_window.chrome');
            return artifacts;
        }

        async solvePoW(salt, difficulty) {
            const targetPrefix = '0'.repeat(difficulty);
            const encoder = new TextEncoder();
            let nonce = 0;

            while (nonce < 10000000) {
                const BATCH = 256;
                const buffers = [];
                for (let b = 0; b < BATCH; b++) {
                    buffers.push(encoder.encode(salt + (nonce + b).toString()));
                }
                const digests = await Promise.all(
                    buffers.map(buf => crypto.subtle.digest('SHA-256', buf))
                );
                for (let b = 0; b < BATCH; b++) {
                    const hash = Array.from(new Uint8Array(digests[b]))
                        .map(x => x.toString(16).padStart(2, '0')).join('');
                    if (hash.startsWith(targetPrefix)) {
                        return { solution: (nonce + b).toString(), hash };
                    }
                }
                nonce += BATCH;
                await new Promise(r => setTimeout(r, 0));
            }
            return null;
        }

        async executeVmTransformation(telemetryStr, operations, salt, hmacKey) {
            const dataBytes = new Uint8Array(new TextEncoder().encode(telemetryStr));
            for (let i = 0; i < operations.length; i++) {
                const t = operations[i].op;
                const k = operations[i].k;
                const n = dataBytes.length;
                if (t === 'XOR') {
                    for (let idx = 0; idx < n; idx++) dataBytes[idx] ^= (k + idx) % 256;
                } else if (t === 'ADD_MOD') {
                    for (let idx = 0; idx < n; idx++) dataBytes[idx] = (dataBytes[idx] + k) % 256;
                } else if (t === 'ROT') {
                    const shift = (k % 7) + 1;
                    for (let idx = 0; idx < n; idx++) {
                        const b = dataBytes[idx];
                        dataBytes[idx] = ((b << shift) | (b >> (8 - shift))) & 0xFF;
                    }
                } else if (t === 'SBOX') {
                    for (let idx = 0; idx < n; idx++) dataBytes[idx] = ((255 - dataBytes[idx]) ^ k) & 0xFF;
                } else if (t === 'SWAP_PAIRS') {
                    for (let idx = 0; idx < n - 1; idx += 2) {
                        const tmp = dataBytes[idx];
                        dataBytes[idx] = dataBytes[idx + 1];
                        dataBytes[idx + 1] = tmp;
                    }
                } else if (t === 'MUL_MOD') {
                    const mk = k | 1;
                    for (let idx = 0; idx < n; idx++) dataBytes[idx] = (dataBytes[idx] * mk) % 256;
                } else if (t === 'FOLD_XOR') {
                    for (let idx = 0; idx < Math.floor(n / 2); idx++) dataBytes[idx] ^= dataBytes[n - 1 - idx];
                } else if (t === 'CASCADE') {
                    for (let idx = 1; idx < n; idx++) dataBytes[idx] ^= dataBytes[idx - 1];
                }
            }
            return await this.hmacSha256(hmacKey + ':' + salt, dataBytes);
        }

        async performSecurityHandshake() {
            if (this._handshakeDone && this.currentTicket) {
                return { success: true, ticket: this.currentTicket, solveTimeMs: 0 };
            }
            if (this.isSolving) return { success: false, reason: 'already_solving' };
            this.isSolving = true;

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

                const pageNonce = window.__WASDW_PAGE_NONCE;
                if (!pageNonce) throw new Error('page_nonce eksik');
                const ephemeralKey = await this.decryptEphemeralKey(chalData.chal_ek, pageNonce);

                const t0 = performance.now();
                const powResult = await this.solvePoW(chalData.chal_salt, chalData.chal_diff);
                const solveTimeMs = Math.round(performance.now() - t0);
                if (!powResult) throw new Error('PoW solve failed');

                const canvasHash = await this.getCanvasFingerprint();
                const audioHash = await this.getAudioFingerprint();
                const webgl = this.getWebGLInfo();
                const automation = this.detectAutomationArtifacts();
                const hpElem = document.getElementById('hp_trap_field');
                const hpValue = hpElem ? hpElem.value : '';
                const dwellTimeMs = Math.round(performance.now() - this.startTime);

                const telemetrySummary = canvasHash + ':' + audioHash + ':' + webgl.vendor + ':' + webgl.renderer + ':' + powResult.solution;
                const sensorSignature = await this.executeVmTransformation(
                    telemetrySummary, chalData.chal_ops, chalData.chal_salt, ephemeralKey
                );

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
                    hp_field: hpValue
                };

                const verifyUrl = '/api/security/verify-challenge' + window.location.search;
                const verifyRes = await fetch(verifyUrl, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    credentials: 'same-origin',
                    body: JSON.stringify(verifyPayload)
                });

                const verifyData = await verifyRes.json();
                this.isSolving = false;

                if (verifyRes.ok && verifyData.status === 'ok') {
                    this.currentTicket = verifyData.login_ticket;
                    this._handshakeDone = true;
                    return { success: true, ticket: verifyData.login_ticket, solveTimeMs: solveTimeMs };
                } else {
                    return { success: false, reason: verifyData.message || 'Dogrulama basarisiz' };
                }
            } catch (err) {
                this.isSolving = false;
                return { success: false, reason: err.message };
            }
        }
    }

    window.ShieldCore = new WASDSensorEngine();

    document.addEventListener('visibilitychange', function() {
        if (document.visibilityState === 'visible' && window.ShieldCore._handshakeDone && window.ShieldCore.currentTicket) {
        }
    });

    setInterval(async function() {
        if (!window.ShieldCore) return;
        window.ShieldCore._handshakeDone = false;
        window.ShieldCore.currentTicket = null;
        window.ShieldCore.isSolving = false;
        await window.ShieldCore.performSecurityHandshake();
    }, 150000);

})(window, document);
