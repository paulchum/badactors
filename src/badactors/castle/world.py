"""Pure world transitions: one tick, one phase boundary.

Nothing here calls a model, touches IO or reads the clock. Every roll comes from
the seeded cursor in the state, and players are always resolved in ``state.seats``
order, so the same state plus the same plans gives the same ledger.

Night is a phase like any other here. Sending the traitors to the conclave and the
Faithful to their chambers needs roles, so the game layer does it; the world owes
it sleep, which is role-blind: a sleeper takes no steps and records nothing.

Two physical rules are worth stating because they generate most of the evidence.
A move commits on arrival: a player who has stepped into a passage is in neither
room until the travel ticks run out, so a long corridor is time nobody can account
for. And when a phase ends, the remaining steps of a plan are cut — that cut is a
ledger entry — but a move already underway finishes, because a person in a passage
has to come out somewhere. Nobody is ever teleported to make a gathering start
tidily, which is exactly why arriving late is evidence.
"""

from __future__ import annotations

from collections.abc import Mapping

from badactors.castle.acts import (
    blocked,
    change_garment,
    on_person,
    own_record,
    reach,
    thing,
    wearable,
)
from badactors.castle.balance import ToolName
from badactors.castle.contracts import (
    DAY_ORDER,
    GARMENT,
    GATHERINGS,
    MAX_PLAN_STEPS,
    AtRoom,
    CastlePhase,
    Drop,
    HeldBy,
    InContainer,
    InRoom,
    InTransit,
    Move,
    Plan,
    PlayerState,
    Step,
    Take,
    Talk,
    ToolUse,
    Use,
    Wear,
    Whisper,
    WitnessRecord,
    WorldState,
)
from badactors.castle.kit import refusal
from badactors.castle.ledger import (
    append,
    append_perceived,
    move_object,
    placement_payload,
    seed_placements,
)
from badactors.castle.papers import LOG_KINDS, READABLE_KINDS, rumor_origin
from badactors.castle.perceive import (
    awake_in,
    occupants,
    perceive,
    unnamed,
    visible_things,
    worn_garment,
)
from badactors.castle.tools import todays_station, use_tool

_FORKED = frozenset(
    {
        "players",
        "placements",
        "ledger",
        "inspected",
        "searched",
        "replans",
        "kits",
        "disguises",
        "notebook",
    }
)
"""WorldState fields a transition may change in place, so :func:`fork` copies
them. Every other field is a scalar or is never changed once the world starts."""


def fork(state: WorldState) -> WorldState:
    """A copy of ``state`` that a transition may change without touching the
    original.

    Everything a transition changes in place gets a fresh container; what is
    never changed after it is made — the map, placements, positions, steps and
    every ledger entry, which is frozen — is shared. That keeps a tick's cost
    independent of how long the game has run.
    """
    return state.model_copy(
        update={
            "players": {
                player_id: player.model_copy(
                    update={
                        "plan": player.plan.model_copy(
                            update={"steps": list(player.plan.steps)}
                        )
                    }
                )
                for player_id, player in state.players.items()
            },
            "placements": dict(state.placements),
            "ledger": list(state.ledger),
            "inspected": {key: dict(value) for key, value in state.inspected.items()},
            "searched": {key: dict(value) for key, value in state.searched.items()},
            "replans": dict(state.replans),
            "kits": dict(state.kits),
            "disguises": dict(state.disguises),
            "notebook": {key: list(value) for key, value in state.notebook.items()},
        }
    )


def start(state: WorldState) -> WorldState:
    """Open the world: record the authored custody and the first phase."""
    world = fork(state)
    seed_placements(world)
    append(
        world,
        type="phase_opened",
        text=f"Day {world.day}: {_phase_label(world.phase)} begins.",
        payload={"phase": world.phase, "day": str(world.day)},
        witnesses=[],
    )
    if world.phase in GATHERINGS:
        _summon(world)
    return world


def submit_plan(
    state: WorldState,
    player_id: str,
    plan: Plan,
    *,
    private: Mapping[str, str] | None = None,
) -> WorldState:
    """Attach a plan to a player and record that they formed one.

    ``private`` adds to what only the audience sees, such as a diary line.
    """
    world = fork(state)
    player = world.players[player_id]
    if not player.alive or player.asleep:
        return world
    player.plan = plan.model_copy(update={"steps": list(plan.steps)})
    meant = {"goal": plan.goal} if plan.goal else {}
    meant.update(private or {})
    append(
        world,
        type="plan_formed",
        text=f"{player.name} made a plan.",
        actor_id=player_id,
        room_id=player.room_id,
        payload={"steps": str(len(plan.steps))},
        private=meant,
        witnesses=[
            own_record(player_id, player.room_id, f"I plan to {plan.goal or 'act'}.")
        ],
    )
    return world


