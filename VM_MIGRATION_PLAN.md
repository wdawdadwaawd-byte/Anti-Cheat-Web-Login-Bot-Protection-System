# VM Koruması Migrasyonu: Loader → Part3

## Mevcut Durum (YANLIŞ)

```
┌─────────────────────────────────────────────────────┐
│ Loader (wasd-loader.js)                            │
├─────────────────────────────────────────────────────┤
│ VM Dispatch: script injection sequence              │
│   1. init                                           │
│   2. createElement("script")                        │
│   3. setAttribute("src", url)                       │
│   4. appendChild(script)                            │
│                                                     │
│ Koruma Değeri: DÜŞ��K                               │
│   - Düz doğrusal akış (4 adım)                     │
│   - Dallanma YOK                                    │
│   - State YOK                                       │
│   - Reverse engineering: KOLAY                      │
└─────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────┐
│ Part3 (part3-pow-vm.js)                            │
├─────────────────────────────────────────────────────┤
│ Gerçek Hesaplama Mantığı: VM YOK                   │
│   - PoW döngüsü (nonce brute-force)                │
│   - Operations transform (8 farklı op)              │
│   - Score hesaplama (12 sinyal, ağırlıklar)        │
│                                                     │
│ Koruma Değeri: YÜKSEK (ama korunmamış)            │
│   - Karmaşık dallanma                               │
│   - State machine (nonce, hash, score)              │
│   - Reverse engineering: ORTA-ZOR                   │
│   → AMA düz JS olarak okunabilir                    │
└─────────────────────────────────────────────────────┘
```

## Hedef Durum (DOĞRU)

```
┌─────────────────────────────────────────────────────┐
│ Loader (wasd-loader.js)                            │
├─────────────────────────────────────────────────────┤
│ Minimal veya VM YOK                                 │
│   - Basit script injection (düz JS)                │
│   - Koruma: Obfuscation yeterli                    │
│   - VM overhead: Gereksiz                           │
└─────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────┐
│ Part3 (part3-pow-vm.js) — VM PROTECTED             │
├─────────────────────────────────────────────────────┤
│ VM Bytecode Engine:                                 │
│   ┌─────────────────────────────────────────┐      │
│   │ PoW Döngüsü → VM Instruction Set        │      │
│   │   - LOOP_INIT(limit)                    │      │
│   │   - CONCAT_STR(salt, nonce)             │      │
│   │   - SHA256_DIGEST(buffer)               │      │
│   │   - PREFIX_CHECK(hash, prefix)          │      │
│   │   - BRANCH_IF_MATCH(success_label)      │      │
│   │   - INCREMENT_NONCE()                   │      │
│   │   - JUMP_IF_LT(loop_start, limit)       │      │
│   └─────────────────────────────────────────┘      │
│                                                     │
│   ┌─────────────────────────────────────────┐      │
│   │ Operations Transform → VM Bytecode       │      │
│   │   - LOAD_OPS_ARRAY()                    │      │
│   │   - DISPATCH_OP(op_id, k, data)         │      │
│   │   - XOR_LOOP / ADD_MOD / ROT / ...      │      │
│   │   - NEXT_OP()                            │      │
│   └─────────────────────────────────────────┘      │
│                                                     │
│ Koruma Değeri: ÇOK YÜKSEK                          │
│   - Karmaşık control flow → bytecode               │
│   - State machine → VM registers                    │
│   - Reverse engineering: ZOR                        │
│   - Static analysis: Opaque bytecode                │
└─────────────────────────────────────────────────────┘
```

## Implementasyon Planı

### Faz 1: VM Instruction Set Tasarımı

```python
# obfuscate_js.py — yeni VM engine
VM_OPCODES = {
    # ── Control Flow ───────────────────────────────────
    'NOP':           0x00,  # No-op
    'JUMP':          0x01,  # Unconditional jump
    'JUMP_IF':       0x02,  # Conditional jump (if stack top != 0)
    'JUMP_IF_NOT':   0x03,  # Conditional jump (if stack top == 0)
    'CALL':          0x04,  # Function call
    'RET':           0x05,  # Return
    
    # ── Stack Operations ───────────────────────────────
    'PUSH':          0x10,  # Push immediate value
    'POP':           0x11,  # Pop stack
    'DUP':           0x12,  # Duplicate stack top
    'SWAP':          0x13,  # Swap top two
    
    # ── Arithmetic ─────────────────────────────────────
    'ADD':           0x20,  # Pop 2, push sum
    'SUB':           0x21,  # Pop 2, push diff
    'MUL':           0x22,  # Pop 2, push product
    'MOD':           0x23,  # Pop 2, push modulo
    'INC':           0x24,  # Increment stack top
    
    # ── Comparison ─────────────────────────────────────
    'EQ':            0x30,  # Pop 2, push (a == b)
    'LT':            0x31,  # Pop 2, push (a < b)
    'GT':            0x32,  # Pop 2, push (a > b)
    
    # ── String/Buffer Operations ───────────────────────
    'STR_CONCAT':    0x40,  # Pop 2 strings, push concatenation
    'STR_SLICE':     0x41,  # Pop string, start, len → push substring
    'BUFFER_XOR':    0x42,  # Pop buffer, key → push XOR result
    'SHA256':        0x43,  # Pop buffer → push SHA-256 hash (async!)
    
    # ── PoW Specific ───────────────────────────────────
    'POW_INIT':      0x50,  # Initialize PoW state (salt, difficulty)
    'POW_HASH':      0x51,  # Hash current nonce candidate
    'POW_CHECK':     0x52,  # Check if hash matches prefix
    'POW_NEXT':      0x53,  # Increment nonce
    
    # ── VM Transform Specific ──────────────────────────
    'VM_LOAD_OPS':   0x60,  # Load operations array
    'VM_DISPATCH':   0x61,  # Dispatch current operation
    'VM_NEXT_OP':    0x62,  # Move to next operation
}
```

