# Ephemeral Cache (Geçici Önbellek) Sistemi

## Problem: "Breakpoint Sonrası Decrypt"

Client-side'da **matematiksel olarak çözülemez** bir problem:

```javascript
function decode(idx) {
  // ... RC4/AES/XOR katmanları ...
  return plaintext;  // ← BREAKPOINT buraya konulur
}

// DevTools Console:
// > decode(5)
// "kritik_url_path"  ← Düz metin elde edildi
```

XOR, AES, nonce, capability pattern — hiçbiri bu noktadan sonra koruma sağlayamaz. Decoder sonunda düz metni **mutlaka** RAM'e yazmalı (Kerckhoffs prensibi, client-side).

## Çözüm YOK — Azaltma VAR

**Strateji:** Decode edilen değerin yaşam süresini minimize et.

```
┌────────────────────────────────────────────────────┐
│ Önceki Sistem (Persistent Cache)                  │
├────────────────────────────────────────────────────┤
│ decode(5) → "url" → cache[5] = "url"              │
│                     └─ SONSUZ saklanır             │
│                     └─ Her breakpoint'te görülür   │
│                     └─ Memory dump'ta daima var    │
└────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────┐
│ Yeni Sistem (Ephemeral Cache + TTL)               │
├────────────────────────────────────────────────────┤
│ decode(5) → "url" → cache[5] = "url"              │
│                     cache._ttl[5] = Date.now()     │
│                                                    │
│ [2-5 saniye sonra]                                 │
│ decode(X) → TTL check → cache[5] silindi          │
│                                                    │
│ Breakpoint'te yakalanan değer:                     │
│   - Sadece o an geçerli                           │
│   - TTL sonrası kaybolur                          │
│   - Memory dump'ta geçici                         │
└────────────────────────────────────────────────────┘
```

## Implementasyon

### 1. Ephemeral Mode Flag

```python
# obfuscate_js.py
def build_string_array(strings, ephemeral_mode=False):
    # ephemeral_mode=True → Cache TTL aktif
```

### 2. Cache TTL Logic (JS)

```javascript
// Her decoder grubu için:
var cache = {};
cache._ttl = {};  // TTL timestamp storage

function decode(idx) {
  // ── TTL Check ────────────────────────────────
  if (!cache._ttl) cache._ttl = {};
  var now = Date.now();
  
  if (cache[idx] !== undefined && cache._ttl[idx]) {
    var age = now - cache._ttl[idx];
    if (age > TTL_MS) {  // TTL_MS: 2000-5000ms (build-time random)
      delete cache[idx];
      delete cache._ttl[idx];
    } else {
      return cache[idx];  // TTL içinde, cache geçerli
    }
  }
  
  // ── Decode Process ──────────────────────────
  // ... RC4/cipher/nonce layers ...
  var plaintext = /* decoded value */;
  
  // ── Cache + TTL Set ─────────────────────────
  cache._ttl[idx] = now;  // TTL kaydı
  return cache[idx] = plaintext;
}
```

### 3. Build-Time Randomization

- **TTL değeri:** 2000-5000ms arası rastgele (her build farklı)
- **TTL değeri obfuscation:** `_num_expr()` ile (XOR/SUB/ADD forms)
- **Değişken adları:** `_rn()` ile (her build farklı)

## Güvenlik Kazanımları

| Özellik | Persistent Cache | Ephemeral Cache (TTL) |
|---------|------------------|-----------------------|
| **Breakpoint'te görülme** | ✗ Her zaman | ✓ Sadece TTL içinde |
| **Memory dump risk** | ✗ Kalıcı | ✓ Geçici (2-5 saniye) |
| **Uzun vadeli analiz** | ✗ Kolay | ✓ Zorlaştırılmış |
| **Cache scraping** | ✗ Tüm cache okunabilir | ✓ TTL expire ile kaybolur |

## Kullanım

### Ephemeral Mode Aktif (Kritik Stringler)

```python
# inject_pre() fonksiyonunda:
code, dec_fn = build_string_array(
    critical_strings,
    ephemeral_mode=True  # ← Cache TTL aktif
)
```

Şu anda **kritik string decoder'ları** için aktif:
- URL path'leri
- API endpoint'leri
- Capability global adları
- Nonce global adları

### Normal Mode (Genel Stringler)

```python
# Normal stringler için:
code, dec_fn = build_string_array(
    regular_strings,
    ephemeral_mode=False  # ← Persistent cache (default)
)
```

## Sınırlamalar ve Gerçekler

### ❌ Çözülemez:

1. **Decoder return breakpoint**
   - `return plaintext;` satırına breakpoint → değer okunur
   - Matematiksel olarak kaçınılmaz

2. **Memory watch**
   - DevTools memory watch → decode anında değer görülür
   - TTL başlamadan önce yakalanabilir

3. **Proxy/hook intercept**
   - `cache[idx] = plaintext` satırına Proxy → değer yakalanır
   - JS runtime seviyesinde kaçınılmaz

### ✓ Azaltılan:

1. **Persistent memory exposure**
   - Eski: Sonsuz cache → memory dump'ta kalıcı
   - Yeni: 2-5 saniye TTL → geçici exposure

2. **Automated scraping**
   - Eski: `Object.keys(cache)` → tüm decode'lar listele
   - Yeni: TTL expire → cache dinamik temizlenir

3. **Long-term analysis**
   - Eski: Tek breakpoint → tüm stringler elde edilir
   - Yeni: Her string için TTL penceresi içinde yakalamak gerekir

## TTL Değeri Seçimi

```python
ttl_ms = R.randint(2000, 5000)  # 2-5 saniye
```

**Neden 2-5 saniye?**

- **Çok kısa (<1 saniye):** Gerçek kullanım senaryolarında cache miss artışı
- **Çok uzun (>10 saniye):** Memory exposure süresi uzar, kazanç azalır
- **2-5 saniye:** Orta nokta — gerçek kullanım için yeterli, analiz için kısa

## Use-Once Pattern (Gelecek)

Daha aggressive bir varyant (şu anda implementasyonda değil):

```javascript
// Kritik string ilk decode sonrası HEMEN silinir
function decode_once(idx) {
  var val = decode(idx);
  delete cache[idx];      // ← İlk kullanımdan sonra kaybolur
  delete cache._ttl[idx];
  return val;
}
```

**Trade-off:**
- ✓ Daha güvenli (tek kullanımlık)
- ✗ Her çağrıda yeniden decode (performans maliyeti)

## Sonuç

Ephemeral cache **çözüm değil, azaltma**dır.

- Client-side'da düz metin **mutlaka** RAM'e gelir
- TTL ile exposure süresini minimize ediyoruz
- Asıl korumanın **#9 (client→server verification flow)** ile sağlanması gerekir

**Analojisi:**  
"Evdeki kasayı kırılmaz yapamazsın, ama 5 dakikada kendini yok eden mesaj gibi yapabilirsin."

---

**Not:** Bu sistem sadece **kritik stringler** için aktif. Normal stringler persistent cache kullanır (performans için).
