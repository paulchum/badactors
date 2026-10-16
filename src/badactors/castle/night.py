"""Dinner, night and the morning after: where the one kill attempt happens.

Each night has exactly one attempt on a life. It is either poison, poured at
dinner by a traitor seated beside the victim, or a murder the conclave chooses in
the crypt. The poisoned die at dawn whatever they carry; a murder is turned aside
by a shield in the victim's hands, which crumbles doing it.

Poison and recruitment are tools, and run on the same machinery as the rest: the
kit gates them, the balance sets their limits, and their rolls are drawn and
recorded the same way. Each place at dinner has its own glass, the one in the
glass store numbered for that place, and the glasses are washed and set before
the seats are drawn. A poisoning may leave residue in the victim's glass, and
whoever sits on the poisoner's other side may notice the reach; the poisoner is
told neither.

The night is walked, not narrated. When the lamps go out everybody walks to the
chambers, traitors included, so leaving dinner tells nobody anything. The
Faithful fall asleep there, and a sleeper records nothing. Once the house is
quiet the traitors slip down to the conclave room, meet, and are back in the
chambers before dawn, so nobody arrives at breakfast from the wrong direction.
:func:`~badactors.castle.game.night_schedule` fixes the ticks from the map.

When one or two traitors remain, and fewer than began, the recruitment window
opens once the house is asleep: a traitor may wake one of the Faithful and make
an offer face to face. Nobody else hears it. Refusing makes the refuser tonight's
target, fixed before the conclave meets.
"""

from __future__ import annotations

from collections import Counter

from badactors.castle.contracts import (
    MAX_PLAN_STEPS,
    Channel,
    Move,
    Offstage,
    Plan,
    Step,
    Wait,
    WitnessRecord,
)
from badactors.castle.decisions import (
    Accept,
    Decision,
    Nominate,
    Pass,
    Poison,
    Recruit,
    Refuse,
    Request,
)
from badactors.castle.game import (
    Fate,
    GameState,
    NightState,
    Offer,
    alive,
    finish,
    living,
    living_faithful,
    living_traitors,
    make_traitor,
    name,
    names,
    present_at,
    remove,
    shield_held_by,
    tell_role,
    victor,
)
from badactors.castle.kit import Attempt, refusal
from badactors.castle.ledger import (
    append,
    append_perceived,
    move_object,
    placement_payload,
)
from badactors.castle.perceive import perceive, seen_as
from badactors.castle.rng import draw
from badactors.castle.steward import announce, note, tell
from badactors.castle.world import fall_asleep, route_steps, wake_up

# ------------------------------------------------------------------------ dinner


def seat_dinner(game: GameState) -> None:
    """Wash and set the glasses, draw tonight's seats for whoever came to
    dinner, and post the plan."""
    world = game.world
    note(
        world,
        type="washed",
        text="The glasses were washed and set, one at each place.",
        room_id=world.map.gathering_room,
    )
    order = present_at(game, world.map.gathering_room)
    cursor = world.rng_cursor
    rolls = []
    for index in range(len(order) - 1, 0, -1):
        roll = draw(world.seed, cursor, f"seat:day{world.day}:{index}")
        cursor += 1
        rolls.append(roll)
        swap = int(roll.value * (index + 1))
        order[index], order[swap] = order[swap], order[index]
    words = (
        f"Dinner is served. Tonight's seats, round the table: {names(game, order)}."
        if order
        else "Dinner is served to an empty table."
    )
    entry = announce(
        world,
        type="seating",
        words=words,
        payload={"seating": ",".join(order)},
        rolls=rolls,
        cursor=cursor,
    )
    game.seating = order
    game.seating_seq = entry.seq
    game.night = NightState(day=world.day)


def neighbours(seating: list[str], player_id: str) -> list[str]:
    """Who sits either side at a round table."""
    if player_id not in seating or len(seating) < 2:
        return []
    index = seating.index(player_id)
    left, right = seating[index - 1], seating[(index + 1) % len(seating)]
    return [left] if left == right else [left, right]


