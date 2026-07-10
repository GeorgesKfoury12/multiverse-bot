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


def report_and_confirm(
    engine: TournamentEngine,
    tournament_id: str,
    match: Match,
    winner: str,
    games_won: int,
    games_lost: int,
) -> None:
    """The winner reports, the loser confirms — the shortest confirmed path."""
    engine.report_result(
        tournament_id,
        match.match_id,
        reported_by=winner,
        winner=winner,
        games_won=games_won,
        games_lost=games_lost,
    )
    loser = match.player_b if winner == match.player_a else match.player_a
    assert loser is not None
    engine.confirm_result(tournament_id, match.match_id, confirmed_by=loser)
