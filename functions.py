import time
import asyncio
import json
import discord
from collections import defaultdict

ALLOWED_ROLES_FILE = "allowed_roles.json"
SUSPENDED_USERS_FILE = "suspended.json"
ONLY_PING_FILE = "only_ping.json"
POKETWO_BOT_ID = 716390085896962058
GRIND_SUPPORT_SERVER_IDS = frozenset({
    1322103302888095837,
    1429413142319988788,
    1429413395769065614,
})


async def set_poketwo_send_permission(guild, channel, allowed: bool | None):
    if guild is None or guild.id not in GRIND_SUPPORT_SERVER_IDS:
        return False

    poketwo = guild.get_member(POKETWO_BOT_ID)
    if poketwo is None:
        poketwo = await guild.fetch_member(POKETWO_BOT_ID)

    if isinstance(channel, discord.Thread):
        channel = channel.parent
    if channel is None:
        return False

    await channel.set_permissions(
        poketwo,
        send_messages=allowed,
        reason="Grind support channel control",
    )
    return True


def is_only_ping_enabled() -> bool:
    try:
        with open(ONLY_PING_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return False
    except (OSError, json.JSONDecodeError) as error:
        print(f"⚠️ Failed to read {ONLY_PING_FILE}: {error}")
        return False

    if isinstance(data, bool):
        return data
    return isinstance(data, dict) and data.get("only_ping") is True


def set_only_ping_enabled(enabled: bool) -> None:
    with open(ONLY_PING_FILE, "w", encoding="utf-8") as f:
        json.dump({"only_ping": enabled}, f, indent=2)
        f.write("\n")


def load_suspended_users() -> set[str]:
    try:
        with open(SUSPENDED_USERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return set()

    if isinstance(data, dict):
        data = data.get("users", [])
    if not isinstance(data, list):
        return set()
    return {username.casefold() for username in data if isinstance(username, str)}


def is_suspended(user) -> bool:
    suspended_users = load_suspended_users()
    user_id = getattr(user, "id", None)
    username = getattr(user, "name", "")
    return (
        user_id is not None and str(user_id) in suspended_users
    ) or (
        isinstance(username, str) and username.casefold() in suspended_users
    )


def has_allowed_support_role(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    try:
        with open(ALLOWED_ROLES_FILE, "r", encoding="utf-8") as f:
            allowed_roles = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        allowed_roles = {}
    allowed_role_ids = {int(role_id) for role_id in allowed_roles.get(str(member.guild.id), [])}
    return any(role.id in allowed_role_ids for role in member.roles)


async def wipe_user_data(db_instance, user_id: int):
    for table in (
        "collection_pings",
        "reserve_pings",
        "shiny_hunt",
        "server_grind_support",
        "grind_support",
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

# guild_id -> pokemon_name -> [userids]
_type_ping_cache = defaultdict(lambda: defaultdict(list))
_region_ping_cache = defaultdict(lambda: defaultdict(list))
_tp_qp_cache_last_updated = 0
_tp_qp_cache_lock = asyncio.Lock()
_quest_requirements = defaultdict(
    lambda: {"types": {}, "regions": {}}
)
_quest_requirements_lock = asyncio.Lock()
with open("typing.json", "r", encoding="utf-8") as _typing_file:
    _type_pokemon_names = {
        type_name: {
            pokemon_name.lower().strip()
            for pokemon_name in pokemon_names
        }
        for type_name, pokemon_names in json.load(_typing_file).items()
    }

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
        colafk BOOLEAN DEFAULT FALSE,
        grind_support BOOLEAN DEFAULT FALSE
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS grind_support (
        id BIGSERIAL PRIMARY KEY,
        guildid BIGINT,
        channelid BIGINT NOT NULL,
        messageid BIGINT,
        pokemon_name TEXT NOT NULL,
        userid BIGINT NOT NULL,
        spawn_order BIGINT NOT NULL,
        UNIQUE (userid, spawn_order)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS server_grind_support (
        serverid BIGINT NOT NULL,
        userid BIGINT NOT NULL,
        PRIMARY KEY (serverid, userid)
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
]


async def ensure_ping_tables(db_instance):
    for statement in PING_TABLES_SQL:
        await db_instance.execute(statement)
    await db_instance.execute(
        "ALTER TABLE shiny_hunt ADD COLUMN IF NOT EXISTS colafk BOOLEAN DEFAULT FALSE;"
    )
    await db_instance.execute(
        "ALTER TABLE shiny_hunt ADD COLUMN IF NOT EXISTS grind_support BOOLEAN DEFAULT FALSE;"
    )
    await db_instance.execute(
        "ALTER TABLE grind_support ADD COLUMN IF NOT EXISTS guildid BIGINT;"
    )
    await db_instance.execute(
        "ALTER TABLE grind_support ADD COLUMN IF NOT EXISTS messageid BIGINT;"
    )
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
        "CREATE INDEX IF NOT EXISTS grind_support_user_order_idx ON grind_support (userid, spawn_order);"
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


async def get_server_spawn_settings(
    server_id: int,
    db_instance,
) -> tuple[bool, bool]:
    row = await db_instance.fetchrow(
        """
        SELECT naming, shiny_hunt_prio
        FROM server_settings
        WHERE serverid = $1
        """,
        server_id,
    )
    if row is None:
        return True, False
    return bool(row["naming"]), bool(row["shiny_hunt_prio"])


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


async def refresh_tp_qp_cache(db_instance):
    global _type_ping_cache, _region_ping_cache, _tp_qp_cache_last_updated
    async with _tp_qp_cache_lock:
        try:
            rows = await db_instance.fetch("""
                SELECT serverid, userid, type_pings, region_pings
                FROM tp_qp
            """)
            temp_type_cache = defaultdict(lambda: defaultdict(list))
            temp_region_cache = defaultdict(lambda: defaultdict(list))

            for row in rows:
                server_id = row["serverid"]
                user_id = row["userid"]

                t_pokemons = _parse_ping_pokemons(row["type_pings"])
                for poke in t_pokemons:
                    if poke:
                        temp_type_cache[server_id][poke].append(user_id)

                r_pokemons = _parse_ping_pokemons(row["region_pings"])
                for poke in r_pokemons:
                    if poke:
                        temp_region_cache[server_id][poke].append(user_id)

            _type_ping_cache = temp_type_cache
            _region_ping_cache = temp_region_cache
            _tp_qp_cache_last_updated = time.time()
        except Exception as e:
            print("❌ TP/QP Cache Refresh Error:", e)


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


async def set_quest_requirements(
    server_id: int,
    user_id: int,
    type_requirements: dict[str, int],
    region_requirements: dict[str, int],
    replace_types: bool = False,
):
    if server_id not in GRIND_SUPPORT_SERVER_IDS:
        return

    async with _quest_requirements_lock:
        requirements = _quest_requirements[(server_id, user_id)]
        if replace_types:
            requirements["types"].clear()
        for quest_type, remaining in type_requirements.items():
            requirements["types"][quest_type] = max(0, remaining)
        for region, remaining in region_requirements.items():
            requirements["regions"][region] = max(0, remaining)


async def limit_quest_ping_users(
    server_id: int,
    grind_support_users: set[int],
    db_instance,
    type_ping_users: list[int],
    region_ping_users: list[int],
    pokemon_types: list[str],
    pokemon_regions: list[str],
):
    if server_id not in GRIND_SUPPORT_SERVER_IDS or not grind_support_users:
        return type_ping_users, region_ping_users

    async with _quest_requirements_lock:
        completed_types = defaultdict(set)
        limited_type_users = []
        for user_id in type_ping_users:
            if user_id not in grind_support_users:
                limited_type_users.append(user_id)
                continue
            requirements = _quest_requirements.get((server_id, user_id))
            type_remaining = (
                [
                    requirements["types"][quest_type]
                    for quest_type in pokemon_types
                    if quest_type in requirements["types"]
                ]
                if requirements
                else []
            )
            if not type_remaining or any(remaining > 0 for remaining in type_remaining):
                limited_type_users.append(user_id)
                if requirements:
                    for quest_type in pokemon_types:
                        if requirements["types"].get(quest_type, 0) > 0:
                            requirements["types"][quest_type] -= 1
                            if requirements["types"][quest_type] == 0:
                                completed_types[user_id].add(quest_type)

        limited_region_users = []
        for user_id in region_ping_users:
            if user_id not in grind_support_users:
                limited_region_users.append(user_id)
                continue
            requirements = _quest_requirements.get((server_id, user_id))
            region_remaining = (
                [
                    requirements["regions"][region]
                    for region in pokemon_regions
                    if region in requirements["regions"]
                ]
                if requirements
                else []
            )
            if not region_remaining or any(remaining > 0 for remaining in region_remaining):
                limited_region_users.append(user_id)
                if requirements:
                    for region in pokemon_regions:
                        if requirements["regions"].get(region, 0) > 0:
                            requirements["regions"][region] -= 1

        changed_type_pings = False
        for user_id, type_names in completed_types.items():
            row = await db_instance.fetchrow(
                """
                SELECT type_pings
                FROM tp_qp
                WHERE serverid = $1 AND userid = $2
                """,
                server_id,
                user_id,
            )
            if not row or not row["type_pings"]:
                continue
            try:
                payload = json.loads(row["type_pings"])
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue

            active_types = {
                str(type_name).casefold()
                for type_name in payload.get("types", [])
            }
            remaining_types = active_types - type_names
            payload["types"] = sorted(remaining_types)
            payload["pokemons"] = sorted(
                {
                    pokemon_name
                    for type_name in remaining_types
                    for pokemon_name in _type_pokemon_names.get(type_name, set())
                }
            )
            await db_instance.execute(
                """
                UPDATE tp_qp
                SET type_pings = $3
                WHERE serverid = $1 AND userid = $2
                """,
                server_id,
                user_id,
                json.dumps(payload),
            )
            changed_type_pings = True

        if changed_type_pings:
            await refresh_tp_qp_cache(db_instance)

    return limited_type_users, limited_region_users


async def get_grind_support_users(db_instance, server_id: int):
    rows = await db_instance.fetch(
        """
        SELECT userid
        FROM server_grind_support
        WHERE serverid = $1
        """,
        server_id,
    )
    return {row["userid"] for row in rows}


async def warm_ping_caches(db_instance, server_ids=None):
    await asyncio.gather(
        refresh_hunt_cache(db_instance, server_ids),
        refresh_collection_cache(db_instance),
        refresh_reserve_cache(db_instance),
        refresh_tp_qp_cache(db_instance),
        warm_perk_cache(db_instance),
    )


# ------------------------
# Incense Tracking Functions
# ------------------------

import copy
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


def is_type_enabled() -> bool:
    try:
        with open("type_settings.json", "r", encoding="utf-8") as f:
            data = json.load(f)
        value = data.get("type", data.get("enabled", True))
        return bool(value)
    except FileNotFoundError:
        return True
    except Exception as e:
        print(f"⚠️ Failed to read type_settings.json: {e}")
        return True


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
