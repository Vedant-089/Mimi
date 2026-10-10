import json
import re
from collections import defaultdict
from typing import Optional

import discord
from discord.ext import commands

from database import db
from functions import ensure_ping_tables, invalidate_reserve_cache, resolve_alias

with open("class.json", "r", encoding="utf-8") as f:
    VALID_POKEMON = set(name.lower().strip() for name in json.load(f).keys())

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

ALLOWED_ROLES_FILE = "allowed_roles.json"

try:
    with open(ALLOWED_ROLES_FILE, "r", encoding="utf-8") as f:
        ALLOWED_ROLES = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    ALLOWED_ROLES = {}


def expand_reserve_inputs(input_names):
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


class ReserveListView(discord.ui.View):
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


class ConfirmClearAllReservesView(discord.ui.View):
    def __init__(self, clear_callback):
        super().__init__(timeout=30)
        self.clear_callback = clear_callback

    @discord.ui.button(label="Clear all reserves", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await self.clear_callback(interaction)
        await interaction.message.edit(view=None)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="❌ Reserve clear cancelled.", view=None)
        self.stop()


class ReserveCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        await ensure_ping_tables(db)

    @commands.group(name="reserves", aliases=["r"], invoke_without_command=True)
    @commands.guild_only()
    async def reserves(self, ctx, *, args: str = ""):
        mentions = self._mention_members(ctx)
        if mentions:
            await self.show_user_reserves(ctx, mentions[0])
            return

        if args:
            try:
                member = await commands.MemberConverter().convert(ctx, args.split()[0])
                await self.show_user_reserves(ctx, member)
                return
            except commands.BadArgument:
                pass

        await ctx.send(
            "Usage: `m!r @user`, `m!r search <pokemon>`, "
            "`m!r a @user <pokemon>`, `m!r a <pokemon> @user`, "
            "`m!r r @user <pokemon>`, `m!r r <pokemon> @user`, "
            "`m!r clear`, `m!r clear @user`, or `m!r l`"
        )

    def has_manage_permission(self, member: discord.Member) -> bool:
        if member.guild_permissions.administrator:
            return True
        allowed_role_ids = {
            int(role_id)
            for role_id in ALLOWED_ROLES.get(str(member.guild.id), [])
        }
        return any(role.id in allowed_role_ids for role in member.roles)

    async def ensure_manage_permission(self, ctx: commands.Context) -> bool:
        if self.has_manage_permission(ctx.author):
            return True

        await ctx.send("❌ You need administrator permissions or an allowed reserves role to use this command.")
        return False

    @commands.command(name="allow")
    @commands.guild_only()
    async def allow_roles(self, ctx: commands.Context, *, role_ids: str = ""):
        if not ctx.author.guild_permissions.administrator:
            await ctx.send("❌ You need administrator permissions to use this command.")
            return

        requested_ids = [value for value in re.split(r"[\s,]+", role_ids) if value]
        if not requested_ids:
            await ctx.send("❌ Use: `m!allow <roleid1>, <roleid2>`")
            return

        invalid_ids = [value for value in requested_ids if not value.isdigit()]
        role_ids_to_add = []
        missing_ids = []
        for value in requested_ids:
            if not value.isdigit():
                continue
            role_id = int(value)
            if ctx.guild.get_role(role_id) is None:
                missing_ids.append(value)
            elif role_id not in role_ids_to_add:
                role_ids_to_add.append(role_id)

        if invalid_ids or missing_ids:
            invalid_text = ", ".join(invalid_ids + missing_ids)
            await ctx.send(f"❌ These are not valid roles in this server: {invalid_text}")
            return

        server_key = str(ctx.guild.id)
        current_ids = [int(role_id) for role_id in ALLOWED_ROLES.get(server_key, [])]
        for role_id in role_ids_to_add:
            if role_id not in current_ids:
                current_ids.append(role_id)
        ALLOWED_ROLES[server_key] = current_ids

        with open(ALLOWED_ROLES_FILE, "w", encoding="utf-8") as f:
            json.dump(ALLOWED_ROLES, f, indent=2)

        role_mentions = ", ".join(f"<@&{role_id}>" for role_id in role_ids_to_add)
        await ctx.send(f"✅ Allowed reserves access for: {role_mentions}")

    def _mention_members(self, ctx: commands.Context) -> list[discord.Member]:
        mentioned = []
        for user in ctx.message.mentions:
            if user.id == ctx.me.id:
                continue
            member = ctx.guild.get_member(user.id)
            if member and member not in mentioned:
                mentioned.append(member)
        return mentioned

    async def parse_member_and_pokemon(self, ctx: commands.Context, args: str):
        mentions = self._mention_members(ctx)
        pokemon_text = re.sub(r"<@!?&?\d+>", " ", args or "")
        pokemon_text = re.sub(r"\s+", " ", pokemon_text).strip()

        if mentions:
            return mentions[0], pokemon_text

        tokens = (args or "").split()
        if not tokens:
            return None, ""

        converter = commands.MemberConverter()
        try:
            member = await converter.convert(ctx, tokens[0])
            return member, " ".join(tokens[1:])
        except commands.BadArgument:
            pass

        try:
            member = await converter.convert(ctx, tokens[-1])
            return member, " ".join(tokens[:-1])
        except commands.BadArgument:
            return None, args

    async def send_reserve_pages(self, ctx: commands.Context, lines: list[str], title: str, footer: str):
        page_size = 20
        total_pages = (len(lines) + page_size - 1) // page_size
        pages = []

        for page_index, start in enumerate(range(0, len(lines), page_size), start=1):
            page_lines = lines[start:start + page_size]
            embed = discord.Embed(
                title=title,
                description="\n".join(page_lines),
                color=discord.Color.purple(),
            )
            embed.set_footer(text=f"{footer} • Page {page_index}/{total_pages}")
            pages.append(embed)

        if len(pages) == 1:
            await ctx.send(embed=pages[0])
            return

        await ctx.send(embed=pages[0], view=ReserveListView(pages))

    async def show_user_reserves(self, ctx: commands.Context, member: discord.Member):
        rows = await db.fetch(
            """
            SELECT pokemon_name FROM reserve_pings
            WHERE serverid = $1 AND userid = $2
            ORDER BY pokemon_name
            """,
            ctx.guild.id, member.id
        )
        names = [
            (row["pokemon_name"] or "").strip().lower()
            for row in rows
            if (row["pokemon_name"] or "").strip()
        ]
        if not names:
            return await ctx.send(f"📭 {member.mention} has no reserves in this server.")

        lines = [f"{name.title()} : {member.mention}" for name in names]
        await self.send_reserve_pages(
            ctx,
            lines,
            f"🔒 {member.display_name}'s Reserves",
            f"{member.display_name}'s reserves",
        )

    async def clear_all_reserves(self, interaction: discord.Interaction):
        serverid = interaction.guild_id
        rows = await db.fetch(
            "SELECT userid FROM reserve_pings WHERE serverid = $1 LIMIT 1",
            serverid
        )
        if not rows:
            await interaction.followup.send("ℹ️ There are no reserve entries to clear.", ephemeral=True)
            return

        await db.execute(
            "DELETE FROM reserve_pings WHERE serverid = $1",
            serverid
        )
        invalidate_reserve_cache()
        await interaction.followup.send("🧹 Cleared all reserves.")

    async def clear_reserves_for_member(self, ctx: commands.Context, member: discord.Member):
        serverid = ctx.guild.id
        row = await db.fetchval(
            "SELECT 1 FROM reserve_pings WHERE userid = $1 AND serverid = $2 LIMIT 1",
            member.id, serverid
        )
        if not row:
            return await ctx.send(f"ℹ️ {member.mention} has no reserves set.")

        await db.execute(
            "DELETE FROM reserve_pings WHERE userid = $1 AND serverid = $2",
            member.id, serverid
        )
        invalidate_reserve_cache()
        await ctx.send(f"🧹 Cleared {member.mention}'s reserves.")

    @reserves.command(name="clear", aliases=["c"])
    @commands.guild_only()
    async def clear(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        if member is not None:
            await self.clear_reserves_for_member(ctx, member)
            return

        if not await self.ensure_manage_permission(ctx):
            return

        await ctx.send(
            "⚠️ This will clear all reserves across the server. Confirm below?",
            view=ConfirmClearAllReservesView(self.clear_all_reserves),
        )

    @reserves.command(name="add", aliases=["a"])
    @commands.guild_only()
    async def add(self, ctx, *, args: str):
        if not await self.ensure_manage_permission(ctx):
            return

        member, pokemon = await self.parse_member_and_pokemon(ctx, args)
        if member is None:
            return await ctx.send("❌ Mention a user: `m!r a @user <pokemon>` or `m!r a <pokemon> @user`.")
        if not pokemon:
            return await ctx.send("❌ Provide at least one Pokémon name.")

        user_id = member.id
        serverid = ctx.guild.id
        input_names = [name.strip().lower() for name in pokemon.split(",") if name.strip()]
        valid_names, invalid_names = expand_reserve_inputs(input_names)

        if not valid_names:
            return await ctx.send("❌ No valid Pokémon provided.")

        await db.execute(
            """
            INSERT INTO reserve_pings (serverid, userid, pokemon_name)
            SELECT $1, $2, UNNEST($3::text[])
            ON CONFLICT (serverid, userid, pokemon_name) DO NOTHING
            """,
            serverid, user_id, valid_names
        )
        invalidate_reserve_cache()

        preview = ", ".join(name.title() for name in valid_names[:25])
        if len(valid_names) > 25:
            preview += f", ... (+{len(valid_names) - 25} more)"

        response = (
            f"✅ Added {len(valid_names)} Pokémon to {member.mention}'s reserves: {preview}"
        )
        if invalid_names:
            response += f"\n❌ Invalid: {', '.join(invalid_names)}"
        await ctx.send(response)

    @reserves.command(name="remove", aliases=["r"])
    @commands.guild_only()
    async def remove(self, ctx, *, args: str):
        if not await self.ensure_manage_permission(ctx):
            return

        member, pokemon = await self.parse_member_and_pokemon(ctx, args)
        if member is None:
            return await ctx.send("❌ Mention a user: `m!r r @user <pokemon>` or `m!r r <pokemon> @user`.")
        if not pokemon:
            return await ctx.send("❌ Provide at least one Pokémon name.")

        user_id = member.id
        serverid = ctx.guild.id
        input_names = [name.strip().lower() for name in pokemon.split(",") if name.strip()]
        valid_names, invalid_names = expand_reserve_inputs(input_names)

        if not valid_names:
            return await ctx.send("❌ No valid Pokémon provided.")

        current_rows = await db.fetch(
            """
            SELECT pokemon_name FROM reserve_pings
            WHERE serverid = $1 AND userid = $2 AND pokemon_name = ANY($3::text[])
            """,
            serverid, user_id, valid_names
        )
        removed = sorted(row["pokemon_name"] for row in current_rows)
        if not removed:
            existing = await db.fetchval(
                "SELECT 1 FROM reserve_pings WHERE serverid = $1 AND userid = $2 LIMIT 1",
                serverid, user_id
            )
            if not existing:
                return await ctx.send(f"ℹ️ {member.mention} has no reserves set.")
            return await ctx.send(f"ℹ️ None of those were in {member.mention}'s reserves.")

        await db.execute(
            """
            DELETE FROM reserve_pings
            WHERE serverid = $1 AND userid = $2 AND pokemon_name = ANY($3::text[])
            """,
            serverid, user_id, valid_names
        )
        invalidate_reserve_cache()

        preview = ", ".join(name.title() for name in removed[:25])
        if len(removed) > 25:
            preview += f", ... (+{len(removed) - 25} more)"

        response = f"🗑️ Removed from {member.mention}'s reserves: {preview}"
        if invalid_names:
            response += f"\n❌ Invalid: {', '.join(invalid_names)}"
        await ctx.send(response)

    @reserves.command(name="search", aliases=["s"])
    @commands.guild_only()
    async def search(self, ctx, *, names: str):
        input_names = [name.strip().lower() for name in names.split(",") if name.strip()]
        valid_names, invalid_names = expand_reserve_inputs(input_names)

        if not valid_names:
            return await ctx.send("❌ No valid Pokémon provided.")

        rows = await db.fetch(
            """
            SELECT userid, pokemon_name FROM reserve_pings
            WHERE serverid = $1 AND pokemon_name = ANY($2::text[])
            """,
            ctx.guild.id, valid_names
        )
        reserve_map = defaultdict(list)
        for row in rows:
            name = (row["pokemon_name"] or "").strip().lower()
            if not name:
                continue
            reserve_map[name].append(row["userid"])

        lines = []
        for pokemon in sorted(valid_names):
            user_ids = sorted(set(reserve_map.get(pokemon, [])))
            if not user_ids:
                continue
            mentions = ", ".join(f"<@{uid}>" for uid in user_ids)
            lines.append(f"{pokemon.title()} : {mentions}")

        if not lines:
            response = "📭 None of those Pokémon are reserved in this server."
            if invalid_names:
                response += f"\n❌ Invalid: {', '.join(invalid_names)}"
            return await ctx.send(response)

        await self.send_reserve_pages(
            ctx,
            lines,
            "🔒 Reserve Search",
            "Reserve search",
        )
        if invalid_names:
            await ctx.send(f"❌ Invalid: {', '.join(invalid_names)}")

    @reserves.command(name="list", aliases=["l"])
    @commands.guild_only()
    async def list_reserves(self, ctx):
        rows = await db.fetch(
            "SELECT userid, pokemon_name FROM reserve_pings WHERE serverid = $1",
            ctx.guild.id
        )
        reserve_map = defaultdict(list)

        for row in rows:
            name = (row["pokemon_name"] or "").strip().lower()
            if not name:
                continue
            reserve_map[name].append(row["userid"])

        if not reserve_map:
            return await ctx.send("📭 No Pokémon are currently in reserves.")

        lines = []
        for pokemon in sorted(reserve_map.keys()):
            mentions = ", ".join(f"<@{uid}>" for uid in sorted(set(reserve_map[pokemon])))
            lines.append(f"{pokemon.title()} : {mentions}")

        await self.send_reserve_pages(
            ctx,
            lines,
            "🔒 Pokémon Reserves",
            "Server reserves",
        )


async def setup(bot):
    await bot.add_cog(ReserveCommands(bot))