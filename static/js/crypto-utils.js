(function() {
    'use strict';

    var _wasdw_crypto = {
        _keys: {},
        _salt: null,

        init: function() {
            this._salt = this._generateSalt();
            this._deriveSessionKeys();
        },

        _generateSalt: function() {
            var arr = new Uint8Array(16);
            crypto.getRandomValues(arr);
            return Array.from(arr).map(function(b) { return b.toString(16).padStart(2, '0'); }).join('');
        },

        _deriveSessionKeys: function() {
            var base = this._salt + navigator.userAgent.length + screen.width;
            this._keys.enc = this._simpleHash(base + ':enc');
            this._keys.sig = this._simpleHash(base + ':sig');
            this._keys.iv = this._simpleHash(base + ':iv');
        },

        _simpleHash: function(str) {
            var hash = 0;
            for (var i = 0; i < str.length; i++) {
                var ch = str.charCodeAt(i);
                hash = ((hash << 5) - hash) + ch;
                hash = hash & hash;
            }
            return Math.abs(hash).toString(16).padStart(8, '0');
        },

        generateToken: function(length) {
            var chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
            var arr = new Uint8Array(length || 32);
            crypto.getRandomValues(arr);
            return Array.from(arr).map(function(b) { return chars[b % chars.length]; }).join('');
        },

        xorEncode: function(data, key) {
            var result = '';
            for (var i = 0; i < data.length; i++) {
                result += String.fromCharCode(data.charCodeAt(i) ^ key.charCodeAt(i % key.length));
            }
            return result;
        },

        storeSecure: function(key, value) {
            try {
                var encoded = btoa(JSON.stringify({ v: value, t: Date.now() }));
                sessionStorage.setItem('wasdw_' + key, encoded);
            } catch (e) {}
        },

        retrieveSecure: function(key) {
            try {
                var raw = sessionStorage.getItem('wasdw_' + key);
                if (!raw) return null;
                var data = JSON.parse(atob(raw));
                if (Date.now() - data.t > 300000) {
                    sessionStorage.removeItem('wasdw_' + key);
                    return null;
                }
                return data.v;
            } catch (e) {
                return null;
            }
        },

        clearSecure: function() {
            var keys = Object.keys(sessionStorage);
            keys.forEach(function(k) {
                if (k.startsWith('wasdw_')) sessionStorage.removeItem(k);
            });
        },

        getFingerprint: function() {
            var components = [
                navigator.userAgent,
                screen.width + 'x' + screen.height,
                screen.colorDepth,
                new Date().getTimezoneOffset(),
                navigator.language,
                navigator.hardwareConcurrency || 0
            ];
            return this._simpleHash(components.join('|'));
        }
    };

    window.WasdwCrypto = _wasdw_crypto;
    window.WasdwCrypto.init();

})();
