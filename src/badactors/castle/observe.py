"""What one player may be told about the game, and proof nothing else got in.

A :class:`GameObservation` is the world observation from ``scope`` plus the game:
the player's role, what the household has been told (fates, tallies, votes, the
seating), today's round table as they witnessed it, and the decision they are
being asked for. Everything public is copied from the game's public record,
which only grows when the Steward announces something; everything private cites
the ledger entry it came from.

:func:`assert_no_game_leak` extends the structural checker to game information.
Every line of memory must still cite an entry that holds a record for this viewer
(``scope`` checks that). Game entries add a second question — was this viewer
allowed a record of it at all? — because a leak here is usually a referee bug that
writes a record for the wrong person, and such a record looks legitimate to the
first check. Each class of game secret has its guard:

* **another living player's role** — only your own, your fellow traitors', and
  the roles the Steward has revealed of the dead;
* **conclave content reaching a Faithful** — conclave entries, and the requests
  only traitors get, belong to whoever was a traitor when they were written;
* **another player's diary or silence reason** — a private value may reach
  another player only inside words that were actually said;
* **who holds a shield** — the award, and the attack a shield turned, are told
  to the holder alone;
* **a recruitment offer seen by a non-participant** — the offer and its answer
  are between the recruiter and the recruit;
* **the night's target before breakfast** — the kill's machinery belongs to the
  traitors and the target, a death is public only once announced, and a dose
  poured at dinner is known to the poisoner and to whoever the roll let notice;
* **a whisper seen by anyone except its recipient** — and its author, whatever
  tool the words came from;
* **a stolen shield's owner** — the report of a shield gone missing is the
  owner's alone, since it says they carried one;
* **a tool its user does not carry** — a round-table turn offers only the tools
  in its player's kit, and cites only records they hold;
* **a failure on a private decision** — a hesitation over a dose, a nomination,
  an offer or the answer to one is its player's alone, since anyone who saw it
  would learn the question had been put;
* **a refused answer** — an answer refused and asked again is its player's
  alone.

Model names never enter the game state at all, and a player's words that name
one are refused before they are written (see ``anonymity``).
:func:`assert_no_model_names` checks, by the same aliases, that no model name
appears anywhere in an observation, quotes included.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from functools import lru_cache

from pydantic import Field

from badactors.castle.anonymity import Aliases, aliases
from badactors.castle.contracts import (
    Build,
    CastleMap,
    CastlePhase,
    Contract,
    LedgerEntry,
    ObservationItem,
    Passage,
    Room,
    ScopedObservation,
    quickest_route,
)
from badactors.castle.decisions import PRIVATE_REQUESTS, TRAITOR_ONLY, Request
from badactors.castle.game import (
    Fate,
    GameState,
    Tally,
    VoteRound,
    living_traitors,
    publicly_living,
    was_traitor_at,
)
from badactors.castle.kit import SPECS, carries
from badactors.castle.ledger import AsideKind, asides_of, entry_at
from badactors.castle.rules import OFFICIAL_ONLY, MissionSpec, Role, rulebook
from badactors.castle.scope import (
    LeakageError,
    assert_item,
    assert_no_leak,
    build_observation,
    item_for,
)


class Character(Contract):
    """One of the household as everyone was introduced to them: the same roster
    for every player, so all start with the same map of who wore what. It shows
    the garment each began in, not what they wear now, and no distinctive
    detail, which only bright light shows."""

    id: str
    name: str
    persona: str
    build: Build
    garment: str


def roster(game: GameState) -> list[Character]:
    world = game.world
    return [
        Character(
            id=player.id,
            name=player.name,
            persona=player.persona,
            build=player.build,
            garment=player.starting_garment,
        )
        for player in (world.players[pid] for pid in world.seats)
    ]


class CastleView(Contract):
    """The castle as every resident knows it: its rooms and the passages between
    them. What lies in the rooms has to be found; where the conclave meets is
    the traitors' business."""

    name: str
    rooms: list[Room]
    passages: list[Passage]
    gathering_room: str
    chambers_room: str

    def route(self, origin: str, target: str) -> list[str]:
        return quickest_route(self.passages, origin, target)

    def travel(self, origin: str, destination: str) -> int | None:
        for passage in self.passages:
            if passage.other(origin) == destination:
                return passage.travel_ticks
        return None

    def room(self, room_id: str) -> Room:
        for room in self.rooms:
            if room.id == room_id:
                return room
        raise KeyError(room_id)


