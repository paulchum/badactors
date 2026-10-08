"""The plan-step tools: what happens when a player uses one during a tick.

:func:`use_tool` is the shared machinery's front door for plan steps. It checks
the gate and the cost through :func:`~badactors.castle.kit.refusal`, then hands
the step to its tool's handler, which checks the physical preconditions, draws
its rolls through an :class:`~badactors.castle.kit.Attempt`, applies the effect
and writes one entry carrying ``payload["tool"]``, the rolls, and any declared
intent in ``private``.

What witnesses record is the physical act in the room's light: someone writing,
someone handling the camera log, someone working a station. A covert act — a hand
in a pocket, a slip into a coat — has no witnesses but its user, unless it is
caught. A whisper-borne tool (a rumour, a hook, a gaslight, a threat) reaches
its listener exactly as a whisper does, so nothing in the listener's record says
which tool, if any, the words came from.

Several acts need light. Nobody can read, write, compare hands or inspect a seal
in the dark, and being told so is a physically obvious failure. Contact acts —
searching a person, picking a pocket, planting something — need the other person
within reach, and finding they are not is obvious too. A whisper is different:
out of bright light it goes ahead whether or not anyone is there to hear it, as a
plain whisper does, because a refusal would say who was in the room.
"""

from __future__ import annotations

from dataclasses import dataclass

from badactors.castle.acts import (
    blocked,
    change_garment,
    find_thing,
    on_person,
    reach,
    thing,
)
from badactors.castle.balance import ToolName
from badactors.castle.contracts import (
    GARMENT,
    SHIELD,
    Blackmail,
    Channel,
    CheckRegister,
    CleanUp,
    CompareHands,
    Counterfeit,
    Disguise,
    Forge,
    Gaslight,
    HeldBy,
    Honeypot,
    Hook,
    InContainer,
    InRoom,
    Inspect,
    InspectSeal,
    Intent,
    LedgerEntry,
    Lighting,
    Offstage,
    Plant,
    PlayerState,
    Read,
    ReadLog,
    Relay,
    Rumor,
    Sabotage,
    Search,
    SearchPerson,
    Steal,
    Tamper,
    Thing,
    ToolUse,
    WitnessRecord,
    WorldState,
    Write,
)
from badactors.castle.kit import Attempt, refusal
from badactors.castle.ledger import (
    append,
    append_perceived,
    entry_at,
    move_object,
    placement_payload,
)
from badactors.castle.papers import (
    LOG_KINDS,
    STEWARD,
    Writing,
    ashes,
    glass_state,
    registered,
    render_log,
    rumor_origin,
    secrets_known,
    writings,
)
from badactors.castle.perceive import (
    Perception,
    awake_in,
    carried_things,
    perceive,
    room_of,
    seen_as,
    worn_garment,
)


def use_tool(
    world: WorldState, player: PlayerState, room_id: str, step: ToolUse
) -> None:
    """Use one tool, in place on a world the tick has already forked."""
    name: ToolName = step.tool
    reason = refusal(world, player.id, name)
    if reason is not None:
        blocked(world, player, room_id, reason, tool=name)
        return
    use = _Use(Attempt(world, player.id, name), player, room_id)
    if isinstance(step, Search):
        _search(use)
    elif isinstance(step, Inspect):
        _inspect(use, step)
    elif isinstance(step, Read):
        _read(use, step)
    elif isinstance(step, ReadLog):
        _read_log(use, step.thing_id, step.day, step.subject)
    elif isinstance(step, Write):
        _write(use, step)
    elif isinstance(step, Relay):
        _relay(use, step)
    elif isinstance(step, CompareHands):
        _compare_hands(use, step)
    elif isinstance(step, CheckRegister):
        _check_register(use, step)
    elif isinstance(step, InspectSeal):
        _inspect_seal(use, step)
    elif isinstance(step, SearchPerson):
        _search_person(use, step)
    elif isinstance(step, Forge):
        _forge(use, step)
    elif isinstance(step, Plant):
        _plant(use, step)
    elif isinstance(step, Tamper):
        _tamper(use, step)
    elif isinstance(step, Rumor):
        _rumor(use, step)
    elif isinstance(step, Sabotage):
        _sabotage(use, step)
    elif isinstance(step, Steal):
        _steal(use, step)
    elif isinstance(step, Blackmail):
        _blackmail(use, step)
    elif isinstance(step, Disguise):
        _disguise(use, step)
    elif isinstance(step, CleanUp):
        _clean_up(use, step)
    elif isinstance(step, Counterfeit):
        _counterfeit(use, step)
    elif isinstance(step, Hook):
        _hook(use, step)
    elif isinstance(step, Honeypot):
        _honeypot(use, step)
    elif isinstance(step, Gaslight):
        _gaslight(use, step)
    else:  # pragma: no cover - the step union is closed
        raise TypeError(f"no handler for {name}")


# ------------------------------------------------------------------ the context


