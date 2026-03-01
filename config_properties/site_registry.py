"""
config_properties/site_registry.py
Backward-compatible shim — delegates to config.settings.SITES.
All site URLs are defined in config/sites.json, loaded via config/settings.py.
"""
from config.settings import SITES

__all__ = ["SITES"]
