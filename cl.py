import json
import requests
import re
import time
from pathlib import Path
from typing import Optional, List

from python_v2ray.downloader import BinaryDownloader, OWN_REPO
from python_v2ray.config_parser import load_configs, deduplicate_configs, ConfigParams, parse_uri
from python_v2ray.tester import ConnectionTester

PROJECT_ROOT = Path(__file__).parent
VENDOR_PATH = PROJECT_ROOT / "vendor"
CORE_ENGINE_PATH = PROJECT_ROOT / "core_engine"
CONFIG_FILE_PATH = PROJECT_ROOT / "config.json"
INPUT_CONFIGS_PATH = PROJECT_ROOT / "normal.txt"
FINAL_CONFIGS_PATH = PROJECT_ROOT / "final.txt"

CHECK_LOC = True
CHECK_IRAN = True

# Iranian nodes from check-host.net for filtering
CHECK_HOST_IRANIAN_NODES = [
    "ir1.node.check-host.net",
    "ir2.node.check-host.net",
    "ir3.node.check-host.net",
]


def get_public_ipv4(proxies: dict) -> Optional[str]:
    """Fetches the public IPv4 address using a given proxy."""
    urls = ["https://api.ipify.org", "https://icanhazip.com"]
    headers = {"Connection": "close", "User-Agent": "Mozilla/5.0"}
    for url in urls:
        try:
            response = requests.get(url, timeout=10, proxies=proxies, headers=headers)
            response.raise_for_status()
            ip = response.text.strip()
            if re.match(r"^\d{1,3}(\.\d{1,3}){3}$", ip):
                print(f"  Successfully fetched exit IP: {ip} from {url}")
                return ip
        except requests.RequestException:
            continue
    print("  Error: Failed to fetch public IPv4 from all services.")
    return None

def fetch_country_code(proxies: dict) -> str:
    """Fetches the exit country code using the provided proxy."""
    try:
        api_url = "https://ipinfo.io/json"
        response = requests.get(api_url, timeout=10, proxies=proxies)
        response.raise_for_status()
        data = response.json()
        country = data.get('country', 'XX')
        print(f"  Successfully fetched country code: {country}")
        return country
    except Exception as e:
        print(f"  Warning: Could not fetch country code. Reason: {e}")
        return "XX"

def get_ip_details_and_retag(original_config_str: str, country_code: str) -> str:
    """Rewrites the tag of a config string to include the country code."""
    config_stripped = original_config_str.strip()
    parts = config_stripped.split("#", 1)
    config_base = parts[0]


    parsed = parse_uri(original_config_str)
    if not parsed:
        return original_config_str

    original_tag = parsed.display_tag
    base_name = original_tag.split("::")[0].strip()

    new_tag = f"{base_name}::{country_code}"

    return f"{config_base}#{new_tag}"

def is_ip_accessible_from_iran(ip_to_check: str, proxies: dict) -> bool:
    """Checks if an IP is accessible from Iran. Returns False if accessible, True if filtered."""
    if not ip_to_check: return True
    print(f"  CHECK-HOST: Checking accessibility of {ip_to_check} from Iran...")
    try:
        nodes_param = ",".join(CHECK_HOST_IRANIAN_NODES)
        init_url = f"https://check-host.net/check-ping?host={ip_to_check}&node={nodes_param}"
        response_init = requests.get(init_url, headers={"Accept": "application/json"}, timeout=10, proxies=proxies).json()
        request_id = response_init.get("request_id")
        if not request_id: return True

        time.sleep(10)
        result_url = f"https://check-host.net/check-result/{request_id}"
        result_data = requests.get(result_url, headers={"Accept": "application/json"}, timeout=10, proxies=proxies).json()

        for node in CHECK_HOST_IRANIAN_NODES:
            if result_data.get(node) and "ms" in str(result_data[node][0]):
                print(f"  CHECK-HOST OK: {ip_to_check} is ACCESSIBLE from {node}.")
                return False

        print(f"  CHECK-HOST Filtered: {ip_to_check} is INACCESSIBLE.")
        return True
    except Exception:
        return True


