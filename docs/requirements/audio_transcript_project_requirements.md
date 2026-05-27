# 音声文字起こしGUIプロジェクト 初版実装仕様書

## 1. 概要

macOS Apple Silicon 上で動作する、音声文字起こし用のローカルGUIアプリを Python で実装する。  
ユーザーが GUI ウィンドウへ `wav` / `mp3` ファイルをドラッグ&ドロップすると、`mlx-whisper` により文字起こしを行い、入力ファイルと同じディレクトリへ Markdown ファイルを保存する。

---

## 2. 対象環境

### 必須環境
- macOS
- Apple Silicon
- Python 3.11 以上を推奨
- 仮想環境利用前提

### 想定実行形態
- `python -m app.main` などで起動
- 配布や `.app` 化は対象外

---

## 3. 使用技術

### GUI
- `PySide6`

### 音声文字起こし
- `mlx-whisper`

### 補助
- 標準ライブラリ中心
  - `pathlib`
  - `datetime`
  - `traceback`
  - `threading`
  - `queue`
  - `dataclasses`
  - `typing`

### 非採用
- DB
- 非同期フレームワーク
- 外部ジョブキュー
- 自動更新機構
- Finder連携
- ffmpeg前提の動画処理

---

## 4. 対応ファイル

### 受け付ける拡張子
- `.wav`
- `.mp3`

### 判定仕様
- 拡張子ベースで判定
- 大文字小文字は区別しない
  - `.WAV`, `.Mp3` も許可

### 拒否仕様
- 上記以外は受け付けない
- ディレクトリは受け付けない
- 存在しないパスはエラー

---

## 5. GUI仕様

### 5.1 メインウィンドウ
単一ウィンドウ構成。

#### 表示要素
1. タイトル
   - 例: `Audio Transcript Tool`

2. ドロップ領域
   - 中央の大きめ領域
   - 文言例:
     - `ここに wav / mp3 ファイルをドラッグ&ドロップ`
     - `複数ファイル対応`

3. 設定エリア
   - 言語選択
   - モデル選択

4. ステータス表示
   - 現在状態を1行で表示
   - 例:
     - `待機中`
     - `3件中 1件目を処理中`
     - `完了`
     - `エラーあり`

5. ログ表示
   - 複数行テキスト
   - 読み取り専用
   - スクロール可能

6. 任意の補助ボタン
   - `ログをクリア`
   - あってよい

### 5.2 言語選択
#### UI部品
- `QComboBox`

#### 選択肢
- `Japanese (ja)` ← デフォルト
- `English (en)`

#### 挙動
- 選択内容を transcription 実行時に `mlx-whisper` へ渡す
- 自動判定は行わない

### 5.3 モデル選択
#### UI部品
- `QComboBox`

#### 初期候補
- `tiny`
- `base`
- `small`
- `medium` ← デフォルト
- `large-v3`

#### 挙動
- 選択されたモデル名をそのまま利用
- 実行時に未取得モデルであれば、`mlx-whisper` 側の挙動に従う
- モデル取得失敗時はログへエラー表示

### 5.4 ドラッグ&ドロップ
#### 受け入れ仕様
- ファイルURLのみ受け付ける
- 1件以上可
- 受理時にフィルタする

#### フィルタ仕様
- 対象拡張子のみ抽出
- 非対応ファイルは無視しつつログに出す
- 有効ファイルが 1 件もなければ処理開始しない

#### 重複投入
- 同一パスが複数回含まれていたら、ドロップ単位では重複除去する

---

## 6. 処理フロー

### 6.1 全体フロー
1. ユーザーがファイルをドロップ
2. ファイル一覧を検証
3. 有効な音声ファイル一覧を確定
4. UI設定を取得
   - 言語
   - モデル
5. バックグラウンド処理開始
6. 各ファイルを順次文字起こし
7. Markdownを書き出し
8. ログ更新
9. 全件終了後にステータス更新

### 6.2 並列実行方針
- **逐次処理**
- 同時に複数ファイルは処理しない

#### 理由
- GPU/メモリ消費を抑えやすい
- UI/ログの整合性が単純
- Apple Silicon ローカル用途として十分

### 6.3 UIスレッド分離
#### 方針
- 文字起こし処理はメインスレッドで行わない
- `QThread` または `threading.Thread` を用いて別スレッドで実行