### Faz 2: PoW Döngüsü → VM Bytecode Compiler

**Orijinal JS:**
```javascript
E.solvePoW = async function (salt, difficulty) {
    var targetPrefix = '0'.repeat(difficulty);
    var nonce = 0;
    while (nonce < 10000000) {
        var candidate = salt + nonce.toString();
        var hash = await sha256(candidate);
        if (hash.startsWith(targetPrefix)) {
            return { solution: nonce.toString(), hash: hash };
        }
        nonce++;
    }
    return null;
};
```

**VM Bytecode (pseudo):**
```
LABEL_LOOP_START:
    PUSH nonce              # Stack: [nonce]
    PUSH 10000000          # Stack: [nonce, limit]
    LT                      # Stack: [nonce < limit]
    JUMP_IF_NOT LABEL_END
    
    PUSH salt               # Stack: [salt]
    PUSH nonce              # Stack: [salt, nonce]
    STR_CONCAT              # Stack: [candidate]
    SHA256                  # Stack: [hash]  (async!)
    DUP                     # Stack: [hash, hash]
    PUSH targetPrefix       # Stack: [hash, hash, prefix]
    STR_STARTS_WITH         # Stack: [hash, match]
    JUMP_IF LABEL_FOUND
    
    POP                     # Discard hash
    PUSH nonce
    INC                     # nonce++
    STORE nonce
    JUMP LABEL_LOOP_START
    
LABEL_FOUND:
    # Return {solution, hash}
    ...
    RET
    
LABEL_END:
    PUSH null
    RET
```

### Faz 3: Operations Transform → VM Bytecode

**Orijinal JS (8 operation types):**
```javascript
for (var i = 0; i < operations.length; i++) {
    var t = operations[i].op;
    var k = operations[i].k;
    if (t === 'XOR') {
        for (var idx = 0; idx < n; idx++) 
            dataBytes[idx] ^= (k + idx) % 256;
    } else if (t === 'ADD_MOD') {
        for (var idx = 0; idx < n; idx++) 
            dataBytes[idx] = (dataBytes[idx] + k) % 256;
    }
    // ... 6 more op types
}
```

**VM Bytecode Dispatch Table:**
```
VM_LOAD_OPS operations     # Load ops array
PUSH 0                     # i = 0

LABEL_OPS_LOOP:
    PUSH i
    PUSH ops.length
    LT
    JUMP_IF_NOT LABEL_OPS_END
    
    LOAD ops[i].op_id      # Load current op type (integer)
    LOAD ops[i].k
    VM_DISPATCH            # Jump to handler based on op_id
    
    INC i
    JUMP LABEL_OPS_LOOP
    
LABEL_OPS_END:
    RET

# ── Op Handlers ──────────────────────────────────────
LABEL_OP_XOR:
    # dataBytes[idx] ^= (k + idx) % 256
    ...
    RET_TO_DISPATCH

LABEL_OP_ADD_MOD:
    # dataBytes[idx] = (dataBytes[idx] + k) % 256
    ...
    RET_TO_DISPATCH
```

### Faz 4: VM Interpreter (JS)

```javascript
// part3-pow-vm.js içinde
var _VM = {
    stack: [],
    regs: { nonce: 0, i: 0, limit: 0 },
    ops: null,          // operations array
    dataBytes: null,    // transform buffer
    pc: 0,              // program counter
    
    exec: async function(bytecode) {
        this.pc = 0;
        while (this.pc < bytecode.length) {
            var opcode = bytecode[this.pc++];
            await this._dispatch(opcode, bytecode);
        }
        return this.stack.pop();
    },
    
    _dispatch: async function(opcode, bytecode) {
        switch (opcode) {
            case 0x01: // JUMP
                this.pc = this._readInt(bytecode);
                break;
            case 0x02: // JUMP_IF
                if (this.stack.pop()) 
                    this.pc = this._readInt(bytecode);
                break;
            case 0x10: // PUSH
                this.stack.push(this._readValue(bytecode));
                break;
            case 0x20: // ADD
                var b = this.stack.pop();
                var a = this.stack.pop();
                this.stack.push(a + b);
                break;
            case 0x43: // SHA256
                var buf = this.stack.pop();
                var hash = await crypto.subtle.digest('SHA-256', buf);
                this.stack.push(hash);
                break;
            // ... tüm opcodes
        }
    }
};
```

