from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.config import AutoPRConfig
from app.services import auto_pr


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    return repo


def _make_transcript(tmp_path: Path, name: str = "meeting.transcript.md") -> Path:
    audio_dir = tmp_path / "downloads"
    audio_dir.mkdir(exist_ok=True)
    p = audio_dir / name
    p.write_text("transcript body", encoding="utf-8")
    return p


def _make_minutes(tmp_path: Path, name: str = "2026-05-23_予算会議.md") -> Path:
    audio_dir = tmp_path / "downloads"
    audio_dir.mkdir(exist_ok=True)
    p = audio_dir / name
    p.write_text("minutes body", encoding="utf-8")
    return p


def _make_audio(tmp_path: Path) -> Path:
    audio_dir = tmp_path / "downloads"
    audio_dir.mkdir(exist_ok=True)
    p = audio_dir / "meeting.wav"
    p.write_bytes(b"fake")
    return p


def _cfg(repo: Path, **overrides) -> AutoPRConfig:
    base = AutoPRConfig(
        enabled=True,
        repo_path=repo,
        transcript_subdir="transcript",
        minutes_subdir="transcript/summary",
        default_branch="main",
        branch_prefix="auto-test/",
        commit_message_template="add transcript for {date}",
        pr_title_template="add transcript for {date}",
        pr_body_template="t={transcript_name} m={minutes_name}",
        gh_repo="",
    )
    for k, v in overrides.items():
        base = AutoPRConfig(
            **{**{f.name: getattr(base, f.name) for f in base.__dataclass_fields__.values()}, k: v}
        )
    return base


class _ProcResult:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _fake_runner(scripted: list):
    """Return a callable that returns scripted results one-by-one and records calls.

    Each item in `scripted` is (predicate, _ProcResult). The first matching predicate
    wins. If nothing matches, returns rc=0 with empty stdout.
    """
    calls: list[list[str]] = []

    def run(args, **kwargs):
        calls.append(list(args))
        for pred, result in scripted:
            if pred(args):
                return result
        return _ProcResult()

    return run, calls


def _is_git(cmd: str):
    return lambda args: args[:2] == ["git", cmd]


def _is_gh_pr_create(args):
    return args[:3] == ["gh", "pr", "create"]


def test_disabled_returns_false_without_calls(tmp_path: Path, monkeypatch) -> None:
    repo = _make_repo(tmp_path)
    transcript = _make_transcript(tmp_path)
    audio = _make_audio(tmp_path)

    run_mock = MagicMock()
    monkeypatch.setattr(subprocess, "run", run_mock)

    ok = auto_pr.publish_pair(
        transcript_path=transcript,
        minutes_path=None,
        audio_path=audio,
        cfg=_cfg(repo, enabled=False),
    )

    assert ok is False
    run_mock.assert_not_called()


def test_happy_path_creates_branch_and_pr(tmp_path: Path, monkeypatch) -> None:
    repo = _make_repo(tmp_path)
    transcript = _make_transcript(tmp_path)
    minutes = _make_minutes(tmp_path)
    audio = _make_audio(tmp_path)

    scripted = [
        (_is_git("status"), _ProcResult(0, "", "")),
        (_is_gh_pr_create, _ProcResult(0, "https://github.com/example/repo/pull/42\n", "")),
    ]
    run, calls = _fake_runner(scripted)
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(auto_pr.notifier, "notify", MagicMock())
    monkeypatch.setattr(auto_pr.shutil, "which", lambda name: "/usr/bin/" + name)

    ok = auto_pr.publish_pair(
        transcript_path=transcript,
        minutes_path=minutes,
        audio_path=audio,
        cfg=_cfg(repo),
    )

    assert ok is True

    command_strings = [" ".join(c) for c in calls]
    assert any(c == "git status --porcelain" for c in command_strings)
    assert any(c == "git fetch origin main" for c in command_strings)
    assert any(c == "git checkout main" for c in command_strings)
    assert any(c == "git reset --hard origin/main" for c in command_strings)
    assert any(c.startswith("git checkout -b auto-test/2026-05-23-") for c in command_strings)
    assert any(c.startswith("git add -- ") for c in command_strings)
    assert any(
        c.startswith('git commit -m')
        or c == "git commit -m add transcript for 2026-05-23"
        for c in command_strings
    )
    assert any(c.startswith("git push -u origin auto-test/2026-05-23-") for c in command_strings)
    assert any(args[:3] == ["gh", "pr", "create"] for args in calls)

    transcript_dest = repo / "transcript" / "meeting.transcript.md"
    minutes_dest = repo / "transcript" / "summary" / "2026-05-23_予算会議.md"
    assert transcript_dest.exists()
    assert minutes_dest.exists()

    pr_call = next(args for args in calls if args[:3] == ["gh", "pr", "create"])
    title = pr_call[pr_call.index("--title") + 1]
    body = pr_call[pr_call.index("--body") + 1]
    base = pr_call[pr_call.index("--base") + 1]
    head = pr_call[pr_call.index("--head") + 1]
    assert title == "add transcript for 2026-05-23"
    assert "meeting.transcript.md" in body
    assert "2026-05-23_予算会議.md" in body
    assert base == "main"
    assert head.startswith("auto-test/2026-05-23-")


