"""Configuration loading and validation."""

import copy
import sys
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

DEFAULTS: dict[str, Any] = {
    "api": {
        "application_id": "",
    },
    "player": {
        "gamertag": "",
        "platform": "xbox",
    },
    "garage": {
        "roi_x": 0,
        "roi_y": 0,
        "roi_width": 0,
        "roi_height": 0,
        "poll_interval": 0.5,
    },
    "ocr": {
        "roi_x": 0,
        "roi_y": 0,
        "roi_width": 300,
        "roi_height": 100,
        "sample_rate": 2,
        "confidence_threshold": 0.8,
    },
    "moe": {
        "current_moe_percent": 0.0,
        "target_damage": 0,
        "ema_alpha": 2.0 / 101.0,
    },
    "overlay": {
        "opacity": 0.85,
        "scale": 1.0,
        "layout": "standard",
        "color_blind_mode": False,
    },
    "server": {
        "http_port": 5173,
        "ws_port": 5174,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Merge override into base, returning a new dict."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load_config(path: str | Path = "config.toml") -> dict[str, Any]:
    """Load config from a TOML file, falling back to defaults for missing values."""
    config_path = Path(path)
    if config_path.exists():
        with open(config_path, "rb") as f:
            user_config = tomllib.load(f)
        return _deep_merge(DEFAULTS, user_config)
    return copy.deepcopy(DEFAULTS)
