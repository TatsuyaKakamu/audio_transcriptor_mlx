# mlx-audio-transcriptor

macOS Apple Silicon 上で動作する、ローカル音声文字起こし GUI アプリ。  
`wav` / `mp3` ファイルをドラッグ&ドロップすると `mlx-whisper` で文字起こしし、同フォルダに Markdown ファイルを保存する。

## 動作環境

- macOS（Apple Silicon 必須）
- Python 3.11 以上
- 仮想環境推奨
- `ffmpeg`（mlx-whisper が音声デコードに使用）

## 2 つの使い方

このアプリは 2 通りの使い方ができる。用途で選択する。

| 使い方 | 向いている場面 | 操作 |
|--------|---------------|------|
| **A. GUI アプリで手動文字起こし** | 任意のファイルをその都度処理したい | アプリを起動して `wav` / `mp3` をドラッグ&ドロップ |
| **B. Downloads フォルダの自動監視** | iPhone から AirDrop で送った音声を放置で処理したい | LaunchAgent をインストール（初回のみ）。以降は `~/Downloads` に置くだけ |

両方を併用してもよい。設定ファイル（`~/.config/mlx-audio-transcriptor/config.toml`）は GUI と CLI で共通で、議事録生成（Ollama 連携）も共通で動く。

## 事前準備

### ffmpeg

`mlx-whisper` は音声ファイルのデコードに `ffmpeg` を使用するため、システムに `ffmpeg` をインストールしておく必要がある。`.mp3` を扱う場合は必須。

```bash
brew install ffmpeg
```

インストール確認:

```bash
ffmpeg -version
```

### Ollama（議事録生成を使う場合）

文字起こし完了後、ローカルの Ollama を呼んで議事録 Markdown を自動生成する機能を既定で有効にしている。
Ollama 未インストールでも文字起こし自体は完走するが、議事録生成だけが毎回失敗する。
不要なら `~/.config/mlx-audio-transcriptor/config.toml` の `[minutes].enabled = false` で無効化できる。

```bash
brew install ollama
ollama serve &           # 別タブで起動しっぱなしにする
ollama pull gemma4       # 既定モデル。config.toml の [minutes].model と一致させる
```

`ollama list` で取得済みモデルを確認できる。

## インストール

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 使い方 A: GUI アプリで手動文字起こし

### 起動

```bash
python -m app.main
```

### 操作手順

1. 言語（`Japanese` / `English`）とモデルを選択する
2. `wav` または `mp3` ファイルをウィンドウにドラッグ&ドロップする
3. プログレスバーと経過時間／ETA がリアルタイムで更新される
4. 完了すると、入力ファイルと同じフォルダに `*.transcript.md` が生成される。
   `[minutes].enabled = true`（既定）かつ Ollama が動作していれば、続けて `<YYYY-MM-DD>_<議題>.md` という議事録ファイルも同じフォルダに生成される

複数ファイルを同時にドロップ可。逐次処理。

## 使い方 B: Downloads フォルダの自動監視（launchd）

iPhone から AirDrop などで `~/Downloads` に音声が入ったら、GUI を開かずに自動で文字起こしし、元ファイルをゴミ箱へ送るバックグラウンドモード。

### セットアップ（初回のみ）

```bash
./scripts/install-watcher.sh          # 登録（初回は config.toml を自動配置）
./scripts/uninstall-watcher.sh        # 解除
```

### 使い方

1. `~/Downloads` に `.wav` / `.mp3` を保存する（AirDrop / コピーなど）
2. 数十秒待つと同フォルダに `*.transcript.md`（および議事録 `.md`）が生成される
3. 元の音源は自動でゴミ箱へ移動される（`trash_source_after_success = false` で無効化可）
4. 処理開始 / 25%・50%・75% 進捗 / 完了は macOS 通知センターへ届く

### 詳細

フルディスクアクセス権限の付与、ログの確認、トラブルシュートなどは [`docs/mac-watcher-setup.md`](docs/mac-watcher-setup.md) を参照。設定ファイル `~/.config/mlx-audio-transcriptor/config.toml` は GUI の既定言語／モデル選択にも反映される。

## 処理の流れ

1. **VAD 前処理** — silero-vad で無音区間を除去し、発話区間のみ連結した PCM を生成する
2. **文字起こし** — mlx-whisper（Apple Silicon MLX）に渡して書き起こす
3. **タイムスタンプ再マッピング** — VAD で圧縮した時間軸を元ファイルのタイムラインに戻す
4. **Markdown 保存** — 同フォルダに `*.transcript.md` として出力する
5. **議事録生成（任意）** — `[minutes].enabled = true` のとき、トランスクリプトを Ollama (`/api/generate`) に投げて議事録 Markdown を生成する。失敗してもトランスクリプト本体は保持され、ゴミ箱送りも実行される（best-effort）

> VAD は常時有効で UI から切り替えは不可。

## 出力フォーマット

```markdown
---
language: ja
model: medium
---

## Transcript

- [00:03.200 - 00:08.000] おはようございます。
- [01:02:03.456 - 01:02:08.000] 1時間を超える場合は HH:MM:SS.mmm 形式
```

タイムスタンプは `MM:SS.mmm`、1時間超は `HH:MM:SS.mmm`。  
同名ファイルが存在する場合は `meeting.transcript.1.md` のように連番が付く。

## 議事録生成（Ollama 連携）

