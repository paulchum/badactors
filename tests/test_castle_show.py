"""The audience feed: a stored game laid out for the show, as a pure function of
its record and the fact-checker's verdicts."""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from badactors.castle import play
from badactors.castle.bots import HeuristicBot
from badactors.castle.claims import PUBLIC
from badactors.castle.cli import main as cli_main
from badactors.castle.contracts import AtRoom, WorldState
from badactors.castle.decisions import Decision
from badactors.castle.episode import episode
from badactors.castle.factcheck import Verdict
from badactors.castle.moments import STAMPS, numbered, stamp
from badactors.castle.observe import GameObservation
from badactors.castle.play import (
    GameRecord,
    Player,
    ReplayDiverged,
    record,
    replay,
    run_game,
)
from badactors.castle.show import (
    BOT_COLOUR,
    ClaimNamed,
    Feed,
    Naming,
    Whereabouts,
    audience_feed,
    audience_names,
    checked,
    load_feed,
    model_name,
    save_feed,
)
from castle_scenes import CAST, CASTLE, MODELS

SEED = 7


def _standing(world: WorldState) -> dict[str, Whereabouts]:
    out: dict[str, Whereabouts] = {}
    for pid in world.seats:
        player = world.players[pid]
        position = player.position
        if isinstance(position, AtRoom):
            where = Whereabouts(room=position.room_id)
        else:
            where = Whereabouts(
                origin=position.origin, destination=position.destination
            )
        out[pid] = where.model_copy(
            update={"asleep": player.asleep, "alive": player.alive}
        )
    return out


@pytest.fixture(scope="module")
def watched() -> Iterator[tuple[GameRecord, dict[int, dict[str, Whereabouts]]]]:
    """A bot game, and where everyone truly stood at the end of every tick: the
    world as it was handed to the next tick, and as it finished."""
    truth: dict[int, dict[str, Whereabouts]] = {}
    real = play.tick

    def spy(state: WorldState) -> WorldState:
        truth[state.tick] = _standing(state)
        return real(state)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(play, "tick", spy)
        game = play.start_game(CASTLE, CAST, seed=SEED)
        bot = HeuristicBot(seed=SEED)
        played = run_game(
            game,
            {pid: bot for pid in game.world.seats},
            check_leaks=False,
            models=MODELS,
        )
    truth[played.game.world.tick] = _standing(played.game.world)
    yield record(CASTLE, CAST, played), truth


@pytest.fixture(scope="module")
def stored(watched: tuple[GameRecord, dict[int, dict[str, Whereabouts]]]) -> GameRecord:
    return watched[0]


@pytest.fixture(scope="module")
def found(stored: GameRecord) -> list[Verdict]:
    return checked(stored)


@pytest.fixture(scope="module")
def feed(stored: GameRecord, found: list[Verdict]) -> Feed:
    return audience_feed(stored, found)


def test_the_feed_is_a_pure_function_of_the_record_and_the_verdicts(
    stored: GameRecord, found: list[Verdict], feed: Feed
) -> None:
    copied = GameRecord.model_validate_json(stored.model_dump_json())
    verdicts = [Verdict.model_validate_json(v.model_dump_json()) for v in found]
    again = audience_feed(copied, verdicts)
    assert again.model_dump_json() == feed.model_dump_json()
    assert checked(copied) == found


