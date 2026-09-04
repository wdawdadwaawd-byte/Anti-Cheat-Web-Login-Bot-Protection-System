(function(window, document) {
    if (!window.ShieldCore) {
        const s = document.createElement('script');
        s.src = '/static/js/WASD-core.js';
        document.head.appendChild(s);
    }
})(window, document);