def glass_at(game: GameState, player_id: str) -> str | None:
    """The glass at this diner's place tonight, if they had one."""
    if player_id not in game.seating:
        return None
    glass = f"glass_{game.seating.index(player_id) + 1}"
    things = {thing.id for thing in game.world.map.things}
    return glass if glass in things else None


def poison_cooling(game: GameState) -> bool:
    """Whether a poisoning too recent stops the traitors pouring tonight."""
    wait = game.world.balance.tools.poison.cooldown_days
    for entry in reversed(game.world.ledger):
        if entry.payload.get("tool") == "poison":
            return game.world.day - entry.day < wait
    return False


def poison_turn(game: GameState) -> tuple[str, Request] | None:
    """The next traitor who sits beside one of the Faithful and may still pour,
    while nobody has poured tonight and the last poisoning is far enough back."""
    night = game.night
    if night is None or night.poisoned is not None or poison_cooling(game):
        return None
    world = game.world
    for pid in world.seats:
        if pid in night.asked_poison or pid not in game.seating:
            continue
        if game.roles[pid] != "traitor" or not alive(game, pid):
            continue
        if refusal(world, pid, "poison") is not None:
            continue
        beside = neighbours(game.seating, pid)
        victims = [
            other
            for other in world.seats
            if other in beside
            and game.roles[other] == "faithful"
            and alive(game, other)
        ]
        if victims:
            return pid, Request(
                kind="poison",
                options=victims,
                prompt=(
                    f"You are seated beside {names(game, victims)}. You may slip "
                    "poison into one glass instead of tonight's murder, or pass. "
                    "The poisoned die at dawn, shield or no."
                ),
            )
    return None


def poison_act(
    game: GameState, player_id: str, request: Request, decision: Decision | None
) -> None:
    night = game.night
    if night is None:
        return
    night.asked_poison.append(player_id)
    if isinstance(decision, Poison):
        _pour(game, night, player_id, decision)
    elif isinstance(decision, Pass):
        declined(game, player_id, request, decision)


def _pour(game: GameState, night: NightState, player_id: str, decision: Poison) -> None:
    """The dose goes in. Residue is a hidden roll, and so is each neighbour's
    chance to notice the reach; the poisoner's own memory is the same whatever
    they came to."""
    world = game.world
    room_id = world.map.gathering_room
    victim = decision.target
    numbers = world.balance.tools.poison
    attempt = Attempt(world, player_id, "poison")
    residue = attempt.roll("trace", numbers.trace, victim)
    others = [
        pid
        for pid in neighbours(game.seating, player_id)
        if pid != victim and alive(game, pid)
    ]
    noticed = [pid for pid in others if attempt.roll("seen", numbers.seen, pid)]
    perception = perceive(
        world,
        kind="poison",
        actor_id=player_id,
        room_id=room_id,
        describe=lambda seen: f"{seen} reached across the table.",
        own_text=f"I tipped the dose into {name(game, victim)}'s glass.",
        private=True,
    )
    perception.note(player_id)
    lighting = world.map.room(room_id).lighting
    for pid in noticed:
        perception.put(
            WitnessRecord(
                player_id=pid,
                sense="saw",
                confidence="clear" if lighting == "bright" else "unsure",
                channel="direct",
                text=f"{seen_as(world, player_id, lighting)} reached across toward "
                f"{name(game, victim)}'s glass.",
                recorded_actor=player_id if lighting == "bright" else None,
                from_room=room_id,
                noted=True,
            )
        )
    glass = glass_at(game, victim)
    append_perceived(
        world,
        perception,
        type="poison",
        text=f"{name(game, player_id)} poisoned {name(game, victim)}'s glass.",
        actor_id=player_id,
        room_id=room_id,
        payload={
            "tool": "poison",
            "target": victim,
            "glass": glass or "",
            "residue": "true" if residue and glass else "false",
            "seen_by": ",".join(noticed),
        },
        private={"diary": decision.diary} if decision.diary else {},
        rolls=attempt.rolls,
    )
    note(
        world,
        type="kill_attempt",
        text=f"Tonight's kill is poison, for {name(game, victim)}.",
        payload={"method": "poison", "target": victim, "by": player_id},
    )
    night.poisoner = player_id
    night.poisoned = victim


