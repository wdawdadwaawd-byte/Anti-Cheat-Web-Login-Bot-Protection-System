// Closure tabanlı handshake — window'a ShieldCore global property yazılmaz.
(function () {
    'use strict';

    var _coreInstance = null;

    var _coreReady = new Promise(function (resolve, reject) {
        if (window.__wasd_core_instance) {
            _coreInstance = window.__wasd_core_instance;
            resolve(_coreInstance);
            return;
        }
        document.addEventListener('__wasd_2700e14af832', function (ev) {
            if (ev && ev.detail && ev.detail.core) {
                _coreInstance = ev.detail.core;
                window.__wasd_core_instance = _coreInstance;
                resolve(_coreInstance);
            } else {
                reject(new Error('core_missing'));
            }
        }, { once: true });
        setTimeout(function () {
            if (!_coreInstance) reject(new Error('shield_timeout'));
        }, 10000);
    });

    document.addEventListener('DOMContentLoaded', function () {
        var registerForm = document.getElementById('registerForm');
        var submitBtn    = document.getElementById('submitBtn');
        var alertBox     = document.getElementById('alertBox');
        var shieldStatus = document.getElementById('shieldStatusText');
        var shieldBadge  = document.getElementById('shieldBadge');

        function showAlert(msg, type) {
            if (!alertBox) return;
            alertBox.innerText = msg;
            alertBox.className = 'alert-box ' + type + ' show';
        }
        function hideAlert() {
            if (!alertBox) return;
            alertBox.className = 'alert-box';
        }

        _coreReady
            .then(async function (core) {
                if (shieldStatus) shieldStatus.innerText = 'Donanım & Biyometri Doğrulanıyor...';
                if (shieldBadge)  shieldBadge.className  = 'shield-dot checking';
                try {
                    var res = await core.performSecurityHandshake();
                    if (res && res.success) {
                        if (shieldStatus) shieldStatus.innerText = 'Sistem Koruması Aktif (PoW: ' + res.solveTimeMs + 'ms)';
                        if (shieldBadge)  shieldBadge.className  = 'shield-dot secure';
                    } else {
                        if (shieldStatus) shieldStatus.innerText = 'Güvenlik Uyarısı: ' + ((res && res.reason) || 'Bilinmeyen hata');
                        if (shieldBadge)  shieldBadge.className  = 'shield-dot blocked';
                    }
                } catch (err) {
                    if (shieldStatus) shieldStatus.innerText = 'Hata: ' + err.message;
                    if (shieldBadge)  shieldBadge.className  = 'shield-dot blocked';
                }
            })
            .catch(function () {
                showAlert('Güvenlik modülü yüklenemedi. Sayfayı yenileyin.', 'error');
                if (submitBtn)    submitBtn.disabled    = false;
                if (shieldBadge)  shieldBadge.className = 'shield-dot blocked';
                if (shieldStatus) shieldStatus.innerText = '';
            });

        if (!registerForm) return;
        registerForm.addEventListener('submit', async function (e) {
            e.preventDefault();
            var username = document.getElementById('reg_username').value.trim();
            var email    = document.getElementById('reg_email').value.trim();
            var password = document.getElementById('reg_password').value;
            var hpTrap   = document.getElementById(window.__WASDW_HP_ID || 'hp_trap_field').value;

            if (!username || !password) { showAlert('Lütfen gerekli alanları doldurun.', 'error'); return; }

            submitBtn.disabled = true;
            submitBtn.innerHTML = '<span class="spinner"></span> Güvenli Kayıt Yapılıyor...';
            hideAlert();

            try {
                var core = _coreInstance;
                if (!core) {
                    showAlert('Güvenlik modülü yüklenemedi. Sayfayı yenileyin.', 'error');
                    submitBtn.disabled = false;
                    submitBtn.innerHTML = '<span>Hesabı Oluştur</span>';
                    return;
                }
                var ticket = core.currentTicket;
                if (!ticket) {
                    var hsRes = await core.performSecurityHandshake();
                    if (!hsRes.success) throw new Error(hsRes.reason || 'Güvenlik doğrulaması geçilemedi.');
                    ticket = hsRes.ticket;
                }
                var response = await fetch('/api/auth/register' + window.location.search, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    credentials: 'same-origin',
                    body: JSON.stringify({ username: username, email: email, password: password, login_ticket: ticket, hp_trap: hpTrap })
                });
                var result = await response.json();
                if (response.ok && result.status === 'success') {
                    showAlert('Kayıt Basarili! Giris sayfasina yonlendiriliyorsunuz...', 'success');
                    setTimeout(function () { window.location.href = '/login?registered=1'; }, 1200);
                } else {
                    showAlert('Kayit Basarisiz: ' + (result.message || 'Hata olustu') + ' (Sayfa 2 saniye icinde yenilenecektir...)', 'error');
                    core.currentTicket = null;
                    setTimeout(function () { window.location.reload(); }, 2000);
                }
            } catch (err) {
                showAlert('Hata: ' + err.message, 'error');
                if (_coreInstance) _coreInstance.currentTicket = null;
            } finally {
                submitBtn.disabled = false;
                submitBtn.innerHTML = '<span>Hesabı Oluştur</span>';
            }
        });
    });
})();
