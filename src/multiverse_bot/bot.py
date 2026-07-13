"""Discord adapter: slash commands and buttons in, engine calls out
(tickets #9, #10, #11, #12).

Thin by design (spec #1): every command translates one-to-one into an engine
command or query, TO authorization is one configured Discord role, and the
adapter's only own state is persisted Discord wiring — channel bindings,
Match threads, and the bytes behind image Decks. Restarting the bot is
therefore just ``open_engine`` plus reading that wiring back — the
confirm/Dispute buttons are stateless too, resolved from their custom_id.

Engine player IDs are Discord user IDs as strings. A message that pings shows
its players as live ``<@id>`` mentions; one that suppresses pings says their
display names as plain text instead, because a suppressed mention carries no
member data and renders as the raw tag on clients without the member cached
(issue #34).
"""

import io
import os
import random
import re
from collections.abc import Callable, Iterable, Mapping

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from fractions import Fraction

from multiverse_bot.engine import (
    EngineError,
    Match,
    Standing,
    Tournament,
    TournamentEngine,
)
from multiverse_bot.store import (
    BindingsStore,
    ChannelBindings,
    DeckImage,
    DeckImageStore,
    open_engine,
)


class CommandError(Exception):
    """A command the adapter itself refuses (e.g. no Tournament to default
    to); reported to the user exactly like an ``EngineError``."""


# Which phases a command applies to, for defaulting to "the only active
# Tournament" (ticket #9) without making the TO name it every time. Both a
# still-open and a closed signup window take Decks and can start ("setup"
# cannot: nobody is registered yet).
_AWAITING_OPEN = ("setup", "registration_closed")
_SIGNUP_OPEN = ("registration",)
_ACCEPTING_DECKS = ("registration", "registration_closed")
_STARTABLE = ("registration", "registration_closed")
_IN_PROGRESS = ("in_progress",)
_RESULTED = ("in_progress", "completed")
_HOLDING_DECKS = ("registration", "registration_closed", "in_progress", "completed")


def resolve_tournament(
    engine: TournamentEngine,
    reference: str | None,
    phases: tuple[str, ...],
    needed: str,
) -> Tournament:
    """The Tournament a command targets: the explicit reference (ID or name)
    if given, else the only Tournament in a phase the command applies to.
    ``needed`` describes that phase for error messages ("open for signups")."""
    tournaments = engine.tournaments()
    if reference is not None:
        matches = [t for t in tournaments if reference in (t.tournament_id, t.name)]
        if not matches:
            raise CommandError(f"no Tournament matches {reference!r}")
        if len(matches) > 1:
            options = ", ".join(t.tournament_id for t in matches)
            raise CommandError(
                f"several Tournaments are named {reference!r}; use an ID: {options}"
            )
        return matches[0]
    candidates = [t for t in tournaments if t.phase in phases]
    if not candidates:
        raise CommandError(f"no Tournament is {needed} right now")
    if len(candidates) > 1:
        options = ", ".join(f"{t.tournament_id} ({t.name})" for t in candidates)
        raise CommandError(
            f"several Tournaments are {needed}; pass one explicitly: {options}"
        )
    return candidates[0]


def parse_score(score: str) -> tuple[int, int, int]:
    """Read a reported game score — "2-1", or "1-1-1" with drawn games —
    as (games_won, games_lost, games_drawn), winner's count first."""
    try:
        numbers = [int(part) for part in score.split("-")]
    except ValueError:
        numbers = []
    if len(numbers) == 2:
        return numbers[0], numbers[1], 0
    if len(numbers) == 3:
        return numbers[0], numbers[1], numbers[2]
    raise CommandError(
        f"could not read {score!r} as a game score — use e.g. 2-0, 2-1, "
        "or 1-1-1 for a draw"
    )


def format_score(games_won: int, games_lost: int, games_drawn: int) -> str:
    if games_drawn:
        return f"{games_won}-{games_lost}-{games_drawn}"
    return f"{games_won}-{games_lost}"


def deck_image_marker(filename: str) -> str:
    """The engine-side Deck string for an image submission. The bytes live in
    the ``DeckImageStore``; this marker is what the engine stores, Seals, and
    Reveals — and what a reader sees if the image itself is ever lost."""
    return f"[screenshot] {filename}"


# Discord's default upload limit is 10 MB; staying under it keeps the Reveal's
# re-upload from failing in any guild.
_MAX_DECK_IMAGE_BYTES = 8 * 1024 * 1024

_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp")


def validate_deck_attachment(
    content_type: str | None, filename: str, size: int
) -> None:
    """Vet a submitted attachment before it becomes the Deck: images only
    (that is what gets Revealed), small enough for the bot to re-upload.
    Discord sometimes omits the content type, so the extension backs it up."""
    is_image = (
        content_type.startswith("image/")
        if content_type is not None
        else filename.lower().endswith(_IMAGE_EXTENSIONS)
    )
    if not is_image:
        raise CommandError(
            "that attachment is not an image — send a screenshot of your "
            "decklist, or submit it as text or a link instead"
        )
    if size > _MAX_DECK_IMAGE_BYTES:
        raise CommandError(
            "that image is too big to re-post at the Reveal — keep it under "
            f"{_MAX_DECK_IMAGE_BYTES // (1024 * 1024)} MB"
        )


def resolve_deck_image(deck: str, stored: DeckImage | None) -> DeckImage | None:
    """The image behind a Deck string, if that is what the string on file
    refers to. A text Deck ignores any leftover stored image: only the latest
    submission counts, and the engine's string is the source of truth."""
    if stored is not None and deck == deck_image_marker(stored.filename):
        return stored
    return None


def deck_presentation(
    deck: str, stored: DeckImage | None
) -> tuple[str, list[discord.File]]:
    """One Deck as message pieces: an attached screenshot for an image Deck,
    a quoted block otherwise. The text goes after an attribution line, the
    files on the same message — the shape every Deck display shares (the
    submit confirmation, the TO's view, the Reveal)."""
    image = resolve_deck_image(deck, stored)
    if image is None:
        return f"\n>>> {deck}", []
    return "", [discord.File(io.BytesIO(image.content), filename=image.filename)]


def require_decks(engine: TournamentEngine, tournament: Tournament) -> None:
    """The start gate, rendered for Discord: refuse while Decks are missing,
    mentioning exactly who — the TO's chase list (spec #1 story 7). The engine
    enforces the same gate; this puts names on it before the attempt."""
    missing = engine.players_missing_decks(tournament.tournament_id)
    if missing:
        mentions = ", ".join(f"<@{player}>" for player in missing)
        raise CommandError(
            f"**{tournament.name}** cannot start — no Deck yet from {mentions}. "
            "Chase them to `/submit-deck`; the start stays blocked until every "
            "Deck is in."
        )


def _match_in_round(
    engine: TournamentEngine, tournament_id: str, round_number: int, match_id: str
) -> Match | None:
    for match in engine.pairings(tournament_id, round_number):
        if match.match_id == match_id:
            return match
    return None


def open_match_by_id(
    engine: TournamentEngine, match_id: str
) -> tuple[Tournament, Match]:
    """The Match a report or confirm/Dispute click is about, if its Round is
    still open. Only current-round Matches of in-progress Tournaments are in
    play: anything else is frozen (or never existed) and the engine would
    refuse it anyway — this just says so upfront."""
    for tournament in engine.tournaments():
        if tournament.phase != "in_progress":
            continue
        assert tournament.current_round is not None
        match = _match_in_round(
            engine, tournament.tournament_id, tournament.current_round, match_id
        )
        if match is not None:
            return tournament, match
    raise CommandError(f"{match_id} is not in an open Round; its result is frozen")


def open_match_for_thread(
    engine: TournamentEngine, bindings_store: BindingsStore, thread_id: int | None
) -> tuple[Tournament, Match]:
    """The open Match hosted by the thread a command was used in."""
    match_id = (
        bindings_store.match_for_thread(thread_id) if thread_id is not None else None
    )
    if match_id is None:
        raise CommandError("this command only works inside a Match thread")
    return open_match_by_id(engine, match_id)


def open_match_by_reference(
    engine: TournamentEngine,
    bindings_store: BindingsStore,
    match_id: str | None,
    thread_id: int | None,
) -> tuple[Tournament, Match]:
    """The Match a TO ruling targets: the explicit Match ID if given, else the
    Match hosted by the thread the command was used in. The ID route keeps a
    Match with no thread on file — the force-close walk-through's fallback —
    rulable, so a Round can always close (ticket #12)."""
    if match_id is not None:
        return open_match_by_id(engine, match_id)
    hosted = (
        bindings_store.match_for_thread(thread_id) if thread_id is not None else None
    )
    if hosted is None:
        raise CommandError(
            "use this command inside a Match thread, or pass the Match ID "
            "(e.g. T1-R2-M3) as `match`"
        )
    return open_match_by_id(engine, hosted)


def mention_names(*player_ids: str | None) -> dict[str, str]:
    """Players in mention form, for the messages that ping them: a pinged
    mention is in the message's mention data, so every client renders it. A
    ``None`` (a Bye's missing opponent) is simply absent."""
    return {player: f"<@{player}>" for player in player_ids if player is not None}


async def player_names(
    client: discord.Client, guild: discord.Guild, player_ids: Iterable[str | None]
) -> dict[str, str]:
    """Players by display name, for the messages that do not ping them: a
    suppressed mention carries no member data, so clients without the member
    cached render the raw ``<@id>`` tag instead of a name (issue #34).

    Members show their server display name; a player who has left the server
    still resolves through their global profile; only an ID Discord cannot
    resolve at all falls back to ``player <id>``. Like ``mention_names``, a
    ``None`` (a Bye's missing opponent) is simply absent."""
    names: dict[str, str] = {}
    for player_id in player_ids:
        if player_id is not None and player_id not in names:
            resolved = await _resolved_name(client, guild, player_id)
            names[player_id] = (
                resolved if resolved is not None else f"player {player_id}"
            )
    return names


