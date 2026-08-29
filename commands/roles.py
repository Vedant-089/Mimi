import discord
from discord.ext import commands
import re

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

    @commands.command(name="event")
    @commands.guild_only()
    async def event(self, ctx: commands.Context, *, role_names: str = ""):
        """Replace the invoker's current type and region roles with the selected ones."""
        requested_names = [
            name.strip().lower()
            for name in re.split(r"[,\s]+", role_names)
            if name.strip()
        ]

        if not requested_names:
            await ctx.send("❌ Use: m!event water, fire")
            return

        invalid_names = [name for name in requested_names if name not in ROLE_NAMES]
        if invalid_names:
            await ctx.send(f"❌ Unknown event role(s): {', '.join(invalid_names)}")
            return

        member = ctx.author
        current_roles = [role for role in member.roles if role.name.lower() in ROLE_NAMES]

        roles_to_add  = []
        missing_roles = []
        for name in requested_names:
            role = discord.utils.get(ctx.guild.roles, name=name)
            if role is None:
                missing_roles.append(name)
                continue
            if role not in roles_to_add:
                roles_to_add.append(role)

        if missing_roles:
            await ctx.send(f"❌ These roles do not exist in the server: {', '.join(missing_roles)}")
            return

        try:
            if current_roles:
                await member.remove_roles(*current_roles, reason="Event role update")
            if roles_to_add:
                await member.add_roles(*roles_to_add, reason="Event role update")
        except discord.Forbidden:
            await ctx.send("❌ I don't have permission to manage roles.")
            return
        except Exception as e:
            await ctx.send(f"⚠️ Error updating roles: {e}")
            return

        removed_text = ", ".join(role.name for role in current_roles) if current_roles else "none"
        added_text   = ", ".join(role.name for role in roles_to_add)
        await ctx.send(f"✅ Updated {member.mention}: removed {removed_text}; added {added_text}")


async def setup(bot: commands.Bot):
    await bot.add_cog(RoleCreator(bot))
