"""Tests for the pairing algorithm, at its own seam.

The engine facade owns lifecycle behavior; this seam owns the pairing
guarantees of issue #3 — no-rematch as a hard constraint (a legal pairing is
found whenever one exists), pair-downs minimized, Bye policy — including
inputs the facade cannot produce yet, like drop patterns.
"""

import itertools
import random

from multiverse_bot.engine.pairing import find_pairing, pair_round


def opponents_from(pairs: list[tuple[str, str]]) -> dict[str, set[str]]:
    history: dict[str, set[str]] = {player: set() for pair in pairs for player in pair}
    for a, b in pairs:
        history[a].add(b)
        history[b].add(a)
    return history


def test_backtracks_across_score_groups_when_no_group_pairs_within_itself() -> None:
    # Every within-group pair is a rematch, so a naive greedy pairer fails;
    # legal cross-group pairings exist and must be found.
    groups = [["a1", "a2"], ["b1", "b2"], ["c1", "c2"]]
    opponents = opponents_from([("a1", "a2"), ("b1", "b2"), ("c1", "c2")])

    result = pair_round(groups, opponents, prior_byes=set())

    assert result is not None
    assert result.bye is None
    paired = {player for pair in result.pairs for player in pair}
    assert paired == {"a1", "a2", "b1", "b2", "c1", "c2"}
    for a, b in result.pairs:
        assert b not in opponents[a]


def group_of(groups: list[list[str]], player: str) -> int:
    return next(i for i, group in enumerate(groups) if player in group)


def cross_group_pairs(
    groups: list[list[str]], pairs: tuple[tuple[str, str], ...]
) -> list[tuple[str, str]]:
    return [(a, b) for a, b in pairs if group_of(groups, a) != group_of(groups, b)]


def test_pairs_within_score_groups_whenever_possible() -> None:
    # a1-a2 and a3-a4 have met, but a within-group pairing still exists and
    # must be preferred over any pair-down.
    groups = [["a1", "a2", "a3", "a4"], ["b1", "b2"]]
    opponents = opponents_from([("a1", "a2"), ("a3", "a4")])

    result = pair_round(groups, opponents, prior_byes=set())

    assert result is not None
    assert cross_group_pairs(groups, result.pairs) == []


def test_odd_sized_groups_pair_down_exactly_once_between_neighbours() -> None:
    groups = [["a1", "a2", "a3"], ["b1", "b2", "b3"]]
    opponents = opponents_from([])

    result = pair_round(groups, opponents, prior_byes=set())

    assert result is not None
    crossings = cross_group_pairs(groups, result.pairs)
    assert len(crossings) == 1


def test_returns_none_only_when_no_rematch_free_pairing_exists() -> None:
    opponents = opponents_from([("a1", "a2")])

    assert pair_round([["a1", "a2"]], opponents, prior_byes=set()) is None


def test_bye_skips_players_who_already_had_one() -> None:
    groups = [["a"], ["b"], ["c"]]
    opponents = opponents_from([])

    result = pair_round(groups, opponents, prior_byes={"c"})

    assert result is not None
    assert result.bye == "b"  # lowest-ranked *bye-less* player, not c


def test_bye_moves_up_when_the_lowest_candidate_leaves_the_rest_unpairable() -> None:
    # Giving the bye to c or b would force the a-b or a-c rematch; only
    # giving it to a leaves a legal pairing.
    groups = [["a"], ["b"], ["c"]]
    opponents = opponents_from([("a", "b"), ("a", "c")])

    result = pair_round(groups, opponents, prior_byes=set())

    assert result is not None
    assert result.bye == "a"
    assert result.pairs == (("b", "c"),)


def test_second_bye_happens_only_when_mathematically_unavoidable() -> None:
    # Every bye-less candidate leaves a rematch behind; c's second Bye is the
    # only legal round.
    groups = [["a"], ["b"], ["c"]]
    opponents = opponents_from([("a", "c"), ("b", "c")])

    result = pair_round(groups, opponents, prior_byes={"c"})

    assert result is not None
    assert result.bye == "c"
    assert result.pairs == (("a", "b"),)


