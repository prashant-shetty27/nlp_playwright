"""
core/ml_engine.py
Compatibility shim.

Canonical ML healer implementation lives in `healing/ml_engine.py`.
This module re-exports `LocatorHealer` so older imports continue to work.
"""

from healing.ml_engine import LocatorHealer

__all__ = ["LocatorHealer"]
