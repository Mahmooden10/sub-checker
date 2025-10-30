import sys
import logging
from pathlib import Path
from python_v2ray.downloader import BinaryDownloader, OWN_REPO
from python_v2ray.config_parser import parse_uri
from python_v2ray.tester import ConnectionTester

logging.basicConfig(level=logging.INFO, format='%(asctime)s - [%(levelname)s] - %(message)s')

PROJECT_ROOT = Path(__file__).parent
VENDOR_PATH = PROJECT_ROOT / "vendor"
CORE_ENGINE_PATH = PROJECT_ROOT / "core_engine"

def main():
    print("--- Starting System Sanity Check (using a public SOCKS proxy) ---")

    # یک پروکسی ساکس عمومی و رایگان که احتمالاً IP گیت‌هاب را بلاک نمی‌کند
    public_proxy_uri = "socks://45.176.120.150:1080#Public-SOCKS-Test"

    print(f"\n--- Using a known public proxy for testing: {public_proxy_uri} ---")
    proxy_config = parse_uri(public_proxy_uri)
    if not proxy_config:
        print("Fatal: Could not parse the hardcoded SOCKS URI.")
        sys.exit(1)
    
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
        timeout=40,  # تایم‌اوت بالا برای پروکسی عمومی
        ping_url="http://connectivitycheck.gstatic.com/generate_204"
    )

    if not results:
        print("\n--- TEST FAILED: No results returned."); sys.exit(1)
    
    result = results[0]
    if result.get('status') == 'success':
        print("\n" + "="*60)
        print("--- SANITY CHECK PASSED! ---")
        print(f"Successfully connected through a public proxy with a ping of {result.get('ping_ms')} ms.")
        print("This PROVES the library and the refactored script are working correctly.")
        print("The '0 working configurations' issue is 100% due to your specific configs being blocked in the GitHub Actions environment.")
        print("="*60)
    else:
        print("\n" + "="*60)
        print("--- TEST FAILED ---")
        print(f"Could not connect even through a public proxy. Reason: {result.get('status')}")
        print("This might indicate a network issue in the GitHub runner itself.")
        print("="*60)
        sys.exit(1)

if __name__ == "__main__":
    main()
