"""
WASDW Challenge Wall (403 Interstitial) Motoru
===============================================
Cloudflare-tarzı "Bir dakika bekleyin, kontrol ediyoruz..." akışı.

Nasıl çalışır:
  1. Her gelen istek clearance cookie'ye bakılır.
  2. Cookie yoksa veya geçersizse → /challenge?next=<orijinal_path> sayfasına yönlendirilir.
  3. /challenge sayfası tarayıcıya özel PoW challenge gönderir.
  4. Frontend JS challenge'ı çözer, POST /api/challenge/verify'a yollar.
  5. Sunucu doğrularsa imzalı clearance cookie set eder, kullanıcı orijinal URL'ye yönlendirilir.
  6. Clearance cookie geçerliyken tekrar challenge gösterilmez (varsayılan 30 dk).

Token formatı (base64 URL-safe):
  CLR:<ip_hash>:<issued_at>:<expires_at>:<signature>
"""

import hmac
import hashlib
import time
import secrets
import base64
from typing import Tuple

import config


# ─────────────────────────────────────────────────────────────
# Yardımcı: IP'yi hash'le (cookie'ye ham IP yazmamak için)
# ─────────────────────────────────────────────────────────────

def _hash_ip(ip: str) -> str:
    """IP adresini tek yönlü hash'ler (clearance token'a gömülür)."""
    return hmac.new(
        config.SECRET_KEY.encode(),
        f"{config.CLEARANCE_SALT}:{ip}".encode(),
        hashlib.sha256
    ).hexdigest()[:16]


def _sign(payload: str) -> str:
    """HMAC-SHA256 imzası."""
    return hmac.new(
        config.SECRET_KEY.encode(),
        f"{config.CLEARANCE_SALT}:{payload}".encode(),
        hashlib.sha256
    ).hexdigest()


# ─────────────────────────────────────────────────────────────
# Clearance Token
# ─────────────────────────────────────────────────────────────

def issue_clearance_token(client_ip: str) -> str:
    """
    IP'ye özel imzalı clearance token üretir.
    Format (raw): CLR:<ip_hash>:<issued_at>:<expires_at>:<sig>
    """
    ip_hash = _hash_ip(client_ip)
    issued_at = time.time()
    expires_at = issued_at + config.CLEARANCE_COOKIE_MAX_AGE

    payload = f"CLR:{ip_hash}:{issued_at:.3f}:{expires_at:.3f}"
    sig = _sign(payload)
    raw = f"{payload}:{sig}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def validate_clearance_token(token: str, client_ip: str) -> Tuple[bool, str]:
    """
    Clearance token'ını doğrular.
    Returns: (is_valid, reason)
    """
    if not token:
        return False, "token_missing"

    try:
        raw = base64.urlsafe_b64decode(token.encode()).decode()
        parts = raw.split(":")
        # CLR:<ip_hash>:<issued_at>:<expires_at>:<sig>  → 5 parça
        if len(parts) != 5:
            return False, "token_malformed"

        token_type, ip_hash, issued_str, expires_str, sig = parts

        if token_type != "CLR":
            return False, "token_type_invalid"

        # İmza doğrula
        payload = f"CLR:{ip_hash}:{issued_str}:{expires_str}"
        expected_sig = _sign(payload)
        if not hmac.compare_digest(expected_sig, sig):
            return False, "token_signature_invalid"

        # Süre kontrolü
        now = time.time()
        expires_at = float(expires_str)
        if now > expires_at:
            return False, "token_expired"

        # IP eşleşme kontrolü (token başka IP'ye ait değilse geçersiz)
        expected_ip_hash = _hash_ip(client_ip)
        if not hmac.compare_digest(expected_ip_hash, ip_hash):
            return False, "token_ip_mismatch"

        return True, "ok"

    except Exception as e:
        return False, f"token_error:{type(e).__name__}"


# ─────────────────────────────────────────────────────────────
# Challenge Üretimi (PoW)
# ─────────────────────────────────────────────────────────────

def create_wall_challenge(client_ip: str) -> dict:
    """
    Challenge wall için imzalı PoW challenge üretir.
    Login sistemindeki challenge'dan bağımsız, daha hafif bir zorluk kullanır.

    Returns: {
        "chal_id": str,       # Benzersiz challenge ID
        "chal_salt": str,     # PoW salt
        "chal_diff": int,     # Zorluk (sıfır sayısı)
        "chal_token": str,    # İmzalı sunucu token (doğrulama için)
        "expires_in": int,    # Saniye cinsinden geçerlilik
    }
    """
    chal_id = secrets.token_hex(12)
    salt = secrets.token_hex(16)
    diff = config.CHALLENGE_WALL_DIFFICULTY
    ts = time.time()
    ip_hash = _hash_ip(client_ip)

    # İmzalı token: WALL:<chal_id>:<salt>:<diff>:<ip_hash>:<ts>
    payload = f"WALL:{chal_id}:{salt}:{diff}:{ip_hash}:{ts:.3f}"
    sig = _sign(payload)
    raw_token = f"{payload}:{sig}"
    chal_token = base64.urlsafe_b64encode(raw_token.encode()).decode()

    return {
        "chal_id": chal_id,
        "chal_salt": salt,
        "chal_diff": diff,
        "chal_token": chal_token,
        "expires_in": config.CHALLENGE_WALL_TOKEN_EXPIRY,
    }


