import time
import asyncio
import copy
import json
import discord
from collections import defaultdict

ALLOWED_ROLES_FILE = "allowed_roles.json"
SUSPENDED_USERS_FILE = "suspended.json"
ONLY_PING_FILE = "only_ping.json"

_only_ping_cached = None


def is_only_ping_enabled() -> bool:
    global _only_ping_cached
    if _only_ping_cached is not None:
        return _only_ping_cached
    try:
        with open(ONLY_PING_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        _only_ping_cached = False
        return False
    except (OSError, json.JSONDecodeError) as error:
        print(f"⚠️ Failed to read {ONLY_PING_FILE}: {error}")
        return False

    if isinstance(data, bool):
        _only_ping_cached = data
    else:
        _only_ping_cached = isinstance(data, dict) and data.get("only_ping") is True
    return _only_ping_cached


def set_only_ping_enabled(enabled: bool) -> None:
    global _only_ping_cached
    with open(ONLY_PING_FILE, "w", encoding="utf-8") as f:
        json.dump({"only_ping": enabled}, f, indent=2)
        f.write("\n")
    _only_ping_cached = enabled


_suspended_users_cache = None


def load_suspended_users() -> set[str]:
    global _suspended_users_cache
    if _suspended_users_cache is not None:
        return set(_suspended_users_cache)

    try:
        with open(SUSPENDED_USERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        _suspended_users_cache = set()
        return set()

    if isinstance(data, dict):
        data = data.get("users", [])
    if not isinstance(data, list):
        _suspended_users_cache = set()
        return set()
    _suspended_users_cache = {
        username.casefold() for username in data if isinstance(username, str)
    }
    return set(_suspended_users_cache)


def save_suspended_users(users) -> None:
    global _suspended_users_cache
    _suspended_users_cache = {
        str(username).casefold() for username in users if username
    }
    with open(SUSPENDED_USERS_FILE, "w", encoding="utf-8") as file:
        json.dump(sorted(_suspended_users_cache), file, indent=2)


def is_suspended(user) -> bool:
    suspended_users = load_suspended_users()
    user_id = getattr(user, "id", None)
    username = getattr(user, "name", "")
    return (
        user_id is not None and str(user_id) in suspended_users
    ) or (
        isinstance(username, str) and username.casefold() in suspended_users
    )


_allowed_roles_cache = None


def load_allowed_roles() -> dict:
    global _allowed_roles_cache
    if _allowed_roles_cache is not None:
        return _allowed_roles_cache
    try:
        with open(ALLOWED_ROLES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            data = {}
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    _allowed_roles_cache = data
    return _allowed_roles_cache


def save_allowed_roles(data: dict) -> None:
    global _allowed_roles_cache
    _allowed_roles_cache = data
    with open(ALLOWED_ROLES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def get_allowed_role_ids(guild_id: int) -> list[int]:
    raw_ids = load_allowed_roles().get(str(guild_id), [])
    role_ids = []
    for value in raw_ids:
        try:
            role_id = int(value)
        except (TypeError, ValueError):
            continue
        if role_id not in role_ids:
            role_ids.append(role_id)
    return role_ids


def set_allowed_role_ids(guild_id: int, role_ids) -> list[int]:
    data = load_allowed_roles()
    cleaned = []
    for value in role_ids:
        try:
            role_id = int(value)
        except (TypeError, ValueError):
            continue
        if role_id not in cleaned:
            cleaned.append(role_id)
    data[str(guild_id)] = cleaned
    save_allowed_roles(data)
    return cleaned


def member_has_allowed_role(member: discord.Member) -> bool:
    allowed_role_ids = set(get_allowed_role_ids(member.guild.id))
    return any(role.id in allowed_role_ids for role in member.roles)


def has_allowed_support_role(member: discord.Member) -> bool:
    return member.guild_permissions.administrator or member_has_allowed_role(member)


async def has_server_permission(member: discord.Member, permission_key: str, db_instance) -> bool:
    if member.guild_permissions.administrator:
        return True
    settings = await get_server_spawn_settings(member.guild.id, db_instance)
    return bool(settings.get(permission_key)) and member_has_allowed_role(member)


async def wipe_user_data(db_instance, user_id: int):
    for table in (
        "collection_pings",
        "reserve_pings",
        "shiny_hunt",
        "server_shiny_hunt",
        "tp_qp",
    ):
        await db_instance.execute(f"DELETE FROM {table} WHERE userid = $1", user_id)


# Global caches and locks
_server_hunt_cache = defaultdict(lambda: defaultdict(list))
_cache_last_updated = 0
_cache_lock = asyncio.Lock()

# guild_id -> pokemon_name -> [userids]
_collection_cache = defaultdict(lambda: defaultdict(list))
_collection_afk = set()  # userids with global collection AFK on
_collection_cache_last_updated = 0
_collection_cache_lock = asyncio.Lock()

# guild_id -> pokemon_name -> [userids]
_reserve_cache = defaultdict(lambda: defaultdict(list))
_reserve_cache_last_updated = 0
_reserve_cache_lock = asyncio.Lock()

# guild_id -> category_key -> {name, pokemon}
# guild_id -> pokemon_name -> [category_keys]
_category_cache = defaultdict(dict)
_category_pokemon_cache = defaultdict(lambda: defaultdict(list))
_category_cache_last_updated = 0
_category_cache_lock = asyncio.Lock()
_default_category_defs = None
_default_category_seed_locks = defaultdict(asyncio.Lock)

DEFAULT_CATEGORY_ORDER = ("rare", "gmax", "eevos", "paradox", "regional")
CATEGORY_ALIAS_TO_KEY = {
    "rare": "rare",
    "rares": "rare",
    "gmax": "gmax",
    "gigantamax": "gmax",
    "eevos": "eevos",
    "eeveelutions": "eevos",
    "evos": "eevos",
    "paradox": "paradox",
    "regional": "regional",
    "regionals": "regional",
}

# guild_id -> pokemon_name -> [userids]
_type_ping_cache = defaultdict(lambda: defaultdict(list))
_region_ping_cache = defaultdict(lambda: defaultdict(list))
_tp_qp_cache_last_updated = 0
_tp_qp_cache_lock = asyncio.Lock()
_server_settings_cache = {}
_server_settings_cache_lock = asyncio.Lock()
PING_TABLES_SQL = [
    """
    CREATE TABLE IF NOT EXISTS collection_pings (
        serverid BIGINT NOT NULL,
        userid BIGINT NOT NULL,
        pokemon_name TEXT NOT NULL,
        PRIMARY KEY (serverid, userid, pokemon_name)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS reserve_pings (
        serverid BIGINT NOT NULL,
        userid BIGINT NOT NULL,
        pokemon_name TEXT NOT NULL,
        PRIMARY KEY (serverid, userid, pokemon_name)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS shiny_hunt (
        userid BIGINT PRIMARY KEY,
        pokemon_name TEXT,
        shinyafk BOOLEAN DEFAULT FALSE,
        colafk BOOLEAN DEFAULT FALSE
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS server_shiny_hunt (
        serverid BIGINT NOT NULL,
        userid BIGINT NOT NULL,
        pokemon_name TEXT,
        PRIMARY KEY (serverid, userid)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS server_settings (
        serverid BIGINT PRIMARY KEY,
        naming BOOLEAN NOT NULL DEFAULT TRUE,
        shiny_hunt_prio BOOLEAN NOT NULL DEFAULT FALSE,
        ping_shiny BOOLEAN NOT NULL DEFAULT TRUE,
        ping_collection BOOLEAN NOT NULL DEFAULT TRUE,
        ping_reserves BOOLEAN NOT NULL DEFAULT TRUE,
        ping_type BOOLEAN NOT NULL DEFAULT TRUE,
        ping_region BOOLEAN NOT NULL DEFAULT TRUE,
        copy_support BOOLEAN NOT NULL DEFAULT TRUE,
        allow_reserves BOOLEAN NOT NULL DEFAULT TRUE,
        allow_category BOOLEAN NOT NULL DEFAULT FALSE,
        allow_settings BOOLEAN NOT NULL DEFAULT FALSE,
        main_starboard BIGINT,
        shiny_starboard BIGINT,
        gmax_starboard BIGINT
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS server_perks (
        serverid BIGINT PRIMARY KEY,
        perk_level TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS tp_qp (
        serverid BIGINT NOT NULL,
        userid BIGINT NOT NULL,
        type_pings TEXT,
        region_pings TEXT,
        PRIMARY KEY (serverid, userid)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS server_categories (
        serverid BIGINT NOT NULL,
        category_key TEXT NOT NULL,
        display_name TEXT NOT NULL,
        pokemon_name TEXT NOT NULL,
        PRIMARY KEY (serverid, category_key, pokemon_name)
    );
    """,
]


async def ensure_ping_tables(db_instance):
    for statement in PING_TABLES_SQL:
        await db_instance.execute(statement)
    await db_instance.execute(
        "ALTER TABLE shiny_hunt ADD COLUMN IF NOT EXISTS colafk BOOLEAN DEFAULT FALSE;"
    )
    await db_instance.execute(
        "ALTER TABLE shiny_hunt DROP COLUMN IF EXISTS grind_support;"
    )
    await db_instance.execute("DROP TABLE IF EXISTS grind_support;")
    await db_instance.execute("DROP TABLE IF EXISTS grind_support_spawns;")
    await db_instance.execute("DROP TABLE IF EXISTS server_grind_support;")
    await db_instance.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'server_settings' AND column_name = 'only_ping'
            ) AND NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'server_settings' AND column_name = 'shiny_hunt_prio'
            ) THEN
                ALTER TABLE server_settings RENAME COLUMN only_ping TO shiny_hunt_prio;
            END IF;
        END $$;
        """
    )
    await db_instance.execute("ALTER TABLE shiny_hunt DROP COLUMN IF EXISTS type_pings;")
    await db_instance.execute("ALTER TABLE shiny_hunt DROP COLUMN IF EXISTS region_pings;")
    await db_instance.execute("DROP TABLE IF EXISTS collection_afk;")
    await db_instance.execute("DROP TABLE IF EXISTS type_pings;")
    await db_instance.execute("DROP TABLE IF EXISTS region_pings;")
    await db_instance.execute("DROP TABLE IF EXISTS type_region_settings;")
    await db_instance.execute(
        "CREATE INDEX IF NOT EXISTS collection_pings_server_pokemon_idx ON collection_pings (serverid, pokemon_name);"
    )
    await db_instance.execute(
        "CREATE INDEX IF NOT EXISTS reserve_pings_server_pokemon_idx ON reserve_pings (serverid, pokemon_name);"
    )
    await db_instance.execute(
        "CREATE INDEX IF NOT EXISTS server_categories_server_pokemon_idx ON server_categories (serverid, pokemon_name);"
    )
    await db_instance.execute(
        "ALTER TABLE server_settings ADD COLUMN IF NOT EXISTS ping_shiny BOOLEAN NOT NULL DEFAULT TRUE;"
    )
    await db_instance.execute(
        "ALTER TABLE server_settings ADD COLUMN IF NOT EXISTS ping_collection BOOLEAN NOT NULL DEFAULT TRUE;"
    )
    await db_instance.execute(
        "ALTER TABLE server_settings ADD COLUMN IF NOT EXISTS ping_reserves BOOLEAN NOT NULL DEFAULT TRUE;"
    )
    await db_instance.execute(
        "ALTER TABLE server_settings ADD COLUMN IF NOT EXISTS ping_type BOOLEAN NOT NULL DEFAULT TRUE;"
    )
    await db_instance.execute(
        "ALTER TABLE server_settings ADD COLUMN IF NOT EXISTS ping_region BOOLEAN NOT NULL DEFAULT TRUE;"
    )
    await db_instance.execute(
        "ALTER TABLE server_settings ADD COLUMN IF NOT EXISTS copy_support BOOLEAN NOT NULL DEFAULT TRUE;"
    )
    await db_instance.execute(
        "ALTER TABLE server_settings ADD COLUMN IF NOT EXISTS allow_reserves BOOLEAN NOT NULL DEFAULT TRUE;"
    )
    await db_instance.execute(
        "ALTER TABLE server_settings ADD COLUMN IF NOT EXISTS allow_category BOOLEAN NOT NULL DEFAULT FALSE;"
    )
    await db_instance.execute(
        "ALTER TABLE server_settings ADD COLUMN IF NOT EXISTS allow_settings BOOLEAN NOT NULL DEFAULT FALSE;"
    )


async def get_server_naming_enabled(server_id: int, db_instance) -> bool:
    value = await db_instance.fetchval(
        "SELECT naming FROM server_settings WHERE serverid = $1",
        server_id,
    )
    return True if value is None else bool(value)


async def get_server_shiny_hunt_priority(server_id: int, db_instance) -> bool:
    value = await db_instance.fetchval(
        "SELECT shiny_hunt_prio FROM server_settings WHERE serverid = $1",
        server_id,
    )
    return bool(value) if value is not None else False


_DEFAULT_SPAWN_SETTINGS = {
    "naming": True,
    "shiny_hunt_prio": False,
    "ping_shiny": True,
    "ping_collection": True,
    "ping_reserves": True,
    "ping_type": True,
    "ping_region": True,
    "copy_support": True,
    "allow_reserves": True,
    "allow_category": False,
    "allow_settings": False,
}


def _spawn_settings_from_row(row) -> dict:
    if row is None:
        return dict(_DEFAULT_SPAWN_SETTINGS)
    return {
        key: True if row[key] is None else bool(row[key])
        for key in _DEFAULT_SPAWN_SETTINGS
    }


async def get_server_spawn_settings(
    server_id: int,
    db_instance,
) -> dict:
    cached = _server_settings_cache.get(server_id)
    if cached is not None:
        return dict(cached)

    async with _server_settings_cache_lock:
        cached = _server_settings_cache.get(server_id)
        if cached is not None:
            return dict(cached)

        row = await db_instance.fetchrow(
            """
            SELECT naming, shiny_hunt_prio, ping_shiny, ping_collection,
                   ping_reserves, ping_type, ping_region, copy_support,
                   allow_reserves, allow_category, allow_settings
            FROM server_settings
            WHERE serverid = $1
            """,
            server_id,
        )
        settings = _spawn_settings_from_row(row)
        _server_settings_cache[server_id] = settings
        return dict(settings)


async def refresh_server_settings_cache(db_instance):
    async with _server_settings_cache_lock:
        try:
            rows = await db_instance.fetch(
                """
                SELECT serverid, naming, shiny_hunt_prio, ping_shiny,
                       ping_collection, ping_reserves, ping_type, ping_region,
                       copy_support, allow_reserves, allow_category, allow_settings
                FROM server_settings
                """
            )
            _server_settings_cache.clear()
            _server_settings_cache.update(
                {
                    row["serverid"]: _spawn_settings_from_row(row)
                    for row in rows
                }
            )
        except Exception as e:
            print("❌ Server Settings Cache Refresh Error:", e)


def invalidate_server_settings_cache(server_id: int):
    _server_settings_cache.pop(server_id, None)


# Load forms.json and prepare mapping
with open("forms.json", "r") as f:
    _forms_mapping = json.load(f)

# Invert mapping: form name → base name
_form_to_base = {}
for base, forms in _forms_mapping.items():
    for form in forms:
        _form_to_base[form] = base


# Load alias.json and prepare mapping
def _load_alias_mapping():
    try:
        with open("alias.json", "r", encoding="utf-8") as f:
            data = json.load(f)
        mapping = {}
        for main_name, aliases in data.items():
            main_clean = main_name.lower().strip()
            mapping[main_clean] = main_clean
            if isinstance(aliases, list):
                for alias in aliases:
                    if isinstance(alias, str):
                        alias_clean = alias.lower().strip()
                        if alias_clean:
                            mapping[alias_clean] = main_clean
        return mapping
    except Exception as e:
        print("⚠️ Failed to load alias.json:", e)
        return {}

ALIAS_MAPPING = _load_alias_mapping()


def resolve_alias(name: str) -> str:
    cleaned = (name or "").lower().strip()
    return ALIAS_MAPPING.get(cleaned, cleaned)


# ------------------------
# Hunt Cache Functions
# ------------------------

async def refresh_hunt_cache(db_instance, server_ids=None):
    global _server_hunt_cache, _cache_last_updated
    async with _cache_lock:
        try:
            global_rows = await db_instance.fetch("""
                SELECT TRIM(pokemon_name) AS name, userid
                FROM shiny_hunt
                WHERE shinyafk = false
                  AND pokemon_name IS NOT NULL
            """)
            server_rows = await db_instance.fetch("""
                SELECT server_hunt.serverid, TRIM(server_hunt.pokemon_name) AS name, server_hunt.userid
                FROM server_shiny_hunt AS server_hunt
                LEFT JOIN shiny_hunt AS user_settings ON user_settings.userid = server_hunt.userid
                WHERE COALESCE(user_settings.shinyafk, false) = false
                  AND server_hunt.pokemon_name IS NOT NULL
            """)
            temp_server_cache = defaultdict(lambda: defaultdict(list))
            if server_ids is None:
                server_ids = {
                    row["serverid"]
                    for row in server_rows
                    if row["serverid"] is not None
                }
            else:
                server_ids = set(server_ids)
            for row in global_rows:
                name = row["name"]
                if name:
                    for server_id in server_ids:
                        temp_server_cache[server_id][name.lower()].append(row["userid"])
            for row in server_rows:
                name = row["name"]
                if name:
                    temp_server_cache[row["serverid"]][name.lower()].append(row["userid"])
            _server_hunt_cache = temp_server_cache
            _cache_last_updated = time.time()
        except Exception as e:
            print("❌ DB Cache Refresh Error:", e)


def invalidate_hunt_cache():
    global _cache_last_updated
    if _cache_last_updated:
        _cache_last_updated = 0.001


def _get_hunt_user_ids(cache, pokemon_name: str):
    name = pokemon_name.lower()
    base_name = _form_to_base.get(name, name)
    user_ids = set(cache.get(name, []))

    user_ids.update(cache.get(f"all {base_name}", []))

    if base_name in _forms_mapping:
        user_ids.update(cache.get(base_name, []))
        for form in _forms_mapping[base_name]:
            user_ids.update(cache.get(form, []))

    return user_ids


async def get_users_hunting(pokemon_name: str, guild_id=None, db_instance=None):
    if db_instance is None:
        db_instance = guild_id
        guild_id = None

    if _cache_last_updated == 0:
        await refresh_hunt_cache(db_instance)

    if guild_id is None:
        return []
    return list(_get_hunt_user_ids(_server_hunt_cache.get(guild_id, {}), pokemon_name))


# ------------------------
# Collection Cache Functions
# ------------------------

async def refresh_collection_cache(db_instance):
    global _collection_cache, _collection_afk, _collection_cache_last_updated
    async with _collection_cache_lock:
        try:
            rows = await db_instance.fetch("""
                SELECT userid, serverid, pokemon_name
                FROM collection_pings
            """)
            afk_rows = await db_instance.fetch("""
                SELECT userid
                FROM shiny_hunt
                WHERE colafk = true
            """)
            temp_cache = defaultdict(lambda: defaultdict(list))
            for row in rows:
                name = (row["pokemon_name"] or "").strip().lower()
                if name:
                    temp_cache[row["serverid"]][name].append(row["userid"])
            _collection_cache = temp_cache
            _collection_afk = {row["userid"] for row in afk_rows}
            _collection_cache_last_updated = time.time()
        except Exception as e:
            print("❌ Collection Cache Refresh Error:", e)


async def apply_collection_cache_names(
    server_id: int,
    user_id: int,
    names,
    *,
    remove: bool = False,
):
    """Patch one user's collection cache without rebuilding every row."""
    if _collection_cache_last_updated == 0:
        return

    async with _collection_cache_lock:
        if _collection_cache_last_updated == 0:
            return
        guild_cache = _collection_cache[server_id]
        for raw_name in names:
            name = (raw_name or "").strip().lower()
            if not name:
                continue
            users = guild_cache[name]
            if remove:
                try:
                    while True:
                        users.remove(user_id)
                except ValueError:
                    pass
            elif user_id not in users:
                users.append(user_id)


def invalidate_collection_cache():
    global _collection_cache_last_updated
    if _collection_cache_last_updated:
        _collection_cache_last_updated = 0.001


async def get_users_collecting(pokemon_name: str, guild_id: int, db_instance):
    if not guild_id:
        return []

    if _collection_cache_last_updated == 0:
        await refresh_collection_cache(db_instance)

    name = pokemon_name.lower()
    guild_cache = _collection_cache.get(guild_id, {})
    user_ids = {
        uid for uid in guild_cache.get(name, [])
        if uid not in _collection_afk
    }

    return list(user_ids)


# ------------------------
# Reserve Cache Functions
# ------------------------

async def refresh_reserve_cache(db_instance):
    global _reserve_cache, _reserve_cache_last_updated
    async with _reserve_cache_lock:
        try:
            rows = await db_instance.fetch("""
                SELECT userid, serverid, pokemon_name
                FROM reserve_pings
            """)
            temp_cache = defaultdict(lambda: defaultdict(list))
            for row in rows:
                name = (row["pokemon_name"] or "").strip().lower()
                if name:
                    temp_cache[row["serverid"]][name].append(row["userid"])
            _reserve_cache = temp_cache
            _reserve_cache_last_updated = time.time()
        except Exception as e:
            print("❌ Reserve Cache Refresh Error:", e)


def invalidate_reserve_cache():
    global _reserve_cache_last_updated
    if _reserve_cache_last_updated:
        _reserve_cache_last_updated = 0.001


async def get_users_reserved(pokemon_name: str, guild_id: int, db_instance):
    if not guild_id:
        return []

    if _reserve_cache_last_updated == 0:
        await refresh_reserve_cache(db_instance)

    name = pokemon_name.lower()
    guild_cache = _reserve_cache.get(guild_id, {})
    return list(set(guild_cache.get(name, [])))


# ------------------------
# Server Category Cache Functions
# ------------------------

def _names_from_grouped_json(path: str) -> set[str]:
    with open(path, "r", encoding="utf-8") as file:
        data = json.load(file)
    return {
        str(name).lower().strip()
        for names in data.values()
        for name in names
        if str(name).strip()
    }


def get_default_category_defs():
    global _default_category_defs
    if _default_category_defs is not None:
        return _default_category_defs

    with open("class.json", "r", encoding="utf-8") as file:
        valid_pokemon = {name.lower().strip() for name in json.load(file).keys()}

    with open("eeveelutions_paradox.json", "r", encoding="utf-8") as file:
        eevee_data = json.load(file)
    eevee_names = {
        str(name).lower().strip()
        for name in eevee_data.get("eeveelutions_paradox", [])
        if str(name).strip()
    }
    paradox_names = {
        str(name).lower().strip()
        for name in eevee_data.get("paradox", [])
        if str(name).strip()
    }

    defs = [
        ("rare", "Rare", sorted(_names_from_grouped_json("rare.json") & valid_pokemon)),
        ("gmax", "Gmax", sorted(_names_from_grouped_json("gigantamax.json") & valid_pokemon)),
        ("eevos", "Eevos", sorted(eevee_names & valid_pokemon)),
        ("paradox", "Paradox", sorted(paradox_names & valid_pokemon)),
        ("regional", "Regional", sorted(_names_from_grouped_json("regional.json") & valid_pokemon)),
    ]
    _default_category_defs = defs
    return _default_category_defs


def resolve_category_key(name: str) -> str:
    key = (name or "").strip().lower()
    return CATEGORY_ALIAS_TO_KEY.get(key, key)


def get_group_pokemon_names(alias: str, guild_id=None) -> list[str] | None:
    key = CATEGORY_ALIAS_TO_KEY.get((alias or "").strip().lower())
    if key is None:
        return None
    if guild_id is not None:
        entry = _category_cache.get(guild_id, {}).get(key)
        if entry and entry.get("pokemon"):
            return list(entry["pokemon"])
    for default_key, _display_name, names in get_default_category_defs():
        if default_key == key:
            return list(names)
    return []


async def ensure_default_categories(server_id: int, db_instance):
    async with _default_category_seed_locks[server_id]:
        if _category_cache_last_updated == 0:
            await refresh_category_cache(db_instance)
        existing = _category_cache.get(server_id, {})
        missing = [
            (key, display_name, names)
            for key, display_name, names in get_default_category_defs()
            if key not in existing
        ]
        if not missing:
            return
        for key, display_name, names in missing:
            if not names:
                continue
            await db_instance.execute(
                """
                INSERT INTO server_categories (serverid, category_key, display_name, pokemon_name)
                SELECT $1, $2, $3, pokemon_name
                FROM unnest($4::text[]) AS pokemon_name
                ON CONFLICT DO NOTHING
                """,
                server_id,
                key,
                display_name,
                names,
            )
        await refresh_category_cache(db_instance)


async def refresh_category_cache(db_instance):
    global _category_cache, _category_pokemon_cache, _category_cache_last_updated
    async with _category_cache_lock:
        try:
            rows = await db_instance.fetch(
                """
                SELECT serverid, category_key, display_name, pokemon_name
                FROM server_categories
                """
            )
            temp_categories = defaultdict(dict)
            temp_pokemon = defaultdict(lambda: defaultdict(list))
            for row in rows:
                server_id = row["serverid"]
                key = (row["category_key"] or "").strip().lower()
                pokemon_name = (row["pokemon_name"] or "").strip().lower()
                if not key or not pokemon_name:
                    continue
                entry = temp_categories[server_id].setdefault(
                    key,
                    {"key": key, "name": row["display_name"] or key, "pokemon": []},
                )
                if pokemon_name not in entry["pokemon"]:
                    entry["pokemon"].append(pokemon_name)
                if key not in temp_pokemon[server_id][pokemon_name]:
                    temp_pokemon[server_id][pokemon_name].append(key)
            for server_entries in temp_categories.values():
                for entry in server_entries.values():
                    entry["pokemon"].sort()
            _category_cache = temp_categories
            _category_pokemon_cache = temp_pokemon
            _category_cache_last_updated = time.time()
        except Exception as e:
            print("❌ Category Cache Refresh Error:", e)


def invalidate_category_cache():
    global _category_cache_last_updated
    if _category_cache_last_updated:
        _category_cache_last_updated = 0.001


async def apply_category_cache(server_id: int, category_key: str, display_name: str, pokemon_names):
    if _category_cache_last_updated == 0:
        return

    key = (category_key or "").strip().lower()
    names = [name.strip().lower() for name in pokemon_names if name and name.strip()]
    async with _category_cache_lock:
        if _category_cache_last_updated == 0:
            return
        old = _category_cache[server_id].get(key)
        if old:
            for poke in old.get("pokemon", []):
                mapped = _category_pokemon_cache[server_id][poke]
                try:
                    mapped.remove(key)
                except ValueError:
                    pass
        _category_cache[server_id][key] = {
            "key": key,
            "name": display_name or key,
            "pokemon": sorted(set(names)),
        }
        for poke in names:
            mapped = _category_pokemon_cache[server_id][poke]
            if key not in mapped:
                mapped.append(key)


async def get_server_categories(server_id: int, db_instance):
    await ensure_default_categories(server_id, db_instance)
    entries = list(_category_cache.get(server_id, {}).values())
    order = {key: index for index, key in enumerate(DEFAULT_CATEGORY_ORDER)}
    entries.sort(
        key=lambda entry: (
            0 if entry["key"] in order else 1,
            order.get(entry["key"], 0),
            entry["name"].casefold(),
        )
    )
    return [
        {
            "key": entry["key"],
            "name": entry["name"],
            "pokemon": list(entry["pokemon"]),
        }
        for entry in entries
    ]


async def get_server_category(server_id: int, category_name: str, db_instance):
    await ensure_default_categories(server_id, db_instance)
    key = resolve_category_key(category_name)
    entry = _category_cache.get(server_id, {}).get(key)
    if entry is None:
        return None
    return {
        "key": entry["key"],
        "name": entry["name"],
        "pokemon": list(entry["pokemon"]),
    }


async def get_categories_for_pokemon(pokemon_name: str, guild_id: int, db_instance):
    if not guild_id:
        return []
    if _category_cache_last_updated == 0:
        await refresh_category_cache(db_instance)
    name = pokemon_name.lower()
    keys = _category_pokemon_cache.get(guild_id, {}).get(name, [])
    categories = _category_cache.get(guild_id, {})
    return [categories[key]["name"] for key in keys if key in categories]


# ------------------------
# Type & Region Ping Cache Functions
# ------------------------

def _parse_ping_pokemons(raw_value):
    if not raw_value:
        return []
    try:
        data = json.loads(raw_value)
        if isinstance(data, list):
            return [str(p).strip().lower() for p in data]
        elif isinstance(data, dict):
            return [str(p).strip().lower() for p in data.get("pokemons", [])]
    except Exception:
        pass
    return []


_TP_QP_UNSET = object()


def _build_tp_qp_caches(payloads):
    temp_type_cache = defaultdict(lambda: defaultdict(list))
    temp_region_cache = defaultdict(lambda: defaultdict(list))
    for server_id, user_id, type_raw, region_raw in payloads:
        for poke in _parse_ping_pokemons(type_raw):
            if poke:
                temp_type_cache[server_id][poke].append(user_id)
        for poke in _parse_ping_pokemons(region_raw):
            if poke:
                temp_region_cache[server_id][poke].append(user_id)
    return temp_type_cache, temp_region_cache


def _remove_user_from_guild_cache(guild_cache, user_id: int):
    for users in guild_cache.values():
        try:
            while True:
                users.remove(user_id)
        except ValueError:
            pass


async def refresh_tp_qp_cache(db_instance):
    global _type_ping_cache, _region_ping_cache, _tp_qp_cache_last_updated
    async with _tp_qp_cache_lock:
        try:
            rows = await db_instance.fetch("""
                SELECT serverid, userid, type_pings, region_pings
                FROM tp_qp
            """)
            payloads = [
                (row["serverid"], row["userid"], row["type_pings"], row["region_pings"])
                for row in rows
            ]
            temp_type_cache, temp_region_cache = await asyncio.to_thread(
                _build_tp_qp_caches,
                payloads,
            )
            _type_ping_cache = temp_type_cache
            _region_ping_cache = temp_region_cache
            _tp_qp_cache_last_updated = time.time()
        except Exception as e:
            print("❌ TP/QP Cache Refresh Error:", e)


async def apply_tp_qp_cache_user(
    server_id: int,
    user_id: int,
    type_pings=_TP_QP_UNSET,
    region_pings=_TP_QP_UNSET,
):
    """Patch one user's type/region ping cache without rebuilding every row."""
    if _tp_qp_cache_last_updated == 0:
        return

    type_pokemons = (
        _parse_ping_pokemons(type_pings)
        if type_pings is not _TP_QP_UNSET
        else None
    )
    region_pokemons = (
        _parse_ping_pokemons(region_pings)
        if region_pings is not _TP_QP_UNSET
        else None
    )

    async with _tp_qp_cache_lock:
        if _tp_qp_cache_last_updated == 0:
            return
        if type_pokemons is not None:
            type_guild = _type_ping_cache[server_id]
            _remove_user_from_guild_cache(type_guild, user_id)
            for poke in type_pokemons:
                if poke and user_id not in type_guild[poke]:
                    type_guild[poke].append(user_id)
        if region_pokemons is not None:
            region_guild = _region_ping_cache[server_id]
            _remove_user_from_guild_cache(region_guild, user_id)
            for poke in region_pokemons:
                if poke and user_id not in region_guild[poke]:
                    region_guild[poke].append(user_id)


def invalidate_tp_qp_cache():
    global _tp_qp_cache_last_updated
    if _tp_qp_cache_last_updated:
        _tp_qp_cache_last_updated = 0.001


async def get_users_type_ping(pokemon_name: str, guild_id: int, db_instance):
    if not guild_id:
        return []

    if _tp_qp_cache_last_updated == 0:
        await refresh_tp_qp_cache(db_instance)

    name = pokemon_name.lower()
    guild_cache = _type_ping_cache.get(guild_id, {})
    return list(set(guild_cache.get(name, [])))


async def get_users_region_ping(pokemon_name: str, guild_id: int, db_instance):
    if not guild_id:
        return []

    if _tp_qp_cache_last_updated == 0:
        await refresh_tp_qp_cache(db_instance)

    name = pokemon_name.lower()
    guild_cache = _region_ping_cache.get(guild_id, {})
    return list(set(guild_cache.get(name, [])))


async def warm_ping_caches(db_instance, server_ids=None):
    await asyncio.gather(
        refresh_hunt_cache(db_instance, server_ids),
        refresh_collection_cache(db_instance),
        refresh_reserve_cache(db_instance),
        refresh_category_cache(db_instance),
        refresh_tp_qp_cache(db_instance),
        refresh_server_settings_cache(db_instance),
        warm_perk_cache(db_instance),
    )

# ------------------------
# Incense Tracking Functions
# ------------------------

import os
import re

_incense_lock = asyncio.Lock()
INCENSE_TIMEOUT_SECONDS = 30.0
_incense_data = None
_incense_write_task = None
_perk_limit_cache = {}
_perks_config = None
DEFAULT_PERK_LEVEL = "Bronze"
CLEARED_PERK_LEVEL = "None"

_INCENSE_ACTIVE_RE = re.compile(r"Incense:\s*Active", re.IGNORECASE)
_INCENSE_20S_RE = re.compile(r"Spawn Interval:\s*20s", re.IGNORECASE)
_INCENSE_REMAINING_RE = re.compile(r"Spawns Remaining:\s*(\d+)", re.IGNORECASE)


def _load_perks_config():
    global _perks_config
    if _perks_config is None:
        try:
            with open("perks.json", "r", encoding="utf-8") as f:
                _perks_config = json.load(f)
        except Exception:
            _perks_config = {}
    return _perks_config


def invalidate_perk_cache(server_id: int | None = None):
    global _perk_limit_cache
    if server_id is None:
        _perk_limit_cache = {}
    else:
        _perk_limit_cache.pop(server_id, None)


async def warm_perk_cache(db_instance):
    try:
        rows = await db_instance.fetch(
            "SELECT serverid, perk_level FROM server_perks"
        )
        config = _load_perks_config()
        for row in rows:
            _perk_limit_cache[row["serverid"]] = config.get(row["perk_level"], 0)
    except Exception as e:
        print("❌ Perk Cache Refresh Error:", e)


async def get_server_max_incense_limit(server_id: int, db_instance) -> int:
    cached = _perk_limit_cache.get(server_id)
    if cached is not None:
        return cached

    row = await db_instance.fetchrow(
        "SELECT perk_level FROM server_perks WHERE serverid = $1", server_id
    )
    perk_level = (
        row["perk_level"]
        if row
        else DEFAULT_PERK_LEVEL
    )
    limit = _load_perks_config().get(perk_level, 0)
    _perk_limit_cache[server_id] = limit
    return limit


def parse_incense_footer(footer_text: str | None):
    """
    Parses embed footer text for Poketwo incense details.
    Returns tuple: (is_active: bool, is_20s: bool, remaining_spawns: int | None)
    """
    if not footer_text:
        return False, False, None

    is_active = bool(_INCENSE_ACTIVE_RE.search(footer_text))
    is_20s = bool(_INCENSE_20S_RE.search(footer_text))

    rem_match = _INCENSE_REMAINING_RE.search(footer_text)
    remaining_spawns = int(rem_match.group(1)) if rem_match else None

    return is_active, is_20s, remaining_spawns


def _read_incense_file() -> dict:
    if not os.path.exists("incense_tracking.json"):
        return {}
    try:
        with open("incense_tracking.json", "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        print("⚠️ Error reading incense_tracking.json:", e)
        return {}


def _write_incense_file(data: dict):
    with open("incense_tracking.json", "w", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"))


def _ensure_incense_loaded() -> dict:
    global _incense_data
    if _incense_data is None:
        _incense_data = _read_incense_file()
    return _incense_data


def _schedule_incense_write():
    global _incense_write_task
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        try:
            _write_incense_file(_ensure_incense_loaded())
        except Exception as e:
            print("⚠️ Error writing incense_tracking.json:", e)
        return

    if _incense_write_task is None or _incense_write_task.done():
        _incense_write_task = loop.create_task(_flush_incense_data())


async def _flush_incense_data():
    await asyncio.sleep(0.15)
    async with _incense_lock:
        snapshot = copy.deepcopy(_ensure_incense_loaded())
    try:
        await asyncio.to_thread(_write_incense_file, snapshot)
    except Exception as e:
        print("⚠️ Error writing incense_tracking.json:", e)


def _cleanup_stale_incenses_locked(data: dict) -> bool:
    """
    Checks all active_incense channels in data.
    If last_spawn_times[channel_id] is older than 40 seconds, removes channel.
    Returns True if data was modified, False otherwise.
    """
    now = time.time()
    changed = False

    for server_key, server_info in data.items():
        active_list = server_info.get("active_incense", [])
        last_times = server_info.setdefault("last_spawn_times", {})

        channels_to_remove = []
        for channel_id in list(active_list):
            last_t = last_times.get(str(channel_id), 0)
            if now - last_t > INCENSE_TIMEOUT_SECONDS:
                channels_to_remove.append(channel_id)

        for channel_id in channels_to_remove:
            active_list.remove(channel_id)
            last_times.pop(str(channel_id), None)
            changed = True

    return changed


async def cleanup_stale_incenses():
    """
    Async thread-safe helper called periodically by background task loop.
    """
    async with _incense_lock:
        data = _ensure_incense_loaded()
        if _cleanup_stale_incenses_locked(data):
            _schedule_incense_write()


async def update_and_check_incense(
    server_id: int, channel_id: int, footer_text: str | None, db_instance
) -> bool:
    """
    Updates incense tracking for server_id and channel_id.
    Returns True if the current spawn is permitted to be named, False otherwise.
    Persistence is in-memory first; disk writes are coalesced in the background
    so spawn naming is not blocked by JSON I/O.
    """
    if not server_id or not channel_id:
        return False

    max_limit = await get_server_max_incense_limit(server_id, db_instance)

    async with _incense_lock:
        data = _ensure_incense_loaded()
        should_save = _cleanup_stale_incenses_locked(data)

        server_key = str(server_id)
        server_info = data.setdefault(
            server_key,
            {
                "serverid": server_id,
                "max_incense_limit": max_limit,
                "active_incense": [],
                "last_spawn_times": {},
            },
        )

        server_info["max_incense_limit"] = max_limit
        active_list = server_info.setdefault("active_incense", [])
        last_times = server_info.setdefault("last_spawn_times", {})

        is_active, is_20s, remaining = parse_incense_footer(footer_text)

        can_be_named = False

        if is_active and remaining is not None:
            last_times[str(channel_id)] = time.time()
            should_save = True

            if channel_id in active_list:
                can_be_named = True
            elif len(active_list) < max_limit:
                active_list.append(channel_id)
                can_be_named = True
            else:
                can_be_named = False
        else:
            if channel_id in active_list:
                active_list.remove(channel_id)
                last_times.pop(str(channel_id), None)
                should_save = True
            can_be_named = False

        if should_save:
            _schedule_incense_write()

        return can_be_named


def get_incense_tracking_data():
    return copy.deepcopy(_ensure_incense_loaded())