def tick(state: WorldState) -> WorldState:
    """Advance one tick: every living, waking player takes at most one step."""
    world = fork(state)
    world.tick += 1
    world.phase_tick += 1
    for player_id in world.seats:
        player = world.players[player_id]
        if not player.alive or player.asleep:
            continue
        if isinstance(player.position, InTransit):
            _advance_transit(world, player)
            continue
        if player.plan.steps:
            step = player.plan.steps.pop(0)
            _execute(world, player, step)
    return world


def phase_complete(state: WorldState) -> bool:
    return state.phase_tick >= state.map.phase_ticks.ticks(state.phase)


def advance_phase(state: WorldState) -> WorldState:
    """Close the current phase, cut running plans, and open the next one."""
    world = fork(state)
    _cut_plans(world)
    index = DAY_ORDER.index(world.phase)
    if index + 1 < len(DAY_ORDER):
        world.phase = DAY_ORDER[index + 1]
    else:
        world.phase = DAY_ORDER[0]
        world.day += 1
        world.disguises = {}
    world.phase_tick = 0
    world.replans = {}
    append(
        world,
        type="phase_opened",
        text=f"Day {world.day}: {_phase_label(world.phase)} begins.",
        payload={"phase": world.phase, "day": str(world.day)},
        witnesses=[],
    )
    if world.phase in GATHERINGS:
        _summon(world)
    return world


def interrupted(state: WorldState, player_id: str, since_seq: int) -> bool:
    """Whether this player should be re-prompted, budget included."""
    if not would_interrupt(state, player_id, since_seq):
        return False
    return replans_left(state, player_id) > 0


def replans_left(state: WorldState, player_id: str) -> int:
    used = state.replans.get(player_id, 0)
    return max(0, state.map.max_replans_per_phase - used)


def would_interrupt(state: WorldState, player_id: str, since_seq: int) -> bool:
    """Whether something happened that a five-step plan could not have foreseen.

    Only events that change what a plan can achieve count: another person entering
    or leaving the room, a step of one's own that failed or was caught, an object
    changing hands in view, being spoken to, or being searched or caught at. Noise
    from next door does not, or a crowded room would burn the phase's model calls
    on nothing.
    """
    player = state.players[player_id]
    if not player.alive or player.asleep:
        return False
    if isinstance(player.position, InTransit):
        # Nothing can be done in a passage; the plan is made on arrival.
        return False
    if not player.plan.steps:
        return True
    for entry in state.ledger[since_seq:]:
        record = entry.record_for(player_id)
        if record is None:
            continue
        if entry.actor_id == player_id:
            if entry.type == "blocked" or entry.payload.get("caught") == "true":
                return True
            continue
        if record.sense != "saw":
            continue
        if entry.type in _INTERRUPTING:
            return True
        if entry.type in _ADDRESSED and entry.payload.get("to") == player_id:
            return True
    return False


def note_replan(state: WorldState, player_id: str) -> WorldState:
    world = fork(state)
    world.replans[player_id] = world.replans.get(player_id, 0) + 1
    return world


def note_replan_capped(state: WorldState, player_id: str) -> WorldState:
    """Record that the phase's decision budget, not the player, ended the acting.

    Without this the ledger would show a character standing still and give no
    reason, which reads exactly like a choice. It is not one.
    """
    world = fork(state)
    player = world.players[player_id]
    append(
        world,
        type="replan_capped",
        text=(
            f"{player.name} needed a new plan with no decisions left this "
            f"{_phase_label(world.phase)}."
        ),
        actor_id=player_id,
        room_id=player.room_id,
        payload={"used": str(world.replans.get(player_id, 0))},
        witnesses=[
            own_record(player_id, player.room_id, "I ran out of time to decide.")
        ],
    )
    return world