@dataclass
class _Use:
    attempt: Attempt
    player: PlayerState
    room_id: str

    @property
    def world(self) -> WorldState:
        return self.attempt.world

    @property
    def tool(self) -> ToolName:
        return self.attempt.tool

    @property
    def lighting(self) -> Lighting:
        return self.world.map.room(self.room_id).lighting

    @property
    def room_name(self) -> str:
        return self.world.map.room(self.room_id).name

    def refuse(self, reason: str) -> None:
        blocked(self.world, self.player, self.room_id, reason, tool=self.tool)

    def reachable(self, thing_id: str) -> Thing | None:
        """The object, if this player could lay a hand on it; refuses if not."""
        if thing_id in reach(self.world, self.player.id, self.room_id):
            return thing(self.world, thing_id)
        self.refuse("that is not within reach")
        return None

    def held(self, thing_id: str) -> Thing | None:
        """The object, if this player has it in hand; refuses if not."""
        if on_person(self.world, self.player.id, thing_id) == "held":
            return thing(self.world, thing_id)
        self.refuse("that is not in hand")
        return None

    def lit(self, verb: str) -> bool:
        if self.lighting == "dark":
            self.refuse(f"it is too dark here to {verb}")
            return False
        return True

    def fixture(self, kind: str) -> Thing | None:
        """A thing of this kind in plain sight here, such as the register."""
        here = reach(self.world, self.player.id, self.room_id)
        for item in self.world.map.things:
            if item.kind == kind and item.id in here:
                return item
        return None

    def within_reach_of(self, other: str) -> bool:
        """Contact: the other player must be here and awake, and touch tells."""
        if other == self.player.id or other not in self.world.players:
            self.refuse("there is nobody by that name to reach")
            return False
        if other not in awake_in(self.world, self.room_id):
            self.refuse("they are not within reach")
            return False
        return True

    def player_name(self, player_id: str) -> str:
        return self.world.players[player_id].name

    def intent_fits(self, intent: Intent | None) -> bool:
        """A declared intent must name real people and places."""
        if intent is None:
            return True
        world = self.world
        rooms = {room.id for room in world.map.rooms}
        sound = intent.target in world.players and intent.target != self.player.id
        if intent.effect in ("vote", "alibi"):
            sound = sound and intent.want in world.players
        elif intent.effect in ("move", "search"):
            sound = sound and intent.want in rooms
        if not sound:
            self.refuse("the intent you declared names nobody and nowhere real")
        return sound

    def record(
        self,
        perception: Perception,
        *,
        type: str,
        text: str,
        payload: dict[str, str] | None = None,
        quote: str | None = None,
        intent: Intent | None = None,
    ) -> LedgerEntry:
        """Write the use to the ledger with its rolls, its tool and its intent.
        The user's own record of a tool is always noted. An act on an object
        names it as the act's target, as anyone watching could see."""
        perception.note(self.player.id)
        facts = {"tool": self.tool, **(payload or {})}
        if "thing_id" in facts and "target" not in facts:
            facts["target"] = facts["thing_id"]
        return append_perceived(
            self.world,
            perception,
            type=type,
            text=text,
            actor_id=self.player.id,
            room_id=self.room_id,
            quote=quote,
            payload=facts,
            private={"intent": intent.render()} if intent is not None else None,
            rolls=self.attempt.rolls,
        )

    def seen(
        self,
        *,
        kind: str,
        describe: str,
        own_text: str,
        silent: bool = True,
        private: bool = False,
    ) -> Perception:
        """How the room takes in the act. ``describe`` is what a witness saw, with
        ``{seen}`` standing for the actor as the light lets them make it out.
        Witnesses see the act, never words written or read."""
        return perceive(
            self.world,
            kind=kind,
            actor_id=self.player.id,
            room_id=self.room_id,
            describe=lambda seen: describe.format(seen=seen),
            own_text=own_text,
            silent=silent,
            private=private,
        )

    def words(
        self,
        perception: Perception,
        *,
        text: str,
        channel: Channel,
        quote: str,
        author: str | None,
    ) -> None:
        """Give the user alone the words they wrote or read."""
        perception.put(
            WitnessRecord(
                player_id=self.player.id,
                sense="own",
                confidence="clear",
                channel=channel,
                text=text,
                quote=quote,
                author=author,
                recorded_actor=self.player.id,
                from_room=self.room_id,
            )
        )


# ------------------------------------------------------------ the household's


def _search(use: _Use) -> None:
    world, room_id = use.world, use.room_id
    hidden = sorted(
        thing_id
        for thing_id, placement in world.placements.items()
        if (
            isinstance(placement, InRoom)
            and placement.room_id == room_id
            and placement.hidden
        )
        or (isinstance(placement, InContainer) and room_of(world, thing_id) == room_id)
    )
    world.searched.setdefault(use.player.id, [])
    if room_id not in world.searched[use.player.id]:
        world.searched[use.player.id].append(room_id)
    found = listing([thing(world, thing_id).name for thing_id in hidden])
    burned = ashes(world.ledger, room_id)
    own = (
        f"I searched the {use.room_name} and found: {found}."
        if hidden
        else f"I searched the {use.room_name} and found nothing hidden."
    )
    if burned:
        own += " There is fresh ash in the grate: something was burned here."
    perception = use.seen(
        kind="search",
        describe="{seen} searched the " + use.room_name + ".",
        own_text=own,
        silent=False,
    )
    use.record(
        perception,
        type="search",
        text=f"{use.player.name} searched the {use.room_name}.",
        payload={"found": str(len(hidden)), "ash": str(len(burned))},
    )


