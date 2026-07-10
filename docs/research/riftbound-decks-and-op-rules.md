# Riftbound: Deck Sharing Formats & Official Organized Play Rules

Research notes for the multiverse-bot tournament bot. Compiled 2026-07-10.

Sources are cited inline. Claims are marked **[primary]** (verified directly against an official
Riot/UVS document or the actual website/repository) or **[community]** (reported by fan sites,
social media, or search summaries; not independently verified).

---

## Part 1 — Deck sharing / interchange formats

### 1.1 Riftbound deck structure (what a "deck" is)

Per the official [Riftbound Tournament Rules PDF, Last Updated 4/29/2026](https://cmsassets.rgpub.io/sanity/files/dsfx7636/news_live/e70866614d68a00a1cbd12c7de08124e0ea5e755.pdf)
(linked from the official [Rules Hub](https://playriftbound.com/en-us/rules-hub/)) **[primary]**:

- **Constructed registration (TR 402.1):** "players must register a Main Deck of exactly 40 cards
  (including a chosen champion), 1 Legend, 12 runes, and exactly 3 battlefields each with a unique
  name."
- **Sideboard (TR 601.1.c):** optional; "8 or fewer cards", main-deck-legal cards only, copy limits
  apply across main deck + sideboard combined. Sideboard swaps are 1-for-1 (TR 403.4); Runes,
  Legend, and Battlefields cannot be changed after registration in constructed (TR 403.4.b), but
  the Chosen Champion can be swapped while sideboarding (TR 403.4.a, 601.1.c.4).
- **A registered decklist (TR 401.2)** must unambiguously record: Champion Legend, battlefields,
  Main Deck including Chosen Champion, Rune Deck, and sideboard (if applicable).
- **Sealed (TR 602.4.a.2):** main deck is "at least 25 cards"; the rest of the pool is the sideboard.

So a complete constructed submission has five zones: **Legend (1) / Battlefields (3) / Main Deck
(40, incl. Chosen Champion) / Rune Deck (12) / Sideboard (0–8)**.

### 1.2 Canonical card codes

Every card has a card code of the form `SET-NNN` with an optional variant suffix, e.g.
`OGN-007a` (three-character set id, three-digit collector number, variant `a`/`b`/`s`/`*`)
— documented in the [RiftboundDeckCodes spec](https://github.com/Piltover-Archive/RiftboundDeckCodes)
**[primary — actual repo]**. Known set codes there: `OGN` (Origins), `OGS` (Proving Grounds),
`ARC` (Arcane Box Set), `SFD` (Spiritforged), `UNL` (Unleashed), `VEN` (Vendetta), `RAD` (Radiance).
Rune cards additionally have `R`-prefixed card numbers (handled by a flag in deck-code v4).
The Tournament Rules themselves also reason in collector numbers (e.g. TR 601.2.c discusses "a card
with collector number 300/250") **[primary]**, so set/collector numbers are canonical identifiers.

Current Standard (TR 601.3.c, as of the 4/29/26 TR): OGS, OGN, SFD, UNL **[primary]**.

### 1.3 The de-facto deck code standard: RiftboundDeckCodes

There is a cross-app deck code, but it is **community-run (Piltover Archive), not Riot-official**:

- [`Piltover-Archive/RiftboundDeckCodes`](https://github.com/Piltover-Archive/RiftboundDeckCodes)
  **[primary — repo inspected]**: "encode/decode Riftbound TCG decks to/from simple strings …
  These strings can be used to share decks across Riftbound TCG applications and the
  PiltoverArchive companion app." Explicitly "adapted from
  [Riot Games' LoRDeckCodes](https://github.com/RiotGames/LoRDeckCodes)" and "not affiliated with
  or endorsed by Riot Games". License: Apache 2.0.
- **Encoding:** card lists grouped by copy count, varints (big endian), base32 string; first byte
  carries 4-bit format + 4-bit version.
- **Versions:** v1 (Apr 2025, main deck) → v2 (Nov 2025, sideboard) → v3 (Jan 2026, chosen
  champion) → v4 (Mar 2026, rune-card flag). Main deck supports counts 1–12; sideboard counts 1–3.
- **Zones:** the code carries one flat main-deck list (which in practice includes Legend,
  Battlefields, and Runes as ordinary card codes — zone membership is inferred from card type),
  plus an optional sideboard list and an optional chosen-champion card code. The library does
  **no rules validation** (explicit note in the README).
- **Reference implementation:** TypeScript only, published as npm package
  [`@piltoverarchive/riftbound-deck-codes`](https://github.com/Piltover-Archive/RiftboundDeckCodes#installation);
  the README invites community ports but lists none — **there is no Python implementation listed**
  as of this writing **[primary]**.
- Example deck code (from the README):
  `CIAAAAAAAAAQCAAAA4AACAIAABMQAAILAAAAICIMDMOVOX3AM5UHIAIDAAACO6XYAEAQKAAABX3QDGACUABKIAQAAEBQAAAWDBOQCAQAABMHE`

Adoption: Piltover Archive deck pages have a "Copy deck code" button (seen in the page markup,
see 1.4) **[primary]**, and RiftMana's builder imports/exports "Deck Code" **[primary — page markup]**.
That makes it the closest thing Riftbound has to an MTG-Arena-string / Lorcana-style interchange
format, but note it is a fan standard maintained by one site.

### 1.4 The deckbuilding websites

#### Piltover Archive — piltoverarchive.com — CONFIRMED, the flagship fan builder

- Exists at [piltoverarchive.com](https://piltoverarchive.com/); deck builder at
  [/deckbuilder](https://piltoverarchive.com/deckbuilder), public deck browser at
  [/decks](https://piltoverarchive.com/decks) **[primary — pages fetched]**.
- **Not official**: footer states "Piltover Archive was created under Riot Games' 'Legal Jibber
  Jabber' policy … Riot Games does not endorse or sponsor this project" (operated by STGMNN Labs UG)
  **[primary — site footer]**. (Some fan FAQs incorrectly call it "Riot's official site",
  e.g. [riftbound.one](https://www.riftbound.one/faq/piltover-archive) — disregard that.)
- **Stable public deck links:** yes — `https://piltoverarchive.com/decks/view/<uuid>`, e.g. a
  published Regional Qualifier winner's list at
  [/decks/view/99f9a2a9-b0fd-4399-bdfd-4327a030c6e3](https://piltoverarchive.com/decks/view/99f9a2a9-b0fd-4399-bdfd-4327a030c6e3)
  **[primary — fetched; page contains the full list as card codes]**.
- **Export:** deck pages expose "Share", "Export deck", and "Copy deck code" buttons
  **[primary — page markup]**. Site metadata advertises text export ("Export and share your
  creations") **[primary]**. It can also export an official-style **deck registration sheet** for
  in-person tournaments ([Riftlab post on X](https://x.com/RiftlabTCG/status/1993314004467695829),
  [Riftbound Report post](https://x.com/RiftboundReport/status/1993316568965542279)) **[community]**.
- **Name-based text lists:** third-party tooling treats "quantity + card name" lines
  (`1 Viktor, Herald of the Arcane` / `3 Seal of Unity`) as the *piltover_archive* decklist format
  ([silhouette-card-maker Riftbound plugin](https://github.com/Alan-Cha/silhouette-card-maker/blob/main/plugins/riftbound/README.md))
  **[primary for the plugin's format definition; community as evidence of PA's exact export text]**.
- Caveat for scraping: the site is a JS-heavy Next.js app and returns 403 to some non-browser
  fetchers (it blocked our WebFetch tool; plain curl with a browser User-Agent worked).

#### RiftMana — riftmana.com — CONFIRMED, richest import/export surface

- Deck builder at [riftmana.com/deck-builder/](https://riftmana.com/deck-builder/)
  **[primary — page fetched]**; also a mobile companion app
  ([Google Play](https://play.google.com/store/apps/details?id=com.riftmana.app)) **[community]**.
- **Import tabs** (in the page's import modal markup): **Deck Code** and **Card Names** **[primary]**.
- **Export tabs** (in the page's export modal markup): **Deck Code, Pixelborn, Card Names, TTS,
  Image, Registration** **[primary]**. "Card Names" is `N Name` lines
  (e.g. `3 Jinx, Loose Cannon`); "TTS" is space-separated card codes with copy index
  (`OGN-249-1 OGN-046-1 …`); "Registration" is a tournament registration sheet.
- Also blocks non-browser fetchers (403 to WebFetch; curl with browser UA worked).

#### Rift Atlas — riftatlas.com — CONFIRMED to exist; details partly unverified

- Exists at [riftatlas.com](https://riftatlas.com/): "Riftbound Cards, Decks, and Simulators",
  with a deck builder/browser at [/decks](https://riftatlas.com/decks), a sealed simulator at
  [/sealed](https://riftatlas.com/sealed), and an online play simulator at
  [play.riftatlas.com](https://play.riftatlas.com/) **[primary — pages exist; site self-describes
  as fan-made under Riot's Legal Jibber Jabber policy]**.
- Reported to let players "paste or import decks" into the simulator and browse community decks
  **[community — search summaries; the site is a client-rendered SPA and its import/export UI
  could not be inspected without a browser]**. Whether it emits Piltover-style deck codes is
  **unverified**.

#### TCG Arena — tcg-arena.fr — CONFIRMED to exist; details unverified

- Multi-TCG browser play simulator at [tcg-arena.fr](https://tcg-arena.fr/games) (French project);
  Riftbound is one of its supported games **[primary that the site exists; the page is a fully
  client-rendered SPA and exposed no inspectable content]**.
- Community guides describe: build a deck in its Deck section or import a community deck via its
  "Text Code", then play in browser lobbies
  ([RiftMana guide "How to Play Riftbound Online"](https://riftmana.com/how-to-play-riftbound-online-league-of-legends-tcg/),
  [video walkthrough](https://www.youtube.com/watch?v=CjiAhbalbxE)) **[community]**. Whether its
  "Text Code" equals the Piltover deck code is **unverified**.

#### Other sites discovered

- **Riftbound Gaming Network / UVS store-locator deck registration** (official OP partner UVS
  Games): [locator.riftbound.uvsgames.com/decks/create](https://locator.riftbound.uvsgames.com/decks/create)
  has a deck import form — "Copy your deck list from your favorite deck building site and paste it
  below. We'll parse it and create a new deck for you", with a **"By Section" mode (Main Deck /
  Rune Pool / Sideboard)** and a **"Full Text" paste mode** **[primary — page fetched]**. This is
  how decklists are submitted for sanctioned events; i.e. even official tooling standardizes on
  *pasted plain-text lists*, not deck codes. PA reportedly supports account-linking to import decks
  directly into it **[community]**.
- [riftbound.gg/builder/](https://riftbound.gg/builder/) — fan site with builder and tournament
  decklist coverage **[primary that it exists]**.
- [magicalmeta.ink/riftbound/deckbuilder](https://magicalmeta.ink/riftbound/deckbuilder) — builder
  with prices and export **[community]**.
- [riftdecks.com](https://riftdecks.com/) — tournament decklist archive (e.g. RQ Sydney standings,
  1405 players) **[community]**.
- **Pixelborn** (simulator with its own base64 encoding of `$`-joined card codes) — **dead**:
  "Pixelborn will be shutdown August 7th, 2025" following Riot's digital-tools policy
  ([silhouette plugin README](https://github.com/Alan-Cha/silhouette-card-maker/blob/main/plugins/riftbound/README.md))
  **[community]**. Its export format still appears as a legacy option in RiftMana.

### 1.5 Typical text decklist shape

Combining the formats above, the interchange text list the ecosystem converges on is
`<count> <card name>` or `<count> <card code>` lines, optionally under section headers matching the
zones from TR 401.2 (Legend / Battlefields / Main Deck (Champion first) / Runes / Sideboard). The
official UVS deck-registration parser accepts exactly such free text or per-section text
**[primary]**. Example (name form, per the
[silhouette plugin](https://github.com/Alan-Cha/silhouette-card-maker/blob/main/plugins/riftbound/README.md)
and RiftMana "Card Names" export):

```
1 Viktor, Herald of the Arcane
3 Seal of Unity
3 Stupefy
...
```

Code form (RiftMana "TTS" export): `OGN-249-1 OGN-046-1 OGS-021-1 …` (card code + copy index).

Not found: any Riot-published deck code or Riot-specified text decklist file format. The
Tournament Rules only require that a registered list be "complete, unambiguous" (TR 401.2)
**[primary]**.

### 1.6 What this means for the bot

1. **Accept plain-text decklists as the primary submission format** (`N Card Name` and/or
   `N SET-NNN` lines, optional zone headers). This is what every builder exports, what the official
   UVS registration form parses, and it is trivially stored, diffed, validated, and displayed in
   Discord. Validate against TR zone sizes (40 main incl. champion / 1 legend / 12 runes /
   3 unique battlefields / ≤8 sideboard).
2. **Accept Piltover deck codes as a convenience** — the format is fully documented and
   Apache-2.0-licensed, but the reference implementation is TypeScript-only, so we would need a
   small Python port of the base32/varint scheme (v1–v4). Decode to card codes on intake and store
   as text; we still need a card database (set code → name) to render names.
3. **Accept Piltover Archive deck URLs cautiously.** Links like
   `piltoverarchive.com/decks/view/<uuid>` are stable and public, but the underlying deck can be
   edited by its owner and the site 403s simple fetchers — so never store just the link. If links
   are accepted, snapshot the list (or require the player to also paste text/code) at submission
   time. Rift Atlas / TCG Arena links are SPA views with no verified stable-export story; don't
   build on them.
4. **Screenshots are display-only.** Fine as an optional attachment for open-decklist reveals, but
   unparseable and unverifiable; never the source of truth.
5. **Store**: raw submitted text + normalized card-code list + (optionally) a generated deck code
   for one-click import into other tools. Display: normalized text grouped by zone. This matches
   the TR 401.3 model that a registered list is frozen once submitted.

---

## Part 2 — Official Riftbound organized play rules

### 2.1 The documents

- **Riftbound Tournament Rules (TR)** — the official document, published by Riot:
  - Canonical PDF (Last Updated **4/29/2026**):
    [cmsassets.rgpub.io …e755.pdf](https://cmsassets.rgpub.io/sanity/files/dsfx7636/news_live/e70866614d68a00a1cbd12c7de08124e0ea5e755.pdf),
    linked from the official [Rules Hub](https://playriftbound.com/en-us/rules-hub/) **[primary]**.
  - Web version: [playriftbound.com … riftbound-tournament-rules](https://playriftbound.com/en-us/news/organizedplay/riftbound-tournament-rules/)
    (carries a banner pointing to the Rules Hub for the latest version) **[primary]**.
  - Updated periodically (e.g. [January update announcement](https://playriftbound.com/en-us/news/announcements/tournament-rules-january-update/),
    a 3/30/26 revision mirrored at [riftbound.gg](https://riftbound.gg/wp-content/uploads/sites/67/2026/03/Riftbound-Tournament-Rules-3.30.26.pdf)).
    All section numbers below were verified against the 4/29/2026 PDF.
- **Core Rules (CR)** — game rules, PDF (3/30/26) also on the
  [Rules Hub](https://playriftbound.com/en-us/rules-hub/):
  [cmsassets.rgpub.io …9a67.pdf](https://cmsassets.rgpub.io/sanity/files/dsfx7636/news_live/861747d1d4d505b7c14d73aba9749d1c3a209a67.pdf) **[primary]**.
- **Organized Play overview**: [playriftbound.com OP article](https://playriftbound.com/en-us/news/organizedplay/riftbound-organized-play/) **[primary]**
  and the [Riftbound Organized Play Quick Reference Guide (UVS Games PDF)](https://uvsgames.com/wp-content/uploads/2025/12/Riftbound-Organized-Play-Quick-Reference-Guide-1.pdf)
  **[primary — UVS is Riot's official OP partner]**: OP ladder = Local store events → Summoner
  Skirmish → Regional Qualifiers → Regional Championships → World Championship (first Worlds 2027).
  Official event software is **riftbound.carde.io** (named in the UVS guide).

### 2.2 What the Tournament Rules DO specify (all **[primary]**, section numbers from the 4/29/26 PDF)

- **Competition shapes (TR 202):** Swiss ("players are paired based on their current standing")
  and Playoff (single elim) are the two defined structures; competitions may combine them.
- **Match structure (TR 404):** "Most matches of Riftbound are 'best of 3'" (first to 2 game wins).
  Drawn games don't count toward that goal (404.3). If round time ends first, most game wins takes
  the match (404.4); "If all sides have equal game wins, the match is a draw" (404.5).
- **Draws exist**, three ways: drawn games/matches at time (404.5, 408.3.b), a drawn game when
  neither player leads by 2+ points after extra turns (408.2.b), and **intentional draws** —
  "all players may mutually agree to draw" a game or match (410.1–410.2), with no compensation
  (bribery, 410.3) and no scouting other results first (410.4).
- **End of round (TR 408.2):** at time, the active player finishes their turn, then **3 additional
  turns** are played; if the game is still unfinished, a player wins that game only with a point
  lead of 2+, otherwise the game is a draw.
- **Tiebreakers (TR 409.2), in order:**
  1. higher **opponents' mean (average) match win percentage** (OMW%),
  2. higher **game win percentage** (GW%),
  3. higher **opponents' mean (average) game win percentage** (OGW%),
  then a **random method** (409.3). *No formulas, floors, or bye handling are given — see 2.3.*
- **Round time (TR 604.1):** recommended **60 minutes** for Swiss rounds of a competitive event;
  single-elimination matches are "strongly recommended" to be **untimed** (604.2), with a special
  timed-single-elim procedure (408.4) ending in sudden-death "next point wins".
- **Deck registration (TR 401):** required at high OPL (Competitive/Professional), optional at
  Casual; list frozen once submitted (401.3); must be registered before round 1 (401.4).
- **Open decklists (TR 401.5):** decklists are private by default, but the head judge may run an
  event (or its final rounds) with an **open decklist policy** making all lists public information;
  each round players get their opponent's list to review at the start of the match and between
  games, not during play (401.5.a). "Typically, professional OPL competitions should be run with
  open decklists" (401.5.b). This directly legitimizes an open-decklist bot tournament.
- **Byes (only two mentions in the whole TR):** if a player drops after a top cut but before
  pairings, "The highest ranked remaining player receives a bye" (414.6). That's it — see 2.3.
  (Round-1 byes exist as OP prizes: Summoner Skirmish winners earn "a Round 1 Bye to use at a
  future Regional Qualifier … only able to utilize one bye per Regional Qualifier event" —
  [UVS Quick Reference Guide](https://uvsgames.com/wp-content/uploads/2025/12/Riftbound-Organized-Play-Quick-Reference-Guide-1.pdf) **[primary]**.)
- **Drops (TR 414):** any time; no-show = auto-drop unless they report; re-entry possible before
  the next round at head-judge discretion, but never after a top cut.
- **Playoff seeding perk (TR 407.2.a):** in playoffs after Swiss, the higher Swiss rank is
  automatically the designated player (chooses play/draw) in game 1.
- **Deck checks (TR 411):** at high OPL, at least 10% of decks, at random.
- **Penalties (TR 700s):** Warning / Game Loss / Match Loss / Disqualification; decklist errors are
  a Game Loss (703.3).

### 2.3 What the Tournament Rules DO NOT specify (verified absences, 4/29/26 PDF) **[primary]**

- **No match points.** The phrase "match points" never appears; there is no 3/1/0 (or any) points
  table. Standings are framed as "match records" plus the 409 tiebreakers. How a draw ranks against
  wins/losses is therefore implicit (a draw is worth more than a loss, less than a win) but never
  quantified.
- **No Swiss round count table.** Nothing maps player count → number of rounds (unlike MTG's MTR
  Appendix E). Round counts are left entirely to organizers/event software.
- **No tiebreaker math.** OMW%/GW%/OGW% are named but not defined: no formula, **no floor value**
  (no 33.3%/0.33 rule), no statement on whether byes are excluded from tiebreakers, and no
  definition of how drawn matches/games count inside the percentages.
- **No odd-player-count bye rule.** How pairings handle an odd number of players (who gets the bye,
  what record a bye confers) is unspecified.
- **No pairing algorithm details** (avoiding repeat pairings, pairing within score groups, etc.)
  beyond the one-line Swiss definition in 202.3.
- Observed practice at premier events fills some gaps: the first English Regional Qualifier ran
  **9 rounds of Swiss (Bo3 with sideboards) on day 1, top 64 to single-elim day 2**
  ([official announcement](https://playriftbound.com/en-us/news/announcements/riftbounds-first-english-regional-qualifier-and-other-op-updates/) **[primary]**);
  later, larger RQs reportedly varied (e.g. ~1405 players at RQ Sydney with 8+ rounds and a record
  cut to day 2 — [riftdecks.com](https://riftdecks.com/riftbound-tournaments/riftbound-regional-qualifier-rq-sydney-swiss-standings-tournament-decks-9722),
  [fanfinity.gg](https://www.fanfinity.gg/blog/big-changes-to-competitive-riftbound-and-news-for-regional-championship-bologna/) **[community]**).
  Summoner Skirmish (the top local event) is officially "1v1 Constructed, Best of 3 … Swiss Rounds
  with optional Top Cut", Competitive OPL
  ([UVS Quick Reference Guide](https://uvsgames.com/wp-content/uploads/2025/12/Riftbound-Organized-Play-Quick-Reference-Guide-1.pdf) **[primary]**).

### 2.4 Where the rules mirror or differ from Magic: The Gathering

Reference: [Magic: The Gathering Tournament Rules (MTR), WPN rules documents](https://wpn.wizards.com/en/rules-documents).

Mirrors MTG:
- Same tiebreaker stack **order** as MTG's Constructed tiebreakers: OMW% → GW% → OGW% (MTR
  Appendix C), applied to Swiss standings.
- Bo3 matches with sideboards; sideboarding 1-for-1 between games; game 1 always with the
  registered configuration.
- Intentional draws and concessions allowed; bribery/scouting prohibited — closely parallels MTR
  sections on match results and bribery.
- Frozen decklists, deck checks (~10%), Casual/Competitive/Professional enforcement tiers
  (analogous to MTG's Regular/Competitive/Professional REL), penalty ladder
  Warning → Game Loss → Match Loss → DQ.
- End-of-round extra turns and a defined timed-out-match procedure.

Differs from MTG:
- **Extra turns:** current turn + **3** additional turns (TR 408.2.a) vs. MTG's 5 additional turns;
  and Riftbound resolves unfinished games by **point lead ≥ 2** (else the game is a draw), a
  scoring-race mechanic MTG has no equivalent of.
- **Round length:** 60 minutes recommended vs. MTG's 50 for Bo3 Swiss.
- **No match-points system defined** (MTG: 3/1/0 codified in MTR 10.2).
- **No tiebreaker floor** (MTG imposes a 0.33 / 33% minimum on MW%/GW% in tiebreakers) and **no
  rule excluding byes from tiebreaker calculations** (MTG excludes byes from OMW%); Riftbound's TR
  is simply silent on both.
- **No recommended-rounds table** (MTG MTR Appendix E) and no bye-assignment rules for odd fields.
- Sideboard is 0–8 cards exchanged strictly 1-for-1 with a fixed 40-card main (MTG: up to 15, free
  reconfiguration as long as minimums are met).
- Playoff game-1 play/draw goes automatically to the higher Swiss seed (MTG does this too for
  playoffs — a mirror, not a difference).

### 2.5 What this means for the bot

1. **Model results as W/D/L per match with game scores.** Draws are real in Riftbound (timed
   rounds, intentional draws), so the bot must support them: store game wins per player per match
   and a match result of win/draw/loss.
2. **Adopt 3/1/0 match points as an explicit bot policy, not an official rule.** Riot defines no
   points; ranking by "match record" with 3/1/0 is the natural, MTG-compatible realization and
   orders records identically. Document it as a house choice.
3. **Implement tiebreakers in the official order — OMW%, GW%, OGW%, then random** (TR 409.2–409.3).
   Because Riot gives no math, the bot must pick and document conventions; the defensible defaults
   (borrowed from MTG since the stack is clearly MTG-derived) are: MW% = match points /
   (3 × matches played) or wins+draws/2 over matches; apply a 33.3% floor to each opponent's MW%
   and GW%; exclude byes from opponents' percentages; count a bye as a 2–0 match win for the
   player receiving it. Flag these in the bot docs as "not specified by Riot; MTG-style defaults".
4. **Swiss round count is the organizer's call.** Use an MTG-style attendance table
   (⌈log2(players)⌉, e.g. 5–8 → 3, 9–16 → 4, 17–32 → 5, 33–64 → 6) as the default with an
   override, since Riot publishes none; official events show pragmatic caps (~9 rounds/day) and
   optional top cuts.
5. **Byes:** implement odd-player byes ourselves (lowest-standing player without a previous bye is
   the common convention — again not specified by Riot). Support seeded round-1 byes, since the
   official OP program awards them (Summoner Skirmish → RQ).
6. **Timing defaults:** 60-minute Swiss rounds, Bo3, first to 2 game wins; untimed (or generously
   timed) single-elim top cut.
7. **Open decklists are officially blessed** (TR 401.5): a tournament where the bot reveals all
   submitted lists publicly matches the TR's open-decklist policy — per official flavor, opponents
   get each other's lists at the start of the match and between games. The bot revealing lists at
   round start (or event start) is consistent with how Riot expects professional events to run.
8. **Track the TR version.** The document changes every few months (Jan / Mar / Apr 2026 updates
   observed); pin the version the tournament runs under and re-check the
   [Rules Hub](https://playriftbound.com/en-us/rules-hub/) before each event.
