import json

import discord
from discord.ext import commands

from database import db
from functions import (
    SUSPENDED_USERS_FILE,
    has_allowed_support_role,
    is_suspended,
    load_suspended_users,
    refresh_collection_cache,
    refresh_hunt_cache,
    refresh_reserve_cache,
    refresh_tp_qp_cache,
    wipe_user_data,
)


class SuspendCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="suspend")
    @commands.guild_only()
    async def suspend(self, ctx: commands.Context, member: discord.Member = None):
        if not has_allowed_support_role(ctx.author):
            await ctx.send(
                "❌ You need administrator permissions or an allowed reserves role to use this command."
            )
            return
        if member is None:
            await ctx.send("❌ Mention the user to suspend: `m!suspend @user`.")
            return
        if member.bot:
            await ctx.send("❌ Bots cannot be suspended.")
            return
        if is_suspended(member):
            await ctx.send(f"ℹ️ {member.mention} is already suspended from Mimi.")
            return

        suspended_users = load_suspended_users()
        suspended_users.add(member.name.casefold())
        with open(SUSPENDED_USERS_FILE, "w", encoding="utf-8") as file:
            json.dump(sorted(suspended_users), file, indent=2)

        await wipe_user_data(db, member.id)
        await refresh_collection_cache(db)
        await refresh_reserve_cache(db)
        await refresh_hunt_cache(db, [guild.id for guild in self.bot.guilds])
        await refresh_tp_qp_cache(db)
        await ctx.send(
            f"✅ {member.mention} has been suspended from Mimi, and all of their Mimi data has been removed."
        )


async def setup(bot):
    await bot.add_cog(SuspendCommands(bot))
