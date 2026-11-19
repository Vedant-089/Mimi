import discord
from discord.ext import commands

from commands.reserves import expand_reserve_inputs
from database import db
from functions import (
    apply_category_cache,
    ensure_default_categories,
    ensure_ping_tables,
    get_server_categories,
    get_server_category,
    has_server_permission,
    is_pokemon_name_or_alias,
)

CATEGORIES_PER_PAGE = 10
MAX_CATEGORY_NAME = 64


def parse_category_args(args: str):
    text = (args or "").strip()
    if not text:
        return None, None
    if text.startswith('"'):
        end = text.find('"', 1)
        if end == -1:
            return None, None
        name = text[1:end].strip()
        rest = text[end + 1 :].strip()
        return name or None, rest
    name, _, rest = text.partition(" ")
    return name.strip() or None, rest.strip()


def split_pokemon_list(pokemon_text: str):
    return [name.strip().lower() for name in (pokemon_text or "").split(",") if name.strip()]


def format_pokemon_names(names):
    return ", ".join(name.title() for name in names)


def format_save_response(action: str, display_name: str, valid_names, invalid_names):
    lines = [f"✅ {action} **{display_name}**"]
    if valid_names:
        lines.append(f"Pokémon: {format_pokemon_names(valid_names)}")
    if invalid_names:
        lines.append(f"Not added: {', '.join(invalid_names)}")
    return "\n".join(lines)


