"""
camoufox_stealth.py
===================
Maximum-stealth Camoufox launcher & multi-profile manager.

This script is an opinionated, production-grade wrapper around the Camoufox package.
It implements persistent hardware identities, human mouse/scrolling/typing humanizers,
and high-stealth behavioral helpers.

Windows Stealth Architecture & Guidelines:
------------------------------------------
1. Timezone & Locale Granularity:
   Passing geoip=True fetches city-specific timezones (e.g. America/New_York) matching the
   current IP or proxy. Timezones are natively applied, aligning Javascript Date offsets,
   Intl.DateTimeFormat().resolvedOptions().timeZone, and system clock structures.
2. Accept-Language fallbacks:
   Accept-Language headers are built dynamically matching the current locale and appended with 
   ordered en-US/en fallback language strings weighted with appropriate quality (q) weights.
3. Chrome Dimensions:
   Headless signatures are eliminated by enforcing a dynamic border/chrome offset matching
   real Windows displays (horizontal borders: 0px or 8-16px, vertical chrome: 80px, 108px, 120px).
4. Page Visibility Emu:
   Real users switch tabs and minimize windows. To emulate this in your scripts, periodically 
   create a new tab/page, switch focus to it via page.bring_to_front() for a few seconds, then
   return. This fires native 'visibilitychange' events (document.hidden = true) on the main page.
5. window.name Isolation:
   Firefox isolates window.name across domains (first-party isolation). To guarantee safety,
   clear window.name between navigations to prevent tracking token handovers.
6. Clipboard & Gamepad APIs:
   Standard Firefox desktop has window.navigator.getGamepads returning an empty array (not undefined)
   and standard Permission-gated Clipboard APIs. Both are fully native in Camoufox.
7. Profile Pre-warming:
   Arriving directly to a high-value page with 1 history entry looks automated. Build up profile cache, 
   cookies, and storage quotas by pre-warming: navigate to Google/news sites, search, scroll, and 
   accumulate history naturally first. Use page.goto(..., referer="https://www.google.com") for referrers.
"""

from __future__ import annotations

import sys
import time
import math
import random
import json
from pathlib import Path
from contextlib import asynccontextmanager, contextmanager
from typing import Any, Dict, List, Optional, Union

# ---------------------------------------------------------------------------
# Camoufox imports
# ---------------------------------------------------------------------------
try:
    from camoufox import AsyncCamoufox, Camoufox, STEALTH_PREFS, pick_realistic_screen
    from camoufox.utils import launch_options
except ImportError as exc:
    sys.exit(
        f"[camoufox_stealth] Missing dependency: {exc}\n"
        "Install with:  pip install camoufox[geoip]\n"
        "Then fetch the browser:  python -m camoufox fetch"
    )

__all__ = [
    "STEALTH_PREFS",
    "pick_realistic_screen",
    "launch_stealth_browser",
    "launch_stealth_browser_async",
    "get_stealth_options",
    "random_wait",
    "stealth_type",
    "stealth_scroll",
    "StealthProfileManager",
]

# ===========================================================================
# Behavioral & Input Humanization Helpers
# ===========================================================================

def random_wait(page: Any = None, min_sec: float = 1.0, max_sec: float = 4.0) -> None:
    """
    Adds a random delay between min_sec and max_sec to mimic human pacing.
    If a Playwright Page object is passed, it uses page.wait_for_timeout (non-blocking).
    Otherwise, falls back to time.sleep().
    """
    delay_ms = int(random.uniform(min_sec, max_sec) * 1000)
    if page:
        page.wait_for_timeout(delay_ms)
    else:
        time.sleep(delay_ms / 1000.0)


def stealth_type(page: Any, selector: str, text: str, error_rate: float = 0.02) -> None:
    """
    Types text into the target element with realistic human keyboard characteristics:
    - Focuses the element first to fire natural focus/blur events.
    - Variable delay between keystrokes.
    - Simulates typos of adjacent QWERTY keys and backspace corrections.
    """
    qwerty_neighbors = {
        'a': 'qwsz', 'b': 'vghn', 'c': 'xdfv', 'd': 'ersfxc', 'e': 'wsdr',
        'f': 'rtgvcd', 'g': 'tyhbvf', 'h': 'yujnbg', 'i': 'ujko', 'j': 'uikmnh',
        'k': 'ijlm', 'l': 'okp', 'm': 'njk', 'n': 'bhjm', 'o': 'iklp',
        'p': 'ol', 'q': 'wa', 'r': 'edft', 's': 'wedxza', 't': 'rfgy',
        'u': 'yhji', 'v': 'cfgb', 'w': 'qase', 'x': 'zsdc', 'y': 'tghu',
        'z': 'asx', ' ': 'c vbnm'
    }
    
    # Focus the element first (fires focus events naturally)
    page.focus(selector)
    random_wait(page, 0.1, 0.3)
    
    for char in text:
        # Typo simulation
        if char.lower() in qwerty_neighbors and random.random() < error_rate:
            typo_char = random.choice(qwerty_neighbors[char.lower()])
            if char.isupper():
                typo_char = typo_char.upper()
            
            # Type the typo
            page.keyboard.type(typo_char)
            # Short pause before realizing the mistake
            random_wait(page, 0.15, 0.35)
            # Press backspace
            page.keyboard.press("Backspace")
            # Wait before correction
            random_wait(page, 0.1, 0.25)
            
        # Type the correct character
        page.keyboard.type(char)
        # Variable keystroke speed (mimicking hand movements)
        random_wait(page, 0.05, 0.18)


