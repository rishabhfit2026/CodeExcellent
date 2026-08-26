"""Configuration loading. Defaults ship in defaults.json; a user config at
~/.codeexcellent/config.json (or $CODEEXCELLENT_CONFIG) overrides them key-by-key.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_DEFAULTS_PATH = Path(__file__).parent / "defaults.json"


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def user_config_path() -> Path:
    env_path = os.environ.get("CODEEXCELLENT_CONFIG")
    if env_path:
        return Path(env_path)
    return Path.home() / ".codeexcellent" / "config.json"


def load_config(extra_path: Path | None = None) -> dict[str, Any]:
    with open(_DEFAULTS_PATH) as f:
        config = json.load(f)

    for path in (user_config_path(), extra_path):
        if path and path.exists():
            with open(path) as f:
                config = _deep_merge(config, json.load(f))

    return config
