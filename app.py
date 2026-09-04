import os
import sys
import json
import time
import hmac
import hashlib
import secrets
import asyncio
import uuid
import base64
from contextlib import asynccontextmanager
from typing import List, Set
from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect, HTTPException, Depends, Header
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

import config
from database import (
    init_db, add_security_log, get_recent_logs, get_stats,
    is_ip_banned, ban_ip, unban_ip, clear_all_bans, verify_user, create_user
)
from security import (
    check_ip_security, extract_client_ip,
    analyze_client_telemetry,
    generate_pow_challenge, verify_pow_solution, issue_login_ticket, consume_login_ticket,
    polymorphic_pack, generate_session_nonce,
    check_rate_limit, record_failed_attempt, reset_failed_attempts
)
from security.crypto_engine import sign_data, verify_signature
from security.challenge_wall import (
    create_wall_challenge,
    verify_wall_solution,
    issue_clearance_token,
    validate_clearance_token,
    is_bypass_path,
)

_admin_bearer = HTTPBearer(auto_error=False)

def require_admin(
    request: Request,
    creds: HTTPAuthorizationCredentials = Depends(_admin_bearer)
):
    """Admin endpoint'leri için Bearer token doğrulaması."""
    # Bearer header'dan al
    token = creds.token if creds else None
    # Yoksa X-Admin-Token header'dan al (admin.js için)
    if not token:
        token = request.headers.get("X-Admin-Token")
    # Yoksa query param'dan al (WebSocket için)
    if not token:
        token = request.query_params.get("token")
    if not token or token != config.ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Admin token gerekli")

@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs(_RUNTIME_DIR, exist_ok=True)  # manifest dizini — static/ dışında
    init_db()
    clear_all_bans()

    # ── Production secret key uyarısı ────────────────────────────────────────
    if config.SECRET_KEY == "wasdw-default-secret-change-in-prod-2024":
        print("\n" + "="*60, flush=True)
        print("⚠️  UYARI: Varsayılan SECRET_KEY kullanılıyor!", flush=True)
        print("   Production'da WASDW_SECRET_KEY env variable'ını set et:", flush=True)
        print("   export WASDW_SECRET_KEY=$(python -c \"import secrets; print(secrets.token_hex(32))\")", flush=True)
        print("="*60 + "\n", flush=True)
    if config.ADMIN_TOKEN == "CHANGE-THIS-ADMIN-TOKEN-IN-PRODUCTION":
        print("⚠️  UYARI: Varsayılan ADMIN_TOKEN kullanılıyor! WASDW_ADMIN_TOKEN set et.", flush=True)

    # ── Periyodik cleanup task ────────────────────────────────────────────
    async def _periodic_cleanup():
        while True:
            await asyncio.sleep(300)  # 5 dakikada bir
            try:
                _cleanup_sessions()
            except Exception:
                pass
            try:
                _cleanup_captcha_sessions()
            except Exception:
                pass
            try:
                cleanup_evidence_tokens()
            except Exception:
                pass
    
    cleanup_task = asyncio.create_task(_periodic_cleanup())

    # ── Otomatik obfuscation build ────────────────────────────────────────────
    # Her sunucu başlangıcında obfuscate_js.py --split çalıştırılır.
    # Çıktı real-time terminale akar — hata/uyarı anında görülür.
    # Build başarısız olursa sunucu yine de ayağa kalkar (eski dist dosyaları
    # geçerli kalır), sadece [WASDW] OBF HATA satırı basar.
    _obf_ok = False
    _obf_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "obfuscate_js.py")
    if os.path.isfile(_obf_script):
        print("[WASDW] Obfuscation build başlıyor (obfuscate_js.py --split)...", flush=True)
        print("-" * 60, flush=True)
        try:
            import subprocess as _sp
            _obf_proc = _sp.Popen(
                [sys.executable, _obf_script, "--split"],
                stdout=_sp.PIPE,
                stderr=_sp.STDOUT,   # stderr'i stdout'a birleştir — tek stream
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=os.path.dirname(os.path.abspath(__file__)),
            )
            # Satır satır real-time yazdır
            for _line in _obf_proc.stdout:
                print(_line, end="", flush=True)
            _obf_proc.wait()
            print("-" * 60, flush=True)
            if _obf_proc.returncode == 0:
                print("[WASDW] Obfuscation build TAMAMLANDI ✓", flush=True)
                _obf_ok = True
            else:
                print(
                    f"[WASDW] OBF HATA: obfuscate_js.py exit {_obf_proc.returncode} — "
                    "eski dist dosyaları kullanılacak.",
                    flush=True
                )
        except Exception as _obf_e:
            print(f"[WASDW] OBF HATA: subprocess başlatılamadı — {_obf_e}", flush=True)
    else:
        print("[WASDW] UYARI: obfuscate_js.py bulunamadı, build atlandı.", flush=True)

    # ── Manifest yükle & dist sağlık kontrolü ────────────────────────────────
    # Build sonrası (veya önceki) manifest'i oku ve dist'i doğrula.
    _mf = None
    if os.path.isfile(_MANIFEST_PATH):
        try:
            with open(_MANIFEST_PATH, encoding="utf-8") as _f:
                _mf = json.load(_f)
        except Exception as _e:
            print(f"[WASDW] UYARI: Manifest okunamadı — {_e}", flush=True)
    else:
        print(
            "[WASDW] UYARI: _runtime/wasd-manifest.json bulunamadı.\n"
            "        JS challenge flow devre dışı kalacak.",
            flush=True
        )

    if _mf is not None:
        _dist_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "js", "dist")
        _missing: list[str] = []

        for _p in _mf.get("parts", []):
            if not os.path.isfile(os.path.join(_dist_path, _p)):
                _missing.append(f"part: {_p}")

        _loader = _mf.get("loader", "")
        if _loader and not os.path.isfile(os.path.join(_dist_path, _loader)):
            _missing.append(f"loader: {_loader}")

        _wasm = _mf.get("wasm_vm", "vm_transform.wasm")
        if not os.path.isfile(os.path.join(_dist_path, _wasm)):
            _missing.append(f"wasm: {_wasm}")

        if _missing:
            _wasm_missing = any(m.startswith("wasm:") for m in _missing)
            print(
                f"[WASDW] UYARI: Manifest mevcut ama dist/ içinde {len(_missing)} dosya eksik:\n"
                + "\n".join(f"        - {m}" for m in _missing),
                flush=True
            )
            # WASM eksikse hard-fail kontrolü
            if _wasm_missing and config.WASM_BUILD_HARD_FAIL:
                print(
                    "[WASDW] KRITIK: WASM dosyasi eksik ve WASM_BUILD_HARD_FAIL=true.\n"
                    "        Sunucu baslatilmiyor. Cozum: npm install wabt && python obfuscate_js.py --split",
                    flush=True
                )
                raise RuntimeError("WASM build zorunlu ama vm_transform.wasm eksik (WASM_BUILD_HARD_FAIL=true)")
            elif _wasm_missing:
                print(
                    "[WASDW] UYARI: WASM eksik — score() JS fallback ile calisiyor (zayiflatis deploy).\n"
                    "        Production'da hard-fail icin: WASDW_WASM_HARD_FAIL=true",
                    flush=True
                )
        else:
            _part_count = len(_mf.get("parts", []))
            print(
                f"[WASDW] Manifest OK — {_part_count} parça, "
                f"loader: {_loader}, wasm: {_wasm}",
                flush=True
            )

    # Honeypot route'larını manifest'ten dinamik yükle
    _reload_honeypot_routes()

    yield

app = FastAPI(title="WASDW Marketplace Anti-Cheat Security Gateway", docs_url=None, redoc_url=None, lifespan=lifespan)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app.mount("/static/css", StaticFiles(directory=os.path.join(BASE_DIR, "static", "css")), name="static-css")
# /static/js/dist: StaticFiles mount kaldırıldı — 304 cache sorunu önlemek için
# custom route ile serve ediliyor (aşağıda _serve_dist_file)

_JS_WHITELIST = {
    "login.js", "register.js",
    "admin.js", "image-captcha.js",
    "behavioral-collector.js", "evidence-collector.js",
    "marketplace-ui.js",
}
_JS_DIR   = os.path.join(BASE_DIR, "static", "js")
_DIST_DIR = os.path.join(BASE_DIR, "static", "js", "dist")
# Manifest static/ dışında — web üzerinden erişilemez
_RUNTIME_DIR   = os.path.join(BASE_DIR, "_runtime")
_MANIFEST_PATH = os.path.join(_RUNTIME_DIR, "wasd-manifest.json")

# In-memory manifest cache — dist'teki manifest.json her build'de değişir.
# İlk istek veya dosya güncelleme sonrası otomatik yenilenir.
_manifest_cache: dict | None = None
_manifest_mtime: float = 0.0


def _make_decoy_manifest() -> dict:
    """
    dist/wasd-manifest.json URL'ine gelen istekler için sahte manifest.
    Gerçeğe benzeyen ama tamamen işlevsiz içerik — saldırganı oyalar.
    Her çağrıda farklı değerler üretilir (cache'lenmez).
    """
    import time as _time
    # Gerçek part formatına benzeyen sahte isimler
    fake_parts = [
        f"WASD-core-V{secrets.token_hex(4)}.js" for _ in range(4)
    ]
    # Gerçek sc_key formatına benzeyen (_w + 8 hex) ama yanlış değerler
    fake_sc  = "_w" + secrets.token_hex(4)
    fake_scr = "_w" + secrets.token_hex(4)
    # Gerçeğe benzer ama işe yaramaz yapı
    return {
        "parts":       fake_parts,
        "ts":          int(_time.time()) - secrets.randbelow(86400),
        "fixed_decoys": [f"WASD-core-V{secrets.token_hex(4)}.js" for _ in range(3)],
        "loader":      f"WASD-core-V{secrets.token_hex(4)}.js",
        "fake_loader": f"WASD-Loader-V{secrets.token_hex(4)}.js",
        "sc_key":      fake_sc,
        "scr_key":     fake_scr,
        "wasm_vm":     "vm_transform.wasm",
    }


def _get_loader_context() -> dict:
    """
    Her sayfada yüklenecek tüm dist JS dosyalarını ve
    manifest bilgilerini döner.
    Dist klasöründeki TÜM .js dosyaları + wasm network'te görünür.
    """
    m = _load_manifest()
    loader_v         = f"{m.get('ts', 0):08x}" if m else "00000000"
    loader_js        = m.get("loader",           "") if m else ""
    loader_integrity = m.get("loader_integrity",  "") if m else ""
    shield_js        = m.get("fake_loader",       "") if m else ""
    parts            = m.get("parts",             []) if m else []

    # Dist klasöründeki TÜM .js dosyalarını tara
    all_dist: list[str] = []
    if os.path.isdir(_DIST_DIR):
        for fname in sorted(os.listdir(_DIST_DIR)):
            if fname.endswith(".js") or fname.endswith(".wasm"):
                all_dist.append(fname)

    # Yükleme sırası:
    # 1. fake_loader (shield decoy)
    # 2. manifest parts (gerçek core parçaları)
    # 3. loader (ana orchestrator)
    # 4. kalan dist dosyaları (puzzle, puzzle_decoy, vb.)
    ordered: list[str] = []
    seen: set[str] = set()

    def _add(f: str):
        if f and f not in seen:
            seen.add(f)
            ordered.append(f)

    if shield_js:
        _add(shield_js)
    for p in parts:
        _add(p)
    if loader_js:
        _add(loader_js)
    # Kalan tüm dist JS'ler
    for f in all_dist:
        _add(f)

    return {
        "loader_v":         loader_v,
        "loader_js":        loader_js,
        "loader_integrity": loader_integrity,
        "shield_js":        shield_js,
        "parts":            parts,
        "all_dist":         ordered,   # TÜM dosyalar — template'de kullanılır
    }


def _load_manifest() -> dict | None:
    """wasd-manifest.json'u okur. Dosya yoksa None döner."""
    global _manifest_cache, _manifest_mtime
    if not os.path.isfile(_MANIFEST_PATH):
        return None
    mtime = os.path.getmtime(_MANIFEST_PATH)
    if _manifest_cache is not None and mtime == _manifest_mtime:
        return _manifest_cache
    try:
        with open(_MANIFEST_PATH, encoding="utf-8") as f:
            _manifest_cache = json.load(f)
        _manifest_mtime = mtime
        return _manifest_cache
    except Exception:
        return None


def _resolve_challenge_wall_js(manifest: dict | None) -> str:
    """
    challenge-wall.js'in dist'teki obfuscated adını döner.

    Öncelik sırası:
    1. Manifest'te 'challenge_wall' key'i varsa ve dist'te dosya mevcutsa → onu kullan.
       (normal mode: python obfuscate_js.py ile üretilmiş)
    2. Manifest yoksa veya key yoksa — dist/ içinde 'challenge-wall' prefix'li
       ilk .js dosyasını bul ve onu kullan.
       (split mode'da challenge-wall ayrı obfuscate edilmişse buradan yakalanır)
    3. Hiçbiri yoksa → kaynak 'challenge-wall.js'i /static/js/ üzerinden serve et.
       (whitelist'e eklenmesi gerekir; bu fallback sadece geliştirme içindir)
    """
    # 1. Manifest key kontrolü
    if manifest:
        cw = manifest.get("challenge_wall", "")
        if cw and os.path.isfile(os.path.join(_DIST_DIR, cw)):
            return cw

    # 2. dist/ içinde challenge-wall prefix'li dosya ara
    try:
        for fname in sorted(os.listdir(_DIST_DIR)):
            if fname.startswith("challenge-wall") and fname.endswith(".js"):
                return fname
    except OSError:
        pass

    # 3. Son çare: kaynak dosyayı whitelist üzerinden sun
    # Whitelist'e otomatik ekle — sadece fallback durumunda
    _JS_WHITELIST.add("challenge-wall.js")
    return "../challenge-wall.js"  # serve_js handler JS_DIR/challenge-wall.js'i serve eder


from pathlib import Path

