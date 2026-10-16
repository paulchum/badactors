# Bad Actors

Frontier AI models play a hidden-role social-deduction game inside a simulated world. The audience sees what each player says, what it privately thinks, and what it actually did. The simulator holds the truth; each player knows only what it perceived.

**Current direction: the Castle upgrade, specified in [SPEC.md](SPEC.md).** Read it before changing game, world, harness or benchmark code.

## Layout

```text
src/badactors/
  castle/         the Castle: world core, game layer, scoping, bots, the model harness
                  (prompt, contract_schema, provider, harness, budget, markers, fouls),
                  and the castle-game, castle-live and castle-sweep CLIs
  bench/          ratings.py: OpenSkill (Weng-Lin) ratings for the benchmark
  clips/render/   Remotion project for claim-versus-truth clips
  server/         FastAPI app: owner-access guard and health check
frontend/         React + Vite placeholder for the castle spectator
scripts/          castle_three_days.py, a model-free walk through the world
configs/castle/   castle map, cast and balance numbers
```

## Commands

```sh
uv sync
uv run pytest -q
uv run mypy src
uv run ruff check && uv run ruff format --check
cd frontend && npm ci && npm run test && npm run build
```

## Invariants

These are the only rules that aren't negotiable.

1. **Scoped context.** A player's prompt is built only from authoritative state through the scoping layer, never from the audience stream. A player never receives another player's unrevealed role, private thoughts, or any event it couldn't perceive. Keep a planted-leak test that fails when a real leak is introduced.
2. **Deterministic core.** World and game rules are pure: no model calls, IO, wall clock or global randomness. Randomness comes from a seeded RNG held in state. The same state plus the same decisions produces the same result.
3. **Ground truth is recorded.** Every action, observation and statement goes into an append-only ledger with actor, room and tick, so any public claim can be checked later.
4. **No fake play.** Never substitute a default or seeded move for a failed model response and present it as the model's choice. A failure is a visible event and counts against that model's reliability.
5. **Keys and money.** API keys come from the environment only; never print, log or commit them. Paid model calls happen only in commands that take an explicit budget (max requests, tokens or dollars).
6. **Brand.** Don't use "The Traitors" in product copy, UI or episode metadata; the name and format belong to their rights holders.

## Working style

- Change whatever the task needs across files, including refactors, schema changes, and test updates when behavior changes on purpose. Delete dead code instead of keeping shims.
- New behavior ships with tests. Use property tests (hypothesis) for world invariants such as custody, perception and determinism.
- Keep docs to this file, README.md (how to run) and SPEC.md (what we're building). Don't add progress logs, reports, changelogs or marketing docs.
- Run the relevant checks before saying something works.
