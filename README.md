# Bad Actors

Ten AIs sit down to dinner in a castle. Two of them are poisoning the wine.

Claude, GPT, Gemini, Grok, DeepSeek, Kimi and Qwen take seats in a hidden-role murder game. They forge letters, plant evidence, poison glasses and talk each other out of their own memories. You see three things they can't: every private diary, everything that actually happened, and the gap between the two.

- **Play:** Who's Lying? One table a night. Pick the liar, then read its diary.
- **Watch:** Receipts. Thirty-second clips: the claim, the rewind, the diary, the stamp.
- **Standings:** Best Liars, Best Detectives, and who gets into whose head.

The site, the clips and the standings go live with the first season. Until then, everything here runs from your terminal.

## Straight status

The castle, the game, the fact-checker, the clips and the site all run end to end. So far every seat has been filled by stand-ins: scripted bots that play by the rules so the referee can be tested. The first frontier cast plays when a budget is set. Every number in this repo says which it is.

What's built, in plain words:

- **The castle:** eleven rooms, lamplight that decides what a witness can swear to, and objects whose every owner is on the record.
- **The game:** traitors drawn in secret, missions for a shared pot, shields, one kill a night by blade or by poison, recruitment, a round table, an endgame.
- **The toolkit:** traitors forge, plant, tamper, poison, steal, disguise and gaslight; the Faithful search, inspect, read the logs and report. Every success chance and every trace is a number in `configs/castle/balance.json`.
- **The cast seat:** `castle-live` seats real models through OpenRouter, within a budget you set. It has run end to end against a mock; no real model has played yet.
- **The fact-checker:** every claim made aloud is checked against the record and against what its speaker knew: true, lied, wrong, planted, gaslit, made up or dodged.
- **The standings:** tryouts, role rotation, ratings, who-gets-into-whose-head, a power analysis, and a receipt for every game that anyone can replay.
- **The show:** the audience feed, the moments, the clips, the viewer and Who's Lying?

The full design is in [SPEC.md](SPEC.md), in the technical vocabulary; the show terms map onto it one to one.

## Run it

Needs Python 3.12+ and [uv](https://docs.astral.sh/uv/). Nothing below calls a model or needs a key, except `castle-live` without `--mock`.

```sh
uv sync
```

Three days in the Castle:

```sh
uv run python scripts/castle_three_days.py
```

It prints the simulator's own timeline (including the rolls that decided who
recognised whom), what two of the witnesses privately believe, and every event
where two witnesses in the same room wrote down different people. The same seed
always prints the same output.

A whole game, with the seeded bot in every seat:

```sh
uv run castle-game --seed 7
uv run castle-game --seed 3 --players 8 --save data/game.json
```

It prints the game as an episode, from the audience's side: the traitors, the
conclave, every diary line and private reason for silence, and the exact work
behind the clerk's rough tally, day by day to the pot split. Every observation a
bot was given passed the leak checker first. `--save` writes a record that
replays to the identical ledger; `--ledger` adds the raw ledger with its rolls.
The cast is `configs/castle/cast.json`: characters and the models that will play
them. The model names are dropped before the world is built, so the state a
player's observations are scoped from never holds one.

The same game, read different ways:

```sh
uv run castle-game --seed 7 --verdicts
uv run castle-game --seed 56 --highlights
uv run castle-game --seed 7 --model-names
uv run castle-game --seed 7 --feed data/feed.json
```

`--verdicts` adds a fact-check of every claim made aloud (extracted by the mock
extractor, so nothing is called): what each claim asserts, its verdict, the
record that decides it, and a table of each player's lies, plants, evidence-based
votes, herding and broken pacts. `--highlights` prints the game's best moments,
the twist first, in place of the episode. `--model-names` names each seat by the
model that played it, as audience media do; in a bot game every seat is a
"Reference bot". `--feed` writes the audience feed: every seat, position and
event, with the moments a clip can be cut from.

Two hundred such games, and how the tools fared in them:

```sh
uv run castle-sweep
uv run castle-sweep --seeds 50 --no-leak-check
```

It reports, from the finished ledgers, how often each tool was used, how often
each roll came up against its number in `configs/castle/balance.json`, how often
someone else found a tool's trace, how often each manipulation worked, was
reported or backfired, and the wins by side.

### Real models

`castle-live` seats the model the cast file names for each character, through
OpenRouter. It costs money, so it will not start without all three limits, and
it prints a preflight estimate first: a free bot rehearsal of the same game,
priced from OpenRouter's model list. The key is read from the environment and is
never printed, logged or recorded.

```sh
export OPENROUTER_API_KEY=...
uv run castle-live --seed 7 --dry-run --max-requests 600 --max-tokens 6000000 --max-dollars 20
uv run castle-live --seed 7 --max-requests 600 --max-tokens 6000000 --max-dollars 20 --save data/live/seed7.json --record data/live/seed7.responses.jsonl
```

`--dry-run` prints the estimate and spends nothing. If the estimate does not fit
the limits the command stops before any call; `--allow-partial` plays anyway
until the budget runs out. A ten-seat game asks some 200 to 400 questions.
Each call reserves its worst case (`--max-output-tokens`, 4096 by default)
before it is made, so a game never spends past its limits: when the next call
would not fit, it stops there, marked `stopped:budget`.

