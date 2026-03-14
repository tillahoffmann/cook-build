from pathlib import Path

import pytest

from cook.config import Config, ConfigError, load_config


def test_config_defaults():
    c = Config()
    assert c.recipe == "recipe.py"
    assert c.executor == "local"
    assert c.default is None
    assert c.local_max_concurrent == 1


def test_load_config_missing_file(tmp_path: Path):
    with pytest.raises(ConfigError, match="Config file not found"):
        load_config(tmp_path / "nonexistent.toml")


def test_load_config_missing_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    result = load_config()
    assert result == Config()


def test_load_config_empty_file(tmp_path: Path):
    p = tmp_path / "cook.toml"
    p.write_text("")
    result = load_config(p)
    assert result == Config()


def test_load_config_all_fields(tmp_path: Path):
    p = tmp_path / "cook.toml"
    p.write_text(
        '[cook]\nrecipe = "build.py"\nexecutor = "slurm"\ndefault = "build-*"\n\n'
        "[cook.local]\nmax_concurrent = 8\n"
    )
    result = load_config(p)
    assert result.recipe == "build.py"
    assert result.executor == "slurm"
    assert result.default == "build-*"
    assert result.local_max_concurrent == 8


def test_load_config_minimal_cook_table(tmp_path: Path):
    p = tmp_path / "cook.toml"
    p.write_text("[cook]\n")
    result = load_config(p)
    assert result == Config()


def test_load_config_partial_fields(tmp_path: Path):
    p = tmp_path / "cook.toml"
    p.write_text('[cook]\nexecutor = "slurm"\n')
    result = load_config(p)
    assert result.executor == "slurm"
    assert result.recipe == "recipe.py"
    assert result.default is None
    assert result.local_max_concurrent == 1


def test_load_config_invalid_toml(tmp_path: Path):
    p = tmp_path / "cook.toml"
    p.write_text("[cook\n")  # bad syntax
    with pytest.raises(ConfigError, match="Invalid TOML"):
        load_config(p)


def test_load_config_unknown_keys_ignored(tmp_path: Path):
    p = tmp_path / "cook.toml"
    p.write_text('[cook]\nrecipe = "recipe.py"\nfuture_key = true\n')
    result = load_config(p)
    assert result.recipe == "recipe.py"


def test_load_config_invalid_recipe_type(tmp_path: Path):
    p = tmp_path / "cook.toml"
    p.write_text("[cook]\nrecipe = 42\n")
    with pytest.raises(ConfigError, match="Expected 'recipe' to be a string"):
        load_config(p)


def test_load_config_invalid_executor_type(tmp_path: Path):
    p = tmp_path / "cook.toml"
    p.write_text("[cook]\nexecutor = 42\n")
    with pytest.raises(ConfigError, match="Expected 'executor' to be a string"):
        load_config(p)


def test_load_config_invalid_default_type(tmp_path: Path):
    p = tmp_path / "cook.toml"
    p.write_text("[cook]\ndefault = 42\n")
    with pytest.raises(ConfigError, match="Expected 'default' to be a string"):
        load_config(p)


def test_load_config_invalid_max_concurrent_type(tmp_path: Path):
    p = tmp_path / "cook.toml"
    p.write_text("[cook.local]\nmax_concurrent = true\n")
    with pytest.raises(
        ConfigError, match="Expected 'local.max_concurrent' to be an integer"
    ):
        load_config(p)


def test_load_config_max_concurrent_too_low(tmp_path: Path):
    p = tmp_path / "cook.toml"
    p.write_text("[cook.local]\nmax_concurrent = 0\n")
    with pytest.raises(ConfigError, match="must be >= 1"):
        load_config(p)


def test_load_config_cwd_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    p = tmp_path / "cook.toml"
    p.write_text('[cook]\nrecipe = "my_recipe.py"\n')
    result = load_config()
    assert result.recipe == "my_recipe.py"


def test_load_config_cwd_no_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    result = load_config()
    assert result == Config()


def test_load_config_executor_read(tmp_path: Path):
    p = tmp_path / "cook.toml"
    p.write_text('[cook]\nexecutor = "remote"\n')
    result = load_config(p)
    assert result.executor == "remote"


def test_load_config_default_read(tmp_path: Path):
    p = tmp_path / "cook.toml"
    p.write_text('[cook]\ndefault = "test-*"\n')
    result = load_config(p)
    assert result.default == "test-*"


def test_load_config_local_max_concurrent_read(tmp_path: Path):
    p = tmp_path / "cook.toml"
    p.write_text("[cook.local]\nmax_concurrent = 16\n")
    result = load_config(p)
    assert result.local_max_concurrent == 16


def test_load_config_cook_not_a_table(tmp_path: Path):
    p = tmp_path / "cook.toml"
    p.write_text('cook = "not a table"\n')
    with pytest.raises(ConfigError, match="Expected \\[cook\\] to be a table"):
        load_config(p)


def test_load_config_local_not_a_table(tmp_path: Path):
    p = tmp_path / "cook.toml"
    p.write_text('[cook]\nlocal = "not a table"\n')
    with pytest.raises(ConfigError, match="Expected \\[cook.local\\] to be a table"):
        load_config(p)


