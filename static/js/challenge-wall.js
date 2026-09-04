(function () {
    'use strict';

     
    const API_REQUEST = '/api/challenge/request';
    const API_VERIFY  = '/api/challenge/verify';
    const NEXT_URL    = window.__WALL_NEXT__ || '/';

     
    const $ = (id) => document.getElementById(id);

    const wallCard      = $('wallCard');
    const spinnerRing   = $('spinnerRing');
    const statusIcon    = $('statusIcon');
    const wallTitle     = $('wallTitle');
    const wallSubtitle  = $('wallSubtitle');
    const progressBar   = $('progressBar');
    const progressWrap  = $('progressWrapper');
    const progressLabel = $('progressLabel');
    const errorBox      = $('errorBox');
    const errorMsg      = $('errorMsg');
    const retryBtn      = $('retryBtn');
    const footerRay     = $('footerRay');
    const rayId         = $('rayId');

    const steps = [$('step1'), $('step2'), $('step3'), $('step4')];
    const lines  = Array.from(document.querySelectorAll('.step-line'));

     
    const currentRay = Array.from(crypto.getRandomValues(new Uint8Array(8)))
        .map(b => b.toString(16).padStart(2, '0')).join('').toUpperCase();
    if (rayId)        rayId.textContent = currentRay;
    if (footerRay)    footerRay.textContent = `Ray ID: ${currentRay}`;

     
    function spawnParticles() {
        const container = document.getElementById('particles');
        if (!container) return;
        const colors = ['#4299e1', '#63b3ed', '#68d391', '#9f7aea', '#4299e1'];
        for (let i = 0; i < 18; i++) {
            const p = document.createElement('div');
            p.className = 'particle';
            const size = Math.random() * 4 + 2;
            p.style.cssText = [
                `left:${Math.random() * 100}%`,
                `width:${size}px`,
                `height:${size}px`,
                `background:${colors[Math.floor(Math.random() * colors.length)]}`,
                `animation-duration:${Math.random() * 12 + 8}s`,
                `animation-delay:${Math.random() * 6}s`,
            ].join(';');
            container.appendChild(p);
        }
    }

     
    function setProgress(pct, label) {
        progressBar.style.width = `${Math.min(100, pct)}%`;
        if (progressWrap) progressWrap.setAttribute('aria-valuenow', pct);
        if (label) progressLabel.textContent = label;
    }

    function setStep(index) {
        
        steps.forEach((s, i) => {
            s.classList.remove('active', 'done');
            if (i < index)       s.classList.add('done');
            else if (i === index) s.classList.add('active');
        });
        lines.forEach((l, i) => {
            l.classList.remove('active', 'done');
            if (i < index - 1) l.classList.add('done');
            else if (i === index - 1) l.classList.add('active');
        });
    }

    function showError(msg) {
        errorMsg.textContent = msg;
        errorBox.hidden = false;
        spinnerRing.classList.add('done');
        statusIcon.textContent = '⛔';
        statusIcon.classList.add('pop');
        wallTitle.textContent = 'Doğrulama Başarısız';
        wallSubtitle.textContent = 'Bir sorun oluştu. Lütfen tekrar deneyin.';
        wallCard.classList.add('error');
        setProgress(0, '—');
    }

    function showSuccess() {
        spinnerRing.classList.add('done');
        statusIcon.textContent = '✅';
        statusIcon.classList.add('pop');
        wallTitle.textContent = 'Doğrulama Tamamlandı';
        wallSubtitle.textContent = 'Yönlendiriliyorsunuz…';
        wallCard.classList.add('success');
        wallCard.classList.remove('error');
        setProgress(100, 'Tamamlandı');
        setStep(3);

        
        const checkPath = document.querySelector('.check-path');
        if (checkPath) checkPath.classList.add('visible');
    }

     
    async function sha256hex(str) {
        const buf = new TextEncoder().encode(str);
        const digest = await crypto.subtle.digest('SHA-256', buf);
        return Array.from(new Uint8Array(digest))
            .map(b => b.toString(16).padStart(2, '0')).join('');
    }

     
    async function canvasFingerprint() {
        try {
            const c = document.createElement('canvas');
            c.width = 180; c.height = 40;
            const ctx = c.getContext('2d');
            if (!ctx) return 'no_canvas';
            ctx.font = "13px Arial";
            ctx.fillStyle = '#1a3a5c';
            ctx.fillRect(0, 0, 180, 40);
            ctx.fillStyle = '#63b3ed';
            ctx.fillText('WASDW-WALL-FP', 4, 26);
            return await sha256hex(c.toDataURL());
        } catch { return 'fp_error'; }
    }

     
    function detectWebdriver() {
        if (navigator.webdriver) return true;
        const cdcKeys = Object.keys(window).filter(k =>
            k.startsWith('cdc_') || k.startsWith('$cdc_') || k.includes('driver'));
        if (cdcKeys.length > 0) return true;
        if (window.__puppeteer_evaluation_script__ ||
            window.__nightmare ||
            window.callPhantom ||
            window._phantom) return true;
        return false;
    }

     
    function solvePoW(salt, difficulty) {
        return new Promise((resolve, reject) => {
            const workerSrc = `
                self.onmessage = async function(e) {
                    const { salt, difficulty } = e.data;
                    const prefix = '0'.repeat(difficulty);
                    let nonce = 0;
                    const enc = new TextEncoder();
                    while (true) {
                        const candidate = salt + nonce.toString();
                        const buf = enc.encode(candidate);
                        const digest = await crypto.subtle.digest('SHA-256', buf);
                        const hex = Array.from(new Uint8Array(digest))
                            .map(b => b.toString(16).padStart(2, '0')).join('');
                        if (hex.startsWith(prefix)) {
                            self.postMessage({ nonce: nonce.toString(), hash: hex });
                            break;
                        }
                        nonce++;
                        // Her 2000 denemede bir progress mesajı gönder
                        if (nonce % 2000 === 0) {
                            self.postMessage({ progress: nonce });
                        }
                    }
                };
            `;
            const blob   = new Blob([workerSrc], { type: 'application/javascript' });
            const url    = URL.createObjectURL(blob);
            const worker = new Worker(url);

            worker.onmessage = (e) => {
                if (e.data.nonce !== undefined) {
                    worker.terminate();
                    URL.revokeObjectURL(url);
                    resolve(e.data);
                } else if (e.data.progress !== undefined) {
                    
                    const approx = Math.min(85, 20 + (e.data.progress / 500));
                    setProgress(approx, `PoW çözülüyor… (${e.data.progress.toLocaleString()} deneme)`);
                }
            };

            worker.onerror = (err) => {
                worker.terminate();
                URL.revokeObjectURL(url);
                reject(new Error('Worker hatası: ' + err.message));
            };

            worker.postMessage({ salt, difficulty });
        });
    }

     
    async function runChallenge() {
        errorBox.hidden = true;
        wallCard.classList.remove('error', 'success');

        try {
            
            setStep(0);
            setProgress(5, 'Challenge alınıyor…');
            wallSubtitle.textContent = 'Güvenlik challenge\'ı alınıyor…';

            const reqRes = await fetch(API_REQUEST, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'same-origin',
                body: JSON.stringify({ ray: currentRay }),
            });

            if (!reqRes.ok) {
                const body = await reqRes.json().catch(() => ({}));
                throw new Error(body.message || body.error || `Sunucu hatası (${reqRes.status})`);
            }

            const chalData = await reqRes.json();
            const { chal_id, chal_salt, chal_diff, chal_token } = chalData;

            setProgress(15, 'PoW challenge hazır');
            setStep(1);

            
            wallSubtitle.textContent = `Proof-of-Work çözülüyor (zorluk: ${chal_diff})…`;
            const t0 = performance.now();

            const powResult = await solvePoW(chal_salt, chal_diff);

            const solveMs = Math.round(performance.now() - t0);
            setProgress(75, `PoW tamamlandı (${solveMs}ms, ${parseInt(powResult.nonce).toLocaleString()} deneme)`);

            
            setStep(2);
            wallSubtitle.textContent = 'Tarayıcı parmak izi alınıyor…';
            setProgress(82, 'Fingerprint hesaplanıyor…');

            const [fpHash] = await Promise.all([canvasFingerprint()]);
            const isBot = detectWebdriver();

            
            wallSubtitle.textContent = 'Sunucu doğrulaması yapılıyor…';
            setProgress(90, 'Sunucuya gönderiliyor…');

            const verifyRes = await fetch(API_VERIFY, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'same-origin',
                body: JSON.stringify({
                    chal_token,
                    solution_nonce: powResult.nonce,
                    canvas_hash:    fpHash,
                    webdriver:      isBot,
                    plugins_len:    navigator.plugins ? navigator.plugins.length : 0,
                    screen_w:       screen.width,
                    screen_h:       screen.height,
                    color_depth:    screen.colorDepth,
                    ray:            currentRay,
                }),
            });

            const verifyData = await verifyRes.json().catch(() => ({}));

            if (!verifyRes.ok || verifyData.status !== 'ok') {
                throw new Error(verifyData.message || verifyData.error || `Doğrulama başarısız (${verifyRes.status})`);
            }

            
            showSuccess();

            
            setTimeout(() => {
                window.location.href = verifyData.redirect || NEXT_URL;
            }, 1200);

        } catch (err) {
            showError(err.message || 'Bilinmeyen hata');
        }
    }

     
    retryBtn.addEventListener('click', () => {
        errorBox.hidden = true;
        wallCard.classList.remove('error');
        statusIcon.textContent = '🛡️';
        statusIcon.classList.remove('pop');
        spinnerRing.classList.remove('done');
        wallTitle.textContent = 'Güvenlik Doğrulaması';
        wallSubtitle.textContent = 'Bağlantınız kontrol ediliyor, lütfen bekleyin…';
        setProgress(0, 'Hazırlanıyor…');
        setStep(0);
        runChallenge();
    });

     
    spawnParticles();
    
    setTimeout(runChallenge, 350);

})();
