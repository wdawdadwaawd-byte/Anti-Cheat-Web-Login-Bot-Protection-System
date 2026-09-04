// WASD-core Part 3: PoW + VM Transform
// solvePoW, executeVmTransformation
// executeVmTransformation: WASM primary path, JS fallback
(function (W) {
    'use strict';
    var E = W.WASDEngine = W.WASDEngine || {};

    // ── Op string → WASM integer ID map ─────────────────────────────────────
    // WAT sabitleri ile eşleşmeli (apply_op op_id parametresi).
    var _OP_IDS = {
        'XOR':        0,
        'ADD_MOD':    1,
        'ROT':        2,
        'SBOX':       3,
        'SWAP_PAIRS': 4,
        'MUL_MOD':    5,
        'FOLD_XOR':   6,
        'CASCADE':    7,
    };

    // ── WASM modülü (lazy init) ──────────────────────────────────────────────
    // Shared memory: ops bölgesi [0..1023], veri bölgesi [1024..]
    var _wasm = null;           // { instance, memory, view } veya null
    var _wasmLoading = false;
    var _wasmLoadPromise = null;

    var _OPS_REGION  = 0;       // ops array başlangıcı (byte offset)
    var _DATA_REGION = 1024;    // veri başlangıcı (byte offset)

    function _loadWasm() {
        if (_wasmLoadPromise) return _wasmLoadPromise;
        _wasmLoading = true;

        // WASM dosyasının URL'si: manifest'ten sc_key ile aynı mekanizma —
        // vm_transform.wasm dist/'de sabit isimle duruyor.
        var wasmUrl = '/static/js/dist/vm_transform.wasm';

        _wasmLoadPromise = fetch(wasmUrl, { credentials: 'same-origin' })
            .then(function (res) {
                if (!res.ok) throw new Error('wasm fetch ' + res.status);
                return res.arrayBuffer();
            })
            .then(function (buf) {
                var mem = new WebAssembly.Memory({ initial: 1 });
                return WebAssembly.instantiate(buf, { env: { memory: mem } })
                    .then(function (result) {
                        _wasm = {
                            instance: result.instance,
                            memory:   mem,
                            view:     new DataView(mem.buffer),
                            u8:       new Uint8Array(mem.buffer),
                            exports:  result.instance.exports,
                        };
                        return _wasm;
                    });
            })
            .catch(function (e) {
                // Sessiz fail — JS fallback devreye girer
                _wasm = null;
                _wasmLoadPromise = null;
                return null;
            });

        return _wasmLoadPromise;
    }

    // ── executeVmTransformation — WASM primary, JS fallback ─────────────────
    E.executeVmTransformation = async function (telemetryStr, operations, salt, hmacKey) {

        // WASM'ın yüklenmesini bekle — prefetch başlatılmıştı, genellikle zaten hazır
        // Hazır değilse burada tamamlanmasını bekle (max 5 saniye)
        if (!_wasm) {
            var deadline = Date.now() + 5000;
            var loaded = await _loadWasm();
            if (!loaded) {
                // 5 saniye içinde yüklenemedi — JS fallback
                return await _executeJs(telemetryStr, operations, salt, hmacKey);
            }
        }

        var wasm = _wasm;

        if (wasm && wasm.exports && typeof wasm.exports.apply_ops === 'function') {
            try {
                return await _executeWasm(wasm, telemetryStr, operations, salt, hmacKey);
            } catch (e) {
                // WASM çalışma hatası → JS fallback
                _wasm = null;
                _wasmLoadPromise = null;
            }
        }

        // JS fallback
        return await _executeJs(telemetryStr, operations, salt, hmacKey);
    };

    // ── WASM implementation ──────────────────────────────────────────────────
    async function _executeWasm(wasm, telemetryStr, operations, salt, hmacKey) {
        var enc       = new TextEncoder();
        var dataBytes = enc.encode(telemetryStr);
        var dataLen   = dataBytes.length;
        var opsCount  = operations.length;

        // Bellek yeterliliği kontrolü
        var needed = _DATA_REGION + dataLen;
        var pageBytes = 65536;
        if (needed > wasm.memory.buffer.byteLength) {
            var extraPages = Math.ceil((needed - wasm.memory.buffer.byteLength) / pageBytes);
            wasm.memory.grow(extraPages);
            wasm.view = new DataView(wasm.memory.buffer);
            wasm.u8   = new Uint8Array(wasm.memory.buffer);
        }

        // ops array'i belleğe yaz
        var v = wasm.view;
        for (var i = 0; i < opsCount; i++) {
            var opStr = operations[i].op;
            var opId  = (_OP_IDS[opStr] !== undefined) ? _OP_IDS[opStr] : -1;
            if (opId < 0) continue;
            var k = (operations[i].k | 0) & 0xFFFFFFFF;
            v.setInt32(_OPS_REGION + i * 8,     opId, true);
            v.setInt32(_OPS_REGION + i * 8 + 4, k,    true);
        }

        // Veriyi belleğe yaz, WASM'ı çalıştır
        wasm.u8.set(dataBytes, _DATA_REGION);
        wasm.exports.apply_ops(_OPS_REGION, opsCount, _DATA_REGION, dataLen);

        // Sonucu oku
        var transformed = new Uint8Array(
            new Uint8Array(wasm.memory.buffer, _DATA_REGION, dataLen)
        );

        // HMAC-SHA256
        var secretSaltKey = hmacKey + ':' + salt;
        var hmacResult = await E.hmacSha256(secretSaltKey, transformed);

        // ── WASM path imzası: get_token(seed) ────────────────────────────────
        // seed = Date.now() & 0xFFFFFFFF — sunucuya da gönderilir,
        // sunucu aynı get_token algoritmasını Python'da uygulayarak doğrular.
        // Fallback path bu tokeni üretemez → sunucu reddeder.
        var seed = (Date.now() / 1000 | 0) & 0xFFFFFFFF;  // unix ts truncated
        var wasmToken = wasm.exports.get_token(seed) >>> 0; // unsigned i32

        // Format: <hmac>:<token_hex>:<seed_hex>
        return hmacResult + ':' + wasmToken.toString(16) + ':' + seed.toString(16);
    }

    // ── JS fallback implementation (orijinal) ───────────────────────────────
    async function _executeJs(telemetryStr, operations, salt, hmacKey) {
        var dataBytes = new Uint8Array(new TextEncoder().encode(telemetryStr));
        for (var i = 0; i < operations.length; i++) {
            var t = operations[i].op;
            var k = operations[i].k;
            var n = dataBytes.length;
            if (t === 'XOR') {
                for (var idx = 0; idx < n; idx++) dataBytes[idx] ^= (k + idx) % 256;
            } else if (t === 'ADD_MOD') {
                for (var idx = 0; idx < n; idx++) dataBytes[idx] = (dataBytes[idx] + k) % 256;
            } else if (t === 'ROT') {
                var shift = (k % 7) + 1;
                for (var idx = 0; idx < n; idx++) {
                    var b = dataBytes[idx];
                    dataBytes[idx] = ((b << shift) | (b >> (8 - shift))) & 0xFF;
                }
            } else if (t === 'SBOX') {
                for (var idx = 0; idx < n; idx++) dataBytes[idx] = ((255 - dataBytes[idx]) ^ k) & 0xFF;
            } else if (t === 'SWAP_PAIRS') {
                for (var idx = 0; idx < n - 1; idx += 2) {
                    var tmp = dataBytes[idx];
                    dataBytes[idx] = dataBytes[idx + 1];
                    dataBytes[idx + 1] = tmp;
                }
            } else if (t === 'MUL_MOD') {
                var mk = k | 1;
                for (var idx = 0; idx < n; idx++) dataBytes[idx] = (dataBytes[idx] * mk) % 256;
            } else if (t === 'FOLD_XOR') {
                for (var idx = 0; idx < Math.floor(n / 2); idx++) dataBytes[idx] ^= dataBytes[n - 1 - idx];
            } else if (t === 'CASCADE') {
                for (var idx = 1; idx < n; idx++) dataBytes[idx] ^= dataBytes[idx - 1];
            }
        }
        var secretSaltKey = hmacKey + ':' + salt;
        return await E.hmacSha256(secretSaltKey, dataBytes);
    }

    // ── chal_ops decrypt ────────────────────────────────────────────────────
    // Sunucu ops'ları ephemeral_key ile XOR+base64 şifreli gönderiyor.
    // Sadece ephemeralKey'e sahip JS (gerçek WASM path) ops'ları açabilir.
    E.decryptOps = function (encryptedOpsB64, ephemeralKeyHex) {
        try {
            var enc     = atob(encryptedOpsB64);
            var keyBytes = E.hexToBytes(ephemeralKeyHex);
            var out     = new Uint8Array(enc.length);
            for (var i = 0; i < enc.length; i++) {
                out[i] = enc.charCodeAt(i) ^ keyBytes[i % keyBytes.length];
            }
            var td = new TextDecoder();
            return JSON.parse(td.decode(out));
        } catch (_e) {
            return [];
        }
    };

    // ── score: env-check sinyallerini WASM'a iletir, eşik kararını WASM alır ──
    // JS tarafında sadece boolean'lar toplanır; ağırlıklar ve eşik değeri
    // WASM binary'sinde kalır — webcrack/Babel tabanlı araçlar göremez.
    //
    // Kullanım (build_env_check üretiminde obfuscate_js.py tarafından inject edilir):
    //   if (W[decKey] && W[decKey].score) {
    //     poisonFlag = W[decKey].score(env,wdrv,attr,ua,pw,cdp,webgl,plug,perm,lang,dim,outer,THRESHOLD) === 1;
    //   }
    //
    // THRESHOLD: build-time random (obfuscate_js.py'nin _num_expr ile ürettiği
    // obfuscated integer) — JS'de okunabilir sabit olarak kalmaz.
    E.score = function (env, wdrv, attr, ua, pw, cdp, webgl, plug, perm, lang, dim, outer, threshold) {
        if (_wasm && _wasm.exports && typeof _wasm.exports.score === 'function') {
            try {
                return _wasm.exports.score(
                    env  ? 1 : 0, wdrv  ? 1 : 0, attr  ? 1 : 0, ua    ? 1 : 0,
                    pw   ? 1 : 0, cdp   ? 1 : 0, webgl ? 1 : 0, plug  ? 1 : 0,
                    perm ? 1 : 0, lang  ? 1 : 0, dim   ? 1 : 0, outer ? 1 : 0,
                    threshold | 0
                );
            } catch (_e) {
                // WASM hatası — JS fallback (eşik JS'de görünmek zorunda kalır ama
                // bu yol sadece WASM tamamen bozulduğunda devreye girer)
            }
        }
        // JS fallback: ağırlıklar burada — ancak WASM hazır değilse çalışır
        var s = 0;
        if (env)   s += 50;
        if (wdrv)  s += 40;
        if (attr)  s += 35;
        if (ua)    s += 30;
        if (pw)    s += 30;
        if (cdp)   s += 35;
        if (webgl) s += 20;
        if (plug)  s += 10;
        if (perm)  s += 20;
        if (lang)  s += 10;
        if (dim)   s += 15;
        if (outer) s += 15;
        return (s >= threshold) ? 1 : 0;
    };
    E.solvePoW = async function (salt, difficulty) {
        var targetPrefix = '0'.repeat(difficulty);
        var encoder = new TextEncoder();
        var nonce = 0;

        while (nonce < 10000000) {
            var BATCH = 256;
            var buffers = [];
            for (var b = 0; b < BATCH; b++) {
                buffers.push(encoder.encode(salt + (nonce + b).toString()));
            }
            var digests = await Promise.all(
                buffers.map(function (buf) { return crypto.subtle.digest('SHA-256', buf); })
            );
            for (var b = 0; b < BATCH; b++) {
                var hash = Array.from(new Uint8Array(digests[b]))
                    .map(function (x) { return x.toString(16).padStart(2, '0'); }).join('');
                if (hash.startsWith(targetPrefix)) {
                    return { solution: (nonce + b).toString(), hash: hash };
                }
            }
            nonce += BATCH;
            await new Promise(function (r) { setTimeout(r, 0); });
        }
        return null;
    };

    // WASM'ı sayfa yüklenirken arka planda başlat (ilk executeVmTransformation çağrısını hızlandırır)
    if (typeof fetch !== 'undefined' && typeof WebAssembly !== 'undefined') {
        _loadWasm();
    }

    W.__wasd_p3_ready = true;
})(window);
