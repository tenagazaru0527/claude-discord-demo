"""Minimal Human Approval Gate via Discord buttons.

Usage:
    python approval.py "message to approve"

Prints exactly one of APPROVED / REJECTED / TIMEOUT as the last stdout line.
Exit codes: 0 = APPROVED, 1 = REJECTED, 2 = TIMEOUT, 3 = error.
"""

import asyncio
import os
import sys

import discord

TIMEOUT_SECONDS = 120
EXIT_CODES = {"APPROVED": 0, "REJECTED": 1, "TIMEOUT": 2}


def fail(msg: str) -> None:
    # Never include the token in error output
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(3)


def load_config() -> tuple[str, int, str, set[int]]:
    if len(sys.argv) != 2 or not sys.argv[1].strip():
        fail('usage: python approval.py "<message>"')
    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    channel_raw = os.environ.get("DISCORD_CHANNEL_ID", "").strip()
    if not token:
        fail("DISCORD_BOT_TOKEN is not set")
    if not channel_raw.isdigit():
        fail("DISCORD_CHANNEL_ID is not set or not numeric")
    # Optional allowlist of user IDs permitted to press the buttons
    approvers_raw = os.environ.get("DISCORD_APPROVER_IDS", "")
    approvers = {int(x) for x in approvers_raw.replace(" ", "").split(",") if x.isdigit()}
    return token, int(channel_raw), sys.argv[1], approvers


class ApprovalView(discord.ui.View):
    def __init__(self, future: asyncio.Future, approvers: set[int]):
        super().__init__(timeout=None)  # timeout is handled by asyncio.wait_for
        self.future = future
        self.approvers = approvers

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.approvers and interaction.user.id not in self.approvers:
            await interaction.response.send_message("この操作を行う権限がありません。", ephemeral=True)
            return False
        if self.future.done():
            await interaction.response.send_message("この承認依頼は既に終了しています。", ephemeral=True)
            return False
        return True

    async def _decide(self, interaction: discord.Interaction, result: str) -> None:
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content=f"**{result}** by {interaction.user.display_name}", view=self
        )
        self.stop()
        if not self.future.done():
            self.future.set_result(result)

    @discord.ui.button(label="承認", style=discord.ButtonStyle.success)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._decide(interaction, "APPROVED")

    @discord.ui.button(label="拒否", style=discord.ButtonStyle.danger)
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._decide(interaction, "REJECTED")


async def run_gate(token: str, channel_id: int, text: str, approvers: set[int]) -> str:
    # Default intents without message_content: buttons only need interactions
    intents = discord.Intents.none()
    intents.guilds = True
    client = discord.Client(intents=intents)
    outcome: dict[str, object] = {}

    @client.event
    async def on_ready() -> None:
        # on_ready can fire again after a reconnect; post only once
        if outcome.get("started"):
            return
        outcome["started"] = True
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        view = ApprovalView(future, approvers)
        message = None
        try:
            channel = client.get_channel(channel_id) or await client.fetch_channel(channel_id)
            message = await channel.send(
                f"**Human Approval Required**\n\n{text}",
                view=view,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            print("Waiting for approval on Discord...", file=sys.stderr, flush=True)
            outcome["result"] = await asyncio.wait_for(future, timeout=TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            outcome["result"] = "TIMEOUT"
            view.stop()
            for item in view.children:
                item.disabled = True
            if message is not None:
                try:
                    await message.edit(content=f"{message.content}\n\n**TIMEOUT**", view=view)
                except discord.HTTPException:
                    pass
        except Exception as exc:  # noqa: BLE001
            outcome["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            await client.close()

    try:
        await client.start(token)
    except discord.LoginFailure:
        fail("Discord login failed (invalid token)")
    if "error" in outcome:
        fail(str(outcome["error"]))
    if "result" not in outcome:
        fail("connection closed before a result was obtained")
    return str(outcome["result"])


def main() -> None:
    token, channel_id, text, approvers = load_config()
    try:
        result = asyncio.run(run_gate(token, channel_id, text, approvers))
    except KeyboardInterrupt:
        fail("interrupted")
    print(result, flush=True)
    sys.exit(EXIT_CODES[result])


if __name__ == "__main__":
    main()
