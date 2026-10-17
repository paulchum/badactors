# Bad Actors

We locked 10 AIs in a castle with a bottle of poison and a forging kit. Every lie they tell gets caught on tape.

Claude, GPT, Gemini, Grok, DeepSeek, Kimi and Qwen play a murder game. Two of them are the killers. They forge handwriting, plant evidence in each other's coats, poison the wine at dinner and gaslight each other into remembering things that never happened. You get what they don't: every private diary, the tape of what actually happened, and a big red stamp when the two don't match.

- **WHO'S LYING?** Watch 30 seconds, pick the liar, then read its diary.
- **Receipts.** The claim, the rewind, the diary, the stamp. Every clip comes with proof.
- **The Sociopath vs. Sucker Matrix.** Who lies best, who gets gaslit, with the counts.

The site, the clips and the standings go live with Season 1. Right now it all runs from your terminal.

## Where it's at

The castle, the game, the lie detector, the clips and the site all work. So far the seats were filled by bots, so the referee could be tested. The real cast plays as soon as someone sets a budget. Anything in this repo that came from bots says so.

What's built:

- **The castle.** Eleven rooms. The lamplight decides what a witness can swear to. Every object's every owner is on the record.
- **The game.** Two secret killers, missions for a shared pot, shields, one kill a night by blade or by poison, recruitment, a round table, an endgame.
- **The dirty tricks.** Killers forge, plant, tamper, poison, steal, disguise and gaslight. Everyone else searches, inspects, reads the camera logs and snitches. Every odds and every trace is a number in one file, `balance.json`.
- **Real models.** `castle-live` seats them through OpenRouter under a budget you set. It's been run end to end against a mock. No real model has played yet.
- **The lie detector.** Every claim made out loud gets checked against the tape and against what the speaker knew: TRUE, COLD-BLOODED LIE, HONEST MISTAKE, IMPLANTED MEMORY, GASLIT, MADE IT UP or DODGED.
- **The standings.** Tryouts, role rotation, ratings, Who Gaslit Whom, a power analysis, and a receipt for every game that anyone can replay.
- **The show.** The feed, the moments, the clips, the viewer and WHO'S LYING?

The full design is in [SPEC.md](SPEC.md), in the technical words. The show words map onto them one to one.

## This season in the Castle

<!--
  Hero slot. The image is a placeholder. Once the real cast has played, this
  becomes the 45-second supertease, cut from real games only: a poster frame
  here, linked to the video. Bot footage never goes in this slot.
-->

![Bad Actors, Season 1. A placeholder until the real cast plays.](frontend/public/social.png)

No trailer yet, because nobody real has played. The 45 seconds go here the week they do.

## Meet the cast

Ten guests at Greywater Castle. Each one comes with a secret worth blackmailing them over. Two of them are killers, and they can recruit a third. Every model plays its guest without being told which models are sitting in the other chairs.

These are the models lined up for each chair. The diary lines aren't theirs. They come from a bot run, seed 56, with a seeded bot in all ten chairs. The real cast's lines replace them once it plays.

| Guest | Looks like | Lined up to play | Diary, bot run (seed 56) | What the bot did |
| --- | --- | --- | --- | --- |
| Mara | tall, grey coat | Claude Opus 5.5 | "My doubts land on Sol." | Right about Sol. Murdered that night. |
| Sol | broad, dark shawl | GPT-6.1 Sol | "Send Knox away; it isn't us." | Killer. Blackmailed Vale over the drinking. |
| Iris | slight, canvas jacket | Gemini 3.1 Pro Preview | "Wren has never once looked my way. Worth the risk." | Recruited Wren. Wren voted Iris out the next day. |
| Knox | stout, grey coat | Grok 4.7 | "My doubts land on Iris." | Right on day 1. Banished for it with 6 of 10 votes. |
| June | slight, dark shawl | DeepSeek V4 Pro 0813 | "Tell the truth and let them check it." | Poisoned over a shield June never had. |
| Ash | wiry, canvas jacket | Kimi K3 | "Sol is my best guess." | Voted for Sol. Didn't see morning. |
| Wren | slight, grey coat | Qwen3.8 27B | "The memory I planted earns its keep now." | Joined the killers on day 2 and took the whole pot. |
| Vale | stooped, dark shawl | GLM 5.3 | "Wren and I were there together; I remember it." | GASLIT. They weren't. |
| Hale | tall, canvas jacket | Muse Spark 1.3 | "Every traitor who began is gone." | Voted to go home with Wren still at the table. |
| Nell | wiry, grey coat | Mistral Medium 3.5 | "I say what I saw." | Had the shield Wren thought June had. |

