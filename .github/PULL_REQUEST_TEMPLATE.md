## What this does

<!-- A sentence or two. Link the issue if there is one. -->

## How you checked it

<!-- The commands you ran. For game changes, a seed that shows it. -->

## The six rules

Tick each one, or say why it doesn't apply.

- [ ] **Players only see what they could see.** Nothing here gets another player's role, private thoughts or unseen events into a prompt, and the planted-leak tests still fail on a real leak.
- [ ] **Same seed, same game.** No model calls, file reads, clock or global randomness in the world or the rules. Dice come from the seeded RNG.
- [ ] **Everything goes on the tape.** New actions, observations and statements are written to the ledger with who, where and when.
- [ ] **No covering for a model.** A failed answer shows up as a failure. Nothing fills in a move for it.
- [ ] **Keys stay in the environment.** No key printed, logged or committed. Any new paid call sits behind the three limits. No test spends money.
- [ ] **The TV show stays out of it.** The format's name isn't in any copy, metadata or alt text.

## Also

- [ ] New behaviour has tests.
- [ ] `uv run pytest -q && uv run mypy src && uv run ruff check && uv run ruff format --check` pass.
- [ ] If the site or the clips changed, `npm run test && npm run build` in `frontend/` or `npx tsc --noEmit` in `clips/render/` pass.
- [ ] Anything a bot played is labelled as bots.
