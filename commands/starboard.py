import re
from pathlib import Path

import discord
from discord.ext import commands

from database import db
from functions import set_only_ping_enabled


POKETWO_BOT_ID = 716390085896962058
OWNER_ID = 760720549092917248
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


async def setup(bot: commands.Bot):
    await bot.add_cog(Starboard(bot))
