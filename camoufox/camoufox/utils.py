import os
import sys
from os import environ
from os.path import abspath
from pathlib import Path
from pprint import pprint
from random import choices, randint, randrange
from typing import Any, Dict, List, Literal, Optional, Tuple, Union, cast

import numpy as np
import orjson
from browserforge.fingerprints import Fingerprint, Screen
from screeninfo import get_monitors
from typing_extensions import TypeAlias
from ua_parser import user_agent_parser

from .addons import DefaultAddons, add_default_addons, confirm_paths
from .exceptions import (
    InvalidOS,
    InvalidPropertyType,
    NonFirefoxFingerprint,
    UnknownProperty,
)
from .fingerprints import from_browserforge, generate_fingerprint
from .ip import Proxy, public_ip, valid_ipv4, valid_ipv6
from .locale import geoip_allowed, get_geolocation, handle_locales
from .pkgman import OS_NAME, get_path, installed_verstr, launch_path
from .virtdisplay import VirtualDisplay
from .warnings import LeakWarning
from .webgl import sample_webgl

ListOrString: TypeAlias = Union[Tuple[str, ...], List[str], str]

# Camoufox preferences to cache previous pages and requests
CACHE_PREFS = {
    'browser.sessionhistory.max_entries': 10,
    'browser.sessionhistory.max_total_viewers': -1,
    'browser.cache.memory.enable': True,
    'browser.cache.disk_cache_ssl': True,
    'browser.cache.disk.smart_size.enabled': True,
}

# ---------------------------------------------------------------------------
# Hardened Firefox user-prefs injected into every launch for maximum stealth.
# These kill telemetry, safe-browsing URL leaks, crash pings, captive-portal
# probes, push/notification APIs, and update nags — none of which a real user
# notices but all of which create detectable network noise.
# ---------------------------------------------------------------------------
STEALTH_PREFS = {
    # -----------------------------------------------------------------------
    # Telemetry & data reporting
    # -----------------------------------------------------------------------
    'toolkit.telemetry.enabled': False,
    'toolkit.telemetry.unified': False,
    'toolkit.telemetry.server': 'data:,',
    'toolkit.telemetry.archive.enabled': False,
    'toolkit.telemetry.newProfilePing.enabled': False,
    'toolkit.telemetry.shutdownPingSender.enabled': False,
    'toolkit.telemetry.updatePing.enabled': False,
    'toolkit.telemetry.bhrPing.enabled': False,
    'toolkit.telemetry.firstShutdownPing.enabled': False,
    'datareporting.healthreport.uploadEnabled': False,
    'datareporting.policy.dataSubmissionEnabled': False,
    'app.shield.optoutstudies.enabled': False,
    'app.normandy.enabled': False,
    'app.normandy.api_url': '',

    # -----------------------------------------------------------------------
    # Google Safe Browsing (leaks visited URLs to Google)
    # -----------------------------------------------------------------------
    'browser.safebrowsing.malware.enabled': False,
    'browser.safebrowsing.phishing.enabled': False,
    'browser.safebrowsing.blockedURIs.enabled': False,
    'browser.safebrowsing.provider.google4.gethashURL': '',
    'browser.safebrowsing.provider.google4.updateURL': '',

    # -----------------------------------------------------------------------
    # Captive portal & connectivity probes
    # -----------------------------------------------------------------------
    'network.captive-portal-service.enabled': False,
    'network.connectivity-service.enabled': False,

    # -----------------------------------------------------------------------
    # Crash reporting
    # -----------------------------------------------------------------------
    'browser.crashReports.unsubmittedCheck.autoSubmit2': False,
    'browser.tabs.crashReporting.sendReport': False,

    # -----------------------------------------------------------------------
    # Push / web notifications
    # -----------------------------------------------------------------------
    'dom.webnotifications.enabled': False,
    'dom.push.enabled': False,

    # -----------------------------------------------------------------------
    # Ping & beacon APIs (tracking vectors)
    # -----------------------------------------------------------------------
    'browser.send_pings': False,
    'beacon.enabled': False,

    # -----------------------------------------------------------------------
    # Geo JS API (Camoufox mocks this itself)
    # -----------------------------------------------------------------------
    'geo.enabled': False,
    'permissions.default.geo': 1,

    # -----------------------------------------------------------------------
    # Update nags
    # -----------------------------------------------------------------------
    'app.update.enabled': False,
    'app.update.auto': False,
    'browser.search.update': False,

    # -----------------------------------------------------------------------
    # WebRTC: only expose the default route, preventing local IP leaks
    # -----------------------------------------------------------------------
    'media.peerconnection.ice.default_address_only': True,
    'media.peerconnection.ice.no_host': False,

    # -----------------------------------------------------------------------
    # Extensions: standard scope, no silent auto-disable
    # -----------------------------------------------------------------------
    'extensions.autoDisableScopes': 15,
    'extensions.enabledScopes': 5,

    # -----------------------------------------------------------------------
    # [NEW] Timer precision hardening
    # High-resolution timers are a timing side-channel fingerprint surface.
    # Reducing precision to 1ms prevents sub-millisecond timing attacks while
    # keeping the browser functionally normal for real users.
    # -----------------------------------------------------------------------
    'privacy.reduceTimerPrecision': True,
    'privacy.resistFingerprinting.reduceTimerPrecision.jitter': True,
    'privacy.resistFingerprinting.reduceTimerPrecision.microseconds': 1000,

    # -----------------------------------------------------------------------
    # [NEW] WebGPU - disable entirely until consistent spoofing is achievable.
    # WebGPU exposes GPU adapter info (vendor, architecture, device),
    # limits (maxTextureDimension2D etc.) and features - all must align
    # with the WebGL identity. Until patched, disable to avoid contradiction.
    # -----------------------------------------------------------------------
    'dom.webgpu.enabled': False,

    # -----------------------------------------------------------------------
    # [NEW] Bluetooth / USB / HID API enumeration
    # These APIs should not be available in a normal desktop browser session.
    # Their mere existence (or unusual behavior) fingerprints the environment.
    # -----------------------------------------------------------------------
    'dom.bluetooth.enabled': False,

    # -----------------------------------------------------------------------
    # [NEW] Gamepad API
    # navigator.getGamepads() timing and polling behavior is fingerprintable.
    # A normal desktop web session almost never has gamepads; disable it.
    # -----------------------------------------------------------------------
    'dom.gamepad.enabled': False,
    'dom.gamepad.extensions.enabled': False,

    # -----------------------------------------------------------------------
    # [NEW] Device Motion / Orientation Sensor APIs
    # These are mobile-only signals. On Windows desktop they must be absent.
    # Exposing them signals a bot environment or mismatched device profile.
    # -----------------------------------------------------------------------
    'device.sensors.enabled': False,
    'device.sensors.motion.enabled': False,
    'device.sensors.orientation.enabled': False,
    'device.sensors.proximity.enabled': False,
    'device.sensors.ambientLight.enabled': False,

    # -----------------------------------------------------------------------
    # [NEW] Network Information API (navigator.connection)
    # Exposes effectiveType ('4g'/'3g'), RTT, and downlink. A proxied session
    # should not claim '4g' with 5ms RTT. Disabling prevents this leak.
    # -----------------------------------------------------------------------
    'dom.netinfo.enabled': False,

    # -----------------------------------------------------------------------
    # [NEW] OffscreenCanvas & Worker privacy
    # Canvas noise spoofing in the main thread must propagate to Workers.
    # Firefox's privacy.resistFingerprinting in workers achieves this.
    # We enable just the worker-scoped timer/canvas precision, not the full
    # resist-fingerprinting mode which would make the UA report a generic one.
    # -----------------------------------------------------------------------
    'privacy.resistFingerprinting.reduceTimerPrecision.microsecondsInWorkers': 1000,

    # -----------------------------------------------------------------------
    # [NEW] Clipboard API
    # Clipboard read/write permission state must be consistent with the
    # Permissions API. Default to 'denied' to match most normal desktop
    # Chrome and Firefox profiles that haven't granted clipboard access.
    # -----------------------------------------------------------------------
    'permissions.default.shortcuts': 2,  # deny keyboard-shortcut override

    # -----------------------------------------------------------------------
    # [NEW] CSS forced-colors / prefers-contrast consistency
    # Windows 11 normal user: forced-colors = none, contrast = no-preference.
    # This is already partially set per-profile but ensures a safe default.
    # -----------------------------------------------------------------------
    'ui.useOverlayScrollbars': 0,
    'browser.display.use_system_colors': False,

    # -----------------------------------------------------------------------
    # [NEW] Disable speculative pre-connections that leak browsing intent
    # -----------------------------------------------------------------------
    'network.http.speculative-parallel-limit': 0,
    'network.prefetch-next': False,
    'network.dns.disablePrefetch': True,
    'network.predictor.enabled': False,

    # -----------------------------------------------------------------------
    # [NEW] Disable Web Speech API recognition (server-side, leaks audio)
    # Note: SpeechSynthesis (local TTS) is kept — it's spoofed by Camoufox.
    # -----------------------------------------------------------------------
    'media.webspeech.recognition.enable': False,
    'media.webspeech.recognition.force_enable': False,

    # -----------------------------------------------------------------------
    # [NEW] Resist storage partitioning probes used for cross-site tracking
    # -----------------------------------------------------------------------
    'privacy.partition.network_state': True,
    'privacy.partition.serviceWorkers': True,
    'privacy.partition.always_partition_third_party_non_cookie_storage': True,
}

