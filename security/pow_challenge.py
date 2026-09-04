import hashlib
import time
import secrets
from typing import Tuple, Dict, Any
import config
from .crypto_engine import create_challenge_token, parse_and_validate_challenge_token, create_login_ticket, validate_login_ticket

_used_tickets = set()
_used_challenges = set()
_cleanup_time = time.time()

def _cleanup_old_entries():
    global _cleanup_time, _used_tickets, _used_challenges
    now = time.time()
    if now - _cleanup_time > 300:
        _used_tickets.clear()
        _used_challenges.clear()
        _cleanup_time = now

def generate_pow_challenge() -> Dict[str, Any]:
    _cleanup_old_entries()
    challenge_id = secrets.token_hex(12)
    salt = secrets.token_hex(16)
    difficulty = config.POW_DIFFICULTY
    ts = time.time()

    signed_token = create_challenge_token(challenge_id, salt, difficulty, ts)

    return {
        "challenge_id": challenge_id,
        "salt": salt,
        "difficulty": difficulty,
        "token": signed_token,
        "algorithm": "SHA-256",
        "issued_at": ts
    }

def verify_pow_solution(token: str, solution_nonce: str) -> Tuple[bool, str, Dict[str, Any]]:
    _cleanup_old_entries()
    valid_token, meta = parse_and_validate_challenge_token(token)
    if not valid_token:
        return False, "Geçersiz veya süresi dolmuş Challenge Token", {}

    challenge_id = meta["challenge_id"]
    if challenge_id in _used_challenges:
        return False, "Challenge daha önce kullanılmış (Replay Attack engellendi)", {}

    salt = meta["salt"]
    difficulty = meta["difficulty"]
    issued_at = meta["timestamp"]

    solve_duration = time.time() - issued_at
    if solve_duration < 0.05:
        return False, "Şüpheli PoW çözüm hızı (<50ms)", {}

    target_prefix = "0" * difficulty
    combined = f"{salt}{solution_nonce}".encode('utf-8')
    computed_hash = hashlib.sha256(combined).hexdigest()

    if not computed_hash.startswith(target_prefix):
        return False, f"Hatalı PoW çözümü (Hash hedef sıfırlarla başlamıyor)", {
            "computed_hash": computed_hash,
            "target_prefix": target_prefix
        }

    _used_challenges.add(challenge_id)

    return True, "PoW Başarıyla Çözüldü", {
        "solve_duration_seconds": round(solve_duration, 3),
        "computed_hash": computed_hash
    }

def issue_login_ticket(session_nonce: str, fingerprint_hash: str, ip_ua_hash: str = "") -> str:
    return create_login_ticket(session_nonce, fingerprint_hash, ip_ua_hash)

def consume_login_ticket(ticket: str) -> Tuple[bool, str, Dict[str, Any]]:
    _cleanup_old_entries()
    valid, meta = validate_login_ticket(ticket)
    if not valid:
        return False, "Geçersiz veya süresi dolmuş Login Bileti", {}

    ticket_id = meta["ticket_id"]
    if ticket_id in _used_tickets:
        return False, "Login bileti daha önce kullanılmış (Replay Saldırısı Engellendi)", {}

    _used_tickets.add(ticket_id)

    return True, "Login Bileti Geçerli", meta
