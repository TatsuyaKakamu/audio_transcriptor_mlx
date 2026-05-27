# AGENTS.md

> 実装上の非自明な詳細（スレッド境界・VAD・議事録生成・自動 PR・テスト制約）は **CLAUDE.md** を参照。
> 出力フォーマット・設定・使い方は **README.md** を参照。
> このファイルはエージェントが作業を始めるのに必要な最低限の情報を提供する。

## 実行環境

- macOS Apple Silicon 必須 / Python 3.11+
- 仮想環境: `.venv`（`ffmpeg` が別途必要: `brew install ffmpeg`）

## セットアップ・主要コマンド

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python -m app.main        # GUI 起動
python -m app.cli scan    # CLI / launchd watcher と同一エントリ

pytest                    # テスト全件
pytest tests/test_file_naming.py  # 特定テスト
```

## ディレクトリ構成

```
app/
  cli.py          # CLI エントリポイント
  config.py       # TOML 設定ロード（~/.config/mlx-audio-transcriptor/config.toml）
  main.py         # GUI エントリポイント
  ui/             # PySide6 ウィジェット
  services/       # ロジック層（GUI なしで単体テスト可能）
  workers/        # QThread ワーカー
  models/         # dataclass（Segment, TranscriptionResult）
tests/            # pytest（services/ 層のみ対象、mlx-whisper/silero_vad は対象外）
scripts/          # launchd watcher のインストール/アンインストール
docs/             # ドキュメント・要件書
```

## 重要な設計ルール

- `services/` 層は GUI なしで単体テスト可能にする
- UI スレッドからワーカーへの直接呼び出し禁止（Signal 経由のみ）
- `minutes.run_for()` と `auto_pr.publish_pair()` は例外を握り潰して best-effort 動作
- 議事録ファイル名は **必ず英語**（`sanitize_slug` で強制）、本文は日本語
- `config.toml.example` にはプレースホルダのみ記載（個人パス・GitHub ID は書かない）