def _inspect(use: _Use, step: Inspect) -> None:
    item = use.reachable(step.thing_id)
    if item is None:
        return
    world = use.world
    world.inspected.setdefault(use.player.id, [])
    if item.id not in world.inspected[use.player.id]:
        world.inspected[use.player.id].append(item.id)
    marks: list[str] = []
    seen: list[str] = []
    if item.kind == "glass":
        state = glass_state(world.ledger, item.id)
        if state == "residue":
            marks.append("residue")
            seen.append("There is a bitter residue in the bottom of it.")
        elif state == "rinsed":
            marks.append("rinsed")
            seen.append(
                "It has been rinsed since dinner, though the other glasses have not."
            )
    written = writings(world.ledger, item.id)
    if written and item.kind == "paper":
        marks.append("writing")
        seen.append("There is writing on it.")
        if any(writing.seal for writing in written):
            marks.append("seal")
            seen.append("It is sealed with wax.")
    own = " ".join([f"I examined the {item.name} and traced where it had been.", *seen])
    perception = use.seen(
        kind="inspect",
        describe="{seen} examined the " + item.name + " closely.",
        own_text=own,
    )
    use.record(
        perception,
        type="inspect",
        text=f"{use.player.name} examined the {item.name}.",
        payload={"thing_id": item.id, "marks": ",".join(marks)},
    )


def _read(use: _Use, step: Read) -> None:
    item = use.reachable(step.thing_id)
    if item is None or not use.lit("read"):
        return
    if item.kind in LOG_KINDS:
        _read_log(use, item.id, None, step.subject)
        return
    if item.kind == "letters":
        _read_letters(use, item, step.subject)
        return
    world = use.world
    written = writings(world.ledger, item.id)
    printed = item.properties.get("text") or item.properties.get("stencil")
    channel: Channel = "book" if item.kind == "book" else "note"
    author: str | None = None
    if item.kind == "paper":
        if not written:
            _plain_read(use, item, f"I looked at the {item.name}: it is blank.")
            return
        latest = written[-1]
        quote = latest.words
        if latest.signed == STEWARD:
            own = "I read a note under the Steward's seal:"
        elif latest.signed in world.players:
            author = latest.signed
            own = f"I read a note signed {use.player_name(latest.signed)}:"
        else:
            own = "I read an unsigned note:"
        shown = [latest]
    else:
        parts = [printed] if printed else []
        if written:
            where = (
                "In the margin, in ink:" if item.kind == "book" else "Added by hand:"
            )
            parts.append(f"{where} " + " / ".join(w.words for w in written if w.words))
        if not parts:
            _plain_read(
                use, item, f"I looked over the {item.name}: nothing is written."
            )
            return
        quote = " ".join(parts)
        own = f"I read the {item.name}:"
        shown = list(written)
    perception = use.seen(
        kind="read",
        describe="{seen} read the " + item.name + ".",
        own_text=own,
    )
    use.words(perception, text=own, channel=channel, quote=quote, author=author)
    payload = {"thing_id": item.id}
    if shown:
        payload["writings"] = ",".join(str(writing.seq) for writing in shown)
    use.record(
        perception,
        type="read",
        text=f"{use.player.name} read the {item.name}.",
        payload=payload,
        quote=quote,
    )


def _plain_read(use: _Use, item: Thing, own: str) -> None:
    perception = use.seen(
        kind="read",
        describe="{seen} read the " + item.name + ".",
        own_text=own,
    )
    use.record(
        perception,
        type="read",
        text=f"{use.player.name} read the {item.name}.",
        payload={"thing_id": item.id},
    )


def _read_letters(use: _Use, item: Thing, subject: str | None) -> None:
    world = use.world
    if subject is None or subject not in world.players:
        use.refuse("the letters are filed by resident; name whose to read")
        return
    whose = use.player_name(subject)
    secret = world.players[subject].secret
    numbers = world.balance.tools.read
    found = secret is not None and use.attempt.roll("secret", numbers.secret, subject)
    if found and secret is not None:
        own = f"I read {whose}'s letters:"
        perception = use.seen(
            kind="read",
            describe="{seen} read through the " + item.name + ".",
            own_text=own,
        )
        use.words(perception, text=own, channel="note", quote=secret, author=subject)
        use.record(
            perception,
            type="read",
            text=f"{use.player.name} read {whose}'s letters.",
            payload={"thing_id": item.id, "secret_of": subject, "found": "true"},
            quote=secret,
        )
        return
    perception = use.seen(
        kind="read",
        describe="{seen} read through the " + item.name + ".",
        own_text=f"I read {whose}'s letters and found nothing they would mind my "
        "knowing.",
    )
    use.record(
        perception,
        type="read",
        text=f"{use.player.name} read {whose}'s letters.",
        payload={"thing_id": item.id, "secret_of": subject, "found": "false"},
    )


