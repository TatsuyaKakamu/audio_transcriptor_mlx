# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 実行環境

- macOS Apple Silicon 必須
- Python 3.11 以上
- 仮想環境: `.venv`
- `ffmpeg`（mlx-whisper の音声デコードに必要、`brew install ffmpeg`）
- Ollama（議事録生成を使う場合のみ。`brew install ollama` + `ollama pull <model>`）

## コマンド

```bash
# 事前準備（システムに ffmpeg が無ければ）
brew install ffmpeg

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
                                  ↓ ([minutes].enabled なら)
                          minutes.run_for()
                              ↓
                          minutes_generator.generate_minutes()  →  Ollama POST /api/generate (format=json)
                              ↓
                          minutes_writer.build_minutes_markdown()
                              ↓
                          <YYYY-MM-DD>_<filename_slug>.md （on_log でログペインへ通知）
```

### データフロー（CLI / launchd）

```
launchd WatchPaths=~/Downloads
    ↓
app.cli scan
    ↓  fcntl.flock で重複起動を抑止
_process_pending()  ─  _scan_once() を「処理件数 0」になるまで再実行（処理中に追加されたファイルも拾う、最大 _RESCAN_MAX_PASSES 回）
    ↓
_scan_once()  ─  拡張子・既処理判定（*.transcript.md 有無）・stability wait
    ↓
_transcribe_one()
    ├─ notifier.notify("文字起こし開始", …)
    ├─ progress.make_milestone_callback() を渡して
    │     transcriber.transcribe(progress_callback=…)
    │       └ tqdm を一時差し替えて 25/50/75% で notify
    ├─ markdown_writer.write()
    ├─ notifier.notify("文字起こし完了", …)
    ├─ [minutes].enabled なら
    │     minutes.run_for()  →  minutes_generator → Ollama → minutes_writer
    │       ├─ notifier.notify("議事録生成中…") / notifier.notify("議事録生成完了") / notifier.notify("議事録生成失敗")
    │       └─ <YYYY-MM-DD>_<filename_slug>.md を書き出す（失敗しても以降の処理に影響しない）
    └─ trash_source_after_success が真なら send2trash
```

### 設定 (`~/.config/mlx-audio-transcriptor/config.toml`)

`app/config.py` の `load_config()` が GUI / CLI 共通で TOML を読む。ファイルが無ければコード内デフォルトを使う。既知キー: `language`, `model`, `watch_dir`, `extensions`, `file_stability_seconds`, `trash_source_after_success`、および `[minutes]` テーブル（`enabled`, `ollama_host`, `model`, `prompt_language`, `num_ctx`, `max_input_chars`, `request_timeout_seconds`）、`[auto_pr]` テーブル（`enabled`, `repo_path`, `transcript_subdir`, `minutes_subdir`, `default_branch`, `branch_prefix`, `commit_message_template`, `pr_title_template`, `pr_body_template`, `gh_repo`）。GUI 起動時にこれを読んでコンボボックスの初期値を設定する。`install-watcher.sh` が未存在時に `config.toml.example` を自動コピーする。

### 通知

`services/notifier.py` は `osascript` を `subprocess.run` で叩き、失敗は黙殺する。`services/progress.py` の `make_milestone_callback(filename)` は 25 / 50 / 75% を一度ずつ通知する。`transcriber.transcribe()` は `tqdm.tqdm` クラスを一時差し替えて `update()` フックから `(processed, total, elapsed)` をコールバックへ渡す。GUI 経路（`TranscriptionWorker`）は完了時のみ通知する（成功・失敗件数を含む）。CLI 経路は開始・進捗・完了の 3 段階で通知する。

### スレッド境界

- `TranscriptionWorker` は `QThread` で動作し、`Signal` で UI に通知する
  - `log_message(level, message)` — ログペイン追記
  - `status_update(text)` — ステータス1行表示
  - `progress(float)` — プログレスバー 0–100%
  - `finished(had_errors, success_count, failure_count)` — 完了通知
