# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.
セットアップ・起動・使い方・出力フォーマットは [README.md](README.md) を参照。

## コマンド

```bash
# セットアップ
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# テスト
pytest
pytest tests/test_file_naming.py   # 特定テスト
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
                          mlx_whisper.transcribe()
                                  ↓
                          normalize_segments()  →  VADタイムラインを元タイムラインに再マッピング
                                  ↓
                          file_naming.resolve_output_path()
                                  ↓
                          markdown_writer.write()
                                  ↓ ([minutes].enabled なら)
                          minutes.run_for()  →  minutes_generator → Ollama → minutes_writer
```

### データフロー（CLI / launchd）

```
launchd WatchPaths=~/Downloads → app.cli scan
    ↓  fcntl.flock で重複起動を抑止
_process_pending()  ─  _scan_once() を「処理件数 0」になるまで再実行（最大 _RESCAN_MAX_PASSES 回）
    ↓
_scan_once()  ─  拡張子・既処理判定（*.transcript.md 有無）・stability wait
    ↓
_transcribe_one()  →  transcribe → markdown_writer → [minutes.run_for] → [auto_pr.publish_pair] → [send2trash]
```

## 実装上の制約と非自明な詳細

### スレッド境界
`TranscriptionWorker` は `QThread` で動作。UI スレッドからワーカーへの直接呼び出しは禁止（Signal 経由のみ）。Signals: `log_message(level, message)`, `status_update(text)`, `progress(float)`, `finished(had_errors, success_count, failure_count)`。

### VAD タイムスタンプ
`services/vad.py` は無音除去済み PCM 配列と元タイムラインへの対応区間リストを返す。`transcriber.py` の `normalize_segments()` が `remap_timestamp()` でタイムスタンプを元の時刻に戻す。VAD 失敗時はファイルパス文字列にフォールバックして処理を継続する。

### 通知
`services/notifier.py` は `osascript` を `subprocess.run` で実行し、失敗は黙殺する。`transcriber.transcribe()` は `tqdm.tqdm` クラスを一時差し替えて `update()` フックからコールバックへ渡す。CLI 経路は開始・進捗（25/50/75%）・完了の 3 段階で通知する。GUI 経路（`TranscriptionWorker`）は完了時のみ通知する。議事録生成・PR 作成それぞれも「〜中…」「〜完了」「〜失敗」の 3 段階を通知する。

### 議事録生成（Ollama）
- `minutes.run_for()` は全例外を握り潰して `None` を返す（best-effort）
- `minutes_generator.py` は `urllib.request` のみで Ollama `POST /api/generate`（`format=json`, `stream=False`）を呼び出す。レスポンスの ` ```json...``` ` フェンスを許容し `{"topic", "filename_slug", "minutes_markdown"}` を取り出す
- `filename_slug`（英語）欠落時は空文字にフォールバック（writer 側で `minutes` に補完）
- `minutes_writer.py`: `sanitize_slug` で ASCII 以外を除去してファイル名を**必ず英語化**（空なら `minutes`）。`sanitize_topic` は日本語のまま整形（パス区切り文字・制御文字除去、空白→`_`）

### 自動 PR (`auto_pr.publish_pair()`)
- 全例外を握り潰して `False` を返す（best-effort）。`False` のとき `_transcribe_one()` は `trash_source_after_success` をスキップする
- preflight: `repo_path` 存在・`.git`・`git`/`gh` バイナリ・`git status --porcelain` が空（dirty なら abort）
- `git reset --hard origin/<default_branch>` でクリーン状態にしてからブランチを作成
- `transcript_subdir` / `minutes_subdir` がリポジトリ外を指す場合は `relative_to()` で abort
- テンプレート変数: `{date}`, `{transcript_name}`, `{minutes_name}`, `{topic}`, `{branch}`（未定義は空文字、構文エラー時は生のテンプレートを使用）
- `config.toml.example` には**プレースホルダのみ**記載（実在パス・GitHub ID は書かない。config.toml 本体は gitignore 対象）

### モデル解決
`transcriber.py` の `_MODEL_REPO_MAP` でモデル名を HuggingFace リポジトリ名にマッピング。未登録名は `mlx-community/whisper-{name}-mlx` で自動補完。

### 設定
`app/config.py` の `load_config()` が `~/.config/mlx-audio-transcriptor/config.toml` を読む（なければコード内デフォルト）。設定キーとデフォルト値は [`config.toml.example`](config.toml.example) を参照。

## テスト

`services/` 層は GUI なしで単体テスト可能。`tests/conftest.py` が `mlx_whisper` / `tqdm` のスタブを差し込むため macOS 以外でも動く。`minutes_generator.py` の autouse fixture `_block_real_http` が実 HTTP を遮断する。`auto_pr.py` は `subprocess.run` を `monkeypatch.setattr` で差し替えて検証する。

`transcriber.py` と `vad.py` は `mlx-whisper` / `silero_vad` 依存のためテスト対象外。

テストファイル: `test_file_naming.py`, `test_markdown_writer.py`, `test_segment_merger.py`, `test_config.py`, `test_cli_scan.py`, `test_notifier.py`, `test_progress.py`, `test_minutes_generator.py`, `test_minutes_orchestrator.py`, `test_minutes_writer.py`, `test_auto_pr.py`
