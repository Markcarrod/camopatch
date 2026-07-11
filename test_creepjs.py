"""
test_creepjs.py — Open CreepJS in a hardened Camoufox window and keep it alive.
Supports optional profile name command line argument:
  python test_creepjs.py [profile_name]
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from camoufox_stealth import launch_stealth_browser

profile_name = sys.argv[1] if len(sys.argv) > 1 else None
URL = "https://abrahamjuliot.github.io/creepjs/"

with launch_stealth_browser(
    geoip=False,         # skip IP lookup for speed
    headless=False,      # headful so you can see it
    humanize=1.5,
    profile_name=profile_name,
) as browser:
    # If a profile_name is passed, launch_stealth_browser returns a persistent BrowserContext.
    # Otherwise, it returns a standard Browser object.
    if hasattr(browser, 'new_context'):
        ctx = browser.new_context()
        page = ctx.new_page()
    else:
        page = browser.new_page()

    page.on("pageerror", lambda err: print(f"[JS Page Error] {err}"))
    page.on("console", lambda msg: print(f"[JS Console] {msg.text.encode('ascii', errors='replace').decode('ascii')}"))


    print(f"[+] Navigating to {URL}")


    page.goto(URL, timeout=60_000)
    print("[+] Page loaded. Waiting for CreepJS to finish scoring (up to 60s)...")

    # Try multiple selectors CreepJS has used across versions
    GRADE_SELECTORS = [
        "span.grade",
        ".grade",
        "[class*='grade']",
        "#creep-grade",
        ".trust-score",
        "[class*='trust']",
        "[class*='score']",
    ]

    grade_found = False
    try:
        # Wait for any of the known grade selectors
        for sel in GRADE_SELECTORS:
            try:
                page.wait_for_selector(sel, timeout=5_000)
                text = page.locator(sel).first.inner_text()
                if text.strip():
                    print(f"\n[OK] CreepJS Grade ({sel}): {text.strip()}\n")
                    grade_found = True
                    break
            except Exception:
                continue

        if not grade_found:
            # Fallback: dump all text that looks like a score/grade via JS
            result = page.evaluate("""() => {
                const all = Array.from(document.querySelectorAll('*'));
                const hits = all.filter(el => {
                    const txt = (el.innerText || '').trim();
                    const cls = (el.className || '').toLowerCase();
                    return (cls.includes('grade') || cls.includes('trust') || cls.includes('score'))
                           && txt.length > 0 && txt.length < 200;
                });
                return hits.map(el => el.className + ': ' + (el.innerText || '').trim().slice(0, 100));
            }""")
            if result:
                print("\n[OK] CreepJS score elements found:")
                for r in result[:10]:
                    print(f"   {r}")
            else:
                print("[!] No score element detected yet — look at the browser window directly.")
    except Exception as e:
        print(f"[!] Error reading grade: {e}")

    # Keep the window open until the user presses Enter
    input(f"\n[Browser is open ({profile_name or 'transient'}) — review CreepJS results, then press ENTER to close]\n")