def test_every_claim_made_out_loud_carries_its_verdict_and_its_proof(
    stored: GameRecord, found: list[Verdict], feed: Feed
) -> None:
    public = [
        (claim_id, v)
        for claim_id, v in numbered(found)
        if stored.ledger[v.claim.seq - 1].type in PUBLIC
    ]
    assert public and len(public) < len(found)
    assert [c.id for c in feed.claims] == [claim_id for claim_id, _ in public]
    assert len({c.id for c in feed.claims}) == len(feed.claims)
    events = len(feed.events)
    for claim, (_, verdict) in zip(feed.claims, public, strict=True):
        event = feed.events[claim.seq - 1]
        assert (claim.tick, claim.day, claim.speaker) == (
            event.tick,
            event.day,
            event.actor,
        )
        assert claim.said == verdict.claim.text
        assert claim.label == verdict.label and claim.stamp == stamp(verdict)
        assert claim.reason == verdict.reason and claim.why
        assert claim.proof == verdict.evidence and claim.knew == verdict.against
        assert claim.path == verdict.path
        assert claim.origin == (verdict.path[0] if verdict.path else None)
        assert all(1 <= seq <= events for seq in [*claim.proof, *claim.knew])
        if claim.label == "implanted":
            assert claim.planted_by is not None
            assert claim.took == {True: "gave_in", False: "lost_memory"}.get(
                verdict.gave_in  # type: ignore[arg-type]
            )
        else:
            assert claim.took is None
    assert {c.stamp for c in feed.claims} <= {*STAMPS.values(), "GASLIT"}
    assert feed.game.fact_checked
    assert all(m.checked for m in feed.moments)


def test_a_feed_without_verdicts_has_no_claims_and_says_so(stored: GameRecord) -> None:
    plain = audience_feed(stored)
    assert not plain.game.fact_checked and plain.claims == []
    assert all(not m.checked and m.verdict is None for m in plain.moments)


def test_the_feed_round_trips_through_its_file(feed: Feed, tmp_path: Path) -> None:
    path = tmp_path / "feed.json"
    save_feed(feed, path)
    assert load_feed(path) == feed


def test_every_ledger_entry_is_one_event_with_its_private_lines_and_rolls(
    stored: GameRecord, feed: Feed
) -> None:
    assert [event.seq for event in feed.events] == [e.seq for e in stored.ledger]
    rooms = {room.id: room.lighting for room in CASTLE.rooms}
    for event, entry in zip(feed.events, stored.ledger, strict=True):
        assert (event.type, event.text, event.quote, event.actor, event.room) == (
            entry.type,
            entry.text,
            entry.quote,
            entry.actor_id,
            entry.room_id,
        )
        assert event.light == (rooms[entry.room_id] if entry.room_id else None)
        assert [w.player for w in event.witnesses] == [
            w.player_id for w in entry.witnesses
        ]
        assert [w.confidence for w in event.witnesses] == [
            w.confidence for w in entry.witnesses
        ]
        assert [(r.value, r.chance) for r in event.rolls] == [
            (r.value, r.threshold) for r in entry.rolls
        ]
        if "diary" in entry.private:
            assert event.private["diary"] == entry.private["diary"]
        if "intent" in entry.private:
            assert event.intent is not None
            assert event.intent.render() == entry.private["intent"]
            assert "intent" not in event.private
    interviews = [event for event in feed.events if event.type == "exit_interview"]
    assert interviews and all(event.told is not None for event in interviews)
    assert any(roll.chance is not None for event in feed.events for roll in event.rolls)


def test_positions_match_the_world_at_the_end_of_every_tick(
    watched: tuple[GameRecord, dict[int, dict[str, Whereabouts]]], feed: Feed
) -> None:
    _, truth = watched
    assert [frame.tick for frame in feed.positions] == sorted(truth)
    for frame in feed.positions:
        assert frame.seats == truth[frame.tick], frame.tick
    states = [where for frame in feed.positions for where in frame.seats.values()]
    assert any(where.room is None for where in states), "nobody was ever walking"
    assert any(where.asleep for where in states)
    assert any(not where.alive for where in states)


def test_seats_carry_their_provenance_and_a_bot_seat_names_no_model(
    stored: GameRecord, feed: Feed
) -> None:
    assert feed.game.reference_bots
    assert [seat.id for seat in feed.seats] == [m.id for m in CAST.players]
    for seat in feed.seats:
        assert seat.agent == "bot:heuristic" and seat.played_by == "bot"
        assert seat.model is None
        assert seat.decisions == sum(
            1 for d in stored.decisions if d.player_id == seat.id
        )
    assert "anthropic/" not in feed.model_dump_json()
    traitors = {seat.id for seat in feed.seats if seat.traitor_from is not None}
    assert set(feed.game.starting_traitors) <= traitors