def a_perfect_matching_exists(players: list[str], banned: set[frozenset[str]]) -> bool:
    """Brute-force oracle: some way to pair everyone avoids every banned pair."""
    if not players:
        return True
    first, rest = players[0], players[1:]
    return any(
        frozenset((first, partner)) not in banned
        and a_perfect_matching_exists([p for p in rest if p != partner], banned)
        for partner in rest
    )


def test_property_finds_a_pairing_exactly_when_one_exists() -> None:
    # Arbitrary rematch graphs, denser than Swiss ever produces, split into
    # arbitrary score groups: find_pairing succeeds iff the oracle says a
    # legal pairing exists, and never returns a rematch.
    for seed in range(150):
        rng = random.Random(seed)
        players = [f"p{i}" for i in range(rng.choice((4, 6, 8)))]
        banned = {
            frozenset(pair)
            for pair in itertools.combinations(players, 2)
            if rng.random() < rng.choice((0.2, 0.5, 0.8))
        }
        opponents = {
            p: {q for q in players if frozenset((p, q)) in banned} for p in players
        }
        split = sorted(rng.sample(range(1, len(players)), rng.randint(0, 2)))
        groups = [
            players[start:stop]
            for start, stop in itertools.pairwise((0, *split, len(players)))
        ]

        pairs = find_pairing(groups, opponents)

        if pairs is None:
            assert not a_perfect_matching_exists(players, banned), (
                f"seed {seed}: a legal pairing exists but find_pairing gave up"
            )
        else:
            assert sorted(p for pair in pairs for p in pair) == sorted(players)
            for a, b in pairs:
                assert frozenset((a, b)) not in banned, f"seed {seed}: rematch {a}-{b}"


def test_property_drop_patterns_never_force_a_rematch_or_break_bye_rules() -> None:
    # Simulated tournaments where players drop between rounds but their
    # history stays: every round still pairs rematch-free, everyone active is
    # paired exactly once or byed, and byes only happen on odd counts.
    for seed in range(60):
        rng = random.Random(seed)
        players = [f"p{i}" for i in range(rng.randint(3, 12))]
        points = {p: 0 for p in players}
        opponents: dict[str, set[str]] = {p: set() for p in players}
        prior_byes: set[str] = set()
        active = list(players)

        for _ in range(rng.randint(2, 5)):
            if len(active) < 2:
                break
            groups = []
            for score in sorted({points[p] for p in active}, reverse=True):
                group = [p for p in active if points[p] == score]
                rng.shuffle(group)
                groups.append(group)

            result = pair_round(groups, opponents, prior_byes)

            if result is None:
                # Dense enough drop patterns can make pairing genuinely
                # impossible; None is only legal when the oracle agrees.
                banned = {frozenset((a, b)) for a, bs in opponents.items() for b in bs}
                rests = (
                    [active]
                    if len(active) % 2 == 0
                    else [[p for p in active if p != bye] for bye in active]
                )
                assert not any(
                    a_perfect_matching_exists(rest, banned) for rest in rests
                ), f"seed {seed}: a legal round exists but pair_round gave up"
                break
            seen = [] if result.bye is None else [result.bye]
            assert (result.bye is not None) == (len(active) % 2 == 1)
            for a, b in result.pairs:
                assert b not in opponents[a], f"seed {seed}: rematch {a}-{b}"
                opponents[a].add(b)
                opponents[b].add(a)
                seen.extend((a, b))
                points[rng.choice((a, b))] += 3
            assert sorted(seen) == sorted(active)
            if result.bye is not None:
                points[result.bye] += 3
                prior_byes.add(result.bye)
            active = [p for p in active if rng.random() > 0.15]
