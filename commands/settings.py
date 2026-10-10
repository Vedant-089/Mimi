import discord
from discord.ext import commands


class ServerSettingsView(discord.ui.LayoutView):
    def __init__(self, author_id: int):
        super().__init__(timeout=180)
        self.author_id = author_id

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

    async def naming(self, interaction: discord.Interaction):
        if not await self._check_user(interaction):
            return
        await interaction.response.edit_message(view=NamingSettingsView(self.author_id))

    async def starboard(self, interaction: discord.Interaction):
        if not await self._check_user(interaction):
            return
        await interaction.response.edit_message(view=StarboardSettingsView(self.author_id))


class ServerSettings(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="settings")
    @commands.guild_only()
    async def settings(self, ctx: commands.Context):
        await ctx.send(view=ServerSettingsView(ctx.author.id))


class NamingSettingsView(discord.ui.LayoutView):
    def __init__(self, author_id: int, naming_enabled: bool = True, only_pings: bool = False):
        super().__init__(timeout=180)
        self.author_id = author_id
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

    async def toggle_naming(self, interaction: discord.Interaction):
        if not await self._check_user(interaction):
            return
        self.naming_enabled = not self.naming_enabled
        self._build_layout()
        await interaction.response.edit_message(view=self)

    async def toggle_only_pings(self, interaction: discord.Interaction):
        if not await self._check_user(interaction):
            return
        self.only_pings = not self.only_pings
        self._build_layout()
        await interaction.response.edit_message(view=self)

    async def back(self, interaction: discord.Interaction):
        if not await self._check_user(interaction):
            return
        await interaction.response.edit_message(view=ServerSettingsView(self.author_id))


class StarboardSettingsView(discord.ui.LayoutView):
    def __init__(self, author_id: int):
        super().__init__(timeout=180)
        self.author_id = author_id
        self._build_layout()

    def _build_layout(self):
        self.clear_items()

        self.main_select = discord.ui.ChannelSelect(
            placeholder="Select main starboard channel",
            channel_types=[discord.ChannelType.text],
            custom_id="settings:starboard:main-channel",
        )
        self.main_select.callback = self.select_main_channel

        self.shiny_select = discord.ui.ChannelSelect(
            placeholder="Select shiny board channel",
            channel_types=[discord.ChannelType.text],
            custom_id="settings:starboard:shiny-channel",
        )
        self.shiny_select.callback = self.select_shiny_channel

        self.gmax_select = discord.ui.ChannelSelect(
            placeholder="Select Gmax board channel",
            channel_types=[discord.ChannelType.text],
            custom_id="settings:starboard:gmax-channel",
        )
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

    async def _confirm_channel(self, interaction: discord.Interaction, board_name: str, selector):
        if not await self._check_user(interaction):
            return
        channel = selector.values[0] if selector.values else None
        channel_name = channel.mention if channel is not None else "that channel"
        await interaction.response.send_message(
            f"✅ {board_name} channel selected: {channel_name}",
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
        await interaction.response.edit_message(view=ServerSettingsView(self.author_id))


async def setup(bot: commands.Bot):
    await bot.add_cog(ServerSettings(bot))
