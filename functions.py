import time
import asyncio
import json
from collections import defaultdict

# Global caches and locks
_hunt_cache = defaultdict(list)
_cache_last_updated = 0
_cache_refresh_interval = 15  # seconds
_cache_lock = asyncio.Lock()

# guild_id -> pokemon_name -> [userids]
_collection_cache = defaultdict(lambda: defaultdict(list))
_collection_afk = set()  # userids with global collection AFK on
_collection_cache_last_updated = 0
_collection_cache_refresh_interval = 15  # seconds
_collection_cache_lock = asyncio.Lock()

# guild_id -> pokemon_name -> [userids]
_reserve_cache = defaultdict(lambda: defaultdict(list))
_reserve_cache_last_updated = 0
_reserve_cache_refresh_interval = 15  # seconds
_reserve_cache_lock = asyncio.Lock()

# guild_id -> pokemon_name -> [userids]
_type_ping_cache = defaultdict(lambda: defaultdict(list))
_region_ping_cache = defaultdict(lambda: defaultdict(list))
_tp_qp_cache_last_updated = 0
_tp_qp_cache_refresh_interval = 15  # seconds
_tp_qp_cache_lock = asyncio.Lock()

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
    CREATE TABLE IF NOT EXISTS server_settings (
        serverid BIGINT PRIMARY KEY,
        naming BOOLEAN NOT NULL DEFAULT TRUE,
        only_ping BOOLEAN NOT NULL DEFAULT FALSE,
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

async def refresh_hunt_cache(db_instance):
    global _hunt_cache, _cache_last_updated
    async with _cache_lock:
        try:
            rows = await db_instance.fetch("""
                SELECT TRIM(pokemon_name) AS name, userid
                FROM shiny_hunt
                WHERE shinyafk = false
                  AND pokemon_name IS NOT NULL
            """)
            temp_cache = defaultdict(list)
            for row in rows:
                name = row["name"]
                if name:
                    temp_cache[name.lower()].append(row["userid"])
            _hunt_cache = temp_cache
            _cache_last_updated = time.time()
        except Exception as e:
            print("❌ DB Cache Refresh Error:", e)


def invalidate_hunt_cache():
    global _cache_last_updated
    if _cache_last_updated:
        _cache_last_updated = 0.001


async def get_users_hunting(pokemon_name: str, db_instance):
    now = time.time()
    if _cache_last_updated == 0:
        await refresh_hunt_cache(db_instance)
    elif now - _cache_last_updated > _cache_refresh_interval:
        if not _cache_lock.locked():
            asyncio.create_task(refresh_hunt_cache(db_instance))

    name = pokemon_name.lower()
    base_name = _form_to_base.get(name, name)
    user_ids = set(_hunt_cache.get(name, []))

    # A hunt stored as "all <base>" covers the base and every known form.
    user_ids.update(_hunt_cache.get(f"all {base_name}", []))

    # A regular base hunt also covers its known forms.
    if base_name in _forms_mapping:
        user_ids.update(_hunt_cache.get(base_name, []))
        for form in _forms_mapping[base_name]:
            user_ids.update(_hunt_cache.get(form, []))

    return list(user_ids)


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

    now = time.time()
    if _collection_cache_last_updated == 0:
        await refresh_collection_cache(db_instance)
    elif now - _collection_cache_last_updated > _collection_cache_refresh_interval:
        if not _collection_cache_lock.locked():
            asyncio.create_task(refresh_collection_cache(db_instance))

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

    now = time.time()
    if _reserve_cache_last_updated == 0:
        await refresh_reserve_cache(db_instance)
    elif now - _reserve_cache_last_updated > _reserve_cache_refresh_interval:
        if not _reserve_cache_lock.locked():
            asyncio.create_task(refresh_reserve_cache(db_instance))

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

    now = time.time()
    if _tp_qp_cache_last_updated == 0:
        await refresh_tp_qp_cache(db_instance)
    elif now - _tp_qp_cache_last_updated > _tp_qp_cache_refresh_interval:
        if not _tp_qp_cache_lock.locked():
            asyncio.create_task(refresh_tp_qp_cache(db_instance))

    name = pokemon_name.lower()
    guild_cache = _type_ping_cache.get(guild_id, {})
    return list(set(guild_cache.get(name, [])))


async def get_users_region_ping(pokemon_name: str, guild_id: int, db_instance):
    if not guild_id:
        return []

    now = time.time()
    if _tp_qp_cache_last_updated == 0:
        await refresh_tp_qp_cache(db_instance)
    elif now - _tp_qp_cache_last_updated > _tp_qp_cache_refresh_interval:
        if not _tp_qp_cache_lock.locked():
            asyncio.create_task(refresh_tp_qp_cache(db_instance))

    name = pokemon_name.lower()
    guild_cache = _region_ping_cache.get(guild_id, {})
    return list(set(guild_cache.get(name, [])))


async def warm_ping_caches(db_instance):
    await asyncio.gather(
        refresh_hunt_cache(db_instance),
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
INCENSE_TIMEOUT_SECONDS = 40.0
_incense_data = None
_incense_write_task = None
_perk_limit_cache = {}
_perks_config = None

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
    limit = 0
    if row:
        limit = _load_perks_config().get(row["perk_level"], 0)
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

        if is_active and is_20s and remaining is not None:
            if remaining > 0:
                last_times[str(channel_id)] = time.time()
                should_save = True

                if channel_id in active_list:
                    can_be_named = True
                else:
                    if len(active_list) < max_limit:
                        active_list.append(channel_id)
                        can_be_named = True
                    else:
                        can_be_named = False
            elif remaining == 0:
                if channel_id in active_list:
                    can_be_named = True
                    active_list.remove(channel_id)
                    last_times.pop(str(channel_id), None)
                    should_save = True
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

