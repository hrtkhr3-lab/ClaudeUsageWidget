# Claude 使用状況ウィジェット

Claude のプラン使用率（5時間枠 / 週間）をデスクトップの右下に小さく表示する Windows 用ウィジェットです。

- Claude Code のログイン情報（`~/.claude/.credentials.json`）を使って使用率を 5 分ごとに取得
- 各枠のリセット時刻と残り時間を表示（80% 以上で赤表示）
- 今日の Claude Code の応答数・出力トークン数をローカルのログから集計
- ヘッダーの Clawd が状態に合わせて動きます（作業中 / 使用率が高い / オフライン など）

## 必要なもの

- Windows 10 / 11
- [Claude Code](https://claude.com/claude-code) にログイン済みであること（Pro / Max プラン）

## インストール

### exe 版（おすすめ）

1. [Releases](../../releases/latest) から `ClaudeUsageWidget-vX.Y.Z-win64.zip` をダウンロードして好きな場所に展開
2. `ClaudeUsageWidget.exe` をダブルクリックで起動

Windows 起動時に自動で立ち上げたい場合は、展開したフォルダで次を実行します。

```powershell
powershell -ExecutionPolicy Bypass -File .\install_startup.ps1
```

> 署名していない exe のため、初回起動時に SmartScreen の警告が出ることがあります。「詳細情報」→「実行」で起動できます。

### Python から実行

Python 3.10 以上（tkinter 付き）があれば、追加のパッケージなしで動きます。

```powershell
pythonw claude_usage_widget.pyw
```

スタートアップ登録は exe 版と同じく `install_startup.ps1` で行えます。

## 使い方

- ドラッグで移動（位置は保存されます）
- 右クリックでメニュー：今すぐ更新 / 使用状況ページを開く / 常に最前面に表示 / 位置を右下に戻す / 終了
- 設定は `%APPDATA%\ClaudeUsageWidget\config.json` に保存されます

## アンインストール

```powershell
powershell -ExecutionPolicy Bypass -File .\uninstall_startup.ps1
```

スタートアップ登録を解除し、起動中のウィジェットを終了します。その後フォルダと `%APPDATA%\ClaudeUsageWidget` を削除してください。

## リリース手順（開発者向け）

`v` から始まるタグを push すると、GitHub Actions が exe をビルドして Release を作成します。

```powershell
git tag v1.0.1
git push origin v1.0.1
```

## 注意

- 使用率の取得には Claude Code の非公開 API（`/api/oauth/usage`）を使っているため、仕様変更で動かなくなる可能性があります。
- 認証情報はローカルで読み取るだけで、Anthropic の API 以外には送信しません。