def declined(game: GameState, player_id: str, request: Request, decision: Pass) -> None:
    """Letting a chance go is a choice, and it is written down as one, with the
    reason only its maker and the audience read."""
    world = game.world
    player = world.players[player_id]
    what = {
        "poison": "I let the chance to poison pass",
        "recruit": "I made no offer",
        "nominate": "I named nobody",
    }
    private = {"reason": decision.reason}
    if decision.diary:
        private["diary"] = decision.diary
    append(
        world,
        type="declined",
        text=f"{player.name} declined to {request.kind}.",
        actor_id=player_id,
        room_id=player.room_id,
        payload={"request": request.kind},
        private=private,
        witnesses=[
            WitnessRecord(
                player_id=player_id,
                sense="own",
                confidence="clear",
                channel="direct",
                text=f"{what.get(request.kind, 'I let it pass')}: {decision.reason}"[
                    :400
                ],
                recorded_actor=player_id,
                from_room=player.room_id,
            )
        ],
    )


# ------------------------------------------------------------------------- night


def open_night(game: GameState) -> None:
    """Lamps out: everyone to the chambers; the traitors down to the conclave
    and back once the house sleeps."""
    world = game.world
    castle = world.map
    if game.night is None:
        game.night = NightState(day=world.day)
    chambers = castle.room(castle.chambers_room).name
    announce(
        world,
        type="retire",
        words=f"Lamps out. The household retires to the {chambers}.",
    )
    traitors = living_traitors(game)
    for pid in living(game):
        steps = route_steps(world, pid, castle.chambers_room)
        goal = f"sleep in the {chambers}"
        if pid in traitors:
            steps = [*steps, *_conclave_trip(game)]
            goal = "slip away to the conclave and back"
        world.players[pid].plan = Plan(goal=goal, steps=steps[:MAX_PLAN_STEPS])
    conclave = castle.room(castle.conclave_room).name
    tell(
        world,
        traitors,
        type="conclave_call",
        words=(
            f"When the house is asleep, slip down to the {conclave}: the conclave "
            f"meets there tonight. Be back in the {chambers} by dawn."
        ),
    )


def _conclave_trip(game: GameState) -> list[Step]:
    castle = game.world.map
    return [
        Move(room_id=castle.conclave_room),
        Wait(reason="the conclave"),
        Move(room_id=castle.chambers_room),
    ]


def settle_sleepers(game: GameState) -> None:
    """The Faithful who have reached the chambers go to sleep."""
    chambers = game.world.map.chambers_room
    for pid in game.world.seats:
        player = game.world.players[pid]
        if (
            player.alive
            and not player.asleep
            and game.roles[pid] == "faithful"
            and player.room_id == chambers
            and not player.plan.steps
        ):
            game.world = fall_asleep(game.world, pid)


# ------------------------------------------------------------------ recruitment


def can_recruit(game: GameState) -> bool:
    """Whether tonight may see an offer at all: one or two traitors left, fewer
    than began, nobody poisoned and no offer made yet."""
    night = game.night
    traitors = living_traitors(game)
    return (
        night is not None
        and night.poisoned is None
        and night.offer is None
        and 1 <= len(traitors) <= 2
        and len(traitors) < len(game.starting_traitors)
    )


def recruit_turn(game: GameState) -> tuple[str, Request] | None:
    """An offer awaiting its answer, or the next traitor who may make one."""
    night = game.night
    if night is None:
        return None
    offer = night.offer
    if offer is not None and offer.answer is None:
        return offer.target, Request(
            kind="recruit_answer",
            about=offer.recruiter,
            prompt=(
                f"{name(game, offer.recruiter)} has woken you and offers you a place "
                "among the traitors. Accept and you are one of them. Refuse and you "
                "are tonight's target; only a shield can save you."
            ),
        )
    if not can_recruit(game):
        return None
    chambers = game.world.map.chambers_room
    approached = {previous.target for previous in game.offers}
    targets = [
        pid
        for pid in living_faithful(game)
        if game.world.players[pid].room_id == chambers and pid not in approached
    ]
    if not targets:
        return None
    for pid in present_at(game, chambers):
        if game.roles[pid] != "traitor" or pid in night.asked_recruit:
            continue
        if refusal(game.world, pid, "recruit") is not None:
            continue
        return pid, Request(
            kind="recruit",
            options=targets,
            prompt=(
                "The house is asleep. You may wake one of the Faithful and offer "
                "them a place among the traitors, face to face. If they refuse, "
                "they are tonight's target, and they will know it was you."
            ),
        )
    return None