def valid_actions(state: WorldState, player_id: str) -> list[str]:
    """Step kinds this player could legally submit right now.

    Tools are listed by what they act on, the way the player could see it: a
    tool aimed at a person is listed once, without a name, whenever anyone else
    is awake in the room, so the list never says who is there. A tool is listed
    only while its kit, the part of the day and its limits allow it."""
    player = state.players[player_id]
    if not player.alive or player.asleep:
        return []
    room_id = player.room_id
    if room_id is None:
        return ["wait"]
    actions = ["wait", "talk"]
    company = any(other != player_id for other in awake_in(state, room_id))
    if company:
        actions.append("whisper")
    actions.extend(
        f"move:{neighbor}"
        for neighbor in state.map.neighbors(room_id)
        if not held_at_dinner(state, room_id, neighbor)
    )
    here = visible_things(state, player_id, room_id)
    things = {item.id: item for item in state.map.things}
    actions.extend(f"take:{thing_id}" for thing_id in here if things[thing_id].portable)
    actions.extend(f"use:{thing_id}" for thing_id in here)
    containers = [thing_id for thing_id, item in things.items() if item.container]
    open_here = [thing_id for thing_id in here if thing_id in containers]
    for thing_id, placement in sorted(state.placements.items()):
        if isinstance(placement, HeldBy) and placement.player_id == player_id:
            actions.append(f"drop:{thing_id}")
            actions.extend(f"drop:{thing_id}:into:{box}" for box in open_here)
            actions.append(f"use:{thing_id}")
    actions.extend(f"wear:{thing_id}" for thing_id in wearable(state, player_id))
    on_back = worn_garment(state, player_id)
    if on_back is not None:
        actions.append(f"drop:{on_back.id}")
        actions.extend(f"drop:{on_back.id}:into:{box}" for box in open_here)
    actions.extend(_tool_actions(state, player_id, room_id, company))
    return sorted(set(actions))


def _tool_actions(
    state: WorldState, player_id: str, room_id: str, company: bool
) -> list[str]:
    """The tools this player carries, and what each could be used on here."""
    things = {item.id: item for item in state.map.things}
    near = reach(state, player_id, room_id)
    held = [
        thing_id for thing_id in near if on_person(state, player_id, thing_id) == "held"
    ]
    lit = state.map.room(room_id).lighting != "dark"
    kinds = {things[thing_id].kind for thing_id in near}
    out = ["tool:search"]
    out.extend(f"tool:inspect:{thing_id}" for thing_id in near)

    def offer(tool: ToolName, targets: list[str] | None = None) -> None:
        if refusal(state, player_id, tool) is not None:
            return
        if targets is None:
            out.append(f"tool:{tool}")
        else:
            out.extend(f"tool:{tool}:{target}" for target in targets)

    readable = [
        thing_id for thing_id in near if things[thing_id].kind in READABLE_KINDS
    ]
    papers = [thing_id for thing_id in held if things[thing_id].kind == "paper"]
    writable = [
        thing_id
        for thing_id in near
        if things[thing_id].kind in ("book", "guest_book", "cargo")
    ]
    logs = [thing_id for thing_id in near if things[thing_id].kind in LOG_KINDS]
    if lit:
        offer("read", readable)
        offer("read_log", logs)
        offer("write", [*papers, *writable])
        offer("inspect_seal", [t for t in near if things[t].kind == "paper"])
        if "letters" in kinds:
            offer("compare_hands", [t for t in readable if things[t].kind != "letters"])
        if "register" in kinds:
            offer("check_register", [t for t in near if things[t].kind == "paper"])
        offer("forge", papers)
        offer("counterfeit", papers)
        offer("honeypot", [*papers, *writable])
        offer("tamper", logs)
    if company:
        offer("search_person")
        if _heard_rumours(state, player_id):
            offer("relay")
        for tool in ("rumor", "hook", "gaslight", "blackmail", "steal"):
            offer(tool)
        offer("plant", held)
    if state.phase == "mission":
        offer("sabotage", [t for t in near if t == todays_station(state)])
    garments = [t for t in near if things[t].kind == GARMENT]
    offer("disguise", garments)
    offer(
        "clean_up",
        [t for t in near if things[t].kind in ("paper", "glass")],
    )
    return out


def _heard_rumours(state: WorldState, player_id: str) -> bool:
    for seq in state.notebook.get(player_id, []):
        entry = state.ledger[seq - 1]
        if entry.type == "whisper" and entry.payload.get("to") == player_id:
            if rumor_origin(entry) is not None:
                return True
    return False