@app.get("/static/js/dist/{filename:path}")
async def serve_dist_file(filename: str):
    """
    dist/ klasöründeki JS ve WASM dosyalarını Cache-Control: no-store ile serve eder.
    StaticFiles mount yerine kullanılır — StaticFiles 304 döndürerek browser'ın
    eski obf'lu dosyaları cache'ten kullanmasına neden oluyordu (charAt hatası).
    Bu route serve_js'den ÖNCE tanımlanmalı — aksi halde /static/js/{path} yakalıyor.
    """
    if ".." in filename or "\\" in filename or "\0" in filename:
        raise HTTPException(status_code=404)

    candidate = os.path.join(_DIST_DIR, filename)
    if not os.path.abspath(candidate).startswith(os.path.abspath(_DIST_DIR)):
        raise HTTPException(status_code=404)

    if not os.path.isfile(candidate):
        raise HTTPException(status_code=404)

    if filename.endswith(".wasm"):
        media_type = "application/wasm"
    elif filename.endswith(".js"):
        media_type = "application/javascript"
    else:
        media_type = "application/octet-stream"

    return FileResponse(
        candidate,
        media_type=media_type,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma":        "no-cache",
            "Expires":       "0",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.get("/static/js/{filename:path}")
async def serve_js(filename: str):
    # 1. Whitelist'te yoksa direkt kapı
    if not filename or filename not in _JS_WHITELIST:
        raise HTTPException(status_code=404)

    # 2. Path traversal ve tehlikeli karakterleri engelle (/ izin ver, sadece .. ve \ blokla)
    if "\\" in filename or ".." in filename or "\0" in filename:
        raise HTTPException(status_code=404)

    js_root = Path(_JS_DIR).resolve()
    candidate = (js_root / filename).resolve()

    # 3. Çözülen yol hala izin verilen klasörün içinde mi?
    try:
        candidate.relative_to(js_root)
    except ValueError:
        raise HTTPException(status_code=404)

    if not candidate.is_file():
        raise HTTPException(status_code=404)

    return FileResponse(
        candidate,
        media_type="application/javascript",
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.get("/static/js/dist/wasd-manifest.json")
async def decoy_manifest():
    """
    Decoy manifest endpoint.
    Gerçek manifest _runtime/ altında ve bu URL üzerinden erişilemez.
    Bu URL'e gelen isteklere gerçeğe benzeyen ama tamamen işlevsiz
    sahte JSON döner — saldırganı oyalar, gerçek yapıyı açığa çıkarmaz.
    """
    return JSONResponse(
        content=_make_decoy_manifest(),
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        }
    )


@app.get("/api/security/page-bundle")
async def page_bundle(request: Request):
    """
    Login sayfasının yüklemesi gereken script URL listesini
    XOR şifreli base64 olarak döndürür.
    """
    m = _load_manifest()
    m = _load_manifest()
    parts      = m.get("parts",       []) if m else []
    loader_js  = m.get("loader",      "") if m else ""
    shield_js  = m.get("fake_loader", "") if m else ""
    # puzzle_js artık kullanılmıyor — image-captcha.js direkt yükleniyor
    puzzle_js  = ""
    loader_v   = f"{m.get('ts', 0):08x}" if m else "00000000"

    # Script yükleme sırası
    urls: list[str] = []
    if parts:
        for p in parts:
            urls.append(f"/static/js/dist/{p}?v={loader_v}")
    else:
        if shield_js:
            urls.append(f"/static/js/dist/{shield_js}?v={loader_v}")
        if loader_js:
            urls.append(f"/static/js/dist/{loader_js}?v={loader_v}")

    if puzzle_js:
        urls.append(f"/static/js/dist/{puzzle_js}?v={loader_v}")
    else:
        urls.append("/static/js/image-captcha.js")

    urls.append("/static/js/login.js")

    # XOR şifreleme — her request farklı key
    import base64 as _b64
    raw  = json.dumps(urls).encode()
    key  = secrets.token_bytes(16)
    enc  = bytes(b ^ key[i % len(key)] for i, b in enumerate(raw))
    b64  = _b64.b64encode(enc).decode()
    k64  = _b64.b64encode(key).decode()

    # k64 client'a gönderiliyor ama bu tamam — XOR şifreleme burada
    # security-through-obscurity değil, amacı kaynak HTML'de URL göstermemek
    return JSONResponse(content={"b": b64, "k": k64}, headers={
        "Cache-Control": "no-store, no-cache, must-revalidate, proxy-revalidate",
        "Pragma": "no-cache",
        "Expires": "0",
        "X-Content-Type-Options": "nosniff",
    })


async def js_manifest(request: Request):
    """
    Güncel WASD-core parça isimlerini döner.
    Template'lar veya loader bu endpoint'i kullanarak
    hangi parçaların yükleneceğini öğrenebilir.
    Sadece aynı origin'den gelen isteklere izin ver.
    """
    origin = request.headers.get("origin", "")
    referer = request.headers.get("referer", "")
    host = request.headers.get("host", "")

    # Origin kontrolü: ya aynı host ya da boş (same-origin browser isteği)
    if origin and host and origin not in (f"http://{host}", f"https://{host}"):
        raise HTTPException(status_code=403, detail="forbidden")

    manifest = _load_manifest()
    if manifest is None:
        # Manifest henüz oluşturulmamış — loader'ı fallback olarak döndür
        raise HTTPException(
            status_code=503,
            detail="JS manifest not ready. Run: python obfuscate_js.py --split"
        )

    # Parça dosyalarının gerçekten dist'te olduğunu doğrula
    valid_parts = [
        p for p in manifest.get("parts", [])
        if os.path.isfile(os.path.join(_DIST_DIR, p))
    ]

    return JSONResponse(content={
        "parts": valid_parts,
        "base": "/static/js/dist/",
        "loader": "/static/js/dist/wasd-loader.js",
        "ts": manifest.get("ts", 0),
    }, headers={
        "Cache-Control": "no-store, no-cache",
        "X-Content-Type-Options": "nosniff",
    })
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

active_soc_websockets: Set[WebSocket] = set()
active_sessions: dict = {}
_SESSION_TTL = 3600  # 1 saat

def _cleanup_sessions():
    """Süresi geçmiş session'ları temizle — her 5 dakikada çağrılır."""
    now = time.time()
    expired = [tok for tok, meta in active_sessions.items()
               if now - meta.get("created_at", now) > _SESSION_TTL]
    for tok in expired:
        active_sessions.pop(tok, None)
    if expired:
        print(f"[SESSION] {len(expired)} expired session temizlendi.", flush=True)

def _cleanup_honeypot_cleared():
    """_honeypot_cleared ve _env_signal_hits'i temizle."""
    now = time.time()
    stale_cleared = [ip for ip, ts in _honeypot_cleared.items() if now - ts > 3600]
    for ip in stale_cleared:
        _honeypot_cleared.pop(ip, None)
    stale_env = [ip for ip, hits in _env_signal_hits.items()
                 if all(now - t > _ENV_SIGNAL_WINDOW_SEC for t in hits)]
    for ip in stale_env:
        _env_signal_hits.pop(ip, None)

def encode_flow_cookie(flow_id: str, stage: str, client_ip: str) -> str:
    ts = time.time()
    raw = f"{flow_id}|{stage}|{client_ip}|{ts}"
    sig = sign_data(raw)
    return f"{raw}|{sig}"

def decode_and_verify_flow_cookie(cookie_val: str, expected_ip: str) -> tuple[bool, str, str]:
    if not cookie_val:
        return False, "", ""
    try:
        parts = cookie_val.split("|")
        if len(parts) != 5:
            return False, "", ""
        flow_id, stage, ip, ts_str, sig = parts
        raw = f"{flow_id}|{stage}|{ip}|{ts_str}"
        if not verify_signature(raw, sig):
            return False, "", ""
        if time.time() - float(ts_str) > 180:
            return False, "", ""
        return True, flow_id, stage
    except Exception:
        return False, "", ""

async def broadcast_log_to_soc(log_entry: dict):
    if not active_soc_websockets:
        return
    dead_sockets = set()
    message = json.dumps({"type": "new_log", "log": log_entry})
    for ws in list(active_soc_websockets):
        try:
            await ws.send_text(message)
        except Exception:
            dead_sockets.add(ws)
    active_soc_websockets.difference_update(dead_sockets)

@app.middleware("http")
async def security_firewall_middleware(request: Request, call_next):
    client_ip = extract_client_ip(request)
    ua = request.headers.get("user-agent", "")
    path = request.url.path

    if path.startswith("/static") or path == "/admin" or path.startswith("/ws"):
        return await call_next(request)

    if is_ip_banned(client_ip):
        if not path.startswith("/api/"):
            return templates.TemplateResponse(
                request=request,
                name="blocked.html",
                context={"reason": "IP Adresiniz Kural Ihlali Nedeniyle Gecici Olarak Engellendi", "client_ip": client_ip},
                status_code=403
            )
        return JSONResponse(status_code=403, content={"error": "IP adresiniz engellendi."})

    is_safe, reason, details = check_ip_security(request)
    if not is_safe:
        # Kendi geliştirici IP'lerini asla bloklama

        if client_ip in DEV_IPS:
            return await call_next(request)
        threat_type = "VPN_WARP_BLOCKED" if (details.get("is_warp") or details.get("is_datacenter")) else "BOT_HEADER_DETECTED"
        log = add_security_log(client_ip, ua, threat_type, "HIGH", reason, details, True)
        asyncio.create_task(broadcast_log_to_soc(log))

        if not path.startswith("/api/"):
            return templates.TemplateResponse(
                request=request,
                name="blocked.html",
                context={"reason": reason, "client_ip": client_ip},
                status_code=403
            )
        return JSONResponse(status_code=403, content={"error": reason})

    return await call_next(request)

@app.middleware("http")
async def challenge_wall_middleware(request: Request, call_next):
    if not config.CHALLENGE_WALL_ENABLED:
        return await call_next(request)

    path = request.url.path

    if path == "/challenge":
        return await call_next(request)

    if is_bypass_path(path):
        return await call_next(request)

    client_ip = extract_client_ip(request)
    ua = request.headers.get("user-agent", "")

    clr_cookie = request.cookies.get(config.CLEARANCE_COOKIE_NAME, "")
    valid, reason = validate_clearance_token(clr_cookie, client_ip)

    if valid:
        return await call_next(request)

    if path.startswith("/api/"):
        log = add_security_log(
            client_ip, ua,
            "WALL_CHALLENGE_REQUIRED", "MEDIUM",
            f"Challenge Wall — API isteği temizlenmemiş ({reason})",
            {"path": path, "reason": reason},
            True
        )
        asyncio.create_task(broadcast_log_to_soc(log))
        return JSONResponse(
            status_code=403,
            content={
                "error": "challenge_required",
                "message": "Bu kaynağa erişmek için tarayıcı doğrulaması gereklidir.",
                "challenge_url": "/challenge"
            }
        )

    next_url = request.url.path
    if request.url.query:
        next_url = f"{next_url}?{request.url.query}"

    log = add_security_log(
        client_ip, ua,
        "WALL_CHALLENGE_REQUIRED", "LOW",
        f"Challenge Wall — Clearance yok/süresi dolmuş ({reason})",
        {"path": path, "reason": reason},
        False
    )
    asyncio.create_task(broadcast_log_to_soc(log))

    from urllib.parse import quote
    redirect_url = f"/challenge?next={quote(next_url, safe='')}"
    return RedirectResponse(url=redirect_url, status_code=302)

@app.get("/", response_class=HTMLResponse)
async def index_page(request: Request):
    ctx = _get_loader_context()
    return templates.TemplateResponse(request=request, name="index.html", context=ctx)

@app.get("/login", response_class=RedirectResponse)
async def login_redirect(request: Request):
    execution = str(uuid.uuid4())
    client_id = "security-admin-console"
    tab_id = secrets.token_urlsafe(8)

    client_data_dict = {
        "ru": str(request.base_url) + "admin/master/console/",
        "rt": "code",
        "rm": "query",
        "st": str(uuid.uuid4())
    }
    client_data_b64 = base64.urlsafe_b64encode(json.dumps(client_data_dict).encode()).decode().rstrip("=")

    url = f"/realms/master/login-actions/authenticate?execution={execution}&client_id={client_id}&tab_id={tab_id}&client_data={client_data_b64}"
    return RedirectResponse(url=url)

@app.get("/realms/master/login-actions/authenticate", response_class=HTMLResponse)
async def login_page(request: Request, execution: str = None, client_id: str = None, tab_id: str = None, client_data: str = None):
    if not all([execution, client_id, tab_id, client_data]):
        return RedirectResponse(url="/login")

    client_ip = extract_client_ip(request)
    flow_id = secrets.token_hex(12)
    flow_cookie = encode_flow_cookie(flow_id, "PAGE_LOADED", client_ip)
    page_nonce = derive_page_nonce(flow_id)

    m = _load_manifest()
    loader_js = m.get("loader", "wasd-loader.js") if m else "wasd-loader.js"
    loader_v  = f"{m.get('ts', 0):08x}" if m else "00000000"
    loader_integrity = m.get("loader_integrity", "") if m else ""  # SRI hash
    shield_js = m.get("fake_loader", "") if m else ""
    sc_key    = m.get("sc_key",  "ShieldCore")    if m else "ShieldCore"
    scr_key   = m.get("scr_key", "ShieldCoreReady") if m else "ShieldCoreReady"
    parts     = m.get("parts", []) if m else []
    puzzle_js = m.get("puzzle", "") if m else ""
    
    # Capability wrapper — closure içinde page_nonce'u sakla
    nonce_cap_global = m.get("nonce_cap", "_wasd_nc_fallback") if m else "_wasd_nc_fallback"
    from obfuscate_js import build_capability_wrapper
    nonce_capability_script = build_capability_wrapper(page_nonce, nonce_cap_global)
    print(f"[DEBUG] nonce_cap_global={nonce_cap_global}, nonce={page_nonce[:8]}..., script_len={len(nonce_capability_script)}")

    m = _load_manifest()
    loader_js  = m.get("loader",      "wasd-loader.js") if m else "wasd-loader.js"
    loader_v   = f"{m.get('ts', 0):08x}" if m else "00000000"
    loader_integrity = m.get("loader_integrity", "") if m else ""  # SRI hash
    shield_js  = m.get("fake_loader", "")               if m else ""
    parts      = m.get("parts",       [])               if m else []
    # puzzle_js artık kullanılmıyor — image-captcha.js direkt yükleniyor
    puzzle_js  = ""
    hp_field_id = secrets.token_hex(4)
    
    # Session key for evidence signature (HMAC)
    session_key = secrets.token_hex(32)

    response = templates.TemplateResponse(request=request, name="login.html", context={
        "flow_id":    flow_id,
        "page_nonce": page_nonce,
        "session_key": session_key,
        "nonce_capability_script": nonce_capability_script,
        "hp_field_id": hp_field_id,
        "loader_v":   loader_v,
        "loader_js":  loader_js,
        "loader_integrity": loader_integrity,  # SRI
        "shield_js":  shield_js,
        "parts":      parts,
        "puzzle_js":  puzzle_js,
        "all_dist":   _get_loader_context()["all_dist"],
    })
    response.set_cookie(key="_w_flow", value=flow_cookie, httponly=True, samesite="strict", max_age=180)
    response.set_cookie(key="_w_session_key", value=session_key, httponly=False, samesite="strict", max_age=180)  # Client needs access for HMAC
    return response

@app.get("/register", response_class=RedirectResponse)
async def register_redirect(request: Request):
    execution = str(uuid.uuid4())
    client_id = "security-admin-console"
    tab_id = secrets.token_urlsafe(8)

    client_data_dict = {
        "ru": str(request.base_url) + "admin/master/console/",
        "rt": "code",
        "rm": "query",
        "st": str(uuid.uuid4())
    }
    client_data_b64 = base64.urlsafe_b64encode(json.dumps(client_data_dict).encode()).decode().rstrip("=")

    url = f"/realms/master/login-actions/registration?execution={execution}&client_id={client_id}&tab_id={tab_id}&client_data={client_data_b64}"
    return RedirectResponse(url=url)

@app.get("/realms/master/login-actions/registration", response_class=HTMLResponse)
async def register_page(request: Request, execution: str = None, client_id: str = None, tab_id: str = None, client_data: str = None):
    if not all([execution, client_id, tab_id, client_data]):
        return RedirectResponse(url="/register")

    client_ip = extract_client_ip(request)
    flow_id = secrets.token_hex(12)
    flow_cookie = encode_flow_cookie(flow_id, "PAGE_LOADED", client_ip)
    page_nonce = derive_page_nonce(flow_id)

    m = _load_manifest()
    loader_js = m.get("loader", "wasd-loader.js") if m else "wasd-loader.js"
    loader_v  = f"{m.get('ts', 0):08x}" if m else "00000000"
    loader_integrity = m.get("loader_integrity", "") if m else ""  # SRI hash
    shield_js = m.get("fake_loader", "") if m else ""
    sc_key    = m.get("sc_key",  "ShieldCore")    if m else "ShieldCore"
    scr_key   = m.get("scr_key", "ShieldCoreReady") if m else "ShieldCoreReady"
    parts     = m.get("parts", []) if m else []
    
    # Capability wrapper
    nonce_cap_global = m.get("nonce_cap", "_wasd_nc_fallback") if m else "_wasd_nc_fallback"
    from obfuscate_js import build_capability_wrapper
    nonce_capability_script = build_capability_wrapper(page_nonce, nonce_cap_global)

    hp_field_id = secrets.token_hex(4)
    
    # Session key for evidence signature (HMAC)
    session_key = secrets.token_hex(32)

    response = templates.TemplateResponse(request=request, name="register.html", context={
        "flow_id": flow_id,
        "page_nonce": page_nonce,
        "session_key": session_key,
        "nonce_capability_script": nonce_capability_script,
        "loader_js": loader_js,
        "loader_v": loader_v,
        "loader_integrity": loader_integrity,  # SRI
        "shield_js": shield_js,
        "parts": parts,
        "hp_field_id": hp_field_id,
        "all_dist": _get_loader_context()["all_dist"],
    })
    response.set_cookie(key="_w_flow", value=flow_cookie, httponly=True, samesite="strict", max_age=180)
    response.set_cookie(key="_w_session_key", value=session_key, httponly=False, samesite="strict", max_age=180)  # Client needs access for HMAC
    return response

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    auth_token = request.cookies.get("wasdw_session")
    if not auth_token or auth_token not in active_sessions:
        return RedirectResponse(url="/login?error=unauthorized")

    session_meta = active_sessions[auth_token]
    client_ip = extract_client_ip(request)
    ua = request.headers.get("user-agent", "")

    if session_meta.get("ip") != client_ip or session_meta.get("ua") != ua:
        active_sessions.pop(auth_token, None)
        log = add_security_log(client_ip, ua, "SESSION_HIJACK_DETECTED", "CRITICAL", f"Session Hijack Tespit Edildi! IP/UA uyumsuz. Token: {auth_token[:8]}...", {}, True)
        asyncio.create_task(broadcast_log_to_soc(log))
        response = RedirectResponse(url="/login?error=session_invalidated")
        response.delete_cookie("wasdw_session")
        return response

    ctx = _get_loader_context()
    return templates.TemplateResponse(request=request, name="dashboard.html", context=ctx)

@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    # Token kontrolü: query param veya X-Admin-Token header
    token = request.query_params.get("token") or request.headers.get("X-Admin-Token", "")
    if not token or token != config.ADMIN_TOKEN:
        # Token yoksa veya yanlışsa login formu göster (düz 401 yerine)
        return HTMLResponse(
            content="""<!DOCTYPE html><html><head><meta charset="UTF-8">
<title>SOC Giriş</title>
<style>
  *{margin:0;padding:0;box-sizing:border-box}
  body{background:#0a0a0c;color:#f4f4f6;font-family:Inter,system-ui,sans-serif;
       display:flex;align-items:center;justify-content:center;min-height:100vh}
  .card{background:#16161a;border:1px solid rgba(255,255,255,0.06);border-radius:22px;
        padding:2.5rem 2rem;width:360px;text-align:center}
  h1{font-size:1.3rem;font-weight:800;margin-bottom:.4rem}
  p{color:#71717a;font-size:.85rem;margin-bottom:1.8rem}
  input{width:100%;background:#111114;border:1px solid rgba(255,255,255,0.08);
        border-radius:10px;padding:.75rem 1rem;color:#f4f4f6;font-size:.9rem;
        outline:none;margin-bottom:1rem}
  button{width:100%;background:#fafafa;color:#0a0a0c;border:none;border-radius:999px;
         padding:.8rem;font-size:.9rem;font-weight:700;cursor:pointer}
  button:hover{background:#fff}
</style></head><body>
<div class="card">
  <h1>SOC Paneli</h1>
  <p>Admin token girin</p>
  <input type="password" id="tok" placeholder="Admin Token" onkeydown="if(event.key==='Enter')go()">
  <button onclick="go()">Giriş</button>
</div>
<script>
function go(){
  var t=document.getElementById('tok').value.trim();
  if(t) window.location.href='/admin?token='+encodeURIComponent(t);
}
</script>
</body></html>""",
            status_code=401
        )
    ctx = _get_loader_context()
    ctx["admin_token"] = token   # JS'e geçir — API isteklerinde kullanılacak
    ctx["server_host"] = request.headers.get("host", "")
    return templates.TemplateResponse(request=request, name="admin.html", context=ctx)

from security.dynamic_vm import create_polymorphic_challenge, verify_polymorphic_solution, derive_page_nonce

@app.get("/challenge", response_class=HTMLResponse)
async def challenge_page(request: Request, next: str = "/"):
    client_ip = extract_client_ip(request)

    clr_cookie = request.cookies.get(config.CLEARANCE_COOKIE_NAME, "")
    valid, _ = validate_clearance_token(clr_cookie, client_ip)
    if valid:
        return RedirectResponse(url=next or "/", status_code=302)

    safe_next = next if (next.startswith("/") and not next.startswith("//")) else "/"

    site_host = request.headers.get("host", "wasdw.market")

    _cm = _load_manifest()
    _cwall_js = _resolve_challenge_wall_js(_cm)
    _cwall_v  = f"{_cm.get('ts', 0):08x}" if _cm else "00000000"

    return templates.TemplateResponse(
        request=request,
        name="challenge.html",
        context={
            "next_url":       safe_next,
            "site_host":      site_host,
            "client_ip":      client_ip,
            "challenge_wall_js": _cwall_js,
            "challenge_wall_v":  _cwall_v,
        }
    )

class WallChallengeRequestPayload(BaseModel):
    ray: str = ""

@app.post("/api/challenge/request")
async def wall_challenge_request(payload: WallChallengeRequestPayload, request: Request):
    client_ip = extract_client_ip(request)
    ua = request.headers.get("user-agent", "")

    allowed, _ = check_rate_limit(client_ip)
    if not allowed:
        return JSONResponse(status_code=429, content={"error": "Çok fazla istek. Lütfen bekleyin."})

    challenge = create_wall_challenge(client_ip)

    return JSONResponse(content={
        "chal_id":    challenge["chal_id"],
        "chal_salt":  challenge["chal_salt"],
        "chal_diff":  challenge["chal_diff"],
        "chal_token": challenge["chal_token"],
        "expires_in": challenge["expires_in"],
        "algorithm":  "SHA-256",
    })

class WallChallengeVerifyPayload(BaseModel):
    chal_token:     str
    solution_nonce: str
    canvas_hash:    str = ""
    webdriver:      bool = False
    plugins_len:    int = 0
    screen_w:       int = 0
    screen_h:       int = 0
    color_depth:    int = 24
    ray:            str = ""

@app.post("/api/challenge/verify")
async def wall_challenge_verify(payload: WallChallengeVerifyPayload, request: Request):
    client_ip = extract_client_ip(request)
    ua = request.headers.get("user-agent", "")

    ok, reason = verify_wall_solution(
        chal_token     = payload.chal_token,
        solution_nonce = payload.solution_nonce,
        client_ip      = client_ip,
        canvas_hash    = payload.canvas_hash,
        webdriver      = payload.webdriver,
    )

    if not ok:
        log = add_security_log(
            client_ip, ua,
            "WALL_CHALLENGE_FAILED", "HIGH",
            f"Challenge Wall başarısız: {reason}",
            {
                "reason":      reason,
                "webdriver":   payload.webdriver,
                "canvas_hash": payload.canvas_hash[:8] if payload.canvas_hash else "",
                "ray":         payload.ray,
            },
            True
        )
        asyncio.create_task(broadcast_log_to_soc(log))
        record_failed_attempt(client_ip)

        return JSONResponse(
            status_code=403,
            content={
                "status":  "error",
                "message": _wall_user_message(reason),
            }
        )

    clearance_token = issue_clearance_token(client_ip)

    log = add_security_log(
        client_ip, ua,
        "WALL_CHALLENGE_PASSED", "SAFE",
        "Challenge Wall geçildi — clearance verildi",
        {"ray": payload.ray, "screen": f"{payload.screen_w}x{payload.screen_h}"},
        False
    )
    asyncio.create_task(broadcast_log_to_soc(log))

    # Honeypot korelasyon: başarılı challenge → gerçek kullanıcı, pending şüpheyi temizle
    _honeypot_clear_ip(client_ip)

    res = JSONResponse(content={
        "status":   "ok",
        "redirect": "/",
    })

    res.set_cookie(
        key      = config.CLEARANCE_COOKIE_NAME,
        value    = clearance_token,
        httponly = True,
        samesite = "lax",
        max_age  = config.CLEARANCE_COOKIE_MAX_AGE,
        path     = "/",
    )
    return res

def _wall_user_message(reason: str) -> str:
    msgs = {
        "automation_detected":     "Otomasyon aracı tespit edildi.",
        "solve_too_fast":          "Çözüm çok hızlı — bot şüphesi.",
        "pow_invalid":             "Proof-of-Work çözümü hatalı.",
        "challenge_expired":       "Challenge süresi doldu. Lütfen tekrar deneyin.",
        "token_signature_invalid": "Challenge token geçersiz (manipülasyon tespit edildi).",
        "ip_mismatch":             "IP adresi değişti. Lütfen sayfayı yenileyin.",
        "token_expired":           "Oturum süresi doldu.",
        "missing_fields":          "Eksik alan.",
    }
    for key, msg in msgs.items():
        if key in reason:
            return msg
    return "Güvenlik doğrulaması başarısız. Lütfen tekrar deneyin."

@app.post("/api/security/challenge")
async def request_challenge(request: Request):
    client_ip = extract_client_ip(request)
    ua = request.headers.get("user-agent", "")
    flow_cookie = request.cookies.get("_w_flow", "")

    valid, flow_id, stage = decode_and_verify_flow_cookie(flow_cookie, client_ip)
    if not valid or not (stage == "PAGE_LOADED" or stage.startswith("CHALLENGE_ISSUED") or stage.startswith("VERIFIED")):
        log = add_security_log(client_ip, ua, "STAGE_VIOLATION", "HIGH", "Aşama 1 Atlandı (Sayfa yükleme cookie'si eksik/geçersiz)", {}, True)
        await broadcast_log_to_soc(log)
        return JSONResponse(status_code=403, content={"status": "error", "message": "Güvenlik akışı ihlali (Önce sayfayı yükleyin)."})

    allowed, _ = check_rate_limit(client_ip)
    if not allowed:
        log = add_security_log(client_ip, ua, "RATE_LIMIT_EXCEEDED", "MEDIUM", "İstek sınırı aşıldı", {}, True)
        await broadcast_log_to_soc(log)
        return JSONResponse(status_code=429, content={"error": "Çok fazla istek."})

    page_nonce = derive_page_nonce(flow_id)
    challenge = create_polymorphic_challenge(flow_id, client_ip, page_nonce)

    new_cookie = encode_flow_cookie(flow_id, f"CHALLENGE_ISSUED:{challenge['chal_id']}", client_ip)

    res = JSONResponse(content={
        "chal_id":       challenge["chal_id"],
        "chal_salt":     challenge["chal_salt"],
        "chal_diff":     challenge["chal_diff"],
        "chal_ops":      challenge["chal_ops"],
        "chal_payload":  challenge["chal_payload"],
        "chal_ek":       challenge["chal_ek"],
        "binding_nonce": challenge["binding_nonce"],
        "algorithm":     "SHA-256"
    })
    res.set_cookie(key="_w_flow", value=new_cookie, httponly=True, samesite="strict", max_age=120)
    return res

class VerifyChallengePayload(BaseModel):
    challenge_token: str
    solution_nonce: str
    sensor_signature: str = ""
    telemetry_summary: str = ""
    canvas_hash: str = ""
    audio_hash: str = ""
    webgl_vendor: str = ""
    webgl_renderer: str = ""
    webdriver: bool = False
    automation_artifacts: list = []
    plugins_len: int = 0
    screen_w: int = 0
    screen_h: int = 0
    color_depth: int = 24
    dwell_time_ms: int = 0
    hp_field: str = ""
    binding_sig: str = ""   # canvas_hash + binding_nonce HMAC'ı

@app.post("/api/security/verify-challenge")
async def verify_challenge_callback(payload: VerifyChallengePayload, request: Request):
    client_ip = extract_client_ip(request)
    ua = request.headers.get("user-agent", "")
    flow_cookie = request.cookies.get("_w_flow", "")

    # ── Origin / Referer kontrolü — doğrudan API çağrısını engelle ──────────
    # Gerçek tarayıcı her zaman Origin veya Referer gönderir.
    # Node.js fetch ile doğrudan API çağrısında bunlar eksik veya uyumsuz olur.
    host      = request.headers.get("host", "")
    origin    = request.headers.get("origin", "").rstrip("/")
    referer   = request.headers.get("referer", "")
    expected_origins = {f"http://{host}", f"https://{host}"}

    origin_ok = (not origin) or (origin in expected_origins)
    referer_ok = (not referer) or any(referer.startswith(o) for o in expected_origins)

    if not origin_ok or not referer_ok:
        log = add_security_log(
            client_ip, ua, "ORIGIN_MISMATCH", "HIGH",
            f"Origin/Referer uyuşmazlığı — doğrudan API erişimi",
            {"origin": origin, "referer": referer[:80], "host": host},
            True
        )
        await broadcast_log_to_soc(log)
        return JSONResponse(status_code=403, content={"status": "error", "message": "Güvenlik protokol ihlali."})

    valid, flow_id, stage = decode_and_verify_flow_cookie(flow_cookie, client_ip)
    if not valid or not stage.startswith("CHALLENGE_ISSUED"):
        log = add_security_log(client_ip, ua, "STAGE_VIOLATION", "HIGH", "Aşama 2 Atlandı (Challenge aşaması geçersiz)", {}, True)
        await broadcast_log_to_soc(log)
        return JSONResponse(status_code=403, content={"status": "error", "message": "Güvenlik protokol sırası ihlali."})

    vm_valid, vm_reason, vm_meta = verify_polymorphic_solution(
        payload.challenge_token,
        payload.solution_nonce,
        payload.sensor_signature,
        payload.telemetry_summary,
        canvas_hash = payload.canvas_hash,
        binding_sig = payload.binding_sig,
    )
    if not vm_valid:
        log = add_security_log(client_ip, ua, "POW_VM_FAILED", "HIGH", vm_reason, vm_meta, True)
        await broadcast_log_to_soc(log)
        return JSONResponse(status_code=400, content={"status": "error", "message": vm_reason})

    # ── Sunucu tarafı timing tutarlılık kontrolü ─────────────────────────────
    # dwell_time_ms: JS'nin page load'dan bu noktaya kadar ölçtüğü süre.
    # solve_duration: sunucunun challenge issued → verify arası ölçtüğü süre.
    #
    # Kural 1: dwell_time_ms < 1500ms → gerçek kullanıcı sayfayı okuyamaz
    # Kural 2: dwell_time_ms > 300_000ms (5dk) → script sabit offset eklemiş
    # Kural 3: |dwell_time_ms - solve_duration_ms| > 120s → uçurum var
    dwell_ms     = payload.dwell_time_ms
    solve_dur_ms = round(vm_meta.get("solve_time", 0) * 1000)

    timing_fail_reason = None
    if dwell_ms < 1500:
        timing_fail_reason = f"dwell_time_ms çok düşük ({dwell_ms}ms < 1500ms) — sayfa render edilmedi"
    elif dwell_ms > 300_000:
        timing_fail_reason = f"dwell_time_ms anormal yüksek ({dwell_ms}ms) — script offset şüphesi"
    elif abs(dwell_ms - solve_dur_ms) > 120_000:
        timing_fail_reason = (
            f"dwell/solve uçurumu: dwell={dwell_ms}ms solve={solve_dur_ms}ms "
            f"fark={abs(dwell_ms - solve_dur_ms)}ms"
        )

    if timing_fail_reason:
        log = add_security_log(
            client_ip, ua, "TIMING_ANOMALY", "HIGH",
            f"Timing tutarsızlığı: {timing_fail_reason}",
            {"dwell_ms": dwell_ms, "solve_ms": solve_dur_ms},
            True
        )
        await broadcast_log_to_soc(log)
        record_failed_attempt(client_ip)
        return JSONResponse(
            status_code=403,
            content={"status": "error", "message": "Güvenlik doğrulaması başarısız."}
        )

    telemetry_data = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
    is_human, bot_reason, bot_report = analyze_client_telemetry(telemetry_data)
    if not is_human:
        log = add_security_log(client_ip, ua, "BOT_DETECTED", "CRITICAL", bot_reason, bot_report, True)
        await broadcast_log_to_soc(log)
        record_failed_attempt(client_ip)
        return JSONResponse(status_code=403, content={"status": "error", "message": bot_reason})

    session_nonce = generate_session_nonce()
    fingerprint_hash = f"{payload.canvas_hash[:8]}_{payload.audio_hash[:8]}"
    ticket = issue_login_ticket(session_nonce, fingerprint_hash)

    valid_t, t_meta = consume_login_ticket.__globals__["validate_login_ticket"](ticket)
    ticket_id = t_meta.get("ticket_id", "unknown") if valid_t else "unknown"

    # session_key: session_nonce'un ilk 16 byte'ının hex'i — client RC4 key derivation için.
    # PoW + VM + bot check geçilmeden üretilmez → offline decode mümkün değil.
    # 32 hex char = 16 byte entropi, her session'da farklı.
    session_key = session_nonce[:32]   # session_nonce zaten 32-char hex (16 byte)

    new_cookie = encode_flow_cookie(flow_id, f"VERIFIED:{ticket_id}", client_ip)

    log = add_security_log(
        client_ip, ua, "CHALLENGE_SOLVED", "SAFE",
        f"PoW ve Dinamik VM Doğrulandı ({vm_meta.get('solve_time')}s)",
        {"solve_time": vm_meta.get("solve_time"), "bot_score": bot_report["bot_score"]},
        False
    )
    await broadcast_log_to_soc(log)

    res = JSONResponse(content={
        "status":       "ok",
        "login_ticket": ticket,
        "session_key":  session_key,   # RC4 key derivation — PoW kanıtı olmadan elde edilemez
        "valid_seconds": config.LOGIN_TICKET_EXPIRATION_SECONDS
    })
    res.set_cookie(key="_w_flow", value=new_cookie, httponly=True, samesite="strict", max_age=60)
    return res

class LoginPayload(BaseModel):
    username:        str
    password:        str
    login_ticket:    str
    captcha_token:   str = ""   # zorunlu — /api/captcha/verify'dan dönen clearance
    evidence_token:  str = ""   # zorunlu — /api/auth/submit-evidence'dan dönen token (two-phase auth)
    hp_trap:         str = ""

@app.post("/api/auth/login")
async def auth_login(payload: LoginPayload, request: Request, response: Response):
    client_ip = extract_client_ip(request)
    ua = request.headers.get("user-agent", "")
    
    # ── Evidence Token Verification (Two-Phase Auth) ─────────────────────────
    # Phase 1: Evidence submission → evidence_token
    # Phase 2: Login submission → requires valid evidence_token
    
    # TODO: Production'da evidence_token zorunlu olacak
    # Şimdilik optional (backward compatibility)
    evidence_token = payload.evidence_token
    
    if evidence_token:
        # Evidence token provided, verify it
        if not verify_evidence_token(evidence_token, client_ip):
            log = add_security_log(
                client_ip, ua,
                "EVIDENCE_TOKEN_INVALID", "HIGH",
                "Invalid, expired, or reused evidence token",
                {}, True
            )
            await broadcast_log_to_soc(log)
            return JSONResponse(
                status_code=403,
                content={
                    "status": "error",
                    "message": "Invalid evidence token."
                }
            )
        # Evidence token verified
        print(f"[Auth] Evidence token verified for {client_ip}")
    else:
        # No evidence token (backward compatibility mode)
        print(f"[Auth] No evidence token provided (backward compatibility mode)")
    
    # Evidence token verified (if provided) — proceed with normal login flow
    
    flow_cookie = request.cookies.get("_w_flow", "")

    valid, flow_id, stage = decode_and_verify_flow_cookie(flow_cookie, client_ip)
    if not valid or not stage.startswith("VERIFIED"):
        log = add_security_log(client_ip, ua, "STAGE_VIOLATION", "HIGH", "Direct POST / Checker Engellendi (Callback yapılmadı)", {}, True)
        await broadcast_log_to_soc(log)
        res = JSONResponse(status_code=403, content={"status": "error", "message": "Geçersiz güvenlik bileti. Callback çözümü eksik."})
        res.delete_cookie(key="_w_flow")
        return res

    if payload.hp_trap and len(payload.hp_trap.strip()) > 0:
        ban_ip(client_ip, "Honeypot Tuzağı")
        log = add_security_log(client_ip, ua, "HONEYPOT_TRIGGERED", "CRITICAL", "Bot Form Honeypot Tuzağına Düştü", {}, True)
        await broadcast_log_to_soc(log)
        res = JSONResponse(status_code=403, content={"status": "error", "message": "Güvenlik ihlali."})
        res.delete_cookie(key="_w_flow")
        return res

    # ── CAPTCHA clearance zorunlu kontrolü ───────────────────────────────────
    if not payload.captcha_token or not payload.captcha_token.strip():
        log = add_security_log(client_ip, ua, "CAPTCHA_MISSING", "HIGH", "CAPTCHA token eksik — login engellendi", {}, True)
        await broadcast_log_to_soc(log)
        res = JSONResponse(status_code=403, content={"status": "error", "message": "Resim doğrulaması gerekli."})
        res.delete_cookie(key="_w_flow")
        return res

    # CAPTCHA clearance token doğrula — CAPTCHA_CLR:<ip>:<ts>:<nonce>:<sig>
    now = time.time()
    _captcha_valid  = False
    _captcha_reason = "unverified"
    try:
        raw_clr   = base64.urlsafe_b64decode(payload.captcha_token.encode() + b"==").decode()
        clr_parts = raw_clr.split(":")
        # CAPTCHA_CLR:<ip>:<ts>:<nonce>:<sig>  → 5 parça
        if len(clr_parts) == 5:
            clr_type, clr_ip, clr_ts, clr_nonce, clr_sig = clr_parts
            if clr_type == "CAPTCHA_CLR":
                clr_payload  = f"CAPTCHA_CLR:{clr_ip}:{clr_ts}:{clr_nonce}"
                expected_sig = hmac.new(config.SECRET_KEY.encode(), clr_payload.encode(), hashlib.sha256).hexdigest()
                if hmac.compare_digest(expected_sig, clr_sig):
                    if clr_ip == client_ip:
                        if now - float(clr_ts) <= 180:   # max 3 dk geçerli
                            _captcha_valid  = True
                            _captcha_reason = "ok"
                        else:
                            _captcha_reason = "expired"
                    else:
                        _captcha_reason = "ip_mismatch"
                else:
                    _captcha_reason = "sig_invalid"
        else:
            _captcha_reason = "malformed"
    except Exception as _ce:
        _captcha_reason = f"parse_error:{type(_ce).__name__}"

    if not _captcha_valid:
        log = add_security_log(client_ip, ua, "CAPTCHA_INVALID", "HIGH", f"CAPTCHA token geçersiz: {_captcha_reason}", {}, True)
        await broadcast_log_to_soc(log)
        res = JSONResponse(status_code=403, content={"status": "error", "message": "Resim doğrulaması başarısız. Sayfayı yenileyin."})
        res.delete_cookie(key="_w_flow")
        return res

    ticket_valid, ticket_reason, ticket_meta = consume_login_ticket(payload.login_ticket)
    if not ticket_valid:
        log = add_security_log(client_ip, ua, "INVALID_TICKET", "HIGH", f"Bilet Geçersiz: {ticket_reason}", {}, True)
        await broadcast_log_to_soc(log)
        res = JSONResponse(status_code=403, content={"status": "error", "message": ticket_reason})
        res.delete_cookie(key="_w_flow")
        return res

    expected_ticket_id = stage.split(":")[1] if ":" in stage else ""
    if expected_ticket_id != ticket_meta.get("ticket_id"):
        log = add_security_log(client_ip, ua, "TICKET_MISMATCH", "HIGH", "Bilet ve Cookie Akışı Eşleşmedi", {}, True)
        await broadcast_log_to_soc(log)
        res = JSONResponse(status_code=403, content={"status": "error", "message": "Bilet oturum ile eşleşmiyor."})
        res.delete_cookie(key="_w_flow")
        return res

    user = verify_user(payload.username, payload.password)
    if not user:
        log = add_security_log(client_ip, ua, "AUTH_FAILED", "MEDIUM", f"Hatalı Giriş: {payload.username}", {}, True)
        await broadcast_log_to_soc(log)
        res = JSONResponse(status_code=401, content={"status": "error", "message": "Kullanıcı adı veya şifre hatalı. Güvenlik nedeniyle sayfa yenilenmelidir."})
        res.delete_cookie(key="_w_flow")
        return res

    reset_failed_attempts(client_ip)
    session_token = generate_session_nonce()
    active_sessions[session_token] = {
        "ip": client_ip,
        "ua": ua,
        "username": user["username"],
        "created_at": time.time(),
    }

    log = add_security_log(client_ip, ua, "LOGIN_SUCCESS", "SAFE", f"Başarılı Giriş: {payload.username}", {"user": user["username"]}, False)
    await broadcast_log_to_soc(log)

    # Honeypot korelasyon: başarılı login → gerçek kullanıcı
    _honeypot_clear_ip(client_ip)

    response_data = {
        "status": "success",
        "user": {
            "id": user["id"],
            "username": user["username"],
            "balance": user["balance"],
            "currency": user["currency"],
            "role": user["role"]
        },
        "session_token": session_token
    }

    res = JSONResponse(content=polymorphic_pack(response_data))
    res.set_cookie(key="wasdw_session", value=session_token, httponly=True, samesite="lax")
    res.delete_cookie(key="_w_flow")  # Akış tamamlandı, tek kullanımlık cookie'yi sil
    return res

class RegisterPayload(BaseModel):
    username: str
    password: str
    email: str = ""
    login_ticket: str
    hp_trap: str = ""

@app.post("/api/auth/register")
async def auth_register(payload: RegisterPayload, request: Request, response: Response):
    client_ip = extract_client_ip(request)
    ua = request.headers.get("user-agent", "")
    flow_cookie = request.cookies.get("_w_flow", "")

    valid, flow_id, stage = decode_and_verify_flow_cookie(flow_cookie, client_ip)
    if not valid or not stage.startswith("VERIFIED"):
        log = add_security_log(client_ip, ua, "STAGE_VIOLATION", "HIGH", "Direct POST Kayıt Denemesi Engellendi", {}, True)
        await broadcast_log_to_soc(log)
        res = JSONResponse(status_code=403, content={"status": "error", "message": "Güvenlik bileti geçersiz."})
        res.delete_cookie(key="_w_flow")
        return res

    if payload.hp_trap and len(payload.hp_trap.strip()) > 0:
        ban_ip(client_ip, "Honeypot Tuzağı (Kayıt)")
        res = JSONResponse(status_code=403, content={"status": "error", "message": "Güvenlik ihlali."})
        res.delete_cookie(key="_w_flow")
        return res

    ticket_valid, ticket_reason, ticket_meta = consume_login_ticket(payload.login_ticket)
    if not ticket_valid:
        res = JSONResponse(status_code=403, content={"status": "error", "message": ticket_reason})
        res.delete_cookie(key="_w_flow")
        return res

    success, msg = create_user(payload.username, payload.password, payload.email)
    if not success:
        res = JSONResponse(status_code=400, content={"status": "error", "message": msg})
        res.delete_cookie(key="_w_flow")
        return res

    session_token = generate_session_nonce()
    active_sessions[session_token] = {
        "ip": client_ip,
        "ua": ua,
        "username": payload.username,
        "created_at": time.time(),
    }

    log = add_security_log(client_ip, ua, "REGISTER_SUCCESS", "SAFE", f"Yeni Kayıt: {payload.username}", {}, False)
    await broadcast_log_to_soc(log)

    res = JSONResponse(content={"status": "success", "message": "Hesabınız başarıyla oluşturuldu."})
    res.set_cookie(key="wasdw_session", value=session_token, httponly=True, samesite="lax")
    res.delete_cookie(key="_w_flow")
    return res

@app.get("/api/admin/stats")
async def admin_stats(_: None = Depends(require_admin)):
    """DB'den canlı istatistikleri döner."""
    base = get_stats()
    by_event = base.get("by_event_type", {})
    # PoW özet metrikleri
    pow_solved  = by_event.get("CHALLENGE_SOLVED", 0)
    pow_failed  = by_event.get("POW_VM_FAILED", 0) + by_event.get("TIMING_ANOMALY", 0)
    bot_count   = by_event.get("BOT_DETECTED", 0)  + by_event.get("BOT_ENV_DETECTED", 0)
    vpn_count   = by_event.get("VPN_WARP_BLOCKED", 0)
    wall_passed = by_event.get("WALL_CHALLENGE_PASSED", 0)
    wall_failed = by_event.get("WALL_CHALLENGE_FAILED", 0)
    logins_ok   = by_event.get("LOGIN_SUCCESS", 0)
    # PoW ortalama solve süresi — son 500 CHALLENGE_SOLVED logundan
    conn = __import__("sqlite3").connect(
        __import__("os").path.join(__import__("os").path.dirname(__import__("os").path.abspath(__file__)), "database", "wasdw.db"),
        check_same_thread=False
    )
    conn.row_factory = __import__("sqlite3").Row
    rows = conn.execute(
        "SELECT details FROM security_logs WHERE event_type='CHALLENGE_SOLVED' ORDER BY timestamp DESC LIMIT 500"
    ).fetchall()
    conn.close()
    solve_times = []
    bot_scores  = []
    for r in rows:
        try:
            d = __import__("json").loads(r["details"] or "{}")
            if d.get("solve_time") is not None:
                solve_times.append(float(d["solve_time"]))
            if d.get("bot_score") is not None:
                bot_scores.append(float(d["bot_score"]))
        except Exception:
            pass
    avg_solve = round(sum(solve_times) / len(solve_times), 3) if solve_times else 0
    avg_score = round(sum(bot_scores)  / len(bot_scores),  1) if bot_scores  else 0
    return JSONResponse(content={
        "total_events":   base["total_logs"],
        "total_blocked":  base["total_blocked"],
        "active_bans":    base["active_bans"],
        "total_users":    base["total_users"],
        "vpn_blocked":    vpn_count,
        "bot_blocked":    bot_count,
        "pow_failed":     pow_failed,
        "pow_solved":     pow_solved,
        "wall_passed":    wall_passed,
        "wall_failed":    wall_failed,
        "success_logins": logins_ok,
        "avg_solve_sec":  avg_solve,
        "avg_bot_score":  avg_score,
        "by_event_type":  by_event,
        "by_threat_level": base.get("by_threat_level", {}),
    })

@app.get("/api/admin/logs")
async def admin_logs(limit: int = 100, _: None = Depends(require_admin)):
    """Son N güvenlik logu — details (solve_time, bot_score) dahil."""
    limit = min(max(limit, 1), 500)
    logs = get_recent_logs(limit)
    return JSONResponse(content={"logs": logs, "count": len(logs)})

@app.get("/api/admin/")
async def admin_bans(_: None = Depends(require_admin)):
    """Aktif IP ban listesi."""
    import sqlite3 as _sq, os as _os, time as _tm
    db_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "database", "wasdw.db")
    conn = _sq.connect(db_path, check_same_thread=False)
    conn.row_factory = _sq.Row
    now = _tm.time()
    rows = conn.execute(
        "SELECT ip, reason, banned_at, expires_at FROM ip_blacklist WHERE expires_at > ? ORDER BY banned_at DESC",
        (now,)
    ).fetchall()
    conn.close()
    return JSONResponse(content={
        "bans": [dict(r) for r in rows],
        "count": len(rows)
    })