def stealth_scroll(page: Any, direction: str = "down", distance: int = 400, steps: int = 15) -> None:
    """
    Scrolls the page smoothly simulating physical momentum (inertia) and human hesitation.
    """
    multipliers = []
    for i in range(1, steps + 1):
        t = i / steps
        multipliers.append(math.sin(t * math.pi))
    
    total_mult = sum(multipliers)
    step_distances = [int((m / total_mult) * distance) for m in multipliers]
    
    sign = 1 if direction == "down" else -1
    
    for dist in step_distances:
        page.evaluate(f"window.scrollBy(0, {sign * dist})")
        random_wait(page, 0.015, 0.04)
        
    # Hesitate at the end of scroll
    random_wait(page, 0.3, 0.8)


# ===========================================================================
# Multi-Profile Manager Class
# ===========================================================================

class StealthProfileManager:
    """
    Manages persistent profiles for Camoufox.
    Saves and rotates profiles, maintaining static hardware identities
    to prevent WebGL/User-Agent rotation anomalies on same profile.
    """
    def __init__(self, profiles_dir: str = "profiles"):
        self.profiles_dir = Path(profiles_dir)
        self.profiles_dir.mkdir(parents=True, exist_ok=True)

    def get_profile_path(self, name: str) -> Path:
        """Get the absolute path to a profile folder."""
        return (self.profiles_dir / name).resolve()

    def list_profiles(self) -> List[str]:
        """List all active profiles in the directory."""
        if not self.profiles_dir.exists():
            return []
        return [p.name for p in self.profiles_dir.iterdir() if p.is_dir()]

    def get_profile_stats(self, name: str) -> Dict[str, Any] | None:
        """Inspect a profile's saved fingerprint metadata."""
        p_path = self.get_profile_path(name)
        config_file = p_path / "camoufox_profile.json"
        if not config_file.exists():
            return None
        try:
            with open(config_file, "r") as f:
                data = json.load(f)
            config = data.get("config", {})
            prefs = data.get("firefox_user_prefs", {})
            return {
                "name": name,
                "path": str(p_path),
                "userAgent": config.get("headers.User-Agent") or config.get("navigator.userAgent"),
                "screen": f"{config.get('screen.width')}x{config.get('screen.height')}",
                "cores": config.get("navigator.hardwareConcurrency"),
                "gpu": config.get("webGl:renderer"),
                "dark_mode": "dark" if prefs.get("layout.css.prefers-color-scheme.content-override") == 0 else "light"
            }
        except Exception:
            return None

    def delete_profile(self, name: str) -> bool:
        """Safely delete a profile folder and its saved cache."""
        p_path = self.get_profile_path(name)
        if p_path.exists():
            import shutil
            shutil.rmtree(p_path)
            return True
        return False


# ===========================================================================
# Internal: shared kwargs builder
# ===========================================================================

def _common_kwargs(
    proxy: Optional[Dict[str, str]] = None,
    geoip: Union[str, bool] = True,
    humanize: Union[bool, float] = 1.5,
    headless: bool = False,
    extra_config: Optional[Dict[str, Any]] = None,
    extra_prefs: Optional[Dict[str, Any]] = None,
    profile_name: Optional[str] = None,
    profiles_dir: str = "profiles",
) -> Dict[str, Any]:
    """
    Build the keyword-argument dict used by both sync and async launchers.
    Supports profile caching and persistent contexts.
    """
    screen = pick_realistic_screen()

    base_config: Dict[str, Any] = {}
    if extra_config:
        base_config.update(extra_config)

    extra_firefox_prefs: Dict[str, Any] = {
        "media.webspeech.synth.enabled": True,
        "media.webspeech.synth.force_global_queue": False,
    }
    if extra_prefs:
        extra_firefox_prefs.update(extra_prefs)

    kwargs: Dict[str, Any] = {
        "os": "windows",
        "screen": screen,
        "humanize": humanize,
        "geoip": geoip,
        "config": base_config,
        "firefox_user_prefs": extra_firefox_prefs,
        "headless": headless,
        "enable_cache": True,
        "block_webrtc": False,
        "block_webgl": False,
        "block_images": False,
        "i_know_what_im_doing": False,
    }

    if proxy:
        kwargs["proxy"] = proxy

    if profile_name:
        manager = StealthProfileManager(profiles_dir)
        profile_path = manager.get_profile_path(profile_name)
        kwargs["user_data_dir"] = str(profile_path)

    return kwargs


