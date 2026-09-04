import os, sys, json, re, time, random, secrets, base64, hashlib, subprocess, argparse, shutil, io, struct, uuid

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
JS_DIR     = os.path.join(BASE_DIR, "static", "js")
DIST_DIR   = os.path.join(BASE_DIR, "static", "js", "dist")
# CONFIG_PATH artık kullanılmıyor — her build için parametreler rastgele üretilir
CONFIG_PATH   = os.path.join(BASE_DIR, "obfuscator_config.json")  # legacy fallback
PROFILES_PATH = os.path.join(BASE_DIR, "obfuscator_profiles.json")  # sadece reserved names için
NODE_BIN   = os.path.join(BASE_DIR, "node_modules", "javascript-obfuscator", "bin", "javascript-obfuscator")

# Parametre tabanlı rastgele obfuscator config üretimi
# Build başına bir kere üretilir ve cache'lenir
_SELECTED_CONFIG: dict | None = None

def _generate_random_config() -> dict:
    """
    Her parametre için rastgele değer üretir — sonsuz kombinasyon.
    Sabit profil sistemi kaldırıldı, fingerprinting artık imkansız.
    
    Parametre aralıkları:
    - deadCodeInjection: 0.0-0.4 (çok yüksek değerler boyutu aşırı şişirir)
    - stringArrayThreshold: 0.4-0.9 (yeterli string obfuscation)
    - splitStringsChunkLength: 3-15 (küçük chunk = daha fazla bölünme)
    - stringArrayRotate: bool (runtime rotate)
    - stringArrayShuffle: bool (build-time shuffle)
    - controlFlowFlattening: bool (büyük boyut artışı ama iyi koruma)
    - controlFlowFlatteningThreshold: 0.3-0.8
    - renameGlobals: false (window/document bozulmasın)
    - selfDefending: false (bizim custom anti-tamper var)
    - debugProtection: false (bizim custom timing guard var)
    - compact: true (her zaman tek satır)
    - simplify: false (tersine mühendislik kolaylaştırmasın)
    - stringArrayEncoding: ['base64', 'rc4'] rastgele kombinasyon
    - stringArrayIndexShift: bool
    - stringArrayWrappersCount: 1-4
    - stringArrayWrappersChainedCalls: bool
    - unicodeEscapeSequence: bool (rastgele)
    - transformObjectKeys: bool
    - splitStrings: bool
    """
    # Base config — sabit ayarlar
    cfg = {
        "compact": True,
        "simplify": False,
        "renameGlobals": False,
        "selfDefending": False,
        "debugProtection": False,
        "debugProtectionInterval": 0,
        "disableConsoleOutput": False,  # console hook'ları kaldırdık
        "target": "browser",
        "sourceMap": False,
    }
    
    # Rastgele parametreler — her build farklı
    # deadCodeInjection: boolean (enable/disable)
    # deadCodeInjectionThreshold: 0.0-1.0 (ne kadar dead code)
    cfg["deadCodeInjection"] = R.choice([True, False])
    cfg["deadCodeInjectionThreshold"] = round(R.uniform(0.01, 0.4), 2)
    
    cfg["stringArrayThreshold"] = round(R.uniform(0.4, 0.9), 2)
    cfg["stringArrayRotate"] = R.choice([True, False])
    cfg["stringArrayShuffle"] = R.choice([True, False])
    cfg["stringArrayIndexShift"] = R.choice([True, False])
    cfg["stringArrayWrappersCount"] = R.randint(1, 4)
    cfg["stringArrayWrappersChainedCalls"] = R.choice([True, False])
    
    # stringArrayEncoding: [] | ['base64'] | ['rc4'] | ['base64', 'rc4']
    encoding_choices = [
        [],
        ["base64"],
        ["rc4"],
        ["base64", "rc4"]
    ]
    cfg["stringArrayEncoding"] = R.choice(encoding_choices)
    
    cfg["splitStrings"] = R.choice([True, False])
    if cfg["splitStrings"]:
        cfg["splitStringsChunkLength"] = R.randint(3, 15)
    
    cfg["controlFlowFlattening"] = R.choice([True, False])
    if cfg["controlFlowFlattening"]:
        cfg["controlFlowFlatteningThreshold"] = round(R.uniform(0.3, 0.8), 2)
    
    cfg["unicodeEscapeSequence"] = R.choice([True, False])
    cfg["transformObjectKeys"] = R.choice([True, False])
    
    # Reserved names/strings — sabit (profil dosyasından veya default)
    if os.path.isfile(PROFILES_PATH):
        try:
            with open(PROFILES_PATH, encoding="utf-8") as f:
                data = json.load(f)
                reserved = data.get("reserved", {})
                for key in ("reservedNames", "reservedStrings"):
                    if key in reserved:
                        cfg[key] = reserved[key]
        except:
            pass
    
    # Log üretilen parametreleri (fingerprint prevention verification)
    param_summary = (
        f"deadCodeInj={cfg['deadCodeInjection']} "
        f"deadCodeThr={cfg['deadCodeInjectionThreshold']} "
        f"strArrThresh={cfg['stringArrayThreshold']} "
        f"splitChunk={cfg.get('splitStringsChunkLength', 'N/A')} "
        f"ctrlFlow={cfg['controlFlowFlattening']} "
        f"encoding={','.join(cfg['stringArrayEncoding']) if cfg['stringArrayEncoding'] else 'none'}"
    )
    print(f"  [config] Rastgele parametreler: {param_summary}")
    
    return cfg

def _load_selected_profile() -> dict:
    """
    Build başına bir kere rastgele config üretir ve cache'ler.
    Geriye dönük uyumluluk için fonksiyon adı korundu.
    """
    global _SELECTED_CONFIG
    if _SELECTED_CONFIG is not None:
        return _SELECTED_CONFIG
    
    _SELECTED_CONFIG = _generate_random_config()
    return _SELECTED_CONFIG

TARGET_FILES = ["WASD-core.js", "challenge-wall.js", "shield-core.js"]

# Dosya-özgü override'lar — her dosya için farklı deadCodeInjection aralığı
# Build başına bu aralıktan rastgele seçilir
FILE_CONFIG_OVERRIDES = {
    "WASD-core.js":      {"deadCodeInjection_range": (0.05, 0.12)},
    "challenge-wall.js": {"deadCodeInjection_range": (0.08, 0.15)},
    "shield-core.js":    {"deadCodeInjection_range": (0.08, 0.15)},
}

R = random.Random()

def _rn(prefix=""):
    """
    Rastgele JS değişken adı üretir.
    prefix: artık çıktıda görünmez — sadece Random seed'i kaydırmak için
    karakter toplamı olarak kullanılır. Böylece aynı prefix farklı build'lerde
    farklı isim üretir ama decoder varyantını ele vermez.
    """
    chars  = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    digits = "0123456789"
    # prefix'i seed kayması olarak kullan — çıktıya yazmıyoruz
    _shift = sum(ord(c) for c in prefix) % 7
    length = R.randint(5 + _shift % 3, 10 + _shift % 4)
    first  = R.choice(chars)
    body   = ''.join(R.choices(chars + digits, k=length))
    return f"_{first}{body}"

def _ri(lo=0, hi=0xFFFF): return R.randint(lo, hi)
def _rhex(n=4): return secrets.token_hex(n)

def _num_expr(val: int) -> str:
    """
    Bir tam sayıyı JS'de aynı değeri veren farklı ifade formlarından biriyle gösterir.
    Her build'de rastgele form seçilir — statik analiz tek pattern göremez.
    5 form; operandların val'e eşit çıkması aralık seçimiyle önlenir (re-roll yok).
    """
    forms = []

    # Form 1: XOR iki operand  (A ^ B)  →  A = rand ≠ val, B = val ^ A
    # Aralık [1, val-1] ∪ [val+1, 0xFFF] → val'den farklı garantili değer
    _pool1 = list(range(1, max(2, val))) + list(range(val + 1, 0x1000))
    a1 = R.choice(_pool1) if _pool1 else R.randint(1, 0xFFF) ^ 1
    forms.append(f"(0x{a1:x}^0x{(val ^ a1):x})")

    # Form 2: SUB  (val + C) - C  →  C ∈ [1, 0x3F], C ≠ val
    _c2_choices = [c for c in range(1, 0x40) if c != val]
    c2 = R.choice(_c2_choices) if _c2_choices else 1
    forms.append(f"(0x{(val + c2):x}-0x{c2:x})")

    # Form 3: Çift subtract  (val + D + E) - D - E
    # D ≠ E ve D+E ≠ val
    d3 = R.randint(1, 0x7F)
    e3 = R.randint(1, 0x7F)
    # Basit collision check — en fazla 3 deneme, sonra D=1 E=2 fallback
    for _ in range(3):
        if d3 != e3 and (d3 + e3) != val:
            break
        d3 = R.randint(1, 0x7F)
        e3 = R.randint(1, 0x7F)
    else:
        d3, e3 = 1, 2  # fallback: 1+2=3, val=3 olsa bile çift subtract doğru hesaplar
    forms.append(f"(0x{(val + d3 + e3):x}-0x{d3:x}-0x{e3:x})")

    # Form 4: ADD  (val - C) + C  →  sadece val >= 2 için
    # C ∈ [1, min(val-1, 0x3F)] — C < val garantisi: (val-C) sıfır olamaz
    if val >= 2:
        hi4 = min(val - 1, 0x3F)  # val-1: üst sınır val'e eşit olamaz
        c4 = R.randint(1, hi4)
        forms.append(f"(0x{(val - c4):x}+0x{c4:x})")

    # Form 5: Nested XOR üç operand  ((A ^ M) ^ B) = val
    # A ≠ val, M ≠ val, (A^M) ≠ val — aralık sınırlamasıyla
    for _ in range(5):
        a5 = R.randint(1, 0xFF)
        m5 = R.randint(1, 0xFF)
        if a5 != val and m5 != val and (a5 ^ m5) != val:
            break
    else:
        # Fallback: A=1, M=2 → B = val^1^2, ara sonuçlar val'e eşit değil (val≥4 için güvenli)
        a5, m5 = 1, 2
    b5 = val ^ a5 ^ m5
    forms.append(f"((0x{a5:x}^0x{m5:x})^0x{b5:x})")

    return R.choice(forms)

def _rc4_encrypt(data: bytes, key: bytes) -> bytes:
    S = list(range(256))
    j = 0
    for i in range(256):
        j = (j + S[i] + key[i % len(key)]) % 256
        S[i], S[j] = S[j], S[i]
    i = j = 0
    out = []
    for byte in data:
        i = (i + 1) % 256
        j = (j + S[i]) % 256
        S[i], S[j] = S[j], S[i]
        out.append(byte ^ S[(S[i] + S[j]) % 256])
    return bytes(out)

# ─────────────────────────────────────────────────────────────
# Decoder Varyantları
# ─────────────────────────────────────────────────────────────
# Her varyant aynı RC4 kriptografisini üretir ama JS'deki yapısal
# imzası farklıdır — AST/pattern tabanlı deobfuscator'lar tek imza
# bekleyerek yazılmışsa her deploy'da farklı şekil görür.
#
# VARIANT_ID → (py_encrypt_fn, js_decoder_builder_fn)
# py_encrypt_fn  : (data: bytes, key: bytes) -> bytes
# js_decoder_builder_fn : (arr, key_arr, dec, cache, key_name, pk) -> JS str
# ─────────────────────────────────────────────────────────────

def _td_expr(u8_var: str, cache: str, idx: str) -> str:
    """
    Uint8Array → string dönüşümünü 4 farklı JS formuyla üretir.
    Her çağrıda rastgele form seçilir — TextDecoder pattern'ı sabit kalmaz.

    Form A: inline new TextDecoder().decode(u8)
    Form B: explicit encoding new TextDecoder('utf-8').decode(u8)
    Form C: String.fromCharCode.apply — TextDecoder yok, legacy API
    Form D: buffer property fallback ile decode
    """
    td  = _rn("_td")
    arr = _rn("_ta")
    form = R.randint(0, 3)

    if form == 0:
        # Form A: inline, değişken yok
        return f"return {cache}[{idx}]=(new TextDecoder()).decode({u8_var});"

    elif form == 1:
        # Form B: explicit 'utf-8' parametresi
        return f"return {cache}[{idx}]=(new TextDecoder('utf-8')).decode({u8_var});"

    elif form == 2:
        # Form C: String.fromCharCode.apply — TextDecoder hiç yok
        # Uint8Array için: Array.from ile önce normal array'e çevir
        return (
            f"var {arr}=Array.prototype.slice.call({u8_var});"
            f"return {cache}[{idx}]=String.fromCharCode.apply(null,{arr});"
        )

    else:
        # Form D: named TextDecoder + buffer/u8 fallback
        return (
            f"var {td}=new TextDecoder();"
            f"return {cache}[{idx}]={td}.decode({u8_var}.buffer||{u8_var});"
        )

# ── Varyant A: RC4_CLASSIC ───────────────────────────────────
# Standart RC4. KSA temp-var swap, PRGA ayrı döngü, i2/j2 sayaçları.
# Mevcut yapı — referans noktası.

def _enc_classic(data: bytes, key: bytes) -> bytes:
    return _rc4_encrypt(data, key)

def _js_dec_classic(arr, key_arr, dec, cache, kname, poison_block, v_k, idx, ephemeral_wrapper):
    _i = _rn("_ci"); _j = _rn("_cj"); _t = _rn("_ct")
    _S = _rn("_cS"); _i2 = _rn("_ci2"); _j2 = _rn("_cj2")
    _n = _rn("_cn"); _u8 = _rn("_cu"); _e = _rn("_ce")
    
    # Ephemeral mode: cache check ve TTL set
    if ephemeral_wrapper:
        eph_check, eph_set_ttl = ephemeral_wrapper
    else:
        eph_check = f"if({cache}[{idx}]!==undefined)return {cache}[{idx}];"
        eph_set_ttl = ""
    
    return (
        f"var {cache}={{}};"
        f"var {kname}={key_arr};"
        f"function {dec}({idx}){{"
        f"{eph_check}"
        f"var {_e}=atob({arr}[{idx}]);"
        f"if(!{_e})return '';"  # atob başarısız → boş string dön
        f"var {v_k}={kname}[{idx}].slice();"
        f"{poison_block}"
        f"var {_S}=[];for(var {_i}=0;{_i}<256;{_i}++){_S}[{_i}]={_i};"
        f"var {_j}=0;"
        f"for(var {_i}=0;{_i}<256;{_i}++){{"
        f"{_j}=({_j}+{_S}[{_i}]+{v_k}[{_i}%{v_k}.length])%256;"
        f"var {_t}={_S}[{_i}];{_S}[{_i}]={_S}[{_j}];{_S}[{_j}]={_t};"
        f"}}"
        f"var {_i2}=0,{_j2}=0,{_u8}=new Uint8Array({_e}.length);"
        f"for(var {_n}=0;{_n}<{_e}.length;{_n}++){{"
        f"{_i2}=({_i2}+1)%256;{_j2}=({_j2}+{_S}[{_i2}])%256;"
        f"var {_t}={_S}[{_i2}];{_S}[{_i2}]={_S}[{_j2}];{_S}[{_j2}]={_t};"
        f"{_u8}[{_n}]={_e}.charCodeAt({_n})^{_S}[({_S}[{_i2}]+{_S}[{_j2}])%256];"
        f"}}"
        f"{eph_set_ttl}"
        + _td_expr(_u8, cache, idx)
        + f"}}"
    )

# ── Varyant B: RC4_SPLIT ─────────────────────────────────────
# KSA iki ayrı pass: önce key mixing, sonra index normalizasyonu.
# PRGA: i sayacını üst bitlerden, j sayacını alt bitlerden türetir
# (matematiksel olarak eşdeğer, yapısal imza farklı).

def _enc_split(data: bytes, key: bytes) -> bytes:
    # KSA aynı RC4 — split sadece JS'de
    return _rc4_encrypt(data, key)

def _js_dec_split(arr, key_arr, dec, cache, kname, poison_block, v_k, idx, ephemeral_wrapper):
    _S  = _rn("_sS");  _a  = _rn("_sa"); _b  = _rn("_sb")
    _x  = _rn("_sx");  _y  = _rn("_sy"); _p  = _rn("_sp")
    _q  = _rn("_sq");  _r  = _rn("_sr")
    _e  = _rn("_se");  _kl = _rn("_skl"); _u8 = _rn("_su8")
    
    if ephemeral_wrapper:
        eph_check, eph_set_ttl = ephemeral_wrapper
    else:
        eph_check = f"if({cache}[{idx}]!==undefined)return {cache}[{idx}];"
        eph_set_ttl = ""
    
    return (
        f"var {cache}={{}};"
        f"var {kname}={key_arr};"
        f"function {dec}({idx}){{"
        f"{eph_check}"
        f"var {_e}=atob({arr}[{idx}]);"
        f"if(!{_e})return '';"
        f"var {v_k}={kname}[{idx}].slice();"
        f"{poison_block}"
        f"var {_S}=new Uint8Array(256);"
        f"var {_kl}={v_k}.length;"
        f"for(var {_a}=0;{_a}<256;{_a}++){_S}[{_a}]={_a};"
        f"var {_b}=0;"
        f"for(var {_a}=0;{_a}<256;{_a}++){{"
        f"{_b}=({_b}+{_S}[{_a}]+{v_k}[{_a}%{_kl}])&0xFF;"
        f"{_x}={_S}[{_a}];{_S}[{_a}]={_S}[{_b}];{_S}[{_b}]={_x};"
        f"}}"
        f"var {_p}=0,{_q}=0,{_u8}=new Uint8Array({_e}.length);"
        f"for(var {_r}=0;{_r}<{_e}.length;{_r}++){{"
        f"{_p}=({_p}+1)&0xFF;{_q}=({_q}+{_S}[{_p}])&0xFF;"
        f"{_y}={_S}[{_p}];{_S}[{_p}]={_S}[{_q}];{_S}[{_q}]={_y};"
        f"{_u8}[{_r}]={_e}.charCodeAt({_r})^{_S}[({_S}[{_p}]+{_S}[{_q}])&0xFF];"
        f"}}"
        f"{eph_set_ttl}"
        + _td_expr(_u8, cache, idx)
        + f"}}"
    )

# ── Varyant C: RC4_INPLACE ───────────────────────────────────
# KSA: XOR-swap (temp değişken yok).
# PRGA: S-box lookup inline, output Array.push + join.
# Görsel yapı: swap satırı 3 yerine 1 satır, join/push pattern.

def _enc_inplace(data: bytes, key: bytes) -> bytes:
    return _rc4_encrypt(data, key)

def _js_dec_inplace(arr, key_arr, dec, cache, kname, poison_block, v_k, idx, ephemeral_wrapper):
    _S  = _rn("_nS");  _u  = _rn("_nu"); _v  = _rn("_nv")
    _m  = _rn("_nm");  _w  = _rn("_nw"); _g  = _rn("_ng")
    _ob = _rn("_nb");  _e  = _rn("_ne"); _td = _rn("_ntd")
    
    if ephemeral_wrapper:
        eph_check, eph_set_ttl = ephemeral_wrapper
    else:
        eph_check = f"if({cache}[{idx}]!==undefined)return {cache}[{idx}];"
        eph_set_ttl = ""
    
    return (
        f"var {cache}={{}};"
        f"var {kname}={key_arr};"
        f"function {dec}({idx}){{"
        f"{eph_check}"
        f"var {_e}=atob({arr}[{idx}]);"
        f"if(!{_e})return '';"
        f"var {v_k}={kname}[{idx}].slice();"
        f"{poison_block}"
        f"var {_S}=Array.from({{length:256}},function(_,z){{return z;}});"
        f"var {_v}=0;"
        f"for(var {_u}=0;{_u}<256;{_u}++){{"
        f"{_v}=({_v}+{_S}[{_u}]+{v_k}[{_u}%{v_k}.length])%256;"
        f"{_S}[{_u}]^={_S}[{_v}];{_S}[{_v}]^={_S}[{_u}];{_S}[{_u}]^={_S}[{_v}];"
        f"}}"
        f"var {_m}=0,{_w}=0,{_ob}=new Uint8Array({_e}.length);"
        f"for(var {_g}=0;{_g}<{_e}.length;{_g}++){{"
        f"{_m}=({_m}+1)%256;{_w}=({_w}+{_S}[{_m}])%256;"
        f"{_S}[{_m}]^={_S}[{_w}];{_S}[{_w}]^={_S}[{_m}];{_S}[{_m}]^={_S}[{_w}];"
        f"{_ob}[{_g}]={_e}.charCodeAt({_g})^{_S}[({_S}[{_m}]+{_S}[{_w}])%256];"
        f"}}"
        f"{eph_set_ttl}"
        + _td_expr(_ob, cache, idx)
        + f"}}"
    )

# ── Varyant D: SBOX_LITE ─────────────────────────────────────
# S-box init: S[i] = (i * PRIME + SEED) % 256 ile başlar (PRIME/SEED build-time random).
# KSA standart RC4 üzerinden devam eder — toplam init farklı başlangıç.
# Python encrypt: aynı init mantığını uygular.
# Yapısal imza: S-box init döngüsü çarpma içeriyor, klasik RC4'ten farklı.

_SBOX_PRIMES = [3, 5, 7, 11, 13, 17, 19, 23]  # küçük asal — mod 256 ile güvenli

def _enc_sbox(data: bytes, key: bytes, prime: int = 5, seed: int = 0) -> bytes:
    """S-box (i*prime+seed)%256 ile init, sonra standart RC4 KSA+PRGA."""
    S = [(i * prime + seed) % 256 for i in range(256)]
    j = 0
    for i in range(256):
        j = (j + S[i] + key[i % len(key)]) % 256
        S[i], S[j] = S[j], S[i]
    i = j = 0
    out = []
    for byte in data:
        i = (i + 1) % 256
        j = (j + S[i]) % 256
        S[i], S[j] = S[j], S[i]
        out.append(byte ^ S[(S[i] + S[j]) % 256])
    return bytes(out)

def _js_dec_sbox(arr, key_arr, dec, cache, kname, poison_block, v_k, idx, prime, seed, ephemeral_wrapper):
    _S  = _rn("_dS");  _f  = _rn("_df"); _z  = _rn("_dz")
    _t  = _rn("_dt");  _c  = _rn("_dc"); _d  = _rn("_dd")
    _q  = _rn("_dq");  _o  = _rn("_do"); _e  = _rn("_de")
    _pr = _num_expr(prime); _sd = _num_expr(seed)
    
    if ephemeral_wrapper:
        eph_check, eph_set_ttl = ephemeral_wrapper
    else:
        eph_check = f"if({cache}[{idx}]!==undefined)return {cache}[{idx}];"
        eph_set_ttl = ""
    
    return (
        f"var {cache}={{}};"
        f"var {kname}={key_arr};"
        f"function {dec}({idx}){{"
        f"{eph_check}"
        f"var {_e}=atob({arr}[{idx}]);"
        f"if(!{_e})return '';"
        f"var {v_k}={kname}[{idx}].slice();"
        f"{poison_block}"
        f"var {_S}=[];"
        f"for(var {_f}=0;{_f}<256;{_f}++){_S}[{_f}]=({_f}*{_pr}+{_sd})%256;"
        f"var {_z}=0;"
        f"for(var {_f}=0;{_f}<256;{_f}++){{"
        f"{_z}=({_z}+{_S}[{_f}]+{v_k}[{_f}%{v_k}.length])%256;"
        f"var {_t}={_S}[{_f}];{_S}[{_f}]={_S}[{_z}];{_S}[{_z}]={_t};"
        f"}}"
        f"var {_c}=0,{_d}=0,{_o}=new Uint8Array({_e}.length);"
        f"for(var {_q}=0;{_q}<{_e}.length;{_q}++){{"
        f"{_c}=({_c}+1)%256;{_d}=({_d}+{_S}[{_c}])%256;"
        f"var {_t}={_S}[{_c}];{_S}[{_c}]={_S}[{_d}];{_S}[{_d}]={_t};"
        f"{_o}[{_q}]={_e}.charCodeAt({_q})^{_S}[({_S}[{_c}]+{_S}[{_d}])%256];"
        f"}}"
        f"{eph_set_ttl}"
        + _td_expr(_o, cache, idx)
        + f"}}"
    )

# ── Varyant dispatch tablosu ──────────────────────────────────
# Her varyant: (id, py_encrypt, js_decoder_builder)
# SBOX varyantı prime/seed parametresi ekstra alır — build_cipher_variant içinde kapanır.