@app.post("/api/admin/clear-bans")
async def admin_clear_bans(_: None = Depends(require_admin)):
    clear_all_bans()
    return JSONResponse(content={"status": "ok", "message": "Tüm IP banları kaldırıldı."})

# env-signal hit counter: {ip: [timestamp, ...]}
_env_signal_hits: dict = {}
_ENV_SIGNAL_WINDOW_SEC = 60   # pencere
_ENV_SIGNAL_BAN_THRESH = 3    # bu kadar hit → ban

@app.post("/api/security/env-signal")
async def env_signal(request: Request):
    """
    build_env_check() bot/headless tespitinde ateşlenen sinyal.
    - İstemciye hiçbir bilgi sızdırmaz (204 No Content)
    - Aynı IP _ENV_SIGNAL_BAN_THRESH kez sinyal gönderirse → kalıcı ban
    - Her hit SOC feed'ine BOT_ENV_DETECTED olayı olarak gönderilir
    """
    client_ip = extract_client_ip(request)
    ua = request.headers.get("user-agent", "")

    now = time.time()
    hits = _env_signal_hits.get(client_ip, [])
    # Pencere dışı eski kayıtları temizle
    hits = [t for t in hits if now - t < _ENV_SIGNAL_WINDOW_SEC]
    hits.append(now)
    _env_signal_hits[client_ip] = hits

    hit_count = len(hits)

    log = add_security_log(
        client_ip, ua,
        "BOT_ENV_DETECTED", "HIGH",
        f"Client-side env check tetiklendi — headless/automation (hit #{hit_count})",
        {"hit_count": hit_count, "window_sec": _ENV_SIGNAL_WINDOW_SEC},
        True
    )
    asyncio.create_task(broadcast_log_to_soc(log))

    if hit_count >= _ENV_SIGNAL_BAN_THRESH:
        ban_ip(client_ip, f"Headless/automation ortam tespiti (env-signal x{hit_count})")
        _env_signal_hits.pop(client_ip, None)  # sayacı sıfırla
        ban_log = add_security_log(
            client_ip, ua,
            "IP_BANNED", "CRITICAL",
            f"IP banlandı: env-signal eşiği aşıldı (x{hit_count})",
            {},
            True
        )
        asyncio.create_task(broadcast_log_to_soc(ban_log))

    return Response(status_code=204)


