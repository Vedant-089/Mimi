import json
import re
import discord
from discord.ext import commands
from database import db
from functions import (
    GRIND_SUPPORT_SERVER_IDS,
    is_suspended,
    refresh_tp_qp_cache,
    set_quest_requirements,
)

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


def parse_quest_pings(message: discord.Message):
    """Extract type and region names and remaining counts from a quest message."""
    text_parts = [message.content or ""]
    for embed in message.embeds:
        text_parts.extend([
            embed.title or "",
            embed.description or "",
            embed.author.name if embed.author else "",
        ])
        text_parts.extend(
            f"{field.name}\n{field.value}" for field in embed.fields
        )
    text = "\n".join(text_parts).casefold()

    type_pattern = "|".join(
        re.escape(ping_type) for ping_type in sorted(ALL_TYPES, key=len, reverse=True)
    )
    region_pattern = "|".join(
        re.escape(region) for region in sorted(ALL_REGIONS, key=len, reverse=True)
    )

    types = set()
    regions = set()
    type_requirements = {}
    region_requirements = {}

    for line in text.splitlines():
        progress_match = re.search(r"\((\d+)\s*/\s*(\d+)\)", line)
        remaining = None
        if progress_match:
            remaining = max(
                0,
                int(progress_match.group(2)) - int(progress_match.group(1)),
            )

        line_types = set(re.findall(rf"\b({type_pattern})-type\b", line))
        line_regions = set(re.findall(rf"\b({region_pattern})\s+region\b", line))
        types.update(line_types)
        regions.update(line_regions)
        if remaining is not None:
            for quest_type in line_types:
                type_requirements[quest_type] = max(
                    remaining,
                    type_requirements.get(quest_type, 0),
                )
            for region in line_regions:
                region_requirements[region] = max(
                    remaining,
                    region_requirements.get(region, 0),
                )

    return types, regions, type_requirements, region_requirements


def build_ping_payload(names, key_name, pokemon_resolver):
    selected_names = sorted(set(names))
    return json.dumps({
        key_name: selected_names,
        "pokemons": pokemon_resolver(selected_names),
    })


def parse_ping_arguments(arguments, valid_names):
    tokens = [
        token.strip().casefold()
        for argument in arguments
        for token in argument.split(",")
        if token.strip()
    ]
    remove = bool(tokens and tokens[0] in {"remove", "r", "remove/r"})
    if remove:
        tokens.pop(0)
    invalid = sorted(set(token for token in tokens if token not in valid_names))
    if invalid:
        return remove, set(), invalid
    return remove, set(tokens), []


# ==================================================
# TYPE AND REGION PING VIEWS
# ==================================================

class PingToggleButton(discord.ui.Button):
    def __init__(self, name: str, kind: str, row: int):
        super().__init__(
            label=name.capitalize(),
            style=discord.ButtonStyle.secondary,
            row=row,
            custom_id=f"toggle_{kind}_{name}",
        )
        self.name = name
        self.kind = kind

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        if view is None or interaction.user.id != view.author_id:
            return await interaction.response.send_message(
                "❌ You cannot use this menu!", ephemeral=True
            )
        await view.toggle_ping(interaction, self.name)


