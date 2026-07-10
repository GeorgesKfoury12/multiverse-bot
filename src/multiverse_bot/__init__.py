def main() -> None:
    # Imported lazily so the pure engine stays importable without discord.
    from multiverse_bot.bot import run

    run()