def _read_log(use: _Use, thing_id: str, day: int | None, subject: str | None) -> None:
    item = use.reachable(thing_id)
    if item is None or not use.lit("read"):
        return
    world = use.world
    if item.kind not in LOG_KINDS:
        use.refuse(f"the {item.name} is not a log")
        return
    which = day if day is not None else world.day
    if which > world.day:
        use.refuse("that day has not happened yet")
        return
    if subject is not None and subject not in world.players:
        use.refuse("there is nobody by that name in the castle")
        return
    text, lines = render_log(
        world,
        item.id,
        which,
        subject=subject,
        limit=world.balance.tools.read_log.lines,
    )
    own = f"I read the {item.name} for day {which}:"
    perception = use.seen(
        kind="read",
        describe="{seen} read the " + item.name + ".",
        own_text=own,
    )
    use.words(perception, text=own, channel="log", quote=text, author=None)
    payload = {
        "thing_id": item.id,
        "day": str(which),
        "lines": str(len(lines)),
        "flags": str(sum(1 for line in lines if line.flag)),
    }
    if subject is not None:
        payload["subject"] = subject
    use.record(
        perception,
        type="read",
        text=f"{use.player.name} read the {item.name}.",
        payload=payload,
        quote=text,
    )


def _write(use: _Use, step: Write) -> None:
    item = use.reachable(step.thing_id)
    if item is None or not use.lit("write"):
        return
    world = use.world
    if item.kind == "paper":
        if use.held(item.id) is None:
            return
        if writings(world.ledger, item.id):
            use.refuse("the sheet is already written on")
            return
        if not step.text:
            use.refuse("there is nothing to write")
            return
        _put_words(
            use,
            item,
            step.text,
            signed=use.player.id,
            own=f"I wrote a note on the {item.name}, signed in my own name:",
        )
        return
    if item.kind not in ("book", "guest_book", "cargo"):
        use.refuse(f"there is nowhere to write on the {item.name}")
        return
    if item.kind == "guest_book":
        signed: str | None = use.player.id
        own = (
            f"I signed the {item.name}:" if step.text else f"I signed the {item.name}."
        )
    else:
        if not step.text:
            use.refuse("there is nothing to write")
            return
        signed = None
        own = f"I wrote in the {item.name}:"
    _put_words(use, item, step.text or None, signed=signed, own=own)


def _put_words(
    use: _Use,
    item: Thing,
    words: str | None,
    *,
    signed: str | None,
    own: str,
    seal: str = "",
    tell: bool = False,
    intent: Intent | None = None,
) -> None:
    """Writing of any kind: the room sees someone writing, and only the writer
    has the words."""
    on = {"book": "in the", "guest_book": "in the", "paper": "on a"}.get(
        item.kind, "on the"
    )
    channel: Channel = "book" if item.kind == "book" else "note"
    perception = use.seen(
        kind="write",
        describe="{seen} wrote something " + f"{on} {item.name}.",
        own_text=own,
    )
    if words is not None:
        use.words(
            perception, text=own, channel=channel, quote=words, author=use.player.id
        )
    use.record(
        perception,
        type="write",
        text=f"{use.player.name} wrote {on} {item.name}.",
        payload={
            "thing_id": item.id,
            "signed": signed or "",
            "seal": seal,
            "tell": "true" if tell else "false",
        },
        quote=words,
        intent=intent,
    )


def _relay(use: _Use, step: Relay) -> None:
    world = use.world
    heard = entry_at(world.ledger, step.rumor)
    record = heard.record_for(use.player.id) if heard is not None else None
    origin = rumor_origin(heard) if heard is not None else None
    if (
        heard is None
        or record is None
        or origin is None
        or heard.payload.get("to") != use.player.id
        or record.quote is None
    ):
        use.refuse("you were told no such rumour")
        return
    _whisper_like(
        use,
        step.to,
        record.quote,
        truth=f"{use.player.name} passed on a rumour to {{listener}}:",
        own="I passed on a rumour to {listener}:",
        heard=f"{use.player.name} passed on a rumour:",
        payload={
            "rumor": str(origin),
            "hop": str(int(heard.payload.get("hop", "0")) + 1),
            "source": str(heard.seq),
        },
    )


def _compare_hands(use: _Use, step: CompareHands) -> None:
    item = use.reachable(step.thing_id)
    if item is None or not use.lit("compare handwriting"):
        return
    letters = use.fixture("letters")
    if letters is None:
        use.refuse("there are no letters here to compare against")
        return
    world = use.world
    written = [w for w in writings(world.ledger, item.id) if w.words][-3:]
    if not written:
        _finding(
            use,
            item,
            own=f"I compared the {item.name} against the {letters.name}: there is "
            "no handwriting on it.",
            describe="{seen} compared papers at the " + letters.name + ".",
            payload={"thing_id": item.id},
        )
        return
    findings: list[str] = []
    named: list[str] = []
    exposed = False
    for writing in written:
        conclusive = use.attempt.roll(
            "success", use.attempt.numbers.success, f"{item.id}:{writing.seq}"
        )
        verdict, who = _hand_verdict(world, writing, conclusive)
        findings.append(verdict)
        named.append(who or "-")
        exposed = exposed or (
            who == writing.hand and writing.signed not in (None, writing.hand)
        )
    own = f"I compared the {item.name} against the {letters.name}: " + " ".join(
        findings
    )
    _finding(
        use,
        item,
        own=own,
        describe="{seen} compared papers at the " + letters.name + ".",
        payload={
            "thing_id": item.id,
            "named": ",".join(named),
            "exposed": "true" if exposed else "false",
        },
    )