# Increment this whenever the cached fingerprint schema changes.
# Any profile whose saved version != PROFILE_VERSION will be regenerated.
PROFILE_VERSION = 10

# Screen resolutions considered "laptop" for GPU/core correlation
_LAPTOP_RESOLUTIONS = {(1366, 768), (1536, 864), (1440, 900), (1280, 720), (1280, 800)}


def get_env_vars(
    config_map: Dict[str, str], user_agent_os: str
) -> Dict[str, Union[str, float, bool]]:
    """
    Gets a dictionary of environment variables for Camoufox.
    """
    env_vars: Dict[str, Union[str, float, bool]] = {}
    try:
        updated_config_data = orjson.dumps(config_map)
    except orjson.JSONEncodeError as e:
        print(f"Error updating config: {e}")
        sys.exit(1)

    # Split the config into chunks
    chunk_size = 2047 if OS_NAME == 'win' else 32767
    config_str = updated_config_data.decode('utf-8')

    for i in range(0, len(config_str), chunk_size):
        chunk = config_str[i : i + chunk_size]
        env_name = f"CAMOU_CONFIG_{(i // chunk_size) + 1}"
        try:
            env_vars[env_name] = chunk
        except Exception as e:
            print(f"Error setting {env_name}: {e}")
            sys.exit(1)

    if OS_NAME == 'lin':
        fontconfig_path = get_path(os.path.join("fontconfig", user_agent_os))
        env_vars['FONTCONFIG_PATH'] = fontconfig_path

    return env_vars


def _load_properties(path: Optional[Path] = None) -> Dict[str, str]:
    """
    Loads the properties.json file.
    """
    if path:
        prop_file = str(path.parent / "properties.json")
    else:
        prop_file = get_path("properties.json")
    with open(prop_file, "rb") as f:
        prop_dict = orjson.loads(f.read())

    return {prop['property']: prop['type'] for prop in prop_dict}


def validate_config(config_map: Dict[str, str], path: Optional[Path] = None) -> None:
    """
    Validates the config map.
    """
    property_types = _load_properties(path=path)

    for key, value in config_map.items():
        expected_type = property_types.get(key)
        if not expected_type:
            raise UnknownProperty(f"Unknown property {key} in config")

        if not validate_type(value, expected_type):
            raise InvalidPropertyType(
                f"Invalid type for property {key}. Expected {expected_type}, got {type(value).__name__}"
            )


def validate_type(value: Any, expected_type: str) -> bool:
    """
    Validates the type of the value.
    """
    if expected_type == "str":
        return isinstance(value, str)
    elif expected_type == "int":
        return isinstance(value, int) or (isinstance(value, float) and value.is_integer())
    elif expected_type == "uint":
        return (
            isinstance(value, int) or (isinstance(value, float) and value.is_integer())
        ) and value >= 0
    elif expected_type == "double":
        return isinstance(value, (float, int))
    elif expected_type == "bool":
        return isinstance(value, bool)
    elif expected_type == "array":
        return isinstance(value, list)
    elif expected_type == "dict":
        return isinstance(value, dict)
    else:
        return False


def get_target_os(config: Dict[str, Any]) -> Literal['mac', 'win', 'lin']:
    """
    Gets the OS from the config if the user agent is set,
    otherwise returns the OS of the current system.
    """
    if config.get("navigator.userAgent"):
        return determine_ua_os(config["navigator.userAgent"])
    return OS_NAME


def determine_ua_os(user_agent: str) -> Literal['mac', 'win', 'lin']:
    """
    Determines the OS from the user agent string.
    """
    parsed_ua = user_agent_parser.ParseOS(user_agent).get('family')
    if not parsed_ua:
        raise ValueError("Could not determine OS from user agent")
    if parsed_ua.startswith("Mac"):
        return "mac"
    if parsed_ua.startswith("Windows"):
        return "win"
    return "lin"


