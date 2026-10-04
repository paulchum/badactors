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

from collections.abc import Callable, Mapping

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
    Thing,
    ToolStep,
    Use,
    Wear,
    Whisper,
    WitnessRecord,
    WorldState,
    WornBy,
)
from badactors.castle.ledger import (
    append,
    append_perceived,
    move_object,
    placement_payload,
    seed_placements,
)
from badactors.castle.perceive import (
    awake_in,
    occupants,
    perceive,
    room_of,
    visible_things,
    worn_garment,
)

_FORKED = frozenset(
    {"players", "placements", "ledger", "inspected", "searched", "replans"}
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
            "inspected": {key: list(value) for key, value in state.inspected.items()},
            "searched": {key: list(value) for key, value in state.searched.items()},
            "replans": dict(state.replans),
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
            _own_record(player_id, player.room_id, f"I plan to {plan.goal or 'act'}.")
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
    or leaving the room, a step of one's own that failed, an object changing hands
    in view, or being spoken to. Overheard noise from next door does not, or a
    crowded room would burn the phase's model calls on nothing.
    """
    player = state.players[player_id]
    if not player.alive or player.asleep:
        return False
    if not player.plan.steps:
        return True
    for entry in state.ledger[since_seq:]:
        record = entry.record_for(player_id)
        if record is None:
            continue
        if entry.actor_id == player_id:
            if entry.type == "blocked":
                return True
            continue
        if record.sense != "saw":
            continue
        if entry.type in _INTERRUPTING:
            return True
        if entry.type in {"talk", "whisper"} and entry.payload.get("to") == player_id:
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
            _own_record(player_id, player.room_id, "I ran out of time to decide.")
        ],
    )
    return world


def valid_actions(state: WorldState, player_id: str) -> list[str]:
    """Step kinds this player could legally submit right now."""
    player = state.players[player_id]
    if not player.alive or player.asleep:
        return []
    room_id = player.room_id
    if room_id is None:
        return ["wait"]
    actions = ["wait", "talk", "tool:search"]
    if any(other != player_id for other in awake_in(state, room_id)):
        actions.append("whisper")
    actions.extend(f"move:{neighbor}" for neighbor in state.map.neighbors(room_id))
    here = visible_things(state, player_id, room_id)
    portable = {thing.id for thing in state.map.things if not thing.container}
    actions.extend(f"take:{thing_id}" for thing_id in here if thing_id in portable)
    actions.extend(f"use:{thing_id}" for thing_id in here)
    actions.extend(f"tool:inspect:{thing_id}" for thing_id in here)
    containers = [thing.id for thing in state.map.things if thing.container]
    open_here = [thing_id for thing_id in here if thing_id in containers]
    for thing_id, placement in sorted(state.placements.items()):
        if isinstance(placement, HeldBy) and placement.player_id == player_id:
            actions.append(f"drop:{thing_id}")
            actions.extend(f"drop:{thing_id}:into:{box}" for box in open_here)
            actions.append(f"use:{thing_id}")
            actions.append(f"tool:inspect:{thing_id}")
    actions.extend(f"wear:{thing_id}" for thing_id in _wearable(state, player_id))
    on_back = worn_garment(state, player_id)
    if on_back is not None:
        actions.append(f"drop:{on_back.id}")
        actions.extend(f"drop:{on_back.id}:into:{box}" for box in open_here)
    return sorted(set(actions))


def _wearable(state: WorldState, player_id: str) -> list[str]:
    """Garments this player could put on: in hand, or lying within reach."""
    room_id = state.players[player_id].room_id
    reach = set(visible_things(state, player_id, room_id)) if room_id else set()
    out: list[str] = []
    for thing in state.map.things:
        if thing.kind != GARMENT:
            continue
        placement = state.placements.get(thing.id)
        held = isinstance(placement, HeldBy) and placement.player_id == player_id
        if held or (isinstance(placement, InRoom) and thing.id in reach):
            out.append(thing.id)
    return sorted(out)


# ------------------------------------------------------------------- step engine

_INTERRUPTING: frozenset[str] = frozenset(
    {"move_arrive", "move_depart", "take", "drop", "stow", "wear", "death"}
)


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
    elif isinstance(step, ToolStep):
        _do_tool(world, player, room_id, step)
    elif isinstance(step, Use):
        _do_use(world, player, room_id, step)
    elif isinstance(step, Drop) and step.into is not None:
        _do_stow(world, player, room_id, step.thing_id, step.into)
    elif isinstance(step, Drop):
        _do_drop(world, player, room_id, step.thing_id, hidden=step.hidden)
    elif isinstance(step, Wear):
        _do_wear(world, player, room_id, step.thing_id)
    else:
        _do_wait(world, player, room_id, step.reason)


def _do_move(
    world: WorldState,
    player: PlayerState,
    room_id: str,
    step: Move,
) -> None:
    travel = world.map.travel_ticks(room_id, step.room_id)
    if travel is None:
        _blocked(world, player, room_id, "there is no passage that way")
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
        payload={"destination": step.room_id, "travel_ticks": str(travel)},
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
        _blocked(
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
        payload={"origin": origin_id},
    )


def _do_take(
    world: WorldState,
    player: PlayerState,
    room_id: str,
    step: Take,
) -> None:
    if step.thing_id not in visible_things(world, player.id, room_id):
        _blocked(world, player, room_id, "that is not within reach")
        return
    thing = _thing(world, step.thing_id)
    if thing.container:
        _blocked(world, player, room_id, f"the {thing.name} cannot be carried")
        return
    origin = world.placements[step.thing_id]
    source = (
        f" from the {_thing(world, origin.thing_id).name}"
        if isinstance(origin, InContainer)
        else ""
    )
    placement = HeldBy(player_id=player.id)
    perception = perceive(
        world,
        kind="take",
        actor_id=player.id,
        room_id=room_id,
        describe=lambda seen: f"{seen} took the {thing.name}{source}.",
        own_text=f"I took the {thing.name}{source}.",
    )
    append_perceived(
        world,
        perception,
        type="take",
        text=f"{player.name} took the {thing.name}{source}.",
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
    on = _on_person(world, player.id, thing_id)
    if on is None:
        _blocked(world, player, room_id, "that is not in hand")
        return
    thing = _thing(world, thing_id)
    placement = InRoom(room_id=room_id, hidden=hidden)
    verb = "hid" if hidden else "set down"
    if on == "worn":
        verb = f"took off and {verb}"
    perception = perceive(
        world,
        kind="drop",
        actor_id=player.id,
        room_id=room_id,
        describe=lambda seen: f"{seen} {verb} the {thing.name}.",
        own_text=f"I {verb} the {thing.name}.",
    )
    append_perceived(
        world,
        perception,
        type="drop",
        text=f"{player.name} {verb} the {thing.name}.",
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
    on = _on_person(world, player.id, thing_id)
    if on is None:
        _blocked(world, player, room_id, "that is not in hand")
        return
    box = next((item for item in world.map.things if item.id == container_id), None)
    if (
        box is None
        or not box.container
        or container_id not in visible_things(world, player.id, room_id)
    ):
        _blocked(world, player, room_id, "there is nothing here to put it in")
        return
    thing = _thing(world, thing_id)
    placement = InContainer(thing_id=container_id)
    verb = "took off and put" if on == "worn" else "put"
    perception = perceive(
        world,
        kind="stow",
        actor_id=player.id,
        room_id=room_id,
        describe=lambda seen: f"{seen} {verb} the {thing.name} in the {box.name}.",
        own_text=f"I {verb} the {thing.name} in the {box.name}.",
    )
    append_perceived(
        world,
        perception,
        type="stow",
        text=f"{player.name} {verb} the {thing.name} in the {box.name}.",
        actor_id=player.id,
        room_id=room_id,
        payload=placement_payload(thing_id, placement),
    )
    move_object(world, thing_id, placement)


def _on_person(world: WorldState, player_id: str, thing_id: str) -> str | None:
    """Whether a player holds a thing or wears it, or neither."""
    placement = world.placements.get(thing_id)
    if isinstance(placement, HeldBy) and placement.player_id == player_id:
        return "held"
    if isinstance(placement, WornBy) and placement.player_id == player_id:
        return "worn"
    return None


def _do_wear(
    world: WorldState,
    player: PlayerState,
    room_id: str,
    thing_id: str,
) -> None:
    """Change outer garments: one tick, seen by the room in whatever light it
    has. The garment taken off stays in hand, to be carried, dropped or hidden,
    and both garments' custody goes on the ledger."""
    if thing_id not in _wearable(world, player.id):
        _blocked(world, player, room_id, "there is no such garment within reach")
        return
    garment = _thing(world, thing_id)
    old = worn_garment(world, player.id)
    # Witnesses see the change as the person they could make out beforehand.
    perception = perceive(
        world,
        kind="wear",
        actor_id=player.id,
        room_id=room_id,
        describe=lambda seen: f"{seen} changed into a {garment.name}.",
        own_text=f"I changed into a {garment.name}.",
    )
    if old is not None:
        held = HeldBy(player_id=player.id)
        append(
            world,
            type="take_off",
            text=f"{player.name} took off a {old.name}.",
            actor_id=player.id,
            room_id=room_id,
            payload=placement_payload(old.id, held),
            witnesses=[_own_record(player.id, room_id, f"I took off the {old.name}.")],
        )
        move_object(world, old.id, held)
    worn = WornBy(player_id=player.id)
    append_perceived(
        world,
        perception,
        type="wear",
        text=f"{player.name} put on a {garment.name}.",
        actor_id=player.id,
        room_id=room_id,
        payload=placement_payload(thing_id, worn),
    )
    move_object(world, thing_id, worn)


def _do_talk(
    world: WorldState,
    player: PlayerState,
    room_id: str,
    step: Talk,
) -> None:
    listeners = [other for other in awake_in(world, room_id) if other != player.id]
    if not listeners:
        _blocked(world, player, room_id, "there is nobody here to talk to")
        return
    if step.to == player.id or (
        step.to is not None
        and step.to not in listeners
        and world.map.room(room_id).lighting == "bright"
    ):
        _blocked(world, player, room_id, "that person is not here")
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
    carries through a wall."""
    if step.to == player.id or step.to not in world.players:
        _blocked(world, player, room_id, "there is nobody by that name to whisper to")
        return
    reached = step.to in awake_in(world, room_id)
    if not reached and world.map.room(room_id).lighting == "bright":
        _blocked(world, player, room_id, "that person is not here to whisper to")
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


def _do_tool(
    world: WorldState,
    player: PlayerState,
    room_id: str,
    step: ToolStep,
) -> None:
    if step.tool == "search":
        _do_search(world, player, room_id)
        return
    _do_inspect(world, player, room_id, step.thing_id)


def _do_search(world: WorldState, player: PlayerState, room_id: str) -> None:
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
    world.searched.setdefault(player.id, [])
    if room_id not in world.searched[player.id]:
        world.searched[player.id].append(room_id)
    found = ", ".join(_thing(world, thing_id).name for thing_id in hidden)
    room = world.map.room(room_id)
    perception = perceive(
        world,
        kind="search",
        actor_id=player.id,
        room_id=room_id,
        describe=lambda seen: f"{seen} searched the {room.name}.",
        own_text=(
            f"I searched the {room.name} and found: {found}."
            if hidden
            else f"I searched the {room.name} and found nothing hidden."
        ),
    )
    append_perceived(
        world,
        perception,
        type="search",
        text=f"{player.name} searched the {room.name}.",
        actor_id=player.id,
        room_id=room_id,
        payload={"found": str(len(hidden))},
    )


def _do_inspect(
    world: WorldState,
    player: PlayerState,
    room_id: str,
    thing_id: str | None,
) -> None:
    reachable = set(visible_things(world, player.id, room_id))
    for held_id, placement in world.placements.items():
        if isinstance(placement, HeldBy) and placement.player_id == player.id:
            reachable.add(held_id)
    if thing_id is None or thing_id not in reachable:
        _blocked(world, player, room_id, "there is nothing there to examine")
        return
    thing = _thing(world, thing_id)
    world.inspected.setdefault(player.id, [])
    if thing_id not in world.inspected[player.id]:
        world.inspected[player.id].append(thing_id)
    perception = perceive(
        world,
        kind="inspect",
        actor_id=player.id,
        room_id=room_id,
        describe=lambda seen: f"{seen} examined the {thing.name} closely.",
        own_text=f"I examined the {thing.name} and traced where it had been.",
        silent=True,
    )
    append_perceived(
        world,
        perception,
        type="inspect",
        text=f"{player.name} examined the {thing.name}.",
        actor_id=player.id,
        room_id=room_id,
        payload={"thing_id": thing_id},
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
        _blocked(world, player, room_id, "that is not within reach")
        return
    thing = _thing(world, step.thing_id)
    perception = perceive(
        world,
        kind="use",
        actor_id=player.id,
        room_id=room_id,
        describe=lambda seen: f"{seen} used the {thing.name}.",
        own_text=f"I used the {thing.name}.",
    )
    append_perceived(
        world,
        perception,
        type="use",
        text=f"{player.name} used the {thing.name}.",
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


def _blocked(
    world: WorldState,
    player: PlayerState,
    room_id: str,
    reason: str,
    *,
    witnessed: bool = False,
    describe: Callable[[str], str] | None = None,
) -> None:
    """A failed step is private to the actor: nobody else sees an intention.

    The exception is a failure with a visible body attached to it — turning back
    at a full door — where ``witnessed`` lets the room record what it saw.
    """
    perception = perceive(
        world,
        kind="blocked",
        actor_id=player.id,
        room_id=room_id,
        describe=describe or (lambda seen: f"{seen} hesitated."),
        own_text=f"I could not: {reason}.",
        silent=True,
        private=not witnessed,
    )
    append_perceived(
        world,
        perception,
        type="blocked",
        text=f"{player.name} could not act: {reason}.",
        actor_id=player.id,
        room_id=room_id,
        payload={"reason": reason},
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
                _own_record(
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
            _own_record(player_id, room.id, f"I fell asleep in the {room.name}.")
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
        witnesses=[_own_record(player_id, room_id, "I woke.")],
    )
    return world


def _own_record(
    player_id: str,
    room_id: str | None,
    text: str,
) -> WitnessRecord:
    return WitnessRecord(
        player_id=player_id,
        sense="own",
        confidence="clear",
        channel="direct",
        text=text,
        recorded_actor=player_id,
        from_room=room_id,
    )


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


def _thing(world: WorldState, thing_id: str) -> Thing:
    for thing in world.map.things:
        if thing.id == thing_id:
            return thing
    raise KeyError(thing_id)


__all__ = [
    "advance_phase",
    "fall_asleep",
    "fork",
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
