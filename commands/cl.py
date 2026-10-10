import discord
from discord.ext import commands
from database import db
from functions import ensure_ping_tables, invalidate_collection_cache, resolve_alias
import json

with open("class.json", "r") as f:
    VALID_POKEMON = set(name.lower() for name in json.load(f).keys())

with open("rare.json", "r", encoding="utf-8") as f:
    RARE_POKEMON = {name.lower().strip() for names in json.load(f).values() for name in names}

with open("regional.json", "r", encoding="utf-8") as f:
    REGIONAL_POKEMON = {name.lower().strip() for names in json.load(f).values() for name in names}

with open("gigantamax.json", "r", encoding="utf-8") as f:
    GIGANTAMAX_POKEMON = {name.lower().strip() for names in json.load(f).values() for name in names}

with open("eeveelutions_paradox.json", "r", encoding="utf-8") as f:
    EEVEELUTIONS_PARADOX_DATA = json.load(f)
    EEVEELUTIONS_POKEMON = {name.lower().strip() for names in [EEVEELUTIONS_PARADOX_DATA.get("eeveelutions_paradox", [])] for name in names}
    PARADOX_POKEMON = {name.lower().strip() for names in [EEVEELUTIONS_PARADOX_DATA.get("paradox", [])] for name in names}

with open("forms.json", "r", encoding="utf-8") as f:
    FORMS_BY_BASE = {
        base.lower().strip(): [form.lower().strip() for form in forms]
        for base, forms in json.load(f).items()
    }

GROUP_ALIASES = {
    "rare": RARE_POKEMON,
    "rares": RARE_POKEMON,
    "regional": REGIONAL_POKEMON,
    "regionals": REGIONAL_POKEMON,
    "gigantamax": GIGANTAMAX_POKEMON,
    "gmax": GIGANTAMAX_POKEMON,
    "eeveelutions": EEVEELUTIONS_POKEMON,
    "eevos": EEVEELUTIONS_POKEMON,
    "evos": EEVEELUTIONS_POKEMON,
    "paradox": PARADOX_POKEMON,
}


def expand_collection_inputs(input_names):
    expanded = set()
    invalid = []

    for raw in input_names:
        key = raw.strip().lower()
        if not key:
            continue

        if key in GROUP_ALIASES:
            expanded.update(name for name in GROUP_ALIASES[key] if name in VALID_POKEMON)
            continue

        if key.startswith("all "):
            target = resolve_alias(key[4:].strip())
            if target in FORMS_BY_BASE:
                if target in VALID_POKEMON:
                    expanded.add(target)
                expanded.update(name for name in FORMS_BY_BASE[target] if name in VALID_POKEMON)
                continue

            if target in VALID_POKEMON:
                expanded.add(target)
                continue

            invalid.append(key[4:].strip())
            continue

        resolved = resolve_alias(key)
        if resolved in VALID_POKEMON:
            expanded.add(resolved)
        else:
            invalid.append(key)

    return sorted(expanded), invalid


def expand_global_collection_inputs(input_names):
    expanded = set()
    invalid = []

    for raw in input_names:
        key = raw.strip().lower()
        if not key:
            continue

        if key in GROUP_ALIASES or key.startswith("all "):
            invalid.append(key)
            continue

        resolved = resolve_alias(key)
        if resolved in VALID_POKEMON:
            expanded.add(resolved)
        else:
            invalid.append(key)

    return sorted(expanded), invalid


class CollectionListView(discord.ui.View):
    def __init__(self, pages):
        super().__init__(timeout=180)
        self.pages = pages
        self.current_page = 0
        self._update_buttons()

    def _update_buttons(self):
        self.children[0].disabled = self.current_page == 0
        self.children[1].disabled = self.current_page >= len(self.pages) - 1

    async def _show_page(self, interaction: discord.Interaction):
        self._update_buttons()
        await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page > 0:
            self.current_page -= 1
        await self._show_page(interaction)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page < len(self.pages) - 1:
            self.current_page += 1
        await self._show_page(interaction)

class CollectionCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        await ensure_ping_tables(db)

    @commands.group(name="gcl", invoke_without_command=True)
    async def global_collection(self, ctx):
        await ctx.send("Usage: `m!gcl add/remove <pokemon>` or `m!gcl clear`")

    @global_collection.command(name="add")
    async def global_add(self, ctx, *, names: str):
        input_names = [name.strip().lower() for name in names.split(",") if name.strip()]
        valid_names, invalid = expand_global_collection_inputs(input_names)

        if not valid_names:
            return await ctx.send("❌ No valid Pokémon names provided. Categories and `all <pokemon>` are not supported by global collection.")

        server_ids = [guild.id for guild in self.bot.guilds]
        for serverid in server_ids:
            await db.execute(
                """
                INSERT INTO collection_pings (serverid, userid, pokemon_name)
                SELECT $1, $2, UNNEST($3::text[])
                ON CONFLICT (serverid, userid, pokemon_name) DO NOTHING
                """,
                serverid, ctx.author.id, valid_names
            )
        invalidate_collection_cache()

        preview = ", ".join(name.title() for name in valid_names[:25])
        if len(valid_names) > 25:
            preview += f", ... (+{len(valid_names) - 25} more)"
        response = f"✅ Added to your collection in {len(server_ids)} servers: {preview}"
        if invalid:
            response += f"\n❌ Invalid or unsupported: {', '.join(invalid)}"
        await ctx.send(response)

    @global_collection.command(name="remove")
    async def global_remove(self, ctx, *, names: str):
        input_names = [name.strip().lower() for name in names.split(",") if name.strip()]
        valid_names, invalid = expand_global_collection_inputs(input_names)

        if not valid_names:
            return await ctx.send("❌ No valid Pokémon names provided. Categories and `all <pokemon>` are not supported by global collection.")

        server_ids = [guild.id for guild in self.bot.guilds]
        for serverid in server_ids:
            await db.execute(
                """
                DELETE FROM collection_pings
                WHERE serverid = $1 AND userid = $2 AND pokemon_name = ANY($3::text[])
                """,
                serverid, ctx.author.id, valid_names
            )
        invalidate_collection_cache()

        preview = ", ".join(name.title() for name in valid_names[:25])
        if len(valid_names) > 25:
            preview += f", ... (+{len(valid_names) - 25} more)"
        response = f"🗑️ Removed from your collection in {len(server_ids)} servers: {preview}"
        if invalid:
            response += f"\n❌ Invalid or unsupported: {', '.join(invalid)}"
        await ctx.send(response)

    @global_collection.command(name="clear")
    async def global_clear(self, ctx):
        server_ids = [guild.id for guild in self.bot.guilds]
        await db.execute(
            """
            DELETE FROM collection_pings
            WHERE userid = $1 AND serverid = ANY($2::bigint[])
            """,
            ctx.author.id, server_ids
        )
        invalidate_collection_cache()
        await ctx.send(f"🧹 Your collection has been cleared in {len(server_ids)} servers.")

    @commands.group(name="cl", invoke_without_command=True)
    @commands.guild_only()
    async def cl(self, ctx):
        await ctx.send("Usage: `m!cl add/remove/clear <pokemon>`")

    @cl.command(name="add")
    @commands.guild_only()
    async def add(self, ctx, *, names: str):
        user_id = ctx.author.id
        serverid = ctx.guild.id
        input_names = [n.strip().lower() for n in names.split(",")]
        valid_names, invalid = expand_collection_inputs(input_names)

        if not valid_names:
            return await ctx.send("❌ No valid Pokémon provided.")

        await db.execute(
            """
            INSERT INTO collection_pings (serverid, userid, pokemon_name)
            SELECT $1, $2, UNNEST($3::text[])
            ON CONFLICT (serverid, userid, pokemon_name) DO NOTHING
            """,
            serverid, user_id, valid_names
        )
        invalidate_collection_cache()

        preview = ", ".join(name.title() for name in valid_names[:25])
        if len(valid_names) > 25:
            preview += f", ... (+{len(valid_names) - 25} more)"

        response = f"✅ Added {len(valid_names)} Pokémon: {preview}"
        if invalid:
            response += f"\n❌ Invalid: {', '.join(invalid)}"
        await ctx.send(response)

    @cl.command(name="remove")
    @commands.guild_only()
    async def remove(self, ctx, *, names: str):
        user_id = ctx.author.id
        serverid = ctx.guild.id
        input_names = [n.strip().lower() for n in names.split(",")]
        valid_names, invalid = expand_collection_inputs(input_names)

        if not valid_names:
            return await ctx.send("❌ No valid Pokémon provided.")

        current_rows = await db.fetch(
            """
            SELECT pokemon_name FROM collection_pings
            WHERE serverid = $1 AND userid = $2 AND pokemon_name = ANY($3::text[])
            """,
            serverid, user_id, valid_names
        )
        removed = sorted(row["pokemon_name"] for row in current_rows)
        if not removed:
            existing = await db.fetchval(
                "SELECT 1 FROM collection_pings WHERE serverid = $1 AND userid = $2 LIMIT 1",
                serverid, user_id
            )
            if not existing:
                return await ctx.send("ℹ️ Your collection is already empty.")
            return await ctx.send("ℹ️ None of those were in your collection.")

        await db.execute(
            """
            DELETE FROM collection_pings
            WHERE serverid = $1 AND userid = $2 AND pokemon_name = ANY($3::text[])
            """,
            serverid, user_id, valid_names
        )
        invalidate_collection_cache()

        preview = ", ".join(name.title() for name in removed[:25])
        if len(removed) > 25:
            preview += f", ... (+{len(removed) - 25} more)"

        response = f"🗑️ Removed: {preview}"
        if invalid:
            response += f"\n❌ Invalid: {', '.join(invalid)}"
        await ctx.send(response)

    @cl.command(name="clear")
    @commands.guild_only()
    async def clear(self, ctx):
        user_id = ctx.author.id
        serverid = ctx.guild.id
        await db.execute(
            "DELETE FROM collection_pings WHERE userid = $1 AND serverid = $2",
            user_id, serverid
        )
        invalidate_collection_cache()
        await ctx.send("🧹 Your collection has been cleared.")

    @cl.command(name="list")
    @commands.guild_only()
    async def list_collection(self, ctx):
        user_id = ctx.author.id
        serverid = ctx.guild.id

        rows = await db.fetch(
            """
            SELECT pokemon_name FROM collection_pings
            WHERE userid = $1 AND serverid = $2
            ORDER BY pokemon_name
            """,
            user_id, serverid
        )
        if not rows:
            return await ctx.send("📭 You don't have any Pokémon in your collection.")

        collection = [row["pokemon_name"] for row in rows]
        page_size = 25
        total_pages = (len(collection) + page_size - 1) // page_size
        pages = []

        for page_index, start in enumerate(range(0, len(collection), page_size), start=1):
            page_items = collection[start:start + page_size]
            embed = discord.Embed(
                title="📦 Pokémon Collection",
                description="\n".join(page_items),
                color=discord.Color.purple(),
            )
            embed.set_footer(text=f"{ctx.author.display_name}'s collection • Page {page_index}/{total_pages}")
            pages.append(embed)

        if len(pages) == 1:
            await ctx.send(embed=pages[0])
            return

        await ctx.send(embed=pages[0], view=CollectionListView(pages))


async def setup(bot):
    await bot.add_cog(CollectionCommands(bot))