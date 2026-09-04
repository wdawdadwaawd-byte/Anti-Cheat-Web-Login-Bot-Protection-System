(function() {
    'use strict';

    const _wasdw_analytics = {
        sessionId: null,
        events: [],
        startTime: performance.now(),

        init: function() {
            this.sessionId = this._generateId();
            this._trackPageView();
            this._setupListeners();
        },

        _generateId: function() {
            return 'xxxx-xxxx-xxxx'.replace(/x/g, function() {
                return ((Math.random() * 16) | 0).toString(16);
            });
        },

        _trackPageView: function() {
            var data = {
                type: 'pageview',
                url: window.location.pathname,
                ts: Date.now(),
                ref: document.referrer || 'direct',
                vw: window.innerWidth,
                vh: window.innerHeight
            };
            this.events.push(data);
        },

        _setupListeners: function() {
            var self = this;
            var trackedEvents = ['click', 'scroll', 'keydown', 'mousemove'];

            trackedEvents.forEach(function(evt) {
                document.addEventListener(evt, function(e) {
                    self._recordInteraction(evt, e);
                }, { passive: true });
            });
        },

        _recordInteraction: function(type, event) {
            if (this.events.length > 50) return;

            var entry = {
                type: 'interaction',
                event: type,
                ts: Date.now(),
                x: event.clientX || 0,
                y: event.clientY || 0
            };

            if (type === 'keydown') {
                entry.key = event.key.length === 1 ? '*' : event.key;
            }

            this.events.push(entry);
        },

        getMetrics: function() {
            return {
                sid: this.sessionId,
                duration: Math.round(performance.now() - this.startTime),
                eventCount: this.events.length,
                mem: performance.memory ? performance.memory.usedJSHeapSize : 0
            };
        },

        flush: function() {
            var metrics = this.getMetrics();
            this.events = [];
            return metrics;
        }
    };

    window.WasdwAnalytics = _wasdw_analytics;
    window.WasdwAnalytics.init();

})();
