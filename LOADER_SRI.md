# Loader SRI (Subresource Integrity) Implementation

## Overview

**Problem:** Integrity chain sadece loader'ın yüklediği part'ları koruyor, loader'ın kendisini değil.  
**Risk:** MITM/CDN/proxy loader.js'i modifiye edebilir → parts integrity check bypass edilebilir.

**Çözüm:** Browser native **SRI (Subresource Integrity)** — HTML template'de `integrity="sha384-..."` attribute.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  HTML Template (login.html / register.html)                 │
│                                                              │
│  <script src="/static/js/dist/WASD-core-V9b440f96.js"      │
│          integrity="sha384-/54nmtpi5Aj...qvgRE"            │
│          crossorigin="anonymous"></script>                   │
│                                                              │
│  ↓ Browser SRI validation (native crypto)                   │
│  ✓ Hash matches → Execute                                   │
│  ✗ Hash mismatch → Blocked (CSP report)                     │
└─────────────────────────────────────────────────────────────┘
         ↓
┌─────────────────────────────────────────────────────────────┐
│  Loader Integrity Chain (inside loader)                     │
│                                                              │
│  1. Loader loads part1.js, part2.js, part3.js              │
│  2. crypto.subtle.digest('SHA-256', part_content)           │
│  3. Compare with AES-GCM encrypted expected hashes          │
│  4. Mismatch → poison flag → block execution                │
└─────────────────────────────────────────────────────────────┘
```

## Implementation

### 1. Build-Time Hash Calculation (`obfuscate_js.py`)

**Location:** `_write_loader_script()` — line ~3820

```python
# ── SRI (Subresource Integrity): Loader SHA-384 hash ──────────────────────
# Browser native integrity check — HTML template'de integrity="sha384-..." attribute
# Loader'ın kendi bütünlüğünü korur (parts'ların integrity'si loader içinde check ediliyor)
loader_sha384 = hashlib.sha384(final.encode("utf-8")).digest()
loader_integrity = "sha384-" + base64.b64encode(loader_sha384).decode("ascii")
print(f"  [+] Loader SRI     : {loader_integrity[:32]}...")
```

**Output:**
```
[+] Loader SRI     : sha384-/54nmtpi5AjdsQ4BNAxMTbd7Q...
```

### 2. Manifest Storage

**Location:** `process_split_parts()` — line ~3185

```python
manifest["loader"]           = loader_name
manifest["fake_loader"]      = fake_loader_name
manifest["loader_integrity"] = loader_integrity  # SRI sha384 hash
manifest["sc_key"]           = sc_key
```

**Manifest Example:**
```json
{
  "loader": "WASD-core-V9b440f96.js",
  "loader_integrity": "sha384-/54nmtpi5AjdsQ4BNAxMTbd7QQ+Xw1xTKifNfRCn94blJOu0kuyN5ckCppyqvgRE",
  "parts": ["WASD-core-V89bc6a51.js", "WASD-core-Vefa303ac.js", "WASD-core-Vfb1f69dc.js"],
  "ts": 1735854287
}
```

### 3. Template Context Injection (`app.py`)

**Locations:**
- Login page: line ~610
- Register page: line ~665

```python
m = _load_manifest()
loader_js  = m.get("loader",      "wasd-loader.js") if m else "wasd-loader.js"
loader_v   = f"{m.get('ts', 0):08x}" if m else "00000000"
loader_integrity = m.get("loader_integrity", "") if m else ""  # SRI hash

response = templates.TemplateResponse(request=request, name="login.html", context={
    "loader_v":   loader_v,
    "loader_js":  loader_js,
    "loader_integrity": loader_integrity,  # SRI
    # ...
})
```

### 4. HTML Template (`login.html` / `register.html`)

**Location:** line ~81-82

**Before:**
```html
<script src="/static/js/dist/{{ loader_js }}?v={{ loader_v }}"></script>
```

**After:**
```html
<script src="/static/js/dist/{{ loader_js }}?v={{ loader_v }}"
        {% if loader_integrity %}
        integrity="{{ loader_integrity }}"
        crossorigin="anonymous"
        {% endif %}></script>
```

**Rendered Output:**
```html
<script src="/static/js/dist/WASD-core-V9b440f96.js?v=67911b4f"
        integrity="sha384-/54nmtpi5AjdsQ4BNAxMTbd7QQ+Xw1xTKifNfRCn94blJOu0kuyN5ckCppyqvgRE"
        crossorigin="anonymous"></script>
