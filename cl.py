import json
import requests
import re
import time
import sys
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed

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
MAX_WORKERS = 10

CHECK_LOC = True
CHECK_IRAN = True

CHECK_HOST_IRANIAN_NODES = [
    "ir1.node.check-host.net",
    "ir2.node.check-host.net",
    "ir3.node.check-host.net",
]


def get_public_ipv4(proxies: dict) -> Optional[str]:
    urls = ["https://api.ipify.org", "https://icanhazip.com"]
    for url in urls:
        try:
            r = requests.get(url, timeout=10, proxies=proxies); r.raise_for_status()
            ip = r.text.strip()
            if re.match(r"^\d{1,3}(\.\d{1,3}){3}$", ip):
                logging.info(f"  Successfully fetched exit IP: {ip}")
                return ip
        except requests.RequestException: continue
    logging.error("  Failed to fetch public IPv4.")
    return None

def fetch_country_code(proxies: dict) -> str:
    try:
        r = requests.get("https://ipinfo.io/json", timeout=10, proxies=proxies); r.raise_for_status()
        country = r.json().get('country', 'XX')
        logging.info(f"  Successfully fetched country code: {country}")
        return country
    except Exception:
        logging.warning("  Could not fetch country code.")
        return "XX"

def get_ip_details_and_retag(original_uri: str, country_code: str) -> str:
    parts = original_uri.strip().split("#", 1)
    base = parts[0]
    p = parse_uri(original_uri)
    if not p: return original_uri
    base_name = p.display_tag.split("::")[0].strip()
    return f"{base}#{base_name}::{country_code}"

def is_ip_accessible_from_iran(ip_to_check: str, proxies_to_use: dict) -> bool:
    """
    Checks if an IP is accessible from Iran using the original polling logic.
    Returns True if INACCESSIBLE (filtered), False if ACCESSIBLE.
    """
    if not ip_to_check: return True

    logging.info(f"CHECK-HOST: Checking Iran PING for target IP {ip_to_check}")
    accessible_from_at_least_one_node = False

    for node in CHECK_HOST_IRANIAN_NODES:
        try:
            init_url = f"https://check-host.net/check-ping?host={ip_to_check}&node={node}&max_nodes=1"
            response_init = requests.get(init_url, headers={"Accept": "application/json"}, timeout=10, proxies=proxies_to_use)
            if response_init.status_code != 200: continue

            init_data = response_init.json()
            if init_data.get("ok") != 1: continue

            request_id = init_data.get("request_id")
            if not request_id: continue

            time.sleep(10)
            result_url = f"https://check-host.net/check-result/{request_id}"
            response_result = requests.get(result_url, headers={"Accept": "application/json"}, timeout=10, proxies=proxies_to_use)
            if response_result.status_code != 200: continue

            result_data = response_result.json()
            if result_data and result_data.get(node) and "ms" in str(result_data[node][0]):
                logging.info(f"  CHECK-HOST OK: Target IP {ip_to_check} ACCESSIBLE from Iran via {node}.")
                accessible_from_at_least_one_node = True
                break

        except Exception as e:
            logging.warning(f"  CHECK-HOST: An error occurred while checking node {node}: {e}")
            continue

    if accessible_from_at_least_one_node:
        return False
    else:
        logging.warning(f"  CHECK-HOST Filtered: Target IP {ip_to_check} INACCESSIBLE from all Iranian nodes.")
        return True
def check_one_proxy(item: dict, test_url: str) -> Optional[str]:
    config_param = item['params']
    original_uri = item['original_uri']
    local_port = item['local_port']

    logging.info(f"--> Starting check for: {config_param.display_tag} on port {local_port}")
    proxies = {"http": f"socks5h://127.0.0.1:{local_port}", "https": f"socks5h://127.0.0.1:{local_port}"}

    try:
        start_time = time.time()
        response = requests.get(test_url, proxies=proxies, timeout=20)
        response.raise_for_status()
        ping_ms = int((time.time() - start_time) * 1000)
        logging.info(f"  [SUCCESS] Ping: {ping_ms} ms for {config_param.display_tag}.")

        if CHECK_LOC:
            country_code = fetch_country_code(proxies)
            exit_ip = get_public_ipv4(proxies)
            if CHECK_IRAN and is_ip_accessible_from_iran(exit_ip, proxies):
                return None
            final_uri = get_ip_details_and_retag(original_uri, country_code)
            logging.info(f"  [ADDED] {config_param.display_tag} passed all checks.")
            return final_uri
        elif CHECK_IRAN:
            exit_ip = get_public_ipv4(proxies)
            if is_ip_accessible_from_iran(exit_ip, proxies):
                return None
            return original_uri
        else:
            return original_uri
    except Exception as e:
        logging.error(f"  [FAIL] Test failed for {config_param.display_tag}. Reason: {str(e)[:120]}")
        return None

def main():
    print("--- Starting Fully Concurrent Refactored Script ---")

    try:
        with open(CONFIG_FILE_PATH, "r") as f: settings = json.load(f)
        test_url = settings.get("core", {}).get("test_url", "http://connectivitycheck.gstatic.com/generate_204")
    except Exception as e:
        print(f"Error loading config.json: {e}"); return

    all_uris = Path(INPUT_CONFIGS_PATH).read_text().splitlines()
    configs_with_uris = []
    for uri in all_uris:
        p = parse_uri(uri)
        if p:
            configs_with_uris.append({'params': p, 'original_uri': uri})

    unique_items = list({(item['params'].protocol, item['params'].address, item['params'].port): item for item in configs_with_uris}.values())
    print(f"Found {len(unique_items)} unique configurations.")

    try:
        downloader = BinaryDownloader(PROJECT_ROOT)
        downloader.ensure_all()
        print("Binaries are ready.")
    except Exception as e:
        print(f"Fatal Error during binary check: {e}"); return

    builder = XrayConfigBuilder()
    base_port = 20800
    for i, item in enumerate(unique_items):
        config_param = item['params']
        local_port = base_port + i
        item['local_port'] = local_port

        builder.add_inbound({"port": local_port, "listen": "127.0.0.1", "protocol": "socks", "tag": f"inbound-{local_port}"})
        outbound = builder.build_outbound_from_params(config_param)
        builder.add_outbound(outbound)
        builder.config["routing"]["rules"].append({"type": "field", "inboundTag": [f"inbound-{local_port}"], "outboundTag": outbound.get("tag", "proxy")})

    final_uris_to_write = []
    try:
        with XrayCore(vendor_path=str(VENDOR_PATH), config_builder=builder, debug_mode=True) as xray:
            if not xray.is_running():
                raise RuntimeError("Xray failed to start with the merged config.")

            print(f"\n--- Xray is running with {len(unique_items)} proxies. Waiting for ports to open... ---")
            time.sleep(5)

            print("\n--- Starting all checks concurrently ---")
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                future_to_item = {executor.submit(check_one_proxy, item, test_url): item for item in unique_items}
                for future in as_completed(future_to_item):
                    result_uri = future.result()
                    if result_uri:
                        final_uris_to_write.append(result_uri)

    except Exception as e:
        logging.critical(f"A critical error occurred: {e}")

    print(f"\n--- Step 6: Writing {len(final_uris_to_write)} Final Configurations ---")
    Path(FINAL_CONFIGS_PATH).write_text("\n".join(final_uris_to_write) + "\n")
    print("\n--- Script finished successfully! ---")

if __name__ == "__main__":
    main()
