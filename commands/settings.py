import discord
from discord.ext import commands

from database import db


async def get_server_settings(server_id: int):
    await db.execute(
        """
        INSERT INTO server_settings (serverid)
        VALUES ($1)
        ON CONFLICT (serverid) DO NOTHING
        """,
        server_id,
    )
    return await db.fetchrow(
        """
        SELECT naming, only_ping, main_starboard, shiny_starboard, gmax_starboard
        FROM server_settings
        WHERE serverid = $1
        """,
        server_id,
    )

class ServerSettingsView(discord.ui.LayoutView):
    def __init__(self, author_id: int, server_id: int, naming_enabled: bool = True, only_pings: bool = False):
        super().__init__(timeout=180)
        self.author_id = author_id
        self.server_id = server_id
        self.naming_enabled = naming_enabled
        self.only_pings = only_pings

        self.naming_button = discord.ui.Button(
            label="Open",
            style=discord.ButtonStyle.primary,
            custom_id="settings:naming",
        )
        self.naming_button.callback = self.naming

        self.starboard_button = discord.ui.Button(
            label="Open",
            style=discord.ButtonStyle.primary,
            custom_id="settings:starboard",
        )
        self.starboard_button.callback = self.starboard

        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(
                    "# Server Settings\nConfigure settings for Mimi in your server."
                ),
                discord.ui.Separator(),
                discord.ui.Section(
                    "**Naming**\n-# Configure server naming preferences.",
                    accessory=self.naming_button,
                ),
                discord.ui.Section(
                    "**Starboard**\n-# Configure the server starboard.",
                    accessory=self.starboard_button,
                ),
                accent_color=discord.Color.from_rgb(47, 49, 54),
            )
        )

    async def _check_user(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "❌ Only the person who opened these settings can use the buttons.",
                ephemeral=True,
            )
            return False
        return True

    async def _check_admin(self, interaction: discord.Interaction) -> bool:
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "❌ Only server administrators can change settings.",
                ephemeral=True,
            )
            return False
        return True

    async def naming(self, interaction: discord.Interaction):
        if not await self._check_user(interaction):
            return
        await interaction.response.edit_message(
            view=NamingSettingsView(
                self.author_id,
                self.server_id,
                self.naming_enabled,
                self.only_pings,
            )
        )

    async def starboard(self, interaction: discord.Interaction):
        if not await self._check_user(interaction):
            return
        settings = await get_server_settings(self.server_id)
        await interaction.response.edit_message(
            view=StarboardSettingsView(
                self.author_id,
                self.server_id,
                settings["main_starboard"],
                settings["shiny_starboard"],
                settings["gmax_starboard"],
            )
        )


class ServerSettings(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="settings")
    @commands.guild_only()
    async def settings(self, ctx: commands.Context):
        settings = await get_server_settings(ctx.guild.id)
        await ctx.send(
            view=ServerSettingsView(
                ctx.author.id,
                ctx.guild.id,
                settings["naming"],
                settings["only_ping"],
            )
        )


class NamingSettingsView(discord.ui.LayoutView):
    def __init__(self, author_id: int, server_id: int, naming_enabled: bool = True, only_pings: bool = False):
        super().__init__(timeout=180)
        self.author_id = author_id
        self.server_id = server_id
        self.naming_enabled = naming_enabled
        self.only_pings = only_pings
        self._build_layout()

    def _build_layout(self):
        self.clear_items()

        naming_button = discord.ui.Button(
            label="On" if self.naming_enabled else "Off",
            style=discord.ButtonStyle.success if self.naming_enabled else discord.ButtonStyle.danger,
            custom_id="settings:naming:toggle",
        )
        naming_button.callback = self.toggle_naming

        only_pings_button = discord.ui.Button(
            label="On" if self.only_pings else "Off",
            style=discord.ButtonStyle.success if self.only_pings else discord.ButtonStyle.danger,
            custom_id="settings:only-pings:toggle",
        )
        only_pings_button.callback = self.toggle_only_pings

        back_button = discord.ui.Button(
            label="Back",
            style=discord.ButtonStyle.primary,
            custom_id="settings:naming:back",
        )
        back_button.callback = self.back

        naming_status = "✅ **Enabled**" if self.naming_enabled else "❌ **Disabled**"
        only_pings_status = "✅ **Enabled**" if self.only_pings else "❌ **Disabled**"

        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(
                    "# Naming settings\nConfigure how Mimi names spawns in your server."
                ),
                discord.ui.Separator(),
                discord.ui.Section(
                    f"**Naming**\n{naming_status}\n-# Allow Mimi to name PokeTwo spawns in your server.",
                    accessory=naming_button,
                ),
                discord.ui.Section(
                    f"**Only-Pings**\n{only_pings_status}\n-# Only name spawns with hunt, collection, or reserve pings.",
                    accessory=only_pings_button,
                ),
                discord.ui.Separator(),
                discord.ui.ActionRow(back_button),
                accent_color=discord.Color.from_rgb(47, 49, 54),
            )
        )

    async def _check_user(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "❌ Only the person who opened these settings can use the buttons.",
                ephemeral=True,
            )
            return False
        return True

    async def _check_admin(self, interaction: discord.Interaction) -> bool:
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "❌ Only server administrators can change settings.",
                ephemeral=True,
            )
            return False
        return True

    async def toggle_naming(self, interaction: discord.Interaction):
        if not await self._check_user(interaction) or not await self._check_admin(interaction):
            return
        self.naming_enabled = not self.naming_enabled
        await db.execute(
            "UPDATE server_settings SET naming = $1 WHERE serverid = $2",
            self.naming_enabled,
            self.server_id,
        )
        self._build_layout()
        await interaction.response.edit_message(view=self)

    async def toggle_only_pings(self, interaction: discord.Interaction):
        if not await self._check_user(interaction) or not await self._check_admin(interaction):
            return
        self.only_pings = not self.only_pings
        await db.execute(
            "UPDATE server_settings SET only_ping = $1 WHERE serverid = $2",
            self.only_pings,
            self.server_id,
        )
        self._build_layout()
        await interaction.response.edit_message(view=self)

    async def back(self, interaction: discord.Interaction):
        if not await self._check_user(interaction):
            return
        await interaction.response.edit_message(
            view=ServerSettingsView(
                self.author_id,
                self.server_id,
                self.naming_enabled,
                self.only_pings,
            )
        )


