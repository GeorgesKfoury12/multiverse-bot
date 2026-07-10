"""Rematch-free Swiss pairing over the compatibility graph.

No-rematch is a hard constraint: the search is exhaustive over the graph of
players who have not yet met, so a legal pairing is produced whenever one
mathematically exists — never a greedy near-miss. Among legal pairings it
returns one of minimal pair-down cost, where a pair costs the squared
distance between its players' Score Group indices (0 within a group):
pair-downs happen only when forced, and a cascade of neighbouring-group
pair-downs beats one player dropping far below their Score Group.

Callers pass Score Groups ordered from most Match Points down, each group
already shuffled into the desired random order; the search is deterministic
from there, so pairings stay reproducible from the recorded seed. A player
missing from ``opponents`` simply has no prior opponents.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class RoundPairing:
    """The Matches of one Round: pairs to play, plus the Bye if the count was odd."""

    pairs: tuple[tuple[str, str], ...]
    bye: str | None


def pair_round(
    groups: list[list[str]],
    opponents: dict[str, set[str]],
    prior_byes: set[str],
) -> RoundPairing | None:
    """Pair a Round, granting a Bye first when the player count is odd.

    The Bye goes to the lowest-ranked player without a prior Bye; if pairing
    the rest is impossible, the next candidate up gets it instead, and only
    when no bye-less candidate works does a second Bye happen — lowest-ranked
    prior-Bye players first. Returns None when no legal pairing exists at all.
    """
    ordered = [player for group in groups for player in group]
    if len(ordered) % 2 == 0:
        pairs = find_pairing(groups, opponents)
        return None if pairs is None else RoundPairing(tuple(pairs), bye=None)

    from_lowest = list(reversed(ordered))
    candidates = [p for p in from_lowest if p not in prior_byes] + [
        p for p in from_lowest if p in prior_byes
    ]
    for bye_player in candidates:
        rest = [[p for p in group if p != bye_player] for group in groups]
        pairs = find_pairing(rest, opponents)
        if pairs is not None:
            return RoundPairing(tuple(pairs), bye=bye_player)
    return None


def find_pairing(
    groups: list[list[str]],
    opponents: dict[str, set[str]],
) -> list[tuple[str, str]] | None:
    """Minimal-pair-down rematch-free pairing of an even number of players.

    Iterative deepening on total pair-down cost: the first budget that admits
    a legal pairing is the minimum, so within-group pairings win whenever they
    exist. Returns None only when no rematch-free pairing exists.
    """
    players = [
        (player, group_index)
        for group_index, group in enumerate(groups)
        for player in group
    ]
    if len(players) % 2:
        raise ValueError("find_pairing needs an even number of players")
    worst_cost = (len(groups) - 1) ** 2 * (len(players) // 2)
    for budget in range(worst_cost + 1):
        pairs = _search(players, opponents, budget)
        if pairs is not None:
            return pairs
    return None


def _search(
    players: list[tuple[str, int]],
    opponents: dict[str, set[str]],
    budget: int,
) -> list[tuple[str, str]] | None:
    """Pair the first player with each affordable non-opponent, then recurse."""
    if not players:
        return []
    (first, first_group), rest = players[0], players[1:]
    for index, (candidate, candidate_group) in enumerate(rest):
        cost = (candidate_group - first_group) ** 2
        if cost > budget:
            break  # players are ordered by group, so it only gets costlier
        if candidate in opponents.get(first, ()):
            continue
        tail = _search(rest[:index] + rest[index + 1 :], opponents, budget - cost)
        if tail is not None:
            return [(first, candidate), *tail]
    return None
