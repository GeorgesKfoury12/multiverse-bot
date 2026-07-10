# House-policy defaults where official Riftbound rules are silent

Riot's official Riftbound Tournament Rules confirm Bo3, the existence of draws, and the tiebreaker order OMW% → GW% → OGW% → random, but are **silent** on match point values, Swiss round counts by attendance, tiebreaker formulas/floors, and bye handling (see `docs/research/riftbound-decks-and-op-rules.md`). We fill those gaps with MTG-derived house policy: match points 3/1/0 (win/draw/loss); round count from the standard Swiss table (ceil(log₂ n)) with TO override; OMW%/OGW% floored at 33.3%; byes score as a 2-0 win and are excluded from the byed player's tiebreakers.

These values live in per-Game ruleset configuration, not constants — the tournament engine is game-agnostic and other TCGs will plug in their own.

## Consequences

- Changing any of these mid-league breaks comparability of standings across that league's tournaments; change them only between leagues.
- If Riot later publishes official values that differ, adopting them is a ruleset-config change plus a superseding ADR, not a code rewrite.
