import hmac
import hashlib
import time
import secrets
import base64
import ast
import json as _json
from typing import Tuple, Dict, Any, List
import config

VM_OPS_POOL = ["XOR", "ROT", "ADD_MOD", "SBOX", "SWAP_PAIRS", "MUL_MOD", "FOLD_XOR", "CASCADE"]

# ── WASM token sabitleri ──────────────────────────────────────────────────────
_WASM_C1   = 0x5A3CF1B7
_WASM_C2   = 0x9E3779B9
_WASM_C3   = 0x6B435621
_WASM_MASK = 0xFFFFFFFF
_WASM_TOKEN_WINDOW_SEC = 60


def _wasm_get_token_raw(seed: int) -> int:
    """WAT get_token() ile birebir aynı — sabitleri public ama ops şifrelenince bypass imkansız."""
    v = (seed ^ _WASM_C1) & _WASM_MASK
    v = ((v << 13) | (v >> 19)) & _WASM_MASK
    v = (v ^ _WASM_C2) & _WASM_MASK
    v = (v * _WASM_C3) & _WASM_MASK
    v = (v ^ (v >> 16)) & _WASM_MASK
    return v


def _encrypt_ops_for_client(operations: list, ephemeral_key: str) -> str:
    """
    chal_ops'u ephemeral_key ile XOR+base64 şifrele.
    Client ops'ları plain göremez → VM transform'u kör kopyalayamaz.
    ephemeral_key = decrypt(chal_ek, page_nonce) — page_nonce sunucu sırrından türüyor.
    """
    ops_bytes = _json.dumps(operations).encode('utf-8')
    key_bytes = bytes.fromhex(ephemeral_key)
    encrypted = bytes(b ^ key_bytes[i % len(key_bytes)] for i, b in enumerate(ops_bytes))
    return base64.b64encode(encrypted).decode()


def _decrypt_ops_server(encrypted_ops_b64: str, ephemeral_key: str) -> list:
    """Sunucu tarafında ops'ları decrypt eder (verify sırasında signed token'dan alınan plain ops kullanılır)."""
    encrypted = base64.b64decode(encrypted_ops_b64)
    key_bytes = bytes.fromhex(ephemeral_key)
    decrypted = bytes(b ^ key_bytes[i % len(key_bytes)] for i, b in enumerate(encrypted))
    return _json.loads(decrypted.decode('utf-8'))


def derive_page_nonce(flow_id: str) -> str:
    return hmac.new(
        config.SECRET_KEY.encode('utf-8'),
        f"PAGE_NONCE_V3:{flow_id}".encode('utf-8'),
        hashlib.sha256
    ).hexdigest()[:32]


def generate_dynamic_seed_matrix() -> Tuple[str, List[Dict[str, Any]]]:
    matrix_id = secrets.token_hex(8)
    num_ops = secrets.randbelow(4) + 5
    operations = []
    for _ in range(num_ops):
        op_type = secrets.choice(VM_OPS_POOL)
        key_val = secrets.randbelow(254) + 1
        operations.append({"op": op_type, "k": key_val})
    return matrix_id, operations


def encrypt_ephemeral_key(ephemeral_key_hex: str, page_nonce: str) -> str:
    ek_bytes = bytes.fromhex(ephemeral_key_hex)
    pad = hashlib.sha256(page_nonce.encode('utf-8')).digest()[:len(ek_bytes)]
    encrypted = bytes(a ^ b for a, b in zip(ek_bytes, pad))
    return encrypted.hex()


