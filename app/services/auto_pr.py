from __future__ import annotations

import datetime as _dt
import logging
import re
import secrets
import shutil
import string
import subprocess
from collections import defaultdict
from pathlib import Path

from app.config import AutoPRConfig
from app.services import notifier

logger = logging.getLogger(__name__)

_GIT_TIMEOUT_SEC = 30.0
_GH_TIMEOUT_SEC = 60.0
_PUSH_TIMEOUT_SEC = 120.0
_DATE_PREFIX_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")
_ALPHABET = string.ascii_letters + string.digits


class AutoPRError(Exception):
    """Raised internally for early aborts; always caught inside publish_pair."""


def publish_pair(
    *,
    transcript_path: Path,
    minutes_path: Path | None,
    audio_path: Path,
    cfg: AutoPRConfig,
) -> bool:
    """Copy transcript (+ optional minutes) to cfg.repo_path, push a branch, open a PR.

    Returns True only when the PR was created successfully. Any failure is
    swallowed (logged + macOS notification) and returns False.
    """
    if not cfg.enabled:
        return False

    repo_path = Path(cfg.repo_path).expanduser()
    try:
        _preflight(repo_path, cfg, transcript_path, minutes_path)
    except AutoPRError as e:
        logger.warning("auto_pr preflight aborted: %s", e)
        notifier.notify("PR 作成失敗", str(e)[:200])
        return False

    notifier.notify("PR 作成中…", f"→ {repo_path.name}")
    logger.info("auto_pr starting (repo=%s)", repo_path)

    branch: str | None = None
    try:
        date_str = _resolve_date(minutes_path, audio_path)
        branch = _build_branch_name(cfg.branch_prefix, date_str)
        variables = _build_variables(
            date_str=date_str,
            transcript_path=transcript_path,
            minutes_path=minutes_path,
            branch=branch,
        )

        _checkout_clean_base(repo_path, cfg.default_branch)
        _run_git(repo_path, ["checkout", "-b", branch])

        rel_paths = _stage_files(repo_path, cfg, transcript_path, minutes_path)
        _run_git(repo_path, ["add", "--", *rel_paths])
        commit_msg = _format_template(cfg.commit_message_template, variables)
        _run_git(repo_path, ["commit", "-m", commit_msg])

        _run_git(repo_path, ["push", "-u", "origin", branch], timeout=_PUSH_TIMEOUT_SEC)
        pr_url = _create_pr(repo_path, cfg, branch, variables)
    except AutoPRError as e:
        logger.warning("auto_pr aborted: %s", e)
        notifier.notify("PR 作成失敗", str(e)[:200])
        _try_restore_base(repo_path, cfg.default_branch)
        return False
    except Exception as e:  # noqa: BLE001 — never propagate
        logger.exception("auto_pr unexpected failure")
        notifier.notify("PR 作成失敗", f"{type(e).__name__}: {str(e)[:160]}")
        _try_restore_base(repo_path, cfg.default_branch)
        return False

    _try_restore_base(repo_path, cfg.default_branch)
    notifier.notify("PR 作成完了", pr_url or branch or "")
    logger.info("auto_pr created PR: %s (branch=%s)", pr_url, branch)
    return True


def _preflight(
    repo_path: Path,
    cfg: AutoPRConfig,
    transcript_path: Path,
    minutes_path: Path | None,
) -> None:
    if not repo_path.is_dir():
        raise AutoPRError(f"repo_path が存在しません: {repo_path}")
    if not (repo_path / ".git").exists():
        raise AutoPRError(f"git リポジトリではありません: {repo_path}")
    if shutil.which("git") is None:
        raise AutoPRError("git コマンドが見つかりません")
    if shutil.which("gh") is None:
        raise AutoPRError("gh CLI が見つかりません")
    if not transcript_path.is_file():
        raise AutoPRError(f"transcript が見つかりません: {transcript_path}")
    if minutes_path is not None and not minutes_path.is_file():
        raise AutoPRError(f"minutes が見つかりません: {minutes_path}")
    if _is_dirty(repo_path):
        raise AutoPRError("ローカルクローンに未コミット変更あり")


def _is_dirty(repo_path: Path) -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(repo_path),
        check=False,
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_SEC,
    )
    if result.returncode != 0:
        raise AutoPRError(f"git status 失敗: {result.stderr.strip()[:160]}")
    return bool(result.stdout.strip())


