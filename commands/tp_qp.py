import json
import logging
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

LOGGER = logging.getLogger(__name__)

# Load type and region JSON mappings
with open("typing.json", "r", encoding="utf-8") as f:
    TYPING_DATA = json.load(f)

with open("region.json", "r", encoding="utf-8") as f:
    REGION_DATA = json.load(f)

ALL_TYPES = [t for t in TYPING_DATA.keys() if t != "???"]
ALL_REGIONS = [r for r in REGION_DATA.keys() if r != "???"]
TYPE_KEYS_BY_CASEFOLD = {name.casefold(): name for name in ALL_TYPES}
REGION_KEYS_BY_CASEFOLD = {name.casefold(): name for name in ALL_REGIONS}


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


def _normalize_quest_text(value: str) -> str:
    """Normalize Unicode spacing/dashes without destroying line boundaries."""
    value = value.casefold()
    value = re.sub(r"[\u200b\u200c\u200d\ufeff\u2060]", "", value)
    value = re.sub(r"[\u2010-\u2015\u2212\ufe58\ufe63\uff0d]", "-", value)
    value = re.sub(r"[\u00a0\t\f\v]+", " ", value)
    return value


def _collect_embed_text(embed) -> list[str]:
    """Read the user-visible text from a discord.py Embed or embed-like object."""
    parts = []

    # Read explicit attributes first. This avoids relying on the exact shape of
    # Embed.to_dict() and ensures description/field values are not missed.
    for attr in ("title", "description", "url"):
        value = getattr(embed, attr, None)
        if isinstance(value, str) and value:
            parts.append(value)

    for attr in ("author", "footer"):
        nested = getattr(embed, attr, None)
        if nested is not None:
            value = getattr(nested, "name", None) or getattr(nested, "text", None)
            if isinstance(value, str) and value:
                parts.append(value)

    for field in getattr(embed, "fields", []) or []:
        name = getattr(field, "name", None)
        value = getattr(field, "value", None)
        if isinstance(name, str) and name:
            parts.append(name)
        if isinstance(value, str) and value:
            parts.append(value)

    # Also support raw dicts and discord.py versions/objects exposing to_dict.
    try:
        raw = embed.to_dict() if hasattr(embed, "to_dict") else embed
        if isinstance(raw, dict):
            def walk(item):
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    for child in item.values():
                        walk(child)
                elif isinstance(item, (list, tuple)):
                    for child in item:
                        walk(child)
            walk(raw)
    except Exception:
        LOGGER.exception("Failed to serialize an embed while parsing quest text")

    # Preserve order while removing duplicate values.
    return list(dict.fromkeys(part for part in parts if part))


def _collect_nested_strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [
            part
            for child in value.values()
            for part in _collect_nested_strings(child)
        ]
    if isinstance(value, (list, tuple)):
        return [
            part
            for child in value
            for part in _collect_nested_strings(child)
        ]
    return []


def _quest_name_pattern(names, suffix: str) -> list[tuple[str, re.Pattern]]:
    """Build one safe regex per canonical mapping key.

    Some mapping files store keys as 'Flying', while others may store
    'Flying-type'. Both should match the visible quest phrase 'Flying-type'.
    """
    compiled = []
    for canonical in names:
        base = str(canonical).strip()
        if not base or base == "???":
            continue
        if suffix == "type":
            base = re.sub(r"\s*-?\s*type$", "", base, flags=re.IGNORECASE).strip()
            if not base:
                continue
            pattern = re.compile(
                rf"(?<!\w){re.escape(_normalize_quest_text(base))}"
                rf"\s*-\s*type(?!\w)|(?<!\w){re.escape(_normalize_quest_text(base))}"
                rf"\s+type(?!\w)",
                re.IGNORECASE,
            )
        else:
            pattern = re.compile(
                rf"(?<!\w){re.escape(_normalize_quest_text(base))}\s+region(?!\w)",
                re.IGNORECASE,
            )
        compiled.append((canonical, pattern))
    return compiled


