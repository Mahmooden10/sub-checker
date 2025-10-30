import json
import requests
import re
import time
import sys
import logging
import socket
import os
import subprocess
from pathlib import Path
from typing import Optional, List, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed

from python_v2ray.downloader import BinaryDownloader
from python_v2ray.config_parser import parse_uri, XrayConfigBuilder
from python_v2ray.core import XrayCore

logging.basicConfig(level=logging.INFO, format='%(asctime)s - [%(levelname)s] - %(message)s')

PROJECT_ROOT = Path(__file__).parent.resolve()
VENDOR_PATH = PROJECT_ROOT / "vendor"
CORE_ENGINE_PATH = PROJECT_ROOT / "core_engine"
CONFIG_FILE_PATH = "config.json"
INPUT_CONFIGS_PATH = "normal.txt"
FINAL_CONFIGS_PATH = "final.txt"
MAX_WORKERS = 10

CHECK_LOC = True
CHECK_IRAN = True

CHECK_HOST_IRANIAN_NODES = [
    "ir1.node.check-host.net", "ir2.node.check-host.net", "ir3.node.check-host.net"
]


def get_public_ipv4(proxies: dict) -> Optional[str]:
    urls = ["https://api.ipify.org", "https://icanhazip.com"]
    for url in urls:
        try:
            r = requests.get(url, timeout=10, proxies=proxies); r.raise_for_status()
            ip = r.text.strip()
            if re.match(r"^\d{1,3}(\.\d{1,3}){3}$", ip):
                logging.info(f"  Successfully fetched exit IP: {ip}"); return ip
        except requests.RequestException: continue
    logging.error("  Failed to fetch public IPv4."); return None


def fetch_country_code(proxies: dict) -> str:
    try:
        r = requests.get("https://ipinfo.io/json", timeout=10, proxies=proxies); r.raise_for_status()
        country = r.json().get('country', 'XX')
        logging.info(f"  Successfully fetched country code: {country}"); return country
    except Exception:
        logging.warning("  Could not fetch country code."); return "XX"


def get_ip_details_and_retag(original_uri: str, country_code: str) -> str:
    parts = original_uri.strip().split("#", 1)
    base = parts[0]
    p = parse_uri(original_uri)
    if not p: return original_uri
    base_name = p.display_tag.split("::")[0].strip()
    return f"{base}#{base_name}::{country_code}"


