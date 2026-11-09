from __future__ import annotations

import datetime as _dt
import logging
import re
import secrets
import shutil
import string
import subprocess
import tempfile
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from app.config import AutoPRConfig
from app.services import notifier

logger = logging.getLogger(__name__)

_GIT_TIMEOUT_SEC = 30.0
_GH_TIMEOUT_SEC = 60.0
_PUSH_TIMEOUT_SEC = 120.0
_DATE_PREFIX_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")
_ALPHABET = string.ascii_letters + string.digits
_DIRECT_PUSH_ATTEMPTS = 3
_NON_FAST_FORWARD_RE = re.compile(r"fetch first|non-fast-forward|\[rejected\]")


@dataclass(frozen=True)
class _Labels:
    started: str
    done: str
    failed: str


_PR_LABELS = _Labels(started="PR 作成中…", done="PR 作成完了", failed="PR 作成失敗")
_DIRECT_LABELS = _Labels(started="コミット中…", done="コミット完了", failed="コミット失敗")


class AutoPRError(Exception):
    """Raised internally for early aborts; always caught inside publish_pair."""


def publish_pair(
    *,
    transcript_path: Path,
    minutes_path: Path | None,
    audio_path: Path,
    cfg: AutoPRConfig,
) -> bool:
    """Copy transcript (+ optional minutes) to cfg.repo_path and publish it.

    mode "pr": push a new branch and open a PR.
    mode "direct": commit on top of origin/default_branch and push it directly.

    Returns True only when the publish step succeeded. Any failure is
    swallowed (logged + macOS notification) and returns False.
    """
    if not cfg.enabled:
        return False

    direct = cfg.mode == "direct"
    labels = _DIRECT_LABELS if direct else _PR_LABELS

    repo_path = Path(cfg.repo_path).expanduser()
    try:
        _preflight(repo_path, cfg, transcript_path, minutes_path, require_gh=not direct)
    except AutoPRError as e:
        logger.warning("auto_pr preflight aborted: %s", e)
        notifier.notify(labels.failed, str(e)[:200])
        return False

    notifier.notify(labels.started, f"→ {repo_path.name}")
    logger.info("auto_pr starting (repo=%s, mode=%s)", repo_path, cfg.mode)

    branch: str | None = None
    result_text = ""
    try:
        date_str = _resolve_date(minutes_path, audio_path)
        branch = cfg.default_branch if direct else _build_branch_name(cfg.branch_prefix, date_str)
        variables = _build_variables(
            date_str=date_str,
            transcript_path=transcript_path,
            minutes_path=minutes_path,
            branch=branch,
        )

        with _temporary_worktree(repo_path, cfg.default_branch) as worktree_path:
            if not direct:
                _run_git(worktree_path, ["checkout", "-b", branch])

            rel_paths = _stage_files(worktree_path, cfg, transcript_path, minutes_path)
            _run_git(worktree_path, ["add", "--", *rel_paths])
            commit_msg = _format_template(cfg.commit_message_template, variables)
            _run_git(worktree_path, ["commit", "-m", commit_msg])

            if direct:
                _push_direct(worktree_path, cfg.default_branch)
                result_text = f"{repo_path.name}@{cfg.default_branch}: {commit_msg}"
            else:
                _run_git(
                    worktree_path, ["push", "-u", "origin", branch], timeout=_PUSH_TIMEOUT_SEC
                )
                result_text = _create_pr(worktree_path, cfg, branch, variables) or branch
    except AutoPRError as e:
        logger.warning("auto_pr aborted: %s", e)
        notifier.notify(labels.failed, str(e)[:200])
        return False
    except Exception as e:  # noqa: BLE001 — never propagate
        logger.exception("auto_pr unexpected failure")
        notifier.notify(labels.failed, f"{type(e).__name__}: {str(e)[:160]}")
        return False

    notifier.notify(labels.done, result_text)
    logger.info("auto_pr finished: %s (mode=%s, branch=%s)", result_text, cfg.mode, branch)
    return True


