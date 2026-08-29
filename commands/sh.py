import json
import logging
from pathlib import Path

import discord
from discord.ext import commands

from functions import ensure_ping_tables

# ---------------------------------------------------------------------------
# LOGGING
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s] %(name)s:%(lineno)d — %(message)s",
)
log = logging.getLogger("ShinyHunt")

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

JSON_PATH = Path("class.json")  # valid Pokémon names

# ---------------------------------------------------------------------------
# UTILS
# ---------------------------------------------------------------------------

def load_valid_pokemon():
    """Return a cached, case‑insensitive Pokémon whitelist."""
    try:
        with JSON_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        pokemon_set = {name.lower().strip() for name in data}
        log.info("Loaded %d Pokémon names from class.json", len(pokemon_set))
        return pokemon_set
    except FileNotFoundError:
        log.warning("class.json missing – accepting no Pokémon names.")
        return set()


VALID_POKEMON = load_valid_pokemon()

# ---------------------------------------------------------------------------
# COG
# ---------------------------------------------------------------------------

class ShinyHunt(commands.Cog):
    """`!sh <pokemon>` — set your shiny hunt (validated via class.json)."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ---------------------------------------------------------------------
    # DB helpers
    # ---------------------------------------------------------------------

    async def ensure_table(self):
        """Create ping tables if they do not exist."""
        log.debug("Ensuring ping tables exist …")
        await ensure_ping_tables(self.bot.db)

    async def set_shiny_hunt(self, user_id: int, pokemon: str):
        """Insert or update pokemon_name for a user and log status."""
        query = (
            """
            INSERT INTO shiny_hunt (userid, pokemon_name)
            VALUES ($1, $2)
            ON CONFLICT (userid) DO UPDATE
            SET pokemon_name = EXCLUDED.pokemon_name;
            """
        )
        status = await self.bot.db.execute(query, user_id, pokemon)
        log.info("DB status after setting hunt for %s → %s: %s", user_id, pokemon, status)

    # ---------------------------------------------------------------------
    # Command
    # ---------------------------------------------------------------------

    @commands.command(name="sh", aliases=["shinyhunt"])
    async def shiny_hunt(self, ctx: commands.Context, *, pokemon=None):
        """Register a shiny hunt if *pokemon* is on the official list, or show current hunt."""
        log.debug("Command invoked by %s (%s) with arg: %s", ctx.author, ctx.author.id, pokemon)

        # ------------------------------------------------------------
        # Case 1 — user typed just m!sh → show current hunt
        # ------------------------------------------------------------
        if pokemon is None:
            query = "SELECT pokemon_name FROM shiny_hunt WHERE userid = $1;"
            row = await self.bot.db.fetchrow(query, ctx.author.id)

            if row and row['pokemon_name']:
                await ctx.reply(f"🔍 You're currently hunting **{row['pokemon_name'].title()}**!")
            else:
                await ctx.reply("❌ You are not hunting any Pokémon currently.")
            return

        # ------------------------------------------------------------
        # Case 2 — user typed m!sh none/reset → clear shiny hunt
        # ------------------------------------------------------------
        key = str(pokemon).lower().strip()

        if key in ["none", "reset", "null", "nothing"]:
            reset_query = """
                INSERT INTO shiny_hunt (userid, pokemon_name)
                VALUES ($1, NULL)
                ON CONFLICT (userid) DO UPDATE
                SET pokemon_name = NULL;
            """

            await self.bot.db.execute(reset_query, ctx.author.id)
            await ctx.reply("🧹 Your shiny hunt has been **reset**. You're now hunting nothing.")
            return

        # ------------------------------------------------------------
        # Case 3 — normal shiny hunt set (validate Pokémon)
        # ------------------------------------------------------------
        if key not in VALID_POKEMON:
            log.debug("%s is not in whitelist", key)
            await ctx.reply(f"❌ **{pokemon}** isn't on the recognised Pokémon list.")
            return

        try:
            await self.set_shiny_hunt(ctx.author.id, key)
            await ctx.reply(f"✅ Shiny hunt updated to **{pokemon.title()}**!")
        except Exception as e:
            log.exception("Failed to set shiny hunt for %s → %s", ctx.author.id, key)
            await ctx.reply("⚠️ An error occurred while saving your shiny hunt. Please try again later.")

    # ---------------------------------------------------------------------
    # Listeners
    # ---------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_ready(self):
        # Ensure table exists once the bot is logged in and DB is ready
        await self.ensure_table()
        log.info("[ShinyHunt] table checked/created and cog ready.")

# ---------------------------------------------------------------------------
# COG SETUP (called by main.py)
# ---------------------------------------------------------------------------

async def setup(bot: commands.Bot):
    await bot.add_cog(ShinyHunt(bot))