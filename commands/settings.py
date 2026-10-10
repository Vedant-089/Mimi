import discord
from discord.ext import commands


class ServerSettingsView(discord.ui.LayoutView):
    def __init__(self, author_id: int):
        super().__init__(timeout=180)
        self.author_id = author_id

        self.naming_button = discord.ui.Button(
            label="Open",
            style=discord.ButtonStyle.success,
            custom_id="settings:naming",
        )
        self.naming_button.callback = self.naming

        self.starboard_button = discord.ui.Button(
            label="Open",
            style=discord.ButtonStyle.success,
            custom_id="settings:starboard",
        )
        self.starboard_button.callback = self.starboard

        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(
                    "# Server Settings\nManage settings for this server."
                ),
                discord.ui.Separator(),
                discord.ui.Section(
                    "### Naming\nConfigure server naming preferences.",
                    accessory=self.naming_button,
                ),
                discord.ui.Section(
                    "### Starboard\nConfigure the server starboard.",
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
        await interaction.response.send_message(
            "Naming settings will be available here soon.",
            ephemeral=True,
        )

    async def starboard(self, interaction: discord.Interaction):
        if not await self._check_user(interaction):
            return
        await interaction.response.send_message(
            "Starboard settings will be available here soon.",
            ephemeral=True,
        )


class ServerSettings(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="settings")
    @commands.guild_only()
    async def settings(self, ctx: commands.Context):
        await ctx.send(view=ServerSettingsView(ctx.author.id))


async def setup(bot: commands.Bot):
    await bot.add_cog(ServerSettings(bot))
