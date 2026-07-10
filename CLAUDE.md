# multiverse-bot

Discord bot for running online Swiss TCG tournaments (pairings, standings, match tracking) for our local TCG community. Python 3.12+, managed with uv, built on discord.py.

- Run the bot: `uv run multiverse-bot` (needs `DISCORD_TOKEN` in `.env`; see `.env.example`)
- Lint/format: `uv run ruff check .` / `uv run ruff format .`
- Tests: `uv run pytest`

## Agent skills

### Issue tracker

Issues are tracked as GitHub Issues on GeorgesKfoury12/multiverse-bot via the `gh` CLI; external PRs are not a triage surface. See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical triage labels are used unmodified (needs-triage, needs-info, ready-for-agent, ready-for-human, wontfix). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `CONTEXT.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.