def _checkout_clean_base(repo_path: Path, default_branch: str) -> None:
    _run_git(repo_path, ["fetch", "origin", default_branch])
    _run_git(repo_path, ["checkout", default_branch])
    _run_git(repo_path, ["reset", "--hard", f"origin/{default_branch}"])


def _stage_files(
    repo_path: Path,
    cfg: AutoPRConfig,
    transcript_path: Path,
    minutes_path: Path | None,
) -> list[str]:
    rel_paths: list[str] = []

    transcript_dest_dir = (repo_path / cfg.transcript_subdir).resolve()
    _ensure_within(repo_path, transcript_dest_dir, "transcript_subdir")
    transcript_dest_dir.mkdir(parents=True, exist_ok=True)
    transcript_dest = transcript_dest_dir / transcript_path.name
    shutil.copy2(transcript_path, transcript_dest)
    rel_paths.append(str(transcript_dest.relative_to(repo_path)))

    if minutes_path is not None:
        minutes_dest_dir = (repo_path / cfg.minutes_subdir).resolve()
        _ensure_within(repo_path, minutes_dest_dir, "minutes_subdir")
        minutes_dest_dir.mkdir(parents=True, exist_ok=True)
        minutes_dest = minutes_dest_dir / minutes_path.name
        shutil.copy2(minutes_path, minutes_dest)
        rel_paths.append(str(minutes_dest.relative_to(repo_path)))

    return rel_paths


def _ensure_within(repo_path: Path, candidate: Path, label: str) -> None:
    repo_resolved = repo_path.resolve()
    try:
        candidate.relative_to(repo_resolved)
    except ValueError as e:
        raise AutoPRError(f"{label} がリポジトリ外を指しています: {candidate}") from e


def _create_pr(
    repo_path: Path,
    cfg: AutoPRConfig,
    branch: str,
    variables: dict[str, str],
) -> str:
    title = _format_template(cfg.pr_title_template, variables)
    body = _format_template(cfg.pr_body_template, variables)
    args = [
        "gh",
        "pr",
        "create",
        "--head",
        branch,
        "--base",
        cfg.default_branch,
        "--title",
        title,
        "--body",
        body,
    ]
    if cfg.gh_repo:
        args.extend(["-R", cfg.gh_repo])
    result = subprocess.run(
        args,
        cwd=str(repo_path),
        check=False,
        capture_output=True,
        text=True,
        timeout=_GH_TIMEOUT_SEC,
    )
    if result.returncode != 0:
        raise AutoPRError(f"gh pr create 失敗: {result.stderr.strip()[:160]}")
    return result.stdout.strip()


def _run_git(
    repo_path: Path,
    args: list[str],
    *,
    timeout: float = _GIT_TIMEOUT_SEC,
) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(repo_path),
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise AutoPRError(f"git {args[0]} 失敗: {result.stderr.strip()[:160]}")
    return result.stdout


def _try_restore_base(repo_path: Path, default_branch: str) -> None:
    try:
        subprocess.run(
            ["git", "checkout", default_branch],
            cwd=str(repo_path),
            check=False,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SEC,
        )
    except Exception:  # noqa: BLE001 — cleanup must not raise
        logger.exception("failed to restore base branch %s", default_branch)


def _resolve_date(minutes_path: Path | None, audio_path: Path) -> str:
    if minutes_path is not None:
        m = _DATE_PREFIX_RE.match(minutes_path.name)
        if m:
            return m.group(1)
    return _dt.datetime.fromtimestamp(audio_path.stat().st_mtime).strftime("%Y-%m-%d")


def _build_branch_name(prefix: str, date_str: str) -> str:
    suffix = "".join(secrets.choice(_ALPHABET) for _ in range(6))
    return f"{prefix}{date_str}-{suffix}"


def _build_variables(
    *,
    date_str: str,
    transcript_path: Path,
    minutes_path: Path | None,
    branch: str,
) -> dict[str, str]:
    topic = ""
    if minutes_path is not None:
        m = _DATE_PREFIX_RE.match(minutes_path.stem)
        if m:
            topic = minutes_path.stem[m.end() :].lstrip("_")
    return {
        "date": date_str,
        "transcript_name": transcript_path.name,
        "minutes_name": minutes_path.name if minutes_path else "",
        "topic": topic,
        "branch": branch,
    }


def _format_template(template: str, variables: dict[str, str]) -> str:
    mapping: defaultdict[str, str] = defaultdict(str, variables)
    try:
        return template.format_map(mapping)
    except Exception:  # noqa: BLE001 — fallback to raw template if format syntax breaks
        logger.warning("template format failed; using raw template: %r", template)
        return template
