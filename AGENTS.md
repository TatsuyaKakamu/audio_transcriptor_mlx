# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## 実行環境

- macOS Apple Silicon 必須
- Python 3.11 以上
- 仮想環境: `.venv`

## コマンド

```bash
# セットアップ
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# アプリ起動
python -m app.main

# ヘッドレス CLI（launchd watcher と同一エントリ）
python -m app.cli scan

# launchd watcher 登録 / 解除
./scripts/install-watcher.sh
./scripts/uninstall-watcher.sh

# テスト全件
pytest

# 特定テスト
pytest tests/test_file_naming.py
pytest tests/test_markdown_writer.py
```

## アーキテクチャ

### データフロー（GUI）

```
DropArea (DnD) → MainWindow → TranscriptionWorker (QThread)
                                  ↓
                          transcriber.transcribe()
                                  ↓ (VAD前処理)
                          vad.preprocess_with_vad()  →  silero_vad で無音区間除去
                                  ↓
                          mlx_whisper.transcribe()  →  mlx-community HuggingFace repo
                                  ↓
                          normalize_segments()  →  VADタイムラインを元タイムラインに再マッピング
                                  ↓
                          file_naming.resolve_output_path()
                                  ↓
                          markdown_writer.write()
```

### データフロー（CLI / launchd）

```
launchd WatchPaths=~/Downloads
    ↓
app.cli scan
    ↓  fcntl.flock で重複起動を抑止
_process_pending()  ─  拡張子・既処理判定（*.transcript.md 有無）・stability wait
    ↓
_transcribe_one()
    ├─ notifier.notify("文字起こし開始", …)
    ├─ progress.make_milestone_callback() を渡して
    │     transcriber.transcribe(progress_callback=…)
    │       └ tqdm を一時差し替えて 25/50/75% で notify
    ├─ markdown_writer.write()
    ├─ notifier.notify("文字起こし完了", …)
    └─ trash_source_after_success が真なら send2trash
```

### 設定 (`~/.config/mlx-audio-transcriptor/config.toml`)

`app/config.py` の `load_config()` が GUI / CLI 共通で TOML を読む。ファイルが無ければコード内デフォルトを使う。既知キー: `language`, `model`, `watch_dir`, `extensions`, `file_stability_seconds`, `trash_source_after_success`、および `[minutes]` / `[auto_pr]` テーブル。GUI 起動時にこれを読んでコンボボックスの初期値を設定する。`install-watcher.sh` が未存在時に `config.toml.example` を自動コピーする。

### 自動 PR (`[auto_pr]`、CLI 経路のみ)

`services/auto_pr.publish_pair()` が CLI 経路 (`app/cli.py:_transcribe_one()`) の議事録ブロック直後で呼ばれる。`cfg.auto_pr.enabled=False` ならスキップ。

フロー: dirty 検知 → `git fetch/checkout/reset` でクリーン状態 → 新ブランチ → md コピー → commit → push → `gh pr create`。失敗時は `False` を返して `notifier.notify` で通知、`trash_source_after_success` もスキップ（best-effort）。

セキュリティ上の前提: mlx-audio-transcriptor は public リポジトリのため、コード・`config.toml.example` ・ドキュメントには個人の GitHub ID やリポジトリ名、絶対パスを書かない。push 先は private リポジトリに限定する旨を README に明記。

### 通知 (CLI のみ)

`services/notifier.py` は `osascript` を `subprocess.run` で叩き、失敗は黙殺する。`services/progress.py` の `make_milestone_callback(filename)` は 25 / 50 / 75% を一度ずつ通知する。`transcriber.transcribe()` は `tqdm.tqdm` クラスを一時差し替えて `update()` フックから `(processed, total, elapsed)` をコールバックへ渡す。GUI 経路（`TranscriptionWorker`）は通知を出さない。

### スレッド境界

- `TranscriptionWorker` は `QThread` で動作し、`Signal` で UI に通知する
  - `log_message(level, message)` — ログペイン追記
  - `status_update(text)` — ステータス1行表示
  - `progress(float)` — プログレスバー 0–100%
  - `finished(had_errors)` — 完了通知
- UI スレッドからワーカーへの直接呼び出しは禁止

### VAD（音声区間検出）

`services/vad.py` は Silero VAD でファイルを前処理し、無音区間を除去した PCM 配列と元タイムラインへの対応区間リストを返す。`transcriber.py` の `normalize_segments()` が `remap_timestamp()` を使ってセグメントのタイムスタンプを元の時刻に戻す。VAD が失敗した場合はファイルパス文字列にフォールバックして処理を続行する。

### モデル解決

`transcriber.py` の `_MODEL_REPO_MAP` でモデル名を HuggingFace リポジトリ名にマッピングする。未登録名は `mlx-community/whisper-{name}-mlx` として自動補完する。

### ファイル命名

`file_naming.resolve_output_path()` は `meeting.wav` → `meeting.transcript.md` を生成し、衝突時は `meeting.transcript.1.md`, `meeting.transcript.2.md` と最小未使用番号で採番する。

## 出力フォーマット

```markdown
---
language: ja
model: medium
---

## Transcript

- [00:00.000 - 00:03.200] おはようございます。
- [01:02:03.456 - 01:02:08.000] 1時間超えは HH:MM:SS.mmm 形式
```

## テスト対象モジュール

ロジック層（`services/`）は GUI なしで単体テスト可能。`tests/conftest.py` が `mlx_whisper` / `tqdm` のスタブを差し込むため、macOS 以外の CI 環境でもロジック層テストが動く。

テストファイル: `test_file_naming.py`, `test_markdown_writer.py`, `test_segment_merger.py`, `test_config.py`, `test_cli_scan.py`, `test_notifier.py`, `test_progress.py`。`transcriber.py` と `vad.py` は `mlx-whisper` / `silero_vad` への依存があるためテスト外。

## 現時点の制限

- キャンセル・一時停止不可（処理中ドロップは無視）
- 話者分離・自動言語判定・動画ファイル非対応
- 設定変更はアプリ再起動で反映。launchd の `WatchPaths` は plist に焼き付くため、`watch_dir` を変更した場合は `./scripts/install-watcher.sh` を再実行する必要がある
- `.app` 化非対応
