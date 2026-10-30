import json

from discord.ext import commands

from database import db
from functions import (
    SUSPENDED_USERS_FILE,
    has_allowed_support_role,
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
    async def suspend(self, ctx: commands.Context, user_id: str = None):
        if not has_allowed_support_role(ctx.author):
            await ctx.send(
                "❌ You need administrator permissions or an allowed reserves role to use this command."
            )
            return
        if user_id is None or not user_id.isdigit() or int(user_id) <= 0:
            await ctx.send("❌ Use: `m!suspend <userid>`.")
            return
        user_id = str(int(user_id))

        suspended_users = load_suspended_users()
        if user_id in suspended_users:
            await ctx.send(f"ℹ️ {user_id} is already suspended from Mimi.")
            return

        suspended_users.add(user_id)
        with open(SUSPENDED_USERS_FILE, "w", encoding="utf-8") as file:
            json.dump(sorted(suspended_users), file, indent=2)

        await wipe_user_data(db, int(user_id))
        await refresh_collection_cache(db)
        await refresh_reserve_cache(db)
        await refresh_hunt_cache(db, [guild.id for guild in self.bot.guilds])
        await refresh_tp_qp_cache(db)
        await ctx.send(f"✅ {user_id} has been suspended from Mimi successfully.")

    @commands.command(name="unsuspend")
    @commands.guild_only()
    async def unsuspend(self, ctx: commands.Context, user_id: str = None):
        if not has_allowed_support_role(ctx.author):
            await ctx.send(
                "❌ You need administrator permissions or an allowed reserves role to use this command."
            )
            return
        if user_id is None or not user_id.isdigit() or int(user_id) <= 0:
            await ctx.send("❌ Use: `m!unsuspend <userid>`.")
            return
        user_id = str(int(user_id))

        suspended_users = load_suspended_users()
        if user_id not in suspended_users:
            await ctx.send(f"ℹ️ {user_id} is not suspended from Mimi.")
            return

        suspended_users.remove(user_id)
        with open(SUSPENDED_USERS_FILE, "w", encoding="utf-8") as file:
            json.dump(sorted(suspended_users), file, indent=2)
        await ctx.send(f"✅ {user_id} has been unsuspended from Mimi successfully.")


async def setup(bot):
    await bot.add_cog(SuspendCommands(bot))
