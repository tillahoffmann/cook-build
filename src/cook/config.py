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
    local_max_concurrent: int = 1
    slurm_max_concurrent: int = 64
    slurm_poll_interval: float = 2.0
    slurm_poll_timeout: float = 86400.0
    slurm_poll_retries: int = 10
    slurm_defaults: dict[str, str] = field(default_factory=dict)


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

    local = cook.get("local", {})
    if not isinstance(local, dict):
        raise ConfigError(
            f"Expected [cook.local] to be a table, got {type(local).__name__}"
        )
    if local:
        if "max_concurrent" in local:
            val = local["max_concurrent"]
            if not isinstance(val, int) or isinstance(val, bool):
                raise ConfigError(
                    f"Expected 'local.max_concurrent' to be an integer, got {type(val).__name__}"
                )
            if val < 1:
                raise ConfigError(f"'local.max_concurrent' must be >= 1, got {val}")
            config.local_max_concurrent = val

    slurm = cook.get("slurm", {})
    if not isinstance(slurm, dict):
        raise ConfigError(
            f"Expected [cook.slurm] to be a table, got {type(slurm).__name__}"
        )
    if slurm:
        if "max_concurrent" in slurm:
            val = slurm["max_concurrent"]
            if not isinstance(val, int) or isinstance(val, bool):
                raise ConfigError(
                    f"Expected 'slurm.max_concurrent' to be an integer, got {type(val).__name__}"
                )
            if val < 1:
                raise ConfigError(f"'slurm.max_concurrent' must be >= 1, got {val}")
            config.slurm_max_concurrent = val

        if "poll_interval" in slurm:
            val = slurm["poll_interval"]
            if not isinstance(val, (int, float)) or isinstance(val, bool):
                raise ConfigError(
                    f"Expected 'slurm.poll_interval' to be a number, got {type(val).__name__}"
                )
            if val <= 0:
                raise ConfigError(f"'slurm.poll_interval' must be > 0, got {val}")
            config.slurm_poll_interval = float(val)

        if "poll_timeout" in slurm:
            val = slurm["poll_timeout"]
            if not isinstance(val, (int, float)) or isinstance(val, bool):
                raise ConfigError(
                    f"Expected 'slurm.poll_timeout' to be a number, got {type(val).__name__}"
                )
            if val <= 0:
                raise ConfigError(f"'slurm.poll_timeout' must be > 0, got {val}")
            config.slurm_poll_timeout = float(val)

        if "poll_retries" in slurm:
            val = slurm["poll_retries"]
            if not isinstance(val, int) or isinstance(val, bool):
                raise ConfigError(
                    f"Expected 'slurm.poll_retries' to be an integer, got {type(val).__name__}"
                )
            if val < 1:
                raise ConfigError(f"'slurm.poll_retries' must be >= 1, got {val}")
            config.slurm_poll_retries = val

        if "defaults" in slurm:
            defaults = slurm["defaults"]
            if not isinstance(defaults, dict):
                raise ConfigError(
                    f"Expected [cook.slurm.defaults] to be a table, got {type(defaults).__name__}"
                )
            for k, v in defaults.items():
                if not isinstance(v, str):
                    raise ConfigError(
                        f"Expected 'slurm.defaults.{k}' to be a string, got {type(v).__name__}"
                    )
            config.slurm_defaults = {k: str(v) for k, v in defaults.items()}

    return config