def test_dirty_repo_aborts_without_branch(tmp_path: Path, monkeypatch) -> None:
    repo = _make_repo(tmp_path)
    transcript = _make_transcript(tmp_path)
    audio = _make_audio(tmp_path)

    scripted = [(_is_git("status"), _ProcResult(0, " M file.txt\n", ""))]
    run, calls = _fake_runner(scripted)
    monkeypatch.setattr(subprocess, "run", run)
    notify_mock = MagicMock()
    monkeypatch.setattr(auto_pr.notifier, "notify", notify_mock)
    monkeypatch.setattr(auto_pr.shutil, "which", lambda name: "/usr/bin/" + name)

    ok = auto_pr.publish_pair(
        transcript_path=transcript,
        minutes_path=None,
        audio_path=audio,
        cfg=_cfg(repo),
    )

    assert ok is False
    assert not any(args[:2] == ["git", "checkout"] and "-b" in args for args in calls)
    notify_mock.assert_called_once()
    title, body = notify_mock.call_args.args
    assert title == "PR 作成失敗"
    assert "未コミット変更" in body


def test_push_failure_returns_false_and_restores_base(tmp_path: Path, monkeypatch) -> None:
    repo = _make_repo(tmp_path)
    transcript = _make_transcript(tmp_path)
    audio = _make_audio(tmp_path)

    scripted = [
        (_is_git("status"), _ProcResult(0, "", "")),
        (_is_git("push"), _ProcResult(1, "", "remote rejected\n")),
    ]
    run, calls = _fake_runner(scripted)
    monkeypatch.setattr(subprocess, "run", run)
    notify_mock = MagicMock()
    monkeypatch.setattr(auto_pr.notifier, "notify", notify_mock)
    monkeypatch.setattr(auto_pr.shutil, "which", lambda name: "/usr/bin/" + name)

    ok = auto_pr.publish_pair(
        transcript_path=transcript,
        minutes_path=None,
        audio_path=audio,
        cfg=_cfg(repo),
    )

    assert ok is False
    notify_mock.assert_called_once()
    assert notify_mock.call_args.args[0] == "PR 作成失敗"
    # cleanup: a git checkout main was attempted
    restore_attempts = [
        args for args in calls if args[:3] == ["git", "checkout", "main"]
    ]
    assert restore_attempts, "expected git checkout main during cleanup"


def test_gh_failure_returns_false(tmp_path: Path, monkeypatch) -> None:
    repo = _make_repo(tmp_path)
    transcript = _make_transcript(tmp_path)
    audio = _make_audio(tmp_path)

    scripted = [
        (_is_git("status"), _ProcResult(0, "", "")),
        (_is_gh_pr_create, _ProcResult(1, "", "gh auth required\n")),
    ]
    run, _calls = _fake_runner(scripted)
    monkeypatch.setattr(subprocess, "run", run)
    notify_mock = MagicMock()
    monkeypatch.setattr(auto_pr.notifier, "notify", notify_mock)
    monkeypatch.setattr(auto_pr.shutil, "which", lambda name: "/usr/bin/" + name)

    ok = auto_pr.publish_pair(
        transcript_path=transcript,
        minutes_path=None,
        audio_path=audio,
        cfg=_cfg(repo),
    )

    assert ok is False
    notify_mock.assert_called_once()


def test_missing_repo_path_aborts(tmp_path: Path, monkeypatch) -> None:
    transcript = _make_transcript(tmp_path)
    audio = _make_audio(tmp_path)
    run_mock = MagicMock()
    monkeypatch.setattr(subprocess, "run", run_mock)
    monkeypatch.setattr(auto_pr.notifier, "notify", MagicMock())
    monkeypatch.setattr(auto_pr.shutil, "which", lambda name: "/usr/bin/" + name)

    ok = auto_pr.publish_pair(
        transcript_path=transcript,
        minutes_path=None,
        audio_path=audio,
        cfg=_cfg(tmp_path / "does-not-exist"),
    )

    assert ok is False
    run_mock.assert_not_called()