# ------------------------------------------------------------------- step engine

_INTERRUPTING: frozenset[str] = frozenset(
    {"move_arrive", "move_depart", "take", "drop", "stow", "wear", "death"}
)
_ADDRESSED: frozenset[str] = frozenset(
    {"talk", "whisper", "search_person", "plant_caught", "pickpocket"}
)
"""Acts that interrupt the player they were aimed at."""


def _execute(world: WorldState, player: PlayerState, step: Step) -> None:
    room_id = player.room_id
    if room_id is None:
        return
    if isinstance(step, Move):
        _do_move(world, player, room_id, step)
    elif isinstance(step, Take):
        _do_take(world, player, room_id, step)
    elif isinstance(step, Talk):
        _do_talk(world, player, room_id, step)
    elif isinstance(step, Whisper):
        _do_whisper(world, player, room_id, step)
    elif isinstance(step, ToolUse):
        use_tool(world, player, room_id, step)
    elif isinstance(step, Use):
        _do_use(world, player, room_id, step)
    elif isinstance(step, Drop) and step.into is not None:
        _do_stow(world, player, room_id, step.thing_id, step.into)
    elif isinstance(step, Drop):
        _do_drop(world, player, room_id, step.thing_id, hidden=step.hidden)
    elif isinstance(step, Wear):
        change_garment(world, player, room_id, step.thing_id)
    else:
        _do_wait(world, player, room_id, step.reason)


def _camera(world: WorldState, player: PlayerState, room_id: str) -> dict[str, str]:
    """What the camera in this room, if it has one, writes down of whoever passes
    its door: their name in bright light, their garment in dim."""
    room = world.map.room(room_id)
    if not room.camera or room.lighting == "dark":
        return {}
    if room.lighting == "bright":
        return {"camera": player.name, "camera_id": player.id}
    return {"camera": unnamed(world, player.id, room.lighting)}


def _do_move(
    world: WorldState,
    player: PlayerState,
    room_id: str,
    step: Move,
) -> None:
    travel = world.map.travel_ticks(room_id, step.room_id)
    if travel is None:
        blocked(world, player, room_id, "there is no passage that way")
        return
    if held_at_dinner(world, room_id, step.room_id):
        blocked(world, player, room_id, "nobody leaves dinner before the lamps go out")
        return
    destination = world.map.room(step.room_id)
    perception = perceive(
        world,
        kind="move_depart",
        actor_id=player.id,
        room_id=room_id,
        describe=lambda seen: f"{seen} left toward the {destination.name}.",
        own_text=f"I set out for the {destination.name}.",
    )
    append_perceived(
        world,
        perception,
        type="move_depart",
        text=f"{player.name} left the {world.map.room(room_id).name} "
        f"toward the {destination.name}.",
        actor_id=player.id,
        room_id=room_id,
        payload={
            "destination": step.room_id,
            "travel_ticks": str(travel),
            **_camera(world, player, room_id),
        },
    )
    # The tick that steps out is the first travel tick, so a one-tick archway is
    # crossed at once and only a longer passage leaves time unaccounted for.
    if travel <= 1:
        _arrive(world, player, room_id, step.room_id)
        return
    player.position = InTransit(
        origin=room_id,
        destination=step.room_id,
        ticks_remaining=travel - 1,
    )


def held_at_dinner(state: WorldState, origin: str, destination: str) -> bool:
    """Whether dinner keeps a player from taking this passage.

    From the dinner summons until the lamps go out the household is at table,
    and the only way anyone may walk is the next step of the quickest way to it.
    So everyone sets out on the night walk from the table or from the road to
    it, and nobody is awake in the chambers, or on the stair, to see the
    traitors go down.
    """
    if state.phase != "dinner":
        return False
    route = state.map.route(origin, state.map.gathering_room)
    return not route or route[0] != destination


def _advance_transit(world: WorldState, player: PlayerState) -> None:
    position = player.position
    if not isinstance(position, InTransit):
        return
    remaining = position.ticks_remaining - 1
    if remaining > 0:
        player.position = position.model_copy(update={"ticks_remaining": remaining})
        return
    _arrive(world, player, position.origin, position.destination)