def get_screen_cons(headless: Optional[bool] = None) -> Optional[Screen]:
    """
    Determines a sane viewport size for Camoufox if being ran in headful mode.
    """
    if headless is False:
        return None  # Skip if headless
    try:
        monitors = get_monitors()
    except Exception:
        return None  # Skip if there's an error getting the monitors
    if not monitors:
        return None  # Skip if there are no monitors

    # Use the dimensions from the monitor with greatest screen real estate
    monitor = max(monitors, key=lambda m: m.width * m.height)
    return Screen(max_width=monitor.width, max_height=monitor.height)


def update_fonts(config: Dict[str, Any], target_os: str) -> None:
    """
    Updates the fonts for the target OS.
    """
    with open(os.path.join(os.path.dirname(__file__), "fonts.json"), "rb") as f:
        fonts = orjson.loads(f.read())[target_os]

    if target_os == 'win':
        # Filter out Windows 11-only fonts to align with the Windows 10 UA & oscpu signatures.
        # Segoe Fluent Icons, Segoe UI Variable, and HoloLens MDL2 Assets only exist in Win11.
        win11_fonts = {"Segoe Fluent Icons", "Segoe UI Variable", "HoloLens MDL2 Assets"}
        fonts = [font for font in fonts if font not in win11_fonts]

    # Merge with existing fonts
    if 'fonts' in config:
        config['fonts'] = np.unique(fonts + config['fonts']).tolist()
    else:
        config['fonts'] = fonts


def check_custom_fingerprint(fingerprint: Fingerprint) -> None:
    """
    Asserts that the passed BrowserForge fingerprint is a valid Firefox fingerprint.
    and warns the user that passing their own fingerprint is not recommended.
    """
    # Check what the browser is
    browser_name = user_agent_parser.ParseUserAgent(fingerprint.navigator.userAgent).get(
        'family', 'Non-Firefox'
    )
    if browser_name != 'Firefox':
        raise NonFirefoxFingerprint(
            f'"{browser_name}" fingerprints are not supported in Camoufox. '
            'Using fingerprints from a browser other than Firefox WILL lead to detection. '
            'If this is intentional, pass `i_know_what_im_doing=True`.'
        )

    LeakWarning.warn('custom_fingerprint', False)


def check_valid_os(os: ListOrString) -> None:
    """
    Checks if the target OS is valid.
    """
    if not isinstance(os, str):
        for os_name in os:
            check_valid_os(os_name)
        return
    # Assert that the OS is lowercase
    if not os.islower():
        raise InvalidOS(f"OS values must be lowercase: '{os}'")
    # Assert that the OS is supported by Camoufox
    if os not in ('windows', 'macos', 'linux'):
        raise InvalidOS(f"Camoufox does not support the OS: '{os}'")