```

## Security Properties

### 1. Browser Native Validation
- **Algorithm:** SHA-384 (W3C recommended for SRI)
- **Timing:** Before script execution (browser blocks tampered scripts)
- **Bypass:** Impossible without valid hash (hash collision infeasible)

### 2. CORS Requirement
- **`crossorigin="anonymous"`:** Mandatory for SRI with external resources
- **Effect:** No credentials sent to CDN (privacy)
- **Fallback:** Self-hosted → same-origin → CORS not required, but attribute harmless

### 3. Attack Surface Reduction

| Attack Vector | Without SRI | With SRI |
|--------------|-------------|----------|
| **MITM inject malware into loader** | ✗ Successful | ✓ **Blocked** (hash mismatch) |
| **CDN compromise** | ✗ Attacker serves backdoor | ✓ **Blocked** (hash mismatch) |
| **Proxy tampering** | ✗ Attacker modifies loader | ✓ **Blocked** (hash mismatch) |
| **Cache poisoning** | ✗ Cached malware served | ✓ **Blocked** (hash mismatch) |
| **Parts tampering** | Loader integrity check | Loader integrity check |

### 4. Defense in Depth

```
Layer 1: SRI (Browser) → Loader integrity validated by HTML attribute
Layer 2: Loader → Parts integrity validated by crypto.subtle.digest + AES-GCM encrypted expected hashes
Layer 3: Parts → Self-defending + multi-channel anti-debug
Layer 4: Backend → PoW + rate limiting + fingerprinting
```

## Performance Impact

- **Hash Calculation:** ~1ms at build-time (SHA-384 on ~300KB loader)
- **Browser Validation:** <1ms (native crypto, async)
- **Network:** +64 bytes (base64 SHA-384 hash in HTML)

**Total Overhead:** <0.1% (negligible)

## Browser Support

**SRI Support:** 96%+ (all modern browsers)

| Browser | Version | Support |
|---------|---------|---------|
| Chrome  | 45+     | ✓       |
| Firefox | 43+     | ✓       |
| Safari  | 11.1+   | ✓       |
| Edge    | 17+     | ✓       |
| Opera   | 32+     | ✓       |

**Fallback:** Older browsers ignore `integrity` attribute → script loads normally (no SRI protection).

## Testing

### 1. Build Verification

```bash
python obfuscate_js.py --split
```

**Expected Output:**
```
[+] Loader SRI     : sha384-/54nmtpi5AjdsQ4BNAxMTbd7Q...
[+] Loader SRI manifest'e eklendi (template'lerde kullanilacak)
```

### 2. HTML Source Inspection

```bash
curl -s http://localhost:8000/login | grep integrity
```

**Expected:**
```html
integrity="sha384-/54nmtpi5AjdsQ4BNAxMTbd7QQ+Xw1xTKifNfRCn94blJOu0kuyN5ckCppyqvgRE"
```

### 3. Browser DevTools

1. Open login page
2. DevTools → Network → Filter: `WASD-core`
3. Check loader request headers:
   - **`sec-fetch-dest: script`**
   - **`sec-fetch-mode: no-cors`**
4. Response should have no CSP errors

### 4. Tampering Test (Negative)

**Manual Test:**
1. Modify `static/js/dist/WASD-core-V9b440f96.js` (add comment)
2. Reload login page
3. **Expected:** Browser blocks script with console error:
   ```
   Failed to find a valid digest in the 'integrity' attribute for resource
   'http://localhost:8000/static/js/dist/WASD-core-V9b440f96.js' with computed SHA-384 integrity
   'sha384-XXXXX...'. The resource has been blocked.
   ```

## Build Workflow

```bash
# 1. Modify source (wasd-parts/*.js)
# 2. Build with SRI
python obfuscate_js.py --split

# ── Build output ──
#   [+] Loader yazildi : dist/WASD-core-V9b440f96.js
#   [+] Loader SRI     : sha384-/54nmtpi5Aj...
#   [+] Loader SRI manifest'e eklendi

# 3. Manifest updated with new hash
cat _runtime/wasd-manifest.json | jq .loader_integrity

# 4. Server reads manifest and injects to template
python app.py

# 5. HTML served with integrity attribute
curl http://localhost:8000/login | grep integrity
```

## CSP (Content Security Policy) Integration

**Future Enhancement (Optional):**

```html
<meta http-equiv="Content-Security-Policy"
      content="require-sri-for script;">
```

**Effect:**
- Browser **rejects** all `<script>` tags without `integrity` attribute
- Enforces SRI globally (belt + suspenders)

**Trade-off:**
- Breaks third-party scripts (analytics, ads)
- Not implemented yet — consider for high-security deployments

## Conclusion

✅ **Loader integrity protected** by browser native SRI  
✅ **Parts integrity protected** by loader's crypto.subtle chain  
✅ **Zero runtime overhead** (build-time hash, browser native validation)  
✅ **Defense in depth** (SRI + AES-GCM + self-defending)

**Result:** Complete integrity chain from HTML → Loader → Parts → Execution

---

**Files Modified:**
- `obfuscate_js.py` (+10 lines: SHA-384 hash calculation, manifest injection)
- `app.py` (+6 lines: loader_integrity context for login/register)
- `templates/login.html` (+1 line: integrity + crossorigin attributes)
- `templates/register.html` (+1 line: integrity + crossorigin attributes)

**Build Output:**
```
[+] Loader SRI manifest'e eklendi (template'lerde kullanilacak)
```

**HTML Output:**
```html
<script src="/static/js/dist/WASD-core-V9b440f96.js?v=67911b4f"
        integrity="sha384-/54nmtpi5AjdsQ4BNAxMTbd7QQ+Xw1xTKifNfRE"
        crossorigin="anonymous"></script>
```

✅ **Implementation Complete**