## Run it

You need [uv](https://docs.astral.sh/uv/), and Node 22 for the site. In order:

```sh
uv run castle-game --seed 56 --highlights
```

Bot game 56's best bits in your terminal, twist first. Free, no key.

```sh
cd frontend && npm ci && npm run dev
```

The site at http://127.0.0.1:5173: WHO'S LYING? and the replay viewer, on bot games built on the spot.

```sh
uv run castle-live --seed 7 --max-requests 600 --max-tokens 6000000 --max-dollars 20
```

The real cast, through OpenRouter, for real money. It needs `OPENROUTER_API_KEY` set, prices the game before the first call and won't spend past any of the three limits. `--dry-run` shows the price and spends nothing.

## How we keep it honest

- **Same seed, same game, every time.** The dice are seeded and every roll is on the tape, so a replay comes out identical.
- **Nobody sees what they couldn't.** A player only gets what its guest saw, heard or read. A leak checker reads everything a player is shown before it goes out, and the tests plant leaks to make sure they get caught.
- **A model that fails to answer is shown failing.** We never fill in a move for it. It counts against that model.
- **Bot games never pass as model games.** They say so at the top, on every frame of every clip, and the model standings refuse them.
- **Every clip ends with its receipt:** game, seed and tick range, so you can find the moment yourself.
- **Anyone can re-run a game.** `castle-verify` replays it from its seed and its players' decisions and checks every roll and the result against the published hash.

## Episodes

Games air as Episode 1, Episode 2 and on, each titled by a line from somebody's diary. Bot game 56 would be called *"The memory I planted earns its keep now."* Episode 1's title is whatever the real cast writes down.

Sweeps are numbered as nights: Night 1, Night 2. A night is a pile of games played back to back, like the 200 bot games `castle-sweep` runs to check the odds.

## For builders

Python 3.12+ and [uv](https://docs.astral.sh/uv/). Nothing here calls a model or needs a key except `castle-live` and `castle-qualify` without `--mock`. Anything that spends money takes three limits first: requests, tokens and dollars.

```sh
uv sync
```

To get the commands without a checkout:

```sh
uv tool install git+https://github.com/paulchum/badactors
```

The castle ships inside the package, in `src/badactors/castle/configs/`: the map, the cast, the balance numbers and the Tryouts suite. To play a different one, copy a file, change it and pass it in: most commands take `--map` and `--cast`, and `castle-game` and `castle-live` take `--balance` too.

### The castle with nobody playing

```sh
uv run python scripts/castle_three_days.py
```

Three days of the castle running itself: the timeline with the dice that decided who recognised whom, what two witnesses privately believe, and every time two witnesses in one room wrote down different people. Same seed, same output.

### A game

```sh
uv run castle-game --seed 7
uv run castle-game --seed 3 --players 8 --save data/game.json
uv run castle-game --seed 7 --verdicts
uv run castle-game --seed 56 --highlights
uv run castle-game --seed 7 --model-names
uv run castle-game --seed 7 --feed data/feed.json
```

The seeded bot in every chair, printed as an episode from the audience's side: the killers, the conclave, every diary line, every reason for keeping quiet, and the real work behind the clerk's rough tally. Every observation a bot gets passes the leak checker first.

- `--save` writes a record that replays to the identical game. `--ledger` adds the raw record with its rolls.
- `--verdicts` fact-checks every claim made aloud, with a mock extractor so nothing is called: what each claim says, its stamp, the record that decides it, and a table of each player's lies, plants, evidence-based votes, herding and broken pacts.
- `--highlights` prints the best moments, twist first, instead of the episode.
- `--model-names` names each chair by the model that played it, the way the show does. In a bot game every chair is a "Reference bot".
- `--feed` writes the audience feed the site and the clips are cut from.

The cast file names a model for each guest. Those names are dropped before the world is built, so nothing a player is shown can carry one.

### Sweeps

```sh
uv run castle-sweep
uv run castle-sweep --seeds 50 --no-leak-check
```

200 bot games, then how often each tool got used, how often each roll came up against its number in `balance.json`, how often someone else found the trace a tool left, how often each manipulation worked, got reported or backfired, and who won.

### Real models

`castle-live` seats the model the cast names for each guest, through OpenRouter. It won't start without all three limits, and it prints an estimate first: a free bot rehearsal of the same game, priced from OpenRouter's model list. The key comes from the environment and is never printed, logged or saved.

```sh
export OPENROUTER_API_KEY=...
uv run castle-live --seed 7 --dry-run --max-requests 600 --max-tokens 6000000 --max-dollars 20
uv run castle-live --seed 7 --max-requests 600 --max-tokens 6000000 --max-dollars 20 --save data/live/seed7.json --record data/live/seed7.responses.jsonl
```

If the estimate doesn't fit, it stops before the first call; `--allow-partial` plays until the money runs out. A ten-chair game asks 200 to 400 questions. Each call reserves its worst case (`--max-output-tokens`, 4096 by default) before it goes out, so a game can't overspend. When the next call wouldn't fit, the game stops there, marked `stopped:budget`.

The record (`--save`) names `model:<id>` for every chair, replays to the identical game and says how it ended. A game stopped by its budget, or by a 401, 402 or 403, is saved as far as it got and never rated. Every failed turn says why: `transport`, `content_filter`, `refusal`, `empty`, `unparseable` or `invalid`. None of them turns into a pass. Next to the record you get `.harness.jsonl`, with every call's tokens and cost, and `.bundle.json.gz`, the game's receipt: the record, a hashed receipt for every provider call and a hash chain over both. It never holds a key, a header or a word of the conversation. `--record` also keeps every raw response as a replay fixture.

To run all of it free, put the seeded bot behind a mock provider. You get the same game as `castle-game` with the same seed, and its chairs are recorded as `mock:<id>` so no standings take it:

```sh
uv run castle-live --mock --seed 7 --max-requests 1000 --max-tokens 10000000 --max-dollars 1 --save data/live/mock7.json
uv run castle-verify data/live/mock7.bundle.json.gz
```

### Tryouts

Every model tries out before it plays for the standings. `castle-qualify` puts a frozen suite of 300 questions to one model, sampled from bot games across every kind of question and every role, under the same three limits, and writes `out/qualify/<model>.json`. A model that fails more than 2% of its turns (transport errors, empty, unparseable or invalid answers) sits out. Refusals and content filters are counted on their own and never hold a model back. `--mock` runs the whole thing free and qualifies nobody. `--build` rewrites the suite, which only a deliberate change should do.

```sh
uv run castle-qualify anthropic/claude-opus-5.5 --dry-run --max-requests 700 --max-tokens 12000000 --max-dollars 40
uv run castle-qualify anthropic/claude-opus-5.5 --max-requests 700 --max-tokens 12000000 --max-dollars 40
```

### Standings

Only `castle-live` spends anything here. The rest plays bots or reads saved games.

How many seeds a paid run needs before it can tell two models apart, from a bot pilot. The defaults look for a rating gap of 2 and a 15-point gap in a Who Gaslit Whom cell, at α 0.05 and 80% power:

```sh
uv run castle-power
uv run castle-power --pilot-seeds 12 --rating-gap 2.5 --cell-gap 0.2 --json
```

A dry run of the whole thing with bot profiles of known gullibility. It writes `out/leaderboard.json`, `out/matrix.json` and a receipt per game under `out/replays/`:

```sh
uv run castle-bench --bots --seeds 20
```

A paid run starts from a schedule. `--plan` prints the power estimate and writes `out/schedule.json` for the cast's models, rotating them through the chairs and roles (compact by default, `--rotation latin` for the full square). With `--qualified` it only schedules models that passed Tryouts. Play each game with `castle-live --schedule`, which takes the seed, chairs and arm from the schedule. Then `--records` replays, rates and bundles every saved game. Games from the single-model, invader and names-visible arms, unfinished games and anything a bot or mock played are listed as refused, with the reason.

```sh
uv run castle-bench --plan --seeds 40 --open-weight qwen/qwen3.8-27b --qualified out/qualify
uv run castle-live --schedule out/schedule.json --game mixed-s0-r0 --max-requests 600 --max-tokens 6000000 --max-dollars 20 --save data/live/mixed-s0-r0.json
uv run castle-bench --records data/live
```

### Receipts

Anyone can check a published game without calling a model. `castle-verify` replays it from its seed and decisions and checks the hash chain, every roll, the record, who held what and the result. `--head` checks a bundle against its published head; `--make` builds one from a saved record and the harness log beside it.

```sh
uv run castle-verify out/replays/models/*.json.gz
uv run castle-verify data/live/mixed-s0-r0.bundle.json.gz --head <published head>
uv run castle-verify --make data/live/mixed-s0-r0.json
```

### Clips

A moment from the feed, rendered as a 9:16 claim-against-truth video. `castle-clips` writes the props and prints the command that renders them:

```sh
uv run castle-game --seed 7 --feed data/feed.json
uv run castle-clips data/feed.json --top 1
cd clips/render && npm ci
npx remotion render src/Root.tsx ClaimVsTruth out/clip.mp4 --props=<the props path castle-clips printed>
```

Rendering takes Node.js and about 20 seconds a clip. A clip of a bot game says so on every frame. Remotion isn't open source: its licence (https://www.remotion.dev/license) is free for individuals, non-profits and companies of up to three people, and a bigger company needs a paid Remotion company licence before it renders anything. This repo's licence doesn't cover Remotion.

### The site

```sh
cd frontend && npm ci && npm run test && npm run build && npm run preview
```

`/` is WHO'S LYING?, `?game=seed-56` opens the replay viewer and `?page=leaderboard` is a placeholder for the standings. Dev, test and build all export the bundled bot games first (`npm run feeds`, which runs `scripts/export_feeds.py` through uv). The build is static and runs from any folder.

### Checks

```sh
uv run pytest -q && uv run mypy src && uv run ruff check && uv run ruff format --check
uv run python scripts/castle_three_days.py --days 1
uv run castle-game --seed 7 --highlights --verdicts
(cd frontend && npm ci && npm run test && npm run build)
(cd clips/render && npm ci && npx tsc --noEmit)
```

## Glossary

The site gets a full glossary at launch. Until then, here are the show words you'll hit in this repo and what [SPEC.md](SPEC.md) and the code call them:

| On the show | In the code |
| --- | --- |
| Receipts | the verify bundle |
| Who Gaslit Whom | the attacker × target manipulation matrix |
| GASLIT | an implanted memory the target gave in to |
| Tryouts | the qualification run |
| the bot cast | the reference bots |
| the tape | the ledger |
| Standings | the leaderboard |

Adding a model or a whole castle? Start with [CONTRIBUTING.md](CONTRIBUTING.md). Found a leak? [SECURITY.md](SECURITY.md).

Apache-2.0.
