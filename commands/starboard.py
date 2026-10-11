import asyncio
import json
import re
from pathlib import Path

import discord
from discord.ext import commands

from database import db


POKETWO_BOT_ID = 716390085896962058
TYPE_SETTINGS_PATH = Path("type_settings.json")
TYPE_CHANNELS_PATH = Path("type_channels.json")
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


def format_pokemon_name(name: str) -> str:
    gender_symbols = {
        "female": "♀️",
        "male": "♂️",
    }
    return GENDER_EMOJI_RE.sub(
        lambda match: gender_symbols[match.group(1).lower()],
        name,
    ).strip()


class Starboard(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.type_channels_lock = asyncio.Lock()

    def is_type_enabled(self) -> bool:
        try:
            with TYPE_SETTINGS_PATH.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return bool(data.get("type", data.get("enabled", True)))
        except FileNotFoundError:
            return True
        except Exception as error:
            print(f"⚠️ Failed to read {TYPE_SETTINGS_PATH}: {error}")
            return True

    def _read_type_channel_data(self):
        if not TYPE_CHANNELS_PATH.exists():
            return {}
        try:
            with TYPE_CHANNELS_PATH.open("r", encoding="utf-8") as f:
                existing = json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}

        data = {}
        for type_name, channel_ids in existing.items():
            cleaned_ids = []
            for channel_id in channel_ids:
                try:
                    channel_int = int(channel_id)
                except (TypeError, ValueError):
                    continue
                if channel_int not in cleaned_ids:
                    cleaned_ids.append(channel_int)
            data[type_name] = cleaned_ids
        return data

    def _write_type_channel_data(self, data):
        with TYPE_CHANNELS_PATH.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)

    async def _remove_channel_from_all_types(self, channel_id: int):
        async with self.type_channels_lock:
            data = await asyncio.to_thread(self._read_type_channel_data)
            changed = False
            for type_name in list(data.keys()):
                filtered_ids = [saved_id for saved_id in data[type_name] if saved_id != channel_id]
                if len(filtered_ids) != len(data[type_name]):
                    data[type_name] = filtered_ids
                    changed = True
            if changed:
                await asyncio.to_thread(self._write_type_channel_data, data)

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

        settings = await db.fetchrow(
            f"""
            SELECT main_starboard, {specialty_column or 'NULL'} AS specialty_starboard
            FROM server_settings
            WHERE serverid = $1
            """,
            message.guild.id,
        )
        if settings is None:
            return

        channel_ids = []
        for channel_id in (settings["main_starboard"], settings["specialty_starboard"]):
            if channel_id is not None and int(channel_id) not in channel_ids:
                channel_ids.append(int(channel_id))
        if not channel_ids:
            return

        embed = discord.Embed(title=title, color=color, timestamp=message.created_at)
        embed.description = (
            f"**Caught By:** <@{catcher_id}>\n"
            f"**Pokémon:** {pokemon_name}\n"
            f"**Level:** {level}\n"
            f"**IV:** {iv:.2f}%"
        )

        for channel_id in channel_ids:
            channel = self.bot.get_channel(channel_id)
            if channel is None:
                try:
                    channel = await self.bot.fetch_channel(channel_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    continue
            try:
                await channel.send(embed=embed)
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

        if self.is_type_enabled():
            try:
                await self._remove_channel_from_all_types(message.channel.id)
            except Exception as error:
                print(f"⚠️ Failed to remove channel after catch: {error}")


async def setup(bot: commands.Bot):
    await bot.add_cog(Starboard(bot))
