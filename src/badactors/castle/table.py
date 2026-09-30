"""The round table: accuse, defend, question, answer, vote, and the endgame.

The table is a small state machine the runner drives. :func:`table_turn` says
whose turn it is and what they are being asked; :func:`table_act` records the
answer; when a stage's queue is spent, :func:`table_advance` moves on, and after
the vote it banishes, reveals and decides whether the game is over. Nothing here
asks a model anything, so the rules stay deterministic and testable turn by turn.

Only those at the table when the round table's ticks run out take part. Anyone
still on the stairs misses it: they cannot speak or vote, though they can be
voted against. Everything said is speech in the gathering room, witnessed like any
other act, so a line spoken there is heard as voices through the walls.
"""

from __future__ import annotations

from collections import Counter

from badactors.castle.decisions import (
    Accuse,
    Answer,
    Decision,
    Defend,
    EndgameVote,
    Question,
    Request,
    StaySilent,
    Vote,
)
from badactors.castle.game import (
    Asked,
    Fate,
    GameState,
    TableState,
    VoteRound,
    alive,
    finish,
    living,
    living_traitors,
    name,
    names,
    present_at,
    remove,
    victor,
)
from badactors.castle.ledger import append_perceived
from badactors.castle.perceive import perceive
from badactors.castle.rng import draw
from badactors.castle.steward import announce


def open_table(game: GameState) -> None:
    """Seat whoever made it to the table, and call the session to order."""
    world = game.world
    present = present_at(game, world.map.gathering_room)
    everyone = living(game)
    final = len(everyone) <= game.config.endgame_at or world.day >= game.config.max_days
    start = (world.day - 1) % len(present) if present else 0
    order = present[start:] + present[:start]
    game.table = TableState(day=world.day, final=final, present=present, order=order)
    words = "The round table is in session."
    if final:
        words += (
            " This is the final round table: after each banishment, everyone left "
            "at the table votes to end the game or to banish again."
        )
    absent = [pid for pid in everyone if pid not in present]
    if absent:
        words += f" Not at the table: {names(game, absent)}."
    entry = announce(
        world,
        type="table_opened",
        words=words,
        payload={"present": ",".join(present), "final": "true" if final else "false"},
    )
    game.table.seqs.append(entry.seq)


def table_turn(game: GameState) -> tuple[str, Request] | None:
    """Whose turn it is and what they are asked, or ``None`` when the current
    stage has run its course and :func:`table_advance` should be called."""
    table = game.table
    if table is None or table.stage == "done" or game.status != "playing":
        return None
    queue = _queue(game, table)
    if table.cursor >= len(queue):
        return None
    return queue[table.cursor]


def table_act(
    game: GameState,
    player_id: str,
    request: Request,
    decision: Decision | None,
    *,
    failed: int | None = None,
) -> None:
    """Record one turn. ``decision`` is ``None`` when the turn failed, in which
    case ``failed`` is the ledger entry that says so."""
    table = game.table
    if table is None:
        return
    if decision is not None:
        seq = _speak(game, player_id, request, decision)
        table.seqs.append(seq)
        if isinstance(decision, Accuse) and decision.target not in table.accused:
            table.accused.append(decision.target)
        elif isinstance(decision, Question):
            table.questions.append(
                Asked(asker=player_id, target=decision.target, seq=seq)
            )
        elif isinstance(decision, Vote):
            table.votes[player_id] = decision.target
        elif isinstance(decision, EndgameVote):
            table.endgame_votes[player_id] = decision.choice
    elif failed is not None:
        table.seqs.append(failed)
    table.cursor += 1


def table_advance(game: GameState) -> None:
    """Close the current stage and open the next; after a vote, resolve it."""
    table = game.table
    if table is None:
        return
    stage = table.stage
    table.cursor = 0
    if stage == "accuse":
        table.stage = "defend" if table.accused else "question"
    elif stage == "defend":
        table.stage = "question"
    elif stage == "question":
        table.stage = "answer" if table.questions else "vote"
    elif stage == "answer":
        table.stage = "vote"
    elif stage == "vote":
        _resolve_vote(game, table)
    elif stage == "endgame":
        _resolve_endgame(game, table)