def _push_direct(worktree_path: Path, default_branch: str) -> None:
    """Push the detached HEAD commit to origin/default_branch.

    If someone else pushed in the meantime (non-fast-forward), fetch and rebase
    onto the new tip, then retry. Never force-push.
    """
    refspec = f"HEAD:refs/heads/{default_branch}"
    last_error: AutoPRError | None = None
    for attempt in range(_DIRECT_PUSH_ATTEMPTS):
        if attempt > 0:
            _run_git(worktree_path, ["fetch", "origin", default_branch])
            try:
                _run_git(worktree_path, ["rebase", f"origin/{default_branch}"])
            except AutoPRError:
                _try_rebase_abort(worktree_path)
                raise
        try:
            _run_git(worktree_path, ["push", "origin", refspec], timeout=_PUSH_TIMEOUT_SEC)
            return
        except AutoPRError as e:
            last_error = e
            logger.warning("direct push attempt %d failed: %s", attempt + 1, e)
            # 認証エラーや保護ブランチなど、rebase しても直らない拒否は再試行しない
            if not _NON_FAST_FORWARD_RE.search(str(e)):
                raise
    assert last_error is not None
    raise last_error


def _try_rebase_abort(worktree_path: Path) -> None:
    try:
        _run_git(worktree_path, ["rebase", "--abort"])
    except Exception:  # noqa: BLE001 — cleanup must not raise
        logger.exception("failed to abort rebase in %s", worktree_path)


def _preflight(
    repo_path: Path,
    cfg: AutoPRConfig,
    transcript_path: Path,
    minutes_path: Path | None,
    *,
    require_gh: bool = True,
) -> None:
    if not repo_path.is_dir():
        raise AutoPRError(f"repo_path が存在しません: {repo_path}")
    if not (repo_path / ".git").exists():
        raise AutoPRError(f"git リポジトリではありません: {repo_path}")
    if shutil.which("git") is None:
        raise AutoPRError("git コマンドが見つかりません")
    if require_gh and shutil.which("gh") is None:
        raise AutoPRError("gh CLI が見つかりません")
    if not transcript_path.is_file():
        raise AutoPRError(f"transcript が見つかりません: {transcript_path}")
    if minutes_path is not None and not minutes_path.is_file():
        raise AutoPRError(f"minutes が見つかりません: {minutes_path}")
@contextmanager
def _temporary_worktree(repo_path: Path, default_branch: str) -> Iterator[Path]:
    """Yield a disposable worktree based on origin/default_branch."""
    with tempfile.TemporaryDirectory(prefix="mlx-audio-transcriptor-") as temp_dir:
        worktree_path = Path(temp_dir) / "worktree"
        added = False
        try:
            _run_git(repo_path, ["fetch", "origin", default_branch])
            _run_git(
                repo_path,
                ["worktree", "add", "--detach", str(worktree_path), f"origin/{default_branch}"],
            )
            added = True
            yield worktree_path
        finally:
            if added:
                _try_remove_worktree(repo_path, worktree_path)


def _try_remove_worktree(repo_path: Path, worktree_path: Path) -> None:
    try:
        _run_git(repo_path, ["worktree", "remove", "--force", str(worktree_path)])
    except Exception:  # noqa: BLE001 — cleanup must not raise
        logger.exception("failed to remove temporary worktree %s", worktree_path)
        try:
            _run_git(repo_path, ["worktree", "prune"])
        except Exception:  # noqa: BLE001 — cleanup must not raise
            logger.exception("failed to prune temporary worktree metadata")


def _stage_files(
    repo_path: Path,
    cfg: AutoPRConfig,
    transcript_path: Path,
    minutes_path: Path | None,
) -> list[str]:
    rel_paths: list[str] = []
    repo_root = repo_path.resolve()

    transcript_dest_dir = (repo_root / cfg.transcript_subdir).resolve()
    _ensure_within(repo_root, transcript_dest_dir, "transcript_subdir")
    transcript_dest_dir.mkdir(parents=True, exist_ok=True)
    transcript_dest = transcript_dest_dir / transcript_path.name
    shutil.copy2(transcript_path, transcript_dest)
    rel_paths.append(str(transcript_dest.relative_to(repo_root)))

    if minutes_path is not None:
        minutes_dest_dir = (repo_root / cfg.minutes_subdir).resolve()
        _ensure_within(repo_root, minutes_dest_dir, "minutes_subdir")
        minutes_dest_dir.mkdir(parents=True, exist_ok=True)
        minutes_dest = minutes_dest_dir / minutes_path.name
        shutil.copy2(minutes_path, minutes_dest)
        rel_paths.append(str(minutes_dest.relative_to(repo_root)))

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
        # push の stderr は先頭が "To <remote>" で始まり理由が末尾に来るため、末尾を残す
        raise AutoPRError(f"git {args[0]} 失敗: {result.stderr.strip()[-160:]}")
    return result.stdout


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
