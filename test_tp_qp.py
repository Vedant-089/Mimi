import asyncio
import json
from types import SimpleNamespace

import functions
from commands.tp_qp import (
    UNKNOWN_GENDER_POKEMON,
    TypeRegionPings,
    build_ping_payload,
    consume_channel_quest_quota,
    get_pokemons_for_regions,
    get_pokemons_for_types,
    matching_saved_channel_ids,
    parse_event_quests,
    parse_quest_pings,
    remove_completed_quests,
    tagged_saved_channels,
)


QUEST_DESCRIPTION = """B1. 🌟 Catch 18 Flying-type pokémon (1/18)
C1. Evolve 14 pokémon (0/14)
D1. Catch 14 pokémon from the Galar region (0/14)
E1. 🌙 Catch 15 Ice-type pokémon (0/15)
A2. Catch 43 pokémon (2/43)
C2. 🌙 Catch 14 Poison-type pokémon (0/14)
D2. Catch 13 Unknown gender pokémon (1/13)
E2. 🌙 Catch 17 Ghost-type pokémon (0/17)
A3. 🌟 Catch 13 Fairy-type pokémon (0/13)
B3. Catch 25 Female gender pokémon (1/25)
D3. Catch 17 pokémon from the Hoenn region (2/17)
E3. 🌟 Catch 16 Water-type pokémon (0/16)
A4. Catch 15 Rock-type pokémon (0/15)
C4. 🌟 Catch 18 Bug-type pokémon (0/18)
D4. 🌙 Catch 12 Dark-type pokémon (0/12)
E4. 🌟 Catch 16 Grass-type pokémon (0/16)
A5. 🌙 Catch 14 Psychic-type pokémon (0/14)
B5. Catch 12 pokémon from the Alola region (0/12)
C5. Catch 18 pokémon from the Kalos region (0/18)
D5. Release 19 pokémon (0/19)
E5. Catch 16 pokémon from the Unova region (0/16)"""


def make_message(description):
    embed = SimpleNamespace(
        title="Quests",
        description=description,
        fields=[],
        footer=SimpleNamespace(text=""),
        author=SimpleNamespace(name=""),
    )
    return SimpleNamespace(
        content="",
        embeds=[embed],
        message_snapshots=[],
    )


def test_parse_exact_quest_embed():
    types, regions, type_requirements, region_requirements = parse_quest_pings(
        make_message(QUEST_DESCRIPTION)
    )

    assert types == {
        "flying", "ice", "poison", "ghost", "fairy", "water",
        "rock", "bug", "dark", "grass", "psychic",
    }
    assert regions == {"galar", "hoenn", "alola", "kalos", "unova"}
    assert type_requirements == {
        "flying": 17, "ice": 15, "poison": 14, "ghost": 17,
        "fairy": 13, "water": 16, "rock": 15, "bug": 18,
        "dark": 12, "grass": 16, "psychic": 14,
    }
    assert region_requirements == {
        "galar": 14, "hoenn": 15, "alola": 12, "kalos": 18, "unova": 16,
    }


def test_parse_embed_fields_and_case_insensitive_names():
    message = make_message("")
    message.embeds[0].fields = [
        SimpleNamespace(name="Quest list", value="Catch 2 FLYING-TYPE pokemon (0/2)"),
        SimpleNamespace(name="Regions", value="Catch pokemon from the GALAR region (0/3)"),
    ]

    types, regions, type_requirements, region_requirements = parse_quest_pings(message)

    assert types == {"flying"}
    assert regions == {"galar"}
    assert type_requirements == {"flying": 2}
    assert region_requirements == {"galar": 3}


def test_parse_component_backed_message_without_embeds():
    message = SimpleNamespace(
        content="",
        embeds=[],
        message_snapshots=[],
        components=[
            SimpleNamespace(
                to_dict=lambda: {
                    "type": 1,
                    "components": [
                        {
                            "label": "B1. Catch 18 Flying-type pokemon (1/18)",
                        },
                        {
                            "label": (
                                "D1. Catch 14 pokemon from the Galar region (0/14)"
                            ),
                        },
                    ],
                }
            )
        ],
    )

    types, regions, type_requirements, region_requirements = parse_quest_pings(
        message
    )

    assert types == {"flying"}
    assert regions == {"galar"}
    assert type_requirements == {"flying": 17}
    assert region_requirements == {"galar": 14}


def test_ping_payload_resolves_canonical_keys():
    type_payload = build_ping_payload({"flying"}, "types", get_pokemons_for_types)
    region_payload = build_ping_payload({"galar"}, "regions", get_pokemons_for_regions)

    assert '"types": ["flying"]' in type_payload
    assert '"regions": ["galar"]' in region_payload