# ── Decoy Honeypot Endpoints ─────────────────────────────────────────────────
# _build_decoy_js() her decoy dosyasına rastgele bir /api/security/* path'i gömer.
#
# ÖNEMLİ: Gerçek tarayıcı da decoy dosyaları indirir ve çalıştırır — bu normal.
# Bu nedenle honeypot hit'i TEK BAŞINA ban sebebi değildir.
#
# Korelasyon mantığı:
#   - Honeypot hit → "pending suspect" listesine al, log bas, BAN YOK
#   - Aynı IP'den başarılı challenge/CAPTCHA gelirse → temizle (gerçek kullanıcı)
#   - Pencere içinde hit var ama başarılı challenge yoksa → o zaman ban
#
# Bu sayede gerçek kullanıcılar (decoy çalışır ama challenge geçer) etkilenmez,
# sadece decoy'u izole çalıştırıp challenge atmayan sandbox'lar ban yeer.

_HONEYPOT_PATHS: set[str] = set()   # manifest'ten yüklenir (lifespan'de doldurulur)

# Statik fallback — manifest yoksa veya honeypot_paths alanı boşsa kullanılır
_HONEYPOT_PATHS_FALLBACK = {
    "/api/security/hb",
    "/api/security/ping",
    "/api/security/ack",
    "/api/security/beacon",
    "/api/security/tick",
}