def _clean_locals(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Gets the launch options from the locals of the function.
    """
    del data['playwright']
    del data['persistent_context']
    return data


def merge_into(target: Dict[str, Any], source: Dict[str, Any]) -> None:
    """
    Merges new keys/values from the source dictionary into the target dictionary.
    Given that the key does not exist in the target dictionary.
    """
    for key, value in source.items():
        if key not in target:
            target[key] = value


def set_into(target: Dict[str, Any], key: str, value: Any) -> None:
    """
    Sets a new key/value into the target dictionary.
    Given that the key does not exist in the target dictionary.
    """
    if key not in target:
        target[key] = value


def is_domain_set(
    config: Dict[str, Any],
    *properties: str,
) -> bool:
    """
    Checks if a domain is set in the config.
    """
    for prop in properties:
        # If the . prefix exists, check if the domain is a prefix of any key in the config
        if prop[-1] in ('.', ':'):
            if any(key.startswith(prop) for key in config):
                return True
        # Otherwise, check if the domain is a direct key in the config
        else:
            if prop in config:
                return True
    return False


def warn_manual_config(config: Dict[str, Any]) -> None:
    """
    Warns the user if they are manually setting properties that Camoufox already sets internally.
    """
    # Manual locale setting
    if is_domain_set(
        config, 'navigator.language', 'navigator.languages', 'headers.Accept-Language', 'locale:'
    ):
        LeakWarning.warn('locale', False)
    # Manual geolocation and timezone setting
    if is_domain_set(config, 'geolocation:', 'timezone'):
        LeakWarning.warn('geolocation', False)
    # Manual User-Agent setting
    if is_domain_set(config, 'headers.User-Agent'):
        LeakWarning.warn('header-ua', False)
    # Manual navigator setting
    if is_domain_set(config, 'navigator.'):
        LeakWarning.warn('navigator', False)
    # Manual screen/window setting
    if is_domain_set(config, 'screen.', 'window.', 'document.body.'):
        LeakWarning.warn('viewport', False)


async def async_attach_vd(
    browser: Any, virtual_display: Optional[VirtualDisplay] = None
) -> Any:  # type: ignore
    """
    Attaches the virtual display to the async browser cleanup
    """
    if not virtual_display:  # Skip if no virtual display is provided
        return browser

    _close = browser.close

    async def new_close(*args: Any, **kwargs: Any):
        await _close(*args, **kwargs)
        if virtual_display:
            virtual_display.kill()

    browser.close = new_close
    browser._virtual_display = virtual_display

    return browser


def sync_attach_vd(
    browser: Any, virtual_display: Optional[VirtualDisplay] = None
) -> Any:  # type: ignore
    """
    Attaches the virtual display to the sync browser cleanup
    """
    if not virtual_display:  # Skip if no virtual display is provided
        return browser

    _close = browser.close

    def new_close(*args: Any, **kwargs: Any):
        _close(*args, **kwargs)
        if virtual_display:
            virtual_display.kill()

    browser.close = new_close
    browser._virtual_display = virtual_display

    return browser


def launch_options(
    *,
    config: Optional[Dict[str, Any]] = None,
    os: Optional[ListOrString] = None,
    block_images: Optional[bool] = None,
    block_webrtc: Optional[bool] = None,
    block_webgl: Optional[bool] = None,
    disable_coop: Optional[bool] = None,
    webgl_config: Optional[Tuple[str, str]] = None,
    geoip: Optional[Union[str, bool]] = None,
    humanize: Optional[Union[bool, float]] = None,
    locale: Optional[Union[str, List[str]]] = None,
    addons: Optional[List[str]] = None,
    fonts: Optional[List[str]] = None,
    custom_fonts_only: Optional[bool] = None,
    exclude_addons: Optional[List[DefaultAddons]] = None,
    screen: Optional[Screen] = None,
    window: Optional[Tuple[int, int]] = None,
    fingerprint: Optional[Fingerprint] = None,
    ff_version: Optional[int] = None,
    headless: Optional[bool] = None,
    main_world_eval: Optional[bool] = None,
    executable_path: Optional[Union[str, Path]] = None,
    firefox_user_prefs: Optional[Dict[str, Any]] = None,
    proxy: Optional[Dict[str, str]] = None,
    enable_cache: Optional[bool] = None,
    args: Optional[List[str]] = None,
    env: Optional[Dict[str, Union[str, float, bool]]] = None,
    i_know_what_im_doing: Optional[bool] = None,
    debug: Optional[bool] = None,
    virtual_display: Optional[str] = None,
    **launch_options: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Launches a new browser instance for Camoufox.
    Accepts all Playwright Firefox launch options, along with the following:

    Parameters:
        config (Optional[Dict[str, Any]]):
            Camoufox properties to use. (read https://github.com/daijro/camoufox/blob/main/README.md)
        os (Optional[ListOrString]):
            Operating system to use for the fingerprint generation.
            Can be "windows", "macos", "linux", or a list to randomly choose from.
            Default: ["windows", "macos", "linux"]
        block_images (Optional[bool]):
            Whether to block all images.
        block_webrtc (Optional[bool]):
            Whether to block WebRTC entirely.
        block_webgl (Optional[bool]):
            Whether to block WebGL. To prevent leaks, only use this for special cases.
        disable_coop (Optional[bool]):
            Disables the Cross-Origin-Opener-Policy, allowing elements in cross-origin iframes,
            such as the Turnstile checkbox, to be clicked.
        geoip (Optional[Union[str, bool]]):
            Calculate longitude, latitude, timezone, country, & locale based on the IP address.
            Pass the target IP address to use, or `True` to find the IP address automatically.
        humanize (Optional[Union[bool, float]]):
            Humanize the cursor movement.
            Takes either `True`, or the MAX duration in seconds of the cursor movement.
            The cursor typically takes up to 1.5 seconds to move across the window.
        locale (Optional[Union[str, List[str]]]):
            Locale(s) to use in Camoufox. The first listed locale will be used for the Intl API.
        addons (Optional[List[str]]):
            List of Firefox addons to use.
        fonts (Optional[List[str]]):
            Fonts to load into Camoufox (in addition to the default fonts for the target `os`).
            Takes a list of font family names that are installed on the system.
        custom_fonts_only (Optional[bool]):
            If enabled, OS-specific system fonts will be not be passed to Camoufox.
        exclude_addons (Optional[List[DefaultAddons]]):
            Default addons to exclude. Passed as a list of camoufox.DefaultAddons enums.
        screen (Optional[Screen]):
            Constrains the screen dimensions of the generated fingerprint.
            Takes a browserforge.fingerprints.Screen instance.
        window (Optional[Tuple[int, int]]):
            Set a fixed window size instead of generating a random one
        fingerprint (Optional[Fingerprint]):
            Use a custom BrowserForge fingerprint. Note: Not all values will be implemented.
            If not provided, a random fingerprint will be generated based on the provided
            `os` & `screen` constraints.
        ff_version (Optional[int]):
            Firefox version to use. Defaults to the current Camoufox version.
            To prevent leaks, only use this for special cases.
        headless (Optional[bool]):
            Whether to run the browser in headless mode. Defaults to False.
            Note: If you are running linux, passing headless='virtual' to Camoufox & AsyncCamoufox
            will use Xvfb.
        main_world_eval (Optional[bool]):
            Whether to enable running scripts in the main world.
            To use this, prepend "mw:" to the script: page.evaluate("mw:" + script).
        executable_path (Optional[Union[str, Path]]):
            Custom Camoufox browser executable path.
        firefox_user_prefs (Optional[Dict[str, Any]]):
            Firefox user preferences to set.
        proxy (Optional[Dict[str, str]]):
            Proxy to use for the browser.
            Note: If geoip is True, a request will be sent through this proxy to find the target IP.
        enable_cache (Optional[bool]):
            Cache previous pages, requests, etc (uses more memory).
        args (Optional[List[str]]):
            Arguments to pass to the browser.
        env (Optional[Dict[str, Union[str, float, bool]]]):
            Environment variables to set.
        debug (Optional[bool]):
            Prints the config being sent to Camoufox.
        virtual_display (Optional[str]):
            Virtual display number. Ex: ':99'. This is handled by Camoufox & AsyncCamoufox.
        webgl_config (Optional[Tuple[str, str]]):
            Use a specific WebGL vendor/renderer pair. Passed as a tuple of (vendor, renderer).
        **launch_options (Dict[str, Any]):
            Additional Firefox launch options.
    """
    # Build the config
    if config is None:
        config = {}
    if locale is None:
        locale = 'en-US'

    original_keys = set(config.keys()) if config else set()

    modernized_gpu_vendor = None
    modernized_gpu_renderer = None

    # Set default values for optional arguments
    if headless is None:
        headless = False
    if addons is None:
        addons = []
    if args is None:
        args = []
    if firefox_user_prefs is None:
        firefox_user_prefs = {}
    if custom_fonts_only is None:
        custom_fonts_only = False
    if i_know_what_im_doing is None:
        i_know_what_im_doing = False
    if env is None:
        env = cast(Dict[str, Union[str, float, bool]], environ)
    if isinstance(executable_path, str):
        # Convert executable path to a Path object
        executable_path = Path(abspath(executable_path))

    # Handle virtual display
    if virtual_display:
        env['DISPLAY'] = virtual_display

    # Warn the user for manual config settings
    if not i_know_what_im_doing:
        warn_manual_config(config)

    # Assert the target OS is valid
    if os:
        check_valid_os(os)

    # webgl_config requires OS to be set
    elif webgl_config:
        raise ValueError('OS must be set when using webgl_config')

    # Add the default addons
    add_default_addons(addons, exclude_addons)

    # Confirm all addon paths are valid
    if addons:
        confirm_paths(addons)
        config['addons'] = addons

    # Get the Firefox version
    if ff_version:
        ff_version_str = str(ff_version)
        LeakWarning.warn('ff_version', i_know_what_im_doing)
    else:
        ff_version_str = installed_verstr().split('.', 1)[0]

    # Persistent Context Profile Cache (Load)
    user_data_dir = launch_options.get('user_data_dir')
    profile_loaded = False

    if user_data_dir:
        profile_path = Path(user_data_dir)
        profile_file = profile_path / 'camoufox_profile.json'
        if profile_file.exists():
            try:
                with open(profile_file, 'rb') as pf:
                    cached_profile = orjson.loads(pf.read())

                if cached_profile.get('version') != PROFILE_VERSION:
                    print(
                        f"[camoufox] Profile version mismatch "
                        f"(saved={cached_profile.get('version')}, current={PROFILE_VERSION}) "
                        "– regenerating fingerprint."
                    )
                    # Delete stale prefs.js so old DNT/GPC/etc. settings
                    # don't leak back from the Firefox profile directory.
                    stale_prefs = profile_path / 'prefs.js'
                    if stale_prefs.exists():
                        stale_prefs.unlink()
                        print("[camoufox] Deleted stale prefs.js")
                else:
                    # Load config — filter out any keys not in the current properties schema
                    # (prevents stale keys from older camoufox versions crashing the launch)
                    try:
                        _valid_keys = set(_load_properties().keys())
                    except Exception:
                        _valid_keys = None
                    cached_config = cached_profile.get('config', {})
                    for k, v in cached_config.items():
                        if k not in config:
                            if _valid_keys is None or k in _valid_keys:
                                config[k] = v

                    # Load firefox_user_prefs
                    cached_prefs = cached_profile.get('firefox_user_prefs', {})
                    for k, v in cached_prefs.items():
                        if k not in firefox_user_prefs:
                            firefox_user_prefs[k] = v

                    profile_loaded = True
            except Exception as e:
                print(f"[camoufox] Warning: Failed to load profile cache: {e}")

    # Generate a fingerprint
    if not profile_loaded:
        if fingerprint is None:
            fingerprint = generate_fingerprint(
                screen=screen or get_screen_cons(headless or 'DISPLAY' in env),
                window=window,
                os=os,
            )
        else:
            # Or use the one passed by the user
            if not i_know_what_im_doing:
                check_custom_fingerprint(fingerprint)

        # Inject the fingerprint into the config
        merge_into(
            config,
            from_browserforge(fingerprint, ff_version_str),
        )

    target_os = get_target_os(config)

    # Set a random window.history.length (2-9 simulates a real browsing session)
    set_into(config, 'window.history.length', randrange(2, 10))  # nosec

    # Inject realistic battery state: desktop/laptop plugged in and fully charged
    # This spoofes navigator.getBattery() to avoid a trivially detectable default.
    set_into(config, 'battery:charging', True)
    set_into(config, 'battery:chargingTime', 0)       # 0 = already full
    set_into(config, 'battery:dischargingTime', randint(3600, 18000))  # nosec

    # Windows desktops have 0 touch points; tablets would use 10.
    # Defaulting to 0 matches the vast majority of Windows desktop sessions.
    set_into(config, 'navigator.maxTouchPoints', 0)

    # Update fonts list
    if fonts:
        config['fonts'] = fonts

    if custom_fonts_only:
        firefox_user_prefs['gfx.bundled-fonts.activate'] = 0
        if fonts:
            # The user has passed their own fonts, and OS fonts are disabled.
            LeakWarning.warn('custom_fonts_only')
        else:
            # OS fonts are disabled, and the user has not passed their own fonts either.
            raise ValueError('No custom fonts were passed, but `custom_fonts_only` is enabled.')
    else:
        update_fonts(config, target_os)

    # Set Windows Specific Overrides
    if target_os == 'win' and not profile_loaded:
        # 1. Select screen resolution randomly based on weighted windows standards
        res_list = [(1920, 1080), (1536, 864), (1366, 768), (1440, 900), (1280, 720)]
        w, h = choices(res_list, weights=[50, 20, 15, 10, 5], k=1)[0]

        memory = config.get('_stealth:deviceMemory')

        # 2. Cohesive hardware pairing logic covering all GPUs, cores, and memory
        if (w, h) == (1920, 1080):
            # 1920x1080 could be High-end desktop, Mid-range desktop/laptop, or Budget office desktop
            sub_type = choices(["high", "mid", "budget"], weights=[40, 45, 15], k=1)[0]
            if sub_type == "high":
                ratio = 1.0
                cores = choices([8, 12, 16], weights=[60, 30, 10], k=1)[0]
                if memory is None:
                    memory = 16
                gpu_list = [
                    ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0)"),
                    ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Ti Direct3D11 vs_5_0 ps_5_0)"),
                    ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce RTX 3070 Direct3D11 vs_5_0 ps_5_0)"),
                    ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce RTX 3070 Ti Direct3D11 vs_5_0 ps_5_0)"),
                    ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce RTX 3080 Direct3D11 vs_5_0 ps_5_0)"),
                    ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce RTX 4060 Direct3D11 vs_5_0 ps_5_0)"),
                    ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce RTX 4060 Ti Direct3D11 vs_5_0 ps_5_0)"),
                    ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 Direct3D11 vs_5_0 ps_5_0)"),
                    ("Google Inc. (AMD)", "ANGLE (AMD, AMD Radeon RX 6700 XT Direct3D11 vs_5_0 ps_5_0)"),
                    ("Google Inc. (AMD)", "ANGLE (AMD, AMD Radeon RX 6800 XT Direct3D11 vs_5_0 ps_5_0)"),
                    ("Google Inc. (AMD)", "ANGLE (AMD, AMD Radeon RX 7700 XT Direct3D11 vs_5_0 ps_5_0)"),
                ]
            elif sub_type == "mid":
                ratio = 1.0
                cores = choices([6, 8], weights=[50, 50], k=1)[0]
                if memory is None:
                    memory = choices([8, 16], weights=[50, 50], k=1)[0]
                gpu_list = [
                    ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce GTX 1060 Direct3D11 vs_5_0 ps_5_0)"),
                    ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce GTX 1050 Ti Direct3D11 vs_5_0 ps_5_0)"),
                    ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce GTX 1650 Direct3D11 vs_5_0 ps_5_0)"),
                    ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 Direct3D11 vs_5_0 ps_5_0)"),
                    ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 Super Direct3D11 vs_5_0 ps_5_0)"),
                    ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 Ti Direct3D11 vs_5_0 ps_5_0)"),
                    ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce RTX 2060 Direct3D11 vs_5_0 ps_5_0)"),
                    ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce RTX 2060 Super Direct3D11 vs_5_0 ps_5_0)"),
                    ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce RTX 2070 Direct3D11 vs_5_0 ps_5_0)"),
                    ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce RTX 3050 Direct3D11 vs_5_0 ps_5_0)"),
                    ("Google Inc. (AMD)", "ANGLE (AMD, AMD Radeon RX 570 Direct3D11 vs_5_0 ps_5_0)"),
                    ("Google Inc. (AMD)", "ANGLE (AMD, AMD Radeon RX 580 Direct3D11 vs_5_0 ps_5_0)"),
                    ("Google Inc. (AMD)", "ANGLE (AMD, AMD Radeon RX 5500 XT Direct3D11 vs_5_0 ps_5_0)"),
                    ("Google Inc. (AMD)", "ANGLE (AMD, AMD Radeon RX 5600 XT Direct3D11 vs_5_0 ps_5_0)"),
                    ("Google Inc. (AMD)", "ANGLE (AMD, AMD Radeon RX 5700 XT Direct3D11 vs_5_0 ps_5_0)"),
                    ("Google Inc. (AMD)", "ANGLE (AMD, AMD Radeon RX 6600 Direct3D11 vs_5_0 ps_5_0)"),
                    ("Google Inc. (AMD)", "ANGLE (AMD, AMD Radeon RX 6600 XT Direct3D11 vs_5_0 ps_5_0)"),
                    ("Google Inc. (AMD)", "ANGLE (AMD, AMD Radeon RX 7600 Direct3D11 vs_5_0 ps_5_0)"),
                ]
            else:
                ratio = 1.0
                cores = choices([4, 6], weights=[60, 40], k=1)[0]
                if memory is None:
                    memory = choices([8, 16], weights=[70, 30], k=1)[0]
                gpu_list = [
                    ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0)"),
                    ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) UHD Graphics 730 Direct3D11 vs_5_0 ps_5_0)"),
                    ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) UHD Graphics 750 Direct3D11 vs_5_0 ps_5_0)"),
                    ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) UHD Graphics 770 Direct3D11 vs_5_0 ps_5_0)"),
                    ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) HD Graphics 630 Direct3D11 vs_5_0 ps_5_0)"),
                ]
        elif (w, h) == (1536, 864):
            # Modern thin and light laptop
            ratio = 1.25
            gpu_vendor, gpu_renderer = choices([
                ("Intel", "Iris Xe"),
                ("Intel", "UHD"),
                ("NVIDIA/Intel", "Dedicated"),
            ], weights=[60, 20, 20], k=1)[0]
            if gpu_vendor == "Intel" and gpu_renderer == "Iris Xe":
                cores = 8
                if memory is None:
                    memory = choices([8, 16], weights=[60, 40], k=1)[0]
                gpu_list = [("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) Iris(R) Xe Graphics Direct3D11 vs_5_0 ps_5_0)")]
            elif gpu_vendor == "Intel" and gpu_renderer == "UHD":
                cores = choices([4, 6], weights=[70, 30], k=1)[0]
                if memory is None:
                    memory = 8
                gpu_list = [
                    ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0)"),
                    ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) UHD Graphics 620 Direct3D11 vs_5_0 ps_5_0)"),
                ]
            else:
                cores = choices([6, 8], weights=[50, 50], k=1)[0]
                if memory is None:
                    memory = choices([8, 16], weights=[50, 50], k=1)[0]
                gpu_list = [
                    ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce GTX 1650 Direct3D11 vs_5_0 ps_5_0)"),
                    ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce RTX 3050 Direct3D11 vs_5_0 ps_5_0)"),
                ]
        elif (w, h) == (1366, 768):
            # Older budget laptop
            ratio = 1.0
            gpu_type = choices(["HD620", "UHD620/Other"], weights=[40, 60], k=1)[0]
            if gpu_type == "HD620":
                cores = choices([2, 4], weights=[40, 60], k=1)[0]
                if memory is None:
                    memory = choices([4, 8], weights=[50, 50], k=1)[0]
                gpu_list = [("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) HD Graphics 620 Direct3D11 vs_5_0 ps_5_0)")]
            else:
                cores = 4
                if memory is None:
                    memory = choices([4, 8], weights=[30, 70], k=1)[0]
                gpu_list = [
                    ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) UHD Graphics 620 Direct3D11 vs_5_0 ps_5_0)"),
                    ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) HD Graphics 630 Direct3D11 vs_5_0 ps_5_0)"),
                    ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) Iris(R) Plus Graphics 640 Direct3D11 vs_5_0 ps_5_0)"),
                ]
        elif (w, h) == (1440, 900):
            # Widescreen monitor
            ratio = 1.0
            cores = choices([4, 6], weights=[70, 30], k=1)[0]
            if memory is None:
                memory = choices([8, 16], weights=[60, 40], k=1)[0]
            gpu_list = [
                ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0)"),
                ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce GTX 1650 Direct3D11 vs_5_0 ps_5_0)"),
                ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) Iris(R) Plus Graphics 640 Direct3D11 vs_5_0 ps_5_0)"),
                ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) Iris(R) Plus Graphics 655 Direct3D11 vs_5_0 ps_5_0)"),
            ]
        else:
            # 1280x720 very old budget machine
            ratio = 1.0
            cores = choices([2, 4], weights=[60, 40], k=1)[0]
            if memory is None:
                memory = choices([4, 8], weights=[70, 30], k=1)[0]
            gpu_list = [
                ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) HD Graphics 520 Direct3D11 vs_5_0 ps_5_0)"),
                ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) HD Graphics 620 Direct3D11 vs_5_0 ps_5_0)"),
                ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) UHD Graphics 620 Direct3D11 vs_5_0 ps_5_0)"),
            ]


        # 3. Apply profile parameters
        config['screen.width'] = w
        config['screen.height'] = h
        config['screen.availWidth'] = w
        config['screen.availHeight'] = h - 40
        config['screen.availTop'] = 0
        config['screen.availLeft'] = 0

        config['screen.colorDepth'] = 24
        config['screen.pixelDepth'] = 24

        config['window.devicePixelRatio'] = ratio
        config['navigator.hardwareConcurrency'] = cores
        # navigator.deviceMemory is NOT in Camoufox's properties.json schema.
        # It is spoofed at the JS layer via stealth_patch.js using the `memory`
        # variable captured from this scope. Store it for the JS preamble below.
        # (memory variable is still used when building the Worker preamble string)
        config['_stealth:deviceMemory'] = memory



        chosen_gpu_vendor, chosen_gpu_renderer = choices(gpu_list)[0]
        modernized_gpu_vendor = chosen_gpu_vendor
        modernized_gpu_renderer = chosen_gpu_renderer

        # 4. Enforce consistent window/viewport dimensions (constrain strictly to screen bounds)
        is_maximized = choices([True, False], weights=[80, 20], k=1)[0]
        if is_maximized:
            outer_w = w
            outer_h = h - 40  # constrained by taskbar
            w_border = 0
            h_chrome = choices([80, 108, 120], weights=[50, 40, 10], k=1)[0]
            config['window.screenX'] = 0
            config['window.screenY'] = 0
        else:
            outer_w = randrange(800, w - 50)
            outer_h = randrange(600, h - 90)
            w_border = choices([8, 16], weights=[50, 50], k=1)[0]
            h_chrome = choices([80, 108, 120], weights=[50, 40, 10], k=1)[0]
            config['window.screenX'] = randrange(0, w - outer_w)
            config['window.screenY'] = randrange(0, h - 40 - outer_h)
            
        config['window.outerWidth'] = outer_w
        config['window.outerHeight'] = outer_h
        config['window.innerWidth'] = max(0, outer_w - w_border)
        config['window.innerHeight'] = max(0, outer_h - h_chrome)

        # Color schemes & preferences
        if 'layout.css.prefers-color-scheme.content-override' not in firefox_user_prefs:
            firefox_user_prefs['layout.css.prefers-color-scheme.content-override'] = choices([0, 1], weights=[50, 50], k=1)[0]
        if 'layout.css.prefers-reduced-motion.content-override' not in firefox_user_prefs:
            firefox_user_prefs['layout.css.prefers-reduced-motion.content-override'] = 0
        if 'layout.css.prefers-contrast.content-override' not in firefox_user_prefs:
            firefox_user_prefs['layout.css.prefers-contrast.content-override'] = 0

    if target_os == 'win':
        set_into(config, 'pdfViewerEnabled', True)
        set_into(config, 'navigator.cookieEnabled', True)

        # Always disable DNT and GPC – they signal a privacy-conscious technical
        # user which is a detectable pattern. Direct assignment ensures these
        # override anything loaded from the profile cache.
        firefox_user_prefs['privacy.donottrackheader.enabled'] = False
        firefox_user_prefs['privacy.globalprivacycontrol.enabled'] = False
        firefox_user_prefs['privacy.globalprivacycontrol.functionality.enabled'] = False
        
        # Override config properties to prevent the browser engine from injecting them
        config['navigator.doNotTrack'] = 'unspecified'
        config.pop('navigator.globalPrivacyControl', None)

        # Media devices – lock webcam to 1 so the device list never
        # changes between sessions (randomising caused it to disappear).
        set_into(config, 'mediaDevices:enabled', True)
        set_into(config, 'mediaDevices:micros', 1)
        set_into(config, 'mediaDevices:speakers', 1)
        set_into(config, 'mediaDevices:webcams', 1)

        # Speech synthesis – force these on every launch so they are never
        # absent, even when restoring from an older profile cache.
        config['voices:blockIfNotDefined'] = False
        config['voices:fakeCompletion'] = True
        if 'voices' not in original_keys:
            # Windows 11 exposes a mix of legacy SAPI desktop voices and
            # newer neural voices (installed automatically on Win11 22H2+).
            # Including both tiers makes the voice fingerprint match a real
            # Windows 11 machine and prevents the trivially short 2-voice list
            # from being a uniqueness signal.
            config['voices'] = [
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
                },
                {
                    "voiceURI": "urn:moz-tts:sapi:Microsoft Ana Online (Natural) - English (United States)?en-US",
                    "name": "Microsoft Ana Online (Natural) - English (United States)",
                    "lang": "en-US",
                    "localService": False,
                    "default": False
                }
            ]

    if not profile_loaded:
        # Set spacing seed for font fingerprinting (fully supported in binary)
        set_into(config, 'fonts:spacing_seed', randint(1, 4_294_967_295))  # nosec


    # Set geolocation
    if geoip:
        geoip_allowed()  # Assert that geoip is allowed

        if geoip is True:
            # Find the user's IP address
            if proxy:
                geoip = public_ip(Proxy(**proxy).as_string())
            else:
                geoip = public_ip()

        # Spoof WebRTC if not blocked
        if not block_webrtc:
            if valid_ipv4(geoip):
                set_into(config, 'webrtc:ipv4', geoip)
                firefox_user_prefs['network.dns.disableIPv6'] = True
            elif valid_ipv6(geoip):
                set_into(config, 'webrtc:ipv6', geoip)

        geolocation = get_geolocation(geoip)
        config.update(geolocation.as_config())
        
        # Override geolocation locale to en-US for Windows users
        if target_os == 'win' and locale == 'en-US':
            config['locale:language'] = 'en'
            config['locale:region'] = 'US'
            if 'locale:script' in config:
                del config['locale:script']

    # Raise a warning when a proxy is being used without spoofing geolocation.
    # This is a very bad idea; the warning cannot be ignored with i_know_what_im_doing.
    elif (
        proxy
        and 'localhost' not in proxy.get('server', '')
        and not is_domain_set(config, 'geolocation')
    ):
        LeakWarning.warn('proxy_without_geoip')

    # Set locale
    if locale:
        handle_locales(locale, config)

    # Align Accept-Language & navigator.languages with determined locale
    lang = config.get('locale:language')
    region = config.get('locale:region')
    if lang:
        primary = f"{lang}-{region}" if region else lang
        set_into(config, 'navigator.language', primary)
        
        # Build languages array with fallbacks
        languages = [primary]
        if region:
            languages.append(lang)
        if primary != 'en-US':
            languages.append('en-US')
        if lang != 'en':
            languages.append('en')
        
        unique_languages = []
        for l in languages:
            if l not in unique_languages:
                unique_languages.append(l)
        
        set_into(config, 'navigator.languages', unique_languages)
        
        q_parts = []
        for idx, l in enumerate(unique_languages):
            if idx == 0:
                q_parts.append(l)
            else:
                q = 1.0 - (idx * 0.1)
                q_parts.append(f"{l};q={q:.1f}")
        accept_lang = ",".join(q_parts)
        set_into(config, 'headers.Accept-Language', accept_lang)
        firefox_user_prefs['intl.accept_languages'] = accept_lang
        if primary == 'en-US':
            firefox_user_prefs['javascript.use_us_english_locale'] = True




    # Pass the humanize option
    if humanize:
        set_into(config, 'humanize', True)
        if isinstance(humanize, (int, float)):
            set_into(config, 'humanize:maxTime', humanize)

    # Enable the main world context creation
    if main_world_eval:
        set_into(config, 'allowMainWorld', True)

    # Set Firefox user preferences
    if block_images:
        LeakWarning.warn('block_images', i_know_what_im_doing)
        firefox_user_prefs['permissions.default.image'] = 2
    if block_webrtc:
        firefox_user_prefs['media.peerconnection.enabled'] = False
    if disable_coop:
        LeakWarning.warn('disable_coop', i_know_what_im_doing)
        firefox_user_prefs['browser.tabs.remote.useCrossOriginOpenerPolicy'] = False

    # Allow allow_webgl parameter for backwards compatibility
    if block_webgl or launch_options.pop('allow_webgl', True) is False:
        firefox_user_prefs['webgl.disabled'] = True
        LeakWarning.warn('block_webgl', i_know_what_im_doing)
    else:
        if not profile_loaded:
            # If the user has provided a specific WebGL vendor/renderer pair, use it
            if webgl_config:
                webgl_fp = sample_webgl(target_os, *webgl_config)
            else:
                webgl_fp = sample_webgl(target_os)
                
            # Intercept and modernize GPU if target OS is Windows.
            # The GPU model is correlated with the screen resolution:
            #   laptop resolutions  → Intel Iris Xe  (or UHD 620)
            #   desktop resolutions → Intel UHD 630  (or UHD 770)
            if target_os == 'win' and not webgl_config:
                if modernized_gpu_vendor and modernized_gpu_renderer:
                    vendor = modernized_gpu_vendor
                    renderer = modernized_gpu_renderer
                else:
                    cur_w = config.get('screen.width', 1920)
                    cur_h = config.get('screen.height', 1080)
                    is_laptop = (cur_w, cur_h) in _LAPTOP_RESOLUTIONS

                    vendor = webgl_fp.get('webGl:vendor', '')
                    orig_renderer = webgl_fp.get('webGl:renderer', '')
                    gpu_text = vendor + ' ' + orig_renderer  # cast a wider net

                    if 'NVIDIA' in gpu_text or 'GeForce' in gpu_text:
                        if is_laptop:
                            models = [
                                'NVIDIA GeForce GTX 1650 Direct3D11 vs_5_0 ps_5_0',
                                'NVIDIA GeForce RTX 3050 Direct3D11 vs_5_0 ps_5_0',
                            ]
                        else:
                            models = [
                                'NVIDIA GeForce RTX 4070 Direct3D11 vs_5_0 ps_5_0',
                                'NVIDIA GeForce RTX 4060 Direct3D11 vs_5_0 ps_5_0',
                                'NVIDIA GeForce RTX 3070 Direct3D11 vs_5_0 ps_5_0',
                                'NVIDIA GeForce RTX 3060 Ti Direct3D11 vs_5_0 ps_5_0',
                                'NVIDIA GeForce GTX 1660 Super Direct3D11 vs_5_0 ps_5_0',
                            ]
                        chosen = models[randrange(len(models))]
                        renderer = f"ANGLE (NVIDIA, {chosen})"
                        vendor = 'Google Inc. (NVIDIA)'

                    elif 'AMD' in gpu_text or 'ATI' in gpu_text or 'Radeon' in gpu_text:
                        if is_laptop:
                            models = [
                                'AMD Radeon RX 6600M Direct3D11 vs_5_0 ps_5_0',
                                'AMD Radeon RX 5500 XT Direct3D11 vs_5_0 ps_5_0',
                            ]
                        else:
                            models = [
                                'AMD Radeon RX 580 Direct3D11 vs_5_0 ps_5_0',
                                'AMD Radeon RX 6600 Direct3D11 vs_5_0 ps_5_0',
                                'AMD Radeon RX 6700 XT Direct3D11 vs_5_0 ps_5_0',
                                'AMD Radeon RX 7600 Direct3D11 vs_5_0 ps_5_0',
                            ]
                        chosen = models[randrange(len(models))]
                        renderer = f"ANGLE (AMD, {chosen})"
                        vendor = 'Google Inc. (AMD)'

                    elif 'Intel' in gpu_text:
                        if is_laptop:
                            models = [
                                'Intel(R) Iris(R) Xe Graphics Direct3D11 vs_5_0 ps_5_0',
                                'Intel(R) UHD Graphics 620 Direct3D11 vs_5_0 ps_5_0',
                            ]
                            weights = [70, 30]
                        else:
                            models = [
                                'Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0',
                                'Intel(R) UHD Graphics 770 Direct3D11 vs_5_0 ps_5_0',
                            ]
                            weights = [60, 40]
                        chosen = choices(models, weights=weights, k=1)[0]
                        renderer = f"ANGLE (Intel, {chosen})"
                        vendor = 'Google Inc. (Intel)'

                    else:
                        # Unknown GPU brand — fall back to a safe modern NVIDIA
                        renderer = 'ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0)'
                        vendor = 'Google Inc. (NVIDIA)'

                webgl_fp['webGl:renderer'] = renderer
                webgl_fp['webGl:vendor'] = vendor
                if 'webGl:parameters' in webgl_fp:
                    params = webgl_fp['webGl:parameters']
                    if '7937' in params:
                        params['7937'] = renderer
                    if '37446' in params:
                        params['37446'] = renderer
                    if '37445' in params:
                        params['37445'] = vendor
                if 'webGl2:parameters' in webgl_fp:
                    params2 = webgl_fp['webGl2:parameters']
                    if '7937' in params2:
                        params2['7937'] = renderer
                    if '37446' in params2:
                        params2['37446'] = renderer
                    if '37445' in params2:
                        params2['37445'] = vendor
            
            enable_webgl2 = webgl_fp.pop('webGl2Enabled')

            # Merge the WebGL fingerprint into the config
            merge_into(config, webgl_fp)
            # Set the WebGL preferences
            merge_into(
                firefox_user_prefs,
                {
                    'webgl.enable-webgl2': enable_webgl2,
                    'webgl.force-enabled': True,
                },
            )

    if not profile_loaded:
        # Canvas anti-fingerprinting
        # aaOffset:    sub-pixel noise injected into every canvas readback
        # aaCapOffset: caps the noise so text metrics remain plausible
        merge_into(
            config,
            {
                'canvas:aaOffset': randint(-50, 50),     # nosec
                'canvas:aaCapOffset': True,
                'canvas:seed': randint(1, 4_294_967_295), # nosec
                'audio:seed': randint(1, 4_294_967_295),  # nosec
            },
        )

    # Always inject hardened stealth prefs (telemetry kill-switches, etc.).
    # These are safe for every launch; they only suppress outbound noise.
    merge_into(firefox_user_prefs, STEALTH_PREFS)

    # Cache previous pages, requests, etc (uses more memory)
    if enable_cache:
        merge_into(firefox_user_prefs, CACHE_PREFS)

    # Save profile config to cache for next run
    if user_data_dir and not profile_loaded:
        try:
            profile_path = Path(user_data_dir)
            profile_path.mkdir(parents=True, exist_ok=True)
            profile_file = profile_path / 'camoufox_profile.json'
            with open(profile_file, 'wb') as pf:
                pf.write(orjson.dumps({
                    'version': PROFILE_VERSION,
                    'config': config,
                    'firefox_user_prefs': firefox_user_prefs,
                }, option=orjson.OPT_INDENT_2))
            if debug:
                print(f"[camoufox] Saved persistent profile config: {profile_file}")
        except Exception as e:
            print(f"[camoufox] Warning: Failed to save profile cache: {e}")

    # Pop internal _stealth config properties to avoid validate_config throwing UnknownProperty
    stealth_keys = [k for k in config.keys() if k.startswith('_stealth:')]
    for k in stealth_keys:
        config.pop(k)

    # Print the config if debug is enabled
    if debug:
        print('[DEBUG] Config:')
        pprint(config)

    # Validate the config
    validate_config(config, path=executable_path)


    # Prepare environment variables to pass to Camoufox
    env_vars = {
        **get_env_vars(config, target_os),
        **env,
    }
    # Prepare the executable path
    if executable_path:
        executable_path = str(executable_path)
    else:
        executable_path = launch_path()

    return {
        "executable_path": executable_path,
        "args": args,
        "env": env_vars,
        "firefox_user_prefs": firefox_user_prefs,
        "proxy": proxy,
        "headless": headless,
        **(launch_options if launch_options is not None else {}),
    }
