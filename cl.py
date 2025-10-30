import json
import requests
import re
import time
import sys
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any

# --- Library Imports ---
from python_v2ray.downloader import BinaryDownloader, OWN_REPO
from python_v2ray.config_parser import load_configs, deduplicate_configs, parse_uri, ConfigParams, XrayConfigBuilder
from python_v2ray.core import XrayCore
from python_v2ray.tester import ConnectionTester

logging.basicConfig(level=logging.INFO, format='%(asctime)s - [%(levelname)s] - %(message)s')

# --- Project Constants & Flags ---
PROJECT_ROOT = Path(__file__).parent
VENDOR_PATH = PROJECT_ROOT / "vendor"
CORE_ENGINE_PATH = PROJECT_ROOT / "core_engine"
CONFIG_FILE_PATH = "config.json"
INPUT_CONFIGS_PATH = "normal.txt"
FINAL_CONFIGS_PATH = "final.txt"

CHECK_LOC = True
CHECK_IRAN = True
SINGLE_TEST_PORT = 20808 # یک پورت ثابت برای تمام تست‌های تکی

# --- Helper Functions (بدون تغییر) ---
def get_public_ipv4(proxies: dict) -> Optional[str]:
    urls = ["https://api.ipify.org", "https://icanhazip.com"]
    for url in urls:
        try:
            r = requests.get(url, timeout=10, proxies=proxies); r.raise_for_status()
            ip = r.text.strip()
            if re.match(r"^\d{1,3}(\.\d{1,3}){3}$", ip):
                print(f"  Successfully fetched exit IP: {ip}"); return ip
        except requests.RequestException: continue
    print("  Error: Failed to fetch public IPv4."); return None

def fetch_country_code(proxies: dict) -> str:
    try:
        r = requests.get("https://ipinfo.io/json", timeout=10, proxies=proxies); r.raise_for_status()
        country = r.json().get('country', 'XX')
        print(f"  Successfully fetched country code: {country}"); return country
    except Exception:
        print("  Warning: Could not fetch country code."); return "XX"

def get_ip_details_and_retag(original_uri: str, country_code: str) -> str:
    parts = original_uri.strip().split("#", 1)
    base = parts[0]
    p = parse_uri(original_uri)
    if not p: return original_uri
    base_name = p.display_tag.split("::")[0].strip()
    return f"{base}#{base_name}::{country_code}"

def is_ip_accessible_from_iran(ip: str, proxies: dict) -> bool:
    if not ip: return True
    print(f"  CHECK-HOST: Checking accessibility of {ip} from Iran...")
    try:
        url = f"https://check-host.net/check-ping?host={ip}&node=ir1.node.check-host.net,ir2.node.check-host.net,ir3.node.check-host.net"
        init = requests.get(url, headers={"Accept": "application/json"}, timeout=15, proxies=proxies).json()
        req_id = init.get("request_id")
        if not req_id: return False
        time.sleep(10)
        res_url = f"https://check-host.net/check-result/{req_id}"
        results = requests.get(res_url, headers={"Accept": "application/json"}, timeout=15, proxies=proxies).json()
        for node in ["ir1.node.check-host.net", "ir2.node.check-host.net", "ir3.node.check-host.net"]:
            if results.get(node) and "ms" in str(results[node][0]):
                print(f"  CHECK-HOST OK: {ip} is ACCESSIBLE from {node}."); return False
        print(f"  CHECK-HOST Filtered: {ip} is INACCESSIBLE."); return True
    except Exception as e:
        print(f"  CHECK-HOST Warning: Service failed ({e}). Assuming accessible."); return False

# ----- Patched Builder to fix library bugs -----
class PatchedXrayConfigBuilder(XrayConfigBuilder):
    def _build_protocol_settings(self, params: ConfigParams) -> Dict[str, Any]:
        settings = super()._build_protocol_settings(params)
        if not settings and params.protocol == "socks":
            print("  [PATCH] Applying fix for 'socks' protocol...")
            return {"servers": [{"address": params.address, "port": params.port}]}
        return settings
    
    def _build_stream_settings(self, params: ConfigParams, **kwargs) -> Dict[str, Any]:
        stream_settings = super()._build_stream_settings(params, **kwargs)
        if params.network == "grpc" and not stream_settings.get("grpcSettings", {}).get("serviceName"):
            print(f"  [PATCH] Removing invalid grpcSettings for '{params.tag}' to prevent crash.")
            if "grpcSettings" in stream_settings:
                del stream_settings["grpcSettings"]
        return stream_settings

