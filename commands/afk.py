import discord
from discord.ext import commands
from database import db
from functions import ensure_ping_tables, is_suspended, refresh_collection_cache, refresh_hunt_cache
from commands.starboard import (
    is_starboard_name_ping_enabled,
    set_starboard_name_ping_enabled,
)


class AFKButtonView(discord.ui.LayoutView):
    def __init__(self, user_id, shinyafk: bool, colafk: bool, page: str = "main"):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.shinyafk = bool(shinyafk)
        self.colafk = bool(colafk)
        self.page = page
        self.name_pings = is_starboard_name_ping_enabled(user_id)
        self._build_layout()

    def _build_layout(self):
        self.clear_items()
        if self.page == "starboard":
            self._build_starboard_layout()
            return
        self._build_main_layout()

    def _build_main_layout(self):
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

        starboard_button = discord.ui.Button(
            label="Starboard AFK",
            style=discord.ButtonStyle.secondary,
            custom_id=f"afk:{self.user_id}:starboard",
        )
        starboard_button.callback = self.open_starboard

        hunt_status = "❌ **Disabled**" if self.shinyafk else "✅ **Enabled**"
        collection_status = "❌ **Disabled**" if self.colafk else "✅ **Enabled**"

        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay("# AFK Status"),
                discord.ui.Separator(),
                discord.ui.Section(
                    f"**Shiny Hunt Pings**\n{hunt_status}",
                    accessory=hunt_button,
                ),
                discord.ui.Section(
                    f"**Collection Pings**\n{collection_status}",
                    accessory=collection_button,
                ),
                discord.ui.Separator(),
                discord.ui.ActionRow(starboard_button),
                accent_color=discord.Color.from_rgb(47, 49, 54),
            )
        )

    def _build_starboard_layout(self):
        name_button = discord.ui.Button(
            label="Toggle",
            style=discord.ButtonStyle.primary,
            custom_id=f"afk:{self.user_id}:name-pings",
        )
        name_button.callback = self.toggle_name_pings

        back_button = discord.ui.Button(
            label="Back",
            style=discord.ButtonStyle.primary,
            custom_id=f"afk:{self.user_id}:back",
        )
        back_button.callback = self.back_to_main

        name_status = "✅ **Enabled**" if self.name_pings else "❌ **Disabled**"
        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay("# Name Pings"),
                discord.ui.Separator(),
                discord.ui.Section(
                    f"**Name Pings**\n{name_status}\n-# Show your name on starboard catch messages. If this is off, your catches are still logged as Anonymous User.",
                    accessory=name_button,
                ),
                discord.ui.Separator(),
                discord.ui.ActionRow(back_button),
                accent_color=discord.Color.from_rgb(47, 49, 54),
            )
        )

    async def _check_owner(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Only you can use these buttons.",
                ephemeral=True,
            )
            return False
        if is_suspended(interaction.user):
            await interaction.response.send_message(
                "❌ You are suspended from using Mimi and cannot add pings.",
                ephemeral=True,
            )
            return False
        return True

    async def toggle_hunt(self, interaction: discord.Interaction):
        if not await self._check_owner(interaction):
            return

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
        await refresh_hunt_cache(db, [guild.id for guild in interaction.client.guilds])

        self.shinyafk = new_val
        self._build_layout()
        await interaction.response.edit_message(view=self)

    async def toggle_collection(self, interaction: discord.Interaction):
        if not await self._check_owner(interaction):
            return

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
        await refresh_collection_cache(db)

        self.colafk = new_val
        self._build_layout()
        await interaction.response.edit_message(view=self)

    async def open_starboard(self, interaction: discord.Interaction):
        if not await self._check_owner(interaction):
            return
        self.page = "starboard"
        self.name_pings = is_starboard_name_ping_enabled(self.user_id)
        self._build_layout()
        await interaction.response.edit_message(view=self)

    async def toggle_name_pings(self, interaction: discord.Interaction):
        if not await self._check_owner(interaction):
            return
        self.name_pings = not self.name_pings
        set_starboard_name_ping_enabled(self.user_id, self.name_pings)
        self._build_layout()
        await interaction.response.edit_message(view=self)

    async def back_to_main(self, interaction: discord.Interaction):
        if not await self._check_owner(interaction):
            return
        self.page = "main"
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
        if is_suspended(ctx.author):
            return await ctx.send("❌ You are suspended from using Mimi and cannot add pings.")
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
