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
