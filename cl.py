import json
import requests
import re
import time
import sys
from pathlib import Path
from typing import Optional, List

# --- Library Imports ---
from python_v2ray.downloader import BinaryDownloader, OWN_REPO
from python_v2ray.config_parser import load_configs, deduplicate_configs, parse_uri, ConfigParams, XrayConfigBuilder
from python_v2ray.core import XrayCore # <-- کامپوننت کلیدی برای راه حل نهایی
from python_v2ray.tester import ConnectionTester

# --- Project Constants & Flags ---
PROJECT_ROOT = Path(__file__).parent
VENDOR_PATH = PROJECT_ROOT / "vendor"
CORE_ENGINE_PATH = PROJECT_ROOT / "core_engine"
CONFIG_FILE_PATH = "config.json"
INPUT_CONFIGS_PATH = "normal.txt"
FINAL_CONFIGS_PATH = "final.txt"

CHECK_LOC = True
CHECK_IRAN = True
LOCATION_CHECK_PORT = 20808

CHECK_HOST_IRANIAN_NODES = [
    "ir1.node.check-host.net", "ir2.node.check-host.net", "ir3.node.check-host.net"
]


def get_public_ipv4(proxies: dict) -> Optional[str]:
    """Fetches the public IPv4 address using a given proxy."""
    urls = ["https://api.ipify.org", "https://icanhazip.com"]
    for url in urls:
        try:
            response = requests.get(url, timeout=10, proxies=proxies)
            response.raise_for_status()
            ip = response.text.strip()
            if re.match(r"^\d{1,3}(\.\d{1,3}){3}$", ip):
                print(f"  Successfully fetched exit IP: {ip}")
                return ip
        except requests.RequestException:
            continue
    print("  Error: Failed to fetch public IPv4.")
    return None

def fetch_country_code(proxies: dict) -> str:
    """Fetches the exit country code using the provided proxy."""
    try:
        response = requests.get("https://ipinfo.io/json", timeout=10, proxies=proxies)
        response.raise_for_status()
        country = response.json().get('country', 'XX')
        print(f"  Successfully fetched country code: {country}")
        return country
    except Exception:
        print("  Warning: Could not fetch country code.")
        return "XX"

