# TO agency over automation

Tournaments run async over a week (~one round per day) and real life causes delays, so the bot deliberately has **no timers and no automatic policy decisions**: no round deadlines, no auto-forfeits for no-shows, no auto-confirm of pending results. Every policy call — resolving an unplayed Match, force-closing a Round, ending a Tournament early, dropping an unresponsive player — is an explicit, first-class TO action (see Assigned Result, Drop in `CONTEXT.md`). The bot's job is to make those calls cheap and visible, not to make them.

## Consequences

- A Round advances only when all results are confirmed or the TO force-closes it; a stuck Round is a TO nudge away, never a bot judgment call.
- Do not "helpfully" add auto-forfeit/auto-close timers. If the community later settles a standing no-show policy, automate it as an *opt-in default the TO can override*, superseding this ADR explicitly.