class CategoryEditModal(discord.ui.Modal, title="Edit Category"):
    pokemon_list = discord.ui.TextInput(
        label="Give the edited list here",
        placeholder="pokemon1, pokemon2",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=4000,
    )

    def __init__(self, view, category_key: str, display_name: str, pokemon_names):
        super().__init__()
        self.list_view = view
        self.category_key = category_key
        self.display_name = display_name
        current = format_pokemon_names(pokemon_names)
        if len(current) > 4000:
            current = current[:4000]
        self.pokemon_list.default = current

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.guild is None:
            return await interaction.response.send_message(
                "❌ Categories can only be edited in a server.",
                ephemeral=True,
            )
        if not await has_server_permission(interaction.user, "allow_category", db):
            return await interaction.response.send_message(
                "❌ You need administrator permissions or an allowed role with Category Edit/Create enabled.",
                ephemeral=True,
            )

        valid_names, invalid_names = expand_reserve_inputs(
            split_pokemon_list(str(self.pokemon_list.value)),
            interaction.guild.id,
        )
        if not valid_names:
            message = "❌ No valid Pokémon to save."
            if invalid_names:
                message += f" Not added: {', '.join(invalid_names)}"
            return await interaction.response.send_message(message, ephemeral=True)

        await save_category_pokemon(
            interaction.guild.id,
            self.category_key,
            self.display_name,
            valid_names,
        )
        categories = await get_server_categories(interaction.guild.id, db)
        self.list_view.categories = categories
        max_page = max(0, (len(categories) - 1) // CATEGORIES_PER_PAGE)
        self.list_view.page = min(self.list_view.page, max_page)
        self.list_view._build_layout()
        await interaction.response.edit_message(view=self.list_view)
        await interaction.followup.send(
            format_save_response("Updated", self.display_name, valid_names, invalid_names)
        )


class CategoryListView(discord.ui.LayoutView):
    def __init__(self, author_id: int, server_id: int, categories: list, page: int = 0):
        super().__init__(timeout=180)
        self.author_id = author_id
        self.server_id = server_id
        self.categories = categories
        self.page = page
        self._build_layout()

    def _build_layout(self):
        self.clear_items()
        total = len(self.categories)
        max_page = max(0, (total - 1) // CATEGORIES_PER_PAGE) if total else 0
        self.page = min(self.page, max_page)
        start = self.page * CATEGORIES_PER_PAGE
        page_categories = self.categories[start : start + CATEGORIES_PER_PAGE]

        children = [
            discord.ui.TextDisplay("# Categories\nServer-specific Pokémon lists."),
            discord.ui.Separator(),
        ]
        if not page_categories:
            children.append(
                discord.ui.TextDisplay(
                    "No categories yet.\nUse `m!category create <name> pokemon1, pokemon2`."
                )
            )
        else:
            for index, category in enumerate(page_categories):
                button = discord.ui.Button(
                    label="Edit",
                    style=discord.ButtonStyle.primary,
                    custom_id=f"category:edit:{start + index}",
                )
                button.callback = self._edit_callback(category)
                children.append(
                    discord.ui.Section(
                        f"**{category['name']}**",
                        accessory=button,
                    )
                )

        if total > CATEGORIES_PER_PAGE:
            prev_button = discord.ui.Button(
                label="◀",
                style=discord.ButtonStyle.secondary,
                custom_id="category:page:prev",
                disabled=self.page == 0,
            )
            prev_button.callback = self.prev_page
            next_button = discord.ui.Button(
                label="▶",
                style=discord.ButtonStyle.secondary,
                custom_id="category:page:next",
                disabled=self.page >= max_page,
            )
            next_button.callback = self.next_page
            children.extend(
                [
                    discord.ui.Separator(),
                    discord.ui.ActionRow(prev_button, next_button),
                ]
            )

        self.add_item(
            discord.ui.Container(
                *children,
                accent_color=discord.Color.from_rgb(47, 49, 54),
            )
        )

    def _edit_callback(self, category):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.author_id:
                return await interaction.response.send_message(
                    "❌ Only the person who opened this menu can use the buttons.",
                    ephemeral=True,
                )
            if not await has_server_permission(interaction.user, "allow_category", db):
                return await interaction.response.send_message(
                    "❌ You need administrator permissions or an allowed role with Category Edit/Create enabled.",
                    ephemeral=True,
                )
            latest = await get_server_category(self.server_id, category["key"], db)
            if latest is None:
                return await interaction.response.send_message(
                    f"❌ **{category['name']}** no longer exists.",
                    ephemeral=True,
                )
            await interaction.response.send_modal(
                CategoryEditModal(self, latest["key"], latest["name"], latest["pokemon"])
            )

        return callback

    async def prev_page(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message(
                "❌ Only the person who opened this menu can use the buttons.",
                ephemeral=True,
            )
        if self.page > 0:
            self.page -= 1
        self._build_layout()
        await interaction.response.edit_message(view=self)

    async def next_page(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message(
                "❌ Only the person who opened this menu can use the buttons.",
                ephemeral=True,
            )
        max_page = max(0, (len(self.categories) - 1) // CATEGORIES_PER_PAGE)
        if self.page < max_page:
            self.page += 1
        self._build_layout()
        await interaction.response.edit_message(view=self)


async def save_category_pokemon(server_id: int, category_key: str, display_name: str, pokemon_names):
    await db.execute(
        "DELETE FROM server_categories WHERE serverid = $1 AND category_key = $2",
        server_id,
        category_key,
    )
    await db.execute(
        """
        INSERT INTO server_categories (serverid, category_key, display_name, pokemon_name)
        SELECT $1, $2, $3, pokemon_name
        FROM unnest($4::text[]) AS pokemon_name
        """,
        server_id,
        category_key,
        display_name,
        pokemon_names,
    )
    await apply_category_cache(server_id, category_key, display_name, pokemon_names)


class CategoryCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        await ensure_ping_tables(db)
        for guild in self.bot.guilds:
            await ensure_default_categories(guild.id, db)

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        await ensure_default_categories(guild.id, db)

    async def _ensure_manage(self, ctx: commands.Context) -> bool:
        if await has_server_permission(ctx.author, "allow_category", db):
            return True
        await ctx.send(
            "❌ You need administrator permissions or an allowed role with Category Edit/Create enabled."
        )
        return False

    @commands.group(name="category", aliases=["categories"], invoke_without_command=True)
    @commands.guild_only()
    async def category(self, ctx: commands.Context):
        categories = await get_server_categories(ctx.guild.id, db)
        await ctx.send(
            view=CategoryListView(ctx.author.id, ctx.guild.id, categories)
        )

    @category.command(name="create")
    @commands.guild_only()
    async def create(self, ctx: commands.Context, *, args: str = ""):
        if not await self._ensure_manage(ctx):
            return

        name, pokemon_text = parse_category_args(args)
        if not name or not pokemon_text:
            return await ctx.send(
                "❌ Usage: `m!category create <name> pokemon1, pokemon2`"
            )
        if len(name) > MAX_CATEGORY_NAME:
            return await ctx.send(f"❌ Category names can be at most {MAX_CATEGORY_NAME} characters.")
        if is_pokemon_name_or_alias(name):
            return await ctx.send(
                f"❌ **{name}** is a Pokémon name or alias, so it cannot be used as a category."
            )

        category_key = name.casefold()
        existing = await get_server_category(ctx.guild.id, category_key, db)
        if existing is not None:
            return await ctx.send(
                f"❌ **{existing['name']}** already exists. Use `m!category edit {existing['name']} pokemon1, pokemon2`."
            )

        valid_names, invalid_names = expand_reserve_inputs(
            split_pokemon_list(pokemon_text),
            ctx.guild.id,
        )
        if not valid_names:
            message = "❌ No valid Pokémon to save."
            if invalid_names:
                message += f" Not added: {', '.join(invalid_names)}"
            return await ctx.send(message)

        await save_category_pokemon(ctx.guild.id, category_key, name, valid_names)
        await ctx.send(format_save_response("Created", name, valid_names, invalid_names))

    @category.command(name="edit")
    @commands.guild_only()
    async def edit(self, ctx: commands.Context, *, args: str = ""):
        if not await self._ensure_manage(ctx):
            return

        name, pokemon_text = parse_category_args(args)
        if not name or not pokemon_text:
            return await ctx.send(
                "❌ Usage: `m!category edit <name> pokemon1, pokemon2`"
            )

        existing = await get_server_category(ctx.guild.id, name, db)
        if existing is None:
            return await ctx.send(f"❌ No category named **{name}** in this server.")

        valid_names, invalid_names = expand_reserve_inputs(
            split_pokemon_list(pokemon_text),
            ctx.guild.id,
        )
        if not valid_names:
            message = "❌ No valid Pokémon to save."
            if invalid_names:
                message += f" Not added: {', '.join(invalid_names)}"
            return await ctx.send(message)

        await save_category_pokemon(
            ctx.guild.id,
            existing["key"],
            existing["name"],
            valid_names,
        )
        await ctx.send(
            format_save_response("Updated", existing["name"], valid_names, invalid_names)
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(CategoryCommands(bot))