def test_every_dinner_seating_is_kept(stored: GameRecord, feed: Feed) -> None:
    dinners = [entry for entry in stored.ledger if entry.type == "seating"]
    assert [seating.seq for seating in feed.seatings] == [e.seq for e in dinners]
    for seating, entry in zip(feed.seatings, dinners, strict=True):
        assert ",".join(seating.order) == entry.payload["seating"]


def test_a_record_whose_ledger_was_altered_is_refused(stored: GameRecord) -> None:
    ledger = list(stored.ledger)
    ledger[-1] = ledger[-1].model_copy(update={"text": "Something else happened."})
    with pytest.raises(ReplayDiverged):
        audience_feed(stored.model_copy(update={"ledger": ledger}))


def test_castle_game_writes_the_feed(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    path = tmp_path / "feed.json"
    cli_main(["--seed", "4", "--players", "8", "--feed", str(path)])
    assert f"Saved the audience feed to {path}." in capsys.readouterr().out
    written = load_feed(path)
    assert written.game.seed == 4 and len(written.seats) == 8
    assert written.game.fact_checked and written.claims


# ---------------------------------------------------------------------- naming


@pytest.mark.parametrize(
    ("model", "name"),
    [
        ("anthropic/claude-opus-5.5", "Claude Opus 5.5"),
        ("openai/gpt-6.1-sol", "GPT-6.1 Sol"),
        ("google/gemini-3.1-pro-preview", "Gemini 3.1 Pro Preview"),
        ("x-ai/grok-4.7", "Grok 4.7"),
        ("deepseek/deepseek-v4-pro-0813", "DeepSeek V4 Pro 0813"),
        ("moonshotai/kimi-k3", "Kimi K3"),
        ("qwen/qwen3.8-27b", "Qwen3.8 27B"),
        ("z-ai/glm-5.3", "GLM 5.3"),
        ("mistralai/mistral-medium-3-5", "Mistral Medium 3.5"),
    ],
)
def test_a_model_id_reads_as_its_name(model: str, name: str) -> None:
    assert model_name(model) == name


def test_seats_on_one_model_are_numbered_and_a_bot_seat_is_a_reference_bot() -> None:
    agents = {
        "mara": "model:anthropic/claude-opus-5.5",
        "sol": "bot:heuristic",
        "iris": "model:anthropic/claude-opus-5.5",
        "knox": "model:x-ai/grok-4.7",
        "june": "unknown",
    }
    assert audience_names(agents, list(agents)) == {
        "mara": "Claude Opus 5.5 #1",
        "sol": "Reference bot",
        "iris": "Claude Opus 5.5 #2",
        "knox": "Grok 4.7",
        "june": "Unlabelled seat",
    }
    bots = {pid: "bot:heuristic" for pid in ("mara", "sol")}
    assert audience_names(bots, list(bots)) == {
        "mara": "Reference bot #1",
        "sol": "Reference bot #2",
    }


def test_narration_names_the_model_and_a_quote_brackets_it() -> None:
    characters = {"knox": "Knox", "ash": "Ash", "hale": "Hale"}
    agents = {
        "knox": "model:x-ai/grok-4.7",
        "ash": "model:anthropic/claude-opus-5.5",
        "hale": "bot:heuristic",
    }
    naming = Naming.of(characters, agents, "model")
    assert naming.narration("Knox poisoned Ash's glass.") == (
        "Grok 4.7 poisoned Claude Opus 5.5's glass."
    )
    assert naming.words("I think Hale is lying, not Ash.") == (
        "I think [Reference bot] is lying, not [Claude Opus 5.5]."
    )
    assert naming.narration("Knox wrote: “Meet me in the crypt. — Ash”") == (
        "Grok 4.7 wrote: “Meet me in the crypt. — [Claude Opus 5.5]”"
    )
    # Only whole names: ash in the grate, a hallway and Ashford stay as they are.
    assert naming.words("Ashford found ash in the Hallway.") == (
        "Ashford found ash in the Hallway."
    )
    plain = Naming.of(characters, agents)
    assert plain.narration("Knox poisoned Ash's glass.") == "Knox poisoned Ash's glass."
    assert plain.words("I think Hale is lying.") == "I think Hale is lying."


def _unbracketed(feed: Feed, words: str) -> str:
    for seat in feed.seats:
        words = words.replace(f"[{seat.audience}]", seat.character)
    return words


def test_the_feed_carries_both_names_and_quotes_still_match_the_ledger(
    feed: Feed,
) -> None:
    labels = [seat.audience for seat in feed.seats]
    assert labels == [f"Reference bot #{n}" for n in range(1, len(labels) + 1)]
    assert {seat.colour for seat in feed.seats} == {BOT_COLOUR}
    characters = re.compile(
        r"(?<!\w)(?:" + "|".join(seat.character for seat in feed.seats) + r")(?!\w)"
    )
    bracketed = 0
    for event in feed.events:
        named = event.audience
        if named is None:
            assert characters.search(event.text) is None
            continue
        assert characters.search(named.text or event.text) is None, event.seq
        if named.quote is not None:
            assert event.quote is not None
            assert characters.search(named.quote) is None
            assert _unbracketed(feed, named.quote) == event.quote
            bracketed += 1
        for key, line in named.private.items():
            assert _unbracketed(feed, line) == event.private[key]
    assert bracketed > 0
    for claim in feed.claims:
        renamed = claim.audience or ClaimNamed()
        assert characters.search(renamed.what or claim.what) is None, claim.id
        assert characters.search(renamed.why or claim.why) is None, claim.id
        if renamed.said is not None:
            assert _unbracketed(feed, renamed.said) == claim.said


@dataclass(frozen=True)
class _Labelled:
    """A bot that says it is a model, as a model-backed player would."""

    agent: str

    def __call__(self, observation: GameObservation) -> Decision:
        return HeuristicBot(seed=5)(observation)


def test_a_model_seat_is_named_by_its_model() -> None:
    game = play.start_game(CASTLE, CAST, seed=5)
    bot = HeuristicBot(seed=5)
    players: dict[str, Player] = {pid: bot for pid in game.world.seats}
    players["mara"] = _Labelled(f"model:{CAST.models()['mara']}")
    played = run_game(game, players, check_leaks=False, models=MODELS)
    feed = audience_feed(record(CASTLE, CAST, played))
    mara, *rest = feed.seats
    assert (mara.audience, mara.played_by, mara.model) == (
        "Claude Opus 5.5",
        "model",
        "anthropic/claude-opus-5.5",
    )
    assert mara.colour != BOT_COLOUR
    assert [seat.audience for seat in rest] == [
        f"Reference bot #{n}" for n in range(1, len(rest) + 1)
    ]
    assert not feed.game.reference_bots


def test_the_episode_names_seats_by_model_only_when_asked(stored: GameRecord) -> None:
    game = replay(stored)
    plain = episode(game, agents=stored.agents)
    named = episode(game, agents=stored.agents, names="model")
    assert "Reference bot #" not in plain and "accused Ash" in plain
    assert "[Reference bot #" in named and "accused Ash" not in named
    assert named.startswith("REFERENCE BOTS")
    for model in CAST.models().values():
        assert model not in named and model_name(model) not in named


def test_castle_game_names_seats_by_model_behind_a_flag(
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli_main(["--seed", "4", "--players", "8", "--model-names"])
    out = capsys.readouterr().out
    assert "Reference bot #8  as" in out and "[Reference bot #" in out
