"""Run a persistent worker independently of the spectator web server."""

from __future__ import annotations

import argparse
import asyncio
import os

from badactors.persistence import SQLiteEventStore
from badactors.simulation.runtime import WorldRuntime


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db", default=os.getenv("BADACTORS_DB", "data/badactors.sqlite3")
    )
    args = parser.parse_args()
    store = SQLiteEventStore(args.db)
    try:
        asyncio.run(WorldRuntime(store).serve())
    except KeyboardInterrupt:
        pass
    finally:
        store.close()


if __name__ == "__main__":
    main()
