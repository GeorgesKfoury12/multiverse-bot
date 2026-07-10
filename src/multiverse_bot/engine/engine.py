"""Pure, game-agnostic tournament-engine facade.

Commands validate against current state and append an action to the history;
state changes happen only by applying actions, so replaying a history through
a fresh engine reproduces identical state. Pairing randomness comes from the
seed recorded in the history, never from ambient entropy.
"""

import random
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from fractions import Fraction

from multiverse_bot.engine.actions import (
    Action,
    DeckSubmitted,
    PlayerDropped,
    PlayerRegistered,
    ResultAssigned,
    ResultConfirmed,
    ResultDisputed,
    ResultReported,
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
    """Snapshot of one Tournament, as exposed by queries.

    ``players`` is everyone who registered; ``dropped`` (in drop order) marks
    those who have left — they stay in ``players`` and in Standings.
    """

    tournament_id: str
    name: str
    phase: str
    players: tuple[str, ...]
    round_count: int | None = None
    current_round: int | None = None
    dropped: tuple[str, ...] = ()


@dataclass(frozen=True)
class Match:
    """One Match of a Round's Pairings, with its Reported Result if any.

    ``status`` walks the result flow: ``awaiting_report`` (no result yet) ->
    ``pending`` (reported, awaiting the opponent) -> ``confirmed``, with
    ``disputed`` flagging the Match for TO resolution. The result fields show
    the reported or confirmed result; ``winner is None`` on a confirmed
    result is a drawn Match (e.g. 1-1-1). ``reported_by`` is the reporting
    player, or None for Assigned Results and Byes.

    A Bye is a Match with no opponent (``player_b is None``): it comes
    pre-confirmed as a win for ``player_a`` at the ruleset's Bye game score
    and stays marked so Tiebreaker exclusion can find it later.
    """

    match_id: str
    round_number: int
    player_a: str
    player_b: str | None
    status: str = "awaiting_report"
    winner: str | None = None
    games_won: int | None = None
    games_lost: int | None = None
    games_drawn: int | None = None
    reported_by: str | None = None

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


@dataclass(frozen=True)
class _Result:
    """A result's content: winner (None for a drawn Match) and game score."""

    winner: str | None
    games_won: int
    games_lost: int
    games_drawn: int


@dataclass
class _MatchResult:
    """One Match's place in the result flow: pending | disputed | confirmed."""

    status: str
    result: _Result
    reported_by: str | None


def _match_points_for(result: _Result, player: str, ruleset: Ruleset) -> int:
    if result.winner is None:
        return ruleset.match_points_draw
    if player == result.winner:
        return ruleset.match_points_win
    return ruleset.match_points_loss


def _games_won_by(result: _Result, player: str) -> int:
    # A drawn Match's game score is symmetric (e.g. 1-1-1), so the winner's
    # side of the score reads correctly for both players.
    if result.winner is None or player == result.winner:
        return result.games_won
    return result.games_lost


@dataclass
class _TournamentState:
    tournament_id: str
    name: str
    ruleset: Ruleset
    phase: str = "registration"
    players: list[str] = field(default_factory=list)
    decks: dict[str, str] = field(default_factory=dict)
    seed: int | None = None
    round_count: int | None = None
    current_round: int | None = None
    # Drop order, kept as a list so replay reproduces identical snapshots.
    dropped: list[str] = field(default_factory=list)
    rounds: dict[int, list[Match]] = field(default_factory=dict)
    matches_by_id: dict[str, Match] = field(default_factory=dict)
    results_by_match: dict[str, _MatchResult] = field(default_factory=dict)
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

    def submit_deck(self, tournament_id: str, player_id: str, deck: str) -> None:
        """The player locks in their Deck; resubmitting before the start
        replaces it (latest wins). Decks are immutable once the Tournament
        starts — they are Revealed as submitted, open-decklist."""
        tournament = self._tournament_state(tournament_id)
        if tournament.phase != "registration":
            raise EngineError(
                f"{tournament_id} has started; Decks are Revealed and immutable"
            )
        if player_id not in tournament.players:
            raise EngineError(f"{player_id} is not registered in {tournament_id}")
        self._record(DeckSubmitted(tournament_id, player_id, deck))

    def start_tournament(
        self, tournament_id: str, seed: int, round_count: int | None = None
    ) -> str | None:
        """Start the Tournament; ``round_count`` overrides the standard Swiss
        count (spec #1 story 15, ADR-0002).

        Returns a warning — not an error, the schedule is the TO's call — when
        the override is too short for a sole undefeated winner to be possible.
        """
        tournament = self._tournament_state(tournament_id)
        if tournament.phase != "registration":
            raise EngineError(f"{tournament_id} has already started")
        player_count = len(tournament.players)
        if player_count < 2:
            raise EngineError(f"{tournament_id} needs at least 2 players to start")
        missing = [p for p in tournament.players if p not in tournament.decks]
        if missing:
            # Naming exactly who is missing hands the TO their chase list.
            raise EngineError(
                f"{tournament_id} cannot start: no Deck from {', '.join(missing)}"
            )
        warning = None
        if round_count is not None:
            # Fresh opponents run out after a round robin: n-1 rounds for an
            # even field, n with each player Byeing once for an odd one.
            max_rounds = player_count if player_count % 2 else player_count - 1
            if not 1 <= round_count <= max_rounds:
                raise EngineError(
                    f"round count must be between 1 and {max_rounds} "
                    f"for {player_count} players"
                )
            standard = tournament.ruleset.swiss_round_count(player_count)
            if round_count < standard:
                warning = (
                    f"{round_count} rounds cannot single out an undefeated "
                    f"winner among {player_count} players; the standard Swiss "
                    f"count is {standard}"
                )
        self._record(TournamentStarted(tournament_id, seed, round_count))
        return warning

    def report_result(
        self,
        tournament_id: str,
        match_id: str,
        reported_by: str,
        winner: str | None,
        games_won: int,
        games_lost: int,
        games_drawn: int = 0,
    ) -> None:
        tournament, match = self._open_match(tournament_id, match_id)
        if reported_by not in (match.player_a, match.player_b):
            raise EngineError(f"{reported_by} is not playing in {match_id}")
        existing = tournament.results_by_match.get(match_id)
        if existing is not None and existing.status == "confirmed":
            raise EngineError(f"{match_id} already has a confirmed result")
        self._validate_score(
            match, tournament.ruleset, winner, games_won, games_lost, games_drawn
        )
        self._record(
            ResultReported(
                tournament_id,
                match_id,
                reported_by,
                winner,
                games_won,
                games_lost,
                games_drawn,
            )
        )

    def confirm_result(
        self, tournament_id: str, match_id: str, confirmed_by: str
    ) -> None:
        _, match = self._open_match(tournament_id, match_id)
        entry = self._unconfirmed_entry(tournament_id, match_id)
        if confirmed_by != self._opponent_of(match, entry.reported_by):
            raise EngineError(
                f"only {entry.reported_by}'s opponent can confirm {match_id}"
            )
        self._record(ResultConfirmed(tournament_id, match_id, confirmed_by))

    def dispute_result(
        self, tournament_id: str, match_id: str, disputed_by: str
    ) -> None:
        _, match = self._open_match(tournament_id, match_id)
        entry = self._unconfirmed_entry(tournament_id, match_id)
        if entry.status == "disputed":
            raise EngineError(f"{match_id} is already disputed")
        if disputed_by != self._opponent_of(match, entry.reported_by):
            raise EngineError(
                f"only {entry.reported_by}'s opponent can dispute {match_id}"
            )
        self._record(ResultDisputed(tournament_id, match_id, disputed_by))

    def confirm_result_as_to(
        self, tournament_id: str, match_id: str, actor: str
    ) -> None:
        """The TO confirms a Pending or Disputed result as reported.

        The engine records ``actor`` but cannot know Discord roles; the caller
        is responsible for only routing TOs here.
        """
        self._open_match(tournament_id, match_id)
        self._unconfirmed_entry(tournament_id, match_id)
        self._record(ResultConfirmed(tournament_id, match_id, actor))

    def assign_result(
        self,
        tournament_id: str,
        match_id: str,
        assigned_by: str,
        winner: str | None,
        games_won: int,
        games_lost: int,
        games_drawn: int = 0,
    ) -> None:
        """The TO sets or replaces the Match's result — unreported, Pending,
        Disputed, or already confirmed — any time before the Round closes.

        The engine records ``assigned_by`` but cannot know Discord roles; the
        caller is responsible for only routing TOs here.
        """
        tournament, match = self._open_match(tournament_id, match_id)
        self._validate_score(
            match, tournament.ruleset, winner, games_won, games_lost, games_drawn
        )
        self._record(
            ResultAssigned(
                tournament_id,
                match_id,
                assigned_by,
                winner,
                games_won,
                games_lost,
                games_drawn,
            )
        )

    def drop_player(self, tournament_id: str, player_id: str, dropped_by: str) -> None:
        """The player leaves the Tournament for good; irreversible.

        Takes effect between Rounds — they are never paired again, but a Match
        already underway stays on the normal result flow. ``dropped_by`` is
        the player themselves or the TO; the engine records it but cannot know
        Discord roles, so the caller routes who may drop whom.
        """
        tournament = self._tournament_state(tournament_id)
        if tournament.phase != "in_progress":
            raise EngineError(f"{tournament_id} is not in progress")
        if player_id not in tournament.players:
            raise EngineError(f"{player_id} is not registered in {tournament_id}")
        if player_id in tournament.dropped:
            raise EngineError(f"{player_id} has already dropped from {tournament_id}")
        if len(self._active_players(tournament)) - 1 < 2:
            raise EngineError(
                f"dropping {player_id} would leave {tournament_id} with fewer "
                f"than 2 players; end the Tournament early instead"
            )
        self._record(PlayerDropped(tournament_id, player_id, dropped_by))

    def end_tournament(self, tournament_id: str) -> None:
        """The TO ends the Tournament early; Standings-so-far become final.

        Legal only between Rounds — rejected while the current Round has any
        result activity beyond its pre-confirmed Bye; the TO force-closes such
        a Round (Assigned Results for its unfinished Matches) before ending.
        The untouched current Round is voided as never played.
        """
        tournament = self._tournament_state(tournament_id)
        if tournament.phase != "in_progress":
            raise EngineError(f"{tournament_id} is not in progress")
        assert tournament.current_round is not None
        touched = any(
            not match.is_bye and match.match_id in tournament.results_by_match
            for match in tournament.rounds[tournament.current_round]
        )
        if touched:
            raise EngineError(
                f"round {tournament.current_round} is in progress; force-close "
                f"it first by assigning results to its unfinished Matches"
            )
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
            dropped=tuple(state.dropped),
        )

    def deck(self, tournament_id: str, player_id: str, requested_by: str) -> str:
        """The player's Deck, as a player sees it: Sealed until the Tournament
        starts (owner-only), Revealed to anyone afterwards (open decklist)."""
        state = self._tournament_state(tournament_id)
        if state.phase == "registration" and requested_by != player_id:
            raise EngineError(
                f"{player_id}'s Deck is Sealed until {tournament_id} starts"
            )
        return self._submitted_deck(state, player_id)

    def deck_as_to(self, tournament_id: str, player_id: str) -> str:
        """The player's Deck, Sealed or Revealed — the TO sees Decks at any
        time. The engine cannot know Discord roles; the caller is responsible
        for only routing TOs here."""
        state = self._tournament_state(tournament_id)
        return self._submitted_deck(state, player_id)

    @staticmethod
    def _submitted_deck(state: _TournamentState, player_id: str) -> str:
        deck = state.decks.get(player_id)
        if deck is None:
            raise EngineError(f"{player_id} has no Deck in {state.tournament_id}")
        return deck

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
            case DeckSubmitted(
                tournament_id=tournament_id, player_id=player_id, deck=deck
            ):
                self._tournaments[tournament_id].decks[player_id] = deck
            case TournamentStarted(
                tournament_id=tournament_id, seed=seed, round_count=round_count
            ):
                state = self._tournaments[tournament_id]
                state.phase = "in_progress"
                state.seed = seed
                state.round_count = (
                    round_count
                    if round_count is not None
                    else state.ruleset.swiss_round_count(len(state.players))
                )
                state.match_points = {player: 0 for player in state.players}
                state.opponents = {player: set() for player in state.players}
                self._begin_round(state, 1)
            case ResultReported(
                tournament_id=tournament_id,
                match_id=match_id,
                reported_by=reported_by,
                winner=winner,
                games_won=games_won,
                games_lost=games_lost,
                games_drawn=games_drawn,
            ):
                state = self._tournaments[tournament_id]
                state.results_by_match[match_id] = _MatchResult(
                    status="pending",
                    result=_Result(winner, games_won, games_lost, games_drawn),
                    reported_by=reported_by,
                )
            case ResultConfirmed(tournament_id=tournament_id, match_id=match_id):
                state = self._tournaments[tournament_id]
                state.results_by_match[match_id].status = "confirmed"
                self._recompute_match_points(state)
                self._advance_if_round_complete(state)
            case ResultDisputed(tournament_id=tournament_id, match_id=match_id):
                state = self._tournaments[tournament_id]
                state.results_by_match[match_id].status = "disputed"
            case ResultAssigned(
                tournament_id=tournament_id,
                match_id=match_id,
                winner=winner,
                games_won=games_won,
                games_lost=games_lost,
                games_drawn=games_drawn,
            ):
                state = self._tournaments[tournament_id]
                state.results_by_match[match_id] = _MatchResult(
                    status="confirmed",
                    result=_Result(winner, games_won, games_lost, games_drawn),
                    reported_by=None,
                )
                self._recompute_match_points(state)
                self._advance_if_round_complete(state)
            case PlayerDropped(tournament_id=tournament_id, player_id=player_id):
                self._tournaments[tournament_id].dropped.append(player_id)
            case TournamentEnded(tournament_id=tournament_id):
                state = self._tournaments[tournament_id]
                self._void_current_round(state)
                state.phase = "completed"

    def _advance_if_round_complete(self, state: _TournamentState) -> None:
        assert state.current_round is not None and state.round_count is not None
        current_matches = state.rounds[state.current_round]
        # Only confirmed results close a Round: a Pending or Disputed result
        # holds it open (ADR-0001: no auto-confirm timers).
        if any(not self._is_confirmed(state, m.match_id) for m in current_matches):
            return
        if state.current_round == state.round_count:
            state.phase = "completed"
        else:
            self._begin_round(state, state.current_round + 1)

    def _void_current_round(self, state: _TournamentState) -> None:
        """Unwind the freshly paired Round an early end declares never played.

        Its Matches (and the pre-confirmed Bye) leave every derived structure,
        so Standings-so-far are exactly the completed Rounds' results.
        """
        assert state.current_round is not None
        voided = state.rounds.pop(state.current_round)
        for match in voided:
            del state.matches_by_id[match.match_id]
            state.results_by_match.pop(match.match_id, None)
            if match.is_bye:
                state.byes.discard(match.player_a)
            else:
                state.opponents[match.player_a].discard(match.player_b)
                state.opponents[match.player_b].discard(match.player_a)
        state.current_round = (
            state.current_round - 1 if state.current_round > 1 else None
        )
        self._recompute_match_points(state)

    def _begin_round(self, state: _TournamentState, round_number: int) -> None:
        state.current_round = round_number
        rng = random.Random(f"{state.seed}:{round_number}")

        # Score Groups from most points down, random order within each group;
        # pair_round pairs within groups where possible, minimizes pair-downs,
        # never repeats an opponent, and grants the Bye on odd counts.
        by_points: dict[int, list[str]] = {}
        for player in self._active_players(state):
            by_points.setdefault(state.match_points[player], []).append(player)
        groups: list[list[str]] = []
        for points in sorted(by_points, reverse=True):
            group = by_points[points]
            rng.shuffle(group)
            groups.append(group)

        pairing = pair_round(groups, state.opponents, state.byes)
        if pairing is None:
            # Unreachable at the standard Swiss count (ceil(log2 n) rounds
            # always admit a rematch-free pairing), but a TO round-count
            # override past it can exhaust legal pairings in adversarial
            # histories; the hard stop keeps a rematch from slipping through.
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
                # A Bye comes pre-confirmed as a win; it never blocks the Round.
                state.results_by_match[match.match_id] = _MatchResult(
                    status="confirmed",
                    result=_Result(match.player_a, *state.ruleset.bye_game_score, 0),
                    reported_by=None,
                )
                state.byes.add(match.player_a)
            else:
                state.opponents[match.player_a].add(match.player_b)
                state.opponents[match.player_b].add(match.player_a)
        self._recompute_match_points(state)

    @staticmethod
    def _active_players(state: _TournamentState) -> list[str]:
        """The pairing pool: registered players who have not dropped."""
        return [p for p in state.players if p not in state.dropped]

    @staticmethod
    def _tiebreakers(state: _TournamentState) -> dict[str, Tiebreakers]:
        # Only confirmed two-player Matches feed the Tiebreakers: Byes are
        # excluded per ADR-0002, Pending results are not results yet. Drawn
        # games count as played but not won in GW% (house policy, like the
        # drawn-Match point split in the ruleset).
        ruleset = state.ruleset
        records: dict[str, list[MatchRecord]] = {player: [] for player in state.players}
        for match, result in TournamentEngine._confirmed_results(state):
            if match.is_bye:
                continue
            assert match.player_b is not None
            games_played = result.games_won + result.games_lost + result.games_drawn
            for player, opponent in (
                (match.player_a, match.player_b),
                (match.player_b, match.player_a),
            ):
                records[player].append(
                    MatchRecord(
                        opponent=opponent,
                        match_points=_match_points_for(result, player, ruleset),
                        games_won=_games_won_by(result, player),
                        games_played=games_played,
                    )
                )
        return compute_tiebreakers(
            records,
            match_points_win=ruleset.match_points_win,
            floor=ruleset.tiebreaker_floor,
        )

    def _recompute_match_points(self, state: _TournamentState) -> None:
        # Derived from confirmed results alone, so a TO correction that
        # replaces a confirmed result never leaves stale points behind.
        ruleset = state.ruleset
        points = {player: 0 for player in state.players}
        for match, result in self._confirmed_results(state):
            if match.is_bye:
                points[match.player_a] += ruleset.match_points_win
                continue
            for player in (match.player_a, match.player_b):
                points[player] += _match_points_for(result, player, ruleset)
        state.match_points = points

    @staticmethod
    def _confirmed_results(state: _TournamentState) -> Iterator[tuple[Match, _Result]]:
        for match_id, entry in state.results_by_match.items():
            if entry.status == "confirmed":
                yield state.matches_by_id[match_id], entry.result

    @staticmethod
    def _is_confirmed(state: _TournamentState, match_id: str) -> bool:
        entry = state.results_by_match.get(match_id)
        return entry is not None and entry.status == "confirmed"

    def _unconfirmed_entry(self, tournament_id: str, match_id: str) -> _MatchResult:
        """The Match's Pending or Disputed result; there must be one."""
        state = self._tournament_state(tournament_id)
        entry = state.results_by_match.get(match_id)
        if entry is None:
            raise EngineError(f"{match_id} has no reported result")
        if entry.status == "confirmed":
            raise EngineError(f"{match_id} already has a confirmed result")
        return entry

    @staticmethod
    def _opponent_of(match: Match, player: str | None) -> str | None:
        return match.player_b if player == match.player_a else match.player_a

    def _open_match(
        self, tournament_id: str, match_id: str
    ) -> tuple[_TournamentState, Match]:
        """The Match if its Round is still open; results freeze at Round close."""
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
        return tournament, match

    @staticmethod
    def _validate_score(
        match: Match,
        ruleset: Ruleset,
        winner: str | None,
        games_won: int,
        games_lost: int,
        games_drawn: int,
    ) -> None:
        if match.is_bye:
            raise EngineError(f"{match.match_id} is a Bye; it comes pre-scored")
        if min(games_won, games_lost, games_drawn) < 0:
            raise EngineError("game counts cannot be negative")
        total_games = games_won + games_lost + games_drawn
        if total_games == 0:
            raise EngineError("a result needs at least one game")
        if total_games > ruleset.best_of:
            raise EngineError(
                f"{games_won}-{games_lost}-{games_drawn} has more than "
                f"{ruleset.best_of} games in a best-of-{ruleset.best_of} Match"
            )
        if winner is None:
            if games_won != games_lost:
                raise EngineError(
                    f"{games_won}-{games_lost}-{games_drawn} is not a drawn score"
                )
        else:
            if winner not in (match.player_a, match.player_b):
                raise EngineError(f"{winner} is not playing in {match.match_id}")
            if games_won <= games_lost:
                raise EngineError(
                    f"{games_won}-{games_lost} is not a winning score for {winner}"
                )
            # The Match ends once a player reaches the winning game count, so
            # e.g. 3-0 can never happen in a best-of-3.
            wins_needed = ruleset.best_of // 2 + 1
            if games_won > wins_needed:
                raise EngineError(
                    f"{games_won}-{games_lost} is not reachable in a "
                    f"best-of-{ruleset.best_of} Match"
                )

    @staticmethod
    def _with_result(state: _TournamentState, match: Match) -> Match:
        entry = state.results_by_match.get(match.match_id)
        if entry is None:
            return match
        result = entry.result
        return replace(
            match,
            status=entry.status,
            winner=result.winner,
            games_won=result.games_won,
            games_lost=result.games_lost,
            games_drawn=result.games_drawn,
            reported_by=entry.reported_by,
        )

    def _tournament_state(self, tournament_id: str) -> _TournamentState:
        try:
            return self._tournaments[tournament_id]
        except KeyError:
            raise EngineError(f"no such tournament: {tournament_id}") from None
