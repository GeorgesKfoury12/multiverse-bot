"""Shared helpers for facade-level engine tests."""

from multiverse_bot.engine import Match, TournamentEngine

PLAYERS = ("alice", "bob", "carol", "dave")


def start_four_player_tournament(engine: TournamentEngine, seed: int = 42) -> str:
    tournament_id = engine.create_tournament(name="Weekly Riftbound #1")
    for player_id in PLAYERS:
        engine.register_player(tournament_id, player_id)
    engine.start_tournament(tournament_id, seed=seed)
    return tournament_id


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
