import json
import discord
from discord.ext import commands
from database import db
from functions import invalidate_tp_qp_cache

# Load type and region JSON mappings
with open("typing.json", "r", encoding="utf-8") as f:
    TYPING_DATA = json.load(f)

with open("region.json", "r", encoding="utf-8") as f:
    REGION_DATA = json.load(f)

ALL_TYPES = [t for t in TYPING_DATA.keys() if t != "???"]
ALL_REGIONS = [r for r in REGION_DATA.keys() if r != "???"]


def get_pokemons_for_types(selected_types):
    pokes = set()
    for t in selected_types:
        if t in TYPING_DATA:
            for name in TYPING_DATA[t]:
                pokes.add(name.lower().strip())
    return sorted(pokes)


def get_pokemons_for_regions(selected_regions):
    pokes = set()
    for r in selected_regions:
        if r in REGION_DATA:
            for name in REGION_DATA[r]:
                pokes.add(name.lower().strip())
    return sorted(pokes)


def parse_user_enabled_keys(raw_data, key_name):
    if not raw_data:
        return set()
    try:
        data = json.loads(raw_data)
        if isinstance(data, dict):
            return set(data.get(key_name, []))
    except Exception:
        pass
    return set()


# ==================================================
# TYPE PINGS VIEW & SELECT
# ==================================================

