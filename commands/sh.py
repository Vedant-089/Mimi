import json
import logging
from pathlib import Path

import discord
from discord.ext import commands

from functions import ensure_ping_tables, is_suspended, refresh_hunt_cache, resolve_alias

with open("forms.json", "r", encoding="utf-8") as f:
    FORMS_BY_BASE = {
        base.lower().strip(): forms
        for base, forms in json.load(f).items()
    }

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

    async def set_shiny_hunt(self, user_id: int, server_id: int, pokemon: str):
        query = """
            INSERT INTO server_shiny_hunt (serverid, userid, pokemon_name)
            VALUES ($1, $2, $3)
            ON CONFLICT (serverid, userid) DO UPDATE
            SET pokemon_name = EXCLUDED.pokemon_name;
        """
        status = await self.bot.db.execute(query, server_id, user_id, pokemon)
        await refresh_hunt_cache(self.bot.db, [guild.id for guild in self.bot.guilds])
        log.info("DB status after setting server hunt for %s in %s → %s: %s", user_id, server_id, pokemon, status)

    async def set_global_shiny_hunt(self, user_id: int, pokemon: str):
        query = """
            INSERT INTO shiny_hunt (userid, pokemon_name)
            VALUES ($1, $2)
            ON CONFLICT (userid) DO UPDATE
            SET pokemon_name = EXCLUDED.pokemon_name;
        """
        status = await self.bot.db.execute(query, user_id, pokemon)
        await refresh_hunt_cache(self.bot.db, [guild.id for guild in self.bot.guilds])
        log.info("DB status after setting global hunt for %s → %s: %s", user_id, pokemon, status)

    # ---------------------------------------------------------------------
    # Command
    # ---------------------------------------------------------------------

    async def handle_shiny_hunt(self, ctx: commands.Context, pokemon: str | None, global_scope: bool):
        log.debug("Command invoked by %s (%s) with arg: %s", ctx.author, ctx.author.id, pokemon)
        if pokemon is not None and is_suspended(ctx.author):
            await ctx.reply("❌ You are suspended from using Mimi and cannot add pings.")
            return

        if global_scope:
            current_query = "SELECT pokemon_name FROM shiny_hunt WHERE userid = $1;"
            current_args = (ctx.author.id,)
        else:
            current_query = "SELECT pokemon_name FROM server_shiny_hunt WHERE serverid = $1 AND userid = $2;"
            current_args = (ctx.guild.id, ctx.author.id)

        if pokemon is None:
            row = await self.bot.db.fetchrow(current_query, *current_args)

            if row and row['pokemon_name']:
                current_hunt = row['pokemon_name']
                display_name = (
                    current_hunt[4:].title() + " (all)"
                    if current_hunt.lower().startswith("all ")
                    else current_hunt.title()
                )
                await ctx.reply(f"🔍 You're currently hunting **{display_name}**!")
            else:
                await ctx.reply("❌ You are not hunting any Pokémon currently.")
            return

        key = str(pokemon).lower().strip()
        if key.startswith("normal "):
            key = key[7:].strip()

        if key in ["none", "reset", "clear", "remove", "null", "nothing"]:
            if global_scope:
                reset_query = """
                    INSERT INTO shiny_hunt (userid, pokemon_name)
                    VALUES ($1, NULL)
                    ON CONFLICT (userid) DO UPDATE SET pokemon_name = NULL;
                """
                reset_args = (ctx.author.id,)
            else:
                reset_query = """
                    INSERT INTO server_shiny_hunt (serverid, userid, pokemon_name)
                    VALUES ($1, $2, NULL)
                    ON CONFLICT (serverid, userid) DO UPDATE SET pokemon_name = NULL;
                """
                reset_args = (ctx.guild.id, ctx.author.id)

            await self.bot.db.execute(reset_query, *reset_args)
            await refresh_hunt_cache(self.bot.db, [guild.id for guild in self.bot.guilds])
            await ctx.reply("🧹 Your shiny hunt has been **reset**. You're now hunting nothing.")
            return

        resolved_key = resolve_alias(key)

        if key.startswith("all "):
            target = resolve_alias(key[4:].strip())
            if target not in FORMS_BY_BASE or target not in VALID_POKEMON:
                await ctx.reply(f"❌ **{pokemon}** isn't a Pokémon with recognised forms.")
                return

            resolved_key = f"all {target}"

        elif resolved_key not in VALID_POKEMON:
            log.debug("%s (%s) is not in whitelist", key, resolved_key)
            await ctx.reply(f"❌ **{pokemon}** isn't on the recognised Pokémon list.")
            return

        try:
            if global_scope:
                await self.set_global_shiny_hunt(ctx.author.id, resolved_key)
            else:
                await self.set_shiny_hunt(ctx.author.id, ctx.guild.id, resolved_key)
            display_name = resolved_key[4:].title() + " (all)" if resolved_key.startswith("all ") else resolved_key.title()
            await ctx.reply(f"✅ Shiny hunt updated to **{display_name}**!")
        except Exception as e:
            log.exception("Failed to set shiny hunt for %s → %s", ctx.author.id, resolved_key)
            await ctx.reply("⚠️ An error occurred while saving your shiny hunt. Please try again later.")

    @commands.command(name="sh", aliases=["shinyhunt"])
    @commands.guild_only()
    async def shiny_hunt(self, ctx: commands.Context, *, pokemon=None):
        await self.handle_shiny_hunt(ctx, pokemon, global_scope=False)

    @commands.command(name="globalshinyhunt", aliases=["gsh"])
    async def global_shiny_hunt(self, ctx: commands.Context, *, pokemon=None):
        await self.handle_shiny_hunt(ctx, pokemon, global_scope=True)

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