def _hand_verdict(
    world: WorldState, writing: Writing, conclusive: bool
) -> tuple[str, str | None]:
    """What a comparison says of one piece of writing, and whose hand it names."""
    if not conclusive:
        return "The comparison is inconclusive.", None
    hand = world.players[writing.hand].name
    signed = writing.signed
    if signed in world.players and signed != writing.hand:
        claimed = world.players[signed].name
        if writing.tell:
            return (
                f"The hand imitates {claimed}'s, but the letter forms are {hand}'s.",
                writing.hand,
            )
        return f"The hand matches {claimed}'s letters.", signed
    if signed == STEWARD:
        return f"The hand is not the Steward's clerk's: it is {hand}'s.", writing.hand
    return f"The hand matches {hand}'s letters.", writing.hand


def _check_register(use: _Use, step: CheckRegister) -> None:
    item = use.reachable(step.thing_id)
    if item is None or not use.lit("read"):
        return
    register = use.fixture("register")
    if register is None:
        use.refuse("the Steward's register is not here")
        return
    written = [w for w in writings(use.world.ledger, item.id) if w.words]
    if not written:
        _finding(
            use,
            item,
            own=f"I looked for the {item.name} in the {register.name}: there is "
            "nothing on it to look up.",
            describe="{seen} consulted the " + register.name + ".",
            payload={"thing_id": item.id},
        )
        return
    day = registered(use.world.ledger, written[-1].words)
    verdict = (
        f"the register lists it, a decree of day {day}."
        if day is not None
        else "the register lists no such decree."
    )
    _finding(
        use,
        item,
        own=f"I looked for the {item.name} in the {register.name}: {verdict}",
        describe="{seen} consulted the " + register.name + ".",
        payload={"thing_id": item.id, "listed": "true" if day else "false"},
    )


def _inspect_seal(use: _Use, step: InspectSeal) -> None:
    item = use.reachable(step.thing_id)
    if item is None or not use.lit("examine a seal"):
        return
    sealed = [w for w in writings(use.world.ledger, item.id) if w.seal is not None]
    seal = find_thing_of_kind(use.world, "seal")
    device = seal.properties.get("device", "device") if seal is not None else "device"
    if not sealed:
        verdict, payload = f"There is no seal on the {item.name}.", {"seal": "none"}
    elif sealed[-1].seal == "real":
        verdict = f"The wax bears the Steward's own {device}."
        payload = {"seal": "real"}
    elif use.attempt.roll("success", use.attempt.numbers.success, item.id):
        verdict = f"The seal is a crude imitation of the Steward's {device}."
        payload = {"seal": "crude", "detected": "true"}
    else:
        verdict = f"The wax bears what looks like the Steward's {device}."
        payload = {"seal": "crude", "detected": "false"}
    _finding(
        use,
        item,
        own=f"I examined the seal on the {item.name}. {verdict}",
        describe="{seen} examined the " + item.name + " closely.",
        payload={"thing_id": item.id, **payload},
        kind="inspect",
    )


def _finding(
    use: _Use,
    item: Thing,
    *,
    own: str,
    describe: str,
    payload: dict[str, str],
    kind: str = "read",
) -> None:
    """An investigation whose result only its user learns."""
    perception = use.seen(kind=kind, describe=describe, own_text=own)
    use.record(
        perception,
        type=kind,
        text=f"{use.player.name} examined the {item.name}.",
        payload=payload,
    )


def _search_person(use: _Use, step: SearchPerson) -> None:
    if not use.within_reach_of(step.to):
        return
    world = use.world
    target = world.players[step.to]
    found = carried_things(world, target.id)
    names = listing([thing(world, thing_id).name for thing_id in found])
    own = (
        f"I searched {target.name} and found: {names}."
        if found
        else f"I searched {target.name} and found nothing."
    )
    label = seen_as(world, target.id, use.lighting)
    perception = use.seen(
        kind="search",
        describe="{seen} searched " + label + ".",
        own_text=own,
        silent=False,
    )
    perception.put(
        WitnessRecord(
            player_id=target.id,
            sense="saw",
            confidence="clear",
            channel="direct",
            text=f"{use.player.name} searched me.",
            recorded_actor=use.player.id,
            from_room=use.room_id,
            noted=True,
        )
    )
    use.record(
        perception,
        type="search_person",
        text=f"{use.player.name} searched {target.name}.",
        payload={"to": target.id, "found": ",".join(found)},
    )


# ----------------------------------------------------------------- the traitors'


def _blank_sheet(use: _Use, thing_id: str) -> Thing | None:
    item = use.held(thing_id)
    if item is None or not use.lit("write"):
        return None
    if item.kind != "paper":
        use.refuse(f"the {item.name} is not something to write a note on")
        return None
    if writings(use.world.ledger, item.id):
        use.refuse("the sheet is already written on")
        return None
    return item