def _arrive(
    world: WorldState,
    player: PlayerState,
    origin_id: str,
    destination_id: str,
) -> None:
    """Land in a room, or turn back at the door if it is already full.

    Capacity binds here rather than at departure, because nobody can tell from the
    far end of a passage whether a room has filled up while they walked. Turning
    back is witnessed in the room they return to, which is the point.
    """
    origin = world.map.room(origin_id)
    destination = world.map.room(destination_id)
    if len(occupants(world, destination_id)) >= destination.capacity:
        player.position = AtRoom(room_id=origin_id)
        blocked(
            world,
            player,
            origin_id,
            f"the {destination.name} was full and I turned back",
            witnessed=True,
            describe=lambda seen: f"{seen} came back from the {destination.name}.",
        )
        return
    player.position = AtRoom(room_id=destination_id)
    perception = perceive(
        world,
        kind="move_arrive",
        actor_id=player.id,
        room_id=destination_id,
        describe=lambda seen: f"{seen} arrived from the {origin.name}.",
        own_text=f"I arrived from the {origin.name}.",
    )
    append_perceived(
        world,
        perception,
        type="move_arrive",
        text=f"{player.name} arrived from the {origin.name}.",
        actor_id=player.id,
        room_id=destination_id,
        payload={"origin": origin_id, **_camera(world, player, destination_id)},
    )


def _do_take(
    world: WorldState,
    player: PlayerState,
    room_id: str,
    step: Take,
) -> None:
    if step.thing_id not in visible_things(world, player.id, room_id):
        blocked(world, player, room_id, "that is not within reach")
        return
    item = thing(world, step.thing_id)
    if not item.portable:
        blocked(world, player, room_id, f"the {item.name} cannot be carried")
        return
    origin = world.placements[step.thing_id]
    source = (
        f" from the {thing(world, origin.thing_id).name}"
        if isinstance(origin, InContainer)
        else ""
    )
    placement = HeldBy(player_id=player.id)
    perception = perceive(
        world,
        kind="take",
        actor_id=player.id,
        room_id=room_id,
        describe=lambda seen: f"{seen} took the {item.name}{source}.",
        own_text=f"I took the {item.name}{source}.",
    )
    append_perceived(
        world,
        perception,
        type="take",
        text=f"{player.name} took the {item.name}{source}.",
        actor_id=player.id,
        room_id=room_id,
        payload=placement_payload(step.thing_id, placement),
    )
    move_object(world, step.thing_id, placement)


def _do_drop(
    world: WorldState,
    player: PlayerState,
    room_id: str,
    thing_id: str,
    *,
    hidden: bool,
) -> None:
    on = on_person(world, player.id, thing_id)
    if on is None:
        blocked(world, player, room_id, "that is not in hand")
        return
    item = thing(world, thing_id)
    placement = InRoom(room_id=room_id, hidden=hidden)
    verb = "hid" if hidden else "set down"
    if on == "worn":
        verb = f"took off and {verb}"
        world.disguises.pop(player.id, None)
    perception = perceive(
        world,
        kind="drop",
        actor_id=player.id,
        room_id=room_id,
        describe=lambda seen: f"{seen} {verb} the {item.name}.",
        own_text=f"I {verb} the {item.name}.",
    )
    append_perceived(
        world,
        perception,
        type="drop",
        text=f"{player.name} {verb} the {item.name}.",
        actor_id=player.id,
        room_id=room_id,
        payload=placement_payload(thing_id, placement),
    )
    move_object(world, thing_id, placement)


def _do_stow(
    world: WorldState,
    player: PlayerState,
    room_id: str,
    thing_id: str,
    container_id: str,
) -> None:
    on = on_person(world, player.id, thing_id)
    if on is None:
        blocked(world, player, room_id, "that is not in hand")
        return
    box = next((item for item in world.map.things if item.id == container_id), None)
    if (
        box is None
        or not box.container
        or container_id not in visible_things(world, player.id, room_id)
    ):
        blocked(world, player, room_id, "there is nothing here to put it in")
        return
    item = thing(world, thing_id)
    placement = InContainer(thing_id=container_id)
    verb = "took off and put" if on == "worn" else "put"
    if on == "worn":
        world.disguises.pop(player.id, None)
    perception = perceive(
        world,
        kind="stow",
        actor_id=player.id,
        room_id=room_id,
        describe=lambda seen: f"{seen} {verb} the {item.name} in the {box.name}.",
        own_text=f"I {verb} the {item.name} in the {box.name}.",
    )
    append_perceived(
        world,
        perception,
        type="stow",
        text=f"{player.name} {verb} the {item.name} in the {box.name}.",
        actor_id=player.id,
        room_id=room_id,
        payload=placement_payload(thing_id, placement),
    )
    move_object(world, thing_id, placement)