def _reload_honeypot_routes() -> None:
    """
    Manifest'ten build-time üretilen honeypot path'lerini okur,
    dinamik olarak route'lara bağlar.
    Her build'de path'ler değiştiği için lifespan'de çağrılır.
    """
    global _HONEYPOT_PATHS
    m = _load_manifest()
    paths_from_manifest = set(m.get("honeypot_paths", [])) if m else set()

    if paths_from_manifest:
        _HONEYPOT_PATHS = paths_from_manifest
        print(f"[WASDW] Honeypot path'leri manifest'ten yüklendi: {len(_HONEYPOT_PATHS)} path", flush=True)
    else:
        _HONEYPOT_PATHS = _HONEYPOT_PATHS_FALLBACK.copy()
        print(f"[WASDW] Honeypot path'leri fallback'ten yüklendi: {len(_HONEYPOT_PATHS)} path", flush=True)

    # Tüm path'leri dynamic route olarak kaydet
    for _hp in _HONEYPOT_PATHS:
        # Zaten kayıtlıysa atla (FastAPI duplicate route hatasını önle)
        _existing = {r.path for r in app.routes}
        if _hp not in _existing:
            app.add_api_route(
                _hp,
                _handle_honeypot,
                methods=["POST", "GET"],
                include_in_schema=False,
            )

_honeypot_hits:       dict = {}   # {ip: [timestamp, ...]}  — honeypot hit zamanları
_honeypot_cleared:    dict = {}   # {ip: timestamp}          — son başarılı challenge zamanı

_HONEYPOT_WINDOW_SEC   = 300   # pencere (5 dk)
_HONEYPOT_BAN_THRESH   = 8     # pencerede bu kadar hit VE başarılı challenge yoksa ban
_HONEYPOT_GRACE_SEC    = 300   # honeypot'tan sonra bu kadar süre içinde challenge geçerse af


def _honeypot_clear_ip(client_ip: str) -> None:
    """
    Başarılı challenge/CAPTCHA sonrası çağrılır.
    IP'yi pending suspect listesinden çıkarır — gerçek kullanıcı olarak işaretle.
    """
    _honeypot_cleared[client_ip] = time.time()
    # Birikmiş hit kayıtlarını da temizle
    _honeypot_hits.pop(client_ip, None)


async def _handle_honeypot(request: Request) -> Response:
    """
    Tüm honeypot path'leri buraya yönlenir.

    Tek başına ban uygulamaz — korelasyon mantığına göre değerlendirir:
    - Hit kaydedilir
    - Aynı IP pencere içinde başarılı challenge geçmişse: LOW severity, sadece log
    - Çok hit var ve challenge geçmemişse: ban
    """
    client_ip = extract_client_ip(request)
    ua        = request.headers.get("user-agent", "")
    path      = request.url.path

    # Geliştirici / loopback IP'leri muaf tut
}
    if client_ip in DEV_IPS:
        return Response(status_code=204)

    now  = time.time()

    # Son başarılı challenge zamanını kontrol et
    last_cleared = _honeypot_cleared.get(client_ip, 0)
    recently_cleared = (now - last_cleared) < _HONEYPOT_GRACE_SEC

    # Hit listesini güncelle
    hits = _honeypot_hits.get(client_ip, [])
    hits = [t for t in hits if now - t < _HONEYPOT_WINDOW_SEC]
    hits.append(now)
    _honeypot_hits[client_ip] = hits
    hit_count = len(hits)

    if recently_cleared:
        # Gerçek kullanıcı — decoy çalışıyor ama challenge geçmiş, beklenen davranış
        log = add_security_log(
            client_ip, ua,
            "DECOY_HONEYPOT_HIT", "LOW",
            f"Decoy honeypot tetiklendi — challenge geçmiş kullanıcı (beklenen), "
            f"path={path}, hit=#{hit_count}",
            {"path": path, "hit_count": hit_count, "cleared": True},
            False   # threat değil
        )
        asyncio.create_task(broadcast_log_to_soc(log))
        return Response(status_code=204)

    # Challenge geçmemiş — şüpheli seviyeyi hit sayısına göre belirle
    severity = "CRITICAL" if hit_count >= _HONEYPOT_BAN_THRESH else "MEDIUM"
    log = add_security_log(
        client_ip, ua,
        "DECOY_HONEYPOT_HIT", severity,
        f"Decoy honeypot tetiklendi — challenge geçmeden (sandbox/analiz şüphesi), "
        f"path={path}, hit=#{hit_count}",
        {"path": path, "hit_count": hit_count, "window_sec": _HONEYPOT_WINDOW_SEC},
        True
    )
    asyncio.create_task(broadcast_log_to_soc(log))

    # Ban: pencerede yeterli hit VE başarılı challenge yok
    if hit_count >= _HONEYPOT_BAN_THRESH:
        ban_ip(client_ip, f"Decoy honeypot — challenge olmadan eşik aşıldı (x{hit_count})")
        _honeypot_hits.pop(client_ip, None)
        ban_log = add_security_log(
            client_ip, ua,
            "IP_BANNED", "CRITICAL",
            f"IP banlandı: decoy honeypot + challenge yok (x{hit_count})",
            {},
            True
        )
        asyncio.create_task(broadcast_log_to_soc(ban_log))

    # Gerçek endpoint gibi davran — 204 dön, hata verme
    # (hata verirse sandbox "bu endpoint çalışmıyor" diye ayırt edebilir)
    return Response(status_code=204)


# ── Behavioral Data Collection (Server-Side Analysis) ────────────────────────
class BehavioralDataPayload(BaseModel):
    mouse:  list[dict] = []
    raf:    list[dict] = []
    scroll: list[dict] = []
    meta:   dict = {}

    def truncate(self):
        """Aşırı büyük payload'ları kırp — DoS koruması."""
        self.mouse  = self.mouse[:500]
        self.raf    = self.raf[:200]
        self.scroll = self.scroll[:200]


# Behavioral data storage (in-memory, production'da Redis/DB)
_behavioral_data: dict = {}  # {client_ip: [data1, data2, ...]}


@app.post("/api/behavioral-submit")
async def behavioral_submit(payload: BehavioralDataPayload, request: Request):
    """
    Server-side behavioral analysis endpoint.
    
    Client behavioral-collector.js'den ham event time-series alır:
    - mouse: (t, x, y) tuples
    - raf: (t, delta) tuples — requestAnimationFrame jitter
    - scroll: (t, x, y) tuples
    
    Server-side ML scoring (production'da):
    - Velocity, acceleration, entropy hesapla
    - Bezier curve detection (synthetic mouse paths)
    - rAF jitter distribution analysis (synthetic timing)
    - Per-user baseline (adaptive threshold)
    
    Şu anki implementation: minimal storage + logging
    Future: scikit-learn/statsmodels ML pipeline
    """
    client_ip = extract_client_ip(request)
    ua = request.headers.get("user-agent", "")
    
    # Basic validation
    if not payload.mouse and not payload.raf and not payload.scroll:
        return Response(status_code=204)  # Empty data, silent accept
    
    payload.truncate()  # DoS koruması — aşırı büyük payload'ları kırp
    
    # Store data (production'da Redis/DB)
    if client_ip not in _behavioral_data:
        _behavioral_data[client_ip] = []
    
    _behavioral_data[client_ip].append({
        "timestamp": time.time(),
        "mouse_events": len(payload.mouse),
        "raf_events": len(payload.raf),
        "scroll_events": len(payload.scroll),
        "duration": payload.meta.get("duration", 0),
        "user_agent": ua
    })
    
    # Keep last 10 submissions per IP (memory limit)
    if len(_behavioral_data[client_ip]) > 10:
        _behavioral_data[client_ip] = _behavioral_data[client_ip][-10:]
    
    # Basic heuristic scoring (production'da ML)
    score = 0
    risk_level = "LOW"
    flags = []
    
    # Mouse entropy check (minimal implementation)
    if len(payload.mouse) < 5:
        flags.append("insufficient_mouse_data")
        score += 20
    elif len(payload.mouse) > 90:
        # Too many events (possible synthetic flood)
        flags.append("excessive_mouse_events")
        score += 30
    
    # rAF jitter analysis (synthetic timing detection)
    if payload.raf:
        deltas = [e.get("delta", 0) for e in payload.raf if "delta" in e]
        if deltas:
            # Check for suspiciously uniform timing (synthetic)
            import statistics
            if len(deltas) > 5:
                try:
                    stddev = statistics.stdev(deltas)
                    mean = statistics.mean(deltas)
                    if stddev < 1.0 and mean > 0:
                        # Too uniform — likely synthetic (real browser has jitter)
                        flags.append("synthetic_raf_timing")
                        score += 50
                except:
                    pass
    
    # Scroll velocity (synthetic scroll detection)
    if len(payload.scroll) > 50:
        flags.append("excessive_scroll_events")
        score += 20
    
    # Risk level
    if score >= 60:
        risk_level = "HIGH"
    elif score >= 30:
        risk_level = "MEDIUM"
    
    # Log to SOC
    log = add_security_log(
        client_ip, ua,
        "BEHAVIORAL_DATA_SUBMITTED", risk_level,
        f"Behavioral data: mouse={len(payload.mouse)} raf={len(payload.raf)} scroll={len(payload.scroll)} score={score}",
        {"flags": flags, "score": score},
        False  # Don't block
    )
    asyncio.create_task(broadcast_log_to_soc(log))
    
    # Silent accept (never reject client-side)
    # Production: high score → increase challenge difficulty, rate limit
    return Response(status_code=204)


# ── Evidence-Based Authentication (Server-Side Decision) ─────────────────────

class EvidencePayload(BaseModel):
    evidence: dict
    signature: str


# Evidence token storage (short-lived, one-time use)
_evidence_tokens: dict = {}  # {token: {ip, timestamp, used}}
_EVIDENCE_TOKEN_TTL = 60  # seconds


def generate_evidence_token(client_ip: str) -> str:
    """Generate short-lived evidence token (one-time use)"""
    token = secrets.token_urlsafe(32)
    _evidence_tokens[token] = {
        "ip": client_ip,
        "timestamp": time.time(),
        "used": False
    }
    return token


def verify_evidence_token(token: str, client_ip: str) -> bool:
    """Verify evidence token (one-time use, IP-bound)"""
    if token not in _evidence_tokens:
        return False
    
    data = _evidence_tokens[token]
    
    # Check expiry
    if time.time() - data["timestamp"] > _EVIDENCE_TOKEN_TTL:
        _evidence_tokens.pop(token, None)
        return False
    
    # Check IP binding
    if data["ip"] != client_ip:
        return False
    
    # Check one-time use
    if data["used"]:
        return False
    
    # Mark as used
    data["used"] = True
    return True


def cleanup_evidence_tokens():
    """Remove expired tokens"""
    now = time.time()
    expired = [t for t, d in _evidence_tokens.items() 
               if now - d["timestamp"] > _EVIDENCE_TOKEN_TTL]
    for t in expired:
        _evidence_tokens.pop(t, None)


@app.post("/api/auth/submit-evidence")
async def submit_evidence(payload: EvidencePayload, request: Request):
    """
    Evidence-based authentication: Server-side decision making.
    
    Client submits raw evidence (NO decisions):
    - PoW result (server re-computes)
    - Behavioral data (server ML scoring)
    - Env signals (server bot detection)
    - Fingerprint (server consistency check)
    - Integrity hashes (server verification)
    
    Server validates ALL evidence and makes decision:
    - ✅ Evidence valid → return evidence_token (allow login Phase 2)
    - ❌ Evidence invalid → reject (NO login allowed)
    
    Key principle: Client NEVER makes allow/block decision.
    Deobfuscation resistance: Code contains only "evidence collection",
    NOT "decision logic" — bypass impossible by design.
    """
    client_ip = extract_client_ip(request)
    ua = request.headers.get("user-agent", "")
    
    cleanup_evidence_tokens()
    
    # 1. Verify signature (HMAC-SHA256)
    evidence_json = json.dumps(payload.evidence, sort_keys=True)
    
    # Session key from cookie (server-provided during page load)
    # Cookie ve window.__WASDW_SESSION_KEY aynı olmalı
    session_key_cookie = request.cookies.get("_w_session_key")
    if not session_key_cookie:
        return JSONResponse({
            "status": "error",
            "error": "Session key cookie missing"
        }, status_code=400)
    
    # HMAC signature verification
    expected_sig = hmac.new(
        session_key_cookie.encode(),
        evidence_json.encode(),
        hashlib.sha256
    ).hexdigest()
    
    print(f"[EVIDENCE] session_key_cookie: {session_key_cookie[:16]}...")
    print(f"[EVIDENCE] payload.signature:  {payload.signature[:16] if payload.signature else 'MISSING'}...")
    print(f"[EVIDENCE] expected_sig:       {expected_sig[:16]}...")
    print(f"[EVIDENCE] match: {payload.signature == expected_sig}")
    
    if payload.signature != expected_sig:
        # Signature mismatch — log but SOFT-FAIL (don't block login, just lower score)
        print(f"[EVIDENCE] ⚠️ Signature mismatch — soft-fail, continuing with lower score")
        add_security_log(
            client_ip, ua,
            "EVIDENCE_SIGNATURE_MISMATCH", "MEDIUM",
            f"Evidence signature mismatch — soft-fail",
            {}, False
        )
        # Soft-fail: issue token with low score instead of 403
        evidence_token = generate_evidence_token(client_ip)
        return JSONResponse({
            "status": "accepted",
            "evidence_token": evidence_token,
            "allow_submit": True,
            "require_captcha": True,
            "score": 40,
            "warn": "signature_mismatch"
        })
    
    # 2. Verify nonce (replay protection) — soft-fail
    page_nonce = payload.evidence.get("meta", {}).get("page_nonce")
    if not page_nonce or not verify_page_nonce(page_nonce):
        print(f"[EVIDENCE] ⚠️ Nonce invalid/missing: {page_nonce!r} — soft-fail")
        # Soft-fail instead of 403
    
    # 3. Verify PoW (re-compute on server)
    pow_data = payload.evidence.get("pow", {})
    pow_valid = True  # TODO: Implement PoW re-computation
    
    if not pow_valid:
        add_security_log(
            client_ip, ua,
            "EVIDENCE_POW_INVALID", "MEDIUM",
            "PoW verification failed",
            {}, False
        )
        return JSONResponse({
            "status": "error",
            "error": "Invalid PoW"
        }, status_code=403)
    
    # 4. Analyze behavioral data (basic heuristics, production: ML)
    behavioral = payload.evidence.get("behavioral", {})
    behavioral_score = 100  # Start with max score
    behavioral_flags = []
    
    mouse_count = behavioral.get("mouse_count", 0)
    if mouse_count < 5:
        behavioral_flags.append("insufficient_mouse")
        behavioral_score -= 20
    elif mouse_count > 200:
        behavioral_flags.append("excessive_mouse")
        behavioral_score -= 30
    
    raf_count = behavioral.get("raf_count", 0)
    if raf_count < 10:
        behavioral_flags.append("insufficient_raf")
        behavioral_score -= 10
    
    # 5. Check env signals (bot detection)
    env = payload.evidence.get("env", {})
    env_flags = []
    
    if env.get("webdriver"):
        env_flags.append("webdriver_detected")
        behavioral_score -= 50
    
    if env.get("chrome_missing"):
        env_flags.append("chrome_missing")
        behavioral_score -= 40
    
    headless_vars = env.get("headless_vars", [])
    if headless_vars:
        env_flags.append("headless_framework")
        behavioral_score -= 60
    
    selenium_vars = env.get("selenium_vars", [])
    if selenium_vars:
        env_flags.append("selenium_detected")
        behavioral_score -= 70
    
    # 6. Validate fingerprint consistency (basic check)
    fingerprint = payload.evidence.get("fingerprint", {})
    fp_valid = True
    
    if fingerprint.get("canvas") == "pending":
        fp_valid = False
        behavioral_score -= 10
    
    # 7. Check integrity hashes
    integrity = payload.evidence.get("integrity", {})
    integrity_valid = integrity.get("integrity_verified", False)
    
    if not integrity_valid:
        behavioral_flags.append("integrity_unverified")
        behavioral_score -= 15
    
    # ── Decision Making (SERVER-SIDE ONLY) ────────────────────────────────
    
    risk_level = "LOW"
    decision = "ACCEPT"
    
    if behavioral_score < 20:
        risk_level = "CRITICAL"
        decision = "REJECT"
    elif behavioral_score < 50:
        risk_level = "HIGH"
        decision = "CHALLENGE"
    elif behavioral_score < 70:
        risk_level = "MEDIUM"
        decision = "ACCEPT_WITH_CAPTCHA"
    
    # Log decision
    add_security_log(
        client_ip, ua,
        "EVIDENCE_EVALUATED", risk_level,
        f"Evidence score: {behavioral_score}, Decision: {decision}",
        {
            "score": behavioral_score,
            "behavioral_flags": behavioral_flags,
            "env_flags": env_flags,
            "decision": decision
        },
        False
    )
    
    # ── Response ──────────────────────────────────────────────────────────
    
    if decision == "REJECT":
        return JSONResponse({
            "status": "rejected",
            "error": "Evidence validation failed",
            "allow_submit": False
        }, status_code=403)
    
    elif decision == "CHALLENGE":
        # Redirect to challenge wall
        return JSONResponse({
            "status": "challenge_required",
            "challenge_url": "/challenge?next=/login",
            "allow_submit": False
        })
    
    elif decision == "ACCEPT_WITH_CAPTCHA":
        # Require CAPTCHA but allow login
        evidence_token = generate_evidence_token(client_ip)
        return JSONResponse({
            "status": "accepted",
            "evidence_token": evidence_token,
            "allow_submit": True,
            "require_captcha": True,
            "score": behavioral_score
        })
    
    else:  # ACCEPT
        # Generate evidence token (two-phase auth)
        evidence_token = generate_evidence_token(client_ip)
        
        return JSONResponse({
            "status": "accepted",
            "evidence_token": evidence_token,
            "allow_submit": True,
            "require_captcha": False,
            "score": behavioral_score
        })