def compute_vm_signature(
    telemetry_str: str,
    operations: List[Dict[str, Any]],
    salt: str,
    hmac_key: str
) -> str:
    data_bytes = bytearray(telemetry_str.encode('utf-8'))
    for op in operations:
        t = op["op"]
        k = op["k"]
        n = len(data_bytes)
        if t == "XOR":
            for idx in range(n): data_bytes[idx] ^= (k + idx) % 256
        elif t == "ADD_MOD":
            for idx in range(n): data_bytes[idx] = (data_bytes[idx] + k) % 256
        elif t == "ROT":
            shift = k % 7 + 1
            for idx in range(n):
                b = data_bytes[idx]
                data_bytes[idx] = ((b << shift) | (b >> (8 - shift))) & 0xFF
        elif t == "SBOX":
            for idx in range(n): data_bytes[idx] = ((255 - data_bytes[idx]) ^ k) & 0xFF
        elif t == "SWAP_PAIRS":
            for idx in range(0, n - 1, 2):
                data_bytes[idx], data_bytes[idx + 1] = data_bytes[idx + 1], data_bytes[idx]
        elif t == "MUL_MOD":
            mk = k | 1
            for idx in range(n): data_bytes[idx] = (data_bytes[idx] * mk) % 256
        elif t == "FOLD_XOR":
            for idx in range(n // 2): data_bytes[idx] ^= data_bytes[n - 1 - idx]
        elif t == "CASCADE":
            for idx in range(1, n): data_bytes[idx] ^= data_bytes[idx - 1]
    return hmac.new(
        f"{hmac_key}:{salt}".encode('utf-8'),
        bytes(data_bytes),
        hashlib.sha256
    ).hexdigest()


def create_polymorphic_challenge(flow_id: str, client_ip: str, page_nonce: str) -> Dict[str, Any]:
    matrix_id, operations = generate_dynamic_seed_matrix()
    salt          = secrets.token_hex(16)
    diff          = config.POW_DIFFICULTY
    ts            = time.time()
    ephemeral_key = secrets.token_hex(16)
    encrypted_ek  = encrypt_ephemeral_key(ephemeral_key, page_nonce)
    binding_nonce = secrets.token_hex(16)

    # ops plain gönderilir — WASM içinde işlendiği için JS kopyalaması yanlış sonuç üretir
    ops_json_b64 = base64.urlsafe_b64encode(str(operations).encode()).decode()
    payload = f"CHAL:{matrix_id}:{salt}:{diff}:{ts}:{ops_json_b64}:{ephemeral_key}:{binding_nonce}"
    sig = hmac.new(config.SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
    signed_token  = base64.urlsafe_b64encode(f"{payload}:{sig}".encode()).decode()

    return {
        "chal_id":       matrix_id,
        "chal_salt":     salt,
        "chal_diff":     diff,
        "chal_ops":      operations,   # plain — JS WASM path'i ile işler
        "chal_payload":  signed_token,
        "chal_ek":       encrypted_ek,
        "binding_nonce": binding_nonce,
        "issued_at":     ts,
    }


def verify_polymorphic_solution(
    token: str,
    solution_nonce: str,
    sensor_signature: str,
    telemetry_summary: str,
    canvas_hash: str = "",
    binding_sig: str = "",
) -> Tuple[bool, str, Dict[str, Any]]:
    try:
        decoded = base64.urlsafe_b64decode(token.encode()).decode()
        parts   = decoded.split(":")

        if len(parts) == 9:
            token_type, matrix_id, salt, diff_str, ts_str, ops_b64, ephemeral_key, binding_nonce, sig = parts
        elif len(parts) == 8:
            token_type, matrix_id, salt, diff_str, ts_str, ops_b64, ephemeral_key, sig = parts
            binding_nonce = ""
        else:
            return False, "Geçersiz challenge formatı", {}

        if token_type != "CHAL":
            return False, "Geçersiz token tipi", {}

        difficulty = int(diff_str)
        timestamp  = float(ts_str)

        # İmza doğrula
        if binding_nonce:
            chk_payload = f"CHAL:{matrix_id}:{salt}:{diff_str}:{ts_str}:{ops_b64}:{ephemeral_key}:{binding_nonce}"
        else:
            chk_payload = f"CHAL:{matrix_id}:{salt}:{diff_str}:{ts_str}:{ops_b64}:{ephemeral_key}"
        expected_sig = hmac.new(config.SECRET_KEY.encode(), chk_payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected_sig, sig):
            return False, "Challenge token imzası geçersiz (Manipülasyon Tespit Edildi)", {}

        now = time.time()
        if now - timestamp > config.POW_EXPIRATION_SECONDS:
            return False, "Challenge süresi dolmuş", {"elapsed": round(now - timestamp, 2)}
        solve_duration = now - timestamp
        if solve_duration < 0.1:
            return False, "Şüpheli PoW çözüm hızı (<100ms)", {"solve_ms": round(solve_duration * 1000)}

        # Canvas binding
        if binding_nonce and canvas_hash:
            expected_binding = hmac.new(
                ephemeral_key.encode('utf-8'),
                f"{canvas_hash}:{binding_nonce}".encode('utf-8'),
                hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(expected_binding, binding_sig):
                return False, "Canvas binding imzası geçersiz (sahte canvas veya token replay)", {
                    "reason": "canvas_binding_mismatch",
                }

        # PoW doğrula
        target_prefix = "0" * difficulty
        if not hashlib.sha256(f"{salt}{solution_nonce}".encode()).hexdigest().startswith(target_prefix):
            return False, "Hatalı PoW çözümü", {}

        # Ops signed token'dan alınır (imzalı, manipüle edilemez)
        operations    = ast.literal_eval(base64.urlsafe_b64decode(ops_b64.encode()).decode())
        expected_hmac = compute_vm_signature(telemetry_summary, operations, salt, ephemeral_key)

        # sensor_signature parse
        sig_parts = sensor_signature.split(':') if sensor_signature else []
        if len(sig_parts) == 3:
            received_hmac, received_token, received_seed = sig_parts
        elif len(sig_parts) == 1:
            if config.WASM_TOKEN_REQUIRED:
                return False, "WASM token eksik (fallback path tespit edildi)", {"reason": "wasm_token_missing"}
            received_hmac, received_token, received_seed = sig_parts[0], None, None
        else:
            return False, "Dinamik Sensör İmzası format hatası", {
                "reason": "sensor_signature_format_invalid", "parts": len(sig_parts)
            }

        # HMAC doğrula
        if not hmac.compare_digest(expected_hmac, received_hmac):
            return False, "Dinamik Sensör İmzası Eşleşmedi (Console/Script Bypass Tespit Edildi)", {
                "reason": "sensor_signature_mismatch",
                "expected_prefix": expected_hmac[:8],
                "got_prefix": received_hmac[:8] if received_hmac else "EMPTY",
            }

        # WASM token doğrula
        if received_token is not None and received_seed is not None:
            try:
                seed_val  = int(received_seed,  16) & 0xFFFFFFFF
                token_val = int(received_token, 16) & 0xFFFFFFFF
            except ValueError:
                return False, "WASM token format hatası (hex parse)", {"reason": "wasm_token_parse_error"}

            now_ts = int(time.time())
            if abs(now_ts - seed_val) > _WASM_TOKEN_WINDOW_SEC:
                return False, "WASM token süresi dolmuş veya zaman damgası geçersiz", {
                    "reason": "wasm_token_expired", "seed": seed_val, "now": now_ts,
                    "delta": abs(now_ts - seed_val),
                }
            raw_expected = _wasm_get_token_raw(seed_val)
            if raw_expected != token_val:
                return False, "WASM token geçersiz (fallback veya manipülasyon)", {
                    "reason": "wasm_token_mismatch",
                    "expected": hex(raw_expected),
                    "got": hex(token_val),
                }

        return True, "PoW ve Dinamik VM Sensör İmzası Doğrulandı", {
            "matrix_id":     matrix_id,
            "solve_time":    round(solve_duration, 3),
            "ops_count":     len(operations),
            "wasm_verified": received_token is not None,
        }
    except Exception as e:
        return False, f"VM doğrulama hatası: {str(e)}", {"exception": str(type(e).__name__)}