def _do_talk(
    world: WorldState,
    player: PlayerState,
    room_id: str,
    step: Talk,
) -> None:
    listeners = [other for other in awake_in(world, room_id) if other != player.id]
    if not listeners:
        blocked(world, player, room_id, "there is nobody here to talk to")
        return
    if step.to == player.id or (
        step.to is not None
        and step.to not in listeners
        and world.map.room(room_id).lighting == "bright"
    ):
        blocked(world, player, room_id, "that person is not here")
        return
    # Out of bright light the speaker cannot tell who is here, so speaking to
    # someone absent goes ahead like any other remark: a refusal would tell the
    # speaker who is not in the room.
    perception = perceive(
        world,
        kind="talk",
        actor_id=player.id,
        room_id=room_id,
        describe=lambda seen: f"{seen} said:",
        own_text="I said:",
        channel="speech",
        quote=step.text,
        speaker_count=1,
    )
    append_perceived(
        world,
        perception,
        type="talk",
        text=f"{player.name} said:",
        actor_id=player.id,
        room_id=room_id,
        quote=step.text,
        payload={"to": step.to or "", "words": str(len(step.text.split()))},
    )


def _do_whisper(
    world: WorldState,
    player: PlayerState,
    room_id: str,
    step: Whisper,
) -> None:
    """Words at one person's ear. Only they catch the words, and they know whose
    they are even in the dark; nobody else in the room notices, and nothing
    carries through a wall. The listener notes every whisper, so a plain one and
    one that is a tool look the same in their notebook."""
    if step.to == player.id or step.to not in world.players:
        blocked(world, player, room_id, "there is nobody by that name to whisper to")
        return
    reached = step.to in awake_in(world, room_id)
    if not reached and world.map.room(room_id).lighting == "bright":
        blocked(world, player, room_id, "that person is not here to whisper to")
        return
    # Out of bright light the whisperer cannot see who is here. A whisper to
    # someone absent, or asleep, reaches nobody, and the whisperer's own memory
    # of it is the same as if it had: a refusal would say who is in the room.
    listener = world.players[step.to]
    witnesses = [
        WitnessRecord(
            player_id=player.id,
            sense="own",
            confidence="clear",
            channel="whisper",
            text=f"I whispered to {listener.name}:",
            quote=step.text,
            author=player.id,
            recorded_actor=player.id,
            from_room=room_id,
        )
    ]
    if reached:
        witnesses.append(
            WitnessRecord(
                player_id=step.to,
                sense="saw",
                confidence="clear",
                channel="whisper",
                text=f"{player.name} whispered to me:",
                quote=step.text,
                author=player.id,
                recorded_actor=player.id,
                from_room=room_id,
                noted=True,
            )
        )
    append(
        world,
        type="whisper",
        text=f"{player.name} whispered to {listener.name}:",
        actor_id=player.id,
        room_id=room_id,
        quote=step.text,
        payload={
            "to": step.to,
            "words": str(len(step.text.split())),
            "reached": "true" if reached else "false",
        },
        witnesses=witnesses,
    )


def _do_use(
    world: WorldState,
    player: PlayerState,
    room_id: str,
    step: Use,
) -> None:
    placement = world.placements.get(step.thing_id)
    holding = isinstance(placement, HeldBy) and placement.player_id == player.id
    if not holding and step.thing_id not in visible_things(world, player.id, room_id):
        blocked(world, player, room_id, "that is not within reach")
        return
    item = thing(world, step.thing_id)
    perception = perceive(
        world,
        kind="use",
        actor_id=player.id,
        room_id=room_id,
        describe=lambda seen: f"{seen} used the {item.name}.",
        own_text=f"I used the {item.name}.",
    )
    append_perceived(
        world,
        perception,
        type="use",
        text=f"{player.name} used the {item.name}.",
        actor_id=player.id,
        room_id=room_id,
        payload={"thing_id": step.thing_id},
    )