def get_ip_details_and_retag(original_config_str: str, country_code: str) -> str:
    """Rewrites the tag of a config string to include the country code."""
    config_stripped = original_config_str.strip()
    parts = config_stripped.split("#", 1)
    config_base = parts[0]

    parsed = parse_uri(original_config_str)
    if not parsed: return original_config_str

    base_name = parsed.display_tag.split("::")[0].strip()
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
        with open(CONFIG_FILE_PATH, "r") as f: settings = json.load(f)
    except Exception as e:
        print(f"Error loading config.json: {e}"); return

    print("\n--- Steps 1 & 2: Loading & Pre-processing Configurations ---")
    configs_with_uris = []
    for uri in Path(INPUT_CONFIGS_PATH).read_text().splitlines():
        if p := parse_uri(uri):
            configs_with_uris.append({'params': p, 'original_uri': uri})

    unique_items_dict = {
        (item['params'].protocol, item['params'].address, item['params'].port): item
        for item in configs_with_uris
    }
    unique_items = list(unique_items_dict.values())

    configs_to_test = [item['params'] for item in unique_items]

    seen_tags = set()
    for config in configs_to_test:
        original_tag = config.tag
        count = 1
        new_tag = original_tag
        while new_tag in seen_tags:
            new_tag = f"{original_tag}_{count}"
            count += 1
        config.tag = new_tag
        seen_tags.add(new_tag)

    print(f"Found {len(configs_to_test)} unique configurations.")

    print("\n--- Step 3: Ensuring Go Test Engine is Ready ---")
    try:
        CORE_ENGINE_PATH.mkdir(exist_ok=True)
        downloader = BinaryDownloader(PROJECT_ROOT)
        if not downloader.ensure_binary("core_engine", CORE_ENGINE_PATH, OWN_REPO):
            raise RuntimeError("Failed to download the core testing engine.")

        generic_name, expected_name = "core_engine", ""
        if sys.platform == "win32": expected_name = "core_engine.exe"
        elif sys.platform == "darwin": expected_name = "core_engine_macos"
        else: expected_name = "core_engine_linux"

        if (CORE_ENGINE_PATH / generic_name).is_file():
            (CORE_ENGINE_PATH / generic_name).rename(CORE_ENGINE_PATH / expected_name)

        print("Go testing engine is ready for use.")
    except Exception as e:
        print(f"Fatal Error during binary check: {e}"); return

    print("\n--- Step 4: Initial Connectivity (Ping) Test ---")
    tester = ConnectionTester(vendor_path=str(VENDOR_PATH), core_engine_path=str(CORE_ENGINE_PATH))
    results = tester.test_uris(parsed_params=configs_to_test, timeout=20)

    successful_tags = {r['tag'] for r in results if r.get('status') == 'success'}
    successful_items = [item for item in unique_items if item['params'].tag in successful_tags]

    print(f"Initial test found {len(successful_items)} working configurations.")
    if not successful_items:
        Path(FINAL_CONFIGS_PATH).write_text(""); return

    print("\n--- Step 5: Location & Iran Accessibility Check ---")
    final_uris_to_write = []

    for i, item in enumerate(successful_items):
        config_param = item['params']
        original_uri = item['original_uri']
        print(f"\nProcessing config {i+1}/{len(successful_items)}: {config_param.tag}")

        builder = XrayConfigBuilder()
        builder.add_inbound({
            "port": LOCATION_CHECK_PORT, "listen": "127.0.0.1", "protocol": "socks",
            "settings": {"auth": "noauth", "udp": True}, "tag": "socks_in"
        })
        outbound = builder.build_outbound_from_params(config_param)
        builder.add_outbound(outbound)
        builder.config["routing"]["rules"].append({
            "type": "field", "inboundTag": ["socks_in"], "outboundTag": outbound["tag"]
        })

        try:
            with XrayCore(vendor_dir=str(VENDOR_PATH), config_builder=builder) as xray:
                if not xray.is_running():
                    print("  Error: Could not start temporary proxy. Skipping.")
                    continue

                print(f"  Temporary proxy is running on port {LOCATION_CHECK_PORT}...")
                time.sleep(2)

                proxies = {"http": f"socks5h://127.0.0.1:{LOCATION_CHECK_PORT}", "https": f"socks5h://127.0.0.1:{LOCATION_CHECK_PORT}"}

                if CHECK_LOC:
                    country_code = fetch_country_code(proxies)
                    exit_ip = get_public_ipv4(proxies)

                    if CHECK_IRAN and is_ip_accessible_from_iran(exit_ip, proxies):
                        print("  Config is filtered in Iran. Discarding.")
                        continue

                    retagged_uri = get_ip_details_and_retag(original_uri, country_code)
                    final_uris_to_write.append(retagged_uri)
                    print("  Config passed. Retagged and added to list.")

                elif CHECK_IRAN:
                    exit_ip = get_public_ipv4(proxies)
                    if is_ip_accessible_from_iran(exit_ip, proxies):
                        print("  Config is filtered in Iran. Discarding.")
                        continue
                    final_uris_to_write.append(original_uri)
                    print("  Config passed Iran check. Added to list.")

                else:
                    final_uris_to_write.append(original_uri)
                    print("  No checks required. Added to list.")

        except Exception as e:
            print(f"  An error occurred during location check for {config_param.tag}: {e}")

    print(f"\n--- Step 6: Writing {len(final_uris_to_write)} Final Configurations ---")
    Path(FINAL_CONFIGS_PATH).write_text("\n".join(final_uris_to_write) + "\n")
    print("\n--- Script finished successfully! ---")

if __name__ == "__main__":
    main()
