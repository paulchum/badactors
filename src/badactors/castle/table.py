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

The table's tools are turns spent differently. Instead of an accusation a player
may **report** a manipulation attempt: the words are copied from their own record,
so the testimony is exact, and whoever the words came from is accused. Instead of
a question a player may **demand an alibi** for part of a day, which is answered
with a room and, if the answerer likes, a companion, who is then asked to vouch;
or **cross-examine** someone by reading one of their own records into the table's
hearing. A traitor may **filibuster** on any speaking turn: a speech past the
usual cap that uses up the stage's last turns, paid for with the filibusterer's
own next turns. Turns lost either way are announced, so everyone sees it.
"""

from __future__ import annotations

from collections import Counter

from badactors.castle.balance import ToolName
from badactors.castle.contracts import RECORD_LIMIT, WorldState
from badactors.castle.decisions import (
    Accuse,
    Alibi,
    Answer,
    CrossExamine,
    Decision,
    Defend,
    DemandAlibi,
    EndgameVote,
    Filibuster,
    Question,
    Report,
    Request,
    StaySilent,
    Vote,
    Vouch,
)
from badactors.castle.game import (
    Asked,
    Fate,
    GameState,
    Given,
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
from badactors.castle.kit import refusal
from badactors.castle.ledger import append_perceived, recent_records
from badactors.castle.perceive import perceive
from badactors.castle.rng import draw
from badactors.castle.steward import announce

SPEAKING: frozenset[str] = frozenset(
    {"accuse", "defend", "question", "answer", "alibi", "vouch"}
)
"""Turns that take the floor: these can be crowded out, or forfeited."""

REPORTABLE: frozenset[str] = frozenset({"whisper", "note", "book", "log"})
"""Channels whose words a player may report: what reached them privately."""

_CHANNEL_WORDS = {
    "whisper": "a whisper",
    "note": "a note",
    "book": "words in a book",
    "log": "lines in a log",
}


def open_table(game: GameState) -> None:
    """Seat whoever made it to the table, and call the session to order."""
    world = game.world
    present = present_at(game, world.map.gathering_room)
    everyone = living(game)
    final = len(everyone) <= game.config.endgame_at or world.day >= game.config.max_days
    # The order turns one seat a day round the whole table, so who speaks first
    # depends on the day, not on who happens to have come.
    start = (world.day - 1) % len(world.seats)
    order = [pid for pid in world.seats[start:] + world.seats[:start] if pid in present]
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
    stage has run its course and :func:`table_advance` should be called.

    Turns nobody may take are settled here, in the open: a turn forfeited to an
    earlier filibuster, and every turn left once a filibuster has used up the
    stage's floor. The Steward announces each, so the table sees it happen."""
    table = game.table
    if table is None or table.stage == "done" or game.status != "playing":
        return None
    while True:
        queue = _queue(game, table)
        if table.cursor >= len(queue):
            return None
        player_id, request = queue[table.cursor]
        if request.kind in SPEAKING:
            if table.cursor >= len(queue) - table.floor_lost:
                _floor_runs_out(game, table, [pid for pid, _ in queue[table.cursor :]])
                table.cursor = len(queue)
                return None
            if game.forfeits.get(player_id, 0) > 0:
                _forfeit(game, table, player_id)
                table.cursor += 1
                continue
        return player_id, _offer(game, player_id, request)


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
        _follow(game, table, player_id, request, decision, seq)
    elif failed is not None:
        table.seqs.append(failed)
    table.cursor += 1


