// WASD-core Part 2: Fingerprint
// getCanvasFingerprint, getWebGLInfo, getAudioFingerprint, detectAutomationArtifacts
(function (W) {
    'use strict';
    var E = W.WASDEngine = W.WASDEngine || {};

    E.getCanvasFingerprint = async function () {
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
            return await E.sha256(canvas.toDataURL());
        } catch (e) {
            return 'canvas_error';
        }
    };

    E.getWebGLInfo = function () {
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
    };

    E.getAudioFingerprint = async function () {
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
    };

    E.detectAutomationArtifacts = function () {
        const artifacts = [];
        if (navigator.webdriver) artifacts.push('navigator.webdriver=true');
        const cdcKeys = Object.keys(window).filter(k => k.startsWith('cdc_') || k.startsWith('$cdc_') || k.includes('driver'));
        if (cdcKeys.length > 0) artifacts.push('selenium_vars:' + cdcKeys.join(','));
        if (window.__puppeteer_evaluation_script__ || window.__nightmare || window.callPhantom || window._phantom) {
            artifacts.push('headless_framework_vars');
        }
        if (/Chrome/.test(navigator.userAgent) && !window.chrome) artifacts.push('missing_window.chrome');
        return artifacts;
    };

    W.__wasd_p2_ready = true;
})(window);
