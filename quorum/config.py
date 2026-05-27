"""Optional per-repo configuration.

Looks for `.quorum.json` (or `.quorum.toml` on Python 3.11+) from the working
directory upward — like git finds `.git`. Lets a project customize the review
*without editing source*:

```json
{
  "provider": "claude-cli",
  "fail_on": "high",
  "agents": { "tests": { "enabled": false } },
  "rules": [
    "Every network call must pass an explicit timeout.",
    "No print() — use the logging module."
  ],
  "custom_agents": {
    "accessibility": {
      "tier": "fast",
      "focus": "a11y issues in UI code: missing alt text, unlabeled controls, non-semantic markup"
    }
  }
}
```

Everything is optional; an absent file means built-in defaults.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

CONFIG_NAMES = (".quorum.json", ".quorum.toml")


class ConfigError(RuntimeError):
    pass


@dataclass
class Config:
    provider: str | None = None
    fail_on: str | None = None
    format: str | None = None
    disabled: tuple = ()                                  # agent names to drop
    custom_agents: dict = field(default_factory=dict)     # name -> {tier, focus}
    rules: list = field(default_factory=list)             # house-rule strings
    source: str | None = None                             # path it was loaded from


def find_config(start: str | None = None) -> str | None:
    d = os.path.abspath(start or os.getcwd())
    while True:
        for name in CONFIG_NAMES:
            p = os.path.join(d, name)
            if os.path.isfile(p):
                return p
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def _load_file(path: str) -> dict:
    if path.endswith(".toml"):
        try:
            import tomllib
        except ImportError as e:  # Python < 3.11
            raise ConfigError(
                ".quorum.toml needs Python 3.11+ (tomllib); use .quorum.json instead") from e
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def load_config(path: str | None = None, search: bool = True) -> Config:
    if path is None and search:
        path = find_config()
    if not path:
        return Config()

    try:
        raw = _load_file(path)
    except ConfigError:
        raise
    except Exception as e:  # noqa: BLE001 — surface any parse/IO error uniformly
        raise ConfigError(f"could not read {path}: {e}") from e
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: top level must be an object/table")

    agents = raw.get("agents") or {}
    if not isinstance(agents, dict):
        raise ConfigError(f"{path}: 'agents' must be an object")
    disabled = tuple(name for name, spec in agents.items()
                     if isinstance(spec, dict) and spec.get("enabled") is False)

    custom = {}
    for name, spec in (raw.get("custom_agents") or {}).items():
        if not isinstance(spec, dict) or not spec.get("focus"):
            raise ConfigError(f"{path}: custom_agents.{name} needs a 'focus' string")
        tier = spec.get("tier", "fast")
        if tier not in ("strong", "fast"):
            raise ConfigError(f"{path}: custom_agents.{name}.tier must be 'strong' or 'fast'")
        custom[name] = {"tier": tier, "focus": str(spec["focus"])}

    rules = [str(r) for r in (raw.get("rules") or [])]

    return Config(provider=raw.get("provider"), fail_on=raw.get("fail_on"),
                  format=raw.get("format"), disabled=disabled,
                  custom_agents=custom, rules=rules, source=path)
