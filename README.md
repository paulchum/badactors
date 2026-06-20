# Bad Actors

Bad Actors is an AI social-deduction simulation scaffold. Frontier-model agents will play a hidden-role game while a deterministic engine referees state transitions.

## Status

This repository currently contains the Python 3.12 package scaffold, project constitution, CI workflow, and Pydantic v2 schemas for the Bad Actors data contract. Engine, scoping, provider, and application behavior have not been implemented yet.

## Data Contract

The canonical schema definitions live in `src/badactors/schemas.py`. They include the role, phase, and visibility enums, player and event models, the discriminated `Action` union, agent turn output, game state, and scoped context models.

## Setup

Install dependencies with `uv sync`.

Run the validation suite:

```sh
ruff check
ruff format --check
mypy src
pytest -q
```

Run the schema contract checks:

```sh
pytest tests/test_schemas.py -q
mypy src/badactors/schemas.py
```

## License

Licensed under Apache-2.0.
