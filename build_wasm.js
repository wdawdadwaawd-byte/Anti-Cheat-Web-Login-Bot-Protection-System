/**
 * build_wasm.js — vm_transform.wat → dist/vm_transform.wasm
 *
 * Kullanım:
 *   node build_wasm.js [--wat <input.wat>] [--out <output.wasm>] [--validate]
 *
 * obfuscate_js.py subprocess.run(['node', 'build_wasm.js', ...]) ile çağırır.
 * Başarıda exit 0, hata/validasyon başarısızlığında exit 1.
 */

'use strict';

const fs      = require('fs');
const path    = require('path');
const process = require('process');

// ── argüman parse ───────────────────────────────────────────────────────────
const args = process.argv.slice(2);
function getArg(flag, def) {
    const idx = args.indexOf(flag);
    return (idx !== -1 && args[idx + 1]) ? args[idx + 1] : def;
}
const validateOnly = args.includes('--validate');

const BASE   = path.dirname(process.argv[1]);
const watIn  = path.resolve(getArg('--wat', path.join(BASE, 'static', 'js', 'wasd-parts', 'vm_transform.wat')));
const wasmOut = path.resolve(getArg('--out', path.join(BASE, 'static', 'js', 'dist',      'vm_transform.wasm')));

// ── wabt yükle ──────────────────────────────────────────────────────────────
let wabtModule;
try {
    wabtModule = require('wabt');
} catch (e) {
    console.error('[build_wasm] wabt paketi bulunamadı. "npm install wabt" çalıştırın.');
    process.exit(1);
}

