from __future__ import annotations

import argparse
import fcntl
import logging
import sys
import time
from pathlib import Path

import send2trash

from app.config import AppConfig, load_config
from app.services import file_naming, markdown_writer, notifier, progress, transcriber

logger = logging.getLogger("mlx_audio_transcriptor.cli")

_LOCK_PATH = Path.home() / ".cache" / "mlx-audio-transcriptor" / "scan.lock"
_STABILITY_TIMEOUT_SEC = 120.0
_STABILITY_POLL_SEC = 1.0


def _already_transcribed(source: Path) -> bool:
    parent = source.parent
    stem = source.stem
    if (parent / f"{stem}.transcript.md").exists():
        return True
    for sibling in parent.glob(f"{stem}.transcript.*.md"):
        if sibling.is_file():
            return True
    return False


def _wait_until_stable(path: Path, stability_seconds: float) -> bool:
    if stability_seconds <= 0:
        return path.exists()

    deadline = time.monotonic() + _STABILITY_TIMEOUT_SEC
    last_size = -1
    last_mtime = -1.0
    last_change = time.monotonic()

    while time.monotonic() < deadline:
        try:
            st = path.stat()
        except FileNotFoundError:
            return False
        if st.st_size != last_size or st.st_mtime != last_mtime:
            last_size = st.st_size
            last_mtime = st.st_mtime
            last_change = time.monotonic()
        elif time.monotonic() - last_change >= stability_seconds:
            return True
        time.sleep(_STABILITY_POLL_SEC)

    logger.warning("stability timeout for %s; will retry on next event", path)
    return False


def _transcribe_one(path: Path, cfg: AppConfig) -> None:
    logger.info("transcribing: %s", path)
    notifier.notify("文字起こし開始", path.name)
    callback = progress.make_milestone_callback(path.name)
    result = transcriber.transcribe(path, cfg.model, cfg.language, progress_callback=callback)
    output_path = file_naming.resolve_output_path(path)
    markdown_writer.write(result, output_path)
    notifier.notify("文字起こし完了", f"{path.name} → {output_path.name}")
    logger.info("wrote markdown: %s", output_path)

    if cfg.trash_source_after_success:
        try:
            send2trash.send2trash(str(path))
            logger.info("moved to Trash: %s", path)
        except Exception as e:
            logger.warning("trash failed for %s: %s", path, e)


def _process_pending(cfg: AppConfig) -> int:
    if not cfg.watch_dir.is_dir():
        logger.warning("watch_dir does not exist: %s", cfg.watch_dir)
        return 0

    processed = 0
    errors = 0
    for path in sorted(cfg.watch_dir.iterdir()):
        if not path.is_file():
            continue
        if path.suffix.lower() not in cfg.extensions:
            continue
        if _already_transcribed(path):
            continue
        if not _wait_until_stable(path, cfg.file_stability_seconds):
            continue
        try:
            _transcribe_one(path, cfg)
            processed += 1
        except Exception as e:
            errors += 1
            logger.error("failed to transcribe %s: %s", path, e, exc_info=True)

    logger.info("scan done: processed=%d errors=%d", processed, errors)
    return 1 if errors else 0


def cmd_scan(cfg: AppConfig | None = None, lock_path: Path | None = None) -> int:
    cfg = cfg or load_config()
    lock_path = lock_path or _LOCK_PATH
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    with open(lock_path, "w") as lf:
        try:
            fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            logger.info("another scan is running, exiting")
            return 0
        return _process_pending(cfg)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="app.cli", description="mlx-audio-transcriptor headless CLI")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("scan", help="scan watch_dir for pending audio files and transcribe them")
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    args = _build_parser().parse_args(argv)
    if args.command == "scan":
        return cmd_scan()
    return 2


if __name__ == "__main__":
    sys.exit(main())
