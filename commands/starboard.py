import asyncio
import copy
import json
import re
from pathlib import Path

import discord
from discord.ext import commands

from database import db
from functions import set_only_ping_enabled


POKETWO_BOT_ID = 716390085896962058
OWNER_ID = 760720549092917248
STARBOARD_CONFIG_FILE = "starboard.json"
DEFAULT_GLOBAL_STARBOARD_CHANNEL_ID = 1572654305666797678
CATCH_LINK_EMOJI = "⛓️‍💥"
CATCH_RE = re.compile(
    r"^Congratulations\s+<@!?([0-9]+)>!\s+You caught a Level",
    re.IGNORECASE,
)
CATCH_DETAILS_RE = re.compile(
    r"Congratulations\s+<@!?([0-9]+)>!\s+You caught a Level\s+"
    r"([0-9]+)\s+(.+?)\s+\(([0-9]+(?:\.[0-9]+)?)%\)!",
    re.IGNORECASE | re.DOTALL,
)
GIGANTAMAX_MARKER = "gigantamax factor"
SHINY_MARKER = "these colors seem unusual"
GIGANTAMAX_EMOJI = "<:gigantamax:1558878072441151558>"
GENDER_EMOJI_RE = re.compile(r"<a?:(female|male):[0-9]+>", re.IGNORECASE)

_starboard_config = None
_starboard_config_lock = asyncio.Lock()
_starboard_write_task = None


def _default_starboard_config() -> dict:
    return {
        "global_starboard_channel_id": DEFAULT_GLOBAL_STARBOARD_CHANNEL_ID,
        "name_pings": {},
    }


def _read_starboard_config() -> dict:
    config = _default_starboard_config()
    path = Path(STARBOARD_CONFIG_FILE)
    if not path.exists():
        return config
    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError) as error:
        print(f"⚠️ Failed to read {STARBOARD_CONFIG_FILE}: {error}")
        return config
    if not isinstance(data, dict):
        return config
    channel_id = data.get("global_starboard_channel_id", config["global_starboard_channel_id"])
    try:
        config["global_starboard_channel_id"] = int(channel_id)
    except (TypeError, ValueError):
        pass
    name_pings = data.get("name_pings", {})
    if isinstance(name_pings, dict):
        config["name_pings"] = {
            str(user_id): bool(enabled)
            for user_id, enabled in name_pings.items()
        }
    return config


def _write_starboard_config(data: dict) -> None:
    temporary_file = f"{STARBOARD_CONFIG_FILE}.tmp"
    with open(temporary_file, "w", encoding="utf-8") as file:
        json.dump(data, file, separators=(",", ":"))
    Path(temporary_file).replace(STARBOARD_CONFIG_FILE)


def _ensure_starboard_config() -> dict:
    global _starboard_config
    if _starboard_config is None:
        _starboard_config = _read_starboard_config()
    return _starboard_config


def _schedule_starboard_config_write() -> None:
    global _starboard_write_task
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _write_starboard_config(_ensure_starboard_config())
        return
    if _starboard_write_task is None or _starboard_write_task.done():
        _starboard_write_task = loop.create_task(_flush_starboard_config())


async def _flush_starboard_config() -> None:
    await asyncio.sleep(0.1)
    async with _starboard_config_lock:
        snapshot = copy.deepcopy(_ensure_starboard_config())
    try:
        await asyncio.to_thread(_write_starboard_config, snapshot)
    except OSError as error:
        print(f"⚠️ Failed to write {STARBOARD_CONFIG_FILE}: {error}")


def get_global_starboard_channel_id() -> int:
    return int(_ensure_starboard_config()["global_starboard_channel_id"])


def is_starboard_name_ping_enabled(user_id: int) -> bool:
    name_pings = _ensure_starboard_config()["name_pings"]
    value = name_pings.get(str(user_id), True)
    return bool(value)


def set_starboard_name_ping_enabled(user_id: int, enabled: bool) -> None:
    config = _ensure_starboard_config()
    config["name_pings"][str(user_id)] = bool(enabled)
    _schedule_starboard_config_write()


def format_pokemon_name(name: str) -> str:
    gender_symbols = {
        "female": "♀️",
        "male": "♂️",
    }
    return GENDER_EMOJI_RE.sub(
        lambda match: gender_symbols[match.group(1).lower()],
        name,
    ).strip()


def _catch_link_view(message: discord.Message) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(
        discord.ui.Button(
            emoji=CATCH_LINK_EMOJI,
            style=discord.ButtonStyle.link,
            url=message.jump_url,
        )
    )
    return view


