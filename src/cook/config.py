from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class ConfigError(Exception):
    """Raised for invalid configuration values."""


@dataclass
class Config:
    recipe: str = "recipe.py"
    executor: str = "local"
    default: str | None = None
    executor_configs: dict[str, dict[str, Any]] = field(default_factory=dict)


def load_config(path: Path | None = None) -> Config:
    explicit = path is not None
    if path is None:
        path = Path.cwd() / "cook.toml"

    if not path.exists():
        if explicit:
            raise ConfigError(f"Config file not found: {path}")
        return Config()

    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"Invalid TOML in {path}: {e}") from e

    cook = data.get("cook", {})
    if not isinstance(cook, dict):
        raise ConfigError(f"Expected [cook] to be a table, got {type(cook).__name__}")

    config = Config()

    if "recipe" in cook:
        val = cook["recipe"]
        if not isinstance(val, str):
            raise ConfigError(
                f"Expected 'recipe' to be a string, got {type(val).__name__}"
            )
        config.recipe = val

    if "executor" in cook:
        val = cook["executor"]
        if not isinstance(val, str):
            raise ConfigError(
                f"Expected 'executor' to be a string, got {type(val).__name__}"
            )
        config.executor = val

    if "default" in cook:
        val = cook["default"]
        if not isinstance(val, str):
            raise ConfigError(
                f"Expected 'default' to be a string, got {type(val).__name__}"
            )
        config.default = val

    # Any sub-table under [cook] is treated as executor configuration.
    _top_level_keys = {"recipe", "executor", "default"}
    for key, val in cook.items():
        if key in _top_level_keys:
            continue
        if not isinstance(val, dict):
            raise ConfigError(
                f"Expected [cook.{key}] to be a table, got {type(val).__name__}"
            )
        config.executor_configs[key] = val

    return config
