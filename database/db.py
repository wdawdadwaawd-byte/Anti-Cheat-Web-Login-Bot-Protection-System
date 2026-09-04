import sqlite3
import sqlite3
import hashlib
import bcrypt
import time
import os
import json
from typing import List, Dict, Optional, Tuple

DB_PATH = os.path.join(os.path.dirname(__file__), "wasdw.db")


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=10000")
    conn.execute("PRAGMA temp_store=MEMORY")
    return conn


def _hash_password(password: str) -> str:
    """bcrypt ile şifre hash'le (maliyet faktörü 12)."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()


def _verify_password(password: str, stored_hash: str) -> bool:
    """bcrypt ve geriye dönük SHA-256 doğrulaması."""
    # Yeni bcrypt hash ($2b$...)
    if stored_hash.startswith("$2b$") or stored_hash.startswith("$2a$"):
        return bcrypt.checkpw(password.encode(), stored_hash.encode())
    # Eski SHA-256 hash — geçiş dönemi için
    sha_hash = hashlib.sha256(password.encode()).hexdigest()
    return sha_hash == stored_hash


def init_db() -> None:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            email TEXT UNIQUE,
            balance REAL DEFAULT 250.00,
            currency TEXT DEFAULT 'USD',
            role TEXT DEFAULT 'Trader',
            trust_score INTEGER DEFAULT 100,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS security_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            ip TEXT NOT NULL,
            user_agent TEXT,
            event_type TEXT NOT NULL,
            threat_level TEXT NOT NULL,
            reason TEXT NOT NULL,
            details TEXT,
            blocked BOOLEAN DEFAULT 0
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ip_blacklist (
            ip TEXT PRIMARY KEY,
            reason TEXT NOT NULL,
            banned_at REAL NOT NULL,
            expires_at REAL NOT NULL
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_logs_ts ON security_logs(timestamp DESC)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_logs_ip ON security_logs(ip)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_blacklist_ip ON ip_blacklist(ip)")
    conn.commit()
    conn.close()


def add_security_log(
    ip: str,
    user_agent: str,
    event_type: str,
    threat_level: str,
    reason: str,
    details: dict = None,
    blocked: bool = False,
) -> dict:
    conn = get_db()
    cursor = conn.cursor()
    ts = time.time()
    details_json = json.dumps(details or {})
    cursor.execute(
        """
        INSERT INTO security_logs (timestamp, ip, user_agent, event_type, threat_level, reason, details, blocked)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (ts, ip, user_agent, event_type, threat_level, reason, details_json, int(blocked)),
    )
    conn.commit()
    log_id = cursor.lastrowid
    conn.close()
    return {
        "id": log_id,
        "timestamp": ts,
        "ip": ip,
        "user_agent": user_agent,
        "event_type": event_type,
        "threat_level": threat_level,
        "reason": reason,
        "details": details or {},
        "blocked": blocked,
    }


def get_recent_logs(limit: int = 50) -> List[dict]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM security_logs ORDER BY timestamp DESC LIMIT ?", (limit,))
    rows = cursor.fetchall()
    conn.close()
    result = []
    for row in rows:
        entry = dict(row)
        try:
            entry["details"] = json.loads(entry.get("details") or "{}")
        except Exception:
            entry["details"] = {}
        result.append(entry)
    return result


def get_stats() -> dict:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM security_logs")
    total_logs = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM security_logs WHERE blocked = 1")
    total_blocked = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM ip_blacklist")
    active_bans = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]
    cursor.execute("SELECT threat_level, COUNT(*) as count FROM security_logs GROUP BY threat_level")
    by_threat = {row["threat_level"]: row["count"] for row in cursor.fetchall()}
    cursor.execute("""
        SELECT event_type, COUNT(*) as count
        FROM security_logs
        GROUP BY event_type
        ORDER BY count DESC
        LIMIT 10
    """)
    by_event = {row["event_type"]: row["count"] for row in cursor.fetchall()}
    conn.close()
    return {
        "total_logs": total_logs,
        "total_blocked": total_blocked,
        "active_bans": active_bans,
        "total_users": total_users,
        "by_threat_level": by_threat,
        "by_event_type": by_event,
    }


def is_ip_banned(ip: str) -> bool:
    conn = get_db()
    cursor = conn.cursor()
    now = time.time()
    cursor.execute("SELECT expires_at FROM ip_blacklist WHERE ip = ?", (ip,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return False
    if row["expires_at"] < now:
        cursor.execute("DELETE FROM ip_blacklist WHERE ip = ?", (ip,))
        conn.commit()
        conn.close()
        return False
    conn.close()
    return True


def ban_ip(ip: str, reason: str, duration: int = 3600) -> None:
    conn = get_db()
    cursor = conn.cursor()
    now = time.time()
    cursor.execute(
        "INSERT OR REPLACE INTO ip_blacklist (ip, reason, banned_at, expires_at) VALUES (?, ?, ?, ?)",
        (ip, reason, now, now + duration),
    )
    conn.commit()
    conn.close()


def unban_ip(ip: str) -> None:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM ip_blacklist WHERE ip = ?", (ip,))
    conn.commit()
    conn.close()


def clear_all_bans() -> None:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM ip_blacklist")
    conn.commit()
    conn.close()


def verify_user(username: str, password: str) -> Optional[dict]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    stored_hash = row["password_hash"]
    if stored_hash == "KEYCLOAK_MANAGED":
        return None
    if not _verify_password(password, stored_hash):
        return None
    return dict(row)


def create_user(username: str, password: str, email: str = "") -> Tuple[bool, str]:
    try:
        from keycloak import KeycloakAdmin
        import config

        kc_admin = KeycloakAdmin(
            server_url=config.KEYCLOAK_SERVER_URL,
            username=config.KEYCLOAK_ADMIN_USER,
            password=config.KEYCLOAK_ADMIN_PASSWORD,
            realm_name=config.KEYCLOAK_REALM,
            verify=True,
        )
        kc_admin.create_user({"email": email, "username": username, "enabled": True})
        user_id_kc = kc_admin.get_user_id(username)
        kc_admin.set_user_password(user_id=user_id_kc, password=password, temporary=False)
        pwd_hash = "KEYCLOAK_MANAGED"
    except Exception as e:
        if getattr(e, "response_code", 0) == 409:
            return False, "Bu kullanıcı adı veya e-posta zaten kullanımda (Keycloak)."
        print(f"[-] Keycloak registration failed ({e}). Falling back to local database.")
        pwd_hash = _hash_password(password)  # bcrypt

    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO users (username, password_hash, email) VALUES (?, ?, ?)",
            (username, pwd_hash, email),
        )
        conn.commit()
        conn.close()
        if pwd_hash == "KEYCLOAK_MANAGED":
            return True, "Kullanıcı başarıyla oluşturuldu."
        else:
            return True, "Kullanıcı başarıyla oluşturuldu (Yerel Veritabanı - Keycloak Bağlantı Hatası)."
    except sqlite3.IntegrityError:
        conn.close()
        return False, "Bu kullanıcı adı veya e-posta zaten kullanımda (Local DB)."
    except Exception as e:
        conn.close()
        return False, str(e)
