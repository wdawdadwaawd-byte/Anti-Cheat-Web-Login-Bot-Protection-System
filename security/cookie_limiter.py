import time
import json
import hmac
import hashlib
from typing import Tuple
import config
from security.crypto_engine import sign_data, verify_signature

COOKIE_MAX_REQUESTS = 2
COOKIE_WINDOW_SECONDS = 300
COOKIE_NAME = "_w_req_guard"

def _make_guard_cookie(flow_id: str, client_ip: str, count: int = 0) -> str:
    ts = time.time()
    raw = f"{flow_id}|{count}|{client_ip}|{ts}"
    sig = sign_data(raw)
    return f"{flow_id}|{count}|{client_ip}|{ts}|{sig}"

def parse_guard_cookie(cookie_val: str, expected_ip: str) -> Tuple[bool, str, int, float]:
    if not cookie_val:
        return False, "", 0, 0
    try:
        parts = cookie_val.split("|")
        if len(parts) != 5:
            return False, "", 0, 0
        flow_id, count_str, ip, ts_str, sig = parts
        raw = f"{flow_id}|{count_str}|{ip}|{ts_str}"
        if not verify_signature(raw, sig):
            return False, "", 0, 0
        created_at = float(ts_str)
        if time.time() - created_at > COOKIE_WINDOW_SECONDS:
            return False, "", 0, 0
        return True, flow_id, int(count_str), created_at
    except Exception:
        return False, "", 0, 0

def check_and_consume_request(cookie_val: str, client_ip: str) -> Tuple[bool, str, str]:
    valid, flow_id, count, created_at = parse_guard_cookie(cookie_val, client_ip)

    if not valid:
        import secrets
        new_flow_id = secrets.token_hex(12)
        new_cookie = _make_guard_cookie(new_flow_id, client_ip, 1)
        return True, "Yeni sayfa ziyareti", new_cookie

    if count >= COOKIE_MAX_REQUESTS:
        remaining = COOKIE_WINDOW_SECONDS - (time.time() - created_at)
        if remaining < 0:
            remaining = 0
        return False, f"İstek sınırı aşıldı. {int(remaining)} saniye sonra tekrar deneyin.", ""

    new_count = count + 1
    new_cookie = _make_guard_cookie(flow_id, client_ip, new_count)
    remaining = COOKIE_MAX_REQUESTS - new_count
    return True, f"Kalan hak: {remaining}", new_cookie

def create_initial_guard_cookie(client_ip: str) -> str:
    import secrets
    flow_id = secrets.token_hex(12)
    return _make_guard_cookie(flow_id, client_ip, 0)
