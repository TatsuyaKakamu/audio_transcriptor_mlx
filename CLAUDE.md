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
                                  ↓ (VAD前処理。[vad].enabled のときだけ)
                          vad.preprocess_with_vad()  →  silero_vad で無音区間除去
                                  ↓
                          mlx_whisper.transcribe()
                                  ↓
                          normalize_segments()  →  VADタイムラインを元タイムラインに再マッピング（VAD 有効時）
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
VAD は既定で無効（`config.VadConfig.enabled = False`）。silero VAD が小さい声・遠い話者を無音と判定し、数十分単位で発話を捨てたため。`transcriber.transcribe()` は `vad: VadConfig` / `mixed: MixedConfig` を受け取り、CLI・GUI は `AppConfig` の値を渡す。`services/vad.py` は無音除去済み PCM 配列と元タイムラインへの対応区間リストを返す。`transcriber.py` の `normalize_segments()` が `remap_timestamp()` でタイムスタンプを元の時刻に戻す。VAD 失敗時は警告ログを出し、ファイルパス文字列にフォールバックして処理を継続する。音声の読み込みは `mlx_whisper.audio.load_audio`（ffmpeg）で 16kHz mono に変換する（resampy は依存に無い）。

### mixed 言語モード
`language == "mixed"` のとき `transcriber._transcribe_mixed()` を通る。発話区間を `mixed_language.split_long_intervals()` で 30 秒以下に分割 → 区間ごとに `_detect_ja_en()`（mlx-whisper 内部の `ModelHolder` / `log_mel_spectrogram` / `detect_language` に依存。`n_mels` はモデルから取り、float16 で読む）→ `resolve_languages()` で短い・失敗・あいまいな区間は直前の言語を引き継ぐ（en と判定するには `switch_margin` = `[mixed].en_min_margin` の厳しい確率差が要る。`switch_margin` 指定時、判定したが差が足りない区間は、1 位が ja なら ja、1 位が en なら直前を引き継ぐ。短い・失敗区間は従来どおり引き継ぐ） → `group_by_language()` で同じ言語の連続区間をまとめ、グループごとに `mlx_whisper.transcribe(language=…)`。en グループの結果が空でなく 3 秒以上で 1 秒あたり 3 文字未満なら ja で起こし直し、長い方を採る（起こし直し中は進捗を送らない。起こし直しの例外は警告して en の結果を残す）。時刻はグループの区間で `remap_timestamp()` する。`ModelHolder` は dtype をキャッシュキーに含まないため、判定側も float16 で読むこと。判定用 mel は transcribe と同じく `log_mel_spectrogram(padding=N_SAMPLES)` で音声側を無音パディングしてから `pad_or_trim(axis=-2)` する（log-mel 空間を 0 で埋めると短い区間の判定が崩れる）。判定前に `_check_multilingual()` で英語専用モデルを止め、モデル読み込み失敗・判定の 3 回連続失敗・ImportError/OSError では判定を打ち切って直前の言語を継承する。進捗の母数は区間のサンプル数（音声長で打ち切り）// 160（mlx の tqdm の total と一致）。

### 通知
`services/notifier.py` は `osascript` を `subprocess.run` で実行し、失敗は黙殺する。`transcriber.transcribe()` は `tqdm.tqdm` クラスを一時差し替えて `update()` フックからコールバックへ渡す。CLI 経路は開始・進捗（25/50/75%）・完了の 3 段階で通知する。GUI 経路（`TranscriptionWorker`）は完了時のみ通知する。議事録生成・PR 作成それぞれも「〜中…」「〜完了」「〜失敗」の 3 段階を通知する。

### 議事録生成（Ollama）
- `minutes.run_for()` は全例外を握り潰して `None` を返す（best-effort）
- `minutes_generator.py` は `urllib.request` のみで Ollama `POST /api/generate`（`format=json`, `stream=False`）を呼び出す。レスポンスの ` ```json...``` ` フェンスを許容し `{"topic", "filename_slug", "minutes_markdown"}` を取り出す
- `filename_slug`（英語）欠落時は空文字にフォールバック（writer 側で `minutes` に補完）
- `minutes_writer.py`: `sanitize_slug` で ASCII 以外を除去してファイル名を**必ず英語化**（空なら `minutes`）。`sanitize_topic` は日本語のまま整形（パス区切り文字・制御文字除去、空白→`_`）

### 自動 PR (`auto_pr.publish_pair()`)
- 全例外を握り潰して `False` を返す（best-effort）。`False` のとき `_transcribe_one()` は `trash_source_after_success` をスキップする
- preflight: `repo_path` 存在・`.git`・`git`/`gh` バイナリ・トランスクリプトと議事録の存在を確認
- `origin/<default_branch>` から一時 `git worktree` を作り、その中でブランチ作成・コミット・push・PR作成を完結する。通常のローカルクローンの変更・現在のブランチには触れない
- `mode = "direct"` では一時 worktree（detached）でコミットし `git push origin HEAD:refs/heads/<default_branch>` する。ブランチ作成と `gh` は使わない（preflight の `gh` 確認も省く）。拒否されたら `fetch` → `rebase origin/<default_branch>` して計 `_DIRECT_PUSH_ATTEMPTS`(3) 回まで push。rebase 失敗時は `rebase --abort` して失敗扱い。force push はしない。未知の `mode` は警告して `"pr"` に戻す
- `transcript_subdir` / `minutes_subdir` がリポジトリ外を指す場合は `relative_to()` で abort
- テンプレート変数: `{date}`, `{transcript_name}`, `{minutes_name}`, `{topic}`, `{branch}`（未定義は空文字、構文エラー時は生のテンプレートを使用）
- `config.toml.example` には**プレースホルダのみ**記載（実在パス・GitHub ID は書かない。config.toml 本体は gitignore 対象）

### モデル解決
`transcriber.py` の `_MODEL_REPO_MAP` でモデル名を HuggingFace リポジトリ名にマッピング。未登録名は `mlx-community/whisper-{name}-mlx` で自動補完。

### 設定
`app/config.py` の `load_config()` が `~/.config/mlx-audio-transcriptor/config.toml` を読む（なければコード内デフォルト）。設定キーとデフォルト値は [`config.toml.example`](config.toml.example) を参照。

## テスト

`services/` 層は GUI なしで単体テスト可能。`tests/conftest.py` が `mlx_whisper` / `tqdm` のスタブを差し込むため macOS 以外でも動く。`minutes_generator.py` の autouse fixture `_block_real_http` が実 HTTP を遮断する。`auto_pr.py` は `subprocess.run` を `monkeypatch.setattr` で差し替えて検証する。

`transcriber.py` の mixed 経路は `vad._load_audio` / `vad.detect_speech_intervals` / `_detect_ja_en` / `mlx_whisper.transcribe` を monkeypatch してテストする。実際の mlx-whisper / silero_vad の推論はテスト対象外。

テストファイル: `test_file_naming.py`, `test_markdown_writer.py`, `test_segment_merger.py`, `test_config.py`, `test_cli_scan.py`, `test_notifier.py`, `test_progress.py`, `test_minutes_generator.py`, `test_minutes_orchestrator.py`, `test_minutes_writer.py`, `test_auto_pr.py`, `test_vad.py`, `test_mixed_language.py`, `test_transcriber_mixed.py`
