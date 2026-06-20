# Bad Actors

Bad Actors is an AI social-deduction simulation scaffold. Frontier-model agents will play a hidden-role game while a deterministic engine referees state transitions.

## Status

This repository currently contains the empty Python 3.12 package scaffold, project constitution, CI workflow, and smoke test. Application behavior has not been implemented yet.

## Setup

Install dependencies with `uv sync`.

Run the validation suite:

```sh
ruff check
ruff format --check
mypy src
pytest -q
```

## License

Licensed under Apache-2.0.
