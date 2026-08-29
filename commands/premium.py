import discord
from discord.ext import commands
import json

from functions import invalidate_perk_cache


AUTHORIZED_IDS = {602572754721701900, 657694864179331075, 760720549092917248}


class Premium(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        with open("perks.json", "r") as f:
            self.perks = json.load(f)

    @commands.command(name="support")
    async def support_perk(self, ctx: commands.Context, arg1: str = None, arg2: str = None):
        """Grants a server a specific perk level or resets it."""
        if ctx.author.id not in AUTHORIZED_IDS:
            await ctx.send("❌ You do not have permission to use this command.")
            return

        if not arg1:
            await ctx.send("❌ Usage: `m!support <serverid> <perk>` or `m!support reset/clear <serverid>`")
            return

        if arg1.lower() in ["reset", "clear"] and arg2 is not None:
            try:
                server_id = int(arg2)
            except ValueError:
                await ctx.send("❌ Invalid server ID.")
                return
            try:
                await self.bot.db.execute("DELETE FROM server_perks WHERE serverid = $1", server_id)
                invalidate_perk_cache(server_id)
                await ctx.send(f"✅ Successfully reset perk for server `{server_id}`.")
            except Exception as e:
                await ctx.send(f"⚠️ Failed to reset perk: {e}")
            return
        
        try:
            server_id = int(arg1)
        except ValueError:
            await ctx.send("❌ Invalid server ID. Usage: `m!support <serverid> <perk>` or `m!support reset/clear <serverid>`.")
            return
            
        perk_name = arg2
        if not perk_name:
            await ctx.send("❌ Please provide a perk name.")
            return

        if perk_name.lower() in ["none", "reset", "clear"]:
            try:
                await self.bot.db.execute("DELETE FROM server_perks WHERE serverid = $1", server_id)
                invalidate_perk_cache(server_id)
                await ctx.send(f"✅ Successfully reset perk for server `{server_id}`.")
            except Exception as e:
                await ctx.send(f"⚠️ Failed to reset perk: {e}")
            return

        # Case-insensitive match for perk name
        matched_perk = None
        for key in self.perks.keys():
            if key.lower() == perk_name.lower():
                matched_perk = key
                break

        if not matched_perk:
            await ctx.send(f"❌ Invalid perk name. Available perks: {', '.join(self.perks.keys())}")
            return

        try:
            # Upsert the server perk
            await self.bot.db.execute("""
                INSERT INTO server_perks (serverid, perk_level)
                VALUES ($1, $2)
                ON CONFLICT (serverid)
                DO UPDATE SET perk_level = EXCLUDED.perk_level
            """, server_id, matched_perk)
            invalidate_perk_cache(server_id)
            await ctx.send(f"✅ Successfully set perk **{matched_perk}** for server `{server_id}`.")
        except Exception as e:
            await ctx.send(f"⚠️ Failed to update perk: {e}")

    @commands.command(name="premium", aliases=["perk"])
    async def show_premium(self, ctx: commands.Context, server_id: int = None):
        """Shows the premium perk status of a server."""
        if server_id is not None and ctx.author.id not in AUTHORIZED_IDS:
            await ctx.send("❌ You do not have permission to view perks for other servers.")
            return

        target_id = server_id or (ctx.guild.id if ctx.guild else None)
        if not target_id:
            await ctx.send("❌ Please run this command in a server or provide a server ID.")
            return

        try:
            row = await self.bot.db.fetchrow("""
                SELECT perk_level FROM server_perks WHERE serverid = $1
            """, target_id)
            
            if row:
                perk_name = row["perk_level"]
                incense_limit = self.perks.get(perk_name, 0)
            else:
                perk_name = "None"
                incense_limit = 0

            guild = self.bot.get_guild(target_id)
            server_name = guild.name if guild else str(target_id)

            embed = discord.Embed(title="Mimi Premium", color=discord.Color.purple())
            embed.description = f"**Perk:** `{perk_name}`\n**Server:** {server_name}\n**Support:** **{incense_limit}** active incense"
            
            await ctx.send(embed=embed)
        except Exception as e:
            await ctx.send(f"⚠️ Failed to fetch premium status: {e}")


async def setup(bot: commands.Bot):
    await bot.add_cog(Premium(bot))
