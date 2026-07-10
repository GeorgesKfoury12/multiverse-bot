# multiverse-bot

Discord bot for our local TCG community: run online Swiss tournaments with
pairings, standings, and match tracking, right from Discord.

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)

## Setup

```bash
git clone git@github.com:GeorgesKfoury12/multiverse-bot.git
cd multiverse-bot
uv sync
cp .env.example .env  # then fill in DISCORD_TOKEN and TO_ROLE_ID (see comments)
```

## Running

```bash
uv run multiverse-bot
```

## Development

```bash
uv run ruff check .   # lint
uv run ruff format .  # format
uv run pytest         # tests
```
