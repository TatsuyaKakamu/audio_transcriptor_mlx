# Downloads 自動文字起こしのセットアップ（macOS）

iPhone から AirDrop などで `~/Downloads` に入ってきた音声ファイル（`.wav` / `.mp3`）を、GUI を開かずにバックグラウンドで文字起こしし、同フォルダに `*.transcript.md` を残したうえで**元の音源はゴミ箱へ移動**するための設定手順です。

launchd の `WatchPaths` で Downloads の変化を検知し、ヘッドレス CLI (`python -m app.cli scan`) を走らせる仕組みです。

---

## 1. 前提

- macOS（Apple Silicon 必須）
- Python 3.11 以上
- このリポジトリをクローン済み
- 仮想環境と依存パッケージが導入済み

```bash
cd /path/to/mlx-audio-transcriptor
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

`.venv/bin/python` が存在することが install スクリプトの前提です。

---

## 2. 設定ファイルを用意

```bash
mkdir -p ~/.config/mlx-audio-transcriptor
cp config.toml.example ~/.config/mlx-audio-transcriptor/config.toml
```

`~/.config/mlx-audio-transcriptor/config.toml` を好みに応じて編集します。

```toml
language = "ja"
model = "medium"
watch_dir = "~/Downloads"
extensions = [".wav", ".mp3"]
file_stability_seconds = 3.0
trash_source_after_success = true
```

- `language` / `model`: ヘッドレス処理の既定値。GUI もこの値を初期選択に使います
- `watch_dir`: 監視するフォルダ
- `extensions`: 対象拡張子
- `file_stability_seconds`: 書き込み完了判定の秒数（AirDrop 中に処理を始めないためのガード）
- `trash_source_after_success`: `true` なら Markdown 生成成功後に音源をゴミ箱へ移動。Finder の "Put Back" で元に戻せます

設定ファイルが無くてもコード側のデフォルトで動きます（`config.toml.example` と同じ値）。

---

## 3. LaunchAgent をインストール

```bash
./scripts/install-watcher.sh
```

スクリプトが行うこと:

1. `~/Library/Logs/mlx-audio-transcriptor/` を作成
2. `~/.config/mlx-audio-transcriptor/config.toml` が無ければ `config.toml.example` をコピー
3. `scripts/com.mlx-audio-transcriptor.watcher.plist.template` のプレースホルダ（venv Python / プロジェクトルート / 監視先 / ホーム）を置換して `~/Library/LaunchAgents/com.mlx-audio-transcriptor.watcher.plist` に書き出し
4. `launchctl bootout`（既存登録を解除）→ `launchctl bootstrap`（再登録）

登録確認:

```bash
launchctl print gui/$(id -u)/com.mlx-audio-transcriptor.watcher | head
```

---

## 4. 動作確認

1. 短い `.wav`（数秒でOK）を `~/Downloads` にコピー
2. 10〜数十秒待つ（`ThrottleInterval=10` と モデルロード時間のため）
3. `~/Downloads/<name>.transcript.md` が生成されること
4. 元の `<name>.wav` が**ゴミ箱に入っている**こと（Finder → ゴミ箱で確認）
5. Finder のゴミ箱でファイルを右クリックし「戻す」で Downloads に復元できること

手動で CLI を試すこともできます:

```bash
cd /path/to/mlx-audio-transcriptor
.venv/bin/python -m app.cli scan
```

---

## 5. ログ

- 標準出力: `~/Library/Logs/mlx-audio-transcriptor/stdout.log`
- 標準エラー: `~/Library/Logs/mlx-audio-transcriptor/stderr.log`（`logging` モジュールの出力はこちら）

```bash
tail -f ~/Library/Logs/mlx-audio-transcriptor/stderr.log
```

---

## 6. フルディスクアクセス権限について

`~/Downloads` は macOS のプライバシー保護対象です。launchd から起動した子プロセスが「ファイルが見えない」状態になる場合は、システム設定 → プライバシーとセキュリティ → **フルディスクアクセス** に、`install-watcher.sh` で plist に書き込まれた Python バイナリ（通常 `/path/to/mlx-audio-transcriptor/.venv/bin/python3.x`）を追加してください。

追加後は再ログインするか、以下で LaunchAgent を再読込してください:

```bash
launchctl bootout gui/$(id -u)/com.mlx-audio-transcriptor.watcher
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.mlx-audio-transcriptor.watcher.plist
```

---

## 7. 停止・アンインストール

```bash
./scripts/uninstall-watcher.sh
```

これで LaunchAgent が解除され、plist ファイルが削除されます。`~/.config` や ログファイルはそのまま残ります（手で消して構いません）。

---

## 8. トラブルシュート

**Downloads に置いても何も起こらない**

```bash
# 1. 登録できているか
launchctl list | grep mlx-audio-transcriptor

# 2. 手動で CLI を走らせて単体で動くか
cd /path/to/mlx-audio-transcriptor
.venv/bin/python -m app.cli scan

# 3. stderr ログに例外が出ていないか
tail -n 200 ~/Library/Logs/mlx-audio-transcriptor/stderr.log
```

**`watch_dir` を変えたのに反映されない**

plist に `WatchPaths` が焼き付けられるため、`config.toml` を書き換えたあとは再度 `./scripts/install-watcher.sh` を実行してください。

**処理が途中で止まっているように見える**

大きなモデル（`large-v3` など）は初回ロードに時間がかかります。`stderr.log` に進捗が出ていれば正常です。

**ゴミ箱へ送られるのを止めたい**

`config.toml` で `trash_source_after_success = false` にしてください。元ファイルは Downloads に残り、次回スキャン時は `*.transcript.md` の存在で再処理をスキップします。
