"""
adapters/factory.py
Adapter factory — returns the correct platform adapter by name.
Single entry point for all platform adapters.
No hardcoded values — platform resolved from config or argument.
"""
import logging
from config import settings

logger = logging.getLogger(__name__)

_ADAPTER_MAP = {
    "web":     "adapters.web.web_adapter.WebAdapter",
    "mobile":  "adapters.mobile.mobile_adapter.MobileAdapter",
    "android": "adapters.android.android_adapter.AndroidAdapter",
    "ios":     "adapters.ios.ios_adapter.IOSAdapter",
    "hybrid":  "adapters.hybrid.hybrid_adapter.HybridAdapter",
}


def get_adapter(platform: str = None):
    """
    Returns an initialized adapter instance for the given platform.
    If platform is not provided, uses PLATFORM from config/settings.py.

    Supported platforms: web, mobile, android, ios, hybrid
    """
    target = (platform or settings.PLATFORM or "web").lower().strip()

    adapter_path = _ADAPTER_MAP.get(target)
    if not adapter_path:
        raise ValueError(
            f"Unknown platform '{target}'. "
            f"Supported: {list(_ADAPTER_MAP.keys())}"
        )

    module_path, class_name = adapter_path.rsplit(".", 1)

    import importlib
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)

    logger.info("🚀 Adapter loaded: %s (platform=%s)", class_name, target)
    return cls()
