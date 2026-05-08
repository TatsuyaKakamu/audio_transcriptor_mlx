from pathlib import Path

from app.config import AppConfig, load_config


def test_missing_file_returns_defaults(tmp_path: Path) -> None:
    cfg = load_config(tmp_path / "does-not-exist.toml")
    defaults = AppConfig()
    assert cfg.language == defaults.language
    assert cfg.model == defaults.model
    assert cfg.watch_dir == defaults.watch_dir
    assert cfg.extensions == defaults.extensions
    assert cfg.trash_source_after_success is True


def test_partial_config_merges_with_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('language = "en"\nmodel = "small"\n', encoding="utf-8")
    cfg = load_config(path)
    assert cfg.language == "en"
    assert cfg.model == "small"
    assert cfg.extensions == AppConfig().extensions
    assert cfg.trash_source_after_success is True


def test_watch_dir_expanduser(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('watch_dir = "~/Downloads"\n', encoding="utf-8")
    cfg = load_config(path)
    assert cfg.watch_dir == Path.home() / "Downloads"


def test_extensions_normalized(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('extensions = ["WAV", ".M4A", "flac"]\n', encoding="utf-8")
    cfg = load_config(path)
    assert cfg.extensions == frozenset({".wav", ".m4a", ".flac"})


def test_trash_source_can_be_disabled(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("trash_source_after_success = false\n", encoding="utf-8")
    cfg = load_config(path)
    assert cfg.trash_source_after_success is False


def test_malformed_toml_falls_back_to_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("this is = = not toml", encoding="utf-8")
    cfg = load_config(path)
    assert cfg == AppConfig()