def _forge(use: _Use, step: Forge) -> None:
    item = _blank_sheet(use, step.thing_id)
    if item is None or not use.intent_fits(step.intent):
        return
    world = use.world
    if step.hand == use.player.id or step.hand not in world.players:
        use.refuse("there is nobody by that name whose hand to imitate")
        return
    tell = use.attempt.roll("trace", use.attempt.numbers.trace, item.id)
    _put_words(
        use,
        item,
        step.text,
        signed=step.hand,
        own=f"I wrote a note in {use.player_name(step.hand)}'s hand, signed as them:",
        tell=tell,
        intent=step.intent,
    )


def _counterfeit(use: _Use, step: Counterfeit) -> None:
    item = _blank_sheet(use, step.thing_id)
    if item is None or not use.intent_fits(step.intent):
        return
    real = any(
        thing(use.world, thing_id).kind == "seal"
        for thing_id in carried_things(use.world, use.player.id)
    )
    pressed = "the Steward's own seal" if real else "a crude seal of my own making"
    _put_words(
        use,
        item,
        step.text,
        signed=STEWARD,
        own=f"I wrote a decree in the Steward's style and pressed {pressed} into "
        "the wax:",
        seal="real" if real else "crude",
        intent=step.intent,
    )


def _honeypot(use: _Use, step: Honeypot) -> None:
    item = use.reachable(step.thing_id)
    if item is None or not use.lit("write") or not use.intent_fits(step.intent):
        return
    if item.kind == "paper":
        if _blank_sheet(use, item.id) is None:
            return
    elif item.kind not in ("book", "guest_book", "cargo"):
        use.refuse(f"there is nowhere to write on the {item.name}")
        return
    _put_words(
        use,
        item,
        step.text,
        signed=None,
        own=f"I wrote in the {item.name}, unsigned:",
        intent=step.intent,
    )


def _plant(use: _Use, step: Plant) -> None:
    item = use.held(step.thing_id)
    if item is None or not use.within_reach_of(step.to):
        return
    if not use.intent_fits(step.intent):
        return
    world = use.world
    target = world.players[step.to]
    slipped = use.attempt.roll("success", use.attempt.numbers.success, step.to)
    if slipped:
        placement = HeldBy(player_id=target.id)
        perception = use.seen(
            kind="plant",
            describe="{seen} moved close.",
            own_text=f"I slipped the {item.name} into {target.name}'s coat.",
            private=True,
        )
        use.record(
            perception,
            type="plant",
            text=f"The {item.name} turned up in {target.name}'s coat.",
            payload={**placement_payload(item.id, placement), "to": target.id},
            intent=step.intent,
        )
        move_object(world, item.id, placement)
        return
    perception = use.seen(
        kind="plant",
        describe="{seen} moved close.",
        own_text=f"{target.name} caught my hand; I still have the {item.name}.",
        private=True,
    )
    perception.put(_caught(use, target.id, "tried to slip something into my coat."))
    use.record(
        perception,
        type="plant_caught",
        text=f"{use.player.name} tried to slip the {item.name} into "
        f"{target.name}'s coat, and was caught.",
        payload={"to": target.id, "item": item.id, "caught": "true"},
        intent=step.intent,
    )


def _steal(use: _Use, step: Steal) -> None:
    if not use.within_reach_of(step.to):
        return
    world = use.world
    target = world.players[step.to]
    unnoticed = use.attempt.roll("success", use.attempt.numbers.success, step.to)
    if not unnoticed:
        perception = use.seen(
            kind="steal",
            describe="{seen} moved close.",
            own_text=f"{target.name} caught my hand in their pocket.",
            private=True,
        )
        perception.put(_caught(use, target.id, "tried to pick my pocket."))
        use.record(
            perception,
            type="pickpocket",
            text=f"{use.player.name} tried to pick {target.name}'s pocket, and was "
            "caught.",
            payload={"to": target.id, "caught": "true"},
        )
        return
    shield = next(
        (
            thing_id
            for thing_id in carried_things(world, target.id)
            if thing(world, thing_id).kind == SHIELD
        ),
        None,
    )
    if shield is None:
        perception = use.seen(
            kind="steal",
            describe="{seen} moved close.",
            own_text=f"I went through {target.name}'s pockets: no shield.",
            private=True,
        )
        use.record(
            perception,
            type="pickpocket",
            text=f"{use.player.name} went through {target.name}'s pockets and found "
            "no shield.",
            payload={"to": target.id, "found": "none"},
        )
        return
    placement = HeldBy(player_id=use.player.id)
    perception = use.seen(
        kind="steal",
        describe="{seen} moved close.",
        own_text=f"I lifted a shield token from {target.name}'s pocket.",
        private=True,
    )
    use.record(
        perception,
        type="steal",
        text=f"{target.name}'s shield token left their keeping.",
        payload={**placement_payload(shield, placement), "to": target.id},
    )
    move_object(world, shield, placement)