def parse_quest_pings(message: discord.Message):
    """Extract type/region quests and remaining counts from a Discord message."""
    text_parts = []
    seen_parts = set()

    def add_text(value):
        if isinstance(value, str) and value and value not in seen_parts:
            seen_parts.add(value)
            text_parts.append(value)

    # Message content is usually empty for Pokétwo embeds, but include it.
    add_text(getattr(message, "content", None))

    source_messages = [message]
    snapshots = getattr(message, "message_snapshots", None) or []
    source_messages.extend(snapshots)

    for source_message in source_messages:
        add_text(getattr(source_message, "content", None))
        for component in getattr(source_message, "components", []) or []:
            for part in _collect_nested_strings(
                component.to_dict() if hasattr(component, "to_dict") else vars(component)
            ):
                add_text(part)
        for embed in getattr(source_message, "embeds", []) or []:
            for part in _collect_embed_text(embed):
                add_text(part)

        # Newer Discord message payloads can carry visible text in components
        # without an Embed object.
        if hasattr(source_message, "to_dict"):
            try:
                raw_message = source_message.to_dict()
                for part in _collect_nested_strings(
                    raw_message.get("components", [])
                ):
                    add_text(part)
            except Exception:
                LOGGER.exception("Failed to read raw message components")

    text = _normalize_quest_text("\n".join(text_parts))
    text = re.sub(r"[ ]{2,}", " ", text)

    type_patterns = _quest_name_pattern(ALL_TYPES, "type")
    region_patterns = _quest_name_pattern(ALL_REGIONS, "region")

    found_types = set()
    found_regions = set()
    type_requirements = {}
    region_requirements = {}

    for line in text.splitlines():
        progress_match = re.search(r"\((\d+)\s*/\s*(\d+)\)", line)
        remaining = None
        if progress_match:
            completed = int(progress_match.group(1))
            total = int(progress_match.group(2))
            remaining = max(0, total - completed)

        line_types = {
            canonical for canonical, pattern in type_patterns if pattern.search(line)
        }
        line_regions = {
            canonical for canonical, pattern in region_patterns if pattern.search(line)
        }

        found_types.update(line_types)
        found_regions.update(line_regions)

        if remaining is not None:
            for quest_type in line_types:
                type_requirements[quest_type] = max(
                    remaining, type_requirements.get(quest_type, 0)
                )
            for region in line_regions:
                region_requirements[region] = max(
                    remaining, region_requirements.get(region, 0)
                )

    if not found_types and not found_regions:
        LOGGER.warning(
            "Quest parser found no type/region quests. message_id=%s author_id=%s "
            "embed_count=%s text_parts=%s extracted_text=%r available_types=%s "
            "available_regions=%s",
            getattr(message, "id", None),
            getattr(getattr(message, "author", None), "id", None),
            len(getattr(message, "embeds", []) or []),
            len(text_parts),
            text[:12000],
            ALL_TYPES,
            ALL_REGIONS,
        )
    else:
        LOGGER.info(
            "Quest parser success. message_id=%s types=%s regions=%s "
            "type_requirements=%s region_requirements=%s",
            getattr(message, "id", None),
            sorted(found_types),
            sorted(found_regions),
            type_requirements,
            region_requirements,
        )

    return found_types, found_regions, type_requirements, region_requirements


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
            replied_message = reference.resolved
            if not isinstance(replied_message, discord.Message):
                replied_message = await ctx.channel.fetch_message(reference.message_id)
            LOGGER.debug(
                "Retrieved replied message id=%s embeds=%d components=%d "
                "snapshots=%d content_length=%d",
                replied_message.id,
                len(replied_message.embeds),
                len(getattr(replied_message, "components", []) or []),
                len(getattr(replied_message, "message_snapshots", []) or []),
                len(replied_message.content or ""),
            )
        except discord.HTTPException:
            return await ctx.send("❌ I could not read the replied-to message.")

        (
            new_types,
            new_regions,
            type_requirements,
            region_requirements,
        ) = parse_quest_pings(replied_message)
        if not new_types and not new_regions:
            LOGGER.warning(
                "m!ev parse returned no matches. replied_message_id=%s embeds=%d "
                "content_length=%d. Check the 'Quest parser found no...' log above.",
                getattr(replied_message, "id", None),
                len(getattr(replied_message, "embeds", []) or []),
                len(getattr(replied_message, "content", None) or ""),
            )
            return await ctx.send(
                "❌ I could not find any type or region quests in that message. "
                "Check Mimi's logs for quest-parser diagnostics."
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
