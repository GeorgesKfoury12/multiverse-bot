"""Golden-fixture tests: full ~20-player Tournaments replayed end to end.

The two real Tournaments' website data proved unrecoverable, so per issue
#13 these are hand-built stand-ins of the same shape (see
``golden/generate.py``). Each fixture freezes a whole Tournament — players,
seed, per-Round Pairings, game scores, Byes, Drops — plus its final
Standings as computed by the independent reference implementation in
``golden/reference.py``, which reimplements the ADR-0002 math from the ADR
without importing the engine.

Replaying a fixture must reproduce it exactly: the seeded Pairings Round by
Round, and the frozen Standings at the end. A pairing divergence means the
seeded pairing changed (regenerate the fixtures if that was intentional); a
Standings divergence means the engine and the reference disagree on the
Swiss math — that is the bug this suite exists to catch.

Discrepancy record (issue #13's third acceptance criterion): the fixtures'
first generation surfaced none — the engine and the reference agreed on both
Tournaments outright, so there is no bug fix or house-policy difference to
document beyond ADR-0002 itself.
"""

import json
from fractions import Fraction
from pathlib import Path
from typing import Sequence

import pytest
from conftest import register_with_deck, report_and_confirm

from golden.reference import ExpectedStanding, PlayedMatch, compute_standings
from multiverse_bot.engine import Standing, TournamentEngine

FIXTURE_DIR = Path(__file__).parent / "golden"


def load(name: str) -> dict:
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text())


@pytest.fixture(params=["tournament_1", "tournament_2"])
def tournament_fixture(request: pytest.FixtureRequest) -> dict:
    return load(request.param)


def replay(fixture: dict) -> tuple[TournamentEngine, str]:
    """Drive a fresh engine through the fixture's recorded Tournament.

    Asserts each Round's Pairings come out exactly as frozen (same seed,
    same prior results — any divergence is a pairing-determinism change),
    then feeds the results through the flow each was recorded with:
    player report + opponent confirm, or TO assignment for the ghosted
    Matches of dropped players.
    """
    engine = TournamentEngine()
    tournament_id = engine.create_tournament(name=fixture["name"], game=fixture["game"])
    engine.open_registration(tournament_id)
    for player in fixture["players"]:
        register_with_deck(engine, tournament_id, player)
    engine.start_tournament(tournament_id, seed=fixture["seed"])

    for round_data in fixture["rounds"]:
        round_number = round_data["round"]
        paired = {m.match_id: m for m in engine.pairings(tournament_id, round_number)}
        seats = [(m.match_id, m.player_a, m.player_b) for m in paired.values()]
        assert seats == [
            (m["match_id"], m["player_a"], m["player_b"]) for m in round_data["matches"]
        ], f"round {round_number} pairings diverge from the fixture"

        # Drops land right after the Pairings post, as they were recorded:
        # the dropper plays out (or is assigned) this Round, then vanishes.
        for player in round_data["drops"]:
            engine.drop_player(tournament_id, player, dropped_by="the-to")

        for match in round_data["matches"]:
            games_won, games_lost, games_drawn = match["games"]
            if match["via"] == "bye":
                continue  # pre-confirmed by the engine
            if match["via"] == "assigned":
                engine.assign_result(
                    tournament_id,
                    match["match_id"],
                    "the-to",
                    match["winner"],
                    games_won,
                    games_lost,
                    games_drawn,
                )
            elif match["winner"] is None:
                # Drawn Matches fall outside the conftest helper: nobody won,
                # so player_a reports and player_b confirms.
                engine.report_result(
                    tournament_id,
                    match["match_id"],
                    match["player_a"],
                    None,
                    games_won,
                    games_lost,
                    games_drawn,
                )
                engine.confirm_result(
                    tournament_id, match["match_id"], match["player_b"]
                )
            else:
                report_and_confirm(
                    engine,
                    tournament_id,
                    paired[match["match_id"]],
                    winner=match["winner"],
                    games_won=games_won,
                    games_lost=games_lost,
                    games_drawn=games_drawn,
                )

    return engine, tournament_id


Row = tuple[int, str, int, Fraction, Fraction, Fraction]


def frozen_standings(fixture: dict) -> list[Row]:
    return [
        (
            row["rank"],
            row["player"],
            row["match_points"],
            Fraction(row["omw"]),
            Fraction(row["gw"]),
            Fraction(row["ogw"]),
        )
        for row in fixture["expected_standings"]
    ]


def rows(standings: Sequence[Standing | ExpectedStanding]) -> list[Row]:
    """Engine Standing and reference ExpectedStanding rows share this shape."""
    return [
        (row.rank, row.player_id, row.match_points, row.omw, row.gw, row.ogw)
        for row in standings
    ]


def test_replay_reproduces_the_frozen_standings(tournament_fixture: dict) -> None:
    engine, tournament_id = replay(tournament_fixture)
    assert engine.tournament(tournament_id).phase == "completed"
    assert rows(engine.standings(tournament_id)) == frozen_standings(tournament_fixture)


def test_frozen_standings_match_the_independent_reference(
    tournament_fixture: dict,
) -> None:
    # Re-derive the Standings from the fixture's raw match data with the
    # reference oracle — no engine involved — so the frozen numbers can
    # never silently drift into being a snapshot of an engine bug.
    matches = [
        PlayedMatch(m["player_a"], m["player_b"], m["winner"], *m["games"])
        for round_data in tournament_fixture["rounds"]
        for m in round_data["matches"]
    ]
    assert rows(
        compute_standings(tournament_fixture["players"], matches)
    ) == frozen_standings(tournament_fixture)


def test_dropped_players_stay_in_the_standings(tournament_fixture: dict) -> None:
    engine, tournament_id = replay(tournament_fixture)
    dropped = [p for r in tournament_fixture["rounds"] for p in r["drops"]]
    assert list(engine.tournament(tournament_id).dropped) == dropped
    standing_players = {row.player_id for row in engine.standings(tournament_id)}
    assert standing_players == set(tournament_fixture["players"])


def test_fixtures_keep_covering_the_interesting_cases(
    tournament_fixture: dict,
) -> None:
    # A regeneration that loses draws, Byes, Drops, or Assigned Results
    # would quietly gut this suite's coverage; fail it loudly instead.
    matches = [
        m for round_data in tournament_fixture["rounds"] for m in round_data["matches"]
    ]
    assert len(tournament_fixture["players"]) >= 19, "fixtures must stay ~20 players"
    assert any(m["player_b"] is None for m in matches), "no Byes left"
    assert any(m["winner"] is None for m in matches), "no drawn Matches left"
    assert any(m["via"] == "assigned" for m in matches), "no Assigned Results left"
    assert any(r["drops"] for r in tournament_fixture["rounds"]), "no Drops left"
