import re
from camoufox_stealth import launch_stealth_browser, StealthProfileManager

# Profiles directory — absolute path on Ubuntu
PROFILES_DIR = "/home/kayan/Desktop/FB WARMS/FBPROFILE"

def main():
    manager = StealthProfileManager(PROFILES_DIR)
    existing = manager.list_profiles()

    # Scan existing profiles to find the highest number 'oN'
    max_num = 0
    for p in existing:
        match = re.match(r"^o(\d+)$", p)
        if match:
            max_num = max(max_num, int(match.group(1)))

    next_profile = f"o{max_num + 1}"
    print(f"\n[INFO] Auto-detected next profile name: '{next_profile}'")
    print(f"[INFO] Launching headful browser (no proxy)...")

    try:
        with launch_stealth_browser(
            profile_name=next_profile,
            profiles_dir=PROFILES_DIR,
            headless=False,
            proxy=None,
            geoip=False,
        ) as browser:
            page = browser.new_page()
            page.goto("https://abrahamjuliot.github.io/creepjs/")
            print(f"\n[SUCCESS] Active Profile: '{next_profile}'")
            print("[INFO] Click the browser window to interact. Closing the browser window will complete the session.")

            # Wait indefinitely until the page is closed
            page.wait_for_event("close", timeout=0)
    except KeyboardInterrupt:
        print("\n[INFO] Interrupted by user.")
    except Exception as e:
        err = str(e)
        # Show the real error always — helpful for debugging
        print(f"\n[ERROR] {err[:600]}")
        if "Target page, context or browser has been closed" in err or err.strip() == "":
            print("[INFO] Browser was closed.")

if __name__ == "__main__":
    main()