def main():
    print("--- Starting Refactored Script (Single-Test Mode) ---")
    
    try:
        with open(CONFIG_FILE_PATH, "r") as f: settings = json.load(f)
        test_url = settings.get("core", {}).get("test_url", "http://connectivitycheck.gstatic.com/generate_204")
    except Exception as e:
        print(f"Error loading config.json: {e}"); return

    print("\n--- Steps 1 & 2: Loading & Pre-processing Configurations ---")
    configs_with_uris = [{'params': p, 'original_uri': uri} for uri in Path(INPUT_CONFIGS_PATH).read_text().splitlines() if (p := parse_uri(uri))]
    unique_items = list({(item['params'].protocol, item['params'].address, item['params'].port): item for item in configs_with_uris}.values())
    print(f"Found {len(unique_items)} unique configurations.")

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

    print("\n--- Step 4 & 5: Single-threaded Testing and Filtering ---")
    final_uris_to_write = []
    
    for i, item in enumerate(unique_items):
        config_param, original_uri = item['params'], item['original_uri']
        print(f"\n[{i+1}/{len(unique_items)}] Testing: {config_param.display_tag}")

        builder = PatchedXrayConfigBuilder()
        builder.add_inbound({"port": SINGLE_TEST_PORT, "listen": "127.0.0.1", "protocol": "socks", "tag": "socks_in"})
        outbound = builder.build_outbound_from_params(config_param)
        builder.add_outbound(outbound)
        builder.config["routing"]["rules"].append({"type": "field", "inboundTag": ["socks_in"], "outboundTag": outbound.get("tag", "proxy")})

        try:
            with XrayCore(vendor_path=str(VENDOR_PATH), config_builder=builder) as xray:
                if not xray.is_running():
                    print("  [FAIL] Xray process failed to start. Likely a config build issue."); continue
                
                print(f"  Proxy is running on port {SINGLE_TEST_PORT}. Pinging...")
                time.sleep(2)
                
                proxies = {"http": f"socks5h://127.0.0.1:{SINGLE_TEST_PORT}", "https": f"socks5h://127.0.0.1:{SINGLE_TEST_PORT}"}
                start_time = time.time()
                response = requests.get(test_url, proxies=proxies, timeout=15)
                response.raise_for_status()
                ping_ms = int((time.time() - start_time) * 1000)
                print(f"  [SUCCESS] Ping: {ping_ms} ms. Proceeding to location checks...")

                if CHECK_LOC:
                    country_code = fetch_country_code(proxies)
                    exit_ip = get_public_ipv4(proxies)
                    if CHECK_IRAN and is_ip_accessible_from_iran(exit_ip, proxies):
                        print("  [FILTERED] Config is blocked in Iran."); continue
                    retagged_uri = get_ip_details_and_retag(original_uri, country_code)
                    final_uris_to_write.append(retagged_uri)
                    print("  [ADDED] Config passed all checks and was retagged.")
                elif CHECK_IRAN:
                    exit_ip = get_public_ipv4(proxies)
                    if is_ip_accessible_from_iran(exit_ip, proxies):
                        print("  [FILTERED] Config is blocked in Iran."); continue
                    final_uris_to_write.append(original_uri)
                    print("  [ADDED] Config passed Iran check.")
                else:
                    final_uris_to_write.append(original_uri)
                    print("  [ADDED] No extra checks required.")

        except Exception as e:
            print(f"  [FAIL] Test failed for this config. Reason: {e}")

    print(f"\n--- Step 6: Writing {len(final_uris_to_write)} Final Configurations ---")
    Path(FINAL_CONFIGS_PATH).write_text("\n".join(final_uris_to_write) + "\n")
    print("\n--- Script finished successfully! ---")

if __name__ == "__main__":
    main()