# ------------------------------------------------------------------------ queues


def _queue(game: GameState, table: TableState) -> list[tuple[str, Request]]:
    seated = [pid for pid in table.order if alive(game, pid)]
    everyone = living(game)

    def others(pid: str, pool: list[str]) -> list[str]:
        return [other for other in pool if other != pid]

    stage = table.stage
    if stage == "accuse":
        return [
            (
                pid,
                Request(
                    kind="accuse",
                    options=others(pid, everyone),
                    prompt="Your turn. Accuse someone of being a traitor and say "
                    "why, or stay silent.",
                ),
            )
            for pid in seated
        ]
    if stage == "defend":
        return [
            (
                pid,
                Request(
                    kind="defend",
                    prompt="You have been accused. Defend yourself, or stay silent.",
                ),
            )
            for pid in seated
            if pid in table.accused
        ]
    if stage == "question":
        return [
            (
                pid,
                Request(
                    kind="question",
                    options=others(pid, seated),
                    prompt="Put one question to someone at the table, or stay silent.",
                ),
            )
            for pid in seated
        ]
    if stage == "answer":
        return [
            (
                asked.target,
                Request(
                    kind="answer",
                    about=asked.asker,
                    prompt=f"{name(game, asked.asker)} put a question to you. "
                    "Answer it, or stay silent.",
                ),
            )
            for asked in table.questions
            if alive(game, asked.target)
        ]
    if stage == "vote":
        return [
            (
                pid,
                Request(
                    kind="vote",
                    options=others(pid, everyone),
                    prompt="Vote for the player to banish.",
                ),
            )
            for pid in seated
        ]
    if stage == "endgame":
        return [
            (
                pid,
                Request(
                    kind="endgame",
                    prompt="Vote to end the game now, or to banish again.",
                ),
            )
            for pid in seated
        ]
    return []


# ---------------------------------------------------------------------- speaking


def _speak(
    game: GameState,
    player_id: str,
    request: Request,
    decision: Decision,
) -> int:
    """Write one turn to the ledger as an act seen by the table."""
    world = game.world
    room_id = world.map.gathering_room
    private = {"diary": decision.diary} if decision.diary else {}
    payload: dict[str, str] = {"stage": request.kind}
    quote: str | None = None
    audible = True
    if isinstance(decision, Accuse):
        kind, did = "accuse", f"accused {name(game, decision.target)}:"
        payload["target"] = decision.target
        quote = decision.text
    elif isinstance(decision, Defend):
        kind, did, quote = "defend", "answered the accusation:", decision.text
    elif isinstance(decision, Question):
        kind, did = "question", f"asked {name(game, decision.target)}:"
        payload["target"] = decision.target
        quote = decision.text
    elif isinstance(decision, Answer):
        asker = request.about or player_id
        kind, did = "answer", f"answered {name(game, asker)}:"
        payload["target"] = asker
        quote = decision.text
    elif isinstance(decision, StaySilent):
        kind, did, audible = "stay_silent", "stayed silent.", False
        private["reason"] = decision.reason
    elif isinstance(decision, Vote):
        kind, did, audible = (
            "vote",
            f"voted to banish {name(game, decision.target)}.",
            False,
        )
        payload["target"] = decision.target
    elif isinstance(decision, EndgameVote):
        choice = "end the game" if decision.choice == "end" else "banish again"
        kind, did, audible = "endgame_vote", f"voted to {choice}.", False
        payload["choice"] = decision.choice
    else:
        raise ValueError(f"{decision.kind} is not a round-table move.")

    own = f"I {did}"
    if isinstance(decision, StaySilent):
        own = f"I stayed silent: {decision.reason}"
    perception = perceive(
        world,
        kind="talk" if audible else kind,
        actor_id=player_id,
        room_id=room_id,
        describe=lambda seen: f"{seen} {did}",
        own_text=own,
        channel="speech" if quote is not None else "direct",
        quote=quote,
        silent=not audible,
    )
    entry = append_perceived(
        world,
        perception,
        type=kind,
        text=f"{name(game, player_id)} {did}",
        actor_id=player_id,
        room_id=room_id,
        quote=quote,
        payload=payload,
        private=private,
    )
    return entry.seq