def recruit_act(
    game: GameState, player_id: str, request: Request, decision: Decision | None
) -> None:
    night = game.night
    if night is None:
        return
    if request.kind == "recruit":
        night.asked_recruit.append(player_id)
        if isinstance(decision, Recruit):
            _make_offer(game, night, player_id, decision)
        elif isinstance(decision, Pass):
            declined(game, player_id, request, decision)
        return
    offer = night.offer
    if offer is None or offer.answer is not None:
        return
    if isinstance(decision, Accept):
        seq = _answer(game, offer, decision, "accepted")
        _settle_offer(game, night, offer.model_copy(update={"answer": "accepted"}))
        make_traitor(game, offer.target, seq)
        tell_role(game, offer.target, joined=True)
        game.world.players[offer.target].plan = Plan(
            goal="follow the traitors to the conclave", steps=_conclave_trip(game)
        )
    elif isinstance(decision, Refuse):
        _answer(game, offer, decision, "refused")
        _settle_offer(game, night, offer.model_copy(update={"answer": "refused"}))
        night.target = offer.target
        game.world = fall_asleep(game.world, offer.target)
    else:
        _settle_offer(game, night, offer.model_copy(update={"answer": "lapsed"}))
        game.world = fall_asleep(game.world, offer.target)


def _make_offer(
    game: GameState, night: NightState, recruiter: str, decision: Recruit
) -> None:
    target = decision.target
    game.world = wake_up(game.world, target)
    world = game.world
    room_id = world.players[recruiter].room_id
    entry = append(
        world,
        type="recruit_offer",
        text=f"{name(game, recruiter)} woke {name(game, target)} and offered a "
        "place among the traitors:",
        actor_id=recruiter,
        room_id=room_id,
        quote=decision.text,
        payload={"tool": "recruit", "target": target},
        private={"diary": decision.diary} if decision.diary else {},
        witnesses=[
            WitnessRecord(
                player_id=recruiter,
                sense="own",
                confidence="clear",
                channel="whisper",
                text=f"I woke {name(game, target)} and made the offer:",
                quote=decision.text,
                author=recruiter,
                recorded_actor=recruiter,
                from_room=room_id,
                noted=True,
            ),
            WitnessRecord(
                player_id=target,
                sense="saw",
                confidence="clear",
                channel="whisper",
                text=f"{name(game, recruiter)} woke me and offered me a place "
                "among the traitors:",
                quote=decision.text,
                author=recruiter,
                recorded_actor=recruiter,
                from_room=room_id,
                noted=True,
            ),
        ],
    )
    offer = Offer(day=world.day, recruiter=recruiter, target=target, seq=entry.seq)
    night.offer = offer
    game.offers.append(offer)


def _answer(game: GameState, offer: Offer, decision: Accept | Refuse, verb: str) -> int:
    world = game.world
    room_id = world.players[offer.target].room_id
    words = decision.text or None
    channel: Channel = "whisper" if words else "direct"
    said = ":" if words else "."
    entry = append(
        world,
        type="recruit_answer",
        text=f"{name(game, offer.target)} {verb} {name(game, offer.recruiter)}'s "
        f"offer{said}",
        actor_id=offer.target,
        room_id=room_id,
        quote=words,
        payload={"target": offer.recruiter, "answer": verb},
        private={"diary": decision.diary} if decision.diary else {},
        witnesses=[
            WitnessRecord(
                player_id=offer.target,
                sense="own",
                confidence="clear",
                channel=channel,
                text=f"I {verb} the offer{said}",
                quote=words,
                author=offer.target if words else None,
                recorded_actor=offer.target,
                from_room=room_id,
            ),
            WitnessRecord(
                player_id=offer.recruiter,
                sense="saw",
                confidence="clear",
                channel=channel,
                text=f"{name(game, offer.target)} {verb} my offer{said}",
                quote=words,
                author=offer.target if words else None,
                recorded_actor=offer.target,
                from_room=room_id,
            ),
        ],
    )
    return entry.seq


