"""``castle-verify``: re-run a game from its receipt and check every number.

A **verify bundle** holds everything a stranger needs to play a game again
without any model: the seed, the castle and the cast, the merged balance config
the game was played with, every recorded decision, and the ledger the game
published. A model game adds one **exchange** per provider call the harness
made: its question (seat, model id, kind, attempt), the provider, the mode, a
SHA-256 of the request body and of the response body, the HTTP status of an
error, and the reason class when the answer could not be used. Exchanges hold
hashes, never words, keys or headers; the request body never holds a key, which
travels only in a header.

Every part is **hash-chained**: each link is the SHA-256 of the previous link,
the part's kind and the part's canonical JSON, so changing, dropping or
reordering anything breaks its link and every one after it, and the last link —
the head — names the whole game. Publish the head beside the replay, and
``--head`` checks a bundle against it.

``castle-verify`` then checks, and fails loudly on any mismatch:

1. **the chain** — every link, and the head;
2. **every roll** in the published ledger, from the seed alone: the value drawn
   at its cursor for its purpose, whether it came up under its threshold, and
   cursors that run from zero with no gap and no repeat, so a dropped roll shows;
3. **the replay** — the game played again from the seed, the config and the
   decisions, and its ledger set beside the published one entry by entry: the
   rolls, the perception records each witness wrote, the custody moves, and
   everything else;
4. **custody** — every object's custody chain and final placement, derived from
   both ledgers;
5. **the result** — the winner, and the roll cursor the world ended on;
6. **the exchanges** — each names its seat's cast model, on a model seat or a
   mock one.

    uv run castle-verify out/replays/models/mixed-s3-r2.json.gz
    uv run castle-verify game.bundle.json --head 3f1c...
    uv run castle-verify --make data/live/seed7.json --out seed7.bundle.json.gz

``--make`` builds a bundle from a saved record, reading the harness log beside
it (``<record>.harness.jsonl``) when there is one.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from badactors.castle.contracts import Contract, LedgerEntry
from badactors.castle.ledger import CUSTODY_TYPES, custody_chain, replay_placements
from badactors.castle.play import (
    FailureReason,
    GameRecord,
    ReplayDiverged,
    is_mock,
    is_model,
    load_record,
    replay,
)
from badactors.castle.provider import (
    HarnessLog,
    Provider,
    ProviderCall,
    ProviderError,
    Reply,
    request_body,
)
from badactors.castle.rng import draw

GENESIS = "badactors-verify/1"
"""What the first link is chained from."""

HEX = r"^[0-9a-f]{64}$"

OPERATOR_STATUSES = frozenset({401, 402, 403})


def canonical(value: Any) -> bytes:
    """One byte string per JSON value: sorted keys, no spaces, UTF-8."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


# ------------------------------------------------------------------ exchanges


class Exchange(Contract):
    """One provider call, as the harness made it: hashes, never words."""

    seat: str
    model: str
    kind: str
    day: int
    tick: int
    ordinal: int
    attempt: int
    provider: str
    mode: str
    request: str = Field(pattern=HEX)
    response: str | None = Field(default=None, pattern=HEX)
    status: int | None = None
    """The HTTP status of an error; ``None`` for an answer, or no response."""
    failure: FailureReason | None = None
    """Why the harness could not use what came back, if it could not."""


@dataclass
class HashingProvider:
    """Wraps a provider and writes an ``exchange`` event to the harness log for
    every call: hashes of the request and response bodies, never the bodies."""

    inner: Provider
    log: HarnessLog
    name: str

    async def complete(self, call: ProviderCall) -> Reply:
        event: dict[str, Any] = {
            "ref": _ref(call),
            "provider": self.name,
            "mode": call.mode,
            "request": digest(request_body(call)),
        }
        try:
            reply = await self.inner.complete(call)
        except ProviderError as exc:
            self.log.write(
                "exchange",
                **event,
                response=None if exc.body is None else digest(exc.body),
                status=exc.status,
            )
            raise
        self.log.write("exchange", **event, response=digest(reply.raw), status=None)
        return reply


def _ref(call: ProviderCall) -> dict[str, Any]:
    ref = call.ref
    return {
        "seat": ref.seat,
        "model": ref.model,
        "kind": ref.kind,
        "day": ref.day,
        "tick": ref.tick,
        "ordinal": ref.ordinal,
        "attempt": ref.attempt,
    }