- UI スレッドからワーカーへの直接呼び出しは禁止

### VAD（音声区間検出）

`services/vad.py` は Silero VAD でファイルを前処理し、無音区間を除去した PCM 配列と元タイムラインへの対応区間リストを返す。`transcriber.py` の `normalize_segments()` が `remap_timestamp()` を使ってセグメントのタイムスタンプを元の時刻に戻す。VAD が失敗した場合はファイルパス文字列にフォールバックして処理を続行する。

### 議事録生成（Ollama）

- `services/minutes.py` がオーケストレーター。`run_for()` は `MinutesGenerationError` も予期せぬ例外も握り潰して `None` を返す（best-effort）。`cfg.enabled=False` なら即 `None` を返してスキップ
- `services/minutes_generator.py` が Ollama に `POST /api/generate` を発行（`format=json`、`stream=False`、`options.num_ctx`）。レスポンスの ` ```json ... ``` ` フェンスを許容し `{"topic", "filename_slug", "minutes_markdown"}` を取り出す。`topic`（日本語、フロントマター用）と `filename_slug`（英語、ファイル名用）は別フィールド。`filename_slug` 欠落時は空文字にフォールバック（writer 側で `minutes` に補完）。`max_input_chars` 超過は先頭から切り詰めて WARN ログを出す。Python 依存は stdlib `urllib.request` のみ
- `services/minutes_writer.py`: フロントマターの `topic` は `sanitize_topic`（パス区切り文字・制御文字除去、空白→`_`）で日本語のまま整形。ファイル名は `sanitize_slug`（ASCII 以外を除去して必ず英語化、空なら `minutes`）→ `derive_minutes_filename`（音声ファイル mtime 基準の日付を使用）→ 衝突時 `.N.md` 採番でファイルを書く。**本文は日本語、ファイル名は必ず英語**
- 入力テキストは `minutes_generator.transcript_plain_text()` がセグメントテキストを改行連結して生成（タイムスタンプは除外）
- GUI は `on_log` 経由でログペインへ、CLI は `notifier.notify` 経由で macOS 通知センターへ「議事録生成中…」「議事録生成完了」「議事録生成失敗」を送る

### 自動 PR (`[auto_pr]`)

CLI 経路 (`app/cli.py:_transcribe_one()`) の議事録ブロック直後で `services/auto_pr.publish_pair()` を呼ぶ。`cfg.auto_pr.enabled=False` ならスキップ。GUI 経路（`TranscriptionWorker`）には組み込んでいない（必要なら同じ関数を再利用）。

`publish_pair()` のフロー:

1. preflight: `repo_path` の存在 / `.git` ディレクトリ / `git` & `gh` バイナリ / 入力ファイル / `git status --porcelain` が空（dirty 検知でユーザー作業を保護）
2. `git fetch origin <default_branch>` → `git checkout <default_branch>` → `git reset --hard origin/<default_branch>` でクリーン状態にする
3. `git checkout -b <branch_prefix><YYYY-MM-DD>-<6文字英数字>` で新ブランチ作成
4. `transcript_subdir` と `minutes_subdir` に `shutil.copy2` でコピー → `git add -- <相対パス>` → `git commit -m <commit_message_template>`
5. `git push -u origin <branch>` → `gh pr create --head <branch> --base <default_branch> --title ... --body ...`（`gh_repo` 指定時は `-R` 追加）
6. 成否に関わらず最後に `git checkout <default_branch>` で main に戻す（best-effort）

通知（議事録生成と同じく 3 段階）:
- preflight 通過後（ブランチ作成前）に `notifier.notify("PR 作成中…", repo名)` を出す
- 成功時: `notifier.notify("PR 作成完了", PR URL)`
- 失敗時: `notifier.notify("PR 作成失敗", 短い理由)`
- preflight 段階の失敗（repo_path 不在・dirty 検知など）は開始通知を出さず、失敗通知のみ

