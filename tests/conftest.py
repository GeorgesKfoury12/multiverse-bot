"""Shared helpers for facade-level engine tests."""

from multiverse_bot.engine import Match, TournamentEngine

PLAYERS = ("alice", "bob", "carol", "dave")


def register_with_deck(
    engine: TournamentEngine, tournament_id: str, player_id: str
) -> str:
    """Register and submit a placeholder Deck, keeping the start gate open.

    Returns the submitted Deck so assertions never hard-code its format.
    """
    deck = f"{player_id}'s decklist"
    engine.register_player(tournament_id, player_id)
    engine.submit_deck(tournament_id, player_id, deck)
    return deck


def create_tournament_with_players(
    engine: TournamentEngine, players: tuple[str, ...] = PLAYERS
) -> str:
    """Create and register only, leaving the start (and its options) to the test."""
    tournament_id = engine.create_tournament(name="Weekly Riftbound #1")
    engine.open_registration(tournament_id)
    for player_id in players:
        register_with_deck(engine, tournament_id, player_id)
    return tournament_id


def start_four_player_tournament(engine: TournamentEngine, seed: int = 42) -> str:
    tournament_id = create_tournament_with_players(engine)
    engine.start_tournament(tournament_id, seed=seed)
    return tournament_id


def confirm_round(
    engine: TournamentEngine, tournament_id: str, round_number: int
) -> None:
    """Report and confirm a player_a win for every playable Match of the Round."""
    for match in engine.pairings(tournament_id, round_number):
        if match.is_bye:
            continue
        report_and_confirm(
            engine,
            tournament_id,
            match,
            winner=match.player_a,
            games_won=2,
            games_lost=0,
        )


def report_last_pairable_round(engine: TournamentEngine) -> tuple[str, Match]:
    """Walk a Tournament to the corner of issues #36/#37: 3 players on a
    3-Round schedule, where the Round 1 loser drops, so Round 2 pairs the last
    two fresh opponents and Round 3 has no rematch-free pairing. Returns the
    tournament id and Round 2's sole Match, reported (player_a winning 2-0)
    but not yet confirmed — the next confirmation closes the last pairable
    Round.
    """
    tournament_id = engine.create_tournament(name="Weekly Riftbound #1")
    engine.open_registration(tournament_id)
    for player_id in PLAYERS[:3]:
        register_with_deck(engine, tournament_id, player_id)
    engine.start_tournament(tournament_id, seed=42, round_count=3)
    round_one = engine.pairings(tournament_id, round_number=1)
    (played,) = [m for m in round_one if not m.is_bye]
    engine.drop_player(tournament_id, played.player_b, dropped_by=played.player_b)
    report_and_confirm(
        engine, tournament_id, played, winner=played.player_a, games_won=2, games_lost=0
    )
    (last_pairable,) = engine.pairings(tournament_id, round_number=2)
    engine.report_result(
        tournament_id,
        last_pairable.match_id,
        reported_by=last_pairable.player_a,
        winner=last_pairable.player_a,
        games_won=2,
        games_lost=0,
    )
    return tournament_id, last_pairable


def report_and_confirm(
    engine: TournamentEngine,
    tournament_id: str,
    match: Match,
    winner: str,
    games_won: int,
    games_lost: int,
    games_drawn: int = 0,
) -> None:
    """The winner reports, the loser confirms — the shortest confirmed path."""
    engine.report_result(
        tournament_id,
        match.match_id,
        reported_by=winner,
        winner=winner,
        games_won=games_won,
        games_lost=games_lost,
        games_drawn=games_drawn,
    )
    loser = match.player_b if winner == match.player_a else match.player_a
    assert loser is not None
    engine.confirm_result(tournament_id, match.match_id, confirmed_by=loser)