class StarboardSettingsView(discord.ui.LayoutView):
    def __init__(self, author_id: int, server_id: int, main_starboard=None, shiny_starboard=None, gmax_starboard=None):
        super().__init__(timeout=180)
        self.author_id = author_id
        self.server_id = server_id
        self.main_starboard = main_starboard
        self.shiny_starboard = shiny_starboard
        self.gmax_starboard = gmax_starboard
        self._build_layout()

    def _build_layout(self):
        self.clear_items()

        main_select_kwargs = {
            "placeholder": "Select main starboard channel",
            "channel_types": [discord.ChannelType.text],
            "custom_id": "settings:starboard:main-channel",
        }
        if self.main_starboard is not None:
            main_select_kwargs["default_values"] = [discord.Object(id=int(self.main_starboard))]
        self.main_select = discord.ui.ChannelSelect(**main_select_kwargs)
        self.main_select.callback = self.select_main_channel

        shiny_select_kwargs = {
            "placeholder": "Select shiny board channel",
            "channel_types": [discord.ChannelType.text],
            "custom_id": "settings:starboard:shiny-channel",
        }
        if self.shiny_starboard is not None:
            shiny_select_kwargs["default_values"] = [discord.Object(id=int(self.shiny_starboard))]
        self.shiny_select = discord.ui.ChannelSelect(**shiny_select_kwargs)
        self.shiny_select.callback = self.select_shiny_channel

        gmax_select_kwargs = {
            "placeholder": "Select Gmax board channel",
            "channel_types": [discord.ChannelType.text],
            "custom_id": "settings:starboard:gmax-channel",
        }
        if self.gmax_starboard is not None:
            gmax_select_kwargs["default_values"] = [discord.Object(id=int(self.gmax_starboard))]
        self.gmax_select = discord.ui.ChannelSelect(**gmax_select_kwargs)
        self.gmax_select.callback = self.select_gmax_channel

        back_button = discord.ui.Button(
            label="Back",
            style=discord.ButtonStyle.primary,
            custom_id="settings:starboard:back",
        )
        back_button.callback = self.back

        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(
                    "# Starboard settings\nConfigure channels for each type of catch."
                ),
                discord.ui.Separator(),
                discord.ui.TextDisplay(
                    "**Main Starboard**\n-# Shows shiny, Gmax, high IV, and low IV catches."
                ),
                discord.ui.ActionRow(self.main_select),
                discord.ui.Separator(),
                discord.ui.TextDisplay(
                    "**Shiny Board**\n-# Shows shiny catches only."
                ),
                discord.ui.ActionRow(self.shiny_select),
                discord.ui.Separator(),
                discord.ui.TextDisplay(
                    "**Gmax Board**\n-# Shows Gmax catches only."
                ),
                discord.ui.ActionRow(self.gmax_select),
                discord.ui.Separator(),
                discord.ui.ActionRow(back_button),
                accent_color=discord.Color.from_rgb(47, 49, 54),
            )
        )

    async def _check_user(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "❌ Only the person who opened these settings can use the buttons.",
                ephemeral=True,
            )
            return False
        return True

    async def _check_admin(self, interaction: discord.Interaction) -> bool:
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "❌ Only server administrators can change settings.",
                ephemeral=True,
            )
            return False
        return True

    async def _confirm_channel(self, interaction: discord.Interaction, board_name: str, selector):
        if not await self._check_user(interaction) or not await self._check_admin(interaction):
            return
        channel = selector.values[0] if selector.values else None
        if channel is None:
            return await interaction.response.send_message("❌ No channel was selected.", ephemeral=True)

        column_by_board = {
            "Main Starboard": "main_starboard",
            "Shiny Board": "shiny_starboard",
            "Gmax Board": "gmax_starboard",
        }
        await db.execute(
            f"UPDATE server_settings SET {column_by_board[board_name]} = $1 WHERE serverid = $2",
            channel.id,
            self.server_id,
        )
        channel_name = channel.mention if channel is not None else "that channel"
        await interaction.response.send_message(
            f"✅ {board_name} channel saved: {channel_name}",
            ephemeral=True,
        )

    async def select_main_channel(self, interaction: discord.Interaction):
        await self._confirm_channel(interaction, "Main Starboard", self.main_select)

    async def select_shiny_channel(self, interaction: discord.Interaction):
        await self._confirm_channel(interaction, "Shiny Board", self.shiny_select)

    async def select_gmax_channel(self, interaction: discord.Interaction):
        await self._confirm_channel(interaction, "Gmax Board", self.gmax_select)

    async def back(self, interaction: discord.Interaction):
        if not await self._check_user(interaction):
            return
        settings = await get_server_settings(self.server_id)
        await interaction.response.edit_message(
            view=ServerSettingsView(
                self.author_id,
                self.server_id,
                settings["naming"],
                settings["only_ping"],
            )
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(ServerSettings(bot))
