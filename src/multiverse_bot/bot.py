"""Discord adapter: slash commands and buttons in, engine calls out
(tickets #9, #10, #11).

Thin by design (spec #1): every command translates one-to-one into an engine
command or query, TO authorization is one configured Discord role, and the
adapter's only own state is persisted Discord wiring — channel bindings,
Match threads, and the bytes behind image Decks. Restarting the bot is
therefore just ``open_engine`` plus reading that wiring back — the
confirm/Dispute buttons are stateless too, resolved from their custom_id.

Engine player IDs are Discord user IDs as strings, so ``<@id>`` mentions are
the display form everywhere.
"""

import io
import os
import random
import re

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


def _percent(value: Fraction) -> str:
    return f"{float(value):.1%}"


def standings_lines(
    tournament: Tournament, standings: tuple[Standing, ...]
) -> list[str]:
    """The Standings post, one ranked row per player with the full Tiebreaker
    stack visible so placements explain themselves (spec #1 story 27). Final
    Standings crown the winner — rank-1 players still tied through the whole
    stack share the title (story 28)."""
    if tournament.phase == "completed":
        title = f"## {tournament.name} — Final Standings"
    else:
        title = (
            f"## {tournament.name} — Standings entering Round "
            f"{tournament.current_round}/{tournament.round_count}"
        )
    lines = [title]
    for row in standings:
        dropped = " (dropped)" if row.player_id in tournament.dropped else ""
        lines.append(
            f"{row.rank}. <@{row.player_id}>{dropped} — **{row.match_points} pts** "
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
        # Confirm/Dispute buttons are matched by custom_id, so the ones on
        # messages posted before a restart keep working.
        self.add_dynamic_items(PendingResultButton)
        if self.guild_id is not None:
            # Guild-scoped sync is instant; global sync can take an hour.
            guild = discord.Object(self.guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

    async def on_ready(self) -> None:
        print(f"Logged in as {self.user} (id: {self.user.id})", flush=True)

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

    async def display_name(player_id: str) -> str:
        member = channel.guild.get_member(int(player_id))
        if member is None:
            try:
                member = await channel.guild.fetch_member(int(player_id))
            except discord.HTTPException:
                return f"player {player_id}"
        return member.display_name

    lines = [
        f"## {tournament.name} — Round {round_number}/{tournament.round_count} Pairings"
    ]
    for index, match in enumerate(matches, start=1):
        if match.is_bye:
            lines.append(
                f"{index}. <@{match.player_a}> has the **Bye** — scored as a "
                f"{match.games_won}-{match.games_lost} win. Enjoy the day off!"
            )
        else:
            lines.append(f"{index}. <@{match.player_a}> vs <@{match.player_b}>")
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
        versus = (
            f"{await display_name(match.player_a)} vs "
            f"{await display_name(match.player_b)}"
        )
        thread = await channel.create_thread(
            name=f"R{round_number}: {versus}"[:100],
            type=discord.ChannelType.public_thread,
        )
        await thread.send(
            f"<@{match.player_a}> <@{match.player_b}> — Round {round_number}: "
            "schedule and play your Match here, then report the result."
        )
        bot.bindings_store.save_match_thread(match.match_id, thread.id)


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
    await channel.send(
        f"## {tournament.name} — Deck Reveal\n"
        "Sealed no more: every player's Deck, all at once. Lists are locked "
        "for the whole Tournament — study away."
    )
    for player_id in tournament.players:
        # Post-start every Deck is public; the owner query works in any phase.
        deck = engine.deck(tournament_id, player_id, requested_by=player_id)
        suffix, files = bot.presented_deck(tournament_id, player_id, deck)
        await channel.send(
            f"**<@{player_id}>'s Deck**{suffix}",
            files=files,
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def announce_result(
    bot: MultiverseBot, tournament: Tournament, match: Match
) -> None:
    """Announce one confirmed result in the bound scores channel, keeping the
    community's at-a-glance record in sync (spec #1 story 18)."""
    channel = await bound_channel(bot, tournament.tournament_id, "scores")
    assert match.games_won is not None
    assert match.games_lost is not None and match.games_drawn is not None
    score = format_score(match.games_won, match.games_lost, match.games_drawn)
    if match.winner is None:
        headline = f"<@{match.player_a}> and <@{match.player_b}> drew {score}"
    else:
        loser = match.player_b if match.winner == match.player_a else match.player_a
        headline = f"<@{match.winner}> beat <@{loser}> {score}"
    await channel.send(
        f"⚔️ **{tournament.name}** Round {match.round_number}: {headline}",
        allowed_mentions=discord.AllowedMentions.none(),
    )


async def announce_standings(bot: MultiverseBot, tournament_id: str) -> None:
    """Post the current Standings in the bound standings channel; final
    Standings also crown (and ping) the Tournament winner."""
    tournament = bot.engine.tournament(tournament_id)
    standings = bot.engine.standings(tournament_id)
    channel = await bound_channel(bot, tournament_id, "standings")
    # Standings rows never ping; the winner announcement is the exception.
    champions = (
        [discord.Object(int(row.player_id)) for row in standings if row.rank == 1]
        if tournament.phase == "completed"
        else []
    )
    await channel.send(
        "\n".join(standings_lines(tournament, standings)),
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
    winner (story 28).

    Shared seam for everything that confirms results: the confirm button now,
    the TO's corrections (ticket #12) later.
    """
    tournament = bot.engine.tournament(tournament_id)
    if tournament.phase == "completed":
        await announce_standings(bot, tournament_id)
    elif tournament.current_round != round_before:
        await announce_standings(bot, tournament_id)
        await announce_pairings(bot, tournament_id)


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
        # a Discord round-trip and the interaction token is on a clock.
        mark = "✅ Confirmed" if self.verb == "confirm" else "⚠️ Disputed"
        assert interaction.message is not None
        await interaction.response.edit_message(
            content=(
                f"{interaction.message.content}\n{mark} by {interaction.user.mention}."
            ),
            view=None,
        )
        if self.verb == "confirm":
            await self._announce_confirmation(
                bot, interaction, tournament, round_before
            )
        else:
            await self._flag_dispute_to_to(bot, interaction)

    async def _announce_confirmation(
        self,
        bot: "MultiverseBot",
        interaction: discord.Interaction,
        tournament: Tournament,
        round_before: int,
    ) -> None:
        """The confirmed result is in the engine; announce it — and whatever
        Round close it triggered — without dying silently if a channel post
        fails, since the confirmation itself already stuck."""
        try:
            confirmed = _match_in_round(
                bot.engine, tournament.tournament_id, round_before, self.match_id
            )
            assert confirmed is not None
            await announce_result(bot, tournament, confirmed)
            await advance_announcements(bot, tournament.tournament_id, round_before)
        except (EngineError, CommandError, discord.HTTPException) as error:
            await interaction.followup.send(
                f"The result is confirmed, but announcing it failed: {error}\n"
                "A TO can re-post with `/tournament post-standings` and "
                "`/tournament post-pairings`."
            )

    async def _flag_dispute_to_to(
        self, bot: "MultiverseBot", interaction: discord.Interaction
    ) -> None:
        """Ping the TO with the Match thread as the resolution context
        (spec #1 story 19)."""
        thread_id = bot.bindings_store.match_thread(self.match_id)
        where = f"<#{thread_id}>" if thread_id is not None else "its Match thread"
        await interaction.followup.send(
            f"<@&{bot.to_role_id}> — {interaction.user.mention} disputed the "
            f"Reported Result in {where}. Sort it out there, then either "
            "player can `/report` again — or the TO rules on it.",
            allowed_mentions=discord.AllowedMentions(
                everyone=False,
                roles=[discord.Object(bot.to_role_id)],
                users=False,
            ),
        )


def _install_commands(bot: MultiverseBot) -> None:
    engine = bot.engine

    def is_to(interaction: discord.Interaction) -> bool:
        roles = getattr(interaction.user, "roles", ())
        return any(role.id == bot.to_role_id for role in roles)

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
        # The Reveal and a thread per Match are a Discord round-trip each.
        await interaction.response.defer()
        warning = engine.start_tournament(
            target.tournament_id, seed=random.randrange(2**63), round_count=rounds
        )
        await announce_reveal(bot, target.tournament_id)
        await announce_pairings(bot, target.tournament_id)
        started = engine.tournament(target.tournament_id)
        lines = [
            f"**{target.name}** ({target.tournament_id}) has started: "
            f"{len(started.players)} players, {started.round_count} Rounds. "
            "Decks are Revealed and Round 1 Pairings are up!"
        ]
        if warning is not None:
            lines.append(f"⚠️ {warning}")
        await interaction.followup.send("\n".join(lines))

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
        missing too."""
        target = resolve_tournament(
            engine, tournament, _RESULTED, "in progress or completed"
        )
        await interaction.response.defer()
        await announce_standings(bot, target.tournament_id)
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
            f"{player.mention}'s Deck in **{target.name}**:{suffix}",
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
        message = (
            f"Deck locked in for **{target.name}**, Sealed until the start. "
            f"On file:{suffix}"
        )
        if interaction.response.is_done():
            await interaction.followup.send(message, files=files, ephemeral=True)
        else:
            await interaction.response.send_message(
                message, files=files, ephemeral=True
            )

    @bot.tree.command(description="Report your Match result from its Match thread")
    @app_commands.guild_only()
    @app_commands.describe(
        score="Game score, winner's count first: 2-0, 2-1, or 1-1-1 for a draw",
        winner="Who won the Match; leave empty for a draw",
    )
    async def report(
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
        outcome = (
            f"a **{format_score(games_won, games_lost, games_drawn)} draw**"
            if winner is None
            else f"**{winner.mention} won "
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
            f"{interaction.user.mention} reports {outcome}.\n"
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
