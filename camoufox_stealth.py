"""
camoufox_stealth.py
===================
Maximum-stealth Camoufox launcher & multi-profile manager.

This script is an opinionated, production-grade wrapper around the Camoufox package.
It implements persistent hardware identities, human mouse/scrolling/typing humanizers,
and high-stealth behavioral helpers.

Supports Windows (RDP) and Linux (Ubuntu RDP / headful X11 sessions).

Stealth Architecture & Guidelines:
-----------------------------------
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

Public Humanization Helpers:
----------------------------
- random_wait(page=None, min_sec=1.0, max_sec=4.0):
  Introduces human-like pauses between actions. Uses page.wait_for_timeout when page is passed.
- stealth_type(page, selector, text, error_rate=0.02):
  Types with random pauses, adjacent QWERTY key typo simulation, and realistic backspace corrections.
- stealth_scroll(page, direction="down", distance=400, steps=15):
  Performs dynamic page scrolling with sub-scrolling curves to simulate human reading speed and focus.
"""


from __future__ import annotations

import hashlib
import os
import platform
import sys
import time
import math
import random
import json
from pathlib import Path
from contextlib import asynccontextmanager, contextmanager
from typing import Any, Dict, List, Optional, Union

# ---------------------------------------------------------------------------
# Platform detection: map Python platform to Camoufox OS string
# ---------------------------------------------------------------------------
_PLATFORM = platform.system()  # 'Windows', 'Linux', 'Darwin'
_CAMOUFOX_OS = {
    "Windows": "windows",
    "Linux":   "linux",
    "Darwin":  "macos",
}.get(_PLATFORM, "linux")  # default to linux for unknown

# ---------------------------------------------------------------------------
# Camoufox imports
# ---------------------------------------------------------------------------
# Prefer the local (hardened) camoufox source over any pip-installed version.
# Our local copy has 62-key STEALTH_PREFS and all fingerprint hardening patches.
_LOCAL_CAMOUFOX = Path(os.path.dirname(os.path.abspath(__file__))) / 'camoufox'
if _LOCAL_CAMOUFOX.exists():
    import sys as _sys
    _local_str = str(_LOCAL_CAMOUFOX)
    if _local_str not in _sys.path:
        _sys.path.insert(0, _local_str)

try:
    from camoufox import AsyncCamoufox, Camoufox
    from camoufox.utils import launch_options, STEALTH_PREFS
    from camoufox.fingerprints import generate_fingerprint
    from browserforge.fingerprints import Screen
    def pick_realistic_screen():
        """Return a Screen constraint matching common Windows desktop resolutions."""
        import random
        res = random.choices(
            [(1920, 1080), (1536, 864), (1366, 768), (1440, 900), (1280, 720)],
            weights=[50, 20, 15, 10, 5], k=1
        )[0]
        return Screen(max_width=res[0], max_height=res[1])
except ImportError as exc:
    sys.exit(
        f"[camoufox_stealth] Missing dependency: {exc}\n"
        "Install with:  pip install camoufox[geoip]\n"
        "Then fetch the browser:  python -m camoufox fetch"
    )

# ---------------------------------------------------------------------------
# Load the deep-stealth JavaScript patch (covers the 4 remaining fingerprint
# surfaces: OffscreenCanvas/Workers, cross-origin iframes, WebGPU, font metrics)
# ---------------------------------------------------------------------------
_STEALTH_PATCH_PATH = Path(os.path.dirname(os.path.abspath(__file__))) / 'stealth_patch.js'
try:
    _STEALTH_PATCH_JS = _STEALTH_PATCH_PATH.read_text(encoding='utf-8')
except FileNotFoundError:
    _STEALTH_PATCH_JS = ''  # Gracefully degrade if file is missing
    print(f'[camoufox_stealth] Warning: stealth_patch.js not found at {_STEALTH_PATCH_PATH}')

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
# Per-profile stable constants generator
# ===========================================================================