def _caught(use: _Use, target_id: str, what: str) -> WitnessRecord:
    """The record of someone who caught a hand: close enough to know whose."""
    return WitnessRecord(
        player_id=target_id,
        sense="saw",
        confidence="clear",
        channel="direct",
        text=f"{use.player.name} {what}",
        recorded_actor=use.player.id,
        from_room=use.room_id,
        noted=True,
    )


def _tamper(use: _Use, step: Tamper) -> None:
    item = use.reachable(step.thing_id)
    if item is None or not use.lit("make out the lines"):
        return
    if not use.intent_fits(step.intent):
        return
    world = use.world
    if item.kind not in LOG_KINDS:
        use.refuse(f"the {item.name} is not a log")
        return
    names = {step.subject, *([step.replace_with] if step.replace_with else [])}
    if not names <= set(world.players):
        use.refuse("there is nobody by that name in the castle")
        return
    if step.edit == "backdate":
        if item.kind != "guest_book":
            use.refuse("only a signature can be backdated")
            return
        if step.tick is None or step.tick > world.tick:
            use.refuse("a line cannot be dated in the future")
            return
    flagged = use.attempt.roll("trace", use.attempt.numbers.trace, item.id)
    subject = use.player_name(step.subject)
    if step.edit == "erase":
        did = f"I scraped {subject}'s lines from today's {item.name}."
    elif step.edit == "rename":
        other = use.player_name(step.replace_with or "")
        did = f"I altered {subject}'s lines in today's {item.name} to read {other}."
    else:
        did = f"I wrote {subject} into the {item.name} at t{step.tick}."
    guest = item.kind == "guest_book"
    perception = use.seen(
        kind="write" if guest else "use",
        describe=(
            "{seen} wrote in the " + item.name + "."
            if guest
            else "{seen} handled the " + item.name + "."
        ),
        own_text=did,
    )
    payload = {
        "thing_id": item.id,
        "edit": step.edit,
        "subject": step.subject,
        "day": str(world.day),
        "flagged": "true" if flagged else "false",
    }
    if step.replace_with is not None:
        payload["replace_with"] = step.replace_with
    if step.tick is not None:
        payload["tick"] = str(step.tick)
    use.record(
        perception,
        type="write" if guest else "use",
        text=f"{use.player.name} tampered with the {item.name}: {step.edit} {subject}.",
        payload=payload,
        intent=step.intent,
    )


def _sabotage(use: _Use, step: Sabotage) -> None:
    item = use.reachable(step.thing_id)
    if item is None:
        return
    if not item.container or not isinstance(use.world.placements.get(item.id), InRoom):
        use.refuse(f"there is no work to spoil at the {item.name}")
        return
    fumble = use.attempt.roll("trace", use.attempt.numbers.trace, item.id)
    describe = (
        "{seen} fumbled at the " + item.name + "."
        if fumble
        else "{seen} used the " + item.name + "."
    )
    perception = use.seen(
        kind="use",
        describe=describe,
        own_text=f"I worked the {item.name}, badly on purpose.",
        silent=False,
    )
    if fumble:
        perception.note(
            *(
                record.player_id
                for record in perception.records
                if record.sense == "saw"
            )
        )
    use.record(
        perception,
        type="use",
        text=f"{use.player.name} sabotaged the {item.name}.",
        payload={"thing_id": item.id, "fumble": "true" if fumble else "false"},
    )


def _disguise(use: _Use, step: Disguise) -> None:
    world = use.world
    other = step.as_player
    if other == use.player.id or other not in world.players:
        use.refuse("there is nobody by that name to pass as")
        return
    garment = find_thing(world, step.thing_id)
    if garment is None or garment.kind != GARMENT:
        use.refuse("that is not a garment")
        return
    if garment.name != world.players[other].starting_garment:
        use.refuse(
            f"{use.player_name(other)} does not wear a {garment.name}; nobody "
            "would take you for them in it"
        )
        return
    manner = f"took on {use.player_name(other)}'s manner"
    current = worn_garment(world, use.player.id)
    if current is not None and current.id == garment.id:
        perception = use.seen(
            kind="disguise",
            describe="{seen} straightened up.",
            own_text=f"I {manner}.",
            private=True,
        )
        use.record(
            perception,
            type="disguise",
            text=f"{use.player.name} began passing as {use.player_name(other)}.",
            payload={"thing_id": garment.id, "as": other},
        )
        world.disguises[use.player.id] = other
        return
    if change_garment(
        world,
        use.player,
        use.room_id,
        garment.id,
        own_text=f"I changed into a {garment.name} and {manner}.",
        payload={"tool": use.tool, "as": other},
        noted=True,
    ):
        world.disguises[use.player.id] = other


