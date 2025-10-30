import json
import requests
import re
import time
import sys
import logging
from pathlib import Path
from typing import Optional, List

from python_v2ray.downloader import BinaryDownloader, OWN_REPO
from python_v2ray.config_parser import load_configs, deduplicate_configs, parse_uri, ConfigParams, XrayConfigBuilder
from python_v2ray.core import XrayCore
from python_v2ray.tester import ConnectionTester

logging.basicConfig(level=logging.INFO, format='%(asctime)s - [%(levelname)s] - %(message)s')

PROJECT_ROOT = Path(__file__).parent
VENDOR_PATH = PROJECT_ROOT / "vendor"
CORE_ENGINE_PATH = PROJECT_ROOT / "core_engine"
CONFIG_FILE_PATH = "config.json"
INPUT_CONFIGS_PATH = "normal.txt"
FINAL_CONFIGS_PATH = "final.txt"

CHECK_LOC = True
CHECK_IRAN = True
LOCATION_CHECK_PORT = 20808

# ... (تمام توابع جانبی مثل قبل اینجا قرار می‌گیرند) ...
def get_public_ipv4(proxies: dict) -> Optional[str]:
    # ... implementation ...
    return "1.1.1.1" # Placeholder
def fetch_country_code(proxies: dict) -> str:
    # ... implementation ...
    return "XX"
def get_ip_details_and_retag(uri: str, code: str) -> str:
    # ... implementation ...
    return uri
def is_ip_accessible_from_iran(ip: str, proxies: dict) -> bool:
    # ... implementation ...
    return False


def main():
    print("--- Starting Refactored Script ---")

    try:
        with open(CONFIG_FILE_PATH, "r") as f: settings = json.load(f)
        test_url = settings.get("core", {}).get("test_url", "http://connectivitycheck.gstatic.com/generate_204")
    except Exception as e:
        print(f"Error loading config.json: {e}"); return

    print("\n--- Steps 1 & 2: Loading & Pre-processing Configurations ---")
    
    # ----- FIX IS HERE: Replaced walrus operator for Python < 3.8 compatibility -----
    configs_with_uris = []
    for uri in Path(INPUT_CONFIGS_PATH).read_text().splitlines():
        p = parse_uri(uri)
        if p:
            configs_with_uris.append({'params': p, 'original_uri': uri})
    # --------------------------------------------------------------------------

    if not configs_with_uris:
        print("No valid configs found after parsing. Please check input file and Python version compatibility.")
        Path(FINAL_CONFIGS_PATH).write_text(""); return

    unique_items = list({(item['params'].protocol, item['params'].address, item['params'].port): item for item in configs_with_uris}.values())
    configs_to_test = [item['params'] for item in unique_items]

    # ... (کد منحصر به فرد کردن تگ‌ها) ...
    seen_tags = set()
    for config in configs_to_test:
        original_tag, count, new_tag = config.tag, 1, config.tag
        while new_tag in seen_tags: new_tag = f"{original_tag}_{count}"; count += 1
        config.tag = new_tag; seen_tags.add(new_tag)
        
    print(f"Found {len(configs_to_test)} unique configurations.")

    print("\n--- Step 3: Ensuring Binaries are Ready ---")
    try:
        CORE_ENGINE_PATH.mkdir(exist_ok=True)
        downloader = BinaryDownloader(PROJECT_ROOT)
        if not downloader.ensure_binary("core_engine", CORE_ENGINE_PATH, OWN_REPO): raise RuntimeError("Failed to download core_engine.")
        generic_path = CORE_ENGINE_PATH / "core_engine"
        if sys.platform != "win32":
            expected_name = "core_engine_linux" if "linux" in sys.platform else "core_engine_macos"
            if generic_path.is_file(): generic_path.rename(CORE_ENGINE_PATH / expected_name)
        print("Binaries are ready.")
    except Exception as e:
        print(f"Fatal Error during binary check: {e}"); return

    print("\n--- Step 4: Initial Connectivity (Ping) Test ---")
    tester = ConnectionTester(vendor_path=str(VENDOR_PATH), core_engine_path=str(CORE_ENGINE_PATH))
    results = tester.test_uris(parsed_params=configs_to_test, timeout=20, ping_url=test_url)
    
    successful_tags = {r['tag'] for r in results if r.get('status') == 'success'}
    successful_items = [item for item in unique_items if item['params'].tag in successful_tags]
    
    print(f"Initial test found {len(successful_items)} working configurations.")
    if not successful_items:
        Path(FINAL_CONFIGS_PATH).write_text(""); return

    # ... (بقیه کد برای Step 5 و 6 مثل قبل است و نیازی به تغییر ندارد) ...
    # Placeholder for the rest of the logic
    print("\n--- Step 5 & 6 are skipped for this test run. ---")
    final_uris_to_write = [item['original_uri'] for item in successful_items]
    Path(FINAL_CONFIGS_PATH).write_text("\n".join(final_uris_to_write) + "\n")


    print("\n--- Script finished successfully! ---")

if __name__ == "__main__":
    main()
