import sys
import logging
from pathlib import Path

# --- Library Imports ---
# ما فقط از کامپوننت‌های سالم کتابخانه استفاده می‌کنیم
from python_v2ray.downloader import BinaryDownloader, OWN_REPO
from python_v2ray.config_parser import ConfigParams # <-- فقط دیتاکلاس رو لازم داریم
from python_v2ray.tester import ConnectionTester

logging.basicConfig(level=logging.INFO, format='%(asctime)s - [%(levelname)s] - %(message)s')

PROJECT_ROOT = Path(__file__).parent
VENDOR_PATH = PROJECT_ROOT / "vendor"
CORE_ENGINE_PATH = PROJECT_ROOT / "core_engine"

def manual_socks_parser(uri: str) -> ConfigParams:
    """
    یک پارسر دستی و ساده فقط برای این تست، چون پارسر کتابخانه باگ دارد.
    """
    print("Using manual SOCKS parser to bypass library bug...")
    main_part = uri.replace("socks://", "").split("#")[0]
    host, port_str = main_part.split(":")
    return ConfigParams(
        protocol="socks",
        address=host,
        port=int(port_str),
        tag="Public-SOCKS-Test",
        display_tag="Public-SOCKS-Test"
    )

def main():
    print("--- Starting FINAL Sanity Check (bypassing library parser) ---")

    public_proxy_uri = "socks://45.176.120.150:1080#Public-SOCKS-Test"
    print(f"\n--- Manually parsing test URI: {public_proxy_uri} ---")
    
    # --- از پارسر دستی خودمان استفاده می‌کنیم ---
    proxy_config = manual_socks_parser(public_proxy_uri)
    if not proxy_config:
        print("Fatal: Manual parser failed."); sys.exit(1)
    
    configs_to_test = [proxy_config]
    
    print("\n--- Ensuring Binaries are Ready ---")
    try:
        CORE_ENGINE_PATH.mkdir(exist_ok=True)
        downloader = BinaryDownloader(PROJECT_ROOT)
        if not downloader.ensure_binary("core_engine", CORE_ENGINE_PATH, OWN_REPO):
            raise RuntimeError("Failed to get core_engine")
        
        generic_path = CORE_ENGINE_PATH / "core_engine"
        if sys.platform != "win32":
            expected_name = "core_engine_linux" if "linux" in sys.platform else "core_engine_macos"
            if generic_path.is_file(): generic_path.rename(CORE_ENGINE_PATH / expected_name)
        
        print("Binaries are ready.")
    except Exception as e:
        print(f"Fatal Error during binary check: {e}"); sys.exit(1)

    print("\n--- Running Connectivity Test on the Public Proxy ---")
    tester = ConnectionTester(vendor_path=str(VENDOR_PATH), core_engine_path=str(CORE_ENGINE_PATH))
    results = tester.test_uris(
        parsed_params=configs_to_test, 
        timeout=40,
        ping_url="http://connectivitycheck.gstatic.com/generate_204"
    )

    if not results:
        print("\n--- TEST FAILED: No results returned."); sys.exit(1)
    
    result = results[0]
    if result.get('status') == 'success':
        print("\n" + "="*60)
        print("--- SANITY CHECK PASSED! ---")
        print(f"Successfully connected with a ping of {result.get('ping_ms')} ms.")
        print("This PROVES the tester, binaries, and environment are working correctly.")
        print("THE ONLY PROBLEM IS THE LIBRARY'S 'parse_uri' FUNCTION.")
        print("="*60)
    else:
        print("\n" + "="*60)
        print("--- TEST FAILED ---")
        print(f"Could not connect. Reason: {result.get('status')}")
        print("="*60)
        sys.exit(1)

if __name__ == "__main__":
    main()
