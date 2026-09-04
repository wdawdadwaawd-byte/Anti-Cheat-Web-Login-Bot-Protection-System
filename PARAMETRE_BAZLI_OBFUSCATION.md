# Parametre Bazlı Obfuscation Sistemi

## Değişiklik Özeti

Obfuscation sistemi **profil seviyesinden parametre seviyesine** indirildi. Artık sabit profiller yerine her build için parametreler rastgele aralıklardan seçiliyor.

## Önceki Sistem (Profil Bazlı)

```
obfuscator_profiles.json
├── Profile 1 (light)
├── Profile 2 (medium)
├── Profile 3 (heavy)
└── Profile N (5-6 adet sabit profil)
```

**Zayıf Nokta:** Sınırlı sayıda profil → Her profil fingerprint'lenebilir
- Saldırgan tüm profilleri toplar
- Her profil için karakteristik imza çıkarır
- Yeni build'lerde profili tanır ve önceki analizini uygular

## Yeni Sistem (Parametre Bazlı)

```python
Her Build İçin:
  ├── deadCodeInjection: 0.0 - 0.4 (rastgele)
  ├── stringArrayThreshold: 0.4 - 0.9 (rastgele)
  ├── splitStringsChunkLength: 3 - 15 (rastgele)
  ├── controlFlowFlattening: True/False (rastgele)
  ├── stringArrayEncoding: [], ['base64'], ['rc4'], ['base64','rc4'] (rastgele)
  ├── stringArrayRotate: True/False (rastgele)
  ├── stringArrayShuffle: True/False (rastgele)
  ├── stringArrayWrappersCount: 1 - 4 (rastgele)
  ├── unicodeEscapeSequence: True/False (rastgele)
  ├── transformObjectKeys: True/False (rastgele)
  └── ... (tüm parametreler bağımsız rastgele)
```

**Güçlü Nokta:** Sonsuz kombinasyon → Fingerprinting imkansız
- Her build benzersiz bir parametre seti üretir
- Aynı kaynak kod bile her build'de farklı çıktı verir
- Önceki analiz sonraki build'lere uygulanamaz

## Parametre Aralıkları

| Parametre | Aralık | Açıklama |
|-----------|--------|----------|
| `deadCodeInjection` | 0.0 - 0.4 | Dead code yoğunluğu |
| `deadCodeInjectionThreshold` | 0.01 - 0.25 | Dead code injection eşiği |
| `stringArrayThreshold` | 0.4 - 0.9 | String obfuscation oranı |
| `splitStringsChunkLength` | 3 - 15 | String bölme chunk boyutu |
| `controlFlowFlatteningThreshold` | 0.3 - 0.8 | Control flow flattening oranı |
| `stringArrayWrappersCount` | 1 - 4 | Wrapper fonksiyon sayısı |

## Dosya-Özgü Overrides

Belirli dosyalar için parametre aralıkları özelleştirilir:

```python
FILE_CONFIG_OVERRIDES = {
    "WASD-core.js": {
        "deadCodeInjection_range": (0.05, 0.12)  # Daha az dead code
    },
    "challenge-wall.js": {
        "deadCodeInjection_range": (0.08, 0.15)
    },
    "shield-core.js": {
        "deadCodeInjection_range": (0.08, 0.15)
    }
}
```

Her dosya için override aralığından **rastgele** değer seçilir.

## Kombinasyon Hesabı

**Boolean parametreler:** 10 adet × 2 seçenek = 1024 kombinasyon  
**Sayısal parametreler:** Her biri 10-50 farklı değer = ~10^20 kombinasyon  
**Encoding seçenekleri:** 4 farklı seçenek

**Toplam:** ~10^23 farklı kombinasyon (pratikte sonsuz)

## Örnek Çıktı

```
Build 1:
  deadCodeInjection=0.34
  stringArrayThreshold=0.61
  splitStringsChunkLength=11
  controlFlowFlattening=True
  stringArrayEncoding=['rc4']

Build 2:
  deadCodeInjection=0.06
  stringArrayThreshold=0.79
  splitStringsChunkLength=N/A
  controlFlowFlattening=False
  stringArrayEncoding=[]

Build 3:
  deadCodeInjection=0.02
  stringArrayThreshold=0.75
  splitStringsChunkLength=N/A
  controlFlowFlattening=False
  stringArrayEncoding=[]
```

## Fingerprinting Karşılaştırması

### Profil Bazlı (Eski):
1. Saldırgan 6 build toplar
2. Her build farklı profil → 6 profil fingerprint'i çıkar
3. Yeni build gelir → mevcut 6 profilden biriyle eşleşir
4. **Sonuç:** Önceki analiz uygulanabilir ✗

### Parametre Bazlı (Yeni):
1. Saldırgan 1000 build toplar
2. Her build benzersiz → 1000 farklı fingerprint
3. Yeni build gelir → hiçbir önceki build'le eşleşmez
4. **Sonuç:** Her build için sıfırdan analiz gerekli ✓

## Geriye Dönük Uyumluluk

- `obfuscator_profiles.json` hala okunur (sadece `reserved` section için)
- `reservedNames` ve `reservedStrings` korunur
- Eski profil dosyası yoksa sistem doğrudan parametre üretir

## Kullanım

Değişiklik tamamen şeffaf — kullanım aynı:

```bash
python obfuscate_js.py
python obfuscate_js.py --split
python obfuscate_js.py --file WASD-core.js
```

Her çalıştırmada yeni rastgele parametreler üretilir.

## Güvenlik Avantajları

1. **Profil Fingerprinting Engellendi**
   - Sabit profil imzası yok
   - Her build benzersiz kombinasyon

2. **Otomatik Deobfuscation Engellendi**
   - Sabit pattern yok
   - Tool her build için farklı strateji gerektirir

3. **Parametre İstatistiği Engellendi**
   - Geniş aralıklar → ortalama/medyan anlamsız
   - Belirli bir "tipik değer" yok

4. **Zaman İçinde Korumanın Sürekliliği**
   - Her deploy yeni imza
   - Önceki deobfuscation deneyimi geçersiz
   - Sürekli hareket eden hedef

## Log Çıktısı

```
[config] Rastgele parametreler: 
  deadCodeInj=0.34 
  strArrThresh=0.61 
  splitChunk=11 
  ctrlFlow=True 
  encoding=rc4
```

Her build'de parametreler görünür (debugging için).