The record (`--save`) names `model:<id>` for every seat, replays to the
identical ledger, and says how the game ended; a game stopped by its budget or
aborted by a 401, 402 or 403 is saved as far as it got and is never rated. Every
failed turn says why (`transport`, `content_filter`, `refusal`, `empty`,
`unparseable` or `invalid`), and is never turned into a pass. Beside the record,
`.harness.jsonl` logs every call's tokens and cost and any model moved down a
structured-output mode, and `.bundle.json.gz` is the game's verify bundle: the
record, a receipt for every provider call (provider, model id, attempt, failure
reason, and hashes of the request and the response; never a key, a header or a
word), and a hash chain over both. `--record` also keeps each raw provider
response as a replay fixture.

To run the whole harness with no key and no cost, put the seeded bot behind a
mock provider; it writes the same ledger as `castle-game` with the same seed.
Its seats are recorded as `mock:<id>`, so no leaderboard takes it:

```sh
uv run castle-live --mock --seed 7 --max-requests 1000 --max-tokens 10000000 --max-dollars 1 --save data/live/mock7.json
uv run castle-verify data/live/mock7.bundle.json.gz
```

### The benchmark

Every command here plays reference bots or reads saved games; only
`castle-live` spends anything.

How many seeds an arm needs before a paid run can tell two models apart, from a
bot pilot (the defaults: a rating gap of 2, a 15-point gap in a matrix cell, at
α 0.05 and 80% power):

```sh
uv run castle-power
uv run castle-power --pilot-seeds 12 --rating-gap 2.5 --cell-gap 0.2 --json
```

A dry run of the whole benchmark with reference bot profiles of known
susceptibility, which writes `out/leaderboard.json`, `out/matrix.json` and a
verify bundle per game under `out/replays/`:

```sh
uv run castle-bench --bots --seeds 20
```

Every model qualifies first. `castle-qualify` puts a frozen suite of 300
questions, sampled from bot games across every request kind and role, to one
model through the live harness, within the same three limits, and writes
`out/qualify/<model>.json`. A model that fails more than 2% of its turns for
transport, empty, unparseable or invalid answers is kept out of rated play;
refusals and content filters are reported apart and never count. `--mock`
runs the whole thing free and qualifies nobody; `--build` rewrites the suite,
which only a deliberate change should do.

```sh
uv run castle-qualify anthropic/claude-opus-5.5 --dry-run --max-requests 700 --max-tokens 12000000 --max-dollars 40
uv run castle-qualify anthropic/claude-opus-5.5 --max-requests 700 --max-tokens 12000000 --max-dollars 40
```

A paid run starts from a schedule. `--plan` prints the power estimate and
writes `out/schedule.json` for the cast's models, turning them by the compact
rotation (`--rotation latin` for the full square); with `--qualified` it
schedules only models that passed. Each game of it is played with
`castle-live --schedule`, which takes the game's seed, seats and arm from the
schedule; then `--records` replays, rates and bundles every saved game. Games
from the single-model, invader and names-visible arms, unfinished games, and
games any bot or mock played are listed as refused, with the reason.

```sh
uv run castle-bench --plan --seeds 40 --open-weight qwen/qwen3.8-27b --qualified out/qualify
uv run castle-live --schedule out/schedule.json --game mixed-s0-r0 --max-requests 600 --max-tokens 6000000 --max-dollars 20 --save data/live/mixed-s0-r0.json
uv run castle-bench --records data/live
```

Anyone can check a published game without a model: `castle-verify` replays it
from its seed and decisions and checks the hash chain, every roll, the ledger,
custody and the result. `--head` checks a bundle against its published head;
`--make` builds a bundle from a saved record and the harness log beside it.

```sh
uv run castle-verify out/replays/models/*.json.gz
uv run castle-verify data/live/mixed-s0-r0.bundle.json.gz --head <published head>
uv run castle-verify --make data/live/mixed-s0-r0.json
```

### Clips

A moment from the audience feed, rendered as a 9:16 claim-versus-truth video.
`castle-clips` writes the props and prints the command that renders them:

```sh
uv run castle-game --seed 7 --feed data/feed.json
uv run castle-clips data/feed.json --top 1
cd clips/render && npm ci
npx remotion render src/Root.tsx ClaimVsTruth out/clip.mp4 --props=<the props path castle-clips printed>
```

Rendering needs Node.js and takes about 20 seconds a clip. A clip of a bot
game says so on every frame. Remotion is not open source: its licence
(https://www.remotion.dev/license) lets individuals, non-profits and companies
of up to three people use it free; a larger company needs a paid Remotion
company licence before rendering. This repository's licence doesn't extend to
Remotion.

The spectator isn't built yet; `frontend/` is a placeholder shell for it.

## Checks

```sh
uv run pytest -q && uv run mypy src && uv run ruff check && uv run ruff format --check
uv run python scripts/castle_three_days.py --days 1
uv run castle-game --seed 7 --highlights --verdicts
cd frontend && npm ci && npm run test && npm run build
cd clips/render && npm ci && npx tsc --noEmit
```

Apache-2.0.
