from pathlib import Path

import pytest

from cook.config import Config, ConfigError, load_config


def test_config_defaults():
    c = Config()
    assert c.recipe == "recipe.py"
    assert c.executor == "local"
    assert c.default is None
    assert c.executor_configs == {}


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
    assert result.executor_configs["local"] == {"max_concurrent": 8}


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


def test_load_config_invalid_toml(tmp_path: Path):
    p = tmp_path / "cook.toml"
    p.write_text("[cook\n")  # bad syntax
    with pytest.raises(ConfigError, match="Invalid TOML"):
        load_config(p)


def test_load_config_unknown_keys_ignored(tmp_path: Path):
    p = tmp_path / "cook.toml"
    p.write_text('[cook]\nrecipe = "recipe.py"\nfuture_key = true\n')
    # Non-dict unknown keys at cook level are not executor configs,
    # so they should raise (expected table)
    with pytest.raises(ConfigError, match="Expected.*to be a table"):
        load_config(p)


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


def test_load_config_cook_not_a_table(tmp_path: Path):
    p = tmp_path / "cook.toml"
    p.write_text('cook = "not a table"\n')
    with pytest.raises(ConfigError, match="Expected \\[cook\\] to be a table"):
        load_config(p)


def test_load_config_executor_section_not_a_table(tmp_path: Path):
    p = tmp_path / "cook.toml"
    p.write_text('[cook]\nlocal = "not a table"\n')
    with pytest.raises(ConfigError, match="Expected \\[cook.local\\] to be a table"):
        load_config(p)


def test_load_config_local_section_stored(tmp_path: Path):
    p = tmp_path / "cook.toml"
    p.write_text("[cook.local]\nmax_concurrent = 16\n")
    result = load_config(p)
    assert result.executor_configs["local"] == {"max_concurrent": 16}


def test_load_config_slurm_section_stored(tmp_path: Path):
    p = tmp_path / "cook.toml"
    p.write_text("[cook.slurm]\nmax_concurrent = 32\npoll_interval = 5.0\n")
    result = load_config(p)
    assert result.executor_configs["slurm"] == {
        "max_concurrent": 32,
        "poll_interval": 5.0,
    }


def test_load_config_slurm_defaults_stored(tmp_path: Path):
    p = tmp_path / "cook.toml"
    p.write_text(
        '[cook.slurm.defaults]\nmem = "4G"\ntime = "01:00:00"\npartition = "gpu"\n'
    )
    config = load_config(p)
    assert config.executor_configs["slurm"]["defaults"] == {
        "mem": "4G",
        "time": "01:00:00",
        "partition": "gpu",
    }


def test_load_config_unknown_executor_section(tmp_path: Path):
    """Unknown executor sections are stored as raw dicts."""
    p = tmp_path / "cook.toml"
    p.write_text("[cook.kubernetes]\nnamespace = 'default'\n")
    result = load_config(p)
    assert result.executor_configs["kubernetes"] == {"namespace": "default"}
