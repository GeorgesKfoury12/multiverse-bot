# TO agency over automation

Tournaments run async over a week (~one round per day) and real life causes delays, so the bot deliberately has **no timers and no automatic policy decisions**: no round deadlines, no auto-forfeits for no-shows, no auto-confirm of pending results. Every policy call — resolving an unplayed Match, force-closing a Round, ending a Tournament early, dropping an unresponsive player — is an explicit, first-class TO action (see Assigned Result, Drop in `CONTEXT.md`). The bot's job is to make those calls cheap and visible, not to make them.

## Consequences

- A Round advances only when all results are confirmed or the TO force-closes it; a stuck Round is a TO nudge away, never a bot judgment call.
- One deliberate carve-out (issue #37): when the schedule outlives the pairable Rounds — no rematch-free pairing exists for the next scheduled Round — closing the current one completes the Tournament automatically, Standings-so-far final, with the final Standings announcing why. This is not a policy call taken from the TO: with rematches a hard constraint, no choice exists to leave them, and the alternative stranded the Tournament with no path to any final state. The reopen window still applies as for any completion on results.
- Do not "helpfully" add auto-forfeit/auto-close timers. If the community later settles a standing no-show policy, automate it as an *opt-in default the TO can override*, superseding this ADR explicitly.
