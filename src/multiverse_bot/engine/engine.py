"""Pure, game-agnostic tournament-engine facade.

Commands validate against current state and append an action to the history;
state changes happen only by applying actions, so replaying a history through
a fresh engine reproduces identical state. Pairing randomness comes from the
seed recorded in the history, never from ambient entropy.
"""

import random
from dataclasses import dataclass, field, replace
from fractions import Fraction

from multiverse_bot.engine.actions import (
    Action,
    PlayerRegistered,
    ResultSubmitted,
    TournamentCreated,
    TournamentEnded,
    TournamentStarted,
)
from multiverse_bot.engine.pairing import pair_round
from multiverse_bot.engine.ruleset import RULESETS, Ruleset
from multiverse_bot.engine.tiebreakers import (
    MatchRecord,
    Tiebreakers,
    compute_tiebreakers,
)


class EngineError(Exception):
    """A command that the tournament's current state does not allow."""


@dataclass(frozen=True)
class Tournament:
    """Snapshot of one Tournament, as exposed by queries."""

    tournament_id: str
    name: str
    phase: str
    players: tuple[str, ...]
    round_count: int | None = None
    current_round: int | None = None


@dataclass(frozen=True)
class Match:
    """One Match of a Round's Pairings; result fields are None until submitted.

    A Bye is a Match with no opponent (``player_b is None``): it comes
    pre-scored as a win for ``player_a`` at the ruleset's Bye game score and
    stays marked so Tiebreaker exclusion can find it later.
    """

    match_id: str
    round_number: int
    player_a: str
    player_b: str | None
    winner: str | None = None
    games_won: int | None = None
    games_lost: int | None = None

    @property
    def is_bye(self) -> bool:
        return self.player_b is None


@dataclass(frozen=True)
class Standing:
    """One row of a Tournament's Standings.

    Ordered by Match Points, then the Tiebreaker stack OMW% -> GW% -> OGW%
    (exact Fractions in [0, 1]); players tied through the whole stack share
    a rank.
    """

    rank: int
    player_id: str
    match_points: int
    omw: Fraction
    gw: Fraction
    ogw: Fraction


@dataclass
class _TournamentState:
    tournament_id: str
    name: str
    ruleset: Ruleset
    phase: str = "registration"
    players: list[str] = field(default_factory=list)
    seed: int | None = None
    round_count: int | None = None
    current_round: int | None = None
    rounds: dict[int, list[Match]] = field(default_factory=dict)
    matches_by_id: dict[str, Match] = field(default_factory=dict)
    results_by_match: dict[str, tuple[str, int, int]] = field(default_factory=dict)
    match_points: dict[str, int] = field(default_factory=dict)
    opponents: dict[str, set[str]] = field(default_factory=dict)
    byes: set[str] = field(default_factory=set)