# ===========================================================================
# JS Stealth Init Script (RDP Speech Synthesis Mock)
# ===========================================================================

INIT_STEALTH_JS = """
(() => {
  if (typeof window === 'undefined') return;
  
  const voiceData = [
    {
      voiceURI: "urn:moz-tts:sapi:Microsoft David Desktop - English (United States)?en-US",
      name: "Microsoft David Desktop - English (United States)",
      lang: "en-US",
      localService: true,
      "default": true
    },
    {
      voiceURI: "urn:moz-tts:sapi:Microsoft Zira Desktop - English (United States)?en-US",
      name: "Microsoft Zira Desktop - English (United States)",
      lang: "en-US",
      localService: true,
      "default": false
    }
  ];

  const voiceProto = window.SpeechSynthesisVoice ? window.SpeechSynthesisVoice.prototype : Object.prototype;
  
  const voices = voiceData.map(data => {
    const voice = Object.create(voiceProto);
    Object.defineProperties(voice, {
      voiceURI: { value: data.voiceURI, enumerable: true },
      name: { value: data.name, enumerable: true },
      lang: { value: data.lang, enumerable: true },
      localService: { value: data.localService, enumerable: true },
      "default": { value: data.default, enumerable: true }
    });
    return voice;
  });

  if (window.SpeechSynthesis) {
    const proto = window.SpeechSynthesis.prototype;

    // 1. Mock getVoices on the prototype
    const getVoicesMock = function() { return voices; };
    Object.defineProperty(getVoicesMock, 'name', { value: 'getVoices', configurable: true });
    Object.defineProperty(getVoicesMock, 'toString', {
      value: function() { return 'function getVoices() {\\n    [native code]\\n}'; },
      configurable: true
    });
    Object.defineProperty(proto, 'getVoices', {
      value: getVoicesMock,
      writable: true,
      configurable: true,
      enumerable: true
    });

    // 2. Mock addEventListener on the prototype to handle voiceschanged
    const origAddEventListener = proto.addEventListener;
    const addEventListenerMock = function(type, listener, options) {
      const res = origAddEventListener.apply(this, arguments);
      if (type === 'voiceschanged') {
        setTimeout(() => {
          try {
            const event = new Event('voiceschanged');
            if (listener && typeof listener.handleEvent === 'function') {
              listener.handleEvent(event);
            } else if (typeof listener === 'function') {
              listener.call(this, event);
            }
          } catch(e) {}
        }, 100);
      }
      return res;
    };
    Object.defineProperty(addEventListenerMock, 'name', { value: 'addEventListener', configurable: true });
    Object.defineProperty(addEventListenerMock, 'toString', {
      value: function() { return 'function addEventListener() {\\n    [native code]\\n}'; },
      configurable: true
    });
    Object.defineProperty(proto, 'addEventListener', {
      value: addEventListenerMock,
      writable: true,
      configurable: true,
      enumerable: true
    });

    // 3. Mock onvoiceschanged property on prototype
    let onvoiceschangedHandler = null;
    Object.defineProperty(proto, 'onvoiceschanged', {
      get() { return onvoiceschangedHandler; },
      set(val) {
        onvoiceschangedHandler = val;
        if (typeof val === 'function') {
          setTimeout(() => {
            try {
              val.call(this, new Event('voiceschanged'));
            } catch(e) {}
          }, 100);
        }
      },
      configurable: true,
      enumerable: true
    });
  }

  // Fallback direct instance override
  if (window.speechSynthesis) {
    const getVoicesMock = function() { return voices; };
    Object.defineProperty(getVoicesMock, 'name', { value: 'getVoices', configurable: true });
    Object.defineProperty(getVoicesMock, 'toString', {
      value: function() { return 'function getVoices() {\\n    [native code]\\n}'; },
      configurable: true
    });
    Object.defineProperty(window.speechSynthesis, 'getVoices', {
      value: getVoicesMock,
      writable: true,
      configurable: true,
      enumerable: true
    });
  }
})();
"""

# ===========================================================================
# Public API
# ===========================================================================

