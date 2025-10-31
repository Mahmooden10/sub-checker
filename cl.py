import sys
import subprocess
import time
from pathlib import Path
import logging

from python_v2ray.config_parser import parse_uri, XrayConfigBuilder
from python_v2ray.core import XrayCore # از کلاس اصلی خودتان استفاده می‌کنیم

logging.basicConfig(level=logging.INFO, format='%(asctime)s - [%(levelname)s] - %(message)s')

# --- تنظیمات ---
PROJECT_ROOT = Path(__file__).parent.resolve()
VENDOR_PATH = PROJECT_ROOT / "vendor"
INPUT_CONFIGS_PATH = "normal.txt"
BAD_CONFIGS_LOG_PATH = "bad_configs.txt"
# --- پایان تنظیمات ---

def main():
    print("--- Finding Bad Xray Configs (one-by-one validation) ---")

    try:
        with open(INPUT_CONFIGS_PATH, "r", encoding="utf-8") as f:
            all_uris = [uri.strip() for uri in f if uri.strip()]
    except FileNotFoundError:
        print(f"Error: Input file not found at '{INPUT_CONFIGS_PATH}'")
        return

    print(f"[*] Found {len(all_uris)} URIs to validate.")
    
    bad_configs = []
    
    for i, uri in enumerate(all_uris):
        print(f"\r[*] Validating URI {i+1}/{len(all_uris)}...", end="")

        params = parse_uri(uri)
        if not params:
            # اگر پارسر نتواند آن را بخواند، احتمالاً بد است
            bad_configs.append(f"UNPARSABLE: {uri}")
            continue
        
        # فقط پروتکل‌هایی که Xray پشتیبانی می‌کند را تست می‌کنیم
        if params.protocol not in ["vless", "vmess", "trojan", "ss", "socks", "mvless"]:
            continue

        builder = XrayConfigBuilder()
        builder.add_inbound({
            "tag": "socks-in", "port": 10808, "listen": "127.0.0.1",
            "protocol": "socks", "settings": {"auth": "noauth"}
        })
        outbound = builder.build_outbound_from_params(params)
        if not outbound:
            bad_configs.append(f"UNBUILDABLE: {uri}")
            continue
        builder.add_outbound(outbound)
        builder.config["routing"]["rules"].append({
            "type": "field", "inboundTag": ["socks-in"], "outboundTag": outbound["tag"]
        })

        # حالا با این کانفیگ تکی، Xray را اجرا می‌کنیم
        try:
            with XrayCore(vendor_path=str(VENDOR_PATH), config_builder=builder) as xray:
                time.sleep(0.5) # زمان کوتاه برای اجرا یا کرش کردن
                if not xray.is_running():
                    # اگر Xray اجرا نشد، این کانفیگ خراب است!
                    logging.warning(f"\n[!!!] Found a bad config at line {i+1}. Xray failed to start.")
                    bad_configs.append(f"XRAY_CRASH: {uri}")
            # اگر with بدون خطا تمام شد، یعنی کانفیگ سالم است و Xray متوقف شده
        except Exception as e:
            logging.error(f"\n[!!!] An unexpected error occurred for URI at line {i+1}: {e}")
            bad_configs.append(f"EXCEPTION: {uri}")
            
    print("\n\n" + "="*50)
    print("Validation Complete.")
    
    if not bad_configs:
        print("[SUCCESS] All parsable configs were validated successfully by Xray!")
    else:
        print(f"[FAIL] Found {len(bad_configs)} problematic configs.")
        with open(BAD_CONFIGS_LOG_PATH, "w", encoding="utf-8") as f:
            f.write("\n".join(bad_configs))
        print(f"A list of them has been saved to '{BAD_CONFIGS_LOG_PATH}'.")
    print("="*50)


if __name__ == "__main__":
    main()
