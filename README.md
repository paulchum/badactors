# Bad Actors

Frontier AI models play a hidden-role social-deduction game. The audience sees each player's public speech and its private diary, so you can watch the lie and the truth underneath.

## Status

- **The Castle (in progress):** an eleven-room castle with per-passage travel times, mixed lighting, objects whose custody is derived from an append-only ledger, and perception that degrades a witness's record as it is written. The game is played in it: traitors drawn from a seed, missions worked at stations for a noisily tallied pot, secret shields, one kill a night (a conclave murder or dinner poison), recruitment, a structured round table and endgame, all refereed through scoped, leak-checked observations and replayable from a stored log. Both toolkits run on one machinery with every number in `configs/castle/balance.json`: traitors forge, plant, tamper, poison, steal, disguise and manipulate; the Faithful search, inspect, read the logs and report. Seeded bots play it end to end; the model harness is next. Specified in [SPEC.md](SPEC.md).
- **Hidden-role game:** deterministic engine, scoped prompts with a leakage test, OpenRouter and mock providers, SQLite persistence, spectator UI, seasons, OpenSkill ratings and clip detection. The game is talk-only: speak, nominate, vote, murder. The Castle replaces it.
- **Persistent village:** residents with needs, places, trade and memories, advanced by a background worker. Residents can enter the hidden-role game and carry the result home. The Castle replaces it.
- **Not done yet:** no games against real models so far; every stored decision came from the demo provider.

## Run it

Needs Python 3.12+, [uv](https://docs.astral.sh/uv/) and Node.

```sh
uv sync
uv run uvicorn badactors.server.app:app --host 127.0.0.1 --port 8000
```

```sh
cd frontend
npm ci
npm run dev
```

Three days in the Castle, with no model calls and nothing to configure:

```sh
uv run python scripts/castle_three_days.py
```

It prints the simulator's own timeline (including the rolls that decided who
recognised whom), what two of the witnesses privately believe, and every event
where two witnesses in the same room wrote down different people. The same seed
always prints the same output.

A whole game in the Castle, with the seeded bot in every seat:

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
them, though no model name ever reaches a player.

Two hundred such games, and how the tools fared in them:

```sh
uv run castle-sweep
uv run castle-sweep --seeds 50 --no-leak-check
```

It reports, from the finished ledgers, how often each tool was used, how often
each roll came up against its number in `configs/castle/balance.json`, how often
someone else found a tool's trace, how often each manipulation worked, was
reported or backfired, and the wins by side.

Open the Vite URL, create a demo world and run some ticks. Games from the command line use the mock provider:

```sh
uv run run-game --cast configs/cast.example.yaml --db data/badactors.sqlite3
uv run run-season --cast configs/cast.example.yaml --games 3 --season-id demo
```

## Real models

Set these for the API and any separate worker:

| Variable | Purpose |
|---|---|
| `OPENROUTER_API_KEY` | Provider key; stays server-side |
| `BADACTORS_MODELS` | Comma-separated allowlist of OpenRouter model IDs |
| `BADACTORS_API_TOKEN` | Owner token; required on every route except health when set |
| `BADACTORS_DB` | SQLite path (default `data/badactors.sqlite3`) |
| `BADACTORS_WORKER` | Set to `0` to run the worker as a separate process: `uv run python -m badactors.simulation.worker` |

A small, budgeted live run (makes paid calls):

```sh
uv run python scripts/run_live_world_smoke.py --env-file .env --model <provider/model> --ticks 2 --max-requests 24
```

## World API

| Route | Purpose |
|---|---|
| `POST /worlds` | Create a demo or live world |
| `GET /worlds/template` | Default residents and the model allowlist |
| `GET /worlds/{id}` | Public world state |
| `POST /worlds/{id}/control` | `step`, `pause`, or `run` a bounded number of ticks |
| `PUT /worlds/{id}/settings` | Tick speed, model allowance, token cap |
| `GET /worlds/{id}/events?after=0&limit=100` | Event journal (cursor) |
| `GET /worlds/{id}/residents/{rid}` | Owner-only private state and scoped perception |
| `POST /worlds/{id}/arena` | Run or resume a hidden-role game from the world |
| `GET /worlds/{id}/report` · `/export` | Operational report · state snapshot |

The API binds to localhost. Put an authenticated TLS gateway in front of it before exposing it anywhere.

## Checks

```sh
uv run pytest -q && uv run mypy src && uv run ruff check && uv run ruff format --check
uv run python scripts/verify_persistent_worlds.py
uv run python scripts/verify_world_lifecycle.py
uv run python scripts/castle_three_days.py --days 1
uv run castle-game --seed 7
cd frontend && npm run test && npm run build
```

Apache-2.0.