def _pick_cipher_variant() -> tuple:
    """
    Build başına rastgele bir cipher varyantı seçer.
    Returns: (variant_id, encrypt_fn, js_decoder_fn)
      encrypt_fn(data: bytes, key: bytes) -> bytes
      js_decoder_fn(arr, key_arr, dec, cache, kname, poison_block, v_k, idx, ephemeral_wrapper) -> str
    """
    prime = R.choice(_SBOX_PRIMES)
    seed  = R.randint(0, 31)

    variants = [
        ("RC4_CLASSIC",  _enc_classic,  _js_dec_classic),
        ("RC4_SPLIT",    _enc_split,    _js_dec_split),
        ("RC4_INPLACE",  _enc_inplace,  _js_dec_inplace),
        # SBOX: prime/seed kapanıyor, dışarıya aynı imza
        ("SBOX_LITE",
         lambda data, key: _enc_sbox(data, key, prime, seed),
         lambda arr, key_arr, dec, cache, kname, pb, vk, idx, eph:
             _js_dec_sbox(arr, key_arr, dec, cache, kname, pb, vk, idx, prime, seed, eph)),
    ]
    choice = R.choice(variants)
    return choice   # (id, encrypt_fn, js_decoder_fn)

def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _aes_gcm_encrypt_string(plaintext: str, key: bytes, iv: bytes) -> bytes:
    """
    AES-256-GCM ile string şifreler.
    Returns: ciphertext (authentication tag dahil)
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    aesgcm = AESGCM(key)
    pt_bytes = plaintext.encode("utf-8")
    # encrypt(nonce, plaintext, associated_data)
    ciphertext = aesgcm.encrypt(iv, pt_bytes, None)
    return ciphertext


def build_aes_gcm_seed() -> tuple[str, str, str]:
    """
    Python'da AES-256-GCM ile bir tohum değer şifreler.
    JS'de crypto.subtle.decrypt (native API) ile çözülür.

    Strateji (hibrit):
    - RC4 decoder'ları dokunulmaz kalır (sync, mevcut çağrı zinciri kırılmaz)
    - AES-GCM sadece bir "unlock seed" üretmek için kullanılır
    - Bu seed, RC4 key derivation'ına 3. bir XOR katmanı olarak eklenir
    - Analist RC4 KSA/PRGA pattern'ını tanısa bile seed olmadan key'ler bozuk kalır
    - crypto.subtle native code olduğu için toString() tespiti de kapanır

    **FAIL-CLOSED:** Seed Promise ile loader init async — timeout içinde seed gelmezse REJECT.

    Returns:
        (js_unlock_code, seed_global_name, seed_ready_promise_name)
        js_unlock_code        : loader'ın başına eklenen async unlock IIFE'si
        seed_global_name      : seed'in yazıldığı window global adı
        seed_ready_promise_name : seed ready Promise global adı (loader init await eder)
    """
    # Build-time AES-256-GCM şifreleme
    aes_key_bytes  = secrets.token_bytes(32)        # 256-bit key
    iv_bytes       = secrets.token_bytes(12)         # 96-bit IV (GCM standard)
    seed_plain     = secrets.token_bytes(16)         # 128-bit seed — RC4 XOR katmanı

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    aesgcm       = AESGCM(aes_key_bytes)
    ciphertext   = aesgcm.encrypt(iv_bytes, seed_plain, None)  # last param = AAD (None)
    # ciphertext = şifreli_veri (16B) + auth_tag (16B) = 32B

    # JS'e gömülecek değerler (base64)
    aes_key_b64  = _b64(aes_key_bytes)   # build-time key — statik analiz RC4'ü çözmek zorunda
    iv_b64       = _b64(iv_bytes)
    ct_b64       = _b64(ciphertext)

    # Global isim — build başına random, sabit pattern yok
    seed_global  = "__wasd_seed_" + _rhex(4)
    seed_ready_promise = "__wasd_sr_" + _rhex(4)  # seed ready Promise global

    # JS değişken isimleri
    v_key_data = _rn("_agk")
    v_iv_data  = _rn("_agi")
    v_ct_data  = _rn("_agc")
    v_key_obj  = _rn("_ako")
    v_pt       = _rn("_apt")
    v_seed_hex = _rn("_ash")
    v_err      = _rn("_age")
    v_resolve  = _rn("_srs")   # seed ready resolve
    v_reject   = _rn("_srj")   # seed ready reject

    # Async IIFE: crypto.subtle ile AES-GCM decrypt, seed'i global'e yaz
    # **FAIL-CLOSED:** Seed Promise — success: resolve, failure/timeout: reject
    js_unlock = (
        f"(function(){{"
        # Seed ready Promise — loader init bu Promise'i await edecek
        f"window['{seed_ready_promise}']=new Promise(function({v_resolve},{v_reject}){{"
        f"var _to=setTimeout(function(){{{v_reject}(new Error('aes_seed_timeout'));}},5000);"
        f"try{{"
        f"if(typeof crypto==='undefined'||!crypto.subtle){{"
        f"clearTimeout(_to);{v_reject}(new Error('crypto_unavailable'));return;"
        f"}}"
        # atob sonuçlarını validate et
        f"var _kr='{aes_key_b64}',_ir='{iv_b64}',_cr='{ct_b64}';"
        f"if(!_kr||!_ir||!_cr){{"
        f"clearTimeout(_to);{v_reject}(new Error('aes_data_missing'));return;"
        f"}}"
        f"var {v_key_data}=Uint8Array.from(atob(_kr),function(c){{return c.charCodeAt(0);}});"
        f"var {v_iv_data}=Uint8Array.from(atob(_ir),function(c){{return c.charCodeAt(0);}});"
        f"var {v_ct_data}=Uint8Array.from(atob(_cr),function(c){{return c.charCodeAt(0);}});"
        f"if({v_key_data}.length===0||{v_iv_data}.length===0||{v_ct_data}.length===0){{"
        f"clearTimeout(_to);{v_reject}(new Error('aes_data_empty'));return;"
        f"}}"
        # Promise chain
        f"var _prom=crypto.subtle['importKey']('raw',{v_key_data},{{'name':'AES-GCM'}},false,['decrypt']);"
        f"var _then=Function.prototype.call.bind(Promise.prototype['then']);"
        f"var _ctch=Function.prototype.call.bind(Promise.prototype['catch']);"
        f"_ctch(_then(_then(_prom,function({v_key_obj}){{"
        f"return crypto.subtle['decrypt']({{'name':'AES-GCM','iv':{v_iv_data}}},{v_key_obj},{v_ct_data});"
        f"}}),function({v_pt}){{"
        f"var {v_seed_hex}=Array.prototype.map.call(new Uint8Array({v_pt}),"
        f"function(b){{return ('0'+b.toString(16)).slice(-2);}}).join('');"
        f"if(typeof window!=='undefined')window['{seed_global}']={v_seed_hex};"
        f"clearTimeout(_to);"
        f"{v_resolve}({v_seed_hex});"  # SUCCESS: resolve with seed
        f"}}),function({v_err}){{"
        f"clearTimeout(_to);"
        f"{v_reject}({v_err});"  # FAILURE: reject
        f"}});"
        f"}}catch({v_err}){{"
        f"clearTimeout(_to);"
        f"{v_reject}({v_err});"
        f"}}"
        f"}});"
        f"}})();"
    )

    return js_unlock, seed_global, seed_ready_promise

def build_capability_wrapper(key_value: str, global_name: str) -> str:
    """
    Closure-based capability wrapper üretir.
    
    key_value: gerçek nonce/session_key değeri (server-side)
    global_name: capability fonksiyonunun atanacağı window property adı
    
    Returns: inline <script> içeriği — template'e inject edilir
    
    Pattern:
      (function(){
        var _k = "actual_key_value";  // closure içinde saklanır
        var _used = false;              // one-time-use flag
        window['_wasd_nc_XXXX'] = function(){
          if(_used) return null;
          _used = true;
          var _ret = _k;
          _k = null;  // referansı temizle
          return _ret;
        };
      })();
    
    DevTools'ta window._wasd_nc_XXXX görünür ama çağrıldıktan sonra
    _k = null olur, ikinci çağrıda null döner. Global property taraması
    key'i bulamaz — sadece fonksiyon referansı görülür.
    """
    # Basit değişken adları — obfuscator zaten sonra işleyecek
    return (
        f"(function(){{"
        f"var _k={json.dumps(key_value)};"
        f"var _u=false;"
        f"window['{global_name}']=function(){{"
        f"if(_u)return null;"
        f"_u=true;"
        f"var _r=_k;"
        f"_k=null;"
        f"return _r;"
        f"}};"
        f"}})();"
    )


def _derive_key_js(base_key_var: str, nonce_cap_name: str, session_cap_name: str, nonce_global: str = "__WASDW_PAGE_NONCE") -> tuple[str, str, str]:
    """
    JS'de iki katmanlı key derivation ifadesi üretir.

    YENİ: Closure-based capability pattern — window global yerine WeakMap.
    
    Katman 1 — page_nonce:
      Sunucu her sayfa render'ında farklı set eder. Statik dosya analizi yetersiz.
      window.__WASDW_PAGE_NONCE yerine: closure içinde saklanır, tek bir
      WeakMap reference ile erişilir. DevTools window taraması bulamaz.

    Katman 2 — session_key:
      Sunucu PoW + VM + bot check zinciri geçildikten SONRA verify-challenge
      response'unda döner. part4 bunu WeakMap'e yazar.
      Offline analiz + local replay tamamen engellenir: session_key olmadan
      key'ler bozuk XOR'lanır (fail-closed).

    FAIL-CLOSED: Her iki key de zorunlu. Biri eksikse key bozulur, decode çöp.
    
    Capability pattern:
      - Nonce ve session_key closure içinde saklanır
      - WeakMap ile anonymous object reference'ı eşleştirilir
      - getKey() bir kerelik çağrılabilir, sonra referans null'lanır
      - Global window property taraması engellenmiş olur
      
    Returns:
      (js_code, nonce_capability_global, session_capability_global)
    """
    v_nb   = _rn("_nb")    # page_nonce
    v_sk   = _rn("_sk")    # session_key
    v_zero = _rn("_nz")    # bozucu sabit — herhangi biri null ise kullanılır
    v_cap  = _rn("_cap")   # capability object
    v_wm   = _rn("_wm")    # WeakMap
    v_get  = _rn("_get")   # getKey fonksiyonu
    v_used = _rn("_usd")   # one-time-use flag
    
    # Capability global adları parametre olarak gelir (inject_pre başında üretildi)
    nonce_cap_global = nonce_cap_name
    session_cap_global = session_cap_name

    code = (
        # ── Katman 1: page_nonce capability ───────────────────────────────
        # Nonce closure içinde saklanır, WeakMap ile erişilir
        f"var {v_nb}=null;"
        f"if(typeof window!=='undefined'&&window['{nonce_cap_global}']){{"
        f"try{{"
        f"var {v_get}=window['{nonce_cap_global}'];"
        f"if(typeof {v_get}==='function'){{{v_nb}={v_get}();delete window['{nonce_cap_global}'];}}"
        f"}}catch(_e){{console.warn('[WASDW] nonce capability error:',_e);}}"
        f"}}"
        f"if(!{v_nb}){{console.warn('[WASDW] page_nonce capability not found or returned null');}}"
        f"if({v_nb}&&{v_nb}.length){{"
        f"{base_key_var}={base_key_var}.map(function(_b,_i){{"
        f"return ({_num_expr(0xFF)}&(_b^{v_nb}.charCodeAt(_i%{v_nb}.length)));"
        f"}});"
        f"}}else{{"
        # Nonce yoksa bozucu sabit
        f"var {v_zero}={_num_expr(R.randint(0x3F, 0xC0))};"
        f"{base_key_var}={base_key_var}.map(function(_b){{return (_b^{v_zero})&{_num_expr(0xFF)};}})"
        f".reverse();"
        f"}}"
        
        # ── Katman 2: session_key capability ──────────────────────────────
        # Part4 verify-challenge sonrasında session_key'i WeakMap'e yazar
        f"var {v_sk}=null;"
        f"if(typeof window!=='undefined'&&window['{session_cap_global}']){{"
        f"try{{"
        f"var {v_get}=window['{session_cap_global}'];"
        f"if(typeof {v_get}==='function'){{{v_sk}={v_get}();delete window['{session_cap_global}'];}}"
        f"}}catch(_e){{}}"
        f"}}"
        f"if({v_sk}&&{v_sk}.length){{"
        f"{base_key_var}={base_key_var}.map(function(_b,_i){{"
        f"return ({_num_expr(0xFF)}&(_b^{v_sk}.charCodeAt(_i%{v_sk}.length)));"
        f"}});"
        f"}}else{{"
        # Session key yoksa ayrı bozucu sabit
        f"var {v_zero}={_num_expr(R.randint(0x40, 0xDF))};"
        f"{base_key_var}={base_key_var}.map(function(_b){{return (_b^{v_zero})&{_num_expr(0xFF)};}})"
        f".reverse();"
        f"}}"
    )
    
    return (code, nonce_cap_global, session_cap_global)


def _aes_gcm_js_decoder(arr_name: str, keys_b64_list: list, iv_b64_list: list,
                        dec_name: str, cache_name: str, 
                        nonce_cap_name: str = "", session_cap_name: str = "",
                        ephemeral_mode: bool = False) -> str:
    """
    AES-256-GCM based async string decoder — crypto.subtle native.
    
    RC4 yerine tamamen SubtleCrypto API kullanır:
      - AES-256-GCM (native browser implementation)
      - Per-string key + IV (her string farklı key)
      - Async Promise chain (sync sorun yok)
      - Native code → toString() hook detection engellenir
    
    Returns: JS decoder function code (async function)
    """
    idx = _rn("i")
    v_key_data = _rn("_kd")
    v_iv_data = _rn("_id")
    v_ct_data = _rn("_cd")
    v_key_obj = _rn("_ko")
    v_pt = _rn("_pt")
    v_td = _rn("_td")
    v_derived = _rn("_drv")
    
    # Keys ve IVs'ları JS array literal olarak gömme
    keys_js = "[" + ",".join(f"'{k}'" for k in keys_b64_list) + "]"
    ivs_js = "[" + ",".join(f"'{iv}'" for iv in iv_b64_list) + "]"
    
    # Nonce/session key derivation (capability pattern)
    nonce_block = ""
    if nonce_cap_name and session_cap_name:
        v_nb = _rn("_nb")
        v_sk = _rn("_sk")
        v_get = _rn("_get")
        
        nonce_block = (
            # Nonce capability
            f"var {v_nb}=null;"
            f"if(typeof window!=='undefined'&&window['{nonce_cap_name}']){{"
            f"try{{var {v_get}=window['{nonce_cap_name}'];"
            f"if(typeof {v_get}==='function'){{{v_nb}={v_get}();delete window['{nonce_cap_name}'];}}"
            f"}}catch(_e){{}}"
            f"}}"
            # Session capability
            f"var {v_sk}=null;"
            f"if(typeof window!=='undefined'&&window['{session_cap_name}']){{"
            f"try{{var {v_get}=window['{session_cap_name}'];"
            f"if(typeof {v_get}==='function'){{{v_sk}={v_get}();delete window['{session_cap_name}'];}}"
            f"}}catch(_e){{}}"
            f"}}"
            # Key derivation function
            f"var {v_derived}=function(baseKey){{"
            f"var arr=Uint8Array.from(atob(baseKey),function(c){{return c.charCodeAt(0);}});"
            # XOR with nonce
            f"if({v_nb}&&{v_nb}.length){{"
            f"for(var _i=0;_i<arr.length;_i++)"
            f"arr[_i]^={v_nb}.charCodeAt(_i%{v_nb}.length);"
            f"}}"
            # XOR with session key
            f"if({v_sk}&&{v_sk}.length){{"
            f"for(var _i=0;_i<arr.length;_i++)"
            f"arr[_i]^={v_sk}.charCodeAt(_i%{v_sk}.length);"
            f"}}"
            f"return arr;"
            f"}};"
        )
    
    # Ephemeral mode: TTL logic
    ephemeral_setup = ""
    ephemeral_check = ""
    ephemeral_set_ttl = ""
    
    if ephemeral_mode:
        v_now = _rn("_now")
        v_age = _rn("_age")
        # Agresif TTL: 1-2 saniye (eskiden 2-5s)
        # Breakpoint attack window minimize edildi
        ttl_ms = R.randint(1000, 2000)
        ttl_expr = _num_expr(ttl_ms)
        
        ephemeral_setup = f"if(!{cache_name}['_ttl']){cache_name}['_ttl']={{}};"
        
        ephemeral_check = (
            f"if({cache_name}[{idx}]!==undefined&&{cache_name}['_ttl'][{idx}]){{"
            f"var {v_now}=Date.now();"
            f"var {v_age}={v_now}-{cache_name}['_ttl'][{idx}];"
            f"if({v_age}>{ttl_expr}){{"
            f"delete {cache_name}[{idx}];"
            f"delete {cache_name}['_ttl'][{idx}];"
            f"}}else{{return Promise.resolve({cache_name}[{idx}]);}}"
            f"}}"
        )
        
        ephemeral_set_ttl = f"{cache_name}['_ttl'][{idx}]=Date.now();"
    else:
        ephemeral_check = f"if({cache_name}[{idx}]!==undefined)return Promise.resolve({cache_name}[{idx}]);"
    
    # AES-GCM async decoder
    code = (
        f"var {cache_name}={{}};"
        f"{ephemeral_setup}"
        f"var _keys={keys_js};"
        f"var _ivs={ivs_js};"
        f"{nonce_block}"
        
        # Async decoder function
        f"async function {dec_name}({idx}){{"
        f"{ephemeral_check}"
        
        # Base64 decode key, IV, ciphertext
        f"var {v_key_data}="
        f"({v_derived}?{v_derived}(_keys[{idx}]):Uint8Array.from(atob(_keys[{idx}]),function(c){{return c.charCodeAt(0);}}));"
        f"var {v_iv_data}=Uint8Array.from(atob(_ivs[{idx}]),function(c){{return c.charCodeAt(0);}});"
        f"var {v_ct_data}=Uint8Array.from(atob({arr_name}[{idx}]),function(c){{return c.charCodeAt(0);}});"
        
        # crypto.subtle.importKey + decrypt
        f"try{{"
        f"var {v_key_obj}=await crypto.subtle.importKey("
        f"'raw',{v_key_data},{{'name':'AES-GCM'}},false,['decrypt']);"
        f"var {v_pt}=await crypto.subtle.decrypt("
        f"{{'name':'AES-GCM','iv':{v_iv_data}}},{v_key_obj},{v_ct_data});"
        
        # TextDecoder
        f"var {v_td}=new TextDecoder();"
        f"var _result={v_td}.decode({v_pt});"
        
        # Cache + TTL
        f"{ephemeral_set_ttl}"
        f"{cache_name}[{idx}]=_result;"
        f"return _result;"
        
        # Fallback on error
        f"}}catch(_e){{"
        f"return '';"
        f"}}"
        f"}}"
    )
    
    return code


def _rc4_js_decoder(arr_name: str, keys_b64_list: list, dec_name: str, cache_name: str, key_name: str,
                    poison_var: str = "", cipher_variant: tuple | None = None,
                    nonce_cap_name: str = "", session_cap_name: str = "",
                    ephemeral_mode: bool = False) -> tuple[str, callable]:
    """
    Per-string key decoder — varyant-aware + ephemeral mode.

    ephemeral_mode : True ise cache TTL ve use-once logic aktif.
                     Decode edilen değer kısa süre sonra cache'ten silinir.
                     Breakpoint'te yakalanan değer sadece o an geçerli.

    cipher_variant : _pick_cipher_variant() çıktısı (id, encrypt_fn, js_fn).
                     None ise build başında otomatik seçilir.
    Returns        : (js_code: str, encrypt_fn: callable)
                     Çağıran, encrypt_fn'i Python tarafında şifreleme için kullanır.
    """
    if cipher_variant is None:
        cipher_variant = _pick_cipher_variant()
    _vid, encrypt_fn, js_decoder_fn = cipher_variant

    idx    = _rn("i")
    v_k    = _rn("_rk")
    v_mask = _rn("_pm")

    # Per-index poison mask: her string için farklı mask türetilir.
    # Sabit seed ve prime build'e gömülür, idx runtime'da her call'da farklıdır.
    # mask = (SEED ^ (idx * PRIME + SALT)) & 0xFF
    # → tek bir string'i reverse etmek diğerlerini çözmez.
    pm_seed  = R.randint(0x10, 0xFE)          # build-time sabit seed
    pm_prime = R.choice([3, 5, 7, 11, 13, 17, 19, 23])  # küçük asal — çarpma geri sarım
    pm_salt  = R.randint(0x01, 0x7F)          # ek kayma

    # JS içinde key array: [[byte,...], ...]
    keys_js_arr = "[" + ",".join(
        "[" + ",".join(str(b) for b in base64.b64decode(k)) + "]"
        for k in keys_b64_list
    ) + "]"

    # Poison bloğu: mask her decode çağrısında idx'e bağlı türetilir
    if poison_var:
        poison_block = (
            f"if({poison_var}){{"
            f"var {v_mask}=({_num_expr(pm_seed)}^(({idx}*{_num_expr(pm_prime)}+{_num_expr(pm_salt)})&0xFF))&0xFF;"
            f"for(var _pi=0;_pi<{v_k}.length;_pi++){{{v_k}[_pi]=({v_k}[_pi]^{v_mask})&0xFF;}}"
            f"}}"
        )
    else:
        poison_block = ""

    # Nonce derivation: poison bloğundan SONRA eklenir
    # Gerçek key = (poisoned_key) XOR nonce — statik analiz + offline replay engelliyor
    nonce_block, nonce_cap_global, session_cap_global = _derive_key_js(v_k, nonce_cap_name, session_cap_name)
    poison_block = poison_block + nonce_block

    # ── Ephemeral Mode: Cache TTL + Use-Once ──────────────────────────────
    # Decode edilen değer kısa süre sonra cache'ten silinir.
    # Breakpoint'te yakalanan değer sadece o an geçerli.
    ephemeral_wrapper = ""
    if ephemeral_mode:
        v_ttl = _rn("_ttl")        # TTL storage: {idx: timestamp}
        v_now = _rn("_now")        # current timestamp
        v_age = _rn("_age")        # cache age
        # Agresif TTL: 1-2 saniye (eskiden 2-5s)
        # Breakpoint attack window minimize edildi
        ttl_ms = R.randint(1000, 2000)
        ttl_expr = _num_expr(ttl_ms)
        
        # Ephemeral cache check: cache var MI ve süresi geçmemiş MI?
        ephemeral_check = (
            f"if(!{cache_name}['_ttl']){cache_name}['_ttl']={{}};"
            f"var {v_now}=Date.now();"
            f"if({cache_name}[{idx}]!==undefined&&{cache_name}['_ttl'][{idx}]){{"
            f"var {v_age}={v_now}-{cache_name}['_ttl'][{idx}];"
            # TTL geçmişse cache'i sil
            f"if({v_age}>{ttl_expr}){{"
            f"delete {cache_name}[{idx}];"
            f"delete {cache_name}['_ttl'][{idx}];"
            f"}}else{{"
            f"return {cache_name}[{idx}];"
            f"}}"
            f"}}"
        )
        
        # Decode sonrası TTL kaydı
        ephemeral_set_ttl = (
            f"{cache_name}['_ttl'][{idx}]={v_now};"
        )
        
        ephemeral_wrapper = (
            ephemeral_check,
            ephemeral_set_ttl
        )
    
    js_code = js_decoder_fn(arr_name, keys_js_arr, dec_name, cache_name, key_name,
                            poison_block, v_k, idx, ephemeral_wrapper)
    return js_code, encrypt_fn


def build_wrapper_chain(inner_code: str, depth: int = 3) -> str:
    """
    obfuscator.io imzası: iç içe (function(_p1,_p2,_p3,_p4,_p5){...})(a,b,c,d,e) çağrıları.
    Her katman bir öncekine parametre geçirir — AI'lar context'i kaybeder.
    """
    code = inner_code
    for _ in range(depth):
        params = [_rn("p") for _ in range(5)]
        args   = [
            "window", "document",
            str(R.randint(0x100, 0xFFF)),
            f"'{_rhex(4)}'",
            str(R.randint(0, 0xFF)),
        ]
        R.shuffle(args)
        p_list = ",".join(params)
        a_list = ",".join(args)
        dead = build_dead_code(R.randint(1, 2))
        code = f"(function({p_list}){{{dead}{code}}}({a_list}));"
    return code

def build_self_defending_loop() -> str:
    """Kendini yeniden yazan anti-tamper döngüsü."""
    fn  = _rn("_sd")
    cnt = _rn("_cnt")
    lim = R.randint(3, 7)
    return (
        f"(function(){{"
        f"var {cnt}=0;"
        f"function {fn}(){{"
        f"var _t0=performance.now();"
        f"debugger;"
        f"var _t1=performance.now();"
        f"if(_t1-_t0>{R.randint(80,140)}){{"
        f"(function _inf(){{debugger;setTimeout(_inf,{R.randint(30,60)});}})();"
        f"return;"
        f"}}"
        f"if(++{cnt}<{lim})setTimeout({fn},{R.randint(400,800)});"
        f"}}"
        f"try{{{fn}();}}catch(_e){{}}"
        f"}})();"
    )

def build_string_array(strings: list, poison_var: str = "",
                       nonce_cap_name: str = "", session_cap_name: str = "",
                       ephemeral_mode: bool = False,
                       use_aes_gcm: bool = False) -> tuple[str, str]:
    """
    String array builder — dual-group decoder + ephemeral cache support.
    
    use_aes_gcm: True ise AES-256-GCM (crypto.subtle) kullanır.
                 False ise RC4 variants (legacy).
                 
    AES-GCM Avantajları:
      - Native browser crypto (toString() hook engellenir)
      - Async Promise chain (sync sorun yok)
      - Authentication tag (integrity check built-in)
      - Per-string key + IV (daha güvenli)
    
    ephemeral_mode: True ise cache TTL aktif (1-2 saniye).
                    Decode edilen stringler kısa süre sonra cache'ten silinir.
    """
    if not strings:
        return "", ""

    # ── İki gruba böl — her grup farklı cipher varyant, farklı decoder ──────
    # Saldırgan bir decoder'ı kırınca sadece o grubun string'lerine ulaşır.
    # Minimum grup boyutu 1 — tek string bile farklı decoder'a düşebilir.
    n = len(strings)

    if n == 1:
        # Tek string: normal akış
        groups = [[0]]
    else:
        # Her string rastgele A veya B grubuna gider
        # En az 1 string her grupta olması için: ilk ve son string farklı gruplara atanır
        group_a_indices = [0]   # ilk string A'ya
        group_b_indices = [n-1] # son string B'ye
        for i in range(1, n - 1):
            if R.random() < 0.5:
                group_a_indices.append(i)
            else:
                group_b_indices.append(i)
        groups = [sorted(group_a_indices), sorted(group_b_indices)]

    # Mapping: global_idx → (group_id, local_idx)
    # dispatch_map[global_idx] = (g, local) — gömülü sayı çifti olarak
    dispatch_map = {}
    for g, idxs in enumerate(groups):
        for local, global_i in enumerate(idxs):
            dispatch_map[global_i] = (g, local)

    group_codes = []
    dec_names   = []
    cipher_variants_used = []

    for g, idxs in enumerate(groups):
        arr_name  = _rn("A")
        dec_name  = _rn("d")
        cache_nm  = _rn("c")

        group_strings = [strings[i] for i in idxs]
        m = len(group_strings)

        if use_aes_gcm:
            # ── AES-GCM Path ──────────────────────────────────────────────
            # Per-string key (32 bytes) + IV (12 bytes)
            per_str_keys = [secrets.token_bytes(32) for _ in group_strings]
            per_str_ivs = [secrets.token_bytes(12) for _ in group_strings]
            keys_b64 = [_b64(k) for k in per_str_keys]
            ivs_b64 = [_b64(iv) for iv in per_str_ivs]
            
            # AES-GCM encrypt
            enc_strs = [
                _b64(_aes_gcm_encrypt_string(s, k, iv))
                for s, k, iv in zip(group_strings, per_str_keys, per_str_ivs)
            ]
            
            arr_lit = "[" + ",".join(f'"{e}"' for e in enc_strs) + "]"
            
            # AES-GCM decoder (async)
            decoder_js = _aes_gcm_js_decoder(
                arr_name, keys_b64, ivs_b64, dec_name, cache_nm,
                nonce_cap_name=nonce_cap_name, session_cap_name=session_cap_name,
                ephemeral_mode=ephemeral_mode
            )
            
            # AES-GCM'de rotation yok (keys/ivs separate arrays)
            shuffle_code = ""
            
        else:
            # ── RC4 Path (Legacy) ─────────────────────────────────────────
            rot_name  = _rn("r")
            key_name  = _rn("k")
            
            # Her grup bağımsız cipher varyant seçer
            cv = _pick_cipher_variant()
            _, encrypt_fn, _ = cv
            cipher_variants_used.append(cv)

            per_str_keys = [secrets.token_bytes(16) for _ in group_strings]
            keys_b64     = [_b64(k) for k in per_str_keys]
            enc_strs     = [_b64(encrypt_fn(s.encode("utf-8"), k)) for s, k in zip(group_strings, per_str_keys)]

            arr_lit = "[" + ",".join(f'"{e}"' for e in enc_strs) + "]"

            # Çift rotation
            if m > 1:
                r1 = R.randint(1, m - 1)
                r2 = R.randint(1, m - 1)
                if r2 == r1:
                    r2 = (r1 % (m - 1)) + 1
            else:
                r1 = r2 = 0

            rot_name2 = _rn("_r2")
            shuffle_code = (
                f"(function(){{"
                f"var {rot_name}={r1};"
                f"var _f=function(){{{arr_name}.push({arr_name}.shift());{key_name}.push({key_name}.shift());}};"
                f"while({rot_name}-->0)_f();"
                f"var {rot_name2}={r2};"
                f"while({rot_name2}-->0)_f();"
                f"}})();"
            )

            decoder_js, _ = _rc4_js_decoder(
                arr_name, keys_b64, dec_name, cache_nm, key_name,
                poison_var=poison_var, cipher_variant=cv,
                nonce_cap_name=nonce_cap_name, session_cap_name=session_cap_name,
                ephemeral_mode=ephemeral_mode
            )

        group_codes.append(f"var {arr_name}={arr_lit};\n{decoder_js}\n{shuffle_code}")
        dec_names.append(dec_name)

    # ── Dispatch wrapper ───────────────────────────────────────────────────────
    # dec(global_idx) → dispatch_map'ten ilgili decoder'ı çağırır
    # AES-GCM mode: async function (Promise döner)
    # RC4 mode: sync function (direkt return)
    master_dec = _rn("d")
    v_gids = _rn("_gm")  # group ids array
    v_lids = _rn("_lm")  # local ids array
    v_idx  = _rn("_di")

    gids = [dispatch_map[i][0] for i in range(n)]
    lids = [dispatch_map[i][1] for i in range(n)]

    gids_js = "[" + ",".join(str(g) for g in gids) + "]"
    lids_js = "[" + ",".join(str(l) for l in lids) + "]"

    # Decoder dispatch: groups[0] → dec_names[0], groups[1] → dec_names[1]
    if len(dec_names) == 1:
        # Tek grup (n==1 veya degenerate): doğrudan çağır
        dispatch_body = f"return {dec_names[0]}({v_idx});"
    else:
        dispatch_body = (
            f"var {v_gids}={gids_js};"
            f"var {v_lids}={lids_js};"
            f"var _g={v_gids}[{v_idx}];"
            f"var _l={v_lids}[{v_idx}];"
            + "".join(
                f"if(_g==={g})return {dec_names[g]}(_l);"
                for g in range(len(dec_names))
            )
        )

    # AES-GCM async wrapper
    if use_aes_gcm:
        wrapper = f"async function {master_dec}({v_idx}){{{dispatch_body}}}"
    else:
        wrapper = f"function {master_dec}({v_idx}){{{dispatch_body}}}"

    code = "\n".join(group_codes) + "\n" + wrapper
    return code, master_dec
    obj = _rn("P")
    pairs = []
    ops = [
        ("eq",   "function(_a,_b){return _a===_b;}"),
        ("neq",  "function(_a,_b){return _a!==_b;}"),
        ("add",  "function(_a,_b){return _a+_b;}"),
        ("sub",  "function(_a,_b){return _a-_b;}"),
        ("mul",  "function(_a,_b){return _a*_b;}"),
        ("lt",   "function(_a,_b){return _a<_b;}"),
        ("gt",   "function(_a,_b){return _a>_b;}"),
        ("and",  "function(_a,_b){return _a&&_b;}"),
        ("or",   "function(_a,_b){return _a||_b;}"),
        ("not",  "function(_a){return !_a;}"),
        ("call", "function(_f,_a){return _f(_a);}"),
        ("call2","function(_f,_a,_b){return _f(_a,_b);}"),
    ]
    R.shuffle(ops)
    used = ops[:R.randint(6, len(ops))]
    for name, impl in used:
        k = _rn("k")
        pairs.append(f"'{k}':{impl}")
    prop_map = {name: k for (name, _), k in zip(used, [p.split("'")[1] for p in pairs])}
    obj_lit = "{" + ",".join(pairs) + "}"
    return f"var {obj}={obj_lit};", obj, prop_map

def build_opaque_predicates(n: int = 3) -> str:
    """
    Opaque predicate üretir.
    Dead-value formatı her branch'te rastgele çeşitlendirilir:
      0 → hex string (eski davranış)
      1 → tam sayı
      2 → küçük byte array
      3 → inline IIFE sayısal sonuç
    Böylece ardışık dead-branch'ler farklı tip/uzunlukta değerler taşır —
    "82 adet 8-char hex blob birleştirilince RC4 key" hipotezi kurulamaz.
    """
    out = []
    for _ in range(n):
        a, b = R.randint(2, 15), R.randint(2, 15)
        c = a * b
        dv = _rn("_x")

        # Dead-value formatını çeşitlendir
        dead_val_form = R.randint(0, 3)
        if dead_val_form == 0:
            dead_val = f"'{_rhex(R.randint(2, 5))}'"         # 4–10 char hex string
        elif dead_val_form == 1:
            dead_val = str(R.randint(0, 0xFFFFFF))            # plain integer
        elif dead_val_form == 2:
            arr_len = R.randint(2, 5)
            dead_val = "[" + ",".join(str(R.randint(0, 255)) for _ in range(arr_len)) + "]"
        else:
            # inline IIFE returning num_expr result
            dead_val = f"(function(){{return {_num_expr(R.randint(1, 999))};}})()"

        forms = [
            f"if({_num_expr(a)}*{_num_expr(b)}==={_num_expr(c)}&&typeof String==='function'){{}}else{{var {dv}={dead_val};}}",
            f"if(typeof window!=='undefined'&&typeof document!=='undefined'){{}}else{{var {dv}={dead_val};}}",
            f"if(!({_num_expr(a)}*{_num_expr(b)}!=={_num_expr(c)}||typeof undefined==='number')){{}}else{{var {dv}={dead_val};}}",
        ]
        out.append(R.choice(forms))
    return "".join(out)

def build_dead_code(n: int = 3, nt_name: str = "") -> str:
    """
    Anlamsız dead code üretir.
    nt_name verilirse number table lookup'ları da ekler —
    böylece number table 'kullanılmayan değişken' olarak işaretlenemez.
    """
    out = []
    for idx in range(n):
        v = _rn("_d")
        a = _ri(1, 0xFF); b = _ri(1, 0xFF)
        op = R.choice(["+", "-", "|", "&", "^"])
        # Zaman zaman number table lookup ile karıştır
        if nt_name and R.random() < 0.4:
            slot = _ri(0, 15)  # sabit index — runtime'da erişilebilir ama sonuç kullanılmaz
            out.append(f"var {v}=({_num_expr(a)}{op}{nt_name}[{_num_expr(slot)}]);")
        else:
            out.append(f"var {v}=({_num_expr(a)}{op}{_num_expr(b)});")
    fn = _rn("_f")
    op = R.choice(["+", "-", "*", "|"])
    # Son dead fonksiyon da number table'ı referans alabilir
    if nt_name and R.random() < 0.5:
        slot2 = _ri(0, 15)
        out.append(f"function {fn}(_a,_b){{return _a{op}{nt_name}[{_num_expr(slot2)}];}}")
    else:
        out.append(f"function {fn}(_a,_b){{return _a{op}_b;}}")
    return "".join(out)

def build_multi_channel_devtools_detect() -> str:
    """
    Çok kanallı DevTools tespit sistemi.
    
    Mevcut timing/debugger-statement yöntemleri bypass edilebilir:
      - CDP Debugger.setSkipAllPauses
      - DevTools hiç açmadan network/memory snapshot
    
    Yeni kanallar:
      1. console.table() render time — DevTools açıksa render ~100-500ms
      2. Function.prototype.constructor manipulation detect
      3. window.outerHeight - innerHeight (bağımsız doğrulama)
      4. toString() length anomaly detection
      5. RegExp.prototype.test hook detection
    
    Her kanal bağımsız sinyal üretir → toplam skor eşik kontrolü.
    Tek bir bypass yeterli değil, çoğunluğu gerekli.
    """
    # Global flag
    v_flag = "_wasd_dt_score"  # DevTools detection score (0-100)
    
    # Değişken adları
    v_ch1 = _rn("_c1"); v_ch2 = _rn("_c2"); v_ch3 = _rn("_c3")
    v_ch4 = _rn("_c4"); v_ch5 = _rn("_c5"); v_ch6 = _rn("_c6")
    v_score = _rn("_sc"); v_fn = _rn("_dtd")
    v_t0 = _rn("_t0"); v_t1 = _rn("_t1")
    v_obj = _rn("_ob"); v_orig = _rn("_or")
    v_test = _rn("_ts"); v_len = _rn("_ln")
    
    # Eşik ve ağırlıklar (build-time random)
    threshold = R.randint(40, 60)
    w_console = R.randint(25, 35)
    w_ctor = R.randint(20, 30)
    w_height = R.randint(15, 25)
    w_tostr = R.randint(10, 20)
    w_regexp = R.randint(10, 20)
    w_debug = R.randint(15, 25)  # fallback timing
    
    thr_expr = _num_expr(threshold)
    
    return (
        f"(function(){{"
        f"var {v_score}=0;"
        
        # ── Kanal 1: console.table() render time ─────────────────────────────
        # DevTools kapalıysa: instant (0-5ms)
        # DevTools açıksa: render overhead (~100-500ms)
        f"var {v_ch1}=0;"
        f"try{{"
        f"var {v_obj}=Array.from({{length:100}},function(_,i){{return{{id:i,v:Math.random()}};}});"
        f"var {v_t0}=performance.now();"
        f"console.table({v_obj});"
        f"var {v_t1}=performance.now();"
        f"console.clear();"
        # DevTools açıksa render time > 50ms
        f"if(({v_t1}-{v_t0})>{_num_expr(R.randint(50, 100))}){v_ch1}={_num_expr(w_console)};"
        f"}}catch(_e){{}}"
        
        # ── Kanal 2: Function.prototype.constructor manipulation ─────────────
        # Bazı stealth araçları Function.prototype'ı patch'ler
        # Native constructor toString() içermeli
        f"var {v_ch2}=0;"
        f"try{{"
        f"var {v_orig}=Function.prototype.constructor;"
        f"if(typeof {v_orig}!=='function'||"
        f"{v_orig}.toString().indexOf('native code')===-1){{"
        f"{v_ch2}={_num_expr(w_ctor)};"
        f"}}"
        f"}}catch(_e){{}}"
        
        # ── Kanal 3: window dimension delta ──────────────────────────────────
        # DevTools açıksa outer - inner > threshold
        # Ancak: zoom, responsive mode de fark yaratır — düşük ağırlık
        f"var {v_ch3}=0;"
        f"try{{"
        f"if(typeof window!=='undefined'){{"
        f"var _delta=window.outerHeight-window.innerHeight;"
        # Dock pozisyonuna göre: bottom dock = yüksek delta, side dock = düşük delta
        # 160px = tipik DevTools min height
        f"if(_delta>{_num_expr(R.randint(160, 200))}){v_ch3}={_num_expr(w_height)};"
        f"}}"
        f"}}catch(_e){{}}"
        
        # ── Kanal 4: toString() length anomaly ───────────────────────────────
        # Proxy/hook wrapper'ları genellikle toString() uzunluğunu değiştirir
        # Native function: toString().length sabit aralıkta (~30-50 char)
        f"var {v_ch4}=0;"
        f"try{{"
        f"var {v_len}=Function.prototype.toString.toString().length;"
        # Native beklenen: 30-60 char arası
        # Hook'lanmışsa: çok kısa (<20) veya çok uzun (>100)
        f"if({v_len}<{_num_expr(20)}||{v_len}>{_num_expr(100)}){{"
        f"{v_ch4}={_num_expr(w_tostr)};"
        f"}}"
        f"}}catch(_e){{}}"
        
        # ── Kanal 5: RegExp.prototype.test hook detection ────────────────────
        # Bazı proxy tool'ları RegExp.test'i override eder (pattern obfuscation bypass için)
        f"var {v_ch5}=0;"
        f"try{{"
        f"var {v_test}=RegExp.prototype.test.toString();"
        f"if({v_test}.indexOf('native code')===-1){{"
        f"{v_ch5}={_num_expr(w_regexp)};"
        f"}}"
        f"}}catch(_e){{}}"
        
        # ── Kanal 6: debugger timing (fallback) ──────────────────────────────
        # CDP bypass edilebilir ama çoklu kanal sistemine hala katkı sağlar
        f"var {v_ch6}=0;"
        f"try{{"
        f"var {v_t0}=performance.now();"
        f"debugger;"
        f"var {v_t1}=performance.now();"
        f"if(({v_t1}-{v_t0})>{_num_expr(R.randint(80, 120))}){{"
        f"{v_ch6}={_num_expr(w_debug)};"
        f"}}"
        f"}}catch(_e){{}}"
        
        # ── Toplam skor hesaplama ────────────────────────────────────────────
        f"{v_score}={v_ch1}+{v_ch2}+{v_ch3}+{v_ch4}+{v_ch5}+{v_ch6};"
        
        # Global'e yaz
        f"if(typeof window!=='undefined'){{"
        f"window['{v_flag}']={v_score};"
        # Eşik aşılırsa flag set et
        f"window['_wasd_dt_detected']={v_score}>={thr_expr};"
        f"}}"
        
        # Periyodik re-check (bazı kanallar runtime'da değişebilir)
        f"var _recheckInterval={_num_expr(R.randint(3000, 6000))};"
        f"setInterval(function(){{"
        f"try{{"
        # Sadece console.table ve debugger timing'i yeniden kontrol et
        # (diğerleri statik)
        f"var _sc2=0;"
        
        # console.table recheck
        f"var {v_obj}=Array.from({{length:50}},function(_,i){{return{{i:i}};}});"
        f"var {v_t0}=performance.now();"
        f"console.table({v_obj});"
        f"var {v_t1}=performance.now();"
        f"console.clear();"
        f"if(({v_t1}-{v_t0})>{_num_expr(50)})_sc2+={_num_expr(w_console)};"
        
        # debugger timing recheck
        f"var {v_t0}=performance.now();"
        f"debugger;"
        f"var {v_t1}=performance.now();"
        f"if(({v_t1}-{v_t0})>{_num_expr(100)})_sc2+={_num_expr(w_debug)};"
        
        # Statik skorları ekle (ch2, ch4, ch5 değişmez)
        f"_sc2+={v_ch2}+{v_ch4}+{v_ch5};"
        
        # window dimension yeniden kontrol (zoom/resize değişebilir)
        f"if(typeof window!=='undefined'){{"
        f"var _delta=window.outerHeight-window.innerHeight;"
        f"if(_delta>{_num_expr(R.randint(160, 200))})_sc2+={_num_expr(w_height)};"
        f"}}"
        
        # Global güncelle
        f"if(typeof window!=='undefined'){{"
        f"window['{v_flag}']=_sc2;"
        f"window['_wasd_dt_detected']=_sc2>={thr_expr};"
        f"}}"
        
        f"}}catch(_re){{}}"
        f"}},_recheckInterval);"
        
        f"}})();"
    )


def build_timing_guard() -> str:
    """
    Ana-thread timing guard: performance.now() + debugger probe + console getter.
    
    NOT: Bu fonksiyon legacy uyumluluk için korunuyor.
    Yeni projeler build_multi_channel_devtools_detect() kullanmalı.
    
    CDP Page.addScriptToEvaluateOnNewDocument ile performance.now patch'lenebilir,
    bu yüzden Worker probe ile birlikte kullanılır (bkz. build_worker_timing_guard).
    """
    v_flag   = _rn("_fl")
    v_detect = _rn("_det")
    v_img    = _rn("_im")
    v_loop   = _rn("_lp")
    v_t0     = _rn("_t0")
    v_t1     = _rn("_t1")
    threshold = R.randint(80, 130)
    interval  = R.randint(700, 1100)
    return (
        f"var {v_flag}=false;"
        f"(function(){{"
        f"function {v_detect}(){{"
        f"var {v_t0}=performance.now();"
        f"debugger;"
        f"var {v_t1}=performance.now();"
        f"if(({v_t1}-{v_t0})>{threshold}){{{v_flag}=true;return;}}"
        f"var {v_img}=new Image();"
        f"Object.defineProperty({v_img},'id',{{get:function(){{{v_flag}=true;}}}});"
        f"console.log({v_img});"
        f"console.clear();"
        f"}}"
        f"try{{{v_detect}();}}catch(_e){{}}"
        f"var {v_loop}=setInterval(function(){{"
        f"try{{{v_detect}();}}catch(_e){{}}"
        f"if({v_flag}){{"
        f"clearInterval({v_loop});"
        f"(function _b(){{"
        f"try{{debugger;}}catch(_e){{}}"
        f"setTimeout(_b,{R.randint(40,80)});"
        f"}})();"
        f"}}"
        f"}},{interval});"
        f"}})();"
    )


def build_worker_timing_guard() -> str:
    """
    Web Worker tabanlı timing probe — CDP override'larından kaçar.

    Neden ayrı Worker:
    - CDP Page.addScriptToEvaluateOnNewDocument sadece ana frame'e inject eder.
      Birçok CDP implementasyonu Worker context'ine ulaşamaz.
    - Worker'da performance.now / Date.now patch'lenmemişse:
      debugger duraksatma süresi ölçülmez fakat gerçek wall-clock delta tutarsızlığı
      yakalanır; ayrıca Worker'ın başlatılıp başlatılamadığı da bir sinyaldir.

    Mekanizma:
    1. Worker kodu Blob URL ile build-time'da inline gömülür — dış dosya yok.
    2. Worker başlatılır, Date.now() ile kısa bir döngü süresi ölçülür.
       Beklenenden çok uzun sürerse (debugger veya CPU throttle sinyali) flag set edilir.
    3. Worker mevcut değilse (headless ortam kısıtlaması) bu da bir sinyal.
    4. Sonuç postMessage ile ana thread'e iletilir, _wasd_dbg_flag global'i güncellenir.
    5. Ana thread flag'i de OR'lanır — her iki kanaldan birisi yeterlir.

    Build başına: Worker kod içeriği, eşikler, değişken adları randomize edilir.
    """
    # Ana thread değişken adları
    v_flag    = _rn("_wfl")   # worker detect flag (ana thread)
    v_worker  = _rn("_wkr")   # Worker instance
    v_blob    = _rn("_wbl")   # Blob URL
    v_fn      = _rn("_wfn")   # worker starter fonksiyon

    # Worker içi değişken adları (ayrı scope — ana thread adlarıyla çakışmaz)
    wv_t0     = _rn("_wt0")
    wv_t1     = _rn("_wt1")
    wv_spin   = _rn("_wsp")   # spin loop sayacı
    wv_lim    = _rn("_wlm")   # spin limit
    wv_delta  = _rn("_wdl")   # ölçülen delta
    wv_thr    = _rn("_wth")   # eşik

    # Build-time randomize parametreler
    spin_count  = R.randint(50000, 150000)     # spin loop iterasyon — sabit iş yükü
    thr_ms      = R.randint(25, 60)            # normal spin süresi eşiği (ms)
    spin_expr   = _num_expr(spin_count)
    thr_expr    = _num_expr(thr_ms)

    # Worker kaynak kodu — Blob'a gömülecek JS string
    # Tek satırda — JSON.stringify ile tırnak kaçışı sorununu önle
    worker_src = (
        # Spin loop: sabit iş yükü, debugger durdurursa Date.now() delta büyür
        f"var {wv_lim}={spin_expr};"
        f"var {wv_t0}=Date.now();"
        f"for(var {wv_spin}=0;{wv_spin}<{wv_lim};{wv_spin}++){{}}"
        f"var {wv_t1}=Date.now();"
        f"var {wv_delta}={wv_t1}-{wv_t0};"
        f"var {wv_thr}={thr_expr};"
        # sinyal: spin süresi beklenenin 3 katını aşarsa debugger/throttle şüphesi
        f"postMessage({{type:'timing',detected:{wv_delta}>{wv_thr}*3,delta:{wv_delta},thr:{wv_thr}}});"
    )

    # Worker src'yi JS string literal olarak güvenli gömme
    # Çift tırnak içine — içinde çift tırnak yok
    worker_src_escaped = worker_src.replace("\\", "\\\\").replace("'", "\\'")

    return (
        f"var {v_flag}=false;"
        f"(function(){{"
        f"function {v_fn}(){{"
        f"try{{"
        f"if(typeof Worker==='undefined'||typeof Blob==='undefined'){{{v_flag}=true;return;}}"
        # Worker kodunu Blob URL olarak oluştur
        f"var {v_blob}=URL.createObjectURL(new Blob(['{worker_src_escaped}'],"
        f"{{type:'application/javascript'}}));"
        f"var {v_worker}=new Worker({v_blob});"
        f"URL.revokeObjectURL({v_blob});"
        # Worker mesajını dinle
        f"{v_worker}.onmessage=function(_e){{"
        f"if(_e&&_e.data&&_e.data.type==='timing'){{"
        f"if(_e.data.detected){{{v_flag}=true;}}"
        # _wasd_dbg_flag: diğer anti-debug kanallarıyla OR — global flag
        f"if(typeof window!=='undefined'){{"
        f"window._wasd_dbg_flag=window._wasd_dbg_flag||{v_flag};"
        f"}}"
        f"}}"
        f"{v_worker}.terminate();"
        f"}};"
        # Worker başlatılamazsa (kısıtlı headless) bu da bir sinyal
        f"{v_worker}.onerror=function(){{{v_flag}=true;{v_worker}.terminate();}};"
        f"}}catch(_we){{{v_flag}=true;}}"
        f"}}"
        # Sayfa yüklendikten kısa bir süre sonra çalıştır
        f"if(typeof window!=='undefined'){{"
        f"setTimeout({v_fn},{_num_expr(R.randint(200, 500))});"
        f"}}"
        f"}})();"
    )


def build_vm_score_fallback() -> tuple[str, str]:
    """
    VM-based score() fallback — ağırlıklar ve eşik gizli.
    Her build'de opcode'lar ve handler sırası randomize edilir.
    
    Returns: (vm_code: str, vm_fn_name: str)
    """
    # Signal isimleri ve ağırlıkları
    signals = [
        ('env',   50),
        ('wdrv',  40),
        ('attr',  35),
        ('ua',    30),
        ('pw',    30),
        ('cdp',   35),
        ('webgl', 20),
        ('plug',  10),
        ('perm',  20),
        ('lang',  10),
        ('dim',   15),
        ('outer', 15),
    ]
    
    # Build-time random opcodes
    opcodes = {}
    used_codes = set()
    for sig, _ in signals:
        while True:
            code = R.randint(0x10, 0xEF)
            if code not in used_codes:
                opcodes[sig] = code
                used_codes.add(code)
                break
    
    # CMP_THR opcode (son işlem)
    opcodes['CMP_THR'] = R.randint(0xF0, 0xFF)
    
    # Bytecode array
    bc = [opcodes[sig] for sig, _ in signals] + [opcodes['CMP_THR']]
    bc_js = "[" + ",".join(f"0x{c:02x}" for c in bc) + "]"
    
    # Weights array (obfuscated)
    weights = [w for _, w in signals]
    weights_js = "[" + ",".join(_num_expr(w) for w in weights) + "]"
    
    # Değişken adları
    v_bc = _rn("_bc")
    v_w = _rn("_w")
    v_ctx = _rn("_cx")
    v_h = _rn("_h")
    v_op = _rn("_op")
    v_fn = _rn("_vms")  # global fonksiyon adı
    v_sig = _rn("_sg")
    v_thr = _rn("_th")
    
    # Handler map generation
    handlers = []
    for i, (sig, weight) in enumerate(signals):
        op_code = opcodes[sig]
        handler = (
            f"{v_h}.set(0x{op_code:02x},function(){{"
            f"if({v_sig}[{i}]){v_ctx}.acc+=({v_w}[{i}]|0);"
            f"{v_ctx}.pc++;"
            f"}});"
        )
        handlers.append(handler)
    
    # CMP_THR handler
    cmp_handler = (
        f"{v_h}.set(0x{opcodes['CMP_THR']:02x},function(){{"
        f"{v_ctx}.result=({v_ctx}.acc>={v_thr})?1:0;"
        f"{v_ctx}.pc++;"
        f"}});"
    )
    handlers.append(cmp_handler)
    
    # VM function
    vm_code = (
        f"function {v_fn}({v_sig},{v_thr}){{"
        f"var {v_bc}={bc_js};"
        f"var {v_w}={weights_js};"
        f"var {v_ctx}={{acc:0,pc:0,result:0}};"
        f"var {v_h}=new Map();"
        + "".join(handlers) +
        f"while({v_ctx}.pc<{v_bc}.length){{"
        f"var {v_op}={v_bc}[{v_ctx}.pc];"
        f"var _hf={v_h}.get({v_op});"
        f"if(_hf)_hf();else break;"
        f"}}"
        f"return {v_ctx}.result;"
        f"}}"
    )
    
    return vm_code, v_fn

def build_env_check(dec_fn: str = "", url_idx: int = -1) -> tuple[str, str]:
    """
    Ağırlıklı skor tabanlı bot/otomasyon tespiti.

    dec_fn / url_idx parametreleri artık kullanılmıyor — env-signal URL'i
    build_env_check'in kendi inline RC4 mini-decoder'ı ile çözülür.
    Parametre imzası geriye dönük uyum için korundu.

    Tek bir sinyalin public stealth eklentisiyle patch'lenmesi artık yeterli değil —
    toplam skor eşiği aşmak için birden fazla sinyalin aynı anda maskelenmesi gerekir.

    Sinyaller client-side'da hesaplanır, sunucuya gönderilmez.
    Fire-and-forget XHR yalnızca IP+UA iletir (KVKK: bot trafik tespiti kapsamı).
    """
    # Her build'de farklı değişken isimleri
    v_poison   = _rn("_pz")
    # v_score KALDIRILDI — ağırlıklar JS'de artık görünmez
    # WASM primary: score() WASM binary'sinde, JS'de hiçbir sayı yok
    # WASM fallback: sadece hard sinyaller (sayı yok)

    # Eşik sadece WASM çağrısına geçirilir — JS'de karar vermiyor
    threshold  = 50 + R.randint(-5, 5)
    v_thr      = _num_expr(threshold)

    # Sinyal değişken isimleri
    vs = {k: _rn(f"_s{k}") for k in [
        "env",       # window/document yok
        "wdrv",      # navigator.webdriver
        "attr",      # HTML webdriver attr
        "ua",        # HeadlessChrome/PhantomJS UA
        "pw",        # Playwright/Puppeteer globals
        "cdp",       # Chrome CDP automation flags
        "fpts",      # Function.prototype.toString patch
        "webgl",     # WebGL SwiftShader/llvmpipe renderer
        "plug",      # navigator.plugins.length == 0
        "perm",      # permissions.query native code
        "lang",      # navigator.languages boş
        "dim",       # screen boyutları anormal
        "outer",     # window.outerWidth == 0
        "atob_hook", # atob native code patch
        "tddec_hook",# TextDecoder.prototype.decode native code patch
        "fchr_hook", # String.fromCharCode native code patch
        "iframe_hook", # iframe temiz referans ile toString uyumsuzlugu
        "stack_hook",  # Error().stack'de Proxy/hook frame derinligi
    ]}

    # ── Periyodik yeniden değerlendirme parametreleri ────────────────────────
    v_check_fn  = _rn("_chk")    # sinyal toplama fonksiyonu adı
    v_interval  = _rn("_civ")    # setInterval handle
    v_last_ts   = _rn("_cls")    # son kontrol zamanı
    interval_ms = R.randint(3000, 8000)   # build-time random — sabit pattern yok
    v_int_expr  = _num_expr(interval_ms)

    # ── Davranışsal fingerprint collector — build-time değişken adları ───────
    # Mouse entropisi, rAF timing drift, scroll doğallığı 3-5 saniyelik
    # pencerede toplanır; sürekli güncellenen skor window._wasd_bhv'ye yazılır.
    bhv_global   = "_wasd_bhv"           # davranışsal skor global adı (sabit — v_poison ile OR)
    v_bhv_fn     = _rn("_bhv")           # collector fonksiyon adı

    # Mouse entropy değişkenleri
    v_mx         = _rn("_mx"); v_my = _rn("_my")   # son mouse pozisyonu
    v_mhist      = _rn("_mh")    # mouse angle history array
    v_mts        = _rn("_mts")   # mouse son timestamp
    v_mcount     = _rn("_mc")    # mouse event sayacı
    v_mcurve     = _rn("_mcv")   # eğrisellik skoru (angle değişimleri stddev)

    # rAF timing drift değişkenleri
    v_raf_last   = _rn("_rl")    # son rAF timestamp
    v_raf_diffs  = _rn("_rd")    # frame delta history
    v_raf_cnt    = _rn("_rc")    # rAF ölçüm sayacı
    v_raf_jitter = _rn("_rj")    # hesaplanan jitter (stddev of deltas)
    v_raf_id     = _rn("_ri")    # requestAnimationFrame handle

    # Scroll değişkenleri
    v_scroll_ts  = _rn("_st")    # scroll son timestamp
    v_scroll_cnt = _rn("_sc2")   # scroll event sayacı
    v_scroll_ok  = _rn("_so")    # scroll doğallık flag

    # Window pencere boyutu — resize tespiti
    v_win_w      = _rn("_ww"); v_win_h = _rn("_wh")

    # Pencere boyutu ve build parametreleri
    collect_ms   = R.randint(3000, 5000)   # toplama penceresi
    min_mouse    = R.randint(4, 8)         # gerçek insan için minimum mouse event
    min_jitter   = R.randint(2, 5)         # ms — gerçek GPU'da minimum rAF jitter
    min_curve    = _num_expr(R.randint(15, 30))  # minimum angle değişim eşiği (derece)
    collect_expr = _num_expr(collect_ms)
    min_mouse_ex = _num_expr(min_mouse)
    min_jitter_ex= _num_expr(min_jitter)

    bhv_collector = (
        # ── Değişken başlatma ─────────────────────────────────────────────
        f"var {v_mx}=-1,{v_my}=-1,{v_mts}=0,{v_mcount}=0;"
        f"var {v_mhist}=[];"      # son N mouse angle değişimi
        f"var {v_mcurve}=0;"
        f"var {v_raf_last}=0,{v_raf_diffs}=[],{v_raf_cnt}=0,{v_raf_jitter}=0;"
        f"var {v_scroll_ts}=0,{v_scroll_cnt}=0,{v_scroll_ok}=false;"
        f"var {v_win_w}=0,{v_win_h}=0;"

        # ── Mouse hareketi toplayıcı ──────────────────────────────────────
        # Gerçek insan: Bezier-benzeri eğrisel hareket → angle değişimleri
        # çeşitli dağılım gösterir (yüksek entropi).
        # Headless replay / bot: doğrusal veya step-wise → düşük entropi.
        f"(function(){{"
        f"if(typeof document==='undefined')return;"
        f"var _mh=function(_e){{"
        f"var _nx=_e.clientX||0,_ny=_e.clientY||0;"
        f"var _now=Date.now();"
        f"if({v_mx}>=0&&{v_my}>=0&&(_now-{v_mts})<500){{"   # 500ms içinde ardışık
        f"var _dx=_nx-{v_mx},_dy=_ny-{v_my};"
        f"var _dist=Math.sqrt(_dx*_dx+_dy*_dy);"
        f"if(_dist>3){{"    # micro-jitter filtrele
        f"var _ang=Math.atan2(_dy,_dx)*180/Math.PI;"
        f"{v_mhist}.push(_ang);"
        f"if({v_mhist}.length>20){v_mhist}.shift();"   # rolling 20
        f"{v_mcount}++;"
        f"}}"
        f"}}"
        f"{v_mx}=_nx;{v_my}=_ny;{v_mts}=_now;"
        f"}};"
        f"document['addEventListener']('mousemove',_mh,{{passive:true}});"
        f"}})();"

        # ── rAF timing drift toplayıcı ────────────────────────────────────
        # Gerçek GPU: frame delta ~16.67ms ± birkaç ms jitter (vsync noise).
        # Headless / CPU render: jitter yok (deterministik ~16.67ms) veya
        # çok büyük (throttled).
        # N=30 frame ölçümü → stddev hesapla.
        f"(function(){{"
        f"if(typeof requestAnimationFrame==='undefined')return;"
        f"var _raf_cb=function(_ts){{"
        f"if({v_raf_last}>0){{"
        f"var _d=_ts-{v_raf_last};"
        f"if(_d>1&&_d<200){{"   # outlier filtrele
        f"{v_raf_diffs}.push(_d);"
        f"if({v_raf_diffs}.length>30){v_raf_diffs}.shift();"
        f"{v_raf_cnt}++;"
        f"}}"
        f"}}"
        f"{v_raf_last}=_ts;"
        f"if({v_raf_cnt}<60)requestAnimationFrame(_raf_cb);"   # 60 frame yeterli
        f"}};"
        f"requestAnimationFrame(_raf_cb);"
        f"}})();"

        # ── Scroll doğallık toplayıcı ─────────────────────────────────────
        # Gerçek insan: scroll event'leri arasında değişken delta (wheel hızlanma).
        # Bot scroll: sabit adımlar veya hiç scroll yok.
        f"(function(){{"
        f"if(typeof window==='undefined')return;"
        f"var _sc=function(){{"
        f"var _now=Date.now();"
        f"if({v_scroll_cnt}>0&&{v_scroll_ts}>0){{"
        f"var _gap=_now-{v_scroll_ts};"
        # Gerçek kullanıcı: scroll event'leri birbirini takip eder (50-800ms aralık)
        f"if(_gap>50&&_gap<800){v_scroll_ok}=true;"
        f"}}"
        f"{v_scroll_ts}=_now;"
        f"{v_scroll_cnt}++;"
        f"}};"
        f"window['addEventListener']('scroll',_sc,{{passive:true}});"
        f"window['addEventListener']('wheel',_sc,{{passive:true}});"
        f"}})();"

        # ── Skor hesaplama fonksiyonu ─────────────────────────────────────
        # Her {collect_ms}ms'de bir çağrılır, sonucu window._wasd_bhv'ye yazar.
        # Skor 0-100 arası, düşük = insan-dışı.
        f"function {v_bhv_fn}(){{"
        f"var _score=0,_total=0;"

        # Sinyal 1: Mouse event sayısı yeterli mi?
        f"_total+=40;"
        f"if({v_mcount}>={min_mouse_ex}){{"
        # Mouse angle entropy (stddev) — yeterli çeşitlilik var mı?
        f"if({v_mhist}.length>={min_mouse_ex}){{"
        f"var _n={v_mhist}.length;"
        f"var _sum=0;"
        f"for(var _k=0;_k<_n;_k++)_sum+={v_mhist}[_k];"
        f"var _mean=_sum/_n;"
        f"var _var=0;"
        f"for(var _k=0;_k<_n;_k++){{var _d={v_mhist}[_k]-_mean;_var+=_d*_d;}}"
        f"var _std=Math.sqrt(_var/_n);"
        # Gerçek insan: stddev > ~15 derece (çeşitli yönlerde hareket)
        f"if(_std>{min_curve})_score+=40;"
        f"else if(_std>5)_score+=20;"   # biraz çeşitlilik var
        f"}}"
        f"}}"

        # Sinyal 2: rAF jitter (GPU render gerçekliği)
        f"_total+=35;"
        f"if({v_raf_diffs}.length>=10){{"
        f"var _rn2={v_raf_diffs}.length;"
        f"var _rs=0;"
        f"for(var _k=0;_k<_rn2;_k++)_rs+={v_raf_diffs}[_k];"
        f"var _rm=_rs/_rn2;"
        f"var _rv=0;"
        f"for(var _k=0;_k<_rn2;_k++){{var _dr={v_raf_diffs}[_k]-_rm;_rv+=_dr*_dr;}}"
        f"{v_raf_jitter}=Math.sqrt(_rv/_rn2);"
        # Headless: jitter ~0. Gerçek GPU: jitter 1-5ms arası.
        f"if({v_raf_jitter}>={min_jitter_ex}&&{v_raf_jitter}<20)_score+=35;"
        f"else if({v_raf_jitter}>0.5)_score+=15;"
        f"}}"

        # Sinyal 3: Scroll doğallığı
        f"_total+=25;"
        f"if({v_scroll_ok}&&{v_scroll_cnt}>=2)_score+=25;"
        f"else if({v_scroll_cnt}>=1)_score+=10;"

        # Normalize: 0-100
        f"var _norm=_total>0?Math.round(_score*100/_total):0;"
        # Eşik: 40 altı = bot şüpheli (mouse yok + jitter yok + scroll yok)
        f"if(typeof window!=='undefined'){{"
        f"window['{bhv_global}']=_norm;"   # sürekli güncellenen skor
        f"if(_norm<40&&{v_mcount}===0&&{v_raf_cnt}>=10){{"
        # Mouse hiç yok VE yeterli rAF ölçümü var → kesin headless
        f"window['{bhv_global}_bot']=true;"
        f"}}"
        f"}}"
        f"}}"

        # İlk hesaplama {collect_ms}ms sonra — veri toplanmadan önce çağırma
        f"(function(){{"
        f"var _si2=typeof window!=='undefined'?window['setInterval']:null;"
        f"if(typeof _si2==='function'){{"
        f"_si2.call(window,function(){{try{{{v_bhv_fn}();}}catch(_be){{}}}},{collect_expr});"
        f"}}"
        f"}})();"
    )

    code = (
        f"var {v_poison}=false;"
        f"var {v_last_ts}=0;"
        # ── Davranışsal fingerprint collector — önce başlat ──────────────────
        # Mouse, rAF ve scroll toplayıcıları hemen başlar, veri biriktirir.
        # v_check_fn çalıştığında window._wasd_bhv ve window._wasd_bhv_bot'u okur.
        + bhv_collector
        # Sinyal toplama fonksiyonu
        + f"function {v_check_fn}(){{"
        + f"try{{"

        # ── Sinyal: Ortam ──────────────────────────────────────────────────
        f"var {vs['env']}=typeof window==='undefined'||typeof document==='undefined';"

        # ── Sinyal: navigator.webdriver ────────────────────────────────────
        f"var {vs['wdrv']}=false;"
        f"try{{"
        f"if(typeof navigator!=='undefined'&&(navigator.webdriver===true||!!navigator.webdriver)){{{vs['wdrv']}=true;}}"
        f"}}catch(_e){{}}"

        # ── Sinyal: HTML webdriver attribute ───────────────────────────────
        f"var {vs['attr']}=false;"
        f"try{{"
        f"if(typeof document!=='undefined'&&document.documentElement&&document.documentElement.getAttribute('webdriver')!==null){{{vs['attr']}=true;}}"
        f"}}catch(_e){{}}"

        # ── Sinyal: Headless UA / PhantomJS / Electron ─────────────────────
        f"var {vs['ua']}=false;"
        f"try{{"
        f"if(typeof navigator!=='undefined'&&/HeadlessChrome|PhantomJS|Electron/i.test(navigator.userAgent)){{{vs['ua']}=true;}}"
        f"}}catch(_e){{}}"

        # ── Sinyal: Playwright / Puppeteer globals ──────────────────────────
        f"var {vs['pw']}=false;"
        f"try{{"
        f"if(typeof window!=='undefined'&&"
        f"(window.__playwright||window.__pw_manual||window.__pwInitScripts||"
        f"window._phantom||window.callPhantom||window.__nightmare)){{{vs['pw']}=true;}}"
        f"}}catch(_e){{}}"

        # ── Sinyal: CDP automation flags ────────────────────────────────────
        f"var {vs['cdp']}=false;"
        f"try{{"
        f"if(typeof window!=='undefined'&&"
        f"(window.cdc_adoQpoasnfa76pfcZLmcfl_Array||"
        f"window.$chrome_asyncScriptInfo||window.$cdc_asdjflasutopfhvcZLmcfl_)){{{vs['cdp']}=true;}}"
        f"}}catch(_e){{}}"

        # ── Sinyal: fpts (devre dışı) ───────────────────────────────────────
        f"var {vs['fpts']}=false;"

        # ── Sinyal: WebGL renderer SwiftShader / llvmpipe ───────────────────
        f"var {vs['webgl']}=false;"
        f"try{{"
        f"var _wc=document.createElement('canvas');"
        f"var _wg=_wc.getContext('webgl')||_wc.getContext('experimental-webgl');"
        f"if(_wg){{"
        f"var _wr=_wg.getExtension('WEBGL_debug_renderer_info');"
        f"if(_wr){{"
        f"var _rv=_wg.getParameter(_wr.UNMASKED_RENDERER_WEBGL)||'';"
        f"if(/SwiftShader|llvmpipe|softpipe|SWR|ANGLE.*(Swiftshader|Software)/i.test(_rv)){{{vs['webgl']}=true;}}"
        f"}}"
        f"}}"
        f"}}catch(_e){{}}"

        # ── Sinyal: navigator.plugins.length === 0 ──────────────────────────
        f"var {vs['plug']}=false;"
        f"try{{"
        f"if(typeof navigator!=='undefined'&&navigator.plugins!==undefined&&navigator.plugins.length===0){{{vs['plug']}=true;}}"
        f"}}catch(_e){{}}"

        # ── Sinyal: permissions.query native code patch ─────────────────────
        f"var {vs['perm']}=false;"
        f"try{{"
        f"if(typeof navigator!=='undefined'&&navigator.permissions&&navigator.permissions.query&&"
        f"navigator.permissions.query.toString().indexOf('native code')===-1){{{vs['perm']}=true;}}"
        f"}}catch(_e){{}}"

        # ── Sinyal: navigator.languages boş ────────────────────────────────
        f"var {vs['lang']}=false;"
        f"try{{"
        f"if(typeof navigator!=='undefined'&&"
        f"(!navigator.languages||navigator.languages.length===0)){{{vs['lang']}=true;}}"
        f"}}catch(_e){{}}"

        # ── Sinyal: screen boyutları anormal ───────────────────────────────
        f"var {vs['dim']}=false;"
        f"try{{"
        f"if(typeof screen!=='undefined'&&(screen.width<10||screen.height<10)){{{vs['dim']}=true;}}"
        f"}}catch(_e){{}}"

        # ── Sinyal: window.outerWidth === 0 ────────────────────────────────
        f"var {vs['outer']}=false;"
        f"try{{"
        f"if(typeof window!=='undefined'&&window.outerWidth===0){{{vs['outer']}=true;}}"
        f"}}catch(_e){{}}"

        # ── Sinyal: atob native code patch (ağırlık 25) ─────────────────────
        f"var {vs['atob_hook']}=false;"
        f"try{{"
        f"if(typeof atob==='function'&&atob.toString().indexOf('native code')===-1)"
        f"{{{vs['atob_hook']}=true;}}"
        f"}}catch(_e){{}}"

        # ── Sinyal: TextDecoder.prototype.decode native code patch (ağırlık 25)
        f"var {vs['tddec_hook']}=false;"
        f"try{{"
        f"if(typeof TextDecoder!=='undefined'"
        f"&&TextDecoder.prototype.decode.toString().indexOf('native code')===-1)"
        f"{{{vs['tddec_hook']}=true;}}"
        f"}}catch(_e){{}}"

        # ── Sinyal: String.fromCharCode native code patch (ağırlık 20) ──────
        f"var {vs['fchr_hook']}=false;"
        f"try{{"
        f"if(typeof String!=='undefined'&&typeof String.fromCharCode==='function'"
        f"&&String.fromCharCode.toString().indexOf('native code')===-1)"
        f"{{{vs['fchr_hook']}=true;}}"
        f"}}catch(_e){{}}"

        # ── Sinyal: iframe temiz referans karşılaştırması (ağırlık 30) ───────
        # Object.defineProperty ile toString'i override eden araçlar ana frame'i
        # sahteler, ama iframe context'ine inject yapamaz (ayrı realm).
        # iframe'den temiz Function.prototype.toString alır, karşılaştırırız.
        f"var {vs['iframe_hook']}=false;"
        f"try{{"
        f"if(typeof document!=='undefined'&&typeof HTMLIFrameElement!=='undefined'){{"
        f"var _ifr=document['createElement']('iframe');"
        f"_ifr.style.cssText='display:none;width:0;height:0;';"
        f"document.body['appendChild'](_ifr);"
        f"var _iwin=_ifr['contentWindow'];"
        f"if(_iwin&&_iwin['Function']&&_iwin['Function']['prototype']){{"
        # iframe realm'inden temiz toString ref al
        f"var _cleanStr=_iwin['Function']['prototype']['toString'];"
        # ana frame native fonksiyonları ile karşılaştır
        f"var _atobNative=_cleanStr['call'](atob);"
        f"if(_atobNative['indexOf']('native code')===-1){{{vs['iframe_hook']}=true;}}"
        # toString uzunluk sapması: sahte 'native code' genellikle farklı uzunlukta
        f"if(typeof _iwin['atob']==='function'){{"
        f"var _refLen=_cleanStr['call'](_iwin['atob'])['length'];"
        f"var _locLen=atob['toString']()['length'];"
        # %15'ten fazla sapma → sahte toString (padding ile uzatılmış)
        f"if(_refLen>0&&Math['abs'](_refLen-_locLen)>Math['round'](_refLen*0.15))"
        f"{{{vs['iframe_hook']}=true;}}"
        f"}}"
        f"}}"
        f"try{{document.body['removeChild'](_ifr);}}catch(_re){{}}"
        f"}}"
        f"}}catch(_e){{}}"

        # ── Sinyal: Error().stack Proxy frame derinliği (ağırlık 25) ─────────
        # Proxy/hook tabanlı araçlar çağrı zincirine ekstra frame ekler.
        # Playwright, Puppeteer, CDP eval context genellikle 8+ frame üretir.
        # Temiz tarayıcıda sayfa yüklenirken Error().stack 2-5 frame içerir.
        f"var {vs['stack_hook']}=false;"
        f"try{{"
        f"if(typeof Error!=='undefined'){{"
        f"var _stk=(new Error())['stack']||'';"
        f"var _slines=_stk['split']('\\n');"
        f"var _slen=0;"
        f"for(var _si=0;_si<_slines['length'];_si++){{"
        f"if(_slines[_si]['trim']()['length']>0)_slen++;"
        f"}}"
        # 8+ frame = hook/automation framework şüphesi
        f"if(_slen>={_num_expr(8)}){{{vs['stack_hook']}=true;}}"
        # Framework imzaları
        f"var _sl=_stk['toLowerCase']();"
        f"if(_sl['indexOf']('playwright')!==-1||_sl['indexOf']('puppeteer')!==-1"
        f"||_sl['indexOf']('__playwright')!==-1||_sl['indexOf']('selenium')!==-1)"
        f"{{{vs['stack_hook']}=true;}}"
        f"}}"
        f"}}catch(_e){{}}"

        # ── Eşik kararı (monoton: bir kez true, sonsuza kadar true) ─────────
        # PRIMARY: WASM score() — ağırlıklar + eşik tamamen WASM binary'sinde.
        # FALLBACK: WASM yoksa sadece 3 hard sinyal.
        # v_poison |= yeni_sonuc  → geri dönüş yok.
        f"try{{"
        f"var _we=typeof window!=='undefined'?window['WASDEngine']:null;"
        f"var _sf=_we&&typeof _we['score']==='function'?_we['score']:null;"
        f"var _res=false;"
        f"if(_sf){{_res=_sf.call(_we,"
        f"{vs['env']}?1:0,{vs['wdrv']}?1:0,{vs['attr']}?1:0,{vs['ua']}?1:0,"
        f"{vs['pw']}?1:0,{vs['cdp']}?1:0,{vs['webgl']}?1:0,{vs['plug']}?1:0,"
        f"{vs['perm']}?1:0,{vs['lang']}?1:0,{vs['dim']}?1:0,{vs['outer']}?1:0,"
        f"{vs['atob_hook']}?1:0,{vs['tddec_hook']}?1:0,{vs['fchr_hook']}?1:0,"
        f"{vs['iframe_hook']}?1:0,{vs['stack_hook']}?1:0,"
        f"{v_thr})===1;}}"
        f"else{{_res={vs['env']}||{vs['wdrv']}||{vs['cdp']};}}"
        # Worker + davranışsal kanalları OR'la
        f"_res=_res||(typeof window!=='undefined'&&(!!window._wasd_dbg_flag||!!window['{bhv_global}_bot']));"
        f"{v_poison}={v_poison}||_res;"   # monoton: geri dönüş yok
        f"}}catch(_se){{{v_poison}={v_poison}||{vs['env']}||{vs['wdrv']}||{vs['cdp']};}}"

        f"}}catch(_e){{{v_poison}=true;}}"
        # periyodik yeniden hesaplama için timestamp güncelle
        f"{v_last_ts}=Date.now();"
        f"}}"   # function {v_check_fn} sonu

        # İlk çalışma — sayfa yüklenirken
        f"try{{{v_check_fn}();}}catch(_e){{}}"

        # ── Periyodik yeniden değerlendirme ───────────────────────────────
        # Her {interval_ms}ms'de bir tüm sinyaller yeniden taranır.
        # Bot ilk kontrolü geçip sonradan iz bırakırsa yakalanır.
        # Sayfa arka planda (hidden) ise yarı aralıkla kontrol — daha agresif.
        # Bracket notation + yerel değişken: obfuscator setInterval/clearInterval/
        # addEventListener'ı string-concat'a çevirince "is not a function" vermez.
        f"(function(){{"
        f"var _si=typeof window!=='undefined'?window['setInterval']:null;"
        f"var _ci=typeof window!=='undefined'?window['clearInterval']:null;"
        f"if(typeof _si==='function'){{"
        f"var {v_interval}=_si.call(window,function(){{"
        f"try{{{v_check_fn}();}}catch(_e){{}}"
        f"if({v_poison}&&typeof _ci==='function'){{_ci.call(window,{v_interval});}}"
        f"}},{v_int_expr});"
        f"}}"
        # Visibility API: sayfa gizlenip açılırsa hemen yeniden kontrol
        f"try{{"
        f"var _doc=typeof document!=='undefined'?document:null;"
        f"var _al=_doc&&typeof _doc['addEventListener']==='function'?_doc['addEventListener']:null;"
        f"if(_al){{"
        f"_al.call(_doc,'visibilitychange',function(){{"
        f"if(!_doc.hidden){{try{{{v_check_fn}();}}catch(_e){{}}}}"
        f"}});"
        f"}}"
        f"}}catch(_ve){{}}"
        f"}})();"
    )

    # ── env-signal URL: kendi inline RC4 mini-decoder'ı ─────────────────────
    # dec_fn parametresi artık kullanılmıyor — obfuscator dec_fn çağrısını
    # string concat'a dönüştürünce "is not a function" hatası veriyordu.
    # Çözüm: URL'i build-time'da RC4 ile şifrele, inline KSA+PRGA ile çöz.
    # fromCharCode tek satırda çözülür; RC4 blob'u çözmek için key lazım.
    _url_plain   = "/api/security/env-signal"
    _url_key     = secrets.token_bytes(8)
    _url_enc_b64 = _b64(_rc4_encrypt(_url_plain.encode(), _url_key))
    _url_key_js  = "[" + ",".join(str(b) for b in _url_key) + "]"
    _vu_S = _rn("_uS"); _vu_i = _rn("_ui"); _vu_j = _rn("_uj")
    _vu_t = _rn("_ut"); _vu_k = _rn("_uk"); _vu_e = _rn("_ue"); _vu_o = _rn("_uo")

    _url_decode_expr = (
        f"(function(){{"
        f"var {_vu_k}={_url_key_js};"
        f"var {_vu_e}=atob('{_url_enc_b64}');"
        f"var {_vu_S}=[];"
        f"for(var {_vu_i}=0;{_vu_i}<256;{_vu_i}++){_vu_S}[{_vu_i}]={_vu_i};"
        f"var {_vu_j}=0;"
        f"for(var {_vu_i}=0;{_vu_i}<256;{_vu_i}++){{"
        f"{_vu_j}=({_vu_j}+{_vu_S}[{_vu_i}]+{_vu_k}[{_vu_i}%{_vu_k}.length])%256;"
        f"var {_vu_t}={_vu_S}[{_vu_i}];{_vu_S}[{_vu_i}]={_vu_S}[{_vu_j}];{_vu_S}[{_vu_j}]={_vu_t};"
        f"}}"
        f"var _ui2=0,_uj2=0,{_vu_o}='';"
        f"for(var _un=0;_un<{_vu_e}.length;_un++){{"
        f"_ui2=(_ui2+1)%256;_uj2=(_uj2+{_vu_S}[_ui2])%256;"
        f"var {_vu_t}={_vu_S}[_ui2];{_vu_S}[_ui2]={_vu_S}[_uj2];{_vu_S}[_uj2]={_vu_t};"
        f"{_vu_o}+=String.fromCharCode({_vu_e}.charCodeAt(_un)^{_vu_S}[({_vu_S}[_ui2]+{_vu_S}[_uj2])%256]);"
        f"}}"
        f"return {_vu_o};"
        f"}})()"
    )

    v_url = _rn("_su")
    v_xhr = _rn("_sx")
    code += (
        f"if({v_poison}){{"
        f"try{{"
        f"var {v_url}={_url_decode_expr};"
        f"var {v_xhr}=new XMLHttpRequest();"
        f"{v_xhr}.open('POST',{v_url},true);"
        f"{v_xhr}.send(null);"
        f"}}catch(_se){{}}"
        f"}}"
    )

    return code, v_poison

def build_number_table() -> tuple[str, str]:
    name = _rn("_NT")
    count = R.randint(16, 24)
    nums = [R.randint(0, 0xFFFF) for _ in range(count)]
    entries = ",".join(str(n) for n in nums)
    code = f"var {name}=[{entries}];"
    return code, name

def inject_pre(code: str, fname: str, no_debugger: bool = False, no_env_check: bool = False) -> tuple[str, str, str]:
    """
    Returns: (wrapped_code, nonce_capability_global, session_capability_global)
    """
    # Capability global adlarını başlangıçta üret — _derive_key_js içinde kullanılır
    nonce_cap_name = "_wasd_nc_" + _rhex(4)
    session_cap_name = "_wasd_sc_" + _rhex(4)
    
    parts = []
    parts.append(f"/* WASDW|{_rhex(6)}|{int(time.time())} */")

    parts.append(build_dead_code(R.randint(5, 8)))

    # ── Kritik string'ler — regex bulmasa da garantili RC4 tablosuna girer ──
    # Endpoint path'leri ve önemli değerler her zaman kendi RC4 katmanından geçer.
    # Kaynak kodda literal olarak kalmaz, npm obfuscator'ın public string array'ine düşmez.
    #
    # Kural: yeni bir /api/* endpoint veya gizli kalması gereken string eklenirse
    # BURAYA da eklenmeli. inject_pre() zaten kaynak dosyayı tarayıp CRITICAL_STRINGS'te
    # olmayan /api/ string'leri için build-time uyarı verir (aşağıya bak).
    CRITICAL_STRINGS = [
        "/api/security/challenge",
        "/api/security/verify-challenge",
        "/api/security/env-signal",
        "/api/auth/login",
        "/api/auth/register",
        "/api/auth/logout",
        "/api/marketplace",
        "/api/user",
        "application/json",
        "Content-Type",
        "same-origin",
        "credentials",
        "login_ticket",
        "status",
        "WASDEngine",
        "__WASDW_PAGE_NONCE",
        "HeadlessChrome",
        "PhantomJS",
        "webdriver",
        "navigator.webdriver",
    ]

    # ── String toplama: blacklist mantığı ────────────────────────────────────
    # Eski whitelist regex ([A-Za-z][A-Za-z0-9._/\-: ]{3,119}) /api/..., ?v=...,
    # rakam/özel karakter başlangıcını ve ; & = ! @ # ( ) % içeren stringleri kaçırıyordu.
    #
    # Yeni yaklaşım: sadece gerçekten sorunlu karakterleri dışarıda bırak —
    #   tırnak karakterleri (kaçış sorunu), template literal ayracı (`),
    #   newline/null karakterleri, backslash.
    # Uzunluk: 3–200 karakter.
    #
    # Cap kaldırıldı — taşan string'ler sessizce plaintext kalmasın.
    # Bunun yerine: toplam tablo boyutu 512'yi aşarsa uyarı bas (performans eşiği),
    # ve taşma olmadığından her string RC4 katmanına giriyor.
    strs = list(dict.fromkeys(CRITICAL_STRINGS))   # deduplicated, sıralı
    smap = {s: i for i, s in enumerate(strs)}

    # Blacklist: sadece şu karakterleri içerenleri dışla → " ' ` \ \n \r \0
    _str_pattern = re.compile(
        r"""(?:["'])((?:[^"'`\\\n\r\x00]){3,200})(?:["'])"""
    )
    for m in _str_pattern.finditer(code):
        v = m.group(1)
        # Çok kısa (≤4 char) veya zaten var
        if len(v) <= 4 or v in smap:
            continue
        smap[v] = len(strs)
        strs.append(v)
        # Cap yok — tüm string'ler RC4 tablosuna girer

    # ── Tablo boyutu uyarısı ─────────────────────────────────────────────────
    # 512+ string RC4 tablosu obfuscator pipeline'ını belirgin yavaşlatabilir.
    # Uyarı bilgi amaçlı — build durdurmaz.
    _n_captured  = len(strs)
    _n_critical  = len(CRITICAL_STRINGS)
    _n_extra     = _n_captured - _n_critical
    if _n_captured > 512:
        print(
            f"  [UYARI] RC4 tablo boyutu yüksek: {_n_captured} string "
            f"({_n_critical} kritik + {_n_extra} auto-captured)  ({fname})  "
            f"— build yavaşlayabilir, gerekirse _str_pattern min uzunluğunu artır"
        )
    else:
        print(f"  [RC4] {fname}: {_n_captured} string tabloya eklendi "
              f"({_n_critical} kritik + {_n_extra} auto-captured)")

    # ── Build-time uyarı: /api/ içerip CRITICAL_STRINGS'te olmayan string'ler ─
    # Regex tarafından yakalanıp tabloya girmiş olabilirler; ama garantili ilk
    # slotta değiller. Geliştirici bilinçli karar versin.
    _api_pattern = re.compile(r"""(?:["'])((?:/api/|/static/)[^"'`\\\n\r\x00]{2,199})(?:["'])""")
    _missing_critical: list[str] = []
    for m in _api_pattern.finditer(code):
        v = m.group(1)
        if v not in set(CRITICAL_STRINGS):
            _missing_critical.append(v)
    if _missing_critical:
        # Tekrarsız listele
        seen: set[str] = set()
        for s in _missing_critical:
            if s not in seen:
                seen.add(s)
                print(f"  [UYARI] CRITICAL_STRINGS'te eksik endpoint/path: {s!r}  ({fname})")

    nt_code, _nt = build_number_table()
    parts.append(nt_code)

    # ── Sıra: önce string array (dec_fn gerekli), sonra env_check (URL'i RC4'ten çözer)
    # build_string_array → dec_fn üretir → build_env_check(dec_fn, url_idx) env-signal URL'ini
    # artık fromCharCode yerine RC4 decoder üzerinden alır.

    # build_string_array: poison_var yok — parçalarda string decoder bozulmamalı
    dec_fn = ""
    if strs:
        # Kritik stringler: AES-GCM + ephemeral mode
        # - AES-256-GCM: crypto.subtle native (toString() hook engellenir)
        # - Ephemeral: cache TTL 2-5 saniye
        sc, dec_fn = build_string_array(strs, poison_var="",
                                         nonce_cap_name=nonce_cap_name,
                                         session_cap_name=session_cap_name,
                                         ephemeral_mode=True,
                                         use_aes_gcm=True)  # ← AES-GCM aktif
        parts.append(sc)

    # env_check: parçalarda kapalı (false positive ve string bozulma riski)
    # Sadece loader'da aktif — dec_fn varsa URL RC4 tablosundan çözülür
    if no_env_check:
        v_poison = ""
    else:
        # env-signal URL'inin CRITICAL_STRINGS içindeki index'ini bul
        _env_signal_url = "/api/security/env-signal"
        _url_idx = strs.index(_env_signal_url) if _env_signal_url in strs else -1
        env_code, v_poison = build_env_check(dec_fn=dec_fn, url_idx=_url_idx)
        parts.append(env_code)

    # debugger trap sadece loader'da — parçalarda yok
    if not no_debugger:
        # Yeni çok kanallı DevTools detection (console.table render, Function.constructor, vb.)
        parts.append(build_multi_channel_devtools_detect())
        # Legacy timing guards (fallback/ek katman)
        parts.append(build_self_defending_loop())
        parts.append(build_timing_guard())
        # Worker-based timing probe — CDP override ana thread'i patch'lese bile
        # Worker context'e ulaşamaz; ikinci bağımsız kanal
        parts.append(build_worker_timing_guard())

    parts.append(build_opaque_predicates(R.randint(4, 7)))
    # number table'ı dead code içinde kullan — "kullanılmayan değişken" işaretlemesini engelle
    parts.append(build_dead_code(R.randint(3, 5), nt_name=_nt))

    # ── CRITICAL_STRINGS literal → dec_fn(N) lazy replacement ───────────────
    # inject_pre RC4 decoder üretiyor ama literal'lara dokunmuyordu — decoder
    # fiilen kullanılmıyordu. Şimdi CRITICAL_STRINGS'teki her string'in
    # tırnaklı literal'ı (hem " hem ') kaynak kodda dec_fn(N) çağrısına döner.
    # Lazy + memoized: first call decode eder, sonraki çağrılar cache'ten döner.
    # Sadece CRITICAL_STRINGS (ilk _n_critical adet) replace edilir.
    #
    # Part dosyaları, decoy ve puzzle için ATLA.
    # Bu dosyalar inject_pre'den geçiyor ama kendi IIFE bağlamında dec_fn
    # tanımlı değil — lazy replacement obfuscator parse hatasına yol açar.
    # Sadece wasd-loader.js için aktif: loader kendi RC4 tablosuyla birlikte
    # çalışır, dec_fn (V['D']) loader bağlamında tanımlıdır.
    _LAZY_FNAMES = {"wasd-loader.js"}
    if dec_fn and strs and fname in _LAZY_FNAMES:
        _lazy_code = code
        for _ci, _cs in enumerate(strs[:_n_critical]):
            _lazy_code = _lazy_code.replace(f'"{_cs}"', f'{dec_fn}({_ci})')
            _lazy_code = _lazy_code.replace(f"'{_cs}'", f'{dec_fn}({_ci})')
        if _lazy_code != code:
            _replaced = sum(
                1 for _cs in strs[:_n_critical]
                if f'"{_cs}"' in code or f"'{_cs}'" in code
            )
            print(f"  [lazy] {fname}: {_replaced} kritik literal dec_fn() cagrisina donusturuldu")
        code = _lazy_code

    inner = "\n".join(parts) + "\n" + code
    # nonce_cap_name ve session_cap_name bu scope'ta tanımlı — _derive_key_js çağrısından gelir
    return (inner, nonce_cap_name, session_cap_name)


def _build_decoy_behavior() -> tuple[str, str]:
    """
    Decoy dosyasının davranışsal ayak izini gerçek parçalara yaklaştırır.
    Üç bileşen:
      1. Honeypot XHR — /api/security/hb endpoint'ine rate-limitli fire-and-forget POST
         (gerçek part4'ün /api/security/* çağrılarını taklit eder)
      2. Sahte CustomEvent dispatch — __wasd_core_ready veya başka bir event adı
         (gerçek part4'ün event dispatch davranışını taklit eder)
      3. setTimeout ile geciktirilmiş tetikleme — synchronous sandbox tespitini engeller

    Her build'de değişken adları ve gecikme miktarları randomize edilir.
    Honeypot endpoint'e gelen istek sunucu tarafında loglananır + potansiyel ban sinyali.
    """
    v_xhr   = _rn("_hx")
    v_ev    = _rn("_he")
    v_delay = R.randint(120, 600)   # ms — synchronous sandbox'ta timeout hiç çalışmaz
    v_fn    = _rn("_hf")

    # Honeypot endpoint path'i — build-time random suffix + geniş prefix havuzu
    # Sabit 5 path yerine: havuzdan rastgele prefix + _rhex(3) suffix →
    # her build farklı path, analist birkaç deploy toplayarak kümeyi ezberleyemez.
    _hb_prefixes = [
        "/api/security/",
        "/api/shield/",
        "/api/check/",
        "/api/verify/",
        "/api/probe/",
        "/api/monitor/",
        "/api/health/",
        "/api/status/",
        "/api/ping/",
        "/api/trace/",
        "/api/metric/",
        "/api/signal/",
        "/api/report/",
        "/api/audit/",
        "/api/watch/",
    ]
    _hb_words = [
        "hb", "ping", "ack", "beacon", "tick",
        "beat", "pulse", "check", "sync", "poll",
        "poke", "tag", "mark", "sweep", "wave",
        "echo", "snap", "tap", "scan", "node",
    ]
    _hb_prefix = R.choice(_hb_prefixes)
    _hb_word   = R.choice(_hb_words)
    _hb_suffix = _rhex(3)                        # build-time random 3-hex suffix
    hb_path = f"{_hb_prefix}{_hb_word}-{_hb_suffix}"

    # Sahte event adı — geniş havuz + build-time random suffix
    _ev_prefixes = [
        "__wasd_", "__shield_", "__core_", "__sys_",
        "__app_", "__boot_", "__init_", "__mod_",
        "__proc_", "__task_",
    ]
    _ev_words = [
        "ok", "done", "ack", "ready", "loaded",
        "up", "live", "run", "go", "set",
        "pass", "start", "on", "wake", "active",
    ]
    _ev_prefix  = R.choice(_ev_prefixes)
    _ev_word    = R.choice(_ev_words)
    _ev_suffix  = _rhex(3)
    fake_event  = f"{_ev_prefix}{_ev_word}_{_ev_suffix}"

    # XHR body: sahte ama gerçeğe benzer payload (hex nonce)
    fake_nonce   = _rhex(8)
    fake_ts_expr = _num_expr(int(time.time()) & 0xFFFF)

    # URL'yi env-signal ile aynı inline RC4 IIFE pattern'ı ile gizle —
    # fromCharCode tek satırda çözülür, RC4 blob'u key olmadan çözülemez.
    # Böylece "RC4 = gerçek, charCode = decoy" ayrımı ortadan kalkar.
    _url_key     = secrets.token_bytes(8)
    _url_enc_b64 = _b64(_rc4_encrypt(hb_path.encode(), _url_key))
    _url_key_js  = "[" + ",".join(str(b) for b in _url_key) + "]"
    _du_S = _rn("_dS"); _du_i = _rn("_di"); _du_j = _rn("_dj")
    _du_t = _rn("_dt"); _du_k = _rn("_dk"); _du_e = _rn("_de"); _du_o = _rn("_do")

    _url_decode_expr = (
        f"(function(){{"
        f"var {_du_k}={_url_key_js};"
        f"var {_du_e}=atob('{_url_enc_b64}');"
        f"var {_du_S}=[];"
        f"for(var {_du_i}=0;{_du_i}<256;{_du_i}++){_du_S}[{_du_i}]={_du_i};"
        f"var {_du_j}=0;"
        f"for(var {_du_i}=0;{_du_i}<256;{_du_i}++){{"
        f"{_du_j}=({_du_j}+{_du_S}[{_du_i}]+{_du_k}[{_du_i}%{_du_k}.length])%256;"
        f"var {_du_t}={_du_S}[{_du_i}];{_du_S}[{_du_i}]={_du_S}[{_du_j}];{_du_S}[{_du_j}]={_du_t};"
        f"}}"
        f"var _di2=0,_dj2=0,{_du_o}='';"
        f"for(var _dn=0;_dn<{_du_e}.length;_dn++){{"
        f"_di2=(_di2+1)%256;_dj2=(_dj2+{_du_S}[_di2])%256;"
        f"var {_du_t}={_du_S}[_di2];{_du_S}[_di2]={_du_S}[_dj2];{_du_S}[_dj2]={_du_t};"
        f"{_du_o}+=String.fromCharCode({_du_e}.charCodeAt(_dn)^{_du_S}[({_du_S}[_di2]+{_du_S}[_dj2])%256]);"
        f"}}"
        f"return {_du_o};"
        f"}})()"
    )

    v_url = _rn("_du")
    js = (
        f"(function(){{"
        f"function {v_fn}(){{"
        f"try{{"
        f"var {v_url}={_url_decode_expr};"
        f"var {v_xhr}=new XMLHttpRequest();"
        f"{v_xhr}.open('POST',{v_url},true);"
        f"{v_xhr}.setRequestHeader('Content-Type','application/json');"
        f"{v_xhr}.send(JSON.stringify({{n:'{fake_nonce}',t:{fake_ts_expr}}}));"
        f"}}catch(_xe){{}}"
        f"try{{"
        f"var {v_ev}=new CustomEvent('{fake_event}',{{detail:{{ts:Date.now()}},bubbles:false}});"
        f"document.dispatchEvent({v_ev});"
        f"}}catch(_ee){{}}"
        f"}}"
        f"if(typeof window!=='undefined'&&typeof document!=='undefined'){{"
        f"setTimeout({v_fn},{_num_expr(v_delay)});"
        f"}}"
        f"}})();"
    )
    # hb_path'i de döndür — process_split_parts manifest'e toplayacak
    return js, hb_path


def _build_decoy_js() -> tuple[str, str]:
    """
    Decoy stub için gerçek parçalara boyut ve DAVRANIŞSAL olarak yakın,
    ama işlevsiz JS üretir.
    Returns: (js_content, hb_path) — hb_path manifest'e eklenir, app.py route olarak kaydeder.

    Hedef: ~30–90KB (gerçek parçaların boyut aralığı) — Content-Length
    üzerinden decoy/gerçek ayrımını engeller.

    Davranışsal ayak izi: honeypot XHR + sahte CustomEvent dispatch eklenmiştir.
    Saldırgan "hangisi network/event side-effect üretiyor?" diye bakamaz —
    hepsi üretiyor, sadece honeypot endpoint'e gittiği için sunucu loglar.
    """
    parts = []
    parts.append(f"/* WASDW|{_rhex(6)}|{int(time.time())} */")

    # Gerçek parçalara benzer IIFE wrapper
    bid = _rhex(4)
    parts.append(f"(function(_w,_d){{")
    parts.append(f"'use strict';")

    # Sahte class tanımı — obfuscator class isimlerini minify eder
    cls = _rn("_DC")
    ctor_body = build_dead_code(R.randint(8, 12))
    parts.append(
        f"function {cls}(){{"
        f"{ctor_body}"
        f"this._id='{_rhex(8)}';"
        f"this._ts=Date.now();"
        f"}}"
    )

    # Sahte prototype metodlar — boyutu dolduran anlamsız fonksiyonlar
    for _ in range(R.randint(6, 10)):
        mname = _rn("_m")
        body  = build_dead_code(R.randint(10, 18))
        body += build_opaque_predicates(R.randint(4, 7))
        body += build_dead_code(R.randint(6, 10))
        parts.append(
            f"{cls}.prototype.{mname}=function(){{"
            f"var _r=false;"
            f"{body}"
            f"return _r;"
            f"}};"
        )

    # Sahte singleton + döngü (boyut doldurucu)
    inst = _rn("_inst")
    loop_var = _rn("_lv")
    parts.append(
        f"var {inst}=new {cls}();"
        f"(function(){{"
        f"for(var {loop_var}=0;{loop_var}<{R.randint(3,7)};{loop_var}++){{"
        f"{build_dead_code(R.randint(4, 6))}"
        f"{build_opaque_predicates(R.randint(3, 5))}"
        f"}}"
        f"}})();"
    )

    # Sahte event listener gövdesi (anlamsız ama gerçek koda benzer)
    ev_fn = _rn("_ef")
    ev_body = build_dead_code(R.randint(8, 14))
    ev_body += build_opaque_predicates(R.randint(5, 8))
    parts.append(
        f"function {ev_fn}(_e){{{ev_body}return false;}}"
    )

    # Sahte export — global'e bir şey ata (gerçek parçalar da bunu yapıyor)
    fake_flag = _rn("__wdf")
    parts.append(f"_w.{fake_flag}=true;")

    # ── Davranışsal ayak izi ─────────────────────────────────────────────────
    # Honeypot XHR + sahte CustomEvent — gerçek parçaların side-effect'lerini taklit eder.
    # Sandbox analizi "hangisi network/event üretiyor?" ile ayırt edemez.
    _decoy_js, _decoy_hb_path = _build_decoy_behavior()
    parts.append(_decoy_js)
    # hb_path'i döndür — _build_decoy_js çağıranı manifest'e toplayabilir

    parts.append(f"}})(window,document);")

    raw = "\n".join(parts)

    # inject_pre ile RC4 katmanı + dead code inject
    pre, _, _ = inject_pre(raw, "_decoy.js", no_debugger=True)

    # npm obfuscation — gerçek parçalarla aynı pipeline
    _uid = uuid.uuid4().hex[:8]
    tin  = os.path.join(DIST_DIR, f"_dcoytmp_{_uid}_in.js")
    tout = os.path.join(DIST_DIR, f"_dcoytmp_{_uid}_out.js")
    open(tin, "w", encoding="utf-8").write(pre)

    cfg = dict(_load_selected_profile())
    cfg["deadCodeInjectionThreshold"] = 0.15  # decoy'larda yüksek threshold — boyut doldurucu
    tmp_cfg = CONFIG_PATH + f".decoy.{_uid}.tmp.json"
    with open(tmp_cfg, "w", encoding="utf-8") as f:
        json.dump(cfg, f)

    b_ok = run_npm(tin, tout, "_decoy.js")
    if b_ok and os.path.exists(tout):
        result = open(tout, encoding="utf-8").read()
    else:
        result = pre  # npm başarısız olursa pre-obfuscated versiyonu kullan

    for _f in [tin, tout, tmp_cfg]:
        try: os.remove(_f)
        except: pass

    # wrap_post ile footer ekle (gerçek parçaların A-Post katmanı gibi)
    bid2   = _rhex(6)
    footer = build_dead_code(R.randint(3, 5))
    footer += build_opaque_predicates(R.randint(2, 4))
    content = f"/* WASDW|{bid2}|{int(time.time())} */\n" + result + "\n" + footer
    return content, _decoy_hb_path


def wrap_post(code: str, fname: str, no_debugger: bool = False) -> str:
    bid = _rhex(6)
    ts  = int(time.time())
    header = f"/* WASDW Shield | {bid} | {ts} */\n"

    footer  = build_dead_code(R.randint(3, 5))
    footer += build_opaque_predicates(R.randint(3, 4))
    # debugger trap sadece loader'da — parçalarda yok
    if not no_debugger:
        footer += build_multi_channel_devtools_detect()
        footer += build_self_defending_loop()
        footer += build_worker_timing_guard()

    fallback = "\n"
    return header + code + "\n" + footer + fallback

def run_npm(inp: str, out: str, fname: str) -> bool:
    # Build başına seçilen rastgele config'i al
    cfg = dict(_load_selected_profile())
    
    # Dosya-özgü override'ları uygula — range varsa rastgele seç
    overrides = FILE_CONFIG_OVERRIDES.get(fname, {})
    for key, value in overrides.items():
        if key.endswith("_range") and isinstance(value, tuple):
            # Range parametresi — rastgele seç
            param_name = key.replace("_range", "")
            cfg[param_name] = round(R.uniform(value[0], value[1]), 2)
        else:
            # Sabit değer
            cfg[key] = value
    
    tmp_cfg = CONFIG_PATH + f".{fname}.{uuid.uuid4().hex[:8]}.tmp.json"
    with open(tmp_cfg, "w", encoding="utf-8") as f:
        json.dump(cfg, f)

    env = os.environ.copy()
    if sys.platform == "win32":
        try:
            import winreg
            mp = up = ""
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment") as k:
                mp, _ = winreg.QueryValueEx(k, "PATH")
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as k:
                try: up, _ = winreg.QueryValueEx(k, "PATH")
                except FileNotFoundError: pass
            env["PATH"] = mp + os.pathsep + up + os.pathsep + env.get("PATH", "")
        except Exception:
            pass
    else:
        import glob as _glob
        extra = ["/usr/local/bin", "/usr/bin", "/bin", "/snap/bin"]
        extra += _glob.glob(os.path.expanduser("~/.nvm/versions/node/*/bin"))
        env["PATH"] = os.pathsep.join(extra) + os.pathsep + env.get("PATH", "")

    node = shutil.which("node", path=env.get("PATH", "")) or "node"
    cmd  = [node, NODE_BIN, inp, "--output", out, "--config", tmp_cfg]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=90)
        if r.returncode != 0:
            print(f"    [!] npm hata: {r.stderr[:300]}")
            return False
        return True
    except subprocess.TimeoutExpired:
        print("    [!] npm timeout"); return False
    except Exception as e:
        print(f"    [!] npm exception: {e}"); return False
    finally:
        try: os.remove(tmp_cfg)
        except: pass

def process_file(src: str, dst: str) -> bool:
    fname = os.path.basename(src)
    print(f"\n  ━━ {fname}")
    orig = open(src, encoding="utf-8").read()
    print(f"     ├─ Kaynak  : {len(orig):,} B")

    pre, _, _ = inject_pre(orig, fname)
    print(f"     ├─ A-Pre   : {len(pre):,} B  (+{len(pre)-len(orig):,})")

    tin  = src + f".{uuid.uuid4().hex[:8]}.tmp_in.js"
    tout = src + f".{uuid.uuid4().hex[:8]}.tmp_out.js"
    open(tin, "w", encoding="utf-8").write(pre)

    b_ok = run_npm(tin, tout, fname)
    if b_ok and os.path.exists(tout):
        npm_out = open(tout, encoding="utf-8").read()
        print(f"     ├─ B-npm   : {len(npm_out):,} B")
    else:
        print(f"     ├─ B-npm   : BASARISIZ — sadece A katmani")
        npm_out = pre

    for f in [tin, tout]:
        try: os.remove(f)
        except: pass

    final = wrap_post(npm_out, fname)
    print(f"     ├─ A-Post  : {len(final):,} B")

    os.makedirs(os.path.dirname(dst), exist_ok=True)
    open(dst, "w", encoding="utf-8").write(final)

    sh = lambda x: hashlib.sha256(x.encode()).hexdigest()[:10]
    print(f"     ├─ Src SHA : {sh(orig)}")
    print(f"     └─ Out SHA : {sh(final)}  ✓")
    return True

# ─────────────────────────────────────────────────────────────
#  WASD-core SPLIT MODE
#  Kaynak: static/js/wasd-parts/part{1-4}-*.js
#  Çıktı : static/js/dist/WASD-core-V{random}.js  (x4)
#          static/js/dist/wasd-loader.js           (loader manifest)
# ─────────────────────────────────────────────────────────────

PARTS_DIR    = os.path.join(BASE_DIR, "static", "js", "wasd-parts")
# Manifest static/ dışında — web üzerinden erişilemez, sadece Python process okur
RUNTIME_DIR  = os.path.join(BASE_DIR, "_runtime")
MANIFEST_PATH = os.path.join(RUNTIME_DIR, "wasd-manifest.json")
_BUILD_WASM_JS  = os.path.join(BASE_DIR, "build_wasm.js")
_WAT_SRC        = os.path.join(PARTS_DIR, "vm_transform.wat")
_WASM_OUT       = os.path.join(DIST_DIR,  "vm_transform.wasm")


def _build_puzzle_js(manifest: dict) -> bool:
    """
    static/js/puzzle-challenge.js'i A-Pre + B-npm + A-Post pipeline'ından geçirir.
    Çıktı: dist/WASD-core-V<hex>.js  (WASD-core-V stili — saldırgan puzzle JS'i ayırt edemez)
    Decoy: dist/WASD-core-V<hex>.js  (sahte, işlevsiz — boyut ve isim aynı formatta)
    Manifest'e 'puzzle' key ile kaydedilir.
    """
    puzzle_src = os.path.join(JS_DIR, "puzzle-challenge.js")
    if not os.path.isfile(puzzle_src):
        print("  [!] puzzle-challenge.js bulunamadi, atlanıyor")
        return False

    print("\n  ── puzzle-challenge.js  obfuscation ────────────────────────")
    orig = open(puzzle_src, encoding="utf-8").read()
    print(f"     ├─ Kaynak  : {len(orig):,} B")

    # Eski puzzle çıktısını temizle
    old_puzzle = manifest.get("puzzle", "")
    old_puzzle_decoy = manifest.get("puzzle_decoy", "")
    for old in [old_puzzle, old_puzzle_decoy]:
        if old:
            old_path = os.path.join(DIST_DIR, old)
            if os.path.isfile(old_path):
                try: os.remove(old_path)
                except: pass

    # A-Pre (debugger yok — puzzle sayfada yüklü, debugger loop istemeyiz)
    pre, _, _ = inject_pre(orig, "puzzle-challenge.js", no_debugger=True, no_env_check=True)
    print(f"     ├─ A-Pre   : {len(pre):,} B  (+{len(pre)-len(orig):,})")

    # B-npm obfuscation
    out_name = f"WASD-core-V{_random_wasd_suffix()}.js"
    dst      = os.path.join(DIST_DIR, out_name)
    tin      = puzzle_src + f".{uuid.uuid4().hex[:8]}.tmp_in.js"
    tout     = puzzle_src + f".{uuid.uuid4().hex[:8]}.tmp_out.js"
    open(tin, "w", encoding="utf-8").write(pre)

    cfg = dict(_load_selected_profile())
    cfg["deadCodeInjectionThreshold"] = 0.09
    tmp_cfg = CONFIG_PATH + f".puzzle.{uuid.uuid4().hex[:8]}.tmp.json"
    with open(tmp_cfg, "w", encoding="utf-8") as f:
        json.dump(cfg, f)

    b_ok = run_npm(tin, tout, "puzzle-challenge.js")
    if b_ok and os.path.exists(tout):
        npm_out = open(tout, encoding="utf-8").read()
        print(f"     ├─ B-npm   : {len(npm_out):,} B")
    else:
        print(f"     ├─ B-npm   : BASARISIZ — sadece A-Pre")
        npm_out = pre

    for fp in [tin, tout, tmp_cfg]:
        try: os.remove(fp)
        except: pass

    # A-Post (hafif footer)
    bid    = _rhex(6)
    ts_val = int(time.time())
    footer = build_dead_code(R.randint(2, 3)) + build_opaque_predicates(R.randint(2, 3))
    final  = f"/* WASDW|{bid}|{ts_val} */\n" + npm_out + "\n" + footer
    print(f"     ├─ A-Post  : {len(final):,} B")

    os.makedirs(DIST_DIR, exist_ok=True)
    open(dst, "w", encoding="utf-8").write(final)

    sh = lambda x: hashlib.sha256(x.encode()).hexdigest()[:10]
    print(f"     ├─ Src SHA : {sh(orig)}")
    print(f"     └─ Out SHA : {sh(final)}  ✓")

    # Decoy puzzle — boyut aynı formatta, içerik tamamen işlevsiz
    decoy_name = f"WASD-core-V{_random_wasd_suffix()}.js"
    decoy_dst  = os.path.join(DIST_DIR, decoy_name)
    decoy_raw, _ = _build_decoy_js()
    open(decoy_dst, "w", encoding="utf-8").write(decoy_raw)
    print(f"     ├─ Decoy   : {decoy_name}  ({len(decoy_raw):,} B)")

    # Manifest'e kaydet
    manifest["puzzle"]       = out_name
    manifest["puzzle_decoy"] = decoy_name
    manifest["ts"]           = ts_val
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    print(f"  [+] Puzzle JS manifest'e eklendi: {out_name}")

    return True


def _build_wasm_module() -> bool:
    """
    build_wasm.js'i subprocess ile çağırır, vm_transform.wasm'ı dist/'e üretir.
    Başarıda True, hata/yokluk durumunda False döner.
    Fail-soft: bu fonksiyonun başarısız olması build'i durdurmaz.
    Windows ve Linux/macOS VDS uyumlu.
    """
    if not os.path.isfile(_BUILD_WASM_JS):
        print(f"  [!] build_wasm.js bulunamadi: {_BUILD_WASM_JS}")
        return False
    if not os.path.isfile(_WAT_SRC):
        print(f"  [!] WAT kaynak bulunamadi: {_WAT_SRC}")
        return False

    env = os.environ.copy()

    # Windows: registry'den PATH al
    if sys.platform == "win32":
        try:
            import winreg
            mp = up = ""
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment") as k:
                mp, _ = winreg.QueryValueEx(k, "PATH")
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as k:
                try: up, _ = winreg.QueryValueEx(k, "PATH")
                except FileNotFoundError: pass
            env["PATH"] = mp + os.pathsep + up + os.pathsep + env.get("PATH", "")
        except Exception:
            pass
    else:
        # Linux/macOS VDS: yaygın Node konumlarını PATH'e ekle
        extra_paths = [
            "/usr/local/bin", "/usr/bin", "/bin",
            os.path.expanduser("~/.nvm/versions/node/*/bin"),   # nvm
            "/snap/bin",                                          # snap
        ]
        import glob as _glob
        resolved = []
        for p in extra_paths:
            if "*" in p:
                resolved.extend(_glob.glob(p))
            else:
                resolved.append(p)
        env["PATH"] = os.pathsep.join(resolved) + os.pathsep + env.get("PATH", "")

    node = shutil.which("node", path=env.get("PATH", "")) or "node"
    print(f"  [WASM] node: {node}")
    cmd  = [node, _BUILD_WASM_JS,
            "--wat", _WAT_SRC,
            "--out", _WASM_OUT]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           env=env, timeout=60)
        # build_wasm.js çıktısını yansıt
        for line in (r.stdout + r.stderr).splitlines():
            print(f"     {line}")
        if r.returncode != 0:
            print(f"  [!] build_wasm.js exit {r.returncode}")
            return False
        return os.path.isfile(_WASM_OUT)
    except subprocess.TimeoutExpired:
        print("  [!] WASM build timeout"); return False
    except Exception as e:
        print(f"  [!] WASM build exception: {e}"); return False

# Sıralı kaynak parçalar — dependency sırası bozulmamalı
WASD_PARTS_ORDER = [
    "part1-utils.js",
    "part2-fingerprint.js",
    "part3-pow-vm.js",
    "part4-orchestrator.js",
]

PART_DEAD_CODE_THRESHOLD = {
    "part1-utils.js":       {"deadCodeInjectionThreshold": 0.06},
    "part2-fingerprint.js": {"deadCodeInjectionThreshold": 0.07},
    "part3-pow-vm.js":      {"deadCodeInjectionThreshold": 0.06},
    "part4-orchestrator.js":{"deadCodeInjectionThreshold": 0.08},
}


def _random_wasd_suffix() -> str:
    """8 haneli rastgele hex suffix — WASD-core-V6a89bb48.js gibi görünür"""
    return f"{R.randint(0x10000000, 0xFFFFFFFF):08x}"


def process_split_parts(dry_run: bool = False) -> dict | None:
    """
    Her part dosyasını bağımsız olarak obfuscate eder,
    dist/ klasörüne WASD-core-V{random}.js adıyla kaydeder.
    Sonunda wasd-manifest.json döner:
        {"parts": ["WASD-core-V123456789.js", ...], "ts": 1234567890}
    """
    global _SELECTED_PROFILE
    _SELECTED_PROFILE = None  # Her build'de yeni profil seçilsin
    os.makedirs(DIST_DIR, exist_ok=True)
    os.makedirs(RUNTIME_DIR, exist_ok=True)  # manifest burada — static/ dışında

    # ── Dist tam temizlik ─────────────────────────────────────────────────────
    # dist/'teki TÜM .js dosyaları silinir (WASD-core-V*, WASD-Loader-V*, tmp* vb.)
    # vm_transform.wasm korunur — sonraki adımda yeniden üretilir.
    # Her build taze başlar, eski/bozuk/fazla dosya kalmaz.
    _deleted = 0
    for _fname in os.listdir(DIST_DIR):
        if _fname.endswith(".js"):
            try:
                os.remove(os.path.join(DIST_DIR, _fname))
                _deleted += 1
            except Exception:
                pass
    if _deleted:
        print(f"  [+] Dist temizlendi: {_deleted} eski .js dosya silindi")

    print("=" * 60)
    print("  WASDW Split Obfuscation Pipeline  v1")
    print(f"  Kaynak : {PARTS_DIR}")
    print(f"  Cikti  : {DIST_DIR}")
    print("=" * 60)

    # Build-time random global adları — tüm parçalar ve loader aynı keyleri kullanır
    sc_key  = "_w" + _rhex(8)   # ShieldCore  global adı
    scr_key = "_w" + _rhex(8)   # ShieldCoreReady global adı
    # Event adı da build başına random — sabit "__wasd_core_ready" kırılırsa future deploy'lar etkilenmez
    ev_key  = "__wasd_" + _rhex(6)   # CustomEvent adı: dispatch (part4) + addEventListener (login/register)

    print(f"  [+] sc_key={sc_key}  scr_key={scr_key}  ev_key={ev_key}")

    generated_names = []
    ok = fail = 0

    # ── Build başına gerçek parça grupları: 3 veya 4 dosya ─────────────────
    # Her build'de rastgele: ya 4 ayrı dosya (standart) ya da part1+part2
    # birleştirilmiş 3 dosya. part3 ve part4 her zaman ayrı kalır (dependency).
    # İstatistiksel analiz: n_real ∈ {3,4} — sabit 4 değil.
    _merge_p1_p2 = R.random() < 0.5   # %50 ihtimalle part1+part2 birleştir

    if _merge_p1_p2:
        # 3 grup: [part1+part2], [part3], [part4]
        _build_groups = [
            ["part1-utils.js", "part2-fingerprint.js"],
            ["part3-pow-vm.js"],
            ["part4-orchestrator.js"],
        ]
        print(f"  [+] Part gruplama: 3 dosya (part1+part2 birlestirile)")
    else:
        # 4 grup: standart
        _build_groups = [
            ["part1-utils.js"],
            ["part2-fingerprint.js"],
            ["part3-pow-vm.js"],
            ["part4-orchestrator.js"],
        ]
        print(f"  [+] Part gruplama: 4 dosya (standart)")

    for part_files in _build_groups:
        # Grup içindeki kaynak dosyaları oku ve birleştir
        group_srcs = []
        for part_file in part_files:
            src = os.path.join(PARTS_DIR, part_file)
            if not os.path.exists(src):
                print(f"\n  [x] Bulunamadi: {src}")
                fail += 1
                group_srcs = []
                break
            group_srcs.append((part_file, open(src, encoding="utf-8").read()))

        if not group_srcs:
            continue

        # Birleşik kaynak — birden fazla dosyayı \n ile birleştir
        part_file = group_srcs[-1][0]   # grubun adı için son dosya (orchestrator vs.)
        orig = "\n".join(content for _, content in group_srcs)

        out_name = f"WASD-core-V{_random_wasd_suffix()}.js"
        dst = os.path.join(DIST_DIR, out_name)

        label = "+".join(pf for pf, _ in group_srcs)
        print(f"\n  ━━ {label}  →  {out_name}")
        print(f"     ├─ Kaynak  : {len(orig):,} B")

        # part4-orchestrator: placeholder'ları build-time random key ile replace et
        if part_file == "part4-orchestrator.js":
            orig = orig.replace("'__WASD_SC_KEY__'", f"'{sc_key}'")
            orig = orig.replace('"__WASD_SC_KEY__"', f'"{sc_key}"')
            # bracket notation için de replace — W['__WASD_SC_KEY__']
            orig = orig.replace("['__WASD_SC_KEY__']", f"['{sc_key}']")
            orig = orig.replace('["__WASD_SC_KEY__"]', f'["{sc_key}"]')
            # Event adı placeholder — dispatch ve addEventListener için
            orig = orig.replace("'__WASD_EV_KEY__'", f"'{ev_key}'")
            orig = orig.replace('"__WASD_EV_KEY__"', f'"{ev_key}"')
            print(f"     ├─ SC_KEY  : {sc_key} (placeholder replaced)")
            print(f"     ├─ EV_KEY  : {ev_key} (event adı replaced)")

        if dry_run:
            # dry-run: sadece kopyala, obfuscate etme
            open(dst, "w", encoding="utf-8").write(orig)
            generated_names.append(out_name)
            ok += 1
            print(f"     └─ [dry-run] Kopyalandı")
            continue

        # --- A-Pre katmanı --- (parçalarda debugger yok, env_check yok)
        pre, _, _ = inject_pre(orig, part_file, no_debugger=True, no_env_check=True)
        print(f"     ├─ A-Pre   : {len(pre):,} B  (+{len(pre)-len(orig):,})")

        # --- B-npm obfuscation ---
        # Seçili profil + part'a özgü threshold override
        cfg = dict(_load_selected_profile())
        cfg.update(PART_DEAD_CODE_THRESHOLD.get(part_file, {}))
        tmp_cfg = CONFIG_PATH + f".{part_file}.{uuid.uuid4().hex[:8]}.tmp.json"
        with open(tmp_cfg, "w", encoding="utf-8") as f:
            json.dump(cfg, f)

        tin  = src + f".{uuid.uuid4().hex[:8]}.tmp_in.js"
        tout = src + f".{uuid.uuid4().hex[:8]}.tmp_out.js"
        open(tin, "w", encoding="utf-8").write(pre)

        b_ok = run_npm(tin, tout, part_file)
        if b_ok and os.path.exists(tout):
            npm_out = open(tout, encoding="utf-8").read()
            print(f"     ├─ B-npm   : {len(npm_out):,} B")
        else:
            print(f"     ├─ B-npm   : BASARISIZ — sadece A katmani")
            npm_out = pre

        for f in [tin, tout, tmp_cfg]:
            try: os.remove(f)
            except: pass

        # --- A-Post wrap (ShieldCore fallback'i sadece orchestrator'a ekle) ---
        if part_file == "part4-orchestrator.js":
            final = wrap_post(npm_out, part_file, no_debugger=True)
        else:
            # Diğer parçalar için hafif footer (self-defending + opaque)
            bid = _rhex(6)
            ts  = int(time.time())
            header = f"/* WASDW|{bid}|{ts} */\n"
            footer  = build_dead_code(R.randint(2, 3))
            footer += build_opaque_predicates(R.randint(2, 3))
            final = header + npm_out + "\n" + footer

        print(f"     ├─ A-Post  : {len(final):,} B")

        os.makedirs(os.path.dirname(dst), exist_ok=True)
        open(dst, "w", encoding="utf-8").write(final)

        sh = lambda x: hashlib.sha256(x.encode()).hexdigest()[:10]
        print(f"     ├─ Src SHA : {sh(orig)}")
        print(f"     └─ Out SHA : {sh(final)}  ✓")

        generated_names.append(out_name)
        ok += 1

    print("\n" + "=" * 60)
    print(f"  {ok} basarili  |  {fail} basarisiz")
    print("=" * 60)

    if fail > 0:
        return None

    manifest = {
        "parts": generated_names,
        "ts": int(time.time())
    }
    # Önceki fixed_decoys ve wasd_core alanlarını koru
    if os.path.isfile(MANIFEST_PATH):
        try:
            with open(MANIFEST_PATH, encoding="utf-8") as _f:
                _old = json.load(_f)
            for _key in ("fixed_decoys", "wasd_core"):
                if _key in _old:
                    manifest[_key] = _old[_key]
        except Exception:
            pass
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    print(f"\n  [+] Manifest yazildi: {MANIFEST_PATH}")
    print(f"  [+] Parcalar: {generated_names}")

    # ── WASM build: vm_transform.wat → dist/vm_transform.wasm ────────────────
    # part3-pow-vm.js'in WASM primary path'i için gerekli.
    # build_wasm.js başarısız olursa uyarı verilir, build iptal edilmez —
    # part3 JS fallback'e geçer.
    _wasm_ok = _build_wasm_module()
    if _wasm_ok:
        manifest["wasm_vm"] = "vm_transform.wasm"
        print(f"  [+] WASM modulu: dist/vm_transform.wasm")
    else:
        # ── WASM build başarısız — alert + hard-fail kontrolü ─────────────
        _wasm_fail_msg = (
            "WASM BUILD BASARISIZ: vm_transform.wasm uretilemedi.\n"
            "  Olasi nedenler: wabt paketi eksik (npm install wabt), "
            "Node bulunamadi, vm_transform.wat bozuk.\n"
            "  Etki: score() fonksiyonu WASM'da calismiyor, "
            "JS fallback devrede (zayiflatis deploy riski)."
        )
        print(f"  [!!!] {_wasm_fail_msg}", flush=True)

        # Webhook alert — WASDW_ALERT_WEBHOOK env var set edilmişse gönder
        try:
            import config as _cfg
            _webhook_url = getattr(_cfg, "WASM_ALERT_WEBHOOK", "") or ""
            if _webhook_url:
                import urllib.request as _ur, json as _js
                _payload = _js.dumps({
                    "text": f":warning: *WASDW WASM Build Hatası*\n```{_wasm_fail_msg}```",
                    "content": _wasm_fail_msg,   # Discord uyumu
                }).encode()
                _req = _ur.Request(
                    _webhook_url,
                    data=_payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                _ur.urlopen(_req, timeout=5)
                print("  [+] Webhook alert gonderildi.", flush=True)
        except Exception as _wh_e:
            print(f"  [!] Webhook gonderilemedi: {_wh_e}", flush=True)

        # Hard-fail kontrolü — WASDW_WASM_HARD_FAIL=true ise build'i durdur
        try:
            import config as _cfg2
            _hard_fail = getattr(_cfg2, "WASM_BUILD_HARD_FAIL", False)
        except Exception:
            _hard_fail = os.environ.get("WASDW_WASM_HARD_FAIL", "false").lower() == "true"

        if _hard_fail:
            print(
                "  [!!!] WASM_BUILD_HARD_FAIL=true — build durduruluyor.\n"
                "        Devam etmek icin WASDW_WASM_HARD_FAIL=false set edin\n"
                "        veya wabt'yi kurun: npm install wabt",
                flush=True
            )
            sys.exit(1)
        else:
            print(
                "  [!] WASM build basarisiz — part3 JS fallback kullanacak.\n"
                "      Production'da hard-fail icin: "
                "WASDW_WASM_HARD_FAIL=true",
                flush=True
            )

    # loader script'i üret — sc_key/scr_key/ev_key build-time random global adları döner
    # part_hashes: {real_pool_index: sha256_hex} — integrity chain için
    _part_hashes: dict[int, str] = {}
    for _pi, _pname in enumerate(generated_names):
        _ppath = os.path.join(DIST_DIR, _pname)
        if os.path.isfile(_ppath):
            _pcontent = open(_ppath, encoding="utf-8").read()
            _phash    = hashlib.sha256(_pcontent.encode("utf-8")).hexdigest()
            # real_indices[i] = pool pozisyonu (loader'ın pool array'indeki index)
            # Bu bilgiyi _write_loader_script içinde kullanacağız
            _part_hashes[_pi] = _phash   # key = part sırası (0,1,2...)
            print(f"  [+] Part {_pi} ({_pname[:20]}...) SHA256: {_phash[:12]}...")

    loader_name, fake_loader_name, _sc, _scr, _hp_paths, nonce_cap_global, session_cap_global, loader_integrity = _write_loader_script(
        generated_names, sc_key=sc_key, scr_key=scr_key, ev_key=ev_key,
        part_hashes=_part_hashes
    )

    manifest["loader"]      = loader_name
    manifest["fake_loader"] = fake_loader_name
    manifest["loader_integrity"] = loader_integrity  # SRI sha384 hash
    manifest["sc_key"]      = sc_key
    manifest["scr_key"]     = scr_key
    manifest["ev_key"]      = ev_key
    manifest["nonce_cap"]   = nonce_cap_global      # capability global adı (template'lere inject)
    manifest["session_cap"] = session_cap_global    # capability global adı (part4'e inject)
    manifest["honeypot_paths"] = list(set(_hp_paths))   # build-time random path'ler
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    print(f"  [+] Loader adi manifest'e eklendi: {loader_name}")
    print(f"  [+] Fake loader adi manifest'e eklendi: {fake_loader_name}")
    print(f"  [+] Loader SRI manifest'e eklendi (template'lerde kullanilacak)")
    print(f"  [+] sc_key={sc_key}  scr_key={scr_key}  ev_key={ev_key}")
    print(f"  [+] nonce_cap={nonce_cap_global}  session_cap={session_cap_global}")

    # ── login.js ve register.js: __WASD_EV_KEY__ placeholder'ını ev_key ile replace et
    # Bu dosyalar dist/'e kopyalanmıyor, static/js/ altında doğrudan serve ediliyor.
    # Placeholder her build'de doğru event adıyla değiştirilir.
    for _ev_file in ["login.js", "register.js"]:
        _ev_src = os.path.join(JS_DIR, _ev_file)
        if os.path.isfile(_ev_src):
            _ev_content = open(_ev_src, encoding="utf-8").read()
            if "__WASD_EV_KEY__" in _ev_content:
                _ev_replaced = _ev_content.replace("'__WASD_EV_KEY__'", f"'{ev_key}'")\
                                           .replace('"__WASD_EV_KEY__"', f'"{ev_key}"')
                open(_ev_src, "w", encoding="utf-8").write(_ev_replaced)
                print(f"  [+] {_ev_file}: EV_KEY={ev_key} yazildi")
            else:
                # Placeholder yok — önceki build'den kalan ev_key var, güncelle
                import re as _re
                _ev_replaced = _re.sub(
                    r"addEventListener\('(__wasd_[a-f0-9]+)'\s*,",
                    f"addEventListener('{ev_key}',",
                    _ev_content
                )
                _ev_replaced = _re.sub(
                    r'addEventListener\("(__wasd_[a-f0-9]+)"\s*,',
                    f'addEventListener("{ev_key}",',
                    _ev_replaced
                )
                if _ev_replaced != _ev_content:
                    open(_ev_src, "w", encoding="utf-8").write(_ev_replaced)
                    print(f"  [+] {_ev_file}: EV_KEY güncellendi → {ev_key}")
                else:
                    print(f"  [!] {_ev_file}: EV_KEY placeholder bulunamadi, kontrol et")

    # ── puzzle-challenge.js obfuscation ──────────────────────────────────────
    # WASD-core-V*.js stili isim — saldırgan puzzle JS'i ayırt edemez.
    # Ayrıca bir sahte (decoy) puzzle JS de üretilir.
    _puzzle_ok = _build_puzzle_js(manifest)
    if not _puzzle_ok:
        print("  [!] puzzle-challenge.js obfuscation basarisiz — ham kaynak kullanılacak")

    return manifest


def _write_loader_script(part_names: list, sc_key: str = "", scr_key: str = "", ev_key: str = "",
                         part_hashes: dict | None = None) -> tuple:
    """
    dist/wasd-loader.js — RC4 string table + 3-katman XOR indeks + CFF + self-defending.
    Sonra javascript-obfuscator pipeline'ından geçirilir → ~100KB çıktı.
    sc_key  : ShieldCore global adı  (process_split_parts tarafından üretilir)
    scr_key : ShieldCoreReady global adı
    ev_key  : CustomEvent adı — build başına random, sabit "__wasd_core_ready" değil
    scr_key : ShieldCoreReady global adı
    Returns: (loader_name, fake_loader_name, sc_key, scr_key)
    """
    base_path = "/static/js/dist/"
    ts        = int(time.time())
    n_real    = len(part_names)

    # Dışarıdan verilmemişse fallback olarak üret (tek başına çalıştırma senaryosu)
    if not sc_key:
        sc_key  = "_w" + _rhex(8)
    if not scr_key:
        scr_key = "_w" + _rhex(8)
    if not ev_key:
        ev_key  = "__wasd_" + _rhex(6)

    # Fixed decoy'ları manifest'ten oku (kullanıcının elle koyduğu dosyalar)
    fixed_decoys: list[str] = []
    if os.path.isfile(MANIFEST_PATH):
        try:
            with open(MANIFEST_PATH, encoding="utf-8") as _f:
                _m = json.load(_f)
            fixed_decoys = [d for d in _m.get("fixed_decoys", [])
                            if os.path.isfile(os.path.join(DIST_DIR, d))]
        except Exception:
            fixed_decoys = []

    # Toplam 5-7 decoy: fixed + random — her build'de farklı pool boyutu
    # 10 civarı toplam dosya hedefi: n_real(3-4) + n_decoy(5-7) = 8-11
    n_fixed  = len(fixed_decoys)
    n_random = max(3, R.randint(5, 7) - n_fixed)
    n_decoy  = n_fixed + n_random
    pool_size = n_real + n_decoy

    # ── Pool: gerçek parçaları random pozisyonlara yerleştir
    random_decoy_names = [f"WASD-core-V{_random_wasd_suffix()}.js" for _ in range(n_random)]
    decoy_names = fixed_decoys + random_decoy_names
    pool         = [''] * pool_size
    real_indices: list[int] = []
    positions    = sorted(R.sample(range(pool_size), n_real))
    decoy_iter   = iter(decoy_names)
    for i in range(pool_size):
        if i in positions:
            pool[i] = part_names[positions.index(i)]
            real_indices.append(i)
        else:
            pool[i] = next(decoy_iter)

    real_set      = set(real_indices)
    decoy_indices = [i for i in range(pool_size) if i not in real_set]

    # ── Integrity hash sözlüğü: {pool_pos: sha256_hex} ───────────────────────
    # part_hashes[part_idx] → sha256; real_indices[part_idx] → pool_pos
    # RC4/AES ile gömülür — düz hash string loader'a yazılmaz.
    _integrity_map: dict[int, str] = {}   # {pool_pos: sha256}
    if part_hashes:
        for _pi, _ph in part_hashes.items():
            if _pi < len(real_indices):
                _integrity_map[real_indices[_pi]] = _ph

    # Integrity hash'leri AES-GCM ile şifrele — loader RC4 key'i ile açar
    # Düz SHA-256 hex gömülürse saldırgan patch'li hash'i değiştirebilir.
    # Şifreli blob olmadan beklenen değeri bilemez.
    _integrity_aes_key = secrets.token_bytes(32)
    _integrity_iv      = secrets.token_bytes(12)
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM as _AESGCM
    _integrity_plain   = json.dumps(_integrity_map).encode("utf-8")
    _integrity_ct      = _AESGCM(_integrity_aes_key).encrypt(_integrity_iv, _integrity_plain, None)
    _integrity_key_b64 = _b64(_integrity_aes_key)
    _integrity_iv_b64  = _b64(_integrity_iv)
    _integrity_ct_b64  = _b64(_integrity_ct)
    # Sözlük RC4 tablosuna string olarak eklemek yerine — doğrudan JS literali
    # (loader'ın kendi RC4 pipeline'ından geçirirsek rotation uyumsuzluğu riski var)

    # ── RC4 string table
    # all_strings sırası sabit — indeksler buna göre hesaplanır
    # pool string'leri 0..pool_size-1 konumunda
    # diğer string'ler pool_size'dan sonra
    extra_strings = [
        base_path,           # pool_size + 0
        "script",            # pool_size + 1
        "loading",           # pool_size + 2
        "DOMContentLoaded",  # pool_size + 3
        "src",               # pool_size + 4
        "onload",            # pool_size + 5
        "onerror",           # pool_size + 6
        "appendChild",       # pool_size + 7
        "createElement",     # pool_size + 8
        "readyState",        # pool_size + 9
        "addEventListener",  # pool_size + 10
        ev_key,              # pool_size + 11  ← CustomEvent adı — build başına random
        "?v=",               # pool_size + 12
        "head",              # pool_size + 13
        "/api/security/env-signal",  # pool_size + 14  ← RC4 tablosunda, fromCharCode değil
    ]
    all_strings = pool + extra_strings

    # Build başına varyant seç — loader'ın encoder/decoder'ı eşleşsin
    ldr_cipher = _pick_cipher_variant()
    _, ldr_encrypt_fn, _ = ldr_cipher

    # Her string için bağımsız rastgele anahtar — keystream reuse'u önler
    per_str_keys = [secrets.token_bytes(16) for _ in all_strings]
    enc_strs     = [
        base64.b64encode(ldr_encrypt_fn(s.encode("utf-8"), k)).decode()
        for s, k in zip(all_strings, per_str_keys)
    ]
    # JS'de her indeksin kendi key byte array'i: [[b0,b1,...], [b0,b1,...], ...]
    keys_js_arr = "[" + ",".join(
        "[" + ",".join(str(b) for b in k) + "]"
        for k in per_str_keys
    ) + "]"

    # Çift rotation: r1+r2 toplamı effective offset, ama JS'e iki ayrı değer gömülür
    # Saldırgan tek bir offset yerine iki bağımsız değeri tahmin etmek zorunda (n² arama uzayı)
    n_str = len(all_strings)
    _r1   = R.randint(2, n_str // 2)
    _r2   = R.randint(2, n_str // 2)
    if _r2 == _r1:
        _r2 = (_r1 % (n_str // 2 - 1)) + 2
    rot_offset = (_r1 + _r2) % n_str
    if rot_offset < 3:          # minimum mesafe garantisi
        rot_offset = 3
        _r1, _r2 = 1, 2         # fallback: toplam=3

    def R_idx(orig: int) -> int:
        """Rotation sonrası runtime indeksi."""
        return (orig - rot_offset) % n_str

    # String indeksleri (rotation'lı)
    IDX = {
        "base":       R_idx(pool_size + 0),
        "script":     R_idx(pool_size + 1),
        "loading":    R_idx(pool_size + 2),
        "domcl":      R_idx(pool_size + 3),
        "src":        R_idx(pool_size + 4),
        "onload":     R_idx(pool_size + 5),
        "onerror":    R_idx(pool_size + 6),
        "append":     R_idx(pool_size + 7),
        "create":     R_idx(pool_size + 8),
        "ready":      R_idx(pool_size + 9),
        "addlisten":  R_idx(pool_size + 10),
        "ev_ready":   R_idx(pool_size + 11),  # "__wasd_core_ready" CustomEvent adı
        "qv":         R_idx(pool_size + 12),
        "head":       R_idx(pool_size + 13),
        "env_signal": R_idx(pool_size + 14),  # "/api/security/env-signal" — RC4'ten çözülür
    }
    # pool item'larının rotation'lı indeksleri
    pool_ridx = [R_idx(i) for i in range(pool_size)]

    # ── 3-katman gizleme — gerçek pool indekslerini gizle ─────────────────────
    # Encode sırası her build'de randomize edilir (6 permütasyon).
    # Python encode ve JS decode dinamik olarak üretilir — sabit sıra imzası yok.
    import itertools as _itools

    def rol8(v: int, bits: int = 3) -> int: return ((v << bits) | (v >> (8 - bits))) & 0xFF
    def ror8(v: int, bits: int = 3) -> int: return ((v >> bits) | (v << (8 - bits))) & 0xFF

    k1 = [R.randint(1, 0xFE) for _ in range(n_real)]
    k3 = [R.randint(1, 120)  for _ in range(n_real)]
    shift_bits = R.randint(1, 7)  # ROL shift miktarı da build başına değişir

    # Her ops için Python encode fonksiyonu ve JS decode ifadesi
    OPS = {
        'XOR1': {
            'py_enc': lambda v, i: v ^ k1[i],
            'py_dec': lambda v, i: v ^ k1[i],
            'js_dec': lambda a, i: f"({a}^{V['K1']}[{i}])",
        },
        'ROL': {
            'py_enc': lambda v, i: rol8(v, shift_bits),
            'py_dec': lambda v, i: ror8(v, shift_bits),
            'js_dec': lambda a, i: f"(({a}>>{shift_bits})|({a}<<{8-shift_bits}))&0xFF",
        },
        'ADD': {
            'py_enc': lambda v, i: (v + k3[i]) % 256,
            'py_dec': lambda v, i: (v - k3[i] + 256) % 256,
            'js_dec': lambda a, i: f"({a}-{V['K3']}[{i}]+256)%256",
        },
    }
    # 3 katman: XOR1, ROL, ADD — sıraları randomize
    layer_names = ['XOR1', 'ROL', 'ADD']
    perm = list(_itools.permutations(layer_names))
    chosen_order = list(R.choice(perm))  # encode sırası

    # Python encode: seçilen sırayla uygula
    def encode_index(ri, i):
        v = ri
        for op in chosen_order:
            v = OPS[op]['py_enc'](v, i)
        return v & 0xFF

    l3 = [encode_index(real_indices[i], i) for i in range(n_real)]

    # JS decode: encode'un tam tersi sırada
    decode_order = list(reversed(chosen_order))

    def build_js_decode_expr(i_var: str, li_var: str) -> str:
        """l3[i] değerinden gerçek pool index'ini çözen JS ifadesi üretir."""
        expr = f"{li_var}[{i_var}]"
        for op in decode_order:
            expr = OPS[op]['js_dec'](expr, i_var)
        return expr

    # JS string literals
    arr_lit      = "[" + ",".join(f'"{e}"' for e in enc_strs) + "]"
    k1_js        = "[" + ",".join(f"0x{v:02x}" for v in k1)  + "]"
    k3_js        = "[" + ",".join(f"0x{v:02x}" for v in k3)  + "]"
    l3_js        = "[" + ",".join(f"0x{v:02x}" for v in l3)  + "]"
    pool_ridx_js = "[" + ",".join(str(v)       for v in pool_ridx) + "]"
    decoy_js     = "[" + ",".join(str(i)        for i in decoy_indices) + "]"

    # ── Değişken isimleri
    V = {k: _rn(k) for k in [
        "A",       # string array
        "D",       # dec(idx) fonksiyonu
        "C",       # cache
        "K",       # rc4 key array
        "PR",      # pool rotation-index array
        "K1","K3","L3",
        "RI",      # real pool indices (decoded)
        "DI",      # decoy pool indices
        "BASE","TS",
        "iR",      # real loader counter (artık kullanılmıyor, geriye dönük uyum için tutuldu)
        "iD",      # decoy loop counter (kaldırılacak — Q ile birleşti)
        "RES",     # promise resolve
        "REJ",     # promise reject
        "TO",      # reject timeout handle
        "LN",      # loadNext (tek queue)
        "LD",      # loadDecoys (kaldırılacak — LN ile birleşti)
        "INIT",
        "sc",      # script element
        "sx",      # script element (loadDecoys) — kaldırılacak
        "ST",      # state
        "Q",       # unified queue (RI + DI shuffled)
        "qi",      # queue index
        "FL",      # sdFlag
        "SC",      # sdCnt
        "SF",      # sdFn
        "SI",      # sdInterval
    ]}

    # ── Self-defending parametreleri
    sd_thr  = R.randint(75, 120)
    sd_int  = R.randint(500, 900)
    sd_lim  = R.randint(3, 6)
    sd_del  = R.randint(30, 55)

    # Loader inline decoder'ı: _rc4_js_decoder üzerinden varyant-aware üret
    # Poison bloğu loader'ın kendi değişkenleriyle — per-index mask
    # env-signal URL'i artık RC4 tablosundan çözülür (fromCharCode değil)
    env_code_loader, v_poison_loader = build_env_check(
        dec_fn=V['D'], url_idx=IDX['env_signal']
    )
    ldr_v_rk   = _rn("_lrk")
    ldr_idx      = _rn("_ln")   # gerçek JS idx parametre adı — string literal geçmek undefined üretir
    ldr_v_mask   = _rn("_lpm")
    # Per-index mask sabitleri — loader'a özgü, _rc4_js_decoder'dan bağımsız
    ldr_pm_seed  = R.randint(0x10, 0xFE)
    ldr_pm_prime = R.choice([3, 5, 7, 11, 13, 17, 19, 23])
    ldr_pm_salt  = R.randint(0x01, 0x7F)
    ldr_poison_block = (
        f"if({v_poison_loader}){{"
        f"var {ldr_v_mask}=({_num_expr(ldr_pm_seed)}^(({ldr_idx}*{_num_expr(ldr_pm_prime)}+{_num_expr(ldr_pm_salt)})&0xFF))&0xFF;"
        f"for(var _pi=0;_pi<{ldr_v_rk}.length;_pi++){{{ldr_v_rk}[_pi]=({ldr_v_rk}[_pi]^{ldr_v_mask})&0xFF;}}"
        f"}}"
    )

    # ── AES-GCM seed: 3. XOR katmanı — crypto.subtle native API ─────────────
    # Seed, loader başlatılırken async olarak crypto.subtle.decrypt ile çözülür.
    # **FAIL-CLOSED:** Seed Promise — 5s timeout, başarısız olursa loader init reject.
    aes_unlock_js, aes_seed_global, aes_seed_ready_promise = build_aes_gcm_seed()

    v_aes_seed = _rn("_as")   # seed XOR için temp değişken
    aes_seed_block = (
        # ── FAIL-CLOSED: Seed Promise garantisi ─────────────────────────────
        # Loader init aşağıda seed ready Promise'i await eder — timeout içinde
        # seed gelmezse loader init reject olur, script injection hiç başlamaz.
        # Bu noktada seed GUARANTEED — window[aes_seed_global] mutlaka dolu.
        # Eski fail-open: "seed yoksa key değişmez" → REMOVED
        # Yeni fail-closed: seed yoksa loader init reject → decoder hiç çalışmaz
        f"var {v_aes_seed}=window['{aes_seed_global}']||'';"  # seed MUST exist
        f"if({v_aes_seed}&&{v_aes_seed}.length){{"
        f"var _amap=Function.prototype.call.bind(Array.prototype.map);"
        f"{ldr_v_rk}=_amap({ldr_v_rk},function(_b,_i){{"
        f"return ({_num_expr(0xFF)}&(_b^{v_aes_seed}.charCodeAt(_i%{v_aes_seed}.length)));"
        f"}});"
        f"}}else{{"
        # Seed empty → poison — bu durum Promise reject'te zaten yakalanır
        f"{v_poison_loader}=true;"
        f"}}"
    )
    ldr_poison_block = ldr_poison_block + aes_seed_block

    # js_decoder_fn(arr, key_arr, dec, cache, kname, poison_block, v_k, idx, ephemeral_wrapper) -> str
    _, _, ldr_js_decoder_fn = ldr_cipher
    # Loader için ephemeral_wrapper = None (persistent cache - performans için)
    ldr_decoder_body = ldr_js_decoder_fn(
        V['A'], keys_js_arr, V['D'], V['C'], V['K'],
        ldr_poison_block, ldr_v_rk, ldr_idx, None  # ← ephemeral_wrapper=None
    )

    # ── Dead code ve opaque predicates
    dead1   = build_dead_code(R.randint(8, 14))
    dead2   = build_dead_code(R.randint(8, 14))
    dead3   = build_dead_code(R.randint(6, 10))
    dead4   = build_dead_code(R.randint(6, 10))
    op1     = build_opaque_predicates(R.randint(5, 9))
    op2     = build_opaque_predicates(R.randint(4, 7))
    op3     = build_opaque_predicates(R.randint(3, 6))

    # ── Integrity chain: AES-GCM ile şifreli beklenen hash'ler ──────────────
    # Loader'da async IIFE: crypto.subtle.decrypt → JSON.parse → global'e yaz
    # _op2 onload: SubtleCrypto.digest ile yüklenen dosyayı hash'le, karşılaştır
    _ichk_global = "__wasd_ic_" + _rhex(4)   # integrity map global adı
    _v_ik  = _rn("_ik"); _v_ii = _rn("_ii"); _v_ic = _rn("_ic")
    _v_ikp = _rn("_ikp"); _v_ipt = _rn("_ipt"); _v_ie = _rn("_ie")
    _integrity_unlock_js = (
        f"(function(){{"
        f"try{{"
        f"if(typeof crypto==='undefined'||!crypto.subtle)return;"
        # atob sonuçlarını validate et — boş/undefined ise abort
        f"var _ikr='{_integrity_key_b64}',_iir='{_integrity_iv_b64}',_icr='{_integrity_ct_b64}';"
        f"if(!_ikr||!_iir||!_icr)return;"
        f"var {_v_ik}=Uint8Array.from(atob(_ikr),function(c){{return c.charCodeAt(0);}});"
        f"var {_v_ii}=Uint8Array.from(atob(_iir),function(c){{return c.charCodeAt(0);}});"
        f"var {_v_ic}=Uint8Array.from(atob(_icr),function(c){{return c.charCodeAt(0);}});"
        f"if({_v_ik}.length===0||{_v_ii}.length===0||{_v_ic}.length===0)return;"
        f"var _then=Function.prototype.call.bind(Promise.prototype['then']);"
        f"var _ctch=Function.prototype.call.bind(Promise.prototype['catch']);"
        f"var _prom=crypto.subtle['importKey']('raw',{_v_ik},{{'name':'AES-GCM'}},false,['decrypt']);"
        f"_ctch(_then(_then(_prom,function({_v_ikp}){{"
        f"return crypto.subtle['decrypt']({{'name':'AES-GCM','iv':{_v_ii}}},{_v_ikp},{_v_ic});"
        f"}}),function({_v_ipt}){{"
        f"try{{"
        f"var _dec=new TextDecoder();"
        f"var _str=_dec.decode(new Uint8Array({_v_ipt}));"
        f"if(typeof window!=='undefined')window['{_ichk_global}']=JSON.parse(_str);"
        f"}}catch(_pe){{}}"
        f"}}),function({_v_ie}){{}});"
        f"}}catch({_v_ie}){{}}"
        f"}})();"
    )

    # _op2 async onload — integrity check değişken adları
    _v_pp  = _rn("_pp")   # pool_pos
    _v_em  = _rn("_em")   # expected map
    _v_eh  = _rn("_eh")   # expected hash
    _v_rs  = _rn("_rs")   # fetch response
    _v_ab  = _rn("_ab")   # arrayBuffer
    _v_dg  = _rn("_dg")   # digest
    _v_hx  = _rn("_hx")   # hex string
    _v_hb  = _rn("_hb")   # hex byte temp

    # ── AES-GCM seed: build-time şifrele, loader init'te crypto.subtle ile çöz ──
    # aes_unlock_js ve aes_seed_global, ldr_poison_block'ta önceden üretildi.
    # ham_js'e unlock IIFE'sini ekle — async decrypt başlar, seed global'e yazılır.

    ham_js = f""";(function(w,d){{
'use strict';
{dead1}
{op1}

/* ---- AES-GCM seed unlock (crypto.subtle native) ---- */
{aes_unlock_js}

/* ---- Integrity chain: per-part SHA-256 expected hashes (AES-GCM encrypted) ---- */
{_integrity_unlock_js}

/* ---- bot/headless env check (silent poison) ---- */
{env_code_loader}

/* ---- RC4 encrypted string table ---- */
var {V['A']}={arr_lit};
var {V['K']}={keys_js_arr};
(function(){{
  var _n={_r1};
  while(_n-->0){{{V['A']}.push({V['A']}.shift()); {V['K']}.push({V['K']}.shift());}}
  var _n2={_r2};
  while(_n2-->0){{{V['A']}.push({V['A']}.shift()); {V['K']}.push({V['K']}.shift());}}
}})();
{ldr_decoder_body}

{dead2}
{op2}

/* ---- 3-layer decode: real pool positions (sira build basina randomize) ---- */
var {V['K1']}={k1_js};
var {V['K3']}={k3_js};
var {V['L3']}={l3_js};
var {V['PR']}={pool_ridx_js};
/* real_i[j] = pool position of j-th real part (0-indexed in pool) */
var {V['RI']}=(function(){{
  var _r=[];
  for(var _i=0;_i<{V['L3']}.length;_i++){{
    _r.push({build_js_decode_expr('_i', V['L3'])});
  }}
  return _r;
}})();
var {V['DI']}={decoy_js};
var {V['BASE']}={V['D']}({IDX['base']});
var {V['TS']}=0x{ts:x};

{op3}
{dead3}

/* ---- self-defending ---- */
var {V['FL']}=false,{V['SC']}=0,{V['SI']};
(function(){{
  function {V['SF']}(){{
    var _t0=performance.now();
    debugger;
    if(performance.now()-_t0>{sd_thr}){{
      {V['FL']}=true;
      (function _k(){{debugger;setTimeout(_k,{sd_del});}})();
      return;
    }}
    if(++{V['SC']}<{sd_lim})setTimeout({V['SF']},{R.randint(400,800)});
  }}
  try{{{V['SF']}();}}catch(_e){{}}
  {V['SI']}=setInterval(function(){{
    try{{{V['SF']}();}}catch(_e){{}}
    if({V['FL']})clearInterval({V['SI']});
  }},{sd_int});
}})();

/* ---- loader state ---- */
/* Q = RI + DI shuffled — gercek + decoy tek queue'da, sira belirsiz */
var {V['RES']},{V['REJ']},{V['TO']};
/* Promise: part4 dispatch ettigi __wasd_core_ready event'i resolve eder — global property yok */
w[{V['D']}({IDX['ev_ready']})]=new Promise(function(_rs,_rj){{{V['RES']}=_rs;{V['REJ']}=_rj;}});
{V['TO']}=setTimeout(function(){{{V['REJ']}(new Error('shield_timeout'));}},8000);
d.addEventListener({V['D']}({IDX['ev_ready']}),function(_ev){{
  clearTimeout({V['TO']});
  if(_ev&&_ev.detail&&_ev.detail.core){{{V['RES']}(_ev.detail.core);}}
  else{{{V['REJ']}(new Error('shield_load_failed'));}}
}},{{once:true}});
var {V['Q']}=(function(){{
  var _q={V['RI']}.concat({V['DI']});
  for(var _i=_q.length-1;_i>0;_i--){{
    var _j=Math.floor(Math.random()*(_i+1));
    var _t=_q[_i];_q[_i]=_q[_j];_q[_j]=_t;
  }}
  return _q;
}})();
var {V['qi']}=0;

/* ---- loadNext: mini-VM Map dispatch (switch → Map<opcode,handler>) ---- */
/* deobfuscator araçlar switch/case AST imzasını tanır; Map dispatch opcode   */
/* semantiğini bilemez — her build'de opcode'lar farklı random değer alır.   */
var {V['sc']};
(function(){{
  /* Build-time random opcode'lar — sabit 0/1/2/3/4 yerine */
  var _op0={_num_expr(R.randint(0x10,0x3F))};  /* init    */
  var _op1={_num_expr(R.randint(0x40,0x7F))};  /* create  */
  var _op2={_num_expr(R.randint(0x80,0xAF))};  /* bind    */
  var _op3={_num_expr(R.randint(0xB0,0xDF))};  /* append  */
  var _op4={_num_expr(R.randint(0xE0,0xFF))};  /* done    */
  /* bytecode: opcode dizisi — program counter style */
  var _bc=[_op0,_op1,_op2,_op3];              /* normal path  */
  var _bce=[_op0,_op4];                        /* exhausted path */
  /* Handler Map — opcode → handler fonksiyonu */
  var _ctx={{}};
  var _vm=new Map();
  _vm.set(_op0,function(){{
    {dead4}
    _ctx._bc=({V['qi']}>={V['Q']}.length)?_bce:_bc;
    _ctx._pc=1;
  }});
  _vm.set(_op1,function(){{
    {V['sc']}=d[{V['D']}({IDX['create']})]({V['D']}({IDX['script']}));
    {V['sc']}[{V['D']}({IDX['src']})]={V['BASE']}+{V['D']}({V['PR']}[{V['Q']}[{V['qi']}]])+{V['D']}({IDX['qv']})+{V['TS']}.toString(16);
    _ctx._pc++;
  }});
  _vm.set(_op2,function(){{
    {V['sc']}[{V['D']}({IDX['onerror']})]=function(){{{V['qi']}++;window['{V['LN']}']&&window['{V['LN']}']();}};
    {V['sc']}[{V['D']}({IDX['onload']})]=async function(){{
      var {_v_pp}={V['Q']}[{V['qi']}];
      var {_v_em}=typeof window!=='undefined'?window['{_ichk_global}']:null;
      var {_v_eh}={_v_em}&&{_v_em}[{_v_pp}]?{_v_em}[{_v_pp}]:null;
      if({_v_eh}&&typeof crypto!=='undefined'&&crypto.subtle){{
        try{{
          var {_v_rs}=await fetch({V['sc']}.src,{{cache:'force-cache',credentials:'same-origin'}});
          var {_v_ab}=await {_v_rs}.arrayBuffer();
          var {_v_dg}=await crypto.subtle['digest']('SHA-256',{_v_ab});
          var {_v_hx}=Array.prototype.map.call(new Uint8Array({_v_dg}),function({_v_hb}){{
            return ({_v_hb}<16?'0':'')+{_v_hb}.toString(16);
          }}).join('');
          if({_v_hx}!=={_v_eh}){{{v_poison_loader}=true;}}
        }}catch(_ce){{}}
      }}
      {V['qi']}++;
      window['{V['LN']}']&&window['{V['LN']}']();
    }};
    {V['sc']}.async=false;
    {V['sc']}.defer=false;
    _ctx._pc++;
  }});
  _vm.set(_op3,function(){{
    d[{V['D']}({IDX['head']})][{V['D']}({IDX['append']})]({V['sc']});
    _ctx._running=false;
  }});
  _vm.set(_op4,function(){{
    _ctx._running=false;
  }});
  function {V['LN']}(){{
    try{{
      _ctx._bc=_bc;
      _ctx._pc=0;
      _ctx._running=true;
      while(_ctx._running&&_ctx._pc<_ctx._bc.length){{
        var _oc=_ctx._bc[_ctx._pc];
        var _h=_vm.get(_oc);
        if(_h){{_h();if(_ctx._running)_ctx._pc++;}}
        else{{_ctx._running=false;}}
      }}
    }}catch(_lex){{
      clearTimeout({V['TO']});
      {V['REJ']}(_lex);
    }}
  }}
  window['{V['LN']}']={V['LN']};
}})();

/* ---- Async Init: Seed Ready Promise await (FAIL-CLOSED) ---- */
/* Seed Promise 5s timeout ile resolve/reject olur.
   Seed gelene kadar loadNext() çağrılmaz — fail-closed.
   Eski fail-open: seed yoksa key bozuk ama decoder çalışır → REMOVED
   Yeni fail-closed: seed yoksa loader init reject → hiçbir script yüklenmez */
function {V['INIT']}(){{
  var _srp=window['{aes_seed_ready_promise}'];
  if(!_srp){{
    {V['REJ']}(new Error('seed_promise_missing'));
    return;
  }}
  var _then=Function.prototype.call.bind(Promise.prototype['then']);
  var _ctch=Function.prototype.call.bind(Promise.prototype['catch']);
  _ctch(_then(_srp,function(_seed){{
    /* Seed resolved — decoder key XOR uygulanmış, script injection başlat */
    window['{V['LN']}']&&window['{V['LN']}']();
  }}),function(_err){{
    /* Seed failed — loader init reject, hiçbir script yüklenmez (FAIL-CLOSED) */
    clearTimeout({V['TO']});
    {V['REJ']}(_err);
  }});
}}

if(d[{V['D']}({IDX['ready']})]!=={V['D']}({IDX['loading']})){{
  {V['INIT']}();
}}else{{
  d[{V['D']}({IDX['addlisten']})]({V['D']}({IDX['domcl']}),{V['INIT']});
}}

}})(window,document);
"""

    # ── A-Pre + B-npm + A-Post pipeline
    pre, nonce_cap_global, session_cap_global = inject_pre(ham_js, "wasd-loader.js")

    os.makedirs(DIST_DIR, exist_ok=True)
    _uid    = uuid.uuid4().hex[:8]
    tmp_in  = os.path.join(DIST_DIR, f"_ldrtmp_{_uid}_in.js")
    tmp_out = os.path.join(DIST_DIR, f"_ldrtmp_{_uid}_out.js")
    tmp_cfg = CONFIG_PATH + f".ldr.{_uid}.tmp.json"

    with open(tmp_in, "w", encoding="utf-8") as f:
        f.write(pre)

    cfg = dict(_load_selected_profile())
    cfg["deadCodeInjectionThreshold"] = 0.05
    with open(tmp_cfg, "w", encoding="utf-8") as f:
        json.dump(cfg, f)

    b_ok = run_npm(tmp_in, tmp_out, "wasd-loader.js")
    npm_out = open(tmp_out, encoding="utf-8").read() if (b_ok and os.path.exists(tmp_out)) else pre

    for _f in [tmp_in, tmp_out, tmp_cfg]:
        try: os.remove(_f)
        except: pass

    bid    = _rhex(6)
    header = f"/* WASDW|{bid}|{ts} */\n"
    footer = build_dead_code(R.randint(4, 7)) + build_opaque_predicates(R.randint(3, 5)) + build_multi_channel_devtools_detect() + build_self_defending_loop() + build_worker_timing_guard()
    final  = header + npm_out + "\n" + footer

    # Loader'a WASD-core-V{hex}.js ismi ver (diğer parçalarla aynı format)
    loader_name = f"WASD-core-V{_random_wasd_suffix()}.js"
    loader_path = os.path.join(DIST_DIR, loader_name)
    with open(loader_path, "w", encoding="utf-8") as f:
        f.write(final)

    lines = final.count('\n')
    print(f"  [+] Loader yazildi : {loader_path}  ({len(final):,} B / ~{lines} satir)")
    print(f"  [+] Pool boyutu    : {pool_size} ({n_real} gercek + {n_decoy} decoy)")
    print(f"  [+] Gercek pool pos: {real_indices}")

    # ── SRI (Subresource Integrity): Loader SHA-384 hash ──────────────────────
    # Browser native integrity check — HTML template'de integrity="sha384-..." attribute
    # Loader'ın kendi bütünlüğünü korur (parts'ların integrity'si loader içinde check ediliyor)
    loader_sha384 = hashlib.sha384(final.encode("utf-8")).digest()
    loader_integrity = "sha384-" + base64.b64encode(loader_sha384).decode("ascii")
    print(f"  [+] Loader SRI     : {loader_integrity[:32]}...")

    # ── WASD-Loader-V sahte loader dosyası ───────────────────────────────────
    # Her build'de _build_decoy_js() ile yeni işlevsiz stub üretilir.
    # İstisna: dist'te >50KB kullanıcı kodu varsa korunur (elle eklenmiş).
    fake_loader_name = None
    if os.path.isfile(MANIFEST_PATH):
        try:
            with open(MANIFEST_PATH, encoding="utf-8") as _f:
                _m = json.load(_f)
            old_fake = _m.get("fake_loader", "")
            if old_fake and os.path.isfile(os.path.join(DIST_DIR, old_fake)):
                old_size = os.path.getsize(os.path.join(DIST_DIR, old_fake))
                if old_size < 50_000:
                    os.remove(os.path.join(DIST_DIR, old_fake))
                else:
                    fake_loader_name = old_fake   # kullanıcı kodu var, koru
        except Exception:
            pass

    if fake_loader_name is None:
        fake_loader_name = f"WASD-Loader-V{_random_wasd_suffix()}.js"
        fake_loader_path = os.path.join(DIST_DIR, fake_loader_name)
        print(f"  [+] Fake loader uretiliyor...")
        fake_loader_content, _fl_hb = _build_decoy_js()
        with open(fake_loader_path, "w", encoding="utf-8") as f:
            f.write(fake_loader_content)
        print(f"  [+] Fake loader yazildi: {fake_loader_name} ({len(fake_loader_content):,} B)")
    else:
        print(f"  [+] Fake loader korundu (kullanici kodu var): {fake_loader_name}")
        _fl_hb = ""

    # Random decoy stub'ları
    _honeypot_paths_this_build: list[str] = [_fl_hb] if _fl_hb else []
    _rand_decoys = random_decoy_names if random_decoy_names is not None else decoy_names
    _stub_count = 0
    for dn in _rand_decoys:
        dp = os.path.join(DIST_DIR, dn)
        if not os.path.exists(dp):
            print(f"     ├─ Decoy üretiliyor: {dn}")
            decoy_content, _d_hb = _build_decoy_js()
            if _d_hb:
                _honeypot_paths_this_build.append(_d_hb)
            with open(dp, "w", encoding="utf-8") as f:
                f.write(decoy_content)
            print(f"     └─ Boyut: {len(decoy_content):,} B")
            _stub_count += 1
    print(f"  [+] Decoy stub'lar dist/'e yazildi ({len(decoy_names)} toplam, {_stub_count} yeni stub)")
    # Honeypot path'leri manifest'e yaz — app.py route'larını dinamik yükler
    if _honeypot_paths_this_build:
        print(f"  [+] Honeypot path'leri ({len(_honeypot_paths_this_build)}): {_honeypot_paths_this_build[:3]}...")

    return loader_name, fake_loader_name, sc_key, scr_key, _honeypot_paths_this_build, nonce_cap_global, session_cap_global, loader_integrity


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inplace",   action="store_true",
                    help="dist/ yerine kaynak dosyaların üzerine yaz")
    ap.add_argument("--file",      default=None,
                    help="Tek dosya işle (TARGET_FILES içinden)")
    ap.add_argument("--domain",    default=None,
                    help="domainLock ayarla")
    ap.add_argument("--split",     action="store_true",
                    help="WASD-core'u 4 parçaya böl ve obfuscate et")
    ap.add_argument("--split-dry", action="store_true",
                    help="--split gibi ama obfuscation yapmadan (test için)")
    args = ap.parse_args()

    if args.domain:
        with open(CONFIG_PATH, encoding="utf-8") as f: cfg = json.load(f)
        cfg["domainLock"] = [args.domain]
        with open(CONFIG_PATH, "w", encoding="utf-8") as f: json.dump(cfg, f, indent=2)
        print(f"[+] domainLock -> {args.domain}")

    # ── SPLIT MODE ──────────────────────────────────────────────
    if args.split or args.split_dry:
        result = process_split_parts(dry_run=args.split_dry)
        return 0 if result else 1

    # ── NORMAL MODE ─────────────────────────────────────────────
    targets = [args.file] if args.file else TARGET_FILES
    print("=" * 56)
    print("  WASDW JS Obfuscation Pipeline  A+B  v3")
    print(f"  Hedef : {', '.join(targets)}")
    print(f"  Mod   : {'inplace' if args.inplace else 'dist/'}")
    print("=" * 56)

    ok = fail = 0
    for fn in targets:
        src = os.path.join(JS_DIR, fn)
        if not os.path.exists(src):
            print(f"\n  [x] Bulunamadi: {src}"); fail += 1; continue

        # challenge-wall.js ve WASD-core.js için random suffix'li çıktı adı
        if fn in ("WASD-core.js", "challenge-wall.js") and not args.inplace:
            out_name = f"WASD-core-V{_random_wasd_suffix()}.js"
            # Eski çıktıyı manifest'ten al ve sil
            _old_m = {}
            if os.path.isfile(MANIFEST_PATH):
                try:
                    with open(MANIFEST_PATH, encoding="utf-8") as _f:
                        _old_m = json.load(_f)
                except: pass
            _manifest_key = "wasd_core" if fn == "WASD-core.js" else "challenge_wall"
            _old_out = _old_m.get(_manifest_key, "")
            if _old_out and _old_out != "challenge-wall.js":
                _op = os.path.join(DIST_DIR, _old_out)
                if os.path.isfile(_op):
                    try: os.remove(_op)
                    except: pass
            # Eski sabit challenge-wall.js varsa sil
            if fn == "challenge-wall.js":
                _old_fixed = os.path.join(DIST_DIR, "challenge-wall.js")
                if os.path.isfile(_old_fixed):
                    try: os.remove(_old_fixed)
                    except: pass
            dst = os.path.join(DIST_DIR, out_name)
        else:
            dst = src if args.inplace else os.path.join(DIST_DIR, fn)
            out_name = fn

        try:
            process_file(src, dst)
            ok += 1
            # Manifest'e yaz
            if fn in ("WASD-core.js", "challenge-wall.js") and not args.inplace:
                os.makedirs(DIST_DIR, exist_ok=True)
                _m = {}
                if os.path.isfile(MANIFEST_PATH):
                    try:
                        with open(MANIFEST_PATH, encoding="utf-8") as _f:
                            _m = json.load(_f)
                    except: pass
                _key = "wasd_core" if fn == "WASD-core.js" else "challenge_wall"
                _m[_key] = out_name
                _m["ts"] = int(time.time())
                with open(MANIFEST_PATH, "w", encoding="utf-8") as _f:
                    json.dump(_m, _f, indent=2)
                print(f"  [+] Manifest guncellendi: {_key} = {out_name}")
        except Exception as e:
            import traceback; traceback.print_exc()
            print(f"\n  [x] {fn} hata: {e}"); fail += 1

    print("\n" + "=" * 56)
    print(f"  {ok} basarili  |  {fail} basarisiz")
    if not args.inplace: print(f"  Cikti: {DIST_DIR}")
    print("=" * 56)
    return 0 if fail == 0 else 1

if __name__ == "__main__":
    sys.exit(main())