async def player_name(
    client: discord.Client, guild: discord.Guild, player_id: str
) -> str:
    return (await player_names(client, guild, (player_id,)))[player_id]


async def _resolved_name(
    client: discord.Client, guild: discord.Guild, player_id: str
) -> str | None:
    try:
        user_id = int(player_id)
    except ValueError:
        # Engine IDs are opaque strings; only production guarantees snowflakes.
        return None
    member = guild.get_member(user_id)
    if member is not None:
        return member.display_name
    try:
        return (await guild.fetch_member(user_id)).display_name
    except discord.HTTPException:
        pass
    try:
        return (await client.fetch_user(user_id)).display_name
    except discord.HTTPException:
        return None


def pinged_result_phrase(match: Match) -> str:
    """``result_phrase`` for the messages that ping the Match's players (the
    TO's thread rulings), where the mention form stays the display form."""
    return result_phrase(match, mention_names(match.player_a, match.player_b))


def result_phrase(match: Match, names: Mapping[str, str]) -> str:
    """A Match's result as one sentence fragment — the wording every surface
    shares (the scores channel, the TO's thread replies, the force-close
    walk-through). The Match must carry result fields; ``names`` renders the
    players (``player_names`` text, or ``mention_names`` where the message
    pings them)."""
    assert match.games_won is not None
    assert match.games_lost is not None and match.games_drawn is not None
    score = format_score(match.games_won, match.games_lost, match.games_drawn)
    if match.winner is None:
        assert match.player_b is not None
        return f"{names[match.player_a]} and {names[match.player_b]} drew {score}"
    loser = match.player_b if match.winner == match.player_a else match.player_a
    assert loser is not None
    return f"{names[match.winner]} beat {names[loser]} {score}"


def unfinished_match_lines(
    matches: Iterable[Match],
    thread_for: Callable[[str], int | None],
    names: Mapping[str, str],
) -> list[str]:
    """The force-close walk-through: one line per unfinished Match saying who
    plays, where (its thread, or the Match ID if none is on file), and where
    the result stands — so the TO can see at a glance what each ruling needs."""
    lines = []
    for match in matches:
        thread_id = thread_for(match.match_id)
        where = f"<#{thread_id}>" if thread_id is not None else match.match_id
        if match.status == "awaiting_report":
            standing = "no report yet"
        else:
            assert match.reported_by is not None
            flag = "Pending" if match.status == "pending" else "Disputed"
            standing = (
                f"{flag} — {result_phrase(match, names)}, "
                f"per {names[match.reported_by]}"
            )
        assert match.player_b is not None
        lines.append(
            f"- {names[match.player_a]} vs {names[match.player_b]} in {where} "
            f"— {standing}"
        )
    return lines


def unfinished_matches(
    engine: TournamentEngine, tournament: Tournament
) -> tuple[Match, ...]:
    """The current Round's Matches still without a confirmed result — what a
    force-close walks the TO through. A Bye never appears: it comes
    pre-confirmed."""
    assert tournament.current_round is not None
    return tuple(
        match
        for match in engine.pairings(tournament.tournament_id, tournament.current_round)
        if match.status != "confirmed"
    )


def round_thread_ids(
    engine: TournamentEngine,
    thread_for: Callable[[str], int | None],
    tournament_id: str,
    round_number: int,
) -> list[int]:
    """One Round's Match threads on file. Byes never had a thread and a crash
    can leave a playable Match without one; both are simply absent."""
    return [
        thread_id
        for match in engine.pairings(tournament_id, round_number)
        if (thread_id := thread_for(match.match_id)) is not None
    ]


def tournament_thread_ids(
    engine: TournamentEngine,
    thread_for: Callable[[str], int | None],
    tournament: Tournament,
) -> list[int]:
    """Every Match thread on file for the Tournament, across all its Rounds —
    what the end-of-Tournament tidy archives and locks (issue #35). An early
    end voids the current Round out of the engine, so its handler runs this
    collection before ``end_tournament``, keeping the voided Round's threads
    in the sweep."""
    if tournament.current_round is None:
        return []
    return [
        thread_id
        for round_number in range(1, tournament.current_round + 1)
        for thread_id in round_thread_ids(
            engine, thread_for, tournament.tournament_id, round_number
        )
    ]


def require_between_rounds(engine: TournamentEngine, tournament: Tournament) -> None:
    """End-early is offered only between Rounds (spec #1): refuse while the
    current Round has any result activity beyond its pre-confirmed Bye,
    directing the TO to force-close first — the same line the engine holds,
    said with the way forward."""
    assert tournament.current_round is not None
    pairings = engine.pairings(tournament.tournament_id, tournament.current_round)
    if any(not m.is_bye and m.status != "awaiting_report" for m in pairings):
        raise CommandError(
            f"Round {tournament.current_round} is in progress — force-close it "
            "first with `/tournament force-close`, then end early."
        )


def require_current_round(tournament: Tournament, round_number: int) -> None:
    """Refuse a confirmation click whose preview described an earlier Round:
    the situation the TO signed off on no longer exists."""
    if tournament.current_round != round_number:
        raise CommandError(
            f"Round {round_number} has moved on since that confirmation was "
            "offered; run the command again for the current state"
        )


def reopen_preview(
    engine: TournamentEngine, tournament: Tournament
) -> tuple[int, list[str]]:
    """The Round a reopen would reopen — the most recently closed one — and
    the preview of what confirming does. Mirrors the engine's own guards
    (issue #17) so the refusal lands before anything records, worded for
    the TO."""
    if tournament.phase == "completed":
        if tournament.ended_early:
            raise CommandError(
                f"**{tournament.name}** was ended early — its last Round was "
                "voided rather than closed on results, so there is nothing "
                "to reopen"
            )
        assert tournament.current_round is not None
        return tournament.current_round, [
            f"Reopen the final Round ({tournament.current_round}) of "
            f"**{tournament.name}**? The Tournament is un-completed and the "
            "posted final Standings stop counting.",
            "Correct the mistaken result with `/tournament assign-result` in "
            "its Match thread; the Tournament completes again, with fresh "
            "final Standings, the moment every result is confirmed.",
        ]
    assert tournament.current_round is not None
    if tournament.current_round == 1:
        raise CommandError(
            f"no Round of **{tournament.name}** has closed yet — Round 1 is "
            "still open, so its results can be corrected directly "
            "(`/tournament assign-result`)"
        )
    pairings = engine.pairings(tournament.tournament_id, tournament.current_round)
    if any(not m.is_bye and m.status == "confirmed" for m in pairings):
        raise CommandError(
            f"Round {tournament.current_round} already has a confirmed "
            f"result — Round {tournament.current_round - 1} stays closed"
        )
    reports = sum(1 for m in pairings if not m.is_bye and m.status != "awaiting_report")
    discarded = (
        f", discarding {reports} report{'s' if reports != 1 else ''} already in"
        if reports
        else ""
    )
    reopened = tournament.current_round - 1
    return reopened, [
        f"Reopen Round {reopened} of **{tournament.name}**? Round "
        f"{tournament.current_round}'s Pairings are reverted as never "
        f"made{discarded}.",
        "Correct the mistaken result with `/tournament assign-result` in its "
        f"Match thread; the Round re-closes and fresh Round "
        f"{tournament.current_round} Pairings post the moment every result "
        "is confirmed.",
    ]


def require_previewed_reopen(
    engine: TournamentEngine, tournament: Tournament, round_number: int
) -> None:
    """Refuse a reopen click whose preview described reopening a different
    Round. ``current_round`` alone cannot tell: if the final Round completed
    the Tournament since the preview, it still reads the same, yet the click
    would now un-complete that Round instead of reverting it. Re-running the
    guards must offer the very Round the TO signed off on."""
    reopened, _ = reopen_preview(engine, tournament)
    if reopened != round_number:
        raise CommandError(
            "the Tournament has moved on since that reopen was offered; run "
            "`/tournament reopen-round` again for the current state"
        )


def require_previewed_unregister(
    engine: TournamentEngine, tournament: Tournament, player_id: str, deckless: bool
) -> None:
    """Refuse an unregister click whose preview no longer holds: the player
    may have left the sign-up list on their own (nothing left to remove), or
    a straggler previewed as deck-less may have submitted their Deck between
    the preview and the click — the chase succeeding — and firing anyway
    would discard a Deck the TO never signed off on."""
    if player_id not in tournament.players:
        raise CommandError(
            f"<@{player_id}> is no longer on **{tournament.name}**'s sign-up list"
        )
    now_deckless = player_id in engine.players_missing_decks(tournament.tournament_id)
    if now_deckless != deckless:
        raise CommandError(
            f"<@{player_id}>'s Deck situation has changed since that "
            "Unregister was offered; run `/tournament unregister` again for "
            "the current state"
        )


def _percent(value: Fraction) -> str:
    return f"{float(value):.1%}"


