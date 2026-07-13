"""Resolving engine player IDs to display names (issue #34).

Messages that suppress pings carry no member data for their mentions, so an
uncached client renders the raw ``<@id>`` tag; those messages say the display
name as plain text instead. The resolver is exercised through duck-typed
stand-ins for the guild and client — the only Discord surface it touches.
"""

import asyncio
from types import SimpleNamespace

import discord

from multiverse_bot.bot import mention_names, player_names


def _named(name: str) -> SimpleNamespace:
    return SimpleNamespace(display_name=name)


def _not_found() -> discord.NotFound:
    return discord.NotFound(
        SimpleNamespace(status=404, reason="Not Found"), "Unknown User"
    )


class FakeGuild:
    def __init__(
        self,
        members: dict[int, SimpleNamespace] | None = None,
        fetchable: dict[int, SimpleNamespace] | None = None,
    ) -> None:
        self._members = members or {}
        self._fetchable = fetchable or {}

    def get_member(self, user_id: int) -> SimpleNamespace | None:
        return self._members.get(user_id)

    async def fetch_member(self, user_id: int) -> SimpleNamespace:
        member = self._fetchable.get(user_id)
        if member is None:
            raise _not_found()
        return member


class FakeClient:
    def __init__(self, users: dict[int, SimpleNamespace] | None = None) -> None:
        self._users = users or {}

    async def fetch_user(self, user_id: int) -> SimpleNamespace:
        user = self._users.get(user_id)
        if user is None:
            raise _not_found()
        return user


def test_a_cached_member_shows_their_server_display_name() -> None:
    guild = FakeGuild(members={7: _named("Alice")})

    names = asyncio.run(player_names(FakeClient(), guild, ["7"]))

    assert names == {"7": "Alice"}


def test_an_uncached_member_is_fetched_from_the_guild() -> None:
    guild = FakeGuild(fetchable={7: _named("Alice")})

    names = asyncio.run(player_names(FakeClient(), guild, ["7"]))

    assert names == {"7": "Alice"}


def test_a_player_who_left_the_server_shows_their_global_profile_name() -> None:
    client = FakeClient(users={7: _named("Alice")})

    names = asyncio.run(player_names(client, FakeGuild(), ["7"]))

    assert names == {"7": "Alice"}


def test_an_id_discord_cannot_resolve_still_reads_as_a_player() -> None:
    names = asyncio.run(player_names(FakeClient(), FakeGuild(), ["7"]))

    assert names == {"7": "player 7"}


def test_a_non_snowflake_id_never_reaches_discord() -> None:
    # Engine IDs are opaque strings; only production guarantees snowflakes.
    names = asyncio.run(player_names(FakeClient(), FakeGuild(), ["alice"]))

    assert names == {"alice": "player alice"}


def test_every_requested_player_resolves_once() -> None:
    guild = FakeGuild(members={7: _named("Alice"), 8: _named("Bob")})

    names = asyncio.run(player_names(FakeClient(), guild, ["7", "8", "7"]))

    assert names == {"7": "Alice", "8": "Bob"}


def test_a_byes_missing_opponent_is_simply_absent() -> None:
    guild = FakeGuild(members={7: _named("Alice")})

    names = asyncio.run(player_names(FakeClient(), guild, ["7", None]))

    assert names == {"7": "Alice"}


def test_mention_names_render_mentions_and_skip_a_missing_opponent() -> None:
    assert mention_names("7", None) == {"7": "<@7>"}
