import discord
from discord.ext import commands
from database import db
from functions import ensure_ping_tables, invalidate_collection_cache, invalidate_hunt_cache

class AFKButtonView(discord.ui.LayoutView):
    def __init__(self, user_id, shinyafk: bool, colafk: bool):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.shinyafk = bool(shinyafk)
        self.colafk = bool(colafk)
        self._build_layout()

    def _build_layout(self):
        self.clear_items()

        hunt_button = discord.ui.Button(
            label="Toggle",
            style=discord.ButtonStyle.primary,
            custom_id=f"afk:{self.user_id}:hunt",
        )
        hunt_button.callback = self.toggle_hunt

        collection_button = discord.ui.Button(
            label="Toggle",
            style=discord.ButtonStyle.primary,
            custom_id=f"afk:{self.user_id}:collection",
        )
        collection_button.callback = self.toggle_collection

        hunt_status = "✅ **Enabled**" if self.shinyafk else "❌ **Disabled**"
        collection_status = "✅ **Enabled**" if self.colafk else "❌ **Disabled**"

        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay("# AFK Status"),
                discord.ui.Separator(),
                discord.ui.Section(
                    f"**Shiny Hunt Pings**\n{hunt_status}",
                    accessory=hunt_button,
                ),
                discord.ui.Separator(),
                discord.ui.Section(
                    f"**Collection Pings**\n{collection_status}",
                    accessory=collection_button,
                ),
                accent_color=discord.Color.from_rgb(47, 49, 54),
            )
        )

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

        self.shinyafk = new_val
        self._build_layout()
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

        self.colafk = new_val
        self._build_layout()
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

        view = AFKButtonView(user_id, shinyafk, colafk)
        await ctx.send(view=view)

async def setup(bot):
    await bot.add_cog(AFK(bot))
