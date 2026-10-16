# Bad Actors

Frontier AI models play a hidden-role social-deduction game inside a simulated castle. The audience sees what each player says, what it privately thinks, and what it actually did. The simulator holds the truth; each player knows only what it perceived.

## Status

The Castle is in progress and specified in [SPEC.md](SPEC.md).

- **World:** an eleven-room castle with per-passage travel times, mixed lighting, objects whose custody is derived from an append-only ledger, and perception that degrades a witness's record as it is written.
- **Game:** traitors drawn from a seed, missions worked at stations for a noisily tallied pot, secret shields, one kill a night (a conclave murder or dinner poison), recruitment, a structured round table and endgame. Every observation is scoped and leak-checked, and a game replays from its stored record.
- **Tools:** traitors forge, plant, tamper, poison, steal, disguise and manipulate; the Faithful search, inspect, read the logs and report. Both toolkits run on one machinery, and their success rates, traces and limits come from `configs/castle/balance.json`.
- **Not done yet:** seeded bots play every seat. The model harness, the spectator and the benchmark are next, so no model has played the Castle yet.

## Run it

Needs Python 3.12+ and [uv](https://docs.astral.sh/uv/). Nothing below calls a model or needs a key.

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

Two hundred such games, and how the tools fared in them:

```sh
uv run castle-sweep
uv run castle-sweep --seeds 50 --no-leak-check
```

It reports, from the finished ledgers, how often each tool was used, how often
each roll came up against its number in `configs/castle/balance.json`, how often
someone else found a tool's trace, how often each manipulation worked, was
reported or backfired, and the wins by side.

The spectator isn't built yet; `frontend/` is a placeholder shell for it.

## Checks

```sh
uv run pytest -q && uv run mypy src && uv run ruff check && uv run ruff format --check
uv run python scripts/castle_three_days.py --days 1
uv run castle-game --seed 7
cd frontend && npm ci && npm run test && npm run build
```

Apache-2.0.
