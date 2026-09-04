(module
  ;; Shared memory: JS ve WASM ayni buffer'i okur/yazar.
  (import "env" "memory" (memory 1))

  ;; ────────────────────────────────────────────────────────────────────────
  ;; OPAQUE HELPER FONKSIYONLAR
  ;; Her zaman sabit doner ama decompiler statik analiz yapamiyor:
  ;;   $junk_mix : karmasik aritmetik → her zaman 0 doner
  ;;   $junk_gate: dead-if zinciri → her zaman parametre doner
  ;; score() ve get_token() bunlari cagirip sonucu atar (dead store) —
  ;; analist hangi dallin gercek oldugunu anlamak icin tum grafigi izlemeli.
  ;; ────────────────────────────────────────────────────────────────────────

  ;; $junk_mix: (a XOR a) * b + (b - b) = 0. Decompiler XOR-self
  ;; optimizasyonunu yapamazsa butun carpma/toplama zincirini izler.
  (func $junk_mix (param $a i32) (param $b i32) (result i32)
    (local $t i32)
    (local $u i32)
    ;; t = a ^ a = 0
    (local.set $t (i32.xor (local.get $a) (local.get $a)))
    ;; u = b - b = 0
    (local.set $u (i32.sub (local.get $b) (local.get $b)))
    ;; sonuc = t * b + u = 0 * b + 0 = 0
    (i32.add
      (i32.mul (local.get $t) (local.get $b))
      (local.get $u))
  )

  ;; $junk_gate: dead-branch zinciri — kodul 0 olmasi mumkun degil
  ;; ama decompiler her dallin hedefini analiz etmek zorunda.
  (func $junk_gate (param $x i32) (result i32)
    (local $r i32)
    (local.set $r (local.get $x))
    ;; i32.const 0: dead branch — hic calismiyor
    (if (i32.const 0) (then
      (local.set $r (i32.add (local.get $r) (i32.const 0x13371337)))
    ))
    (if (i32.const 0) (then
      (local.set $r (i32.xor (local.get $r) (i32.const 0xDEADBEEF)))
    ))
    (local.get $r)
  )

  ;; $junk_hash: rol+xor zinciri — sonuc gercekte parametre * 1 ama
  ;; sabit katlanmasi olmadan decompiler tum rotasyonlari izler.
  ;; (x ROL 0) = x; (x XOR 0) = x; net etki: parametreyi aynen doner
  (func $junk_hash (param $v i32) (result i32)
    (local $h i32)
    ;; ROL 0 = NOP equivalent ama binary'de shl+shr+or gorulur
    (local.set $h
      (i32.or
        (i32.shl  (local.get $v) (i32.const 0))
        (i32.shr_u (local.get $v) (i32.const 32))))
    ;; XOR 0 = NOP
    (local.set $h (i32.xor (local.get $h) (i32.const 0)))
    (local.get $h)
  )

  ;; ────────────────────────────────────────────────────────────────────────
  ;; apply_op: Tek bir transform operasyonunu bellege uygular.
  ;; ────────────────────────────────────────────────────────────────────────
  (func $apply_op (export "apply_op")
        (param $op_id i32) (param $k i32)
        (param $offset i32) (param $length i32)

    ;; Gercek local'ler
    (local $i     i32)
    (local $b     i32)
    (local $addr  i32)
    (local $addr2 i32)
    (local $shift i32)
    (local $tmp   i32)
    (local $mk    i32)
    (local $mirror i32)
    ;; Dead local'ler — kullanilmiyor ama decompiler type inference'i karistiriyor
    (local $dead0 i32)
    (local $dead1 i32)
    (local $dead2 i32)

    ;; Junk: opaque helper cagir, sonucu dead local'e at (side-effect yok)
    (local.set $dead0 (call $junk_mix (local.get $op_id) (local.get $k)))
    (local.set $dead1 (call $junk_gate (local.get $length)))

    ;; op 0: XOR
    (if (i32.eq (local.get $op_id) (i32.const 0)) (then
      (local.set $i (i32.const 0))
      (block $brk0 (loop $lp0
        (br_if $brk0 (i32.ge_u (local.get $i) (local.get $length)))
        (local.set $addr (i32.add (local.get $offset) (local.get $i)))
        (local.set $b (i32.load8_u (local.get $addr)))
        ;; Dead branch: offset == -1 hicbir zaman dogru degil
        (if (i32.eq (local.get $offset) (i32.const -1)) (then
          (local.set $dead2 (i32.add (local.get $b) (i32.const 1)))
        ))
        (i32.store8 (local.get $addr)
          (i32.xor (local.get $b)
            (i32.rem_u (i32.add (local.get $k) (local.get $i)) (i32.const 256))))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br $lp0)
      ))
    ))

    ;; op 1: ADD_MOD
    (if (i32.eq (local.get $op_id) (i32.const 1)) (then
      (local.set $i (i32.const 0))
      (block $brk1 (loop $lp1
        (br_if $brk1 (i32.ge_u (local.get $i) (local.get $length)))
        (local.set $addr (i32.add (local.get $offset) (local.get $i)))
        (local.set $b (i32.load8_u (local.get $addr)))
        (i32.store8 (local.get $addr)
          (i32.rem_u (i32.add (local.get $b) (local.get $k)) (i32.const 256)))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br $lp1)
      ))
    ))

    ;; op 2: ROT — circular left shift, shift = (k%7)+1
    (if (i32.eq (local.get $op_id) (i32.const 2)) (then
      (local.set $shift
        (i32.add (i32.rem_u (local.get $k) (i32.const 7)) (i32.const 1)))
      (local.set $i (i32.const 0))
      (block $brk2 (loop $lp2
        (br_if $brk2 (i32.ge_u (local.get $i) (local.get $length)))
        (local.set $addr (i32.add (local.get $offset) (local.get $i)))
        (local.set $b (i32.load8_u (local.get $addr)))
        (i32.store8 (local.get $addr)
          (i32.and
            (i32.or
              (i32.shl (local.get $b) (local.get $shift))
              (i32.shr_u (local.get $b)
                (i32.sub (i32.const 8) (local.get $shift))))
            (i32.const 255)))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br $lp2)
      ))
    ))

    ;; op 3: SBOX — ((255 - data[idx]) ^ k) & 0xFF
    (if (i32.eq (local.get $op_id) (i32.const 3)) (then
      (local.set $i (i32.const 0))
      (block $brk3 (loop $lp3
        (br_if $brk3 (i32.ge_u (local.get $i) (local.get $length)))
        (local.set $addr (i32.add (local.get $offset) (local.get $i)))
        (local.set $b (i32.load8_u (local.get $addr)))
        (i32.store8 (local.get $addr)
          (i32.and
            (i32.xor (i32.sub (i32.const 255) (local.get $b)) (local.get $k))
            (i32.const 255)))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br $lp3)
      ))
    ))

    ;; op 4: SWAP_PAIRS — her cift byte yer degistir
    (if (i32.eq (local.get $op_id) (i32.const 4)) (then
      (local.set $i (i32.const 0))
      (block $brk4 (loop $lp4
        (br_if $brk4 (i32.ge_u (local.get $i)
          (i32.sub (local.get $length) (i32.const 1))))
        (local.set $addr  (i32.add (local.get $offset) (local.get $i)))
        (local.set $addr2 (i32.add (local.get $addr) (i32.const 1)))
        (local.set $tmp   (i32.load8_u (local.get $addr)))
        (i32.store8 (local.get $addr)  (i32.load8_u (local.get $addr2)))
        (i32.store8 (local.get $addr2) (local.get $tmp))
        (local.set $i (i32.add (local.get $i) (i32.const 2)))
        (br $lp4)
      ))
    ))

    ;; op 5: MUL_MOD — mk = k|1; data[idx] = (data[idx]*mk) % 256
    (if (i32.eq (local.get $op_id) (i32.const 5)) (then
      (local.set $mk (i32.or (local.get $k) (i32.const 1)))
      (local.set $i (i32.const 0))
      (block $brk5 (loop $lp5
        (br_if $brk5 (i32.ge_u (local.get $i) (local.get $length)))
        (local.set $addr (i32.add (local.get $offset) (local.get $i)))
        (local.set $b (i32.load8_u (local.get $addr)))
        (i32.store8 (local.get $addr)
          (i32.rem_u (i32.mul (local.get $b) (local.get $mk)) (i32.const 256)))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br $lp5)
      ))
    ))

    ;; op 6: FOLD_XOR — data[idx] ^= data[n-1-idx], idx < n/2
    (if (i32.eq (local.get $op_id) (i32.const 6)) (then
      (local.set $i (i32.const 0))
      (block $brk6 (loop $lp6
        (br_if $brk6 (i32.ge_u (local.get $i)
          (i32.div_u (local.get $length) (i32.const 2))))
        (local.set $addr
          (i32.add (local.get $offset) (local.get $i)))
        (local.set $mirror
          (i32.add (local.get $offset)
            (i32.sub (i32.sub (local.get $length) (i32.const 1)) (local.get $i))))
        (local.set $b (i32.load8_u (local.get $addr)))
        (i32.store8 (local.get $addr)
          (i32.xor (local.get $b) (i32.load8_u (local.get $mirror))))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br $lp6)
      ))
    ))

    ;; op 7: CASCADE — data[idx] ^= data[idx-1], idx = 1..n-1
    (if (i32.eq (local.get $op_id) (i32.const 7)) (then
      (local.set $i (i32.const 1))
      (block $brk7 (loop $lp7
        (br_if $brk7 (i32.ge_u (local.get $i) (local.get $length)))
        (local.set $addr (i32.add (local.get $offset) (local.get $i)))
        (i32.store8 (local.get $addr)
          (i32.xor
            (i32.load8_u (local.get $addr))
            (i32.load8_u (i32.sub (local.get $addr) (i32.const 1)))))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br $lp7)
      ))
    ))
  )

  ;; ────────────────────────────────────────────────────────────────────────
  ;; apply_ops: ops array'ini sirayla isler — JS'den tek WASM cagrisi.
  ;; ────────────────────────────────────────────────────────────────────────
  (func $apply_ops (export "apply_ops")
        (param $ops_ptr i32) (param $ops_count i32)
        (param $data_offset i32) (param $data_len i32)

    (local $i i32)
    (local $entry_ptr i32)
    (local $op_id i32)
    (local $k i32)
    ;; Dead local — junk helper sonucunu tutar
    (local $junk i32)

    (local.set $i (i32.const 0))
    ;; Junk: her ops batch basinda helper cagir (dead store)
    (local.set $junk (call $junk_mix (local.get $ops_ptr) (local.get $ops_count)))
    (block $brk (loop $lp
      (br_if $brk (i32.ge_u (local.get $i) (local.get $ops_count)))

      (local.set $entry_ptr
        (i32.add (local.get $ops_ptr)
          (i32.mul (local.get $i) (i32.const 8))))

      (local.set $op_id (i32.load (local.get $entry_ptr)))
      (local.set $k     (i32.load (i32.add (local.get $entry_ptr) (i32.const 4))))

      (call $apply_op
        (local.get $op_id) (local.get $k)
        (local.get $data_offset) (local.get $data_len))

      (local.set $i (i32.add (local.get $i) (i32.const 1)))
      (br $lp)
    ))
  )

  ;; ────────────────────────────────────────────────────────────────────────
  ;; get_token: WASM path imzasi — JS fallback uretemez.
  ;; Junk: opaque helper ile obfuscated akis.
  ;; ────────────────────────────────────────────────────────────────────────
  (func $get_token (export "get_token")
        (param $seed i32)
        (result i32)
    (local $v i32)
    (local $junk i32)
    ;; Junk: dead hash hesabi — sonuc kullanilmiyor
    (local.set $junk (call $junk_hash (local.get $seed)))
    ;; Gercek hesap
    (local.set $v (i32.xor (local.get $seed) (i32.const 0x5A3CF1B7)))
    (local.set $v (i32.or (i32.shl (local.get $v) (i32.const 13)) (i32.shr_u (local.get $v) (i32.const 19))))
    (local.set $v (i32.xor (local.get $v) (i32.const 0x9E3779B9)))
    (local.set $v (i32.mul (local.get $v) (i32.const 0x6B435621)))
    (local.set $v (i32.xor (local.get $v) (i32.shr_u (local.get $v) (i32.const 16))))
    ;; Dead branch: v == 0 pratikte hicbir zaman dogru degil (astronomically unlikely)
    (if (i32.eqz (local.get $v)) (then
      (local.set $junk (call $junk_gate (local.get $v)))
      (local.set $v (i32.add (local.get $v) (i32.const 1)))
    ))
    (local.get $v)
  )

  ;; ────────────────────────────────────────────────────────────────────────
  ;; score: Agirlikli sinyal skorunu hesaplar, esige gore 1/0 doner.
  ;; Obfuscation: dead local'ler + opaque helper cagrisi + dead branch.
  ;; ────────────────────────────────────────────────────────────────────────
  (func $score (export "score")
    (param $env    i32) (param $wdrv  i32) (param $attr  i32) (param $ua    i32)
    (param $pw     i32) (param $cdp   i32) (param $webgl i32) (param $plug  i32)
    (param $perm   i32) (param $lang  i32) (param $dim   i32) (param $outer i32)
    (param $atob_hook  i32) (param $tddec_hook i32) (param $fchr_hook i32)
    (param $iframe_hook i32) (param $stack_hook i32)
    (param $threshold i32)
    (result i32)
    (local $s    i32)
    (local $junk i32)
    ;; Dead local'ler — decompiler type inference'i karistiriyor
    (local $dead0 i32)
    (local $dead1 i32)

    (local.set $s (i32.const 0))

    ;; Junk: ilk parametreler uzerinde opaque hesap (dead store)
    (local.set $junk (call $junk_mix (local.get $env) (local.get $wdrv)))
    (local.set $dead0 (call $junk_gate (local.get $threshold)))

    ;; Dead branch: threshold == 0x7FFFFFFF hicbir zaman dogru degil
    (if (i32.eq (local.get $threshold) (i32.const 0x7FFFFFFF)) (then
      (local.set $dead1 (call $junk_hash (local.get $s)))
      (local.set $s (i32.const 0))
    ))

    ;; env: +50
    (if (i32.eq (local.get $env) (i32.const 1))
      (then (local.set $s (i32.add (local.get $s) (i32.const 50)))))

    ;; wdrv: +40
    (if (i32.eq (local.get $wdrv) (i32.const 1))
      (then (local.set $s (i32.add (local.get $s) (i32.const 40)))))

    ;; attr: +35
    (if (i32.eq (local.get $attr) (i32.const 1))
      (then (local.set $s (i32.add (local.get $s) (i32.const 35)))))

    ;; ua: +30
    (if (i32.eq (local.get $ua) (i32.const 1))
      (then (local.set $s (i32.add (local.get $s) (i32.const 30)))))

    ;; pw: +30
    (if (i32.eq (local.get $pw) (i32.const 1))
      (then (local.set $s (i32.add (local.get $s) (i32.const 30)))))

    ;; cdp: +35
    (if (i32.eq (local.get $cdp) (i32.const 1))
      (then (local.set $s (i32.add (local.get $s) (i32.const 35)))))

    ;; webgl: +20
    (if (i32.eq (local.get $webgl) (i32.const 1))
      (then (local.set $s (i32.add (local.get $s) (i32.const 20)))))

    ;; plug: +10
    (if (i32.eq (local.get $plug) (i32.const 1))
      (then (local.set $s (i32.add (local.get $s) (i32.const 10)))))

    ;; perm: +20
    (if (i32.eq (local.get $perm) (i32.const 1))
      (then (local.set $s (i32.add (local.get $s) (i32.const 20)))))

    ;; lang: +10
    (if (i32.eq (local.get $lang) (i32.const 1))
      (then (local.set $s (i32.add (local.get $s) (i32.const 10)))))

    ;; dim: +15
    (if (i32.eq (local.get $dim) (i32.const 1))
      (then (local.set $s (i32.add (local.get $s) (i32.const 15)))))

    ;; outer: +15
    (if (i32.eq (local.get $outer) (i32.const 1))
      (then (local.set $s (i32.add (local.get $s) (i32.const 15)))))

    ;; atob_hook: +25
    (if (i32.eq (local.get $atob_hook) (i32.const 1))
      (then (local.set $s (i32.add (local.get $s) (i32.const 25)))))

    ;; tddec_hook: +25
    (if (i32.eq (local.get $tddec_hook) (i32.const 1))
      (then (local.set $s (i32.add (local.get $s) (i32.const 25)))))

    ;; fchr_hook: +20
    (if (i32.eq (local.get $fchr_hook) (i32.const 1))
      (then (local.set $s (i32.add (local.get $s) (i32.const 20)))))

    ;; iframe_hook: +30 — iframe temiz ref != ana frame → Object.defineProperty bypass
    (if (i32.eq (local.get $iframe_hook) (i32.const 1))
      (then (local.set $s (i32.add (local.get $s) (i32.const 30)))))

    ;; stack_hook: +25 — Error().stack Proxy frame derinligi
    (if (i32.eq (local.get $stack_hook) (i32.const 1))
      (then (local.set $s (i32.add (local.get $s) (i32.const 25)))))

    ;; Junk: score hesabinin sonunda opaque gate (dead store, s degismez)
    (local.set $junk (call $junk_gate (local.get $s)))

    ;; Donus: score >= threshold ? 1 : 0
    (i32.ge_s (local.get $s) (local.get $threshold))
  )
)
