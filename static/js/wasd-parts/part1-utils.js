// WASD-core Part 1: Utils
// sha256, hmacSha256, hexToBytes, bytesToHex, decryptEphemeralKey
(function (W) {
    'use strict';
    var E = W.WASDEngine = W.WASDEngine || {};

    E.sha256 = async function (str) {
        const buffer = new TextEncoder().encode(str);
        const digest = await crypto.subtle.digest('SHA-256', buffer);
        return Array.from(new Uint8Array(digest)).map(b => b.toString(16).padStart(2, '0')).join('');
    };

    E.hmacSha256 = async function (keyStr, dataBytes) {
        const keyBuffer = new TextEncoder().encode(keyStr);
        const cryptoKey = await crypto.subtle.importKey(
            'raw', keyBuffer, { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']
        );
        const signature = await crypto.subtle.sign('HMAC', cryptoKey, dataBytes);
        return Array.from(new Uint8Array(signature)).map(b => b.toString(16).padStart(2, '0')).join('');
    };

    E.hexToBytes = function (hex) {
        const bytes = new Uint8Array(hex.length / 2);
        for (let i = 0; i < bytes.length; i++) {
            bytes[i] = parseInt(hex.substr(i * 2, 2), 16);
        }
        return bytes;
    };

    E.bytesToHex = function (bytes) {
        return Array.from(bytes).map(b => b.toString(16).padStart(2, '0')).join('');
    };

    E.decryptEphemeralKey = async function (encryptedHex, pageNonce) {
        const ekBytes = E.hexToBytes(encryptedHex);
        const nonceBuffer = new TextEncoder().encode(pageNonce);
        const digest = await crypto.subtle.digest('SHA-256', nonceBuffer);
        const pad = new Uint8Array(digest).slice(0, ekBytes.length);
        const decrypted = new Uint8Array(ekBytes.length);
        for (let i = 0; i < ekBytes.length; i++) {
            decrypted[i] = ekBytes[i] ^ pad[i];
        }
        return E.bytesToHex(decrypted);
    };

    W.__wasd_p1_ready = true;
})(window);
