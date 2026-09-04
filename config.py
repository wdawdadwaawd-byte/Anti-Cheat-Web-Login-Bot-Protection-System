import os
import secrets

HOST = "0.0.0.0"
PORT = 8000

# SECRET_KEY: env variable'dan al, yoksa kalıcı bir değer kullan.
# Üretimde WASDW_SECRET_KEY environment variable'ı mutlaka set et.
# Her restart'ta değişirse cookie/nonce'lar bozulur.
SECRET_KEY = os.environ.get("WASDW_SECRET_KEY") or "wasdw-default-secret-change-in-prod-2024"

# WASM token doğrulaması: True = zorunlu, False = opsiyonel
# part3-pow-vm.js WASM'ın yüklenmesini bekleyerek garantiledikten sonra True güvenli.
WASM_TOKEN_REQUIRED = os.environ.get("WASDW_WASM_TOKEN_REQUIRED", "true").lower() == "true"

# ── WASM Build Güvenlik Ayarları ──────────────────────────────────────────────
# WASM_BUILD_HARD_FAIL=true → WASM build başarısız olursa obfuscate_js.py
#   nonzero exit code ile çıkar ve sunucu başlamaz.
# WASM_BUILD_HARD_FAIL=false (default) → eski davranış: uyarı bas, JS fallback.
# Production'da true önerilir; geliştirme ortamında false bırakılabilir.
WASM_BUILD_HARD_FAIL = os.environ.get("WASDW_WASM_HARD_FAIL", "false").lower() == "true"

# WASM_ALERT_WEBHOOK: WASM build başarısız olduğunda POST gönderilecek URL.
# Boş bırakılırsa webhook devre dışı.
# Örnek: https://hooks.slack.com/services/xxx  veya  https://discord.com/api/webhooks/xxx
WASM_ALERT_WEBHOOK = os.environ.get("WASDW_ALERT_WEBHOOK", "")
GATEWAY_CLIENT_KEY = "WASDW_GATEWAY_V2"
POW_DIFFICULTY = 4
POW_EXPIRATION_SECONDS = 90
LOGIN_TICKET_EXPIRATION_SECONDS = 180
SHIELD_MAX_AGE = 300

KEYCLOAK_SERVER_URL = "http://localhost:8080/"
KEYCLOAK_REALM = "wasdw-realm"
KEYCLOAK_CLIENT_ID = "wasdw-client"
KEYCLOAK_ADMIN_USER = os.environ.get("KEYCLOAK_ADMIN_USER", "admin")
KEYCLOAK_ADMIN_PASSWORD = os.environ.get("KEYCLOAK_ADMIN_PASSWORD", "admin")

# Admin token — production'da mutlaka set et:
# export WASDW_ADMIN_TOKEN=$(python -c "import secrets; print(secrets.token_hex(32))")
ADMIN_TOKEN = os.environ.get("WASDW_ADMIN_TOKEN") or "WASD-PRODUCTION"

MIN_FORM_TIME_MS = 600
MIN_MOUSE_EVENTS = 2
MIN_KEY_INTERVAL_AVG_MS = 20

CLOUDFLARE_WARP_SUBNETS = [
    "8.20.0.0/14",
    "8.24.0.0/14",
    "8.28.0.0/14",
    "104.28.0.0/14",
    "104.30.0.0/15",
]

DATACENTER_SUBNETS = [
    "45.32.0.0/16",
    "45.33.0.0/16",
    "45.55.0.0/16",
    "45.76.0.0/16",
    "46.101.0.0/16",
    "51.15.0.0/16",
    "64.225.0.0/16",
    "65.21.0.0/16",
    "88.99.0.0/16",
    "95.216.0.0/16",
    "104.131.0.0/16",
    "104.248.0.0/16",
    "116.202.0.0/16",
    "116.203.0.0/16",
    "128.199.0.0/16",
    "138.68.0.0/16",
    "139.59.0.0/16",
    "142.93.0.0/16",
    "144.76.0.0/16",
    "159.65.0.0/16",
    "167.99.0.0/16",
    "167.172.0.0/16",
    "178.62.0.0/16",
    "188.166.0.0/16",
    "192.241.0.0/16",
    "198.199.0.0/16",
    "206.189.0.0/16",
    "207.154.0.0/16",
    "209.97.0.0/16",
]

CHALLENGE_WALL_ENABLED = True

CLEARANCE_COOKIE_MAX_AGE = 1800

CHALLENGE_WALL_DIFFICULTY = 4

CHALLENGE_WALL_TOKEN_EXPIRY = 60

CLEARANCE_COOKIE_NAME = "_w_clr"

CLEARANCE_SALT = "WASDW_CLR_V1"

CHALLENGE_WALL_BYPASS_PATHS = [
    "/static",
    "/challenge",
    "/api/challenge",   # challenge duvarının kendi API'si
    "/admin",
    "/ws",
    "/api/admin",
    "/favicon.ico",
]

SUSPICIOUS_USER_AGENTS = [
    "python-requests",
    "python-urllib",
    "aiohttp",
    "httpx",
    "curl",
    "wget",
    "postman",
    "insomnia",
    "go-http-client",
    "java/",
    "okhttp",
    "winhttp",
    "apache-httpclient",
    "openbullet",
    "silverbullet",
    "phantomjs",
    "burpsuite",
    "burp suite",
    "zaproxy",
    "owasp zap",
    "nikto",
    "sqlmap",
    "nmap",
    "masscan",
    "dirbuster",
    "gobuster",
    "wfuzz",
    "hydra",
    "metasploit",
    "nessus",
    "acunetix",
    "nuclei",
    "ffuf",
    "semrush",
    "ahrefsbot",
    "mj12bot",
]

PROXY_HEADERS = [
    "x-proxy-id",
    "x-varnish",
    "squid-ip",
    "x-roxy-connection",
    "cf-warp-tag",
    "x-burp-flag",
    "x-scanner",
    "x-security-test",
    "x-pentest",
    "x-audit",
]
