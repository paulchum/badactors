# Contributing

Glad you're here. Bug fixes, new tools, new rooms, new castles and new models are all welcome. So are bot games that break the referee.

Before you change game, world, harness or benchmark code, read [SPEC.md](SPEC.md). It's the plan, in the technical words. [AGENTS.md](AGENTS.md) has the layout and the house rules, and it's what coding agents read too.

## Setting up

You need Python 3.12+, [uv](https://docs.astral.sh/uv/), and Node 22.12+ if you touch the site or the clips. Or open the repo in the dev container, which has all three.

```sh
uv sync --locked
(cd frontend && npm ci)
(cd clips/render && npm ci)
```

Nothing needs a key except real model runs. When you do want one, copy `.env.example` to `.env`, fill it in, and pass it along yourself: `uv run --env-file .env castle-live ...`. The code reads keys from the environment and nowhere else. `.env` is git-ignored. Keep it that way.

## Checks

Run these before you open a pull request. CI runs the same ones.

```sh
uv run pytest -q && uv run mypy src && uv run ruff check && uv run ruff format --check
uv run pip-audit --skip-editable
(cd frontend && npm audit && npm run test && npm run build)
(cd clips/render && npm audit && npx tsc --noEmit)
```

New behaviour comes with tests. For world rules (who holds what, who saw what, same seed same game) write a property test with hypothesis. If you change something on purpose and an old test breaks, update the test in the same change.

CI also scans the checked-out Git history with [Gitleaks](https://github.com/gitleaks/gitleaks). To run that check locally after installing it:

```sh
gitleaks git . --log-opts=--all --redact --no-banner
```

The one entry in `.gitleaksignore` identifies a historical mocked-provider test sentinel. Don't add broad exclusions for keys or test files. If a real key is found, revoke it before dealing with the Git history.

## The six rules

Everything else is up for discussion. These aren't.

1. **Players only see what they could see.** A player's prompt is built from the true state of the world through the scoping layer, never from what the audience gets. Nobody learns another player's role before it's revealed, reads another player's private thoughts, or hears about something they couldn't have seen. There's a planted-leak test that has to keep failing on a real leak.
2. **Same seed, same game.** The world and the game rules don't call models, read files, look at the clock or use global randomness. All the dice come from a seeded RNG kept in the game state.
3. **Everything goes on the tape.** Every action, observation and statement is appended to the ledger with who, where and when, so any claim can be checked later.
4. **No covering for a model.** If a model fails to answer, that's shown as a failure and counts against it. Never fill in a default move and pass it off as the model's.
5. **Keys stay in the environment, and money needs a budget.** Never print, log or commit a key. A paid model call only happens in a command that takes explicit limits on requests, tokens or dollars. Tests never spend money.
6. **Leave the TV show out of it.** The format this game riffs on has a name and owners. Don't use that name anywhere: copy, UI, episode titles, metadata, alt text, file names. A test checks the audience copy for it.

## Adding a tool

A tool is something a player can do, like forge, plant or inspect a seal.

1. Name it in `ToolName` in `src/badactors/castle/balance.py` and give it its numbers in `src/badactors/castle/configs/balance.json`. Every number lives in that file. The code never hard-codes odds.
2. Describe it with a `ToolSpec` in `kit.py`: who carries it, when it can be used, what it costs, what its rolls decide, what the user is told and what's kept from them.
3. Add its plan step to `contracts.py` and its handler to `tools.py`. A handler writes one ledger entry with the tool's name, its rolls and any declared intent.
4. Think about witnesses. People in the room see the physical act in that room's light, never the intent. A covert act has no witnesses unless it gets caught.
5. Test it in `tests/test_castle_tools.py`, add a planted leak for it in `tests/test_castle_tool_leaks.py`, and run `castle-sweep` to see how often it works in practice.

## Adding a room

Rooms live in `src/badactors/castle/configs/map.json`. A room has an id, a name, a description, a light level (`bright`, `dim` or `dark`), a capacity and whether it has a camera. Connect it with passages, which go both ways. The map linter in `mapfile.py` refuses a castle with unreachable rooms, one-way passages, objects only one person could plausibly own, or no reason to go anywhere private. Try it with:

```sh
uv run python scripts/castle_three_days.py --map path/to/map.json
uv run castle-game --map path/to/map.json --seed 7
```

## Adding a model

1. Put its OpenRouter id in a character's `model` field in `src/badactors/castle/configs/cast.json`. The show names it from the id (`show.model_name`), so check the name reads right.
2. Run Tryouts, a dry run first. A model that fails more than 2% of its turns sits out of the standings.

   ```sh
   uv run castle-qualify lab/model --dry-run --max-requests 700 --max-tokens 12000000 --max-dollars 40
   ```

3. If it needs a different structured-output mode, the harness drops down a mode by itself and logs it. Don't add per-model special cases to the game.

Open a [new model issue](https://github.com/paulchum/badactors/issues/new?template=new-model.yml) first if you'd like us to run it.

## Adding a castle

A castle is a map plus a cast. Write both, keep the shipped ones as your template, and pass them with `--map` and `--cast` (and `--balance` if you want different numbers). Every guest needs a build, a garment, a detail and a secret. No garment can belong to just one guest (the linter wants at least two wearers, three when the cast is big enough), or a witness in dim light who only saw the coat would know exactly who it was. The site draws Greywater's floor plan by hand (`frontend/src/feed/layout.ts`) and falls back to a grid for any other castle. Open a [new castle issue](https://github.com/paulchum/badactors/issues/new?template=new-castle.yml) to talk it over.

## Pull requests

- Keep each one about one thing. Big changes are fine if they hang together.
- Change whatever the work needs, including tests and schemas. Delete dead code; don't leave shims.
- Docs live in README.md, SPEC.md, AGENTS.md and these community files. No progress logs or changelogs.
- Anything a bot played says so. Never present a bot game as a model game.

Be decent to each other. The [Code of Conduct](CODE_OF_CONDUCT.md) applies everywhere this project lives. Found a leak or a key? Don't open an issue. Follow [SECURITY.md](SECURITY.md).

## Publishing on GitHub

Before making a private copy public, review its full Git history and open branches, run the checks above, and merge the reviewed changes into `main`. CI must pass on that commit. GitHub can refuse to start jobs when an account has a billing or Actions spending-limit problem; resolve that in the account's Billing and plans settings before relying on its checks.

Set the repository's description and homepage, then change its visibility when ready to publish. In Settings → Code security, enable private vulnerability reporting, dependency alerts, secret scanning and push protection where available. Confirm that SECURITY.md lists the current confidential reporting email, that the issue chooser links to it, and that the private [report form](https://github.com/paulchum/badactors/security/advisories/new) works after enabling it. Protect `main` against force pushes and deletion, and require the CI checks `checks`, `frontend`, `clips` and `secrets` before merging pull requests.

For the spectator site, choose **GitHub Actions** under Settings → Pages → Build and deployment. Then run the **Pages** workflow on `main`. Public repositories publish pushes to `main` only after CI succeeds, and Pages checks out the exact commit CI tested. Private copies require an explicit workflow dispatch to publish. Open the resulting site at `/badactors/` and check the landing game's reveal, its replay receipt and the standings. The default build includes only labelled bot games and requires no keys. Publishing the repository and publishing the site are separate actions.