def castle_view(castle: CastleMap) -> CastleView:
    return CastleView(
        name=castle.name,
        rooms=list(castle.rooms),
        passages=list(castle.passages),
        gathering_room=castle.gathering_room,
        chambers_room=castle.chambers_room,
    )


class OfferReceived(Contract):
    """An offer made to the viewer: they know who came to them."""

    day: int = Field(ge=1)
    recruiter: str
    answer: str | None = None
    seq: int = Field(ge=1)


class Aside(Contract):
    """One of the viewer's own earlier private lines: a diary line, or their
    reason for silence or for letting a chance pass."""

    seq: int = Field(ge=1)
    day: int = Field(ge=1)
    phase: CastlePhase
    kind: AsideKind
    text: str = Field(min_length=1, max_length=600)


class GameObservation(Contract):
    viewer_id: str
    day: int = Field(ge=1)
    phase: CastlePhase
    phase_ticks_left: int = Field(ge=0)
    max_days: int = Field(ge=1)
    rules: str
    castle: CastleView
    starting_traitors: int = Field(ge=0)
    role: Role
    fellow_traitors: list[str] = Field(default_factory=list)
    known_roles: dict[str, Role] = Field(default_factory=dict)
    cast: list[Character]
    living: list[str]
    fates: list[Fate] = Field(default_factory=list)
    pot: int = Field(ge=0)
    tallies: list[Tally] = Field(default_factory=list)
    votes: list[VoteRound] = Field(default_factory=list)
    mission: MissionSpec | None = None
    seating: list[str] = Field(default_factory=list)
    table: list[ObservationItem] = Field(default_factory=list)
    """Today's round table as this player witnessed it."""
    offers: list[OfferReceived] = Field(default_factory=list)
    asides: list[Aside] = Field(default_factory=list)
    """The viewer's own most recent private lines, which nobody else reads."""
    world: ScopedObservation
    request: Request


def observe_game(game: GameState, viewer_id: str, request: Request) -> GameObservation:
    world = game.world
    role = game.roles[viewer_id]
    fellows = (
        [pid for pid in living_traitors(game) if pid != viewer_id]
        if role == "traitor"
        else []
    )
    known: dict[str, Role] = {viewer_id: role}
    known.update({pid: "traitor" for pid in fellows})
    known.update({fate.player_id: fate.role for fate in game.fates})
    table: list[ObservationItem] = []
    if game.table is not None:
        for seq in game.table.seqs:
            entry = world.ledger[seq - 1]
            record = entry.record_for(viewer_id)
            if record is not None:
                table.append(item_for(entry, record))
    return GameObservation(
        viewer_id=viewer_id,
        day=world.day,
        phase=world.phase,
        phase_ticks_left=max(
            0, world.map.phase_ticks.ticks(world.phase) - world.phase_tick
        ),
        max_days=game.config.max_days,
        rules=rules_for(game),
        castle=castle_view(world.map),
        starting_traitors=len(game.starting_traitors),
        role=role,
        fellow_traitors=fellows,
        known_roles=known,
        cast=roster(game),
        living=publicly_living(game),
        fates=list(game.fates),
        pot=game.pot,
        tallies=list(game.tallies),
        votes=list(game.votes),
        mission=game.mission,
        seating=list(game.seating),
        table=table,
        offers=[
            OfferReceived(
                day=offer.day,
                recruiter=offer.recruiter,
                answer=offer.answer,
                seq=offer.seq,
            )
            for offer in game.offers
            if offer.target == viewer_id
        ],
        asides=asides(game, viewer_id),
        world=build_observation(
            world, viewer_id, recent_limit=game.config.recent_limit
        ),
        request=request,
    )


