import discord
from discord.ext import commands


HELP_PAGES_DATA = [
    {
        "title": "✨ Shiny Hunt Commands",
        "description": (
            "• `m!shinyhunt` (`m!sh`) - Sets user's Shiny Hunt to that specific Pokémon globally.\n"
            "• `m!sh <pokemon>` - Sets your global shiny hunt target to a specified Pokémon.\n"
            "• `m!sh` - Displays your current active shiny hunt target.\n"
            "• `m!sh reset` / `none` / `clear` / `remove` - Resets and clears your current shiny hunt target."
        ),
    },
    {
        "title": "🔒 Reserve Commands",
        "description": (
            "• `m!reserves` (`m!r`) - Main command for managing Pokémon reserves in the server.\n"
            "• `m!r add <pokemon> @user` (`m!r a`) - Adds Pokémon to a user's reserves (Admins/allowed roles).\n"
            "• `m!r remove <pokemon> @user` (`m!r r`) - Removes Pokémon from a user's reserves (Admins/allowed roles).\n"
            "• `m!r remove <pokemon>` (`m!r r`) - Removes Pokémon from every user's reserves (Admins/allowed roles).\n"
            "• `m!r exchange @user` (`m!r e`) - Exchanges Pokémon from a replied reserve list with that user's reserves (Admins/allowed roles).\n"
            "• `m!r exchange @user1 @user2 [pokemon]` (`m!r e`) - Transfers selected or all reserves from user1 to user2.\n"
            "• `m!r clear` (`m!r c`) - Clears reserves for a specified user or all server reserves.\n"
            "• `m!r search <pokemon>` (`m!r s`) - Searches for users who have reserved specific Pokémon.\n"
            "• `m!r list` (`m!r l`) - Lists all active Pokémon reserves in the server.\n"
            "• `m!r @user` - Displays all reserved Pokémon for a specific user.\n"
            "• `m!allow <roleid1>, <roleid2>` - Admin-only; allows roles to use restricted reserve commands."
        ),
    },
    {
        "title": "📦 Collection Commands",
        "description": (
            "• `m!cl` - Main command for managing your server collection pings.\n"
            "• `m!cl add <pokemon>` - Adds Pokémon to your collection pings.\n"
            "• `m!cl remove <pokemon>` - Removes Pokémon from your collection pings.\n"
            "• `m!cl clear` - Clears all Pokémon from your collection pings.\n"
            "• `m!cl list` - Displays all Pokémon in your server collection.\n"
            "• `m!gcl add/remove <pokemon>` - Adds or removes exact Pokémon names in every server collection.\n"
            "• `m!gcl clear` - Clears your collection in every server."
        ),
    },
    {
        "title": "🎯 Type & Region Ping Commands",
        "description": (
            "• `m!tp` - Opens the interactive Type Pings embed with toggle buttons and dropdown.\n"
            "• `m!rp` (`m!qp`) - Opens the interactive Region Pings embed with toggle buttons and dropdown."
        ),
    },
    {
        "title": "👑 Premium & Support Commands",
        "description": (
            "• `m!perk` (`m!premium`) - Displays the active premium perk status and incense limit for the current server.\n"
            "• `m!perk <serverid>` - Displays active perk status for a specific server (Authorized Users Only).\n"
            "• `m!support <serverid> <perk>` - Grants or updates a server's premium perk level (Authorized Users Only).\n"
            "• `m!support reset <serverid>` - Resets and removes a server's premium perk level (Authorized Users Only)."
        ),
    },
    {
        "title": "⚙️ Utility & AFK Commands",
        "description": (
            "• `m!afk` - Opens the interactive AFK menu to toggle global Shiny Hunt and Collection AFK.\n"
            "• `m!rank <rare/regional/incense>` - Toggles ping roles (`Rares`, `Regionals`, `Incense`).\n"
            "• `m!incenses` - Displays active tracked incense channels in the server.\n"
            "• `m!event <role_names>` - Replaces your current type and region roles with selected ones.\n"
            "• `m!roles` - Creates standard type, region, and perk roles (Admin Only).\n"
            "• `m!settings` - Opens the server settings panel."
        ),
    },
]


class HelpSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Shiny Hunt", description="Shiny hunt commands", value="0", emoji="✨"),
            discord.SelectOption(label="Reserves", description="Server reserve commands", value="1", emoji="🔒"),
            discord.SelectOption(label="Collection", description="Collection ping commands", value="2", emoji="📦"),
            discord.SelectOption(label="Type & Region Pings", description="Type and region ping embeds", value="3", emoji="🎯"),
            discord.SelectOption(label="Premium & Support", description="Perk and support commands", value="4", emoji="👑"),
            discord.SelectOption(label="Utility & AFK", description="AFK, ranks, and utility commands", value="5", emoji="⚙️"),
        ]
        super().__init__(
            placeholder="Select a command category...",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.view.author_id:
            return await interaction.response.send_message("❌ You cannot use this menu!", ephemeral=True)

        self.view.current_page = int(self.values[0])
        await self.view.show_page(interaction)


class HelpView(discord.ui.View):
    def __init__(self, pages: list[discord.Embed], author_id: int):
        super().__init__(timeout=180)
        self.pages = pages
        self.author_id = author_id
        self.current_page = 0

        self.select_menu = HelpSelect()
        self.add_item(self.select_menu)

        self._update_controls()

    def _update_controls(self):
        self.first_page_btn.disabled = (self.current_page == 0)
        self.prev_page_btn.disabled = (self.current_page == 0)
        self.next_page_btn.disabled = (self.current_page >= len(self.pages) - 1)
        self.last_page_btn.disabled = (self.current_page >= len(self.pages) - 1)

        for option in self.select_menu.options:
            option.default = (option.value == str(self.current_page))

    async def show_page(self, interaction: discord.Interaction):
        self._update_controls()
        await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)

    @discord.ui.button(label="⏮", style=discord.ButtonStyle.secondary, row=1)
    async def first_page_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("❌ You cannot use this menu!", ephemeral=True)
        self.current_page = 0
        await self.show_page(interaction)

    @discord.ui.button(label="◀", style=discord.ButtonStyle.primary, row=1)
    async def prev_page_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("❌ You cannot use this menu!", ephemeral=True)
        if self.current_page > 0:
            self.current_page -= 1
        await self.show_page(interaction)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.primary, row=1)
    async def next_page_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("❌ You cannot use this menu!", ephemeral=True)
        if self.current_page < len(self.pages) - 1:
            self.current_page += 1
        await self.show_page(interaction)

    @discord.ui.button(label="⏭", style=discord.ButtonStyle.secondary, row=1)
    async def last_page_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("❌ You cannot use this menu!", ephemeral=True)
        self.current_page = len(self.pages) - 1
        await self.show_page(interaction)


class HelpCommand(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def build_help_pages(self) -> list[discord.Embed]:
        total_pages = len(HELP_PAGES_DATA)
        embeds = []

        for idx, data in enumerate(HELP_PAGES_DATA, start=1):
            embed = discord.Embed(
                title=data["title"],
                description=data["description"],
                color=discord.Color.purple()
            )
            embed.set_footer(text=f"Page {idx}/{total_pages} • Use buttons or dropdown below to navigate")
            embeds.append(embed)

        return embeds

    @commands.command(name="help", aliases=["h"])
    async def help_cmd(self, ctx: commands.Context):
        """Displays interactive paginated help menu."""
        pages = self.build_help_pages()
        view = HelpView(pages, ctx.author.id)
        await ctx.send(embed=pages[0], view=view)


async def setup(bot: commands.Bot):
    await bot.add_cog(HelpCommand(bot))
