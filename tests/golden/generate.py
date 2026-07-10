"""Regenerate the golden fixture JSONs: uv run python tests/golden/generate.py

The two real ~20-player Tournaments' website data proved unrecoverable
(issue #13), so the fixtures are hand-built stand-ins of the same shape:
~20 players, 5 Swiss Rounds, Byes, mid-event Drops with TO-Assigned Results
for the ghosted opponents, drawn Matches, and 2-0/2-1 spreads.

Pairings come from the engine's seeded pairing (there is no other source of
pairings — the engine accepts no external ones), results from the scripted
rule below, and the expected Standings from the independent reference
implementation in ``reference.py``. Generation fails loudly if the engine
and the reference disagree, and the replay test re-checks that agreement on
every run; the JSONs are otherwise frozen artifacts, regenerated only when
pairing or scoring policy intentionally changes.
"""

import json
from fractions import Fraction
from pathlib import Path

from reference import ExpectedStanding, PlayedMatch, compute_standings

from multiverse_bot.engine import TournamentEngine

TOURNAMENTS = [
    {
        "file": "tournament_1.json",
        "name": "Multiverse Weekly League I",
        "seed": 4217,
        # Registration order doubles as the scripted strength order.
        "players": [
            "ahri",
            "akali",
            "ashe",
            "braum",
            "caitlyn",
            "darius",
            "draven",
            "ekko",
            "ezreal",
            "fiora",
            "galio",
            "garen",
            "janna",
            "jinx",
            "karma",
            "leona",
            "lucian",
            "lulu",
            "lux",
            "malphite",
        ],
        # Round -> players the TO drops right after its Pairings post; their
        # Match that Round resolves by a TO-Assigned 2-0 loss (the ghosted
        # opponent must not be punished), and they are never paired again.
        "drops": {3: ["galio"], 5: ["janna"]},
    },
    {
        "file": "tournament_2.json",
        "name": "Multiverse Weekly League II",
        "seed": 90210,
        "players": [
            "morgana",
            "nami",
            "nasus",
            "olaf",
            "orianna",
            "poppy",
            "quinn",
            "rakan",
            "rell",
            "renekton",
            "riven",
            "samira",
            "sejuani",
            "senna",
            "sett",
            "shen",
            "sona",
            "soraka",
            "swain",
        ],
        "drops": {4: ["olaf"]},
    },
]


def scripted_result(
    round_number: int, index_a: int, index_b: int
) -> tuple[bool, int, int, int]:
    """(stronger wins?, games won-lost-drawn) for a Match, deterministically.

    Strength follows registration order (lower index is stronger), with
    periodic draws, upsets, and a 2-0/2-1 mix so the Tiebreaker math gets
    real spread. The constants are arbitrary; the JSONs freeze the outcome.
    """
    low, high = sorted((index_a, index_b))
    key = round_number * 37 + low * 7 + high * 13
    if key % 17 == 0:
        return True, 1, 1, 1  # a drawn Match; the "winner" flag is unused
    stronger_wins = key % 5 != 0
    if key % 3 == 0:
        return stronger_wins, 2, 0, 0
    return stronger_wins, 2, 1, 0


def generate(config: dict) -> dict:
    engine = TournamentEngine()
    players: list[str] = config["players"]
    tournament_id = engine.create_tournament(name=config["name"])
    engine.open_registration(tournament_id)
    for player in players:
        engine.register_player(tournament_id, player)
        engine.submit_deck(tournament_id, player, f"{player}'s decklist")
    engine.start_tournament(tournament_id, seed=config["seed"])

    round_count = engine.tournament(tournament_id).round_count
    assert round_count is not None
    rounds = []
    for round_number in range(1, round_count + 1):
        matches = engine.pairings(tournament_id, round_number)
        drops = config["drops"].get(round_number, ())
        for player in drops:
            engine.drop_player(tournament_id, player, dropped_by="the-to")
        recorded = []
        for match in matches:
            if match.is_bye:
                recorded.append(_record(match, "bye", match.player_a, 2, 0, 0))
                continue
            assert match.player_b is not None
            dropper = next(
                (p for p in drops if p in (match.player_a, match.player_b)), None
            )
            if dropper is not None:
                opponent = (
                    match.player_b if dropper == match.player_a else match.player_a
                )
                engine.assign_result(
                    tournament_id, match.match_id, "the-to", opponent, 2, 0
                )
                recorded.append(_record(match, "assigned", opponent, 2, 0, 0))
                continue
            index_a = players.index(match.player_a)
            index_b = players.index(match.player_b)
            stronger_wins, won, lost, drawn = scripted_result(
                round_number, index_a, index_b
            )
            if drawn and won == lost:
                winner = None
                engine.report_result(
                    tournament_id,
                    match.match_id,
                    match.player_a,
                    None,
                    won,
                    lost,
                    drawn,
                )
                engine.confirm_result(tournament_id, match.match_id, match.player_b)
            else:
                stronger, weaker = (
                    (match.player_a, match.player_b)
                    if index_a < index_b
                    else (match.player_b, match.player_a)
                )
                winner = stronger if stronger_wins else weaker
                engine.report_result(
                    tournament_id, match.match_id, winner, winner, won, lost
                )
                loser = match.player_b if winner == match.player_a else match.player_a
                engine.confirm_result(tournament_id, match.match_id, loser)
            recorded.append(_record(match, "reported", winner, won, lost, drawn))
        rounds.append(
            {"round": round_number, "drops": list(drops), "matches": recorded}
        )

    assert engine.tournament(tournament_id).phase == "completed"
    expected = compute_standings(
        players,
        [
            PlayedMatch(
                m["player_a"],
                m["player_b"],
                m["winner"],
                m["games"][0],
                m["games"][1],
                m["games"][2],
            )
            for r in rounds
            for m in r["matches"]
        ],
    )
    actual = [
        ExpectedStanding(
            row.rank, row.player_id, row.match_points, row.omw, row.gw, row.ogw
        )
        for row in engine.standings(tournament_id)
    ]
    assert actual == expected, "engine and reference disagree; do not freeze this"

    return {
        "name": config["name"],
        "game": "riftbound",
        "seed": config["seed"],
        "round_count": round_count,
        "players": players,
        "rounds": rounds,
        "expected_standings": [
            {
                "rank": row.rank,
                "player": row.player_id,
                "match_points": row.match_points,
                "omw": str(Fraction(row.omw)),
                "gw": str(Fraction(row.gw)),
                "ogw": str(Fraction(row.ogw)),
            }
            for row in expected
        ],
    }


def _record(
    match, via: str, winner: str | None, won: int, lost: int, drawn: int
) -> dict:
    return {
        "match_id": match.match_id,
        "player_a": match.player_a,
        "player_b": match.player_b,
        "winner": winner,
        "games": [won, lost, drawn],
        "via": via,
    }


def main() -> None:
    for config in TOURNAMENTS:
        fixture = generate(config)
        path = Path(__file__).parent / config["file"]
        path.write_text(json.dumps(fixture, indent=2) + "\n")
        draws = sum(
            1 for r in fixture["rounds"] for m in r["matches"] if m["winner"] is None
        )
        byes = sum(
            1 for r in fixture["rounds"] for m in r["matches"] if m["player_b"] is None
        )
        print(
            f"{path.name}: {len(fixture['players'])} players, "
            f"{fixture['round_count']} rounds, {draws} draws, {byes} byes"
        )


if __name__ == "__main__":
    main()
