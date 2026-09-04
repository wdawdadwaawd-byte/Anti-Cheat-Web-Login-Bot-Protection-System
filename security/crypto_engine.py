import hmac
import hashlib
import time
import json
import base64
import secrets
import config

def sign_data(data_str: str) -> str:
    return hmac.new(
        config.SECRET_KEY.encode('utf-8'),
        data_str.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

def verify_signature(data_str: str, signature: str) -> bool:
    expected = sign_data(data_str)
    return hmac.compare_digest(expected, signature)

def generate_session_nonce() -> str:
    return secrets.token_hex(16)

def create_challenge_token(challenge_id: str, salt: str, difficulty: int, timestamp: float) -> str:
    payload = f"CHAL:{challenge_id}:{salt}:{difficulty}:{timestamp}"
    signature = sign_data(payload)
    return base64.urlsafe_b64encode(f"{payload}:{signature}".encode()).decode()

def parse_and_validate_challenge_token(token: str) -> tuple[bool, dict]:
    try:
        decoded = base64.urlsafe_b64decode(token.encode()).decode()
        parts = decoded.split(":")
        if len(parts) != 6:
            return False, {}

        token_type, challenge_id, salt, diff_str, ts_str, signature = parts
        if token_type != "CHAL":
            return False, {}

        difficulty = int(diff_str)
        timestamp = float(ts_str)

        payload = f"CHAL:{challenge_id}:{salt}:{difficulty}:{timestamp}"
        if not verify_signature(payload, signature):
            return False, {}

        now = time.time()
        if now - timestamp > config.POW_EXPIRATION_SECONDS or timestamp > now + 5:
            return False, {}

        return True, {
            "challenge_id": challenge_id,
            "salt": salt,
            "difficulty": difficulty,
            "timestamp": timestamp
        }
    except Exception:
        return False, {}

def create_login_ticket(session_nonce: str, fingerprint_hash: str, ip_ua_hash: str = "") -> str:
    ts = time.time()
    ticket_id = secrets.token_hex(16)
    payload = f"TICKET:{ticket_id}:{session_nonce}:{fingerprint_hash}:{ip_ua_hash}:{ts}"
    signature = sign_data(payload)
    return base64.urlsafe_b64encode(f"{payload}:{signature}".encode()).decode()

def validate_login_ticket(ticket: str) -> tuple[bool, dict]:
    try:
        decoded = base64.urlsafe_b64decode(ticket.encode()).decode()
        parts = decoded.split(":")

        if len(parts) != 7:
            return False, {}

        token_type, ticket_id, session_nonce, fingerprint_hash, ip_ua_hash, ts_str, signature = parts
        if token_type != "TICKET":
            return False, {}

        timestamp = float(ts_str)
        payload = f"TICKET:{ticket_id}:{session_nonce}:{fingerprint_hash}:{ip_ua_hash}:{timestamp}"
        if not verify_signature(payload, signature):
            return False, {}

        now = time.time()
        if now - timestamp > config.LOGIN_TICKET_EXPIRATION_SECONDS:
            return False, {}

        return True, {
            "ticket_id": ticket_id,
            "session_nonce": session_nonce,
            "fingerprint_hash": fingerprint_hash,
            "ip_ua_hash": ip_ua_hash,
            "timestamp": timestamp
        }
    except Exception:
        return False, {}

def polymorphic_pack(data: dict) -> dict:
    junk_keys = [secrets.token_hex(4) for _ in range(4)]
    packed = {
        "_w_sec": secrets.token_hex(10),
        "_w_ts": int(time.time()),
        "payload": data
    }
    for jk in junk_keys:
        packed[f"w_{jk}"] = secrets.token_hex(16)
    return packed
