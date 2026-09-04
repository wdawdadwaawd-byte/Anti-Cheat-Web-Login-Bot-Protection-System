from typing import Tuple, Dict, Any
import config

VIRTUAL_GPU_RENDERERS = [
    "swiftshader",
    "llvmpipe",
    "mesa offscreen",
    "softpipe",
    "vmware",
    "virtualbox",
    "microsoft basic render driver",
    "google inc. (google)"
]

# ── Canvas hash'lerin bilinen sahte/hatalı değerleri ─────────────────────────
# test_checker ve benzeri araçlar canvas fingerprint üretemez;
# bunun yerine sabit hata string'leri veya boş değer geçirirler.
_CANVAS_BAD_VALUES = {
    "", "canvas_error", "canvas_unsupported", "fp_error",
    "error", "unsupported", "none", "null", "undefined",
}

# ── Audio hash'lerin bilinen sahte/hatalı değerleri ─────────────────────────
# Node.js OfflineAudioContext.startRendering() dummy implementasyonu
# sıfır dolgulu Float32Array döndürür → toplam = 0.0 → "0" veya "0.0"
_AUDIO_BAD_VALUES = {
    "", "audio_error", "audio_unsupported", "error", "unsupported",
    "none", "null", "undefined", "0", "0.0", "0.00",
}

# Near-zero audio: gerçek tarayıcıda audio fingerprint 0.x değil ondalıklı
# 500 karakter üzeri float string'ler gerçek; "0" veya çok kısa değerler sahte.
def _audio_is_fake(audio_hash: str) -> bool:
    """
    Gerçek OfflineAudioContext çıktısı Float kanaldan alınan
    toplam enerjidir; hiçbir zaman tam sıfır ya da çok kısa olmaz.
    """
    if not audio_hash or audio_hash in _AUDIO_BAD_VALUES:
        return True
    # Bazı bot framework'leri sabit kısa sayısal string geçirir
    try:
        val = float(audio_hash)
        if abs(val) < 1e-10:   # sıfıra çok yakın → sahte
            return True
    except ValueError:
        pass  # sayısal değilse normal hash — iyi
    return False


def _canvas_is_fake(canvas_hash: str) -> bool:
    if not canvas_hash:
        return True
    if canvas_hash.lower() in _CANVAS_BAD_VALUES:
        return True
    # Gerçek SHA-256 hex: tam 64 karakter, sadece hex digits
    if len(canvas_hash) == 64 and all(c in "0123456789abcdef" for c in canvas_hash.lower()):
        return False
    # Kısa sabit değer → sahte
    if len(canvas_hash) < 16:
        return True
    return False


def analyze_client_telemetry(telemetry: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
    report = {
        "bot_score": 0,
        "detected_anomalies": [],
        "fingerprint": telemetry.get("canvas_hash", "unknown"),
        "hardware_canvas": telemetry.get("canvas_hash", "unknown"),
        "hardware_audio": telemetry.get("audio_hash", "unknown")
    }

    # ── Honeypot (anında engelle) ────────────────────────────────────────────
    honeypot = telemetry.get("hp_field", "")
    if honeypot and len(str(honeypot).strip()) > 0:
        report["bot_score"] = 100
        report["detected_anomalies"].append("Honeypot Tuzağına Düştü")
        return False, "Bot Tuzağı Tespit Edildi (Honeypot)", report

    # ── navigator.webdriver ─────────────────────────────────────────────────
    if telemetry.get("webdriver", False):
        report["bot_score"] += 80
        report["detected_anomalies"].append("navigator.webdriver = true (Selenium/Puppeteer/Playwright)")

    # ── Otomasyon artefaktları ───────────────────────────────────────────────
    automation_artifacts = telemetry.get("automation_artifacts", [])
    if automation_artifacts:
        report["bot_score"] += 80
        report["detected_anomalies"].append(f"Otomasyon İzi: {', '.join(str(a) for a in automation_artifacts)}")

    # ── Yazılımsal GPU ───────────────────────────────────────────────────────
    webgl_renderer = str(telemetry.get("webgl_renderer", "")).lower()
    webgl_vendor   = str(telemetry.get("webgl_vendor",   "")).lower()
    for v_gpu in VIRTUAL_GPU_RENDERERS:
        if v_gpu in webgl_renderer or v_gpu in webgl_vendor:
            report["bot_score"] += 60
            report["detected_anomalies"].append(f"Yazılımsal/Headless GPU: {webgl_renderer}")
            break

    # ── Canvas fingerprint analizi ───────────────────────────────────────────
    canvas_hash = telemetry.get("canvas_hash", "")
    audio_hash  = telemetry.get("audio_hash",  "")

    canvas_fake = _canvas_is_fake(canvas_hash)
    audio_fake  = _audio_is_fake(audio_hash)

    if canvas_fake and audio_fake:
        # Her ikisi de sahte: Node.js/headless ortam — güçlü sinyal
        report["bot_score"] += 70
        report["detected_anomalies"].append(
            f"Canvas+Audio parmak izi ikisi de sahte/hatalı "
            f"(canvas='{canvas_hash[:20]}', audio='{str(audio_hash)[:20]}')"
        )
    elif canvas_fake:
        # Sadece canvas sahte ama audio gerçek — hafif sinyal
        report["bot_score"] += 30
        report["detected_anomalies"].append(f"Sahte canvas imzası: '{canvas_hash[:20]}'")
    elif audio_fake:
        # Sadece audio sahte (OfflineAudioContext dummy / sıfır çıktı)
        report["bot_score"] += 40
        report["detected_anomalies"].append(
            f"Sahte/sıfır audio fingerprint: '{str(audio_hash)[:20]}' "
            "(OfflineAudioContext dummy veya VM ortamı)"
        )

    # ── Ekran çözünürlüğü ────────────────────────────────────────────────────
    screen_w    = telemetry.get("screen_w",    0)
    screen_h    = telemetry.get("screen_h",    0)
    plugins_len = telemetry.get("plugins_len", 0)

    if screen_w == 0 or screen_h == 0:
        report["bot_score"] += 50
        report["detected_anomalies"].append("Geçersiz Ekran Çözünürlüğü (0x0)")
    elif screen_w == 800 and screen_h == 600 and plugins_len == 0:
        report["bot_score"] += 45
        report["detected_anomalies"].append("Standart Headless Çözünürlük + 0 Plugin (800x600)")

    # ── Dwell time kontrolü ──────────────────────────────────────────────────
    # Çok hızlı: bot script anında gönderir
    # Çok yavaş: otomasyon araçları bazen sabit offset ekler (örn: tam 1200ms)
    dwell_time_ms = telemetry.get("dwell_time_ms", 0)

    if dwell_time_ms < 50:
        report["bot_score"] += 40
        report["detected_anomalies"].append(f"Şüpheli Form Hızı ({dwell_time_ms}ms < 50ms)")
    elif dwell_time_ms > 60000:
        # 60 saniyeden fazla: script'in sleep() ile beklediğini taklit etmesi
        report["bot_score"] += 20
        report["detected_anomalies"].append(f"Anormal Bekleme Süresi ({dwell_time_ms}ms > 60s)")

    # ── Skor kararı ─────────────────────────────────────────────────────────
    if report["bot_score"] >= 60:
        reasons_summary = " | ".join(report["detected_anomalies"])
        return False, f"Bot/Otomasyon Tespiti (Skor: {report['bot_score']}): {reasons_summary}", report

    return True, "İstemci Donanım ve Güvenlik İmzası Doğrulandı", report