def verify_wall_solution(
    chal_token: str,
    solution_nonce: str,
    client_ip: str,
    canvas_hash: str = "",
    webdriver: bool = False,
) -> Tuple[bool, str]:
    """
    Frontend'den gelen PoW çözümünü doğrular.

    Kontroller:
      1. Token imzası
      2. Token süresi (CHALLENGE_WALL_TOKEN_EXPIRY)
      3. IP eşleşmesi
      4. PoW matematiksel doğruluğu
      5. Çözüm hızı (< 80ms → bot şüphesi)
      6. Webdriver flag → otomatik ret
      7. Canvas hash eksikse → yumuşak uyarı (engel değil, log)

    Returns: (is_valid, reason)
    """
    if not chal_token or not solution_nonce:
        return False, "missing_fields"

    # Webdriver anında engelle
    if webdriver:
        return False, "automation_detected"

    try:
        raw = base64.urlsafe_b64decode(chal_token.encode()).decode()
        parts = raw.split(":")
        # WALL:<id>:<salt>:<diff>:<ip_hash>:<ts>:<sig>  → 7 parça
        if len(parts) != 7:
            return False, "token_malformed"

        token_type, chal_id, salt, diff_str, ip_hash, ts_str, sig = parts

        if token_type != "WALL":
            return False, "token_type_invalid"

        # İmza kontrolü
        payload = f"WALL:{chal_id}:{salt}:{diff_str}:{ip_hash}:{ts_str}"
        expected_sig = _sign(payload)
        if not hmac.compare_digest(expected_sig, sig):
            return False, "token_signature_invalid"

        # Süre kontrolü
        now = time.time()
        issued_at = float(ts_str)
        if now - issued_at > config.CHALLENGE_WALL_TOKEN_EXPIRY:
            return False, "challenge_expired"

        # Çok hızlı çözüm (< 80ms) → bot
        if now - issued_at < 0.08:
            return False, "solve_too_fast"

        # IP eşleşmesi
        expected_ip_hash = _hash_ip(client_ip)
        if not hmac.compare_digest(expected_ip_hash, ip_hash):
            return False, "ip_mismatch"

        # PoW matematiksel doğrulama
        difficulty = int(diff_str)
        target_prefix = "0" * difficulty
        candidate = f"{salt}{solution_nonce}".encode("utf-8")
        computed = hashlib.sha256(candidate).hexdigest()
        if not computed.startswith(target_prefix):
            return False, "pow_invalid"

        # Canvas hash zorunlu kontrolü
        # fp_error / canvas_error / boş değer → Node.js/headless ortam
        _canvas_bad = {
            "", "fp_error", "canvas_error", "canvas_unsupported",
            "error", "unsupported", "none", "null", "undefined",
        }
        ch = (canvas_hash or "").strip().lower()
        if ch in _canvas_bad:
            return False, "automation_detected"
        # Gerçek SHA-256 canvas hash: 64 hex karakter
        if len(ch) != 64 or not all(c in "0123456789abcdef" for c in ch):
            return False, "automation_detected"

        return True, "ok"

    except Exception as e:
        return False, f"verify_error:{type(e).__name__}"


# ─────────────────────────────────────────────────────────────
# Path Bypass Kontrolü
# ─────────────────────────────────────────────────────────────

def is_bypass_path(path: str) -> bool:
    """
    Challenge wall'dan muaf tutulması gereken yolları kontrol eder.
    config.CHALLENGE_WALL_BYPASS_PATHS listesine göre prefix eşleşmesi yapar.
    """
    for prefix in config.CHALLENGE_WALL_BYPASS_PATHS:
        if path == prefix or path.startswith(prefix + "/") or path.startswith(prefix + "?"):
            return True
    # Challenge sayfasının kendisi her zaman muaf (sonsuz redirect önlemi)
    if path == "/challenge":
        return True
    # Login/register flow kendi challenge'ını yönetir — duvardan muaf
    for login_path in ["/realms/", "/api/security/", "/api/auth/", "/api/puzzle/"]:
        if path.startswith(login_path):
            return True
    return False