def _do_wait(
    world: WorldState,
    player: PlayerState,
    room_id: str,
    reason: str,
) -> None:
    perception = perceive(
        world,
        kind="wait",
        actor_id=player.id,
        room_id=room_id,
        describe=lambda seen: f"{seen} waited.",
        own_text=f"I waited: {reason}." if reason else "I waited.",
        silent=True,
    )
    append_perceived(
        world,
        perception,
        type="wait",
        text=f"{player.name} waited.",
        actor_id=player.id,
        room_id=room_id,
        private={"reason": reason} if reason else {},
    )


# --------------------------------------------------------------- phase machinery


def _cut_plans(world: WorldState) -> None:
    for player_id in world.seats:
        player = world.players[player_id]
        if not player.alive or not player.plan.steps:
            continue
        dropped = len(player.plan.steps)
        kinds = ",".join(step.kind for step in player.plan.steps)
        player.plan = Plan()
        append(
            world,
            type="plan_cut",
            text=(
                f"{player.name}'s plan was cut with {dropped} step(s) unfinished "
                f"when {_phase_label(world.phase)} ended."
            ),
            actor_id=player_id,
            room_id=player.room_id,
            payload={"dropped": str(dropped), "kinds": kinds},
            witnesses=[
                own_record(
                    player_id,
                    player.room_id,
                    f"{_phase_label(world.phase)} ended before I finished.",
                )
            ],
        )


def _summon(world: WorldState) -> None:
    """Call everyone to the gathering, routing each from where they stand."""
    target = world.map.gathering_room
    announcement = f"The household is called to the {world.map.room(target).name}."
    append(
        world,
        type="summoned",
        text="The Steward announced:",
        room_id=target,
        quote=announcement,
        payload={"room": target},
        witnesses=[
            _steward_record(player_id, world.players[player_id].room_id, announcement)
            for player_id in world.seats
            if world.players[player_id].alive
        ],
    )
    for player_id in world.seats:
        player = world.players[player_id]
        if not player.alive:
            continue
        player.plan = Plan(
            goal=f"answer the call to the {world.map.room(target).name}",
            steps=route_steps(world, player_id, target),
        )


def route_steps(state: WorldState, player_id: str, target: str) -> list[Step]:
    """Moves along the quickest way to ``target`` from wherever this player will
    next stand, as many as one plan can hold."""
    position = state.players[player_id].position
    origin = position.room_id if isinstance(position, AtRoom) else position.destination
    route = state.map.route(origin, target)
    return [Move(room_id=room_id) for room_id in route[:MAX_PLAN_STEPS]]


def fall_asleep(state: WorldState, player_id: str) -> WorldState:
    """Put a player to sleep where they stand. A sleeper takes no steps and
    records nothing until woken."""
    world = fork(state)
    player = world.players[player_id]
    if not player.alive or player.asleep or player.room_id is None:
        return world
    player.plan = Plan()
    player.asleep = True
    room = world.map.room(player.room_id)
    append(
        world,
        type="sleep",
        text=f"{player.name} fell asleep in the {room.name}.",
        actor_id=player_id,
        room_id=room.id,
        witnesses=[
            own_record(player_id, room.id, f"I fell asleep in the {room.name}.")
        ],
    )
    return world


def wake_up(state: WorldState, player_id: str) -> WorldState:
    world = fork(state)
    player = world.players[player_id]
    if not player.alive or not player.asleep:
        return world
    player.asleep = False
    room_id = player.room_id
    append(
        world,
        type="wake",
        text=f"{player.name} woke.",
        actor_id=player_id,
        room_id=room_id,
        witnesses=[own_record(player_id, room_id, "I woke.")],
    )
    return world


def _steward_record(
    player_id: str,
    room_id: str | None,
    announcement: str,
) -> WitnessRecord:
    """One player's copy of an official announcement. Only the host writes these."""
    return WitnessRecord(
        player_id=player_id,
        sense="own",
        confidence="clear",
        channel="steward",
        text="The Steward announced:",
        quote=announcement,
        from_room=room_id,
    )


def _phase_label(phase: CastlePhase) -> str:
    return phase.replace("_", " ")


__all__ = [
    "advance_phase",
    "fall_asleep",
    "fork",
    "held_at_dinner",
    "interrupted",
    "note_replan",
    "note_replan_capped",
    "phase_complete",
    "replans_left",
    "route_steps",
    "start",
    "submit_plan",
    "tick",
    "valid_actions",
    "wake_up",
    "would_interrupt",
]