def _clean_up(use: _Use, step: CleanUp) -> None:
    item = use.reachable(step.thing_id)
    if item is None:
        return
    world = use.world
    marker = use.attempt.roll("trace", use.attempt.numbers.trace, item.id)
    if item.kind == "paper":
        gone = Offstage()
        perception = use.seen(
            kind="clean",
            describe="{seen} burned a " + item.name + ".",
            own_text=f"I burned the {item.name}.",
        )
        use.record(
            perception,
            type="destroyed",
            text=f"A {item.name} was burned in the {use.room_name}.",
            payload={
                **placement_payload(item.id, gone),
                "marker": "true" if marker else "false",
            },
        )
        move_object(world, item.id, gone)
        return
    if item.kind == "glass":
        perception = use.seen(
            kind="clean",
            describe="{seen} rinsed a " + item.name + ".",
            own_text=f"I rinsed the {item.name}.",
        )
        use.record(
            perception,
            type="clean",
            text=f"{use.player.name} rinsed the {item.name}.",
            payload={"thing_id": item.id, "marker": "true" if marker else "false"},
        )
        return
    use.refuse(f"there is nothing on the {item.name} to clean away")


# ------------------------------------------------------- whispered manipulations


def _rumor(use: _Use, step: Rumor) -> None:
    if not use.intent_fits(step.intent):
        return
    _whisper_like(
        use,
        step.to,
        step.text,
        truth=f"{use.player.name} started a rumour with {{listener}}:",
        own="I started a rumour with {listener}:",
        heard=f"{use.player.name} passed on a rumour:",
        payload={"hop": "0"},
        intent=step.intent,
    )


def _hook(use: _Use, step: Hook) -> None:
    if not use.intent_fits(step.intent):
        return
    _whisper_like(
        use,
        step.to,
        step.text,
        truth=f"{use.player.name} whispered a hook to {{listener}}:",
        own="I whispered to {listener}:",
        heard=f"{use.player.name} whispered to me:",
        intent=step.intent,
    )


def _gaslight(use: _Use, step: Gaslight) -> None:
    if not use.intent_fits(step.intent):
        return
    world = use.world
    if step.room_id not in {room.id for room in world.map.rooms}:
        use.refuse("there is no such room to remember")
        return
    _whisper_like(
        use,
        step.to,
        step.text,
        truth=f"{use.player.name} planted a false memory in {{listener}}:",
        own="I whispered to {listener}:",
        heard=f"{use.player.name} whispered to me:",
        payload={
            "room": step.room_id,
            "day": str(step.day),
            "phase": step.phase,
        },
        intent=step.intent,
    )


def _blackmail(use: _Use, step: Blackmail) -> None:
    if not use.intent_fits(step.intent):
        return
    if step.to not in secrets_known(use.world, use.player.id):
        use.refuse("you know nothing about them to threaten them with")
        return
    _whisper_like(
        use,
        step.to,
        step.text,
        truth=f"{use.player.name} threatened {{listener}}:",
        own="I whispered a threat to {listener}:",
        heard=f"{use.player.name} whispered to me:",
        payload={"secret_of": step.to},
        intent=step.intent,
    )


def _whisper_like(
    use: _Use,
    to: str,
    words: str,
    *,
    truth: str,
    own: str,
    heard: str,
    payload: dict[str, str] | None = None,
    intent: Intent | None = None,
) -> None:
    """Words at one person's ear, exactly as a whisper reaches them: only they
    catch the words, they know whose they are even in the dark, and out of bright
    light the words go ahead whether or not anyone is there."""
    world = use.world
    if to == use.player.id or to not in world.players:
        use.refuse("there is nobody by that name to whisper to")
        return
    reached = to in awake_in(world, use.room_id)
    if not reached and use.lighting == "bright":
        use.refuse("that person is not here to whisper to")
        return
    listener = world.players[to]
    witnesses = [
        WitnessRecord(
            player_id=use.player.id,
            sense="own",
            confidence="clear",
            channel="whisper",
            text=own.format(listener=listener.name),
            quote=words,
            author=use.player.id,
            recorded_actor=use.player.id,
            from_room=use.room_id,
            noted=True,
        )
    ]
    if reached:
        witnesses.append(
            WitnessRecord(
                player_id=to,
                sense="saw",
                confidence="clear",
                channel="whisper",
                text=heard,
                quote=words,
                author=use.player.id,
                recorded_actor=use.player.id,
                from_room=use.room_id,
                noted=True,
            )
        )
    append(
        world,
        type="whisper",
        text=truth.format(listener=listener.name),
        actor_id=use.player.id,
        room_id=use.room_id,
        quote=words,
        payload={
            "tool": use.tool,
            "to": to,
            "words": str(len(words.split())),
            "reached": "true" if reached else "false",
            **(payload or {}),
        },
        private={"intent": intent.render()} if intent is not None else None,
        witnesses=witnesses,
        rolls=use.attempt.rolls,
    )


def listing(names: list[str], room: int = 300) -> str:
    """Objects found, named once each with a count for repeats, cut short to fit
    a record."""
    counts: dict[str, int] = {}
    for item in names:
        counts[item] = counts.get(item, 0) + 1
    parts = [item if n == 1 else f"{item} (x{n})" for item, n in counts.items()]
    text = ", ".join(parts)
    while len(text) > room and len(parts) > 1:
        parts = parts[:-1]
        text = ", ".join(parts) + " and more"
    return text[:room]


def find_thing_of_kind(world: WorldState, kind: str) -> Thing | None:
    return next((item for item in world.map.things if item.kind == kind), None)


__all__ = ["find_thing_of_kind", "listing", "use_tool"]