class Starboard(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="op")
    async def only_ping(self, ctx: commands.Context, value: str = ""):
        if ctx.author.id != OWNER_ID:
            return

        value = value.strip().casefold()
        if value not in {"true", "false"}:
            await ctx.send("Usage: `p!op true` or `p!op false`")
            return

        try:
            set_only_ping_enabled(value == "true")
        except OSError as error:
            print(f"⚠️ Failed to write {Path('only_ping.json')}: {error}")
            await ctx.send("❌ Failed to update the only-ping setting.")
            return

        state = "enabled" if value == "true" else "disabled"
        await ctx.send(f"✅ Only-ping naming is now **{state}**.")

    async def send_starboard_catch(self, message: discord.Message):
        match = CATCH_DETAILS_RE.search(message.content or "")
        if match is None or message.guild is None:
            return

        catcher_id = int(match.group(1))
        level = int(match.group(2))
        pokemon_name = format_pokemon_name(match.group(3))
        iv = float(match.group(4))
        content_lower = message.content.lower()

        if SHINY_MARKER in content_lower:
            catch_type = "shiny"
            title = "✨ Shiny Catch Detected ✨"
            color = discord.Color.gold()
            specialty_column = "shiny_starboard"
        elif GIGANTAMAX_MARKER in content_lower:
            catch_type = "gmax"
            title = f"{GIGANTAMAX_EMOJI} Gigantamax Catch Detected {GIGANTAMAX_EMOJI}"
            color = discord.Color.purple()
            specialty_column = "gmax_starboard"
        elif iv > 90:
            catch_type = "high_iv"
            title = "📈 High IV Catch Detected 📈"
            color = discord.Color.green()
            specialty_column = None
        elif iv < 10:
            catch_type = "low_iv"
            title = "📉 Low IV Catch Detected 📉"
            color = discord.Color.red()
            specialty_column = None
        else:
            return

        caught_by = (
            f"<@{catcher_id}>"
            if is_starboard_name_ping_enabled(catcher_id)
            else "Anonymous User"
        )
        embed = discord.Embed(title=title, color=color, timestamp=message.created_at)
        embed.description = (
            f"**Caught By:** {caught_by}\n"
            f"**Pokémon:** {pokemon_name}\n"
            f"**Level:** {level}\n"
            f"**IV:** {iv:.2f}%"
        )
        embed.set_footer(text=message.guild.name)
        view = _catch_link_view(message)

        local_channel_ids = await self._local_starboard_channel_ids(
            message.guild.id,
            specialty_column,
        )
        global_channel_id = get_global_starboard_channel_id()
        channel_ids = list(local_channel_ids)
        if global_channel_id not in channel_ids:
            channel_ids.append(global_channel_id)

        for channel_id in channel_ids:
            await self._send_starboard_embed(channel_id, catch_type, embed, view)

    async def _local_starboard_channel_ids(self, guild_id: int, specialty_column):
        try:
            settings = await db.fetchrow(
                f"""
                SELECT main_starboard, {specialty_column or 'NULL'} AS specialty_starboard
                FROM server_settings
                WHERE serverid = $1
                """,
                guild_id,
            )
        except Exception as error:
            print(f"⚠️ Failed to load starboard channels for {guild_id}: {error}")
            return []

        if settings is None:
            return []

        channel_ids = []
        for channel_id in (settings["main_starboard"], settings["specialty_starboard"]):
            if channel_id is not None and int(channel_id) not in channel_ids:
                channel_ids.append(int(channel_id))
        return channel_ids

    async def _send_starboard_embed(
        self,
        channel_id: int,
        catch_type: str,
        embed: discord.Embed,
        view: discord.ui.View,
    ):
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException) as error:
                print(f"⚠️ Failed to resolve {catch_type} starboard channel {channel_id}: {error}")
                channel = self.bot.get_partial_messageable(channel_id)
        try:
            await channel.send(embed=embed, view=view)
        except (discord.Forbidden, discord.HTTPException) as error:
            print(f"⚠️ Failed to send {catch_type} starboard embed to {channel_id}: {error}")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if (
            message.author.id != POKETWO_BOT_ID
            or not message.content
            or not CATCH_RE.search(message.content)
        ):
            return

        try:
            await self.send_starboard_catch(message)
        except Exception as error:
            print(f"⚠️ Failed to process starboard catch: {error}")


async def setup(bot: commands.Bot):
    await bot.add_cog(Starboard(bot))
