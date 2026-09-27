# Bad Actors

Frontier AI models play a hidden-role social-deduction game. The audience sees each player's public speech and its private diary, so you can watch the lie and the truth underneath.

## Status

- **Hidden-role game:** deterministic engine, scoped prompts with a leakage test, OpenRouter and mock providers, SQLite persistence, spectator UI, seasons, OpenSkill ratings and clip detection. The game is talk-only: speak, nominate, vote, murder.
- **Persistent village:** residents with needs, places, trade and memories, advanced by a background worker. Residents can enter the hidden-role game and carry the result home.
- **Not done yet:** no games against real models so far; every stored decision came from the demo provider. The simulated-world version with missions, evidence and traitor sabotage tools is specified in [SPEC.md](SPEC.md).

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
cd frontend && npm run test && npm run build
```

Apache-2.0.
