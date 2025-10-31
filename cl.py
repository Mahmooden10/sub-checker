import sys
import subprocess
from pathlib import Path

# از همان پارسر و بیلدر کتابخانه خودتان استفاده می‌کنیم
from python_v2ray.config_parser import parse_uri, XrayConfigBuilder

# --- تنظیمات ---
PROJECT_ROOT = Path(__file__).parent.resolve()
VENDOR_PATH = PROJECT_ROOT / "vendor"
INPUT_CONFIGS_PATH = "normal.txt"  # فایلی که کانفیگ‌ها در آن قرار دارند
CONFIG_TO_TEST_INDEX = 0  # شماره خط کانفیگی که می‌خواهید تست کنید (0 یعنی اولین کانفیگ)
# --- پایان تنظیمات ---

def find_xray_executable() -> Path:
    """پیدا کردن نام فایل اجرایی Xray بر اساس سیستم عامل"""
    if sys.platform == "win32":
        exe_name = "xray.exe"
    elif sys.platform == "darwin":
        exe_name = "xray_macos"
    else:
        exe_name = "xray_linux"
    
    executable_path = VENDOR_PATH / exe_name
    if not executable_path.is_file():
        raise FileNotFoundError(f"Xray executable not found at: {executable_path}")
    return executable_path

def main():
    print("--- Xray Minimal Config Debugger ---")

    # ۱. خواندن کانفیگ مشخص شده از فایل
    try:
        with open(INPUT_CONFIGS_PATH, "r", encoding="utf-8") as f:
            all_uris = f.read().splitlines()
        
        if len(all_uris) <= CONFIG_TO_TEST_INDEX:
            print(f"Error: Config index {CONFIG_TO_TEST_INDEX} is out of bounds. File only has {len(all_uris)} configs.")
            return
            
        target_uri = all_uris[CONFIG_TO_TEST_INDEX]
        print(f"[*] Testing URI from line {CONFIG_TO_TEST_INDEX + 1}: {target_uri[:50]}...")
        
    except FileNotFoundError:
        print(f"Error: Input file not found at '{INPUT_CONFIGS_PATH}'")
        return

    # ۲. پارس کردن کانفیگ
    params = parse_uri(target_uri)
    if not params:
        print("\n[FAIL] The URI could not be parsed. Nothing to test.")
        # اینجا از همان منطق اصلاح gRPC استفاده می‌شود
        if "grpc" in target_uri:
             print("Info: This might be a gRPC config with a missing 'path' (serviceName) and no fallback.")
        return

    # ۳. ساخت یک کانفیگ Xray بسیار ساده
    builder = XrayConfigBuilder()

    # یک ورودی (inbound) ساده برای تست
    builder.add_inbound({
        "tag": "socks-in",
        "port": 10808,
        "listen": "127.0.0.1",
        "protocol": "socks",
        "settings": {"auth": "noauth", "udp": True},
    })

    # ساخت خروجی (outbound) از روی کانفیگ پارس شده
    outbound = builder.build_outbound_from_params(params)
    if not outbound:
        print("\n[FAIL] Failed to build a valid Xray outbound from the parsed params.")
        print(f"Info: This usually happens for protocols not handled by Xray (like Hysteria). Protocol was: '{params.protocol}'")
        return
        
    builder.add_outbound(outbound)
    
    # اضافه کردن یک روتینگ ساده
    builder.config["routing"]["rules"].append({
        "type": "field",
        "inboundTag": ["socks-in"],
        "outboundTag": outbound["tag"],
    })

    # ۴. نوشتن کانفیگ در فایل و چاپ آن
    debug_config_path = PROJECT_ROOT / "debug_config.json"
    config_json_str = builder.to_json()
    debug_config_path.write_text(config_json_str, encoding="utf-8")

    print("\n" + "="*50)
    print(f"[*] Generated 'debug_config.json' for Xray:")
    print("="*50)
    print(config_json_str)
    print("="*50 + "\n")

    # ۵. اجرای مستقیم Xray و نمایش خروجی
    try:
        xray_exe = find_xray_executable()
        command = [str(xray_exe), "-c", str(debug_config_path)]
        
        print(f"[*] Running command: {' '.join(command)}")
        print("[*] --- Xray Output Starts Below ---")
        
        # اجرای Xray و نمایش خروجی به صورت زنده
        # این پروسه تا زمانی که با Ctrl+C آن را متوقف نکنید، ادامه خواهد داشت
        process = subprocess.run(command, check=False)
        
        print("[*] --- Xray Output Ended ---")
        if process.returncode == 0:
            print("\n[SUCCESS] Xray exited with code 0. The config seems valid!")
        else:
            print(f"\n[FAIL] Xray exited with a non-zero code ({process.returncode}). The config is invalid.")
            print("Check the error message above from Xray itself.")

    except FileNotFoundError as e:
        print(f"\n[FAIL] Error: {e}")
    except Exception as e:
        print(f"\n[FAIL] An unexpected error occurred: {e}")

if __name__ == "__main__":
    main()
