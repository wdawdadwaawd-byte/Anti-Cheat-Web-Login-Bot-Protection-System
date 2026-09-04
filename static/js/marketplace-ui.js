(function() {
    'use strict';

    var _wasdw_ui = {
        version: '2.8.4',
        theme: 'dark',
        initialized: false,

        init: function() {
            if (this.initialized) return;
            this.initialized = true;
            this._applyTheme();
            this._initAnimations();
            this._setupTooltips();
        },

        _applyTheme: function() {
            var saved = localStorage.getItem('wasdw_theme') || this.theme;
            document.documentElement.setAttribute('data-theme', saved);
        },

        _initAnimations: function() {
            var elements = document.querySelectorAll('.login-card, .shield-status-box, .stat-card');
            elements.forEach(function(el, i) {
                el.style.opacity = '0';
                el.style.transform = 'translateY(20px)';
                setTimeout(function() {
                    el.style.transition = 'opacity 0.5s ease, transform 0.5s ease';
                    el.style.opacity = '1';
                    el.style.transform = 'translateY(0)';
                }, 100 + (i * 80));
            });
        },

        _setupTooltips: function() {
            var tips = document.querySelectorAll('[data-tooltip]');
            tips.forEach(function(el) {
                el.addEventListener('mouseenter', function() {
                    var tip = document.createElement('div');
                    tip.className = 'wasdw-tooltip';
                    tip.textContent = el.getAttribute('data-tooltip');
                    document.body.appendChild(tip);
                    var rect = el.getBoundingClientRect();
                    tip.style.left = rect.left + 'px';
                    tip.style.top = (rect.bottom + 5) + 'px';
                });
                el.addEventListener('mouseleave', function() {
                    var tips = document.querySelectorAll('.wasdw-tooltip');
                    tips.forEach(function(t) { t.remove(); });
                });
            });
        },

        formatNumber: function(num) {
            return num.toString().replace(/\B(?=(\d{3})+(?!\d))/g, '.');
        },

        formatDate: function(ts) {
            return new Date(ts).toLocaleDateString('tr-TR');
        },

        animateValue: function(el, start, end, duration) {
            var range = end - start;
            var startTime = performance.now();
            function step(timestamp) {
                var progress = Math.min((timestamp - startTime) / duration, 1);
                var current = Math.floor(start + (range * progress));
                el.textContent = _wasdw_ui.formatNumber(current);
                if (progress < 1) requestAnimationFrame(step);
            }
            requestAnimationFrame(step);
        },

        debounce: function(fn, delay) {
            var timer;
            return function() {
                var args = arguments;
                var ctx = this;
                clearTimeout(timer);
                timer = setTimeout(function() { fn.apply(ctx, args); }, delay);
            };
        },

        escapeHtml: function(str) {
            var div = document.createElement('div');
            div.textContent = str;
            return div.innerHTML;
        }
    };

    window.WasdwUI = _wasdw_ui;

    document.addEventListener('DOMContentLoaded', function() {
        window.WasdwUI.init();
    });

})();
