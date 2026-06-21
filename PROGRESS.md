Prompt 1 complete: scaffold and CI green.
Prompt 2 complete: initialized git, updated README and CHANGELOG, and checks pass: ruff check, ruff format --check, mypy src, pytest -q.
Prompt 3 complete: implemented Pydantic v2 schema data contract and tests pass: pytest tests/test_schemas.py -q, mypy src/badactors/schemas.py.
Prompt 4 complete: implemented provider protocol, OpenRouter provider, deterministic mock provider, README/CHANGELOG updates, and tests pass: pytest tests/test_providers.py -q, mypy src/badactors/providers.
Prompt 5 complete: implemented deterministic engine events, rules, and state transitions; tests pass: pytest tests/test_engine.py -q, mypy src/badactors/engine.
Prompt 6 complete: implemented scoped context construction and leakage checks; tests pass: pytest tests/test_scoping.py -q, mypy src/badactors/scoping.py.
Prompt 7 complete: implemented agent prompt assembly and turn loop; tests pass: pytest tests/test_agent.py -q, mypy src/badactors/agents.
Prompt 8 complete: implemented host narration boundary; tests pass: pytest tests/test_host.py -q, mypy src/badactors/host.
Runner milestone complete: implemented SQLite persistence, full mock game runner, example cast, and CLI; tests pass: pytest tests/test_runner.py -q, run-game --cast configs/cast.example.yaml.
Prompt 9 complete: implemented FastAPI game records, SSE stream, and replay sink; tests pass: pytest tests/test_server.py -q, mypy src/badactors/server.
Prompt 10 complete: implemented spectator frontend with live SSE, public-vs-diary view, conclave/reveal panels, replay mode, and Vitest coverage; tests pass: npm run build, npm run test.
Prompt 11 complete: implemented deterministic clip moment detection, make-clips entry, and Remotion vertical renderer; tests pass: pytest tests/test_clips.py -q, render produced clips/render/out/clip.mp4.
Prompt 12 complete: implemented prompt-only submission intake, moderation, and trusted wrapper; tests pass: pytest tests/test_submissions.py -q, mypy src/badactors/platform/submissions.py.
Prompt 13 complete: implemented cost-bounded auditions and host casting with secret Traitor designation; tests pass: pytest tests/test_casting.py -q, mypy src/badactors/platform/audition.py src/badactors/host/host.py.
Prompt 14 complete: implemented cross-game memory persistence, scoped injection, wipe/throttle behavior, and budget ladder; tests pass: pytest tests/test_ladder.py -q, mypy src/badactors/platform/ladder.py.
