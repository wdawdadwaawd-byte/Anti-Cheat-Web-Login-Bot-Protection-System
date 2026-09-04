// Closure tabanlı handshake — window'a ShieldCore global property yazılmaz.
// part4-orchestrator __wasd_core_ready event'i dispatch eder.
// Event zaten dispatch edildiyse window.__wasd_core_instance üzerinden alınır.

(function () {
    'use strict';

    var _coreInstance = null;

    var _coreReady = new Promise(function (resolve, reject) {
        // 1) Event zaten dispatch edildiyse direkt al
        if (window.__wasd_core_instance) {
            _coreInstance = window.__wasd_core_instance;
            resolve(_coreInstance);
            return;
        }
        // 2) Henüz gelmediyse dinle
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
        var loginForm    = document.getElementById('loginForm');
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

        // ── Evidence-Based Flow: Phase 1 (Evidence Submission) ────────────
        var evidenceSubmitted = false;
        var evidenceToken = null;
        
        _coreReady
            .then(async function (core) {
                if (shieldStatus) shieldStatus.innerText = 'Donanım & Biyometri Doğrulanıyor...';
                if (shieldBadge)  shieldBadge.className  = 'shield-dot checking';
                try {
                    var res = await core.performSecurityHandshake();
                    if (res && res.success) {
                        if (shieldStatus) shieldStatus.innerText = 'Sistem Koruması Aktif (PoW: ' + res.solveTimeMs + 'ms)';
                        if (shieldBadge)  shieldBadge.className  = 'shield-dot secure';
                        
                        // ── Evidence Collection & Submission ──────────────────────
                        if (window.WASDWEvidence) {
                            try {
                                if (shieldStatus) shieldStatus.innerText = 'Kanıt Gönderiliyor...';
                                var evidenceResult = await window.WASDWEvidence.submit();
                                
                                if (evidenceResult && evidenceResult.status === 'accepted' && evidenceResult.allow_submit) {
                                    evidenceSubmitted = true;
                                    evidenceToken = evidenceResult.evidence_token;
                                    if (shieldStatus) shieldStatus.innerText = 'Sistem Koruması Aktif (PoW: ' + res.solveTimeMs + 'ms)';
                                } else {
                                    console.warn('[Login] Evidence submission failed, continuing in fallback mode');
                                    if (shieldStatus) shieldStatus.innerText = 'Sistem Koruması Aktif (PoW: ' + res.solveTimeMs + 'ms)';
                                }
                            } catch (evidenceErr) {
                                console.error('[Login] Evidence submission error:', evidenceErr);
                                console.warn('[Login] Continuing in fallback mode (evidence optional)');
                            }
                        } else {
                            console.warn('[Login] WASDWEvidence not initialized, proceeding without evidence token');
                            if (shieldStatus) shieldStatus.innerText = 'Sistem Koruması Aktif (PoW: ' + res.solveTimeMs + 'ms)';
                        }
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

        if (!loginForm) return;
        loginForm.addEventListener('submit', async function (e) {
            e.preventDefault();

            // ── isTrusted guard ──────────────────────────────────────────────
            // Otomasyon araçları form.submit() veya dispatchEvent() ile
            // sentetik event üretir — bunlarda isTrusted=false olur.
            // Gerçek kullanıcı tıklaması her zaman isTrusted=true üretir.
            // Guard geçilmeden login URL'i decode edilmez, fetch atılmaz.
            if (!e.isTrusted) {
                showAlert('Otomatik form gönderimi engellendi.', 'error');
                return;
            }

            var username = document.getElementById('username').value.trim();
            var password = document.getElementById('password').value;
            var hpTrap   = document.getElementById(window.__WASDW_HP_ID || 'hp_trap_field').value;

            if (!username || !password) { showAlert('Lütfen tüm alanları doldurun.', 'error'); return; }

            submitBtn.disabled = true;
            submitBtn.innerHTML = '<span class="spinner"></span> Güvenlik Doğrulanıyor...';
            hideAlert();

            try {
                // ── Evidence Token Check (Phase 1 preferred but optional) ─────
                // Production: evidence token zorunlu olacak
                // Şimdi: optional (fallback mode)
                if (!evidenceSubmitted || !evidenceToken) {
                    console.warn('[Login] Evidence submission failed, proceeding without token (fallback mode)');
                }
                
                var core = _coreInstance;
                if (!core) {
                    showAlert('Güvenlik modülü yüklenemedi. Sayfayı yenileyin.', 'error');
                    submitBtn.disabled = false;
                    submitBtn.innerHTML = '<span>Güvenli Giriş Yap</span>';
                    return;
                }
                var ticket = core.currentTicket;
                if (!ticket) {
                    var hsRes = await core.performSecurityHandshake();
                    if (!hsRes.success) throw new Error(hsRes.reason || 'Güvenlik doğrulaması geçilemedi.');
                    ticket = hsRes.ticket;
                }

                // ── Image CAPTCHA ────────────────────────────────────────────
                submitBtn.innerHTML = '<span class="spinner"></span> Resim Doğrulaması...';
                var captchaToken = null;
                if (window.ImageCaptcha) {
                    try {
                        var captcha = new window.ImageCaptcha();
                        captchaToken = await captcha.run();
                    } catch (_ce) {
                        showAlert('Resim doğrulaması başarısız. Sayfayı yenileyip tekrar deneyin.', 'error');
                        submitBtn.disabled = false;
                        submitBtn.innerHTML = '<span>Güvenli Giriş Yap</span>';
                        return;
                    }
                }
                if (!captchaToken) {
                    showAlert('Resim doğrulaması tamamlanmadı. Lütfen tekrar deneyin.', 'error');
                    submitBtn.disabled = false;
                    submitBtn.innerHTML = '<span>Güvenli Giriş Yap</span>';
                    return;
                }

                submitBtn.innerHTML = '<span class="spinner"></span> Güvenli Giriş Yapılıyor...';

                var loginBody = { 
                    username: username, 
                    password: password, 
                    login_ticket: ticket, 
                    hp_trap: hpTrap, 
                    captcha_token: captchaToken
                };
                
                // Add evidence token if available (Phase 2)
                if (evidenceToken) {
                    loginBody.evidence_token = evidenceToken;
                }

                // ── Lazy URL thunk ────────────────────────────────────────────
                // '/api/auth/login' URL'i isTrusted guard geçilene kadar
                // açık string olarak var olmaz — inject_pre bunu dec_fn(N)
                // çağrısına dönüştürür, sandbox isTrusted=false ile gelemez.
                var _loginEndpoint = '/api/auth/login';
                var response = await fetch(_loginEndpoint + window.location.search, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    credentials: 'same-origin',
                    body: JSON.stringify(loginBody)
                });
                var result = await response.json();
                if (response.ok) {
                    showAlert('Giriş Başarılı! Yönlendiriliyorsunuz...', 'success');
                    setTimeout(function () { window.location.href = '/dashboard'; }, 1000);
                } else {
                    showAlert('Giriş Başarısız: ' + (result.message || 'Hata oluştu') + ' (Sayfa 2 saniye içinde yenilenecektir...)', 'error');
                    core.currentTicket = null;
                    setTimeout(function () { window.location.reload(); }, 2000);
                }
            } catch (err) {
                showAlert('Hata: ' + err.message, 'error');
                if (_coreInstance) _coreInstance.currentTicket = null;
            } finally {
                submitBtn.disabled = false;
                submitBtn.innerHTML = '<span>Güvenli Giriş Yap</span>';
            }
        });
    });
})();
