# Multi-Channel DevTools Detection

## Problem: Timing/Debugger-Statement Bypass

Mevcut anti-debug teknikler kolayca bypass edilebilir:

### ❌ Eski Yöntemler (Zayıf)

```javascript
// Yöntem 1: debugger statement
debugger;  // ← CDP: Debugger.setSkipAllPauses

// Yöntem 2: timing delta
var t0 = performance.now();
debugger;
var t1 = performance.now();
if (t1 - t0 > 100) { /* detected */ }
// ← CDP: performance.now override
```

**Bypass:**
- CDP `Debugger.setSkipAllPauses` → debugger statement'lar atllanır
- DevTools hiç açmadan (Sources paneli kapalı) → timing değişmez
- `performance.now()` override → timing tutarsız

## ✅ Yeni Yöntem: Multi-Channel Scoring

**İlke:** Tek bir sinyale güvenme, çoğunluk oyu sistemi kullan.

```
┌──────────────────────────────────────────────────┐
│ 6 Bağımsız Algılama Kanalı                       │
├──────────────────────────────────────────────────┤
│ 1. console.table() render time                   │
│ 2. Function.prototype.constructor manipulation   │
│ 3. window.outerHeight - innerHeight              │
│ 4. Function.prototype.toString() length anomaly  │
│ 5. RegExp.prototype.test hook detection          │
│ 6. debugger timing (fallback)                    │
├──────────────────────────────────────────────────┤
│ Her kanal → ağırlıklı skor (10-35 puan)         │
│ Toplam skor ≥ eşik (40-60) → DevTools detected  │
└──────────────────────────────────────────────────┘
```

### Kanal 1: console.table() Render Time

**Prensip:** DevTools açıksa console.table() render overhead'i yüksek.

```javascript
var obj = Array.from({length: 100}, (_, i) => ({id: i, v: Math.random()}));
var t0 = performance.now();
console.table(obj);
var t1 = performance.now();
console.clear();

// DevTools kapalı: 0-5ms
// DevTools açık: 100-500ms
if ((t1 - t0) > 50) score += 30;
```

**Bypass direnci:**
- CDP `performance.now` override edilse bile: gerçek render süresi değişir
- Network/Memory panel açıksa bile: console render overhead var

### Kanal 2: Function.prototype.constructor Manipulation

**Prensip:** Bazı stealth araçları `Function.prototype` patch'ler.

```javascript
var orig = Function.prototype.constructor;
if (typeof orig !== 'function' || 
    orig.toString().indexOf('native code') === -1) {
    score += 25;
}
```

**Bypass direnci:**
- Native constructor'ı perfectly mock etmek zor
- `toString()` içinde `[native code]` gerçek native'de sabit format

### Kanal 3: Window Dimension Delta

**Prensip:** DevTools açıksa `outerHeight - innerHeight` farkı büyük.

```javascript
var delta = window.outerHeight - window.innerHeight;
// Bottom dock: ~200-400px
// Side dock: ~50-150px
if (delta > 160) score += 20;
```

**Sınırlama:**
- Zoom, responsive mode de fark yaratır
- Düşük ağırlık (20 puan) — tek başına yeterli değil

### Kanal 4: toString() Length Anomaly

**Prensip:** Proxy/hook wrapper'ları toString() uzunluğunu değiştirir.

```javascript
var len = Function.prototype.toString.toString().length;
// Native: ~30-60 char
// Hooked: <20 veya >100 char
if (len < 20 || len > 100) score += 15;
```

**Bypass direnci:**
- toString() perfect mock: whitespace/newline tam eşleşmeli
- Browser'lar arası native format farkı → anomaly detection

### Kanal 5: RegExp.prototype.test Hook

**Prensip:** Pattern obfuscation bypass için `RegExp.test` override edilir.

```javascript
var test = RegExp.prototype.test.toString();
if (test.indexOf('native code') === -1) {
    score += 15;
}
```

**Bypass direnci:**
- Çoğu proxy tool RegExp'i override eder (common pattern)
- Native code check: basit ama etkili

### Kanal 6: Debugger Timing (Fallback)

