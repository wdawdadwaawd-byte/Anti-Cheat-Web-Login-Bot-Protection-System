import ipaddress
from typing import Tuple, Dict, Any, Optional
from fastapi import Request
import config

WARP_NETWORKS = [ipaddress.ip_network(net) for net in config.CLOUDFLARE_WARP_SUBNETS]
DATACENTER_NETWORKS = [ipaddress.ip_network(net) for net in config.DATACENTER_SUBNETS]

def extract_client_ip(request: Request) -> str:
    cf_ip = request.headers.get("cf-connecting-ip")
    if cf_ip:
        return cf_ip.strip()

    xff = request.headers.get("x-forwarded-for")
    if xff:
        client_ip = xff.split(",")[0].strip()
        if client_ip:
            return client_ip

    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()

    if request.client and request.client.host:
        return request.client.host

    return "127.0.0.1"

def check_ip_security(request: Request) -> Tuple[bool, str, Dict[str, Any]]:
    client_ip_str = extract_client_ip(request)
    headers = {k.lower(): v for k, v in request.headers.items()}
    user_agent = headers.get("user-agent", "").lower()

    details = {
        "client_ip": client_ip_str,
        "user_agent": user_agent,
        "is_warp": False,
        "is_datacenter": False,
        "is_bot_header": False,
        "proxy_header_detected": False
    }

    try:
        ip_obj = ipaddress.ip_address(client_ip_str)
        if ip_obj.is_loopback or ip_obj.is_private:
            return True, "Yerel / Geliştirici Ağı", details
    except ValueError:
        return False, "Geçersiz IP Formatı", details

    if not user_agent:
        return False, "Eksik User-Agent (Bot/Script)", details

    for suspicious in config.SUSPICIOUS_USER_AGENTS:
        if suspicious in user_agent:
            details["is_bot_header"] = True
            return False, f"Otomasyon/Checker İmzası Tespit Edildi: {suspicious}", details

    for ph in config.PROXY_HEADERS:
        if ph in headers:
            details["proxy_header_detected"] = True
            return False, f"Açık Proxy Header Tespit Edildi: {ph}", details

    content_type = headers.get("content-type", "")
    accept_language = headers.get("accept-language", "")
    accept_encoding = headers.get("accept-encoding", "")

    # Sadece API endpoint'lerine yapılan header'sız istekleri kontrol et
    # Normal tarayıcı sayfaları için bu kontrolü atla
    if content_type and "application/json" in content_type:
        is_api_path = any(p in str(request.url) for p in ["/api/security", "/api/auth", "/api/challenge"])
        if is_api_path:
            # Hem accept-language hem accept-encoding eksikse → bot
            if not accept_encoding and not accept_language:
                details["proxy_header_detected"] = True
                return False, "Bot/Scanner: HTTP header profili browser ile uyuşmuyor", details

    for net in WARP_NETWORKS:
        if ip_obj in net:
            details["is_warp"] = True
            return False, "Cloudflare WARP (1.1.1.1) VPN Bağlantısı Engellendi", details

    for net in DATACENTER_NETWORKS:
        if ip_obj in net:
            details["is_datacenter"] = True
            return False, "Datacenter / Hosting / Bot Sunucu IP'si Engellendi", details

    return True, "IP ve Ağ Güvenli", details