#### 推奨
- `QThread` ベース  
理由:
- PySide6 の signal/slot で UI 更新が安全
- GUIアプリとの相性がよい

#### UI更新
- ワーカースレッドから signal を発行
- メインスレッドで以下を行う
  - ステータス更新
  - ログ追記
  - 完了通知

---

## 7. 文字起こし処理仕様

### 7.1 呼び出し単位
1ファイルごとに以下を実施。

#### 入力
- ファイルパス
- モデル名
- 言語コード

#### 出力
- transcription 結果
- セグメント一覧
- エラー時は例外情報

### 7.2 `mlx-whisper` 利用方針
#### 必須要件
- `mlx-whisper` を直接使用
- CLI 呼び出しではなく Python から利用する設計を優先

#### 期待する取得情報
- 全文テキスト
- セグメント単位の開始時刻
- セグメント単位の終了時刻
- セグメント本文

#### 実装方針
`mlx_whisper.transcribe(...)` 相当のAPI利用を想定。  
戻り値から `segments` を取り出して Markdown 化する。

#### API差異吸収
ライブラリ差分で戻り値形式が変わる可能性があるため、変換層を1か所に集約する。

例:
- `services/transcriber.py`
- `normalize_segments(result)` のような関数で吸収

---

## 8. Markdown出力仕様

### 8.1 保存先
- 入力ファイルと同じディレクトリ

### 8.2 ファイル名
入力:
- `meeting.wav`

出力:
- `meeting.transcript.md`

### 8.3 連番仕様
既存ファイルがある場合、以下の規則で回避する。

#### ルール
- 1件目: `meeting.transcript.md`
- 既存あり: `meeting.transcript.1.md`
- さらに存在: `meeting.transcript.2.md`

#### 採番方式
- 最小の未使用番号を採用

### 8.4 Markdown構造
形式は **YAMLフロントマター + 本文**。

#### 最小例
```md
---
language: ja
model: medium
---

## Transcript

- [00:00.000 - 00:03.200] おはようございます。
- [00:03.200 - 00:08.000] それでは会議を始めます。
```

### 8.5 YAML項目
「メタデータは増やさない」という要件に合わせて、最小限にする。

#### 必須項目
- `language`
- `model`

#### 非採用
- source path
- generated_at
- 処理時間
- セグメント数
- 元ファイル名

### 8.6 セグメント書式
各セグメントは1行1項目。

#### 書式
```text
- [開始 - 終了] 本文
```

#### 時刻形式
- `MM:SS.mmm`
- 1時間超え時は `HH:MM:SS.mmm`

例:
- `00:12.345`
- `01:02:03.456`

#### テキスト整形
- 前後空白を除去
- 改行はスペースに変換
- 空文字セグメントは出力しない

---

## 9. ログ仕様

### 9.1 ログ出力先
- GUI内ログペインのみ

### 9.2 出力例
```text
[INFO] 2 files dropped
[INFO] Start: /Users/me/audio/a.wav
[INFO] Saved: /Users/me/audio/a.transcript.md
[WARN] Unsupported file skipped: /Users/me/audio/b.txt
[ERROR] Failed: /Users/me/audio/c.mp3
```

### 9.3 ログレベル
- `INFO`
- `WARN`
- `ERROR`

---

## 10. エラー処理仕様

### 10.1 ファイル単位エラー
1ファイル失敗しても、残りは継続処理する。

### 10.2 想定エラー
- 非対応拡張子
- ファイル不存在
- 読み込み失敗
- `mlx-whisper` 実行失敗
- モデル取得失敗
- 出力書き込み失敗

### 10.3 ログ方針
- ユーザー向けには簡潔なエラーメッセージ
- 開発時向けには詳細例外も出せるようにしてよい
- 初版ではログに traceback を全文出さず、要約だけでもよい

---

## 11. 状態管理仕様

### アプリ状態
- `IDLE`
- `PROCESSING`
- `DONE`
- `ERROR_PARTIAL`

### 状態遷移
- 起動時 → `IDLE`
- ドロップ受理 → `PROCESSING`
- 全件成功 → `DONE`
- 一部失敗あり → `ERROR_PARTIAL`
- 次回ドロップ時に再び `PROCESSING`

