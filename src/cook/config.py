from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


class ConfigError(Exception):
    """Raised for invalid configuration values."""


@dataclass
class Config:
    recipe: str = "recipe.py"
    executor: str = "local"
    default: str | None = None
    local_max_concurrent: int = 4


def load_config(path: Path | None = None) -> Config:
    if path is None:
        path = Path.cwd() / "cook.toml"

    if not path.exists():
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

    local = cook.get("local", {})
    if isinstance(local, dict):
        if "max_concurrent" in local:
            val = local["max_concurrent"]
            if not isinstance(val, int) or isinstance(val, bool):
                raise ConfigError(
                    f"Expected 'local.max_concurrent' to be an integer, got {type(val).__name__}"
                )
            if val < 1:
                raise ConfigError(f"'local.max_concurrent' must be >= 1, got {val}")
            config.local_max_concurrent = val

    return config