class TypeSelect(discord.ui.Select):
    def __init__(self, active_types):
        options = [
            discord.SelectOption(
                label=t.capitalize(),
                value=t,
                default=(t in active_types)
            )
            for t in ALL_TYPES
        ]
        super().__init__(
            placeholder="Select type pings to toggle...",
            min_values=0,
            max_values=len(options),
            options=options,
            row=0
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.view.author_id:
            return await interaction.response.send_message(
                "❌ You cannot use this menu!", ephemeral=True
            )

        selected_types = self.values
        await self.view.update_type_settings(interaction, selected_types)


class TypePingView(discord.ui.View):
    def __init__(self, author_id: int, server_id: int, active_types: set):
        super().__init__(timeout=180)
        self.author_id = author_id
        self.server_id = server_id
        self.active_types = set(active_types)

        self.select_menu = TypeSelect(self.active_types)
        self.add_item(self.select_menu)

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="Type Pings",
            color=discord.Color.purple()
        )
        lines = []
        for t in ALL_TYPES:
            icon = "🟢" if t in self.active_types else "🔴"
            lines.append(f"{icon} **{t.capitalize()}**")

        embed.description = "\n".join(lines)
        embed.set_footer(text="Use the dropdown or buttons below to update your type pings.")
        return embed

    def update_select_defaults(self):
        for option in self.select_menu.options:
            option.default = (option.value in self.active_types)

    async def save_to_db(self):
        selected = sorted(self.active_types)
        pokemons = get_pokemons_for_types(selected)
        payload = json.dumps({"types": selected, "pokemons": pokemons})

        query = """
            INSERT INTO tp_qp (serverid, userid, type_pings, region_pings)
            VALUES ($1, $2, $3, NULL)
            ON CONFLICT (serverid, userid)
            DO UPDATE SET type_pings = EXCLUDED.type_pings;
        """
        await db.execute(query, self.server_id, self.author_id, payload)
        invalidate_tp_qp_cache()

    async def update_type_settings(self, interaction: discord.Interaction, new_types: list | set):
        self.active_types = set(new_types)
        await self.save_to_db()
        self.update_select_defaults()
        embed = self.build_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="All On", style=discord.ButtonStyle.success, row=1)
    async def all_on(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message(
                "❌ You cannot use this menu!", ephemeral=True
            )
        await self.update_type_settings(interaction, ALL_TYPES)

    @discord.ui.button(label="All Off", style=discord.ButtonStyle.danger, row=1)
    async def all_off(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message(
                "❌ You cannot use this menu!", ephemeral=True
            )
        await self.update_type_settings(interaction, [])


# ==================================================
# REGION PINGS VIEW & SELECT
# ==================================================

class RegionSelect(discord.ui.Select):
    def __init__(self, active_regions):
        options = [
            discord.SelectOption(
                label=r.capitalize(),
                value=r,
                default=(r in active_regions)
            )
            for r in ALL_REGIONS
        ]
        super().__init__(
            placeholder="Select region pings to toggle...",
            min_values=0,
            max_values=len(options),
            options=options,
            row=0
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.view.author_id:
            return await interaction.response.send_message(
                "❌ You cannot use this menu!", ephemeral=True
            )

        selected_regions = self.values
        await self.view.update_region_settings(interaction, selected_regions)


class RegionPingView(discord.ui.View):
    def __init__(self, author_id: int, server_id: int, active_regions: set):
        super().__init__(timeout=180)
        self.author_id = author_id
        self.server_id = server_id
        self.active_regions = set(active_regions)

        self.select_menu = RegionSelect(self.active_regions)
        self.add_item(self.select_menu)

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="Region Pings",
            color=discord.Color.purple()
        )
        lines = []
        for r in ALL_REGIONS:
            icon = "🟢" if r in self.active_regions else "🔴"
            lines.append(f"{icon} **{r.capitalize()}**")

        embed.description = "\n".join(lines)
        embed.set_footer(text="Use the dropdown or buttons below to update your region pings.")
        return embed

    def update_select_defaults(self):
        for option in self.select_menu.options:
            option.default = (option.value in self.active_regions)

    async def save_to_db(self):
        selected = sorted(self.active_regions)
        pokemons = get_pokemons_for_regions(selected)
        payload = json.dumps({"regions": selected, "pokemons": pokemons})

        query = """
            INSERT INTO tp_qp (serverid, userid, type_pings, region_pings)
            VALUES ($1, $2, NULL, $3)
            ON CONFLICT (serverid, userid)
            DO UPDATE SET region_pings = EXCLUDED.region_pings;
        """
        await db.execute(query, self.server_id, self.author_id, payload)
        invalidate_tp_qp_cache()

    async def update_region_settings(self, interaction: discord.Interaction, new_regions: list | set):
        self.active_regions = set(new_regions)
        await self.save_to_db()
        self.update_select_defaults()
        embed = self.build_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="All On", style=discord.ButtonStyle.success, row=1)
    async def all_on(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message(
                "❌ You cannot use this menu!", ephemeral=True
            )
        await self.update_region_settings(interaction, ALL_REGIONS)

    @discord.ui.button(label="All Off", style=discord.ButtonStyle.danger, row=1)
    async def all_off(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message(
                "❌ You cannot use this menu!", ephemeral=True
            )
        await self.update_region_settings(interaction, [])


# ==================================================
# COG COMMANDS
# ==================================================

class TypeRegionPings(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="tp")
    async def type_pings_cmd(self, ctx):
        """Displays type pings embed with toggle options."""
        row = await db.fetchrow(
            "SELECT type_pings FROM tp_qp WHERE serverid = $1 AND userid = $2",
            ctx.guild.id,
            ctx.author.id
        )
        active_types = parse_user_enabled_keys(row["type_pings"] if row else None, "types")

        view = TypePingView(ctx.author.id, ctx.guild.id, active_types)
        embed = view.build_embed()
        await ctx.send(embed=embed, view=view)

    @commands.command(name="rp", aliases=["qp"])
    async def region_pings_cmd(self, ctx):
        """Displays region pings embed with toggle options."""
        row = await db.fetchrow(
            "SELECT region_pings FROM tp_qp WHERE serverid = $1 AND userid = $2",
            ctx.guild.id,
            ctx.author.id
        )
        active_regions = parse_user_enabled_keys(row["region_pings"] if row else None, "regions")

        view = RegionPingView(ctx.author.id, ctx.guild.id, active_regions)
        embed = view.build_embed()
        await ctx.send(embed=embed, view=view)


async def setup(bot):
    await bot.add_cog(TypeRegionPings(bot))