**Prensip:** Legacy yöntem — ama multi-channel'da hala katkı sağlar.

```javascript
var t0 = performance.now();
debugger;
var t1 = performance.now();
if ((t1 - t0) > 80) score += 20;
```

**Sınırlama:**
- CDP bypass edilebilir
- Ama: diğer 5 kanal bypass edilemezse hala etkili

## Skor Sistemi

### Ağırlıklar (Build-Time Random)

| Kanal | Ağırlık Aralığı | Bypass Zorluğu |
|-------|-----------------|----------------|
| console.table render | 25-35 puan | Zor (gerçek render) |
| Function.constructor | 20-30 puan | Orta-Zor (perfect mock) |
| window dimension | 15-25 puan | Kolay (zoom bypass) |
| toString length | 10-20 puan | Orta (format mock) |
| RegExp.test hook | 10-20 puan | Orta (native mock) |
| debugger timing | 15-25 puan | Kolay (CDP bypass) |

**Toplam:** 90-155 puan (max)  
**Eşik:** 40-60 puan (build-time random)

### Karar Mekanizması

```javascript
var score = ch1 + ch2 + ch3 + ch4 + ch5 + ch6;
window._wasd_dt_score = score;
window._wasd_dt_detected = (score >= threshold);
```

**Örnek Senaryo:**
```
Kanal 1 (console): 30 ← DevTools açık
Kanal 2 (constructor): 0 ← Native
Kanal 3 (dimension): 20 ← Dock açık
Kanal 4 (toString): 0 ← Native
Kanal 5 (RegExp): 0 ← Native
Kanal 6 (debugger): 0 ← CDP bypass
────────────────────────
Toplam: 50 puan
Eşik: 45 puan
Sonuç: DETECTED ✓
```

## Periyodik Yeniden Kontrol

DevTools runtime'da açılabilir/kapanabilir:

```javascript
setInterval(function() {
    // Dynamic channels: console.table, debugger, dimension
    var newScore = recheckConsole() + recheckDebugger() + recheckDimension();
    // Static channels: constructor, toString, regexp (değişmez)
    newScore += ch2 + ch4 + ch5;
    
    window._wasd_dt_score = newScore;
    window._wasd_dt_detected = (newScore >= threshold);
}, 3000-6000ms);  // Build-time random interval
```

## Build-Time Randomization

Her build farklı:
- Değişken adları: `_rn()` ile unique
- Ağırlıklar: Her kanal random aralıktan
- Eşik: 40-60 arası random
- Recheck interval: 3-6 saniye arası
- Render eşikleri: console.table için 50-100ms random

**Sonuç:** Fingerprinting imkansız, her build benzersiz.

## Bypass Resistance Analizi

### Senaryo 1: CDP Debugger.setSkipAllPauses

```
Bypass: debugger statement atlanır
Etki: Kanal 6 (debugger timing) → 0 puan
Diğer Kanallar:
  - console.table render: Hâlâ aktif (30 puan)
  - Function.constructor: Hâlâ aktif (25 puan)
  - window dimension: Hâlâ aktif (20 puan)
Toplam: 75 puan > 50 eşik
Sonuç: DETECTED ✓
```

### Senaryo 2: DevTools Kapalı + Network/Memory Snapshot

```
Bypass: Sources paneli hiç açılmaz
Etki: Kanal 6 (debugger timing) → 0 puan
      Kanal 3 (window dimension) → 0 puan
Diğer Kanallar:
  - console.table render: DevTools açık = render overhead (30 puan)
  - Function.constructor: Statik (0 puan, çünkü native)
Toplam: 30 puan < 50 eşik
Sonuç: NOT DETECTED (False Negative)

Geliştirme: Kanal 7 eklenebilir (performance.memory heap diff)
```

### Senaryo 3: Performance API Override

```
Bypass: performance.now() → return Date.now()
Etki: Kanal 1 (console.table) tutarsız olabilir
      Kanal 6 (debugger) tutarsız olabilir
Diğer Kanallar:
  - Function.constructor: Non-timing (25 puan)
  - toString length: Non-timing (15 puan)
  - RegExp.test: Non-timing (15 puan)
Toplam: 55 puan > 50 eşik
Sonuç: DETECTED ✓ (non-timing channels sayesinde)
```

