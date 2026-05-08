"""Pytest fixtures/stubs.

On machines without the macOS-only ML dependencies installed (e.g. CI on Linux)
we install lightweight stubs so pure-Python tests can still import modules that
transitively pull in `mlx_whisper`. On a real install the real modules are used.
"""

from __future__ import annotations

import importlib
import sys
import types


def _ensure_stub(name: str) -> None:
    try:
        importlib.import_module(name)
    except Exception:
        sys.modules[name] = types.ModuleType(name)


_ensure_stub("mlx_whisper")

# `tqdm` is pulled in by app.services.transcriber; provide a minimal stub so
# tests that import cli/transcriber transitively can still collect.
if "tqdm" not in sys.modules:
    try:
        importlib.import_module("tqdm")
    except Exception:
        _tqdm_stub = types.ModuleType("tqdm")

        class _Tqdm:  # pragma: no cover - only used if real tqdm missing
            def __init__(self, *args, **kwargs):
                pass

            def update(self, n=1):
                pass

            def close(self):
                pass

        _tqdm_stub.tqdm = _Tqdm
        sys.modules["tqdm"] = _tqdm_stub
