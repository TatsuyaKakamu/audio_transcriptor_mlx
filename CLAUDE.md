# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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

# テスト全件
pytest

# 特定テスト
pytest tests/test_file_naming.py
pytest tests/test_markdown_writer.py
```

## アーキテクチャ

### データフロー

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

ロジック層（`services/`）は GUI なしで単体テスト可能。`tests/` にはファイル命名と Markdown 生成のテストがある。`transcriber.py` と `vad.py` は `mlx-whisper` / `silero_vad` への依存があるためテスト外。

## 現時点の制限

- キャンセル・一時停止不可（処理中ドロップは無視）
- 話者分離・自動言語判定・動画ファイル非対応
- 設定永続化なし（毎回 GUI から選択）
- `.app` 化非対応