class PingView(discord.ui.View):
    kind = ""
    title = ""
    all_names = ()

    def __init__(self, author_id: int, server_id: int, active_names: set):
        super().__init__(timeout=180)
        self.author_id = author_id
        self.server_id = server_id
        self.active_names = set(active_names)

        for index, name in enumerate(self.all_names):
            self.add_item(PingToggleButton(name, self.kind, index // 5))

        control_row = (len(self.all_names) + 4) // 5
        self.add_item(
            discord.ui.Button(
                label="All On",
                style=discord.ButtonStyle.success,
                row=control_row,
                custom_id=f"{self.kind}_all_on",
            )
        )
        self.children[-1].callback = self.all_on
        self.add_item(
            discord.ui.Button(
                label="All Off",
                style=discord.ButtonStyle.danger,
                row=control_row,
                custom_id=f"{self.kind}_all_off",
            )
        )
        self.children[-1].callback = self.all_off
        self.refresh_buttons()

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(title=self.title, color=discord.Color.purple())
        embed.description = "\n".join(
            f"{'🟢' if name in self.active_names else '🔴'} **{name.capitalize()}**"
            for name in self.all_names
        )
        embed.set_footer(text="Use the buttons below to update your pings.")
        return embed

    def refresh_buttons(self):
        for button in self.children:
            if isinstance(button, PingToggleButton):
                enabled = button.name in self.active_names
                button.style = (
                    discord.ButtonStyle.success
                    if enabled
                    else discord.ButtonStyle.secondary
                )
                button.label = (
                    f"✅ {button.name.capitalize()}"
                    if enabled
                    else button.name.capitalize()
                )

    async def toggle_ping(self, interaction: discord.Interaction, name: str):
        if is_suspended(interaction.user):
            return await interaction.response.send_message(
                "❌ You are suspended from using Mimi and cannot add pings.",
                ephemeral=True,
            )
        if name in self.active_names:
            self.active_names.remove(name)
        else:
            self.active_names.add(name)
        await self.save_to_db()
        self.refresh_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def all_on(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message(
                "❌ You cannot use this menu!", ephemeral=True
            )
        await self.update_settings(interaction, self.all_names)

    async def all_off(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message(
                "❌ You cannot use this menu!", ephemeral=True
            )
        await self.update_settings(interaction, [])

    async def update_settings(self, interaction: discord.Interaction, names):
        if is_suspended(interaction.user):
            return await interaction.response.send_message(
                "❌ You are suspended from using Mimi and cannot add pings.",
                ephemeral=True,
            )
        self.active_names = set(names)
        await self.save_to_db()
        self.refresh_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)


class TypePingView(PingView):
    kind = "type"
    title = "Type Pings"
    all_names = tuple(ALL_TYPES)

    async def save_to_db(self):
        selected = sorted(self.active_names)
        payload = json.dumps({
            "types": selected,
            "pokemons": get_pokemons_for_types(selected),
        })
        await db.execute(
            """
            INSERT INTO tp_qp (serverid, userid, type_pings, region_pings)
            VALUES ($1, $2, $3, NULL)
            ON CONFLICT (serverid, userid)
            DO UPDATE SET type_pings = EXCLUDED.type_pings;
            """,
            self.server_id,
            self.author_id,
            payload,
        )
        await refresh_tp_qp_cache(db)


class RegionPingView(PingView):
    kind = "region"
    title = "Region Pings"
    all_names = tuple(ALL_REGIONS)

    async def save_to_db(self):
        selected = sorted(self.active_names)
        payload = json.dumps({
            "regions": selected,
            "pokemons": get_pokemons_for_regions(selected),
        })
        await db.execute(
            """
            INSERT INTO tp_qp (serverid, userid, type_pings, region_pings)
            VALUES ($1, $2, NULL, $3)
            ON CONFLICT (serverid, userid)
            DO UPDATE SET region_pings = EXCLUDED.region_pings;
            """,
            self.server_id,
            self.author_id,
            payload,
        )
        await refresh_tp_qp_cache(db)


# ==================================================
# COG COMMANDS
# ==================================================

class TypeRegionPings(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="tp")
    async def type_pings_cmd(self, ctx, *arguments):
        """Displays type pings embed with toggle options."""
        row = await db.fetchrow(
            "SELECT type_pings FROM tp_qp WHERE serverid = $1 AND userid = $2",
            ctx.guild.id,
            ctx.author.id
        )
        active_types = parse_user_enabled_keys(row["type_pings"] if row else None, "types")

        if arguments:
            remove, requested, invalid = parse_ping_arguments(arguments, set(ALL_TYPES))
            if invalid:
                return await ctx.send(
                    f"❌ Unknown type(s): {', '.join(name.capitalize() for name in invalid)}."
                )
            if not requested:
                return await ctx.send("❌ Specify one or more types.")
            if is_suspended(ctx.author):
                return await ctx.send(
                    "❌ You are suspended from using Mimi and cannot add pings."
                )
            if remove:
                active_types.difference_update(requested)
                action = "Removed"
            else:
                active_types.update(requested)
                action = "Added"
            payload = build_ping_payload(active_types, "types", get_pokemons_for_types)
            await db.execute(
                """
                INSERT INTO tp_qp (serverid, userid, type_pings, region_pings)
                VALUES ($1, $2, $3, NULL)
                ON CONFLICT (serverid, userid)
                DO UPDATE SET type_pings = EXCLUDED.type_pings;
                """,
                ctx.guild.id,
                ctx.author.id,
                payload,
            )
            await refresh_tp_qp_cache(db)
            return await ctx.send(
                f"✅ {action} type pings: "
                f"{', '.join(name.capitalize() for name in sorted(requested))}."
            )

        view = TypePingView(ctx.author.id, ctx.guild.id, active_types)
        embed = view.build_embed()
        await ctx.send(embed=embed, view=view)

    @commands.command(name="rp", aliases=["qp"])
    async def region_pings_cmd(self, ctx, *arguments):
        """Displays region pings embed with toggle options."""
        row = await db.fetchrow(
            "SELECT region_pings FROM tp_qp WHERE serverid = $1 AND userid = $2",
            ctx.guild.id,
            ctx.author.id
        )
        active_regions = parse_user_enabled_keys(row["region_pings"] if row else None, "regions")

        if arguments:
            remove, requested, invalid = parse_ping_arguments(arguments, set(ALL_REGIONS))
            if invalid:
                return await ctx.send(
                    f"❌ Unknown region(s): "
                    f"{', '.join(name.capitalize() for name in invalid)}."
                )
            if not requested:
                return await ctx.send("❌ Specify one or more regions.")
            if is_suspended(ctx.author):
                return await ctx.send(
                    "❌ You are suspended from using Mimi and cannot add pings."
                )
            if remove:
                active_regions.difference_update(requested)
                action = "Removed"
            else:
                active_regions.update(requested)
                action = "Added"
            payload = build_ping_payload(
                active_regions, "regions", get_pokemons_for_regions
            )
            await db.execute(
                """
                INSERT INTO tp_qp (serverid, userid, type_pings, region_pings)
                VALUES ($1, $2, NULL, $3)
                ON CONFLICT (serverid, userid)
                DO UPDATE SET region_pings = EXCLUDED.region_pings;
                """,
                ctx.guild.id,
                ctx.author.id,
                payload,
            )
            await refresh_tp_qp_cache(db)
            return await ctx.send(
                f"✅ {action} region pings: "
                f"{', '.join(name.capitalize() for name in sorted(requested))}."
            )

        view = RegionPingView(ctx.author.id, ctx.guild.id, active_regions)
        embed = view.build_embed()
        await ctx.send(embed=embed, view=view)

    @commands.command(name="event", aliases=["ev"])
    @commands.guild_only()
    async def event_pings_cmd(self, ctx):
        """Adds type and region pings found in any replied-to text."""
        if is_suspended(ctx.author):
            return await ctx.send(
                "❌ You are suspended from using Mimi and cannot add pings."
            )

        reference = ctx.message.reference
        if reference is None or reference.message_id is None:
            return await ctx.send("❌ Reply to a message containing quest descriptions.")

        try:
            replied_message = await ctx.channel.fetch_message(reference.message_id)
        except discord.HTTPException:
            return await ctx.send("❌ I could not read the replied-to message.")

        (
            new_types,
            new_regions,
            type_requirements,
            region_requirements,
        ) = parse_quest_pings(replied_message)
        if not new_types and not new_regions:
            return await ctx.send(
                "❌ I could not find any type or region quests in that message."
            )

        grind_support_enabled = (
            ctx.guild.id in GRIND_SUPPORT_SERVER_IDS
            and await db.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM server_grind_support
                    WHERE serverid = $1 AND userid = $2
                )
                """,
                ctx.guild.id,
                ctx.author.id,
            )
        )
        row = await db.fetchrow(
            """
            SELECT type_pings, region_pings
            FROM tp_qp
            WHERE serverid = $1 AND userid = $2
            """,
            ctx.guild.id,
            ctx.author.id,
        )
        active_types = parse_user_enabled_keys(
            row["type_pings"] if row else None, "types"
        )
        active_regions = parse_user_enabled_keys(
            row["region_pings"] if row else None, "regions"
        )
        previous_types = set(active_types)
        if grind_support_enabled:
            active_types = set(new_types)
        added_types = sorted(new_types - previous_types)
        removed_types = sorted(previous_types - new_types)
        added_regions = sorted(new_regions - active_regions)
        active_types.update(new_types)
        active_regions.update(new_regions)

        await db.execute(
            """
            INSERT INTO tp_qp (serverid, userid, type_pings, region_pings)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (serverid, userid)
            DO UPDATE SET
                type_pings = EXCLUDED.type_pings,
                region_pings = EXCLUDED.region_pings;
            """,
            ctx.guild.id,
            ctx.author.id,
            build_ping_payload(active_types, "types", get_pokemons_for_types),
            build_ping_payload(active_regions, "regions", get_pokemons_for_regions),
        )
        await refresh_tp_qp_cache(db)
        await set_quest_requirements(
            ctx.guild.id,
            ctx.author.id,
            type_requirements,
            region_requirements,
            replace_types=grind_support_enabled,
        )

        added = []
        if added_types:
            added.append(f"types: {', '.join(name.capitalize() for name in added_types)}")
        if removed_types:
            added.append(
                f"removed types: {', '.join(name.capitalize() for name in removed_types)}"
            )
        if added_regions:
            added.append(
                f"regions: {', '.join(name.capitalize() for name in added_regions)}"
            )
        if not added:
            return await ctx.send("✅ All quests in that message are already in your pings.")
        await ctx.send(f"✅ Added event pings — {'; '.join(added)}.")


async def setup(bot):
    await bot.add_cog(TypeRegionPings(bot))