def exchanges(events: Iterable[Mapping[str, Any]]) -> list[Exchange]:
    """The harness log's exchanges, each with the reason the harness gave for
    not using its answer (its ``bad_answer`` event for the same question and
    attempt), or ``transport`` for a call that raised, unless the error was
    the operator's."""
    listed = list(events)

    def key(ref: Mapping[str, Any]) -> tuple[Any, ...]:
        return (ref.get("seat"), ref.get("ordinal"), ref.get("attempt"))

    reasons = {
        key(event["ref"]): event.get("reason")
        for event in listed
        if event.get("event") == "bad_answer"
    }
    out = []
    for event in listed:
        if event.get("event") != "exchange":
            continue
        ref = event["ref"]
        failure = reasons.get(key(ref))
        status = event.get("status")
        if failure is None and (status is not None or event.get("response") is None):
            # An error the harness did not class; a 401, 402 or 403 is the
            # operator's, never the model's.
            failure = None if status in OPERATOR_STATUSES else "transport"
        out.append(
            Exchange(
                **ref,
                provider=event["provider"],
                mode=event["mode"],
                request=event["request"],
                response=event.get("response"),
                status=event.get("status"),
                failure=failure,
            )
        )
    return out


def read_events(path: str | Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()
    ]


# ------------------------------------------------------------------ bundles

PartKind = Literal["game", "decision", "entry", "exchange"]


class Bundle(Contract):
    """A game's receipt: its record, its exchanges, and their hash chain."""

    bundle_version: Literal[1] = 1
    record: GameRecord
    exchanges: list[Exchange] = Field(default_factory=list)
    links: list[str]
    head: str = Field(pattern=HEX)


def parts(record: GameRecord, calls: Sequence[Exchange]) -> list[tuple[PartKind, Any]]:
    """Everything the chain covers, in order: the game's header (all but its
    decisions and ledger), each decision, each ledger entry, each exchange. A
    game played from no schedule has no arm, and its header leaves the field
    out, so it hashes as records from before the field did."""
    left_out = {"decisions", "ledger"} | ({"arm"} if record.arm is None else set())
    header = record.model_dump(mode="json", exclude=left_out)
    return [
        ("game", header),
        *(("decision", d.model_dump(mode="json")) for d in record.decisions),
        *(("entry", e.model_dump(mode="json")) for e in record.ledger),
        *(("exchange", x.model_dump(mode="json")) for x in calls),
    ]


def chain(record: GameRecord, calls: Sequence[Exchange]) -> list[str]:
    links = []
    previous = GENESIS
    for kind, part in parts(record, calls):
        previous = hashlib.sha256(
            previous.encode() + b"|" + kind.encode() + b"|" + canonical(part)
        ).hexdigest()
        links.append(previous)
    return links


def make_bundle(record: GameRecord, events: Iterable[Mapping[str, Any]] = ()) -> Bundle:
    calls = exchanges(events)
    links = chain(record, calls)
    return Bundle(record=record, exchanges=calls, links=links, head=links[-1])


