from types import SimpleNamespace

from commands.tp_qp import (
    build_ping_payload,
    get_pokemons_for_regions,
    get_pokemons_for_types,
    parse_quest_pings,
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


def test_ping_payload_resolves_canonical_keys():
    type_payload = build_ping_payload({"flying"}, "types", get_pokemons_for_types)
    region_payload = build_ping_payload({"galar"}, "regions", get_pokemons_for_regions)

    assert '"types": ["flying"]' in type_payload
    assert '"regions": ["galar"]' in region_payload