`[minutes].enabled = true`（既定）のとき、トランスクリプト書き出し直後にローカルの Ollama を呼び出して議事録 Markdown を生成する。GUI・CLI どちらの経路でも動く。

### 出力ファイル

- ファイル名: `<音声ファイルの更新日時 YYYY-MM-DD>_<議題>.md`（例: `2026-05-08_予算会議.md`）
- 出力先: トランスクリプトと同じディレクトリ
- 同名ファイルが存在する場合は `2026-05-08_予算会議.1.md` のように連番が付く

フロントマター例:

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

（Ollama が生成した本文）

---
原文書き起こし: [meeting.transcript.md](meeting.transcript.md)
```

### 設定（`config.toml` の `[minutes]` テーブル）

```toml
[minutes]
enabled = true                      # false で機能全体を無効化
ollama_host = "http://localhost:11434"
model = "gemma4"                    # ollama pull で取得したモデル名
prompt_language = "ja"              # "ja" / "en" — 出力の見出し言語
num_ctx = 32768                     # コンテキスト長。8GB Mac → 16384、16GB → 32768、32GB+ → 65536
max_input_chars = 30000             # 送信する書き起こしの最大文字数
request_timeout_seconds = 600.0    # num_ctx >= 32768 なら 600 以上推奨
```

詳細なコメント付き設定例は [`config.toml.example`](config.toml.example) の `[minutes]` セクションを参照。

### 失敗時の挙動

Ollama 未起動・モデル未取得・タイムアウト等で失敗しても、トランスクリプト本体および `trash_source_after_success` による元ファイルのゴミ箱送りには影響しない（best-effort）。
CLI では macOS 通知センターに「議事録生成失敗」が届き、GUI ではログペインに記録される。

## 対応ファイル

| 拡張子 | 備考 |
|--------|------|
| `.wav` | 大文字小文字不問 |
| `.mp3` | 大文字小文字不問 |

## モデル

| モデル | 備考 |
|--------|------|
| `tiny` | 最速・低精度 |
| `base` | |
| `small` | |
| `medium` | デフォルト |
| `large-v3` | 最高精度 |

初回使用時はモデルが自動ダウンロードされる。

## プロジェクト構成

```
mlx-audio-transcriptor/
├── app/
│   ├── main.py                        # GUI エントリーポイント
│   ├── cli.py                         # ヘッドレス CLI（launchd から呼ばれる）
│   ├── config.py                      # TOML 設定ロード（GUI/CLI 共用）
│   ├── ui/
│   │   ├── main_window.py             # メインウィンドウ（プログレスバー・ログペイン）
│   │   └── drop_area.py               # D&D ウィジェット
│   ├── services/
│   │   ├── transcriber.py             # mlx-whisper 呼び出し
│   │   ├── vad.py                     # silero-vad 前処理・タイムスタンプ再マッピング
│   │   ├── segment_merger.py          # 発話区間のマージ
│   │   ├── markdown_writer.py         # Markdown 生成・保存
│   │   ├── file_naming.py             # 連番ファイル名決定
│   │   ├── notifier.py                # macOS 通知センター連携（CLI のみ）
│   │   ├── progress.py                # 25/50/75% マイルストーン通知コールバック
│   │   ├── minutes.py                 # 議事録生成オーケストレーター（best-effort）
│   │   ├── minutes_generator.py       # Ollama HTTP クライアント（urllib.request）
│   │   └── minutes_writer.py          # <日付>_<議題>.md ファイル生成・採番
│   ├── workers/
│   │   └── transcription_worker.py    # バックグラウンド処理（進捗通知）
│   └── models/
│       └── types.py                   # Segment / TranscriptionResult
├── scripts/
│   ├── com.mlx-audio-transcriptor.watcher.plist.template
│   ├── install-watcher.sh             # LaunchAgent 設置
│   └── uninstall-watcher.sh
├── docs/
│   └── mac-watcher-setup.md           # Downloads 監視セットアップ手順
├── config.toml.example
└── tests/
    ├── test_file_naming.py
    ├── test_markdown_writer.py
    ├── test_vad.py
    ├── test_segment_merger.py
    ├── test_config.py
    ├── test_cli_scan.py
    ├── test_notifier.py
    ├── test_progress.py
    ├── test_minutes_generator.py
    ├── test_minutes_orchestrator.py
    └── test_minutes_writer.py
```

## テスト

```bash
pytest
```

## 依存パッケージ

- [PySide6](https://doc.qt.io/qtforpython/) — GUI
- [mlx-whisper](https://github.com/ml-explore/mlx-examples/tree/main/whisper) — 音声文字起こし（Apple Silicon MLX）
- [silero-vad](https://github.com/snakers4/silero-vad) — 無音区間検出（VAD 前処理）
- [soundfile](https://python-soundfile.readthedocs.io/) — 音声ファイル読み込み
- [Send2Trash](https://github.com/arsenetar/send2trash) — 自動監視モードで元ファイルをゴミ箱へ送る

**外部ランタイム（任意）**

- [Ollama](https://ollama.com/) — 議事録生成（Python パッケージ追加なし。stdlib `urllib.request` で疎通するため `requirements.txt` の変更は不要）

## 制限事項

- 話者分離・自動言語判定・動画ファイルは非対応
- キャンセル・一時停止機能なし
- `.app` 化非対応
- 議事録生成は best-effort で、Ollama 未起動・モデル未取得の場合は失敗するがトランスクリプト本体には影響しない
- `[minutes].model` は事前に `ollama pull <model>` で取得しておく必要がある（既定 `gemma4`）