def save_bundle(bundle: Bundle, path: str | Path) -> None:
    """Write a bundle; a path ending ``.gz`` is gzipped."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    text = bundle.model_dump_json()
    if target.suffix == ".gz":
        target.write_bytes(gzip.compress(text.encode(), mtime=0))
    else:
        target.write_text(text)


def load_bundle(path: str | Path) -> Bundle:
    source = Path(path)
    raw = source.read_bytes()
    if source.suffix == ".gz":
        raw = gzip.decompress(raw)
    return Bundle.model_validate_json(raw)


# ------------------------------------------------------------------ checking


@dataclass(frozen=True)
class Problem:
    check: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.check}] {self.detail}"


def verify(bundle: Bundle, *, head: str | None = None) -> list[Problem]:
    """Every mismatch in the bundle; none means it verified."""
    record = bundle.record
    return [
        *check_chain(bundle, head=head),
        *check_rolls(record),
        *check_replay(record),
        *check_exchanges(bundle),
    ]


def check_chain(bundle: Bundle, *, head: str | None = None) -> list[Problem]:
    found = chain(bundle.record, bundle.exchanges)
    problems = []
    if len(found) != len(bundle.links):
        problems.append(
            Problem(
                "chain",
                f"the bundle has {len(bundle.links)} links for {len(found)} parts",
            )
        )
    kinds = [kind for kind, _ in parts(bundle.record, bundle.exchanges)]
    for index, (mine, theirs) in enumerate(zip(found, bundle.links, strict=False)):
        if mine != theirs:
            problems.append(
                Problem(
                    "chain",
                    f"link {index} ({kinds[index]} {_ordinal(kinds, index)}) does not "
                    "match its part; every link after it breaks too",
                )
            )
            break
    if found and bundle.head != found[-1]:
        problems.append(Problem("chain", "the head does not match the last link"))
    if head is not None and head != bundle.head:
        problems.append(
            Problem("chain", f"the head is {bundle.head}, not the published {head}")
        )
    return problems


def _ordinal(kinds: Sequence[str], index: int) -> int:
    return sum(1 for kind in kinds[:index] if kind == kinds[index]) + 1


def check_rolls(record: GameRecord) -> list[Problem]:
    """Re-derive every roll in the published ledger from the seed."""
    problems = []
    cursors: list[int] = []
    for entry in record.ledger:
        for roll in entry.rolls:
            cursors.append(roll.cursor)
            again = draw(
                record.seed, roll.cursor, roll.purpose, threshold=roll.threshold
            )
            if (again.value, again.success) != (roll.value, roll.success):
                problems.append(
                    Problem(
                        "roll",
                        f"entry {entry.seq}: the roll at cursor {roll.cursor} "
                        f"({roll.purpose}) is {roll.value} ({roll.success}); the "
                        f"seed gives {again.value} ({again.success})",
                    )
                )
    if cursors != sorted(cursors) or len(set(cursors)) != len(cursors):
        repeated = sorted({c for c in cursors if cursors.count(c) > 1})
        problems.append(
            Problem(
                "roll",
                "cursors run out of order"
                + (f", and {repeated[:5]} are used twice" if repeated else ""),
            )
        )
    missing = sorted(set(range(max(cursors, default=-1) + 1)) - set(cursors))
    if missing:
        problems.append(
            Problem(
                "roll",
                f"no roll is recorded at cursor {missing[0]}"
                + (f" or {len(missing) - 1} others" if len(missing) > 1 else "")
                + ": a roll was dropped",
            )
        )
    return problems


def check_replay(record: GameRecord) -> list[Problem]:
    """Play the game again from its seed, config and decisions, and set the
    ledger it writes beside the published one."""
    try:
        game = replay(record)
    except ReplayDiverged as exc:
        return [Problem("replay", f"the decisions do not replay: {exc}")]
    except Exception as exc:
        return [Problem("replay", f"the game cannot be replayed: {exc!r}")]
    mine, theirs = game.world.ledger, record.ledger
    problems = compare_ledgers(mine, theirs)
    problems += compare_custody(mine, theirs)
    if game.winner != record.winner:
        problems.append(
            Problem(
                "result",
                f"the replay's winner is {game.winner}, the record's {record.winner}",
            )
        )
    drawn = sum(len(entry.rolls) for entry in theirs)
    if game.world.rng_cursor != drawn:
        problems.append(
            Problem(
                "roll",
                f"the replay drew {game.world.rng_cursor} rolls; the published "
                f"ledger records {drawn}",
            )
        )
    return problems


ASPECTS: tuple[tuple[str, str], ...] = (
    ("roll", "rolls"),
    ("perception", "witnesses"),
)


def compare_ledgers(
    mine: Sequence[LedgerEntry], theirs: Sequence[LedgerEntry]
) -> list[Problem]:
    """The first difference of each kind, and how many entries differ."""
    problems = []
    if len(mine) != len(theirs):
        problems.append(
            Problem(
                "ledger",
                f"the replay writes {len(mine)} entries; the published ledger "
                f"has {len(theirs)}",
            )
        )
    first: dict[str, Problem] = {}
    differing = 0
    for again, published in zip(mine, theirs, strict=False):
        if again == published:
            continue
        differing += 1
        seq = published.seq
        for check, attribute in ASPECTS:
            if check in first:
                continue
            ours, given = getattr(again, attribute), getattr(published, attribute)
            if ours != given:
                first[check] = Problem(
                    check, f"entry {seq} ({published.type}): {_first_diff(ours, given)}"
                )
        if "custody" not in first and (
            again.type in CUSTODY_TYPES or published.type in CUSTODY_TYPES
        ):
            if (again.type, again.payload) != (published.type, published.payload):
                first["custody"] = Problem(
                    "custody",
                    f"entry {seq}: the replay has {_move(again)}; the record "
                    f"says {_move(published)}",
                )
        if "entry" not in first:
            fields = [
                name
                for name in LedgerEntry.model_fields
                if getattr(again, name) != getattr(published, name)
            ]
            first["entry"] = Problem(
                "entry",
                f"entry {seq} ({published.type}) differs in {', '.join(fields)}",
            )
    problems.extend(first.values())
    if differing:
        problems.append(
            Problem("ledger", f"{differing} entries differ from the replay")
        )
    return problems


def _move(entry: LedgerEntry) -> str:
    return f"{entry.type} of {entry.payload.get('thing_id', 'nothing')}"


def _first_diff(ours: Sequence[Any], given: Sequence[Any]) -> str:
    if len(ours) != len(given):
        return f"the replay records {len(ours)}, the ledger {len(given)}"
    for index, (a, b) in enumerate(zip(ours, given, strict=True)):
        if a == b:
            continue
        if isinstance(a, Contract) and type(a) is type(b):
            fields = [
                f"{name} {_short(getattr(a, name))} in the replay, "
                f"{_short(getattr(b, name))} published"
                for name in type(a).model_fields
                if getattr(a, name) != getattr(b, name)
            ]
            return f"item {index}: " + "; ".join(fields)
        return f"item {index} is {_short(a)} in the replay, {_short(b)} published"
    return "they differ"


def _short(value: Any) -> str:
    dumped = value.model_dump(mode="json") if isinstance(value, Contract) else value
    text = json.dumps(dumped, sort_keys=True)
    return text if len(text) <= 120 else text[:117] + "..."


def compare_custody(
    mine: Sequence[LedgerEntry], theirs: Sequence[LedgerEntry]
) -> list[Problem]:
    """Every object's custody chain and final placement, both ways."""
    things = sorted(
        {
            entry.payload["thing_id"]
            for ledger in (mine, theirs)
            for entry in ledger
            if entry.type in CUSTODY_TYPES and "thing_id" in entry.payload
        }
    )
    problems = []
    for thing in things:
        if custody_chain(mine, thing) != custody_chain(theirs, thing):
            problems.append(
                Problem("custody", f"{thing}'s custody chain differs from the replay")
            )
            break
    if replay_placements(mine) != replay_placements(theirs):
        problems.append(
            Problem("custody", "where things end up differs from the replay")
        )
    return problems


