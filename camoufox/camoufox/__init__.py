from .addons import DefaultAddons
from .async_api import AsyncCamoufox, AsyncNewBrowser
from .fingerprints import pick_realistic_screen
from .sync_api import Camoufox, NewBrowser
from .utils import STEALTH_PREFS, launch_options

__all__ = [
    "Camoufox",
    "NewBrowser",
    "AsyncCamoufox",
    "AsyncNewBrowser",
    "DefaultAddons",
    "launch_options",
    "pick_realistic_screen",
    "STEALTH_PREFS",
]
