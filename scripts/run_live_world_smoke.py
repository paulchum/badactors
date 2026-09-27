"""Run a bounded real-model world check and verify state after reopening SQLite."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shlex
import uuid
from pathlib import Path

from badactors.persistence import SQLiteEventStore
from badactors.providers.openrouter import OpenRouterProvider
from badactors.simulation.engine import default_residents
from badactors.simulation.models import WorldCreate, WorldSettings
from badactors.simulation.report import world_report
from badactors.simulation.runtime import WorldRuntime


def api_key(env_file: Path | None) -> str:
    key = os.getenv("OPENROUTER_API_KEY", "")
    if env_file is not None:
        for line in env_file.read_text().splitlines():
            name, separator, value = line.partition("=")
            if (
                separator
                and name.strip().removeprefix("export ") == "OPENROUTER_API_KEY"
            ):
                parts = shlex.split(value.strip(), comments=True)
                if len(parts) != 1:
                    raise ValueError(
                        "The API key entry must contain one literal value."
                    )
                key = parts[0]
    if not key:
        raise ValueError("Set OPENROUTER_API_KEY or supply --env-file .env.")
    return key


async def run(args: argparse.Namespace, key: str) -> None:
    provider = OpenRouterProvider(api_key=key, timeout=45)
    store = SQLiteEventStore(args.db)
    world_id = args.world_id or f"live-smoke-{uuid.uuid4().hex[:10]}"
    print(
        f"World: {world_id}; model: {args.model}; "
        f"request allowance: {args.max_requests}"
    )
    try:
        runtime = WorldRuntime(store, provider)
        if store.document("world", world_id) is None:
            residents = default_residents()
            for resident in residents:
                resident.model = args.model
            state = runtime.create(
                WorldCreate(
                    id=world_id,
                    name="Live model verification",
                    residents=residents,
                    provider="openrouter",
                    max_model_requests=args.max_requests,
                    max_tokens=args.max_tokens,
                )
            )
        else:
            _, state = runtime.load(world_id)
            if state.provider != "openrouter" or any(
                r.model != args.model for r in state.residents.values()
            ):
                raise ValueError("The saved world uses a different provider or model.")
            state = await runtime.configure(
                world_id,
                WorldSettings(
                    max_model_requests=args.max_requests,
                    max_tokens=args.max_tokens,
                    tick_seconds=state.tick_seconds,
                ),
            )
        while state.tick < args.ticks:
            state = await runtime.tick(world_id, manual=True)
            expected = state.model_dump(mode="json")
            store.close()
            store = SQLiteEventStore(args.db)
            runtime = WorldRuntime(store, provider)
            _, state = runtime.load(world_id)
            assert state.model_dump(mode="json") == expected
            print(f"Committed tick {state.tick}; state matched after database reopen.")
        report = world_report(runtime, world_id)
        report_path = Path(args.db).with_suffix(".report.json")
        report_path.write_text(json.dumps(report, indent=2) + "\n")
        print(f"Live world smoke check passed. Operational report: {report_path}")
    finally:
        store.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="Approved OpenRouter model ID.")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--db", type=Path, default=Path("data/live-smoke.sqlite3"))
    parser.add_argument(
        "--world-id", help="Resume the printed ID after an interruption."
    )
    parser.add_argument("--ticks", type=int, default=2, choices=range(2, 11))
    parser.add_argument("--max-requests", type=int, default=24)
    parser.add_argument("--max-tokens", type=int, default=400)
    args = parser.parse_args()
    if not 1 <= args.max_requests <= 1000:
        parser.error("--max-requests must be between 1 and 1000.")
    try:
        key = api_key(args.env_file)
        asyncio.run(run(args, key))
    except (ValueError, OSError) as error:
        parser.exit(2, f"Live check could not run: {error}\n")
    except Exception as error:
        parser.exit(
            1,
            f"Live check paused after {type(error).__name__}; "
            "saved progress is retained.\n",
        )


if __name__ == "__main__":
    main()