def test_parse_event_quests_separates_catchable_and_non_catch_quests():
    quests, non_catch = parse_event_quests(make_message(QUEST_DESCRIPTION))

    assert {
        (quest["code"], quest["kind"], quest["name"], quest["remaining"])
        for quest in quests
    } == {
        ("b1", "type", "flying", 17),
        ("d1", "region", "galar", 14),
        ("d2", "unknown_gender", "unknown gender", 12),
        ("e1", "type", "ice", 15),
        ("c2", "type", "poison", 14),
        ("e2", "type", "ghost", 17),
        ("a3", "type", "fairy", 13),
        ("d3", "region", "hoenn", 15),
        ("e3", "type", "water", 16),
        ("a4", "type", "rock", 15),
        ("c4", "type", "bug", 18),
        ("d4", "type", "dark", 12),
        ("e4", "type", "grass", 16),
        ("a5", "type", "psychic", 14),
        ("b5", "region", "alola", 12),
        ("c5", "region", "kalos", 18),
        ("e5", "region", "unova", 16),
    }
    assert "evolve 14 pokémon" in non_catch
    assert "release 19 pokémon" in non_catch


def test_parse_event_quests_includes_unknown_gender_quest():
    quests, _ = parse_event_quests(
        make_message("E4. Catch 15 Unknown gender pokémon (9/15)")
    )

    assert quests == [{
        "code": "e4",
        "description": "catch 15 unknown gender pokémon",
        "kind": "unknown_gender",
        "name": "unknown gender",
        "remaining": 6,
    }]
    assert UNKNOWN_GENDER_POKEMON


def test_remove_completed_quest_removes_queued_inactive_quests():
    state = {
        "quests": [
            {"code": "a4", "kind": "type", "name": "rock", "remaining": 15},
            {"code": "b1", "kind": "type", "name": "flying", "remaining": 17},
        ],
        "active": [
            {"code": "a4", "kind": "type", "name": "rock", "remaining": 15}
        ],
    }

    assert remove_completed_quests(state, "B1") is True
    assert [quest["code"] for quest in state["quests"]] == ["a4"]


class _QuestPingDB:
    def __init__(self, server_id):
        self.server_id = server_id
        self.payloads = {
            "type_pings": json.dumps({
                "types": ["dark"],
                "pokemons": ["dark-pokemon"],
            }),
            "region_pings": json.dumps({
                "regions": ["sinnoh"],
                "pokemons": ["sinnoh-pokemon"],
            }),
        }

    async def fetchrow(self, query, server_id, user_id):
        key = "type_pings" if "type_pings" in query else "region_pings"
        return {key: self.payloads[key]}

    async def execute(self, query, server_id, user_id, payload):
        key = "type_pings" if "type_pings" in query else "region_pings"
        self.payloads[key] = payload

    async def fetch(self, query):
        return [{
            "serverid": self.server_id,
            "userid": 2,
            "type_pings": self.payloads["type_pings"],
            "region_pings": self.payloads["region_pings"],
        }]


def test_grind_support_limits_overlapping_type_and_region_pings():
    async def exercise():
        server_id = next(iter(functions.GRIND_SUPPORT_SERVER_IDS))
        db = _QuestPingDB(server_id)
        async with functions._quest_requirements_lock:
            functions._quest_requirements.clear()
            functions._quest_requirements[(server_id, 2)] = {
                "types": {"dark": 18},
                "regions": {"sinnoh": 18},
            }

        results = []
        for _ in range(19):
            results.append(
                await functions.limit_quest_ping_users(
                    server_id,
                    {2},
                    db,
                    [2],
                    [2],
                    ["dark"],
                    ["sinnoh"],
                )
            )
        return results

    results = asyncio.run(exercise())

    assert results[:18] == [([2], [2])] * 18
    assert results[18] == ([], [])


def test_matching_saved_channel_ids_dedupes_current_quest_channels():
    channel_data = {
        "Dark": [11, "12", 11],
        "sinnoh": [12, 13],
        "water": [99],
    }

    assert matching_saved_channel_ids(channel_data, ["dark", "Sinnoh"]) == [11, 12, 13]


def test_tagged_saved_channels_marks_overlapping_type_and_region():
    tagged = tagged_saved_channels(
        {"dark": [11, 12], "sinnoh": [12, 13]},
        ["dark"],
        ["sinnoh"],
    )

    assert tagged == [
        (11, {"dark"}, set()),
        (12, {"dark"}, {"sinnoh"}),
        (13, set(), {"sinnoh"}),
    ]


def test_consume_channel_quest_quota_counts_dual_match_once_per_quest():
    remaining_types = {"dark": 14}
    remaining_regions = {"sinnoh": 10}

    consume_types, consume_regions = consume_channel_quest_quota(
        remaining_types,
        remaining_regions,
        {"dark"},
        {"sinnoh"},
    )

    assert consume_types == {"dark"}
    assert consume_regions == {"sinnoh"}
    remaining_types["dark"] -= 1
    remaining_regions["sinnoh"] -= 1
    assert remaining_types["dark"] == 13
    assert remaining_regions["sinnoh"] == 9

    assert consume_channel_quest_quota(
        {"dark": 0},
        {"sinnoh": 0},
        {"dark"},
        {"sinnoh"},
    ) == (set(), set())