def _profile_constants(profile_name: Optional[str] = None, profiles_dir: str = "profiles") -> Dict[str, Any]:
    """
    Generates stable per-profile values that are:
      - Consistent: same profile name → same values every launch
      - Varied:     different profile names → different values
      - Realistic:  values within real-world observed ranges

    Uses SHA-256 of the profile name as a deterministic seed so no extra
    files need to be read or written — zero overhead.
    Also checks if a cached profile exists to read and align the deviceMemory value.
    """
    seed_str = (profile_name or 'transient').encode('utf-8')
    seed_int = int(hashlib.sha256(seed_str).hexdigest()[:16], 16)
    rng = random.Random(seed_int)

    # Check for cached deviceMemory, audio:seed, and canvas:seed first
    cached_memory = None
    cached_audio_seed = None
    cached_canvas_seed = None
    if profile_name:
        try:
            manager = StealthProfileManager(profiles_dir)
            profile_path = manager.get_profile_path(profile_name)
            profile_file = profile_path / 'camoufox_profile.json'
            if profile_file.exists():
                with open(profile_file, 'rb') as pf:
                    cached_profile = json.loads(pf.read())
                cached_config = cached_profile.get('config', {})
                cached_memory = cached_config.get('_stealth:deviceMemory')
                cached_audio_seed = cached_config.get('audio:seed')
                cached_canvas_seed = cached_config.get('canvas:seed')
        except Exception:
            pass

    if profile_name is None:
        # Transient mode: generate fresh random values every launch
        fonts_seed = random.randint(1, 4_294_967_295)
        audio_seed = random.randint(1, 4_294_967_295)
        canvas_seed = random.randint(1, 4_294_967_295)
        aa_offset = random.randint(-50, 50)
        device_memory = random.choices([4, 8, 16], weights=[10, 50, 40], k=1)[0]
        # Storage estimate
        base_gb = random.randint(22, 105)
        jitter_mb = random.randint(1, 1023)
        byte_tail = random.randint(0, 1_048_575)
        storage_quota = base_gb * 1_073_741_824 + jitter_mb * 1_048_576 - byte_tail
        # winVersion
        win_version = random.choices(["10", "11"], weights=[40, 60], k=1)[0]
        if win_version == "10":
            platform_version = "10.0.0"
            patch_level = random.randint(100, 3999)
            full_build = f"19045.{patch_level}"
        else:
            platform_version = random.choices(["14.0.0", "15.0.0", "16.0.0"], weights=[15, 60, 25], k=1)[0]
            build_map = {"14.0.0": 22000, "15.0.0": 22621, "16.0.0": 26100}
            base_build = build_map[platform_version]
            patch_level = random.randint(100, 3999)
            full_build = f"{base_build}.{patch_level}"
    else:
        # Persistent profile mode: deterministic from seed
        fonts_seed = rng.randint(1, 4_294_967_295)
        det_audio_seed = rng.randint(1, 4_294_967_295)
        audio_seed = cached_audio_seed if cached_audio_seed is not None else det_audio_seed
        det_canvas_seed = rng.randint(1, 4_294_967_295)
        canvas_seed = cached_canvas_seed if cached_canvas_seed is not None else det_canvas_seed
        aa_offset = rng.randint(-50, 50)
        det_device_memory = rng.choices([4, 8, 16], weights=[10, 50, 40], k=1)[0]
        device_memory = cached_memory if cached_memory is not None else det_device_memory
        # Storage estimate
        base_gb = rng.randint(22, 105)
        jitter_mb = rng.randint(1, 1023)
        byte_tail = rng.randint(0, 1_048_575)
        storage_quota = base_gb * 1_073_741_824 + jitter_mb * 1_048_576 - byte_tail
        # winVersion
        win_version = rng.choices(["10", "11"], weights=[40, 60], k=1)[0]
        if win_version == "10":
            platform_version = "10.0.0"
            patch_level = rng.randint(100, 3999)
            full_build = f"19045.{patch_level}"
        else:
            platform_version = rng.choices(["14.0.0", "15.0.0", "16.0.0"], weights=[15, 60, 25], k=1)[0]
            build_map = {"14.0.0": 22000, "15.0.0": 22621, "16.0.0": 26100}
            base_build = build_map[platform_version]
            patch_level = rng.randint(100, 3999)
            full_build = f"{base_build}.{patch_level}"

    return {
        'platformVersion': platform_version,
        'fullBuild':       full_build,
        'storageQuota':    storage_quota,
        'fontsSeed':       fonts_seed,
        'audioSeed':       audio_seed,
        'canvasSeed':      canvas_seed,
        'canvasAaOffset':  aa_offset,
        'winVersion':      win_version,
        'deviceMemory':    device_memory,
    }