### Faz 5: Bytecode Generation (Python)

```python
# obfuscate_js.py
def compile_pow_to_bytecode() -> bytes:
    """
    PoW döngüsünü VM bytecode'a derler.
    Returns: Uint8Array literal (JS'ye gömülecek)
    """
    bc = BytecodeBuilder()
    
    # LOOP_START
    bc.label("LOOP_START")
    bc.push_var("nonce")
    bc.push_const(10000000)
    bc.lt()
    bc.jump_if_not("LOOP_END")
    
    # Hash candidate
    bc.push_var("salt")
    bc.push_var("nonce")
    bc.str_concat()
    bc.sha256()
    
    # Check prefix
    bc.dup()
    bc.push_var("targetPrefix")
    bc.str_starts_with()
    bc.jump_if("FOUND")
    
    # Increment nonce
    bc.pop()
    bc.inc_var("nonce")
    bc.jump("LOOP_START")
    
    # Labels
    bc.label("FOUND")
    bc.ret()
    
    bc.label("LOOP_END")
    bc.push_null()
    bc.ret()
    
    return bc.build()

def inject_vm_bytecode_into_part3(part3_js: str) -> str:
    """
    Part3'ün içine VM bytecode'u ve interpreter'ı inject eder.
    """
    bytecode = compile_pow_to_bytecode()
    bytecode_js = f"new Uint8Array([{','.join(str(b) for b in bytecode)}])"
    
    vm_code = f"""
    // ── VM Bytecode Engine ────────────────────────────────────
    var _VM_BYTECODE = {bytecode_js};
    var _VM = {{ /* interpreter implementation */ }};
    
    // Override solvePoW to use VM
    E.solvePoW = async function(salt, difficulty) {{
        _VM.init(salt, difficulty);
        return await _VM.exec(_VM_BYTECODE);
    }};
    """
    
    # part3_js içine inject et (solvePoW tanımından önce)
    return part3_js.replace(
        "E.solvePoW = async function",
        vm_code + "\n    // Original (fallback):\n    E.solvePoW_FALLBACK = async function"
    )
```

## Koruma Kazanımları

| Özellik | Öncesi (Düz JS) | Sonrası (VM Bytecode) |
|---------|------------------|-----------------------|
| **Static Analysis** | ✗ AST parse → tam akış | ✓ Opaque bytecode array |
| **Control Flow Obfuscation** | ✗ Açık if/while | ✓ JUMP/BRANCH opcodes |
| **Deobfuscation** | ✗ Babel/prettier çalışır | ✓ Bytecode → AST dönüşüm yok |
| **Breakpoint Placement** | ✗ Her satır breakable | ✓ VM dispatcher tek nokta |
| **Patch Resistance** | ✗ Direkt function override | ✓ Bytecode modification gerekli |

## Build Process Integration

```
part3-pow-vm.js (kaynak)
    ↓
Python: compile_pow_to_bytecode()
    ↓
VM bytecode + interpreter inject
    ↓
inject_pre() — RC4/nonce layers
    ↓
javascript-obfuscator
    ↓
part3-pow-vm.js (final)
```

## Fallback Path

VM runtime hatası olursa (bytecode corruption, interpreter bug):
```javascript
if (_VM_AVAILABLE) {
    return await _VM.exec(_VM_BYTECODE);
} else {
    return await E.solvePoW_FALLBACK(salt, difficulty);
}
```

Original düz JS kodunu `_FALLBACK` suffix'i ile sakla — sadece VM tamamen bozulduğunda çalışır.

## Performans Trade-off

- **VM Overhead:** ~20-30% yavaşlama (interpretation cost)
- **Security Gain:** Control flow opaque, static analysis engellenir
- **Acceptable:** PoW zaten compute-intensive, 20-30% ek maliyet tolere edilebilir

## Sonuç

✅ **Loader VM'i kaldır** → Basit script injection için gereksiz  
✅ **Part3'e VM taşı** → PoW/transform mantığı gerçek koruma değerine sahip  
✅ **Bytecode compilation** → Build-time'da Python, runtime'da JS interpreter  
✅ **Fallback korun** → VM hatası olursa düz JS çalışır (availability)  

**Hedef:** Yüksek değerli hesaplama mantığını VM katmanının arkasına sakla, düşük değerli loader'ı olduğu gibi bırak.