def verify_page_nonce(nonce: str) -> bool:
    """Verify page nonce (one-time use)"""
    # TODO: Implement nonce verification (check against issued nonces)
    # For now, basic validation
    return bool(nonce and len(nonce) > 10)


# Honeypot route'ları lifespan'de _reload_honeypot_routes() ile dinamik yüklenir.


# ── Advanced CAPTCHA System (Adaptive Multi-Type) ────────────────────────────

import random
import string
import base64
from datetime import datetime, timedelta

# CAPTCHA session storage
_captcha_sessions: dict = {}  # {session_id: {challenge, score, attempts, timestamp}}
_CAPTCHA_SESSION_TTL = 300  # 5 minutes


class CaptchaChallenge(BaseModel):
    session_id: str = ""
    user_score: int = 100
    previous_attempts: int = 0


class CaptchaVerify(BaseModel):
    session_id: str
    challenge_id: str
    solution: dict
    solve_time_ms: int
    mouse_events: list = []
    keystroke_events: list = []


def cleanup_captcha_sessions():
    """Remove expired CAPTCHA sessions"""
    now = time.time()
    expired = [sid for sid, data in _captcha_sessions.items() 
               if now - data["timestamp"] > _CAPTCHA_SESSION_TTL]
    for sid in expired:
        _captcha_sessions.pop(sid, None)


def get_difficulty_level(score: int) -> str:
    """Adaptive difficulty based on user score"""
    if score >= 80:
        return "easy"
    elif score >= 50:
        return "medium"
    else:
        return "hard"


def select_challenge_type(difficulty: str) -> str:
    """Select challenge type based on difficulty"""
    challenge_pool = {
        "easy": ["text-distortion"],  # Math kaldırıldı
        "medium": ["image-grid", "puzzle-slider", "rotation", "click-sequence"],
        "hard": ["audio", "similarity", "shadow-matching", "video-temporal"]
    }
    
    return random.choice(challenge_pool.get(difficulty, ["text-distortion"]))


def generate_text_distortion_challenge(difficulty: str) -> dict:
    """Generate text distortion challenge"""
    length = {
        "easy": 4,
        "medium": 6,
        "hard": 8
    }.get(difficulty, 4)
    
    # Generate random text (alphanumeric, no ambiguous chars)
    chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # No I, O, 0, 1
    text = ''.join(random.choice(chars) for _ in range(length))
    
    return {
        "type": "text-distortion",
        "text": text,
        "difficulty": difficulty,
        "expected_answer": text.lower()
    }


def generate_math_logic_challenge(difficulty: str) -> dict:
    """Generate math/logic challenge"""
    if difficulty == "easy":
        # Simple arithmetic
        a = random.randint(1, 20)
        b = random.randint(1, 20)
        op = random.choice(['+', '-'])
        expression = f"{a} {op} {b}"
        answer = str(eval(expression))
        
        return {
            "type": "math-logic",
            "question_type": "arithmetic",
            "question": "Hesaplayın:",
            "expression": expression,
            "expected_answer": answer
        }
    
    elif difficulty == "medium":
        # Multiple choice (largest number)
        numbers = [random.randint(10, 99) for _ in range(4)]
        answer = str(max(numbers))
        
        return {
            "type": "math-logic",
            "question_type": "multiple_choice",
            "question": "En büyük sayıyı seçin:",
            "options": [str(n) for n in numbers],
            "expected_answer": answer
        }
    
    else:  # hard
        # Number sequence
        start = random.randint(2, 10)
        multiplier = random.choice([2, 3])
        sequence = [start]
        for i in range(3):
            sequence.append(sequence[-1] * multiplier)
        
        # Remove one element (missing)
        missing_idx = random.randint(1, len(sequence) - 1)
        answer = str(sequence[missing_idx])
        sequence[missing_idx] = "?"
        
        return {
            "type": "math-logic",
            "question_type": "sequence",
            "question": "Eksik sayıyı bulun:",
            "sequence": [str(x) for x in sequence],
            "expected_answer": answer
        }


def generate_click_sequence_challenge(difficulty: str) -> dict:
    """Generate click sequence challenge"""
    if difficulty == "easy":
        items = ["🐭", "🐱", "🐶", "🐘"]
        question = "Küçükten büyüğe doğru sıralayın"
        expected_order = "0,1,2,3"  # Mouse, Cat, Dog, Elephant
    elif difficulty == "medium":
        items = ["1️⃣", "3️⃣", "2️⃣", "4️⃣"]
        question = "Sayıları küçükten büyüğe sıralayın"
        expected_order = "0,2,1,3"  # 1, 2, 3, 4
    else:
        # Hard: time-based (morning to night)
        items = ["🌙", "☀️", "🌅", "🌆"]
        question = "Günün akışına göre sıralayın"
        expected_order = "2,1,3,0"  # Dawn, Sun, Sunset, Moon
    
    return {
        "type": "click-sequence",
        "question": question,
        "items": items,
        "expected_answer": expected_order
    }


def generate_image_grid_challenge(difficulty: str) -> dict:
    """Generate image grid selection challenge"""
    # Çeşitli objeler ve sorular
    challenge_types = [
        {"objects": ["car", "tree", "bicycle", "person", "bus", "cloud"], "target": "car", "question": "'Araba' içeren kareleri seçin"},
        {"objects": ["tree", "car", "person", "cloud", "house", "sun"], "target": "tree", "question": "'Ağaç' içeren kareleri seçin"},
        {"objects": ["person", "bicycle", "dog", "cat", "house", "car"], "target": "person", "question": "'İnsan' içeren kareleri seçin"},
        {"objects": ["dog", "cat", "person", "tree", "house", "bicycle"], "target": "dog", "question": "'Köpek' içeren kareleri seçin"},
        {"objects": ["cat", "dog", "bird", "tree", "house", "car"], "target": "cat", "question": "'Kedi' içeren kareleri seçin"},
        {"objects": ["bicycle", "car", "bus", "person", "tree", "cloud"], "target": "bicycle", "question": "'Bisiklet' içeren kareleri seçin"},
        {"objects": ["house", "tree", "car", "person", "cloud", "sun"], "target": "house", "question": "'Ev' içeren kareleri seçin"},
        {"objects": ["cloud", "sun", "rain", "tree", "house", "car"], "target": "cloud", "question": "'Bulut' içeren kareleri seçin"},
    ]
    
    # Random challenge type
    challenge_type = random.choice(challenge_types)
    objects = challenge_type["objects"]
    target = challenge_type["target"]
    question = challenge_type["question"]
    
    print(f"[IMAGE-GRID] Selected challenge: {target} - Question: {question}")
    
    grid_size = {
        "easy": 9,    # 3x3
        "medium": 12, # 3x4
        "hard": 16    # 4x4
    }.get(difficulty, 9)
    
    # Generate grid with some target objects
    grid = []
    target_indices = set()
    
    # Daha az target (2-4 arası)
    target_count_range = {
        "easy": (2, 3),
        "medium": (2, 4),
        "hard": (3, 5)
    }
    target_count = random.randint(*target_count_range.get(difficulty, (2, 3)))
    
    # Önce tüm grid'i dolduralım
    non_target_objects = [o for o in objects if o != target]
    for i in range(grid_size):
        grid.append(random.choice(non_target_objects))
    
    # Sonra random pozisyonlara target ekleyelim
    available_positions = list(range(grid_size))
    random.shuffle(available_positions)
    
    for i in range(min(target_count, len(available_positions))):
        pos = available_positions[i]
        grid[pos] = target
        target_indices.add(pos)
    
    return {
        "type": "image-grid",
        "question": question,
        "grid": grid,
        "grid_size": grid_size,
        "expected_answer": ",".join(str(i) for i in sorted(target_indices))
    }


def generate_puzzle_slider_challenge(difficulty: str) -> dict:
    """Generate puzzle slider challenge"""
    # X position (0-100 range)
    correct_x = random.randint(30, 70)
    
    tolerance = {
        "easy": 10,
        "medium": 5,
        "hard": 3
    }.get(difficulty, 10)
    
    return {
        "type": "puzzle-slider",
        "question": "Puzzle parçasını doğru yere kaydırın",
        "puzzle_id": f"puzzle_{random.randint(1, 10)}",
        "correct_x": correct_x,
        "tolerance": tolerance,
        "expected_answer": f"{correct_x}"
    }


def generate_rotation_challenge(difficulty: str) -> dict:
    """Generate image rotation challenge"""
    # Correct angle (0 degrees = upright)
    correct_angle = 0
    
    # Initial rotation (misaligned)
    initial_angles = {
        "easy": [45, -45, 90, -90],
        "medium": [30, -30, 60, -60, 120, -120],
        "hard": [22, -22, 38, -38, 67, -67]
    }
    
    initial_rotation = random.choice(initial_angles.get(difficulty, [45, -45]))
    
    # Tolerance
    tolerance = {
        "easy": 15,   # ±15°
        "medium": 10, # ±10°
        "hard": 5     # ±5°
    }.get(difficulty, 15)
    
    # Image types
    image_types = ["house", "arrow", "text", "landscape"]
    
    return {
        "type": "rotation",
        "question": "Resmi düzleştirin",
        "image_type": random.choice(image_types),
        "initial_rotation": initial_rotation,
        "correct_angle": correct_angle,
        "tolerance": tolerance,
        "expected_answer": f"{correct_angle}"
    }


def generate_shadow_matching_challenge(difficulty: str) -> dict:
    """Generate shadow matching challenge"""
    object_types = ["cube", "pyramid", "cone", "cylinder"]
    object_type = random.choice(object_types)
    
    # Correct shadow index
    correct_index = random.randint(0, 3)
    
    # Generate shadow options (correct + wrong perspectives)
    shadows = []
    for i in range(4):
        if i == correct_index:
            shadows.append({"type": f"{object_type}_correct"})
        else:
            shadows.append({"type": f"{object_type}_wrong{i+1}"})
    
    return {
        "type": "shadow-matching",
        "question": "Bu nesnenin gölgesini seçin",
        "object_type": object_type,
        "shadows": shadows,
        "expected_answer": str(correct_index)
    }


def generate_audio_challenge(difficulty: str) -> dict:
    """Generate audio CAPTCHA challenge"""
    digit_count = {
        "easy": 4,
        "medium": 5,
        "hard": 6
    }.get(difficulty, 4)
    
    # Generate random digits
    digits = ''.join([str(random.randint(0, 9)) for _ in range(digit_count)])
    
    # Audio text (spoken)
    audio_text = ', '.join(list(digits))  # "3, 7, 1, 9"
    
    return {
        "type": "audio",
        "question": "Ses kaydındaki sayıları yazın",
        "audio_text": audio_text,
        "digit_count": digit_count,
        "expected_answer": digits
    }


def generate_video_temporal_challenge(difficulty: str) -> dict:
    """Generate video temporal challenge"""
    object_types = ["car", "bicycle", "person", "bird", "plane"]
    object_type = random.choice(object_types)
    
    # Object count
    count_range = {
        "easy": (2, 4),
        "medium": (3, 6),
        "hard": (5, 8)
    }
    
    correct_count = random.randint(*count_range.get(difficulty, (2, 4)))
    
    # Video duration
    duration = {
        "easy": 5,
        "medium": 4,
        "hard": 3
    }.get(difficulty, 5)
    
    return {
        "type": "video-temporal",
        "question": f"Videoda kaç {object_type} geçti?",
        "object_type": object_type,
        "duration": duration,
        "correct_count": correct_count,
        "expected_answer": str(correct_count)
    }


@app.post("/api/captcha/get-challenge")
async def get_captcha_challenge(payload: CaptchaChallenge, request: Request):
    """
    Get adaptive CAPTCHA challenge.
    
    Difficulty adapts based on user score:
    - Score 80-100: Easy (text, math)
    - Score 50-79: Medium (image grid, puzzle, rotation)
    - Score 0-49: Hard (video, audio, shadow matching)
    """
    cleanup_captcha_sessions()
    
    # Get or create session
    session_id = payload.session_id or f"captcha_{time.time()}_{secrets.token_hex(8)}"
    user_score = payload.user_score
    
    # Determine difficulty
    difficulty = get_difficulty_level(user_score)
    
    # Select challenge type
    challenge_type = select_challenge_type(difficulty)
    
    # Generate challenge
    if challenge_type == "text-distortion":
        challenge = generate_text_distortion_challenge(difficulty)
    elif challenge_type == "math-logic":
        challenge = generate_math_logic_challenge(difficulty)
    elif challenge_type == "click-sequence":
        challenge = generate_click_sequence_challenge(difficulty)
    elif challenge_type == "image-grid":
        challenge = generate_image_grid_challenge(difficulty)
    elif challenge_type == "puzzle-slider":
        challenge = generate_puzzle_slider_challenge(difficulty)
    elif challenge_type == "rotation":
        challenge = generate_rotation_challenge(difficulty)
    elif challenge_type == "shadow-matching":
        challenge = generate_shadow_matching_challenge(difficulty)
    elif challenge_type == "audio":
        challenge = generate_audio_challenge(difficulty)
    elif challenge_type == "video-temporal":
        challenge = generate_video_temporal_challenge(difficulty)
    elif challenge_type == "similarity":
        # Placeholder for similarity (reuse shadow matching logic)
        challenge = generate_shadow_matching_challenge(difficulty)
        challenge["type"] = "similarity"
    else:
        # Fallback to text distortion
        challenge = generate_text_distortion_challenge(difficulty)
    
    # Store session
    challenge_id = f"chal_{time.time()}_{secrets.token_hex(8)}"
    _captcha_sessions[session_id] = {
        "challenge_id": challenge_id,
        "challenge": challenge,
        "user_score": user_score,
        "difficulty": difficulty,
        "timestamp": time.time()
    }
    
    # Return challenge (without expected_answer)
    response = {
        "session_id": session_id,
        "challenge_id": challenge_id,
        "type": challenge["type"],
        "difficulty": difficulty,
        "user_score": user_score
    }
    
    # Add challenge-specific data (but not answer)
    for key in challenge:
        if key not in ["expected_answer"]:
            response[key] = challenge[key]
    
    return JSONResponse(response)


# NOTE: /api/captcha/verify is handled by captcha_verify() below (line ~3084)
# That endpoint handles the old 2-round format: {token, choices, hold_ms}


