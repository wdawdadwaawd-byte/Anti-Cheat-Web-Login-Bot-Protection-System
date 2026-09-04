import time
import secrets
import json
import base64
from typing import Tuple, Optional, Dict
import config
from security.crypto_engine import sign_data, verify_signature

def create_shield_cookie(client_ip: str, client_data_b64: str, stage: str = "PAGE_LOADED", flow_id: str = None) -> str:
    if not flow_id:
        flow_id = secrets.token_hex(12)
    ts = time.time()
    cd_hash_short = client_data_b64[:32] if client_data_b64 else "none"
    raw = f"{flow_id}|{stage}|{client_ip}|{cd_hash_short}|{ts}"
    sig = sign_data(raw)
    return f"{flow_id}|{stage}|{client_ip}|{cd_hash_short}|{ts}|{sig}"

def parse_shield_cookie(cookie_val: str, expected_ip: str) -> Tuple[bool, str, str, str, float]:
    if not cookie_val:
        return False, "", "", "", 0
    try:
        parts = cookie_val.split("|")
        if len(parts) != 6:
            return False, "", "", "", 0
        flow_id, stage, ip, cd_hash, ts_str, sig = parts
        raw = f"{flow_id}|{stage}|{ip}|{cd_hash}|{ts_str}"
        if not verify_signature(raw, sig):
            return False, "", "", "", 0
        if ip != expected_ip:
            return False, "", "", "", 0
        created_at = float(ts_str)
        if time.time() - created_at > config.SHIELD_MAX_AGE:
            return False, "", "", "", 0
        return True, flow_id, stage, cd_hash, created_at
    except Exception:
        return False, "", "", "", 0

def update_shield_stage(cookie_val: str, new_stage: str, client_ip: str) -> Optional[str]:
    valid, flow_id, _, cd_hash, _ = parse_shield_cookie(cookie_val, client_ip)
    if not valid:
        return None
    ts = time.time()
    raw = f"{flow_id}|{new_stage}|{client_ip}|{cd_hash}|{ts}"
    sig = sign_data(raw)
    return f"{flow_id}|{new_stage}|{client_ip}|{cd_hash}|{ts}|{sig}"

def validate_shield_for_api(cookie_val: str, client_ip: str, required_stages: list) -> Tuple[bool, str, str]:
    valid, flow_id, stage, _, _ = parse_shield_cookie(cookie_val, client_ip)
    if not valid:
        return False, "", ""
    if not any(stage.startswith(s) for s in required_stages):
        return False, flow_id, stage
    return True, flow_id, stage

def extract_client_data(client_data_b64: str) -> Dict:
    try:
        padded = client_data_b64 + "=" * (4 - len(client_data_b64) % 4)
        decoded = base64.urlsafe_b64decode(padded)
        return json.loads(decoded)
    except Exception:
        return {}
