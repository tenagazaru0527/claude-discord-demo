# Claude Code × Discord 講習用デモ

Discordの`/claude`からローカルのClaude Codeへ質問し、固定したプロジェクトを読み取り専用で調査して回答を返すデモです。別の`approval.py`は、DiscordにHuman Approvalの承認・拒否ボタンを出し、結果をCLIの終了コードで返します。

```text
Discord /claude
  ↓
Python Bot (discord_bridge.py)
  ↓
ローカルの Claude Code CLI (`claude -p`)
  ↓
CLAUDE_PROJECT_DIR で指定したプロジェクト

別経路: 承認が必要なローカル作業 → approval.py → Discordの承認/拒否 → CLIの結果
```

このデモはAnthropic APIをPythonから直接呼び出しません。`discord_bridge.py`はローカルにインストールされ、ログイン済みのClaude Code CLIを`claude -p`で非対話実行します。`approval.py`は独立したCLIです。読み取り専用の`/claude`から`approval.py`や任意のシェルコマンドを実行することはできません。

## 前提条件

- Python、Git
- ローカルにインストールしたClaude Code CLI **v2.1.259以降**と、そのログイン
- Discordアカウント、Discord Bot / Application、Botを招待できるサーバーとチャンネル

このデモのCLI引数では、`--restricted`はv2.1.248以降、`--permission-prompts`はv2.1.259以降が必要です。したがって必要な最低バージョンは**v2.1.259**です（[Claude Code公式CLIリファレンス](https://code.claude.com/docs/en/cli-reference)）。

Botには対象チャンネルの`View Channel`と`Send Messages`を付与してください。`/claude`を使うため、招待時のScopeには`bot`と`applications.commands`が必要です。Message ContentなどのPrivileged Intentsは不要です。

## セットアップ（PowerShell）

```powershell
git clone https://github.com/tenagazaru0527/claude-discord-demo.git
cd claude-discord-demo
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
claude --version
```

`claude --version`でv2.1.259以降と表示されることを確認してください。古い場合は[公式の更新手順](https://code.claude.com/docs/en/cli-reference)に従って更新してください。`CLAUDE_BIN`を設定する場合は、その実行ファイルのバージョンを確認してください。

`.env.example`は必要な変数の一覧です。**両スクリプトは`.env`を自動読み込みしません。** 起動するPowerShellで環境変数を設定してください。Token、ID、ローカルパスは自分の値に置き換え、ファイルやチャットに貼らないでください。

```powershell
$env:DISCORD_BOT_TOKEN="<自分のBot Token>"
$env:DISCORD_CHANNEL_ID="<自分のChannel ID>"
$env:CLAUDE_PROJECT_DIR="C:\path\to\project"
# 任意: 承認ボタンを押せる人を限定する（複数ならカンマ区切り）
$env:DISCORD_APPROVER_IDS="<自分のDiscord User ID>"
```

`CLAUDE_PROJECT_DIR`は既に存在するローカルの調査対象ディレクトリです。Discordから変更することはできません。Claude CodeがPATHから見つからない場合だけ、`CLAUDE_BIN`に実行ファイルのパスを設定してください。Windowsでは安全のためnpmの`claude.cmd`ではなく実体の`claude.exe`を起動します。

## Discord → Claude Code

上記の環境変数を設定したPowerShellで起動します。

```powershell
.\.venv\Scripts\python.exe discord_bridge.py
```

Botが起動したら、指定チャンネルで次を実行します。

```text
/claude prompt:このプロジェクトが何をするものか調べて
```

`discord_bridge.py`はClaude Codeを`CLAUDE_PROJECT_DIR`で起動し、ツールを`Read,Glob,Grep`に限定します。`--permission-prompts none`、`--disallowedTools "mcp__*"`、`--strict-mcp-config`、`--restricted`も指定します。質問文はシェルを通さず、`--`の後に1引数として渡します。同時実行は1件で、120秒を超えると終了します。応答はDiscordの文字数制限に合わせて分割し、メンションを無効にします。

## Claude Code → Discord Human Approval Gate

承認が必要な**別のローカル作業**から`approval.py`を起動します。講習では、まずPowerShellから単独で動作確認できます。同じBot TokenとChannel IDを使い、必要に応じて`DISCORD_APPROVER_IDS`を設定してください。未設定の場合、チャンネルにアクセスできる人なら誰でも承認・拒否できます。

```powershell
.\.venv\Scripts\python.exe approval.py "この処理を実行してよいですか？"
```

Discordに承認・拒否ボタンが表示されます。選択は標準出力の最終行と終了コードで返ります。承認対象の作業を実行するかは、呼び出し元がこの結果を見て決めます。このスクリプト自体は承認対象の処理を実行しません。

| 結果 | 標準出力の最終行 | 終了コード |
|---|---|---:|
| 承認 | `APPROVED` | 0 |
| 拒否 | `REJECTED` | 1 |
| 120秒無操作 | `TIMEOUT` | 2 |
| エラー | 理由を標準エラー出力 | 3 |

## 安全上の注意

- Bot Tokenやその他の秘密情報をGitHubへ登録したり、チャットへ貼ったりしないでください。`.env`と仮想環境はGitの対象外です。
- このDiscord → Claude Code経路は講習用に読み取り専用ツールへ制限しています。`discord_bridge.py`からClaude Codeへ自由なシェル実行権限を与えないでください。
- 公開リポジトリのコードを変更して権限を広げる場合は、内容と影響を理解してから行ってください。
- `approval.py`に渡した依頼文はDiscordへ投稿されます。秘密情報や非公開の内容を入れないでください。

Pythonの外部依存は`requirements.txt`に記載した`discord.py`のみです。
