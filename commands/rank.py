import discord
from discord.ext import commands


class RankToggle(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="rank")
    @commands.guild_only()
    async def rank(self, ctx: commands.Context, *, rank_name: str = ""):
        """Toggle selected ping roles (Rares/Regionals/Incense)."""
        key = rank_name.strip().lower()
        target_roles = {
            "rare": "Rares",
            "rares": "Rares",
            "Rare": "Rares",
            "Rares": "Rares",
            "regional": "Regionals",
            "regionals": "Regionals",
            "Regional": "Regionals",
            "Regionals": "Regionals",
            "incense": "Incense",
            "Incense": "Incense"
        }

        if key not in target_roles:
            await ctx.send("❌ Use: m!rank rare, m!rank regional, or m!rank incense")
            return

        role_name = target_roles[key]
        role = discord.utils.get(ctx.guild.roles, name=role_name)

        if role is None:
            await ctx.send(f"❌ Role '{role_name}' does not exist. Ask an admin to run m!roles.")
            return

        member = ctx.author

        if role in member.roles:
            await member.remove_roles(role, reason="Self-toggle rank role")
            await ctx.send(f"➖ Removed {role.name} from {member.mention}")
        else:
            await member.add_roles(role, reason="Self-toggle rank role")
            await ctx.send(f"➕ Added {role.name} to {member.mention}")


async def setup(bot: commands.Bot):
    await bot.add_cog(RankToggle(bot))