# ===========================================================================
# Internal: config key validator (version-agnostic)
# ===========================================================================

def _validate_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Strips any config keys not supported by the currently installed camoufox version.
    Uses camoufox's own _load_properties() (reads properties.json) — fast, no
    fingerprint generation, no warnings. Falls back to returning config unchanged
    if the internal function is not accessible.
    """
    try:
        from camoufox.utils import _load_properties
        valid_keys = _load_properties()  # dict of {property_name: type}
    except Exception:
        return config  # can't validate — pass everything through unchanged

    filtered = {}
    for key, val in config.items():
        if key in valid_keys:
            filtered[key] = val
        else:
            print(f"[camoufox_stealth] Note: config key '{key}' not supported by this camoufox version — skipping.")
    return filtered


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
    consts = _profile_constants(profile_name, profiles_dir)


    base_config: Dict[str, Any] = {
        "voices:blockIfNotDefined": False,
        "voices:fakeCompletion": True,
        "voices": [
            # Legacy SAPI desktop voices (present on all Windows 10/11)
            {
                "voiceURI": "urn:moz-tts:sapi:Microsoft David Desktop - English (United States)?en-US",
                "name": "Microsoft David Desktop - English (United States)",
                "lang": "en-US",
                "localService": True,
                "default": True
            },
            {
                "voiceURI": "urn:moz-tts:sapi:Microsoft Zira Desktop - English (United States)?en-US",
                "name": "Microsoft Zira Desktop - English (United States)",
                "lang": "en-US",
                "localService": True,
                "default": False
            },
            {
                "voiceURI": "urn:moz-tts:sapi:Microsoft Mark Desktop - English (United States)?en-US",
                "name": "Microsoft Mark Desktop - English (United States)",
                "lang": "en-US",
                "localService": True,
                "default": False
            },
            # Neural (natural) voices — added automatically on Windows 11 22H2+
            {
                "voiceURI": "urn:moz-tts:sapi:Microsoft Aria Online (Natural) - English (United States)?en-US",
                "name": "Microsoft Aria Online (Natural) - English (United States)",
                "lang": "en-US",
                "localService": False,
                "default": False
            },
            {
                "voiceURI": "urn:moz-tts:sapi:Microsoft Guy Online (Natural) - English (United States)?en-US",
                "name": "Microsoft Guy Online (Natural) - English (United States)",
                "lang": "en-US",
                "localService": False,
                "default": False
            },
            {
                "voiceURI": "urn:moz-tts:sapi:Microsoft Jenny Online (Natural) - English (United States)?en-US",
                "name": "Microsoft Jenny Online (Natural) - English (United States)",
                "lang": "en-US",
                "localService": False,
                "default": False
            }
        ],
        "fonts:spacing_seed": consts['fontsSeed'],
        "audio:seed": consts['audioSeed'],
        "canvas:seed": consts['canvasSeed'],
        "canvas:aaOffset": consts['canvasAaOffset'],
        "canvas:aaCapOffset": True,
        "_stealth:deviceMemory": consts['deviceMemory'],
    }


    if extra_config:
        base_config.update(extra_config)

    # Strip any config keys unsupported by the installed camoufox version
    base_config = _validate_config(base_config)

    extra_firefox_prefs: Dict[str, Any] = {
        "media.webspeech.synth.enabled": True,
        "media.webspeech.synth.force_global_queue": False,
    }
    if extra_prefs:
        extra_firefox_prefs.update(extra_prefs)

    kwargs: Dict[str, Any] = {
        "os": _CAMOUFOX_OS,
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

    # Linux (Ubuntu RDP): fix RDD sandbox crash and software rendering failure.
    # - MOZ_DISABLE_RDD_SANDBOX: stops seccomp from blocking syscalls in the
    #   Remote Data Decoder process (causes immediate crash on RDP without GPU).
    # - LIBGL_ALWAYS_SOFTWARE: forces Mesa software GL so Firefox doesn't need
    #   a real GPU to render (fixes "RenderCompositorSWGL failed mapping default
    #   framebuffer, no dt" crash on VMs / RDP sessions).
    if _PLATFORM == "Linux":
        import os
        custom_env = os.environ.copy()
        custom_env.update({
            "MOZ_DISABLE_SANDBOX": "1",
            "LIBGL_ALWAYS_SOFTWARE": "1",
        })
        kwargs["env"] = custom_env

    # Compute stable per-profile constants and attach them so the launchers
    # can inject them as window.__camou_profile before stealth_patch.js runs.
    kwargs['_profile_consts'] = _profile_constants(profile_name, profiles_dir)

    return kwargs


# ===========================================================================
# JS Stealth Init Script (RDP Speech Synthesis Mock)
# ===========================================================================

INIT_STEALTH_JS = """
(() => {
  if (typeof window === 'undefined') return;
  console.log('[speech-mock] injected, SpeechSynthesis:', typeof SpeechSynthesis);

  // Helper: make a function look like a native built-in to toString() checks
  const nativify = (fn, nativeName) => {
    Object.defineProperty(fn, 'name', { value: nativeName, configurable: true });
    Object.defineProperty(fn, 'toString', {
      value: () => `function ${nativeName}() {\\n    [native code]\\n}`,
      configurable: true
    });
    return fn;
  };

  // ------------------------------------------------------------------
  // 1. Build prototype-conformant SpeechSynthesisVoice objects
  //    using a WeakMap to store data and prototype getters to return it.
  // ------------------------------------------------------------------
  const voiceProto = (typeof SpeechSynthesisVoice !== 'undefined')
    ? SpeechSynthesisVoice.prototype
    : Object.prototype;

  const voiceStore = new WeakMap();

  if (typeof SpeechSynthesisVoice !== 'undefined') {
    const proto = SpeechSynthesisVoice.prototype;
    const props = ['voiceURI', 'name', 'lang', 'localService', 'default'];
    props.forEach(prop => {
      const origDesc = Object.getOwnPropertyDescriptor(proto, prop);
      const origGetter = origDesc && origDesc.get;
      Object.defineProperty(proto, prop, {
        get: nativify(function() {
          if (voiceStore.has(this)) {
            return voiceStore.get(this)[prop];
          }
          return origGetter ? origGetter.call(this) : undefined;
        }, `get ${prop}`),
        configurable: true,
        enumerable: true
      });
    });
  }

  const voiceData = [
    {
      voiceURI:     'urn:moz-tts:sapi:Microsoft David Desktop - English (United States)?en-US',
      name:         'Microsoft David Desktop - English (United States)',
      lang:         'en-US',
      localService: true,
      default:      true
    },
    {
      voiceURI:     'urn:moz-tts:sapi:Microsoft Zira Desktop - English (United States)?en-US',
      name:         'Microsoft Zira Desktop - English (United States)',
      lang:         'en-US',
      localService: true,
      default:      false
    },
    {
      voiceURI:     'urn:moz-tts:sapi:Microsoft Mark Desktop - English (United States)?en-US',
      name:         'Microsoft Mark Desktop - English (United States)',
      lang:         'en-US',
      localService: true,
      default:      false
    }
  ];

  const voices = voiceData.map(data => {
    const v = Object.create(voiceProto);
    voiceStore.set(v, data);
    return v;
  });


  // ------------------------------------------------------------------
  // 2. Patch SpeechSynthesis.prototype so every instance (including
  //    window.speechSynthesis) inherits the mocked getVoices.
  //    This preserves the prototype chain CreepJS inspects.
  // ------------------------------------------------------------------
  if (typeof SpeechSynthesis !== 'undefined') {
    const proto = SpeechSynthesis.prototype;

    // getVoices
    Object.defineProperty(proto, 'getVoices', {
      value:        nativify(() => voices, 'getVoices'),
      writable:     true,
      configurable: true,
      enumerable:   true
    });

    // addEventListener — intercept voiceschanged to fire immediately
    const _origAEL = proto.addEventListener;
    Object.defineProperty(proto, 'addEventListener', {
      value: nativify(function(type, listener, opts) {
        const res = _origAEL ? _origAEL.apply(this, arguments) : undefined;
        if (type === 'voiceschanged') {
          setTimeout(() => {
            try {
              const ev = new Event('voiceschanged');
              if (listener && typeof listener.handleEvent === 'function') {
                listener.handleEvent(ev);
              } else if (typeof listener === 'function') {
                listener.call(this, ev);
              }
            } catch (_) {}
          }, 50);
        }
        return res;
      }, 'addEventListener'),
      writable:     true,
      configurable: true,
      enumerable:   true
    });

    // onvoiceschanged setter — call handler immediately via setTimeout
    let _onvcHandler = null;
    Object.defineProperty(proto, 'onvoiceschanged', {
      get() { return _onvcHandler; },
      set(fn) {
        _onvcHandler = fn;
        if (typeof fn === 'function') {
          const self = this;
          setTimeout(() => {
            try { fn.call(self, new Event('voiceschanged')); } catch (_) {}
          }, 50);
        }
      },
      configurable: true,
      enumerable:   true
    });

    // speaking / pending / paused — make sure they read false (not undefined)
    for (const prop of ['speaking', 'pending', 'paused']) {
      if (!(prop in proto)) {
        Object.defineProperty(proto, prop, {
          get: () => false, configurable: true, enumerable: true
        });
      }
    }
  }

  // ------------------------------------------------------------------
  // 3. Object.defineProperty on window.speechSynthesis itself.
  //    Wrapping via a getter lets us keep the real SpeechSynthesis
  //    instance (so instanceof / prototype checks still pass) while
  //    guaranteeing getVoices always returns our mocked list.
  // ------------------------------------------------------------------
  if (typeof window.speechSynthesis !== 'undefined') {
    const _real = window.speechSynthesis;

    // Only redefine if the descriptor is configurable (it is in Firefox)
    const desc = Object.getOwnPropertyDescriptor(window, 'speechSynthesis');
    if (!desc || desc.configurable) {
      Object.defineProperty(window, 'speechSynthesis', {
        get: nativify(function() {
          // Patch getVoices on the real instance every time it's accessed
          // so late-binding code that bypassed the prototype still works.
          if (_real && typeof _real.getVoices === 'function') {
            Object.defineProperty(_real, 'getVoices', {
              value:        nativify(() => voices, 'getVoices'),
              writable:     true,
              configurable: true
            });
          }
          return _real;
        }, 'get speechSynthesis'),
        configurable: true,
        enumerable:   true
      });
    }
  }

  // ------------------------------------------------------------------
  // 4. Fire voiceschanged on the load event (async, just like real FF)
  //    so any code waiting on window 'load' to call getVoices works.
  // ------------------------------------------------------------------
  const _fireVC = () => {
    try {
      if (window.speechSynthesis && window.speechSynthesis.dispatchEvent) {
        window.speechSynthesis.dispatchEvent(new Event('voiceschanged'));
      }
    } catch (_) {}
  };
  if (document.readyState === 'complete') {
    setTimeout(_fireVC, 100);
  } else {
    window.addEventListener('load', () => setTimeout(_fireVC, 100), { once: true });
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
    profile_consts = kwargs.pop('_profile_consts', {})
    win_ver = profile_consts.get('winVersion', '10')
    os.environ['CAMOUFOX_STEALTH_WIN_VERSION'] = win_ver
    
    profile_js = f"window.__camou_profile = {json.dumps(profile_consts)};"
    persistent = "user_data_dir" in kwargs

    print(f"\n[camoufox_stealth] Launching sync context (profile: '{profile_name or 'transient'}') with seeds:")
    print(f"   - OS Version:  Windows {win_ver}")
    print(f"   - Audio seed:  {profile_consts.get('audioSeed')}")
    print(f"   - Canvas seed: {profile_consts.get('canvasSeed')}")
    print(f"   - Fonts seed:  {profile_consts.get('fontsSeed')}")
    print(f"   - Quota:       {profile_consts.get('storageQuota')} bytes\n")

    with Camoufox(persistent_context=persistent, **kwargs) as browser:
        if persistent:

            browser.add_init_script(profile_js)
            browser.add_init_script(INIT_STEALTH_JS)
            if _STEALTH_PATCH_JS:
                browser.add_init_script(_STEALTH_PATCH_JS)
            
            orig_new_page = browser.new_page
            def new_page(*args, **kwargs):
                page = orig_new_page(*args, **kwargs)
                page.context.add_init_script(profile_js)
                page.context.add_init_script(INIT_STEALTH_JS)
                if _STEALTH_PATCH_JS:
                    page.context.add_init_script(_STEALTH_PATCH_JS)
                return page
            browser.new_page = new_page
        else:
            orig_new_context = browser.new_context
            def new_context(*args, **kwargs):
                ctx = orig_new_context(*args, **kwargs)
                ctx.add_init_script(profile_js)
                ctx.add_init_script(INIT_STEALTH_JS)
                if _STEALTH_PATCH_JS:
                    ctx.add_init_script(_STEALTH_PATCH_JS)
                return ctx
            browser.new_context = new_context
            
            orig_new_page = browser.new_page
            def new_page(*args, **kwargs):
                page = orig_new_page(*args, **kwargs)
                page.context.add_init_script(profile_js)
                page.context.add_init_script(INIT_STEALTH_JS)
                if _STEALTH_PATCH_JS:
                    page.context.add_init_script(_STEALTH_PATCH_JS)
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
    profile_consts = kwargs.pop('_profile_consts', {})
    win_ver = profile_consts.get('winVersion', '10')
    os.environ['CAMOUFOX_STEALTH_WIN_VERSION'] = win_ver

    profile_js = f"window.__camou_profile = {json.dumps(profile_consts)};"
    persistent = "user_data_dir" in kwargs

    print(f"\n[camoufox_stealth] Launching async context (profile: '{profile_name or 'transient'}') with seeds:")
    print(f"   - OS Version:  Windows {win_ver}")
    print(f"   - Audio seed:  {profile_consts.get('audioSeed')}")
    print(f"   - Canvas seed: {profile_consts.get('canvasSeed')}")
    print(f"   - Fonts seed:  {profile_consts.get('fontsSeed')}")
    print(f"   - Quota:       {profile_consts.get('storageQuota')} bytes\n")

    async with AsyncCamoufox(persistent_context=persistent, **kwargs) as browser:
        if persistent:
            await browser.add_init_script(profile_js)
            await browser.add_init_script(INIT_STEALTH_JS)
            if _STEALTH_PATCH_JS:
                await browser.add_init_script(_STEALTH_PATCH_JS)
            
            orig_new_page = browser.new_page
            async def new_page(*args, **kwargs):
                page = await orig_new_page(*args, **kwargs)
                await page.context.add_init_script(profile_js)
                await page.context.add_init_script(INIT_STEALTH_JS)
                if _STEALTH_PATCH_JS:
                    await page.context.add_init_script(_STEALTH_PATCH_JS)
                return page
            browser.new_page = new_page
        else:
            orig_new_context = browser.new_context
            async def new_context(*args, **kwargs):
                ctx = await orig_new_context(*args, **kwargs)
                await ctx.add_init_script(profile_js)
                await ctx.add_init_script(INIT_STEALTH_JS)
                if _STEALTH_PATCH_JS:
                    await ctx.add_init_script(_STEALTH_PATCH_JS)
                return ctx
            browser.new_context = new_context
            
            orig_new_page = browser.new_page
            async def new_page(*args, **kwargs):
                page = await orig_new_page(*args, **kwargs)
                await page.context.add_init_script(profile_js)
                await page.context.add_init_script(INIT_STEALTH_JS)
                if _STEALTH_PATCH_JS:
                    await page.context.add_init_script(_STEALTH_PATCH_JS)
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
    profile_consts = kwargs.pop('_profile_consts', {})
    win_ver = profile_consts.get('winVersion', '10')
    os.environ['CAMOUFOX_STEALTH_WIN_VERSION'] = win_ver
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