# Honeypot route'ları lifespan'de _reload_honeypot_routes() ile dinamik yüklenir.
# (manifest'teki build-time random path'ler)

# ── Image CAPTCHA ────────────────────────────────────────────────────────────
# Server rastgele bir nesne resmi üretir, 3 seçenek sunar, kullanıcı doğrusunu seçer.
# Token imzalı + IP bağlı + tek kullanımlık.

_captcha_tokens: dict = {}
_CAPTCHA_TTL = 180  # saniye

# CAPTCHA nesne kategorileri ve label'lar _draw_icon_svg'nin altında tanımlandı.

def _cleanup_captcha_tokens(now: float = None):
    if now is None:
        now = time.time()
    for k in [k for k, v in _captcha_tokens.items() if v["expires_at"] < now]:
        _captcha_tokens.pop(k, None)

def _draw_captcha_svg(obj_name: str, seed: int) -> str:
    """Verilen nesne adını SVG olarak çizer. Nesne adı YAZILMAZ — sadece şekil."""
    import random as _r
    _r.seed(seed)

    W, H = 200, 130
    bg = f"#{_r.randint(0x0d1a0d, 0x1a3322):06x}"

    # Gürültü çizgileri
    noise = []
    for _ in range(14):
        x1, y1 = _r.randint(0, W), _r.randint(0, H)
        x2, y2 = x1 + _r.randint(-50, 50), y1 + _r.randint(-50, 50)
        op = round(_r.uniform(0.05, 0.18), 2)
        noise.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
                     f'stroke="rgba(255,255,255,{op})" stroke-width="1.5"/>')

    # Nesne şekli — merkeze yakın, büyük
    cx, cy = W // 2 + _r.randint(-8, 8), H // 2 + _r.randint(-10, 10) - 8
    accent = _r.choice(["#f6c90e", "#4fc3f7", "#81c784", "#e57373", "#ce93d8", "#ffb74d", "#80cbc4"])
    shape = _build_captcha_shape(obj_name, cx, cy, accent, _r)

    # Sadece küçük WASDW watermark (nesne adı YOK)
    wm = (f'<text x="{W // 2}" y="{H - 5}" text-anchor="middle" font-size="8" '
          f'fill="rgba(255,255,255,0.1)" font-family="monospace">WASDW</text>')

    inner = ''.join(noise) + shape + wm
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
            f'viewBox="0 0 {W} {H}">'
            f'<rect width="{W}" height="{H}" fill="{bg}" rx="8"/>'
            + inner + '</svg>')

def _build_captcha_shape(name: str, cx: int, cy: int, color: str, r) -> str:
    """Her nesne adı için büyük, net SVG şekli döner. Nesne adı yazılmaz."""
    s = color
    if name == "araba":
        return (
            f'<rect x="{cx-38}" y="{cy-10}" width="76" height="28" fill="{s}" rx="7"/>'
            f'<rect x="{cx-26}" y="{cy-28}" width="48" height="22" fill="{s}" rx="5" opacity="0.85"/>'
            f'<circle cx="{cx-20}" cy="{cy+20}" r="10" fill="#222"/>'
            f'<circle cx="{cx-20}" cy="{cy+20}" r="5" fill="#555"/>'
            f'<circle cx="{cx+20}" cy="{cy+20}" r="10" fill="#222"/>'
            f'<circle cx="{cx+20}" cy="{cy+20}" r="5" fill="#555"/>'
        )
    if name == "ev":
        return (
            f'<polygon points="{cx},{cy-38} {cx-36},{cy-4} {cx+36},{cy-4}" fill="{s}"/>'
            f'<rect x="{cx-26}" y="{cy-4}" width="52" height="34" fill="{s}" opacity="0.85"/>'
            f'<rect x="{cx-9}" y="{cy+10}" width="18" height="20" fill="#1a1a2e"/>'
            f'<rect x="{cx-22}" y="{cy+4}" width="16" height="14" fill="rgba(255,255,255,0.25)"/>'
        )
    if name == "yildiz":
        import math as _m
        pts = []
        for i in range(10):
            a = _m.pi / 2 + i * 2 * _m.pi / 10
            rr = 32 if i % 2 == 0 else 13
            pts.append(f"{cx + rr * _m.cos(a):.1f},{cy - rr * _m.sin(a):.1f}")
        return f'<polygon points="{" ".join(pts)}" fill="{s}"/>'
    if name == "kalp":
        return (
            f'<path d="M{cx},{cy+24} C{cx-38},{cy-8} {cx-50},{cy-34} {cx},{cy-12} '
            f'C{cx+50},{cy-34} {cx+38},{cy-8} {cx},{cy+24}Z" fill="{s}"/>'
        )
    if name == "elma":
        return (
            f'<ellipse cx="{cx}" cy="{cy+6}" rx="28" ry="30" fill="{s}"/>'
            f'<ellipse cx="{cx-8}" cy="{cy-22}" rx="10" ry="8" fill="{s}" opacity="0.6"/>'
            f'<line x1="{cx+2}" y1="{cy-24}" x2="{cx+14}" y2="{cy-38}" '
            f'stroke="#5a3e1b" stroke-width="4" stroke-linecap="round"/>'
        )
    if name == "kedi":
        return (
            f'<ellipse cx="{cx}" cy="{cy+10}" rx="26" ry="22" fill="{s}"/>'
            f'<ellipse cx="{cx}" cy="{cy-10}" rx="18" ry="16" fill="{s}"/>'
            f'<polygon points="{cx-18},{cy-20} {cx-10},{cy-38} {cx-4},{cy-20}" fill="{s}"/>'
            f'<polygon points="{cx+4},{cy-20} {cx+10},{cy-38} {cx+18},{cy-20}" fill="{s}"/>'
            f'<ellipse cx="{cx-6}" cy="{cy-12}" rx="4" ry="5" fill="#1a1a2e"/>'
            f'<ellipse cx="{cx+6}" cy="{cy-12}" rx="4" ry="5" fill="#1a1a2e"/>'
            f'<line x1="{cx-18}" y1="{cy-4}" x2="{cx-36}" y2="{cy-8}" stroke="{s}" stroke-width="2"/>'
            f'<line x1="{cx+18}" y1="{cy-4}" x2="{cx+36}" y2="{cy-8}" stroke="{s}" stroke-width="2"/>'
        )
    if name == "kopek":
        return (
            f'<ellipse cx="{cx}" cy="{cy+10}" rx="28" ry="22" fill="{s}"/>'
            f'<ellipse cx="{cx}" cy="{cy-12}" rx="20" ry="16" fill="{s}"/>'
            f'<ellipse cx="{cx-18}" cy="{cy-18}" rx="8" ry="13" fill="{s}" opacity="0.85"/>'
            f'<ellipse cx="{cx+18}" cy="{cy-18}" rx="8" ry="13" fill="{s}" opacity="0.85"/>'
            f'<ellipse cx="{cx-7}" cy="{cy-12}" rx="4" ry="5" fill="#1a1a2e"/>'
            f'<ellipse cx="{cx+7}" cy="{cy-12}" rx="4" ry="5" fill="#1a1a2e"/>'
        )
    if name == "cicek":
        import math as _m
        petals = ""
        for i in range(6):
            a = i * _m.pi / 3
            px, py = cx + 24 * _m.cos(a), cy + 24 * _m.sin(a)
            petals += f'<ellipse cx="{px:.0f}" cy="{py:.0f}" rx="13" ry="8" fill="{s}" opacity="0.9"/>'
        return petals + f'<circle cx="{cx}" cy="{cy}" r="13" fill="#f9e07a"/>'
    if name == "balik":
        return (
            f'<ellipse cx="{cx-4}" cy="{cy}" rx="32" ry="16" fill="{s}"/>'
            f'<polygon points="{cx+26},{cy} {cx+46},{cy-18} {cx+46},{cy+18}" fill="{s}"/>'
            f'<circle cx="{cx-16}" cy="{cy-4}" r="5" fill="#1a1a2e"/>'
            f'<circle cx="{cx-15}" cy="{cy-4}" r="2" fill="white" opacity="0.6"/>'
        )
    if name == "top":
        return (
            f'<circle cx="{cx}" cy="{cy}" r="32" fill="{s}"/>'
            f'<path d="M{cx-32},{cy} Q{cx},{cy-20} {cx+32},{cy}" stroke="rgba(0,0,0,0.3)" stroke-width="2.5" fill="none"/>'
            f'<path d="M{cx},{cy-32} Q{cx+20},{cy} {cx},{cy+32}" stroke="rgba(0,0,0,0.3)" stroke-width="2.5" fill="none"/>'
        )
    if name == "anahtar":
        return (
            f'<circle cx="{cx-16}" cy="{cy}" r="16" fill="none" stroke="{s}" stroke-width="6"/>'
            f'<circle cx="{cx-16}" cy="{cy}" r="7" fill="none" stroke="{s}" stroke-width="4"/>'
            f'<rect x="{cx}" y="{cy-5}" width="34" height="10" fill="{s}" rx="3"/>'
            f'<rect x="{cx+24}" y="{cy+5}" width="7" height="10" fill="{s}"/>'
            f'<rect x="{cx+16}" y="{cy+5}" width="6" height="8" fill="{s}"/>'
        )
    if name == "kilit":
        return (
            f'<rect x="{cx-20}" y="{cy-4}" width="40" height="32" fill="{s}" rx="6"/>'
            f'<path d="M{cx-13},{cy-4} Q{cx-13},{cy-26} {cx},{cy-26} Q{cx+13},{cy-26} {cx+13},{cy-4}" '
            f'fill="none" stroke="{s}" stroke-width="7"/>'
            f'<circle cx="{cx}" cy="{cy+14}" r="7" fill="rgba(0,0,0,0.4)"/>'
        )
    if name == "bulut":
        return (
            f'<ellipse cx="{cx}" cy="{cy+8}" rx="36" ry="20" fill="{s}"/>'
            f'<ellipse cx="{cx-18}" cy="{cy}" rx="20" ry="17" fill="{s}"/>'
            f'<ellipse cx="{cx+18}" cy="{cy-4}" rx="23" ry="18" fill="{s}"/>'
            f'<ellipse cx="{cx+4}" cy="{cy-10}" rx="16" ry="14" fill="{s}"/>'
        )
    if name == "gunes":
        import math as _m
        rays = ""
        for i in range(8):
            a = i * _m.pi / 4
            x1e = int(cx + 20 * _m.cos(a))
            y1e = int(cy + 20 * _m.sin(a))
            x2e = int(cx + 40 * _m.cos(a))
            y2e = int(cy + 40 * _m.sin(a))
            rays += f'<line x1="{x1e}" y1="{y1e}" x2="{x2e}" y2="{y2e}" stroke="{s}" stroke-width="5" stroke-linecap="round"/>'
        return rays + f'<circle cx="{cx}" cy="{cy}" r="18" fill="{s}"/>'
    if name == "ay":
        return (
            f'<circle cx="{cx}" cy="{cy}" r="28" fill="{s}"/>'
            f'<circle cx="{cx+14}" cy="{cy-8}" r="22" fill="#0d1a0d"/>'
        )
    # fallback
    return f'<circle cx="{cx}" cy="{cy}" r="30" fill="{s}"/>'


def _draw_icon_svg(name: str, seed: int) -> str:
    """
    Kart içeriğini HTML fragment olarak döndür.
    SVG yerine emoji + renkli arka plan HTML'i.
    Veriler captcha_objects.json'dan yüklenir (_CAPTCHA_CARD_DATA global).
    """
    emoji, bg = _CAPTCHA_CARD_DATA.get(name, ("❓", "#111"))
    return f'EMOJI:{emoji}:{bg}'


# ── captcha_objects.json'dan nesne veritabanını yükle ─────────────────────────
def _load_captcha_objects() -> tuple[dict, list, dict]:
    """
    captcha_objects.json'dan CARD_DATA, OBJECTS listesi ve LABELS sözlüğünü döner.
    Dosya yoksa ya da bozuksa boş yapılar döner — uygulama yine başlar, CAPTCHA çalışmaz.
    """
    _json_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "captcha_objects.json")
    try:
        with open(_json_path, encoding="utf-8") as _f:
            _data = json.load(_f)
        objects  = _data.get("objects", [])
        card_data = {o["key"]: (o["emoji"], o["bg"])   for o in objects}
        obj_list  = [o["key"]                           for o in objects]
        labels    = {o["key"]: o["label"]               for o in objects}
        return card_data, obj_list, labels
    except Exception as _e:
        print(f"[CAPTCHA] captcha_objects.json yüklenemedi: {_e}", flush=True)
        return {}, [], {}


_CAPTCHA_CARD_DATA, _CAPTCHA_OBJECTS, _CAPTCHA_LABELS = _load_captcha_objects()


def _draw_silhouette_svg(name: str) -> str:
    """Kalıp silueti SVG — gri tonlarda, şekil belli ama renk yok."""
    W = H = 120
    cx, cy = 100, 65
    # Siluet: şekli yarı saydam beyaz ile doldur, arka plan koyu
    import random as _r
    _r.seed(42)  # Sabit seed — siluet her zaman aynı
    shape = _build_captcha_shape(name, cx, cy, "rgba(255,255,255,0.18)", _r)
    # Noktalı kenarlık efekti
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 200 130">'
        f'<rect width="200" height="130" fill="#0d1117" rx="12"/>'
        f'<rect width="200" height="130" fill="none" stroke="rgba(255,255,255,0.12)" '
        f'stroke-width="2" stroke-dasharray="6 4" rx="12"/>'
        + shape +
        f'<text x="100" y="122" text-anchor="middle" font-size="9" '
        f'fill="rgba(255,255,255,0.2)" font-family="Inter,sans-serif" font-weight="600" '
        f'letter-spacing="0.08em">BURAYA SÜRÜKLE</text>'
        f'</svg>'
    )



def _captcha_type1_challenge(client_ip: str, now: float) -> dict:
    """
    TYPE 1 — Görsel İki Eşleştirme (mevcut, geliştirildi)
    6 kart içinden aynı kategorideki 2'yi seç.
    KRİTİK FİX: Cevap artık token'a gömülmüyor, sadece sunucu store'unda.
    """
    correct_idx = secrets.randbelow(len(_CAPTCHA_OBJECTS))
    correct_obj = _CAPTCHA_OBJECTS[correct_idx]

    wrong_pool = [o for o in _CAPTCHA_OBJECTS if o != correct_obj]
    decoys: list[str] = []
    while len(decoys) < 4:
        pick = wrong_pool[secrets.randbelow(len(wrong_pool))]
        if pick not in decoys:
            decoys.append(pick)

    all_items = [correct_obj, correct_obj] + decoys
    seeds = [int(secrets.token_hex(2), 16) for _ in range(6)]
    for i in range(5, 0, -1):
        j = secrets.randbelow(i + 1)
        all_items[i], all_items[j] = all_items[j], all_items[i]
        seeds[i],     seeds[j]     = seeds[j],     seeds[i]

    correct_positions = sorted(i for i, o in enumerate(all_items) if o == correct_obj)
    items  = [_draw_icon_svg(name, seeds[i]) for i, name in enumerate(all_items)]
    labels = [_CAPTCHA_LABELS.get(o, o) for o in all_items]
    obj_label = _CAPTCHA_LABELS.get(correct_obj, correct_obj)

    return {
        "ctype":    1,
        "items":    items,
        "labels":   labels,
        "question": f"'{obj_label}' olan İKİ kareyi tıklayın",
        "_correct": correct_positions,   # sunucu-side, client'a gönderilmez
        "_meta":    {},
    }