### 制約
処理中に新しいドロップを受けた場合の仕様。

#### 推奨
- **処理中ドロップは拒否**
- ログへ
  - `現在処理中のため新しいドロップは無視しました`
と出す

初版はこれが安全。

---

## 12. ディレクトリ構成案

```text
project/
├─ README.md
├─ pyproject.toml
├─ requirements.txt
├─ app/
│  ├─ __init__.py
│  ├─ main.py
│  ├─ ui/
│  │  ├─ __init__.py
│  │  ├─ main_window.py
│  │  └─ drop_area.py
│  ├─ services/
│  │  ├─ __init__.py
│  │  ├─ transcriber.py
│  │  ├─ markdown_writer.py
│  │  └─ file_naming.py
│  ├─ workers/
│  │  ├─ __init__.py
│  │  └─ transcription_worker.py
│  └─ models/
│     ├─ __init__.py
│     └─ types.py
└─ tests/
   ├─ test_file_naming.py
   └─ test_markdown_writer.py
```

---

## 13. モジュール責務

### `main.py`
- アプリ起動
- `QApplication` 構築
- メインウィンドウ起動

### `ui/main_window.py`
- メイン画面
- 設定UI
- ログUI
- ワーカー起動制御
- signal受信

### `ui/drop_area.py`
- ドラッグ&ドロップ専用Widget
- 受理パス抽出
- メインウィンドウへ通知

### `services/transcriber.py`
- `mlx-whisper` 呼び出し
- 結果正規化
- セグメント整形

### `services/markdown_writer.py`
- YAML生成
- 本文生成
- `.md` ファイル保存

### `services/file_naming.py`
- 連番付き未使用ファイル名の決定

### `workers/transcription_worker.py`
- バックグラウンド逐次処理
- 進捗signal発行

### `models/types.py`
- `Segment`
- `TranscriptionResult`
などの dataclass

---

## 14. データモデル案

```python
from dataclasses import dataclass
from pathlib import Path

@dataclass
class Segment:
    start_sec: float
    end_sec: float
    text: str

@dataclass
class TranscriptionResult:
    source_path: Path
    language: str
    model: str
    segments: list[Segment]
```

---

## 15. 設定値仕様

初版では設定ファイルは持たない。

### 毎回GUIから選択
- 言語
- モデル

### デフォルト
- language = `ja`
- model = `medium`

将来必要なら `QSettings` で最後の選択値を保存可能だが、初版では不要。

---

## 16. テスト方針

少なくともロジック部は分離してテスト可能にする。

### 単体テスト対象
- 出力ファイル名採番
- タイムスタンプ整形
- Markdown生成
- セグメント整形

### GUIテスト
- 初版は手動確認中心で可

---

## 17. 非機能要件

### 応答性
- 処理中もGUIが固まらないこと

### 保守性
- UI層と文字起こし処理層を分離

### 可読性
- 型ヒント使用
- dataclass使用
- 例外処理を局所化

---

## 18. 初版でやらないこと

- 話者分離
- 自動言語判定
- 動画ファイル対応
- ノイズ除去
- VAD詳細設定
- キャンセルボタン
- 一時停止/再開
- Finder連携
- `.app` 化
- 設定永続化
- 履歴管理

---

## 19. 実装時の細部推奨

### 文字コード
- Markdown は `UTF-8`

### 改行
- `\n` に統一

### 時刻丸め
- ミリ秒3桁

### セグメント順
- `start_sec` 昇順

### 空結果時
- YAMLは出力
- 本文は以下のみでもよい

```md
## Transcript
```

---

## 20. 実装着手用の最終仕様要約

- PySide6 単一ウィンドウGUI
- `wav` / `mp3` をドラッグ&ドロップ
- 複数ファイル同時投入可、逐次処理
- 言語選択は `ja/en`、デフォルト `ja`
- モデル選択あり、デフォルト `medium`
- `mlx-whisper` で文字起こし
- 出力は同フォルダに `*.transcript.md`
- 同名衝突時は連番
- Markdown は YAMLフロントマター + タイムスタンプ付きセグメント
- ログはGUI表示のみ
- 処理はバックグラウンドスレッド
- エラー時も他ファイル処理継続
