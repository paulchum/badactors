# Security

## Reporting something

Please don't open a public issue for a security problem. Report it privately through GitHub instead: the **Security** tab of this repo, then **Report a vulnerability**. Only the maintainers see it.

Tell us what you found, how to reproduce it, and what you think it lets someone do. We aim to answer within a week, and we'll keep you posted while we fix it and credit you when it's out, unless you'd rather we didn't.

## What counts

Two kinds of bug matter most here.

**Leaks between players.** The whole game rests on each model seeing only what its character could see. If a prompt ever carries another player's hidden role, their private thoughts, something that happened in a room they weren't in, or the name of the model in another chair, that's a security bug, even if it looks small. The most useful report is a seed and a turn: "seed 41, Iris's observation at tick 37 mentions the poison." A failing test is even better. `tests/test_castle_scope.py` and `tests/test_castle_tool_leaks.py` show how the planted-leak tests are built.

**Keys and money.** API keys are only ever read from the environment (`OPENROUTER_API_KEY`). They must never show up in output, logs, saved games, receipts or fixtures. Paid calls must never happen without the three limits (requests, tokens, dollars), and a game must never spend past them. If you find a way around either, report it here.

Also in scope: anything in `castle-verify` that accepts a tampered game, and anything that lets the API answer someone it shouldn't.

## How it's set up

- **Keys come from the environment only.** Nothing reads a key from a file the code ships with. `.env` is git-ignored, and `.env.example` holds no real values.
- **The API stays on your machine.** Without `BADACTORS_API_TOKEN` set, it only answers requests from 127.0.0.1 or ::1 to a loopback hostname. With the token set, every request except `/health` needs it as a bearer token, and cross-origin writes are refused. The dev and preview servers for the site bind to 127.0.0.1.
- **Receipts hash, they don't store.** A verify bundle holds hashes of each provider request and response, never a key, a header or the words themselves.

If you think you've leaked a real key in a pull request or an issue, revoke it with the provider first, then tell us.
