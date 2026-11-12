import json
import logging
import re
import asyncio
import csv
import os
from copy import deepcopy
from pathlib import Path
import discord
from discord.ext import commands
from database import db
from functions import (
    GRIND_SUPPORT_SERVER_IDS,
    get_grind_support_users,
    is_suspended,
    limit_quest_ping_users,
    refresh_tp_qp_cache,
    set_quest_requirements,
)

LOGGER = logging.getLogger(__name__)
QUEST_COMPLETION_BOT_ID = 716390085896962058
QUEST_DATA_FILE = "quest_data.json"
TYPE_CHANNELS_FILE = Path("type_channels.json")
_QUEST_STATE_LOCK = asyncio.Lock()
_QUEST_FILE_LOCK = asyncio.Lock()

# Load type and region JSON mappings
with open("typing.json", "r", encoding="utf-8") as f:
    TYPING_DATA = json.load(f)

with open("region.json", "r", encoding="utf-8") as f:
    REGION_DATA = json.load(f)

with open("rare.json", "r", encoding="utf-8") as f:
    RARE_POKEMON = {
        name.casefold().strip()
        for names in json.load(f).values()
        for name in names
    }

with open("regional.json", "r", encoding="utf-8") as f:
    REGIONAL_POKEMON = {
        name.casefold().strip()
        for names in json.load(f).values()
        for name in names
    }

with open("gigantamax.json", "r", encoding="utf-8") as f:
    GIGANTAMAX_POKEMON = {
        name.casefold().strip()
        for names in json.load(f).values()
        for name in names
    }

with open("eeveelutions_paradox.json", "r", encoding="utf-8") as f:
    _EEVEELUTIONS_PARADOX_DATA = json.load(f)
PARADOX_POKEMON = {
    name.casefold().strip()
    for name in _EEVEELUTIONS_PARADOX_DATA.get("paradox", [])
}

with open("pokemon.csv", "r", encoding="utf-8-sig", newline="") as f:
    UNKNOWN_GENDER_POKEMON = {
        row["name.en"].casefold().strip()
        for row in csv.DictReader(f)
        if row.get("gender_rate") == "-1"
        and row.get("enabled") == "1"
        and row.get("catchable") == "1"
        and row.get("name.en")
        and row["name.en"].casefold().strip() not in (
            RARE_POKEMON
            | REGIONAL_POKEMON
            | GIGANTAMAX_POKEMON
            | PARADOX_POKEMON
        )
    }

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


def parse_event_quests(message: discord.Message) -> tuple[list[dict], list[str]]:
    """Return catchable type/region quests and descriptions of other quests."""
    text_parts = []
    for source_message in [message, *(getattr(message, "message_snapshots", None) or [])]:
        if getattr(source_message, "content", None):
            text_parts.append(source_message.content)
        for embed in getattr(source_message, "embeds", []) or []:
            text_parts.extend(_collect_embed_text(embed))
        for component in getattr(source_message, "components", []) or []:
            text_parts.extend(_collect_nested_strings(
                component.to_dict() if hasattr(component, "to_dict") else vars(component)
            ))

    type_patterns = _quest_name_pattern(ALL_TYPES, "type")
    region_patterns = _quest_name_pattern(ALL_REGIONS, "region")
    quests = []
    non_catch = []
    for raw_line in _normalize_quest_text("\n".join(text_parts)).splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip(" -*")
        progress = re.search(r"\((\d+)\s*/\s*(\d+)\)", line)
        if not progress:
            continue
        code_match = re.match(r"([a-z]\d+)\.\s*(.+?)\s*\(\d+\s*/\s*\d+\)", line)
        if not code_match:
            continue
        code, description = code_match.groups()
        remaining = max(0, int(progress.group(2)) - int(progress.group(1)))
        line_types = [name for name, pattern in type_patterns if pattern.search(line)]
        line_regions = [name for name, pattern in region_patterns if pattern.search(line)]
        if "catch" not in description.casefold():
            non_catch.append(description)
            continue
        if re.search(r"\bunknown\s+gender\b", description, re.IGNORECASE):
            quests.append({
                "code": code,
                "description": description,
                "kind": "unknown_gender",
                "name": "unknown gender",
                "remaining": remaining,
            })
            continue
        for kind, names in (("type", line_types), ("region", line_regions)):
            for name in names:
                quests.append({
                    "code": code,
                    "description": description,
                    "kind": kind,
                    "name": name,
                    "remaining": remaining,
                })
    return quests, non_catch