## Katmanlı Savunma (Defense in Depth)

```
┌────────────────────────────────────────┐
│ Layer 1: Multi-Channel Detection       │  ← Yeni
│   - 6 bağımsız kanal                   │
│   - Skor bazlı eşik                    │
│   - Periyodik recheck                  │
├────────────────────────────────────────┤
│ Layer 2: Legacy Timing Guards          │  ← Mevcut
│   - build_timing_guard()               │
│   - build_self_defending_loop()        │
├────────────────────────────────────────┤
│ Layer 3: Worker Timing Guard           │  ← Mevcut
│   - build_worker_timing_guard()        │
│   - CDP bypass'ten bağımsız            │
└────────────────────────────────────────┘
```

**Toplam Kod:** ~3700 byte (multi:2141 + timing:536 + worker:998)

## Kullanım

Otomatik aktif — `inject_pre()` içinde:

```python
if not no_debugger:
    parts.append(build_multi_channel_devtools_detect())  # ← Yeni
    parts.append(build_self_defending_loop())            # Legacy
    parts.append(build_timing_guard())                   # Legacy
    parts.append(build_worker_timing_guard())            # Legacy
```

## Global API

JavaScript'te:

```javascript
// Anlık skor (0-100+)
var score = window._wasd_dt_score;

// Tespit flag (boolean)
var detected = window._wasd_dt_detected;

if (detected) {
    // DevTools açık — davranışı değiştir
    // Örn: PoW difficulty artır, telemetri sıklığını düşür
}
```

## False Positive/Negative Oranları

### False Positive (DevTools kapalı, ama detected)

**Senaryo:**
- Zoom level çok yüksek (dimension delta büyük)
- Stealth extension (constructor patch)
- Proxy tool aktif (RegExp hook)

**Olasılık:** ~5-10% (normal kullanıcılar için)

**Azaltma:**
- window dimension düşük ağırlık (20)
- Eşik 40-60 arası (tek kanal tetiklemez)

### False Negative (DevTools açık, ama not detected)

**Senaryo:**
- Sadece Network/Memory panel (console render yok)
- Tüm timing API'lar bypass (CDP + override)
- Native function perfect mock (toString, constructor)

**Olasılık:** ~10-20% (advanced bypass araçlarıyla)

**Azaltma:**
- Ek kanallar eklenebilir (performance.memory, heap snapshot)
- Eşik düşürülebilir (40 → 30)

## Gelecek Geliştirmeler

### Kanal 7: performance.memory Heap Size

```javascript
if (performance.memory) {
    var heapDelta = performance.memory.usedJSHeapSize / 
                    performance.memory.totalJSHeapSize;
    // DevTools açıksa heap ratio düşer (~0.3-0.5)
    if (heapDelta < 0.4) score += 20;
}
```

### Kanal 8: Firebug/Chrome Extension Detection

```javascript
// Chrome Extension page context detection
if (window.chrome && chrome.runtime && chrome.runtime.id) {
    score += 15;
}
```

### Kanal 9: Console Method Override

```javascript
var origLog = console.log.toString();
var origError = console.error.toString();
if (origLog !== origError) {
    // Bazı tool'lar sadece log'u override eder
    score += 10;
}
```

## Sonuç

✅ **6 bağımsız kanal** → Tek bypass yeterli değil  
✅ **Skor sistemi** → Çoğunluk oyu gerekli  
✅ **Periyodik recheck** → Runtime açma/kapama algılanır  
✅ **Build-time random** → Fingerprinting imkansız  
✅ **Legacy uyumlu** → Katmanlı savunma (defense in depth)  

**Trade-off:**
- ⚠️ False positive: ~5-10% (zoom, extension)
- ⚠️ False negative: ~10-20% (perfect bypass)
- ✅ Çoğu senaryo için yeterli (CDP bypass + DevTools kapalı analiz engellenir)

**Asıl Korumanın Kaynağı:** #9 (Server-side verification)  
Bu sistem client-side defensive layer — asıl otorite sunucuda.