async function main() {
    // WAT dosyasını oku
    if (!fs.existsSync(watIn)) {
        console.error(`[build_wasm] WAT dosyası bulunamadı: ${watIn}`);
        process.exit(1);
    }
    const watSource = fs.readFileSync(watIn, 'utf8');
    console.log(`[build_wasm] WAT okundu: ${watIn} (${watSource.length} byte)`);

    // wabt başlat
    const wabt = await wabtModule();

    // WAT → WASM
    let wasmModule;
    try {
        wasmModule = wabt.parseWat(watIn, watSource, {
            bulk_memory:       false,
            exceptions:        false,
            mutable_globals:   true,
            sat_float_to_int:  false,
            sign_extension:    true,
            simd:              false,
            tail_call:         false,
            threads:           false,
        });
    } catch (e) {
        console.error('[build_wasm] WAT parse hatası:', e.message);
        process.exit(1);
    }

    // Validasyon
    try {
        wasmModule.validate();
        console.log('[build_wasm] Validasyon: GEÇTI');
    } catch (e) {
        console.error('[build_wasm] Validasyon BAŞARISIZ:', e.message);
        process.exit(1);
    }

    if (validateOnly) {
        console.log('[build_wasm] --validate modu, .wasm yazılmadı.');
        process.exit(0);
    }

    // Binary üret
    const { buffer } = wasmModule.toBinary({
        log:                  false,
        canonicalize_lebs:    true,
        relocatable:          false,
        write_debug_names:    false,  // debug semboller çıktıya girmesin
    });

    // ── Custom section strip ─────────────────────────────────────────────────
    // write_debug_names:false ile name section genellikle eklenmez, ama
    // wabt versiyonuna göre farklılık gösterebilir. Güvence: binary'den
    // tüm custom section'ları (section id=0) elle sileriz.
    // Custom section formatı: [0x00][varuint32 size][varuint32 name_len][name...][data...]
    function stripCustomSections(raw) {
        const src = new Uint8Array(raw);
        // WASM magic + version (8 byte) koru
        if (src[0] !== 0x00 || src[1] !== 0x61 || src[2] !== 0x73 || src[3] !== 0x6D) {
            return raw; // WASM magic yok, dokunma
        }
        const out = [0x00, 0x61, 0x73, 0x6D, src[4], src[5], src[6], src[7]];
        let i = 8;
        let stripped = 0;
        while (i < src.length) {
            const sectionId = src[i];
            // varuint32 size oku
            let sizeVal = 0, sizeBytes = 0, shift = 0;
            let j = i + 1;
            while (j < src.length) {
                const b = src[j++]; sizeBytes++;
                sizeVal |= (b & 0x7F) << shift; shift += 7;
                if ((b & 0x80) === 0) break;
            }
            const sectionEnd = j + sizeVal;
            if (sectionId === 0x00) {
                // Custom section — atla
                stripped++;
            } else {
                // Tut
                for (let k = i; k < sectionEnd && k < src.length; k++) out.push(src[k]);
            }
            i = sectionEnd;
        }
        if (stripped > 0) console.log(`[build_wasm] ${stripped} custom section strip edildi`);
        return Buffer.from(out);
    }

    const stripped = stripCustomSections(Buffer.from(buffer));
    console.log(`[build_wasm] Strip sonrası boyut: ${stripped.length} byte (orijinal: ${buffer.byteLength} byte)`);

    // Çıktı dizini yoksa oluştur
    const outDir = path.dirname(wasmOut);
    if (!fs.existsSync(outDir)) fs.mkdirSync(outDir, { recursive: true });

    fs.writeFileSync(wasmOut, stripped);
    const size = fs.statSync(wasmOut).size;
    console.log(`[build_wasm] WASM yazıldı: ${wasmOut} (${size} byte)`);

    // Hızlı runtime doğrulama: Node'da instantiate et, export'lar var mı?
    try {
        const wasmBytes = fs.readFileSync(wasmOut);
        const mem = new WebAssembly.Memory({ initial: 1 });
        const result = await WebAssembly.instantiate(wasmBytes, { env: { memory: mem } });
        const exports = result.instance.exports;

        const required = ['apply_op', 'apply_ops', 'get_token', 'score'];
        for (const fn of required) {
            if (typeof exports[fn] !== 'function') {
                throw new Error(`Export eksik: ${fn}`);
            }
        }
        console.log('[build_wasm] Runtime doğrulama: GEÇTI (apply_op, apply_ops, get_token, score mevcut)');

        // Basit smoke test: XOR op (op_id=0, k=42, offset=0, length=4)
        const view = new Uint8Array(mem.buffer);
        view[0] = 0xAA; view[1] = 0xBB; view[2] = 0xCC; view[3] = 0xDD;
        exports.apply_op(0, 42, 0, 4);
        const expected = [
            0xAA ^ (42 % 256),
            0xBB ^ (43 % 256),
            0xCC ^ (44 % 256),
            0xDD ^ (45 % 256),
        ];
        for (let i = 0; i < 4; i++) {
            if (view[i] !== expected[i]) {
                throw new Error(`XOR smoke test başarısız @ byte ${i}: got ${view[i]}, expected ${expected[i]}`);
            }
        }
        console.log('[build_wasm] Smoke test (XOR): GEÇTI');

        // get_token smoke test: aynı seed → aynı sonuç, farklı seed → farklı sonuç
        const t1 = exports.get_token(0x12345678);
        const t2 = exports.get_token(0x12345678);
        const t3 = exports.get_token(0x87654321);
        if (t1 !== t2) throw new Error('get_token: aynı seed farklı sonuç!');
        if (t1 === t3) throw new Error('get_token: farklı seed aynı sonuç (collision)!');
        if (t1 === 0) throw new Error('get_token: sıfır döndü (degenerate)!');
        console.log(`[build_wasm] Smoke test (get_token): GEÇTI — token(0x12345678)=0x${(t1 >>> 0).toString(16)}`);

        // score smoke test:
        //   Tüm sinyaller 0 → score=0 < threshold=50 → beklenen: 0
        //   env=1 (ağırlık 50) + threshold=50 → score=50 >= 50 → beklenen: 1
        //   wdrv=1 (40) + cdp=1 (35) + threshold=50 → score=75 >= 50 → beklenen: 1
        const scoreAllZero = exports.score(0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,50);
        if (scoreAllZero !== 0) throw new Error(`score: tüm sinyaller 0 iken ${scoreAllZero} döndü`);
        const scoreEnvOnly = exports.score(1,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,50);
        if (scoreEnvOnly !== 1) throw new Error(`score: env=1 threshold=50 iken ${scoreEnvOnly} döndü`);
        const scoreIframe = exports.score(0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,1,0,50);
        if (scoreIframe !== 0) throw new Error(`score: iframe_hook=1(30) threshold=50 iken ${scoreIframe} döndü, 0 beklendi`);
        const scoreIframeStack = exports.score(0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,1,1,50);
        if (scoreIframeStack !== 1) throw new Error(`score: iframe+stack=55 threshold=50 iken ${scoreIframeStack} döndü, 1 beklendi`);
        console.log('[build_wasm] Smoke test (score): GEÇTI');
    } catch (e) {
        console.error('[build_wasm] Runtime doğrulama BAŞARISIZ:', e.message);
        process.exit(1);
    }

    // Temizle — wabt.destroy() bazı Node versiyonlarında libuv assertion hatası veriyor,
    // bu çıktıyı etkilemez ancak exit code'u bozar; güvenli çıkış için atlıyoruz.
    // wasmModule.destroy();
    console.log('[build_wasm] Tamamlandı.');
    process.exit(0);
}

main().catch(e => {
    console.error('[build_wasm] Beklenmedik hata:', e);
    process.exit(1);
});