def _follow(
    game: GameState,
    table: TableState,
    player_id: str,
    request: Request,
    decision: Decision,
    seq: int,
) -> None:
    """What a turn sets in motion: who must defend, who must answer, who must
    vouch, and what a filibuster costs."""
    if isinstance(decision, Accuse):
        _accuse(table, decision.target)
    elif isinstance(decision, Report):
        author = game.world.ledger[seq - 1].payload.get("target")
        if author and alive(game, author):
            _accuse(table, author)
    elif isinstance(decision, Question):
        table.questions.append(Asked(asker=player_id, target=decision.target, seq=seq))
    elif isinstance(decision, CrossExamine):
        table.questions.append(
            Asked(asker=player_id, target=decision.target, seq=seq, kind="cross")
        )
    elif isinstance(decision, DemandAlibi):
        table.questions.append(
            Asked(
                asker=player_id,
                target=decision.target,
                seq=seq,
                kind="alibi",
                day=decision.day,
                phase=decision.phase,
            )
        )
    elif isinstance(decision, Alibi):
        demand = _demand(table, player_id, request.about)
        if demand is not None and demand.day is not None and demand.phase is not None:
            table.alibis.append(
                Given(
                    claimant=player_id,
                    room=decision.room,
                    companion=decision.companion,
                    day=demand.day,
                    phase=demand.phase,
                    seq=seq,
                )
            )
    elif isinstance(decision, Filibuster):
        numbers = game.world.balance.tools.filibuster
        table.floor_lost += max(0, numbers.floor - 1)
        game.forfeits[player_id] = game.forfeits.get(player_id, 0) + numbers.forfeit
    elif isinstance(decision, Vote):
        table.votes[player_id] = decision.target
    elif isinstance(decision, EndgameVote):
        table.endgame_votes[player_id] = decision.choice


def _accuse(table: TableState, player_id: str) -> None:
    if player_id not in table.accused:
        table.accused.append(player_id)


def _demand(table: TableState, target: str, asker: str | None) -> Asked | None:
    return next(
        (
            asked
            for asked in table.questions
            if asked.kind == "alibi" and asked.target == target and asked.asker == asker
        ),
        None,
    )


def table_advance(game: GameState) -> None:
    """Close the current stage and open the next; after a vote, resolve it."""
    table = game.table
    if table is None:
        return
    stage = table.stage
    table.cursor = 0
    table.floor_lost = 0
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
        return _answers(game, table, seated, everyone)
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


def _answers(
    game: GameState, table: TableState, seated: list[str], everyone: list[str]
) -> list[tuple[str, Request]]:
    """Every question, cross-examination and demand gets its answer, in the order
    they were put; then everyone named as company in an alibi is asked to vouch."""
    rooms = sorted(room.id for room in game.world.map.rooms)
    turns: list[tuple[str, Request]] = []
    for asked in table.questions:
        if not alive(game, asked.target):
            continue
        asker = name(game, asked.asker)
        if asked.kind == "alibi" and asked.day is not None and asked.phase is not None:
            turns.append(
                (
                    asked.target,
                    Request(
                        kind="alibi",
                        about=asked.asker,
                        options=[pid for pid in everyone if pid != asked.target],
                        rooms=rooms,
                        prompt=f"{asker} demands to know where you were during "
                        f"{_phase(asked.phase)} on day {asked.day}. Name the room, "
                        "and anyone who was with you, or stay silent.",
                    ),
                )
            )
        else:
            turns.append(
                (
                    asked.target,
                    Request(
                        kind="answer",
                        about=asked.asker,
                        prompt=f"{asker} put a question to you. Answer it, or stay "
                        "silent.",
                    ),
                )
            )
    for given in table.alibis:
        companion = given.companion
        if companion is None or companion not in seated:
            continue
        turns.append(
            (
                companion,
                Request(
                    kind="vouch",
                    about=given.claimant,
                    prompt=f"{name(game, given.claimant)} says you were with them in "
                    f"the {game.world.map.room(given.room).name} during "
                    f"{_phase(given.phase)} on day {given.day}. Confirm it, deny it, "
                    "or stay silent.",
                ),
            )
        )
    return turns


def _offer(game: GameState, player_id: str, request: Request) -> Request:
    """The tools this player may use on this turn instead, and what each may
    cite. Only tools in their kit are offered, and only where they fit."""
    world = game.world
    tools: list[str] = []
    evidence: list[int] = []
    options = list(request.options)

    def usable(tool: ToolName) -> bool:
        return refusal(world, player_id, tool) is None

    if request.kind == "accuse" and usable("report"):
        reportable = _reportable(world, player_id)
        if reportable:
            tools.append("report")
            evidence = reportable
    if request.kind == "question" and options:
        if usable("demand_alibi"):
            tools.append("demand_alibi")
        citable = _citable(game, player_id)
        if citable and usable("cross_examine"):
            tools.append("cross_examine")
            evidence = citable
    limit: int | None = None
    if request.kind in ("accuse", "defend", "question", "answer") and usable(
        "filibuster"
    ):
        tools.append("filibuster")
        limit = game.world.balance.tools.filibuster.chars
        if not options:
            options = [pid for pid in living(game) if pid != player_id]
    if not tools:
        return request
    return request.model_copy(
        update={
            "tools": tools,
            "evidence": evidence,
            "options": options,
            "limit": limit,
        }
    )


