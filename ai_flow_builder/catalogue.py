"""
ai_flow_builder/catalogue.py

Obtains the current command catalogue from the REAL parser and action registry —
never a hand-maintained list. This is what makes generated flows provable: the
generator can only emit commands this function actually found in the runtime.

Reuses tools/flow_lint.py rather than re-deriving the runner dispatch tables, so
the generator and the validator can never disagree about what is supported.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from functools import lru_cache

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)


@dataclass
class Catalogue:
    """A snapshot of what the tool can actually execute, right now."""

    platform: str
    supported_commands: set[str] = field(default_factory=set)
    web_only: set[str] = field(default_factory=set)
    appium_only: set[str] = field(default_factory=set)
    locators: set[str] = field(default_factory=set)
    locators_by_group: dict[str, list[str]] = field(default_factory=dict)
    codeless_actions: set[str] = field(default_factory=set)
    reusable_steps: set[str] = field(default_factory=set)
    sites: dict[str, str] = field(default_factory=dict)
    source: dict[str, str] = field(default_factory=dict)

    def supports(self, command_type: str) -> bool:
        return command_type in self.supported_commands

    def has_locator(self, name: str) -> bool:
        return name in self.locators

    def evidence_for(self, command_type: str) -> str:
        """Repository evidence that a command type is executable on this platform."""
        runner = "runner_appium.py" if self.platform in _APPIUM else "runner.py"
        if command_type in self.supported_commands:
            return f"dispatch key '{command_type}' present in {runner}"
        other = "runner.py" if runner != "runner.py" else "runner_appium.py"
        if command_type in (self.appium_only | self.web_only):
            return f"NOT in {runner}; only in {other}"
        return f"no dispatch key '{command_type}' in either runner"


_APPIUM = {"ios", "android", "hybrid"}


@lru_cache(maxsize=8)
def load(platform: str = "web") -> Catalogue:
    """Build a Catalogue by interrogating the live parser, registry and locator DBs."""
    platform = (platform or "web").lower()

    from tools import flow_lint

    supported = flow_lint._supported_for(platform)
    locator_names = flow_lint.load_locator_names(platform)

    # Full catalogue (also gives grouped locators + codeless actions + sites).
    cat = flow_lint.build_catalog()
    cs = cat["command_support"]

    if platform in _APPIUM:
        groups = cat["locators"]["appium_by_screen"].get(platform, {})
    else:
        groups = cat["locators"]["web_by_page"]

    try:
        from core.reusable_steps import list_names

        reusables = set(list_names())
    except Exception:  # noqa: BLE001
        reusables = set()

    return Catalogue(
        platform=platform,
        supported_commands=set(supported),
        web_only=set(cs["web_only"]),
        appium_only=set(cs["appium_only"]),
        locators=set(locator_names),
        locators_by_group={k: list(v) for k, v in groups.items()},
        codeless_actions=set(cat["codeless_actions"]),
        reusable_steps=reusables,
        sites=dict(cat["sites"]),
        source={
            "commands": "nlp/parser.py + runner.py/runner_appium.py dispatch (AST)",
            "locators": "data/recorded_elements.json + data/locators_manual.json",
            "actions": "core.registry.ACTION_REGISTRY via execution/action_service.py",
            "extracted_by": "tools/flow_lint.py",
        },
    )


def summary(cat: Catalogue) -> str:
    return (
        f"platform={cat.platform} "
        f"commands={len(cat.supported_commands)} "
        f"locators={len(cat.locators)} "
        f"codeless_actions={len(cat.codeless_actions)} "
        f"reusables={len(cat.reusable_steps)}"
    )


if __name__ == "__main__":  # pragma: no cover
    for p in ("web", "ios", "android"):
        print(summary(load(p)))
