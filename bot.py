from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

from config import load_settings


class SquadlockBot(commands.Bot):
    def __init__(self, *, settings):
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)
        self.settings = settings

    async def setup_hook(self):
        await self.load_extension("Cogs.squadlock_cog")
        if self.settings.sync_commands:
            if self.settings.guild_id:
                guild = discord.Object(id=self.settings.guild_id)
                await self.tree.sync(guild=guild)
            else:
                await self.tree.sync()

    async def on_ready(self):
        logging.getLogger(__name__).info("Logged in as %s (%s)", self.user, self.user.id)


async def main():
    settings = load_settings()
    bot = SquadlockBot(settings=settings)
    async with bot:
        await bot.start(settings.token)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    asyncio.run(main())