def _settle_offer(game: GameState, night: NightState, offer: Offer) -> None:
    night.offer = offer
    game.offers = [
        offer if previous.seq == offer.seq else previous for previous in game.offers
    ]


# ---------------------------------------------------------------------- conclave


def open_conclave(game: GameState) -> None:
    """The traitors who made it down to the conclave room hear where things
    stand tonight."""
    night = game.night
    if night is None:
        return
    world = game.world
    members = present_at(game, world.map.conclave_room)
    members = [pid for pid in members if game.roles[pid] == "traitor"]
    night.conclave = members
    night.conclave_open = True
    if not members:
        note(
            world,
            type="conclave_missed",
            text="No traitor reached the conclave tonight.",
            room_id=world.map.conclave_room,
        )
        return
    lines = ["The conclave is met."]
    offer = night.offer
    if offer is not None and offer.answer == "accepted":
        lines.append(
            f"{name(game, offer.target)} accepted {name(game, offer.recruiter)}'s "
            "offer and sits with you now."
        )
    fixed = night.poisoned or night.target
    if night.poisoned is not None:
        lines.append(
            f"Tonight's kill is already in a glass: {name(game, night.poisoned)} "
            "will not wake."
        )
    elif night.target is not None:
        lines.append(
            f"{name(game, night.target)} refused the offer, and is tonight's target. "
            "Only a shield can save them."
        )
    else:
        lines.append("Name tonight's victim.")
    tell(
        world,
        members,
        type="conclave",
        words=" ".join(lines),
        payload={"target": fixed} if fixed else {},
    )


def conclave_turn(game: GameState) -> tuple[str, Request] | None:
    night = game.night
    if night is None or not night.conclave_open:
        return None
    if night.poisoned is not None or night.target is not None:
        return None
    victims = living_faithful(game)
    for pid in night.conclave:
        if pid in night.asked_nominate or not alive(game, pid):
            continue
        return pid, Request(
            kind="nominate",
            options=victims,
            prompt="Name tonight's victim, or pass. The others here will hear a "
            "name; a pass is yours alone.",
        )
    return None


def conclave_act(
    game: GameState, player_id: str, request: Request, decision: Decision | None
) -> None:
    night = game.night
    if night is None:
        return
    night.asked_nominate.append(player_id)
    if isinstance(decision, Pass):
        declined(game, player_id, request, decision)
        return
    if not isinstance(decision, Nominate):
        return
    night.nominations[player_id] = decision.target
    world = game.world
    room_id = world.map.conclave_room
    whom = name(game, decision.target)
    words = decision.text or None
    said = f"named {whom}:" if words else f"named {whom}."
    perception = perceive(
        world,
        kind="talk" if words else "nominate",
        actor_id=player_id,
        room_id=room_id,
        describe=lambda seen: f"{seen} {said}",
        own_text=f"I {said}",
        channel="speech" if words else "direct",
        quote=words,
        silent=words is None,
    )
    append_perceived(
        world,
        perception,
        type="nominate",
        text=f"{name(game, player_id)} {said}",
        actor_id=player_id,
        room_id=room_id,
        quote=words,
        payload={"target": decision.target},
        private={"diary": decision.diary} if decision.diary else {},
    )


