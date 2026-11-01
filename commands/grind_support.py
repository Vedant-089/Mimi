import discord
from discord.ext import commands

from database import db
from functions import (
    ensure_ping_tables,
    GRIND_SUPPORT_SERVER_IDS,
    set_poketwo_send_permission,
)

class GrindSupport(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        await ensure_ping_tables(db)

    @commands.command(name="pause")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def pause_poketwo(self, ctx, scope: str = ""):
        if ctx.guild.id not in GRIND_SUPPORT_SERVER_IDS:
            return await ctx.send(
                "❌ This command is only available in grind-support servers."
            )
        if scope.casefold() not in {"", "all"}:
            return await ctx.send("❌ Usage: `m!pause` or `m!pause all`.")

        channels = (
            ctx.guild.text_channels
            if scope.casefold() == "all"
            else [ctx.channel]
        )
        changed = 0
        for channel in channels:
            try:
                if await set_poketwo_send_permission(ctx.guild, channel, False):
                    changed += 1
            except Exception as error:
                print(
                    f"⚠️ Failed to pause Pokétwo in "
                    f"{ctx.guild.id}/{channel.id}: {error}"
                )
        await ctx.send(f"✅ Pokétwo sending paused in {changed} channel(s).")

    @commands.command(name="resume")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def resume_poketwo(self, ctx, scope: str = ""):
        if ctx.guild.id not in GRIND_SUPPORT_SERVER_IDS:
            return await ctx.send(
                "❌ This command is only available in grind-support servers."
            )
        if scope.casefold() not in {"", "all"}:
            return await ctx.send("❌ Usage: `m!resume` or `m!resume all`.")

        channels = (
            ctx.guild.text_channels
            if scope.casefold() == "all"
            else [ctx.channel]
        )
        changed = 0
        for channel in channels:
            try:
                if await set_poketwo_send_permission(ctx.guild, channel, None):
                    changed += 1
            except Exception as error:
                print(
                    f"⚠️ Failed to resume Pokétwo in "
                    f"{ctx.guild.id}/{channel.id}: {error}"
                )
        await ctx.send(f"✅ Pokétwo sending resumed in {changed} channel(s).")

async def setup(bot):
    await bot.add_cog(GrindSupport(bot))
