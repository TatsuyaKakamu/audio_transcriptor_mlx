from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

CONFIG_PATH = Path.home() / ".config" / "mlx-audio-transcriptor" / "config.toml"

_DEFAULT_EXTENSIONS: frozenset[str] = frozenset({".wav", ".mp3"})


@dataclass(frozen=True)
class AppConfig:
    language: str = "ja"
    model: str = "medium"
    watch_dir: Path = field(default_factory=lambda: Path.home() / "Downloads")
    extensions: frozenset[str] = _DEFAULT_EXTENSIONS
    file_stability_seconds: float = 3.0
    trash_source_after_success: bool = True


def _parse(data: dict) -> AppConfig:
    defaults = AppConfig()

    language = str(data.get("language", defaults.language))
    model = str(data.get("model", defaults.model))

    watch_dir_raw = data.get("watch_dir")
    if watch_dir_raw:
        watch_dir = Path(str(watch_dir_raw)).expanduser()
    else:
        watch_dir = defaults.watch_dir

    ext_raw = data.get("extensions")
    if ext_raw:
        exts = [e.lower() if e.startswith(".") else f".{e.lower()}" for e in ext_raw]
        extensions = frozenset(exts)
    else:
        extensions = defaults.extensions

    stability = float(data.get("file_stability_seconds", defaults.file_stability_seconds))
    trash = bool(data.get("trash_source_after_success", defaults.trash_source_after_success))

    return AppConfig(
        language=language,
        model=model,
        watch_dir=watch_dir,
        extensions=extensions,
        file_stability_seconds=stability,
        trash_source_after_success=trash,
    )


def load_config(path: Path | None = None) -> AppConfig:
    path = path or CONFIG_PATH
    if not path.exists():
        return AppConfig()
    try:
        raw = path.read_bytes()
        data = tomllib.loads(raw.decode("utf-8"))
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        logger.warning("failed to read config %s: %s — falling back to defaults", path, e)
        return AppConfig()
    return _parse(data)