def remove_completed_quests(state: dict, quest_code: str) -> bool:
    """Remove every stored quest with a completed Bingo code, active or queued."""
    quests = state.get("quests", [])
    remaining_quests = [
        quest for quest in quests
        if quest.get("code", "").casefold() != quest_code.casefold()
    ]
    if len(remaining_quests) == len(quests):
        return False
    state["quests"] = remaining_quests
    return True


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

class QuestNumberModal(discord.ui.Modal, title="Change Number of Quest Pings"):
    amount = discord.ui.TextInput(
        label="Quest pings at once",
        placeholder="Type 0 for all",
        min_length=1,
        max_length=6,
    )

    def __init__(self, cog, server_id: int, user_id: int):
        super().__init__()
        self.cog = cog
        self.server_id = server_id
        self.user_id = user_id

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message(
                "❌ You cannot use this menu!", ephemeral=True
            )
        try:
            amount = int(str(self.amount.value).strip())
            if amount < 0:
                raise ValueError
        except ValueError:
            return await interaction.response.send_message(
                "❌ Type a whole number greater than or equal to 0.", ephemeral=True
            )

        settings = await self.cog._load_quest_settings(self.server_id, self.user_id)
        settings["max_active"] = amount
        await self.cog._save_quest_settings(self.server_id, self.user_id, settings)
        await self.cog._sync_active_quest_pings(self.server_id, self.user_id)
        view = QuestSettingsView(self.cog, self.server_id, self.user_id, settings)
        await interaction.response.edit_message(embed=view.build_embed(), view=view)


