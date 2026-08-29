import discord
from discord.ext import commands
from database import db
from functions import ensure_ping_tables, invalidate_collection_cache, invalidate_hunt_cache

class AFKButtonView(discord.ui.View):
    def __init__(self, user_id, shinyafk: bool, colafk: bool):
        super().__init__(timeout=None)
        self.user_id = user_id

        # Invert the logic here — red = AFK = True
        self.hunt_button = discord.ui.Button(
            label="Hunt Ping",
            style=discord.ButtonStyle.danger if shinyafk else discord.ButtonStyle.success,
            custom_id="afk_hunt"
        )
        self.collection_button = discord.ui.Button(
            label="Collection Ping",
            style=discord.ButtonStyle.danger if colafk else discord.ButtonStyle.success,
            custom_id="afk_collection"
        )

        self.add_item(self.hunt_button)
        self.add_item(self.collection_button)

        self.hunt_button.callback = self.toggle_hunt
        self.collection_button.callback = self.toggle_collection

    async def toggle_hunt(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("Only you can use these buttons.", ephemeral=True)

        current = await db.fetchval("SELECT shinyafk FROM shiny_hunt WHERE userid = $1", self.user_id)
        new_val = not bool(current)
        await db.execute(
            """
            INSERT INTO shiny_hunt (userid, shinyafk)
            VALUES ($1, $2)
            ON CONFLICT (userid) DO UPDATE SET shinyafk = EXCLUDED.shinyafk
            """,
            self.user_id, new_val
        )
        invalidate_hunt_cache()

        self.hunt_button.style = discord.ButtonStyle.danger if new_val else discord.ButtonStyle.success
        await interaction.response.edit_message(view=self)

    async def toggle_collection(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("Only you can use these buttons.", ephemeral=True)

        current = await db.fetchval("SELECT colafk FROM shiny_hunt WHERE userid = $1", self.user_id)
        new_val = not bool(current)
        await db.execute(
            """
            INSERT INTO shiny_hunt (userid, colafk)
            VALUES ($1, $2)
            ON CONFLICT (userid) DO UPDATE SET colafk = EXCLUDED.colafk
            """,
            self.user_id, new_val
        )
        invalidate_collection_cache()

        self.collection_button.style = discord.ButtonStyle.danger if new_val else discord.ButtonStyle.success
        await interaction.response.edit_message(view=self)

class AFK(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        await ensure_ping_tables(db)

    @commands.command(name="afk")
    async def afk(self, ctx):
        user_id = ctx.author.id

        await db.execute("""
            INSERT INTO shiny_hunt (userid, shinyafk, colafk)
            VALUES ($1, false, false)
            ON CONFLICT (userid) DO NOTHING
        """, user_id)

        shinyafk = await db.fetchval("SELECT shinyafk FROM shiny_hunt WHERE userid = $1", user_id)
        colafk = await db.fetchval("SELECT colafk FROM shiny_hunt WHERE userid = $1", user_id)

        embed = discord.Embed(
            title="AFK Status",
            description="Hunt AFK and Collection AFK are both global.\nCollection lists stay per server.\n\nRed = AFK ON\nGreen = AFK OFF",
            color=discord.Color.purple()
        )

        view = AFKButtonView(user_id, shinyafk, colafk)
        await ctx.send(embed=embed, view=view)

async def setup(bot):
    await bot.add_cog(AFK(bot))
