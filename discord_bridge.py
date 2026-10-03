"""Discord -> Claude Code (read-only) -> Discord bridge.

Usage:
    python discord_bridge.py

Environment variables:
    DISCORD_BOT_TOKEN   Bot token (required)
    DISCORD_CHANNEL_ID  Only /claude invocations from this channel are handled (required)
    CLAUDE_PROJECT_DIR  Fixed project directory Claude Code investigates (required)
    CLAUDE_BIN          Optional explicit path to the claude executable
"""

import asyncio
import os
import shutil
import sys
from pathlib import Path

import discord
from discord import app_commands

CLAUDE_TIMEOUT_SECONDS = 120
DISCORD_CHUNK_SIZE = 1900  # Discord hard limit is 2000 characters per message

# Read-only tool set; everything else (Bash, Edit, Write, MCP, ...) stays unavailable
CLAUDE_ARGS = [
    "-p",
    "--tools", "Read,Glob,Grep",
    "--permission-prompts", "none",
    "--disallowedTools", "mcp__*",
    "--strict-mcp-config",
    "--restricted",
]

# Environment variables never passed to the Claude Code child process
SECRET_ENV_KEYS = ("DISCORD_BOT_TOKEN",)


class BridgeError(Exception):
    """Error whose message is safe to show on Discord."""


def resolve_claude() -> str:
    override = os.environ.get("CLAUDE_BIN", "").strip()
    path = override or shutil.which("claude")
    if not path or not Path(path).is_file():
        raise BridgeError("claudeコマンドが見つかりません（PATH または CLAUDE_BIN を確認してください）")
    if os.name == "nt" and Path(path).suffix.lower() in (".cmd", ".bat"):
        # .cmd shims are run through cmd.exe, which would re-interpret metacharacters
        # in the prompt. Use the real executable behind the npm shim instead.
        exe = Path(path).parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
        if not exe.is_file():
            raise BridgeError("claudeコマンドが見つかりません（.cmd 経由の起動は安全のため使用しません。CLAUDE_BIN に claude.exe を指定してください）")
        path = str(exe)
    return path


def resolve_project_dir() -> Path:
    raw = os.environ.get("CLAUDE_PROJECT_DIR", "").strip()
    if not raw:
        raise BridgeError("CLAUDE_PROJECT_DIR が設定されていません")
    project = Path(raw)
    if not project.is_dir():
        raise BridgeError("CLAUDE_PROJECT_DIR が存在しません")
    return project.resolve()


def redact(text: str) -> str:
    for key in SECRET_ENV_KEYS:
        value = os.environ.get(key)
        if value:
            text = text.replace(value, "[REDACTED]")
    return text


def split_message(text: str, limit: int = DISCORD_CHUNK_SIZE) -> list[str]:
    chunks = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    if text:
        chunks.append(text)
    return chunks or ["（空の応答）"]


async def run_claude(prompt: str, timeout: float = CLAUDE_TIMEOUT_SECONDS) -> str:
    claude = resolve_claude()
    project = resolve_project_dir()
    env = {k: v for k, v in os.environ.items() if k not in SECRET_ENV_KEYS}
    # "--" ends option parsing so the prompt is always a single positional argument
    argv = [claude, *CLAUDE_ARGS, "--", prompt]
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(project),
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        raise BridgeError("claudeコマンドが見つかりません") from None

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise BridgeError("Claude Codeの処理がタイムアウトしました") from None

    out = stdout.decode("utf-8", errors="replace").strip()
    if proc.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip() or out
        detail = redact(detail)[-500:]
        raise BridgeError(f"Claude Codeが異常終了しました（終了コード {proc.returncode}）\n```\n{detail}\n```")
    return redact(out)


class BridgeClient(discord.Client):
    def __init__(self, channel_id: int):
        # No privileged intents (Message Content is not needed for slash commands)
        super().__init__(intents=discord.Intents(guilds=True))
        self.channel_id = channel_id
        self.tree = app_commands.CommandTree(self)
        self.busy = False
        self.synced = False

    async def on_ready(self) -> None:
        if self.synced:
            return
        self.synced = True
        channel = self.get_channel(self.channel_id) or await self.fetch_channel(self.channel_id)
        # Guild-scoped sync makes the command available immediately
        self.tree.copy_global_to(guild=channel.guild)
        await self.tree.sync(guild=channel.guild)
        print(f"Ready as {self.user}. /claude registered in guild {channel.guild.id}", flush=True)


def build_client(channel_id: int) -> BridgeClient:
    client = BridgeClient(channel_id)
    no_mentions = discord.AllowedMentions.none()

    @client.tree.command(name="claude", description="Claude Code（読み取り専用）にプロジェクトについて質問します")
    @app_commands.describe(prompt="Claude Codeへの質問")
    async def claude_command(interaction: discord.Interaction, prompt: str) -> None:
        if interaction.channel_id != client.channel_id:
            await interaction.response.send_message("このチャンネルでは /claude を利用できません。", ephemeral=True)
            return
        if client.busy:
            await interaction.response.send_message("Claude Codeは現在別の処理を実行中です", ephemeral=True)
            return
        client.busy = True  # set before the first await so concurrent calls see it
        try:
            await interaction.response.defer(thinking=True)
            print(f"/claude from {interaction.user} ({len(prompt)} chars)", flush=True)
            try:
                answer = await run_claude(prompt)
            except BridgeError as exc:
                answer = f"⚠️ {exc}"
            except Exception as exc:  # noqa: BLE001
                answer = f"⚠️ 予期しないエラー: {type(exc).__name__}"
                print(f"unexpected error: {redact(repr(exc))}", file=sys.stderr, flush=True)
            header = f"> {prompt[:300]}\n\n"
            for chunk in split_message(header + answer):
                await interaction.followup.send(chunk, allowed_mentions=no_mentions)
        finally:
            client.busy = False

    return client


def main() -> None:
    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    channel_raw = os.environ.get("DISCORD_CHANNEL_ID", "").strip()
    if not token:
        sys.exit("ERROR: DISCORD_BOT_TOKEN is not set")
    if not channel_raw.isdigit():
        sys.exit("ERROR: DISCORD_CHANNEL_ID is not set or not numeric")
    try:
        project = resolve_project_dir()
        claude = resolve_claude()
    except BridgeError as exc:
        sys.exit(f"ERROR: {exc}")
    print(f"Project dir: {project}\nClaude CLI: {claude}", flush=True)
    build_client(int(channel_raw)).run(token)


if __name__ == "__main__":
    main()
