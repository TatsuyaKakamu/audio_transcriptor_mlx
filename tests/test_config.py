from pathlib import Path

from app.config import AppConfig, AutoPRConfig, MinutesConfig, load_config


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


def test_minutes_defaults_when_missing(tmp_path: Path) -> None:
    cfg = load_config(tmp_path / "missing.toml")
    assert cfg.minutes == MinutesConfig()
    assert cfg.minutes.enabled is True
    assert cfg.minutes.model == "gemma4"
    assert cfg.minutes.ollama_host == "http://localhost:11434"
    assert cfg.minutes.max_input_chars == 30000
    assert cfg.minutes.request_timeout_seconds == 600.0
    assert cfg.minutes.num_ctx == 32768


def test_minutes_partial_override(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        "[minutes]\nenabled = true\n",
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert cfg.minutes.enabled is True
    # 他フィールドは defaults
    assert cfg.minutes.model == MinutesConfig().model
    assert cfg.minutes.max_input_chars == MinutesConfig().max_input_chars


def test_minutes_full_override(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        "\n".join(
            [
                "[minutes]",
                "enabled = true",
                'ollama_host = "http://example.local:9999"',
                'model = "qwen3:30b"',
                'prompt_language = "en"',
                "max_input_chars = 12345",
                "request_timeout_seconds = 30.5",
                "num_ctx = 16384",
                "",
            ]
        ),
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert cfg.minutes == MinutesConfig(
        enabled=True,
        ollama_host="http://example.local:9999",
        model="qwen3:30b",
        prompt_language="en",
        max_input_chars=12345,
        request_timeout_seconds=30.5,
        num_ctx=16384,
    )


def test_auto_pr_defaults_when_missing(tmp_path: Path) -> None:
    cfg = load_config(tmp_path / "missing.toml")
    assert cfg.auto_pr == AutoPRConfig()
    assert cfg.auto_pr.enabled is False
    assert cfg.auto_pr.default_branch == "main"
    assert cfg.auto_pr.branch_prefix == "auto-transcript/"
    assert cfg.auto_pr.gh_repo == ""


def test_auto_pr_partial_override(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        "[auto_pr]\nenabled = true\n",
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert cfg.auto_pr.enabled is True
    assert cfg.auto_pr.branch_prefix == AutoPRConfig().branch_prefix
    assert cfg.auto_pr.default_branch == AutoPRConfig().default_branch


def test_auto_pr_full_override(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        "\n".join(
            [
                "[auto_pr]",
                "enabled = true",
                'repo_path = "~/some-repo"',
                'transcript_subdir = "transcript"',
                'minutes_subdir = "transcript/summary"',
                'default_branch = "trunk"',
                'branch_prefix = "claude/transcript-"',
                'commit_message_template = "add for {date}"',
                'pr_title_template = "PR for {date}"',
                'pr_body_template = "body"',
                'gh_repo = "owner/repo"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert cfg.auto_pr.enabled is True
    assert cfg.auto_pr.repo_path == Path.home() / "some-repo"
    assert cfg.auto_pr.transcript_subdir == "transcript"
    assert cfg.auto_pr.minutes_subdir == "transcript/summary"
    assert cfg.auto_pr.default_branch == "trunk"
    assert cfg.auto_pr.branch_prefix == "claude/transcript-"
    assert cfg.auto_pr.commit_message_template == "add for {date}"
    assert cfg.auto_pr.pr_title_template == "PR for {date}"
    assert cfg.auto_pr.pr_body_template == "body"
    assert cfg.auto_pr.gh_repo == "owner/repo"


def test_mixed_language_is_loaded(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('language = "mixed"\nmodel = "large-v3-turbo"\n', encoding="utf-8")
    cfg = load_config(path)
    assert cfg.language == "mixed"
    assert cfg.model == "large-v3-turbo"


def test_auto_pr_mode_defaults_to_pr(tmp_path: Path) -> None:
    cfg = load_config(tmp_path / "missing.toml")
    assert cfg.auto_pr.mode == "pr"


def test_auto_pr_mode_direct(tmp_path: Path) -> None:
    p = tmp_path / "config.toml"
    p.write_text('[auto_pr]\nmode = "Direct"\n', encoding="utf-8")
    assert load_config(p).auto_pr.mode == "direct"


def test_auto_pr_mode_unknown_falls_back_to_pr(tmp_path: Path) -> None:
    p = tmp_path / "config.toml"
    p.write_text('[auto_pr]\nmode = "merge"\n', encoding="utf-8")
    assert load_config(p).auto_pr.mode == "pr"


def test_vad_and_mixed_defaults(tmp_path: Path) -> None:
    from app.config import MixedConfig, VadConfig
    cfg = load_config(tmp_path / "missing.toml")
    assert cfg.vad == VadConfig()
    assert cfg.vad.enabled is False
    assert cfg.mixed == MixedConfig()


def test_vad_and_mixed_override(tmp_path: Path) -> None:
    p = tmp_path / "c.toml"
    p.write_text(
        "[vad]\nenabled = true\nthreshold = 0.3\nmin_silence_ms = 1000\nspeech_pad_ms = 400\n"
        "[mixed]\nen_min_margin = 0.6\n",
        encoding="utf-8",
    )
    cfg = load_config(p)
    assert cfg.vad.enabled is True
    assert cfg.vad.threshold == 0.3
    assert cfg.vad.min_silence_ms == 1000
    assert cfg.vad.speech_pad_ms == 400
    assert cfg.mixed.en_min_margin == 0.6


def test_invalid_vad_and_mixed_values_fall_back(tmp_path: Path) -> None:
    from app.config import MixedConfig, VadConfig
    p = tmp_path / "c.toml"
    p.write_text(
        '[vad]\nenabled = "true"\nthreshold = 1.5\nmin_silence_ms = -1\nspeech_pad_ms = "x"\n'
        "[mixed]\nen_min_margin = 0.1\n",
        encoding="utf-8",
    )
    cfg = load_config(p)
    assert cfg.vad == VadConfig()
    assert cfg.mixed == MixedConfig()


def test_vad_numbers_must_be_numeric(tmp_path: Path) -> None:
    from app.config import VadConfig
    p = tmp_path / "c.toml"
    p.write_text('[vad]\nthreshold = "0.3"\nmin_silence_ms = inf\nspeech_pad_ms = 200.5\n',
                 encoding="utf-8")
    assert load_config(p).vad == VadConfig()


def test_vad_integer_valued_float_is_accepted(tmp_path: Path) -> None:
    p = tmp_path / "c.toml"
    p.write_text("[vad]\nmin_silence_ms = 800.0\n", encoding="utf-8")
    assert load_config(p).vad.min_silence_ms == 800