def _reportable(world: WorldState, player_id: str) -> list[int]:
    """Words that reached this player privately from someone else, by entry: a
    whisper, or something they read that someone else wrote. Their own words
    are not theirs to report."""
    out: list[int] = []
    for seq in world.notebook.get(player_id, []):
        record = world.ledger[seq - 1].record_for(player_id)
        if (
            record is not None
            and record.quote is not None
            and record.channel in REPORTABLE
            and record.author != player_id
        ):
            out.append(seq)
    return out


def _citable(game: GameState, player_id: str) -> list[int]:
    """Records this player holds and can see: their notebook and recent memory."""
    world = game.world
    seqs = set(world.notebook.get(player_id, []))
    seqs.update(
        entry.seq
        for entry, _ in recent_records(
            world.ledger, player_id, game.config.recent_limit
        )
    )
    return sorted(seqs)


def _floor_runs_out(game: GameState, table: TableState, skipped: list[str]) -> None:
    if not skipped:
        return
    entry = announce(
        game.world,
        type="floor_out",
        words=f"Time. The floor has run out: {names(game, skipped)} will not speak "
        "this round.",
        payload={"stage": table.stage, "skipped": ",".join(skipped)},
    )
    table.seqs.append(entry.seq)


def _forfeit(game: GameState, table: TableState, player_id: str) -> None:
    left = game.forfeits.get(player_id, 0) - 1
    if left > 0:
        game.forfeits[player_id] = left
    else:
        game.forfeits.pop(player_id, None)
    entry = announce(
        game.world,
        type="forfeit",
        words=f"{name(game, player_id)} has no turn: it was spent holding the floor "
        "earlier.",
        payload={"stage": table.stage, "player": player_id},
    )
    table.seqs.append(entry.seq)


def _phase(phase: str) -> str:
    return phase.replace("_", " ")


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
    noted = False
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
    elif isinstance(decision, Report):
        kind, did, quote = _report(game, player_id, decision, payload)
        noted = True
    elif isinstance(decision, Filibuster):
        kind, did, quote = "filibuster", "held the floor at length:", decision.text
        payload.update({"tool": "filibuster", "chars": str(len(decision.text))})
        private["intent"] = decision.intent.render()
        noted = True
    elif isinstance(decision, DemandAlibi):
        target = name(game, decision.target)
        when = f"{_phase(decision.phase)} on day {decision.day}"
        kind, did = "demand_alibi", f"demanded that {target} account for {when}:"
        quote = decision.text or f"Where were you during {when}, {target}?"
        payload.update(
            {
                "tool": "demand_alibi",
                "target": decision.target,
                "day": str(decision.day),
                "phase": decision.phase,
            }
        )
        noted = True
    elif isinstance(decision, CrossExamine):
        kind, did, quote = _cross_examine(game, player_id, decision, payload)
        noted = True
    elif isinstance(decision, Alibi):
        kind, did, quote = _alibi(game, player_id, request, decision, payload)
        noted = True
    elif isinstance(decision, Vouch):
        kind, did, quote = _vouch(game, player_id, request, decision, payload)
        noted = True
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
    if noted:
        perception.note(*(record.player_id for record in perception.records))
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


def _report(
    game: GameState, player_id: str, decision: Report, payload: dict[str, str]
) -> tuple[str, str, str]:
    """The reported words, copied from the reporter's own record, and who they
    came from as the reporter recorded it."""
    cited = game.world.ledger[decision.evidence - 1]
    record = cited.record_for(player_id)
    if record is None or record.quote is None:  # pragma: no cover - checked on ask
        raise ValueError("A report cites a record its reporter does not hold.")
    what = _CHANNEL_WORDS.get(record.channel, "words")
    author = record.author
    source = f"{what} from {name(game, author)}" if author else f"{what}, unsigned"
    payload.update(
        {"tool": "report", "evidence": str(cited.seq), "channel": record.channel}
    )
    if author:
        payload["target"] = author
    return "report", f"reported, word for word, {source}:", record.quote


