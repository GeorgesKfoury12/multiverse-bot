# Multiverse Tournaments

Discord bot that runs online Swiss TCG tournaments for the Multiverse LGS community — pairings, standings, and match tracking — alongside the store's physical events.

## Language

**Game**:
A TCG that tournaments can be run for (first: Riftbound). Each Game carries its own ruleset configuration; the tournament engine itself is game-agnostic.
_Avoid_: title, format (reserved for possible in-game formats like Standard)

**Tournament**:
A single Swiss competitive event for one Game, typically spanning a week with roughly one round per day. Players sign up and submit a deck before it starts.
_Avoid_: event, league

**Nexus League**:
A month-long league made up of weekly Tournaments. Players earn points toward the league from each Tournament they play; skipping weeks is allowed and simply earns no points.
_Avoid_: season (until the community uses it)

**Round**:
One step of a Swiss Tournament, lasting about a day. Pairings are published, paired players schedule and play their Match within the round. A Round closes automatically when all results are in, or when the TO force-closes it. The TO can reopen the most recently closed Round to correct a mistaken result — reverting the next Round's Pairings (or un-completing the Tournament after the final Round), allowed only while the next Round has no confirmed results; the correction re-closes the Round and regenerates the Pairings.

**Tournament Organizer (TO)**:
The human running a Tournament. Policy calls stay with the TO, not the bot: resolving unplayed Matches, force-closing Rounds, and ending a Tournament early.
_Avoid_: admin, mod (Discord roles are not domain roles)

**Deck**:
The list a player locks in for a whole Tournament, submitted privately to the bot before the start — typically a screenshot image (preferred; easiest to read during open-decklist play), or a text list or deckbuilder link. Stored and Revealed verbatim, unparsed. Resubmitting before the start replaces it; only the latest counts. The TO can view Decks at any time.

**Sealed / Reveal**:
Decks are Sealed (visible only to their owner and the TO) until the Tournament starts, then Revealed to everyone at once — Tournaments are open-decklist. A Tournament cannot start while a registered player has no Deck; the TO resolves stragglers (chase or drop).

**Reported Result**:
A Match result submitted by either player as winner + game score (e.g. "Georges won 2-1"). It is Pending until the opponent confirms or disputes it; the TO can confirm, correct, or replace it any time before the Round closes. Frozen once the Round closes, unless the TO reopens the Round.

**Pending**:
The state of a Reported Result awaiting opponent confirmation. A Round cannot auto-advance while any result is Pending.

**Dispute**:
The opponent's explicit rejection of a Pending result, flagging the Match for TO resolution. There is no auto-confirm timer.

**Drop**:
A player's permanent exit from a Tournament between Rounds, whether self-initiated or TO-initiated (e.g. unresponsiveness). Dropped players are not paired again but their played Matches still count for opponents' points and Tiebreakers; they keep their place in Standings. Not retroactive, not reversible.
_Avoid_: kick, remove (deletion is exactly what a Drop is not)

**Assigned Result**:
A Match result set by the TO instead of reported by the players — used for no-shows and force-closed Rounds. Counts identically to a reported result once set.
_Avoid_: forfeit (an Assigned Result need not blame anyone)

**Match**:
A best-of-3 series between two paired players in a Round, worth Match Points (win 3, draw 1, loss 0).
_Avoid_: game (a Game of Riftbound is one game inside a Match; Game also means the TCG itself — prefer "game" lowercase for the in-match sense)

**Match Points**:
Points a player accumulates within a single Tournament from Match results; the basis for standings and pairings.

**Standings**:
The ranked list of a Tournament's players, ordered by Match Points then Tiebreakers. Final Standings decide the Tournament winner — there is no top cut.
_Avoid_: leaderboard (reserve for the Nexus League level)

**Pairings**:
The set of Matches for a Round: players paired randomly within Score Groups, never against a previous opponent (hard constraint), with pair-downs minimized.

**Score Group**:
All players currently on the same Match Points total; the unit within which Pairings are made.
_Avoid_: bracket

**Bye**:
The free win given to the lowest-ranked bye-less player when the player count is odd. Scores as a 2-0 Match win (3 Match Points) and is excluded from the byed player's Tiebreaker calculations. No second bye unless unavoidable.

**Tiebreakers**:
The standard TCG stack applied in order when Match Points are equal: OMW% (opponents' match-win %, floored at 33.3%), then GW% (own game-win %), then OGW% (opponents' game-win %). Defined per Game ruleset; players still tied share the placement.