@contextmanager
def launch_stealth_browser(
    proxy: Optional[Dict[str, str]] = None,
    geoip: Union[str, bool] = True,
    humanize: Union[bool, float] = 1.5,
    headless: bool = False,
    extra_config: Optional[Dict[str, Any]] = None,
    extra_prefs: Optional[Dict[str, Any]] = None,
    profile_name: Optional[str] = None,
    profiles_dir: str = "profiles",
):
    """
    Sync context manager -> yields a stealthy Camoufox browser (or persistent context if profile_name is provided).
    """
    kwargs = _common_kwargs(
        proxy=proxy, geoip=geoip, humanize=humanize, headless=headless,
        extra_config=extra_config, extra_prefs=extra_prefs,
        profile_name=profile_name, profiles_dir=profiles_dir,
    )
    persistent = "user_data_dir" in kwargs
    with Camoufox(persistent_context=persistent, **kwargs) as browser:
        if persistent:
            browser.add_init_script(INIT_STEALTH_JS)
        else:
            orig_new_context = browser.new_context
            def new_context(*args, **kwargs):
                ctx = orig_new_context(*args, **kwargs)
                ctx.add_init_script(INIT_STEALTH_JS)
                return ctx
            browser.new_context = new_context
            
            orig_new_page = browser.new_page
            def new_page(*args, **kwargs):
                page = orig_new_page(*args, **kwargs)
                page.context.add_init_script(INIT_STEALTH_JS)
                return page
            browser.new_page = new_page

        yield browser


@asynccontextmanager
async def launch_stealth_browser_async(
    proxy: Optional[Dict[str, str]] = None,
    geoip: Union[str, bool] = True,
    humanize: Union[bool, float] = 1.5,
    headless: bool = False,
    extra_config: Optional[Dict[str, Any]] = None,
    extra_prefs: Optional[Dict[str, Any]] = None,
    profile_name: Optional[str] = None,
    profiles_dir: str = "profiles",
):
    """
    Async context manager -> yields a stealthy Camoufox browser (or persistent context if profile_name is provided).
    """
    kwargs = _common_kwargs(
        proxy=proxy, geoip=geoip, humanize=humanize, headless=headless,
        extra_config=extra_config, extra_prefs=extra_prefs,
        profile_name=profile_name, profiles_dir=profiles_dir,
    )
    persistent = "user_data_dir" in kwargs
    async with AsyncCamoufox(persistent_context=persistent, **kwargs) as browser:
        if persistent:
            await browser.add_init_script(INIT_STEALTH_JS)
        else:
            orig_new_context = browser.new_context
            async def new_context(*args, **kwargs):
                ctx = await orig_new_context(*args, **kwargs)
                await ctx.add_init_script(INIT_STEALTH_JS)
                return ctx
            browser.new_context = new_context
            
            orig_new_page = browser.new_page
            async def new_page(*args, **kwargs):
                page = await orig_new_page(*args, **kwargs)
                await page.context.add_init_script(INIT_STEALTH_JS)
                return page
            browser.new_page = new_page

        yield browser


def get_stealth_options(
    proxy: Optional[Dict[str, str]] = None,
    geoip: Union[str, bool] = True,
    humanize: Union[bool, float] = 1.5,
    headless: bool = False,
    extra_config: Optional[Dict[str, Any]] = None,
    extra_prefs: Optional[Dict[str, Any]] = None,
    profile_name: Optional[str] = None,
    profiles_dir: str = "profiles",
) -> Dict[str, Any]:
    """
    Return the raw launch_options() dict without launching a browser.
    """
    kwargs = _common_kwargs(
        proxy=proxy, geoip=geoip, humanize=humanize, headless=headless,
        extra_config=extra_config, extra_prefs=extra_prefs,
        profile_name=profile_name, profiles_dir=profiles_dir,
    )
    return launch_options(**kwargs)


# ===========================================================================
# Smoke-test:  python camoufox_stealth.py
# ===========================================================================
if __name__ == "__main__":
    print("\n[camoufox_stealth] Building options in transient mode...")
    opts = get_stealth_options(geoip=False, headless=True)
    
    print(f"[OK] Total env variables: {len(opts.get('env', {}))}")
    print(f"[OK] User prefs configured: {len(opts.get('firefox_user_prefs', {}))}")
    
    print("\n[camoufox_stealth] Testing profile manager...")
    manager = StealthProfileManager("test_profiles")
    profile_path = manager.get_profile_path("test_account")
    print(f"[OK] Profile path: {profile_path}")
    
    manager.delete_profile("test_account")
    if Path("test_profiles").exists():
        Path("test_profiles").rmdir()
        
    print("\n[camoufox_stealth] All smoke tests passed successfully!")
