"""Self-tests for the golden fixtures' reference standings implementation.

The reference (``tests/golden/reference.py``) is the independent oracle the
golden-fixture tests compare the engine against, so it must itself be pinned
to hand-computed numbers. The scenarios and expected values here are the
worked examples of ``test_standings.py``, restated as explicit match lists —
every Fraction below was derived by hand in that file's comments.
"""

from fractions import Fraction

from golden.reference import ExpectedStanding, PlayedMatch, compute_standings


def test_worked_example_gw_breaks_a_match_point_and_omw_tie() -> None:
    # Records: A 2-0 (6 pts), B 1-1 (3), C 1-1 (3), D 0-2 (0); B and C tie on
    # Match Points AND OMW%, C's 3/5 GW% beats B's 1/2 (test_standings.py).
    matches = [
        PlayedMatch("a", "b", winner="a", games_won=2, games_lost=1),
        PlayedMatch("c", "d", winner="c", games_won=2, games_lost=0),
        PlayedMatch("a", "c", winner="a", games_won=2, games_lost=1),
        PlayedMatch("b", "d", winner="b", games_won=2, games_lost=1),
    ]
    assert compute_standings(("a", "b", "c", "d"), matches) == [
        ExpectedStanding(1, "a", 6, Fraction(1, 2), Fraction(2, 3), Fraction(11, 20)),
        ExpectedStanding(2, "c", 3, Fraction(2, 3), Fraction(3, 5), Fraction(1, 2)),
        ExpectedStanding(3, "b", 3, Fraction(2, 3), Fraction(1, 2), Fraction(1, 2)),
        ExpectedStanding(4, "d", 0, Fraction(1, 2), Fraction(1, 5), Fraction(11, 20)),
    ]


def test_worked_example_byes_score_points_but_leave_tiebreakers() -> None:
    # Byes pay 3 Match Points but vanish from the byed player's own MW% and
    # GW%; A and B tie on 3 points and A's 2/3 OMW% wins (test_standings.py).
    matches = [
        PlayedMatch("a", "b", winner="a", games_won=2, games_lost=0),
        PlayedMatch("c", None, winner="c", games_won=2, games_lost=0),
        PlayedMatch("b", None, winner="b", games_won=2, games_lost=0),
        PlayedMatch("c", "a", winner="c", games_won=2, games_lost=1),
    ]
    assert compute_standings(("a", "b", "c"), matches) == [
        ExpectedStanding(1, "c", 6, Fraction(1, 2), Fraction(2, 3), Fraction(3, 5)),
        ExpectedStanding(2, "a", 3, Fraction(2, 3), Fraction(3, 5), Fraction(1, 2)),
        ExpectedStanding(3, "b", 3, Fraction(1, 2), Fraction(0), Fraction(3, 5)),
    ]


def test_players_tied_through_the_whole_stack_share_the_rank() -> None:
    # All 2-0 wins: A beats B and C, B and C split with D — B and C end with
    # identical stacks (3 pts, OMW 2/3, GW 1/2, OGW 2/3), share rank 2 in
    # registration order, and D takes rank 4 (test_standings.py).
    matches = [
        PlayedMatch("a", "b", winner="a", games_won=2, games_lost=0),
        PlayedMatch("c", "d", winner="c", games_won=2, games_lost=0),
        PlayedMatch("a", "c", winner="a", games_won=2, games_lost=0),
        PlayedMatch("b", "d", winner="b", games_won=2, games_lost=0),
    ]
    assert compute_standings(("a", "b", "c", "d"), matches) == [
        ExpectedStanding(1, "a", 6, Fraction(1, 2), Fraction(1), Fraction(1, 2)),
        ExpectedStanding(2, "b", 3, Fraction(2, 3), Fraction(1, 2), Fraction(2, 3)),
        ExpectedStanding(2, "c", 3, Fraction(2, 3), Fraction(1, 2), Fraction(2, 3)),
        ExpectedStanding(4, "d", 0, Fraction(1, 2), Fraction(0), Fraction(1, 2)),
    ]


def test_a_drawn_match_splits_points_and_counts_games_played_not_won() -> None:
    # 1-1-1: each side holds 1 Match Point, GW% 1/3 (the drawn game is played
    # but not won), and the opponent's 1/3 MW% sits exactly at the floor.
    matches = [
        PlayedMatch("a", "b", winner=None, games_won=1, games_lost=1, games_drawn=1),
    ]
    assert compute_standings(("a", "b"), matches) == [
        ExpectedStanding(1, "a", 1, Fraction(1, 3), Fraction(1, 3), Fraction(1, 3)),
        ExpectedStanding(1, "b", 1, Fraction(1, 3), Fraction(1, 3), Fraction(1, 3)),
    ]


def test_a_player_with_no_matches_rates_zero_everywhere() -> None:
    # Registered but never paired (e.g. registered into a voided round):
    # empty denominators are 0, never floored for the player's own rates.
    matches = [
        PlayedMatch("a", "b", winner="a", games_won=2, games_lost=0),
    ]
    assert compute_standings(("a", "b", "c"), matches)[2] == ExpectedStanding(
        3, "c", 0, Fraction(0), Fraction(0), Fraction(0)
    )
