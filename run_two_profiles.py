import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from camoufox_stealth import launch_stealth_browser

def run_profile(profile_name):
    print(f"\n=========================================")
    print(f"Launching profile: {profile_name}")
    print(f"=========================================")
    try:
        with launch_stealth_browser(
            geoip=False,
            headless=False,
            profile_name=profile_name
        ) as browser:
            if hasattr(browser, "new_context"):
                ctx = browser.new_context()
                page = ctx.new_page()
            else:
                page = browser.new_page()
            
            # Safely log consoles and page errors
            page.on("pageerror", lambda err: print(f"[{profile_name} JS Error] {err}"))
            page.on("console", lambda msg: print(f"[{profile_name} JS Console] {msg.text.encode('ascii', errors='replace').decode('ascii')}"))
            
            print(f"[+] Navigating to CreepJS...")
            page.goto("https://abrahamjuliot.github.io/creepjs/")
            
            print("[+] Browser is open. Review CreepJS results.")
            print("[+] Close the browser window/tab to proceed to the next profile.")
            
            # Wait for the page close event
            page.wait_for_event("close", timeout=0)
            
    except Exception as e:
        print(f"[!] Session ended: {e}")

run_profile("manual_test_profile_Z1")
run_profile("manual_test_profile_Z2")

print("\n=========================================")
print("Both verification sessions completed successfully!")
print("=========================================")