def asides(game: GameState, viewer_id: str) -> list[Aside]:
    """The viewer's last ``diary_limit`` private lines, oldest first."""
    world = game.world
    limit = game.config.diary_limit
    if limit == 0:
        return []
    lines: list[Aside] = []
    for seq in reversed(world.asides.get(viewer_id, [])):
        entry = world.ledger[seq - 1]
        for kind, text in reversed(asides_of(entry)):
            lines.append(
                Aside(seq=seq, day=entry.day, phase=entry.phase, kind=kind, text=text)
            )
        if len(lines) >= limit:
            break
    return list(reversed(lines[:limit]))


def observed(observation: GameObservation) -> list[int]:
    """Every ledger entry whose content this observation put in front of its
    player, in order: their memory and notebook, the table, the announcements
    behind the public record, offers made to them, their own private lines,
    and the custody links of anything they have inspected. Kept beside each
    decision, so a later check can tell whether a record that contradicts a
    claim was there when the claim was made."""
    world = observation.world
    seqs = {item.ledger_seq for item in (*world.recent, *world.evidence)}
    seqs.update(item.ledger_seq for item in observation.table)
    seqs.update(fate.seq for fate in observation.fates)
    seqs.update(tally.seq for tally in observation.tallies)
    seqs.update(vote.seq for vote in observation.votes)
    seqs.update(offer.seq for offer in observation.offers)
    seqs.update(aside.seq for aside in observation.asides)
    things = [*world.visible_objects, *world.carrying]
    if world.wearing is not None:
        things.append(world.wearing)
    for thing in things:
        seqs.update(link.ledger_seq for link in thing.custody or [])
    return sorted(seqs)


def rules_for(game: GameState) -> str:
    world = game.world
    return rulebook(
        game.config,
        players=len(world.seats),
        traitors=len(game.starting_traitors),
        castle_name=world.map.name,
    )


# ------------------------------------------------------------------- the checker


def assert_no_game_leak(
    observation: GameObservation,
    game: GameState,
    *,
    models: Iterable[str] = (),
) -> None:
    """Raise :class:`LeakageError` unless every part of ``observation`` is
    something its viewer may know."""
    viewer = observation.viewer_id
    world = game.world
    if viewer not in world.players:
        raise LeakageError("Observation names a viewer who is not in this game.")
    if not world.players[viewer].alive:
        raise LeakageError(f"{viewer!r} is dead, and the dead take no actions.")
    if observation.world.viewer_id != viewer:
        raise LeakageError("Observation mixes two viewers.")
    # First, so a model name is reported as what it is, wherever it was put.
    assert_no_model_names(
        observation,
        models,
        exempt=[
            name
            for player in world.players.values()
            for name in (player.id, player.name)
        ],
    )
    assert_no_leak(observation.world, world)
    for item in observation.table:
        assert_item(item, viewer, world)
    for item in (
        *observation.world.recent,
        *observation.world.evidence,
        *observation.table,
    ):
        entry = entry_at(world.ledger, item.ledger_seq)
        assert entry is not None  # assert_item has already proven it exists
        _assert_audience(entry, viewer, game)
        _assert_private(entry, item, viewer)
    _assert_asides(observation, game)
    _assert_roles(observation, game)
    _assert_public(observation, game)
    _assert_offers(observation, game)
    _assert_request(observation, game)
    if OFFICIAL_ONLY not in observation.rules:
        raise LeakageError(
            "The rules must tell every player that only the Steward's announcements "
            "are real."
        )