def test_transcript_only_pr_omits_minutes_copy(tmp_path: Path, monkeypatch) -> None:
    repo = _make_repo(tmp_path)
    transcript = _make_transcript(tmp_path)
    audio = _make_audio(tmp_path)

    scripted = [
        (_is_git("status"), _ProcResult(0, "", "")),
        (_is_gh_pr_create, _ProcResult(0, "https://example/pr/1\n", "")),
    ]
    run, calls = _fake_runner(scripted)
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(auto_pr.notifier, "notify", MagicMock())
    monkeypatch.setattr(auto_pr.shutil, "which", lambda name: "/usr/bin/" + name)

    ok = auto_pr.publish_pair(
        transcript_path=transcript,
        minutes_path=None,
        audio_path=audio,
        cfg=_cfg(repo),
    )

    assert ok is True
    assert (repo / "transcript" / "meeting.transcript.md").exists()
    assert not (repo / "transcript" / "summary").exists()

    pr_call = next(args for args in calls if args[:3] == ["gh", "pr", "create"])
    body = pr_call[pr_call.index("--body") + 1]
    assert "meeting.transcript.md" in body
    assert "m= " not in body  # minutes_name is empty
    # add command stages only the transcript
    add_call = next(args for args in calls if args[:2] == ["git", "add"])
    assert "transcript/meeting.transcript.md" in add_call
    assert not any("summary" in a for a in add_call)


def test_gh_repo_passes_R_flag(tmp_path: Path, monkeypatch) -> None:
    repo = _make_repo(tmp_path)
    transcript = _make_transcript(tmp_path)
    audio = _make_audio(tmp_path)

    scripted = [
        (_is_git("status"), _ProcResult(0, "", "")),
        (_is_gh_pr_create, _ProcResult(0, "https://example/pr/1\n", "")),
    ]
    run, calls = _fake_runner(scripted)
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(auto_pr.notifier, "notify", MagicMock())
    monkeypatch.setattr(auto_pr.shutil, "which", lambda name: "/usr/bin/" + name)

    ok = auto_pr.publish_pair(
        transcript_path=transcript,
        minutes_path=None,
        audio_path=audio,
        cfg=_cfg(repo, gh_repo="example/repo"),
    )

    assert ok is True
    pr_call = next(args for args in calls if args[:3] == ["gh", "pr", "create"])
    assert pr_call[pr_call.index("-R") + 1] == "example/repo"


def test_date_falls_back_to_audio_mtime_when_no_minutes(tmp_path: Path, monkeypatch) -> None:
    repo = _make_repo(tmp_path)
    transcript = _make_transcript(tmp_path)
    audio = _make_audio(tmp_path)
    # 2026-03-04 00:00:00 UTC-ish — use a known timestamp
    import os
    import datetime as dt
    ts = dt.datetime(2026, 3, 4, 12, 0, 0).timestamp()
    os.utime(audio, (ts, ts))

    scripted = [
        (_is_git("status"), _ProcResult(0, "", "")),
        (_is_gh_pr_create, _ProcResult(0, "https://example/pr/1\n", "")),
    ]
    run, calls = _fake_runner(scripted)
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(auto_pr.notifier, "notify", MagicMock())
    monkeypatch.setattr(auto_pr.shutil, "which", lambda name: "/usr/bin/" + name)

    ok = auto_pr.publish_pair(
        transcript_path=transcript,
        minutes_path=None,
        audio_path=audio,
        cfg=_cfg(repo),
    )

    assert ok is True
    pr_call = next(args for args in calls if args[:3] == ["gh", "pr", "create"])
    title = pr_call[pr_call.index("--title") + 1]
    assert title == "add transcript for 2026-03-04"


def test_subdir_escape_attempt_aborts(tmp_path: Path, monkeypatch) -> None:
    repo = _make_repo(tmp_path)
    transcript = _make_transcript(tmp_path)
    audio = _make_audio(tmp_path)

    scripted = [(_is_git("status"), _ProcResult(0, "", ""))]
    run, _calls = _fake_runner(scripted)
    monkeypatch.setattr(subprocess, "run", run)
    notify_mock = MagicMock()
    monkeypatch.setattr(auto_pr.notifier, "notify", notify_mock)
    monkeypatch.setattr(auto_pr.shutil, "which", lambda name: "/usr/bin/" + name)

    ok = auto_pr.publish_pair(
        transcript_path=transcript,
        minutes_path=None,
        audio_path=audio,
        cfg=_cfg(repo, transcript_subdir="../escape"),
    )

    assert ok is False
    notify_mock.assert_called_once()


def test_format_template_unknown_var_blank() -> None:
    result = auto_pr._format_template(
        "x={date} y={unknown} z={branch}", {"date": "2026-01-01", "branch": "b"}
    )
    assert result == "x=2026-01-01 y= z=b"


def test_format_template_malformed_returns_raw() -> None:
    raw = "broken {unclosed"
    assert auto_pr._format_template(raw, {"date": "x"}) == raw