def standings_lines(
    tournament: Tournament, standings: tuple[Standing, ...], names: Mapping[str, str]
) -> list[str]:
    """The Standings post, one ranked row per player with the full Tiebreaker
    stack visible so placements explain themselves (spec #1 story 27). Final
    Standings crown the winner — rank-1 players still tied through the whole
    stack share the title (story 28). Rows never ping, so they carry ``names``
    as text; the crowning line pings, so champions stay in mention form."""
    if tournament.phase == "completed":
        title = f"## {tournament.name} — Final Standings"
    else:
        title = (
            f"## {tournament.name} — Standings entering Round "
            f"{tournament.current_round}/{tournament.round_count}"
        )
    lines = [title]
    if tournament.unpairable_round is not None:
        # A schedule cut short explains itself (issue #37): the TO should not
        # have to wonder where the remaining scheduled Rounds went.
        lines.append(
            f"Round {tournament.unpairable_round} has no rematch-free pairing "
            "among the remaining players — the Tournament ends on the Rounds "
            "already played."
        )
    for row in standings:
        dropped = " (dropped)" if row.player_id in tournament.dropped else ""
        lines.append(
            f"{row.rank}. {names[row.player_id]}{dropped} — **{row.match_points} pts** "
            f"(OMW {_percent(row.omw)} · GW {_percent(row.gw)} "
            f"· OGW {_percent(row.ogw)})"
        )
    if tournament.phase == "completed":
        champions = [row.player_id for row in standings if row.rank == 1]
        mentions = " and ".join(f"<@{player}>" for player in champions)
        if len(champions) == 1:
            lines.append(f"🏆 {mentions} wins **{tournament.name}**!")
        else:
            lines.append(
                f"🏆 {mentions} share the title of **{tournament.name}** — "
                "tied through every Tiebreaker!"
            )
    return lines


