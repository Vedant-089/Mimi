import re

import discord
from discord.ext import commands

from database import db
from functions import advance_grind_support, ensure_ping_tables, is_suspended


POKETWO_BOT_ID = 716390085896962058
CATCH_COMMAND_RE = re.compile(
    rf"^<@!?{POKETWO_BOT_ID}>\s+c\s+(.+?)\s*$",
    re.IGNORECASE,
)


class GrindSupport(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        await ensure_ping_tables(db)

    @commands.command(name="gs")
    async def grind_support(self, ctx, setting: str = None):
        if setting is not None and is_suspended(ctx.author):
            return await ctx.send("❌ You are suspended from using Mimi and cannot add pings.")
        if setting is None or setting.lower() not in {"true", "false"}:
            current = await db.fetchval(
                """
                SELECT COALESCE(grind_support, false)
                FROM shiny_hunt
                WHERE userid = $1
                """,
                ctx.author.id,
            )
            status = "enabled" if current else "disabled"
            return await ctx.send(
                f"Grind support is currently **{status}**. "
                "Use `m!gs true` or `m!gs false`."
            )

        enabled = setting.lower() == "true"
        await db.execute(
            """
            INSERT INTO shiny_hunt (userid, grind_support)
            VALUES ($1, $2)
            ON CONFLICT (userid) DO UPDATE
            SET grind_support = EXCLUDED.grind_support
            """,
            ctx.author.id,
            enabled,
        )
        await ctx.send(
            f"✅ Grind support **{'enabled' if enabled else 'disabled'}**."
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        match = CATCH_COMMAND_RE.match(message.content or "")
        if not match:
            return

        try:
            next_spawn = await advance_grind_support(
                db,
                message.channel.id,
                message.author.id,
                match.group(1),
            )
        except Exception as error:
            print(f"⚠️ Failed to advance grind support: {error}")
            return

        if next_spawn:
            await message.channel.send(
                f"<#{next_spawn['channelid']}> - {next_spawn['pokemon_name']}"
            )


async def setup(bot):
    await bot.add_cog(GrindSupport(bot))
