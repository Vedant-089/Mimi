import discord
from discord.ext import commands

ROLE_NAMES = [
    "kanto", "johto", "hoenn", "sinnoh", "unova", "kalos",
    "alola", "galar", "paldea", "normal", "fire", "water", "electric", "grass", "ice",
    "fighting", "poison", "ground", "flying", "psychic", "bug",
    "rock", "ghost", "dragon", "dark", "steel", "fairy"
]


class RoleCreator(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="roles")
    @commands.has_permissions(manage_roles=True)
    async def create_typing_roles(self, ctx: commands.Context):
        """Creates roles for every Pokémon typing if they don't exist."""
        extra_roles = ["Rares", "Regionals", "Eeveelutions & Paradox", "Gigantamax"]
        created = []
        skipped = []

        for t in ROLE_NAMES + extra_roles:
            existing_role = discord.utils.get(ctx.guild.roles, name=t)
            if existing_role:
                skipped.append(t)
                continue
            try:
                await ctx.guild.create_role(name=t)
                created.append(t)
            except discord.Forbidden:
                await ctx.send("❌ I don't have permission to manage roles.")
                return
            except Exception as e:
                await ctx.send(f"⚠️ Error creating {t} role: {e}")
                return

        msg = []
        if created:
            msg.append(f"✅ Created roles: {', '.join(created)}")
        if skipped:
            msg.append(f"⏩ Skipped existing: {', '.join(skipped)}")
        if not msg:
            msg.append("⚪ No roles were created or skipped.")
        await ctx.send("\n".join(msg))

async def setup(bot: commands.Bot):
    await bot.add_cog(RoleCreator(bot))