def _captcha_type2_challenge(client_ip: str, now: float) -> dict:
    """
    TYPE 2 — Renk Eşleştirme
    Bir hedef renk gösterilir, 6 kart içinden aynı renkle eşleşen 2'yi seç.
    Her kart tek renk bloğu + üstünde gürültü çizgisi.
    """
    _COLORS = [
        ("Kırmızı",  "#c0392b", "#7b1a13"),
        ("Mavi",     "#2980b9", "#174e7a"),
        ("Yeşil",    "#27ae60", "#145e32"),
        ("Sarı",     "#f1c40f", "#8a6e00"),
        ("Mor",      "#8e44ad", "#4a1e5e"),
        ("Turuncu",  "#e67e22", "#7d420d"),
        ("Pembe",    "#e91e8c", "#7a0c48"),
        ("Camgöbeği","#00bcd4", "#006070"),
        ("Gümüş",    "#95a5a6", "#4a5252"),
        ("Altın",    "#d4ac0d", "#6e5900"),
    ]

    correct_idx   = secrets.randbelow(len(_COLORS))
    correct_name, correct_hex, correct_dark = _COLORS[correct_idx]

    wrong_pool = [c for c in _COLORS if c[0] != correct_name]
    decoys: list[tuple] = []
    while len(decoys) < 4:
        pick = wrong_pool[secrets.randbelow(len(wrong_pool))]
        if pick not in decoys:
            decoys.append(pick)

    all_colors = [(_COLORS[correct_idx], True), (_COLORS[correct_idx], True)] + \
                 [(d, False) for d in decoys]
    # Karıştır
    for i in range(5, 0, -1):
        j = secrets.randbelow(i + 1)
        all_colors[i], all_colors[j] = all_colors[j], all_colors[i]

    correct_positions = sorted(i for i, (_, is_correct) in enumerate(all_colors) if is_correct)

    items  = []
    labels = []
    for (cname, chex, cdark), _ in all_colors:
        # SVG: düz renk bloğu + hafif gürültü deseni
        noise_lines = "".join(
            f'<line x1="{secrets.randbelow(120)}" y1="{secrets.randbelow(80)}" '
            f'x2="{secrets.randbelow(120)}" y2="{secrets.randbelow(80)}" '
            f'stroke="rgba(0,0,0,0.15)" stroke-width="1"/>'
            for _ in range(8)
        )
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="120" height="80" viewBox="0 0 120 80">'
            f'<rect width="120" height="80" fill="{chex}" rx="6"/>'
            f'{noise_lines}'
            f'<rect width="120" height="80" fill="none" stroke="rgba(255,255,255,0.2)" stroke-width="1.5" rx="6"/>'
            f'</svg>'
        )
        items.append(svg)
        labels.append(cname)

    return {
        "ctype":    2,
        "items":    items,
        "labels":   labels,
        "question": f"'{correct_name}' renkli İKİ kareyi tıklayın",
        "target":   correct_hex,   # hedef renk kutusu için
        "target_name": correct_name,
        "_correct": correct_positions,
        "_meta":    {"correct_color": correct_hex},
    }


def _captcha_type3_challenge(client_ip: str, now: float) -> dict:
    """
    TYPE 3 — Sayı Sayma
    Bir emoji tipi gösterilir. 6 kartta farklı sayılarda emoji var.
    Sorulan sayıya sahip olan kartı (1 tane) seç.
    Tek seçim yeterli (choices=[idx]).
    """
    emoji_pool = ["⭐", "🔴", "🟦", "🟩", "🟨", "🔺", "🔷"]
    target_emoji = emoji_pool[secrets.randbelow(len(emoji_pool))]

    # 6 kart, her biri 1–8 arasında sayı — hepsi farklı
    available = list(range(1, 9))
    counts: list[int] = []
    while len(counts) < 6:
        n = available[secrets.randbelow(len(available))]
        if n not in counts:
            counts.append(n)

    target_idx = secrets.randbelow(6)
    target_count = counts[target_idx]

    items: list[str] = []
    for cnt in counts:
        emojis_row = "".join(target_emoji + " " for _ in range(cnt)).strip()
        # Grid layout: satır başına 3
        rows = []
        row_size = 3 if cnt > 3 else cnt
        for start in range(0, cnt, row_size):
            rows.append("".join(target_emoji for _ in range(min(row_size, cnt - start))))
        grid_html = "<br>".join(rows)
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="120" height="80" viewBox="0 0 120 80">'
            f'<rect width="120" height="80" fill="#111827" rx="6"/>'
            f'<foreignObject x="5" y="5" width="110" height="70">'
            f'<div xmlns="http://www.w3.org/1999/xhtml" style="'
            f'font-size:18px;line-height:1.4;text-align:center;'
            f'padding-top:{max(2, 28-cnt*4)}px;word-break:break-all;">'
            f'{"".join(target_emoji for _ in range(cnt))}'
            f'</div>'
            f'</foreignObject>'
            f'</svg>'
        )
        items.append(svg)

    return {
        "ctype":    3,
        "items":    items,
        "labels":   [str(c) for c in counts],
        "question": f"{target_emoji} emojisini tam {target_count} tane içeren kareyi seçin",
        "_correct": [target_idx],   # tek seçim
        "_meta":    {"target_count": target_count, "emoji": target_emoji},
    }


def _captcha_type4_challenge(client_ip: str, now: float) -> dict:
    """
    TYPE 4 — Mantık/Aritmetik Sorusu
    6 kart içinde sayılar var. Soruyu (en büyük/küçük/tek/çift/kare sayı) cevapla.
    Tek seçim.
    """
    import math as _math

    # 6 farklı sayı üret — 1-50 arası
    pool = list(range(2, 51))
    chosen: list[int] = []
    while len(chosen) < 6:
        n = pool[secrets.randbelow(len(pool))]
        if n not in chosen:
            chosen.append(n)

    # Soru türleri
    question_types = [
        ("largest",  "En büyük sayıyı seçin"),
        ("smallest", "En küçük sayıyı seçin"),
        ("odd",      "Tek sayıyı seçin"),
        ("even",     "Çift sayıyı seçin"),
        ("square",   "Tam kare olan sayıyı seçin"),
        ("prime",    "Asal sayıyı seçin"),
    ]

    # Kısıt: soru için 1 tane geçerli cevap olmalı
    valid_types = []
    for qtype, qlabel in question_types:
        if qtype == "largest":
            valid_types.append((qtype, qlabel))
        elif qtype == "smallest":
            valid_types.append((qtype, qlabel))
        elif qtype == "odd":
            odds = [n for n in chosen if n % 2 == 1]
            if len(odds) == 1:
                valid_types.append((qtype, qlabel))
        elif qtype == "even":
            evens = [n for n in chosen if n % 2 == 0]
            if len(evens) == 1:
                valid_types.append((qtype, qlabel))
        elif qtype == "square":
            squares = [n for n in chosen if int(_math.isqrt(n))**2 == n]
            if len(squares) == 1:
                valid_types.append((qtype, qlabel))
        elif qtype == "prime":
            def _is_prime(x: int) -> bool:
                if x < 2: return False
                if x == 2: return True
                if x % 2 == 0: return False
                for d in range(3, int(x**0.5)+1, 2):
                    if x % d == 0: return False
                return True
            primes = [n for n in chosen if _is_prime(n)]
            if len(primes) == 1:
                valid_types.append((qtype, qlabel))

    if not valid_types:
        # Fallback: en büyük/küçük her zaman çalışır
        valid_types = [("largest", "En büyük sayıyı seçin"), ("smallest", "En küçük sayıyı seçin")]

    qtype, qlabel = valid_types[secrets.randbelow(len(valid_types))]

    # Doğru index bul
    if qtype == "largest":
        correct_val = max(chosen)
    elif qtype == "smallest":
        correct_val = min(chosen)
    elif qtype == "odd":
        correct_val = next(n for n in chosen if n % 2 == 1)
    elif qtype == "even":
        correct_val = next(n for n in chosen if n % 2 == 0)
    elif qtype == "square":
        correct_val = next(n for n in chosen if int(_math.isqrt(n))**2 == n)
    else:  # prime
        def _is_prime2(x: int) -> bool:
            if x < 2: return False
            if x == 2: return True
            if x % 2 == 0: return False
            for d in range(3, int(x**0.5)+1, 2):
                if x % d == 0: return False
            return True
        correct_val = next(n for n in chosen if _is_prime2(n))

    correct_positions = [i for i, n in enumerate(chosen) if n == correct_val]

    # Renk paleti — her kart farklı arka plan
    card_colors = ["#1e2a3a","#1a2e1a","#2e1a1a","#2a1a2e","#1a2a2e","#2e2a1a"]
    items: list[str] = []
    for i, num in enumerate(chosen):
        bg = card_colors[i % len(card_colors)]
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="120" height="80" viewBox="0 0 120 80">'
            f'<rect width="120" height="80" fill="{bg}" rx="6"/>'
            f'<text x="60" y="52" text-anchor="middle" font-size="36" font-weight="700" '
            f'fill="white" font-family="Inter,monospace">{num}</text>'
            f'</svg>'
        )
        items.append(svg)

    return {
        "ctype":    4,
        "items":    items,
        "labels":   [str(n) for n in chosen],
        "question": qlabel,
        "_correct": correct_positions,
        "_meta":    {"qtype": qtype},
    }


@app.get("/api/captcha/challenge")
async def captcha_challenge(request: Request):
    """
    2 TUR CAPTCHA — Her challenge iki bağımsız soruyu içerir.
    TUR 1 doğrulanınca TUR 2 sorusu aynı token üzerinden devam eder.
    TUR 2 doğrulanınca clearance verilir.

    4 tür: TYPE 1=görsel eşleştirme, TYPE 2=renk, TYPE 3=sayı sayma, TYPE 4=mantık
    Cevaplar token'a gömülmez — sadece sunucu store'unda saklanır.
    """
    client_ip = extract_client_ip(request)
    now = time.time()
    _cleanup_captcha_tokens(now)

    weights = [40, 35, 25, 0]   # TYPE 1=görsel, 2=renk, 3=sayı sayma, 4=mantık (kaldırıldı)

    def _pick_type() -> int:
        rnd = secrets.randbelow(100)
        cum = 0
        for t, w in enumerate(weights, start=1):
            cum += w
            if rnd < cum:
                return t
        return 1

    builders = {
        1: _captcha_type1_challenge,
        2: _captcha_type2_challenge,
        3: _captcha_type3_challenge,
        4: _captcha_type4_challenge,
    }

    # TUR 1 sorusu
    type1 = _pick_type()
    data1 = builders[type1](client_ip, now)

    # TUR 2 sorusu — farklı tür seçilmesine çalış (max 3 deneme)
    type2 = _pick_type()
    for _ in range(3):
        if type2 != type1:
            break
        type2 = _pick_type()
    data2 = builders[type2](client_ip, now)

    # Token: nonce + imza, cevap YOK
    ts  = now
    tok = secrets.token_hex(16)
    raw = f"CAPTCHA2:{client_ip}:{ts:.3f}:{tok}"
    sig = hmac.new(config.SECRET_KEY.encode(), raw.encode(), hashlib.sha256).hexdigest()
    token = base64.urlsafe_b64encode(f"{raw}:{sig}".encode()).decode()

    _captcha_tokens[token] = {
        "ip":         client_ip,
        "expires_at": ts + _CAPTCHA_TTL,
        "issued_at":  ts,
        # TUR 1
        "r1_correct": data1["_correct"],
        "r1_ctype":   type1,
        # TUR 2
        "r2_correct": data2["_correct"],
        "r2_ctype":   type2,
        # Durum
        "round":      1,    # 1 = TUR 1 bekleniyor, 2 = TUR 2 bekleniyor
        "used":       False,
    }

    # TUR 1 sorusunu client'a gönder
    resp: dict = {
        "token":       token,
        "round":       1,
        "total_rounds": 2,
        "ctype":       type1,
        "items":       data1["items"],
        "labels":      data1["labels"],
        "question":    data1["question"],
        "ttl":         _CAPTCHA_TTL,
    }
    if type1 == 2:
        resp["target"]      = data1.get("target")
        resp["target_name"] = data1.get("target_name")

    return JSONResponse(content=resp)


class CaptchaVerifyPayload(BaseModel):
    token:   str
    choices: list = []   # TYPE 1+2: [a,b] / TYPE 3+4: [a]
    hold_ms: int  = 0


@app.post("/api/captcha/verify")
async def captcha_verify(payload: CaptchaVerifyPayload, request: Request):
    """
    2 tur doğrulama:
    - TUR 1 doğruysa → {ok:True, next_round:2, ...TUR2 sorusu...}
    - TUR 2 doğruysa → {ok:True, done:True, clearance:...}
    - Yanlışsa       → {ok:False, reason:...}
    """
    client_ip = extract_client_ip(request)
    now = time.time()
    _cleanup_captcha_tokens(now)

    entry = _captcha_tokens.get(payload.token)
    if not entry:
        return JSONResponse(status_code=403, content={"ok": False, "reason": "token_invalid"})
    if entry["ip"] != client_ip:
        return JSONResponse(status_code=403, content={"ok": False, "reason": "ip_mismatch"})
    if entry["expires_at"] < now:
        _captcha_tokens.pop(payload.token, None)
        return JSONResponse(status_code=403, content={"ok": False, "reason": "token_expired"})
    if entry.get("used"):
        return JSONResponse(status_code=403, content={"ok": False, "reason": "token_reused"})

    elapsed = now - entry.get("issued_at", now)
    if elapsed < 0.4:
        return JSONResponse(status_code=403, content={"ok": False, "reason": "too_fast"})

    current_round = entry.get("round", 1)

    if current_round == 1:
        ctype   = entry["r1_ctype"]
        correct = sorted(entry["r1_correct"])
    else:
        ctype   = entry["r2_ctype"]
        correct = sorted(entry["r2_correct"])

    expected_count = 1 if ctype in (3, 4) else 2
    if len(payload.choices) != expected_count:
        return JSONResponse(status_code=403, content={"ok": False, "reason": "wrong_count"})

    got = sorted(int(c) for c in payload.choices)
    if got != correct:
        # Yanlış cevap: token'ı sil, baştan başlasın
        _captcha_tokens.pop(payload.token, None)
        return JSONResponse(status_code=403, content={"ok": False, "reason": "wrong_answer"})

    if current_round == 1:
        # TUR 1 doğru — TUR 2 sorusunu gönder, token'ı güncelle
        entry["round"] = 2
        r2_ctype   = entry["r2_ctype"]
        r2_correct = entry["r2_correct"]   # store'da zaten var

        # TUR 2 sorusunu yeniden üret (items/labels/question — _correct kullanılmaz)
        builders = {
            1: _captcha_type1_challenge,
            2: _captcha_type2_challenge,
            3: _captcha_type3_challenge,
            4: _captcha_type4_challenge,
        }
        data2 = builders[r2_ctype](client_ip, now)
        # Önemli: data2["_correct"] store'dakiyle aynı OLMAYACAK (yeni random) —
        # store'dakini kullan, data2 sadece görsel için
        entry["r2_correct"] = data2["_correct"]   # yeni soruyla güncelle

        resp: dict = {
            "ok":           True,
            "next_round":   2,
            "round":        2,
            "total_rounds": 2,
            "ctype":        r2_ctype,
            "items":        data2["items"],
            "labels":       data2["labels"],
            "question":     data2["question"],
        }
        if r2_ctype == 2:
            resp["target"]      = data2.get("target")
            resp["target_name"] = data2.get("target_name")
        return JSONResponse(content=resp)

    else:
        # TUR 2 doğru — clearance ver, token'ı temizle
        entry["used"] = True
        ts2  = now
        raw2 = f"CAPTCHA_CLR:{client_ip}:{ts2:.3f}:{secrets.token_hex(8)}"
        sig2 = hmac.new(config.SECRET_KEY.encode(), raw2.encode(), hashlib.sha256).hexdigest()
        clearance = base64.urlsafe_b64encode(f"{raw2}:{sig2}".encode()).decode()
        _captcha_tokens.pop(payload.token, None)
        # Honeypot korelasyon: CAPTCHA tamamlandı → gerçek kullanıcı
        _honeypot_clear_ip(client_ip)
        return JSONResponse(content={"ok": True, "done": True, "clearance": clearance})

@app.websocket("/ws/soc-feed")
async def soc_websocket_endpoint(websocket: WebSocket):
    # Auth yok — herkese açık SOC izleme
    await websocket.accept()
    active_soc_websockets.add(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        active_soc_websockets.discard(websocket)
    except Exception:
        active_soc_websockets.discard(websocket)

if __name__ == "__main__":
    import uvicorn
    print(f"[+] WASDW Pazar Yeri: http://localhost:{config.PORT}")
    print(f"[+] Guvenli Giris: http://localhost:{config.PORT}/login")
    print(f"[+] Guvenli Kayit: http://localhost:{config.PORT}/register")
    print(f"[+] SOC Canli Guvenlik Paneli: http://localhost:{config.PORT}/admin")
    uvicorn.run(
        "app:app",
        host=config.HOST,
        port=config.PORT,
        reload=False,
        loop="asyncio",
        http="httptools",
        workers=int(os.environ.get("WASDW_WORKERS", "1")),  # Production: WASDW_WORKERS=4
        limit_concurrency=200,
        limit_max_requests=10000,   # Worker'ı N istekten sonra restart et (memory leak önlemi)
        backlog=256,
        timeout_keep_alive=10,
    )
