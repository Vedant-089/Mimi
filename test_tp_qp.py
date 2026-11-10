from types import SimpleNamespace

from commands.tp_qp import (
    UNKNOWN_GENDER_POKEMON,
    build_ping_payload,
    get_pokemons_for_regions,
    get_pokemons_for_types,
    parse_event_quests,
    parse_quest_pings,
    remove_completed_quests,
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