def main():
    print("--- Starting Refactored Script ---")

    try:
        with open(CONFIG_FILE_PATH, "r") as f:
            settings = json.load(f)
        test_url = settings.get("core", {}).get("test_url", "http://www.google.com/generate_204")
    except Exception as e:
        print(f"Error loading config.json: {e}")
        return

    print("\n--- Steps 1 & 2: Loading, Parsing, and Deduplicating ---")
    all_configs = load_configs(source=INPUT_CONFIGS_PATH)
    if not all_configs: return
    unique_configs = deduplicate_configs(all_configs)
    print(f"Found {len(unique_configs)} unique configurations.")

    print("\n--- Step 3: Ensuring Go Test Engine is Ready ---")
    try:
        downloader = BinaryDownloader(PROJECT_ROOT)
        if not downloader.ensure_binary("core_engine", CORE_ENGINE_PATH, OWN_REPO):
            raise RuntimeError("Failed to download the core testing engine.")
    except Exception as e:
        print(f"Fatal Error: {e}")
        return

    print("\n--- Step 4: Initial Connectivity (Ping) Test ---")
    tester = ConnectionTester(vendor_path=str(VENDOR_PATH), core_engine_path=str(CORE_ENGINE_PATH))
    results = tester.test_uris(parsed_params=unique_configs, timeout=20, ping_url=test_url)

    successful_params = [
        config for config in unique_configs
        if any(r.get('status') == 'success' and r.get('tag') == config.tag for r in results)
    ]
    print(f"Initial test found {len(successful_params)} working configurations.")
    if not successful_params: return

    print("\n--- Step 5: Location & Iran Accessibility Check ---")
    final_uris_to_write = []

    original_uris_map = {p.tag: uri for uri in INPUT_CONFIGS_PATH.read_text().splitlines() if (p := parse_uri(uri))}

    for i, config in enumerate(successful_params):
        print(f"\nProcessing config {i+1}/{len(successful_params)}: {config.tag}")
        original_uri = original_uris_map.get(config.tag)
        if not original_uri:
            print(f"  Warning: Could not find original URI for tag '{config.tag}'. Skipping.")
            continue

        session_results = tester.test_uris(parsed_params=[config], timeout=10, ping_url=test_url)
        if not session_results or session_results[0].get('status') != 'success':
            print(f"  Config failed the second check. Skipping.")
            continue

        local_port = session_results[0].get('local_port')
        proxies = {"http": f"socks5h://127.0.0.1:{local_port}", "https": f"socks5h://127.0.0.1:{local_port}"}

        if CHECK_LOC:
            country_code = fetch_country_code(proxies)
            exit_ip = get_public_ipv4(proxies)

            if CHECK_IRAN:
                if is_ip_accessible_from_iran(exit_ip, proxies):
                    print("  Config is filtered in Iran. Discarding.")
                    continue

            retagged_uri = get_ip_details_and_retag(original_uri, country_code)
            final_uris_to_write.append(retagged_uri)
            print(f"  Config passed. Retagged and added to list.")

        elif CHECK_IRAN:
            exit_ip = get_public_ipv4(proxies)
            if is_ip_accessible_from_iran(exit_ip, proxies):
                print("  Config is filtered in Iran. Discarding.")
                continue
            final_uris_to_write.append(original_uri)
            print(f"  Config passed Iran check. Added to list.")

        else:
            final_uris_to_write.append(original_uri)
            print(f"  No checks required. Added to list.")

    print(f"\n--- Step 6: Writing {len(final_uris_to_write)} Final Configurations ---")
    FINAL_CONFIGS_PATH.write_text("\n".join(final_uris_to_write) + "\n")
    print("\n--- Script finished successfully! ---")

if __name__ == "__main__":
    main()
