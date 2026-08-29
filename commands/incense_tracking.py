from discord.ext import commands, tasks
from database import db
from functions import (
    get_incense_tracking_data,
    get_server_max_incense_limit,
    cleanup_stale_incenses,
)

class IncenseTracking(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.cleanup_task.start()

    def cog_unload(self):
        self.cleanup_task.cancel()

    @tasks.loop(seconds=10)
    async def cleanup_task(self):
        try:
            await cleanup_stale_incenses()
        except Exception as e:
            print("⚠️ Error in incense cleanup task:", e)

    @commands.command(name="incenses", aliases=["incensetracking", "activeincenses"])
    @commands.guild_only()
    async def show_active_incenses(self, ctx: commands.Context):
        """Shows active tracked incenses for the current server."""
        data = get_incense_tracking_data()
        server_key = str(ctx.guild.id)
        server_info = data.get(server_key, {})
        
        max_limit = await get_server_max_incense_limit(ctx.guild.id, db)
        active_channels = server_info.get("active_incense", [])

        embed = discord.Embed(
            title=f"Incense Tracking - {ctx.guild.name}",
            color=discord.Color.purple()
        )
        embed.add_field(
            name="Active Incenses",
            value=f"**{len(active_channels)}** / **{max_limit}**",
            inline=False
        )

        if active_channels:
            mentions = [f"<#{cid}>" for cid in active_channels]
            embed.add_field(
                name="Channels",
                value="\n".join(mentions),
                inline=False
            )
        else:
            embed.add_field(
                name="Channels",
                value="No active incenses currently being tracked.",
                inline=False
            )

        await ctx.send(embed=embed)

async def setup(bot: commands.Bot):
    await bot.add_cog(IncenseTracking(bot))
