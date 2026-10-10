import discord
from discord.ext import commands


class ServerSettingsView(discord.ui.View):
    def __init__(self, author_id: int):
        super().__init__(timeout=180)
        self.author_id = author_id

    async def _check_user(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "❌ Only the person who opened these settings can use the buttons.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Naming", style=discord.ButtonStyle.success, row=0)
    async def naming(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        await interaction.response.send_message(
            "Naming settings will be available here soon.",
            ephemeral=True,
        )

    @discord.ui.button(label="Starboard", style=discord.ButtonStyle.success, row=0)
    async def starboard(self, interaction: discord.Interaction, button: discord.ui.Button):
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
        embed = discord.Embed(
            title="Server Settings",
            description="━━━━━━━━━━━━━━━━━━━━━━━━\nManage settings for this server.",
            color=discord.Color.from_rgb(88, 96, 105),
        )
        embed.add_field(
            name="Naming",
            value="Configure server naming preferences.",
            inline=True,
        )
        embed.add_field(
            name="\u200b",
            value="\u200b",
            inline=True,
        )
        embed.add_field(
            name="Starboard",
            value="Configure the server starboard.",
            inline=True,
        )
        embed.set_footer(text="Select a setting below to continue.")

        await ctx.send(embed=embed, view=ServerSettingsView(ctx.author.id))


async def setup(bot: commands.Bot):
    await bot.add_cog(ServerSettings(bot))
