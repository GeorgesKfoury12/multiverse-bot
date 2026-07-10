"""Discord adapter: slash commands in, engine calls out (ticket #9).

Thin by design (spec #1): every command translates one-to-one into an engine
command or query, TO authorization is one configured Discord role, and the
adapter's only own state is the persisted channel wiring. Restarting the bot
is therefore just ``open_engine`` plus reading that wiring back.

Engine player IDs are Discord user IDs as strings, so ``<@id>`` mentions are
the display form everywhere.
"""

import os
import random

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from multiverse_bot.engine import EngineError, Tournament, TournamentEngine
from multiverse_bot.store import BindingsStore, ChannelBindings, open_engine


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


class MultiverseBot(commands.Bot):
    def __init__(
        self,
        engine: TournamentEngine,
        bindings_store: BindingsStore,
        to_role_id: int,
        guild_id: int | None = None,
    ) -> None:
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)
        self.engine = engine
        self.bindings_store = bindings_store
        self.to_role_id = to_role_id
        self.guild_id = guild_id
        _install_commands(self)

    async def setup_hook(self) -> None:
        if self.guild_id is not None:
            # Guild-scoped sync is instant; global sync can take an hour.
            guild = discord.Object(self.guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

    async def on_ready(self) -> None:
        print(f"Logged in as {self.user} (id: {self.user.id})", flush=True)


async def announce_pairings(bot: MultiverseBot, tournament_id: str) -> None:
    """Post the current Round's Pairings in the bound pairings channel and
    open one thread per Match with both players pinged inside; a Bye is
    announced in the post itself (spec #1 stories 9, 10, 13).

    Shared seam for every Round: ``/tournament start`` posts Round 1 here, and
    the result flow (ticket #10) will call it as confirmations close Rounds.
    Safe to re-run (``/tournament post-pairings``): Matches whose thread is
    already on file keep it, so a crash mid-announcement is recoverable
    without duplicate threads.
    """
    engine = bot.engine
    tournament = engine.tournament(tournament_id)
    round_number = tournament.current_round
    assert round_number is not None
    bindings = bot.bindings_store.bindings(tournament_id)
    if bindings is None:
        raise CommandError(f"{tournament_id} has no channel bindings on file")
    channel = bot.get_channel(bindings.pairings_channel_id) or await bot.fetch_channel(
        bindings.pairings_channel_id
    )
    if not isinstance(channel, discord.TextChannel):
        raise CommandError(
            f"the pairings binding for {tournament_id} is not a text channel"
        )
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
        # Opening a thread per Match is a Discord round-trip each; buy time.
        await interaction.response.defer()
        warning = engine.start_tournament(
            target.tournament_id, seed=random.randrange(2**63), round_count=rounds
        )
        await announce_pairings(bot, target.tournament_id)
        started = engine.tournament(target.tournament_id)
        lines = [
            f"**{target.name}** ({target.tournament_id}) has started: "
            f"{len(started.players)} players, {started.round_count} Rounds. "
            "Round 1 Pairings are up!"
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
        deck="Your decklist: text or a deckbuilder link",
        tournament="Tournament ID or name; defaults if unique",
    )
    async def submit_deck(
        interaction: discord.Interaction, deck: str, tournament: str | None = None
    ) -> None:
        target = resolve_tournament(
            engine, tournament, _ACCEPTING_DECKS, "accepting Decks"
        )
        engine.submit_deck(target.tournament_id, str(interaction.user.id), deck)
        # Ephemeral: a Sealed submission leaves no channel-history trace.
        await interaction.response.send_message(
            f"Deck locked in for **{target.name}**, Sealed until the start. "
            f"On file:\n>>> {deck}",
            ephemeral=True,
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
        to_role_id=to_role_id,
        guild_id=int(guild_id) if guild_id else None,
    )
    bot.run(token)