def is_ip_accessible_from_iran_via_check_host(ip_to_check_on_checkhost: str,
proxies_to_use: Optional[dict],
timeout_seconds: int = 35) -> Optional[bool]:
    """
    Checks if an IP address is pingable from Iran using check-host.net.
    Returns:
        False: If accessible from Iran.
        True: If inaccessible from Iran.
        None: If the check-host.net service had an issue.
    """
    if not ip_to_check_on_checkhost:
        logging.warning(f"  CHECK-HOST: No target IP provided to check.")
        return True
    proxy_display = proxies_to_use.get('http', 'None') if proxies_to_use else 'None'
    logging.info(f"  CHECK-HOST (via proxy {proxy_display}): Checking Iran PING for target IP {ip_to_check_on_checkhost}")
    check_host_api_url_base = "https://check-host.net/check-ping"
    headers = {"Accept": "application/json", "User-Agent": "MyConfigTester/1.2"}
    accessible_from_at_least_one_node = False
    any_node_test_completed_without_service_error = False

    for node_idx, node in enumerate(CHECK_HOST_IRANIAN_NODES):
        if accessible_from_at_least_one_node:
            break
        try:
            init_url = f"{check_host_api_url_base}?host={ip_to_check_on_checkhost}&node={node}&max_nodes=1"
            response_init = requests.get(init_url, headers=headers, timeout=10, proxies=proxies_to_use)
            if response_init.status_code == 429:
                logging.warning(f"  CH_PROXY: Rate limited by check-host.net for {ip_to_check_on_checkhost} (node {node}).")
                return None
            response_init.raise_for_status()
            init_data = response_init.json()
            if init_data.get("ok") != 1:
                error_msg = init_data.get('error', 'Unknown error')
                logging.warning(f"  CH_PROXY: PING init API error for {ip_to_check_on_checkhost} (node {node}): {error_msg}")
                if "limit for your ip" in error_msg.lower() or "many requests" in error_msg.lower():
                    return None
                any_node_test_completed_without_service_error = True
                continue

            request_id = init_data.get("request_id")
            if not request_id:
                logging.warning(f"  CH_PROXY: No request_id for {ip_to_check_on_checkhost} (node {node}).")
                any_node_test_completed_without_service_error = True
                continue

            result_url = f"https://check-host.net/check-result/{request_id}"
            polling_deadline = time.time() + (timeout_seconds - 10)
            node_ping_to_target_successful = False
            while time.time() < polling_deadline:
                time.sleep(3)
                try:
                    response_result = requests.get(result_url, headers=headers, timeout=5, proxies=proxies_to_use)
                    if response_result.status_code == 429:
                        logging.warning(f"  CH_PROXY: Rate limited during polling for {request_id} (node {node}).")
                        return None
                    response_result.raise_for_status()
                    result_data = response_result.json()
                    if not result_data: continue

                    node_result = result_data.get(node)
                    if node_result:
                        any_node_test_completed_without_service_error = True
                        if isinstance(node_result, list) and len(node_result) > 0 and node_result[0]:
                            if isinstance(node_result[0], list) and len(node_result[0]) > 0 and node_result[0][0]:
                                ping_stats = node_result[0][0]
                                if isinstance(ping_stats, list) and len(ping_stats) >= 4:
                                    avg_rtt = ping_stats[3]
                                    if avg_rtt is not None and "ms" in avg_rtt:
                                        logging.info(f"  CH_PROXY: OK! Target {ip_to_check_on_checkhost} ACCESSIBLE from {node} (RTT: {avg_rtt}).")
                                        accessible_from_at_least_one_node = True
                                        node_ping_to_target_successful = True
                                        break
                        break
                except requests.RequestException as e_poll:
                    logging.error(f"  CH_PROXY: RequestException polling {request_id} ({node}): {e_poll}")
                    return None
            if node_ping_to_target_successful:
                break
        except requests.RequestException as e_init:
            logging.error(f"  CH_PROXY: RequestException initiating PING for {ip_to_check_on_checkhost} ({node}): {e_init}")
            if node_idx == 0: return None
        except Exception as e_init_other:
            logging.error(f"  CH_PROXY: Generic error initiating PING for {ip_to_check_on_checkhost} ({node}): {e_init_other}")
            if node_idx == 0: return None

    if accessible_from_at_least_one_node:
        return False
    if any_node_test_completed_without_service_error:
        logging.info(f"  CH_PROXY: OK! Target {ip_to_check_on_checkhost} INACCESSIBLE from Iran.")
        return True
    else:
        logging.warning(f"  CH_PROXY: Could not get a conclusive result for {ip_to_check_on_checkhost}.")
        return None