def test_grind_support_notify_pings_visible_guild_channels_once():
    class FakeChannel:
        def __init__(self, channel_id, guild):
            self.id = channel_id
            self.guild = guild
            self.sent = []

        async def send(self, content):
            self.sent.append(content)

    class FakeGuild:
        def __init__(self, guild_id):
            self.id = guild_id
            self.channels = {}

        def get_channel(self, channel_id):
            return self.channels.get(channel_id)

        def get_thread(self, channel_id):
            return None

    class FakeBot:
        def __init__(self):
            self.channels = {}

        def get_channel(self, channel_id):
            return self.channels.get(channel_id)

    guild = FakeGuild(1)
    other_guild = FakeGuild(2)
    local_channel = FakeChannel(11, guild)
    thread_channel = FakeChannel(12, guild)
    foreign_channel = FakeChannel(99, other_guild)
    guild.channels[11] = local_channel

    cog = TypeRegionPings.__new__(TypeRegionPings)
    cog.bot = FakeBot()
    cog.bot.channels[12] = thread_channel
    cog.bot.channels[99] = foreign_channel
    cog._read_type_channel_data = lambda: {
        "dark": [11, 12, 99, 11],
        "water": [100],
    }
    persisted = []

    async def fake_persist(server_id, user_id, remaining_types, remaining_regions):
        persisted.append((server_id, user_id, dict(remaining_types), dict(remaining_regions)))

    cog._persist_grind_support_remaining = fake_persist
    ctx = SimpleNamespace(
        guild=guild,
        author=SimpleNamespace(id=2, mention="<@2>"),
    )

    sent = asyncio.run(
        cog._notify_existing_grind_support_channels(ctx, {"dark": 18}, {})
    )

    assert sent == 2
    assert local_channel.sent == ["<@2>"]
    assert thread_channel.sent == ["<@2>"]
    assert foreign_channel.sent == []
    assert persisted == [(1, 2, {"dark": 16}, {})]


def _fake_notify_cog(channel_map, channel_data):
    class FakeChannel:
        def __init__(self, channel_id, guild):
            self.id = channel_id
            self.guild = guild
            self.sent = []

        async def send(self, content):
            self.sent.append(content)

    class FakeGuild:
        def __init__(self, guild_id):
            self.id = guild_id
            self.channels = {}

        def get_channel(self, channel_id):
            return self.channels.get(channel_id)

        def get_thread(self, channel_id):
            return None

    class FakeBot:
        def get_channel(self, channel_id):
            return None

    guild = FakeGuild(1)
    channels = {
        channel_id: FakeChannel(channel_id, guild)
        for channel_id in channel_map
    }
    guild.channels = channels
    persisted = []
    cog = TypeRegionPings.__new__(TypeRegionPings)
    cog.bot = FakeBot()
    cog._read_type_channel_data = lambda: channel_data

    async def fake_persist(server_id, user_id, remaining_types, remaining_regions):
        persisted.append((dict(remaining_types), dict(remaining_regions)))

    cog._persist_grind_support_remaining = fake_persist
    ctx = SimpleNamespace(
        guild=guild,
        author=SimpleNamespace(id=2, mention="<@2>"),
    )
    return cog, ctx, channels, persisted


def test_grind_support_notify_caps_pings_to_remaining_needed():
    cog, ctx, channels, persisted = _fake_notify_cog(
        (11, 12, 13),
        {"dark": [11, 12, 13]},
    )

    sent = asyncio.run(
        cog._notify_existing_grind_support_channels(ctx, {"dark": 2}, {})
    )

    assert sent == 2
    assert [channels[channel_id].sent for channel_id in (11, 12, 13)] == [
        ["<@2>"],
        ["<@2>"],
        [],
    ]
    assert persisted == [({"dark": 0}, {})]


def test_grind_support_notify_pings_dual_type_region_channel_once():
    cog, ctx, channels, persisted = _fake_notify_cog(
        (11, 12, 13, 14),
        {"dark": [12, 13], "sinnoh": [12, 14]},
    )

    sent = asyncio.run(
        cog._notify_existing_grind_support_channels(
            ctx,
            {"dark": 1},
            {"sinnoh": 1},
        )
    )

    assert sent == 1
    assert [channels[channel_id].sent for channel_id in (11, 12, 13, 14)] == [
        [],
        ["<@2>"],
        [],
        [],
    ]
    assert persisted == [({"dark": 0}, {"sinnoh": 0})]


def test_grind_support_backfill_requires_a_matching_remaining_quest():
    async def exercise():
        server_id = next(iter(functions.GRIND_SUPPORT_SERVER_IDS))
        db = _QuestPingDB(server_id)
        async with functions._quest_requirements_lock:
            functions._quest_requirements.clear()
            functions._quest_requirements[(server_id, 2)] = {
                "types": {"dark": 1},
                "regions": {},
            }

        return await functions.limit_quest_ping_users(
            server_id,
            {2},
            db,
            [2],
            [2],
            ["water"],
            ["sinnoh"],
            require_matching_quest=True,
        )

    assert asyncio.run(exercise()) == ([], [])