def _cross_examine(
    game: GameState, player_id: str, decision: CrossExamine, payload: dict[str, str]
) -> tuple[str, str, str]:
    """The examiner's own record, read out exactly, and their question."""
    cited = game.world.ledger[decision.evidence - 1]
    record = cited.record_for(player_id)
    if record is None:  # pragma: no cover - checked on ask
        raise ValueError(
            "A cross-examination cites a record its examiner does not hold."
        )
    read = f"At t{cited.tick} I recorded: {record.text}"
    if record.quote is not None:
        read += f" “{record.quote}”"
    room = RECORD_LIMIT - len(decision.text) - 2
    quote = f"{read[:room]} {decision.text}"
    payload.update(
        {
            "tool": "cross_examine",
            "target": decision.target,
            "evidence": str(cited.seq),
        }
    )
    target = name(game, decision.target)
    return "cross_examine", f"cross-examined {target}, reading from a record:", quote


def _alibi(
    game: GameState,
    player_id: str,
    request: Request,
    decision: Alibi,
    payload: dict[str, str],
) -> tuple[str, str, str]:
    table = game.table
    asker = request.about or player_id
    demand = _demand(table, player_id, asker) if table is not None else None
    day = (
        demand.day if demand is not None and demand.day is not None else game.world.day
    )
    phase = (
        demand.phase if demand is not None and demand.phase is not None else "free_time"
    )
    room = game.world.map.room(decision.room).name
    claim = f"I was in the {room}"
    if decision.companion is not None:
        claim += f", with {name(game, decision.companion)}"
    claim += f", during {_phase(phase)} on day {day}."
    quote = f"{claim} {decision.text}".strip()
    payload.update(
        {
            "target": asker,
            "room": decision.room,
            "companion": decision.companion or "",
            "day": str(day),
            "phase": phase,
            "demand": str(demand.seq) if demand is not None else "",
        }
    )
    return "alibi", f"gave {name(game, asker)} an alibi:", quote


def _vouch(
    game: GameState,
    player_id: str,
    request: Request,
    decision: Vouch,
    payload: dict[str, str],
) -> tuple[str, str, str]:
    table = game.table
    claimant = request.about or player_id
    given = next(
        (
            alibi
            for alibi in reversed(table.alibis if table is not None else [])
            if alibi.claimant == claimant and alibi.companion == player_id
        ),
        None,
    )
    whom = name(game, claimant)
    room = game.world.map.room(given.room).name if given is not None else "room"
    said = (
        f"Yes: I was with {whom} in the {room}."
        if decision.confirm
        else f"No: I was not with {whom} in the {room}."
    )
    payload.update(
        {
            "target": claimant,
            "confirm": "true" if decision.confirm else "false",
            "alibi": str(given.seq) if given is not None else "",
            "room": given.room if given is not None else "",
            "day": str(given.day) if given is not None else "",
            "phase": given.phase if given is not None else "",
        }
    )
    verb = "vouched for" if decision.confirm else "refused to vouch for"
    return "vouch", f"{verb} {whom}:", f"{said} {decision.text}".strip()


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
    """End the game if every vote cast was to end it, or if none was cast at
    all, as a banishment vote nobody casts ends the final table. What the
    Steward says is what happened: who voted, and who did not."""
    choices = table.endgame_votes
    unheard = [pid for pid in table.order if alive(game, pid) and pid not in choices]
    if not choices:
        table.stage = "done"
        _end_by_roles(
            game, "No vote to end the game or to banish again was cast, so it ends."
        )
        return
    if all(choice == "end" for choice in choices.values()):
        table.stage = "done"
        if unheard:
            _end_by_roles(
                game,
                "Every vote cast was to end the game; "
                f"{names(game, unheard)} cast none.",
            )
        else:
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