def test_config_slurm_defaults():
    c = Config()
    assert c.slurm_max_concurrent == 64
    assert c.slurm_poll_interval == 2.0
    assert c.slurm_poll_timeout == 86400.0
    assert c.slurm_poll_retries == 10


def test_load_config_slurm_all_fields(tmp_path: Path):
    p = tmp_path / "cook.toml"
    p.write_text("[cook.slurm]\nmax_concurrent = 32\npoll_interval = 5.0\n")
    result = load_config(p)
    assert result.slurm_max_concurrent == 32
    assert result.slurm_poll_interval == 5.0


def test_load_config_slurm_poll_interval_int(tmp_path: Path):
    p = tmp_path / "cook.toml"
    p.write_text("[cook.slurm]\npoll_interval = 3\n")
    result = load_config(p)
    assert result.slurm_poll_interval == 3.0


def test_load_config_slurm_not_a_table(tmp_path: Path):
    p = tmp_path / "cook.toml"
    p.write_text('[cook]\nslurm = "not a table"\n')
    with pytest.raises(ConfigError, match="Expected \\[cook.slurm\\] to be a table"):
        load_config(p)


def test_load_config_slurm_max_concurrent_invalid_type(tmp_path: Path):
    p = tmp_path / "cook.toml"
    p.write_text("[cook.slurm]\nmax_concurrent = true\n")
    with pytest.raises(
        ConfigError, match="Expected 'slurm.max_concurrent' to be an integer"
    ):
        load_config(p)


def test_load_config_slurm_max_concurrent_too_low(tmp_path: Path):
    p = tmp_path / "cook.toml"
    p.write_text("[cook.slurm]\nmax_concurrent = 0\n")
    with pytest.raises(ConfigError, match="must be >= 1"):
        load_config(p)


def test_load_config_slurm_poll_interval_invalid_type(tmp_path: Path):
    p = tmp_path / "cook.toml"
    p.write_text("[cook.slurm]\npoll_interval = true\n")
    with pytest.raises(
        ConfigError, match="Expected 'slurm.poll_interval' to be a number"
    ):
        load_config(p)


def test_load_config_slurm_poll_interval_too_low(tmp_path: Path):
    p = tmp_path / "cook.toml"
    p.write_text("[cook.slurm]\npoll_interval = 0\n")
    with pytest.raises(ConfigError, match="must be > 0"):
        load_config(p)


def test_load_config_slurm_poll_timeout(tmp_path: Path):
    p = tmp_path / "cook.toml"
    p.write_text("[cook.slurm]\npoll_timeout = 3600\n")
    result = load_config(p)
    assert result.slurm_poll_timeout == 3600.0


def test_load_config_slurm_poll_timeout_invalid_type(tmp_path: Path):
    p = tmp_path / "cook.toml"
    p.write_text("[cook.slurm]\npoll_timeout = true\n")
    with pytest.raises(
        ConfigError, match="Expected 'slurm.poll_timeout' to be a number"
    ):
        load_config(p)


def test_load_config_slurm_poll_timeout_too_low(tmp_path: Path):
    p = tmp_path / "cook.toml"
    p.write_text("[cook.slurm]\npoll_timeout = 0\n")
    with pytest.raises(ConfigError, match="must be > 0"):
        load_config(p)


def test_load_config_slurm_poll_retries(tmp_path: Path):
    p = tmp_path / "cook.toml"
    p.write_text("[cook.slurm]\npoll_retries = 5\n")
    result = load_config(p)
    assert result.slurm_poll_retries == 5


def test_load_config_slurm_poll_retries_invalid_type(tmp_path: Path):
    p = tmp_path / "cook.toml"
    p.write_text("[cook.slurm]\npoll_retries = true\n")
    with pytest.raises(
        ConfigError, match="Expected 'slurm.poll_retries' to be an integer"
    ):
        load_config(p)


def test_load_config_slurm_poll_retries_too_low(tmp_path: Path):
    p = tmp_path / "cook.toml"
    p.write_text("[cook.slurm]\npoll_retries = 0\n")
    with pytest.raises(ConfigError, match="must be >= 1"):
        load_config(p)


def test_load_config_slurm_defaults(tmp_path: Path):
    p = tmp_path / "cook.toml"
    p.write_text(
        '[cook.slurm.defaults]\nmem = "4G"\ntime = "01:00:00"\npartition = "gpu"\n'
    )
    config = load_config(p)
    assert config.slurm_defaults == {
        "mem": "4G",
        "time": "01:00:00",
        "partition": "gpu",
    }


def test_load_config_slurm_defaults_empty(tmp_path: Path):
    p = tmp_path / "cook.toml"
    p.write_text("[cook.slurm.defaults]\n")
    config = load_config(p)
    assert config.slurm_defaults == {}


def test_load_config_slurm_defaults_not_table(tmp_path: Path):
    p = tmp_path / "cook.toml"
    p.write_text('[cook.slurm]\ndefaults = "bad"\n')
    with pytest.raises(ConfigError, match="Expected.*defaults.*to be a table"):
        load_config(p)


def test_load_config_slurm_defaults_non_string_value(tmp_path: Path):
    p = tmp_path / "cook.toml"
    p.write_text("[cook.slurm.defaults]\nmem = 42\n")
    with pytest.raises(
        ConfigError, match="Expected 'slurm.defaults.mem' to be a string"
    ):
        load_config(p)