def check_exchanges(bundle: Bundle) -> list[Problem]:
    record = bundle.record
    cast = record.cast.models()
    problems = []
    for index, call in enumerate(bundle.exchanges):
        agent = record.agents.get(call.seat)
        if agent is None:
            problems.append(Problem("exchange", f"exchange {index} names no seat"))
        elif (is_model(agent) or is_mock(agent)) and call.model != cast[call.seat]:
            problems.append(
                Problem(
                    "exchange",
                    f"exchange {index} asked {call.model} for seat {call.seat}, "
                    f"which the cast gives to {cast[call.seat]}",
                )
            )
    return problems


# ------------------------------------------------------------------ the command


def summary(bundle: Bundle) -> str:
    record = bundle.record
    rolls = sum(len(entry.rolls) for entry in record.ledger)
    if any(is_mock(agent) for agent in record.agents.values()):
        played = "the seeded bot behind the mock provider"
    elif record.reference_bots:
        played = "reference bots"
    else:
        played = "models"
    return (
        f"seed {record.seed}, {record.world_id}, played by {played}: "
        f"{len(record.decisions)} decisions, {len(record.ledger)} entries, "
        f"{rolls} rolls, {len(bundle.exchanges)} exchanges; head {bundle.head}"
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="castle-verify",
        description="Replay a game from its verify bundle and check every number.",
    )
    parser.add_argument(
        "bundles", nargs="*", help="Verify bundles (.json or .json.gz)."
    )
    parser.add_argument(
        "--head", help="The published head to check one bundle against."
    )
    parser.add_argument("--make", help="Build a bundle from this saved game record.")
    parser.add_argument(
        "--harness", help="The harness log (default: beside the record)."
    )
    parser.add_argument("--out", help="Where --make writes the bundle.")
    args = parser.parse_args(argv)
    if args.make:
        record_path = Path(args.make)
        harness = (
            Path(args.harness)
            if args.harness
            else record_path.with_suffix(".harness.jsonl")
        )
        events = read_events(harness) if harness.exists() else []
        bundle = make_bundle(load_record(record_path), events)
        out = Path(args.out) if args.out else record_path.with_suffix(".bundle.json.gz")
        save_bundle(bundle, out)
        print(f"Wrote {out}: {summary(bundle)}")
        return
    if not args.bundles:
        parser.error("name a bundle to verify, or --make one from a record")
    if args.head and len(args.bundles) != 1:
        parser.error("--head checks one bundle")
    failed = 0
    for path in args.bundles:
        try:
            bundle = load_bundle(path)
        except (OSError, ValueError) as exc:
            failed += 1
            print(f"FAILED {path}: not a verify bundle: {str(exc)[:300]}")
            continue
        problems = verify(bundle, head=args.head)
        if problems:
            failed += 1
            print(f"FAILED {path}: {summary(bundle)}")
            for problem in problems:
                print(f"  {problem}")
        else:
            print(f"verified {path}: {summary(bundle)}")
    if failed:
        sys.exit(f"{failed} of {len(args.bundles)} bundles failed verification.")


if __name__ == "__main__":
    main()


__all__ = [
    "GENESIS",
    "Bundle",
    "Exchange",
    "HashingProvider",
    "Problem",
    "canonical",
    "chain",
    "check_chain",
    "check_exchanges",
    "check_replay",
    "check_rolls",
    "compare_custody",
    "compare_ledgers",
    "digest",
    "exchanges",
    "load_bundle",
    "main",
    "make_bundle",
    "parts",
    "read_events",
    "save_bundle",
    "summary",
    "verify",
]
