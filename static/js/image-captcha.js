/**
 * WASDW Image CAPTCHA — 2 Tur, 4 Tür
 *
 * Akış:
 *   run() → TUR 1 yükle → kullanıcı seçer → Onayla →
 *   sunucu TUR 2 sorusunu döner → kullanıcı seçer → Onayla →
 *   clearance alınır → resolve(clearance)
 *
 * TYPE 1: Görsel iki eşleştirme  (2 seçim)
 * TYPE 2: Renk eşleştirme        (2 seçim, hedef renk gösterilir)
 * TYPE 3: Sayı sayma              (1 seçim)
 * TYPE 4: Mantık/Aritmetik        (1 seçim)
 */
(function (W) {
    'use strict';

    function setCookie(n, v, s) {
        var e = s > 0
            ? '; expires=' + new Date(Date.now() + s * 1000).toUTCString()
            : '; expires=Thu, 01 Jan 1970 00:00:00 GMT';
        document.cookie = n + '=' + encodeURIComponent(v) + e + '; path=/; SameSite=Strict';
    }

    function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

    // Her tür için beklenen seçim sayısı
    var EXPECTED = { 1: 2, 2: 2, 3: 1, 4: 1 };

    class ImageCaptcha {
        constructor() {
            this._ov       = document.getElementById('captchaOverlay');
            this._dialog   = document.getElementById('captchaDialog');
            this._grid     = document.getElementById('captchaItemGrid');
            this._question = document.getElementById('captchaQuestion');
            this._hint     = document.getElementById('captchaHint');
            this._confirm  = document.getElementById('captchaConfirmBtn');
            this._refresh  = document.getElementById('captchaRefreshBtn');

            // Tur gösterge noktaları
            this._dot1  = document.getElementById('cptDot1');
            this._dot2  = document.getElementById('cptDot2');
            this._pLabel = document.getElementById('captchaProgressLabel');

            // TYPE 2 hedef renk
            this._targetDiv    = document.getElementById('captchaTarget');
            this._targetSwatch = document.getElementById('cptTargetSwatch');
            this._targetName   = document.getElementById('cptTargetName');

            this._token    = null;
            this._ctype    = 1;
            this._round    = 1;
            this._selected = [];
            this._locked   = false;
            this._resolve  = null;
            this._reject   = null;

            var self = this;
            if (this._refresh) {
                this._refresh.addEventListener('click', function () {
                    if (!self._locked) self._loadFresh();
                });
            }
            if (this._confirm) {
                this._confirm.addEventListener('click', function (e) {
                    if (!e.isTrusted || self._locked) return;
                    var need = EXPECTED[self._ctype] || 2;
                    if (self._selected.length === need) self._submit();
                });
            }
        }

        run() {
            var self = this;
            return new Promise(function (res, rej) {
                self._resolve = res;
                self._reject  = rej;
                self._loadFresh();
            });
        }

        // Sıfırdan yeni challenge yükle (TUR 1)
        async _loadFresh() {
            this._token    = null;
            this._round    = 1;
            this._locked   = false;
            this._selected = [];
            this._setHint('');
            this._setConfirm(false);
            this._showTarget(null);
            this._updateProgress(1);

            this._showSpinner();
            if (this._question) this._question.textContent = 'Yükleniyor...';

            try {
                var r = await fetch('/api/captcha/challenge', { credentials: 'same-origin' });
                if (!r.ok) throw new Error('HTTP ' + r.status);
                var d = await r.json();
                this._token = d.token;
                this._applyQuestion(d);
                this._show();
            } catch (err) {
                this._setHint('⚠ Bağlantı hatası — yenile butonuna tıklayın');
                this._showSpinner(false);
            }
        }

        // Mevcut token ile TUR 2 sorusunu uygula (sunucudan geldi)
        _loadRound2(d) {
            this._round    = 2;
            this._locked   = false;
            this._selected = [];
            this._setConfirm(false);
            this._showTarget(null);
            this._updateProgress(2);
            this._applyQuestion(d);

            // Geçiş animasyonu
            if (this._grid) this._grid.classList.add('cpt-round-transition');
            setTimeout(function () {
                var g = document.getElementById('captchaItemGrid');
                if (g) g.classList.remove('cpt-round-transition');
            }, 300);
        }

        // Soru verisini DOM'a yaz ve grid'i inşa et
        _applyQuestion(d) {
            this._ctype  = d.ctype || 1;
            this._items  = d.items  || [];
            this._labels = d.labels || [];

            if (this._question) this._question.textContent = d.question || '';

            if (this._ctype === 2 && d.target) {
                this._showTarget(d.target, d.target_name);
            }

            this._buildGrid();

            var need = EXPECTED[this._ctype] || 2;
            this._setHint(need === 1
                ? 'Doğru kareyi tıklayın, ardından Onayla\'ya basın'
                : 'İki kareyi seçin, ardından Onayla\'ya tıklayın');
        }

        _buildGrid() {
            if (!this._grid) return;
            this._grid.innerHTML = '';
            var self = this;

            this._items.forEach(function (data, idx) {
                var card = document.createElement('div');
                card.className   = 'cpt-card';
                card.dataset.idx = String(idx);

                if (typeof data === 'string' && data.startsWith('EMOJI:')) {
                    // TYPE 1: EMOJI:🚗:#hex
                    var parts = data.split(':');
                    var emoji = parts[1] || '❓';
                    var bg    = parts[2] || '#1a2535';
                    card.style.background = bg;
                    card.innerHTML =
                        '<div style="font-size:50px;line-height:1;text-align:center;'
                        + 'padding-top:16px;pointer-events:none;user-select:none;">'
                        + emoji + '</div>'
                        + '<div style="font-size:10px;font-weight:700;text-align:center;'
                        + 'color:rgba(255,255,255,0.5);padding:5px 4px 8px;letter-spacing:.04em;'
                        + 'text-transform:uppercase;pointer-events:none;">'
                        + (self._labels[idx] || '') + '</div>';

                } else if (typeof data === 'string' && data.startsWith('<svg')) {
                    // TYPE 2/3/4: inline SVG
                    card.innerHTML = data;
                    var svg = card.querySelector('svg');
                    if (svg) svg.style.cssText = 'display:block;width:100%;height:auto;pointer-events:none;';

                    // TYPE 2: renk ismi altına yaz
                    if (self._ctype === 2 && self._labels[idx]) {
                        var lbl = document.createElement('div');
                        lbl.style.cssText = 'font-size:10px;font-weight:700;text-align:center;'
                            + 'color:rgba(255,255,255,0.6);padding:3px 0 6px;letter-spacing:.04em;'
                            + 'text-transform:uppercase;';
                        lbl.textContent = self._labels[idx];
                        card.appendChild(lbl);
                    }
                } else {
                    card.textContent = self._labels[idx] || String(idx);
                }

                card.addEventListener('click', function (e) {
                    if (!e.isTrusted || self._locked) return;
                    self._toggleSelect(idx, card);
                });

                self._grid.appendChild(card);
            });
        }

        _toggleSelect(idx, card) {
            var need = EXPECTED[this._ctype] || 2;
            var pos  = this._selected.indexOf(idx);

            if (pos === -1) {
                if (this._selected.length >= need) {
                    var old     = this._selected.shift();
                    var oldCard = this._grid && this._grid.querySelector('[data-idx="' + old + '"]');
                    if (oldCard) oldCard.classList.remove('cpt-selected');
                }
                this._selected.push(idx);
                card.classList.add('cpt-selected');
            } else {
                this._selected.splice(pos, 1);
                card.classList.remove('cpt-selected');
            }

            var cur = this._selected.length;
            this._setConfirm(cur === need);

            if (cur === need) {
                this._setHint('✔ Seçiminizi onaylayın');
            } else if (need === 1) {
                this._setHint('Doğru kareyi tıklayın');
            } else {
                this._setHint(cur === 1 ? '1 tane daha seçin' : 'İki kareyi seçin');
            }
        }

        async _submit() {
            var need = EXPECTED[this._ctype] || 2;
            if (this._locked || this._selected.length !== need) return;

            this._locked = true;
            this._setHint('Doğrulanıyor...');
            this._setConfirm(false);

            try {
                var vr = await fetch('/api/captcha/verify', {
                    method:  'POST',
                    headers: { 'Content-Type': 'application/json' },
                    credentials: 'same-origin',
                    body: JSON.stringify({
                        token:   this._token,
                        choices: this._selected.slice(),
                        hold_ms: 0,
                    }),
                });
                var vd = await vr.json();

                if (!vr.ok || !vd.ok) {
                    await this._handleWrong(vd);
                    return;
                }

                if (vd.done) {
                    // ── TUR 2 TAMAMLANDI — clearance al ──────────────────
                    this._selected.forEach(function (i) {
                        var c = document.querySelector('[data-idx="' + i + '"]');
                        if (c) c.style.outline = '3px solid #00b3c6';
                    });
                    this._setHint('✓ Doğrulandı! Giriş yapılıyor...');
                    if (this._question) this._question.textContent = '✓ Doğrulama tamamlandı';
                    this._updateProgress(3);   // her iki nokta yeşil
                    setCookie('_wct', vd.clearance, 180);
                    await sleep(350);
                    this._hide();
                    if (this._resolve) this._resolve(vd.clearance);

                } else if (vd.next_round === 2) {
                    // ── TUR 1 DOĞRU — TUR 2'ye geç ───────────────────────
                    this._selected.forEach(function (i) {
                        var c = document.querySelector('[data-idx="' + i + '"]');
                        if (c) { c.style.outline = '3px solid #68d391'; c.classList.remove('cpt-selected'); }
                    });
                    this._setHint('✓ Tur 1 tamam! Bir tur daha...');
                    await sleep(500);
                    this._loadRound2(vd);
                }

            } catch (_) {
                this._locked = false;
                this._setHint('Bağlantı hatası, yenileniyor...');
                await sleep(800);
                await this._loadFresh();
            }
        }

        async _handleWrong(vd) {
            var self = this;
            this._selected.forEach(function (i) {
                var c = self._grid && self._grid.querySelector('[data-idx="' + i + '"]');
                if (c) { c.classList.remove('cpt-selected'); c.classList.add('cpt-wrong-flash'); }
            });
            this._locked   = false;
            this._selected = [];
            this._setConfirm(false);

            var reason = (vd && vd.reason) || '';
            if (reason === 'token_expired') {
                this._setHint('⚠ Süre doldu, yenileniyor...');
                await sleep(600);
                await this._loadFresh();
            } else {
                var need = EXPECTED[this._ctype] || 2;
                this._setHint('❌ Yanlış seçim — tekrar dene');
                await sleep(600);
                var cards = this._grid && this._grid.querySelectorAll('.cpt-wrong-flash');
                if (cards) cards.forEach(function (c) { c.classList.remove('cpt-wrong-flash'); });

                // Yanlış cevapta sıfırdan başla (güvenlik: token sunucuda silindi)
                await this._loadFresh();
            }
        }

        // ── Yardımcı metodlar ──────────────────────────────────────────────

        _updateProgress(round) {
            // round=1: dot1 active, dot2 inactive
            // round=2: dot1 done,   dot2 active
            // round=3: dot1 done,   dot2 done (tamamlandı)
            var d1 = this._dot1, d2 = this._dot2, pl = this._pLabel;
            if (!d1 || !d2) return;
            d1.className = 'cpt-round-dot';
            d2.className = 'cpt-round-dot';
            if (round === 1) {
                d1.classList.add('active');
                if (pl) pl.textContent = 'TUR 1 / 2';
            } else if (round === 2) {
                d1.classList.add('done');
                d2.classList.add('active');
                if (pl) pl.textContent = 'TUR 2 / 2';
            } else {
                d1.classList.add('done');
                d2.classList.add('done');
                if (pl) pl.textContent = 'TAMAMLANDI';
            }
        }

        _showTarget(colorHex, colorName) {
            if (!this._targetDiv) return;
            if (!colorHex) {
                this._targetDiv.style.display = 'none';
                return;
            }
            this._targetDiv.style.display = 'flex';
            if (this._targetSwatch) this._targetSwatch.style.background = colorHex;
            if (this._targetName)   this._targetName.textContent = colorName || '';
        }

        _showSpinner(show) {
            if (!this._grid) return;
            if (show === false) {
                this._grid.innerHTML = '';
                return;
            }
            this._grid.innerHTML = '<div class="cpt-spinner"></div>';
        }

        _setConfirm(enabled) {
            if (this._confirm) this._confirm.disabled = !enabled;
        }

        _setHint(msg) {
            if (this._hint) this._hint.textContent = msg;
        }

        _show() { if (this._ov) this._ov.style.display = 'flex'; }
        _hide() { if (this._ov) this._ov.style.display = 'none'; }
    }

    W.ImageCaptcha = ImageCaptcha;
})(window);