def close_conclave(game: GameState) -> None:
    """Count the names. Ties are settled by lot."""
    night = game.night
    if night is None or not night.conclave_open:
        return
    night.conclave_open = False
    if not night.conclave or night.poisoned is not None or night.target is not None:
        return
    world = game.world
    counts = Counter(night.nominations.values())
    if not counts:
        tell(
            world,
            night.conclave,
            type="conclave_choice",
            words="The conclave named nobody. Tonight nobody dies by your hand.",
        )
        return
    top = max(counts.values())
    tied = [pid for pid in world.seats if counts.get(pid) == top]
    rolls = []
    if len(tied) > 1:
        roll = draw(world.seed, world.rng_cursor, f"conclave:day{world.day}")
        rolls.append(roll)
        chosen = tied[int(roll.value * len(tied))]
    else:
        chosen = tied[0]
    night.target = chosen
    ballots = "; ".join(
        f"{name(game, pid)} named {name(game, target)}"
        for pid, target in night.nominations.items()
    )
    lots = " Lots settled the tie." if len(tied) > 1 else ""
    who = name(game, chosen)
    tell(
        world,
        night.conclave,
        type="conclave_choice",
        words=(
            f"{ballots}.{lots} The conclave has chosen {who}. Unless a shield turns "
            f"the blow, {who} will not wake."
        ),
        payload={"target": chosen},
        rolls=rolls,
        cursor=world.rng_cursor + len(rolls),
    )


# ------------------------------------------------------------------------- dawn


def end_night(game: GameState) -> None:
    """The murder, if any; the poison takes; then everyone left wakes."""
    night = game.night
    world = game.world
    blocked: str | None = None
    if night is not None:
        target = night.target
        if night.poisoned is None and target is not None and alive(game, target):
            note(
                world,
                type="kill_attempt",
                text=f"The traitors came for {name(game, target)} in the night.",
                payload={
                    "method": "murder",
                    "target": target,
                    "by": ",".join(night.conclave),
                },
            )
            shield = shield_held_by(game, target)
            if shield is not None:
                append(
                    world,
                    type="spent",
                    text=f"{name(game, target)}'s shield token turned the blow and "
                    "crumbled.",
                    payload={**placement_payload(shield, Offstage()), "target": target},
                )
                move_object(world, shield, Offstage())
                game.shields_spent.append(shield)
                night.blocked = True
                blocked = target
            else:
                remove(game, target, type="death", cause="murdered in the night")
        if night.poisoned is not None and alive(game, night.poisoned):
            remove(game, night.poisoned, type="death", cause="poisoned at dinner")
    for pid in living(game):
        if game.world.players[pid].asleep:
            game.world = wake_up(game.world, pid)
    if blocked is not None:
        tell(
            game.world,
            [blocked],
            type="attack_survived",
            words=(
                "In the night someone came for you. The shield you carried turned "
                "the blow aside, and crumbled to dust."
            ),
            payload={"target": blocked},
        )
        tell(
            game.world,
            living_traitors(game),
            type="kill_failed",
            words=f"The attempt on {name(game, blocked)} failed. "
            f"{name(game, blocked)} is alive.",
            payload={"target": blocked},
        )


def breakfast_news(game: GameState) -> None:
    """Announce last night's dead, if any, and see whether anyone has won."""
    world = game.world
    announced = {fate.player_id for fate in game.fates}
    dead = [pid for pid in world.seats if not alive(game, pid) and pid not in announced]
    if dead:
        words = " ".join(
            f"{name(game, pid)} did not come down to breakfast. {name(game, pid)} is "
            f"dead, and was "
            f"{'a traitor' if game.roles[pid] == 'traitor' else 'one of the Faithful'}."
            for pid in dead
        )
    else:
        words = "Everyone came down to breakfast. Nobody died in the night."
    entry = announce(
        world,
        type="breakfast_news",
        words=words,
        payload={"dead": ",".join(dead)},
    )
    for pid in dead:
        game.fates.append(
            Fate(
                player_id=pid,
                day=world.day,
                how="night",
                role=game.roles[pid],
                seq=entry.seq,
            )
        )
    game.night = None
    game.table = None
    game.mission = None
    game.mission_seq = None
    winner = victor(game)
    if winner is not None:
        finish(game, winner, reason="Nobody is left to stop them.")


__all__ = [
    "breakfast_news",
    "can_recruit",
    "close_conclave",
    "conclave_act",
    "conclave_turn",
    "declined",
    "end_night",
    "neighbours",
    "open_conclave",
    "open_night",
    "poison_act",
    "poison_turn",
    "recruit_act",
    "recruit_turn",
    "seat_dinner",
    "settle_sleepers",
]