class TournamentEngine:
    def __init__(self) -> None:
        self._history: list[Action] = []
        self._tournaments: dict[str, _TournamentState] = {}
        self._created_count = 0

    @classmethod
    def replay(cls, history: tuple[Action, ...]) -> "TournamentEngine":
        """Rebuild an engine from a recorded history; state comes out identical."""
        engine = cls()
        for action in history:
            engine._record(action)
        return engine

    @property
    def history(self) -> tuple[Action, ...]:
        """The ordered actions this engine's state derives from."""
        return tuple(self._history)

    # -- commands ----------------------------------------------------------

    def create_tournament(self, name: str, game: str = "riftbound") -> str:
        if game not in RULESETS:
            raise EngineError(f"no ruleset configured for game: {game}")
        tournament_id = f"T{self._created_count + 1}"
        self._record(TournamentCreated(tournament_id, name, game))
        return tournament_id

    def register_player(self, tournament_id: str, player_id: str) -> None:
        tournament = self._tournament_state(tournament_id)
        if tournament.phase != "registration":
            raise EngineError(f"{tournament_id} is no longer open for registration")
        if player_id in tournament.players:
            raise EngineError(f"{player_id} is already registered in {tournament_id}")
        self._record(PlayerRegistered(tournament_id, player_id))

    def start_tournament(self, tournament_id: str, seed: int) -> None:
        tournament = self._tournament_state(tournament_id)
        if tournament.phase != "registration":
            raise EngineError(f"{tournament_id} has already started")
        if len(tournament.players) < 2:
            raise EngineError(f"{tournament_id} needs at least 2 players to start")
        self._record(TournamentStarted(tournament_id, seed))

    def submit_result(
        self,
        tournament_id: str,
        match_id: str,
        winner: str,
        games_won: int,
        games_lost: int,
    ) -> None:
        tournament = self._tournament_state(tournament_id)
        if tournament.phase != "in_progress":
            raise EngineError(f"{tournament_id} is not in progress")
        match = tournament.matches_by_id.get(match_id)
        if match is None:
            raise EngineError(f"no such match in {tournament_id}: {match_id}")
        if match.round_number != tournament.current_round:
            raise EngineError(
                f"round {match.round_number} is closed; {match_id} is frozen"
            )
        if winner not in (match.player_a, match.player_b):
            raise EngineError(f"{winner} is not playing in {match_id}")
        if match_id in tournament.results_by_match:
            raise EngineError(f"{match_id} already has a result")
        if games_lost < 0 or games_won <= games_lost:
            raise EngineError(
                f"{games_won}-{games_lost} is not a winning score for {winner}"
            )
        self._record(
            ResultSubmitted(tournament_id, match_id, winner, games_won, games_lost)
        )

    def end_tournament(self, tournament_id: str) -> None:
        tournament = self._tournament_state(tournament_id)
        if tournament.phase != "in_progress":
            raise EngineError(f"{tournament_id} is not in progress")
        self._record(TournamentEnded(tournament_id))

    # -- queries -----------------------------------------------------------

    def tournament(self, tournament_id: str) -> Tournament:
        state = self._tournament_state(tournament_id)
        return Tournament(
            tournament_id=state.tournament_id,
            name=state.name,
            phase=state.phase,
            players=tuple(state.players),
            round_count=state.round_count,
            current_round=state.current_round,
        )

    def pairings(self, tournament_id: str, round_number: int) -> tuple[Match, ...]:
        state = self._tournament_state(tournament_id)
        if round_number not in state.rounds:
            raise EngineError(f"{tournament_id} has no round {round_number}")
        return tuple(
            self._with_result(state, match) for match in state.rounds[round_number]
        )

    def standings(self, tournament_id: str) -> tuple[Standing, ...]:
        state = self._tournament_state(tournament_id)
        if state.phase == "registration":
            raise EngineError(f"{tournament_id} has not started; no standings yet")
        tiebreakers = self._tiebreakers(state)

        def stack(player: str) -> tuple[int, Fraction, Fraction, Fraction]:
            t = tiebreakers[player]
            return (state.match_points[player], t.omw, t.gw, t.ogw)

        registration_order = {player: i for i, player in enumerate(state.players)}
        ordered = sorted(
            state.players,
            key=lambda p: (
                *(-value for value in stack(p)),
                registration_order[p],
            ),
        )
        rows = []
        previous_stack = None
        for position, player in enumerate(ordered, start=1):
            current = stack(player)
            points, omw, gw, ogw = current
            # Standard competition ranking: players tied through the whole
            # stack share the rank.
            rank = rows[-1].rank if current == previous_stack else position
            previous_stack = current
            rows.append(
                Standing(
                    rank=rank,
                    player_id=player,
                    match_points=points,
                    omw=omw,
                    gw=gw,
                    ogw=ogw,
                )
            )
        return tuple(rows)

    # -- history -----------------------------------------------------------

    def _record(self, action: Action) -> None:
        self._apply(action)
        self._history.append(action)

    def _apply(self, action: Action) -> None:
        match action:
            case TournamentCreated(tournament_id=tournament_id, name=name, game=game):
                self._tournaments[tournament_id] = _TournamentState(
                    tournament_id=tournament_id, name=name, ruleset=RULESETS[game]
                )
                self._created_count += 1
            case PlayerRegistered(tournament_id=tournament_id, player_id=player_id):
                self._tournaments[tournament_id].players.append(player_id)
            case TournamentStarted(tournament_id=tournament_id, seed=seed):
                state = self._tournaments[tournament_id]
                state.phase = "in_progress"
                state.seed = seed
                state.round_count = state.ruleset.swiss_round_count(len(state.players))
                state.match_points = {player: 0 for player in state.players}
                state.opponents = {player: set() for player in state.players}
                self._begin_round(state, 1)
            case ResultSubmitted(
                tournament_id=tournament_id,
                match_id=match_id,
                winner=winner,
                games_won=games_won,
                games_lost=games_lost,
            ):
                state = self._tournaments[tournament_id]
                state.results_by_match[match_id] = (winner, games_won, games_lost)
                match = state.matches_by_id[match_id]
                loser = match.player_b if winner == match.player_a else match.player_a
                assert loser is not None
                state.match_points[winner] += state.ruleset.match_points_win
                state.match_points[loser] += state.ruleset.match_points_loss
                self._advance_if_round_complete(state)
            case TournamentEnded(tournament_id=tournament_id):
                self._tournaments[tournament_id].phase = "completed"

    def _advance_if_round_complete(self, state: _TournamentState) -> None:
        assert state.current_round is not None and state.round_count is not None
        current_matches = state.rounds[state.current_round]
        if any(m.match_id not in state.results_by_match for m in current_matches):
            return
        if state.current_round == state.round_count:
            state.phase = "completed"
        else:
            self._begin_round(state, state.current_round + 1)

    def _begin_round(self, state: _TournamentState, round_number: int) -> None:
        state.current_round = round_number
        rng = random.Random(f"{state.seed}:{round_number}")

        # Score Groups from most points down, random order within each group;
        # pair_round pairs within groups where possible, minimizes pair-downs,
        # never repeats an opponent, and grants the Bye on odd counts.
        by_points: dict[int, list[str]] = {}
        for player in state.players:
            by_points.setdefault(state.match_points[player], []).append(player)
        groups: list[list[str]] = []
        for points in sorted(by_points, reverse=True):
            group = by_points[points]
            rng.shuffle(group)
            groups.append(group)

        pairing = pair_round(groups, state.opponents, state.byes)
        if pairing is None:
            # Unreachable while opponents come only from Swiss rounds of this
            # engine (ceil(log2 n) rounds always admit a rematch-free pairing);
            # kept as a hard stop so a future bug can't silently pair a rematch.
            raise EngineError(
                f"{state.tournament_id} has no rematch-free pairing "
                f"for round {round_number}"
            )

        seats: list[tuple[str, str | None]] = list(pairing.pairs)
        if pairing.bye is not None:
            seats.append((pairing.bye, None))
        matches = [
            Match(
                match_id=f"{state.tournament_id}-R{round_number}-M{index + 1}",
                round_number=round_number,
                player_a=player_a,
                player_b=player_b,
            )
            for index, (player_a, player_b) in enumerate(seats)
        ]
        state.rounds[round_number] = matches
        for match in matches:
            state.matches_by_id[match.match_id] = match
            if match.is_bye:
                # A Bye scores as a win immediately; it never blocks the Round.
                state.results_by_match[match.match_id] = (
                    match.player_a,
                    *state.ruleset.bye_game_score,
                )
                state.match_points[match.player_a] += state.ruleset.match_points_win
                state.byes.add(match.player_a)
            else:
                state.opponents[match.player_a].add(match.player_b)
                state.opponents[match.player_b].add(match.player_a)

    @staticmethod
    def _tiebreakers(state: _TournamentState) -> dict[str, Tiebreakers]:
        # Only completed two-player Matches feed the Tiebreakers: Byes are
        # excluded per ADR-0002, unreported Matches have nothing to count.
        ruleset = state.ruleset
        records: dict[str, list[MatchRecord]] = {player: [] for player in state.players}
        for match_id, (winner, games_won, games_lost) in state.results_by_match.items():
            match = state.matches_by_id[match_id]
            if match.is_bye:
                continue
            assert match.player_b is not None
            games_played = games_won + games_lost
            for player, opponent in (
                (match.player_a, match.player_b),
                (match.player_b, match.player_a),
            ):
                won = player == winner
                records[player].append(
                    MatchRecord(
                        opponent=opponent,
                        match_points=ruleset.match_points_win
                        if won
                        else ruleset.match_points_loss,
                        games_won=games_won if won else games_lost,
                        games_played=games_played,
                    )
                )
        return compute_tiebreakers(
            records,
            match_points_win=ruleset.match_points_win,
            floor=ruleset.tiebreaker_floor,
        )

    @staticmethod
    def _with_result(state: _TournamentState, match: Match) -> Match:
        result = state.results_by_match.get(match.match_id)
        if result is None:
            return match
        winner, games_won, games_lost = result
        return replace(match, winner=winner, games_won=games_won, games_lost=games_lost)

    def _tournament_state(self, tournament_id: str) -> _TournamentState:
        try:
            return self._tournaments[tournament_id]
        except KeyError:
            raise EngineError(f"no such tournament: {tournament_id}") from None
