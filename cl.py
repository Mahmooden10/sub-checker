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
from python_v2ray.hysteria_manager import HysteriaCore

import base64
import urllib.parse

import argparse

logging.basicConfig(level=logging.INFO, format='%(asctime)s - [%(levelname)s] - %(message)s')

PROJECT_ROOT = Path(__file__).parent.resolve()
VENDOR_PATH = PROJECT_ROOT / "vendor"
CORE_ENGINE_PATH = PROJECT_ROOT / "core_engine"
CONFIG_FILE_PATH = PROJECT_ROOT / "config.json"
INPUT_CONFIGS_PATH = PROJECT_ROOT / "normal.txt"
FINAL_CONFIGS_PATH = PROJECT_ROOT / "final.txt"
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

def full_unquote(s: str) -> str:

    if '%' not in s:
        return s

    prev_s = ""
    while s != prev_s:
        prev_s = s
        s = urllib.parse.unquote(s)
    return s
def get_ip_details_and_retag(original_uri: str, country_code: str) -> str:

    uri = original_uri.strip()
    country_code = country_code.upper()

    if uri.startswith("vmess://"):
        try:
            parts = uri.split('#', 1)
            base_part = parts[0]

            encoded_json = base_part.replace("vmess://", "")
            encoded_json += '=' * (-len(encoded_json) % 4)

            decoded_json = base64.b64decode(encoded_json).decode('utf-8')
            vmess_data = json.loads(decoded_json)

            original_ps = vmess_data.get("ps", "")
            cleaned_ps = full_unquote(original_ps)
            vmess_data["ps"] = f"{cleaned_ps}::{country_code}"

            updated_json = json.dumps(vmess_data, separators=(',', ':'))
            updated_b64 = base64.b64encode(updated_json.encode('utf-8')).decode('utf-8').rstrip('=')

            return "vmess://" + updated_b64

        except Exception as e:
            logging.warning(f"Could not inject location into VMess PS tag for '{uri[:30]}...': {e}. Appending externally as fallback.")
            return f"{uri}::{country_code}"

    elif '#' in uri:
        base_uri, tag = uri.split('#', 1)
        cleaned_tag = re.sub(r'::[A-Z]{2}$', '', tag).strip()
        new_tag = f"{cleaned_tag}::{country_code}"
        return f"{base_uri}#{urllib.parse.quote(new_tag)}"
    else:
        return f"{uri}#::{country_code}"


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


def check_one_proxy(item: dict, test_url: str, check_loc_enabled: bool, check_iran_enabled: bool) -> Optional[str]:
    config_param, original_uri, local_port = item['params'], item['original_uri'], item['local_port']
    logging.info(f"--> Starting check for: {config_param.display_tag} on port {local_port}")
    proxies = {"http": f"socks5h://127.0.0.1:{local_port}", "https": f"socks5h://127.0.0.1:{local_port}"}
    try:
        start_time = time.time()
        response = requests.get(test_url, proxies=proxies, timeout=20); response.raise_for_status()
        ping_ms = int((time.time() - start_time) * 1000)
        logging.info(f"  [SUCCESS] Ping: {ping_ms} ms for {config_param.display_tag}.")

        exit_ip = get_public_ipv4(proxies)

        if check_iran_enabled:
            is_inaccessible_from_iran = is_ip_accessible_from_iran_via_check_host(exit_ip, proxies)
            if is_inaccessible_from_iran is False:  # False means it IS accessible
                logging.warning(f"  [FILTERED] {config_param.display_tag} is accessible from Iran and has been removed.")
                return None
            if is_inaccessible_from_iran is None: # Inconclusive check
                logging.warning(f"  [WARNING] Iran accessibility check for {config_param.display_tag} was inconclusive. Allowing it to pass.")

        if check_loc_enabled:
            country_code = fetch_country_code(proxies)
            final_uri = get_ip_details_and_retag(original_uri, country_code)
            logging.info(f"  [ADDED] {config_param.display_tag} passed all checks.")
            return final_uri
        else:
            return original_uri

    except Exception as e:
        logging.error(f"  [FAIL] Test failed for {config_param.display_tag}. Reason: {str(e)[:120]}")
        return None