失敗時の挙動（best-effort）:
- 全ての例外を握り潰して `False` を返す
- `_transcribe_one()` 側は `publish_pair()` が `False` を返したら `trash_source_after_success` をスキップする（後から手動 push できるよう元音声を残す）

ブランチ名・コミットメッセージ・PR タイトル/本文のテンプレート変数: `{date}`, `{transcript_name}`, `{minutes_name}`, `{topic}`, `{branch}`。未定義変数は空文字に置換（`defaultdict(str) + format_map`）。テンプレート構文エラー時は生のテンプレート文字列を使う。

日付の決定: 議事録ファイル名先頭 `YYYY-MM-DD` を優先、なければ音声ファイル `mtime`。

セキュリティ上の前提:
- mlx-audio-transcriptor リポジトリは public なので、`config.toml.example` には**プレースホルダのみ**書く。実在パス・GitHub ID・運用固有のタイトル文言は書かない
- `~/.config/mlx-audio-transcriptor/config.toml` 本体は git ignore 対象、ユーザー個人運用に閉じる
- トランスクリプト全文が push 先リポジトリにコミットされるため、push 先は **private リポジトリに限定**する旨を README に明記
- 認証は実行ユーザーの `gh` CLI 認証情報に依存（`gh auth status` で確認）

`transcript_subdir` / `minutes_subdir` がリポジトリ外を指す場合（`../escape` など）は `relative_to()` 検査で abort する。

### モデル解決

`transcriber.py` の `_MODEL_REPO_MAP` でモデル名を HuggingFace リポジトリ名にマッピングする。未登録名は `mlx-community/whisper-{name}-mlx` として自動補完する。

### ファイル命名

`file_naming.resolve_output_path()` は `meeting.wav` → `meeting.transcript.md` を生成し、衝突時は `meeting.transcript.1.md`, `meeting.transcript.2.md` と最小未使用番号で採番する。

## 出力フォーマット

### トランスクリプト（`*.transcript.md`）

```markdown
---
language: ja
model: medium
---

## Transcript

- [00:00.000 - 00:03.200] おはようございます。
- [01:02:03.456 - 01:02:08.000] 1時間超えは HH:MM:SS.mmm 形式
```

### 議事録（`<YYYY-MM-DD>_<filename_slug>.md`、ファイル名は必ず英語）

```markdown
---
date: 2026-05-08
source_audio: meeting.wav
transcript: meeting.transcript.md
language: ja
whisper_model: medium
ollama_model: gemma4
topic: 予算会議
---

（Ollama が生成した本文 Markdown）

---
原文書き起こし: [meeting.transcript.md](meeting.transcript.md)
```

## テスト対象モジュール

ロジック層（`services/`）は GUI なしで単体テスト可能。`tests/conftest.py` が `mlx_whisper` / `tqdm` のスタブを差し込むため、macOS 以外の CI 環境でもロジック層テストが動く。

テストファイル: `test_file_naming.py`, `test_markdown_writer.py`, `test_segment_merger.py`, `test_config.py`, `test_cli_scan.py`, `test_notifier.py`, `test_progress.py`, `test_minutes_generator.py`, `test_minutes_orchestrator.py`, `test_minutes_writer.py`, `test_auto_pr.py`。`transcriber.py` と `vad.py` は `mlx-whisper` / `silero_vad` への依存があるためテスト外。`minutes_generator.py` は autouse fixture `_block_real_http` が実 HTTP を遮断するためロジック単体でテスト可能。`auto_pr.py` は `subprocess.run` を `monkeypatch.setattr` で差し替えて git/gh コマンド呼び出しを検証する。

## 現時点の制限

- キャンセル・一時停止不可（処理中ドロップは無視）
- 話者分離・自動言語判定・動画ファイル非対応
- 設定変更はアプリ再起動で反映。launchd の `WatchPaths` は plist に焼き付くため、`watch_dir` を変更した場合は `./scripts/install-watcher.sh` を再実行する必要がある
- `.app` 化非対応