PRIVATE_OWN: frozenset[str] = frozenset(
    {"plan_formed", "wait", "stay_silent", "declined", "refused"}
)
"""Entries whose actor's own record repeats words nobody else ever sees: a
plan's goal, a reason to wait, stay silent or let a chance pass, and an answer
refused back to its giver. A model name there tells another player nothing."""


def assert_no_model_names(
    observation: GameObservation,
    models: Iterable[str],
    *,
    exempt: Iterable[str] = (),
) -> None:
    """No model name anywhere: not in a persona, a rule, or a quoted word.
    ``exempt`` holds the characters' names and ids, which never count. The
    viewer's own private words, shown back to them, are theirs to see."""
    known = _aliases(tuple(sorted(set(models))), tuple(sorted(set(exempt))))
    # The rulebook is the same long text in every observation of a game, so it is
    # checked once and remembered; everything else is checked every time.
    found = _mention(known, observation.rules) or known.found(
        _for_others(observation).model_dump_json(exclude={"rules"})
    )
    if found:
        raise LeakageError(
            f"Observation for {observation.viewer_id!r} contains the model name "
            f"{found!r}."
        )


def _for_others(observation: GameObservation) -> GameObservation:
    """The observation less what only its viewer wrote for themselves: their
    private lines, the plan they have yet to carry out, and why an answer of
    theirs was refused."""

    def heard(items: list[ObservationItem]) -> list[ObservationItem]:
        return [
            item
            for item in items
            if not (item.sense == "own" and item.kind in PRIVATE_OWN)
        ]

    world = observation.world.model_copy(
        update={
            "recent": heard(observation.world.recent),
            "evidence": heard(observation.world.evidence),
            "plan_remaining": [],
        }
    )
    return observation.model_copy(
        update={
            "world": world,
            "table": heard(observation.table),
            "asides": [],
            "request": observation.request.model_copy(update={"refusal": None}),
        }
    )


@lru_cache(maxsize=32)
def _aliases(models: tuple[str, ...], exempt: tuple[str, ...]) -> Aliases:
    return aliases(models, exempt=exempt)


@lru_cache(maxsize=64)
def _mention(known: Aliases, text: str) -> str | None:
    return known.found(text)


# ------------------------------------------------------------- who may hold what

CONCLAVE_TYPES: frozenset[str] = frozenset(
    {
        "conclave_call",
        "conclave",
        "nominate",
        "conclave_choice",
        "kill_failed",
        "conclave_missed",
    }
)
UNSEEN_TYPES: frozenset[str] = frozenset(
    {"roles_drawn", "kill_attempt", "death", "spent", "contribution", "payout"}
)
"""Ground truth nobody perceived when it happened."""


type Audience = Callable[[str], bool]


def _audience_of(entry: LedgerEntry, game: GameState) -> tuple[str, Audience] | None:
    """The leak class an entry belongs to, and who may hold a record of it."""
    kind = entry.type
    payload = entry.payload
    if kind == "role_told":
        return "another player's role", lambda viewer: viewer == payload.get("player")
    if kind in CONCLAVE_TYPES:
        return "conclave content", lambda viewer: was_traitor_at(
            game, viewer, entry.seq
        )
    if kind in ("recruit_offer", "recruit_answer"):
        party = {entry.actor_id, payload.get("target")}
        return "a recruitment offer", lambda viewer: viewer in party
    if kind == "awarded":
        return "who holds a shield", lambda viewer: viewer == payload.get("holder_id")
    if kind == "attack_survived":
        return "the night's target", lambda viewer: viewer == payload.get("target")
    if kind == "poison":
        noticed = set(payload.get("seen_by", "").split(","))
        return (
            "the night's target",
            lambda viewer: viewer == entry.actor_id or viewer in noticed,
        )
    if kind == "missing":
        return "a stolen shield's owner", lambda viewer: viewer == payload.get("owner")
    if kind in UNSEEN_TYPES:
        return "the night's target", lambda viewer: False
    if kind == "whisper":
        pair = {entry.actor_id, payload.get("to")}
        return "a whisper", lambda viewer: viewer in pair
    if kind == "declined":
        return "a private choice", lambda viewer: viewer == entry.actor_id
    if kind == "refused":
        return "a refused answer", lambda viewer: viewer == entry.actor_id
    if kind == "turn_failed" and payload.get("request") in PRIVATE_REQUESTS:
        return (
            "a failure on a private decision",
            lambda viewer: viewer == entry.actor_id,
        )
    return None


