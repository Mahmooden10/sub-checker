import json
import requests
import re
import time
import sys
import logging
import subprocess
from pathlib import Path
from typing import Optional, List

# --- Library Imports ---
# ما هنوز از اینها برای آماده‌سازی کانفیگ استفاده می‌کنیم
from python_v2ray.downloader import BinaryDownloader, OWN_REPO
from python_v2ray.config_parser import load_configs, deduplicate_configs, parse_uri, ConfigParams, XrayConfigBuilder

# --- لاگینگ رو خاموش می‌کنیم تا خروجی Xray تمیز دیده بشه ---
logging.basicConfig(level=logging.CRITICAL)

# --- ثابت‌ها ---
PROJECT_ROOT = Path(__file__).parent
VENDOR_PATH = PROJECT_ROOT / "vendor"
CORE_ENGINE_PATH = PROJECT_ROOT / "core_engine"
INPUT_CONFIGS_PATH = "normal.txt"
DEBUG_CONFIG_PATH = PROJECT_ROOT / "debug_xray_config.json" # یک فایل برای دیباگ کانفیگ

def main():
    print("--- ULTIMATE DEBUG MODE: Bypassing the tester to run Xray directly ---")

    # --- مرحله ۱: آماده‌سازی کانفیگ‌ها مثل قبل ---
    print("\n--- Step 1: Loading & Pre-processing Configurations ---")
    all_configs = load_configs(source=INPUT_CONFIGS_PATH)
    if not all_configs:
        print("No valid configs found."); sys.exit(1)
    
    configs_to_test = deduplicate_configs(all_configs)
    
    # اطمینان از منحصر به فرد بودن تگ‌ها
    seen_tags = set()
    for config in configs_to_test:
        original_tag, count, new_tag = config.tag, 1, config.tag
        while new_tag in seen_tags:
            new_tag = f"{original_tag}_{count}"; count += 1
        config.tag = new_tag; seen_tags.add(new_tag)
        
    print(f"Found {len(configs_to_test)} unique configurations to test.")

    # --- مرحله ۲: ساختن کانفیگ ترکیبی به صورت دستی ---
    print("\n--- Step 2: Manually creating the merged Xray config ---")
    builder = XrayConfigBuilder()
    base_port = 20800
    
    for i, params in enumerate(configs_to_test):
        local_port = base_port + i
        inbound_tag = f"inbound-{local_port}"
        
        # اضافه کردن اینباند برای هر کانفیگ
        builder.add_inbound({
            "tag": inbound_tag,
            "port": local_port,
            "listen": "127.0.0.1",
            "protocol": "socks",
            "settings": {"auth": "noauth", "udp": True}
        })
        
        # ساختن و اضافه کردن اوت‌باند
        outbound = builder.build_outbound_from_params(params)
        if not outbound:
            print(f"Warning: Could not build outbound for tag '{params.tag}'. Skipping.")
            continue
            
        builder.add_outbound(outbound)
        
        # اضافه کردن قانون روتینگ
        builder.config["routing"]["rules"].append({
            "type": "field",
            "inboundTag": [inbound_tag],
            "outboundTag": outbound["tag"]
        })

    # ذخیره کردن کانفیگ نهایی در یک فایل برای بررسی
    config_json = builder.to_json()
    DEBUG_CONFIG_PATH.write_text(config_json)
    print(f"Merged config saved to: {DEBUG_CONFIG_PATH}")
    
    # چاپ بخش outbounds برای بررسی سریع
    print("\n--- DEBUG: Generated Outbounds Section ---")
    print(json.dumps(builder.config.get('outbounds', []), indent=2))
    print("-" * 40)

    # --- مرحله ۳: اجرای مستقیم Xray و گرفتن خروجی خطا ---
    print("\n--- Step 3: Running Xray directly and capturing all output ---")
    
    xray_executable = VENDOR_PATH / "xray_linux"
    command = [str(xray_executable), "-c", str(DEBUG_CONFIG_PATH)]
    
    print(f"Executing command: {' '.join(command)}")
    
    # Xray را اجرا کرده و منتظر می‌مانیم تا تمام شود یا تایم‌اوت شود
    # تمام خروجی استاندارد و خروجی خطا را ضبط می‌کنیم
    process = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=15 # 15 ثانیه صبر می‌کنیم
    )

    # --- مرحله ۴: نمایش نتایج دیباگ ---
    print("\n--- XRAY EXECUTION RESULT ---")
    print(f"Return Code: {process.returncode}")
    
    print("\n--- XRAY STDOUT (Standard Output) ---")
    print(process.stdout if process.stdout else "[EMPTY]")
    print("-" * 40)
    
    print("\n--- XRAY STDERR (THE REAL ERROR IS LIKELY HERE!) ---")
    print(process.stderr if process.stderr else "[EMPTY]")
    print("-" * 40)

    if process.returncode != 0:
        print("\nCONCLUSION: Xray crashed! The error message is in the STDERR section above.")
    else:
        print("\nCONCLUSION: Xray ran without crashing, but something else is wrong.")

    sys.exit(1) # اسکریپت را متوقف می‌کنیم چون این فقط یک تست دیباگ بود

if __name__ == "__main__":
    main()