def main(check_loc_enabled: bool, check_iran_enabled: bool):

    print("--- Starting Full Protocol Checker Script ---")
    try:
        with open(CONFIG_FILE_PATH, "r") as f: settings = json.load(f)
        test_url = settings.get("core", {}).get("test_url", "http://connectivitycheck.gstatic.com/generate_204")
    except Exception as e:
        print(f"Error loading config.json: {e}"); return

    all_uris = Path(INPUT_CONFIGS_PATH).read_text().splitlines()
    configs_with_uris = []
    for uri in all_uris:
        if p := parse_uri(uri):
            configs_with_uris.append({'params': p, 'original_uri': uri})

    print(f"Successfully parsed {len(configs_with_uris)} configs out of {len(all_uris)}.")

    unique_items_map = {}
    for item in configs_with_uris:
        p = item['params']
        key = (p.protocol, p.address, p.port, p.id, p.password, p.hy2_password, p.wg_secret_key)
        if key not in unique_items_map:
            unique_items_map[key] = item
    unique_items = list(unique_items_map.values())
    print(f"Found {len(unique_items)} unique configurations to test.")

    print("--- Ensuring all necessary binaries are available... ---")
    try:
        downloader = BinaryDownloader(PROJECT_ROOT)
        downloader.ensure_all()
        print("--- Binaries are ready. ---")
    except Exception as e:
        # If download fails, the script cannot continue.
        print(f"Fatal Error: Could not obtain necessary binaries: {e}")
        return

    xray_items = []
    hysteria_items = []
    for item in unique_items:
        proto = item['params'].protocol
        if proto in ["vless", "vmess", "trojan", "ss", "socks", "mvless"]:
            xray_items.append(item)
        elif proto in ["hysteria", "hysteria2", "hy2"]:
            hysteria_items.append(item)

    final_uris_to_write = []
    base_port_start = 20800

    if xray_items:
        print(f"\n--- Found {len(xray_items)} Xray-compatible configs to test ---")
        BATCH_SIZE = 50
        for i in range(0, len(xray_items), BATCH_SIZE):
            batch_items = xray_items[i:i + BATCH_SIZE]
            print(f"\n--- Processing Xray Batch {i//BATCH_SIZE + 1} ({len(batch_items)} configs) ---")

            builder = XrayConfigBuilder()
            items_to_test_in_batch = []
            expected_ports = set()

            for idx, item in enumerate(batch_items):

                local_port = base_port_start + i + idx
                unique_outbound_tag = f"proxy_out_{local_port}"
                outbound = builder.build_outbound_from_params(item['params'], explicit_tag=unique_outbound_tag)

                if outbound:
                    item['local_port'] = local_port
                    items_to_test_in_batch.append(item)
                    expected_ports.add(local_port)

                    builder.add_inbound({"port": local_port, "listen": "127.0.0.1", "protocol": "socks", "tag": f"inbound-{local_port}"})
                    builder.add_outbound(outbound)
                    builder.config["routing"]["rules"].append({"type": "field", "inboundTag": [f"inbound-{local_port}"], "outboundTag": unique_outbound_tag})

            if not items_to_test_in_batch:
                print("No Xray-compatible configurations found in this batch. Skipping.")
                continue

            try:
                with XrayCore(vendor_path=str(VENDOR_PATH), config_builder=builder, debug_mode=True) as xray:
                    time.sleep(1)
                    if not xray.is_running():
                        raise RuntimeError("Xray process failed to start for this batch. The batch may contain a malformed config.")

                    print(f"  Xray is running for this batch. Waiting for {len(expected_ports)} ports...")

                    ready_ports = set()
                    for _ in range(40):
                        if not xray.is_running():
                            print("\n  --- XRAY CRASHED! Skipping this batch. ---")
                            break
                        ports_to_check = expected_ports - ready_ports
                        for port in ports_to_check:
                            try:
                                with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                                    ready_ports.add(port)
                            except (socket.timeout, ConnectionRefusedError):
                                pass
                        if ready_ports == expected_ports:
                            print(f"  All {len(expected_ports)} ports are ready.")
                            break
                        time.sleep(0.25)
                    else:
                        if xray.is_running():
                            raise RuntimeError(f"  Timeout: Not all ports became ready. {len(ready_ports)}/{len(expected_ports)} ready.")
                        else:
                            raise RuntimeError("Xray crashed during port check.")

                    if len(ready_ports) != len(expected_ports):
                        logging.warning(f"Not all ports were ready ({len(ready_ports)}/{len(expected_ports)}). Continuing with what's available.")

                    print(f"  Starting Xray checks for batch...")
                    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                        future_to_item = {executor.submit(check_one_proxy, item, test_url,check_loc_enabled,check_iran_enabled  ): item for item in items_to_test_in_batch}
                        for future in as_completed(future_to_item):
                            if result_uri := future.result():
                                final_uris_to_write.append(result_uri)

            except Exception as e:
                logging.error(f"  [FAIL] Critical error in Xray batch {i//BATCH_SIZE + 1}: {e}", exc_info=False)
                logging.warning("  Skipping to the next batch.")
                continue

    if hysteria_items:
        print(f"\n--- Found {len(hysteria_items)} Hysteria configs to test ---")
        hysteria_managers = []
        hysteria_jobs_for_test = []

        hysteria_base_port = base_port_start + len(xray_items)

        for i, item in enumerate(hysteria_items):
            local_port = hysteria_base_port + i
            item['local_port'] = local_port
            hysteria_jobs_for_test.append(item)
            manager = HysteriaCore(str(VENDOR_PATH), params=item['params'], local_port=local_port)
            hysteria_managers.append(manager)

        try:
            print(f"Starting {len(hysteria_managers)} Hysteria client(s)...")
            for manager in hysteria_managers:
                manager.start()

            time.sleep(2)
            print("\n--- Starting Hysteria checks concurrently ---")
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                future_to_item = {executor.submit(check_one_proxy, item, test_url,check_loc_enabled,check_iran_enabled): item for item in hysteria_jobs_for_test}
                for future in as_completed(future_to_item):
                    if result_uri := future.result():
                        final_uris_to_write.append(result_uri)
        finally:
            print("Stopping all Hysteria clients...")
            for manager in reversed(hysteria_managers):
                manager.stop()

    print(f"\n--- Writing {len(final_uris_to_write)} Final Configurations ---")
    Path(FINAL_CONFIGS_PATH).write_text("\n".join(final_uris_to_write) + "\n")
    print("\n--- Script finished successfully! ---")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="V2Ray/Hysteria Config Checker (with optional overrides)")
    parser.add_argument(
        '--location',
        dest='check_loc_override',
        choices=['true', 'false'],
        help="Override the default CHECK_LOC setting. Use 'true' or 'false'."
    )
    parser.add_argument(
        '--iran-check',
        dest='check_iran_override',
        choices=['true', 'false'],
        help="Override the default CHECK_IRAN setting. Use 'true' or 'false'."
    )
    args = parser.parse_args()

    check_loc_enabled = CHECK_LOC
    check_iran_enabled = CHECK_IRAN

    if args.check_loc_override is not None:
        check_loc_enabled = (args.check_loc_override == 'true')
        print(f"--- Overriding Location Check via command line: Set to {check_loc_enabled} ---")

    if args.check_iran_override is not None:
        check_iran_enabled = (args.check_iran_override == 'true')
        print(f"--- Overriding Iran Check via command line: Set to {check_iran_enabled} ---")

    print(f"--- Starting Full Protocol Checker Script ---")
    print(f"Effective Location Check: {'Enabled' if check_loc_enabled else 'Disabled'}")
    print(f"Effective Iran Accessibility Check: {'Enabled' if check_iran_enabled else 'Disabled'}")
    main(check_loc_enabled, check_iran_enabled)