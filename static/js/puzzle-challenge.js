/**
 * WASDW Puzzle Challenge v5 — Renk Eşleştirme
 * ─────────────────────────────────────────────
 * 4 renkli kart (üst sıra) karışık sırada sunulur.
 * 4 renkli slot (alt sıra) farklı sırada dizilidir.
 * Kullanıcı her kartı doğru renkli slota sürükler.
 * Server renk atamasını şifreli gönderir — script okuyamaz.
 */
(function (W) {
    'use strict';

    // 4 renk tanımı
    var COLORS = [
        { id: 0, name: 'Kırmızı', bg: '#e53e3e', glow: 'rgba(229,62,62,0.55)',  icon: '🔴' },
        { id: 1, name: 'Mavi',    bg: '#3182ce', glow: 'rgba(49,130,206,0.55)', icon: '🔵' },
        { id: 2, name: 'Yeşil',   bg: '#38a169', glow: 'rgba(56,161,105,0.55)', icon: '🟢' },
        { id: 3, name: 'Sarı',    bg: '#d69e2e', glow: 'rgba(214,158,46,0.55)', icon: '🟡' },
    ];

    // ── Cookie ───────────────────────────────────────────────────────────────
    function setCookie(name, value, seconds) {
        var exp = seconds > 0
            ? '; expires=' + new Date(Date.now() + seconds * 1000).toUTCString()
            : '; expires=Thu, 01 Jan 1970 00:00:00 GMT';
        document.cookie = name + '=' + encodeURIComponent(value)
            + exp + '; path=/; SameSite=Strict';
    }

    // ── HMAC-SHA256 ──────────────────────────────────────────────────────────
    async function _hmacHex(keyStr, dataStr) {
        var enc = new TextEncoder();
        var ck  = await crypto.subtle.importKey(
            'raw', enc.encode(keyStr), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']
        );
        var sig = await crypto.subtle.sign('HMAC', ck, enc.encode(dataStr));
        return Array.from(new Uint8Array(sig))
            .map(function (b) { return b.toString(16).padStart(2, '0'); }).join('');
    }

    // ── Standart sapma ───────────────────────────────────────────────────────
    function _stddev(arr) {
        if (arr.length < 2) return 0;
        var mean = arr.reduce(function (a, b) { return a + b; }, 0) / arr.length;
        var v    = arr.reduce(function (s, x) { return s + (x - mean) * (x - mean); }, 0) / arr.length;
        return Math.sqrt(v);
    }

    // ── XOR decode (server'dan gelen şifreli renk listesi) ───────────────────
    async function _decodeColorList(enc, token, label) {
        var enc2 = new TextEncoder();
        var keyData = await crypto.subtle.importKey(
            'raw', enc2.encode(token),
            { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']
        );
        var keyBuf = await crypto.subtle.sign('HMAC', keyData, enc2.encode(label));
        var xorKey = new Uint8Array(keyBuf).slice(0, 8);
        var b64    = enc.replace(/-/g, '+').replace(/_/g, '/');
        var raw    = Uint8Array.from(atob(b64), function (c) { return c.charCodeAt(0); });
        return Array.from(raw).map(function (b, i) { return (b ^ xorKey[i % 8]) & 0x0f; });
    }

    // ════════════════════════════════════════════════════════════════════════
    class PuzzleChallenge {
        constructor() {
            this._overlay = document.getElementById('puzzleOverlay');
            this._stage   = this._overlay ? this._overlay.querySelector('.puzzle-stage')   : null;
            this._dots    = this._overlay ? Array.from(this._overlay.querySelectorAll('.puzzle-dot'))   : [];
            this._box     = this._overlay ? this._overlay.querySelector('.puzzle-box')     : null;
            this._hint    = this._overlay ? this._overlay.querySelector('.puzzle-hint')    : null;

            this._token      = null;
            this._cardColors = [];   // kart pozisyon → renk id
            this._slotColors = [];   // slot pozisyon → renk id
            this._matches    = [];   // [{card, slot, holdMs, path}]
            this._done       = 0;
            this._resolve    = null;
            this._reject     = null;

            // Aktif drag durumu
            this._drag = null;   // {cardEl, cardIdx, startX, startY, downTs, moveEvents, ghost}
        }

        // ── Public API ───────────────────────────────────────────────────────
        run() {
            return new Promise(function (resolve, reject) {
                this._resolve = resolve;
                this._reject  = reject;
                this._reset();
                this._init().then(function () { this._show(); }.bind(this)).catch(reject);
            }.bind(this));
        }

        // ── Reset ────────────────────────────────────────────────────────────
        _reset() {
            this._token      = null;
            this._cardColors = [];
            this._slotColors = [];
            this._matches    = [];
            this._done       = 0;
            this._drag       = null;
        }

        // ── Init ─────────────────────────────────────────────────────────────
        async _init() {
            // 1. Challenge al
            var cr = await fetch('/api/puzzle/challenge', { credentials: 'same-origin' });
            if (!cr.ok) throw new Error('Challenge alınamadı');
            var cd = await cr.json();
            this._token = cd.token;

            // 2. Renk listelerini decode et
            this._cardColors = await _decodeColorList(cd.card_enc, this._token, 'COLOR_KEY');
            this._slotColors = await _decodeColorList(cd.slot_enc, this._token, 'COLOR_KEY');

            // 3. UI'ı oluştur
            this._buildUI();

            // 4. Progress sıfırla
            this._dots.forEach(function (d) { d.classList.remove('done'); });
            if (this._hint) this._hint.textContent = 'Her renk kartını eşleşen renkli slota sürükle';
        }

        // ── UI oluştur ───────────────────────────────────────────────────────
        _buildUI() {
            if (!this._stage) return;

            // Temizle
            this._stage.innerHTML = '';

            // Kart alanı (üst)
            var cardRow = document.createElement('div');
            cardRow.className = 'pz-card-row';

            // Slot alanı (alt)
            var slotRow = document.createElement('div');
            slotRow.className = 'pz-slot-row';

            // Kartları oluştur
            this._cardEls = [];
            for (var i = 0; i < 4; i++) {
                var c = COLORS[this._cardColors[i]];
                var card = document.createElement('div');
                card.className   = 'pz-card';
                card.dataset.idx = String(i);
                card.setAttribute('aria-label', c.name + ' kart');
                card.style.setProperty('--card-bg',   c.bg);
                card.style.setProperty('--card-glow', c.glow);

                var label = document.createElement('span');
                label.className   = 'pz-card-label';
                label.textContent = c.name;
                card.appendChild(label);

                this._bindCard(card, i);
                cardRow.appendChild(card);
                this._cardEls.push(card);
            }

            // Slotları oluştur
            this._slotEls = [];
            for (var j = 0; j < 4; j++) {
                var sc = COLORS[this._slotColors[j]];
                var slot = document.createElement('div');
                slot.className   = 'pz-slot';
                slot.dataset.idx = String(j);
                slot.setAttribute('aria-label', sc.name + ' slot');
                slot.style.setProperty('--slot-color',  sc.bg);
                slot.style.setProperty('--slot-glow',   sc.glow);

                var slotLabel = document.createElement('span');
                slotLabel.className   = 'pz-slot-label';
                slotLabel.textContent = sc.name[0];  // ilk harf
                slot.appendChild(slotLabel);

                slotRow.appendChild(slot);
                this._slotEls.push(slot);
            }

            this._stage.appendChild(cardRow);

            // Ayırıcı ok
            var sep = document.createElement('div');
            sep.className   = 'pz-separator';
            sep.textContent = '↓ Eşleştir ↓';
            this._stage.appendChild(sep);

            this._stage.appendChild(slotRow);
        }

        // ── Kart drag bind ───────────────────────────────────────────────────
        _bindCard(card, idx) {
            var self = this;
            card.addEventListener('mousedown', function (e) {
                if (!e.isTrusted) return;
                self._onDown(e, card, idx);
            }, { passive: false });
            card.addEventListener('touchstart', function (e) {
                if (!e.isTrusted) return;
                self._onDown(e, card, idx);
            }, { passive: false });
        }

        // ── Koordinat yardımcısı ─────────────────────────────────────────────
        _xy(e) {
            if (e.changedTouches && e.changedTouches[0])
                return { x: e.changedTouches[0].clientX, y: e.changedTouches[0].clientY };
            if (e.touches && e.touches[0])
                return { x: e.touches[0].clientX, y: e.touches[0].clientY };
            return { x: e.clientX, y: e.clientY };
        }

        // ── Mouse Down ───────────────────────────────────────────────────────
        _onDown(e, card, idx) {
            if (this._drag) return;                     // zaten sürükleniyor
            if (card.dataset.matched === '1') return;   // zaten eşleşti

            e.preventDefault();
            var p = this._xy(e);

            // Hayalet klon oluştur
            var rect  = card.getBoundingClientRect();
            var ghost = card.cloneNode(true);
            ghost.className = 'pz-card pz-ghost';
            ghost.style.width  = rect.width  + 'px';
            ghost.style.height = rect.height + 'px';
            ghost.style.setProperty('--card-bg',   card.style.getPropertyValue('--card-bg'));
            ghost.style.setProperty('--card-glow', card.style.getPropertyValue('--card-glow'));
            ghost.style.left = (rect.left + window.scrollX) + 'px';
            ghost.style.top  = (rect.top  + window.scrollY) + 'px';
            document.body.appendChild(ghost);

            card.style.opacity = '0.35';

            this._drag = {
                cardEl:      card,
                cardIdx:     idx,
                startX:      p.x,
                startY:      p.y,
                downTs:      Date.now(),
                moveEvents:  [],
                ghost:       ghost,
                offX:        p.x - rect.left,
                offY:        p.y - rect.top,
                untrusted:   0,
            };

            var self = this;
            this._drag._moveH = function (e2) {
                if (!e2.isTrusted) {
                    self._drag.untrusted++;
                    if (self._drag.untrusted > 3) self._cancelDrag();
                    return;
                }
                self._onMove(e2);
            };
            this._drag._upH = function (e2) {
                if (!e2.isTrusted) { self._cancelDrag(); return; }
                self._onUp(e2);
            };

            document.addEventListener('mousemove', this._drag._moveH, { passive: false });
            document.addEventListener('mouseup',   this._drag._upH);
            document.addEventListener('touchmove', this._drag._moveH, { passive: false });
            document.addEventListener('touchend',  this._drag._upH);
        }

        // ── Mouse Move ───────────────────────────────────────────────────────
        _onMove(e) {
            if (!this._drag) return;
            e.preventDefault();
            var p  = this._xy(e);
            var dx = p.x - this._drag.startX;
            var dy = p.y - this._drag.startY;

            // Ghost'u hareket ettir
            var rect = this._drag.cardEl.getBoundingClientRect();
            this._drag.ghost.style.left = (p.x - this._drag.offX + window.scrollX) + 'px';
            this._drag.ghost.style.top  = (p.y - this._drag.offY + window.scrollY) + 'px';

            // Hareket kaydet (throttle: max 200)
            if (this._drag.moveEvents.length < 200) {
                this._drag.moveEvents.push({
                    dx: Math.round(dx),
                    dy: Math.round(dy),
                    t:  Date.now() - this._drag.downTs,
                });
            }

            // Slot hover highlight
            this._slotEls.forEach(function (slot) {
                var sr = slot.getBoundingClientRect();
                var hit = p.x >= sr.left && p.x <= sr.right && p.y >= sr.top && p.y <= sr.bottom;
                slot.classList.toggle('pz-slot-hover', hit);
            });
        }

        // ── Mouse Up ────────────────────────────────────────────────────────
        async _onUp(e) {
            if (!this._drag) return;
            var drag = this._drag;
            this._drag = null;
            this._removeDragListeners(drag);

            var p = this._xy(e);

            // Ghost'u kaldır
            drag.ghost.remove();
            drag.cardEl.style.opacity = '1';

            // Slot hover temizle
            this._slotEls.forEach(function (s) { s.classList.remove('pz-slot-hover'); });

            // Hangi slotun üzerinde bırakıldı?
            var targetSlot = null;
            var targetIdx  = -1;
            this._slotEls.forEach(function (slot, si) {
                var sr = slot.getBoundingClientRect();
                if (p.x >= sr.left && p.x <= sr.right && p.y >= sr.top && p.y <= sr.bottom) {
                    targetSlot = slot;
                    targetIdx  = si;
                }
            });

            var holdMs = Date.now() - drag.downTs;
            var moves  = drag.moveEvents;

            if (targetSlot === null || holdMs < 80) {
                // Geçersiz bırakma — shake
                this._shake(drag.cardEl);
                return;
            }

            // Path özeti + HMAC
            var pathSummary = await this._summarizePath(moves);

            // Doğru renk mi?
            var cardColorId = this._cardColors[drag.cardIdx];
            var slotColorId = this._slotColors[targetIdx];
            var isCorrect   = (cardColorId === slotColorId);

            if (!isCorrect) {
                this._shake(drag.cardEl);
                if (this._hint) this._hint.textContent = '❌ Yanlış slot! Tekrar dene...';
                setTimeout(function () {
                    if (this._hint) this._hint.textContent = 'Her renk kartını eşleşen renkli slota sürükle';
                }.bind(this), 1200);
                return;
            }

            // Doğru eşleşme
            this._matches.push({
                card:   drag.cardIdx,
                slot:   targetIdx,
                holdMs: holdMs,
                path:   pathSummary,
            });

            drag.cardEl.dataset.matched = '1';
            drag.cardEl.style.opacity   = '0.3';
            drag.cardEl.style.cursor    = 'default';
            drag.cardEl.style.pointerEvents = 'none';

            targetSlot.classList.add('pz-slot-done');
            targetSlot.style.setProperty('--slot-color', COLORS[cardColorId].bg);

            // Dot
            this._done++;
            if (this._dots[this._done - 1]) this._dots[this._done - 1].classList.add('done');

            var rem = 4 - this._done;
            if (this._hint) {
                this._hint.textContent = rem > 0
                    ? '✅ +1 — ' + rem + ' kart kaldı'
                    : 'Doğrulanıyor...';
            }

            if (this._done >= 4) {
                setTimeout(function () { this._complete(); }.bind(this), 400);
            }
        }

        // ── Drag iptal ───────────────────────────────────────────────────────
        _cancelDrag() {
            if (!this._drag) return;
            var drag = this._drag;
            this._drag = null;
            this._removeDragListeners(drag);
            drag.ghost.remove();
            drag.cardEl.style.opacity = '1';
            this._slotEls.forEach(function (s) { s.classList.remove('pz-slot-hover'); });
        }

        _removeDragListeners(drag) {
            document.removeEventListener('mousemove', drag._moveH);
            document.removeEventListener('mouseup',   drag._upH);
            document.removeEventListener('touchmove', drag._moveH);
            document.removeEventListener('touchend',  drag._upH);
        }

        // ── Shake feedback ───────────────────────────────────────────────────
        _shake(el) {
            el.style.animation = 'pzShake 0.3s ease';
            el.addEventListener('animationend', function () {
                el.style.animation = '';
            }, { once: true });
        }

        // ── Tamamlama ────────────────────────────────────────────────────────
        async _complete() {
            if (this._box) {
                this._box.classList.add('success-flash');
                this._box.addEventListener('animationend',
                    function () { this._box.classList.remove('success-flash'); }.bind(this),
                    { once: true });
            }
            try {
                var vr = await fetch('/api/puzzle/verify', {
                    method:  'POST',
                    headers: { 'Content-Type': 'application/json' },
                    credentials: 'same-origin',
                    body: JSON.stringify({ token: this._token, matches: this._matches }),
                });
                var vd = await vr.json();

                if (vr.ok && vd.ok) {
                    setCookie('_wpz', this._token, 120);
                    await this._sleep(250);
                    this._hide();
                    if (this._resolve) this._resolve(this._token);
                } else {
                    setCookie('_wpz', '', 0);
                    if (this._hint) this._hint.textContent = '❌ Doğrulama başarısız. Tekrar dene...';
                    await this._sleep(700);
                    this._reset();
                    await this._init();
                }
            } catch (_) {
                await this._sleep(400);
                this._reset();
                await this._init();
            }
        }

        // ── Path özeti ───────────────────────────────────────────────────────
        async _summarizePath(moves) {
            var n = moves.length;
            var tsd = 0, dsd = 0;

            if (n > 1) {
                var ts  = moves.map(function (m) { return m.t; });
                var dts = [];
                for (var i = 1; i < ts.length; i++) dts.push(ts[i] - ts[i - 1]);
                tsd = dts.length > 1 ? Math.round(_stddev(dts)) : 0;

                var dists = moves.map(function (m) { return Math.sqrt(m.dx * m.dx + m.dy * m.dy); });
                dsd = dists.length > 1 ? Math.round(_stddev(dists)) : 0;
            }

            // HMAC imzası — MATCH:n:tsd:dsd (round() ile, server ile eşleşir)
            var raw = 'MATCH:' + n + ':' + tsd + ':' + dsd;
            var sig = await _hmacHex(this._token, raw);
            return { n: n, tsd: tsd, dsd: dsd, sig: sig.slice(0, 16) };
        }

        // ── Göster / Gizle ───────────────────────────────────────────────────
        _show() { if (this._overlay) this._overlay.classList.add('active'); }
        _hide() { if (this._overlay) this._overlay.classList.remove('active'); }
        _sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
    }

    W.PuzzleChallenge = PuzzleChallenge;

})(window);
