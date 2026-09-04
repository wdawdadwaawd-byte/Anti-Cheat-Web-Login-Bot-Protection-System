# WASDW Anti-Cheat — Web Login Bot Protection System

A multi-layer anti-bot and automation protection system for web login flows. Built with Python (FastAPI) + JavaScript.

---

## Architecture

```
┌─────────────────────────────────────────┐
│  Browser SRI (Layer 1)                  │
│  integrity="sha384-..."                 │
├─────────────────────────────────────────┤
│  AES Seed Promise (Layer 2)             │
│  5s timeout · fail-closed              │
├─────────────────────────────────────────┤
│  Obfuscated Loader (Layer 3)            │
│  crypto.subtle · AES-GCM · 10²³ combos │
├─────────────────────────────────────────┤
│  Client Parts (Layer 4)                 │
│  6-channel anti-debug · 1-2s cache TTL │
├─────────────────────────────────────────┤
│  Server Behavioral Analysis (Layer 5)   │
│  Raw time-series · rAF jitter · ML-ready│
├─────────────────────────────────────────┤
│  Backend (Layer 6)                      │
│  PoW · Dynamic VM · CAPTCHA · Evidence  │
└─────────────────────────────────────────┘
```

---

## Features

| Feature | Description |
|---------|-------------|
| **Proof of Work** | SHA-256 PoW challenge on every login flow |
| **Dynamic VM** | Per-session polymorphic WASM challenge (XOR, ROT, AES ops) |
| **Canvas Binding** | HMAC-signed canvas fingerprint tied to session |
| **CAPTCHA** | 2-round adaptive image CAPTCHA (4 types) |
| **Evidence Auth** | Two-phase auth — client submits raw evidence, server decides |
| **Behavioral Analysis** | Server-side mouse/rAF/scroll analysis — no client-side threshold |
| **Honeypot Routes** | Dynamic decoy endpoints, correlation-based ban |
| **AES-GCM Obfuscation** | Build-time parameterized JS obfuscation (10²³ combinations) |
| **SRI Integrity** | Browser-native loader integrity chain |
| **SOC Dashboard** | Real-time security event monitoring (WebSocket) |

---

## Flow

```
GET  /login
  └─ HTML + page_nonce + session_key cookie

POST /api/challenge/request      ← PoW challenge
POST /api/challenge/verify       ← PoW solution + clearance cookie

POST /api/security/challenge     ← Dynamic VM challenge
POST /api/security/verify-challenge  ← VM solution + WASM token + login ticket

POST /api/behavioral-submit      ← Raw mouse/rAF/scroll events

POST /api/auth/submit-evidence   ← HMAC-signed evidence bundle → evidence_token

GET  /api/captcha/challenge      ← 2-round image CAPTCHA
POST /api/captcha/verify         ← CAPTCHA solution → clearance

POST /api/auth/login             ← Username + password + all tokens
```

---

## Security Layers

### 1 · Proof of Work
SHA-256 brute-force. Difficulty configurable. Prevents trivial scripted access.

### 2 · Dynamic VM (Polymorphic)
Per-session random op chain (5–8 ops): `XOR`, `ROT`, `ADD_MOD`, `SBOX`, `SWAP_PAIRS`, `MUL_MOD`, `FOLD_XOR`, `CASCADE`.
WASM token ties the solution to real browser execution time.

### 3 · Canvas Binding
Canvas fingerprint HMAC-signed against the ephemeral key. Replay attacks fail because the binding nonce is session-unique.

### 4 · Evidence-Based Auth
Client collects raw signals (PoW result, behavioral data, env flags, fingerprint) and sends them unsigned to the server. **Server makes all decisions** — no client-side allow/block logic to reverse.

### 5 · Behavioral Analysis
Raw `(t, x, y)` mouse events and rAF deltas submitted server-side. Detects:
- Synthetic uniform rAF timing (`stddev < 1.0`)
- Excessive/insufficient event counts
- Scroll velocity anomalies

ML-ready pipeline (Isolation Forest).

### 6 · CAPTCHA (4 Types)
| Type | Challenge |
|------|-----------|
| 1 | Pick 2 matching icons from 6 cards |
| 2 | Pick 2 matching color cards |
| 3 | Pick card with exact emoji count |
| 4 | Logic/arithmetic (largest, prime, odd/even, square) |

Answers stored server-side only — labels in response are the solution vector (intentional design tradeoff for this demo).

### 7 · Honeypot Routes
Build-time random decoy paths embedded in obfuscated JS. Hits are correlated against challenge success — bots that execute decoys without completing the challenge get banned.

---

## Stack

| Component | Technology |
|-----------|-----------|
| Backend | Python 3.11 · FastAPI · uvicorn |
| Database | SQLite (via `database/db.py`) |
| JS Build | `obfuscate_js.py` · javascript-obfuscator · wabt (WASM) |
| Crypto | `hmac` · `hashlib` · `secrets` · AES-GCM (browser `crypto.subtle`) |
| Frontend | Vanilla JS · SVG CAPTCHA · WebSocket SOC feed |

---

## Quick Start

```bash
# Install dependencies
pip install fastapi uvicorn pydantic

# Build obfuscated JS (requires Node.js + npm)
npm install
python obfuscate_js.py --split

# Run
python app.py
```

Server starts at `http://localhost:8000`

SOC dashboard: `http://localhost:8000/admin?token=<ADMIN_TOKEN>`

---

## Configuration

Set via environment variables:

```bash
WASDW_SECRET_KEY=<32-byte hex>       # HMAC signing key
WASDW_ADMIN_TOKEN=<token>            # SOC dashboard token
WASDW_WORKERS=4                      # uvicorn workers (production)
WASDW_WASM_HARD_FAIL=true            # Fail startup if WASM missing
```

---

## JS Build

```bash
python obfuscate_js.py --split
```

Outputs to `static/js/dist/`:
- `WASD-core-V{hash}.js` — obfuscated parts (3 chunks)
- `wasd-loader.js` — orchestrator with SRI hash
- `vm_transform.wasm` — PoW + VM execution module

Manifest written to `_runtime/wasd-manifest.json` (not web-accessible).

---

## Bypass Research

A working Python bypass checker is included as `bypas.py` — demonstrates the system is bypassable from the API layer without a browser. Bot score achieved: **100/100**.

Key observations:
- CAPTCHA labels are returned in plaintext → solvable without image parsing
- All crypto primitives are reimplementable from the open source code
- Behavioral analysis passes with realistic synthetic data

---

## License

MIT — see [LICENSE](LICENSE)

Copyright (c) 2026 WASDW
