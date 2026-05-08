import time
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from app.services import file_naming, markdown_writer, transcriber


def _fmt_sec(sec: float) -> str:
    s = int(sec)
    return f"{s // 60:02d}:{s % 60:02d}"


class TranscriptionWorker(QThread):
    log_message = Signal(str, str)             # level, message
    status_update = Signal(str)
    progress = Signal(float)                   # overall_percent 0-100
    finished = Signal(bool, int, int)          # had_errors, success_count, failure_count

    def __init__(self, files: list[Path], language: str, model: str) -> None:
        super().__init__()
        self._files = files
        self._language = language
        self._model = model

    def run(self) -> None:
        total_files = len(self._files)
        had_errors = False
        success_count = 0
        failure_count = 0

        for i, path in enumerate(self._files, 1):
            self.log_message.emit("INFO", f"Start: {path}")
            file_start = time.monotonic()

            def on_progress(processed: int, total_frames: int, elapsed: float, _i=i) -> None:
                in_file = processed / total_frames if total_frames else 0.0
                overall = ((_i - 1) + in_file) / total_files * 100
                self.progress.emit(overall)

                rate = processed / elapsed if elapsed > 0 else 0.0
                if rate > 0:
                    eta = (total_frames - processed) / rate
                    eta_str = f"残り {_fmt_sec(eta)}"
                else:
                    eta_str = "残り 推定中…"
                pct = int(in_file * 100)
                self.status_update.emit(
                    f"{total_files}件中 {_i}件目を処理中 — {pct}% "
                    f"(経過 {_fmt_sec(elapsed)} / {eta_str})"
                )

            try:
                result = transcriber.transcribe(
                    path, self._model, self._language, progress_callback=on_progress
                )
                output_path = file_naming.resolve_output_path(path)
                markdown_writer.write(result, output_path)
                self.log_message.emit("INFO", f"Saved: {output_path}")
                success_count += 1
            except Exception as e:
                had_errors = True
                failure_count += 1
                self.log_message.emit("ERROR", f"Failed: {path} — {e}")

        self.finished.emit(had_errors, success_count, failure_count)
