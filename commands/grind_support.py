import discord
from discord.ext import commands

from database import db
from functions import (
    ensure_ping_tables,
    GRIND_SUPPORT_SERVER_IDS,
    is_suspended,
    set_poketwo_send_permission,
)

class GrindSupport(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        await ensure_ping_tables(db)

    @commands.command(name="gs")
    @commands.guild_only()
    async def grind_support(self, ctx, setting: str = None):
        if ctx.guild.id not in GRIND_SUPPORT_SERVER_IDS:
            return await ctx.send(
                "❌ This command is only available in grind-support servers."
            )
        if setting is not None and is_suspended(ctx.author):
            return await ctx.send(
                "❌ You are suspended from using Mimi and cannot change grind support."
            )
        if setting is None or setting.casefold() not in {"true", "false"}:
            current = await db.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM server_grind_support
                    WHERE serverid = $1 AND userid = $2
                )
                """,
                ctx.guild.id,
                ctx.author.id,
            )
            status = "true" if current else "false"
            return await ctx.send(
                f"Grind support is currently **{status}**. "
                "Use `m!gs true` or `m!gs false`."
            )

        enabled = setting.casefold() == "true"
        if enabled:
            await db.execute(
                """
                INSERT INTO server_grind_support (serverid, userid)
                VALUES ($1, $2)
                ON CONFLICT (serverid, userid) DO NOTHING
                """,
                ctx.guild.id,
                ctx.author.id,
            )
        else:
            await db.execute(
                """
                DELETE FROM server_grind_support
                WHERE serverid = $1 AND userid = $2
                """,
                ctx.guild.id,
                ctx.author.id,
            )
        await ctx.send(
            f"✅ Grind support set to **{str(enabled).lower()}**."
        )

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