class QuestSettingsView(discord.ui.View):
    def __init__(self, cog, server_id: int, user_id: int, settings: dict):
        super().__init__(timeout=180)
        self.cog = cog
        self.server_id = server_id
        self.user_id = user_id
        self.settings = settings

    def build_embed(self) -> discord.Embed:
        amount = self.settings.get("max_active", 1)
        non_catch = self.settings.get("non_catch", "before")
        embed = discord.Embed(title="Event Quest Settings", color=discord.Color.purple())
        embed.description = (
            f"**No. of Quest Pings at once** `{'All' if amount == 0 else amount}`\n"
            f"**Non-Catch Quests to be completed** `{non_catch.capitalize()}`"
        )
        embed.set_footer(text="These settings are used when you reply to a quest embed with m!ev.")
        return embed

    @discord.ui.button(label="Change Number", style=discord.ButtonStyle.primary)
    async def change_number(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message(
                "❌ You cannot use this menu!", ephemeral=True
            )
        await interaction.response.send_modal(
            QuestNumberModal(self.cog, self.server_id, self.user_id)
        )

    @discord.ui.button(label="Change Non-Catch Order", style=discord.ButtonStyle.secondary)
    async def change_non_catch(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message(
                "❌ You cannot use this menu!", ephemeral=True
            )
        self.settings["non_catch"] = (
            "after" if self.settings.get("non_catch", "before") == "before" else "before"
        )
        await self.cog._save_quest_settings(self.server_id, self.user_id, self.settings)
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

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
    async def event_pings_cmd(self, ctx, action: str = ""):
        """Configure event quest pings or start them from a replied-to quest embed."""
        print(
            f"[m!ev] command received guild={ctx.guild.id} "
            f"user={ctx.author.id} action={action!r} "
            f"has_reference={ctx.message.reference is not None}"
        )
        if is_suspended(ctx.author):
            return await ctx.send(
                "❌ You are suspended from using Mimi and cannot add pings."
            )

        if action.casefold() == "clear":
            if await self._grind_support_enabled(ctx.guild.id, ctx.author.id):
                return await self._clear_grind_support_event_pings(ctx)
            return await self._clear_event_pings(ctx)
        if action:
            return await ctx.send("❌ Use `m!ev clear` or reply to a quest embed.")

        reference = ctx.message.reference
        if reference is None or reference.message_id is None:
            if await self._grind_support_enabled(ctx.guild.id, ctx.author.id):
                return await ctx.send(
                    "❌ Reply to a quest embed to start grind-support event pings."
                )
            settings = await self._load_quest_settings(ctx.guild.id, ctx.author.id)
            view = QuestSettingsView(self, ctx.guild.id, ctx.author.id, settings)
            return await ctx.send(embed=view.build_embed(), view=view)

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

        quests, non_catch = parse_event_quests(replied_message)
        if await self._grind_support_enabled(ctx.guild.id, ctx.author.id):
            return await self._start_grind_support_event(ctx, quests, non_catch)
        settings = await self._load_quest_settings(ctx.guild.id, ctx.author.id)
        if settings.get("non_catch", "before") == "before" and non_catch:
            return await ctx.send(
                "Complete your Non-Catchable Quests\n\n"
                + "\n".join(f"• {description}" for description in non_catch)
                + "\n\nThen use `m!ev` again to start."
            )
        if not quests:
            return await ctx.send("❌ I could not find any catchable type or region quests in that message.")

        previous_state = await self._load_quest_state(ctx.guild.id, ctx.author.id)
        current_pings = await db.fetchrow(
            "SELECT type_pings, region_pings FROM tp_qp WHERE serverid = $1 AND userid = $2",
            ctx.guild.id,
            ctx.author.id,
        )
        base_types = parse_user_enabled_keys(
            current_pings["type_pings"] if current_pings else None, "types"
        )
        base_regions = parse_user_enabled_keys(
            current_pings["region_pings"] if current_pings else None, "regions"
        )
        unknown_gender_added = []
        if any(quest["kind"] == "unknown_gender" for quest in quests):
            existing_collection = {
                row["pokemon_name"]
                for row in await db.fetch(
                    """
                    SELECT pokemon_name FROM collection_pings
                    WHERE serverid = $1 AND userid = $2 AND pokemon_name = ANY($3::text[])
                    """,
                    ctx.guild.id,
                    ctx.author.id,
                    sorted(UNKNOWN_GENDER_POKEMON),
                )
            }
            unknown_gender_added = sorted(UNKNOWN_GENDER_POKEMON - existing_collection)
        state = {
            "quests": [quest for quest in quests if quest["remaining"] > 0],
            "active": [],
            "_synced_active": previous_state.get("_synced_active", []),
            "_base_types": sorted(base_types),
            "_base_regions": sorted(base_regions),
            "unknown_gender_collection_added": unknown_gender_added,
        }
        await self._save_quest_state(ctx.guild.id, ctx.author.id, state)
        unknown_gender_quest = any(
            quest["kind"] == "unknown_gender" for quest in state["quests"]
        )
        if unknown_gender_quest:
            await db.execute(
                """
                INSERT INTO collection_pings (serverid, userid, pokemon_name)
                SELECT $1, $2, UNNEST($3::text[])
                ON CONFLICT (serverid, userid, pokemon_name) DO NOTHING
                """,
                ctx.guild.id,
                ctx.author.id,
                sorted(UNKNOWN_GENDER_POKEMON),
            )
            await refresh_collection_cache(db)
        await self._sync_active_quest_pings(ctx.guild.id, ctx.author.id)
        if ctx.guild.id in GRIND_SUPPORT_SERVER_IDS:
            type_requirements = {}
            region_requirements = {}
            for quest in state["quests"]:
                if quest["kind"] not in {"type", "region"}:
                    continue
                target = (
                    type_requirements
                    if quest["kind"] == "type"
                    else region_requirements
                )
                target[quest["name"]] = max(
                    quest["remaining"], target.get(quest["name"], 0)
                )
            await set_quest_requirements(
                ctx.guild.id,
                ctx.author.id,
                type_requirements,
                region_requirements,
                replace_types=True,
            )

        active = await self._load_quest_state(ctx.guild.id, ctx.author.id)
        active_names = len(active.get("active", []))
        response = f"✅ Stored {len(state['quests'])} catch quests and started {active_names} quest ping(s)."
        if settings.get("non_catch", "before") == "after" and non_catch:
            response += (
                "\n\nComplete your Non-Catchable Quests\n\n"
                + "\n".join(f"• {description}" for description in non_catch)
            )
        await ctx.send(response)

    async def _grind_support_enabled(self, server_id: int, user_id: int) -> bool:
        if server_id not in GRIND_SUPPORT_SERVER_IDS:
            return False
        return user_id in await get_grind_support_users(db, server_id)

    async def _start_grind_support_event(self, ctx, quests, non_catch):
        print(
            f"[m!ev] starting grind-support event guild={ctx.guild.id} "
            f"user={ctx.author.id} quests={len(quests)}"
        )
        type_requirements = {}
        region_requirements = {}
        new_types = set()
        new_regions = set()
        for quest in quests:
            if quest["kind"] == "type":
                new_types.add(quest["name"])
                type_requirements[quest["name"]] = max(
                    quest["remaining"],
                    type_requirements.get(quest["name"], 0),
                )
            elif quest["kind"] == "region":
                new_regions.add(quest["name"])
                region_requirements[quest["name"]] = max(
                    quest["remaining"],
                    region_requirements.get(quest["name"], 0),
                )
        if not new_types and not new_regions:
            return await ctx.send("❌ No type or region quests were found for grind support.")
        await db.execute(
            """
            INSERT INTO tp_qp (serverid, userid, type_pings, region_pings)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (serverid, userid)
            DO UPDATE SET type_pings = EXCLUDED.type_pings,
                          region_pings = EXCLUDED.region_pings
            """,
            ctx.guild.id,
            ctx.author.id,
            build_ping_payload(new_types, "types", get_pokemons_for_types),
            build_ping_payload(new_regions, "regions", get_pokemons_for_regions),
        )
        await refresh_tp_qp_cache(db)
        await set_quest_requirements(
            ctx.guild.id,
            ctx.author.id,
            type_requirements,
            region_requirements,
            replace_types=True,
        )
        backfilled = await self._notify_existing_grind_support_channels(
            ctx,
            type_requirements,
            region_requirements,
        )
        await ctx.send(
            "✅ Grind support event pings started — "
            f"{len(new_types)} type and {len(new_regions)} region quest(s)."
            + (f" Sent {backfilled} quota-limited channel ping(s)." if backfilled else "")
        )

    async def _notify_existing_grind_support_channels(
        self,
        ctx,
        type_requirements: dict[str, int],
        region_requirements: dict[str, int],
    ) -> int:
        """Ping saved type/region channels, capped by each quest's remaining count."""
        async with _QUEST_STATE_LOCK:
            channel_data = {
                str(name).casefold(): saved_ids
                for name, saved_ids in (
                    await asyncio.to_thread(self._read_type_channel_data)
                ).items()
            }
            backfilled = 0

            for kind, requirements in (
                ("type", type_requirements),
                ("region", region_requirements),
            ):
                for name, needed in requirements.items():
                    saved_ids = channel_data.get(name, [])
                    if not isinstance(saved_ids, list):
                        continue

                    sent = 0
                    for channel_id in dict.fromkeys(saved_ids):
                        if sent >= needed:
                            break
                        try:
                            channel = ctx.guild.get_channel(int(channel_id))
                        except (TypeError, ValueError):
                            continue
                        if channel is None:
                            continue
                        if kind == "type":
                            type_users, _ = await limit_quest_ping_users(
                                ctx.guild.id,
                                {ctx.author.id},
                                db,
                                [ctx.author.id],
                                [],
                                [name],
                                [],
                                require_matching_quest=True,
                            )
                        else:
                            _, region_users = await limit_quest_ping_users(
                                ctx.guild.id,
                                {ctx.author.id},
                                db,
                                [],
                                [ctx.author.id],
                                [],
                                [name],
                                require_matching_quest=True,
                            )
                        if not (type_users if kind == "type" else region_users):
                            break

                        try:
                            await channel.send(ctx.author.mention)
                        except (discord.Forbidden, discord.HTTPException):
                            LOGGER.warning(
                                "Could not send %s quest ping in channel %s",
                                kind,
                                channel.id,
                            )
                            continue

                        sent += 1
                        backfilled += 1
                        print(
                            f"[m!ev] sent {kind} ping name={name} "
                            f"channel={channel.id} count={sent}/{needed}"
                        )

            return backfilled

    @staticmethod
    def _read_type_channel_data():
        try:
            with TYPE_CHANNELS_FILE.open("r", encoding="utf-8") as file:
                data = json.load(file)
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        return data

    async def _notify_existing_grind_support_channels(
        self,
        ctx,
        type_names: set[str],
        region_names: set[str],
    ):
        channel_data = await asyncio.to_thread(self._read_type_channel_data)
        channels_by_name = {}

        for name in type_names | region_names:
            channel_ids = channel_data.get(name, [])
            if not isinstance(channel_ids, list):
                continue
            for channel_id in channel_ids:
                try:
                    channel = ctx.guild.get_channel(int(channel_id))
                except (TypeError, ValueError):
                    continue
                if channel is None:
                    continue
                channels_by_name.setdefault(name, []).append(channel)

        for name in (*sorted(type_names), *sorted(region_names)):
            for channel in channels_by_name.get(name, []):
                try:
                    await channel.send(ctx.author.mention)
                except (discord.Forbidden, discord.HTTPException):
                    LOGGER.warning(
                        "Could not send grind-support event ping to channel %s",
                        channel.id,
                    )

    async def _clear_grind_support_event_pings(self, ctx):
        await db.execute(
            """
            INSERT INTO tp_qp (serverid, userid, type_pings, region_pings)
            VALUES ($1, $2, NULL, NULL)
            ON CONFLICT (serverid, userid)
            DO UPDATE SET type_pings = NULL, region_pings = NULL
            """,
            ctx.guild.id,
            ctx.author.id,
        )
        await refresh_tp_qp_cache(db)
        await set_quest_requirements(
            ctx.guild.id, ctx.author.id, {}, {}, replace_types=True
        )
        await ctx.send("✅ Cleared your grind-support event pings.")

    async def _clear_event_pings(self, ctx):
        async with _QUEST_STATE_LOCK:
            state = await self._load_quest_state(ctx.guild.id, ctx.author.id)
            if (
                not state.get("quests")
                and not state.get("_synced_active")
                and not state.get("unknown_gender_collection_added")
            ):
                return await ctx.send("ℹ️ You have no stored event quests or event pings.")

            base_types = set(state.get("_base_types", []))
            base_regions = set(state.get("_base_regions", []))
            if "_base_types" not in state:
                for quest in state.get("_synced_active", []):
                    if quest.get("kind") == "type":
                        base_types.discard(quest.get("name"))
                    elif quest.get("kind") == "region":
                        base_regions.discard(quest.get("name"))

            added_collection = state.get("unknown_gender_collection_added", [])
            if added_collection:
                await db.execute(
                    """
                    DELETE FROM collection_pings
                    WHERE serverid = $1 AND userid = $2 AND pokemon_name = ANY($3::text[])
                    """,
                    ctx.guild.id,
                    ctx.author.id,
                    added_collection,
                )
                await refresh_collection_cache(db)

            await db.execute(
                """
                INSERT INTO tp_qp (serverid, userid, type_pings, region_pings)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (serverid, userid)
                DO UPDATE SET type_pings = EXCLUDED.type_pings,
                              region_pings = EXCLUDED.region_pings
                """,
                ctx.guild.id,
                ctx.author.id,
                build_ping_payload(base_types, "types", get_pokemons_for_types),
                build_ping_payload(base_regions, "regions", get_pokemons_for_regions),
            )
            await refresh_tp_qp_cache(db)
            await self._save_quest_state(ctx.guild.id, ctx.author.id, {})
            if ctx.guild.id in GRIND_SUPPORT_SERVER_IDS:
                await set_quest_requirements(
                    ctx.guild.id, ctx.author.id, {}, {}, replace_types=True
                )
        await ctx.send("✅ Cleared your event quests and event-added pings.")

    async def _load_quest_settings(self, server_id: int, user_id: int) -> dict:
        data = await self._load_quest_data()
        settings = data.get(self._quest_user_key(server_id, user_id), {}).get("settings", {})
        if not isinstance(settings, dict):
            settings = {}
        try:
            max_active = max(0, int(settings.get("max_active", 1)))
        except (TypeError, ValueError):
            max_active = 1
        return {
            "max_active": max_active,
            "non_catch": settings.get("non_catch", "before")
            if settings.get("non_catch") in {"before", "after"} else "before",
        }

    async def _save_quest_settings(self, server_id: int, user_id: int, settings: dict):
        async with _QUEST_FILE_LOCK:
            data = self._read_quest_data()
            entry = data.setdefault(self._quest_user_key(server_id, user_id), {})
            entry["settings"] = settings
            self._write_quest_data(data)

    async def _load_quest_state(self, server_id: int, user_id: int) -> dict:
        data = await self._load_quest_data()
        state = data.get(self._quest_user_key(server_id, user_id), {}).get("state", {})
        return state if isinstance(state, dict) else {}

    async def _save_quest_state(self, server_id: int, user_id: int, state: dict):
        async with _QUEST_FILE_LOCK:
            data = self._read_quest_data()
            entry = data.setdefault(self._quest_user_key(server_id, user_id), {})
            entry["state"] = state
            self._write_quest_data(data)

    @staticmethod
    def _quest_user_key(server_id: int, user_id: int) -> str:
        return f"{server_id}:{user_id}"

    async def _load_quest_data(self) -> dict:
        async with _QUEST_FILE_LOCK:
            return self._read_quest_data()

    @staticmethod
    def _read_quest_data() -> dict:
        if not os.path.exists(QUEST_DATA_FILE):
            return {}
        try:
            with open(QUEST_DATA_FILE, "r", encoding="utf-8") as file:
                data = json.load(file)
        except json.JSONDecodeError:
            LOGGER.exception("Quest data file contains invalid JSON: %s", QUEST_DATA_FILE)
            return {}
        if not isinstance(data, dict):
            LOGGER.error("Quest data file must contain a JSON object: %s", QUEST_DATA_FILE)
            return {}
        return data

    @staticmethod
    def _write_quest_data(data: dict):
        temporary_file = f"{QUEST_DATA_FILE}.tmp"
        with open(temporary_file, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=2)
            file.write("\n")
        os.replace(temporary_file, QUEST_DATA_FILE)

    async def _sync_active_quest_pings(self, server_id: int, user_id: int):
        async with _QUEST_STATE_LOCK:
            state = await self._load_quest_state(server_id, user_id)
            settings = await self._load_quest_settings(server_id, user_id)
            pending = [quest for quest in state.get("quests", []) if quest.get("remaining", 0) > 0]
            max_active = settings["max_active"]
            active = pending if max_active == 0 else pending[:max_active]
            state["quests"] = pending
            state["active"] = active
            await self._save_quest_state(server_id, user_id, state)

            row = await db.fetchrow(
                "SELECT type_pings, region_pings FROM tp_qp WHERE serverid = $1 AND userid = $2",
                server_id, user_id,
            )
            type_names = parse_user_enabled_keys(row["type_pings"] if row else None, "types")
            region_names = parse_user_enabled_keys(row["region_pings"] if row else None, "regions")
            old_active = state.get("_synced_active", [])
            for quest in old_active:
                if quest["kind"] == "type":
                    type_names.discard(quest["name"])
                elif quest["kind"] == "region":
                    region_names.discard(quest["name"])
            for quest in active:
                if quest["kind"] == "type":
                    type_names.add(quest["name"])
                elif quest["kind"] == "region":
                    region_names.add(quest["name"])
            state["_synced_active"] = deepcopy(active)
            await self._save_quest_state(server_id, user_id, state)
            await db.execute(
                """
                INSERT INTO tp_qp (serverid, userid, type_pings, region_pings)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (serverid, userid)
                DO UPDATE SET type_pings = EXCLUDED.type_pings, region_pings = EXCLUDED.region_pings
                """,
                server_id, user_id,
                build_ping_payload(type_names, "types", get_pokemons_for_types),
                build_ping_payload(region_names, "regions", get_pokemons_for_regions),
            )
            await refresh_tp_qp_cache(db)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.id != QUEST_COMPLETION_BOT_ID or not message.reference:
            return
        reference = message.reference.resolved
        if reference is None:
            try:
                reference = await message.channel.fetch_message(message.reference.message_id)
            except discord.HTTPException:
                return
        user = getattr(reference, "author", None)
        guild = message.guild
        if user is None or guild is None:
            return
        completion_text = message.content or ""
        for embed in getattr(message, "embeds", []) or []:
            completion_text += "\n" + "\n".join(_collect_embed_text(embed))
        match = re.search(
            r"completed bingo quest\s+\*\*([a-z]\d+)\*\*\s*\(([^)]+)\)",
            completion_text,
            re.IGNORECASE,
        )
        if not match:
            return
        code = match.group(1).casefold()
        async with _QUEST_STATE_LOCK:
            state = await self._load_quest_state(guild.id, user.id)
            if not remove_completed_quests(state, code):
                return
            await self._save_quest_state(guild.id, user.id, state)
        await self._sync_active_quest_pings(guild.id, user.id)


async def setup(bot):
    await bot.add_cog(TypeRegionPings(bot))
