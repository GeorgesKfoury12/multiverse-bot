import os

import discord
from discord.ext import commands
from dotenv import load_dotenv


class MultiverseBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self) -> None:
        await self.tree.sync()

    async def on_ready(self) -> None:
        print(f"Logged in as {self.user} (id: {self.user.id})")


bot = MultiverseBot()


@bot.tree.command(description="Check that the bot is alive")
async def ping(interaction: discord.Interaction) -> None:
    latency_ms = round(bot.latency * 1000)
    await interaction.response.send_message(f"Pong! ({latency_ms}ms)")


def run() -> None:
    load_dotenv()
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise SystemExit(
            "DISCORD_TOKEN is not set. Copy .env.example to .env and add your bot token."
        )
    bot.run(token)