def check_one_proxy(item: dict, test_url: str) -> Optional[str]:
    config_param, original_uri, local_port = item['params'], item['original_uri'], item['local_port']
    logging.info(f"--> Starting check for: {config_param.display_tag} on port {local_port}")
    proxies = {"http": f"socks5h://127.0.0.1:{local_port}", "https": f"socks5h://127.0.0.1:{local_port}"}
    try:
        start_time = time.time()
        response = requests.get(test_url, proxies=proxies, timeout=20); response.raise_for_status()
        ping_ms = int((time.time() - start_time) * 1000)
        logging.info(f"  [SUCCESS] Ping: {ping_ms} ms for {config_param.display_tag}.")

        exit_ip = get_public_ipv4(proxies)

        if CHECK_IRAN:
            is_inaccessible_from_iran = is_ip_accessible_from_iran_via_check_host(exit_ip, proxies)
            if is_inaccessible_from_iran is False:  # False means it IS accessible
                logging.warning(f"  [FILTERED] {config_param.display_tag} is accessible from Iran and has been removed.")
                return None
            if is_inaccessible_from_iran is None: # Inconclusive check
                logging.warning(f"  [WARNING] Iran accessibility check for {config_param.display_tag} was inconclusive. Allowing it to pass.")

        if CHECK_LOC:
            country_code = fetch_country_code(proxies)
            final_uri = get_ip_details_and_retag(original_uri, country_code)
            logging.info(f"  [ADDED] {config_param.display_tag} passed all checks.")
            return final_uri
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
        if p: configs_with_uris.append({'params': p, 'original_uri': uri})
    unique_items = list({(item['params'].protocol, item['params'].address, item['params'].port, item['params'].id): item for item in configs_with_uris}.values())
    print(f"Found {len(unique_items)} unique configurations.")

    if not os.environ.get('CI'):
        print("--- Local environment detected. Checking/downloading binaries... ---")
        try:
            downloader = BinaryDownloader(PROJECT_ROOT)
            downloader.ensure_all()
            print("Binaries are ready.")
        except Exception as e:
            print(f"Fatal Error during binary check: {e}")
            return
    else:
        print("--- CI environment detected. Skipping automatic binary download. ---")

    builder = XrayConfigBuilder()
    base_port = 20800
    expected_ports = set()
    items_to_test = []
    for item in unique_items:
        if item['params'].protocol in ["vless", "vmess", "trojan", "ss", "socks", "mvless"]:
            local_port = base_port + len(items_to_test)
            unique_outbound_tag = f"proxy_out_{local_port}"
            outbound = builder.build_outbound_from_params(item['params'], explicit_tag=unique_outbound_tag)

            if outbound:
                item['local_port'] = local_port
                items_to_test.append(item)
                expected_ports.add(local_port)

                builder.add_inbound({"port": local_port, "listen": "127.0.0.1", "protocol": "socks", "tag": f"inbound-{local_port}"})
                builder.add_outbound(outbound)
                builder.config["routing"]["rules"].append({"type": "field", "inboundTag": [f"inbound-{local_port}"], "outboundTag": unique_outbound_tag})

    if not items_to_test:
        print("No Xray-compatible configurations found to test.")
        return

    final_uris_to_write = []
    try:
        with XrayCore(vendor_path=str(VENDOR_PATH), config_builder=builder, debug_mode=True) as xray:
            time.sleep(1)
            if not xray.is_running():
                raise RuntimeError("Xray process failed to start. The config might still have issues.")

            print(f"\n--- Xray is running with {len(items_to_test)} proxies. Waiting for ports... ---")

            ready_ports = set()
            for _ in range(40):
                if not xray.is_running():
                    print("\n--- XRAY CRASHED! ---")
                    break
                ports_to_check = expected_ports - ready_ports
                for port in ports_to_check:
                    try:
                        with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                            ready_ports.add(port)
                    except (socket.timeout, ConnectionRefusedError):
                        pass
                if ready_ports == expected_ports:
                    print(f"All {len(expected_ports)} ports are ready.")
                    break
                time.sleep(0.25)
            else:
                if xray.is_running():
                    raise RuntimeError(f"Timeout: Not all ports became ready. {len(ready_ports)}/{len(expected_ports)} ready.")
                else:
                    raise RuntimeError("Xray crashed during port check.")

            if not ready_ports:
                print("\n--- No ports became ready. Aborting tests. ---")
                return

            print("\n--- Starting all checks concurrently ---")
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                future_to_item = {executor.submit(check_one_proxy, item, test_url): item for item in items_to_test}
                for future in as_completed(future_to_item):
                    if result_uri := future.result():
                        final_uris_to_write.append(result_uri)

    except Exception as e:
        logging.critical(f"A critical error occurred: {e}", exc_info=False)

    finally:
        print(f"\n--- Writing {len(final_uris_to_write)} Final Configurations ---")
        Path(FINAL_CONFIGS_PATH).write_text("\n".join(final_uris_to_write) + "\n")
        print("\n--- Script finished successfully! ---")

if __name__ == "__main__":
    main()