def _assert_audience(entry: LedgerEntry, viewer: str, game: GameState) -> None:
    rule = _audience_of(entry, game)
    if rule is None:
        return
    label, allowed = rule
    if not allowed(viewer):
        raise LeakageError(
            f"Entry {entry.seq} ({entry.type}) reached {viewer!r}, who has no claim "
            f"to it: {label}."
        )


def _assert_private(entry: LedgerEntry, item: ObservationItem, viewer: str) -> None:
    """Another player's diary or reason may only reach you in words they said."""
    if entry.actor_id == viewer or not entry.private:
        return
    public = f"{entry.text}\n{entry.quote or ''}"
    shown = f"{item.text}\n{item.quote or ''}"
    for key, value in entry.private.items():
        if len(value) >= 3 and value in shown and value not in public:
            raise LeakageError(
                f"Entry {entry.seq} shows {viewer!r} the private {key} of "
                f"{entry.actor_id!r}: another player's diary or silence reason."
            )


def _assert_asides(observation: GameObservation, game: GameState) -> None:
    """Private lines shown back are the viewer's own, word for word, and no more
    of them than the limit allows."""
    viewer = observation.viewer_id
    if len(observation.asides) > game.config.diary_limit:
        raise LeakageError(
            f"Observation shows {viewer!r} more private lines than the limit allows."
        )
    for aside in observation.asides:
        entry = entry_at(game.world.ledger, aside.seq)
        if entry is None or entry.actor_id != viewer:
            raise LeakageError(
                f"Observation shows {viewer!r} a private line from entry {aside.seq}, "
                "which is not theirs: another player's diary or silence reason."
            )
        if (aside.kind, aside.text) not in asides_of(entry) or (
            aside.day,
            aside.phase,
        ) != (entry.day, entry.phase):
            raise LeakageError(
                f"Observation misquotes {viewer!r}'s private line from entry "
                f"{entry.seq}."
            )


def _assert_roles(observation: GameObservation, game: GameState) -> None:
    viewer = observation.viewer_id
    truth = game.roles[viewer]
    if observation.role != truth:
        raise LeakageError("Observation misstates the viewer's own role.")
    fellows = (
        [pid for pid in living_traitors(game) if pid != viewer]
        if truth == "traitor"
        else []
    )
    if observation.fellow_traitors != fellows:
        raise LeakageError(
            f"Observation names fellow traitors {observation.fellow_traitors} to "
            f"{viewer!r}: another living player's role."
        )
    revealed = {fate.player_id: fate.role for fate in game.fates}
    for pid, role in observation.known_roles.items():
        if pid == viewer and role == truth:
            continue
        if pid in fellows and role == "traitor":
            continue
        if revealed.get(pid) == role:
            continue
        raise LeakageError(
            f"Observation tells {viewer!r} that {pid!r} is a {role}: another "
            "living player's role."
        )