# --------------------------------------------------------------------- the vote


def _resolve_vote(game: GameState, table: TableState) -> None:
    world = game.world
    seat = {pid: index for index, pid in enumerate(world.seats)}
    counts = Counter(table.votes.values())
    if not counts:
        entry = announce(
            world,
            type="banishment",
            words="No votes were cast, so nobody is banished.",
            payload={"round": str(table.round)},
        )
        table.seqs.append(entry.seq)
        game.votes.append(
            VoteRound(
                day=world.day,
                round=table.round,
                votes={},
                banished=None,
                seq=entry.seq,
            )
        )
        table.stage = "done"
        if table.final:
            _end_by_roles(game, "Nobody would vote at the final table.")
        return

    top = max(counts.values())
    tied = sorted(
        (pid for pid, count in counts.items() if count == top),
        key=lambda pid: seat[pid],
    )
    rolls = []
    if len(tied) > 1:
        roll = draw(world.seed, world.rng_cursor, f"lots:day{world.day}:{table.round}")
        rolls.append(roll)
        banished = tied[int(roll.value * len(tied))]
    else:
        banished = tied[0]
    role = game.roles[banished]
    remove(game, banished, type="banished", cause="banished by the round table")

    ballots = "; ".join(
        f"{name(game, voter)} for {name(game, target)}"
        for voter, target in table.votes.items()
    )
    lots = (
        f" {names(game, tied)} were tied, and lots were drawn." if len(tied) > 1 else ""
    )
    what = "a traitor" if role == "traitor" else "one of the Faithful"
    who = name(game, banished)
    entry = announce(
        world,
        type="banishment",
        words=f"The votes: {ballots}.{lots} {who} is banished. {who} was {what}.",
        payload={
            "target": banished,
            "role": role,
            "round": str(table.round),
            "votes": ",".join(f"{v}={t}" for v, t in table.votes.items()),
        },
        rolls=rolls,
        cursor=world.rng_cursor + len(rolls),
    )
    table.seqs.append(entry.seq)
    game.votes.append(
        VoteRound(
            day=world.day,
            round=table.round,
            votes=dict(table.votes),
            banished=banished,
            seq=entry.seq,
        )
    )
    game.fates.append(
        Fate(
            player_id=banished, day=world.day, how="banished", role=role, seq=entry.seq
        )
    )

    table.stage = "done"
    winner = victor(game)
    if winner == "faithful":
        finish(game, winner, reason="The last traitor is gone.")
    elif winner == "traitor":
        finish(game, winner, reason="Only traitors are left.")
    elif table.final:
        if len(living(game)) <= 2:
            _end_by_roles(game, "Only two remain.")
        else:
            table.stage = "endgame"
            table.endgame_votes = {}


def _resolve_endgame(game: GameState, table: TableState) -> None:
    choices = list(table.endgame_votes.values())
    # Every vote cast was to end — vacuously so if nobody at the table could vote,
    # which also guarantees the last day ends the game.
    if all(choice == "end" for choice in choices):
        table.stage = "done"
        _end_by_roles(game, "Every vote was to end the game.")
        return
    world = game.world
    banishers = [
        pid for pid, choice in table.endgame_votes.items() if choice == "banish"
    ]
    entry = announce(
        world,
        type="endgame_result",
        words=f"{names(game, banishers)} voted to banish again, so the table "
        "votes once more.",
        payload={"banish": ",".join(banishers)},
    )
    table.seqs.append(entry.seq)
    table.round += 1
    table.votes = {}
    table.endgame_votes = {}
    table.stage = "vote"


def _end_by_roles(game: GameState, reason: str) -> None:
    """End the game now: any traitor still alive has survived to the end."""
    finish(game, "traitor" if living_traitors(game) else "faithful", reason=reason)


__all__ = ["open_table", "table_act", "table_advance", "table_turn"]