class MultiverseBot(commands.Bot):
    def __init__(
        self,
        engine: TournamentEngine,
        bindings_store: BindingsStore,
        deck_images: DeckImageStore,
        to_role_id: int,
        guild_id: int | None = None,
    ) -> None:
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)
        self.engine = engine
        self.bindings_store = bindings_store
        self.deck_images = deck_images
        self.to_role_id = to_role_id
        self.guild_id = guild_id
        _install_commands(self)

    async def setup_hook(self) -> None:
        # Buttons are matched by custom_id, so the ones on messages posted
        # before a restart keep working.
        self.add_dynamic_items(PendingResultButton, TOConfirmButton)
        if self.guild_id is not None:
            # Guild-scoped sync is instant; global sync can take an hour.
            guild = discord.Object(self.guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            # An earlier run may have synced the same commands globally;
            # clear them or the picker offers every command twice (#30).
            self.tree.clear_commands(guild=None)
            await self.tree.sync()
        else:
            await self.tree.sync()

    async def on_ready(self) -> None:
        print(f"Logged in as {self.user} (id: {self.user.id})", flush=True)

    def member_is_to(self, user: discord.abc.User) -> bool:
        """Whether the user holds the configured TO role — the one
        authorization gate, shared by the command check and the TO
        confirmation buttons."""
        roles = getattr(user, "roles", ())
        return any(role.id == self.to_role_id for role in roles)

    def presented_deck(
        self, tournament_id: str, player_id: str, deck: str
    ) -> tuple[str, list[discord.File]]:
        """``deck_presentation`` with the player's stored image looked up —
        the pair always travels together."""
        return deck_presentation(deck, self.deck_images.image(tournament_id, player_id))


async def bound_channel(
    bot: MultiverseBot, tournament_id: str, purpose: str
) -> discord.TextChannel:
    """The Tournament's bound channel for one artifact ("pairings", "scores",
    "decklists" or "standings")."""
    bindings = bot.bindings_store.bindings(tournament_id)
    if bindings is None:
        raise CommandError(f"{tournament_id} has no channel bindings on file")
    channel_id: int = getattr(bindings, f"{purpose}_channel_id")
    channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
    if not isinstance(channel, discord.TextChannel):
        raise CommandError(
            f"the {purpose} binding for {tournament_id} is not a text channel"
        )
    return channel


def unregister_and_discard(
    bot: MultiverseBot, tournament_id: str, player_id: str, unregistered_by: str
) -> None:
    """One seam for both Unregister routes (the TO's confirm and the player's
    own command): the engine removal and the stored Deck-image bytes travel
    together, so neither route can leave a Sealed image behind."""
    bot.engine.unregister_player(
        tournament_id, player_id, unregistered_by=unregistered_by
    )
    bot.deck_images.delete_image(tournament_id, player_id)


async def announce_pairings(bot: MultiverseBot, tournament_id: str) -> None:
    """Post the current Round's Pairings in the bound pairings channel and
    open one thread per Match with both players pinged inside; a Bye is
    announced in the post itself (spec #1 stories 9, 10, 13).

    Shared seam for every Round: ``/tournament start`` posts Round 1 here, and
    ``advance_announcements`` calls it as confirmations close Rounds.
    Safe to re-run (``/tournament post-pairings``): Matches whose thread is
    already on file keep it, so a crash mid-announcement is recoverable
    without duplicate threads.
    """
    engine = bot.engine
    tournament = engine.tournament(tournament_id)
    round_number = tournament.current_round
    assert round_number is not None
    channel = await bound_channel(bot, tournament_id, "pairings")
    matches = engine.pairings(tournament_id, round_number)
    names = await player_names(bot, channel.guild, tournament.players)

    lines = [
        f"## {tournament.name} — Round {round_number}/{tournament.round_count} Pairings"
    ]
    for index, match in enumerate(matches, start=1):
        if match.is_bye:
            # The byed player is pinged (see below), so their mention is live.
            lines.append(
                f"{index}. <@{match.player_a}> has the **Bye** — scored as a "
                f"{match.games_won}-{match.games_lost} win."
            )
        else:
            assert match.player_b is not None
            lines.append(f"{index}. {names[match.player_a]} vs {names[match.player_b]}")
    # The channel post lists, the Match threads ping — except byed players,
    # whose only notification is this post, so their mention stays live.
    byed = [discord.Object(int(m.player_a)) for m in matches if m.is_bye]
    await channel.send(
        "\n".join(lines),
        allowed_mentions=discord.AllowedMentions(
            everyone=False, roles=False, users=byed
        ),
    )

    for match in matches:
        if match.is_bye:
            continue
        if bot.bindings_store.match_thread(match.match_id) is not None:
            continue  # already announced before a crash or re-post
        assert match.player_b is not None
        versus = f"{names[match.player_a]} vs {names[match.player_b]}"
        thread = await channel.create_thread(
            name=f"R{round_number}: {versus}"[:100],
            type=discord.ChannelType.public_thread,
        )
        await thread.send(
            f"<@{match.player_a}> <@{match.player_b}> — Round {round_number}: "
            "schedule and play your Match here, then report the result "
            "with `/report-score`."
        )
        bot.bindings_store.save_match_thread(match.match_id, thread.id)
        await _delete_thread_created_notice(channel, thread)


async def _delete_thread_created_notice(
    channel: discord.TextChannel, thread: discord.Thread
) -> None:
    """Remove the "started a thread" system line Discord posts for a thread
    created without a parent message, so the Pairings post stands alone
    (#33). Cosmetic: without Manage Messages the line simply stays."""
    try:
        # Called right after create_thread, so the notice is the newest
        # channel message; the small window only guards against a user
        # posting in the same instant.
        async for message in channel.history(limit=3):
            if (
                message.type is discord.MessageType.thread_created
                and message.reference is not None
                and message.reference.channel_id == thread.id
            ):
                await message.delete()
                return
    except discord.HTTPException:
        pass


async def tidy_match_threads(
    bot: MultiverseBot, thread_ids: Iterable[int], undo: bool = False
) -> int:
    """Archive and lock Match threads once their Tournament ends, tidying
    them out of the pairings channel's live thread list (issue #35). Archived
    and locked, never deleted: threads hold the scheduling and dispute record
    that day-later disputes are settled from. ``undo`` un-tidies instead, for
    a reopened final Round whose corrections happen back in those threads —
    locked ones would refuse the players and any TO without Manage Threads.

    Best-effort, like the thread-created notice cleanup: a thread Discord
    cannot produce or edit (deleted, missing permissions) is skipped, and a
    failed tidy never blocks the announcements around it. Returns how many
    threads were skipped, for the caller whose message would otherwise
    overclaim (the reopen announcing threads as unlocked)."""
    skipped = 0
    for thread_id in thread_ids:
        try:
            thread = bot.get_channel(thread_id) or await bot.fetch_channel(thread_id)
        except (discord.HTTPException, discord.InvalidData):
            thread = None
        if not isinstance(thread, discord.Thread):
            skipped += 1
            continue
        try:
            await thread.edit(archived=not undo, locked=not undo)
        except discord.HTTPException:
            skipped += 1
    return skipped


async def announce_reveal(bot: MultiverseBot, tournament_id: str) -> None:
    """Post every Deck at once in the bound decklists channel — the Reveal
    (spec #1 stories 5, 29). One attributed post per player, no pings; the
    posts stay up all Tournament (open decklist).

    Safe to re-run (``/tournament post-decklists``) after a crash mid-Reveal,
    though it posts the full Reveal again rather than patching gaps.
    """
    engine = bot.engine
    tournament = engine.tournament(tournament_id)
    channel = await bound_channel(bot, tournament_id, "decklists")
    names = await player_names(bot, channel.guild, tournament.players)
    await channel.send(
        f"## {tournament.name} — Deck Reveal\nLists are locked for the whole tournament"
    )
    for player_id in tournament.players:
        # Post-start every Deck is public; the owner query works in any phase.
        deck = engine.deck(tournament_id, player_id, requested_by=player_id)
        suffix, files = bot.presented_deck(tournament_id, player_id, deck)
        await channel.send(
            f"**{names[player_id]}'s Deck**{suffix}",
            files=files,
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def announce_result(
    bot: MultiverseBot, tournament: Tournament, match: Match, corrected: bool = False
) -> None:
    """Announce one confirmed result in the bound scores channel, keeping the
    community's at-a-glance record in sync (spec #1 story 18). ``corrected``
    marks a TO replacement of a result this channel already announced, so the
    record contradicts itself out loud rather than silently."""
    channel = await bound_channel(bot, tournament.tournament_id, "scores")
    names = await player_names(bot, channel.guild, (match.player_a, match.player_b))
    note = " (corrected by the TO)" if corrected else ""
    await channel.send(
        f"⚔️ **{tournament.name}** Round {match.round_number}: "
        f"{result_phrase(match, names)}{note}",
        allowed_mentions=discord.AllowedMentions.none(),
    )


async def announce_standings(bot: MultiverseBot, tournament_id: str) -> None:
    """Post the current Standings in the bound standings channel; final
    Standings also crown (and ping) the Tournament winner."""
    tournament = bot.engine.tournament(tournament_id)
    standings = bot.engine.standings(tournament_id)
    channel = await bound_channel(bot, tournament_id, "standings")
    names = await player_names(bot, channel.guild, tournament.players)
    # Standings rows never ping; the winner announcement is the exception.
    champions = (
        [discord.Object(int(row.player_id)) for row in standings if row.rank == 1]
        if tournament.phase == "completed"
        else []
    )
    await channel.send(
        "\n".join(standings_lines(tournament, standings, names)),
        allowed_mentions=discord.AllowedMentions(
            everyone=False, roles=False, users=champions
        ),
    )


async def advance_announcements(
    bot: MultiverseBot, tournament_id: str, round_before: int | None
) -> None:
    """After a confirmation lands: if it closed the Round, post Standings and
    then the next Round's Pairings, with no TO action needed (spec #1 stories
    21, 27); if it completed the Tournament, post final Standings and the
    winner (story 28), then tidy the Match threads (issue #35).

    Shared seam for everything that confirms results: the confirm button now,
    the TO's corrections (ticket #12) later.
    """
    tournament = bot.engine.tournament(tournament_id)
    if tournament.phase == "completed":
        await announce_standings(bot, tournament_id)
        await tidy_match_threads(
            bot,
            tournament_thread_ids(
                bot.engine, bot.bindings_store.match_thread, tournament
            ),
        )
    elif tournament.current_round != round_before:
        await announce_standings(bot, tournament_id)
        await announce_pairings(bot, tournament_id)


async def announce_confirmed_result(
    bot: MultiverseBot,
    interaction: discord.Interaction,
    tournament: Tournament,
    match_id: str,
    round_before: int,
    corrected: bool = False,
) -> None:
    """The result is in the engine; announce it — and whatever Round close it
    triggered — without dying silently if a channel post fails, since the
    result itself already stuck. Shared by everything that lands a confirmed
    result: the confirm button, and the TO's confirm/assign (ticket #12)."""
    try:
        confirmed = _match_in_round(
            bot.engine, tournament.tournament_id, round_before, match_id
        )
        assert confirmed is not None
        await announce_result(bot, tournament, confirmed, corrected=corrected)
        await advance_announcements(bot, tournament.tournament_id, round_before)
    except (EngineError, CommandError, discord.HTTPException) as error:
        await interaction.followup.send(
            f"The result is recorded, but announcing it failed: {error}\n"
            "A TO can re-post with `/tournament post-standings` and "
            "`/tournament post-pairings`."
        )


async def start_and_announce(
    bot: MultiverseBot,
    interaction: discord.Interaction,
    target: Tournament,
    rounds: int | None,
) -> None:
    """Start the Tournament and roll out its opening posts — the Reveal, the
    Round 1 Pairings, and the public started announcement. Shared by the
    direct start and the confirmed short-schedule start (ticket #12); the
    interaction must already be acknowledged, the rollout being several
    Discord round-trips."""
    warning = bot.engine.start_tournament(
        target.tournament_id, seed=random.randrange(2**63), round_count=rounds
    )
    await announce_reveal(bot, target.tournament_id)
    await announce_pairings(bot, target.tournament_id)
    started = bot.engine.tournament(target.tournament_id)
    lines = [
        f"**{target.name}** ({target.tournament_id}) has started: "
        f"{len(started.players)} players, {started.round_count} Rounds. "
        "Decks are Revealed and Round 1 Pairings are up!"
    ]
    if warning is not None:
        lines.append(f"⚠️ {warning}")
    await interaction.followup.send("\n".join(lines))


# The two one-click reactions to a Pending result (spec #1 story 17).
_PENDING_RESULT_ACTIONS = {
    "confirm": ("Confirm", discord.ButtonStyle.success),
    "dispute": ("Dispute", discord.ButtonStyle.danger),
}


class PendingResultButton(
    discord.ui.DynamicItem[discord.ui.Button],
    # Player IDs are opaque engine strings (Discord snowflakes in production),
    # so the ID fields accept anything colon-free.
    template=(
        r"multiverse:(?P<verb>confirm|dispute):(?P<match_id>[A-Za-z0-9-]+):"
        r"(?P<reported_by>[^:]+):(?P<winner>[^:]+):(?P<score>\d+-\d+-\d+)"
    ),
):
    """The opponent's one-click confirm or Dispute of a Pending result.

    Everything lives in the custom_id — the Match *and* the report's content
    (reporter, winner, score) — so the button keeps working across bot
    restarts with no adapter state, and a click on a message whose report has
    since been replaced is refused instead of acting on a result the clicker
    never saw.
    """

    def __init__(
        self, verb: str, match_id: str, reported_by: str, winner: str, score: str
    ) -> None:
        """``winner`` is the winning player's ID or ``"draw"``; ``score`` is
        always the three-part ``won-lost-drawn`` form."""
        label, style = _PENDING_RESULT_ACTIONS[verb]
        super().__init__(
            discord.ui.Button(
                label=label,
                style=style,
                custom_id=(
                    f"multiverse:{verb}:{match_id}:{reported_by}:{winner}:{score}"
                ),
            )
        )
        self.verb = verb
        self.match_id = match_id
        self.reported_by = reported_by
        self.winner = winner
        self.score = score

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Button,
        match: "re.Match[str]",
    ) -> "PendingResultButton":
        return cls(
            match["verb"],
            match["match_id"],
            match["reported_by"],
            match["winner"],
            match["score"],
        )

    def reported_match(self, engine: TournamentEngine) -> tuple[Tournament, Match]:
        """The button's Match, provided its live report is still the one this
        button was posted under."""
        tournament, match = open_match_by_id(engine, self.match_id)
        current = (
            match.reported_by,
            match.winner if match.winner is not None else "draw",
            f"{match.games_won}-{match.games_lost}-{match.games_drawn}",
        )
        if current != (self.reported_by, self.winner, self.score):
            raise CommandError(
                "that report has been replaced — use the buttons under the "
                "latest report"
            )
        return tournament, match

    async def callback(self, interaction: discord.Interaction) -> None:
        bot = interaction.client
        assert isinstance(bot, MultiverseBot)
        actor = str(interaction.user.id)
        try:
            tournament, _ = self.reported_match(bot.engine)
            round_before = tournament.current_round
            assert round_before is not None
            if self.verb == "confirm":
                bot.engine.confirm_result(
                    tournament.tournament_id, self.match_id, confirmed_by=actor
                )
            else:
                bot.engine.dispute_result(
                    tournament.tournament_id, self.match_id, disputed_by=actor
                )
        except (EngineError, CommandError) as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        # Acknowledge in the thread first — the channel posts below each cost
        # a Discord round-trip and the interaction token is on a clock. The
        # clicker is named as text: an acknowledgment of their own click
        # should neither ping anyone nor depend on mention rendering
        # (issue #34).
        mark = "✅ Confirmed" if self.verb == "confirm" else "⚠️ Disputed"
        assert interaction.message is not None
        await interaction.response.edit_message(
            content=(
                f"{interaction.message.content}\n"
                f"{mark} by {interaction.user.display_name}."
            ),
            view=None,
        )
        if self.verb == "confirm":
            await announce_confirmed_result(
                bot, interaction, tournament, self.match_id, round_before
            )
        else:
            await self._flag_dispute_to_to(bot, interaction)

    async def _flag_dispute_to_to(
        self, bot: "MultiverseBot", interaction: discord.Interaction
    ) -> None:
        """Ping the TO with the Match thread as the resolution context
        (spec #1 story 19)."""
        thread_id = bot.bindings_store.match_thread(self.match_id)
        where = f"<#{thread_id}>" if thread_id is not None else "its Match thread"
        # The disputer is named, not pinged (only the TO role is), so their
        # mention would render raw on uncached clients (issue #34).
        await interaction.followup.send(
            f"<@&{bot.to_role_id}> — {interaction.user.display_name} disputed the "
            f"Reported Result in {where}. Sort it out there, then either "
            "player can `/report-score` again — or the TO rules on it.",
            allowed_mentions=discord.AllowedMentions(
                everyone=False,
                roles=[discord.Object(bot.to_role_id)],
                users=False,
            ),
        )


_TO_CONFIRM_TEMPLATE = (
    r"multiverse:to-(?P<operation>start|drop|unregister|forceclose|end|reopen):"
    r"(?P<tournament_id>[^:]+):(?P<argument>[^:]+)(?::(?P<qualifier>[^:]+))?"
)


class TOConfirmButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=_TO_CONFIRM_TEMPLATE,
):
    """The explicit confirmation step in front of a destructive TO operation
    (ticket #12): a short-schedule start, a Drop, a force-close, an early end,
    a Round reopen. The slash command posts an ephemeral preview of exactly
    what will happen; only this button fires it.

    Like the confirm/Dispute buttons, everything lives in the custom_id so a
    click works across restarts: ``argument`` is the round count (start), the
    player (drop/unregister), the Round the preview described
    (forceclose/end), or the Round a reopen would reopen (reopen) —
    Round-scoped clicks are refused
    once the Round has moved on, so the button never acts on a situation the
    TO did not sign off. ``qualifier`` is an optional extra segment for the
    same guard where a Round number cannot carry it: an unregister records
    the Deck situation its preview described."""

    def __init__(
        self,
        operation: str,
        tournament_id: str,
        argument: str,
        label: str = "Confirm",
        qualifier: str | None = None,
    ) -> None:
        custom_id = f"multiverse:to-{operation}:{tournament_id}:{argument}"
        if qualifier is not None:
            custom_id += f":{qualifier}"
        super().__init__(
            discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.danger,
                custom_id=custom_id,
            )
        )
        self.operation = operation
        self.tournament_id = tournament_id
        self.argument = argument
        self.qualifier = qualifier

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Button,
        match: "re.Match[str]",
    ) -> "TOConfirmButton":
        # The label is not reconstructed: the posted message still renders the
        # original button; this instance only handles the click.
        return cls(
            match["operation"],
            match["tournament_id"],
            match["argument"],
            qualifier=match["qualifier"],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        bot = interaction.client
        assert isinstance(bot, MultiverseBot)
        operations = {
            "start": self._start,
            "drop": self._drop,
            "unregister": self._unregister,
            "forceclose": self._force_close,
            "end": self._end,
            "reopen": self._reopen,
        }
        try:
            # The preview is ephemeral (invoker-only), but roles can change
            # between the preview and the click; re-check.
            if not bot.member_is_to(interaction.user):
                raise CommandError("this confirmation is reserved for the TO role")
            await operations[self.operation](bot, interaction)
        except (EngineError, CommandError) as error:
            if interaction.response.is_done():
                await interaction.followup.send(str(error), ephemeral=True)
            else:
                await interaction.response.send_message(str(error), ephemeral=True)

    def _in_progress_tournament(self, engine: TournamentEngine) -> Tournament:
        tournament = engine.tournament(self.tournament_id)
        if tournament.phase != "in_progress":
            raise CommandError(f"**{tournament.name}** is not in progress")
        return tournament

    async def _start(
        self, bot: MultiverseBot, interaction: discord.Interaction
    ) -> None:
        """The TO accepts the short-schedule warning; start for real (spec #1
        story 15: warn, then obey)."""
        tournament = bot.engine.tournament(self.tournament_id)
        if tournament.phase not in _STARTABLE:
            raise CommandError(f"**{tournament.name}** has already started")
        require_decks(bot.engine, tournament)
        await interaction.response.edit_message(
            content=f"Starting **{tournament.name}** with {self.argument} Rounds…",
            view=None,
        )
        await start_and_announce(bot, interaction, tournament, int(self.argument))

    async def _drop(self, bot: MultiverseBot, interaction: discord.Interaction) -> None:
        tournament = self._in_progress_tournament(bot.engine)
        assert interaction.guild is not None
        dropped = await player_name(bot, interaction.guild, self.argument)
        bot.engine.drop_player(
            self.tournament_id, self.argument, dropped_by=str(interaction.user.id)
        )
        await interaction.response.edit_message(
            content=f"{dropped} is dropped from **{tournament.name}**.",
            view=None,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        try:
            channel = await bound_channel(bot, self.tournament_id, "pairings")
            await channel.send(
                f"🚪 <@{self.argument}> has dropped from **{tournament.name}** "
                "(TO decision). They are not paired from here on; their played "
                "Matches still count and they keep their place in Standings.",
                allowed_mentions=discord.AllowedMentions(
                    everyone=False,
                    roles=False,
                    users=[discord.Object(int(self.argument))],
                ),
            )
        except (CommandError, discord.HTTPException) as error:
            await interaction.followup.send(
                f"The Drop is recorded, but announcing it failed: {error}",
                ephemeral=True,
            )

    async def _unregister(
        self, bot: MultiverseBot, interaction: discord.Interaction
    ) -> None:
        """The TO removes a registrant before the start (issue #20) — the
        "drop" half of resolving a straggler, distinct from a Drop: they
        leave the sign-up list entirely, Deck and all. The click is refused
        if it outraced the start, or if the previewed Deck situation (the
        qualifier) no longer holds — e.g. the straggler submitted meanwhile."""
        tournament = bot.engine.tournament(self.tournament_id)
        if tournament.phase not in _STARTABLE:
            raise CommandError(f"**{tournament.name}** has already started")
        require_previewed_unregister(
            bot.engine, tournament, self.argument, deckless=self.qualifier == "deckless"
        )
        assert interaction.guild is not None
        removed = await player_name(bot, interaction.guild, self.argument)
        unregister_and_discard(
            bot, self.tournament_id, self.argument, str(interaction.user.id)
        )
        await interaction.response.edit_message(
            content=f"{removed} is unregistered from **{tournament.name}**.",
            view=None,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        try:
            channel = await bound_channel(bot, self.tournament_id, "pairings")
            await channel.send(
                f"🚪 The TO unregistered <@{self.argument}> from "
                f"**{tournament.name}** — off the sign-up list, any submitted "
                "Deck discarded. Signing up again while registration is open "
                "starts fresh.",
                allowed_mentions=discord.AllowedMentions(
                    everyone=False,
                    roles=False,
                    users=[discord.Object(int(self.argument))],
                ),
            )
        except (CommandError, discord.HTTPException) as error:
            await interaction.followup.send(
                f"The Unregister is recorded, but announcing it failed: {error}",
                ephemeral=True,
            )

    async def _force_close(
        self, bot: MultiverseBot, interaction: discord.Interaction
    ) -> None:
        """Kick off the walk-through: announce the force-close, then hand the
        TO the per-Match checklist. Each ruling is its own explicit action in
        the Match thread; the Round closes itself when the last result lands
        (ADR-0001: the bot makes the calls cheap, not for the TO)."""
        tournament = self._in_progress_tournament(bot.engine)
        require_current_round(tournament, int(self.argument))
        remaining = unfinished_matches(bot.engine, tournament)
        if not remaining:
            raise CommandError(
                f"Round {tournament.current_round} has no unfinished Matches left"
            )
        assert interaction.guild is not None
        names = await player_names(bot, interaction.guild, tournament.players)
        lines = unfinished_match_lines(
            remaining, bot.bindings_store.match_thread, names
        )
        closing = "\nThe Round closes itself the moment the last result lands."
        if any(bot.bindings_store.match_thread(m.match_id) is None for m in remaining):
            closing = (
                "\nA Match with no thread takes its ruling by ID: "
                "`/tournament assign-result match:<ID>`." + closing
            )
        await interaction.response.edit_message(
            content=(
                f"Force-closing Round {tournament.current_round} of "
                f"**{tournament.name}**. In each Match thread below, "
                "`/tournament assign-result` — or `/tournament confirm-result` "
                "to accept a Pending report as it stands:\n"
                + "\n".join(lines)
                + closing
            ),
            view=None,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        try:
            channel = await bound_channel(bot, self.tournament_id, "pairings")
            await channel.send(
                f"⚖️ The TO is force-closing Round {tournament.current_round} of "
                f"**{tournament.name}** — these Matches are getting TO rulings:\n"
                + "\n".join(lines),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (CommandError, discord.HTTPException) as error:
            await interaction.followup.send(
                f"The walk-through is above, but announcing the force-close "
                f"failed: {error}",
                ephemeral=True,
            )

    async def _reopen(
        self, bot: MultiverseBot, interaction: discord.Interaction
    ) -> None:
        """Undo the close the last confirmation triggered so the TO can
        correct a mistaken result (issue #17). The reverted next Round's
        threads are forgotten, so the re-close opens fresh ones instead of
        reusing threads whose Pairings may have changed; the engine re-checks
        the no-confirmed-results guard, refusing a click that outraced play."""
        tournament = bot.engine.tournament(self.tournament_id)
        if tournament.phase not in _RESULTED:
            raise CommandError(f"**{tournament.name}** is not underway")
        require_previewed_reopen(bot.engine, tournament, int(self.argument))
        completed = tournament.phase == "completed"
        assert tournament.current_round is not None
        reverted = (
            ()
            if completed
            else bot.engine.pairings(self.tournament_id, tournament.current_round)
        )
        bot.engine.reopen_round(
            self.tournament_id, reopened_by=str(interaction.user.id)
        )
        for match in reverted:
            bot.bindings_store.delete_match_thread(match.match_id)
        reopened = bot.engine.tournament(self.tournament_id).current_round
        await interaction.response.edit_message(
            content=f"Round {reopened} of **{tournament.name}** is reopened.",
            view=None,
        )
        if completed:
            # The completion tidied every Match thread; corrections happen
            # back in the reopened final Round's, so exactly those un-tidy —
            # earlier Rounds stay archived (issue #35).
            assert reopened is not None
            stuck = await tidy_match_threads(
                bot,
                round_thread_ids(
                    bot.engine,
                    bot.bindings_store.match_thread,
                    self.tournament_id,
                    reopened,
                ),
                undo=True,
            )
            threads_note = (
                "and the final Round's Match threads are unlocked for the correction"
                if not stuck
                else "but some Match threads could not be unlocked — a still-"
                "locked Match takes its ruling by ID: "
                "`/tournament assign-result match:<ID>`"
            )
            announcement = (
                f"🔓 The TO reopened the final Round of **{tournament.name}** "
                "to correct a result — the posted final Standings stop "
                f"counting, {threads_note}. The Tournament completes again, "
                "with fresh final Standings, when every result is confirmed."
            )
        else:
            announcement = (
                f"🔓 The TO reopened Round {reopened} of **{tournament.name}** "
                f"to correct a result — Round {tournament.current_round}'s "
                "Pairings and Match threads are void; fresh Pairings post "
                "when the Round re-closes."
            )
        try:
            channel = await bound_channel(bot, self.tournament_id, "pairings")
            await channel.send(
                announcement, allowed_mentions=discord.AllowedMentions.none()
            )
        except (CommandError, discord.HTTPException) as error:
            await interaction.followup.send(
                f"The Round is reopened, but announcing it failed: {error}",
                ephemeral=True,
            )

    async def _end(self, bot: MultiverseBot, interaction: discord.Interaction) -> None:
        tournament = self._in_progress_tournament(bot.engine)
        require_current_round(tournament, int(self.argument))
        voided = tournament.current_round
        # Collected before the end voids the current Round out of the engine:
        # the voided Round's threads are already open on Discord, so the tidy
        # below must sweep them too.
        thread_ids = tournament_thread_ids(
            bot.engine, bot.bindings_store.match_thread, tournament
        )
        # The engine re-checks that the Round is untouched; a report that
        # landed since the preview refuses the end instead of voiding it.
        bot.engine.end_tournament(self.tournament_id)
        await interaction.response.edit_message(
            content=f"**{tournament.name}** is ended; Standings-so-far are final.",
            view=None,
        )
        try:
            channel = await bound_channel(bot, self.tournament_id, "pairings")
            await channel.send(
                f"🏁 The TO has ended **{tournament.name}** early — Round "
                f"{voided}'s Pairings are void, and the Standings so far are "
                "final.",
                allowed_mentions=discord.AllowedMentions.none(),
            )
            await announce_standings(bot, self.tournament_id)
        except (CommandError, discord.HTTPException) as error:
            await interaction.followup.send(
                f"The Tournament is ended, but announcing it failed: {error}\n"
                "A TO can re-post with `/tournament post-standings`.",
                ephemeral=True,
            )
        await tidy_match_threads(bot, thread_ids)


def to_confirmation(
    operation: str,
    tournament_id: str,
    argument: str,
    label: str,
    qualifier: str | None = None,
) -> discord.ui.View:
    """An ephemeral preview's single confirm button — the only way a
    destructive TO operation fires."""
    view = discord.ui.View(timeout=None)
    view.add_item(TOConfirmButton(operation, tournament_id, argument, label, qualifier))
    return view


def _install_commands(bot: MultiverseBot) -> None:
    engine = bot.engine

    def is_to(interaction: discord.Interaction) -> bool:
        return bot.member_is_to(interaction.user)

    tournament_group = app_commands.Group(
        name="tournament",
        description="TO controls: create and run a Tournament",
        guild_only=True,
    )
    bot.tree.add_command(tournament_group)

    @tournament_group.command(
        description="Create a Tournament bound to its purpose channels"
    )
    @app_commands.check(is_to)
    @app_commands.describe(
        name="The Tournament's name",
        pairings="Channel for Round Pairings; Match threads open here",
        scores="Channel where confirmed results are announced",
        decklists="Channel for the Deck Reveal at start",
        standings="Channel for Standings after each Round",
    )
    async def create(
        interaction: discord.Interaction,
        name: str,
        pairings: discord.TextChannel,
        scores: discord.TextChannel,
        decklists: discord.TextChannel,
        standings: discord.TextChannel,
    ) -> None:
        tournament_id = engine.create_tournament(name=name)
        bot.bindings_store.save_bindings(
            tournament_id,
            ChannelBindings(pairings.id, scores.id, decklists.id, standings.id),
        )
        await interaction.response.send_message(
            f"Created **{name}** ({tournament_id}) — Pairings will post in "
            f"{pairings.mention}. Open signups with "
            "`/tournament open-registration`."
        )

    @tournament_group.command(
        name="open-registration",
        description="Open (or reopen) signups for a Tournament",
    )
    @app_commands.check(is_to)
    @app_commands.describe(tournament="Tournament ID or name; defaults if unique")
    async def open_registration(
        interaction: discord.Interaction, tournament: str | None = None
    ) -> None:
        target = resolve_tournament(
            engine, tournament, _AWAITING_OPEN, "awaiting a signup window"
        )
        engine.open_registration(target.tournament_id)
        await interaction.response.send_message(
            f"Registration for **{target.name}** ({target.tournament_id}) is "
            "open — sign up with `/signup`!"
        )

    @tournament_group.command(
        name="close-registration",
        description="Close signups, finalizing the player count",
    )
    @app_commands.check(is_to)
    @app_commands.describe(tournament="Tournament ID or name; defaults if unique")
    async def close_registration(
        interaction: discord.Interaction, tournament: str | None = None
    ) -> None:
        target = resolve_tournament(
            engine, tournament, _SIGNUP_OPEN, "open for signups"
        )
        engine.close_registration(target.tournament_id)
        count = len(engine.tournament(target.tournament_id).players)
        await interaction.response.send_message(
            f"Registration for **{target.name}** ({target.tournament_id}) is "
            f"closed at {count} players. Start with `/tournament start`."
        )

    @tournament_group.command(
        description="Start the Tournament and post Round 1 Pairings"
    )
    @app_commands.check(is_to)
    @app_commands.describe(
        tournament="Tournament ID or name; defaults if unique",
        rounds="Override the Swiss round count (the schedule is the TO's call)",
    )
    async def start(
        interaction: discord.Interaction,
        tournament: str | None = None,
        rounds: app_commands.Range[int, 1] | None = None,
    ) -> None:
        target = resolve_tournament(engine, tournament, _STARTABLE, "ready to start")
        # The friendly face of the engine's own gate: refuse with mentions,
        # before deferring, so the TO gets their chase list ephemerally.
        require_decks(engine, target)
        if rounds is not None:
            # A start is irreversible (Decks Reveal, Pairings post), so a
            # schedule too short for a sole undefeated winner warns *before*
            # starting; the engine's own warning only comes after. The
            # schedule stays the TO's call — confirm and it obeys (ticket #12).
            standard = engine.standard_round_count(target.tournament_id)
            if rounds < standard:
                plural = "" if rounds == 1 else "s"
                await interaction.response.send_message(
                    f"⚠️ {rounds} Round{plural} cannot single out an undefeated "
                    f"winner among {len(target.players)} players; the standard "
                    f"Swiss count is {standard}.\n"
                    "The schedule is yours to call — start anyway?",
                    view=to_confirmation(
                        "start",
                        target.tournament_id,
                        str(rounds),
                        f"Start with {rounds} Round{plural}",
                    ),
                    ephemeral=True,
                )
                return
        # The Reveal and a thread per Match are a Discord round-trip each.
        await interaction.response.defer()
        await start_and_announce(bot, interaction, target, rounds)

    @tournament_group.command(
        name="post-pairings",
        description="Re-post the current Round's Pairings (recovery)",
    )
    @app_commands.check(is_to)
    @app_commands.describe(tournament="Tournament ID or name; defaults if unique")
    async def post_pairings(
        interaction: discord.Interaction, tournament: str | None = None
    ) -> None:
        """Recovery for a crash between a Round beginning and its announcement
        finishing: lists the Pairings again and opens only the Match threads
        still missing (existing ones are kept, not duplicated)."""
        target = resolve_tournament(engine, tournament, _IN_PROGRESS, "in progress")
        await interaction.response.defer()
        await announce_pairings(bot, target.tournament_id)
        await interaction.followup.send(
            f"Round {engine.tournament(target.tournament_id).current_round} "
            f"Pairings for **{target.name}** are re-posted."
        )

    @tournament_group.command(
        name="post-standings",
        description="Re-post the current Standings (recovery)",
    )
    @app_commands.check(is_to)
    @app_commands.describe(tournament="Tournament ID or name; defaults if unique")
    async def post_standings(
        interaction: discord.Interaction, tournament: str | None = None
    ) -> None:
        """Recovery for a crash between a Round closing and its Standings
        landing: posts the current Standings again (final ones for a completed
        Tournament); pair with post-pairings if the next Round's posts are
        missing too. For a completed Tournament it also re-runs the Match
        thread tidy (issue #35), covering a crash mid-tidy — though not a
        Round an early end already voided out of the engine."""
        target = resolve_tournament(
            engine, tournament, _RESULTED, "in progress or completed"
        )
        await interaction.response.defer()
        await announce_standings(bot, target.tournament_id)
        if target.phase == "completed":
            await tidy_match_threads(
                bot,
                tournament_thread_ids(engine, bot.bindings_store.match_thread, target),
            )
        await interaction.followup.send(
            f"Standings for **{target.name}** are re-posted."
        )

    @tournament_group.command(
        name="view-deck",
        description="View a player's Deck, Sealed or Revealed (TO only)",
    )
    @app_commands.check(is_to)
    @app_commands.describe(
        player="Whose Deck to view",
        tournament="Tournament ID or name; defaults if unique",
    )
    async def view_deck(
        interaction: discord.Interaction,
        player: discord.Member,
        tournament: str | None = None,
    ) -> None:
        """Ephemeral, so checking a Sealed Deck in a public channel does not
        Reveal it."""
        target = resolve_tournament(engine, tournament, _HOLDING_DECKS, "underway")
        try:
            deck = engine.deck_as_to(target.tournament_id, str(player.id))
        except EngineError:
            # The tournament resolved above, so the only refusal left is a
            # missing Deck; say it with a mention, not a raw ID.
            raise CommandError(
                f"{player.mention} has no Deck on file in **{target.name}**"
            ) from None
        suffix, files = bot.presented_deck(target.tournament_id, str(player.id), deck)
        await interaction.response.send_message(
            f"{player.display_name}'s Deck in **{target.name}**:{suffix}",
            files=files,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @tournament_group.command(
        name="post-decklists",
        description="Re-post the Deck Reveal (recovery)",
    )
    @app_commands.check(is_to)
    @app_commands.describe(tournament="Tournament ID or name; defaults if unique")
    async def post_decklists(
        interaction: discord.Interaction, tournament: str | None = None
    ) -> None:
        """Recovery for a crash between the start and the Reveal finishing:
        posts the whole Reveal again."""
        target = resolve_tournament(
            engine, tournament, _RESULTED, "in progress or completed"
        )
        await interaction.response.defer()
        await announce_reveal(bot, target.tournament_id)
        await interaction.followup.send(
            f"The Deck Reveal for **{target.name}** is re-posted."
        )

    def player_mentions(match: Match) -> discord.AllowedMentions:
        """Ping exactly the Match's players — a TO ruling should reach them."""
        players = (match.player_a, match.player_b)
        return discord.AllowedMentions(
            everyone=False,
            roles=False,
            users=[discord.Object(int(p)) for p in players if p is not None],
        )

    @tournament_group.command(
        name="confirm-result",
        description="Confirm a Match's reported result as the TO",
    )
    @app_commands.check(is_to)
    @app_commands.rename(match_id="match")
    @app_commands.describe(
        match_id="Match ID (e.g. T1-R2-M3); defaults to this thread's Match",
    )
    async def confirm_result(
        interaction: discord.Interaction, match_id: str | None = None
    ) -> None:
        """The TO confirms a Pending or Disputed result as reported — ruling a
        Dispute in the reporter's favor, or unsticking an unresponsive
        opponent (ticket #12). Used in the Match thread, like `/report-score`; the
        Match ID reaches a Match with no thread."""
        target, match = open_match_by_reference(
            engine, bot.bindings_store, match_id, interaction.channel_id
        )
        round_before = target.current_round
        assert round_before is not None
        engine.confirm_result_as_to(
            target.tournament_id, match.match_id, actor=str(interaction.user.id)
        )
        await interaction.response.send_message(
            f"✅ The TO confirmed the reported result: {pinged_result_phrase(match)}.",
            allowed_mentions=player_mentions(match),
        )
        await announce_confirmed_result(
            bot, interaction, target, match.match_id, round_before
        )

    @tournament_group.command(
        name="assign-result",
        description="Set or correct a Match's result by TO ruling",
    )
    @app_commands.check(is_to)
    @app_commands.rename(match_id="match")
    @app_commands.describe(
        score="Game score, winner's count first: 2-0, 2-1, or 1-1-1 for a draw",
        winner="Who won the Match; leave empty for a draw",
        match_id="Match ID (e.g. T1-R2-M3); defaults to this thread's Match",
    )
    async def assign_result(
        interaction: discord.Interaction,
        score: str,
        winner: discord.Member | None = None,
        match_id: str | None = None,
    ) -> None:
        """The TO sets the Match's result by fiat — no-shows, Dispute rulings,
        corrections — replacing whatever was there, until the Round closes
        (ticket #12). Used in the Match thread; the Match ID reaches a Match
        with no thread. An Assigned Result counts identically to a reported
        one."""
        target, match = open_match_by_reference(
            engine, bot.bindings_store, match_id, interaction.channel_id
        )
        round_before = target.current_round
        assert round_before is not None
        corrected = match.status == "confirmed"
        games_won, games_lost, games_drawn = parse_score(score)
        engine.assign_result(
            target.tournament_id,
            match.match_id,
            assigned_by=str(interaction.user.id),
            winner=str(winner.id) if winner is not None else None,
            games_won=games_won,
            games_lost=games_lost,
            games_drawn=games_drawn,
        )
        assigned = _match_in_round(
            engine, target.tournament_id, round_before, match.match_id
        )
        assert assigned is not None
        note = " (replacing the confirmed result)" if corrected else ""
        await interaction.response.send_message(
            f"⚖️ The TO set {match.match_id}'s result{note}: "
            f"{pinged_result_phrase(assigned)}.",
            allowed_mentions=player_mentions(match),
        )
        await announce_confirmed_result(
            bot, interaction, target, match.match_id, round_before, corrected=corrected
        )

    @tournament_group.command(
        name="force-close",
        description="Close the current Round by ruling on its unfinished Matches",
    )
    @app_commands.check(is_to)
    @app_commands.describe(tournament="Tournament ID or name; defaults if unique")
    async def force_close(
        interaction: discord.Interaction, tournament: str | None = None
    ) -> None:
        """Preview + confirm: the flow walks the TO through a ruling per
        unfinished Match, and the Round closes itself when the last result
        lands (ticket #12; ADR-0001 — every ruling stays the TO's)."""
        target = resolve_tournament(engine, tournament, _IN_PROGRESS, "in progress")
        remaining = unfinished_matches(engine, target)
        if not remaining:
            raise CommandError(
                f"Round {target.current_round} has no unfinished Matches; "
                "it closes on its own"
            )
        count = len(remaining)
        need = "1 Match still needs" if count == 1 else f"{count} Matches still need"
        assert interaction.guild is not None
        names = await player_names(bot, interaction.guild, target.players)
        await interaction.response.send_message(
            f"Force-close Round {target.current_round} of **{target.name}**? "
            f"{need} a result:\n"
            + "\n".join(
                unfinished_match_lines(
                    remaining, bot.bindings_store.match_thread, names
                )
            )
            + "\nConfirming announces the force-close and hands you the "
            "walk-through; every ruling stays yours.",
            view=to_confirmation(
                "forceclose",
                target.tournament_id,
                str(target.current_round),
                f"Force-close Round {target.current_round}",
            ),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @tournament_group.command(
        description="Drop a player from the Tournament (permanent)"
    )
    @app_commands.check(is_to)
    @app_commands.describe(
        player="Who to drop",
        tournament="Tournament ID or name; defaults if unique",
    )
    async def drop(
        interaction: discord.Interaction,
        player: discord.Member,
        tournament: str | None = None,
    ) -> None:
        """Preview + confirm: the TO-initiated Drop, e.g. for unresponsiveness
        (ticket #12). Permanent — never paired again, played Matches keep
        counting, place in Standings kept."""
        target = resolve_tournament(engine, tournament, _IN_PROGRESS, "in progress")
        player_id = str(player.id)
        # The engine holds the same lines; saying them here keeps the preview
        # honest and the refusals in mention form rather than raw IDs.
        if player_id not in target.players:
            raise CommandError(
                f"{player.mention} is not registered in **{target.name}**"
            )
        if player_id in target.dropped:
            raise CommandError(
                f"{player.mention} has already dropped from **{target.name}**"
            )
        if len(target.players) - len(target.dropped) - 1 < 2:
            raise CommandError(
                f"dropping {player.mention} would leave **{target.name}** with "
                "fewer than 2 players — end the Tournament early instead "
                "(`/tournament end-early`)"
            )
        lines = [
            f"Drop {player.display_name} from **{target.name}**? This is "
            "permanent: they are never paired again; their played Matches "
            "still count and they keep their place in Standings."
        ]
        if any(
            player_id in (m.player_a, m.player_b)
            for m in unfinished_matches(engine, target)
        ):
            lines.append(
                f"Their Round {target.current_round} Match is still unfinished — "
                "it stays on the normal result flow (report/confirm, or "
                "`/tournament assign-result` in its thread)."
            )
        await interaction.response.send_message(
            "\n".join(lines),
            view=to_confirmation(
                "drop", target.tournament_id, player_id, f"Drop {player.display_name}"
            ),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @tournament_group.command(
        name="unregister",
        description="Remove a player from the sign-up list before the start",
    )
    @app_commands.check(is_to)
    @app_commands.describe(
        player="Who to unregister",
        tournament="Tournament ID or name; defaults if unique",
    )
    async def to_unregister(
        interaction: discord.Interaction,
        player: discord.Member,
        tournament: str | None = None,
    ) -> None:
        """Preview + confirm: the TO resolves a deck-less straggler blocking
        the start (issue #20) — the "drop" half of "chase or drop", though
        distinct from a Drop: the player leaves the roster entirely, Deck and
        all, and never appears in Standings. Re-registering starts fresh."""
        target = resolve_tournament(engine, tournament, _STARTABLE, "still in signups")
        player_id = str(player.id)
        # The engine holds the same line; saying it here keeps the refusal in
        # mention form rather than a raw ID.
        if player_id not in target.players:
            raise CommandError(
                f"{player.mention} is not registered in **{target.name}**"
            )
        deckless = player_id in engine.players_missing_decks(target.tournament_id)
        deck_note = (
            "they have no Deck on file"
            if deckless
            else "their submitted Deck is discarded"
        )
        await interaction.response.send_message(
            f"Unregister {player.display_name} from **{target.name}**? They "
            f"leave the sign-up list entirely ({deck_note}); signing up again "
            "while registration is open starts fresh.",
            view=to_confirmation(
                "unregister",
                target.tournament_id,
                player_id,
                f"Unregister {player.display_name}",
                qualifier="deckless" if deckless else "decked",
            ),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @tournament_group.command(
        name="end-early",
        description="End the Tournament between Rounds; Standings become final",
    )
    @app_commands.check(is_to)
    @app_commands.describe(tournament="Tournament ID or name; defaults if unique")
    async def end_early(
        interaction: discord.Interaction, tournament: str | None = None
    ) -> None:
        """Preview + confirm: offered only between Rounds — the untouched
        current Round is voided as never played; mid-Round the TO is directed
        to force-close first (ticket #12, spec #1 story 24)."""
        target = resolve_tournament(engine, tournament, _IN_PROGRESS, "in progress")
        require_between_rounds(engine, target)
        assert target.current_round is not None
        if target.current_round == 1:
            outcome = "No Round has completed, so the final Standings are a flat tie."
        else:
            outcome = (
                f"The Standings after Round {target.current_round - 1} become final."
            )
        await interaction.response.send_message(
            f"End **{target.name}** early? Round {target.current_round}'s "
            f"Pairings are voided as never played. {outcome}",
            view=to_confirmation(
                "end",
                target.tournament_id,
                str(target.current_round),
                "End the Tournament",
            ),
            ephemeral=True,
        )

    @tournament_group.command(
        name="reopen-round",
        description="Reopen the most recently closed Round to correct a result",
    )
    @app_commands.check(is_to)
    @app_commands.describe(tournament="Tournament ID or name; defaults if unique")
    async def reopen_round(
        interaction: discord.Interaction, tournament: str | None = None
    ) -> None:
        """Preview + confirm: undoes the close (or Tournament completion) the
        last confirmation triggered, so even the result that closed the Round
        has a correction window (issue #17; ADR-0001 — an explicit first-class
        TO action). Refused once the next Round has a confirmed result."""
        target = resolve_tournament(
            engine, tournament, _RESULTED, "in progress or completed"
        )
        reopened, lines = reopen_preview(engine, target)
        await interaction.response.send_message(
            "\n".join(lines),
            view=to_confirmation(
                "reopen",
                target.tournament_id,
                str(reopened),
                f"Reopen Round {reopened}",
            ),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @bot.tree.command(description="Sign up for a Tournament")
    @app_commands.guild_only()
    @app_commands.describe(tournament="Tournament ID or name; defaults if unique")
    async def signup(
        interaction: discord.Interaction, tournament: str | None = None
    ) -> None:
        target = resolve_tournament(
            engine, tournament, _SIGNUP_OPEN, "open for signups"
        )
        engine.register_player(target.tournament_id, str(interaction.user.id))
        count = len(engine.tournament(target.tournament_id).players)
        await interaction.response.send_message(
            f"{interaction.user.mention} is in **{target.name}** — "
            f"{count} signed up so far. Lock in your Deck with `/submit-deck` "
            "before the start."
        )

    @bot.tree.command(description="Leave a Tournament's sign-up list before it starts")
    @app_commands.guild_only()
    @app_commands.describe(tournament="Tournament ID or name; defaults if unique")
    async def unregister(
        interaction: discord.Interaction, tournament: str | None = None
    ) -> None:
        """The self-service mirror of `/signup` (issue #20): the player leaves
        the roster entirely, Deck and all — no confirm step, matching signup.
        Signing up again while registration is open starts fresh."""
        target = resolve_tournament(engine, tournament, _STARTABLE, "still in signups")
        player_id = str(interaction.user.id)
        if player_id not in target.players:
            raise CommandError(f"you are not signed up for **{target.name}**")
        unregister_and_discard(bot, target.tournament_id, player_id, player_id)
        count = len(engine.tournament(target.tournament_id).players)
        await interaction.response.send_message(
            f"{interaction.user.mention} has left **{target.name}** — "
            f"{count} still signed up. Changed your mind? `/signup` again "
            "while registration is open; your Deck starts fresh."
        )

    @bot.tree.command(
        name="submit-deck",
        description="Privately lock in your Deck; resubmitting replaces it",
    )
    @app_commands.guild_only()
    @app_commands.describe(
        image="A screenshot of your decklist (preferred)",
        deck="Your decklist as text, or a deckbuilder link",
        tournament="Tournament ID or name; defaults if unique",
    )
    async def submit_deck(
        interaction: discord.Interaction,
        image: discord.Attachment | None = None,
        # Capped so the Reveal post (attribution line + quoted Deck) always
        # fits Discord's 2000-character message limit — Decks are immutable
        # once Revealed, so an unpostable one could never be fixed.
        deck: app_commands.Range[str, 1, 1500] | None = None,
        tournament: str | None = None,
    ) -> None:
        """Ephemeral end to end: neither the submission nor the confirmation
        leaves a channel-history trace, so a Sealed Deck is truly Sealed
        (spec #1 stories 2, 3). The confirmation echoes what is now on file."""
        target = resolve_tournament(
            engine, tournament, _ACCEPTING_DECKS, "accepting Decks"
        )
        if (deck is None) == (image is None):
            raise CommandError(
                "submit your decklist in exactly one form: a screenshot "
                "`image` (preferred), or `deck` text / a deckbuilder link"
            )
        tournament_id = target.tournament_id
        player_id = str(interaction.user.id)
        if image is not None:
            validate_deck_attachment(image.content_type, image.filename, image.size)
            # Downloading the screenshot can outlast the 3s interaction window.
            await interaction.response.defer(ephemeral=True)
            try:
                content = await image.read()
            except discord.HTTPException as error:
                raise CommandError(
                    "could not download that image from Discord; try again"
                ) from error
            engine.submit_deck(
                tournament_id, player_id, deck_image_marker(image.filename)
            )
            bot.deck_images.save_image(
                tournament_id, player_id, DeckImage(image.filename, content)
            )
        else:
            assert deck is not None
            engine.submit_deck(tournament_id, player_id, deck)
            # A text Deck replaces an image one; drop the stale bytes.
            bot.deck_images.delete_image(tournament_id, player_id)
        on_file = engine.deck(tournament_id, player_id, requested_by=player_id)
        suffix, files = bot.presented_deck(tournament_id, player_id, on_file)
        message = f"Deck locked in for **{target.name}**, this message is only visible to you."
        if interaction.response.is_done():
            await interaction.followup.send(message, files=files, ephemeral=True)
        else:
            await interaction.response.send_message(
                message, files=files, ephemeral=True
            )

    @bot.tree.command(
        name="report-score",
        description="Report your Match result from its Match thread",
    )
    @app_commands.guild_only()
    @app_commands.describe(
        score="Game score, winner's count first: 2-0, 2-1, or 1-1-1 for a draw",
        winner="Who won the Match; leave empty for a draw",
    )
    async def report_score(
        interaction: discord.Interaction,
        score: str,
        winner: discord.Member | None = None,
    ) -> None:
        """Either player reports winner + game score; the opponent gets
        one-click confirm / Dispute (spec #1 stories 16, 17). Re-reporting
        replaces a Pending or Disputed result with a fresh Pending one."""
        target, match = open_match_for_thread(
            engine, bot.bindings_store, interaction.channel_id
        )
        games_won, games_lost, games_drawn = parse_score(score)
        engine.report_result(
            target.tournament_id,
            match.match_id,
            reported_by=str(interaction.user.id),
            winner=str(winner.id) if winner is not None else None,
            games_won=games_won,
            games_lost=games_lost,
            games_drawn=games_drawn,
        )
        reporter = str(interaction.user.id)
        opponent = match.player_b if reporter == match.player_a else match.player_a
        assert opponent is not None
        # Only the opponent is pinged below; the reporter and winner are
        # named as text so they never render as raw tags (issue #34).
        outcome = (
            f"a **{format_score(games_won, games_lost, games_drawn)} draw**"
            if winner is None
            else f"**{winner.display_name} won "
            f"{format_score(games_won, games_lost, games_drawn)}**"
        )
        view = discord.ui.View(timeout=None)
        for verb in _PENDING_RESULT_ACTIONS:
            view.add_item(
                PendingResultButton(
                    verb,
                    match.match_id,
                    reporter,
                    str(winner.id) if winner is not None else "draw",
                    f"{games_won}-{games_lost}-{games_drawn}",
                )
            )
        await interaction.response.send_message(
            f"{interaction.user.display_name} reports {outcome}.\n"
            f"<@{opponent}> — Confirm or Dispute below:",
            view=view,
            allowed_mentions=discord.AllowedMentions(
                everyone=False, roles=False, users=[discord.Object(int(opponent))]
            ),
        )

    @bot.tree.command(description="Check that the bot is alive")
    async def ping(interaction: discord.Interaction) -> None:
        latency_ms = round(bot.latency * 1000)
        await interaction.response.send_message(f"Pong! ({latency_ms}ms)")

    @bot.tree.error
    async def on_command_error(
        interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.NoPrivateMessage):
            message = "This command only works in the community server."
        elif isinstance(error, app_commands.CheckFailure):
            message = "This command is reserved for the TO role."
        elif isinstance(error, app_commands.CommandInvokeError) and isinstance(
            error.original, EngineError | CommandError
        ):
            message = str(error.original)
        else:
            raise error  # let discord.py log the unexpected traceback
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.NotFound:
            # The interaction expired before we could answer (e.g. delivered
            # late after a gateway resume); nobody is listening anymore.
            pass


def _required_env(name: str, hint: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"{name} is not set. {hint}")
    return value


def run() -> None:
    load_dotenv()
    token = _required_env(
        "DISCORD_TOKEN",
        "Copy .env.example to .env and add your bot token.",
    )
    to_role_id = int(
        _required_env(
            "TO_ROLE_ID",
            "Set it to the Discord role ID that grants Tournament Organizer "
            "powers (see .env.example).",
        )
    )
    guild_id = os.environ.get("GUILD_ID")
    db_path = os.environ.get("DB_PATH", "tournaments.db")
    bot = MultiverseBot(
        engine=open_engine(db_path),
        bindings_store=BindingsStore(db_path),
        deck_images=DeckImageStore(db_path),
        to_role_id=to_role_id,
        guild_id=int(guild_id) if guild_id else None,
    )
    bot.run(token)