def _assert_public(observation: GameObservation, game: GameState) -> None:
    """The public record, exactly as announced, and nothing announced early."""
    viewer = observation.viewer_id
    ledger = game.world.ledger
    if observation.living != publicly_living(game):
        raise LeakageError(
            "Observation misstates who is alive: a death is public only once the "
            "Steward announces it (the night's target before breakfast)."
        )
    if observation.fates != game.fates:
        raise LeakageError(
            "Observation misstates the announced deaths and banishments."
        )
    for fate in observation.fates:
        _assert_announced(ledger, fate.seq, viewer, "a fate")
    if observation.tallies != game.tallies:
        raise LeakageError("Observation misstates the announced mission tallies.")
    for tally in observation.tallies:
        _assert_announced(ledger, tally.seq, viewer, "a mission tally")
    if observation.votes != game.votes:
        raise LeakageError("Observation misstates the announced votes.")
    for vote in observation.votes:
        _assert_announced(ledger, vote.seq, viewer, "a vote")
    if observation.pot != game.pot:
        raise LeakageError("Observation misstates the pot.")
    if observation.cast != roster(game):
        raise LeakageError(
            "Observation's roster is not the household as introduced: it must "
            "show the garments people began in, and no distinctive details."
        )
    if observation.castle != castle_view(game.world.map):
        raise LeakageError(
            "Observation describes the castle beyond its public rooms and passages."
        )
    if observation.starting_traitors != len(game.starting_traitors):
        raise LeakageError("Observation misstates how many traitors began.")
    if observation.mission != game.mission:
        raise LeakageError("Observation misstates today's mission.")
    if observation.seating != game.seating:
        raise LeakageError("Observation misstates tonight's seating.")


def _assert_announced(
    ledger: list[LedgerEntry], seq: int, viewer: str, what: str
) -> None:
    entry = entry_at(ledger, seq)
    record = entry.record_for(viewer) if entry is not None else None
    if entry is None or record is None or record.channel != "steward":
        raise LeakageError(
            f"Observation shows {viewer!r} {what} the Steward never announced to them."
        )


def _assert_offers(observation: GameObservation, game: GameState) -> None:
    viewer = observation.viewer_id
    received = [
        (offer.day, offer.recruiter, offer.answer, offer.seq)
        for offer in game.offers
        if offer.target == viewer
    ]
    shown = [
        (offer.day, offer.recruiter, offer.answer, offer.seq)
        for offer in observation.offers
    ]
    if shown != received:
        raise LeakageError(
            f"Observation shows {viewer!r} an offer made to someone else: a "
            "recruitment offer seen by a non-participant."
        )


def _assert_request(observation: GameObservation, game: GameState) -> None:
    viewer = observation.viewer_id
    request = observation.request
    if request.kind in TRAITOR_ONLY and game.roles[viewer] != "traitor":
        raise LeakageError(
            f"A {request.kind} decision was put to {viewer!r}, one of the Faithful: "
            "conclave content reaching a Faithful."
        )
    for tool in request.tools:
        if tool not in SPECS or not carries(game.world, viewer, tool):
            raise LeakageError(
                f"{viewer!r} is offered {tool!r}, which their kit does not hold: a "
                "tool its user does not carry."
            )
    for seq in request.evidence:
        entry = entry_at(game.world.ledger, seq)
        if entry is None or entry.record_for(viewer) is None:
            raise LeakageError(
                f"{viewer!r} is offered entry {seq} to cite, which they never "
                "witnessed: a tool its user does not carry."
            )
    if request.kind == "recruit_answer":
        offer = game.night.offer if game.night is not None else None
        if (
            offer is None
            or offer.answer is not None
            or offer.target != viewer
            or request.about != offer.recruiter
        ):
            raise LeakageError(
                f"{viewer!r} is asked to answer an offer they were never made: a "
                "recruitment offer seen by a non-participant."
            )


__all__ = [
    "CONCLAVE_TYPES",
    "PRIVATE_OWN",
    "UNSEEN_TYPES",
    "Aside",
    "CastleView",
    "Character",
    "GameObservation",
    "OfferReceived",
    "asides",
    "assert_no_game_leak",
    "assert_no_model_names",
    "castle_view",
    "observe_game",
    "observed",
    "rules_for",
]
